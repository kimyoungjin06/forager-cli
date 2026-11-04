//! Shared, read-only projection for one bounded Offdesk shift review.

use std::{
    collections::{BTreeMap, BTreeSet, HashMap, HashSet},
    path::Path,
};

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

use super::{
    offdesk_shift_manifest_sha256, operator_safe_text, AdaptiveWikiScope, AdaptiveWikiStore,
    BackgroundProbe, BackgroundRunStore, BackgroundRunnerKind, BackgroundRunnerPhase,
    DecisionLedger, DecisionRaisedBy, DecisionRecord, DecisionStatus, JudgmentEvaluator,
    OffdeskCloseoutStateSummary, OffdeskPlanReviewRecord, OffdeskShiftRecordV1,
    OffdeskShiftRoutineKindV1, OffdeskShiftRoutineOutcomeV1, OffdeskShiftStatusV1,
    OffdeskShiftStore, OffdeskShiftTaskOutcomeV1, OffdeskTask, OffdeskTaskStatus, OffdeskTaskStore,
};

pub const OFFDESK_SHIFT_REVIEW_PROJECTION_SCHEMA: &str = "offdesk_shift_review_projection.v1";
pub const OFFDESK_SHIFT_COUNCIL_ESCALATION_PACKET_SCHEMA: &str =
    "offdesk_shift_council_escalation_packet.v1";
const MAX_COUNCIL_DECISIONS: usize = 8;
const MAX_ORIGINAL_GOALS: usize = 32;
const MAX_GOAL_CHARS: usize = 2_000;
const MAX_EVIDENCE_DRILLDOWN_ITEMS: usize = 64;
const MAX_EVIDENCE_DRILLDOWN_TEXT_CHARS: usize = 32 * 1024;
const MAX_EVIDENCE_FIELD_CHARS: usize = 1_024;

const COUNCIL_PACKET_DENIALS: [&str; 8] = [
    "task_dispatch",
    "accepted_truth",
    "plan_revision",
    "wiki_promotion",
    "canonical_mutation",
    "provider_retargeting",
    "destructive_action",
    "external_side_effect",
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftReviewProjectionV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub selection: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub plan_id: String,
    pub plan_review_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub read_policy: Option<super::read_policy::ReadPolicy>,
    pub lifecycle_status: String,
    pub lifecycle_reason: String,
    pub window_starts_at: DateTime<Utc>,
    pub window_stops_at: DateTime<Utc>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_routine_run_id: Option<String>,
    pub review_status: String,
    pub summary: OffdeskShiftReviewSummaryV1,
    pub tasks: Vec<OffdeskShiftTaskReviewV1>,
    pub routines: Vec<OffdeskShiftRoutineReviewV1>,
    pub council: OffdeskShiftCouncilReviewV1,
    pub closeout: OffdeskShiftCloseoutReviewV1,
    pub adaptive_wiki: OffdeskShiftWikiReviewV1,
    pub evidence_drilldown: OffdeskShiftEvidenceDrilldownV1,
    pub attention_reasons: Vec<String>,
    pub authority: OffdeskShiftReviewAuthorityV1,
    pub sources: OffdeskShiftReviewSourcesV1,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftReviewSummaryV1 {
    pub bound_tasks: usize,
    pub task_attempts_reserved: usize,
    pub task_attempts_ticketed: usize,
    pub task_attempts_terminal: usize,
    pub task_attempts_completed: usize,
    pub task_attempts_requiring_recovery: usize,
    pub task_attempts_closed_history: usize,
    pub bound_routines: usize,
    pub routine_runs_reserved: usize,
    pub routine_runs_terminal: usize,
    pub routine_runs_succeeded: usize,
    pub routine_runs_requiring_attention: usize,
    pub open_council_decisions: usize,
    pub closeout_required: usize,
    pub wiki_candidates: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftTaskReviewV1 {
    pub task_id: String,
    pub project_key: String,
    pub capability_id: String,
    pub runner_kind: BackgroundRunnerKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_status: Option<OffdeskTaskStatus>,
    pub current_background_ticket_id: Option<String>,
    pub current_attempt_attribution: String,
    pub task_record_present: bool,
    pub closeout_report: OffdeskTaskCloseoutReportV1,
    pub attempts: Vec<OffdeskShiftTaskAttemptReviewV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskTaskCloseoutReportV1 {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recorded_review: Option<super::task_queue::TaskReviewProjection>,
    pub schema: String,
    pub result_observation: super::result_observation::ResultObservation,
    pub last_observed_supervisor_shell_status: Option<u8>,
    pub historical_scope_status: String,
    pub historical_source_sha256: Option<String>,
    pub original_goal: Option<String>,
    pub original_completion_criteria: Vec<String>,
    pub historical_scope_truncated: bool,
    pub source_status: String,
    pub requested_scope: Option<String>,
    pub requested_scope_provenance: String,
    pub requested_scope_truncated: bool,
    pub execution_status: String,
    pub terminal_phase: Option<BackgroundRunnerPhase>,
    pub actual_changes_status: String,
    pub validation_checks: Vec<String>,
    pub evidence_references: Vec<String>,
    pub evidence_references_truncated: bool,
    pub limitations: Vec<String>,
    pub next_actions: Vec<String>,
    pub review_disposition: String,
    pub plan_update_authorized: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub closed_failure_history: Option<super::failure_history::ClosedFailureHistory>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftTaskAttemptReviewV1 {
    pub launch_id: String,
    pub attempt_number: u32,
    pub background_ticket_id: String,
    pub ticket_receipt_present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome: Option<OffdeskShiftTaskOutcomeV1>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub terminal_phase: Option<BackgroundRunnerPhase>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub live_probe_phase: Option<BackgroundRunnerPhase>,
    pub live_probe_identity: String,
    pub result_artifact_reported_present: bool,
    pub validation_status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRoutineReviewV1 {
    pub run_id: String,
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub scheduled_at: DateTime<Utc>,
    pub reserved_at: DateTime<Utc>,
    pub projection_run: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<DateTime<Utc>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub outcome: Option<OffdeskShiftRoutineOutcomeV1>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    pub evidence_count: usize,
    pub validation_status: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilReviewV1 {
    pub decision_count: usize,
    pub open_count: usize,
    pub recent: Vec<OffdeskShiftCouncilDecisionV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilDecisionV1 {
    pub decision_id: String,
    pub project_key: String,
    pub task_id: String,
    pub status: DecisionStatus,
    pub recommendation: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agreement: Option<bool>,
    pub summary: String,
    pub evidence_gap_count: usize,
    pub risk_note_count: usize,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCloseoutReviewV1 {
    pub scope: String,
    pub required_count: usize,
    pub state: OffdeskCloseoutStateSummary,
    pub accepted_truth_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftWikiReviewV1 {
    pub candidate_count: usize,
    pub review_due_count: usize,
    pub targets: Vec<OffdeskShiftWikiTargetReviewV1>,
    pub promotion_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftWikiTargetReviewV1 {
    pub project_key: String,
    pub wiki_profile: String,
    pub candidate_count: usize,
    pub review_due_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftEvidenceDrilldownV1 {
    pub policy: String,
    pub max_items: usize,
    pub max_text_chars: usize,
    pub total_available: usize,
    pub emitted_count: usize,
    pub omitted_count: usize,
    pub emitted_text_chars: usize,
    pub truncated: bool,
    pub coverage: Vec<OffdeskShiftEvidenceCoverageV1>,
    pub items: Vec<OffdeskShiftEvidenceItemV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftEvidenceCoverageV1 {
    pub source: String,
    pub detail_status: String,
    pub available_items: usize,
    pub emitted_items: usize,
    pub omitted_items: usize,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftEvidenceItemV1 {
    pub source: String,
    pub kind: String,
    pub subject_id: String,
    pub label: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reference: Option<String>,
    pub reference_truncated: bool,
    pub text_truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size_bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reported_present: Option<bool>,
    pub verification: String,
}

#[derive(Debug, Clone)]
struct EvidenceCandidate {
    source: &'static str,
    kind: &'static str,
    subject_id: String,
    label: String,
    reference: Option<String>,
    sha256: Option<String>,
    size_bytes: Option<u64>,
    reported_present: Option<bool>,
    verification: &'static str,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftReviewAuthorityV1 {
    pub review_only: bool,
    pub task_dispatch_authorized: bool,
    pub accepted_truth_authorized: bool,
    pub plan_revision_authorized: bool,
    pub wiki_promotion_authorized: bool,
    pub does_not_authorize: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftReviewSourcesV1 {
    pub shift_ledger: String,
    pub task_store: String,
    pub background_store: String,
    pub decision_ledger: String,
    pub closeout_receipts: String,
    pub adaptive_wiki_candidates: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilEscalationPacketV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub original_goal: OffdeskShiftCouncilOriginalGoalV1,
    pub current_evidence: OffdeskShiftCouncilCurrentEvidenceV1,
    pub drift: OffdeskShiftCouncilDriftV1,
    pub remaining_budget: OffdeskShiftCouncilRemainingBudgetV1,
    pub decision: OffdeskShiftCouncilDecisionRequestV1,
    pub authority: OffdeskShiftCouncilPacketAuthorityV1,
    pub sources: OffdeskShiftCouncilPacketSourcesV1,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilOriginalGoalV1 {
    pub plan_id: String,
    pub plan_review_id: String,
    pub plan_schema: String,
    pub summary: String,
    pub objectives: Vec<String>,
    pub objective_count: usize,
    pub objectives_truncated: bool,
    pub provenance: Vec<String>,
    pub review_reason: String,
    pub review_followups: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilCurrentEvidenceV1 {
    pub review_projection_sha256: String,
    pub review_status: String,
    pub lifecycle_status: String,
    pub lifecycle_reason: String,
    pub tasks: Vec<OffdeskShiftTaskReviewV1>,
    pub routines: Vec<OffdeskShiftRoutineReviewV1>,
    pub open_council_decisions: usize,
    pub closeout_required: usize,
    pub wiki_candidates: usize,
    pub attention_reasons: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilDriftV1 {
    pub assessment: String,
    pub signals: Vec<String>,
    pub semantic_drift_requires_council_judgment: bool,
    pub inference_limit: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilRemainingBudgetV1 {
    pub task: OffdeskShiftCouncilTaskBudgetV1,
    pub routine: OffdeskShiftCouncilRoutineBudgetV1,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub routine_resources: Option<OffdeskShiftCouncilResourceBudgetV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilTaskBudgetV1 {
    pub max_concurrent_tasks: u16,
    pub currently_active_tasks: usize,
    pub remaining_concurrent_slots: usize,
    pub max_unique_task_launches: u16,
    pub launched_unique_tasks: usize,
    pub remaining_unique_task_launches: usize,
    pub max_attempts_per_task: u16,
    pub per_task: Vec<OffdeskShiftCouncilTaskAttemptBudgetV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilTaskAttemptBudgetV1 {
    pub task_id: String,
    pub reserved_attempts: usize,
    pub remaining_attempts: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilRoutineBudgetV1 {
    pub max_runs: u16,
    pub reserved_runs: usize,
    pub unresolved_runs: usize,
    pub remaining_runs: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilResourceBudgetV1 {
    pub limits: OffdeskShiftCouncilResourceAmountsV1,
    pub used: OffdeskShiftCouncilResourceAmountsV1,
    pub remaining: OffdeskShiftCouncilResourceAmountsV1,
    pub usage_measurement_complete: bool,
    pub llm_cost_rate_micro_usd_per_million_tokens: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilResourceAmountsV1 {
    pub process_starts: u64,
    pub external_calls: u64,
    pub disk_bytes_written: u64,
    pub llm_tokens: u64,
    pub cost_micro_usd: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilDecisionRequestV1 {
    pub question: String,
    pub default_if_no_decision: String,
    pub options: Vec<OffdeskShiftCouncilOptionV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilOptionV1 {
    pub id: String,
    pub description: String,
    pub authority_requirement: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilPacketAuthorityV1 {
    pub review_only: bool,
    pub recommendation_authorizes_execution: bool,
    pub task_dispatch_authorized: bool,
    pub accepted_truth_authorized: bool,
    pub plan_revision_authorized: bool,
    pub wiki_promotion_authorized: bool,
    pub does_not_authorize: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftCouncilPacketSourcesV1 {
    pub review_projection_schema: String,
    pub review_projection_sha256: String,
    pub plan_source_sha256: String,
    pub plan_review_sha256: String,
    pub shift_ledger: String,
}

pub fn build_offdesk_shift_council_escalation_packet(
    projection: OffdeskShiftReviewProjectionV1,
    record: &OffdeskShiftRecordV1,
    plan_source: &Value,
    plan_review: &OffdeskPlanReviewRecord,
    tasks: &[OffdeskTask],
) -> Result<OffdeskShiftCouncilEscalationPacketV1> {
    let manifest_sha256 = offdesk_shift_manifest_sha256(&record.manifest)?;
    ensure!(
        projection.shift_id == record.manifest.shift_id
            && projection.manifest_sha256 == manifest_sha256,
        "Council packet review does not match the exact shift manifest"
    );
    ensure!(
        plan_review.plan_id == record.manifest.plan_id
            && plan_review.review_id == record.manifest.plan_review_id
            && plan_review.source_sha256 == record.manifest.plan_source_sha256,
        "Council packet plan review does not match the exact shift manifest"
    );
    ensure!(
        projection.summary.task_attempts_reserved == record.task_launch_reservations.len()
            && projection.summary.task_attempts_ticketed
                == record.task_launch_ticket_receipts.len()
            && projection.summary.task_attempts_terminal == record.task_outcome_receipts.len()
            && projection.summary.routine_runs_reserved == record.routine_run_reservations.len()
            && projection.summary.routine_runs_terminal == record.routine_outcome_receipts.len(),
        "Council packet shift evidence changed after review projection"
    );
    for binding in &record.manifest.task_bindings {
        let task = tasks
            .iter()
            .find(|task| task.task_id == binding.task_id)
            .context("Council packet bound task is missing")?;
        ensure!(
            super::offdesk_task_launch_spec_sha256(task)? == binding.launch_spec_sha256,
            "Council packet task no longer matches the exact shift launch specification"
        );
        let projected_task = projection
            .tasks
            .iter()
            .find(|projected| projected.task_id == operator_safe_text(&binding.task_id))
            .context("Council packet review is missing a bound task")?;
        ensure!(
            projected_task.task_record_present
                && projected_task.current_status == Some(task.status),
            "Council packet task state changed after review projection"
        );
    }

    let (objectives, objective_count, objectives_truncated, provenance) =
        extract_original_goals(plan_source, record, tasks);
    let summary = objectives.first().cloned().unwrap_or_else(|| {
        "Original goal is unavailable from the exact bound plan source.".to_string()
    });
    let review_projection_sha256 =
        format!("{:x}", Sha256::digest(serde_json::to_vec(&projection)?));
    let remaining_budget = remaining_budget(record, &projection)?;
    let drift = council_drift(&projection, objectives.is_empty());
    let current_evidence = OffdeskShiftCouncilCurrentEvidenceV1 {
        review_projection_sha256: review_projection_sha256.clone(),
        review_status: projection.review_status.clone(),
        lifecycle_status: projection.lifecycle_status.clone(),
        lifecycle_reason: projection.lifecycle_reason.clone(),
        tasks: projection.tasks.clone(),
        routines: projection.routines.clone(),
        open_council_decisions: projection.summary.open_council_decisions,
        closeout_required: projection.summary.closeout_required,
        wiki_candidates: projection.summary.wiki_candidates,
        attention_reasons: projection.attention_reasons.clone(),
    };

    Ok(OffdeskShiftCouncilEscalationPacketV1 {
        schema: OFFDESK_SHIFT_COUNCIL_ESCALATION_PACKET_SCHEMA.to_string(),
        generated_at: projection.generated_at,
        shift_id: operator_safe_text(&record.manifest.shift_id),
        manifest_sha256,
        original_goal: OffdeskShiftCouncilOriginalGoalV1 {
            plan_id: operator_safe_text(&record.manifest.plan_id),
            plan_review_id: operator_safe_text(&record.manifest.plan_review_id),
            plan_schema: operator_safe_text(
                plan_source
                    .get("schema")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown"),
            ),
            summary,
            objectives,
            objective_count,
            objectives_truncated,
            provenance,
            review_reason: bounded_operator_text(&plan_review.reason),
            review_followups: plan_review
                .followups
                .iter()
                .take(MAX_ORIGINAL_GOALS)
                .map(|value| bounded_operator_text(value))
                .collect(),
        },
        current_evidence,
        drift,
        remaining_budget,
        decision: council_decision_request(),
        authority: OffdeskShiftCouncilPacketAuthorityV1 {
            review_only: true,
            recommendation_authorizes_execution: false,
            task_dispatch_authorized: false,
            accepted_truth_authorized: false,
            plan_revision_authorized: false,
            wiki_promotion_authorized: false,
            does_not_authorize: COUNCIL_PACKET_DENIALS
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
        },
        sources: OffdeskShiftCouncilPacketSourcesV1 {
            review_projection_schema: OFFDESK_SHIFT_REVIEW_PROJECTION_SCHEMA.to_string(),
            review_projection_sha256,
            plan_source_sha256: record.manifest.plan_source_sha256.clone(),
            plan_review_sha256: record.manifest.plan_review_sha256.clone(),
            shift_ledger: "offdesk_shifts.v1.json".to_string(),
        },
    })
}

fn extract_original_goals(
    plan_source: &Value,
    record: &OffdeskShiftRecordV1,
    tasks: &[OffdeskTask],
) -> (Vec<String>, usize, bool, Vec<String>) {
    let mut candidates = Vec::new();
    if let Some(objective) = plan_source.get("objective").and_then(Value::as_str) {
        candidates.push((objective, "plan.objective".to_string()));
    }
    if let Some(selected_planner) = plan_source
        .get("consensus")
        .and_then(|consensus| consensus.get("selected_planner"))
        .and_then(Value::as_str)
    {
        if let Some((index, selected)) = plan_source
            .get("candidates")
            .and_then(Value::as_array)
            .and_then(|items| {
                items.iter().enumerate().find(|(_, item)| {
                    item.get("planner").and_then(Value::as_str) == Some(selected_planner)
                })
            })
        {
            if let Some(objective) = selected.get("objective").and_then(Value::as_str) {
                candidates.push((objective, format!("plan.candidates[{index}].objective")));
            }
        }
    }
    if let Some(sequence) = plan_source
        .get("execution_sequence")
        .and_then(Value::as_array)
    {
        for (index, item) in sequence.iter().enumerate() {
            if let Some(objective) = item.get("objective").and_then(Value::as_str) {
                candidates.push((
                    objective,
                    format!("plan.execution_sequence[{index}].objective"),
                ));
            }
        }
    }
    for binding in &record.manifest.task_bindings {
        if let Some(task) = tasks.iter().find(|task| task.task_id == binding.task_id) {
            if let Some(packet) = &task.implementation_packet {
                candidates.push((
                    packet.goal.as_str(),
                    format!("task.{}.implementation_packet.goal", binding.task_id),
                ));
            }
        }
    }

    let mut seen = HashSet::new();
    let mut objectives = Vec::new();
    let mut provenance = Vec::new();
    let mut objective_count = 0;
    for (candidate, source) in candidates {
        let value = bounded_operator_text(candidate);
        if value.is_empty() || !seen.insert(value.clone()) {
            continue;
        }
        objective_count += 1;
        if objectives.len() < MAX_ORIGINAL_GOALS {
            objectives.push(value);
            provenance.push(source);
        }
    }
    let objectives_truncated = objective_count > objectives.len();
    (
        objectives,
        objective_count,
        objectives_truncated,
        provenance,
    )
}

fn bounded_operator_text(value: &str) -> String {
    let safe = operator_safe_text(value.trim());
    if safe.chars().count() <= MAX_GOAL_CHARS {
        safe
    } else {
        format!(
            "{}...[truncated]",
            safe.chars().take(MAX_GOAL_CHARS).collect::<String>()
        )
    }
}

fn council_drift(
    projection: &OffdeskShiftReviewProjectionV1,
    original_goal_missing: bool,
) -> OffdeskShiftCouncilDriftV1 {
    let mut signals = projection.attention_reasons.clone();
    if original_goal_missing {
        signals.push("original_goal_unavailable".to_string());
    }
    if matches!(
        projection.lifecycle_status.as_str(),
        "attention_required" | "revision_required" | "blocked"
    ) {
        signals.push(format!("shift_lifecycle_{}", projection.lifecycle_status));
    }
    for task in &projection.tasks {
        for attempt in &task.attempts {
            if !matches!(
                attempt.validation_status.as_str(),
                "terminal_completed" | "ticket_observed"
            ) {
                signals.push(format!(
                    "task:{}:{}",
                    task.task_id, attempt.validation_status
                ));
            }
        }
    }
    for routine in &projection.routines {
        if !matches!(
            routine.validation_status.as_str(),
            "terminal_succeeded" | "projection_in_progress"
        ) {
            signals.push(format!(
                "routine:{}:{}",
                routine.routine_id, routine.validation_status
            ));
        }
    }
    signals.sort();
    signals.dedup();
    OffdeskShiftCouncilDriftV1 {
        assessment: if signals.is_empty() {
            "no_deterministic_drift_detected"
        } else {
            "deterministic_signals_present"
        }
        .to_string(),
        signals,
        semantic_drift_requires_council_judgment: true,
        inference_limit: "Deterministic lifecycle and evidence signals do not prove semantic goal drift; Council must compare them with the original goal."
            .to_string(),
    }
}

fn remaining_budget(
    record: &OffdeskShiftRecordV1,
    projection: &OffdeskShiftReviewProjectionV1,
) -> Result<OffdeskShiftCouncilRemainingBudgetV1> {
    let budget = &record.manifest.budget;
    let active_tasks = projection
        .tasks
        .iter()
        .filter(|task| {
            matches!(
                task.current_status,
                Some(OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running)
            )
        })
        .count();
    let launched_task_ids = record
        .task_launch_reservations
        .iter()
        .map(|reservation| reservation.task_id.as_str())
        .collect::<HashSet<_>>();
    let per_task = record
        .manifest
        .task_bindings
        .iter()
        .map(|binding| {
            let reserved_attempts = record
                .task_launch_reservations
                .iter()
                .filter(|reservation| reservation.task_id == binding.task_id)
                .count();
            let remaining_attempts = usize::from(budget.max_attempts_per_task)
                .checked_sub(reserved_attempts)
                .context("shift task attempts exceed the manifest budget")?;
            Ok(OffdeskShiftCouncilTaskAttemptBudgetV1 {
                task_id: operator_safe_text(&binding.task_id),
                reserved_attempts,
                remaining_attempts,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let routine_resources = record
        .manifest
        .routine_resource_budget
        .as_ref()
        .map(
            |resource_budget| -> Result<OffdeskShiftCouncilResourceBudgetV1> {
                let mut used = OffdeskShiftCouncilResourceAmountsV1 {
                    process_starts: 0,
                    external_calls: 0,
                    disk_bytes_written: 0,
                    llm_tokens: 0,
                    cost_micro_usd: 0,
                };
                for receipt in &record.routine_outcome_receipts {
                    let Some(usage) = &receipt.usage else {
                        continue;
                    };
                    used.process_starts = used
                        .process_starts
                        .checked_add(usage.process_starts)
                        .context("shift routine process usage overflow")?;
                    used.external_calls = used
                        .external_calls
                        .checked_add(usage.external_calls)
                        .context("shift routine external-call usage overflow")?;
                    used.disk_bytes_written = used
                        .disk_bytes_written
                        .checked_add(usage.disk_bytes_written)
                        .context("shift routine disk usage overflow")?;
                    used.llm_tokens = used
                        .llm_tokens
                        .checked_add(usage.llm_tokens)
                        .context("shift routine LLM usage overflow")?;
                    used.cost_micro_usd = used
                        .cost_micro_usd
                        .checked_add(usage.cost_micro_usd)
                        .context("shift routine cost usage overflow")?;
                }
                let limits = OffdeskShiftCouncilResourceAmountsV1 {
                    process_starts: resource_budget.max_process_starts,
                    external_calls: resource_budget.max_external_calls,
                    disk_bytes_written: resource_budget.max_disk_bytes_written,
                    llm_tokens: resource_budget.max_llm_tokens,
                    cost_micro_usd: resource_budget.max_cost_micro_usd,
                };
                let remaining = OffdeskShiftCouncilResourceAmountsV1 {
                    process_starts: limits
                        .process_starts
                        .checked_sub(used.process_starts)
                        .context("shift routine process usage exceeds its budget")?,
                    external_calls: limits
                        .external_calls
                        .checked_sub(used.external_calls)
                        .context("shift routine external-call usage exceeds its budget")?,
                    disk_bytes_written: limits
                        .disk_bytes_written
                        .checked_sub(used.disk_bytes_written)
                        .context("shift routine disk usage exceeds its budget")?,
                    llm_tokens: limits
                        .llm_tokens
                        .checked_sub(used.llm_tokens)
                        .context("shift routine LLM usage exceeds its budget")?,
                    cost_micro_usd: limits
                        .cost_micro_usd
                        .checked_sub(used.cost_micro_usd)
                        .context("shift routine cost usage exceeds its budget")?,
                };
                Ok(OffdeskShiftCouncilResourceBudgetV1 {
                    limits,
                    used,
                    remaining,
                    usage_measurement_complete: record.routine_run_reservations.iter().all(
                        |reservation| {
                            record
                                .routine_outcome_receipts
                                .iter()
                                .any(|outcome| outcome.run_id == reservation.run_id)
                        },
                    ),
                    llm_cost_rate_micro_usd_per_million_tokens: resource_budget
                        .llm_cost_rate_micro_usd_per_million_tokens,
                })
            },
        )
        .transpose()?;

    Ok(OffdeskShiftCouncilRemainingBudgetV1 {
        task: OffdeskShiftCouncilTaskBudgetV1 {
            max_concurrent_tasks: budget.max_concurrent_tasks,
            currently_active_tasks: active_tasks,
            remaining_concurrent_slots: usize::from(budget.max_concurrent_tasks)
                .checked_sub(active_tasks)
                .context("active shift tasks exceed the concurrency budget")?,
            max_unique_task_launches: budget.max_task_launches,
            launched_unique_tasks: launched_task_ids.len(),
            remaining_unique_task_launches: usize::from(budget.max_task_launches)
                .checked_sub(launched_task_ids.len())
                .context("shift unique task launches exceed the manifest budget")?,
            max_attempts_per_task: budget.max_attempts_per_task,
            per_task,
        },
        routine: OffdeskShiftCouncilRoutineBudgetV1 {
            max_runs: budget.max_routine_runs,
            reserved_runs: record.routine_run_reservations.len(),
            unresolved_runs: record
                .routine_run_reservations
                .iter()
                .filter(|reservation| {
                    !record
                        .routine_outcome_receipts
                        .iter()
                        .any(|outcome| outcome.run_id == reservation.run_id)
                })
                .count(),
            remaining_runs: usize::from(budget.max_routine_runs)
                .checked_sub(record.routine_run_reservations.len())
                .context("shift routine runs exceed the manifest budget")?,
        },
        routine_resources,
    })
}

fn council_decision_request() -> OffdeskShiftCouncilDecisionRequestV1 {
    OffdeskShiftCouncilDecisionRequestV1 {
        question: "Should the bounded shift continue unchanged, require a newly reviewed revision, hand off for operator review, or remain blocked?"
            .to_string(),
        default_if_no_decision: "block".to_string(),
        options: vec![
            OffdeskShiftCouncilOptionV1 {
                id: "continue".to_string(),
                description: "Continue only within the existing immutable manifest and its remaining inner approval gates."
                    .to_string(),
                authority_requirement: "existing_shift_authority_only".to_string(),
            },
            OffdeskShiftCouncilOptionV1 {
                id: "revise".to_string(),
                description: "Recommend a changed plan, scope, task, routine, or budget."
                    .to_string(),
                authority_requirement: "new_reviewed_plan_and_manifest".to_string(),
            },
            OffdeskShiftCouncilOptionV1 {
                id: "handoff".to_string(),
                description: "Return the evidence and unresolved choices to the operator without new execution."
                    .to_string(),
                authority_requirement: "operator_review".to_string(),
            },
            OffdeskShiftCouncilOptionV1 {
                id: "block".to_string(),
                description: "Keep the shift inert until the blocking evidence or authority gap is resolved."
                    .to_string(),
                authority_requirement: "no_new_authority".to_string(),
            },
        ],
    }
}

pub fn build_offdesk_shift_review_projection(
    profile: &str,
    shift_id: Option<&str>,
    current_routine_run_id: Option<&str>,
    generated_at: DateTime<Utc>,
) -> Result<Option<OffdeskShiftReviewProjectionV1>> {
    let snapshot = OffdeskShiftStore::read_only(profile)?.snapshot()?;
    let (record, selection) = match shift_id {
        Some(shift_id) => (
            snapshot
                .records
                .iter()
                .find(|record| record.manifest.shift_id == shift_id)
                .with_context(|| format!("bounded Offdesk shift not found: {shift_id}"))?,
            "explicit",
        ),
        None => match snapshot.active() {
            Some(record) => (record, "active"),
            None => match snapshot.latest() {
                Some(record) => (record, "latest"),
                None => return Ok(None),
            },
        },
    };
    if let Some(run_id) = current_routine_run_id {
        let current_run = record
            .routine_run_reservations
            .iter()
            .find(|reservation| reservation.run_id == run_id);
        ensure!(
            current_run.is_some(),
            "current morning projection run is not reserved by the selected shift"
        );
        ensure!(
            current_run.is_some_and(|reservation| {
                reservation.routine_kind == OffdeskShiftRoutineKindV1::MorningReviewBrief
            }),
            "current morning projection run is not a morning-review routine"
        );
    }

    let profile_dir = crate::session::profile_dir_path(profile)?;
    let tasks = OffdeskTaskStore::new(&profile_dir).load()?;
    let backgrounds = BackgroundRunStore::new(&profile_dir).load()?;
    let mut task_ids = HashSet::new();
    let mut ticket_ids = HashSet::new();
    ensure!(
        tasks.iter().all(|task| task_ids.insert(&task.task_id)),
        "Duplicate task identity in report queue"
    );
    ensure!(
        backgrounds
            .iter()
            .all(|probe| ticket_ids.insert(&probe.ticket_id)),
        "Duplicate runtime ticket identity in report store"
    );
    let decisions = DecisionLedger::new(&profile_dir).load()?;
    let mut attention_reasons = Vec::new();
    let task_reviews = build_task_reviews(
        &profile_dir,
        record,
        &tasks,
        &backgrounds,
        &mut attention_reasons,
        generated_at,
    );
    let execution_review_due = generated_at >= record.manifest.window.stops_at
        || matches!(
            record.state.as_ref().map(|state| state.status),
            Some(
                OffdeskShiftStatusV1::AttentionRequired
                    | OffdeskShiftStatusV1::ReviewReady
                    | OffdeskShiftStatusV1::RevisionRequired
                    | OffdeskShiftStatusV1::Blocked
                    | OffdeskShiftStatusV1::Disarmed
                    | OffdeskShiftStatusV1::Accepted
            )
        );
    if execution_review_due {
        if task_reviews.iter().any(|task| task.attempts.is_empty()) {
            push_attention(&mut attention_reasons, "bound_task_not_attempted");
        }
        if task_reviews
            .iter()
            .flat_map(|task| &task.attempts)
            .any(|attempt| attempt.validation_status == "ticket_observed")
        {
            push_attention(&mut attention_reasons, "task_attempt_not_terminal");
        }
    }
    let routine_reviews =
        build_routine_reviews(record, current_routine_run_id, &mut attention_reasons);
    let council = build_council_review(record, &decisions, &mut attention_reasons)?;
    let bound_tasks = tasks
        .iter()
        .filter(|task| {
            record
                .manifest
                .task_bindings
                .iter()
                .any(|binding| binding.task_id == task.task_id)
        })
        .cloned()
        .collect::<Vec<_>>();
    let closeout_state = super::control_loop::summarize_closeout_state(&profile_dir, &bound_tasks);
    let closeout_required = closeout_state.required_count();
    if closeout_required > 0 {
        push_attention(&mut attention_reasons, "closeout_review_required");
    }
    let adaptive_wiki = build_wiki_review(record, generated_at)?;
    if adaptive_wiki.candidate_count > 0 || adaptive_wiki.review_due_count > 0 {
        push_attention(&mut attention_reasons, "wiki_review_required");
    }
    let evidence_drilldown = build_evidence_drilldown(
        record,
        &tasks,
        &backgrounds,
        &decisions,
        closeout_required,
        &adaptive_wiki,
    );
    if matches!(
        record.state.as_ref().map(|state| state.status),
        Some(
            OffdeskShiftStatusV1::AttentionRequired
                | OffdeskShiftStatusV1::RevisionRequired
                | OffdeskShiftStatusV1::Blocked
        )
    ) {
        push_attention(&mut attention_reasons, "shift_lifecycle_requires_attention");
    }

    let task_attempts_ticketed = record.task_launch_ticket_receipts.len();
    let task_attempts_terminal = record.task_outcome_receipts.len();
    let task_attempts_completed = record
        .task_outcome_receipts
        .iter()
        .filter(|outcome| outcome.outcome == OffdeskShiftTaskOutcomeV1::Completed)
        .count();
    let task_attempts_requiring_recovery = task_reviews
        .iter()
        .filter(|task| task.closeout_report.closed_failure_history.is_none())
        .flat_map(|task| &task.attempts)
        .filter(|attempt| {
            attempt.validation_status != "terminal_completed"
                && (execution_review_due || attempt.validation_status != "ticket_observed")
        })
        .count();
    let routine_runs_terminal = record.routine_outcome_receipts.len();
    let routine_runs_succeeded = record
        .routine_outcome_receipts
        .iter()
        .filter(|outcome| outcome.outcome == OffdeskShiftRoutineOutcomeV1::Succeeded)
        .count();
    let routine_runs_requiring_attention = routine_reviews
        .iter()
        .filter(|routine| {
            !matches!(
                routine.validation_status.as_str(),
                "terminal_succeeded" | "projection_in_progress"
            )
        })
        .count();
    let lifecycle_status = record
        .state
        .as_ref()
        .map(|state| shift_status_label(state.status).to_string())
        .unwrap_or_else(|| "prepared".to_string());
    let lifecycle_reason = record
        .state
        .as_ref()
        .map(|state| operator_safe_text(&state.reason))
        .unwrap_or_else(|| "shift manifest is prepared but not armed".to_string());
    let review_status = if !attention_reasons.is_empty() {
        "needs_attention"
    } else if !task_reviews.is_empty()
        && task_reviews
            .iter()
            .all(|task| task.closeout_report.closed_failure_history.is_some())
    {
        "closed_failure_history"
    } else if matches!(
        record.state.as_ref().map(|state| state.status),
        Some(OffdeskShiftStatusV1::ReviewReady | OffdeskShiftStatusV1::Disarmed)
    ) {
        "review_ready"
    } else if record
        .state
        .as_ref()
        .is_some_and(|state| state.status == OffdeskShiftStatusV1::Accepted)
    {
        "accepted"
    } else if record.state.is_none() {
        "prepared"
    } else {
        "in_progress"
    };

    Ok(Some(OffdeskShiftReviewProjectionV1 {
        schema: OFFDESK_SHIFT_REVIEW_PROJECTION_SCHEMA.to_string(),
        generated_at,
        selection: selection.to_string(),
        shift_id: operator_safe_text(&record.manifest.shift_id),
        manifest_sha256: offdesk_shift_manifest_sha256(&record.manifest)?,
        plan_id: operator_safe_text(&record.manifest.plan_id),
        plan_review_id: operator_safe_text(&record.manifest.plan_review_id),
        read_policy: record.manifest.read_policy.clone(),
        lifecycle_status,
        lifecycle_reason,
        window_starts_at: record.manifest.window.starts_at,
        window_stops_at: record.manifest.window.stops_at,
        current_routine_run_id: current_routine_run_id.map(operator_safe_text),
        review_status: review_status.to_string(),
        summary: OffdeskShiftReviewSummaryV1 {
            bound_tasks: record.manifest.task_bindings.len(),
            task_attempts_reserved: record.task_launch_reservations.len(),
            task_attempts_ticketed,
            task_attempts_terminal,
            task_attempts_completed,
            task_attempts_requiring_recovery,
            task_attempts_closed_history: task_reviews
                .iter()
                .filter(|task| task.closeout_report.closed_failure_history.is_some())
                .map(|task| task.attempts.len())
                .sum(),
            bound_routines: record.manifest.routine_bindings.len(),
            routine_runs_reserved: record.routine_run_reservations.len(),
            routine_runs_terminal,
            routine_runs_succeeded,
            routine_runs_requiring_attention,
            open_council_decisions: council.open_count,
            closeout_required,
            wiki_candidates: adaptive_wiki.candidate_count,
        },
        tasks: task_reviews,
        routines: routine_reviews,
        council,
        closeout: OffdeskShiftCloseoutReviewV1 {
            scope: "exact_bound_tasks".to_string(),
            required_count: closeout_required,
            state: closeout_state,
            accepted_truth_authorized: false,
        },
        adaptive_wiki,
        evidence_drilldown,
        attention_reasons,
        authority: OffdeskShiftReviewAuthorityV1 {
            review_only: true,
            task_dispatch_authorized: false,
            accepted_truth_authorized: false,
            plan_revision_authorized: false,
            wiki_promotion_authorized: false,
            does_not_authorize: vec![
                "task_dispatch".to_string(),
                "accepted_truth".to_string(),
                "plan_revision".to_string(),
                "wiki_promotion".to_string(),
                "canonical_mutation".to_string(),
            ],
        },
        sources: OffdeskShiftReviewSourcesV1 {
            shift_ledger: "offdesk_shifts.v1.json".to_string(),
            task_store: "offdesk_tasks.json".to_string(),
            background_store: "background_runs.json".to_string(),
            decision_ledger: "offdesk_decisions.jsonl".to_string(),
            closeout_receipts: "offdesk_closeouts/*/closeout_review_*.json".to_string(),
            adaptive_wiki_candidates: "adaptive_wiki_candidates.json".to_string(),
        },
    }))
}

fn build_task_reviews(
    profile_dir: &Path,
    record: &OffdeskShiftRecordV1,
    tasks: &[OffdeskTask],
    backgrounds: &[BackgroundProbe],
    attention: &mut Vec<String>,
    generated_at: DateTime<Utc>,
) -> Vec<OffdeskShiftTaskReviewV1> {
    let retirements =
        super::failure_history::RetirementIndex::load(profile_dir).unwrap_or_default();
    let tasks_by_id = tasks
        .iter()
        .map(|task| (task.task_id.as_str(), task))
        .collect::<HashMap<_, _>>();
    let probes_by_ticket = backgrounds
        .iter()
        .map(|probe| (probe.ticket_id.as_str(), probe))
        .collect::<HashMap<_, _>>();
    let tickets_by_launch = record
        .task_launch_ticket_receipts
        .iter()
        .map(|ticket| (ticket.launch_id.as_str(), ticket))
        .collect::<HashMap<_, _>>();
    let outcomes_by_launch = record
        .task_outcome_receipts
        .iter()
        .map(|outcome| (outcome.launch_id.as_str(), outcome))
        .collect::<HashMap<_, _>>();

    record
        .manifest
        .task_bindings
        .iter()
        .map(|binding| {
            let task = tasks_by_id.get(binding.task_id.as_str()).copied();
            if task.is_none() {
                push_attention(attention, "bound_task_record_missing");
            }
            let launches: Vec<_> = record
                .task_launch_reservations
                .iter()
                .filter(|launch| launch.task_id == binding.task_id)
                .collect();
            let closed_history = task
                .filter(|task| {
                    super::offdesk_task_launch_spec_sha256(task)
                        .is_ok_and(|hash| hash == binding.launch_spec_sha256)
                        && launches.len() == 1
                        && launches[0].attempt_number == 1
                        && task.background_ticket_id.as_deref()
                            == Some(launches[0].background_ticket_id.as_str())
                        && tickets_by_launch.contains_key(launches[0].launch_id.as_str())
                        && outcomes_by_launch
                            .get(launches[0].launch_id.as_str())
                            .is_some_and(|outcome| {
                                outcome.outcome == OffdeskShiftTaskOutcomeV1::RecoveryRequired
                                    && outcome.terminal_phase == Some(BackgroundRunnerPhase::Failed)
                            })
                })
                .and_then(|task| retirements.closed(profile_dir, task, backgrounds, generated_at));
            let attempts: Vec<OffdeskShiftTaskAttemptReviewV1> = record
                .task_launch_reservations
                .iter()
                .filter(|reservation| reservation.task_id == binding.task_id)
                .map(|reservation| {
                    let ticket = tickets_by_launch
                        .get(reservation.launch_id.as_str())
                        .copied();
                    let outcome = outcomes_by_launch
                        .get(reservation.launch_id.as_str())
                        .copied();
                    let probe = probes_by_ticket
                        .get(reservation.background_ticket_id.as_str())
                        .copied();
                    let probe_identity = probe.map_or("absent", |probe| {
                        if probe.task_id.as_deref() == Some(binding.task_id.as_str())
                            && probe.request_id.as_deref() == Some(binding.request_id.as_str())
                            && probe.project_key.as_deref() == Some(binding.project_key.as_str())
                            && probe.capability_id.as_deref()
                                == Some(binding.capability_id.as_str())
                            && probe.runner_kind == binding.runner_kind
                        {
                            "exact"
                        } else {
                            "mismatch"
                        }
                    });
                    let validation_status = match outcome.map(|outcome| outcome.outcome) {
                        Some(OffdeskShiftTaskOutcomeV1::Completed) => "terminal_completed",
                        Some(OffdeskShiftTaskOutcomeV1::RecoveryRequired) => "recovery_required",
                        Some(OffdeskShiftTaskOutcomeV1::Terminated) => "terminated",
                        Some(OffdeskShiftTaskOutcomeV1::LaunchUnresolved) => "launch_unresolved",
                        Some(OffdeskShiftTaskOutcomeV1::IdentityDrift) => "identity_drift",
                        None if ticket.is_some() => "ticket_observed",
                        None => "reservation_only",
                    };
                    if closed_history.is_none()
                        && validation_status != "terminal_completed"
                        && !matches!(validation_status, "ticket_observed")
                    {
                        push_attention(attention, "task_attempt_requires_attention");
                    }
                    if probe_identity == "mismatch" {
                        push_attention(attention, "background_probe_identity_mismatch");
                    }
                    OffdeskShiftTaskAttemptReviewV1 {
                        launch_id: operator_safe_text(&reservation.launch_id),
                        attempt_number: reservation.attempt_number,
                        background_ticket_id: operator_safe_text(&reservation.background_ticket_id),
                        ticket_receipt_present: ticket.is_some(),
                        outcome: outcome.map(|outcome| outcome.outcome),
                        terminal_phase: outcome.and_then(|outcome| outcome.terminal_phase),
                        live_probe_phase: probe.map(|probe| probe.phase),
                        live_probe_identity: probe_identity.to_string(),
                        result_artifact_reported_present: probe
                            .is_some_and(|probe| probe.result_artifact_present),
                        validation_status: validation_status.to_string(),
                    }
                })
                .collect();
            let mut closeout_report =
                task_closeout_report(task, &binding.launch_spec_sha256, &attempts);
            if task
                .and_then(|task| task.background_ticket_id.as_ref())
                .is_some_and(|ticket| {
                    !attempts
                        .iter()
                        .any(|attempt| &attempt.background_ticket_id == ticket)
                })
            {
                push_attention(attention, "current_queue_ticket_outside_selected_shift");
                let action = "inspect_current_ticket_in_tasks_outside_saved_shifts".to_string();
                if !closeout_report.next_actions.contains(&action) {
                    closeout_report.next_actions.insert(0, action);
                }
            }
            if let Some(history) = closed_history {
                closeout_report.review_disposition = "retired_incomplete_history".into();
                closeout_report.next_actions =
                    vec!["inspect_preserved_failure_history_no_resume".into()];
                closeout_report.closed_failure_history = Some(history);
            }
            if let Some(attempt) = attempts.iter().max_by_key(|attempt| attempt.attempt_number) {
                if attempt.live_probe_identity == "exact" {
                    closeout_report.last_observed_supervisor_shell_status = probes_by_ticket
                        .get(attempt.background_ticket_id.as_str())
                        .and_then(|probe| probe.local_exit_receipt.as_ref())
                        .and_then(|receipt| receipt.status);
                }
            }
            if let Some(task) = task.filter(|task| {
                super::offdesk_task_launch_spec_sha256(task)
                    .is_ok_and(|hash| hash == binding.launch_spec_sha256)
            }) {
                if attempts
                    .iter()
                    .max_by_key(|attempt| attempt.attempt_number)
                    .is_some_and(|attempt| {
                        attempt.validation_status == "terminal_completed"
                            && attempt.ticket_receipt_present
                            && attempt.live_probe_identity != "mismatch"
                            && task.background_ticket_id.as_deref()
                                == Some(&attempt.background_ticket_id)
                    })
                {
                    let view = task.operator_view_with_profile(profile_dir);
                    if !view.next_safe_action.requires_operator_review && view.review.is_some() {
                        closeout_report.review_disposition = view.next_safe_action.kind;
                        closeout_report.recorded_review = view.review;
                        closeout_report.next_actions.retain(|action| {
                            !matches!(
                                action.as_str(),
                                "record_user_agreement_before_updating_ondesk"
                                    | "review_actual_changes_and_task_specific_checks"
                                    | "resolve_result_content_conflict_before_acceptance"
                            )
                        });
                        closeout_report.next_actions.push(
                            "follow_recorded_owning_plan_disposition_and_remaining_work".into(),
                        );
                    }
                }
            }
            if closeout_report
                .next_actions
                .iter()
                .any(|action| action == "resolve_result_content_conflict_before_acceptance")
            {
                push_attention(attention, "result_content_requires_review");
            }
            if let Some(snapshot) = &binding.ondesk_source_snapshot {
                match snapshot.task(&binding.project_key, &binding.task_id) {
                    Ok(original) => {
                        closeout_report.historical_scope_status =
                            "preserved_source_hash_verified".into();
                        closeout_report.historical_source_sha256 =
                            Some(snapshot.source_sha256.clone());
                        closeout_report.original_goal = Some(bounded_operator_text(&original.goal));
                        closeout_report.original_completion_criteria = original
                            .completion_criteria
                            .iter()
                            .take(32)
                            .map(|value| bounded_operator_text(value))
                            .collect();
                        closeout_report.historical_scope_truncated =
                            original.completion_criteria.len() > 32
                                || std::iter::once(&original.goal)
                                    .chain(original.completion_criteria.iter())
                                    .any(|value| {
                                        operator_safe_text(value).chars().count() > MAX_GOAL_CHARS
                                    });
                    }
                    Err(_) => {
                        closeout_report.historical_scope_status = "invalid_preserved_source".into();
                        push_attention(attention, "invalid_preserved_ondesk_source");
                    }
                }
            }
            OffdeskShiftTaskReviewV1 {
                task_id: operator_safe_text(&binding.task_id),
                project_key: operator_safe_text(&binding.project_key),
                capability_id: operator_safe_text(&binding.capability_id),
                runner_kind: binding.runner_kind,
                current_status: task.map(|task| task.status),
                current_background_ticket_id: task
                    .and_then(|task| task.background_ticket_id.as_deref())
                    .map(bounded_operator_text),
                current_attempt_attribution: if task.is_some_and(|task| {
                    super::offdesk_task_launch_spec_sha256(task)
                        .is_ok_and(|hash| hash == binding.launch_spec_sha256)
                }) {
                    match task.and_then(|task| task.background_ticket_id.as_deref()) {
                        Some(ticket)
                            if attempts
                                .iter()
                                .any(|attempt| attempt.background_ticket_id == ticket) =>
                        {
                            "selected_shift"
                        }
                        Some(_) => "outside_selected_shift",
                        None => "no_current_ticket",
                    }
                } else {
                    "task_record_missing_or_drifted"
                }
                .into(),
                task_record_present: task.is_some(),
                closeout_report,
                attempts,
            }
        })
        .collect()
}

fn task_closeout_report(
    task: Option<&OffdeskTask>,
    launch_spec_sha256: &str,
    attempts: &[OffdeskShiftTaskAttemptReviewV1],
) -> OffdeskTaskCloseoutReportV1 {
    let exact = task.filter(|task| {
        super::offdesk_task_launch_spec_sha256(task).is_ok_and(|hash| hash == launch_spec_sha256)
    });
    let source_status = match (task, exact) {
        (_, Some(_)) => "launch_spec_matched",
        (Some(_), None) => "task_record_drifted",
        (None, None) => "task_record_missing",
    };
    let latest = attempts.iter().max_by_key(|attempt| attempt.attempt_number);
    let execution_status = latest.map_or("not_attempted", |attempt| {
        attempt.validation_status.as_str()
    });
    let mut evidence: Vec<String> = vec![format!("launch_spec_sha256:{launch_spec_sha256}")];
    if let Some(task) = exact {
        evidence.extend(task.artifact_refs.iter().take(16).filter_map(|artifact| {
            artifact.path.as_ref().map(|path| {
                format!(
                    "declared:{}={}",
                    bounded_operator_text(&artifact.artifact_id),
                    bounded_operator_text(path)
                )
            })
        }));
        if let Some(path) = &task.result_artifact_path {
            evidence.push(format!("declared_result:{}", bounded_operator_text(path)));
        }
    }
    if let Some(attempt) = latest {
        evidence.push(format!(
            "shift_launch:{}",
            bounded_operator_text(&attempt.launch_id)
        ));
        evidence.push(format!(
            "background_ticket:{}",
            bounded_operator_text(&attempt.background_ticket_id)
        ));
    }
    let mut limitations = vec![
        "Execution completion is not verification of task goals or user acceptance.".into(),
        "Declared artifact paths and presence flags are not verified artifact content.".into(),
        "Local result-file presence does not validate its status field; missing callbacks do not establish a process exit code.".into(),
        "Actual code/data changes and task-specific checks require evidence review.".into(),
    ];
    let mut result_observation = super::result_observation::ResultObservation::default();
    if let (Some(task), Some(attempt)) = (exact, latest) {
        if task.background_ticket_id.as_deref() == Some(&attempt.background_ticket_id)
            && attempt.live_probe_identity != "mismatch"
            && attempt.ticket_receipt_present
        {
            if let Some(path) = &task.result_artifact_path {
                let workdir = std::path::Path::new(&task.workdir);
                let result_path = workdir.join(path);
                result_observation = super::result_observation::observe(
                    workdir,
                    &result_path,
                    &task.task_id,
                    &task.request_id,
                    &attempt.background_ticket_id,
                );
            }
        }
    }
    limitations.push("Result content is a current-file observation, not a launch-time or terminal-time snapshot; reported exit codes are not independently collected process exits.".into());
    if exact.is_none() {
        limitations.push("Current task details are absent or differ from the approved launch; they are not used as historical scope.".into());
    }
    let mut next_actions = match execution_status {
        "terminal_completed" => vec![
            "review_actual_changes_and_task_specific_checks".into(),
            "record_user_agreement_before_updating_ondesk".into(),
        ],
        "ticket_observed" => vec!["inspect_live_execution_without_duplicate_dispatch".into()],
        "not_attempted" => {
            vec!["inspect_shift_window_budget_and_approval_before_any_launch".into()]
        }
        _ => vec!["inspect_exact_attempt_and_resolve_recovery_before_retry".into()],
    };
    if let (Some(task), Some(attempt)) = (exact, latest) {
        if task.background_ticket_id.as_deref() != Some(&attempt.background_ticket_id) {
            limitations.push("Current queue ticket differs from the saved attempt. Its current result file is withheld from this historical report.".into());
            next_actions.insert(
                0,
                "inspect_current_ticket_in_tasks_outside_saved_shifts".into(),
            );
        }
    }
    if exact.is_none() {
        next_actions.insert(
            0,
            "recover_the_approved_scope_from_preserved_evidence".into(),
        );
    }
    if result_observation.identity == "mismatch"
        || (execution_status == "terminal_completed"
            && !matches!(
                result_observation.reported_status.as_deref(),
                Some("completed" | "pending_user_review")
            ))
        || matches!(
            result_observation.reported_status.as_deref(),
            Some("failed" | "cancelled" | "blocked" | "revision_required")
        )
        || result_observation
            .reported_exit_code
            .is_some_and(|code| code != 0)
        || result_observation
            .evidence
            .iter()
            .any(|entry| entry.verification != "current_bytes_hash_match_not_claim_verification")
    {
        next_actions.insert(
            0,
            "resolve_result_content_conflict_before_acceptance".into(),
        );
    }
    if result_observation.coexisting_failure.is_some() {
        limitations.push("A current coexisting failure artifact is preserved separately from the result; neither result presence nor its declared zero exit establishes successful closeout.".into());
        next_actions.insert(0, "inspect_coexisting_failure_before_acceptance".into());
    }
    if latest.is_some_and(|attempt| attempt.live_probe_identity == "mismatch") {
        limitations.push("Live probe identity conflicts with this attempt; do not attribute its contents to the task.".into());
        next_actions.insert(0, "resolve_live_probe_identity_mismatch".into());
    }
    let mut checks = vec![
        format!("source:{source_status}"),
        format!("execution_receipt:{execution_status}"),
    ];
    if let Some(attempt) = latest {
        checks.push(format!(
            "live_probe_identity:{}",
            attempt.live_probe_identity
        ));
    }
    OffdeskTaskCloseoutReportV1 {
        recorded_review: None,
        schema: "offdesk_task_closeout_report.v1".into(),
        result_observation,
        last_observed_supervisor_shell_status: None,
        historical_scope_status: "not_preserved".into(),
        historical_source_sha256: None,
        original_goal: None,
        original_completion_criteria: vec![],
        historical_scope_truncated: false,
        source_status: source_status.into(),
        requested_scope: exact
            .map(|task| bounded_operator_text(&task.command))
            .filter(|scope| !scope.trim().is_empty()),
        requested_scope_provenance:
            "launch_hash_bound_execution_command_not_full_goal_specification".into(),
        requested_scope_truncated: exact
            .is_some_and(|task| operator_safe_text(&task.command).chars().count() > MAX_GOAL_CHARS),
        execution_status: execution_status.into(),
        terminal_phase: latest.and_then(|attempt| attempt.terminal_phase),
        actual_changes_status: "not_independently_verified".into(),
        validation_checks: checks,
        evidence_references: evidence,
        evidence_references_truncated: exact.is_some_and(|task| {
            task.artifact_refs.len() > 16
                || task.artifact_refs.iter().any(|artifact| {
                    operator_safe_text(&artifact.artifact_id).chars().count() > MAX_GOAL_CHARS
                        || artifact.path.as_ref().is_some_and(|path| {
                            operator_safe_text(path).chars().count() > MAX_GOAL_CHARS
                        })
                })
                || task
                    .result_artifact_path
                    .as_ref()
                    .is_some_and(|path| operator_safe_text(path).chars().count() > MAX_GOAL_CHARS)
        }) || latest.is_some_and(|attempt| {
            [&attempt.launch_id, &attempt.background_ticket_id]
                .iter()
                .any(|value| operator_safe_text(value).chars().count() > MAX_GOAL_CHARS)
        }),
        limitations,
        next_actions,
        review_disposition: "pending_user_review".into(),
        plan_update_authorized: false,
        closed_failure_history: None,
    }
}

fn build_routine_reviews(
    record: &OffdeskShiftRecordV1,
    current_routine_run_id: Option<&str>,
    attention: &mut Vec<String>,
) -> Vec<OffdeskShiftRoutineReviewV1> {
    let outcomes = record
        .routine_outcome_receipts
        .iter()
        .map(|outcome| (outcome.run_id.as_str(), outcome))
        .collect::<HashMap<_, _>>();
    record
        .routine_run_reservations
        .iter()
        .map(|reservation| {
            let outcome = outcomes.get(reservation.run_id.as_str()).copied();
            let projection_run = current_routine_run_id == Some(reservation.run_id.as_str());
            let validation_status = match outcome.map(|outcome| outcome.outcome) {
                Some(OffdeskShiftRoutineOutcomeV1::Succeeded) => "terminal_succeeded",
                Some(OffdeskShiftRoutineOutcomeV1::Failed) => "failed",
                Some(OffdeskShiftRoutineOutcomeV1::Skipped) => "skipped",
                Some(OffdeskShiftRoutineOutcomeV1::BudgetExhausted) => "budget_exhausted",
                Some(OffdeskShiftRoutineOutcomeV1::IdentityDrift) => "identity_drift",
                None if projection_run => "projection_in_progress",
                None => "recovery_required",
            };
            if !matches!(
                validation_status,
                "terminal_succeeded" | "projection_in_progress"
            ) {
                push_attention(attention, "routine_run_requires_attention");
            }
            OffdeskShiftRoutineReviewV1 {
                run_id: operator_safe_text(&reservation.run_id),
                routine_id: operator_safe_text(&reservation.routine_id),
                routine_kind: reservation.routine_kind,
                scheduled_at: reservation.scheduled_at,
                reserved_at: reservation.reserved_at,
                projection_run,
                completed_at: outcome.map(|outcome| outcome.completed_at),
                outcome: outcome.map(|outcome| outcome.outcome),
                summary: outcome.map(|outcome| operator_safe_text(&outcome.summary)),
                evidence_count: outcome.map_or(0, |outcome| outcome.evidence.len()),
                validation_status: validation_status.to_string(),
            }
        })
        .collect()
}

fn build_council_review(
    record: &OffdeskShiftRecordV1,
    decisions: &[DecisionRecord],
    attention: &mut Vec<String>,
) -> Result<OffdeskShiftCouncilReviewV1> {
    let task_ids = record
        .manifest
        .task_bindings
        .iter()
        .map(|binding| binding.task_id.as_str())
        .collect::<HashSet<_>>();
    let mut latest = BTreeMap::<&str, &DecisionRecord>::new();
    for decision in decisions.iter().filter(|decision| {
        task_ids.contains(decision.task_id.as_str())
            && (decision.raised_by == DecisionRaisedBy::Council
                || decision.council_review.is_some()
                || decision
                    .judgment_route
                    .as_ref()
                    .is_some_and(|route| route.evaluator == JudgmentEvaluator::Council))
    }) {
        ensure!(
            decision.validation_issues().is_empty(),
            "selected shift has an invalid Council decision record"
        );
        latest.insert(decision.decision_id.as_str(), decision);
    }
    let mut records = latest.into_values().collect::<Vec<_>>();
    records.sort_by_key(|record| record.updated_at);
    let open_count = records
        .iter()
        .filter(|record| decision_is_open(record.status))
        .count();
    if open_count > 0 {
        push_attention(attention, "council_decision_pending");
    }
    let decision_count = records.len();
    let recent = records
        .into_iter()
        .rev()
        .take(MAX_COUNCIL_DECISIONS)
        .map(|record| {
            let review = record.council_review.as_ref();
            OffdeskShiftCouncilDecisionV1 {
                decision_id: operator_safe_text(&record.decision_id),
                project_key: operator_safe_text(&record.project_key),
                task_id: operator_safe_text(&record.task_id),
                status: record.status,
                recommendation: review
                    .map(|review| operator_safe_text(&review.recommendation))
                    .unwrap_or_else(|| "not_recorded".to_string()),
                agreement: review.and_then(|review| review.agreement),
                summary: operator_safe_text(&record.decision_request.summary),
                evidence_gap_count: review.map_or(0, |review| review.evidence_gaps.len()),
                risk_note_count: review.map_or(0, |review| review.risk_notes.len()),
                updated_at: record.updated_at,
            }
        })
        .collect();
    Ok(OffdeskShiftCouncilReviewV1 {
        decision_count,
        open_count,
        recent,
    })
}

fn build_wiki_review(
    record: &OffdeskShiftRecordV1,
    generated_at: DateTime<Utc>,
) -> Result<OffdeskShiftWikiReviewV1> {
    let mut targets = BTreeSet::new();
    for routine in record.manifest.routine_bindings.iter().filter(|routine| {
        routine.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
    }) {
        let project_key = routine
            .project_key
            .as_deref()
            .context("shift Wiki routine has no project key")?;
        let wiki_profile = routine
            .wiki_profile
            .as_deref()
            .context("shift Wiki routine has no Wiki profile")?;
        targets.insert((project_key.to_string(), wiki_profile.to_string()));
    }

    let mut reviews = Vec::new();
    for (project_key, wiki_profile) in targets {
        let root = crate::session::profile_dir_path(&wiki_profile)?;
        let store = AdaptiveWikiStore::new(root);
        let candidates = store
            .load_candidates()
            .with_context(|| format!("load Wiki candidates for profile {wiki_profile}"))?;
        let entries = store
            .load_entries()
            .with_context(|| format!("load Wiki entries for profile {wiki_profile}"))?;
        let candidate_count = candidates
            .candidates
            .iter()
            .filter(|candidate| {
                candidate.scope == AdaptiveWikiScope::Project && candidate.scope_ref == project_key
            })
            .count();
        let review_due_count = entries
            .entries
            .iter()
            .filter(|entry| {
                entry.scope == AdaptiveWikiScope::Project
                    && entry.scope_ref == project_key
                    && entry
                        .review_after
                        .is_some_and(|review_after| review_after <= generated_at)
            })
            .count();
        reviews.push(OffdeskShiftWikiTargetReviewV1 {
            project_key: operator_safe_text(&project_key),
            wiki_profile: operator_safe_text(&wiki_profile),
            candidate_count,
            review_due_count,
        });
    }
    Ok(OffdeskShiftWikiReviewV1 {
        candidate_count: reviews.iter().map(|target| target.candidate_count).sum(),
        review_due_count: reviews.iter().map(|target| target.review_due_count).sum(),
        targets: reviews,
        promotion_authorized: false,
    })
}

fn build_evidence_drilldown(
    record: &OffdeskShiftRecordV1,
    tasks: &[OffdeskTask],
    backgrounds: &[BackgroundProbe],
    decisions: &[DecisionRecord],
    closeout_required: usize,
    wiki: &OffdeskShiftWikiReviewV1,
) -> OffdeskShiftEvidenceDrilldownV1 {
    let mut candidates = Vec::new();

    let routine_outcomes = record
        .routine_outcome_receipts
        .iter()
        .map(|outcome| (outcome.run_id.as_str(), outcome))
        .collect::<HashMap<_, _>>();
    for reservation in &record.routine_run_reservations {
        let Some(outcome) = routine_outcomes.get(reservation.run_id.as_str()) else {
            continue;
        };
        for evidence in &outcome.evidence {
            candidates.push(EvidenceCandidate {
                source: "routine",
                kind: "routine_receipt_evidence",
                subject_id: reservation.run_id.clone(),
                label: reservation.routine_id.clone(),
                reference: Some(evidence.reference.clone()),
                sha256: Some(evidence.sha256.clone()),
                size_bytes: Some(evidence.size_bytes),
                reported_present: None,
                verification: "receipt_hash_and_size_bound",
            });
        }
    }

    let tasks_by_id = tasks
        .iter()
        .map(|task| (task.task_id.as_str(), task))
        .collect::<HashMap<_, _>>();
    for binding in &record.manifest.task_bindings {
        let Some(task) = tasks_by_id.get(binding.task_id.as_str()) else {
            continue;
        };
        for artifact in &task.artifact_refs {
            candidates.push(EvidenceCandidate {
                source: "task",
                kind: "declared_artifact",
                subject_id: binding.task_id.clone(),
                label: artifact.artifact_id.clone(),
                reference: artifact.path.clone(),
                sha256: None,
                size_bytes: None,
                reported_present: Some(artifact.present),
                verification: "task_record_reported_not_live_verified",
            });
        }
        for (kind, label, reference) in [
            (
                "configured_log_artifact",
                "configured log artifact",
                task.log_artifact_path.as_ref(),
            ),
            (
                "configured_result_artifact",
                "configured result artifact",
                task.result_artifact_path.as_ref(),
            ),
        ] {
            if let Some(reference) = reference {
                candidates.push(EvidenceCandidate {
                    source: "task",
                    kind,
                    subject_id: binding.task_id.clone(),
                    label: label.to_string(),
                    reference: Some(reference.clone()),
                    sha256: None,
                    size_bytes: None,
                    reported_present: None,
                    verification: "task_record_configured_not_live_verified",
                });
            }
        }
    }

    let probes_by_ticket = backgrounds
        .iter()
        .map(|probe| (probe.ticket_id.as_str(), probe))
        .collect::<HashMap<_, _>>();
    for reservation in &record.task_launch_reservations {
        let Some(probe) = probes_by_ticket.get(reservation.background_ticket_id.as_str()) else {
            continue;
        };
        for (kind, label, reference, reported_present) in [
            (
                "background_log_artifact",
                "background log artifact",
                probe.log_artifact_path.as_ref(),
                probe.log_artifact_present,
            ),
            (
                "background_result_artifact",
                "background result artifact",
                probe.result_artifact_path.as_ref(),
                probe.result_artifact_present,
            ),
        ] {
            if let Some(reference) = reference {
                candidates.push(EvidenceCandidate {
                    source: "task",
                    kind,
                    subject_id: reservation.launch_id.clone(),
                    label: label.to_string(),
                    reference: Some(reference.clone()),
                    sha256: None,
                    size_bytes: None,
                    reported_present: Some(reported_present),
                    verification: "background_probe_reported_not_live_verified",
                });
            }
        }
    }

    for decision in selected_council_records(record, decisions) {
        let evidence_refs = decision
            .decision_request
            .evidence_refs
            .iter()
            .chain(decision.decision_request.trace_refs.iter())
            .chain(decision.trace_refs.iter())
            .chain(
                decision
                    .judgment_route
                    .iter()
                    .flat_map(|route| route.evidence_refs.iter()),
            );
        for evidence in evidence_refs {
            candidates.push(EvidenceCandidate {
                source: "council",
                kind: "decision_trace_ref",
                subject_id: decision.decision_id.clone(),
                label: evidence.label.clone(),
                reference: Some(evidence.reference.clone()),
                sha256: None,
                size_bytes: None,
                reported_present: None,
                verification: "decision_ledger_recorded_not_live_verified",
            });
        }
        if let Some(review) = &decision.council_review {
            for gap in &review.evidence_gaps {
                candidates.push(EvidenceCandidate {
                    source: "council",
                    kind: "evidence_gap",
                    subject_id: decision.decision_id.clone(),
                    label: "Council evidence gap".to_string(),
                    reference: Some(gap.clone()),
                    sha256: None,
                    size_bytes: None,
                    reported_present: None,
                    verification: "decision_ledger_recorded_not_live_verified",
                });
            }
            for risk in &review.risk_notes {
                candidates.push(EvidenceCandidate {
                    source: "council",
                    kind: "risk_note",
                    subject_id: decision.decision_id.clone(),
                    label: "Council risk note".to_string(),
                    reference: Some(risk.clone()),
                    sha256: None,
                    size_bytes: None,
                    reported_present: None,
                    verification: "decision_ledger_recorded_not_live_verified",
                });
            }
        }
    }

    finalize_evidence_drilldown(candidates, closeout_required, wiki)
}

fn selected_council_records<'a>(
    record: &OffdeskShiftRecordV1,
    decisions: &'a [DecisionRecord],
) -> Vec<&'a DecisionRecord> {
    let task_ids = record
        .manifest
        .task_bindings
        .iter()
        .map(|binding| binding.task_id.as_str())
        .collect::<HashSet<_>>();
    let mut latest = BTreeMap::<&str, &DecisionRecord>::new();
    for decision in decisions.iter().filter(|decision| {
        task_ids.contains(decision.task_id.as_str())
            && (decision.raised_by == DecisionRaisedBy::Council
                || decision.council_review.is_some()
                || decision
                    .judgment_route
                    .as_ref()
                    .is_some_and(|route| route.evaluator == JudgmentEvaluator::Council))
    }) {
        latest.insert(decision.decision_id.as_str(), decision);
    }
    let mut records = latest.into_values().collect::<Vec<_>>();
    records.sort_by_key(|record| record.updated_at);
    records.reverse();
    records.truncate(MAX_COUNCIL_DECISIONS);
    records
}

fn finalize_evidence_drilldown(
    candidates: Vec<EvidenceCandidate>,
    closeout_required: usize,
    wiki: &OffdeskShiftWikiReviewV1,
) -> OffdeskShiftEvidenceDrilldownV1 {
    let total_available = candidates.len();
    let mut available_by_source = BTreeMap::<&str, usize>::new();
    for candidate in &candidates {
        *available_by_source.entry(candidate.source).or_default() += 1;
    }

    let mut items = Vec::new();
    let mut emitted_text_chars = 0_usize;
    for candidate in candidates {
        if items.len() == MAX_EVIDENCE_DRILLDOWN_ITEMS {
            break;
        }
        let (subject_id, subject_truncated) = bounded_evidence_text(&candidate.subject_id);
        let (label, label_truncated) = bounded_evidence_text(&candidate.label);
        let (reference, reference_truncated) = candidate
            .reference
            .as_deref()
            .map(bounded_evidence_text)
            .map_or((None, false), |(value, truncated)| (Some(value), truncated));
        let sha256 = candidate.sha256.as_deref().map(operator_safe_text);
        let text_chars = candidate.source.chars().count()
            + candidate.kind.chars().count()
            + candidate.verification.chars().count()
            + subject_id.chars().count()
            + label.chars().count()
            + reference
                .as_deref()
                .map_or(0, |value| value.chars().count())
            + sha256.as_deref().map_or(0, |value| value.chars().count());
        if emitted_text_chars.saturating_add(text_chars) > MAX_EVIDENCE_DRILLDOWN_TEXT_CHARS {
            continue;
        }
        emitted_text_chars += text_chars;
        items.push(OffdeskShiftEvidenceItemV1 {
            source: candidate.source.to_string(),
            kind: candidate.kind.to_string(),
            subject_id,
            label,
            reference,
            reference_truncated,
            text_truncated: subject_truncated || label_truncated || reference_truncated,
            sha256,
            size_bytes: candidate.size_bytes,
            reported_present: candidate.reported_present,
            verification: candidate.verification.to_string(),
        });
    }

    let mut emitted_by_source = BTreeMap::<&str, usize>::new();
    for item in &items {
        *emitted_by_source.entry(item.source.as_str()).or_default() += 1;
    }
    let mut coverage = [
        (
            "routine",
            "itemized",
            "Only terminal routine receipt references with their bound hash and size are itemized.",
        ),
        (
            "task",
            "itemized",
            "Task and background artifact state is reported evidence, not a live file verification.",
        ),
        (
            "council",
            "itemized",
            "Council references, evidence gaps, and risk notes are ledger-recorded review evidence.",
        ),
        (
            "closeout",
            "summary_only",
            "Closeout remains on the exact bounded aggregate; raw receipt content is not projected here.",
        ),
        (
            "adaptive_wiki",
            "summary_only",
            "Wiki candidate and review-due counts remain summary-only and grant no promotion authority.",
        ),
    ]
    .into_iter()
    .map(|(source, detail_status, note)| {
        let available_items = *available_by_source.get(source).unwrap_or(&0);
        let emitted_items = *emitted_by_source.get(source).unwrap_or(&0);
        OffdeskShiftEvidenceCoverageV1 {
            source: source.to_string(),
            detail_status: detail_status.to_string(),
            available_items,
            emitted_items,
            omitted_items: available_items.saturating_sub(emitted_items),
            note: note.to_string(),
        }
    })
    .collect::<Vec<_>>();
    if closeout_required > 0 {
        coverage
            .iter_mut()
            .find(|item| item.source == "closeout")
            .expect("fixed closeout evidence coverage")
            .note = format!(
            "{closeout_required} closeout review item(s) remain summary-only; raw receipt content is not projected here."
        );
    }
    if wiki.candidate_count > 0 || wiki.review_due_count > 0 {
        coverage
            .iter_mut()
            .find(|item| item.source == "adaptive_wiki")
            .expect("fixed Wiki evidence coverage")
            .note = format!(
            "{} Wiki candidate(s) and {} review-due entry(s) remain summary-only and grant no promotion authority.",
            wiki.candidate_count, wiki.review_due_count
        );
    }

    let emitted_count = items.len();
    let omitted_count = total_available.saturating_sub(emitted_count);
    OffdeskShiftEvidenceDrilldownV1 {
        policy: "bounded_operator_safe_review_evidence".to_string(),
        max_items: MAX_EVIDENCE_DRILLDOWN_ITEMS,
        max_text_chars: MAX_EVIDENCE_DRILLDOWN_TEXT_CHARS,
        total_available,
        emitted_count,
        omitted_count,
        emitted_text_chars,
        truncated: omitted_count > 0 || items.iter().any(|item| item.text_truncated),
        coverage,
        items,
    }
}

fn bounded_evidence_text(value: &str) -> (String, bool) {
    let safe = operator_safe_text(value);
    if safe.chars().count() <= MAX_EVIDENCE_FIELD_CHARS {
        return (safe, false);
    }
    let mut bounded = safe
        .chars()
        .take(MAX_EVIDENCE_FIELD_CHARS.saturating_sub(3))
        .collect::<String>();
    bounded.push_str("...");
    (bounded, true)
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

fn shift_status_label(status: OffdeskShiftStatusV1) -> &'static str {
    match status {
        OffdeskShiftStatusV1::Armed => "armed",
        OffdeskShiftStatusV1::Running => "running",
        OffdeskShiftStatusV1::AttentionRequired => "attention_required",
        OffdeskShiftStatusV1::ReviewReady => "review_ready",
        OffdeskShiftStatusV1::RevisionRequired => "revision_required",
        OffdeskShiftStatusV1::Blocked => "blocked",
        OffdeskShiftStatusV1::Disarmed => "disarmed",
        OffdeskShiftStatusV1::Accepted => "accepted",
    }
}

fn push_attention(reasons: &mut Vec<String>, reason: &str) {
    if !reasons.iter().any(|existing| existing == reason) {
        reasons.push(reason.to_string());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closeout_scope_is_hash_bound_and_bounded() {
        use crate::offdesk::{BackgroundRunnerKind, OffdeskTaskInput};
        let mut task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some("report_task".into()),
                request_id: "report_request".into(),
                project_key: "forager".into(),
                capability_id: "dispatch.runtime".into(),
                runner_kind: BackgroundRunnerKind::LocalTmux,
                command: "x".repeat(MAX_GOAL_CHARS + 1),
                workdir: "/workspace/forager".into(),
                execution_brief: None,
                not_before: None,
                mutation_class: None,
                artifact_refs: vec![],
                implementation_packet: None,
                artifact_kind: None,
                agent_mode: None,
                provider_id: None,
                model: None,
                preview: "report fixture".into(),
                reason: "not part of the approved launch hash".into(),
                log_artifact_path: None,
                result_artifact_path: Some("y".repeat(MAX_GOAL_CHARS + 1)),
            },
            chrono::DateTime::from_timestamp(0, 0).unwrap(),
        );
        let approved_hash = super::super::offdesk_task_launch_spec_sha256(&task).unwrap();
        let report = task_closeout_report(Some(&task), &approved_hash, &[]);
        assert_eq!(report.source_status, "launch_spec_matched");
        assert!(report.requested_scope_truncated);
        assert!(report.evidence_references_truncated);
        assert_eq!(
            report.requested_scope.unwrap(),
            format!("{}...[truncated]", "x".repeat(MAX_GOAL_CHARS))
        );
        task.reason = "changed after launch".into();
        let report = task_closeout_report(Some(&task), &approved_hash, &[]);
        assert_eq!(report.source_status, "launch_spec_matched");
        assert!(!report
            .requested_scope
            .unwrap()
            .contains("changed after launch"));
        task.command = "false".into();
        let report = task_closeout_report(Some(&task), &approved_hash, &[]);
        assert_eq!(report.source_status, "task_record_drifted");
        assert!(report.requested_scope.is_none());
        assert!(!report
            .evidence_references
            .iter()
            .any(|r| r.starts_with("declared")));
        assert!(!report.plan_update_authorized);
    }

    fn attempt(status: &str, number: u32) -> OffdeskShiftTaskAttemptReviewV1 {
        OffdeskShiftTaskAttemptReviewV1 {
            launch_id: format!("launch_{number}"),
            attempt_number: number,
            background_ticket_id: format!("ticket_{number}"),
            ticket_receipt_present: true,
            outcome: None,
            terminal_phase: None,
            live_probe_phase: None,
            live_probe_identity: "absent".into(),
            result_artifact_reported_present: true,
            validation_status: status.into(),
        }
    }

    #[test]
    fn closeout_report_keeps_success_recovery_and_nonexecution_distinct() {
        for status in [
            "terminal_completed",
            "recovery_required",
            "terminated",
            "launch_unresolved",
            "identity_drift",
            "reservation_only",
            "ticket_observed",
        ] {
            let report = task_closeout_report(None, &"a".repeat(64), &[attempt(status, 1)]);
            assert_eq!(report.execution_status, status);
            assert_eq!(report.review_disposition, "pending_user_review");
            assert!(!report.plan_update_authorized);
            assert_eq!(report.actual_changes_status, "not_independently_verified");
            assert!(report.requested_scope.is_none());
            assert!(report
                .evidence_references
                .iter()
                .any(|r| r == "shift_launch:launch_1"));
        }
        let report = task_closeout_report(None, &"a".repeat(64), &[]);
        assert_eq!(report.execution_status, "not_attempted");
        assert!(report
            .next_actions
            .iter()
            .any(|a| a.contains("window_budget_and_approval")));
    }

    #[test]
    fn closeout_uses_latest_attempt_not_vector_order_or_old_success() {
        let mut latest = attempt("launch_unresolved", 2);
        latest.live_probe_identity = "mismatch".into();
        let report = task_closeout_report(
            None,
            &"a".repeat(64),
            &[latest, attempt("terminal_completed", 1)],
        );
        assert_eq!(report.execution_status, "launch_unresolved");
        assert_eq!(
            report.next_actions[0],
            "resolve_live_probe_identity_mismatch"
        );
        assert!(!report.plan_update_authorized);
    }

    #[test]
    fn evidence_drilldown_is_bounded_and_redacts_before_truncation() {
        let candidates = (0..70)
            .map(|index| EvidenceCandidate {
                source: "task",
                kind: "declared_artifact",
                subject_id: format!("task_{index}"),
                label: format!("artifact_{index}"),
                reference: Some(format!(
                    "https://example.test/{}?token=super-secret-value-{index}",
                    "x".repeat(1_500),
                )),
                sha256: None,
                size_bytes: None,
                reported_present: Some(true),
                verification: "task_record_reported_not_live_verified",
            })
            .collect();
        let wiki = OffdeskShiftWikiReviewV1 {
            candidate_count: 2,
            review_due_count: 1,
            targets: Vec::new(),
            promotion_authorized: false,
        };

        let drilldown = finalize_evidence_drilldown(candidates, 3, &wiki);

        assert_eq!(drilldown.total_available, 70);
        assert!(drilldown.emitted_count <= MAX_EVIDENCE_DRILLDOWN_ITEMS);
        assert_eq!(
            drilldown.omitted_count,
            drilldown.total_available - drilldown.emitted_count
        );
        assert!(drilldown.truncated);
        assert!(drilldown.emitted_text_chars <= MAX_EVIDENCE_DRILLDOWN_TEXT_CHARS);
        assert!(drilldown.items.iter().all(|item| {
            item.reference
                .as_deref()
                .is_none_or(|reference| !reference.contains("super-secret-value"))
        }));
        assert!(drilldown.items.iter().all(|item| {
            item.reference
                .as_deref()
                .is_none_or(|reference| reference.chars().count() <= MAX_EVIDENCE_FIELD_CHARS)
        }));
        assert!(drilldown.items.iter().all(|item| item.reference_truncated));
        assert!(drilldown.coverage.iter().any(|coverage| {
            coverage.source == "closeout"
                && coverage.detail_status == "summary_only"
                && coverage.note.contains("3 closeout")
        }));
        assert!(drilldown.coverage.iter().any(|coverage| {
            coverage.source == "adaptive_wiki"
                && coverage.detail_status == "summary_only"
                && coverage.note.contains("2 Wiki candidate")
        }));
    }
}
