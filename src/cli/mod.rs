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
pub mod project_audit;
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
    let index = resolve_session_index(identifier, instances)?;
    Ok(&instances[index])
}

pub fn resolve_session_index(identifier: &str, instances: &[Instance]) -> Result<usize> {
    let identifier = identifier.trim();
    if identifier.is_empty() {
        bail!("Session identifier cannot be empty");
    }

    let exact_id = matching_indices(instances, |inst| inst.id == identifier);
    if let Some(index) = unique_match(identifier, instances, &exact_id)? {
        return Ok(index);
    }

    let id_prefix = matching_indices(instances, |inst| inst.id.starts_with(identifier));
    if let Some(index) = unique_match(identifier, instances, &id_prefix)? {
        return Ok(index);
    }

    let exact_title = matching_indices(instances, |inst| inst.title == identifier);
    if let Some(index) = unique_match(identifier, instances, &exact_title)? {
        return Ok(index);
    }

    let exact_path = matching_indices(instances, |inst| inst.project_path == identifier);
    if let Some(index) = unique_match(identifier, instances, &exact_path)? {
        return Ok(index);
    }

    bail!("Session not found: {}", identifier)
}

fn matching_indices(instances: &[Instance], predicate: impl Fn(&Instance) -> bool) -> Vec<usize> {
    instances
        .iter()
        .enumerate()
        .filter_map(|(index, inst)| predicate(inst).then_some(index))
        .collect()
}

fn unique_match(
    identifier: &str,
    instances: &[Instance],
    indices: &[usize],
) -> Result<Option<usize>> {
    match indices {
        [] => Ok(None),
        [index] => Ok(Some(*index)),
        _ => bail!(
            "{}",
            ambiguous_session_message(identifier, instances, indices)
        ),
    }
}

fn ambiguous_session_message(
    identifier: &str,
    instances: &[Instance],
    indices: &[usize],
) -> String {
    let mut message = format!(
        "Ambiguous session identifier '{}' matches {} sessions:",
        identifier,
        indices.len()
    );
    for index in indices.iter().take(8) {
        let inst = &instances[*index];
        message.push_str(&format!(
            "\n  - {} ({}) {}",
            inst.title,
            truncate_id(&inst.id, 12),
            inst.project_path
        ));
    }
    if indices.len() > 8 {
        message.push_str(&format!("\n  ... and {} more", indices.len() - 8));
    }
    message.push_str("\nUse a longer session ID, exact title, or exact path.");
    message
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

#[cfg(test)]
mod tests {
    use super::*;

    fn instance(id: &str, title: &str, path: &str) -> Instance {
        let mut inst = Instance::new(title, path);
        inst.id = id.to_string();
        inst
    }

    #[test]
    fn resolve_session_index_rejects_ambiguous_title() {
        let instances = vec![
            instance("aaa111", "same", "/repo/one"),
            instance("bbb222", "same", "/repo/two"),
        ];

        let error = resolve_session_index("same", &instances).unwrap_err();
        let message = error.to_string();

        assert!(message.contains("Ambiguous session identifier 'same'"));
        assert!(message.contains("aaa111"));
        assert!(message.contains("bbb222"));
    }

    #[test]
    fn resolve_session_index_rejects_ambiguous_id_prefix() {
        let instances = vec![
            instance("abcdef111", "one", "/repo/one"),
            instance("abcdef222", "two", "/repo/two"),
        ];

        let error = resolve_session_index("abcdef", &instances).unwrap_err();
        let message = error.to_string();

        assert!(message.contains("Ambiguous session identifier 'abcdef'"));
        assert!(message.contains("one"));
        assert!(message.contains("two"));
    }

    #[test]
    fn resolve_session_index_accepts_exact_id_before_ambiguous_prefix() {
        let instances = vec![
            instance("abcdef", "exact", "/repo/exact"),
            instance("abcdef222", "prefix", "/repo/prefix"),
        ];

        assert_eq!(resolve_session_index("abcdef", &instances).unwrap(), 0);
    }
}
