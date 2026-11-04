//! Linux task-domain placement and read-only population evidence.
//!
//! A matching, empty cgroup is an observation, not release authorization. The
//! caller must independently prove exclusive task binding, pre-exec placement,
//! no migration out, and no later launch into the group. Missing groups never
//! count as empty, and this module does not discover or terminate processes.

use super::{open_bounded_regular_file, open_bounded_root};
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::Read;
use std::os::fd::AsRawFd;
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::Path;

/// Create a fresh child domain and place the spawned process there before exec.
/// The parent must already be explicitly delegated to the caller. No discovery,
/// fallback, controller changes, cleanup or release authorization is performed.
pub fn prepare_command(
    command: &mut std::process::Command,
    parent: &Path,
) -> Result<serde_json::Value> {
    let parent_identity = inspect_cgroup_v2(parent, None)?.identity;
    let directory = open_bounded_root(parent)?;
    let name = format!("forager-task-{}", uuid::Uuid::new_v4());
    nix::sys::stat::mkdirat(&directory, name.as_str(), nix::sys::stat::Mode::S_IRWXU)?;
    let path = parent.join(name);
    inspect_cgroup_v2(parent, Some(&parent_identity))?;
    let observation = inspect_cgroup_v2(&path, None)?;
    ensure!(
        !observation.populated,
        "new task domain is unexpectedly populated"
    );
    let domain = open_bounded_root(&path)?;
    let fd = nix::fcntl::openat(
        &domain,
        "cgroup.procs",
        nix::fcntl::OFlag::O_WRONLY | nix::fcntl::OFlag::O_CLOEXEC | nix::fcntl::OFlag::O_NOFOLLOW,
        nix::sys::stat::Mode::empty(),
    )?;
    let membership = File::from(fd);
    inspect_cgroup_v2(&path, Some(&observation.identity))?;
    // Only async-signal-safe write and errno handling run after fork. Writing
    // zero moves the calling child, never the dispatching parent. The opened
    // control fd pins the target; deletion causes an error, not path retargeting.
    unsafe {
        command.pre_exec(move || loop {
            let result = nix::libc::write(membership.as_raw_fd(), b"0".as_ptr().cast(), 1);
            if result == 1 {
                return Ok(());
            }
            if result < 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() == std::io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(error);
            }
            return Err(std::io::Error::from_raw_os_error(nix::libc::EIO));
        });
    }
    Ok(serde_json::json!({
        "schema": "linux_cgroup_launch_binding.v1",
        "path": path,
        "identity": observation.identity,
        "placement": "pre_exec_cgroup_procs",
        "release_authorized": false
    }))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CgroupV2Identity {
    boot_id: String,
    namespace_device: u64,
    namespace_inode: u64,
    directory_device: u64,
    directory_inode: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CgroupV2Observation {
    pub identity: CgroupV2Identity,
    pub populated: bool,
}

/// In-memory evidence of this operation, not a restart-safe release receipt.
/// Deliberately not deserializable: absence on a later read cannot recreate it.
#[derive(Debug, Serialize)]
pub struct CgroupV2Seal {
    schema: &'static str,
    path: std::path::PathBuf,
    identity: CgroupV2Identity,
    admissions_closed: bool,
    release_authorized: bool,
}

/// Close only an explicitly bound, empty Forager leaf domain. Caller must hold
/// exclusive task lifecycle ownership; hostile same-user path replacement is
/// outside this contract. No signaling, recursion or missing-path success.
/// This does not prove that no process migrated out before closure.
pub fn seal_empty_cgroup_v2(path: &Path, expected: &CgroupV2Identity) -> Result<CgroupV2Seal> {
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| anyhow::anyhow!("task cgroup name missing"))?;
    let suffix = name
        .strip_prefix("forager-task-")
        .ok_or_else(|| anyhow::anyhow!("refusing non-task cgroup"))?;
    ensure!(
        uuid::Uuid::parse_str(suffix)?.to_string() == suffix,
        "noncanonical task cgroup name"
    );
    let parent_path = path
        .parent()
        .ok_or_else(|| anyhow::anyhow!("task cgroup parent missing"))?;
    let parent = open_bounded_root(parent_path)?;
    let directory = open_bounded_root(path)?;
    require_identity(&identity(&directory)?, expected)?;
    ensure!(
        !inspect_cgroup_v2(path, Some(expected))?.populated,
        "task cgroup is populated"
    );
    let current_parent = open_bounded_root(parent_path)?;
    require_identity(&identity(&parent)?, &identity(&current_parent)?)?;
    // The kernel refuses rmdir if a live process entered after observation or
    // if child cgroups exist. A pinned pre-exec fd cannot target a new domain
    // later created at this path.
    nix::unistd::unlinkat(&parent, name, nix::unistd::UnlinkatFlags::RemoveDir)?;
    // kernfs directory link counts are not ordinary filesystem unlink proof.
    // Require the exact name to be absent under the still-pinned parent; a
    // concurrent recreation is uncertainty, never a successful replay.
    match nix::fcntl::openat(
        &parent,
        name,
        nix::fcntl::OFlag::O_RDONLY
            | nix::fcntl::OFlag::O_DIRECTORY
            | nix::fcntl::OFlag::O_NOFOLLOW
            | nix::fcntl::OFlag::O_CLOEXEC,
        nix::sys::stat::Mode::empty(),
    ) {
        Err(nix::errno::Errno::ENOENT) => {}
        _ => anyhow::bail!("closed cgroup path is not verifiably absent"),
    }
    Ok(CgroupV2Seal {
        schema: "linux_cgroup_admission_seal.v1",
        path: path.to_path_buf(),
        identity: expected.clone(),
        admissions_closed: true,
        release_authorized: false,
    })
}

fn bounded_text(file: File) -> Result<String> {
    let mut bytes = Vec::new();
    file.take(4097).read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 4096, "cgroup evidence exceeds read limit");
    Ok(String::from_utf8(bytes)?)
}

fn parse_populated(text: &str) -> Result<bool> {
    let mut populated = None;
    for line in text.lines() {
        let fields: Vec<_> = line.split_whitespace().collect();
        ensure!(fields.len() == 2, "invalid cgroup event record");
        if fields[0] == "populated" {
            ensure!(populated.is_none(), "duplicate cgroup populated event");
            populated = Some(match fields[1] {
                "0" => false,
                "1" => true,
                _ => anyhow::bail!("invalid cgroup populated value"),
            });
        }
    }
    populated.ok_or_else(|| anyhow::anyhow!("cgroup populated event missing"))
}

fn identity(directory: &File) -> Result<CgroupV2Identity> {
    let boot_id = bounded_text(File::open("/proc/sys/kernel/random/boot_id")?)?;
    let boot_id = uuid::Uuid::parse_str(boot_id.trim())?.to_string();
    let namespace = std::fs::metadata("/proc/self/ns/cgroup")?;
    let directory = directory.metadata()?;
    Ok(CgroupV2Identity {
        boot_id,
        namespace_device: namespace.dev(),
        namespace_inode: namespace.ino(),
        directory_device: directory.dev(),
        directory_inode: directory.ino(),
    })
}

fn require_identity(actual: &CgroupV2Identity, expected: &CgroupV2Identity) -> Result<()> {
    ensure!(
        actual == expected,
        "cgroup identity changed or belongs to another boot/namespace"
    );
    Ok(())
}

/// Capture or recheck an explicit cgroup-v2 directory without changing it.
/// Persist the identity only as part of an independently bound launch record.
/// `None` captures current identity; it must not backfill a lost task's binding.
pub fn inspect_cgroup_v2(
    path: &Path,
    expected: Option<&CgroupV2Identity>,
) -> Result<CgroupV2Observation> {
    let directory = open_bounded_root(path)?;
    ensure!(
        nix::sys::statfs::fstatfs(&directory)?.filesystem_type()
            == nix::sys::statfs::CGROUP2_SUPER_MAGIC,
        "containment directory is not cgroup v2"
    );
    let before = identity(&directory)?;
    if let Some(expected) = expected {
        require_identity(&before, expected)?;
    }
    let events = open_bounded_regular_file(&directory, Path::new("cgroup.events"))?;
    let populated = parse_populated(&bounded_text(events)?)?;
    require_identity(&identity(&directory)?, &before)?;
    // Reopen the path as well: an unlinked open descriptor is not proof that
    // the persisted path still names the same domain.
    require_identity(&identity(&open_bounded_root(path)?)?, &before)?;
    Ok(CgroupV2Observation {
        identity: before,
        populated,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn events_require_exact_unique_populated_value() -> Result<()> {
        assert!(!parse_populated("populated 0\nfrozen 0\nfuture_field 3\n")?);
        assert!(parse_populated("populated 1\nfrozen 0\n")?);
        for invalid in [
            "",
            "frozen 0\n",
            "populated 2",
            "populated -1",
            "populated 00",
            "populated 0\npopulated 0",
            "populated 0 extra",
            "populated 0\nbroken",
        ] {
            assert!(parse_populated(invalid).is_err(), "{invalid:?}");
        }
        Ok(())
    }

    #[test]
    fn persisted_identity_rejects_every_changed_component() -> Result<()> {
        let root = tempfile::tempdir()?;
        let original = identity(&File::open(root.path())?)?;
        let restored: CgroupV2Identity = serde_json::from_slice(&serde_json::to_vec(&original)?)?;
        require_identity(&original, &restored)?;
        for field in [
            "boot_id",
            "namespace_device",
            "namespace_inode",
            "directory_device",
            "directory_inode",
        ] {
            let mut value = serde_json::to_value(&original)?;
            value[field] = if field == "boot_id" {
                serde_json::json!("different-boot")
            } else {
                serde_json::json!(value[field].as_u64().unwrap().wrapping_add(1))
            };
            let changed = serde_json::from_value(value)?;
            assert!(require_identity(&original, &changed).is_err());
        }
        Ok(())
    }

    #[test]
    fn missing_fake_and_symlinked_groups_never_count_as_empty() -> Result<()> {
        let root = tempfile::tempdir()?;
        std::fs::write(root.path().join("cgroup.events"), "populated 0\n")?;
        assert!(inspect_cgroup_v2(root.path(), None).is_err());
        assert!(inspect_cgroup_v2(&root.path().join("missing"), None).is_err());
        std::os::unix::fs::symlink(root.path(), root.path().join("alias"))?;
        assert!(inspect_cgroup_v2(&root.path().join("alias"), None).is_err());
        assert!(inspect_cgroup_v2(Path::new("relative"), None).is_err());
        Ok(())
    }

    #[test]
    fn oversized_evidence_is_rejected() -> Result<()> {
        let mut file = tempfile::tempfile()?;
        use std::io::{Seek, Write};
        file.write_all(&vec![b' '; 4097])?;
        file.rewind()?;
        assert!(bounded_text(file).is_err());
        Ok(())
    }

    #[test]
    fn seal_rejects_shared_fake_missing_and_mismatched_targets() -> Result<()> {
        let root = tempfile::tempdir()?;
        let shared = root.path().join("shared");
        std::fs::create_dir(&shared)?;
        let shared_identity = identity(&File::open(&shared)?)?;
        assert!(seal_empty_cgroup_v2(&shared, &shared_identity).is_err());
        assert!(shared.is_dir());
        let fake = root
            .path()
            .join(format!("forager-task-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&fake)?;
        std::fs::write(fake.join("cgroup.events"), "populated 0\n")?;
        let actual = identity(&File::open(&fake)?)?;
        assert!(seal_empty_cgroup_v2(&fake, &actual).is_err());
        assert!(seal_empty_cgroup_v2(&fake, &shared_identity).is_err());
        assert_eq!(
            std::fs::read_to_string(fake.join("cgroup.events"))?,
            "populated 0\n"
        );
        let absent = root
            .path()
            .join(format!("forager-task-{}", uuid::Uuid::new_v4()));
        assert!(seal_empty_cgroup_v2(&absent, &actual).is_err());
        Ok(())
    }

    #[test]
    #[ignore = "requires an explicit existing populated cgroup path; read-only"]
    fn live_cgroup_observation_round_trips_without_writes() -> Result<()> {
        let path = std::env::var("FORAGER_TEST_CGROUP_PATH")?;
        let path = Path::new(&path);
        let first = inspect_cgroup_v2(path, None)?;
        assert!(first.populated);
        let persisted = serde_json::to_vec(&first.identity)?;
        let restored = serde_json::from_slice(&persisted)?;
        let second = inspect_cgroup_v2(path, Some(&restored))?;
        assert_eq!(first, second);
        let mut wrong = restored;
        wrong.directory_inode = wrong.directory_inode.wrapping_add(1);
        assert!(inspect_cgroup_v2(path, Some(&wrong)).is_err());
        Ok(())
    }

    #[test]
    #[ignore = "run only inside a disposable delegated service; moves only its owned child"]
    fn migrated_process_outlives_empty_domain_seal() -> Result<()> {
        use std::time::{Duration, Instant};
        let parent = std::path::PathBuf::from(std::env::var("FORAGER_TEST_DELEGATED_PARENT")?);
        let root = tempfile::tempdir()?;
        let mut command = std::process::Command::new("/bin/sh");
        command.current_dir(root.path()).env("FORAGER_TEST_ESCAPE", parent.join("cgroup.procs"))
            .args(["-c", "printf 0 > \"$FORAGER_TEST_ESCAPE\" || exit 72; cat /proc/self/cgroup > membership.tmp && mv membership.tmp membership; n=0; while [ ! -f release ] && [ \"$n\" -lt 500 ]; do n=$((n+1)); sleep 0.01; done"]);
        let binding = prepare_command(&mut command, &parent)?;
        let path = std::path::PathBuf::from(binding["path"].as_str().unwrap());
        let expected = serde_json::from_value(binding["identity"].clone())?;
        let mut child = command.spawn()?;
        let deadline = Instant::now() + Duration::from_secs(4);
        while !root.path().join("membership").exists() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        let membership = std::fs::read_to_string(root.path().join("membership"))?;
        let expected_parent = parent.strip_prefix("/sys/fs/cgroup")?.to_str().unwrap();
        assert_eq!(membership.trim(), format!("0::/{expected_parent}"));
        assert!(child.try_wait()?.is_none());
        assert!(!inspect_cgroup_v2(&path, Some(&expected))?.populated);
        let seal = serde_json::to_value(seal_empty_cgroup_v2(&path, &expected)?)?;
        assert_eq!(seal["admissions_closed"], true);
        assert_eq!(seal["release_authorized"], false);
        // The original domain is closed, but this task process is still alive.
        assert!(child.try_wait()?.is_none());
        std::fs::write(root.path().join("release"), b"release owned test process")?;
        assert!(child.wait()?.success());
        Ok(())
    }
}
