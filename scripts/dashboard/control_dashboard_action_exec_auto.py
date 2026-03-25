#!/usr/bin/env python3
"""Auto recover execution bridge for dashboard mutation actions."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import aoe_tg_management_handlers as management_handlers
import aoe_tg_scheduler_control_handlers as scheduler_control_handlers

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
            "team_dir": str(paths.team_dir),
            "next_step": str(outcome.get("next_step", "")).strip() or ("/auto status" if not blocked else "/offdesk review"),
            "remediation": _auto_recover_remediation(blocked=blocked, provider_state=provider_state),
        },
        status=409 if blocked else 200,
    )
