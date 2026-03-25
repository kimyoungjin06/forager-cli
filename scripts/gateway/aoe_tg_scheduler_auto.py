#!/usr/bin/env python3
"""Auto scheduler command helpers for scheduler control handlers."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_action_audit import load_latest_action_audit
from aoe_tg_operator_summary import load_latest_command_resolution
from aoe_tg_operator_surface import append_operator_status_lines
from aoe_tg_scheduler_capacity import (
    _PROVIDER_RECOVERY_GRACE_SEC,
    _capacity_recovery_action,
    _capacity_recovery_target,
    _next_rate_limited_retry_at,
    _next_rate_limited_task_snapshot,
    _parse_iso_datetime,
    _provider_capacity_memory_lines,
    _provider_capacity_policy,
    _provider_capacity_repeat_memory,
    _provider_capacity_repeat_summary_line,
    _prune_provider_capacity_state,
    _rate_limited_capacity_summary,
    _rate_limited_project_aliases,
    _recovery_repeat_snapshot,
)

def _handle_auto_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    get_chat_report_level: Callable[[Dict[str, Any], str, str], str],
    status_report_level: Callable[[List[str], str], str],
    parse_replace_sync_flag: Callable[[List[str]], bool | None],
    normalize_prefetch_token: Callable[[Any], str],
    prefetch_display: Callable[[Any, Any, bool], str],
    compact_reason: Callable[[Any, int], str],
    focused_project_snapshot_lines: Callable[[Dict[str, Any]], List[str]],
    ops_scope_compact_lines: Callable[[Dict[str, Any], int, str], List[str]],
    project_lock_row: Callable[[Dict[str, Any]], Dict[str, Any]],
    project_lock_label: Callable[[Dict[str, Any]], str],
    auto_state_path: Callable[[Any], Any],
    load_auto_state: Callable[[Any], Dict[str, Any]],
    save_auto_state: Callable[[Any, Dict[str, Any]], None],
    provider_capacity_state_path: Callable[[Any], Any],
    load_provider_capacity_state: Callable[[Any], Dict[str, Any]],
    save_provider_capacity_state: Callable[[Any, Dict[str, Any]], None],
    scheduler_session_name: Callable[[], str],
    tmux_has_session: Callable[[str], bool],
    tmux_auto_command: Callable[[Any, str], tuple[bool, str]],
    now_iso: Callable[[], str],
    default_auto_interval_sec: int,
    default_auto_idle_sec: int,
    default_auto_max_failures: int,
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    tokens = [t for t in str(rest or "").split() if t.strip()]
    sub = (tokens[0].lower() if tokens else "status").strip()
    if sub in {"", "show"}:
        sub = "status"
    if sub not in {"status", "on", "off", "start", "stop", "recover"}:
        raise RuntimeError("usage: /auto [on|off|status|recover]")

    command = None
    for tok in tokens[1:]:
        low = tok.strip().lower()
        if low in {"fanout", "wave", "oneeach", "round"}:
            command = "fanout"
        elif low in {"next", "global"}:
            command = "next"

    prefetch = None
    for tok in tokens[1:]:
        low = tok.strip().lower()
        if low in {"recent", "docs", "prefetch", "sync-recent", "recent-docs"}:
            prefetch = "sync_recent"
        elif low in {"no-recent", "no-docs", "noprefetch", "no-prefetch"}:
            prefetch = ""
    replace_sync = parse_replace_sync_flag(tokens[1:])

    prefetch_since = None
    i = 1
    while i < len(tokens):
        tok = str(tokens[i] or "").strip()
        low = tok.lower()
        if low in {"since", "--since", "-s", "within", "--within"}:
            if i + 1 < len(tokens):
                prefetch_since = str(tokens[i + 1] or "").strip()
                i += 2
            else:
                i += 1
            continue
        if low.startswith("since=") or low.startswith("--since=") or low.startswith("-s=") or low.startswith("within="):
            prefetch_since = tok.split("=", 1)[1].strip() if "=" in tok else ""
            i += 1
            continue
        i += 1

    force = any(t.lower() in {"force", "!", "--force"} for t in tokens[1:])
    interval_sec = None
    idle_sec = None
    max_failures = None
    nums = [t for t in tokens[1:] if t.replace(".", "", 1).isdigit()]
    if nums:
        try:
            interval_sec = max(1, min(300, int(float(nums[0]))))
        except Exception:
            interval_sec = None
    if len(nums) >= 2:
        try:
            idle_sec = max(1, min(3600, int(float(nums[1]))))
        except Exception:
            idle_sec = None

    for tok in tokens[1:]:
        low = tok.strip().lower()
        if not any(
            low.startswith(p)
            for p in {
                "maxfail=",
                "maxfails=",
                "maxfailures=",
                "max_fail=",
                "max_fails=",
                "max_failures=",
            }
        ):
            continue
        raw = tok.split("=", 1)[1].strip() if "=" in tok else ""
        try:
            max_failures = max(1, min(50, int(float(raw))))
        except Exception:
            max_failures = None

    path = auto_state_path(args)
    current = load_auto_state(path)
    provider_state_path = provider_capacity_state_path(args)
    provider_state = load_provider_capacity_state(provider_state_path)
    enabled = bool(current.get("enabled", False))
    session = scheduler_session_name()
    sess_up = tmux_has_session(session)
    focus_row = project_lock_row(manager_state)
    focus_label = project_lock_label(manager_state) or "-"
    fallback_level = str(getattr(args, "default_report_level", "normal") or "normal").strip().lower()
    current_report_level = get_chat_report_level(manager_state, chat_id, fallback_level)
    status_level = status_report_level(tokens, current_report_level)

    if sub == "status":
        latest_intent = load_latest_command_resolution(getattr(args, "team_dir", ""))
        latest_action = load_latest_action_audit(getattr(args, "team_dir", ""))
        recovery_action = _capacity_recovery_action(current, provider_state, manager_state)
        recovery_target = _capacity_recovery_target(
            current,
            focus_row=focus_row,
            normalize_prefetch_token=normalize_prefetch_token,
            prefetch_display=prefetch_display,
        )
        recovery_repeat = _recovery_repeat_snapshot(current, manager_state)
        chat_ref = str(current.get("chat_id", "")).strip() or "-"
        eff_force = bool(current.get("force", False))
        eff_command = str(current.get("command", "next")).strip().lower() or "next"
        if eff_command not in {"next", "fanout"}:
            eff_command = "next"
        prefetch_token = normalize_prefetch_token(current.get("prefetch", ""))
        replace_sync_enabled = bool(current.get("prefetch_replace_sync", False))
        eff_interval = int(current.get("interval_sec") or default_auto_interval_sec)
        eff_idle = int(current.get("idle_sec") or default_auto_idle_sec)
        eff_max_fail = int(current.get("max_failures") or default_auto_max_failures)
        last_reason = str(current.get("last_reason", "")).strip()
        last_run = str(current.get("last_run_at", "")).strip()
        last_candidate = str(current.get("last_candidate", "")).strip()
        last_prefetch_at = str(current.get("last_prefetch_at", "")).strip()
        last_prefetch_reason = str(current.get("last_prefetch_reason", "")).strip()
        last_prefetch_mode = str(current.get("last_prefetch_mode", "")).strip()
        next_retry_at = str(current.get("next_retry_at", "")).strip()
        recovery_grace_until = str(current.get("recovery_grace_until", "")).strip()
        next_retry_target = _next_rate_limited_task_snapshot(manager_state)
        capacity_summary = _rate_limited_capacity_summary(manager_state)
        repeat_memory = _provider_capacity_repeat_memory(provider_state)
        capacity_policy = _provider_capacity_policy(capacity_summary, recovery_repeat, repeat_memory)
        stuck_candidate = str(current.get("stuck_candidate", "")).strip()
        stuck_count = int(current.get("stuck_count") or 0)
        fail_count = int(current.get("fail_count") or 0)
        fail_candidate = str(current.get("fail_candidate", "")).strip()
        fail_reason = str(current.get("fail_reason", "")).strip()
        lines = [
            "auto scheduler",
            f"- enabled: {'yes' if enabled else 'no'}",
            f"- project_lock: {focus_label}",
            f"- report_view: {status_level}",
            f"- chat_id: {chat_ref}",
            f"- command: {eff_command}",
            f"- prefetch: {prefetch_display(prefetch_token, current.get('prefetch_since', ''), replace_sync_enabled)}",
            f"- force: {'yes' if eff_force else 'no'}",
            f"- interval_sec: {eff_interval}",
            f"- idle_sec: {eff_idle}",
            f"- max_failures: {eff_max_fail}",
            f"- tmux_session: {session} ({'up' if sess_up else 'down'})",
        ]
        if last_run:
            lines.append(f"- last_run_at: {last_run}")
        if last_candidate:
            lines.append(f"- last_candidate: {last_candidate}")
        if last_reason:
            lines.append(f"- last_reason: {compact_reason(last_reason, 120)}")
        if next_retry_at:
            lines.append(f"- next_retry_at: {next_retry_at}")
        if recovery_grace_until:
            lines.append(f"- recovery_grace_until: {recovery_grace_until}")
        append_operator_status_lines(
            lines,
            latest_intent=latest_intent,
            latest_action=latest_action,
            compact_reason=compact_reason,
            line_prefix="- ",
        )
        if capacity_summary:
            lines.append(
                "- provider_capacity: tasks={tasks} projects={projects} providers={providers}".format(
                    tasks=capacity_summary.get("task_count", "0"),
                    projects=capacity_summary.get("project_count", "0"),
                    providers=capacity_summary.get("provider_summary", "-"),
                )
            )
        if capacity_policy:
            lines.append(
                "- capacity_policy: {level} | {reason}".format(
                    level=capacity_policy.get("level", "-"),
                    reason=capacity_policy.get("reason", "-"),
                )
            )
            lines.append(f"- capacity_operator_action: {capacity_policy.get('operator_action', '-')}")
        if recovery_repeat:
            lines.append(f"- capacity_recovery_repeat: {recovery_repeat.get('summary', '-')}")
        if recovery_action:
            lines.append(f"- capacity_recovery_action: {recovery_action.get('action', '-')}")
            lines.append(f"- capacity_recovery_reason: {recovery_action.get('reason', '-')}")
            lines.append(f"- capacity_recovery_target: {recovery_target.get('target', '-')}")
            if recovery_target.get("adjusted_reason"):
                lines.append(f"- capacity_recovery_note: {recovery_target.get('adjusted_reason', '-')}")
        repeat_summary_line = _provider_capacity_repeat_summary_line(provider_state)
        if repeat_summary_line:
            lines.append(repeat_summary_line)
        lines.extend(_provider_capacity_memory_lines(provider_state))
        if next_retry_target:
            lines.append(
                "- next_retry_target: {alias} {task_ref} providers={providers} degraded={degraded}".format(
                    alias=next_retry_target.get("alias", "-"),
                    task_ref=next_retry_target.get("task_ref", "-"),
                    providers=next_retry_target.get("providers", "-"),
                    degraded=next_retry_target.get("degraded", "-"),
                )
            )
        if stuck_count and stuck_candidate:
            lines.append(f"- stuck: {stuck_count} ({stuck_candidate})")
        if fail_count:
            suffix = f" ({fail_candidate})" if fail_candidate else ""
            lines.append(f"- fail_count: {fail_count}{suffix}")
        if fail_reason:
            lines.append(f"- fail_reason: {compact_reason(fail_reason, 120)}")
            if status_level == "long" and compact_reason(fail_reason, 120) != fail_reason:
                lines.append(f"- fail_reason_full: {fail_reason}")
        if last_prefetch_at:
            lines.append(f"- last_prefetch_at: {last_prefetch_at}")
        if last_prefetch_mode:
            lines.append(f"- last_prefetch_mode: {last_prefetch_mode}")
        if last_prefetch_reason:
            lines.append(f"- last_prefetch_reason: {compact_reason(last_prefetch_reason, 120)}")
        snapshot_lines = focused_project_snapshot_lines(manager_state)
        if status_level == "long" and snapshot_lines:
            lines.extend([""] + snapshot_lines)
        compact_lines = ops_scope_compact_lines(manager_state, 4, status_level)
        if compact_lines:
            lines.extend(["", "ops projects:"] + compact_lines)
        lines.extend(
            [
                "",
                "set:",
                "- /auto on",
                "- /auto on fanout",
                "- /auto on fanout recent",
                "- /auto on fanout recent replace-sync",
                "- /auto on fanout recent since 3h",
                "- /auto off",
                "- /auto recover",
                "- /auto on force",
                "- /auto on maxfail=3",
                "- /auto on <interval_sec> <idle_sec>",
            ]
        )
        send("\n".join(lines).strip(), context="auto-status", with_menu=True)
        return True

    if chat_role == "readonly":
        send(
            "permission denied: readonly chat cannot change auto scheduler.\n"
            "read-only: /auto (status only)",
            context="auto-deny",
            with_menu=True,
        )
        return True

    if sub in {"off", "stop"}:
        capacity_summary = _rate_limited_capacity_summary(manager_state)
        repeat_memory = _provider_capacity_repeat_memory(provider_state)
        capacity_policy = _provider_capacity_policy(capacity_summary, recovery_repeat_memory=repeat_memory)
        override_history = provider_state.get("override_history") if isinstance(provider_state.get("override_history"), list) else []
        override_entry = {
            "at": now_iso(),
            "action": "/auto off",
            "source": "operator",
            "policy_level": str(capacity_policy.get("level", "")).strip() or "manual",
            "policy_reason": str(capacity_policy.get("reason", "")).strip(),
            "providers": str(capacity_summary.get("provider_summary", "")).strip(),
        }
        override_history = [row for row in override_history if isinstance(row, dict)][-9:] + [override_entry]
        provider_state["override_history"] = override_history
        current["enabled"] = False
        current["chat_id"] = str(current.get("chat_id", "")).strip() or str(chat_id)
        current["stopped_at"] = now_iso()
        if not args.dry_run:
            save_auto_state(path, current)
            save_provider_capacity_state(provider_state_path, provider_state)
        if args.dry_run:
            ok, out = True, "dry-run: skipped tmux auto off"
        else:
            ok, out = tmux_auto_command(args, "off")
        send(
            "auto scheduler updated\n"
            "- enabled: no\n"
            f"- tmux: {'stopped' if ok else 'stop_failed'}\n"
            f"- detail: {out or '-'}",
            context="auto-off",
            with_menu=True,
        )
        return True

    if sub == "recover":
        force_recover = force
        retry_at = _next_rate_limited_retry_at(manager_state)
        retry_dt = _parse_iso_datetime(retry_at)
        now_dt = _parse_iso_datetime(now_iso()) or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        if retry_dt is not None and retry_dt > now_dt.astimezone(timezone.utc) and not force_recover:
            if callable(record_outcome):
                record_outcome(
                    {
                        "kind": "auto_recover",
                        "status": "blocked",
                        "reason_code": "provider_capacity_blocked",
                        "next_step": "/offdesk review",
                        "detail": f"next_retry_at={retry_at}" if retry_at else "provider capacity is still blocked",
                    }
                )
            send(
                "auto recovery blocked\n"
                f"- next_retry_at: {retry_at}\n"
                "- reason: provider capacity is still blocked\n"
                "next:\n"
                "- /auto status\n"
                "- /auto recover force\n"
                "- wait until retry_at, then /auto recover",
                context="auto-recover-blocked",
                with_menu=True,
            )
            return True

        recovery_target = _capacity_recovery_target(
            current,
            focus_row=focus_row,
            normalize_prefetch_token=normalize_prefetch_token,
            prefetch_display=prefetch_display,
        )
        effective_command = str(recovery_target.get("command", "next")).strip().lower() or "next"

        provider_state = _prune_provider_capacity_state(provider_state, now=now_dt)
        override_history = provider_state.get("override_history") if isinstance(provider_state.get("override_history"), list) else []
        override_entry = {
            "at": now_iso(),
            "action": "/auto recover" + (" force" if force_recover else ""),
            "source": "operator",
            "policy_level": "manual",
            "policy_reason": "resume auto after provider capacity interruption",
            "providers": str(_rate_limited_capacity_summary(manager_state).get("provider_summary", "")).strip(),
        }
        override_history = [row for row in override_history if isinstance(row, dict)][-9:] + [override_entry]
        provider_state["override_history"] = override_history

        current["enabled"] = True
        current["chat_id"] = str(current.get("chat_id", "")).strip() or str(chat_id)
        current["command"] = effective_command
        current["recovered_at"] = now_iso()
        current["recovery_grace_until"] = (
            now_dt.astimezone(timezone.utc) + timedelta(seconds=_PROVIDER_RECOVERY_GRACE_SEC)
        ).replace(microsecond=0).isoformat()
        current["recovery_project_aliases"] = _rate_limited_project_aliases(manager_state)
        current.pop("stopped_at", None)
        current.pop("next_retry_at", None)
        current.pop("stuck_candidate", None)
        current.pop("stuck_count", None)
        current.pop("fail_count", None)
        current.pop("fail_candidate", None)
        current.pop("fail_reason", None)

        if not args.dry_run:
            save_auto_state(path, current)
            save_provider_capacity_state(provider_state_path, provider_state)
        if args.dry_run:
            ok, out = True, "dry-run: skipped tmux auto recover"
        else:
            ok, out = tmux_auto_command(args, "on")
        if callable(record_outcome):
            record_outcome(
                {
                    "kind": "auto_recover",
                    "status": "executed" if ok else "blocked",
                    "reason_code": "auto_recover_started" if ok else "tmux_start_failed",
                    "next_step": "/auto status" if ok else "/offdesk review",
                    "detail": str(out or "-").strip(),
                }
            )
        send(
            "auto scheduler recovered\n"
            "- enabled: yes\n"
            f"- command: {effective_command}\n"
            f"- resume_target: {recovery_target.get('target', '-')}\n"
            + (f"- resume_note: {recovery_target.get('adjusted_reason', '-')}\n" if recovery_target.get("adjusted_reason") else "")
            + f"- recovery_grace_until: {current.get('recovery_grace_until', '-')}\n"
            +
            f"- force: {'yes' if force_recover else 'no'}\n"
            f"- tmux: {'started' if ok else 'start_failed'}\n"
            f"- detail: {out or '-'}\n"
            "next:\n"
            "- /auto status\n"
            "- /queue\n"
            "- /offdesk review",
            context="auto-recover",
            with_menu=True,
        )
        return True

    effective_command = command if command in {"next", "fanout"} else str(current.get("command", "next")).strip().lower() or "next"
    if effective_command not in {"next", "fanout"}:
        effective_command = "next"
    if focus_row and effective_command == "fanout":
        send(
            "auto scheduler blocked\n"
            f"- project_lock: {focus_label}\n"
            "- reason: fanout is a global multi-project wave\n"
            "next:\n"
            "- /auto on next\n"
            "- /offdesk on\n"
            "- /focus off",
            context="auto-on-blocked",
            with_menu=True,
        )
        return True

    current["enabled"] = True
    current["chat_id"] = str(chat_id)
    if "started_at" not in current:
        current["started_at"] = now_iso()
    current["command"] = effective_command
    current.pop("recovery_grace_until", None)
    current.pop("recovery_project_aliases", None)
    if prefetch is not None:
        current["prefetch"] = prefetch
    elif "prefetch" not in current:
        current["prefetch"] = ""
    if replace_sync is not None:
        current["prefetch_replace_sync"] = bool(replace_sync)
    elif "prefetch_replace_sync" not in current:
        current["prefetch_replace_sync"] = False
    if prefetch_since is not None:
        current["prefetch_since"] = str(prefetch_since or "").strip()
    elif "prefetch_since" not in current:
        current["prefetch_since"] = ""
    if bool(current.get("prefetch_replace_sync", False)) and not normalize_prefetch_token(current.get("prefetch", "")):
        current["prefetch"] = "sync_recent"
    if not normalize_prefetch_token(current.get("prefetch", "")):
        current["prefetch_replace_sync"] = False
    if force:
        current["force"] = True
    elif "force" not in current:
        current["force"] = False
    if interval_sec is not None:
        current["interval_sec"] = interval_sec
    elif "interval_sec" not in current:
        current["interval_sec"] = default_auto_interval_sec
    if idle_sec is not None:
        current["idle_sec"] = idle_sec
    elif "idle_sec" not in current:
        current["idle_sec"] = default_auto_idle_sec
    if max_failures is not None:
        current["max_failures"] = int(max_failures)
    elif "max_failures" not in current:
        current["max_failures"] = default_auto_max_failures
    if not args.dry_run:
        save_auto_state(path, current)

    if args.dry_run:
        ok, out = True, "dry-run: skipped tmux auto on"
    else:
        ok, out = tmux_auto_command(args, "on")
    prefetch_token = normalize_prefetch_token(current.get("prefetch", ""))
    replace_sync_enabled = bool(current.get("prefetch_replace_sync", False))
    body = (
        "auto scheduler updated\n"
        "- enabled: yes\n"
        f"- command: {str(current.get('command', 'next')).strip() or 'next'}\n"
        f"- prefetch: {prefetch_display(prefetch_token, current.get('prefetch_since', ''), replace_sync_enabled)}\n"
        f"- force: {'yes' if bool(current.get('force', False)) else 'no'}\n"
        f"- interval_sec: {int(current.get('interval_sec') or default_auto_interval_sec)}\n"
        f"- idle_sec: {int(current.get('idle_sec') or default_auto_idle_sec)}\n"
        f"- tmux: {'started' if ok else 'start_failed'}\n"
        f"- detail: {out or '-'}\n"
    )
    if focus_row:
        body += f"- project_lock: {focus_label}\n"
    snapshot_lines = focused_project_snapshot_lines(manager_state)
    if snapshot_lines:
        body += "\n" + "\n".join(snapshot_lines) + "\n"
    body += "next:\n- /queue\n- /auto status"
    send(body, context="auto-on", with_menu=True)
    return True


