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
    fn wrong_schema_yields_empty() {
        let file = write_registry("schema = \"other.v1\"\n[projects.x]\n");
        assert!(load_registry_from(file.path()).is_empty());
    }

    #[test]
    fn missing_file_yields_empty() {
        assert!(load_registry_from(Path::new("/nonexistent/projects.toml")).is_empty());
    }
}
