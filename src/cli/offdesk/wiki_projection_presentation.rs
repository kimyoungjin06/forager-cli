//! Adaptive-wiki projection presentation.
//!
//! Command handlers retain query, budget, and policy construction, store access,
//! and runtime-policy acknowledgement authority. This module only renders
//! read-only projection results.

use anyhow::Result;
use serde::Serialize;

use super::{adaptive_wiki_agent_modes_label, operator_safe_json_value};
use crate::offdesk::{
    operator_safe_text, AdaptiveWikiProjectionComparisonReport, AdaptiveWikiProjectionReport,
};

pub(super) fn present_wiki_projection(
    report: &AdaptiveWikiProjectionReport,
    include_report: bool,
    json: bool,
) -> Result<()> {
    if json {
        if include_report {
            present_operator_safe_json(report)?;
        } else {
            present_operator_safe_json(&report.selected)?;
        }
        return Ok(());
    }

    if include_report {
        print_wiki_projection_report(report);
        return Ok(());
    }

    if report.selected.is_empty() {
        println!("No adaptive wiki projection entries found.");
        return Ok(());
    }

    println!(
        "{:<44} {:<16} {:<16} {:<18} INSTRUCTION",
        "ID", "SCOPE", "ACTIVATION", "AGENT_MODES"
    );
    for entry in &report.selected {
        println!(
            "{:<44} {:<16} {:<16} {:<18} {}",
            operator_safe_text(&entry.id),
            format!("{:?}", entry.scope).to_lowercase(),
            format!("{:?}", entry.activation_mode).to_lowercase(),
            adaptive_wiki_agent_modes_label(&entry.agent_modes),
            operator_safe_text(&entry.instruction)
        );
    }
    Ok(())
}

pub(super) fn present_wiki_projection_comparison(
    report: &AdaptiveWikiProjectionComparisonReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!("Adaptive wiki projection review-expired policy comparison");
    println!(
        "  budget: entries={} context_chars={} instruction_chars={}",
        report.budget.max_entries,
        report.budget.max_context_chars,
        report.budget.max_instruction_chars
    );
    println!(
        "  warn:   selected={} rejected={} review_expired_projected={} context_chars={}",
        report.summary.warn_selected,
        report.summary.warn_rejected,
        report.warn.summary.review_expired_projected,
        report.summary.warn_estimated_context_chars
    );
    println!(
        "  strict: selected={} rejected={} review_expired_projected={} context_chars={}",
        report.summary.strict_selected,
        report.summary.strict_rejected,
        report.strict.summary.review_expired_projected,
        report.summary.strict_estimated_context_chars
    );
    if !report.summary.selected_only_in_warn.is_empty() {
        println!(
            "  selected only in warn: {}",
            operator_safe_list(&report.summary.selected_only_in_warn)
        );
    }
    if !report.summary.selected_only_in_strict.is_empty() {
        println!(
            "  selected only in strict: {}",
            operator_safe_list(&report.summary.selected_only_in_strict)
        );
    }
    if !report.summary.review_expired_excluded.is_empty() {
        println!(
            "  review_expired_excluded: {}",
            operator_safe_list(&report.summary.review_expired_excluded)
        );
    }
    Ok(())
}

fn print_wiki_projection_report(report: &AdaptiveWikiProjectionReport) {
    println!(
        "Adaptive wiki projection: {} selected, {} rejected, {} conflicts, {} review-expired ({} matching promoted entries)",
        report.summary.selected,
        report.summary.rejected,
        report.summary.conflicts,
        report.summary.review_expired_projected,
        report.summary.promoted_scope_matches
    );
    println!(
        "  budget: entries={} context_chars={} instruction_chars={}",
        report.budget.max_entries,
        report.budget.max_context_chars,
        report.budget.max_instruction_chars
    );
    println!(
        "  policy: review_expired={:?}",
        report.policy.review_expired
    );
    println!(
        "  estimated_context_chars: {}",
        report.summary.estimated_context_chars
    );
    if report.summary.instructions_truncated > 0 {
        println!(
            "  instructions_truncated: {}",
            report.summary.instructions_truncated
        );
    }
    if !report.selected.is_empty() {
        println!("  selected:");
        for entry in &report.selected {
            println!(
                "    {} {:?} {:?}:{} {:?} agent_modes={} evidence={}",
                operator_safe_text(&entry.id),
                entry.kind,
                entry.scope,
                operator_safe_text(&entry.scope_ref),
                entry.confidence,
                adaptive_wiki_agent_modes_label(&entry.agent_modes),
                entry.evidence_count
            );
        }
    }
    if !report.rejected.is_empty() {
        println!("  rejected:");
        for rejection in &report.rejected {
            println!(
                "    {} {:?}: {}",
                operator_safe_text(&rejection.entry_id),
                rejection.reason,
                operator_safe_text(&rejection.detail)
            );
        }
    }
    if !report.conflicts.is_empty() {
        println!("  conflicts:");
        for conflict in &report.conflicts {
            println!(
                "    {} <-> {} {}: {}",
                operator_safe_text(&conflict.entry_id),
                operator_safe_text(&conflict.conflicting_entry_id),
                operator_safe_text(&conflict.signature),
                operator_safe_text(&conflict.detail)
            );
        }
    }
    if !report.review_expired.is_empty() {
        println!("  review_expired:");
        for warning in &report.review_expired {
            println!(
                "    {} {:?}: review_after={} {}",
                operator_safe_text(&warning.entry_id),
                warning.scope,
                warning.review_after,
                operator_safe_text(&warning.detail)
            );
        }
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
