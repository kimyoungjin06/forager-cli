//! CLI command implementations

pub mod add;
pub mod definition;
pub mod doctor;
pub mod group;
pub mod init;
pub mod list;
pub mod migrate;
pub mod offdesk;
pub mod ondesk;
pub mod profile;
pub mod project;
pub mod remove;
pub mod session;
pub mod sounds;
pub mod status;
pub mod tmux;
pub mod uninstall;
pub mod worktree;

pub use definition::{Cli, Commands};

use crate::session::Instance;
use anyhow::{bail, Result};
use std::path::Path;

pub const PRIMARY_BINARY_NAME: &str = "forager";
pub const LEGACY_BINARY_NAME: &str = "aoe";

pub fn invoked_binary_name() -> String {
    std::env::args()
        .next()
        .and_then(|path| {
            Path::new(&path)
                .file_stem()
                .map(|name| name.to_string_lossy().into_owned())
        })
        .filter(|name| !name.is_empty())
        .unwrap_or_else(|| PRIMARY_BINARY_NAME.to_string())
}

pub fn resolve_session<'a>(identifier: &str, instances: &'a [Instance]) -> Result<&'a Instance> {
    // Try exact ID match
    if let Some(inst) = instances.iter().find(|i| i.id == identifier) {
        return Ok(inst);
    }

    // Try ID prefix match
    if let Some(inst) = instances.iter().find(|i| i.id.starts_with(identifier)) {
        return Ok(inst);
    }

    // Try exact title match
    if let Some(inst) = instances.iter().find(|i| i.title == identifier) {
        return Ok(inst);
    }

    // Try path match
    if let Some(inst) = instances.iter().find(|i| i.project_path == identifier) {
        return Ok(inst);
    }

    bail!("Session not found: {}", identifier)
}

pub fn truncate(s: &str, max: usize) -> String {
    let char_count = s.chars().count();
    if char_count <= max {
        s.to_string()
    } else if max <= 3 {
        s.chars().take(max).collect()
    } else {
        let truncated: String = s.chars().take(max - 3).collect();
        format!("{}...", truncated)
    }
}

pub fn truncate_id(id: &str, max_len: usize) -> &str {
    if id.len() > max_len {
        &id[..max_len]
    } else {
        id
    }
}
