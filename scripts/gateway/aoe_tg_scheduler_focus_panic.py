#!/usr/bin/env python3
"""Focus and panic command helpers for scheduler control handlers."""

from typing import Any, Callable, Dict

def _handle_focus_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    save_manager_state: Callable[..., None],
    resolve_project_entry: Callable[[Dict[str, Any], str], tuple[str, Dict[str, Any]]],
    project_lock_row: Callable[[Dict[str, Any]], Dict[str, Any]],
    project_lock_label: Callable[[Dict[str, Any]], str],
    now_iso: Callable[[], str],
) -> bool:
    tokens = [t for t in str(rest or "").split() if t.strip()]
    sub = (tokens[0].lower() if tokens else "status").strip()
    if sub in {"", "show", "status"}:
        sub = "status"

    row = project_lock_row(manager_state)
    active_key = str(manager_state.get("active", "default") or "default").strip()
    active_label = ""
    try:
        key0, entry0 = resolve_project_entry(manager_state, active_key)
        alias0 = str(entry0.get("project_alias", "")).strip() or key0
        active_label = f"{alias0} ({key0})"
    except Exception:
        active_label = active_key or "-"

    if sub == "status":
        send(
            "project focus lock\n"
            f"- enabled: {'yes' if row else 'no'}\n"
            f"- active_project: {active_label or '-'}\n"
            f"- locked_project: {project_lock_label(manager_state) or '-'}\n"
            "set:\n"
            "- /map\n"
            "- /focus O2\n"
            "- /focus off\n"
            "rules:\n"
            "- /next, /queue, plain text, Task Team run are pinned to the locked project\n"
            "- /fanout and /auto on fanout stay blocked while lock is enabled",
            context="focus-status",
            with_menu=True,
        )
        return True

    if chat_role == "readonly":
        send(
            "permission denied: readonly chat cannot change project focus.\n"
            "read-only: /focus",
            context="focus-deny",
            with_menu=True,
        )
        return True

    if sub in {"off", "clear", "none", "unlock", "release"}:
        existed = bool(row)
        manager_state.pop("project_lock", None)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        send(
            "project focus lock updated\n"
            "- enabled: no\n"
            f"- changed: {'yes' if existed else 'no'}\n"
            f"- active_project: {active_label or '-'}\n"
            "next:\n"
            "- /map\n"
            "- /use O2",
            context="focus-off",
            with_menu=True,
        )
        return True

    target = str(tokens[0] if tokens else "").strip()
    if not target:
        raise RuntimeError("usage: /focus [O#|name|off]")

    key, entry = resolve_project_entry(manager_state, target)
    alias = str(entry.get("project_alias", "")).strip() or key
    manager_state["active"] = key
    manager_state["project_lock"] = {
        "enabled": True,
        "project_key": key,
        "locked_at": now_iso(),
        "locked_by": f"telegram:{chat_id}",
    }
    if not args.dry_run:
        save_manager_state(args.manager_state_file, manager_state)
    send(
        "project focus lock updated\n"
        "- enabled: yes\n"
        f"- locked_project: {alias} ({key})\n"
        "- effect: /next, /queue, plain text, Task Team run -> this project by default\n"
        "- blocked: /fanout, /auto on fanout\n"
        "next:\n"
        f"- /sync {alias} 1h\n"
        "- /next\n"
        "- /focus off",
        context="focus-on",
        with_menu=True,
    )
    return True


def _handle_panic_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    get_default_mode: Callable[[Dict[str, Any], str], str],
    get_pending_mode: Callable[[Dict[str, Any], str], str],
    clear_default_mode: Callable[[Dict[str, Any], str], bool],
    clear_pending_mode: Callable[[Dict[str, Any], str], bool],
    clear_confirm_action: Callable[[Dict[str, Any], str], bool],
    save_manager_state: Callable[..., None],
    auto_state_path: Callable[[Any], Any],
    offdesk_state_path: Callable[[Any], Any],
    load_auto_state: Callable[[Any], Dict[str, Any]],
    save_auto_state: Callable[[Any, Dict[str, Any]], None],
    load_offdesk_state: Callable[[Any], Dict[str, Any]],
    save_offdesk_state: Callable[[Any, Dict[str, Any]], None],
    scheduler_session_name: Callable[[], str],
    tmux_has_session: Callable[[str], bool],
    tmux_auto_command: Callable[[Any, str], tuple[bool, str]],
    now_iso: Callable[[], str],
) -> bool:
    tokens = [t for t in str(rest or "").split() if t.strip()]
    sub = (tokens[0].lower() if tokens else "").strip()
    if sub in {"", "go", "now", "on", "stop"}:
        sub = "stop"
    if sub in {"show"}:
        sub = "status"
    if sub in {"help", "h", "?"}:
        raise RuntimeError("usage: /panic [status]")
    if sub not in {"stop", "status"}:
        raise RuntimeError("usage: /panic [status]")

    auto_path = auto_state_path(args)
    auto_state = load_auto_state(auto_path)
    auto_enabled = bool(auto_state.get("enabled", False))
    auto_chat = str(auto_state.get("chat_id", "")).strip() or "-"

    off_path = offdesk_state_path(args)
    off_state = load_offdesk_state(off_path)
    off_enabled = bool(off_state.get("enabled", False))
    off_chat = str(off_state.get("chat_id", "")).strip() or "-"

    session = scheduler_session_name()
    sess_up = tmux_has_session(session)

    current_default_mode = get_default_mode(manager_state, chat_id) or "off"
    current_pending_mode = get_pending_mode(manager_state, chat_id) or "none"

    if sub == "status":
        lines = [
            "panic switch",
            f"- routing_mode: {current_default_mode}",
            f"- one_shot_pending: {current_pending_mode}",
            f"- auto_enabled: {'yes' if auto_enabled else 'no'} (chat_id={auto_chat})",
            f"- offdesk_enabled: {'yes' if off_enabled else 'no'} (chat_id={off_chat})",
            f"- tmux_scheduler: {session} ({'up' if sess_up else 'down'})",
            "",
            "actions:",
            "- /panic        # stop auto/offdesk + clear pending/confirm + routing off",
            "- /offdesk on   # resume preset",
            "- /auto on fanout recent",
            "- /auto status",
        ]
        send("\n".join(lines).strip(), context="panic-status", with_menu=True)
        return True

    if chat_role == "readonly":
        send(
            "permission denied: readonly chat cannot use /panic.\n"
            "read-only: /panic status",
            context="panic-deny",
            with_menu=True,
        )
        return True

    if args.dry_run:
        tmux_ok, tmux_out = True, "dry-run: skipped tmux auto off"
    else:
        tmux_ok, tmux_out = tmux_auto_command(args, "off")

    auto_state["enabled"] = False
    auto_state["chat_id"] = str(auto_state.get("chat_id", "")).strip() or str(chat_id)
    auto_state["stopped_at"] = now_iso()
    auto_state["stopped_reason"] = "panic"
    if not args.dry_run:
        save_auto_state(auto_path, auto_state)

    if not isinstance(off_state, dict):
        off_state = {}
    off_state["enabled"] = False
    off_state["chat_id"] = str(chat_id)
    off_state["stopped_at"] = now_iso()
    off_state["stopped_reason"] = "panic"
    if not args.dry_run:
        save_offdesk_state(off_path, off_state)

    existed_default = clear_default_mode(manager_state, chat_id)
    cleared_pending = clear_pending_mode(manager_state, chat_id)
    cleared_confirm = clear_confirm_action(manager_state, chat_id)
    if not args.dry_run:
        save_manager_state(args.manager_state_file, manager_state)

    send(
        "panic activated\n"
        "- auto: stopped\n"
        f"- offdesk: {'stopped' if off_enabled else 'already_off'}\n"
        f"- tmux: {'stopped' if tmux_ok else 'stop_failed'}\n"
        f"- detail: {tmux_out or '-'}\n"
        f"- routing_mode: off (changed={'yes' if existed_default else 'no'})\n"
        f"- pending_cleared: {'yes' if cleared_pending else 'no'}\n"
        f"- confirm_cleared: {'yes' if cleared_confirm else 'no'}\n"
        "next:\n"
        "- /offdesk status\n"
        "- /auto status\n"
        "- /offdesk on   (resume)\n"
        "- /mode on      (enable plain-text routing again)",
        context="panic",
        with_menu=True,
    )
    return True

