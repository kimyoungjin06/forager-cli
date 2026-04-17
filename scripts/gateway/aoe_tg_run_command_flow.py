#!/usr/bin/env python3
"""Top-level run command orchestration helpers."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_orch_roles import classify_dispatch_role_preset
from aoe_tg_request_contract import (
    apply_execution_brief_snapshot,
    apply_job_contract_snapshot,
    apply_request_contract_snapshot,
    build_execution_brief,
    build_job_contract,
    execution_brief_block_reason,
    execution_brief_is_offdesk_allowed,
    execution_brief_summary,
    build_request_contract,
    job_contract_block_reason,
    job_contract_is_blocking,
    job_contract_planning_appendix,
    request_contract_block_reason,
    request_contract_is_blocking,
    request_contract_planning_appendix,
    request_contract_summary,
)
from aoe_tg_run_dispatch_flow import (
    RunDispatchFlowContext,
    RunDispatchFlowDeps,
)


@dataclass
class RunCommandFlowHelpers:
    resolve_prompt_or_handle_unknown: Callable[..., Optional[str]]
    apply_success_first_prompt_fallbacks: Callable[[str], tuple[str, List[str]]]
    handle_run_rate_limit_and_confirm: Callable[..., bool]
    resolve_dispatch_mode_and_roles: Callable[..., Any]
    resolve_effective_run_options: Callable[..., Any]
    compute_dispatch_plan: Callable[..., Any]
    emit_planning_progress: Callable[..., None]
    enforce_dispatch_policies: Callable[..., Any]
    build_dry_run_preview: Callable[..., str]
    dispatch_and_sync_task: Callable[..., Any]
    apply_plan_and_lineage: Callable[..., None]
    filter_phase2_retry_scope: Callable[..., tuple[Optional[Dict[str, Any]], Dict[str, Any]]]
    provision_planning_task: Callable[..., tuple[str, Optional[Dict[str, Any]]]]
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
    send_planning_detached_notice: Callable[..., None]
    start_background_dispatch_flow: Callable[..., None]
    maybe_handle_no_wait_dispatch_detach: Callable[..., Optional[bool]]
    execute_dispatch_flow: Callable[..., bool]


def execute_run_command_flow(
    *,
    ctx: Any,
    deps: Any,
    helpers: RunCommandFlowHelpers,
) -> bool:
    cmd = ctx.cmd
    args = ctx.args
    manager_state = ctx.manager_state
    chat_id = ctx.chat_id
    text = ctx.text
    rest = ctx.rest
    orch_target = ctx.orch_target
    run_prompt = ctx.run_prompt
    run_roles_override = ctx.run_roles_override
    run_priority_override = ctx.run_priority_override
    run_timeout_override = ctx.run_timeout_override
    run_no_wait_override = ctx.run_no_wait_override
    run_force_mode = ctx.run_force_mode
    run_auto_source = ctx.run_auto_source
    run_control_mode = ctx.run_control_mode
    run_source_request_id = ctx.run_source_request_id
    run_intent_command = ctx.run_intent_command
    run_intent_action = ctx.run_intent_action
    run_intent_class = ctx.run_intent_class
    run_intent_trace = ctx.run_intent_trace
    run_source_task = ctx.run_source_task
    run_selected_execution_lane_ids = list(ctx.run_selected_execution_lane_ids or [])
    run_selected_review_lane_ids = list(ctx.run_selected_review_lane_ids or [])
    send = deps.core.send
    log_event = deps.core.log_event
    help_text = deps.core.help_text
    record_outcome = deps.core.record_outcome
    summarize_chat_usage = deps.guard.summarize_chat_usage
    detect_high_risk_prompt = deps.guard.detect_high_risk_prompt
    set_confirm_action = deps.guard.set_confirm_action
    save_manager_state = deps.guard.save_manager_state
    get_context = deps.routing.get_context
    choose_auto_dispatch_roles = deps.planning.choose_auto_dispatch_roles
    resolve_verifier_candidates = deps.planning.resolve_verifier_candidates
    load_orchestrator_roles = deps.planning.load_orchestrator_roles
    parse_roles_csv = deps.planning.parse_roles_csv
    ensure_verifier_roles = deps.planning.ensure_verifier_roles
    available_worker_roles = deps.planning.available_worker_roles
    normalize_task_plan_payload = deps.planning.normalize_task_plan_payload
    build_task_execution_plan = deps.planning.build_task_execution_plan
    critique_task_execution_plan = deps.planning.critique_task_execution_plan
    critic_has_blockers = deps.planning.critic_has_blockers
    repair_task_execution_plan = deps.planning.repair_task_execution_plan
    plan_roles_from_subtasks = deps.planning.plan_roles_from_subtasks
    build_planned_dispatch_prompt = deps.planning.build_planned_dispatch_prompt
    run_orchestrator_direct = deps.routing.run_orchestrator_direct
    run_aoe_orch = deps.routing.run_aoe_orch
    create_request_id = deps.routing.create_request_id
    ensure_task_record = deps.routing.ensure_task_record
    touch_chat_recent_task_ref = deps.routing.touch_chat_recent_task_ref
    set_chat_selected_task_ref = deps.routing.set_chat_selected_task_ref
    now_iso = deps.routing.now_iso
    sync_task_lifecycle = deps.routing.sync_task_lifecycle
    lifecycle_set_stage = deps.routing.lifecycle_set_stage
    summarize_task_lifecycle = deps.routing.summarize_task_lifecycle
    synthesize_orchestrator_response = deps.routing.synthesize_orchestrator_response
    critique_task_result = deps.routing.critique_task_result
    extract_todo_proposals = deps.routing.extract_todo_proposals
    merge_todo_proposals = deps.routing.merge_todo_proposals
    render_run_response = deps.routing.render_run_response

    prompt = helpers.resolve_prompt_or_handle_unknown(
        cmd=cmd,
        run_prompt=run_prompt,
        rest=rest,
        text=text,
        send=send,
        help_text=help_text,
    )
    if prompt is None:
        return True

    key, entry, p_args = get_context(orch_target)
    setattr(p_args, "_aoe_control_mode", str(run_control_mode or "").strip().lower())
    setattr(p_args, "_aoe_source_request_id", str(run_source_request_id or "").strip())

    prompt, fallback_notes = helpers.apply_success_first_prompt_fallbacks(prompt)
    source_prompt = str(prompt or "").strip()
    if fallback_notes:
        log_event(
            event="run_fallback_applied",
            project=key,
            request_id=str(run_source_request_id or "").strip(),
            task=run_source_task if isinstance(run_source_task, dict) else None,
            stage="intake",
            status="adjusted",
            detail="; ".join(str(note).strip() for note in fallback_notes if str(note).strip())[:280],
        )

    if helpers.handle_run_rate_limit_and_confirm(
        cmd=cmd,
        args=args,
        manager_state=manager_state,
        chat_id=chat_id,
        key=key,
        entry=entry,
        run_auto_source=run_auto_source,
        run_force_mode=run_force_mode,
        orch_target=orch_target,
        prompt=prompt,
        summarize_chat_usage=summarize_chat_usage,
        detect_high_risk_prompt=detect_high_risk_prompt,
        set_confirm_action=set_confirm_action,
        save_manager_state=save_manager_state,
        send=send,
        log_event=log_event,
    ):
        return True

    todo_id = ""
    pending_todo_used = False
    if isinstance(run_source_task, dict):
        todo_id = str(run_source_task.get("todo_id", "")).strip()
    if (not todo_id) and str(run_auto_source or "").strip().lower().startswith("todo"):
        pending = entry.get("pending_todo")
        if isinstance(pending, dict):
            token = str(pending.get("todo_id", "")).strip()
            if token and str(pending.get("chat_id", "")).strip() == str(chat_id):
                todo_id = token
                pending_todo_used = True

    available_roles = load_orchestrator_roles(p_args.team_dir)
    dispatch_meta = helpers.resolve_dispatch_mode_and_roles(
        run_force_mode=run_force_mode,
        run_roles_override=run_roles_override,
        project_roles_csv=(p_args.roles or ""),
        auto_dispatch_enabled=bool(args.auto_dispatch),
        prompt=prompt,
        choose_auto_dispatch_roles=choose_auto_dispatch_roles,
        available_roles=available_roles,
        team_dir=p_args.team_dir,
    )
    dispatch_mode = bool(dispatch_meta.dispatch_mode)
    dispatch_roles = str(dispatch_meta.dispatch_roles).strip()

    verifier_candidates = resolve_verifier_candidates(args.verifier_roles)

    effective = helpers.resolve_effective_run_options(
        p_args=p_args,
        run_priority_override=run_priority_override,
        run_timeout_override=run_timeout_override,
        run_no_wait_override=run_no_wait_override,
    )
    effective_priority = str(effective.priority)
    effective_timeout = int(effective.timeout)
    effective_no_wait = bool(effective.no_wait)

    planning_requested = bool(getattr(args, "task_planning", False)) or (run_control_mode in {"retry", "replan"})
    configured_phase1_mode = (
        "ensemble" if bool(getattr(args, "plan_phase1_ensemble", True)) and planning_requested and dispatch_mode else "single"
    )
    configured_phase1_rounds = max(3, int(getattr(args, "plan_phase1_rounds", 3) or 3)) if configured_phase1_mode == "ensemble" else 1
    configured_phase1_providers = [
        str(token).strip().lower()
        for token in str(getattr(args, "plan_phase1_providers", "codex,claude") or "codex,claude").split(",")
        if str(token).strip()
    ]
    selected_dispatch_roles = parse_roles_csv(dispatch_roles)
    request_contract = (
        build_request_contract(
            source_prompt=source_prompt,
            selected_roles=selected_dispatch_roles,
            run_control_mode=run_control_mode,
            run_source_task=run_source_task if isinstance(run_source_task, dict) else None,
            intent_action=run_intent_action,
            project_key=key,
        )
        if dispatch_mode
        else {}
    )
    execution_brief = build_execution_brief(request_contract) if dispatch_mode else {}
    job_contract = build_job_contract(request_contract, execution_brief) if dispatch_mode else {}
    selected_role_preset = (
        str(request_contract.get("preset", "")).strip().lower()
        or classify_dispatch_role_preset(source_prompt, selected_roles=selected_dispatch_roles)
    )
    planning_prompt = source_prompt
    contract_appendix = request_contract_planning_appendix(request_contract)
    if contract_appendix:
        planning_prompt = f"{source_prompt}\n\n{contract_appendix}"
    job_contract_appendix = job_contract_planning_appendix(job_contract)
    if job_contract_appendix:
        planning_prompt = f"{planning_prompt}\n\n{job_contract_appendix}"
    provisional_req_id = ""
    provisional_task: Optional[Dict[str, Any]] = None
    if dispatch_mode and planning_requested and (not args.dry_run):
        provisional_req_id, provisional_task = helpers.provision_planning_task(
            entry=entry,
            manager_state=manager_state,
            chat_id=chat_id,
            key=key,
            prompt=source_prompt,
            selected_roles=selected_dispatch_roles,
            require_verifier=bool(args.require_verifier),
            create_request_id=create_request_id,
            ensure_task_record=ensure_task_record,
            lifecycle_set_stage=lifecycle_set_stage,
            touch_chat_recent_task_ref=touch_chat_recent_task_ref,
            set_chat_selected_task_ref=set_chat_selected_task_ref,
            now_iso=now_iso,
            phase1_mode=configured_phase1_mode,
            phase1_rounds=configured_phase1_rounds,
            phase1_providers=configured_phase1_providers,
            phase1_role_preset=selected_role_preset,
            phase2_team_preset=selected_role_preset,
            run_intent_command=run_intent_command,
            run_intent_action=run_intent_action,
            run_intent_class=run_intent_class,
            run_intent_trace=run_intent_trace,
            request_contract=request_contract,
        )
        if isinstance(provisional_task, dict):
            apply_job_contract_snapshot(provisional_task, job_contract)
            apply_execution_brief_snapshot(provisional_task, execution_brief)
        save_manager_state(args.manager_state_file, manager_state)

    job_contract_blocked = job_contract_is_blocking(job_contract) if dispatch_mode else False
    if dispatch_mode and (request_contract_is_blocking(request_contract) or job_contract_blocked or not execution_brief_is_offdesk_allowed(execution_brief)):
        contract_reason = request_contract_block_reason(request_contract)
        job_reason = job_contract_block_reason(job_contract)
        brief_reason = execution_brief_block_reason(execution_brief)
        contract_incomplete = request_contract_is_blocking(request_contract) or job_contract_blocked
        blocked_reason = contract_reason if request_contract_is_blocking(request_contract) else (job_reason if job_contract_blocked else brief_reason)
        block_context = "contract-incomplete" if contract_incomplete else "execution-brief-blocked"
        if isinstance(provisional_task, dict):
            apply_request_contract_snapshot(provisional_task, request_contract)
            apply_job_contract_snapshot(provisional_task, job_contract)
            apply_execution_brief_snapshot(provisional_task, execution_brief)
            helpers.finalize_provisional_task(
                task=provisional_task,
                outcome="blocked",
                reason=blocked_reason,
                lifecycle_set_stage=lifecycle_set_stage,
                now_iso=now_iso,
            )

        if callable(record_outcome):
            record_outcome(
                {
                    "kind": "run_contract",
                    "status": "blocked",
                    "reason_code": "contract_incomplete" if contract_incomplete else "execution_brief_blocked",
                    "next_step": "/offdesk review",
                    "detail": blocked_reason,
                }
            )
        if request_contract_is_blocking(request_contract):
            send(
                "request contract incomplete\n"
                f"- preset: {selected_role_preset or '-'}\n"
                f"- summary: {request_contract_summary(request_contract) or '-'}\n"
                f"- reason: {blocked_reason}\n"
                "hint: 입력 파일, 대상 컬럼, 변환 규칙, artifact contract를 명시한 뒤 다시 실행하세요.",
                context=block_context,
                with_menu=True,
            )
        elif job_contract_blocked:
            send(
                "job contract incomplete\n"
                f"- preset: {selected_role_preset or '-'}\n"
                f"- contract: {job_contract.get('summary', '-') or '-'}\n"
                f"- reason: {blocked_reason}\n"
                "hint: scope, acceptance checks, artifact targets 중 최소 하나를 남길 수 있도록 요청을 더 구체화하세요.",
                context=block_context,
                with_menu=True,
            )
        else:
            send(
                "execution brief blocked\n"
                f"- preset: {selected_role_preset or '-'}\n"
                f"- brief: {execution_brief_summary(execution_brief) or '-'}\n"
                f"- reason: {blocked_reason}\n"
                "hint: on-desk에서 executable slice와 operator decision boundary를 먼저 확정하세요.",
                context=block_context,
                with_menu=True,
            )
        log_event(
            event="contract_incomplete" if contract_incomplete else "execution_brief_blocked",
            project=key,
            request_id=provisional_req_id,
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status="failed",
            detail=blocked_reason[:280],
        )
        if not args.dry_run:
            effective_todo_id = helpers.effective_todo_token(
                entry=entry,
                chat_id=chat_id,
                todo_id=todo_id,
                run_auto_source=run_auto_source,
            )
            helpers.cleanup_terminal_todo_gate(
                entry=entry,
                chat_id=chat_id,
                todo_id=todo_id,
                pending_todo_used=pending_todo_used,
                run_auto_source=run_auto_source,
                reason=blocked_reason,
                now_iso=now_iso,
            )
            helpers.maybe_send_manual_followup_alert(
                entry=entry,
                todo_id=effective_todo_id,
                project_key=key,
                send=send,
                now_iso=now_iso,
            )
            save_manager_state(args.manager_state_file, manager_state)
        return True

    if args.dry_run:
        selected_roles = list(selected_dispatch_roles)
        plan_meta = helpers.compute_dispatch_plan(
            args=args,
            p_args=p_args,
            prompt=planning_prompt,
            request_contract=request_contract,
            dispatch_mode=dispatch_mode,
            run_control_mode=run_control_mode,
            run_source_task=run_source_task,
            selected_roles=selected_roles,
            available_roles=available_roles,
            available_worker_roles=available_worker_roles,
            normalize_task_plan_payload=normalize_task_plan_payload,
            build_task_execution_plan=build_task_execution_plan,
            critique_task_execution_plan=critique_task_execution_plan,
            critic_has_blockers=critic_has_blockers,
            repair_task_execution_plan=repair_task_execution_plan,
            plan_roles_from_subtasks=plan_roles_from_subtasks,
            phase1_ensemble_planning=deps.planning.phase1_ensemble_planning,
            report_progress=None,
        )
        selected_roles = list(plan_meta.selected_roles or selected_roles)
        plan_data = plan_meta.plan_data
        plan_replans = list(plan_meta.plan_replans or [])
        plan_error = str(plan_meta.plan_error or "")
        plan_gate_blocked = bool(plan_meta.plan_gate_blocked)
        planning_enabled = bool(plan_meta.planning_enabled)
        reuse_source_plan = bool(plan_meta.reuse_source_plan)

        policy = helpers.enforce_dispatch_policies(
            dispatch_mode=dispatch_mode,
            args=args,
            key=key,
            entry=entry,
            selected_roles=selected_roles,
            available_roles=available_roles,
            verifier_candidates=verifier_candidates,
            plan_gate_blocked=plan_gate_blocked,
            plan_gate_reason=str(plan_meta.plan_gate_reason or ""),
            plan_replans=plan_replans,
            ensure_verifier_roles=ensure_verifier_roles,
            dispatch_roles=dispatch_roles,
            send=send,
            record_outcome=record_outcome,
        )
        if bool(policy.terminal):
            if not args.dry_run:
                effective_todo_id = helpers.effective_todo_token(
                    entry=entry,
                    chat_id=chat_id,
                    todo_id=todo_id,
                    run_auto_source=run_auto_source,
                )
                helpers.cleanup_terminal_todo_gate(
                    entry=entry,
                    chat_id=chat_id,
                    todo_id=todo_id,
                    pending_todo_used=pending_todo_used,
                    run_auto_source=run_auto_source,
                    reason=str(policy.terminal_reason or "dispatch policy blocked").strip(),
                    now_iso=now_iso,
                )
                helpers.maybe_send_manual_followup_alert(
                    entry=entry,
                    todo_id=effective_todo_id,
                    project_key=key,
                    send=send,
                    now_iso=now_iso,
                )
                save_manager_state(args.manager_state_file, manager_state)
            return True
        dry_dispatch_roles = str(policy.dispatch_roles or dispatch_roles).strip()
        verifier_roles = list(policy.verifier_roles or [])
        verifier_added = bool(policy.verifier_added)

        preview = helpers.build_dry_run_preview(
            key=key,
            dispatch_mode=dispatch_mode,
            prompt=prompt,
            dispatch_roles=dry_dispatch_roles,
            require_verifier=bool(args.require_verifier),
            verifier_roles=verifier_roles,
            verifier_added=verifier_added,
            run_control_mode=run_control_mode,
            run_source_request_id=run_source_request_id,
            planning_enabled=planning_enabled,
            reuse_source_plan=reuse_source_plan,
            plan_data=plan_data if isinstance(plan_data, dict) else None,
            plan_replans=plan_replans,
            plan_gate_blocked=plan_gate_blocked,
            plan_error=plan_error,
            effective_priority=str(effective_priority),
            effective_timeout=int(effective_timeout),
            effective_no_wait=bool(effective_no_wait),
        )
        send(preview, context="dry-run")
        return True

    if not dispatch_mode:
        direct_reply = run_orchestrator_direct(p_args, prompt)
        send(direct_reply, context="direct")
        log_event(event="direct_reply", project=key, stage="close", status="completed")
        return True

    def _execute_dispatch_flow() -> bool:
        return helpers.execute_dispatch_flow(
            ctx=RunDispatchFlowContext(
                args=args,
                p_args=p_args,
                key=key,
                entry=entry,
                manager_state=manager_state,
                chat_id=chat_id,
                prompt=planning_prompt,
                source_prompt=source_prompt,
                dispatch_mode=dispatch_mode,
                dispatch_roles=dispatch_roles,
                available_roles=available_roles,
                verifier_candidates=verifier_candidates,
                run_priority_override=run_priority_override,
                run_timeout_override=run_timeout_override,
                run_no_wait_override=run_no_wait_override,
                run_auto_source=run_auto_source,
                run_control_mode=run_control_mode,
                run_source_request_id=run_source_request_id,
                run_intent_command=run_intent_command,
                run_intent_action=run_intent_action,
                run_intent_class=run_intent_class,
                run_intent_trace=run_intent_trace,
                run_source_task=run_source_task,
                run_selected_execution_lane_ids=run_selected_execution_lane_ids,
                run_selected_review_lane_ids=run_selected_review_lane_ids,
                provisional_req_id=provisional_req_id,
                provisional_task=provisional_task,
                todo_id=todo_id,
                pending_todo_used=pending_todo_used,
                selected_role_preset=selected_role_preset,
                request_contract=request_contract,
            ),
            deps=RunDispatchFlowDeps(
                send=send,
                log_event=log_event,
                record_outcome=record_outcome,
                now_iso=now_iso,
                save_manager_state=save_manager_state,
                lifecycle_set_stage=lifecycle_set_stage,
                parse_roles_csv=parse_roles_csv,
                available_worker_roles=available_worker_roles,
                normalize_task_plan_payload=normalize_task_plan_payload,
                build_task_execution_plan=build_task_execution_plan,
                critique_task_execution_plan=critique_task_execution_plan,
                critic_has_blockers=critic_has_blockers,
                repair_task_execution_plan=repair_task_execution_plan,
                plan_roles_from_subtasks=plan_roles_from_subtasks,
                phase1_ensemble_planning=deps.planning.phase1_ensemble_planning,
                ensure_verifier_roles=ensure_verifier_roles,
                build_planned_dispatch_prompt=build_planned_dispatch_prompt,
                run_aoe_orch=run_aoe_orch,
                touch_chat_recent_task_ref=touch_chat_recent_task_ref,
                set_chat_selected_task_ref=set_chat_selected_task_ref,
                sync_task_lifecycle=sync_task_lifecycle,
                summarize_task_lifecycle=summarize_task_lifecycle,
                synthesize_orchestrator_response=synthesize_orchestrator_response,
                render_run_response=render_run_response,
                finalize_request_reply_messages=deps.routing.finalize_request_reply_messages,
                critique_task_result=critique_task_result,
                extract_todo_proposals=extract_todo_proposals,
                merge_todo_proposals=merge_todo_proposals,
                compute_dispatch_plan=helpers.compute_dispatch_plan,
                emit_planning_progress=helpers.emit_planning_progress,
                dispatch_and_sync_task=helpers.dispatch_and_sync_task,
                apply_plan_and_lineage=helpers.apply_plan_and_lineage,
                enforce_dispatch_policies=helpers.enforce_dispatch_policies,
                filter_phase2_retry_scope=helpers.filter_phase2_retry_scope,
                finalize_provisional_task=helpers.finalize_provisional_task,
                update_provisional_planning_task=helpers.update_provisional_planning_task,
                effective_todo_token=helpers.effective_todo_token,
                cleanup_terminal_todo_gate=helpers.cleanup_terminal_todo_gate,
                maybe_send_manual_followup_alert=helpers.maybe_send_manual_followup_alert,
                maybe_capture_todo_proposals=helpers.maybe_capture_todo_proposals,
                attach_todo_to_task_and_entry=helpers.attach_todo_to_task_and_entry,
                finalize_todo_after_run=helpers.finalize_todo_after_run,
                send_dispatch_exception=helpers.send_dispatch_exception,
                send_exec_critic_intervention=helpers.send_exec_critic_intervention,
                send_dispatch_result=helpers.send_dispatch_result,
            ),
        )

    detached_result = helpers.maybe_handle_no_wait_dispatch_detach(
        dispatch_mode=dispatch_mode,
        planning_requested=planning_requested,
        effective_no_wait=effective_no_wait,
        args=args,
        entry=entry,
        key=key,
        orch_target=orch_target or "",
        provisional_task=provisional_task,
        provisional_req_id=provisional_req_id,
        chat_id=chat_id,
        todo_id=todo_id,
        manager_state=manager_state,
        send=send,
        now_iso=now_iso,
        save_manager_state=save_manager_state,
        lifecycle_set_stage=lifecycle_set_stage,
        log_event=log_event,
        record_outcome=record_outcome,
        execute_dispatch_flow=_execute_dispatch_flow,
        selected_roles=selected_dispatch_roles,
        effective_priority=effective_priority,
        effective_timeout=effective_timeout,
        send_planning_detached_notice=helpers.send_planning_detached_notice,
        finalize_provisional_task=helpers.finalize_provisional_task,
        start_background_dispatch_flow=helpers.start_background_dispatch_flow,
        send_dispatch_exception=helpers.send_dispatch_exception,
    )
    if detached_result is not None:
        return bool(detached_result)

    return _execute_dispatch_flow()
