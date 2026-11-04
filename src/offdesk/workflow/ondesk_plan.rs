//! Read-only task projections from an explicitly selected owning Markdown plan.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{bail, ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const PLAN_SCHEMA: &str = "ondesk_task_plan.v1";
pub const MAX_PLAN_BYTES: usize = 1_048_576;
const MAX_TASKS: usize = 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskPlan {
    pub schema: String,
    pub project_key: String,
    pub tasks: Vec<PlanTask>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanTask {
    pub task_id: String,
    pub goal: String,
    pub completion_criteria: Vec<String>,
    pub depends_on: Vec<String>,
    pub review_state: ReviewState,
    pub evidence_refs: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReviewState {
    Unreviewed,
    PendingReview,
    Accepted,
    PartiallyAccepted,
    Rework,
    Deferred,
}

#[derive(Debug, Serialize)]
pub struct PlanInspection {
    pub schema: &'static str,
    pub source_sha256: String,
    pub plan: TaskPlan,
    pub does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Serialize)]
pub struct PlanPreview {
    pub schema: &'static str,
    pub project_key: String,
    pub baseline_sha256: String,
    pub candidate_sha256: String,
    pub added: Vec<String>,
    pub changed: Vec<String>,
    pub removed: Vec<String>,
    pub does_not_authorize: Vec<&'static str>,
}

fn denials() -> Vec<&'static str> {
    vec![
        "plan_write",
        "approval",
        "launch",
        "accepted_truth",
        "wiki_promotion",
    ]
}

pub(super) fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .chars()
            .all(|c| c.is_alphanumeric() || "-_.".contains(c))
}

/// Replace only the owning fence body, preserving narrative and examples.
pub fn with_plan(source: &str, plan: &TaskPlan) -> Result<String> {
    inspect(source, &plan.project_key)?;
    validate(plan, &plan.project_key)?;
    let mut offset = 0;
    let mut start = None;
    let mut outer = None;
    for line in source.split_inclusive('\n') {
        let trimmed = line.trim();
        if let Some(begin) = start {
            if trimmed == "```" {
                let candidate = format!(
                    "{}{}\n{}",
                    &source[..begin],
                    serde_json::to_string_pretty(plan)?,
                    &source[offset..]
                );
                inspect(&candidate, &plan.project_key)?;
                return Ok(candidate);
            }
        } else if let Some((character, count)) = outer {
            if trimmed.chars().all(|c| c == character) && trimmed.len() >= count {
                outer = None;
            }
        } else if line.trim_end() == "```forager-plan" {
            start = Some(offset + line.len());
        } else if let Some(character @ ('`' | '~')) = trimmed.chars().next() {
            let count = trimmed.chars().take_while(|c| *c == character).count();
            if count >= 3 {
                outer = Some((character, count));
            }
        }
        offset += line.len();
    }
    bail!("owning plan fence missing")
}

pub fn inspect(source: &str, project_key: &str) -> Result<PlanInspection> {
    ensure!(source.len() <= MAX_PLAN_BYTES, "plan exceeds byte budget");
    // A dedicated fence makes ownership explicit; ordinary checklists and
    // examples are not silently imported as authoritative tasks.
    let mut body = None;
    let mut active: Option<String> = None;
    let mut outer_fence: Option<(char, usize)> = None;
    for line in source.lines() {
        let trimmed = line.trim();
        if let Some(ref mut text) = active {
            if trimmed == "```" {
                body = active.take();
            } else {
                text.push_str(line);
                text.push('\n');
            }
            continue;
        }
        if let Some((character, count)) = outer_fence {
            if trimmed.chars().all(|c| c == character) && trimmed.len() >= count {
                outer_fence = None;
            }
            continue;
        }
        if line.trim_end() == "```forager-plan" {
            ensure!(body.is_none(), "multiple forager-plan blocks are ambiguous");
            active = Some(String::new());
        } else if let Some(character @ ('`' | '~')) = trimmed.chars().next() {
            let count = trimmed.chars().take_while(|c| *c == character).count();
            if count >= 3 {
                outer_fence = Some((character, count));
            }
        }
    }
    ensure!(active.is_none(), "unterminated forager-plan block");
    let body = body.context("no explicit forager-plan block; no tasks imported")?;
    let plan: TaskPlan = serde_json::from_str(&body).context("invalid task plan JSON")?;
    validate(&plan, project_key)?;
    Ok(PlanInspection {
        schema: "ondesk_task_plan_inspection.v1",
        source_sha256: format!("{:x}", Sha256::digest(source.as_bytes())),
        plan,
        does_not_authorize: denials(),
    })
}

fn validate(plan: &TaskPlan, project_key: &str) -> Result<()> {
    ensure!(plan.schema == PLAN_SCHEMA, "unsupported task plan schema");
    ensure!(valid_id(&plan.project_key), "invalid project key");
    ensure!(plan.project_key == project_key, "project identity mismatch");
    ensure!(plan.tasks.len() <= MAX_TASKS, "too many tasks");
    let mut tasks = BTreeMap::new();
    for task in &plan.tasks {
        ensure!(valid_id(&task.task_id), "invalid task ID");
        ensure!(
            tasks.insert(&task.task_id, task).is_none(),
            "duplicate task ID"
        );
        ensure!(!task.goal.trim().is_empty(), "task goal is missing");
        ensure!(
            !task.completion_criteria.is_empty()
                && task
                    .completion_criteria
                    .iter()
                    .all(|c| !c.trim().is_empty()),
            "completion criteria are missing"
        );
        ensure!(
            task.evidence_refs.iter().all(|r| !r.trim().is_empty()),
            "empty evidence reference"
        );
        if matches!(
            task.review_state,
            ReviewState::Accepted | ReviewState::PartiallyAccepted
        ) {
            ensure!(
                !task.evidence_refs.is_empty(),
                "reviewed result needs evidence references"
            );
        }
    }
    // Iterative traversal avoids stack exhaustion on a long dependency chain.
    let mut incoming = BTreeMap::new();
    let mut dependents: BTreeMap<&String, Vec<&String>> = BTreeMap::new();
    for task in &plan.tasks {
        let mut seen = BTreeSet::new();
        for dependency in &task.depends_on {
            ensure!(tasks.contains_key(dependency), "missing dependency");
            ensure!(seen.insert(dependency), "duplicate dependency");
            dependents
                .entry(dependency)
                .or_default()
                .push(&task.task_id);
        }
        incoming.insert(&task.task_id, task.depends_on.len());
    }
    let mut ready: Vec<_> = incoming
        .iter()
        .filter_map(|(id, n)| (*n == 0).then_some(*id))
        .collect();
    let mut visited = 0;
    while let Some(id) = ready.pop() {
        visited += 1;
        for next in dependents.get(id).into_iter().flatten() {
            let count = incoming.get_mut(next).expect("validated task identity");
            *count -= 1;
            if *count == 0 {
                ready.push(*next);
            }
        }
    }
    ensure!(visited == plan.tasks.len(), "dependency cycle");
    Ok(())
}

pub fn preview(
    source: &str,
    candidate: &str,
    project_key: &str,
    expected_sha256: &str,
) -> Result<PlanPreview> {
    let current = inspect(source, project_key)?;
    ensure!(
        current.source_sha256 == expected_sha256,
        "stale plan baseline"
    );
    let proposed = inspect(candidate, project_key)?;
    let before: BTreeMap<_, _> = current.plan.tasks.iter().map(|t| (&t.task_id, t)).collect();
    let after: BTreeMap<_, _> = proposed
        .plan
        .tasks
        .iter()
        .map(|t| (&t.task_id, t))
        .collect();
    if expected_sha256.len() != 64 {
        bail!("invalid baseline hash");
    }
    Ok(PlanPreview {
        schema: "ondesk_task_plan_preview.v1",
        project_key: project_key.to_owned(),
        baseline_sha256: current.source_sha256,
        candidate_sha256: proposed.source_sha256,
        added: after
            .keys()
            .filter(|id| !before.contains_key(*id))
            .map(|id| (*id).clone())
            .collect(),
        changed: after
            .iter()
            .filter(|(id, task)| before.get(*id).is_some_and(|old| old != *task))
            .map(|(id, _)| (*id).clone())
            .collect(),
        removed: before
            .keys()
            .filter(|id| !after.contains_key(*id))
            .map(|id| (*id).clone())
            .collect(),
        does_not_authorize: denials(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    fn task(id: &str) -> Value {
        json!({"task_id":id,"goal":"fix and verify","completion_criteria":["regression passes"],
            "depends_on":[],"review_state":"unreviewed","evidence_refs":[]})
    }

    fn document(tasks: Vec<Value>) -> String {
        format!(
            "# Existing plan\n\n```forager-plan\n{}\n```\n",
            json!({"schema":PLAN_SCHEMA,"project_key":"demo","tasks":tasks})
        )
    }

    #[test]
    fn inspection_preserves_identity_and_source_hash() {
        let source = document(vec![task("T-1")]);
        let output = inspect(&source, "demo").unwrap();
        assert_eq!(output.plan.tasks[0].task_id, "T-1");
        assert_ne!(
            output.source_sha256,
            inspect(&(source + "\n"), "demo").unwrap().source_sha256
        );
        assert!(output.does_not_authorize.contains(&"accepted_truth"));
        let roundtrip: TaskPlan =
            serde_json::from_value(serde_json::to_value(&output.plan).unwrap()).unwrap();
        assert_eq!(roundtrip, output.plan);
    }

    #[test]
    fn refuses_ambiguous_missing_and_nested_examples() {
        let source = document(vec![]);
        for bad in [
            "- [ ] T-1".to_owned(),
            source.repeat(2),
            format!("````markdown\n{source}\n````"),
            "```forager-plan\n{}".to_owned(),
        ] {
            assert!(inspect(&bad, "demo").is_err());
        }
        assert!(inspect(&source, "other").is_err());
    }

    #[test]
    fn validates_dependencies_and_ids() {
        assert!(inspect(&document(vec![task("A"), task("A")]), "demo").is_err());
        let mut a = task("A");
        a["depends_on"] = json!(["missing"]);
        assert!(inspect(&document(vec![a.clone()]), "demo").is_err());
        a["depends_on"] = json!(["A"]);
        assert!(inspect(&document(vec![a.clone()]), "demo").is_err());
        a["depends_on"] = json!(["B"]);
        let mut b = task("B");
        b["depends_on"] = json!(["A"]);
        assert!(inspect(&document(vec![a.clone(), b]), "demo").is_err());
        assert!(inspect(&document(vec![a, task("B")]), "demo").is_ok());
    }

    #[test]
    fn does_not_translate_execution_into_acceptance() {
        let mut a = task("A");
        a["review_state"] = json!("completed");
        assert!(inspect(&document(vec![a.clone()]), "demo").is_err());
        a["review_state"] = json!("partially_accepted");
        assert!(inspect(&document(vec![a.clone()]), "demo").is_err());
        a["evidence_refs"] = json!(["tests/receipt.json"]);
        assert!(inspect(&document(vec![a]), "demo").is_ok());
    }

    #[test]
    fn preview_is_deterministic_and_rejects_stale_baselines() {
        let source = document(vec![task("A"), task("B")]);
        let hash = inspect(&source, "demo").unwrap().source_sha256;
        let mut b = task("B");
        b["review_state"] = json!("pending_review");
        let candidate = document(vec![b, task("C")]);
        let output = preview(&source, &candidate, "demo", &hash).unwrap();
        assert_eq!(output.added, ["C"]);
        assert_eq!(output.changed, ["B"]);
        assert_eq!(output.removed, ["A"]);
        assert_eq!(
            serde_json::to_value(&output).unwrap(),
            serde_json::to_value(preview(&source, &candidate, "demo", &hash).unwrap()).unwrap()
        );
        assert!(preview(&(source + "\n"), &candidate, "demo", &hash).is_err());
    }

    #[test]
    fn rejects_missing_criteria_unknown_fields_and_oversize() {
        let mut a = task("A");
        a["completion_criteria"] = json!([]);
        assert!(inspect(&document(vec![a]), "demo").is_err());
        let mut a = task("A");
        a["launch"] = json!(true);
        assert!(inspect(&document(vec![a]), "demo").is_err());
        assert!(inspect(&" ".repeat(MAX_PLAN_BYTES + 1), "demo").is_err());
    }

    #[test]
    fn indented_code_example_is_not_an_owning_plan() {
        let source = document(vec![task("A")]);
        let indented = source
            .lines()
            .map(|line| format!("    {line}\n"))
            .collect::<String>();
        assert!(inspect(&indented, "demo").is_err());
    }

    #[test]
    fn bounded_long_chain_does_not_require_recursive_traversal() {
        let tasks = (0..MAX_TASKS)
            .map(|index| {
                let mut value = task(&format!("T-{index}"));
                if index > 0 {
                    value["depends_on"] = json!([format!("T-{}", index - 1)]);
                }
                value
            })
            .collect();
        assert!(inspect(&document(tasks), "demo").is_ok());
    }
}
