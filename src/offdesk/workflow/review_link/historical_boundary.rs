use super::{checked_artifact, value_digest, AssessmentReference, ReviewLinkRequest};
use crate::offdesk::workflow::ondesk_plan;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JournalGap {
    pub previous_request_id: String,
    pub next_request_id: String,
    pub previous_candidate_sha256: String,
    pub next_baseline_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Boundary {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    journal_entries: usize,
    suffix_start_index: usize,
    continuous_suffix_entries: usize,
    gap: JournalGap,
    previous_document: AssessmentReference,
    next_document: AssessmentReference,
    agreement: AssessmentReference,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Agreement {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    journal_entries: usize,
    suffix_start_index: usize,
    gap: JournalGap,
    decided_by: String,
    decision_ref: String,
    permitted_effect: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoricalBoundaryObservation {
    pub historical_transition: String,
    pub boundary: AssessmentReference,
    pub agreement: AssessmentReference,
    pub previous_document: AssessmentReference,
    pub next_document: AssessmentReference,
    pub journal_entries: usize,
    pub suffix_start_index: usize,
    pub continuous_suffix_entries: usize,
    pub gap: JournalGap,
    pub decided_by: String,
    pub decision_ref: String,
}

/// Bind the complete request without the boundary reference to avoid a hash cycle.
pub(super) fn request_sha256(request: &ReviewLinkRequest) -> Result<String> {
    let mut selected = request.clone();
    selected.historical_boundary = None;
    value_digest(serde_json::to_value(selected)?)
}

pub(super) fn observe(
    request: &ReviewLinkRequest,
    profile_identity: (u64, u64),
    entries: &[Value],
) -> Result<Option<HistoricalBoundaryObservation>> {
    let Some(reference) = &request.historical_boundary else {
        return Ok(None);
    };
    let boundary: Boundary =
        serde_json::from_slice(&checked_artifact(&reference.path, &reference.sha256)?)?;
    ensure!(
        boundary.schema == "offdesk_historical_journal_boundary.v1"
            && boundary.review_request_sha256 == request_sha256(request)?
            && boundary.profile_identity == profile_identity,
        "historical boundary request or profile mismatch"
    );
    let start = boundary.suffix_start_index;
    ensure!(
        boundary.journal_entries == entries.len()
            && start > 0
            && start < entries.len()
            && boundary.continuous_suffix_entries == entries.len() - start,
        "historical boundary journal range mismatch"
    );
    let previous = &entries[start - 1]["request"];
    let next = &entries[start]["request"];
    ensure!(
        previous["request_id"] == boundary.gap.previous_request_id
            && next["request_id"] == boundary.gap.next_request_id
            && previous["candidate_sha256"] == boundary.gap.previous_candidate_sha256
            && next["baseline_sha256"] == boundary.gap.next_baseline_sha256
            && boundary.gap.previous_candidate_sha256 != boundary.gap.next_baseline_sha256,
        "historical boundary does not match the exact discontinuity"
    );
    ensure!(
        entries[start..]
            .iter()
            .any(|entry| { entry["request"]["request_id"] == request.decision_request_id }),
        "review decision precedes the acknowledged boundary"
    );
    let agreement: Agreement = serde_json::from_slice(&checked_artifact(
        &boundary.agreement.path,
        &boundary.agreement.sha256,
    )?)?;
    ensure!(
        agreement.schema == "offdesk_historical_journal_boundary_agreement.v1"
            && agreement.review_request_sha256 == boundary.review_request_sha256
            && agreement.profile_identity == boundary.profile_identity
            && agreement.journal_entries == boundary.journal_entries
            && agreement.suffix_start_index == start
            && agreement.gap == boundary.gap
            && agreement.permitted_effect
                == "link_current_decision_with_unknown_historical_transition"
            && !agreement.decided_by.trim().is_empty()
            && agreement.decided_by.len() <= 256
            && !agreement.decision_ref.trim().is_empty()
            && agreement.decision_ref.len() <= 4096,
        "historical boundary needs exact separately pinned agreement"
    );
    for (document, expected) in [
        (
            &boundary.previous_document,
            &boundary.gap.previous_candidate_sha256,
        ),
        (&boundary.next_document, &boundary.gap.next_baseline_sha256),
    ] {
        ensure!(
            &document.sha256 == expected,
            "historical endpoint hash mismatch"
        );
        let bytes = checked_artifact(&document.path, expected)?;
        // Endpoints may contain different task sets. Their missing transaction stays unknown.
        let project = entries[start]["request"]["project_key"]
            .as_str()
            .unwrap_or("");
        ondesk_plan::inspect(std::str::from_utf8(&bytes)?, project)?;
    }
    Ok(Some(HistoricalBoundaryObservation {
        historical_transition: "unknown_explicitly_acknowledged_for_this_review_link".into(),
        boundary: reference.clone(),
        agreement: boundary.agreement,
        previous_document: boundary.previous_document,
        next_document: boundary.next_document,
        journal_entries: boundary.journal_entries,
        suffix_start_index: start,
        continuous_suffix_entries: boundary.continuous_suffix_entries,
        gap: boundary.gap,
        decided_by: agreement.decided_by,
        decision_ref: agreement.decision_ref,
    }))
}
