//! Offdesk provider-capacity and fallback presentation.
//!
//! Command handlers retain profile and capacity-store reads plus fallback
//! recommendation policy. This module only renders completed read models and
//! applies the final operator-safe boundary.

use anyhow::Result;

use super::operator_safe_json_value;
use crate::offdesk::{operator_safe_text, ProviderCapacityState, ProviderFallbackRecommendation};

pub(super) fn present_provider_capacity(
    states: &[ProviderCapacityState],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(states)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if states.is_empty() {
        println!("No provider capacity state found.");
        return Ok(());
    }

    println!(
        "{:<20} {:<24} {:<14} {:<16} COOLDOWN_UNTIL",
        "PROVIDER", "MODEL", "STATUS", "REASON"
    );
    for state in states {
        println!(
            "{:<20} {:<24} {:<14} {:<16} {}",
            operator_safe_text(&state.provider_id),
            state
                .model
                .as_deref()
                .map(operator_safe_text)
                .unwrap_or_else(|| "-".to_string()),
            format!("{:?}", state.status).to_lowercase(),
            format!("{:?}", state.reason).to_lowercase(),
            state
                .cooldown_until
                .map(|cooldown_until| cooldown_until.to_string())
                .unwrap_or_else(|| "-".to_string())
        );
        if let Some(summary) = state.last_error_summary.as_deref() {
            println!("  summary: {}", operator_safe_text(summary));
        }
    }
    Ok(())
}

pub(super) fn present_provider_fallback(
    recommendation: &ProviderFallbackRecommendation,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(recommendation)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "Provider fallback for {} model {}",
        operator_safe_text(&recommendation.current_provider_id),
        recommendation
            .current_model
            .as_deref()
            .map(operator_safe_text)
            .unwrap_or_else(|| "-".to_string())
    );
    println!(
        "  trigger: {}",
        operator_safe_text(&recommendation.trigger_reason)
    );
    if recommendation.candidates.is_empty() {
        println!("  no fallback candidates found");
        return Ok(());
    }
    println!(
        "{:<20} {:<28} {:<30} {:<14} {:<14} RECOMMENDED",
        "PROVIDER", "MODEL", "SOURCE", "AUTH", "CAPACITY"
    );
    for candidate in &recommendation.candidates {
        println!(
            "{:<20} {:<28} {:<30} {:<14} {:<14} {}",
            operator_safe_text(&candidate.provider_id),
            candidate
                .model
                .as_deref()
                .map(operator_safe_text)
                .unwrap_or_else(|| "-".to_string()),
            format!("{:?}", candidate.source).to_lowercase(),
            format!("{:?}", candidate.auth_status).to_lowercase(),
            format!("{:?}", candidate.capacity_status).to_lowercase(),
            if candidate.recommended { "yes" } else { "no" }
        );
        println!("  reason: {}", operator_safe_text(&candidate.reason));
    }
    Ok(())
}
