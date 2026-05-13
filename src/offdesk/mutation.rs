//! Pre-mutation checkpoint and rollback artifacts.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

use super::redaction::operator_safe_text;

const SNAPSHOT_DIR: &str = "mutation_snapshots";
const MAX_INLINE_SNAPSHOT_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MutationSnapshot {
    pub mutation_id: String,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub target_path: String,
    pub mutation_kind: String,
    pub before_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_excerpt_or_snapshot_path: Option<String>,
    pub diff_preview: String,
    pub created_at: DateTime<Utc>,
    pub created_by: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub git_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub git_diff_preview: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MutationSnapshotRequest {
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub target_path: PathBuf,
    pub mutation_kind: String,
    pub diff_preview: String,
    pub created_by: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SnapshotPolicy {
    pub snapshot_not_applicable: bool,
}

impl SnapshotPolicy {
    pub fn require_snapshot() -> Self {
        Self {
            snapshot_not_applicable: false,
        }
    }

    pub fn not_applicable() -> Self {
        Self {
            snapshot_not_applicable: true,
        }
    }
}

#[derive(Debug, Clone)]
pub struct MutationSnapshotStore {
    root: PathBuf,
}

impl MutationSnapshotStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn snapshots_dir(&self) -> PathBuf {
        self.root.join(SNAPSHOT_DIR)
    }

    pub fn create_snapshot(&self, request: MutationSnapshotRequest) -> Result<MutationSnapshot> {
        fs::create_dir_all(self.snapshots_dir())?;

        let mutation_id = format!("mutation_{}", Uuid::new_v4());
        let target = request.target_path.as_path();
        let target_display = target.to_string_lossy().to_string();
        let before_bytes = fs::read(target).unwrap_or_default();
        let before_hash = sha256_hex(&before_bytes);
        let before_excerpt_or_snapshot_path =
            self.write_before_snapshot(&mutation_id, &before_bytes)?;
        let git_status = git_status_for_target(target).map(|status| operator_safe_text(&status));
        let git_diff_preview = git_diff_for_target(target)
            .map(|diff| truncate_chars(&operator_safe_text(&diff), 4000));

        let snapshot = MutationSnapshot {
            mutation_id,
            project_key: request.project_key,
            request_id: request.request_id,
            task_id: request.task_id,
            target_path: target_display,
            mutation_kind: request.mutation_kind,
            before_hash,
            before_excerpt_or_snapshot_path,
            diff_preview: truncate_chars(&operator_safe_text(&request.diff_preview), 4000),
            created_at: Utc::now(),
            created_by: operator_safe_text(&request.created_by),
            git_status,
            git_diff_preview,
        };

        let path = self.snapshot_path(&snapshot.mutation_id);
        fs::write(&path, serde_json::to_string_pretty(&snapshot)?)
            .with_context(|| format!("write mutation snapshot {}", path.display()))?;
        Ok(snapshot)
    }

    pub fn load(&self, mutation_id: &str) -> Result<Option<MutationSnapshot>> {
        let path = self.snapshot_path(mutation_id);
        if !path.exists() {
            return Ok(None);
        }
        let content = fs::read_to_string(path)?;
        Ok(Some(serde_json::from_str(&content)?))
    }

    pub fn snapshot_exists(&self, mutation_id: &str) -> bool {
        self.snapshot_path(mutation_id).exists()
    }

    pub fn can_execute_canonical_mutation(
        &self,
        mutation_id: Option<&str>,
        policy: SnapshotPolicy,
    ) -> bool {
        if policy.snapshot_not_applicable {
            return true;
        }
        mutation_id.is_some_and(|mutation_id| self.snapshot_exists(mutation_id))
    }

    fn snapshot_path(&self, mutation_id: &str) -> PathBuf {
        self.snapshots_dir().join(format!("{mutation_id}.json"))
    }

    fn write_before_snapshot(
        &self,
        mutation_id: &str,
        before_bytes: &[u8],
    ) -> Result<Option<String>> {
        if before_bytes.is_empty() {
            return Ok(None);
        }
        let snapshot_path = self.snapshots_dir().join(format!("{mutation_id}.before"));
        let bytes = if before_bytes.len() > MAX_INLINE_SNAPSHOT_BYTES {
            &before_bytes[..MAX_INLINE_SNAPSHOT_BYTES]
        } else {
            before_bytes
        };
        fs::write(&snapshot_path, bytes)?;
        Ok(Some(snapshot_path.to_string_lossy().to_string()))
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    value.chars().take(max_chars).collect()
}

fn git_root_for_target(target: &Path) -> Option<PathBuf> {
    let cwd = if target.is_dir() {
        target
    } else {
        target.parent()?
    };
    let output = Command::new("git")
        .arg("-C")
        .arg(cwd)
        .args(["rev-parse", "--show-toplevel"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let root = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if root.is_empty() {
        None
    } else {
        Some(PathBuf::from(root))
    }
}

fn git_status_for_target(target: &Path) -> Option<String> {
    let root = git_root_for_target(target)?;
    let output = Command::new("git")
        .arg("-C")
        .arg(&root)
        .args(["status", "--short", "--"])
        .arg(target)
        .output()
        .ok()?;
    if output.status.success() {
        Some(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        None
    }
}

fn git_diff_for_target(target: &Path) -> Option<String> {
    let root = git_root_for_target(target)?;
    let output = Command::new("git")
        .arg("-C")
        .arg(&root)
        .args(["diff", "--"])
        .arg(target)
        .output()
        .ok()?;
    if output.status.success() {
        Some(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn snapshot_request(target: PathBuf, diff_preview: &str) -> MutationSnapshotRequest {
        MutationSnapshotRequest {
            project_key: "project".to_string(),
            request_id: "request".to_string(),
            task_id: "task".to_string(),
            target_path: target,
            mutation_kind: "canonical_syncback".to_string(),
            diff_preview: diff_preview.to_string(),
            created_by: "worker".to_string(),
        }
    }

    #[test]
    fn canonical_mutation_requires_snapshot_artifact() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        assert!(!store.can_execute_canonical_mutation(None, SnapshotPolicy::require_snapshot()));

        let target = temp.path().join("target.txt");
        fs::write(&target, "before")?;
        let snapshot = store.create_snapshot(snapshot_request(target, "diff preview"))?;

        assert!(store.can_execute_canonical_mutation(
            Some(&snapshot.mutation_id),
            SnapshotPolicy::require_snapshot()
        ));
        Ok(())
    }

    #[test]
    fn snapshot_includes_target_hash_and_diff_preview() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        let target = temp.path().join("target.txt");
        fs::write(&target, "before")?;

        let snapshot = store.create_snapshot(snapshot_request(
            target.clone(),
            "diff preview token=sk-secretsecretsecret",
        ))?;

        assert_eq!(snapshot.target_path, target.to_string_lossy().to_string());
        assert_eq!(snapshot.before_hash, sha256_hex(b"before"));
        assert!(snapshot
            .before_excerpt_or_snapshot_path
            .as_deref()
            .is_some_and(|path| Path::new(path).exists()));
        assert!(snapshot.diff_preview.contains("diff preview"));
        assert!(!snapshot.diff_preview.contains("sk-secret"));
        assert!(store.load(&snapshot.mutation_id)?.is_some());
        Ok(())
    }

    #[test]
    fn explicit_not_applicable_policy_allows_without_snapshot() {
        let temp = tempdir().expect("temp dir");
        let store = MutationSnapshotStore::new(temp.path());
        assert!(store.can_execute_canonical_mutation(None, SnapshotPolicy::not_applicable()));
    }
}
