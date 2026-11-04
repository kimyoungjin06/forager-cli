//! Intent/completion journal for explicit containment closure. No CLI enables
//! this mutation yet; neither a journal nor closure authorizes task release.

use super::{local_exit::LocalExitReceipt, runtime_occupancy::DomainBinding, OffdeskTask};
use crate::process;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[cfg(any(target_os = "linux", test))]
use std::io::Write;
use std::{fs::File, io::Read, path::Path};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SealJournalState {
    NotStarted,
    Incomplete,
    Completed,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: String,
    operation_id: String,
    binding: serde_json::Value,
    seal: Option<serde_json::Value>,
}

pub(crate) fn binding(
    task: &OffdeskTask,
    receipt: &LocalExitReceipt,
    domain: &DomainBinding,
) -> serde_json::Value {
    serde_json::json!({"task_id":task.task_id,"ticket_id":task.background_ticket_id,
        "project_key":task.project_key,"request_id":task.request_id,"workdir":task.workdir,
        "nonce":receipt.nonce,"receipt_path":receipt.relative_path,"domain":domain})
}

fn names(binding: &serde_json::Value) -> Result<(String, String)> {
    let hash = format!("{:x}", Sha256::digest(serde_json::to_vec(binding)?));
    Ok((
        format!("containment-seal-{hash}.intent.json"),
        format!("containment-seal-{hash}.completed.json"),
    ))
}

fn read(root: &File, name: &str) -> Result<Option<Record>> {
    let file = match process::open_bounded_regular_file(root, Path::new(name)) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "seal journal must have one link"
    );
    let before = process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut reader = file.take(32769);
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 32768, "seal journal too large");
    let record = serde_json::from_slice(&bytes)?;
    ensure!(
        before == process::FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?),
        "seal journal changed"
    );
    let reopened = process::open_bounded_regular_file(root, Path::new(name))?;
    ensure!(
        before == process::FilesystemObjectSnapshot::from_metadata(&reopened.metadata()?),
        "seal journal replaced"
    );
    Ok(Some(record))
}

pub(crate) fn inspect(root: &File, binding: &serde_json::Value) -> Result<SealJournalState> {
    let (intent_name, complete_name) = names(binding)?;
    let intent = read(root, &intent_name)?;
    let complete = read(root, &complete_name)?;
    let Some(intent) = intent else {
        ensure!(complete.is_none(), "seal completion has no intent");
        return Ok(SealJournalState::NotStarted);
    };
    ensure!(
        intent.schema == "containment_seal_intent.v1"
            && intent.binding == *binding
            && intent.seal.is_none(),
        "seal intent binding invalid"
    );
    uuid::Uuid::parse_str(&intent.operation_id)?;
    let Some(complete) = complete else {
        return Ok(SealJournalState::Incomplete);
    };
    ensure!(
        complete.schema == "containment_seal_completed.v1"
            && complete.binding == *binding
            && complete.operation_id == intent.operation_id,
        "seal completion binding invalid"
    );
    let seal = complete
        .seal
        .ok_or_else(|| anyhow::anyhow!("seal result missing"))?;
    ensure!(
        seal == serde_json::json!({"schema":"linux_cgroup_admission_seal.v1",
        "path":binding["domain"]["path"],"identity":binding["domain"]["identity"],
        "admissions_closed":true,"release_authorized":false}),
        "seal result mismatch"
    );
    Ok(SealJournalState::Completed)
}

#[cfg(any(target_os = "linux", test))]
fn write_once(root: &File, name: &str, record: &Record) -> Result<()> {
    let bytes = serde_json::to_vec(record)?;
    ensure!(bytes.len() <= 32768, "seal record exceeds limit");
    let partial = format!(".seal-{}.partial", uuid::Uuid::new_v4());
    let mut file = process::create_new_bounded_regular_file(root, Path::new(&partial))?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    process::finalize_bounded_file_noreplace(
        root,
        Path::new(""),
        Path::new(&partial),
        Path::new(name),
    )?;
    Ok(())
}

#[cfg(any(target_os = "linux", test))]
fn transaction(
    root: &File,
    binding: serde_json::Value,
    close: impl FnOnce() -> Result<serde_json::Value>,
) -> Result<SealJournalState> {
    let state = inspect(root, &binding)?;
    if state != SealJournalState::NotStarted {
        return Ok(state);
    }
    let (intent_name, complete_name) = names(&binding)?;
    let mut record = Record {
        schema: "containment_seal_intent.v1".into(),
        operation_id: uuid::Uuid::new_v4().to_string(),
        binding,
        seal: None,
    };
    write_once(root, &intent_name, &record)?;
    record.seal = Some(close()?);
    record.schema = "containment_seal_completed.v1".into();
    write_once(root, &complete_name, &record)?;
    inspect(root, &record.binding)
}

/// Explicit internal mutation. Holds the existing profile task lock, rereads
/// exact task/probe binding, and records intent before removing an empty domain.
/// An incomplete operation is never automatically retried, even if now absent.
#[cfg(target_os = "linux")]
pub fn seal_task(profile: &Path, task_id: &str) -> Result<SealJournalState> {
    use super::{BackgroundRunStore, BackgroundRunnerKind, OffdeskTaskStore, OffdeskTickLockGuard};
    let root = process::open_bounded_root(profile)?;
    let guard =
        OffdeskTickLockGuard::acquire(profile, chrono::Utc::now(), chrono::Duration::minutes(30))?;
    guard.ensure_root(profile)?;
    let tasks = OffdeskTaskStore::new(profile).load()?;
    let matches: Vec<_> = tasks
        .iter()
        .filter(|task| task.task_id == task_id)
        .collect();
    ensure!(matches.len() == 1, "seal requires one exact task");
    let task = matches[0];
    let ticket = task
        .background_ticket_id
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("seal requires ticket"))?;
    let probes = BackgroundRunStore::new(profile).load()?;
    let matches: Vec<_> = probes
        .iter()
        .filter(|probe| &probe.ticket_id == ticket)
        .collect();
    ensure!(matches.len() == 1, "seal requires one exact probe");
    let probe = matches[0];
    ensure!(
        probe.runner_kind == BackgroundRunnerKind::LocalBackground
            && probe.task_id.as_deref() == Some(&task.task_id)
            && probe.project_key.as_deref() == Some(&task.project_key)
            && probe.request_id.as_deref() == Some(&task.request_id)
            && probe.working_dir.as_deref() == Some(&task.workdir),
        "seal probe binding mismatch"
    );
    let receipt = probe
        .local_exit_receipt
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("seal receipt missing"))?;
    ensure!(
        receipt.wait_scope.as_deref() == Some(super::local_exit::CONTAINED_SCOPE),
        "seal requires contained launch"
    );
    let domain = super::runtime_occupancy::read_containment(task, receipt)?;
    let key = binding(task, receipt, &domain);
    if inspect(&root, &key)? == SealJournalState::NotStarted {
        let identity = serde_json::from_value(domain.identity.clone())?;
        ensure!(
            !process::inspect_cgroup_v2(&domain.path, Some(&identity))?.populated,
            "cannot begin closure while domain is populated"
        );
    }
    let current = process::open_bounded_root(profile)?;
    ensure!(
        process::filesystem_file_identity(&root.metadata()?)
            == process::filesystem_file_identity(&current.metadata()?),
        "seal profile replaced"
    );
    transaction(&root, key, || {
        let identity = serde_json::from_value(domain.identity)?;
        Ok(serde_json::to_value(process::seal_empty_cgroup_v2(
            &domain.path,
            &identity,
        )?)?)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key() -> serde_json::Value {
        serde_json::json!({"task_id":"fixture","domain":{"path":"/fixture","identity":{"fixture":true}}})
    }
    fn seal(key: &serde_json::Value) -> serde_json::Value {
        serde_json::json!({"schema":"linux_cgroup_admission_seal.v1","path":key["domain"]["path"],
            "identity":key["domain"]["identity"],"admissions_closed":true,"release_authorized":false})
    }

    #[test]
    fn completed_replay_never_repeats_mutation_and_corruption_fails_closed() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let root = process::open_bounded_root(temp.path())?;
        let binding = key();
        assert_eq!(
            transaction(&root, binding.clone(), || Ok(seal(&binding)))?,
            SealJournalState::Completed
        );
        assert_eq!(
            transaction(&root, binding.clone(), || panic!("replayed closure"))?,
            SealJournalState::Completed
        );
        let (_, name) = names(&binding)?;
        let path = temp.path().join(name);
        let original = std::fs::read(&path)?;
        let mut record: Record = serde_json::from_slice(&original)?;
        record.operation_id = uuid::Uuid::new_v4().to_string();
        std::fs::write(&path, serde_json::to_vec(&record)?)?;
        assert!(inspect(&root, &binding).is_err());
        std::fs::write(&path, vec![b' '; 32769])?;
        assert!(inspect(&root, &binding).is_err());
        std::fs::write(&path, original)?;
        std::fs::remove_file(temp.path().join(names(&binding)?.0))?;
        assert!(inspect(&root, &binding).is_err());
        Ok(())
    }

    #[test]
    fn seal_crash_worker() -> Result<()> {
        let Ok(path) = std::env::var("FORAGER_TEST_SEAL_CRASH_ROOT") else {
            return Ok(());
        };
        let _guard = super::super::OffdeskTickLockGuard::acquire(
            &path,
            chrono::Utc::now(),
            chrono::Duration::minutes(30),
        )?;
        let root = process::open_bounded_root(Path::new(&path))?;
        transaction(&root, key(), || {
            if std::env::var("FORAGER_TEST_SEAL_CRASH_STAGE")?.as_str() == "after_close" {
                // Owned stand-in for the irreversible kernel operation.
                let mut marker = File::create(Path::new(&path).join("closed-marker"))?;
                marker.write_all(b"closed")?;
                marker.sync_all()?;
            }
            std::process::exit(91);
        })?;
        anyhow::bail!("crash worker unexpectedly returned")
    }

    #[test]
    fn process_crash_before_or_after_close_keeps_uncertainty_and_unlocks() -> Result<()> {
        for stage in ["before_close", "after_close"] {
            let temp = tempfile::tempdir()?;
            let result = std::process::Command::new(std::env::current_exe()?)
                .args([
                    "--exact",
                    "offdesk::containment_seal::tests::seal_crash_worker",
                    "--nocapture",
                ])
                .env("FORAGER_TEST_SEAL_CRASH_ROOT", temp.path())
                .env("FORAGER_TEST_SEAL_CRASH_STAGE", stage)
                .status()?;
            assert_eq!(result.code(), Some(91));
            let _guard = super::super::OffdeskTickLockGuard::acquire(
                temp.path(),
                chrono::Utc::now(),
                chrono::Duration::minutes(30),
            )?;
            let root = process::open_bounded_root(temp.path())?;
            assert_eq!(inspect(&root, &key())?, SealJournalState::Incomplete);
            assert_eq!(
                transaction(&root, key(), || panic!("uncertain closure repeated"))?,
                SealJournalState::Incomplete
            );
            assert_eq!(
                temp.path().join("closed-marker").exists(),
                stage == "after_close"
            );
        }
        Ok(())
    }
}
