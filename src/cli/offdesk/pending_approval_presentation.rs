//! Offdesk pending-approval presentation.
//!
//! Command handlers retain approval expiration and ledger reads. This module
//! only renders completed operator views and applies the final redaction
//! boundary to legacy approval content.

use anyhow::Result;

use super::operator_safe_json_value;
use crate::offdesk::{operator_safe_text, OffdeskNextSafeAction, OffdeskPendingApprovalView};

pub(super) fn present_pending_approval_views(
    approvals: &[OffdeskPendingApprovalView],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(approvals)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if approvals.is_empty() {
        println!("No offdesk approvals found.");
        return Ok(());
    }

    println!(
        "{:<44} {:<44} {:<10} {:<18} {:<24} ACTION",
        "APPROVAL ID", "ACTION ID", "STATUS", "RISK", "TASK"
    );
    for approval_view in approvals {
        let approval = &approval_view.approval;
        println!(
            "{:<44} {:<44} {:<10} {:<18} {:<24} {}",
            operator_safe_text(&approval.approval_id),
            operator_safe_text(approval.action_id()),
            format!("{:?}", approval.status).to_lowercase(),
            format!("{:?}", approval.risk_level).to_lowercase(),
            operator_safe_text(&approval.task_id),
            operator_safe_text(&approval.action)
        );
        if !approval.preview.trim().is_empty() {
            println!("  preview: {}", operator_safe_text(&approval.preview));
        }
        if !approval.reason.trim().is_empty() {
            println!("  reason:  {}", operator_safe_text(&approval.reason));
        }
        if let Some(brief) = approval
            .metadata
            .as_ref()
            .and_then(crate::offdesk::ActionApprovalMetadata::approval_brief)
        {
            println!(
                "  prompt: {} recommendation for {}",
                operator_safe_text(&brief.recommendation),
                operator_safe_text(&brief.subject)
            );
            for line in brief.summary_lines.iter().take(3) {
                println!("    {}", operator_safe_text(line));
            }
            println!("  question: {}", operator_safe_text(&brief.question));
            println!("  scope: {}", operator_safe_text(&brief.scope));
        }
        if let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(crate::offdesk::ActionApprovalMetadata::as_provider_fallback)
        {
            println!(
                "  fallback target: {} model {} ({})",
                operator_safe_text(&metadata.current_provider_id),
                metadata
                    .current_model
                    .as_deref()
                    .map(operator_safe_text)
                    .unwrap_or_else(|| "-".to_string()),
                format!("{:?}", metadata.apply_scope).to_lowercase()
            );
            for candidate in metadata.candidates.iter().take(metadata.candidate_limit) {
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
        if let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(crate::offdesk::ActionApprovalMetadata::as_artifact_retention)
        {
            println!(
                "  artifact: {} [{} / {}]",
                operator_safe_text(&metadata.label),
                operator_safe_text(&metadata.retention_class),
                operator_safe_text(&metadata.review_status)
            );
            println!(
                "  requested: {} recommended: {}",
                operator_safe_text(&metadata.requested_action),
                operator_safe_text(&metadata.recommended_action)
            );
        }
        if let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(crate::offdesk::ActionApprovalMetadata::as_retention_v2)
        {
            println!(
                "  retention target: {} [{:?}]",
                operator_safe_text(&metadata.proposal.target.retention_target_id),
                metadata.proposal.target.source_kind
            );
            println!("  requested: {:?}", metadata.proposal.requested_action);
        }
        present_next_safe_action(&approval_view.next_safe_action);
    }
    Ok(())
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
