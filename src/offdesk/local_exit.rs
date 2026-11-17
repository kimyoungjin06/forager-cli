//! Exit receipts for local commands, with Linux descendant-wait supervision.

use crate::process;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use std::{io::Read, path::Path, process::Command};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalExitReceipt {
    pub relative_path: String,
    pub nonce: String,
    pub status: Option<u8>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wait_scope: Option<String>,
}

pub fn command(root: &Path, source: &str) -> Result<(Command, LocalExitReceipt)> {
    build_command(root, source, "linux_subreaper_children")
}

pub(crate) const CONTAINED_SCOPE: &str = "linux_subreaper_children_cgroup_v1";

pub(crate) fn contained_command(root: &Path, source: &str) -> Result<(Command, LocalExitReceipt)> {
    ensure!(
        cfg!(target_os = "linux"),
        "contained command requires Linux"
    );
    build_command(root, source, CONTAINED_SCOPE)
}

fn build_command(root: &Path, source: &str, scope: &str) -> Result<(Command, LocalExitReceipt)> {
    let directory = tempfile::Builder::new()
        .prefix(".forager-exit-")
        .tempdir_in(root)?;
    let nonce = uuid::Uuid::new_v4().to_string();
    let path = directory.path().join("receipt.json");
    let relative_path = path.strip_prefix(root)?.to_string_lossy().into_owned();
    let (command, waits_for_descendants) =
        process::local_supervisor_command(source, &path, &nonce, scope);
    // Preserve receipt storage after the dispatching CLI exits. No cleanup of
    // evidence is inferred from task completion.
    let _ = directory.keep();
    Ok((
        command,
        LocalExitReceipt {
            relative_path,
            nonce,
            status: None,
            wait_scope: waits_for_descendants.then(|| scope.to_string()),
        },
    ))
}

pub fn refresh(root: &Path, receipt: &mut LocalExitReceipt) -> Result<()> {
    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Record {
        nonce: String,
        status: u8,
        #[serde(default)]
        wait_scope: Option<String>,
    }
    receipt.status = None;
    let directory = process::open_bounded_root(root)?;
    let file = process::open_bounded_regular_file(&directory, Path::new(&receipt.relative_path))?;
    let before = process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "exit receipt must have one link"
    );
    let mut bytes = Vec::new();
    let mut reader = file.take(1025);
    reader.read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 1024, "exit receipt too large");
    let record: Record = serde_json::from_slice(&bytes)?;
    ensure!(
        record.wait_scope == receipt.wait_scope,
        "exit receipt wait scope mismatch"
    );
    ensure!(
        record.nonce == receipt.nonce,
        "exit receipt identity mismatch"
    );
    ensure!(
        before == process::FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?),
        "exit receipt changed during read"
    );
    let current =
        process::open_bounded_regular_file(&directory, Path::new(&receipt.relative_path))?;
    ensure!(
        before == process::FilesystemObjectSnapshot::from_metadata(&current.metadata()?),
        "exit receipt replaced during read"
    );
    let current_root = process::open_bounded_root(root)?;
    ensure!(
        process::filesystem_file_identity(&directory.metadata()?)
            == process::filesystem_file_identity(&current_root.metadata()?),
        "exit receipt root replaced"
    );
    receipt.status = Some(record.status);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn supervisor_death_does_not_create_a_success_receipt() -> Result<()> {
        let root = tempfile::tempdir()?;
        // The disposable receiver kills only its own supervising parent.
        let (mut command, receipt) = command(root.path(), "kill -KILL \"$PPID\"; exit 0")?;
        assert!(!command.status()?.success());
        let mut reloaded: LocalExitReceipt =
            serde_json::from_slice(&serde_json::to_vec(&receipt)?)?;
        assert!(refresh(root.path(), &mut reloaded).is_err());
        assert_eq!(reloaded.status, None);
        Ok(())
    }

    #[test]
    fn damaged_or_missing_receipts_clear_cached_success() -> Result<()> {
        let root = tempfile::tempdir()?;
        let (mut command, mut receipt) = command(root.path(), "exit 0")?;
        assert!(command.status()?.success());
        let path = root.path().join(&receipt.relative_path);
        let valid = std::fs::read(&path)?;
        for bytes in [
            b"{".to_vec(),
            vec![b' '; 1025],
            br#"{"nonce":"other","status":0}"#.to_vec(),
        ] {
            std::fs::write(&path, &valid)?;
            refresh(root.path(), &mut receipt)?;
            assert_eq!(receipt.status, Some(0));
            std::fs::write(&path, bytes)?;
            assert!(refresh(root.path(), &mut receipt).is_err());
            assert_eq!(receipt.status, None);
        }
        std::fs::write(&path, &valid)?;
        refresh(root.path(), &mut receipt)?;
        std::fs::rename(&path, root.path().join("preserved-receipt.json"))?;
        assert!(refresh(root.path(), &mut receipt).is_err());
        assert_eq!(receipt.status, None);
        Ok(())
    }
    #[test]
    fn missing_exit_never_accepts_a_result_file() {
        use crate::offdesk::{BackgroundProbe, BackgroundRunnerKind, BackgroundRunnerPhase};
        let mut probe = BackgroundProbe::new("test", BackgroundRunnerKind::LocalBackground);
        probe.result_artifact_present = true;
        probe.local_exit_receipt = Some(LocalExitReceipt {
            relative_path: "absent".into(),
            nonce: "test".into(),
            status: None,
            wait_scope: None,
        });
        let decision = probe.evaluate(chrono::Utc::now());
        assert_ne!(decision.phase, BackgroundRunnerPhase::Completed);
        probe.runtime_handle_alive = true;
        probe.worker_heartbeat_stale = true;
        assert_eq!(
            probe.evaluate(chrono::Utc::now()).phase,
            BackgroundRunnerPhase::StaleLostCallback
        );
        probe.worker_heartbeat_stale = false;
        assert!(!probe.evaluate(chrono::Utc::now()).terminal);
        probe.local_exit_receipt.as_mut().unwrap().status = Some(7);
        assert_eq!(
            probe.evaluate(chrono::Utc::now()).phase,
            BackgroundRunnerPhase::Failed
        );
    }
    #[test]
    fn wait_status_survives_serialization_and_rejects_wrong_receipt() -> Result<()> {
        let root = tempfile::tempdir()?;
        for code in [0, 7] {
            let (mut process, receipt) = command(root.path(), &format!("exit {code}"))?;
            assert_eq!(process.status()?.code(), Some(code));
            let mut restored: LocalExitReceipt =
                serde_json::from_slice(&serde_json::to_vec(&receipt)?)?;
            refresh(root.path(), &mut restored)?;
            assert_eq!(restored.status, Some(code as u8));
            restored.nonce = "wrong".into();
            assert!(refresh(root.path(), &mut restored).is_err());
            assert_eq!(restored.status, None);
        }
        Ok(())
    }
}
#[cfg(target_os = "linux")]
#[test]
fn detached_child_delays_receipt_and_supervisor_loss_never_proves_release() -> Result<()> {
    use std::time::{Duration, Instant};
    fn wait_for(path: &Path) {
        let deadline = Instant::now() + Duration::from_secs(8);
        while !path.exists() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(path.exists(), "fixture child did not reach checkpoint");
    }
    for kill_supervisor in [false, true] {
        let root = tempfile::tempdir()?;
        let source = "setsid /bin/sh -c 'echo ready > child-ready; n=0; while [ ! -f release ] && [ \"$n\" -lt 300 ]; do n=$((n+1)); sleep 0.01; done; echo done > child-done' >/dev/null 2>&1 & exit 0";
        let (mut command, mut receipt) = command(root.path(), source)?;
        let mut supervisor = command.current_dir(root.path()).spawn()?;
        wait_for(&root.path().join("child-ready"));
        assert!(refresh(root.path(), &mut receipt).is_err());
        assert!(supervisor.try_wait()?.is_none());
        if kill_supervisor {
            supervisor.kill()?;
            supervisor.wait()?;
        }
        std::fs::write(root.path().join("release"), b"release fixture child")?;
        wait_for(&root.path().join("child-done"));
        if kill_supervisor {
            assert!(refresh(root.path(), &mut receipt).is_err());
            assert_eq!(receipt.status, None);
        } else {
            assert!(supervisor.wait()?.success());
            refresh(root.path(), &mut receipt)?;
            assert_eq!(receipt.status, Some(0));
            assert_eq!(
                receipt.wait_scope.as_deref(),
                Some("linux_subreaper_children")
            );
        }
    }
    Ok(())
}
