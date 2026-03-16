#!/usr/bin/env python3
"""Shared side-effect-free runtime state helpers for gateway and dashboard consumers."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict

from aoe_tg_chat_state import sanitize_chat_session_row
from aoe_tg_project_state import ensure_project_aliases, sanitize_project_lock_row
from aoe_tg_runtime_core import default_manager_state as runtime_default_manager_state
from aoe_tg_runtime_core import load_manager_state as runtime_load_manager_state
from aoe_tg_schema import (
    normalize_exec_critic_payload,
    normalize_plan_critic_payload,
    normalize_plan_replans_payload,
    normalize_task_plan_payload,
    plan_critic_primary_issue,
)
from aoe_tg_task_state import (
    backfill_task_aliases,
    normalize_task_alias_key,
    sanitize_task_record as sanitize_task_record_state,
    trim_project_tasks,
)
from aoe_tg_task_view import LIFECYCLE_STAGES, build_task_context, dedupe_roles, normalize_project_alias, normalize_project_name


TASK_STAGE_STATUS_ALLOWED = {"pending", "running", "done", "failed"}
TASK_OVERALL_STATUS_ALLOWED = {"pending", "running", "completed", "failed"}
DEFAULT_TASK_KEEP_PER_PROJECT = 120


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def bool_from_json(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    token = str(raw).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_stage_status(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in TASK_STAGE_STATUS_ALLOWED:
        return token
    aliases = {
        "complete": "done",
        "completed": "done",
        "success": "done",
        "active": "running",
        "in_progress": "running",
        "progress": "running",
        "fail": "failed",
        "error": "failed",
    }
    return aliases.get(token, "pending")


def normalize_task_status(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in TASK_OVERALL_STATUS_ALLOWED:
        return token
    aliases = {
        "done": "completed",
        "complete": "completed",
        "success": "completed",
        "fail": "failed",
        "error": "failed",
        "active": "running",
        "in_progress": "running",
        "progress": "running",
    }
    return aliases.get(token, "pending")


def default_manager_state(project_root: Path, team_dir: Path) -> Dict[str, Any]:
    return runtime_default_manager_state(project_root, team_dir, now_iso=now_iso)


def sanitize_task_record(raw_task: Dict[str, Any], req_id: str) -> Dict[str, Any]:
    return sanitize_task_record_state(
        raw_task,
        req_id,
        dedupe_roles=dedupe_roles,
        lifecycle_stages=LIFECYCLE_STAGES,
        normalize_stage_status=normalize_stage_status,
        normalize_task_status=normalize_task_status,
        now_iso=now_iso,
        history_limit=80,
        normalize_task_plan_schema=normalize_task_plan_payload,
        normalize_plan_critic_payload=normalize_plan_critic_payload,
        normalize_plan_replans_payload=normalize_plan_replans_payload,
        plan_critic_primary_issue=plan_critic_primary_issue,
        normalize_exec_critic_payload=normalize_exec_critic_payload,
        build_task_context=build_task_context,
    )


def _trim_project_tasks(tasks: Dict[str, Any]) -> None:
    trim_project_tasks(tasks, keep=DEFAULT_TASK_KEEP_PER_PROJECT)


def _sanitize_project_lock_row(raw: Any, projects: Any) -> Dict[str, Any]:
    return sanitize_project_lock_row(raw, projects, bool_from_json=bool_from_json)


def load_manager_state(path: Path, project_root: Path, team_dir: Path) -> Dict[str, Any]:
    return runtime_load_manager_state(
        path,
        project_root,
        team_dir,
        default_manager_state=default_manager_state,
        now_iso=now_iso,
        normalize_project_name=normalize_project_name,
        sanitize_task_record=sanitize_task_record,
        trim_project_tasks=_trim_project_tasks,
        normalize_task_alias_key=normalize_task_alias_key,
        bool_from_json=bool_from_json,
        normalize_project_alias=normalize_project_alias,
        backfill_task_aliases=backfill_task_aliases,
        ensure_project_aliases=ensure_project_aliases,
        sanitize_project_lock_row=_sanitize_project_lock_row,
        sanitize_chat_session_row=sanitize_chat_session_row,
    )
