#!/usr/bin/env python3
"""Dispatch execution flow helpers for run handlers."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_orch_contract import attach_phase2_team_spec
from aoe_tg_orch_contract import derive_tf_phase, derive_tf_phase_reason, normalize_tf_phase
from aoe_tg_request_contract import request_contract_metadata
from aoe_tg_task_state import apply_exec_critic_lifecycle


@dataclass
class RunDispatchFlowContext:
    args: Any
    p_args: Any
    key: str
    entry: Dict[str, Any]
    manager_state: Dict[str, Any]
    chat_id: str
    prompt: str
    dispatch_mode: bool
    dispatch_roles: str
    available_roles: List[str]
    verifier_candidates: List[str]
    run_priority_override: Optional[str]
    run_timeout_override: Optional[int]
    run_no_wait_override: Optional[bool]
    run_auto_source: str
    run_control_mode: str
    run_source_request_id: str
    run_intent_command: str = ""
    run_intent_action: str = ""
    run_intent_class: str = ""
    run_intent_trace: str = ""
    run_source_task: Optional[Dict[str, Any]] = None
    run_selected_execution_lane_ids: List[str] = field(default_factory=list)
    run_selected_review_lane_ids: List[str] = field(default_factory=list)
    provisional_req_id: str = ""
    provisional_task: Optional[Dict[str, Any]] = None
    todo_id: str = ""
    pending_todo_used: bool = False
    selected_role_preset: str = ""
    source_prompt: str = ""
    request_contract: Optional[Dict[str, Any]] = None


@dataclass
class RunDispatchFlowDeps:
    send: Callable[..., bool]
    log_event: Callable[..., None]
    record_outcome: Optional[Callable[[Dict[str, Any]], None]]
    now_iso: Callable[[], str]
    save_manager_state: Callable[..., None]
    lifecycle_set_stage: Callable[..., None]
    parse_roles_csv: Callable[[Optional[str]], List[str]]
    available_worker_roles: Callable[[List[str]], List[str]]
    normalize_task_plan_payload: Callable[..., Dict[str, Any]]
    build_task_execution_plan: Callable[..., Dict[str, Any]]
    critique_task_execution_plan: Callable[..., Dict[str, Any]]
    critic_has_blockers: Callable[[Dict[str, Any]], bool]
    repair_task_execution_plan: Callable[..., Dict[str, Any]]
    plan_roles_from_subtasks: Callable[[Optional[Dict[str, Any]]], List[str]]
    phase1_ensemble_planning: Callable[..., Dict[str, Any]]
    ensure_verifier_roles: Callable[..., tuple[List[str], List[str], bool, List[str]]]
    build_planned_dispatch_prompt: Callable[[str, Dict[str, Any], Dict[str, Any]], str]
    run_aoe_orch: Callable[..., Dict[str, Any]]
    touch_chat_recent_task_ref: Callable[..., None]
    set_chat_selected_task_ref: Callable[..., None]
    sync_task_lifecycle: Callable[..., Optional[Dict[str, Any]]]
    summarize_task_lifecycle: Callable[[str, Dict[str, Any]], str]
    synthesize_orchestrator_response: Callable[[Any, str, Dict[str, Any]], str]
    render_run_response: Callable[..., str]
    finalize_request_reply_messages: Callable[..., Dict[str, Any]]
    critique_task_result: Callable[..., Dict[str, Any]]
    extract_todo_proposals: Callable[..., List[Dict[str, Any]]]
    merge_todo_proposals: Callable[..., Dict[str, Any]]
    compute_dispatch_plan: Callable[..., Any]
    emit_planning_progress: Callable[..., None]
    dispatch_and_sync_task: Callable[..., Any]
    apply_plan_and_lineage: Callable[..., None]
    enforce_dispatch_policies: Callable[..., Any]
    filter_phase2_retry_scope: Callable[..., tuple[Optional[Dict[str, Any]], Dict[str, Any]]]
    finalize_provisional_task: Callable[..., None]
    update_provisional_planning_task: Callable[..., None]
    effective_todo_token: Callable[..., str]
    cleanup_terminal_todo_gate: Callable[..., None]
    maybe_send_manual_followup_alert: Callable[..., None]
    maybe_capture_todo_proposals: Callable[..., Dict[str, Any]]
    attach_todo_to_task_and_entry: Callable[..., None]
    finalize_todo_after_run: Callable[..., None]
    send_dispatch_exception: Callable[..., None]
    send_exec_critic_intervention: Callable[..., bool]
    send_dispatch_result: Callable[..., bool]


def execute_dispatch_flow(*, ctx: RunDispatchFlowContext, deps: RunDispatchFlowDeps) -> bool:
    args = ctx.args
    p_args = ctx.p_args
    key = ctx.key
    entry = ctx.entry
    manager_state = ctx.manager_state
    chat_id = ctx.chat_id
    prompt = ctx.prompt
    source_prompt = str(ctx.source_prompt or prompt).strip() or prompt
    dispatch_mode = ctx.dispatch_mode
    dispatch_roles = ctx.dispatch_roles
    available_roles = list(ctx.available_roles or [])
    verifier_candidates = list(ctx.verifier_candidates or [])
    run_priority_override = ctx.run_priority_override
    run_timeout_override = ctx.run_timeout_override
    run_no_wait_override = ctx.run_no_wait_override
    run_auto_source = ctx.run_auto_source
    run_control_mode = ctx.run_control_mode
    run_source_request_id = ctx.run_source_request_id
    run_intent_command = str(ctx.run_intent_command or "").strip()
    run_intent_action = str(ctx.run_intent_action or "").strip()
    run_intent_class = str(ctx.run_intent_class or "").strip()
    run_intent_trace = str(ctx.run_intent_trace or "").strip()
    run_source_task = ctx.run_source_task if isinstance(ctx.run_source_task, dict) else None
    run_selected_execution_lane_ids = list(ctx.run_selected_execution_lane_ids or [])
    run_selected_review_lane_ids = list(ctx.run_selected_review_lane_ids or [])
    provisional_req_id = str(ctx.provisional_req_id or "").strip()
    provisional_task = ctx.provisional_task if isinstance(ctx.provisional_task, dict) else None
    todo_id = str(ctx.todo_id or "").strip()
    pending_todo_used = bool(ctx.pending_todo_used)
    selected_role_preset = str(ctx.selected_role_preset or "").strip()
    request_contract = ctx.request_contract if isinstance(ctx.request_contract, dict) else {}

    send = deps.send
    log_event = deps.log_event
    record_outcome = deps.record_outcome
    now_iso = deps.now_iso
    save_manager_state = deps.save_manager_state
    lifecycle_set_stage = deps.lifecycle_set_stage

    exec_enabled = bool(getattr(args, "exec_critic", False))
    exec_max_attempts = max(1, int(getattr(args, "exec_critic_retry_max", 3)))
    exec_attempt = 1
    exec_feedback = ""
    last_exec_critic: Dict[str, Any] = {}

    final_state: Dict[str, Any] = {}
    final_req_id = ""
    final_task: Optional[Dict[str, Any]] = None
    final_control_mode = run_control_mode
    final_source_request_id = run_source_request_id
    local_run_control_mode = run_control_mode
    local_run_source_request_id = run_source_request_id
    local_run_source_task = run_source_task
    local_pending_todo_used = bool(pending_todo_used)
    local_todo_id = todo_id
    emit_planning_chat = True

    def _report_plan_progress(*, phase: str, detail: str = "", attempt: int = 0, total: int = 0) -> None:
        deps.emit_planning_progress(
            phase=phase,
            key=key,
            send=send,
            log_event=log_event,
            emit_chat=emit_planning_chat,
            request_id=provisional_req_id,
            task=provisional_task if isinstance(provisional_task, dict) else None,
            detail=detail,
            attempt=attempt,
            total=total,
        )
        deps.update_provisional_planning_task(
            task=provisional_task,
            phase=phase,
            detail=detail,
            attempt=attempt,
            total=total,
            lifecycle_set_stage=lifecycle_set_stage,
            now_iso=now_iso,
        )
        if isinstance(provisional_task, dict) and (not args.dry_run):
            save_manager_state(args.manager_state_file, manager_state)

    while True:
        attempt_prompt = prompt
        if exec_feedback:
            attempt_prompt = f"{prompt}\n\n[Exec Critic Feedback]\n{exec_feedback}"

        selected_roles = deps.parse_roles_csv(dispatch_roles)
        plan_meta = deps.compute_dispatch_plan(
            args=args,
            p_args=p_args,
            prompt=attempt_prompt,
            request_contract=request_contract,
            dispatch_mode=dispatch_mode,
            run_control_mode=local_run_control_mode,
            run_source_task=local_run_source_task,
            selected_roles=selected_roles,
            available_roles=available_roles,
            available_worker_roles=deps.available_worker_roles,
            normalize_task_plan_payload=deps.normalize_task_plan_payload,
            build_task_execution_plan=deps.build_task_execution_plan,
            critique_task_execution_plan=deps.critique_task_execution_plan,
            critic_has_blockers=deps.critic_has_blockers,
            repair_task_execution_plan=deps.repair_task_execution_plan,
            plan_roles_from_subtasks=deps.plan_roles_from_subtasks,
            phase1_ensemble_planning=deps.phase1_ensemble_planning,
            report_progress=_report_plan_progress,
        )
        selected_roles = list(plan_meta.selected_roles or selected_roles)
        plan_data = plan_meta.plan_data
        plan_critic = plan_meta.plan_critic or {"approved": True, "issues": [], "recommendations": []}
        plan_roles = list(plan_meta.plan_roles or [])
        plan_replans = list(plan_meta.plan_replans or [])
        plan_error = str(plan_meta.plan_error or "")
        plan_gate_blocked = bool(plan_meta.plan_gate_blocked)
        plan_gate_reason = str(plan_meta.plan_gate_reason or "")
        planning_enabled = bool(plan_meta.planning_enabled)
        reuse_source_plan = bool(plan_meta.reuse_source_plan)
        phase1_mode = str(plan_meta.phase1_mode or "")
        phase1_rounds = max(0, int(plan_meta.phase1_rounds or 0))
        phase1_providers = [
            str(item).strip() for item in (plan_meta.phase1_providers or []) if str(item).strip()
        ]
        rate_limit_meta = dict(plan_meta.rate_limit or {}) if isinstance(plan_meta.rate_limit, dict) else {}
        if provisional_task is not None:
            if rate_limit_meta:
                provisional_task["rate_limit"] = rate_limit_meta
            else:
                provisional_task.pop("rate_limit", None)

        policy = deps.enforce_dispatch_policies(
            dispatch_mode=dispatch_mode,
            args=args,
            key=key,
            entry=entry,
            selected_roles=selected_roles,
            available_roles=available_roles,
            verifier_candidates=verifier_candidates,
            plan_gate_blocked=plan_gate_blocked,
            plan_gate_reason=plan_gate_reason,
            plan_replans=plan_replans,
            ensure_verifier_roles=deps.ensure_verifier_roles,
            dispatch_roles=dispatch_roles,
            send=send,
            record_outcome=record_outcome,
        )
        if bool(policy.terminal):
            deps.finalize_provisional_task(
                task=provisional_task,
                outcome="blocked",
                reason=str(plan_gate_reason or policy.terminal_reason or "dispatch policy blocked").strip(),
                lifecycle_set_stage=lifecycle_set_stage,
                now_iso=now_iso,
            )
            effective_todo_id = deps.effective_todo_token(
                entry=entry,
                chat_id=chat_id,
                todo_id=local_todo_id,
                run_auto_source=run_auto_source,
            )
            deps.cleanup_terminal_todo_gate(
                entry=entry,
                chat_id=chat_id,
                todo_id=local_todo_id,
                pending_todo_used=local_pending_todo_used,
                run_auto_source=run_auto_source,
                reason=str(policy.terminal_reason or "dispatch policy blocked").strip(),
                now_iso=now_iso,
            )
            deps.maybe_send_manual_followup_alert(
                entry=entry,
                todo_id=effective_todo_id,
                project_key=key,
                send=send,
                now_iso=now_iso,
            )
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)
            return True
        dispatch_roles_effective = str(policy.dispatch_roles or dispatch_roles).strip()
        selected_roles = list(policy.selected_roles or selected_roles)
        verifier_roles = list(policy.verifier_roles or [])

        rerun_scope: Dict[str, Any] = {}
        if isinstance(plan_data, dict):
            plan_data = attach_phase2_team_spec(
                plan_data,
                roles=selected_roles,
                verifier_roles=verifier_roles,
                require_verifier=bool(args.require_verifier),
            )
            plan_data, rerun_scope = deps.filter_phase2_retry_scope(
                plan_data=plan_data,
                run_control_mode=local_run_control_mode,
                run_source_task=local_run_source_task,
                retry_critic=last_exec_critic,
                selected_execution_lane_ids=run_selected_execution_lane_ids,
                selected_review_lane_ids=run_selected_review_lane_ids,
            )
            if rerun_scope:
                selected_roles = list(rerun_scope.get("planned_roles") or selected_roles)
                plan_roles = list(rerun_scope.get("planned_roles") or plan_roles)
                verifier_roles = list(rerun_scope.get("review_roles") or verifier_roles)
                if selected_roles:
                    dispatch_roles_effective = ",".join(selected_roles)
                log_event(
                    event="exec_critic_rerun_scope",
                    project=key,
                    request_id=str(local_run_source_request_id or "").strip(),
                    task=local_run_source_task if isinstance(local_run_source_task, dict) else None,
                    stage="planning",
                    status="running",
                    detail=(
                        "execution={execs} review={reviews}".format(
                            execs=",".join(rerun_scope.get("rerun_execution_lane_ids") or []) or "-",
                            reviews=",".join(rerun_scope.get("rerun_review_lane_ids") or []) or "-",
                        )
                    ),
                )

        dispatch_metadata: Dict[str, Any] = {}
        dispatch_metadata.update(request_contract_metadata(request_contract))
        if isinstance(plan_data, dict):
            current_plan_meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
            phase2_team_spec = current_plan_meta.get("phase2_team_spec")
            if isinstance(phase2_team_spec, dict) and phase2_team_spec:
                dispatch_metadata["phase2_team_spec"] = phase2_team_spec
                phase2_critic_role = str(phase2_team_spec.get("critic_role", "")).strip()
                if phase2_critic_role:
                    dispatch_metadata["phase2_critic_role"] = phase2_critic_role
                phase2_integration_role = str(phase2_team_spec.get("integration_role", "")).strip()
                if phase2_integration_role:
                    dispatch_metadata["phase2_integration_role"] = phase2_integration_role
            phase2_execution_plan = current_plan_meta.get("phase2_execution_plan")
            if isinstance(phase2_execution_plan, dict) and phase2_execution_plan:
                dispatch_metadata["phase2_execution_plan"] = phase2_execution_plan
            plan_phase1_mode = str(current_plan_meta.get("phase1_mode", "")).strip().lower()
            if plan_phase1_mode:
                dispatch_metadata["phase1_mode"] = plan_phase1_mode
            try:
                plan_phase1_rounds = max(0, int(current_plan_meta.get("phase1_rounds", 0) or 0))
            except Exception:
                plan_phase1_rounds = 0
            if plan_phase1_rounds > 0:
                dispatch_metadata["phase1_rounds"] = plan_phase1_rounds
            plan_phase1_providers = current_plan_meta.get("phase1_providers")
            if isinstance(plan_phase1_providers, list) and plan_phase1_providers:
                dispatch_metadata["phase1_providers"] = [
                    str(row).strip() for row in plan_phase1_providers if str(row).strip()
                ]
            plan_phase1_role_preset = str(current_plan_meta.get("phase1_role_preset", "")).strip().lower()
            if plan_phase1_role_preset:
                dispatch_metadata["phase1_role_preset"] = plan_phase1_role_preset
            plan_phase2_team_preset = str(current_plan_meta.get("phase2_team_preset", "")).strip().lower()
            if plan_phase2_team_preset:
                dispatch_metadata["phase2_team_preset"] = plan_phase2_team_preset
            plan_evidence_required = plan_data.get("evidence_required")
            if isinstance(plan_evidence_required, list) and plan_evidence_required:
                dispatch_metadata["evidence_required"] = [
                    str(item).strip() for item in plan_evidence_required if str(item).strip()
                ]
        if provisional_req_id:
            dispatch_metadata["request_id"] = provisional_req_id

        dispatch_prompt = attempt_prompt
        if isinstance(plan_data, dict):
            dispatch_prompt = deps.build_planned_dispatch_prompt(attempt_prompt, plan_data, plan_critic)

        try:
            dispatch_result = deps.dispatch_and_sync_task(
                p_args=p_args,
                dispatch_prompt=dispatch_prompt,
                chat_id=chat_id,
                dispatch_roles=dispatch_roles_effective,
                run_priority_override=run_priority_override,
                run_timeout_override=run_timeout_override,
                run_no_wait_override=run_no_wait_override,
                dispatch_metadata=dispatch_metadata,
                key=key,
                entry=entry,
                manager_state=manager_state,
                prompt=source_prompt,
                selected_roles=selected_roles,
                verifier_roles=verifier_roles,
                require_verifier=bool(args.require_verifier),
                verifier_candidates=verifier_candidates,
                run_aoe_orch=deps.run_aoe_orch,
                touch_chat_recent_task_ref=deps.touch_chat_recent_task_ref,
                set_chat_selected_task_ref=deps.set_chat_selected_task_ref,
                now_iso=now_iso,
                sync_task_lifecycle=deps.sync_task_lifecycle,
                intent_command=run_intent_command,
                intent_action=run_intent_action,
                intent_class=run_intent_class,
                intent_trace=run_intent_trace,
            )
        except Exception as exc:
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "dispatch_failed"
            if (not local_todo_id) and str(run_auto_source or "").strip().lower().startswith("todo"):
                pending = entry.get("pending_todo")
                if isinstance(pending, dict):
                    token = str(pending.get("todo_id", "")).strip()
                    if token and str(pending.get("chat_id", "")).strip() == str(chat_id):
                        local_todo_id = token
                        local_pending_todo_used = True

            if local_todo_id:
                deps.finalize_todo_after_run(
                    entry=entry,
                    todo_id=local_todo_id,
                    status="failed",
                    exec_verdict="fail",
                    exec_reason=f"dispatch_failed: {reason}"[:260],
                    req_id="",
                    task=None,
                    now_iso=now_iso,
                )
                pending = entry.get("pending_todo")
                if (
                    isinstance(pending, dict)
                    and str(pending.get("todo_id", "")).strip() == local_todo_id
                    and str(pending.get("chat_id", "")).strip() == str(chat_id)
                ):
                    entry.pop("pending_todo", None)
                    local_pending_todo_used = False
                deps.maybe_send_manual_followup_alert(
                    entry=entry,
                    todo_id=local_todo_id,
                    project_key=key,
                    send=send,
                    now_iso=now_iso,
                )

            if local_pending_todo_used:
                entry.pop("pending_todo", None)
                local_pending_todo_used = False

            deps.finalize_provisional_task(
                task=provisional_task,
                outcome="dispatch_failed",
                reason=reason,
                lifecycle_set_stage=lifecycle_set_stage,
                now_iso=now_iso,
            )

            entry["updated_at"] = now_iso()
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)
            deps.send_dispatch_exception(
                entry=entry,
                key=key,
                todo_id=local_todo_id,
                reason=reason,
                send=send,
                record_outcome=record_outcome,
            )
            log_event(
                event="dispatch_failed",
                project=key,
                request_id="",
                task=None,
                stage="dispatch",
                status="failed",
                error_code="E_DISPATCH",
                detail=reason,
            )
            return True
        state = dispatch_result.state
        req_id = str(dispatch_result.request_id)
        task = dispatch_result.task if isinstance(dispatch_result.task, dict) else None

        deps.apply_plan_and_lineage(
            task=task,
            plan_data=plan_data if isinstance(plan_data, dict) else None,
            plan_critic=plan_critic,
            plan_roles=plan_roles,
            plan_replans=plan_replans,
            plan_error=plan_error,
            phase1_mode=phase1_mode,
            phase1_rounds=phase1_rounds,
            phase1_providers=phase1_providers,
            phase1_role_preset=str(plan_meta.phase1_role_preset or selected_role_preset),
            phase2_team_preset=str(plan_meta.phase2_team_preset or selected_role_preset),
            critic_has_blockers=deps.critic_has_blockers,
            lifecycle_set_stage=lifecycle_set_stage,
            run_control_mode=local_run_control_mode,
            run_source_request_id=local_run_source_request_id,
            run_source_task=local_run_source_task,
            req_id=req_id,
            now_iso=now_iso,
        )
        if isinstance(task, dict):
            task["tf_phase"] = normalize_tf_phase(derive_tf_phase(task), "queued")
            tf_phase_reason = derive_tf_phase_reason(task)
            if tf_phase_reason:
                task["tf_phase_reason"] = tf_phase_reason
            else:
                task.pop("tf_phase_reason", None)
            if rate_limit_meta:
                task["rate_limit"] = dict(rate_limit_meta)
                if rate_limit_meta.get("degraded_by"):
                    task.setdefault("result", {})
                    task["result"]["degraded_by"] = [
                        str(item).strip()
                        for item in (rate_limit_meta.get("degraded_by") or [])
                        if str(item).strip()
                    ]

        if local_todo_id:
            deps.attach_todo_to_task_and_entry(
                entry=entry,
                chat_id=chat_id,
                todo_id=local_todo_id,
                req_id=req_id,
                task=task,
                now_iso=now_iso,
            )
            if local_pending_todo_used:
                entry.pop("pending_todo", None)
                local_pending_todo_used = False

        final_state = state
        final_req_id = req_id
        final_task = task
        final_control_mode = local_run_control_mode
        final_source_request_id = local_run_source_request_id

        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

        ver_status = ""
        if isinstance(task, dict):
            ver_status = str((task.get("stages") or {}).get("verification", "pending"))

        if (not exec_enabled) or (not bool(state.get("complete", False))) or (not (state.get("replies") or [])):
            break
        if bool(args.require_verifier) and ver_status == "failed":
            break

        try:
            critic = deps.critique_task_result(
                p_args,
                source_prompt,
                state,
                task,
                exec_attempt,
                exec_max_attempts,
            )
        except Exception as exc:
            critic = {
                "verdict": "fail",
                "action": "escalate",
                "reason": f"critic_error: {str(exc)[:120]}",
                "fix": "",
                "attempt": exec_attempt,
                "max_attempts": exec_max_attempts,
            }

        last_exec_critic = critic if isinstance(critic, dict) else {}
        if isinstance(task, dict):
            task["exec_critic"] = dict(last_exec_critic)
            apply_exec_critic_lifecycle(
                task,
                last_exec_critic,
                lifecycle_set_stage=lifecycle_set_stage,
            )
            task["updated_at"] = now_iso()
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

        verdict = str(last_exec_critic.get("verdict", "")).strip().lower()
        action = str(last_exec_critic.get("action", "")).strip().lower()
        if verdict == "success":
            break

        if exec_attempt >= exec_max_attempts:
            break

        if verdict != "retry":
            break

        exec_feedback = str(last_exec_critic.get("fix", "")).strip() or str(last_exec_critic.get("reason", "")).strip()
        exec_feedback = exec_feedback[:800]

        local_run_control_mode = "replan" if action == "replan" else "retry"
        local_run_source_request_id = req_id
        local_run_source_task = task
        exec_attempt += 1
        log_event(
            event="exec_critic_retry",
            project=key,
            request_id=req_id,
            task=task,
            stage="integration",
            status="running",
            detail=f"attempt={exec_attempt}/{exec_max_attempts} mode={local_run_control_mode}",
        )
        continue

    verdict = str(last_exec_critic.get("verdict", "")).strip().lower()
    if exec_enabled and last_exec_critic and verdict in {"retry", "fail"}:
        reason = str(last_exec_critic.get("reason", "")).strip()
        proposal_result = deps.maybe_capture_todo_proposals(
            args=args,
            entry=entry,
            key=key,
            p_args=p_args,
            prompt=source_prompt,
            state=final_state,
            req_id=final_req_id,
            task=final_task,
            todo_id=local_todo_id,
            send=send,
            log_event=log_event,
            now_iso=now_iso,
            extract_todo_proposals=deps.extract_todo_proposals,
            merge_todo_proposals=deps.merge_todo_proposals,
        )
        if local_todo_id:
            deps.finalize_todo_after_run(
                entry=entry,
                todo_id=local_todo_id,
                status=str((final_task or {}).get("status", "")).strip(),
                exec_verdict=verdict,
                exec_reason=reason,
                req_id=final_req_id,
                task=final_task,
                now_iso=now_iso,
            )
            deps.maybe_send_manual_followup_alert(
                entry=entry,
                todo_id=local_todo_id,
                project_key=key,
                send=send,
                now_iso=now_iso,
            )
        if (local_todo_id or int(proposal_result.get("created_count", 0) or 0) > 0) and (not args.dry_run):
            save_manager_state(args.manager_state_file, manager_state)
        deps.send_exec_critic_intervention(
            entry=entry,
            key=key,
            final_req_id=final_req_id,
            verdict=verdict,
            reason=reason,
            exec_attempt=exec_attempt,
            exec_max_attempts=exec_max_attempts,
            send=send,
            record_outcome=record_outcome,
        )
        log_event(
            event="exec_critic_blocked",
            project=key,
            request_id=final_req_id,
            task=final_task,
            stage="integration",
            status="failed",
            error_code="E_GATE",
            detail=f"verdict={verdict} attempts={exec_attempt}/{exec_max_attempts}",
        )
        return True

    proposal_result = deps.maybe_capture_todo_proposals(
        args=args,
        entry=entry,
        key=key,
        p_args=p_args,
        prompt=source_prompt,
        state=final_state,
        req_id=final_req_id,
        task=final_task,
        todo_id=local_todo_id,
        send=send,
        log_event=log_event,
        now_iso=now_iso,
        extract_todo_proposals=deps.extract_todo_proposals,
        merge_todo_proposals=deps.merge_todo_proposals,
    )
    if local_todo_id:
        deps.finalize_todo_after_run(
            entry=entry,
            todo_id=local_todo_id,
            status=str((final_task or {}).get("status", "")).strip(),
            exec_verdict=str(last_exec_critic.get("verdict", "")).strip(),
            exec_reason=str(last_exec_critic.get("reason", "")).strip(),
            req_id=final_req_id,
            task=final_task,
            now_iso=now_iso,
        )
        deps.maybe_send_manual_followup_alert(
            entry=entry,
            todo_id=local_todo_id,
            project_key=key,
            send=send,
            now_iso=now_iso,
        )
    if (local_todo_id or int(proposal_result.get("created_count", 0) or 0) > 0) and (not args.dry_run):
        save_manager_state(args.manager_state_file, manager_state)

    return deps.send_dispatch_result(
        args=args,
        key=key,
        entry=entry,
        p_args=p_args,
        prompt=source_prompt,
        state=final_state,
        req_id=final_req_id,
        task=final_task,
        run_control_mode=final_control_mode,
        run_source_request_id=final_source_request_id,
        run_auto_source=run_auto_source,
        send=send,
        log_event=log_event,
        summarize_task_lifecycle=deps.summarize_task_lifecycle,
        synthesize_orchestrator_response=deps.synthesize_orchestrator_response,
        render_run_response=deps.render_run_response,
        finalize_request_reply_messages=deps.finalize_request_reply_messages,
        record_outcome=record_outcome,
    )
