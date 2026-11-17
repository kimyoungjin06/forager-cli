//! Pending action approvals for offdesk execution.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Seek, SeekFrom, Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use uuid::Uuid;

use super::provider::{ProviderFallbackCandidate, ProviderFallbackRecommendation};
use super::redaction::operator_safe_text;

const APPROVALS_FILE: &str = "pending_action_approvals.json";
const APPROVALS_LOCK_FILE: &str = "pending_action_approvals.lock";
const ACTION_AUDIT_FILE: &str = "action_audit.jsonl";
const APPROVAL_BRIEF_SCHEMA: &str = "approval_brief.v1";
pub const OFFDESK_SHIFT_ARM_APPROVAL_METADATA_SCHEMA: &str =
    "offdesk_shift_arm_approval_metadata.v1";
const APPROVAL_SNAPSHOT_HASH_DOMAIN: &[u8] = b"forager.approval-ledger.snapshot.v1\0";
const MAX_APPROVAL_LEDGER_BYTES: u64 = 64 * 1024 * 1024;

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
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub action_id: String,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub metadata: Option<ActionApprovalMetadata>,
}

impl PendingActionApproval {
    pub fn action_id(&self) -> &str {
        if self.action_id.is_empty() {
            &self.approval_id
        } else {
            &self.action_id
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ActionApprovalMetadata {
    ProviderFallback(Box<ProviderFallbackApprovalMetadata>),
    ArtifactRetention(Box<ArtifactRetentionApprovalMetadata>),
    RetentionV2(Box<crate::document_lifecycle::RetentionApprovalMetadataV2>),
    DocumentLifecycle(Box<crate::document_lifecycle::DocumentLifecycleApprovalMetadataV1>),
    OffdeskShiftArm(Box<OffdeskShiftArmApprovalMetadataV1>),
}

impl ActionApprovalMetadata {
    pub fn provider_fallback_from_recommendation(
        recommendation: &ProviderFallbackRecommendation,
        runner_role: &str,
        candidate_limit: usize,
    ) -> Option<Self> {
        let candidates = recommendation
            .candidates
            .iter()
            .filter(|candidate| candidate.recommended)
            .take(candidate_limit)
            .map(operator_safe_provider_fallback_candidate)
            .collect::<Vec<_>>();
        if candidates.is_empty() {
            return None;
        }
        let approval_brief =
            ApprovalBrief::provider_fallback(recommendation, runner_role, &candidates);

        Some(Self::ProviderFallback(Box::new(
            ProviderFallbackApprovalMetadata {
                current_provider_id: operator_safe_text(&recommendation.current_provider_id),
                current_model: recommendation
                    .current_model
                    .as_deref()
                    .map(operator_safe_text),
                runner_role: operator_safe_text(runner_role),
                generated_at: recommendation.generated_at,
                candidate_limit,
                candidates,
                apply_scope: ProviderFallbackApplyScope::RequestMatchingProviderModel,
                approval_brief: Some(approval_brief),
            },
        )))
    }

    pub fn as_provider_fallback(&self) -> Option<&ProviderFallbackApprovalMetadata> {
        match self {
            Self::ProviderFallback(metadata) => Some(metadata),
            Self::ArtifactRetention(_)
            | Self::RetentionV2(_)
            | Self::DocumentLifecycle(_)
            | Self::OffdeskShiftArm(_) => None,
        }
    }

    pub fn as_artifact_retention(&self) -> Option<&ArtifactRetentionApprovalMetadata> {
        match self {
            Self::ArtifactRetention(metadata) => Some(metadata),
            Self::ProviderFallback(_)
            | Self::RetentionV2(_)
            | Self::DocumentLifecycle(_)
            | Self::OffdeskShiftArm(_) => None,
        }
    }

    pub fn as_retention_v2(
        &self,
    ) -> Option<&crate::document_lifecycle::RetentionApprovalMetadataV2> {
        match self {
            Self::RetentionV2(metadata) => Some(metadata),
            Self::ProviderFallback(_)
            | Self::ArtifactRetention(_)
            | Self::DocumentLifecycle(_)
            | Self::OffdeskShiftArm(_) => None,
        }
    }

    pub fn as_document_lifecycle(
        &self,
    ) -> Option<&crate::document_lifecycle::DocumentLifecycleApprovalMetadataV1> {
        match self {
            Self::DocumentLifecycle(metadata) => Some(metadata),
            Self::ProviderFallback(_)
            | Self::ArtifactRetention(_)
            | Self::RetentionV2(_)
            | Self::OffdeskShiftArm(_) => None,
        }
    }

    pub fn as_offdesk_shift_arm(&self) -> Option<&OffdeskShiftArmApprovalMetadataV1> {
        match self {
            Self::OffdeskShiftArm(metadata) => Some(metadata),
            Self::ProviderFallback(_)
            | Self::ArtifactRetention(_)
            | Self::RetentionV2(_)
            | Self::DocumentLifecycle(_) => None,
        }
    }

    pub fn approval_brief(&self) -> Option<&ApprovalBrief> {
        match self {
            Self::ProviderFallback(metadata) => metadata.approval_brief.as_ref(),
            Self::ArtifactRetention(metadata) => metadata.approval_brief.as_ref(),
            Self::RetentionV2(metadata) => Some(&metadata.approval_brief),
            Self::DocumentLifecycle(metadata) => Some(&metadata.approval_brief),
            Self::OffdeskShiftArm(metadata) => Some(&metadata.approval_brief),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftArmApprovalMetadataV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub plan_id: String,
    pub plan_review_id: String,
    pub starts_at: DateTime<Utc>,
    pub stops_at: DateTime<Utc>,
    pub task_count: usize,
    pub routine_count: usize,
    pub approval_brief: ApprovalBrief,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovalBrief {
    pub schema: String,
    pub recommendation: String,
    pub subject: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub summary_lines: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub judgment_route_summary: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence_sufficiency: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_if_no_reply: Option<String>,
    pub scope: String,
    pub question: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub options: Vec<ApprovalBriefOption>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub why_recommendation: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub decision_impacts: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reply_examples: Vec<String>,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub context: HashMap<String, String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovalBriefOption {
    pub id: String,
    pub label: String,
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub natural_input_prompt: Option<String>,
}

impl ApprovalBrief {
    fn provider_fallback(
        recommendation: &ProviderFallbackRecommendation,
        runner_role: &str,
        candidates: &[ProviderFallbackCandidate],
    ) -> Self {
        let current_target = provider_model_label(
            &recommendation.current_provider_id,
            recommendation.current_model.as_deref(),
        );
        let candidate_label = candidates
            .first()
            .map(|candidate| {
                provider_model_label(&candidate.provider_id, candidate.model.as_deref())
            })
            .unwrap_or_else(|| "no recommended candidate".to_string());
        let safe_trigger = operator_safe_text(&recommendation.trigger_reason);
        let safe_runner_role = operator_safe_text(runner_role);
        let approve_impact = format!(
            "Retarget only this request from {current_target} to {candidate_label}; runtime dispatch still needs its own approval."
        );
        let deny_impact = format!(
            "Keep {current_target} queued on the current provider/model and wait for capacity recovery or manual retargeting."
        );
        let defer_impact =
            "Leave this approval pending while reviewing provider capacity, quality, or cost evidence."
                .to_string();
        let mut decision_impacts = HashMap::new();
        decision_impacts.insert("approve".to_string(), approve_impact.clone());
        decision_impacts.insert("deny".to_string(), deny_impact.clone());
        decision_impacts.insert("defer".to_string(), defer_impact.clone());

        let mut context = HashMap::new();
        context.insert("runner_role".to_string(), safe_runner_role.clone());
        context.insert("current_provider_model".to_string(), current_target.clone());
        context.insert("top_candidate".to_string(), candidate_label.clone());

        Self {
            schema: APPROVAL_BRIEF_SCHEMA.to_string(),
            source: Some("offdesk.provider_fallback".to_string()),
            recommendation: "approve".to_string(),
            subject: "provider fallback".to_string(),
            summary_lines: vec![
                "Provider/model retargeting is waiting for operator approval.".to_string(),
                format!("Reason: {safe_trigger}."),
                format!("Candidate: {candidate_label}."),
            ],
            judgment_route_summary: Some(
                "판단 경로: deterministic gate - provider capacity policy selected operator approval for retargeting.".to_string(),
            ),
            evidence_sufficiency: Some(
                "Provider capacity evidence and ranked fallback candidates are summarized; dispatch still needs a separate approval.".to_string(),
            ),
            default_if_no_reply: Some("defer".to_string()),
            options: vec![
                ApprovalBriefOption {
                    id: "approve".to_string(),
                    label: "Approve fallback".to_string(),
                    description: approve_impact,
                    natural_input_prompt: None,
                },
                ApprovalBriefOption {
                    id: "deny".to_string(),
                    label: "Deny fallback".to_string(),
                    description: deny_impact,
                    natural_input_prompt: Some(
                        "Explain why this fallback should not be applied.".to_string(),
                    ),
                },
                ApprovalBriefOption {
                    id: "defer".to_string(),
                    label: "Need more detail".to_string(),
                    description: defer_impact,
                    natural_input_prompt: Some(
                        "State what provider, cost, or quality evidence you need first.".to_string(),
                    ),
                },
            ],
            why_recommendation: vec![
                format!("{current_target} is currently blocked by provider capacity state."),
                format!("{candidate_label} is the first currently recommended fallback candidate."),
                "The approval is scoped to provider/model retargeting only.".to_string(),
            ],
            evidence: candidates
                .iter()
                .take(3)
                .map(|candidate| {
                    format!(
                        "{}: {}",
                        provider_model_label(&candidate.provider_id, candidate.model.as_deref()),
                        operator_safe_text(&candidate.reason)
                    )
                })
                .collect(),
            decision_impacts,
            reply_examples: vec![
                "approve".to_string(),
                "deny - keep the current provider until cooldown ends".to_string(),
                "defer - check cost or quality first".to_string(),
            ],
            context,
            scope: "Approves provider/model retargeting for this request only; does not approve runtime dispatch, command/workdir changes, cleanup, or wiki promotion.".to_string(),
            question: "Approve this provider fallback retargeting?".to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderFallbackApprovalMetadata {
    pub current_provider_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_model: Option<String>,
    pub runner_role: String,
    pub generated_at: DateTime<Utc>,
    pub candidate_limit: usize,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub candidates: Vec<ProviderFallbackCandidate>,
    pub apply_scope: ProviderFallbackApplyScope,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approval_brief: Option<ApprovalBrief>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProviderFallbackApplyScope {
    RequestMatchingProviderModel,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ArtifactRetentionApprovalMetadata {
    pub generated_at: DateTime<Utc>,
    pub artifact_id: String,
    pub label: String,
    pub source: String,
    pub artifact_kind: String,
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relative_path: Option<String>,
    pub present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bytes: Option<u64>,
    pub retention_class: String,
    pub review_status: String,
    pub recommended_action: String,
    pub requested_action: String,
    pub reason: String,
    pub why_it_matters: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub refs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approval_brief: Option<ApprovalBrief>,
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
                .is_none_or(|fresh_until| fresh_until >= now)
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
    pub metadata: Option<ActionApprovalMetadata>,
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
            metadata: None,
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
    action_id: &'a str,
    status: ApprovalStatus,
    result: ApprovalStatus,
    scope: ApprovalScope,
    project_key: &'a str,
    request_id: &'a str,
    task_id: &'a str,
    action: &'a str,
    risk_level: RiskLevel,
    approval_mode: ApprovalMode,
    source_surface: &'a str,
    preview: &'a str,
    reason: &'a str,
    created_at: DateTime<Utc>,
    expires_at: DateTime<Utc>,
    resolved_at: Option<DateTime<Utc>>,
    resolved_by: Option<&'a str>,
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

#[derive(Debug)]
pub struct ApprovalLedgerSession {
    ledger: ApprovalLedger,
    bounded_root: Option<File>,
    bounded_root_identity: Option<(u64, u64)>,
    _lock: ApprovalLedgerLockGuard,
    snapshot: ApprovalLedgerSnapshot,
    approvals: Vec<PendingActionApproval>,
    lookup: HashMap<ApprovalLookupKey, Vec<usize>>,
    transitions: Vec<QueuedApprovalTransition>,
    dirty: bool,
    expired_through: DateTime<Utc>,
}

#[derive(Debug)]
struct ApprovalLedgerLockGuard {
    file: File,
}

impl Drop for ApprovalLedgerLockGuard {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ApprovalLedgerSnapshot {
    exists: bool,
    content_sha256: String,
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

    fn lock_path(&self) -> PathBuf {
        self.root.join(APPROVALS_LOCK_FILE)
    }

    pub fn load(&self) -> Result<Vec<PendingActionApproval>> {
        Ok(read_approvals_with_snapshot(&self.approvals_path())?.0)
    }

    #[cfg(test)]
    pub fn save(&self, approvals: &[PendingActionApproval]) -> Result<()> {
        let _lock = self.acquire_lock()?;
        let (_, snapshot) = read_approvals_with_snapshot(&self.approvals_path())?;
        self.replace_approvals_locked(&snapshot, approvals)?;
        Ok(())
    }

    pub fn begin_session(
        &self,
        now: DateTime<Utc>,
    ) -> Result<(ApprovalLedgerSession, Vec<PendingActionApproval>)> {
        let lock = self.acquire_lock()?;
        let (mut approvals, snapshot) = read_approvals_with_snapshot(&self.approvals_path())?;
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
                bounded_root: None,
                bounded_root_identity: None,
                _lock: lock,
                snapshot,
                lookup: build_approval_lookup(&approvals),
                approvals,
                transitions,
                dirty,
                expired_through: now,
            },
            expired,
        ))
    }

    pub(crate) fn begin_bounded_session(
        &self,
        now: DateTime<Utc>,
    ) -> Result<(ApprovalLedgerSession, Vec<PendingActionApproval>)> {
        let root = crate::process::open_bounded_root(&self.root)
            .with_context(|| format!("open approval root {}", self.root.display()))?;
        let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("approval root lacks a stable filesystem identity")?;
        let lock_file = crate::process::open_or_create_bounded_regular_file(
            &root,
            Path::new(APPROVALS_LOCK_FILE),
        )?;
        ensure_owner_only(&lock_file, &self.lock_path())?;
        lock_file
            .lock_exclusive()
            .with_context(|| format!("lock approval ledger {}", self.lock_path().display()))?;
        ensure_root_identity(&self.root, root_identity)?;
        let (mut approvals, snapshot) = read_approvals_with_snapshot_bounded(&root)?;
        ensure_root_identity(&self.root, root_identity)?;
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
                bounded_root: Some(root),
                bounded_root_identity: Some(root_identity),
                _lock: ApprovalLedgerLockGuard { file: lock_file },
                snapshot,
                lookup: build_approval_lookup(&approvals),
                approvals,
                transitions,
                dirty,
                expired_through: now,
            },
            expired,
        ))
    }

    fn acquire_lock(&self) -> Result<ApprovalLedgerLockGuard> {
        fs::create_dir_all(&self.root)
            .with_context(|| format!("create approval root {}", self.root.display()))?;
        let lock_path = self.lock_path();
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        options.mode(0o600);
        let file = options
            .open(&lock_path)
            .with_context(|| format!("open approval lock {}", lock_path.display()))?;
        ensure_owner_only(&file, &lock_path)?;
        file.lock_exclusive()
            .with_context(|| format!("lock approval ledger {}", lock_path.display()))?;
        Ok(ApprovalLedgerLockGuard { file })
    }

    fn mutate<T>(
        &self,
        mutation: impl FnOnce(
            &mut Vec<PendingActionApproval>,
            &mut Vec<QueuedApprovalTransition>,
        ) -> Result<(T, bool)>,
    ) -> Result<T> {
        let _lock = self.acquire_lock()?;
        let (mut approvals, mut snapshot) = read_approvals_with_snapshot(&self.approvals_path())?;
        let mut transitions = Vec::new();
        let (value, dirty) = mutation(&mut approvals, &mut transitions)?;
        // Direct commands have always materialized an empty ledger on first use.
        if dirty || !snapshot.exists {
            snapshot = self.replace_approvals_locked(&snapshot, &approvals)?;
        }
        if !transitions.is_empty() {
            self.verify_snapshot_locked(&snapshot)?;
            self.append_queued_transitions_locked(&transitions)?;
        }
        Ok(value)
    }

    fn replace_approvals_locked(
        &self,
        expected: &ApprovalLedgerSnapshot,
        approvals: &[PendingActionApproval],
    ) -> Result<ApprovalLedgerSnapshot> {
        verify_approval_snapshot(&self.approvals_path(), expected)?;
        let bytes = serde_json::to_vec_pretty(approvals)?;
        atomic_replace_owner_only(&self.approvals_path(), &bytes)?;
        let (reloaded, snapshot) = read_approvals_with_snapshot(&self.approvals_path())?;
        if reloaded != approvals {
            bail!("durable approval replacement did not re-read as the committed state");
        }
        Ok(snapshot)
    }

    fn verify_snapshot_locked(&self, expected: &ApprovalLedgerSnapshot) -> Result<()> {
        verify_approval_snapshot(&self.approvals_path(), expected)
    }

    pub fn evaluate_action(
        &self,
        request: ActionApprovalRequest,
        brief: Option<&ExecutionBrief>,
        now: DateTime<Utc>,
    ) -> Result<ApprovalDecision> {
        self.mutate(|approvals, transitions| {
            let expired = expire_due_collect(approvals, now);
            let mut dirty = !expired.is_empty();
            transitions.extend(
                expired
                    .into_iter()
                    .map(|approval| QueuedApprovalTransition {
                        transition: "expire",
                        approval,
                        detail: "expired".to_string(),
                    }),
            );

            if let Some(approval_mode) = approvals
                .iter()
                .find(|approval| approved_session_grant_covers(approval, &request, now))
                .map(|approval| approval.approval_mode)
            {
                return Ok((ApprovalDecision::Proceed(approval_mode), dirty));
            }

            if let Some(index) = approvals
                .iter()
                .position(|approval| approved_once_grant_covers(approval, &request, now))
            {
                let approval_mode = approvals[index].approval_mode;
                approvals[index].status = ApprovalStatus::Superseded;
                let consumed = approvals[index].clone();
                dirty = true;
                transitions.push(QueuedApprovalTransition {
                    transition: "supersede",
                    approval: consumed,
                    detail: "consumed".to_string(),
                });
                return Ok((ApprovalDecision::Proceed(approval_mode), dirty));
            }

            if let Some(pending) = approvals
                .iter()
                .find(|approval| pending_approval_covers(approval, &request, now))
                .cloned()
            {
                return Ok((ApprovalDecision::Pending(Box::new(pending)), dirty));
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
                    let approval = pending_approval_from_request(request, now);
                    approvals.push(approval.clone());
                    dirty = true;
                    transitions.push(QueuedApprovalTransition {
                        transition: "pending",
                        approval: approval.clone(),
                        detail: "created".to_string(),
                    });
                    ApprovalDecision::Pending(Box::new(approval))
                }
            };

            Ok((decision, dirty))
        })
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
        self.mutate(|approvals, transitions| {
            let expired = expire_due_collect(approvals, now);
            transitions.extend(
                expired
                    .iter()
                    .cloned()
                    .map(|approval| QueuedApprovalTransition {
                        transition: "expire",
                        approval,
                        detail: "expired".to_string(),
                    }),
            );
            let dirty = !expired.is_empty();
            Ok((expired, dirty))
        })
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
        let resolved_by = operator_safe_text(&resolved_by.into());
        self.mutate(|approvals, transitions| {
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
            transitions.extend(superseded.iter().cloned().map(|approval| {
                QueuedApprovalTransition {
                    transition: "supersede_denied",
                    approval,
                    detail: "new_approval_retry".to_string(),
                }
            }));
            let dirty = !superseded.is_empty();
            Ok((superseded, dirty))
        })
    }

    fn resolve_pending(
        &self,
        approval_id: Option<&str>,
        status: ApprovalStatus,
        resolved_by: String,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
        self.mutate(|approvals, transitions| {
            let expired = expire_due_collect(approvals, now);
            let mut dirty = !expired.is_empty();
            transitions.extend(
                expired
                    .into_iter()
                    .map(|approval| QueuedApprovalTransition {
                        transition: "expire",
                        approval,
                        detail: "expired".to_string(),
                    }),
            );
            let target_index = match approval_id {
                Some(approval_id) => approvals.iter().position(|approval| {
                    approval.status == ApprovalStatus::Pending
                        && approval.approval_id == approval_id
                }),
                None => approvals
                    .iter()
                    .enumerate()
                    .filter(|(_, approval)| approval.status == ApprovalStatus::Pending)
                    .min_by_key(|(_, approval)| approval.created_at)
                    .map(|(index, _)| index),
            };

            let Some(target_index) = target_index else {
                return Ok((None, dirty));
            };
            let approval = &mut approvals[target_index];
            approval.status = status;
            approval.resolved_at = Some(now);
            approval.resolved_by = Some(operator_safe_text(&resolved_by));
            let resolved = approval.clone();
            let transition = match status {
                ApprovalStatus::Approved => "approve",
                ApprovalStatus::Denied => "deny",
                _ => "resolve",
            };
            transitions.push(QueuedApprovalTransition {
                transition,
                approval: resolved.clone(),
                detail: transition.to_string(),
            });
            dirty = true;
            Ok((Some(resolved), dirty))
        })
    }

    fn append_queued_transitions_locked(
        &self,
        transitions: &[QueuedApprovalTransition],
    ) -> Result<()> {
        if transitions.is_empty() {
            return Ok(());
        }
        fs::create_dir_all(&self.root)?;
        let path = self.action_audit_path();
        let existed = path.exists();
        let mut bytes = Vec::new();
        for transition in transitions {
            let approval = &transition.approval;
            let entry = ActionAuditEntry {
                transition: transition.transition,
                approval_id: &approval.approval_id,
                action_id: approval.action_id(),
                status: approval.status,
                result: approval.status,
                scope: approval.scope,
                project_key: &approval.project_key,
                request_id: &approval.request_id,
                task_id: &approval.task_id,
                action: &approval.action,
                risk_level: approval.risk_level,
                approval_mode: approval.approval_mode,
                source_surface: &approval.source_surface,
                preview: &approval.preview,
                reason: &approval.reason,
                created_at: approval.created_at,
                expires_at: approval.expires_at,
                resolved_at: approval.resolved_at,
                resolved_by: approval.resolved_by.as_deref(),
                detail: &transition.detail,
                recorded_at: Utc::now(),
            };
            serde_json::to_writer(&mut bytes, &entry)?;
            bytes.push(b'\n');
        }
        let mut options = OpenOptions::new();
        options.create(true).append(true);
        #[cfg(unix)]
        options.mode(0o600);
        let mut file = options
            .open(&path)
            .with_context(|| format!("open approval audit {}", path.display()))?;
        ensure_owner_only(&file, &path)?;
        file.write_all(&bytes)
            .with_context(|| format!("append approval audit {}", path.display()))?;
        file.sync_all()
            .with_context(|| format!("sync approval audit {}", path.display()))?;
        if !existed {
            sync_parent_directory(&path)?;
        }
        Ok(())
    }
}

pub(crate) fn load_approvals_bounded_read_only(
    root_path: &Path,
) -> Result<(Vec<PendingActionApproval>, String, (u64, u64))> {
    let root = crate::process::open_bounded_root(root_path)
        .with_context(|| format!("open approval root {}", root_path.display()))?;
    let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
        .context("approval root lacks a stable filesystem identity")?;
    let (approvals, snapshot) = read_approvals_with_snapshot_bounded(&root)?;
    verify_approval_snapshot_bounded(&root, &snapshot)?;
    let active_metadata = fs::symlink_metadata(root_path)
        .with_context(|| format!("reinspect approval root {}", root_path.display()))?;
    if active_metadata.file_type().is_symlink()
        || !active_metadata.is_dir()
        || crate::process::filesystem_file_identity(&active_metadata) != Some(root_identity)
        || crate::process::filesystem_file_identity(&root.metadata()?) != Some(root_identity)
    {
        bail!("approval root changed during bounded read-only load");
    }
    Ok((approvals, snapshot.content_sha256, root_identity))
}

impl ApprovalLedgerSession {
    pub(crate) fn bounded_root_identity(&self) -> Option<(u64, u64)> {
        self.bounded_root_identity
    }

    pub(crate) fn ensure_bounded_root_active(&self) -> Result<()> {
        if let Some(identity) = self.bounded_root_identity {
            ensure_root_identity(&self.ledger.root, identity)?;
        }
        Ok(())
    }

    pub fn approvals(&self) -> &[PendingActionApproval] {
        &self.approvals
    }

    pub fn approval(&self, approval_id: &str) -> Option<&PendingActionApproval> {
        self.approvals
            .iter()
            .find(|approval| approval.approval_id == approval_id)
    }

    /// Resolve only the exact version inspected by the caller under this ledger lock.
    pub(crate) fn approve_exact_pending(
        &mut self,
        expected: &PendingActionApproval,
        resolved_by: &str,
        now: DateTime<Utc>,
    ) -> Result<PendingActionApproval> {
        self.ensure_bounded_root_active()?;
        let resolved_by = operator_safe_text(resolved_by.trim());
        anyhow::ensure!(
            !resolved_by.is_empty() && resolved_by.len() <= 256,
            "operator identity must contain 1 to 256 bytes"
        );
        let approval = self
            .approvals
            .iter_mut()
            .find(|a| a.approval_id == expected.approval_id)
            .context("reviewed approval disappeared")?;
        anyhow::ensure!(
            approval == expected,
            "reviewed approval changed before confirmation"
        );
        anyhow::ensure!(
            approval.status == ApprovalStatus::Pending
                && approval.created_at <= now
                && approval.expires_at >= now,
            "reviewed approval is not fresh and pending"
        );
        approval.status = ApprovalStatus::Approved;
        approval.resolved_at = Some(now);
        approval.resolved_by = Some(resolved_by);
        let resolved = approval.clone();
        self.dirty = true;
        self.transitions.push(QueuedApprovalTransition {
            transition: "approve",
            approval: resolved.clone(),
            detail: "approve_exact_local_review".to_string(),
        });
        Ok(resolved)
    }

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
                let approval = pending_approval_from_request(request, now);
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

    pub fn ensure_pending_without_consuming_grant(
        &mut self,
        request: ActionApprovalRequest,
        now: DateTime<Utc>,
    ) -> Result<Option<PendingActionApproval>> {
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

        if let Some(existing) = candidate_indices
            .iter()
            .map(|&index| &self.approvals[index])
            .find(|approval| pending_approval_covers(approval, &request, now))
        {
            return Ok(Some(existing.clone()));
        }

        if candidate_indices
            .iter()
            .map(|&index| &self.approvals[index])
            .any(|approval| {
                denied_approval_covers(approval, &request)
                    || approved_once_grant_covers(approval, &request, now)
                    || approved_session_grant_covers(approval, &request, now)
            })
        {
            return Ok(None);
        }

        let approval = pending_approval_from_request(request, now);
        let index = self.approvals.len();
        self.lookup.entry(lookup_key).or_default().push(index);
        self.approvals.push(approval.clone());
        self.dirty = true;
        self.transitions.push(QueuedApprovalTransition {
            transition: "pending",
            approval: approval.clone(),
            detail: "created".to_string(),
        });
        Ok(Some(approval))
    }

    pub fn approved_provider_fallbacks(&self, now: DateTime<Utc>) -> Vec<PendingActionApproval> {
        self.approvals
            .iter()
            .filter(|approval| {
                approval.status == ApprovalStatus::Approved
                    && approval.expires_at >= now
                    && approval
                        .metadata
                        .as_ref()
                        .and_then(ActionApprovalMetadata::as_provider_fallback)
                        .is_some()
            })
            .cloned()
            .collect()
    }

    pub fn supersede_approval(
        &mut self,
        approval_id: &str,
        detail: impl Into<String>,
    ) -> Option<PendingActionApproval> {
        let index = self
            .approvals
            .iter()
            .position(|approval| approval.approval_id == approval_id)?;
        if self.approvals[index].status == ApprovalStatus::Superseded {
            return Some(self.approvals[index].clone());
        }

        self.approvals[index].status = ApprovalStatus::Superseded;
        let approval = self.approvals[index].clone();
        self.dirty = true;
        self.transitions.push(QueuedApprovalTransition {
            transition: "supersede",
            approval: approval.clone(),
            detail: detail.into(),
        });
        Some(approval)
    }

    pub fn flush(&mut self) -> Result<()> {
        self.ensure_bounded_root_active()?;
        if self.dirty {
            self.snapshot = match self.bounded_root.as_ref() {
                Some(root) => {
                    replace_approvals_locked_bounded(root, &self.snapshot, &self.approvals)?
                }
                None => self
                    .ledger
                    .replace_approvals_locked(&self.snapshot, &self.approvals)?,
            };
            self.dirty = false;
        }
        if !self.transitions.is_empty() {
            match self.bounded_root.as_ref() {
                Some(root) => {
                    verify_approval_snapshot_bounded(root, &self.snapshot)?;
                    append_queued_transitions_bounded(root, &self.transitions)?;
                }
                None => {
                    self.ledger.verify_snapshot_locked(&self.snapshot)?;
                    self.ledger
                        .append_queued_transitions_locked(&self.transitions)?;
                }
            }
            self.transitions.clear();
        }
        self.ensure_bounded_root_active()?;
        Ok(())
    }
}

fn pending_approval_from_request(
    request: ActionApprovalRequest,
    now: DateTime<Utc>,
) -> PendingActionApproval {
    PendingActionApproval {
        approval_id: format!("approval_{}", Uuid::new_v4()),
        action_id: format!("action_{}", Uuid::new_v4()),
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
        metadata: request.metadata,
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

fn provider_model_label(provider_id: &str, model: Option<&str>) -> String {
    match model
        .map(operator_safe_text)
        .filter(|model| !model.is_empty())
    {
        Some(model) => format!("{} model {}", operator_safe_text(provider_id), model),
        None => format!("{} model -", operator_safe_text(provider_id)),
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

fn read_approvals_with_snapshot(
    path: &Path,
) -> Result<(Vec<PendingActionApproval>, ApprovalLedgerSnapshot)> {
    let (exists, bytes) = read_approval_bytes(path)?;
    let snapshot = approval_snapshot(exists, &bytes);
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Ok((Vec::new(), snapshot));
    }
    let approvals = serde_json::from_slice(&bytes)
        .with_context(|| format!("parse approval ledger {}", path.display()))?;
    Ok((approvals, snapshot))
}

fn read_approvals_with_snapshot_bounded(
    root: &File,
) -> Result<(Vec<PendingActionApproval>, ApprovalLedgerSnapshot)> {
    let (exists, bytes) = read_approval_bytes_bounded(root)?;
    let snapshot = approval_snapshot(exists, &bytes);
    if bytes.iter().all(u8::is_ascii_whitespace) {
        return Ok((Vec::new(), snapshot));
    }
    let approvals = serde_json::from_slice(&bytes).context("parse bounded approval ledger")?;
    Ok((approvals, snapshot))
}

fn read_approval_bytes_bounded(root: &File) -> Result<(bool, Vec<u8>)> {
    let mut file = match crate::process::open_bounded_regular_file(root, Path::new(APPROVALS_FILE))
    {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok((false, Vec::new())),
        Err(error) => return Err(error).context("open bounded approval ledger"),
    };
    ensure_owner_only(&file, Path::new(APPROVALS_FILE))?;
    let initial_metadata = file.metadata().context("inspect bounded approval ledger")?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(MAX_APPROVAL_LEDGER_BYTES + 1)
        .read_to_end(&mut bytes)
        .context("read bounded approval ledger")?;
    if bytes.len() as u64 > MAX_APPROVAL_LEDGER_BYTES {
        bail!("bounded approval ledger exceeds its read budget");
    }
    let final_metadata = file
        .metadata()
        .context("reinspect bounded approval ledger")?;
    let active = crate::process::open_bounded_regular_file(root, Path::new(APPROVALS_FILE))
        .context("reopen active bounded approval ledger")?;
    let active_metadata = active
        .metadata()
        .context("inspect active bounded approval ledger")?;
    if crate::process::filesystem_file_identity(&initial_metadata)
        != crate::process::filesystem_file_identity(&final_metadata)
        || crate::process::filesystem_file_identity(&final_metadata)
            != crate::process::filesystem_file_identity(&active_metadata)
        || initial_metadata.len() != final_metadata.len()
        || final_metadata.len() != active_metadata.len()
        || initial_metadata.modified().ok() != final_metadata.modified().ok()
        || final_metadata.modified().ok() != active_metadata.modified().ok()
    {
        bail!("bounded approval ledger changed during read");
    }
    Ok((true, bytes))
}

fn read_approval_bytes(path: &Path) -> Result<(bool, Vec<u8>)> {
    match fs::read(path) {
        Ok(bytes) => Ok((true, bytes)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok((false, Vec::new())),
        Err(error) => {
            Err(error).with_context(|| format!("read approval ledger {}", path.display()))
        }
    }
}

fn approval_snapshot(exists: bool, bytes: &[u8]) -> ApprovalLedgerSnapshot {
    let mut hasher = Sha256::new();
    hasher.update(APPROVAL_SNAPSHOT_HASH_DOMAIN);
    hasher.update([u8::from(exists)]);
    hasher.update((bytes.len() as u64).to_be_bytes());
    hasher.update(bytes);
    ApprovalLedgerSnapshot {
        exists,
        content_sha256: format!("{:x}", hasher.finalize()),
    }
}

fn verify_approval_snapshot(path: &Path, expected: &ApprovalLedgerSnapshot) -> Result<()> {
    let (exists, bytes) = read_approval_bytes(path)?;
    if approval_snapshot(exists, &bytes) != *expected {
        bail!(
            "approval ledger changed after the exclusive session reload: {}",
            path.display()
        );
    }
    Ok(())
}

fn verify_approval_snapshot_bounded(root: &File, expected: &ApprovalLedgerSnapshot) -> Result<()> {
    let (exists, bytes) = read_approval_bytes_bounded(root)?;
    if approval_snapshot(exists, &bytes) != *expected {
        bail!("bounded approval ledger changed after the exclusive session reload");
    }
    Ok(())
}

fn replace_approvals_locked_bounded(
    root: &File,
    expected: &ApprovalLedgerSnapshot,
    approvals: &[PendingActionApproval],
) -> Result<ApprovalLedgerSnapshot> {
    verify_approval_snapshot_bounded(root, expected)?;
    let bytes = serde_json::to_vec_pretty(approvals)?;
    let temporary_name = format!(".{APPROVALS_FILE}.{}.tmp", Uuid::new_v4());
    let temporary_path = Path::new(&temporary_name);
    let result = (|| -> Result<()> {
        let mut file = crate::process::create_new_bounded_regular_file(root, temporary_path)
            .context("create bounded approval temp")?;
        ensure_owner_only(&file, temporary_path)?;
        file.write_all(&bytes)
            .context("write bounded approval temp")?;
        file.sync_all().context("sync bounded approval temp")?;
        drop(file);
        crate::process::replace_bounded_root_file(root, temporary_path, Path::new(APPROVALS_FILE))
            .context("atomically replace bounded approval ledger")
    })();
    if result.is_err() {
        let _ = crate::process::remove_bounded_root_file(root, temporary_path);
    }
    result?;
    let (reloaded, snapshot) = read_approvals_with_snapshot_bounded(root)?;
    if reloaded != approvals {
        bail!("durable bounded approval replacement did not re-read committed state");
    }
    Ok(snapshot)
}

fn append_queued_transitions_bounded(
    root: &File,
    transitions: &[QueuedApprovalTransition],
) -> Result<()> {
    if transitions.is_empty() {
        return Ok(());
    }
    let bytes = action_audit_bytes(transitions)?;
    let existed =
        crate::process::open_bounded_regular_file(root, Path::new(ACTION_AUDIT_FILE)).is_ok();
    let mut file =
        crate::process::open_or_create_bounded_regular_file(root, Path::new(ACTION_AUDIT_FILE))
            .context("open bounded approval audit")?;
    ensure_owner_only(&file, Path::new(ACTION_AUDIT_FILE))?;
    file.seek(SeekFrom::End(0))
        .context("seek bounded approval audit")?;
    file.write_all(&bytes)
        .context("append bounded approval audit")?;
    file.sync_all().context("sync bounded approval audit")?;
    if !existed {
        root.sync_all().context("sync bounded approval root")?;
    }
    Ok(())
}

fn action_audit_bytes(transitions: &[QueuedApprovalTransition]) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    for transition in transitions {
        let approval = &transition.approval;
        let entry = ActionAuditEntry {
            transition: transition.transition,
            approval_id: &approval.approval_id,
            action_id: approval.action_id(),
            status: approval.status,
            result: approval.status,
            scope: approval.scope,
            project_key: &approval.project_key,
            request_id: &approval.request_id,
            task_id: &approval.task_id,
            action: &approval.action,
            risk_level: approval.risk_level,
            approval_mode: approval.approval_mode,
            source_surface: &approval.source_surface,
            preview: &approval.preview,
            reason: &approval.reason,
            created_at: approval.created_at,
            expires_at: approval.expires_at,
            resolved_at: approval.resolved_at,
            resolved_by: approval.resolved_by.as_deref(),
            detail: &transition.detail,
            recorded_at: Utc::now(),
        };
        serde_json::to_writer(&mut bytes, &entry)?;
        bytes.push(b'\n');
    }
    Ok(bytes)
}

fn ensure_root_identity(path: &Path, expected: (u64, u64)) -> Result<()> {
    let reopened = crate::process::open_bounded_root(path)
        .with_context(|| format!("reopen approval root {}", path.display()))?;
    if crate::process::filesystem_file_identity(&reopened.metadata()?) != Some(expected) {
        bail!("active profile root was replaced while approval lock was held");
    }
    Ok(())
}

fn atomic_replace_owner_only(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(APPROVALS_FILE);
    let temporary = path.with_file_name(format!(".{file_name}.{}.tmp", Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options.mode(0o600);
        let mut file = options
            .open(&temporary)
            .with_context(|| format!("create approval temp {}", temporary.display()))?;
        file.write_all(bytes)
            .with_context(|| format!("write approval temp {}", temporary.display()))?;
        file.sync_all()
            .with_context(|| format!("sync approval temp {}", temporary.display()))?;
        drop(file);
        fs::rename(&temporary, path).with_context(|| {
            format!(
                "atomically replace approval ledger {} from {}",
                path.display(),
                temporary.display()
            )
        })?;
        sync_parent_directory(path)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}

fn sync_parent_directory(path: &Path) -> Result<()> {
    let parent = path
        .parent()
        .context("approval persistence path has no parent directory")?;
    #[cfg(unix)]
    {
        File::open(parent)
            .with_context(|| format!("open approval parent {}", parent.display()))?
            .sync_all()
            .with_context(|| format!("sync approval parent {}", parent.display()))?;
    }
    #[cfg(not(unix))]
    {
        let _ = parent;
    }
    Ok(())
}

fn ensure_owner_only(file: &File, path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        let permissions = file
            .metadata()
            .with_context(|| format!("read approval permissions {}", path.display()))?
            .permissions();
        if permissions.mode() & 0o777 != 0o600 {
            file.set_permissions(fs::Permissions::from_mode(0o600))
                .with_context(|| format!("set owner-only permissions {}", path.display()))?;
            file.sync_all()
                .with_context(|| format!("sync owner-only permissions {}", path.display()))?;
        }
    }
    #[cfg(not(unix))]
    {
        let _ = (file, path);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{
        ProviderCapacityStatus, ProviderFallbackApplyScope, ProviderFallbackAuthStatus,
        ProviderFallbackSource,
    };
    use std::sync::{Arc, Barrier};
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
            action_id: format!("action_{approval_id}"),
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
            metadata: None,
        }
    }

    fn provider_fallback_metadata(now: DateTime<Utc>) -> ActionApprovalMetadata {
        ActionApprovalMetadata::ProviderFallback(Box::new(ProviderFallbackApprovalMetadata {
            current_provider_id: "openai".to_string(),
            current_model: Some("gpt-4.1".to_string()),
            runner_role: "worker".to_string(),
            generated_at: now,
            candidate_limit: 3,
            candidates: vec![ProviderFallbackCandidate {
                provider_id: "openai".to_string(),
                model: Some("gpt-4.1-mini".to_string()),
                source: ProviderFallbackSource::SameProviderModel,
                auth_status: ProviderFallbackAuthStatus::Available,
                capacity_status: ProviderCapacityStatus::Available,
                recommended: true,
                reason: "same provider fallback model".to_string(),
            }],
            apply_scope: ProviderFallbackApplyScope::RequestMatchingProviderModel,
            approval_brief: None,
        }))
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
        assert!(ledger.approvals_path().is_file());
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
        assert!(approval.action_id.starts_with("action_"));
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
        assert!(audit.contains("\"action_id\":\"action_"));
        assert!(audit.contains("\"result\":\"approved\""));
        assert!(audit.contains("\"resolved_by\":\"operator\""));
        Ok(())
    }

    #[test]
    fn legacy_approval_without_action_id_uses_approval_id_fallback() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        fs::create_dir_all(temp.path())?;
        fs::write(
            ledger.approvals_path(),
            serde_json::to_string_pretty(&serde_json::json!([
                {
                    "approval_id": "approval_legacy",
                    "status": "pending",
                    "scope": "once",
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "task",
                    "action": "dispatch",
                    "risk_level": "runtime_mutation",
                    "approval_mode": "operator_required",
                    "preview": "",
                    "reason": "",
                    "created_at": now,
                    "expires_at": now + Duration::minutes(10),
                    "source_surface": "legacy"
                }
            ]))?,
        )?;

        let resolved = ledger
            .approve_pending(
                Some("approval_legacy"),
                "operator",
                now + Duration::seconds(1),
            )?
            .expect("approval");

        assert!(resolved.action_id.is_empty());
        assert_eq!(resolved.action_id(), "approval_legacy");
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert!(audit.contains("\"action_id\":\"approval_legacy\""));
        Ok(())
    }

    #[test]
    fn legacy_approval_without_metadata_still_evaluates() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        fs::create_dir_all(temp.path())?;
        fs::write(
            ledger.approvals_path(),
            serde_json::to_string_pretty(&serde_json::json!([
                {
                    "approval_id": "approval_legacy_metadata",
                    "action_id": "action_legacy_metadata",
                    "status": "approved",
                    "scope": "once",
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "task",
                    "action": "dispatch",
                    "risk_level": "runtime_mutation",
                    "approval_mode": "operator_required",
                    "preview": "",
                    "reason": "",
                    "created_at": now,
                    "expires_at": now + Duration::minutes(10),
                    "resolved_at": now,
                    "resolved_by": "operator",
                    "source_surface": "legacy"
                }
            ]))?,
        )?;

        let decision = ledger.evaluate_action(
            request(RiskLevel::RuntimeMutation),
            None,
            now + Duration::seconds(1),
        )?;

        assert_eq!(
            decision,
            ApprovalDecision::Proceed(ApprovalMode::OperatorRequired)
        );
        assert_eq!(ledger.load()?[0].status, ApprovalStatus::Superseded);
        Ok(())
    }

    #[test]
    fn provider_fallback_approval_does_not_authorize_runtime_dispatch() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let mut fallback_approval = approval(
            "approved_provider_fallback",
            ApprovalStatus::Approved,
            "project",
            "request",
            "task",
            "dispatch.provider_fallback",
            now,
        );
        fallback_approval.metadata = Some(provider_fallback_metadata(now));
        ledger.save(&[fallback_approval])?;

        let mut runtime_request = ActionApprovalRequest::new(
            "project",
            "request",
            "task",
            "dispatch.runtime",
            RiskLevel::RuntimeMutation,
        );
        runtime_request.mutation_class = Some("dispatch.runtime".to_string());
        let decision = ledger.evaluate_action(runtime_request, None, now + Duration::seconds(1))?;

        let ApprovalDecision::Pending(runtime_approval) = decision else {
            panic!("expected runtime dispatch to require its own approval");
        };
        assert_eq!(runtime_approval.action, "dispatch.runtime");
        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 2);
        assert_eq!(approvals[0].status, ApprovalStatus::Approved);
        assert_eq!(approvals[0].action, "dispatch.provider_fallback");
        assert_eq!(approvals[1].action, "dispatch.runtime");
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
    fn provider_fallback_pending_approval_is_reused_for_repeated_capacity_block() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let (mut session, _) = ledger.begin_session(now)?;
        let mut request = ActionApprovalRequest::new(
            "project",
            "request",
            "task",
            "dispatch.provider_fallback",
            RiskLevel::RuntimeMutation,
        );
        request.mutation_class = Some("dispatch.provider_fallback".to_string());
        request.metadata = Some(provider_fallback_metadata(now));

        let first = session
            .ensure_pending_without_consuming_grant(request.clone(), now)?
            .expect("first approval");
        let second = session
            .ensure_pending_without_consuming_grant(request, now + Duration::seconds(1))?
            .expect("reused approval");

        assert_eq!(first.approval_id, second.approval_id);
        session.flush()?;
        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 1);
        assert_eq!(approvals[0].action, "dispatch.provider_fallback");
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

    #[test]
    fn ledger_session_holds_the_exclusive_lock_until_drop() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let (session, _) = ledger.begin_session(Utc::now())?;
        let contender = OpenOptions::new()
            .read(true)
            .write(true)
            .open(ledger.lock_path())?;

        let error = contender
            .try_lock_exclusive()
            .expect_err("session must retain the exclusive approval lock");
        assert_eq!(error.kind(), ErrorKind::WouldBlock);

        drop(session);
        contender.try_lock_exclusive()?;
        FileExt::unlock(&contender)?;
        Ok(())
    }

    #[test]
    fn concurrent_direct_writers_preserve_both_pending_approvals() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let barrier = Arc::new(Barrier::new(3));
        let now = Utc::now();
        let mut handles = Vec::new();

        for task_id in ["first", "second"] {
            let ledger = ledger.clone();
            let barrier = Arc::clone(&barrier);
            let task_id = task_id.to_string();
            handles.push(std::thread::spawn(move || -> Result<()> {
                let mut request = request(RiskLevel::RuntimeMutation);
                request.task_id = task_id;
                barrier.wait();
                assert!(matches!(
                    ledger.evaluate_action(request, None, now)?,
                    ApprovalDecision::Pending(_)
                ));
                Ok(())
            }));
        }

        barrier.wait();
        for handle in handles {
            handle.join().expect("approval writer thread panicked")?;
        }

        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 2);
        assert!(approvals.iter().any(|approval| approval.task_id == "first"));
        assert!(approvals
            .iter()
            .any(|approval| approval.task_id == "second"));
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert_eq!(audit.matches("\"transition\":\"pending\"").count(), 2);
        Ok(())
    }

    #[test]
    fn concurrent_evaluation_resolution_and_expiration_preserve_all_changes() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let to_resolve = approval(
            "to_resolve",
            ApprovalStatus::Pending,
            "project",
            "request",
            "resolve-task",
            "dispatch",
            now,
        );
        let mut to_expire = approval(
            "to_expire",
            ApprovalStatus::Pending,
            "project",
            "request",
            "expire-task",
            "dispatch",
            now,
        );
        to_expire.expires_at = now + Duration::seconds(1);
        ledger.save(&[to_resolve, to_expire])?;

        let barrier = Arc::new(Barrier::new(4));
        let resolver = {
            let ledger = ledger.clone();
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || -> Result<()> {
                barrier.wait();
                let resolved = ledger.approve_pending(
                    Some("to_resolve"),
                    "operator",
                    now + Duration::seconds(2),
                )?;
                assert!(resolved.is_some());
                Ok(())
            })
        };
        let expirer = {
            let ledger = ledger.clone();
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || -> Result<()> {
                barrier.wait();
                ledger.expire_due(now + Duration::seconds(2))?;
                Ok(())
            })
        };
        let evaluator = {
            let ledger = ledger.clone();
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || -> Result<()> {
                let mut request = request(RiskLevel::RuntimeMutation);
                request.task_id = "new-task".to_string();
                barrier.wait();
                assert!(matches!(
                    ledger.evaluate_action(request, None, now + Duration::seconds(2))?,
                    ApprovalDecision::Pending(_)
                ));
                Ok(())
            })
        };

        barrier.wait();
        for handle in [resolver, expirer, evaluator] {
            handle.join().expect("approval writer thread panicked")?;
        }

        let approvals = ledger.load()?;
        assert_eq!(approvals.len(), 3);
        assert_eq!(approvals[0].status, ApprovalStatus::Approved);
        assert_eq!(approvals[1].status, ApprovalStatus::Expired);
        assert_eq!(approvals[2].status, ApprovalStatus::Pending);
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert_eq!(audit.matches("\"transition\":\"approve\"").count(), 1);
        assert_eq!(audit.matches("\"transition\":\"expire\"").count(), 1);
        assert_eq!(audit.matches("\"transition\":\"pending\"").count(), 1);
        Ok(())
    }

    #[test]
    fn stale_session_snapshot_cannot_overwrite_external_state() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let (mut session, _) = ledger.begin_session(now)?;
        session.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?;

        let external = vec![approval(
            "external",
            ApprovalStatus::Pending,
            "external-project",
            "external-request",
            "external-task",
            "external-action",
            now,
        )];
        fs::write(
            ledger.approvals_path(),
            serde_json::to_vec_pretty(&external)?,
        )?;

        let error = session
            .flush()
            .expect_err("stale session must not replace externally changed state");
        assert!(error
            .to_string()
            .contains("approval ledger changed after the exclusive session reload"));
        assert_eq!(ledger.load()?, external);
        Ok(())
    }

    #[test]
    fn bounded_session_rejects_replaced_profile_root() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().join("profile");
        let detached = temp.path().join("detached-profile");
        fs::create_dir(&root)?;
        #[cfg(unix)]
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
        let ledger = ApprovalLedger::new(&root);
        let now = Utc::now();
        let (mut session, _) = ledger.begin_bounded_session(now)?;
        session.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?;

        fs::rename(&root, &detached)?;
        fs::create_dir(&root)?;
        #[cfg(unix)]
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;

        let error = session
            .flush()
            .expect_err("a bounded approval session must reject a replaced profile root");
        assert!(error
            .to_string()
            .contains("active profile root was replaced"));
        assert!(!root.join(APPROVALS_FILE).exists());
        assert!(!detached.join(APPROVALS_FILE).exists());
        Ok(())
    }

    #[test]
    fn bounded_exact_confirmation_rejects_stale_snapshot_and_preserves_other_pending() -> Result<()>
    {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        let first = approval(
            "first",
            ApprovalStatus::Pending,
            "project",
            "request",
            "task",
            "arm",
            now,
        );
        let second = approval(
            "second",
            ApprovalStatus::Pending,
            "other",
            "request-other",
            "task-other",
            "arm",
            now,
        );
        ledger.save(&[first.clone(), second.clone()])?;
        {
            let (mut session, _) = ledger.begin_bounded_session(now)?;
            let mut stale = first.clone();
            stale.reason = "older-reason".into();
            assert!(session
                .approve_exact_pending(&stale, "operator", now)
                .is_err());
            assert!(session.approve_exact_pending(&first, " ", now).is_err());
            let resolved = session.approve_exact_pending(&first, "local-operator", now)?;
            assert_eq!(resolved.status, ApprovalStatus::Approved);
            assert!(session
                .approve_exact_pending(&first, "operator", now)
                .is_err());
            assert_eq!(session.approval("second"), Some(&second));
            session.flush()?;
        }
        let stored = ledger.load()?;
        assert_eq!(
            stored
                .iter()
                .find(|a| a.approval_id == "first")
                .unwrap()
                .resolved_by
                .as_deref(),
            Some("local-operator")
        );
        assert_eq!(
            stored.iter().find(|a| a.approval_id == "second"),
            Some(&second)
        );
        Ok(())
    }

    #[test]
    fn audit_failure_is_explicit_and_session_flush_can_retry() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let now = Utc::now();
        fs::create_dir_all(ledger.action_audit_path())?;
        let (mut session, _) = ledger.begin_session(now)?;
        session.evaluate_action(request(RiskLevel::RuntimeMutation), None, now)?;

        let error = session
            .flush()
            .expect_err("audit directory must make the append fail");
        assert!(error.to_string().contains("open approval audit"));
        assert_eq!(ledger.load()?.len(), 1);

        fs::remove_dir(ledger.action_audit_path())?;
        session.flush()?;
        let audit = fs::read_to_string(ledger.action_audit_path())?;
        assert_eq!(audit.matches("\"transition\":\"pending\"").count(), 1);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn approval_persistence_files_are_owner_only() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        ledger.evaluate_action(request(RiskLevel::RuntimeMutation), None, Utc::now())?;

        for path in [
            ledger.approvals_path(),
            ledger.action_audit_path(),
            ledger.lock_path(),
        ] {
            fs::set_permissions(&path, fs::Permissions::from_mode(0o644))?;
        }
        let mut second = request(RiskLevel::RuntimeMutation);
        second.task_id = "second".to_string();
        ledger.evaluate_action(second, None, Utc::now())?;

        for path in [
            ledger.approvals_path(),
            ledger.action_audit_path(),
            ledger.lock_path(),
        ] {
            let mode = fs::metadata(&path)?.permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "{} must be owner-only", path.display());
        }
        Ok(())
    }
}
