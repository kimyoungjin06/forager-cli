//! Opt-in, manifest-bound filesystem read restrictions for local work.
use std::{
    path::Path,
    process::{Command, Stdio},
};

use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::{background::BackgroundRunnerKind, shift_runtime::ProjectRootBoundary};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadPolicy {
    pub schema: String,
    pub provider: String,
    pub minimum_landlock_abi: u32,
    pub runtime_readonly_paths: Vec<String>,
    pub device_readwrite_paths: Vec<String>,
    pub device_readonly_paths: Vec<String>,
    pub hardlink_aliases_covered: bool,
    pub nested_mounts_covered: bool,
    pub preopened_descriptors_covered: bool,
    pub workdir_readwrite: bool,
    pub network_isolated: bool,
    pub metadata_access_covered: bool,
    pub ipc_covered: bool,
    pub inherited_stdio_restricted: bool,
}

impl ReadPolicy {
    pub fn canonical() -> Self {
        Self {
            schema: "offdesk_read_policy.v1".into(),
            provider: "linux_landlock_workdir_and_runtime_only".into(),
            minimum_landlock_abi: crate::process::READ_POLICY_MIN_ABI,
            runtime_readonly_paths: crate::process::READ_POLICY_RUNTIME_PATHS
                .iter()
                .map(|p| p.to_string())
                .collect(),
            device_readwrite_paths: vec!["/dev/null".into()],
            device_readonly_paths: vec!["/dev/urandom".into(), "/dev/random".into()],
            hardlink_aliases_covered: false,
            nested_mounts_covered: false,
            preopened_descriptors_covered: false,
            workdir_readwrite: true,
            network_isolated: false,
            metadata_access_covered: false,
            ipc_covered: false,
            inherited_stdio_restricted: false,
        }
    }

    pub fn metadata() -> Self {
        let mut policy = Self::canonical();
        policy.schema = "offdesk_metadata_read_policy.v1".into();
        policy.provider = "linux_landlock_metadata_boot_identity".into();
        policy
            .runtime_readonly_paths
            .push("/proc/sys/kernel/random/boot_id".into());
        policy
    }

    pub fn validate(&self) -> Result<()> {
        ensure!(
            self == &Self::canonical() || self == &Self::metadata(),
            "read policy is not canonical"
        );
        Ok(())
    }

    pub fn sha256(&self) -> Result<String> {
        self.validate()?;
        Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(self)?)))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadPolicyLaunchEvidence {
    pub schema: String,
    pub policy_sha256: String,
    pub working_directory_identity_sha256: String,
    pub landlock_abi: u32,
    pub restrictions_applied_before_exec: bool,
}

impl ReadPolicyLaunchEvidence {
    pub fn validate_against(&self, policy: &ReadPolicy, root_identity: &str) -> Result<()> {
        ensure!(
            self.schema == "offdesk_read_policy_launch_evidence.v1"
                && self.policy_sha256 == policy.sha256()?
                && self.working_directory_identity_sha256 == root_identity
                && self.landlock_abi >= policy.minimum_landlock_abi
                && self.restrictions_applied_before_exec,
            "read policy launch evidence does not match the manifest and working directory"
        );
        Ok(())
    }
}

pub(crate) fn validate_launch_evidence(
    policy: Option<&ReadPolicy>,
    evidence: Option<&ReadPolicyLaunchEvidence>,
    root_identity: &str,
) -> Result<()> {
    match (policy, evidence) {
        (Some(policy), Some(evidence)) => evidence.validate_against(policy, root_identity),
        (Some(_), None) => {
            anyhow::bail!("restricted shift task probe omitted read policy launch evidence")
        }
        (None, Some(_)) => {
            anyhow::bail!("unrestricted shift task probe claimed read policy launch evidence")
        }
        (None, None) => Ok(()),
    }
}

pub fn apply_to_command(
    policy: &ReadPolicy,
    command: &mut Command,
    workdir: &Path,
) -> Result<ReadPolicyLaunchEvidence> {
    apply_to_command_bound(policy, command, workdir, None)
}

pub(crate) fn apply_to_command_bound(
    policy: &ReadPolicy,
    command: &mut Command,
    workdir: &Path,
    expected_root_identity: Option<&str>,
) -> Result<ReadPolicyLaunchEvidence> {
    policy.validate()?;
    let root = ProjectRootBoundary::open(workdir)?;
    if let Some(expected) = expected_root_identity {
        ensure!(
            root.identity_sha256 == expected,
            "read policy working directory differs from the approved root"
        );
    }
    let abi = crate::process::restrict_command_filesystem(
        command,
        workdir,
        root.descriptor()?,
        policy == &ReadPolicy::metadata(),
    )
    .context("prepare mandatory Landlock restriction; no unrestricted fallback")?;
    root.ensure_current()?;
    Ok(ReadPolicyLaunchEvidence {
        schema: "offdesk_read_policy_launch_evidence.v1".into(),
        policy_sha256: policy.sha256()?,
        working_directory_identity_sha256: root.identity_sha256,
        landlock_abi: abi,
        // This prepared evidence is stored only after Command::spawn confirms
        // the child completed pre_exec and successfully executed its supervisor.
        restrictions_applied_before_exec: true,
    })
}

pub fn validate_workdir(policy: &ReadPolicy, workdir: &Path) -> Result<()> {
    let mut command = Command::new("/bin/true");
    apply_to_command(policy, &mut command, workdir)?;
    Ok(())
}

pub fn verify(policy: &ReadPolicy) -> Result<u32> {
    policy.validate()?;
    let fixture = tempfile::tempdir()?;
    let root = fixture.path().join("task");
    std::fs::create_dir(&root)?;
    std::fs::write(root.join("allowed.txt"), b"allowed")?;
    let outside = fixture.path().join("outside.txt");
    std::fs::write(&outside, b"outside")?;
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, root.join("escape"))?;
    let mut command = Command::new("/usr/bin/python3");
    command.args(["-I", "-S", "-c", r#"
import errno, os, pathlib, subprocess, sys
assert pathlib.Path('allowed.txt').read_bytes() == b'allowed'
assert pathlib.Path('/bin/sh').read_bytes()
for name in [sys.argv[1], 'escape', '/etc/passwd', '/proc/%s/root%s' % (os.getppid(), sys.argv[1])]:
    try:
        open(name, 'rb').read(1)
    except OSError as exc:
        assert exc.errno == errno.EACCES, (name, exc.errno)
    else:
        raise AssertionError('outside read allowed')
try:
    open(sys.argv[1], 'wb')
except OSError as exc:
    assert exc.errno == errno.EACCES
else:
    raise AssertionError('outside write allowed')
try:
    os.listdir(pathlib.Path(sys.argv[1]).parent)
except OSError as exc:
    assert exc.errno == errno.EACCES
else:
    raise AssertionError('outside directory enumeration allowed')
pathlib.Path('written.txt').write_bytes(b'written')
assert subprocess.run(['/bin/sh', '-c', 'cat "$1"', 'probe', sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
try:
    boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
except PermissionError as exc:
    assert exc.errno == errno.EACCES and sys.argv[2] == 'no'
else:
    assert sys.argv[2] == 'yes' and len(boot) == 36
for path in ['/proc/sys/kernel/random/uuid', '/proc/sys/kernel/random']:
    try:
        if pathlib.Path(path).is_dir():
            os.listdir(path)
        else:
            pathlib.Path(path).read_bytes()
    except PermissionError as exc:
        assert exc.errno == errno.EACCES
    else:
        raise AssertionError('unselected kernel read allowed')
print('FORAGER_READ_POLICY_PROBE_PASS')
"#]).arg(&outside).arg(if policy == &ReadPolicy::metadata() { "yes" } else { "no" }).stdin(Stdio::null());
    let evidence = apply_to_command(policy, &mut command, &root)?;
    let output = command
        .output()
        .context("execute mandatory Landlock probe")?;
    ensure!(
        output.status.success() && output.stdout == b"FORAGER_READ_POLICY_PROBE_PASS\n",
        "Landlock read/write/descendant probe failed"
    );
    ensure!(
        std::fs::read(outside)? == b"outside",
        "Landlock probe modified its outside canary"
    );
    Ok(evidence.landlock_abi)
}

pub fn report() -> serde_json::Value {
    let policy = ReadPolicy::canonical();
    match verify(&policy) {
        Ok(abi) => {
            serde_json::json!({"schema":"offdesk_read_policy_report.v1", "status":"verified", "landlock_abi":abi, "policy":policy,
            "creates_profile_state":false,"authorizes_dispatch":false})
        }
        Err(error) => {
            serde_json::json!({"schema":"offdesk_read_policy_report.v1", "status":"unavailable", "reason":format!("{error:#}"),
            "creates_profile_state":false,"authorizes_dispatch":false})
        }
    }
}

pub fn validate_runners(
    policy: &ReadPolicy,
    runners: impl Iterator<Item = BackgroundRunnerKind>,
) -> Result<()> {
    policy.validate()?;
    let kinds = runners.collect::<Vec<_>>();
    ensure!(
        !kinds.is_empty()
            && kinds.iter().all(|kind| matches!(
                kind,
                BackgroundRunnerKind::LocalBackground | BackgroundRunnerKind::LocalTmux
            )),
        "read policy requires every bound task to use local-background or local-tmux"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noncanonical_policy_and_unsupported_runners_fail_closed() -> Result<()> {
        let policy = ReadPolicy::canonical();
        validate_runners(&policy, [BackgroundRunnerKind::LocalBackground].into_iter())?;
        validate_runners(&policy, [BackgroundRunnerKind::LocalTmux].into_iter())?;
        for runner in [
            BackgroundRunnerKind::GithubRunner,
            BackgroundRunnerKind::RemoteWorker,
        ] {
            assert!(validate_runners(&policy, [runner].into_iter()).is_err());
        }
        assert!(validate_runners(&policy, std::iter::empty()).is_err());
        let mut changed = policy.clone();
        changed.runtime_readonly_paths.push("/".into());
        assert!(changed.validate().is_err());
        let root = tempfile::tempdir()?;
        let mut command = Command::new("/bin/sh");
        assert!(
            apply_to_command_bound(&policy, &mut command, root.path(), Some(&"b".repeat(64)))
                .is_err()
        );
        assert!(apply_to_command(&changed, &mut command, root.path()).is_err());
        Ok(())
    }

    #[test]
    fn launch_evidence_rejects_changed_policy_root_abi_or_missing_application() -> Result<()> {
        let policy = ReadPolicy::canonical();
        let evidence = ReadPolicyLaunchEvidence {
            schema: "offdesk_read_policy_launch_evidence.v1".into(),
            policy_sha256: policy.sha256()?,
            working_directory_identity_sha256: "a".repeat(64),
            landlock_abi: 5,
            restrictions_applied_before_exec: true,
        };
        evidence.validate_against(&policy, &"a".repeat(64))?;
        validate_launch_evidence(Some(&policy), Some(&evidence), &"a".repeat(64))?;
        validate_launch_evidence(None, None, "unused")?;
        assert!(validate_launch_evidence(Some(&policy), None, &"a".repeat(64)).is_err());
        assert!(validate_launch_evidence(None, Some(&evidence), &"a".repeat(64)).is_err());
        assert!(evidence.validate_against(&policy, &"b".repeat(64)).is_err());
        for variant in 0..4 {
            let mut changed = evidence.clone();
            match variant {
                0 => changed.policy_sha256 = "b".repeat(64),
                1 => changed.landlock_abi = 4,
                2 => changed.restrictions_applied_before_exec = false,
                _ => changed.schema = "unknown".into(),
            }
            assert!(changed.validate_against(&policy, &"a".repeat(64)).is_err());
        }
        Ok(())
    }

    #[test]
    fn kernel_probe_reports_availability_without_granting_authority() -> Result<()> {
        let value = report();
        assert_eq!(value["creates_profile_state"], false);
        assert_eq!(value["authorizes_dispatch"], false);
        if crate::process::landlock_abi().is_ok() && Path::new("/usr/bin/python3").exists() {
            assert_eq!(value["status"], "verified", "{value}");
        } else {
            assert_eq!(value["status"], "unavailable");
        }
        Ok(())
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn replaced_root_is_refused_before_user_code_executes() -> Result<()> {
        if crate::process::landlock_abi().is_err() {
            return Ok(());
        }
        let fixture = tempfile::tempdir()?;
        let root = fixture.path().join("task");
        std::fs::create_dir(&root)?;
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "printf bypass > marker"]);
        apply_to_command(&ReadPolicy::canonical(), &mut command, &root)?;
        std::fs::rename(&root, fixture.path().join("original"))?;
        std::fs::create_dir(&root)?;
        let error = command.spawn().expect_err("replaced root must not execute");
        assert_eq!(error.raw_os_error(), Some(nix::libc::ESTALE));
        assert!(!root.join("marker").exists());
        assert!(!fixture.path().join("original/marker").exists());
        Ok(())
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn broad_kernel_roots_and_runtime_overlap_are_rejected() -> Result<()> {
        if crate::process::landlock_abi().is_err() {
            return Ok(());
        }
        for path in ["/", "/proc", "/sys", "/dev", "/usr", "/usr/bin", "/etc"] {
            let mut command = Command::new("/bin/true");
            assert!(
                apply_to_command(&ReadPolicy::canonical(), &mut command, Path::new(path)).is_err(),
                "{path}"
            );
        }
        Ok(())
    }
}
