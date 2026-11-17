//! Adaptive-wiki curator review report presentation.
//!
//! Command handlers retain queue-filter validation, store selection, report
//! generation, and report-file writes. This module only renders completed
//! reports for JSON and terminal consumers.

use anyhow::Result;
use serde::Serialize;

use super::operator_safe_json_value;
use crate::offdesk::{operator_safe_text, AdaptiveWikiReviewReport};

pub(super) fn present_wiki_review_report(
    report: &AdaptiveWikiReviewReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki review report {} {} proposals ({} open, {} filtered out) at {}",
        action,
        report.summary.proposals,
        report.summary.open_proposals,
        report.summary.filtered_out_proposals,
        operator_safe_text(&report.report_dir)
    );
    println!(
        "  checked: {} entries, {} candidates, {} usage records, {} audit records, {} correction records, {} review events",
        report.summary.entries_checked,
        report.summary.candidates_checked,
        report.summary.usage_records_checked,
        report.summary.audit_records_checked,
        report.summary.correction_records_checked,
        report.summary.review_events_checked
    );
    println!(
        "  lint: {} errors, {} warnings, {} info",
        report.summary.lint_errors, report.summary.lint_warnings, report.summary.lint_info
    );
    println!(
        "  lifecycle: {} with events, {} accepted, {} rejected, {} superseded",
        report.summary.proposals_with_events,
        report.summary.accepted_proposals,
        report.summary.rejected_proposals,
        report.summary.superseded_proposals
    );
    println!(
        "  promotion receipts: {} checked, {} invalid files, {} promoted entries covered, {} missing receipts",
        report.summary.promotion_receipts_checked,
        report.summary.promotion_receipt_files_invalid,
        report.summary.promoted_entries_with_promotion_receipt,
        report.summary.promoted_entries_missing_promotion_receipt
    );
    if report.summary.stale_decision_proposals > 0 {
        println!(
            "  stale decisions: {} need renewed review",
            report.summary.stale_decision_proposals
        );
    }
    for proposal in &report.proposals {
        let lifecycle = proposal
            .lifecycle
            .as_ref()
            .map(|lifecycle| {
                let stale = if lifecycle.stale { ", stale" } else { "" };
                format!(
                    "{:?} by {}{}",
                    lifecycle.decision,
                    operator_safe_or_dash(&lifecycle.actor),
                    stale
                )
            })
            .unwrap_or_else(|| "Open".to_string());
        println!(
            "  - {:?} {} {} ({:?}, {}): {}",
            proposal.action,
            operator_safe_text(&proposal.subject_kind),
            operator_safe_text(&proposal.subject_id),
            proposal.risk,
            lifecycle,
            operator_safe_text(&proposal.title)
        );
        if let Some(lifecycle) = proposal.lifecycle.as_ref() {
            println!(
                "    lifecycle: event={} at={} reason={}",
                operator_safe_text(&lifecycle.latest_event_id),
                lifecycle.decided_at.to_rfc3339(),
                operator_safe_or_dash(&lifecycle.reason)
            );
            if !lifecycle.stale_evidence_refs.is_empty() {
                println!(
                    "    stale evidence: {}",
                    operator_safe_list(&lifecycle.stale_evidence_refs)
                );
            }
        }
        if let Some(command) = proposal.suggested_command.as_deref() {
            println!("    command: {}", operator_safe_text(command));
        }
        println!(
            "    evidence: {}",
            operator_safe_list(&proposal.evidence_refs)
        );
    }
    Ok(())
}

fn operator_safe_or_dash(value: &str) -> String {
    if value.is_empty() {
        "-".to_string()
    } else {
        operator_safe_text(value)
    }
}

fn operator_safe_list(values: &[String]) -> String {
    values
        .iter()
        .map(|value| operator_safe_text(value))
        .collect::<Vec<_>>()
        .join(", ")
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
