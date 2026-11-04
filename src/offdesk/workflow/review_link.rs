//! Explicit links from historical runtime evidence to an already committed decision.

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

use super::{ondesk_plan, ondesk_plan_store, ondesk_review::TaskDecision};
use crate::offdesk::task_queue::{OffdeskTask, OffdeskTaskStatus};

mod historical_boundary;
pub use historical_boundary::HistoricalBoundaryObservation;
mod historical_prefix;
pub use historical_prefix::HistoricalPrefixObservation;
mod narrative_reconciliation;
pub use narrative_reconciliation::NarrativeReconciliationObservation;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReviewLinkRequest {
    pub schema: String,
    pub link_request_id: String,
    pub profile_root: PathBuf,
    pub task_id: String,
    pub request_id: String,
    pub background_ticket_id: String,
    pub runtime_sha256: String,
    pub result_sha256: String,
    pub owning_plan: PathBuf,
    pub owning_plan_sha256: String,
    pub owning_journal_sha256: String,
    pub decision_request_id: String,
    pub owning_task_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assessment: Option<AssessmentReference>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub closeouts: Vec<AssessmentReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub historical_boundary: Option<AssessmentReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub historical_prefix: Option<AssessmentReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub narrative_reconciliation: Option<AssessmentReference>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssessmentReference {
    pub path: PathBuf,
    pub sha256: String,
}

/// An explicitly confirmed historical classification, never a process receipt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResultAssessment {
    pub schema: String,
    pub task_id: String,
    pub request_id: String,
    pub ticket_id: String,
    pub result_sha256: String,
    pub classification: String,
    pub reason: String,
    pub evidence_refs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub successor: Option<SuccessorReference>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SuccessorReference {
    pub task_id: String,
    pub request_id: String,
    pub ticket_id: String,
    pub runtime_sha256: String,
    pub result_sha256: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub legacy_evidence: Vec<LegacyResultEvidence>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyResultEvidence {
    pub hash_field: String,
    pub artifact: AssessmentReference,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReviewLinkPreview {
    pub schema: String,
    pub request: ReviewLinkRequest,
    pub profile_identity: (u64, u64),
    pub decision: TaskDecision,
    pub decision_ref: String,
    pub decided_by: String,
    pub result_validity: String,
    pub assessment: Option<ResultAssessment>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub historical_boundary: Option<HistoricalBoundaryObservation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub historical_prefix: Option<HistoricalPrefixObservation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub narrative_reconciliation: Option<NarrativeReconciliationObservation>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecordedReviewLink {
    pub preview: ReviewLinkPreview,
    pub preview_sha256: String,
    pub recorded_by: String,
    pub recorded_at: DateTime<Utc>,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn value_digest(mut value: serde_json::Value) -> Result<String> {
    value.sort_all_objects();
    Ok(digest(&serde_json::to_vec(&value)?))
}

pub fn runtime_sha256(task: &OffdeskTask) -> Result<String> {
    let mut value = serde_json::to_value(task)?;
    value
        .as_object_mut()
        .context("task is not an object")?
        .remove("review_links");
    value_digest(value)
}

pub fn attention_version(task: &OffdeskTask) -> String {
    let mut parts = vec![runtime_sha256(task).unwrap_or_default()];
    let mut paths = Vec::new();
    if let Some(path) = &task.result_artifact_path {
        paths.push(Path::new(&task.workdir).join(path));
    }
    if let Some(link) = task.review_links.last() {
        parts.push(link.preview_sha256.clone());
        paths.push(link.preview.request.owning_plan.clone());
        if let Ok(path) = journal_path(&link.preview.request.owning_plan) {
            paths.push(path);
        }
        if let Some(reference) = &link.preview.request.assessment {
            paths.push(reference.path.clone());
        }
        for reference in &link.preview.decision.evidence_refs {
            if let Some((_, path)) = reference
                .strip_prefix("sha256:")
                .and_then(|text| text.split_once(':'))
            {
                paths.push(PathBuf::from(path));
            }
        }
        if let Some(boundary) = &link.preview.historical_boundary {
            paths.extend([
                boundary.boundary.path.clone(),
                boundary.agreement.path.clone(),
                boundary.previous_document.path.clone(),
                boundary.next_document.path.clone(),
            ]);
        }
        if let Some(prefix) = &link.preview.historical_prefix {
            paths.extend([
                prefix.prefix.path.clone(),
                prefix.snapshot.path.clone(),
                prefix.baseline_document.path.clone(),
                prefix.agreement.path.clone(),
            ]);
        }
        if let Some(reconciled) = &link.preview.narrative_reconciliation {
            paths.extend([
                reconciled.reconciliation.path.clone(),
                reconciled.approved_document.path.clone(),
                reconciled.agreement.path.clone(),
            ]);
        }
    }
    for path in paths {
        parts.push(
            read_artifact(&path)
                .map(|bytes| digest(&bytes))
                .unwrap_or_else(|_| "unavailable".into()),
        );
    }
    digest(parts.join("\0").as_bytes())
}

pub fn read_artifact(path: &Path) -> Result<Vec<u8>> {
    ensure!(path.is_absolute(), "review artifact path must be absolute");
    ensure!(
        !path.components().any(|component| matches!(
            component,
            std::path::Component::ParentDir | std::path::Component::CurDir
        )),
        "review artifact path must be normalized"
    );
    let parent = path.parent().context("review artifact parent missing")?;
    let name = Path::new(
        path.file_name()
            .context("review artifact filename missing")?,
    );
    let root = crate::process::open_bounded_root(parent)?;
    let bytes = ondesk_plan_store::read(&root, name, ondesk_plan::MAX_PLAN_BYTES)?;
    ondesk_plan_store::active_root(parent, &root)?;
    Ok(bytes)
}

fn checked_artifact(path: &Path, expected: &str) -> Result<Vec<u8>> {
    ensure!(
        expected.len() == 64
            && expected
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid review artifact hash"
    );
    let bytes = read_artifact(path)?;
    ensure!(
        digest(&bytes) == expected,
        "review artifact changed: {}",
        path.display()
    );
    Ok(bytes)
}

fn journal_path(plan: &Path) -> Result<PathBuf> {
    let name = plan
        .file_name()
        .and_then(|name| name.to_str())
        .context("owning plan filename missing")?;
    Ok(plan.with_file_name(format!(".{name}.forager-plan-journal.json")))
}

pub fn preview(
    root: &Path,
    task: &OffdeskTask,
    request: &ReviewLinkRequest,
) -> Result<ReviewLinkPreview> {
    ensure!(
        request.schema == "offdesk_review_link_request.v1",
        "unsupported review link request"
    );
    ensure!(
        ondesk_plan::valid_id(&request.link_request_id),
        "invalid review link request ID"
    );
    ensure!(
        root.is_absolute() && root == request.profile_root,
        "review profile root mismatch"
    );
    let profile = crate::process::open_bounded_root(root)?;
    let profile_identity = crate::process::filesystem_file_identity(&profile.metadata()?)
        .context("profile identity unavailable")?;
    ensure!(
        task.status == OffdeskTaskStatus::Completed,
        "review link requires a terminal completed runtime"
    );
    ensure!(
        task.task_id == request.task_id
            && task.request_id == request.request_id
            && task.background_ticket_id.as_deref() == Some(request.background_ticket_id.as_str()),
        "runtime review identity mismatch"
    );
    ensure!(
        runtime_sha256(task)? == request.runtime_sha256,
        "runtime task version changed"
    );
    let result = Path::new(
        task.result_artifact_path
            .as_deref()
            .context("runtime result missing")?,
    );
    let result = if result.is_absolute() {
        result.to_path_buf()
    } else {
        Path::new(&task.workdir).join(result)
    };
    let result_bytes = checked_artifact(&result, &request.result_sha256)?;
    let result_value: serde_json::Value = serde_json::from_slice(&result_bytes)?;
    ensure!(
        result_value["task_id"] == task.task_id,
        "result task identity mismatch"
    );
    for (key, expected) in [
        ("request_id", task.request_id.as_str()),
        ("ticket_id", request.background_ticket_id.as_str()),
    ] {
        if let Some(actual) = result_value.get(key) {
            ensure!(actual == expected, "result {key} mismatch");
        }
    }
    let assessment = request
        .assessment
        .as_ref()
        .map(|reference| -> Result<ResultAssessment> {
            let value: ResultAssessment =
                serde_json::from_slice(&checked_artifact(&reference.path, &reference.sha256)?)?;
            ensure!(
                value.schema == "offdesk_result_assessment.v1"
                    && value.task_id == task.task_id
                    && value.request_id == task.request_id
                    && value.ticket_id == request.background_ticket_id
                    && value.result_sha256 == request.result_sha256,
                "result assessment identity mismatch"
            );
            ensure!(
                matches!(
                    value.classification.as_str(),
                    "invalid_result" | "evidence_reviewed"
                ) && !value.reason.trim().is_empty()
                    && !value.evidence_refs.is_empty()
                    && value.evidence_refs.len() <= 128,
                "result assessment needs a bounded classification, reason and evidence"
            );
            for reference in &value.evidence_refs {
                check_reference(reference)?;
            }
            if let Some(successor) = &value.successor {
                ensure!(
                    value.classification == "invalid_result" && successor.task_id != task.task_id,
                    "only an invalid historical attempt can have a distinct successor"
                );
                let tasks = crate::offdesk::OffdeskTaskStore::new(root).load()?;
                let matches: Vec<_> = tasks
                    .iter()
                    .filter(|other| other.task_id == successor.task_id)
                    .collect();
                ensure!(matches.len() == 1, "successor missing or ambiguous");
                let next = matches[0];
                ensure!(
                    next.project_key == task.project_key
                        && next.status == OffdeskTaskStatus::Completed
                        && next.request_id == successor.request_id
                        && next.background_ticket_id.as_deref() == Some(&successor.ticket_id)
                        && runtime_sha256(next)? == successor.runtime_sha256,
                    "successor runtime identity changed"
                );
                let path = Path::new(
                    next.result_artifact_path
                        .as_deref()
                        .context("successor result missing")?,
                );
                let path = if path.is_absolute() {
                    path.to_owned()
                } else {
                    Path::new(&next.workdir).join(path)
                };
                let bytes = checked_artifact(&path, &successor.result_sha256)?;
                let observed = crate::offdesk::result_observation::observe(
                    Path::new(&next.workdir),
                    &path,
                    &next.task_id,
                    &next.request_id,
                    &successor.ticket_id,
                );
                let source: serde_json::Value = serde_json::from_slice(&bytes)?;
                let modern_evidence = observed.identity == "self_reported_exact"
                    && !observed.evidence_truncated
                    && !observed.evidence.is_empty()
                    && observed.evidence.iter().all(|item| {
                        item.verification == "current_bytes_hash_match_not_claim_verification"
                    });
                let mut legacy_evidence =
                    !successor.legacy_evidence.is_empty() && successor.legacy_evidence.len() <= 16;
                let mut fields = std::collections::HashSet::new();
                let mut decision_bound = false;
                for entry in &successor.legacy_evidence {
                    ensure!(
                        fields.insert(&entry.hash_field)
                            && entry.hash_field.ends_with("_sha256")
                            && entry.hash_field.len() <= 64
                            && entry
                                .hash_field
                                .bytes()
                                .all(|byte| byte.is_ascii_lowercase()
                                    || byte.is_ascii_digit()
                                    || byte == b'_'),
                        "invalid legacy result hash field"
                    );
                    ensure!(
                        entry.artifact.path.starts_with(&next.workdir),
                        "successor evidence outside its declared root"
                    );
                    checked_artifact(&entry.artifact.path, &entry.artifact.sha256)?;
                    legacy_evidence &= source
                        .get(&entry.hash_field)
                        .and_then(|value| value.as_str())
                        == Some(&entry.artifact.sha256);
                    decision_bound |= value.evidence_refs.contains(&format!(
                        "sha256:{}:{}",
                        entry.artifact.sha256,
                        entry.artifact.path.display()
                    ));
                }
                // Legacy envelopes remain legacy observations. Exact queue and
                // result versions plus explicitly selected hash fields bind the
                // relation; no missing IDs or process receipts are fabricated.
                legacy_evidence &= decision_bound
                    && source["task_id"] == next.task_id
                    && source
                        .get("request_id")
                        .is_none_or(|id| id == &next.request_id)
                    && source
                        .get("ticket_id")
                        .is_none_or(|id| id == &successor.ticket_id);
                ensure!(
                    (modern_evidence || legacy_evidence)
                        && observed.content_sha256.as_deref() == Some(&successor.result_sha256)
                        && matches!(
                            observed.reported_status.as_deref(),
                            Some("completed" | "pending_user_review")
                        )
                        && observed.reported_exit_code.is_none_or(|code| code == 0),
                    "successor evidence is incomplete, contradictory or changed"
                );
                ensure!(
                    digest(&bytes) == successor.result_sha256,
                    "successor changed"
                );
            }
            Ok(value)
        })
        .transpose()?;
    let blocked = !matches!(
        result_value["status"].as_str(),
        Some("completed" | "pending_user_review")
    ) || result_value["exit_code"]
        .as_i64()
        .is_some_and(|code| code != 0);
    ensure!(
        !blocked
            || assessment
                .as_ref()
                .is_none_or(|value| value.classification == "invalid_result"),
        "blocked or failed output cannot be classified as evidence reviewed"
    );
    let result_validity = if blocked {
        "invalid_result"
    } else {
        assessment
            .as_ref()
            .map(|value| value.classification.as_str())
            .unwrap_or("unverified_observation")
    }
    .to_string();
    ensure!(request.closeouts.len() <= 32, "too many closeout bindings");
    let closeout_root = root.join("offdesk_closeouts");
    let mut closeout_paths = std::collections::HashSet::new();
    for reference in &request.closeouts {
        ensure!(
            reference.path.starts_with(&closeout_root)
                && closeout_paths.insert(reference.path.clone()),
            "duplicate or out-of-profile closeout binding"
        );
        let value: serde_json::Value =
            serde_json::from_slice(&checked_artifact(&reference.path, &reference.sha256)?)?;
        let refs = value
            .get("tasks")
            .or_else(|| value.get("applies_to_tasks"))
            .and_then(|value| value.as_array())
            .context("closeout task references missing")?;
        ensure!(
            refs.iter().any(|item| item["task_id"] == task.task_id
                && item["request_id"] == task.request_id
                && item["project_key"] == task.project_key),
            "closeout belongs to another attempt"
        );
        ensure!(
            value["closeout_id"]
                .as_str()
                .is_some_and(|id| !id.is_empty())
                && value["profile"].as_str() == root.file_name().and_then(|name| name.to_str()),
            "closeout identity mismatch"
        );
    }
    if closeout_root.exists() {
        let entries: Vec<_> = std::fs::read_dir(&closeout_root)?.collect::<std::io::Result<_>>()?;
        ensure!(entries.len() <= 1024, "closeout discovery exceeds bounds");
        for entry in entries {
            ensure!(
                !entry.file_type()?.is_symlink(),
                "unsafe closeout directory"
            );
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let path = entry.path().join("closeout_plan.json");
            if !path.exists() {
                continue;
            }
            let value: serde_json::Value = serde_json::from_slice(&read_artifact(&path)?)?;
            if value["tasks"].as_array().is_some_and(|refs| {
                refs.iter().any(|item| {
                    item["task_id"] == task.task_id && item["request_id"] == task.request_id
                })
            }) {
                ensure!(
                    closeout_paths.contains(&path),
                    "an applicable closeout needs an explicit hash binding"
                );
            }
        }
    }
    let narrative_reconciliation =
        narrative_reconciliation::observe(request, profile_identity, &task.project_key)?;
    let source = if let Some(reconciled) = &narrative_reconciliation {
        checked_artifact(
            &reconciled.approved_document.path,
            &request.owning_plan_sha256,
        )?
    } else {
        checked_artifact(&request.owning_plan, &request.owning_plan_sha256)?
    };
    let inspection = ondesk_plan::inspect(std::str::from_utf8(&source)?, &task.project_key)?;
    let owning_task = inspection
        .plan
        .tasks
        .iter()
        .find(|item| item.task_id == request.owning_task_id)
        .context("owning task missing")?;
    ensure!(
        narrative_reconciliation.is_none()
            || matches!(
                owning_task.review_state,
                ondesk_plan::ReviewState::PartiallyAccepted
                    | ondesk_plan::ReviewState::Rework
                    | ondesk_plan::ReviewState::Deferred
            ),
        "narrative reconciliation is limited to an existing nonaccepted decision"
    );
    let journal = journal_path(&request.owning_plan)?;
    let journal_bytes = checked_artifact(&journal, &request.owning_journal_sha256)?;
    let journal_value: serde_json::Value = serde_json::from_slice(&journal_bytes)?;
    ensure!(
        journal_value["schema"] == "ondesk_plan_change_journal.v1"
            && journal_value["source_path"] == request.owning_plan.to_string_lossy().as_ref(),
        "owning journal identity mismatch"
    );
    let entries = journal_value["entries"]
        .as_array()
        .context("journal entries missing")?;
    ensure!(
        !entries.is_empty() && entries.len() <= 512,
        "journal entry count outside bounds"
    );
    ensure!(
        request.historical_boundary.is_none() || request.historical_prefix.is_none(),
        "select only one historical acknowledgment"
    );
    let historical_boundary = historical_boundary::observe(request, profile_identity, entries)?;
    let historical_prefix = historical_prefix::observe(request, profile_identity, entries)?;
    let mut prior_candidate: Option<&str> = None;
    let mut selected = None;
    let mut ids = std::collections::HashSet::new();
    for (index, entry) in entries.iter().enumerate() {
        ensure!(
            entry["committed"] == true,
            "owning journal contains an unfinished write"
        );
        let write: ondesk_plan_store::PlanChangeRequest =
            serde_json::from_value(entry["request"].clone())?;
        ondesk_plan_store::validate_request(&write)?;
        ensure!(
            write.project_key == task.project_key && write.source_path == request.owning_plan,
            "journal write scope mismatch"
        );
        ensure!(
            ids.insert(write.request_id.clone()),
            "duplicate journal request identity"
        );
        if let Some(prior) = prior_candidate {
            ensure!(
                prior == write.baseline_sha256
                    || historical_boundary
                        .as_ref()
                        .is_some_and(|boundary| boundary.suffix_start_index == index)
                    || historical_prefix.as_ref().is_some_and(|prefix| prefix
                        .gaps
                        .iter()
                        .any(|gap| gap.entry_index == index)),
                "journal chain is discontinuous"
            );
        }
        prior_candidate = entry["request"]["candidate_sha256"].as_str();
        if let Some(review) = &write.review_record {
            let mut task_ids = std::collections::HashSet::new();
            ensure!(
                review
                    .tasks
                    .iter()
                    .all(|item| task_ids.insert(&item.task_id)),
                "duplicate task decision"
            );
            if let Some(decision) = review
                .tasks
                .iter()
                .find(|item| item.task_id == request.owning_task_id)
            {
                // The latest decision for this task wins; an earlier decision
                // cannot be selected after a subsequent review.
                selected = Some((
                    write.request_id.clone(),
                    write.candidate_sha256.clone(),
                    review.clone(),
                    decision.clone(),
                ));
            }
        }
    }
    ensure!(
        prior_candidate == Some(request.owning_plan_sha256.as_str()),
        "owning plan is not the committed journal version"
    );
    let (decision_id, decision_candidate, review, decision) =
        selected.context("committed task decision missing")?;
    ensure!(
        decision_candidate == request.owning_plan_sha256,
        "owning plan advanced after this decision; explicit reconciliation required"
    );
    ensure!(
        decision_id == request.decision_request_id,
        "selected decision was superseded"
    );
    ensure!(
        decision.disposition == owning_task.review_state
            && decision
                .evidence_refs
                .iter()
                .all(|reference| owning_task.evidence_refs.contains(reference)),
        "owning task disagrees with its decision"
    );
    ensure!(
        !matches!(
            decision.disposition,
            ondesk_plan::ReviewState::Unreviewed | ondesk_plan::ReviewState::PendingReview
        ),
        "operator disposition not recorded"
    );
    ensure!(decision.disposition != ondesk_plan::ReviewState::Accepted || result_validity == "evidence_reviewed",
        "accepted disposition requires an explicit evidence assessment; process completion is insufficient");
    ensure!(
        !review.decided_by.trim().is_empty() && !review.decision_ref.trim().is_empty(),
        "decision attribution missing"
    );
    ensure!(
        !decision.evidence_refs.is_empty() && decision.evidence_refs.len() <= 128,
        "decision evidence missing or excessive"
    );
    let mut evidence = Vec::new();
    for reference in &decision.evidence_refs {
        let reference = reference
            .strip_prefix("sha256:")
            .context("decision needs explicit hash-bound file evidence")?;
        let (hash, path) = reference
            .split_once(':')
            .context("invalid decision evidence reference")?;
        checked_artifact(Path::new(path), hash)?;
        evidence.push((PathBuf::from(path), hash));
    }
    if let Some(assessment) = &assessment {
        ensure!(
            assessment
                .evidence_refs
                .iter()
                .all(|reference| decision.evidence_refs.contains(reference)),
            "assessment evidence is outside the committed decision"
        );
    }
    // Recheck external versions before returning a preview or committing a link.
    checked_artifact(&result, &request.result_sha256)?;
    if narrative_reconciliation.is_none() {
        checked_artifact(&request.owning_plan, &request.owning_plan_sha256)?;
    }
    checked_artifact(&journal, &request.owning_journal_sha256)?;
    for (path, hash) in evidence {
        checked_artifact(&path, hash)?;
    }
    if let Some(reference) = &request.assessment {
        checked_artifact(&reference.path, &reference.sha256)?;
    }
    for reference in &request.closeouts {
        checked_artifact(&reference.path, &reference.sha256)?;
    }
    ensure!(
        historical_boundary::observe(request, profile_identity, entries)? == historical_boundary,
        "historical boundary changed during review"
    );
    ensure!(
        historical_prefix::observe(request, profile_identity, entries)? == historical_prefix,
        "historical prefix changed during review"
    );
    ensure!(
        narrative_reconciliation::observe(request, profile_identity, &task.project_key)?
            == narrative_reconciliation,
        "narrative reconciliation changed during review"
    );
    ondesk_plan_store::active_root(root, &profile)?;
    Ok(ReviewLinkPreview {
        schema: "offdesk_review_link_preview.v1".into(),
        request: request.clone(),
        profile_identity,
        decision,
        decision_ref: review.decision_ref,
        decided_by: review.decided_by,
        result_validity,
        assessment,
        historical_boundary,
        historical_prefix,
        narrative_reconciliation,
    })
}

fn check_reference(reference: &str) -> Result<()> {
    let (hash, path) = reference
        .strip_prefix("sha256:")
        .context("assessment needs hash-bound file evidence")?
        .split_once(':')
        .context("invalid assessment evidence")?;
    checked_artifact(Path::new(path), hash)?;
    Ok(())
}

impl ReviewLinkPreview {
    pub fn sha256(&self) -> Result<String> {
        value_digest(serde_json::to_value(self)?)
    }
}

pub fn validate_record(root: &Path, task: &OffdeskTask, record: &RecordedReviewLink) -> Result<()> {
    let current = preview(root, task, &record.preview.request)?;
    ensure!(
        current == record.preview && current.sha256()? == record.preview_sha256,
        "stored review binding changed"
    );
    ensure!(
        !record.recorded_by.trim().is_empty(),
        "review link recorder missing"
    );
    Ok(())
}

/// Administrative closure contributes acceptance only with the independent,
/// current accepted result decision and an exact binding of this receipt.
pub fn reviewed_accepted_receipt(
    root: &Path,
    tasks: &[OffdeskTask],
    review: &serde_json::Value,
) -> bool {
    let receipt = &review["closeout_receipt"];
    if review["verdict"] != "approved"
        || receipt["verdict"] != "approved"
        || receipt["acceptance_status"] != "approved_with_followups"
        || receipt["evidence_status"] != "review_ready"
        || receipt["stale_task_count"].as_u64() != Some(0)
        || !matches!(
            receipt["verification_status"].as_str(),
            Some("pending" | "recorded")
        )
        || !matches!(
            receipt["retention_review"].as_str(),
            Some("not_required" | "resolved_preserve_in_place")
        )
        || !matches!(
            receipt["wiki_promotion_state"].as_str(),
            Some("no_candidate" | "not_requested" | "not_required")
        )
        || [
            "open_decisions",
            "missing_evidence",
            "required_first_reads",
            "unsafe_operations",
        ]
        .iter()
        .any(|key| !receipt[*key].as_array().is_some_and(Vec::is_empty))
        || review["profile"].as_str() != root.file_name().and_then(|name| name.to_str())
    {
        return false;
    }
    if receipt["retention_review"] == "resolved_preserve_in_place"
        && !receipt["resolved_open_decisions"]
            .as_array()
            .is_some_and(|items| {
                items.iter().any(|item| {
                    item["kind"] == "archive_review"
                        && matches!(
                            item["decision"].as_str(),
                            Some("preserve_in_place" | "preserve_in_place_administrative")
                        )
                })
            })
    {
        return false;
    }
    let Some(refs) = review["applies_to_tasks"]
        .as_array()
        .filter(|refs| !refs.is_empty() && refs.len() <= 1024)
    else {
        return false;
    };
    let reviewed_at = review["reviewed_at"]
        .as_str()
        .and_then(|v| DateTime::parse_from_rfc3339(v).ok());
    let generated_at = receipt["generated_at"]
        .as_str()
        .and_then(|v| DateTime::parse_from_rfc3339(v).ok());
    let mut seen = std::collections::HashSet::new();
    refs.iter().all(|reference| {
        let id = reference["task_id"].as_str().unwrap_or("");
        if id.is_empty() || !seen.insert(id) {
            return false;
        }
        let matching: Vec<_> = tasks
            .iter()
            .filter(|task| {
                task.task_id == id
                    && reference["request_id"] == task.request_id
                    && reference["project_key"] == task.project_key
            })
            .collect();
        if matching.len() != 1 {
            return false;
        }
        let task = matching[0];
        if reviewed_at.is_none_or(|v| v <= task.updated_at)
            || generated_at.is_none_or(|v| v <= task.updated_at)
        {
            return false;
        }
        task.review_links.last().is_some_and(|link| {
            link.preview.decision.disposition == ondesk_plan::ReviewState::Accepted
                && link.preview.result_validity == "evidence_reviewed"
                && link
                    .preview
                    .assessment
                    .as_ref()
                    .is_some_and(|a| a.classification == "evidence_reviewed")
                && link.preview.decision.remaining_work.is_empty()
                && link.preview.decision.unanswered_questions.is_empty()
                && validate_record(root, task, link).is_ok()
                && link.preview.request.closeouts.iter().any(|reference| {
                    checked_artifact(&reference.path, &reference.sha256)
                        .ok()
                        .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
                        .is_some_and(|value| &value == review)
                })
        })
    })
}

/// Projection only. The independently recorded administrative receipt stays
/// unchanged on disk, including its original acceptance/verification fields.
pub fn effective_closeout_receipt(
    root: &Path,
    tasks: &[OffdeskTask],
    review: &serde_json::Value,
) -> serde_json::Value {
    let mut receipt = review["closeout_receipt"].clone();
    if reviewed_accepted_receipt(root, tasks, review) {
        receipt["recorded_acceptance_status"] = receipt["acceptance_status"].clone();
        receipt["acceptance_status"] = serde_json::json!("accepted");
        receipt["verification_status"] = serde_json::json!("recorded");
        receipt["acceptance_basis"] =
            serde_json::json!("accepted_owning_decision_and_exact_administrative_receipt");
        receipt["accepted_scope"] = serde_json::json!(review["applies_to_tasks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|task| {
                format!(
                    "{}:{} request={}",
                    task["project_key"].as_str().unwrap_or(""),
                    task["task_id"].as_str().unwrap_or(""),
                    task["request_id"].as_str().unwrap_or("")
                )
            })
            .collect::<Vec<_>>());
        receipt["next_safe_action"] = serde_json::json!("Result acceptance and administrative closeout are recorded; follow the existing owning plan.");
    }
    receipt
}

/// Separate recorded rework from an accepted-truth recovery request.
pub fn reviewed_nonaccepted_receipt(
    root: &Path,
    tasks: &[OffdeskTask],
    review: &serde_json::Value,
) -> bool {
    let receipt = &review["closeout_receipt"];
    let preservation_recorded = receipt["retention_review"] == "resolved_preserve_in_place"
        && receipt["resolved_open_decisions"]
            .as_array()
            .is_some_and(|decisions| {
                decisions.iter().any(|decision| {
                    decision["kind"] == "archive_review"
                        && matches!(
                            decision["decision"].as_str(),
                            Some("preserve_in_place" | "preserve_in_place_administrative")
                        )
                })
            });
    let operations_pending = !receipt["unsafe_operations"]
        .as_array()
        .is_some_and(|operations| {
            operations.iter().all(|operation| {
                preservation_recorded
                    && operation
                        .as_str()
                        .is_some_and(|operation| operation.starts_with("archive_candidate:"))
            })
        });
    if receipt["acceptance_status"] != "approved_with_followups"
        || receipt["stale_task_count"].as_u64() != Some(0)
        || receipt["retention_review"] == "required"
        || operations_pending
        || !matches!(
            receipt["wiki_promotion_state"].as_str(),
            Some("no_candidate" | "not_requested" | "not_required")
        )
        || !receipt["open_decisions"]
            .as_array()
            .is_some_and(Vec::is_empty)
        || !receipt["required_first_reads"]
            .as_array()
            .is_some_and(Vec::is_empty)
    {
        return false;
    }
    let Some(refs) = review["applies_to_tasks"]
        .as_array()
        .filter(|refs| !refs.is_empty())
    else {
        return false;
    };
    let mut seen = std::collections::HashSet::new();
    refs.iter().all(|reference| {
        let id = reference["task_id"].as_str().unwrap_or("");
        if !seen.insert(id) {
            return false;
        }
        let matches: Vec<_> = tasks
            .iter()
            .filter(|task| {
                task.task_id == id
                    && reference["request_id"] == task.request_id
                    && reference["project_key"] == task.project_key
            })
            .collect();
        if matches.len() != 1 {
            return false;
        }
        let task = matches[0];
        task.review_links.last().is_some_and(|link| {
            matches!(
                link.preview.decision.disposition,
                ondesk_plan::ReviewState::Rework
                    | ondesk_plan::ReviewState::PartiallyAccepted
                    | ondesk_plan::ReviewState::Deferred
            ) && !link.preview.decision.remaining_work.is_empty()
                && validate_record(root, task, link).is_ok()
        })
    })
}

#[cfg(test)]
#[path = "review_link_tests.rs"]
mod tests;
