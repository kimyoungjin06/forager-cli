//! Pending action approvals for offdesk execution.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use uuid::Uuid;

use super::redaction::operator_safe_text;

const APPROVALS_FILE: &str = "pending_action_approvals.json";
const ACTION_AUDIT_FILE: &str = "action_audit.jsonl";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalStatus {
    Pending,
    Approved,
    Denied,
    Expired,
    Superseded,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalScope {
    Once,
    Session,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RiskLevel {
    Safe,
    RuntimeMutation,
    CanonicalMutation,
    Destructive,
    ExternalSideEffect,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ApprovalMode {
    EnvelopeAuto,
    OperatorRequired,
    PolicyDenied,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PendingActionApproval {
    pub approval_id: String,
    pub status: ApprovalStatus,
    pub scope: ApprovalScope,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub action: String,
    pub risk_level: RiskLevel,
    pub approval_mode: ApprovalMode,
    pub preview: String,
    pub reason: String,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resolved_by: Option<String>,
    pub source_surface: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ApprovalDecision {
    Proceed(ApprovalMode),
    Pending(Box<PendingActionApproval>),
    Denied(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionBrief {
    pub request_id: String,
    pub task_id: String,
    pub project_key: String,
    #[serde(default)]
    pub approved: bool,
    #[serde(default)]
    pub allowed_runtime_mutations: Vec<String>,
    #[serde(default)]
    pub allowed_canonical_mutations: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fresh_until: Option<DateTime<Utc>>,
}

impl ExecutionBrief {
    fn is_fresh_at(&self, now: DateTime<Utc>) -> bool {
        self.approved
            && self
                .fresh_until
                .map_or(true, |fresh_until| fresh_until >= now)
    }

    fn matches_context(&self, request: &ActionApprovalRequest) -> bool {
        self.project_key == request.project_key
            && self.request_id == request.request_id
            && self.task_id == request.task_id
    }

    fn allows_runtime(&self, request: &ActionApprovalRequest, now: DateTime<Utc>) -> bool {
        self.is_fresh_at(now)
            && self.matches_context(request)
            && class_allowed(&self.allowed_runtime_mutations, request.mutation_class())
    }

    fn allows_canonical(&self, request: &ActionApprovalRequest, now: DateTime<Utc>) -> bool {
        self.is_fresh_at(now)
            && self.matches_context(request)
            && class_allowed(&self.allowed_canonical_mutations, request.mutation_class())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionApprovalRequest {
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub action: String,
    pub risk_level: RiskLevel,
    pub mutation_class: Option<String>,
    pub preview: String,
    pub reason: String,
    pub source_surface: String,
    pub ttl: Duration,
}

impl ActionApprovalRequest {
    pub fn new(
        project_key: impl Into<String>,
        request_id: impl Into<String>,
        task_id: impl Into<String>,
        action: impl Into<String>,
        risk_level: RiskLevel,
    ) -> Self {
        Self {
            project_key: project_key.into(),
            request_id: request_id.into(),
            task_id: task_id.into(),
            action: action.into(),
            risk_level,
            mutation_class: None,
            preview: String::new(),
            reason: String::new(),
            source_surface: "offdesk".to_string(),
            ttl: Duration::minutes(30),
        }
    }

    fn mutation_class(&self) -> &str {
        self.mutation_class.as_deref().unwrap_or(&self.action)
    }
}

#[derive(Debug, Serialize)]
struct ActionAuditEntry<'a> {
    transition: &'a str,
    approval_id: &'a str,
    status: ApprovalStatus,
    project_key: &'a str,
    request_id: &'a str,
    task_id: &'a str,
    action: &'a str,
    risk_level: RiskLevel,
    approval_mode: ApprovalMode,
    detail: &'a str,
    recorded_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
struct QueuedApprovalTransition {
    transition: &'static str,
    approval: PendingActionApproval,
    detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct ApprovalLookupKey {
    project_key: String,
    request_id: String,
    task_id: String,
    action: String,
    risk_level: RiskLevel,
}

impl ApprovalLookupKey {
    fn from_approval(approval: &PendingActionApproval) -> Self {
        Self {
            project_key: approval.project_key.clone(),
            request_id: approval.request_id.clone(),
            task_id: approval.task_id.clone(),
            action: approval.action.clone(),
            risk_level: approval.risk_level,
        }
    }

    fn from_request(request: &ActionApprovalRequest) -> Self {
        Self {
            project_key: request.project_key.clone(),
            request_id: request.request_id.clone(),
            task_id: request.task_id.clone(),
            action: request.action.clone(),
            risk_level: request.risk_level,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ApprovalLedger {
    root: PathBuf,
}

#[derive(Debug, Clone)]
pub struct ApprovalLedgerSession {
    ledger: ApprovalLedger,
    approvals: Vec<PendingActionApproval>,
    lookup: HashMap<ApprovalLookupKey, Vec<usize>>,
    transitions: Vec<QueuedApprovalTransition>,
    dirty: bool,
    expired_through: DateTime<Utc>,
}

impl ApprovalLedger {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn approvals_path(&self) -> PathBuf {
        self.root.join(APPROVALS_FILE)
    }

    pub fn action_audit_path(&self) -> PathBuf {
        self.root.join(ACTION_AUDIT_FILE)
    }

    pub fn load(&self) -> Result<Vec<PendingActionApproval>> {
        read_approvals(&self.approvals_path())
    }

    pub fn save(&self, approvals: &[PendingActionApproval]) -> Result<()> {
        write_approvals(&self.approvals_path(), approvals)
    }

    pub fn begin_session(
        &self,
        now: DateTime<Utc>,
    ) -> Result<(ApprovalLedgerSession, Vec<PendingActionApproval>)> {
        let mut approvals = self.load()?;
        let expired = expire_due_collect(&mut approvals, now);
        let transitions = expired
            .iter()
            .cloned()
            .map(|approval| QueuedApprovalTransition {
                transition: "expire",
                approval,
                detail: "expired".to_string(),
            })
            .collect::<Vec<_>>();
        let dirty = !expired.is_empty();
        Ok((
            ApprovalLedgerSession {
                ledger: self.clone(),
                lookup: build_approval_lookup(&approvals),
                approvals,
                transitions,
                dirty,
                expired_through: now,
            },
            expired,
        ))
    }

    pub fn evaluate_action(
        &self,
        request: ActionApprovalRequest,
        brief: Option<&ExecutionBrief>,
        now: DateTime<Utc>,
    ) -> Result<ApprovalDecision> {
        let mut approvals = self.load()?;
        expire_due_in_place(&mut approvals, now);

        if let Some(grant) = approvals
            .iter()
            .find(|approval| approved_session_grant_covers(approval, &request, now))
        {
            self.save(&approvals)?;
            return Ok(ApprovalDecision::Proceed(grant.approval_mode));
        }

        if let Some(index) = approvals
            .iter()
            .position(|approval| approved_once_grant_covers(approval, &request, now))
        {
            let approval_mode = approvals[index].approval_mode;
            approvals[index].status = ApprovalStatus::Superseded;
            let consumed = approvals[index].clone();
            self.save(&approvals)?;
            self.append_transition("supersede", &consumed, "consumed")?;
            return Ok(ApprovalDecision::Proceed(approval_mode));
        }

        if let Some(pending) = approvals
            .iter()
            .find(|approval| pending_approval_covers(approval, &request, now))
        {
            let pending = pending.clone();
            self.save(&approvals)?;
            return Ok(ApprovalDecision::Pending(Box::new(pending)));
        }

        let decision = match request.risk_level {
            RiskLevel::Safe => ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto),
            RiskLevel::RuntimeMutation
                if brief.is_some_and(|brief| brief.allows_runtime(&request, now)) =>
            {
                ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto)
            }
            RiskLevel::CanonicalMutation
                if brief.is_some_and(|brief| brief.allows_canonical(&request, now)) =>
            {
                ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto)
            }
            RiskLevel::RuntimeMutation
            | RiskLevel::CanonicalMutation
            | RiskLevel::Destructive
            | RiskLevel::ExternalSideEffect => {
                let approval = PendingActionApproval {
                    approval_id: format!("approval_{}", Uuid::new_v4()),
                    status: ApprovalStatus::Pending,
                    scope: ApprovalScope::Once,
                    project_key: request.project_key,
                    request_id: request.request_id,
                    task_id: request.task_id,
                    action: request.action,
                    risk_level: request.risk_level,
                    approval_mode: ApprovalMode::OperatorRequired,
                    preview: operator_safe_text(&request.preview),
                    reason: operator_safe_text(&request.reason),
                    created_at: now,
                    expires_at: now + request.ttl,
                    resolved_at: None,
                    resolved_by: None,
                    source_surface: request.source_surface,
                };
                approvals.push(approval.clone());
                self.save(&approvals)?;
                self.append_transition("pending", &approval, "created")?;
                ApprovalDecision::Pending(Box::new(approval))
            }
        };

        if !matches!(decision, ApprovalDecision::Pending(_)) {
            self.save(&approvals)?;
        }

        Ok(decision)
    }

    pub fn approve_oldest_pending(
        &self,
        resolved_by: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        self.resolve_pending(None, ApprovalStatus::Approved, resolved_by.into(), now)
    }

    pub fn deny_oldest_pending(
        &self,
        resolved_by: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        self.resolve_pending(None, ApprovalStatus::Denied, resolved_by.into(), now)
    }

    pub fn approve_pending(
        &self,
        approval_id: Option<&str>,
        resolved_by: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        self.resolve_pending(
            approval_id,
            ApprovalStatus::Approved,
            resolved_by.into(),
            now,
        )
    }

    pub fn deny_pending(
        &self,
        approval_id: Option<&str>,
        resolved_by: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        self.resolve_pending(approval_id, ApprovalStatus::Denied, resolved_by.into(), now)
    }

    pub fn expire_due(&self, now: DateTime<Utc>) -> Result<Vec<PendingActionApproval>> {
        let mut approvals = self.load()?;
        let expired = expire_due_collect(&mut approvals, now);
        self.save(&approvals)?;
        for approval in &expired {
            self.append_transition("expire", approval, "expired")?;
        }
        Ok(expired)
    }

    pub fn denied_matches(&self, request: &ActionApprovalRequest) -> Result<bool> {
        Ok(self
            .load()?
            .iter()
            .any(|approval| denied_approval_covers(approval, request)))
    }

    pub fn supersede_denied_for_task(
        &self,
        project_key: &str,
        request_id: &str,
        task_id: &str,
        action: &str,
        resolved_by: impl Into<String>,
        now: DateTime<Utc>,
    ) -> Result<Vec<PendingActionApproval>> {
        let mut approvals = self.load()?;
        let resolved_by = operator_safe_text(&resolved_by.into());
        let mut superseded = Vec::new();

        for approval in approvals.iter_mut().filter(|approval| {
            approval.status == ApprovalStatus::Denied
                && approval.project_key == project_key
                && approval.request_id == request_id
                && approval.task_id == task_id
                && approval.action == action
        }) {
            approval.status = ApprovalStatus::Superseded;
            approval.resolved_at = Some(now);
            approval.resolved_by = Some(resolved_by.clone());
            superseded.push(approval.clone());
        }

        self.save(&approvals)?;
        for approval in &superseded {
            self.append_transition("supersede_denied", approval, "new_approval_retry")?;
        }

        Ok(superseded)
    }

    fn resolve_pending(
        &self,
        approval_id: Option<&str>,
        status: ApprovalStatus,
        resolved_by: String,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        let mut approvals = self.load()?;
        expire_due_in_place(&mut approvals, now);
        let target_index = match approval_id {
            Some(approval_id) => approvals.iter().position(|approval| {
                approval.status == ApprovalStatus::Pending && approval.approval_id == approval_id
            }),
            None => approvals
                .iter()
                .enumerate()
                .filter(|(_, approval)| approval.status == ApprovalStatus::Pending)
                .min_by_key(|(_, approval)| approval.created_at)
                .map(|(index, _)| index),
        };

        let Some(target_index) = target_index else {
            self.save(&approvals)?;
            return Ok(None);
        };
        let approval = &mut approvals[target_index];

        approval.status = status;
        approval.resolved_at = Some(now);
        approval.resolved_by = Some(operator_safe_text(&resolved_by));
        let resolved = approval.clone();

        self.save(&approvals)?;
        let transition = match status {
            ApprovalStatus::Approved => "approve",
            ApprovalStatus::Denied => "deny",
            _ => "resolve",
        };
        self.append_transition(transition, &resolved, transition)?;
        Ok(Some(resolved))
    }

    fn append_transition(
        &self,
        transition: &'static str,
        approval: &PendingActionApproval,
        detail: &str,
    ) -> Result<()> {
        self.append_queued_transitions(&[QueuedApprovalTransition {
            transition,
            approval: approval.clone(),
            detail: detail.to_string(),
        }])
    }

    fn append_queued_transitions(&self, transitions: &[QueuedApprovalTransition]) -> Result<()> {
        if transitions.is_empty() {
            return Ok(());
        }
        fs::create_dir_all(&self.root)?;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.action_audit_path())?;
        for transition in transitions {
            let approval = &transition.approval;
            let entry = ActionAuditEntry {
                transition: transition.transition,
                approval_id: &approval.approval_id,
                status: approval.status,
                project_key: &approval.project_key,
                request_id: &approval.request_id,
                task_id: &approval.task_id,
                action: &approval.action,
                risk_level: approval.risk_level,
                approval_mode: approval.approval_mode,
                detail: &transition.detail,
                recorded_at: Utc::now(),
            };
            writeln!(file, "{}", serde_json::to_string(&entry)?)?;
        }
        Ok(())
    }
}

impl ApprovalLedgerSession {
    pub fn denied_matches(&self, request: &ActionApprovalRequest) -> bool {
        let lookup_key = ApprovalLookupKey::from_request(request);
        self.lookup
            .get(&lookup_key)
            .map_or(&[] as &[usize], Vec::as_slice)
            .iter()
            .any(|&index| denied_approval_covers(&self.approvals[index], request))
    }

    pub fn evaluate_action(
        &mut self,
        request: ActionApprovalRequest,
        brief: Option<&ExecutionBrief>,
        now: DateTime<Utc>,
    ) -> Result<ApprovalDecision> {
        if now > self.expired_through {
            for approval in expire_due_collect(&mut self.approvals, now) {
                self.dirty = true;
                self.transitions.push(QueuedApprovalTransition {
                    transition: "expire",
                    approval,
                    detail: "expired".to_string(),
                });
            }
            self.expired_through = now;
        }

        let lookup_key = ApprovalLookupKey::from_request(&request);
        let candidate_indices = self
            .lookup
            .get(&lookup_key)
            .map_or(&[] as &[usize], Vec::as_slice);

        if let Some(grant) = candidate_indices
            .iter()
            .map(|&index| &self.approvals[index])
            .find(|approval| approved_session_grant_covers(approval, &request, now))
        {
            return Ok(ApprovalDecision::Proceed(grant.approval_mode));
        }

        if let Some(index) = candidate_indices
            .iter()
            .copied()
            .find(|&index| approved_once_grant_covers(&self.approvals[index], &request, now))
        {
            let approval_mode = self.approvals[index].approval_mode;
            self.approvals[index].status = ApprovalStatus::Superseded;
            let consumed = self.approvals[index].clone();
            self.dirty = true;
            self.transitions.push(QueuedApprovalTransition {
                transition: "supersede",
                approval: consumed,
                detail: "consumed".to_string(),
            });
            return Ok(ApprovalDecision::Proceed(approval_mode));
        }

        if let Some(pending) = candidate_indices
            .iter()
            .map(|&index| &self.approvals[index])
            .find(|approval| pending_approval_covers(approval, &request, now))
        {
            return Ok(ApprovalDecision::Pending(Box::new(pending.clone())));
        }

        let decision = match request.risk_level {
            RiskLevel::Safe => ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto),
            RiskLevel::RuntimeMutation
                if brief.is_some_and(|brief| brief.allows_runtime(&request, now)) =>
            {
                ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto)
            }
            RiskLevel::CanonicalMutation
                if brief.is_some_and(|brief| brief.allows_canonical(&request, now)) =>
            {
                ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto)
            }
            RiskLevel::RuntimeMutation
            | RiskLevel::CanonicalMutation
            | RiskLevel::Destructive
            | RiskLevel::ExternalSideEffect => {
                let approval = PendingActionApproval {
                    approval_id: format!("approval_{}", Uuid::new_v4()),
                    status: ApprovalStatus::Pending,
                    scope: ApprovalScope::Once,
                    project_key: request.project_key,
                    request_id: request.request_id,
                    task_id: request.task_id,
                    action: request.action,
                    risk_level: request.risk_level,
                    approval_mode: ApprovalMode::OperatorRequired,
                    preview: operator_safe_text(&request.preview),
                    reason: operator_safe_text(&request.reason),
                    created_at: now,
                    expires_at: now + request.ttl,
                    resolved_at: None,
                    resolved_by: None,
                    source_surface: request.source_surface,
                };
                let index = self.approvals.len();
                self.lookup.entry(lookup_key).or_default().push(index);
                self.approvals.push(approval.clone());
                self.dirty = true;
                self.transitions.push(QueuedApprovalTransition {
                    transition: "pending",
                    approval: approval.clone(),
                    detail: "created".to_string(),
                });
                ApprovalDecision::Pending(Box::new(approval))
            }
        };

        Ok(decision)
    }

    pub fn flush(&mut self) -> Result<()> {
        if self.dirty {
            self.ledger.save(&self.approvals)?;
            self.dirty = false;
        }
        let transitions = std::mem::take(&mut self.transitions);
        self.ledger.append_queued_transitions(&transitions)?;
        Ok(())
    }
}

fn class_allowed(allowed: &[String], mutation_class: &str) -> bool {
    allowed.iter().any(|class| {
        let class = class.trim();
        class == "*" || class.eq_ignore_ascii_case(mutation_class)
    })
}

fn build_approval_lookup(
    approvals: &[PendingActionApproval],
) -> HashMap<ApprovalLookupKey, Vec<usize>> {
    let mut lookup: HashMap<ApprovalLookupKey, Vec<usize>> = HashMap::new();
    for (index, approval) in approvals.iter().enumerate() {
        lookup
            .entry(ApprovalLookupKey::from_approval(approval))
            .or_default()
            .push(index);
    }
    lookup
}

fn approval_identity_matches(
    approval: &PendingActionApproval,
    request: &ActionApprovalRequest,
) -> bool {
    approval.project_key == request.project_key
        && approval.request_id == request.request_id
        && approval.task_id == request.task_id
        && approval.action == request.action
        && approval.risk_level == request.risk_level
}

fn denied_approval_covers(
    approval: &PendingActionApproval,
    request: &ActionApprovalRequest,
) -> bool {
    approval.status == ApprovalStatus::Denied && approval_identity_matches(approval, request)
}

fn approved_session_grant_covers(
    approval: &PendingActionApproval,
    request: &ActionApprovalRequest,
    now: DateTime<Utc>,
) -> bool {
    approval.status == ApprovalStatus::Approved
        && approval.scope == ApprovalScope::Session
        && approval.expires_at >= now
        && approval_identity_matches(approval, request)
}

fn approved_once_grant_covers(
    approval: &PendingActionApproval,
    request: &ActionApprovalRequest,
    now: DateTime<Utc>,
) -> bool {
    approval.status == ApprovalStatus::Approved
        && approval.scope == ApprovalScope::Once
        && approval.expires_at >= now
        && approval_identity_matches(approval, request)
}

fn pending_approval_covers(
    approval: &PendingActionApproval,
    request: &ActionApprovalRequest,
    now: DateTime<Utc>,
) -> bool {
    approval.status == ApprovalStatus::Pending
        && approval.expires_at >= now
        && approval_identity_matches(approval, request)
}

fn expire_due_in_place(approvals: &mut [PendingActionApproval], now: DateTime<Utc>) {
    let _ = expire_due_collect(approvals, now);
}

fn expire_due_collect(
    approvals: &mut [PendingActionApproval],
    now: DateTime<Utc>,
) -> Vec<PendingActionApproval> {
    let mut expired = Vec::new();
    for approval in approvals {
        if approval.status == ApprovalStatus::Pending && approval.expires_at <= now {
            approval.status = ApprovalStatus::Expired;
            approval.resolved_at = Some(now);
            approval.resolved_by = Some("system".to_string());
            expired.push(approval.clone());
        }
    }
    expired
}

fn read_approvals(path: &Path) -> Result<Vec<PendingActionApproval>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }
    Ok(serde_json::from_str(&content)?)
}

fn write_approvals(path: &Path, approvals: &[PendingActionApproval]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(approvals)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn request(risk_level: RiskLevel) -> ActionApprovalRequest {
        let mut request =
            ActionApprovalRequest::new("project", "request", "task", "dispatch", risk_level);
        request.mutation_class = Some("dispatch".to_string());
        request.preview = "token=sk-secretsecretsecretsecret".to_string();
        request
    }

    fn approval(
        approval_id: &str,
        status: ApprovalStatus,
        project_key: &str,
        request_id: &str,
        task_id: &str,
        action: &str,
        now: DateTime<Utc>,
    ) -> PendingActionApproval {
        PendingActionApproval {
            approval_id: approval_id.to_string(),
            status,
            scope: ApprovalScope::Once,
            project_key: project_key.to_string(),
            request_id: request_id.to_string(),
            task_id: task_id.to_string(),
            action: action.to_string(),
            risk_level: RiskLevel::RuntimeMutation,
            approval_mode: ApprovalMode::OperatorRequired,
            preview: String::new(),
            reason: String::new(),
            created_at: now,
            expires_at: now + Duration::minutes(10),
            resolved_at: if status == ApprovalStatus::Pending {
                None
            } else {
                Some(now)
            },
            resolved_by: if status == ApprovalStatus::Pending {
                None
            } else {
                Some("operator".to_string())
            },
            source_surface: "test".to_string(),
        }
    }

    #[test]
    fn runtime_mutation_inside_execution_brief_proceeds() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let brief = ExecutionBrief {
            request_id: "request".to_string(),
            task_id: "task".to_string(),
            project_key: "project".to_string(),
            approved: true,
            allowed_runtime_mutations: vec!["dispatch".to_string()],
            allowed_canonical_mutations: vec![],
            fresh_until: Some(now + Duration::minutes(5)),
        };

        let decision =
            ledger.evaluate_action(request(RiskLevel::RuntimeMutation), Some(&brief), now)?;

        assert_eq!(
            decision,
            ApprovalDecision::Proceed(ApprovalMode::EnvelopeAuto)
        );
        assert!(ledger.load()?.is_empty());
        Ok(())
    }

    #[test]
    fn runtime_mutation_outside_envelope_creates_pending_approval() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();

        let decision = ledger.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?;

        let ApprovalDecision::Pending(approval) = decision else {
            panic!("expected pending approval");
        };
        assert_eq!(approval.status, ApprovalStatus::Pending);
        assert!(!approval.preview.contains("sk-secret"));
        assert_eq!(ledger.load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn ok_and_cancel_resolve_oldest_pending_and_audit() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        ledger.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?;
        ledger.evaluate_action(
            request(RiskLevel::Destructive),
            None,
            now + Duration::seconds(1),
        )?;

        let approved = ledger
            .approve_oldest_pending("operator", now + Duration::seconds(2))?
            .expect("approval");
        assert_eq!(approved.status, ApprovalStatus::Approved);

        let denied = ledger
            .deny_oldest_pending("operator", now + Duration::seconds(3))?
            .expect("denial");
        assert_eq!(denied.status, ApprovalStatus::Denied);
        assert!(ledger.denied_matches(&request(RiskLevel::Destructive))?);

        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert!(audit.contains("\"transition\":\"pending\""));
        assert!(audit.contains("\"transition\":\"approve\""));
        assert!(audit.contains("\"transition\":\"deny\""));
        Ok(())
    }

    #[test]
    fn can_resolve_targeted_pending_approval() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let ApprovalDecision::Pending(first) =
            ledger.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?
        else {
            panic!("expected first pending approval");
        };
        let ApprovalDecision::Pending(second) = ledger.evaluate_action(
            request(RiskLevel::Destructive),
            None,
            now + Duration::seconds(1),
        )?
        else {
            panic!("expected second pending approval");
        };

        let resolved = ledger
            .approve_pending(
                Some(&second.approval_id),
                "operator",
                now + Duration::seconds(2),
            )?
            .expect("targeted approval");

        assert_eq!(resolved.approval_id, second.approval_id);
        let approvals = ledger.load()?;
        assert_eq!(approvals[0].approval_id, first.approval_id);
        assert_eq!(approvals[0].status, ApprovalStatus::Pending);
        assert_eq!(approvals[1].status, ApprovalStatus::Approved);
        Ok(())
    }

    #[test]
    fn expired_approval_transitions_to_expired() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let mut req = request(RiskLevel::CanonicalMutation);
        req.ttl = Duration::seconds(1);
        let now = Utc::now();
        ledger.evaluate_action(req, None, now)?;

        let expired = ledger.expire_due(now + Duration::seconds(2))?;

        assert_eq!(expired.len(), 1);
        assert_eq!(ledger.load()?[0].status, ApprovalStatus::Expired);
        Ok(())
    }

    #[test]
    fn supersede_denied_for_task_only_updates_matching_denied() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        ledger.save(&[
            approval(
                "matching_denied",
                ApprovalStatus::Denied,
                "project",
                "request",
                "task",
                "dispatch.runtime",
                now,
            ),
            approval(
                "pending_match",
                ApprovalStatus::Pending,
                "project",
                "request",
                "task",
                "dispatch.runtime",
                now,
            ),
            approval(
                "approved_match",
                ApprovalStatus::Approved,
                "project",
                "request",
                "task",
                "dispatch.runtime",
                now,
            ),
            approval(
                "other_task",
                ApprovalStatus::Denied,
                "project",
                "request",
                "other",
                "dispatch.runtime",
                now,
            ),
        ])?;

        let superseded = ledger.supersede_denied_for_task(
            "project",
            "request",
            "task",
            "dispatch.runtime",
            "operator",
            now + Duration::seconds(1),
        )?;

        assert_eq!(superseded.len(), 1);
        assert_eq!(superseded[0].approval_id, "matching_denied");
        let approvals = ledger.load()?;
        assert_eq!(approvals[0].status, ApprovalStatus::Superseded);
        assert_eq!(approvals[1].status, ApprovalStatus::Pending);
        assert_eq!(approvals[2].status, ApprovalStatus::Approved);
        assert_eq!(approvals[3].status, ApprovalStatus::Denied);

        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert!(audit.contains("\"transition\":\"supersede_denied\""));
        Ok(())
    }

    #[test]
    fn ledger_session_batches_pending_approvals_until_flush() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let (mut session, expired) = ledger.begin_session(now)?;
        assert!(expired.is_empty());

        let mut first = request(RiskLevel::RuntimeMutation);
        first.task_id = "first".to_string();
        let mut second = request(RiskLevel::RuntimeMutation);
        second.task_id = "second".to_string();

        assert!(matches!(
            session.evaluate_action(first, None, now)?,
            ApprovalDecision::Pending(_)
        ));
        assert!(matches!(
            session.evaluate_action(second, None, now)?,
            ApprovalDecision::Pending(_)
        ));
        assert!(ledger.load()?.is_empty());

        session.flush()?;

        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 2);
        assert_eq!(approvals[0].task_id, "first");
        assert_eq!(approvals[1].task_id, "second");
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert_eq!(audit.matches("\"transition\":\"pending\"").count(), 2);
        Ok(())
    }

    #[test]
    fn ledger_session_indexes_new_pending_until_flush() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let (mut session, _) = ledger.begin_session(now)?;
        let req = request(RiskLevel::RuntimeMutation);

        let ApprovalDecision::Pending(first) = session.evaluate_action(req.clone(), None, now)?
        else {
            panic!("expected first pending approval");
        };
        let ApprovalDecision::Pending(second) = session.evaluate_action(req, None, now)? else {
            panic!("expected indexed pending approval");
        };

        assert_eq!(first.approval_id, second.approval_id);
        session.flush()?;
        assert_eq!(ledger.load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn ledger_session_index_respects_once_grant_consumption() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        ledger.save(&[approval(
            "approved_once",
            ApprovalStatus::Approved,
            "project",
            "request",
            "task",
            "dispatch",
            now,
        )])?;
        let (mut session, _) = ledger.begin_session(now)?;
        let req = request(RiskLevel::RuntimeMutation);

        assert!(matches!(
            session.evaluate_action(req.clone(), None, now)?,
            ApprovalDecision::Proceed(ApprovalMode::OperatorRequired)
        ));
        assert!(matches!(
            session.evaluate_action(req, None, now)?,
            ApprovalDecision::Pending(_)
        ));

        session.flush()?;
        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 2);
        assert_eq!(approvals[0].status, ApprovalStatus::Superseded);
        assert_eq!(approvals[1].status, ApprovalStatus::Pending);
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert!(audit.contains("\"transition\":\"supersede\""));
        assert!(audit.contains("\"transition\":\"pending\""));
        Ok(())
    }
}
