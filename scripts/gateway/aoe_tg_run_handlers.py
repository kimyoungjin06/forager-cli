#!/usr/bin/env python3
"""Run and confirmation handler helpers for Telegram gateway."""

import copy
import os
from dataclasses import dataclass, field
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
    "request",
    "run",
    "clear",
]


def _dedupe_role_tokens(rows: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for row in rows:
        token = str(row or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _lane_id_token(row: Dict[str, Any]) -> str:
    return str(row.get("lane_id", row.get("group_id", "")) or "").strip()[:32]


def _should_filter_retry_phase2_plan(
    *,
    run_control_mode: str,
    run_source_task: Optional[Dict[str, Any]],
    retry_critic: Optional[Dict[str, Any]] = None,
    selected_execution_lane_ids: Optional[List[str]] = None,
    selected_review_lane_ids: Optional[List[str]] = None,
) -> bool:
    if selected_execution_lane_ids or selected_review_lane_ids:
        return bool(run_control_mode in {"retry", "replan"} and isinstance(run_source_task, dict))
    if run_control_mode != "retry" or not isinstance(run_source_task, dict):
        return bool(
            run_control_mode == "retry"
            and isinstance(retry_critic, dict)
            and str(retry_critic.get("verdict", "")).strip().lower() == "retry"
            and str(retry_critic.get("action", "")).strip().lower() != "replan"
        )
    critic = retry_critic if isinstance(retry_critic, dict) else run_source_task.get("exec_critic")
    if not isinstance(critic, dict):
        return False
    verdict = str(critic.get("verdict", "")).strip().lower()
    action = str(critic.get("action", "")).strip().lower()
    return verdict == "retry" and action != "replan"


def _filter_phase2_retry_scope(
    *,
    plan_data: Optional[Dict[str, Any]],
    run_control_mode: str,
    run_source_task: Optional[Dict[str, Any]],
    retry_critic: Optional[Dict[str, Any]] = None,
    selected_execution_lane_ids: Optional[List[str]] = None,
    selected_review_lane_ids: Optional[List[str]] = None,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not _should_filter_retry_phase2_plan(
        run_control_mode=run_control_mode,
        run_source_task=run_source_task,
        retry_critic=retry_critic,
        selected_execution_lane_ids=selected_execution_lane_ids,
        selected_review_lane_ids=selected_review_lane_ids,
    ):
        return plan_data, {}
    if not isinstance(plan_data, dict):
        return plan_data, {}

    critic = retry_critic if isinstance(retry_critic, dict) else (
        run_source_task.get("exec_critic") if isinstance(run_source_task, dict) else {}
    )
    meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    if not exec_plan:
        return plan_data, {}

    execution_rows = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    review_rows = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []
    if not execution_rows:
        return plan_data, {}

    has_operator_lane_selector = bool(selected_execution_lane_ids or selected_review_lane_ids)
    target_exec_source = selected_execution_lane_ids if has_operator_lane_selector else (critic.get("rerun_execution_lane_ids") or [])
    target_review_source = selected_review_lane_ids if has_operator_lane_selector else (critic.get("rerun_review_lane_ids") or [])
    target_exec_ids = {
        str(item).strip()[:32]
        for item in (target_exec_source or [])
        if str(item).strip()
    }
    target_review_ids = {
        str(item).strip()[:32]
        for item in (target_review_source or [])
        if str(item).strip()
    }

    if target_review_ids and not target_exec_ids:
        for row in review_rows:
            if not isinstance(row, dict):
                continue
            if _lane_id_token(row) not in target_review_ids:
                continue
            for lane_id in (row.get("depends_on") or []):
                token = str(lane_id).strip()[:32]
                if token:
                    target_exec_ids.add(token)

    filtered_execution = [
        copy.deepcopy(row)
        for row in execution_rows
        if isinstance(row, dict) and (not target_exec_ids or _lane_id_token(row) in target_exec_ids)
    ]
    if not filtered_execution:
        return plan_data, {}

    selected_exec_ids = {
        _lane_id_token(row) for row in filtered_execution if isinstance(row, dict) and _lane_id_token(row)
    }
    selected_subtask_ids = {
        str(item).strip()[:32]
        for row in filtered_execution
        if isinstance(row, dict)
        for item in (row.get("subtask_ids") or [])
        if str(item).strip()
    }

    filtered_review: List[Dict[str, Any]] = []
    for row in review_rows:
        if not isinstance(row, dict):
            continue
        lane_id = _lane_id_token(row)
        depends_on = {
            str(item).strip()[:32]
            for item in (row.get("depends_on") or [])
            if str(item).strip()
        }
        if target_review_ids:
            if lane_id in target_review_ids:
                filtered_review.append(copy.deepcopy(row))
            continue
        if not depends_on or depends_on.intersection(selected_exec_ids):
            filtered_review.append(copy.deepcopy(row))

    execution_roles = _dedupe_role_tokens(
        [str(row.get("role", "")).strip() for row in filtered_execution if isinstance(row, dict)]
    )
    review_roles = _dedupe_role_tokens(
        [str(row.get("role", "")).strip() for row in filtered_review if isinstance(row, dict)]
    )
    planned_roles = _dedupe_role_tokens(execution_roles + review_roles)
    if not planned_roles:
        return plan_data, {}

    filtered_plan = copy.deepcopy(plan_data)
    if isinstance(filtered_plan.get("subtasks"), list):
        filtered_plan["subtasks"] = [
            row
            for row in filtered_plan["subtasks"]
            if isinstance(row, dict)
            and (
                (selected_subtask_ids and str(row.get("id", "")).strip()[:32] in selected_subtask_ids)
                or (not selected_subtask_ids and str(row.get("owner_role", "")).strip() in planned_roles)
            )
        ]
    if isinstance(filtered_plan.get("assignments"), list):
        filtered_plan["assignments"] = [
            row
            for row in filtered_plan["assignments"]
            if isinstance(row, dict)
            and (
                (selected_subtask_ids and str(row.get("subtask_id", row.get("id", ""))).strip()[:32] in selected_subtask_ids)
                or str(row.get("role", "")).strip() in planned_roles
            )
        ]
    if isinstance(filtered_plan.get("execution_order"), list):
        filtered_plan["execution_order"] = [
            str(role).strip()
            for role in filtered_plan["execution_order"]
            if str(role).strip() in planned_roles
        ]

    meta_out = filtered_plan.get("meta") if isinstance(filtered_plan.get("meta"), dict) else {}
    team_spec = meta_out.get("phase2_team_spec") if isinstance(meta_out.get("phase2_team_spec"), dict) else {}
    exec_groups = team_spec.get("execution_groups") if isinstance(team_spec.get("execution_groups"), list) else []
    review_groups = team_spec.get("review_groups") if isinstance(team_spec.get("review_groups"), list) else []
    filtered_exec_groups = [
        copy.deepcopy(row)
        for row in exec_groups
        if isinstance(row, dict) and _lane_id_token(row) in selected_exec_ids
    ]
    filtered_review_groups = [
        copy.deepcopy(row)
        for row in review_groups
        if isinstance(row, dict)
        and (
            _lane_id_token(row) in { _lane_id_token(item) for item in filtered_review if isinstance(item, dict) }
            or not str(row.get("group_id", "")).strip()
        )
    ]
    filtered_team_spec = normalize_phase2_team_spec(
        {
            **team_spec,
            "execution_groups": filtered_exec_groups,
            "review_groups": filtered_review_groups,
            "team_roles": planned_roles,
        },
        plan=filtered_plan,
        roles=planned_roles,
        verifier_roles=review_roles,
        require_verifier=bool(review_roles),
    )
    filtered_exec_plan = normalize_phase2_execution_plan(
        {
            **exec_plan,
            "execution_lanes": filtered_execution,
            "review_lanes": filtered_review,
            "execution_mode": "parallel" if len(filtered_execution) > 1 else "single",
            "review_mode": (
                "parallel"
                if len(filtered_review) > 1
                else ("single" if filtered_review else "skip")
            ),
            "parallel_workers": len(filtered_execution) > 1,
            "parallel_reviews": len(filtered_review) > 1,
        },
        team_spec=filtered_team_spec,
        readonly=bool(exec_plan.get("readonly", True)),
    )
    meta_out["phase2_team_spec"] = filtered_team_spec
    meta_out["phase2_execution_plan"] = filtered_exec_plan
    filtered_plan["meta"] = meta_out
    return filtered_plan, {
        "rerun_execution_lane_ids": sorted(selected_exec_ids),
        "rerun_review_lane_ids": [
            _lane_id_token(row) for row in filtered_review if isinstance(row, dict) and _lane_id_token(row)
        ],
        "execution_roles": execution_roles,
        "review_roles": review_roles,
        "planned_roles": planned_roles,
        "subtask_ids": sorted(selected_subtask_ids),
    }


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


@dataclass
class RunContext:
    cmd: str
    args: Any
    manager_state: Dict[str, Any]
    chat_id: str
    text: str
    rest: str
    orch_target: Optional[str]
    run_prompt: str
    run_roles_override: Optional[str]
    run_priority_override: Optional[str]
    run_timeout_override: Optional[int]
    run_no_wait_override: Optional[bool]
    run_force_mode: Optional[str]
    run_auto_source: str
    run_control_mode: str
    run_source_request_id: str
    run_intent_command: str = ""
    run_intent_action: str = ""
    run_intent_class: str = ""
    run_intent_trace: str = ""
    run_source_task: Optional[Dict[str, Any]] = None
    run_selected_execution_lane_ids: Optional[List[str]] = None
    run_selected_review_lane_ids: Optional[List[str]] = None


@dataclass
class RunCoreDeps:
    send: Callable[..., bool]
    log_event: Callable[..., None]
    help_text: Callable[[], str]
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None


@dataclass
class RunGuardDeps:
    summarize_chat_usage: Callable[[Dict[str, Any], str], tuple[int, int]]
    detect_high_risk_prompt: Callable[[str], str]
    set_confirm_action: Callable[..., None]
    save_manager_state: Callable[..., None]


@dataclass
class RunPlanningDeps:
    choose_auto_dispatch_roles: Callable[..., List[str]]
    resolve_verifier_candidates: Callable[[Optional[str]], List[str]]
    load_orchestrator_roles: Callable[[Any], List[str]]
    parse_roles_csv: Callable[[Optional[str]], List[str]]
    ensure_verifier_roles: Callable[..., tuple[List[str], List[str], bool, List[str]]]
    available_worker_roles: Callable[[List[str]], List[str]]
    normalize_task_plan_payload: Callable[..., Dict[str, Any]]
    build_task_execution_plan: Callable[..., Dict[str, Any]]
    critique_task_execution_plan: Callable[..., Dict[str, Any]]
    critic_has_blockers: Callable[[Dict[str, Any]], bool]
    repair_task_execution_plan: Callable[..., Dict[str, Any]]
    plan_roles_from_subtasks: Callable[[Optional[Dict[str, Any]]], List[str]]
    build_planned_dispatch_prompt: Callable[[str, Dict[str, Any], Dict[str, Any]], str]
    phase1_ensemble_planning: Callable[..., Dict[str, Any]]


@dataclass
class RunRoutingDeps:
    get_context: Callable[[Optional[str]], tuple[str, Dict[str, Any], Any]]
    run_orchestrator_direct: Callable[[Any, str], str]
    run_aoe_orch: Callable[..., Dict[str, Any]]
    create_request_id: Callable[[], str]
    ensure_task_record: Callable[..., Dict[str, Any]]
    finalize_request_reply_messages: Callable[..., Dict[str, Any]]
    touch_chat_recent_task_ref: Callable[..., None]
    set_chat_selected_task_ref: Callable[..., None]
    now_iso: Callable[[], str]
    sync_task_lifecycle: Callable[..., Optional[Dict[str, Any]]]
    lifecycle_set_stage: Callable[..., None]
    summarize_task_lifecycle: Callable[[str, Dict[str, Any]], str]
    synthesize_orchestrator_response: Callable[[Any, str, Dict[str, Any]], str]
    critique_task_result: Callable[..., Dict[str, Any]]
    extract_todo_proposals: Callable[..., List[Dict[str, Any]]]
    merge_todo_proposals: Callable[..., Dict[str, Any]]
    render_run_response: Callable[..., str]


@dataclass
class RunDeps:
    core: RunCoreDeps
    guard: RunGuardDeps
    planning: RunPlanningDeps
    routing: RunRoutingDeps


def build_run_context(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    text: str,
    rest: str,
    orch_target: Optional[str],
    run_prompt: str,
    run_roles_override: Optional[str],
    run_priority_override: Optional[str],
    run_timeout_override: Optional[int],
    run_no_wait_override: Optional[bool],
    run_force_mode: Optional[str],
    run_auto_source: str,
    run_control_mode: str,
    run_source_request_id: str,
    run_intent_command: str = "",
    run_intent_action: str = "",
    run_intent_class: str = "",
    run_intent_trace: str = "",
    run_source_task: Optional[Dict[str, Any]] = None,
    run_selected_execution_lane_ids: Optional[List[str]] = None,
    run_selected_review_lane_ids: Optional[List[str]] = None,
) -> RunContext:
    return RunContext(
        cmd=cmd,
        args=args,
        manager_state=manager_state,
        chat_id=chat_id,
        text=text,
        rest=rest,
        orch_target=orch_target,
        run_prompt=run_prompt,
        run_roles_override=run_roles_override,
        run_priority_override=run_priority_override,
        run_timeout_override=run_timeout_override,
        run_no_wait_override=run_no_wait_override,
        run_force_mode=run_force_mode,
        run_auto_source=run_auto_source,
        run_control_mode=run_control_mode,
        run_source_request_id=run_source_request_id,
        run_intent_command=str(run_intent_command or "").strip(),
        run_intent_action=str(run_intent_action or "").strip(),
        run_intent_class=str(run_intent_class or "").strip(),
        run_intent_trace=str(run_intent_trace or "").strip(),
        run_source_task=run_source_task,
        run_selected_execution_lane_ids=run_selected_execution_lane_ids,
        run_selected_review_lane_ids=run_selected_review_lane_ids,
    )


def build_run_deps(
    *,
    send: Callable[..., bool],
    log_event: Callable[..., None],
    help_text: Callable[[], str],
    summarize_chat_usage: Callable[[Dict[str, Any], str], tuple[int, int]],
    detect_high_risk_prompt: Callable[[str], str],
    set_confirm_action: Callable[..., None],
    save_manager_state: Callable[..., None],
    get_context: Callable[[Optional[str]], tuple[str, Dict[str, Any], Any]],
    choose_auto_dispatch_roles: Callable[..., List[str]],
    resolve_verifier_candidates: Callable[[Optional[str]], List[str]],
    load_orchestrator_roles: Callable[[Any], List[str]],
    parse_roles_csv: Callable[[Optional[str]], List[str]],
    ensure_verifier_roles: Callable[..., tuple[List[str], List[str], bool, List[str]]],
    available_worker_roles: Callable[[List[str]], List[str]],
    normalize_task_plan_payload: Callable[..., Dict[str, Any]],
    build_task_execution_plan: Callable[..., Dict[str, Any]],
    critique_task_execution_plan: Callable[..., Dict[str, Any]],
    critic_has_blockers: Callable[[Dict[str, Any]], bool],
    repair_task_execution_plan: Callable[..., Dict[str, Any]],
    plan_roles_from_subtasks: Callable[[Optional[Dict[str, Any]]], List[str]],
    build_planned_dispatch_prompt: Callable[[str, Dict[str, Any], Dict[str, Any]], str],
    phase1_ensemble_planning: Callable[..., Dict[str, Any]],
    run_orchestrator_direct: Callable[[Any, str], str],
    run_aoe_orch: Callable[..., Dict[str, Any]],
    create_request_id: Callable[[], str],
    ensure_task_record: Callable[..., Dict[str, Any]],
    finalize_request_reply_messages: Callable[..., Dict[str, Any]],
    touch_chat_recent_task_ref: Callable[..., None],
    set_chat_selected_task_ref: Callable[..., None],
    now_iso: Callable[[], str],
    sync_task_lifecycle: Callable[..., Optional[Dict[str, Any]]],
    lifecycle_set_stage: Callable[..., None],
    summarize_task_lifecycle: Callable[[str, Dict[str, Any]], str],
    synthesize_orchestrator_response: Callable[[Any, str, Dict[str, Any]], str],
    critique_task_result: Callable[..., Dict[str, Any]],
    extract_todo_proposals: Callable[..., List[Dict[str, Any]]],
    merge_todo_proposals: Callable[..., Dict[str, Any]],
    render_run_response: Callable[..., str],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> RunDeps:
    return RunDeps(
        core=RunCoreDeps(
            send=send,
            log_event=log_event,
            help_text=help_text,
            record_outcome=record_outcome,
        ),
        guard=RunGuardDeps(
            summarize_chat_usage=summarize_chat_usage,
            detect_high_risk_prompt=detect_high_risk_prompt,
            set_confirm_action=set_confirm_action,
            save_manager_state=save_manager_state,
        ),
        planning=RunPlanningDeps(
            choose_auto_dispatch_roles=choose_auto_dispatch_roles,
            resolve_verifier_candidates=resolve_verifier_candidates,
            load_orchestrator_roles=load_orchestrator_roles,
            parse_roles_csv=parse_roles_csv,
            ensure_verifier_roles=ensure_verifier_roles,
            available_worker_roles=available_worker_roles,
            normalize_task_plan_payload=normalize_task_plan_payload,
            build_task_execution_plan=build_task_execution_plan,
            critique_task_execution_plan=critique_task_execution_plan,
            critic_has_blockers=critic_has_blockers,
            repair_task_execution_plan=repair_task_execution_plan,
            plan_roles_from_subtasks=plan_roles_from_subtasks,
            build_planned_dispatch_prompt=build_planned_dispatch_prompt,
            phase1_ensemble_planning=phase1_ensemble_planning,
        ),
        routing=RunRoutingDeps(
            get_context=get_context,
            run_orchestrator_direct=run_orchestrator_direct,
            run_aoe_orch=run_aoe_orch,
            create_request_id=create_request_id,
            ensure_task_record=ensure_task_record,
            finalize_request_reply_messages=finalize_request_reply_messages,
            touch_chat_recent_task_ref=touch_chat_recent_task_ref,
            set_chat_selected_task_ref=set_chat_selected_task_ref,
            now_iso=now_iso,
            sync_task_lifecycle=sync_task_lifecycle,
            lifecycle_set_stage=lifecycle_set_stage,
            summarize_task_lifecycle=summarize_task_lifecycle,
            synthesize_orchestrator_response=synthesize_orchestrator_response,
            critique_task_result=critique_task_result,
            extract_todo_proposals=extract_todo_proposals,
            merge_todo_proposals=merge_todo_proposals,
            render_run_response=render_run_response,
        ),
    )


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
    phase1_mode: str = "",
    phase1_rounds: int = 0,
    phase1_providers: Optional[List[str]] = None,
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
        phase1_mode=phase1_mode,
        phase1_rounds=phase1_rounds,
        phase1_providers=phase1_providers,
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

    prompt = _resolve_prompt_or_handle_unknown(
        cmd=cmd,
        run_prompt=run_prompt,
        rest=rest,
        text=text,
        send=send,
        help_text=help_text,
    )
    if prompt is None:
        return True

    # Resolve project context early to allow todo linkage (and future policies).
    key, entry, p_args = get_context(orch_target)
    setattr(p_args, "_aoe_control_mode", str(run_control_mode or "").strip().lower())
    setattr(p_args, "_aoe_source_request_id", str(run_source_request_id or "").strip())

    prompt, fallback_notes = _apply_success_first_prompt_fallbacks(prompt)
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

    if _handle_run_rate_limit_and_confirm(
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
    dispatch_meta = _resolve_dispatch_mode_and_roles(
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

    effective = _resolve_effective_run_options(
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
    selected_role_preset = classify_dispatch_role_preset(prompt, selected_roles=selected_dispatch_roles)
    provisional_req_id = ""
    provisional_task: Optional[Dict[str, Any]] = None
    if dispatch_mode and planning_requested and (not args.dry_run):
        provisional_req_id, provisional_task = _provision_planning_task(
            entry=entry,
            manager_state=manager_state,
            chat_id=chat_id,
            key=key,
            prompt=prompt,
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
        )
        save_manager_state(args.manager_state_file, manager_state)

    if args.dry_run:
        selected_roles = list(selected_dispatch_roles)
        plan_meta = _compute_dispatch_plan(
            args=args,
            p_args=p_args,
            prompt=prompt,
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
        plan_critic = plan_meta.plan_critic or {"approved": True, "issues": [], "recommendations": []}
        plan_replans = list(plan_meta.plan_replans or [])
        plan_error = str(plan_meta.plan_error or "")
        plan_gate_blocked = bool(plan_meta.plan_gate_blocked)
        planning_enabled = bool(plan_meta.planning_enabled)
        reuse_source_plan = bool(plan_meta.reuse_source_plan)

        policy = _enforce_dispatch_policies(
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
                effective_todo_id = _effective_todo_token(
                    entry=entry,
                    chat_id=chat_id,
                    todo_id=todo_id,
                    run_auto_source=run_auto_source,
                )
                _cleanup_terminal_todo_gate(
                    entry=entry,
                    chat_id=chat_id,
                    todo_id=todo_id,
                    pending_todo_used=pending_todo_used,
                    run_auto_source=run_auto_source,
                    reason=str(policy.terminal_reason or "dispatch policy blocked").strip(),
                    now_iso=now_iso,
                )
                _maybe_send_manual_followup_alert(
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

        preview = _build_dry_run_preview(
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
        return execute_run_dispatch_flow(
            ctx=RunDispatchFlowContext(
                args=args,
                p_args=p_args,
                key=key,
                entry=entry,
                manager_state=manager_state,
                chat_id=chat_id,
                prompt=prompt,
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
                compute_dispatch_plan=_compute_dispatch_plan,
                emit_planning_progress=_emit_planning_progress,
                dispatch_and_sync_task=_dispatch_and_sync_task,
                apply_plan_and_lineage=_apply_plan_and_lineage,
                enforce_dispatch_policies=_enforce_dispatch_policies,
                filter_phase2_retry_scope=_filter_phase2_retry_scope,
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
            ),
        )

    detached_result = maybe_handle_no_wait_dispatch_detach(
        dispatch_mode=dispatch_mode,
        planning_requested=planning_requested,
        effective_no_wait=effective_no_wait,
        args=args,
        entry=entry,
        key=key,
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
        send_planning_detached_notice=_send_planning_detached_notice,
        finalize_provisional_task=_finalize_provisional_task,
        start_background_dispatch_flow=_start_background_dispatch_flow,
        send_dispatch_exception=_send_dispatch_exception,
    )
    if detached_result is not None:
        return bool(detached_result)


    return _execute_dispatch_flow()
