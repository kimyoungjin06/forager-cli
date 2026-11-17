//! Conservative release evidence for the initial local-background execution scope.

use super::{BackgroundRunStore, BackgroundRunnerKind, OffdeskTask};
use anyhow::{ensure, Result};
use std::io::Read;
use std::path::Path;

#[derive(Debug, serde::Serialize)]
pub(crate) struct RecoveryInspection {
    pub schema: &'static str,
    pub release_verified: bool,
    pub reason: &'static str,
    pub next_action: &'static str,
    pub automatic_recovery: bool,
}

fn inspection(
    release_verified: bool,
    reason: &'static str,
    next_action: &'static str,
) -> RecoveryInspection {
    RecoveryInspection {
        schema: "offdesk_task_runtime_recovery.v1",
        release_verified,
        reason,
        next_action,
        automatic_recovery: false,
    }
}

pub(crate) fn released(profile: &Path, task: &OffdeskTask) -> Result<bool> {
    Ok(inspect(profile, task)?.release_verified)
}

pub(crate) fn inspect(profile: &Path, task: &OffdeskTask) -> Result<RecoveryInspection> {
    let Some(ticket) = &task.background_ticket_id else {
        let active = matches!(
            task.status,
            super::OffdeskTaskStatus::Launched | super::OffdeskTaskStatus::Running
        );
        return Ok(if active {
            inspection(
                false,
                "active_task_missing_ticket",
                "recover exact launch identity; do not clear or replace task",
            )
        } else {
            inspection(
                true,
                "no_runtime_ticket",
                "normal task and approval gates still apply",
            )
        });
    };
    let probes = BackgroundRunStore::new(profile).load()?;
    let matches: Vec<_> = probes
        .iter()
        .filter(|probe| &probe.ticket_id == ticket)
        .collect();
    let [probe] = matches.as_slice() else {
        return Ok(inspection(
            false,
            "runtime_probe_missing_or_ambiguous",
            "recover the exact ticket record; do not infer process absence",
        ));
    };
    inspect_probe(profile, task, probe, false)
}

/// Presentation only. This does not widen scheduler or workdir-release authority.
pub(crate) fn inspect_failure_history(
    profile: &Path,
    task: &OffdeskTask,
    probe: &super::BackgroundProbe,
) -> Result<RecoveryInspection> {
    inspect_probe(profile, task, probe, true)
}

fn inspect_probe(
    profile: &Path,
    task: &OffdeskTask,
    probe: &super::BackgroundProbe,
    history: bool,
) -> Result<RecoveryInspection> {
    let supported = probe.runner_kind == BackgroundRunnerKind::LocalBackground
        || (history
            && probe.runner_kind == BackgroundRunnerKind::LocalTmux
            && task.runner_kind == BackgroundRunnerKind::LocalTmux);
    if !supported
        || Some(&probe.ticket_id) != task.background_ticket_id.as_ref()
        || probe.task_id.as_deref() != Some(&task.task_id)
        || probe.project_key.as_deref() != Some(&task.project_key)
        || probe.request_id.as_deref() != Some(&task.request_id)
        || probe.working_dir.as_deref() != Some(&task.workdir)
    {
        return Ok(inspection(
            false,
            "runtime_binding_unsupported_or_mismatched",
            "inspect runner and exact task binding; no automatic release",
        ));
    }
    if let Some(receipt) = probe
        .local_exit_receipt
        .as_ref()
        .filter(|receipt| receipt.wait_scope.as_deref() == Some(super::local_exit::CONTAINED_SCOPE))
    {
        return Ok(inspect_containment(profile, task, receipt));
    }
    let Some(pid) = probe.runtime_pid else {
        return Ok(inspection(
            false,
            "supervisor_pid_missing",
            "recover exact supervisor identity before any process action",
        ));
    };
    let Some(identity) = probe.runtime_identity.as_deref() else {
        return Ok(inspection(
            false,
            "supervisor_identity_missing",
            "do not terminate or release by PID alone",
        ));
    };
    if let Some(current) = crate::process::process_identity(pid) {
        return Ok(if current == identity {
            inspection(
                false,
                "supervisor_alive",
                "wait and poll the exact ticket; descendants may still be running",
            )
        } else {
            inspection(
                false,
                "supervisor_pid_reused",
                "do not signal the reused PID; original descendants remain unverified",
            )
        });
    }
    if let Some(mut receipt) = probe.local_exit_receipt.clone() {
        if super::local_exit::refresh(Path::new(&task.workdir), &mut receipt).is_ok()
            && receipt.status.is_some()
            && receipt.wait_scope.as_deref() == Some("linux_subreaper_children")
        {
            return Ok(inspection(
                true,
                "descendant_wait_receipt_verified",
                "normal retry and approval gates still apply; inspection does not launch work",
            ));
        }
    }
    // Parent termination alone cannot prove detached descendants stopped.
    Ok(inspection(false, "supervisor_lost_descendants_unverified", "keep workdir held; descendant identities cannot be reconstructed from a dead parent; inspect externally and do not clear tickets or auto-retry"))
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ContainmentBinding {
    schema: String,
    ticket_id: String,
    task_id: String,
    project_key: String,
    request_id: String,
    nonce: String,
    containment: DomainBinding,
}

#[derive(Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DomainBinding {
    schema: String,
    pub(crate) path: std::path::PathBuf,
    pub(crate) identity: serde_json::Value,
    placement: String,
    release_authorized: bool,
}

pub(crate) fn read_containment(
    task: &OffdeskTask,
    receipt: &super::local_exit::LocalExitReceipt,
) -> Result<DomainBinding> {
    let root = crate::process::open_bounded_root(Path::new(&task.workdir))?;
    let relative = Path::new(&receipt.relative_path).with_file_name("containment.json");
    let file = crate::process::open_bounded_regular_file(&root, &relative)?;
    let before = crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        crate::process::filesystem_link_count(&file.metadata()?) == Some(1),
        "containment evidence must have one link"
    );
    let mut reader = file.take(8193);
    let mut bytes = Vec::new();
    reader.read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 8192, "containment evidence too large");
    let record: ContainmentBinding = serde_json::from_slice(&bytes)?;
    ensure!(
        record.schema == "offdesk_containment_binding.v1"
            && Some(&record.ticket_id) == task.background_ticket_id.as_ref()
            && record.task_id == task.task_id
            && record.project_key == task.project_key
            && record.request_id == task.request_id
            && record.nonce == receipt.nonce,
        "containment task binding mismatch"
    );
    ensure!(
        record.containment.schema == "linux_cgroup_launch_binding.v1"
            && record.containment.placement == "pre_exec_cgroup_procs"
            && !record.containment.release_authorized
            && record.containment.path.is_absolute(),
        "unsupported containment contract"
    );
    ensure!(
        before
            == crate::process::FilesystemObjectSnapshot::from_metadata(
                &reader.get_ref().metadata()?
            ),
        "containment evidence changed"
    );
    let reopened = crate::process::open_bounded_regular_file(&root, &relative)?;
    ensure!(
        before == crate::process::FilesystemObjectSnapshot::from_metadata(&reopened.metadata()?),
        "containment evidence replaced"
    );
    let current_root = crate::process::open_bounded_root(Path::new(&task.workdir))?;
    ensure!(
        crate::process::filesystem_file_identity(&root.metadata()?)
            == crate::process::filesystem_file_identity(&current_root.metadata()?),
        "containment root replaced"
    );
    Ok(record.containment)
}

fn observe_domain(binding: DomainBinding) -> Result<bool> {
    #[cfg(target_os = "linux")]
    {
        let identity = serde_json::from_value(binding.identity)?;
        Ok(crate::process::inspect_cgroup_v2(&binding.path, Some(&identity))?.populated)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (binding.path, binding.identity);
        anyhow::bail!("cgroup inspection requires Linux")
    }
}

fn inspect_containment(
    profile: &Path,
    task: &OffdeskTask,
    receipt: &super::local_exit::LocalExitReceipt,
) -> RecoveryInspection {
    let binding = match read_containment(task, receipt) {
        Ok(binding) => binding,
        Err(_) => return inspection(
            false,
            "containment_binding_missing_or_invalid",
            "keep workdir held; restore exact launch evidence; do not fall back to shell receipt",
        ),
    };
    let journal = crate::process::open_bounded_root(profile)
        .map_err(anyhow::Error::from)
        .and_then(|root| {
            super::containment_seal::inspect(
                &root,
                &super::containment_seal::binding(task, receipt, &binding),
            )
        });
    use super::containment_seal::SealJournalState;
    match journal {
        Ok(SealJournalState::Completed) => return inspection(false, "containment_sealed_migration_unverified", "closure recorded; prior migration is unverified; keep workdir held"),
        Ok(SealJournalState::Incomplete) => return inspection(false, "containment_seal_incomplete", "intent exists without confirmed closure; do not retry closure or infer success from absence"),
        Err(_) => return inspection(false, "containment_seal_journal_invalid", "closure journal unreadable or mismatched; preserve evidence and keep workdir held"),
        Ok(SealJournalState::NotStarted) => {},
    }
    match observe_domain(binding) {
        Ok(true) => inspection(false, "containment_populated", "bound cgroup still contains live processes; no release or automatic termination"),
        Ok(false) => inspection(false, "containment_empty_admission_unfenced", "empty domain observed, but future admissions and migration are not fenced; keep workdir held"),
        Err(_) => inspection(false, "containment_observation_unverified", "domain missing, changed or unreadable; absence is not verified emptiness; keep workdir held"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture(
        root: &Path,
    ) -> (
        OffdeskTask,
        super::super::local_exit::LocalExitReceipt,
        serde_json::Value,
    ) {
        let task = serde_json::from_value(json!({
            "task_id":"task", "request_id":"request", "project_key":"project",
            "status":"failed", "capability_id":"background.launch", "runner_kind":"local_background",
            "command":"exit 0", "workdir":root, "background_ticket_id":"ticket",
            "created_at":chrono::Utc::now(), "updated_at":chrono::Utc::now()
        })).unwrap();
        let receipt = super::super::local_exit::LocalExitReceipt {
            relative_path: "receipt.json".into(),
            nonce: "nonce".into(),
            status: Some(0),
            wait_scope: Some(super::super::local_exit::CONTAINED_SCOPE.into()),
        };
        let record = json!({"schema":"offdesk_containment_binding.v1", "task_id":"task",
            "request_id":"request", "project_key":"project", "ticket_id":"ticket", "nonce":"nonce",
            "containment":{"schema":"linux_cgroup_launch_binding.v1", "path":root,
                "identity":{}, "placement":"pre_exec_cgroup_procs", "release_authorized":false}});
        (task, receipt, record)
    }

    #[test]
    fn every_binding_component_and_contract_is_checked() -> Result<()> {
        let root = tempfile::tempdir()?;
        let (task, receipt, record) = fixture(root.path());
        let path = root.path().join("containment.json");
        std::fs::write(&path, serde_json::to_vec(&record)?)?;
        assert!(read_containment(&task, &receipt).is_ok());
        for field in [
            "schema",
            "task_id",
            "request_id",
            "project_key",
            "ticket_id",
            "nonce",
        ] {
            let mut invalid = record.clone();
            invalid[field] = json!("wrong");
            std::fs::write(&path, serde_json::to_vec(&invalid)?)?;
            assert!(read_containment(&task, &receipt).is_err(), "{field}");
        }
        for (field, value) in [
            ("schema", json!("other")),
            ("placement", json!("after_exec")),
            ("release_authorized", json!(true)),
            ("path", json!("relative")),
        ] {
            let mut invalid = record.clone();
            invalid["containment"][field] = value;
            std::fs::write(&path, serde_json::to_vec(&invalid)?)?;
            assert!(read_containment(&task, &receipt).is_err(), "{field}");
        }
        Ok(())
    }

    #[test]
    fn missing_damaged_and_unreadable_domain_evidence_never_releases() -> Result<()> {
        let root = tempfile::tempdir()?;
        let (task, receipt, record) = fixture(root.path());
        let path = root.path().join("containment.json");
        assert_eq!(
            inspect_containment(root.path(), &task, &receipt).reason,
            "containment_binding_missing_or_invalid"
        );
        for bytes in [
            b"{".to_vec(),
            vec![b' '; 8193],
            br#"{"unknown":true}"#.to_vec(),
        ] {
            std::fs::write(&path, bytes)?;
            assert_eq!(
                inspect_containment(root.path(), &task, &receipt).reason,
                "containment_binding_missing_or_invalid"
            );
        }
        std::fs::write(&path, serde_json::to_vec(&record)?)?;
        let result = inspect_containment(root.path(), &task, &receipt);
        assert_eq!(result.reason, "containment_observation_unverified");
        assert!(!result.release_verified);
        assert!(!result.automatic_recovery);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn linked_evidence_is_rejected() -> Result<()> {
        let root = tempfile::tempdir()?;
        let (task, receipt, record) = fixture(root.path());
        let source = root.path().join("source.json");
        let evidence = root.path().join("containment.json");
        std::fs::write(&source, serde_json::to_vec(&record)?)?;
        std::os::unix::fs::symlink(&source, &evidence)?;
        assert!(read_containment(&task, &receipt).is_err());
        std::fs::remove_file(&evidence)?;
        std::fs::hard_link(&source, &evidence)?;
        assert!(read_containment(&task, &receipt).is_err());
        Ok(())
    }

    #[test]
    fn closure_journal_diagnostics_never_release_or_rewrite() -> Result<()> {
        use sha2::{Digest, Sha256};
        let root = tempfile::tempdir()?;
        let (task, receipt, record) = fixture(root.path());
        std::fs::write(
            root.path().join("containment.json"),
            serde_json::to_vec(&record)?,
        )?;
        let domain = read_containment(&task, &receipt)?;
        let key = super::super::containment_seal::binding(&task, &receipt, &domain);
        let hash = format!("{:x}", Sha256::digest(serde_json::to_vec(&key)?));
        let intent_path = root
            .path()
            .join(format!("containment-seal-{hash}.intent.json"));
        let completion_path = root
            .path()
            .join(format!("containment-seal-{hash}.completed.json"));
        let operation_id = uuid::Uuid::new_v4().to_string();
        let intent = json!({"schema":"containment_seal_intent.v1","operation_id":operation_id,"binding":key,"seal":null});
        let completion = json!({"schema":"containment_seal_completed.v1","operation_id":operation_id,"binding":key,
            "seal":{"schema":"linux_cgroup_admission_seal.v1","path":domain.path,"identity":domain.identity,
                "admissions_closed":true,"release_authorized":false}});
        std::fs::write(&intent_path, serde_json::to_vec(&intent)?)?;
        assert_eq!(
            inspect_containment(root.path(), &task, &receipt).reason,
            "containment_seal_incomplete"
        );
        for (bytes, expected) in [
            (b"{".to_vec(), "containment_seal_journal_invalid"),
            (
                serde_json::to_vec(&completion)?,
                "containment_sealed_migration_unverified",
            ),
        ] {
            std::fs::write(&completion_path, &bytes)?;
            let result = inspect_containment(root.path(), &task, &receipt);
            assert_eq!(result.reason, expected);
            assert!(!result.release_verified);
            assert!(!result.automatic_recovery);
            assert_eq!(std::fs::read(&completion_path)?, bytes);
        }
        Ok(())
    }
}
