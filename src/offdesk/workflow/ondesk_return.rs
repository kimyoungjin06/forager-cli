//! Read-only morning context from an exact report and preserved shift sources.
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    process::Command,
};

use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::{
    ondesk_plan::{self, PlanTask},
    ondesk_plan_store,
    ondesk_review::{read_shift_review, ShiftReviewReference},
};
use crate::{
    offdesk::{
        offdesk_shift_manifest_sha256, shift_runtime::ProjectRootBoundary, OffdeskShiftStore,
    },
    session::Storage,
};

#[derive(Debug, Serialize)]
pub struct MorningReturnContext {
    pub schema: &'static str,
    pub project_key: String,
    pub workdir: PathBuf,
    pub owning_plan: PathBuf,
    pub shift_review: ShiftReviewReference,
    pub shift_record_sha256: String,
    pub lifecycle_status: String,
    pub window_starts_at: chrono::DateTime<chrono::Utc>,
    pub window_stops_at: chrono::DateTime<chrono::Utc>,
    pub current_plan_sha256: String,
    pub current_git: GitObservation,
    pub historical_git_status: &'static str,
    pub resume_mode: &'static str,
    pub session_id: Option<String>,
    pub session_input_status: &'static str,
    pub tasks: Vec<MorningTaskContext>,
    pub review_history: Vec<RecordedReview>,
    pub pending_plan_requests: Vec<String>,
    pub plan_journal_sha256: Option<String>,
    pub council_questions: Vec<serde_json::Value>,
    pub council_scope: &'static str,
    pub pending_decisions: Vec<&'static str>,
    pub limitations: Vec<&'static str>,
    pub does_not_authorize: Vec<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub canonical_return_binding: Option<super::ondesk_return_binding::VerifiedReturnBinding>,
}

#[derive(Debug, Serialize)]
pub struct MorningTaskContext {
    pub task_id: String,
    pub historical_plan_sha256: String,
    pub baseline_status: &'static str,
    pub original_task: PlanTask,
    pub current_task: Option<PlanTask>,
    pub added_tasks: Vec<String>,
    pub changed_tasks: Vec<String>,
    pub removed_tasks: Vec<String>,
    pub closeout_report: serde_json::Value,
}

#[derive(Debug, PartialEq, Eq, Serialize)]
pub struct GitObservation {
    pub head: Option<String>,
    pub branch: Option<String>,
    pub status: Option<String>,
    pub status_sha256: Option<String>,
    pub status_truncated: bool,
    pub dirty: Option<bool>,
}

#[derive(Deserialize)]
struct Report {
    tasks: Vec<ReportTask>,
    council: serde_json::Value,
}

#[derive(Deserialize)]
struct ReportTask {
    project_key: String,
    task_id: String,
    closeout_report: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct RecordedReview {
    pub request_id: String,
    pub committed: bool,
    pub candidate_is_current: bool,
    #[serde(serialize_with = "serialize_review_decisions")]
    pub decisions: super::ondesk_review::ReviewDecisions,
}

fn serialize_review_decisions<S: serde::Serializer>(
    decisions: &super::ondesk_review::ReviewDecisions,
    serializer: S,
) -> std::result::Result<S::Ok, S::Error> {
    let mut value = serde_json::to_value(decisions).map_err(serde::ser::Error::custom)?;
    if let Some(reconciliation) = value
        .get_mut("reconciliation")
        .and_then(serde_json::Value::as_object_mut)
    {
        // Return surfaces redact/trim strings. Do not present their transformed
        // Markdown as the exact hash-bound witness stored in the plan journal.
        reconciliation.remove("execution_baseline_markdown");
        reconciliation.insert(
            "exact_record_source".into(),
            serde_json::json!("owning_plan_journal"),
        );
    }
    value.serialize(serializer)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct PlanJournal {
    pub(super) schema: String,
    pub(super) source_path: PathBuf,
    pub(super) entries: Vec<JournalEntry>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct JournalEntry {
    pub(super) request: ondesk_plan_store::PlanChangeRequest,
    pub(super) committed: bool,
}

fn optional_journal(root: &std::fs::File, path: &Path) -> Result<Option<Vec<u8>>> {
    match ondesk_plan_store::read(root, path, ondesk_plan::MAX_PLAN_BYTES) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) =>
        {
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

pub struct MorningReturnInput<'a> {
    pub profile: &'a str,
    pub project_key: &'a str,
    pub workdir: &'a Path,
    pub owning_plan: &'a Path,
    pub reference: ShiftReviewReference,
    pub resume_session: Option<&'a str>,
    pub expected_branch: Option<&'a str>,
}

/// A bounded return surface for an existing Markdown checklist. This does not
/// import checklist items as Forager tasks or grant plan-write authority.
pub fn build_unstructured_review(input: MorningReturnInput<'_>) -> Result<serde_json::Value> {
    ensure!(
        input.resume_session.is_none(),
        "unstructured morning review cannot bind a resume session"
    );
    ensure!(
        input.workdir.is_absolute() && input.owning_plan.is_absolute(),
        "morning workdir and owning plan must be absolute"
    );
    let boundary = ProjectRootBoundary::open(input.workdir)?;
    let relative = input
        .owning_plan
        .strip_prefix(input.workdir)
        .context("morning owning plan is outside workdir")?;
    let root = crate::process::open_bounded_root(input.workdir)?;
    let plan_bytes = ondesk_plan_store::read(&root, relative, ondesk_plan::MAX_PLAN_BYTES)?;
    std::str::from_utf8(&plan_bytes).context("morning owning plan is not UTF-8")?;
    let plan_sha256 = format!("{:x}", Sha256::digest(&plan_bytes));
    let reference = &input.reference;
    let report_bytes = read_shift_review(reference)?;
    let report: serde_json::Value = serde_json::from_slice(&report_bytes)?;
    let report_tasks = report["tasks"]
        .as_array()
        .context("morning shift review tasks missing")?;
    let store = OffdeskShiftStore::read_only(input.profile)?;
    let record = store
        .record(&reference.shift_id)?
        .context("morning shift record missing")?;
    ensure!(
        offdesk_shift_manifest_sha256(&record.manifest)? == reference.manifest_sha256,
        "morning report does not match saved shift manifest"
    );
    let selected: Vec<_> = record
        .manifest
        .task_bindings
        .iter()
        .filter(|binding| binding.project_key == input.project_key)
        .collect();
    ensure!(
        !selected.is_empty(),
        "morning project is not bound to shift"
    );
    ensure!(
        report_tasks
            .iter()
            .filter(|task| task["project_key"].as_str() == Some(input.project_key))
            .count()
            == selected.len(),
        "morning report task set differs from manifest"
    );
    let mut tasks = Vec::new();
    for binding in selected {
        let task = report_tasks
            .iter()
            .find(|task| {
                task["project_key"].as_str() == Some(input.project_key)
                    && task["task_id"].as_str() == Some(binding.task_id.as_str())
            })
            .context("bound task missing from morning report")?;
        let closeout = &task["closeout_report"];
        ensure!(
            closeout["schema"] == "offdesk_task_closeout_report.v1"
                && closeout["review_disposition"] == "pending_user_review"
                && closeout["plan_update_authorized"] == false,
            "morning task report does not preserve review-pending authority"
        );
        tasks.push(serde_json::json!({
            "task_id": binding.task_id,
            "current_status": task["current_status"],
            "closeout_report": closeout,
            "canonical_plan_task_mapping": "unverified",
            "agent_read_scope_enforcement": "not_verified_by_return_package"
        }));
    }
    let git = observe_git(input.workdir)?;
    if let Some(expected) = input.expected_branch {
        ensure!(
            git.branch.as_deref() == Some(expected),
            "morning branch mismatch or unavailable"
        );
    }
    ensure!(
        ondesk_plan_store::read(&root, relative, ondesk_plan::MAX_PLAN_BYTES)? == plan_bytes,
        "morning plan changed during observation"
    );
    ensure!(
        read_shift_review(reference)? == report_bytes,
        "morning shift review changed during observation"
    );
    ensure!(
        store.record(&reference.shift_id)?.as_ref() == Some(&record),
        "morning shift changed during observation"
    );
    ondesk_plan_store::active_root(input.workdir, &root)?;
    boundary.ensure_current()?;
    Ok(serde_json::json!({
        "schema": "ondesk_unstructured_morning_review.v1",
        "project_key": input.project_key,
        "workdir": input.workdir,
        "owning_plan": input.owning_plan,
        "current_plan_sha256": plan_sha256,
        "plan_format": "unstructured_markdown_not_imported",
        "shift_review": reference,
        "shift_record_sha256": format!("{:x}", Sha256::digest(serde_json::to_vec(&record)?)),
        "lifecycle_status": record.state.as_ref().map(|state| serde_json::to_value(state.status)).transpose()?.unwrap_or(serde_json::json!("prepared")),
        "current_git": git,
        "tasks": tasks,
        "closeout": report["closeout"],
        "attention_reasons": report["attention_reasons"],
        "required_first_reads": [input.owning_plan, reference.path.as_path()],
        "historical_plan_binding": "not_preserved_or_not_imported",
        "task_workdir_to_project_root_binding": "not_verified",
        "authority": "review_only",
        "does_not_authorize": ["plan_write", "task_mapping", "accepted_truth", "session_input", "wiki_promotion", "scientific_acceptance"],
        "limitations": ["Compare the canonical TODO with the packet manually before disposition.", "A completed runtime and result sidecar do not verify the task goal.", "Agent read scope is not verified by this return package."]
    }))
}

pub fn build(input: MorningReturnInput<'_>) -> Result<MorningReturnContext> {
    build_with_binding(input, None)
}

pub fn build_with_binding(
    input: MorningReturnInput<'_>,
    mapping: Option<&super::ondesk_return_binding::ArtifactPin>,
) -> Result<MorningReturnContext> {
    let verified = mapping
        .map(|reference| super::ondesk_return_binding::verify(&input, reference))
        .transpose()?;
    let MorningReturnInput {
        profile,
        project_key,
        workdir,
        owning_plan,
        reference,
        resume_session,
        expected_branch,
    } = input;
    ensure!(
        workdir.is_absolute() && owning_plan.is_absolute(),
        "morning workdir and owning plan must be absolute"
    );
    let boundary = ProjectRootBoundary::open(workdir)?;
    let relative = owning_plan
        .strip_prefix(workdir)
        .context("morning owning plan is outside workdir")?;
    let root = crate::process::open_bounded_root(workdir)?;
    let bytes = ondesk_plan_store::read(&root, relative, ondesk_plan::MAX_PLAN_BYTES)?;
    let current = ondesk_plan::inspect(std::str::from_utf8(&bytes)?, project_key)?;
    let stem = relative
        .file_name()
        .and_then(|s| s.to_str())
        .context("morning plan filename must be UTF-8")?;
    let journal_path = relative.with_file_name(format!(".{stem}.forager-plan-journal.json"));
    let journal_bytes = optional_journal(&root, &journal_path)?;
    let mut review_history = Vec::new();
    let mut pending_plan_requests = Vec::new();
    if let Some(bytes) = &journal_bytes {
        let journal: PlanJournal =
            serde_json::from_slice(bytes).context("invalid morning plan journal")?;
        ensure!(
            journal.schema == "ondesk_plan_change_journal.v1"
                && journal.source_path == owning_plan
                && journal.entries.len() <= 512,
            "morning plan journal identity or bounds mismatch"
        );
        let mut ids = std::collections::HashSet::new();
        for entry in journal.entries {
            ondesk_plan_store::validate_request(&entry.request)?;
            ensure!(
                entry.request.source_path == owning_plan
                    && ids.insert(entry.request.request_id.clone()),
                "invalid morning journal request identity"
            );
            if let Some(decisions) = &entry.request.review_record {
                super::ondesk_review::validate_decision_shape(decisions)?;
                if entry.request.candidate_sha256 == current.source_sha256 {
                    ensure!(
                        entry.request.project_key == project_key,
                        "current morning journal project mismatch"
                    );
                    validate_current_review(&current.plan.tasks, decisions)?;
                }
            }
            if !entry.committed {
                pending_plan_requests.push(entry.request.request_id.clone());
            }
            if entry.request.project_key == project_key {
                if let Some(decisions) = entry.request.review_record {
                    review_history.push(RecordedReview {
                        request_id: entry.request.request_id,
                        committed: entry.committed,
                        candidate_is_current: entry.request.candidate_sha256
                            == current.source_sha256,
                        decisions,
                    });
                }
            }
        }
    }
    let git = observe_git(workdir)?;
    if let Some(expected) = expected_branch {
        ensure!(
            git.branch.as_deref() == Some(expected),
            "morning branch mismatch or unavailable"
        );
    }
    let report: Report = serde_json::from_slice(&read_shift_review(&reference)?)?;
    let store = OffdeskShiftStore::read_only(profile)?;
    let record = store
        .record(&reference.shift_id)?
        .context("morning shift record missing")?;
    ensure!(
        offdesk_shift_manifest_sha256(&record.manifest)? == reference.manifest_sha256,
        "morning report does not match saved shift manifest"
    );
    let selected: Vec<_> = record
        .manifest
        .task_bindings
        .iter()
        .filter(|b| b.project_key == project_key)
        .collect();
    ensure!(
        !selected.is_empty(),
        "morning project is not bound to shift"
    );
    ensure!(
        report
            .tasks
            .iter()
            .filter(|t| t.project_key == project_key)
            .count()
            == selected.len(),
        "morning report task set differs from manifest"
    );
    let mut tasks = Vec::new();
    for binding in selected {
        if let Some(verified) = &verified {
            ensure!(
                verified
                    .binding
                    .tasks
                    .iter()
                    .any(|task| task.task_id == binding.task_id
                        && task.request_id == binding.request_id
                        && task.execution_root.identity_sha256
                            == binding.project_root_identity_sha256),
                "morning mapped task is not bound to the preserved execution root"
            );
        } else {
            ensure!(
                binding.project_root_identity_sha256 == boundary.identity_sha256,
                "morning project root identity changed"
            );
        }
        let snapshot = binding
            .ondesk_source_snapshot
            .as_ref()
            .context("morning historical source not preserved")?;
        let original = snapshot.task(project_key, &binding.task_id)?;
        let historical = ondesk_plan::inspect(&snapshot.source, project_key)?;
        let task_report = report
            .tasks
            .iter()
            .find(|t| t.project_key == project_key && t.task_id == binding.task_id)
            .context("morning bound task missing from report")?;
        let closeout = &task_report.closeout_report;
        ensure!(
            closeout["schema"] == "offdesk_task_closeout_report.v1"
                && closeout["historical_scope_status"] == "preserved_source_hash_verified"
                && closeout["historical_source_sha256"].as_str()
                    == Some(snapshot.source_sha256.as_str()),
            "morning historical report binding mismatch"
        );
        ensure!(
            closeout["review_disposition"] == "pending_user_review"
                && closeout["plan_update_authorized"].as_bool() == Some(false),
            "morning report must remain review pending"
        );
        let comparison = compare_plans(&historical.plan.tasks, &current.plan.tasks);
        let baseline_status = if snapshot.source_sha256 == current.source_sha256 {
            "exact_baseline"
        } else if comparison.0.is_empty() && comparison.1.is_empty() && comparison.2.is_empty() {
            "document_changed_tasks_equal"
        } else {
            "tasks_changed"
        };
        tasks.push(MorningTaskContext {
            task_id: binding.task_id.clone(),
            historical_plan_sha256: snapshot.source_sha256.clone(),
            baseline_status,
            original_task: original,
            current_task: current
                .plan
                .tasks
                .iter()
                .find(|t| t.task_id == binding.task_id)
                .cloned(),
            added_tasks: comparison.0,
            changed_tasks: comparison.1,
            removed_tasks: comparison.2,
            closeout_report: closeout.clone(),
        });
    }
    let mut session_snapshot = None;
    if let Some(id) = resume_session {
        let session = Storage::new_read_only(profile)?
            .load()?
            .into_iter()
            .find(|s| s.id == id)
            .context("morning resume session not found")?;
        let session_root = ProjectRootBoundary::open(Path::new(&session.project_path))?;
        ensure!(
            session_root.identity_sha256 == boundary.identity_sha256,
            "morning resume session belongs to another workdir"
        );
        session_snapshot = Some(serde_json::to_value(session)?);
    }
    let council_questions = report
        .council
        .get("recent")
        .and_then(serde_json::Value::as_array)
        .context("morning Council recent decisions missing")?
        .iter()
        .filter(|decision| {
            decision["project_key"].as_str() == Some(project_key)
                && tasks
                    .iter()
                    .any(|task| decision["task_id"].as_str() == Some(task.task_id.as_str()))
        })
        .cloned()
        .collect();
    // These are observations, not a reservation against later edits or launches.
    ensure!(
        ondesk_plan_store::read(&root, relative, ondesk_plan::MAX_PLAN_BYTES)? == bytes,
        "morning plan changed during observation"
    );
    ensure!(
        optional_journal(&root, &journal_path)? == journal_bytes,
        "morning journal changed during observation"
    );
    read_shift_review(&reference)?;
    ensure!(
        store.record(&reference.shift_id)?.as_ref() == Some(&record),
        "morning shift changed during observation"
    );
    ensure!(
        observe_git(workdir)? == git,
        "morning git context changed during observation"
    );
    ondesk_plan_store::active_root(workdir, &root)?;
    boundary.ensure_current()?;
    if let Some(id) = resume_session {
        let session = Storage::new_read_only(profile)?
            .load()?
            .into_iter()
            .find(|s| s.id == id)
            .context("morning resume session disappeared during observation")?;
        ensure!(
            Some(serde_json::to_value(session)?) == session_snapshot,
            "morning resume session changed during observation"
        );
    }
    let current_review_recorded = review_history.iter().any(|entry| {
        entry.committed
            && entry.candidate_is_current
            && entry.decisions.shift_review.as_ref() == Some(&reference)
            && tasks.iter().all(|task| {
                entry
                    .decisions
                    .tasks
                    .iter()
                    .any(|decision| decision.task_id == task.task_id)
            })
    });
    if let Some(reference_pin) = mapping {
        let final_input = MorningReturnInput {
            profile,
            project_key,
            workdir,
            owning_plan,
            reference: reference.clone(),
            resume_session,
            expected_branch,
        };
        let after = super::ondesk_return_binding::verify(&final_input, reference_pin)?;
        ensure!(
            Some(&after.binding) == verified.as_ref().map(|v| &v.binding),
            "canonical return binding changed during observation"
        );
    }
    let mut pending_decisions = Vec::new();
    if !pending_plan_requests.is_empty() {
        pending_decisions.push("resolve_pending_plan_journal_before_new_review");
    } else if !current_review_recorded {
        pending_decisions.extend([
            "review_evidence_and_questions",
            "choose_accept_partial_rework_or_defer",
        ]);
        if tasks
            .iter()
            .any(|task| task.baseline_status != "exact_baseline")
        {
            pending_decisions.push("reconcile_changed_baseline_before_proposal");
        }
    }
    Ok(MorningReturnContext {
        schema: if verified.is_some() {
            "ondesk_morning_return.v2"
        } else {
            "ondesk_morning_return.v1"
        },
        project_key: project_key.into(),
        workdir: workdir.into(),
        owning_plan: owning_plan.into(),
        shift_review: reference,
        shift_record_sha256: format!("{:x}", Sha256::digest(serde_json::to_vec(&record)?)),
        lifecycle_status: record
            .state
            .as_ref()
            .map(|s| serde_json::to_value(s.status))
            .transpose()?
            .and_then(|value| value.as_str().map(String::from))
            .unwrap_or_else(|| "manifest_only".into()),
        window_starts_at: record.manifest.window.starts_at,
        window_stops_at: record.manifest.window.stops_at,
        current_plan_sha256: current.source_sha256,
        current_git: git,
        historical_git_status: "not_preserved",
        resume_mode: if resume_session.is_some() {
            "existing_session"
        } else {
            "fresh_session"
        },
        session_id: resume_session.map(String::from),
        session_input_status: "not_assessed_no_delivery",
        tasks,
        review_history,
        pending_plan_requests,
        plan_journal_sha256: journal_bytes
            .as_ref()
            .map(|bytes| format!("{:x}", Sha256::digest(bytes))),
        council_questions,
        council_scope: "selected_project_recent_saved_decisions",
        pending_decisions,
        limitations: vec![
            "saved_report_is_not_authenticated_or_latest_runtime",
            "historical_branch_and_head_not_preserved",
            "session_identity_does_not_prove_safe_input_or_acknowledgement",
            "no_automatic_baseline_reconciliation",
        ],
        does_not_authorize: vec![
            "plan_write",
            "launch",
            "session_input",
            "user_acceptance",
            "wiki_promotion",
        ],
        canonical_return_binding: verified,
    })
}

fn compare_plans(
    before: &[PlanTask],
    after: &[PlanTask],
) -> (Vec<String>, Vec<String>, Vec<String>) {
    let old: BTreeMap<_, _> = before.iter().map(|t| (&t.task_id, t)).collect();
    let new: BTreeMap<_, _> = after.iter().map(|t| (&t.task_id, t)).collect();
    let added = new
        .keys()
        .filter(|id| !old.contains_key(*id))
        .map(|id| (*id).clone())
        .collect();
    let changed = new
        .iter()
        .filter(|(id, task)| old.get(*id).is_some_and(|original| original != *task))
        .map(|(id, _)| (*id).clone())
        .collect();
    let removed = old
        .keys()
        .filter(|id| !new.contains_key(*id))
        .map(|id| (*id).clone())
        .collect();
    (added, changed, removed)
}

fn validate_current_review(
    tasks: &[PlanTask],
    decisions: &super::ondesk_review::ReviewDecisions,
) -> Result<()> {
    for decision in &decisions.tasks {
        let task = tasks
            .iter()
            .find(|task| task.task_id == decision.task_id)
            .context("current morning journal task missing from plan")?;
        ensure!(
            task.review_state == decision.disposition
                && decision
                    .evidence_refs
                    .iter()
                    .all(|evidence| task.evidence_refs.contains(evidence)),
            "current morning journal decisions do not match plan"
        );
    }
    Ok(())
}

fn git_text(path: &Path, args: &[&str]) -> Result<Option<String>> {
    let output = Command::new("git")
        .arg("--no-optional-locks")
        .args(["-c", "core.fsmonitor=false", "-C"])
        .arg(path)
        .args(args)
        .output()
        .context("observe morning git context")?;
    Ok(output.status.success().then(|| {
        String::from_utf8_lossy(&output.stdout)
            .trim_end()
            .to_owned()
    }))
}

fn observe_git(path: &Path) -> Result<GitObservation> {
    let status = git_text(
        path,
        &["status", "--porcelain=v1", "--untracked-files=normal"],
    )?;
    let dirty = status.as_ref().map(|s| !s.is_empty());
    let status_sha256 = status
        .as_ref()
        .map(|s| format!("{:x}", Sha256::digest(s.as_bytes())));
    let status_truncated = status.as_ref().is_some_and(|s| s.chars().count() > 12_000);
    Ok(GitObservation {
        head: git_text(path, &["rev-parse", "--verify", "HEAD"])?,
        branch: git_text(path, &["symbolic-ref", "--quiet", "--short", "HEAD"])?,
        status: status.map(|s| s.chars().take(12_000).collect()),
        status_sha256,
        status_truncated,
        dirty,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn return_review_summarizes_reconciliation_without_exposing_exact_witness_bytes() {
        let source = "private-execution-witness\n";
        let decisions: super::super::ondesk_review::ReviewDecisions = serde_json::from_value(serde_json::json!({
            "request_id":"r1", "project_key":"demo", "baseline_sha256":"b".repeat(64),
            "decision_ref":"operator:fixture", "decided_by":"fixture operator", "tasks":[],
            "reconciliation":{
                "schema":"ondesk_review_reconciliation.v1", "execution_baseline_sha256":"a".repeat(64),
                "current_baseline_sha256":"b".repeat(64), "execution_baseline_markdown":source,
                "reason":"Only narrative changed"
            }
        })).unwrap();
        let record = RecordedReview {
            request_id: "r1".into(),
            committed: true,
            candidate_is_current: true,
            decisions,
        };
        let value = serde_json::to_value(&record).unwrap();
        assert!(!value.to_string().contains("private-execution-witness"));
        assert_eq!(
            value["decisions"]["reconciliation"]["execution_baseline_sha256"],
            "a".repeat(64)
        );
        assert_eq!(
            value["decisions"]["reconciliation"]["reason"],
            "Only narrative changed"
        );
        assert_eq!(
            value["decisions"]["reconciliation"]["exact_record_source"],
            "owning_plan_journal"
        );
        assert_eq!(
            record
                .decisions
                .reconciliation
                .unwrap()
                .execution_baseline_markdown,
            source
        );
    }

    fn task(id: &str, goal: &str) -> PlanTask {
        PlanTask {
            task_id: id.into(),
            goal: goal.into(),
            completion_criteria: vec!["verified".into()],
            depends_on: vec![],
            review_state: ondesk_plan::ReviewState::Unreviewed,
            evidence_refs: vec![],
        }
    }
    #[test]
    fn compares_identity_and_all_task_fields_without_treating_order_as_work_change() {
        let before = vec![
            task("T-1", "original"),
            task("T-2", "keep"),
            task("T-3", "remove"),
        ];
        let after = vec![
            task("T-4", "added"),
            task("T-2", "keep"),
            task("T-1", "updated"),
        ];
        assert_eq!(
            compare_plans(&before, &after),
            (vec!["T-4".into()], vec!["T-1".into()], vec!["T-3".into()])
        );
        let reversed: Vec<_> = before.iter().rev().cloned().collect();
        assert_eq!(compare_plans(&before, &reversed), (vec![], vec![], vec![]));
        for field in ["criteria", "dependency", "review", "evidence"] {
            let mut changed = before.clone();
            match field {
                "criteria" => changed[0].completion_criteria.push("new check".into()),
                "dependency" => changed[0].depends_on.push("T-2".into()),
                "review" => changed[0].review_state = ondesk_plan::ReviewState::Accepted,
                _ => changed[0].evidence_refs.push("receipt".into()),
            }
            assert_eq!(compare_plans(&before, &changed).1, vec!["T-1"]);
        }
    }
}
