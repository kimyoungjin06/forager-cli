//! One-shot offdesk control loop.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::approval::{ApprovalLedger, ApprovalLedgerSession, ApprovalStatus};
use super::background::{BackgroundProbe, BackgroundRunStore, BackgroundRunnerPhase};
use super::redaction::operator_safe_text;
use super::resume::{ResumeEvidence, ResumePendingInput, TaskResumeState, TaskResumeStore};
use super::runner::{
    launch_background_command_with_gate_outcome, poll_background_runs, BackgroundLaunchRequest,
    BackgroundPollOutcome, LocalCommandLaunchSpec,
};
use super::scheduler::{SchedulerGate, SchedulerGateRequest, SchedulerGateStatus};
use super::task_queue::{
    count_tasks, OffdeskTask, OffdeskTaskCounts, OffdeskTaskStatus, OffdeskTaskStore,
};
use super::tick_lock::OffdeskTickLockGuard;

#[derive(Debug, Clone)]
pub struct OffdeskTickOptions {
    pub limit: usize,
    pub now: DateTime<Utc>,
    pub notification_cooldown: Option<Duration>,
    pub lock_stale_after: Duration,
}

impl OffdeskTickOptions {
    pub fn new(now: DateTime<Utc>) -> Self {
        Self {
            limit: 10,
            now,
            notification_cooldown: None,
            lock_stale_after: Duration::minutes(30),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct OffdeskTickReport {
    pub expired_approvals: usize,
    pub polled_background: usize,
    pub launched: usize,
    pub pending_approval: usize,
    pub completed: usize,
    pub failed: usize,
    pub resume_pending: usize,
    pub skipped: usize,
    pub stale_lock_replaced: bool,
    pub updated_task_ids: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct OffdeskStatusSummary {
    pub pending_approvals: usize,
    pub tasks: OffdeskTaskCounts,
    pub background_active: usize,
    pub background_stale: usize,
    pub background_failed: usize,
}

pub fn run_offdesk_tick(
    profile_dir: impl AsRef<Path>,
    options: OffdeskTickOptions,
) -> Result<OffdeskTickReport> {
    let profile_dir = profile_dir.as_ref();
    let tick_lock =
        OffdeskTickLockGuard::acquire(profile_dir, options.now, options.lock_stale_after)?;
    let approval_ledger = ApprovalLedger::new(profile_dir);
    let background_store = BackgroundRunStore::new(profile_dir);
    let task_store = OffdeskTaskStore::new(profile_dir);
    let resume_store = TaskResumeStore::new(profile_dir);
    let gate = SchedulerGate::new(approval_ledger.clone());
    let (mut approval_session, expired) = approval_ledger.begin_session(options.now)?;
    let background_outcomes = poll_background_runs(
        &background_store,
        None,
        options.now,
        options.notification_cooldown,
    )?;
    let background_by_ticket = background_outcomes
        .iter()
        .map(|outcome| (outcome.probe.ticket_id.clone(), outcome))
        .collect::<HashMap<_, _>>();

    let mut report = OffdeskTickReport {
        expired_approvals: expired.len(),
        polled_background: background_outcomes.len(),
        stale_lock_replaced: tick_lock.stale_metadata_replaced(),
        ..OffdeskTickReport::default()
    };

    let mut tasks = task_store.load()?;
    for task in tasks.iter_mut() {
        apply_background_outcome(
            task,
            &background_by_ticket,
            &resume_store,
            options.now,
            &mut report,
        )?;
    }

    let mut dispatched = 0usize;
    for task in tasks.iter_mut() {
        if dispatched >= options.limit {
            if task.can_dispatch_at(options.now) {
                report.skipped += 1;
            }
            continue;
        }
        if !task.can_dispatch_at(options.now) {
            continue;
        }

        let before = task.status;
        if let Err(error) = dispatch_task(
            task,
            &gate,
            &mut approval_session,
            &background_store,
            options.now,
            &mut report,
        ) {
            approval_session.flush()?;
            return Err(error);
        }
        if task.status != before || matches!(task.status, OffdeskTaskStatus::PendingApproval) {
            dispatched += 1;
        }
    }

    approval_session.flush()?;
    task_store.save(&tasks)?;
    Ok(report)
}

pub fn load_offdesk_status_summary(
    profile_dir: impl AsRef<Path>,
    now: DateTime<Utc>,
) -> Result<OffdeskStatusSummary> {
    let profile_dir = profile_dir.as_ref();
    let approvals = ApprovalLedger::new(profile_dir).load()?;
    let tasks = OffdeskTaskStore::new(profile_dir).load()?;
    let backgrounds = BackgroundRunStore::new(profile_dir).load()?;

    let pending_approvals = approvals
        .iter()
        .filter(|approval| approval.status == ApprovalStatus::Pending && approval.expires_at >= now)
        .count();
    let mut summary = OffdeskStatusSummary {
        pending_approvals,
        tasks: count_tasks(&tasks),
        ..OffdeskStatusSummary::default()
    };

    for probe in backgrounds {
        match probe.phase {
            BackgroundRunnerPhase::Failed => summary.background_failed += 1,
            BackgroundRunnerPhase::StaleNoAck
            | BackgroundRunnerPhase::StaleLostCallback
            | BackgroundRunnerPhase::Reconstructable => summary.background_stale += 1,
            BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived => {}
            BackgroundRunnerPhase::Launched
            | BackgroundRunnerPhase::HandoffEmitted
            | BackgroundRunnerPhase::PickupAcknowledged => summary.background_active += 1,
        }
    }

    Ok(summary)
}

fn apply_background_outcome(
    task: &mut OffdeskTask,
    background_by_ticket: &HashMap<String, &BackgroundPollOutcome>,
    resume_store: &TaskResumeStore,
    now: DateTime<Utc>,
    report: &mut OffdeskTickReport,
) -> Result<()> {
    if !matches!(
        task.status,
        OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running | OffdeskTaskStatus::ResumePending
    ) {
        return Ok(());
    }

    let Some(ticket_id) = task.background_ticket_id.as_deref() else {
        return Ok(());
    };
    let Some(outcome) = background_by_ticket.get(ticket_id) else {
        return Ok(());
    };

    match outcome.decision.phase {
        BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived => {
            if task.status != OffdeskTaskStatus::Completed {
                task.status = OffdeskTaskStatus::Completed;
                task.last_error = None;
                task.updated_at = now;
                resume_store.clear_after_recovery(&task.project_key, &task.task_id, now)?;
                report.completed += 1;
                report.updated_task_ids.push(task.task_id.clone());
            }
        }
        BackgroundRunnerPhase::Failed
        | BackgroundRunnerPhase::StaleNoAck
        | BackgroundRunnerPhase::StaleLostCallback
        | BackgroundRunnerPhase::Reconstructable => {
            if task.status != OffdeskTaskStatus::ResumePending {
                let previous_status = task.status;
                task.status = OffdeskTaskStatus::ResumePending;
                task.last_error = Some(outcome.decision.evidence.clone());
                task.updated_at = now;
                write_resume_state(task, previous_status, outcome, resume_store, now)?;
                report.resume_pending += 1;
                report.updated_task_ids.push(task.task_id.clone());
            }
        }
        BackgroundRunnerPhase::Launched
        | BackgroundRunnerPhase::HandoffEmitted
        | BackgroundRunnerPhase::PickupAcknowledged => {
            if task.status != OffdeskTaskStatus::Running {
                task.status = OffdeskTaskStatus::Running;
                task.updated_at = now;
                report.updated_task_ids.push(task.task_id.clone());
            }
        }
    }

    Ok(())
}

fn dispatch_task(
    task: &mut OffdeskTask,
    gate: &SchedulerGate,
    approvals: &mut ApprovalLedgerSession,
    background_store: &BackgroundRunStore,
    now: DateTime<Utc>,
    report: &mut OffdeskTickReport,
) -> Result<()> {
    let mut gate_request = SchedulerGateRequest::new(
        task.capability_id.clone(),
        task.project_key.clone(),
        task.request_id.clone(),
        task.task_id.clone(),
    );
    gate_request.mutation_class = task
        .mutation_class
        .clone()
        .or_else(|| Some(task.capability_id.clone()));
    gate_request.preview = if task.preview.trim().is_empty() {
        task.command.clone()
    } else {
        task.preview.clone()
    };
    gate_request.reason = if task.reason.trim().is_empty() {
        "offdesk task requires operator approval".to_string()
    } else {
        task.reason.clone()
    };
    gate_request.source_surface = "offdesk.tick".to_string();

    let mut launch_request = BackgroundLaunchRequest::new(gate_request, task.runner_kind);
    launch_request.ticket_id = task.background_ticket_id.clone();
    launch_request.launch_spec_summary = Some(task.command.clone());
    launch_request.runtime_handle_alive = true;

    let mut command_spec = LocalCommandLaunchSpec::new(&task.command, PathBuf::from(&task.workdir));
    command_spec.log_artifact_path = task.log_artifact_path.as_ref().map(PathBuf::from);
    command_spec.result_artifact_path = task.result_artifact_path.as_ref().map(PathBuf::from);

    let gate_outcome = gate.evaluate_with_session(
        launch_request.gate_request.clone(),
        task.execution_brief.as_ref(),
        now,
        approvals,
    )?;
    let outcome = launch_background_command_with_gate_outcome(
        background_store,
        launch_request,
        gate_outcome,
        now,
        command_spec,
    )?;
    task.last_gate_status = Some(outcome.gate.status);
    match outcome.gate.status {
        SchedulerGateStatus::Proceed => {
            if let Some(probe) = outcome.probe {
                task.status = OffdeskTaskStatus::Launched;
                task.background_ticket_id = Some(probe.ticket_id);
                task.attempt_count += 1;
                task.last_error = None;
                task.updated_at = now;
                report.launched += 1;
                report.updated_task_ids.push(task.task_id.clone());
            }
        }
        SchedulerGateStatus::PendingApproval => {
            task.status = OffdeskTaskStatus::PendingApproval;
            task.updated_at = now;
            report.pending_approval += 1;
            report.updated_task_ids.push(task.task_id.clone());
        }
        SchedulerGateStatus::Denied | SchedulerGateStatus::Blocked => {
            task.status = OffdeskTaskStatus::Failed;
            task.last_error = Some(outcome.gate.reason);
            task.updated_at = now;
            report.failed += 1;
            report.updated_task_ids.push(task.task_id.clone());
        }
    }

    Ok(())
}

fn write_resume_state(
    task: &OffdeskTask,
    previous_status: OffdeskTaskStatus,
    outcome: &BackgroundPollOutcome,
    resume_store: &TaskResumeStore,
    now: DateTime<Utc>,
) -> Result<()> {
    let decision = &outcome.decision;
    let mut state = TaskResumeState::mark_pending(ResumePendingInput {
        task_id: task.task_id.clone(),
        request_id: task.request_id.clone(),
        project_key: task.project_key.clone(),
        phase: format!("{:?}", decision.phase).to_lowercase(),
        runner_target: format!("{:?}", task.runner_kind).to_lowercase(),
        interruption_reason: decision.evidence.clone(),
        interrupted_at: now,
        fresh_until: now + Duration::minutes(30),
    });
    state.background_ticket_id = task.background_ticket_id.clone();
    state.last_task_status = Some(format!("{:?}", previous_status).to_lowercase());
    state.attempt_count = task.attempt_count;
    state.last_durable_action = Some("background runner poll".to_string());
    state.last_evidence_artifacts = outcome
        .probe
        .log_artifact_path
        .iter()
        .chain(outcome.probe.result_artifact_path.iter())
        .cloned()
        .collect();
    state.last_log_tail = decision.last_log_tail.as_deref().map(operator_safe_text);
    state.evidence = resume_evidence_from_background(&outcome.probe, decision, now);
    state.next_safe_resume_step =
        "inspect background result sidecar and logs before retrying".to_string();
    resume_store.mark_resume_pending(state)
}

fn resume_evidence_from_background(
    probe: &BackgroundProbe,
    decision: &super::background::BackgroundRecoveryDecision,
    now: DateTime<Utc>,
) -> Vec<ResumeEvidence> {
    let mut evidence = vec![ResumeEvidence::new(
        "background_probe",
        operator_safe_text(&format!(
            "{}: {}",
            format!("{:?}", decision.phase).to_lowercase(),
            decision.evidence
        )),
        now,
    )];

    if let Some(path) = probe.log_artifact_path.as_deref() {
        evidence.push(ResumeEvidence::artifact(
            "log_artifact",
            operator_safe_text(path),
            probe.log_artifact_present,
            now,
        ));
    }
    if let Some(path) = probe.result_artifact_path.as_deref() {
        evidence.push(ResumeEvidence::artifact(
            "result_artifact",
            operator_safe_text(path),
            probe.result_artifact_present,
            now,
        ));
    }
    if let Some(tail) = decision.last_log_tail.as_deref() {
        evidence.push(ResumeEvidence::new(
            "log_tail",
            operator_safe_text(tail),
            now,
        ));
    }

    evidence
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{BackgroundProbe, BackgroundRunnerKind, ExecutionBrief, OffdeskTaskInput};
    use tempfile::tempdir;

    fn task_input(now: DateTime<Utc>, command: &str) -> OffdeskTaskInput {
        OffdeskTaskInput {
            task_id: Some("task".to_string()),
            request_id: "request".to_string(),
            project_key: "project".to_string(),
            capability_id: "dispatch.runtime".to_string(),
            runner_kind: BackgroundRunnerKind::LocalBackground,
            command: command.to_string(),
            workdir: "/tmp".to_string(),
            execution_brief: Some(ExecutionBrief {
                request_id: "request".to_string(),
                task_id: "task".to_string(),
                project_key: "project".to_string(),
                approved: true,
                allowed_runtime_mutations: vec!["dispatch.runtime".to_string()],
                allowed_canonical_mutations: vec![],
                fresh_until: Some(now + Duration::minutes(10)),
            }),
            not_before: None,
            mutation_class: None,
            preview: String::new(),
            reason: String::new(),
            log_artifact_path: None,
            result_artifact_path: None,
        }
    }

    #[test]
    fn tick_marks_stale_background_as_resume_pending() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let store = OffdeskTaskStore::new(temp.path());
        let mut task = OffdeskTask::new(task_input(now, "true"), now);
        task.status = OffdeskTaskStatus::Running;
        task.background_ticket_id = Some("ticket".to_string());
        store.enqueue(task)?;

        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.runtime_handle_alive = false;
        BackgroundRunStore::new(temp.path()).upsert(probe)?;

        let report = run_offdesk_tick(temp.path(), OffdeskTickOptions::new(now))?;

        assert_eq!(report.resume_pending, 1);
        assert_eq!(store.load()?[0].status, OffdeskTaskStatus::ResumePending);
        assert_eq!(
            TaskResumeStore::new(temp.path()).load()?[0].status,
            crate::offdesk::ResumeStatus::ResumePending
        );
        Ok(())
    }
}
