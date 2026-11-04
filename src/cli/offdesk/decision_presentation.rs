//! Offdesk decision list and detail presentation.
//!
//! Command handlers retain ledger reads, filtering, resolution, receipt
//! construction, and append authority. This module only renders completed
//! decision records through the final operator-safe boundary.

use anyhow::Result;

use super::operator_safe_json_value;
use crate::offdesk::{operator_safe_text, DecisionRecord, DecisionRecordView};

pub(super) fn present_decisions(records: Vec<DecisionRecord>, json: bool) -> Result<()> {
    if json {
        let views = records
            .into_iter()
            .map(DecisionRecordView::from)
            .collect::<Vec<_>>();
        present_operator_safe_json(&views)?;
        return Ok(());
    }

    if records.is_empty() {
        println!("No offdesk decisions found.");
        return Ok(());
    }

    println!(
        "{:<28} {:<16} {:<10} {:<18} {:<10} {:<18} SUBJECT",
        "DECISION", "STATUS", "MATERIAL", "EVAL", "TARGET", "TASK"
    );
    for record in &records {
        let evaluator = record
            .judgment_route
            .as_ref()
            .map(|route| route.evaluator.as_str())
            .unwrap_or("-");
        let target = record
            .route
            .as_ref()
            .map(|route| route.target.as_str())
            .unwrap_or("-");
        let subject = record
            .approval_brief
            .as_ref()
            .map(|brief| brief.subject.as_str())
            .unwrap_or(record.decision_request.kind.as_str());
        println!(
            "{:<28} {:<16} {:<10} {:<18} {:<10} {:<18} {}",
            operator_safe_text(&record.decision_id),
            record.status.as_str(),
            record.materiality.as_str(),
            evaluator,
            target,
            operator_safe_text(&record.task_id),
            operator_safe_text(subject)
        );
        if !record.decision_request.summary.trim().is_empty() {
            println!(
                "  summary: {}",
                operator_safe_text(&record.decision_request.summary)
            );
        }
        if let Some(judgment) = record.judgment_route.as_ref() {
            println!(
                "  judgment: {} ({})",
                judgment.evaluator.as_str(),
                operator_safe_text(&judgment.reason)
            );
        }
        if let Some(route) = record.route.as_ref() {
            println!(
                "  route:   {} ({})",
                route.target.as_str(),
                operator_safe_text(&route.reason)
            );
        }
        let issue_count = record.validation_issues().len();
        if issue_count > 0 {
            println!("  validation_issues: {issue_count}");
        }
    }
    Ok(())
}

pub(super) fn present_decision(record: DecisionRecord, json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(&DecisionRecordView::from(record))?;
        return Ok(());
    }

    println!("decision: {}", operator_safe_text(&record.decision_id));
    println!("status:   {}", record.status.as_str());
    println!("material: {}", record.materiality.as_str());
    println!("project:  {}", operator_safe_text(&record.project_key));
    println!("request:  {}", operator_safe_text(&record.request_id));
    println!("task:     {}", operator_safe_text(&record.task_id));
    println!("raised:   {}", record.raised_by.as_str());
    println!("source:   {}", operator_safe_text(&record.source_surface));
    println!("updated:  {}", record.updated_at);
    println!();
    println!("Decision request:");
    println!(
        "  kind:     {}",
        operator_safe_text(&record.decision_request.kind)
    );
    println!(
        "  summary:  {}",
        operator_safe_text(&record.decision_request.summary)
    );
    println!(
        "  needed:   {}",
        operator_safe_text(&record.decision_request.decision_needed)
    );
    println!(
        "  scope:    {}",
        operator_safe_text(&record.decision_request.current_scope)
    );
    if !record.decision_request.non_authorized_scope.is_empty() {
        let scopes = record
            .decision_request
            .non_authorized_scope
            .iter()
            .map(|scope| operator_safe_text(scope))
            .collect::<Vec<_>>();
        println!("  not authorized: {}", scopes.join(", "));
    }
    if let Some(council) = record.council_review.as_ref() {
        println!();
        println!("Council:");
        println!(
            "  recommendation: {}",
            operator_safe_text(&council.recommendation)
        );
        if let Some(agreement) = council.agreement {
            println!("  agreement:      {agreement}");
        }
        if !council.reviewer_decisions.is_empty() {
            println!("  reviewers:");
            for (reviewer, decision) in &council.reviewer_decisions {
                println!(
                    "    - {}: {}",
                    operator_safe_text(reviewer),
                    operator_safe_text(decision)
                );
            }
        }
    }
    if let Some(judgment) = record.judgment_route.as_ref() {
        println!();
        println!("Judgment route:");
        println!("  evaluator: {}", judgment.evaluator.as_str());
        println!("  reason:    {}", operator_safe_text(&judgment.reason));
        println!(
            "  selected:  {} by {}",
            judgment.selected_at,
            operator_safe_text(&judgment.selected_by)
        );
        if let Some(default) = judgment.default_if_no_reply.as_deref() {
            println!("  default:   {}", operator_safe_text(default));
        }
        if !judgment.policy_basis.is_empty() {
            println!("  policy:");
            for basis in &judgment.policy_basis {
                println!("    - {}", operator_safe_text(basis));
            }
        }
    }
    if let Some(route) = record.route.as_ref() {
        println!();
        println!("Delivery route:");
        println!("  target:  {}", route.target.as_str());
        println!("  reason:  {}", operator_safe_text(&route.reason));
        if let Some(default) = route.default_if_no_reply.as_deref() {
            println!("  default: {}", operator_safe_text(default));
        }
    }
    if let Some(brief) = record.approval_brief.as_ref() {
        println!();
        println!("Approval brief:");
        println!(
            "  recommendation: {}",
            operator_safe_text(&brief.recommendation)
        );
        println!("  subject:        {}", operator_safe_text(&brief.subject));
        println!("  question:       {}", operator_safe_text(&brief.question));
        println!("  scope:          {}", operator_safe_text(&brief.scope));
    }
    if let Some(handoff) = record.execution_handoff.as_ref() {
        println!();
        println!("Execution handoff:");
        println!("  handoff_id: {}", operator_safe_text(&handoff.handoff_id));
        println!("  target:     {}", operator_safe_text(&handoff.target));
        println!(
            "  direction:  {}",
            operator_safe_text(&handoff.approved_direction)
        );
        println!(
            "  scope:      {}",
            operator_safe_text(&handoff.approved_scope)
        );
    }
    if let Some(receipt) = record.decision_receipt.as_ref() {
        println!();
        println!("Decision receipt:");
        println!("  receipt_id: {}", operator_safe_text(&receipt.receipt_id));
        println!(
            "  decision:   {}",
            operator_safe_text(&receipt.final_decision)
        );
        println!(
            "  resolved:   {} by {}",
            receipt.resolved_at,
            operator_safe_text(&receipt.resolved_by)
        );
        println!(
            "  result:     {}",
            operator_safe_text(&receipt.result_status)
        );
    }
    let validation_issues = record.validation_issues();
    if !validation_issues.is_empty() {
        println!();
        println!("Validation issues:");
        for issue in validation_issues {
            println!(
                "  - {:?}: {} ({})",
                issue.severity,
                operator_safe_text(&issue.code),
                operator_safe_text(&issue.detail)
            );
        }
    }
    Ok(())
}

fn present_operator_safe_json(value: &impl serde::Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
