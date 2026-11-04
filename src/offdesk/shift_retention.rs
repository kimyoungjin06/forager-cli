//! Read-only retention policy for bounded Offdesk shift records and evidence.

use std::path::Path;

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::Serialize;

use super::{
    operator_safe_identity, operator_safe_text, OffdeskShiftRecordV1, OffdeskShiftRoutineKindV1,
    OffdeskShiftStatusV1, OFFDESK_SHIFT_STORE_SNAPSHOT_SCHEMA,
};

pub const OFFDESK_SHIFT_RETENTION_REPORT_SCHEMA: &str = "offdesk_shift_retention_report.v1";
pub const OFFDESK_SHIFT_RETENTION_POLICY_SCHEMA: &str = "offdesk_shift_retention_policy.v1";

const MAX_RETENTION_ITEMS: usize = 128;
const MAX_COMPACT_AFTER_DAYS: u16 = 3650;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftEvidenceRetentionDispositionV1 {
    Preserve,
    CompactDerivedViewCandidate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftEvidenceRetentionReasonV1 {
    DerivedReviewWindowElapsed,
    RetentionWindowOpen,
    ShiftStillNeedsReview,
    NonDerivedEvidence,
    WikiCandidateQueue,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRetentionPolicyV1 {
    pub schema: String,
    pub compact_after_days: u16,
    pub canonical_ledger_disposition: String,
    pub canonical_receipt_disposition: String,
    pub derived_review_artifact_disposition: String,
    pub wiki_candidate_disposition: String,
    pub eligible_derived_artifact_names: Vec<String>,
    pub eligible_terminal_states: Vec<OffdeskShiftStatusV1>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRetentionAuthorityV1 {
    pub read_only: bool,
    pub source_ledger_mutated: bool,
    pub evidence_mutated: bool,
    pub compaction_authorized: bool,
    pub deletion_authorized: bool,
    pub archive_authorized: bool,
    pub accepted_truth_authorized: bool,
    pub wiki_promotion_authorized: bool,
    pub requires_separate_retention_approval: bool,
    pub does_not_authorize: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRetentionSummaryV1 {
    pub shift_count: usize,
    pub canonical_ledger_record_count: usize,
    pub canonical_receipt_count: usize,
    pub acceptance_binding_count: usize,
    pub evidence_reference_count: usize,
    pub evidence_bytes: u64,
    pub preserved_evidence_reference_count: usize,
    pub compaction_candidate_count: usize,
    pub compaction_candidate_bytes: u64,
    pub emitted_item_count: usize,
    pub omitted_item_count: usize,
    pub items_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRetentionItemV1 {
    pub shift_id: String,
    pub run_id: String,
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub shift_status: Option<OffdeskShiftStatusV1>,
    pub completed_at: DateTime<Utc>,
    pub age_days: i64,
    pub reference: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub disposition: OffdeskShiftEvidenceRetentionDispositionV1,
    pub reason: OffdeskShiftEvidenceRetentionReasonV1,
    pub live_artifact_verified: bool,
    pub ledger_receipt_preserved: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftRetentionReportV1 {
    pub schema: String,
    pub source_snapshot_schema: String,
    pub profile: String,
    pub selected_shift_id: Option<String>,
    pub generated_at: DateTime<Utc>,
    pub policy: OffdeskShiftRetentionPolicyV1,
    pub summary: OffdeskShiftRetentionSummaryV1,
    pub items: Vec<OffdeskShiftRetentionItemV1>,
    pub authority: OffdeskShiftRetentionAuthorityV1,
}

pub fn build_offdesk_shift_retention_report(
    profile: &str,
    records: &[OffdeskShiftRecordV1],
    selected_shift_id: Option<&str>,
    compact_after_days: u16,
    generated_at: DateTime<Utc>,
) -> Result<OffdeskShiftRetentionReportV1> {
    ensure!(
        compact_after_days <= MAX_COMPACT_AFTER_DAYS,
        "shift retention window cannot exceed {MAX_COMPACT_AFTER_DAYS} days"
    );

    let policy = OffdeskShiftRetentionPolicyV1 {
        schema: OFFDESK_SHIFT_RETENTION_POLICY_SCHEMA.to_string(),
        compact_after_days,
        canonical_ledger_disposition: "preserve".to_string(),
        canonical_receipt_disposition: "preserve".to_string(),
        derived_review_artifact_disposition: "review_after_window".to_string(),
        wiki_candidate_disposition: "preserve_candidate_queue".to_string(),
        eligible_derived_artifact_names: vec![
            "morning-review.json".to_string(),
            "morning-review.md".to_string(),
        ],
        eligible_terminal_states: vec![
            OffdeskShiftStatusV1::Disarmed,
            OffdeskShiftStatusV1::Accepted,
        ],
    };
    let mut summary = OffdeskShiftRetentionSummaryV1 {
        shift_count: records.len(),
        canonical_ledger_record_count: 0,
        canonical_receipt_count: 0,
        acceptance_binding_count: 0,
        evidence_reference_count: 0,
        evidence_bytes: 0,
        preserved_evidence_reference_count: 0,
        compaction_candidate_count: 0,
        compaction_candidate_bytes: 0,
        emitted_item_count: 0,
        omitted_item_count: 0,
        items_complete: true,
    };
    let mut items = Vec::new();

    for record in records {
        summary.canonical_ledger_record_count += canonical_record_count(record);
        summary.canonical_receipt_count += canonical_receipt_count(record);
        if record
            .state
            .as_ref()
            .is_some_and(|state| state.status == OffdeskShiftStatusV1::Accepted)
        {
            summary.acceptance_binding_count += 1;
        }
        let shift_status = record.state.as_ref().map(|state| state.status);
        for outcome in &record.routine_outcome_receipts {
            for evidence in &outcome.evidence {
                summary.evidence_reference_count += 1;
                summary.evidence_bytes = summary
                    .evidence_bytes
                    .checked_add(evidence.size_bytes)
                    .context("shift retention evidence byte total overflow")?;
                let age_days = generated_at
                    .signed_duration_since(outcome.completed_at)
                    .num_days()
                    .max(0);
                let (disposition, reason) = retention_disposition(
                    outcome.routine_kind,
                    shift_status,
                    &evidence.reference,
                    outcome.completed_at <= generated_at,
                    age_days,
                    compact_after_days,
                );
                match disposition {
                    OffdeskShiftEvidenceRetentionDispositionV1::Preserve => {
                        summary.preserved_evidence_reference_count += 1;
                    }
                    OffdeskShiftEvidenceRetentionDispositionV1::CompactDerivedViewCandidate => {
                        summary.compaction_candidate_count += 1;
                        summary.compaction_candidate_bytes = summary
                            .compaction_candidate_bytes
                            .checked_add(evidence.size_bytes)
                            .context("shift retention candidate byte total overflow")?;
                    }
                }
                if items.len() < MAX_RETENTION_ITEMS {
                    items.push(OffdeskShiftRetentionItemV1 {
                        shift_id: operator_safe_identity(&record.manifest.shift_id),
                        run_id: operator_safe_identity(&outcome.run_id),
                        routine_id: operator_safe_identity(&outcome.routine_id),
                        routine_kind: outcome.routine_kind,
                        shift_status,
                        completed_at: outcome.completed_at,
                        age_days,
                        reference: operator_safe_text(&evidence.reference),
                        sha256: evidence.sha256.clone(),
                        size_bytes: evidence.size_bytes,
                        disposition,
                        reason,
                        live_artifact_verified: false,
                        ledger_receipt_preserved: true,
                    });
                }
            }
        }
    }

    summary.emitted_item_count = items.len();
    summary.omitted_item_count = summary.evidence_reference_count.saturating_sub(items.len());
    summary.items_complete = summary.omitted_item_count == 0;
    Ok(OffdeskShiftRetentionReportV1 {
        schema: OFFDESK_SHIFT_RETENTION_REPORT_SCHEMA.to_string(),
        source_snapshot_schema: OFFDESK_SHIFT_STORE_SNAPSHOT_SCHEMA.to_string(),
        profile: operator_safe_text(profile),
        selected_shift_id: selected_shift_id.map(operator_safe_identity),
        generated_at,
        policy,
        summary,
        items,
        authority: OffdeskShiftRetentionAuthorityV1 {
            read_only: true,
            source_ledger_mutated: false,
            evidence_mutated: false,
            compaction_authorized: false,
            deletion_authorized: false,
            archive_authorized: false,
            accepted_truth_authorized: false,
            wiki_promotion_authorized: false,
            requires_separate_retention_approval: true,
            does_not_authorize: vec![
                "shift_ledger_compaction".to_string(),
                "evidence_deletion".to_string(),
                "evidence_archival".to_string(),
                "wiki_candidate_removal".to_string(),
                "accepted_truth".to_string(),
            ],
        },
    })
}

fn canonical_record_count(record: &OffdeskShiftRecordV1) -> usize {
    1 + usize::from(record.arm_receipt.is_some())
        + usize::from(record.state.is_some())
        + record.transitions.len()
        + record.task_launch_reservations.len()
        + record.task_launch_ticket_receipts.len()
        + record.task_outcome_receipts.len()
        + record.routine_run_reservations.len()
        + record.routine_outcome_receipts.len()
}

fn canonical_receipt_count(record: &OffdeskShiftRecordV1) -> usize {
    usize::from(record.arm_receipt.is_some())
        + record.transitions.len()
        + record.task_launch_ticket_receipts.len()
        + record.task_outcome_receipts.len()
        + record.routine_outcome_receipts.len()
}

fn retention_disposition(
    routine_kind: OffdeskShiftRoutineKindV1,
    shift_status: Option<OffdeskShiftStatusV1>,
    reference: &str,
    completed_in_past: bool,
    age_days: i64,
    compact_after_days: u16,
) -> (
    OffdeskShiftEvidenceRetentionDispositionV1,
    OffdeskShiftEvidenceRetentionReasonV1,
) {
    if routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation {
        return (
            OffdeskShiftEvidenceRetentionDispositionV1::Preserve,
            OffdeskShiftEvidenceRetentionReasonV1::WikiCandidateQueue,
        );
    }
    if routine_kind != OffdeskShiftRoutineKindV1::MorningReviewBrief
        || !is_derived_review_artifact(reference)
    {
        return (
            OffdeskShiftEvidenceRetentionDispositionV1::Preserve,
            OffdeskShiftEvidenceRetentionReasonV1::NonDerivedEvidence,
        );
    }
    if !matches!(
        shift_status,
        Some(OffdeskShiftStatusV1::Disarmed | OffdeskShiftStatusV1::Accepted)
    ) {
        return (
            OffdeskShiftEvidenceRetentionDispositionV1::Preserve,
            OffdeskShiftEvidenceRetentionReasonV1::ShiftStillNeedsReview,
        );
    }
    if !completed_in_past || age_days < i64::from(compact_after_days) {
        return (
            OffdeskShiftEvidenceRetentionDispositionV1::Preserve,
            OffdeskShiftEvidenceRetentionReasonV1::RetentionWindowOpen,
        );
    }
    (
        OffdeskShiftEvidenceRetentionDispositionV1::CompactDerivedViewCandidate,
        OffdeskShiftEvidenceRetentionReasonV1::DerivedReviewWindowElapsed,
    )
}

fn is_derived_review_artifact(reference: &str) -> bool {
    Path::new(reference)
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| matches!(name, "morning-review.json" | "morning-review.md"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_old_terminal_morning_views_become_candidates() {
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::MorningReviewBrief,
                Some(OffdeskShiftStatusV1::Disarmed),
                "/tmp/morning-review.json",
                true,
                30,
                30,
            ),
            (
                OffdeskShiftEvidenceRetentionDispositionV1::CompactDerivedViewCandidate,
                OffdeskShiftEvidenceRetentionReasonV1::DerivedReviewWindowElapsed,
            )
        );
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::MorningReviewBrief,
                Some(OffdeskShiftStatusV1::ReviewReady),
                "/tmp/morning-review.md",
                true,
                90,
                30,
            )
            .1,
            OffdeskShiftEvidenceRetentionReasonV1::ShiftStillNeedsReview
        );
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::MorningReviewBrief,
                Some(OffdeskShiftStatusV1::Accepted),
                "/tmp/morning-review.md",
                true,
                29,
                30,
            )
            .1,
            OffdeskShiftEvidenceRetentionReasonV1::RetentionWindowOpen
        );
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::MorningReviewBrief,
                Some(OffdeskShiftStatusV1::Accepted),
                "/tmp/raw-review.log",
                true,
                90,
                30,
            )
            .1,
            OffdeskShiftEvidenceRetentionReasonV1::NonDerivedEvidence
        );
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
                Some(OffdeskShiftStatusV1::Accepted),
                "/tmp/morning-review.json",
                true,
                90,
                30,
            )
            .1,
            OffdeskShiftEvidenceRetentionReasonV1::WikiCandidateQueue
        );
        assert_eq!(
            retention_disposition(
                OffdeskShiftRoutineKindV1::MorningReviewBrief,
                Some(OffdeskShiftStatusV1::Accepted),
                "/tmp/morning-review.json",
                false,
                0,
                0,
            )
            .1,
            OffdeskShiftEvidenceRetentionReasonV1::RetentionWindowOpen
        );
    }
}
