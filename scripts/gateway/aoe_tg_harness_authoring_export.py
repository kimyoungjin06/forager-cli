#!/usr/bin/env python3
"""Export a read-only harness authoring plan from current runtime state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from aoe_tg_harness_authoring_adapter import build_harness_authoring_plan
from aoe_tg_runtime_core import harness_authoring_plan_path, resolve_default_team_dir, resolve_state_file
from aoe_tg_runtime_read import load_manager_state
from aoe_tg_task_state import normalize_task_alias_key


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _find_project_entry(
    manager_state: Dict[str, Any],
    *,
    project_alias: str = "",
    project_key: str = "",
    project_root: Optional[Path] = None,
    team_dir: Optional[Path] = None,
) -> Tuple[str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    alias_token = _trim(project_alias, 32).upper()
    key_token = _trim(project_key, 80)
    root_token = str(project_root.resolve()) if project_root is not None else ""
    team_token = str(team_dir.resolve()) if team_dir is not None else ""
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if key_token and str(key).strip() == key_token:
            return str(key), entry
        if alias_token and _trim(entry.get("project_alias"), 32).upper() == alias_token:
            return str(key), entry
        if root_token and _trim(entry.get("project_root"), 400) == root_token:
            return str(key), entry
        if team_token and _trim(entry.get("team_dir"), 400) == team_token:
            return str(key), entry
    if len(projects) == 1:
        only_key = next(iter(projects))
        only_entry = projects.get(only_key)
        if isinstance(only_entry, dict):
            return str(only_key), only_entry
    raise SystemExit("project entry not found; pass --project-alias or --project-key")


def _find_task(entry: Dict[str, Any], *, request_id: str = "", task_ref: str = "") -> Dict[str, Any]:
    tasks = entry.get("tasks") if isinstance(entry.get("tasks"), dict) else {}
    req_token = _trim(request_id, 128)
    if req_token:
        task = tasks.get(req_token)
        if isinstance(task, dict):
            return task
        raise SystemExit(f"request_id not found: {req_token}")
    ref_token = normalize_task_alias_key(_trim(task_ref, 64))
    if ref_token:
        alias_index = entry.get("task_alias_index") if isinstance(entry.get("task_alias_index"), dict) else {}
        request = _trim(alias_index.get(ref_token), 128)
        if request and isinstance(tasks.get(request), dict):
            return tasks[request]
        for rid, task in tasks.items():
            if not isinstance(task, dict):
                continue
            if normalize_task_alias_key(_trim(task.get("short_id"), 64)) == ref_token:
                return task
            if normalize_task_alias_key(_trim(task.get("alias"), 64)) == ref_token:
                return task
        raise SystemExit(f"task_ref not found: {task_ref}")
    raise SystemExit("either --request-id or --task-ref is required")


def export_harness_authoring_plan(
    *,
    project_root: Path,
    team_dir: Path,
    manager_state_file: Path,
    project_alias: str = "",
    project_key: str = "",
    request_id: str = "",
    task_ref: str = "",
    vendor_root: str = "",
    output_path: str = "",
) -> Dict[str, Any]:
    manager_state = load_manager_state(manager_state_file, project_root, team_dir)
    project_name, entry = _find_project_entry(
        manager_state,
        project_alias=project_alias,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
    )
    task = _find_task(entry, request_id=request_id, task_ref=task_ref)
    plan = build_harness_authoring_plan(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    plan["project_name"] = project_name
    plan["request_id"] = _trim(task.get("request_id"), 128)
    plan["task_short_id"] = _trim(task.get("short_id"), 64)
    artifact = Path(output_path).expanduser().resolve() if _trim(output_path, 400) else harness_authoring_plan_path(
        team_dir,
        request_id=plan["request_id"],
        task_ref=plan["task_short_id"],
    )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"artifact_path": str(artifact), "summary": _trim(plan.get("summary"), 400), "plan": plan}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export a read-only harness authoring plan from current runtime state.")
    parser.add_argument("--project-root", required=True, help="Project root for the target runtime.")
    parser.add_argument("--team-dir", default="", help="Optional explicit team dir; defaults to resolved runtime team dir.")
    parser.add_argument("--manager-state-file", default="", help="Optional explicit orch manager state file path.")
    parser.add_argument("--project-alias", default="", help="Optional project alias such as O2.")
    parser.add_argument("--project-key", default="", help="Optional normalized project key.")
    parser.add_argument("--request-id", default="", help="Request id to export.")
    parser.add_argument("--task-ref", default="", help="Task short id or alias to export.")
    parser.add_argument("--vendor-root", default="", help="Optional override for vendored harness root.")
    parser.add_argument("--output-path", default="", help="Optional explicit output path.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    team_dir = Path(args.team_dir).expanduser().resolve() if _trim(args.team_dir, 400) else resolve_default_team_dir(project_root)
    manager_state_file = (
        Path(args.manager_state_file).expanduser().resolve()
        if _trim(args.manager_state_file, 400)
        else resolve_state_file(project_root, None)
    )
    result = export_harness_authoring_plan(
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        project_alias=args.project_alias,
        project_key=args.project_key,
        request_id=args.request_id,
        task_ref=args.task_ref,
        vendor_root=args.vendor_root,
        output_path=args.output_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
