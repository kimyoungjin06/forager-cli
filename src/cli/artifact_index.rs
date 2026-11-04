//! Project and profile artifact inventory read model.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Args, ValueEnum};
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::io::{Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::time::Instant;
use uuid::Uuid;

use crate::document_lifecycle::{
    retention_approval_metadata_sha256_v2, retention_proposal_sha256_v2,
    scan_project_with_lifecycle, CatalogOptions, DocumentCatalogEntryV2,
    DocumentLifecycleObservationV1, DocumentLifecycleStateV1, LifecycleProjectionStatusV1,
    RetentionApprovalMetadataV2, RetentionDisposition, RetentionProposalV2,
    RetentionRequestedActionV2, RetentionTargetSourceKind, RetentionTargetV2, StrictUtcTimestamp,
    RETENTION_APPROVAL_METADATA_SCHEMA_V2,
};
use crate::offdesk::{
    operator_safe_text, ActionApprovalMetadata, ActionApprovalRequest, ApprovalBrief,
    ApprovalBriefOption, ApprovalLedger, ApprovalMode, ApprovalScope, ApprovalStatus,
    ArtifactRetentionApprovalMetadata, MutationRestorePlan, MutationSnapshotRequest,
    MutationSnapshotStore, MutationSnapshotVerification, PendingActionApproval, RiskLevel,
};
use crate::session::{get_profile_dir, profile_dir_path};

const ARTIFACT_INDEX_SCHEMA: &str = "artifact_index.v1";
const ARTIFACT_INDEX_SCHEMA_V2: &str = "artifact_index.v2";
const ARTIFACT_RETENTION_REVIEW_SCHEMA: &str = "artifact_retention_review.v1";
const ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA_V2: &str =
    "artifact_retention_approval_request.v2";
const ARTIFACT_RETENTION_APPLICATION_SCHEMA: &str = "artifact_retention_application.v1";
const ARTIFACT_RETENTION_APPLICATION_SCHEMA_V2: &str = "artifact_retention_application.v2";
const ARTIFACT_RETENTION_PROMOTION_SCHEMA: &str = "artifact_retention_promotion.v1";
const ARTIFACT_RETENTION_APPLICATIONS_DIR: &str = "artifact_retention_applications";
const ARTIFACT_RETENTION_PROMOTIONS_DIR: &str = "artifact_retention_promotions";
const RETENTION_APPROVAL_ACTION: &str = "maintenance.artifact_cleanup";
const RETENTION_PROMOTE_MUTATION_KIND: &str = "retention.promote_deliverable";
const DELIVERABLE_EXTENSIONS: &[&str] = &[".html", ".png", ".jpg", ".jpeg", ".pdf"];
const OUTPUT_ROOTS: &[&str] = &["outputs", "web", "deliverables", "previews", "gallery"];
const MAX_INDEX_ENTRIES: usize = 240;
const MAX_HUMAN_ROWS: usize = 12;
const MAX_RETENTION_REVIEW_ITEMS: usize = 40;
const MAX_RETENTION_REVIEW_KEEP_SAMPLE: usize = 12;
const MAX_RETENTION_REVIEW_PROJECTION_ITEMS: usize = 10;
const MAX_RETENTION_ARTIFACT_BYTES: u64 = 64 * 1024 * 1024;
const MAX_RETENTION_DISCOVERY_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
const MAX_RETENTION_DISCOVERY_ENTRIES: u64 = 10_000;
const MAX_RETENTION_DISCOVERY_DEPTH: usize = 32;
const MAX_RETENTION_DISCOVERY_MS: u64 = 5_000;
const MAX_RETENTION_DELIVERABLES_BYTES: u64 = 1024 * 1024;
const MAX_RETENTION_RECEIPT_BYTES: u64 = 1024 * 1024;
const MAX_RETENTION_RECEIPT_ENTRIES: u64 = 100_000;
const MAX_RETENTION_RECEIPT_SCAN_MS: u64 = 5_000;

#[derive(Debug, Clone, Args)]
pub struct ProjectArtifactIndexArgs {
    /// Project repository/root directory to scan. Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used to filter profile-local Forager artifacts
    #[arg(long)]
    project_key: Option<String>,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectRetentionReviewArgs {
    /// Project repository/root directory to scan. Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used to filter profile-local Forager artifacts
    #[arg(long)]
    project_key: Option<String>,

    /// Resolve one exact durable document through the retention crosswalk
    #[arg(long)]
    document_id: Option<String>,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectRetentionRequestArgs {
    /// Project repository/root directory to scan. Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used for approval and audit correlation
    #[arg(long)]
    project_key: String,

    /// Existing artifact_index/artifact_retention_review artifact id to request approval for
    #[arg(long)]
    artifact_id: Option<String>,

    /// Artifact path or relative path to request approval for
    #[arg(long = "path")]
    path_filter: Option<String>,

    /// Durable lifecycle document to request retention for
    #[arg(long)]
    document_id: Option<String>,

    /// Exact active summary document required by summary-keep
    #[arg(long)]
    summary_document_id: Option<String>,

    /// Retention action to request approval for
    #[arg(long, value_enum)]
    action: RetentionRequestAction,

    /// Request ID for approval correlation
    #[arg(long, default_value = "retention-review")]
    request_id: String,

    /// Override task ID used for approval deduplication
    #[arg(long)]
    task_id: Option<String>,

    /// Extra operator-safe reason to include in the approval brief
    #[arg(long)]
    reason: Option<String>,

    /// Pending approval TTL in minutes
    #[arg(long, default_value_t = 30)]
    ttl_minutes: i64,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectRetentionApplyArgs {
    /// Project repository/root directory related to the approved retention decision.
    /// Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used to validate the approved retention decision
    #[arg(long)]
    project_key: String,

    /// Approved artifact retention approval ID to consume
    #[arg(long)]
    approval_id: String,

    /// Write the receipt to this path instead of the profile receipt directory
    #[arg(long)]
    receipt_out: Option<PathBuf>,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectRetentionPromoteArgs {
    /// Project repository/root directory whose DELIVERABLES.md should be updated.
    /// Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used to validate the retention application receipt
    #[arg(long)]
    project_key: String,

    /// artifact_retention_application.v1 receipt path to promote from
    #[arg(long, conflicts_with = "approval_id")]
    receipt: Option<PathBuf>,

    /// Find the latest application receipt for this approval ID
    #[arg(long, conflicts_with = "receipt")]
    approval_id: Option<String>,

    /// Override the deliverables entry label
    #[arg(long)]
    title: Option<String>,

    /// Confirm that the artifact preview/provenance and deliverables mutation were reviewed
    #[arg(long)]
    reviewed: bool,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum RetentionRequestAction {
    Keep,
    Preserve,
    #[serde(rename = "summary-keep")]
    SummaryKeep,
    Promote,
    Archive,
    Quarantine,
    Dispose,
}

impl RetentionRequestAction {
    fn as_str(self) -> &'static str {
        match self {
            Self::Keep => "keep",
            Self::Preserve => "preserve",
            Self::SummaryKeep => "summary-keep",
            Self::Promote => "promote",
            Self::Archive => "archive",
            Self::Quarantine => "quarantine",
            Self::Dispose => "dispose",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Keep => "Keep artifact",
            Self::Preserve => "Preserve artifact",
            Self::SummaryKeep => "Keep artifact through summary",
            Self::Promote => "Promote artifact",
            Self::Archive => "Archive artifact",
            Self::Quarantine => "Quarantine artifact",
            Self::Dispose => "Dispose artifact",
        }
    }

    fn risk_level(self) -> RiskLevel {
        match self {
            Self::Archive | Self::Quarantine | Self::Dispose => RiskLevel::Destructive,
            Self::Keep | Self::Preserve | Self::SummaryKeep | Self::Promote => {
                RiskLevel::CanonicalMutation
            }
        }
    }

    fn approval_impact(self) -> &'static str {
        match self {
            Self::Keep => {
                "Records approval to preserve this artifact in the retention plan; it does not edit project files."
            }
            Self::Preserve => {
                "Records reviewed preservation in lifecycle state; it does not copy or move the file."
            }
            Self::SummaryKeep => {
                "Records a reviewed summary replacement; it does not edit or remove either file."
            }
            Self::Promote => {
                "Records approval to promote this artifact in a later reviewed surface update; it does not edit DELIVERABLES.md."
            }
            Self::Archive => {
                "Records approval to prepare an archive step for this artifact; it does not move the file."
            }
            Self::Quarantine => {
                "Records approval to prepare a quarantine step for this artifact; it does not move the file."
            }
            Self::Dispose => {
                "Records approval to prepare a disposal step for this artifact; it does not delete the file."
            }
        }
    }

    fn as_v2(self) -> RetentionRequestedActionV2 {
        match self {
            Self::Keep => RetentionRequestedActionV2::Keep,
            Self::Preserve => RetentionRequestedActionV2::Preserve,
            Self::SummaryKeep => RetentionRequestedActionV2::SummaryKeep,
            Self::Promote => RetentionRequestedActionV2::Promote,
            Self::Archive => RetentionRequestedActionV2::Archive,
            Self::Quarantine => RetentionRequestedActionV2::Quarantine,
            Self::Dispose => RetentionRequestedActionV2::Dispose,
        }
    }

    fn from_v2(action: RetentionRequestedActionV2) -> Self {
        match action {
            RetentionRequestedActionV2::Keep => Self::Keep,
            RetentionRequestedActionV2::Preserve => Self::Preserve,
            RetentionRequestedActionV2::SummaryKeep => Self::SummaryKeep,
            RetentionRequestedActionV2::Promote => Self::Promote,
            RetentionRequestedActionV2::Archive => Self::Archive,
            RetentionRequestedActionV2::Quarantine => Self::Quarantine,
            RetentionRequestedActionV2::Dispose => Self::Dispose,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ArtifactIndexEntryV2 {
    artifact_id: String,
    relative_path: Option<String>,
    present: bool,
    size_bytes: Option<u64>,
    modified_at: Option<StrictUtcTimestamp>,
    content_sha256: Option<String>,
    exact_current_identity: bool,
    status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ArtifactIndexV2 {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    project_key: String,
    root_id: String,
    root_path_identity_sha256: String,
    root_instance_identity_sha256: String,
    source_snapshot_sha256: String,
    entries: Vec<ArtifactIndexEntryV2>,
    read_only: bool,
    file_mutation_authorized: bool,
}

#[derive(Debug, Clone, Copy)]
struct RetentionDiscoveryBudgets {
    max_entries: u64,
    max_depth: usize,
    max_elapsed_ms: u64,
}

impl Default for RetentionDiscoveryBudgets {
    fn default() -> Self {
        Self {
            max_entries: MAX_RETENTION_DISCOVERY_ENTRIES,
            max_depth: MAX_RETENTION_DISCOVERY_DEPTH,
            max_elapsed_ms: MAX_RETENTION_DISCOVERY_MS,
        }
    }
}

#[derive(Debug)]
struct BoundedRetentionArtifactIndex {
    index: ArtifactIndex,
    directory_snapshots: Vec<(PathBuf, crate::process::FilesystemObjectSnapshot)>,
    source_file_snapshots: Vec<(PathBuf, crate::process::FilesystemObjectSnapshot)>,
}

#[derive(Debug, Clone, Serialize)]
struct CanonicalRetentionResolutionV2 {
    schema: &'static str,
    project_key: String,
    artifact_snapshot_sha256: String,
    artifact_index: ArtifactIndexV2,
    target: RetentionTargetV2,
    #[serde(skip_serializing_if = "Option::is_none")]
    reviewed_state: Option<DocumentLifecycleStateV1>,
    selector_evidence: Vec<String>,
    authority: ArtifactRetentionReviewAuthority,
}

struct ResolvedRetentionContextV2 {
    resolution: CanonicalRetentionResolutionV2,
    proposal: RetentionProposalV2,
    review_item: ArtifactRetentionReviewItem,
}

struct RetentionResolverInput<'a> {
    artifact_id: Option<&'a str>,
    path: Option<&'a str>,
    document_id: Option<&'a str>,
    action: RetentionRequestAction,
    summary_document_id: Option<&'a str>,
    reason: Option<&'a str>,
    allow_applied_state: bool,
}

struct RetentionApplyV2Context<'a> {
    profile: &'a str,
    project_root: Option<&'a Path>,
    profile_dir: &'a Path,
    args: &'a ProjectRetentionApplyArgs,
    generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndex {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_root: Option<String>,
    summary: ArtifactIndexSummary,
    entries: Vec<ArtifactIndexEntry>,
    redaction: ArtifactIndexRedaction,
    authority: ArtifactIndexAuthority,
}

#[derive(Debug, Clone, Default, Serialize)]
struct ArtifactIndexSummary {
    total_entries: usize,
    present_entries: usize,
    missing_entries: usize,
    review_required_entries: usize,
    disposal_candidate_entries: usize,
    human_facing_entries: usize,
    truncated_entries: usize,
    by_retention_class: BTreeMap<String, usize>,
    by_source: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexEntry {
    id: String,
    label: String,
    source: String,
    kind: String,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    relative_path: Option<String>,
    present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    modified_at: Option<DateTime<Utc>>,
    retention_class: String,
    review_status: String,
    why_it_matters: String,
    refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexRedaction {
    operator_safe: bool,
    path_policy: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexAuthority {
    read_only: bool,
    does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionReview {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_root: Option<String>,
    summary: ArtifactRetentionReviewSummary,
    queues: ArtifactRetentionReviewQueues,
    recommendations: Vec<ArtifactRetentionRecommendation>,
    redaction: ArtifactIndexRedaction,
    authority: ArtifactRetentionReviewAuthority,
}

#[derive(Debug, Clone, Default, Serialize)]
struct ArtifactRetentionReviewSummary {
    total_entries: usize,
    action_required_entries: usize,
    keep_entries: usize,
    missing_entries: usize,
    review_required_entries: usize,
    disposal_candidate_entries: usize,
    archive_candidate_entries: usize,
    unreferenced_human_facing_entries: usize,
    queue_items: usize,
    truncated_queue_items: usize,
    by_scope: BTreeMap<String, ArtifactRetentionScopeSummary>,
}

#[derive(Debug, Clone, Default, Serialize)]
struct ArtifactRetentionScopeSummary {
    total_entries: usize,
    action_required_entries: usize,
    keep_entries: usize,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionReviewQueues {
    action_required: Vec<ArtifactRetentionReviewItem>,
    keep_sample: Vec<ArtifactRetentionReviewItem>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionReviewItem {
    id: String,
    label: String,
    source: String,
    kind: String,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    relative_path: Option<String>,
    present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<u64>,
    retention_class: String,
    review_status: String,
    retention_scope: String,
    scope_reason: String,
    recommended_action: String,
    reason: String,
    why_it_matters: String,
    refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionRecommendation {
    kind: String,
    priority: String,
    count: usize,
    summary: String,
    next_action: String,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionReviewAuthority {
    read_only: bool,
    does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionApprovalRequestReport {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    status: String,
    action: &'static str,
    requested_action: RetentionRequestAction,
    project_key: String,
    request_id: String,
    task_id: String,
    risk_level: RiskLevel,
    target: ArtifactRetentionReviewItem,
    approval: Option<PendingActionApproval>,
    detail: String,
    next_commands: Vec<String>,
    authority: ArtifactRetentionApprovalRequestAuthority,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionApprovalRequestAuthority {
    records_approval_only: bool,
    does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionApplicationReport {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    status: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_root: Option<String>,
    approval_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    application_id: Option<String>,
    request_id: String,
    task_id: String,
    action: &'static str,
    requested_action: String,
    risk_level: RiskLevel,
    target: ArtifactRetentionApplicationTarget,
    operation: ArtifactRetentionApplicationOperation,
    approval: ArtifactRetentionApplicationApproval,
    #[serde(skip_serializing_if = "Option::is_none")]
    lifecycle_event: Option<RetentionLifecycleEventReceiptV2>,
    receipt_path: String,
    next_actions: Vec<String>,
    authority: ArtifactRetentionApplicationAuthority,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RetentionLifecycleEventReceiptV2 {
    event_id: String,
    event_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct RetentionApplicationReceiptV2 {
    schema: String,
    application_id: String,
    approval_id: String,
    project_key: String,
    proposal_sha256: String,
    approval_metadata_sha256: String,
    approval_preview_sha256: String,
    retention_target_id: String,
    requested_action: RetentionRequestedActionV2,
    lifecycle_event: Option<RetentionLifecycleEventReceiptV2>,
    plan_only: bool,
    file_mutation_performed: bool,
}

impl RetentionApplicationReceiptV2 {
    fn validate_shape(&self, filename: &str) -> Result<()> {
        if self.schema != ARTIFACT_RETENTION_APPLICATION_SCHEMA_V2
            || self.application_id != format!("retention_application_{}", self.proposal_sha256)
            || filename != format!("{}.json", self.application_id)
            || !is_lower_sha256(&self.proposal_sha256)
            || !is_lower_sha256(&self.approval_metadata_sha256)
            || !is_lower_sha256(&self.approval_preview_sha256)
            || !self.approval_id.starts_with("approval_")
            || self.project_key.trim().is_empty()
            || self.retention_target_id.trim().is_empty()
            || !self.plan_only
            || self.file_mutation_performed
        {
            bail!("retention v2 receipt has an invalid authority shape");
        }
        if let Some(event) = &self.lifecycle_event {
            let event_uuid = event
                .event_id
                .strip_prefix("document_event_")
                .context("retention receipt lifecycle event ID has the wrong prefix")?;
            Uuid::parse_str(event_uuid)
                .context("retention receipt lifecycle event ID is invalid")?;
            if !is_lower_sha256(&event.event_sha256) {
                bail!("retention receipt lifecycle event hash is invalid");
            }
        }
        Ok(())
    }
}

fn is_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ArtifactRetentionApplicationTarget {
    artifact_id: String,
    label: String,
    source: String,
    kind: String,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    relative_path: Option<String>,
    present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<u64>,
    retention_class: String,
    review_status: String,
    why_it_matters: String,
    refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ArtifactRetentionApplicationOperation {
    operation_id: String,
    mutation_performed: bool,
    plan_only: bool,
    disposition: String,
    summary: String,
    planned_changes: Vec<String>,
    blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ArtifactRetentionApplicationApproval {
    before_status: ApprovalStatus,
    after_status: ApprovalStatus,
    consumed: bool,
    consumed_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionApplicationAuthority {
    consumes_approval: bool,
    writes_profile_receipt: bool,
    writes_project_state: bool,
    writes_files: bool,
    does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Clone, Deserialize)]
struct ArtifactRetentionApplicationReceipt {
    schema: String,
    generated_at: DateTime<Utc>,
    status: String,
    project_key: String,
    #[serde(default)]
    project_root: Option<String>,
    approval_id: String,
    request_id: String,
    task_id: String,
    requested_action: String,
    target: ArtifactRetentionApplicationTarget,
    operation: ArtifactRetentionApplicationOperation,
    approval: ArtifactRetentionApplicationApproval,
}

struct LoadedRetentionApplicationReceipt {
    path: PathBuf,
    receipt: ArtifactRetentionApplicationReceipt,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionPromotionReport {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    status: String,
    project_key: String,
    project_root: String,
    source_receipt: ArtifactRetentionPromotionSource,
    target: ArtifactRetentionApplicationTarget,
    deliverables_path: String,
    deliverables_entry: String,
    reviewed: bool,
    mutation_performed: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    snapshot_verification: Option<MutationSnapshotVerification>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    restore_plan: Option<MutationRestorePlan>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    promotion_receipt_path: Option<String>,
    next_actions: Vec<String>,
    authority: ArtifactRetentionPromotionAuthority,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionPromotionSource {
    receipt_path: String,
    approval_id: String,
    request_id: String,
    task_id: String,
    application_generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactRetentionPromotionAuthority {
    requires_reviewed_flag: bool,
    requires_snapshot: bool,
    writes_project_state: bool,
    writes_profile_receipt: bool,
    supported_action: &'static str,
    does_not_authorize: Vec<&'static str>,
}

struct EntryInput {
    label: String,
    source: String,
    kind: String,
    path: String,
    relative_path: Option<String>,
    present_override: Option<bool>,
    retention_class: String,
    review_status: String,
    why_it_matters: String,
    refs: Vec<String>,
}

struct PathEntryInput<'a> {
    label: &'a str,
    source: &'a str,
    kind: &'a str,
    path: &'a Path,
    retention_class: &'a str,
    review_status: &'a str,
    why_it_matters: &'a str,
    refs: Vec<String>,
}

pub async fn run(profile: &str, args: ProjectArtifactIndexArgs) -> Result<()> {
    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = args
        .project_key
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default_project_key(project_root.as_deref()));
    let index = build_artifact_index(profile, Some(&project_key), project_root.as_deref())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&index)?);
    } else {
        print!("{}", human_summary(&serde_json::to_value(&index)?));
    }
    Ok(())
}

pub async fn run_retention_review(profile: &str, args: ProjectRetentionReviewArgs) -> Result<()> {
    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = args
        .project_key
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default_project_key(project_root.as_deref()));
    if let Some(document_id) = args.document_id.as_deref() {
        let project_root = project_root
            .as_deref()
            .context("document retention review requires one explicit project root")?;
        let resolved = resolve_retention_target_v2(
            profile,
            project_root,
            &project_key,
            RetentionResolverInput {
                artifact_id: None,
                path: None,
                document_id: Some(document_id),
                action: RetentionRequestAction::Keep,
                summary_document_id: None,
                reason: None,
                allow_applied_state: true,
            },
        )?;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&resolved.resolution)?);
        } else {
            println!(
                "Retention target: {} ({:?})\n  path: {}\n  boundary: read-only; no lifecycle or file mutation was performed.",
                resolved.resolution.target.retention_target_id,
                resolved.resolution.target.source_kind,
                resolved.resolution.target.observation.relative_path
            );
        }
        return Ok(());
    }
    let index = build_artifact_index(profile, Some(&project_key), project_root.as_deref())?;
    let review = build_retention_review(&index);

    if args.json {
        println!("{}", serde_json::to_string_pretty(&review)?);
    } else {
        print!(
            "{}",
            retention_review_human_summary(&serde_json::to_value(&review)?)
        );
    }
    Ok(())
}

pub async fn run_retention_request(profile: &str, args: ProjectRetentionRequestArgs) -> Result<()> {
    let json = args.json;
    let report = build_retention_approval_request(profile, args)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print!("{}", retention_request_human_summary(&report));
    }
    Ok(())
}

pub async fn run_retention_apply(profile: &str, args: ProjectRetentionApplyArgs) -> Result<()> {
    let json = args.json;
    let report = build_retention_application(profile, args)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print!("{}", retention_application_human_summary(&report));
    }
    Ok(())
}

pub async fn run_retention_promote(profile: &str, args: ProjectRetentionPromoteArgs) -> Result<()> {
    let json = args.json;
    let report = build_retention_promotion(profile, args)?;
    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print!("{}", retention_promotion_human_summary(&report));
    }
    Ok(())
}

fn build_retention_approval_request(
    profile: &str,
    args: ProjectRetentionRequestArgs,
) -> Result<ArtifactRetentionApprovalRequestReport> {
    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = operator_safe_text(args.project_key.trim());
    if project_key.trim().is_empty() {
        bail!("--project-key cannot be empty");
    }
    let request_id = operator_safe_text(args.request_id.trim());
    if request_id.trim().is_empty() {
        bail!("--request-id cannot be empty");
    }

    let generated_at = Utc::now();
    let profile_dir = get_profile_dir(if profile.is_empty() {
        "default"
    } else {
        profile
    })?;
    let ledger = ApprovalLedger::new(profile_dir.clone());
    let (mut session, _) = ledger.begin_bounded_session(generated_at)?;
    let project_root = project_root
        .as_deref()
        .context("retention-request requires one current project root")?;
    let project_root_guard = crate::process::open_bounded_root(project_root)
        .with_context(|| format!("open retention project root {}", project_root.display()))?;
    let project_root_identity =
        crate::process::filesystem_file_identity(&project_root_guard.metadata()?)
            .context("retention project root lacks stable identity")?;
    let resolved = resolve_retention_target_v2(
        profile,
        project_root,
        &project_key,
        RetentionResolverInput {
            artifact_id: args.artifact_id.as_deref(),
            path: args.path_filter.as_deref(),
            document_id: args.document_id.as_deref(),
            action: args.action,
            summary_document_id: args.summary_document_id.as_deref(),
            reason: args.reason.as_deref(),
            allow_applied_state: false,
        },
    )?;
    ensure_retention_project_root_active(project_root, project_root_identity)?;
    let target = resolved.review_item;
    validate_retention_request_action(args.action, &target)?;

    let profile_root_identity = session
        .bounded_root_identity()
        .context("retention v2 request requires a descriptor-bound approval session")?;
    let retention_receipts =
        load_retention_application_receipts_v2(&profile_dir, profile_root_identity)?;
    for receipt in &retention_receipts {
        if receipt.project_key != project_key
            || receipt.retention_target_id != resolved.proposal.target.retention_target_id
        {
            continue;
        }
        let receipt_approval = session
            .approval(&receipt.approval_id)
            .cloned()
            .context("retention receipt references a missing approval")?;
        let receipt_metadata = receipt_approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_retention_v2)
            .cloned()
            .context("retention v2 receipt references non-v2 approval metadata")?;
        validate_retention_receipt_v2_against_approval(
            receipt,
            &receipt_approval,
            &receipt_metadata,
        )?;
        crate::document_lifecycle::validate_retention_approval_identity_v2(
            &receipt_approval,
            &receipt_metadata,
        )?;
        let existing_lifecycle = crate::document_lifecycle::reconcile_existing_retention_event_v2(
            &profile_dir,
            profile_root_identity,
            &receipt_approval,
            &receipt_metadata,
            generated_at,
        )?;
        let expected_lifecycle = receipt_metadata.proposal.target.document_id.is_some()
            && receipt_metadata
                .proposal
                .requested_action
                .lifecycle_action()
                .is_some();
        let existing_event_receipt =
            existing_lifecycle
                .as_ref()
                .map(|result| RetentionLifecycleEventReceiptV2 {
                    event_id: result.event.payload.event_id.clone(),
                    event_sha256: result.event.event_sha256.clone(),
                });
        if (expected_lifecycle && existing_lifecycle.is_none())
            || receipt.lifecycle_event != existing_event_receipt
        {
            bail!("retention receipt lacks its exact durable lifecycle event");
        }
        if receipt_approval.status == ApprovalStatus::Superseded {
            continue;
        }
        if receipt.requested_action != resolved.proposal.requested_action {
            bail!(
                "retention target already has a different unconsumed application receipt: {}",
                receipt.approval_id
            );
        }
        if receipt_metadata.proposal != resolved.proposal
            || receipt_metadata.proposal_sha256 != retention_proposal_sha256_v2(&resolved.proposal)?
        {
            bail!(
                "unconsumed retention receipt {} no longer matches the exact current target snapshot",
                receipt.approval_id
            );
        }
        validate_retention_approval_v2_against_live_target(
            &receipt_approval,
            &receipt_metadata,
            args.action,
            &target,
        )?;
        ensure_retention_project_root_active(project_root, project_root_identity)?;
        session.flush()?;
        ensure_retention_project_root_active(project_root, project_root_identity)?;
        return Ok(ArtifactRetentionApprovalRequestReport {
            schema: ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA_V2,
            generated_at,
            status: retention_request_status(receipt_approval.status).to_string(),
            action: RETENTION_APPROVAL_ACTION,
            requested_action: args.action,
            project_key,
            request_id: receipt_approval.request_id.clone(),
            task_id: receipt_approval.task_id.clone(),
            risk_level: receipt_approval.risk_level,
            target,
            approval: Some(receipt_approval.clone()),
            detail: "Reused the exact approval referenced by a durable application receipt."
                .to_string(),
            next_commands: retention_request_next_commands(Some(&receipt_approval)),
            authority: ArtifactRetentionApprovalRequestAuthority {
                records_approval_only: true,
                does_not_authorize: vec![
                    "delete files",
                    "move files",
                    "archive files",
                    "edit DELIVERABLES.md",
                    "publish outputs",
                    "accept output as truth without review",
                ],
            },
        });
    }

    for approval in session.approvals().iter().filter(|approval| {
        approval.project_key == project_key && approval.status == ApprovalStatus::Superseded
    }) {
        let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_retention_v2)
        else {
            continue;
        };
        if metadata.proposal.target.retention_target_id
            == resolved.proposal.target.retention_target_id
            && !retention_receipts
                .iter()
                .any(|receipt| receipt.approval_id == approval.approval_id)
        {
            bail!("consumed retention approval has no matching deterministic receipt");
        }
    }

    let unresolved_approvals = session
        .approvals()
        .iter()
        .filter(|approval| {
            approval.project_key == project_key
                && matches!(
                    approval.status,
                    ApprovalStatus::Pending | ApprovalStatus::Approved
                )
                && approval.expires_at >= generated_at
        })
        .cloned()
        .collect::<Vec<_>>();
    for approval in &unresolved_approvals {
        if let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_retention_v2)
        {
            metadata.validate()?;
            if metadata.proposal.target.retention_target_id
                == resolved.proposal.target.retention_target_id
            {
                validate_retention_request_approval_v2(
                    approval,
                    metadata,
                    RetentionRequestAction::from_v2(metadata.proposal.requested_action),
                    &target,
                )?;
                if metadata.proposal.requested_action != resolved.proposal.requested_action {
                    bail!(
                        "retention target already has a different unresolved action: {}",
                        approval.approval_id
                    );
                }
                if metadata.proposal != resolved.proposal
                    || metadata.proposal_sha256 != retention_proposal_sha256_v2(&resolved.proposal)?
                {
                    bail!(
                        "unresolved retention approval {} no longer matches the exact current target snapshot",
                        approval.approval_id
                    );
                }
                ensure_retention_project_root_active(project_root, project_root_identity)?;
                session.flush()?;
                ensure_retention_project_root_active(project_root, project_root_identity)?;
                return Ok(ArtifactRetentionApprovalRequestReport {
                    schema: ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA_V2,
                    generated_at,
                    status: retention_request_status(approval.status).to_string(),
                    action: RETENTION_APPROVAL_ACTION,
                    requested_action: args.action,
                    project_key,
                    request_id: approval.request_id.clone(),
                    task_id: approval.task_id.clone(),
                    risk_level: approval.risk_level,
                    target,
                    approval: Some(approval.clone()),
                    detail: "Reused the exact unresolved retention target and action.".to_string(),
                    next_commands: retention_request_next_commands(Some(approval)),
                    authority: ArtifactRetentionApprovalRequestAuthority {
                        records_approval_only: true,
                        does_not_authorize: vec![
                            "delete files",
                            "move files",
                            "archive files",
                            "edit DELIVERABLES.md",
                            "publish outputs",
                            "accept output as truth without review",
                        ],
                    },
                });
            }
        } else if let Some(legacy) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_artifact_retention)
        {
            if resolved.proposal.target.artifact_id.as_deref() == Some(&legacy.artifact_id)
                || resolved.proposal.target.observation.relative_path
                    == legacy.relative_path.as_deref().unwrap_or_default()
            {
                bail!(
                    "legacy unresolved approval {} lacks the current hash authority needed for a v2 request",
                    approval.approval_id
                );
            }
        }
    }

    let task_id = args
        .task_id
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| {
            format!(
                "retention_{}_{}",
                args.action.as_str().replace('-', "_"),
                sanitize_id_fragment(&resolved.proposal.target.retention_target_id)
            )
        });
    let risk_level = args.action.risk_level();
    let brief = retention_request_approval_brief(args.action, &target, &project_key, &request_id);
    let proposal_sha256 = retention_proposal_sha256_v2(&resolved.proposal)?;
    let metadata = RetentionApprovalMetadataV2 {
        schema: RETENTION_APPROVAL_METADATA_SCHEMA_V2.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(generated_at),
        proposal: resolved.proposal,
        proposal_sha256: proposal_sha256.clone(),
        application_id: format!("retention_application_{proposal_sha256}"),
        request_id: request_id.clone(),
        task_id: task_id.clone(),
        approval_brief: brief,
    };
    metadata.validate()?;
    let approval_reason = metadata.proposal.reason.clone();
    let mut request = ActionApprovalRequest::new(
        project_key.clone(),
        request_id.clone(),
        task_id.clone(),
        RETENTION_APPROVAL_ACTION,
        risk_level,
    );
    request.mutation_class = Some(RETENTION_APPROVAL_ACTION.to_string());
    request.preview = retention_request_preview(args.action, &target);
    request.reason = approval_reason;
    request.source_surface = "project.retention_request".to_string();
    request.ttl = Duration::minutes(args.ttl_minutes.max(1));
    request.metadata = Some(ActionApprovalMetadata::RetentionV2(Box::new(metadata)));
    let pending = session.ensure_pending_without_consuming_grant(request, generated_at)?;
    ensure_retention_project_root_active(project_root, project_root_identity)?;
    session.flush()?;
    ensure_retention_project_root_active(project_root, project_root_identity)?;
    let approval = pending;
    let approval_status = approval
        .as_ref()
        .map(|approval| approval.status)
        .unwrap_or(ApprovalStatus::Superseded);
    let status = approval
        .as_ref()
        .map(|_| retention_request_status(approval_status).to_string())
        .unwrap_or_else(|| "not_created".to_string());
    let detail = approval
        .as_ref()
        .map(|_| retention_request_detail(approval_status))
        .unwrap_or_else(|| "No artifact retention approval was created.".to_string());
    let next_commands = retention_request_next_commands(approval.as_ref());

    Ok(ArtifactRetentionApprovalRequestReport {
        schema: ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA_V2,
        generated_at,
        status,
        action: RETENTION_APPROVAL_ACTION,
        requested_action: args.action,
        project_key,
        request_id,
        task_id,
        risk_level,
        target,
        approval,
        detail,
        next_commands,
        authority: ArtifactRetentionApprovalRequestAuthority {
            records_approval_only: true,
            does_not_authorize: vec![
                "delete files",
                "move files",
                "archive files",
                "edit DELIVERABLES.md",
                "publish outputs",
                "accept output as truth without review",
            ],
        },
    })
}

fn retention_request_human_summary(report: &ArtifactRetentionApprovalRequestReport) -> String {
    let mut lines = Vec::new();
    lines.push(format!(
        "Artifact retention approval request: {}",
        report.status
    ));
    lines.push(format!(
        "  action: {} ({})",
        report.requested_action.as_str(),
        retention_request_risk_label(report.risk_level)
    ));
    lines.push(format!(
        "  target: {} [{} / {}]",
        report.target.label, report.target.retention_class, report.target.review_status
    ));
    lines.push(format!("  why: {}", report.target.why_it_matters));
    lines.push(format!("  detail: {}", report.detail));
    if let Some(approval) = &report.approval {
        lines.push(format!("  approval: {}", approval.approval_id));
    }
    lines.push("  boundary: approval-only; no file mutation was performed.".to_string());
    if !report.next_commands.is_empty() {
        lines.push("Next:".to_string());
        for command in &report.next_commands {
            lines.push(format!("  - {command}"));
        }
    }
    lines.push(String::new());
    lines.join("\n")
}

fn build_retention_application(
    profile: &str,
    args: ProjectRetentionApplyArgs,
) -> Result<ArtifactRetentionApplicationReport> {
    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = operator_safe_text(args.project_key.trim());
    if project_key.trim().is_empty() {
        bail!("--project-key cannot be empty");
    }
    let approval_id = operator_safe_text(args.approval_id.trim());
    if approval_id.trim().is_empty() {
        bail!("--approval-id cannot be empty");
    }

    let generated_at = Utc::now();
    let profile_name = if profile.is_empty() {
        "default"
    } else {
        profile
    };
    let profile_dir = profile_dir_path(profile_name)?;
    let ledger = ApprovalLedger::new(profile_dir.clone());
    let (mut session, _) = ledger.begin_bounded_session(generated_at)?;
    let approval = session
        .approval(&approval_id)
        .cloned()
        .with_context(|| format!("approval not found: {approval_id}"))?;

    if approval.project_key != project_key {
        bail!(
            "approval {} belongs to project {}, not {}",
            approval.approval_id,
            approval.project_key,
            project_key
        );
    }
    if approval.action != RETENTION_APPROVAL_ACTION {
        bail!(
            "approval {} is for action {}, not {}",
            approval.approval_id,
            approval.action,
            RETENTION_APPROVAL_ACTION
        );
    }
    if let Some(metadata) = approval
        .metadata
        .as_ref()
        .and_then(ActionApprovalMetadata::as_retention_v2)
        .cloned()
    {
        return build_retention_application_v2(
            &mut session,
            approval,
            metadata,
            RetentionApplyV2Context {
                profile,
                project_root: project_root.as_deref(),
                profile_dir: &profile_dir,
                args: &args,
                generated_at,
            },
        );
    }
    if approval.status != ApprovalStatus::Approved {
        bail!(
            "approval {} must be approved before retention-apply; current status is {}",
            approval.approval_id,
            retention_request_status(approval.status)
        );
    }
    if approval.expires_at < generated_at {
        bail!("approval {} is expired", approval.approval_id);
    }

    let metadata = approval
        .metadata
        .as_ref()
        .and_then(ActionApprovalMetadata::as_artifact_retention)
        .cloned()
        .with_context(|| {
            format!(
                "approval {} does not contain artifact_retention metadata",
                approval.approval_id
            )
        })?;
    let requested_action = operator_safe_text(&metadata.requested_action);
    if !matches!(
        requested_action.as_str(),
        "keep" | "promote" | "archive" | "dispose"
    ) {
        bail!(
            "approval {} has unsupported retention action {}",
            approval.approval_id,
            requested_action
        );
    }

    let receipt_path =
        resolve_retention_application_receipt_path(&profile_dir, generated_at, &approval, &args)?;
    let target = retention_application_target(&metadata);
    let operation =
        retention_application_operation(&requested_action, &metadata, &approval.approval_id);
    let next_actions = retention_application_next_actions(
        project_root.as_deref(),
        &project_key,
        &requested_action,
    );

    let consumed = session
        .supersede_approval(&approval.approval_id, "retention_application_consumed")
        .with_context(|| {
            format!(
                "approval {} was not available for consumption",
                approval.approval_id
            )
        })?;
    session.flush()?;

    let report = ArtifactRetentionApplicationReport {
        schema: ARTIFACT_RETENTION_APPLICATION_SCHEMA,
        generated_at,
        status: "applied_plan_recorded".to_string(),
        project_key,
        project_root: project_root
            .as_deref()
            .map(|root| operator_safe_path(root.to_string_lossy().as_ref())),
        approval_id: approval.approval_id.clone(),
        application_id: None,
        request_id: approval.request_id.clone(),
        task_id: approval.task_id.clone(),
        action: RETENTION_APPROVAL_ACTION,
        requested_action,
        risk_level: approval.risk_level,
        target,
        operation,
        approval: ArtifactRetentionApplicationApproval {
            before_status: ApprovalStatus::Approved,
            after_status: consumed.status,
            consumed: consumed.status == ApprovalStatus::Superseded,
            consumed_at: generated_at,
        },
        lifecycle_event: None,
        receipt_path: operator_safe_path(receipt_path.to_string_lossy().as_ref()),
        next_actions,
        authority: ArtifactRetentionApplicationAuthority {
            consumes_approval: true,
            writes_profile_receipt: true,
            writes_project_state: false,
            writes_files: false,
            does_not_authorize: vec![
                "delete files",
                "move files",
                "archive files",
                "edit DELIVERABLES.md",
                "publish outputs",
                "accept output as truth without review",
            ],
        },
    };
    write_retention_application_receipt(&receipt_path, &report)?;
    Ok(report)
}

fn build_retention_application_v2(
    session: &mut crate::offdesk::approval::ApprovalLedgerSession,
    approval: PendingActionApproval,
    metadata: RetentionApprovalMetadataV2,
    context: RetentionApplyV2Context<'_>,
) -> Result<ArtifactRetentionApplicationReport> {
    metadata.validate()?;
    crate::document_lifecycle::validate_retention_approval_identity_v2(&approval, &metadata)?;
    if context.args.receipt_out.is_some() {
        bail!("retention v2 uses one deterministic profile receipt; --receipt-out is not allowed");
    }
    let project_root = context
        .project_root
        .context("retention v2 apply requires one project root")?;
    let project_root_guard = crate::process::open_bounded_root(project_root)
        .with_context(|| format!("open retention project root {}", project_root.display()))?;
    let project_root_identity =
        crate::process::filesystem_file_identity(&project_root_guard.metadata()?)
            .context("retention project root lacks stable identity")?;
    if crate::document_lifecycle::root_instance_identity_sha256(project_root)?
        != metadata.proposal.root_instance_identity_sha256
    {
        bail!("retention approval belongs to a different project root instance");
    }
    let requested_action = RetentionRequestAction::from_v2(metadata.proposal.requested_action);
    let profile_root_identity = session
        .bounded_root_identity()
        .context("retention v2 apply requires a descriptor-bound approval session")?;
    let stored_receipt = load_retention_application_receipt_v2(
        context.profile_dir,
        profile_root_identity,
        &metadata.application_id,
    )?;
    let existing_lifecycle = crate::document_lifecycle::reconcile_existing_retention_event_v2(
        context.profile_dir,
        profile_root_identity,
        &approval,
        &metadata,
        context.generated_at,
    )?;
    let lifecycle_expected = metadata.proposal.target.document_id.is_some()
        && metadata
            .proposal
            .requested_action
            .lifecycle_action()
            .is_some();
    if let Some(receipt) = &stored_receipt {
        validate_retention_receipt_v2_against_approval(receipt, &approval, &metadata)?;
        if lifecycle_expected && existing_lifecycle.is_none() {
            bail!("retention receipt exists without its exact durable lifecycle event");
        }
        let existing_event_receipt =
            existing_lifecycle
                .as_ref()
                .map(|result| RetentionLifecycleEventReceiptV2 {
                    event_id: result.event.payload.event_id.clone(),
                    event_sha256: result.event.event_sha256.clone(),
                });
        if receipt.lifecycle_event != existing_event_receipt {
            bail!("retention receipt lifecycle identity differs from the durable event");
        }
    }
    if approval.status == ApprovalStatus::Superseded
        && lifecycle_expected
        && existing_lifecycle.is_none()
    {
        bail!("consumed retention approval has no matching durable lifecycle event");
    }
    if approval.status == ApprovalStatus::Superseded && stored_receipt.is_none() {
        bail!("consumed retention approval has no matching deterministic receipt");
    }

    let durable_application_started = stored_receipt.is_some() || existing_lifecycle.is_some();
    let target = if durable_application_started {
        retention_application_target_v2_from_metadata(&metadata)
    } else {
        if approval.expires_at < context.generated_at {
            bail!("retention apply requires one current approved approval");
        }
        let summary_document_id = metadata
            .proposal
            .after
            .as_ref()
            .and_then(|state| state.summary_document_id.as_deref());
        let live = resolve_retention_target_v2(
            context.profile,
            project_root,
            &metadata.proposal.project_key,
            RetentionResolverInput {
                artifact_id: metadata.proposal.target.artifact_id.as_deref(),
                path: Some(&metadata.proposal.target.observation.relative_path),
                document_id: metadata.proposal.target.document_id.as_deref(),
                action: requested_action,
                summary_document_id,
                reason: Some(&metadata.proposal.reason),
                allow_applied_state: false,
            },
        )?;
        if live.proposal != metadata.proposal
            || retention_proposal_sha256_v2(&live.proposal)? != metadata.proposal_sha256
        {
            bail!("current retention target differs from the approved exact proposal");
        }
        validate_retention_approval_v2_against_live_target(
            &approval,
            &metadata,
            requested_action,
            &live.review_item,
        )?;
        ArtifactRetentionApplicationTarget {
            artifact_id: metadata.proposal.target.retention_target_id.clone(),
            label: live.review_item.label,
            source: live.review_item.source,
            kind: live.review_item.kind,
            path: live.review_item.path,
            relative_path: live.review_item.relative_path,
            present: true,
            bytes: Some(metadata.proposal.target.observation.size_bytes),
            retention_class: live.review_item.retention_class,
            review_status: live.review_item.review_status,
            why_it_matters: live.review_item.why_it_matters,
            refs: live.review_item.refs,
        }
    };
    ensure_retention_project_root_active(project_root, project_root_identity)?;
    let (lifecycle, receipt, receipt_path, reused_receipt, consumed) =
        crate::document_lifecycle::with_retention_event_v2_lock(
            context.profile_dir,
            profile_root_identity,
            &approval,
            &metadata,
            !durable_application_started,
            context.generated_at,
            |lifecycle| {
                if lifecycle_expected && lifecycle.is_none() {
                    bail!("retention application lacks its exact durable lifecycle event");
                }
                let lifecycle_receipt = lifecycle.map(|result| RetentionLifecycleEventReceiptV2 {
                    event_id: result.event.payload.event_id.clone(),
                    event_sha256: result.event.event_sha256.clone(),
                });
                let receipt = RetentionApplicationReceiptV2 {
                    schema: ARTIFACT_RETENTION_APPLICATION_SCHEMA_V2.to_string(),
                    application_id: metadata.application_id.clone(),
                    approval_id: approval.approval_id.clone(),
                    project_key: approval.project_key.clone(),
                    proposal_sha256: metadata.proposal_sha256.clone(),
                    approval_metadata_sha256: retention_approval_metadata_sha256_v2(&metadata)?,
                    approval_preview_sha256: sha256_text(&approval.preview),
                    retention_target_id: metadata.proposal.target.retention_target_id.clone(),
                    requested_action: metadata.proposal.requested_action,
                    lifecycle_event: lifecycle_receipt,
                    plan_only: true,
                    file_mutation_performed: false,
                };
                let (receipt_path, reused_receipt) =
                    write_or_reconcile_retention_application_receipt_v2(
                        context.profile_dir,
                        profile_root_identity,
                        &receipt,
                        approval.status == ApprovalStatus::Approved,
                    )?;
                ensure_retention_project_root_active(project_root, project_root_identity)?;
                let consumed = match approval.status {
                    ApprovalStatus::Approved => session
                        .supersede_approval(&approval.approval_id, "retention_application_consumed")
                        .context("approved retention row disappeared while its lock was held")?,
                    ApprovalStatus::Superseded => approval.clone(),
                    _ => unreachable!("status checked above"),
                };
                session.ensure_bounded_root_active()?;
                session.flush()?;
                ensure_retention_project_root_active(project_root, project_root_identity)?;
                Ok((
                    lifecycle.cloned(),
                    receipt,
                    receipt_path,
                    reused_receipt,
                    consumed,
                ))
            },
        )?;

    let disposition = metadata
        .proposal
        .after
        .as_ref()
        .map(|state| format!("{:?}", state.retention_disposition).to_lowercase())
        .unwrap_or_else(|| "promotion_plan".to_string());
    let lifecycle_receipt = receipt.lifecycle_event.clone();
    Ok(ArtifactRetentionApplicationReport {
        schema: ARTIFACT_RETENTION_APPLICATION_SCHEMA_V2,
        generated_at: context.generated_at,
        status: if lifecycle
            .as_ref()
            .is_some_and(|result| result.reused_existing)
            || reused_receipt
        {
            "reconciled_existing".to_string()
        } else {
            "applied_plan_recorded".to_string()
        },
        project_key: approval.project_key,
        project_root: Some(operator_safe_path(project_root.to_string_lossy().as_ref())),
        approval_id: approval.approval_id,
        application_id: Some(metadata.application_id),
        request_id: approval.request_id,
        task_id: approval.task_id,
        action: RETENTION_APPROVAL_ACTION,
        requested_action: requested_action.as_str().to_string(),
        risk_level: approval.risk_level,
        target,
        operation: ArtifactRetentionApplicationOperation {
            operation_id: receipt.application_id,
            mutation_performed: false,
            plan_only: true,
            disposition,
            summary: requested_action.approval_impact().to_string(),
            planned_changes: vec![
                "Record the reviewed retention decision without changing project files."
                    .to_string(),
            ],
            blockers: vec![
                "Physical archive, quarantine, or disposal requires a separate mutation workflow."
                    .to_string(),
            ],
        },
        approval: ArtifactRetentionApplicationApproval {
            before_status: approval.status,
            after_status: consumed.status,
            consumed: consumed.status == ApprovalStatus::Superseded,
            consumed_at: context.generated_at,
        },
        lifecycle_event: lifecycle_receipt,
        receipt_path: operator_safe_path(receipt_path.to_string_lossy().as_ref()),
        next_actions: retention_application_next_actions(
            Some(project_root),
            &metadata.proposal.project_key,
            requested_action.as_str(),
        ),
        authority: ArtifactRetentionApplicationAuthority {
            consumes_approval: true,
            writes_profile_receipt: true,
            writes_project_state: false,
            writes_files: false,
            does_not_authorize: vec![
                "delete files",
                "move files",
                "archive files",
                "edit DELIVERABLES.md",
                "publish outputs",
            ],
        },
    })
}

fn retention_application_target_v2_from_metadata(
    metadata: &RetentionApprovalMetadataV2,
) -> ArtifactRetentionApplicationTarget {
    let target = &metadata.proposal.target;
    let source = match target.source_kind {
        RetentionTargetSourceKind::Artifact => "artifact_index.v2",
        RetentionTargetSourceKind::Document => "document_catalog.v2",
    };
    let kind = if target.artifact_id.is_some() {
        "artifact"
    } else {
        "document"
    };
    let mut refs = Vec::new();
    if let Some(artifact_id) = &target.artifact_id {
        refs.push(format!("artifact:{artifact_id}"));
    }
    if let Some(document_id) = &target.document_id {
        refs.push(format!("document:{document_id}"));
    }
    ArtifactRetentionApplicationTarget {
        artifact_id: target.retention_target_id.clone(),
        label: target.observation.relative_path.clone(),
        source: source.to_string(),
        kind: kind.to_string(),
        path: target.observation.relative_path.clone(),
        relative_path: Some(target.observation.relative_path.clone()),
        present: true,
        bytes: Some(target.observation.size_bytes),
        retention_class: "approved_snapshot".to_string(),
        review_status: "durable_application_reconciled".to_string(),
        why_it_matters: "This report reconciles an already durable retention application."
            .to_string(),
        refs,
    }
}

fn load_retention_application_receipt_v2(
    profile_dir: &Path,
    expected_root_identity: (u64, u64),
    application_id: &str,
) -> Result<Option<RetentionApplicationReceiptV2>> {
    let root = crate::process::open_bounded_root(profile_dir)
        .with_context(|| format!("open active profile root {}", profile_dir.display()))?;
    if crate::process::filesystem_file_identity(&root.metadata()?) != Some(expected_root_identity) {
        bail!("active profile root changed before retention receipt preflight");
    }
    let receipt_directory = Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR);
    let initial_directory_snapshot =
        crate::process::bounded_directory_snapshot(&root, receipt_directory).ok();
    let relative =
        Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR).join(format!("{application_id}.json"));
    match crate::process::open_bounded_regular_file(&root, &relative) {
        Ok(mut file) => {
            ensure_owner_only_retention_receipt(&file)?;
            let initial_metadata = file.metadata().context("inspect retention receipt")?;
            let mut bytes = Vec::new();
            Read::by_ref(&mut file)
                .take(MAX_RETENTION_RECEIPT_BYTES + 1)
                .read_to_end(&mut bytes)
                .context("read deterministic retention receipt")?;
            if bytes.len() as u64 > MAX_RETENTION_RECEIPT_BYTES {
                bail!("deterministic retention receipt exceeds its read budget");
            }
            let final_metadata = file.metadata().context("reinspect retention receipt")?;
            let active = crate::process::open_bounded_regular_file(&root, &relative)
                .context("reopen active retention receipt")?;
            let active_metadata = active
                .metadata()
                .context("inspect active retention receipt")?;
            if crate::process::filesystem_file_identity(&initial_metadata)
                != crate::process::filesystem_file_identity(&final_metadata)
                || crate::process::filesystem_file_identity(&final_metadata)
                    != crate::process::filesystem_file_identity(&active_metadata)
                || initial_metadata.len() != final_metadata.len()
                || final_metadata.len() != active_metadata.len()
                || initial_metadata.modified().ok() != final_metadata.modified().ok()
                || final_metadata.modified().ok() != active_metadata.modified().ok()
                || initial_directory_snapshot.is_none()
                || crate::process::bounded_directory_snapshot(&root, receipt_directory).ok()
                    != initial_directory_snapshot
            {
                bail!("deterministic retention receipt changed during bounded read");
            }
            let receipt: RetentionApplicationReceiptV2 =
                serde_json::from_slice(&bytes).context("parse deterministic retention receipt")?;
            receipt.validate_shape(
                relative
                    .file_name()
                    .and_then(|name| name.to_str())
                    .context("deterministic retention receipt name is invalid")?,
            )?;
            Ok(Some(receipt))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error).context("inspect deterministic retention receipt"),
    }
}

fn load_retention_application_receipts_v2(
    profile_dir: &Path,
    expected_root_identity: (u64, u64),
) -> Result<Vec<RetentionApplicationReceiptV2>> {
    let root = crate::process::open_bounded_root(profile_dir)
        .with_context(|| format!("open active profile root {}", profile_dir.display()))?;
    if crate::process::filesystem_file_identity(&root.metadata()?) != Some(expected_root_identity) {
        bail!("active profile root changed before retention receipt scan");
    }
    let relative_dir = Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR);
    let scan = match crate::process::read_bounded_directory(
        &root,
        relative_dir,
        MAX_RETENTION_RECEIPT_ENTRIES,
        Instant::now(),
        MAX_RETENTION_RECEIPT_SCAN_MS,
    ) {
        Ok(scan) => scan,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error).context("scan bounded retention receipt directory"),
    };
    if scan.limit.is_some() || !scan.stable {
        bail!("retention receipt scan was incomplete or unstable");
    }
    let mut receipts = Vec::new();
    for name in scan.names {
        let name = name
            .to_str()
            .context("retention receipt name is not valid UTF-8")?;
        if name.starts_with('.') || !name.ends_with(".json") {
            continue;
        }
        let relative = relative_dir.join(name);
        let mut file = crate::process::open_bounded_regular_file(&root, &relative)
            .context("open bounded retention receipt")?;
        ensure_owner_only_retention_receipt(&file)?;
        let initial_metadata = file
            .metadata()
            .context("inspect bounded retention receipt")?;
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take(MAX_RETENTION_RECEIPT_BYTES + 1)
            .read_to_end(&mut bytes)
            .context("read bounded retention receipt")?;
        if bytes.len() as u64 > MAX_RETENTION_RECEIPT_BYTES {
            bail!("retention receipt exceeds its read budget");
        }
        let final_metadata = file
            .metadata()
            .context("reinspect bounded retention receipt")?;
        let active = crate::process::open_bounded_regular_file(&root, &relative)
            .context("reopen active bounded retention receipt")?;
        let active_metadata = active
            .metadata()
            .context("inspect active bounded retention receipt")?;
        if crate::process::filesystem_file_identity(&initial_metadata)
            != crate::process::filesystem_file_identity(&final_metadata)
            || crate::process::filesystem_file_identity(&final_metadata)
                != crate::process::filesystem_file_identity(&active_metadata)
            || initial_metadata.len() != final_metadata.len()
            || final_metadata.len() != active_metadata.len()
            || initial_metadata.modified().ok() != final_metadata.modified().ok()
            || final_metadata.modified().ok() != active_metadata.modified().ok()
        {
            bail!("retention receipt changed during bounded scan");
        }
        let value: Value = serde_json::from_slice(&bytes).context("parse retention receipt")?;
        match value.get("schema").and_then(Value::as_str) {
            Some(ARTIFACT_RETENTION_APPLICATION_SCHEMA) => continue,
            Some(ARTIFACT_RETENTION_APPLICATION_SCHEMA_V2) => {
                let receipt: RetentionApplicationReceiptV2 =
                    serde_json::from_value(value).context("parse retention v2 receipt")?;
                receipt.validate_shape(name)?;
                receipts.push(receipt);
            }
            _ => bail!("unsupported retention receipt schema in the active receipt directory"),
        }
    }
    if crate::process::bounded_directory_snapshot(&root, relative_dir).ok() != Some(scan.snapshot)
        || crate::process::filesystem_file_identity(&root.metadata()?)
            != Some(expected_root_identity)
    {
        bail!("retention receipt directory changed during its bounded scan");
    }
    Ok(receipts)
}

fn ensure_retention_project_root_active(
    project_root: &Path,
    expected_identity: (u64, u64),
) -> Result<()> {
    let metadata = fs::symlink_metadata(project_root)
        .with_context(|| format!("inspect retention project root {}", project_root.display()))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_dir()
        || crate::process::filesystem_file_identity(&metadata) != Some(expected_identity)
    {
        bail!("retention project root changed during the bounded operation");
    }
    Ok(())
}

fn validate_retention_approval_v2_against_live_target(
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    requested_action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
) -> Result<()> {
    crate::document_lifecycle::validate_retention_approval_identity_v2(approval, metadata)?;
    let expected_brief = retention_request_approval_brief(
        requested_action,
        target,
        &metadata.proposal.project_key,
        &metadata.request_id,
    );
    if approval.preview != retention_request_preview(requested_action, target)
        || metadata.approval_brief != expected_brief
    {
        bail!("approval presentation is not bound to the exact retention v2 proposal");
    }
    Ok(())
}

fn validate_retention_request_approval_v2(
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    requested_action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
) -> Result<()> {
    metadata.validate()?;
    let status_provenance_valid = match approval.status {
        ApprovalStatus::Pending => approval.resolved_at.is_none() && approval.resolved_by.is_none(),
        ApprovalStatus::Approved => {
            approval.resolved_at.is_some()
                && approval
                    .resolved_by
                    .as_deref()
                    .is_some_and(|actor| !operator_safe_text(actor).is_empty())
        }
        _ => false,
    };
    if approval.project_key != metadata.proposal.project_key
        || approval.request_id != metadata.request_id
        || approval.task_id != metadata.task_id
        || approval.action != RETENTION_APPROVAL_ACTION
        || approval.scope != ApprovalScope::Once
        || approval.risk_level != requested_action.risk_level()
        || approval.approval_mode != ApprovalMode::OperatorRequired
        || approval.action_id.is_empty()
        || approval.action_id == approval.approval_id
        || approval.reason != metadata.proposal.reason
        || approval.source_surface != "project.retention_request"
        || approval.created_at != *metadata.generated_at.as_datetime()
        || approval.expires_at <= approval.created_at
        || !status_provenance_valid
    {
        bail!("approval identity is not one exact retention v2 request authority");
    }
    let expected_brief = retention_request_approval_brief(
        requested_action,
        target,
        &metadata.proposal.project_key,
        &metadata.request_id,
    );
    if approval.preview != retention_request_preview(requested_action, target)
        || metadata.approval_brief != expected_brief
    {
        bail!("approval presentation is not bound to the exact retention v2 proposal");
    }
    Ok(())
}

fn validate_retention_receipt_v2_against_approval(
    receipt: &RetentionApplicationReceiptV2,
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
) -> Result<()> {
    let metadata_sha256 = retention_approval_metadata_sha256_v2(metadata)?;
    let lifecycle_event_expected = metadata.proposal.target.document_id.is_some()
        && metadata
            .proposal
            .requested_action
            .lifecycle_action()
            .is_some();
    if receipt.approval_id != approval.approval_id
        || receipt.application_id != metadata.application_id
        || receipt.project_key != metadata.proposal.project_key
        || receipt.proposal_sha256 != metadata.proposal_sha256
        || receipt.approval_metadata_sha256 != metadata_sha256
        || receipt.approval_preview_sha256 != sha256_text(&approval.preview)
        || receipt.retention_target_id != metadata.proposal.target.retention_target_id
        || receipt.requested_action != metadata.proposal.requested_action
        || receipt.lifecycle_event.is_some() != lifecycle_event_expected
    {
        bail!("retention receipt is not bound to one exact approval and proposal");
    }
    Ok(())
}

fn sha256_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn write_or_reconcile_retention_application_receipt_v2(
    profile_dir: &Path,
    expected_root_identity: (u64, u64),
    receipt: &RetentionApplicationReceiptV2,
    allow_create: bool,
) -> Result<(PathBuf, bool)> {
    let root = crate::process::open_bounded_root(profile_dir)
        .with_context(|| format!("open active profile root {}", profile_dir.display()))?;
    let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
        .context("active profile root lacks stable identity")?;
    if root_identity != expected_root_identity {
        bail!("active profile root changed before retention receipt persistence");
    }
    crate::process::ensure_bounded_directory(
        &root,
        Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR),
    )?;
    let filename = format!("{}.json", receipt.application_id);
    let relative = Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR).join(&filename);
    if let Some(stored) = load_retention_application_receipt_v2(
        profile_dir,
        expected_root_identity,
        &receipt.application_id,
    )? {
        if &stored != receipt {
            bail!("deterministic retention receipt differs from the approved application");
        }
        return Ok((profile_dir.join(relative), true));
    }
    if !allow_create {
        bail!("consumed retention approval has no matching deterministic receipt");
    }
    let partial_name = format!(".{filename}.{}.partial", Uuid::new_v4());
    let partial_relative = Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR).join(&partial_name);
    let mut partial = crate::process::create_new_bounded_regular_file(&root, &partial_relative)
        .context("create deterministic retention receipt partial")?;
    ensure_owner_only_retention_receipt(&partial)?;
    let mut bytes = serde_json::to_vec_pretty(receipt).context("serialize retention v2 receipt")?;
    bytes.push(b'\n');
    partial
        .write_all(&bytes)
        .context("write deterministic retention receipt")?;
    partial
        .sync_all()
        .context("sync deterministic retention receipt")?;
    drop(partial);
    crate::process::finalize_bounded_file_noreplace(
        &root,
        Path::new(ARTIFACT_RETENTION_APPLICATIONS_DIR),
        Path::new(&partial_name),
        Path::new(&filename),
    )
    .context("finalize deterministic retention receipt")?;
    if crate::process::filesystem_file_identity(&root.metadata()?) != Some(expected_root_identity) {
        bail!("active profile root changed after retention receipt persistence");
    }
    Ok((profile_dir.join(relative), false))
}

fn ensure_owner_only_retention_receipt(file: &fs::File) -> Result<()> {
    let metadata = file.metadata().context("inspect retention receipt")?;
    if !metadata.is_file() {
        bail!("retention receipt is not one regular file");
    }
    #[cfg(unix)]
    if metadata.permissions().mode() & 0o077 != 0 || metadata.nlink() != 1 {
        bail!("retention receipt must be one owner-only file");
    }
    Ok(())
}

fn retention_application_human_summary(report: &ArtifactRetentionApplicationReport) -> String {
    let mut lines = Vec::new();
    lines.push(format!("Artifact retention apply: {}", report.status));
    lines.push(format!(
        "  decision: {} -> {}",
        report.approval_id, report.requested_action
    ));
    lines.push(format!(
        "  target: {} [{} / {}]",
        report.target.label, report.target.retention_class, report.target.review_status
    ));
    lines.push(format!("  plan: {}", report.operation.summary));
    lines
        .push("  boundary: approval consumed; no project file mutation was performed.".to_string());
    lines.push(format!("  receipt: {}", report.receipt_path));
    if !report.next_actions.is_empty() {
        lines.push("Next actions:".to_string());
        for action in &report.next_actions {
            lines.push(format!("  - {action}"));
        }
    }
    lines.push(String::new());
    lines.join("\n")
}

fn retention_application_target(
    metadata: &ArtifactRetentionApprovalMetadata,
) -> ArtifactRetentionApplicationTarget {
    ArtifactRetentionApplicationTarget {
        artifact_id: metadata.artifact_id.clone(),
        label: metadata.label.clone(),
        source: metadata.source.clone(),
        kind: metadata.artifact_kind.clone(),
        path: metadata.path.clone(),
        relative_path: metadata.relative_path.clone(),
        present: metadata.present,
        bytes: metadata.bytes,
        retention_class: metadata.retention_class.clone(),
        review_status: metadata.review_status.clone(),
        why_it_matters: metadata.why_it_matters.clone(),
        refs: metadata.refs.clone(),
    }
}

fn retention_application_operation(
    requested_action: &str,
    metadata: &ArtifactRetentionApprovalMetadata,
    approval_id: &str,
) -> ArtifactRetentionApplicationOperation {
    let operation_id = format!(
        "retention-application-{}",
        sanitize_id_fragment(approval_id)
    );
    let (summary, planned_changes, blockers) = match requested_action {
        "keep" => (
            "Record the artifact as retained for future review manifests.".to_string(),
            vec![
                "Keep the artifact available while the project remains active.".to_string(),
                "Use the next retention review to confirm the artifact still has a live role."
                    .to_string(),
            ],
            Vec::new(),
        ),
        "promote" => (
            "Prepare a reviewed deliverables promotion without editing DELIVERABLES.md here."
                .to_string(),
            vec![
                "Review the artifact preview and provenance before relying on it.".to_string(),
                "Update the deliverables surface through a separate reviewed canonical mutation."
                    .to_string(),
            ],
            vec![
                "This command does not edit DELIVERABLES.md.".to_string(),
                "A promotion mutation should have its own snapshot or review receipt.".to_string(),
            ],
        ),
        "archive" => (
            "Prepare an archive workflow without moving the artifact here.".to_string(),
            vec![
                "Create an archive manifest entry that explains why the artifact is no longer primary."
                    .to_string(),
                "Run a later archive mutation only after snapshot and restore planning.".to_string(),
            ],
            vec![
                "This command does not move files.".to_string(),
                "Archive execution needs a separate mutation workflow and restore plan.".to_string(),
            ],
        ),
        "dispose" => (
            "Prepare a disposal workflow without deleting the artifact here.".to_string(),
            vec![
                "Confirm the artifact has no remaining review, deliverable, or provenance role."
                    .to_string(),
                "Run a later destructive mutation only after snapshot and restore planning."
                    .to_string(),
            ],
            vec![
                "This command does not delete files.".to_string(),
                "Disposal execution needs a separate destructive approval and restore plan."
                    .to_string(),
            ],
        ),
        _ => (
            "Unsupported retention action was recorded without mutation.".to_string(),
            Vec::new(),
            vec![format!(
                "Unsupported retention action: {}",
                operator_safe_text(requested_action)
            )],
        ),
    };

    let mut planned_changes = planned_changes;
    planned_changes.push(format!(
        "Decision basis: {}",
        operator_safe_text(&metadata.reason)
    ));

    ArtifactRetentionApplicationOperation {
        operation_id,
        mutation_performed: false,
        plan_only: true,
        disposition: requested_action.to_string(),
        summary,
        planned_changes,
        blockers,
    }
}

fn retention_application_next_actions(
    project_root: Option<&Path>,
    project_key: &str,
    requested_action: &str,
) -> Vec<String> {
    let review_command = project_root
        .map(|root| {
            format!(
                "forager project retention-review {} --project-key {}",
                shell_arg(&root.to_string_lossy()),
                shell_arg(project_key)
            )
        })
        .unwrap_or_else(|| {
            format!(
                "forager project retention-review --project-key {}",
                shell_arg(project_key)
            )
        });
    let mut actions = vec![review_command];
    match requested_action {
        "keep" => {
            actions.push(
                "Confirm the next retention review shows no remaining action for this artifact."
                    .to_string(),
            );
        }
        "promote" => {
            actions.push(
                "Open the artifact preview/provenance, then perform a separate reviewed DELIVERABLES.md update."
                    .to_string(),
            );
        }
        "archive" => {
            actions.push(
                "Design a separate archive mutation with snapshot verification before moving files."
                    .to_string(),
            );
        }
        "dispose" => {
            actions.push(
                "Design a separate destructive cleanup with snapshot and restore plan before deleting files."
                    .to_string(),
            );
        }
        _ => {}
    }
    actions
}

fn resolve_retention_application_receipt_path(
    profile_dir: &Path,
    generated_at: DateTime<Utc>,
    approval: &PendingActionApproval,
    args: &ProjectRetentionApplyArgs,
) -> Result<PathBuf> {
    if let Some(path) = args.receipt_out.as_ref() {
        if path.exists() {
            bail!("receipt output already exists: {}", path.display());
        }
        return Ok(path.clone());
    }

    let timestamp = generated_at.format("%Y%m%dT%H%M%SZ");
    let approval_id = sanitize_id_fragment(&approval.approval_id);
    let dir = profile_dir.join(ARTIFACT_RETENTION_APPLICATIONS_DIR);
    for attempt in 0..1000 {
        let filename = if attempt == 0 {
            format!("artifact_retention_application_{timestamp}_{approval_id}.json")
        } else {
            format!("artifact_retention_application_{timestamp}_{approval_id}_{attempt:03}.json")
        };
        let path = dir.join(filename);
        if !path.exists() {
            return Ok(path);
        }
    }
    bail!("could not allocate a unique artifact retention application receipt path")
}

fn write_retention_application_receipt(
    path: &Path,
    report: &ArtifactRetentionApplicationReport,
) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    if path.exists() {
        bail!("receipt output already exists: {}", path.display());
    }
    let content = serde_json::to_string_pretty(report).context("serialize retention receipt")?;
    fs::write(path, format!("{content}\n")).with_context(|| format!("write {}", path.display()))
}

fn build_retention_promotion(
    profile: &str,
    args: ProjectRetentionPromoteArgs,
) -> Result<ArtifactRetentionPromotionReport> {
    let project_root = resolve_project_root(args.path.as_deref())?
        .context("project path must resolve to a directory for retention-promote")?;
    let project_key = operator_safe_text(args.project_key.trim());
    if project_key.trim().is_empty() {
        bail!("--project-key cannot be empty");
    }
    match (args.receipt.as_ref(), args.approval_id.as_ref()) {
        (Some(_), Some(_)) => bail!("provide only one of --receipt or --approval-id"),
        (None, None) => bail!("provide --receipt or --approval-id"),
        _ => {}
    }

    let profile_dir = get_profile_dir(profile)?;
    let loaded = load_retention_application_receipt(&profile_dir, &args)?;
    validate_retention_promotion_receipt(&loaded, &project_key, &project_root)?;

    let generated_at = Utc::now();
    let receipt = loaded.receipt;
    let deliverables_path = project_root.join("DELIVERABLES.md");
    if !deliverables_path.exists() {
        bail!("DELIVERABLES.md is missing; create the governance surface before retention-promote");
    }
    if !deliverables_path.is_file() {
        bail!(
            "DELIVERABLES.md is not a file: {}",
            deliverables_path.display()
        );
    }

    let relative_path = promotion_relative_path(&project_root, &receipt.target)?;
    let deliverables_entry =
        promotion_deliverables_entry(args.title.as_deref(), &receipt.target, &relative_path)?;
    let current = fs::read_to_string(&deliverables_path)
        .with_context(|| format!("read {}", deliverables_path.display()))?;
    let already_promoted = current.contains(&format!("`{relative_path}`"));

    let source_receipt = ArtifactRetentionPromotionSource {
        receipt_path: operator_safe_path(loaded.path.to_string_lossy().as_ref()),
        approval_id: receipt.approval_id.clone(),
        request_id: receipt.request_id.clone(),
        task_id: receipt.task_id.clone(),
        application_generated_at: receipt.generated_at,
    };

    if already_promoted {
        return Ok(ArtifactRetentionPromotionReport {
            schema: ARTIFACT_RETENTION_PROMOTION_SCHEMA,
            generated_at,
            status: "already_promoted".to_string(),
            project_key: project_key.clone(),
            project_root: operator_safe_path(project_root.to_string_lossy().as_ref()),
            source_receipt,
            target: receipt.target,
            deliverables_path: operator_safe_path(deliverables_path.to_string_lossy().as_ref()),
            deliverables_entry,
            reviewed: args.reviewed,
            mutation_performed: false,
            snapshot_verification: None,
            restore_plan: None,
            promotion_receipt_path: None,
            next_actions: vec![format!(
                "forager project retention-review {} --project-key {}",
                shell_arg(&project_root.to_string_lossy()),
                shell_arg(&project_key)
            )],
            authority: retention_promotion_authority(args.reviewed, false),
        });
    }

    let updated = append_deliverables_entry(&current, &deliverables_entry);
    if !args.reviewed {
        return Ok(ArtifactRetentionPromotionReport {
            schema: ARTIFACT_RETENTION_PROMOTION_SCHEMA,
            generated_at,
            status: "planned_review_required".to_string(),
            project_key: project_key.clone(),
            project_root: operator_safe_path(project_root.to_string_lossy().as_ref()),
            source_receipt,
            target: receipt.target,
            deliverables_path: operator_safe_path(deliverables_path.to_string_lossy().as_ref()),
            deliverables_entry,
            reviewed: false,
            mutation_performed: false,
            snapshot_verification: None,
            restore_plan: None,
            promotion_receipt_path: None,
            next_actions: vec![format!(
                "forager project retention-promote {} --project-key {} --approval-id {} --reviewed",
                shell_arg(&project_root.to_string_lossy()),
                shell_arg(&project_key),
                shell_arg(&receipt.approval_id)
            )],
            authority: retention_promotion_authority(false, false),
        });
    }

    let promotion_receipt_path =
        resolve_retention_promotion_receipt_path(&profile_dir, generated_at, &receipt)?;
    prepare_retention_receipt_parent(&promotion_receipt_path)?;

    let snapshot_store = MutationSnapshotStore::new(&profile_dir);
    let snapshot = snapshot_store.create_snapshot(MutationSnapshotRequest {
        project_key: project_key.clone(),
        request_id: receipt.request_id.clone(),
        task_id: format!(
            "retention-promote-{}",
            sanitize_id_fragment(&receipt.approval_id)
        ),
        target_path: deliverables_path.clone(),
        mutation_kind: RETENTION_PROMOTE_MUTATION_KIND.to_string(),
        diff_preview: format!("Append deliverables entry:\n+ {deliverables_entry}"),
        created_by: "project.retention_promote".to_string(),
    })?;
    let pre_verification = snapshot_store.verify_snapshot(&snapshot.mutation_id, generated_at)?;
    let pre_restore_plan = snapshot_store.restore_plan(&snapshot.mutation_id, generated_at)?;
    if !pre_verification.rollback_available || !pre_restore_plan.rollback_available {
        bail!(
            "mutation snapshot is not rollback-ready: {}",
            pre_verification.blockers.join("; ")
        );
    }

    fs::write(&deliverables_path, updated)
        .with_context(|| format!("write {}", deliverables_path.display()))?;
    let post_verification = snapshot_store.verify_snapshot(&snapshot.mutation_id, Utc::now())?;
    let post_restore_plan = snapshot_store.restore_plan(&snapshot.mutation_id, Utc::now())?;

    let mut report = ArtifactRetentionPromotionReport {
        schema: ARTIFACT_RETENTION_PROMOTION_SCHEMA,
        generated_at,
        status: "promoted".to_string(),
        project_key,
        project_root: operator_safe_path(project_root.to_string_lossy().as_ref()),
        source_receipt,
        target: receipt.target,
        deliverables_path: operator_safe_path(deliverables_path.to_string_lossy().as_ref()),
        deliverables_entry,
        reviewed: true,
        mutation_performed: true,
        snapshot_verification: Some(post_verification),
        restore_plan: Some(post_restore_plan),
        promotion_receipt_path: Some(operator_safe_path(
            promotion_receipt_path.to_string_lossy().as_ref(),
        )),
        next_actions: vec![
            format!(
                "forager offdesk restore-plan {}",
                shell_arg(&snapshot.mutation_id)
            ),
            format!(
                "forager project retention-review {} --project-key {}",
                shell_arg(&project_root.to_string_lossy()),
                shell_arg(&args.project_key)
            ),
        ],
        authority: retention_promotion_authority(true, true),
    };
    write_retention_promotion_receipt(&promotion_receipt_path, &report)?;
    report.promotion_receipt_path = Some(operator_safe_path(
        promotion_receipt_path.to_string_lossy().as_ref(),
    ));
    Ok(report)
}

fn retention_promotion_human_summary(report: &ArtifactRetentionPromotionReport) -> String {
    let mut lines = Vec::new();
    lines.push(format!("Artifact retention promote: {}", report.status));
    lines.push(format!("  target: {}", report.target.label));
    lines.push(format!("  entry: {}", report.deliverables_entry));
    lines.push(format!(
        "  boundary: reviewed={}, mutation_performed={}",
        report.reviewed, report.mutation_performed
    ));
    if let Some(verification) = report.snapshot_verification.as_ref() {
        lines.push(format!("  snapshot: {}", verification.mutation_id));
        lines.push(format!(
            "  rollback_available: {}",
            verification.rollback_available
        ));
    }
    if let Some(path) = report.promotion_receipt_path.as_deref() {
        lines.push(format!("  receipt: {path}"));
    }
    if !report.next_actions.is_empty() {
        lines.push("Next actions:".to_string());
        for action in &report.next_actions {
            lines.push(format!("  - {action}"));
        }
    }
    lines.push(String::new());
    lines.join("\n")
}

fn load_retention_application_receipt(
    profile_dir: &Path,
    args: &ProjectRetentionPromoteArgs,
) -> Result<LoadedRetentionApplicationReceipt> {
    if let Some(path) = args.receipt.as_ref() {
        return read_retention_application_receipt(path);
    }
    let approval_id = args
        .approval_id
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .context("--approval-id cannot be empty")?;
    find_retention_application_receipt(profile_dir, &approval_id)
}

fn read_retention_application_receipt(path: &Path) -> Result<LoadedRetentionApplicationReceipt> {
    let content =
        fs::read_to_string(path).with_context(|| format!("read receipt {}", path.display()))?;
    let receipt = serde_json::from_str::<ArtifactRetentionApplicationReceipt>(&content)
        .with_context(|| format!("parse receipt {}", path.display()))?;
    Ok(LoadedRetentionApplicationReceipt {
        path: path.to_path_buf(),
        receipt,
    })
}

fn find_retention_application_receipt(
    profile_dir: &Path,
    approval_id: &str,
) -> Result<LoadedRetentionApplicationReceipt> {
    let dir = profile_dir.join(ARTIFACT_RETENTION_APPLICATIONS_DIR);
    if !dir.exists() {
        bail!(
            "artifact retention application receipt directory is missing: {}",
            dir.display()
        );
    }
    let mut matches = Vec::new();
    for entry in fs::read_dir(&dir).with_context(|| format!("read {}", dir.display()))? {
        let path = entry?.path();
        if path.extension().and_then(|extension| extension.to_str()) != Some("json") {
            continue;
        }
        let Ok(loaded) = read_retention_application_receipt(&path) else {
            continue;
        };
        if loaded.receipt.approval_id == approval_id {
            matches.push(loaded);
        }
    }
    matches.sort_by_key(|loaded| loaded.receipt.generated_at);
    matches
        .pop()
        .with_context(|| format!("application receipt not found for approval {approval_id}"))
}

fn validate_retention_promotion_receipt(
    loaded: &LoadedRetentionApplicationReceipt,
    project_key: &str,
    project_root: &Path,
) -> Result<()> {
    let receipt = &loaded.receipt;
    if receipt.schema != ARTIFACT_RETENTION_APPLICATION_SCHEMA {
        bail!(
            "receipt {} has schema {}, not {}",
            loaded.path.display(),
            receipt.schema,
            ARTIFACT_RETENTION_APPLICATION_SCHEMA
        );
    }
    if receipt.project_key != project_key {
        bail!(
            "receipt project_key {} does not match {}",
            receipt.project_key,
            project_key
        );
    }
    if let Some(recorded_root) = receipt.project_root.as_deref() {
        let current_root = operator_safe_path(project_root.to_string_lossy().as_ref());
        if recorded_root != current_root {
            bail!(
                "receipt project_root {} does not match {}",
                recorded_root,
                current_root
            );
        }
    }
    if receipt.status != "applied_plan_recorded" {
        bail!("receipt status must be applied_plan_recorded");
    }
    if receipt.requested_action != "promote" {
        bail!(
            "retention-promote only supports promote receipts; receipt action is {}",
            receipt.requested_action
        );
    }
    if !receipt.approval.consumed {
        bail!("retention application approval must be consumed before promotion");
    }
    if receipt.operation.mutation_performed {
        bail!("retention application receipt must be plan-only before promotion");
    }
    if !receipt.target.present {
        bail!("cannot promote a missing artifact");
    }
    Ok(())
}

fn promotion_relative_path(
    project_root: &Path,
    target: &ArtifactRetentionApplicationTarget,
) -> Result<String> {
    let relative = target
        .relative_path
        .clone()
        .unwrap_or_else(|| rel_path(project_root, Path::new(&target.path)));
    if relative.trim().is_empty()
        || relative.contains('\n')
        || relative.contains('\r')
        || relative.contains('`')
        || relative.starts_with("../")
        || relative == ".."
        || Path::new(&relative).is_absolute()
    {
        bail!("artifact relative path is not safe for DELIVERABLES.md");
    }
    let target_path = project_root.join(&relative);
    if !target_path.exists() {
        bail!("artifact path is missing: {}", target_path.display());
    }
    Ok(relative)
}

fn promotion_deliverables_entry(
    title: Option<&str>,
    target: &ArtifactRetentionApplicationTarget,
    relative_path: &str,
) -> Result<String> {
    let title = title
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| target.label.clone())
        .replace(['\n', '\r', '`'], " ");
    if title.trim().is_empty() {
        bail!("deliverables title cannot be empty");
    }
    Ok(format!("- {}: `{}`", title.trim(), relative_path))
}

fn append_deliverables_entry(current: &str, deliverables_entry: &str) -> String {
    let mut updated = current.to_string();
    if !updated.ends_with('\n') {
        updated.push('\n');
    }
    if !updated.ends_with("\n\n") {
        updated.push('\n');
    }
    updated.push_str(deliverables_entry);
    updated.push('\n');
    updated
}

fn retention_promotion_authority(
    reviewed: bool,
    writes_project_state: bool,
) -> ArtifactRetentionPromotionAuthority {
    ArtifactRetentionPromotionAuthority {
        requires_reviewed_flag: true,
        requires_snapshot: true,
        writes_project_state,
        writes_profile_receipt: writes_project_state,
        supported_action: "promote",
        does_not_authorize: if reviewed {
            vec![
                "delete files",
                "move files",
                "archive files",
                "publish outputs",
                "accept output as truth without review",
            ]
        } else {
            vec![
                "edit DELIVERABLES.md without --reviewed",
                "delete files",
                "move files",
                "archive files",
                "publish outputs",
                "accept output as truth without review",
            ]
        },
    }
}

fn resolve_retention_promotion_receipt_path(
    profile_dir: &Path,
    generated_at: DateTime<Utc>,
    receipt: &ArtifactRetentionApplicationReceipt,
) -> Result<PathBuf> {
    let timestamp = generated_at.format("%Y%m%dT%H%M%SZ");
    let approval_id = sanitize_id_fragment(&receipt.approval_id);
    let dir = profile_dir.join(ARTIFACT_RETENTION_PROMOTIONS_DIR);
    for attempt in 0..1000 {
        let filename = if attempt == 0 {
            format!("artifact_retention_promotion_{timestamp}_{approval_id}.json")
        } else {
            format!("artifact_retention_promotion_{timestamp}_{approval_id}_{attempt:03}.json")
        };
        let path = dir.join(filename);
        if !path.exists() {
            return Ok(path);
        }
    }
    bail!("could not allocate a unique artifact retention promotion receipt path")
}

fn write_retention_promotion_receipt(
    path: &Path,
    report: &ArtifactRetentionPromotionReport,
) -> Result<()> {
    prepare_retention_receipt_parent(path)?;
    let content =
        serde_json::to_string_pretty(report).context("serialize retention promotion receipt")?;
    fs::write(path, format!("{content}\n")).with_context(|| format!("write {}", path.display()))
}

fn prepare_retention_receipt_parent(path: &Path) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    if path.exists() {
        bail!(
            "promotion receipt output already exists: {}",
            path.display()
        );
    }
    Ok(())
}

fn shell_arg(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '/' | '.' | '_' | '-' | ':'))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn validate_retention_request_action(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
) -> Result<()> {
    if !target.present && !matches!(action, RetentionRequestAction::Keep) {
        bail!("only keep can be requested for a missing artifact reference");
    }
    Ok(())
}

fn retention_request_preview(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
) -> String {
    format!(
        "{} for artifact {} [{} / {}]. No file mutation is performed by this request.",
        action.label(),
        target.label,
        target.retention_class,
        target.review_status
    )
}

fn retention_request_approval_brief(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
    project_key: &str,
    request_id: &str,
) -> ApprovalBrief {
    let mut decision_impacts = HashMap::new();
    decision_impacts.insert("approve".to_string(), action.approval_impact().to_string());
    decision_impacts.insert(
        "deny".to_string(),
        "Keep the artifact index unchanged and require a revised scoped request if needed."
            .to_string(),
    );
    decision_impacts.insert(
        "defer".to_string(),
        "Leave this approval pending while asking for more artifact context.".to_string(),
    );

    let mut context = HashMap::new();
    context.insert("artifact_id".to_string(), target.id.clone());
    context.insert("project_key".to_string(), project_key.to_string());
    context.insert("request_id".to_string(), request_id.to_string());
    context.insert("source".to_string(), target.source.clone());
    context.insert("kind".to_string(), target.kind.clone());
    context.insert("present".to_string(), target.present.to_string());
    context.insert(
        "retention_class".to_string(),
        target.retention_class.clone(),
    );
    context.insert("review_status".to_string(), target.review_status.clone());
    context.insert(
        "recommended_action".to_string(),
        target.recommended_action.clone(),
    );
    context.insert("requested_action".to_string(), action.as_str().to_string());

    let mut evidence = vec![
        format!("Why it matters: {}", target.why_it_matters),
        format!("Review reason: {}", target.reason),
    ];
    evidence.extend(
        target
            .refs
            .iter()
            .take(3)
            .map(|reference| format!("Reference: {reference}")),
    );

    ApprovalBrief {
        schema: "approval_brief.v1".to_string(),
        source: Some("project.retention_request".to_string()),
        recommendation: action.as_str().to_string(),
        subject: format!("artifact retention {}", action.as_str()),
        summary_lines: vec![
            "Artifact retention follow-up is waiting for operator approval.".to_string(),
            format!("Artifact: {} ({})", target.label, target.kind),
            format!(
                "Review: {} / {}",
                target.retention_class, target.review_status
            ),
            format!("Recommended by review: {}", target.recommended_action),
        ],
        judgment_route_summary: Some(
            "판단 경로: 사용자 - retention follow-up changes artifact governance state and requires bounded operator approval."
                .to_string(),
        ),
        evidence_sufficiency: Some(
            "Retention review reason, artifact meaning, and up to three artifact refs are summarized; file mutation remains out of scope."
                .to_string(),
        ),
        default_if_no_reply: Some("defer".to_string()),
        scope: "Approves only the retention follow-up request for this artifact; does not delete, move, archive, edit DELIVERABLES.md, publish, or accept output as truth."
            .to_string(),
        question: format!("Approve the {} retention follow-up?", action.as_str()),
        options: vec![
            ApprovalBriefOption {
                id: "approve".to_string(),
                label: format!("Approve {}", action.as_str()),
                description: action.approval_impact().to_string(),
                natural_input_prompt: None,
            },
            ApprovalBriefOption {
                id: "deny".to_string(),
                label: "Deny".to_string(),
                description:
                    "Reject this request and keep the artifact index unchanged.".to_string(),
                natural_input_prompt: Some(
                    "Explain why this artifact should stay out of the requested retention action."
                        .to_string(),
                ),
            },
            ApprovalBriefOption {
                id: "defer".to_string(),
                label: "Need more detail".to_string(),
                description:
                    "Ask for more artifact context before making the retention decision."
                        .to_string(),
                natural_input_prompt: Some(
                    "State what evidence, preview, or provenance you need first.".to_string(),
                ),
            },
        ],
        why_recommendation: vec![
            target.reason.clone(),
            target.why_it_matters.clone(),
            "The approval is intentionally separated from any file mutation.".to_string(),
        ],
        evidence,
        decision_impacts,
        reply_examples: vec![
            "approve".to_string(),
            "deny - keep it until the final report is reviewed".to_string(),
            "defer - show the preview and provenance first".to_string(),
        ],
        context,
    }
}

fn retention_request_status(approval_status: ApprovalStatus) -> &'static str {
    match approval_status {
        ApprovalStatus::Pending => "pending_approval",
        ApprovalStatus::Approved => "already_approved",
        ApprovalStatus::Denied => "previously_denied",
        ApprovalStatus::Expired => "expired",
        ApprovalStatus::Superseded => "superseded",
    }
}

fn retention_request_detail(approval_status: ApprovalStatus) -> String {
    match approval_status {
        ApprovalStatus::Pending => {
            "Artifact retention approval is pending or was reused.".to_string()
        }
        ApprovalStatus::Approved => {
            "A matching retention approval already exists; this command did not consume it."
                .to_string()
        }
        ApprovalStatus::Denied => {
            "A matching retention approval was previously denied; create a new scoped request if needed."
                .to_string()
        }
        ApprovalStatus::Expired => "A matching retention approval is expired.".to_string(),
        ApprovalStatus::Superseded => "A matching retention approval is superseded.".to_string(),
    }
}

fn retention_request_next_commands(approval: Option<&PendingActionApproval>) -> Vec<String> {
    let Some(approval) = approval else {
        return vec!["forager offdesk pending".to_string()];
    };
    match approval.status {
        ApprovalStatus::Pending => vec![
            format!("forager offdesk ok {}", approval.approval_id),
            format!("forager offdesk deny {}", approval.approval_id),
            "forager offdesk pending".to_string(),
        ],
        ApprovalStatus::Approved => vec![
            format!(
                "forager project retention-apply --project-key {} --approval-id {}",
                shell_arg(&approval.project_key),
                shell_arg(&approval.approval_id)
            ),
            "forager offdesk pending --all".to_string(),
        ],
        _ => vec!["forager offdesk pending --all".to_string()],
    }
}

fn retention_request_risk_label(risk_level: RiskLevel) -> &'static str {
    match risk_level {
        RiskLevel::Safe => "safe",
        RiskLevel::RuntimeMutation => "runtime_mutation",
        RiskLevel::CanonicalMutation => "canonical_mutation",
        RiskLevel::Destructive => "destructive",
        RiskLevel::ExternalSideEffect => "external_side_effect",
    }
}

fn sanitize_id_fragment(value: &str) -> String {
    let fragment = operator_safe_text(value)
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || *ch == '-')
        .take(32)
        .collect::<String>();
    if fragment.is_empty() {
        "artifact".to_string()
    } else {
        fragment
    }
}

pub(crate) fn build_profile_artifact_index_value(
    profile: &str,
    project_key: Option<&str>,
) -> Result<Value> {
    serde_json::to_value(build_artifact_index(profile, project_key, None)?)
        .context("serialize artifact index")
}

pub(crate) fn build_profile_retention_review_value(
    profile: &str,
    project_key: Option<&str>,
) -> Result<Value> {
    let index = build_artifact_index(profile, project_key, None)?;
    serde_json::to_value(build_retention_review(&index))
        .context("serialize artifact retention review")
}

pub(crate) fn review_surface_projection(index: &Value) -> Value {
    let entries = index
        .get("entries")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .take(20)
                .map(|entry| {
                    json!({
                        "id": entry.get("id").cloned().unwrap_or(Value::Null),
                        "label": entry.get("label").cloned().unwrap_or(Value::Null),
                        "source": entry.get("source").cloned().unwrap_or(Value::Null),
                        "kind": entry.get("kind").cloned().unwrap_or(Value::Null),
                        "path": entry.get("path").cloned().unwrap_or(Value::Null),
                        "present": entry.get("present").cloned().unwrap_or(Value::Null),
                        "retention_class": entry.get("retention_class").cloned().unwrap_or(Value::Null),
                        "review_status": entry.get("review_status").cloned().unwrap_or(Value::Null),
                        "why_it_matters": entry.get("why_it_matters").cloned().unwrap_or(Value::Null)
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    json!({
        "schema": index.get("schema").cloned().unwrap_or(Value::String(ARTIFACT_INDEX_SCHEMA.to_string())),
        "summary": index.get("summary").cloned().unwrap_or(Value::Object(Default::default())),
        "entries": entries,
        "projection_policy": "first_20_entries_summary_first"
    })
}

pub(crate) fn retention_review_projection(review: &Value) -> Value {
    let action_required = review
        .pointer("/queues/action_required")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(MAX_RETENTION_REVIEW_PROJECTION_ITEMS)
                .map(|item| {
                    json!({
                        "id": item.get("id").cloned().unwrap_or(Value::Null),
                        "label": item.get("label").cloned().unwrap_or(Value::Null),
                        "source": item.get("source").cloned().unwrap_or(Value::Null),
                        "kind": item.get("kind").cloned().unwrap_or(Value::Null),
                        "present": item.get("present").cloned().unwrap_or(Value::Null),
                        "retention_class": item.get("retention_class").cloned().unwrap_or(Value::Null),
                        "review_status": item.get("review_status").cloned().unwrap_or(Value::Null),
                        "retention_scope": item.get("retention_scope").cloned().unwrap_or(Value::Null),
                        "scope_reason": item.get("scope_reason").cloned().unwrap_or(Value::Null),
                        "recommended_action": item.get("recommended_action").cloned().unwrap_or(Value::Null),
                        "reason": item.get("reason").cloned().unwrap_or(Value::Null),
                        "why_it_matters": item.get("why_it_matters").cloned().unwrap_or(Value::Null)
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let recommendations = review
        .get("recommendations")
        .and_then(Value::as_array)
        .map(|recommendations| recommendations.iter().take(5).cloned().collect::<Vec<_>>())
        .unwrap_or_default();

    json!({
        "schema": review.get("schema").cloned().unwrap_or(Value::String(ARTIFACT_RETENTION_REVIEW_SCHEMA.to_string())),
        "summary": review.get("summary").cloned().unwrap_or(Value::Object(Default::default())),
        "recommendations": recommendations,
        "action_required": action_required,
        "projection_policy": "summary_plus_first_10_action_required_items"
    })
}

fn build_artifact_index(
    profile: &str,
    project_key: Option<&str>,
    project_root: Option<&Path>,
) -> Result<ArtifactIndex> {
    let profile_name = if profile.is_empty() {
        "default"
    } else {
        profile
    };
    let profile_dir = profile_dir_path(profile_name)?;
    let safe_project_key = project_key
        .map(operator_safe_text)
        .unwrap_or_else(|| "all".to_string());
    let mut entries = BTreeMap::new();

    if let Some(root) = project_root {
        collect_project_outputs(root, &safe_project_key, &mut entries)?;
    }
    collect_profile_artifacts(&profile_dir, project_key, &mut entries)?;

    let mut entries = entries.into_values().collect::<Vec<_>>();
    entries.sort_by(|left, right| {
        left.source
            .cmp(&right.source)
            .then(left.retention_class.cmp(&right.retention_class))
            .then(left.path.cmp(&right.path))
    });
    let total_entries = entries.len();
    let truncated_entries = total_entries.saturating_sub(MAX_INDEX_ENTRIES);
    entries.truncate(MAX_INDEX_ENTRIES);
    let summary = summarize_entries(&entries, total_entries, truncated_entries);

    Ok(ArtifactIndex {
        schema: ARTIFACT_INDEX_SCHEMA,
        generated_at: Utc::now(),
        profile: operator_safe_text(profile_name),
        project_key: safe_project_key,
        project_root: project_root.map(|root| operator_safe_path(root.to_string_lossy().as_ref())),
        summary,
        entries,
        redaction: ArtifactIndexRedaction {
            operator_safe: true,
            path_policy: "summary_first_paths_in_json",
        },
        authority: ArtifactIndexAuthority {
            read_only: true,
            does_not_authorize: vec![
                "delete",
                "move",
                "archive",
                "publish",
                "accepting output as truth without closeout receipt review",
            ],
        },
    })
}

fn build_bounded_retention_artifact_index(
    profile: &str,
    project_key: &str,
    project_root: &Path,
    root_instance_identity_sha256: &str,
    scan_started: Instant,
    budgets: RetentionDiscoveryBudgets,
) -> Result<BoundedRetentionArtifactIndex> {
    let root = crate::process::open_bounded_root(project_root)
        .with_context(|| format!("open retention artifact root {}", project_root.display()))?;
    if crate::document_lifecycle::root_instance_identity_sha256_from_descriptor(&root)?
        != root_instance_identity_sha256
    {
        bail!("project root changed before bounded artifact discovery");
    }

    let mut entries = BTreeMap::new();
    let mut directory_snapshots = vec![(
        PathBuf::new(),
        crate::process::bounded_directory_snapshot(&root, Path::new(""))
            .context("snapshot bounded retention artifact root")?,
    )];
    let mut source_file_snapshots = Vec::new();
    let deliverables_relative = Path::new("DELIVERABLES.md");
    let referenced = match read_bounded_retention_text(
        &root,
        deliverables_relative,
        MAX_RETENTION_DELIVERABLES_BYTES,
    )? {
        Some((text, snapshot)) => {
            source_file_snapshots.push((deliverables_relative.to_path_buf(), snapshot));
            extract_backtick_paths(&text)
                .into_iter()
                .filter(|value| {
                    let path = Path::new(value);
                    !path.is_absolute()
                        && !path.as_os_str().is_empty()
                        && path
                            .components()
                            .all(|component| matches!(component, std::path::Component::Normal(_)))
                })
                .collect::<BTreeSet<_>>()
        }
        None => BTreeSet::new(),
    };

    for reference in &referenced {
        let relative = Path::new(reference);
        let metadata = crate::process::open_bounded_regular_file(&root, relative)
            .ok()
            .and_then(|file| file.metadata().ok())
            .filter(|metadata| metadata.is_file());
        insert_bounded_project_entry(
            &mut entries,
            project_root,
            reference,
            metadata.as_ref(),
            "Deliverables surface reference",
            "project_deliverables",
            if has_deliverable_extension(relative) {
                "handoff"
            } else {
                "review"
            },
            if metadata.is_some() {
                "referenced"
            } else {
                "missing"
            },
            "A human-facing deliverables surface selected this artifact for inspection.",
            vec![
                format!("project:{project_key}"),
                "surface:DELIVERABLES.md".to_string(),
            ],
        );
    }

    let mut observed_entries = 0_u64;
    let mut output_candidates = Vec::new();
    for output_root in OUTPUT_ROOTS {
        let relative = Path::new(output_root);
        match walk_bounded_output_directory(
            &root,
            relative,
            1,
            scan_started,
            budgets,
            &mut observed_entries,
            &mut directory_snapshots,
            &mut output_candidates,
        ) {
            Ok(()) => {}
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) => {}
            Err(error) => return Err(error),
        }
    }
    output_candidates.sort_by(|left, right| left.0.cmp(&right.0));
    for (relative, metadata) in output_candidates {
        let relative_path = relative.to_string_lossy().replace('\\', "/");
        let referenced_by_deliverables = referenced.contains(&relative_path);
        insert_bounded_project_entry(
            &mut entries,
            project_root,
            &relative_path,
            Some(&metadata),
            if referenced_by_deliverables {
                "Referenced human-facing output"
            } else {
                "Unreferenced human-facing output"
            },
            "project_output_scan",
            if referenced_by_deliverables {
                "handoff"
            } else {
                "review"
            },
            if referenced_by_deliverables {
                "referenced"
            } else {
                "needs_triage"
            },
            if referenced_by_deliverables {
                "Selected output is already promoted to the deliverables surface."
            } else {
                "Human-facing output exists but is not yet promoted to the deliverables surface."
            },
            vec![format!("project:{project_key}")],
        );
    }

    if scan_started.elapsed().as_millis() as u64 >= budgets.max_elapsed_ms {
        bail!("bounded artifact discovery exceeded its elapsed-time budget");
    }
    let mut entries = entries.into_values().collect::<Vec<_>>();
    entries.sort_by(|left, right| {
        left.source
            .cmp(&right.source)
            .then(left.retention_class.cmp(&right.retention_class))
            .then(left.path.cmp(&right.path))
    });
    if entries.len() > MAX_INDEX_ENTRIES {
        bail!("bounded artifact discovery exceeded its retained-entry budget");
    }
    let total_entries = entries.len();
    let summary = summarize_entries(&entries, total_entries, 0);
    let profile_name = if profile.is_empty() {
        "default"
    } else {
        profile
    };
    Ok(BoundedRetentionArtifactIndex {
        index: ArtifactIndex {
            schema: ARTIFACT_INDEX_SCHEMA,
            generated_at: Utc::now(),
            profile: operator_safe_text(profile_name),
            project_key: operator_safe_text(project_key),
            project_root: Some(operator_safe_path(project_root.to_string_lossy().as_ref())),
            summary,
            entries,
            redaction: ArtifactIndexRedaction {
                operator_safe: true,
                path_policy: "summary_first_paths_in_json",
            },
            authority: ArtifactIndexAuthority {
                read_only: true,
                does_not_authorize: vec![
                    "delete",
                    "move",
                    "archive",
                    "publish",
                    "accepting output as truth without closeout receipt review",
                ],
            },
        },
        directory_snapshots,
        source_file_snapshots,
    })
}

fn read_bounded_retention_text(
    root: &std::fs::File,
    relative: &Path,
    max_bytes: u64,
) -> Result<Option<(String, crate::process::FilesystemObjectSnapshot)>> {
    let mut file = match crate::process::open_bounded_regular_file(root, relative) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("open bounded retention source text"),
    };
    let initial = crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(max_bytes + 1)
        .read_to_end(&mut bytes)
        .context("read bounded retention source text")?;
    if bytes.len() as u64 > max_bytes {
        bail!("bounded retention source text exceeds its byte budget");
    }
    let final_snapshot = crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let current = crate::process::open_bounded_regular_file(root, relative)
        .context("reopen bounded retention source text")?;
    let current_snapshot =
        crate::process::FilesystemObjectSnapshot::from_metadata(&current.metadata()?);
    if initial != final_snapshot || initial != current_snapshot {
        bail!("bounded retention source text changed while it was read");
    }
    Ok(Some((
        String::from_utf8_lossy(&bytes).into_owned(),
        initial,
    )))
}

#[allow(clippy::too_many_arguments)]
fn insert_bounded_project_entry(
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
    project_root: &Path,
    relative_path: &str,
    metadata: Option<&std::fs::Metadata>,
    label: &str,
    source: &str,
    retention_class: &str,
    review_status: &str,
    why_it_matters: &str,
    refs: Vec<String>,
) {
    let path = project_root.join(relative_path);
    let kind = artifact_kind(&path);
    let path_text = path.to_string_lossy().into_owned();
    let key = format!("{source}|{kind}|{path_text}");
    let entry = ArtifactIndexEntry {
        id: artifact_id(&key),
        label: operator_safe_text(label),
        source: source.to_string(),
        kind,
        path: operator_safe_path(&path_text),
        relative_path: Some(operator_safe_text(relative_path)),
        present: metadata.is_some(),
        bytes: metadata.map(std::fs::Metadata::len),
        modified_at: metadata
            .and_then(|metadata| metadata.modified().ok())
            .map(Into::into),
        retention_class: retention_class.to_string(),
        review_status: review_status.to_string(),
        why_it_matters: why_it_matters.to_string(),
        refs: refs
            .into_iter()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
    };
    entries
        .entry(key)
        .and_modify(|existing| merge_entry(existing, &entry))
        .or_insert(entry);
}

#[allow(clippy::too_many_arguments)]
fn walk_bounded_output_directory(
    root: &std::fs::File,
    relative: &Path,
    depth: usize,
    scan_started: Instant,
    budgets: RetentionDiscoveryBudgets,
    observed_entries: &mut u64,
    snapshots: &mut Vec<(PathBuf, crate::process::FilesystemObjectSnapshot)>,
    candidates: &mut Vec<(PathBuf, std::fs::Metadata)>,
) -> Result<()> {
    if depth > budgets.max_depth {
        bail!("bounded artifact discovery exceeded its depth budget");
    }
    if scan_started.elapsed().as_millis() as u64 >= budgets.max_elapsed_ms {
        bail!("bounded artifact discovery exceeded its elapsed-time budget");
    }
    let remaining = budgets.max_entries.saturating_sub(*observed_entries);
    if remaining == 0 {
        bail!("bounded artifact discovery exceeded its directory-entry budget");
    }
    let read = crate::process::read_bounded_directory(
        root,
        relative,
        remaining,
        scan_started,
        budgets.max_elapsed_ms,
    )?;
    *observed_entries = observed_entries.saturating_add(read.entries_observed);
    if read.limit.is_some() || !read.stable {
        bail!("bounded artifact discovery was incomplete or unstable");
    }
    snapshots.push((relative.to_path_buf(), read.snapshot));
    for name in read.names {
        let name = name
            .to_str()
            .context("bounded artifact path is not valid UTF-8")?;
        let child = relative.join(name);
        if depth + 1 > budgets.max_depth {
            bail!("bounded artifact discovery exceeded its depth budget");
        }
        match crate::process::open_bounded_regular_file(root, &child) {
            Ok(file) => {
                let metadata = file
                    .metadata()
                    .context("inspect bounded artifact candidate")?;
                if !metadata.is_file() {
                    bail!("bounded artifact candidate is not one regular file");
                }
                if has_deliverable_extension(&child) {
                    candidates.push((child, metadata));
                    if candidates.len() > MAX_INDEX_ENTRIES {
                        bail!("bounded artifact discovery exceeded its retained-entry budget");
                    }
                }
            }
            Err(_) => walk_bounded_output_directory(
                root,
                &child,
                depth + 1,
                scan_started,
                budgets,
                observed_entries,
                snapshots,
                candidates,
            )
            .with_context(|| format!("classify bounded artifact path {}", child.display()))?,
        }
    }
    Ok(())
}

fn build_artifact_index_v2(
    project_root: &Path,
    source: &BoundedRetentionArtifactIndex,
    root_id: &str,
    root_path_identity_sha256: &str,
    root_instance_identity_sha256: &str,
    scan_started: Instant,
) -> Result<ArtifactIndexV2> {
    let index = &source.index;
    let root = crate::process::open_bounded_root(project_root)
        .with_context(|| format!("open artifact-index root {}", project_root.display()))?;
    if crate::document_lifecycle::root_instance_identity_sha256_from_descriptor(&root)?
        != root_instance_identity_sha256
    {
        bail!("project root changed before artifact-index v2 hashing");
    }
    let mut entries = Vec::with_capacity(index.entries.len());
    let mut total_hashed_bytes = 0_u64;
    let mut artifact_file_snapshots = Vec::new();
    for entry in &index.entries {
        if scan_started.elapsed().as_millis() as u64 >= MAX_RETENTION_DISCOVERY_MS {
            bail!("artifact-index v2 exceeded its elapsed-time budget");
        }
        let Some(relative_path) = entry.relative_path.as_deref() else {
            continue;
        };
        let relative = Path::new(relative_path);
        if relative.is_absolute()
            || relative.as_os_str().is_empty()
            || !relative
                .components()
                .all(|component| matches!(component, std::path::Component::Normal(_)))
        {
            entries.push(ArtifactIndexEntryV2 {
                artifact_id: entry.id.clone(),
                relative_path: Some(relative_path.to_string()),
                present: entry.present,
                size_bytes: entry.bytes,
                modified_at: None,
                content_sha256: None,
                exact_current_identity: false,
                status: "invalid_relative_path".to_string(),
            });
            continue;
        }
        let opened = crate::process::open_bounded_regular_file(&root, relative);
        let Ok(mut file) = opened else {
            entries.push(ArtifactIndexEntryV2 {
                artifact_id: entry.id.clone(),
                relative_path: Some(relative_path.to_string()),
                present: entry.present,
                size_bytes: entry.bytes,
                modified_at: None,
                content_sha256: None,
                exact_current_identity: false,
                status: "not_one_current_regular_file".to_string(),
            });
            continue;
        };
        let metadata = file.metadata().context("inspect bounded artifact")?;
        let remaining_total = MAX_RETENTION_DISCOVERY_TOTAL_BYTES
            .checked_sub(total_hashed_bytes)
            .context("artifact-index v2 total byte budget was exceeded")?;
        if !metadata.is_file()
            || metadata.len() > MAX_RETENTION_ARTIFACT_BYTES
            || metadata.len() > remaining_total
        {
            bail!("artifact-index v2 cannot completely hash its bounded source set");
        }
        let path_metadata = fs::symlink_metadata(project_root.join(relative))
            .with_context(|| format!("inspect artifact path {relative_path}"))?;
        if path_metadata.file_type().is_symlink()
            || crate::process::filesystem_file_identity(&path_metadata)
                != crate::process::filesystem_file_identity(&metadata)
        {
            bail!("artifact changed while its bounded descriptor was opened");
        }
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        Read::by_ref(&mut file)
            .take(remaining_total.min(MAX_RETENTION_ARTIFACT_BYTES) + 1)
            .read_to_end(&mut bytes)
            .with_context(|| format!("read bounded artifact {relative_path}"))?;
        if bytes.len() as u64 > remaining_total.min(MAX_RETENTION_ARTIFACT_BYTES) {
            bail!("artifact-index v2 exceeded its byte budget while reading");
        }
        let final_metadata = file.metadata().context("reinspect bounded artifact")?;
        let final_path_metadata = fs::symlink_metadata(project_root.join(relative))
            .with_context(|| format!("reinspect artifact path {relative_path}"))?;
        if crate::process::filesystem_file_identity(&metadata)
            != crate::process::filesystem_file_identity(&final_metadata)
            || metadata.len() != final_metadata.len()
            || metadata.modified().ok() != final_metadata.modified().ok()
            || final_path_metadata.file_type().is_symlink()
            || crate::process::filesystem_file_identity(&final_path_metadata)
                != crate::process::filesystem_file_identity(&final_metadata)
        {
            bail!("artifact changed while its content hash was computed");
        }
        total_hashed_bytes = total_hashed_bytes
            .checked_add(bytes.len() as u64)
            .context("artifact-index v2 total byte budget overflow")?;
        artifact_file_snapshots.push((
            relative.to_path_buf(),
            crate::process::FilesystemObjectSnapshot::from_metadata(&final_metadata),
        ));
        entries.push(ArtifactIndexEntryV2 {
            artifact_id: entry.id.clone(),
            relative_path: Some(relative_path.to_string()),
            present: true,
            size_bytes: Some(metadata.len()),
            modified_at: metadata
                .modified()
                .ok()
                .map(|value| StrictUtcTimestamp::from_datetime(value.into())),
            content_sha256: Some(format!("{:x}", Sha256::digest(&bytes))),
            exact_current_identity: true,
            status: "exact_current".to_string(),
        });
    }
    entries.sort_by(|left, right| left.artifact_id.cmp(&right.artifact_id));
    for (relative, expected) in &source.directory_snapshots {
        let current = crate::process::bounded_directory_snapshot(&root, relative)
            .with_context(|| format!("reinspect artifact directory {}", relative.display()))?;
        if &current != expected {
            bail!("artifact source directory changed while artifact-index v2 was built");
        }
    }
    for (relative, expected) in &source.source_file_snapshots {
        let current = crate::process::open_bounded_regular_file(&root, relative)
            .with_context(|| format!("reopen artifact source file {}", relative.display()))?;
        let current = crate::process::FilesystemObjectSnapshot::from_metadata(&current.metadata()?);
        if &current != expected {
            bail!("artifact source file changed while artifact-index v2 was built");
        }
    }
    for (relative, expected) in &artifact_file_snapshots {
        let current = crate::process::open_bounded_regular_file(&root, relative)
            .with_context(|| format!("reopen indexed artifact {}", relative.display()))?;
        let current = crate::process::FilesystemObjectSnapshot::from_metadata(&current.metadata()?);
        if &current != expected {
            bail!("indexed artifact changed before artifact-index v2 completed");
        }
    }
    for entry in entries.iter().filter(|entry| !entry.exact_current_identity) {
        let Some(relative) = entry.relative_path.as_deref() else {
            continue;
        };
        if crate::process::open_bounded_regular_file(&root, Path::new(relative)).is_ok() {
            bail!("non-regular artifact source became a current regular file");
        }
    }
    if scan_started.elapsed().as_millis() as u64 >= MAX_RETENTION_DISCOVERY_MS {
        bail!("artifact-index v2 exceeded its elapsed-time budget");
    }
    if crate::document_lifecycle::root_instance_identity_sha256(project_root)?
        != root_instance_identity_sha256
    {
        bail!("project root changed while artifact-index v2 was built");
    }
    let source_bytes = serde_json::to_vec(&entries).context("serialize artifact-index v2 rows")?;
    Ok(ArtifactIndexV2 {
        schema: ARTIFACT_INDEX_SCHEMA_V2,
        generated_at: Utc::now(),
        project_key: index.project_key.clone(),
        root_id: root_id.to_string(),
        root_path_identity_sha256: root_path_identity_sha256.to_string(),
        root_instance_identity_sha256: root_instance_identity_sha256.to_string(),
        source_snapshot_sha256: format!("{:x}", Sha256::digest(source_bytes)),
        entries,
        read_only: true,
        file_mutation_authorized: false,
    })
}

fn retention_observation_from_artifact(
    root_id: &str,
    entry: &ArtifactIndexEntryV2,
) -> Result<DocumentLifecycleObservationV1> {
    let relative_path = entry
        .relative_path
        .as_deref()
        .context("retention artifact is outside the validated project root")?;
    let size_bytes = entry
        .size_bytes
        .context("retention artifact lacks a bounded current size")?;
    let content_sha256 = entry
        .content_sha256
        .as_deref()
        .context("retention artifact lacks a bounded current content hash")?;
    if !entry.exact_current_identity {
        bail!("retention artifact does not have one exact current identity");
    }
    let modified_at = entry
        .modified_at
        .as_ref()
        .context("retention artifact lacks a bounded current modified time")?;
    Ok(DocumentLifecycleObservationV1 {
        observation_id: crate::document_lifecycle::observation_id(
            root_id,
            relative_path,
            size_bytes,
            Some(modified_at.as_datetime()),
            Some(content_sha256),
        ),
        relative_path: relative_path.to_string(),
        size_bytes,
        modified_at: modified_at.clone(),
        content_sha256: content_sha256.to_string(),
    })
}

fn normalize_retention_path_selector(project_root: &Path, value: &str) -> Result<String> {
    let value = value.trim();
    if value.is_empty() {
        bail!("retention path selector cannot be empty");
    }
    let path = Path::new(value);
    let relative = if path.is_absolute() {
        let canonical = path
            .canonicalize()
            .with_context(|| format!("resolve retention path {value}"))?;
        canonical
            .strip_prefix(project_root)
            .context("retention path is outside the validated project root")?
            .to_path_buf()
    } else {
        path.to_path_buf()
    };
    if relative.as_os_str().is_empty()
        || !relative
            .components()
            .all(|component| matches!(component, std::path::Component::Normal(_)))
    {
        bail!("retention path must be one bounded relative file path");
    }
    Ok(relative.to_string_lossy().replace('\\', "/"))
}

fn exact_document_entry<'a>(
    entries: &'a [DocumentCatalogEntryV2],
    document_id: &str,
) -> Result<&'a DocumentCatalogEntryV2> {
    let matches = entries
        .iter()
        .filter(|entry| entry.document_id.as_deref() == Some(document_id))
        .collect::<Vec<_>>();
    match matches.as_slice() {
        [entry] if !entry.lifecycle_stale => Ok(entry),
        [_, _, ..] => bail!("document selector resolves to multiple observations"),
        [_] => bail!("document selector resolves only to stale lifecycle state"),
        [] => bail!("document selector is not an exact current lifecycle head"),
    }
}

fn resolve_retention_target_v2(
    profile: &str,
    project_root: &Path,
    project_key: &str,
    input: RetentionResolverInput<'_>,
) -> Result<ResolvedRetentionContextV2> {
    let RetentionResolverInput {
        artifact_id: artifact_id_selector,
        path: path_selector,
        document_id: document_id_selector,
        action: requested_action,
        summary_document_id,
        reason,
        allow_applied_state,
    } = input;
    if artifact_id_selector.is_none() && path_selector.is_none() && document_id_selector.is_none() {
        bail!("one of --artifact-id, --path, or --document-id is required");
    }
    let profile_dir = profile_dir_path(if profile.is_empty() {
        "default"
    } else {
        profile
    })?;
    let catalog = scan_project_with_lifecycle(
        &profile_dir,
        project_root,
        &CatalogOptions {
            project_key: Some(project_key.to_string()),
            ..CatalogOptions::default()
        },
    )?;
    let root = catalog
        .base_catalog
        .roots
        .first()
        .context("retention resolver requires exactly one project root")?;
    let root_instance_identity_sha256 =
        crate::document_lifecycle::root_instance_identity_sha256(project_root)?;
    let artifact_scan_started = Instant::now();
    let bounded_index = build_bounded_retention_artifact_index(
        profile,
        project_key,
        project_root,
        &root_instance_identity_sha256,
        artifact_scan_started,
        RetentionDiscoveryBudgets::default(),
    )?;
    let legacy_index = &bounded_index.index;
    let artifact_index = build_artifact_index_v2(
        project_root,
        &bounded_index,
        &root.root_id,
        &root.path_identity_sha256,
        &root_instance_identity_sha256,
        artifact_scan_started,
    )?;
    let normalized_path = path_selector
        .map(|value| normalize_retention_path_selector(project_root, value))
        .transpose()?;

    let selected_artifact = artifact_id_selector
        .map(|selector| {
            let matches = artifact_index
                .entries
                .iter()
                .filter(|entry| entry.artifact_id == selector.trim())
                .collect::<Vec<_>>();
            match matches.as_slice() {
                [entry] => Ok(*entry),
                [] => bail!("artifact selector is not present in artifact-index v2"),
                _ => bail!("artifact selector is ambiguous"),
            }
        })
        .transpose()?;
    let selected_document = document_id_selector
        .map(|selector| exact_document_entry(&catalog.entries, selector.trim()))
        .transpose()?;

    if let (Some(path), Some(artifact)) = (&normalized_path, selected_artifact) {
        if artifact.relative_path.as_deref() != Some(path.as_str()) {
            bail!("artifact and path selectors do not identify the same current file");
        }
    }
    if let (Some(path), Some(document)) = (&normalized_path, selected_document) {
        if document.observation.relative_path != *path {
            bail!("document and path selectors do not identify the same current file");
        }
    }

    let selected_relative_path = selected_document
        .map(|entry| entry.observation.relative_path.as_str())
        .or_else(|| selected_artifact.and_then(|entry| entry.relative_path.as_deref()))
        .or(normalized_path.as_deref())
        .context("selectors do not identify one current relative path")?;
    let artifacts_at_path = artifact_index
        .entries
        .iter()
        .filter(|entry| entry.relative_path.as_deref() == Some(selected_relative_path))
        .collect::<Vec<_>>();
    if artifacts_at_path.len() > 1 {
        bail!("multiple artifact IDs resolve to the same current physical path");
    }
    let artifact = selected_artifact.or_else(|| artifacts_at_path.first().copied());
    if let Some(artifact) = artifact {
        if !artifact.exact_current_identity || artifact.content_sha256.is_none() {
            bail!("hashless or stale artifact identity cannot authorize retention");
        }
    }

    let catalog_rows_at_path = catalog
        .entries
        .iter()
        .filter(|entry| entry.observation.relative_path == selected_relative_path)
        .collect::<Vec<_>>();
    if catalog.lifecycle.status != LifecycleProjectionStatusV1::Absent
        && catalog_rows_at_path
            .iter()
            .any(|entry| entry.lifecycle_stale)
    {
        bail!("stale lifecycle identity at the selected path blocks artifact-only normalization");
    }
    let documents_at_path = catalog_rows_at_path
        .iter()
        .copied()
        .filter(|entry| entry.document_id.is_some())
        .collect::<Vec<_>>();
    if documents_at_path.len() > 1 {
        bail!("multiple durable documents resolve to the same current physical path");
    }
    let document = selected_document.or_else(|| documents_at_path.first().copied());
    if document.is_none()
        && !matches!(
            catalog.lifecycle.status,
            LifecycleProjectionStatusV1::Absent
        )
        && (!matches!(catalog.lifecycle.status, LifecycleProjectionStatusV1::Valid)
            || !catalog.base_catalog.scan.complete
            || !catalog.lifecycle_findings.is_empty())
    {
        bail!("incomplete or unresolved lifecycle projection blocks artifact-only normalization");
    }
    if let (Some(artifact), Some(document)) = (artifact, document) {
        if artifact.relative_path.as_deref() != Some(document.observation.relative_path.as_str())
            || artifact.size_bytes != Some(document.observation.size_bytes)
            || artifact.content_sha256.as_deref() != document.observation.content_sha256.as_deref()
        {
            bail!("artifact and document selectors disagree on current content identity");
        }
    }

    let observation = if let Some(document) = document {
        let modified_at = document
            .observation
            .modified_at
            .context("document retention requires a current modified time")?;
        DocumentLifecycleObservationV1 {
            observation_id: document.observation.observation_id.clone(),
            relative_path: document.observation.relative_path.clone(),
            size_bytes: document.observation.size_bytes,
            modified_at: StrictUtcTimestamp::from_datetime(modified_at),
            content_sha256: document
                .observation
                .content_sha256
                .clone()
                .context("document retention requires a current content hash")?,
        }
    } else {
        retention_observation_from_artifact(
            &root.root_id,
            artifact.context("path selector is neither a document nor an indexed artifact")?,
        )?
    };

    let document_id = document.and_then(|entry| entry.document_id.clone());
    let expected_document_head_event_id =
        document.and_then(|entry| entry.lifecycle_head_event_id.clone());
    let source_kind = if artifact.is_some() {
        RetentionTargetSourceKind::Artifact
    } else {
        RetentionTargetSourceKind::Document
    };
    let retention_target_id = artifact
        .map(|entry| entry.artifact_id.clone())
        .or_else(|| document_id.clone())
        .context("retention target lacks canonical identity")?;
    let target = RetentionTargetV2 {
        retention_target_id,
        source_kind,
        artifact_id: artifact.map(|entry| entry.artifact_id.clone()),
        document_id: document_id.clone(),
        expected_document_head_event_id,
        observation,
    };

    let before = document.and_then(|entry| entry.reviewed_state.clone());
    let after = if requested_action == RetentionRequestAction::Promote {
        None
    } else {
        before.clone().map(|mut state| {
            state.retention_disposition = match requested_action {
                RetentionRequestAction::Keep => RetentionDisposition::Keep,
                RetentionRequestAction::Preserve => RetentionDisposition::Preserved,
                RetentionRequestAction::SummaryKeep => RetentionDisposition::SummaryKept,
                RetentionRequestAction::Archive => RetentionDisposition::RetirementCandidate,
                RetentionRequestAction::Quarantine => RetentionDisposition::QuarantineCandidate,
                RetentionRequestAction::Dispose => RetentionDisposition::DisposalCandidate,
                RetentionRequestAction::Promote => unreachable!(),
            };
            state.summary_document_id = if requested_action == RetentionRequestAction::SummaryKeep {
                summary_document_id.map(str::to_string)
            } else {
                None
            };
            state
        })
    };
    if requested_action == RetentionRequestAction::SummaryKeep {
        let summary_id =
            summary_document_id.context("summary-keep requires --summary-document-id")?;
        let summary = exact_document_entry(&catalog.entries, summary_id)?;
        if summary.reviewed_state.as_ref().is_none_or(|state| {
            state.activity_state != crate::document_lifecycle::ActivityState::Active
        }) {
            bail!("summary-keep requires one exact reviewed-active summary document");
        }
    }
    let reason = operator_safe_text(reason.unwrap_or_else(|| requested_action.approval_impact()));
    let proposal = RetentionProposalV2 {
        project_key: project_key.to_string(),
        root_id: root.root_id.clone(),
        root_path_identity_sha256: root.path_identity_sha256.clone(),
        root_instance_identity_sha256,
        filesystem_snapshot_sha256: catalog
            .base_catalog
            .source_identities
            .filesystem_snapshot
            .content_sha256
            .clone(),
        artifact_snapshot_sha256: artifact_index.source_snapshot_sha256.clone(),
        target: target.clone(),
        requested_action: requested_action.as_v2(),
        before: if requested_action == RetentionRequestAction::Promote {
            None
        } else {
            before
        },
        after,
        reason,
    };
    if proposal.before.is_some() && proposal.before == proposal.after {
        if !allow_applied_state {
            bail!("requested retention action is already the current reviewed disposition");
        }
    } else {
        proposal.validate()?;
    }

    let review_item = if let Some(artifact) = artifact {
        let legacy = legacy_index
            .entries
            .iter()
            .find(|entry| entry.id == artifact.artifact_id)
            .context("artifact-index v2 row lacks its v1 presentation row")?;
        retention_review_item(legacy)
    } else {
        let document = document.context("catalog-only target lost its document row")?;
        ArtifactRetentionReviewItem {
            id: document_id.clone().unwrap_or_default(),
            label: document.observation.relative_path.clone(),
            source: "document_catalog_v2".to_string(),
            kind: document.observation.media_type.clone(),
            path: document.observation.relative_path.clone(),
            relative_path: Some(document.observation.relative_path.clone()),
            present: true,
            bytes: Some(document.observation.size_bytes),
            retention_class: "reviewed_document".to_string(),
            review_status: "exact_current_head".to_string(),
            retention_scope: "document_lifecycle".to_string(),
            scope_reason: "Exact current lifecycle document without an artifact crosswalk."
                .to_string(),
            recommended_action: "review".to_string(),
            reason: "Uses the durable document ID as the retention target.".to_string(),
            why_it_matters: "Retention changes are projected through the lifecycle ledger."
                .to_string(),
            refs: vec![format!(
                "document:{}",
                document_id.clone().unwrap_or_default()
            )],
        }
    };
    Ok(ResolvedRetentionContextV2 {
        resolution: CanonicalRetentionResolutionV2 {
            schema: "retention_target_resolution.v2",
            project_key: project_key.to_string(),
            artifact_snapshot_sha256: artifact_index.source_snapshot_sha256.clone(),
            artifact_index,
            target,
            reviewed_state: document.and_then(|entry| entry.reviewed_state.clone()),
            selector_evidence: [
                artifact_id_selector.map(|value| format!("artifact_id:{}", value.trim())),
                normalized_path.map(|value| format!("relative_path:{value}")),
                document_id_selector.map(|value| format!("document_id:{}", value.trim())),
            ]
            .into_iter()
            .flatten()
            .collect(),
            authority: ArtifactRetentionReviewAuthority {
                read_only: true,
                does_not_authorize: vec!["delete", "move", "archive", "publish"],
            },
        },
        proposal,
        review_item,
    })
}

fn build_retention_review(index: &ArtifactIndex) -> ArtifactRetentionReview {
    let mut action_required = Vec::new();
    let mut keep_sample = Vec::new();
    let mut summary = ArtifactRetentionReviewSummary {
        total_entries: index.summary.total_entries,
        missing_entries: index.summary.missing_entries,
        review_required_entries: index.summary.review_required_entries,
        disposal_candidate_entries: index.summary.disposal_candidate_entries,
        archive_candidate_entries: index
            .entries
            .iter()
            .filter(|entry| entry.retention_class == "archive_candidate")
            .count(),
        unreferenced_human_facing_entries: index
            .entries
            .iter()
            .filter(|entry| {
                entry.review_status == "needs_triage" && is_human_facing_artifact(entry)
            })
            .count(),
        ..ArtifactRetentionReviewSummary::default()
    };

    for entry in &index.entries {
        let item = retention_review_item(entry);
        let scope_counts = summary
            .by_scope
            .entry(item.retention_scope.clone())
            .or_default();
        scope_counts.total_entries += 1;
        if retention_review_action_is_keep(&item.recommended_action) {
            summary.keep_entries += 1;
            scope_counts.keep_entries += 1;
            if keep_sample.len() < MAX_RETENTION_REVIEW_KEEP_SAMPLE {
                keep_sample.push(item);
            }
        } else {
            summary.action_required_entries += 1;
            scope_counts.action_required_entries += 1;
            action_required.push(item);
        }
    }

    let queue_items = action_required.len();
    let truncated_queue_items = queue_items.saturating_sub(MAX_RETENTION_REVIEW_ITEMS);
    action_required.sort_by_key(|item| retention_scope_sort_key(&item.retention_scope));
    action_required.truncate(MAX_RETENTION_REVIEW_ITEMS);
    summary.queue_items = action_required.len();
    summary.truncated_queue_items = truncated_queue_items;

    ArtifactRetentionReview {
        schema: ARTIFACT_RETENTION_REVIEW_SCHEMA,
        generated_at: Utc::now(),
        profile: index.profile.clone(),
        project_key: index.project_key.clone(),
        project_root: index.project_root.clone(),
        recommendations: retention_recommendations(&summary),
        summary,
        queues: ArtifactRetentionReviewQueues {
            action_required,
            keep_sample,
        },
        redaction: ArtifactIndexRedaction {
            operator_safe: true,
            path_policy: "summary_first_paths_in_json",
        },
        authority: ArtifactRetentionReviewAuthority {
            read_only: true,
            does_not_authorize: vec![
                "delete",
                "move",
                "archive",
                "publish",
                "accepting an artifact as disposable without operator approval",
            ],
        },
    }
}

fn retention_review_item(entry: &ArtifactIndexEntry) -> ArtifactRetentionReviewItem {
    let (recommended_action, reason) = retention_action_and_reason(entry);
    let (retention_scope, scope_reason) = retention_scope_and_reason(entry);
    ArtifactRetentionReviewItem {
        id: entry.id.clone(),
        label: entry.label.clone(),
        source: entry.source.clone(),
        kind: entry.kind.clone(),
        path: entry.path.clone(),
        relative_path: entry.relative_path.clone(),
        present: entry.present,
        bytes: entry.bytes,
        retention_class: entry.retention_class.clone(),
        review_status: entry.review_status.clone(),
        retention_scope: retention_scope.to_string(),
        scope_reason: scope_reason.to_string(),
        recommended_action: recommended_action.to_string(),
        reason: reason.to_string(),
        why_it_matters: entry.why_it_matters.clone(),
        refs: entry.refs.clone(),
    }
}

fn retention_review_action_is_keep(action: &str) -> bool {
    matches!(
        action,
        "preserve" | "preserve_in_place_recorded" | "retention_review_resolved"
    )
}

fn retention_scope_and_reason(entry: &ArtifactIndexEntry) -> (&'static str, &'static str) {
    let closeout_statuses = entry
        .refs
        .iter()
        .filter_map(|reference| reference.strip_prefix("closeout_status:"))
        .collect::<BTreeSet<_>>();
    if closeout_statuses.contains("accepted") {
        return (
            "active_accepted",
            "Referenced by an accepted closeout receipt.",
        );
    }
    if closeout_statuses.contains("retired_incomplete") {
        return (
            "retired_historical",
            "Referenced by retired historical closeout evidence and no accepted receipt.",
        );
    }
    if !closeout_statuses.is_empty()
        || entry
            .refs
            .iter()
            .any(|reference| reference.starts_with("closeout:"))
    {
        return (
            "unresolved_closeout",
            "Referenced by a closeout that still needs review or has no accepted-truth receipt.",
        );
    }
    if entry.source.starts_with("project_") {
        return (
            "project_surface",
            "Discovered from project-local documentation or output scans.",
        );
    }
    (
        "profile_artifact",
        "Discovered from profile-local Forager artifacts outside closeout scope.",
    )
}

fn retention_scope_sort_key(scope: &str) -> u8 {
    match scope {
        "active_accepted" => 0,
        "unresolved_closeout" => 1,
        "project_surface" => 2,
        "profile_artifact" => 3,
        "retired_historical" => 4,
        _ => 5,
    }
}

fn retention_action_and_reason(entry: &ArtifactIndexEntry) -> (&'static str, &'static str) {
    if !entry.present {
        return (
            "restore_or_update_reference",
            "The index points to an artifact that is not present.",
        );
    }
    if archive_preserve_in_place_is_recorded(entry) {
        return (
            "preserve_in_place_recorded",
            "An accepted closeout receipt already resolved this archive review as preserve-in-place.",
        );
    }
    if closeout_retention_support_review_is_resolved(entry) {
        return (
            "retention_review_resolved",
            "An accepted closeout receipt already resolved this retention review; preserve this support artifact as provenance.",
        );
    }
    match entry.retention_class.as_str() {
        "disposal_candidate" => {
            return (
                "review_disposal_candidate",
                "The artifact is marked as a disposal candidate, but mutation needs separate approval.",
            );
        }
        "archive_candidate" => {
            return (
                "review_archive_candidate",
                "The artifact is marked as an archive candidate, but movement needs separate approval.",
            );
        }
        _ => {}
    }
    if entry.review_status == "needs_triage" && is_human_facing_artifact(entry) {
        return (
            "promote_to_deliverables_or_mark_disposable",
            "A human-facing output exists outside the deliverables surface.",
        );
    }
    if entry.review_status.contains("review") || entry.review_status == "needs_triage" {
        return (
            "review_before_relying",
            "The artifact is useful but still needs operator review before relying on it.",
        );
    }
    (
        "preserve",
        "The artifact is referenced and currently has no retention action.",
    )
}

fn archive_preserve_in_place_is_recorded(entry: &ArtifactIndexEntry) -> bool {
    entry.retention_class == "archive_candidate" && accepted_retention_review_is_resolved(entry)
}

fn closeout_retention_support_review_is_resolved(entry: &ArtifactIndexEntry) -> bool {
    entry.source == "profile_closeout"
        && matches!(
            entry.kind.as_str(),
            "commercial_review_packet" | "cleanup_manifest_json"
        )
        && accepted_retention_review_is_resolved(entry)
}

fn accepted_retention_review_is_resolved(entry: &ArtifactIndexEntry) -> bool {
    entry
        .refs
        .iter()
        .any(|reference| reference == "closeout_status:accepted")
        && entry
            .refs
            .iter()
            .any(|reference| reference == "closeout_retention_review:resolved_preserve_in_place")
}

fn retention_recommendations(
    summary: &ArtifactRetentionReviewSummary,
) -> Vec<ArtifactRetentionRecommendation> {
    let mut recommendations = Vec::new();
    if summary.missing_entries > 0 {
        recommendations.push(ArtifactRetentionRecommendation {
            kind: "restore_or_update_missing_artifacts".to_string(),
            priority: "high".to_string(),
            count: summary.missing_entries,
            summary: "Some indexed artifacts are missing from disk.".to_string(),
            next_action: "Restore the artifact or update the referring surface before relying on the package."
                .to_string(),
        });
    }
    if summary.unreferenced_human_facing_entries > 0 {
        recommendations.push(ArtifactRetentionRecommendation {
            kind: "triage_unreferenced_human_outputs".to_string(),
            priority: "normal".to_string(),
            count: summary.unreferenced_human_facing_entries,
            summary: "Human-facing outputs exist outside the deliverables surface.".to_string(),
            next_action:
                "Promote useful outputs to DELIVERABLES.md or mark them for later disposal review."
                    .to_string(),
        });
    }
    if summary.disposal_candidate_entries > 0 {
        recommendations.push(ArtifactRetentionRecommendation {
            kind: "review_disposal_or_archive_candidates".to_string(),
            priority: "normal".to_string(),
            count: summary.disposal_candidate_entries,
            summary: "Some artifacts are archive or disposal candidates.".to_string(),
            next_action:
                "Inspect the candidate list and request an explicit cleanup/archive action if appropriate."
                    .to_string(),
        });
    }
    if summary.review_required_entries > 0 {
        recommendations.push(ArtifactRetentionRecommendation {
            kind: "review_required_artifacts".to_string(),
            priority: "normal".to_string(),
            count: summary.review_required_entries,
            summary: "Some artifacts need review before they can support handoff or acceptance."
                .to_string(),
            next_action:
                "Review the artifact meaning and evidence status before treating it as reusable."
                    .to_string(),
        });
    }
    if let Some(retired) = summary.by_scope.get("retired_historical") {
        if retired.action_required_entries > 0 {
            recommendations.push(ArtifactRetentionRecommendation {
                kind: "separate_retired_historical_artifacts".to_string(),
                priority: "low".to_string(),
                count: retired.action_required_entries,
                summary:
                    "Some retention actions belong only to retired evidence-incomplete history."
                        .to_string(),
                next_action:
                    "Review these separately from active accepted or unresolved closeout artifacts."
                        .to_string(),
            });
        }
    }
    if recommendations.is_empty() {
        recommendations.push(ArtifactRetentionRecommendation {
            kind: "no_retention_action_required".to_string(),
            priority: "info".to_string(),
            count: 0,
            summary: "No retention action is required by the current index.".to_string(),
            next_action: "Continue using the artifact index as the read-only inventory."
                .to_string(),
        });
    }
    recommendations
}

fn is_human_facing_artifact(entry: &ArtifactIndexEntry) -> bool {
    matches!(entry.kind.as_str(), "html" | "png" | "jpg" | "jpeg" | "pdf")
}

fn resolve_project_root(path: Option<&Path>) -> Result<Option<PathBuf>> {
    let explicit = path.is_some();
    let path = path
        .map(PathBuf::from)
        .unwrap_or(std::env::current_dir().context("resolve current directory")?);
    if !path.exists() {
        if explicit {
            bail!("project path does not exist: {}", path.display());
        }
        return Ok(None);
    }
    let canonical = path
        .canonicalize()
        .with_context(|| format!("resolve project path {}", path.display()))?;
    if explicit && !canonical.is_dir() {
        bail!("project path is not a directory: {}", canonical.display());
    }
    Ok(canonical.is_dir().then_some(canonical))
}

fn default_project_key(project_root: Option<&Path>) -> String {
    project_root
        .and_then(|root| root.file_name())
        .and_then(|name| name.to_str())
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "default".to_string())
}

fn collect_project_outputs(
    root: &Path,
    project_key: &str,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let deliverables_path = root.join("DELIVERABLES.md");
    let referenced = if deliverables_path.exists() {
        extract_backtick_paths(&read_text_lossy(&deliverables_path))
            .into_iter()
            .collect::<BTreeSet<_>>()
    } else {
        BTreeSet::new()
    };

    for reference in &referenced {
        let path = root.join(reference);
        add_entry(
            entries,
            EntryInput {
                label: "Deliverables surface reference".to_string(),
                source: "project_deliverables".to_string(),
                kind: artifact_kind(&path),
                path: path.to_string_lossy().into_owned(),
                relative_path: Some(operator_safe_text(reference)),
                present_override: None,
                retention_class: if has_deliverable_extension(&path) {
                    "handoff".to_string()
                } else {
                    "review".to_string()
                },
                review_status: if path.exists() {
                    "referenced".to_string()
                } else {
                    "missing".to_string()
                },
                why_it_matters:
                    "A human-facing deliverables surface selected this artifact for inspection."
                        .to_string(),
                refs: vec![
                    format!("project:{project_key}"),
                    "surface:DELIVERABLES.md".to_string(),
                ],
            },
        );
    }

    for path in collect_output_candidates(root) {
        let relative = rel_path(root, &path);
        let referenced_by_deliverables = referenced.contains(&relative);
        add_entry(
            entries,
            EntryInput {
                label: if referenced_by_deliverables {
                    "Referenced human-facing output".to_string()
                } else {
                    "Unreferenced human-facing output".to_string()
                },
                source: "project_output_scan".to_string(),
                kind: artifact_kind(&path),
                path: path.to_string_lossy().into_owned(),
                relative_path: Some(operator_safe_text(&relative)),
                present_override: Some(true),
                retention_class: if referenced_by_deliverables {
                    "handoff".to_string()
                } else {
                    "review".to_string()
                },
                review_status: if referenced_by_deliverables {
                    "referenced".to_string()
                } else {
                    "needs_triage".to_string()
                },
                why_it_matters: if referenced_by_deliverables {
                    "Selected output is already promoted to the deliverables surface.".to_string()
                } else {
                    "Human-facing output exists but is not yet promoted to the deliverables surface.".to_string()
                },
                refs: vec![format!("project:{project_key}")],
            },
        );
    }
    Ok(())
}

fn collect_profile_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    collect_closeout_artifacts(profile_dir, project_key, entries)?;
    collect_project_initialization_artifacts(profile_dir, project_key, entries)?;
    collect_ondesk_capture_artifacts(profile_dir, project_key, entries)?;
    Ok(())
}

fn collect_closeout_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let closeouts_dir = profile_dir.join("offdesk_closeouts");
    if !closeouts_dir.exists() {
        return Ok(());
    }
    for entry in
        fs::read_dir(&closeouts_dir).with_context(|| format!("read {}", closeouts_dir.display()))?
    {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let plan_path = artifact_dir.join("closeout_plan.json");
        let plan = read_json_object(&plan_path);
        if !value_matches_project(&plan, project_key) {
            continue;
        }
        let closeout_id =
            json_text(&plan, "/closeout_id").unwrap_or_else(|| artifact_dir_name(&artifact_dir));
        let closeout_refs = closeout_scope_refs(&artifact_dir);
        add_path_entry(
            entries,
            PathEntryInput {
                label: "Closeout plan",
                source: "profile_closeout",
                kind: "closeout_plan",
                path: &plan_path,
                retention_class: "review",
                review_status: "referenced",
                why_it_matters:
                    "Explains what Offdesk produced and what must be reviewed before acceptance.",
                refs: closeout_refs_for(&closeout_id, &closeout_refs),
            },
        );

        if let Some(artifacts) = plan.get("artifacts").and_then(Value::as_object) {
            for (field, value) in artifacts {
                let Some(path) = value.as_str().filter(|value| !value.trim().is_empty()) else {
                    continue;
                };
                let (retention_class, review_status, why) = classify_closeout_artifact(field);
                add_entry(
                    entries,
                    EntryInput {
                        label: closeout_artifact_label(field),
                        source: "profile_closeout".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: None,
                        retention_class: retention_class.to_string(),
                        review_status: review_status.to_string(),
                        why_it_matters: why.to_string(),
                        refs: closeout_refs_for(&closeout_id, &closeout_refs),
                    },
                );
            }
        }

        collect_closeout_task_artifacts(&plan, &closeout_id, &closeout_refs, entries);
        collect_closeout_file_operations(&plan, &closeout_id, &closeout_refs, entries);
        collect_closeout_review_files(&artifact_dir, &closeout_id, &closeout_refs, entries)?;
    }
    Ok(())
}

fn collect_closeout_task_artifacts(
    plan: &Value,
    closeout_id: &str,
    closeout_refs: &[String],
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) {
    for task in plan
        .get("tasks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let task_id = json_text(task, "/task_id").unwrap_or_else(|| "unknown".to_string());
        for (field, label, retention, review, why) in [
            (
                "result_artifact_path",
                "Task result artifact",
                "evidence",
                "referenced",
                "Task result artifacts are provenance anchors for Ondesk review.",
            ),
            (
                "log_artifact_path",
                "Task log artifact",
                "archive_candidate",
                "requires_review",
                "Raw logs may be large but remain useful while referenced by a closeout.",
            ),
        ] {
            if let Some(path) = task.get(field).and_then(Value::as_str) {
                add_entry(
                    entries,
                    EntryInput {
                        label: label.to_string(),
                        source: "closeout_task".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: None,
                        retention_class: retention.to_string(),
                        review_status: review.to_string(),
                        why_it_matters: why.to_string(),
                        refs: closeout_refs_for(closeout_id, closeout_refs)
                            .into_iter()
                            .chain([format!("task:{task_id}")])
                            .collect(),
                    },
                );
            }
        }
        for artifact in task
            .get("artifact_refs")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let Some(path) = artifact.get("path").and_then(Value::as_str) else {
                continue;
            };
            let artifact_id = artifact
                .get("artifact_id")
                .and_then(Value::as_str)
                .unwrap_or("declared");
            add_entry(
                entries,
                EntryInput {
                    label: "Declared task artifact".to_string(),
                    source: "closeout_task".to_string(),
                    kind: "artifact_ref".to_string(),
                    path: path.to_string(),
                    relative_path: None,
                    present_override: artifact.get("present").and_then(Value::as_bool),
                    retention_class: "evidence".to_string(),
                    review_status: "referenced".to_string(),
                    why_it_matters: "Declared artifacts must remain available for review."
                        .to_string(),
                    refs: vec![
                        format!("closeout:{closeout_id}"),
                        format!("task:{task_id}"),
                        format!("artifact:{artifact_id}"),
                    ]
                    .into_iter()
                    .chain(closeout_refs.iter().cloned())
                    .collect(),
                },
            );
        }
    }

    for run in plan
        .get("background_runs")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let ticket_id = json_text(run, "/ticket_id").unwrap_or_else(|| "unknown".to_string());
        for (field, present_field, label, retention, review, why) in [
            (
                "result_artifact_path",
                "result_artifact_present",
                "Background result artifact",
                "evidence",
                "referenced",
                "Background result artifacts are required for morning review.",
            ),
            (
                "log_artifact_path",
                "log_artifact_present",
                "Background log artifact",
                "archive_candidate",
                "requires_review",
                "Background logs may be large but should be archived while referenced.",
            ),
        ] {
            if let Some(path) = run.get(field).and_then(Value::as_str) {
                add_entry(
                    entries,
                    EntryInput {
                        label: label.to_string(),
                        source: "closeout_background".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: run.get(present_field).and_then(Value::as_bool),
                        retention_class: retention.to_string(),
                        review_status: review.to_string(),
                        why_it_matters: why.to_string(),
                        refs: vec![
                            format!("closeout:{closeout_id}"),
                            format!("background:{ticket_id}"),
                        ]
                        .into_iter()
                        .chain(closeout_refs.iter().cloned())
                        .collect(),
                    },
                );
            }
        }
    }
}

fn collect_closeout_file_operations(
    plan: &Value,
    closeout_id: &str,
    closeout_refs: &[String],
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) {
    for operation in plan
        .get("file_operations")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(path) = operation.get("path").and_then(Value::as_str) else {
            continue;
        };
        let op = operation
            .get("operation")
            .and_then(Value::as_str)
            .unwrap_or("review");
        let retention = match op {
            "archive_candidate" => "archive_candidate",
            "delete_candidate" => "disposal_candidate",
            _ => "review",
        };
        let requires_review = operation
            .get("requires_commercial_review")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            || operation
                .get("requires_human_approval")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        add_entry(
            entries,
            EntryInput {
                label: format!("Closeout {op}"),
                source: "closeout_file_operation".to_string(),
                kind: op.to_string(),
                path: path.to_string(),
                relative_path: None,
                present_override: operation.get("present").and_then(Value::as_bool),
                retention_class: retention.to_string(),
                review_status: if requires_review {
                    "requires_review".to_string()
                } else {
                    "referenced".to_string()
                },
                why_it_matters: operation
                    .get("reason")
                    .and_then(Value::as_str)
                    .map(operator_safe_text)
                    .unwrap_or_else(|| {
                        "Closeout proposed this file operation for operator review.".to_string()
                    }),
                refs: closeout_refs_for(closeout_id, closeout_refs),
            },
        );
    }
}

fn collect_closeout_review_files(
    artifact_dir: &Path,
    closeout_id: &str,
    closeout_refs: &[String],
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    for entry in
        fs::read_dir(artifact_dir).with_context(|| format!("read {}", artifact_dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if name.starts_with("closeout_review_") && name.ends_with(".json") {
            add_path_entry(
                entries,
                PathEntryInput {
                    label: "Closeout review record",
                    source: "profile_closeout_review",
                    kind: "closeout_review",
                    path: &path,
                    retention_class: "acceptance",
                    review_status: "referenced",
                    why_it_matters:
                        "Records the review verdict used before Ondesk accepts or revises work.",
                    refs: closeout_refs_for(closeout_id, closeout_refs),
                },
            );
        } else if name.starts_with("closeout_receipt_") && name.ends_with(".json") {
            add_path_entry(
                entries,
                PathEntryInput {
                    label: "Closeout receipt",
                    source: "profile_closeout_review",
                    kind: "closeout_receipt",
                    path: &path,
                    retention_class: "acceptance",
                    review_status: "referenced",
                    why_it_matters: "Records accepted-truth status and remaining follow-ups.",
                    refs: closeout_refs_for(closeout_id, closeout_refs),
                },
            );
        }
    }
    Ok(())
}

fn collect_project_initialization_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let root = profile_dir.join("project_initializations");
    if !root.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(&root).with_context(|| format!("read {}", root.display()))? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let profile_path = artifact_dir.join("PROJECT_OPERATION_PROFILE.json");
        let profile = read_json_object(&profile_path);
        if !value_project_key_matches(&profile, project_key) {
            continue;
        }
        let init_id =
            json_text(&profile, "/id").unwrap_or_else(|| artifact_dir_name(&artifact_dir));
        for (field, fallback, label, retention, why) in [
            (
                "operation_profile_path",
                "PROJECT_OPERATION_PROFILE.json",
                "Project operation profile",
                "handoff",
                "Defines the project operation scope a fresh harness should use.",
            ),
            (
                "ondesk_start_package_path",
                "ONDESK_START_PACKAGE.md",
                "Ondesk start package",
                "handoff",
                "Gives the next harness a bounded starting packet.",
            ),
            (
                "offdesk_ready_check_path",
                "OFFDESK_READY_CHECK.json",
                "Offdesk ready check",
                "review",
                "Records whether the project is ready for runtime execution.",
            ),
            (
                "module_operation_preflight_path",
                "MODULE_OPERATION_PREFLIGHT.json",
                "Module operation preflight",
                "review",
                "Lists module readiness and blockers before runtime work.",
            ),
            (
                "governance_surface_hints_path",
                "GOVERNANCE_SURFACE_HINTS.md",
                "Governance surface hints",
                "review",
                "Suggests missing current-state, decision, and deliverable surfaces.",
            ),
        ] {
            let path = profile
                .get(field)
                .and_then(Value::as_str)
                .map(PathBuf::from)
                .unwrap_or_else(|| artifact_dir.join(fallback));
            add_path_entry(
                entries,
                PathEntryInput {
                    label,
                    source: "profile_project_initialization",
                    kind: field,
                    path: &path,
                    retention_class: retention,
                    review_status: "referenced",
                    why_it_matters: why,
                    refs: vec![format!("project_init:{init_id}")],
                },
            );
        }
    }
    Ok(())
}

fn collect_ondesk_capture_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let root = profile_dir.join("ondesk_captures");
    if !root.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(&root).with_context(|| format!("read {}", root.display()))? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let capture_path = entry.path().join("capture.json");
        let capture = read_json_object(&capture_path);
        if !value_project_key_matches(&capture, project_key) {
            continue;
        }
        let capture_id =
            json_text(&capture, "/id").unwrap_or_else(|| artifact_dir_name(&entry.path()));
        for (field, fallback, label, retention, why) in [
            (
                "capture_path",
                "capture.json",
                "Ondesk capture",
                "review",
                "Stores bounded scrollback and context captured from a live harness.",
            ),
            (
                "prompt_package_path",
                "PROMPT_CONTEXT.md",
                "Ondesk prompt package",
                "handoff",
                "Rehydrates a fresh harness from captured context.",
            ),
        ] {
            let path = capture
                .get(field)
                .and_then(Value::as_str)
                .map(PathBuf::from)
                .unwrap_or_else(|| entry.path().join(fallback));
            add_path_entry(
                entries,
                PathEntryInput {
                    label,
                    source: "profile_ondesk_capture",
                    kind: field,
                    path: &path,
                    retention_class: retention,
                    review_status: "referenced",
                    why_it_matters: why,
                    refs: vec![format!("ondesk_capture:{capture_id}")],
                },
            );
        }
    }
    Ok(())
}

fn add_path_entry(entries: &mut BTreeMap<String, ArtifactIndexEntry>, input: PathEntryInput<'_>) {
    add_entry(
        entries,
        EntryInput {
            label: input.label.to_string(),
            source: input.source.to_string(),
            kind: input.kind.to_string(),
            path: input.path.to_string_lossy().into_owned(),
            relative_path: None,
            present_override: None,
            retention_class: input.retention_class.to_string(),
            review_status: input.review_status.to_string(),
            why_it_matters: input.why_it_matters.to_string(),
            refs: input.refs,
        },
    );
}

fn add_entry(entries: &mut BTreeMap<String, ArtifactIndexEntry>, input: EntryInput) {
    let present = input
        .present_override
        .unwrap_or_else(|| Path::new(&input.path).exists());
    let metadata = Path::new(&input.path).metadata().ok();
    let key = format!("{}|{}|{}", input.source, input.kind, input.path);
    let id = artifact_id(&key);
    let refs = input
        .refs
        .into_iter()
        .map(|value| operator_safe_text(&value))
        .filter(|value| !value.trim().is_empty())
        .collect::<BTreeSet<_>>();
    let entry = ArtifactIndexEntry {
        id,
        label: operator_safe_text(&input.label),
        source: operator_safe_text(&input.source),
        kind: operator_safe_text(&input.kind),
        path: operator_safe_path(&input.path),
        relative_path: input.relative_path.map(|value| operator_safe_text(&value)),
        present,
        bytes: metadata
            .as_ref()
            .filter(|_| present)
            .map(|metadata| metadata.len()),
        modified_at: metadata
            .and_then(|metadata| metadata.modified().ok())
            .map(Into::into),
        retention_class: operator_safe_text(&input.retention_class),
        review_status: if present {
            operator_safe_text(&input.review_status)
        } else {
            "missing".to_string()
        },
        why_it_matters: operator_safe_text(&input.why_it_matters),
        refs: refs.into_iter().collect(),
    };

    entries
        .entry(key)
        .and_modify(|existing| merge_entry(existing, &entry))
        .or_insert(entry);
}

fn merge_entry(existing: &mut ArtifactIndexEntry, incoming: &ArtifactIndexEntry) {
    existing.present |= incoming.present;
    if existing.bytes.is_none() {
        existing.bytes = incoming.bytes;
    }
    if existing.modified_at.is_none() {
        existing.modified_at = incoming.modified_at;
    }
    let mut refs = existing.refs.iter().cloned().collect::<BTreeSet<_>>();
    refs.extend(incoming.refs.iter().cloned());
    existing.refs = refs.into_iter().collect();
    if existing.review_status == "referenced" && incoming.review_status == "requires_review" {
        existing.review_status = incoming.review_status.clone();
    }
}

fn summarize_entries(
    entries: &[ArtifactIndexEntry],
    total_entries: usize,
    truncated_entries: usize,
) -> ArtifactIndexSummary {
    let mut summary = ArtifactIndexSummary {
        total_entries,
        present_entries: entries.iter().filter(|entry| entry.present).count(),
        missing_entries: entries.iter().filter(|entry| !entry.present).count(),
        review_required_entries: entries
            .iter()
            .filter(|entry| {
                entry.review_status.contains("review") || entry.review_status == "needs_triage"
            })
            .count(),
        disposal_candidate_entries: entries
            .iter()
            .filter(|entry| {
                matches!(
                    entry.retention_class.as_str(),
                    "archive_candidate" | "disposal_candidate"
                )
            })
            .count(),
        human_facing_entries: entries
            .iter()
            .filter(|entry| matches!(entry.kind.as_str(), "html" | "png" | "jpg" | "jpeg" | "pdf"))
            .count(),
        truncated_entries,
        ..ArtifactIndexSummary::default()
    };
    for entry in entries {
        *summary
            .by_retention_class
            .entry(entry.retention_class.clone())
            .or_default() += 1;
        *summary.by_source.entry(entry.source.clone()).or_default() += 1;
    }
    summary
}

fn human_summary(index: &Value) -> String {
    let mut output = String::new();
    output.push_str("Artifact Index\n");
    if let Some(project_key) = index.get("project_key").and_then(Value::as_str) {
        output.push_str(&format!("  project: {project_key}\n"));
    }
    if let Some(root) = index.get("project_root").and_then(Value::as_str) {
        output.push_str(&format!("  root: {root}\n"));
    }
    let summary = index.get("summary").unwrap_or(&Value::Null);
    output.push_str(&format!(
        "  entries: {} total, {} present, {} missing\n",
        json_u64(summary, "total_entries"),
        json_u64(summary, "present_entries"),
        json_u64(summary, "missing_entries")
    ));
    output.push_str(&format!(
        "  review: {} review-required, {} disposal/archive candidates, {} human-facing\n",
        json_u64(summary, "review_required_entries"),
        json_u64(summary, "disposal_candidate_entries"),
        json_u64(summary, "human_facing_entries")
    ));
    if let Some(by_class) = summary.get("by_retention_class").and_then(Value::as_object) {
        let classes = by_class
            .iter()
            .map(|(key, value)| format!("{key}={}", value.as_u64().unwrap_or_default()))
            .collect::<Vec<_>>()
            .join(", ");
        if !classes.is_empty() {
            output.push_str(&format!("  retention: {classes}\n"));
        }
    }
    output.push_str("  notable artifacts:\n");
    if let Some(entries) = index.get("entries").and_then(Value::as_array) {
        for entry in entries.iter().take(MAX_HUMAN_ROWS) {
            let label = entry
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("Artifact");
            let retention = entry
                .get("retention_class")
                .and_then(Value::as_str)
                .unwrap_or("review");
            let status = entry
                .get("review_status")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            let path = entry
                .get("relative_path")
                .or_else(|| entry.get("path"))
                .and_then(Value::as_str)
                .unwrap_or("-");
            let why = entry
                .get("why_it_matters")
                .and_then(Value::as_str)
                .unwrap_or("Review before use.");
            output.push_str(&format!(
                "    - {label}: {why} [{retention}/{status}] `{path}`\n"
            ));
        }
    }
    output.push_str("  authority: read-only index; deletion, movement, archive, and publication need separate approval\n");
    output
}

fn retention_review_human_summary(review: &Value) -> String {
    let mut output = String::new();
    output.push_str("Artifact Retention Review\n");
    if let Some(project_key) = review.get("project_key").and_then(Value::as_str) {
        output.push_str(&format!("  project: {project_key}\n"));
    }
    if let Some(root) = review.get("project_root").and_then(Value::as_str) {
        output.push_str(&format!("  root: {root}\n"));
    }
    let summary = review.get("summary").unwrap_or(&Value::Null);
    output.push_str(&format!(
        "  entries: {} total, {} action-required, {} keep\n",
        json_u64(summary, "total_entries"),
        json_u64(summary, "action_required_entries"),
        json_u64(summary, "keep_entries")
    ));
    output.push_str(&format!(
        "  review: {} missing, {} review-required, {} disposal/archive candidates, {} unreferenced human-facing\n",
        json_u64(summary, "missing_entries"),
        json_u64(summary, "review_required_entries"),
        json_u64(summary, "disposal_candidate_entries"),
        json_u64(summary, "unreferenced_human_facing_entries")
    ));
    if let Some(by_scope) = summary.get("by_scope").and_then(Value::as_object) {
        let scopes = by_scope
            .iter()
            .map(|(scope, counts)| {
                format!(
                    "{scope}={} action/{} total",
                    json_u64(counts, "action_required_entries"),
                    json_u64(counts, "total_entries")
                )
            })
            .collect::<Vec<_>>()
            .join(", ");
        if !scopes.is_empty() {
            output.push_str(&format!("  scope: {scopes}\n"));
        }
    }
    if let Some(recommendations) = review.get("recommendations").and_then(Value::as_array) {
        output.push_str("  recommendations:\n");
        for recommendation in recommendations.iter().take(5) {
            let kind = recommendation
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or("review");
            let priority = recommendation
                .get("priority")
                .and_then(Value::as_str)
                .unwrap_or("normal");
            let count = recommendation
                .get("count")
                .and_then(Value::as_u64)
                .unwrap_or_default();
            let next = recommendation
                .get("next_action")
                .and_then(Value::as_str)
                .unwrap_or("Review before mutation.");
            output.push_str(&format!("    - {kind} ({priority}, {count}): {next}\n"));
        }
    }
    if let Some(items) = review
        .pointer("/queues/action_required")
        .and_then(Value::as_array)
        .filter(|items| !items.is_empty())
    {
        output.push_str("  action-required artifacts:\n");
        for item in items.iter().take(MAX_HUMAN_ROWS) {
            let label = item
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("Artifact");
            let action = item
                .get("recommended_action")
                .and_then(Value::as_str)
                .unwrap_or("review_before_relying");
            let retention = item
                .get("retention_class")
                .and_then(Value::as_str)
                .unwrap_or("review");
            let status = item
                .get("review_status")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            let path = item
                .get("relative_path")
                .or_else(|| item.get("path"))
                .and_then(Value::as_str)
                .unwrap_or("-");
            output.push_str(&format!(
                "    - {label}: {action} [{retention}/{status}] `{path}`\n"
            ));
        }
    } else {
        output.push_str("  action-required artifacts: none\n");
    }
    output.push_str(
        "  authority: read-only review; cleanup, archive, movement, and deletion need separate approval\n",
    );
    output
}

fn collect_output_candidates(root: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for name in OUTPUT_ROOTS {
        let base = root.join(name);
        if !base.exists() {
            continue;
        }
        collect_files_into(&base, &mut candidates);
    }
    candidates.retain(|path| has_deliverable_extension(path));
    candidates.sort();
    candidates
}

fn collect_files_into(root: &Path, files: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_files_into(&path, files);
        } else if path.is_file() {
            files.push(path);
        }
    }
}

fn has_deliverable_extension(path: &Path) -> bool {
    let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
        return false;
    };
    let normalized = format!(".{}", extension.to_ascii_lowercase());
    DELIVERABLE_EXTENSIONS.contains(&normalized.as_str())
}

fn extract_backtick_paths(text: &str) -> Vec<String> {
    let regex = Regex::new(r"`([^`]+)`").expect("valid path regex");
    let mut paths = Vec::new();
    for captures in regex.captures_iter(text) {
        let Some(value) = captures.get(1).map(|value| value.as_str().trim()) else {
            continue;
        };
        if value.is_empty()
            || value.contains(' ')
            || ["http://", "https://", "forager ", "python ", "./.venv"]
                .iter()
                .any(|prefix| value.starts_with(prefix))
            || value.chars().any(|ch| matches!(ch, '*' | '<' | '>' | '|'))
        {
            continue;
        }
        let file_name_has_dot = Path::new(value)
            .file_name()
            .and_then(|item| item.to_str())
            .is_some_and(|name| name.contains('.'));
        if value.contains('/') || file_name_has_dot {
            paths.push(value.to_string());
        }
    }
    paths
}

fn read_json_object(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok())
        .filter(Value::is_object)
        .unwrap_or(Value::Object(Default::default()))
}

fn read_text_lossy(path: &Path) -> String {
    fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default()
}

fn value_matches_project(value: &Value, project_key: Option<&str>) -> bool {
    let Some(project_key) = project_key else {
        return true;
    };
    if value_project_key_matches(value, Some(project_key)) {
        return true;
    }
    value
        .pointer("/filters/project_key")
        .and_then(Value::as_str)
        .is_some_and(|value| value == project_key)
        || value
            .get("tasks")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .any(|task| {
                task.get("project_key")
                    .and_then(Value::as_str)
                    .is_some_and(|value| value == project_key)
            })
}

fn value_project_key_matches(value: &Value, project_key: Option<&str>) -> bool {
    match project_key {
        Some(project_key) => value
            .get("project_key")
            .and_then(Value::as_str)
            .is_some_and(|value| value == project_key),
        None => true,
    }
}

fn classify_closeout_artifact(field: &str) -> (&'static str, &'static str, &'static str) {
    match field {
        "return_package_markdown" => (
            "handoff",
            "referenced",
            "Rehydrates a fresh harness with reviewed context and next steps.",
        ),
        "closeout_receipt_json" => (
            "acceptance",
            "referenced",
            "Records whether execution is accepted truth or still needs follow-up.",
        ),
        "commercial_review_packet" | "cleanup_manifest_json" => (
            "review",
            "requires_review",
            "Supports disposal, archive, and commercial review decisions.",
        ),
        _ => (
            "review",
            "referenced",
            "Supports closeout review and later audit.",
        ),
    }
}

fn closeout_artifact_label(field: &str) -> String {
    match field {
        "return_package_markdown" => "Ondesk return package".to_string(),
        "closeout_receipt_json" => "Closeout receipt".to_string(),
        "commercial_review_packet" => "Commercial review packet".to_string(),
        "cleanup_manifest_json" => "Cleanup manifest".to_string(),
        "closeout_plan_json" => "Closeout plan".to_string(),
        "closeout_plan_markdown" => "Closeout plan markdown".to_string(),
        _ => field.replace('_', " "),
    }
}

fn artifact_kind(path: &Path) -> String {
    path.extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase())
        .unwrap_or_else(|| "file".to_string())
}

fn artifact_dir_name(path: &Path) -> String {
    path.file_name()
        .and_then(|value| value.to_str())
        .map(operator_safe_text)
        .unwrap_or_else(|| "unknown".to_string())
}

fn closeout_refs_for(closeout_id: &str, closeout_refs: &[String]) -> Vec<String> {
    std::iter::once(format!("closeout:{closeout_id}"))
        .chain(closeout_refs.iter().cloned())
        .collect()
}

fn closeout_scope_refs(artifact_dir: &Path) -> Vec<String> {
    let latest_review = latest_closeout_review_value(artifact_dir);
    let receipt = latest_review
        .as_ref()
        .and_then(|review| review.get("closeout_receipt"));
    let status = receipt
        .and_then(|receipt| receipt.get("acceptance_status"))
        .and_then(Value::as_str)
        .map(operator_safe_text)
        .unwrap_or_else(|| "pending_review".to_string());
    let mut refs = vec![format!("closeout_status:{status}")];
    if let Some(receipt_id) = receipt
        .and_then(|receipt| receipt.get("receipt_id"))
        .and_then(Value::as_str)
        .map(operator_safe_text)
    {
        refs.push(format!("closeout_receipt:{receipt_id}"));
    }
    if let Some(retention_review) = receipt
        .and_then(|receipt| receipt.get("retention_review"))
        .and_then(Value::as_str)
        .map(operator_safe_text)
    {
        refs.push(format!("closeout_retention_review:{retention_review}"));
    }
    refs
}

fn latest_closeout_review_value(artifact_dir: &Path) -> Option<Value> {
    let mut reviews = Vec::new();
    for entry in fs::read_dir(artifact_dir).ok()? {
        let Ok(entry) = entry else {
            continue;
        };
        let path = entry.path();
        let Some(filename) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if !filename.starts_with("closeout_review_") || !filename.ends_with(".json") {
            continue;
        }
        let value = read_json_object(&path);
        let Some(reviewed_at) = value
            .get("reviewed_at")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc))
        else {
            continue;
        };
        reviews.push((reviewed_at, value));
    }
    reviews.sort_by_key(|(reviewed_at, _)| *reviewed_at);
    reviews.pop().map(|(_, review)| review)
}

fn json_text(value: &Value, pointer: &str) -> Option<String> {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .map(operator_safe_text)
}

fn json_u64(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or_default()
}

fn artifact_id(value: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    format!("artifact-{}", &digest[..12])
}

fn operator_safe_path(path: &str) -> String {
    operator_safe_text(path).replace(['\n', '\r'], " ")
}

fn rel_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
        .replace(['\n', '\r'], " ")
}

#[cfg(test)]
mod retention_discovery_tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn bounded_discovery_rejects_directory_entry_overrun() -> Result<()> {
        let temp = tempdir()?;
        let project = temp.path().join("project");
        fs::create_dir_all(project.join("outputs"))?;
        fs::write(project.join("outputs/a.tmp"), b"a")?;
        fs::write(project.join("outputs/b.tmp"), b"b")?;
        let root_identity = crate::document_lifecycle::root_instance_identity_sha256(&project)?;
        let result = build_bounded_retention_artifact_index(
            "default",
            "bounded-test",
            &project,
            &root_identity,
            Instant::now(),
            RetentionDiscoveryBudgets {
                max_entries: 1,
                max_depth: 8,
                max_elapsed_ms: 5_000,
            },
        );
        assert!(result.is_err());
        Ok(())
    }

    #[test]
    fn bounded_discovery_rechecks_initially_absent_sources() -> Result<()> {
        let temp = tempdir()?;
        let project = temp.path().join("project");
        fs::create_dir_all(project.join("outputs"))?;
        fs::write(project.join("outputs/report.html"), b"<h1>Report</h1>\n")?;
        let root_identity = crate::document_lifecycle::root_instance_identity_sha256(&project)?;
        let scan_started = Instant::now();
        let source = build_bounded_retention_artifact_index(
            "default",
            "bounded-test",
            &project,
            &root_identity,
            scan_started,
            RetentionDiscoveryBudgets::default(),
        )?;
        fs::write(
            project.join("DELIVERABLES.md"),
            b"- [Report](outputs/report.html)\n",
        )?;
        let result = build_artifact_index_v2(
            &project,
            &source,
            "root-test",
            &"0".repeat(64),
            &root_identity,
            scan_started,
        );
        assert!(result.is_err());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn bounded_discovery_rejects_directory_symlink_cycles() -> Result<()> {
        use std::os::unix::fs::symlink;

        let temp = tempdir()?;
        let project = temp.path().join("project");
        fs::create_dir_all(project.join("outputs"))?;
        symlink(".", project.join("outputs/loop"))?;
        let root_identity = crate::document_lifecycle::root_instance_identity_sha256(&project)?;
        let result = build_bounded_retention_artifact_index(
            "default",
            "bounded-test",
            &project,
            &root_identity,
            Instant::now(),
            RetentionDiscoveryBudgets::default(),
        );
        assert!(result.is_err());
        Ok(())
    }
}
