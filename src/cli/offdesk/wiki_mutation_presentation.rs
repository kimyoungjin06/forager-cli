//! Adaptive-wiki mutation result envelopes and operator presentation.
//!
//! Command handlers retain all validation, canonical writes, audit appends, and
//! receipt creation. This module only serializes and renders their completed
//! results.

use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::Serialize;

use super::{adaptive_wiki_agent_modes_label, operator_safe_json_value, wiki_scope_label};
use crate::offdesk::{
    operator_safe_text, AdaptiveWikiAuditRecord, AdaptiveWikiCandidate, AdaptiveWikiHumanCandidate,
    AdaptiveWikiHumanEntry, AdaptiveWikiPromotionReceipt,
};

#[derive(Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub(super) enum WikiMutationResult {
    Promote {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
        promotion_receipt: Box<AdaptiveWikiPromotionReceipt>,
        promotion_receipt_path: String,
    },
    Reject {
        candidate: AdaptiveWikiHumanCandidate,
        audit: AdaptiveWikiAuditRecord,
    },
    Rescope {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    Edit {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    Retag {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    Deprecate {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    AddCounterexample {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    UpdateRunbook {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    RenewReviewAfter {
        entry: AdaptiveWikiHumanEntry,
        previous_review_after: Option<DateTime<Utc>>,
        audit: AdaptiveWikiAuditRecord,
    },
}

pub(super) fn present_wiki_candidate_recorded(
    candidate: &AdaptiveWikiCandidate,
    profile: &str,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(candidate)?;
        return Ok(());
    }

    println!("Recorded candidate {}", operator_safe_text(&candidate.id));
    println!(
        "  {:?} · {:?}:{} · confidence {:?}",
        candidate.kind,
        candidate.scope,
        operator_safe_text(&candidate.scope_ref),
        candidate.confidence
    );
    println!("  claim: {}", operator_safe_text(&candidate.claim));
    println!("  occurrences: {}", candidate.occurrence_count);
    println!(
        "  promote: forager -p {} offdesk wiki promote {} --activation-mode context_only",
        operator_safe_text(profile),
        operator_safe_text(&candidate.id)
    );
    Ok(())
}

pub(super) fn present_wiki_mutation(result: &WikiMutationResult, json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(result)?;
        return Ok(());
    }

    match result {
        WikiMutationResult::Promote {
            entry,
            audit,
            promotion_receipt,
            promotion_receipt_path,
        } => {
            println!(
                "Promoted adaptive wiki candidate to entry {}",
                operator_safe_text(&entry.id)
            );
            println!(
                "  scope: {}",
                operator_safe_text(&wiki_scope_label(entry.scope, &entry.scope_ref))
            );
            println!("  activation:  {:?}", entry.activation_mode);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&entry.agent_modes)
            );
            println!("  audit: {}", operator_safe_text(&audit.id));
            println!(
                "  receipt: {}",
                operator_safe_text(&promotion_receipt.receipt_id)
            );
            println!(
                "  receipt_path: {}",
                operator_safe_text(promotion_receipt_path)
            );
        }
        WikiMutationResult::Reject { candidate, audit } => {
            println!(
                "Rejected adaptive wiki candidate {}",
                operator_safe_text(&candidate.id)
            );
            println!("  reason: {}", operator_safe_text(&audit.reason));
            println!("  audit:  {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::Rescope { entry, audit } => {
            println!(
                "Rescoped adaptive wiki entry {}",
                operator_safe_text(&entry.id)
            );
            println!(
                "  scope: {}",
                operator_safe_text(&wiki_scope_label(entry.scope, &entry.scope_ref))
            );
            println!("  audit: {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::Edit { entry, audit } => {
            println!(
                "Edited adaptive wiki entry {}",
                operator_safe_text(&entry.id)
            );
            println!("  kind: {:?}", entry.kind);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&entry.agent_modes)
            );
            println!("  claim: {}", operator_safe_text(&entry.claim));
            if !entry.evidence_refs.is_empty() {
                println!("  evidence: {}", operator_safe_list(&entry.evidence_refs));
            }
            println!("  audit: {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::Retag { entry, audit } => {
            println!(
                "Retagged adaptive wiki entry {}",
                operator_safe_text(&entry.id)
            );
            if !entry.core_tags.is_empty() {
                println!("  core tags: {}", operator_safe_list(&entry.core_tags));
            }
            if !entry.proposed_tags.is_empty() {
                println!(
                    "  proposed tags: {}",
                    operator_safe_list(&entry.proposed_tags)
                );
            }
            println!("  audit: {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::Deprecate { entry, audit } => {
            println!(
                "Deprecated adaptive wiki entry {}",
                operator_safe_text(&entry.id)
            );
            println!("  reason: {}", operator_safe_text(&audit.reason));
            println!("  audit:  {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::AddCounterexample { entry, audit } => {
            println!(
                "Added adaptive wiki counterexample to {}",
                operator_safe_text(&entry.id)
            );
            if let Some(evidence_ref) = audit.evidence_ref.as_deref() {
                println!("  evidence: {}", operator_safe_text(evidence_ref));
            }
            println!("  audit:    {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::UpdateRunbook { entry, audit } => {
            println!(
                "Updated adaptive wiki runbook {}",
                operator_safe_text(&entry.id)
            );
            if !entry.support_refs.is_empty() {
                println!("  support: {}", operator_safe_list(&entry.support_refs));
            }
            if !entry.capability_ids.is_empty() {
                println!(
                    "  capabilities: {}",
                    operator_safe_list(&entry.capability_ids)
                );
            }
            if !entry.required_artifact_kinds.is_empty() {
                println!(
                    "  artifacts: {}",
                    operator_safe_list(&entry.required_artifact_kinds)
                );
            }
            println!("  audit:   {}", operator_safe_text(&audit.id));
        }
        WikiMutationResult::RenewReviewAfter {
            entry,
            previous_review_after,
            audit,
        } => {
            println!(
                "Renewed adaptive wiki review_after {}",
                operator_safe_text(&entry.id)
            );
            println!(
                "  previous: {}",
                previous_review_after
                    .as_ref()
                    .map(DateTime::<Utc>::to_rfc3339)
                    .unwrap_or_else(|| "-".to_string())
            );
            println!(
                "  review_after: {}",
                entry
                    .review_after
                    .as_ref()
                    .map(DateTime::<Utc>::to_rfc3339)
                    .unwrap_or_else(|| "-".to_string())
            );
            println!("  audit: {}", operator_safe_text(&audit.id));
        }
    }
    Ok(())
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn operator_safe_list(values: &[String]) -> String {
    values
        .iter()
        .map(|value| operator_safe_text(value))
        .collect::<Vec<_>>()
        .join(", ")
}
