#!/usr/bin/env python3
"""Read-only helpers for dashboard action audit state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


ACTION_AUDIT_DIRNAME = "dashboard"
ACTION_AUDIT_FILENAME = "action-history.jsonl"


def compact_action_text(raw: Any, limit: int = 120) -> str:
    text = " ".join(str(raw or "").strip().split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def load_latest_action_audit(team_dir: Any) -> Dict[str, str]:
    token = str(team_dir or "").strip()
    if not token:
        return {}
    path = Path(token).expanduser().resolve() / ACTION_AUDIT_DIRNAME / ACTION_AUDIT_FILENAME
    if not path.exists():
        return {}
    latest: Dict[str, str] = {}
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
                latest = {
                    "headline": str(row.get("headline", "")).strip() or "-",
                    "status": str(row.get("status", "")).strip() or "unknown",
                    "next_step": str(row.get("next_step", "")).strip() or "-",
                    "remediation": str(row.get("remediation", "")).strip() or "-",
                    "source_command": str(row.get("source_command", "")).strip() or "-",
                }
    except Exception:
        return {}
    return latest


def append_latest_action_lines(
    lines: List[str],
    latest_action: Dict[str, str],
    *,
    compact_reason: Optional[callable] = None,
) -> None:
    if not isinstance(latest_action, dict) or not latest_action:
        return
    headline = str(latest_action.get("headline", "")).strip() or "-"
    next_step = str(latest_action.get("next_step", "")).strip() or "-"
    remediation = str(latest_action.get("remediation", "")).strip() or "-"
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    if headline != "-":
        lines.append(f"latest_action: {headline}")
    if next_step != "-":
        lines.append(f"latest_action_next: {next_step}")
    if remediation != "-":
        lines.append(f"latest_action_note: {formatter(remediation, 120)}")
