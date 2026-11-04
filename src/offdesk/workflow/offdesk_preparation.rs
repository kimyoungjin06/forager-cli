//! Shared, inert preparation from exact owning tasks before queue admission.

use std::collections::{BTreeMap, BTreeSet};

use anyhow::{ensure, Context, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};

use super::ondesk_plan::{self, PlanInspection, ReviewState};

pub struct PreparationSource<'a> {
    pub project_key: &'a str,
    pub path: &'a str,
    pub source: &'a str,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparationReviewBinding {
    pub schema: String,
    pub report_sha256: String,
    pub decision_rules_sha256: String,
    pub advisory_only: bool,
    pub runtime_policy_authorized: bool,
}

impl PreparationReviewBinding {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == "offdesk_preparation_review_binding.v1"
                && self.advisory_only
                && !self.runtime_policy_authorized,
            "unsupported preparation review binding or authority"
        );
        for value in [&self.report_sha256, &self.decision_rules_sha256] {
            ensure!(
                value.len() == 64
                    && value
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
                "invalid preparation review hash"
            );
        }
        Ok(())
    }
}

pub fn review_binding(
    manifest: &crate::offdesk::OffdeskShiftManifestV1,
    tasks: &[crate::offdesk::OffdeskTask],
) -> Result<Option<PreparationReviewBinding>> {
    let count = manifest
        .task_bindings
        .iter()
        .filter(|binding| binding.ondesk_source_snapshot.is_some())
        .count();
    if count == 0 {
        return Ok(None);
    }
    ensure!(
        count == manifest.task_bindings.len(),
        "preparation review needs owning sources for every selected task"
    );
    let report = from_shift(manifest, tasks);
    let report_sha256 = report["report_sha256"]
        .as_str()
        .context("preparation review draft unavailable")?
        .to_string();
    let mut hash = Sha256::new();
    hash.update(b"forager.offdesk-preparation-decision-rules.v1\0");
    hash.update(serde_json::to_vec(&decision_templates())?);
    let binding = PreparationReviewBinding {
        schema: "offdesk_preparation_review_binding.v1".into(),
        report_sha256,
        decision_rules_sha256: format!("{:x}", hash.finalize()),
        advisory_only: true,
        runtime_policy_authorized: false,
    };
    binding.validate()?;
    Ok(Some(binding))
}

pub fn validate_review_binding(
    manifest: &crate::offdesk::OffdeskShiftManifestV1,
    tasks: &[crate::offdesk::OffdeskTask],
) -> Result<()> {
    if let Some(expected) = &manifest.preparation_review {
        expected.validate()?;
        ensure!(
            review_binding(manifest, tasks)?.as_ref() == Some(expected),
            "preparation review version changed; prepare and review a new manifest"
        );
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct PreparationReport {
    pub schema: &'static str,
    pub status: &'static str,
    pub sources: Vec<SourceVersion>,
    pub tasks: Vec<PreparedTask>,
    pub dependent_followups: Vec<DependentFollowup>,
    pub decisions: Vec<DecisionTemplate>,
    pub unresolved_execution_choices: Vec<&'static str>,
    pub execution_order: &'static str,
    pub estimated_wall_time_seconds: Option<u64>,
    pub estimated_effort_seconds: Option<u64>,
    pub ready_for_launch: bool,
    pub effect: &'static str,
    pub does_not_authorize: Vec<&'static str>,
    pub report_sha256: String,
}

#[derive(Debug, Serialize)]
pub struct SourceVersion {
    pub project_key: String,
    pub path: String,
    pub source_sha256: String,
}

#[derive(Debug, Serialize)]
pub struct PreparedTask {
    pub project_key: String,
    pub task_id: String,
    pub goal: String,
    pub review_state: ReviewState,
    pub source_eligible: bool,
    pub holds: Vec<String>,
    pub dependencies: Vec<Dependency>,
    pub technical_step_candidates: Vec<String>,
    pub step_order: &'static str,
}

#[derive(Debug, Serialize)]
pub struct Dependency {
    pub task_id: String,
    pub review_state: ReviewState,
    pub accepted: bool,
}

#[derive(Debug, Serialize)]
pub struct DependentFollowup {
    pub project_key: String,
    pub task_id: String,
    pub selected: bool,
    pub unaccepted_selected_predecessors: Vec<String>,
    pub reason: &'static str,
}

#[derive(Debug, Serialize)]
pub struct DecisionTemplate {
    pub id: &'static str,
    pub condition: &'static str,
    pub proposed_response: &'static str,
    pub requires_exact_scope_and_capability_review: bool,
    pub runtime_enforced_by_this_report: bool,
}

/// These responses are drafts for scope/capability review, never dispatch rules.
pub fn decision_templates() -> Vec<DecisionTemplate> {
    [
        (
            "dependency_acceptance",
            "owning predecessor not accepted",
            "hold dependent task; a completion report does not accept its source predecessor",
        ),
        (
            "field_evidence_gap",
            "nonessential evidence missing",
            "record the gap; continue only declared independent steps inside the exact package",
        ),
        (
            "source_drift",
            "mandatory source identity changed or corrupt",
            "hold affected scope; shared source failure holds the package",
        ),
        (
            "incomparable_evidence",
            "wrong object, epoch, universe or denominator",
            "report supported observations and missing comparison inputs",
        ),
        (
            "opposing_result",
            "evidence opposes expected outcome",
            "retain the result and competing explanation without retuning or replacement search",
        ),
        (
            "resource_unavailable",
            "owned resources or remote inputs unavailable",
            "hold resource-dependent work; use only selected local inputs where permitted",
        ),
        (
            "uncertain_call",
            "timeout or transport failure with unknown execution outcome",
            "inspect owned ticket/outcome/release; do not blindly resubmit or switch provider",
        ),
        (
            "serial_failure",
            "task failed or predecessor not released",
            "retain the shift hold; separate work needs a fresh supported selection",
        ),
        (
            "usage_hold",
            "bound usage exhausted, stale, unknown or mismatched",
            "stop new guarded admission; do not renew baseline or infer zero usage",
        ),
        (
            "closeout_window",
            "next stage and closeout do not fit remaining window",
            "start no new stage; follow selected checkpoint and shutdown limits",
        ),
        (
            "early_finish",
            "selected meaningful work completed or no ready fallback",
            "write the return packet and finish; no filler or unselected fallback",
        ),
        (
            "return_drift",
            "owning plan changed or apply interrupted",
            "preserve original baseline/proposal; use exact reconciliation and supported replay",
        ),
    ]
    .into_iter()
    .map(|(id, condition, proposed_response)| DecisionTemplate {
        id,
        condition,
        proposed_response,
        requires_exact_scope_and_capability_review: true,
        runtime_enforced_by_this_report: false,
    })
    .collect()
}

pub fn prepare(
    sources: &[PreparationSource<'_>],
    selections: &[(String, String)],
) -> Result<PreparationReport> {
    ensure!(
        !sources.is_empty() && sources.len() <= 16,
        "select 1 to 16 owning sources"
    );
    ensure!(
        !selections.is_empty() && selections.len() <= 128,
        "select 1 to 128 exact tasks"
    );
    ensure!(
        sources.iter().map(|s| s.source.len()).sum::<usize>() <= 4 * 1024 * 1024,
        "preparation sources exceed byte budget"
    );
    let mut inspections: BTreeMap<&str, PlanInspection> = BTreeMap::new();
    let mut versions = Vec::new();
    for source in sources {
        ensure!(
            !source.path.trim().is_empty(),
            "owning source path required"
        );
        let inspected = ondesk_plan::inspect(source.source, source.project_key)?;
        versions.push(SourceVersion {
            project_key: source.project_key.to_string(),
            path: source.path.to_string(),
            source_sha256: inspected.source_sha256.clone(),
        });
        ensure!(
            inspections.insert(source.project_key, inspected).is_none(),
            "duplicate project source"
        );
    }
    let mut selected = BTreeSet::new();
    let mut tasks = Vec::new();
    for (project_key, task_id) in selections {
        ensure!(
            selected.insert((project_key.as_str(), task_id.as_str())),
            "duplicate selected task"
        );
        let plan = &inspections
            .get(project_key.as_str())
            .context("selected project has no owning source")?
            .plan;
        let task = plan
            .tasks
            .iter()
            .find(|t| t.task_id == *task_id)
            .context("selected task missing from owning source")?;
        let mut holds = Vec::new();
        if !matches!(
            task.review_state,
            ReviewState::Unreviewed | ReviewState::Rework | ReviewState::PartiallyAccepted
        ) {
            holds.push("task_completed_deferred_or_awaiting_review".to_string());
        }
        let dependencies = task
            .depends_on
            .iter()
            .map(|id| {
                let predecessor = plan
                    .tasks
                    .iter()
                    .find(|t| t.task_id == *id)
                    .context("owning predecessor missing")?;
                let accepted = predecessor.review_state == ReviewState::Accepted;
                if !accepted {
                    holds.push(format!("predecessor_not_accepted:{id}"));
                }
                Ok(Dependency {
                    task_id: id.clone(),
                    review_state: predecessor.review_state,
                    accepted,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        tasks.push(PreparedTask {
            project_key: project_key.clone(),
            task_id: task_id.clone(),
            goal: task.goal.clone(),
            review_state: task.review_state,
            source_eligible: holds.is_empty(),
            holds,
            dependencies,
            technical_step_candidates: task.completion_criteria.clone(),
            step_order: "completion_criteria_are_review_candidates_not_an_execution_plan",
        });
    }
    ensure!(
        inspections
            .keys()
            .all(|key| selected.iter().any(|(project, _)| project == key)),
        "owning source has no selected task"
    );
    let mut followups = Vec::new();
    for (project, inspected) in &inspections {
        for task in &inspected.plan.tasks {
            let predecessors =
                task.depends_on
                    .iter()
                    .filter(|id| {
                        selected.contains(&(*project, id.as_str()))
                            && inspected.plan.tasks.iter().any(|t| {
                                t.task_id == **id && t.review_state != ReviewState::Accepted
                            })
                    })
                    .cloned()
                    .collect::<Vec<_>>();
            if !predecessors.is_empty() {
                followups.push(DependentFollowup {
                    project_key: (*project).to_string(),
                    task_id: task.task_id.clone(),
                    selected: selected.contains(&(*project, task.task_id.as_str())),
                    unaccepted_selected_predecessors: predecessors,
                    reason: "selected_predecessor_completion_will_not_accept_owning_dependency",
                });
            }
        }
    }
    let mut report = PreparationReport {
        schema: "offdesk_preparation_draft.v1",
        status: if tasks.iter().any(|t| !t.source_eligible) {
            "owning_source_holds"
        } else {
            "execution_preparation_required"
        },
        sources: versions,
        tasks,
        dependent_followups: followups,
        decisions: decision_templates(),
        unresolved_execution_choices: vec![
            "reviewed_technical_steps_and_order",
            "exact_command_workdir_and_output_contract",
            "task_qualified_executor_provider_model_and_isolation",
            "actual_runtime_support_for_selected_branch_actions",
            "resources_occupancy_and_concurrency",
            "estimates_or_explicit_unknowns",
            "window_closeout_timeout_retry_checkpoint_policy",
            "applicable_budget_and_fresh_usage",
            "exact_ready_fallback_set_or_finish_policy",
            "return_and_ondesk_disposition_contract",
        ],
        execution_order: "not_inferred_from_task_selection_order",
        estimated_wall_time_seconds: None,
        estimated_effort_seconds: None,
        ready_for_launch: false,
        effect: "read_only_no_provider_call_queue_import_or_profile_write",
        does_not_authorize: vec![
            "approval",
            "enqueue",
            "arm",
            "launch",
            "source_acceptance",
            "plan_write",
            "provider_call",
            "scope_expansion",
        ],
        report_sha256: String::new(),
    };
    let mut hash = Sha256::new();
    hash.update(b"forager.offdesk-preparation-draft.v1\0");
    hash.update(serde_json::to_vec(&report)?);
    report.report_sha256 = format!("{:x}", hash.finalize());
    Ok(report)
}

pub fn from_shift(
    manifest: &crate::offdesk::OffdeskShiftManifestV1,
    tasks: &[crate::offdesk::OffdeskTask],
) -> serde_json::Value {
    use crate::offdesk::workflow::{offdesk_preparation, ondesk_task_binding};
    let build = || -> Result<serde_json::Value> {
        ensure!(
            !manifest.task_bindings.is_empty(),
            "no selected owning tasks"
        );
        let mut sources = std::collections::BTreeMap::new();
        let mut selections = Vec::new();
        for binding in &manifest.task_bindings {
            let snapshot = binding
                .ondesk_source_snapshot
                .as_ref()
                .context("owning source binding unavailable")?;
            let task = tasks
                .iter()
                .find(|t| t.task_id == binding.task_id)
                .context("selected queue task unavailable")?;
            let path = task
                .artifact_refs
                .iter()
                .find(|a| {
                    a.artifact_id
                        .starts_with(ondesk_task_binding::BINDING_PREFIX)
                })
                .and_then(|a| a.path.as_deref())
                .context("owning source path unavailable")?;
            snapshot.task(&binding.project_key, &binding.task_id)?;
            let source = (path, snapshot.source.as_str());
            if let Some(prior) = sources.insert(binding.project_key.as_str(), source) {
                ensure!(
                    prior == source,
                    "prepare different owning plans for one project separately"
                );
            }
            selections.push((binding.project_key.clone(), binding.task_id.clone()));
        }
        let sources = sources
            .into_iter()
            .map(
                |(project_key, (path, source))| offdesk_preparation::PreparationSource {
                    project_key,
                    path,
                    source,
                },
            )
            .collect::<Vec<_>>();
        Ok(serde_json::to_value(offdesk_preparation::prepare(
            &sources,
            &selections,
        )?)?)
    };
    build().unwrap_or_else(|error| {
        serde_json::json!({
            "schema":"offdesk_preparation_draft.v1", "status":"owning_source_binding_unavailable",
            "reason":error.to_string(), "ready_for_launch":false,
            "decisions":offdesk_preparation::decision_templates(),
            "effect":"diagnostic_only_no_new_execution_authority"
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn document(project: &str, parent_state: &str) -> String {
        format!(
            "# Existing plan\n```forager-plan\n{}\n```\n",
            json!({
                "schema":"ondesk_task_plan.v1", "project_key":project,"tasks":[
                    {"task_id":"restore", "goal":"restore evidence", "completion_criteria":["cite exact inputs", "record gaps"],"depends_on":[],"review_state":parent_state,"evidence_refs":["review:source-state"]},
                    {"task_id":"case", "goal":"prepare case", "completion_criteria":["case draft"],"depends_on":["restore"],"review_state":"unreviewed","evidence_refs":[]}
                ]
            })
        )
    }

    #[test]
    fn review_binding_covers_sources_selection_and_rules_without_granting_runtime_policy() {
        use crate::offdesk::{OffdeskShiftManifestV1, OffdeskTask};
        let source = document("nano", "unreviewed");
        let source_hash = ondesk_plan::inspect(&source, "nano").unwrap().source_sha256;
        let now = chrono::Utc::now();
        let task:OffdeskTask=serde_json::from_value(json!({"task_id":"restore","request_id":"request", "project_key":"nano",
            "capability_id":"dispatch.runtime","status":"queued","runner_kind":"local_background","command":"true",
            "workdir":"/fixture","created_at":now,"updated_at":now,"artifact_refs":[{
                "artifact_id":format!("ondesk-plan-sha256:{source_hash}"),"path":"/fixture/TODO.md","present":true}]})).unwrap();
        let mut manifest:OffdeskShiftManifestV1=serde_json::from_value(json!({
            "schema":"offdesk_shift_manifest.v1","shift_id":"fixture","profile":"fixture","created_at":now,
            "window":{"starts_at":now,"stops_at":now+chrono::Duration::hours(1),"timezone":"Asia/Seoul"},
            "plan_id":"plan","plan_review_id":"review","plan_source_sha256":"a".repeat(64),"plan_review_sha256":"b".repeat(64),
            "task_bindings":[{"task_id":"restore","request_id":"request","project_key":"nano","capability_id":"dispatch.runtime",
                "runner_kind":"local_background","project_root_identity_sha256":"c".repeat(64),"launch_spec_sha256":"d".repeat(64),
                "execution_brief_sha256":"e".repeat(64),"ondesk_source_snapshot":{"source_sha256":source_hash,"source":source}}],
            "routine_bindings":[],"budget":{"max_concurrent_tasks":1,"max_task_launches":1,"max_attempts_per_task":1,"max_routine_runs":0},
            "authority":{"task_dispatch_requires_fresh_approval":true,"task_launch_spec_is_immutable":true,"wiki_writes_are_candidate_only":true,
                "plan_revision_requires_review":true,"acceptance_requires_ondesk_review":true,"does_not_authorize":crate::offdesk::shift::OFFDESK_SHIFT_REQUIRED_DENIALS}
        })).unwrap();
        let tasks = std::slice::from_ref(&task);
        let binding = review_binding(&manifest, tasks).unwrap().unwrap();
        assert_eq!(
            binding.report_sha256,
            from_shift(&manifest, tasks)["report_sha256"]
        );
        assert_eq!(from_shift(&manifest, tasks)["ready_for_launch"], false);
        assert!(binding.advisory_only);
        assert!(!binding.runtime_policy_authorized);
        manifest.preparation_review = Some(binding.clone());
        validate_review_binding(&manifest, tasks).unwrap();
        manifest
            .preparation_review
            .as_mut()
            .unwrap()
            .decision_rules_sha256 = "f".repeat(64);
        assert!(validate_review_binding(&manifest, tasks).is_err());
        manifest.preparation_review = Some(binding.clone());
        manifest
            .preparation_review
            .as_mut()
            .unwrap()
            .runtime_policy_authorized = true;
        assert!(manifest.validate().is_err());
        manifest.preparation_review = Some(binding);
        manifest.task_bindings[0]
            .ondesk_source_snapshot
            .as_mut()
            .unwrap()
            .source
            .push_str("\nchanged\n");
        assert!(validate_review_binding(&manifest, tasks).is_err());
        manifest.preparation_review = None;
        manifest.task_bindings[0].ondesk_source_snapshot = None;
        assert_eq!(review_binding(&manifest, tasks).unwrap(), None);
        validate_review_binding(&manifest, tasks).unwrap();
        assert!(serde_json::to_value(&manifest)
            .unwrap()
            .get("preparation_review")
            .is_none());
    }

    #[test]
    fn completion_cannot_unlock_followups_and_draft_is_repeatable() {
        let source = document("nano", "unreviewed");
        let sources = [PreparationSource {
            project_key: "nano",
            path: "/nano/TODO.md",
            source: &source,
        }];
        let selected = [("nano".into(), "restore".into())];
        let a = prepare(&sources, &selected).unwrap();
        let b = prepare(&sources, &selected).unwrap();
        assert_eq!(a.report_sha256, b.report_sha256);
        assert!(a.tasks[0].source_eligible);
        assert_eq!(
            a.tasks[0].technical_step_candidates,
            ["cite exact inputs", "record gaps"]
        );
        assert_eq!(a.dependent_followups.len(), 1);
        assert_eq!(a.dependent_followups[0].task_id, "case");
        assert!(!a.dependent_followups[0].selected);
        assert!(!a.ready_for_launch);
        assert_eq!(a.decisions.len(), 12);
        assert!(a
            .decisions
            .iter()
            .all(|d| !d.runtime_enforced_by_this_report
                && d.requires_exact_scope_and_capability_review));
        assert_eq!(a.estimated_wall_time_seconds, None);
    }

    #[test]
    fn selecting_parent_and_child_does_not_flatten_acceptance() {
        for state in ["unreviewed", "partially_accepted"] {
            let source = document("nano", state);
            let report = prepare(
                &[PreparationSource {
                    project_key: "nano",
                    path: "/nano/TODO.md",
                    source: &source,
                }],
                &[
                    ("nano".into(), "restore".into()),
                    ("nano".into(), "case".into()),
                ],
            )
            .unwrap();
            assert!(report.tasks[0].source_eligible);
            assert!(!report.tasks[1].source_eligible);
            assert!(report.dependent_followups[0].selected);
            assert_eq!(report.status, "owning_source_holds");
        }
        let source = document("nano", "accepted");
        let report = prepare(
            &[PreparationSource {
                project_key: "nano",
                path: "/nano/TODO.md",
                source: &source,
            }],
            &[("nano".into(), "case".into())],
        )
        .unwrap();
        assert!(report.tasks[0].source_eligible);
        assert!(report.dependent_followups.is_empty());
    }

    #[test]
    fn same_task_ids_remain_scoped_to_their_owning_projects() {
        let a = document("nano", "unreviewed");
        let b = document("forager", "deferred");
        let sources = [
            PreparationSource {
                project_key: "nano",
                path: "/nano/TODO.md",
                source: &a,
            },
            PreparationSource {
                project_key: "forager",
                path: "/forager/TODO.md",
                source: &b,
            },
        ];
        let report = prepare(
            &sources,
            &[
                ("nano".into(), "restore".into()),
                ("forager".into(), "restore".into()),
            ],
        )
        .unwrap();
        assert!(report.tasks[0].source_eligible);
        assert!(!report.tasks[1].source_eligible);
        assert_eq!(report.sources.len(), 2);
        assert_eq!(report.dependent_followups.len(), 2);
        assert_eq!(report.dependent_followups[0].project_key, "forager");
    }

    #[test]
    fn ambiguous_empty_or_unscoped_selections_are_refused() {
        let source = document("nano", "unreviewed");
        let s = PreparationSource {
            project_key: "nano",
            path: "/nano/TODO.md",
            source: &source,
        };
        assert!(prepare(&[s], &[]).is_err());
        let s = || PreparationSource {
            project_key: "nano",
            path: "/nano/TODO.md",
            source: &source,
        };
        assert!(prepare(&[s()], &[("other".into(), "restore".into())]).is_err());
        assert!(prepare(&[s(), s()], &[("nano".into(), "restore".into())]).is_err());
        assert!(prepare(
            &[s()],
            &[
                ("nano".into(), "restore".into()),
                ("nano".into(), "restore".into())
            ]
        )
        .is_err());
    }
}
