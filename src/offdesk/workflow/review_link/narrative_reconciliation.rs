use super::{checked_artifact, value_digest, AssessmentReference, ReviewLinkRequest};
use crate::offdesk::workflow::ondesk_plan;
use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Reconciliation {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    approved_document: AssessmentReference,
    current_document_sha256: String,
    agreement: AssessmentReference,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Agreement {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    approved_document_sha256: String,
    current_document_sha256: String,
    decided_by: String,
    decision_ref: String,
    permitted_effect: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeReconciliationObservation {
    pub status: String,
    pub reconciliation: AssessmentReference,
    pub approved_document: AssessmentReference,
    pub current_document_sha256: String,
    pub agreement: AssessmentReference,
    pub structured_plan_sha256: String,
    pub task_count: usize,
    pub decided_by: String,
    pub decision_ref: String,
}

pub(super) fn request_sha256(request: &ReviewLinkRequest) -> Result<String> {
    let mut selected = request.clone();
    // Historical acknowledgments can themselves bind this artifact. Omit all
    // acknowledgment references here to keep that graph acyclic; identity,
    // original plan/journal, result, assessment and closeouts remain bound.
    selected.narrative_reconciliation = None;
    selected.historical_boundary = None;
    selected.historical_prefix = None;
    value_digest(serde_json::to_value(selected)?)
}

pub(super) fn observe(
    request: &ReviewLinkRequest,
    profile_identity: (u64, u64),
    project_key: &str,
) -> Result<Option<NarrativeReconciliationObservation>> {
    let Some(reference) = &request.narrative_reconciliation else {
        return Ok(None);
    };
    let selected: Reconciliation =
        serde_json::from_slice(&checked_artifact(&reference.path, &reference.sha256)?)?;
    ensure!(
        selected.schema == "offdesk_review_narrative_reconciliation.v1"
            && selected.review_request_sha256 == request_sha256(request)?
            && selected.profile_identity == profile_identity
            && selected.approved_document.sha256 == request.owning_plan_sha256
            && selected.current_document_sha256 != request.owning_plan_sha256,
        "narrative reconciliation request, profile or approved version mismatch"
    );
    let approved = checked_artifact(
        &selected.approved_document.path,
        &selected.approved_document.sha256,
    )?;
    let current = checked_artifact(&request.owning_plan, &selected.current_document_sha256)?;
    let approved = ondesk_plan::inspect(std::str::from_utf8(&approved)?, project_key)?;
    let current = ondesk_plan::inspect(std::str::from_utf8(&current)?, project_key)?;
    ensure!(
        approved.plan == current.plan,
        "narrative reconciliation requires every structured task to remain exactly equal"
    );
    let agreement: Agreement = serde_json::from_slice(&checked_artifact(
        &selected.agreement.path,
        &selected.agreement.sha256,
    )?)?;
    ensure!(
        agreement.schema == "offdesk_review_narrative_reconciliation_agreement.v1"
            && agreement.review_request_sha256 == selected.review_request_sha256
            && agreement.profile_identity == profile_identity
            && agreement.approved_document_sha256 == selected.approved_document.sha256
            && agreement.current_document_sha256 == selected.current_document_sha256
            && agreement.permitted_effect
                == "link_existing_nonaccepted_decision_across_exact_narrative_change"
            && !agreement.decided_by.trim().is_empty()
            && agreement.decided_by.len() <= 256
            && !agreement.decision_ref.trim().is_empty()
            && agreement.decision_ref.len() <= 4096,
        "narrative reconciliation requires exact separately pinned agreement"
    );
    Ok(Some(NarrativeReconciliationObservation {
        status: "explicit_narrative_reconciliation_not_historical_plan_rewrite".into(),
        reconciliation: reference.clone(),
        approved_document: selected.approved_document,
        current_document_sha256: selected.current_document_sha256,
        agreement: selected.agreement,
        structured_plan_sha256: value_digest(serde_json::to_value(approved.plan.clone())?)?,
        task_count: approved.plan.tasks.len(),
        decided_by: agreement.decided_by,
        decision_ref: agreement.decision_ref,
    }))
}
