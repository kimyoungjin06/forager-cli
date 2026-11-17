//! Verified Offdesk task termination.
//!
//! Cancellation is a durable lifecycle decision. Termination additionally
//! proves that the exact local runtime bound to the task was stopped before the
//! task is marked cancelled.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::path::Path;

use super::background::BackgroundRunStore;
use super::runner::{terminate_background_run, BackgroundTerminationReport};
use super::task_queue::{OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskStore};

pub const OFFDESK_TASK_TERMINATION_REPORT_SCHEMA: &str = "offdesk_task_termination_report.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskTaskTerminationReport {
    pub schema: &'static str,
    pub task_id: String,
    pub changed: bool,
    pub task: OffdeskTaskLifecycleReport,
    pub background: BackgroundTerminationReport,
}

pub fn terminate_offdesk_task(
    profile_dir: impl AsRef<Path>,
    task_id: &str,
    reason: Option<&str>,
    terminated_by: &str,
    now: DateTime<Utc>,
) -> Result<OffdeskTaskTerminationReport> {
    let profile_dir = profile_dir.as_ref();
    let guard = super::tick_lock::OffdeskTickLockGuard::acquire(
        profile_dir,
        now,
        chrono::Duration::minutes(30),
    )?;
    let task_store = OffdeskTaskStore::new(profile_dir);
    let task = task_store
        .load()?
        .into_iter()
        .find(|task| task.task_id == task_id)
        .with_context(|| format!("offdesk task not found: {task_id}"))?;
    if task.status == OffdeskTaskStatus::Completed {
        bail!("completed tasks cannot be terminated");
    }
    let ticket_id = task
        .background_ticket_id
        .as_deref()
        .context("task has no background runtime to terminate")?;

    let background_store = BackgroundRunStore::new(profile_dir);
    let probe = background_store
        .load()?
        .into_iter()
        .find(|probe| probe.ticket_id == ticket_id)
        .with_context(|| format!("background ticket not found: {ticket_id}"))?;
    if probe.task_id.as_deref() != Some(task_id) {
        bail!("background ticket is not bound to the requested task");
    }

    let background =
        terminate_background_run(&background_store, ticket_id, terminated_by, reason, now)?;
    let task = task_store.cancel_task_locked(task_id, reason, now, &guard)?;
    let changed = background.changed || task.changed;

    Ok(OffdeskTaskTerminationReport {
        schema: OFFDESK_TASK_TERMINATION_REPORT_SCHEMA,
        task_id: task_id.to_string(),
        changed,
        task,
        background,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{BackgroundProbe, BackgroundRunnerKind, OffdeskTask, OffdeskTaskInput};
    use tempfile::tempdir;

    fn task(now: DateTime<Utc>, runner_kind: BackgroundRunnerKind) -> OffdeskTask {
        let mut task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some("task".to_string()),
                request_id: "request".to_string(),
                project_key: "project".to_string(),
                capability_id: "dispatch.runtime".to_string(),
                runner_kind,
                command: "true".to_string(),
                workdir: "/tmp".to_string(),
                execution_brief: None,
                not_before: None,
                mutation_class: None,
                artifact_refs: Vec::new(),
                implementation_packet: None,
                artifact_kind: None,
                agent_mode: None,
                provider_id: None,
                model: None,
                preview: String::new(),
                reason: String::new(),
                log_artifact_path: None,
                result_artifact_path: None,
            },
            now,
        );
        task.status = OffdeskTaskStatus::Running;
        task.background_ticket_id = Some("ticket".to_string());
        task
    }

    #[test]
    fn termination_records_stopped_runtime_and_cancels_task() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let task_store = OffdeskTaskStore::new(temp.path());
        task_store.enqueue(task(now, BackgroundRunnerKind::LocalBackground))?;
        let background_store = BackgroundRunStore::new(temp.path());
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.task_id = Some("task".to_string());
        background_store.upsert(probe)?;

        let report =
            terminate_offdesk_task(temp.path(), "task", Some("operator stop"), "test", now)?;

        assert!(report.changed);
        assert!(!report.background.changed);
        assert_eq!(report.task.status, OffdeskTaskStatus::Cancelled);
        assert_eq!(task_store.load()?[0].status, OffdeskTaskStatus::Cancelled);
        assert_eq!(
            background_store.load()?[0].phase,
            super::super::BackgroundRunnerPhase::Terminated
        );
        Ok(())
    }

    #[test]
    fn external_runner_termination_fails_without_cancelling_task() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let task_store = OffdeskTaskStore::new(temp.path());
        task_store.enqueue(task(now, BackgroundRunnerKind::RemoteWorker))?;
        let background_store = BackgroundRunStore::new(temp.path());
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::RemoteWorker);
        probe.task_id = Some("task".to_string());
        background_store.upsert(probe)?;

        let error = terminate_offdesk_task(temp.path(), "task", None, "test", now)
            .expect_err("external termination must fail closed");

        assert!(error.to_string().contains("cannot be terminated"));
        assert_eq!(task_store.load()?[0].status, OffdeskTaskStatus::Running);
        Ok(())
    }
}
