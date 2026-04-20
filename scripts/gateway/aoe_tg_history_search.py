#!/usr/bin/env python3
"""Read-only history search helpers for Telegram operator recovery workflows."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aoe_tg_action_audit import (
    normalize_planning_handoff_snapshot,
    summarize_action_audit_headline,
    summarize_retry_replan_planning_compact_handoff,
)
from aoe_tg_operator_summary import load_latest_command_resolution
from aoe_tg_planning_compact_compat import legacy_planning_review_summary_alias
from aoe_tg_project_state import project_alias_for_key
from aoe_tg_runtime_core import (
    action_audit_path as runtime_action_audit_path,
    latest_intent_snapshot_path as runtime_latest_intent_snapshot_path,
    recovery_summary_dir as runtime_recovery_summary_dir,
)
from aoe_tg_task_view import planning_compact_operator_summary, task_display_label


HISTORY_USAGE = (
    "usage: /history search <query> [--project O#|name] [--since 12h] "
    "[--limit N] [--scope control|runtime|task|dashboard|recovery|all]"
)

_SCOPE_VALUES = {"control", "runtime", "task", "dashboard", "recovery", "all"}
_SOURCE_PRIORITY = {
    "dashboard": 5,
    "task": 4,
    "runtime": 3,
    "control": 2,
    "recovery": 1,
}


@dataclass
class HistorySearchOptions:
    query: str
    project_filter: str = ""
    since_seconds: int = 0
    since_label: str = ""
    limit: int = 8
    scope: str = "all"


@dataclass
class HistoryRow:
    at: str
    scope: str
    source: str
    project_alias: str = ""
    project_key: str = ""
    request_id: str = ""
    task_short_id: str = ""
    task_title: str = ""
    action: str = ""
    intent_action: str = ""
    reason_code: str = ""
    phase: str = ""
    status: str = ""
    summary: str = ""
    detail: str = ""
    planning_compact_summary: str = ""
    subagent_contract_summary: str = ""
    subagent_evidence_summary: str = ""
    subagent_artifact_path: str = ""
    approved_plan_summary: str = ""
    followup_hint: str = ""
    raw_ref: str = ""

    @property
    def planning_review_summary(self) -> str:
        return legacy_planning_review_summary_alias(self.planning_compact_summary)


def _normalize_text(raw: Any) -> str:
    return " ".join(str(raw or "").strip().split())


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _action_audit_debug_handoff_detail(row: Dict[str, Any]) -> str:
    handoff = normalize_planning_handoff_snapshot(row.get("planning_handoff"), row=row)
    if not handoff:
        return ""
    debug_packet = handoff.get("debug_packet") if isinstance(handoff.get("debug_packet"), dict) else {}
    if not debug_packet:
        return ""
    parts: List[str] = []
    state = str(debug_packet.get("state", "")).strip()
    symptom = str(debug_packet.get("symptom", "")).strip()
    failed_attempt = str(debug_packet.get("failed_attempt", "")).strip()
    next_step = str(debug_packet.get("next_step", "")).strip()
    if state and state != "-":
        parts.append(f"debug={state}")
    if symptom and symptom != "-":
        parts.append(f"symptom={symptom}")
    if failed_attempt and failed_attempt != "-":
        parts.append(f"attempt={failed_attempt}")
    if next_step and next_step != "-":
        parts.append(f"next={next_step}")
    return " | ".join(parts)


def _action_audit_debug_handoff_summary(row: Dict[str, Any]) -> str:
    handoff = normalize_planning_handoff_snapshot(row.get("planning_handoff"), row=row)
    if not handoff:
        return ""
    debug_packet = handoff.get("debug_packet") if isinstance(handoff.get("debug_packet"), dict) else {}
    if not debug_packet:
        return ""
    parts: List[str] = []
    state = str(debug_packet.get("state", "")).strip()
    symptom = str(debug_packet.get("symptom", "")).strip()
    failed_attempt = str(debug_packet.get("failed_attempt", "")).strip()
    if state and state != "-":
        parts.append(f"debug={state}")
    if symptom and symptom != "-":
        parts.append(f"symptom={symptom}")
    elif failed_attempt and failed_attempt != "-":
        parts.append(f"attempt={failed_attempt}")
    return " | ".join(parts)


def _action_audit_approved_plan_handoff_detail(row: Dict[str, Any]) -> str:
    handoff = normalize_planning_handoff_snapshot(row.get("planning_handoff"), row=row)
    if not handoff:
        return ""
    approved_plan = handoff.get("approved_plan") if isinstance(handoff.get("approved_plan"), dict) else {}
    if not approved_plan:
        return ""
    summary = str(approved_plan.get("summary", "")).strip()
    if summary and summary != "-":
        return summary
    status = str(approved_plan.get("status", "")).strip()
    return f"approved_plan={status}" if status and status != "-" else ""


def _action_audit_approved_plan_handoff_summary(row: Dict[str, Any]) -> str:
    handoff = normalize_planning_handoff_snapshot(row.get("planning_handoff"), row=row)
    if not handoff:
        return ""
    approved_plan = handoff.get("approved_plan") if isinstance(handoff.get("approved_plan"), dict) else {}
    if not approved_plan:
        return ""
    status = str(approved_plan.get("status", "")).strip().lower()
    if status in {"", "-", "approved"}:
        return ""
    summary = str(approved_plan.get("summary", "")).strip() or f"approved_plan={status}"
    return _compact_detail(summary, 120)


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
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


def _parse_since_seconds(raw: str) -> int:
    token = str(raw or "").strip().lower()
    if not token:
        return 0
    try:
        if token.isdigit():
            return max(0, int(token))
        unit = token[-1]
        value = int(token[:-1])
    except Exception:
        return 0
    if value <= 0:
        return 0
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    return 0


def _history_target(row: HistoryRow) -> str:
    parts: List[str] = []
    if row.project_alias:
        parts.append(row.project_alias)
    if row.task_short_id:
        parts.append(row.task_short_id)
    elif row.request_id:
        parts.append(row.request_id)
    return " ".join(parts) or "-"


def _compact_detail(raw: Any, limit: int = 160) -> str:
    text = _normalize_text(raw)
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def parse_history_search_options(rest: Any) -> HistorySearchOptions:
    text = str(rest or "").strip()
    if not text:
        raise RuntimeError(HISTORY_USAGE)
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise RuntimeError(f"invalid history search syntax: {exc}") from exc
    if not tokens:
        raise RuntimeError(HISTORY_USAGE)
    if str(tokens[0]).strip().lower() == "search":
        tokens = tokens[1:]
    if not tokens:
        raise RuntimeError(HISTORY_USAGE)

    project_filter = ""
    since_seconds = 0
    since_label = ""
    limit = 8
    scope = "all"
    query_tokens: List[str] = []

    i = 0
    while i < len(tokens):
        token = str(tokens[i] or "").strip()
        low = token.lower()
        if low == "--project":
            i += 1
            if i >= len(tokens):
                raise RuntimeError(HISTORY_USAGE)
            project_filter = str(tokens[i] or "").strip()
        elif low.startswith("--project="):
            project_filter = token.split("=", 1)[1].strip()
        elif low == "--since":
            i += 1
            if i >= len(tokens):
                raise RuntimeError(HISTORY_USAGE)
            since_label = str(tokens[i] or "").strip()
            since_seconds = _parse_since_seconds(since_label)
            if since_seconds <= 0:
                raise RuntimeError("invalid --since value (use 30m, 12h, 3d)")
        elif low.startswith("--since="):
            since_label = token.split("=", 1)[1].strip()
            since_seconds = _parse_since_seconds(since_label)
            if since_seconds <= 0:
                raise RuntimeError("invalid --since value (use 30m, 12h, 3d)")
        elif low == "--limit":
            i += 1
            if i >= len(tokens):
                raise RuntimeError(HISTORY_USAGE)
            try:
                limit = max(1, min(50, int(tokens[i])))
            except Exception as exc:
                raise RuntimeError("invalid --limit value") from exc
        elif low.startswith("--limit="):
            try:
                limit = max(1, min(50, int(token.split("=", 1)[1].strip())))
            except Exception as exc:
                raise RuntimeError("invalid --limit value") from exc
        elif low == "--scope":
            i += 1
            if i >= len(tokens):
                raise RuntimeError(HISTORY_USAGE)
            scope = str(tokens[i] or "").strip().lower()
            if scope not in _SCOPE_VALUES:
                raise RuntimeError("invalid --scope value")
        elif low.startswith("--scope="):
            scope = token.split("=", 1)[1].strip().lower()
            if scope not in _SCOPE_VALUES:
                raise RuntimeError("invalid --scope value")
        else:
            query_tokens.append(token)
        i += 1

    query = _normalize_text(" ".join(query_tokens))
    if not query:
        raise RuntimeError(HISTORY_USAGE)
    return HistorySearchOptions(
        query=query,
        project_filter=project_filter,
        since_seconds=since_seconds,
        since_label=since_label,
        limit=limit,
        scope=scope,
    )


def _manager_indexes(manager_state: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    projects = manager_state.get("projects")
    if not isinstance(projects, dict):
        return {}, {}
    project_index: Dict[str, Dict[str, str]] = {}
    task_index: Dict[str, Dict[str, str]] = {}
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        project_key = str(key or "").strip()
        project_alias = project_alias_for_key(manager_state, project_key) or str(entry.get("project_alias", "")).strip()
        project_index[project_key] = {
            "project_alias": project_alias,
            "project_key": project_key,
            "project_label": str(entry.get("display_name", "")).strip() or project_key,
        }
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        for request_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            rid = str(request_id or "").strip()
            if not rid:
                continue
            task_index[rid] = {
                "project_alias": project_alias,
                "project_key": project_key,
                "task_short_id": str(task.get("short_id", "")).strip().upper(),
                "task_title": _compact_detail(str(task.get("prompt", "")).strip() or str(task.get("alias", "")).strip(), 120),
            }
    return project_index, task_index


def _project_matches(project_filter: str, row: HistoryRow) -> bool:
    token = str(project_filter or "").strip().lower()
    if not token:
        return True
    return token in {
        str(row.project_alias or "").strip().lower(),
        str(row.project_key or "").strip().lower(),
    }


def _row_text(row: HistoryRow) -> str:
    fields = (
        row.request_id,
        row.task_short_id,
        row.task_title,
        row.project_alias,
        row.project_key,
        row.action,
        row.intent_action,
        row.reason_code,
        row.phase,
        row.status,
        row.summary,
        row.detail,
    )
    return " ".join(_normalize_text(item).lower() for item in fields if _normalize_text(item))


def _scope_for_event(row: Dict[str, Any]) -> str:
    request_id = str(row.get("request_id", "")).strip()
    project = str(row.get("project", "")).strip()
    event = str(row.get("event", "")).strip().lower()
    if request_id:
        return "task"
    if event in {"command_resolved", "send_message", "input_rejected", "auth_denied"}:
        return "control"
    if project:
        return "runtime"
    return "control"


def _extract_reason_code(detail: Any, error_code: Any = "") -> str:
    error_token = str(error_code or "").strip()
    if error_token:
        return error_token
    text = str(detail or "").strip()
    for marker in ("reason=", "error_code=", "code="):
        if marker in text:
            tail = text.split(marker, 1)[1].strip()
            token = tail.split()[0].strip(" ,;|")
            if token:
                return token
    return ""


def _gateway_event_rows(
    *,
    team_dir: Path,
    project_index: Dict[str, Dict[str, str]],
    task_index: Dict[str, Dict[str, str]],
) -> List[HistoryRow]:
    path = team_dir / "logs" / "gateway_events.jsonl"
    if not path.exists():
        return []
    rows: List[HistoryRow] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parsed = _safe_json_loads(str(line or "").strip())
                if not parsed:
                    continue
                request_id = str(parsed.get("request_id", "")).strip()
                project_key = str(parsed.get("project", "")).strip()
                meta = task_index.get(request_id, {})
                project_meta = project_index.get(project_key, {})
                project_alias = str(meta.get("project_alias") or project_meta.get("project_alias") or "").strip()
                task_short_id = str(parsed.get("task_short_id", "")).strip().upper() or str(meta.get("task_short_id", "")).strip().upper()
                summary = "{event} | {status}".format(
                    event=str(parsed.get("event", "")).strip() or "-",
                    status=str(parsed.get("status", "")).strip() or "-",
                )
                detail = _normalize_text(parsed.get("detail", ""))
                if detail:
                    summary = f"{summary} | {_compact_detail(detail, 120)}"
                hint = ""
                if task_short_id:
                    hint = f"/task {task_short_id}"
                elif project_alias:
                    hint = f"/monitor {project_alias}"
                elif str(parsed.get("event", "")).strip() == "command_resolved":
                    hint = "/offdesk review"
                rows.append(
                    HistoryRow(
                        at=str(parsed.get("timestamp", "")).strip(),
                        scope=_scope_for_event(parsed),
                        source="gateway_events",
                        project_alias=project_alias,
                        project_key=project_key,
                        request_id=request_id,
                        task_short_id=task_short_id,
                        task_title=str(meta.get("task_title", "")).strip(),
                        action=str(parsed.get("event", "")).strip(),
                        reason_code=_extract_reason_code(detail, parsed.get("error_code", "")),
                        phase=str(parsed.get("stage", "")).strip(),
                        status=str(parsed.get("status", "")).strip(),
                        summary=summary,
                        detail=detail,
                        followup_hint=hint,
                        raw_ref=f"{path}:{str(parsed.get('trace_id', '')).strip() or str(parsed.get('timestamp', '')).strip()}",
                    )
                )
    except Exception:
        return []
    return rows


def _action_audit_rows(
    *,
    team_dir: Path,
    task_index: Dict[str, Dict[str, str]],
) -> List[HistoryRow]:
    path = runtime_action_audit_path(team_dir)
    if not path.exists():
        return []
    rows: List[HistoryRow] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                parsed = _safe_json_loads(str(line or "").strip())
                if not parsed:
                    continue
                request_id = ""
                link_href = str(parsed.get("link_href", "")).strip()
                marker = "/control/tasks/by-request/"
                if marker in link_href:
                    request_id = link_href.split(marker, 1)[1].strip()
                meta = task_index.get(request_id, {})
                project_alias = str(meta.get("project_alias", "")).strip()
                task_short_id = str(meta.get("task_short_id", "")).strip().upper()
                reason_code = str(parsed.get("outcome_reason_code", "")).strip()
                summary = _normalize_text(summarize_action_audit_headline(parsed))
                if not summary:
                    summary = _normalize_text(parsed.get("headline", ""))
                if summary and reason_code and "reason=" not in summary:
                    summary = f"{summary} | reason={reason_code}"
                debug_handoff_detail = _action_audit_debug_handoff_detail(parsed)
                planning_compact_summary = _normalize_text(
                    summarize_retry_replan_planning_compact_handoff(parsed.get("planning_handoff"), row=parsed)
                )
                if planning_compact_summary in {"", "-"}:
                    planning_compact_summary = _normalize_text(str(parsed.get("planning_compact_summary") or parsed.get("planning_compact")))
                approved_plan_handoff_summary = _action_audit_approved_plan_handoff_summary(parsed)
                planning_compact_summary = _normalize_text(
                    planning_compact_operator_summary(
                        planning_compact=planning_compact_summary,
                        approved_plan=approved_plan_handoff_summary,
                    )
                )
                approved_plan_handoff_detail = _action_audit_approved_plan_handoff_detail(parsed)
                subagent_contract_summary = _normalize_text(
                    str(parsed.get("subagent_contract_summary") or parsed.get("general_subagent_summary") or "")
                )
                subagent_evidence_summary = _normalize_text(
                    str(parsed.get("subagent_evidence_summary") or parsed.get("general_subagent_artifact_summary") or "")
                )
                subagent_artifact_path = _normalize_text(
                    str(parsed.get("subagent_artifact_path") or parsed.get("general_subagent_artifact_path") or "")
                )
                detail = _normalize_text(
                    " ".join(
                        str(item).strip()
                        for item in (
                            parsed.get("outcome_detail", ""),
                            "" if debug_handoff_detail and debug_handoff_detail in str(parsed.get("outcome_detail", "")) else debug_handoff_detail,
                            ""
                            if approved_plan_handoff_detail
                            and approved_plan_handoff_detail in str(parsed.get("outcome_detail", ""))
                            else approved_plan_handoff_detail,
                            parsed.get("remediation", ""),
                            parsed.get("source_command", ""),
                        )
                        if str(item).strip()
                    )
                )
                rows.append(
                    HistoryRow(
                        at=str(parsed.get("at", "")).strip(),
                        scope="dashboard",
                        source="action_audit",
                        project_alias=project_alias,
                        project_key=str(meta.get("project_key", "")).strip(),
                        request_id=request_id,
                        task_short_id=task_short_id,
                        task_title=str(meta.get("task_title", "")).strip(),
                        action=str(parsed.get("source_command", "")).strip() or summary,
                        reason_code=reason_code,
                        status=str(parsed.get("status", "")).strip() or str(parsed.get("outcome_status", "")).strip(),
                        summary=summary or "-",
                        detail=detail,
                        planning_compact_summary=planning_compact_summary or "",
                        subagent_contract_summary=subagent_contract_summary or "",
                        subagent_evidence_summary=subagent_evidence_summary or "",
                        subagent_artifact_path=subagent_artifact_path or "",
                        approved_plan_summary=approved_plan_handoff_summary or "",
                        followup_hint=str(parsed.get("next_step", "")).strip() or (f"/task {task_short_id}" if task_short_id else ""),
                        raw_ref=f"{path}:{str(parsed.get('at', '')).strip()}",
                    )
                )
    except Exception:
        return []
    return rows


def _nightly_summary_rows(team_dir: Path) -> List[HistoryRow]:
    root = runtime_recovery_summary_dir(team_dir)
    if not root.exists():
        return []
    rows: List[HistoryRow] = []
    for path in sorted(root.glob("*.json"), reverse=True):
        parsed = _safe_json_loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not parsed:
            continue
        generated_at = str(parsed.get("generated_at", "")).strip() or path.stem
        runtimes = parsed.get("runtimes")
        if not isinstance(runtimes, list):
            continue
        for runtime in runtimes:
            if not isinstance(runtime, dict):
                continue
            project_alias = str(runtime.get("project_alias", "")).strip()
            project_key = str(runtime.get("project_key", "")).strip()
            summary = "nightly runtime | {status} | {attention}".format(
                status=str(runtime.get("status", "")).strip() or "-",
                attention=str(runtime.get("attention_summary", "")).strip() or "-",
            )
            rows.append(
                HistoryRow(
                    at=generated_at,
                    scope="recovery",
                    source="nightly_summary",
                    project_alias=project_alias,
                    project_key=project_key,
                    action="nightly_summary",
                    status=str(runtime.get("status", "")).strip(),
                    summary=summary,
                    detail=_normalize_text(
                        " ".join(
                            str(item).strip()
                            for item in (
                                runtime.get("priority_reason", ""),
                                runtime.get("next_focus", ""),
                                runtime.get("provider_pressure_summary", ""),
                            )
                            if str(item).strip()
                        )
                    ),
                    followup_hint=str(runtime.get("priority_action", "")).strip() or (f"/monitor {project_alias}" if project_alias else "/offdesk review"),
                    raw_ref=str(path),
                )
            )
            task_teams = runtime.get("task_teams")
            if not isinstance(task_teams, list):
                continue
            for task in task_teams:
                if not isinstance(task, dict):
                    continue
                request_id = str(task.get("request_id", "")).strip()
                label = str(task.get("label", "")).strip()
                rows.append(
                    HistoryRow(
                        at=generated_at,
                        scope="recovery",
                        source="nightly_summary",
                        project_alias=project_alias,
                        project_key=project_key,
                        request_id=request_id,
                        task_short_id=label.split("|", 1)[0].strip() if "|" in label else "",
                        task_title=label,
                        action="nightly_task",
                        reason_code="",
                        phase=str(task.get("tf_phase", "")).strip(),
                        status=str(task.get("status", "")).strip(),
                        summary="nightly task | {label} | {status}/{phase}".format(
                            label=label or request_id or "-",
                            status=str(task.get("status", "")).strip() or "-",
                            phase=str(task.get("tf_phase", "")).strip() or "-",
                        ),
                        detail=_normalize_text(
                            " ".join(
                                str(item).strip()
                                for item in (
                                    task.get("lane_summary", ""),
                                    task.get("rerun_summary", ""),
                                    task.get("followup_summary", ""),
                                    (task.get("completion_contract") or {}).get("focus", "") if isinstance(task.get("completion_contract"), dict) else "",
                                )
                                if str(item).strip()
                            )
                        ),
                        followup_hint=(task.get("operator_hints") or [""])[0] if isinstance(task.get("operator_hints"), list) else "",
                        raw_ref=str(path),
                    )
                )
    return rows


def _latest_intent_rows(team_dir: Path) -> List[HistoryRow]:
    latest = load_latest_command_resolution(team_dir)
    if not latest:
        return []
    path = runtime_latest_intent_snapshot_path(team_dir)
    return [
        HistoryRow(
            at=str(latest.get("recorded_at", "")).strip(),
            scope="control",
            source="latest_intent",
            action=str(latest.get("command", "")).strip(),
            intent_action=str(latest.get("action", "")).strip(),
            status="accepted",
            summary="latest intent | {command} | {action}".format(
                command=str(latest.get("command", "")).strip() or "-",
                action=str(latest.get("action", "")).strip() or "-",
            ),
            detail=_normalize_text(
                " ".join(
                    str(item).strip() for item in (latest.get("trace", ""), latest.get("focus", "")) if str(item).strip()
                )
            ),
            followup_hint="/offdesk review",
            raw_ref=str(path),
        )
    ]


def _manager_state_rows(
    *,
    manager_state: Dict[str, Any],
    project_index: Dict[str, Dict[str, str]],
) -> List[HistoryRow]:
    rows: List[HistoryRow] = []
    projects = manager_state.get("projects")
    if not isinstance(projects, dict):
        return rows
    for project_key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        project_meta = project_index.get(str(project_key), {})
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        for request_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            task_short_id = str(task.get("short_id", "")).strip().upper()
            rows.append(
                HistoryRow(
                    at=str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip(),
                    scope="task",
                    source="manager_state",
                    project_alias=str(project_meta.get("project_alias", "")).strip(),
                    project_key=str(project_key).strip(),
                    request_id=str(request_id).strip(),
                    task_short_id=task_short_id,
                    task_title=_compact_detail(str(task.get("prompt", "")).strip() or str(task.get("alias", "")).strip(), 120),
                    action="task_state",
                    reason_code="",
                    phase=str(task.get("tf_phase", "")).strip(),
                    status=str(task.get("status", "")).strip(),
                    summary="current task state | {label} | {status}".format(
                        label=task_display_label(task, fallback_request_id=str(request_id).strip()),
                        status=str(task.get("status", "")).strip() or "-",
                    ),
                    detail=_normalize_text(
                        " ".join(
                            str(item).strip()
                            for item in (
                                task.get("tf_phase_reason", ""),
                                task.get("rate_limit", ""),
                                task.get("backend_note", ""),
                            )
                            if str(item).strip()
                        )
                    ),
                    followup_hint=f"/task {task_short_id}" if task_short_id else "/offdesk review",
                    raw_ref=f"orch_manager_state:{request_id}",
                )
            )
    return rows


def load_history_rows(*, team_dir: Path, manager_state: Dict[str, Any]) -> List[HistoryRow]:
    project_index, task_index = _manager_indexes(manager_state)
    rows: List[HistoryRow] = []
    rows.extend(_gateway_event_rows(team_dir=team_dir, project_index=project_index, task_index=task_index))
    rows.extend(_action_audit_rows(team_dir=team_dir, task_index=task_index))
    rows.extend(_nightly_summary_rows(team_dir))
    rows.extend(_latest_intent_rows(team_dir))
    rows.extend(_manager_state_rows(manager_state=manager_state, project_index=project_index))
    return rows


def search_history_rows(
    *,
    team_dir: Path | str,
    manager_state: Dict[str, Any],
    options: HistorySearchOptions,
) -> List[HistoryRow]:
    return _filter_rows(
        load_history_rows(team_dir=Path(str(team_dir)).expanduser().resolve(), manager_state=manager_state),
        options=options,
    )


def _filter_rows(rows: Iterable[HistoryRow], *, options: HistorySearchOptions) -> List[HistoryRow]:
    out: List[HistoryRow] = []
    query = options.query.lower().strip()
    cutoff: Optional[datetime] = None
    if options.since_seconds > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=options.since_seconds)
    for row in rows:
        if options.scope != "all" and row.scope != options.scope:
            continue
        if not _project_matches(options.project_filter, row):
            continue
        row_dt = _parse_iso_dt(row.at)
        if cutoff is not None and row_dt is not None and row_dt < cutoff:
            continue
        if query and query not in _row_text(row):
            continue
        out.append(row)
    out.sort(
        key=lambda row: (
            _parse_iso_dt(row.at) or datetime.fromtimestamp(0, tz=timezone.utc),
            _SOURCE_PRIORITY.get(row.scope, 0),
        ),
        reverse=True,
    )
    return out[: options.limit]


def render_history_search(
    *,
    team_dir: Path | str,
    manager_state: Dict[str, Any],
    rest: Any,
) -> str:
    options = parse_history_search_options(rest)
    rows = search_history_rows(team_dir=team_dir, manager_state=manager_state, options=options)
    if not rows:
        filters: List[str] = []
        if options.project_filter:
            filters.append(f"project={options.project_filter}")
        if options.since_label:
            filters.append(f"since={options.since_label}")
        if options.scope != "all":
            filters.append(f"scope={options.scope}")
        filter_text = ", ".join(filters) if filters else "-"
        return (
            "history search\n"
            f"- query: {options.query}\n"
            f"- filters: {filter_text}\n"
            "- matches: 0\n"
            "next:\n"
            "- /offdesk review\n"
            "- /auto status\n"
            "- /task <T-xxx>"
        )

    lines = [
        "history search",
        f"- query: {options.query}",
        f"- matches: {len(rows)}",
    ]
    if options.project_filter:
        lines.append(f"- project: {options.project_filter}")
    if options.since_label:
        lines.append(f"- since: {options.since_label}")
    if options.scope != "all":
        lines.append(f"- scope: {options.scope}")
    for idx, row in enumerate(rows, start=1):
        lines.append(f"{idx}. {row.at or '-'} | {row.scope} | {_history_target(row)} | {row.summary or '-'}")
        if row.reason_code:
            lines.append(f"   reason: {row.reason_code}")
        if row.followup_hint:
            lines.append(f"   next: {row.followup_hint}")
    return "\n".join(lines)
