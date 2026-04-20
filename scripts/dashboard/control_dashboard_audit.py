#!/usr/bin/env python3
"""Action audit helpers for the Control Dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from aoe_tg_artifact_backend import artifact_backend, load_jsonl_rows
from aoe_tg_planning_compact_compat import legacy_planning_review_summary
from control_dashboard_common import DashboardAppConfig, _dashboard_paths

DEFAULT_ACTION_AUDIT_RETENTION_DAYS = 14
DEFAULT_ACTION_AUDIT_KEEP_ROWS = 500


def _action_audit_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()



def _int_from_env(raw: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw if raw is not None else "").strip())
    except Exception:
        return default
    return max(minimum, min(maximum, value))



def _action_audit_retention_days() -> int:
    return _int_from_env(
        os.environ.get("AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS"),
        DEFAULT_ACTION_AUDIT_RETENTION_DAYS,
        minimum=0,
        maximum=3650,
    )



def _action_audit_keep_rows() -> int:
    return _int_from_env(
        os.environ.get("AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS"),
        DEFAULT_ACTION_AUDIT_KEEP_ROWS,
        minimum=10,
        maximum=10000,
    )



def _action_audit_headline(payload: Dict[str, Any]) -> str:
    path = str(payload.get("path", "")).strip()
    status = str(payload.get("status", "")).strip() or "unknown"
    source_command = str(payload.get("source_command", "")).strip() or "-"
    focus_badge = str(payload.get("focus_badge", "")).strip()
    if path == "/control/actions/task/followup":
        return f"Follow-up Preview | {status}"
    if path == "/control/actions/task/followup-execute":
        return f"Follow-up Execute | {status}"
    if path == "/control/actions/task/worker-update-preview":
        return f"Worker Update Preview | {status}"
    if path == "/control/actions/task/subagent-support-run":
        return f"Support Research | {status}"
    if path == "/control/actions/task/worker-apply-preview":
        return f"Worker Apply Preview | {status}"
    if path == "/control/actions/task/worker-apply-propose":
        return f"Worker Apply Proposal | {status}"
    if path == "/control/actions/task/worker-apply-accept":
        return f"Artifact Apply Accept | {status}"
    if path == "/control/actions/chat/send":
        return f"Chat Send | {status}"
    if path == "/control/actions/chat/session-update":
        preset_label = str(payload.get("server_guard_preset_label", "")).strip()
        if focus_badge == "server-guard" and preset_label:
            return f"{preset_label} | {status}"
        return f"Chat Session Update | {status}"
    if path == "/control/actions/chat/session-select-task":
        return f"Chat Session Task | {status}"
    if path == "/control/actions/runtime/judge":
        return f"Offdesk Judge | {status}"
    if path == "/control/actions/runtime/todo-accept":
        return f"Accept Proposal | {status}"
    if path == "/control/actions/runtime/todo-reject":
        return f"Reject Proposal | {status}"
    if path == "/control/actions/runtime/sync-preview":
        return f"Sync Preview | {status}"
    if path == "/control/actions/runtime/syncback-preview":
        return f"Syncback Preview | {status}"
    if path == "/control/actions/runtime/syncback-apply":
        return f"Syncback Apply | {status}"
    if path == "/control/actions/runtime/server-guard-pressure-preview":
        pressure_kind = str((payload.get("payload") or {}).get("pressure_kind", "")).strip().lower()
        label = {
            "codex": "Codex Pressure Preview",
            "python": "Python Pressure Preview",
            "tmux": "Tmux Pressure Preview",
            "process": "Process Pressure Preview",
        }.get(pressure_kind, "Server Guard Pressure Preview")
        return f"{label} | {status}"
    if path == "/control/actions/runtime/background-queue-clean":
        return f"Background Queue Cleanup | {status}"
    if path == "/control/actions/runtime/background-queue-clean-preview":
        return f"Background Queue Cleanup Preview | {status}"
    if path == "/control/actions/task/retry":
        return f"Retry | {status}"
    if path == "/control/actions/control/auto-recover":
        force = bool((payload.get("payload") or {}).get("force"))
        return f"Auto Recover{' Force' if force else ''} | {status}"
    return f"{source_command} | {status}"



def _action_audit_link(payload: Dict[str, Any]) -> Tuple[str, str]:
    path = str(payload.get("path", "")).strip()
    if path == "/control/actions/runtime/server-guard-pressure-preview":
        return "health", "/control/health"
    if path == "/control/actions/chat/session-update":
        next_step = str(payload.get("next_step", "")).strip()
        if next_step.startswith("/control/chat"):
            return "chat console", next_step
        chat_id = str(payload.get("chat_id", "")).strip()
        if chat_id:
            return "chat console", f"/control/chat?chat={chat_id}"
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
    task_href = str(task.get("detail_path", "")).strip() or str(preview.get("detail_path", "")).strip()
    if task_href:
        return "task detail", task_href
    runtime_href = str(preview.get("runtime_path", "")).strip()
    if runtime_href:
        return "runtime detail", runtime_href
    return "-", "-"



def _parse_action_audit_at(raw: object) -> datetime | None:
    token = str(raw or "").strip()
    if not token:
        return None
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _load_existing_action_audit_rows(path: Path) -> List[Dict[str, Any]]:
    return load_jsonl_rows(path)



def _action_audit_lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")



def _prune_action_audit_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    retention_days = _action_audit_retention_days()
    keep_rows = _action_audit_keep_rows()
    if retention_days > 0:
        keep_from = datetime.now().astimezone() - timedelta(days=retention_days)
        filtered: List[Dict[str, Any]] = []
        for row in rows:
            parsed_at = _parse_action_audit_at(row.get("at"))
            if parsed_at is None or parsed_at >= keep_from:
                filtered.append(row)
        rows = filtered
    if keep_rows > 0:
        rows = rows[-keep_rows:]
    return rows



def _append_action_audit(
    config: DashboardAppConfig,
    payload: Dict[str, Any],
    *,
    load_existing_rows: Callable[[Path], List[Dict[str, Any]]] | None = None,
) -> None:
    source_command = str(payload.get("source_command", "")).strip()
    if not source_command:
        return
    paths = _dashboard_paths(config)
    link_label, link_href = _action_audit_link(payload)
    outcome = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else {}
    row = {
        "at": _action_audit_now(),
        "headline": _action_audit_headline(payload),
        "status": str(payload.get("status", "")).strip() or "unknown",
        "outcome_kind": str(outcome.get("kind", "")).strip() or "-",
        "outcome_status": str(outcome.get("status", "")).strip() or str(payload.get("status", "")).strip() or "unknown",
        "outcome_reason_code": str(outcome.get("reason_code", "")).strip() or "-",
        "outcome_detail": str(outcome.get("detail", "")).strip() or "-",
        "next_step": str(payload.get("next_step", "")).strip() or "-",
        "remediation": str(payload.get("remediation", "")).strip() or "-",
        "link_label": link_label,
        "link_href": link_href,
        "source_command": source_command,
        "focus_badge": str(payload.get("focus_badge", "")).strip(),
        "chat_id": str(payload.get("chat_id", "")).strip() or "",
        "transcript_preview": str(payload.get("reply_text", "")).strip()[:4000],
        "chat_preset_diff_summary": str(payload.get("chat_preset_diff_summary", "")).strip(),
    }
    planning_handoff = payload.get("planning_handoff") if isinstance(payload.get("planning_handoff"), dict) else {}
    if planning_handoff:
        row["planning_handoff"] = planning_handoff

    for source_key, row_key in (
        ("planning_compact_summary", "planning_compact_summary"),
        ("planning_compact", "planning_compact_summary"),
        ("subagent_contract_summary", "subagent_contract_summary"),
        ("general_subagent_summary", "subagent_contract_summary"),
        ("subagent_evidence_summary", "subagent_evidence_summary"),
        ("general_subagent_artifact_summary", "subagent_evidence_summary"),
        ("subagent_artifact_path", "subagent_artifact_path"),
        ("general_subagent_artifact_path", "subagent_artifact_path"),
        ("planning_lanes_summary", "planning_lanes_summary"),
        ("planning_lanes", "planning_lanes_summary"),
        ("approved_plan_gate_summary", "approved_plan_gate_summary"),
        ("approved_plan_gate", "approved_plan_gate_summary"),
        ("planner_lane_summary", "planner_lane_summary"),
        ("planner_lane", "planner_lane_summary"),
        ("critic_lane_summary", "critic_lane_summary"),
        ("critic_lane", "critic_lane_summary"),
        ("approved_plan_summary", "approved_plan_summary"),
        ("approved_plan", "approved_plan_summary"),
    ):
        value = str(payload.get(source_key, "")).strip()
        if value:
            row[row_key] = value
    if str(row.get("planning_compact_summary", "")).strip() in {"", "-"}:
        legacy_summary = legacy_planning_review_summary(payload)
        if legacy_summary:
            row["planning_compact_summary"] = legacy_summary
    loader = load_existing_rows or _load_existing_action_audit_rows
    try:
        rows = loader(paths.action_audit_file)
        rows.append(row)
        rows = _prune_action_audit_rows(rows)
        artifact_backend(paths.team_dir).rewrite_action_audit_rows(rows)
    except Exception:
        return



def _with_action_audit(
    response: Tuple[int, Dict[str, str], bytes],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    status, headers, body = response
    if "application/json" not in str(headers.get("Content-Type", "")).lower():
        return response
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception:
        return response
    if not isinstance(payload, dict):
        return response
    _append_action_audit(config, payload)
    return response
