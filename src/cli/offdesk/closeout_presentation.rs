//! Offdesk closeout report presentation.
//!
//! Closeout assembly and exact local artifact writes remain in the closeout
//! report builder. This module only renders the completed report through the
//! final operator-safe boundary.

use anyhow::Result;

use super::{operator_safe_json_value, OffdeskCloseoutReport};
use crate::offdesk::{operator_safe_text, CloseoutReviewRecord};

pub(super) fn present_closeout_report(report: &OffdeskCloseoutReport, json: bool) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!("Offdesk closeout plan");
    println!("  generated_at: {}", report.generated_at);
    println!(
        "  closeout_id:  {}",
        operator_safe_text(&report.closeout_id)
    );
    println!("  profile:      {}", operator_safe_text(&report.profile));
    println!(
        "  artifact_dir: {}",
        operator_safe_text(&report.artifact_dir)
    );
    println!(
        "  tasks:        scanned={} completed={} active_or_blocked={}",
        report.summary.tasks_scanned,
        report.summary.completed_tasks,
        report.summary.active_or_blocked_tasks
    );
    println!(
        "  operations:   keep={} archive={} delete={} review_required={}",
        report.summary.keep_operations,
        report.summary.archive_candidates,
        report.summary.delete_candidates,
        report.summary.operations_requiring_commercial_review
    );
    if report.summary.implementation_packets_scanned > 0 {
        println!(
            "  packets:      scanned={} completed={} deferred={} missing={} drifted={}",
            report.summary.implementation_packets_scanned,
            report.summary.packet_goals_completed,
            report.summary.packet_goals_deferred,
            report.summary.packet_goals_missing,
            report.summary.packet_goals_drifted
        );
        if report.summary.packet_detail_items > 0 {
            println!(
                "  packet items: completed={} deferred={} missing={} drifted={} total={}",
                report.summary.packet_detail_items_completed,
                report.summary.packet_detail_items_deferred,
                report.summary.packet_detail_items_missing,
                report.summary.packet_detail_items_drifted,
                report.summary.packet_detail_items
            );
        }
    }
    println!("  dry_run:      true (no project files moved or deleted)");
    println!("Artifacts:");
    println!(
        "  plan:         {}",
        operator_safe_text(&report.artifacts.closeout_plan_json)
    );
    println!(
        "  markdown:     {}",
        operator_safe_text(&report.artifacts.closeout_plan_markdown)
    );
    println!(
        "  review:       {}",
        operator_safe_text(&report.artifacts.commercial_review_packet)
    );
    println!(
        "  return:       {}",
        operator_safe_text(&report.artifacts.return_package_markdown)
    );
    if !report.open_decisions.is_empty() {
        println!("Open decisions:");
        for decision in &report.open_decisions {
            println!(
                "  - {}: {}",
                operator_safe_text(decision.kind),
                operator_safe_text(&decision.detail)
            );
        }
    }
    Ok(())
}

pub(super) fn present_closeout_review_record(
    record: &CloseoutReviewRecord,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(record)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!("Offdesk closeout review");
    println!("  reviewed_at:  {}", record.reviewed_at);
    println!("  review_id:    {}", operator_safe_text(&record.review_id));
    println!(
        "  closeout_id:  {}",
        operator_safe_text(&record.closeout_id)
    );
    println!(
        "  verdict:      {}",
        operator_safe_text(record.verdict.as_str())
    );
    println!(
        "  acceptance:   {}",
        operator_safe_text(record.closeout_receipt.acceptance_status)
    );
    println!("  reviewer:     {}", operator_safe_text(&record.reviewer));
    if let Some(provider) = record.review_provider.as_deref() {
        println!("  provider:     {}", operator_safe_text(provider));
    }
    if let Some(resolution) = record.decision_resolution.as_ref() {
        println!("  decision:     {}", operator_safe_text(&resolution.kind));
        println!(
            "  resolution:   {}",
            operator_safe_text(&resolution.decision)
        );
        println!("  reason:       {}", operator_safe_text(&resolution.reason));
    }
    println!("  project file mutations: none");
    println!("Artifacts:");
    println!(
        "  plan:         {}",
        operator_safe_text(&record.artifacts.closeout_plan_json)
    );
    println!(
        "  review:       {}",
        operator_safe_text(&record.artifacts.review_record_json)
    );
    println!(
        "  receipt:      {}",
        operator_safe_text(&record.artifacts.closeout_receipt_json)
    );
    println!(
        "  return:       {}",
        operator_safe_text(&record.artifacts.return_package_markdown)
    );
    if !record.unsafe_operations.is_empty() {
        println!("Unsafe operations:");
        for operation in &record.unsafe_operations {
            println!("  - {}", operator_safe_text(operation));
        }
    }
    if !record.missing_evidence.is_empty() {
        println!("Missing evidence:");
        for evidence in &record.missing_evidence {
            println!("  - {}", operator_safe_text(evidence));
        }
    }
    Ok(())
}
