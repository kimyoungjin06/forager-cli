//! Offdesk task and task-lifecycle presentation.
//!
//! Command handlers and stores retain task construction, enqueue, mutation,
//! resume-artifact updates, denied-approval supersession, list filtering, and
//! latest selection. This module only renders operator task views and completed
//! lifecycle reports.

use anyhow::Result;
use serde::Serialize;

use super::{adaptive_wiki_agent_mode_cli_value, operator_safe_json_value};
use crate::offdesk::{
    operator_safe_text, OffdeskModeAssessment, OffdeskNextSafeAction, OffdeskTask,
    OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskTerminationReport, OffdeskTaskView,
};

#[derive(Serialize)]
struct RetryTaskLifecycleReport<'a> {
    #[serde(flatten)]
    report: &'a OffdeskTaskLifecycleReport,
    superseded_denied_approvals: usize,
}

pub(super) fn present_enqueued_task(task: &OffdeskTask, json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(&task.operator_view())?;
        return Ok(());
    }

    println!(
        "Enqueued offdesk task {}",
        operator_safe_text(&task.task_id)
    );
    println!("  capability: {}", operator_safe_text(&task.capability_id));
    println!("  runner:     {:?}", task.runner_kind);
    if let Some(packet) = task.implementation_packet.as_ref() {
        println!(
            "  packet:     {} ({})",
            operator_safe_text(&packet.packet_id),
            operator_safe_text(&packet.outcome)
        );
    }
    Ok(())
}

pub(super) fn present_tasks(tasks: &[OffdeskTaskView], json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(tasks)?;
        return Ok(());
    }

    if tasks.is_empty() {
        println!("No offdesk tasks found.");
        return Ok(());
    }

    let open = tasks
        .iter()
        .filter(|task| !is_terminal_task_status(task.status))
        .collect::<Vec<_>>();
    let terminal = tasks
        .iter()
        .filter(|task| is_terminal_task_status(task.status))
        .collect::<Vec<_>>();

    if !open.is_empty() {
        println!("Open tasks:");
        present_task_rows(&open);
    }
    if !terminal.is_empty() {
        if !open.is_empty() {
            println!();
        }
        println!("Terminal tasks:");
        present_task_rows(&terminal);
    }
    Ok(())
}

pub(super) fn present_task_lifecycle_report(
    report: &OffdeskTaskLifecycleReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!(
        "{} offdesk task {}: {} -> {} ({})",
        if report.changed {
            "Updated"
        } else {
            "Unchanged"
        },
        operator_safe_text(&report.task.task_id),
        task_status_label(report.previous_status),
        task_status_label(report.status),
        operator_safe_text(&report.message)
    );
    if let Some(ticket_id) = report.task.background_ticket_id.as_deref() {
        println!("  ticket: {}", operator_safe_text(ticket_id));
    }
    if !report.task.reason.trim().is_empty() {
        println!("  reason: {}", operator_safe_text(&report.task.reason));
    }
    if let Some(error) = report.task.last_error.as_deref() {
        println!("  error:  {}", operator_safe_text(error));
    }
    Ok(())
}

pub(super) fn present_retry_task_lifecycle_report(
    report: &OffdeskTaskLifecycleReport,
    superseded_denied_approvals: usize,
    json: bool,
    include_denied_reset: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(&RetryTaskLifecycleReport {
            report,
            superseded_denied_approvals,
        })?;
        return Ok(());
    }

    present_task_lifecycle_report(report, false)?;
    if include_denied_reset {
        println!(
            "  superseded denied approvals: {}",
            superseded_denied_approvals
        );
    }
    Ok(())
}

pub(super) fn present_task_termination_report(
    report: &OffdeskTaskTerminationReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!(
        "{} offdesk task {} runtime: {}",
        if report.background.changed {
            "Terminated"
        } else {
            "Recorded"
        },
        operator_safe_text(&report.task_id),
        operator_safe_text(&report.background.evidence)
    );
    println!(
        "  task: {} -> {}",
        task_status_label(report.task.previous_status),
        task_status_label(report.task.status)
    );
    println!(
        "  ticket: {} ({:?})",
        operator_safe_text(&report.background.ticket_id),
        report.background.runner_kind
    );
    Ok(())
}

fn task_status_label(status: OffdeskTaskStatus) -> &'static str {
    match status {
        OffdeskTaskStatus::Queued => "queued",
        OffdeskTaskStatus::PendingApproval => "pending-approval",
        OffdeskTaskStatus::Launched => "launched",
        OffdeskTaskStatus::Running => "running",
        OffdeskTaskStatus::Completed => "completed",
        OffdeskTaskStatus::Failed => "failed",
        OffdeskTaskStatus::ResumePending => "resume-pending",
        OffdeskTaskStatus::Cancelled => "cancelled",
    }
}

fn present_task_rows(tasks: &[&OffdeskTaskView]) {
    println!(
        "{:<24} {:<18} {:<18} {:<14} TICKET",
        "TASK", "STATUS", "CAPABILITY", "RUNNER"
    );
    for task in tasks {
        println!(
            "{:<24} {:<18} {:<18} {:<14} {}",
            operator_safe_text(&task.task_id),
            task_status_label(task.status),
            operator_safe_text(&task.capability_id),
            format!("{:?}", task.runner_kind).to_lowercase(),
            task.background_ticket_id
                .as_deref()
                .map(operator_safe_text)
                .unwrap_or_else(|| "-".to_string())
        );
        if !task.preview.trim().is_empty() {
            println!("  preview: {}", operator_safe_text(&task.preview));
        }
        if let Some(last_error) = task.last_error.as_deref() {
            println!("  error:   {}", operator_safe_text(last_error));
        }
        if !task.last_adaptive_wiki_entry_ids.is_empty() {
            let entry_ids = task
                .last_adaptive_wiki_entry_ids
                .iter()
                .map(|entry_id| operator_safe_text(entry_id))
                .collect::<Vec<_>>();
            println!("  adaptive_wiki: {}", entry_ids.join(", "));
        }
        if let Some(agent_mode) = task.agent_mode {
            println!(
                "  agent_mode: {}",
                adaptive_wiki_agent_mode_cli_value(agent_mode)
            );
        }
        present_mode_assessment(&task.mode_assessment);
        if task.provider_id.is_some() || task.model.is_some() {
            println!(
                "  provider: {} model: {}",
                task.provider_id
                    .as_deref()
                    .map(operator_safe_text)
                    .unwrap_or_else(|| "-".to_string()),
                task.model
                    .as_deref()
                    .map(operator_safe_text)
                    .unwrap_or_else(|| "-".to_string())
            );
        }
        if let Some(artifact_kind) = task.artifact_kind.as_deref() {
            println!("  artifact_kind: {}", operator_safe_text(artifact_kind));
        }
        if let Some(fallback) = task.last_provider_fallback.as_ref() {
            let recommended = fallback
                .candidates
                .iter()
                .filter(|candidate| candidate.recommended)
                .count();
            println!(
                "  fallback: {} candidates, {} recommended",
                fallback.candidates.len(),
                recommended
            );
            for candidate in fallback
                .candidates
                .iter()
                .filter(|candidate| candidate.recommended)
                .take(3)
            {
                println!(
                    "    - {} {} ({:?})",
                    operator_safe_text(&candidate.provider_id),
                    candidate
                        .model
                        .as_deref()
                        .map(operator_safe_text)
                        .unwrap_or_else(|| "-".to_string()),
                    candidate.source
                );
            }
        }
        if let Some(not_before) = task.not_before {
            println!("  not_before: {not_before}");
        }
        if let Some(last_gate_status) = task.last_gate_status {
            println!(
                "  gate:    {}",
                format!("{:?}", last_gate_status).to_lowercase()
            );
        }
        present_next_safe_action(&task.next_safe_action);
    }
}

pub(super) fn present_next_safe_actions(actions: &[OffdeskNextSafeAction]) {
    if actions.is_empty() {
        return;
    }
    println!("Next safe actions:");
    for action in actions {
        present_next_safe_action(action);
    }
}

fn present_next_safe_action(action: &OffdeskNextSafeAction) {
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

fn present_mode_assessment(assessment: &OffdeskModeAssessment) {
    println!(
        "  mode_verdict: {} risk: {}",
        assessment.mode_verdict.label(),
        assessment.mode_risk.label()
    );
    println!(
        "  mode_risk_detail: {}",
        operator_safe_text(&assessment.mode_risk_detail)
    );
    if assessment.review_stage_required {
        println!("  review_stage_required: true");
    }
}

fn is_terminal_task_status(status: OffdeskTaskStatus) -> bool {
    matches!(
        status,
        OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled
    )
}

fn present_operator_safe_json(value: &(impl Serialize + ?Sized)) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
