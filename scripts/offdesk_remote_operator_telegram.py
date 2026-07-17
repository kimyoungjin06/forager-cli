#!/usr/bin/env python3
"""Telegram adapter for guarded Forager Remote Operator projections.

This adapter is intentionally narrow. It maps a small Telegram command surface
to read-only projections, remote Plan Mode receipts, exact gate resolution,
reviewed enqueue, and task-scoped runtime start/monitor receipts. It never
executes arbitrary shell text, starts unbound work, closes out completed work,
accepts runtime output as truth, or mutates project files directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
from typing import Any

from telegram_operator.agent import (
    DEFAULT_AGENT_CONFIG_FILE,
    chat_with_agent,
    classify_feedback_kind,
    classify_feedback_with_agent,
)
from telegram_operator.common import (
    RemoteOperatorTelegramError,
    append_jsonl,
    load_json,
    sha256_short,
    utc_now,
    write_json,
)
from telegram_operator.config import resolve_telegram_config
from telegram_operator.health import listener_health
from telegram_operator.redaction import public_remote_plan_session
from telegram_operator.base import attach_choice_surface, result_base
from telegram_operator.plan_workflow import (
    active_remote_plan_session,
    handle_remote_plan_session_input,
    remote_plan_selection_context,
    remote_plan_session_should_handle_text,
    store_remote_plan_session,
)
from telegram_operator.receipts import create_remote_plan_session
from telegram_operator.schemas import (
    FORBIDDEN_REMOTE_INTENTS,
    INTERACTION_CONTEXT_SCHEMA,
    RESULT_SCHEMA,
)
from telegram_operator.plan_messages import render_project_selection_message
from telegram_operator.dispatch import (
    apply_decision_action,
    apply_recovery_action,
    apply_runtime_dispatch,
    available_action_kinds,
    available_recovery_action_kinds,
    build_confirmation,
    clear_confirmation,
    confirmation_is_fresh,
    export_workstation_surface,
    find_action_envelope,
    find_recovery_envelope,
    open_decision_actions,
    open_recovery_actions,
    open_runtime_dispatch,
    pop_confirmation,
    runtime_dispatch_item,
    store_confirmation,
)
from telegram_operator.persistence import (
    append_chat_history,
    chat_history_for_chat_hash,
    last_context_for_chat_hash,
    load_state,
    remember_context_for_chat_hash,
    save_state,
)
from telegram_operator.rendering import (
    agent_assistant_reply,
    render_decisions_message,
    render_dispatch_cancel_message,
    render_dispatch_confirm_message,
    render_dispatch_error_message,
    render_dispatch_result_message,
    render_recovery_confirm_message,
    render_recovery_message,
    render_recovery_result_message,
    render_runtime_confirm_message,
    render_runtime_disabled_message,
    render_runtime_message,
    render_runtime_result_message,
    display_action,
    display_review_status,
    help_message,
    mobile_card_contract,
    number,
    profile_label_from_projection,
    projection_payload,
    render_chat_message,
    render_feedback_message,
    render_wiki_candidate_message,
    render_projection_message,
    sanitize_text,
    status_summary,
)
from telegram_operator.routing import (
    parse_remote_command,
    remote_plan_session_command_payload,
)
from telegram_operator.transport import get_updates, send_message
from telegram_operator.wiki import record_remember_candidate


DEFAULT_TELEGRAM_ENV_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_TELEGRAM_ENV",
        "/home/kimyoungjin06/Desktop/Workspace/aoe_orch_control/.aoe-team/telegram.env",
    )
)
DEFAULT_STATE_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_STATE",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_state.json"),
    )
)
DEFAULT_FEEDBACK_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_FEEDBACK",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_feedback.jsonl"),
    )
)
DEFAULT_FEEDBACK_INGEST_DIR = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_FEEDBACK_INGEST_DIR",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_feedback_ingest"),
    )
)
DEFAULT_REMOTE_PLAN_ARTIFACT_DIR = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_PLAN_ARTIFACT_DIR",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_plan_sessions"),
    )
)
DEFAULT_LOOP_STATUS_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_LOOP_STATUS",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_loop.json"),
    )
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    parser.add_argument("--forager-bin", default=os.environ.get("FORAGER_BIN", "forager"))
    parser.add_argument("--env-file", type=pathlib.Path, default=DEFAULT_TELEGRAM_ENV_FILE)
    parser.add_argument("--state-file", type=pathlib.Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--feedback-file", type=pathlib.Path, default=DEFAULT_FEEDBACK_FILE)
    parser.add_argument("--feedback-ingest-dir", type=pathlib.Path, default=DEFAULT_FEEDBACK_INGEST_DIR)
    parser.add_argument("--remote-plan-artifact-dir", type=pathlib.Path, default=DEFAULT_REMOTE_PLAN_ARTIFACT_DIR)
    parser.add_argument("--loop-status-file", type=pathlib.Path, default=DEFAULT_LOOP_STATUS_FILE)
    parser.add_argument(
        "--no-decision-feedback-ingest",
        dest="decision_feedback_ingest",
        action="store_false",
        default=True,
        help="Record freeform Telegram feedback JSONL only; do not promote it to offdesk decisions.",
    )
    parser.add_argument("--out", type=pathlib.Path, help="Optional JSON result path.")
    parser.add_argument("--command-text", help="Deterministic command text, for tests or manual dry-runs.")
    parser.add_argument("--send-command-text", help="Render a read-only command and send it to the configured target chat.")
    parser.add_argument("--replay-update-file", type=pathlib.Path, help="Dry-run only: process local Telegram update JSON through the poller.")
    parser.add_argument("--projection-file", type=pathlib.Path, help="Dry-run only: render this read-only projection instead of invoking forager.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the Telegram API.")
    parser.add_argument("--once", action="store_true", help="Poll Telegram once and answer at most one update.")
    parser.add_argument("--health", action="store_true", help="Report local Telegram listener health and exit.")
    parser.add_argument("--health-max-age-sec", type=int, default=120)
    parser.add_argument(
        "--context-max-age-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_CONTEXT_MAX_AGE_SEC", "86400")),
        help="Maximum age for remembered Telegram card context; negative disables expiry.",
    )
    parser.add_argument(
        "--dispatch-confirm-ttl-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_DISPATCH_CONFIRM_TTL_SEC", "300")),
        help="Lifetime of a pending Telegram execution confirmation token.",
    )
    parser.add_argument(
        "--enable-runtime-dispatch",
        action="store_true",
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_ENABLE_RUNTIME_DISPATCH", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Allow /dispatch to queue an operator-supplied runtime command. Off by default; "
        "this is remote command execution and should only be enabled on trusted setups.",
    )
    parser.add_argument(
        "--agent-intent-mode",
        choices=("auto", "off", "required"),
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_INTENT_MODE", "auto"),
        help="Classify freeform Telegram text with a local agent when available.",
    )
    parser.add_argument("--agent-config-file", type=pathlib.Path, default=DEFAULT_AGENT_CONFIG_FILE)
    parser.add_argument("--agent-provider", default=os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_PROVIDER"))
    parser.add_argument("--agent-base-url", action="append", default=[])
    parser.add_argument("--agent-model", action="append", default=[])
    parser.add_argument(
        "--agent-model-candidates",
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_MODELS", ""),
        help="Comma-separated model preference list for Telegram intent classification.",
    )
    parser.add_argument("--agent-timeout-sec", type=int, default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_TIMEOUT_SEC", "20")))
    parser.add_argument("--agent-num-ctx", type=int, default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_NUM_CTX", "8192")))
    parser.add_argument("--agent-num-predict", type=int, default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_NUM_PREDICT", "768")))
    parser.add_argument(
        "--project-init-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PROJECT_INIT_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--plan-draft-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PLAN_DRAFT_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--plan-registration-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PLAN_REGISTRATION_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--plan-review-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PLAN_REVIEW_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--plan-launch-prep-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PLAN_LAUNCH_PREP_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--gate-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_GATE_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--execution-brief-ttl-minutes",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_EXECUTION_BRIEF_TTL_MINUTES", "30")),
    )
    parser.add_argument(
        "--enqueue-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_ENQUEUE_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--runtime-start-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_RUNTIME_START_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--runtime-monitor-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_RUNTIME_MONITOR_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--closeout-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_CLOSEOUT_TIMEOUT_SEC", "60")),
    )
    parser.add_argument(
        "--workspace-root",
        action="append",
        type=pathlib.Path,
        default=[],
        help="Workspace root to scan for remote Plan Mode project candidates. Can be repeated.",
    )
    parser.add_argument(
        "--max-project-candidates",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_MAX_PROJECT_CANDIDATES", "3")),
        help="Maximum project candidates to present in Telegram planning sessions.",
    )
    parser.add_argument("--max-polls", type=int, help="Stop after this many polls; useful for smoke tests.")
    parser.add_argument("--poll-timeout-sec", type=int, default=5)
    parser.add_argument("--api-timeout-sec", type=int, default=20)
    parser.add_argument("--poll-error-backoff-sec", type=int, default=5)
    parser.add_argument("--max-message-chars", type=int, default=3500)
    return parser.parse_args()










def projection_command(forager_bin: str, profile: str, parsed: dict[str, Any]) -> list[str]:
    argv = [forager_bin]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(["offdesk", "remote-operator"])
    argv.extend(parsed["argv"])
    argv.extend(["--transport", "telegram", "--json"])
    return argv


def run_projection(forager_bin: str, profile: str, parsed: dict[str, Any]) -> dict[str, Any]:
    command = projection_command(forager_bin, profile, parsed)
    process = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        detail = sanitize_text(process.stderr.strip() or process.stdout.strip())
        raise RemoteOperatorTelegramError(
            f"forager remote operator projection failed: {detail}"
        )
    try:
        projection = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError("forager projection did not return JSON") from error
    validate_projection(projection, expected_command=parsed.get("command"))
    return projection


def decision_feedback_ingest_command(
    args: argparse.Namespace,
    feedback_path: pathlib.Path,
) -> list[str]:
    argv = [args.forager_bin]
    if args.profile:
        argv.extend(["--profile", args.profile])
    argv.extend(
        [
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            str(feedback_path),
            "--json",
        ]
    )
    return argv


def ingest_feedback_decision(
    args: argparse.Namespace,
    feedback_record: dict[str, Any],
) -> dict[str, Any]:
    if not args.decision_feedback_ingest:
        return {"decision_feedback_ingest_status": "disabled"}
    fingerprint = hashlib.sha256(
        json.dumps(feedback_record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    message_id = feedback_record.get("message_id")
    suffix = str(message_id) if message_id is not None else fingerprint
    feedback_path = args.feedback_ingest_dir / f"telegram_feedback_{suffix}_{fingerprint}.json"
    write_json(feedback_path, feedback_record)
    command = decision_feedback_ingest_command(args, feedback_path)
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(str(error), max_chars=300),
        }
    if process.returncode != 0:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(
                process.stderr.strip() or process.stdout.strip(),
                max_chars=300,
            ),
        }
    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": "decision ingest did not return JSON",
        }
    return {
        "decision_feedback_ingest_status": "recorded"
        if report.get("appended") is True
        else "existing",
        "decision_feedback_ingest_file": str(feedback_path),
        "decision_feedback_decision_id": report.get("decision_id"),
        "decision_feedback_appended": bool(report.get("appended")),
    }


def load_projection_file(path: pathlib.Path, parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        projection = load_json(path)
    except OSError as error:
        raise RemoteOperatorTelegramError(f"projection file cannot be read: {path}") from error
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError(f"projection file is not valid JSON: {path}") from error
    if not isinstance(projection, dict):
        raise RemoteOperatorTelegramError("projection file must contain one JSON object")
    validate_projection(projection, expected_command=parsed.get("command"))
    return projection


def validate_projection(projection: dict[str, Any], *, expected_command: Any = None) -> None:
    if projection.get("schema") != "remote_operator_readonly_projection.v1":
        raise RemoteOperatorTelegramError("unexpected projection schema")
    if projection.get("read_only") is not True:
        raise RemoteOperatorTelegramError("projection is not read-only")
    if projection.get("mutation_authorized") is not False:
        raise RemoteOperatorTelegramError("projection unexpectedly authorizes mutation")
    if projection.get("approval_authorized") is not False:
        raise RemoteOperatorTelegramError("projection unexpectedly authorizes approval")
    expected = str(expected_command or "").strip()
    actual = str(projection.get("command") or "").strip()
    if expected and actual != expected:
        raise RemoteOperatorTelegramError(
            f"projection command mismatch: expected {expected}, got {actual or 'missing'}"
        )


































































































































































































































































def show_command_for(plan_id: Any) -> str:
    value = str(plan_id or "").strip()
    return f"/show {shlex.quote(value)}" if value else "/plans --latest"


def interaction_context_from_projection(projection: dict[str, Any]) -> dict[str, Any]:
    command = str(projection.get("command") or "").strip()
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    context: dict[str, Any] = {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": command or "unknown",
        "profile": profile,
        "projection_generated_at": str(projection.get("generated_at") or ""),
        "context_kind": "generic",
        "focus_kind": None,
        "focus_ref": None,
        "focus_label": None,
        "next_command": None,
    }
    if command == "status":
        pending = number(payload, "pending_approvals")
        failed = number(payload, "failed_offdesk_tasks")
        closeout = number(payload, "closeout_required_offdesk_tasks")
        active = number(payload, "active_offdesk_tasks")
        queued = number(payload, "queued_offdesk_tasks")
        if pending:
            context.update(
                {
                    "context_kind": "status_attention",
                    "focus_kind": "approval_queue",
                    "focus_ref": str(pending),
                    "focus_label": f"승인 요청 {pending}개",
                    "next_command": "/pending",
                }
            )
        elif failed or closeout:
            context.update(
                {
                    "context_kind": "status_attention",
                    "focus_kind": "local_review",
                    "focus_ref": f"failed:{failed};closeout:{closeout}",
                    "focus_label": status_summary(payload),
                    "next_command": "/status",
                }
            )
        elif active or queued:
            context.update(
                {
                    "context_kind": "status_activity",
                    "focus_kind": "offdesk_activity",
                    "focus_ref": f"active:{active};queued:{queued}",
                    "focus_label": status_summary(payload),
                    "next_command": "/status",
                }
            )
        else:
            context.update(
                {
                    "context_kind": "status_clear",
                    "focus_kind": "none",
                    "focus_label": "처리할 항목 없음",
                    "next_command": "/status",
                }
            )
    elif command == "pending":
        approvals = payload.get("approvals") if isinstance(payload.get("approvals"), list) else []
        if approvals and isinstance(approvals[0], dict):
            approval = approvals[0]
            context.update(
                {
                    "context_kind": "approval_attention",
                    "focus_kind": "approval",
                    "focus_ref": str(approval.get("approval_id") or "approval"),
                    "focus_label": display_action(approval.get("action")),
                    "next_command": "/pending --all" if len(approvals) > 1 else "/pending",
                }
            )
        else:
            context.update(
                {
                    "context_kind": "approval_clear",
                    "focus_kind": "none",
                    "focus_label": "승인할 항목 없음",
                    "next_command": "/pending",
                }
            )
    elif command == "plans":
        plans = payload.get("plans") if isinstance(payload.get("plans"), list) else []
        if plans and isinstance(plans[0], dict):
            plan = plans[0]
            plan_id = str(plan.get("plan_id") or "plan")
            context.update(
                {
                    "context_kind": "plan_attention",
                    "focus_kind": "plan",
                    "focus_ref": plan_id,
                    "focus_label": display_review_status(plan.get("review_status")),
                    "next_command": show_command_for(plan_id),
                }
            )
        else:
            context.update(
                {
                    "context_kind": "plan_clear",
                    "focus_kind": "none",
                    "focus_label": "등록된 계획 없음",
                    "next_command": "/plans --latest",
                }
            )
    elif command == "show":
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        plan_id = str(plan.get("plan_id") or "unknown")
        context.update(
            {
                "context_kind": "plan_detail",
                "focus_kind": "plan",
                "focus_ref": plan_id,
                "focus_label": display_review_status(plan.get("review_status")),
                "next_command": "/plans --latest",
            }
        )
    return context






def finalize_dispatch_result(result: dict[str, Any], message_preview: str) -> dict[str, Any]:
    attach_choice_surface(result, None)
    result.update(
        {
            "status": "rendered",
            "projection": None,
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result


def render_dispatch_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    parsed: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Render the read-only part of a dispatch command.

    State mutation (storing a confirmation, popping it, running the executor
    chain) happens in run_once so it persists and stays out of the offset
    save path. This function only reads a fresh surface and builds cards.
    """

    result["parsed_command"] = parsed
    command = parsed.get("command")
    generated_at = result["generated_at"]
    if command == "decisions":
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            decisions = open_decision_actions(surface)
            message_preview = render_decisions_message(
                profile=args.profile, generated_at=generated_at, decisions=decisions
            )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="결정 목록을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "decision":
        decision_id = str(parsed.get("decision_id") or "")
        action_kind = str(parsed.get("decision_action_kind") or "")
        note = str(parsed.get("decision_note") or "")
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            envelope = find_action_envelope(surface, decision_id, action_kind)
            if envelope is None:
                kinds = available_action_kinds(surface, decision_id)
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline=f"{decision_id}에 {action_kind} 동작이 없습니다",
                    detail=f"가능한 동작: {', '.join(kinds) or '없음'}",
                )
            else:
                confirmation = build_confirmation(
                    kind="decision",
                    target_id=decision_id,
                    action_kind=action_kind,
                    observed_hash=str(envelope.get("observed_hash") or ""),
                    note=note,
                    chat_hash=None,
                    ttl_sec=args.dispatch_confirm_ttl_sec,
                )
                result["pending_dispatch_confirmation"] = confirmation
                message_preview = render_dispatch_confirm_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    decision_id=decision_id,
                    action_kind=action_kind,
                    note=note,
                    token=confirmation["token"],
                )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="실행 확인을 준비하지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "recovery":
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            recoveries = open_recovery_actions(surface)
            message_preview = render_recovery_message(
                profile=args.profile, generated_at=generated_at, recoveries=recoveries
            )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="복구 목록을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "recover":
        closeout_id = str(parsed.get("closeout_id") or "")
        action_kind = str(parsed.get("recovery_action_kind") or "")
        note = str(parsed.get("recovery_note") or "")
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            envelope = find_recovery_envelope(surface, closeout_id, action_kind)
            if envelope is None:
                kinds = available_recovery_action_kinds(surface, closeout_id)
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline=f"{closeout_id}에 {action_kind} 복구가 없습니다",
                    detail=f"가능한 동작: {', '.join(kinds) or '없음'}",
                )
            else:
                confirmation = build_confirmation(
                    kind="recovery",
                    target_id=closeout_id,
                    action_kind=action_kind,
                    observed_hash=str(envelope.get("observed_hash") or ""),
                    note=note,
                    chat_hash=None,
                    ttl_sec=args.dispatch_confirm_ttl_sec,
                )
                result["pending_dispatch_confirmation"] = confirmation
                message_preview = render_recovery_confirm_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    closeout_id=closeout_id,
                    action_kind=action_kind,
                    note=note,
                    token=confirmation["token"],
                )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="복구 확인을 준비하지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "runtime":
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            rows = open_runtime_dispatch(surface)
            message_preview = render_runtime_message(
                profile=args.profile,
                generated_at=generated_at,
                rows=rows,
                enabled=bool(args.enable_runtime_dispatch),
            )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="런타임 대기열을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "dispatch":
        if not args.enable_runtime_dispatch:
            message_preview = render_runtime_disabled_message(
                profile=args.profile, generated_at=generated_at
            )
            return finalize_dispatch_result(result, message_preview)
        closeout_id = str(parsed.get("closeout_id") or "")
        runner = str(parsed.get("runner") or "")
        command_text = str(parsed.get("dispatch_command_text") or "")
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            item = runtime_dispatch_item(surface, closeout_id)
            if item is None:
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline=f"{closeout_id} 런타임 항목이 없습니다",
                    detail="/runtime 로 대기열을 확인하세요.",
                )
            else:
                confirmation = build_confirmation(
                    kind="runtime",
                    target_id=closeout_id,
                    action_kind="dispatch",
                    observed_hash="",
                    note=command_text,
                    chat_hash=None,
                    ttl_sec=args.dispatch_confirm_ttl_sec,
                )
                confirmation["runner"] = runner
                confirmation["command"] = command_text
                result["pending_dispatch_confirmation"] = confirmation
                message_preview = render_runtime_confirm_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    closeout_id=closeout_id,
                    runner=runner,
                    command=command_text,
                    token=confirmation["token"],
                )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="런타임 확인을 준비하지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    # confirm and cancel are resolved against persisted state in run_once;
    # this placeholder is only visible on non-live probe paths.
    message_preview = render_dispatch_error_message(
        profile=args.profile,
        generated_at=generated_at,
        headline="라이브 처리 필요",
        detail="이 명령은 라이브 폴링에서 처리됩니다.",
    )
    return finalize_dispatch_result(result, message_preview)


def resolve_dispatch_confirmation(
    args: argparse.Namespace,
    state: dict[str, Any],
    chat_hash: str,
    rendered: dict[str, Any],
    parsed_command: dict[str, Any],
) -> None:
    """Apply the state-touching part of a dispatch command inside run_once."""

    command = parsed_command.get("command")
    generated_at = rendered["generated_at"]
    if command in {"decision", "recover", "dispatch"}:
        confirmation = rendered.get("pending_dispatch_confirmation")
        if isinstance(confirmation, dict):
            confirmation["chat_id_hash"] = chat_hash
            previous = store_confirmation(state, chat_hash, confirmation)
            if previous is not None:
                # A new request invalidates the old token. The card stays within
                # its line budget; the old /confirm already fails clearly, and
                # this field records the supersede for logs and tests.
                rendered["superseded_pending_confirmation"] = True
        return
    if command == "cancel":
        cleared = clear_confirmation(state, chat_hash)
        message_preview = render_dispatch_cancel_message(
            profile=args.profile, generated_at=generated_at, cleared=cleared
        )
        rendered["message_preview"] = message_preview
        rendered["mobile_card_contract"] = mobile_card_contract(message_preview)
        return
    if command != "confirm":
        return

    token = str(parsed_command.get("confirm_token") or "")
    confirmation = pop_confirmation(state, chat_hash, token)
    message_preview = None
    if confirmation is None:
        message_preview = render_dispatch_error_message(
            profile=args.profile,
            generated_at=generated_at,
            headline="확인 요청을 찾을 수 없습니다",
            detail="/decisions 로 다시 시작하세요.",
        )
    elif not confirmation_is_fresh(confirmation):
        message_preview = render_dispatch_error_message(
            profile=args.profile,
            generated_at=generated_at,
            headline="확인 시간이 만료되었습니다",
            detail="/decisions 로 다시 시작하세요.",
        )
    elif str(confirmation.get("kind") or "") == "runtime":
        # Runtime dispatch has no envelope hash; runtime-preflight re-verifies
        # the closeout against the latest decision receipt at execution time.
        if not args.enable_runtime_dispatch:
            message_preview = render_runtime_disabled_message(
                profile=args.profile, generated_at=generated_at
            )
        else:
            try:
                dispatch_result = apply_runtime_dispatch(
                    args.forager_bin,
                    args.profile,
                    closeout_id=str(confirmation.get("target_id") or ""),
                    runner=str(confirmation.get("runner") or ""),
                    command=str(confirmation.get("command") or ""),
                )
                rendered["dispatch_result"] = dispatch_result
                message_preview = render_runtime_result_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    result=dispatch_result,
                )
            except Exception as error:  # noqa: BLE001 - poll loop must never crash here
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline="런타임 디스패치에 실패했습니다",
                    detail=str(error),
                )
    else:
        kind = str(confirmation.get("kind") or "decision")
        # Support decision confirmations issued before the kind field existed.
        target_id = str(confirmation.get("target_id") or confirmation.get("decision_id") or "")
        is_recovery = kind == "recovery"
        list_hint = "/recovery" if is_recovery else "/decisions"
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            if is_recovery:
                envelope = find_recovery_envelope(
                    surface, target_id, confirmation["action_kind"]
                )
            else:
                envelope = find_action_envelope(
                    surface, target_id, confirmation["action_kind"]
                )
            if envelope is None:
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline="동작이 더 이상 없습니다",
                    detail=f"{list_hint} 로 다시 확인하세요.",
                )
            elif str(envelope.get("observed_hash") or "") != confirmation.get("observed_hash"):
                message_preview = render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline="대상이 변경되었습니다",
                    detail=f"{list_hint} 로 최신 상태를 다시 확인하세요.",
                )
            elif is_recovery:
                dispatch_result = apply_recovery_action(
                    args.forager_bin, args.profile, envelope
                )
                rendered["dispatch_result"] = dispatch_result
                message_preview = render_recovery_result_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    result=dispatch_result,
                )
            else:
                dispatch_result = apply_decision_action(
                    args.forager_bin,
                    args.profile,
                    envelope,
                    note=str(confirmation.get("note") or ""),
                )
                rendered["dispatch_result"] = dispatch_result
                message_preview = render_dispatch_result_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    result=dispatch_result,
                )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="실행에 실패했습니다",
                detail=str(error),
            )
    if message_preview is not None:
        rendered["message_preview"] = message_preview
        rendered["mobile_card_contract"] = mobile_card_contract(message_preview)


def render_command_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    command_text: str,
    *,
    mode: str,
    feedback_context: dict[str, Any] | None = None,
    chat_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = result_base(args, config, mode)
    result["command_text"] = sanitize_text(command_text, max_chars=400)
    parsed = parse_remote_command(command_text)
    if not parsed.get("supported"):
        result["parsed_command"] = parsed
        message_preview = help_message(profile=args.profile, generated_at=result["generated_at"])
        attach_choice_surface(result, None)
        result.update(
            {
                "status": "unsupported",
                "reason": parsed.get("reason"),
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "help":
        result["parsed_command"] = parsed
        message_preview = help_message(profile=args.profile, generated_at=result["generated_at"])
        attach_choice_surface(result, None)
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "chat":
        agent_intent = chat_with_agent(
            args,
            str(parsed.get("chat_text") or command_text),
            feedback_context=feedback_context,
            chat_history=chat_history,
        )
        if isinstance(agent_intent, dict):
            parsed["agent_intent"] = agent_intent
            parsed["agent_chat_reason"] = str(
                agent_intent.get("reason") or agent_intent.get("status") or "unknown"
            )
        result["parsed_command"] = parsed
        message_preview = render_chat_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            chat_text=str(parsed.get("chat_text") or command_text),
            feedback_context=feedback_context,
            agent_intent=agent_intent if isinstance(agent_intent, dict) else None,
        )
        attach_choice_surface(result, feedback_context)
        if isinstance(feedback_context, dict):
            result["feedback_context"] = feedback_context
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "feedback":
        result["parsed_command"] = parsed
        message_preview = render_feedback_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            feedback_text=str(parsed.get("feedback_text") or command_text),
            feedback_kind=str(parsed.get("feedback_kind") or "freeform_feedback"),
            feedback_context=feedback_context,
        )
        attach_choice_surface(result, feedback_context)
        if isinstance(feedback_context, dict):
            result["feedback_context"] = feedback_context
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "remember":
        result["parsed_command"] = parsed
        message_preview = render_wiki_candidate_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            remember_text=str(parsed.get("remember_text") or command_text),
        )
        attach_choice_surface(result, feedback_context)
        if isinstance(feedback_context, dict):
            result["feedback_context"] = feedback_context
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "plan_request":
        agent_intent = classify_feedback_with_agent(
            args,
            str(parsed.get("feedback_text") or command_text),
            feedback_context=feedback_context,
        )
        if isinstance(agent_intent, dict):
            parsed["agent_intent"] = agent_intent
            parsed["feedback_kind"] = "planning_request"
            parsed["reason"] = f"agent_intent:{agent_intent.get('intent') or 'unknown'}"
        result["parsed_command"] = parsed
        message_preview = render_feedback_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            feedback_text=str(parsed.get("feedback_text") or command_text),
            feedback_kind="planning_request",
            feedback_context=feedback_context,
            agent_intent=agent_intent if isinstance(agent_intent, dict) else None,
        )
        attach_choice_surface(result, feedback_context)
        if isinstance(feedback_context, dict):
            result["feedback_context"] = feedback_context
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") in {
        "decisions",
        "decision",
        "recovery",
        "recover",
        "runtime",
        "dispatch",
        "confirm",
        "cancel",
    }:
        return render_dispatch_command(args, config, parsed, result)
    result["parsed_command"] = parsed
    if args.projection_file:
        projection = load_projection_file(args.projection_file, parsed)
    else:
        projection = run_projection(args.forager_bin, args.profile, parsed)
    adapter_health = None
    if parsed.get("command") == "status" and (not args.dry_run or args.loop_status_file.exists()):
        adapter_health = listener_health(args, config)
    message_preview = render_projection_message(
        projection,
        max_chars=max(200, int(args.max_message_chars)),
        adapter_health=adapter_health,
    )
    interaction_context = interaction_context_from_projection(projection)
    attach_choice_surface(result, interaction_context)
    result.update(
        {
            "status": "rendered",
            "projection_schema": projection.get("schema"),
            "projection": projection,
            "adapter_health": adapter_health,
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result


def message_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    message = update.get("message")
    return message if isinstance(message, dict) else None


def update_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    return str(text or "").strip()


def chat_id_for(message: dict[str, Any]) -> str:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return ""
    value = chat.get("id")
    return str(value or "").strip()


def user_id_for(message: dict[str, Any]) -> str:
    user = message.get("from")
    if not isinstance(user, dict):
        return ""
    value = user.get("id")
    return str(value or "").strip()


def message_id_for(message: dict[str, Any]) -> int | None:
    value = message.get("message_id")
    return int(value) if isinstance(value, int) else None


def record_feedback(
    args: argparse.Namespace,
    config: dict[str, Any],
    message: dict[str, Any],
    text: str,
    *,
    feedback_context: dict[str, Any] | None = None,
    parsed_command: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feedback_kind = classify_feedback_kind(text)
    agent_intent = None
    if isinstance(parsed_command, dict):
        parsed_kind = str(parsed_command.get("feedback_kind") or "").strip()
        if parsed_kind in {"freeform_feedback", "planning_request"}:
            feedback_kind = parsed_kind
        parsed_agent = parsed_command.get("agent_intent")
        if isinstance(parsed_agent, dict):
            agent_intent = parsed_agent
    record = {
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": utc_now(),
        "profile": args.profile,
        "chat_id_hash": sha256_short(chat_id_for(message)),
        "user_id_hash": sha256_short(user_id_for(message)),
        "message_id": message_id_for(message),
        "feedback_text": sanitize_text(text, max_chars=2000),
        "feedback_kind": feedback_kind,
        "target_chat_id_hash": config.get("target_chat_id_hash"),
        "feedback_context": feedback_context,
    }
    if agent_intent:
        record["agent_intent"] = agent_intent
    append_jsonl(args.feedback_file, record)
    return {
        "feedback_recorded": True,
        "feedback_file": str(args.feedback_file),
        "feedback_text_chars": len(str(text or "")),
        "feedback_context": feedback_context,
        "feedback_record": record,
    }


def update_is_allowed(config: dict[str, Any], message: dict[str, Any]) -> tuple[bool, str]:
    chat_id = chat_id_for(message)
    user_id = user_id_for(message)
    allowed_chat_ids = config.get("allowed_chat_ids") or set()
    allowed_user_ids = config.get("allowed_user_ids") or set()
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return False, "chat_not_allowed"
    if allowed_user_ids and user_id not in allowed_user_ids:
        return False, "user_not_allowed"
    return True, "allowed"


def run_once(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    state = load_state(args.state_file)
    updates = get_updates(config, int(state.get("offset") or 0), args)
    result = result_base(args, config, "live_once")
    result.update({"status": "no_update", "updates_seen": len(updates)})
    max_update_id = int(state.get("offset") or 0) - 1
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
        message = message_from_update(update)
        if not message:
            continue
        allowed, reason = update_is_allowed(config, message)
        if not allowed:
            result.update(
                {
                    "status": "ignored",
                    "reason": reason,
                    "chat_id_hash": sha256_short(chat_id_for(message)),
                    "user_id_hash": sha256_short(user_id_for(message)),
                }
            )
            continue
        text = update_text(message)
        if not text:
            result.update({"status": "ignored", "reason": "empty_message"})
            continue
        chat_hash = sha256_short(chat_id_for(message))
        active_session = active_remote_plan_session(state, chat_hash)
        session_payload = remote_plan_session_command_payload(text) if active_session else None
        if active_session and session_payload:
            rendered = handle_remote_plan_session_input(
                args,
                config,
                state,
                chat_hash=chat_hash,
                session=active_session,
                text=session_payload,
                mode="live_once",
            )
        elif active_session and remote_plan_session_should_handle_text(args, active_session, text):
            rendered = handle_remote_plan_session_input(
                args,
                config,
                state,
                chat_hash=chat_hash,
                session=active_session,
                text=text,
                mode="live_once",
            )
        else:
            feedback_context = last_context_for_chat_hash(
                state,
                chat_hash,
                max_age_sec=args.context_max_age_sec,
            )
            rendered = render_command_result(
                args,
                config,
                text,
                mode="live_once",
                feedback_context=feedback_context,
                chat_history=chat_history_for_chat_hash(
                    state,
                    chat_hash,
                    max_age_sec=args.context_max_age_sec,
                ),
            )
        rendered["updates_seen"] = len(updates)
        if isinstance(update_id, int):
            rendered["processed_update_id"] = update_id
        parsed_command = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
        if parsed_command.get("command") == "remember":
            remember_text = str(parsed_command.get("remember_text") or text)
            # A persistence failure must not raise before the offset save:
            # that would re-deliver the same update on every poll.
            try:
                record_result = record_remember_candidate(
                    profile=args.profile,
                    text=remember_text,
                    chat_hash=chat_hash,
                    user_hash=sha256_short(user_id_for(message)),
                    message_id=message_id_for(message),
                )
            except (OSError, RemoteOperatorTelegramError) as error:
                record_result = {
                    "wiki_candidate_recorded": False,
                    "wiki_candidate_status": "failed",
                    "wiki_candidate_error": sanitize_text(str(error), max_chars=240),
                }
            rendered.update(record_result)
            rendered["message_preview"] = render_wiki_candidate_message(
                profile=args.profile,
                generated_at=rendered["generated_at"],
                remember_text=remember_text,
                record_result=record_result,
            )
            rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
        if parsed_command.get("command") in {"feedback", "plan_request"}:
            feedback_context = last_context_for_chat_hash(
                state,
                chat_hash,
                max_age_sec=args.context_max_age_sec,
            )
            feedback_text = str(parsed_command.get("feedback_text") or parsed_command.get("plan_text") or text)
            feedback_result = record_feedback(
                args,
                config,
                message,
                feedback_text,
                feedback_context=feedback_context,
                parsed_command=parsed_command,
            )
            feedback_record = feedback_result.pop("feedback_record", None)
            rendered.update(feedback_result)
            if isinstance(feedback_record, dict):
                ingest_result = ingest_feedback_decision(args, feedback_record)
                rendered.update(ingest_result)
                rendered["message_preview"] = render_feedback_message(
                    profile=args.profile,
                    generated_at=rendered["generated_at"],
                    feedback_text=feedback_text,
                    feedback_kind=str(parsed_command.get("feedback_kind") or "freeform_feedback"),
                    feedback_context=feedback_context,
                    inbox_status=str(ingest_result.get("decision_feedback_ingest_status") or ""),
                    agent_intent=parsed_command.get("agent_intent")
                    if isinstance(parsed_command.get("agent_intent"), dict)
                    else None,
                )
                rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
                if parsed_command.get("command") == "plan_request":
                    session = create_remote_plan_session(
                        args,
                        chat_hash=chat_hash,
                        request_text=feedback_text,
                        parsed_command=parsed_command,
                        feedback_context=feedback_context,
                        decision_id=ingest_result.get("decision_feedback_decision_id"),
                    )
                    store_remote_plan_session(state, chat_hash, session)
                    rendered["remote_plan_session"] = public_remote_plan_session(session)
                    rendered["message_preview"] = render_project_selection_message(
                        profile=args.profile,
                        session=session,
                    )
                    attach_choice_surface(rendered, remote_plan_selection_context(session))
                    rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
        if parsed_command.get("command") in {
            "decision",
            "recover",
            "dispatch",
            "confirm",
            "cancel",
        }:
            # Guarded remote execution. Every branch catches its own errors so
            # nothing raises before the offset save and re-delivers the update.
            resolve_dispatch_confirmation(args, state, chat_hash, rendered, parsed_command)
        try:
            message_id = send_message(
                config,
                chat_id_for(message),
                rendered["message_preview"],
                args,
                reply_markup=rendered.get("reply_markup_preview")
                if isinstance(rendered.get("reply_markup_preview"), dict)
                else None,
            )
            rendered["send_status"] = "dry_run" if args.dry_run else "sent"
        except RemoteOperatorTelegramError as error:
            if "Telegram API" not in str(error):
                raise
            message_id = None
            rendered["status"] = "send_failed"
            rendered["send_status"] = "failed"
            rendered["send_error"] = sanitize_text(str(error), max_chars=240)
        rendered["sent_message_id"] = message_id
        remember_context_for_chat_hash(state, chat_hash, rendered)
        rendered_parsed = (
            rendered.get("parsed_command")
            if isinstance(rendered.get("parsed_command"), dict)
            else {}
        )
        if rendered_parsed.get("command") == "chat":
            append_chat_history(
                state,
                chat_hash,
                role="operator",
                text=str(rendered_parsed.get("chat_text") or text),
            )
            assistant_reply = agent_assistant_reply(rendered_parsed.get("agent_intent"))
            if assistant_reply:
                append_chat_history(state, chat_hash, role="assistant", text=assistant_reply)
        result = rendered
        break
    if max_update_id >= int(state.get("offset") or 0):
        state["offset"] = max_update_id + 1
        save_state(args.state_file, state)
    return result


def loop_summary_base(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    result = result_base(args, config, "live_loop")
    result.update(
        {
            "status": "polling",
            "poll_count": 0,
            "updates_seen": 0,
            "handled_result_count": 0,
            "last_result": None,
            "last_handled_result": None,
        }
    )
    return result


def update_loop_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    summary["poll_count"] = int(summary.get("poll_count") or 0) + 1
    summary["updates_seen"] = int(summary.get("updates_seen") or 0) + int(result.get("updates_seen") or 0)
    summary["last_result"] = result
    if result.get("status") not in {"no_update", "poll_error", "loop_error"}:
        summary["handled_result_count"] = int(summary.get("handled_result_count") or 0) + 1
        summary["last_handled_result"] = result


def loop_transport_error_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    error: RemoteOperatorTelegramError,
) -> dict[str, Any]:
    result = result_base(args, config, "live_once")
    result.update(
        {
            "status": "poll_error",
            "updates_seen": 0,
            "reason": "telegram_transport_error",
            "error": sanitize_text(str(error), max_chars=240),
        }
    )
    return result


def loop_internal_error_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    error: Exception,
) -> dict[str, Any]:
    result = result_base(args, config, "live_once")
    result.update(
        {
            "status": "loop_error",
            "updates_seen": 0,
            "reason": "unexpected_loop_exception",
            "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=240),
        }
    )
    return result


def loop_backoff_if_needed(
    args: argparse.Namespace,
    result: dict[str, Any],
    consecutive_errors: int,
) -> int:
    status = str(result.get("status") or "")
    if status not in {"poll_error", "send_failed", "loop_error"}:
        return 0
    consecutive_errors += 1
    if args.max_polls is None:
        sleep_sec = min(max(0, int(args.poll_error_backoff_sec)) * consecutive_errors, 60)
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return consecutive_errors


def loop_status_path(args: argparse.Namespace) -> pathlib.Path | None:
    if args.out:
        return args.out
    return args.loop_status_file


def run_loop(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    summary = loop_summary_base(args, config)
    max_polls = args.max_polls
    status_path = loop_status_path(args)
    consecutive_errors = 0
    try:
        while max_polls is None or int(summary["poll_count"]) < max_polls:
            try:
                result = run_once(args, config)
            except RemoteOperatorTelegramError as error:
                if "Telegram API" not in str(error):
                    result = loop_internal_error_result(args, config, error)
                else:
                    result = loop_transport_error_result(args, config, error)
            except Exception as error:
                result = loop_internal_error_result(args, config, error)
            update_loop_summary(summary, result)
            if status_path:
                write_json(status_path, summary)
            if max_polls is None and result.get("status") != "no_update":
                print(json.dumps(result, ensure_ascii=False), flush=True)
            consecutive_errors = loop_backoff_if_needed(args, result, consecutive_errors)
    except KeyboardInterrupt:
        summary["status"] = "interrupted"
        if status_path:
            write_json(status_path, summary)
        return summary
    summary["status"] = "max_polls_reached" if max_polls is not None else "stopped"
    return summary


def send_command_text(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    target_chat_id = str(config.get("target_chat_id") or "").strip()
    if not target_chat_id:
        raise RemoteOperatorTelegramError("target chat id is missing")
    state = load_state(args.state_file)
    feedback_context = last_context_for_chat_hash(
        state,
        sha256_short(target_chat_id),
        max_age_sec=args.context_max_age_sec,
    )
    rendered = render_command_result(
        args,
        config,
        args.send_command_text or "/status",
        mode="live_send",
        feedback_context=feedback_context,
    )
    if rendered.get("status") != "rendered":
        return rendered
    rendered["sent_message_id"] = send_message(
        config,
        target_chat_id,
        rendered["message_preview"],
        args,
        reply_markup=rendered.get("reply_markup_preview")
        if isinstance(rendered.get("reply_markup_preview"), dict)
        else None,
    )
    remember_context_for_chat_hash(state, sha256_short(target_chat_id), rendered)
    save_state(args.state_file, state)
    return rendered


def emit_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()
    try:
        if args.max_polls is not None and args.max_polls < 1:
            raise RemoteOperatorTelegramError("--max-polls must be at least 1")
        if args.once and args.max_polls is not None:
            raise RemoteOperatorTelegramError("--once and --max-polls cannot be used together")
        if args.projection_file and not args.dry_run:
            raise RemoteOperatorTelegramError("--projection-file is only allowed with --dry-run")
        if args.replay_update_file and not args.dry_run:
            raise RemoteOperatorTelegramError("--replay-update-file is only allowed with --dry-run")
        if args.max_polls is not None and not args.replay_update_file and (args.dry_run or args.once or args.send_command_text):
            raise RemoteOperatorTelegramError("--max-polls is only used by the live poller or dry-run replay poller")
        if args.health:
            config = resolve_telegram_config(args.env_file, required=False)
            result = listener_health(args, config)
            emit_result(args, result)
            return 0 if result.get("health_status") == "healthy" else 1
        if args.dry_run:
            config = resolve_telegram_config(args.env_file, required=False)
            if args.replay_update_file:
                result = run_loop(args, config) if args.max_polls is not None else run_once(args, config)
                emit_result(args, result)
                return 0 if result.get("status") != "unsupported" else 2
            command_text = args.command_text or args.send_command_text or "/status"
            state = load_state(args.state_file)
            feedback_context = last_context_for_chat_hash(
                state,
                config.get("target_chat_id_hash"),
                max_age_sec=args.context_max_age_sec,
            )
            result = render_command_result(
                args,
                config,
                command_text,
                mode="dry_run",
                feedback_context=feedback_context,
            )
            emit_result(args, result)
            return 0 if result.get("status") != "unsupported" else 2
        if args.send_command_text:
            config = resolve_telegram_config(args.env_file, required=True)
            result = send_command_text(args, config)
            emit_result(args, result)
            return 0 if result.get("status") != "unsupported" else 2
        config = resolve_telegram_config(args.env_file, required=True)
        if args.enable_runtime_dispatch:
            # Make this powerful mode visible in logs/systemd journal: it lets
            # the allowlisted chat queue arbitrary commands for tick execution.
            print(
                "WARNING: --enable-runtime-dispatch is ON. Allowlisted Telegram chat can "
                "queue arbitrary commands for `forager offdesk tick`. Treat this as remote "
                "command execution and keep the chat allowlist locked down.",
                file=sys.stderr,
                flush=True,
            )
        result = run_once(args, config) if args.once else run_loop(args, config)
        emit_result(args, result)
        return 0
    except RemoteOperatorTelegramError as error:
        result = {
            "schema": RESULT_SCHEMA,
            "generated_at": utc_now(),
            "status": "error",
            "error": sanitize_text(str(error)),
            "read_only": True,
            "mutation_authorized": False,
            "approval_authorized": False,
            "forbidden_remote_intents": list(FORBIDDEN_REMOTE_INTENTS),
        }
        if args.out:
            write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
