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
    keyboard: List[List[Dict[str, str]]] = []
    for row in rows[:3]:
        alias = str(row.get("alias", "")).strip().upper()
        if not alias:
            continue
        if followup_only:
            row_buttons: List[Dict[str, str]] = [{"text": f"/todo {alias} followup"}]
            ack_ref = str(row.get("manual_followup_ack_ref", "")).strip()
            if ack_ref:
                row_buttons.append({"text": f"/todo {alias} ackrun {ack_ref}"})
            row_buttons.append({"text": f"/orch status {alias}"})
            keyboard.append(row_buttons[:3])
        else:
            keyboard.append([{"text": f"/todo {alias}"}, {"text": f"/orch status {alias}"}])

    fallback = str(fallback_alias or "").strip().upper()
    if followup_only and (not keyboard) and fallback:
        keyboard.append([{"text": f"/todo {fallback} followup"}, {"text": f"/todo {fallback}"}, {"text": f"/orch status {fallback}"}])

    utility_row: List[Dict[str, str]] = []
    if followup_only:
        utility_row.extend([{"text": "/queue"}, {"text": "/map"}, {"text": "/help"}])
    else:
        has_followup = any(int(row.get("manual_followup_count", 0) or 0) > 0 for row in rows)
        if has_followup:
            utility_row.append({"text": "/queue followup"})
        utility_row.extend([{"text": "/next"}, {"text": "/map"}])
    if utility_row:
        keyboard.append(utility_row[:3])

    if focus_key:
        keyboard.append([{"text": "/focus off"}])

    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "예: /queue followup 또는 /todo O# followup",
    }


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
        queue_tokens = [t for t in str(rest or "").split() if t.strip()]
        followup_only = bool(queue_tokens and str(queue_tokens[0]).strip().lower() in {"followup", "fu"})
        fallback_alias = ""
        fallback_key = str(focus_key or "").strip()
        fallback_entry = focus_entry if focus_key and isinstance(focus_entry, dict) else None
        if not isinstance(fallback_entry, dict):
            active_key = str(manager_state.get("active", "")).strip().lower()
            active_entry = projects.get(active_key) if isinstance(projects, dict) else None
            if isinstance(active_entry, dict):
                fallback_entry = active_entry
                fallback_key = active_key
        if isinstance(fallback_entry, dict):
            fallback_alias = _project_alias(fallback_entry, str(fallback_key or manager_state.get("active", "") or ""))
        rows: List[Dict[str, Any]] = []
        iter_projects = (
            [(focus_key, focus_entry)]
            if focus_key and isinstance(focus_entry, dict)
            else list_ops_projects(projects)
        )
        for key, entry in iter_projects:
            alias = _project_alias(entry, str(key))
            display = str(entry.get("display_name", "")).strip() or str(entry.get("name", "")).strip() or str(key)
            if len(display) > 24:
                display = display[:21] + "..."
            raw = entry.get("todos")
            todos: List[Dict[str, Any]] = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
            counts = _count_todo_statuses(todos)
            open_rows = _sorted_open_todos(todos)
            next_item = open_rows[0] if open_rows else None

            pending = entry.get("pending_todo")
            pending_id = ""
            pending_chat = ""
            if isinstance(pending, dict):
                pending_id = str(pending.get("todo_id", "")).strip()
                pending_chat = str(pending.get("chat_id", "")).strip()

            flags: List[str] = []
            if bool(entry.get("paused", False)):
                flags.append("paused")
            if pending_id:
                flags.append("pending")
            if counts.get(_STATUS_RUNNING, 0) > 0:
                flags.append("running")
            if counts.get(_STATUS_BLOCKED, 0) > 0:
                flags.append("blocked")

            next_summary = ""
            next_id = ""
            next_pr = ""
            if isinstance(next_item, dict):
                next_id = str(next_item.get("id", "")).strip()
                next_pr = _normalize_priority(str(next_item.get("priority", "P2")))
                next_summary = str(next_item.get("summary", "")).strip().replace("\n", " ")
                if len(next_summary) > 64:
                    next_summary = next_summary[:61] + "..."
            manual_followup_count = _blocked_bucket_count(todos, "manual_followup")
            followup_indices = _manual_followup_indices(todos, limit=1)
            if followup_only and manual_followup_count <= 0:
                continue

            rows.append(
                {
                    "alias": alias,
                    "key": str(key),
                    "display": display,
                    "open": int(counts.get(_STATUS_OPEN, 0) or 0),
                    "running": int(counts.get(_STATUS_RUNNING, 0) or 0),
                    "blocked": int(counts.get(_STATUS_BLOCKED, 0) or 0),
                    "done": int(counts.get(_STATUS_DONE, 0) or 0),
                    "canceled": int(counts.get(_STATUS_CANCELED, 0) or 0),
                    "flags": ",".join(flags),
                    "pending_id": pending_id,
                    "pending_self": "yes" if (pending_id and pending_chat == str(chat_id)) else "no",
                    "next_id": next_id,
                    "next_pr": next_pr,
                    "next_summary": next_summary,
                    "blocked_head": _blocked_head_summary(todos),
                    "manual_followup_count": manual_followup_count,
                    "manual_followup_ack_ref": str(followup_indices[0]) if followup_indices else "",
                }
            )

        if not rows:
            msg = "manual follow-up queue: empty." if followup_only else "queue: no projects found."
            send(
                msg,
                context="queue-followup-none" if followup_only else "queue-none",
                with_menu=True,
                reply_markup=_queue_reply_markup(
                    rows,
                    followup_only=followup_only,
                    focus_key=str(focus_key or ""),
                    fallback_alias=fallback_alias,
                ),
            )
            return {"terminal": True}

        rows.sort(key=lambda r: (_alias_index(r.get("alias", "")), str(r.get("alias", "")), str(r.get("key", ""))))

        limit = 12
        lines: List[str] = []
        if followup_only:
            lines.append("manual follow-up queue")
        else:
            lines.append("project todo queue" if focus_key else "global todo queue")
        if focus_key:
            lines.append(f"- project_lock: {focus_alias or focus_key}")
        lines.append(f"- projects: {len(rows)}")
        lines.append("")
        for row in rows[:limit]:
            alias = row.get("alias", "-")
            display = row.get("display", "")
            counts_part = "open={o} running={r} blocked={b} done={d}".format(
                o=row.get("open", 0),
                r=row.get("running", 0),
                b=row.get("blocked", 0),
                d=row.get("done", 0),
            )
            manual_followup_count = int(row.get("manual_followup_count", 0) or 0)
            if manual_followup_count > 0:
                counts_part += f" followup={manual_followup_count}"
            flag = str(row.get("flags", "")).strip()
            if flag:
                counts_part = counts_part + f" ({flag})"
            lines.append(f"- {alias} {display}: {counts_part}")
            if str(row.get("pending_id", "")).strip():
                suffix = " (yours)" if str(row.get("pending_self", "no")) == "yes" else ""
                lines.append(f"  pending: {row.get('pending_id')}{suffix}")
            blocked_head = row.get("blocked_head") if isinstance(row.get("blocked_head"), dict) else {}
            if blocked_head:
                blocked_line = f"  blocked_head: {blocked_head.get('id', '-')} x{blocked_head.get('count', 1)}"
                bucket = str(blocked_head.get("bucket", "")).strip()
                if bucket:
                    blocked_line += f" [{bucket}]"
                reason = str(blocked_head.get("reason", "")).strip()
                if reason:
                    blocked_line += f" | {reason}"
                lines.append(blocked_line)
            if str(row.get("next_id", "")).strip():
                next_line = f"  next: {row.get('next_pr','P2')} {row.get('next_id')} | {row.get('next_summary') or '-'}"
                lines.append(next_line)
        if len(rows) > limit:
            lines.append("")
            lines.append(f"... and {len(rows) - limit} more")

        lines.append("")
        lines.append("next:")
        if followup_only:
            lines.append("- /todo followup   (current project follow-up backlog)")
        lines.append("- /next   (pick and run next todo)")
        lines.append("- /todo   (view/edit current project)")
        lines.append("- /use O# (switch active project)")
        if focus_key:
            lines.append("- /focus off (unlock global scheduling)")
        send(
            "\n".join(lines).strip(),
            context="queue",
            with_menu=True,
            reply_markup=_queue_reply_markup(
                rows,
                followup_only=followup_only,
                focus_key=str(focus_key or ""),
                fallback_alias=fallback_alias,
            ),
        )
        return {"terminal": True}

    if chat_role == "readonly":
        send(
            "permission denied: readonly chat cannot start scheduling.\n"
            "read-only: /status /monitor /todo (list) ...",
            context="next-deny",
            with_menu=True,
        )
        return {"terminal": True}

    tokens = [t for t in str(rest or "").split() if t.strip()]
    force = any(t.lower() in {"force", "!", "--force"} for t in tokens)

    # 0) If a pending todo exists for this chat, try to resume it instead of selecting a new one.
    candidate_projects = ({focus_key: focus_entry} if focus_key and isinstance(focus_entry, dict) else projects)
    unready_rows: List[str] = []
    for p_key, p_entry in candidate_projects.items():
        if not isinstance(p_entry, dict):
            continue
        if not project_schedulable(p_entry):
            continue
        if not project_runtime_issue(p_entry):
            continue
        alias = _project_alias(p_entry, str(p_key))
        unready_rows.append(f"- {alias} ({p_key}): {project_runtime_label(p_entry)}")
    pending_hit = _find_pending_todo_for_chat(candidate_projects, chat_id, skip_paused=not force)
    if pending_hit and not force:
        p_key, pending = pending_hit
        p_todo_id = str(pending.get("todo_id", "")).strip()
        try:
            key, entry, _p_args = get_context(p_key)
        except Exception:
            key = p_key
            entry = projects.get(p_key) if isinstance(projects.get(p_key), dict) else {}

        alias = _project_alias(entry if isinstance(entry, dict) else {}, key)
        if isinstance(entry, dict) and _has_task_linked_to_todo(entry, p_todo_id):
            send(
                "next blocked: pending todo already has an active task\n"
                f"- runtime: {key} ({alias})\n"
                f"- pending: {p_todo_id}\n"
                "next:\n"
                "- /todo (list)\n"
                "- /monitor",
                context="next-pending-has-task",
                with_menu=True,
            )
            return {"terminal": True}

        todo_item = _find_todo_item(entry if isinstance(entry, dict) else {}, p_todo_id) if isinstance(entry, dict) else None
        summary = str((todo_item or {}).get("summary", "")).strip() if isinstance(todo_item, dict) else ""
        if not summary:
            send(
                "next blocked: pending todo exists but summary was not found\n"
                f"- runtime: {key} ({alias})\n"
                f"- pending: {p_todo_id}\n"
                "next:\n"
                f"- /todo {key}\n"
                "- /next force  (override)",
                context="next-pending-missing",
                with_menu=True,
            )
            return {"terminal": True}

        summary_preview = summary.replace("\n", " ").strip()
        if len(summary_preview) > 220:
            summary_preview = summary_preview[:217] + "..."
        send(
            "next resumed: pending todo\n"
            f"- runtime: {key} ({alias})\n"
            f"- id: {p_todo_id}\n"
            f"- summary: {summary_preview or '-'}\n"
            "dispatch starting...",
            context="next-resume",
            with_menu=True,
        )
        return {
            "terminal": False,
            "cmd": "run",
            "orch_target": key,
            "run_prompt": summary,
            "run_force_mode": "dispatch",
            "run_auto_source": "todo-next-global",
        }

    # 1) Pick a candidate across all projects.
    recovery_grace_until = _auto_recovery_grace_until(args)
    provider_capacity_state = load_provider_capacity_state(getattr(args, "team_dir", ""))
    candidate = _pick_global_next_candidate(
        candidate_projects,
        ignore_busy=force,
        skip_paused=not force,
        recovery_grace_until=recovery_grace_until,
        provider_capacity_state=provider_capacity_state,
    )
    if not candidate:
        body = build_no_runnable_todo_message(
            focus_label=focus_alias or focus_key,
            unready_rows=unready_rows,
        )
        send(body, context="next-none", with_menu=True)
        return {"terminal": True}

    key = str(candidate.get("project_key", "")).strip()
    todo = candidate.get("todo") if isinstance(candidate.get("todo"), dict) else {}
    selection_kind = str(candidate.get("selection_kind", "")).strip().lower()

    # Resolve entry via get_context to ensure target exists and uses normalized key.
    key, entry, _p_args = get_context(key)
    alias = _project_alias(entry, key)

    todo_id = str(todo.get("id", "")).strip() or "-"
    pr = _normalize_priority(str(todo.get("priority", "P2")))
    summary = str(todo.get("summary", "")).strip()
    if not summary:
        send(
            "next blocked: selected todo has empty summary\n"
            f"- runtime: {key} ({alias})\n"
            f"- id: {todo_id}\n"
            "next:\n"
            f"- /todo {key}\n"
            "- /next force",
            context="next-empty-summary",
            with_menu=True,
        )
        return {"terminal": True}

    now = now_iso()
    todo["updated_at"] = now
    todo["queued_at"] = str(todo.get("queued_at", "")).strip() or now
    todo["queued_by"] = str(todo.get("queued_by", "")).strip() or f"telegram:{chat_id}"
    entry["pending_todo"] = {"todo_id": todo_id, "chat_id": str(chat_id), "selected_at": now}
    entry["updated_at"] = now
    if not args.dry_run:
        save_manager_state(args.manager_state_file, manager_state)

    summary_preview = summary.replace("\n", " ").strip()
    if len(summary_preview) > 220:
        summary_preview = summary_preview[:217] + "..."
    lines = [
        "next resumed (global)" if selection_kind == "resume" else "next selected (global)",
        f"- runtime: {key} ({alias})",
        f"- id: {todo_id}",
        f"- priority: {pr}",
        f"- summary: {summary_preview or '-'}",
    ]
    blocked_head = _blocked_head_summary(entry.get("todos") if isinstance(entry.get("todos"), list) else [])
    if blocked_head and str(blocked_head.get("bucket", "")).strip() == "manual_followup":
        attention = f"- attention: blocked backlog {blocked_head.get('id', '-')} x{blocked_head.get('count', 1)} [manual_followup]"
        reason = str(blocked_head.get("reason", "")).strip()
        if reason:
            attention += f" | {reason}"
        lines.append(attention)
    lines.append("dispatch starting...")
    send("\n".join(lines), context="next-selected", with_menu=True)

    return {
        "terminal": False,
        "cmd": "run",
        "orch_target": key,
        "run_prompt": summary,
        "run_force_mode": "dispatch",
        "run_auto_source": "todo-next-global",
    }
