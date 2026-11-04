//! Inert, version-bound operator review proposals consumed by plan-apply.
use super::{
    ondesk_plan::{self, ReviewState},
    ondesk_plan_store::PlanChangeRequest,
};
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReviewDecisions {
    pub request_id: String,
    pub project_key: String,
    pub baseline_sha256: String,
    pub decision_ref: String,
    pub decided_by: String,
    pub tasks: Vec<TaskDecision>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub shift_review: Option<ShiftReviewReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reconciliation: Option<NarrativeReconciliation>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct NarrativeReconciliation {
    pub schema: String,
    pub execution_baseline_sha256: String,
    pub current_baseline_sha256: String,
    pub execution_baseline_markdown: String,
    pub reason: String,
}

impl NarrativeReconciliation {
    pub fn new(execution_source: String, current_sha256: String, reason: String) -> Self {
        Self {
            schema: "ondesk_review_reconciliation.v1".into(),
            execution_baseline_sha256: format!("{:x}", Sha256::digest(execution_source.as_bytes())),
            current_baseline_sha256: current_sha256,
            execution_baseline_markdown: execution_source,
            reason,
        }
    }
}

fn validate_reconciliation(decisions: &ReviewDecisions) -> Result<()> {
    if let Some(value) = &decisions.reconciliation {
        ensure!(
            decisions.shift_review.is_some(),
            "reconciliation requires a bound shift review"
        );
        ensure!(
            value.schema == "ondesk_review_reconciliation.v1",
            "unsupported reconciliation schema"
        );
        ensure!(
            !value.reason.trim().is_empty() && value.reason.len() <= 4096,
            "reconciliation reason required within byte budget"
        );
        ensure!(
            value.execution_baseline_markdown.len() <= ondesk_plan::MAX_PLAN_BYTES,
            "execution baseline exceeds byte budget"
        );
        ensure!(
            value.current_baseline_sha256 == decisions.baseline_sha256,
            "reconciliation current baseline mismatch"
        );
        ensure!(
            value.execution_baseline_sha256 != value.current_baseline_sha256,
            "reconciliation requires baseline drift"
        );
        ensure!(
            format!(
                "{:x}",
                Sha256::digest(value.execution_baseline_markdown.as_bytes())
            ) == value.execution_baseline_sha256,
            "execution baseline hash mismatch"
        );
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ShiftReviewReference {
    pub path: PathBuf,
    pub sha256: String,
    pub shift_id: String,
    pub manifest_sha256: String,
}

// Consume only the identity and review-boundary fields of the larger projection.
#[derive(Deserialize)]
struct ShiftReviewArtifact {
    schema: String,
    shift_id: String,
    manifest_sha256: String,
    tasks: Vec<ShiftReviewTask>,
}

#[derive(Deserialize)]
struct ShiftReviewTask {
    task_id: String,
    project_key: String,
    closeout_report: ShiftReviewCloseout,
}

#[derive(Deserialize)]
struct ShiftReviewCloseout {
    schema: String,
    historical_scope_status: String,
    historical_source_sha256: Option<String>,
    review_disposition: String,
    plan_update_authorized: bool,
    #[serde(default)]
    result_observation: Option<serde_json::Value>,
}

pub(super) fn validate_shift_review(decisions: &ReviewDecisions) -> Result<()> {
    validate_reconciliation(decisions)?;
    let Some(reference) = &decisions.shift_review else {
        return Ok(());
    };
    let bytes = read_shift_review(reference)?;
    let report: ShiftReviewArtifact = serde_json::from_slice(&bytes)?;
    let execution_hash = decisions
        .reconciliation
        .as_ref()
        .map(|value| value.execution_baseline_sha256.as_str())
        .unwrap_or(&decisions.baseline_sha256);
    for decision in &decisions.tasks {
        let task = report
            .tasks
            .iter()
            .find(|task| {
                task.project_key == decisions.project_key && task.task_id == decision.task_id
            })
            .context("reviewed task missing from shift review project")?;
        let closeout = &task.closeout_report;
        ensure!(
            closeout.schema == "offdesk_task_closeout_report.v1",
            "unsupported task closeout schema"
        );
        ensure!(
            closeout.historical_scope_status == "preserved_source_hash_verified"
                && closeout.historical_source_sha256.as_deref() == Some(execution_hash),
            "shift review source baseline mismatch or unavailable"
        );
        ensure!(
            closeout.review_disposition == "pending_user_review"
                && !closeout.plan_update_authorized,
            "shift report must not grant plan-write authority"
        );
        if decision.disposition == ReviewState::Accepted {
            ensure!(
                closeout
                    .result_observation
                    .as_ref()
                    .and_then(|v| v.get("coexisting_failure"))
                    .is_none_or(|v| v.is_null()),
                "coexisting failure evidence cannot be accepted as completed work"
            );
        }
    }
    Ok(())
}

/// Reuse the exact-artifact boundary for decisions and morning context.
pub(super) fn read_shift_review(reference: &ShiftReviewReference) -> Result<Vec<u8>> {
    ensure!(
        reference.path.is_absolute(),
        "shift review path must be absolute"
    );
    ensure!(
        ondesk_plan::valid_id(&reference.shift_id),
        "invalid shift review ID"
    );
    for hash in [&reference.sha256, &reference.manifest_sha256] {
        ensure!(
            hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "invalid shift review hash"
        );
    }
    let parent = reference
        .path
        .parent()
        .context("shift review parent missing")?;
    let name = Path::new(
        reference
            .path
            .file_name()
            .context("shift review filename missing")?,
    );
    let root = crate::process::open_bounded_root(parent)?;
    let bytes = super::ondesk_plan_store::read(&root, name, ondesk_plan::MAX_PLAN_BYTES)?;
    super::ondesk_plan_store::active_root(parent, &root)?;
    ensure!(
        format!("{:x}", Sha256::digest(&bytes)) == reference.sha256,
        "shift review artifact hash changed"
    );
    let report: ShiftReviewArtifact =
        serde_json::from_slice(&bytes).context("invalid shift review artifact")?;
    ensure!(
        report.schema == "offdesk_shift_review_projection.v1",
        "unsupported shift review schema"
    );
    ensure!(
        report.shift_id == reference.shift_id
            && report.manifest_sha256 == reference.manifest_sha256,
        "shift review execution identity mismatch"
    );
    ensure!(
        !report.tasks.is_empty() && report.tasks.len() <= 1024,
        "shift review task count outside bounds"
    );
    let mut identities = HashSet::new();
    for task in &report.tasks {
        ensure!(
            identities.insert((&task.project_key, &task.task_id)),
            "duplicate shift review task identity"
        );
    }
    Ok(bytes)
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TaskDecision {
    pub task_id: String,
    pub disposition: ReviewState,
    pub evidence_refs: Vec<String>,
    pub remaining_work: Vec<String>,
    pub unanswered_questions: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ReviewProposal {
    pub schema: &'static str,
    pub decisions: ReviewDecisions,
    pub change_request: PlanChangeRequest,
    pub candidate_markdown: String,
    pub approval_status: &'static str,
}

pub(super) fn validate_decision_shape(decisions: &ReviewDecisions) -> Result<()> {
    validate_reconciliation(decisions)?;
    ensure!(
        ondesk_plan::valid_id(&decisions.request_id),
        "invalid review request ID"
    );
    ensure!(
        !decisions.decided_by.trim().is_empty() && !decisions.decision_ref.trim().is_empty(),
        "decision attribution required"
    );
    ensure!(
        !decisions.tasks.is_empty() && decisions.tasks.len() <= 1024,
        "review task count outside bounds"
    );
    ensure!(
        serde_json::to_vec(decisions)?.len() <= ondesk_plan::MAX_PLAN_BYTES,
        "review exceeds byte budget"
    );
    let mut seen = HashSet::new();
    for decision in &decisions.tasks {
        ensure!(seen.insert(&decision.task_id), "duplicate task decision");
        ensure!(
            matches!(
                decision.disposition,
                ReviewState::Accepted
                    | ReviewState::PartiallyAccepted
                    | ReviewState::Rework
                    | ReviewState::Deferred
            ),
            "invalid review disposition"
        );
        ensure!(
            decision
                .evidence_refs
                .iter()
                .chain(&decision.remaining_work)
                .chain(&decision.unanswered_questions)
                .all(|text| !text.trim().is_empty()),
            "empty review detail"
        );
        if decision.disposition == ReviewState::Accepted {
            ensure!(
                decision.remaining_work.is_empty() && decision.unanswered_questions.is_empty(),
                "acceptance cannot hide remaining work or unanswered questions"
            );
        }
        if matches!(
            decision.disposition,
            ReviewState::PartiallyAccepted | ReviewState::Rework
        ) {
            ensure!(
                !decision.remaining_work.is_empty(),
                "partial acceptance or rework requires remaining work"
            );
        }
        if matches!(
            decision.disposition,
            ReviewState::Accepted | ReviewState::PartiallyAccepted
        ) {
            ensure!(
                !decision.evidence_refs.is_empty(),
                "acceptance requires decision-specific evidence"
            );
        }
    }
    Ok(())
}

pub fn propose(source: &str, path: &Path, decisions: ReviewDecisions) -> Result<ReviewProposal> {
    ensure!(path.is_absolute(), "owning plan path must be absolute");
    validate_decision_shape(&decisions)?;
    let inspected = ondesk_plan::inspect(source, &decisions.project_key)?;
    ensure!(
        inspected.source_sha256 == decisions.baseline_sha256,
        "review baseline changed"
    );
    validate_shift_review(&decisions)?;
    if let Some(value) = &decisions.reconciliation {
        let historical =
            ondesk_plan::inspect(&value.execution_baseline_markdown, &decisions.project_key)?;
        ensure!(
            historical.plan == inspected.plan,
            "reconciliation is limited to unchanged structured task plans"
        );
    }
    let mut plan = inspected.plan;
    for decision in &decisions.tasks {
        let task = plan
            .tasks
            .iter_mut()
            .find(|task| task.task_id == decision.task_id)
            .context("review task missing from owning plan")?;
        task.review_state = decision.disposition;
        for reference in &decision.evidence_refs {
            if !task.evidence_refs.contains(reference) {
                task.evidence_refs.push(reference.clone());
            }
        }
    }
    let candidate_markdown = ondesk_plan::with_plan(source, &plan)?;
    let preview = ondesk_plan::preview(
        source,
        &candidate_markdown,
        &decisions.project_key,
        &decisions.baseline_sha256,
    )?;
    let change_request = PlanChangeRequest {
        schema: "ondesk_plan_change_request.v1".into(),
        request_id: decisions.request_id.clone(),
        project_key: decisions.project_key.clone(),
        source_path: path.to_owned(),
        baseline_sha256: decisions.baseline_sha256.clone(),
        candidate_sha256: preview.candidate_sha256,
        decision_ref: decisions.decision_ref.clone(),
        review_record: Some(decisions.clone()),
    };
    super::ondesk_plan_store::validate_request(&change_request)?;
    Ok(ReviewProposal {
        schema: "ondesk_review_proposal.v1",
        decisions,
        change_request,
        candidate_markdown,
        approval_status: "attribution_only_requires_explicit_plan_write_confirmation",
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn bound_fixture() -> (
        tempfile::TempDir,
        String,
        ReviewDecisions,
        serde_json::Value,
    ) {
        let dir = tempfile::tempdir_in(fs::canonicalize(std::env::temp_dir()).unwrap()).unwrap();
        let (source, mut decisions) = fixture(ReviewState::Accepted);
        let report = json!({
            "schema":"offdesk_shift_review_projection.v1", "shift_id":"night-1",
            "manifest_sha256":"a".repeat(64), "tasks":[{
                "project_key":"demo", "task_id":"T-1", "closeout_report":{
                    "schema":"offdesk_task_closeout_report.v1",
                    "historical_scope_status":"preserved_source_hash_verified",
                    "historical_source_sha256":decisions.baseline_sha256,
                    "review_disposition":"pending_user_review", "plan_update_authorized":false
                }
            }]
        });
        let bytes = serde_json::to_vec(&report).unwrap();
        let path = dir.path().join("night-review.json");
        fs::write(&path, &bytes).unwrap();
        decisions.shift_review = Some(ShiftReviewReference {
            path,
            sha256: format!("{:x}", Sha256::digest(&bytes)),
            shift_id: "night-1".into(),
            manifest_sha256: "a".repeat(64),
        });
        (dir, source, decisions, report)
    }

    #[test]
    fn explicit_narrative_reconciliation_preserves_current_prose_and_binds_both_versions() {
        let (_dir, source, mut decisions, _) = bound_fixture();
        let current = format!("# Updated operator note\n{source}\nPreserve this new note.\n");
        decisions.baseline_sha256 = ondesk_plan::inspect(&current, "demo")
            .unwrap()
            .source_sha256;
        assert!(propose(&current, Path::new("/plan.md"), decisions.clone()).is_err());
        decisions.reconciliation = Some(NarrativeReconciliation::new(
            source,
            decisions.baseline_sha256.clone(),
            "Only narrative changed; task plan reviewed unchanged".into(),
        ));
        let proposal = propose(&current, Path::new("/plan.md"), decisions.clone()).unwrap();
        assert!(proposal
            .candidate_markdown
            .starts_with("# Updated operator note\n"));
        assert!(proposal
            .candidate_markdown
            .ends_with("Preserve this new note.\n"));
        assert_eq!(proposal.change_request.review_record, Some(decisions));
        assert_eq!(
            ondesk_plan::inspect(&proposal.candidate_markdown, "demo")
                .unwrap()
                .plan
                .tasks[0]
                .review_state,
            ReviewState::Accepted
        );
    }

    #[test]
    fn reconciliation_refuses_semantic_drift_and_changed_witnesses() {
        let (_dir, source, mut decisions, _) = bound_fixture();
        let current = format!("{source}\nNew narrative\n");
        decisions.baseline_sha256 = ondesk_plan::inspect(&current, "demo")
            .unwrap()
            .source_sha256;
        decisions.reconciliation = Some(NarrativeReconciliation::new(
            source.clone(),
            decisions.baseline_sha256.clone(),
            "Reviewed narrative change".into(),
        ));
        let mut value = serde_json::to_value(&decisions).unwrap();
        for pointer in [
            "/reconciliation/schema",
            "/reconciliation/execution_baseline_sha256",
            "/reconciliation/current_baseline_sha256",
            "/reconciliation/execution_baseline_markdown",
            "/reconciliation/reason",
        ] {
            let before = value.pointer(pointer).unwrap().clone();
            *value.pointer_mut(pointer).unwrap() = json!("");
            let changed = serde_json::from_value(value.clone()).unwrap();
            assert!(
                propose(&current, Path::new("/plan.md"), changed).is_err(),
                "{pointer}"
            );
            *value.pointer_mut(pointer).unwrap() = before;
        }
        let mut no_report = decisions.clone();
        no_report.shift_review = None;
        assert!(propose(&current, Path::new("/plan.md"), no_report).is_err());
        for pointer in [
            "/tasks/0/goal",
            "/tasks/0/completion_criteria/0",
            "/tasks/0/review_state",
            "/tasks/0/evidence_refs",
            "/tasks",
        ] {
            let mut plan =
                serde_json::to_value(ondesk_plan::inspect(&source, "demo").unwrap().plan).unwrap();
            *plan.pointer_mut(pointer).unwrap() = match pointer {
                "/tasks/0/review_state" => json!("deferred"),
                "/tasks/0/evidence_refs" => json!(["new-evidence"]),
                "/tasks" => json!([]),
                _ => json!("changed task semantics"),
            };
            let changed =
                ondesk_plan::with_plan(&current, &serde_json::from_value(plan).unwrap()).unwrap();
            let mut bound = decisions.clone();
            bound.baseline_sha256 = ondesk_plan::inspect(&changed, "demo")
                .unwrap()
                .source_sha256;
            bound
                .reconciliation
                .as_mut()
                .unwrap()
                .current_baseline_sha256 = bound.baseline_sha256.clone();
            assert!(
                propose(&changed, Path::new("/plan.md"), bound)
                    .unwrap_err()
                    .to_string()
                    .contains("unchanged structured task plans"),
                "{pointer}"
            );
        }
    }

    #[test]
    fn shift_review_binds_execution_project_task_and_preserved_source() {
        let (_dir, source, decisions, report) = bound_fixture();
        let proposal = propose(&source, Path::new("/plan.md"), decisions.clone()).unwrap();
        assert_eq!(
            proposal.change_request.review_record,
            Some(decisions.clone())
        );
        for pointer in [
            "/schema",
            "/shift_id",
            "/manifest_sha256",
            "/tasks/0/project_key",
            "/tasks/0/task_id",
            "/tasks/0/closeout_report/schema",
            "/tasks/0/closeout_report/historical_scope_status",
            "/tasks/0/closeout_report/historical_source_sha256",
            "/tasks/0/closeout_report/review_disposition",
        ] {
            let mut changed = report.clone();
            *changed.pointer_mut(pointer).unwrap() = json!("wrong");
            let bytes = serde_json::to_vec(&changed).unwrap();
            let mut bound = decisions.clone();
            let reference = bound.shift_review.as_mut().unwrap();
            fs::write(&reference.path, &bytes).unwrap();
            reference.sha256 = format!("{:x}", Sha256::digest(&bytes));
            assert!(
                propose(&source, Path::new("/plan.md"), bound).is_err(),
                "{pointer}"
            );
        }
        for changed in [
            {
                let mut value = report.clone();
                value["tasks"][0]["closeout_report"]["historical_source_sha256"] =
                    serde_json::Value::Null;
                value
            },
            {
                let mut value = report.clone();
                value["tasks"][0]["closeout_report"]["plan_update_authorized"] = json!(true);
                value
            },
            {
                let mut value = report.clone();
                let task = value["tasks"][0].clone();
                value["tasks"].as_array_mut().unwrap().push(task);
                value
            },
            {
                let mut value = report.clone();
                value["tasks"] = json!([]);
                value
            },
        ] {
            let bytes = serde_json::to_vec(&changed).unwrap();
            let mut bound = decisions.clone();
            let reference = bound.shift_review.as_mut().unwrap();
            fs::write(&reference.path, &bytes).unwrap();
            reference.sha256 = format!("{:x}", Sha256::digest(&bytes));
            assert!(propose(&source, Path::new("/plan.md"), bound).is_err());
        }
    }

    #[test]
    fn coexisting_failure_blocks_acceptance_but_allows_explicit_rework() {
        let (_dir, source, mut decisions, mut report) = bound_fixture();
        report["tasks"][0]["closeout_report"]["result_observation"] = json!({
            "reported_status":"pending_user_review","reported_exit_code":0,
            "coexisting_failure":{"reference":"FAILURE.json","verification":"current_file_parsed_not_historical_receipt"}});
        let bytes = serde_json::to_vec(&report).unwrap();
        let reference = decisions.shift_review.as_mut().unwrap();
        fs::write(&reference.path, &bytes).unwrap();
        reference.sha256 = format!("{:x}", Sha256::digest(&bytes));
        assert!(propose(&source, Path::new("/plan.md"), decisions.clone())
            .unwrap_err()
            .to_string()
            .contains("coexisting failure"));
        decisions.tasks[0].disposition = ReviewState::Rework;
        decisions.tasks[0].remaining_work =
            vec!["Inspect preserved post-publication failure.".into()];
        assert!(propose(&source, Path::new("/plan.md"), decisions).is_ok());
    }

    #[test]
    fn changed_missing_oversized_and_unsafe_shift_review_artifacts_are_refused() {
        let (_dir, source, decisions, _) = bound_fixture();
        let reference = decisions.shift_review.as_ref().unwrap();
        fs::write(&reference.path, b"{}").unwrap();
        assert!(propose(&source, Path::new("/plan.md"), decisions.clone()).is_err());
        fs::write(&reference.path, vec![b' '; ondesk_plan::MAX_PLAN_BYTES + 1]).unwrap();
        assert!(propose(&source, Path::new("/plan.md"), decisions.clone()).is_err());
        fs::remove_file(&reference.path).unwrap();
        assert!(propose(&source, Path::new("/plan.md"), decisions.clone()).is_err());
        let mut relative = decisions.clone();
        relative.shift_review.as_mut().unwrap().path = PathBuf::from("relative.json");
        assert!(propose(&source, Path::new("/plan.md"), relative).is_err());
        #[cfg(unix)]
        {
            let (_dir, source, decisions, _) = bound_fixture();
            let path = &decisions.shift_review.as_ref().unwrap().path;
            let other = path.with_extension("original");
            fs::rename(path, &other).unwrap();
            std::os::unix::fs::symlink(&other, path).unwrap();
            assert!(propose(&source, Path::new("/plan.md"), decisions.clone()).is_err());
            fs::remove_file(path).unwrap();
            fs::hard_link(&other, path).unwrap();
            assert!(propose(&source, Path::new("/plan.md"), decisions).is_err());
        }
    }
    fn fixture(state: ReviewState) -> (String, ReviewDecisions) {
        let source = format!(
            "```forager-plan\n{}\n```\n",
            serde_json::json!({"schema":"ondesk_task_plan.v1","project_key":"demo","tasks":[{"task_id":"T-1","goal":"fix","completion_criteria":["verified"],"depends_on":[],"review_state":"pending_review","evidence_refs":[]}]})
        );
        let decisions = ReviewDecisions {
            request_id: "review-1".into(),
            project_key: "demo".into(),
            baseline_sha256: ondesk_plan::inspect(&source, "demo").unwrap().source_sha256,
            decision_ref: "review:1".into(),
            decided_by: "operator".into(),
            tasks: vec![TaskDecision {
                task_id: "T-1".into(),
                disposition: state,
                evidence_refs: vec!["receipt:1".into()],
                remaining_work: vec![],
                unanswered_questions: vec![],
            }],
            shift_review: None,
            reconciliation: None,
        };
        (source, decisions)
    }
    #[test]
    fn dispositions_preserve_unresolved_work_and_require_evidence() {
        for state in [
            ReviewState::Accepted,
            ReviewState::PartiallyAccepted,
            ReviewState::Rework,
            ReviewState::Deferred,
        ] {
            let (source, mut decisions) = fixture(state);
            if matches!(state, ReviewState::PartiallyAccepted | ReviewState::Rework) {
                assert!(propose(&source, Path::new("/plan.md"), fixture(state).1).is_err());
                decisions.tasks[0]
                    .remaining_work
                    .push("finish missing coverage".into());
            }
            let proposal = propose(&source, Path::new("/plan.md"), decisions).unwrap();
            assert_eq!(
                ondesk_plan::inspect(&proposal.candidate_markdown, "demo")
                    .unwrap()
                    .plan
                    .tasks[0]
                    .review_state,
                state
            );
            assert_eq!(
                proposal.approval_status,
                "attribution_only_requires_explicit_plan_write_confirmation"
            );
        }
        let (source, mut decisions) = fixture(ReviewState::Accepted);
        decisions.tasks[0]
            .unanswered_questions
            .push("is the result correct?".into());
        assert!(propose(&source, Path::new("/plan.md"), decisions).is_err());
        let (_, mut decisions) = fixture(ReviewState::Accepted);
        decisions.tasks[0].evidence_refs.clear();
        assert!(propose(&source, Path::new("/plan.md"), decisions).is_err());
    }
    #[test]
    fn wrong_baseline_identity_and_nonreview_state_fail_closed() {
        let (source, mut decisions) = fixture(ReviewState::Accepted);
        decisions.baseline_sha256 = "0".repeat(64);
        assert!(propose(&source, Path::new("/plan.md"), decisions).is_err());
        let (_, mut decisions) = fixture(ReviewState::Accepted);
        decisions.tasks[0].task_id = "unknown".into();
        assert!(propose(&source, Path::new("/plan.md"), decisions).is_err());
        assert!(propose(
            &source,
            Path::new("/plan.md"),
            fixture(ReviewState::PendingReview).1
        )
        .is_err());
        assert!(propose(
            &source,
            Path::new("relative.md"),
            fixture(ReviewState::Accepted).1
        )
        .is_err());
    }
}
