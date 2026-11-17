//! Required manifest controls use existing host gates, never inferred worker behavior.

use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;

use crate::offdesk::{BackgroundRunnerKind, OffdeskShiftManifestV1, OffdeskTask};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PreparationRequirementsV1 {
    pub schema: String,
    pub required_controls: Vec<String>,
    pub capability_rules_sha256: String,
}

impl PreparationRequirementsV1 {
    pub fn new(required_controls: Vec<String>) -> Result<Self> {
        let requirements = Self {
            schema: "offdesk_preparation_requirements.v1".into(),
            required_controls,
            capability_rules_sha256: rules_sha256()?,
        };
        requirements.validate()?;
        Ok(requirements)
    }

    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == "offdesk_preparation_requirements.v1",
            "unsupported preparation requirements schema"
        );
        ensure!(
            !self.required_controls.is_empty() && self.required_controls.len() <= 32,
            "select 1 to 32 preparation controls"
        );
        let mut ids = BTreeSet::new();
        for id in &self.required_controls {
            ensure!(
                !id.is_empty()
                    && id.len() <= 80
                    && id
                        .bytes()
                        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_'),
                "invalid preparation control ID"
            );
            ensure!(ids.insert(id), "duplicate required preparation control");
        }
        ensure!(
            self.capability_rules_sha256.len() == 64
                && self
                    .capability_rules_sha256
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "invalid preparation capability rules hash"
        );
        Ok(())
    }
}

#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
enum Prerequisite {
    ShiftWindow,
    OwningTaskSources,
    LocalBackgroundTasks,
    SingleTaskAttempt,
    LocalSerialSelection,
    WeeklyTaskUsageBinding,
    NativeInvocation,
    NativeEntryPreflight,
    BoundWorkerExecutionContract,
    TaskDeclaredOutput,
    TaskDeclaredProviderModel,
    TaskSelectedIsolation,
    Unsupported,
}

#[derive(Serialize)]
struct Control {
    id: &'static str,
    prerequisite: Prerequisite,
    boundary: &'static str,
}

fn catalog() -> Vec<Control> {
    use Prerequisite::*;
    [
        (
            "stop_new_dispatch_at_window_end",
            ShiftWindow,
            "new task admission only; running processes are not stopped",
        ),
        (
            "hold_unaccepted_dependencies",
            OwningTaskSources,
            "declared dependencies in the exact owning task sources",
        ),
        (
            "hold_source_drift",
            OwningTaskSources,
            "bound owning task sources; arbitrary child inputs are not covered",
        ),
        (
            "hold_active_or_uncertain_workdir",
            LocalBackgroundTasks,
            "task queue and owned runtime evidence in this profile only",
        ),
        (
            "hold_uncertain_launch",
            LocalBackgroundTasks,
            "durable task launch reservation and known ticket; child API calls are not covered",
        ),
        (
            "no_automatic_task_retry",
            SingleTaskAttempt,
            "one attempt per bound task; child retries are not covered",
        ),
        (
            "hold_failed_serial_predecessor",
            LocalSerialSelection,
            "selected task order with verified completion and runtime release",
        ),
        (
            "guard_weekly_provider_usage",
            WeeklyTaskUsageBinding,
            "new guarded task admission; no child token or cost ceiling",
        ),
        (
            "require_declared_task_output",
            TaskDeclaredOutput,
            "exact queue result path declaration only; file contents and scientific validity are not qualified",
        ),
        (
            "require_declared_provider_model",
            TaskDeclaredProviderModel,
            "exact queue provider/model metadata only; actual command behavior and model fitness are not attested",
        ),
        (
            "require_selected_task_isolation",
            TaskSelectedIsolation,
            "explicit existing host or read policy; actual scope is the selected policy, not complete isolation",
        ),
        (
            "require_verified_native_worker_invocation",
            NativeInvocation,
            "exact supported controller/bootstrap and input/preflight binding; adapter rechecks source at entry; not model qualification",
        ),
        (
            "require_native_entry_preflight_recheck",
            NativeEntryPreflight,
            "sealed preflight argv rerun once inside approved native task; fresh sidecar and closeout reserve required before model entry",
        ),
        (
            "require_bound_worker_execution_contract",
            BoundWorkerExecutionContract,
            "supported native v2, tool-free Codex or deterministic metadata stages, deadlines, limits and review-pending return; adapter-declared checkpoint limits, no model qualification or arbitrary branch authority",
        ),
        (
            "require_qualified_worker_contract",
            Unsupported,
            "no common accepted worker qualification or executable step/output contract is bound to queue metadata",
        ),
        (
            "checkpoint_running_task",
            Unsupported,
            "no common worker checkpoint contract",
        ),
        (
            "terminate_running_task_at_window_end",
            Unsupported,
            "shift stop does not terminate running tasks",
        ),
        (
            "continue_independent_field_steps",
            Unsupported,
            "no common observation or execution contract for child scientific steps",
        ),
        (
            "retain_opposing_scientific_result",
            Unsupported,
            "an arbitrary child command cannot attest scientific content retention",
        ),
        (
            "retry_uncertain_call",
            Unsupported,
            "no common child-call outcome or idempotency contract",
        ),
        (
            "switch_provider_on_timeout",
            Unsupported,
            "provider change needs a newly reviewed execution selection",
        ),
        (
            "auto_accept_predecessor",
            Unsupported,
            "owning task acceptance requires Ondesk disposition",
        ),
        (
            "reserve_stage_and_closeout_time",
            Unsupported,
            "no common per-stage duration and closeout reservation contract",
        ),
    ]
    .into_iter()
    .map(|(id, prerequisite, boundary)| Control {
        id,
        prerequisite,
        boundary,
    })
    .collect()
}

pub fn rules_sha256() -> Result<String> {
    let mut hash = Sha256::new();
    hash.update(b"forager.offdesk-preparation-capability-rules.v1\0");
    hash.update(serde_json::to_vec(&catalog())?);
    hash.update(serde_json::to_vec(
        &super::native_worker_invocation::execution_rules(),
    )?);
    hash.update(serde_json::to_vec(
        &super::codex_review_invocation::execution_rules(),
    )?);
    hash.update(Sha256::digest(include_bytes!(
        "metadata_consumer_invocation.rs"
    )));
    hash.update(Sha256::digest(include_bytes!(
        "../../../scripts/offdesk_metadata_dispatch.py"
    )));
    hash.update(super::super::read_policy::ReadPolicy::metadata().sha256()?);
    hash.update(b"forager.weekly-usage-evaluator.v1\0");
    hash.update(Sha256::digest(include_bytes!(
        "../../../scripts/offdesk_usage_guard.py"
    )));
    Ok(format!("{:x}", hash.finalize()))
}

pub fn assess(manifest: &OffdeskShiftManifestV1, tasks: &[OffdeskTask]) -> Result<Value> {
    let current_rules = rules_sha256()?;
    let workers = manifest
        .task_bindings
        .iter()
        .map(|binding| {
            let checked = tasks
                .iter()
                .find(|t| t.task_id == binding.task_id)
                .map(|task| {
                    super::worker_invocation::inspect(
                        task,
                        Some(&manifest.window),
                        chrono::Utc::now(),
                    )
                    .and_then(|view| {
                        ensure!(
                            view.is_none() || manifest.budget.max_attempts_per_task == 1,
                            "bound native invocation requires one task attempt"
                        );
                        Ok(view)
                    })
                });
            match checked {
                Some(Ok(Some(review))) => review,
                Some(Err(error)) => {
                    json!({"task_id":binding.task_id,"status":"held","reason":error.to_string()})
                }
                _ => json!({"task_id":binding.task_id,"status":"unselected"}),
            }
        })
        .collect::<Vec<_>>();
    let native = workers
        .iter()
        .map(|row| {
            if row["adapter"] == "codex_tool_free_review" {
                json!({"task_id":row["task_id"],"status":"unselected"})
            } else {
                row.clone()
            }
        })
        .collect::<Vec<_>>();
    let mut holds = workers
        .iter()
        .filter(|row| row["status"] == "held")
        .map(|row| format!("worker invocation {}: {}", row["task_id"], row["reason"]))
        .collect::<Vec<_>>();

    if workers
        .iter()
        .any(|row| row["adapter"] == "codex_tool_free_review")
        && (manifest
            .read_policy
            .as_ref()
            .is_none_or(|p| p.validate().is_err())
            || manifest.host_policy_attestation.is_some())
    {
        holds.push("Codex review requires the canonical shift read policy".into());
    }
    let Some(requirements) = &manifest.preparation_requirements else {
        return Ok(
            json!({"schema":"offdesk_preparation_capability_review.v1", "status":if holds.is_empty() {"unselected"} else {"held"},
            "requirements_satisfied":null, "controls":[], "holds":holds, "native_invocations":native,"worker_invocations":workers,
            "scope":"selected_manifest_controls_only", "runtime_policy_authorized":false,
            "current_rules_sha256":current_rules}),
        );
    };
    requirements.validate()?;
    if requirements.capability_rules_sha256 != current_rules {
        holds.push(
            "preparation capability rules changed; prepare and review a new manifest".to_string(),
        );
    }
    let has_tasks = !manifest.task_bindings.is_empty();
    let local = has_tasks
        && manifest
            .task_bindings
            .iter()
            .all(|t| t.runner_kind == BackgroundRunnerKind::LocalBackground);
    let sources = has_tasks
        && manifest
            .task_bindings
            .iter()
            .all(|t| t.ondesk_source_snapshot.is_some());
    let choices = super::task_execution_review::choices(manifest, tasks);
    let exact_tasks = has_tasks && choices.iter().all(|task| task.task_identity_matches);
    let catalog = catalog();
    let mut rows = Vec::new();
    for id in &requirements.required_controls {
        let control = catalog.iter().find(|c| c.id == id);
        let prerequisite = control
            .map(|c| c.prerequisite)
            .unwrap_or(Prerequisite::Unsupported);
        use Prerequisite::*;
        let (supported, reason) = match prerequisite {
            ShiftWindow => (true, "existing_shift_admission_gate"),
            OwningTaskSources => (sources, "requires_exact_owning_sources_for_every_task"),
            LocalBackgroundTasks => (local, "requires_local_background_tasks"),
            SingleTaskAttempt => (
                has_tasks && manifest.budget.max_attempts_per_task == 1,
                "requires_task_attempt_limit_one",
            ),
            LocalSerialSelection => (
                local
                    && manifest.serial_selection.is_some()
                    && manifest.budget.max_concurrent_tasks == 1,
                "requires_local_background_serial_selection",
            ),
            WeeklyTaskUsageBinding => (
                has_tasks && manifest.weekly_usage.is_some(),
                "requires_weekly_task_usage_binding",
            ),
            NativeInvocation => (
                has_tasks
                    && native.iter().all(|row| {
                        row["status"] == "verified_invocation_adapter_rechecks_source_at_entry"
                    }),
                "requires_verified_native_invocation_for_every_task",
            ),
            NativeEntryPreflight => (
                has_tasks
                    && native.iter().all(|row| {
                        row["status"] == "verified_invocation_adapter_rechecks_source_at_entry"
                            && row["entry_preflight"].is_object()
                    }),
                "requires_explicit_native_entry_preflight_policy_for_every_task",
            ),
            BoundWorkerExecutionContract => (
                exact_tasks
                    && sources
                    && super::worker_invocation::validate_selected(
                        manifest,
                        tasks,
                        chrono::Utc::now(),
                    )
                    .is_ok()
                    && manifest.budget.max_attempts_per_task == 1
                    && workers.iter().all(super::worker_invocation::bound),
                "requires_exact_source_bound_supported_worker_contract_for_every_task",
            ),
            TaskDeclaredOutput => (
                exact_tasks && choices.iter().all(|task| task.output_declared),
                "requires_declared_result_path_for_every_exact_task",
            ),
            TaskDeclaredProviderModel => (
                exact_tasks && choices.iter().all(|task| task.provider_model_declared),
                "requires_declared_provider_and_model_for_every_exact_task",
            ),
            TaskSelectedIsolation => (
                exact_tasks && choices.iter().all(|task| task.isolation_selected),
                "requires_selected_task_host_or_read_policy",
            ),
            Unsupported => (false, "unsupported_preparation_control"),
        };
        let status = if supported {
            "supported_by_existing_gate"
        } else if matches!(prerequisite, Unsupported) {
            "unsupported"
        } else {
            "missing_selection"
        };
        if !supported {
            holds.push(format!("preparation control {id}: {reason}"));
        }
        rows.push(json!({"id":id,"status":status,"prerequisite":reason,
            "boundary":control.map(|c| c.boundary).unwrap_or("unknown control; no execution support inferred")}));
    }
    Ok(json!({"schema":"offdesk_preparation_capability_review.v1",
        "status":if holds.is_empty() {"selected_requirements_supported"} else {"held"},
        "requirements_satisfied":holds.is_empty(),"controls":rows,"holds":holds,"native_invocations":native,"worker_invocations":workers,
        "scope":"selected_manifest_controls_only", "runtime_policy_authorized":false,
        "recorded_rules_sha256":requirements.capability_rules_sha256,"current_rules_sha256":current_rules}))
}

pub fn validate_launch(manifest: &OffdeskShiftManifestV1, tasks: &[OffdeskTask]) -> Result<()> {
    let report = assess(manifest, tasks)?;
    let holds = report["holds"]
        .as_array()
        .expect("capability assessment holds");
    ensure!(
        holds.is_empty(),
        "preparation requirements hold: {}",
        holds
            .iter()
            .map(|h| h.as_str().unwrap_or("unknown requirement"))
            .collect::<Vec<_>>()
            .join("; ")
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn manifest() -> OffdeskShiftManifestV1 {
        let now = chrono::Utc::now();
        serde_json::from_value(json!({"schema":"offdesk_shift_manifest.v1", "shift_id":"capability-fixture",
            "profile":"fixture","created_at":now,"window":{"starts_at":now,"stops_at":now+chrono::Duration::hours(1),"timezone":"Asia/Seoul"},
            "plan_id":"plan","plan_review_id":"review","plan_source_sha256":"a".repeat(64),"plan_review_sha256":"b".repeat(64),
            "task_bindings":[],"routine_bindings":[],"budget":{"max_concurrent_tasks":1,"max_task_launches":1,"max_attempts_per_task":1,"max_routine_runs":0},
            "authority":{"task_dispatch_requires_fresh_approval":true,"task_launch_spec_is_immutable":true,"wiki_writes_are_candidate_only":true,
                "plan_revision_requires_review":true,"acceptance_requires_ondesk_review":true,"does_not_authorize":crate::offdesk::OFFDESK_SHIFT_REQUIRED_DENIALS}})).unwrap()
    }

    fn require(manifest: &mut OffdeskShiftManifestV1, ids: &[&str]) {
        manifest.preparation_requirements = Some(
            PreparationRequirementsV1::new(ids.iter().map(|id| (*id).to_string()).collect())
                .unwrap(),
        );
    }

    #[test]
    fn absent_requirements_make_no_capability_claim_and_keep_legacy_bytes() {
        let manifest = manifest();
        let report = assess(&manifest, &[]).unwrap();
        assert_eq!(report["status"], "unselected");
        assert!(report["requirements_satisfied"].is_null());
        assert_eq!(report["runtime_policy_authorized"], false);
        assert!(serde_json::to_value(&manifest)
            .unwrap()
            .get("preparation_requirements")
            .is_none());
        validate_launch(&manifest, &[]).unwrap();
    }

    #[test]
    fn every_worker_semantic_or_unknown_control_is_held() {
        let mut manifest = manifest();
        for control in catalog()
            .iter()
            .filter(|c| matches!(c.prerequisite, Prerequisite::Unsupported))
        {
            require(&mut manifest, &[control.id]);
            assert_eq!(
                assess(&manifest, &[]).unwrap()["controls"][0]["status"],
                "unsupported"
            );
            assert!(validate_launch(&manifest, &[]).is_err(), "{}", control.id);
        }
        require(&mut manifest, &["unknown_control"]);
        assert!(validate_launch(&manifest, &[]).is_err());
    }

    #[test]
    fn missing_manifest_choices_are_distinct_from_supported_admission() {
        let mut manifest = manifest();
        for id in [
            "guard_weekly_provider_usage",
            "hold_failed_serial_predecessor",
            "hold_source_drift",
            "hold_unaccepted_dependencies",
            "hold_active_or_uncertain_workdir",
            "hold_uncertain_launch",
            "no_automatic_task_retry",
        ] {
            require(&mut manifest, &[id]);
            assert_eq!(
                assess(&manifest, &[]).unwrap()["controls"][0]["status"],
                "missing_selection"
            );
            assert!(validate_launch(&manifest, &[]).is_err());
        }
        require(&mut manifest, &["stop_new_dispatch_at_window_end"]);
        validate_launch(&manifest, &[]).unwrap();
        assert_eq!(
            assess(&manifest, &[]).unwrap()["requirements_satisfied"],
            true
        );
    }

    #[test]
    fn invalid_duplicate_or_drifted_requirements_cannot_inherit_support() {
        for ids in [
            vec![],
            vec!["same".into(), "same".into()],
            vec!["BAD-id".into()],
            vec!["x".repeat(81)],
            vec!["x".into(); 33],
        ] {
            assert!(PreparationRequirementsV1::new(ids).is_err());
        }
        let mut manifest = manifest();
        require(&mut manifest, &["stop_new_dispatch_at_window_end"]);
        let before = serde_json::to_vec(&manifest).unwrap();
        manifest
            .preparation_requirements
            .as_mut()
            .unwrap()
            .capability_rules_sha256 = "f".repeat(64);
        assert_ne!(before, serde_json::to_vec(&manifest).unwrap());
        assert!(validate_launch(&manifest, &[])
            .unwrap_err()
            .to_string()
            .contains("capability rules changed"));
        let mut value = serde_json::to_value(manifest.preparation_requirements.unwrap()).unwrap();
        value["runtime_policy_authorized"] = json!(true);
        assert!(serde_json::from_value::<PreparationRequirementsV1>(value).is_err());
    }
}
