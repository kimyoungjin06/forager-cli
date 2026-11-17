"""Listener health and action-readiness reporting for the Telegram operator.

These functions build read-only health projections. They do not touch the run
loop's result plumbing (which stays with the poller in the main script); they
only read loop-status telemetry and the local agent runtime status.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from typing import Any

from .agent import agent_runtime_status as resolve_agent_runtime_status
from .common import load_json, unique_nonempty, utc_now
from .persistence import RemoteOperatorStateError, load_state, parse_utc_timestamp
from .config import public_control_generation_id
from .project_candidates import workspace_roots
from .projects import find_project_dir, load_registry
from .rendering import sanitize_text
from .reply_outbox import outbox_health_issue, outbox_inspection
from .update_journal import journal_health_issue
from .transport_health import classify_transport_issues, failure_age, TRANSPORT_FAILURE_COUNT, TRANSPORT_FAILURE_SECONDS
from .delivery_attention import hold_summary

HEALTH_SCHEMA = "remote_operator_telegram_health.v1"
ACTION_READINESS_SCHEMA = "telegram_action_readiness.v1"


def action_readiness(
    action: str,
    status: str,
    *,
    reason: str,
    allowed_actions: list[str] | None = None,
    blocked_actions: list[str] | None = None,
    recovery_hint: str | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema": ACTION_READINESS_SCHEMA,
        "action": action,
        "status": status,
        "reason": sanitize_text(reason, max_chars=160),
        "allowed_actions": unique_nonempty(list(allowed_actions or [])),
        "blocked_actions": unique_nonempty(list(blocked_actions or [])),
        "recovery_hint": sanitize_text(recovery_hint or "", max_chars=160) or None,
        "evidence": unique_nonempty(list(evidence or [])),
    }


def agent_runtime_issue(agent_runtime_status: dict[str, Any]) -> str | None:
    status = str(agent_runtime_status.get("status") or "").strip().lower()
    if status in {"available", "disabled"}:
        return None
    if status == "unavailable":
        return "agent_runtime_unavailable"
    if status == "error":
        return "agent_runtime_error"
    return "agent_runtime_unknown"


def readiness_from_agent_intent(agent_intent: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(agent_intent, dict):
        return None
    reason = str(agent_intent.get("reason") or "").strip()
    status = str(agent_intent.get("status") or "").strip()
    if status == "fallback" and reason.startswith(("local_agent_unavailable", "local_agent_failed")):
        return action_readiness(
            "build_plan",
            "blocked",
            reason="local_agent_unavailable",
            allowed_actions=["status", "project_scan", "existing_plans"],
            blocked_actions=["new_plan", "start_offdesk"],
            recovery_hint="로컬 모델 연결을 복구한 뒤 다시 시작",
            evidence=[reason],
        )
    return action_readiness(
        "build_plan",
        "healthy",
        reason="agent_intent_available",
        allowed_actions=["project_scan", "plan_draft"],
        blocked_actions=["arbitrary_launch", "shell"],
        recovery_hint="실행은 reviewed bound task만 가능",
    )


def health_action_readiness(
    *,
    transport_issues: list[str],
    agent_runtime_status: dict[str, Any],
    project_scan_issue: str | None = None,
    tmux_available: bool = True,
) -> list[dict[str, Any]]:
    transport_blocked = bool(transport_issues)
    agent_issue = agent_runtime_issue(agent_runtime_status)
    status_readiness = action_readiness(
        "status",
        "blocked" if transport_blocked else "healthy",
        reason=transport_issues[0] if transport_issues else "listener_status_available",
        allowed_actions=[] if transport_blocked else ["status", "pending", "plans"],
        blocked_actions=["remote_commands"] if transport_blocked else [],
        recovery_hint="텔레그램 설정과 listener 상태 확인" if transport_blocked else None,
        evidence=transport_issues,
    )
    project_scan_readiness = action_readiness(
        "project_scan",
        "blocked" if transport_blocked or project_scan_issue else "healthy",
        reason=(
            transport_issues[0]
            if transport_issues
            else project_scan_issue or "workspace_scan_available"
        ),
        allowed_actions=[]
        if transport_blocked or project_scan_issue
        else ["project_scan", "manual_path_check"],
        blocked_actions=["project_selection"]
        if transport_blocked or project_scan_issue
        else [],
        recovery_hint=(
            "텔레그램 수신 복구 후 다시 시도"
            if transport_blocked
            else "설치 서비스의 --workspace-root와 프로젝트 레지스트리를 확인"
            if project_scan_issue
            else None
        ),
        evidence=transport_issues + ([project_scan_issue] if project_scan_issue else []),
    )
    if transport_blocked:
        build_plan = action_readiness(
            "build_plan",
            "blocked",
            reason=transport_issues[0],
            allowed_actions=[],
            blocked_actions=["new_plan", "start_offdesk"],
            recovery_hint="텔레그램 수신 복구 필요",
            evidence=transport_issues,
        )
    elif agent_issue:
        build_plan = action_readiness(
            "build_plan",
            "blocked",
            reason=agent_issue,
            allowed_actions=["status", "project_scan", "existing_plans"],
            blocked_actions=["new_plan", "start_offdesk"],
            recovery_hint="로컬 모델 연결을 복구한 뒤 다시 시작",
            evidence=[agent_issue],
        )
    else:
        build_plan = action_readiness(
            "build_plan",
            "healthy",
            reason="agent_runtime_available"
            if str(agent_runtime_status.get("status") or "") == "available"
            else "agent_runtime_disabled",
            allowed_actions=["project_scan", "plan_draft"],
            blocked_actions=["arbitrary_launch", "shell"],
            recovery_hint="실행은 reviewed bound task만 가능",
        )
    start_offdesk = action_readiness(
        "start_offdesk",
        "guarded",
        reason="reviewed_bound_task_only",
        allowed_actions=["bound_enqueue_run", "task_scoped_start", "task_scoped_monitor"],
        blocked_actions=["arbitrary_launch", "shell", "accepted_truth"],
        recovery_hint="계획 승인, 게이트, 브리프, 워크로드 binding 후 대상 task만 시작",
    )
    agent_status = str(agent_runtime_status.get("status") or "").strip().lower()
    if transport_blocked:
        session_message = action_readiness(
            "session_message",
            "blocked",
            reason=transport_issues[0],
            blocked_actions=["plain_text_delivery"],
            recovery_hint="텔레그램 수신 복구 필요",
            evidence=transport_issues,
        )
    elif agent_status != "available":
        session_message = action_readiness(
            "session_message",
            "blocked",
            reason=f"agent_runtime_{agent_status or 'unknown'}",
            allowed_actions=["explicit_session_status"],
            blocked_actions=["plain_text_delivery"],
            recovery_hint="로컬 평문 해석 모델을 활성화하고 연결 상태를 확인",
        )
    elif not tmux_available:
        session_message = action_readiness(
            "session_message",
            "blocked",
            reason="tmux_unavailable",
            blocked_actions=["plain_text_delivery", "waiting_card_reply"],
            recovery_hint="tmux 설치와 PATH를 확인",
            evidence=["tmux_unavailable"],
        )
    else:
        session_message = action_readiness(
            "session_message",
            "healthy",
            reason="agent_runtime_and_tmux_available",
            allowed_actions=["plain_text_delivery", "waiting_card_reply"],
            blocked_actions=["ambiguous_session_target"],
            recovery_hint="대상이 여러 개면 프로젝트와 에이전트 이름을 함께 지정",
        )
    return [
        status_readiness,
        project_scan_readiness,
        build_plan,
        start_offdesk,
        session_message,
    ]


def listener_health(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    status_path = args.loop_status_file
    issues: list[str] = []
    transport_issues: list[str] = []
    token_configured = bool(config.get("token"))
    if not token_configured:
        transport_issues.append("telegram_bot_token_missing")
    if not config.get("chat_allowlist_configured"):
        transport_issues.append("telegram_chat_allowlist_missing")
    if not config.get("user_allowlist_configured"):
        transport_issues.append("telegram_user_allowlist_missing")
    transport_issues.extend(
        str(issue) for issue in config.get("operator_config_issues") or []
    )
    listener_state: dict[str, Any] = {}
    listener_state_valid = True
    try:
        listener_state = load_state(args.state_file)
    except RemoteOperatorStateError:
        listener_state_valid = False
        transport_issues.append("listener_state_unreadable")
    configured_generation = str(
        config.get("_control_generation_sha256") or ""
    ).strip()
    bound_generation = str(
        listener_state.get("control_generation_sha256") or ""
    ).strip()
    if bound_generation and bound_generation != configured_generation:
        transport_issues.append("control_generation_mismatch")
    try:
        journal_issue = journal_health_issue(args.update_journal_file)
    except RemoteOperatorStateError:
        journal_issue = "update_journal_unreadable"
    if journal_issue:
        transport_issues.append(journal_issue)
    try:
        reply_outbox = outbox_inspection(args.reply_outbox_file)
        outbox_issue = outbox_health_issue(args.reply_outbox_file)
    except RemoteOperatorStateError:
        reply_outbox = {
            "schema": "remote_operator_telegram_reply_outbox_inspection.v1",
            "path": str(args.reply_outbox_file),
            "status": "unreadable",
        }
        outbox_issue = "reply_outbox_unreadable"
    delivery_warnings = []
    if outbox_issue == "reply_delivery_unconfirmed":
        delivery_warnings.append(outbox_issue)
    elif outbox_issue:
        transport_issues.append(outbox_issue)
    # An uncertain delivery must not mask a separate response still awaiting delivery.
    if reply_outbox.get("pending_count") and "reply_delivery_pending" not in transport_issues:
        transport_issues.append("reply_delivery_pending")
    proactive_holds = hold_summary(listener_state if listener_state_valid else None)
    if proactive_holds["status"] == "unreadable":
        transport_issues.append("notification_hold_ledger_unreadable")
    elif proactive_holds["uncertain_count"]:
        delivery_warnings.append("proactive_delivery_unconfirmed")
    elif proactive_holds["confirmed_commit_pending_count"]:
        delivery_warnings.append("proactive_delivery_commit_pending")
    loop_status: dict[str, Any] = {}
    if status_path.exists():
        try:
            loaded = load_json(status_path)
            loop_status = loaded if isinstance(loaded, dict) else {}
            if not loop_status:
                transport_issues.append("loop_status_unreadable")
        except (OSError, json.JSONDecodeError):
            transport_issues.append("loop_status_unreadable")
    else:
        transport_issues.append("loop_status_missing")
    last_result = loop_status.get("last_result") if isinstance(loop_status.get("last_result"), dict) else {}
    last_poll_at = parse_utc_timestamp(last_result.get("generated_at") or loop_status.get("generated_at"))
    last_poll_age_sec = None
    if last_poll_at:
        last_poll_age_sec = max(
            0,
            int((dt.datetime.now(dt.timezone.utc) - last_poll_at).total_seconds()),
        )
        if last_poll_age_sec > max(1, int(args.health_max_age_sec)):
            transport_issues.append("last_poll_stale")
    elif loop_status:
        transport_issues.append("last_poll_missing")
    if str(loop_status.get("status") or "") not in {"polling", "max_polls_reached"} and loop_status:
        transport_issues.append("listener_not_polling")
    if str(last_result.get("status") or "") == "poll_error":
        transport_issues.append("last_poll_transport_error")
    if str(last_result.get("status") or "") == "send_failed":
        transport_issues.append("last_send_transport_error")
    if str(last_result.get("status") or "") == "loop_error":
        transport_issues.append("last_loop_internal_error")
    if str(last_result.get("status") or "") == "state_error":
        transport_issues.append("last_loop_state_error")
    transport_issues, transport_warnings = classify_transport_issues(
        transport_issues, count=loop_status.get("consecutive_error_count"),
        age=failure_age(loop_status, dt.datetime.now(dt.timezone.utc)),
    )
    agent_runtime_status = resolve_agent_runtime_status(args)
    tmux_available = shutil.which("tmux") is not None
    roots = workspace_roots(args)
    registry = load_registry()
    resolvable_projects = sum(
        1 for entry in registry.values() if find_project_dir(entry, roots) is not None
    )
    project_scan_issue = None
    if registry and resolvable_projects == 0:
        project_scan_issue = "registered_projects_unresolvable"
    elif registry and resolvable_projects < len(registry):
        project_scan_issue = "registered_projects_partially_unresolvable"
    issues.extend(transport_issues)
    issues.extend(delivery_warnings)
    agent_issue = agent_runtime_issue(agent_runtime_status)
    if agent_issue:
        issues.append(agent_issue)
    if project_scan_issue:
        issues.append(project_scan_issue)
    if transport_issues:
        health_status = "unhealthy"
    elif agent_issue or project_scan_issue or transport_warnings or delivery_warnings:
        health_status = "degraded"
    else:
        health_status = "healthy"
    readiness = health_action_readiness(
        transport_issues=transport_issues,
        agent_runtime_status=agent_runtime_status,
        project_scan_issue=project_scan_issue,
        tmux_available=tmux_available,
    )
    delivery_issues = [issue for issue in transport_issues if issue in {"reply_delivery_pending", "last_send_transport_error"}]
    listener_issues = [issue for issue in transport_issues if issue not in delivery_issues]
    return {
        "schema": HEALTH_SCHEMA,
        "generated_at": utc_now(),
        "profile": args.profile,
        "health_status": health_status,
        "issues": issues,
        "transport_issues": transport_issues,
        "transport_warnings": transport_warnings,
        "attention_domains": {
            "listener": {
                "status": "unhealthy" if listener_issues else "degraded" if transport_warnings else "healthy",
                "issues": listener_issues, "warnings": transport_warnings,
            },
            "delivery": {
                "status": "unknown" if reply_outbox.get("status") == "unreadable" or proactive_holds["status"] == "unreadable" else "blocked" if reply_outbox.get("pending_count") or delivery_issues else "attention" if delivery_warnings else "clear",
                "issues": delivery_issues, "warnings": delivery_warnings, "proactive_holds": proactive_holds,
                "reply_unconfirmed_count": reply_outbox.get("unconfirmed_count"),
            },
            "project_review": {"status": "not_inspected", "source": "project-scoped review surfaces"},
        },
        "transport_failure_policy": {"minimum_consecutive_errors": TRANSPORT_FAILURE_COUNT,
            "minimum_seconds_without_success": TRANSPORT_FAILURE_SECONDS},
        "env_file": str(args.env_file),
        "status_file": str(status_path),
        "state_file": str(args.state_file),
        "update_journal_file": str(args.update_journal_file),
        "reply_outbox_file": str(args.reply_outbox_file),
        "reply_outbox": reply_outbox,
        "token_configured": token_configured,
        "control_generation_id": public_control_generation_id(
            configured_generation
        ),
        "bound_control_generation_id": public_control_generation_id(
            bound_generation
        ),
        "operator_mode": config.get("operator_mode"),
        "operator_identity_configured": bool(
            config.get("operator_identity_configured")
        ),
        "chat_allowlist_configured": bool(config.get("chat_allowlist_configured")),
        "user_allowlist_configured": bool(config.get("user_allowlist_configured")),
        "listener_status": loop_status.get("status"),
        "poll_count": loop_status.get("poll_count"),
        "updates_seen": loop_status.get("updates_seen"),
        "handled_result_count": loop_status.get("handled_result_count"),
        "status_counts": loop_status.get("status_counts", {}),
        "consecutive_error_count": loop_status.get("consecutive_error_count"),
        "last_error_at": loop_status.get("last_error_at"),
        "last_success_at": loop_status.get("last_success_at"),
        "last_poll_age_sec": last_poll_age_sec,
        "last_result_status": last_result.get("status"),
        "last_handled_status": (
            loop_status.get("last_handled_result", {}).get("status")
            if isinstance(loop_status.get("last_handled_result"), dict)
            else None
        ),
        "agent_runtime_status": agent_runtime_status,
        "tmux_available": tmux_available,
        "workspace_root_count": len(roots),
        "registered_project_count": len(registry),
        "resolvable_project_count": resolvable_projects,
        "action_readiness": readiness,
        "runtime_dispatch_enabled": bool(args.enable_runtime_dispatch),
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
    }
