#!/usr/bin/env python3
"""Read-only helpers for dashboard action audit state."""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from aoe_tg_runtime_core import action_audit_path as runtime_action_audit_path

ACTION_AUDIT_DIRNAME = "dashboard"
ACTION_AUDIT_FILENAME = "action-history.jsonl"


def _action_audit_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


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
    return runtime_action_audit_path(token)


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


def _parse_json_object_from_text(text: Any) -> Dict[str, Any]:
    src = str(text or "").strip()
    if not src:
        return {}
    try:
        obj = json.loads(src)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(src):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(src[idx:])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _judge_recommended_action(next_step: str, verdict: str) -> str:
    step = str(next_step or "").strip().lower()
    if step.startswith("/replan "):
        return "replan"
    if step.startswith("/retry "):
        return "retry"
    if step.startswith("/followup-exec "):
        return "followup_execute"
    if step.startswith("/offdesk review") or step.startswith("/orch judge "):
        return "manual_review"
    token = str(verdict or "").strip().lower()
    if token in {"continue", "retry", "replan", "escalate", "hold"}:
        return token
    return "review"


def normalize_offdesk_judge_decision(raw: Any) -> Dict[str, str]:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return {}
    verdict = str(row.get("verdict", "")).strip() or "-"
    confidence = str(row.get("confidence", "")).strip() or "-"
    reasoning = str(row.get("reasoning", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    caution = str(row.get("caution", "")).strip() or "-"
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
        "next_step": next_step,
        "caution": caution,
        "recommended_action": _judge_recommended_action(next_step, verdict),
    }


def summarize_offdesk_judge_decision(decision: Any) -> str:
    row = decision if isinstance(decision, dict) else normalize_offdesk_judge_decision(decision)
    if not isinstance(row, dict) or not row:
        return "-"
    action = str(row.get("recommended_action", "")).strip() or "-"
    verdict = str(row.get("verdict", "")).strip() or "-"
    confidence = str(row.get("confidence", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    reasoning = str(row.get("reasoning", "")).strip() or "-"
    parts = [f"action={action}", f"verdict={verdict}"]
    if confidence != "-":
        parts.append(f"confidence={confidence}")
    if next_step != "-":
        parts.append(f"next={next_step}")
    if reasoning != "-":
        parts.append(reasoning)
    return " | ".join(parts) if parts else "-"


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


def append_action_audit_row(
    team_dir: Any,
    *,
    headline: Any,
    status: Any,
    outcome_kind: Any,
    outcome_status: Any,
    outcome_reason_code: Any,
    outcome_detail: Any,
    next_step: Any,
    remediation: Any,
    source_command: Any,
    link_label: Any = "-",
    link_href: Any = "-",
    at: Any = "",
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    token = str(team_dir or "").strip()
    source = str(source_command or "").strip()
    if not token or not source:
        return False
    path = _action_audit_path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": str(at or "").strip() or _action_audit_now(),
        "headline": str(headline or "").strip() or "-",
        "status": str(status or "").strip() or "unknown",
        "outcome_kind": str(outcome_kind or "").strip() or "-",
        "outcome_status": str(outcome_status or "").strip() or str(status or "").strip() or "unknown",
        "outcome_reason_code": str(outcome_reason_code or "").strip() or "-",
        "outcome_detail": str(outcome_detail or "").strip() or "-",
        "next_step": str(next_step or "").strip() or "-",
        "remediation": str(remediation or "").strip() or "-",
        "link_label": str(link_label or "").strip() or "-",
        "link_href": str(link_href or "").strip() or "-",
        "source_command": source,
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            token = str(key or "").strip()
            if not token or token in row:
                continue
            row[token] = value
    try:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return False
    return True


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


def load_latest_action_audit_for_runtime_kind(
    team_dir: Any,
    *,
    project_alias: Any,
    outcome_kind: Any,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    kind = str(outcome_kind or "").strip()
    if not alias or not kind:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != kind:
            continue
        normalized = _normalize_latest_action_row(row)
        normalized["at"] = str(row.get("at", "")).strip() or "-"
        return normalized
    return {}


def load_latest_offdesk_judge_decision_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != "offdesk_judge":
            continue
        decision = normalize_offdesk_judge_decision(row.get("decision_snapshot") or row.get("response_text"))
        if not decision:
            continue
        decision["at"] = str(row.get("at", "")).strip() or "-"
        return decision
    return {}


def load_latest_offdesk_judge_decision_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    decision = load_latest_offdesk_judge_decision_for_runtime(team_dir, project_alias=project_alias)
    return summarize_offdesk_judge_decision(decision)


def load_latest_model_ping_audit_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
    kind: Any,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    ping_kind = str(kind or "").strip().lower()
    if not alias or not ping_kind:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    suffix = f" {ping_kind}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != "model_ping":
            continue
        source_command = str(row.get("source_command", "")).strip()
        if not source_command.endswith(suffix):
            continue
        normalized = _normalize_latest_action_row(row)
        normalized["at"] = str(row.get("at", "")).strip() or "-"
        return normalized
    return {}


def prefer_recent_model_ping_probe_summary(
    team_dir: Any,
    *,
    project_alias: Any,
    kind: Any,
    endpoint_id: Any = "",
    probe_status: Any = "",
    probe_summary: Any = "",
) -> str:
    current_status = str(probe_status or "").strip().lower()
    current_summary = str(probe_summary or "").strip() or "-"
    if current_status not in {"probe_timeout", "deferred_live_probe", "unsupported_probe"}:
        return current_summary
    latest_ping = load_latest_model_ping_audit_for_runtime(
        team_dir,
        project_alias=project_alias,
        kind=kind,
    )
    if not latest_ping:
        return current_summary
    if str(latest_ping.get("status", "")).strip().lower() != "executed":
        return current_summary
    detail = str(latest_ping.get("outcome_detail", "")).strip() or "-"
    binding_endpoint_id = str(endpoint_id or "").strip()
    if binding_endpoint_id and f"endpoint={binding_endpoint_id}" not in detail:
        return current_summary
    return f"status=last_invoke_ok | {detail}"


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
