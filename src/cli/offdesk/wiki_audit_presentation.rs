//! Adaptive-wiki lint, markdown-export, and graph presentation.
//!
//! Command handlers retain canonical store queries, export path selection,
//! markdown-vault writes, graph artifact construction, and graph file writes.
//! This module only renders completed reports.

use std::path::Path;

use anyhow::Result;
use serde::Serialize;

use super::operator_safe_json_value;
use crate::offdesk::{
    operator_safe_text, AdaptiveWikiGraphReport, AdaptiveWikiLintReport,
    AdaptiveWikiMarkdownExportReport,
};

pub(super) fn present_wiki_lint(report: &AdaptiveWikiLintReport, json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!(
        "Adaptive wiki lint: {} errors, {} warnings, {} info ({} entries, {} candidates)",
        report.summary.errors,
        report.summary.warnings,
        report.summary.info,
        report.summary.entries_checked,
        report.summary.candidates_checked
    );
    for issue in &report.issues {
        println!(
            "  - {:?} {} {}: {}",
            issue.severity,
            operator_safe_text(&issue.subject_kind),
            operator_safe_text(&issue.subject_id),
            operator_safe_text(&issue.message)
        );
    }
    Ok(())
}

pub(super) fn present_wiki_markdown_export(
    report: &AdaptiveWikiMarkdownExportReport,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki markdown export {} {} files to {}",
        action,
        report.summary.files_planned,
        operator_safe_text(&report.output_dir)
    );
    println!(
        "  status: {:?}  reexport_recommended={}",
        report.projection_status.state, report.projection_status.reexport_recommended
    );
    println!(
        "  entries: {}  candidates: {}",
        report.summary.entries_exported, report.summary.candidates_exported
    );
    for file in &report.files {
        println!(
            "  - {} ({} bytes, sha256:{})",
            operator_safe_text(&file.path),
            file.bytes,
            operator_safe_text(&file.sha256)
        );
    }
    Ok(())
}

pub(super) fn present_wiki_graph(
    report: &AdaptiveWikiGraphReport,
    output: Option<&Path>,
    dry_run: bool,
    files: usize,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(report)?;
        return Ok(());
    }

    println!(
        "Adaptive wiki tag graph: {} nodes, {} edges, {} review issues",
        report.nodes.len(),
        report.edges.len(),
        report.review_issues.len()
    );
    println!(
        "  entries: {}  candidates: {}  tag_nodes: {}",
        report.summary.entries, report.summary.candidates, report.summary.tag_nodes
    );
    println!(
        "  tag_edges: derived_core={} core={} proposed={}",
        report.summary.derived_core_tag_edges,
        report.summary.core_tag_edges,
        report.summary.proposed_tag_edges
    );
    if let Some(output) = output {
        let action = if dry_run { "planned" } else { "wrote" };
        println!(
            "  export: {} {} files to {}",
            action,
            files,
            operator_safe_text(output.to_string_lossy().as_ref())
        );
    }
    for issue in report.review_issues.iter().take(8) {
        println!(
            "  - {:?} {}:{} #{} {}",
            issue.severity,
            operator_safe_text(&issue.subject_kind),
            operator_safe_text(&issue.subject_id),
            operator_safe_text(&issue.tag),
            operator_safe_text(&issue.code)
        );
    }
    Ok(())
}

fn present_operator_safe_json(value: &impl Serialize) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
