//! Documentation and human-facing artifact governance audit.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, NaiveDate, Utc};
use clap::{Args, ValueEnum};
use regex::Regex;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

mod bounded_io;
use bounded_io::AuditIo;

const CURRENT_SURFACES: &[&str] = &["CURRENT_STATE.md", "PROJECT_STATE.md"];
const STANDARD_SURFACES: &[&str] = &["NEXT_ACTIONS.md", "DECISIONS.md", "DELIVERABLES.md"];
const LOG_NAMES: &[&str] = &["AGENT_LOG.md", "AGENTS.log", "RunLog.md"];
const DELIVERABLE_EXTENSIONS: &[&str] = &[".html", ".png", ".jpg", ".jpeg", ".pdf"];
const OUTPUT_ROOTS: &[&str] = &["outputs", "web", "deliverables", "previews", "gallery"];
const LOCAL_OUTPUT_PREFIXES: &[&str] = &["target/", "book/", "dist/"];
const RECOMMENDATION_PATH_LIMIT: usize = 5;

#[derive(Debug, Clone, Args)]
pub struct ProjectAuditDocsArgs {
    /// Project repository/root directory to audit
    path: PathBuf,

    /// Governance profile to apply
    #[arg(
        long = "audit-profile",
        value_enum,
        default_value_t = DocumentationAuditProfile::Standard
    )]
    audit_profile: DocumentationAuditProfile,

    /// Optional profile directory containing adaptive wiki state
    #[arg(long)]
    adaptive_profile_dir: Option<PathBuf>,

    /// Allowed day gap before the current-state surface is considered stale
    #[arg(long, default_value_t = 0)]
    current_stale_days: i64,

    /// Line threshold for large-log warnings
    #[arg(long, default_value_t = 1000)]
    large_log_lines: usize,

    /// Output machine-readable JSON to stdout
    #[arg(long)]
    json: bool,

    /// Write JSON report to this path
    #[arg(long)]
    json_out: Option<PathBuf>,

    /// Write Markdown report to this path
    #[arg(long)]
    md_out: Option<PathBuf>,
}

#[derive(Debug, Copy, Clone, Eq, PartialEq, ValueEnum)]
pub enum DocumentationAuditProfile {
    Light,
    Standard,
    #[value(name = "research-longrun")]
    ResearchLongrun,
}

impl DocumentationAuditProfile {
    fn as_str(self) -> &'static str {
        match self {
            DocumentationAuditProfile::Light => "light",
            DocumentationAuditProfile::Standard => "standard",
            DocumentationAuditProfile::ResearchLongrun => "research-longrun",
        }
    }

    fn required_surfaces(self) -> &'static [&'static str] {
        match self {
            DocumentationAuditProfile::Light => &[],
            DocumentationAuditProfile::Standard => &["current", "DECISIONS.md", "DELIVERABLES.md"],
            DocumentationAuditProfile::ResearchLongrun => &[
                "current",
                "NEXT_ACTIONS.md",
                "DECISIONS.md",
                "DELIVERABLES.md",
            ],
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct AuditFinding {
    severity: String,
    code: String,
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    suggestion: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct DocumentationAuditResult {
    schema: String,
    generated_at: DateTime<Utc>,
    root: String,
    profile: String,
    summary: Value,
    findings: Vec<AuditFinding>,
    recommendations: Vec<AuditRecommendation>,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuditRecommendation {
    pub priority: String,
    pub kind: String,
    pub title: String,
    pub rationale: String,
    pub suggested_action: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub paths: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
}

pub fn audit_recommendations_for_project(
    path: &Path,
    audit_profile: DocumentationAuditProfile,
    large_log_lines: usize,
) -> Result<Vec<AuditRecommendation>> {
    Ok(audit_for_project(path, audit_profile, large_log_lines)?.0)
}

pub(super) fn audit_for_project(
    path: &Path,
    audit_profile: DocumentationAuditProfile,
    large_log_lines: usize,
) -> Result<(Vec<AuditRecommendation>, Value)> {
    let args = ProjectAuditDocsArgs {
        path: path.to_path_buf(),
        audit_profile,
        adaptive_profile_dir: None,
        current_stale_days: 0,
        large_log_lines,
        json: false,
        json_out: None,
        md_out: None,
    };
    let result = build_audit_result(&args)?;
    Ok((result.recommendations, result.summary["coverage"].clone()))
}

pub fn run_audit_docs(args: ProjectAuditDocsArgs) -> Result<()> {
    let result = build_audit_result(&args)?;

    if let Some(path) = &args.json_out {
        write_text(
            &resolve_output_path(path)?,
            &format!("{}\n", serde_json::to_string_pretty(&result)?),
        )?;
    }
    if let Some(path) = &args.md_out {
        write_text(&resolve_output_path(path)?, &build_markdown_report(&result))?;
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else if args.json_out.is_none() && args.md_out.is_none() {
        print_human_summary(&result);
    }

    if result.findings.iter().any(|item| item.severity == "error") {
        bail!("documentation governance audit found error findings");
    }
    Ok(())
}

fn build_audit_result(args: &ProjectAuditDocsArgs) -> Result<DocumentationAuditResult> {
    let root = expand_user(&args.path)
        .canonicalize()
        .with_context(|| format!("resolve project path {}", args.path.display()))?;
    if !root.is_dir() {
        bail!("project path is not a directory: {}", root.display());
    }

    let mut io = AuditIo::new(&root)?;
    let mut findings = Vec::new();
    let mut summary = serde_json::Map::new();
    summary.insert(
        "surfaces".to_string(),
        audit_surfaces(&root, args.audit_profile, &mut findings, &mut io),
    );
    summary.insert(
        "entrypoints".to_string(),
        audit_entrypoints(&root, &mut findings, &mut io),
    );
    summary.insert(
        "decisions".to_string(),
        audit_decision_sources(&root, &mut findings, &mut io),
    );
    summary.insert(
        "current_freshness".to_string(),
        audit_current_freshness(&root, &mut findings, args.current_stale_days, &mut io),
    );
    io.scan();
    summary.insert(
        "deliverables".to_string(),
        audit_deliverables(&root, &mut findings, &mut io)?,
    );
    summary.insert(
        "logs".to_string(),
        audit_logs(&root, &mut findings, args.large_log_lines, &mut io),
    );
    let adaptive_profile = args
        .adaptive_profile_dir
        .as_ref()
        .map(|path| expand_user(path));
    summary.insert(
        "adaptive_wiki".to_string(),
        audit_adaptive_wiki(adaptive_profile.as_deref(), &mut findings),
    );

    let coverage = io.coverage();
    if coverage["complete"] == false {
        add_finding(
            &mut findings,
            "warn",
            "audit_scope_incomplete",
            "Some paths or contents were not inspected within the owner audit policy.",
            None,
            Some("Review summary.coverage before interpreting missing or zero counts."),
        );
    }
    summary.insert("coverage".to_string(), coverage);
    let summary = Value::Object(summary);
    let recommendations = build_recommendations(&root, &summary, &findings);

    Ok(DocumentationAuditResult {
        schema: "documentation_governance_audit_v1".to_string(),
        generated_at: Utc::now(),
        root: safe_path(&root),
        profile: args.audit_profile.as_str().to_string(),
        summary,
        findings,
        recommendations,
    })
}

fn audit_surfaces(
    root: &Path,
    profile: DocumentationAuditProfile,
    findings: &mut Vec<AuditFinding>,
    io: &mut AuditIo,
) -> Value {
    let mut existing = BTreeMap::new();
    for name in CURRENT_SURFACES.iter().chain(STANDARD_SURFACES.iter()) {
        existing.insert((*name).to_string(), io.exists(&root.join(name)));
    }
    let current_present = if CURRENT_SURFACES
        .iter()
        .any(|name| existing[*name] == Some(true))
    {
        Some(true)
    } else if CURRENT_SURFACES
        .iter()
        .all(|name| existing[*name] == Some(false))
    {
        Some(false)
    } else {
        None
    };

    for surface in profile.required_surfaces() {
        if *surface == "current" {
            if current_present == Some(false) {
                add_finding(
                    findings,
                    "warn",
                    "missing_current_surface",
                    "No CURRENT_STATE.md or PROJECT_STATE.md was found.",
                    None,
                    Some(
                        "Add a compact current-state surface before relying on logs or README history.",
                    ),
                );
            }
        } else if existing.get(*surface) == Some(&Some(false)) {
            add_finding(
                findings,
                "warn",
                "missing_surface",
                &format!("{surface} is missing for profile {}.", profile.as_str()),
                Some(surface),
                Some("Add the surface or record where the equivalent current surface lives."),
            );
        }
    }

    json!({
        "surfaces": existing,
        "current_present": current_present
    })
}

fn audit_entrypoints(root: &Path, findings: &mut Vec<AuditFinding>, io: &mut AuditIo) -> Value {
    let mut summary = serde_json::Map::new();
    let current_names = CURRENT_SURFACES
        .iter()
        .chain(STANDARD_SURFACES.iter())
        .copied()
        .collect::<BTreeSet<_>>();
    for name in ["README.md", "AGENTS.md"] {
        let path = root.join(name);
        if io.exists(&path) == Some(false) {
            continue;
        }
        let Some(text) = io.read_text(&path) else {
            continue;
        };
        let mentioned_current = current_names
            .iter()
            .filter(|candidate| text.contains(**candidate))
            .map(|value| (*value).to_string())
            .collect::<Vec<_>>();
        let mentioned_logs = LOG_NAMES
            .iter()
            .filter(|candidate| text.contains(**candidate))
            .map(|value| (*value).to_string())
            .collect::<Vec<_>>();
        if !mentioned_logs.is_empty() && mentioned_current.is_empty() {
            add_finding(
                findings,
                "warn",
                "entrypoint_points_to_log_without_current_surface",
                &format!("{name} references logs but no current-state surface."),
                Some(&rel_path(root, &path)),
                Some("Point new agents to current state and next actions before the chronological log."),
            );
        }
        summary.insert(
            name.to_string(),
            json!({
                "mentioned_current_surfaces": mentioned_current,
                "mentioned_logs": mentioned_logs
            }),
        );
    }
    Value::Object(summary)
}

fn audit_deliverables(
    root: &Path,
    findings: &mut Vec<AuditFinding>,
    io: &mut AuditIo,
) -> Result<Value> {
    let output_candidates = collect_output_candidates(root, io);
    let deliverables = root.join("DELIVERABLES.md");
    let present = io.exists(&deliverables);
    if present.is_none() {
        return Ok(json!({"present": null, "inspected": false}));
    }
    if present == Some(false) {
        if !output_candidates.is_empty() {
            add_finding(
                findings,
                "info",
                "deliverables_surface_missing",
                &format!(
                    "No DELIVERABLES.md exists, while {} human-facing output candidates were found.",
                    output_candidates.len()
                ),
                None,
                Some("Add a deliverables surface if these outputs are meant for inspection or handoff."),
            );
        }
        let mut largest_output_candidates = output_candidates.clone();
        largest_output_candidates.sort_by_cached_key(|path| std::cmp::Reverse(io.file_size(path)));
        largest_output_candidates.truncate(RECOMMENDATION_PATH_LIMIT);
        return Ok(json!({
            "present": false,
            "paths": 0,
            "missing_paths": [],
            "output_candidates": output_candidates.len(),
            "largest_output_candidates": path_size_rows(root, &largest_output_candidates, io)
        }));
    }

    let Some(text) = io.read_text(&deliverables) else {
        return Ok(
            json!({"present": true, "inspected": false, "output_candidates": output_candidates.len()}),
        );
    };
    let paths = io.path_references(&text, &deliverables);
    let referenced = paths.iter().cloned().collect::<BTreeSet<_>>();
    let mut missing = Vec::new();
    let mut existing_paths = Vec::new();
    let mut uninspected = Vec::new();
    for value in &paths {
        let candidate = root.join(value);
        if io.exists(&candidate).is_none() {
            uninspected.push(value.clone());
            continue;
        }
        if io.exists(&candidate) == Some(false) {
            missing.push(value.clone());
            add_finding(
                findings,
                "error",
                "missing_deliverable_path",
                "DELIVERABLES.md references a missing path.",
                Some(value),
                Some("Update the deliverables surface or restore the artifact."),
            );
        } else {
            existing_paths.push(candidate);
        }
    }

    let retention_managed =
        collect_retention_managed_outputs(root, &output_candidates, &referenced, io);
    let referenced_outputs = paths
        .iter()
        .filter(|value| has_deliverable_extension(Path::new(value)))
        .count();
    if !output_candidates.is_empty() && referenced_outputs == 0 {
        add_finding(
            findings,
            "warn",
            "deliverables_without_human_outputs",
            "DELIVERABLES.md exists but does not reference HTML, image, or PDF outputs.",
            Some(&rel_path(root, &deliverables)),
            Some("Link the selected inspection artifacts from the deliverables surface."),
        );
    }

    let mut unreferenced = Vec::new();
    let mut manifest_covered = Vec::new();
    for path in &output_candidates {
        let path_rel = rel_path(root, path);
        if referenced.contains(&path_rel) {
            continue;
        }
        if manifest_covers_output(path, root, &referenced, io) {
            manifest_covered.push(path.clone());
            continue;
        }
        if equivalent_latest_alias_is_referenced(path, root, &referenced, io)
            || retention_managed.contains(path)
        {
            continue;
        }
        unreferenced.push(path.clone());
    }
    unreferenced.sort_by_cached_key(|path| std::cmp::Reverse(io.file_size(path)));
    let largest_unreferenced = unreferenced.iter().take(10).cloned().collect::<Vec<_>>();
    let retention_managed_rows = retention_managed.iter().cloned().collect::<Vec<_>>();

    if !largest_unreferenced.is_empty() {
        add_finding(
            findings,
            "info",
            "unreferenced_human_output_candidates",
            &format!(
                "{} human-facing output candidates are not listed in DELIVERABLES.md.",
                unreferenced.len()
            ),
            Some(&rel_path(root, &deliverables)),
            Some("Review the largest candidates and promote only the outputs useful for inspection or handoff."),
        );
    }

    let latest_aliases = audit_latest_aliases(root, &existing_paths, findings, io)?;
    let local_outputs = existing_paths
        .iter()
        .map(|path| rel_path(root, path))
        .filter(|value| {
            LOCAL_OUTPUT_PREFIXES
                .iter()
                .any(|prefix| value.starts_with(prefix))
        })
        .collect::<Vec<_>>();

    Ok(json!({
        "present": true,
        "paths": paths.len(),
        "missing_paths": missing,
        "uninspected_paths": uninspected,
        "output_candidates": output_candidates.len(),
        "referenced_human_outputs": referenced_outputs,
        "manifest_covered_human_outputs": manifest_covered.len(),
        "retention_managed_human_outputs": retention_managed.len(),
        "retention_managed_human_output_paths": path_size_rows(root, &retention_managed_rows, io),
        "unreferenced_human_outputs": unreferenced.len(),
        "unreferenced_human_output_review_sample": path_size_rows(root, &largest_unreferenced, io),
        "largest_unreferenced_human_outputs": path_size_rows(root, &largest_unreferenced, io),
        "latest_aliases": latest_aliases,
        "local_outputs": local_outputs
    }))
}

fn audit_decision_sources(
    root: &Path,
    findings: &mut Vec<AuditFinding>,
    io: &mut AuditIo,
) -> Value {
    let decisions = root.join("DECISIONS.md");
    if io.exists(&decisions) == Some(false) {
        return json!({"present": false, "sources": 0, "missing_sources": []});
    }
    let Some(text) = io.read_text(&decisions) else {
        return json!({"present": true, "inspected": false});
    };
    let sources = io.path_references(&text, &decisions);
    let mut missing = Vec::new();
    for value in &sources {
        if io.exists(&root.join(value)) == Some(false) {
            missing.push(value.clone());
            add_finding(
                findings,
                "warn",
                "missing_decision_source",
                "DECISIONS.md references a missing source path.",
                Some(value),
                Some("Update the decision source or add a transition note."),
            );
        }
    }
    json!({"present": true, "sources": sources.len(), "missing_sources": missing})
}

fn audit_current_freshness(
    root: &Path,
    findings: &mut Vec<AuditFinding>,
    stale_days: i64,
    io: &mut AuditIo,
) -> Value {
    let Some(current) = CURRENT_SURFACES
        .iter()
        .map(|name| root.join(name))
        .find(|path| io.exists(path) == Some(true))
    else {
        return json!({"present": false});
    };

    let Some(text) = io.read_text(&current) else {
        return json!({"present": true, "inspected": false});
    };
    let updated = parse_updated_date(&text);
    let mut summary = serde_json::Map::new();
    summary.insert("present".to_string(), json!(true));
    summary.insert("path".to_string(), json!(rel_path(root, &current)));
    summary.insert(
        "updated".to_string(),
        updated
            .map(|date| json!(date.to_string()))
            .unwrap_or(Value::Null),
    );
    if updated.is_none() {
        add_finding(
            findings,
            "warn",
            "missing_current_updated_date",
            "Current-state surface is missing an Updated: YYYY-MM-DD line.",
            Some(&rel_path(root, &current)),
            Some("Add an Updated line so stale current surfaces can be detected."),
        );
        return Value::Object(summary);
    }

    let newest = ["DECISIONS.md", "DELIVERABLES.md", "NEXT_ACTIONS.md"]
        .iter()
        .map(|name| root.join(name))
        .filter_map(|path| {
            io.metadata(&path)
                .and_then(|m| m.modified().ok())
                .map(|time| (path, time))
        })
        .max_by_key(|(_, time)| *time);

    if let Some((newest, time)) = newest {
        let datetime: DateTime<Utc> = time.into();
        let newest_date = Some(datetime.date_naive());
        summary.insert(
            "newest_watched_surface".to_string(),
            json!(rel_path(root, &newest)),
        );
        if let Some(newest_date) = newest_date {
            summary.insert(
                "newest_watched_date".to_string(),
                json!(newest_date.to_string()),
            );
            if (newest_date - updated.expect("checked")).num_days() > stale_days {
                add_finding(
                    findings,
                    "warn",
                    "current_surface_stale",
                    "Current-state surface is older than another current governance surface.",
                    Some(&rel_path(root, &current)),
                    Some("Refresh the current-state summary after changing next actions, decisions, or deliverables."),
                );
            }
        }
    }

    Value::Object(summary)
}

fn audit_logs(
    root: &Path,
    findings: &mut Vec<AuditFinding>,
    large_log_lines: usize,
    io: &mut AuditIo,
) -> Value {
    let mut logs = Vec::new();
    for path in io.files().to_vec() {
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !LOG_NAMES.contains(&name) {
            continue;
        }
        let Some(text) = io.read_text(&path) else {
            logs.push(json!({"path": rel_path(root, &path), "lines": null, "inspected": false}));
            continue;
        };
        let line_count = text.lines().count();
        if line_count >= large_log_lines {
            add_finding(
                findings,
                "warn",
                "large_log",
                &format!("Log has {line_count} lines."),
                Some(&rel_path(root, &path)),
                Some("Keep the log as evidence, but maintain a smaller current-state surface."),
            );
        }
        logs.push(json!({"path": rel_path(root, &path), "lines": line_count}));
    }
    logs.sort_by_key(|row| {
        std::cmp::Reverse(row.get("lines").and_then(Value::as_u64).unwrap_or_default())
    });
    json!({"logs": logs})
}

fn audit_adaptive_wiki(profile_dir: Option<&Path>, findings: &mut Vec<AuditFinding>) -> Value {
    let Some(profile_dir) = profile_dir else {
        return Value::Null;
    };
    if !profile_dir.exists() {
        add_finding(
            findings,
            "error",
            "adaptive_profile_missing",
            "Adaptive wiki profile dir is missing.",
            Some(&safe_path(profile_dir)),
            None,
        );
        return json!({"present": false});
    }
    let entries = profile_dir.join("adaptive_wiki_entries.json");
    let candidates = profile_dir.join("adaptive_wiki_candidates.json");
    let vault_index = profile_dir.join("wiki-vault").join("index.md");
    let canonical_paths = [entries.as_path(), candidates.as_path()]
        .into_iter()
        .filter(|path| path.exists())
        .collect::<Vec<_>>();
    let mut summary = serde_json::Map::new();
    summary.insert("profile_dir".to_string(), json!(safe_path(profile_dir)));
    summary.insert("entries_present".to_string(), json!(entries.exists()));
    summary.insert("candidates_present".to_string(), json!(candidates.exists()));
    summary.insert(
        "vault_index_present".to_string(),
        json!(vault_index.exists()),
    );
    summary.insert(
        "canonical_source_count".to_string(),
        json!(canonical_paths.len()),
    );

    if !canonical_paths.is_empty() && vault_index.exists() {
        let newest_canonical = canonical_paths
            .iter()
            .max_by_key(|path| modified_time(path))
            .expect("non-empty canonical paths");
        let stale = modified_time(newest_canonical) > modified_time(&vault_index);
        summary.insert(
            "projection_state".to_string(),
            json!(if stale { "stale" } else { "fresh" }),
        );
        summary.insert("projection_stale".to_string(), json!(stale));
        summary.insert(
            "canonical_newest_path".to_string(),
            json!(safe_path(newest_canonical)),
        );
        if stale {
            add_finding(
                findings,
                "warn",
                "stale_adaptive_wiki_projection",
                "Canonical adaptive wiki state is newer than wiki-vault/index.md.",
                Some(&safe_path(&vault_index)),
                Some("Re-export the human markdown projection or mark it stale in the operator surface."),
            );
        }
    } else if !canonical_paths.is_empty() && !vault_index.exists() {
        summary.insert("projection_state".to_string(), json!("missing"));
        add_finding(
            findings,
            "warn",
            "missing_adaptive_wiki_projection",
            "Canonical adaptive wiki state exists without a wiki-vault/index.md human projection.",
            Some(&safe_path(profile_dir)),
            Some("Export the markdown projection if humans need to inspect the wiki."),
        );
    } else {
        summary.insert("projection_state".to_string(), json!("empty_canonical"));
    }

    Value::Object(summary)
}

fn collect_output_candidates(root: &Path, io: &AuditIo) -> Vec<PathBuf> {
    io.files()
        .iter()
        .filter(|path| {
            OUTPUT_ROOTS
                .iter()
                .any(|name| path.starts_with(root.join(name)))
                && has_deliverable_extension(path)
        })
        .cloned()
        .collect()
}

fn collect_retention_managed_outputs(
    root: &Path,
    output_candidates: &[PathBuf],
    referenced: &BTreeSet<String>,
    io: &mut AuditIo,
) -> BTreeSet<PathBuf> {
    let retention_review = root.join("RETENTION_REVIEW.md");
    if io.exists(&retention_review) == Some(false) {
        return BTreeSet::new();
    }
    let text = io.read_text(&retention_review).unwrap_or_default();
    let managed_refs = io
        .path_references(&text, &retention_review)
        .into_iter()
        .collect::<BTreeSet<_>>();
    output_candidates
        .iter()
        .filter(|path| {
            let path_rel = rel_path(root, path);
            !referenced.contains(&path_rel) && managed_refs.contains(&path_rel)
        })
        .cloned()
        .collect()
}

fn equivalent_latest_alias_is_referenced(
    path: &Path,
    root: &Path,
    referenced: &BTreeSet<String>,
    io: &mut AuditIo,
) -> bool {
    let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
        return false;
    };
    if name.contains("latest") {
        return false;
    }
    let path_rel = rel_path(root, path);
    if referenced.contains(&path_rel) {
        return true;
    }
    for reference in referenced {
        let ref_path = root.join(reference);
        let Some(ref_name) = ref_path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if ref_path.parent() != path.parent()
            || !ref_name.contains("latest")
            || ref_path.extension() != path.extension()
            || io.exists(&ref_path) != Some(true)
            || io.file_size(&ref_path) != io.file_size(path)
        {
            continue;
        }
        if let (Some(left), Some(right)) = (io.hash(&ref_path), io.hash(path)) {
            if left == right {
                return true;
            }
        }
    }
    false
}

fn manifest_covers_output(
    path: &Path,
    root: &Path,
    referenced: &BTreeSet<String>,
    io: &mut AuditIo,
) -> bool {
    if !has_deliverable_extension(path) {
        return false;
    }
    referenced.iter().any(|reference| {
        let ref_path = root.join(reference);
        ref_path.parent() == path.parent()
            && io.exists(&ref_path) == Some(true)
            && ref_path.extension().and_then(|value| value.to_str()) == Some("json")
            && ref_path
                .file_name()
                .and_then(|value| value.to_str())
                .is_some_and(|name| name.to_ascii_lowercase().contains("manifest"))
    })
}

fn audit_latest_aliases(
    root: &Path,
    existing_paths: &[PathBuf],
    findings: &mut Vec<AuditFinding>,
    io: &mut AuditIo,
) -> Result<Vec<Value>> {
    let mut aliases = Vec::new();
    for path in existing_paths {
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if !name.contains("latest") || !path.is_file() {
            continue;
        }
        let Some(parent) = path.parent() else {
            continue;
        };
        let prefix = name.split("latest").next().unwrap_or_default();
        let suffix = name.split("latest").nth(1).unwrap_or_default();
        let Some(alias_hash) = io.hash(path) else {
            aliases.push(json!({"path": rel_path(root, path), "inspected": false}));
            continue;
        };
        let mut matching = Vec::new();
        let mut candidate_siblings = 0usize;
        let mut complete = io.sibling_inventory_complete(parent);
        for sibling in io.files().to_vec() {
            let Some(sibling_name) = sibling.file_name().and_then(|value| value.to_str()) else {
                continue;
            };
            if sibling == *path
                || sibling.parent() != Some(parent)
                || sibling_name.contains("latest")
                || !sibling_name.starts_with(prefix)
                || !sibling_name.ends_with(suffix)
            {
                continue;
            }
            candidate_siblings += 1;
            match io.hash(&sibling) {
                Some(hash) if hash == alias_hash => matching.push(rel_path(root, &sibling)),
                None => complete = false,
                _ => {}
            }
        }
        if matching.is_empty() && complete {
            add_finding(
                findings,
                "warn",
                "latest_alias_without_matching_artifact",
                "A latest deliverable alias has no same-content tagged sibling.",
                Some(&rel_path(root, path)),
                Some("Confirm whether the alias is still meaningful or replace it with a stable tagged artifact."),
            );
        }
        aliases.push(json!({
            "path": rel_path(root, path),
            "candidate_siblings": candidate_siblings,
            "complete": complete,
            "matching_siblings": matching
        }));
    }
    Ok(aliases)
}

fn extract_backtick_paths(text: &str) -> Vec<String> {
    let regex = Regex::new(r"`([^`]+)`").expect("valid path regex");
    let mut paths = Vec::new();
    for capture in regex.captures_iter(text) {
        let value = capture
            .get(1)
            .map(|matched| matched.as_str().trim())
            .unwrap_or_default();
        if value.is_empty()
            || value.contains(' ')
            || value.starts_with("http://")
            || value.starts_with("https://")
            || value.starts_with("forager ")
            || value.starts_with("python ")
            || value.starts_with("./.venv")
            || value.chars().any(|ch| matches!(ch, '*' | '<' | '>' | '|'))
        {
            continue;
        }
        let file_name_has_dot = Path::new(value)
            .file_name()
            .and_then(|item| item.to_str())
            .is_some_and(|name| name.contains('.'));
        if value.contains('/') || file_name_has_dot {
            paths.push(value.to_string());
        }
    }
    paths
}

fn parse_updated_date(text: &str) -> Option<NaiveDate> {
    let regex = Regex::new(r"(?m)^Updated:\s*(\d{4}-\d{2}-\d{2})\s*$").expect("valid date regex");
    let value = regex.captures(text)?.get(1)?.as_str();
    NaiveDate::parse_from_str(value, "%Y-%m-%d").ok()
}

fn has_deliverable_extension(path: &Path) -> bool {
    let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
        return false;
    };
    let normalized = format!(".{}", extension.to_ascii_lowercase());
    DELIVERABLE_EXTENSIONS.contains(&normalized.as_str())
}

fn path_size_rows(root: &Path, paths: &[PathBuf], io: &mut AuditIo) -> Vec<Value> {
    paths
        .iter()
        .map(|path| json!({"path": rel_path(root, path), "bytes": io.file_size(path)}))
        .collect()
}

fn modified_time(path: &Path) -> std::time::SystemTime {
    path.metadata()
        .and_then(|metadata| metadata.modified())
        .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
}

fn add_finding(
    findings: &mut Vec<AuditFinding>,
    severity: &str,
    code: &str,
    message: &str,
    path: Option<&str>,
    suggestion: Option<&str>,
) {
    findings.push(AuditFinding {
        severity: severity.to_string(),
        code: code.to_string(),
        message: message.to_string(),
        path: path.map(ToString::to_string),
        suggestion: suggestion.map(ToString::to_string),
    });
}

fn build_recommendations(
    root: &Path,
    summary: &Value,
    findings: &[AuditFinding],
) -> Vec<AuditRecommendation> {
    let mut recommendations = Vec::new();
    if summary.pointer("/coverage/complete") == Some(&json!(false)) {
        recommendations.push(AuditRecommendation {
            priority: "normal".into(), kind: "review_audit_coverage".into(),
            title: "Review documentation audit coverage".into(),
            rationale: "The bounded owner audit skipped paths or could not inspect some contents; counts cover only inspected files.".into(),
            suggested_action: "Inspect summary.coverage and select a bounded follow-up scope for unresolved paths.".into(),
            paths: vec![], command: None,
        });
    }

    let missing_deliverable_paths = findings
        .iter()
        .filter(|finding| finding.code == "missing_deliverable_path")
        .filter_map(|finding| finding.path.clone())
        .take(RECOMMENDATION_PATH_LIMIT)
        .collect::<Vec<_>>();
    if !missing_deliverable_paths.is_empty() {
        recommendations.push(AuditRecommendation {
            priority: "urgent".to_string(),
            kind: "repair_deliverables_surface".to_string(),
            title: "Repair missing deliverable references".to_string(),
            rationale:
                "DELIVERABLES.md points to paths that cannot be inspected from the current checkout."
                    .to_string(),
            suggested_action:
                "Restore the artifact or update DELIVERABLES.md before using the deliverables surface for handoff."
                    .to_string(),
            paths: missing_deliverable_paths,
            command: Some(format!(
                "forager project audit-docs {} --audit-profile standard --json",
                shell_arg(&safe_path(root))
            )),
        });
    }

    let missing_surfaces = findings
        .iter()
        .filter(|finding| {
            matches!(
                finding.code.as_str(),
                "missing_surface" | "missing_current_surface"
            )
        })
        .map(|finding| {
            finding
                .path
                .clone()
                .unwrap_or_else(|| "PROJECT_STATE.md".to_string())
        })
        .take(RECOMMENDATION_PATH_LIMIT)
        .collect::<Vec<_>>();
    if !missing_surfaces.is_empty() {
        recommendations.push(AuditRecommendation {
            priority: "high".to_string(),
            kind: "create_governance_surfaces".to_string(),
            title: "Create compact governance surfaces".to_string(),
            rationale:
                "The project is missing one or more shallow surfaces that let humans avoid raw logs."
                    .to_string(),
            suggested_action:
                "Create or refresh the listed surface(s), then rerun the audit before starting a long-running handoff."
                    .to_string(),
            paths: missing_surfaces,
            command: None,
        });
    }

    let deliverables = summary.get("deliverables").unwrap_or(&Value::Null);
    let deliverables_present = deliverables
        .get("present")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let output_candidates = deliverables
        .get("output_candidates")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    if !deliverables_present && output_candidates > 0 {
        recommendations.push(AuditRecommendation {
            priority: "high".to_string(),
            kind: "create_deliverables_surface".to_string(),
            title: "Create a focused deliverables surface".to_string(),
            rationale: format!(
                "{output_candidates} human-facing output candidate(s) exist, but no DELIVERABLES.md was found."
            ),
            suggested_action:
                "Review the sample paths, promote only inspection-worthy outputs to DELIVERABLES.md, and leave the rest in source artifact folders."
                    .to_string(),
            paths: row_paths(
                deliverables.get("largest_output_candidates"),
                RECOMMENDATION_PATH_LIMIT,
            ),
            command: None,
        });
    }

    let unreferenced_count = deliverables
        .get("unreferenced_human_outputs")
        .and_then(Value::as_u64)
        .unwrap_or_default();
    if unreferenced_count > 0 {
        recommendations.push(AuditRecommendation {
            priority: "normal".to_string(),
            kind: "review_human_output_candidates".to_string(),
            title: "Review unpromoted human-facing outputs".to_string(),
            rationale: format!(
                "{unreferenced_count} human-facing output candidate(s) are outside the active deliverables surface."
            ),
            suggested_action:
                "Promote selected outputs to DELIVERABLES.md, or record non-active outputs in RETENTION_REVIEW.md with keep/archive/delete intent."
                    .to_string(),
            paths: row_paths(
                deliverables.get("largest_unreferenced_human_outputs"),
                RECOMMENDATION_PATH_LIMIT,
            ),
            command: None,
        });
    }

    let latest_alias_paths = findings
        .iter()
        .filter(|finding| finding.code == "latest_alias_without_matching_artifact")
        .filter_map(|finding| finding.path.clone())
        .take(RECOMMENDATION_PATH_LIMIT)
        .collect::<Vec<_>>();
    if !latest_alias_paths.is_empty() {
        recommendations.push(AuditRecommendation {
            priority: "normal".to_string(),
            kind: "verify_latest_aliases".to_string(),
            title: "Verify latest aliases before handoff".to_string(),
            rationale:
                "A latest alias is deliverable-facing, but it has no same-content stable sibling."
                    .to_string(),
            suggested_action:
                "Replace the alias with a tagged artifact or regenerate the matching stable artifact before sharing."
                    .to_string(),
            paths: latest_alias_paths,
            command: None,
        });
    }

    let large_logs = findings
        .iter()
        .filter(|finding| finding.code == "large_log")
        .filter_map(|finding| finding.path.clone())
        .take(RECOMMENDATION_PATH_LIMIT)
        .collect::<Vec<_>>();
    if !large_logs.is_empty() {
        recommendations.push(AuditRecommendation {
            priority: "normal".to_string(),
            kind: "summarize_large_logs".to_string(),
            title: "Keep logs as evidence, not the first-read surface".to_string(),
            rationale:
                "Large logs are useful for audit, but they are a poor operator entrypoint."
                    .to_string(),
            suggested_action:
                "Refresh PROJECT_STATE.md or CURRENT_STATE.md with the current truth and link to the log only as evidence."
                    .to_string(),
            paths: large_logs,
            command: None,
        });
    }

    let stale_current = findings
        .iter()
        .find(|finding| finding.code == "current_surface_stale")
        .and_then(|finding| finding.path.clone());
    if let Some(path) = stale_current {
        recommendations.push(AuditRecommendation {
            priority: "normal".to_string(),
            kind: "refresh_current_surface".to_string(),
            title: "Refresh the current-state surface".to_string(),
            rationale:
                "Another governance surface is newer than the current-state summary.".to_string(),
            suggested_action:
                "Update the current-state file after reviewing decisions, deliverables, and next actions."
                    .to_string(),
            paths: vec![path],
            command: None,
        });
    }

    let adaptive_projection_paths = findings
        .iter()
        .filter(|finding| {
            matches!(
                finding.code.as_str(),
                "stale_adaptive_wiki_projection" | "missing_adaptive_wiki_projection"
            )
        })
        .filter_map(|finding| finding.path.clone())
        .take(RECOMMENDATION_PATH_LIMIT)
        .collect::<Vec<_>>();
    if !adaptive_projection_paths.is_empty() {
        let profile_name = summary
            .pointer("/adaptive_wiki/profile_dir")
            .and_then(Value::as_str)
            .and_then(profile_name_from_profile_dir);
        recommendations.push(AuditRecommendation {
            priority: "normal".to_string(),
            kind: "reexport_adaptive_wiki_projection".to_string(),
            title: "Re-export the adaptive wiki markdown vault".to_string(),
            rationale:
                "Canonical adaptive wiki state is ahead of the human markdown projection."
                    .to_string(),
            suggested_action:
                "Re-export the profile wiki vault, then rerun the documentation audit with the same adaptive profile dir."
                    .to_string(),
            paths: adaptive_projection_paths,
            command: profile_name.map(|profile| {
                format!(
                    "forager -p {} offdesk wiki export-markdown",
                    shell_arg(&profile)
                )
            }),
        });
    }

    recommendations
}

fn profile_name_from_profile_dir(path: &str) -> Option<String> {
    let path = Path::new(path);
    if path.parent()?.file_name()?.to_str()? != "profiles" {
        return None;
    }
    path.file_name()
        .and_then(|value| value.to_str())
        .map(ToString::to_string)
}

fn row_paths(rows: Option<&Value>, limit: usize) -> Vec<String> {
    rows.and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(limit)
                .filter_map(|item| item.get("path").and_then(Value::as_str))
                .map(ToString::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn summary_u64(summary: &Value, pointer: &str) -> u64 {
    summary
        .pointer(pointer)
        .and_then(Value::as_u64)
        .unwrap_or_default()
}

fn build_markdown_report(result: &DocumentationAuditResult) -> String {
    let mut output = String::new();
    output.push_str("# Documentation Governance Audit\n\n");
    output.push_str(&format!("- Root: `{}`\n", result.root));
    output.push_str(&format!("- Profile: `{}`\n", result.profile));
    output.push_str(&format!("- Generated: `{}`\n", result.generated_at));
    output.push_str(&format!("- Findings: `{}`\n", result.findings.len()));
    output.push_str(&format!(
        "- Recommendations: `{}`\n\n",
        result.recommendations.len()
    ));
    output.push_str("## Summary\n\n");
    output.push_str(&format!(
        "- Coverage: `{}`. Counts describe inspected files only.\n",
        if result.summary.pointer("/coverage/complete") == Some(&json!(true)) {
            "complete within owner policy"
        } else {
            "partial; inspect JSON coverage diagnostics"
        }
    ));
    output.push_str(&format!(
        "- Output candidates: `{}`\n",
        summary_u64(&result.summary, "/deliverables/output_candidates")
    ));
    output.push_str(&format!(
        "- Referenced human outputs: `{}`\n",
        summary_u64(&result.summary, "/deliverables/referenced_human_outputs")
    ));
    output.push_str(&format!(
        "- Unpromoted human outputs: `{}`\n",
        summary_u64(&result.summary, "/deliverables/unreferenced_human_outputs")
    ));
    output.push_str(&format!(
        "- Retention-managed human outputs: `{}`\n",
        summary_u64(
            &result.summary,
            "/deliverables/retention_managed_human_outputs"
        )
    ));
    if result.findings.is_empty() {
        output.push_str("- No findings.\n");
    } else {
        for severity in ["error", "warn", "info"] {
            let count = result
                .findings
                .iter()
                .filter(|finding| finding.severity == severity)
                .count();
            if count > 0 {
                output.push_str(&format!("- `{severity}`: {count}\n"));
            }
        }
    }
    output.push_str("\n## Recommendations\n\n");
    if result.recommendations.is_empty() {
        output.push_str("_none_\n\n");
    }
    for recommendation in &result.recommendations {
        output.push_str(&format!(
            "### {} `{}`\n\n",
            recommendation.priority.to_uppercase(),
            recommendation.kind
        ));
        output.push_str(&recommendation.title);
        output.push('\n');
        output.push_str(&format!("- Rationale: {}\n", recommendation.rationale));
        output.push_str(&format!(
            "- Suggested action: {}\n",
            recommendation.suggested_action
        ));
        if !recommendation.paths.is_empty() {
            output.push_str("- Focus paths:\n");
            for path in recommendation.paths.iter().take(RECOMMENDATION_PATH_LIMIT) {
                output.push_str(&format!("  - `{path}`\n"));
            }
        }
        if let Some(command) = &recommendation.command {
            output.push_str(&format!("- Recheck: `{command}`\n"));
        }
        output.push('\n');
    }
    output.push_str("\n## Findings\n\n");
    if result.findings.is_empty() {
        output.push_str("_none_\n\n");
    }
    for finding in &result.findings {
        output.push_str(&format!(
            "### {} `{}`\n\n",
            finding.severity.to_uppercase(),
            finding.code
        ));
        output.push_str(&finding.message);
        output.push('\n');
        if let Some(path) = &finding.path {
            output.push_str(&format!("- Path: `{path}`\n"));
        }
        if let Some(suggestion) = &finding.suggestion {
            output.push_str(&format!("- Suggested action: {suggestion}\n"));
        }
        output.push('\n');
    }
    output.push_str(
        "For the full machine-readable summary, run this audit with `--json` or `--json-out`.\n",
    );
    output
}

fn print_human_summary(result: &DocumentationAuditResult) {
    println!("Documentation governance audit");
    println!("  root:     {}", result.root);
    println!("  profile:  {}", result.profile);
    println!("  findings: {}", result.findings.len());
    println!("  recs:     {}", result.recommendations.len());
    println!(
        "  coverage: {}",
        if result.summary.pointer("/coverage/complete") == Some(&json!(true)) {
            "complete within owner policy"
        } else {
            "partial; use --json to inspect skipped paths and limits"
        }
    );
    for severity in ["error", "warn", "info"] {
        let count = result
            .findings
            .iter()
            .filter(|finding| finding.severity == severity)
            .count();
        if count > 0 {
            println!("  {severity}: {count}");
        }
    }
}

fn write_text(path: &Path, content: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    fs::write(path, content).with_context(|| format!("write {}", path.display()))
}

fn resolve_output_path(path: &Path) -> Result<PathBuf> {
    let expanded = expand_user(path);
    if expanded.is_absolute() {
        Ok(expanded)
    } else {
        Ok(std::env::current_dir()?.join(expanded))
    }
}

fn expand_user(path: &Path) -> PathBuf {
    let Some(raw) = path.to_str() else {
        return path.to_path_buf();
    };
    if raw == "~" {
        return dirs::home_dir().unwrap_or_else(|| path.to_path_buf());
    }
    if let Some(stripped) = raw.strip_prefix("~/") {
        if let Some(home) = dirs::home_dir() {
            return home.join(stripped);
        }
    }
    path.to_path_buf()
}

fn safe_path(path: &Path) -> String {
    path.to_string_lossy().replace(['\n', '\r'], " ")
}

fn shell_arg(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '/' | '.' | '_' | '-' | ':'))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn rel_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
        .replace(['\n', '\r'], " ")
}

#[cfg(test)]
mod bounded_tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    #[test]
    fn uninspected_references_and_oversized_aliases_never_become_missing_or_verified() -> Result<()>
    {
        let root = tempdir()?;
        let external = tempdir()?;
        fs::write(external.path().join("report.pdf"), "outside")?;
        symlink(external.path(), root.path().join("outputs"))?;
        fs::write(
            root.path().join("DELIVERABLES.md"),
            "`outputs/report.pdf`\n`../unknown.pdf`\n",
        )?;
        let (recommendations, coverage) =
            audit_for_project(root.path(), DocumentationAuditProfile::Standard, 10)?;
        assert_eq!(coverage["complete"], false);
        assert!(recommendations
            .iter()
            .any(|r| r.kind == "review_audit_coverage"));
        assert!(!recommendations
            .iter()
            .any(|r| r.kind == "repair_deliverables_surface"));
        fs::remove_file(root.path().join("outputs"))?;
        fs::create_dir(root.path().join("outputs"))?;
        let file = fs::File::create(root.path().join("outputs/report_latest.pdf"))?;
        file.set_len(5 * 1024 * 1024)?;
        fs::write(
            root.path().join("DELIVERABLES.md"),
            "`outputs/report_latest.pdf`\n",
        )?;
        let (recommendations, coverage) =
            audit_for_project(root.path(), DocumentationAuditProfile::Standard, 10)?;
        assert_eq!(coverage["complete"], false);
        assert!(!recommendations
            .iter()
            .any(|r| r.kind == "verify_latest_aliases"));
        Ok(())
    }
}
