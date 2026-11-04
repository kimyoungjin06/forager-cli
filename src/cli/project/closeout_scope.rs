//! Read-only owner routing for closeout; no project initialization or writes.

use anyhow::{ensure, Context, Result};
use serde::Deserialize;
use serde_json::Value;
use std::path::{Component, Path, PathBuf};

#[derive(Deserialize)]
struct ScopeSpec {
    schema: String,
    project_key: String,
    subproject_id: String,
    label: String,
    anchor_path: String,
    owning_plan_path: String,
    #[serde(default)]
    read_paths: Vec<String>,
}

fn relative(value: &str) -> Result<&Path> {
    let path = Path::new(value);
    ensure!(
        !value.trim().is_empty()
            && !path.is_absolute()
            && path
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "scope path must be normalized and project-relative"
    );
    Ok(path)
}

pub(crate) fn select(
    root: &Path,
    key: &str,
    owner: Option<&str>,
    spec: Option<&Path>,
) -> Result<(PathBuf, Value)> {
    let root = root.canonicalize()?;
    let mut anchor = ".".to_string();
    let mut selected_owner = owner.map(str::to_string);
    if let Some(spec_path) = spec {
        let before = crate::offdesk::workflow::review_link::read_artifact(spec_path)?;
        let spec: ScopeSpec = serde_json::from_slice(&before)?;
        ensure!(
            spec.schema == "forager_subproject_scope.v1" && spec.project_key == key,
            "subproject schema or project key mismatch"
        );
        let id = spec.subproject_id.trim();
        ensure!(
            !id.is_empty()
                && id.len() <= 64
                && id.as_bytes()[0].is_ascii_alphanumeric()
                && id.bytes().all(|b| b.is_ascii_lowercase()
                    || b.is_ascii_digit()
                    || b == b'-'
                    || b == b'_'),
            "invalid subproject identity"
        );
        ensure!(!spec.label.trim().is_empty(), "missing subproject label");
        let descriptor = crate::process::open_bounded_root(&root)?;
        crate::process::open_bounded_subdirectory(&descriptor, relative(&spec.anchor_path)?)?;
        crate::process::open_bounded_regular_file(&descriptor, relative(&spec.owning_plan_path)?)?;
        ensure!(
            spec.read_paths.len() <= 64,
            "subproject read path budget exceeded"
        );
        for value in &spec.read_paths {
            let path = relative(value)?;
            ensure!(
                crate::process::open_bounded_subdirectory(&descriptor, path).is_ok()
                    || crate::process::open_bounded_regular_file(&descriptor, path).is_ok(),
                "subproject read path is missing or unsafe"
            );
        }
        if let Some(explicit) = owner {
            ensure!(
                spec.owning_plan_path == explicit,
                "conflicting subproject owner"
            );
        }
        ensure!(
            crate::offdesk::workflow::review_link::read_artifact(spec_path)? == before,
            "subproject declaration changed"
        );
        anchor = spec.anchor_path;
        selected_owner = Some(spec.owning_plan_path);
    }
    let preview =
        super::plan_routing::inspect(&root, key, selected_owner.as_deref(), &anchor, &[], false)?;
    if selected_owner.is_some() {
        ensure!(
            preview.selected_existing_owner().is_some(),
            "selected owner is missing or unsafe"
        );
    }
    let audit_root = if anchor == "." {
        root
    } else {
        root.join(anchor)
    };
    ensure!(audit_root.is_dir(), "selected audit scope is missing");
    Ok((
        audit_root,
        serde_json::to_value(preview).context("serialize owner routing")?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    #[test]
    fn declared_subproject_selects_only_anchor_and_rejects_conflicts() -> Result<()> {
        let root = tempdir()?;
        fs::create_dir(root.path().join("annual"))?;
        fs::write(root.path().join("TODO.md"), "# Owner\n")?;
        let spec = root.path().join("scope.json");
        fs::write(
            &spec,
            serde_json::to_vec(&serde_json::json!({
                "schema":"forager_subproject_scope.v1", "project_key":"nano",
                "subproject_id":"annual", "label":"Annual replay", "anchor_path":"annual",
                "owning_plan_path":"TODO.md", "read_paths":["annual"]
            }))?,
        )?;
        let (path, _) = select(root.path(), "nano", Some("TODO.md"), Some(&spec))?;
        assert_eq!(path, root.path().join("annual"));
        assert!(select(root.path(), "wrong", None, Some(&spec)).is_err());
        assert!(select(root.path(), "nano", Some("other.md"), Some(&spec)).is_err());
        fs::remove_dir(root.path().join("annual"))?;
        symlink(root.path(), root.path().join("annual"))?;
        assert!(select(root.path(), "nano", None, Some(&spec)).is_err());
        Ok(())
    }
}
