//! Offdesk debug-bundle presentation.
//!
//! Command handlers retain bundle assembly, redaction accounting, and export
//! writes. This module only renders a completed bundle and optional export
//! result through the final operator-safe boundary.

use anyhow::Result;
use serde::Serialize;
use serde_json::Value;

use super::{operator_safe_json_value, DebugBundleExport, OffdeskDebugBundle};
use crate::offdesk::operator_safe_text;

#[derive(Serialize)]
struct DebugBundleExportReceipt<'a> {
    exported_to: String,
    bytes_written: usize,
    bundle: &'a OffdeskDebugBundle,
}

pub(super) fn present_debug_bundle(
    bundle: &OffdeskDebugBundle,
    export: Option<&DebugBundleExport>,
    json: bool,
) -> Result<()> {
    if json {
        if let Some(export) = export {
            present_operator_safe_json(&DebugBundleExportReceipt {
                exported_to: operator_safe_text(export.path.to_string_lossy().as_ref()),
                bytes_written: export.bytes_written,
                bundle,
            })?;
        } else {
            present_operator_safe_json(bundle)?;
        }
        return Ok(());
    }

    print_debug_bundle_summary(bundle);
    if let Some(export) = export {
        println!(
            "  exported_to:        {}",
            operator_safe_text(export.path.to_string_lossy().as_ref())
        );
        println!("  bytes_written:      {}", export.bytes_written);
    }
    Ok(())
}

pub(super) fn debug_bundle_export_error_context(action: &str, path: &std::path::Path) -> String {
    format!(
        "{} {}",
        operator_safe_text(action),
        operator_safe_text(path.to_string_lossy().as_ref())
    )
}

fn print_debug_bundle_summary(bundle: &OffdeskDebugBundle) {
    println!("Offdesk debug bundle");
    println!("  generated_at:       {}", bundle.generated_at);
    println!(
        "  profile:            {}",
        operator_safe_text(&bundle.profile)
    );
    println!(
        "  profile_dir:        {}",
        operator_safe_text(&bundle.profile_dir)
    );
    println!("  read_only:          {}", bundle.read_only);
    println!(
        "  approvals:          {}",
        json_array_len(&bundle.approvals)
    );
    println!("  tasks:              {}", json_array_len(&bundle.tasks));
    println!(
        "  resume_states:      {}",
        json_array_len(&bundle.resume_states)
    );
    println!(
        "  background_runs:    {}",
        json_array_len(&bundle.background_runs)
    );
    println!(
        "  provider_capacity:  {}",
        json_array_len(&bundle.provider_capacity)
    );
    println!(
        "  wiki_usage:         {}",
        json_array_len(&bundle.adaptive_wiki_usage)
    );
    println!(
        "  wiki_corrections:   {}",
        json_array_len(&bundle.adaptive_wiki_corrections)
    );
    println!(
        "  wiki_review_events: {}",
        json_array_len(&bundle.adaptive_wiki_review_events)
    );
    println!(
        "  wiki_runtime_acks:  {}",
        json_array_len(&bundle.adaptive_wiki_runtime_policy_acknowledgements)
    );
    println!(
        "  wiki_ack_attention: expired={} near_expiry={} suggested_actions={}",
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .expired,
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .near_expiry,
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .suggested_actions
    );
    println!(
        "  wiki_review_after:  expired={} near_expiry={} missing_review_after={}",
        bundle.adaptive_wiki_review_after_attention_summary.expired,
        bundle
            .adaptive_wiki_review_after_attention_summary
            .near_expiry,
        bundle
            .adaptive_wiki_review_after_attention_summary
            .missing_review_after
    );
    println!(
        "  redaction:          {} changed fields, {} context blocks, {} secrets",
        bundle.redaction_summary.changed_text_fields,
        bundle.redaction_summary.runner_context_removed,
        bundle.redaction_summary.secrets_redacted
    );
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn json_array_len(value: &Value) -> usize {
    value.as_array().map_or(0, Vec::len)
}
