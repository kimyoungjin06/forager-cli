#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def queue_reply_markup(
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


def handle_queue_command(
    *,
    manager_state: Dict[str, Any],
    chat_id: str,
    rest: str,
    send: Callable[..., bool],
    projects: Dict[str, Any],
    focus_key: str,
    focus_entry: Dict[str, Any],
    focus_alias: str,
    list_ops_projects: Callable[[Dict[str, Any]], List[Any]],
    project_alias: Callable[[Dict[str, Any], str], str],
    count_todo_statuses: Callable[[List[Dict[str, Any]]], Dict[str, int]],
    sorted_open_todos: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    blocked_bucket_count: Callable[[List[Dict[str, Any]], str], int],
    manual_followup_indices: Callable[[List[Dict[str, Any]], int], List[int]],
    blocked_head_summary: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    alias_index: Callable[[str], int],
    queue_reply_markup_fn: Callable[..., Dict[str, Any]],
    normalize_priority: Callable[[str], str],
    status_open: str,
    status_running: str,
    status_blocked: str,
    status_done: str,
    status_canceled: str,
) -> Dict[str, Any]:
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
        fallback_alias = project_alias(fallback_entry, str(fallback_key or manager_state.get("active", "") or ""))

    rows: List[Dict[str, Any]] = []
    iter_projects = (
        [(focus_key, focus_entry)]
        if focus_key and isinstance(focus_entry, dict)
        else list_ops_projects(projects)
    )
    for key, entry in iter_projects:
        alias = project_alias(entry, str(key))
        display = str(entry.get("display_name", "")).strip() or str(entry.get("name", "")).strip() or str(key)
        if len(display) > 24:
            display = display[:21] + "..."
        raw = entry.get("todos")
        todos: List[Dict[str, Any]] = [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        counts = count_todo_statuses(todos)
        open_rows = sorted_open_todos(todos)
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
        if counts.get(status_running, 0) > 0:
            flags.append("running")
        if counts.get(status_blocked, 0) > 0:
            flags.append("blocked")

        next_summary = ""
        next_id = ""
        next_pr = ""
        if isinstance(next_item, dict):
            next_id = str(next_item.get("id", "")).strip()
            next_pr = normalize_priority(str(next_item.get("priority", "P2")))
            next_summary = str(next_item.get("summary", "")).strip().replace("\n", " ")
            if len(next_summary) > 64:
                next_summary = next_summary[:61] + "..."
        manual_followup_count = blocked_bucket_count(todos, "manual_followup")
        followup_indices = manual_followup_indices(todos, limit=1)
        if followup_only and manual_followup_count <= 0:
            continue

        rows.append(
            {
                "alias": alias,
                "key": str(key),
                "display": display,
                "open": int(counts.get(status_open, 0) or 0),
                "running": int(counts.get(status_running, 0) or 0),
                "blocked": int(counts.get(status_blocked, 0) or 0),
                "done": int(counts.get(status_done, 0) or 0),
                "canceled": int(counts.get(status_canceled, 0) or 0),
                "flags": ",".join(flags),
                "pending_id": pending_id,
                "pending_self": "yes" if (pending_id and pending_chat == str(chat_id)) else "no",
                "next_id": next_id,
                "next_pr": next_pr,
                "next_summary": next_summary,
                "blocked_head": blocked_head_summary(todos),
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
            reply_markup=queue_reply_markup_fn(
                rows,
                followup_only=followup_only,
                focus_key=str(focus_key or ""),
                fallback_alias=fallback_alias,
            ),
        )
        return {"terminal": True}

    rows.sort(key=lambda r: (alias_index(r.get("alias", "")), str(r.get("alias", "")), str(r.get("key", ""))))

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
            lines.append(f"  next: {row.get('next_pr','P2')} {row.get('next_id')} | {row.get('next_summary') or '-'}")
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
        reply_markup=queue_reply_markup_fn(
            rows,
            followup_only=followup_only,
            focus_key=str(focus_key or ""),
            fallback_alias=fallback_alias,
        ),
    )
    return {"terminal": True}
