#!/usr/bin/env python3
"""Retry execution bridge for dashboard mutation actions."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import aoe_tg_chat_state as chat_state
import aoe_tg_retry_handlers as retry_handlers
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view

from control_dashboard_action_exec_shared import (
    _DASHBOARD_CHAT_ID,
    _build_dashboard_retry_run_deps,
    _dashboard_action_args,
    _dashboard_get_context_factory,
    _dashboard_run_args,
    _find_task_project_key,
    _json,
    _latest_recorded_outcome,
    _load_dashboard_manager_state,
    _make_send_collector,
    _missing_outcome_response,
)
from control_dashboard_common import DashboardAppConfig, _not_found_json

_RETRY_BLOCKED_REMEDIATIONS = {
    "planning-gate": "inspect planning critic issues and approval blockers in /task and /offdesk review before retrying again",
    "dispatch-exception": "inspect dispatch exception output and backend notes in the task detail before attempting another retry",
    "exec-critic": "inspect exec critic verdict and lane rerun targets in /task before retrying again",
    "verifier-gate failed": "inspect verifier findings and required verifier roles in /task before retrying again",
    "run usage": "inspect the retry command payload and lane selection before retrying again",
    "unknown command": "inspect the retry action contract and command mapping before retrying again",
    "empty prompt": "inspect the source task prompt in the runtime lifecycle before retrying again",
}


def _retry_blocked_remediation_for_reason(reason_code: str, detail: str = "") -> str:
    token = str(reason_code or "").strip().lower().replace("-", "_")
    if token == "planning_gate":
        return _RETRY_BLOCKED_REMEDIATIONS["planning-gate"]
    if token == "dispatch_exception":
        return _RETRY_BLOCKED_REMEDIATIONS["dispatch-exception"]
    if token == "exec_critic":
        return _RETRY_BLOCKED_REMEDIATIONS["exec-critic"]
    if token == "verifier_gate_failed":
        return _RETRY_BLOCKED_REMEDIATIONS["verifier-gate failed"]
    if token == "verifier_gate_setup":
        return "assign or enable the required verifier role before retrying the runtime again"
    return str(detail or "").strip() or "inspect the task and runtime state before retrying again"



def _retry_blocked_remediation(contexts: List[str]) -> str:
    for context in contexts:
        token = str(context or "").strip()
        if token in _RETRY_BLOCKED_REMEDIATIONS:
            return _RETRY_BLOCKED_REMEDIATIONS[token]
    return "inspect planning or critic blockers in /offdesk review before re-running retry"



def _execute_retry_run_transition(
    transition: Dict[str, Any],
    *,
    config: DashboardAppConfig,
    manager_state: Dict[str, Any],
    paths: Any,
    source_command: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes]:
    import aoe_tg_run_handlers as run_handlers

    messages: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    source_task = transition.get("run_source_task") if isinstance(transition.get("run_source_task"), dict) else None
    args = _dashboard_run_args(config, source_task=source_task)
    ctx = run_handlers.build_run_context(
        cmd=str(transition.get("cmd", "run")).strip() or "run",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        text=str(transition.get("run_prompt", "")).strip(),
        rest=str(transition.get("rest", "")).strip(),
        orch_target=str(transition.get("orch_target", "")).strip() or None,
        run_prompt=str(transition.get("run_prompt", "")).strip(),
        run_roles_override=transition.get("run_roles_override"),
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=transition.get("run_no_wait_override"),
        run_force_mode=str(transition.get("run_force_mode", "")).strip() or None,
        run_auto_source="dashboard_retry",
        run_control_mode=str(transition.get("run_control_mode", "")).strip(),
        run_source_request_id=str(transition.get("run_source_request_id", "")).strip(),
        run_source_task=source_task,
        run_selected_execution_lane_ids=list(transition.get("run_selected_execution_lane_ids") or []),
        run_selected_review_lane_ids=list(transition.get("run_selected_review_lane_ids") or []),
    )
    deps = _build_dashboard_retry_run_deps(
        config=config,
        manager_state=manager_state,
        paths=paths,
        messages=messages,
        events=events,
        outcomes=outcomes,
    )
    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)
    if not handled:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "unhandled",
                "path": "/control/actions/task/retry",
                "source_command": source_command,
                "payload": payload,
                "messages": messages,
                "events": events,
                "remediation": "inspect the runtime task detail and retry contract before attempting another retry bridge",
            },
            status=500,
        )

    project_key = str(transition.get("orch_target", "")).strip()
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else {}
    executed_request_id = str(entry.get("last_request_id", "")).strip() if isinstance(entry, dict) else ""
    executed_task = gateway_task_state.get_task_record(entry, executed_request_id) if isinstance(entry, dict) and executed_request_id else None
    outcome = _latest_recorded_outcome(outcomes, kind="retry_run")
    if not outcome:
        return _missing_outcome_response(
            path="/control/actions/task/retry",
            source_command=source_command,
            payload=payload,
            kind="retry_run",
            messages=messages,
            events=events,
            remediation="inspect the retry handler contract; dashboard actions now require structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"
    task_payload = None
    if isinstance(executed_task, dict) and executed_request_id:
        task_payload = {
            "request_id": executed_request_id,
            "label": gateway_task_view.task_display_label(executed_task, fallback_request_id=executed_request_id),
            "status": str(executed_task.get("status", "")).strip() or "-",
            "tf_phase": str(executed_task.get("tf_phase", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{executed_request_id}",
        }
    next_step = str(outcome.get("next_step", "")).strip() or ("/offdesk review" if blocked else (f"/task {task_payload['label']}" if isinstance(task_payload, dict) else "/monitor"))
    remediation = "review the updated task detail and lane state before repeating another retry" if not blocked else "inspect the structured retry outcome and planning contract before repeating another retry"
    reason_code = str(outcome.get("reason_code", "")).strip() or "-"
    detail_note = str(outcome.get("detail", "")).strip()
    if blocked:
        remediation = _retry_blocked_remediation_for_reason(reason_code, detail_note)
    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": True,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": "/control/actions/task/retry",
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
            },
            "messages": messages,
            "events": events,
            "outcome": {
                "kind": "retry_run",
                "status": "blocked" if blocked else "executed",
                "reason_code": reason_code,
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "task": task_payload,
            "next_step": next_step,
            "remediation": remediation,
        },
        status=409 if blocked else 200,
    )



def _execute_retry_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    paths, manager_state = _load_dashboard_manager_state(config)
    project_key = _find_task_project_key(manager_state, task_ref)
    if not project_key:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")

    messages: List[Dict[str, Any]] = []
    transition = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-retry",
        args=_dashboard_action_args(config),
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        orch_target=project_key,
        orch_retry_request_id=task_ref,
        orch_replan_request_id=None,
        orch_retry_lane_ids=list(payload.get("lane_ids") or []),
        orch_replan_lane_ids=None,
        send=_make_send_collector(messages),
        get_context=_dashboard_get_context_factory(manager_state, paths),
        get_chat_selected_task_ref=chat_state.get_chat_selected_task_ref,
        resolve_chat_task_ref=chat_state.resolve_chat_task_ref,
        resolve_task_request_id=gateway_task_state.resolve_task_request_id,
        get_task_record=gateway_task_state.get_task_record,
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=gateway_task_view.dedupe_roles,
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
    )
    if not isinstance(transition, dict):
        return _json(
            {
                "ok": False,
                "error": "retry_transition_unavailable",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect the task lifecycle first; retry transition could not be derived from the current runtime state",
            },
            status=500,
        )

    if bool(transition.get("terminal")):
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "messages": messages,
                "next_step": "/offdesk review",
                "remediation": _retry_blocked_remediation([str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()]),
            },
            status=409,
        )
    import sys

    compatibility_module = sys.modules.get("control_dashboard")
    execute_retry = getattr(compatibility_module, "_execute_retry_run_transition", _execute_retry_run_transition)
    return execute_retry(
        transition,
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command=str(spec.get("command", "")).strip() or "/retry",
        payload=payload,
    )
