#!/usr/bin/env python3
"""Read-only helpers for operator-facing latest intent summaries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_action_audit import compact_action_text


_COMMAND_RESOLVED_DETAIL_RE = re.compile(r"(?:^| )(?P<key>cmd|action|class|trace)=(?P<value>.*?)(?= (?:cmd|action|class|trace)=|$)")


def parse_command_resolved_detail(detail: Any) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for match in _COMMAND_RESOLVED_DETAIL_RE.finditer(str(detail or "").strip()):
        key = str(match.group("key") or "").strip()
        value = str(match.group("value") or "").strip()
        if key and value:
            parsed[key] = value
    return {
        "command": parsed.get("cmd", "").strip() or "-",
        "action": parsed.get("action", "").strip() or "-",
        "trace": parsed.get("trace", "").strip() or "-",
    }


def latest_intent_focus(action: Any, trace: Any) -> str:
    action_token = str(action or "").strip().lower()
    trace_text = str(trace or "").strip().lower()
    if action_token == "offdesk_review":
        if "safe_mode=prefer_control_review_over_dispatch" in trace_text:
            return "execution으로 넘기기 전에 offdesk review와 active runtime 상태를 먼저 확인"
        return "active runtime/task를 먼저 검토하고 blocked·followup·warning을 정리"
    if action_token == "offdesk_prepare":
        return "오늘 밤 scope, provider capacity, auto posture를 먼저 점검"
    if action_token in {"monitor_project", "status", "orch-monitor"}:
        return "재시도보다 먼저 현재 runtime/task 상태와 latest warnings를 확인"
    if action_token == "dispatch_task":
        return "runtime으로 넘기기 전에 preset, approval mode, quality contract를 다시 확인"
    if action_token in {"recover_auto", "auto_recover"}:
        return "recover 전에 retry_at, blocked provider, repeat memory를 먼저 확인"
    return "-"


def load_latest_command_resolution(team_dir: Any) -> Dict[str, str]:
    token = str(team_dir or "").strip()
    if not token:
        return {}
    path = Path(token).expanduser().resolve() / "logs" / "gateway_events.jsonl"
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
                if str(row.get("event", "")).strip() != "command_resolved":
                    continue
                if str(row.get("status", "")).strip() != "accepted":
                    continue
                latest = parse_command_resolved_detail(row.get("detail", ""))
    except Exception:
        return {}
    if not latest:
        return {}
    latest["focus"] = latest_intent_focus(latest.get("action", ""), latest.get("trace", ""))
    return latest


def append_latest_intent_lines(
    lines: List[str],
    intent: Dict[str, str],
    *,
    compact_reason: Optional[Callable[[Any, int], str]] = None,
    line_prefix: str = "",
) -> None:
    if not isinstance(intent, dict) or not intent:
        return
    command = str(intent.get("command", "")).strip() or "-"
    action = str(intent.get("action", "")).strip() or "-"
    trace = str(intent.get("trace", "")).strip() or "-"
    focus = str(intent.get("focus", "")).strip() or latest_intent_focus(action, trace)
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    if command != "-" or action != "-":
        lines.append(f"{line_prefix}latest_intent: {command} | {action}")
    if focus != "-":
        lines.append(f"{line_prefix}first_focus: {focus}")
    if trace != "-":
        lines.append(f"{line_prefix}latest_intent_trace: {formatter(trace, 160)}")


def append_latest_intent_summary_line(
    lines: List[str],
    intent: Dict[str, str],
    *,
    compact_reason: Optional[Callable[[Any, int], str]] = None,
    line_prefix: str = "",
    focus_limit: int = 88,
) -> None:
    if not isinstance(intent, dict) or not intent:
        return
    command = str(intent.get("command", "")).strip() or "-"
    action = str(intent.get("action", "")).strip() or "-"
    trace = str(intent.get("trace", "")).strip() or "-"
    focus = str(intent.get("focus", "")).strip() or latest_intent_focus(action, trace)
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    parts: List[str] = []
    if command != "-" or action != "-":
        parts.append(f"{command} | {action}")
    if focus != "-":
        parts.append(formatter(focus, focus_limit))
    if parts:
        lines.append(f"{line_prefix}latest_intent: " + " | ".join(parts))
