//! Task-centric resume state for interrupted offdesk work.

use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

const RESUME_FILE: &str = "task_resume_state.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResumeStatus {
    Clean,
    ResumePending,
    Resumed,
    Abandoned,
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskResumeState {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub resume_id: String,
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub status: ResumeStatus,
    pub phase: String,
    pub runner_target: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background_ticket_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_task_status: Option<String>,
    #[serde(default, skip_serializing_if = "is_zero")]
    pub attempt_count: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_durable_action: Option<String>,
    #[serde(default)]
    pub last_evidence_artifacts: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<ResumeEvidence>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_log_tail: Option<String>,
    pub next_safe_resume_step: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interrupted_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interruption_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fresh_until: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cleared_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResumeEvidence {
    pub kind: String,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub present: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observed_at: Option<DateTime<Utc>>,
}

impl ResumeEvidence {
    pub fn new(
        kind: impl Into<String>,
        summary: impl Into<String>,
        observed_at: DateTime<Utc>,
    ) -> Self {
        Self {
            kind: kind.into(),
            summary: summary.into(),
            path: None,
            present: None,
            observed_at: Some(observed_at),
        }
    }

    pub fn artifact(
        kind: impl Into<String>,
        path: impl Into<String>,
        present: bool,
        observed_at: DateTime<Utc>,
    ) -> Self {
        let kind = kind.into();
        let present_label = if present { "present" } else { "missing" };
        Self {
            summary: format!("{kind} {present_label}"),
            kind,
            path: Some(path.into()),
            present: Some(present),
            observed_at: Some(observed_at),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResumePendingInput {
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub phase: String,
    pub runner_target: String,
    pub interruption_reason: String,
    pub interrupted_at: DateTime<Utc>,
    pub fresh_until: DateTime<Utc>,
}

impl TaskResumeState {
    pub fn mark_pending(input: ResumePendingInput) -> Self {
        Self {
            resume_id: format!("resume_{}", Uuid::new_v4()),
            task_id: input.task_id,
            request_id: input.request_id,
            project_key: input.project_key,
            status: ResumeStatus::ResumePending,
            phase: input.phase,
            runner_target: input.runner_target,
            background_ticket_id: None,
            last_task_status: None,
            attempt_count: 0,
            last_durable_action: None,
            last_evidence_artifacts: Vec::new(),
            evidence: Vec::new(),
            last_log_tail: None,
            next_safe_resume_step: "recover from last durable action before mutating".to_string(),
            interrupted_at: Some(input.interrupted_at),
            interruption_reason: Some(input.interruption_reason),
            fresh_until: Some(input.fresh_until),
            cleared_at: None,
        }
    }

    pub fn resume_id(&self) -> String {
        if self.resume_id.is_empty() {
            format!("{}:{}", self.project_key, self.task_id)
        } else {
            self.resume_id.clone()
        }
    }

    pub fn is_fresh_at(&self, now: DateTime<Utc>) -> bool {
        self.status == ResumeStatus::ResumePending && self.fresh_until.is_some_and(|t| t >= now)
    }

    pub fn mark_resumed(&mut self, now: DateTime<Utc>) {
        self.status = ResumeStatus::Resumed;
        self.cleared_at = Some(now);
    }

    pub fn cancel(&mut self, now: DateTime<Utc>) {
        self.status = ResumeStatus::Cancelled;
        self.cleared_at = Some(now);
    }

    pub fn abandon(&mut self, now: DateTime<Utc>) {
        self.status = ResumeStatus::Abandoned;
        self.cleared_at = Some(now);
    }

    pub fn operator_status(&self, now: DateTime<Utc>) -> String {
        let freshness = if self.is_fresh_at(now) {
            "fresh"
        } else if self.status == ResumeStatus::ResumePending {
            "stale"
        } else {
            "clear"
        };
        let status = match self.status {
            ResumeStatus::Clean => "clean",
            ResumeStatus::ResumePending => "resume_pending",
            ResumeStatus::Resumed => "resumed",
            ResumeStatus::Abandoned => "abandoned",
            ResumeStatus::Cancelled => "cancelled",
        };
        format!(
            "{}:{}:{}:{}",
            self.project_key, self.task_id, status, freshness
        )
    }
}

fn is_zero(value: &u32) -> bool {
    *value == 0
}

#[derive(Debug, Clone)]
pub struct TaskResumeStore {
    root: PathBuf,
}

impl TaskResumeStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn path(&self) -> PathBuf {
        self.root.join(RESUME_FILE)
    }

    pub fn load(&self) -> Result<Vec<TaskResumeState>> {
        read_resume_states(&self.path())
    }

    pub fn save(&self, states: &[TaskResumeState]) -> Result<()> {
        write_resume_states(&self.path(), states)
    }

    pub fn upsert(&self, state: TaskResumeState) -> Result<()> {
        let mut states = self.load()?;
        if let Some(existing) = states.iter_mut().find(|existing| {
            existing.project_key == state.project_key && existing.task_id == state.task_id
        }) {
            *existing = state;
        } else {
            states.push(state);
        }
        self.save(&states)
    }

    pub fn mark_resume_pending(&self, state: TaskResumeState) -> Result<()> {
        self.upsert(state)
    }

    pub fn clear_after_recovery(
        &self,
        project_key: &str,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<Option<TaskResumeState>> {
        self.transition(project_key, task_id, now, ResumeStatus::Resumed)
    }

    pub fn cancel(
        &self,
        project_key: &str,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<Option<TaskResumeState>> {
        self.transition(project_key, task_id, now, ResumeStatus::Cancelled)
    }

    pub fn abandon(
        &self,
        project_key: &str,
        task_id: &str,
        now: DateTime<Utc>,
    ) -> Result<Option<TaskResumeState>> {
        self.transition(project_key, task_id, now, ResumeStatus::Abandoned)
    }

    pub fn fresh_pending(&self, now: DateTime<Utc>) -> Result<Vec<TaskResumeState>> {
        Ok(self
            .load()?
            .into_iter()
            .filter(|state| state.is_fresh_at(now))
            .collect())
    }

    fn transition(
        &self,
        project_key: &str,
        task_id: &str,
        now: DateTime<Utc>,
        status: ResumeStatus,
    ) -> Result<Option<TaskResumeState>> {
        let mut states = self.load()?;
        let Some(state) = states
            .iter_mut()
            .find(|state| state.project_key == project_key && state.task_id == task_id)
        else {
            return Ok(None);
        };

        state.status = status;
        state.cleared_at = Some(now);
        let updated = state.clone();
        self.save(&states)?;
        Ok(Some(updated))
    }
}

fn read_resume_states(path: &Path) -> Result<Vec<TaskResumeState>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }
    Ok(serde_json::from_str(&content)?)
}

fn write_resume_states(path: &Path, states: &[TaskResumeState]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(states)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;
    use tempfile::tempdir;

    fn pending_input(now: DateTime<Utc>, fresh_until: DateTime<Utc>) -> ResumePendingInput {
        ResumePendingInput {
            task_id: "task".to_string(),
            request_id: "request".to_string(),
            project_key: "project".to_string(),
            phase: "background".to_string(),
            runner_target: "local_tmux".to_string(),
            interruption_reason: "gateway shutdown".to_string(),
            interrupted_at: now,
            fresh_until,
        }
    }

    #[test]
    fn interrupted_task_creates_fresh_resume_pending_state() -> Result<()> {
        let temp = tempdir()?;
        let store = TaskResumeStore::new(temp.path());
        let now = Utc::now();
        let mut state =
            TaskResumeState::mark_pending(pending_input(now, now + Duration::minutes(10)));
        assert!(state.resume_id.starts_with("resume_"));
        state.background_ticket_id = Some("ticket-1".to_string());
        store.mark_resume_pending(state)?;

        let fresh = store.fresh_pending(now + Duration::minutes(5))?;
        assert_eq!(fresh.len(), 1);
        assert_eq!(fresh[0].status, ResumeStatus::ResumePending);
        Ok(())
    }

    #[test]
    fn legacy_resume_state_without_resume_id_still_loads() -> Result<()> {
        let temp = tempdir()?;
        let store = TaskResumeStore::new(temp.path());
        let now = Utc::now();
        fs::create_dir_all(temp.path())?;
        fs::write(
            store.path(),
            serde_json::to_string_pretty(&serde_json::json!([
                {
                    "task_id": "task",
                    "request_id": "request",
                    "project_key": "project",
                    "status": "resume_pending",
                    "phase": "background",
                    "runner_target": "local_background",
                    "last_evidence_artifacts": [],
                    "next_safe_resume_step": "inspect result sidecar",
                    "interrupted_at": now,
                    "interruption_reason": "restart",
                    "fresh_until": now + Duration::minutes(10)
                }
            ]))?,
        )?;

        let states = store.load()?;
        assert_eq!(states.len(), 1);
        assert!(states[0].resume_id.is_empty());
        assert_eq!(states[0].resume_id(), "project:task");
        assert!(states[0].evidence.is_empty());
        assert_eq!(states[0].attempt_count, 0);
        Ok(())
    }

    #[test]
    fn successful_recovery_clears_pending_state() -> Result<()> {
        let temp = tempdir()?;
        let store = TaskResumeStore::new(temp.path());
        let now = Utc::now();
        store.mark_resume_pending(TaskResumeState::mark_pending(pending_input(
            now,
            now + Duration::minutes(10),
        )))?;

        let cleared = store
            .clear_after_recovery("project", "task", now + Duration::minutes(1))?
            .expect("cleared state");

        assert_eq!(cleared.status, ResumeStatus::Resumed);
        assert!(store.fresh_pending(now + Duration::minutes(2))?.is_empty());
        Ok(())
    }

    #[test]
    fn stale_resume_state_does_not_count_as_fresh() {
        let now = Utc::now();
        let state = TaskResumeState::mark_pending(pending_input(now, now + Duration::seconds(1)));
        assert!(!state.is_fresh_at(now + Duration::seconds(2)));
    }
}
