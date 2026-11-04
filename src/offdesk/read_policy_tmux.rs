//! Per-pane restriction, applied after the shared tmux server allocates stdio.
//! Receipts are local evidence, not protection against same-user tampering or IPC.
use super::{
    read_policy::{self, ReadPolicy, ReadPolicyLaunchEvidence},
    shift_runtime::ProjectRootBoundary,
};
use crate::{process, tmux::Session};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::Duration,
};

pub(crate) const INTERNAL_ARGUMENT: &str = "--internal-read-policy-launch";
const LIMIT: usize = 131_072;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LaunchSpec {
    schema: String,
    nonce: String,
    workdir: PathBuf,
    root_identity: String,
    policy: ReadPolicy,
    program: String,
    arguments: Vec<String>,
    log: Option<PathBuf>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TmuxReadPolicyBinding {
    pub schema: String,
    pub receipt_path: PathBuf,
    pub receipt_sha256: String,
    pub receipt: TmuxReadPolicyReceipt,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TmuxReadPolicyReceipt {
    pub schema: String,
    pub nonce: String,
    pub launch_spec_sha256: String,
    pub workdir: PathBuf,
    pub helper_pid: u32,
    pub helper_identity: String,
    pub child_pid: u32,
    pub child_identity: String,
    pub evidence: ReadPolicyLaunchEvidence,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(crate) fn task_session(ticket: &str, root_identity: &str) -> Result<(Session, String)> {
    // Shift tickets share a textual prefix and profiles can reuse ticket IDs.
    // Include the full ticket and approved root before applying name limits.
    let key = digest(format!("{ticket}\0{root_identity}").as_bytes());
    let title = format!("offdesk_{}", &key[8..20]);
    Ok((
        Session::new(&key, &title)?,
        Session::generate_name(&key, &title),
    ))
}

fn read(path: &Path) -> Result<Vec<u8>> {
    ensure!(path.is_absolute(), "absolute launcher artifact required");
    let root = process::open_bounded_root(path.parent().context("artifact parent missing")?)?;
    let file = process::open_bounded_regular_file(
        &root,
        Path::new(path.file_name().context("artifact name missing")?),
    )?;
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "launcher artifact must have one link"
    );
    let mut bytes = Vec::new();
    file.take((LIMIT + 1) as u64).read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= LIMIT, "launcher artifact too large");
    Ok(bytes)
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let root = process::open_bounded_root(path.parent().context("artifact parent missing")?)?;
    let mut file = process::create_new_bounded_regular_file(
        &root,
        Path::new(path.file_name().context("artifact name missing")?),
    )?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    }
    file.write_all(bytes)?;
    file.sync_all()?;
    root.sync_all()?;
    Ok(())
}

pub(crate) fn run_helper(path: &Path, expected: &str) -> Result<u8> {
    let bytes = read(path)?;
    ensure!(digest(&bytes) == expected, "restricted launch spec changed");
    let spec: LaunchSpec = serde_json::from_slice(&bytes)?;
    ensure!(
        spec.schema == "offdesk_tmux_read_policy_spec.v1",
        "invalid restricted launch spec"
    );
    ensure!(
        path.starts_with(&spec.workdir),
        "launch spec must be in the approved workdir"
    );
    let mut command = Command::new(&spec.program);
    command.args(&spec.arguments).stdin(Stdio::inherit());
    if let Some(log) = &spec.log {
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(log)?;
        command.stdout(file.try_clone()?).stderr(file);
    } else {
        command.stdout(Stdio::inherit()).stderr(Stdio::inherit());
    }
    let evidence = read_policy::apply_to_command_bound(
        &spec.policy,
        &mut command,
        &spec.workdir,
        Some(&spec.root_identity),
    )?;
    command.env("TERM", "xterm-256color");
    let helper_pid = std::process::id();
    let helper_identity =
        process::process_identity(helper_pid).context("launcher identity unavailable")?;
    let mut child = command
        .spawn()
        .context("mandatory restricted pane exec failed; no fallback")?;
    let saved = (|| -> Result<()> {
        let receipt = TmuxReadPolicyReceipt {
            schema: "offdesk_tmux_read_policy_receipt.v1".into(),
            nonce: spec.nonce.clone(),
            launch_spec_sha256: expected.into(),
            workdir: spec.workdir,
            helper_pid,
            helper_identity,
            child_pid: child.id(),
            child_identity: process::process_identity(child.id())
                .context("restricted child identity unavailable")?,
            evidence,
        };
        write_new(
            &path.with_file_name("receipt.json"),
            &serde_json::to_vec(&receipt)?,
        )
    })();
    if let Err(error) = saved {
        process::kill_process_tree(child.id());
        let _ = child.wait();
        return Err(error);
    }
    let mut acknowledged = false;
    for _ in 0..200 {
        if read(&path.with_file_name("acknowledged")).ok().as_deref() == Some(spec.nonce.as_bytes())
        {
            acknowledged = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(25));
    }
    if !acknowledged {
        process::kill_process_tree(child.id());
        let _ = child.wait();
        anyhow::bail!("restricted pane launch acknowledgement lost");
    }
    Ok(child
        .wait()?
        .code()
        .and_then(|c| u8::try_from(c).ok())
        .unwrap_or(1))
}

/// The executable must be the Forager CLI, which implements INTERNAL_ARGUMENT.
pub(crate) fn launch(
    executable: &Path,
    session: &Session,
    workdir: &Path,
    command: &Command,
    policy: &ReadPolicy,
    expected_root: &str,
    log: Option<&Path>,
) -> Result<TmuxReadPolicyBinding> {
    ensure!(
        !session.exists(),
        "restricted launch requires a fresh tmux session"
    );
    policy.validate()?;
    let root = ProjectRootBoundary::open(workdir)?;
    ensure!(
        root.identity_sha256 == expected_root,
        "restricted pane root differs from approval"
    );
    read_policy::verify(policy)?;
    read_policy::validate_workdir(policy, workdir)?;
    let directory = tempfile::Builder::new()
        .prefix(".forager-read-policy-")
        .tempdir_in(workdir)?;
    let path = directory.path().join("spec.json");
    let spec = LaunchSpec {
        schema: "offdesk_tmux_read_policy_spec.v1".into(),
        nonce: uuid::Uuid::new_v4().to_string(),
        workdir: workdir.into(),
        root_identity: expected_root.into(),
        policy: policy.clone(),
        program: command
            .get_program()
            .to_str()
            .context("non-UTF8 restricted program")?
            .into(),
        arguments: command
            .get_args()
            .map(|a| {
                a.to_str()
                    .context("non-UTF8 restricted argument")
                    .map(String::from)
            })
            .collect::<Result<_>>()?,
        log: log.map(PathBuf::from),
    };
    let bytes = serde_json::to_vec(&spec)?;
    ensure!(bytes.len() <= LIMIT, "restricted launch spec too large");
    write_new(&path, &bytes)?;
    // Keep launch evidence after the dispatching CLI exits, including failures.
    let _ = directory.keep();
    root.ensure_current()?;
    session.create_direct(
        workdir.to_str().context("non-UTF8 workdir")?,
        &[
            executable
                .to_str()
                .context("non-UTF8 launcher path")?
                .into(),
            INTERNAL_ARGUMENT.into(),
            path.to_str().context("non-UTF8 launch path")?.into(),
            digest(&bytes),
        ],
    )?;
    let pane_pid = session.get_pane_pid();
    let pane_identity = pane_pid.and_then(process::process_identity);
    let result = (|| -> Result<TmuxReadPolicyBinding> {
        let receipt_path = path.with_file_name("receipt.json");
        // tmux success only acknowledges pane creation, not restricted exec.
        for _ in 0..100 {
            if let Ok(receipt_bytes) = read(&receipt_path) {
                if let Ok(receipt) = serde_json::from_slice::<TmuxReadPolicyReceipt>(&receipt_bytes)
                {
                    ensure!(
                        receipt.nonce == spec.nonce && receipt.launch_spec_sha256 == digest(&bytes),
                        "restricted launch receipt mismatch"
                    );
                    ensure!(
                        pane_pid == Some(receipt.helper_pid)
                            && pane_identity.as_ref() == Some(&receipt.helper_identity),
                        "restricted launcher pane identity mismatch"
                    );
                    receipt.evidence.validate_against(policy, expected_root)?;
                    root.ensure_current()?;
                    write_new(&path.with_file_name("acknowledged"), spec.nonce.as_bytes())?;
                    return Ok(TmuxReadPolicyBinding {
                        schema: "offdesk_tmux_read_policy_binding.v1".into(),
                        receipt_path,
                        receipt_sha256: digest(&receipt_bytes),
                        receipt,
                    });
                }
            }
            std::thread::sleep(Duration::from_millis(25));
        }
        anyhow::bail!("restricted pane did not acknowledge successful exec; no fallback")
    })();
    if result.is_err()
        && pane_pid.and_then(process::process_identity) == pane_identity
        && pane_identity.is_some()
    {
        let _ = session.kill();
    }
    result
}

pub fn load_binding(path: &Path) -> Result<TmuxReadPolicyBinding> {
    let bytes = read(path)?;
    let receipt = serde_json::from_slice(&bytes)?;
    Ok(TmuxReadPolicyBinding {
        schema: "offdesk_tmux_read_policy_binding.v1".into(),
        receipt_path: path.into(),
        receipt_sha256: digest(&bytes),
        receipt,
    })
}

pub fn validate_live(
    binding: &TmuxReadPolicyBinding,
    session: &Session,
    workdir: &Path,
) -> Result<()> {
    ensure!(
        binding.schema == "offdesk_tmux_read_policy_binding.v1"
            && binding == &load_binding(&binding.receipt_path)?,
        "restricted pane receipt changed"
    );
    let receipt = &binding.receipt;
    ensure!(
        receipt.schema == "offdesk_tmux_read_policy_receipt.v1" && receipt.workdir == workdir,
        "restricted pane workdir mismatch"
    );
    let root = ProjectRootBoundary::open(workdir)?;
    receipt
        .evidence
        .validate_against(&ReadPolicy::canonical(), &root.identity_sha256)?;
    ensure!(
        session.get_pane_pid() == Some(receipt.helper_pid)
            && process::process_identity(receipt.helper_pid).as_ref()
                == Some(&receipt.helper_identity)
            && process::process_identity(receipt.child_pid).as_ref()
                == Some(&receipt.child_identity),
        "restricted pane process identity changed or exited"
    );
    root.ensure_current()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn protected_task_names_separate_shared_ticket_prefixes_and_roots() -> Result<()> {
        let name = task_session("shift_launch_a", "root_a")?.1;
        assert_ne!(name, task_session("shift_launch_b", "root_a")?.1);
        assert_ne!(name, task_session("shift_launch_a", "root_b")?.1);
        assert_eq!(name, task_session("shift_launch_a", "root_a")?.1);
        Ok(())
    }

    #[test]
    fn launcher_rejects_changed_spec_root_policy_and_exec_without_fallback() -> Result<()> {
        if process::landlock_abi().is_err() {
            return Ok(());
        }
        for variant in 0..4 {
            let fixture = tempfile::tempdir()?;
            let root = ProjectRootBoundary::open(fixture.path())?;
            let mut spec = LaunchSpec {
                schema: "offdesk_tmux_read_policy_spec.v1".into(),
                nonce: uuid::Uuid::new_v4().to_string(),
                workdir: fixture.path().into(),
                root_identity: root.identity_sha256,
                policy: ReadPolicy::canonical(),
                program: "/bin/sh".into(),
                arguments: vec!["-c".into(), "touch must-not-run".into()],
                log: None,
            };
            match variant {
                1 => spec.root_identity = "b".repeat(64),
                2 => spec.policy.network_isolated = true,
                3 => {
                    spec.program = fixture
                        .path()
                        .join("missing-executable")
                        .to_string_lossy()
                        .into_owned()
                }
                _ => {}
            }
            let bytes = serde_json::to_vec(&spec)?;
            let path = fixture.path().join("spec.json");
            write_new(&path, &bytes)?;
            let expected = if variant == 0 {
                "0".repeat(64)
            } else {
                digest(&bytes)
            };
            assert!(run_helper(&path, &expected).is_err());
            assert!(!fixture.path().join("must-not-run").exists());
            assert!(!fixture.path().join("receipt.json").exists());
        }
        Ok(())
    }
}
