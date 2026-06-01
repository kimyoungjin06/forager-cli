//! Shared review surface contract for Ondesk and future rich UIs.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use clap::Args;
use serde::Serialize;
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

use super::artifact_index;
use super::status::current_status_json_value;
use crate::offdesk::{
    load_offdesk_status_summary, operator_safe_text, AdaptiveWikiStore, BackgroundProbe,
    BackgroundRunStore, DecisionLedger, DecisionRecord, DecisionStatus, OffdeskStatusSummary,
};
use crate::session::get_profile_dir;

const REVIEW_SURFACE_SCHEMA: &str = "review_surface.v1";
const MAX_RECENT_DECISIONS: usize = 5;

#[derive(Args)]
pub struct ReviewSurfaceArgs {
    /// Stable project key to focus the review packet. Defaults to all projects.
    #[arg(long)]
    pub project_key: Option<String>,

    /// Emit compact JSON. Without this flag, a human summary is printed.
    #[arg(long)]
    pub json: bool,
}

#[derive(Debug, Serialize)]
struct ReviewSurface {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    status: ReviewSurfaceStatus,
    next_safe_actions: Vec<Value>,
    accepted_truth: ReviewSurfaceAcceptedTruth,
    closeout: ReviewSurfaceCloseout,
    runtime: ReviewSurfaceRuntime,
    decisions: ReviewSurfaceDecisions,
    adaptive_wiki: ReviewSurfaceAdaptiveWiki,
    artifacts: ReviewSurfaceArtifacts,
    redaction: ReviewSurfaceRedaction,
    sources: ReviewSurfaceSources,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceStatus {
    label: String,
    summary: String,
    severity: String,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceAcceptedTruth {
    status: String,
    source: String,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_acceptance_status: Option<String>,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceCloseout {
    #[serde(skip_serializing_if = "Option::is_none")]
    latest_closeout_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    latest_receipt_id: Option<String>,
    execution_status: String,
    review_status: String,
    unresolved_risks: Vec<String>,
    summary: Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    generated_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    reviewed_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    verification_status: Option<String>,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceRuntime {
    active: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    last_heartbeat_at: Option<DateTime<Utc>>,
    progress_summary: String,
    sessions: Value,
    offdesk: Value,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceDecisions {
    open_count: usize,
    recent: Vec<ReviewSurfaceDecision>,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceDecision {
    decision_id: String,
    project_key: String,
    task_id: String,
    status: String,
    summary: String,
    decision_needed: String,
    updated_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceAdaptiveWiki {
    candidate_count: usize,
    entry_count: usize,
    review_due_count: usize,
    promotion_required: bool,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceArtifacts {
    index: Value,
    retention_review: Value,
    summary: Vec<ReviewSurfaceArtifactSummary>,
    refs: Vec<ReviewSurfaceArtifactRef>,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceArtifactSummary {
    label: String,
    why_it_matters: String,
    retention_class: String,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceArtifactRef {
    id: String,
    label: String,
    path: String,
    source: String,
    present: bool,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceRedaction {
    operator_safe: bool,
    path_policy: &'static str,
}

#[derive(Debug, Serialize)]
struct ReviewSurfaceSources {
    status_json: &'static str,
    offdesk_status_summary: &'static str,
    closeout_receipt: &'static str,
    artifact_index: &'static str,
    artifact_retention_review: &'static str,
}

struct LatestCloseout {
    closeout_id: String,
    generated_at: DateTime<Utc>,
    artifact_dir: PathBuf,
    plan_path: PathBuf,
    return_package_path: Option<PathBuf>,
    review: Option<LatestCloseoutReview>,
}

struct LatestCloseoutReview {
    reviewed_at: DateTime<Utc>,
    verdict: String,
    record_path: PathBuf,
    receipt_path: Option<PathBuf>,
    receipt: Option<Value>,
}

pub async fn run(profile: &str, args: ReviewSurfaceArgs) -> Result<()> {
    let surface = build_review_surface(profile, &args)?;
    if args.json {
        println!("{}", serde_json::to_string(&surface)?);
    } else {
        let value = serde_json::to_value(&surface)?;
        print!("{}", human_summary_from_value(&value));
    }
    Ok(())
}

pub(crate) fn build_review_surface_value(
    profile: &str,
    project_key: Option<&str>,
) -> Result<Value> {
    let args = ReviewSurfaceArgs {
        project_key: project_key.map(ToOwned::to_owned),
        json: true,
    };
    serde_json::to_value(build_review_surface(profile, &args)?).context("serialize review surface")
}

pub(crate) fn human_summary_from_value(surface: &Value) -> String {
    let mut output = String::new();
    output.push_str("Morning Review Surface\n");
    push_summary_line(&mut output, "profile", text_at(surface, "/profile"));
    push_summary_line(&mut output, "project", text_at(surface, "/project_key"));
    let status_label = text_at(surface, "/status/label").unwrap_or("unknown");
    let status_severity = text_at(surface, "/status/severity").unwrap_or("unknown");
    output.push_str(&format!("  status: {status_label} ({status_severity})\n"));
    if let Some(summary) = text_at(surface, "/status/summary") {
        output.push_str(&format!("  summary: {summary}\n"));
    }
    let truth_status = text_at(surface, "/accepted_truth/status").unwrap_or("unknown");
    let truth_source = text_at(surface, "/accepted_truth/source").unwrap_or("unknown");
    output.push_str(&format!(
        "  accepted truth: {truth_status} via {truth_source}\n"
    ));
    if let Some(reason) = text_at(surface, "/accepted_truth/reason") {
        output.push_str(&format!("  reason: {reason}\n"));
    }
    let execution = text_at(surface, "/closeout/execution_status").unwrap_or("unknown");
    let review = text_at(surface, "/closeout/review_status").unwrap_or("unknown");
    output.push_str(&format!(
        "  closeout: execution={execution}, review={review}\n"
    ));
    if let Some(runtime) = text_at(surface, "/runtime/progress_summary") {
        output.push_str(&format!("  runtime: {runtime}\n"));
    }
    output.push_str(&format!(
        "  open decisions: {}\n",
        number_at(surface, "/decisions/open_count").unwrap_or_default()
    ));
    output.push_str(&format!(
        "  adaptive wiki: {} candidate(s), {} review-due entry(s)\n",
        number_at(surface, "/adaptive_wiki/candidate_count").unwrap_or_default(),
        number_at(surface, "/adaptive_wiki/review_due_count").unwrap_or_default()
    ));
    if surface.pointer("/artifacts/index/schema").is_some() {
        output.push_str(&format!(
            "  artifact index: {} total, {} review-required, {} disposal/archive candidate(s)\n",
            number_at(surface, "/artifacts/index/summary/total_entries").unwrap_or_default(),
            number_at(surface, "/artifacts/index/summary/review_required_entries")
                .unwrap_or_default(),
            number_at(
                surface,
                "/artifacts/index/summary/disposal_candidate_entries"
            )
            .unwrap_or_default()
        ));
    }
    if surface
        .pointer("/artifacts/retention_review/schema")
        .is_some()
    {
        output.push_str(&format!(
            "  retention review: {} action-required, {} missing, {} unreferenced human-facing\n",
            number_at(
                surface,
                "/artifacts/retention_review/summary/action_required_entries"
            )
            .unwrap_or_default(),
            number_at(
                surface,
                "/artifacts/retention_review/summary/missing_entries"
            )
            .unwrap_or_default(),
            number_at(
                surface,
                "/artifacts/retention_review/summary/unreferenced_human_facing_entries"
            )
            .unwrap_or_default()
        ));
    }

    if let Some(actions) = surface
        .get("next_safe_actions")
        .and_then(Value::as_array)
        .filter(|actions| !actions.is_empty())
    {
        output.push_str("  next safe actions:\n");
        for action in actions.iter().take(3) {
            output.push_str(&format!("    - {}\n", format_next_safe_action(action)));
        }
    }

    if let Some(risks) = surface
        .pointer("/closeout/unresolved_risks")
        .and_then(Value::as_array)
        .filter(|risks| !risks.is_empty())
    {
        output.push_str("  unresolved risks:\n");
        for risk in risks.iter().take(4).filter_map(Value::as_str) {
            output.push_str(&format!("    - {}\n", operator_safe_text(risk)));
        }
    }

    if let Some(summaries) = surface
        .pointer("/artifacts/summary")
        .and_then(Value::as_array)
        .filter(|summaries| !summaries.is_empty())
    {
        output.push_str("  artifact summaries:\n");
        for summary in summaries.iter().take(5) {
            let label = text_at(summary, "/label").unwrap_or("Artifact");
            let why = text_at(summary, "/why_it_matters").unwrap_or("Review before use.");
            let retention = text_at(summary, "/retention_class").unwrap_or("review");
            output.push_str(&format!("    - {label}: {why} [{retention}]\n"));
        }
    }
    output.push_str("  refs: use --json for audit paths and source ids\n");
    output
}

fn build_review_surface(profile: &str, args: &ReviewSurfaceArgs) -> Result<ReviewSurface> {
    let generated_at = Utc::now();
    let profile_dir = get_profile_dir(profile)?;
    let status_json = current_status_json_value(profile)?;
    let offdesk_summary = load_offdesk_status_summary(&profile_dir, generated_at)
        .unwrap_or_else(|_| OffdeskStatusSummary::default());
    let next_safe_actions = status_json
        .get("offdesk_next_safe_actions")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    let project_key = args
        .project_key
        .as_deref()
        .map(operator_safe_text)
        .unwrap_or_else(|| "all".to_string());
    let latest_closeout = latest_closeout(&profile_dir, args.project_key.as_deref())?;
    let artifact_index =
        artifact_index::build_profile_artifact_index_value(profile, args.project_key.as_deref())?;
    let retention_review =
        artifact_index::build_profile_retention_review_value(profile, args.project_key.as_deref())?;
    let artifact_index = artifact_index::review_surface_projection(&artifact_index);
    let retention_review = artifact_index::retention_review_projection(&retention_review);
    let artifacts = build_artifacts(latest_closeout.as_ref(), artifact_index, retention_review);

    Ok(ReviewSurface {
        schema: REVIEW_SURFACE_SCHEMA,
        generated_at,
        profile: operator_safe_text(profile),
        project_key,
        status: build_surface_status(&status_json, &next_safe_actions),
        next_safe_actions,
        accepted_truth: build_accepted_truth(&offdesk_summary, latest_closeout.as_ref()),
        closeout: build_closeout(&offdesk_summary, latest_closeout.as_ref()),
        runtime: build_runtime(&profile_dir, &status_json, args.project_key.as_deref()),
        decisions: build_decisions(&profile_dir, args.project_key.as_deref()),
        adaptive_wiki: build_adaptive_wiki(&profile_dir, args.project_key.as_deref(), generated_at),
        artifacts,
        redaction: ReviewSurfaceRedaction {
            operator_safe: true,
            path_policy: "summary_first",
        },
        sources: ReviewSurfaceSources {
            status_json: "forager status --json",
            offdesk_status_summary: "load_offdesk_status_summary",
            closeout_receipt: "closeout_receipt.v1",
            artifact_index: "artifact_index.v1",
            artifact_retention_review: "artifact_retention_review.v1",
        },
    })
}

fn build_surface_status(status_json: &Value, next_safe_actions: &[Value]) -> ReviewSurfaceStatus {
    if let Some(action) = next_safe_actions.first() {
        let summary = action
            .get("detail")
            .and_then(Value::as_str)
            .map(operator_safe_text)
            .unwrap_or_else(|| "Operator review is required before continuing.".to_string());
        let requires_review = action
            .get("requires_operator_review")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        return ReviewSurfaceStatus {
            label: if requires_review {
                "needs_review".to_string()
            } else {
                "action_available".to_string()
            },
            summary,
            severity: if requires_review {
                "attention".to_string()
            } else {
                "info".to_string()
            },
        };
    }

    let active = value_usize(status_json, "running")
        + value_usize(status_json, "active_offdesk_tasks")
        + value_usize(status_json, "queued_offdesk_tasks");
    if active > 0 {
        ReviewSurfaceStatus {
            label: "active".to_string(),
            summary:
                "Forager has active or queued work, but no blocking operator review is visible."
                    .to_string(),
            severity: "info".to_string(),
        }
    } else {
        ReviewSurfaceStatus {
            label: "clear".to_string(),
            summary: "No blocking Forager action is currently visible.".to_string(),
            severity: "ok".to_string(),
        }
    }
}

fn build_accepted_truth(
    summary: &OffdeskStatusSummary,
    latest: Option<&LatestCloseout>,
) -> ReviewSurfaceAcceptedTruth {
    let receipt = latest.and_then(|closeout| closeout.review.as_ref()?.receipt.as_ref());
    let receipt_status = receipt
        .and_then(|receipt| receipt.get("acceptance_status"))
        .and_then(Value::as_str)
        .map(operator_safe_text);
    let next_safe_action = receipt
        .and_then(|receipt| receipt.get("next_safe_action"))
        .and_then(Value::as_str)
        .map(operator_safe_text);

    if let Some(status) = receipt_status.clone() {
        return ReviewSurfaceAcceptedTruth {
            status: if status == "accepted" {
                "accepted".to_string()
            } else {
                "pending".to_string()
            },
            source: "closeout_receipt.v1".to_string(),
            reason: next_safe_action.unwrap_or_else(|| closeout_acceptance_reason(&status)),
            receipt_acceptance_status: Some(status),
        };
    }

    if summary.closeout_required > 0 {
        ReviewSurfaceAcceptedTruth {
            status: "pending".to_string(),
            source: "offdesk_status_summary".to_string(),
            reason: "Completed Offdesk output still needs closeout or closeout receipt review."
                .to_string(),
            receipt_acceptance_status: None,
        }
    } else if summary.closeout_state.accepted > 0 {
        ReviewSurfaceAcceptedTruth {
            status: "accepted".to_string(),
            source: "offdesk_status_summary".to_string(),
            reason: "At least one closeout receipt is recorded as accepted truth.".to_string(),
            receipt_acceptance_status: Some("accepted".to_string()),
        }
    } else {
        ReviewSurfaceAcceptedTruth {
            status: "not_applicable".to_string(),
            source: "offdesk_status_summary".to_string(),
            reason: "No completed Offdesk closeout currently requires accepted-truth review."
                .to_string(),
            receipt_acceptance_status: None,
        }
    }
}

fn build_closeout(
    summary: &OffdeskStatusSummary,
    latest: Option<&LatestCloseout>,
) -> ReviewSurfaceCloseout {
    let receipt = latest.and_then(|closeout| closeout.review.as_ref()?.receipt.as_ref());
    let receipt_status = receipt
        .and_then(|receipt| receipt.get("acceptance_status"))
        .and_then(Value::as_str)
        .map(operator_safe_text);
    let verification_status = receipt
        .and_then(|receipt| receipt.get("verification_status"))
        .and_then(Value::as_str)
        .map(operator_safe_text);
    let latest_receipt_id = receipt
        .and_then(|receipt| receipt.get("receipt_id"))
        .and_then(Value::as_str)
        .map(operator_safe_text);
    let unresolved_risks = receipt
        .map(closeout_unresolved_risks)
        .unwrap_or_else(|| closeout_summary_risks(summary));
    let review_status = latest
        .and_then(|closeout| closeout.review.as_ref())
        .map(|review| {
            receipt_status
                .clone()
                .unwrap_or_else(|| operator_safe_text(&review.verdict))
        })
        .unwrap_or_else(|| {
            if summary.closeout_required > 0 {
                "pending".to_string()
            } else {
                "none".to_string()
            }
        });
    let execution_status = if receipt_status.as_deref() == Some("accepted") {
        "accepted".to_string()
    } else if summary.closeout_required > 0 || latest.is_some() {
        "completed_needs_review".to_string()
    } else {
        "not_applicable".to_string()
    };

    ReviewSurfaceCloseout {
        latest_closeout_id: latest.map(|closeout| closeout.closeout_id.clone()),
        latest_receipt_id,
        execution_status,
        review_status,
        unresolved_risks,
        summary: serde_json::to_value(&summary.closeout_state).unwrap_or(Value::Null),
        generated_at: latest.map(|closeout| closeout.generated_at.to_rfc3339()),
        reviewed_at: latest
            .and_then(|closeout| closeout.review.as_ref())
            .map(|review| review.reviewed_at.to_rfc3339()),
        receipt_status,
        verification_status,
    }
}

fn build_runtime(
    profile_dir: &Path,
    status_json: &Value,
    project_key: Option<&str>,
) -> ReviewSurfaceRuntime {
    let backgrounds = BackgroundRunStore::new(profile_dir)
        .load()
        .unwrap_or_default();
    let filtered_backgrounds: Vec<&BackgroundProbe> = backgrounds
        .iter()
        .filter(|probe| option_matches(project_key, probe.project_key.as_deref()))
        .collect();
    let last_heartbeat_at = filtered_backgrounds
        .iter()
        .filter_map(|probe| probe.worker_heartbeat_at.or(probe.last_observed_at))
        .max();
    let active = value_usize(status_json, "running")
        + value_usize(status_json, "active_offdesk_tasks")
        + value_usize(status_json, "queued_offdesk_tasks")
        > 0;
    let queued = value_usize(status_json, "queued_offdesk_tasks");
    let active_offdesk = value_usize(status_json, "active_offdesk_tasks");
    let failed = value_usize(status_json, "failed_offdesk_tasks");
    let closeout_required = value_usize(status_json, "closeout_required_offdesk_tasks");
    let progress_summary = format!(
        "{} queued, {} active, {} failed, {} closeout-required offdesk task(s).",
        queued, active_offdesk, failed, closeout_required
    );

    ReviewSurfaceRuntime {
        active,
        last_heartbeat_at,
        progress_summary,
        sessions: serde_json::json!({
            "total": value_usize(status_json, "total"),
            "waiting": value_usize(status_json, "waiting"),
            "running": value_usize(status_json, "running"),
            "idle": value_usize(status_json, "idle"),
            "stopped": value_usize(status_json, "stopped"),
            "error": value_usize(status_json, "error")
        }),
        offdesk: serde_json::json!({
            "queued": queued,
            "active": active_offdesk,
            "pending_approval": value_usize(status_json, "offdesk_tasks_pending_approval"),
            "failed": failed,
            "resume_pending": value_usize(status_json, "resume_pending_offdesk_tasks"),
            "cancelled": value_usize(status_json, "cancelled_offdesk_tasks"),
            "stale_background": value_usize(status_json, "stale_background_runs"),
            "failed_background": value_usize(status_json, "failed_background_runs"),
            "closeout_required": closeout_required,
            "background_records": filtered_backgrounds.len()
        }),
    }
}

fn build_decisions(profile_dir: &Path, project_key: Option<&str>) -> ReviewSurfaceDecisions {
    let mut decisions = DecisionLedger::new(profile_dir)
        .load()
        .unwrap_or_default()
        .into_iter()
        .filter(|record| option_matches(project_key, Some(record.project_key.as_str())))
        .collect::<Vec<_>>();
    decisions.sort_by_key(|record| record.updated_at);
    let open_count = decisions
        .iter()
        .filter(|record| decision_is_open(record.status))
        .count();
    let recent = decisions
        .iter()
        .rev()
        .take(MAX_RECENT_DECISIONS)
        .map(review_decision)
        .collect();
    ReviewSurfaceDecisions { open_count, recent }
}

fn review_decision(record: &DecisionRecord) -> ReviewSurfaceDecision {
    ReviewSurfaceDecision {
        decision_id: operator_safe_text(&record.decision_id),
        project_key: operator_safe_text(&record.project_key),
        task_id: operator_safe_text(&record.task_id),
        status: record.status.as_str().to_string(),
        summary: operator_safe_text(&record.decision_request.summary),
        decision_needed: operator_safe_text(&record.decision_request.decision_needed),
        updated_at: record.updated_at,
    }
}

fn decision_is_open(status: DecisionStatus) -> bool {
    matches!(
        status,
        DecisionStatus::Draft
            | DecisionStatus::CouncilReview
            | DecisionStatus::UserPending
            | DecisionStatus::Deferred
            | DecisionStatus::HandoffReady
    )
}

fn build_adaptive_wiki(
    profile_dir: &Path,
    project_key: Option<&str>,
    now: DateTime<Utc>,
) -> ReviewSurfaceAdaptiveWiki {
    let store = AdaptiveWikiStore::new(profile_dir.to_path_buf());
    let candidates = store.load_candidates().unwrap_or_default();
    let entries = store.load_entries().unwrap_or_default();
    let candidate_count = candidates
        .candidates
        .iter()
        .filter(|candidate| option_matches(project_key, Some(candidate.scope_ref.as_str())))
        .count();
    let review_due_count = entries
        .entries
        .iter()
        .filter(|entry| {
            entry
                .review_after
                .is_some_and(|review_after| review_after <= now)
        })
        .count();
    ReviewSurfaceAdaptiveWiki {
        candidate_count,
        entry_count: entries.entries.len(),
        review_due_count,
        promotion_required: candidate_count > 0,
    }
}

fn build_artifacts(
    latest: Option<&LatestCloseout>,
    index: Value,
    retention_review: Value,
) -> ReviewSurfaceArtifacts {
    let mut summary = Vec::new();
    let mut refs = Vec::new();
    if let Some(closeout) = latest {
        summary.push(ReviewSurfaceArtifactSummary {
            label: "Closeout plan".to_string(),
            why_it_matters: "Explains what completed Offdesk work produced and what must be reviewed before acceptance.".to_string(),
            retention_class: "review".to_string(),
        });
        push_artifact_ref(
            &mut refs,
            "closeout_plan",
            "Closeout plan",
            &closeout.plan_path,
            "closeout_plan.json",
        );
        push_artifact_ref(
            &mut refs,
            "closeout_artifact_dir",
            "Closeout artifact directory",
            &closeout.artifact_dir,
            "offdesk_closeouts",
        );
        if let Some(path) = &closeout.return_package_path {
            summary.push(ReviewSurfaceArtifactSummary {
                label: "Ondesk return package".to_string(),
                why_it_matters: "Rehydrates a fresh harness with reviewed context and next steps."
                    .to_string(),
                retention_class: "handoff".to_string(),
            });
            push_artifact_ref(
                &mut refs,
                "return_package",
                "Ondesk return package",
                path,
                "RETURN_PACKAGE.md",
            );
        }
        if let Some(review) = &closeout.review {
            summary.push(ReviewSurfaceArtifactSummary {
                label: "Closeout receipt".to_string(),
                why_it_matters:
                    "Records whether execution is accepted truth or still needs follow-up."
                        .to_string(),
                retention_class: "acceptance".to_string(),
            });
            push_artifact_ref(
                &mut refs,
                "closeout_review",
                "Closeout review record",
                &review.record_path,
                "closeout_review_*.json",
            );
            if let Some(path) = &review.receipt_path {
                push_artifact_ref(
                    &mut refs,
                    "closeout_receipt",
                    "Closeout receipt",
                    path,
                    "closeout_receipt_*.json",
                );
            }
        }
    }
    ReviewSurfaceArtifacts {
        index,
        retention_review,
        summary,
        refs,
    }
}

fn push_artifact_ref(
    refs: &mut Vec<ReviewSurfaceArtifactRef>,
    id: &str,
    label: &str,
    path: &Path,
    source: &str,
) {
    refs.push(ReviewSurfaceArtifactRef {
        id: id.to_string(),
        label: label.to_string(),
        path: operator_safe_text(path.to_string_lossy().as_ref()),
        source: source.to_string(),
        present: path.exists(),
    });
}

fn latest_closeout(
    profile_dir: &Path,
    project_key: Option<&str>,
) -> Result<Option<LatestCloseout>> {
    let closeouts_dir = profile_dir.join("offdesk_closeouts");
    if !closeouts_dir.exists() {
        return Ok(None);
    }

    let mut candidates = Vec::new();
    for entry in fs::read_dir(&closeouts_dir)
        .with_context(|| format!("read closeout directory {}", closeouts_dir.display()))?
    {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let plan_path = artifact_dir.join("closeout_plan.json");
        let Ok(plan_content) = fs::read_to_string(&plan_path) else {
            continue;
        };
        let Ok(plan) = serde_json::from_str::<Value>(&plan_content) else {
            continue;
        };
        if !closeout_plan_matches_project(&plan, project_key) {
            continue;
        }
        let generated_at = closeout_plan_generated_at(&plan);
        let closeout_id = plan
            .get("closeout_id")
            .and_then(Value::as_str)
            .map(operator_safe_text)
            .unwrap_or_else(|| "unknown".to_string());
        let return_package_path = plan
            .pointer("/artifacts/return_package_markdown")
            .and_then(Value::as_str)
            .map(PathBuf::from)
            .or_else(|| {
                let fallback = artifact_dir.join("RETURN_PACKAGE.md");
                fallback.exists().then_some(fallback)
            });
        let review = latest_closeout_review(&artifact_dir)?;
        let sort_key = review
            .as_ref()
            .map(|review| review.reviewed_at)
            .unwrap_or(generated_at);
        candidates.push((
            sort_key,
            LatestCloseout {
                closeout_id,
                generated_at,
                artifact_dir,
                plan_path,
                return_package_path,
                review,
            },
        ));
    }

    candidates.sort_by_key(|(sort_key, _)| *sort_key);
    Ok(candidates.pop().map(|(_, closeout)| closeout))
}

fn latest_closeout_review(artifact_dir: &Path) -> Result<Option<LatestCloseoutReview>> {
    let mut reviews = Vec::new();
    for entry in fs::read_dir(artifact_dir)? {
        let entry = entry?;
        let path = entry.path();
        let Some(filename) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if !filename.starts_with("closeout_review_") || !filename.ends_with(".json") {
            continue;
        }
        let Ok(content) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&content) else {
            continue;
        };
        let Some(reviewed_at) = value
            .get("reviewed_at")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc))
        else {
            continue;
        };
        let verdict = value
            .get("verdict")
            .and_then(Value::as_str)
            .map(operator_safe_text)
            .unwrap_or_else(|| "unknown".to_string());
        let receipt_path = value
            .pointer("/artifacts/closeout_receipt_json")
            .and_then(Value::as_str)
            .map(PathBuf::from);
        let receipt = value.get("closeout_receipt").cloned();
        reviews.push(LatestCloseoutReview {
            reviewed_at,
            verdict,
            record_path: path,
            receipt_path,
            receipt,
        });
    }
    reviews.sort_by_key(|review| review.reviewed_at);
    Ok(reviews.pop())
}

fn closeout_plan_matches_project(plan: &Value, project_key: Option<&str>) -> bool {
    let Some(project_key) = project_key else {
        return true;
    };
    if plan
        .pointer("/filters/project_key")
        .and_then(Value::as_str)
        .is_some_and(|value| value == project_key)
    {
        return true;
    }
    plan.get("tasks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .any(|task| {
            task.get("project_key")
                .and_then(Value::as_str)
                .is_some_and(|value| value == project_key)
        })
}

fn closeout_plan_generated_at(plan: &Value) -> DateTime<Utc> {
    plan.get("generated_at")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc))
        .unwrap_or(DateTime::<Utc>::UNIX_EPOCH)
}

fn closeout_unresolved_risks(receipt: &Value) -> Vec<String> {
    let mut risks = Vec::new();
    let open_decisions = receipt
        .get("open_decisions")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or_default();
    if open_decisions > 0 {
        risks.push(format!(
            "{open_decisions} open decision(s) remain in the closeout receipt."
        ));
    }
    let missing_evidence = receipt
        .get("missing_evidence")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or_default();
    if missing_evidence > 0 {
        risks.push(format!(
            "{missing_evidence} missing evidence item(s) must be resolved."
        ));
    }
    let required_first_reads = receipt
        .get("required_first_reads")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or_default();
    if required_first_reads > 0 {
        risks.push(format!(
            "{required_first_reads} required first-read item(s) remain."
        ));
    }
    let unsafe_operations = receipt
        .get("unsafe_operations")
        .and_then(Value::as_array)
        .map(Vec::len)
        .unwrap_or_default();
    if unsafe_operations > 0 {
        risks.push(format!(
            "{unsafe_operations} unsafe operation(s) need review."
        ));
    }
    if receipt
        .get("stale_task_count")
        .and_then(Value::as_u64)
        .unwrap_or_default()
        > 0
    {
        risks.push(
            "One or more tasks changed after the closeout package was generated.".to_string(),
        );
    }
    for key in ["retention_review", "wiki_promotion_state"] {
        if let Some(value) = receipt.get(key).and_then(Value::as_str) {
            if matches!(value, "required" | "review_required" | "audit_unavailable") {
                risks.push(format!("{key} is {value}."));
            }
        }
    }
    risks
}

fn closeout_summary_risks(summary: &OffdeskStatusSummary) -> Vec<String> {
    let mut risks = Vec::new();
    if summary.closeout_state.missing_closeout > 0 {
        risks.push(format!(
            "{} completed task(s) need a closeout package.",
            summary.closeout_state.missing_closeout
        ));
    }
    if summary.closeout_state.pending_review > 0 {
        risks.push(format!(
            "{} closeout package(s) need review.",
            summary.closeout_state.pending_review
        ));
    }
    if summary.closeout_state.revision_required > 0 {
        risks.push(format!(
            "{} closeout review(s) require revision or are blocked.",
            summary.closeout_state.revision_required
        ));
    }
    if summary.closeout_state.approved_with_followups > 0 {
        risks.push(format!(
            "{} approved closeout receipt(s) still have follow-ups.",
            summary.closeout_state.approved_with_followups
        ));
    }
    risks
}

fn closeout_acceptance_reason(status: &str) -> String {
    match status {
        "accepted" => "Closeout receipt accepted the executed scope as accepted truth.".to_string(),
        "approved_with_followups" => {
            "Closeout review is approved, but receipt follow-ups remain before accepted truth."
                .to_string()
        }
        "revision_required" => {
            "Closeout receipt requires revision before accepted truth.".to_string()
        }
        "blocked" => {
            "Closeout receipt is blocked and cannot be treated as accepted truth.".to_string()
        }
        _ => "Closeout receipt must be inspected before accepted truth is set.".to_string(),
    }
}

fn option_matches(filter: Option<&str>, value: Option<&str>) -> bool {
    match filter {
        Some(filter) => value == Some(filter),
        None => true,
    }
}

fn value_usize(value: &Value, key: &str) -> usize {
    value.get(key).and_then(Value::as_u64).unwrap_or_default() as usize
}

fn push_summary_line(output: &mut String, label: &str, value: Option<&str>) {
    if let Some(value) = value {
        output.push_str(&format!("  {label}: {value}\n"));
    }
}

fn text_at<'a>(value: &'a Value, pointer: &str) -> Option<&'a str> {
    value.pointer(pointer).and_then(Value::as_str)
}

fn number_at(value: &Value, pointer: &str) -> Option<u64> {
    value.pointer(pointer).and_then(Value::as_u64)
}

fn format_next_safe_action(action: &Value) -> String {
    if !action.is_object() {
        return action
            .as_str()
            .map(operator_safe_text)
            .unwrap_or_else(|| action.to_string());
    }
    let kind = action
        .get("kind")
        .and_then(Value::as_str)
        .map(operator_safe_text)
        .unwrap_or_else(|| "next".to_string());
    let detail = action
        .get("detail")
        .and_then(Value::as_str)
        .map(operator_safe_text)
        .unwrap_or_default();
    let review = action
        .get("requires_operator_review")
        .and_then(Value::as_bool)
        .map(|value| {
            if value {
                "operator review required"
            } else {
                "monitoring step"
            }
        });
    let mut text = if detail.is_empty() {
        kind
    } else {
        format!("{kind}: {detail}")
    };
    if let Some(review) = review {
        text.push_str(&format!(" ({review})"));
    }
    text
}
