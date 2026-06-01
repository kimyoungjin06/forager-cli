//! Project and profile artifact inventory read model.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use clap::Args;
use regex::Regex;
use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use crate::offdesk::operator_safe_text;
use crate::session::get_profile_dir;

const ARTIFACT_INDEX_SCHEMA: &str = "artifact_index.v1";
const DELIVERABLE_EXTENSIONS: &[&str] = &[".html", ".png", ".jpg", ".jpeg", ".pdf"];
const OUTPUT_ROOTS: &[&str] = &["outputs", "web", "deliverables", "previews", "gallery"];
const MAX_INDEX_ENTRIES: usize = 240;
const MAX_HUMAN_ROWS: usize = 12;

#[derive(Debug, Clone, Args)]
pub struct ProjectArtifactIndexArgs {
    /// Project repository/root directory to scan. Defaults to the current directory.
    path: Option<PathBuf>,

    /// Stable project key used to filter profile-local Forager artifacts
    #[arg(long)]
    project_key: Option<String>,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndex {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_root: Option<String>,
    summary: ArtifactIndexSummary,
    entries: Vec<ArtifactIndexEntry>,
    redaction: ArtifactIndexRedaction,
    authority: ArtifactIndexAuthority,
}

#[derive(Debug, Clone, Default, Serialize)]
struct ArtifactIndexSummary {
    total_entries: usize,
    present_entries: usize,
    missing_entries: usize,
    review_required_entries: usize,
    disposal_candidate_entries: usize,
    human_facing_entries: usize,
    truncated_entries: usize,
    by_retention_class: BTreeMap<String, usize>,
    by_source: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexEntry {
    id: String,
    label: String,
    source: String,
    kind: String,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    relative_path: Option<String>,
    present: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    bytes: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    modified_at: Option<DateTime<Utc>>,
    retention_class: String,
    review_status: String,
    why_it_matters: String,
    refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexRedaction {
    operator_safe: bool,
    path_policy: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct ArtifactIndexAuthority {
    read_only: bool,
    does_not_authorize: Vec<&'static str>,
}

struct EntryInput {
    label: String,
    source: String,
    kind: String,
    path: String,
    relative_path: Option<String>,
    present_override: Option<bool>,
    retention_class: String,
    review_status: String,
    why_it_matters: String,
    refs: Vec<String>,
}

struct PathEntryInput<'a> {
    label: &'a str,
    source: &'a str,
    kind: &'a str,
    path: &'a Path,
    retention_class: &'a str,
    review_status: &'a str,
    why_it_matters: &'a str,
    refs: Vec<String>,
}

pub async fn run(profile: &str, args: ProjectArtifactIndexArgs) -> Result<()> {
    let project_root = resolve_project_root(args.path.as_deref())?;
    let project_key = args
        .project_key
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default_project_key(project_root.as_deref()));
    let index = build_artifact_index(profile, Some(&project_key), project_root.as_deref())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&index)?);
    } else {
        print!("{}", human_summary(&serde_json::to_value(&index)?));
    }
    Ok(())
}

pub(crate) fn build_profile_artifact_index_value(
    profile: &str,
    project_key: Option<&str>,
) -> Result<Value> {
    serde_json::to_value(build_artifact_index(profile, project_key, None)?)
        .context("serialize artifact index")
}

pub(crate) fn review_surface_projection(index: &Value) -> Value {
    let entries = index
        .get("entries")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .take(20)
                .map(|entry| {
                    json!({
                        "id": entry.get("id").cloned().unwrap_or(Value::Null),
                        "label": entry.get("label").cloned().unwrap_or(Value::Null),
                        "source": entry.get("source").cloned().unwrap_or(Value::Null),
                        "kind": entry.get("kind").cloned().unwrap_or(Value::Null),
                        "path": entry.get("path").cloned().unwrap_or(Value::Null),
                        "present": entry.get("present").cloned().unwrap_or(Value::Null),
                        "retention_class": entry.get("retention_class").cloned().unwrap_or(Value::Null),
                        "review_status": entry.get("review_status").cloned().unwrap_or(Value::Null),
                        "why_it_matters": entry.get("why_it_matters").cloned().unwrap_or(Value::Null)
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    json!({
        "schema": index.get("schema").cloned().unwrap_or(Value::String(ARTIFACT_INDEX_SCHEMA.to_string())),
        "summary": index.get("summary").cloned().unwrap_or(Value::Object(Default::default())),
        "entries": entries,
        "projection_policy": "first_20_entries_summary_first"
    })
}

fn build_artifact_index(
    profile: &str,
    project_key: Option<&str>,
    project_root: Option<&Path>,
) -> Result<ArtifactIndex> {
    let profile_name = if profile.is_empty() {
        "default"
    } else {
        profile
    };
    let profile_dir = get_profile_dir(profile_name)?;
    let safe_project_key = project_key
        .map(operator_safe_text)
        .unwrap_or_else(|| "all".to_string());
    let mut entries = BTreeMap::new();

    if let Some(root) = project_root {
        collect_project_outputs(root, &safe_project_key, &mut entries)?;
    }
    collect_profile_artifacts(&profile_dir, project_key, &mut entries)?;

    let mut entries = entries.into_values().collect::<Vec<_>>();
    entries.sort_by(|left, right| {
        left.source
            .cmp(&right.source)
            .then(left.retention_class.cmp(&right.retention_class))
            .then(left.path.cmp(&right.path))
    });
    let total_entries = entries.len();
    let truncated_entries = total_entries.saturating_sub(MAX_INDEX_ENTRIES);
    entries.truncate(MAX_INDEX_ENTRIES);
    let summary = summarize_entries(&entries, total_entries, truncated_entries);

    Ok(ArtifactIndex {
        schema: ARTIFACT_INDEX_SCHEMA,
        generated_at: Utc::now(),
        profile: operator_safe_text(profile_name),
        project_key: safe_project_key,
        project_root: project_root.map(|root| operator_safe_path(root.to_string_lossy().as_ref())),
        summary,
        entries,
        redaction: ArtifactIndexRedaction {
            operator_safe: true,
            path_policy: "summary_first_paths_in_json",
        },
        authority: ArtifactIndexAuthority {
            read_only: true,
            does_not_authorize: vec![
                "delete",
                "move",
                "archive",
                "publish",
                "accepting output as truth without closeout receipt review",
            ],
        },
    })
}

fn resolve_project_root(path: Option<&Path>) -> Result<Option<PathBuf>> {
    let explicit = path.is_some();
    let path = path
        .map(PathBuf::from)
        .unwrap_or(std::env::current_dir().context("resolve current directory")?);
    if !path.exists() {
        if explicit {
            bail!("project path does not exist: {}", path.display());
        }
        return Ok(None);
    }
    let canonical = path
        .canonicalize()
        .with_context(|| format!("resolve project path {}", path.display()))?;
    if explicit && !canonical.is_dir() {
        bail!("project path is not a directory: {}", canonical.display());
    }
    Ok(canonical.is_dir().then_some(canonical))
}

fn default_project_key(project_root: Option<&Path>) -> String {
    project_root
        .and_then(|root| root.file_name())
        .and_then(|name| name.to_str())
        .map(operator_safe_text)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "default".to_string())
}

fn collect_project_outputs(
    root: &Path,
    project_key: &str,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let deliverables_path = root.join("DELIVERABLES.md");
    let referenced = if deliverables_path.exists() {
        extract_backtick_paths(&read_text_lossy(&deliverables_path))
            .into_iter()
            .collect::<BTreeSet<_>>()
    } else {
        BTreeSet::new()
    };

    for reference in &referenced {
        let path = root.join(reference);
        add_entry(
            entries,
            EntryInput {
                label: "Deliverables surface reference".to_string(),
                source: "project_deliverables".to_string(),
                kind: artifact_kind(&path),
                path: path.to_string_lossy().into_owned(),
                relative_path: Some(operator_safe_text(reference)),
                present_override: None,
                retention_class: if has_deliverable_extension(&path) {
                    "handoff".to_string()
                } else {
                    "review".to_string()
                },
                review_status: if path.exists() {
                    "referenced".to_string()
                } else {
                    "missing".to_string()
                },
                why_it_matters:
                    "A human-facing deliverables surface selected this artifact for inspection."
                        .to_string(),
                refs: vec![
                    format!("project:{project_key}"),
                    "surface:DELIVERABLES.md".to_string(),
                ],
            },
        );
    }

    for path in collect_output_candidates(root) {
        let relative = rel_path(root, &path);
        let referenced_by_deliverables = referenced.contains(&relative);
        add_entry(
            entries,
            EntryInput {
                label: if referenced_by_deliverables {
                    "Referenced human-facing output".to_string()
                } else {
                    "Unreferenced human-facing output".to_string()
                },
                source: "project_output_scan".to_string(),
                kind: artifact_kind(&path),
                path: path.to_string_lossy().into_owned(),
                relative_path: Some(operator_safe_text(&relative)),
                present_override: Some(true),
                retention_class: if referenced_by_deliverables {
                    "handoff".to_string()
                } else {
                    "review".to_string()
                },
                review_status: if referenced_by_deliverables {
                    "referenced".to_string()
                } else {
                    "needs_triage".to_string()
                },
                why_it_matters: if referenced_by_deliverables {
                    "Selected output is already promoted to the deliverables surface.".to_string()
                } else {
                    "Human-facing output exists but is not yet promoted to the deliverables surface.".to_string()
                },
                refs: vec![format!("project:{project_key}")],
            },
        );
    }
    Ok(())
}

fn collect_profile_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    collect_closeout_artifacts(profile_dir, project_key, entries)?;
    collect_project_initialization_artifacts(profile_dir, project_key, entries)?;
    collect_ondesk_capture_artifacts(profile_dir, project_key, entries)?;
    Ok(())
}

fn collect_closeout_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let closeouts_dir = profile_dir.join("offdesk_closeouts");
    if !closeouts_dir.exists() {
        return Ok(());
    }
    for entry in
        fs::read_dir(&closeouts_dir).with_context(|| format!("read {}", closeouts_dir.display()))?
    {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let plan_path = artifact_dir.join("closeout_plan.json");
        let plan = read_json_object(&plan_path);
        if !value_matches_project(&plan, project_key) {
            continue;
        }
        let closeout_id =
            json_text(&plan, "/closeout_id").unwrap_or_else(|| artifact_dir_name(&artifact_dir));
        add_path_entry(
            entries,
            PathEntryInput {
                label: "Closeout plan",
                source: "profile_closeout",
                kind: "closeout_plan",
                path: &plan_path,
                retention_class: "review",
                review_status: "referenced",
                why_it_matters:
                    "Explains what Offdesk produced and what must be reviewed before acceptance.",
                refs: vec![format!("closeout:{closeout_id}")],
            },
        );

        if let Some(artifacts) = plan.get("artifacts").and_then(Value::as_object) {
            for (field, value) in artifacts {
                let Some(path) = value.as_str().filter(|value| !value.trim().is_empty()) else {
                    continue;
                };
                let (retention_class, review_status, why) = classify_closeout_artifact(field);
                add_entry(
                    entries,
                    EntryInput {
                        label: closeout_artifact_label(field),
                        source: "profile_closeout".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: None,
                        retention_class: retention_class.to_string(),
                        review_status: review_status.to_string(),
                        why_it_matters: why.to_string(),
                        refs: vec![format!("closeout:{closeout_id}")],
                    },
                );
            }
        }

        collect_closeout_task_artifacts(&plan, &closeout_id, entries);
        collect_closeout_file_operations(&plan, &closeout_id, entries);
        collect_closeout_review_files(&artifact_dir, &closeout_id, entries)?;
    }
    Ok(())
}

fn collect_closeout_task_artifacts(
    plan: &Value,
    closeout_id: &str,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) {
    for task in plan
        .get("tasks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let task_id = json_text(task, "/task_id").unwrap_or_else(|| "unknown".to_string());
        for (field, label, retention, review, why) in [
            (
                "result_artifact_path",
                "Task result artifact",
                "evidence",
                "referenced",
                "Task result artifacts are provenance anchors for Ondesk review.",
            ),
            (
                "log_artifact_path",
                "Task log artifact",
                "archive_candidate",
                "requires_review",
                "Raw logs may be large but remain useful while referenced by a closeout.",
            ),
        ] {
            if let Some(path) = task.get(field).and_then(Value::as_str) {
                add_entry(
                    entries,
                    EntryInput {
                        label: label.to_string(),
                        source: "closeout_task".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: None,
                        retention_class: retention.to_string(),
                        review_status: review.to_string(),
                        why_it_matters: why.to_string(),
                        refs: vec![format!("closeout:{closeout_id}"), format!("task:{task_id}")],
                    },
                );
            }
        }
        for artifact in task
            .get("artifact_refs")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let Some(path) = artifact.get("path").and_then(Value::as_str) else {
                continue;
            };
            let artifact_id = artifact
                .get("artifact_id")
                .and_then(Value::as_str)
                .unwrap_or("declared");
            add_entry(
                entries,
                EntryInput {
                    label: "Declared task artifact".to_string(),
                    source: "closeout_task".to_string(),
                    kind: "artifact_ref".to_string(),
                    path: path.to_string(),
                    relative_path: None,
                    present_override: artifact.get("present").and_then(Value::as_bool),
                    retention_class: "evidence".to_string(),
                    review_status: "referenced".to_string(),
                    why_it_matters: "Declared artifacts must remain available for review."
                        .to_string(),
                    refs: vec![
                        format!("closeout:{closeout_id}"),
                        format!("task:{task_id}"),
                        format!("artifact:{artifact_id}"),
                    ],
                },
            );
        }
    }

    for run in plan
        .get("background_runs")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let ticket_id = json_text(run, "/ticket_id").unwrap_or_else(|| "unknown".to_string());
        for (field, present_field, label, retention, review, why) in [
            (
                "result_artifact_path",
                "result_artifact_present",
                "Background result artifact",
                "evidence",
                "referenced",
                "Background result artifacts are required for morning review.",
            ),
            (
                "log_artifact_path",
                "log_artifact_present",
                "Background log artifact",
                "archive_candidate",
                "requires_review",
                "Background logs may be large but should be archived while referenced.",
            ),
        ] {
            if let Some(path) = run.get(field).and_then(Value::as_str) {
                add_entry(
                    entries,
                    EntryInput {
                        label: label.to_string(),
                        source: "closeout_background".to_string(),
                        kind: field.to_string(),
                        path: path.to_string(),
                        relative_path: None,
                        present_override: run.get(present_field).and_then(Value::as_bool),
                        retention_class: retention.to_string(),
                        review_status: review.to_string(),
                        why_it_matters: why.to_string(),
                        refs: vec![
                            format!("closeout:{closeout_id}"),
                            format!("background:{ticket_id}"),
                        ],
                    },
                );
            }
        }
    }
}

fn collect_closeout_file_operations(
    plan: &Value,
    closeout_id: &str,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) {
    for operation in plan
        .get("file_operations")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(path) = operation.get("path").and_then(Value::as_str) else {
            continue;
        };
        let op = operation
            .get("operation")
            .and_then(Value::as_str)
            .unwrap_or("review");
        let retention = match op {
            "archive_candidate" => "archive_candidate",
            "delete_candidate" => "disposal_candidate",
            _ => "review",
        };
        let requires_review = operation
            .get("requires_commercial_review")
            .and_then(Value::as_bool)
            .unwrap_or(false)
            || operation
                .get("requires_human_approval")
                .and_then(Value::as_bool)
                .unwrap_or(false);
        add_entry(
            entries,
            EntryInput {
                label: format!("Closeout {op}"),
                source: "closeout_file_operation".to_string(),
                kind: op.to_string(),
                path: path.to_string(),
                relative_path: None,
                present_override: operation.get("present").and_then(Value::as_bool),
                retention_class: retention.to_string(),
                review_status: if requires_review {
                    "requires_review".to_string()
                } else {
                    "referenced".to_string()
                },
                why_it_matters: operation
                    .get("reason")
                    .and_then(Value::as_str)
                    .map(operator_safe_text)
                    .unwrap_or_else(|| {
                        "Closeout proposed this file operation for operator review.".to_string()
                    }),
                refs: vec![format!("closeout:{closeout_id}")],
            },
        );
    }
}

fn collect_closeout_review_files(
    artifact_dir: &Path,
    closeout_id: &str,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    for entry in
        fs::read_dir(artifact_dir).with_context(|| format!("read {}", artifact_dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|value| value.to_str()) else {
            continue;
        };
        if name.starts_with("closeout_review_") && name.ends_with(".json") {
            add_path_entry(
                entries,
                PathEntryInput {
                    label: "Closeout review record",
                    source: "profile_closeout_review",
                    kind: "closeout_review",
                    path: &path,
                    retention_class: "acceptance",
                    review_status: "referenced",
                    why_it_matters:
                        "Records the review verdict used before Ondesk accepts or revises work.",
                    refs: vec![format!("closeout:{closeout_id}")],
                },
            );
        } else if name.starts_with("closeout_receipt_") && name.ends_with(".json") {
            add_path_entry(
                entries,
                PathEntryInput {
                    label: "Closeout receipt",
                    source: "profile_closeout_review",
                    kind: "closeout_receipt",
                    path: &path,
                    retention_class: "acceptance",
                    review_status: "referenced",
                    why_it_matters: "Records accepted-truth status and remaining follow-ups.",
                    refs: vec![format!("closeout:{closeout_id}")],
                },
            );
        }
    }
    Ok(())
}

fn collect_project_initialization_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let root = profile_dir.join("project_initializations");
    if !root.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(&root).with_context(|| format!("read {}", root.display()))? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let profile_path = artifact_dir.join("PROJECT_OPERATION_PROFILE.json");
        let profile = read_json_object(&profile_path);
        if !value_project_key_matches(&profile, project_key) {
            continue;
        }
        let init_id =
            json_text(&profile, "/id").unwrap_or_else(|| artifact_dir_name(&artifact_dir));
        for (field, fallback, label, retention, why) in [
            (
                "operation_profile_path",
                "PROJECT_OPERATION_PROFILE.json",
                "Project operation profile",
                "handoff",
                "Defines the project operation scope a fresh harness should use.",
            ),
            (
                "ondesk_start_package_path",
                "ONDESK_START_PACKAGE.md",
                "Ondesk start package",
                "handoff",
                "Gives the next harness a bounded starting packet.",
            ),
            (
                "offdesk_ready_check_path",
                "OFFDESK_READY_CHECK.json",
                "Offdesk ready check",
                "review",
                "Records whether the project is ready for runtime execution.",
            ),
            (
                "module_operation_preflight_path",
                "MODULE_OPERATION_PREFLIGHT.json",
                "Module operation preflight",
                "review",
                "Lists module readiness and blockers before runtime work.",
            ),
            (
                "governance_surface_hints_path",
                "GOVERNANCE_SURFACE_HINTS.md",
                "Governance surface hints",
                "review",
                "Suggests missing current-state, decision, and deliverable surfaces.",
            ),
        ] {
            let path = profile
                .get(field)
                .and_then(Value::as_str)
                .map(PathBuf::from)
                .unwrap_or_else(|| artifact_dir.join(fallback));
            add_path_entry(
                entries,
                PathEntryInput {
                    label,
                    source: "profile_project_initialization",
                    kind: field,
                    path: &path,
                    retention_class: retention,
                    review_status: "referenced",
                    why_it_matters: why,
                    refs: vec![format!("project_init:{init_id}")],
                },
            );
        }
    }
    Ok(())
}

fn collect_ondesk_capture_artifacts(
    profile_dir: &Path,
    project_key: Option<&str>,
    entries: &mut BTreeMap<String, ArtifactIndexEntry>,
) -> Result<()> {
    let root = profile_dir.join("ondesk_captures");
    if !root.exists() {
        return Ok(());
    }
    for entry in fs::read_dir(&root).with_context(|| format!("read {}", root.display()))? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let capture_path = entry.path().join("capture.json");
        let capture = read_json_object(&capture_path);
        if !value_project_key_matches(&capture, project_key) {
            continue;
        }
        let capture_id =
            json_text(&capture, "/id").unwrap_or_else(|| artifact_dir_name(&entry.path()));
        for (field, fallback, label, retention, why) in [
            (
                "capture_path",
                "capture.json",
                "Ondesk capture",
                "review",
                "Stores bounded scrollback and context captured from a live harness.",
            ),
            (
                "prompt_package_path",
                "PROMPT_CONTEXT.md",
                "Ondesk prompt package",
                "handoff",
                "Rehydrates a fresh harness from captured context.",
            ),
        ] {
            let path = capture
                .get(field)
                .and_then(Value::as_str)
                .map(PathBuf::from)
                .unwrap_or_else(|| entry.path().join(fallback));
            add_path_entry(
                entries,
                PathEntryInput {
                    label,
                    source: "profile_ondesk_capture",
                    kind: field,
                    path: &path,
                    retention_class: retention,
                    review_status: "referenced",
                    why_it_matters: why,
                    refs: vec![format!("ondesk_capture:{capture_id}")],
                },
            );
        }
    }
    Ok(())
}

fn add_path_entry(entries: &mut BTreeMap<String, ArtifactIndexEntry>, input: PathEntryInput<'_>) {
    add_entry(
        entries,
        EntryInput {
            label: input.label.to_string(),
            source: input.source.to_string(),
            kind: input.kind.to_string(),
            path: input.path.to_string_lossy().into_owned(),
            relative_path: None,
            present_override: None,
            retention_class: input.retention_class.to_string(),
            review_status: input.review_status.to_string(),
            why_it_matters: input.why_it_matters.to_string(),
            refs: input.refs,
        },
    );
}

fn add_entry(entries: &mut BTreeMap<String, ArtifactIndexEntry>, input: EntryInput) {
    let present = input
        .present_override
        .unwrap_or_else(|| Path::new(&input.path).exists());
    let metadata = Path::new(&input.path).metadata().ok();
    let key = format!("{}|{}|{}", input.source, input.kind, input.path);
    let id = artifact_id(&key);
    let refs = input
        .refs
        .into_iter()
        .map(|value| operator_safe_text(&value))
        .filter(|value| !value.trim().is_empty())
        .collect::<BTreeSet<_>>();
    let entry = ArtifactIndexEntry {
        id,
        label: operator_safe_text(&input.label),
        source: operator_safe_text(&input.source),
        kind: operator_safe_text(&input.kind),
        path: operator_safe_path(&input.path),
        relative_path: input.relative_path.map(|value| operator_safe_text(&value)),
        present,
        bytes: metadata
            .as_ref()
            .filter(|_| present)
            .map(|metadata| metadata.len()),
        modified_at: metadata
            .and_then(|metadata| metadata.modified().ok())
            .map(Into::into),
        retention_class: operator_safe_text(&input.retention_class),
        review_status: if present {
            operator_safe_text(&input.review_status)
        } else {
            "missing".to_string()
        },
        why_it_matters: operator_safe_text(&input.why_it_matters),
        refs: refs.into_iter().collect(),
    };

    entries
        .entry(key)
        .and_modify(|existing| merge_entry(existing, &entry))
        .or_insert(entry);
}

fn merge_entry(existing: &mut ArtifactIndexEntry, incoming: &ArtifactIndexEntry) {
    existing.present |= incoming.present;
    if existing.bytes.is_none() {
        existing.bytes = incoming.bytes;
    }
    if existing.modified_at.is_none() {
        existing.modified_at = incoming.modified_at;
    }
    let mut refs = existing.refs.iter().cloned().collect::<BTreeSet<_>>();
    refs.extend(incoming.refs.iter().cloned());
    existing.refs = refs.into_iter().collect();
    if existing.review_status == "referenced" && incoming.review_status == "requires_review" {
        existing.review_status = incoming.review_status.clone();
    }
}

fn summarize_entries(
    entries: &[ArtifactIndexEntry],
    total_entries: usize,
    truncated_entries: usize,
) -> ArtifactIndexSummary {
    let mut summary = ArtifactIndexSummary {
        total_entries,
        present_entries: entries.iter().filter(|entry| entry.present).count(),
        missing_entries: entries.iter().filter(|entry| !entry.present).count(),
        review_required_entries: entries
            .iter()
            .filter(|entry| {
                entry.review_status.contains("review") || entry.review_status == "needs_triage"
            })
            .count(),
        disposal_candidate_entries: entries
            .iter()
            .filter(|entry| {
                matches!(
                    entry.retention_class.as_str(),
                    "archive_candidate" | "disposal_candidate"
                )
            })
            .count(),
        human_facing_entries: entries
            .iter()
            .filter(|entry| matches!(entry.kind.as_str(), "html" | "png" | "jpg" | "jpeg" | "pdf"))
            .count(),
        truncated_entries,
        ..ArtifactIndexSummary::default()
    };
    for entry in entries {
        *summary
            .by_retention_class
            .entry(entry.retention_class.clone())
            .or_default() += 1;
        *summary.by_source.entry(entry.source.clone()).or_default() += 1;
    }
    summary
}

fn human_summary(index: &Value) -> String {
    let mut output = String::new();
    output.push_str("Artifact Index\n");
    if let Some(project_key) = index.get("project_key").and_then(Value::as_str) {
        output.push_str(&format!("  project: {project_key}\n"));
    }
    if let Some(root) = index.get("project_root").and_then(Value::as_str) {
        output.push_str(&format!("  root: {root}\n"));
    }
    let summary = index.get("summary").unwrap_or(&Value::Null);
    output.push_str(&format!(
        "  entries: {} total, {} present, {} missing\n",
        json_u64(summary, "total_entries"),
        json_u64(summary, "present_entries"),
        json_u64(summary, "missing_entries")
    ));
    output.push_str(&format!(
        "  review: {} review-required, {} disposal/archive candidates, {} human-facing\n",
        json_u64(summary, "review_required_entries"),
        json_u64(summary, "disposal_candidate_entries"),
        json_u64(summary, "human_facing_entries")
    ));
    if let Some(by_class) = summary.get("by_retention_class").and_then(Value::as_object) {
        let classes = by_class
            .iter()
            .map(|(key, value)| format!("{key}={}", value.as_u64().unwrap_or_default()))
            .collect::<Vec<_>>()
            .join(", ");
        if !classes.is_empty() {
            output.push_str(&format!("  retention: {classes}\n"));
        }
    }
    output.push_str("  notable artifacts:\n");
    if let Some(entries) = index.get("entries").and_then(Value::as_array) {
        for entry in entries.iter().take(MAX_HUMAN_ROWS) {
            let label = entry
                .get("label")
                .and_then(Value::as_str)
                .unwrap_or("Artifact");
            let retention = entry
                .get("retention_class")
                .and_then(Value::as_str)
                .unwrap_or("review");
            let status = entry
                .get("review_status")
                .and_then(Value::as_str)
                .unwrap_or("unknown");
            let path = entry
                .get("relative_path")
                .or_else(|| entry.get("path"))
                .and_then(Value::as_str)
                .unwrap_or("-");
            let why = entry
                .get("why_it_matters")
                .and_then(Value::as_str)
                .unwrap_or("Review before use.");
            output.push_str(&format!(
                "    - {label}: {why} [{retention}/{status}] `{path}`\n"
            ));
        }
    }
    output.push_str("  authority: read-only index; deletion, movement, archive, and publication need separate approval\n");
    output
}

fn collect_output_candidates(root: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    for name in OUTPUT_ROOTS {
        let base = root.join(name);
        if !base.exists() {
            continue;
        }
        collect_files_into(&base, &mut candidates);
    }
    candidates.retain(|path| has_deliverable_extension(path));
    candidates.sort();
    candidates
}

fn collect_files_into(root: &Path, files: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_files_into(&path, files);
        } else if path.is_file() {
            files.push(path);
        }
    }
}

fn has_deliverable_extension(path: &Path) -> bool {
    let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
        return false;
    };
    let normalized = format!(".{}", extension.to_ascii_lowercase());
    DELIVERABLE_EXTENSIONS.contains(&normalized.as_str())
}

fn extract_backtick_paths(text: &str) -> Vec<String> {
    let regex = Regex::new(r"`([^`]+)`").expect("valid path regex");
    let mut paths = Vec::new();
    for captures in regex.captures_iter(text) {
        let Some(value) = captures.get(1).map(|value| value.as_str().trim()) else {
            continue;
        };
        if value.is_empty()
            || value.contains(' ')
            || ["http://", "https://", "forager ", "python ", "./.venv"]
                .iter()
                .any(|prefix| value.starts_with(prefix))
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

fn read_json_object(path: &Path) -> Value {
    fs::read_to_string(path)
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok())
        .filter(Value::is_object)
        .unwrap_or(Value::Object(Default::default()))
}

fn read_text_lossy(path: &Path) -> String {
    fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default()
}

fn value_matches_project(value: &Value, project_key: Option<&str>) -> bool {
    let Some(project_key) = project_key else {
        return true;
    };
    if value_project_key_matches(value, Some(project_key)) {
        return true;
    }
    value
        .pointer("/filters/project_key")
        .and_then(Value::as_str)
        .is_some_and(|value| value == project_key)
        || value
            .get("tasks")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .any(|task| {
                task.get("project_key")
                    .and_then(Value::as_str)
                    .is_some_and(|value| value == project_key)
            })
}

fn value_project_key_matches(value: &Value, project_key: Option<&str>) -> bool {
    match project_key {
        Some(project_key) => value
            .get("project_key")
            .and_then(Value::as_str)
            .is_some_and(|value| value == project_key),
        None => true,
    }
}

fn classify_closeout_artifact(field: &str) -> (&'static str, &'static str, &'static str) {
    match field {
        "return_package_markdown" => (
            "handoff",
            "referenced",
            "Rehydrates a fresh harness with reviewed context and next steps.",
        ),
        "closeout_receipt_json" => (
            "acceptance",
            "referenced",
            "Records whether execution is accepted truth or still needs follow-up.",
        ),
        "commercial_review_packet" | "cleanup_manifest_json" => (
            "review",
            "requires_review",
            "Supports disposal, archive, and commercial review decisions.",
        ),
        _ => (
            "review",
            "referenced",
            "Supports closeout review and later audit.",
        ),
    }
}

fn closeout_artifact_label(field: &str) -> String {
    match field {
        "return_package_markdown" => "Ondesk return package".to_string(),
        "closeout_receipt_json" => "Closeout receipt".to_string(),
        "commercial_review_packet" => "Commercial review packet".to_string(),
        "cleanup_manifest_json" => "Cleanup manifest".to_string(),
        "closeout_plan_json" => "Closeout plan".to_string(),
        "closeout_plan_markdown" => "Closeout plan markdown".to_string(),
        _ => field.replace('_', " "),
    }
}

fn artifact_kind(path: &Path) -> String {
    path.extension()
        .and_then(|value| value.to_str())
        .map(|value| value.to_ascii_lowercase())
        .unwrap_or_else(|| "file".to_string())
}

fn artifact_dir_name(path: &Path) -> String {
    path.file_name()
        .and_then(|value| value.to_str())
        .map(operator_safe_text)
        .unwrap_or_else(|| "unknown".to_string())
}

fn json_text(value: &Value, pointer: &str) -> Option<String> {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .map(operator_safe_text)
}

fn json_u64(value: &Value, key: &str) -> u64 {
    value.get(key).and_then(Value::as_u64).unwrap_or_default()
}

fn artifact_id(value: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    format!("artifact-{}", &digest[..12])
}

fn operator_safe_path(path: &str) -> String {
    operator_safe_text(path).replace(['\n', '\r'], " ")
}

fn rel_path(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
        .replace(['\n', '\r'], " ")
}
