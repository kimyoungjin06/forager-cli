#!/usr/bin/env python3
"""Run and confirmation handler helpers for Telegram gateway."""

import os
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_exec_pipeline import (
    DispatchSyncResult,
    dispatch_and_sync_task as exec_dispatch_and_sync_task,
)
from aoe_tg_run_guards import (
    DispatchPolicyResult,
    EffectiveRunOptions,
    build_dry_run_preview as guard_build_dry_run_preview,
    enforce_dispatch_policies as guard_enforce_dispatch_policies,
    handle_run_rate_limit_and_confirm as guard_handle_run_rate_limit_and_confirm,
    resolve_confirm_run_transition as guard_resolve_confirm_run_transition,
    resolve_effective_run_options as guard_resolve_effective_run_options,
)
from aoe_tg_run_todo_flow import (
    _attach_todo_to_task_and_entry,
    _cleanup_terminal_todo_gate,
    _effective_todo_token,
    _finalize_todo_after_run,
    _find_project_todo_item,
    _find_todo_proposal_row,
    _maybe_capture_todo_proposals,
    _maybe_send_manual_followup_alert,
    _task_label_for_todo,
)
from aoe_tg_run_response_flow import (
    _confirm_required_reply_markup,
    _confirmed_result_reply_markup,
    _early_gate_reply_markup,
    _intervention_reply_markup,
    _rate_limit_reply_markup,
    _send_dispatch_exception,
    _send_dispatch_result,
    _send_exec_critic_intervention,
)
from aoe_tg_run_command_flow import (
    RunCommandFlowHelpers,
    execute_run_command_flow,
)
from aoe_tg_run_detached_flow import maybe_handle_no_wait_dispatch_detach
from aoe_tg_run_dispatch_flow import (
    RunDispatchFlowContext,
    RunDispatchFlowDeps,
    execute_dispatch_flow as execute_run_dispatch_flow,
)
from aoe_tg_run_planning_flow import (
    _finalize_provisional_task,
    _planning_detached_reply_markup,
    _provision_planning_task,
    _send_planning_detached_notice,
    _start_background_dispatch_flow,
    _update_provisional_planning_task,
)
from aoe_tg_run_models import (
    RunContext,
    RunCoreDeps,
    RunDeps,
    RunGuardDeps,
    RunPlanningDeps,
    RunRoutingDeps,
    build_run_context,
    build_run_deps,
)
from aoe_tg_run_retry_scope import filter_phase2_retry_scope as _filter_phase2_retry_scope
from aoe_tg_plan_pipeline import (
    DispatchModeResult,
    PlanMeta,
    apply_plan_and_lineage as plan_apply_plan_and_lineage,
    apply_success_first_prompt_fallbacks as plan_apply_success_first_prompt_fallbacks,
    compute_dispatch_plan as plan_compute_dispatch_plan,
    emit_planning_progress as plan_emit_planning_progress,
    resolve_dispatch_mode_and_roles as plan_resolve_dispatch_mode_and_roles,
)
from aoe_tg_orch_contract import normalize_phase2_execution_plan, normalize_phase2_team_spec
from aoe_tg_orch_roles import classify_dispatch_role_preset


_KNOWN_COMMANDS = [
    "help",
    "status",
    "check",
    "task",
    "monitor",
    "kpi",
    "map",
    "queue",
    "sync",
    "next",
    "fanout",
    "drain",
    "auto",
    "offdesk",
    "panic",
    "todo",
    "room",
    "gc",
    "tf",
    "use",
    "orch",
    "mode",
    "lang",
    "report",
    "replay",
    "ok",
    "whoami",
    "lockme",
    "onlyme",
    "acl",
    "grant",
    "revoke",
    "pick",
    "dispatch",
    "direct",
    "cancel",
    "retry",
    "replan",
    "followup",
    "followup-exec",
    "request",
    "run",
    "clear",
]


def _cmd_prefix() -> str:
    raw = str(os.environ.get("AOE_TG_COMMAND_PREFIXES", "/") or "/").strip()
    for ch in raw:
        if ch in {"/", "!"}:
            return ch
    return "/"


def _suggest_commands(raw_cmd: str, limit: int = 5) -> List[str]:
    token = str(raw_cmd or "").strip().lower()
    if not token:
        return []
    exact = [c for c in _KNOWN_COMMANDS if c == token]
    if exact:
        return exact
    starts = [c for c in _KNOWN_COMMANDS if c.startswith(token)]
    if starts:
        return starts[: max(1, int(limit))]
    contains = [c for c in _KNOWN_COMMANDS if token in c]
    return contains[: max(1, int(limit))]




def _resolve_prompt_or_handle_unknown(
    *,
    cmd: str,
    run_prompt: str,
    rest: str,
    text: str,
    send: Callable[..., bool],
    help_text: Callable[[], str],
) -> Optional[str]:
    p = _cmd_prefix()
    if cmd in {"run", "orch-run"}:
        prompt = run_prompt or rest.strip()
        if not prompt:
            send(
                f"usage: {p}run <prompt> | {p}dispatch <prompt> | {p}direct <prompt> | "
                "aoe run [--direct|--dispatch] [--roles <csv>] [--priority P1|P2|P3] "
                "[--timeout-sec N] [--no-wait] <prompt>",
                context="run usage",
            )
            return None
    elif cmd:
        suggestions = _suggest_commands(cmd)
        sug = ""
        if suggestions:
            sug = "suggest: " + ", ".join(f"{p}{c}" for c in suggestions)
        send(
            "unknown command\n"
            f"- cmd: {p}{cmd}\n"
            + (f"- {sug}\n" if sug else "")
            + f"hint: {p}help (or send just '{p}' for the command menu)",
            context="unknown command",
            with_menu=True,
        )
        return None
    else:
        prompt = text.strip()

    if not prompt:
        send("empty prompt", context="empty prompt")
        return None
    return prompt


def _apply_success_first_prompt_fallbacks(prompt: str) -> tuple[str, List[str]]:
    return plan_apply_success_first_prompt_fallbacks(prompt)


def _handle_run_rate_limit_and_confirm(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    key: str,
    entry: Optional[Dict[str, Any]],
    run_auto_source: str,
    run_force_mode: Optional[str],
    orch_target: Optional[str],
    prompt: str,
    summarize_chat_usage: Callable[[Dict[str, Any], str], tuple[int, int]],
    detect_high_risk_prompt: Callable[[str], str],
    set_confirm_action: Callable[..., None],
    save_manager_state: Callable[..., None],
    send: Callable[..., bool],
    log_event: Callable[..., None],
) -> bool:
    return guard_handle_run_rate_limit_and_confirm(
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
    )


def _resolve_dispatch_mode_and_roles(
    *,
    run_force_mode: Optional[str],
    run_roles_override: Optional[str],
    project_roles_csv: Optional[str],
    auto_dispatch_enabled: bool,
    prompt: str,
    choose_auto_dispatch_roles: Callable[..., List[str]],
    available_roles: List[str],
    team_dir: Any,
) -> DispatchModeResult:
    return plan_resolve_dispatch_mode_and_roles(
        run_force_mode=run_force_mode,
        run_roles_override=run_roles_override,
        project_roles_csv=project_roles_csv,
        auto_dispatch_enabled=auto_dispatch_enabled,
        prompt=prompt,
        choose_auto_dispatch_roles=choose_auto_dispatch_roles,
        available_roles=available_roles,
        team_dir=team_dir,
    )


def _compute_dispatch_plan(
    *,
    args: Any,
    p_args: Any,
    prompt: str,
    request_contract: Optional[Dict[str, Any]] = None,
    dispatch_mode: bool,
    run_control_mode: str,
    run_source_task: Optional[Dict[str, Any]],
    selected_roles: List[str],
    available_roles: List[str],
    available_worker_roles: Callable[[List[str]], List[str]],
    normalize_task_plan_payload: Callable[..., Dict[str, Any]],
    build_task_execution_plan: Callable[..., Dict[str, Any]],
    critique_task_execution_plan: Callable[..., Dict[str, Any]],
    critic_has_blockers: Callable[[Dict[str, Any]], bool],
    repair_task_execution_plan: Callable[..., Dict[str, Any]],
    plan_roles_from_subtasks: Callable[[Optional[Dict[str, Any]]], List[str]],
    phase1_ensemble_planning: Optional[Callable[..., Dict[str, Any]]] = None,
    report_progress: Optional[Callable[..., None]] = None,
) -> PlanMeta:
    return plan_compute_dispatch_plan(
        args=args,
        p_args=p_args,
        prompt=prompt,
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
        phase1_ensemble_planning=phase1_ensemble_planning,
        report_progress=report_progress,
    )


def _emit_planning_progress(
    *,
    phase: str,
    key: str,
    send: Callable[..., bool],
    log_event: Callable[..., None],
    emit_chat: bool,
    request_id: str = "",
    task: Optional[Dict[str, Any]] = None,
    detail: str = "",
    attempt: int = 0,
    total: int = 0,
) -> None:
    plan_emit_planning_progress(
        phase=phase,
        key=key,
        send=send,
        log_event=log_event,
        emit_chat=emit_chat,
        request_id=request_id,
        task=task,
        detail=detail,
        attempt=attempt,
        total=total,
    )


def _dispatch_and_sync_task(
    *,
    p_args: Any,
    dispatch_prompt: str,
    chat_id: str,
    dispatch_roles: str,
    run_priority_override: Optional[str],
    run_timeout_override: Optional[int],
    run_no_wait_override: Optional[bool],
    dispatch_metadata: Optional[Dict[str, Any]],
    key: str,
    entry: Dict[str, Any],
    manager_state: Dict[str, Any],
    prompt: str,
    selected_roles: List[str],
    verifier_roles: List[str],
    require_verifier: bool,
    verifier_candidates: List[str],
    run_aoe_orch: Callable[..., Dict[str, Any]],
    touch_chat_recent_task_ref: Callable[..., None],
    set_chat_selected_task_ref: Callable[..., None],
    now_iso: Callable[[], str],
    sync_task_lifecycle: Callable[..., Optional[Dict[str, Any]]],
    intent_command: str = "",
    intent_action: str = "",
    intent_class: str = "",
    intent_trace: str = "",
) -> DispatchSyncResult:
    return exec_dispatch_and_sync_task(
        p_args=p_args,
        dispatch_prompt=dispatch_prompt,
        chat_id=chat_id,
        dispatch_roles=dispatch_roles,
        run_priority_override=run_priority_override,
        run_timeout_override=run_timeout_override,
        run_no_wait_override=run_no_wait_override,
        dispatch_metadata=dispatch_metadata,
        key=key,
        entry=entry,
        manager_state=manager_state,
        prompt=prompt,
        selected_roles=selected_roles,
        verifier_roles=verifier_roles,
        require_verifier=require_verifier,
        verifier_candidates=verifier_candidates,
        run_aoe_orch=run_aoe_orch,
        touch_chat_recent_task_ref=touch_chat_recent_task_ref,
        set_chat_selected_task_ref=set_chat_selected_task_ref,
        now_iso=now_iso,
        sync_task_lifecycle=sync_task_lifecycle,
        intent_command=intent_command,
        intent_action=intent_action,
        intent_class=intent_class,
        intent_trace=intent_trace,
    )


def _apply_plan_and_lineage(
    *,
    task: Optional[Dict[str, Any]],
    plan_data: Optional[Dict[str, Any]],
    plan_critic: Dict[str, Any],
    plan_roles: List[str],
    plan_replans: List[Dict[str, Any]],
    plan_error: str,
    plan_gate_blocked: bool,
    plan_gate_reason: str,
    plan_review_count: int = 0,
    plan_issue_codes: Optional[List[str]] = None,
    plan_issue_history: Optional[List[Dict[str, Any]]] = None,
    plan_convergence_status: str = "",
    plan_stalled_reason: str = "",
    plan_last_round: int = 0,
    phase1_mode: str = "",
    phase1_rounds: int = 0,
    phase1_providers: Optional[List[str]] = None,
    phase1_planner_providers: Optional[List[str]] = None,
    phase1_critic_providers: Optional[List[str]] = None,
    phase1_role_preset: str = "",
    phase2_team_preset: str = "",
    critic_has_blockers: Callable[[Dict[str, Any]], bool],
    lifecycle_set_stage: Callable[..., None],
    run_control_mode: str,
    run_source_request_id: str,
    run_source_task: Optional[Dict[str, Any]],
    req_id: str,
    now_iso: Callable[[], str],
) -> None:
    plan_apply_plan_and_lineage(
        task=task,
        plan_data=plan_data,
        plan_critic=plan_critic,
        plan_roles=plan_roles,
        plan_replans=plan_replans,
        plan_error=plan_error,
        plan_gate_blocked=plan_gate_blocked,
        plan_gate_reason=plan_gate_reason,
        plan_review_count=plan_review_count,
        plan_issue_codes=plan_issue_codes,
        plan_issue_history=plan_issue_history,
        plan_convergence_status=plan_convergence_status,
        plan_stalled_reason=plan_stalled_reason,
        plan_last_round=plan_last_round,
        phase1_mode=phase1_mode,
        phase1_rounds=phase1_rounds,
        phase1_providers=phase1_providers,
        phase1_planner_providers=phase1_planner_providers,
        phase1_critic_providers=phase1_critic_providers,
        phase1_role_preset=phase1_role_preset,
        phase2_team_preset=phase2_team_preset,
        critic_has_blockers=critic_has_blockers,
        lifecycle_set_stage=lifecycle_set_stage,
        run_control_mode=run_control_mode,
        run_source_request_id=run_source_request_id,
        run_source_task=run_source_task,
        req_id=req_id,
        now_iso=now_iso,
    )


def _enforce_dispatch_policies(
    *,
    dispatch_mode: bool,
    args: Any,
    key: str,
    entry: Dict[str, Any],
    selected_roles: List[str],
    available_roles: List[str],
    verifier_candidates: List[str],
    plan_gate_blocked: bool,
    plan_gate_reason: str,
    plan_replans: List[Dict[str, Any]],
    ensure_verifier_roles: Callable[..., tuple[List[str], List[str], bool, List[str]]],
    dispatch_roles: str,
    send: Callable[..., bool],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> DispatchPolicyResult:
    return guard_enforce_dispatch_policies(
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
        ensure_verifier_roles=ensure_verifier_roles,
        dispatch_roles=dispatch_roles,
        send=send,
        record_outcome=record_outcome,
    )


def _resolve_effective_run_options(
    *,
    p_args: Any,
    run_priority_override: Optional[str],
    run_timeout_override: Optional[int],
    run_no_wait_override: Optional[bool],
) -> EffectiveRunOptions:
    return guard_resolve_effective_run_options(
        p_args=p_args,
        run_priority_override=run_priority_override,
        run_timeout_override=run_timeout_override,
        run_no_wait_override=run_no_wait_override,
    )


def _build_dry_run_preview(
    *,
    key: str,
    dispatch_mode: bool,
    prompt: str,
    dispatch_roles: str,
    require_verifier: bool,
    verifier_roles: List[str],
    verifier_added: bool,
    run_control_mode: str,
    run_source_request_id: str,
    planning_enabled: bool,
    reuse_source_plan: bool,
    plan_data: Optional[Dict[str, Any]],
    plan_replans: List[Dict[str, Any]],
    plan_gate_blocked: bool,
    plan_error: str,
    effective_priority: str,
    effective_timeout: int,
    effective_no_wait: bool,
) -> str:
    return guard_build_dry_run_preview(
        key=key,
        dispatch_mode=dispatch_mode,
        prompt=prompt,
        dispatch_roles=dispatch_roles,
        require_verifier=require_verifier,
        verifier_roles=verifier_roles,
        verifier_added=verifier_added,
        run_control_mode=run_control_mode,
        run_source_request_id=run_source_request_id,
        planning_enabled=planning_enabled,
        reuse_source_plan=reuse_source_plan,
        plan_data=plan_data,
        plan_replans=plan_replans,
        plan_gate_blocked=plan_gate_blocked,
        plan_error=plan_error,
        effective_priority=effective_priority,
        effective_timeout=effective_timeout,
        effective_no_wait=effective_no_wait,
    )


def resolve_confirm_run_transition(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    orch_target: Optional[str],
    send: Callable[..., bool],
    get_confirm_action: Callable[[Dict[str, Any], str], Dict[str, Any]],
    parse_iso_ts: Callable[[str], Optional[Any]],
    clear_confirm_action: Callable[[Dict[str, Any], str], bool],
    save_manager_state: Callable[..., None],
) -> Optional[Dict[str, Any]]:
    return guard_resolve_confirm_run_transition(
        cmd=cmd,
        args=args,
        manager_state=manager_state,
        chat_id=chat_id,
        orch_target=orch_target,
        send=send,
        get_confirm_action=get_confirm_action,
        parse_iso_ts=parse_iso_ts,
        clear_confirm_action=clear_confirm_action,
        save_manager_state=save_manager_state,
    )


def handle_run_or_unknown_command(
    *,
    ctx: RunContext,
    deps: RunDeps,
) -> bool:
    return execute_run_command_flow(
        ctx=ctx,
        deps=deps,
        helpers=RunCommandFlowHelpers(
            resolve_prompt_or_handle_unknown=_resolve_prompt_or_handle_unknown,
            apply_success_first_prompt_fallbacks=_apply_success_first_prompt_fallbacks,
            handle_run_rate_limit_and_confirm=_handle_run_rate_limit_and_confirm,
            resolve_dispatch_mode_and_roles=_resolve_dispatch_mode_and_roles,
            resolve_effective_run_options=_resolve_effective_run_options,
            compute_dispatch_plan=_compute_dispatch_plan,
            emit_planning_progress=_emit_planning_progress,
            enforce_dispatch_policies=_enforce_dispatch_policies,
            build_dry_run_preview=_build_dry_run_preview,
            dispatch_and_sync_task=_dispatch_and_sync_task,
            apply_plan_and_lineage=_apply_plan_and_lineage,
            filter_phase2_retry_scope=_filter_phase2_retry_scope,
            provision_planning_task=_provision_planning_task,
            finalize_provisional_task=_finalize_provisional_task,
            update_provisional_planning_task=_update_provisional_planning_task,
            effective_todo_token=_effective_todo_token,
            cleanup_terminal_todo_gate=_cleanup_terminal_todo_gate,
            maybe_send_manual_followup_alert=_maybe_send_manual_followup_alert,
            maybe_capture_todo_proposals=_maybe_capture_todo_proposals,
            attach_todo_to_task_and_entry=_attach_todo_to_task_and_entry,
            finalize_todo_after_run=_finalize_todo_after_run,
            send_dispatch_exception=_send_dispatch_exception,
            send_exec_critic_intervention=_send_exec_critic_intervention,
            send_dispatch_result=_send_dispatch_result,
            send_planning_detached_notice=_send_planning_detached_notice,
            start_background_dispatch_flow=_start_background_dispatch_flow,
            maybe_handle_no_wait_dispatch_detach=maybe_handle_no_wait_dispatch_detach,
            execute_dispatch_flow=execute_run_dispatch_flow,
        ),
    )
