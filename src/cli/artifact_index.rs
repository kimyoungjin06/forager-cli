//! Project and profile artifact inventory read model.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Args, ValueEnum};
use regex::Regex;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::{Path, PathBuf};

use crate::offdesk::{
    operator_safe_text, ActionApprovalMetadata, ActionApprovalRequest, ApprovalBrief,
    ApprovalBriefOption, ApprovalLedger, ApprovalStatus, ArtifactRetentionApprovalMetadata,
    PendingActionApproval, RiskLevel,
};
use crate::session::get_profile_dir;

const ARTIFACT_INDEX_SCHEMA: &str = "artifact_index.v1";
const ARTIFACT_RETENTION_REVIEW_SCHEMA: &str = "artifact_retention_review.v1";
const DELIVERABLE_EXTENSIONS: &[&str] = &[".html", ".png", ".jpg", ".jpeg", ".pdf"];
const OUTPUT_ROOTS: &[&str] = &["outputs", "web", "deliverables", "previews", "gallery"];
const MAX_INDEX_ENTRIES: usize = 240;
const MAX_HUMAN_ROWS: usize = 12;
const MAX_RETENTION_REVIEW_ITEMS: usize = 40;
const MAX_RETENTION_REVIEW_KEEP_SAMPLE: usize = 12;
const MAX_RETENTION_REVIEW_PROJECTION_ITEMS: usize = 10;

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
    #[arg(long, conflicts_with = "path_filter")]
    artifact_id: Option<String>,

    /// Artifact path or relative path to request approval for
    #[arg(long = "path", conflicts_with = "artifact_id")]
    path_filter: Option<String>,

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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ValueEnum)]
#[serde(rename_all = "snake_case")]
pub enum RetentionRequestAction {
    Keep,
    Promote,
    Archive,
    Dispose,
}

impl RetentionRequestAction {
    fn as_str(self) -> &'static str {
        match self {
            Self::Keep => "keep",
            Self::Promote => "promote",
            Self::Archive => "archive",
            Self::Dispose => "dispose",
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Keep => "Keep artifact",
            Self::Promote => "Promote artifact",
            Self::Archive => "Archive artifact",
            Self::Dispose => "Dispose artifact",
        }
    }

    fn risk_level(self) -> RiskLevel {
        match self {
            Self::Archive | Self::Dispose => RiskLevel::Destructive,
            Self::Keep | Self::Promote => RiskLevel::CanonicalMutation,
        }
    }

    fn approval_impact(self) -> &'static str {
        match self {
            Self::Keep => {
                "Records approval to preserve this artifact in the retention plan; it does not edit project files."
            }
            Self::Promote => {
                "Records approval to promote this artifact in a later reviewed surface update; it does not edit DELIVERABLES.md."
            }
            Self::Archive => {
                "Records approval to prepare an archive step for this artifact; it does not move the file."
            }
            Self::Dispose => {
                "Records approval to prepare a disposal step for this artifact; it does not delete the file."
            }
        }
    }
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

fn build_retention_approval_request(
    profile: &str,
    args: ProjectRetentionRequestArgs,
) -> Result<ArtifactRetentionApprovalRequestReport> {
    const ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA: &str =
        "artifact_retention_approval_request.v1";
    const RETENTION_APPROVAL_ACTION: &str = "maintenance.artifact_cleanup";

    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = operator_safe_text(args.project_key.trim());
    if project_key.trim().is_empty() {
        bail!("--project-key cannot be empty");
    }
    let request_id = operator_safe_text(args.request_id.trim());
    if request_id.trim().is_empty() {
        bail!("--request-id cannot be empty");
    }

    let index = build_artifact_index(profile, Some(&project_key), project_root.as_deref())?;
    let target = retention_request_target(
        &index,
        args.artifact_id.as_deref(),
        args.path_filter.as_deref(),
    )?;
    validate_retention_request_action(args.action, &target)?;

    let generated_at = Utc::now();
    let task_id = retention_request_task_id(args.action, &target, args.task_id.as_deref());
    let risk_level = args.action.risk_level();
    let brief = retention_request_approval_brief(args.action, &target, &project_key, &request_id);
    let mut request = ActionApprovalRequest::new(
        project_key.clone(),
        request_id.clone(),
        task_id.clone(),
        RETENTION_APPROVAL_ACTION,
        risk_level,
    );
    request.mutation_class = Some(RETENTION_APPROVAL_ACTION.to_string());
    request.preview = retention_request_preview(args.action, &target);
    request.reason = retention_request_reason(args.action, &target, args.reason.as_deref());
    request.source_surface = "project.retention_request".to_string();
    request.ttl = Duration::minutes(args.ttl_minutes.max(1));
    request.metadata = Some(retention_request_metadata(
        generated_at,
        args.action,
        &target,
        brief,
    ));

    let ledger = ApprovalLedger::new(get_profile_dir(profile)?);
    let (mut session, _) = ledger.begin_session(generated_at)?;
    let pending = session.ensure_pending_without_consuming_grant(request, generated_at)?;
    session.flush()?;

    let approvals = ledger.load()?;
    let approval = pending.or_else(|| {
        matching_retention_approval(
            &approvals,
            &project_key,
            &request_id,
            &task_id,
            RETENTION_APPROVAL_ACTION,
            risk_level,
        )
    });
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
        schema: ARTIFACT_RETENTION_APPROVAL_REQUEST_SCHEMA,
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

fn retention_request_target(
    index: &ArtifactIndex,
    artifact_id: Option<&str>,
    path_filter: Option<&str>,
) -> Result<ArtifactRetentionReviewItem> {
    let artifact_id = artifact_id.map(operator_safe_text);
    let path_filter = path_filter.map(operator_safe_path);
    match (artifact_id.as_deref(), path_filter.as_deref()) {
        (Some(_), Some(_)) => bail!("provide only one of --artifact-id or --path"),
        (None, None) => bail!("provide --artifact-id or --path"),
        _ => {}
    }

    let matches = index
        .entries
        .iter()
        .filter(
            |entry| match (artifact_id.as_deref(), path_filter.as_deref()) {
                (Some(id), None) => entry.id == id,
                (None, Some(path)) => {
                    entry.path == path
                        || entry
                            .relative_path
                            .as_deref()
                            .is_some_and(|rel| rel == path)
                }
                _ => false,
            },
        )
        .map(retention_review_item)
        .collect::<Vec<_>>();

    match matches.as_slice() {
        [target] => Ok(target.clone()),
        [] => bail!("no artifact retention review item matched the selector"),
        _ => bail!("selector matched multiple artifacts; use --artifact-id for a stable request"),
    }
}

fn validate_retention_request_action(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
) -> Result<()> {
    if !target.present && !matches!(action, RetentionRequestAction::Keep) {
        bail!("only keep can be requested for a missing artifact reference");
    }
    if action == RetentionRequestAction::Dispose
        && target.retention_class != "disposal_candidate"
        && target.review_status != "needs_triage"
    {
        bail!("dispose requires a disposal candidate or needs_triage item");
    }
    if action == RetentionRequestAction::Archive
        && target.retention_class != "archive_candidate"
        && target.review_status != "needs_triage"
    {
        bail!("archive requires an archive candidate or needs_triage item");
    }
    Ok(())
}

fn retention_request_task_id(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
    override_task_id: Option<&str>,
) -> String {
    override_task_id
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| {
            format!(
                "retention-{}-{}",
                action.as_str(),
                sanitize_id_fragment(&target.id)
            )
        })
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

fn retention_request_reason(
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
    extra: Option<&str>,
) -> String {
    let mut reason = format!(
        "{}. Review recommended: {}. {}",
        target.reason,
        target.recommended_action,
        action.approval_impact()
    );
    if let Some(extra) = extra
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
    {
        reason.push_str(" Operator note: ");
        reason.push_str(&extra);
    }
    reason
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

fn retention_request_metadata(
    generated_at: DateTime<Utc>,
    action: RetentionRequestAction,
    target: &ArtifactRetentionReviewItem,
    brief: ApprovalBrief,
) -> ActionApprovalMetadata {
    ActionApprovalMetadata::ArtifactRetention(Box::new(ArtifactRetentionApprovalMetadata {
        generated_at,
        artifact_id: target.id.clone(),
        label: target.label.clone(),
        source: target.source.clone(),
        artifact_kind: target.kind.clone(),
        path: target.path.clone(),
        relative_path: target.relative_path.clone(),
        present: target.present,
        bytes: target.bytes,
        retention_class: target.retention_class.clone(),
        review_status: target.review_status.clone(),
        recommended_action: target.recommended_action.clone(),
        requested_action: action.as_str().to_string(),
        reason: target.reason.clone(),
        why_it_matters: target.why_it_matters.clone(),
        refs: target.refs.clone(),
        approval_brief: Some(brief),
    }))
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

fn matching_retention_approval(
    approvals: &[PendingActionApproval],
    project_key: &str,
    request_id: &str,
    task_id: &str,
    action: &str,
    risk_level: RiskLevel,
) -> Option<PendingActionApproval> {
    approvals
        .iter()
        .find(|approval| {
            approval.project_key == project_key
                && approval.request_id == request_id
                && approval.task_id == task_id
                && approval.action == action
                && approval.risk_level == risk_level
                && approval.status == ApprovalStatus::Pending
        })
        .or_else(|| {
            approvals.iter().find(|approval| {
                approval.project_key == project_key
                    && approval.request_id == request_id
                    && approval.task_id == task_id
                    && approval.action == action
                    && approval.risk_level == risk_level
            })
        })
        .cloned()
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
    let profile_dir = get_profile_dir(profile_name)?;
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
        if item.recommended_action == "preserve" {
            summary.keep_entries += 1;
            if keep_sample.len() < MAX_RETENTION_REVIEW_KEEP_SAMPLE {
                keep_sample.push(item);
            }
        } else {
            summary.action_required_entries += 1;
            action_required.push(item);
        }
    }

    let queue_items = action_required.len();
    let truncated_queue_items = queue_items.saturating_sub(MAX_RETENTION_REVIEW_ITEMS);
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
        recommended_action: recommended_action.to_string(),
        reason: reason.to_string(),
        why_it_matters: entry.why_it_matters.clone(),
        refs: entry.refs.clone(),
    }
}

fn retention_action_and_reason(entry: &ArtifactIndexEntry) -> (&'static str, &'static str) {
    if !entry.present {
        return (
            "restore_or_update_reference",
            "The index points to an artifact that is not present.",
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
                refs: vec![format!("closeout:{closeout_id}")],
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
                        refs: vec![format!("closeout:{closeout_id}")],
                    },
                );
            }
        }

        collect_closeout_task_artifacts(&plan, &closeout_id, entries);
        collect_closeout_file_operations(&plan, &closeout_id, entries);
        collect_closeout_review_files(&artifact_dir, &closeout_id, entries)?;
    }
    Ok(())
}

fn collect_closeout_task_artifacts(
    plan: &Value,
    closeout_id: &str,
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
                        refs: vec![format!("closeout:{closeout_id}"), format!("task:{task_id}")],
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
                    ],
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
                        ],
                    },
                );
            }
        }
    }
}

fn collect_closeout_file_operations(
    plan: &Value,
    closeout_id: &str,
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
                refs: vec![format!("closeout:{closeout_id}")],
            },
        );
    }
}

fn collect_closeout_review_files(
    artifact_dir: &Path,
    closeout_id: &str,
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
                    refs: vec![format!("closeout:{closeout_id}")],
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
                    refs: vec![format!("closeout:{closeout_id}")],
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
