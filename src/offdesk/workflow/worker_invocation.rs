//! Supported adapter selection; command metadata alone never grants a contract.
use super::{codex_review_invocation, metadata_consumer_invocation, native_worker_invocation};
use crate::offdesk::{OffdeskShiftManifestV1, OffdeskShiftWindowV1, OffdeskTask};
use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde_json::Value;

pub fn inspect(
    task: &OffdeskTask,
    window: Option<&OffdeskShiftWindowV1>,
    now: DateTime<Utc>,
) -> Result<Option<Value>> {
    let count = task
        .artifact_refs
        .iter()
        .filter(|a| {
            a.artifact_id.starts_with("native-worker-invocation")
                || a.artifact_id.starts_with("codex-review-invocation")
                || a.artifact_id.starts_with("metadata-consumer-invocation")
        })
        .count();
    ensure!(
        count <= 1,
        "multiple or conflicting worker invocation bindings"
    );
    if let Some(view) = native_worker_invocation::inspect(task, window, now)? {
        return Ok(Some(view));
    }
    if let Some(view) = codex_review_invocation::inspect(task, window, now)? {
        return Ok(Some(view));
    }
    metadata_consumer_invocation::inspect(task, window, now)
}

pub(crate) fn bound(view: &Value) -> bool {
    (view["status"] == "verified_invocation_adapter_rechecks_source_at_entry"
        && view["worker_execution_contract"]["schema"] == "offdesk_native_execution_contract.v1")
        || (view["status"] == "verified_codex_review_adapter_rechecks_source_at_entry"
            && view["worker_execution_contract"]["schema"] == "offdesk_codex_execution_contract.v1")
        || (view["status"] == "verified_metadata_adapter_rechecks_source_at_entry"
            && view["worker_execution_contract"]["schema"]
                == "offdesk_metadata_execution_contract.v1")
}

pub fn validate_selected(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
    now: DateTime<Utc>,
) -> Result<()> {
    for binding in &manifest.task_bindings {
        let task = tasks
            .iter()
            .find(|t| t.task_id == binding.task_id)
            .context("selected worker task missing")?;
        let view = inspect(task, Some(&manifest.window), now)?;
        let metadata = view
            .as_ref()
            .is_some_and(|v| v["adapter"] == "deterministic_metadata");
        let metadata_policy = manifest.read_policy.as_ref()
            == Some(&crate::offdesk::read_policy::ReadPolicy::metadata());
        ensure!(
            metadata == metadata_policy
                && (!metadata || manifest.host_policy_attestation.is_none()),
            "metadata requires its dedicated read policy and only metadata tasks may select it"
        );
        if view.is_some() {
            ensure!(
                manifest.budget.max_attempts_per_task == 1,
                "bound worker requires one task attempt"
            );
        }
    }
    Ok(())
}
