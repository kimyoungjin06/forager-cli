use super::{
    checked_artifact, historical_boundary::JournalGap, value_digest, AssessmentReference,
    ReviewLinkRequest,
};
use crate::offdesk::workflow::{ondesk_plan_store::PlanChangeRequest, ondesk_review};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PrefixGap {
    pub entry_index: usize,
    pub gap: JournalGap,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    schema: String,
    source_path: PathBuf,
    entries: Vec<Value>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Prefix {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    prefix_entries: usize,
    snapshot: AssessmentReference,
    baseline_document: AssessmentReference,
    gaps: Vec<PrefixGap>,
    agreement: AssessmentReference,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Agreement {
    schema: String,
    review_request_sha256: String,
    profile_identity: (u64, u64),
    prefix_entries: usize,
    snapshot_sha256: String,
    baseline_document_sha256: String,
    gaps: Vec<PrefixGap>,
    decided_by: String,
    decision_ref: String,
    permitted_effect: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoricalPrefixObservation {
    pub historical_transition: String,
    pub prefix: AssessmentReference,
    pub snapshot: AssessmentReference,
    pub baseline_document: AssessmentReference,
    pub agreement: AssessmentReference,
    pub prefix_entries: usize,
    pub journal_entries: usize,
    pub strict_suffix_entries: usize,
    pub gaps: Vec<PrefixGap>,
    pub decided_by: String,
    pub decision_ref: String,
}

pub(super) fn request_sha256(request: &ReviewLinkRequest) -> Result<String> {
    let mut selected = request.clone();
    selected.historical_prefix = None;
    value_digest(serde_json::to_value(selected)?)
}

pub(super) fn observe(
    request: &ReviewLinkRequest,
    profile_identity: (u64, u64),
    entries: &[Value],
) -> Result<Option<HistoricalPrefixObservation>> {
    let Some(reference) = &request.historical_prefix else {
        return Ok(None);
    };
    ensure!(
        request.historical_boundary.is_none(),
        "historical acknowledgments are exclusive"
    );
    let prefix: Prefix =
        serde_json::from_slice(&checked_artifact(&reference.path, &reference.sha256)?)?;
    ensure!(
        prefix.schema == "offdesk_historical_journal_prefix.v1"
            && prefix.review_request_sha256 == request_sha256(request)?
            && prefix.profile_identity == profile_identity,
        "historical prefix request or profile mismatch"
    );
    let count = prefix.prefix_entries;
    ensure!(
        count > 0 && count < entries.len() && entries.len() <= 512,
        "historical prefix range mismatch"
    );
    let snapshot: Snapshot = serde_json::from_slice(&checked_artifact(
        &prefix.snapshot.path,
        &prefix.snapshot.sha256,
    )?)?;
    ensure!(
        snapshot.schema == "ondesk_plan_change_journal.v1"
            && snapshot.source_path == request.owning_plan
            && snapshot.entries.len() == count
            && snapshot.entries == entries[..count],
        "historical snapshot does not preserve the exact journal prefix"
    );
    let mut gaps = Vec::new();
    for index in 1..=count {
        let previous = &entries[index - 1]["request"];
        let next = &entries[index]["request"];
        if previous["candidate_sha256"] != next["baseline_sha256"] {
            gaps.push(PrefixGap {
                entry_index: index,
                gap: JournalGap {
                    previous_request_id: previous["request_id"]
                        .as_str()
                        .context("prefix previous request missing")?
                        .into(),
                    next_request_id: next["request_id"]
                        .as_str()
                        .context("prefix next request missing")?
                        .into(),
                    previous_candidate_sha256: previous["candidate_sha256"]
                        .as_str()
                        .context("prefix previous candidate missing")?
                        .into(),
                    next_baseline_sha256: next["baseline_sha256"]
                        .as_str()
                        .context("prefix next baseline missing")?
                        .into(),
                },
            });
        }
    }
    ensure!(
        !gaps.is_empty() && prefix.gaps == gaps,
        "historical prefix must disclose every exact gap including its boundary"
    );
    let first: PlanChangeRequest = serde_json::from_value(entries[count]["request"].clone())?;
    ensure!(
        first.request_id == request.decision_request_id
            && first.source_path == request.owning_plan
            && first.baseline_sha256 == prefix.baseline_document.sha256,
        "selected decision must start the strict suffix at the pinned baseline"
    );
    let baseline = checked_artifact(
        &prefix.baseline_document.path,
        &prefix.baseline_document.sha256,
    )?;
    let review = first
        .review_record
        .clone()
        .context("prefix suffix requires an explicit new review decision")?;
    let proposal = ondesk_review::propose(
        std::str::from_utf8(&baseline)?,
        &request.owning_plan,
        review,
    )?;
    ensure!(
        proposal.change_request == first
            && proposal.change_request.candidate_sha256 == request.owning_plan_sha256,
        "new decision does not reproduce the current plan from the acknowledged baseline"
    );
    let agreement: Agreement = serde_json::from_slice(&checked_artifact(
        &prefix.agreement.path,
        &prefix.agreement.sha256,
    )?)?;
    ensure!(
        agreement.schema == "offdesk_historical_journal_prefix_agreement.v1"
            && agreement.review_request_sha256 == prefix.review_request_sha256
            && agreement.profile_identity == profile_identity
            && agreement.prefix_entries == count
            && agreement.snapshot_sha256 == prefix.snapshot.sha256
            && agreement.baseline_document_sha256 == prefix.baseline_document.sha256
            && agreement.gaps == gaps
            && agreement.permitted_effect == "link_current_decision_with_unknown_historical_prefix"
            && !agreement.decided_by.trim().is_empty()
            && agreement.decided_by.len() <= 256
            && !agreement.decision_ref.trim().is_empty()
            && agreement.decision_ref.len() <= 4096,
        "historical prefix needs exact separately pinned agreement"
    );
    Ok(Some(HistoricalPrefixObservation {
        historical_transition: "unknown_prefix_explicitly_acknowledged_for_this_review_link".into(),
        prefix: reference.clone(),
        snapshot: prefix.snapshot,
        baseline_document: prefix.baseline_document,
        agreement: prefix.agreement,
        prefix_entries: count,
        journal_entries: entries.len(),
        strict_suffix_entries: entries.len() - count,
        gaps,
        decided_by: agreement.decided_by,
        decision_ref: agreement.decision_ref,
    }))
}
