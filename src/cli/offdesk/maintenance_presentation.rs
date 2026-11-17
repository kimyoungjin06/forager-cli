//! Offdesk maintenance report and approval-request presentation.
//!
//! Command handlers retain store reads, aggregation, approval creation,
//! deduplication, and persistence. This module only renders completed reports
//! through the final operator-safe boundary.

use anyhow::Result;
use serde::Serialize;
use std::collections::BTreeMap;

use super::{
    enum_label, operator_safe_json_value, MaintenanceApprovalRequestReport,
    OffdeskMaintenanceReport,
};
use crate::offdesk::{operator_safe_text, OffdeskNextSafeAction};

pub(super) fn present_maintenance_report(
    report: &OffdeskMaintenanceReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!("Offdesk maintenance report");
    println!("  generated_at:       {}", report.generated_at);
    println!(
        "  profile:            {}",
        operator_safe_text(&report.profile)
    );
    println!(
        "  profile_dir:        {}",
        operator_safe_text(&report.profile_dir)
    );
    println!("  read_only:          {}", report.read_only);
    println!(
        "  tasks:              total={} status=[{}] risk=[{}]",
        report.tasks.total,
        format_counts(&report.tasks.by_status),
        format_counts(&report.tasks.mode.by_risk)
    );
    println!(
        "  task_modes:         agent=[{}] review_required={}",
        format_counts(&report.tasks.by_agent_mode),
        report.tasks.mode.review_stage_required
    );
    println!(
        "  background_runs:    total={} phase=[{}] risk=[{}]",
        report.background_runs.total,
        format_counts(&report.background_runs.by_phase),
        format_counts(&report.background_runs.mode.by_risk)
    );
    println!(
        "  approvals:          total={} pending={} status=[{}]",
        report.approvals.total,
        report.approvals.pending,
        format_counts(&report.approvals.by_status)
    );
    println!(
        "  resume_states:      total={} status=[{}]",
        report.resume_states.total,
        format_counts(&report.resume_states.by_status)
    );
    println!(
        "  provider_capacity:  total={} attention={} status=[{}]",
        report.provider_capacity.total,
        report.provider_capacity.attention,
        format_counts(&report.provider_capacity.by_status)
    );
    println!(
        "  wiki_ack_attention: expired={} near_expiry={} suggested_actions={}",
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .expired,
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .near_expiry,
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .suggested_actions
    );
    println!(
        "  wiki_review_after:  expired={} near_expiry={} missing_review_after={}",
        report.adaptive_wiki_review_after_attention_summary.expired,
        report
            .adaptive_wiki_review_after_attention_summary
            .near_expiry,
        report
            .adaptive_wiki_review_after_attention_summary
            .missing_review_after
    );
    if report.recommended_actions.is_empty() {
        println!("No maintenance actions recommended.");
    } else {
        println!("Recommended actions:");
        for action in &report.recommended_actions {
            println!(
                "  - {}: {}",
                operator_safe_text(action.kind),
                operator_safe_text(&action.detail)
            );
            println!("    command: {}", operator_safe_text(action.command));
        }
    }
    present_next_safe_actions(&report.next_safe_actions);
    Ok(())
}

pub(super) fn present_maintenance_request(
    report: &MaintenanceApprovalRequestReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!("Offdesk maintenance approval request");
    println!("  generated_at: {}", report.generated_at);
    println!("  action:       {}", operator_safe_text(&report.action));
    println!(
        "  kind:         {}",
        operator_safe_text(report.action_kind.cli_value())
    );
    println!("  risk:         {}", enum_label(report.risk_level));
    println!("  status:       {}", operator_safe_text(&report.status));
    println!("  detail:       {}", operator_safe_text(&report.detail));
    println!(
        "  project_key:  {}",
        operator_safe_text(&report.project_key)
    );
    println!("  request_id:   {}", operator_safe_text(&report.request_id));
    println!("  task_id:      {}", operator_safe_text(&report.task_id));
    if let Some(target_id) = &report.target_id {
        println!("  target_id:    {}", operator_safe_text(target_id));
    }
    if let Some(approval) = &report.approval {
        if let Some(approval_id) = approval["approval_id"].as_str() {
            println!("  approval_id:  {}", operator_safe_text(approval_id));
        }
    }
    if !report.next_commands.is_empty() {
        println!("Next commands:");
        for command in &report.next_commands {
            println!("  - {}", operator_safe_text(command));
        }
    }
    Ok(())
}

fn present_next_safe_actions(actions: &[OffdeskNextSafeAction]) {
    if actions.is_empty() {
        return;
    }
    println!("Next safe actions:");
    for action in actions {
        println!("  next:    {}", operator_safe_text(&action.detail));
        if !action.commands.is_empty() {
            let commands = action
                .commands
                .iter()
                .map(|command| operator_safe_text(command))
                .collect::<Vec<_>>();
            println!("  command: {}", commands.join(" | "));
        }
        if action.requires_operator_review {
            println!("  review:  operator review required");
        }
    }
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn format_counts(counts: &BTreeMap<String, usize>) -> String {
    if counts.is_empty() {
        return "-".to_string();
    }
    counts
        .iter()
        .map(|(key, value)| format!("{}={value}", operator_safe_text(key)))
        .collect::<Vec<_>>()
        .join(",")
}
