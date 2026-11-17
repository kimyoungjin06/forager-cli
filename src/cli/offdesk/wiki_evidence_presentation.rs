//! Adaptive-wiki episode and evidence report presentation.
//!
//! Command handlers retain query and filter construction, profile and store
//! selection, report generation, and report-file writes. This module only
//! renders completed reports for JSON and terminal consumers.

use anyhow::Result;
use serde::Serialize;

use super::{operator_safe_json_value, wiki_scope_label};
use crate::offdesk::{
    operator_safe_text, AdaptiveWikiCorrectionRecurrenceReport,
    AdaptiveWikiEpisodeEvaluationReport, AdaptiveWikiLiveEpisodeTraceReport,
    AdaptiveWikiPromotionEvidenceChainReport,
};

pub(super) fn present_wiki_episode_evaluation_report(
    report: &AdaptiveWikiEpisodeEvaluationReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    let status = if report.passed { "passed" } else { "failed" };
    println!(
        "Adaptive wiki episode evaluation {action} at {} ({status})",
        operator_safe_text(&report.report_dir)
    );
    println!("  target: {}", operator_safe_text(&report.target_entry_id));
    println!(
        "  in-scope: {} entries  out-of-scope: {} entries",
        report.summary.in_scope_projection_count, report.summary.out_of_scope_projection_count
    );
    println!(
        "  checks: target_in_scope={} target_out_of_scope={} scope_leakage={} review_expired_projected={} deprecated_projected={} projected_without_evidence={}",
        report.summary.target_entry_in_scope,
        report.summary.target_entry_out_of_scope,
        report.summary.scope_leakage_count,
        report.summary.review_expired_entry_projected,
        report.summary.deprecated_entry_projected,
        report.summary.projected_without_evidence
    );
    if report.failures.is_empty() {
        println!("  failures: none");
    } else {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {}", operator_safe_text(failure));
        }
    }
    if !report.in_scope_projection.is_empty() {
        println!(
            "  in-scope ids: {}",
            operator_safe_ids(
                report
                    .in_scope_projection
                    .iter()
                    .map(|entry| entry.id.as_str()),
                ", ",
            )
        );
    }
    if !report.out_of_scope_projection.is_empty() {
        println!(
            "  out-of-scope ids: {}",
            operator_safe_ids(
                report
                    .out_of_scope_projection
                    .iter()
                    .map(|entry| entry.id.as_str()),
                ", ",
            )
        );
    }
    Ok(())
}

pub(super) fn present_wiki_live_episode_trace_report(
    report: &AdaptiveWikiLiveEpisodeTraceReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki live episode trace {action} {} events at {}",
        report.summary.events,
        operator_safe_text(&report.report_dir)
    );
    println!(
        "  tasks: {}  runtime usage: {}  projections: {}  candidates: {}  corrections: {}",
        report.summary.task_events,
        report.summary.runtime_usage_events,
        report.summary.projection_events,
        report.summary.candidate_events,
        report.summary.correction_events
    );
    println!(
        "  promotions: {}  completed: {}  failed: {}  resume pending: {}  rollbacks: {}",
        report.summary.promotion_events,
        report.summary.completion_events,
        report.summary.failure_events,
        report.summary.resume_pending_events,
        report.summary.rollback_events
    );
    if report.summary.usage_without_task > 0 {
        println!(
            "  usage without matching task: {}",
            report.summary.usage_without_task
        );
    }
    for event in &report.events {
        println!(
            "  - {:?} {} task={} request={} entries={} {}",
            event.kind,
            event.occurred_at.to_rfc3339(),
            operator_safe_optional(event.task_id.as_deref()),
            operator_safe_optional(event.request_id.as_deref()),
            operator_safe_ids(event.entry_ids.iter().map(String::as_str), ","),
            operator_safe_text(&event.summary)
        );
    }
    Ok(())
}

pub(super) fn present_wiki_correction_recurrence_report(
    report: &AdaptiveWikiCorrectionRecurrenceReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki correction recurrence {action} at {} ({:?})",
        operator_safe_text(&report.report_dir),
        report.assessment
    );
    println!("  entry: {}", operator_safe_text(&report.entry_id));
    if let Some(scope) = &report.scope {
        println!(
            "  scope: {}",
            operator_safe_text(&wiki_scope_label(scope.scope, &scope.scope_ref))
        );
    }
    if let Some(promotion_at) = report.promotion_at {
        println!("  promotion: {}", promotion_at.to_rfc3339());
    }
    println!(
        "  corrections: pre={} post={} delta={}",
        report.summary.pre_promotion_correction_events,
        report.summary.post_promotion_correction_events,
        report.summary.recurrence_delta
    );
    println!(
        "  post usage={} failures={} counterexamples={} recurrence_per_1000={}",
        report.summary.post_promotion_usage_events,
        report.summary.post_promotion_failure_events,
        report.summary.post_promotion_counterexample_events,
        report.summary.post_promotion_recurrence_per_1000
    );
    if !report.failures.is_empty() {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {}", operator_safe_text(failure));
        }
    }
    Ok(())
}

pub(super) fn present_wiki_promotion_chain_report(
    report: &AdaptiveWikiPromotionEvidenceChainReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki promotion evidence chain {action} at {}",
        operator_safe_text(&report.report_dir)
    );
    println!("  entry: {}", operator_safe_text(&report.entry_id));
    println!(
        "  promotion audit={} candidate snapshot={} entry snapshot={} current entry={}",
        report.summary.promotion_audit_found,
        report.summary.candidate_snapshot_present,
        report.summary.entry_snapshot_present,
        report.summary.current_entry_present
    );
    println!(
        "  usage records={} related audits={} failures={}",
        report.summary.usage_records, report.summary.related_audit_records, report.summary.failures
    );
    if let Some(audit) = &report.promotion_audit {
        println!(
            "  promoted at {} candidate={} actor={}",
            audit.created_at.to_rfc3339(),
            operator_safe_optional(audit.candidate_id.as_deref()),
            operator_safe_text(&audit.actor)
        );
    }
    if !report.failures.is_empty() {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {}", operator_safe_text(failure));
        }
    }
    Ok(())
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn operator_safe_optional(value: Option<&str>) -> String {
    value
        .map(operator_safe_text)
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "-".to_string())
}

fn operator_safe_ids<'a>(values: impl Iterator<Item = &'a str>, separator: &str) -> String {
    values
        .map(operator_safe_text)
        .collect::<Vec<_>>()
        .join(separator)
}
