#!/usr/bin/env python3
"""Scheduler-control command handlers extracted from management handlers."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_action_audit import load_latest_action_audit
from aoe_tg_operator_summary import load_latest_command_resolution
from aoe_tg_operator_surface import append_operator_status_lines
from aoe_tg_scheduler_capacity import (
    _PROVIDER_RECOVERY_GRACE_SEC,
    _annotate_reports_with_provider_repeat_memory,
    _capacity_recovery_action,
    _capacity_recovery_target,
    _compact_text,
    _next_rate_limited_task_snapshot,
    _next_rate_limited_retry_at,
    _parse_iso_datetime,
    _provider_capacity_memory_lines,
    _provider_capacity_policy,
    _provider_capacity_repeat_memory,
    _provider_capacity_repeat_summary_line,
    _prune_provider_capacity_state,
    _rate_limited_capacity_summary,
    _rate_limited_capacity_summary_for_reports,
    _rate_limited_project_aliases,
    _recovery_repeat_snapshot,
)
from aoe_tg_scheduler_focus_panic import _handle_focus_command, _handle_panic_command

def _handle_offdesk_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    get_default_mode: Callable[[Dict[str, Any], str], str],
    get_pending_mode: Callable[[Dict[str, Any], str], str],
    get_chat_report_level: Callable[[Dict[str, Any], str, str], str],
    get_chat_room: Callable[[Dict[str, Any], str, str], str],
    set_default_mode: Callable[[Dict[str, Any], str, str], None],
    set_chat_report_level: Callable[[Dict[str, Any], str, str], None],
    set_chat_room: Callable[[Dict[str, Any], str, str], None],
    clear_default_mode: Callable[[Dict[str, Any], str], bool],
    clear_pending_mode: Callable[[Dict[str, Any], str], bool],
    clear_confirm_action: Callable[[Dict[str, Any], str], bool],
    clear_chat_report_level: Callable[[Dict[str, Any], str], bool],
    save_manager_state: Callable[..., None],
    parse_replace_sync_flag: Callable[[List[str]], bool | None],
    status_report_level: Callable[[List[str], str], str],
    prefetch_display: Callable[[Any, Any, bool], str],
    focused_project_snapshot_lines: Callable[[Dict[str, Any]], List[str]],
    ops_scope_summary: Callable[[Dict[str, Any]], Dict[str, List[str]]],
    ops_scope_compact_lines: Callable[[Dict[str, Any], int, str], List[str]],
    project_lock_row: Callable[[Dict[str, Any]], Dict[str, Any]],
    project_lock_label: Callable[[Dict[str, Any]], str],
    offdesk_prepare_targets: Callable[[Dict[str, Any], str], List[tuple[str, Dict[str, Any]]]],
    offdesk_prepare_project_report: Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]],
    sort_offdesk_reports: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    offdesk_review_reply_markup: Callable[[List[Dict[str, Any]], bool], Dict[str, Any]],
    offdesk_prepare_reply_markup: Callable[[List[Dict[str, Any]], int, bool], Dict[str, Any]],
    auto_state_path: Callable[[Any], Any],
    offdesk_state_path: Callable[[Any], Any],
    provider_capacity_state_path: Callable[[Any], Any],
    load_auto_state: Callable[[Any], Dict[str, Any]],
    save_auto_state: Callable[[Any, Dict[str, Any]], None],
    load_offdesk_state: Callable[[Any], Dict[str, Any]],
    save_offdesk_state: Callable[[Any, Dict[str, Any]], None],
    load_provider_capacity_state: Callable[[Any], Dict[str, Any]],
    save_provider_capacity_state: Callable[[Any, Dict[str, Any]], None],
    tmux_auto_command: Callable[[Any, str], tuple[bool, str]],
    now_iso: Callable[[], str],
    default_offdesk_command: str,
    default_offdesk_prefetch: str,
    default_offdesk_prefetch_since: str,
    default_offdesk_report_level: str,
    default_offdesk_room: str,
    default_auto_interval_sec: int,
    default_auto_idle_sec: int,
) -> bool:
    tokens = [t for t in str(rest or "").split() if t.strip()]
    sub = (tokens[0].lower() if tokens else "status").strip()
    if sub in {"", "show"}:
        sub = "status"
    if sub not in {"status", "on", "off", "start", "stop", "prepare", "preflight", "check", "review"}:
        raise RuntimeError("usage: /offdesk [on|off|status|prepare|review] [replace-sync|O#|name|all]")
    replace_sync = parse_replace_sync_flag(tokens[1:])

    fallback_level = str(getattr(args, "default_report_level", "normal") or "normal").strip().lower()
    current_default_mode = get_default_mode(manager_state, chat_id) or "off"
    current_pending_mode = get_pending_mode(manager_state, chat_id) or "none"
    current_report_level = get_chat_report_level(manager_state, chat_id, fallback_level)
    status_level = status_report_level(tokens, current_report_level)
    current_room = get_chat_room(manager_state, chat_id, default_offdesk_room) or default_offdesk_room

    off_path = offdesk_state_path(args)
    off_state = load_offdesk_state(off_path)
    off_enabled = bool(off_state.get("enabled", False))
    provider_state_path = provider_capacity_state_path(args)
    provider_state = load_provider_capacity_state(provider_state_path)

    auto_path = auto_state_path(args)
    auto_state = load_auto_state(auto_path)
    auto_enabled = bool(auto_state.get("enabled", False))
    auto_cmd = str(auto_state.get("command", "")).strip().lower() or "next"
    auto_prefetch = str(auto_state.get("prefetch", "")).strip().lower()
    auto_replace_sync = bool(auto_state.get("prefetch_replace_sync", False))
    focus_label = project_lock_label(manager_state) or "-"
    scope_summary = ops_scope_summary(manager_state)
    included_scope = ", ".join(scope_summary.get("included", [])[:6]) or "-"
    excluded_scope = ", ".join(scope_summary.get("excluded", [])[:6]) or "-"

    if sub == "status":
        latest_intent = load_latest_command_resolution(getattr(args, "team_dir", ""))
        latest_action = load_latest_action_audit(getattr(args, "team_dir", ""))
        lines = [
            "offdesk mode",
            f"- enabled: {'yes' if off_enabled else 'no'}",
            f"- project_lock: {focus_label}",
            f"- ops_scope: {included_scope}",
            f"- ops_excluded: {excluded_scope}",
            f"- report_view: {status_level}",
            f"- routing_mode: {current_default_mode}",
            f"- one_shot_pending: {current_pending_mode}",
            f"- report_level: {current_report_level}",
            f"- room: {current_room}",
            f"- auto_enabled: {'yes' if auto_enabled else 'no'}",
            f"- auto_command: {auto_cmd}",
            f"- auto_prefetch: {prefetch_display(auto_prefetch, auto_state.get('prefetch_since', ''), auto_replace_sync)}",
            "",
            "set:",
            "- /offdesk on",
            "- /offdesk on replace-sync",
            "- /offdesk off",
            "- /auto status",
        ]
        append_operator_status_lines(
            lines,
            latest_intent=latest_intent,
            latest_action=latest_action,
            compact_reason=_compact_text,
            line_prefix="- ",
        )
        snapshot_lines = focused_project_snapshot_lines(manager_state)
        if status_level == "long" and snapshot_lines:
            lines.extend([""] + snapshot_lines)
        compact_lines = ops_scope_compact_lines(manager_state, 4, status_level)
        if compact_lines:
            lines.extend(["", "ops projects:"] + compact_lines)
        send("\n".join(lines).strip(), context="offdesk-status", with_menu=True)
        return True

    if sub in {"prepare", "preflight", "check"}:
        latest_intent = load_latest_command_resolution(getattr(args, "team_dir", ""))
        latest_action = load_latest_action_audit(getattr(args, "team_dir", ""))
        raw_target = ""
        for tok in tokens[1:]:
            low = str(tok or "").strip().lower()
            if low in {
                "replace-sync",
                "sync-replace",
                "replace_prefetch",
                "prefetch-replace",
                "no-replace-sync",
                "safe-sync",
                "no-sync-replace",
            }:
                continue
            raw_target = str(tok or "").strip()
            break
        try:
            targets = offdesk_prepare_targets(manager_state, raw_target)
        except RuntimeError as exc:
            send(str(exc).strip(), context="offdesk-prepare blocked", with_menu=True)
            return True
        if not targets:
            lines = ["offdesk prepare", "- no orch projects registered"]
            append_operator_status_lines(
                lines,
                latest_intent=latest_intent,
                latest_action=latest_action,
                compact_reason=_compact_text,
                line_prefix="- ",
            )
            send("\n".join(lines).strip(), context="offdesk-prepare empty", with_menu=True)
            return True

        reports = [offdesk_prepare_project_report(manager_state, key, entry) for key, entry in targets]
        reports = _annotate_reports_with_provider_repeat_memory(reports, provider_state)
        reports = sort_offdesk_reports(reports)
        ready_count = sum(1 for row in reports if row.get("status") == "ready")
        warn_count = sum(1 for row in reports if row.get("status") == "warn")
        blocked_count = sum(1 for row in reports if row.get("status") == "blocked")
        scope_label = project_lock_label(manager_state) or ("all" if len(targets) > 1 else reports[0].get("alias", "-"))
        scope_summary = ops_scope_summary(manager_state)
        included_scope = ", ".join(scope_summary.get("included", [])[:6]) or "-"
        excluded_scope = ", ".join(scope_summary.get("excluded", [])[:6]) or "-"
        lines = [
            "offdesk prepare",
            f"- scope: {scope_label}",
            f"- ops_scope: {included_scope}",
            f"- ops_excluded: {excluded_scope}",
            f"- projects: {len(targets)}",
            f"- ready: {ready_count}",
            f"- warn: {warn_count}",
            f"- blocked: {blocked_count}",
        ]
        append_operator_status_lines(
            lines,
            latest_intent=latest_intent,
            latest_action=latest_action,
            compact_reason=_compact_text,
            line_prefix="- ",
        )
        lines.extend(_provider_capacity_memory_lines(provider_state))
        lines.extend(["", "projects:"])
        for report in reports:
            lines.extend(report.get("lines") or [])

        lines.extend(["", "next:"])
        if blocked_count == 0:
            lines.append("- /offdesk on")
        else:
            lines.append("- fix blocked items before /offdesk on")
        if len(targets) == 1:
            alias = str(reports[0].get("alias", "")).strip() or "-"
            lines.append(f"- /sync preview {alias} 24h")
            lines.append(f"- /todo {alias}")
            lines.append(f"- /todo {alias} syncback preview")
        else:
            lines.append("- /map")
            lines.append("- /queue")
            lines.append("- /todo proposals")
        send(
            "\n".join(lines).strip(),
            context="offdesk-prepare",
            with_menu=True,
            reply_markup=offdesk_prepare_reply_markup(
                reports,
                blocked_count,
                warn_count == 0 and blocked_count == 0,
            ),
        )
        return True

    if sub == "review":
        latest_intent = load_latest_command_resolution(getattr(args, "team_dir", ""))
        latest_action = load_latest_action_audit(getattr(args, "team_dir", ""))
        raw_target = ""
        for tok in tokens[1:]:
            low = str(tok or "").strip().lower()
            if low in {
                "replace-sync",
                "sync-replace",
                "replace_prefetch",
                "prefetch-replace",
                "no-replace-sync",
                "safe-sync",
                "no-sync-replace",
            }:
                continue
            raw_target = str(tok or "").strip()
            break
        try:
            targets = offdesk_prepare_targets(manager_state, raw_target)
        except RuntimeError as exc:
            send(str(exc).strip(), context="offdesk-review blocked", with_menu=True)
            return True
        if not targets:
            capacity_summary = _rate_limited_capacity_summary(manager_state)
            recovery_repeat = _recovery_repeat_snapshot(auto_state, manager_state)
            repeat_memory = _provider_capacity_repeat_memory(provider_state)
            capacity_policy = _provider_capacity_policy(capacity_summary, recovery_repeat, repeat_memory)
            recovery_action = _capacity_recovery_action(auto_state, provider_state, manager_state)
            recovery_grace_until = str(auto_state.get("recovery_grace_until", "")).strip()
            recovery_target = _capacity_recovery_target(
                auto_state,
                focus_row=project_lock_row(manager_state),
                normalize_prefetch_token=lambda raw: str(raw or "").strip().lower(),
                prefetch_display=prefetch_display,
            )
            lines = ["offdesk review", "- no orch projects registered"]
            append_operator_status_lines(
                lines,
                latest_intent=latest_intent,
                latest_action=latest_action,
                compact_reason=_compact_text,
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
            if recovery_grace_until:
                lines.append(f"- recovery_grace_until: {recovery_grace_until}")
            repeat_summary_line = _provider_capacity_repeat_summary_line(provider_state)
            if repeat_summary_line:
                lines.append(repeat_summary_line)
            lines.extend(_provider_capacity_memory_lines(provider_state))
            send(
                "\n".join(lines).strip(),
                context="offdesk-review empty",
                with_menu=True,
                reply_markup=offdesk_review_reply_markup(
                    [],
                    clean=True,
                    capacity_operator_action=str(capacity_policy.get("operator_action", "")).strip() if capacity_policy else "",
                    capacity_recovery_action=str(recovery_action.get("action", "")).strip() if recovery_action else "",
                ),
            )
            return True

        reports = [offdesk_prepare_project_report(manager_state, key, entry) for key, entry in targets]
        reports = _annotate_reports_with_provider_repeat_memory(reports, provider_state)
        reports = sort_offdesk_reports(reports)
        flagged = [row for row in reports if str(row.get("status", "")).strip().lower() in {"warn", "blocked"}]
        capacity_summary = _rate_limited_capacity_summary_for_reports(reports)
        recovery_repeat = _recovery_repeat_snapshot(auto_state, manager_state)
        repeat_memory = _provider_capacity_repeat_memory(provider_state)
        capacity_policy = _provider_capacity_policy(capacity_summary, recovery_repeat, repeat_memory)
        recovery_action = _capacity_recovery_action(auto_state, provider_state, manager_state)
        recovery_grace_until = str(auto_state.get("recovery_grace_until", "")).strip()
        recovery_target = _capacity_recovery_target(
            auto_state,
            focus_row=project_lock_row(manager_state),
            normalize_prefetch_token=lambda raw: str(raw or "").strip().lower(),
            prefetch_display=prefetch_display,
        )
        lines = [
            "offdesk review",
            f"- reviewed: {len(reports)}",
            f"- flagged: {len(flagged)}",
        ]
        append_operator_status_lines(
            lines,
            latest_intent=latest_intent,
            latest_action=latest_action,
            compact_reason=_compact_text,
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
        if recovery_grace_until:
            lines.append(f"- recovery_grace_until: {recovery_grace_until}")
        repeat_summary_line = _provider_capacity_repeat_summary_line(provider_state)
        if repeat_summary_line:
            lines.append(repeat_summary_line)
        lines.extend(_provider_capacity_memory_lines(provider_state))
        if not flagged:
            lines.extend(["- status: clean", "", "next:", "- /offdesk on", "- /auto status"])
            send(
                "\n".join(lines).strip(),
                context="offdesk-review clean",
                with_menu=True,
                reply_markup=offdesk_review_reply_markup(
                    [],
                    clean=True,
                    capacity_recovery_action=str(recovery_action.get("action", "")).strip() if recovery_action else "",
                ),
            )
            return True

        lines.extend(["", "actions:"])
        for row in flagged:
            alias = str(row.get("alias", "")).strip() or "-"
            display = str(row.get("display", "")).strip() or alias
            actions: List[str] = []
            first_action = str(row.get("priority_action", "")).strip()
            if first_action:
                actions.append(first_action)
            active_rate_limit = row.get("active_task_rate_limit") if isinstance(row.get("active_task_rate_limit"), dict) else {}
            if active_rate_limit:
                actions.append("/auto status")
            if bool(row.get("syncback_pending", False)):
                actions.append(f"/todo {alias} syncback preview")
            if int(row.get("proposals", 0) or 0) > 0:
                actions.append(f"/todo {alias} proposals")
            if int(row.get("followup_count", 0) or 0) > 0:
                actions.append(f"/todo {alias} followup")
            active_task_label = str(row.get("active_task_label", "")).strip()
            active_task_tf_phase = str(row.get("active_task_tf_phase", "")).strip()
            if active_task_label and active_task_tf_phase in {"needs_retry", "manual_intervention", "critic_review", "blocked", "rate_limited"}:
                actions.append(f"/task {active_task_label}")
            if bool(row.get("bootstrap_recommended", False)):
                actions.append(f"/sync bootstrap {alias} 24h")
            if (
                int(row.get("blocked_count", 0) or 0) > 0
                or int(row.get("open", 0) or 0) == 0
                or bool(row.get("sync_quality_warn", False))
            ):
                actions.append(f"/sync preview {alias} 24h")
            if bool(row.get("pending_flag", False)) or int(row.get("running", 0) or 0) > 0:
                actions.append(f"/orch status {alias}")
            if not actions:
                actions.append(f"/todo {alias}")
            lines.append(f"- {alias} {display} [{row.get('status', '-')}]")
            lines.append(f"  attention: {str(row.get('attention_summary', '-')).strip() or '-'}")
            first_reason = str(row.get("priority_reason", "")).strip() or "-"
            first_action = first_action or "-"
            lines.append(f"  first: {first_action} | {first_reason}")
            active_degraded_by = [str(x).strip() for x in (row.get("active_task_degraded_by") or []) if str(x).strip()]
            if active_rate_limit:
                providers = [
                    str(x).strip()
                    for x in (active_rate_limit.get("limited_providers") or [])
                    if str(x).strip()
                ]
                retry_at = str(active_rate_limit.get("retry_at", "")).strip() or "-"
                lines.append(
                    "  provider_capacity: providers={providers} retry_at={retry_at} degraded={degraded}".format(
                        providers=",".join(providers) if providers else "-",
                        retry_at=retry_at,
                        degraded=",".join(active_degraded_by) if active_degraded_by else "-",
                    )
                )
            note_rows = list(row.get("notes") or [])
            for note in note_rows[:2]:
                lines.append(f"  note: {note}")
            proposal_triage = row.get("proposal_triage") if isinstance(row.get("proposal_triage"), dict) else {}
            if int(proposal_triage.get("open_count", 0) or 0) > 0:
                lines.append(
                    "  proposal_triage: priorities={priorities} | kinds={kinds}".format(
                        priorities=str(proposal_triage.get("priority_summary", "-")).strip() or "-",
                        kinds=str(proposal_triage.get("kind_summary", "-")).strip() or "-",
                    )
                )
                top_summary = str(proposal_triage.get("top_summary", "")).strip()
                if top_summary:
                    lines.append(f"  proposal_top: {top_summary}")
            dedup_actions: List[str] = []
            seen_actions: set[str] = set()
            for action in actions:
                text = str(action or "").strip()
                if not text or text in seen_actions:
                    continue
                seen_actions.add(text)
                dedup_actions.append(text)
            lines.append(f"  do: {', '.join(dedup_actions)}")

        lines.extend(["", "next:", "- resolve flagged items, then /offdesk on", "- /offdesk prepare"])
        send(
            "\n".join(lines).strip(),
            context="offdesk-review",
            with_menu=True,
            reply_markup=offdesk_review_reply_markup(
                flagged,
                clean=False,
                capacity_operator_action=str(capacity_policy.get("operator_action", "")).strip() if capacity_policy else "",
                capacity_recovery_action=str(recovery_action.get("action", "")).strip() if recovery_action else "",
            ),
        )
        return True

    if chat_role == "readonly":
        send(
            "permission denied: readonly chat cannot change offdesk mode.\n"
            "read-only: /offdesk (status/prepare only)",
            context="offdesk-deny",
            with_menu=True,
        )
        return True

    if sub in {"off", "stop"}:
        prev = off_state.get("prev") if isinstance(off_state.get("prev"), dict) else {}

        prev_mode_present = bool(prev.get("default_mode_present", False))
        prev_mode = str(prev.get("default_mode", "")).strip().lower()
        if prev_mode_present and prev_mode in {"dispatch", "direct"}:
            set_default_mode(manager_state, chat_id, prev_mode)
        else:
            clear_default_mode(manager_state, chat_id)

        prev_report_present = bool(prev.get("report_level_present", False))
        prev_report = str(prev.get("report_level", "")).strip().lower()
        if prev_report_present and prev_report in {"short", "normal", "long"}:
            set_chat_report_level(manager_state, chat_id, prev_report)
        else:
            clear_chat_report_level(manager_state, chat_id)

        prev_room_present = bool(prev.get("room_present", False))
        prev_room = str(prev.get("room", "")).strip()
        if prev_room_present and prev_room:
            set_chat_room(manager_state, chat_id, prev_room)
        else:
            set_chat_room(manager_state, chat_id, default_offdesk_room)

        cleared_pending = clear_pending_mode(manager_state, chat_id)
        cleared_confirm = clear_confirm_action(manager_state, chat_id)

        auto_state = load_auto_state(auto_path)
        auto_state["enabled"] = False
        auto_state["chat_id"] = str(auto_state.get("chat_id", "")).strip() or str(chat_id)
        auto_state["stopped_at"] = now_iso()
        if not args.dry_run:
            save_auto_state(auto_path, auto_state)

        if args.dry_run:
            ok, out = True, "dry-run: skipped tmux auto off"
        else:
            ok, out = tmux_auto_command(args, "off")

        off_state["enabled"] = False
        off_state["chat_id"] = str(chat_id)
        off_state["stopped_at"] = now_iso()
        if not args.dry_run:
            save_offdesk_state(off_path, off_state)
            save_manager_state(args.manager_state_file, manager_state)

        send(
            "offdesk disabled\n"
            f"- restored_routing_mode: {(get_default_mode(manager_state, chat_id) or 'off')}\n"
            f"- restored_report_level: {get_chat_report_level(manager_state, chat_id, fallback_level)}\n"
            f"- restored_room: {get_chat_room(manager_state, chat_id, default_offdesk_room) or default_offdesk_room}\n"
            f"- pending_cleared: {'yes' if cleared_pending else 'no'}\n"
            f"- confirm_cleared: {'yes' if cleared_confirm else 'no'}\n"
            f"- auto: {'stopped' if ok else 'stop_failed'}\n"
            f"- detail: {out or '-'}\n"
            "next:\n"
            "- /offdesk status\n"
            "- /auto status",
            context="offdesk-off",
            with_menu=True,
        )
        return True

    existing_prev = off_state.get("prev") if isinstance(off_state, dict) else None
    if off_enabled and isinstance(existing_prev, dict):
        prev = dict(existing_prev)
    else:
        sessions = manager_state.get("chat_sessions") if isinstance(manager_state, dict) else {}
        row = sessions.get(str(chat_id)) if isinstance(sessions, dict) else None
        row = row if isinstance(row, dict) else {}
        prev = {
            "default_mode_present": ("default_mode" in row),
            "default_mode": str(row.get("default_mode", "")).strip().lower(),
            "report_level_present": ("report_level" in row),
            "report_level": str(row.get("report_level", "")).strip().lower(),
            "room_present": ("room" in row),
            "room": str(row.get("room", "")).strip(),
        }

    off_state = {
        "enabled": True,
        "chat_id": str(chat_id),
        "started_at": str(off_state.get("started_at", "")).strip() or now_iso(),
        "prev": prev,
    }
    if not args.dry_run:
        save_offdesk_state(off_path, off_state)

    set_chat_report_level(manager_state, chat_id, default_offdesk_report_level)
    set_chat_room(manager_state, chat_id, default_offdesk_room)
    existed_default = clear_default_mode(manager_state, chat_id)
    cleared_pending = clear_pending_mode(manager_state, chat_id)
    cleared_confirm = clear_confirm_action(manager_state, chat_id)

    focus_row = project_lock_row(manager_state)
    offdesk_command = "next" if focus_row else default_offdesk_command
    auto_state = load_auto_state(auto_path)
    auto_state["enabled"] = True
    auto_state["chat_id"] = str(chat_id)
    if "started_at" not in auto_state:
        auto_state["started_at"] = now_iso()
    auto_state["command"] = offdesk_command
    auto_state["prefetch"] = default_offdesk_prefetch
    auto_state["prefetch_replace_sync"] = bool(replace_sync)
    if "prefetch_since" not in auto_state:
        auto_state["prefetch_since"] = default_offdesk_prefetch_since
    auto_state["force"] = False
    if "interval_sec" not in auto_state:
        auto_state["interval_sec"] = default_auto_interval_sec
    if "idle_sec" not in auto_state:
        auto_state["idle_sec"] = default_auto_idle_sec
    if not args.dry_run:
        save_auto_state(auto_path, auto_state)
        save_manager_state(args.manager_state_file, manager_state)

    if args.dry_run:
        ok, out = True, "dry-run: skipped tmux auto on"
    else:
        ok, out = tmux_auto_command(args, "on")

    scope_summary = ops_scope_summary(manager_state)
    included_scope = ", ".join(scope_summary.get("included", [])[:6]) or "-"
    excluded_scope = ", ".join(scope_summary.get("excluded", [])[:6]) or "-"
    body = (
        "offdesk enabled\n"
        f"- ops_scope: {included_scope}\n"
        f"- ops_excluded: {excluded_scope}\n"
        "- routing_mode: off\n"
        f"- report_level: {default_offdesk_report_level}\n"
        f"- room: {default_offdesk_room}\n"
        f"- auto: {'started' if ok else 'start_failed'}\n"
        f"- command: {offdesk_command}\n"
        f"- prefetch: {prefetch_display(default_offdesk_prefetch, default_offdesk_prefetch_since, bool(replace_sync))}\n"
        f"- changed_default_mode: {'yes' if existed_default else 'no'}\n"
        f"- pending_cleared: {'yes' if cleared_pending else 'no'}\n"
        f"- confirm_cleared: {'yes' if cleared_confirm else 'no'}\n"
        f"- detail: {out or '-'}\n"
    )
    if focus_row:
        body += f"- project_lock: {project_lock_label(manager_state)}\n"
        body += "- note: project lock active, offdesk was narrowed to single-project /next mode\n"
    snapshot_lines = focused_project_snapshot_lines(manager_state)
    if snapshot_lines:
        body += "\n" + "\n".join(snapshot_lines) + "\n"
    compact_lines = ops_scope_compact_lines(manager_state, 4, "short")
    if compact_lines:
        body += "\nops projects:\n" + "\n".join(compact_lines) + "\n"
    body += "next:\n- /offdesk status\n- /queue\n- /room tail 30\n- /auto status"
    send(body, context="offdesk-on", with_menu=True)
    return True


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


def handle_scheduler_control_command(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    chat_role: str,
    rest: str,
    send: Callable[..., bool],
    get_default_mode: Callable[[Dict[str, Any], str], str],
    get_pending_mode: Callable[[Dict[str, Any], str], str],
    get_chat_report_level: Callable[[Dict[str, Any], str, str], str],
    get_chat_room: Callable[[Dict[str, Any], str, str], str],
    set_default_mode: Callable[[Dict[str, Any], str, str], None],
    set_chat_report_level: Callable[[Dict[str, Any], str, str], None],
    set_chat_room: Callable[[Dict[str, Any], str, str], None],
    clear_default_mode: Callable[[Dict[str, Any], str], bool],
    clear_pending_mode: Callable[[Dict[str, Any], str], bool],
    clear_confirm_action: Callable[[Dict[str, Any], str], bool],
    clear_chat_report_level: Callable[[Dict[str, Any], str], bool],
    save_manager_state: Callable[..., None],
    resolve_project_entry: Callable[[Dict[str, Any], str], tuple[str, Dict[str, Any]]],
    project_lock_row: Callable[[Dict[str, Any]], Dict[str, Any]],
    project_lock_label: Callable[[Dict[str, Any]], str],
    parse_replace_sync_flag: Callable[[List[str]], bool | None],
    normalize_prefetch_token: Callable[[Any], str],
    prefetch_display: Callable[[Any, Any, bool], str],
    compact_reason: Callable[[Any, int], str],
    status_report_level: Callable[[List[str], str], str],
    focused_project_snapshot_lines: Callable[[Dict[str, Any]], List[str]],
    ops_scope_summary: Callable[[Dict[str, Any]], Dict[str, List[str]]],
    ops_scope_compact_lines: Callable[[Dict[str, Any], int, str], List[str]],
    offdesk_prepare_targets: Callable[[Dict[str, Any], str], List[tuple[str, Dict[str, Any]]]],
    offdesk_prepare_project_report: Callable[[Dict[str, Any], str, Dict[str, Any]], Dict[str, Any]],
    sort_offdesk_reports: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    offdesk_review_reply_markup: Callable[[List[Dict[str, Any]], bool], Dict[str, Any]],
    offdesk_prepare_reply_markup: Callable[[List[Dict[str, Any]], int, bool], Dict[str, Any]],
    auto_state_path: Callable[[Any], Any],
    offdesk_state_path: Callable[[Any], Any],
    provider_capacity_state_path: Callable[[Any], Any],
    load_auto_state: Callable[[Any], Dict[str, Any]],
    save_auto_state: Callable[[Any, Dict[str, Any]], None],
    load_offdesk_state: Callable[[Any], Dict[str, Any]],
    save_offdesk_state: Callable[[Any, Dict[str, Any]], None],
    load_provider_capacity_state: Callable[[Any], Dict[str, Any]],
    save_provider_capacity_state: Callable[[Any, Dict[str, Any]], None],
    scheduler_session_name: Callable[[], str],
    tmux_has_session: Callable[[str], bool],
    tmux_auto_command: Callable[[Any, str], tuple[bool, str]],
    now_iso: Callable[[], str],
    default_auto_interval_sec: int,
    default_auto_idle_sec: int,
    default_auto_max_failures: int,
    default_offdesk_command: str,
    default_offdesk_prefetch: str,
    default_offdesk_prefetch_since: str,
    default_offdesk_report_level: str,
    default_offdesk_room: str,
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    if cmd == "focus":
        return _handle_focus_command(
            args=args,
            manager_state=manager_state,
            chat_id=chat_id,
            chat_role=chat_role,
            rest=rest,
            send=send,
            save_manager_state=save_manager_state,
            resolve_project_entry=resolve_project_entry,
            project_lock_row=project_lock_row,
            project_lock_label=project_lock_label,
            now_iso=now_iso,
        )
    if cmd == "panic":
        return _handle_panic_command(
            args=args,
            manager_state=manager_state,
            chat_id=chat_id,
            chat_role=chat_role,
            rest=rest,
            send=send,
            get_default_mode=get_default_mode,
            get_pending_mode=get_pending_mode,
            clear_default_mode=clear_default_mode,
            clear_pending_mode=clear_pending_mode,
            clear_confirm_action=clear_confirm_action,
            save_manager_state=save_manager_state,
            auto_state_path=auto_state_path,
            offdesk_state_path=offdesk_state_path,
            load_auto_state=load_auto_state,
            save_auto_state=save_auto_state,
            load_offdesk_state=load_offdesk_state,
            save_offdesk_state=save_offdesk_state,
            scheduler_session_name=scheduler_session_name,
            tmux_has_session=tmux_has_session,
            tmux_auto_command=tmux_auto_command,
            now_iso=now_iso,
        )
    if cmd == "offdesk":
        return _handle_offdesk_command(
            args=args,
            manager_state=manager_state,
            chat_id=chat_id,
            chat_role=chat_role,
            rest=rest,
            send=send,
            get_default_mode=get_default_mode,
            get_pending_mode=get_pending_mode,
            get_chat_report_level=get_chat_report_level,
            get_chat_room=get_chat_room,
            set_default_mode=set_default_mode,
            set_chat_report_level=set_chat_report_level,
            set_chat_room=set_chat_room,
            clear_default_mode=clear_default_mode,
            clear_pending_mode=clear_pending_mode,
            clear_confirm_action=clear_confirm_action,
            clear_chat_report_level=clear_chat_report_level,
            save_manager_state=save_manager_state,
            parse_replace_sync_flag=parse_replace_sync_flag,
            status_report_level=status_report_level,
            prefetch_display=prefetch_display,
            focused_project_snapshot_lines=focused_project_snapshot_lines,
            ops_scope_summary=ops_scope_summary,
            ops_scope_compact_lines=ops_scope_compact_lines,
            project_lock_row=project_lock_row,
            project_lock_label=project_lock_label,
            offdesk_prepare_targets=offdesk_prepare_targets,
            offdesk_prepare_project_report=offdesk_prepare_project_report,
            sort_offdesk_reports=sort_offdesk_reports,
            offdesk_review_reply_markup=offdesk_review_reply_markup,
            offdesk_prepare_reply_markup=offdesk_prepare_reply_markup,
            auto_state_path=auto_state_path,
            offdesk_state_path=offdesk_state_path,
            provider_capacity_state_path=provider_capacity_state_path,
            load_auto_state=load_auto_state,
            save_auto_state=save_auto_state,
            load_offdesk_state=load_offdesk_state,
            save_offdesk_state=save_offdesk_state,
            load_provider_capacity_state=load_provider_capacity_state,
            save_provider_capacity_state=save_provider_capacity_state,
            tmux_auto_command=tmux_auto_command,
            now_iso=now_iso,
            default_offdesk_command=default_offdesk_command,
            default_offdesk_prefetch=default_offdesk_prefetch,
            default_offdesk_prefetch_since=default_offdesk_prefetch_since,
            default_offdesk_report_level=default_offdesk_report_level,
            default_offdesk_room=default_offdesk_room,
            default_auto_interval_sec=default_auto_interval_sec,
            default_auto_idle_sec=default_auto_idle_sec,
        )
    if cmd == "auto":
        return _handle_auto_command(
            args=args,
            manager_state=manager_state,
            chat_id=chat_id,
            chat_role=chat_role,
            rest=rest,
            send=send,
            get_chat_report_level=get_chat_report_level,
            status_report_level=status_report_level,
            parse_replace_sync_flag=parse_replace_sync_flag,
            normalize_prefetch_token=normalize_prefetch_token,
            prefetch_display=prefetch_display,
            compact_reason=compact_reason,
            focused_project_snapshot_lines=focused_project_snapshot_lines,
            ops_scope_compact_lines=ops_scope_compact_lines,
            project_lock_row=project_lock_row,
            project_lock_label=project_lock_label,
            auto_state_path=auto_state_path,
            load_auto_state=load_auto_state,
            save_auto_state=save_auto_state,
            provider_capacity_state_path=provider_capacity_state_path,
            load_provider_capacity_state=load_provider_capacity_state,
            save_provider_capacity_state=save_provider_capacity_state,
            scheduler_session_name=scheduler_session_name,
            tmux_has_session=tmux_has_session,
            tmux_auto_command=tmux_auto_command,
            now_iso=now_iso,
            default_auto_interval_sec=default_auto_interval_sec,
            default_auto_idle_sec=default_auto_idle_sec,
            default_auto_max_failures=default_auto_max_failures,
            record_outcome=record_outcome,
        )
    return False
