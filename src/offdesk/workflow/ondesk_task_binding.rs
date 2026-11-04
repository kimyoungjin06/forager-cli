//! Opt-in Ondesk source binding carried by existing execution artifact refs.

use std::io::Read;
use std::path::Path;

use anyhow::{ensure, Context, Result};

use super::ondesk_plan::{inspect, ReviewState, MAX_PLAN_BYTES};
use crate::offdesk::{CapabilityArtifactRef, OffdeskTask};
use crate::process::{self, FilesystemObjectSnapshot};

pub const BINDING_PREFIX: &str = "ondesk-plan-sha256:";

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OndeskSourceSnapshot {
    pub source_sha256: String,
    pub source: String,
}

impl OndeskSourceSnapshot {
    pub fn task(&self, project_key: &str, task_id: &str) -> Result<super::ondesk_plan::PlanTask> {
        let inspected = inspect(&self.source, project_key)?;
        ensure!(
            inspected.source_sha256 == self.source_sha256,
            "Ondesk snapshot hash mismatch"
        );
        inspected
            .plan
            .tasks
            .into_iter()
            .find(|task| task.task_id == task_id)
            .context("Ondesk snapshot task missing")
    }
}

pub fn validate_task_source(task: &OffdeskTask) -> Result<()> {
    capture_task_source(task).map(|_| ())
}

pub fn capture_task_source(task: &OffdeskTask) -> Result<Option<OndeskSourceSnapshot>> {
    let bindings: Vec<_> = task
        .artifact_refs
        .iter()
        .filter(|artifact| artifact.artifact_id.starts_with("ondesk-plan"))
        .collect();
    ensure!(bindings.len() <= 1, "multiple Ondesk source bindings");
    if let Some(binding) = bindings.first() {
        return validate_binding(
            Path::new(&task.workdir),
            &task.project_key,
            &task.task_id,
            binding,
        )
        .map(Some);
    }
    Ok(None)
}

fn validate_binding(
    root_path: &Path,
    project_key: &str,
    task_id: &str,
    binding: &CapabilityArtifactRef,
) -> Result<OndeskSourceSnapshot> {
    let expected = binding
        .artifact_id
        .strip_prefix(BINDING_PREFIX)
        .context("invalid Ondesk binding ID")?;
    ensure!(
        expected.len() == 64
            && expected
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid Ondesk binding hash"
    );
    let path = Path::new(
        binding
            .path
            .as_deref()
            .context("Ondesk source path missing")?,
    );
    ensure!(path.is_absolute(), "Ondesk source path must be absolute");
    let relative = path
        .strip_prefix(root_path)
        .context("Ondesk source is outside task workdir")?;
    let root = process::open_bounded_root(root_path)?;
    let file = process::open_bounded_regular_file(&root, relative)?;
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "Ondesk source must have one link"
    );
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut reader = file.take(MAX_PLAN_BYTES as u64 + 1);
    let mut source = String::new();
    reader.read_to_string(&mut source)?;
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?),
        "Ondesk source changed during read"
    );
    let inspected = inspect(&source, project_key)?;
    ensure!(
        inspected.source_sha256 == expected,
        "Ondesk plan baseline changed; new review required"
    );
    let task = inspected
        .plan
        .tasks
        .iter()
        .find(|task| task.task_id == task_id)
        .context("bound Ondesk task missing")?;
    ensure!(
        matches!(
            task.review_state,
            ReviewState::Unreviewed | ReviewState::Rework | ReviewState::PartiallyAccepted
        ),
        "Ondesk task is completed, deferred, or awaiting review"
    );
    for dependency in &task.depends_on {
        ensure!(
            inspected
                .plan
                .tasks
                .iter()
                .any(|task| &task.task_id == dependency
                    && task.review_state == ReviewState::Accepted),
            "Ondesk dependency is not accepted"
        );
    }
    let active = process::open_bounded_regular_file(&root, relative)?;
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&active.metadata()?),
        "Ondesk source replaced during validation"
    );
    let active_root = process::open_bounded_root(root_path)?;
    ensure!(
        process::filesystem_file_identity(&root.metadata()?)
            == process::filesystem_file_identity(&active_root.metadata()?),
        "Ondesk project root changed"
    );
    Ok(OndeskSourceSnapshot {
        source_sha256: expected.to_owned(),
        source,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn document(state: &str) -> String {
        format!(
            "```forager-plan\n{}\n```\n",
            json!({"schema":"ondesk_task_plan.v1", "project_key":"demo", "tasks":[{"task_id":"T-1","goal":"fix and verify","completion_criteria":["verified"],"depends_on":[],"review_state":state,"evidence_refs":["receipt"]}]})
        )
    }
    #[test]
    fn snapshot_validates_content_identity_without_live_files() {
        let source = document("unreviewed");
        let mut snapshot = OndeskSourceSnapshot {
            source_sha256: inspect(&source, "demo").unwrap().source_sha256,
            source,
        };
        let original = snapshot.task("demo", "T-1").unwrap();
        assert_eq!(original.goal, "fix and verify");
        assert_eq!(original.completion_criteria, vec!["verified"]);
        assert!(snapshot.task("other", "T-1").is_err());
        assert!(snapshot.task("demo", "T-2").is_err());
        snapshot.source.push('\n');
        assert!(snapshot.task("demo", "T-1").is_err());
    }

    #[test]
    fn live_binding_rejects_drift_wrong_identity_and_review_pending() {
        let dir = tempfile::tempdir_in(fs::canonicalize(std::env::temp_dir()).unwrap()).unwrap();
        let path = dir.path().join("plan.md");
        let source = document("unreviewed");
        fs::write(&path, &source).unwrap();
        let mut binding = CapabilityArtifactRef {
            artifact_id: format!(
                "{BINDING_PREFIX}{}",
                inspect(&source, "demo").unwrap().source_sha256
            ),
            path: Some(path.display().to_string()),
            present: false,
        };
        // The cached presence bit is not live evidence in either direction.
        assert!(validate_binding(dir.path(), "demo", "T-1", &binding).is_ok());
        assert!(validate_binding(dir.path(), "demo", "T-2", &binding).is_err());
        assert!(validate_binding(dir.path(), "other", "T-1", &binding).is_err());
        fs::write(&path, format!("{source}\n")).unwrap();
        assert!(validate_binding(dir.path(), "demo", "T-1", &binding).is_err());
        for state in ["pending_review", "accepted", "deferred"] {
            let source = document(state);
            fs::write(&path, &source).unwrap();
            binding.artifact_id = format!(
                "{BINDING_PREFIX}{}",
                inspect(&source, "demo").unwrap().source_sha256
            );
            assert!(validate_binding(dir.path(), "demo", "T-1", &binding).is_err());
        }
    }
}
