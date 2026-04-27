#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


def handle_next_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    get_context: Callable[[Optional[str]], tuple[str, Dict[str, Any], Any]],
    save_manager_state: Callable[..., None],
    now_iso: Callable[[], str],
    projects: Dict[str, Any],
    focus_key: str,
    focus_entry: Dict[str, Any],
    focus_alias: str,
    project_schedulable: Callable[[Dict[str, Any]], bool],
    project_runtime_issue: Callable[[Dict[str, Any]], str],
    project_runtime_label: Callable[[Dict[str, Any]], str],
    project_alias: Callable[[Dict[str, Any], str], str],
    find_pending_todo_for_chat: Callable[..., Optional[Tuple[str, Dict[str, Any]]]],
    has_task_linked_to_todo: Callable[[Dict[str, Any], str], bool],
    find_todo_item: Callable[[Dict[str, Any], str], Optional[Dict[str, Any]]],
    auto_recovery_grace_until: Callable[[Any], str],
    load_provider_capacity_state: Callable[[Any], Any],
    pick_global_next_candidate: Callable[..., Optional[Dict[str, Any]]],
    build_no_runnable_todo_message: Callable[..., str],
    blocked_head_summary: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    normalize_priority: Callable[[str], str],
) -> Dict[str, Any]:
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

    candidate_projects = ({focus_key: focus_entry} if focus_key and isinstance(focus_entry, dict) else projects)
    unready_rows: List[str] = []
    for p_key, p_entry in candidate_projects.items():
        if not isinstance(p_entry, dict):
            continue
        if not project_schedulable(p_entry):
            continue
        if not project_runtime_issue(p_entry):
            continue
        alias = project_alias(p_entry, str(p_key))
        unready_rows.append(f"- {alias} ({p_key}): {project_runtime_label(p_entry)}")

    pending_hit = find_pending_todo_for_chat(candidate_projects, chat_id, skip_paused=not force)
    if pending_hit and not force:
        p_key, pending = pending_hit
        p_todo_id = str(pending.get("todo_id", "")).strip()
        try:
            key, entry, _p_args = get_context(p_key)
        except Exception:
            key = p_key
            entry = projects.get(p_key) if isinstance(projects.get(p_key), dict) else {}

        alias = project_alias(entry if isinstance(entry, dict) else {}, key)
        if isinstance(entry, dict) and has_task_linked_to_todo(entry, p_todo_id):
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

        todo_item = find_todo_item(entry if isinstance(entry, dict) else {}, p_todo_id) if isinstance(entry, dict) else None
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

    recovery_grace_until = auto_recovery_grace_until(args)
    provider_capacity_state = load_provider_capacity_state(getattr(args, "team_dir", ""))
    candidate = pick_global_next_candidate(
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

    key, entry, _p_args = get_context(key)
    alias = project_alias(entry, key)

    todo_id = str(todo.get("id", "")).strip() or "-"
    pr = normalize_priority(str(todo.get("priority", "P2")))
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
    blocked_head = blocked_head_summary(entry.get("todos") if isinstance(entry.get("todos"), list) else [])
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
