//! Shared local review projection. Only an explicit request writes a pending approval.

use super::*;
use serde_json::json;

#[derive(Clone, Default)]
pub(crate) struct ReviewOptions {
    pub project_keys: Option<Vec<String>>,
    pub approval_ttl_minutes: Option<u64>,
}

pub(crate) fn list(profile: &str) -> Result<Vec<String>> {
    Ok(OffdeskShiftStore::read_only(profile)?
        .snapshot()?
        .records
        .into_iter()
        .rev()
        .map(|record| record.manifest.shift_id)
        .collect())
}

fn preferences(profile: &str, options: &ReviewOptions) -> Result<(Vec<String>, u64)> {
    let global = crate::session::load_config_read_only()?.unwrap_or_default();
    let root = crate::session::profile_dir_path(profile)?;
    let path = root.join("config.toml");
    let config = if match std::fs::symlink_metadata(&path) {
        Ok(_) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => return Err(error.into()),
    } {
        let mut sources = ProfileSourceBoundary::open(root)?;
        let bytes = sources.read_bytes(&path)?;
        let profile: crate::session::ProfileConfig = toml::from_str(std::str::from_utf8(&bytes)?)?;
        sources.ensure_current()?;
        crate::session::merge_configs(global, &profile)
    } else {
        global
    };
    let keys = options
        .project_keys
        .clone()
        .unwrap_or(config.night.project_keys);
    let ttl = options
        .approval_ttl_minutes
        .unwrap_or(config.night.approval_ttl_minutes);
    ensure!(
        (1..=1440).contains(&ttl),
        "approval TTL must be between 1 and 1440 minutes"
    );
    let unique: HashSet<_> = keys.iter().collect();
    ensure!(
        unique.len() == keys.len() && keys.iter().all(|k| !k.trim().is_empty() && k.trim() == k),
        "night project keys must be nonempty, trimmed and unique"
    );
    Ok((keys, ttl))
}

pub(crate) fn inspect(profile: &str, shift_id: &str, options: &ReviewOptions) -> Result<Value> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::read_only(&profile)?;
    let record = store
        .record(shift_id)?
        .context("bounded Offdesk shift not found")?;
    let manifest = &record.manifest;
    let (keys, ttl) = preferences(&profile, options)?;
    let now = Utc::now();
    let mut holds = Vec::new();
    let mut projects = manifest
        .task_bindings
        .iter()
        .map(|b| b.project_key.clone())
        .chain(
            manifest
                .routine_bindings
                .iter()
                .filter_map(|b| b.project_key.clone()),
        )
        .collect::<Vec<_>>();
    projects.sort();
    projects.dedup();
    let selected: HashSet<_> = keys.iter().collect();
    let bound: HashSet<_> = projects.iter().collect();
    if keys.is_empty() || selected != bound {
        holds.push("choose_exact_manifest_project_scope".to_string());
    }
    if record.state.is_some() || record.arm_receipt.is_some() {
        holds.push("shift_already_armed_or_terminal".to_string());
    }
    if now > manifest.window.stops_at {
        holds.push("shift_window_expired".to_string());
    }
    // Reuse prepare/arm source checks, including initial serial queue and registry versions.
    let live = match revalidate_manifest_authority(
        &profile,
        manifest,
        now,
        false,
        record.arm_receipt.is_none(),
    ) {
        Ok(live) => Some(live),
        Err(error) => {
            holds.push(format!("authority_changed: {error:#}"));
            None
        }
    };
    let root = crate::session::profile_dir_path(&profile)?;
    let mut sources = ProfileSourceBoundary::open(root.clone())?;
    let queue: Vec<OffdeskTask> = if manifest.task_bindings.is_empty() {
        Vec::new()
    } else {
        sources.read_task_queue(&root.join("offdesk_tasks.json"), false)?
    };
    let tasks = manifest
        .task_bindings
        .iter()
        .map(|binding| {
            let task = queue.iter().find(|t| t.task_id == binding.task_id);
            json!({"binding":binding,
            "command":task.map(|t| &t.command), "workdir":task.map(|t| &t.workdir),
            "provider":task.and_then(|t| t.provider_id.as_deref()).unwrap_or("unknown"),
            "model":task.and_then(|t| t.model.as_deref()).unwrap_or("unknown"),
            "queue_status":task.map(|t| t.status),
            "dispatch_approval":"requires_fresh_tick_gate_not_granted_by_shift_arm"})
        })
        .collect::<Vec<_>>();
    let (approvals, _, _) = crate::offdesk::approval::load_approvals_bounded_read_only(&root)?;
    let approvals = approvals.iter().filter(|a| arm_approval_identity_matches(a, manifest)).map(|a| {
        Ok(json!({"approval_id":a.approval_id,"status":a.status,"expires_at":a.expires_at,"expired":a.expires_at < now,
            "approval_sha256":approval_sha256(a)?}))
    }).collect::<Result<Vec<_>>>()?;
    let active_approvals = approvals
        .iter()
        .filter(|a| {
            a["expired"] == false && matches!(a["status"].as_str(), Some("pending" | "approved"))
        })
        .collect::<Vec<_>>();
    let activation_approval = (holds.is_empty() && active_approvals.len() == 1)
        .then(|| active_approvals[0]["approval_id"].clone());
    sources.ensure_current()?;
    if let Some(live) = live {
        live.ensure_current()?;
    }
    let weekly =
        crate::offdesk::weekly_usage::projection(&root, manifest.weekly_usage.as_ref(), now);
    if weekly["allowed"] == false {
        holds.push("weekly_usage_hold_requires_fresh_samples_and_review".into());
    }
    let capabilities =
        crate::offdesk::workflow::preparation_capabilities::assess(manifest, &queue)?;
    let execution_choices = crate::offdesk::workflow::task_execution_review::report_with_workers(
        manifest,
        &queue,
        capabilities["worker_invocations"]
            .as_array()
            .context("worker invocation views missing")?,
    )?;
    crate::offdesk::workflow::invocation_inputs::ReviewInputScope::ensure_current()?;
    let mut report = json!({
        "schema":"offdesk_shift_local_start_review.v1", "profile":profile,
        "manifest_sha256":offdesk_shift_manifest_sha256(manifest)?, "manifest":manifest,
        "projects":projects, "tasks":tasks,
        "preparation":preparation_draft(manifest, &queue),
        "preparation_review_binding":manifest.preparation_review,
        "task_execution_choices":execution_choices,
        "preparation_capabilities":capabilities,
        "preferences":{"project_keys":keys,"approval_ttl_minutes":ttl,
            "scope_origin":if options.project_keys.is_some() { "one_run_override" } else { "saved_global_profile" },
            "ttl_origin":if options.approval_ttl_minutes.is_some() { "one_run_override" } else { "saved_global_profile" }},
        "weekly_usage":weekly,
        "arm_approvals":approvals,"requestable":holds.is_empty(),"holds":holds,
        "activation":{"approval_id":if holds.is_empty() {activation_approval} else {None},"requires_explicit_confirmation":true,
            "requires_unknown_task_usage_acknowledgement":!manifest.task_bindings.is_empty(),
            "does_not_dispatch":true},
        "task_usage":{"tokens":null,"cost_micro_usd":null,"status":"unknown", "cost_ceiling":"not_bound_by_this_review"},
        "routine_budget_scope":"fixed_routine_handlers_only_not_child_task_models",
        "stop_policy":"stop_new_dispatch_at_manifest_stops_at",
        "shutdown_policy":"does_not_kill_or_checkpoint_already_running_tasks",
        "effect":"read_only_no_prepare_arm_dispatch_or_config_write"
    });
    let bytes = serde_json::to_vec(&report)?;
    let mut hash = Sha256::new();
    hash.update(b"forager.local-shift-review.v1\0");
    hash.update(bytes);
    report["review_sha256"] = json!(format!("{:x}", hash.finalize()));
    Ok(report)
}

fn approval_sha256(approval: &crate::offdesk::PendingActionApproval) -> Result<String> {
    // Approval briefs contain HashMaps; their iteration order differs across processes.
    let mut value = serde_json::to_value(approval)?;
    value.sort_all_objects();
    Ok(format!("{:x}", Sha256::digest(serde_json::to_vec(&value)?)))
}

pub(crate) fn confirm_arm(
    profile: &str,
    shift_id: &str,
    options: &ReviewOptions,
    expected_review: &str,
    approval_id: &str,
    operator: &str,
    acknowledge_unknown_task_usage: bool,
) -> Result<Value> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    let report = inspect(profile, shift_id, options)?;
    ensure!(
        report["review_sha256"].as_str() == Some(expected_review),
        "local review changed; refresh before confirmation"
    );
    ensure!(
        report["activation"]["approval_id"].as_str() == Some(approval_id),
        "exact fresh arm approval is unavailable; refresh and review"
    );
    ensure!(report["activation"]["requires_unknown_task_usage_acknowledgement"] != true || acknowledge_unknown_task_usage,
        "child-task usage is unknown; explicit acknowledgement required, no spend cap is enforced by this confirmation");
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::read_only(&profile)?;
    let record = store
        .record(shift_id)?
        .context("reviewed shift disappeared")?;
    ensure!(
        report["manifest_sha256"].as_str()
            == Some(offdesk_shift_manifest_sha256(&record.manifest)?.as_str()),
        "reviewed manifest changed"
    );
    let now = Utc::now();
    let manifest = &record.manifest;
    manifest.enterprise_policy()?.validate()?;
    if let Some(attestation) = &manifest.host_policy_attestation {
        crate::offdesk::reverify_offdesk_shift_host_policy_attestation(attestation, now)?;
    }
    if let Some(policy) = &manifest.read_policy {
        crate::offdesk::read_policy::verify(policy)?;
    }
    let live = revalidate_manifest_authority(&profile, manifest, now, false, true)?;
    let expected_approval_hash = report["arm_approvals"]
        .as_array()
        .context("review lacks arm approvals")?
        .iter()
        .find(|a| a["approval_id"].as_str() == Some(approval_id))
        .and_then(|a| a["approval_sha256"].as_str())
        .context("review lacks exact approval version")?;
    let ledger = ApprovalLedger::new(crate::session::profile_dir_path(&profile)?);
    let confirmed_approval = {
        let (mut session, _) = ledger.begin_bounded_session(now)?;
        let identity = session
            .bounded_root_identity()
            .context("local confirmation lacks profile identity")?;
        store.ensure_profile_root_identity(identity)?;
        let approval = session
            .approval(approval_id)
            .cloned()
            .context("reviewed approval disappeared")?;
        ensure!(
            approval_sha256(&approval)? == expected_approval_hash,
            "arm approval changed before confirmation"
        );
        validate_arm_approval(&approval, manifest, now)?;
        live.ensure_current()?;
        let confirmed = match approval.status {
            ApprovalStatus::Pending => session.approve_exact_pending(&approval, operator, now)?,
            ApprovalStatus::Approved => approval,
            _ => bail!("reviewed arm approval is not pending or approved"),
        };
        session.flush()?;
        store.ensure_profile_root_identity(identity)?;
        live.ensure_current()?;
        confirmed
    };
    // Reuse arm's live source, policy and receipt checks after resolving approval.
    // A failure here preserves the approved request for explicit recovery, without retry.
    let result = arm_offdesk_shift_bound(
        &profile,
        &ShiftArmArgs {
            shift_id: Some(shift_id.to_string()),
            approval_id: Some(approval_id.to_string()),
            approval_ttl_minutes: 30,
            json: true,
        },
        Some(&confirmed_approval),
    )?;
    match result {
        ShiftArmResult::Armed { record, created } => {
            Ok(json!({"effect":"shift_armed_without_tick_or_dispatch",
            "shift_id":shift_id,"manifest_sha256":offdesk_shift_manifest_sha256(&record.manifest)?,
            "approval_id":approval_id,"created":created,"arm_receipt":record.arm_receipt,
            "state":record.state,"task_dispatch_requires_fresh_approval":true,
            "task_usage":"unknown_no_new_spend_cap","automatic_retry":false}))
        }
        ShiftArmResult::Pending(_) => {
            bail!("confirmation did not arm; inspect pending approval and shift state before retry")
        }
    }
}

pub(crate) fn request_approval(
    profile: &str,
    shift_id: &str,
    options: &ReviewOptions,
    expected_review: &str,
) -> Result<Value> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    let report = inspect(profile, shift_id, options)?;
    ensure!(
        report["review_sha256"].as_str() == Some(expected_review),
        "local review changed; refresh before requesting approval"
    );
    ensure!(
        report["requestable"] == true,
        "local review is held: {}",
        report["holds"]
    );
    let ttl = report["preferences"]["approval_ttl_minutes"]
        .as_u64()
        .context("review lacks approval TTL")?;
    let store = OffdeskShiftStore::read_only(profile)?;
    let record = store
        .record(shift_id)?
        .context("reviewed shift disappeared")?;
    ensure!(
        report["manifest_sha256"].as_str()
            == Some(offdesk_shift_manifest_sha256(&record.manifest)?.as_str()),
        "reviewed manifest changed"
    );
    let now = Utc::now();
    let live = revalidate_manifest_authority(profile, &record.manifest, now, false, true)?;
    ensure!(
        now <= record.manifest.window.stops_at,
        "shift window expired"
    );
    let ledger = ApprovalLedger::new(crate::session::profile_dir_path(profile)?);
    let (mut session, _) = ledger.begin_bounded_session(now)?;
    let profile_identity = session
        .bounded_root_identity()
        .context("local arm request lacks profile root identity")?;
    store.ensure_profile_root_identity(profile_identity)?;
    live.ensure_current()?;
    session.ensure_bounded_root_active()?;
    let args = ShiftArmArgs {
        shift_id: Some(shift_id.to_string()),
        approval_id: None,
        approval_ttl_minutes: i64::try_from(ttl)?,
        json: true,
    };
    let selected = select_or_create_arm_approval(&mut session, &record.manifest, &args, now)?;
    let approval = selected
        .or(find_exact_pending_approval(
            &session,
            &record.manifest,
            now,
        )?)
        .context("arm approval request has no durable identity")?;
    live.ensure_current()?;
    session.ensure_bounded_root_active()?;
    session.flush()?;
    store.ensure_profile_root_identity(profile_identity)?;
    Ok(
        json!({"effect":"arm_approval_request_only_no_arm_or_dispatch", "approval_id":approval.approval_id,
        "status":approval.status,"expires_at":approval.expires_at,
        "next":"review with offdesk pending; explicitly approve with offdesk ok <id>, then shift arm <shift-id> --approval-id <id>"}),
    )
}

pub(crate) fn text(report: &Value) -> String {
    let mut lines = vec![
        format!(
            "Shift: {} | profile: {}",
            report["manifest"]["shift_id"], report["profile"]
        ),
        format!(
            "Pending-request readiness: {} | holds: {}",
            report["requestable"], report["holds"]
        ),
        "Task token/cost usage: UNKNOWN. No child-task cost ceiling bound here.".into(),
        format!(
            "Preparation: {} | follow-ups awaiting predecessor acceptance: {}",
            report["preparation"]["status"]
                .as_str()
                .unwrap_or("unavailable"),
            report["preparation"]["dependent_followups"]
                .as_array()
                .map_or(0, Vec::len)
        ),
        "Preparation responses are review drafts; runtime action support remains to be verified."
            .into(),
        format!(
            "Recorded preparation SHA256: {} | current draft SHA256: {}",
            report["preparation_review_binding"]["report_sha256"]
                .as_str()
                .unwrap_or("legacy_unbound"),
            report["preparation"]["report_sha256"]
                .as_str()
                .unwrap_or("unavailable")
        ),
        format!(
            "Recorded decision-rules SHA256: {} | draft ready for launch: {}",
            report["preparation_review_binding"]["decision_rules_sha256"]
                .as_str()
                .unwrap_or("legacy_unbound"),
            report["preparation"]["ready_for_launch"]
        ),
        format!(
            "Preparation controls: {} | selected requirements satisfied: {}",
            report["preparation_capabilities"]["status"],
            report["preparation_capabilities"]["requirements_satisfied"]
        ),
        "Shift arm does not approve task dispatch. Each task needs the fresh tick gate.".into(),
        format!(
            "Manifest SHA256: {}",
            report["manifest_sha256"].as_str().unwrap_or("unknown")
        ),
        format!(
            "Review SHA256: {}",
            report["review_sha256"].as_str().unwrap_or("unknown")
        ),
        format!(
            "Projects: {} | selected scope: {}",
            report["projects"], report["preferences"]["project_keys"]
        ),
        format!(
            "Window: {} to {} ({})",
            report["manifest"]["window"]["starts_at"],
            report["manifest"]["window"]["stops_at"],
            report["manifest"]["window"]["timezone"]
        ),
        format!(
            "Concurrency: {} | launch/attempt budget: {}",
            report["manifest"]["budget"]["max_concurrent_tasks"], report["manifest"]["budget"]
        ),
        format!(
            "New arm request TTL: {} minutes ({})",
            report["preferences"]["approval_ttl_minutes"], report["preferences"]["ttl_origin"]
        ),
        "Stop time stops new dispatch. Running tasks are not killed or checkpointed.".into(),
        format!(
            "Routine-only resource budget: {}",
            report["manifest"]["routine_resource_budget"]
        ),
        format!("Arm approvals: {}", report["arm_approvals"]),
        format!(
            "Activation approval: {} | unknown task usage acknowledgement: {}",
            report["activation"]["approval_id"],
            report["activation"]["requires_unknown_task_usage_acknowledgement"]
        ),
    ];
    for control in report["preparation_capabilities"]["controls"]
        .as_array()
        .into_iter()
        .flatten()
    {
        lines.push(format!(
            "Control {}: {} | {}",
            control["id"], control["status"], control["boundary"]
        ));
    }
    for native in report["preparation_capabilities"]["worker_invocations"]
        .as_array()
        .into_iter()
        .flatten()
    {
        if let Some(contract) = native["worker_execution_contract"].as_object() {
            let continuation = if native["adapter"] == "deterministic_metadata" {
                "deterministic local worker, no model; committed batches within one attempt; no dispatcher restart"
            } else {
                "no automatic retry or checkpoint resume"
            };
            lines.push(format!("Selected execution {}: deadline {}; command timeout {}s; one worker start; {}; result needs Ondesk review.",
                native["task_id"],contract["deadline"],contract["command_timeout_seconds"], continuation));
            lines.push(format!("Execution steps: {}", contract["technical_steps"]));
        }
        if native["entry_preflight"].is_object() {
            let policy = &native["entry_preflight"];
            lines.push(format!(
                "Native {}: deadline={} | entry tests run only inside approved task",
                native["task_id"], native["deadline"]
            ));
            lines.push(format!(
                "Entry preflight: at most {}s | minimum worker {}s | closeout reserve {}s",
                policy["timeout_seconds"],
                policy["minimum_worker_seconds"],
                policy["closeout_reserve_seconds"]
            ));
        }
    }
    lines.push(format!(
        "Task execution choice review: {} | qualified worker contract: {}",
        report["task_execution_choices"]["review_sha256"],
        report["task_execution_choices"]["worker_qualification_complete"]
    ));
    for task in report["task_execution_choices"]["tasks"]
        .as_array()
        .into_iter()
        .flatten()
    {
        lines.push(format!(
            "Execution {} / {}: exact identity={} result={} | missing declarations={}",
            task["project_key"],
            task["task_id"],
            task["task_identity_matches"],
            task["result_artifact_path"],
            task["missing_declarations"]
        ));
    }
    if report["weekly_usage"]["runtime_weekly_limit"] == true {
        let scope = if report["weekly_usage"]["binding"]["contract"]["nightly"].is_object() {
            "Nightly allowance from nightly-start weekly-meter baseline"
        } else {
            "Run allowance from approved weekly-meter baseline"
        };
        lines.push(format!(
            "{}: {} percentage points per provider | {} | increments: {}",
            scope,
            report["weekly_usage"]["binding"]["contract"]["limit_percentage_points"],
            report["weekly_usage"]["status"],
            report["weekly_usage"]["verdict"]["increment_percentage_points"]
        ));
        lines.push(format!("Samples must be <= {} seconds old. Other sessions count; in-flight tasks/calls may overshoot. No automatic polling or USD/token cap.",
            report["weekly_usage"]["binding"]["max_age_seconds"]));
    }
    for (index, task) in report["tasks"].as_array().into_iter().flatten().enumerate() {
        lines.push(format!(
            "{}. {} / {} | runner {} | provider {} | model {}",
            index + 1,
            task["binding"]["project_key"],
            task["binding"]["task_id"],
            task["binding"]["runner_kind"],
            task["provider"],
            task["model"]
        ));
        lines.push(format!(
            "   command: {}\n   workdir: {}",
            task["command"], task["workdir"]
        ));
        lines.push(format!(
            "   source/root/command/brief bindings: {}",
            task["binding"]
        ));
    }
    lines.join("\n")
}
