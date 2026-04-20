#!/usr/bin/env python3
"""Low-level path and freshness loaders for dashboard state assembly."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_chat_aliases as chat_aliases
import aoe_tg_action_audit as action_audit
import aoe_tg_operator_summary as operator_summary
import aoe_tg_runtime_core as runtime_core
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_view as task_view

MANAGER_STATE_FILENAME = "orch_manager_state.json"
AUTO_STATE_FILENAME = "auto_scheduler.json"
PROVIDER_CAPACITY_FILENAME = "provider_capacity.json"
GATEWAY_EVENTS_FILENAME = "gateway_events.jsonl"
ACTION_AUDIT_DIRNAME = "dashboard"
ACTION_AUDIT_FILENAME = "action-history.jsonl"
RECOVERY_SUMMARY_DIRNAME = "nightly-session-summary"
RECOVERY_SUMMARY_FILENAME = "latest.json"
LATEST_INTENT_DIRNAME = operator_summary.LATEST_INTENT_DIRNAME
LATEST_INTENT_FILENAME = operator_summary.LATEST_INTENT_FILENAME

_LAST_GOOD_JSON: Dict[str, Dict[str, Any]] = {}
_LAST_GOOD_MANAGER_STATE: Dict[str, Dict[str, Any]] = {}
_LAST_GOOD_COMMAND_RESOLUTION: Dict[str, Dict[str, str]] = {}
_LAST_GOOD_ACTION_AUDIT: Dict[str, List[Dict[str, str]]] = {}


@dataclass(frozen=True)
class FileFreshnessDTO:
    name: str
    path: str
    exists: bool
    updated_at: str = ""
    stale: bool = False
    error: str = ""


@dataclass(frozen=True)
class ActionAuditRowDTO:
    at: str
    headline: str
    headline_summary: str
    planning_compact_summary: str
    approved_plan_summary: str
    status: str
    outcome_kind: str
    outcome_status: str
    outcome_reason_code: str
    outcome_detail: str
    next_step: str
    remediation: str
    link_label: str
    link_href: str
    source_command: str
    focus_badge: str
    chat_id: str = ""
    transcript_preview: str = ""
    chat_preset_diff_summary: str = ""
    thread_href: str = ""
    thread_label: str = ""

    @property
    def planning_review_summary(self) -> str:
        return self.planning_compact_summary


@dataclass(frozen=True)
class ControlPaths:
    control_root: Path
    team_dir: Path
    manager_state_file: Path
    chat_aliases_file: Path
    auto_state_file: Path
    provider_capacity_file: Path
    latest_intent_file: Path
    gateway_events_file: Path
    action_audit_file: Path


@dataclass(frozen=True)
class ManagerStateLoadResult:
    state: Dict[str, Any]
    freshness: FileFreshnessDTO



def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()



def _iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().replace(microsecond=0).isoformat()
    except Exception:
        return ""



def resolve_control_paths(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> ControlPaths:
    root = Path(control_root).expanduser().resolve()
    resolved_manager = Path(manager_state_file).expanduser().resolve() if manager_state_file else None
    if team_dir:
        resolved_team_dir = Path(team_dir).expanduser().resolve()
    elif resolved_manager is not None:
        resolved_team_dir = resolved_manager.parent.resolve()
    else:
        resolved_team_dir = runtime_core.resolve_team_dir(root, None)
    if resolved_manager is None:
        resolved_manager = (resolved_team_dir / MANAGER_STATE_FILENAME).resolve()
    return ControlPaths(
        control_root=root,
        team_dir=resolved_team_dir,
        manager_state_file=resolved_manager,
        chat_aliases_file=chat_aliases.resolve_chat_aliases_file(resolved_team_dir, None),
        auto_state_file=(resolved_team_dir / AUTO_STATE_FILENAME).resolve(),
        provider_capacity_file=runtime_core.provider_capacity_state_path(resolved_team_dir, filename=PROVIDER_CAPACITY_FILENAME),
        latest_intent_file=runtime_core.latest_intent_snapshot_path(resolved_team_dir),
        gateway_events_file=(resolved_team_dir / "logs" / GATEWAY_EVENTS_FILENAME).resolve(),
        action_audit_file=runtime_core.action_audit_path(resolved_team_dir),
    )



def _load_json_file(path: Path, *, name: str) -> Tuple[Dict[str, Any], FileFreshnessDTO]:
    key = str(path)
    exists = path.exists()
    updated_at = _iso_from_mtime(path) if exists else ""
    if not exists:
        return {}, FileFreshnessDTO(name=name, path=key, exists=False, updated_at="")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("json root is not an object")
        _LAST_GOOD_JSON[key] = data
        return data, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at)
    except Exception as exc:
        cached = _LAST_GOOD_JSON.get(key)
        if isinstance(cached, dict):
            return cached, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc))
        return {}, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc))



def _normalize_latest_intent_record(raw: Dict[str, Any]) -> Dict[str, str]:
    if not any(str(raw.get(key, "")).strip() for key in ("command", "action", "trace", "focus")):
        return {}
    command = str(raw.get("command", "")).strip() or "-"
    action = str(raw.get("action", "")).strip() or "-"
    trace = str(raw.get("trace", "")).strip() or "-"
    return {
        "command": command,
        "action": action,
        "trace": trace,
        "focus": str(raw.get("focus", "")).strip() or operator_summary.latest_intent_focus(action, trace),
    }



def _load_latest_command_resolution_from_events(path: Path) -> Tuple[Dict[str, str], FileFreshnessDTO]:
    key = str(path)
    exists = path.exists()
    updated_at = _iso_from_mtime(path) if exists else ""
    if not exists:
        empty = {"command": "-", "action": "-", "trace": "-"}
        return empty, FileFreshnessDTO(name="gateway_events", path=key, exists=False, updated_at="")
    try:
        latest: Dict[str, str] | None = None
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
                latest = operator_summary.parse_command_resolved_detail(str(row.get("detail", "")))
        resolution = latest or {"command": "-", "action": "-", "trace": "-"}
        _LAST_GOOD_COMMAND_RESOLUTION[key] = dict(resolution)
        return resolution, FileFreshnessDTO(name="gateway_events", path=key, exists=True, updated_at=updated_at)
    except Exception as exc:
        cached = _LAST_GOOD_COMMAND_RESOLUTION.get(key)
        if isinstance(cached, dict):
            resolution = {
                "command": str(cached.get("command", "-")).strip() or "-",
                "action": str(cached.get("action", "-")).strip() or "-",
                "trace": str(cached.get("trace", "-")).strip() or "-",
            }
            return resolution, FileFreshnessDTO(
                name="gateway_events",
                path=key,
                exists=True,
                updated_at=updated_at,
                stale=True,
                error=str(exc),
            )
        return {"command": "-", "action": "-", "trace": "-"}, FileFreshnessDTO(
            name="gateway_events",
            path=key,
            exists=True,
            updated_at=updated_at,
            stale=True,
            error=str(exc),
        )



def _load_latest_command_resolution(
    latest_intent_path: Path,
    gateway_events_path: Path,
) -> Tuple[Dict[str, str], FileFreshnessDTO, Optional[FileFreshnessDTO]]:
    latest_intent_data, latest_intent_freshness = _load_json_file(latest_intent_path, name="latest_intent")
    normalized = _normalize_latest_intent_record(latest_intent_data)
    if normalized:
        return normalized, latest_intent_freshness, None
    fallback, gateway_events_freshness = _load_latest_command_resolution_from_events(gateway_events_path)
    return fallback, latest_intent_freshness, gateway_events_freshness



def _normalize_action_audit_row(raw: Dict[str, Any]) -> ActionAuditRowDTO:
    outcome_kind = str(raw.get("outcome_kind", "")).strip() or "-"
    focus_badge = str(raw.get("focus_badge", "")).strip()
    if outcome_kind == "replan_auto_route":
        focus_badge = "auto-route"
    elif outcome_kind == "offdesk_judge":
        focus_badge = "judge"
    elif outcome_kind == "retry_run":
        focus_badge = "retry"
    elif not focus_badge and outcome_kind in {
        "background_queue_cleanup",
        "background_queue_cleanup_preview",
        "auto_recover",
        "codex_process_pressure_preview",
        "python_process_pressure_preview",
        "tmux_process_pressure_preview",
        "process_pressure_preview",
    }:
        focus_badge = "server-guard"
    planning_compact_summary = action_audit.summarize_retry_replan_planning_compact_handoff(
        raw.get("planning_handoff"),
        row=raw,
    )
    if planning_compact_summary in {"", "-"}:
        planning_compact_summary = str(raw.get("planning_compact_summary") or raw.get("planning_compact") or "-").strip() or "-"
    approved_plan_summary = action_audit.summarize_retry_replan_approved_plan_handoff(
        raw.get("planning_handoff"),
        row=raw,
    )
    if approved_plan_summary in {"", "-"}:
        approved_plan_summary = str(
            raw.get("approved_plan_summary") or raw.get("approved_plan") or "-"
        ).strip() or "-"
    planning_compact_summary = task_view.planning_compact_operator_summary(
        planning_compact=planning_compact_summary,
        approved_plan=approved_plan_summary,
    )
    return ActionAuditRowDTO(
        at=str(raw.get("at", "")).strip() or "-",
        headline=str(raw.get("headline", "")).strip() or "-",
        headline_summary=action_audit.summarize_action_audit_headline(raw),
        planning_compact_summary=planning_compact_summary,
        approved_plan_summary=approved_plan_summary,
        status=str(raw.get("status", "")).strip() or "unknown",
        outcome_kind=outcome_kind,
        outcome_status=str(raw.get("outcome_status", "")).strip() or str(raw.get("status", "")).strip() or "unknown",
        outcome_reason_code=str(raw.get("outcome_reason_code", "")).strip() or "-",
        outcome_detail=str(raw.get("outcome_detail", "")).strip() or "-",
        next_step=str(raw.get("next_step", "")).strip() or "-",
        remediation=str(raw.get("remediation", "")).strip() or "-",
        link_label=str(raw.get("link_label", "")).strip() or "-",
        link_href=str(raw.get("link_href", "")).strip() or "-",
        source_command=str(raw.get("source_command", "")).strip() or "-",
        focus_badge=focus_badge,
        chat_id=str(raw.get("chat_id", "")).strip(),
        transcript_preview=str(raw.get("transcript_preview", "")).strip(),
    )



def _load_recent_action_audit(path: Path, *, limit: int = 5) -> Tuple[List[ActionAuditRowDTO], FileFreshnessDTO]:
    key = str(path)
    exists = path.exists()
    updated_at = _iso_from_mtime(path) if exists else ""
    if not exists:
        return [], FileFreshnessDTO(name="action_audit", path=key, exists=False, updated_at="")
    try:
        rows: List[ActionAuditRowDTO] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(parsed, dict):
                    continue
                rows.append(_normalize_action_audit_row(parsed))
        rows = rows[-limit:]
        rows.reverse()
        _LAST_GOOD_ACTION_AUDIT[key] = [
            {
                "at": row.at,
                "headline": row.headline,
                "status": row.status,
                "outcome_kind": row.outcome_kind,
                "outcome_status": row.outcome_status,
                "outcome_reason_code": row.outcome_reason_code,
                "outcome_detail": row.outcome_detail,
                "next_step": row.next_step,
                "remediation": row.remediation,
                "link_label": row.link_label,
                "link_href": row.link_href,
                "source_command": row.source_command,
                "focus_badge": row.focus_badge,
                "chat_id": row.chat_id,
                "transcript_preview": row.transcript_preview,
                "chat_preset_diff_summary": row.chat_preset_diff_summary,
            }
            for row in rows
        ]
        return rows, FileFreshnessDTO(name="action_audit", path=key, exists=True, updated_at=updated_at)
    except Exception as exc:
        cached = _LAST_GOOD_ACTION_AUDIT.get(key)
        if isinstance(cached, list):
            rows = [_normalize_action_audit_row(row) for row in cached if isinstance(row, dict)]
            return rows, FileFreshnessDTO(
                name="action_audit",
                path=key,
                exists=True,
                updated_at=updated_at,
                stale=True,
                error=str(exc),
            )
        return [], FileFreshnessDTO(
            name="action_audit",
            path=key,
            exists=True,
            updated_at=updated_at,
            stale=True,
            error=str(exc),
        )



def _action_audit_status_summary(rows: List[ActionAuditRowDTO]) -> str:
    counts: Dict[str, int] = {}
    for row in rows:
        status = str(row.status or "").strip().lower() or "unknown"
        counts[status] = int(counts.get(status, 0)) + 1
    if not counts:
        return "-"
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{status}={count}" for status, count in ordered)



def _load_manager_state(paths: ControlPaths) -> ManagerStateLoadResult:
    key = str(paths.manager_state_file)
    exists = paths.manager_state_file.exists()
    updated_at = _iso_from_mtime(paths.manager_state_file) if exists else ""
    if not exists:
        state = runtime_read.default_manager_state(paths.control_root, paths.team_dir)
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=False, updated_at=""),
        )

    try:
        json.loads(paths.manager_state_file.read_text(encoding="utf-8"))
        state = runtime_read.load_manager_state(paths.manager_state_file, paths.control_root, paths.team_dir)
        _LAST_GOOD_MANAGER_STATE[key] = state
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at),
        )
    except Exception as exc:
        cached = _LAST_GOOD_MANAGER_STATE.get(key)
        if isinstance(cached, dict):
            return ManagerStateLoadResult(
                state=cached,
                freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc)),
            )
        state = runtime_read.default_manager_state(paths.control_root, paths.team_dir)
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc)),
        )
