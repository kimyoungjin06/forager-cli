//! Project registry: the single source of truth for multi-project routing.
//!
//! `~/.config/forager/projects.toml` (schema `forager_project_registry.v1`)
//! maps each managed project to workspace path patterns, a forager session
//! group, and a wiki knowledge plane. The Telegram operator, nightly wiki
//! mining, and the web portfolio surface read the same file (Python loader:
//! `scripts/telegram_operator/projects.py`).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

pub const PROJECT_REGISTRY_SCHEMA: &str = "forager_project_registry.v1";

#[derive(Debug, Clone)]
pub struct ProjectRegistryEntry {
    pub key: String,
    pub display_name: String,
    pub workspace_patterns: Vec<String>,
    pub session_group: Option<String>,
    pub wiki_profile: Option<String>,
}

#[derive(Deserialize)]
struct RegistryFile {
    schema: Option<String>,
    #[serde(default)]
    projects: BTreeMap<String, RegistryEntryRaw>,
}

#[derive(Deserialize)]
struct RegistryEntryRaw {
    display_name: Option<String>,
    #[serde(default)]
    workspace_patterns: Vec<String>,
    session_group: Option<String>,
    wiki_profile: Option<String>,
}

pub fn registry_path() -> PathBuf {
    if let Ok(explicit) = std::env::var("FORAGER_PROJECT_REGISTRY") {
        if !explicit.trim().is_empty() {
            return PathBuf::from(explicit);
        }
    }
    let config_home = std::env::var("XDG_CONFIG_HOME")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            dirs::home_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join(".config")
        });
    config_home.join("forager").join("projects.toml")
}

/// Load the registry, returning an empty list when the file is absent,
/// unreadable, or has an unexpected schema. Routing must degrade, not fail.
pub fn load_registry() -> Vec<ProjectRegistryEntry> {
    load_registry_from(&registry_path())
}

pub fn load_registry_from(path: &Path) -> Vec<ProjectRegistryEntry> {
    let Ok(raw) = std::fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(file) = toml::from_str::<RegistryFile>(&raw) else {
        return Vec::new();
    };
    if file.schema.as_deref() != Some(PROJECT_REGISTRY_SCHEMA) {
        return Vec::new();
    }
    registry_entries(file)
}

fn registry_entries(file: RegistryFile) -> Vec<ProjectRegistryEntry> {
    file.projects
        .into_iter()
        .map(|(key, entry)| ProjectRegistryEntry {
            display_name: entry.display_name.unwrap_or_else(|| key.clone()),
            workspace_patterns: entry
                .workspace_patterns
                .into_iter()
                .map(|pattern| pattern.trim().to_string())
                .filter(|pattern| !pattern.is_empty())
                .collect(),
            session_group: entry
                .session_group
                .map(|group| group.trim().to_string())
                .filter(|group| !group.is_empty()),
            wiki_profile: entry
                .wiki_profile
                .map(|profile| profile.trim().to_string())
                .filter(|profile| !profile.is_empty()),
            key,
        })
        .collect()
}

/// Unlike routing's degrading loader, inventory must expose unreadable or
/// malformed input instead of treating it as an empty registry.
pub(crate) struct ProjectRegistryInspection {
    pub entries: Vec<ProjectRegistryEntry>,
    pub aliases: BTreeMap<String, Vec<String>>,
    pub content_sha256: String,
}

pub(crate) fn load_registry_checked(path: &Path) -> anyhow::Result<ProjectRegistryInspection> {
    use anyhow::{ensure, Context};
    use sha2::{Digest, Sha256};
    use std::io::Read;

    ensure!(
        std::fs::metadata(path)?.is_file(),
        "registry must be a regular file"
    );
    let file = std::fs::File::open(path).context("open project registry")?;
    ensure!(
        file.metadata()?.is_file(),
        "registry must be a regular file"
    );
    let mut raw = String::new();
    file.take(1_048_577).read_to_string(&mut raw)?;
    ensure!(raw.len() <= 1_048_576, "registry exceeds 1 MiB");
    let file: RegistryFile = toml::from_str(&raw).context("parse project registry")?;
    ensure!(
        file.schema.as_deref() == Some(PROJECT_REGISTRY_SCHEMA),
        "unsupported project registry schema"
    );
    let value: toml::Value = toml::from_str(&raw)?;
    let mut aliases = BTreeMap::new();
    if let Some(projects) = value.get("projects").and_then(toml::Value::as_table) {
        for (key, project) in projects {
            let Some(values) = project.get("aliases") else {
                continue;
            };
            let values = values
                .as_array()
                .context("project aliases must be an array")?;
            let mut names = Vec::new();
            for name in values {
                let name = name.as_str().context("project aliases must be strings")?;
                if !name.trim().is_empty() {
                    names.push(name.trim().to_string());
                }
            }
            aliases.insert(key.clone(), names);
        }
    }
    Ok(ProjectRegistryInspection {
        entries: registry_entries(file),
        aliases,
        content_sha256: format!("{:x}", Sha256::digest(raw.as_bytes())),
    })
}

/// Slug usable as a project key and wiki profile name.
pub fn slugify_key(name: &str) -> String {
    let slug: String = name
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || ('가'..='힣').contains(&c) {
                c
            } else {
                '-'
            }
        })
        .collect();
    let slug = slug.trim_matches('-').to_string();
    // Collapse runs of '-' left by consecutive separators (e.g. "1.2.8.").
    let mut collapsed = String::with_capacity(slug.len());
    for c in slug.chars() {
        if c == '-' && collapsed.ends_with('-') {
            continue;
        }
        collapsed.push(c);
    }
    if collapsed.is_empty() {
        "project".to_string()
    } else {
        collapsed
    }
}

/// Drop leading all-numeric dot segments from a workspace folder name so
/// numbering prefixes ("1.2.8.TwinPaper") do not leak into project keys.
fn key_base_from_folder(folder: &str) -> String {
    let rest: Vec<&str> = folder
        .split('.')
        .skip_while(|segment| !segment.is_empty() && segment.chars().all(|c| c.is_ascii_digit()))
        .collect();
    if rest.is_empty() {
        folder.to_string()
    } else {
        rest.join(".")
    }
}

/// Default registry entry for onboarding a workspace directory: the folder
/// name becomes the workspace pattern, and the (slugified) key doubles as the
/// wiki profile so a fresh knowledge plane comes up with the project.
pub fn default_entry_for_path(path: &Path, key: Option<&str>) -> ProjectRegistryEntry {
    let folder = path
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_else(|| "project".to_string());
    let key = key
        .map(slugify_key)
        .unwrap_or_else(|| slugify_key(&key_base_from_folder(&folder)));
    ProjectRegistryEntry {
        display_name: folder.clone(),
        workspace_patterns: vec![folder],
        session_group: None,
        wiki_profile: Some(key.clone()),
        key,
    }
}

/// Append a project to the registry file, creating it (with the schema
/// header) when missing. Existing content is never rewritten; a duplicate
/// key is an error so hand-edited entries stay authoritative.
pub fn append_project(path: &Path, entry: &ProjectRegistryEntry) -> anyhow::Result<()> {
    let existing = load_registry_from(path);
    if existing.iter().any(|item| item.key == entry.key) {
        anyhow::bail!("project key already registered: {}", entry.key);
    }
    if path.exists() {
        let raw = std::fs::read_to_string(path)?;
        let parsed: Result<RegistryFile, _> = toml::from_str(&raw);
        let schema_ok = matches!(parsed, Ok(ref file) if file.schema.as_deref() == Some(PROJECT_REGISTRY_SCHEMA));
        if !raw.trim().is_empty() && !schema_ok {
            anyhow::bail!(
                "refusing to append to {} (unexpected schema or unparsable TOML)",
                path.display()
            );
        }
    }
    let mut block = String::new();
    if !path.exists() || std::fs::read_to_string(path)?.trim().is_empty() {
        block.push_str(&format!("schema = \"{PROJECT_REGISTRY_SCHEMA}\"\n"));
    }
    block.push_str(&format!("\n[projects.{}]\n", entry.key));
    block.push_str(&format!(
        "display_name = {}\n",
        toml_string(&entry.display_name)
    ));
    let patterns = entry
        .workspace_patterns
        .iter()
        .map(|pattern| toml_string(pattern))
        .collect::<Vec<_>>()
        .join(", ");
    block.push_str(&format!("workspace_patterns = [{patterns}]\n"));
    if let Some(group) = &entry.session_group {
        block.push_str(&format!("session_group = {}\n", toml_string(group)));
    }
    if let Some(profile) = &entry.wiki_profile {
        block.push_str(&format!("wiki_profile = {}\n", toml_string(profile)));
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    file.write_all(block.as_bytes())?;
    Ok(())
}

fn toml_string(value: &str) -> String {
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Markers that make a directory look like a real project during a workspace
/// scan (stricter than the Telegram candidate ranker: a bare README does not
/// qualify for bulk registration).
pub const SCAN_MARKERS: &[&str] = &[
    ".git",
    ".forager",
    "AGENTS.md",
    "CLAUDE.md",
    "Cargo.toml",
    "pyproject.toml",
    "package.json",
    "uv.lock",
];

/// Immediate child directories of `root` that carry at least one project
/// marker. Hidden directories are skipped; the result is sorted by name.
pub fn scan_workspace_projects(root: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut projects: Vec<PathBuf> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.is_dir())
        .filter(|path| {
            path.file_name()
                .map(|name| !name.to_string_lossy().starts_with('.'))
                .unwrap_or(false)
        })
        .filter(|path| SCAN_MARKERS.iter().any(|marker| path.join(marker).exists()))
        .collect();
    projects.sort();
    projects
}

/// Key that does not collide with any registered project (base, base-2, ...).
pub fn unique_key(registry: &[ProjectRegistryEntry], base: &str) -> String {
    let taken: std::collections::HashSet<&str> =
        registry.iter().map(|entry| entry.key.as_str()).collect();
    if !taken.contains(base) {
        return base.to_string();
    }
    for counter in 2..1000 {
        let candidate = format!("{base}-{counter}");
        if !taken.contains(candidate.as_str()) {
            return candidate;
        }
    }
    format!("{base}-{}", uuid::Uuid::new_v4())
}

/// Match a filesystem path against workspace patterns (substring match, same
/// semantics as the Python loader and the nightly session miner).
pub fn resolve_project_for_path(
    path: &Path,
    registry: &[ProjectRegistryEntry],
) -> Option<ProjectRegistryEntry> {
    let text = path.to_string_lossy();
    registry
        .iter()
        .find(|entry| {
            entry
                .workspace_patterns
                .iter()
                .any(|pattern| text.contains(pattern.as_str()))
        })
        .cloned()
}

/// Resolve exactly one project for an authority-bearing operation.
///
/// Presentation and mutation boundaries must not inherit the registry's
/// first-match display behavior when overlapping workspace patterns exist.
pub fn resolve_unique_project_for_path(
    path: &Path,
    registry: &[ProjectRegistryEntry],
) -> anyhow::Result<Option<ProjectRegistryEntry>> {
    let text = path.to_string_lossy();
    let matches = registry
        .iter()
        .filter(|entry| {
            entry
                .workspace_patterns
                .iter()
                .any(|pattern| text.contains(pattern.as_str()))
        })
        .collect::<Vec<_>>();
    match matches.as_slice() {
        [] => Ok(None),
        [entry] => Ok(Some((*entry).clone())),
        _ => anyhow::bail!(
            "project path matches multiple registry entries: {}",
            matches
                .iter()
                .map(|entry| entry.key.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn write_registry(content: &str) -> tempfile::NamedTempFile {
        let mut file = tempfile::NamedTempFile::new().expect("temp registry");
        file.write_all(content.as_bytes()).expect("write registry");
        file
    }

    #[test]
    fn loads_and_resolves_projects() {
        let file = write_registry(
            r#"
schema = "forager_project_registry.v1"

[projects.twinpaper]
display_name = "TwinPaper"
workspace_patterns = ["1.2.8.TwinPaper"]
session_group = "Twin"
wiki_profile = "twinpaper-review"

[projects.bare]
"#,
        );
        let registry = load_registry_from(file.path());
        assert_eq!(registry.len(), 2);
        let resolved = resolve_project_for_path(
            Path::new("/home/user/Workspace/1.2.8.TwinPaper/modules/02_golden_set"),
            &registry,
        )
        .expect("twinpaper resolves");
        assert_eq!(resolved.key, "twinpaper");
        assert_eq!(resolved.wiki_profile.as_deref(), Some("twinpaper-review"));
        assert_eq!(resolved.session_group.as_deref(), Some("Twin"));
        assert!(resolve_project_for_path(Path::new("/elsewhere"), &registry).is_none());
    }

    #[test]
    fn authority_resolution_rejects_overlapping_project_patterns() {
        let registry = vec![
            ProjectRegistryEntry {
                key: "one".to_string(),
                display_name: "One".to_string(),
                workspace_patterns: vec!["workspace".to_string()],
                session_group: None,
                wiki_profile: Some("one-wiki".to_string()),
            },
            ProjectRegistryEntry {
                key: "two".to_string(),
                display_name: "Two".to_string(),
                workspace_patterns: vec!["demo-project".to_string()],
                session_group: None,
                wiki_profile: Some("two-wiki".to_string()),
            },
        ];
        let error =
            resolve_unique_project_for_path(Path::new("/workspace/demo-project"), &registry)
                .unwrap_err();
        assert!(error.to_string().contains("multiple registry entries"));
    }

    #[test]
    fn wrong_schema_yields_empty() {
        let file = write_registry("schema = \"other.v1\"\n[projects.x]\n");
        assert!(load_registry_from(file.path()).is_empty());
    }

    #[test]
    fn missing_file_yields_empty() {
        assert!(load_registry_from(Path::new("/nonexistent/projects.toml")).is_empty());
    }

    #[test]
    fn slugify_produces_stable_keys() {
        assert_eq!(slugify_key("1.2.8.TwinPaper"), "1-2-8-twinpaper");
        assert_eq!(slugify_key("My Project"), "my-project");
        assert_eq!(slugify_key("---"), "project");
    }

    #[test]
    fn default_key_drops_numbering_prefix() {
        let key =
            |folder: &str| default_entry_for_path(Path::new(&format!("/ws/{folder}")), None).key;
        assert_eq!(key("1.2.8.TwinPaper"), "twinpaper");
        assert_eq!(key("1.4.1.1.RAG-mLLM"), "rag-mllm");
        assert_eq!(key("0.4.2024Mobility"), "2024mobility");
        assert_eq!(key("3.n8n"), "n8n");
        assert_eq!(key("aoe_orch_control"), "aoe-orch-control");
        assert_eq!(key("1.9.0"), "1-9-0");
    }

    #[test]
    fn append_creates_and_extends_registry() {
        let dir = tempfile::tempdir().expect("temp dir");
        let registry_file = dir.path().join("projects.toml");
        let entry = default_entry_for_path(Path::new("/ws/1.9.9.NewThing"), Some("newthing"));
        assert_eq!(entry.key, "newthing");
        assert_eq!(entry.wiki_profile.as_deref(), Some("newthing"));
        assert_eq!(entry.workspace_patterns, vec!["1.9.9.NewThing"]);

        append_project(&registry_file, &entry).expect("append into fresh file");
        let loaded = load_registry_from(&registry_file);
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].display_name, "1.9.9.NewThing");

        // Duplicate keys must be rejected; a second project appends cleanly.
        assert!(append_project(&registry_file, &entry).is_err());
        let second = default_entry_for_path(Path::new("/ws/Other"), None);
        append_project(&registry_file, &second).expect("append second");
        let loaded = load_registry_from(&registry_file);
        assert_eq!(loaded.len(), 2);
        assert!(resolve_project_for_path(Path::new("/ws/Other/src"), &loaded).is_some());
    }

    #[test]
    fn append_refuses_foreign_toml() {
        let dir = tempfile::tempdir().expect("temp dir");
        let registry_file = dir.path().join("projects.toml");
        std::fs::write(&registry_file, "schema = \"other.v1\"\n").expect("seed foreign file");
        let entry = default_entry_for_path(Path::new("/ws/X"), None);
        assert!(append_project(&registry_file, &entry).is_err());
    }
}
