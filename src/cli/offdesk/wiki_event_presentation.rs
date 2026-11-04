//! Adaptive-wiki proposal lifecycle-event and correction-record presentation.
//!
//! Command handlers retain canonical reads, event filtering, event creation,
//! and append-only persistence. This module only renders completed records for
//! JSON and terminal consumers.

use anyhow::Result;

use super::operator_safe_json_value;
use crate::offdesk::{
    operator_safe_text, AdaptiveWikiCorrectionRecord, AdaptiveWikiReviewProposalEventRecord,
};

pub(super) fn present_wiki_proposal_events(
    events: &[AdaptiveWikiReviewProposalEventRecord],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(events)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if events.is_empty() {
        println!("No adaptive wiki proposal lifecycle events found.");
        return Ok(());
    }
    for event in events {
        println!(
            "{} {:?} proposal={} action={} subject={}:{} by={} {}",
            operator_safe_text(&event.id),
            event.decision,
            operator_safe_text(&event.proposal_id),
            event
                .proposal_action
                .map(|action| format!("{action:?}"))
                .unwrap_or_else(|| "-".to_string()),
            operator_safe_or_dash(Some(&event.subject_kind)),
            operator_safe_or_dash(Some(&event.subject_id)),
            operator_safe_or_dash(Some(&event.actor)),
            operator_safe_text(&event.reason)
        );
    }
    Ok(())
}

pub(super) fn present_wiki_proposal_event(
    event: &AdaptiveWikiReviewProposalEventRecord,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(event)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "Recorded adaptive wiki proposal event {} for {} ({:?})",
        operator_safe_text(&event.id),
        operator_safe_text(&event.proposal_id),
        event.decision
    );
    Ok(())
}

pub(super) fn present_wiki_corrections(
    corrections: &[AdaptiveWikiCorrectionRecord],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(corrections)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if corrections.is_empty() {
        println!("No adaptive wiki correction records found.");
        return Ok(());
    }
    for correction in corrections {
        println!(
            "{} {:?} task={} request={} entry={} {}",
            operator_safe_text(&correction.id),
            correction.correction_kind,
            operator_safe_or_dash(correction.task_id.as_deref()),
            operator_safe_or_dash(correction.request_id.as_deref()),
            operator_safe_or_dash(correction.entry_id.as_deref()),
            operator_safe_text(&correction.summary)
        );
    }
    Ok(())
}

fn operator_safe_or_dash(value: Option<&str>) -> String {
    value
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "-".to_string())
}
