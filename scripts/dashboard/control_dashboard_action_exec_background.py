#!/usr/bin/env python3
"""Background queue execution bridge for dashboard mutation actions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import aoe_tg_background_runs as background_runs
import aoe_tg_ops_policy as ops_policy

from control_dashboard_action_exec_shared import _dashboard_paths, _json, _load_dashboard_manager_state
from control_dashboard_common import DashboardAppConfig, _not_found_json


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _execute_background_queue_clean_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    if not project_ref:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message="runtime not found: -")

    paths, manager_state = _load_dashboard_manager_state(config)
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = None
    alias_token = project_ref.upper()
    for key, candidate in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        alias = ops_policy.project_alias(candidate, str(key)).upper()
        if alias == alias_token or str(key).strip() == project_ref:
            entry = candidate
            break
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
