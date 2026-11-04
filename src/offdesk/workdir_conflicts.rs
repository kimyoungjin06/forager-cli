//! Queue overlap observations shared by preview and bounded-shift launch gates.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use anyhow::{ensure, Result};
use serde_json::{json, Value};

use crate::offdesk::{OffdeskTask, OffdeskTaskStatus};

fn occupies_workdir(task: &OffdeskTask) -> bool {
    matches!(
        task.status,
        OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running | OffdeskTaskStatus::ResumePending
    ) || task.background_ticket_id.is_some()
}

pub(crate) fn inspect_with_runtime(
    profile: &Path,
    tasks: &[OffdeskTask],
    selected_ids: &[String],
) -> Result<Value> {
    let mut observed = tasks.to_vec();
    for task in &mut observed {
        if task.background_ticket_id.is_some()
            && matches!(
                task.status,
                OffdeskTaskStatus::Completed
                    | OffdeskTaskStatus::Cancelled
                    | OffdeskTaskStatus::Failed
            )
            && super::runtime_occupancy::released(profile, task)?
        {
            task.background_ticket_id = None;
        }
    }
    inspect(&observed, selected_ids)
}

fn overlaps(a: &Path, b: &Path) -> bool {
    a.starts_with(b) || b.starts_with(a)
}

pub(crate) fn inspect(tasks: &[OffdeskTask], selected_ids: &[String]) -> Result<Value> {
    ensure!(
        tasks.len() <= 1024,
        "conflict inspection exceeds 1024-task limit"
    );
    let mut ids = HashSet::new();
    ensure!(
        tasks.iter().all(|task| ids.insert(&task.task_id)),
        "conflict queue has duplicate task IDs"
    );
    let selected: HashSet<_> = selected_ids.iter().collect();
    ensure!(
        selected.len() == selected_ids.len(),
        "conflict selection has duplicate task IDs"
    );
    ensure!(
        selected.iter().all(|id| ids.contains(id)),
        "conflict selection task is missing"
    );
    let relevant: Vec<_> = tasks
        .iter()
        .filter(|task| selected.contains(&task.task_id) || occupies_workdir(task))
        .collect();
    let mut paths: Vec<Option<PathBuf>> = Vec::new();
    let mut unresolved = Vec::new();
    for task in &relevant {
        let path = Path::new(&task.workdir);
        let resolved = if path.is_absolute() {
            std::fs::canonicalize(path).ok().filter(|p| p.is_dir())
        } else {
            None
        };
        if resolved.is_none() {
            unresolved.push(task.task_id.clone());
        }
        paths.push(resolved);
    }
    let mut conflicts = Vec::new();
    for (i, a) in relevant.iter().enumerate() {
        for (j, b) in relevant.iter().enumerate().skip(i + 1) {
            let a_selected = selected.contains(&a.task_id);
            let b_selected = selected.contains(&b.task_id);
            if !(a_selected || b_selected) {
                continue;
            }
            if let (Some(a_path), Some(b_path)) = (&paths[i], &paths[j]) {
                if overlaps(a_path, b_path) {
                    conflicts.push(json!({
                        "task_ids":[a.task_id,b.task_id],
                        "reason":if occupies_workdir(a) || occupies_workdir(b) { "active_or_uncertain_queue_occupancy" } else { "selected_workdirs_overlap" },
                        "required_action":if a_selected && b_selected && !occupies_workdir(a) && !occupies_workdir(b) { "serialize_or_isolate_then_revalidate" } else { "resolve_existing_occupancy_then_revalidate" }
                    }));
                }
            }
        }
    }
    Ok(json!({
        "schema":"offdesk_queue_conflicts.v1",
        "status":if !unresolved.is_empty() { "incomplete" } else if !conflicts.is_empty() { "conflicts_detected" } else { "no_observed_queue_overlap" },
        "conflicts":conflicts, "unresolved_task_ids":unresolved,
        "scope":"current_profile_queue_snapshot",
        "runtime_enforced":false,
        "limitations":["not an ownership lease or process liveness check", "other profiles and interactive sessions are not inspected", "shared services, data outside workdirs and future changes are not covered", "serialization is required advice, not a generated execution schedule"]
    }))
}

/// Call under the profile tick lock, before committing any launch reservation.
pub(crate) fn launch_hold_reason(
    profile: &Path,
    tasks: &[OffdeskTask],
    task_id: &str,
) -> Result<Option<&'static str>> {
    let observation = inspect_with_runtime(profile, tasks, &[task_id.to_string()])?;
    match observation["status"].as_str() {
        Some("no_observed_queue_overlap") => Ok(None),
        Some("conflicts_detected") => Ok(Some("shift_workdir_occupied")),
        Some("incomplete") => Ok(Some("shift_workdir_occupancy_unresolved")),
        _ => anyhow::bail!("invalid queue conflict observation"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{BackgroundRunnerKind, OffdeskTaskInput};
    use chrono::Utc;

    fn task(id: &str, path: &Path, status: OffdeskTaskStatus) -> OffdeskTask {
        let mut task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some(id.into()),
                request_id: id.into(),
                project_key: "project".into(),
                capability_id: "dispatch.runtime".into(),
                runner_kind: BackgroundRunnerKind::LocalBackground,
                command: "true".into(),
                workdir: path.display().to_string(),
                execution_brief: None,
                not_before: None,
                mutation_class: None,
                artifact_refs: Vec::new(),
                implementation_packet: None,
                artifact_kind: None,
                agent_mode: None,
                provider_id: None,
                model: None,
                preview: String::new(),
                reason: String::new(),
                log_artifact_path: None,
                result_artifact_path: None,
            },
            Utc::now(),
        );
        task.status = status;
        task
    }

    #[test]
    fn selected_parent_child_need_serialization_but_siblings_do_not() -> Result<()> {
        let root = tempfile::tempdir()?;
        let child = root.path().join("child");
        let sibling = root.path().join("child-other");
        std::fs::create_dir(&child)?;
        std::fs::create_dir(&sibling)?;
        let tasks = vec![
            task("a", root.path(), OffdeskTaskStatus::Queued),
            task("b", &child, OffdeskTaskStatus::Queued),
        ];
        let report = inspect(&tasks, &["a".into(), "b".into()])?;
        assert_eq!(report["status"], "conflicts_detected");
        assert_eq!(
            report["conflicts"][0]["required_action"],
            "serialize_or_isolate_then_revalidate"
        );
        let tasks = vec![
            task("a", &child, OffdeskTaskStatus::Queued),
            task("b", &sibling, OffdeskTaskStatus::Queued),
        ];
        assert_eq!(
            inspect(&tasks, &["a".into(), "b".into()])?["status"],
            "no_observed_queue_overlap"
        );
        Ok(())
    }

    #[test]
    fn out_of_selection_running_and_uncertain_tasks_are_not_ignored() -> Result<()> {
        let root = tempfile::tempdir()?;
        for status in [
            OffdeskTaskStatus::Launched,
            OffdeskTaskStatus::Running,
            OffdeskTaskStatus::ResumePending,
            OffdeskTaskStatus::Failed,
            OffdeskTaskStatus::Cancelled,
            OffdeskTaskStatus::Completed,
        ] {
            let mut occupied = task("other", root.path(), status);
            occupied.background_ticket_id = Some("ticket".into());
            let tasks = vec![
                task("chosen", root.path(), OffdeskTaskStatus::Queued),
                occupied,
            ];
            let report = inspect(&tasks, &["chosen".into()])?;
            assert_eq!(
                report["conflicts"][0]["reason"],
                "active_or_uncertain_queue_occupancy"
            );
            assert_eq!(report["runtime_enforced"], false);
        }
        Ok(())
    }

    #[test]
    fn unresolved_occupancy_is_incomplete_and_terminal_queue_records_do_not_reserve() -> Result<()>
    {
        let root = tempfile::tempdir()?;
        let mut tasks = vec![
            task("a", root.path(), OffdeskTaskStatus::Queued),
            task(
                "b",
                &root.path().join("missing"),
                OffdeskTaskStatus::Running,
            ),
        ];
        assert_eq!(inspect(&tasks, &["a".into()])?["status"], "incomplete");
        for status in [OffdeskTaskStatus::Completed, OffdeskTaskStatus::Cancelled] {
            tasks[1].status = status;
            assert_eq!(
                inspect(&tasks, &["a".into()])?["status"],
                "no_observed_queue_overlap"
            );
        }
        assert!(inspect(&tasks, &["missing".into()]).is_err());
        tasks.push(tasks[0].clone());
        assert!(inspect(&tasks, &["a".into()]).is_err());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn symlink_aliases_are_reported_as_overlap() -> Result<()> {
        let root = tempfile::tempdir()?;
        let real = root.path().join("real");
        let alias = root.path().join("alias");
        std::fs::create_dir(&real)?;
        std::os::unix::fs::symlink(&real, &alias)?;
        let tasks = vec![
            task("a", &real, OffdeskTaskStatus::Queued),
            task("b", &alias, OffdeskTaskStatus::Running),
        ];
        assert_eq!(
            inspect(&tasks, &["a".into()])?["status"],
            "conflicts_detected"
        );
        Ok(())
    }
}
