#!/usr/bin/env python3
"""Auto recover execution bridge for dashboard mutation actions."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import aoe_tg_harness_authoring_adapter as harness_authoring_adapter
import aoe_tg_management_handlers as management_handlers
import aoe_tg_scheduler_control_handlers as scheduler_control_handlers
import aoe_tg_task_state as task_state
import aoe_tg_task_view as task_view

from control_dashboard_action_exec_shared import (
    _DASHBOARD_CHAT_ID,
    _DASHBOARD_CHAT_ROLE,
    _dashboard_action_args,
    _json,
    _latest_recorded_outcome,
    _load_dashboard_manager_state,
    _make_send_collector,
    _missing_outcome_response,
)
from control_dashboard_common import DashboardAppConfig



def _auto_recover_remediation(*, blocked: bool, provider_state: Dict[str, Any]) -> str:
    retry_at = str(provider_state.get("next_retry_at", "")).strip()
    repeat_count = int(provider_state.get("recovery_repeat_count", 0) or 0)
    if not blocked:
        return "verify recovery grace and next retry timing in /auto status before making the next control decision"
    if retry_at and repeat_count > 0:
        return f"provider capacity is still blocked; inspect /offdesk review and /auto status, then wait for retry_at={retry_at} with repeat memory in mind"
    if retry_at:
        return f"provider capacity is still blocked; inspect /auto status and wait for retry_at={retry_at} before forcing another recover"
    if repeat_count > 0:
        return "provider capacity is repeatedly blocked; inspect repeat memory and blocked runtimes in /offdesk review before forcing another recover"
    return "inspect provider capacity and blocked runtimes in /offdesk review before forcing another recover"


def _latest_task_for_active_runtime(manager_state: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any], str]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    active_key = str(manager_state.get("active", "")).strip()
    if not active_key:
        return {}, {}, ""
    entry = projects.get(active_key) if isinstance(projects.get(active_key), dict) else {}
    if not isinstance(entry, dict):
        return {}, {}, ""
    request_id = str(entry.get("last_request_id", "")).strip()
    task = task_state.get_task_record(entry, request_id) if request_id else {}
    if isinstance(task, dict) and task:
        return task, entry, request_id
    tasks = task_state.ensure_project_tasks(entry)
    latest_task: Dict[str, Any] = {}
    latest_request_id = ""
    latest_at = ""
    for candidate_request_id, candidate_task in tasks.items():
        if not isinstance(candidate_task, dict):
            continue
        status = str(candidate_task.get("status", "")).strip().lower()
        if status == "completed":
            continue
        updated_at = str(candidate_task.get("updated_at", "")).strip() or str(candidate_task.get("created_at", "")).strip()
        if updated_at >= latest_at:
            latest_at = updated_at
            latest_request_id = str(candidate_request_id).strip()
            latest_task = candidate_task
    if latest_task:
        return latest_task, entry, latest_request_id
    return {}, entry, ""



def _execute_auto_recover_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    args = _dashboard_action_args(config)
    paths, manager_state = _load_dashboard_manager_state(config)
    messages: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []

    handled = scheduler_control_handlers.handle_scheduler_control_command(
        cmd="auto",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        chat_role=_DASHBOARD_CHAT_ROLE,
        rest="recover force" if bool(payload.get("force")) else "recover",
        send=_make_send_collector(messages),
        get_default_mode=lambda *_args, **_kwargs: "",
        get_pending_mode=lambda *_args, **_kwargs: "",
        get_chat_report_level=lambda *_args, **_kwargs: "normal",
        get_chat_room=lambda *_args, **_kwargs: management_handlers.DEFAULT_OFFDESK_ROOM,
        set_default_mode=lambda *_args, **_kwargs: None,
        set_chat_report_level=lambda *_args, **_kwargs: None,
        set_chat_room=lambda *_args, **_kwargs: None,
        clear_default_mode=lambda *_args, **_kwargs: False,
        clear_pending_mode=lambda *_args, **_kwargs: False,
        clear_confirm_action=lambda *_args, **_kwargs: False,
        clear_chat_report_level=lambda *_args, **_kwargs: False,
        save_manager_state=lambda *_args, **_kwargs: None,
        resolve_project_entry=management_handlers._resolve_project_entry,
        project_lock_row=management_handlers._project_lock_row,
        project_lock_label=management_handlers._project_lock_label,
        parse_replace_sync_flag=management_handlers._parse_replace_sync_flag,
        normalize_prefetch_token=management_handlers._normalize_prefetch_token,
        prefetch_display=management_handlers._prefetch_display,
        compact_reason=management_handlers._compact_reason,
        status_report_level=management_handlers._status_report_level,
        focused_project_snapshot_lines=management_handlers._focused_project_snapshot_lines,
        ops_scope_summary=management_handlers._ops_scope_summary,
        ops_scope_compact_lines=lambda state, limit, detail_level: management_handlers._ops_scope_compact_lines(
            state,
            limit=limit,
            detail_level=detail_level,
        ),
        offdesk_prepare_targets=management_handlers._offdesk_prepare_targets,
        offdesk_prepare_project_report=management_handlers._offdesk_prepare_project_report,
        sort_offdesk_reports=management_handlers._sort_offdesk_reports,
        offdesk_review_reply_markup=lambda *_args, **_kwargs: {},
        offdesk_prepare_reply_markup=lambda *_args, **_kwargs: {},
        auto_state_path=management_handlers._auto_state_path,
        offdesk_state_path=management_handlers._offdesk_state_path,
        provider_capacity_state_path=management_handlers._provider_capacity_state_path,
        load_auto_state=management_handlers._load_auto_state,
        save_auto_state=management_handlers._save_auto_state,
        load_offdesk_state=management_handlers._load_offdesk_state,
        save_offdesk_state=management_handlers._save_offdesk_state,
        load_provider_capacity_state=management_handlers._load_provider_capacity_state,
        save_provider_capacity_state=management_handlers._save_provider_capacity_state,
        scheduler_session_name=management_handlers._scheduler_session_name,
        tmux_has_session=management_handlers._tmux_has_session,
        tmux_auto_command=management_handlers._tmux_auto_command,
        now_iso=management_handlers._now_iso,
        default_auto_interval_sec=management_handlers.DEFAULT_AUTO_INTERVAL_SEC,
        default_auto_idle_sec=management_handlers.DEFAULT_AUTO_IDLE_SEC,
        default_auto_max_failures=management_handlers.DEFAULT_AUTO_MAX_FAILURES,
        default_offdesk_command=management_handlers.DEFAULT_OFFDESK_COMMAND,
        default_offdesk_prefetch=management_handlers.DEFAULT_OFFDESK_PREFETCH,
        default_offdesk_prefetch_since=management_handlers.DEFAULT_OFFDESK_PREFETCH_SINCE,
        default_offdesk_report_level=management_handlers.DEFAULT_OFFDESK_REPORT_LEVEL,
        default_offdesk_room=management_handlers.DEFAULT_OFFDESK_ROOM,
        record_outcome=lambda row: outcomes.append(dict(row)) if isinstance(row, dict) else None,
    )

    if not handled:
        return _json(
            {
                "ok": False,
                "error": "auto_recover_unhandled",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect /auto status and provider capacity state before retrying auto recover",
            },
            status=500,
        )

    auto_state = management_handlers._load_auto_state(management_handlers._auto_state_path(args))
    provider_state = management_handlers._load_provider_capacity_state(management_handlers._provider_capacity_state_path(args))
    active_task, active_entry, active_request_id = _latest_task_for_active_runtime(manager_state)
    planning_bundle = task_view.planning_operator_bundle(active_task)
    subagent_surface = harness_authoring_adapter.ensure_general_subagent_support_surface(
        str(active_entry.get("team_dir", "")).strip() or paths.team_dir,
        entry=active_entry,
        task=active_task,
    )
    general_subagent_executed = bool(subagent_surface.get("executed", False)) if isinstance(subagent_surface, dict) else False
    outcome = _latest_recorded_outcome(outcomes, kind="auto_recover")
    if not outcome:
        return _missing_outcome_response(
            path=str(spec.get("path", "-")),
            source_command=str(spec.get("command", "-")),
            payload=payload,
            kind="auto_recover",
            messages=messages,
            events=[],
            remediation="inspect the auto recover handler contract; dashboard actions now require structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"
    next_step = str(outcome.get("next_step", "")).strip() or ("/auto status" if not blocked else "/offdesk review")
    followup_actions: List[Dict[str, Any]] = [
        {
            "label": "Open Recovery",
            "href": "/control/recovery",
            "note": "inspect recovery summary and latest runtime posture after auto recover",
            "priority": "primary",
        },
        {
            "label": "Open Offdesk Prep",
            "href": "/control/offdesk",
            "note": "inspect blocked runtimes and next retry posture before another control decision",
            "priority": "secondary",
        },
    ]
    active_task_ref = (
        str(active_task.get("short_id", "")).strip()
        or str(active_task.get("alias", "")).strip()
        or active_request_id
    )
    if active_task_ref:
        support_note = task_view.planning_operator_note(
            active_task,
            notes=["materialize bounded general_research evidence for the active task before retrying or rerouting"],
        )
        followup_actions.append(
            {
                "label": "Run Support Research",
                "path": "/control/actions/task/subagent-support-run",
                "payload_json": json.dumps({"task_ref": active_task_ref}, ensure_ascii=False, separators=(",", ":")),
                "command": f"/task {active_task_ref} | general-research-support",
                "mode": "safe",
                "note": support_note,
                "priority": "secondary",
            }
        )

    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": not blocked,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "messages": messages,
            "outcome": {
                "kind": "auto_recover",
                "status": "blocked" if blocked else "executed",
                "reason_code": str(outcome.get("reason_code", "")).strip() if outcome else "-",
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "auto_state": {
                "enabled": bool(auto_state.get("enabled", False)),
                "command": str(auto_state.get("command", "")).strip() or "-",
                "recovered_at": str(auto_state.get("recovered_at", "")).strip() or "-",
                "recovery_grace_until": str(auto_state.get("recovery_grace_until", "")).strip() or "-",
            },
            "provider_capacity": {
                "next_retry_at": str(provider_state.get("next_retry_at", "")).strip() or "-",
                "repeat_count": int(provider_state.get("recovery_repeat_count", 0) or 0),
            },
            "planning_compact": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "planning_compact_summary": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "subagent_contract_summary": str(subagent_surface.get("summary", "")).strip() or "-",
            "subagent_evidence_summary": str(subagent_surface.get("artifact_summary", "")).strip() or "-",
            "subagent_artifact_path": str(subagent_surface.get("artifact_path", "")).strip() or "-",
            "general_subagent_executed": general_subagent_executed,
            "team_dir": str(paths.team_dir),
            "next_step": next_step,
            "remediation": _auto_recover_remediation(blocked=blocked, provider_state=provider_state),
            "actions": followup_actions,
        },
        status=409 if blocked else 200,
    )
