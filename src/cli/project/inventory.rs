//! Bounded workspace census. Inventory findings grant no execution authority.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use anyhow::{ensure, Context, Result};
use clap::Args;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::offdesk::operator_safe_text;
use crate::session::project_registry::{self as registry, ProjectRegistryEntry};

#[derive(Args)]
pub struct ProjectInventoryArgs {
    /// Explicit workspace roots whose children should be accounted for
    #[arg(required = true)]
    roots: Vec<PathBuf>,
    /// Registry to inspect; defaults to the existing project registry
    #[arg(long, value_name = "PATH")]
    registry: Option<PathBuf>,
    /// Directory levels inspected through marker-free containers only
    #[arg(long, default_value_t = 2, value_parser = clap::value_parser!(u32).range(1..=4))]
    max_depth: u32,
    /// Maximum number of filesystem entries across all roots
    #[arg(long, default_value_t = 4096, value_parser = clap::value_parser!(u32).range(1..=16384))]
    max_entries: u32,
    /// Project keys to highlight; absence means inventory only, no selection
    #[arg(long = "project-key")]
    project_keys: Vec<String>,
    /// Explicitly select no projects while still reporting inventory
    #[arg(long, conflicts_with = "project_keys")]
    no_selection: bool,
    /// Explicitly excluded path; descendants are not inspected
    #[arg(long = "exclude", value_name = "PATH")]
    exclusions: Vec<PathBuf>,
    /// Existing initialization subproject spec; parent must resolve uniquely
    #[arg(long = "subproject-spec", value_name = "PATH")]
    subproject_specs: Vec<PathBuf>,
    /// Output as JSON
    #[arg(long)]
    json: bool,
}

struct Census<'a> {
    registry: &'a [ProjectRegistryEntry],
    selected: &'a BTreeSet<String>,
    boundaries: Vec<PathBuf>,
    exclusions: Vec<PathBuf>,
    max_depth: u32,
    limit: usize,
    entries: Vec<Value>,
    errors: Vec<Value>,
    truncated: bool,
    projects: BTreeMap<String, BTreeSet<PathBuf>>,
}

pub(super) fn run(args: ProjectInventoryArgs) -> Result<()> {
    let report = inspect(&args)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        println!("Workspace inventory (read-only; no runtime selection)");
        println!("{}", serde_json::to_string_pretty(&report["summary"])?);
        for entry in report["entries"].as_array().unwrap().iter().take(80) {
            println!(
                "{}  {}",
                entry["status"].as_str().unwrap_or("unknown"),
                entry["path"].as_str().unwrap_or("")
            );
        }
        println!("Use --json for all entries, registry coverage and declared subprojects.");
    }
    Ok(())
}

fn display(path: &Path) -> String {
    operator_safe_text(&path.to_string_lossy())
}

fn error_text(error: impl std::fmt::Display) -> String {
    operator_safe_text(&error.to_string())
}

fn filesystem_identity(path: &Path) -> Option<Value> {
    crate::process::filesystem_file_identity(&fs::metadata(path).ok()?)
        .map(|(device, inode)| json!({"device":device,"inode":inode}))
}

fn absolute(path: &Path) -> Result<PathBuf> {
    Ok(if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    })
}

fn read_regular(path: &Path, limit: u64) -> Result<String> {
    ensure!(
        fs::metadata(path)?.is_file(),
        "input must be a regular file"
    );
    let file = fs::File::open(path)?;
    ensure!(file.metadata()?.is_file(), "input changed type");
    let mut text = String::new();
    file.take(limit + 1).read_to_string(&mut text)?;
    ensure!(text.len() as u64 <= limit, "input exceeds size limit");
    Ok(text)
}

fn inspect(args: &ProjectInventoryArgs) -> Result<Value> {
    let registry_path = args
        .registry
        .clone()
        .unwrap_or_else(registry::registry_path);
    let (registered, aliases, registry_status, registry_error, registry_sha256) =
        match registry::load_registry_checked(&registry_path) {
            Ok(inspection) => (
                inspection.entries,
                inspection.aliases,
                "loaded",
                None,
                Some(inspection.content_sha256),
            ),
            Err(error) => {
                let status = match error
                    .downcast_ref::<std::io::Error>()
                    .map(std::io::Error::kind)
                {
                    Some(std::io::ErrorKind::NotFound) => "missing",
                    Some(std::io::ErrorKind::PermissionDenied) => "unreadable",
                    Some(_) => "unavailable",
                    None => "invalid",
                };
                (
                    Vec::new(),
                    BTreeMap::new(),
                    status,
                    Some(error_text(format!("{error:#}"))),
                    None,
                )
            }
        };
    let selected: BTreeSet<String> = args.project_keys.iter().cloned().collect();
    let keys: BTreeSet<String> = registered.iter().map(|entry| entry.key.clone()).collect();
    let unknown_selection: Vec<_> = selected.difference(&keys).cloned().collect();
    let mut roots = Vec::new();
    let mut seen = BTreeSet::new();
    let mut canonical_roots = Vec::new();
    for path in &args.roots {
        let requested = absolute(path)?;
        match requested.canonicalize() {
            Ok(canonical) if canonical.is_dir() => {
                let duplicate = !seen.insert(canonical.clone());
                roots.push(
                    json!({"path":display(&requested), "canonical_path":display(&canonical),
                    "filesystem_identity":filesystem_identity(&canonical),
                    "status":if duplicate {"duplicate_root"} else {"pending"}}),
                );
                if !duplicate {
                    canonical_roots.push(canonical);
                }
            }
            Ok(_) => roots.push(json!({"path":display(&requested), "status":"not_directory"})),
            Err(error) => roots.push(json!({"path":display(&requested), "status":"unavailable",
                "reason":error_text(error)})),
        }
    }
    let exclusions = args
        .exclusions
        .iter()
        .map(|path| Ok(path.canonicalize().unwrap_or(absolute(path)?)))
        .collect::<Result<Vec<_>>>()?;
    let mut census = Census {
        registry: &registered,
        selected: &selected,
        boundaries: canonical_roots.clone(),
        exclusions,
        max_depth: args.max_depth,
        limit: args.max_entries as usize,
        entries: Vec::new(),
        errors: Vec::new(),
        truncated: false,
        projects: BTreeMap::new(),
    };
    let mut pending_roots = canonical_roots.iter();
    for row in &mut roots {
        if row["status"] != "pending" {
            continue;
        }
        let root = pending_roots.next().unwrap();
        row["status"] = json!(if census.walk(root, root, 1) {
            "enumerated"
        } else {
            "incomplete"
        });
    }
    census.entries.sort_by_key(|row| {
        (
            row["path"].as_str().unwrap_or("").to_string(),
            row["workspace_root"].as_str().unwrap_or("").to_string(),
        )
    });
    let mut canonical_groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (index, row) in census.entries.iter().enumerate() {
        if row["entry_type"] == "directory" && row["status"] != "excluded_outside_roots" {
            if let Some(path) = row["canonical_path_id"].as_str() {
                canonical_groups
                    .entry(path.to_string())
                    .or_default()
                    .push(index);
            }
        }
    }
    for indices in canonical_groups.values_mut() {
        indices.sort_by_key(|index| {
            (
                census.entries[*index]["is_symlink"]
                    .as_bool()
                    .unwrap_or(false),
                census.entries[*index]["path"]
                    .as_str()
                    .unwrap_or("")
                    .to_string(),
            )
        });
        let first = census.entries[indices[0]]["path"].clone();
        for index in indices.iter().skip(1) {
            census.entries[*index]["duplicate_of"] = first.clone();
            census.entries[*index]["status_before_deduplication"] =
                census.entries[*index]["status"].clone();
            census.entries[*index]["status"] = json!("duplicate_path");
        }
    }
    let mut alias_owners: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for entry in &registered {
        for name in std::iter::once(&entry.key).chain(aliases.get(&entry.key).into_iter().flatten())
        {
            alias_owners
                .entry(name.to_lowercase())
                .or_default()
                .insert(entry.key.clone());
        }
    }
    let conflicts: Vec<_> = alias_owners
        .into_iter()
        .filter(|(_, owners)| owners.len() > 1)
        .map(|(alias, owners)| json!({"alias":alias,"project_keys":owners}))
        .collect();
    let mut registry_rows = Vec::new();
    for entry in &registered {
        let observed: BTreeSet<_> = census
            .entries
            .iter()
            .filter(|row| {
                row["project_keys"]
                    .as_array()
                    .is_some_and(|keys| keys.contains(&json!(entry.key)))
                    && row["entry_type"] == "directory"
                    && row["status"] != "excluded_outside_roots"
            })
            .filter_map(|row| row["canonical_path"].as_str())
            .map(str::to_string)
            .collect();
        registry_rows.push(json!({"project_key":entry.key,"display_name":entry.display_name,
            "workspace_patterns":entry.workspace_patterns,"aliases":aliases.get(&entry.key).cloned().unwrap_or_default(),
            "observed_paths":observed,"status":if entry.workspace_patterns.is_empty(){"no_workspace_patterns"}
                else if observed.is_empty(){"unobserved_in_declared_scope"}else{"observed"}}));
    }
    let subprojects = declared_subprojects(args, &census.projects)?;
    let counts = census
        .entries
        .iter()
        .fold(BTreeMap::<String, usize>::new(), |mut counts, row| {
            *counts
                .entry(row["status"].as_str().unwrap_or("unknown").to_string())
                .or_default() += 1;
            counts
        });
    let enumeration_complete = roots
        .iter()
        .all(|root| root["status"] == "enumerated" || root["status"] == "duplicate_root")
        && census.errors.is_empty()
        && !census
            .entries
            .iter()
            .any(|row| row["status"] == "unavailable")
        && !census.truncated;
    let unresolved = census
        .entries
        .iter()
        .filter(|row| {
            row["status"]
                .as_str()
                .is_some_and(|status| status.starts_with("unresolved") || status == "unavailable")
        })
        .count();
    Ok(
        json!({"schema":"forager_workspace_inventory.v1", "read_only":true,"authorizes_dispatch":false,
        "registry":{"path":display(&registry_path),"status":registry_status,"error":registry_error,"content_sha256":registry_sha256,
            "entries":registry_rows,"alias_conflicts":conflicts},
        "scope":{"max_depth":args.max_depth,"max_entries":args.max_entries,
            "project_contents_recursively_scanned":false,"logical_subprojects_inferred":false,
            "policy":"Inspect declared roots and marker-free containers to max_depth. Do not recurse into identified projects, hidden/excluded paths or outside-root aliases. Deeper and logical scopes require explicit roots/specs."},
        "selection":{"project_keys":selected,"explicit_empty":args.no_selection,
            "unknown_project_keys":unknown_selection,"execution_ready":false},
        "roots":roots,"entries":census.entries,"declared_subprojects":subprojects,"errors":census.errors,
        "summary":{"filesystem_entries":census.entries.len(),"by_status":counts,
            "registry_entries":registered.len(),"unobserved_registry_entries":registry_rows.iter().filter(|row| row["status"]!="observed").count(),
            "unresolved_entries":unresolved,"enumeration_complete":enumeration_complete,
            "registry_complete":registry_status=="loaded","truncated":census.truncated,
            "whole_workspace_project_classification_complete":false,
            "offdesk_ready":false}}),
    )
}

impl Census<'_> {
    fn walk(&mut self, root: &Path, directory: &Path, depth: u32) -> bool {
        let children = match fs::read_dir(directory) {
            Ok(children) => children,
            Err(error) => {
                self.errors
                    .push(json!({"path":display(directory),"reason":error_text(error)}));
                return false;
            }
        };
        let mut paths = Vec::new();
        let remaining = self.limit.saturating_sub(self.entries.len());
        let mut complete = true;
        for (offset, child) in children.enumerate() {
            if offset >= remaining {
                self.truncated = true;
                complete = false;
                break;
            }
            match child {
                Ok(child) => paths.push(child.path()),
                Err(error) => {
                    self.errors
                        .push(json!({"path":display(directory),"reason":error_text(error)}));
                    complete = false;
                }
            }
        }
        paths.sort();
        for path in paths {
            if self.entries.len() >= self.limit {
                self.truncated = true;
                return false;
            }
            let (mut row, descend) = self.inspect_path(&path, depth);
            row["workspace_root"] = json!(display(root));
            row["depth"] = json!(depth);
            self.entries.push(row);
            if let Some(directory) = descend.filter(|_| depth < self.max_depth) {
                if !self.walk(root, &directory, depth + 1) {
                    complete = false;
                }
            }
        }
        complete
    }

    fn inspect_path(&mut self, path: &Path, depth: u32) -> (Value, Option<PathBuf>) {
        let mut row = json!({"path":display(path),"entry_path_id":format!("{:x}", Sha256::digest(path.as_os_str().as_encoded_bytes())),
            "entry_type":"unknown","status":"unavailable",
            "project_keys":[],"markers":[],"selected":false,
            "subproject_coverage":"not_assessed_without_explicit_declaration"});
        let metadata = match fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) => {
                row["reason"] = json!(error_text(error));
                return (row, None);
            }
        };
        row["is_symlink"] = json!(metadata.is_symlink());
        if path.file_name().is_some_and(|name| name.to_str().is_none()) {
            row["status"] = json!("unresolved_non_unicode_path");
            return (row, None);
        }
        let canonical = match path.canonicalize() {
            Ok(canonical) => canonical,
            Err(error) => {
                row["reason"] = json!(error_text(error));
                return (row, None);
            }
        };
        row["canonical_path"] = json!(display(&canonical));
        row["filesystem_identity"] = json!(filesystem_identity(&canonical));
        row["canonical_path_id"] = json!(format!(
            "{:x}",
            Sha256::digest(canonical.as_os_str().as_encoded_bytes())
        ));
        let is_directory = canonical.is_dir();
        row["entry_type"] = json!(if is_directory {
            "directory"
        } else {
            "file_or_special"
        });
        if !self
            .boundaries
            .iter()
            .any(|root| canonical.starts_with(root))
        {
            row["status"] = json!("excluded_outside_roots");
            return (row, None);
        }
        if self
            .exclusions
            .iter()
            .any(|excluded| path.starts_with(excluded) || canonical.starts_with(excluded))
        {
            row["status"] = json!("excluded_by_operator");
            return (row, None);
        }
        if path
            .file_name()
            .is_some_and(|name| name.to_string_lossy().starts_with('.'))
        {
            row["status"] = json!("excluded_hidden");
            return (row, None);
        }
        if !is_directory {
            row["status"] = json!("excluded_non_directory");
            return (row, None);
        }
        if let Err(error) = fs::read_dir(&canonical) {
            row["reason"] = json!(error_text(error));
            return (row, None);
        }
        let mut markers = Vec::new();
        for marker in registry::SCAN_MARKERS {
            match fs::symlink_metadata(canonical.join(marker)) {
                Ok(_) => markers.push(*marker),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    row["reason"] = json!(error_text(error));
                    return (row, None);
                }
            }
        }
        let keys: Vec<_> = self
            .registry
            .iter()
            .filter(|entry| {
                entry.workspace_patterns.iter().any(|pattern| {
                    canonical.to_string_lossy().contains(pattern.as_str())
                        || path.to_string_lossy().contains(pattern.as_str())
                })
            })
            .map(|entry| entry.key.clone())
            .collect();
        row["markers"] = json!(markers);
        row["project_keys"] = json!(keys);
        row["selected"] = json!(keys.len() == 1 && self.selected.contains(&keys[0]));
        if keys.len() == 1 {
            self.projects
                .entry(keys[0].clone())
                .or_default()
                .insert(canonical.clone());
        }
        row["status"] = json!(if keys.len() > 1 {
            "unresolved_registry_overlap"
        } else if keys.len() == 1 {
            "registered_project"
        } else if !markers.is_empty() {
            "unregistered_project_candidate"
        } else if depth < self.max_depth {
            "unresolved_container_inspected"
        } else {
            "unresolved_marker_free_depth_limit"
        });
        let descend = keys.is_empty() && markers.is_empty() && !metadata.is_symlink();
        if metadata.is_symlink() && keys.is_empty() && markers.is_empty() {
            row["status"] = json!("unresolved_marker_free_alias");
        }
        (row, descend.then_some(canonical))
    }
}

fn declared_subprojects(
    args: &ProjectInventoryArgs,
    projects: &BTreeMap<String, BTreeSet<PathBuf>>,
) -> Result<Vec<Value>> {
    let mut result = Vec::new();
    let mut identities = BTreeSet::new();
    for path in &args.subproject_specs {
        let inspected = (|| -> Result<Value> {
            let raw = read_regular(path, 65_536)?;
            let spec: super::SubprojectSpec = serde_json::from_str(&raw)?;
            let parents = projects
                .get(&spec.project_key)
                .context("declared parent is not observed")?;
            ensure!(
                parents.len() == 1,
                "declared subproject parent is missing or ambiguous in this inventory"
            );
            let root = parents.first().unwrap();
            let candidates = super::load_subproject_candidates(
                std::slice::from_ref(path),
                &spec.project_key,
                root,
                &[],
            )?;
            let candidate = candidates.first().context("missing validated subproject")?;
            ensure!(
                read_regular(path, 65_536)? == raw,
                "subproject declaration changed during inspection"
            );
            ensure!(
                identities.insert((spec.project_key.clone(), spec.subproject_id.clone())),
                "duplicate declared subproject identity"
            );
            Ok(
                json!({"spec_path":display(path),"status":"declared_scope_validated",
                "project_key":spec.project_key,"subproject_id":candidate.scope_ref,"project_root":display(root),
                "spec_sha256":format!("{:x}",Sha256::digest(raw.as_bytes())),
                "anchor_path":candidate.path,"owning_plan_path":candidate.owning_plan_path,
                "read_paths":candidate.read_paths,"execution_ready":false}),
            )
        })();
        result.push(inspected.unwrap_or_else(|error| json!({"spec_path":display(path),
            "status":"unresolved_declaration","reason":error_text(format!("{error:#}")),"execution_ready":false})));
    }
    Ok(result)
}
