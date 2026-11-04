#!/usr/bin/env python3
"""Telegram adapter for guarded Forager Remote Operator projections.

This adapter is intentionally narrow. It maps a small Telegram command surface
to read-only projections, remote Plan Mode receipts, exact gate resolution,
reviewed enqueue, and task-scoped runtime start/monitor receipts. It never
executes operator-supplied shell text directly in the adapter, starts unbound
work, closes out completed work, accepts runtime output as truth, or mutates
project files directly. Opt-in runtime dispatch queues receipted work for the
Offdesk tick launcher.
"""

from __future__ import annotations

import argparse
import copy
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
    SESSION_MESSAGE_MIN_CONFIDENCE,
    chat_with_agent,
    classify_feedback_kind,
    classify_feedback_with_agent,
    looks_like_session_relay_request,
    validate_session_message_intent,
)
from telegram_operator.common import (
    RemoteOperatorTelegramError,
    append_jsonl,
    load_json,
    sha256_short,
    utc_now,
    write_json,
)
from telegram_operator.behavior_core import (
    InboundEvent,
    finalize_action_outcome,
    finalize_response_card,
    route_inbound_event,
    validate_action_candidate,
)
from telegram_operator.command_envelope import build_command_envelope
from telegram_operator.allowlist import (
    dispatch_templates,
    find_dispatch_template,
    load_dispatch_allowlist,
)
from telegram_operator.config import (
    TelegramControlRevokedError,
    assert_telegram_config_current,
    default_telegram_env_file,
    public_control_generation_id,
    resolve_telegram_config,
)
from telegram_operator.health import listener_health
from telegram_operator.redaction import public_remote_plan_session
from telegram_operator.base import attach_choice_surface, result_base
from telegram_operator.plan_workflow import (
    active_remote_plan_session,
    handle_remote_plan_session_input,
    remote_plan_init_context,
    remote_plan_selection_context,
    remote_plan_session_should_handle_text,
)
from telegram_operator.schemas import (
    FORBIDDEN_REMOTE_INTENTS,
    INTERACTION_CONTEXT_SCHEMA,
    RESULT_SCHEMA,
)
from telegram_operator.plan_messages import render_project_selection_message
from telegram_operator.dispatch import (
    apply_cancel_task,
    apply_terminate_task,
    apply_session_input,
    apply_session_text_input,
    apply_decision_action,
    apply_operator_pause,
    apply_operator_resume,
    apply_recovery_action,
    apply_runtime_dispatch,
    available_action_kinds,
    available_recovery_action_kinds,
    build_confirmation,
    cancellable_tasks_from_surface,
    clear_confirmation,
    classify_session_interaction,
    confirmation_is_fresh,
    export_workstation_surface,
    find_action_envelope,
    find_recovery_envelope,
    open_decision_actions,
    open_recovery_actions,
    open_runtime_dispatch,
    pop_confirmation,
    runtime_dispatch_item,
    run_forager_json,
    store_confirmation,
)
from telegram_operator.effects import annotate_result_effect
from telegram_operator.feedback_workflow import (
    FeedbackWorkflowOutcome,
    process_feedback_workflow,
)
from telegram_operator.reply_outbox import (
    compact_reply_outbox,
    latest_delivery_records,
    mark_delivery_delivered,
    mark_delivery_failed,
    mark_delivery_started,
    mark_delivery_unconfirmed,
    outbox_inspection,
    pending_deliveries,
    queue_reply,
    revoke_mismatched_deliveries,
)
from telegram_operator.remote_session_identity import (
    identity_issue as remote_session_identity_issue,
)
from telegram_operator.update_journal import (
    begin_update,
    compact_update_journal,
    complete_update,
    journal_inspection,
    latest_update_records,
    mark_effect_committed,
    reconcile_update,
)
from telegram_operator.receipt_inspector import inspect_remote_receipts
from telegram_operator.notifier import (
    attention_summary,
    build_attention_notification,
    commit_attention_notification,
    render_attention_message,
)
from telegram_operator.notification_policy import (
    NotificationCandidate,
    NotificationCycle,
    NotificationPriority,
)
from telegram_operator.session_lifecycle import (
    mark_stop_alert,
    observe_session_lifecycle,
)
from telegram_operator.project_candidates import (
    discover_project_paths,
    relative_path_hint,
    workspace_roots,
)
from telegram_operator.projects import (
    build_project_focus,
    build_project_portfolio_summary,
    build_wiki_plane_summary,
    build_wiki_portfolio_summary,
    find_project_dir,
    inspect_project_workspace,
    inspection_summary_lines,
    list_project_dir,
    load_registry,
    project_mention_matches,
    project_selector_matches,
    read_project_file,
    registry_summary,
    resolve_chat_focus,
)
from telegram_operator.persistence import (
    RemoteOperatorStateError,
    append_chat_history,
    bind_control_generation,
    chat_history_for_chat_hash,
    clear_session_focus_for_chat_hash,
    last_context_for_chat_hash,
    load_state,
    project_focus_for_chat_hash,
    remember_session_focus,
    remember_context_for_chat_hash,
    save_state,
    session_focus_for_chat_hash,
)
from telegram_operator.rendering import (
    choice_keyboard,
    choice_surface_contract,
    render_decisions_message,
    render_dispatch_cancel_message,
    render_command_envelope_refusal_message,
    render_receipt_inspection_message,
    render_dispatch_confirm_message,
    render_dispatch_error_message,
    render_dispatch_result_message,
    render_attention_summary_message,
    render_cancel_task_confirm_message,
    render_cancel_task_result_message,
    render_terminate_task_confirm_message,
    render_terminate_task_result_message,
    render_pause_result_message,
    render_recovery_confirm_message,
    render_recovery_message,
    render_recovery_result_message,
    render_runtime_confirm_message,
    render_runtime_disabled_message,
    render_runtime_message,
    render_runtime_result_message,
    render_run_confirm_message,
    render_run_disabled_message,
    render_run_list_message,
    render_resume_confirm_message,
    render_resume_result_message,
    render_tasks_message,
    display_action,
    display_review_status,
    guide_message,
    help_message,
    mobile_card_contract,
    GUIDE_SHEET_MAX_CHARS,
    GUIDE_SHEET_MAX_LINES,
    number,
    profile_label_from_projection,
    projection_payload,
    render_chat_message,
    render_feedback_message,
    render_project_portfolio_message,
    render_wiki_candidate_message,
    render_wiki_summary_message,
    render_projection_message,
    sanitize_text,
    status_summary,
)
from telegram_operator.routing import (
    parse_remote_command,
)
from telegram_operator.transport import (
    TelegramDeliveryUnconfirmed,
    answer_callback_query,
    clear_inline_keyboard,
    edit_message,
    get_updates,
    send_message,
)
from telegram_operator.wiki import record_remember_candidate


DEFAULT_TELEGRAM_ENV_FILE = default_telegram_env_file()
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
DEFAULT_CONVERSATION_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_CONVERSATION",
        str(
            pathlib.Path.home()
            / ".cache"
            / "forager"
            / "remote_operator_telegram_conversation.jsonl"
        ),
    )
)
DEFAULT_UPDATE_JOURNAL_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_UPDATE_JOURNAL",
        str(
            pathlib.Path.home()
            / ".cache"
            / "forager"
            / "remote_operator_telegram_updates.jsonl"
        ),
    )
)
DEFAULT_REPLY_OUTBOX_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_REPLY_OUTBOX",
        str(
            pathlib.Path.home()
            / ".cache"
            / "forager"
            / "remote_operator_telegram_reply_outbox.jsonl"
        ),
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
DEFAULT_LISTENER_LOCK_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_LISTENER_LOCK",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_listener.lock"),
    )
)
# Off by default: curated /run is opt-in, the operator must point this at a file
# of vetted command templates. Kept distinct from --enable-runtime-dispatch so a
# setup can allow named commands without allowing free-form ones.
_DISPATCH_ALLOWLIST_ENV = os.environ.get("OFFDESK_REMOTE_OPERATOR_DISPATCH_ALLOWLIST", "").strip()
DEFAULT_DISPATCH_ALLOWLIST_FILE = pathlib.Path(_DISPATCH_ALLOWLIST_ENV) if _DISPATCH_ALLOWLIST_ENV else None
SESSION_CONTINUE_MESSAGE = (
    "현재 계획과 마지막 응답을 기준으로 다음 미완료 작업을 바로 진행해. "
    "다음 작업이 명확하지 않거나 새 권한이 필요하면 실행하지 말고 질문해."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    parser.add_argument("--forager-bin", default=os.environ.get("FORAGER_BIN", "forager"))
    parser.add_argument("--env-file", type=pathlib.Path, default=DEFAULT_TELEGRAM_ENV_FILE)
    parser.add_argument("--state-file", type=pathlib.Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--feedback-file", type=pathlib.Path, default=DEFAULT_FEEDBACK_FILE)
    parser.add_argument(
        "--conversation-file", type=pathlib.Path, default=DEFAULT_CONVERSATION_FILE
    )
    parser.add_argument(
        "--update-journal-file",
        type=pathlib.Path,
        default=DEFAULT_UPDATE_JOURNAL_FILE,
        help="Durable inbound-update journal used to prevent replayed mutations.",
    )
    parser.add_argument(
        "--reply-outbox-file",
        type=pathlib.Path,
        default=DEFAULT_REPLY_OUTBOX_FILE,
        help="Durable Telegram reply outbox for committed inbound updates.",
    )
    parser.add_argument(
        "--journal-inspect",
        action="store_true",
        help="Inspect durable update and reply-delivery state, then exit.",
    )
    parser.add_argument(
        "--receipt-inspect",
        action="store_true",
        help="Inspect one remote Plan receipt chain without changing state.",
    )
    parser.add_argument(
        "--receipt-session-id",
        help="Plan session selected by --receipt-inspect; defaults to the latest artifact session.",
    )
    parser.add_argument(
        "--journal-reconcile",
        choices=("retry", "complete"),
        help="Resolve one ambiguous started update after local evidence review.",
    )
    parser.add_argument(
        "--journal-compact",
        action="store_true",
        help="Remove completed journal/outbox history older than the trusted listener offset.",
    )
    parser.add_argument(
        "--journal-update-id",
        type=int,
        help="Telegram update id selected by journal tools or --receipt-inspect.",
    )
    parser.add_argument(
        "--journal-reason",
        help="Required operator reason for --journal-reconcile.",
    )
    parser.add_argument("--feedback-ingest-dir", type=pathlib.Path, default=DEFAULT_FEEDBACK_INGEST_DIR)
    parser.add_argument("--remote-plan-artifact-dir", type=pathlib.Path, default=DEFAULT_REMOTE_PLAN_ARTIFACT_DIR)
    parser.add_argument("--loop-status-file", type=pathlib.Path, default=DEFAULT_LOOP_STATUS_FILE)
    parser.add_argument(
        "--listener-lock-file",
        type=pathlib.Path,
        default=DEFAULT_LISTENER_LOCK_FILE,
        help="Local lock that prevents two continuous Telegram pollers from running.",
    )
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
    parser.add_argument(
        "--shift-review-id",
        help="Exact bounded shift used only to ground a read-only review projection.",
    )
    parser.add_argument(
        "--shift-review-current-run-id",
        help="Exact reserved routine currently producing the read-only review projection.",
    )
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
        "--project-focus-max-age-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_PROJECT_FOCUS_MAX_AGE_SEC", "86400")),
        help="Maximum age for a sticky chat project. Negative disables expiry.",
    )
    parser.add_argument(
        "--dispatch-confirm-ttl-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_DISPATCH_CONFIRM_TTL_SEC", "300")),
        help="Lifetime of a pending Telegram execution confirmation token.",
    )
    parser.add_argument(
        "--remote-command-ttl-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_COMMAND_TTL_SEC", "300")),
        help="Maximum age of a Telegram command that can reach a state-changing handler.",
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
        "--dispatch-allowlist-file",
        type=pathlib.Path,
        default=DEFAULT_DISPATCH_ALLOWLIST_FILE,
        help="Path to a JSON file of curated /run command templates ({\"templates\": "
        "[{\"name\", \"runner\", \"command\"}]}). When set, /run lets the operator dispatch "
        "a named, pre-vetted command without --enable-runtime-dispatch; the operator names a "
        "template and never supplies the command. The command is re-resolved from this file at "
        "confirm time, so removing a template revokes it even for an outstanding confirmation.",
    )
    parser.add_argument(
        "--attention-notify",
        action="store_true",
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_ATTENTION_NOTIFY", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="On each poll, proactively send the owner chat a card for newly waiting decisions "
        "and recovery follow-ups, so urgent items can be handled without polling first.",
    )
    parser.add_argument(
        "--autonomy-propose",
        action="store_true",
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_AUTONOMY_PROPOSE", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="When the workstation goes idle, propose arming the latest reviewed bounded shift "
        "via a Telegram confirm card. The operator's tap resolves only that exact manifest.",
    )
    parser.add_argument("--autonomy-idle-min", type=int, default=30,
                        help="Idle minutes across watch paths before proposing.")
    parser.add_argument("--autonomy-propose-from-hour", type=int, default=17,
                        help="Do not propose before this local hour (avoids lunch-break proposals).")
    parser.add_argument("--autonomy-backoff-sec", type=int, default=7200,
                        help="Minimum seconds between proposals (unanswered or declined).")
    parser.add_argument("--autonomy-watch-path", action="append", type=pathlib.Path, default=[],
                        help="Activity probe path (repeatable). Defaults to Claude/Codex session dirs.")
    parser.add_argument(
        "--session-notify",
        action="store_true",
        default=os.environ.get("OFFDESK_REMOTE_OPERATOR_SESSION_NOTIFY", "") == "1",
        help="Push a card when a supervised agent session sits in the waiting state "
        "(permission prompt / question) so the operator hears about it away from the desk.",
    )
    parser.add_argument("--session-notify-backoff-sec", type=int, default=1800,
                        help="Base seconds between cards for the same session. Each repeat for "
                        "one continuously-waiting prompt doubles the wait.")
    parser.add_argument("--session-notify-max-backoff-sec", type=int, default=14400,
                        help="Ceiling for the doubling reminder interval.")
    parser.add_argument("--session-notify-max-cards", type=int, default=4,
                        help="Stop re-notifying a single continuously-waiting prompt after this "
                        "many cards. A prompt the operator has not cleared in four tries is not "
                        "going to be cleared by a fifth card. 0 disables the cap.")
    parser.add_argument("--session-notify-quiet-from-hour", type=int, default=23,
                        help="Local hour when session cards stop being pushed. Sessions still "
                        "waiting when the window ends are carded then. Equal from/to disables.")
    parser.add_argument("--session-notify-quiet-to-hour", type=int, default=8,
                        help="Local hour when session cards resume.")
    parser.add_argument("--session-status-file", type=pathlib.Path, default=None,
                        help="Read session status JSON from this file instead of running "
                        "`forager status --json` (replay/testing).")
    parser.add_argument(
        "--attention-reminder-sec",
        type=int,
        # A decision nobody answers is the one worth repeating. Announcing it
        # once meant a real item sat unread for two months while a misdetected
        # session was re-carded every 30 minutes.
        default=int(os.environ.get("OFFDESK_REMOTE_OPERATOR_ATTENTION_REMINDER_SEC", "21600")),
        help="Re-notify a still-waiting attention item after this many seconds. 0 notifies once.",
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
    parser.add_argument(
        "--reply-outbox-retry-limit",
        type=int,
        default=10,
        help="Maximum pending replies retried before one inbound poll.",
    )
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






def decision_action_choice_context(decisions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One-tap action buttons for the most urgent open decision.

    Each button is a complete `/decision <id> <action>` command, so tapping it
    dispatches that action (through the usual confirm step) without typing the
    id or action. Scoped to the top open decision and its first few actions to
    keep the mobile card tight; other decisions stay reachable via the text hint.
    """

    for decision in decisions:
        actions = decision.get("actions")
        decision_id = str(decision.get("decision_id") or "").strip()
        if not decision_id or not isinstance(actions, list) or not actions:
            continue
        commands: list[str] = []
        for action in actions:
            action_kind = str(action.get("action_kind") or "").strip()
            # revise/block require a bounded note. A one-tap callback cannot
            # supply that operator-authored direction, so those actions remain
            # explicit text commands instead of creating an invalid confirm.
            if action_kind in {"continue", "stop", "deny", "defer"}:
                commands.append(f"/decision {decision_id} {action_kind}")
            if len(commands) >= 3:
                break
        if commands:
            return {"context_kind": "decisions_actions", "choice_commands": commands}
    return None


def finalize_dispatch_result(
    result: dict[str, Any],
    message_preview: str,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # A card that stashed a pending confirmation gets one-tap 확인/취소 buttons.
    confirmation = result.get("pending_dispatch_confirmation")
    if context is None and isinstance(confirmation, dict):
        context = {
            "context_kind": "dispatch_confirm",
            "confirmation_token": str(confirmation.get("token") or ""),
        }
    attach_choice_surface(result, context)
    result.update(
        {
            "status": "rendered",
            "projection": None,
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result


def render_session_message_delivery(
    args: argparse.Namespace,
    result: dict[str, Any],
    *,
    session_id: str,
    message: str,
    expected_hash: str = "",
    expected_prompt_identity: str = "",
    expected_interaction_kind: str = "",
    require_exact_id: bool = False,
    expected_identity: dict[str, Any] | None = None,
    require_remote_identity: bool = False,
) -> dict[str, Any]:
    if result.get("mode") == "dry_run":
        message_preview = "\n".join(
            [
                "<b>로컬 에이전트 전달 준비</b>",
                f"대상: {html.escape(sanitize_text(session_id, max_chars=64))}",
                f"내용: {html.escape(sanitize_text(message, max_chars=240))}",
                "dry-run에서는 tmux 입력을 보내지 않았습니다.",
            ]
        )
        result["session_message_preview"] = {
            "session_id": session_id,
            "message_hash": hashlib.sha256(message.encode("utf-8")).hexdigest()[:16],
            "message_chars": len(message),
        }
        return finalize_dispatch_result(result, message_preview)
    try:
        dispatch_result = apply_session_text_input(
            args.forager_bin,
            args.profile,
            session_id,
            message,
            status_file=args.session_status_file,
            expected_hash=expected_hash,
            expected_prompt_identity=expected_prompt_identity,
            expected_interaction_kind=expected_interaction_kind,
            require_exact_id=require_exact_id,
            expected_identity=expected_identity,
            require_remote_identity=require_remote_identity,
        )
    except Exception as error:  # noqa: BLE001 - poll loop must never crash here
        dispatch_result = {
            "ok": False,
            "kind": "session_message",
            "action": "send_text",
            "error": sanitize_text(str(error), max_chars=200),
        }
    result["dispatch_result"] = dispatch_result
    target = " · ".join(
        part
        for part in (
            str(dispatch_result.get("project") or ""),
            str(dispatch_result.get("session_title") or ""),
            str(dispatch_result.get("tool") or ""),
        )
        if part
    )
    if dispatch_result.get("ok"):
        reacted = bool(dispatch_result.get("pane_reacted"))
        message_preview = "\n".join(
            [
                "<b>로컬 에이전트에 입력 전송됨</b>",
                html.escape(target or str(dispatch_result.get("session_id") or "")),
                f"전달: {html.escape(sanitize_text(message, max_chars=240))}",
                (
                    "tmux 입력 후 화면 변화를 감지했습니다. 작업 수락 여부는 아직 확인 전입니다."
                    if reacted
                    else "tmux 입력을 보냈습니다. 화면 반응과 작업 수락 여부는 아직 확인 전입니다."
                ),
            ]
        )
    elif str(dispatch_result.get("error") or "") == "prompt_changed":
        message_preview = "\n".join(
            [
                "<b>에이전트 화면이 바뀌어 전달하지 않았습니다</b>",
                f"현재: {html.escape(str(dispatch_result.get('prompt_line') or ''))}",
                "새 화면을 기준으로 다시 지시해 주세요.",
            ]
        )
    elif str(dispatch_result.get("error") or "") == "no_effect":
        message_preview = "\n".join(
            [
                "<b>에이전트 전달을 확인하지 못했습니다</b>",
                html.escape(target or session_id),
                "텍스트 전송 후에도 화면이 바뀌지 않아 성공으로 기록하지 않았습니다.",
            ]
        )
    elif str(dispatch_result.get("error") or "").startswith(
        ("session_identity_changed:", "session_identity_missing")
    ):
        message_preview = "\n".join(
            [
                "<b>에이전트 세션이 교체되어 전달하지 않았습니다</b>",
                html.escape(target or session_id),
                "새 세션 상태를 확인하고 다시 지시해 주세요.",
            ]
        )
    else:
        message_preview = render_dispatch_error_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            headline="로컬 에이전트에 전달하지 못했습니다",
            detail=str(dispatch_result.get("error") or "unknown"),
        )
    return finalize_dispatch_result(result, message_preview)


def render_dispatch_command(
    args: argparse.Namespace,
    config: dict[str, Any],
    parsed: dict[str, Any],
    result: dict[str, Any],
    *,
    session_context: dict[str, Any] | None = None,
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
        context: dict[str, Any] | None = None
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            decisions = open_decision_actions(surface)
            message_preview = render_decisions_message(
                profile=args.profile, generated_at=generated_at, decisions=decisions
            )
            context = decision_action_choice_context(decisions)
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="결정 목록을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview, context=context)
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
    if command == "run_list":
        allowlist = load_dispatch_allowlist(args.dispatch_allowlist_file)
        message_preview = render_run_list_message(
            profile=args.profile,
            generated_at=generated_at,
            templates=dispatch_templates(allowlist),
            configured=bool(args.dispatch_allowlist_file),
        )
        return finalize_dispatch_result(result, message_preview)
    if command == "run":
        if not args.dispatch_allowlist_file:
            message_preview = render_run_disabled_message(
                profile=args.profile, generated_at=generated_at
            )
            return finalize_dispatch_result(result, message_preview)
        closeout_id = str(parsed.get("closeout_id") or "")
        template_name = str(parsed.get("template_name") or "")
        allowlist = load_dispatch_allowlist(args.dispatch_allowlist_file)
        template = find_dispatch_template(allowlist, template_name)
        if template is None:
            names = ", ".join(str(item.get("name") or "") for item in dispatch_templates(allowlist)[:5])
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline=f"{template_name} 템플릿이 없습니다",
                detail=(f"가능: {names}" if names else "/run 으로 템플릿을 확인하세요."),
            )
            return finalize_dispatch_result(result, message_preview)
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
                    note=str(template.get("command") or ""),
                    chat_hash=None,
                    ttl_sec=args.dispatch_confirm_ttl_sec,
                )
                confirmation["runner"] = str(template.get("runner") or "")
                confirmation["command"] = str(template.get("command") or "")
                # The template name is authoritative: at confirm time the command
                # is re-resolved from the current allowlist by this name, so the
                # stored command is only a preview and cannot outlive its template.
                confirmation["template"] = str(template.get("name") or "")
                result["pending_dispatch_confirmation"] = confirmation
                message_preview = render_run_confirm_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    closeout_id=closeout_id,
                    template_name=str(template.get("name") or ""),
                    command=str(template.get("command") or ""),
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
    if command == "tasks":
        try:
            surface = export_workstation_surface(args.forager_bin, args.profile)
            tasks = cancellable_tasks_from_surface(surface)
            message_preview = render_tasks_message(
                profile=args.profile, generated_at=generated_at, tasks=tasks
            )
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="작업 목록을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "attention":
        attention_context = None
        try:
            surface = export_workstation_surface(
                args.forager_bin,
                args.profile,
                shift_id=args.shift_review_id,
                current_run_id=args.shift_review_current_run_id,
            )
            summary = attention_summary(surface)
            wiki = build_wiki_portfolio_summary(args.forager_bin, load_registry())
            wiki_candidate_count = int(wiki.get("candidate_count") or 0)
            summary["wiki_candidate_count"] = wiki_candidate_count
            if wiki_candidate_count:
                summary["total"] = int(summary.get("total") or 0) + wiki_candidate_count
                if not summary.get("top"):
                    summary["top"] = {
                        "kind": "wiki",
                        "title": f"위키 후보 {wiki_candidate_count}개 검토 대기",
                        "command_hint": "/wiki",
                    }
            message_preview = render_attention_summary_message(
                profile=args.profile, generated_at=generated_at, summary=summary
            )
            top = summary.get("top") if isinstance(summary, dict) else None
            top_command = str(top.get("command_hint") or "") if isinstance(top, dict) else ""
            # One-tap button for the single most urgent action.
            if top_command:
                attention_context = {"next_command": top_command}
        except RemoteOperatorTelegramError as error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="요약을 불러오지 못했습니다",
                detail=str(error),
            )
        return finalize_dispatch_result(result, message_preview, context=attention_context)
    if command in {"session_approve", "session_deny"}:
        # One tap by operator decision: the waiting card already shows what
        # the agent is asking, so the button acts directly. The executor
        # still re-verifies the session is waiting before sending the key.
        session_id = str(parsed.get("session_input_session_id") or "")
        action = "approve" if command == "session_approve" else "deny"
        if result.get("mode") == "dry_run":
            message_preview = "\n".join(
                [
                    "<b>세션 입력 준비</b>",
                    f"대상: {html.escape(session_id)}",
                    "dry-run에서는 tmux 키 입력을 보내지 않았습니다.",
                ]
            )
            return finalize_dispatch_result(result, message_preview)
        if (
            not isinstance(session_context, dict)
            or session_context.get("source") != "waiting_card"
            or not isinstance(session_context.get("remote_session_identity"), dict)
        ):
            dispatch_result = {
                "ok": False,
                "kind": "session_input",
                "error": "waiting_card_context_missing_or_changed",
            }
            result["dispatch_result"] = dispatch_result
            return finalize_dispatch_result(
                result,
                render_dispatch_error_message(
                    profile=args.profile,
                    generated_at=generated_at,
                    headline="세션 입력을 보내지 않았습니다",
                    detail=(
                        "현재 identity-bound waiting card가 없거나 startup gate가 "
                        "백그라운드 상태로 전환되었습니다."
                    ),
                ),
            )
        try:
            dispatch_result = apply_session_input(
                args.forager_bin, args.profile, session_id, action,
                status_file=args.session_status_file,
                expected_prompt_identity=str(parsed.get("session_input_hash") or ""),
                expected_interaction_kind=str(
                    (session_context or {}).get("interaction_kind") or ""
                ),
                option_key=str(parsed.get("session_input_option") or ""),
                expected_identity=(session_context or {}).get("remote_session_identity"),
                require_remote_identity=True,
            )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            dispatch_result = {"ok": False, "kind": "session_input", "error": sanitize_text(str(error), max_chars=200)}
        result["dispatch_result"] = dispatch_result
        if dispatch_result.get("ok"):
            message_preview = "\n".join(
                [
                    "<b>세션 입력 전송됨</b>",
                    f"{html.escape(str(dispatch_result.get('project') or ''))} · {html.escape(str(dispatch_result.get('session_title') or ''))}",
                    "승인 키(Enter)를 보냈습니다." if action == "approve" else "거부 키(Esc)를 보냈습니다.",
                    "다음 조치: /status",
                ]
            )
        elif str(dispatch_result.get("error") or "") == "no_effect":
            # The key was delivered but the pane never repainted, so the
            # session was not really holding a prompt. Say so instead of
            # letting the operator retry the same dead keystroke.
            message_preview = "\n".join(
                [
                    "<b>입력이 먹히지 않았습니다</b>",
                    f"{html.escape(str(dispatch_result.get('project') or ''))} · {html.escape(str(dispatch_result.get('session_title') or ''))}",
                    f"{html.escape(str(dispatch_result.get('key') or ''))} 전송 후에도 화면이 그대로입니다.",
                    "원격 승인으로는 해결되지 않습니다. 직접 확인이 필요합니다.",
                    "다음 조치: /status",
                ]
            )
        elif str(dispatch_result.get("error") or "") == "prompt_changed":
            message_preview = "\n".join(
                [
                    "<b>프롬프트가 바뀌어 중단했습니다</b>",
                    f"현재: {html.escape(str(dispatch_result.get('prompt_line') or ''))}",
                    "새 identity-bound waiting card를 다시 확인하세요.",
                    "다음 조치: /status",
                ]
            )
        elif str(dispatch_result.get("error") or "").startswith(
            ("session_identity_changed:", "session_identity_missing")
        ):
            message_preview = "\n".join(
                [
                    "<b>세션 대상이 교체되어 입력하지 않았습니다</b>",
                    "프로젝트, worktree, agent process 중 하나가 카드 생성 후 바뀌었습니다.",
                    "새 상태와 waiting card를 다시 확인해 주세요.",
                    "다음 조치: /status",
                ]
            )
        else:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="세션 입력 전송에 실패했습니다",
                detail=str(dispatch_result.get("error") or "unknown"),
            )
        return finalize_dispatch_result(result, message_preview)
    if command == "session_send":
        return render_session_message_delivery(
            args,
            result,
            session_id=str(parsed.get("session_message_session_id") or ""),
            message=str(parsed.get("session_message_text") or ""),
            expected_prompt_identity=str(parsed.get("session_message_hash") or ""),
            expected_interaction_kind=str(
                (session_context or {}).get("interaction_kind") or ""
            ),
            expected_identity=(session_context or {}).get("remote_session_identity"),
            require_remote_identity=bool(
                session_context
                and str(session_context.get("source") or "")
                in {"reply_card", "waiting_card"}
            ),
        )
    if command == "session_continue":
        selector = str(parsed.get("session_continue_session_id") or "")
        expected_hash = str(parsed.get("session_continue_hash") or "")
        context = session_context if isinstance(session_context, dict) else {}
        if context.get("already_processed"):
            result["dispatch_result"] = {
                "ok": False, "kind": "session_message", "action": "continue_next_task",
                "error": "already_processed",
            }
            return finalize_dispatch_result(result, "<b>이미 처리된 버튼입니다</b>\n추가 입력을 보내지 않았습니다.")
        context_session_id = str(context.get("session_id") or "")
        context_hash = str(context.get("prompt_hash") or "")
        if (
            context.get("source") != "waiting_card"
            or context.get("interaction_kind") != "task_ready"
            or not context_session_id.startswith(selector)
            or context_hash != expected_hash
            or not isinstance(context.get("remote_session_identity"), dict)
        ):
            message_preview = "\n".join(
                [
                    "<b>다음 작업을 시작하지 않았습니다</b>",
                    "현재 카드가 완료 후 다음 지시 대기 상태로 검증되지 않았습니다.",
                    "새 상태 카드를 확인해 주세요.",
                ]
            )
            result["dispatch_result"] = {
                "ok": False,
                "kind": "session_message",
                "action": "continue_next_task",
                "error": "task_ready_context_missing_or_changed",
            }
            return finalize_dispatch_result(result, message_preview)
        return render_session_message_delivery(
            args,
            result,
            session_id=context_session_id,
            message=SESSION_CONTINUE_MESSAGE,
            expected_prompt_identity=context_hash,
            expected_interaction_kind="task_ready",
            require_exact_id=True,
            expected_identity=context.get("remote_session_identity"),
            require_remote_identity=True,
        )
    if command == "cancel_task":
        task_id = str(parsed.get("cancel_task_id") or "")
        reason = str(parsed.get("cancel_reason") or "")
        confirmation = build_confirmation(
            kind="cancel_task",
            target_id=task_id,
            action_kind="cancel",
            observed_hash="",
            note=reason,
            chat_hash=None,
            ttl_sec=args.dispatch_confirm_ttl_sec,
        )
        result["pending_dispatch_confirmation"] = confirmation
        message_preview = render_cancel_task_confirm_message(
            profile=args.profile,
            generated_at=generated_at,
            task_id=task_id,
            reason=reason,
            token=confirmation["token"],
        )
        return finalize_dispatch_result(result, message_preview)
    if command == "terminate_task":
        task_id = str(parsed.get("terminate_task_id") or "")
        reason = str(parsed.get("terminate_reason") or "")
        confirmation = build_confirmation(
            kind="terminate_task",
            target_id=task_id,
            action_kind="terminate",
            observed_hash="",
            note=reason,
            chat_hash=None,
            ttl_sec=args.dispatch_confirm_ttl_sec,
        )
        result["pending_dispatch_confirmation"] = confirmation
        message_preview = render_terminate_task_confirm_message(
            profile=args.profile,
            generated_at=generated_at,
            task_id=task_id,
            reason=reason,
            token=confirmation["token"],
        )
        return finalize_dispatch_result(result, message_preview)
    if command == "resume":
        # Re-enabling autonomy is deliberate, so it is confirm-gated.
        confirmation = build_confirmation(
            kind="resume",
            target_id="operator_pause",
            action_kind="resume",
            observed_hash="",
            note="",
            chat_hash=None,
            ttl_sec=args.dispatch_confirm_ttl_sec,
        )
        result["pending_dispatch_confirmation"] = confirmation
        message_preview = render_resume_confirm_message(
            profile=args.profile, generated_at=generated_at, token=confirmation["token"]
        )
        return finalize_dispatch_result(result, message_preview)
    # pause immediately blocks new dispatch in run_once; confirm, cancel,
    # and resume are resolved against persisted state there too. This placeholder
    # is only visible on non-live probe paths.
    message_preview = render_dispatch_error_message(
        profile=args.profile,
        generated_at=generated_at,
        headline="라이브 처리 필요",
        detail="이 명령은 라이브 폴링에서 처리됩니다.",
    )
    return finalize_dispatch_result(result, message_preview)


def resolve_dispatch_confirmation(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    chat_hash: str,
    user_hash: str,
    rendered: dict[str, Any],
    parsed_command: dict[str, Any],
) -> None:
    """Apply the state-touching part of a dispatch command inside run_once."""

    assert_telegram_config_current(config, required=not args.dry_run)
    command = parsed_command.get("command")
    generated_at = rendered["generated_at"]
    if command == "pause":
        # Emergency stop is immediate (fail-safe): no confirmation step.
        try:
            pause_result = apply_operator_pause(
                args.forager_bin,
                args.profile,
                reason=str(parsed_command.get("pause_reason") or ""),
            )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            pause_result = {"ok": False, "kind": "pause", "error": sanitize_text(str(error), max_chars=240)}
        rendered["dispatch_result"] = pause_result
        message_preview = render_pause_result_message(
            profile=args.profile, generated_at=generated_at, result=pause_result
        )
        rendered["message_preview"] = message_preview
        rendered["mobile_card_contract"] = mobile_card_contract(message_preview)
        return
    if command in {
        "decision",
        "recover",
        "dispatch",
        "run",
        "cancel_task",
        "terminate_task",
        "resume",
    }:
        confirmation = rendered.get("pending_dispatch_confirmation")
        if isinstance(confirmation, dict):
            confirmation["chat_id_hash"] = chat_hash
            confirmation["user_id_hash"] = user_hash
            previous = store_confirmation(state, chat_hash, user_hash, confirmation)
            if previous is not None:
                # A new request invalidates the old token. The card stays within
                # its line budget; the old /confirm already fails clearly, and
                # this field records the supersede for logs and tests.
                rendered["superseded_pending_confirmation"] = True
        return
    if command == "cancel":
        cleared = clear_confirmation(
            state,
            chat_hash,
            user_hash,
            token=str(parsed_command.get("cancel_token") or ""),
        )
        message_preview = render_dispatch_cancel_message(
            profile=args.profile, generated_at=generated_at, cleared=cleared
        )
        rendered["message_preview"] = message_preview
        rendered["mobile_card_contract"] = mobile_card_contract(message_preview)
        return
    if command != "confirm":
        return

    token = str(parsed_command.get("confirm_token") or "")
    confirmation = pop_confirmation(state, chat_hash, user_hash, token)
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
        template_name = str(confirmation.get("template") or "")
        runner = str(confirmation.get("runner") or "")
        command = str(confirmation.get("command") or "")
        dispatch_allowed = bool(args.enable_runtime_dispatch)
        template_error = None
        if template_name:
            # Curated dispatch: re-resolve runner/command from the current
            # allowlist by name so only a still-vetted template can run. The
            # operator never supplied the command, and this path is allowed
            # without --enable-runtime-dispatch because the command is bounded
            # by the local allowlist rather than free-form operator input.
            template = find_dispatch_template(
                load_dispatch_allowlist(args.dispatch_allowlist_file), template_name
            )
            if template is None:
                template_error = True
            else:
                runner = str(template.get("runner") or "")
                command = str(template.get("command") or "")
                dispatch_allowed = True
        if template_error:
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="실행 템플릿이 더 이상 없습니다",
                detail="/run 으로 템플릿을 확인하세요.",
            )
        elif not dispatch_allowed:
            # Runtime dispatch has no envelope hash; runtime-preflight re-verifies
            # the closeout against the latest decision receipt at execution time.
            message_preview = render_runtime_disabled_message(
                profile=args.profile, generated_at=generated_at
            )
        else:
            try:
                dispatch_result = apply_runtime_dispatch(
                    args.forager_bin,
                    args.profile,
                    closeout_id=str(confirmation.get("target_id") or ""),
                    runner=runner,
                    command=command,
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
    elif str(confirmation.get("kind") or "") == "cancel_task":
        # Cancel is the fail-safe direction; it has no envelope hash to re-check.
        try:
            dispatch_result = apply_cancel_task(
                args.forager_bin,
                args.profile,
                str(confirmation.get("target_id") or ""),
                reason=str(confirmation.get("note") or ""),
            )
            rendered["dispatch_result"] = dispatch_result
            message_preview = render_cancel_task_result_message(
                profile=args.profile,
                generated_at=generated_at,
                result=dispatch_result,
            )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="작업 취소에 실패했습니다",
                detail=str(error),
            )
    elif str(confirmation.get("kind") or "") == "terminate_task":
        try:
            dispatch_result = apply_terminate_task(
                args.forager_bin,
                args.profile,
                str(confirmation.get("target_id") or ""),
                reason=str(confirmation.get("note") or ""),
                terminated_by=f"telegram:{user_hash}",
            )
            rendered["dispatch_result"] = dispatch_result
            message_preview = render_terminate_task_result_message(
                profile=args.profile,
                generated_at=generated_at,
                result=dispatch_result,
            )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="로컬 러너 종료에 실패했습니다",
                detail=str(error),
            )
    elif str(confirmation.get("kind") or "") == "plan_capture":
        rendered["captured_plan_text"] = str(confirmation.get("note") or "")
        message_preview = "\n".join([
            "<b>계획 후보 등록</b>",
            sanitize_text(rendered["captured_plan_text"], max_chars=140),
            "검토 후 로컬 Plan Mode 또는 야간 창에서 이어집니다.",
            "다음 조치: /plans · /status",
        ])
    elif str(confirmation.get("kind") or "") == "autonomy_start":
        try:
            dispatch_result = arm_confirmed_shift(
                args.forager_bin,
                args.profile,
                str(confirmation.get("target_id") or ""),
                str(confirmation.get("observed_hash") or ""),
            )
            rendered["dispatch_result"] = dispatch_result
            message_preview = "\n".join([
                "<b>검토된 자율주행 시작</b>",
                f"shift: {html.escape(sanitize_text(str(dispatch_result.get('shift_id') or ''), max_chars=64))}",
                "각 타이머는 exact shift ID와 due routine receipt를 사용합니다.",
                "이미 승인된 작업만 실행됩니다.",
                "다음 조치: /status · /pause",
            ])
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            message_preview = render_dispatch_error_message(
                profile=args.profile, generated_at=generated_at,
                headline="자율주행 시작에 실패했습니다", detail=str(error),
            )
    elif str(confirmation.get("kind") or "") == "resume":
        try:
            dispatch_result = apply_operator_resume(args.forager_bin, args.profile)
            rendered["dispatch_result"] = dispatch_result
            message_preview = render_resume_result_message(
                profile=args.profile,
                generated_at=generated_at,
                result=dispatch_result,
            )
        except Exception as error:  # noqa: BLE001 - poll loop must never crash here
            message_preview = render_dispatch_error_message(
                profile=args.profile,
                generated_at=generated_at,
                headline="정지 해제에 실패했습니다",
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


def build_chat_operator_snapshot(
    args: argparse.Namespace,
    *,
    focus_entry: dict[str, Any] | None = None,
    focus_source: str | None = None,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact live read-only state handed to the local chat agent.

    Without this the agent only sees chat history, so it answers state and
    workspace questions with "cannot check" and invents slash commands. Every
    section degrades gracefully; the chat must still work if a probe fails.
    """

    snapshot: dict[str, Any] = {"schema": "telegram_chat_operator_snapshot.v1"}
    if isinstance(focus_entry, dict):
        snapshot["project_focus"] = build_project_focus(
            args.forager_bin,
            args.profile,
            focus_entry,
            focus_source,
        )
    try:
        surface = export_workstation_surface(
            args.forager_bin,
            args.profile,
            shift_id=args.shift_review_id,
            current_run_id=args.shift_review_current_run_id,
        )
        snapshot["workstation"] = {
            "generated_at": surface.get("generated_at"),
            "attention_counts": surface.get("attention_counts"),
            "capacity": surface.get("capacity"),
            "health": [
                {"id": item.get("id"), "status": item.get("status")}
                for item in surface.get("health") or []
                if isinstance(item, dict)
            ],
            "open_decisions": [
                {"decision_id": item.get("decision_id"), "title": item.get("title")}
                for item in open_decision_actions(surface)[:5]
            ],
        }
        shift_review = surface.get("offdesk_shift_review")
        if isinstance(shift_review, dict):
            shift_summary = shift_review.get("summary")
            snapshot["workstation"]["offdesk_shift_review"] = {
                "shift_id": shift_review.get("shift_id"),
                "review_status": shift_review.get("review_status"),
                "lifecycle_status": shift_review.get("lifecycle_status"),
                "attention_reasons": [
                    sanitize_text(str(reason), max_chars=80)
                    for reason in (shift_review.get("attention_reasons") or [])[:8]
                ],
                "summary": shift_summary if isinstance(shift_summary, dict) else {},
                "authority": shift_review.get("authority"),
            }
    except (OSError, RemoteOperatorTelegramError) as error:
        snapshot["workstation"] = {
            "status": "unavailable",
            "error": sanitize_text(str(error), max_chars=120),
        }
    try:
        if args.session_status_file is not None:
            session_status = json.loads(args.session_status_file.read_text())
        else:
            session_status = run_forager_json(
                args.forager_bin,
                args.profile,
                ["status", "--json"],
                label="agent session status",
            )
        live_sessions = [
            {
                "id": str(row.get("id") or ""),
                "title": sanitize_text(str(row.get("title") or ""), max_chars=60),
                "tool": sanitize_text(str(row.get("tool") or ""), max_chars=24),
                "project": sanitize_text(str(row.get("project") or ""), max_chars=60),
                "status": str(row.get("status") or ""),
                "remote_session_identity_sha256": (
                    row.get("remote_session_identity") or {}
                ).get("binding_sha256")
                if remote_session_identity_issue(row.get("remote_session_identity")) is None
                else None,
            }
            for row in session_status.get("sessions") or []
            if isinstance(row, dict)
            and str(row.get("status") or "") in {"running", "idle", "waiting"}
            and str(row.get("id") or "")
        ]
        snapshot["live_agent_sessions"] = live_sessions
        context_id = str((session_context or {}).get("session_id") or "")
        context_match = next(
            (
                row
                for row in live_sessions
                if context_id and str(row.get("id") or "") == context_id
            ),
            None,
        )
        if (
            context_match
            and focus_source == "mention"
            and isinstance(snapshot.get("project_focus"), dict)
            and str(context_match.get("project") or "")
            != str(snapshot["project_focus"].get("key") or "")
        ):
            context_match = None
        if context_match:
            snapshot["conversation_session"] = {
                **context_match,
                "source": str((session_context or {}).get("source") or "sticky"),
                "prompt_hash": str((session_context or {}).get("prompt_hash") or ""),
            }
    except (OSError, json.JSONDecodeError, RemoteOperatorTelegramError) as error:
        snapshot["live_agent_sessions"] = []
        snapshot["agent_session_error"] = sanitize_text(str(error), max_chars=120)
    snapshot["autonomy_armed"] = autonomy_is_armed(args.forager_bin, args.profile)
    registry = load_registry()
    if registry:
        snapshot["registered_projects"] = registry_summary(registry)
    try:
        roots = workspace_roots(args)
        paths = discover_project_paths(roots, max_paths=60)
        snapshot["workspace_projects"] = [relative_path_hint(path, roots) for path in paths][:40]
    except OSError:
        snapshot["workspace_projects"] = []
    return snapshot


def run_chat_tool(
    args: argparse.Namespace,
    request: dict[str, Any],
    *,
    registry: dict[str, dict[str, Any]],
    focus_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Execute one read-only chat-agent tool call. Never raises.

    The chat agent may only touch registered projects, and only through the
    bounded read-only helpers in telegram_operator.projects.
    """

    tool = str(request.get("tool") or "")
    if tool == "service_probe":
        try:
            port = int(request.get("port"))
        except (TypeError, ValueError):
            return {"status": "invalid_port", "port": request.get("port")}
        if not 1 <= port <= 65535:
            return {"status": "invalid_port", "port": port}
        commands = (
            ["ss", "-ltnp", f"sport = :{port}"],
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        )
        unavailable: list[str] = []
        for command in commands:
            try:
                process = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=3,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                unavailable.append(type(error).__name__)
                continue
            lines = [line.strip() for line in process.stdout.splitlines() if line.strip()]
            # ss includes one header line; lsof includes one header line. A
            # second line is a live listener observation.
            listening = len(lines) >= 2
            return {
                "status": "listening" if listening else "not_listening",
                "port": port,
                "observed_by": command[0],
                "listener": sanitize_text(lines[-1], max_chars=180) if listening else None,
            }
        return {
            "status": "probe_unavailable",
            "port": port,
            "errors": unavailable[:2],
        }
    key = str(request.get("project") or "").strip() or str((focus_entry or {}).get("key") or "")
    entry = registry.get(key)
    if not isinstance(entry, dict):
        return {
            "status": "unknown_project",
            "project": key or None,
            "registered": sorted(registry.keys())[:20],
        }
    project_dir = find_project_dir(entry, workspace_roots(args))
    if project_dir is None:
        return {"status": "workspace_not_found", "project": key}
    try:
        if tool == "workspace_overview":
            report = inspect_project_workspace(project_dir)
            report["status"] = "ok"
            report["project"] = key
            return report
        if tool == "list_dir":
            return list_project_dir(project_dir, str(request.get("path") or ""))
        if tool == "read_file":
            return read_project_file(project_dir, str(request.get("path") or ""))
    except Exception as error:  # noqa: BLE001 - chat tools must never crash the poll loop
        return {
            "status": "tool_failed",
            "tool": tool[:40],
            "error": sanitize_text(str(error), max_chars=120),
        }
    return {"status": "unknown_tool", "tool": tool[:40]}


def requested_service_port(text: str) -> int | None:
    """Extract an explicitly named TCP port for an automatic read-only probe."""

    patterns = (
        r"(?<!\d)(\d{2,5})\s*포트",
        r"\bport\s*[:#]?\s*(\d{2,5})\b",
        r"(?<!\d)(\d{2,5})\s*(?:리스닝|listen(?:ing)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            port = int(match.group(1))
            return port if 1 <= port <= 65535 else None
    return None


def telegram_message_text(preview: Any) -> str:
    """Convert the exact rendered Telegram card into compact conversation text."""

    raw = html.unescape(re.sub(r"<[^>]+>", "", str(preview or "")))
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    semantic = [
        line
        for line in lines
        if not line.startswith(("다음 조치:", "참조:", "관련:"))
        and line not in {"Forager 응답", "확인 필요"}
    ]
    return sanitize_text("\n".join(semantic), max_chars=800)


def record_conversation_event(
    args: argparse.Namespace,
    rendered: dict[str, Any],
    *,
    chat_hash: str,
    user_hash: str,
    message_id: int | None,
) -> None:
    parsed = rendered.get("parsed_command")
    parsed = parsed if isinstance(parsed, dict) else {}
    append_jsonl(
        args.conversation_file,
        {
            "schema": "remote_operator_telegram_conversation.v1",
            "recorded_at": utc_now(),
            "profile": args.profile,
            "chat_id_hash": chat_hash,
            "user_id_hash": user_hash,
            "source_message_id": message_id,
            "processed_update_id": rendered.get("processed_update_id"),
            "command": parsed.get("command"),
            "command_reason": parsed.get("reason"),
            "operator_text": sanitize_text(
                str(rendered.get("command_text") or ""), max_chars=800
            ),
            "project_focus": rendered.get("project_focus"),
            "tools_used": rendered.get("chat_tools_used") or [],
            "sent_text": telegram_message_text(rendered.get("message_preview")),
            "send_status": rendered.get("send_status"),
            "sent_message_id": rendered.get("sent_message_id"),
            "effect": rendered.get("effect"),
            "remote_command_envelope": rendered.get("remote_command_envelope"),
            "remote_plan_session_id": rendered.get("remote_plan_session_id"),
        },
    )


def render_command_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    command_text: str,
    *,
    mode: str,
    feedback_context: dict[str, Any] | None = None,
    chat_history: list[dict[str, Any]] | None = None,
    sticky_focus_key: str | None = None,
    session_context: dict[str, Any] | None = None,
    remote_plan_session_id: str | None = None,
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
    if parsed.get("command") == "receipts":
        selected_session_id = (
            str(parsed.get("receipt_session_id") or "").strip()
            or str(remote_plan_session_id or "").strip()
            or None
        )
        try:
            report = inspect_remote_receipts(
                args.remote_plan_artifact_dir,
                args.update_journal_file,
                requested_session_id=selected_session_id,
            )
        except RemoteOperatorStateError:
            report = {
                "schema": "remote_operator_receipt_inspection.v1",
                "status": "invalid" if selected_session_id else "no_plan_session",
                "session_id": selected_session_id,
                "session_issues": ["receipt_inspection_unavailable"],
                "available_receipt_count": 0,
                "invalid_receipt_count": 1,
                "total_receipt_stage_count": 17,
                "command_update_count": 0,
                "refused_command_count": 0,
                "read_only": True,
                "mutation_authorized": False,
                "approval_authorized": False,
                "execution_authorized": False,
                "accepted_truth_authorized": False,
            }
        result["parsed_command"] = parsed
        result["remote_receipt_inspection"] = report
        if report.get("session_id"):
            result["remote_plan_session_id"] = report.get("session_id")
        message_preview = render_receipt_inspection_message(
            profile=args.profile,
            report=report,
        )
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
    if parsed.get("command") == "guide":
        result["parsed_command"] = parsed
        message_preview = guide_message(profile=args.profile, generated_at=result["generated_at"])
        attach_choice_surface(result, None)
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(
                    message_preview,
                    max_lines=GUIDE_SHEET_MAX_LINES,
                    max_chars=GUIDE_SHEET_MAX_CHARS,
                ),
            }
        )
        return result
    if parsed.get("command") == "chat":
        chat_text = str(parsed.get("chat_text") or command_text)
        registry = load_registry()
        focus_entry, focus_source = resolve_chat_focus(
            chat_text,
            registry,
            sticky_focus_key,
        )
        if focus_entry:
            result["project_focus"] = {
                "key": focus_entry.get("key"),
                "source": focus_source,
            }
        ambiguous_projects = (
            project_mention_matches(chat_text, registry)
            if focus_source == "ambiguous"
            else []
        )
        if ambiguous_projects:
            result["project_resolution"] = {
                "status": "ambiguous",
                "candidates": [entry.get("key") for entry in ambiguous_projects],
            }
        operator_snapshot = build_chat_operator_snapshot(
            args,
            focus_entry=focus_entry,
            focus_source=focus_source,
            session_context=session_context,
        )
        tool_outcomes: list[dict[str, Any]] = []

        def execute_chat_tool(request: dict[str, Any]) -> dict[str, Any]:
            outcome = run_chat_tool(args, request, registry=registry, focus_entry=focus_entry)
            tool_outcomes.append({"request": request, "result": outcome})
            return outcome

        initial_tool_results: list[dict[str, Any]] = []
        service_port = requested_service_port(chat_text) if not ambiguous_projects else None
        if service_port is not None:
            request = {"tool": "service_probe", "port": service_port, "project": "", "path": ""}
            initial_tool_results.append(
                {"request": request, "result": execute_chat_tool(request)}
            )

        if ambiguous_projects:
            choices = ", ".join(str(entry.get("key") or "") for entry in ambiguous_projects)
            agent_intent = {
                "status": "classified",
                "intent": "chat",
                "confidence": 1.0,
                "requires_clarification": True,
                "clarifying_question": f"어느 프로젝트인가요? {choices}",
                "assistant_reply": "",
                "reason": "ambiguous_project_reference",
                "non_authorized": ["execution", "approval", "shell"],
            }
        else:
            agent_intent = chat_with_agent(
                args,
                chat_text,
                feedback_context=feedback_context,
                chat_history=chat_history,
                operator_snapshot=operator_snapshot,
                tool_executor=execute_chat_tool,
                initial_tool_results=initial_tool_results,
            )
            if isinstance(agent_intent, dict):
                agent_intent = validate_session_message_intent(
                    agent_intent,
                    chat_text=chat_text,
                    operator_snapshot=operator_snapshot,
                )
        if isinstance(agent_intent, dict) and agent_intent.get("tools_used"):
            result["chat_tools_used"] = agent_intent.get("tools_used")
        if (
            isinstance(agent_intent, dict)
            and agent_intent.get("intent") == "chat"
            and not agent_intent.get("assistant_reply")
        ):
            # The model inspected but never composed an answer; degrade to a
            # deterministic summary of the last successful overview probe.
            overview = next(
                (
                    item["result"]
                    for item in reversed(tool_outcomes)
                    if item["request"].get("tool") == "workspace_overview"
                    and item["result"].get("status") == "ok"
                ),
                None,
            )
            if overview and focus_entry:
                agent_intent = {
                    **agent_intent,
                    "assistant_reply": "\n".join(
                        [f"{focus_entry.get('display_name')} 로컬 상태:"]
                        + inspection_summary_lines(overview)
                    ),
                }
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
        if (
            mode == "dry_run"
            and isinstance(agent_intent, dict)
            and agent_intent.get("intent") == "session_message"
        ):
            message_preview = "\n".join(
                [
                    "<b>로컬 에이전트 전달 준비</b>",
                    f"대상: {sanitize_text(str(agent_intent.get('session_id') or ''), max_chars=24)}",
                    f"내용: {sanitize_text(str(agent_intent.get('session_message') or ''), max_chars=180)}",
                    "dry-run에서는 실제 입력을 보내지 않았습니다.",
                    "다음 조치: Telegram에서 같은 평문 전송",
                ]
            )
        clarification_context = None
        if isinstance(agent_intent, dict) and agent_intent.get("requires_clarification"):
            clarification_context = {
                "schema": INTERACTION_CONTEXT_SCHEMA,
                "context_kind": "chat_clarification",
                "question": sanitize_text(
                    str(agent_intent.get("clarifying_question") or ""), max_chars=180
                ),
                "project_key": str((focus_entry or {}).get("key") or "") or None,
            }
        attach_choice_surface(result, clarification_context)
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
    if parsed.get("command") == "projects":
        registry = load_registry()
        summary = build_project_portfolio_summary(
            args.forager_bin,
            args.profile,
            registry,
            workspace_roots(args),
        )
        selector = str(parsed.get("project_selector") or "")
        selector_matches = project_selector_matches(selector, registry) if selector else []
        selected = selector_matches[0] if len(selector_matches) == 1 else None
        selected_key = str((selected or {}).get("key") or "") or None
        context = None
        if selected:
            result["project_focus"] = {"key": selected_key, "source": "explicit"}
        elif selector:
            summary["selection_status"] = (
                "ambiguous" if len(selector_matches) > 1 else "unknown"
            )
            summary["project_selector"] = selector
            summary["selection_candidates"] = [
                entry.get("key") for entry in selector_matches
            ]
        elif not selector:
            rows = [row for row in summary.get("projects") or [] if isinstance(row, dict)]
            if rows:
                context = {"next_command": f"/projects {rows[0].get('key')}"}
        result["parsed_command"] = parsed
        message_preview = render_project_portfolio_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            summary=summary,
            selected_key=selected_key,
        )
        attach_choice_surface(result, context)
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "project_summary": summary,
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
        remember_text = str(parsed.get("remember_text") or command_text)
        registry = load_registry()
        focus_entry, focus_source = resolve_chat_focus(
            remember_text,
            registry,
            sticky_focus_key,
        )
        target_profile = args.profile
        if focus_source == "ambiguous":
            matches = project_mention_matches(remember_text, registry)
            parsed["remember_target_status"] = "ambiguous"
            parsed["remember_target_candidates"] = [entry.get("key") for entry in matches]
            result["parsed_command"] = parsed
            result["project_resolution"] = {
                "status": "ambiguous",
                "candidates": parsed["remember_target_candidates"],
            }
            choices = ", ".join(str(value) for value in parsed["remember_target_candidates"])
            message_preview = "\n".join(
                [
                    "<b>위키 대상 확인 필요</b>",
                    f"같은 별칭을 쓰는 프로젝트가 여러 개입니다: {html.escape(choices)}",
                    "프로젝트 키를 포함해 /remember를 다시 보내세요.",
                    "아직 위키 후보를 저장하지 않았습니다.",
                ]
            )
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
        if focus_entry:
            target_profile = str(focus_entry.get("wiki_profile") or args.profile)
            result["project_focus"] = {
                "key": focus_entry.get("key"),
                "source": focus_source,
            }
        parsed["wiki_profile"] = target_profile
        result["parsed_command"] = parsed
        message_preview = render_wiki_candidate_message(
            profile=target_profile,
            generated_at=result["generated_at"],
            remember_text=remember_text,
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
    if parsed.get("command") == "wiki":
        registry = load_registry()
        selector = str(parsed.get("project_selector") or "")
        wiki_context = None
        if selector:
            selector_matches = project_selector_matches(selector, registry)
            focus_entry = selector_matches[0] if len(selector_matches) == 1 else None
            if focus_entry:
                summary = build_wiki_plane_summary(
                    args.forager_bin,
                    focus_entry,
                    include_entries=True,
                )
                result["project_focus"] = {
                    "key": focus_entry.get("key"),
                    "source": "explicit",
                }
            else:
                summary = {
                    "status": "ambiguous_project"
                    if len(selector_matches) > 1
                    else "unknown_project",
                    "project_selector": selector,
                    "project_candidates": [
                        entry.get("key") for entry in selector_matches
                    ],
                }
        else:
            summary = build_wiki_portfolio_summary(args.forager_bin, registry)
            top_projects = [
                row
                for row in summary.get("projects") or []
                if isinstance(row, dict) and int(row.get("candidate_count") or 0) > 0
            ]
            if top_projects:
                top_key = str(top_projects[0].get("project_key") or "")
                if top_key:
                    wiki_context = {"next_command": f"/wiki {top_key}"}
        result["parsed_command"] = parsed
        message_preview = render_wiki_summary_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            summary=summary,
        )
        attach_choice_surface(result, wiki_context)
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "wiki_summary": summary,
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
        "run_list",
        "run",
        "tasks",
        "cancel_task",
        "terminate_task",
        "session_approve",
        "session_deny",
        "session_continue",
        "session_send",
        "pause",
        "resume",
        "attention",
        "confirm",
        "cancel",
    }:
        return render_dispatch_command(
            args,
            config,
            parsed,
            result,
            session_context=session_context,
        )
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


SESSION_CALLBACK_RE = re.compile(
    r"^fg1:(c|a|d):([A-Za-z0-9_-]{4,32}):([0-9a-f]{8}|[0-9a-f]{16})(?::([1-4]))?$"
)
CONFIRMATION_CALLBACK_RE = re.compile(r"^fg1:x:(c|d):([0-9a-f]{12})$")
DECISION_CALLBACK_RE = re.compile(
    r"^fg1:v:([A-Za-z0-9._-]{1,40}):(continue|stop|deny|defer)$"
)
PLAN_CALLBACK_RE = re.compile(
    r"^fg1:p:([0-9a-f]{8}):([0-9a-f]{16}):([0-9]{1,2})$"
)


def command_from_callback_data(data: str) -> str:
    """Decode only bounded callback payloads emitted by this adapter."""

    value = str(data or "").strip()
    if value == "fg1:s":
        return "/status"
    confirmation = CONFIRMATION_CALLBACK_RE.fullmatch(value)
    if confirmation:
        action, token = confirmation.groups()
        return f"/confirm {token}" if action == "c" else f"/cancel {token}"
    decision = DECISION_CALLBACK_RE.fullmatch(value)
    if decision:
        decision_id, action = decision.groups()
        return f"/decision {decision_id} {action}"
    plan = PLAN_CALLBACK_RE.fullmatch(value)
    if plan:
        session_id, context_hash, choice_index = plan.groups()
        return f"/plan_input @{session_id}:{context_hash}:{choice_index}"
    match = SESSION_CALLBACK_RE.fullmatch(value)
    if not match:
        return ""
    action, session_id, prompt_hash, option = match.groups()
    if action == "c" and option is None:
        return f"/session_continue {session_id} {prompt_hash}"
    if action == "a":
        suffix = f" {option}" if option else ""
        return f"/session_approve {session_id} {prompt_hash}{suffix}"
    if action == "d" and option is None:
        return f"/session_deny {session_id} {prompt_hash}"
    return ""


def callback_query_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    callback = update.get("callback_query")
    return callback if isinstance(callback, dict) else None


def callback_query_id_for(update: dict[str, Any]) -> str:
    callback = callback_query_from_update(update)
    return str((callback or {}).get("id") or "").strip()


def callback_data_for(update: dict[str, Any]) -> str:
    callback = callback_query_from_update(update)
    return str((callback or {}).get("data") or "").strip()


def callback_consumes_card(data: str) -> bool:
    value = str(data or "").strip()
    return value != "fg1:s" and bool(
        SESSION_CALLBACK_RE.fullmatch(value)
        or CONFIRMATION_CALLBACK_RE.fullmatch(value)
        or DECISION_CALLBACK_RE.fullmatch(value)
        or PLAN_CALLBACK_RE.fullmatch(value)
    )


def callback_outcome_text(rendered: dict[str, Any]) -> str:
    """Return a truthful callback toast after validation and handling."""

    effect = rendered.get("effect") if isinstance(rendered.get("effect"), dict) else {}
    dispatch = (
        rendered.get("dispatch_result")
        if isinstance(rendered.get("dispatch_result"), dict)
        else {}
    )
    if effect.get("status") == "applied" or dispatch.get("ok") is True:
        return "요청을 처리했습니다."
    error = str(dispatch.get("error") or rendered.get("reason") or "")
    if error == "already_processed":
        return "이미 처리된 버튼입니다. 추가 입력은 보내지 않았습니다."
    if error == "prompt_changed":
        return "화면이 변경되어 처리하지 않았습니다."
    if error.startswith("plan_"):
        return "계획 카드가 변경되어 처리하지 않았습니다."
    if error.startswith(("session_identity_", "remote_session_identity_")):
        return "세션이 변경되어 처리하지 않았습니다."
    if str(rendered.get("status") or "") == "command_refused":
        return "만료되었거나 유효하지 않은 요청입니다."
    parsed = rendered.get("parsed_command")
    if isinstance(parsed, dict) and parsed.get("command") == "status":
        return "최신 상태를 보냈습니다."
    if (
        isinstance(parsed, dict)
        and parsed.get("command") == "remote_plan_selection"
        and str(rendered.get("status") or "") == "rendered"
    ):
        return "계획 선택을 처리했습니다."
    if (
        isinstance(parsed, dict)
        and parsed.get("command") == "decision"
        and isinstance(rendered.get("pending_dispatch_confirmation"), dict)
    ):
        return "확인 카드를 만들었습니다."
    if effect.get("status") == "refused" or error:
        return "요청을 처리하지 않았습니다."
    return "요청 결과를 보냈습니다."


def render_remote_plan_callback_refusal(
    args: argparse.Namespace,
    config: dict[str, Any],
    *,
    mode: str,
    session: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    result = result_base(args, config, mode)
    message_preview = "\n".join(
        [
            "<b>계획 선택 카드가 변경되었습니다</b>",
            "이전 버튼은 적용하지 않았습니다.",
            "아래의 현재 선택지를 다시 확인해 주세요."
            if isinstance(session, dict)
            else "새 계획 요청에서 다시 시작해 주세요.",
        ]
    )
    result.update(
        {
            "status": "rendered",
            "reason": reason,
            "parsed_command": {
                "supported": True,
                "command": "remote_plan_callback",
                "reason": reason,
            },
            "dispatch_result": {
                "ok": False,
                "kind": "remote_plan_session",
                "error": reason,
            },
            "projection": None,
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    if isinstance(session, dict):
        result["remote_plan_session"] = public_remote_plan_session(session)
        context = (
            remote_plan_selection_context(session)
            if str(session.get("stage") or "") == "project_selection"
            else remote_plan_init_context(session)
        )
        attach_choice_surface(result, context)
    else:
        attach_choice_surface(result, None)
    return result


def message_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    message = update.get("message")
    if isinstance(message, dict):
        return message
    callback = callback_query_from_update(update)
    callback_message = (callback or {}).get("message")
    command = command_from_callback_data(str((callback or {}).get("data") or ""))
    if not isinstance(callback_message, dict) or not command:
        return None
    synthetic = dict(callback_message)
    synthetic["from"] = (callback or {}).get("from")
    synthetic["text"] = command
    synthetic["date"] = int(time.time())
    return synthetic


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


def chat_type_for(message: dict[str, Any]) -> str:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return ""
    return str(chat.get("type") or "").strip().lower()


def message_id_for(message: dict[str, Any]) -> int | None:
    value = message.get("message_id")
    return int(value) if isinstance(value, int) else None


def message_date_for(message: dict[str, Any]) -> int | None:
    value = message.get("date")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def replied_message_id_for(message: dict[str, Any]) -> int | None:
    replied = message.get("reply_to_message")
    if not isinstance(replied, dict):
        return None
    value = replied.get("message_id")
    return int(value) if isinstance(value, int) else None


def replied_session_context(
    state: dict[str, Any], message: dict[str, Any]
) -> dict[str, Any] | None:
    replied_message_id = replied_message_id_for(message)
    if replied_message_id is None:
        return None
    notified = state.get("waiting_notified_by_session")
    if not isinstance(notified, dict):
        return None
    matches = [
        (str(session_id), entry)
        for session_id, entry in notified.items()
        if isinstance(entry, dict)
        and not entry.get("resolved")
        and entry.get("message_id") == replied_message_id
    ]
    if len(matches) != 1:
        return None
    session_id, entry = matches[0]
    context = {
        "session_id": session_id,
        "project": str(entry.get("project") or ""),
        "title": str(entry.get("title") or ""),
        "tool": str(entry.get("tool") or ""),
        "prompt_hash": str(entry.get("prompt_identity") or entry.get("prompt_hash") or ""),
        "prompt_identity": str(
            entry.get("prompt_identity") or entry.get("prompt_hash") or ""
        ),
        "prompt_observation_hash": str(entry.get("prompt_observation_hash") or ""),
        "source": "reply_card",
        "updated_at": utc_now(),
        "interaction_kind": str(entry.get("interaction_kind") or "unknown"),
    }
    identity = entry.get("remote_session_identity")
    if isinstance(identity, dict):
        context["remote_session_identity"] = identity
    return context


def consumed_continue_key(parsed: dict[str, Any], chat_hash: str, user_hash: str, card_id: int | None = None) -> str | None:
    if parsed.get("command") != "session_continue":
        return None
    if type(card_id) is not int or card_id <= 0:
        return None
    selector = str(parsed.get("session_continue_session_id") or "")
    prompt = str(parsed.get("session_continue_hash") or "")
    if not selector or not prompt:
        return None
    return hashlib.sha256(json.dumps([chat_hash, user_hash, card_id, selector, prompt]).encode()).hexdigest()


def pending_session_action_context(
    state: dict[str, Any], parsed: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve one waiting-card action to its exact stored session identity."""

    if parsed.get("command") not in {
        "session_approve",
        "session_deny",
        "session_continue",
        "session_send",
    }:
        return None
    selector = str(
        parsed.get("session_input_session_id")
        or parsed.get("session_continue_session_id")
        or parsed.get("session_message_session_id")
        or ""
    )
    prompt_hash = str(
        parsed.get("session_input_hash")
        or parsed.get("session_continue_hash")
        or parsed.get("session_message_hash")
        or ""
    )
    if not selector:
        return None
    notified = state.get("waiting_notified_by_session")
    if not isinstance(notified, dict):
        return None
    matches = [
        (str(session_id), entry)
        for session_id, entry in notified.items()
        if isinstance(entry, dict)
        and not entry.get("resolved")
        and not entry.get("bootstrap_suppressed")
        and str(session_id).startswith(selector)
        and (
            not prompt_hash
            or str(entry.get("prompt_identity") or entry.get("prompt_hash") or "")
            == prompt_hash
        )
        and (
            parsed.get("command") != "session_continue"
            or entry.get("interaction_kind") == "task_ready"
        )
    ]
    if len(matches) != 1:
        return None
    session_id, entry = matches[0]
    context = {
        "session_id": session_id,
        "project": str(entry.get("project") or ""),
        "title": str(entry.get("title") or ""),
        "tool": str(entry.get("tool") or ""),
        "prompt_hash": str(entry.get("prompt_identity") or entry.get("prompt_hash") or ""),
        "prompt_identity": str(
            entry.get("prompt_identity") or entry.get("prompt_hash") or ""
        ),
        "prompt_observation_hash": str(entry.get("prompt_observation_hash") or ""),
        "source": "waiting_card",
        "updated_at": utc_now(),
        "interaction_kind": str(entry.get("interaction_kind") or "unknown"),
    }
    identity = entry.get("remote_session_identity")
    if isinstance(identity, dict):
        context["remote_session_identity"] = identity
    return context


def apply_feedback_workflow_presentation(
    args: argparse.Namespace,
    rendered: dict[str, Any],
    outcome: FeedbackWorkflowOutcome,
    *,
    feedback_text: str,
    parsed_command: dict[str, Any],
    feedback_context: dict[str, Any] | None,
) -> None:
    """Apply one domain outcome and render its Telegram presentation."""

    rendered.update(outcome.result_fields())
    if isinstance(outcome.plan_session, dict):
        message_preview = render_project_selection_message(
            profile=args.profile,
            session=outcome.plan_session,
        )
        choice_context = remote_plan_selection_context(outcome.plan_session)
    else:
        message_preview = render_feedback_message(
            profile=args.profile,
            generated_at=rendered["generated_at"],
            feedback_text=feedback_text,
            feedback_kind=str(
                parsed_command.get("feedback_kind") or "freeform_feedback"
            ),
            feedback_context=feedback_context,
            inbox_status=str(
                outcome.ingest_result.get("decision_feedback_ingest_status") or ""
            ),
            agent_intent=(
                parsed_command.get("agent_intent")
                if isinstance(parsed_command.get("agent_intent"), dict)
                else None
            ),
        )
        choice_context = feedback_context
    rendered["message_preview"] = message_preview
    rendered["mobile_card_contract"] = mobile_card_contract(message_preview)
    attach_choice_surface(rendered, choice_context)


def update_is_allowed(config: dict[str, Any], message: dict[str, Any]) -> tuple[bool, str]:
    chat_id = chat_id_for(message)
    user_id = user_id_for(message)
    if not config.get("operator_identity_configured"):
        return False, "operator_identity_not_configured"
    if config.get("operator_mode") == "single_owner":
        if chat_type_for(message) != "private":
            return False, "single_owner_private_chat_required"
        if chat_id != str(config.get("owner_chat_id") or ""):
            return False, "chat_not_owner"
        if user_id != str(config.get("owner_user_id") or ""):
            return False, "user_not_owner"
        return True, "owner_allowed"
    if config.get("operator_mode") != "multi_operator":
        return False, "operator_mode_not_supported"
    if chat_type_for(message) != "private":
        return False, "multi_operator_private_chat_required"
    if (chat_id, user_id) not in (config.get("operator_members") or set()):
        return False, "operator_pair_not_allowed"
    return True, "operator_allowed"


def operator_identity_for(
    config: dict[str, Any], *, chat_hash: str, user_hash: str
) -> dict[str, Any]:
    owner_chat_hash = sha256_short(str(config.get("owner_chat_id") or ""))
    owner_user_hash = sha256_short(str(config.get("owner_user_id") or ""))
    role = (
        "owner"
        if chat_hash == owner_chat_hash and user_hash == owner_user_hash
        else "operator"
    )
    return {
        "schema": "remote_operator_identity.v1",
        "operator_id": f"telegram:{user_hash}",
        "operator_mode": str(config.get("operator_mode") or ""),
        "role": role,
        "chat_id_hash": chat_hash,
        "user_id_hash": user_hash,
    }


DEFAULT_AUTONOMY_WATCH = [
    pathlib.Path.home() / ".claude" / "projects",
    pathlib.Path.home() / ".codex" / "sessions",
]


def latest_activity_epoch(paths: list[pathlib.Path], max_depth: int = 3) -> float:
    """Newest mtime across shallow levels of the watch trees (cheap idle probe)."""
    newest = 0.0
    for root in paths:
        if not root.exists():
            continue
        stack = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                newest = max(newest, current.stat().st_mtime)
                if depth >= max_depth or not current.is_dir():
                    continue
                for child in current.iterdir():
                    stack.append((child, depth + 1))
            except OSError:
                continue
    return newest


def run_shift_forager_json(
    forager_bin: str,
    profile: str,
    argv: list[str],
    *,
    timeout: int = 30,
) -> Any:
    command = [forager_bin, "-p", profile, *argv, "--json"]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if process.returncode != 0:
        detail = sanitize_text(process.stderr or process.stdout or "", max_chars=300)
        raise RemoteOperatorTelegramError(f"forager command failed: {detail}")
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError("forager command returned invalid JSON") from error


def bounded_shift_status(forager_bin: str, profile: str) -> dict[str, Any]:
    value = run_shift_forager_json(
        forager_bin,
        profile,
        ["offdesk", "shift", "status"],
    )
    if not isinstance(value, dict) or value.get("schema") != "offdesk_shift_status_report.v1":
        raise RemoteOperatorTelegramError("bounded shift status schema is invalid")
    return value


def autonomy_is_armed(forager_bin: str, profile: str) -> bool:
    try:
        status = bounded_shift_status(forager_bin, profile)
        active_shift_id = str(status.get("active_shift_id") or "")
        return any(
            isinstance(shift, dict)
            and shift.get("shift_id") == active_shift_id
            and shift.get("status") in {"armed", "running"}
            for shift in status.get("shifts") or []
        )
    except Exception:  # noqa: BLE001 - probe must never break the loop
        return False


def latest_prepared_shift(status: dict[str, Any]) -> dict[str, Any] | None:
    shifts = status.get("shifts")
    if not isinstance(shifts, list):
        return None
    now = dt.datetime.now(dt.timezone.utc)
    for shift in reversed(shifts):
        if not isinstance(shift, dict) or shift.get("status") is not None:
            continue
        try:
            stops_at = dt.datetime.fromisoformat(str(shift.get("stops_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if stops_at >= now:
            return shift
    return None


def arm_confirmed_shift(
    forager_bin: str,
    profile: str,
    shift_id: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    status = bounded_shift_status(forager_bin, profile)
    item = next(
        (
            shift
            for shift in status.get("shifts") or []
            if isinstance(shift, dict) and shift.get("shift_id") == shift_id
        ),
        None,
    )
    if not item or item.get("manifest_sha256") != manifest_sha256:
        raise RemoteOperatorTelegramError(
            "confirmed bounded shift no longer matches the reviewed manifest"
        )
    weekly = item.get("weekly_usage") or {}
    if weekly.get("runtime_weekly_limit") and weekly.get("allowed") is not True:
        raise RemoteOperatorTelegramError("weekly allowance is held; fresh bound samples and review required")
    if status.get("active_shift_id") == shift_id:
        return {"ok": True, "kind": "autonomy_start", "shift_id": shift_id, "reconciled": True}
    if status.get("active_shift_id"):
        raise RemoteOperatorTelegramError("another bounded shift is already active")
    arm_result = run_shift_forager_json(
        forager_bin,
        profile,
        ["offdesk", "shift", "arm", shift_id],
    )
    if isinstance(arm_result, dict) and arm_result.get("schema") == "offdesk_shift_arm_report.v1":
        return {"ok": True, "kind": "autonomy_start", "shift_id": shift_id, "reconciled": True}
    if not isinstance(arm_result, list) or len(arm_result) != 1:
        raise RemoteOperatorTelegramError("shift arm did not return one exact approval")
    approval_id = str(arm_result[0].get("approval_id") or "")
    if not approval_id:
        raise RemoteOperatorTelegramError("shift arm approval ID is missing")
    run_shift_forager_json(
        forager_bin,
        profile,
        ["offdesk", "ok", approval_id, "--by", "telegram"],
    )
    armed = run_shift_forager_json(
        forager_bin,
        profile,
        [
            "offdesk",
            "shift",
            "arm",
            shift_id,
            "--approval-id",
            approval_id,
        ],
    )
    if not isinstance(armed, dict) or armed.get("schema") != "offdesk_shift_arm_report.v1":
        raise RemoteOperatorTelegramError("shift arm receipt was not durably reconciled")
    return {
        "ok": True,
        "kind": "autonomy_start",
        "shift_id": shift_id,
        "approval_id": approval_id,
    }


def _standalone_notification_cycle(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
) -> NotificationCycle:
    """Build the delivery adapter used by direct scanner calls and tests."""

    def send(candidate: NotificationCandidate) -> int | None:
        return send_message(
            config,
            str(config.get("target_chat_id") or ""),
            candidate.message,
            args,
            reply_markup=candidate.reply_markup,
        )

    holds = state.setdefault("notification_unconfirmed_events", {})
    if not isinstance(holds, dict):
        raise RemoteOperatorTelegramError("invalid notification uncertainty ledger")
    return NotificationCycle(
        send, dry_run=bool(getattr(args, "dry_run", False)), unconfirmed_events=holds,
        persist=lambda: save_state(args.state_file, state),
    )


def _close_notification_scan(
    cycle: NotificationCycle,
    own_cycle: bool,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Close a direct scanner cycle while leaving shared cycles to run_once."""

    if own_cycle:
        result["notification_cycle"] = cycle.deliver()
    return result


def _attention_priority(item: dict[str, Any]) -> NotificationPriority:
    kind = str(item.get("kind") or "")
    if kind in {"recovery", "task"}:
        return NotificationPriority.RECOVERY
    if kind == "decision":
        return NotificationPriority.DECISION
    return NotificationPriority.STATUS


def _session_priority(interaction_kind: str) -> NotificationPriority:
    if interaction_kind in {"permission", "question", "selection", "startup_trust"}:
        return NotificationPriority.INTERACTION
    if interaction_kind == "task_ready":
        return NotificationPriority.TASK_READY
    return NotificationPriority.STATUS


def scan_and_propose_autonomy(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    cycle: NotificationCycle | None = None,
) -> dict[str, Any] | None:
    """Offer (never start) overnight autonomy when the workstation goes idle.

    The proposal is a normal confirmation card: the operator's 확인 tap arms a
    bounded window; 취소 or silence leaves everything off. Never raises into
    the poll loop.
    """

    if not getattr(args, "autonomy_propose", False):
        return None
    own_cycle = cycle is None
    cycle = cycle or _standalone_notification_cycle(args, config, state)
    try:
        target_chat_id = str(config.get("target_chat_id") or "").strip()
        chat_hash = str(config.get("target_chat_id_hash") or "").strip()
        owner_user_id = str(config.get("owner_user_id") or "").strip()
        if not target_chat_id or not chat_hash or not owner_user_id:
            return {"status": "no_target_chat"}
        user_hash = sha256_short(owner_user_id)
        now = time.time()
        local_hour = time.localtime(now).tm_hour
        if not (local_hour >= int(args.autonomy_propose_from_hour) or local_hour < 2):
            return {"status": "outside_window"}
        shift_status = bounded_shift_status(args.forager_bin, args.profile)
        if shift_status.get("active_shift_id"):
            return {"status": "already_armed"}
        try:
            pause = run_shift_forager_json(
                args.forager_bin,
                args.profile,
                ["offdesk", "pause-status"],
            )
            if isinstance(pause, dict) and pause.get("paused"):
                return {"status": "paused"}
        except (OSError, ValueError, RemoteOperatorTelegramError):
            pass
        prepared = latest_prepared_shift(shift_status)
        if not prepared:
            return {"status": "no_prepared_shift"}
        pending = (state.get("pending_dispatch_confirmations_by_chat") or {}).get(chat_hash)
        if isinstance(pending, dict):
            if confirmation_is_fresh(pending):
                return {"status": "confirmation_already_pending"}
            # An expired confirmation (e.g. last night's unanswered proposal)
            # must not block proposals forever.
            clear_confirmation(state, chat_hash, user_hash)
        last = float(state.get("autonomy_proposal_at") or 0)
        if now - last < int(args.autonomy_backoff_sec):
            return {"status": "backoff"}
        watch = list(args.autonomy_watch_path) or DEFAULT_AUTONOMY_WATCH
        newest = latest_activity_epoch(watch)
        idle_min = int((now - newest) / 60) if newest else 0
        if not newest or idle_min < int(args.autonomy_idle_min):
            return {"status": "active", "idle_min": idle_min}

        confirmation = build_confirmation(
            kind="autonomy_start",
            target_id=str(prepared.get("shift_id") or ""),
            action_kind="arm",
            observed_hash=str(prepared.get("manifest_sha256") or ""),
            note=f"idle {idle_min}m",
            chat_hash=chat_hash,
            user_hash=user_hash,
            ttl_sec=1800,
        )
        message = "\n".join(
            [
                "<b>자율주행 제안</b>",
                f"{idle_min}분째 활동이 없습니다.",
                f"검토된 shift: {sanitize_text(str(prepared.get('shift_id') or ''), max_chars=64)}",
                f"창: {prepared.get('starts_at')} → {prepared.get('stops_at')}",
                f"범위: task {int(prepared.get('task_count') or 0)} · routine {int(prepared.get('routine_count') or 0)}",
                (f"{'야간 시작 주간 사용률 대비 예산' if prepared['weekly_usage']['binding']['contract'].get('nightly') else '승인 기준 주간 사용률 대비 실행 예산'}: 제공자별 {prepared['weekly_usage']['binding']['contract']['limit_percentage_points']}%p · 최신 측정값 검사 · 실행 중 초과 가능"
                 if (prepared.get("weekly_usage") or {}).get("runtime_weekly_limit") else "주간 허용량 제한: 이 실행에 연결되지 않음"),
                "이미 승인된 작업만 실행되며 /pause 로 즉시 중단됩니다.",
                "다음 조치: 확인 또는 취소",
            ]
        )
        report = {
            "status": "deferred",
            "idle_min": idle_min,
            "attempted_count": 0,
            "notified_count": 0,
            "message_preview": message,
            "mobile_card_contract": mobile_card_contract(message),
        }
        event_key = "autonomy_start:" + ":".join(
            [
                str(prepared.get("shift_id") or ""),
                str(prepared.get("manifest_sha256") or ""),
            ]
        )

        def on_delivered(message_id: int | None) -> None:
            store_confirmation(state, chat_hash, user_hash, confirmation)
            state["autonomy_proposal_at"] = now
            report.update(
                {
                    "status": "proposed",
                    "send_status": "dry_run" if args.dry_run else "sent",
                    "attempted_count": 1,
                    "notified_count": 1,
                    "sent_message_id": message_id,
                }
            )

        cycle.add(
            NotificationCandidate(
                event_key=event_key,
                source="autonomy",
                priority=NotificationPriority.AUTONOMY,
                message=message,
                reply_markup=choice_keyboard(
                    {
                        "context_kind": "dispatch_confirm",
                        "confirmation_token": str(confirmation.get("token") or ""),
                    }
                ),
                report=report,
                on_delivered=on_delivered,
            )
        )
        return _close_notification_scan(cycle, own_cycle, report)
    except TelegramControlRevokedError:
        raise
    except Exception as error:  # noqa: BLE001 - poll loop must never crash here
        return {"status": "scan_failed", "error": sanitize_text(str(error), max_chars=240)}


def summarize_waiting_prompt(args: argparse.Namespace, tail: str) -> str:
    """One-line summary of the latest agent response or request.

    Captures the tmux pane tail (read-only) and asks the local model to
    compress it. This output is presentation only: deterministic terminal
    markers separately decide which actions a card may expose.
    """

    from telegram_operator.agent import (  # noqa: PLC0415
        call_ollama_intent_agent,
        resolve_agent_config,
        select_agent_runtime,
    )
    try:
        if not tail.strip():
            return ""
        fallback = sanitize_text(tail.splitlines()[-1], max_chars=120)
        agent_config = resolve_agent_config(args)
        if agent_config.get("mode") == "off":
            return fallback
        runtime = select_agent_runtime(agent_config)
        if not runtime:
            return fallback
        prompt = "\n".join(
            [
                "This is a terminal observation, not proof that operator input is required.",
                "In ONE short Korean line, summarize its latest completed result, question, selection, or permission request.",
                "Preserve any no-action-required guidance. Do not invent a request, authorization, or next task.",
                'Return exactly one JSON object: {"summary": "<one line>"}. No markdown.',
                "Terminal tail:",
                tail[-3000:],
            ]
        )
        parsed = call_ollama_intent_agent(runtime, prompt)
        summary = sanitize_text(str(parsed.get("summary") or ""), max_chars=140)
        return summary or fallback
    except Exception:  # noqa: BLE001 - poll loop must never crash here
        return ""


def session_interaction_markup(
    interaction: dict[str, Any], *, session_id: str, prompt_hash: str
) -> dict[str, Any]:
    """Build a bounded inline keyboard for one exact prompt identity."""

    sid = session_id[:8]
    kind = str(interaction.get("kind") or "unknown")
    keyboard: list[list[dict[str, str]]] = []
    if prompt_hash and kind in {"permission", "selection", "startup_trust"}:
        options = [
            option
            for option in interaction.get("options") or []
            if isinstance(option, dict)
        ]
        if options:
            for option in options[:4]:
                key = str(option.get("key") or "")
                label = sanitize_text(str(option.get("label") or key), max_chars=40)
                keyboard.append(
                    [
                        {
                            "text": f"{key}. {label}",
                            "callback_data": f"fg1:a:{sid}:{prompt_hash}:{key}",
                        }
                    ]
                )
        else:
            keyboard.append(
                [
                    {
                        "text": "승인",
                        "callback_data": f"fg1:a:{sid}:{prompt_hash}",
                    },
                    {
                        "text": "거부",
                        "callback_data": f"fg1:d:{sid}:{prompt_hash}",
                    },
                ]
            )
    elif prompt_hash and kind == "task_ready":
        keyboard.append(
            [
                {
                    "text": "다음 작업 바로 진행",
                    "callback_data": f"fg1:c:{sid}:{prompt_hash}",
                }
            ]
        )
    keyboard.append([{"text": "상태", "callback_data": "fg1:s"}])
    return {"inline_keyboard": keyboard}


def in_quiet_hours(from_hour: int, to_hour: int, *, local_hour: int) -> bool:
    """True when ``local_hour`` falls inside the do-not-disturb window.

    Equal bounds disable the window. The usual window wraps midnight (23 to 8).
    """

    from_hour %= 24
    to_hour %= 24
    if from_hour == to_hour:
        return False
    if from_hour < to_hour:
        return from_hour <= local_hour < to_hour
    return local_hour >= from_hour or local_hour < to_hour


def session_notify_threshold(args: argparse.Namespace, cards_sent: int) -> int:
    """Seconds to wait before re-carding a prompt already carded ``cards_sent`` times.

    The wait doubles per repeat. A prompt that stays open is either being
    handled or cannot be handled remotely; either way the reminder should fade
    rather than hold a fixed 30-minute cadence all night.
    """

    base = int(args.session_notify_backoff_sec)
    ceiling = int(args.session_notify_max_backoff_sec)
    return min(base * (2 ** max(0, cards_sent - 1)), ceiling)


def is_unattended_orchestrator_startup_gate(
    session: dict[str, Any], interaction_kind: str
) -> bool:
    """Keep auto-created coordinator bootstrap UI out of urgent action cards.

    New Forager status rows carry explicit provenance. The default title is a
    bounded legacy fallback for coordinators stored before that field existed.
    Only startup trust and self-update UI are suppressed; real runtime
    permission requests remain visible.
    """

    is_orchestrator = bool(session.get("auto_orchestrator")) or str(
        session.get("title") or ""
    ).strip().casefold() == "orchestrator"
    return is_orchestrator and interaction_kind in {
        "startup_trust",
        "maintenance_update",
    }


def update_orchestrator_bootstrap_digest(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    blocked: list[dict[str, str]],
    *,
    cycle: NotificationCycle | None = None,
) -> dict[str, Any] | None:
    """Collect one status-only digest when the blocked bootstrap set changes."""

    target_chat_id = str(config.get("target_chat_id") or "").strip()
    previous = state.get("orchestrator_bootstrap_digest")
    previous = previous if isinstance(previous, dict) else {}
    if not blocked:
        message_id = previous.get("message_id")
        if message_id and not previous.get("resolved"):
            edit_message(
                config,
                target_chat_id,
                int(message_id),
                "<b>백그라운드 Orchestrator 준비 상태 - 해결됨</b>\n"
                "현재 보류 중인 startup gate가 없습니다.",
                args,
            )
        state.pop("orchestrator_bootstrap_digest", None)
        return None

    canonical = sorted(
        (
            str(item.get("session_id") or ""),
            str(item.get("prompt_hash") or ""),
            str(item.get("interaction_kind") or ""),
        )
        for item in blocked
    )
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    if previous.get("fingerprint") == fingerprint:
        return {
            "status": "unchanged",
            "count": len(blocked),
            "fingerprint": fingerprint,
        }

    old_message_id = previous.get("message_id")

    labels = {
        "startup_trust": "프로젝트 신뢰 확인",
        "maintenance_update": "Codex 업데이트 안내",
    }
    lines = [
        f"<b>백그라운드 Orchestrator 준비 필요 · {len(blocked)}개</b>",
        "개별 승인 버튼은 보내지 않았습니다.",
    ]
    for item in sorted(
        blocked,
        key=lambda value: (
            str(value.get("project") or ""),
            str(value.get("session_id") or ""),
        ),
    )[:6]:
        project = html.escape(sanitize_text(item.get("project") or "미등록", max_chars=40))
        kind = str(item.get("interaction_kind") or "")
        lines.append(f"• {project}: {labels.get(kind, '시작 확인 필요')}")
    if len(blocked) > 6:
        lines.append(f"• 그 외 {len(blocked) - 6}개")
    lines.append("On-desk에서 검토하거나 해당 세션을 재시작하세요.")
    own_cycle = cycle is None
    cycle = cycle or _standalone_notification_cycle(args, config, state)
    report = {
        "status": "deferred",
        "count": len(blocked),
        "fingerprint": fingerprint,
    }
    message = "\n".join(lines)

    def on_delivered(message_id: int | None) -> None:
        state["orchestrator_bootstrap_digest"] = {
            "fingerprint": fingerprint,
            "message_id": message_id,
            "count": len(blocked),
            "at": time.time(),
            "resolved": False,
        }
        if old_message_id and not previous.get("resolved"):
            try:
                edit_message(
                    config,
                    target_chat_id,
                    int(old_message_id),
                    "<b>백그라운드 Orchestrator 준비 상태 - 갱신됨</b>\n"
                    "새 상태 요약이 전송되었습니다.",
                    args,
                )
            except TelegramControlRevokedError:
                raise
            except RemoteOperatorTelegramError:
                pass
        report.update(
            {
                "status": "notified",
                "send_status": "dry_run" if args.dry_run else "sent",
                "message_id": message_id,
                "notified_count": 1,
            }
        )

    cycle.add(
        NotificationCandidate(
            event_key=f"bootstrap:{fingerprint}",
            source="session",
            priority=NotificationPriority.STATUS,
            message=message,
            reply_markup={
                "inline_keyboard": [[{"text": "상태", "callback_data": "fg1:s"}]]
            },
            report=report,
            on_delivered=on_delivered,
        )
    )
    return _close_notification_scan(cycle, own_cycle, report)


def session_input_dead_keys(state: dict[str, Any]) -> dict[str, Any]:
    """Prompts where a remote keystroke provably did nothing, keyed sid8:hash."""

    dead = state.get("session_input_no_effect_by_key")
    if not isinstance(dead, dict):
        dead = {}
        state["session_input_no_effect_by_key"] = dead
    return dead


def record_session_input_outcome(state: dict[str, Any], rendered: dict[str, Any]) -> None:
    """Remember prompts a remote keystroke failed to move, and forget the rest.

    A prompt that swallowed Enter without repainting will swallow the next one
    too. Recording it lets the card stop asking; any other outcome clears the
    mark so a later, live prompt is not muted by an old failure.
    """

    dispatch = rendered.get("dispatch_result")
    parsed = rendered.get("parsed_command")
    if not isinstance(dispatch, dict) or not isinstance(parsed, dict):
        return
    if dispatch.get("kind") != "session_input":
        return
    sid8 = str(parsed.get("session_input_session_id") or "")[:8]
    prompt_hash = str(parsed.get("session_input_hash") or "")
    if not sid8 or not prompt_hash:
        return
    dead = session_input_dead_keys(state)
    key = f"{sid8}:{prompt_hash}"
    if str(dispatch.get("error") or "") == "no_effect":
        entry = dead.get(key) if isinstance(dead.get(key), dict) else {}
        dead[key] = {"count": int(entry.get("count") or 0) + 1, "at": utc_now()}
    else:
        dead.pop(key, None)


def scan_and_notify_waiting_sessions(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    cycle: NotificationCycle | None = None,
) -> dict[str, Any] | None:
    """Collect cards for waiting sessions for the shared notification cycle.

    A session that flips to waiting gets a card once, then again on a doubling
    backoff up to a card cap. Sessions that leave the waiting state are cleared
    so the next episode notifies immediately. Cards are suppressed inside the
    quiet-hours window, and for prompts a remote keystroke has already failed
    to move. Never raises into the poll loop.
    """

    if not getattr(args, "session_notify", False):
        return None
    target_chat_id = str(config.get("target_chat_id") or "").strip()
    if not target_chat_id:
        return {"status": "no_target_chat"}
    own_cycle = cycle is None
    cycle = cycle or _standalone_notification_cycle(args, config, state)
    try:
        keyboard_cleanup = None
        if (
            not args.dry_run
            and state.get("telegram_session_keyboard_schema") != "inline_v3"
        ):
            result = {
                "status": "deferred",
                "keyboard_cleanup": "pending",
                "notified_count": 0,
                "attempted_count": 0,
            }
            report = result
            message = (
                "<b>입력창 고정 버튼을 정리했습니다</b>\n"
                "이제 일반 응답에는 버튼이 없고 필요한 선택지만 카드에 표시됩니다."
            )

            def on_delivered(message_id: int | None) -> None:
                state["telegram_session_keyboard_schema"] = "inline_v3"
                # Reply-keyboard cards cannot be edited into inline cards and
                # may already have exhausted their reminder cap. Forget only
                # that rebuildable notification registry so every still-live
                # interaction receives one freshly classified inline card.
                state["waiting_notified_by_session"] = {}
                state["waiting_ids_last_scan"] = []
                report.update(
                    {
                        "status": "notified",
                        "keyboard_cleanup": "removed",
                        "send_status": "dry_run" if args.dry_run else "sent",
                        "attempted_count": 1,
                        "notified_count": 1,
                        "sent_message_id": message_id,
                    }
                )

            cycle.add(
                NotificationCandidate(
                    event_key="keyboard_cleanup:inline_v3",
                    source="session",
                    priority=NotificationPriority.KEYBOARD_CLEANUP,
                    message=message,
                    reply_markup={"remove_keyboard": True},
                    report=report,
                    on_delivered=on_delivered,
                )
            )
            # Cleanup is an exclusive prerequisite. Do not inspect or mutate
            # waiting cards after staging it in this cycle.
            return _close_notification_scan(cycle, own_cycle, result)
        if args.session_status_file is not None:
            status = json.loads(args.session_status_file.read_text())
        else:
            process = subprocess.run(
                [args.forager_bin, "-p", args.profile, "status", "--json", "--notification-capture"],
                capture_output=True, text=True, timeout=30,
            )
            if process.returncode != 0:
                return {
                    "status": "scan_failed",
                    "error": sanitize_text(process.stderr or process.stdout, max_chars=200),
                }
            status = json.loads(process.stdout)
        sessions = [s for s in status.get("sessions") or [] if isinstance(s, dict)]
        waiting = [s for s in sessions if s.get("status") == "waiting"]
        notified = state.setdefault("waiting_notified_by_session", {})
        if not isinstance(notified, dict):
            notified = {}
            state["waiting_notified_by_session"] = notified
        now = time.time()
        stopped_pending = observe_session_lifecycle(state, sessions, now=now)
        waiting_ids = {str(s.get("id") or "") for s in waiting}
        chat_hash = config.get("target_chat_id_hash")
        if len(waiting) > 1:
            # A plain follow-up cannot safely choose between several waiting
            # cards. Only an explicit Telegram reply remains session-bound.
            clear_session_focus_for_chat_hash(
                state,
                chat_hash,
                sources={"waiting_card"},
            )
        # A dead-key mark belongs to one waiting episode. Once the session
        # stops waiting, drop it so the next prompt is carded normally.
        dead_keys = session_input_dead_keys(state)
        waiting_prefixes = {sid[:8] for sid in waiting_ids}
        for key in list(dead_keys):
            if key.split(":", 1)[0] not in waiting_prefixes:
                del dead_keys[key]
        # Normalize legacy float entries to dicts so cards can be expired.
        for session_id, value in list(notified.items()):
            if isinstance(value, (int, float)):
                notified[session_id] = {"at": float(value)}
        # Episode end: expire the pushed card in the chat right away (a stale
        # card tapped hours later just fails its re-verification, which reads
        # as a broken bot), then forget the session so the next episode
        # notifies immediately (entries younger than 300s stay to damp
        # flapping between polls).
        for session_id in list(notified):
            entry = notified[session_id]
            if session_id in waiting_ids:
                entry.pop("task_ready_running_scans", None)
                continue
            if entry.get("interaction_kind") == "task_ready":
                observed = next((s for s in sessions if str(s.get("id") or "") == session_id), {})
                if observed.get("status") == "running":
                    entry["task_ready_running_scans"] = int(entry.get("task_ready_running_scans") or 0) + 1
                else:
                    entry.pop("task_ready_running_scans", None)
                if int(entry.get("task_ready_running_scans") or 0) >= 2:
                    entry["task_ready_rearmed"] = True
                if not entry.get("task_ready_rearmed"):
                    # Missing/idle observations are not proof of another task.
                    continue
            clear_session_focus_for_chat_hash(
                state,
                chat_hash,
                session_id=session_id,
                sources={"waiting_card"},
            )
            message_id = entry.get("message_id")
            if message_id and not entry.get("resolved"):
                edit_message(
                    config,
                    target_chat_id,
                    int(message_id),
                    "<b>세션 입력 대기 - 해결됨</b>\n"
                    f"{entry.get('label') or session_id[:8]}\n"
                    "이 카드는 만료되었습니다 (세션이 더 이상 입력 대기가 아닙니다).",
                    args,
                )
                entry["resolved"] = True
            if now - float(entry.get("at") or 0) > 300:
                del notified[session_id]
        prev_waiting = {str(x) for x in state.get("waiting_ids_last_scan") or []}
        state["waiting_ids_last_scan"] = sorted(waiting_ids)
        # Ordinary cards go silent overnight. Startup gates are still
        # classified so an older actionable card can be invalidated, but the
        # compact digest waits until quiet hours end.
        quiet_hours = in_quiet_hours(
            int(args.session_notify_quiet_from_hour),
            int(args.session_notify_quiet_to_hour),
            local_hour=time.localtime(now).tm_hour,
        )
        sent = []
        candidate_count = 0
        muted = []
        result: dict[str, Any] = {
            "status": "deferred",
            "waiting_count": len(waiting),
            "stopped_pending_count": len(stopped_pending),
            "sent": sent,
        }
        if not quiet_hours:
            for stop_event in stopped_pending:
                session_id = stop_event["session_id"]
                project = html.escape(sanitize_text(stop_event["project"], max_chars=40))
                title = html.escape(sanitize_text(stop_event["title"], max_chars=60))
                tool = html.escape(sanitize_text(stop_event["tool"], max_chars=24))
                message = (
                    "<b>세션 중단 감지</b>\n"
                    f"□ {project} · {title} ({tool})\n"
                    "살아 있던 세션의 실행이 사라졌습니다. 종료 원인은 확인되지 않았습니다.\n"
                    "On-desk에서 상태와 마지막 작업을 확인한 뒤 필요하면 재시작하세요."
                )

                def on_stop_delivered(
                    message_id: int | None, *, _event: dict[str, Any] = stop_event
                ) -> None:
                    if not args.dry_run:
                        mark_stop_alert(
                            state, _event, status="delivered", now=time.time(),
                            message_id=message_id,
                        )
                    result.update({
                        "status": "notified",
                        "stopped_session_id": _event["session_id"],
                        "stopped_alert_message_id": message_id,
                    })

                def on_stop_unconfirmed(*, _event: dict[str, Any] = stop_event) -> None:
                    mark_stop_alert(
                        state, _event, status="delivery_unconfirmed", now=time.time()
                    )

                cycle.add(NotificationCandidate(
                    event_key=f"session-stopped:{session_id}:{stop_event['observed_at']}",
                    source="session",
                    priority=NotificationPriority.RECOVERY,
                    message=message,
                    reply_markup=None,
                    report=result,
                    on_delivered=on_stop_delivered,
                    on_unconfirmed=on_stop_unconfirmed,
                ))
        bootstrap_blocked: list[dict[str, str]] = []
        for session in waiting:
            session_id = str(session.get("id") or "")
            if not session_id:
                continue
            remote_identity = session.get("remote_session_identity")
            identity_error = remote_session_identity_issue(remote_identity)
            if identity_error:
                muted.append(
                    {
                        "session_id": session_id,
                        "reason": "remote_session_identity_unavailable",
                        "identity_error": identity_error,
                    }
                )
                continue
            if str(remote_identity.get("session_id") or "") != session_id:
                muted.append(
                    {
                        "session_id": session_id,
                        "reason": "remote_session_identity_mismatch",
                    }
                )
                continue
            entry = notified.get(session_id) or {}
            last = float(entry.get("at") or 0)
            cards_sent = int(entry.get("cards") or 0)
            title = sanitize_text(str(session.get("title") or session_id), max_chars=60)
            tool = sanitize_text(str(session.get("tool") or "agent"), max_chars=24)
            project = sanitize_text(str(session.get("project") or "미등록"), max_chars=40)
            from telegram_operator.dispatch import (  # noqa: PLC0415
                pane_prompt_hash,
                session_prompt_identity,
            )
            from telegram_operator.remote_session_identity import notification_capture_tail
            from telegram_operator.notification_evidence import (
                payload_evidence, phrase_flags, record_delivery,
            )

            tail = notification_capture_tail(session)
            if not tail:
                muted.append({"session_id": session_id, "reason": "capture_unavailable"})
                continue
            interaction = classify_session_interaction(tail, tool)
            if interaction.get("kind") in {"passive_wait", "unknown"}:
                muted.append({
                    "session_id": session_id,
                    "reason": "no_verified_input_request",
                    "interaction_kind": interaction.get("kind"),
                    "interaction_reason": interaction.get("reason"),
                    "observed_phrase_flags": phrase_flags(tail),
                })
                continue
            prompt_observation_hash = pane_prompt_hash(tail)
            prompt_hash = session_prompt_identity(
                tail, tool, interaction=interaction
            )
            previous_prompt_hash = str(
                entry.get("prompt_identity") or entry.get("prompt_hash") or ""
            )
            previous_identity_hash = str(
                (entry.get("remote_session_identity") or {}).get("binding_sha256") or ""
            )
            identity_changed = previous_identity_hash != str(
                remote_identity.get("binding_sha256") or ""
            )
            if (entry.get("delivery_unconfirmed") and not identity_changed
                    and previous_prompt_hash == prompt_hash):
                muted.append({"session_id": session_id, "reason": "delivery_unconfirmed_hold"})
                continue
            if previous_identity_hash and identity_changed:
                dead_keys.pop(f"{session_id[:8]}:{prompt_hash}", None)
            interaction_kind = str(interaction.get("kind") or "unknown")
            if is_unattended_orchestrator_startup_gate(
                session, interaction_kind
            ):
                bootstrap_blocked.append(
                    {
                        "session_id": session_id,
                        "project": project,
                        "interaction_kind": interaction_kind,
                        "prompt_hash": prompt_hash,
                        "prompt_identity": prompt_hash,
                        "prompt_observation_hash": prompt_observation_hash,
                    }
                )
                if (
                    not entry.get("bootstrap_suppressed")
                    or previous_prompt_hash != prompt_hash
                    or identity_changed
                ):
                    message_id = entry.get("message_id")
                    if message_id and not entry.get("resolved"):
                        edit_message(
                            config,
                            target_chat_id,
                            int(message_id),
                            "<b>백그라운드 Orchestrator 시작 확인</b>\n"
                            f"{html.escape(project)} · {html.escape(title)}\n"
                            "개별 승인 카드는 만료되었습니다. 묶음 상태에서 확인하세요.",
                            args,
                        )
                    clear_session_focus_for_chat_hash(
                        state,
                        chat_hash,
                        session_id=session_id,
                        sources={"waiting_card"},
                    )
                    # Invalidating an obsolete action is maintenance, not a
                    # claim that its replacement digest was delivered.
                    notified[session_id] = {
                        "at": now,
                        "cards": 0,
                        "message_id": None,
                        "prompt_hash": prompt_hash,
                        "prompt_identity": prompt_hash,
                        "prompt_observation_hash": prompt_observation_hash,
                        "label": f"{project} · {title} ({tool})",
                        "project": project,
                        "title": title,
                        "tool": tool,
                        "interaction_kind": interaction_kind,
                        "interaction_reason": interaction.get("reason"),
                        "remote_session_identity": remote_identity,
                        "bootstrap_suppressed": True,
                        "resolved": True,
                    }
                muted.append(
                    {
                        "session_id": session_id,
                        "reason": "unattended_orchestrator_startup_gate",
                        "interaction_kind": interaction_kind,
                        "prompt_hash": prompt_hash,
                    }
                )
                continue
            if quiet_hours:
                continue
            if (interaction_kind == "task_ready" and entry.get("task_ready_input_confirmed")
                    and not identity_changed and not entry.get("task_ready_rearmed")):
                candidate = entry.get("task_ready_candidate_identity")
                stable = prompt_hash != previous_prompt_hash and candidate == prompt_hash
                entry["task_ready_candidate_identity"] = prompt_hash
                if stable:
                    entry["task_ready_rearmed"] = True
                else:
                    muted.append({"session_id": session_id, "reason": "task_ready_awaiting_stable_post_input"})
                    continue
            same_task_ready_episode = (
                interaction_kind == "task_ready"
                and entry.get("interaction_kind") == "task_ready"
                and not identity_changed
                and not entry.get("task_ready_rearmed")
                and bool(entry.get("message_id") or entry.get("delivery_unconfirmed"))
            )
            if same_task_ready_episode:
                updated = False
                if (prompt_hash != previous_prompt_hash and entry.get("message_id")
                        and not entry.get("delivery_unconfirmed")
                        and now - float(entry.get("task_ready_edit_attempt_at") or 0) >= 60):
                    # Editing preserves one card. Bind its new buttons only after
                    # Telegram acknowledges the edit; old callbacks stay stale.
                    message = ("<b>에이전트 작업 완료 · 다음 지시 대기</b>\n"
                               f"⏸ {html.escape(project)} · {html.escape(title)} ({html.escape(tool)})\n"
                               "같은 대기 상태입니다. 최신 화면을 확인해 버튼을 갱신했습니다.\n"
                               "기존 계획의 다음 미완료 작업만 이어서 진행할 수 있습니다.")
                    entry["task_ready_edit_attempt_at"] = now
                    updated = edit_message(config, target_chat_id, int(entry["message_id"]), message, args,
                        reply_markup=session_interaction_markup(interaction, session_id=session_id, prompt_hash=prompt_hash))
                    if updated:
                        entry.update(prompt_hash=prompt_hash, prompt_identity=prompt_hash,
                                     prompt_observation_hash=prompt_observation_hash)
                muted.append({"session_id": session_id, "reason": "same_task_ready_episode",
                              "card_updated": updated, "observed_prompt_identity": prompt_hash,
                              "delivered_prompt_identity": entry.get("prompt_identity")})
                continue
            # A session that was not waiting at the previous scan hit a NEW
            # waiting episode. A changed pane hash is also a new prompt even
            # when status stayed continuously waiting between polls. Notify
            # either right away (60s flap guard only). The doubling
            # backoff is a reminder cadence for one continuously-waiting
            # prompt, not a mute for the session.
            is_new_prompt = session_id not in prev_waiting or bool(
                prompt_hash and prompt_hash != previous_prompt_hash
            ) or identity_changed
            prompt_cards_sent = 0 if is_new_prompt else cards_sent
            threshold = (
                60
                if is_new_prompt
                else session_notify_threshold(args, prompt_cards_sent)
            )
            if now - last < threshold:
                continue
            max_cards = int(args.session_notify_max_cards)
            if not is_new_prompt and max_cards > 0 and prompt_cards_sent >= max_cards:
                muted.append(
                    {
                        "session_id": session_id,
                        "reason": "card_cap",
                        "cards": prompt_cards_sent,
                    }
                )
                continue
            # A keystroke already proved inert against this exact prompt. Re-carding
            # it just invites the operator to retry something that cannot work.
            if dead_keys.get(f"{session_id[:8]}:{prompt_hash}"):
                muted.append({"session_id": session_id, "reason": "no_effect", "prompt_hash": prompt_hash})
                continue
            options = interaction.get("options") or []
            ask_summary = summarize_waiting_prompt(args, tail)
            headlines = {
                "startup_trust": "Codex 프로젝트 신뢰 확인",
                "maintenance_update": "Codex 유지보수 안내",
                "permission": "에이전트 권한 확인 필요",
                "selection": "에이전트 선택 필요",
                "question": "에이전트 질문",
                "task_ready": "에이전트 작업 완료 · 다음 지시 대기",
                "unknown": "에이전트 확인 필요",
            }
            lines = [
                f"<b>{headlines.get(interaction_kind, headlines['unknown'])}</b>",
                f"⏸ {html.escape(project)} · {html.escape(title)} ({html.escape(tool)})",
            ]
            if ask_summary:
                label = "최근 응답" if interaction_kind == "task_ready" else "요청"
                lines.append(f"{label}: {html.escape(ask_summary)}")
            elif interaction_kind == "task_ready":
                lines.append("에이전트가 이전 응답을 마치고 다음 작업을 기다립니다.")
            elif interaction_kind == "question":
                lines.append("에이전트가 답변을 기다립니다.")
            else:
                lines.append("터미널의 현재 요청을 직접 확인해 주세요.")
            if options and interaction_kind in {
                "permission",
                "selection",
                "startup_trust",
            }:
                lines.append(
                    " · ".join(
                        f"{opt['key']}. {html.escape(sanitize_text(opt['label'], max_chars=24))}"
                        for opt in options
                    )
                )
            if interaction_kind == "maintenance_update":
                lines.append(
                    "현재 세션에서 전역 패키지 업데이트를 실행하지 않습니다. "
                    "On-desk 유지보수 후 세션을 재시작하세요."
                )
            elif interaction_kind == "startup_trust":
                lines.append(
                    "프로젝트 로컬 설정과 hooks를 허용하는 별도 시작 권한입니다."
                )
            elif interaction_kind == "task_ready":
                lines.append("기존 계획의 다음 미완료 작업만 이어서 진행할 수 있습니다.")
            elif interaction_kind == "question":
                lines.append("이 카드에 답장하면 정확한 세션에 전달됩니다.")
            elif interaction_kind == "unknown":
                lines.append("상태가 불명확하여 자동 입력 버튼을 제공하지 않습니다.")
            else:
                lines.append("권한이나 선택 내용을 확인한 뒤 카드 버튼을 누르세요.")
            message = "\n".join(lines)
            interaction_markup = session_interaction_markup(
                interaction,
                session_id=session_id,
                prompt_hash=prompt_hash,
            )
            entry_to_commit = {
                "at": now,
                "cards": 1 if is_new_prompt else prompt_cards_sent + 1,
                "message_id": None,
                "prompt_hash": prompt_hash,
                "prompt_identity": prompt_hash,
                "prompt_observation_hash": prompt_observation_hash,
                "label": f"{project} · {title} ({tool})",
                "project": project,
                "title": title,
                "tool": tool,
                "interaction_kind": interaction_kind,
                "interaction_reason": interaction.get("reason"),
                "remote_session_identity": remote_identity,
            }
            report = result

            def on_delivered(
                message_id: int | None,
                *,
                _session_id: str = session_id,
                _entry: dict[str, Any] = entry_to_commit,
                _message: str = message,
                _project: str = project,
                _title: str = title,
                _tool: str = tool,
                _prompt_hash: str = prompt_hash,
                _prompt_observation_hash: str = prompt_observation_hash,
                _remote_identity: dict[str, Any] = remote_identity,
                _interaction_kind: str = interaction_kind,
                _waiting_count: int = len(waiting),
                _chat_hash: str = str(chat_hash or ""),
                _evidence: dict[str, Any] = payload_evidence(message, interaction_markup),
            ) -> None:
                record_delivery(
                    state, _evidence, message_id=message_id,
                    binding_sha256=str(_remote_identity.get("binding_sha256") or ""),
                    now=time.time(), dry_run=bool(args.dry_run),
                )
                _entry["message_id"] = message_id
                notified[_session_id] = _entry
                if _waiting_count == 1:
                    remember_session_focus(
                        state,
                        _chat_hash,
                        session_id=_session_id,
                        project=_project,
                        title=_title,
                        tool=_tool,
                        prompt_hash=_prompt_hash,
                        prompt_observation_hash=_prompt_observation_hash,
                        source="waiting_card",
                        remote_session_identity=_remote_identity,
                    )
                sent.append(
                    {
                        "session_id": _session_id,
                        "title": _title,
                        "project": _project,
                        "prompt_hash": _prompt_hash,
                        "prompt_identity": _prompt_hash,
                        "prompt_observation_hash": _prompt_observation_hash,
                        "pushed_at": utc_now(),
                        "message_id": message_id,
                        "interaction_kind": _interaction_kind,
                        "remote_session_identity_sha256": _remote_identity.get("binding_sha256"),
                        "payload_evidence": _evidence,
                        "delivery_evidence_status": (
                            "preview" if args.dry_run else
                            "acknowledged" if type(message_id) is int and message_id > 0
                            else "unacknowledged"
                        ),
                    }
                )
                result.update(
                    {
                        "status": "notified",
                        "send_status": "dry_run" if args.dry_run else "sent",
                        "attempted_count": 1,
                        "notified_count": 1,
                        "message_id": message_id,
                        "session_id": _session_id,
                        "message_preview": _message,
                    }
                )

            def on_unconfirmed(
                *, _session_id: str = session_id,
                _entry: dict[str, Any] = entry_to_commit,
            ) -> None:
                # A missing acknowledgement may still mean Telegram accepted
                # the send. Hold this exact request without creating a reply
                # target or counting it as delivered.
                _entry.update({"delivery_unconfirmed": True, "cards": 0, "resolved": True})
                notified[_session_id] = _entry
                clear_session_focus_for_chat_hash(
                    state, chat_hash, session_id=_session_id, sources={"waiting_card"},
                )

            cycle.add(
                NotificationCandidate(
                    event_key=(
                        f"waiting:{session_id}:{prompt_hash}:"
                        f"{remote_identity.get('binding_sha256') or ''}"
                    ),
                    source="session",
                    priority=_session_priority(interaction_kind),
                    message=message,
                    reply_markup=interaction_markup,
                    report=report,
                    on_delivered=on_delivered,
                    on_unconfirmed=on_unconfirmed,
                )
            )
            candidate_count += 1
        if quiet_hours:
            result = {"status": "quiet_hours", "waiting_count": len(waiting)}
            if muted:
                result["muted"] = muted
            if keyboard_cleanup:
                result["keyboard_cleanup"] = keyboard_cleanup
            return result
        try:
            bootstrap_digest = update_orchestrator_bootstrap_digest(
                args,
                config,
                state,
                bootstrap_blocked,
                cycle=cycle,
            )
        except TelegramControlRevokedError:
            raise
        except RemoteOperatorTelegramError as error:
            bootstrap_digest = {
                "status": "send_failed",
                "count": len(bootstrap_blocked),
                "error": sanitize_text(str(error), max_chars=160),
            }
        if candidate_count == 0 and not stopped_pending and not (
            bootstrap_digest and bootstrap_digest.get("status") == "deferred"
        ):
            result["status"] = "no_new_waiting"
        if muted:
            result["muted"] = muted
        if bootstrap_digest:
            result["bootstrap_digest"] = bootstrap_digest
        if keyboard_cleanup:
            result["keyboard_cleanup"] = keyboard_cleanup
        if own_cycle:
            delivery = cycle.deliver()
            result["notification_cycle"] = delivery
            if sent:
                result["status"] = "notified"
            elif delivery.get("status") == "send_failed":
                result["status"] = "send_failed"
            elif delivery.get("status") == "delivered":
                result["status"] = "notified"
        return result
    except TelegramControlRevokedError:
        raise
    except Exception as error:  # noqa: BLE001 - poll loop must never crash here
        return {"status": "scan_failed", "error": sanitize_text(str(error), max_chars=240)}


def scan_and_notify_attention(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    cycle: NotificationCycle | None = None,
) -> dict[str, Any] | None:
    """Collect attention cards; a cycle owns the single delivery boundary."""

    if not args.attention_notify:
        return None
    target_chat_id = str(config.get("target_chat_id") or "").strip()
    if not target_chat_id:
        return {"status": "no_target_chat"}
    try:
        surface = export_workstation_surface(args.forager_bin, args.profile)
        notification = build_attention_notification(
            surface, state, reminder_sec=int(args.attention_reminder_sec)
        )
    except (OSError, RemoteOperatorTelegramError) as error:
        return {"status": "scan_failed", "error": sanitize_text(str(error), max_chars=240)}
    own_cycle = cycle is None
    cycle = cycle or _standalone_notification_cycle(args, config, state)
    fresh = notification.get("fresh_items") or []
    # Prune resolved entries during collection, but never consume a fresh
    # item until that item's candidate succeeds.
    commit_attention_notification(state, notification, delivered_keys=())
    if not fresh:
        return {"status": "no_new_attention", "pending_count": notification.get("pending_count")}
    result: dict[str, Any] = {
        "status": "deferred",
        "pending_count": notification.get("pending_count"),
        "notified_count": 0,
        "items": fresh,
        "attempted_count": 0,
    }
    for item in fresh:
        item_key = str(item.get("key") or "")
        message = render_attention_message(
            profile=args.profile, generated_at=utc_now(), items=[item]
        )
        report = result

        def on_delivered(
            message_id: int | None,
            *,
            _item_key: str = item_key,
            _item: dict[str, Any] = item,
            _message: str = message,
        ) -> None:
            commit_attention_notification(
                state, notification, delivered_keys=(_item_key,)
            )
            result.update(
                {
                    "status": "notified",
                    "send_status": "dry_run" if args.dry_run else "sent",
                    "notified_count": 1,
                    "sent_message_id": message_id,
                    "attempted_count": 1,
                    "items": [_item],
                    "message_preview": _message,
                    "mobile_card_contract": mobile_card_contract(_message),
                }
            )

        cycle.add(
            NotificationCandidate(
                event_key=f"attention:{item_key}",
                source="attention",
                priority=_attention_priority(item),
                message=message,
                reply_markup=None,
                report=report,
                on_delivered=on_delivered,
            )
        )
    if own_cycle:
        delivery = cycle.deliver()
        result["notification_cycle"] = delivery
        if delivery.get("status") == "send_failed":
            result["status"] = "send_failed"
            result["send_status"] = "failed"
    return result


def synchronize_reply_delivery_journal(args: argparse.Namespace) -> None:
    """Repair crash gaps between the outbox and update journal.

    The outbox row is written first. Its presence proves handler processing
    reached the delivery boundary, so a restarted listener can safely advance
    `started` to `effect_committed` without running the handler again.
    """

    journal = latest_update_records(args.update_journal_file)
    deliveries = latest_delivery_records(args.reply_outbox_file)
    for delivery in deliveries.values():
        update_id = int(delivery["update_id"])
        previous = journal.get(update_id)
        if previous is None:
            raise RemoteOperatorStateError(
                f"reply outbox delivery {delivery['delivery_id']} has no update journal entry"
            )
        result = {
            "status": delivery.get("result_status"),
            "effect": delivery.get("effect"),
            "action_outcome": delivery.get("action_outcome"),
            "response_card": delivery.get("response_card"),
            "feedback_workflow": delivery.get("feedback_workflow"),
            "delivery_id": delivery.get("delivery_id"),
        }
        if previous.get("status") == "started":
            mark_effect_committed(
                args.update_journal_file,
                update_id=update_id,
                input_hash=str(delivery["input_hash"]),
                result=result,
            )
            previous = latest_update_records(args.update_journal_file)[update_id]
        if previous.get("status") == "completed" and delivery.get("status") in {
            "queued",
            "failed",
        }:
            raise RemoteOperatorStateError(
                f"completed update {update_id} still has a pending reply delivery"
            )
        if delivery.get("status") == "delivered" and previous.get("status") == "effect_committed":
            complete_update(
                args.update_journal_file,
                update_id=update_id,
                input_hash=str(delivery["input_hash"]),
                result={
                    **result,
                    "send_status": "sent",
                    "sent_message_id": delivery.get("sent_message_id"),
                    "conversation_record_status": "unknown_after_recovery",
                },
            )
        if delivery.get("status") == "revoked" and previous.get("status") == "effect_committed":
            complete_update(
                args.update_journal_file,
                update_id=update_id,
                input_hash=str(delivery["input_hash"]),
                result={
                    **result,
                    "send_status": "revoked",
                    "sent_message_id": None,
                    "conversation_record_status": "not_delivered_control_revoked",
                },
            )


def attempt_reply_delivery(
    args: argparse.Namespace,
    config: dict[str, Any],
    delivery: dict[str, Any],
) -> dict[str, Any]:
    latest = latest_delivery_records(args.reply_outbox_file).get(str(delivery["delivery_id"]))
    if latest is not None and latest.get("delivery_unconfirmed"):
        delivery = latest
    if delivery.get("delivery_unconfirmed"):
        return {"status": "delivery_unconfirmed", "delivery": delivery}
    if not args.dry_run:
        delivery = mark_delivery_started(args.reply_outbox_file, delivery)
    try:
        message_id = send_message(
            config,
            str(delivery["chat_id"]),
            str(delivery["message"]),
            args,
            reply_markup=(
                delivery.get("reply_markup")
                if isinstance(delivery.get("reply_markup"), dict)
                else None
            ),
        )
    except TelegramControlRevokedError:
        raise
    except TelegramDeliveryUnconfirmed:
        held = mark_delivery_unconfirmed(args.reply_outbox_file, delivery)
        return {"status": "delivery_unconfirmed", "delivery": held, "error": held["last_error"]}
    except RemoteOperatorTelegramError as error:
        failed = mark_delivery_failed(
            args.reply_outbox_file,
            delivery,
            error=sanitize_text(str(error), max_chars=240),
        )
        return {
            "status": "failed",
            "delivery": failed,
            "error": failed.get("last_error"),
        }
    delivered = mark_delivery_delivered(
        args.reply_outbox_file,
        delivery,
        message_id=message_id,
    )
    return {
        "status": "delivered",
        "delivery": delivered,
        "sent_message_id": message_id,
        "send_status": "dry_run" if args.dry_run else "sent",
    }


def drain_reply_outbox(
    args: argparse.Namespace, config: dict[str, Any]
) -> dict[str, Any]:
    synchronize_reply_delivery_journal(args)
    pending = pending_deliveries(args.reply_outbox_file)
    if not pending:
        return {"status": "empty", "delivered_count": 0, "remaining_count": 0}
    limit = max(1, int(args.reply_outbox_retry_limit))
    delivered_count = 0
    last_delivery_id = None
    for delivery in pending[:limit]:
        attempt = attempt_reply_delivery(args, config, delivery)
        last_delivery_id = delivery.get("delivery_id")
        if attempt.get("status") != "delivered":
            return {
                "status": attempt.get("status", "failed"),
                "delivered_count": delivered_count,
                "remaining_count": len(pending) - delivered_count,
                "delivery_id": last_delivery_id,
                "error": attempt.get("error"),
            }
        delivered = attempt["delivery"]
        complete_update(
            args.update_journal_file,
            update_id=int(delivered["update_id"]),
            input_hash=str(delivered["input_hash"]),
            result={
                "status": delivered.get("result_status"),
                "effect": delivered.get("effect"),
                "delivery_id": delivered.get("delivery_id"),
                "send_status": attempt.get("send_status"),
                "sent_message_id": attempt.get("sent_message_id"),
                "conversation_record_status": "recorded_before_retry_or_unknown",
            },
        )
        delivered_count += 1
    remaining_count = len(pending) - delivered_count
    return {
        "status": "delivered" if remaining_count == 0 else "pending",
        "delivered_count": delivered_count,
        "remaining_count": remaining_count,
        "delivery_id": last_delivery_id,
    }


def bind_live_control_generation(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    generation = str(config.get("_control_generation_sha256") or "").strip()
    binding = bind_control_generation(state, generation)
    if binding.get("changed"):
        save_state(args.state_file, state)

    mismatched_reply_exists = any(
        str(delivery.get("control_generation_sha256") or "").strip()
        != generation
        for delivery in latest_delivery_records(args.reply_outbox_file).values()
        if delivery.get("status") in {"queued", "failed"}
    )
    revoked: list[dict[str, Any]] = []
    if mismatched_reply_exists:
        # Finish crash ordering locally before revoking a pending external
        # reply. No Telegram request is made in this reconciliation path.
        synchronize_reply_delivery_journal(args)
        revoked = revoke_mismatched_deliveries(
            args.reply_outbox_file,
            current_generation_sha256=generation,
        )
    for delivery in revoked:
        complete_update(
            args.update_journal_file,
            update_id=int(delivery["update_id"]),
            input_hash=str(delivery["input_hash"]),
            result={
                "status": delivery.get("result_status"),
                "effect": delivery.get("effect"),
                "delivery_id": delivery.get("delivery_id"),
                "send_status": "revoked",
                "sent_message_id": None,
                "conversation_record_status": "not_delivered_control_revoked",
            },
        )

    return {
        "status": binding.get("status"),
        "changed": bool(binding.get("changed")),
        "previous_generation_id": public_control_generation_id(
            binding.get("previous_generation")
        ),
        "current_generation_id": public_control_generation_id(generation),
        "previous_offset": binding.get("previous_offset"),
        "cleared_state": binding.get("cleared_state") or {},
        "revoked_reply_count": len(revoked),
    }


def revoke_live_control(
    args: argparse.Namespace,
    config: dict[str, Any],
    error: TelegramControlRevokedError,
) -> dict[str, Any]:
    try:
        current = resolve_telegram_config(args.env_file, required=False)
        current_generation = str(
            current.get("_control_generation_sha256") or ""
        ).strip()
    except RemoteOperatorTelegramError:
        current_generation = "sha256:" + hashlib.sha256(
            (
                "revoked\0"
                + str(error.previous_generation or "")
                + "\0"
                + utc_now()
            ).encode("utf-8")
        ).hexdigest()
    state = load_state(args.state_file)
    revoked_config = dict(config)
    revoked_config["_control_generation_sha256"] = current_generation
    cleanup = bind_live_control_generation(args, revoked_config, state)
    save_state(args.state_file, state)
    result = result_base(args, config, "live_once")
    result.update(
        {
            "status": "control_revoked",
            "reason": "telegram_control_generation_changed",
            "updates_seen": 0,
            "previous_generation_id": public_control_generation_id(
                error.previous_generation
            ),
            "current_generation_id": public_control_generation_id(
                current_generation
            ),
            "cleanup": cleanup,
        }
    )
    return result


def run_once(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    assert_telegram_config_current(config, required=not args.dry_run)
    state = load_state(args.state_file)
    control_binding = bind_live_control_generation(args, config, state)
    outbox_delivery = drain_reply_outbox(args, config)
    if outbox_delivery.get("status") == "failed":
        result = result_base(args, config, "live_once")
        result.update(
            {
                "status": "send_failed",
                "reason": "reply_outbox_delivery_failed",
                "updates_seen": 0,
                "reply_outbox_delivery": outbox_delivery,
            }
        )
        return result
    if outbox_delivery.get("status") == "pending":
        result = result_base(args, config, "live_once")
        result.update(
            {
                "status": "reply_delivery_pending",
                "updates_seen": 0,
                "reply_outbox_delivery": outbox_delivery,
            }
        )
        return result
    updates = get_updates(config, int(state.get("offset") or 0), args)
    result = result_base(args, config, "live_once")
    result.update(
        {
            "status": (
                "reply_delivered"
                if outbox_delivery.get("status") == "delivered"
                and int(outbox_delivery.get("delivered_count") or 0) > 0
                else "no_update"
            ),
            "updates_seen": len(updates),
            "reply_outbox_delivery": outbox_delivery,
            "control_generation_binding": control_binding,
        }
    )
    max_update_id = int(state.get("offset") or 0) - 1
    for update in updates:
        callback_query_id = callback_query_id_for(update)
        callback_data = callback_data_for(update)
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
        message = message_from_update(update)
        if not message:
            if callback_query_id:
                answer_callback_query(
                    config, callback_query_id, args, text="만료되었거나 알 수 없는 버튼입니다."
                )
            continue
        allowed, reason = update_is_allowed(config, message)
        if not allowed:
            if callback_query_id:
                answer_callback_query(
                    config, callback_query_id, args, text="이 작업을 사용할 권한이 없습니다."
                )
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
            if callback_query_id:
                answer_callback_query(
                    config, callback_query_id, args, text="버튼 요청이 비어 있습니다."
                )
            result.update({"status": "ignored", "reason": "empty_message"})
            continue
        if isinstance(update_id, bool) or not isinstance(update_id, int):
            raise RemoteOperatorStateError(
                "allowed Telegram update is missing an integer update_id"
            )
        chat_hash = sha256_short(chat_id_for(message))
        user_hash = sha256_short(user_id_for(message))
        input_hash = sha256_short(
            json.dumps(
                {
                    "update_id": update_id,
                    "message_id": message_id_for(message),
                    "chat_id_hash": chat_hash,
                    "user_id_hash": user_hash,
                    "text": text,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        inbound_event = InboundEvent(
            transport="telegram",
            source="callback" if callback_query_id else "message",
            event_id=update_id,
            message_id=message_id_for(message),
            occurred_at_epoch=message_date_for(message),
            input_hash=input_hash,
            actor_scope_hash=chat_hash,
            actor_hash=user_hash,
            text=text,
        )
        active_session = active_remote_plan_session(state, chat_hash)
        is_plan_callback = PLAN_CALLBACK_RE.fullmatch(callback_data) is not None
        plan_text_owned = bool(
            active_session
            and remote_plan_session_should_handle_text(args, active_session, text)
        )
        routed_intent = route_inbound_event(
            inbound_event,
            active_plan_session=active_session,
            plan_callback=is_plan_callback,
            plan_text_owned=plan_text_owned,
        )
        session_payload = routed_intent.plan_payload
        plan_callback_issue = routed_intent.validation_issue
        session_handles_text = routed_intent.plan_session_input
        preparsed_command = routed_intent.parsed_command
        command_envelope = build_command_envelope(
            update_id=update_id,
            message_id=message_id_for(message),
            message_date=message_date_for(message),
            input_hash=input_hash,
            chat_hash=chat_hash,
            user_hash=user_hash,
            parsed_command=preparsed_command,
            plan_session_input=session_handles_text,
            ttl_sec=args.remote_command_ttl_sec,
        )
        action_candidate = validate_action_candidate(
            inbound_event,
            routed_intent,
            command_envelope,
        )
        completed_record = begin_update(
            args.update_journal_file,
            update_id=update_id,
            input_hash=input_hash,
            chat_hash=chat_hash,
            user_hash=user_hash,
            command_envelope=command_envelope,
        )
        if completed_record is not None:
            if callback_query_id:
                answer_callback_query(
                    config,
                    callback_query_id,
                    args,
                    text="이미 처리된 요청입니다.",
                )
                callback_message_id = message_id_for(message)
                if callback_message_id is not None and callback_consumes_card(
                    callback_data
                ):
                    clear_inline_keyboard(
                        config,
                        chat_id_for(message),
                        callback_message_id,
                        args,
                    )
            result.update(
                {
                    "status": "already_processed",
                    "reason": "durable_update_journal_completed",
                    "processed_update_id": update_id,
                    "updates_seen": len(updates),
                    "update_journal_status": "completed",
                    "recovered_effect": completed_record.get("effect"),
                }
            )
            break
        # An explicitly authorized retry can restore the original durable
        # envelope in begin_update. Rebind the candidate to the exact envelope
        # that will govern this handler attempt.
        action_candidate = validate_action_candidate(
            inbound_event,
            routed_intent,
            command_envelope,
        )
        session_context: dict[str, Any] | None = None
        authority_issue = (
            action_candidate.primary_issue
            if action_candidate.primary_issue != plan_callback_issue
            else None
        )
        if authority_issue:
            rendered = result_base(args, config, "live_once")
            refusal_reason = str(authority_issue)
            message_preview = render_command_envelope_refusal_message(
                profile=args.profile,
                reason=refusal_reason,
            )
            rendered.update(
                {
                    "status": "command_refused",
                    "reason": refusal_reason,
                    "command_text": sanitize_text(text, max_chars=400),
                    "parsed_command": {
                        "supported": False,
                        "command": "command_envelope_refused",
                        "reason": refusal_reason,
                        "requested_command": preparsed_command.get("command"),
                    },
                    "dispatch_result": {
                        "ok": False,
                        "kind": "remote_command_envelope",
                        "stage": "command_envelope_refused",
                        "error": refusal_reason,
                    },
                    "projection": None,
                    "message_preview": message_preview,
                    "mobile_card_contract": mobile_card_contract(message_preview),
                }
            )
            attach_choice_surface(rendered, None)
        elif plan_callback_issue:
            rendered = render_remote_plan_callback_refusal(
                args,
                config,
                mode="live_once",
                session=active_session,
                reason=plan_callback_issue,
            )
        elif active_session and session_payload:
            assert_telegram_config_current(config, required=not args.dry_run)
            rendered = handle_remote_plan_session_input(
                args,
                config,
                state,
                chat_hash=chat_hash,
                session=active_session,
                text=session_payload,
                mode="live_once",
            )
        elif active_session and routed_intent.plan_text_owned:
            assert_telegram_config_current(config, required=not args.dry_run)
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
            session_context = pending_session_action_context(
                state,
                preparsed_command,
            )
            if session_context is None:
                session_context = replied_session_context(state, message)
            if session_context is None:
                session_context = session_focus_for_chat_hash(
                    state,
                    chat_hash,
                    max_age_sec=args.context_max_age_sec,
                )
            sticky_focus = project_focus_for_chat_hash(
                state,
                chat_hash,
                max_age_sec=args.project_focus_max_age_sec,
            )
            consumed_key = consumed_continue_key(preparsed_command, chat_hash, user_hash, message_id_for(message) if callback_query_id else None)
            consumed = state.get("consumed_continue_actions", {})
            if not isinstance(consumed, dict) or any(not isinstance(row, dict) for row in consumed.values()):
                raise RemoteOperatorTelegramError("invalid consumed action ledger")
            if consumed_key and isinstance(consumed, dict) and consumed_key in consumed:
                session_context = {"already_processed": True}
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
                sticky_focus_key=str((sticky_focus or {}).get("project_key") or "") or None,
                session_context=session_context,
                remote_plan_session_id=str((active_session or {}).get("session_id") or "")
                or None,
            )
        rendered["updates_seen"] = len(updates)
        rendered["remote_command_envelope"] = command_envelope
        rendered["inbound_event"] = inbound_event.public_record()
        rendered["routed_intent"] = routed_intent.public_record()
        rendered["action_candidate"] = action_candidate.public_record()
        if session_handles_text and isinstance(active_session, dict):
            rendered.setdefault(
                "remote_plan_session_id",
                str(active_session.get("session_id") or "") or None,
            )
        rendered["operator_identity"] = operator_identity_for(
            config, chat_hash=chat_hash, user_hash=user_hash
        )
        if isinstance(update_id, int):
            rendered["processed_update_id"] = update_id
        parsed_command = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
        focus = rendered.get("project_focus")
        if (
            isinstance(focus, dict)
            and focus.get("source") in {"mention", "explicit"}
            and focus.get("key")
        ):
            state.setdefault("chat_focus_by_chat", {})[chat_hash] = {
                "project_key": focus.get("key"),
                "updated_at": utc_now(),
            }
        if parsed_command.get("command") == "remember":
            remember_text = str(parsed_command.get("remember_text") or text)
            target_profile = str(parsed_command.get("wiki_profile") or args.profile)
            if parsed_command.get("remember_target_status") == "ambiguous":
                rendered.update(
                    {
                        "wiki_candidate_recorded": False,
                        "wiki_candidate_status": "blocked",
                        "wiki_candidate_error": "ambiguous_project_reference",
                    }
                )
            else:
            # A persistence failure must not raise before the offset save:
            # that would re-deliver the same update on every poll.
                try:
                    focus_value = rendered.get("project_focus")
                    focus_key = (
                        str(focus_value.get("key") or "")
                        if isinstance(focus_value, dict)
                        else ""
                    )
                    record_result = record_remember_candidate(
                        profile=target_profile,
                        text=remember_text,
                        chat_hash=chat_hash,
                        user_hash=user_hash,
                        message_id=message_id_for(message),
                        project_key=focus_key or None,
                    )
                except (OSError, RemoteOperatorTelegramError) as error:
                    record_result = {
                        "wiki_candidate_recorded": False,
                        "wiki_candidate_status": "failed",
                        "wiki_candidate_error": sanitize_text(str(error), max_chars=240),
                    }
                rendered.update(record_result)
                rendered["message_preview"] = render_wiki_candidate_message(
                    profile=target_profile,
                    generated_at=rendered["generated_at"],
                    remember_text=remember_text,
                    record_result=record_result,
                )
                rendered["mobile_card_contract"] = mobile_card_contract(
                    rendered["message_preview"]
                )
        if parsed_command.get("command") in {"feedback", "plan_request"}:
            feedback_context = last_context_for_chat_hash(
                state,
                chat_hash,
                max_age_sec=args.context_max_age_sec,
            )
            feedback_text = str(parsed_command.get("feedback_text") or parsed_command.get("plan_text") or text)
            feedback_outcome = process_feedback_workflow(
                args,
                config,
                state,
                chat_hash=chat_hash,
                user_hash=user_hash,
                message_id=message_id_for(message),
                text=feedback_text,
                parsed_command=parsed_command,
                feedback_context=feedback_context,
            )
            apply_feedback_workflow_presentation(
                args,
                rendered,
                feedback_outcome,
                feedback_text=feedback_text,
                parsed_command=parsed_command,
                feedback_context=feedback_context,
            )
        if parsed_command.get("command") == "chat":
            agent_intent = (
                parsed_command.get("agent_intent")
                if isinstance(parsed_command.get("agent_intent"), dict)
                else {}
            )
            wants_session_message = (
                agent_intent.get("status") == "classified"
                and agent_intent.get("intent") == "session_message"
                and agent_intent.get("session_target_status") == "resolved"
                and not agent_intent.get("requires_clarification")
            )
            if wants_session_message:
                target_session_id = str(agent_intent.get("session_id") or "")
                session_message = str(agent_intent.get("session_message") or "")
                expected_prompt_identity = ""
                context_session_id = str((session_context or {}).get("session_id") or "")
                same_context_session = bool(
                    target_session_id
                    and context_session_id
                    and (
                        target_session_id.startswith(context_session_id)
                        or context_session_id.startswith(target_session_id)
                    )
                )
                if same_context_session and str((session_context or {}).get("source") or "") in {
                    "reply_card",
                    "waiting_card",
                }:
                    expected_prompt_identity = str(
                        (session_context or {}).get("prompt_identity")
                        or (session_context or {}).get("prompt_hash")
                        or ""
                    )
                if (
                    float(agent_intent.get("confidence") or 0.0)
                    < SESSION_MESSAGE_MIN_CONFIDENCE
                ):
                    preview = "\n".join(
                        [
                            "<b>어느 로컬 에이전트에 전달할지 확실하지 않습니다</b>",
                            "프로젝트나 세션 이름을 포함해 다시 말씀해 주세요.",
                            "예: forager Codex에게 전체 테스트를 다시 돌려줘",
                        ]
                    )
                    rendered["message_preview"] = preview
                    rendered["mobile_card_contract"] = mobile_card_contract(preview)
                    rendered["session_message_status"] = "clarification_required"
                else:
                    render_session_message_delivery(
                        args,
                        rendered,
                        session_id=target_session_id,
                        message=session_message,
                        expected_prompt_identity=expected_prompt_identity,
                        expected_interaction_kind=str(
                            (session_context or {}).get("interaction_kind") or ""
                        ),
                        require_exact_id=True,
                        expected_identity=(session_context or {}).get(
                            "remote_session_identity"
                        ),
                        require_remote_identity=bool(
                            same_context_session
                            and str((session_context or {}).get("source") or "")
                            in {"reply_card", "waiting_card"}
                        ),
                    )
            if agent_intent.get("status") == "classified":
                # The chat agent owns the routing decision (function-calling
                # style); keyword markers are only the no-agent fallback.
                wants_capture = (
                    agent_intent.get("intent") == "delegate_work"
                    and not wants_session_message
                )
                capture_note = str(agent_intent.get("delegation_goal") or "").strip() or text
            else:
                relay_fallback = looks_like_session_relay_request(
                    text, has_session_context=bool(session_context)
                )
                wants_capture = (
                    not relay_fallback
                    and classify_feedback_kind(text) == "planning_request"
                )
                capture_note = text
                if relay_fallback:
                    preview = "\n".join(
                        [
                            "<b>로컬 에이전트에 전달하지 않았습니다</b>",
                            "평문 대상 해석 모델을 사용할 수 없습니다.",
                            "모델 연결이 복구되면 같은 지시를 다시 보내 주세요.",
                        ]
                    )
                    rendered["message_preview"] = preview
                    rendered["mobile_card_contract"] = mobile_card_contract(preview)
                    rendered["session_message_status"] = "agent_runtime_unavailable"
            pending_now = (state.get("pending_dispatch_confirmations_by_chat") or {}).get(chat_hash)
            if (
                wants_capture
                and not (isinstance(pending_now, dict) and confirmation_is_fresh(pending_now))
                # An active plan session already owns this chat's plain text;
                # questions about the plan must stay chat, not spawn a second
                # capture card.
                and active_remote_plan_session(state, chat_hash) is None
            ):
                # Natural-language delegation: offer to capture it as a plan
                # candidate. The tap is the approval; chat alone records nothing.
                capture = build_confirmation(
                    kind="plan_capture", target_id="plan", action_kind="capture",
                    observed_hash="", note=sanitize_text(capture_note, max_chars=380),
                    chat_hash=chat_hash, user_hash=user_hash, ttl_sec=900,
                )
                store_confirmation(state, chat_hash, user_hash, capture)
                preview = "\n".join([
                    "<b>계획 후보로 등록할까요?</b>",
                    sanitize_text(capture_note, max_chars=150),
                    "확인 시 검토와 야간 실행 대기열에 계획 후보로 기록됩니다.",
                    "다음 조치: 확인 또는 취소",
                ])
                rendered["message_preview"] = preview
                rendered["mobile_card_contract"] = mobile_card_contract(preview)
                attach_choice_surface(
                    rendered,
                    {
                        "context_kind": "dispatch_confirm",
                        "confirmation_token": str(capture.get("token") or ""),
                    },
                )
                rendered["plan_capture_offered"] = True
        if parsed_command.get("command") in {
            "decision",
            "recover",
            "dispatch",
            "run",
            "cancel_task",
            "terminate_task",
            "session_approve",
            "session_deny",
            "pause",
            "resume",
            "confirm",
            "cancel",
        }:
            # Guarded remote execution. Every branch catches its own errors so
            # nothing raises before the offset save and re-delivers the update.
            resolve_dispatch_confirmation(
                args, config, state, chat_hash, user_hash, rendered, parsed_command
            )
            plan_text = rendered.pop("captured_plan_text", None)
            if plan_text:
                synthetic = {
                    "command": "plan_request", "feedback_kind": "planning_request",
                    "plan_text": plan_text, "feedback_text": plan_text,
                    "reason": "plan_capture_confirm",
                }
                capture_context = last_context_for_chat_hash(
                    state, chat_hash, max_age_sec=args.context_max_age_sec,
                )
                capture_outcome = process_feedback_workflow(
                    args,
                    config,
                    state,
                    chat_hash=chat_hash,
                    user_hash=user_hash,
                    message_id=message_id_for(message),
                    text=plan_text,
                    parsed_command=synthetic,
                    feedback_context=capture_context,
                )
                apply_feedback_workflow_presentation(
                    args,
                    rendered,
                    capture_outcome,
                    feedback_text=plan_text,
                    parsed_command=synthetic,
                    feedback_context=capture_context,
                )
        dispatch_result = (
            rendered.get("dispatch_result")
            if isinstance(rendered.get("dispatch_result"), dict)
            else {}
        )
        if dispatch_result.get("kind") == "session_message" and dispatch_result.get("ok"):
            delivered_session = str(dispatch_result.get("session_id") or "")
            previous_card = state.get("waiting_notified_by_session", {}).get(delivered_session)
            if isinstance(previous_card, dict) and previous_card.get("interaction_kind") == "task_ready":
                previous_card["task_ready_input_confirmed"] = True
                previous_card.pop("task_ready_candidate_identity", None)
            consumed_key = consumed_continue_key(parsed_command, chat_hash, user_hash, message_id_for(message) if callback_query_id else None)
            if consumed_key:
                consumed = state.setdefault("consumed_continue_actions", {})
                consumed[consumed_key] = {"at": utc_now(), "update_id": update_id}
                while len(consumed) > 256:
                    oldest = min(consumed, key=lambda key: str(consumed[key].get("at") or ""))
                    del consumed[oldest]
            remember_session_focus(
                state,
                chat_hash,
                session_id=str(dispatch_result.get("session_id") or ""),
                project=str(dispatch_result.get("project") or ""),
                title=str(dispatch_result.get("session_title") or ""),
                tool=str(dispatch_result.get("tool") or ""),
                source="delivered_message",
            )
        annotate_result_effect(rendered)
        action_outcome = finalize_action_outcome(action_candidate, rendered)
        rendered["action_outcome"] = action_outcome.public_record()
        response_card = finalize_response_card(
            action_outcome,
            rendered,
            mobile_contract_builder=mobile_card_contract,
            choice_contract_builder=choice_surface_contract,
        )
        rendered["message_preview"] = response_card.message
        rendered["reply_markup_preview"] = response_card.reply_markup
        rendered["mobile_card_contract"] = response_card.mobile_contract
        rendered["choice_surface_contract"] = response_card.choice_contract
        rendered["response_card"] = response_card.public_record()
        # Persist handler-owned state and the reply payload before attempting
        # delivery. A restart can then retry the reply without re-running the
        # guarded effect handler.
        save_state(args.state_file, state)
        delivery = queue_reply(
            args.reply_outbox_file,
            update_id=update_id,
            input_hash=input_hash,
            chat_id=chat_id_for(message),
            message=response_card.message,
            reply_markup=response_card.reply_markup,
            result=rendered,
            control_generation_sha256=str(
                config.get("_control_generation_sha256") or ""
            ),
        )
        rendered["delivery_id"] = delivery["delivery_id"]
        mark_effect_committed(
            args.update_journal_file,
            update_id=update_id,
            input_hash=input_hash,
            result=rendered,
        )
        # Callback feedback is external state too. Acknowledge and consume the
        # originating card only after the handler state, queued reply, and
        # effect-committed journal record are durable.
        if callback_query_id:
            answer_callback_query(
                config,
                callback_query_id,
                args,
                text=callback_outcome_text(rendered),
            )
            callback_message_id = message_id_for(message)
            if callback_message_id is not None and callback_consumes_card(callback_data):
                clear_inline_keyboard(
                    config,
                    chat_id_for(message),
                    callback_message_id,
                    args,
                )
        delivery_attempt = attempt_reply_delivery(args, config, delivery)
        if delivery_attempt.get("status") == "delivered":
            message_id = delivery_attempt.get("sent_message_id")
            rendered["send_status"] = delivery_attempt.get("send_status")
        else:
            message_id = None
            uncertain = delivery_attempt.get("status") == "delivery_unconfirmed"
            rendered["status"] = "delivery_unconfirmed" if uncertain else "send_failed"
            rendered["send_status"] = "unconfirmed" if uncertain else "failed"
            rendered["send_error"] = delivery_attempt.get("error")
        rendered["sent_message_id"] = message_id
        remember_context_for_chat_hash(state, chat_hash, rendered)
        record_session_input_outcome(state, rendered)
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
            sent_text = telegram_message_text(rendered.get("message_preview"))
            if sent_text:
                append_chat_history(state, chat_hash, role="assistant", text=sent_text)
        try:
            record_conversation_event(
                args,
                rendered,
                chat_hash=chat_hash,
                user_hash=sha256_short(user_id_for(message)),
                message_id=message_id_for(message),
            )
            rendered["conversation_record_status"] = "recorded"
        except OSError as error:
            # The inbound update must still advance when the optional audit
            # ledger is temporarily unavailable; otherwise Telegram replays
            # the same guarded action.
            rendered["conversation_record_status"] = "failed"
            rendered["conversation_record_error"] = sanitize_text(
                str(error), max_chars=160
            )
        if delivery_attempt.get("status") == "delivered":
            complete_update(
                args.update_journal_file,
                update_id=update_id,
                input_hash=input_hash,
                result=rendered,
            )
            rendered["update_journal_status"] = "completed"
        else:
            rendered["update_journal_status"] = "effect_committed"
            rendered["reply_delivery_pending"] = True
        if int(outbox_delivery.get("delivered_count") or 0) > 0:
            rendered["reply_outbox_delivery"] = outbox_delivery
        result = rendered
        break
    attention = None
    autonomy = None
    session_notification = None
    notification_cycle = None
    notification_state_before = None
    # Do not stack proactive cards behind a failed reply. The next poll first
    # drains the durable reply outbox, then resumes notification scans.
    if result.get("status") not in {"send_failed", "delivery_unconfirmed"}:
        notification_cycle = _standalone_notification_cycle(args, config, state)
        notification_state_before = copy.deepcopy(state)
        attention = scan_and_notify_attention(args, config, state, cycle=notification_cycle)
        if attention is not None:
            result["attention_notification"] = attention
        autonomy = scan_and_propose_autonomy(args, config, state, cycle=notification_cycle)
        if autonomy is not None:
            result["autonomy_proposal"] = autonomy
        session_notification = scan_and_notify_waiting_sessions(
            args, config, state, cycle=notification_cycle
        )
        if session_notification is not None:
            result["session_notification"] = session_notification
        delivery = notification_cycle.deliver()
        result["notification_cycle"] = delivery
        if session_notification is not None:
            digest = session_notification.get("bootstrap_digest")
            if isinstance(digest, dict) and digest.get("status") in {
                "notified", "send_failed"
            }:
                session_notification.update({
                    "status": digest["status"],
                    "send_status": digest.get("send_status"),
                    "notified_count": int(digest.get("notified_count") or 0),
                })
        if delivery.get("status") == "send_failed":
            result["proactive_notification_status"] = "send_failed"
            if result.get("status") == "no_update":
                result["status"] = "send_failed"
        elif delivery.get("status") in {"delivery_unconfirmed", "delivery_unconfirmed_hold"}:
            result["proactive_notification_status"] = delivery["status"]
            if result.get("status") == "no_update":
                result["status"] = delivery["status"]
        elif delivery.get("status") == "delivered":
            result["proactive_notification_status"] = "delivered"
    offset_advanced = max_update_id >= int(state.get("offset") or 0)
    if offset_advanced:
        state["offset"] = max_update_id + 1
    state_changed = (
        notification_state_before is not None
        and state != notification_state_before
    )
    if (
        offset_advanced
        or state_changed
    ):
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
            "status_counts": {},
            "consecutive_error_count": 0,
            "last_error_at": None,
            "last_success_at": None,
            "last_result": None,
            "last_handled_result": None,
        }
    )
    return result


def update_loop_summary(summary: dict[str, Any], result: dict[str, Any]) -> None:
    summary["poll_count"] = int(summary.get("poll_count") or 0) + 1
    summary["updates_seen"] = int(summary.get("updates_seen") or 0) + int(result.get("updates_seen") or 0)
    summary["last_result"] = result
    status = str(result.get("status") or "unknown")
    status_counts = summary.setdefault("status_counts", {})
    status_counts[status] = int(status_counts.get(status) or 0) + 1
    if status in {"poll_error", "send_failed", "delivery_unconfirmed", "delivery_unconfirmed_hold", "loop_error", "state_error"}:
        summary["consecutive_error_count"] = (
            int(summary.get("consecutive_error_count") or 0) + 1
        )
        summary["last_error_at"] = result.get("generated_at") or utc_now()
    else:
        summary["consecutive_error_count"] = 0
        summary["last_success_at"] = result.get("generated_at") or utc_now()
    if status not in {"no_update", "poll_error", "loop_error", "state_error"}:
        summary["handled_result_count"] = int(summary.get("handled_result_count") or 0) + 1
        summary["last_handled_result"] = result


def acquire_listener_lock(path: pathlib.Path) -> int:
    """Hold one local continuous poller so Telegram long polling stays single-owner."""

    try:
        import fcntl
    except ImportError as error:
        raise RemoteOperatorTelegramError(
            "continuous Telegram polling requires local file-lock support"
        ) from error
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise RemoteOperatorTelegramError(
            f"another Telegram listener already holds {path}"
        ) from error
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


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


def loop_state_error_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    error: RemoteOperatorStateError,
) -> dict[str, Any]:
    result = result_base(args, config, "live_once")
    result.update(
        {
            "status": "state_error",
            "updates_seen": 0,
            "reason": "listener_state_unreadable",
            "error": sanitize_text(str(error), max_chars=240),
        }
    )
    return result


def loop_backoff_if_needed(
    args: argparse.Namespace,
    result: dict[str, Any],
    consecutive_errors: int,
) -> int:
    status = str(result.get("status") or "")
    if status not in {"poll_error", "send_failed", "loop_error", "state_error"}:
        return 0
    consecutive_errors += 1
    if args.max_polls is None:
        sleep_sec = min(max(0, int(args.poll_error_backoff_sec)) * consecutive_errors, 60)
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return consecutive_errors


def result_carries_push(result: dict[str, Any]) -> bool:
    """True when a proactive card went out on this poll, even with no inbound update.

    Quiet polls report status "no_update", which run_loop does not print; a
    push buried in one would leave no journal trace, making notify latency
    unmeasurable.
    """

    cycle = result.get("notification_cycle")
    if isinstance(cycle, dict) and int(cycle.get("delivered_count") or 0) > 0:
        return True
    session = result.get("session_notification")
    if isinstance(session, dict) and session.get("status") == "notified":
        return True
    attention = result.get("attention_notification")
    if isinstance(attention, dict) and attention.get("status") == "notified":
        return True
    autonomy = result.get("autonomy_proposal")
    return isinstance(autonomy, dict) and autonomy.get("status") == "proposed"


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
            except TelegramControlRevokedError as error:
                result = revoke_live_control(args, config, error)
                update_loop_summary(summary, result)
                summary["status"] = "control_revoked"
                summary["control_revocation"] = result
                if status_path:
                    write_json(status_path, summary)
                return summary
            except RemoteOperatorStateError as error:
                result = loop_state_error_result(args, config, error)
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
            if max_polls is None and (
                result.get("status") != "no_update" or result_carries_push(result)
            ):
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
        if args.reply_outbox_retry_limit < 1:
            raise RemoteOperatorTelegramError(
                "--reply-outbox-retry-limit must be at least 1"
            )
        journal_tool_count = sum(
            [
                bool(args.journal_inspect),
                bool(args.journal_reconcile),
                bool(args.journal_compact),
                bool(args.receipt_inspect),
            ]
        )
        if journal_tool_count > 1:
            raise RemoteOperatorTelegramError(
                "journal and receipt inspection/reconciliation modes cannot be combined"
            )
        if args.receipt_session_id and not args.receipt_inspect:
            raise RemoteOperatorTelegramError(
                "--receipt-session-id requires --receipt-inspect"
            )
        if args.journal_reconcile:
            if args.journal_update_id is None:
                raise RemoteOperatorTelegramError(
                    "--journal-reconcile requires --journal-update-id"
                )
            if not str(args.journal_reason or "").strip():
                raise RemoteOperatorTelegramError(
                    "--journal-reconcile requires --journal-reason"
                )
            if args.dry_run or args.once or args.max_polls is not None:
                raise RemoteOperatorTelegramError(
                    "--journal-reconcile cannot be combined with listener or dry-run modes"
                )
        if args.journal_inspect:
            result = journal_inspection(
                args.update_journal_file,
                update_id=args.journal_update_id,
            )
            result["reply_outbox"] = outbox_inspection(args.reply_outbox_file)
            emit_result(args, result)
            return 0
        if args.receipt_inspect:
            if args.dry_run or args.once or args.max_polls is not None:
                raise RemoteOperatorTelegramError(
                    "--receipt-inspect cannot be combined with listener or dry-run modes"
                )
            result = inspect_remote_receipts(
                args.remote_plan_artifact_dir,
                args.update_journal_file,
                requested_session_id=args.receipt_session_id,
                update_id=args.journal_update_id,
            )
            emit_result(args, result)
            return 0
        if args.journal_compact:
            if args.dry_run or args.once or args.max_polls is not None:
                raise RemoteOperatorTelegramError(
                    "--journal-compact cannot be combined with listener or dry-run modes"
                )
            listener_lock = acquire_listener_lock(args.listener_lock_file)
            try:
                state = load_state(args.state_file)
                synchronize_reply_delivery_journal(args)
                offset = int(state.get("offset") or 0)
                journal_compaction = compact_update_journal(
                    args.update_journal_file,
                    committed_before_update_id=offset,
                )
                outbox_compaction = compact_reply_outbox(
                    args.reply_outbox_file,
                    committed_before_update_id=offset,
                )
            finally:
                os.close(listener_lock)
            result = {
                "schema": "remote_operator_telegram_journal_compaction.v1",
                "generated_at": utc_now(),
                "status": "compacted",
                "trusted_offset": offset,
                "update_journal": journal_compaction,
                "reply_outbox": outbox_compaction,
                "read_only": False,
                "mutation_authorized": True,
                "authority_domain": "telegram_update_journal",
                "approval_authorized": False,
            }
            emit_result(args, result)
            return 0
        if args.journal_reconcile:
            listener_lock = acquire_listener_lock(args.listener_lock_file)
            try:
                result = reconcile_update(
                    args.update_journal_file,
                    update_id=args.journal_update_id,
                    resolution=args.journal_reconcile,
                    reason=args.journal_reason,
                )
            finally:
                os.close(listener_lock)
            emit_result(args, result)
            return 0
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
                "WARNING: --enable-runtime-dispatch is ON. An authenticated Telegram operator can "
                "queue arbitrary commands for `forager offdesk tick`. Treat this as remote "
                "command execution and keep the operator identities and bot token locked down.",
                file=sys.stderr,
                flush=True,
            )
        if args.once:
            try:
                result = run_once(args, config)
            except TelegramControlRevokedError as error:
                result = revoke_live_control(args, config, error)
        else:
            listener_lock = acquire_listener_lock(args.listener_lock_file)
            try:
                result = run_loop(args, config)
            finally:
                os.close(listener_lock)
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
