//! Read-only execution choices from the exact existing queue launch contract.

use serde::Serialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::offdesk::{offdesk_task_launch_spec_sha256, OffdeskShiftManifestV1, OffdeskTask};

#[derive(Debug, Serialize)]
pub struct TaskExecutionChoice {
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub launch_spec_sha256: String,
    pub task_identity_matches: bool,
    pub command: Option<String>,
    pub workdir: Option<String>,
    pub provider_id: Option<String>,
    pub model: Option<String>,
    pub result_artifact_path: Option<String>,
    pub log_artifact_path: Option<String>,
    pub output_declared: bool,
    pub provider_model_declared: bool,
    pub executor_kind: &'static str,
    pub isolation_selected: bool,
    pub missing_declarations: Vec<&'static str>,
    pub worker_qualification: &'static str,
    pub output_content_validation: &'static str,
    pub technical_step_execution: &'static str,
    pub worker_timeout_retry_checkpoint: &'static str,
    pub return_disposition: &'static str,
    pub worker_execution_contract: Option<Value>,
    pub worker_execution_contract_hold: Option<String>,
}

fn declared(value: Option<&str>) -> bool {
    value.is_some_and(|value| !value.trim().is_empty())
}

pub fn choices(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
) -> Vec<TaskExecutionChoice> {
    manifest
        .task_bindings
        .iter()
        .map(|binding| {
            let mut matching = tasks.iter().filter(|t| t.task_id == binding.task_id);
            let task = matching.next();
            let unique = matching.next().is_none();
            let task_identity_matches = unique
                && task.is_some_and(|t| {
                    offdesk_task_launch_spec_sha256(t)
                        .is_ok_and(|hash| hash == binding.launch_spec_sha256)
                        && t.project_key == binding.project_key
                        && t.request_id == binding.request_id
                });
            let output_declared = task.is_some_and(|t| declared(t.result_artifact_path.as_deref()));
            let provider_model_declared = task.is_some_and(|t| {
                declared(t.provider_id.as_deref()) && declared(t.model.as_deref())
            });
            let isolation_selected =
                manifest.host_policy_attestation.is_some() || manifest.read_policy.is_some();
            let mut missing_declarations = Vec::new();
            if !task_identity_matches {
                missing_declarations.push("exact_queue_launch_identity");
            }
            if !output_declared {
                missing_declarations.push("result_artifact_path");
            }
            if !provider_model_declared {
                missing_declarations.push("provider_and_model_if_required");
            }
            if !isolation_selected {
                missing_declarations.push("host_or_read_isolation_if_required");
            }
            TaskExecutionChoice {
                task_id: binding.task_id.clone(),
                request_id: binding.request_id.clone(),
                project_key: binding.project_key.clone(),
                launch_spec_sha256: binding.launch_spec_sha256.clone(),
                task_identity_matches,
                command: task.map(|t| t.command.clone()),
                workdir: task.map(|t| t.workdir.clone()),
                provider_id: task.and_then(|t| t.provider_id.clone()),
                model: task.and_then(|t| t.model.clone()),
                result_artifact_path: task.and_then(|t| t.result_artifact_path.clone()),
                log_artifact_path: task.and_then(|t| t.log_artifact_path.clone()),
                output_declared,
                provider_model_declared,
                executor_kind: "unselected",
                isolation_selected,
                missing_declarations,
                worker_qualification: "not_bound_by_common_queue_contract",
                output_content_validation: "path_declaration_only_no_content_or_schema_proof",
                technical_step_execution:
                    "owning_completion_criteria_are_candidates_not_executable_steps",
                worker_timeout_retry_checkpoint: "not_bound_by_common_queue_contract",
                return_disposition: "result_observation_does_not_accept_owning_task",
                worker_execution_contract: None,
                worker_execution_contract_hold: None,
            }
        })
        .collect()
}

pub fn report(manifest: &OffdeskShiftManifestV1, tasks: &[OffdeskTask]) -> anyhow::Result<Value> {
    let native = manifest.task_bindings.iter().filter_map(|binding| {
        let task = tasks.iter().find(|task| task.task_id == binding.task_id)?;
        match super::worker_invocation::inspect(task, Some(&manifest.window), chrono::Utc::now()) {
            Ok(Some(view)) => Some(view),
            Err(error) => Some(json!({"task_id":binding.task_id,"status":"held","reason":crate::offdesk::operator_safe_text(&error.to_string())})),
            _ => None,
        }
    }).collect::<Vec<_>>();
    report_with_workers(manifest, tasks, &native)
}

/// Reuse this operation's validated views, never cached approval or task metadata.
pub(crate) fn report_with_workers(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
    native: &[Value],
) -> anyhow::Result<Value> {
    let mut choices = choices(manifest, tasks);
    for task in &mut choices {
        if !task.task_identity_matches {
            continue;
        }
        let matching: Vec<_> = native
            .iter()
            .filter(|row| row["task_id"] == task.task_id)
            .collect();
        if let [view] = matching.as_slice() {
            let contract = &view["worker_execution_contract"];
            if super::worker_invocation::bound(view)
                && contract["task_id"] == task.task_id
                && contract["request_id"] == task.request_id
                && contract["project_key"] == task.project_key
                && contract.is_object()
            {
                task.worker_execution_contract = Some(contract.clone());
                if view["adapter"] == "deterministic_metadata" {
                    task.executor_kind = "deterministic_local_no_model";
                    task.missing_declarations
                        .retain(|v| *v != "provider_and_model_if_required");
                }
                task.technical_step_execution = "supported_controller_fixed_stage_sequence";
                task.worker_timeout_retry_checkpoint =
                    if view["adapter"] == "deterministic_metadata" {
                        "bound_deadline_committed_intra_attempt_batches_no_dispatcher_restart"
                    } else {
                        "bound_worker_deadline_limits_checkpoint_unsupported"
                    };
            } else if view["status"] == "held" {
                task.worker_execution_contract_hold = view["reason"].as_str().map(str::to_owned);
            }
        }
    }
    let all_bound = !choices.is_empty()
        && choices
            .iter()
            .all(|task| task.worker_execution_contract.is_some());
    let contracts: Vec<_> = choices
        .iter()
        .map(|task| &task.worker_execution_contract)
        .collect();
    let mut contract_hash = Sha256::new();
    contract_hash.update(b"forager.worker-execution-contracts.v1\0");
    contract_hash.update(serde_json::to_vec(&contracts)?);
    let mut report = json!({"schema":"offdesk_task_execution_choice_review.v1",
        "status":if choices.is_empty() {"no_selected_tasks"} else if all_bound {"selected_operational_contracts_bound"} else {"worker_contract_review_required"},
        "all_task_identities_match":choices.iter().all(|t| t.task_identity_matches),
        "all_worker_execution_contracts_bound":all_bound,
        "worker_execution_contracts_sha256":format!("{:x}",contract_hash.finalize()),
        "tasks":choices,"selected_isolation":{"host_policy":manifest.host_policy_attestation,"read_policy":manifest.read_policy},
        "task_attempt_limit":manifest.budget.max_attempts_per_task,
        "window":manifest.window,"window_policy":"stop_new_dispatch_only_not_worker_shutdown",
        "weekly_usage_selected":manifest.weekly_usage.is_some(),
        "task_cost_or_token_ceiling":null,"estimated_wall_time_seconds":null,
        "worker_qualification_complete":false,"runtime_policy_authorized":false,
        "effect":"read_only_existing_queue_declarations_no_worker_or_provider_call",
        "review_sha256":""});
    let mut hash = Sha256::new();
    hash.update(b"forager.offdesk-task-execution-choice-review.v1\0");
    hash.update(serde_json::to_vec(&report)?);
    report["review_sha256"] = json!(format!("{:x}", hash.finalize()));
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::workflow::preparation_capabilities::{self, PreparationRequirementsV1};

    fn fixture() -> (OffdeskShiftManifestV1, OffdeskTask) {
        let now = chrono::Utc::now();
        let task: OffdeskTask=serde_json::from_value(json!({"task_id":"task","request_id":"request","project_key":"project",
            "status":"queued","capability_id":"dispatch.runtime","runner_kind":"local_background","command":"true","workdir":"/fixture",
            "created_at":now,"updated_at":now,"provider_id":"fixture-provider","model":"fixture-model","result_artifact_path":"/fixture/result.json"})).unwrap();
        let hash = offdesk_task_launch_spec_sha256(&task).unwrap();
        let manifest=serde_json::from_value(json!({"schema":"offdesk_shift_manifest.v1","shift_id":"fixture","profile":"fixture","created_at":now,
            "window":{"starts_at":now,"stops_at":now+chrono::Duration::hours(1),"timezone":"Asia/Seoul"},"plan_id":"plan","plan_review_id":"review",
            "plan_source_sha256":"a".repeat(64),"plan_review_sha256":"b".repeat(64),
            "task_bindings":[{"task_id":"task","request_id":"request","project_key":"project","capability_id":"dispatch.runtime","runner_kind":"local_background",
                "project_root_identity_sha256":"c".repeat(64),"launch_spec_sha256":hash,"execution_brief_sha256":"d".repeat(64)}],
            "routine_bindings":[],"budget":{"max_concurrent_tasks":1,"max_task_launches":1,"max_attempts_per_task":1,"max_routine_runs":0},
            "authority":{"task_dispatch_requires_fresh_approval":true,"task_launch_spec_is_immutable":true,"wiki_writes_are_candidate_only":true,
                "plan_revision_requires_review":true,"acceptance_requires_ondesk_review":true,"does_not_authorize":crate::offdesk::OFFDESK_SHIFT_REQUIRED_DENIALS}})).unwrap();
        (manifest, task)
    }

    fn require(manifest: &mut OffdeskShiftManifestV1, id: &str) {
        manifest.preparation_requirements =
            Some(PreparationRequirementsV1::new(vec![id.into()]).unwrap());
    }

    #[test]
    fn declarations_are_repeatable_without_worker_qualification_or_output_proof() {
        let (mut manifest, task) = fixture();
        let tasks = std::slice::from_ref(&task);
        let first = report(&manifest, tasks).unwrap();
        assert_eq!(first, report(&manifest, tasks).unwrap());
        assert_eq!(first["all_task_identities_match"], true);
        assert_eq!(first["tasks"][0]["output_declared"], true);
        assert_eq!(first["tasks"][0]["provider_model_declared"], true);
        assert_eq!(first["worker_qualification_complete"], false);
        assert_eq!(first["runtime_policy_authorized"], false);
        assert_eq!(
            first["tasks"][0]["output_content_validation"],
            "path_declaration_only_no_content_or_schema_proof"
        );
        for id in [
            "require_declared_task_output",
            "require_declared_provider_model",
        ] {
            require(&mut manifest, id);
            preparation_capabilities::validate_launch(&manifest, tasks).unwrap();
        }
        require(&mut manifest, "require_selected_task_isolation");
        assert!(preparation_capabilities::validate_launch(&manifest, tasks).is_err());
        manifest.read_policy = Some(crate::offdesk::read_policy::ReadPolicy::canonical());
        preparation_capabilities::validate_launch(&manifest, tasks).unwrap();
        require(&mut manifest, "require_qualified_worker_contract");
        assert!(preparation_capabilities::validate_launch(&manifest, tasks).is_err());
        require(&mut manifest, "require_bound_worker_execution_contract");
        assert!(preparation_capabilities::validate_launch(&manifest, tasks).is_err());
        assert_eq!(
            report(&manifest, tasks).unwrap()["all_worker_execution_contracts_bound"],
            false
        );
    }

    #[test]
    fn missing_duplicate_wrong_or_changed_queue_identity_never_satisfies_a_declaration() {
        let (mut manifest, task) = fixture();
        require(&mut manifest, "require_declared_task_output");
        for tasks in [vec![], vec![task.clone(), task.clone()]] {
            assert_eq!(
                report(&manifest, &tasks).unwrap()["all_task_identities_match"],
                false
            );
            assert!(preparation_capabilities::validate_launch(&manifest, &tasks).is_err());
        }
        let before = report(&manifest, std::slice::from_ref(&task)).unwrap();
        let mut changed = task.clone();
        changed.result_artifact_path = Some("/fixture/different.json".into());
        let after = report(&manifest, std::slice::from_ref(&changed)).unwrap();
        assert_ne!(before["review_sha256"], after["review_sha256"]);
        assert_eq!(after["all_task_identities_match"], false);
        assert!(preparation_capabilities::validate_launch(
            &manifest,
            std::slice::from_ref(&changed)
        )
        .is_err());
        let mut wrong = task;
        wrong.project_key = "other".into();
        assert_eq!(
            report(&manifest, &[wrong]).unwrap()["all_task_identities_match"],
            false
        );
    }

    #[test]
    fn mutable_task_lifecycle_does_not_rewrite_execution_choice_identity() {
        let (manifest, mut task) = fixture();
        let before = report(&manifest, std::slice::from_ref(&task)).unwrap();
        task.status = crate::offdesk::OffdeskTaskStatus::Running;
        task.attempt_count = 1;
        task.updated_at += chrono::Duration::seconds(1);
        task.background_ticket_id = Some("owned-ticket".into());
        assert_eq!(
            before,
            report(&manifest, std::slice::from_ref(&task)).unwrap()
        );
    }

    #[test]
    fn reused_worker_view_cannot_cross_request_project_or_duplicate_identity() {
        let (manifest, task) = fixture();
        let row = json!({"task_id":"task","status":"verified_invocation_adapter_rechecks_source_at_entry",
            "worker_execution_contract":{"schema":"offdesk_native_execution_contract.v1", "task_id":"task", "request_id":"wrong", "project_key":"project"}});
        let wrong = report_with_workers(
            &manifest,
            std::slice::from_ref(&task),
            std::slice::from_ref(&row),
        )
        .unwrap();
        assert_eq!(wrong["all_worker_execution_contracts_bound"], false);
        let mut project = row.clone();
        project["worker_execution_contract"]["request_id"] = json!("request");
        project["worker_execution_contract"]["project_key"] = json!("wrong-project");
        assert_eq!(
            report_with_workers(&manifest, std::slice::from_ref(&task), &[project]).unwrap()
                ["all_worker_execution_contracts_bound"],
            false
        );
        assert_eq!(
            report_with_workers(&manifest, &[task], &[row.clone(), row]).unwrap()
                ["all_worker_execution_contracts_bound"],
            false
        );
    }

    #[test]
    fn empty_result_provider_or_model_declarations_stay_missing_after_exact_rebinding() {
        let (mut manifest, mut task) = fixture();
        task.result_artifact_path = Some(" \n".into());
        task.provider_id = None;
        task.model = Some(" ".into());
        manifest.task_bindings[0].launch_spec_sha256 =
            offdesk_task_launch_spec_sha256(&task).unwrap();
        let tasks = std::slice::from_ref(&task);
        assert_eq!(
            report(&manifest, tasks).unwrap()["all_task_identities_match"],
            true
        );
        for id in [
            "require_declared_task_output",
            "require_declared_provider_model",
        ] {
            require(&mut manifest, id);
            let checked = preparation_capabilities::assess(&manifest, tasks).unwrap();
            assert_eq!(checked["controls"][0]["status"], "missing_selection");
        }
    }
}
