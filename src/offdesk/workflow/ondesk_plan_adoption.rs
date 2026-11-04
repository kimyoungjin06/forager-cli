//! Explicit, append-only task mapping for an existing plain Markdown plan.
use std::path::Path;

use anyhow::{ensure, Context, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};

use super::{
    ondesk_plan::{inspect, ReviewState, TaskPlan, MAX_PLAN_BYTES},
    ondesk_plan_store::{self, PlanChangeRequest},
};

pub const REQUEST_SCHEMA: &str = "ondesk_plan_adoption_request.v1";

#[derive(Debug, Serialize)]
pub struct AdoptionProposal {
    pub schema: &'static str,
    pub change_request: PlanChangeRequest,
    pub candidate_markdown: String,
    pub selected_task_ids: Vec<String>,
    pub original_bytes_preserved: bool,
    pub task_mapping_provenance: &'static str,
    pub does_not_authorize: Vec<&'static str>,
}

pub fn read_source(path: &Path) -> Result<String> {
    ensure!(path.is_absolute(), "absolute owning plan path required");
    let root = crate::process::open_bounded_root(path.parent().context("plan parent missing")?)?;
    let name = Path::new(path.file_name().context("plan filename missing")?);
    String::from_utf8(ondesk_plan_store::read(&root, name, MAX_PLAN_BYTES)?)
        .context("owning plan is not UTF-8")
}

pub fn validate_initial_tasks(plan: &TaskPlan) -> Result<()> {
    ensure!(!plan.tasks.is_empty(), "explicit task selection required");
    ensure!(
        plan.tasks
            .iter()
            .all(|task| task.review_state == ReviewState::Unreviewed),
        "adoption requires unreviewed tasks; use the review flow for dispositions"
    );
    Ok(())
}

pub fn candidate(source: &str, plan: &TaskPlan) -> Result<String> {
    ensure!(source.len() <= MAX_PLAN_BYTES, "plan exceeds byte budget");
    validate_initial_tasks(plan)?;
    let mut outer = None;
    for line in source.lines() {
        let trimmed = line.trim();
        if let Some((character, count)) = outer {
            if trimmed.chars().all(|c| c == character) && trimmed.len() >= count {
                outer = None;
            }
        } else if line.trim_end() == "```forager-plan" {
            anyhow::bail!("existing forager-plan block; use the normal plan change flow");
        } else if let Some(character @ ('`' | '~')) = trimmed.chars().next() {
            let count = trimmed.chars().take_while(|c| *c == character).count();
            if count >= 3 {
                outer = Some((character, count));
            }
        }
    }
    ensure!(
        outer.is_none(),
        "unterminated Markdown fence prevents adoption"
    );
    let separator = if source.ends_with('\n') { "\n" } else { "\n\n" };
    let candidate = format!(
        "{source}{separator}## Forager Task Mapping\n\nExplicit operator-selected tasks from this owning document. Other checklist items are not imported. This mapping does not accept results or authorize execution.\n\n```forager-plan\n{}\n```\n",
        serde_json::to_string_pretty(plan)?
    );
    inspect(&candidate, &plan.project_key)?;
    Ok(candidate)
}

pub fn validate_append(source: &str, proposed: &str, project_key: &str) -> Result<()> {
    let plan = inspect(proposed, project_key)?.plan;
    ensure!(
        candidate(source, &plan)? == proposed,
        "adoption candidate must preserve the original bytes and append only the task mapping"
    );
    Ok(())
}

pub fn propose(
    source_path: &Path,
    plan: TaskPlan,
    project_key: &str,
    expected_sha256: &str,
    request_id: String,
    decision_ref: String,
) -> Result<AdoptionProposal> {
    ensure!(plan.project_key == project_key, "project identity mismatch");
    let source = read_source(source_path)?;
    let baseline_sha256 = format!("{:x}", Sha256::digest(source.as_bytes()));
    ensure!(baseline_sha256 == expected_sha256, "stale plan baseline");
    let candidate_markdown = candidate(&source, &plan)?;
    let change_request = PlanChangeRequest {
        schema: REQUEST_SCHEMA.into(),
        request_id,
        project_key: plan.project_key,
        source_path: source_path.into(),
        baseline_sha256,
        candidate_sha256: format!("{:x}", Sha256::digest(candidate_markdown.as_bytes())),
        decision_ref,
        review_record: None,
    };
    ondesk_plan_store::validate_request(&change_request)?;
    ensure!(
        read_source(source_path)? == source,
        "plan changed during proposal"
    );
    Ok(AdoptionProposal {
        schema: "ondesk_plan_adoption_proposal.v1",
        change_request,
        candidate_markdown,
        selected_task_ids: plan.tasks.into_iter().map(|task| task.task_id).collect(),
        original_bytes_preserved: true,
        task_mapping_provenance: "explicit_operator_selection_not_inferred_from_checklist",
        does_not_authorize: vec![
            "plan_write",
            "launch",
            "historical_source_rebinding",
            "accepted_truth",
            "deployment",
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn tasks() -> TaskPlan {
        serde_json::from_value(
            json!({"schema":"ondesk_task_plan.v1","project_key":"demo","tasks":[{
                "task_id":"T-1","goal":"selected task","completion_criteria":["verified"],
                "depends_on":[],"review_state":"unreviewed","evidence_refs":[]
            }]}),
        )
        .unwrap()
    }

    #[test]
    fn append_preserves_utf8_crlf_and_nested_examples_without_importing_other_items() {
        let source = "# TODO\r\n- [ ] 작업\r\n````markdown\r\n```forager-plan\r\nexample only\r\n```\r\n````\r\n- [ ] other task";
        let proposed = candidate(source, &tasks()).unwrap();
        assert!(proposed.starts_with(source));
        assert_eq!(inspect(&proposed, "demo").unwrap().plan.tasks.len(), 1);
        validate_append(source, &proposed, "demo").unwrap();
        assert!(validate_append(
            source,
            &proposed.replace("other task", "changed task"),
            "demo"
        )
        .is_err());
        assert!(candidate(&proposed, &tasks()).is_err());
    }

    #[test]
    fn refuses_ambiguous_sources_invalid_selection_and_premature_acceptance() {
        for source in [
            "```forager-plan\ninvalid\n```",
            "```forager-plan\n",
            "~~~markdown\nunfinished",
        ] {
            assert!(candidate(source, &tasks()).is_err());
        }
        let mut plan = tasks();
        plan.tasks[0].review_state = ReviewState::Accepted;
        plan.tasks[0].evidence_refs.push("claim".into());
        assert!(candidate("# TODO", &plan).is_err());
        plan = tasks();
        plan.tasks.push(plan.tasks[0].clone());
        assert!(candidate("# TODO", &plan).is_err());
        plan = tasks();
        plan.tasks[0].depends_on.push("missing".into());
        assert!(candidate("# TODO", &plan).is_err());
        plan = tasks();
        plan.tasks.clear();
        assert!(candidate("# TODO", &plan).is_err());
        assert!(candidate(&"x".repeat(MAX_PLAN_BYTES), &tasks()).is_err());
    }
}
