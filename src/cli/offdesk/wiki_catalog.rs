//! Read-only adaptive-wiki entry and candidate catalog adapter.
//!
//! This module queries human projections and renders list or detail output. It
//! never writes canonical adaptive-wiki state or promotes a candidate.

use anyhow::{bail, Result};
use clap::Args;
use serde::Serialize;

use super::{
    adaptive_wiki_agent_modes_label, operator_safe_json_value, parse_adaptive_wiki_agent_mode,
    wiki_query, wiki_scope_label, wiki_store,
};
use crate::offdesk::{
    operator_safe_identity_selector, operator_safe_text, AdaptiveWikiAgentMode,
    AdaptiveWikiHumanCandidate, AdaptiveWikiHumanEntry, AdaptiveWikiQuery,
};

#[derive(Args)]
pub struct WikiListArgs {
    /// Session/request scope to match
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to match
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiShowArgs {
    /// Adaptive wiki entry or candidate id
    id: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum WikiShowResult {
    Entry {
        entry: AdaptiveWikiHumanEntry,
    },
    Candidate {
        candidate: AdaptiveWikiHumanCandidate,
    },
}

pub(super) async fn wiki_candidates(profile: &str, args: WikiListArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    ))?;

    if args.json {
        present_operator_safe_json(&projection.candidates)?;
        return Ok(());
    }

    if projection.candidates.is_empty() {
        println!("No adaptive wiki candidates found.");
        return Ok(());
    }

    print_wiki_candidates(&projection.candidates);
    Ok(())
}

pub(super) async fn wiki_entries(profile: &str, args: WikiListArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    ))?;

    if args.json {
        present_operator_safe_json(&projection.entries)?;
        return Ok(());
    }

    if projection.entries.is_empty() {
        println!("No adaptive wiki entries found.");
        return Ok(());
    }

    print_wiki_entries(&projection.entries);
    Ok(())
}

pub(super) async fn wiki_show(profile: &str, args: WikiShowArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&AdaptiveWikiQuery::default())?;
    let selector = operator_safe_identity_selector(&args.id);
    let result = projection
        .entries
        .into_iter()
        .find(|entry| entry.id == selector)
        .map(|entry| WikiShowResult::Entry { entry })
        .or_else(|| {
            projection
                .candidates
                .into_iter()
                .find(|candidate| candidate.id == selector)
                .map(|candidate| WikiShowResult::Candidate { candidate })
        });

    let Some(result) = result else {
        bail!(
            "Adaptive wiki entry or candidate not found: {}",
            operator_safe_text(&args.id)
        );
    };

    if args.json {
        present_operator_safe_json(&result)?;
        return Ok(());
    }

    print_wiki_show(&result);
    Ok(())
}

fn print_wiki_entries(entries: &[AdaptiveWikiHumanEntry]) {
    println!(
        "{:<44} {:<12} {:<14} {:<16} {:<18} CLAIM",
        "ID", "STATUS", "SCOPE", "ACTIVATION", "AGENT_MODES"
    );
    for entry in entries {
        println!(
            "{:<44} {:<12} {:<14} {:<16} {:<18} {}",
            operator_safe_text(&entry.id),
            format!("{:?}", entry.status).to_lowercase(),
            operator_safe_text(&wiki_scope_label(entry.scope, &entry.scope_ref)),
            format!("{:?}", entry.activation_mode).to_lowercase(),
            adaptive_wiki_agent_modes_label(&entry.agent_modes),
            operator_safe_text(&entry.claim)
        );
        if !entry.human_summary.trim().is_empty() {
            println!("  summary: {}", operator_safe_text(&entry.human_summary));
        }
        if !entry.evidence_refs.is_empty() {
            println!("  evidence: {}", operator_safe_list(&entry.evidence_refs));
        }
        if !entry.support_refs.is_empty() {
            println!("  support: {}", operator_safe_list(&entry.support_refs));
        }
        if !entry.capability_ids.is_empty() {
            println!(
                "  capabilities: {}",
                operator_safe_list(&entry.capability_ids)
            );
        }
        if !entry.required_artifact_kinds.is_empty() {
            println!(
                "  artifacts: {}",
                operator_safe_list(&entry.required_artifact_kinds)
            );
        }
    }
}

fn print_wiki_candidates(candidates: &[AdaptiveWikiHumanCandidate]) {
    println!(
        "{:<44} {:<14} {:<18} {:<18} {:<8} CLAIM",
        "ID", "SCOPE", "SIGNAL", "AGENT_MODES", "HITS"
    );
    for candidate in candidates {
        println!(
            "{:<44} {:<14} {:<18} {:<18} {:<8} {}",
            operator_safe_text(&candidate.id),
            operator_safe_text(&wiki_scope_label(candidate.scope, &candidate.scope_ref)),
            format!("{:?}", candidate.signal_kind).to_lowercase(),
            adaptive_wiki_agent_modes_label(&candidate.agent_modes),
            candidate.occurrence_count,
            operator_safe_text(&candidate.claim)
        );
        if !candidate.review_reason.trim().is_empty() {
            println!("  review: {}", operator_safe_text(&candidate.review_reason));
        }
        if !candidate.source_refs.is_empty() {
            println!("  sources: {}", operator_safe_list(&candidate.source_refs));
        } else if !candidate.evidence_refs.is_empty() {
            println!(
                "  evidence: {}",
                operator_safe_list(&candidate.evidence_refs)
            );
        }
    }
}

fn print_wiki_show(result: &WikiShowResult) {
    match result {
        WikiShowResult::Entry { entry } => {
            println!("Adaptive wiki entry {}", operator_safe_text(&entry.id));
            println!("  status:     {:?}", entry.status);
            println!("  kind:       {:?}", entry.kind);
            println!(
                "  scope:      {}",
                operator_safe_text(&wiki_scope_label(entry.scope, &entry.scope_ref))
            );
            println!("  activation: {:?}", entry.activation_mode);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&entry.agent_modes)
            );
            println!("  confidence: {:?}", entry.confidence);
            println!("  claim:      {}", operator_safe_text(&entry.claim));
            if !entry.human_summary.trim().is_empty() {
                println!("  summary:    {}", operator_safe_text(&entry.human_summary));
            }
            if !entry.evidence_refs.is_empty() {
                println!("  evidence:   {}", operator_safe_list(&entry.evidence_refs));
            }
            if !entry.support_refs.is_empty() {
                println!("  support:    {}", operator_safe_list(&entry.support_refs));
            }
            if !entry.capability_ids.is_empty() {
                println!(
                    "  capabilities: {}",
                    operator_safe_list(&entry.capability_ids)
                );
            }
            if !entry.required_artifact_kinds.is_empty() {
                println!(
                    "  artifacts:  {}",
                    operator_safe_list(&entry.required_artifact_kinds)
                );
            }
        }
        WikiShowResult::Candidate { candidate } => {
            println!(
                "Adaptive wiki candidate {}",
                operator_safe_text(&candidate.id)
            );
            println!("  kind:       {:?}", candidate.kind);
            println!(
                "  scope:      {}",
                operator_safe_text(&wiki_scope_label(candidate.scope, &candidate.scope_ref))
            );
            println!("  signal:     {:?}", candidate.signal_kind);
            println!("  origin:     {:?}", candidate.origin);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&candidate.agent_modes)
            );
            println!("  hits:       {}", candidate.occurrence_count);
            println!("  confidence: {:?}", candidate.confidence);
            println!("  claim:      {}", operator_safe_text(&candidate.claim));
            if !candidate.human_summary.trim().is_empty() {
                println!(
                    "  summary:    {}",
                    operator_safe_text(&candidate.human_summary)
                );
            }
            if !candidate.review_reason.trim().is_empty() {
                println!(
                    "  review:     {}",
                    operator_safe_text(&candidate.review_reason)
                );
            }
            if !candidate.source_refs.is_empty() {
                println!(
                    "  sources:    {}",
                    operator_safe_list(&candidate.source_refs)
                );
            }
        }
    }
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

fn operator_safe_list(values: &[String]) -> String {
    values
        .iter()
        .map(|value| operator_safe_text(value))
        .collect::<Vec<_>>()
        .join(", ")
}
