//! Offdesk approval-resolution presentation.
//!
//! Command handlers retain approval selection, ledger mutation, denial-learning
//! candidate creation, and audit persistence. This module only renders an
//! already resolved approval or the read-only empty/not-found result.

use anyhow::{bail, Result};

use super::operator_safe_json_value;
use crate::offdesk::{operator_safe_text, ApprovalStatus, PendingActionApproval};

pub(super) fn present_approval_resolution(
    approval: &PendingActionApproval,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(approval)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    let verb = match approval.status {
        ApprovalStatus::Approved => "Approved",
        ApprovalStatus::Denied => "Denied",
        status => bail!("cannot present unresolved offdesk approval status: {status:?}"),
    };
    println!(
        "{} offdesk approval {}: {} ({:?})",
        verb,
        operator_safe_text(&approval.approval_id),
        operator_safe_text(&approval.action),
        approval.risk_level
    );
    Ok(())
}

pub(super) fn present_missing_approval_resolution(approval_id: Option<&str>) -> Result<()> {
    if let Some(approval_id) = approval_id {
        bail!(
            "Pending offdesk approval not found: {}",
            operator_safe_text(approval_id)
        );
    }
    println!("No pending offdesk approvals.");
    Ok(())
}
