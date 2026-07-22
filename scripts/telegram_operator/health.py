"""Listener health and action-readiness reporting for the Telegram operator.

These functions build read-only health projections. They do not touch the run
loop's result plumbing (which stays with the poller in the main script); they
only read loop-status telemetry and the local agent runtime status.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

from .agent import agent_runtime_status as resolve_agent_runtime_status
from .common import load_json, unique_nonempty, utc_now
from .persistence import parse_utc_timestamp
from .rendering import sanitize_text

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
        "blocked" if transport_blocked else "healthy",
        reason=transport_issues[0] if transport_issues else "workspace_scan_available",
        allowed_actions=[] if transport_blocked else ["project_scan", "manual_path_check"],
        blocked_actions=["project_selection"] if transport_blocked else [],
        recovery_hint="텔레그램 수신 복구 후 다시 시도" if transport_blocked else None,
        evidence=transport_issues,
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
    return [status_readiness, project_scan_readiness, build_plan, start_offdesk]


def listener_health(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    status_path = args.loop_status_file
    issues: list[str] = []
    transport_issues: list[str] = []
    token_configured = bool(config.get("token"))
    if not token_configured:
        transport_issues.append("telegram_bot_token_missing")
    if not config.get("chat_allowlist_configured"):
        transport_issues.append("telegram_chat_allowlist_missing")
    loop_status: dict[str, Any] = {}
    if status_path.exists():
        try:
            loaded = load_json(status_path)
            loop_status = loaded if isinstance(loaded, dict) else {}
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
    agent_runtime_status = resolve_agent_runtime_status(args)
    issues.extend(transport_issues)
    agent_issue = agent_runtime_issue(agent_runtime_status)
    if agent_issue:
        issues.append(agent_issue)
    if transport_issues:
        health_status = "unhealthy"
    elif agent_issue:
        health_status = "degraded"
    else:
        health_status = "healthy"
    readiness = health_action_readiness(
        transport_issues=transport_issues,
        agent_runtime_status=agent_runtime_status,
    )
    return {
        "schema": HEALTH_SCHEMA,
        "generated_at": utc_now(),
        "profile": args.profile,
        "health_status": health_status,
        "issues": issues,
        "transport_issues": transport_issues,
        "env_file": str(args.env_file),
        "status_file": str(status_path),
        "state_file": str(args.state_file),
        "token_configured": token_configured,
        "chat_allowlist_configured": bool(config.get("chat_allowlist_configured")),
        "user_allowlist_configured": bool(config.get("user_allowlist_configured")),
        "listener_status": loop_status.get("status"),
        "poll_count": loop_status.get("poll_count"),
        "updates_seen": loop_status.get("updates_seen"),
        "handled_result_count": loop_status.get("handled_result_count"),
        "last_poll_age_sec": last_poll_age_sec,
        "last_result_status": last_result.get("status"),
        "last_handled_status": (
            loop_status.get("last_handled_result", {}).get("status")
            if isinstance(loop_status.get("last_handled_result"), dict)
            else None
        ),
        "agent_runtime_status": agent_runtime_status,
        "action_readiness": readiness,
        "runtime_dispatch_enabled": bool(args.enable_runtime_dispatch),
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
    }
