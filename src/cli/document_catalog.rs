use anyhow::{Context, Result};
use clap::Args;
use std::path::PathBuf;

use crate::document_lifecycle::{
    lookup_document_with_lifecycle, lookup_observation, scan_project, scan_project_with_lifecycle,
    search_project, search_project_with_lifecycle, CatalogOptions, DocumentCatalogV2,
    DocumentExactSelectorV2, DocumentSearchV1, DocumentSearchV2, MediaCoverage,
    ProjectionSourceIdentity, ScanBudgets, ScanEnvelope, DEFAULT_MAX_DIRECTORY_ENTRIES,
    DEFAULT_MAX_ELAPSED_MS, DEFAULT_MAX_FILES, DEFAULT_MAX_FILE_BYTES, DEFAULT_MAX_TOTAL_BYTES,
};
use crate::session::profile_dir_path;

#[derive(Debug, Clone, Args)]
pub struct DocumentScanBudgetArgs {
    /// Maximum supported document files to observe
    #[arg(long, default_value_t = DEFAULT_MAX_FILES)]
    max_files: u64,

    /// Maximum filesystem entries to enumerate across all directories
    #[arg(long, default_value_t = DEFAULT_MAX_DIRECTORY_ENTRIES)]
    max_directory_entries: u64,

    /// Maximum total document bytes to hash and extract
    #[arg(long, default_value_t = DEFAULT_MAX_TOTAL_BYTES)]
    max_total_bytes: u64,

    /// Maximum bytes to read from any one document
    #[arg(long, default_value_t = DEFAULT_MAX_FILE_BYTES)]
    max_file_bytes: u64,

    /// Maximum elapsed scan time in milliseconds
    #[arg(long, default_value_t = DEFAULT_MAX_ELAPSED_MS)]
    max_elapsed_ms: u64,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentCatalogArgs {
    /// One explicit bounded project root to scan
    path: PathBuf,

    /// Project key for this ephemeral projection
    #[arg(long)]
    project_key: Option<String>,

    #[command(flatten)]
    budgets: DocumentScanBudgetArgs,

    /// Join the active profile lifecycle ledger and emit document_catalog.v2
    #[arg(long)]
    lifecycle: bool,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentSearchArgs {
    /// One explicit bounded project root to scan live
    path: PathBuf,

    /// Path and supported-text query
    #[arg(
        long,
        conflicts_with_all = ["observation_id", "document_id", "relative_path"]
    )]
    query: Option<String>,

    /// Inspect one observation from the current live catalog
    #[arg(long, conflicts_with = "query")]
    observation_id: Option<String>,

    /// Inspect one durable lifecycle document from the current live catalog
    #[arg(long, requires = "lifecycle", conflicts_with = "query")]
    document_id: Option<String>,

    /// Inspect one exact bounded project-relative path
    #[arg(long, requires = "lifecycle", conflicts_with = "query")]
    relative_path: Option<String>,

    /// Join the active profile lifecycle ledger and emit document_search.v2
    #[arg(long)]
    lifecycle: bool,

    /// Return reviewed cold query matches instead of omitting them
    #[arg(long, requires_all = ["lifecycle", "query"])]
    include_cold: bool,

    /// Project key for this ephemeral projection
    #[arg(long)]
    project_key: Option<String>,

    #[command(flatten)]
    budgets: DocumentScanBudgetArgs,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

pub fn run_catalog(profile: &str, args: ProjectDocumentCatalogArgs) -> Result<()> {
    let options = catalog_options(args.project_key, args.budgets);
    if args.lifecycle {
        let profile_root = profile_dir_path(profile)?;
        let catalog = scan_project_with_lifecycle(&profile_root, &args.path, &options)?;
        if args.json {
            println!(
                "{}",
                serde_json::to_string_pretty(&catalog)
                    .context("serialize document catalog v2 projection")?
            );
        } else {
            render_catalog_v2(&catalog);
        }
        return Ok(());
    }
    let catalog = scan_project(&args.path, &options)?;
    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&catalog)
                .context("serialize document catalog projection")?
        );
    } else {
        println!("Document catalog: {}", catalog.project_key);
        println!("Root: {}", catalog.roots[0].canonical_path);
        println!("Documents: {}", catalog.entries.len());
        render_scan_evidence(
            catalog.generated_at,
            &catalog.source_identities.filesystem_snapshot,
            &catalog.scan,
            &catalog.coverage,
        );
        for entry in &catalog.entries {
            println!(
                "- {} [{}] {} bytes",
                entry.relative_path, entry.observation_id, entry.size_bytes
            );
        }
    }
    Ok(())
}

fn render_catalog_v2(catalog: &DocumentCatalogV2) {
    println!("Document catalog v2: {}", catalog.base_catalog.project_key);
    println!("Root: {}", catalog.base_catalog.roots[0].canonical_path);
    println!("Documents: {}", catalog.entries.len());
    println!("Lifecycle status: {:?}", catalog.lifecycle.status);
    println!(
        "Lifecycle filtering authorized: {}",
        catalog.lifecycle.filtering_authorized
    );
    if let Some(source) = &catalog.lifecycle.source_identity {
        println!(
            "Lifecycle snapshot: events {} through {}, head {}, observed {}",
            source.first_sequence,
            source.last_sequence,
            source.final_event_sha256,
            source.observed_at
        );
    }
    if let Some(code) = &catalog.lifecycle.failure_code {
        println!("Lifecycle failure: {code}");
    }
    render_scan_evidence(
        catalog.base_catalog.generated_at,
        &catalog.base_catalog.source_identities.filesystem_snapshot,
        &catalog.base_catalog.scan,
        &catalog.base_catalog.coverage,
    );
    for entry in &catalog.entries {
        println!(
            "- {} [{}] document={} stale={} visibility={:?}",
            entry.observation.relative_path,
            entry.observation.observation_id,
            entry.document_id.as_deref().unwrap_or("unbound"),
            entry.lifecycle_stale,
            entry.visibility
        );
    }
    println!("Lifecycle findings: {}", catalog.lifecycle_findings.len());
    for finding in &catalog.lifecycle_findings {
        println!(
            "- {} document={} current_candidates={}: {}",
            finding.code,
            finding.document_id,
            finding.current_observation_ids.len(),
            finding.message
        );
    }
    println!("File mutation: not authorized");
}

pub fn run_search(profile: &str, args: ProjectDocumentSearchArgs) -> Result<()> {
    let options = catalog_options(args.project_key, args.budgets);
    if args.lifecycle {
        let profile_root = profile_dir_path(profile)?;
        let result = if let Some(query) = args.query.as_deref() {
            search_project_with_lifecycle(
                &profile_root,
                &args.path,
                &options,
                query,
                args.include_cold,
            )?
        } else {
            lookup_document_with_lifecycle(
                &profile_root,
                &args.path,
                &options,
                DocumentExactSelectorV2 {
                    observation_id: args.observation_id,
                    document_id: args.document_id,
                    relative_path: args.relative_path,
                },
            )?
        };
        if args.json {
            println!(
                "{}",
                serde_json::to_string_pretty(&result)
                    .context("serialize document search v2 projection")?
            );
        } else {
            render_search_v2(&result);
        }
        return Ok(());
    }
    let result = match (args.query.as_deref(), args.observation_id.as_deref()) {
        (Some(query), None) => search_project(&args.path, &options, query)?,
        (None, Some(observation_id)) => lookup_observation(&args.path, &options, observation_id)?,
        _ => anyhow::bail!("document search requires --query or --observation-id"),
    };
    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(&result)
                .context("serialize document search projection")?
        );
    } else {
        render_search(&result);
    }
    Ok(())
}

fn render_search_v2(result: &DocumentSearchV2) {
    println!(
        "Document search v2: {}",
        result.catalog.base_catalog.project_key
    );
    println!("Mode: {:?}", result.mode);
    println!("Lifecycle status: {:?}", result.catalog.lifecycle.status);
    println!(
        "Lifecycle filtering authorized: {}",
        result.catalog.lifecycle.filtering_authorized
    );
    println!("Pre-filter matches: {}", result.prefilter_match_count);
    println!("Visible matches: {}", result.visible_match_count);
    println!("Cold matches omitted: {}", result.cold_matches_omitted);
    for result_match in &result.matches {
        println!(
            "- {} [{}] visibility={:?}",
            result_match.entry.observation.relative_path,
            result_match.entry.observation.observation_id,
            result_match.entry.visibility
        );
        if let Some(warning) = &result_match.cold_warning {
            println!("  Warning: {warning}");
        }
        for snippet in &result_match.snippets {
            println!("  {snippet}");
        }
    }
    if let Some(negative) = &result.negative_evidence {
        println!(
            "Content absence established: {}",
            negative.content_absence_established
        );
        println!("Owner absence authorized: false");
        println!("Reason: {}", negative.reason);
    }
    println!("Profile state write: not authorized");
    println!("Project state write: not authorized");
    println!("File mutation: not authorized");
}

fn catalog_options(project_key: Option<String>, budgets: DocumentScanBudgetArgs) -> CatalogOptions {
    CatalogOptions {
        project_key,
        budgets: ScanBudgets {
            max_files: budgets.max_files,
            max_directory_entries: budgets.max_directory_entries,
            max_total_bytes: budgets.max_total_bytes,
            max_file_bytes: budgets.max_file_bytes,
            max_elapsed_ms: budgets.max_elapsed_ms,
        },
    }
}

fn render_search(result: &DocumentSearchV1) {
    println!("Document search: {}", result.project_key);
    println!(
        "Search freshness: {} (stale={})",
        result.freshness.mode, result.freshness.stale
    );
    render_scan_evidence(
        result.generated_at,
        &result.filesystem_snapshot,
        &result.scan,
        &result.coverage,
    );
    println!("Matches: {}", result.matches.len());
    for result_match in &result.matches {
        println!(
            "- {} [{}]",
            result_match.entry.relative_path, result_match.entry.observation_id
        );
        for snippet in &result_match.snippets {
            println!("  {}", snippet);
        }
    }
    if let Some(negative) = &result.negative_evidence {
        println!("Absence established: {}", negative.absence_established);
        println!("Reason: {}", negative.reason);
    }
}

fn render_scan_evidence(
    generated_at: chrono::DateTime<chrono::Utc>,
    snapshot: &ProjectionSourceIdentity,
    scan: &ScanEnvelope,
    coverage: &[MediaCoverage],
) {
    println!(
        "Freshness: live snapshot {} observed at {}; projection generated at {}",
        snapshot.identity, snapshot.observed_at, generated_at
    );
    println!("Complete: {}", scan.complete);
    println!(
        "Budget usage: files {}/{}, directory entries {}/{}, hashed bytes {}/{}, extracted bytes {}, largest read {}/{}, elapsed {}/{} ms",
        scan.usage.files_observed,
        scan.budgets.max_files,
        scan.usage.directory_entries_observed,
        scan.budgets.max_directory_entries,
        scan.usage.bytes_hashed,
        scan.budgets.max_total_bytes,
        scan.usage.bytes_extracted,
        scan.usage.largest_file_bytes_read,
        scan.budgets.max_file_bytes,
        scan.usage.elapsed_ms,
        scan.budgets.max_elapsed_ms
    );
    if scan.reached_limits.is_empty() {
        println!("Reached limits: none");
    } else {
        println!(
            "Reached limits: {}",
            scan.reached_limits
                .iter()
                .map(|limit| format!("{limit:?}"))
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    println!("Coverage:");
    if coverage.is_empty() {
        println!("- no supported document media observed");
    }
    for row in coverage {
        println!(
            "- {:?}: metadata {}/{}, body {}/{}, extraction errors {}, {:?}",
            row.media_family,
            row.metadata_complete_files,
            row.observed_files,
            row.body_extracted_files,
            row.body_searchable_files,
            row.extraction_errors,
            row.body_coverage
        );
    }
    println!("Incomplete scope:");
    if scan.incomplete_scope.is_empty() {
        println!("- none");
    }
    for scope in &scan.incomplete_scope {
        println!(
            "- {} ({:?}): {}",
            scope.relative_path, scope.reason, scope.detail
        );
    }
}
