//! Offdesk capability-registry and hosted-harness profile presentation.
//!
//! Command handlers retain registry and profile selection. Hosted-harness
//! profile definitions and prompt construction remain in `hosted_harness`.
//! This module only renders completed read models.

use anyhow::Result;

use super::{
    hosted_harness::{HostedHarnessProfileView, HostedHarnessPromptPacket},
    operator_safe_json_value,
};
use crate::offdesk::{operator_safe_text, CapabilityDescriptor};

pub(super) fn present_capabilities(
    capabilities: &[CapabilityDescriptor],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(capabilities)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "{:<24} {:<20} {:<18} {:<8} LABEL",
        "CAPABILITY", "OWNER", "RISK", "OFFDESK"
    );
    for capability in capabilities {
        println!(
            "{:<24} {:<20} {:<18} {:<8} {}",
            operator_safe_text(&capability.capability_id),
            operator_safe_text(&capability.owner_module),
            format!("{:?}", capability.risk_level).to_lowercase(),
            if capability.offdesk_allowed {
                "yes"
            } else {
                "no"
            },
            operator_safe_text(&capability.dashboard_label)
        );
    }
    Ok(())
}

pub(super) fn present_hosted_harness_profiles(
    profiles: &[HostedHarnessProfileView],
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(profiles)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!("Hosted harness agent profiles");
    println!("Current support target: Codex CLI and Claude Code");
    println!();
    for profile in profiles {
        let command = profile
            .launch_command
            .map(operator_safe_text)
            .unwrap_or_else(|| "manual integration".to_string());
        println!(
            "- {} ({}) [{}]",
            operator_safe_text(profile.display_name),
            operator_safe_text(profile.id),
            operator_safe_text(profile.support_status)
        );
        println!("  launch:  {command}");
        println!("  runner:  {}", operator_safe_text(profile.runner));
        println!("  scope:   {}", operator_safe_text(profile.mutation_scope));
        println!(
            "  prompt:  {} (inline <= {} bytes, first-read <= {} bytes total)",
            operator_safe_text(profile.prompt_contract.strategy),
            profile.prompt_contract.inline_context_budget_bytes,
            profile.prompt_contract.first_read_total_budget_bytes
        );
        let first_reads = profile
            .prompt_contract
            .preferred_first_reads
            .iter()
            .map(|value| operator_safe_text(value))
            .collect::<Vec<_>>();
        println!("  reads:   {}", first_reads.join(", "));
        println!("  result:  {}", operator_safe_text(profile.result_artifact));
        println!("  failure: {}", operator_safe_text(profile.failure_signal));
        println!("  note:    {}", operator_safe_text(profile.notes));
    }
    Ok(())
}

pub(super) fn hosted_harness_prompt_guard_error(warnings: &[String]) -> String {
    let warnings = warnings
        .iter()
        .map(|warning| operator_safe_text(warning))
        .collect::<Vec<_>>();
    format!("first-read budget guard failed: {}", warnings.join("; "))
}

pub(super) fn present_hosted_harness_prompt_packet(
    packet: &HostedHarnessPromptPacket,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(packet)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if let Some(output_path) = packet.output_path.as_deref() {
        println!(
            "Wrote hosted harness prompt: {}",
            operator_safe_text(output_path)
        );
    } else {
        println!("{}", operator_safe_text(&packet.prompt_markdown));
    }
    for warning in &packet.warnings {
        println!("warning: {}", operator_safe_text(warning));
    }
    Ok(())
}
