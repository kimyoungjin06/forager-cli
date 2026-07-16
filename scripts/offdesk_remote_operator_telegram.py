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
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from typing import Any

from telegram_operator.agent import (
    DEFAULT_AGENT_CONFIG_FILE,
    agent_runtime_status as resolve_agent_runtime_status,
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
    unique_nonempty,
    write_json,
)
from telegram_operator.config import resolve_telegram_config
from telegram_operator.health import (
    action_readiness,
    listener_health,
    readiness_from_agent_intent,
)
from telegram_operator.redaction import public_remote_plan_session
from telegram_operator.receipts import (
    bind_prepared_workload_to_execution_brief,
    create_closeout_packet_from_runtime_monitor,
    create_closeout_review_handoff_from_packet,
    create_closeout_verdict_from_handoff,
    create_enqueue_handoff_from_execution_brief,
    create_enqueue_run_from_workload_binding,
    create_execution_brief_from_gate_resolution,
    create_plan_draft,
    create_project_init_preview,
    create_remote_plan_session,
    create_runtime_monitor_from_runtime_start,
    create_runtime_start_from_enqueue_run,
    prepare_plan_launch_packet,
    run_project_init_packet,
    sha256_hex,
    sha256_id,
)
from telegram_operator.schemas import (
    REMOTE_PLAN_SESSION_SCHEMA,
    PROJECT_INIT_PREVIEW_SCHEMA,
    PROJECT_INIT_RUN_SCHEMA,
    PLAN_DRAFT_SCHEMA,
    PLAN_REGISTRATION_SCHEMA,
    PLAN_REVIEW_SCHEMA,
    PLAN_LAUNCH_PREP_SCHEMA,
    PLAN_GATE_REQUEST_SCHEMA,
    PLAN_GATE_RESOLUTION_SCHEMA,
    PLAN_EXECUTION_BRIEF_SCHEMA,
    PLAN_ENQUEUE_HANDOFF_SCHEMA,
    PLAN_WORKLOAD_BINDING_SCHEMA,
    PLAN_ENQUEUE_RUN_SCHEMA,
    PLAN_RUNTIME_START_SCHEMA,
    PLAN_RUNTIME_MONITOR_SCHEMA,
    PLAN_CLOSEOUT_PACKET_SCHEMA,
    PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
    PLAN_CLOSEOUT_VERDICT_SCHEMA,
    PLAN_DRAFT_AUTHORITY_DENIALS,
)
from telegram_operator.plan_messages import (
    render_plan_closeout_packet_failed_message,
    render_plan_closeout_packet_message,
    render_plan_closeout_required_message,
    render_plan_closeout_review_handoff_failed_message,
    render_plan_closeout_review_handoff_message,
    render_plan_closeout_verdict_failed_message,
    render_plan_closeout_verdict_message,
    render_plan_draft_failed_message,
    render_plan_draft_required_message,
    render_plan_draft_validated_message,
    render_plan_enqueue_handoff_created_message,
    render_plan_enqueue_handoff_failed_message,
    render_plan_enqueue_handoff_required_message,
    render_plan_enqueue_run_done_message,
    render_plan_enqueue_run_failed_message,
    render_plan_enqueue_run_required_message,
    render_plan_execution_brief_created_message,
    render_plan_execution_brief_failed_message,
    render_plan_execution_brief_required_message,
    render_plan_gate_request_created_message,
    render_plan_gate_request_failed_message,
    render_plan_gate_request_required_message,
    render_plan_gate_resolution_done_message,
    render_plan_gate_resolution_failed_message,
    render_plan_gate_resolution_required_message,
    render_plan_launch_prep_failed_message,
    render_plan_launch_prep_prepared_message,
    render_plan_launch_prep_required_message,
    render_plan_registered_message,
    render_plan_registration_failed_message,
    render_plan_registration_required_message,
    render_plan_registration_stale_message,
    render_plan_review_approved_message,
    render_plan_review_failed_message,
    render_plan_review_required_message,
    render_plan_runtime_monitor_failed_message,
    render_plan_runtime_monitor_message,
    render_plan_runtime_monitor_required_message,
    render_plan_runtime_start_failed_message,
    render_plan_runtime_start_required_message,
    render_plan_runtime_started_message,
    render_plan_workload_binding_failed_message,
    render_plan_workload_bound_message,
    render_plan_workload_path_required_message,
    render_project_init_created_message,
    render_project_init_failed_message,
    render_project_init_preview_message,
    render_project_init_preview_required_message,
    render_project_path_required_message,
    render_project_selected_message,
    render_project_selection_deferred_message,
    render_project_selection_message,
    render_remote_plan_note_message,
)
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
    parse_utc_timestamp,
    load_state,
    remember_context_for_chat_hash,
    save_state,
)
from telegram_operator.project_candidates import (
    PROJECT_CANDIDATE_SCHEMA,
    build_project_candidate,
    manual_project_candidate,
    discover_project_paths,
    display_project_readiness,
    display_project_risk,
    is_git_repo,
    project_marker_names,
    public_project_candidate,
    ranked_project_candidates,
    request_tokens,
    scan_project_candidates,
    slugify_project_key,
    truncate_label,
    workspace_roots,
)
from telegram_operator.rendering import (
    MOBILE_CARD_MAX_LINES,
    REMOTE_PLAN_INIT_CONTEXT_KIND,
    REMOTE_PLAN_SESSION_CONTEXT_KIND,
    agent_assistant_reply,
    choice_keyboard,
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
    choice_surface_contract,
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
    title_with_profile,
)
from telegram_operator.routing import (
    parse_remote_command,
    remote_plan_session_command_payload,
)
from telegram_operator.transport import get_updates, send_message
from telegram_operator.wiki import record_remember_candidate


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
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
RESULT_SCHEMA = "remote_operator_telegram_adapter_result.v1"
INTERACTION_CONTEXT_SCHEMA = "telegram_interaction_context.v1"
FORBIDDEN_REMOTE_INTENTS = (
    "approve_plan",
    "approve_launch",
    "deny_launch",
    "enqueue",
    "launch",
    "dispatch",
    "shell",
    "git_push",
    "delete",
    "provider_retarget",
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




def remote_plan_choice_label(candidate: dict[str, Any]) -> str:
    rank = int(candidate.get("rank") or 0)
    name = truncate_label(candidate.get("display_name") or candidate.get("project_key"), max_chars=22)
    return f"{rank} {name}".strip()


def remote_plan_selection_context(session: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    return {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": "remote_plan_project_selection",
        "profile": session.get("profile") or "default",
        "context_kind": REMOTE_PLAN_SESSION_CONTEXT_KIND,
        "focus_kind": "remote_plan_session",
        "focus_ref": session.get("session_id"),
        "focus_label": "계획 대상 선택",
        "next_command": None,
        "choice_labels": [remote_plan_choice_label(candidate) for candidate in candidates],
    }


def remote_plan_init_context(session: dict[str, Any]) -> dict[str, Any]:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    stage = str(session.get("stage") or "")
    if stage == "plan_gate_request_created":
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["게이트 승인", "게이트 거절", "보류"],
        }
    if stage == "plan_gate_approved":
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["실행 브리프 생성", "보류"],
        }
    if stage in {"plan_execution_brief_created", "plan_enqueue_handoff_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["큐 등록 검토", "보류"],
        }
    if stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["워크로드 패킷 연결", "보류"],
        }
    if stage in {"plan_workload_bound", "plan_enqueue_run_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["큐 등록 실행", "보류"],
        }
    if stage in {"plan_enqueued", "plan_runtime_start_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["실행 시작", "보류"],
        }
    if stage in {"plan_runtime_started", "plan_runtime_monitored", "plan_runtime_monitor_failed"}:
        monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
        if stage == "plan_runtime_monitored" and monitor.get("task_status") == "completed":
            labels = ["마무리 패킷 생성", "실행 상태 확인", "보류"]
        else:
            labels = ["실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {
        "plan_closeout_packet_created",
        "plan_closeout_packet_failed",
        "plan_closeout_review_handoff_created",
        "plan_closeout_review_handoff_failed",
    }:
        labels = ["실행 상태 확인", "보류"]
        if stage == "plan_closeout_packet_created":
            labels = ["마무리 검토 준비", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_packet_failed":
            labels = ["마무리 패킷 생성", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_review_handoff_failed":
            labels = ["마무리 검토 준비", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_review_handoff_created":
            labels = ["승인 기록", "수정 요청 기록", "차단 기록", "실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {"plan_closeout_verdict_recorded", "plan_closeout_verdict_failed"}:
        labels = ["실행 상태 확인", "보류"]
        if stage == "plan_closeout_verdict_failed":
            labels = ["수정 요청 기록", "차단 기록", "실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage == "project_init_previewed":
        primary = "초기화 생성"
    elif stage in {"plan_registered", "plan_review_failed"}:
        primary = "계획 승인"
    elif stage in {"plan_review_approved", "plan_launch_prep_failed"}:
        primary = "실행 준비 검토"
    elif stage in {"plan_launch_prep_prepared", "plan_gate_request_failed"}:
        primary = "게이트 요청"
    elif stage == "plan_draft_validated":
        primary = "계획 등록"
    elif stage in {"project_init_created", "plan_draft_failed"}:
        primary = "계획 초안 생성"
    elif stage == "plan_registration_failed":
        registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
        primary = "계획 초안 생성" if registration.get("status") == "stale" else "계획 등록"
    else:
        primary = "초기화 검토"
    return {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": "remote_plan_init_review",
        "profile": session.get("profile") or "default",
        "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
        "focus_kind": "remote_plan_session",
        "focus_ref": session.get("session_id"),
        "focus_label": candidate.get("display_name") or "프로젝트",
        "next_command": None,
        "choice_labels": [primary, "다시 선택", "보류"],
    }




























































































































def project_init_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "초기화 생성",
        "초기화 패킷 생성",
        "project init 생성",
        "project init run",
        "create init",
        "create project init",
    }




def plan_draft_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "계획 초안 생성",
        "계획초안 생성",
        "초안 생성",
        "plan draft",
        "create plan draft",
        "draft plan",
    }


def plan_registration_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "계획 등록",
        "계획등록",
        "plan register",
        "register plan",
        "create plan registration",
    }


def plan_review_approve_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "계획 승인",
        "계획승인",
        "승인",
        "plan approve",
        "approve plan",
        "approve",
    }


def plan_launch_prep_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 준비 검토",
        "실행준비 검토",
        "실행 준비",
        "실행준비",
        "launch prep",
        "plan launch prep",
        "prepare launch",
        "prepare launch packet",
    }


def plan_gate_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "게이트 요청",
        "게이트요청",
        "승인 요청",
        "승인요청",
        "gate request",
        "request gate",
        "create gate request",
        "request approval",
    }


def gate_approval_approve_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "게이트 승인",
        "게이트승인",
        "실행 승인",
        "실행승인",
        "approve gate",
        "gate approve",
        "approve approval",
    }


def gate_approval_deny_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "게이트 거절",
        "게이트거절",
        "게이트 반려",
        "실행 거절",
        "실행거절",
        "deny gate",
        "gate deny",
        "deny approval",
    }


def execution_brief_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 브리프 생성",
        "실행브리프 생성",
        "브리프 생성",
        "execution brief",
        "create execution brief",
        "runtime brief",
    }


def enqueue_handoff_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "큐 등록 검토",
        "큐등록 검토",
        "큐 등록 준비",
        "enqueue 검토",
        "enqueue 준비",
        "enqueue handoff",
        "create enqueue handoff",
        "prepare enqueue",
    }


def workload_binding_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "워크로드 패킷 연결",
        "워크로드패킷 연결",
        "prepared_task 연결",
        "prepared task 연결",
        "bind workload",
        "workload binding",
        "connect workload",
    }


def enqueue_run_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "큐 등록 실행",
        "큐등록 실행",
        "큐 등록",
        "enqueue 실행",
        "enqueue run",
        "run enqueue",
        "execute enqueue",
    }


def runtime_start_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 시작",
        "실행시작",
        "런타임 시작",
        "start runtime",
        "runtime start",
        "start execution",
    }


def runtime_monitor_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 상태 확인",
        "실행상태 확인",
        "상태 확인",
        "런타임 상태 확인",
        "monitor runtime",
        "runtime monitor",
        "check runtime",
        "check execution",
    }


def closeout_packet_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "마무리 패킷 생성",
        "마무리 생성",
        "closeout 생성",
        "closeout 패킷 생성",
        "closeout packet",
        "create closeout",
        "create closeout packet",
    }


def closeout_review_handoff_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "마무리 검토 준비",
        "마무리검토 준비",
        "closeout 검토 준비",
        "closeout-review 준비",
        "closeout review 준비",
        "prepare closeout review",
        "closeout review handoff",
    }


def closeout_verdict_request(text: str) -> tuple[str | None, str]:
    raw = str(text or "").strip()
    normalized = raw.lower()
    aliases: list[tuple[str, tuple[str, ...]]] = [
        (
            "revise",
            (
                "수정 요청 기록",
                "수정요청 기록",
                "수정 필요 기록",
                "revision required",
                "record revise",
                "revise closeout",
            ),
        ),
        (
            "blocked",
            (
                "차단 기록",
                "블락 기록",
                "blocked 기록",
                "record blocked",
                "block closeout",
            ),
        ),
        (
            "approved",
            (
                "승인 기록",
                "승인",
                "approved 기록",
                "record approved",
                "approve closeout",
                "accept closeout",
            ),
        ),
    ]
    for verdict, values in aliases:
        for value in values:
            lowered = value.lower()
            if normalized == lowered:
                return verdict, ""
            if normalized.startswith(f"{lowered}:") or normalized.startswith(f"{lowered} -"):
                note = raw[len(value) :].lstrip(" :-")
                return verdict, sanitize_text(note, max_chars=500)
    return None, ""






def register_plan_draft(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    project_key = str(draft.get("project_key") or "project")
    plan_path = pathlib.Path(str(draft.get("plan_artifact_path") or ""))
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_REGISTRATION.json"
    receipt = {
        "schema": PLAN_REGISTRATION_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_artifact_path": str(plan_path),
        "expected_plan_sha256": draft.get("plan_sha256"),
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    try:
        current_sha = sha256_hex(plan_path.read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan draft unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_plan_sha256"] = current_sha
    if draft.get("plan_sha256") != current_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "plan draft changed after validation",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "plan",
            str(plan_path),
            "--project-key",
            project_key,
            "--request-id",
            str(session.get("session_id") or ""),
            "--json",
        ]
    )
    receipt["registration_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_registration_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        registration_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan registration did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "registered",
            "registration_output": registration_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def registered_plan_ref(registration: dict[str, Any]) -> str:
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    plan_id = str(output.get("plan_id") or "").strip()
    if plan_id:
        return plan_id
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    registry_dir = str(artifacts.get("registry_dir") or "").strip()
    if registry_dir:
        return pathlib.Path(registry_dir).name
    return ""


def approve_registered_plan(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    registration: dict[str, Any],
) -> dict[str, Any]:
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or registration.get("project_key") or "project")
    plan_ref = registered_plan_ref(registration)
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_REVIEW.json"
    copied_source_json = str(artifacts.get("copied_source_json") or "").strip()
    expected_source_sha = str(output.get("source_sha256") or "").strip()
    receipt = {
        "schema": PLAN_REVIEW_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_ref": plan_ref,
        "registration_json": str(artifacts.get("registration_json") or ""),
        "copied_source_json": copied_source_json,
        "expected_source_sha256": expected_source_sha,
        "plan_review_authorized": True,
        "approval_authorized": False,
        "execution_authorized": False,
        "launch_preparation_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not plan_ref:
        receipt.update(
            {
                "status": "error",
                "error": "registered plan id unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if copied_source_json and expected_source_sha:
        try:
            current_sha = sha256_hex(pathlib.Path(copied_source_json).read_bytes())
        except OSError as error:
            receipt.update(
                {
                    "status": "error",
                    "error": sanitize_text(f"registered plan source unavailable: {error}", max_chars=400),
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
        receipt["current_source_sha256"] = current_sha
        if current_sha != expected_source_sha:
            receipt.update(
                {
                    "status": "stale",
                    "error": "registered plan source changed after registration",
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "plan-review",
            plan_ref,
            "--decision",
            "approved",
            "--reviewer",
            "telegram",
            "--reason",
            "Telegram operator approved the registered plan for a separate launch-preparation review.",
            "--follow-up",
            "Prepare launch packet in a separate command.",
            "--json",
        ]
    )
    receipt["review_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_review_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        review_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan review did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "approved" if review_output.get("decision") == "approved" else "reviewed",
            "review_output": review_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt




def request_gate_for_launch_prep(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    launch_prep: dict[str, Any],
) -> dict[str, Any]:
    output = launch_prep.get("launch_prep_output") if isinstance(launch_prep.get("launch_prep_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or launch_prep.get("project_key") or "project")
    request_id = str(output.get("request_id") or session.get("session_id") or "telegram_request")
    task_id = str(output.get("task_id") or "").strip()
    if not task_id:
        task_id = f"telegram_gate_{sha256_id(str(session.get('session_id') or '') + ':' + str(output.get('prep_id') or 'launch_prep'))}"
    launch_prep_json = str(launch_prep.get("launch_prep_json") or artifacts.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(launch_prep.get("launch_prep_sha256") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_GATE_REQUEST.json"
    receipt = {
        "schema": PLAN_GATE_REQUEST_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "capability_id": "dispatch.runtime",
        "mutation_class": "dispatch.runtime",
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "gate_request_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not launch_prep_json:
        receipt.update(
            {
                "status": "error",
                "error": "launch-preparation packet path unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"launch-preparation packet unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_launch_prep_sha256"] = current_sha
    if expected_launch_prep_sha and current_sha != expected_launch_prep_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "launch-preparation packet changed after preparation",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    prep_id = str(output.get("prep_id") or "launch-prep")
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "gate",
            "dispatch.runtime",
            "--project-key",
            project_key,
            "--request-id",
            request_id,
            "--task-id",
            task_id,
            "--mutation-class",
            "dispatch.runtime",
            "--preview",
            f"Prepare dispatch.runtime approval from launch-prep {prep_id}.",
            "--reason",
            "Telegram requested a gate evaluation from a read-only launch-preparation packet; local approval remains required.",
            "--source-surface",
            "telegram.remote_operator",
            "--json",
        ]
    )
    receipt["gate_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        gate_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"gate did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "pending_approval" if gate_output.get("status") == "pending_approval" else str(gate_output.get("status") or "evaluated"),
            "gate_output": gate_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def gate_request_approval(gate_request: dict[str, Any]) -> dict[str, Any]:
    output = gate_request.get("gate_output") if isinstance(gate_request.get("gate_output"), dict) else {}
    approval = output.get("approval") if isinstance(output.get("approval"), dict) else {}
    return approval


def pending_approval_snapshot(
    args: argparse.Namespace,
    *,
    approval_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(["offdesk", "pending", "--json"])
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, sanitize_text(f"{type(error).__name__}: {error}", max_chars=400)
    if process.returncode != 0:
        return None, sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600)
    try:
        approvals = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        return None, sanitize_text(f"pending approvals did not return JSON: {error}", max_chars=400)
    if not isinstance(approvals, list):
        return None, "pending approvals output was not a list"
    for approval in approvals:
        if isinstance(approval, dict) and str(approval.get("approval_id") or "") == approval_id:
            return approval, None
    return None, "pending approval not found"


def approval_matches_gate_request(
    approval: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for key in ("approval_id", "action", "project_key", "request_id", "task_id"):
        if str(approval.get(key) or "") != str(expected.get(key) or ""):
            mismatches.append(f"{key}_mismatch")
    if str(approval.get("status") or "") != "pending":
        mismatches.append("status_not_pending")
    if str(approval.get("source_surface") or "") != "telegram.remote_operator":
        mismatches.append("source_surface_mismatch")
    return mismatches


def resolve_gate_approval(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    gate_request: dict[str, Any],
    approve: bool,
) -> dict[str, Any]:
    expected = gate_request_approval(gate_request)
    approval_id = str(expected.get("approval_id") or "").strip()
    project_key = str(gate_request.get("project_key") or expected.get("project_key") or "project")
    request_id = str(gate_request.get("request_id") or expected.get("request_id") or session.get("session_id") or "")
    task_id = str(gate_request.get("task_id") or expected.get("task_id") or "")
    launch_prep_json = str(gate_request.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(gate_request.get("expected_launch_prep_sha256") or gate_request.get("current_launch_prep_sha256") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_GATE_RESOLUTION.json"
    decision = "approved" if approve else "denied"
    receipt = {
        "schema": PLAN_GATE_RESOLUTION_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "decision": decision,
        "approval_id": approval_id,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "approval_resolution_authorized": True,
        "approval_authorized": bool(approve),
        "gate_approval_authorized": bool(approve),
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not approval_id:
        receipt.update(
            {
                "status": "error",
                "error": "approval id unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if launch_prep_json and expected_launch_prep_sha:
        try:
            current_sha = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
        except OSError as error:
            receipt.update(
                {
                    "status": "error",
                    "error": sanitize_text(f"launch-preparation packet unavailable: {error}", max_chars=400),
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
        receipt["current_launch_prep_sha256"] = current_sha
        if current_sha != expected_launch_prep_sha:
            receipt.update(
                {
                    "status": "stale",
                    "error": "launch-preparation packet changed after gate request",
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
    pending, pending_error = pending_approval_snapshot(args, approval_id=approval_id)
    if pending_error or pending is None:
        receipt.update(
            {
                "status": "stale",
                "error": pending_error or "pending approval unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["pending_approval"] = pending
    mismatches = approval_matches_gate_request(
        pending,
        {
            "approval_id": approval_id,
            "action": "dispatch.runtime",
            "project_key": project_key,
            "request_id": request_id,
            "task_id": task_id,
        },
    )
    if mismatches:
        receipt.update(
            {
                "status": "stale",
                "error": "pending approval no longer matches gate request",
                "mismatches": mismatches,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "ok" if approve else "cancel",
            approval_id,
            "--by",
            "telegram",
            "--json",
        ]
    )
    receipt["resolution_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        resolution_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"approval resolution did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": decision,
            "resolution_output": resolution_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt






def resolve_prepared_task_path(text: str) -> pathlib.Path | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = [raw]
    candidates = tokens or [raw]
    if raw.endswith(".json") and raw not in candidates:
        candidates.append(raw)
    for token in candidates:
        if not token or token.startswith("-"):
            continue
        path = pathlib.Path(token).expanduser()
        if not path.is_absolute():
            path = (pathlib.Path.cwd() / path).resolve()
        if path.exists() and path.is_file() and path.name == "prepared_task.json":
            return path
    return None


























def remote_plan_sessions_by_chat(state: dict[str, Any]) -> dict[str, Any]:
    sessions = state.setdefault("remote_plan_sessions_by_chat", {})
    if not isinstance(sessions, dict):
        sessions = {}
        state["remote_plan_sessions_by_chat"] = sessions
    return sessions


def active_remote_plan_session(state: dict[str, Any], chat_hash: str) -> dict[str, Any] | None:
    session = remote_plan_sessions_by_chat(state).get(str(chat_hash or ""))
    if not isinstance(session, dict):
        return None
    if str(session.get("stage") or "") in {
        "project_selection",
        "project_selected",
        "project_manual_input",
        "project_path_required",
        "project_init_previewed",
        "project_init_created",
        "project_init_failed",
        "plan_draft_validated",
        "plan_draft_failed",
        "plan_registered",
        "plan_registration_failed",
        "plan_review_approved",
        "plan_review_failed",
        "plan_launch_prep_prepared",
        "plan_launch_prep_failed",
        "plan_gate_request_created",
        "plan_gate_request_failed",
        "plan_gate_approved",
        "plan_execution_brief_created",
        "plan_execution_brief_failed",
        "plan_enqueue_handoff_created",
        "plan_enqueue_handoff_failed",
        "plan_workload_path_required",
        "plan_workload_binding_failed",
        "plan_workload_bound",
        "plan_enqueue_run_failed",
        "plan_enqueued",
        "plan_runtime_started",
        "plan_runtime_start_failed",
        "plan_runtime_monitored",
        "plan_runtime_monitor_failed",
        "plan_closeout_packet_created",
        "plan_closeout_packet_failed",
        "plan_closeout_review_handoff_created",
        "plan_closeout_review_handoff_failed",
        "plan_closeout_verdict_recorded",
        "plan_closeout_verdict_failed",
    }:
        return session
    return None


def store_remote_plan_session(state: dict[str, Any], chat_hash: str, session: dict[str, Any]) -> None:
    session["updated_at"] = utc_now()
    remote_plan_sessions_by_chat(state)[str(chat_hash or "")] = session


def remote_plan_defer_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"보류", "취소", "나중에", "hold", "cancel"}


def remote_plan_rescan_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"다시 스캔", "재스캔", "rescan", "scan again"}


def remote_plan_search_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(marker in normalized for marker in ("검색", "찾아", "찾아봐", "search", "scan"))


def remote_plan_reselect_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"다시 선택", "재선택", "reselect", "choose again"}


def remote_plan_init_review_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "초기화 검토",
        "초기화",
        "init review",
        "project init",
        "project init preview",
    }


def remote_plan_action_text(text: str) -> bool:
    return any(
        predicate(text)
        for predicate in (
            remote_plan_defer_text,
            remote_plan_rescan_text,
            remote_plan_reselect_text,
            remote_plan_init_review_text,
            project_init_create_text,
            plan_draft_create_text,
            plan_registration_create_text,
            plan_review_approve_text,
            plan_launch_prep_create_text,
            plan_gate_request_text,
            gate_approval_approve_text,
            gate_approval_deny_text,
            execution_brief_create_text,
            enqueue_handoff_create_text,
            workload_binding_request_text,
            enqueue_run_create_text,
            runtime_start_create_text,
            runtime_monitor_text,
            closeout_packet_create_text,
            closeout_review_handoff_create_text,
        )
    ) or closeout_verdict_request(text)[0] is not None


def candidate_directly_selected(candidate: dict[str, Any], text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    rank = str(candidate.get("rank") or "").strip()
    if rank and re.match(rf"^\s*{re.escape(rank)}\s*(번)?(\s|$)", normalized):
        return True
    values = (
        candidate.get("display_name"),
        candidate.get("project_key"),
        candidate.get("workspace_path_hint"),
    )
    for value in values:
        option = str(value or "").strip().lower()
        if option and normalized in {option, f"{rank} {option}".strip(), f"{rank}번 {option}".strip()}:
            return True
    return False


def remote_plan_project_selection_text(
    args: argparse.Namespace,
    session: dict[str, Any],
    text: str,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if resolve_manual_project_path(args, normalized):
        return True
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    if any(candidate_directly_selected(candidate, normalized) for candidate in candidates):
        return True
    # Allow exact workspace names that are not currently visible in the limited candidate list.
    roots = workspace_roots(args)
    lowered = normalized.lower()
    if len(normalized) <= 80 and not any(marker in lowered for marker in ("?", "？", "뭐", "왜", "설명", "알려")):
        for path in discover_project_paths(roots):
            if lowered == path.name.lower():
                return True
    return False


def remote_plan_session_should_handle_text(
    args: argparse.Namespace,
    session: dict[str, Any],
    text: str,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    stage = str(session.get("stage") or "")
    if remote_plan_action_text(normalized):
        return True
    if stage == "project_selection":
        return remote_plan_project_selection_text(args, session, normalized)
    if stage == "project_path_required":
        return bool(resolve_manual_project_path(args, normalized)) or remote_plan_search_request_text(normalized)
    if stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"}:
        return bool(resolve_prepared_task_path(normalized))
    return False


def append_remote_plan_note(session: dict[str, Any], text: str) -> None:
    notes = session.setdefault("operator_notes", [])
    if not isinstance(notes, list):
        notes = []
        session["operator_notes"] = notes
    notes.append(
        {
            "noted_at": utc_now(),
            "text": sanitize_text(text, max_chars=800),
        }
    )


def resolve_manual_project_path(args: argparse.Namespace, text: str) -> pathlib.Path | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    direct = pathlib.Path(raw).expanduser()
    if direct.is_absolute() and direct.exists() and direct.is_dir():
        return direct.resolve()
    for root in workspace_roots(args):
        candidate = (root / raw).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def candidate_from_manual_path(args: argparse.Namespace, path: pathlib.Path, text: str) -> dict[str, Any]:
    roots = workspace_roots(args)
    return build_project_candidate(
        path,
        roots=roots,
        tokens=request_tokens(text, path.name),
        rank=1,
    )


def candidate_matches_selection(candidate: dict[str, Any], text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    rank = str(candidate.get("rank") or "").strip()
    if rank and re.match(rf"^\s*{re.escape(rank)}\s*(번)?(\s|$)", normalized):
        return True
    for value in (
        candidate.get("display_name"),
        candidate.get("project_key"),
        candidate.get("workspace_path_hint"),
    ):
        option = str(value or "").strip().lower()
        if option and (normalized == option or option in normalized or normalized in option):
            return True
        option_tokens = re.findall(r"[a-z0-9]{3,}|[가-힣]{2,}", option)
        if any(token in normalized for token in option_tokens if len(token) >= 3):
            return True
    return False


def selected_candidate_for_text(session: dict[str, Any], text: str) -> dict[str, Any] | None:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    for candidate in candidates:
        if candidate_matches_selection(candidate, text):
            return candidate
    return None


def workspace_candidate_for_text(
    args: argparse.Namespace,
    *,
    text: str,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    resolved = resolve_manual_project_path(args, text)
    if resolved:
        candidate = candidate_from_manual_path(args, resolved, text)
        candidate["resolved_by"] = "workspace_search"
        return candidate
    candidates = ranked_project_candidates(
        args,
        request_text=" ".join([str(request_text or ""), str(text or "")]),
        agent_intent=agent_intent,
    )
    for candidate in candidates:
        if candidate_matches_selection(candidate, text):
            candidate["resolved_by"] = "workspace_search"
            return candidate
    return None


def handle_remote_plan_session_input(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    chat_hash: str,
    session: dict[str, Any],
    text: str,
    mode: str,
) -> dict[str, Any]:
    result = result_base(args, config, mode)
    result["command_text"] = sanitize_text(text, max_chars=400)
    normalized = str(text or "").strip()
    parsed = {
        "supported": True,
        "command": "remote_plan_selection",
        "argv": [],
        "command_text": normalized,
        "session_id": session.get("session_id"),
    }
    stage = str(session.get("stage") or "")
    if remote_plan_defer_text(normalized):
        session["stage"] = "deferred"
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "deferred"}
        message_preview = render_project_selection_deferred_message(profile=args.profile)
        attach_choice_surface(result, None)
    elif remote_plan_rescan_text(normalized):
        agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
        session["candidates"] = scan_project_candidates(
            args,
            request_text=str(session.get("request_text") or ""),
            agent_intent=agent_intent,
        )
        session["stage"] = "project_selection"
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "rescanned"}
        message_preview = render_project_selection_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_selection_context(session))
    elif remote_plan_reselect_text(normalized):
        session["stage"] = "project_selection"
        session.pop("selected_candidate", None)
        session.pop("project_init_preview", None)
        session.pop("project_init_run", None)
        session.pop("plan_draft", None)
        session.pop("plan_registration", None)
        session.pop("plan_review", None)
        session.pop("plan_launch_prep", None)
        session.pop("plan_gate_request", None)
        session.pop("plan_gate_resolution", None)
        session.pop("plan_execution_brief", None)
        session.pop("plan_enqueue_handoff", None)
        session.pop("plan_workload_binding", None)
        session.pop("plan_enqueue_run", None)
        session.pop("plan_runtime_start", None)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "reselect"}
        message_preview = render_project_selection_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_selection_context(session))
    elif stage == "project_selection":
        candidate = selected_candidate_for_text(session, normalized)
        if not candidate:
            agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
            candidate = workspace_candidate_for_text(
                args,
                text=normalized,
                request_text=str(session.get("request_text") or ""),
                agent_intent=agent_intent,
            )
        if candidate:
            session["stage"] = "project_selected"
            session["selected_candidate"] = candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "selected_by_search"
                if candidate.get("resolved_by") == "workspace_search"
                else "selected",
                "selected_project_key": candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            manual_candidate = manual_project_candidate(normalized)
            session["stage"] = "project_manual_input"
            session["selected_candidate"] = manual_candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "manual_input",
                "selected_project_key": manual_candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage == "project_path_required":
        resolved = resolve_manual_project_path(args, normalized)
        if resolved:
            candidate = candidate_from_manual_path(args, resolved, normalized)
            session["stage"] = "project_selected"
            session["selected_candidate"] = candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "path_confirmed",
                "selected_project_key": candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        elif remote_plan_search_request_text(normalized):
            selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
            agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
            search_text = " ".join(
                [
                    str(selected.get("display_name") or selected.get("project_key") or ""),
                    str(session.get("request_text") or ""),
                ]
            )
            candidate = workspace_candidate_for_text(
                args,
                text=search_text,
                request_text=str(session.get("request_text") or ""),
                agent_intent=agent_intent,
            )
            if candidate:
                session["stage"] = "project_selected"
                session["selected_candidate"] = candidate
                store_remote_plan_session(state, chat_hash, session)
                result["parsed_command"] = {
                    **parsed,
                    "selection_status": "path_resolved_by_search",
                    "selected_project_key": candidate.get("project_key"),
                }
                message_preview = render_project_selected_message(profile=args.profile, session=session)
                attach_choice_surface(result, remote_plan_init_context(session))
            else:
                append_remote_plan_note(session, normalized)
                store_remote_plan_session(state, chat_hash, session)
                result["parsed_command"] = {**parsed, "selection_status": "path_unresolved"}
                message_preview = render_project_path_required_message(profile=args.profile, session=session)
                attach_choice_surface(result, remote_plan_init_context(session))
        else:
            append_remote_plan_note(session, normalized)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "path_unresolved"}
            message_preview = render_project_path_required_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif project_init_create_text(normalized):
        selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
        if stage != "project_init_previewed" or not isinstance(session.get("project_init_preview"), dict):
            result["parsed_command"] = {**parsed, "selection_status": "preview_required"}
            message_preview = render_project_init_preview_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            run = run_project_init_packet(args, session=session, candidate=selected)
            session["project_init_run"] = run
            if run.get("status") == "created":
                session["stage"] = "project_init_created"
                message_preview = render_project_init_created_message(profile=args.profile, session=session)
                selection_status = "init_created"
            else:
                session["stage"] = "project_init_failed"
                message_preview = render_project_init_failed_message(profile=args.profile, session=session)
                selection_status = "init_failed"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": selected.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif plan_launch_prep_create_text(normalized):
        review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
        if stage not in {"plan_review_approved", "plan_launch_prep_failed"} or review.get("status") != "approved":
            result["parsed_command"] = {**parsed, "selection_status": "plan_review_required"}
            message_preview = render_plan_launch_prep_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            launch_prep = prepare_plan_launch_packet(args, session=session, review=review)
            session["plan_launch_prep"] = launch_prep
            if launch_prep.get("status") == "prepared":
                session["stage"] = "plan_launch_prep_prepared"
                message_preview = render_plan_launch_prep_prepared_message(profile=args.profile, session=session)
                selection_status = "plan_launch_prep_prepared"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_launch_prep_failed"
                message_preview = render_plan_launch_prep_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_launch_prep_stale" if launch_prep.get("status") == "stale" else "plan_launch_prep_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": launch_prep.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif execution_brief_create_text(normalized):
        gate_resolution = (
            session.get("plan_gate_resolution")
            if isinstance(session.get("plan_gate_resolution"), dict)
            else {}
        )
        if stage not in {"plan_gate_approved", "plan_execution_brief_failed"} or gate_resolution.get("status") != "approved":
            result["parsed_command"] = {**parsed, "selection_status": "gate_approval_required"}
            message_preview = render_plan_execution_brief_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            brief = create_execution_brief_from_gate_resolution(
                args,
                session=session,
                gate_resolution=gate_resolution,
            )
            session["plan_execution_brief"] = brief
            if brief.get("status") == "created":
                session["stage"] = "plan_execution_brief_created"
                message_preview = render_plan_execution_brief_created_message(profile=args.profile, session=session)
                selection_status = "plan_execution_brief_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_execution_brief_failed"
                message_preview = render_plan_execution_brief_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_execution_brief_stale"
                    if brief.get("status") == "stale"
                    else "plan_execution_brief_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": brief.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif enqueue_handoff_create_text(normalized):
        execution_brief = (
            session.get("plan_execution_brief")
            if isinstance(session.get("plan_execution_brief"), dict)
            else {}
        )
        if stage not in {"plan_execution_brief_created", "plan_enqueue_handoff_failed"} or execution_brief.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "execution_brief_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            handoff = create_enqueue_handoff_from_execution_brief(
                args,
                session=session,
                execution_brief_receipt=execution_brief,
            )
            session["plan_enqueue_handoff"] = handoff
            if handoff.get("status") == "created":
                session["stage"] = "plan_enqueue_handoff_created"
                message_preview = render_plan_enqueue_handoff_created_message(profile=args.profile, session=session)
                selection_status = "plan_enqueue_handoff_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_enqueue_handoff_failed"
                message_preview = render_plan_enqueue_handoff_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_enqueue_handoff_stale"
                    if handoff.get("status") == "stale"
                    else "plan_enqueue_handoff_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": handoff.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif enqueue_run_create_text(normalized):
        workload_binding = (
            session.get("plan_workload_binding")
            if isinstance(session.get("plan_workload_binding"), dict)
            else {}
        )
        if stage not in {"plan_workload_bound", "plan_enqueue_run_failed"} or workload_binding.get("status") != "bound":
            result["parsed_command"] = {**parsed, "selection_status": "workload_binding_required"}
            message_preview = render_plan_enqueue_run_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            enqueue_run = create_enqueue_run_from_workload_binding(
                args,
                session=session,
                workload_binding=workload_binding,
            )
            session["plan_enqueue_run"] = enqueue_run
            if enqueue_run.get("status") == "queued":
                session["stage"] = "plan_enqueued"
                message_preview = render_plan_enqueue_run_done_message(profile=args.profile, session=session)
                selection_status = "plan_enqueued"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_enqueue_run_failed"
                message_preview = render_plan_enqueue_run_failed_message(profile=args.profile, session=session)
                selection_status = "plan_enqueue_run_blocked" if enqueue_run.get("status") == "blocked" else "plan_enqueue_run_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": enqueue_run.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif runtime_start_create_text(normalized):
        enqueue_run = (
            session.get("plan_enqueue_run")
            if isinstance(session.get("plan_enqueue_run"), dict)
            else {}
        )
        if stage not in {"plan_enqueued", "plan_runtime_start_failed"} or enqueue_run.get("status") != "queued":
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_run_required"}
            message_preview = render_plan_runtime_start_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            runtime_start = create_runtime_start_from_enqueue_run(
                args,
                session=session,
                enqueue_run=enqueue_run,
            )
            session["plan_runtime_start"] = runtime_start
            if runtime_start.get("status") == "launched":
                session["stage"] = "plan_runtime_started"
                message_preview = render_plan_runtime_started_message(profile=args.profile, session=session)
                selection_status = "plan_runtime_started"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_runtime_start_failed"
                message_preview = render_plan_runtime_start_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_runtime_start_blocked"
                    if runtime_start.get("status") in {"blocked", "not_started"}
                    else "plan_runtime_start_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": runtime_start.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif runtime_monitor_text(normalized):
        runtime_start = (
            session.get("plan_runtime_start")
            if isinstance(session.get("plan_runtime_start"), dict)
            else {}
        )
        if stage not in {"plan_runtime_started", "plan_runtime_monitored", "plan_runtime_monitor_failed", "plan_closeout_packet_created"} or runtime_start.get("status") != "launched":
            result["parsed_command"] = {**parsed, "selection_status": "runtime_start_required"}
            message_preview = render_plan_runtime_monitor_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            runtime_monitor = create_runtime_monitor_from_runtime_start(
                args,
                session=session,
                runtime_start=runtime_start,
            )
            session["plan_runtime_monitor"] = runtime_monitor
            if runtime_monitor.get("status") in {"running", "completed", "failed", "resume_pending", "observed"}:
                session["stage"] = "plan_runtime_monitored"
                message_preview = render_plan_runtime_monitor_message(profile=args.profile, session=session)
                selection_status = "plan_runtime_monitored"
            else:
                session["stage"] = "plan_runtime_monitor_failed"
                message_preview = render_plan_runtime_monitor_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_runtime_monitor_blocked"
                    if runtime_monitor.get("status") == "blocked"
                    else "plan_runtime_monitor_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": runtime_monitor.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_packet_create_text(normalized):
        runtime_monitor = (
            session.get("plan_runtime_monitor")
            if isinstance(session.get("plan_runtime_monitor"), dict)
            else {}
        )
        if stage not in {"plan_runtime_monitored", "plan_closeout_packet_failed"} or runtime_monitor.get("task_status") != "completed":
            result["parsed_command"] = {**parsed, "selection_status": "runtime_completion_required"}
            message_preview = render_plan_closeout_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            closeout_packet = create_closeout_packet_from_runtime_monitor(
                args,
                session=session,
                runtime_monitor=runtime_monitor,
            )
            session["plan_closeout_packet"] = closeout_packet
            if closeout_packet.get("status") == "created":
                session["stage"] = "plan_closeout_packet_created"
                message_preview = render_plan_closeout_packet_message(profile=args.profile, session=session)
                selection_status = "plan_closeout_packet_created"
            else:
                session["stage"] = "plan_closeout_packet_failed"
                message_preview = render_plan_closeout_packet_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_closeout_packet_blocked"
                    if closeout_packet.get("status") == "blocked"
                    else "plan_closeout_packet_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": closeout_packet.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_review_handoff_create_text(normalized):
        closeout_packet = (
            session.get("plan_closeout_packet")
            if isinstance(session.get("plan_closeout_packet"), dict)
            else {}
        )
        if stage not in {"plan_closeout_packet_created", "plan_closeout_review_handoff_failed"} or closeout_packet.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "closeout_packet_required"}
            message_preview = render_plan_closeout_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            handoff = create_closeout_review_handoff_from_packet(
                args,
                session=session,
                closeout_packet=closeout_packet,
            )
            session["plan_closeout_review_handoff"] = handoff
            if handoff.get("status") == "created":
                session["stage"] = "plan_closeout_review_handoff_created"
                message_preview = render_plan_closeout_review_handoff_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = "plan_closeout_review_handoff_created"
            else:
                session["stage"] = "plan_closeout_review_handoff_failed"
                message_preview = render_plan_closeout_review_handoff_failed_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = (
                    "plan_closeout_review_handoff_blocked"
                    if handoff.get("status") == "blocked"
                    else "plan_closeout_review_handoff_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": handoff.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_verdict_request(normalized)[0] is not None:
        requested_verdict, requested_note = closeout_verdict_request(normalized)
        handoff = (
            session.get("plan_closeout_review_handoff")
            if isinstance(session.get("plan_closeout_review_handoff"), dict)
            else {}
        )
        if stage not in {"plan_closeout_review_handoff_created", "plan_closeout_verdict_failed"} or handoff.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "closeout_review_handoff_required"}
            message_preview = render_plan_closeout_review_handoff_failed_message(
                profile=args.profile,
                session={
                    **session,
                    "plan_closeout_review_handoff": {
                        "error": "마무리 검토 준비가 먼저 필요합니다.",
                    },
                },
            )
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            closeout_verdict = create_closeout_verdict_from_handoff(
                args,
                session=session,
                handoff=handoff,
                verdict=str(requested_verdict or ""),
                note=requested_note,
            )
            session["plan_closeout_verdict"] = closeout_verdict
            if closeout_verdict.get("status") == "recorded":
                session["stage"] = "plan_closeout_verdict_recorded"
                message_preview = render_plan_closeout_verdict_message(profile=args.profile, session=session)
                selection_status = "plan_closeout_verdict_recorded"
            else:
                session["stage"] = "plan_closeout_verdict_failed"
                message_preview = render_plan_closeout_verdict_failed_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = (
                    "plan_closeout_verdict_blocked"
                    if closeout_verdict.get("status") == "blocked"
                    else "plan_closeout_verdict_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": closeout_verdict.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif workload_binding_request_text(normalized):
        handoff = (
            session.get("plan_enqueue_handoff")
            if isinstance(session.get("plan_enqueue_handoff"), dict)
            else {}
        )
        if stage not in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"} or handoff.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_handoff_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            session["stage"] = "plan_workload_path_required"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "workload_path_required"}
            message_preview = render_plan_workload_path_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"} and resolve_prepared_task_path(normalized):
        handoff = (
            session.get("plan_enqueue_handoff")
            if isinstance(session.get("plan_enqueue_handoff"), dict)
            else {}
        )
        manifest_path = resolve_prepared_task_path(normalized)
        if handoff.get("status") != "created" or manifest_path is None:
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_handoff_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            binding = bind_prepared_workload_to_execution_brief(
                args,
                session=session,
                enqueue_handoff=handoff,
                manifest_path=manifest_path,
            )
            session["plan_workload_binding"] = binding
            if binding.get("status") == "bound":
                session["stage"] = "plan_workload_bound"
                message_preview = render_plan_workload_bound_message(profile=args.profile, session=session)
                selection_status = "plan_workload_bound"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_workload_binding_failed"
                message_preview = render_plan_workload_binding_failed_message(profile=args.profile, session=session)
                selection_status = "plan_workload_binding_blocked" if binding.get("status") == "blocked" else "plan_workload_binding_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": binding.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif gate_approval_approve_text(normalized) or gate_approval_deny_text(normalized):
        gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
        approve = gate_approval_approve_text(normalized)
        if stage != "plan_gate_request_created" or gate_request.get("status") != "pending_approval":
            result["parsed_command"] = {**parsed, "selection_status": "gate_request_required"}
            message_preview = render_plan_gate_resolution_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            resolution = resolve_gate_approval(
                args,
                session=session,
                gate_request=gate_request,
                approve=approve,
            )
            session["plan_gate_resolution"] = resolution
            if resolution.get("status") in {"approved", "denied"}:
                session["stage"] = "plan_gate_approved" if resolution.get("status") == "approved" else "plan_gate_denied"
                message_preview = render_plan_gate_resolution_done_message(profile=args.profile, session=session)
                selection_status = f"plan_gate_{resolution.get('status')}"
                choice_context = remote_plan_init_context(session) if resolution.get("status") == "approved" else None
            else:
                session["stage"] = "plan_gate_resolution_failed"
                message_preview = render_plan_gate_resolution_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_gate_resolution_stale"
                    if resolution.get("status") == "stale"
                    else "plan_gate_resolution_failed"
                )
                choice_context = None
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": resolution.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_gate_request_text(normalized):
        launch_prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
        if stage not in {"plan_launch_prep_prepared", "plan_gate_request_failed"} or launch_prep.get("status") != "prepared":
            result["parsed_command"] = {**parsed, "selection_status": "launch_prep_required"}
            message_preview = render_plan_gate_request_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            gate_request = request_gate_for_launch_prep(args, session=session, launch_prep=launch_prep)
            session["plan_gate_request"] = gate_request
            if gate_request.get("status") == "pending_approval":
                session["stage"] = "plan_gate_request_created"
                message_preview = render_plan_gate_request_created_message(profile=args.profile, session=session)
                selection_status = "plan_gate_request_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_gate_request_failed"
                message_preview = render_plan_gate_request_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_gate_request_stale" if gate_request.get("status") == "stale" else "plan_gate_request_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": gate_request.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_review_approve_text(normalized):
        registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
        if stage not in {"plan_registered", "plan_review_failed"} or registration.get("status") != "registered":
            result["parsed_command"] = {**parsed, "selection_status": "plan_registration_required"}
            message_preview = render_plan_review_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            review = approve_registered_plan(args, session=session, registration=registration)
            session["plan_review"] = review
            if review.get("status") == "approved":
                session["stage"] = "plan_review_approved"
                message_preview = render_plan_review_approved_message(profile=args.profile, session=session)
                selection_status = "plan_review_approved"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_review_failed"
                message_preview = render_plan_review_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_review_stale" if review.get("status") == "stale" else "plan_review_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": review.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_registration_create_text(normalized):
        draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
        if stage != "plan_draft_validated" or draft.get("status") != "validated":
            result["parsed_command"] = {**parsed, "selection_status": "plan_draft_required"}
            message_preview = render_plan_registration_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            registration = register_plan_draft(args, session=session, draft=draft)
            session["plan_registration"] = registration
            if registration.get("status") == "registered":
                session["stage"] = "plan_registered"
                message_preview = render_plan_registered_message(profile=args.profile, session=session)
                selection_status = "plan_registered"
                choice_context = remote_plan_init_context(session)
            elif registration.get("status") == "stale":
                session["stage"] = "plan_registration_failed"
                message_preview = render_plan_registration_stale_message(profile=args.profile)
                selection_status = "plan_registration_stale"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_registration_failed"
                message_preview = render_plan_registration_failed_message(profile=args.profile, session=session)
                selection_status = "plan_registration_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": registration.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_draft_create_text(normalized):
        init_run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
        if init_run.get("status") != "created" or not isinstance(init_run.get("project_init_output"), dict):
            result["parsed_command"] = {**parsed, "selection_status": "init_required"}
            message_preview = render_plan_draft_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            draft = create_plan_draft(args, session=session, init_run=init_run)
            session["plan_draft"] = draft
            if draft.get("status") == "validated":
                session["stage"] = "plan_draft_validated"
                message_preview = render_plan_draft_validated_message(profile=args.profile, session=session)
                selection_status = "plan_draft_validated"
            else:
                session["stage"] = "plan_draft_failed"
                message_preview = render_plan_draft_failed_message(profile=args.profile, session=session)
                selection_status = "plan_draft_failed"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": init_run.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif remote_plan_init_review_text(normalized):
        selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
        if selected.get("manual_input") and not selected.get("workspace_path"):
            session["stage"] = "project_path_required"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "path_required"}
            message_preview = render_project_path_required_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            preview = create_project_init_preview(args, session=session, candidate=selected)
            session["stage"] = "project_init_previewed"
            session["project_init_preview"] = preview
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "init_previewed",
                "selected_project_key": selected.get("project_key"),
            }
            message_preview = render_project_init_preview_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage == "plan_workload_path_required":
        append_remote_plan_note(session, normalized)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "workload_path_unresolved"}
        message_preview = render_plan_workload_path_required_message(profile=args.profile)
        attach_choice_surface(result, remote_plan_init_context(session))
    else:
        append_remote_plan_note(session, normalized)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "note_added"}
        message_preview = render_remote_plan_note_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_init_context(session))
    result.update(
        {
            "status": "rendered",
            "projection": None,
            "remote_plan_session": public_remote_plan_session(session),
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result


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


def result_base(args: argparse.Namespace, config: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "generated_at": utc_now(),
        "mode": mode,
        "profile": args.profile,
        "target_chat_id_hash": config.get("target_chat_id_hash"),
        "chat_allowlist_configured": bool(config.get("chat_allowlist_configured")),
        "user_allowlist_configured": bool(config.get("user_allowlist_configured")),
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
        "forbidden_remote_intents": list(FORBIDDEN_REMOTE_INTENTS),
    }


def attach_choice_surface(result: dict[str, Any], context: dict[str, Any] | None) -> None:
    reply_markup = choice_keyboard(context)
    result["reply_markup_preview"] = reply_markup
    result["choice_surface_contract"] = choice_surface_contract(reply_markup, context)
    if isinstance(context, dict):
        result["interaction_context"] = context


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
            except (OSError, RemoteOperatorTelegramError) as error:
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
        except (OSError, RemoteOperatorTelegramError) as error:
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
