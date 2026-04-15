#!/usr/bin/env python3
"""Background queue execution bridge for dashboard mutation actions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import aoe_tg_background_runs as background_runs
import aoe_tg_ops_policy as ops_policy

from control_dashboard_action_exec_shared import _json, _load_dashboard_manager_state
from control_dashboard_common import DashboardAppConfig, _not_found_json
from control_dashboard_state import load_dashboard_snapshot


def _server_guard_pressure_preview_payload(
    *,
    spec: Dict[str, object],
    payload: Dict[str, object],
    pressure_kind: str,
    snapshot,
    matching_reasons: list[str],
    next_step: str,
    remediation: str,
    note: str,
    outcome_kind: str,
) -> Dict[str, object]:
    guard = snapshot.control_summary.server_guard
    links = {
        "codex": [
            {"label": "Open Chat Console", "href": "/control/chat"},
            {"label": "Open Codex History", "href": "/control/history?q=codex&scope=control"},
            {"label": "Open Server Guard Audit", "href": "/control/audit?focus=server-guard"},
            {"label": "Open Health JSON", "href": "/control/health"},
        ],
        "python": [
            {"label": "Open Recovery", "href": "/control/recovery"},
            {"label": "Open Python History", "href": "/control/history?q=python&scope=control"},
            {"label": "Open Offdesk", "href": "/control/offdesk"},
            {"label": "Open Health JSON", "href": "/control/health"},
        ],
        "tmux": [
            {"label": "Open Tmux History", "href": "/control/history?q=tmux&scope=control"},
            {"label": "Open Recovery", "href": "/control/recovery"},
            {"label": "Open Server Guard Audit", "href": "/control/audit?focus=server-guard"},
            {"label": "Open Health JSON", "href": "/control/health"},
        ],
        "process": [
            {"label": "Open Process History", "href": "/control/history?q=process&scope=control"},
            {"label": "Open Recovery", "href": "/control/recovery"},
            {"label": "Open Server Guard Audit", "href": "/control/audit?focus=server-guard"},
            {"label": "Open Health JSON", "href": "/control/health"},
        ],
    }[pressure_kind]
    return {
        "ok": True,
        "implemented": True,
        "executed": False,
        "status": "preview",
        "method": "POST",
        "path": spec.get("path", "-"),
        "mode": spec.get("mode", "-"),
        "source_command": spec.get("command", "-"),
        "payload": payload,
        "next_step": next_step,
        "remediation": remediation,
        "links": links,
        "preview": {
            "kind": outcome_kind,
            "pressure_kind": pressure_kind,
            "server_guard_summary": guard.summary,
            "server_guard_note": guard.note,
            "server_guard_next_step": guard.next_step,
            "reason_summary": guard.reason_summary,
            "matching_reasons": matching_reasons,
            "disk_summary": guard.disk_summary,
            "memory_summary": guard.memory_summary,
            "load_summary": guard.load_summary,
            "process_summary": guard.process_summary,
            "queue_summary": guard.queue_summary,
            "snapshot_path": guard.snapshot_path,
            "note": note,
        },
        "outcome": {
            "kind": outcome_kind,
            "status": "preview",
            "reason_code": matching_reasons[0] if matching_reasons else f"{pressure_kind}_pressure_open",
            "detail": f"pressure_kind={pressure_kind} | reasons={' | '.join(matching_reasons) if matching_reasons else '-'} | process={guard.process_summary}",
        },
    }


def _preview_server_guard_pressure_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    pressure_kind = str(payload.get("pressure_kind", "")).strip().lower()
    if pressure_kind not in {"codex", "python", "tmux", "process"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "next_step": "/control/health",
                "remediation": "select a concrete pressure_kind before previewing server guard pressure",
                "outcome": {
                    "kind": "server_guard_pressure_preview",
                    "status": "blocked",
                    "reason_code": "pressure_kind_missing",
                    "detail": "pressure_kind must be one of codex, python, tmux, process",
                },
            },
            status=400,
        )

    snapshot = load_dashboard_snapshot(
        control_root=config.control_root,
        team_dir=config.team_dir,
        manager_state_file=config.manager_state_file,
    )
    guard = snapshot.control_summary.server_guard
    reasons = [token.strip() for token in str(guard.reason_summary or "").split("|") if token.strip()]
    prefixes = {
        "codex": ("codex_process",),
        "python": ("python_process",),
        "tmux": ("tmux_process",),
        "process": ("total_process", "process"),
    }[pressure_kind]
    matching_reasons = [reason for reason in reasons if any(reason.startswith(prefix) for prefix in prefixes)]
    next_step = {
        "codex": "/control/chat",
        "python": "/control/recovery",
        "tmux": "/control/history?q=tmux&scope=control",
        "process": "/control/history?q=process&scope=control",
    }[pressure_kind]
    note = {
        "codex": "inspect duplicated chat sessions and interactive codex runs before opening more operator surfaces",
        "python": "inspect local worker churn and queue pressure before launching more python-backed runs",
        "tmux": "inspect detached runtime handles and stale tmux sessions before starting more off-desk workers",
        "process": "inspect broad process churn before increasing runtime fanout",
    }[pressure_kind]
    remediation = {
        "codex": "consolidate chat sessions, trim duplicate codex loops, and keep new work in the chat manager rail until pressure drops",
        "python": "reduce local worker churn, inspect queue buildup, and prefer recovery/offdesk review before relaunching python-backed work",
        "tmux": "inspect detached runtime handles and stale tmux workers before starting additional detached runs",
        "process": "review global process churn and recovery surfaces before increasing concurrency",
    }[pressure_kind]
    outcome_kind = {
        "codex": "codex_process_pressure_preview",
        "python": "python_process_pressure_preview",
        "tmux": "tmux_process_pressure_preview",
        "process": "process_pressure_preview",
    }[pressure_kind]
    return _json(
        _server_guard_pressure_preview_payload(
            spec=spec,
            payload=payload,
            pressure_kind=pressure_kind,
            snapshot=snapshot,
            matching_reasons=matching_reasons,
            next_step=next_step,
            remediation=remediation,
            note=note,
            outcome_kind=outcome_kind,
        ),
        status=200,
    )


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _resolve_background_queue_runtime_entry(
    project_ref: str,
    *,
    config: DashboardAppConfig,
) -> Dict[str, object] | None:
    _paths, manager_state = _load_dashboard_manager_state(config)
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    alias_token = project_ref.upper()
    for key, candidate in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        alias = ops_policy.project_alias(candidate, str(key)).upper()
        if alias == alias_token or str(key).strip() == project_ref:
            return candidate
    return None


def _background_queue_preview_payload(
    *,
    spec: Dict[str, object],
    payload: Dict[str, object],
    entry: Dict[str, object],
    queue_path: Path,
    before: Dict[str, object],
) -> Dict[str, object]:
    project_ref = str(payload.get("project_ref", "")).strip()
    stale_count = int(before.get("stale_count", 0) or 0)
    return {
        "ok": True,
        "implemented": True,
        "executed": False,
        "status": "preview",
        "method": "POST",
        "path": spec.get("path", "-"),
        "mode": spec.get("mode", "-"),
        "source_command": spec.get("command", "-"),
        "payload": payload,
        "next_step": f"/orch status {project_ref}",
        "remediation": (
            "run background queue cleanup only after confirming stale tickets belong to abandoned worker sessions"
            if stale_count > 0
            else "queue is already current; inspect runtime detail before launching more detached work"
        ),
        "preview": {
            "kind": "background_queue_cleanup_preview",
            "project_alias": ops_policy.project_alias(entry, str(entry.get("name", ""))).upper() or project_ref.upper(),
            "runtime_path": f"/control/runtimes/{project_ref}",
            "queue_path": str(queue_path),
            "before": before,
        },
        "outcome": {
            "kind": "background_queue_cleanup_preview",
            "status": "preview",
            "reason_code": "stale_present" if stale_count > 0 else "queue_current",
            "detail": f"stale_count={stale_count} | summary={before.get('summary', '-')}",
        },
    }


def _preview_background_queue_clean_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    if not project_ref:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message="runtime not found: -")

    entry = _resolve_background_queue_runtime_entry(project_ref, config=config)
    if not isinstance(entry, dict):
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"runtime not found: {project_ref}")

    team_dir_raw = str(entry.get("team_dir", "")).strip()
    if not team_dir_raw:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "next_step": f"/orch status {project_ref}",
                "remediation": "repair the runtime first; background queue cleanup requires a concrete team_dir",
                "outcome": {
                    "kind": "background_queue_cleanup",
                    "status": "blocked",
                    "reason_code": "team_dir_missing",
                    "detail": "runtime is missing team_dir",
                },
            },
            status=409,
        )

    queue_path = background_runs.background_runs_state_path(Path(team_dir_raw))
    before = background_runs.summarize_background_runs_state(queue_path)
    return _json(_background_queue_preview_payload(spec=spec, payload=payload, entry=entry, queue_path=queue_path, before=before), status=200)


def _execute_background_queue_clean_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    if not project_ref:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message="runtime not found: -")

    entry = _resolve_background_queue_runtime_entry(project_ref, config=config)
    if not isinstance(entry, dict):
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"runtime not found: {project_ref}")

    team_dir_raw = str(entry.get("team_dir", "")).strip()
    if not team_dir_raw:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "next_step": f"/orch status {project_ref}",
                "remediation": "repair the runtime first; background queue cleanup requires a concrete team_dir",
                "outcome": {
                    "kind": "background_queue_cleanup",
                    "status": "blocked",
                    "reason_code": "team_dir_missing",
                    "detail": "runtime is missing team_dir",
                },
            },
            status=409,
        )

    queue_path = background_runs.background_runs_state_path(Path(team_dir_raw))
    before = background_runs.summarize_background_runs_state(queue_path)
    marked = background_runs.mark_stale_background_run_tickets(queue_path, now_iso=_now_iso)
    after = background_runs.summarize_background_runs_state(queue_path)
    changed = bool(marked.get("changed"))
    stale_count = int(marked.get("stale_count", 0) or 0)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "next_step": f"/orch status {project_ref}",
            "remediation": (
                "inspect runtime queue depth and stale tickets before relaunching detached off-desk work"
                if stale_count > 0
                else "queue state is already current; inspect runtime detail before launching more detached work"
            ),
            "preview": {
                "kind": "background_queue_cleanup",
                "project_alias": ops_policy.project_alias(entry, str(entry.get("name", ""))).upper() or project_ref.upper(),
                "runtime_path": f"/control/runtimes/{project_ref}",
                "queue_path": str(queue_path),
                "before": before,
                "after": after,
            },
            "outcome": {
                "kind": "background_queue_cleanup",
                "status": "executed",
                "reason_code": "stale_marked" if changed and stale_count > 0 else "noop",
                "detail": f"marked_stale={stale_count} | before={before.get('summary', '-')} | after={after.get('summary', '-')}",
            },
        },
        status=200,
    )
