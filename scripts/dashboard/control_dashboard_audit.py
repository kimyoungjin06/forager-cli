#!/usr/bin/env python3
"""Action audit helpers for the Control Dashboard."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

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
    if path == "/control/actions/task/followup":
        return f"Follow-up Preview | {status}"
    if path == "/control/actions/task/followup-execute":
        return f"Follow-up Execute | {status}"
    if path == "/control/actions/task/worker-update-preview":
        return f"Worker Update Preview | {status}"
    if path == "/control/actions/task/worker-apply-preview":
        return f"Worker Apply Preview | {status}"
    if path == "/control/actions/task/worker-apply-propose":
        return f"Worker Apply Proposal | {status}"
    if path == "/control/actions/task/worker-apply-accept":
        return f"Artifact Apply Accept | {status}"
    if path == "/control/actions/chat/send":
        return f"Chat Send | {status}"
    if path == "/control/actions/chat/session-update":
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
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
    except Exception:
        return []
    return rows



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
        "chat_id": str(payload.get("chat_id", "")).strip() or "",
        "transcript_preview": str(payload.get("reply_text", "")).strip()[:4000],
    }
    loader = load_existing_rows or _load_existing_action_audit_rows
    try:
        paths.action_audit_file.parent.mkdir(parents=True, exist_ok=True)
        lock_path = _action_audit_lock_path(paths.action_audit_file)
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            rows = loader(paths.action_audit_file)
            rows.append(row)
            rows = _prune_action_audit_rows(rows)
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(paths.action_audit_file.parent),
                prefix=paths.action_audit_file.name + ".tmp.",
                delete=False,
            ) as tmp_handle:
                tmp_path = Path(tmp_handle.name)
                for item in rows:
                    tmp_handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                tmp_handle.flush()
                os.fsync(tmp_handle.fileno())
            os.replace(tmp_path, paths.action_audit_file)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
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
