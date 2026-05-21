//! Durable offdesk task queue.

use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

use super::adaptive_wiki::AdaptiveWikiAgentMode;
use super::approval::ExecutionBrief;
use super::background::BackgroundRunnerKind;
use super::capability::CapabilityArtifactRef;
use super::mode_contract::{assess_offdesk_mode, OffdeskModeAssessment, OffdeskModeLifecycle};
use super::provider::{ProviderFallbackCandidate, ProviderFallbackRecommendation};
use super::redaction::operator_safe_text;
use super::resume::TaskResumeStore;
use super::scheduler::SchedulerGateStatus;

const TASKS_FILE: &str = "offdesk_tasks.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskTaskStatus {
    Queued,
    PendingApproval,
    Launched,
    Running,
    Completed,
    Failed,
    ResumePending,
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OffdeskTask {
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub status: OffdeskTaskStatus,
    pub capability_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub command: String,
    pub workdir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_brief: Option<ExecutionBrief>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background_ticket_id: Option<String>,
    #[serde(default)]
    pub attempt_count: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_gate_status: Option<SchedulerGateStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mutation_class: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifact_refs: Vec<CapabilityArtifactRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_mode: Option<AdaptiveWikiAgentMode>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_provider_fallback: Option<ProviderFallbackRecommendation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub last_adaptive_wiki_entry_ids: Vec<String>,
    #[serde(default)]
    pub preview: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_artifact_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result_artifact_path: Option<String>,
}

impl OffdeskTask {
    pub fn new(input: OffdeskTaskInput, now: DateTime<Utc>) -> Self {
        let task_id = input
            .task_id
            .unwrap_or_else(|| format!("task_{}", Uuid::new_v4()));
        Self {
            task_id,
            request_id: input.request_id,
            project_key: input.project_key,
            status: OffdeskTaskStatus::Queued,
            capability_id: input.capability_id,
            runner_kind: input.runner_kind,
            command: input.command,
            workdir: input.workdir,
            execution_brief: input.execution_brief,
            background_ticket_id: None,
            attempt_count: 0,
            last_gate_status: None,
            last_error: None,
            created_at: now,
            updated_at: now,
            not_before: input.not_before,
            mutation_class: input.mutation_class,
            artifact_refs: input.artifact_refs,
            artifact_kind: input.artifact_kind,
            agent_mode: input.agent_mode,
            provider_id: input.provider_id,
            model: input.model,
            last_provider_fallback: None,
            last_adaptive_wiki_entry_ids: Vec::new(),
            preview: input.preview,
            reason: input.reason,
            log_artifact_path: input.log_artifact_path,
            result_artifact_path: input.result_artifact_path,
        }
    }

    pub fn is_due_at(&self, now: DateTime<Utc>) -> bool {
        self.not_before.map_or(true, |not_before| not_before <= now)
    }

    pub fn can_dispatch_at(&self, now: DateTime<Utc>) -> bool {
        matches!(
            self.status,
            OffdeskTaskStatus::Queued | OffdeskTaskStatus::PendingApproval
        ) && self.is_due_at(now)
    }

    pub fn operator_view(&self) -> OffdeskTaskView {
        OffdeskTaskView {
            task_id: self.task_id.clone(),
            request_id: self.request_id.clone(),
            project_key: self.project_key.clone(),
            status: self.status,
            capability_id: self.capability_id.clone(),
            runner_kind: self.runner_kind,
            command: operator_safe_text(&self.command),
            workdir: self.workdir.clone(),
            background_ticket_id: self.background_ticket_id.clone(),
            attempt_count: self.attempt_count,
            last_gate_status: self.last_gate_status,
            last_error: self.last_error.as_deref().map(operator_safe_text),
            created_at: self.created_at,
            updated_at: self.updated_at,
            not_before: self.not_before,
            mutation_class: self.mutation_class.clone(),
            artifact_refs: self
                .artifact_refs
                .iter()
                .map(operator_safe_artifact_ref)
                .collect(),
            artifact_kind: self.artifact_kind.as_deref().map(operator_safe_text),
            agent_mode: self.agent_mode,
            mode_assessment: assess_offdesk_mode(
                self.agent_mode,
                task_mode_lifecycle(self.status, self.result_artifact_path.as_deref()),
            ),
            provider_id: self.provider_id.as_deref().map(operator_safe_text),
            model: self.model.as_deref().map(operator_safe_text),
            last_provider_fallback: self
                .last_provider_fallback
                .as_ref()
                .map(operator_safe_provider_fallback),
            last_adaptive_wiki_entry_ids: self
                .last_adaptive_wiki_entry_ids
                .iter()
                .map(|entry_id| operator_safe_text(entry_id))
                .collect(),
            preview: operator_safe_text(&self.preview),
            reason: operator_safe_text(&self.reason),
            log_artifact_path: self.log_artifact_path.clone(),
            result_artifact_path: self.result_artifact_path.clone(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OffdeskTaskInput {
    pub task_id: Option<String>,
    pub request_id: String,
    pub project_key: String,
    pub capability_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub command: String,
    pub workdir: String,
    pub execution_brief: Option<ExecutionBrief>,
    pub not_before: Option<DateTime<Utc>>,
    pub mutation_class: Option<String>,
    pub artifact_refs: Vec<CapabilityArtifactRef>,
    pub artifact_kind: Option<String>,
    pub agent_mode: Option<AdaptiveWikiAgentMode>,
    pub provider_id: Option<String>,
    pub model: Option<String>,
    pub preview: String,
    pub reason: String,
    pub log_artifact_path: Option<String>,
    pub result_artifact_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskTaskView {
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub status: OffdeskTaskStatus,
    pub capability_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub command: String,
    pub workdir: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background_ticket_id: Option<String>,
    pub attempt_count: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_gate_status: Option<SchedulerGateStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error: Option<String>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub not_before: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mutation_class: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub artifact_refs: Vec<CapabilityArtifactRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_mode: Option<AdaptiveWikiAgentMode>,
    #[serde(flatten)]
    pub mode_assessment: OffdeskModeAssessment,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_provider_fallback: Option<ProviderFallbackRecommendation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub last_adaptive_wiki_entry_ids: Vec<String>,
    pub preview: String,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_artifact_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result_artifact_path: Option<String>,
}

fn task_mode_lifecycle(
    status: OffdeskTaskStatus,
    result_artifact_path: Option<&str>,
) -> OffdeskModeLifecycle {
    match status {
        OffdeskTaskStatus::Queued | OffdeskTaskStatus::PendingApproval => {
            OffdeskModeLifecycle::Pending
        }
        OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running => OffdeskModeLifecycle::Running,
        OffdeskTaskStatus::Completed if result_artifact_path.is_some() => {
            OffdeskModeLifecycle::CompletedWithResult
        }
        OffdeskTaskStatus::Completed => OffdeskModeLifecycle::CompletedWithoutResult,
        OffdeskTaskStatus::Failed | OffdeskTaskStatus::ResumePending => {
            OffdeskModeLifecycle::Blocked
        }
        OffdeskTaskStatus::Cancelled => OffdeskModeLifecycle::Cancelled,
    }
}

fn operator_safe_artifact_ref(artifact_ref: &CapabilityArtifactRef) -> CapabilityArtifactRef {
    CapabilityArtifactRef {
        artifact_id: operator_safe_text(&artifact_ref.artifact_id),
        path: artifact_ref.path.as_deref().map(operator_safe_text),
        present: artifact_ref.present,
    }
}

fn operator_safe_provider_fallback(
    recommendation: &ProviderFallbackRecommendation,
) -> ProviderFallbackRecommendation {
    ProviderFallbackRecommendation {
        current_provider_id: operator_safe_text(&recommendation.current_provider_id),
        current_model: recommendation
            .current_model
            .as_deref()
            .map(operator_safe_text),
        trigger_reason: operator_safe_text(&recommendation.trigger_reason),
        generated_at: recommendation.generated_at,
        candidates: recommendation
            .candidates
            .iter()
            .map(operator_safe_provider_fallback_candidate)
            .collect(),
    }
}

fn operator_safe_provider_fallback_candidate(
    candidate: &ProviderFallbackCandidate,
) -> ProviderFallbackCandidate {
    ProviderFallbackCandidate {
        provider_id: operator_safe_text(&candidate.provider_id),
        model: candidate.model.as_deref().map(operator_safe_text),
        source: candidate.source,
        auth_status: candidate.auth_status,
        capacity_status: candidate.capacity_status,
        recommended: candidate.recommended,
        reason: operator_safe_text(&candidate.reason),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskTaskLifecycleAction {
    Cancel,
    Retry,
    Resume,
    Abandon,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskTaskLifecycleReport {
    pub task: OffdeskTaskView,
    pub action: OffdeskTaskLifecycleAction,
    pub changed: bool,
    pub previous_status: OffdeskTaskStatus,
    pub status: OffdeskTaskStatus,
    pub message: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct OffdeskTaskCounts {
    pub queued: usize,
    pub pending_approval: usize,
    pub active: usize,
    pub completed: usize,
    pub failed: usize,
    pub resume_pending: usize,
    pub cancelled: usize,
}

#[derive(Debug, Clone)]
pub struct OffdeskTaskStore {
    root: PathBuf,
}

impl OffdeskTaskStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn path(&self) -> PathBuf {
        self.root.join(TASKS_FILE)
    }

    pub fn load(&self) -> Result<Vec<OffdeskTask>> {
        read_tasks(&self.path())
    }

    pub fn save(&self, tasks: &[OffdeskTask]) -> Result<()> {
        write_tasks(&self.path(), tasks)
    }

    pub fn enqueue(&self, task: OffdeskTask) -> Result<()> {
        let mut tasks = self.load()?;
        if let Some(existing) = tasks
            .iter_mut()
            .find(|existing| existing.task_id == task.task_id)
        {
            *existing = task;
        } else {
            tasks.push(task);
        }
        self.save(&tasks)
    }

    pub fn counts(&self) -> Result<OffdeskTaskCounts> {
        Ok(count_tasks(&self.load()?))
    }

    pub fn cancel_task(
        &self,
        task_id: &str,
        reason: Option<&str>,
        now: DateTime<Utc>,
    ) -> Result<OffdeskTaskLifecycleReport> {
        let mut tasks = self.load()?;
        let index = find_task_index(&tasks, task_id)?;
        let previous_status = tasks[index].status;

        let changed = match previous_status {
            OffdeskTaskStatus::Cancelled => false,
            OffdeskTaskStatus::Completed => false,
            _ => {
                let task = &mut tasks[index];
                task.status = OffdeskTaskStatus::Cancelled;
                task.updated_at = now;
                if let Some(reason) = reason.map(str::trim).filter(|reason| !reason.is_empty()) {
                    task.reason = reason.to_string();
                }
                true
            }
        };

        if changed {
            self.save(&tasks)?;
            let task = &tasks[index];
            TaskResumeStore::new(&self.root).cancel(&task.project_key, &task.task_id, now)?;
        }

        let message = match (previous_status, changed) {
            (OffdeskTaskStatus::Cancelled, false) => "task is already cancelled",
            (OffdeskTaskStatus::Completed, false) => "completed tasks cannot be cancelled",
            _ => "task marked cancelled; background runner was left untouched",
        };
        Ok(lifecycle_report(
            &tasks[index],
            OffdeskTaskLifecycleAction::Cancel,
            changed,
            previous_status,
            message,
        ))
    }

    pub fn retry_task(
        &self,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<OffdeskTaskLifecycleReport> {
        let mut tasks = self.load()?;
        let index = find_task_index(&tasks, task_id)?;
        let previous_status = tasks[index].status;

        let changed = match previous_status {
            OffdeskTaskStatus::Failed
            | OffdeskTaskStatus::ResumePending
            | OffdeskTaskStatus::Cancelled => {
                requeue_task(&mut tasks[index], now);
                true
            }
            _ => false,
        };

        if changed {
            self.save(&tasks)?;
            if previous_status == OffdeskTaskStatus::ResumePending {
                let task = &tasks[index];
                TaskResumeStore::new(&self.root).clear_after_recovery(
                    &task.project_key,
                    &task.task_id,
                    now,
                )?;
            }
        }

        let message = match (previous_status, changed) {
            (_, true) => "task requeued; next tick will create a fresh background ticket",
            (OffdeskTaskStatus::Queued, false) => "task is already queued",
            (OffdeskTaskStatus::Completed, false) => "completed tasks cannot be retried",
            (OffdeskTaskStatus::PendingApproval, false) => {
                "task is pending approval; resolve or cancel the approval path first"
            }
            (OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running, false) => {
                "active tasks cannot be retried without cancellation or recovery"
            }
            _ => "task cannot be retried from its current status",
        };
        Ok(lifecycle_report(
            &tasks[index],
            OffdeskTaskLifecycleAction::Retry,
            changed,
            previous_status,
            message,
        ))
    }

    pub fn resume_task(
        &self,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<OffdeskTaskLifecycleReport> {
        let mut tasks = self.load()?;
        let index = find_task_index(&tasks, task_id)?;
        let previous_status = tasks[index].status;

        let changed = if previous_status == OffdeskTaskStatus::ResumePending {
            requeue_task(&mut tasks[index], now);
            true
        } else {
            false
        };

        if changed {
            self.save(&tasks)?;
            let task = &tasks[index];
            TaskResumeStore::new(&self.root).clear_after_recovery(
                &task.project_key,
                &task.task_id,
                now,
            )?;
        }

        let message = if changed {
            "resume accepted; task requeued and resume artifact marked resumed"
        } else if previous_status == OffdeskTaskStatus::Queued {
            "task is already queued"
        } else {
            "only resume_pending tasks can be resumed"
        };
        Ok(lifecycle_report(
            &tasks[index],
            OffdeskTaskLifecycleAction::Resume,
            changed,
            previous_status,
            message,
        ))
    }

    pub fn abandon_task(
        &self,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<OffdeskTaskLifecycleReport> {
        let mut tasks = self.load()?;
        let index = find_task_index(&tasks, task_id)?;
        let previous_status = tasks[index].status;

        let changed = match previous_status {
            OffdeskTaskStatus::ResumePending | OffdeskTaskStatus::Failed => {
                let task = &mut tasks[index];
                task.status = OffdeskTaskStatus::Cancelled;
                task.updated_at = now;
                true
            }
            _ => false,
        };

        if changed {
            self.save(&tasks)?;
            let task = &tasks[index];
            TaskResumeStore::new(&self.root).abandon(&task.project_key, &task.task_id, now)?;
        }

        let message = match (previous_status, changed) {
            (_, true) => "task abandoned and marked cancelled",
            (OffdeskTaskStatus::Cancelled, false) => "task is already cancelled",
            (OffdeskTaskStatus::Completed, false) => "completed tasks cannot be abandoned",
            _ => "only failed or resume_pending tasks can be abandoned",
        };
        Ok(lifecycle_report(
            &tasks[index],
            OffdeskTaskLifecycleAction::Abandon,
            changed,
            previous_status,
            message,
        ))
    }
}

pub fn count_tasks(tasks: &[OffdeskTask]) -> OffdeskTaskCounts {
    let mut counts = OffdeskTaskCounts::default();
    for task in tasks {
        match task.status {
            OffdeskTaskStatus::Queued => counts.queued += 1,
            OffdeskTaskStatus::PendingApproval => counts.pending_approval += 1,
            OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running => counts.active += 1,
            OffdeskTaskStatus::Completed => counts.completed += 1,
            OffdeskTaskStatus::Failed => counts.failed += 1,
            OffdeskTaskStatus::ResumePending => counts.resume_pending += 1,
            OffdeskTaskStatus::Cancelled => counts.cancelled += 1,
        }
    }
    counts
}

fn read_tasks(path: &Path) -> Result<Vec<OffdeskTask>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }
    Ok(serde_json::from_str(&content)?)
}

fn write_tasks(path: &Path, tasks: &[OffdeskTask]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(tasks)?)?;
    Ok(())
}

fn find_task_index(tasks: &[OffdeskTask], task_id: &str) -> Result<usize> {
    tasks
        .iter()
        .position(|task| task.task_id == task_id)
        .ok_or_else(|| anyhow::anyhow!("offdesk task not found: {task_id}"))
}

fn requeue_task(task: &mut OffdeskTask, now: DateTime<Utc>) {
    task.status = OffdeskTaskStatus::Queued;
    task.background_ticket_id = None;
    task.last_gate_status = None;
    task.last_error = None;
    task.last_provider_fallback = None;
    task.updated_at = now;
}

fn lifecycle_report(
    task: &OffdeskTask,
    action: OffdeskTaskLifecycleAction,
    changed: bool,
    previous_status: OffdeskTaskStatus,
    message: &str,
) -> OffdeskTaskLifecycleReport {
    OffdeskTaskLifecycleReport {
        task: task.operator_view(),
        action,
        changed,
        previous_status,
        status: task.status,
        message: operator_safe_text(message),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{
        OffdeskModeRisk, OffdeskModeVerdict, ResumePendingInput, ResumeStatus, TaskResumeState,
        TaskResumeStore,
    };
    use chrono::Duration;
    use tempfile::tempdir;

    fn input() -> OffdeskTaskInput {
        OffdeskTaskInput {
            task_id: Some("task".to_string()),
            request_id: "request".to_string(),
            project_key: "project".to_string(),
            capability_id: "dispatch.runtime".to_string(),
            runner_kind: BackgroundRunnerKind::LocalBackground,
            command: "token=sk-secretsecretsecretsecret".to_string(),
            workdir: "/tmp".to_string(),
            execution_brief: None,
            not_before: None,
            mutation_class: None,
            artifact_refs: Vec::new(),
            artifact_kind: None,
            agent_mode: None,
            provider_id: None,
            model: None,
            preview: "preview".to_string(),
            reason: "reason".to_string(),
            log_artifact_path: None,
            result_artifact_path: None,
        }
    }

    #[test]
    fn queue_roundtrips_task() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let task = OffdeskTask::new(input(), Utc::now());

        store.enqueue(task.clone())?;

        assert_eq!(store.load()?, vec![task]);
        Ok(())
    }

    #[test]
    fn legacy_task_without_optional_dispatch_fields_still_loads() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let now = Utc::now();
        fs::create_dir_all(temp.path())?;
        fs::write(
            store.path(),
            serde_json::to_string_pretty(&serde_json::json!([
                {
                    "task_id": "task",
                    "request_id": "request",
                    "project_key": "project",
                    "status": "queued",
                    "capability_id": "dispatch.runtime",
                    "runner_kind": "local_background",
                    "command": "true",
                    "workdir": "/tmp",
                    "created_at": now,
                    "updated_at": now
                }
            ]))?,
        )?;

        let task = store.load()?.remove(0);

        assert_eq!(task.attempt_count, 0);
        assert!(task.execution_brief.is_none());
        assert!(task.background_ticket_id.is_none());
        assert!(task.last_gate_status.is_none());
        assert!(task.last_error.is_none());
        assert!(task.not_before.is_none());
        assert!(task.mutation_class.is_none());
        assert!(task.artifact_refs.is_empty());
        assert!(task.provider_id.is_none());
        assert!(task.model.is_none());
        assert!(task.last_provider_fallback.is_none());
        assert!(task.preview.is_empty());
        assert!(task.reason.is_empty());
        assert!(task.log_artifact_path.is_none());
        assert!(task.result_artifact_path.is_none());
        Ok(())
    }

    #[test]
    fn operator_view_redacts_command() {
        let task = OffdeskTask::new(input(), Utc::now());
        let view = task.operator_view();

        assert!(!view.command.contains("sk-secret"));
    }

    #[test]
    fn operator_view_surfaces_mode_assessment_without_persisting_new_fields() {
        let mut input = input();
        input.agent_mode = Some(AdaptiveWikiAgentMode::Development);
        input.result_artifact_path = Some("/tmp/result.json".to_string());
        let mut task = OffdeskTask::new(input, Utc::now());
        task.status = OffdeskTaskStatus::Completed;

        let view = task.operator_view();

        assert_eq!(
            view.mode_assessment.mode_verdict,
            OffdeskModeVerdict::EvidenceReady
        );
        assert_eq!(
            view.mode_assessment.mode_risk,
            OffdeskModeRisk::OperatorReviewRequired
        );
        assert!(view.mode_assessment.review_stage_required);
    }

    fn resume_state(now: DateTime<Utc>) -> TaskResumeState {
        TaskResumeState::mark_pending(ResumePendingInput {
            task_id: "task".to_string(),
            request_id: "request".to_string(),
            project_key: "project".to_string(),
            phase: "background".to_string(),
            runner_target: "local_background".to_string(),
            interruption_reason: "restart".to_string(),
            interrupted_at: now,
            fresh_until: now + Duration::minutes(10),
        })
    }

    #[test]
    fn cancel_queued_task_marks_cancelled() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let now = Utc::now();
        store.enqueue(OffdeskTask::new(input(), now))?;

        let report =
            store.cancel_task("task", Some("operator stop"), now + Duration::seconds(1))?;

        assert!(report.changed);
        assert_eq!(report.previous_status, OffdeskTaskStatus::Queued);
        assert_eq!(report.status, OffdeskTaskStatus::Cancelled);
        assert_eq!(store.load()?[0].status, OffdeskTaskStatus::Cancelled);
        Ok(())
    }

    #[test]
    fn cancel_running_task_preserves_background_ticket() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let now = Utc::now();
        let mut task = OffdeskTask::new(input(), now);
        task.status = OffdeskTaskStatus::Running;
        task.background_ticket_id = Some("ticket".to_string());
        store.enqueue(task)?;

        let report = store.cancel_task("task", None, now + Duration::seconds(1))?;
        let updated = store.load()?.remove(0);

        assert!(report.changed);
        assert_eq!(updated.status, OffdeskTaskStatus::Cancelled);
        assert_eq!(updated.background_ticket_id.as_deref(), Some("ticket"));
        Ok(())
    }

    #[test]
    fn retry_failed_task_requeues_and_clears_dispatch_state() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let now = Utc::now();
        let mut task = OffdeskTask::new(input(), now);
        task.status = OffdeskTaskStatus::Failed;
        task.background_ticket_id = Some("ticket".to_string());
        task.attempt_count = 2;
        task.last_gate_status = Some(SchedulerGateStatus::Denied);
        task.last_error = Some("denied".to_string());
        task.last_provider_fallback = Some(ProviderFallbackRecommendation {
            current_provider_id: "openai".to_string(),
            current_model: Some("gpt-4.1".to_string()),
            trigger_reason: "provider capacity cooldown active".to_string(),
            generated_at: now,
            candidates: Vec::new(),
        });
        store.enqueue(task)?;

        let report = store.retry_task("task", now + Duration::seconds(1))?;
        let updated = store.load()?.remove(0);

        assert!(report.changed);
        assert_eq!(updated.status, OffdeskTaskStatus::Queued);
        assert_eq!(updated.attempt_count, 2);
        assert!(updated.background_ticket_id.is_none());
        assert!(updated.last_gate_status.is_none());
        assert!(updated.last_error.is_none());
        assert!(updated.last_provider_fallback.is_none());
        Ok(())
    }

    #[test]
    fn resume_pending_resume_requeues_and_marks_resume_artifact_resumed() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let resume_store = TaskResumeStore::new(temp.path());
        let now = Utc::now();
        let mut task = OffdeskTask::new(input(), now);
        task.status = OffdeskTaskStatus::ResumePending;
        task.background_ticket_id = Some("ticket".to_string());
        task.last_error = Some("stale".to_string());
        store.enqueue(task)?;
        resume_store.mark_resume_pending(resume_state(now))?;

        let report = store.resume_task("task", now + Duration::seconds(1))?;
        let updated = store.load()?.remove(0);
        let resume = resume_store.load()?.remove(0);

        assert!(report.changed);
        assert_eq!(updated.status, OffdeskTaskStatus::Queued);
        assert!(updated.background_ticket_id.is_none());
        assert!(updated.last_error.is_none());
        assert_eq!(resume.status, ResumeStatus::Resumed);
        Ok(())
    }

    #[test]
    fn resume_pending_abandon_cancels_task_and_marks_resume_artifact_abandoned() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let resume_store = TaskResumeStore::new(temp.path());
        let now = Utc::now();
        let mut task = OffdeskTask::new(input(), now);
        task.status = OffdeskTaskStatus::ResumePending;
        store.enqueue(task)?;
        resume_store.mark_resume_pending(resume_state(now))?;

        let report = store.abandon_task("task", now + Duration::seconds(1))?;
        let updated = store.load()?.remove(0);
        let resume = resume_store.load()?.remove(0);

        assert!(report.changed);
        assert_eq!(updated.status, OffdeskTaskStatus::Cancelled);
        assert_eq!(resume.status, ResumeStatus::Abandoned);
        Ok(())
    }

    #[test]
    fn completed_task_lifecycle_commands_are_no_ops() -> Result<()> {
        let temp = tempdir()?;
        let store = OffdeskTaskStore::new(temp.path());
        let now = Utc::now();
        let mut task = OffdeskTask::new(input(), now);
        task.status = OffdeskTaskStatus::Completed;
        store.enqueue(task)?;

        let cancel = store.cancel_task("task", None, now + Duration::seconds(1))?;
        let retry = store.retry_task("task", now + Duration::seconds(2))?;
        let updated = store.load()?.remove(0);

        assert!(!cancel.changed);
        assert!(!retry.changed);
        assert_eq!(updated.status, OffdeskTaskStatus::Completed);
        Ok(())
    }
}
