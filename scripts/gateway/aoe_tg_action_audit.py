#!/usr/bin/env python3
"""Read-only helpers for dashboard action audit state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote


ACTION_AUDIT_DIRNAME = "dashboard"
ACTION_AUDIT_FILENAME = "action-history.jsonl"


def compact_action_text(raw: Any, limit: int = 120) -> str:
    text = " ".join(str(raw or "").strip().split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def load_latest_action_audit(team_dir: Any) -> Dict[str, str]:
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    return _normalize_latest_action_row(rows[-1])


def _action_audit_path(team_dir: Any) -> Path:
    token = str(team_dir or "").strip()
    return Path(token).expanduser().resolve() / ACTION_AUDIT_DIRNAME / ACTION_AUDIT_FILENAME


def _normalize_latest_action_row(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "headline": str(row.get("headline", "")).strip() or "-",
        "status": str(row.get("status", "")).strip() or "unknown",
        "outcome_kind": str(row.get("outcome_kind", "")).strip() or "-",
        "outcome_status": str(row.get("outcome_status", "")).strip() or str(row.get("status", "")).strip() or "unknown",
        "outcome_reason_code": str(row.get("outcome_reason_code", "")).strip() or "-",
        "outcome_detail": str(row.get("outcome_detail", "")).strip() or "-",
        "next_step": str(row.get("next_step", "")).strip() or "-",
        "remediation": str(row.get("remediation", "")).strip() or "-",
        "source_command": str(row.get("source_command", "")).strip() or "-",
    }


def _latest_action_headline(latest_action: Dict[str, str]) -> str:
    headline = str(latest_action.get("headline", "")).strip() or "-"
    reason_code = str(latest_action.get("outcome_reason_code", "")).strip() or "-"
    if reason_code in {"", "-"}:
        return headline
    if "reason=" in headline:
        return headline
    return f"{headline} | reason={reason_code}"


def _load_action_audit_rows(team_dir: Any) -> List[Dict[str, Any]]:
    token = str(team_dir or "").strip()
    if not token:
        return []
    path = _action_audit_path(token)
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
                    row = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                rows.append(row)
    except Exception:
        return []
    return rows


def load_latest_action_audit_for_task(team_dir: Any, request_id: Any) -> Dict[str, str]:
    token = str(request_id or "").strip()
    if not token:
        return {}
    task_path = f"/control/tasks/by-request/{quote(token, safe='')}"
    rows = _load_action_audit_rows(team_dir)
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() == task_path:
            return _normalize_latest_action_row(row)
    return {}


def load_latest_action_audit_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
    request_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}" if alias else ""
    task_paths = {
        f"/control/tasks/by-request/{quote(str(item).strip(), safe='')}"
        for item in (request_ids or [])
        if str(item).strip()
    }
    for row in reversed(rows):
        link_href = str(row.get("link_href", "")).strip()
        if runtime_path and link_href == runtime_path:
            return _normalize_latest_action_row(row)
        if task_paths and link_href in task_paths:
            return _normalize_latest_action_row(row)
    return {}


def append_latest_action_lines(
    lines: List[str],
    latest_action: Dict[str, str],
    *,
    compact_reason: Optional[callable] = None,
    line_prefix: str = "",
) -> None:
    if not isinstance(latest_action, dict) or not latest_action:
        return
    headline = _latest_action_headline(latest_action)
    next_step = str(latest_action.get("next_step", "")).strip() or "-"
    remediation = str(latest_action.get("remediation", "")).strip() or "-"
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    if headline != "-":
        lines.append(f"{line_prefix}latest_action: {headline}")
    if next_step != "-":
        lines.append(f"{line_prefix}latest_action_next: {next_step}")
    if remediation != "-":
        lines.append(f"{line_prefix}latest_action_note: {formatter(remediation, 120)}")


def append_latest_action_summary_line(
    lines: List[str],
    latest_action: Dict[str, str],
    *,
    compact_reason: Optional[callable] = None,
    line_prefix: str = "",
    note_limit: int = 88,
) -> None:
    if not isinstance(latest_action, dict) or not latest_action:
        return
    headline = _latest_action_headline(latest_action)
    next_step = str(latest_action.get("next_step", "")).strip() or "-"
    remediation = str(latest_action.get("remediation", "")).strip() or "-"
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    parts: List[str] = []
    if headline != "-":
        parts.append(headline)
    if next_step != "-":
        parts.append(f"next={next_step}")
    if remediation != "-":
        parts.append(formatter(remediation, note_limit))
    if parts:
        lines.append(f"{line_prefix}latest_action: " + " | ".join(parts))
