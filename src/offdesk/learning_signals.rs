//! Event-driven learning signals.
//!
//! This translates Offdesk transition events into adaptive-wiki *candidates*.
//! It is the Forager-owned adaptation of the Hermes "memory lifecycle hooks"
//! pattern: task/approval/resume events feed learning candidates, but nothing
//! here promotes knowledge or mutates runtime behavior. Candidates only become
//! reusable knowledge through the existing reviewed promotion path.
//!
//! Idempotency is durable: a per-profile cursor (`learning_signals_state.json`)
//! records which event instances have already emitted a candidate, so the scan
//! can run every tick and emit each denial/failure/resume exactly once. The
//! candidate store still merges by claim, so repeated patterns (e.g. the same
//! action denied twice) accrue `occurrence_count` while each distinct event is
//! counted once.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use super::adaptive_wiki::{
    AdaptiveWikiCandidateInput, AdaptiveWikiConfidence, AdaptiveWikiKind, AdaptiveWikiOrigin,
    AdaptiveWikiScope, AdaptiveWikiSignalKind, AdaptiveWikiStore,
};
use super::approval::{ApprovalLedger, ApprovalStatus, PendingActionApproval};
use super::redaction::operator_safe_text;
use super::resume::{ResumeStatus, TaskResumeState, TaskResumeStore};
use super::task_queue::{OffdeskTask, OffdeskTaskStatus, OffdeskTaskStore};

pub const LEARNING_SIGNALS_FILE: &str = "learning_signals_state.json";
pub const LEARNING_SIGNALS_SCHEMA: &str = "offdesk_learning_signals.v1";

fn default_schema() -> String {
    LEARNING_SIGNALS_SCHEMA.to_string()
}

/// The Offdesk event that produced a learning candidate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LearningSignalSource {
    ApprovalDenied,
    TaskFailed,
    ResumePending,
}

impl LearningSignalSource {
    pub fn as_str(self) -> &'static str {
        match self {
            LearningSignalSource::ApprovalDenied => "approval_denied",
            LearningSignalSource::TaskFailed => "task_failed",
            LearningSignalSource::ResumePending => "resume_pending",
        }
    }
}

/// Durable cursor of event keys that have already emitted a candidate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LearningSignalCursor {
    #[serde(default = "default_schema")]
    pub schema: String,
    #[serde(default)]
    pub processed: BTreeSet<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<DateTime<Utc>>,
}

impl Default for LearningSignalCursor {
    fn default() -> Self {
        Self {
            schema: default_schema(),
            processed: BTreeSet::new(),
            updated_at: None,
        }
    }
}

/// Reads and writes `learning_signals_state.json` in a profile directory.
pub struct LearningSignalStore {
    path: PathBuf,
}

impl LearningSignalStore {
    pub fn new(profile_dir: impl AsRef<Path>) -> Self {
        Self {
            path: profile_dir.as_ref().join(LEARNING_SIGNALS_FILE),
        }
    }

    pub fn load(&self) -> Result<LearningSignalCursor> {
        if !self.path.exists() {
            return Ok(LearningSignalCursor::default());
        }
        let raw = fs::read_to_string(&self.path)
            .with_context(|| format!("reading {}", self.path.display()))?;
        let cursor: LearningSignalCursor = serde_json::from_str(&raw)
            .with_context(|| format!("parsing {}", self.path.display()))?;
        Ok(cursor)
    }

    pub fn save(&self, cursor: &LearningSignalCursor) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).with_context(|| format!("creating {}", parent.display()))?;
        }
        let body = serde_json::to_string_pretty(cursor)?;
        fs::write(&self.path, format!("{body}\n"))
            .with_context(|| format!("writing {}", self.path.display()))?;
        Ok(())
    }
}

/// One candidate emitted by a scan.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EmittedLearningSignal {
    pub signal_key: String,
    pub source: LearningSignalSource,
    pub candidate_id: String,
    pub claim: String,
}

/// The result of one learning-signal scan.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LearningScanReport {
    pub emitted: Vec<EmittedLearningSignal>,
    pub skipped_already_processed: usize,
}

impl LearningScanReport {
    pub fn emitted_count(&self) -> usize {
        self.emitted.len()
    }
}

fn scope_for_project(project_key: &str) -> (AdaptiveWikiScope, String) {
    let project = project_key.trim();
    if project.is_empty() {
        (AdaptiveWikiScope::UserGlobal, String::new())
    } else {
        (AdaptiveWikiScope::Project, project.to_string())
    }
}

fn truncate_for_summary(text: &str, max_chars: usize) -> String {
    let redacted = operator_safe_text(text);
    let trimmed = redacted.trim();
    if trimmed.chars().count() <= max_chars {
        return trimmed.to_string();
    }
    let head: String = trimmed.chars().take(max_chars.saturating_sub(3)).collect();
    format!("{head}...")
}

/// Stable per-event cursor key: identifies the exact event instance so it emits
/// once. Distinct from the candidate claim, which is pattern-level and merges.
pub fn approval_denial_key(approval: &PendingActionApproval) -> String {
    format!(
        "{}:{}",
        LearningSignalSource::ApprovalDenied.as_str(),
        approval.approval_id
    )
}

pub fn task_failure_key(task: &OffdeskTask) -> String {
    // Include the attempt count so a later failed retry of the same task is a
    // new event rather than being suppressed by the first failure.
    format!(
        "{}:{}:{}",
        LearningSignalSource::TaskFailed.as_str(),
        task.task_id,
        task.attempt_count
    )
}

pub fn resume_pending_key(resume: &TaskResumeState) -> String {
    format!(
        "{}:{}",
        LearningSignalSource::ResumePending.as_str(),
        resume.resume_id()
    )
}

/// Build the learning candidate for a denied approval: an operator boundary the
/// runtime should respect on future attempts.
pub fn approval_denial_input(approval: &PendingActionApproval) -> AdaptiveWikiCandidateInput {
    let (scope, scope_ref) = scope_for_project(&approval.project_key);
    let action = operator_safe_text(approval.action.trim());
    let action = if action.is_empty() {
        "this runtime action".to_string()
    } else {
        action
    };
    let mut summary = format!("Denied approval {}", approval.approval_id);
    let reason = truncate_for_summary(&approval.reason, 160);
    if !reason.is_empty() {
        summary.push_str(&format!(": {reason}"));
    }
    summary.push('.');
    AdaptiveWikiCandidateInput {
        kind: AdaptiveWikiKind::PolicyRule,
        scope,
        scope_ref,
        claim: format!("Operator denied action \"{action}\" in this project."),
        suggested_ai_instruction: format!(
            "Do not retry \"{action}\" here without a fresh operator approval; it was previously denied."
        ),
        human_summary: summary,
        evidence_ref: Some(approval.approval_id.clone()),
        signal_kind: AdaptiveWikiSignalKind::ApprovalDenial,
        origin: AdaptiveWikiOrigin::RuntimeObserved,
        confidence: AdaptiveWikiConfidence::Inferred,
        review_reason: "Observed from an operator approval denial.".to_string(),
        ..AdaptiveWikiCandidateInput::default()
    }
}

/// Build the learning candidate for a failed runtime task: a failure pattern to
/// check before retrying similar work.
pub fn task_failure_input(task: &OffdeskTask) -> AdaptiveWikiCandidateInput {
    let (scope, scope_ref) = scope_for_project(&task.project_key);
    let runner = format!("{:?}", task.runner_kind).to_lowercase();
    let mut summary = format!(
        "Task {} failed (attempt {})",
        task.task_id, task.attempt_count
    );
    if let Some(error) = task.last_error.as_deref() {
        let error = truncate_for_summary(error, 160);
        if !error.is_empty() {
            summary.push_str(&format!(": {error}"));
        }
    }
    summary.push('.');
    AdaptiveWikiCandidateInput {
        kind: AdaptiveWikiKind::FailurePattern,
        scope,
        scope_ref,
        claim: format!("A runtime task via {runner} failed in this project."),
        suggested_ai_instruction: format!(
            "A prior {runner} task failed here; check the recorded error before retrying the same command."
        ),
        human_summary: summary,
        evidence_ref: Some(task.task_id.clone()),
        signal_kind: AdaptiveWikiSignalKind::RepeatedFailure,
        origin: AdaptiveWikiOrigin::RuntimeObserved,
        confidence: AdaptiveWikiConfidence::Inferred,
        review_reason: "Observed from a failed runtime task.".to_string(),
        ..AdaptiveWikiCandidateInput::default()
    }
}

/// Build the learning candidate for a resume-pending task: a recovery pattern to
/// watch for on future long-running work.
pub fn resume_recovery_input(resume: &TaskResumeState) -> AdaptiveWikiCandidateInput {
    let (scope, scope_ref) = scope_for_project(&resume.project_key);
    let phase = operator_safe_text(resume.phase.trim());
    let phase = if phase.is_empty() {
        "an earlier phase".to_string()
    } else {
        format!("phase \"{phase}\"")
    };
    let mut summary = format!("Resume {} for task {}", resume.resume_id(), resume.task_id);
    if let Some(last) = resume.last_task_status.as_deref() {
        let last = operator_safe_text(last.trim());
        if !last.is_empty() {
            summary.push_str(&format!(" (last status {last})"));
        }
    }
    summary.push('.');
    AdaptiveWikiCandidateInput {
        kind: AdaptiveWikiKind::FailurePattern,
        scope,
        scope_ref,
        claim: format!("A task needed recovery (resume) at {phase} in this project."),
        suggested_ai_instruction: format!(
            "A prior run went stale or lost at {phase}; verify heartbeat/liveness and resume from the recorded safe step."
        ),
        human_summary: summary,
        evidence_ref: Some(resume.resume_id()),
        signal_kind: AdaptiveWikiSignalKind::RepeatedFailure,
        origin: AdaptiveWikiOrigin::RuntimeObserved,
        confidence: AdaptiveWikiConfidence::Inferred,
        review_reason: "Observed from a resume-pending recovery row.".to_string(),
        ..AdaptiveWikiCandidateInput::default()
    }
}

/// Scan the current Offdesk stores and emit an adaptive-wiki candidate for each
/// not-yet-processed denial, failure, and resume-recovery event. Idempotent
/// across runs via the durable cursor; safe to call every tick.
pub fn scan_and_emit_learning_signals(
    profile_dir: impl AsRef<Path>,
    now: DateTime<Utc>,
) -> Result<LearningScanReport> {
    let profile_dir = profile_dir.as_ref();
    let cursor_store = LearningSignalStore::new(profile_dir);
    let mut cursor = cursor_store.load()?;
    let wiki = AdaptiveWikiStore::new(profile_dir);

    let approvals = ApprovalLedger::new(profile_dir).load()?;
    let tasks = OffdeskTaskStore::new(profile_dir).load()?;
    let resumes = TaskResumeStore::new(profile_dir).load()?;

    let mut report = LearningScanReport::default();

    let mut emit = |key: String,
                    source: LearningSignalSource,
                    input: AdaptiveWikiCandidateInput|
     -> Result<()> {
        if cursor.processed.contains(&key) {
            report.skipped_already_processed += 1;
            return Ok(());
        }
        let claim = input.claim.clone();
        let candidate = wiki.record_candidate(input, now)?;
        cursor.processed.insert(key.clone());
        report.emitted.push(EmittedLearningSignal {
            signal_key: key,
            source,
            candidate_id: candidate.id,
            claim,
        });
        Ok(())
    };

    for approval in approvals
        .iter()
        .filter(|approval| approval.status == ApprovalStatus::Denied)
    {
        emit(
            approval_denial_key(approval),
            LearningSignalSource::ApprovalDenied,
            approval_denial_input(approval),
        )?;
    }

    for task in tasks
        .iter()
        .filter(|task| task.status == OffdeskTaskStatus::Failed)
    {
        emit(
            task_failure_key(task),
            LearningSignalSource::TaskFailed,
            task_failure_input(task),
        )?;
    }

    for resume in resumes
        .iter()
        .filter(|resume| resume.status == ResumeStatus::ResumePending)
    {
        emit(
            resume_pending_key(resume),
            LearningSignalSource::ResumePending,
            resume_recovery_input(resume),
        )?;
    }

    if !report.emitted.is_empty() {
        cursor.updated_at = Some(now);
        cursor_store.save(&cursor)?;
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::approval::{ApprovalMode, ApprovalScope, RiskLevel};
    use crate::offdesk::background::BackgroundRunnerKind;
    use crate::offdesk::task_queue::{OffdeskTaskInput, OffdeskTaskStatus};
    use tempfile::tempdir;

    fn denied_approval(id: &str, action: &str, project: &str) -> PendingActionApproval {
        PendingActionApproval {
            approval_id: id.to_string(),
            action_id: String::new(),
            status: ApprovalStatus::Denied,
            scope: ApprovalScope::Once,
            project_key: project.to_string(),
            request_id: "req-1".to_string(),
            task_id: "task-1".to_string(),
            action: action.to_string(),
            risk_level: RiskLevel::RuntimeMutation,
            approval_mode: ApprovalMode::OperatorRequired,
            preview: String::new(),
            reason: "not allowed here".to_string(),
            created_at: now(),
            expires_at: now(),
            resolved_at: Some(now()),
            resolved_by: Some("operator".to_string()),
            source_surface: "cli".to_string(),
            metadata: None,
        }
    }

    fn failed_task(task_id: &str, project: &str, last_error: &str) -> OffdeskTask {
        let mut task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some(task_id.to_string()),
                request_id: "req-1".to_string(),
                project_key: project.to_string(),
                capability_id: "dispatch.runtime".to_string(),
                runner_kind: BackgroundRunnerKind::LocalBackground,
                command: "cargo build".to_string(),
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
            now(),
        );
        task.status = OffdeskTaskStatus::Failed;
        task.attempt_count = 2;
        task.last_error = Some(last_error.to_string());
        task
    }

    fn now() -> DateTime<Utc> {
        DateTime::parse_from_rfc3339("2026-07-20T00:00:00Z")
            .unwrap()
            .with_timezone(&Utc)
    }

    #[test]
    fn approval_denial_input_is_policy_rule_and_redacts_secrets() {
        let mut approval = denied_approval("appr-1", "canonical.apply", "proj-a");
        approval.reason = "blocked: token=sk-secret-value".to_string();
        let input = approval_denial_input(&approval);
        assert_eq!(input.kind, AdaptiveWikiKind::PolicyRule);
        assert_eq!(input.signal_kind, AdaptiveWikiSignalKind::ApprovalDenial);
        assert_eq!(input.scope, AdaptiveWikiScope::Project);
        assert_eq!(input.scope_ref, "proj-a");
        assert!(input.claim.contains("canonical.apply"));
        assert_eq!(input.evidence_ref.as_deref(), Some("appr-1"));
        assert!(
            !input.human_summary.contains("sk-secret-value"),
            "secret leaked into summary: {}",
            input.human_summary
        );
    }

    #[test]
    fn empty_project_falls_back_to_user_global_scope() {
        let approval = denied_approval("appr-2", "dispatch.runtime", "");
        let input = approval_denial_input(&approval);
        assert_eq!(input.scope, AdaptiveWikiScope::UserGlobal);
        assert!(input.scope_ref.is_empty());
    }

    #[test]
    fn scan_emits_once_and_is_idempotent_across_runs() {
        let dir = tempdir().unwrap();
        let profile = dir.path();

        // Seed one denied approval and one failed task.
        let approvals = vec![denied_approval("appr-1", "canonical.apply", "proj-a")];
        ApprovalLedger::new(profile).save(&approvals).unwrap();

        let task = failed_task("task-9", "proj-a", "compile error: password=hunter2");
        OffdeskTaskStore::new(profile).save(&[task]).unwrap();

        let first = scan_and_emit_learning_signals(profile, now()).unwrap();
        assert_eq!(first.emitted.len(), 2, "first scan should emit both events");
        assert_eq!(first.skipped_already_processed, 0);

        // The candidate store must not leak the secret from last_error.
        let candidates = AdaptiveWikiStore::new(profile)
            .load_candidates()
            .unwrap()
            .candidates;
        assert_eq!(candidates.len(), 2);
        assert!(
            candidates
                .iter()
                .all(|candidate| !candidate.human_summary.contains("hunter2")),
            "secret leaked into a candidate summary"
        );

        // Re-running the scan must emit nothing new (durable cursor).
        let second = scan_and_emit_learning_signals(profile, now()).unwrap();
        assert_eq!(second.emitted.len(), 0, "second scan must be idempotent");
        assert_eq!(second.skipped_already_processed, 2);
    }
}
