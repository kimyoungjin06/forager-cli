#!/usr/bin/env python3
"""Scheduler command handlers for Telegram gateway.

This module implements a minimal "Control Plane" scheduler:
- /next : pick the next runnable todo across all registered orch projects
- /queue : summarize todo queue across all registered orch projects

Design goals:
- Keep operator input minimal (single command).
- Prefer safety: do not start a new todo when a project already has a running todo,
  or when a pending todo exists (unless forced).
- Keep blocked todos visible, but do not let them freeze unrelated open todos in the same project.
- Return a transition dict compatible with the NonRun->Run pipeline.
"""

from __future__ import annotations

import copy
import fnmatch
import json
from pathlib import Path
import os
import re
import heapq
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from aoe_tg_provider_fallback import load_provider_capacity_state

from aoe_tg_blocked_state import (
    blocked_bucket_count as blocked_bucket_count_base,
    blocked_bucket_label as blocked_bucket_label_base,
    blocked_head_summary as blocked_head_summary_base,
    blocked_reason_preview as blocked_reason_preview_base,
    manual_followup_indices as manual_followup_indices_base,
)
from aoe_tg_ops_policy import (
    build_no_runnable_todo_message,
    find_pending_todo_for_chat as find_ops_pending_todo_for_chat,
    list_ops_projects,
    priority_rank as ops_priority_rank,
    project_alias as ops_project_alias,
    project_queue_snapshot,
    project_schedulable,
    sorted_open_todos as ops_sorted_open_todos,
)
from aoe_tg_project_runtime import project_hidden_from_ops, project_runtime_issue, project_runtime_label
from aoe_tg_queue_engine import (
    count_todo_statuses as queue_count_todo_statuses,
    find_todo_item as queue_find_todo_item,
    has_task_linked_to_todo as queue_has_task_linked_to_todo,
    pick_global_next_candidate as queue_pick_global_next_candidate,
    sorted_active_todos as queue_sorted_active_todos,
)
from aoe_tg_sync_merge import apply_scenario_items_to_entry as _apply_scenario_items_to_entry
from aoe_tg_sync_merge import stamp_sync_meta as _stamp_sync_meta
import aoe_tg_scheduler_sync as scheduler_sync_mod
import aoe_tg_scheduler_queue as scheduler_queue_mod
import aoe_tg_scheduler_next as scheduler_next_mod
from aoe_tg_todo_state import merge_todo_proposals

_PRIORITIES = {"P1", "P2", "P3"}
_STATUS_OPEN = "open"
_STATUS_RUNNING = "running"
_STATUS_BLOCKED = "blocked"
_STATUS_DONE = "done"
_STATUS_CANCELED = "canceled"
_SCENARIO_FILENAME = "AOE_TODO.md"
_SCENARIO_INCLUDE_PREFIX = "@include"

_DISCOVERY_DEFAULT_DOCS_LIMIT = 3
_DISCOVERY_DEFAULT_CANDIDATE_KEEP = 250
_DISCOVERY_DEFAULT_MAX_BYTES = 512 * 1024
_DISCOVERY_ALLOWED_EXTS = {".md", ".txt", ".rst"}
_DISCOVERY_DEFAULT_TODO_FILES_LIMIT = 80
_DISCOVERY_EXCLUDE_DIRS = {
    ".github",
    ".git",
    ".aoe-team",
    ".venv",
    "archive",
    "venv",
    "templates",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    ".pytest_cache",
}

_LAST_CMD_ARGS_KEY = "last_cmd_args"
_AUTO_STATE_FILENAME = "auto_scheduler.json"

from aoe_tg_sync_sources import (
    _apply_sync_policy,
    _attach_item_provenance,
    _choose_sync_row,
    _classify_sync_source,
    _discover_recent_doc_todos,
    _discover_salvage_doc_proposals,
    _discover_salvage_doc_todos,
    _discover_sync_fallback_todos,
    _discover_todo_file_todos,
    _doc_has_any_markdown_checkbox,
    _doc_has_todo_markers,
    _extract_explicit_todo_id,
    _extract_salvage_proposal_items_from_doc,
    _extract_todo_items_from_doc,
    _has_following_top_level_actionable_child,
    _heading_is_done,
    _heading_is_meta,
    _is_excluded_doc_path,
    _is_within,
    _line_is_plain_done_label,
    _line_is_plain_meta_label,
    _line_is_plain_todo_label,
    _load_project_sync_policy,
    _normalize_summary_key,
    _parse_doc_section_bullet,
    _parse_doc_todo_line,
    _parse_scenario_lines,
    _path_has_todo_hint,
    _proposal_from_salvage_row,
    _recent_doc_candidates,
    _rel_display,
    _salvage_heading,
    _scenario_include_tokens,
    _strip_summary_marker,
    _summary_has_completion_marker,
    _summary_has_meta_prefix,
    _summary_is_done_label,
    _summary_is_meta_label,
    _summary_is_non_actionable,
    _summary_is_reference_note,
    _summary_is_structural_title,
    _summary_label_key,
    _sync_candidate_allowed,
    _tag_sync_items,
    _todo_file_candidates,
    _todo_heading,
)


def _project_lock_row(manager_state: Dict[str, Any]) -> Dict[str, Any]:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    raw = manager_state.get("project_lock") if isinstance(manager_state, dict) else {}
    if not isinstance(raw, dict) or not isinstance(projects, dict):
        return {}
    if not bool(raw.get("enabled", False)):
        return {}
    key = str(raw.get("project_key", "")).strip().lower()
    entry = projects.get(key)
    if not key or not isinstance(entry, dict):
        return {}
    return {"enabled": True, "project_key": key}



def _is_auto_invocation(args: Any) -> bool:
    return scheduler_sync_mod._is_auto_invocation(args)


def _get_last_cmd_args(state: Dict[str, Any], chat_id: str, cmd: str) -> str:
    return scheduler_sync_mod._get_last_cmd_args(state, chat_id, cmd)


def _set_last_cmd_args(state: Dict[str, Any], chat_id: str, cmd: str, rest: str, now: str) -> bool:
    return scheduler_sync_mod._set_last_cmd_args(state, chat_id, cmd, rest, now)


def _parse_since_seconds(raw: str) -> int:
    return scheduler_sync_mod._parse_since_seconds(raw)


def _preview_item_line(row: Dict[str, Any]) -> str:
    return scheduler_sync_mod._preview_item_line(row)


def _summarize_sync_candidate_classes(items: List[Dict[str, Any]]) -> str:
    return scheduler_sync_mod._summarize_sync_candidate_classes(items)


def _summarize_sync_candidate_doc_types(items: List[Dict[str, Any]]) -> str:
    return scheduler_sync_mod._summarize_sync_candidate_doc_types(items)


def _sync_candidate_counter(items: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    return scheduler_sync_mod._sync_candidate_counter(items, field)


def _build_sync_diagnostics(
    *,
    mode: str,
    source_mode: str,
    key: str,
    alias: str,
    policy_path: str,
    sources: List[str],
    include_warnings: List[str],
    items: List[Dict[str, Any]],
    proposal_payloads: List[Dict[str, Any]],
    inspect_lines: List[str],
) -> List[str]:
    return scheduler_sync_mod._build_sync_diagnostics(
        mode=mode,
        source_mode=source_mode,
        key=key,
        alias=alias,
        policy_path=policy_path,
        sources=sources,
        include_warnings=include_warnings,
        items=items,
        proposal_payloads=proposal_payloads,
        inspect_lines=inspect_lines,
    )


def _render_sync_lock_message(*, locked_label: str, requested_label: str) -> str:
    return scheduler_sync_mod._render_sync_lock_message(locked_label=locked_label, requested_label=requested_label)


def _alias_index(alias: str) -> int:
    token = str(alias or "").strip().upper()
    if token.startswith("O"):
        token = token[1:]
    return int(token) if token.isdigit() else 10**9


def _normalize_priority(token: str) -> str:
    raw = str(token or "").strip().upper()
    if raw in _PRIORITIES:
        return raw
    return "P2"


def _priority_rank(priority: str) -> int:
    return ops_priority_rank(priority)


def _project_alias(entry: Dict[str, Any], fallback: str) -> str:
    return ops_project_alias(entry, fallback)


def _sorted_open_todos(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return ops_sorted_open_todos(todos)


def _sorted_active_todos(todos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return queue_sorted_active_todos(todos)


def _count_todo_statuses(todos: List[Dict[str, Any]]) -> Dict[str, int]:
    return queue_count_todo_statuses(todos)


def _blocked_reason_preview(raw: Any, limit: int = 72) -> str:
    return blocked_reason_preview_base(raw, limit=limit)


def _blocked_bucket_label(raw: Any) -> str:
    return blocked_bucket_label_base(raw)


def _blocked_head_summary(todos: List[Dict[str, Any]]) -> Dict[str, Any]:
    return blocked_head_summary_base(todos, blocked_status=_STATUS_BLOCKED, priority_rank=_priority_rank)


def _blocked_bucket_count(todos: List[Dict[str, Any]], bucket: str) -> int:
    return blocked_bucket_count_base(todos, bucket, blocked_status=_STATUS_BLOCKED)


def _manual_followup_indices(todos: List[Dict[str, Any]], limit: int = 1) -> List[int]:
    return manual_followup_indices_base(_sorted_active_todos(todos), limit=limit, blocked_status=_STATUS_BLOCKED)


def _queue_reply_markup(
    rows: List[Dict[str, Any]],
    *,
    followup_only: bool,
    focus_key: str,
    fallback_alias: str = "",
) -> Dict[str, Any]:
    return scheduler_queue_mod.queue_reply_markup(
        rows,
        followup_only=followup_only,
        focus_key=focus_key,
        fallback_alias=fallback_alias,
    )


def _find_pending_todo_for_chat(
    projects: Dict[str, Any],
    chat_id: str,
    *,
    skip_paused: bool = False,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    hit = find_ops_pending_todo_for_chat(projects, chat_id, skip_paused=skip_paused, require_ready=True)
    if not hit:
        return None
    key, _entry, pending = hit
    return key, pending


def _find_todo_item(entry: Dict[str, Any], todo_id: str) -> Optional[Dict[str, Any]]:
    return queue_find_todo_item(entry, todo_id)


def _has_task_linked_to_todo(entry: Dict[str, Any], todo_id: str) -> bool:
    return queue_has_task_linked_to_todo(entry, todo_id)


def _pick_global_next_candidate(
    projects: Dict[str, Any],
    *,
    ignore_busy: bool = False,
    skip_paused: bool = False,
    recovery_grace_until: Any = None,
    provider_capacity_state: Any = None,
) -> Optional[Dict[str, Any]]:
    return queue_pick_global_next_candidate(
        projects,
        ignore_busy=ignore_busy,
        skip_paused=skip_paused,
        recovery_grace_until=recovery_grace_until,
        provider_capacity_state=provider_capacity_state,
    )


def _auto_state_path(args: Any) -> Path:
    return Path(str(getattr(args, "team_dir", "."))).expanduser().resolve() / _AUTO_STATE_FILENAME


def _load_auto_state(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _auto_recovery_grace_until(args: Any) -> str:
    state = _load_auto_state(_auto_state_path(args))
    if not bool(state.get("enabled", False)):
        return ""
    return str(state.get("recovery_grace_until", "")).strip()


def handle_scheduler_command(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    orch_target: Optional[str],
    rest: str,
    send: Callable[..., bool],
    get_context: Callable[[Optional[str]], tuple[str, Dict[str, Any], Any]],
    save_manager_state: Callable[..., None],
    now_iso: Callable[[], str],
) -> Optional[Dict[str, Any]]:
    """Handle /next (Control Plane global todo scheduler).

    Returns:
      - {"terminal": True} when handled and no run is needed
      - transition dict with {"terminal": False, "cmd":"run", ...} to continue into run pipeline
      - None when cmd does not match
    """
    if cmd not in {"next", "queue", "sync"}:
        return None

    projects = manager_state.get("projects")
    if not isinstance(projects, dict) or not projects:
        send("no orch projects registered. use /map and /orch add first.", context="sched-empty", with_menu=True)
        return {"terminal": True}
    focus_row = _project_lock_row(manager_state)
    focus_key = str(focus_row.get("project_key", "")).strip()
    focus_entry = projects.get(focus_key) if focus_key and isinstance(projects.get(focus_key), dict) else {}
    focus_alias = _project_alias(focus_entry if isinstance(focus_entry, dict) else {}, focus_key) if focus_key else ""

    if cmd == "sync":
        return scheduler_sync_mod.handle_sync_command(
            args=args,
            manager_state=manager_state,
            chat_id=chat_id,
            orch_target=orch_target,
            rest=rest,
            send=send,
            get_context=get_context,
            save_manager_state=save_manager_state,
            now_iso=now_iso,
            projects=projects,
            focus_key=focus_key,
            focus_entry=focus_entry if isinstance(focus_entry, dict) else {},
            focus_alias=focus_alias,
        )
    if cmd == "queue":
        return scheduler_queue_mod.handle_queue_command(
            manager_state=manager_state,
            chat_id=chat_id,
            rest=rest,
            send=send,
            projects=projects,
            focus_key=focus_key,
            focus_entry=focus_entry if isinstance(focus_entry, dict) else {},
            focus_alias=focus_alias,
            list_ops_projects=list_ops_projects,
            project_alias=_project_alias,
            count_todo_statuses=_count_todo_statuses,
            sorted_open_todos=_sorted_open_todos,
            blocked_bucket_count=_blocked_bucket_count,
            manual_followup_indices=_manual_followup_indices,
            blocked_head_summary=_blocked_head_summary,
            alias_index=_alias_index,
            queue_reply_markup_fn=_queue_reply_markup,
            normalize_priority=_normalize_priority,
            status_open=_STATUS_OPEN,
            status_running=_STATUS_RUNNING,
            status_blocked=_STATUS_BLOCKED,
            status_done=_STATUS_DONE,
            status_canceled=_STATUS_CANCELED,
        )

    return scheduler_next_mod.handle_next_command(
        args=args,
        manager_state=manager_state,
        chat_id=chat_id,
        chat_role=chat_role,
        rest=rest,
        send=send,
        get_context=get_context,
        save_manager_state=save_manager_state,
        now_iso=now_iso,
        projects=projects,
        focus_key=focus_key,
        focus_entry=focus_entry if isinstance(focus_entry, dict) else {},
        focus_alias=focus_alias,
        project_schedulable=project_schedulable,
        project_runtime_issue=project_runtime_issue,
        project_runtime_label=project_runtime_label,
        project_alias=_project_alias,
        find_pending_todo_for_chat=_find_pending_todo_for_chat,
        has_task_linked_to_todo=_has_task_linked_to_todo,
        find_todo_item=_find_todo_item,
        auto_recovery_grace_until=_auto_recovery_grace_until,
        load_provider_capacity_state=load_provider_capacity_state,
        pick_global_next_candidate=_pick_global_next_candidate,
        build_no_runnable_todo_message=build_no_runnable_todo_message,
        blocked_head_summary=_blocked_head_summary,
        normalize_priority=_normalize_priority,
    )
