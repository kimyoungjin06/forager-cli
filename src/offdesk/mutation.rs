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
    #[serde(default = "default_snapshot_schema_version")]
    pub snapshot_schema_version: u32,
    pub mutation_id: String,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub target_path: String,
    pub mutation_kind: String,
    #[serde(default = "default_true")]
    pub target_exists_before: bool,
    #[serde(default)]
    pub before_size_bytes: u64,
    pub before_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_excerpt_or_snapshot_path: Option<String>,
    #[serde(default)]
    pub snapshot_truncated: bool,
    #[serde(default)]
    pub rollback_available: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rollback_blockers: Vec<String>,
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

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MutationSnapshotVerification {
    pub mutation_id: String,
    pub snapshot_present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub snapshot: Option<MutationSnapshot>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_exists_now: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_current_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_current_matches_before: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_snapshot_path: Option<String>,
    pub before_snapshot_present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_snapshot_hash_matches: Option<bool>,
    pub rollback_available: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blockers: Vec<String>,
    pub checked_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationRestoreOperation {
    RestoreFile,
    DeleteFile,
    Unavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MutationRestorePlan {
    pub mutation_id: String,
    pub target_path: String,
    pub operation: MutationRestoreOperation,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub before_snapshot_path: Option<String>,
    pub before_hash: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub current_hash: Option<String>,
    pub rollback_available: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blockers: Vec<String>,
    pub generated_at: DateTime<Utc>,
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
        let target_exists_before = target.exists();
        let before_bytes = if target_exists_before {
            fs::read(target)
                .with_context(|| format!("read mutation target {}", target.display()))?
        } else {
            Vec::new()
        };
        let before_size_bytes = before_bytes.len() as u64;
        let before_hash = sha256_hex(&before_bytes);
        let snapshot_truncated = before_bytes.len() > MAX_INLINE_SNAPSHOT_BYTES;
        let before_excerpt_or_snapshot_path =
            self.write_before_snapshot(&mutation_id, &before_bytes)?;
        let rollback_blockers =
            rollback_blockers_for_new_snapshot(target_exists_before, snapshot_truncated);
        let rollback_available = rollback_blockers.is_empty();
        let git_status = git_status_for_target(target).map(|status| operator_safe_text(&status));
        let git_diff_preview = git_diff_for_target(target)
            .map(|diff| truncate_chars(&operator_safe_text(&diff), 4000));

        let snapshot = MutationSnapshot {
            snapshot_schema_version: default_snapshot_schema_version(),
            mutation_id,
            project_key: request.project_key,
            request_id: request.request_id,
            task_id: request.task_id,
            target_path: target_display,
            mutation_kind: request.mutation_kind,
            target_exists_before,
            before_size_bytes,
            before_hash,
            before_excerpt_or_snapshot_path,
            snapshot_truncated,
            rollback_available,
            rollback_blockers,
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

    pub fn list(&self) -> Result<Vec<MutationSnapshot>> {
        let snapshots_dir = self.snapshots_dir();
        if !snapshots_dir.exists() {
            return Ok(Vec::new());
        }

        let mut snapshots = Vec::new();
        for entry in fs::read_dir(snapshots_dir)? {
            let entry = entry?;
            let path = entry.path();
            if path.extension().and_then(|extension| extension.to_str()) != Some("json") {
                continue;
            }
            let content = fs::read_to_string(&path)
                .with_context(|| format!("read mutation snapshot {}", path.display()))?;
            let snapshot = serde_json::from_str::<MutationSnapshot>(&content)
                .with_context(|| format!("parse mutation snapshot {}", path.display()))?;
            snapshots.push(snapshot);
        }
        snapshots.sort_by(|left, right| {
            left.created_at
                .cmp(&right.created_at)
                .then_with(|| left.mutation_id.cmp(&right.mutation_id))
        });
        Ok(snapshots)
    }

    pub fn verify_snapshot(
        &self,
        mutation_id: &str,
        now: DateTime<Utc>,
    ) -> Result<MutationSnapshotVerification> {
        let Some(snapshot) = self.load(mutation_id)? else {
            return Ok(MutationSnapshotVerification {
                mutation_id: mutation_id.to_string(),
                snapshot_present: false,
                snapshot: None,
                target_path: None,
                target_exists_now: None,
                target_current_hash: None,
                target_current_matches_before: None,
                before_snapshot_path: None,
                before_snapshot_present: false,
                before_snapshot_hash_matches: None,
                rollback_available: false,
                blockers: vec!["snapshot artifact missing".to_string()],
                checked_at: now,
            });
        };

        let target = PathBuf::from(&snapshot.target_path);
        let target_exists_now = target.exists();
        let target_current_hash = if target_exists_now {
            Some(sha256_hex(&fs::read(&target).with_context(|| {
                format!("read mutation target {}", target.display())
            })?))
        } else {
            None
        };
        let target_current_matches_before = if snapshot.target_exists_before {
            Some(target_current_hash.as_deref() == Some(snapshot.before_hash.as_str()))
        } else {
            Some(!target_exists_now)
        };

        let before_snapshot_path = snapshot.before_excerpt_or_snapshot_path.clone();
        let before_snapshot = before_snapshot_path.as_deref().map(Path::new);
        let before_snapshot_present = before_snapshot.is_some_and(Path::exists);
        let before_snapshot_hash_matches =
            before_snapshot_hash_matches(&snapshot, before_snapshot)?;
        let blockers = verification_blockers(
            &snapshot,
            before_snapshot_present,
            before_snapshot_hash_matches,
        );
        let rollback_available = blockers.is_empty();

        Ok(MutationSnapshotVerification {
            mutation_id: mutation_id.to_string(),
            snapshot_present: true,
            snapshot: Some(operator_safe_snapshot(snapshot)),
            target_path: Some(target.to_string_lossy().to_string()),
            target_exists_now: Some(target_exists_now),
            target_current_hash,
            target_current_matches_before,
            before_snapshot_path,
            before_snapshot_present,
            before_snapshot_hash_matches,
            rollback_available,
            blockers,
            checked_at: now,
        })
    }

    pub fn restore_plan(
        &self,
        mutation_id: &str,
        now: DateTime<Utc>,
    ) -> Result<MutationRestorePlan> {
        let verification = self.verify_snapshot(mutation_id, now)?;
        let Some(snapshot) = verification.snapshot.as_ref() else {
            return Ok(MutationRestorePlan {
                mutation_id: mutation_id.to_string(),
                target_path: String::new(),
                operation: MutationRestoreOperation::Unavailable,
                before_snapshot_path: None,
                before_hash: String::new(),
                current_hash: None,
                rollback_available: false,
                blockers: verification.blockers,
                generated_at: now,
            });
        };
        let operation = if !verification.rollback_available {
            MutationRestoreOperation::Unavailable
        } else if snapshot.target_exists_before {
            MutationRestoreOperation::RestoreFile
        } else {
            MutationRestoreOperation::DeleteFile
        };

        Ok(MutationRestorePlan {
            mutation_id: mutation_id.to_string(),
            target_path: snapshot.target_path.clone(),
            operation,
            before_snapshot_path: snapshot.before_excerpt_or_snapshot_path.clone(),
            before_hash: snapshot.before_hash.clone(),
            current_hash: verification.target_current_hash,
            rollback_available: verification.rollback_available,
            blockers: verification.blockers,
            generated_at: now,
        })
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

fn default_snapshot_schema_version() -> u32 {
    1
}

fn default_true() -> bool {
    true
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

fn rollback_blockers_for_new_snapshot(
    _target_exists_before: bool,
    snapshot_truncated: bool,
) -> Vec<String> {
    if snapshot_truncated {
        vec!["before snapshot is truncated evidence only".to_string()]
    } else {
        Vec::new()
    }
}

fn before_snapshot_hash_matches(
    snapshot: &MutationSnapshot,
    before_snapshot: Option<&Path>,
) -> Result<Option<bool>> {
    if !snapshot.target_exists_before {
        return Ok(None);
    }
    if snapshot.before_size_bytes == 0 {
        return Ok(Some(snapshot.before_hash == sha256_hex(&[])));
    }
    let Some(before_snapshot) = before_snapshot else {
        return Ok(None);
    };
    if !before_snapshot.exists() {
        return Ok(None);
    }
    let bytes = fs::read(before_snapshot)
        .with_context(|| format!("read before snapshot {}", before_snapshot.display()))?;
    Ok(Some(sha256_hex(&bytes) == snapshot.before_hash))
}

fn verification_blockers(
    snapshot: &MutationSnapshot,
    before_snapshot_present: bool,
    before_snapshot_hash_matches: Option<bool>,
) -> Vec<String> {
    let mut blockers = snapshot.rollback_blockers.clone();
    if !snapshot.rollback_available {
        blockers.push("snapshot was not marked rollback available".to_string());
    }
    if snapshot.snapshot_truncated {
        blockers.push("before snapshot is truncated evidence only".to_string());
    }
    if snapshot.target_exists_before && snapshot.before_size_bytes > 0 {
        if snapshot.before_excerpt_or_snapshot_path.is_none() {
            blockers.push("before snapshot path missing".to_string());
        } else if !before_snapshot_present {
            blockers.push("before snapshot artifact missing".to_string());
        }
    }
    if before_snapshot_hash_matches == Some(false) {
        blockers.push("before snapshot hash mismatch".to_string());
    }
    blockers.sort();
    blockers.dedup();
    blockers
}

fn operator_safe_snapshot(mut snapshot: MutationSnapshot) -> MutationSnapshot {
    snapshot.diff_preview = operator_safe_text(&snapshot.diff_preview);
    snapshot.created_by = operator_safe_text(&snapshot.created_by);
    snapshot.git_status = snapshot.git_status.as_deref().map(operator_safe_text);
    snapshot.git_diff_preview = snapshot.git_diff_preview.as_deref().map(operator_safe_text);
    snapshot.rollback_blockers = snapshot
        .rollback_blockers
        .into_iter()
        .map(|blocker| operator_safe_text(&blocker))
        .collect();
    snapshot
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
        assert_eq!(snapshot.snapshot_schema_version, 1);
        assert!(snapshot.target_exists_before);
        assert_eq!(snapshot.before_size_bytes, 6);
        assert_eq!(snapshot.before_hash, sha256_hex(b"before"));
        assert!(!snapshot.snapshot_truncated);
        assert!(snapshot.rollback_available);
        assert!(snapshot.rollback_blockers.is_empty());
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
    fn legacy_snapshot_without_rollback_fields_still_loads() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        fs::create_dir_all(store.snapshots_dir())?;
        let now = Utc::now();
        fs::write(
            store.snapshot_path("mutation_legacy"),
            serde_json::to_string_pretty(&serde_json::json!({
                "mutation_id": "mutation_legacy",
                "project_key": "project",
                "request_id": "request",
                "task_id": "task",
                "target_path": temp.path().join("target.txt"),
                "mutation_kind": "canonical_syncback",
                "before_hash": sha256_hex(b"before"),
                "before_excerpt_or_snapshot_path": temp.path().join("missing.before"),
                "diff_preview": "diff preview",
                "created_at": now,
                "created_by": "worker"
            }))?,
        )?;

        let snapshot = store.load("mutation_legacy")?.expect("legacy snapshot");
        assert_eq!(snapshot.snapshot_schema_version, 1);
        assert!(snapshot.target_exists_before);
        assert_eq!(snapshot.before_size_bytes, 0);
        assert!(!snapshot.snapshot_truncated);
        assert!(!snapshot.rollback_available);
        Ok(())
    }

    #[test]
    fn large_snapshot_is_evidence_only_not_rollback_available() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        let target = temp.path().join("target.bin");
        fs::write(&target, vec![b'a'; MAX_INLINE_SNAPSHOT_BYTES + 1])?;

        let snapshot = store.create_snapshot(snapshot_request(target, "large diff"))?;
        assert!(snapshot.snapshot_truncated);
        assert!(!snapshot.rollback_available);
        assert!(snapshot
            .rollback_blockers
            .contains(&"before snapshot is truncated evidence only".to_string()));

        let verification = store.verify_snapshot(&snapshot.mutation_id, Utc::now())?;
        assert!(!verification.rollback_available);
        assert!(verification
            .blockers
            .contains(&"before snapshot is truncated evidence only".to_string()));
        Ok(())
    }

    #[test]
    fn verify_snapshot_reports_missing_before_artifact_and_target_change() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        let target = temp.path().join("target.txt");
        fs::write(&target, "before")?;
        let snapshot = store.create_snapshot(snapshot_request(target.clone(), "diff preview"))?;
        fs::write(&target, "after")?;
        fs::remove_file(
            snapshot
                .before_excerpt_or_snapshot_path
                .as_deref()
                .expect("before path"),
        )?;

        let verification = store.verify_snapshot(&snapshot.mutation_id, Utc::now())?;
        assert!(verification.snapshot_present);
        assert_eq!(verification.target_exists_now, Some(true));
        assert_eq!(verification.target_current_matches_before, Some(false));
        assert!(!verification.before_snapshot_present);
        assert!(!verification.rollback_available);
        assert!(verification
            .blockers
            .contains(&"before snapshot artifact missing".to_string()));
        Ok(())
    }

    #[test]
    fn restore_plan_is_read_only() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());
        let target = temp.path().join("target.txt");
        fs::write(&target, "before")?;
        let snapshot = store.create_snapshot(snapshot_request(target.clone(), "diff preview"))?;
        fs::write(&target, "after")?;

        let plan = store.restore_plan(&snapshot.mutation_id, Utc::now())?;
        assert_eq!(plan.operation, MutationRestoreOperation::RestoreFile);
        assert!(plan.rollback_available);
        assert_eq!(fs::read_to_string(target)?, "after");
        Ok(())
    }

    #[test]
    fn missing_snapshot_verification_is_structured() -> Result<()> {
        let temp = tempdir()?;
        let store = MutationSnapshotStore::new(temp.path());

        let verification = store.verify_snapshot("mutation_missing", Utc::now())?;
        assert!(!verification.snapshot_present);
        assert!(!verification.rollback_available);
        assert_eq!(verification.blockers, vec!["snapshot artifact missing"]);
        Ok(())
    }

    #[test]
    fn explicit_not_applicable_policy_allows_without_snapshot() {
        let temp = tempdir().expect("temp dir");
        let store = MutationSnapshotStore::new(temp.path());
        assert!(store.can_execute_canonical_mutation(None, SnapshotPolicy::not_applicable()));
    }
}
