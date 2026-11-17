//! Read-only proposals reuse task/shift validation and confer no dispatch authority.

use anyhow::{ensure, Context, Result};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::path::Path;

use super::*;
use crate::session::project_registry::{load_registry_checked, registry_path};

pub(super) fn propose(profile: &str, args: &super::super::ShiftProposalArgs) -> Result<Value> {
    let prepare = &args.prepare;
    ensure!(
        prepare.serial_proposal_sha256.is_none() && prepare.serial_project_keys.is_empty(),
        "propose takes --project-key; serial binding flags belong to prepare/preview"
    );
    validate_prepare_window(prepare, Utc::now())?;
    ensure!(
        prepare.host_policy.is_none()
            && (prepare.read_policy.is_none()
                || prepare.read_policy
                    == Some(super::super::ShiftReadPolicyArg::LinuxLandlockMetadataBootIdentity)),
        "proposal does not run isolation probes; verify isolation separately during prepare"
    );
    ensure!(
        prepare.shift_id.is_none()
            && prepare.dispatch_interval_minutes.is_none()
            && prepare.wiki_project_key.is_none()
            && prepare.wiki_profile.is_none()
            && prepare.wiki_root.is_none()
            && prepare.wiki_at.is_none()
            && prepare.morning_review_at.is_none(),
        "proposal selects queued tasks only, without shift IDs or routines"
    );
    ensure!(
        prepare.max_concurrent_tasks.is_none_or(|value| value == 1),
        "serial proposal requires max-concurrent-tasks=1"
    );
    ensure!(prepare.task_ids.len() <= 128, "proposal exceeds 128 tasks");
    let selected: HashSet<_> = prepare.task_ids.iter().collect();
    ensure!(
        selected.len() == prepare.task_ids.len(),
        "duplicate selected task ID"
    );
    let projects: HashSet<_> = args.project_keys.iter().collect();
    ensure!(
        projects.len() == args.project_keys.len(),
        "duplicate project key"
    );
    let registry_path = registry_path();
    let registry = load_registry_checked(&registry_path)?;
    for key in &args.project_keys {
        ensure!(
            registry
                .entries
                .iter()
                .filter(|entry| entry.key == *key)
                .count()
                == 1,
            "proposal project must be uniquely registered: {key}"
        );
    }

    let profile = normalize_profile_name(profile)?;
    let mut sources = ProfileSourceBoundary::open(crate::session::profile_dir_path(&profile)?)?;
    let plan = load_plan_authority(
        &profile,
        &mut sources,
        &prepare.plan_ref,
        prepare.review_id.as_deref(),
    )?;
    let queue_path = sources.root_path().join("offdesk_tasks.json");
    let bytes = if queue_path.try_exists()? {
        Some(sources.read_bytes(&queue_path)?)
    } else {
        None
    };
    let tasks: Vec<OffdeskTask> = bytes
        .as_deref()
        .map(|bytes| crate::migrations::v005_task_review_links::decode(std::str::from_utf8(bytes)?))
        .transpose()?
        .unwrap_or_default();
    ensure!(tasks.len() <= 1024, "proposal queue exceeds 1024 tasks");
    let ids: HashSet<_> = tasks.iter().map(|task| &task.task_id).collect();
    ensure!(
        ids.len() == tasks.len(),
        "proposal queue has duplicate task IDs"
    );
    ensure!(
        selected.iter().all(|id| ids.contains(*id)),
        "selected task missing from queue"
    );

    let conflicts = crate::offdesk::workdir_conflicts::inspect_with_runtime(
        sources.root_path(),
        &tasks,
        &prepare.task_ids,
    )?;
    let store = OffdeskShiftStore::read_only(&profile)?;
    let mut rows = Vec::new();
    let mut boundaries = Vec::new();
    let mut verified_sources = Vec::new();
    for (index, id) in prepare.task_ids.iter().enumerate() {
        let task = tasks.iter().find(|task| task.task_id == *id).unwrap();
        ensure!(
            projects.contains(&task.project_key),
            "selected task is outside explicit project scope"
        );
        let mut row = json!({
            "position":index + 1, "project_key":task.project_key, "task_id":task.task_id,
            "request_id":task.request_id, "workdir":task.workdir,
            "command_sha256":sha256_hex(task.command.as_bytes()),
            "provider_id":task.provider_id, "model":task.model,
            "not_before":task.not_before, "status":"held", "reasons":[],
            "subproject_scope":"not_declared_by_queue_contract"
        });
        let mut reasons = Vec::new();
        let checked = (|| -> Result<_> {
            let entry =
                resolve_unique_project_for_path(Path::new(&task.workdir), &registry.entries)?
                    .context("proposal workdir is not uniquely registered")?;
            ensure!(
                entry.key == task.project_key,
                "proposal workdir belongs to another project"
            );
            let mut one = prepare.clone();
            one.task_ids = vec![id.clone()];
            one.review_id = Some(plan.review.review_id.clone());
            one.max_concurrent_tasks = Some(1);
            let (manifest, source, roots) =
                build_shift_manifest_unbound(&profile, &one, None, &store, false)?;
            let binding = &manifest.task_bindings[0];
            let snapshot = binding
                .ondesk_source_snapshot
                .as_ref()
                .context("explicit owning TODO binding required; unbound work remains held")?;
            let original = snapshot.task(&task.project_key, id)?;
            let artifact = task
                .artifact_refs
                .iter()
                .find(|artifact| {
                    artifact
                        .artifact_id
                        .starts_with(crate::offdesk::workflow::ondesk_task_binding::BINDING_PREFIX)
                })
                .context("owning TODO artifact missing")?;
            row["owning_plan_path"] = json!(artifact.path);
            row["source_sha256"] = json!(snapshot.source_sha256);
            row["goal"] = json!(original.goal);
            row["completion_criteria"] = json!(original.completion_criteria);
            row["dependencies"] = json!(original.depends_on);
            row["dependency_status"] = json!("all_source_dependencies_accepted");
            row["launch_spec_sha256"] = json!(binding.launch_spec_sha256);
            row["execution_brief_sha256"] = json!(binding.execution_brief_sha256);
            row["root_identity_sha256"] = json!(binding.project_root_identity_sha256);
            verified_sources.push((task, snapshot.clone()));
            Ok((source, roots))
        })();
        match checked {
            Ok(boundary) => boundaries.push(boundary),
            Err(error) => reasons.push(error.to_string()),
        }
        if conflicts["status"] == "incomplete" {
            reasons
                .push("queue occupancy unresolved; resolve before selecting runtime work".into());
        }
        for conflict in conflicts["conflicts"].as_array().unwrap() {
            if conflict["reason"] == "active_or_uncertain_queue_occupancy"
                && conflict["task_ids"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(id))
            {
                reasons.push("active_or_uncertain_queue_occupancy".into());
            }
        }
        if reasons.is_empty() {
            row["status"] = json!("eligible_for_serial_proposal");
        }
        row["reasons"] = json!(reasons);
        rows.push(row);
    }
    let valid = !rows.is_empty()
        && rows
            .iter()
            .all(|row| row["status"] == "eligible_for_serial_proposal");
    let mut weekly_binding = None;
    let mut preparation = json!({
        "schema":"offdesk_preparation_draft.v1", "status":"selection_held",
        "holds_from":"selected task reasons", "ready_for_launch":false,
        "decisions":crate::offdesk::workflow::offdesk_preparation::decision_templates(),
        "effect":"diagnostic_only_no_new_execution_authority"
    });
    if valid {
        let mut combined = prepare.clone();
        combined.review_id = Some(plan.review.review_id.clone());
        combined.max_concurrent_tasks = Some(1);
        let (combined_manifest, source, roots) =
            build_shift_manifest_unbound(&profile, &combined, None, &store, false)?;
        preparation = preparation_draft(&combined_manifest, &tasks);
        weekly_binding = combined_manifest.weekly_usage;
        boundaries.push((source, roots));
    }
    for (source, roots) in &boundaries {
        ensure_boundaries_current(source, roots)?;
    }
    for (task, snapshot) in &verified_sources {
        ensure!(
            crate::offdesk::workflow::ondesk_task_binding::capture_task_source(task)?.as_ref()
                == Some(snapshot),
            "owning TODO changed during proposal"
        );
    }
    ensure!(
        conflicts
            == crate::offdesk::workdir_conflicts::inspect_with_runtime(
                sources.root_path(),
                &tasks,
                &prepare.task_ids,
            )?,
        "queue occupancy changed during proposal"
    );
    ensure!(
        load_registry_checked(&registry_path)?.content_sha256 == registry.content_sha256,
        "project registry changed during proposal"
    );
    sources.ensure_current()?;
    let mut proposal = super::super::operator_safe_json_value(json!({
        "schema":"offdesk_serial_selection_proposal.v1", "profile":profile,
        "plan_id":plan.plan_id, "plan_review_id":plan.review.review_id,
        "plan_source_sha256":plan.registration.source_sha256, "plan_review_sha256":plan.review_sha256,
        "registry_sha256":registry.content_sha256,
        "queue_sha256":bytes.as_ref().map(|bytes| sha256_hex(bytes)),
        "profile_root_identity":sources.root_identity(),
        "window":{"starts_at":prepare.starts_at,"stops_at":prepare.stops_at,"timezone":prepare.timezone},
        "requested_settings":prepare_settings(prepare)?, "weekly_usage_binding":weekly_binding,
        "project_keys":args.project_keys, "requested_task_order":prepare.task_ids,
        "tasks":rows, "serial_order":if valid {prepare.task_ids.clone()} else {Vec::new()},
        "selection_status":if rows.is_empty() {"no_selected_work"} else if valid {"ready_for_review"} else {"held"},
        "outside_scope_count":tasks.iter().filter(|task| !projects.contains(&task.project_key)).count(),
        "unselected_in_scope_count":tasks.iter().filter(|task| projects.contains(&task.project_key) && !selected.contains(&task.task_id)).count(),
        "queue_conflicts":conflicts, "runtime_enforced":false,
        "preparation":preparation,
        "resource_review":{"status":"required_before_dispatch", "unverified":["other profiles", "interactive sessions", "shared external services and data", "executor qualification and isolation"]},
        "estimated_wall_time":"unavailable", "task_cost_ceiling":"unavailable",
        "does_not_authorize":["plan_write","enqueue","approval","prepare","arm","launch","session_input","accepted_truth"],
        "limitations":["explicit task-id order is a proposal, not scheduler enforcement", "selected predecessors must already be accepted in each owning TODO", "prepare and dispatch must revalidate exact inputs and enforce the selected order", "logical subproject ownership needs an explicit reviewed scope", "no automatic TODO import, owner assignment or fallback work"]
    }));
    let hash = sha256_hex(&serde_json::to_vec(&proposal)?);
    if let Some(expected) = &args.expected_proposal_sha256 {
        ensure!(
            *expected == hash,
            "serial proposal changed; inspect a fresh proposal before confirmation"
        );
    }
    proposal["proposal_sha256"] = json!(hash);
    Ok(proposal)
}

fn prepare_settings(args: &ShiftPrepareArgs) -> Result<Value> {
    let mut settings = json!({
        "weekly_usage_state":args.weekly_usage_state,
        "weekly_gpt_sample":args.weekly_gpt_sample,"weekly_claude_sample":args.weekly_claude_sample,
        "weekly_max_age_seconds":args.weekly_max_age_seconds,
        "max_concurrent_tasks":1, "max_attempts_per_task":args.max_attempts_per_task,
        "enterprise_policy":format!("{:?}",args.enterprise_policy),
        "max_routine_process_starts":args.max_routine_process_starts,
        "max_routine_external_calls":args.max_routine_external_calls,
        "max_routine_disk_bytes":args.max_routine_disk_bytes,
        "max_routine_llm_tokens":args.max_routine_llm_tokens,
        "max_routine_cost_micro_usd":args.max_routine_cost_micro_usd,
        "routine_llm_cost_rate_micro_usd_per_million_tokens":args.routine_llm_cost_rate_micro_usd_per_million_tokens
    });
    if !args.required_preparation_controls.is_empty() {
        settings["preparation_requirements"] = json!(
            crate::offdesk::workflow::preparation_capabilities::PreparationRequirementsV1::new(
                args.required_preparation_controls.clone()
            )?
        );
    }
    if args.read_policy == Some(super::super::ShiftReadPolicyArg::LinuxLandlockMetadataBootIdentity)
    {
        settings["metadata_read_policy_sha256"] =
            json!(crate::offdesk::read_policy::ReadPolicy::metadata().sha256()?);
    }
    Ok(settings)
}

pub(super) fn bind(
    profile: &str,
    args: &ShiftPrepareArgs,
) -> Result<Option<crate::offdesk::shift::OffdeskShiftSerialSelectionV1>> {
    let Some(expected) = &args.serial_proposal_sha256 else {
        ensure!(
            args.serial_project_keys.is_empty(),
            "serial project scope needs a proposal hash"
        );
        return Ok(None);
    };
    ensure!(
        !args.serial_project_keys.is_empty(),
        "serial proposal needs explicit project scope"
    );
    let mut input = args.clone();
    input.serial_proposal_sha256 = None;
    input.serial_project_keys.clear();
    input.shift_id = None;
    input.dispatch_interval_minutes = None;
    input.wiki_project_key = None;
    input.wiki_profile = None;
    input.wiki_root = None;
    input.wiki_at = None;
    input.morning_review_at = None;
    input.host_policy = None;
    if input.read_policy
        != Some(super::super::ShiftReadPolicyArg::LinuxLandlockMetadataBootIdentity)
    {
        input.read_policy = None;
    }
    let proposal = propose(
        profile,
        &super::super::ShiftProposalArgs {
            prepare: input,
            project_keys: args.serial_project_keys.clone(),
            expected_proposal_sha256: Some(expected.clone()),
        },
    )?;
    ensure!(
        proposal["selection_status"] == "ready_for_review",
        "serial selection remains held"
    );
    Ok(Some(crate::offdesk::shift::OffdeskShiftSerialSelectionV1 {
        proposal_sha256: expected.clone(),
        registry_sha256: proposal["registry_sha256"]
            .as_str()
            .context("proposal registry hash missing")?
            .into(),
        queue_sha256: proposal["queue_sha256"]
            .as_str()
            .context("proposal queue hash missing")?
            .into(),
    }))
}

pub(super) fn ensure_serial_inputs(
    manifest: &OffdeskShiftManifestV1,
    sources: &mut ProfileSourceBoundary,
    initial_confirmation: bool,
) -> Result<()> {
    let Some(serial) = &manifest.serial_selection else {
        return Ok(());
    };
    ensure!(
        load_registry_checked(&registry_path())?.content_sha256 == serial.registry_sha256,
        "serial registry changed; prepare a newly reviewed selection"
    );
    if initial_confirmation {
        let path = sources.root_path().join("offdesk_tasks.json");
        ensure!(
            sha256_hex(&sources.read_bytes(&path)?) == serial.queue_sha256,
            "serial queue changed before confirmation; prepare a fresh proposal"
        );
    }
    Ok(())
}
