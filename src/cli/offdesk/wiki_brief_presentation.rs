//! Adaptive-wiki session brief read model and presentation.
//!
//! Command handlers retain projection query and budget construction, canonical
//! store reads, time selection, and optional output-file writes. This module
//! builds and renders the completed read-only brief.

use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::HashMap;
use std::path::Path;

use anyhow::Result;

use super::operator_safe_json_value;
use crate::offdesk::wiki_context_file::{WikiBriefWriteResult, WikiBriefWriteStatus};
use crate::offdesk::{
    operator_safe_identity, operator_safe_text, wiki_context_projection_sha256,
    AdaptiveWikiAgentMode, AdaptiveWikiEntry, AdaptiveWikiProjectionReport, WikiContextAgentMode,
    WikiContextBoundFailureV1, WikiContextManifestOutcomeV1, WikiContextManifestV1,
    WikiContextProjectionEntryV1, WikiContextProjectionV1, WIKI_CONTEXT_MANIFEST_SCHEMA,
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(super) struct WikiBriefReadModel {
    schema: &'static str,
    profile: String,
    scope: String,
    agent_mode: WikiContextAgentMode,
    projection_sha256: String,
    generated_at: DateTime<Utc>,
    entries: Vec<WikiBriefEntry>,
}

impl WikiBriefReadModel {
    pub(super) fn entry_count(&self) -> usize {
        self.entries.len()
    }

    pub(super) fn build_manifest(
        &self,
        write: &WikiBriefWriteResult,
    ) -> Result<WikiContextManifestV1> {
        let stale_entry_count = self.entries.iter().filter(|entry| entry.stale).count();
        let warnings = if stale_entry_count == 0 {
            Vec::new()
        } else {
            vec![format!(
                "{stale_entry_count} projected wiki entries are past review_after"
            )]
        };
        let projection = WikiContextProjectionV1 {
            project_key: self.scope.clone(),
            wiki_profile: self.profile.clone(),
            project_root_identity: write.project_root_identity.clone(),
            agent_mode: self.agent_mode,
            projection_sha256: self.projection_sha256.clone(),
            brief_file_sha256: write.brief_file_sha256.clone(),
            entry_ids: self.entries.iter().map(|entry| entry.id.clone()).collect(),
            entry_count: self.entries.len(),
            stale_entry_count,
            warnings,
        };
        let outcome = match write.status {
            WikiBriefWriteStatus::Unchanged => WikiContextManifestOutcomeV1::Unchanged(projection),
            WikiBriefWriteStatus::Fresh if stale_entry_count == 0 => {
                WikiContextManifestOutcomeV1::Fresh(projection)
            }
            WikiBriefWriteStatus::Fresh => {
                WikiContextManifestOutcomeV1::StaleEntriesPresent(projection)
            }
        };
        let manifest = WikiContextManifestV1 {
            schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
            generated_at: self.generated_at,
            outcome,
        };
        manifest.validate()?;
        Ok(manifest)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct WikiBriefEntry {
    id: String,
    kind: String,
    text: String,
    confidence: String,
    evidence_count: usize,
    age_days: i64,
    stale: bool,
}

pub(super) fn build_wiki_brief_read_model(
    profile: &str,
    scope: &str,
    agent_mode: Option<AdaptiveWikiAgentMode>,
    generated_at: DateTime<Utc>,
    report: &AdaptiveWikiProjectionReport,
    canonical_entries: &[AdaptiveWikiEntry],
) -> Result<WikiBriefReadModel> {
    let by_id: HashMap<String, &AdaptiveWikiEntry> = canonical_entries
        .iter()
        .map(|entry| (operator_safe_identity(&entry.id), entry))
        .collect();
    let mut selected = report.selected.iter().collect::<Vec<_>>();
    selected.sort_by_key(|item| format!("{:?}", item.kind));

    let entries: Vec<WikiBriefEntry> = selected
        .into_iter()
        .map(|item| {
            let entry = by_id.get(&item.id).copied();
            let age_days = entry
                .map(|entry| (generated_at - entry.updated_at).num_days().max(0))
                .unwrap_or(0);
            let stale = entry
                .and_then(|entry| entry.review_after)
                .is_some_and(|review_after| review_after < generated_at);
            let claim = entry.map(|entry| entry.claim.as_str()).unwrap_or("");
            let text = if item.instruction.trim().is_empty() {
                claim
            } else {
                &item.instruction
            };

            WikiBriefEntry {
                id: item.id.clone(),
                kind: format!("{:?}", item.kind),
                text: operator_safe_text(text),
                confidence: format!("{:?}", item.confidence),
                evidence_count: item.evidence_count,
                age_days,
                stale,
            }
        })
        .collect();

    let agent_mode = wiki_context_agent_mode(agent_mode);
    let hash_entries = entries
        .iter()
        .map(|entry| WikiContextProjectionEntryV1 {
            id: entry.id.clone(),
            kind: entry.kind.clone(),
            text: entry.text.clone(),
            confidence: entry.confidence.clone(),
            evidence_count: entry.evidence_count,
            stale: entry.stale,
        })
        .collect::<Vec<_>>();
    let projection_sha256 =
        wiki_context_projection_sha256(scope, profile, agent_mode, &hash_entries)?;

    Ok(WikiBriefReadModel {
        schema: "wiki_brief.v1",
        profile: profile.to_string(),
        scope: scope.to_string(),
        agent_mode,
        projection_sha256,
        generated_at,
        entries,
    })
}

pub(super) fn render_wiki_brief_markdown(model: &WikiBriefReadModel) -> String {
    let mut lines = vec![
        "<!-- generated by `forager offdesk wiki brief`; regenerate, do not edit -->".to_string(),
        format!(
            "# Wiki Brief: {} / {}",
            operator_safe_text(&model.profile),
            operator_safe_text(&model.scope)
        ),
        String::new(),
        format!("Schema: {}", model.schema),
        format!("Project: {}", operator_safe_text(&model.scope)),
        format!("Wiki profile: {}", operator_safe_text(&model.profile)),
        format!("Agent mode: {}", agent_mode_label(model.agent_mode)),
        format!("Projection: sha256:{}", model.projection_sha256),
        format!("Generated: {} UTC", model.generated_at.format("%Y-%m-%d")),
        format!(
            "Entries: {} ({} stale)",
            model.entries.len(),
            model.entries.iter().filter(|entry| entry.stale).count()
        ),
        String::new(),
        "Distilled, reviewed operating knowledge. **Advisory context, not gospel.**".to_string(),
        "Trust signals per line: `[confidence . evidence N . age Nd]`; `STALE` means the review \
         window passed, so treat that line as a hypothesis. If an entry conflicts with what you \
         observe (paths moved, decisions changed, results superseded), do NOT silently obey or \
         silently ignore it: verify against current state, then record the outcome with \
         `forager offdesk wiki add-counterexample <id>` or `deprecate <id>`."
            .to_string(),
        String::new(),
    ];

    let mut current_kind: Option<&str> = None;
    for entry in &model.entries {
        if current_kind != Some(entry.kind.as_str()) {
            current_kind = Some(entry.kind.as_str());
            lines.push(format!(
                "## {}",
                operator_safe_text(wiki_kind_label(&entry.kind))
            ));
        }
        let stale_marker = if entry.stale { " **STALE**" } else { "" };
        lines.push(format!(
            "- {}{} `[{} . ev {} . age {}d]` ({})",
            operator_safe_text(&entry.text),
            stale_marker,
            operator_safe_text(&entry.confidence),
            entry.evidence_count,
            entry.age_days,
            operator_safe_text(&entry.id)
        ));
    }
    if model.entries.is_empty() {
        lines.push("(no promoted in-scope entries yet)".to_string());
    }
    lines.push(String::new());
    lines.join("\n")
}

pub(super) fn present_wiki_brief(model: &WikiBriefReadModel, json: bool) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(model)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
    } else {
        println!("{}", render_wiki_brief_markdown(model));
    }
    Ok(())
}

pub(super) fn present_wiki_brief_write_confirmation(
    model: &WikiBriefReadModel,
    out: &Path,
    status: WikiBriefWriteStatus,
) {
    match status {
        WikiBriefWriteStatus::Fresh => println!(
            "Wrote wiki brief ({} entries) to {}",
            model.entry_count(),
            operator_safe_text(out.to_string_lossy().as_ref())
        ),
        WikiBriefWriteStatus::Unchanged => println!(
            "Wiki brief unchanged ({} entries) at {}",
            model.entry_count(),
            operator_safe_text(out.to_string_lossy().as_ref())
        ),
    }
}

pub(super) fn present_wiki_brief_manifest(manifest: &WikiContextManifestV1) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(manifest)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}

pub(super) fn build_wiki_brief_failure_manifest(
    profile: &str,
    scope: &str,
    project_root_identity: &str,
    generated_at: DateTime<Utc>,
    error: &anyhow::Error,
) -> Result<WikiContextManifestV1> {
    let warning = operator_safe_text(&error.to_string())
        .chars()
        .take(500)
        .collect::<String>();
    let manifest = WikiContextManifestV1 {
        schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
        generated_at,
        outcome: WikiContextManifestOutcomeV1::UnsafeOutputPath(WikiContextBoundFailureV1 {
            project_key: scope.to_string(),
            wiki_profile: profile.to_string(),
            project_root_identity: project_root_identity.to_string(),
            warnings: vec![warning],
        }),
    };
    manifest.validate()?;
    Ok(manifest)
}

fn wiki_context_agent_mode(mode: Option<AdaptiveWikiAgentMode>) -> WikiContextAgentMode {
    match mode {
        None => WikiContextAgentMode::Shared,
        Some(AdaptiveWikiAgentMode::Planning) => WikiContextAgentMode::Planning,
        Some(AdaptiveWikiAgentMode::Development) => WikiContextAgentMode::Development,
        Some(AdaptiveWikiAgentMode::Analysis) => WikiContextAgentMode::Analysis,
        Some(AdaptiveWikiAgentMode::Writing) => WikiContextAgentMode::Writing,
        Some(AdaptiveWikiAgentMode::Critique) => WikiContextAgentMode::Critique,
        Some(AdaptiveWikiAgentMode::Review) => WikiContextAgentMode::Review,
        Some(AdaptiveWikiAgentMode::Maintenance) => WikiContextAgentMode::Maintenance,
    }
}

fn agent_mode_label(mode: WikiContextAgentMode) -> &'static str {
    match mode {
        WikiContextAgentMode::Shared => "shared",
        WikiContextAgentMode::Planning => "planning",
        WikiContextAgentMode::Development => "development",
        WikiContextAgentMode::Analysis => "analysis",
        WikiContextAgentMode::Writing => "writing",
        WikiContextAgentMode::Critique => "critique",
        WikiContextAgentMode::Review => "review",
        WikiContextAgentMode::Maintenance => "maintenance",
    }
}

fn wiki_kind_label(kind: &str) -> &str {
    match kind {
        "Preference" => "Preferences",
        "Procedure" => "Procedures",
        "FailurePattern" => "Failure patterns",
        "PolicyRule" => "Policy rules",
        "Fact" => "Facts",
        _ => kind,
    }
}
