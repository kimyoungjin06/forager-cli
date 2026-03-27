#!/usr/bin/env python3
"""Helpers for operator-facing latest intent summaries."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_runtime_core import latest_intent_snapshot_path as runtime_latest_intent_snapshot_path
from aoe_tg_action_audit import compact_action_text


_COMMAND_RESOLVED_DETAIL_RE = re.compile(r"(?:^| )(?P<key>cmd|action|class|trace)=(?P<value>.*?)(?= (?:cmd|action|class|trace)=|$)")
LATEST_INTENT_DIRNAME = "control"
LATEST_INTENT_FILENAME = "latest-intent.json"


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
        "intent_class": parsed.get("class", "").strip(),
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


def latest_intent_snapshot_path(team_dir: Any) -> Path:
    token = str(team_dir or "").strip()
    if not token:
        return Path("")
    return runtime_latest_intent_snapshot_path(token)


def _normalize_latest_intent_record(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    if not any(str(raw.get(key, "")).strip() for key in ("command", "action", "trace", "focus", "intent_class", "recorded_at")):
        return {}
    command = str(raw.get("command", "")).strip() or "-"
    action = str(raw.get("action", "")).strip() or "-"
    trace = str(raw.get("trace", "")).strip() or "-"
    focus = str(raw.get("focus", "")).strip() or latest_intent_focus(action, trace)
    intent_class = str(raw.get("intent_class", "")).strip()
    recorded_at = str(raw.get("recorded_at", "")).strip()
    out = {
        "command": command,
        "action": action,
        "trace": trace,
        "focus": focus,
    }
    if intent_class:
        out["intent_class"] = intent_class
    if recorded_at:
        out["recorded_at"] = recorded_at
    return out


def _load_latest_command_resolution_from_events(path: Path) -> Dict[str, str]:
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


def _parse_recorded_at(raw: Any) -> Optional[datetime]:
    token = str(raw or "").strip()
    if not token:
        return None
    normalized = token[:-1] + "+00:00" if token.endswith("Z") else token
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_intent_lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _load_existing_latest_intent_payload(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _normalize_latest_intent_record(parsed)


def _should_replace_latest_intent(current: Dict[str, str], incoming: Dict[str, str]) -> bool:
    current_dt = _parse_recorded_at(current.get("recorded_at"))
    incoming_dt = _parse_recorded_at(incoming.get("recorded_at"))
    if current_dt is not None and incoming_dt is not None:
        return incoming_dt >= current_dt
    if current_dt is not None and incoming_dt is None:
        return False
    if current_dt is None and incoming_dt is not None:
        return True
    return True


def _write_latest_intent_payload(path: Path, payload: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _latest_intent_lock_path(path)
    tmp_path: Optional[Path] = None
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        current = _load_existing_latest_intent_payload(path)
        if current and not _should_replace_latest_intent(current, payload):
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            return
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".tmp.",
                delete=False,
            ) as tmp_handle:
                tmp_path = Path(tmp_handle.name)
                tmp_handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                tmp_handle.flush()
                os.fsync(tmp_handle.fileno())
            os.replace(tmp_path, path)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass


def save_latest_command_resolution(
    team_dir: Any,
    *,
    command: Any,
    action: Any,
    trace: Any,
    intent_class: Any = "",
    recorded_at: Any = "",
    mirror_team_dir: Any = None,
) -> None:
    payload = _normalize_latest_intent_record(
        {
            "command": command,
            "action": action,
            "trace": trace,
            "focus": latest_intent_focus(action, trace),
            "intent_class": intent_class,
            "recorded_at": recorded_at,
        }
    )
    if not payload:
        return
    primary = latest_intent_snapshot_path(team_dir)
    if str(primary).strip():
        _write_latest_intent_payload(primary, payload)
    if mirror_team_dir is None:
        return
    mirror = latest_intent_snapshot_path(mirror_team_dir)
    if str(mirror).strip() and mirror != primary:
        _write_latest_intent_payload(mirror, payload)


def load_latest_command_resolution(team_dir: Any) -> Dict[str, str]:
    token = str(team_dir or "").strip()
    if not token:
        return {}
    try:
        snapshot = latest_intent_snapshot_path(token)
        if str(snapshot).strip() and snapshot.exists():
            parsed = json.loads(snapshot.read_text(encoding="utf-8"))
            normalized = _normalize_latest_intent_record(parsed)
            if normalized:
                return normalized
    except Exception:
        pass
    return _load_latest_command_resolution_from_events(
        Path(token).expanduser().resolve() / "logs" / "gateway_events.jsonl"
    )


def task_intent_summary(task: Any) -> Dict[str, str]:
    if not isinstance(task, dict):
        return {}
    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    command = str(task.get("intent_command", "") or context.get("intent_command", "")).strip()
    action = str(task.get("intent_action", "") or context.get("intent_action", "")).strip()
    trace = str(task.get("intent_trace", "") or context.get("intent_trace", "")).strip()
    if not any((command, action, trace)):
        return {}
    return {
        "command": command or "-",
        "action": action or "-",
        "trace": trace or "-",
        "focus": latest_intent_focus(action, trace),
        "recorded_at": str(task.get("intent_recorded_at", "")).strip() or str(task.get("updated_at", "")).strip() or "",
    }


def runtime_latest_intent_summary(entry: Any) -> Dict[str, str]:
    if not isinstance(entry, dict):
        return {}
    tasks = entry.get("tasks")
    if not isinstance(tasks, dict):
        return {}
    latest: Dict[str, str] = {}
    latest_at = ""
    for task in tasks.values():
        intent = task_intent_summary(task)
        if not intent:
            continue
        recorded_at = str(intent.get("recorded_at", "")).strip()
        if recorded_at and recorded_at >= latest_at:
            latest = intent
            latest_at = recorded_at
        elif (not latest) and (not latest_at):
            latest = intent
    if latest:
        latest.pop("recorded_at", None)
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
