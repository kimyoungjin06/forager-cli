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
import http.client
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from offdesk_llm_endpoint import (
    DEFAULT_CODING_MODEL_CANDIDATES,
    LlmProviderError,
    call_ollama_json,
    default_ollama_base_urls,
    provider_status,
    resolve_provider_config,
    select_provider_runtime as select_llm_provider_runtime,
)


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
DEFAULT_AGENT_CONFIG_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_AGENT_CONFIG",
        str(pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")) / "forager" / "config.toml"),
    )
)

RESULT_SCHEMA = "remote_operator_telegram_adapter_result.v1"
MOBILE_CARD_CONTRACT_SCHEMA = "telegram_mobile_card_contract.v1"
CHOICE_SURFACE_CONTRACT_SCHEMA = "telegram_choice_surface_contract.v1"
INTERACTION_CONTEXT_SCHEMA = "telegram_interaction_context.v1"
HEALTH_SCHEMA = "remote_operator_telegram_health.v1"
ACTION_READINESS_SCHEMA = "telegram_action_readiness.v1"
AGENT_INTENT_SCHEMA = "telegram_agent_intent.v1"
REMOTE_PLAN_SESSION_SCHEMA = "telegram_remote_plan_session.v1"
PROJECT_CANDIDATE_SCHEMA = "telegram_remote_project_candidate.v1"
PROJECT_INIT_PREVIEW_SCHEMA = "telegram_remote_project_init_preview.v1"
PROJECT_INIT_RUN_SCHEMA = "telegram_remote_project_init_run.v1"
PLAN_DRAFT_SCHEMA = "telegram_remote_plan_draft.v1"
PLAN_REGISTRATION_SCHEMA = "telegram_remote_plan_registration.v1"
PLAN_REVIEW_SCHEMA = "telegram_remote_plan_review.v1"
PLAN_LAUNCH_PREP_SCHEMA = "telegram_remote_plan_launch_prep.v1"
PLAN_GATE_REQUEST_SCHEMA = "telegram_remote_plan_gate_request.v1"
PLAN_GATE_RESOLUTION_SCHEMA = "telegram_remote_plan_gate_resolution.v1"
PLAN_EXECUTION_BRIEF_SCHEMA = "telegram_remote_plan_execution_brief.v1"
PLAN_ENQUEUE_HANDOFF_SCHEMA = "telegram_remote_plan_enqueue_handoff.v1"
PLAN_WORKLOAD_BINDING_SCHEMA = "telegram_remote_plan_workload_binding.v1"
PLAN_ENQUEUE_RUN_SCHEMA = "telegram_remote_plan_enqueue_run.v1"
PLAN_RUNTIME_START_SCHEMA = "telegram_remote_plan_runtime_start.v1"
PLAN_RUNTIME_MONITOR_SCHEMA = "telegram_remote_plan_runtime_monitor.v1"
PLAN_CLOSEOUT_PACKET_SCHEMA = "telegram_remote_plan_closeout_packet.v1"
PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA = "telegram_remote_plan_closeout_review_handoff.v1"
PLAN_CLOSEOUT_VERDICT_SCHEMA = "telegram_remote_plan_closeout_verdict.v1"
REMOTE_PLAN_SESSION_CONTEXT_KIND = "remote_plan_project_selection"
REMOTE_PLAN_INIT_CONTEXT_KIND = "remote_plan_init_review"
MOBILE_CARD_MAX_LINES = 5
PLAN_DRAFT_AUTHORITY_DENIALS = [
    "enqueue",
    "launch",
    "approval",
    "file movement",
    "archive",
    "delete",
    "wiki promotion",
    "accepted truth",
]
MOBILE_CARD_MAX_CHARS = 360
DEFAULT_AGENT_BASE_URLS = (
    *default_ollama_base_urls(),
)
DEFAULT_AGENT_MODEL_CANDIDATES = DEFAULT_CODING_MODEL_CANDIDATES
MOBILE_CARD_FORBIDDEN_TERMS = (
    "Forager Remote Status",
    "Read-only",
    "상태:",
    "다음:",
    "맥락:",
    "기준 ",
    "검증:",
    "sha256:",
    "dispatch",
    "shell",
    "launch-prep",
    "runtime_handle_alive",
)
BUTTON_COMMAND_ALIASES = {
    "상태": "/status",
    "승인 대기": "/pending",
    "전체 승인": "/pending --all",
    "계획": "/plans --latest",
    "도움말": "/help",
}
CORE_BUTTON_LABELS = ("상태", "승인 대기", "계획", "도움말")
ALLOWED_COMMANDS = ("status", "pending", "plans", "show", "help", "feedback")
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


class RemoteOperatorTelegramError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


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


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_file(path: pathlib.Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise RemoteOperatorTelegramError(f"telegram env file not found: {path}")
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def unique_nonempty(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def arg_was_provided(flag: str) -> bool:
    return any(raw == flag or raw.startswith(flag + "=") for raw in sys.argv[1:])


def resolve_agent_config(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return resolve_provider_config(
            config_file=args.agent_config_file,
            section_paths=(
                ("offdesk", "remote_operator", "agent"),
                ("remote_operator", "agent"),
                ("remote_operator", "telegram", "agent"),
                ("offdesk", "llm", "provider"),
                ("llm", "provider"),
            ),
            mode=str(args.agent_intent_mode or "auto"),
            mode_explicit=arg_was_provided("--agent-intent-mode"),
            provider=args.agent_provider,
            provider_explicit=arg_was_provided("--agent-provider"),
            base_urls=args.agent_base_url,
            models=args.agent_model,
            model_candidates=csv_values(args.agent_model_candidates)
            + list(DEFAULT_AGENT_MODEL_CANDIDATES),
            timeout_sec=int(args.agent_timeout_sec),
            timeout_explicit=arg_was_provided("--agent-timeout-sec"),
            num_ctx=int(args.agent_num_ctx),
            num_ctx_explicit=arg_was_provided("--agent-num-ctx"),
            num_predict=int(args.agent_num_predict),
            num_predict_explicit=arg_was_provided("--agent-num-predict"),
            env_mode_key="OFFDESK_REMOTE_OPERATOR_AGENT_INTENT_MODE",
            env_provider_key="OFFDESK_REMOTE_OPERATOR_AGENT_PROVIDER",
            env_base_url_keys=(
                "OFFDESK_REMOTE_OPERATOR_AGENT_BASE_URL",
                "OFFDESK_LLM_BASE_URL",
                "OLLAMA_BASE_URL",
            ),
            env_model_keys=(
                "OFFDESK_REMOTE_OPERATOR_AGENT_MODELS",
                "OFFDESK_LLM_MODELS",
                "OFFDESK_OLLAMA_MODEL",
                "OFFDESK_LLM_MODEL",
            ),
            env_timeout_key="OFFDESK_REMOTE_OPERATOR_AGENT_TIMEOUT_SEC",
            env_num_ctx_key="OFFDESK_REMOTE_OPERATOR_AGENT_NUM_CTX",
            env_num_predict_key="OFFDESK_REMOTE_OPERATOR_AGENT_NUM_PREDICT",
            default_provider="ollama",
            default_base_urls=list(DEFAULT_AGENT_BASE_URLS),
            default_models=list(DEFAULT_AGENT_MODEL_CANDIDATES),
        )
    except LlmProviderError as error:
        raise RemoteOperatorTelegramError(str(error)) from error


def select_agent_runtime(agent_config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return select_llm_provider_runtime(agent_config)
    except LlmProviderError as error:
        raise RemoteOperatorTelegramError(str(error)) from error


def build_agent_intent_prompt(
    *,
    feedback_text: str,
    deterministic_feedback_kind: str,
    feedback_context: dict[str, Any] | None,
) -> str:
    context = feedback_context if isinstance(feedback_context, dict) else {}
    payload = {
        "telegram_text": sanitize_text(feedback_text, max_chars=1200),
        "deterministic_hint": deterministic_feedback_kind,
        "last_interaction_context": context,
    }
    return "\n".join(
        [
            "You are the Telegram intent classifier for a generic Offdesk remote operator harness.",
            "Classify the operator's freeform Telegram message. You are not allowed to approve, launch, dispatch, run shell commands, mutate files, resolve approvals, or retarget providers.",
            "Return exactly one JSON object. Do not include markdown.",
            "Allowed intent values: feedback, plan_request, execution_request, approval_attempt, unsafe_mutation, clarification, unknown.",
            "Use feedback_kind=planning_request only when the text should become a Plan Mode candidate. Otherwise use feedback_kind=freeform_feedback.",
            "If execution is requested, classify intent as execution_request but do not imply authorization.",
            "When you set requires_clarification=true, write clarifying_question in the same language as telegram_text and keep it short enough for a mobile chat card.",
            "JSON schema:",
            json.dumps(
                {
                    "intent": "feedback",
                    "feedback_kind": "freeform_feedback",
                    "confidence": 0.0,
                    "project_hint": None,
                    "goal": None,
                    "timebox": None,
                    "requires_clarification": False,
                    "clarifying_question": None,
                    "reason": "short reason",
                    "non_authorized": [
                        "execution",
                        "approval",
                        "shell",
                        "git mutation",
                    ],
                },
                ensure_ascii=False,
            ),
            "Input:",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ]
    )


def call_ollama_intent_agent(runtime: dict[str, Any], prompt: str) -> dict[str, Any]:
    return call_ollama_json(runtime, prompt, temperature=0.1)


def clamp_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def short_optional_text(value: Any, max_chars: int = 240) -> str | None:
    text = sanitize_text(str(value or "").strip(), max_chars=max_chars)
    return text or None


def normalize_agent_intent(
    parsed: dict[str, Any],
    *,
    runtime: dict[str, Any],
    deterministic_feedback_kind: str,
) -> dict[str, Any]:
    allowed_intents = {
        "feedback",
        "plan_request",
        "execution_request",
        "approval_attempt",
        "unsafe_mutation",
        "clarification",
        "unknown",
    }
    intent = str(parsed.get("intent") or "").strip().lower()
    if intent not in allowed_intents:
        intent = "unknown"
    requested_kind = str(parsed.get("feedback_kind") or "").strip()
    if requested_kind not in {"freeform_feedback", "planning_request"}:
        requested_kind = (
            "planning_request"
            if intent in {"plan_request", "execution_request"}
            else deterministic_feedback_kind
        )
    non_authorized = unique_nonempty(
        list(parsed.get("non_authorized") if isinstance(parsed.get("non_authorized"), list) else [])
        + ["execution", "approval", "shell", "git mutation"]
    )
    return {
        "schema": AGENT_INTENT_SCHEMA,
        "status": "classified",
        "source": "ollama",
        "provider": runtime.get("provider"),
        "base_url": runtime.get("base_url"),
        "model": runtime.get("model"),
        "intent": intent,
        "feedback_kind": requested_kind,
        "confidence": clamp_float(parsed.get("confidence")),
        "project_hint": short_optional_text(parsed.get("project_hint"), max_chars=120),
        "goal": short_optional_text(parsed.get("goal"), max_chars=240),
        "timebox": short_optional_text(parsed.get("timebox"), max_chars=120),
        "requires_clarification": bool(parsed.get("requires_clarification")),
        "clarifying_question": short_optional_text(parsed.get("clarifying_question"), max_chars=240),
        "reason": short_optional_text(parsed.get("reason"), max_chars=240),
        "non_authorized": non_authorized,
        "config_sources": list(runtime.get("config_sources") or []),
    }


def fallback_agent_intent(
    *,
    reason: str,
    deterministic_feedback_kind: str,
    agent_config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": AGENT_INTENT_SCHEMA,
        "status": "fallback",
        "source": "deterministic",
        "reason": sanitize_text(reason, max_chars=240),
        "intent": "plan_request"
        if deterministic_feedback_kind == "planning_request"
        else "feedback",
        "feedback_kind": deterministic_feedback_kind,
        "confidence": 0.25,
        "provider": agent_config.get("provider"),
        "configured_models": list(agent_config.get("models") or [])[:4],
        "non_authorized": ["execution", "approval", "shell", "git mutation"],
    }


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


def classify_feedback_with_agent(
    args: argparse.Namespace,
    feedback_text: str,
    *,
    feedback_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    deterministic_feedback_kind = classify_feedback_kind(feedback_text)
    agent_config = resolve_agent_config(args)
    if agent_config.get("mode") == "off":
        return None
    runtime = select_agent_runtime(agent_config)
    if not runtime:
        return fallback_agent_intent(
            reason="local_agent_unavailable",
            deterministic_feedback_kind=deterministic_feedback_kind,
            agent_config=agent_config,
        )
    prompt = build_agent_intent_prompt(
        feedback_text=feedback_text,
        deterministic_feedback_kind=deterministic_feedback_kind,
        feedback_context=feedback_context,
    )
    try:
        parsed = call_ollama_intent_agent(runtime, prompt)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
        if agent_config.get("mode") == "required":
            raise RemoteOperatorTelegramError(f"local agent intent classification failed: {error}") from error
        return fallback_agent_intent(
            reason=f"local_agent_failed:{type(error).__name__}",
            deterministic_feedback_kind=deterministic_feedback_kind,
            agent_config=agent_config,
        )
    return normalize_agent_intent(
        parsed,
        runtime=runtime,
        deterministic_feedback_kind=deterministic_feedback_kind,
    )


def sha256_short(value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_id(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def contains_secret_like_text(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    markers = ("token=", "api_key=", "apikey=", "password=", "secret=")
    return any(marker in text for marker in markers) or re.search(r"\bsk-[a-z0-9]{12,}", text) is not None


def ensure_cli_option(argv: list[str], flag: str, value: str) -> list[str]:
    output = [str(item) for item in argv]
    if flag in output:
        index = output.index(flag)
        if index + 1 < len(output):
            output[index + 1] = value
        else:
            output.append(value)
    else:
        output.extend([flag, value])
    return output


def resolve_telegram_config(env_file: pathlib.Path, *, required: bool) -> dict[str, Any]:
    env = parse_env_file(env_file, required=required)
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner_chat_id = env.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    allowed_chat_ids = set(csv_values(env.get("TELEGRAM_ALLOW_CHAT_IDS", "")))
    allowed_chat_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")))
    if owner_chat_id:
        allowed_chat_ids.add(owner_chat_id)
    owner_user_id = env.get("TELEGRAM_OWNER_USER_ID", "").strip()
    allowed_user_ids = set(csv_values(env.get("TELEGRAM_ALLOW_USER_IDS", "")))
    allowed_user_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_USER_IDS", "")))
    if owner_user_id:
        allowed_user_ids.add(owner_user_id)
    target_chat_id = owner_chat_id or next(iter(sorted(allowed_chat_ids)), "")
    if required and not token:
        raise RemoteOperatorTelegramError("TELEGRAM_BOT_TOKEN is missing")
    if required and not allowed_chat_ids:
        raise RemoteOperatorTelegramError(
            "TELEGRAM_OWNER_CHAT_ID or TELEGRAM_ALLOW_CHAT_IDS is required"
        )
    return {
        "token": token,
        "target_chat_id": target_chat_id,
        "target_chat_id_hash": sha256_short(target_chat_id) if target_chat_id else None,
        "allowed_chat_ids": allowed_chat_ids,
        "allowed_user_ids": allowed_user_ids,
        "chat_allowlist_configured": bool(allowed_chat_ids),
        "user_allowlist_configured": bool(allowed_user_ids),
        "env_file": str(env_file),
    }


def normalize_command_name(raw: str) -> str:
    text = raw.strip()
    if text.startswith("/"):
        text = text[1:]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip().lower().replace("-", "_")


def parse_remote_command(command_text: str) -> dict[str, Any]:
    text = str(command_text or "").strip()
    if not text:
        return unsupported_command(text, "empty_command")
    original_text = text
    alias = BUTTON_COMMAND_ALIASES.get(text)
    if alias:
        text = alias
    if not text.startswith("/"):
        feedback_kind = classify_feedback_kind(original_text)
        return {
            "supported": True,
            "command": "feedback",
            "argv": [],
            "reason": feedback_kind,
            "command_text": original_text,
            "feedback_text": original_text,
            "feedback_kind": feedback_kind,
        }
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        return unsupported_command(original_text, f"parse_error:{error}")
    if not tokens:
        return unsupported_command(original_text, "empty_command")

    command = normalize_command_name(tokens[0])
    args = tokens[1:]
    if command in {"start", "help"}:
        return {"supported": True, "command": "help", "argv": [], "reason": "help"}
    if command == "status":
        if args:
            return unsupported_command(original_text, "status_accepts_no_arguments")
        return {"supported": True, "command": "status", "argv": ["status"]}
    if command == "pending":
        argv = ["pending"]
        for arg in args:
            if arg == "--all":
                argv.append("--all")
            else:
                return unsupported_command(original_text, f"unsupported_pending_argument:{arg}")
        return {"supported": True, "command": "pending", "argv": argv}
    if command == "plans":
        return parse_plans_command(original_text, args)
    if command == "show":
        return parse_show_command(original_text, args)
    return unsupported_command(original_text, "unsupported_remote_operator_command")


def parse_plans_command(command_text: str, args: list[str]) -> dict[str, Any]:
    argv = ["plans"]
    index = 0
    value_flags = {"--project-key", "--task-id", "--profile-key", "--artifact-kind"}
    while index < len(args):
        arg = args[index]
        if arg == "--latest":
            argv.append(arg)
            index += 1
            continue
        if arg in value_flags:
            if index + 1 >= len(args):
                return unsupported_command(command_text, f"missing_value:{arg}")
            value = args[index + 1].strip()
            if not value:
                return unsupported_command(command_text, f"empty_value:{arg}")
            argv.extend([arg, value])
            index += 2
            continue
        return unsupported_command(command_text, f"unsupported_plans_argument:{arg}")
    return {"supported": True, "command": "plans", "argv": argv}


def parse_show_command(command_text: str, args: list[str]) -> dict[str, Any]:
    if len(args) != 1 or not args[0].strip():
        return unsupported_command(command_text, "show_requires_one_plan_ref")
    return {"supported": True, "command": "show", "argv": ["show", args[0].strip()]}


def classify_feedback_kind(text: str) -> str:
    normalized = str(text or "").strip().lower()
    planning_markers = (
        "자율주행",
        "야간주행",
        "야간 주행",
        "밤샘",
        "overnight",
        "night run",
        "계획",
        "plan",
        "offdesk",
        "진행",
        "처리",
        "검토해볼까",
        "시작",
        "맡기",
    )
    if any(marker in normalized for marker in planning_markers):
        return "planning_request"
    return "freeform_feedback"


def unsupported_command(command_text: str, reason: str) -> dict[str, Any]:
    return {
        "supported": False,
        "command": None,
        "argv": [],
        "reason": reason,
        "command_text": command_text,
    }


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


def sanitize_text(text: str, *, max_chars: int = 1200) -> str:
    safe = str(text or "")
    safe = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot<redacted>", safe)
    safe = re.sub(r"(?i)(telegram_bot_token|bot_token|token)=\S+", r"\1=<redacted>", safe)
    safe = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-<redacted>", safe)
    if len(safe) > max_chars:
        safe = safe[:max_chars] + "...<truncated>"
    return safe


def profile_label_from_projection(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    value = payload.get("profile") or projection.get("forager_profile") or "default"
    return sanitize_text(str(value), max_chars=80)


def title_with_profile(title: str, profile: Any) -> str:
    safe_profile = str(profile or "default").strip()
    if safe_profile and safe_profile != "default":
        return f"<b>{html.escape(str(title))}</b> · <code>{html.escape(safe_profile)}</code>"
    return f"<b>{html.escape(str(title))}</b>"


def render_projection_message(
    projection: dict[str, Any],
    *,
    max_chars: int,
    adapter_health: dict[str, Any] | None = None,
) -> str:
    command = str(projection.get("command") or "").strip()
    if command == "status":
        message = render_status_message(projection, adapter_health=adapter_health)
    elif command == "pending":
        message = render_pending_message(projection)
    elif command == "plans":
        message = render_plans_message(projection)
    elif command == "show":
        message = render_show_message(projection)
    else:
        message = render_generic_projection_message(projection)
    if len(message) > max_chars:
        return message[: max(0, max_chars - 20)] + "\n...<truncated>"
    return message


def render_status_message(
    projection: dict[str, Any],
    *,
    adapter_health: dict[str, Any] | None = None,
) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    lines = [
        title_with_profile("Forager 점검", profile),
        status_headline(payload),
    ]
    summary = status_summary(payload, primary_status_kind(payload))
    if summary:
        lines.append(summary)
    adapter_line = adapter_status_line(adapter_health)
    if adapter_line:
        lines.append(adapter_line)
    lines.append(status_next_action(payload))
    return "\n".join(lines)


def readiness_for_action(adapter_health: dict[str, Any] | None, action: str) -> dict[str, Any] | None:
    if not isinstance(adapter_health, dict):
        return None
    readiness = adapter_health.get("action_readiness")
    if not isinstance(readiness, list):
        return None
    for item in readiness:
        if isinstance(item, dict) and str(item.get("action") or "") == action:
            return item
    return None


def adapter_status_line(adapter_health: dict[str, Any] | None) -> str:
    if not isinstance(adapter_health, dict):
        return ""
    health_status = str(adapter_health.get("health_status") or "").strip()
    build_plan = readiness_for_action(adapter_health, "build_plan") or {}
    build_plan_status = str(build_plan.get("status") or "").strip()
    if health_status == "healthy" and build_plan_status == "healthy":
        return "원격 정상 · 계획 준비 가능"
    if health_status == "degraded" or build_plan_status == "blocked":
        return "부분 장애: 새 계획/야간주행 막힘"
    if health_status == "unhealthy":
        return "원격 수신 장애: 로컬 CLI 확인"
    return ""


def render_pending_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    approvals = payload.get("approvals") if isinstance(payload.get("approvals"), list) else []
    lines = [
        title_with_profile("승인 대기", profile),
    ]
    if approvals:
        expired_count = sum(1 for item in approvals if isinstance(item, dict) and item.get("expired"))
        expired_suffix = f" 만료 {expired_count}개 포함." if expired_count else ""
        lines.append(
            f"승인 요청 {number(payload, 'approval_count')}개가 기다립니다.{expired_suffix}"
        )
    else:
        lines.append("승인할 항목이 없습니다.")
    action_labels: list[str] = []
    for approval in approvals[:2]:
        if not isinstance(approval, dict):
            continue
        expired = " 만료" if approval.get("expired") else ""
        action_labels.append(html.escape(display_action(approval.get("action"))) + expired)
    if action_labels:
        lines.append(" · ".join(action_labels))
    if len(approvals) > 2:
        lines.append(f"외 {len(approvals) - 2}개 더 있음")
    next_line = (
        "승인은 로컬에서 판단하세요."
        if approvals
        else "새 승인 요청이 오면 다시 확인하세요."
    )
    lines.append(next_line)
    return "\n".join(lines)


def render_plans_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    plans = payload.get("plans") if isinstance(payload.get("plans"), list) else []
    lines = [
        title_with_profile("자율주행 계획", profile),
    ]
    if plans:
        lines.append(f"계획 {number(payload, 'plan_count')}개가 있습니다.")
    else:
        lines.append("등록된 계획이 없습니다.")
    for plan in plans[:2]:
        if not isinstance(plan, dict):
            continue
        lines.append(
            html.escape(str(plan.get("plan_id") or "plan"))
            + " · "
            + html.escape(display_review_status(plan.get("review_status")))
        )
    if len(plans) > 2:
        lines.append(f"외 {len(plans) - 2}개 더 있음")
    next_line = (
        "아래 버튼으로 계획 상세 보기"
        if plans
        else "계획을 등록한 뒤 다시 확인하세요."
    )
    lines.append(next_line)
    return "\n".join(lines)


def render_show_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    reviews = payload.get("reviews") if isinstance(payload.get("reviews"), list) else []
    launch_preps = payload.get("launch_preps") if isinstance(payload.get("launch_preps"), list) else []
    lines = [
        title_with_profile("계획 상세", profile),
        f"계획: {html.escape(str(plan.get('plan_id') or 'unknown'))}",
        f"리뷰: {html.escape(display_review_status(plan.get('review_status')))} / 실행 준비 {len(launch_preps)}개",
        f"다음 조치: {html.escape(display_next_action(plan.get('next_safe_action')))}",
    ]
    if reviews:
        latest = reviews[-1] if isinstance(reviews[-1], dict) else {}
        lines.append(
            "최근 리뷰: "
            + html.escape(str(latest.get("decision") or "unknown"))
            + " by "
            + html.escape(str(latest.get("reviewer") or "operator"))
        )
    return "\n".join(lines)


def render_generic_projection_message(projection: dict[str, Any]) -> str:
    card = projection.get("card") if isinstance(projection.get("card"), dict) else {}
    title = html.escape(str(card.get("title") or "Forager"))
    summary_lines = safe_string_list(card.get("summary_lines"))
    lines = [
        f"<b>{title}</b>",
        html.escape(summary_lines[0] if summary_lines else "내용 확인"),
    ]
    for item in summary_lines[1:3]:
        lines.append(html.escape(item))
    lines.append("세부 내용은 로컬에서 확인하세요.")
    return "\n".join(lines)


def projection_payload(projection: dict[str, Any]) -> dict[str, Any]:
    payload = projection.get("payload")
    return payload if isinstance(payload, dict) else {}


def projection_card(projection: dict[str, Any]) -> dict[str, Any]:
    card = projection.get("card")
    return card if isinstance(card, dict) else {}


def number(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return int(raw) if isinstance(raw, int) else 0


def status_headline(payload: dict[str, Any]) -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    active = number(payload, "active_offdesk_tasks")
    queued = number(payload, "queued_offdesk_tasks")
    if pending:
        return f"승인 요청 {pending}개가 먼저입니다."
    if failed:
        return f"실패한 자율주행 {failed}개를 확인해야 합니다."
    if closeout:
        return f"마무리 확인 {closeout}개가 남았습니다."
    if active:
        return f"자율주행 {active}개가 진행 중입니다."
    if queued:
        return f"자율주행 {queued}개가 대기 중입니다."
    return "처리할 항목이 없습니다."


def primary_status_kind(payload: dict[str, Any]) -> str:
    if number(payload, "pending_approvals"):
        return "pending"
    if number(payload, "failed_offdesk_tasks"):
        return "failed"
    if number(payload, "closeout_required_offdesk_tasks"):
        return "closeout"
    if number(payload, "active_offdesk_tasks"):
        return "active"
    if number(payload, "queued_offdesk_tasks"):
        return "queued"
    return "none"


def status_summary(payload: dict[str, Any], primary: str = "none") -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    active = number(payload, "active_offdesk_tasks")
    queued = number(payload, "queued_offdesk_tasks")
    parts: list[str] = []
    if pending and primary != "pending":
        parts.append(f"승인 {pending}")
    if failed and primary != "failed":
        parts.append(f"실패 {failed}")
    if closeout and primary != "closeout":
        parts.append(f"마무리 {closeout}")
    if (active or queued) and primary not in {"active", "queued"}:
        parts.append(f"진행 {active} / 대기 {queued}")
    return "그 밖에 " + " · ".join(parts) if parts else ""


def status_next_action(payload: dict[str, Any]) -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    if pending:
        return "아래 버튼으로 승인 내용 보기"
    if failed or closeout:
        return "로컬에서 실패/마무리 항목을 점검하세요."
    return "새 알림이 오면 다시 확인하세요."


def display_action(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "approve_plan": "계획 승인",
        "approve_launch": "실행 승인",
        "deny_launch": "실행 거절",
        "provider_fallback": "모델 대체",
        "provider_retarget": "모델 변경",
    }
    return labels.get(text, text.replace("_", " ") or "확인 필요")


def display_review_status(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "accepted": "승인됨",
        "approved": "승인됨",
        "pending": "검토 대기",
        "missing": "검토 없음",
        "not_reviewed": "검토 없음",
        "revision_required": "수정 필요",
        "rejected": "거절됨",
        "review_unknown": "검토 상태 불명",
        "unknown": "상태 불명",
    }
    return labels.get(text, text.replace("_", " ") or "상태 불명")


def display_next_action(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "inspect": "내용 확인",
        "review": "리뷰 필요",
        "approve": "승인 검토",
        "launch_prep": "실행 준비 확인",
        "launch": "실행 검토",
        "closeout": "마무리 확인",
    }
    return labels.get(text, text.replace("_", " ") or "내용 확인")


def short_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "sha256:unknown"
    if text.startswith("sha256:") and len(text) > 22:
        return text[:22]
    return text


def safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_text(str(item), max_chars=400) for item in value if str(item).strip()]


def truncate_label(value: Any, *, max_chars: int = 34) -> str:
    text = sanitize_text(str(value or "").strip(), max_chars=max_chars + 20)
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"


def slugify_project_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9가-힣]+", "-", text)
    return text.strip("-") or "project"


def workspace_root_inputs(args: argparse.Namespace) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for raw in args.workspace_root or []:
        roots.append(pathlib.Path(raw))
    for raw in csv_values(os.environ.get("OFFDESK_REMOTE_OPERATOR_WORKSPACE_ROOTS", "")):
        roots.append(pathlib.Path(raw))
    if roots:
        return roots
    for parent in REPO_ROOT.parents:
        if parent.name == "Workspace" and parent.exists():
            return [parent]
    return [REPO_ROOT.parent]


def workspace_roots(args: argparse.Namespace) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    seen: set[str] = set()
    for raw in workspace_root_inputs(args):
        try:
            path = pathlib.Path(raw).expanduser().resolve()
        except OSError:
            path = pathlib.Path(raw).expanduser()
        key = str(path)
        if key in seen or not path.exists() or not path.is_dir():
            continue
        seen.add(key)
        roots.append(path)
    return roots


PROJECT_MARKER_FILES = (
    ".git",
    ".forager",
    "AGENTS.md",
    "README.md",
    "README_KO.md",
    "Cargo.toml",
    "pyproject.toml",
    "package.json",
    "uv.lock",
)


def project_marker_names(path: pathlib.Path) -> list[str]:
    markers: list[str] = []
    for name in PROJECT_MARKER_FILES:
        if (path / name).exists():
            markers.append(name)
    return markers


def looks_like_project_dir(path: pathlib.Path) -> bool:
    if project_marker_names(path):
        return True
    try:
        child_names = {child.name for child in path.iterdir() if child.is_dir()}
    except OSError:
        return False
    return bool(child_names.intersection({"src", "scripts", "tests", "forager-cli"}))


def discover_project_paths(roots: list[pathlib.Path], *, max_paths: int = 80) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    seen: set[str] = set()

    def add(path: pathlib.Path) -> None:
        if len(found) >= max_paths:
            return
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved)
        if key in seen or not path.exists() or not path.is_dir():
            return
        if not looks_like_project_dir(path):
            return
        seen.add(key)
        found.append(path)

    for root in roots:
        add(root)
        try:
            children = sorted(
                [child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")],
                key=lambda item: item.name.lower(),
            )
        except OSError:
            continue
        for child in children:
            add(child)
        for child in children[:40]:
            try:
                nested = sorted(
                    [
                        grandchild
                        for grandchild in child.iterdir()
                        if grandchild.is_dir() and not grandchild.name.startswith(".")
                    ],
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                continue
            for grandchild in nested:
                if (grandchild / ".git").exists() or project_marker_names(grandchild):
                    add(grandchild)
    return found


def git_output(path: pathlib.Path, args: list[str], *, timeout_sec: float = 1.5) -> str | None:
    try:
        process = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    return process.stdout.strip()


def is_git_repo(path: pathlib.Path) -> bool:
    marker = path / ".git"
    if marker.exists():
        return True
    return git_output(path, ["rev-parse", "--is-inside-work-tree"]) == "true"


def request_tokens(*values: Any) -> set[str]:
    text = " ".join(str(value or "") for value in values)
    tokens = re.findall(r"[A-Za-z0-9가-힣]{2,}", text.lower())
    return set(tokens)


def relative_path_hint(path: pathlib.Path, roots: list[pathlib.Path]) -> str:
    for root in roots:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            continue
    return path.name


def project_readiness(markers: list[str], git_repo: bool, dirty: bool | None) -> str:
    if git_repo and dirty is False:
        return "ready"
    if git_repo:
        return "needs_review"
    if markers:
        return "needs_review"
    return "not_git"


def project_risk(readiness: str, dirty: bool | None) -> str:
    if readiness == "ready":
        return "low"
    if dirty is True:
        return "medium"
    return "medium" if readiness == "needs_review" else "high"


def display_project_readiness(value: Any) -> str:
    labels = {
        "ready": "준비됨",
        "needs_review": "검토 필요",
        "not_git": "경로 확인 필요",
    }
    return labels.get(str(value or ""), "검토 필요")


def display_project_risk(value: Any) -> str:
    labels = {"low": "낮음", "medium": "중간", "high": "높음"}
    return labels.get(str(value or ""), "중간")


def build_project_candidate(
    path: pathlib.Path,
    *,
    roots: list[pathlib.Path],
    tokens: set[str],
    rank: int,
) -> dict[str, Any]:
    markers = project_marker_names(path)
    git_repo = is_git_repo(path)
    branch = git_output(path, ["branch", "--show-current"]) if git_repo else None
    head = git_output(path, ["rev-parse", "--short", "HEAD"]) if git_repo else None
    status = git_output(path, ["status", "--porcelain"]) if git_repo else None
    dirty = bool(status) if status is not None else (None if git_repo else False)
    hint = relative_path_hint(path, roots)
    display_name = path.name if path.name else hint
    if "/" in hint and path.name not in hint.split("/", 1)[0]:
        display_name = hint
    readiness = project_readiness(markers, git_repo, dirty)
    risk = project_risk(readiness, dirty)
    reasons: list[str] = []
    if git_repo:
        reasons.append("git repository")
    if dirty is False and git_repo:
        reasons.append("clean worktree")
    if dirty is True:
        reasons.append("dirty worktree")
    for marker in markers[:3]:
        if marker != ".git":
            reasons.append(marker)
    name_blob = f"{display_name} {hint}".lower()
    token_hits = sum(1 for token in tokens if token and token in name_blob)
    score = token_hits * 20
    score += 10 if git_repo else 0
    score += 5 if dirty is False and git_repo else 0
    score += min(5, len(markers))
    score -= 3 if dirty is True else 0
    return {
        "schema": PROJECT_CANDIDATE_SCHEMA,
        "rank": rank,
        "score": score,
        "project_key": slugify_project_key(display_name),
        "display_name": truncate_label(display_name, max_chars=40),
        "workspace_path": str(path),
        "workspace_path_hint": sanitize_text(hint, max_chars=160),
        "is_git_repo": git_repo,
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "readiness": readiness,
        "risk": risk,
        "autonomy_fit": "high" if readiness == "ready" else "medium" if readiness == "needs_review" else "low",
        "reasons": unique_nonempty(reasons)[:4],
        "next_step": "init_review",
    }


def ranked_project_candidates(
    args: argparse.Namespace,
    *,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    roots = workspace_roots(args)
    token_set = request_tokens(
        request_text,
        agent_intent.get("project_hint") if isinstance(agent_intent, dict) else None,
        agent_intent.get("goal") if isinstance(agent_intent, dict) else None,
    )
    candidates = [
        build_project_candidate(path, roots=roots, tokens=token_set, rank=index + 1)
        for index, path in enumerate(discover_project_paths(roots))
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("workspace_path_hint") or "").lower(),
        )
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    return candidates


def scan_project_candidates(
    args: argparse.Namespace,
    *,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates = ranked_project_candidates(
        args,
        request_text=request_text,
        agent_intent=agent_intent,
    )
    limited = candidates[: max(1, int(args.max_project_candidates))]
    for index, candidate in enumerate(limited, start=1):
        candidate["rank"] = index
    return limited


def public_project_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    public = dict(candidate)
    if "workspace_path" in public:
        public["workspace_path_hash"] = sha256_short(public.pop("workspace_path"))
    return public


def public_remote_plan_session(session: dict[str, Any]) -> dict[str, Any]:
    public = dict(session)
    public["candidates"] = [
        public_project_candidate(candidate)
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    selected = session.get("selected_candidate")
    if isinstance(selected, dict):
        public["selected_candidate"] = public_project_candidate(selected)
    preview = session.get("project_init_preview")
    if isinstance(preview, dict):
        public["project_init_preview"] = public_project_init_preview(preview)
    run = session.get("project_init_run")
    if isinstance(run, dict):
        public["project_init_run"] = public_project_init_run(run)
    draft = session.get("plan_draft")
    if isinstance(draft, dict):
        public["plan_draft"] = public_plan_draft(draft)
    registration = session.get("plan_registration")
    if isinstance(registration, dict):
        public["plan_registration"] = public_plan_registration(registration)
    review = session.get("plan_review")
    if isinstance(review, dict):
        public["plan_review"] = public_plan_review(review)
    launch_prep = session.get("plan_launch_prep")
    if isinstance(launch_prep, dict):
        public["plan_launch_prep"] = public_plan_launch_prep(launch_prep)
    gate_request = session.get("plan_gate_request")
    if isinstance(gate_request, dict):
        public["plan_gate_request"] = public_plan_gate_request(gate_request)
    gate_resolution = session.get("plan_gate_resolution")
    if isinstance(gate_resolution, dict):
        public["plan_gate_resolution"] = public_plan_gate_resolution(gate_resolution)
    execution_brief = session.get("plan_execution_brief")
    if isinstance(execution_brief, dict):
        public["plan_execution_brief"] = public_plan_execution_brief(execution_brief)
    enqueue_handoff = session.get("plan_enqueue_handoff")
    if isinstance(enqueue_handoff, dict):
        public["plan_enqueue_handoff"] = public_plan_enqueue_handoff(enqueue_handoff)
    workload_binding = session.get("plan_workload_binding")
    if isinstance(workload_binding, dict):
        public["plan_workload_binding"] = public_plan_workload_binding(workload_binding)
    enqueue_run = session.get("plan_enqueue_run")
    if isinstance(enqueue_run, dict):
        public["plan_enqueue_run"] = public_plan_enqueue_run(enqueue_run)
    runtime_start = session.get("plan_runtime_start")
    if isinstance(runtime_start, dict):
        public["plan_runtime_start"] = public_plan_runtime_start(runtime_start)
    runtime_monitor = session.get("plan_runtime_monitor")
    if isinstance(runtime_monitor, dict):
        public["plan_runtime_monitor"] = public_plan_runtime_monitor(runtime_monitor)
    closeout_packet = session.get("plan_closeout_packet")
    if isinstance(closeout_packet, dict):
        public["plan_closeout_packet"] = public_plan_closeout_packet(closeout_packet)
    closeout_review_handoff = session.get("plan_closeout_review_handoff")
    if isinstance(closeout_review_handoff, dict):
        public["plan_closeout_review_handoff"] = public_plan_closeout_review_handoff(
            closeout_review_handoff
        )
    closeout_verdict = session.get("plan_closeout_verdict")
    if isinstance(closeout_verdict, dict):
        public["plan_closeout_verdict"] = public_plan_closeout_verdict(closeout_verdict)
    return public


def public_project_init_preview(preview: dict[str, Any]) -> dict[str, Any]:
    public = dict(preview)
    workspace_path = str(public.pop("workspace_path", "") or "")
    if workspace_path:
        public["workspace_path_hash"] = sha256_short(workspace_path)
    if "recommended_next_command" in public:
        public["recommended_next_command"] = [
            "<workspace_path>" if workspace_path and str(item) == workspace_path else str(item)
            for item in public.get("recommended_next_command", [])
        ]
    return public


def public_project_init_run(run: dict[str, Any]) -> dict[str, Any]:
    public = dict(run)
    workspace_path = str(public.pop("workspace_path", "") or "")
    if workspace_path:
        public["workspace_path_hash"] = sha256_short(workspace_path)
    command = public.get("command")
    if isinstance(command, list):
        public["command"] = [
            "<workspace_path>" if workspace_path and str(item) == workspace_path else str(item)
            for item in command
        ]
    output = public.get("project_init_output")
    if isinstance(output, dict):
        public["project_init_output"] = public_project_init_output(output)
    return public


def public_project_init_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("project_root", "artifact_dir"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    return public


def public_plan_draft(draft: dict[str, Any]) -> dict[str, Any]:
    public = dict(draft)
    plan_path = str(public.pop("plan_artifact_path", "") or "")
    if plan_path:
        public["plan_artifact_path_hash"] = sha256_short(plan_path)
    command = public.get("validation_command")
    if isinstance(command, list):
        public["validation_command"] = [
            "<plan_draft_path>" if plan_path and str(item) == plan_path else str(item)
            for item in command
        ]
    output = public.get("validation_output")
    if isinstance(output, dict):
        public["validation_output"] = public_offdesk_plan_registration_output(output)
    return public


def public_plan_registration(registration: dict[str, Any]) -> dict[str, Any]:
    public = dict(registration)
    plan_path = str(public.pop("plan_artifact_path", "") or "")
    if plan_path:
        public["plan_artifact_path_hash"] = sha256_short(plan_path)
    command = public.get("registration_command")
    if isinstance(command, list):
        public["registration_command"] = [
            "<plan_draft_path>" if plan_path and str(item) == plan_path else str(item)
            for item in command
        ]
    output = public.get("registration_output")
    if isinstance(output, dict):
        public["registration_output"] = public_offdesk_plan_registration_output(output)
    return public


def public_offdesk_plan_registration_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    source_path = str(public.pop("source_path", "") or "")
    if source_path:
        public["source_path_hash"] = sha256_short(source_path)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_review(review: dict[str, Any]) -> dict[str, Any]:
    public = dict(review)
    plan_ref = str(public.get("plan_ref") or "")
    if plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["plan_ref_hash"] = sha256_short(str(public.pop("plan_ref")))
    for key in ("registration_json", "copied_source_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("review_command")
    if isinstance(command, list) and plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["review_command"] = [
            "<plan_ref>" if str(item) == plan_ref else str(item)
            for item in command
        ]
    output = public.get("review_output")
    if isinstance(output, dict):
        public["review_output"] = public_offdesk_plan_review_output(output)
    return public


def public_offdesk_plan_review_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("registration_path", "review_file"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_launch_prep(prep: dict[str, Any]) -> dict[str, Any]:
    public = dict(prep)
    plan_ref = str(public.get("plan_ref") or "")
    if plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["plan_ref_hash"] = sha256_short(str(public.pop("plan_ref")))
    for key in ("copied_source_json", "review_record_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("launch_prep_command")
    if isinstance(command, list) and plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["launch_prep_command"] = [
            "<plan_ref>" if str(item) == plan_ref else str(item)
            for item in command
        ]
    output = public.get("launch_prep_output")
    if isinstance(output, dict):
        public["launch_prep_output"] = public_offdesk_plan_launch_prep_output(output)
    return public


def public_offdesk_plan_launch_prep_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("registration_path", "source_path", "review_record_json", "selected_plan_path"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    reads = public.get("required_first_reads")
    if isinstance(reads, list):
        public["required_first_reads"] = [
            sha256_short(str(item))
            for item in reads
            if str(item or "").strip()
        ]
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_gate_request(gate_request: dict[str, Any]) -> dict[str, Any]:
    public = dict(gate_request)
    launch_prep_json = str(public.pop("launch_prep_json", "") or "")
    if launch_prep_json:
        public["launch_prep_json_hash"] = sha256_short(launch_prep_json)
    command = public.get("gate_command")
    if isinstance(command, list) and launch_prep_json:
        public["gate_command"] = [
            "<launch_prep_json>" if str(item) == launch_prep_json else str(item)
            for item in command
        ]
    return public


def public_plan_gate_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    public = dict(resolution)
    launch_prep_json = str(public.pop("launch_prep_json", "") or "")
    if launch_prep_json:
        public["launch_prep_json_hash"] = sha256_short(launch_prep_json)
    pending = public.get("pending_approval")
    if isinstance(pending, dict):
        public["pending_approval"] = public_approval_for_resolution(pending)
    output = public.get("resolution_output")
    if isinstance(output, dict):
        public["resolution_output"] = public_approval_for_resolution(output)
    return public


def public_approval_for_resolution(approval: dict[str, Any]) -> dict[str, Any]:
    public = dict(approval)
    metadata = public.get("metadata")
    if isinstance(metadata, dict):
        public["metadata_hash"] = sha256_short(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        public.pop("metadata", None)
    return public


def public_plan_execution_brief(brief: dict[str, Any]) -> dict[str, Any]:
    public = dict(brief)
    for key in ("execution_brief_json", "launch_prep_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    output = public.get("execution_brief")
    if isinstance(output, dict):
        public["execution_brief"] = dict(output)
    return public


def public_plan_enqueue_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    public = dict(handoff)
    execution_brief_json = str(public.pop("execution_brief_json", "") or "")
    if execution_brief_json:
        public["execution_brief_json_hash"] = sha256_short(execution_brief_json)
    command = public.get("command_template")
    if isinstance(command, list) and execution_brief_json:
        public["command_template"] = [
            "<execution_brief_json>" if str(item) == execution_brief_json else str(item)
            for item in command
        ]
    return public


def public_plan_workload_binding(binding: dict[str, Any]) -> dict[str, Any]:
    public = dict(binding)
    path_values: dict[str, str] = {}
    for key in ("prepared_task_json", "execution_brief_json", "repo", "out_dir", "workload_wrapper"):
        value = str(public.pop(key, "") or "")
        if value:
            path_values[key] = value
            public[f"{key}_hash"] = sha256_short(value)
    for key in ("bound_enqueue_args", "manifest_enqueue_args"):
        command = public.get(key)
        if isinstance(command, list):
            sanitized = []
            for item in command:
                item_text = str(item)
                for path_key, path_value in sorted(
                    path_values.items(),
                    key=lambda item: len(item[1]),
                    reverse=True,
                ):
                    if path_value and path_value in item_text:
                        item_text = item_text.replace(path_value, f"<{path_key}>")
                sanitized.append(item_text)
            public[key] = sanitized
    manifest_summary = public.get("manifest_summary")
    if isinstance(manifest_summary, dict):
        summary = dict(manifest_summary)
        for key in ("repo", "out_dir", "workload_wrapper"):
            value = str(summary.pop(key, "") or "")
            if value:
                summary[f"{key}_hash"] = sha256_short(value)
        public["manifest_summary"] = summary
    return public


def public_plan_enqueue_run(enqueue_run: dict[str, Any]) -> dict[str, Any]:
    public = dict(enqueue_run)
    for key in ("workload_binding_json", "prepared_task_json", "execution_brief_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("enqueue_command")
    if isinstance(command, list):
        public["enqueue_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("enqueue_command", None)
    output = public.get("enqueue_output")
    if isinstance(output, dict):
        public["enqueue_output"] = public_offdesk_task_view(output)
    return public


def public_plan_runtime_start(runtime_start: dict[str, Any]) -> dict[str, Any]:
    public = dict(runtime_start)
    for key in ("enqueue_run_json", "prepared_task_json", "execution_brief_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("tick_command")
    if isinstance(command, list):
        public["tick_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("tick_command", None)
    output = public.get("tick_output")
    if isinstance(output, dict):
        public["tick_output"] = public_tick_output(output)
    return public


def public_plan_runtime_monitor(runtime_monitor: dict[str, Any]) -> dict[str, Any]:
    public = dict(runtime_monitor)
    for key in ("runtime_start_json",):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    for key in ("tick_command", "tasks_command"):
        command = public.get(key)
        if isinstance(command, list):
            public[f"{key}_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
            public.pop(key, None)
    output = public.get("tick_output")
    if isinstance(output, dict):
        public["tick_output"] = public_tick_output(output)
    target_task = public.get("target_task")
    if isinstance(target_task, dict):
        public["target_task"] = public_offdesk_task_view(target_task)
    return public


def public_plan_closeout_packet(closeout_packet: dict[str, Any]) -> dict[str, Any]:
    public = dict(closeout_packet)
    for key in ("runtime_monitor_json",):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("closeout_command")
    if isinstance(command, list):
        public["closeout_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("closeout_command", None)
    output = public.get("closeout_output")
    if isinstance(output, dict):
            public["closeout_output"] = public_closeout_output(output)
    return public


def public_plan_closeout_review_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    public = dict(handoff)
    for key in ("closeout_packet_json", "artifact_dir", "closeout_plan_json", "return_package_markdown"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    commands = public.get("local_review_commands")
    if isinstance(commands, dict):
        public["local_review_command_hashes"] = {
            str(key): sha256_short(json.dumps(value, ensure_ascii=False, sort_keys=True))
            for key, value in commands.items()
            if isinstance(value, list)
        }
        public.pop("local_review_commands", None)
    return public


def public_plan_closeout_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    public = dict(verdict)
    for key in ("closeout_review_handoff_json", "artifact_dir"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("closeout_review_command")
    if isinstance(command, list):
        public["closeout_review_command_hash"] = sha256_short(
            json.dumps(command, ensure_ascii=False, sort_keys=True)
        )
        public.pop("closeout_review_command", None)
    output = public.get("closeout_review_output")
    if isinstance(output, dict):
        public["closeout_review_output"] = public_closeout_review_output(output)
    return public


def public_closeout_review_output(output: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in (
        "review_id",
        "closeout_id",
        "verdict",
        "read_only_project_state",
        "applies_file_operations",
    ):
        if key in output:
            public[key] = output.get(key)
    receipt = output.get("closeout_receipt")
    if isinstance(receipt, dict):
        public["closeout_receipt"] = {
            key: receipt.get(key)
            for key in (
                "schema",
                "receipt_id",
                "closeout_id",
                "verdict",
                "acceptance_status",
                "evidence_status",
                "verification_status",
                "retention_review",
                "wiki_promotion_state",
                "stale_task_count",
                "next_safe_action",
            )
            if key in receipt
        }
        for key in ("open_decisions", "missing_evidence", "required_first_reads", "unsafe_operations"):
            value = receipt.get(key)
            if isinstance(value, list):
                public["closeout_receipt"][f"{key}_count"] = len(value)
    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    return public


def public_closeout_output(output: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in (
        "closeout_id",
        "dry_run",
        "operator_requested_dry_run",
        "read_only_project_state",
    ):
        if key in output:
            public[key] = output.get(key)
    for key in ("summary", "filters"):
        value = output.get(key)
        if isinstance(value, dict):
            public[key] = value
    review_contract = output.get("review_contract")
    if isinstance(review_contract, dict):
        public["review_contract"] = {
            key: review_contract.get(key)
            for key in ("provider", "required", "required_verdicts")
            if key in review_contract
        }
    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    open_decisions = output.get("open_decisions")
    if isinstance(open_decisions, list):
        public["open_decision_count"] = len(open_decisions)
    verification_commands = output.get("verification_commands")
    if isinstance(verification_commands, list):
        public["verification_command_count"] = len(verification_commands)
    return public


def public_tick_output(output: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "expired_approvals",
        "polled_background",
        "launched",
        "pending_approval",
        "completed",
        "failed",
        "resume_pending",
        "provider_deferred",
        "provider_retargeted",
        "skipped",
        "stale_lock_replaced",
        "updated_task_ids",
    }
    return {key: value for key, value in output.items() if key in allowed}


def public_offdesk_task_view(task: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "task_id",
        "request_id",
        "project_key",
        "status",
        "capability_id",
        "runner_kind",
        "background_ticket_id",
        "attempt_count",
        "last_gate_status",
        "mutation_class",
        "artifact_kind",
        "agent_mode",
        "provider_id",
        "model",
        "preview",
        "reason",
        "next_safe_action",
    }
    public = {key: value for key, value in task.items() if key in allowed}
    for key in ("workdir", "log_artifact_path", "result_artifact_path"):
        value = str(task.get(key) or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    return public


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


def render_project_selection_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    action_readiness_value = (
        session.get("action_readiness") if isinstance(session.get("action_readiness"), dict) else {}
    )
    build_plan_readiness = (
        action_readiness_value.get("build_plan")
        if isinstance(action_readiness_value.get("build_plan"), dict)
        else {}
    )
    build_plan_blocked = str(build_plan_readiness.get("status") or "") == "blocked"
    lines = [title_with_profile("계획 대상 선택", profile)]
    if not candidates:
        lines.append("후보 프로젝트를 찾지 못했습니다.")
        lines.append("프로젝트명을 직접 입력하세요.")
        lines.append("아직 실행은 시작하지 않았습니다.")
        return "\n".join(lines)
    if build_plan_blocked:
        lines.append("부분 장애: 로컬 에이전트 연결 실패")
        lines.append("막힘: 새 계획/야간주행 시작")
        shown_candidates = candidates[:1]
    else:
        lines.append(f"후보 {len(candidates)}개를 찾았습니다.")
        shown_candidates = candidates[:2]
    for candidate in shown_candidates:
        name = truncate_label(candidate.get("display_name"), max_chars=22)
        lines.append(
            f"{candidate.get('rank')}. {html.escape(name)} · {display_project_readiness(candidate.get('readiness'))}"
        )
    if len(candidates) > len(shown_candidates):
        lines[-1] = lines[-1] + f" 외 {len(candidates) - len(shown_candidates)}"
    lines.append("버튼 또는 번호/이름 직접 입력")
    return "\n".join(lines[:MOBILE_CARD_MAX_LINES])


def render_project_selected_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "프로젝트", max_chars=24)
    readiness = display_project_readiness(candidate.get("readiness"))
    risk = display_project_risk(candidate.get("risk"))
    return "\n".join(
        [
            title_with_profile("계획 대상 선택됨", profile),
            f"{html.escape(name)} · {readiness} · 위험 {risk}",
            "다음 단계는 초기화 검토입니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_project_init_preview_message(*, profile: Any, session: dict[str, Any]) -> str:
    preview = session.get("project_init_preview") if isinstance(session.get("project_init_preview"), dict) else {}
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "프로젝트", max_chars=24)
    marker_count = len(preview.get("root_markers") if isinstance(preview.get("root_markers"), list) else [])
    doc_count = len(preview.get("documentation_sources") if isinstance(preview.get("documentation_sources"), list) else [])
    return "\n".join(
        [
            title_with_profile("초기화 검토 준비", profile),
            f"{html.escape(name)} · 문서 {doc_count} · 마커 {marker_count}",
            "초기화 검토 기록을 저장했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 초기화 명령 검토",
        ]
    )


def render_project_init_preview_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("초기화 검토 필요", profile),
            "먼저 초기화 검토가 필요합니다.",
            "초기화 검토를 먼저 선택하세요.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_project_init_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
    output = run.get("project_init_output") if isinstance(run.get("project_init_output"), dict) else {}
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    name = truncate_label(output.get("project_key") or run.get("project_key") or "프로젝트", max_chars=24)
    module_count = int(summary.get("module_candidate_count") or 0)
    review_required = bool(output.get("requires_operator_review", True))
    return "\n".join(
        [
            title_with_profile("초기화 패킷 생성됨", profile),
            f"{html.escape(name)} · 모듈 {module_count} · 검토 {'필요' if review_required else '확인'}",
            "초기화 패킷을 저장했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "아래 버튼으로 계획 초안 생성",
        ]
    )


def render_project_init_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
    reason = truncate_label(run.get("error") or "생성 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("초기화 생성 실패", profile),
            html.escape(reason),
            "초기화 패킷을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_draft_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("초기화 패킷 필요", profile),
            "먼저 초기화 패킷을 생성하세요.",
            "계획 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_draft_validated_message(*, profile: Any, session: dict[str, Any]) -> str:
    draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
    output = draft.get("validation_output") if isinstance(draft.get("validation_output"), dict) else {}
    project_key = truncate_label(output.get("project_key") or draft.get("project_key") or "프로젝트", max_chars=24)
    ready = bool(output.get("ready_for_operator_review", True))
    return "\n".join(
        [
            title_with_profile("계획 초안 검증됨", profile),
            f"{html.escape(project_key)} · 검토 {'준비' if ready else '필요'}",
            "계획 초안을 저장했습니다.",
            "계획 등록/실행은 아직 하지 않았습니다.",
            "아래 버튼으로 계획 등록",
        ]
    )


def render_plan_draft_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
    reason = truncate_label(draft.get("error") or "검증 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 초안 실패", profile),
            html.escape(reason),
            "계획 초안을 검증하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_registration_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 초안 필요", profile),
            "먼저 계획 초안을 생성하세요.",
            "계획 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_registration_stale_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 초안 변경됨", profile),
            "초안이 바뀌어 재검증이 필요합니다.",
            "계획 등록은 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "아래 버튼으로 초안 재생성",
        ]
    )


def render_plan_registered_message(*, profile: Any, session: dict[str, Any]) -> str:
    registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    project_key = truncate_label(output.get("project_key") or registration.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("계획 등록됨", profile),
            f"{html.escape(project_key)} · 검토 대기",
            "계획을 등록했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 계획 검토",
        ]
    )


def render_plan_review_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 등록 필요", profile),
            "먼저 계획을 등록하세요.",
            "계획 검토는 아직 기록하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_review_approved_message(*, profile: Any, session: dict[str, Any]) -> str:
    review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
    output = review.get("review_output") if isinstance(review.get("review_output"), dict) else {}
    project_key = truncate_label(review.get("project_key") or output.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("계획 승인됨", profile),
            f"{html.escape(project_key)} · 실행 준비 후보",
            "계획 검토를 기록했습니다.",
            "실행 준비는 아직 하지 않았습니다.",
            "아래 버튼으로 실행 준비 검토",
        ]
    )


def render_plan_launch_prep_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 승인 필요", profile),
            "먼저 계획 승인을 기록하세요.",
            "실행 준비 패킷은 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_launch_prep_prepared_message(*, profile: Any, session: dict[str, Any]) -> str:
    prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
    output = prep.get("launch_prep_output") if isinstance(prep.get("launch_prep_output"), dict) else {}
    project_key = truncate_label(prep.get("project_key") or output.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("실행 준비 패킷 생성됨", profile),
            f"{html.escape(project_key)} · 게이트 검토 대기",
            "패킷만 저장했습니다.",
            "실행/승인은 아직 하지 않았습니다.",
            "아래 버튼으로 게이트 요청",
        ]
    )


def render_plan_launch_prep_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
    reason = truncate_label(prep.get("error") or "실행 준비 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 준비 실패", profile),
            html.escape(reason),
            "실행 준비 패킷을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_gate_request_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 준비 필요", profile),
            "먼저 실행 준비 패킷을 만드세요.",
            "게이트 요청은 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_gate_request_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
    output = gate_request.get("gate_output") if isinstance(gate_request.get("gate_output"), dict) else {}
    approval = output.get("approval") if isinstance(output.get("approval"), dict) else {}
    approval_id = truncate_label(approval.get("approval_id") or "approval", max_chars=28)
    return "\n".join(
        [
            title_with_profile("게이트 요청 생성됨", profile),
            f"{html.escape(approval_id)} · 승인 대기",
            "승인 대기열에 올렸습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 approval 확인",
        ]
    )


def render_plan_gate_request_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
    reason = truncate_label(gate_request.get("error") or "게이트 요청 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("게이트 요청 실패", profile),
            html.escape(reason),
            "승인 대기열을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_gate_resolution_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("게이트 요청 필요", profile),
            "먼저 게이트 요청을 만드세요.",
            "승인/거절은 아직 기록하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_gate_resolution_done_message(*, profile: Any, session: dict[str, Any]) -> str:
    resolution = session.get("plan_gate_resolution") if isinstance(session.get("plan_gate_resolution"), dict) else {}
    decision = str(resolution.get("decision") or "")
    label = "승인됨" if decision == "approved" else "거절됨"
    approval_id = truncate_label(resolution.get("approval_id") or "approval", max_chars=28)
    return "\n".join(
        [
            title_with_profile(f"게이트 {label}", profile),
            f"{html.escape(approval_id)} · {label}",
            "approval만 해결했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 다음 단계 검토",
        ]
    )


def render_plan_gate_resolution_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    resolution = session.get("plan_gate_resolution") if isinstance(session.get("plan_gate_resolution"), dict) else {}
    reason = truncate_label(resolution.get("error") or "게이트 처리 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("게이트 처리 실패", profile),
            html.escape(reason),
            "approval을 해결하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 pending 상태 확인",
        ]
    )


def render_plan_execution_brief_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("게이트 승인 필요", profile),
            "먼저 게이트 승인을 완료하세요.",
            "실행 브리프는 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_execution_brief_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    brief = session.get("plan_execution_brief") if isinstance(session.get("plan_execution_brief"), dict) else {}
    task_id = truncate_label(brief.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("실행 브리프 생성됨", profile),
            f"{html.escape(task_id)} · 로컬 enqueue 준비",
            "브리프 파일만 저장했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 enqueue 검토",
        ]
    )


def render_plan_execution_brief_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    brief = session.get("plan_execution_brief") if isinstance(session.get("plan_execution_brief"), dict) else {}
    reason = truncate_label(brief.get("error") or "브리프 생성 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 브리프 실패", profile),
            html.escape(reason),
            "실행 브리프를 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_enqueue_handoff_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 브리프 필요", profile),
            "먼저 실행 브리프를 생성하세요.",
            "큐 등록 검토는 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_enqueue_handoff_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = session.get("plan_enqueue_handoff") if isinstance(session.get("plan_enqueue_handoff"), dict) else {}
    task_id = truncate_label(handoff.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("큐 등록 검토 준비됨", profile),
            f"{html.escape(task_id)} · 로컬 검토 필요",
            "명령 템플릿만 저장했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 workload 명령 확인",
        ]
    )


def render_plan_enqueue_handoff_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = session.get("plan_enqueue_handoff") if isinstance(session.get("plan_enqueue_handoff"), dict) else {}
    reason = truncate_label(handoff.get("error") or "큐 등록 검토 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("큐 등록 검토 실패", profile),
            html.escape(reason),
            "명령 템플릿을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_workload_path_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 필요", profile),
            "prepared_task.json 경로를 입력하세요.",
            "검토된 패킷만 연결할 수 있습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 경로 직접 입력",
        ]
    )


def render_plan_workload_bound_message(*, profile: Any, session: dict[str, Any]) -> str:
    binding = session.get("plan_workload_binding") if isinstance(session.get("plan_workload_binding"), dict) else {}
    task_id = truncate_label(binding.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 연결됨", profile),
            f"{html.escape(task_id)} · 로컬 enqueue 검토 가능",
            "검토된 패킷만 연결했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 enqueue 실행 검토",
        ]
    )


def render_plan_workload_binding_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    binding = session.get("plan_workload_binding") if isinstance(session.get("plan_workload_binding"), dict) else {}
    reason = truncate_label(binding.get("error") or "패킷 연결 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 실패", profile),
            html.escape(reason),
            "패킷을 연결하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 manifest 확인",
        ]
    )


def render_plan_enqueue_run_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("워크로드 연결 필요", profile),
            "먼저 prepared_task.json을 연결하세요.",
            "큐 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 경로 직접 입력",
        ]
    )


def render_plan_enqueue_run_done_message(*, profile: Any, session: dict[str, Any]) -> str:
    enqueue_run = session.get("plan_enqueue_run") if isinstance(session.get("plan_enqueue_run"), dict) else {}
    task_id = truncate_label(enqueue_run.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("큐 등록됨", profile),
            f"{html.escape(task_id)} · queued",
            "Offdesk 큐에만 등록했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 다음 실행 검토",
        ]
    )


def render_plan_enqueue_run_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    enqueue_run = session.get("plan_enqueue_run") if isinstance(session.get("plan_enqueue_run"), dict) else {}
    reason = truncate_label(enqueue_run.get("error") or "큐 등록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("큐 등록 실패", profile),
            html.escape(reason),
            "Offdesk 큐에 등록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 queue 상태 확인",
        ]
    )


def render_plan_runtime_start_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("큐 등록 필요", profile),
            "먼저 큐 등록을 완료하세요.",
            "실행 시작은 아직 하지 않았습니다.",
            "완료 판정도 아직 없습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_runtime_started_message(*, profile: Any, session: dict[str, Any]) -> str:
    runtime_start = session.get("plan_runtime_start") if isinstance(session.get("plan_runtime_start"), dict) else {}
    task_id = truncate_label(runtime_start.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("실행 시작됨", profile),
            f"{html.escape(task_id)} · launched",
            "대상 task만 시작했습니다.",
            "완료 판정은 아직 없습니다.",
            "아래 버튼으로 상태 확인",
        ]
    )


def runtime_monitor_next_action_label(target_task: dict[str, Any]) -> str:
    next_action = target_task.get("next_safe_action") if isinstance(target_task.get("next_safe_action"), dict) else {}
    kind = str(next_action.get("kind") or "").strip()
    if kind in {"review_required", "closeout_check"}:
        return "로컬에서 closeout 검토"
    if kind in {"recovery_required", "resume_review_required", "result_artifact_missing"}:
        return "로컬에서 복구 검토"
    if kind == "runtime_monitoring":
        return "잠시 후 다시 확인"
    if kind in {"dispatch_pending", "approval_pending"}:
        return "로컬에서 큐/승인 확인"
    return "로컬에서 상태 확인"


def render_plan_runtime_monitor_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 시작 필요", profile),
            "먼저 실행 시작을 완료하세요.",
            "상태 확인은 대상 task만 봅니다.",
            "완료 판정은 아직 없습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_runtime_monitor_message(*, profile: Any, session: dict[str, Any]) -> str:
    monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
    target_task = monitor.get("target_task") if isinstance(monitor.get("target_task"), dict) else {}
    task_id = truncate_label(monitor.get("task_id") or target_task.get("task_id") or "task", max_chars=24)
    task_status = str(monitor.get("task_status") or target_task.get("status") or "unknown")
    status_labels = {
        "completed": ("실행 완료 확인", "완료 상태를 확인했습니다."),
        "failed": ("실행 실패 확인", "실패 상태입니다."),
        "resume_pending": ("복구 검토 필요", "복구 검토가 필요합니다."),
        "launched": ("실행 진행 중", "아직 실행 중입니다."),
        "running": ("실행 진행 중", "아직 실행 중입니다."),
        "queued": ("큐 대기 확인", "아직 큐에 있습니다."),
        "pending_approval": ("승인 대기 확인", "아직 승인 대기입니다."),
    }
    title, summary = status_labels.get(task_status, ("실행 상태 확인", "상태를 갱신했습니다."))
    return "\n".join(
        [
            title_with_profile(title, profile),
            f"{html.escape(task_id)} · {html.escape(task_status)}",
            summary,
            runtime_monitor_next_action_label(target_task),
            "결과 승인은 아직 없습니다.",
        ]
    )


def render_plan_runtime_monitor_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
    reason = truncate_label(monitor.get("error") or "상태 확인 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("상태 확인 실패", profile),
            html.escape(reason),
            "대상 task만 확인하려 했습니다.",
            "완료 판정은 아직 없습니다.",
            "로컬에서 상태 확인",
        ]
    )


def render_plan_closeout_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("완료 확인 필요", profile),
            "먼저 completed 상태를 확인하세요.",
            "마무리 패킷은 완료 task만 대상입니다.",
            "결과 승인은 아직 없습니다.",
            "아래 버튼으로 상태 확인",
        ]
    )


def render_plan_closeout_packet_message(*, profile: Any, session: dict[str, Any]) -> str:
    closeout_packet = session.get("plan_closeout_packet") if isinstance(session.get("plan_closeout_packet"), dict) else {}
    closeout_output = closeout_packet.get("closeout_output") if isinstance(closeout_packet.get("closeout_output"), dict) else {}
    closeout_id = truncate_label(closeout_output.get("closeout_id") or "closeout", max_chars=30)
    summary = closeout_output.get("summary") if isinstance(closeout_output.get("summary"), dict) else {}
    open_count = int(closeout_packet.get("open_decision_count") or summary.get("open_decision_records") or 0)
    return "\n".join(
        [
            title_with_profile("마무리 패킷 생성됨", profile),
            f"{html.escape(closeout_id)} · review needed",
            "closeout 자료만 만들었습니다.",
            f"열린 검토 {open_count}개 · 결과 승인은 아직 없습니다.",
            "로컬에서 closeout-review 검토",
        ]
    )


def render_plan_closeout_packet_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    closeout_packet = session.get("plan_closeout_packet") if isinstance(session.get("plan_closeout_packet"), dict) else {}
    reason = truncate_label(closeout_packet.get("error") or "마무리 패킷 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("마무리 패킷 실패", profile),
            html.escape(reason),
            "대상 task만 closeout하려 했습니다.",
            "결과 승인은 아직 없습니다.",
            "로컬에서 closeout 확인",
        ]
    )


def render_plan_closeout_review_handoff_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = (
        session.get("plan_closeout_review_handoff")
        if isinstance(session.get("plan_closeout_review_handoff"), dict)
        else {}
    )
    closeout_id = truncate_label(handoff.get("closeout_id") or "closeout", max_chars=30)
    if handoff.get("approved_verdict_may_accept_truth"):
        warning = "approved는 accepted truth가 될 수 있습니다."
    else:
        warning = "follow-up이 남아도 로컬 검토가 필요합니다."
    return "\n".join(
        [
            title_with_profile("마무리 검토 준비됨", profile),
            f"{html.escape(closeout_id)} · verdict ready",
            "Telegram에서 verdict를 기록할 수 있습니다.",
            warning,
            "아래 버튼에서 verdict 선택",
        ]
    )


def render_plan_closeout_review_handoff_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = (
        session.get("plan_closeout_review_handoff")
        if isinstance(session.get("plan_closeout_review_handoff"), dict)
        else {}
    )
    reason = truncate_label(handoff.get("error") or "마무리 검토 준비 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("마무리 검토 준비 실패", profile),
            html.escape(reason),
            "closeout packet만 참조하려 했습니다.",
            "verdict 실행은 하지 않았습니다.",
            "로컬에서 closeout 확인",
        ]
    )


def render_plan_closeout_verdict_message(*, profile: Any, session: dict[str, Any]) -> str:
    verdict = session.get("plan_closeout_verdict") if isinstance(session.get("plan_closeout_verdict"), dict) else {}
    output = (
        verdict.get("closeout_review_output")
        if isinstance(verdict.get("closeout_review_output"), dict)
        else {}
    )
    receipt = output.get("closeout_receipt") if isinstance(output.get("closeout_receipt"), dict) else {}
    recorded_verdict = truncate_label(verdict.get("verdict") or output.get("verdict") or "verdict", max_chars=18)
    acceptance = truncate_label(receipt.get("acceptance_status") or "not_accepted", max_chars=24)
    if receipt.get("acceptance_status") == "accepted":
        truth_line = "accepted truth가 기록됐습니다."
    elif recorded_verdict == "approved":
        truth_line = "follow-up이 남아 아직 accepted는 아닙니다."
    else:
        truth_line = "accepted truth는 아직 없습니다."
    return "\n".join(
        [
            title_with_profile("마무리 verdict 기록됨", profile),
            f"{html.escape(recorded_verdict)} · {html.escape(acceptance)}",
            truth_line,
            "프로젝트 파일 변경은 없습니다.",
            "로컬에서 return package 확인",
        ]
    )


def render_plan_closeout_verdict_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    verdict = session.get("plan_closeout_verdict") if isinstance(session.get("plan_closeout_verdict"), dict) else {}
    reason = truncate_label(verdict.get("error") or "verdict 기록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("verdict 기록 실패", profile),
            html.escape(reason),
            "accepted truth는 만들지 않았습니다.",
            "프로젝트 파일 변경은 없습니다.",
            "로컬에서 closeout-review 확인",
        ]
    )


def render_plan_runtime_start_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    runtime_start = session.get("plan_runtime_start") if isinstance(session.get("plan_runtime_start"), dict) else {}
    reason = truncate_label(runtime_start.get("error") or "실행 시작 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 시작 실패", profile),
            html.escape(reason),
            "대상 task를 시작하지 못했습니다.",
            "완료 판정은 아직 없습니다.",
            "로컬에서 queue 상태 확인",
        ]
    )


def render_plan_review_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
    reason = truncate_label(review.get("error") or "승인 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 승인 실패", profile),
            html.escape(reason),
            "계획 검토를 기록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_registration_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
    reason = truncate_label(registration.get("error") or "등록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 등록 실패", profile),
            html.escape(reason),
            "계획을 등록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_project_path_required_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "직접 입력", max_chars=24)
    return "\n".join(
        [
            title_with_profile("경로 확인 필요", profile),
            f"{html.escape(name)} 위치를 아직 모릅니다.",
            "프로젝트 경로를 직접 입력하세요.",
            "아직 실행은 시작하지 않았습니다.",
            "보류 또는 다시 선택 가능",
        ]
    )


def render_remote_plan_note_message(*, profile: Any, session: dict[str, Any]) -> str:
    return "\n".join(
        [
            title_with_profile("계획 의견 추가", profile),
            "세션에 의견을 남겼습니다.",
            "초기화 검토 전 참고합니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def manual_project_candidate(text: str) -> dict[str, Any]:
    label = truncate_label(text, max_chars=40) or "직접 입력"
    return {
        "schema": PROJECT_CANDIDATE_SCHEMA,
        "rank": None,
        "score": 0,
        "project_key": slugify_project_key(label),
        "display_name": label,
        "workspace_path_hint": "manual_input",
        "is_git_repo": False,
        "branch": None,
        "head": None,
        "dirty": None,
        "readiness": "not_git",
        "risk": "high",
        "autonomy_fit": "low",
        "reasons": ["manual input", "path not resolved"],
        "next_step": "init_review",
        "manual_input": True,
    }


def render_project_selection_error_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    labels = " · ".join(str(candidate.get("rank")) for candidate in candidates[:3])
    return "\n".join(
        [
            title_with_profile("선택 확인 필요", profile),
            "입력한 후보를 찾지 못했습니다.",
            f"가능한 번호: {html.escape(labels or '없음')}",
            "버튼 또는 번호/이름 직접 입력",
        ]
    )


def render_project_selection_deferred_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 선택 보류", profile),
            "세션을 보류했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "다시 시작하려면 계획 요청을 입력하세요.",
        ]
    )


def create_remote_plan_session(
    args: argparse.Namespace,
    *,
    chat_hash: str,
    request_text: str,
    parsed_command: dict[str, Any],
    feedback_context: dict[str, Any] | None,
    decision_id: Any = None,
) -> dict[str, Any]:
    agent_intent = parsed_command.get("agent_intent") if isinstance(parsed_command.get("agent_intent"), dict) else None
    build_plan_readiness = readiness_from_agent_intent(agent_intent)
    seed = f"{utc_now()}|{chat_hash}|{request_text}"
    session = {
        "schema": REMOTE_PLAN_SESSION_SCHEMA,
        "session_id": "telegram-plan-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12],
        "profile": args.profile,
        "chat_id_hash": chat_hash,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stage": "project_selection",
        "request_text": sanitize_text(request_text, max_chars=1200),
        "feedback_kind": str(parsed_command.get("feedback_kind") or "planning_request"),
        "feedback_context": feedback_context,
        "agent_intent": agent_intent,
        "decision_feedback_decision_id": decision_id,
        "execution_authorized": False,
        "approval_authorized": False,
        "action_readiness": {
            "project_scan": action_readiness(
                "project_scan",
                "healthy",
                reason="workspace_scan_available",
                allowed_actions=["project_selection", "manual_path_check"],
            ),
            "build_plan": build_plan_readiness
            if isinstance(build_plan_readiness, dict)
            else action_readiness(
                "build_plan",
                "healthy",
                reason="agent_intent_not_required",
                allowed_actions=["project_scan", "plan_draft"],
                blocked_actions=["arbitrary_launch", "shell"],
                recovery_hint="실행은 reviewed bound task만 가능",
            ),
            "start_offdesk": action_readiness(
                "start_offdesk",
                "guarded",
                reason="reviewed_bound_task_only",
                allowed_actions=["bound_enqueue_run", "task_scoped_start", "task_scoped_monitor"],
                blocked_actions=["arbitrary_launch", "shell", "accepted_truth"],
                recovery_hint="계획 승인, 게이트, 브리프, 워크로드 binding 후 대상 task만 시작",
            ),
        },
        "candidates": scan_project_candidates(
            args,
            request_text=request_text,
            agent_intent=agent_intent,
        ),
    }
    return session


def root_marker_summary(path: pathlib.Path) -> list[str]:
    return project_marker_names(path)


def documentation_summary(path: pathlib.Path) -> list[str]:
    names = (
        "README.md",
        "README_KO.md",
        "AGENTS.md",
        "CURRENT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    )
    return [name for name in names if (path / name).exists()]


def entrypoint_summary(path: pathlib.Path) -> list[str]:
    names = ("Cargo.toml", "pyproject.toml", "package.json", "Makefile", "justfile", "uv.lock")
    return [name for name in names if (path / name).exists()]


def project_init_command_preview(args: argparse.Namespace, candidate: dict[str, Any]) -> list[str]:
    workspace_path = str(candidate.get("workspace_path") or "").strip()
    project_key = str(candidate.get("project_key") or "project").strip() or "project"
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "project",
            "init",
            workspace_path,
            "--project-key",
            project_key,
            "--include-git",
            "--json",
        ]
    )
    return command


def create_project_init_preview(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = pathlib.Path(str(candidate.get("workspace_path") or ""))
    preview = {
        "schema": PROJECT_INIT_PREVIEW_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": candidate.get("project_key"),
        "display_name": candidate.get("display_name"),
        "workspace_path": str(workspace_path),
        "workspace_path_hint": candidate.get("workspace_path_hint"),
        "path_exists": workspace_path.exists(),
        "path_is_dir": workspace_path.is_dir(),
        "root_markers": root_marker_summary(workspace_path) if workspace_path.is_dir() else [],
        "documentation_sources": documentation_summary(workspace_path) if workspace_path.is_dir() else [],
        "entrypoints": entrypoint_summary(workspace_path) if workspace_path.is_dir() else [],
        "is_git_repo": is_git_repo(workspace_path) if workspace_path.is_dir() else False,
        "dirty": candidate.get("dirty"),
        "recommended_next_command": project_init_command_preview(args, candidate)
        if workspace_path.is_dir()
        else [],
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
        "notes": [
            "This preview does not run project init.",
            "Telegram selection does not authorize launch, approval, shell, or git mutation.",
        ],
    }
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    artifact_path = artifact_dir / "PROJECT_INIT_PREVIEW.json"
    write_json(artifact_path, preview)
    preview["artifact_path"] = str(artifact_path)
    return preview


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


def run_project_init_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = pathlib.Path(str(candidate.get("workspace_path") or ""))
    command = project_init_command_preview(args, candidate)
    run = {
        "schema": PROJECT_INIT_RUN_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": candidate.get("project_key"),
        "display_name": candidate.get("display_name"),
        "workspace_path": str(workspace_path),
        "workspace_path_hint": candidate.get("workspace_path_hint"),
        "command": command,
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    artifact_path = artifact_dir / "PROJECT_INIT_RUN.json"
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.project_init_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    run["returncode"] = process.returncode
    if process.returncode != 0:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    try:
        output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(f"project init did not return JSON: {error}", max_chars=400),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    run.update(
        {
            "status": "created",
            "project_init_output": output,
            "artifact_path": str(artifact_path),
        }
    )
    write_json(artifact_path, run)
    return run


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


def build_multiturn_plan_draft(session: dict[str, Any], init_run: dict[str, Any]) -> dict[str, Any]:
    output = init_run.get("project_init_output") if isinstance(init_run.get("project_init_output"), dict) else {}
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    project_key = str(output.get("project_key") or init_run.get("project_key") or "project")
    request_text = sanitize_text(session.get("request_text") or "", max_chars=500)
    plan_id = f"telegram_plan_{sha256_id(str(session.get('session_id') or '') + ':' + project_key)}"
    module_count = int(summary.get("module_candidate_count") or 0)
    evidence_count = int(summary.get("evidence_source_count") or 0)
    blocker_count = int(summary.get("module_operation_preflight_blocker_count") or 0)
    return {
        "schema": "offdesk_multiturn_plan.v1",
        "plan_id": plan_id,
        "created_at": utc_now(),
        "profile_key": "telegram_remote_plan",
        "profile_name": "Telegram Remote Plan Draft",
        "project_key": project_key,
        "source": {
            "schema": PROJECT_INIT_RUN_SCHEMA,
            "session_id": session.get("session_id"),
            "project_init_id": output.get("id"),
            "read_only_project_state": output.get("read_only_project_state") is True,
            "requires_operator_review": output.get("requires_operator_review") is not False,
        },
        "request": {
            "transport": "telegram",
            "operator_request": request_text,
        },
        "project_summary": {
            "module_candidate_count": module_count,
            "evidence_source_count": evidence_count,
            "module_operation_preflight_blocker_count": blocker_count,
            "ready_for_ondesk_start": bool(summary.get("ready_for_ondesk_start", False)),
            "ready_for_offdesk_runtime": bool(summary.get("ready_for_offdesk_runtime", False)),
        },
        "decision": {
            "ready_for_operator_review": True,
            "ready_for_launch_preparation": False,
            "ready_for_enqueue": False,
            "reason": "Telegram created a bounded draft from a project initialization packet; operator review is required before registration or launch preparation.",
        },
        "execution_sequence": [
            {
                "id": "review_initialization_packet",
                "objective": "Review the project initialization packet, first reads, candidate modules, and blockers before any runtime work.",
                "stop_condition": "Stop when the operator confirms scope, blockers, and evidence are understood.",
            },
            {
                "id": "prepare_registered_plan",
                "objective": "Convert the reviewed draft into a registered Offdesk plan only after operator review.",
                "stop_condition": "Stop at plan registration; launch preparation and runtime dispatch remain separate approvals.",
            },
        ],
        "authority": {
            "read_only_plan": True,
            "does_not_authorize": PLAN_DRAFT_AUTHORITY_DENIALS,
        },
    }


def create_plan_draft(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    init_run: dict[str, Any],
) -> dict[str, Any]:
    output = init_run.get("project_init_output") if isinstance(init_run.get("project_init_output"), dict) else {}
    project_key = str(output.get("project_key") or init_run.get("project_key") or "project")
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    plan_path = artifact_dir / "OFFDESK_PLAN_DRAFT.json"
    receipt_path = artifact_dir / "PLAN_DRAFT_VALIDATION.json"
    plan = build_multiturn_plan_draft(session, init_run)
    write_json(plan_path, plan)
    plan_sha256 = sha256_hex(plan_path.read_bytes())
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
            "--dry-run",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_DRAFT_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_artifact_path": str(plan_path),
        "plan_sha256": plan_sha256,
        "validation_command": command,
        "dry_run": True,
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_draft_timeout_sec)),
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
        validation_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan dry-run did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "validated",
            "validation_output": validation_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


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


def prepare_plan_launch_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    output = review.get("review_output") if isinstance(review.get("review_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or review.get("project_key") or "project")
    plan_ref = str(review.get("plan_ref") or output.get("plan_id") or "").strip()
    review_id = str(output.get("review_id") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_LAUNCH_PREP.json"
    copied_source_json = str(review.get("copied_source_json") or artifacts.get("copied_source_json") or "").strip()
    expected_source_sha = str(review.get("expected_source_sha256") or output.get("source_sha256") or "").strip()
    review_record_json = str(artifacts.get("review_record_json") or output.get("review_record_json") or "").strip()
    receipt = {
        "schema": PLAN_LAUNCH_PREP_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_ref": plan_ref,
        "review_id": review_id,
        "copied_source_json": copied_source_json,
        "review_record_json": review_record_json,
        "expected_source_sha256": expected_source_sha,
        "launch_preparation_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not plan_ref or not review_id:
        receipt.update(
            {
                "status": "error",
                "error": "approved plan review id unavailable",
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
                    "error": "registered plan source changed after plan review",
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
            "plan-launch-prep",
            plan_ref,
            "--review-id",
            review_id,
            "--prepared-by",
            "telegram",
            "--notes",
            "Telegram operator requested a launch-preparation packet; runtime gate approval remains separate.",
            "--json",
        ]
    )
    receipt["launch_prep_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_launch_prep_timeout_sec)),
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
        launch_prep_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"launch prep did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "prepared" if launch_prep_output.get("schema") == "offdesk_plan_launch_prep.v1" else "created",
            "launch_prep_output": launch_prep_output,
            "artifact_path": str(receipt_path),
        }
    )
    artifacts = launch_prep_output.get("artifacts") if isinstance(launch_prep_output.get("artifacts"), dict) else {}
    launch_prep_json = str(artifacts.get("launch_prep_json") or "").strip()
    if launch_prep_json:
        receipt["launch_prep_json"] = launch_prep_json
        try:
            receipt["launch_prep_sha256"] = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
        except OSError as error:
            receipt["launch_prep_hash_error"] = sanitize_text(str(error), max_chars=200)
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


def create_execution_brief_from_gate_resolution(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    gate_resolution: dict[str, Any],
) -> dict[str, Any]:
    resolution_output = (
        gate_resolution.get("resolution_output")
        if isinstance(gate_resolution.get("resolution_output"), dict)
        else {}
    )
    decision = str(gate_resolution.get("decision") or "")
    project_key = str(gate_resolution.get("project_key") or resolution_output.get("project_key") or "project")
    request_id = str(gate_resolution.get("request_id") or resolution_output.get("request_id") or session.get("session_id") or "")
    task_id = str(gate_resolution.get("task_id") or resolution_output.get("task_id") or "")
    approval_id = str(gate_resolution.get("approval_id") or resolution_output.get("approval_id") or "")
    launch_prep_json = str(gate_resolution.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(gate_resolution.get("expected_launch_prep_sha256") or gate_resolution.get("current_launch_prep_sha256") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    brief_path = artifact_dir / "EXECUTION_BRIEF.json"
    receipt_path = artifact_dir / "PLAN_EXECUTION_BRIEF.json"
    receipt = {
        "schema": PLAN_EXECUTION_BRIEF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "approval_id": approval_id,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "execution_brief_json": str(brief_path),
        "execution_brief_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if decision != "approved" or str(resolution_output.get("status") or "") != "approved":
        receipt.update(
            {
                "status": "error",
                "error": "gate approval is not approved",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not project_key or not request_id or not task_id or not approval_id:
        receipt.update(
            {
                "status": "error",
                "error": "approved gate resolution is missing execution context",
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
                    "error": "launch-preparation packet changed after gate approval",
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
    fresh_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=max(1, int(args.execution_brief_ttl_minutes))
    )
    execution_brief = {
        "request_id": request_id,
        "task_id": task_id,
        "project_key": project_key,
        "approved": True,
        "allowed_runtime_mutations": ["dispatch.runtime"],
        "allowed_canonical_mutations": [],
        "fresh_until": fresh_until.isoformat(),
    }
    write_json(brief_path, execution_brief)
    receipt.update(
        {
            "status": "created",
            "execution_brief": execution_brief,
            "execution_brief_sha256": sha256_hex(brief_path.read_bytes()),
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_enqueue_handoff_from_execution_brief(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    execution_brief_receipt: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_ENQUEUE_HANDOFF.json"
    execution_brief_json = str(execution_brief_receipt.get("execution_brief_json") or "").strip()
    project_key = str(execution_brief_receipt.get("project_key") or "project").strip()
    request_id = str(execution_brief_receipt.get("request_id") or session.get("session_id") or "").strip()
    task_id = str(execution_brief_receipt.get("task_id") or "").strip()
    expected_brief_sha = str(execution_brief_receipt.get("execution_brief_sha256") or "").strip()
    command_template = [args.forager_bin]
    if args.profile:
        command_template.extend(["--profile", args.profile])
    command_template.extend(
        [
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            project_key,
            "--request-id",
            request_id,
            "--task-id",
            task_id,
            "--brief",
            execution_brief_json,
            "--mutation-class",
            "dispatch.runtime",
            "--cmd",
            "<reviewed-workload-command-required>",
            "--workdir",
            "<reviewed-project-workdir-required>",
            "--log-artifact",
            "<reviewed-log-artifact-required>",
            "--result-artifact",
            "<reviewed-result-artifact-required>",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_ENQUEUE_HANDOFF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "execution_brief_json": execution_brief_json,
        "expected_execution_brief_sha256": expected_brief_sha,
        "command_template": command_template,
        "prepared_workload_required": True,
        "reviewed_workload_command_required": True,
        "required_local_review": [
            "read EXECUTION_BRIEF.json",
            "confirm a reviewed workload command",
            "confirm workdir and artifacts",
            "run enqueue locally only after review",
        ],
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if execution_brief_receipt.get("status") != "created" or not execution_brief_json:
        receipt.update(
            {
                "status": "error",
                "error": "execution brief receipt is not ready",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not project_key or not request_id or not task_id:
        receipt.update(
            {
                "status": "error",
                "error": "execution brief is missing enqueue context",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"execution brief unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_execution_brief_sha256"] = current_sha
    if expected_brief_sha and current_sha != expected_brief_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "execution brief changed after approval",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "created",
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


def bind_prepared_workload_to_execution_brief(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    enqueue_handoff: dict[str, Any],
    manifest_path: pathlib.Path,
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_WORKLOAD_BINDING.json"
    execution_brief_json = str(enqueue_handoff.get("execution_brief_json") or "").strip()
    expected_brief_sha = str(enqueue_handoff.get("expected_execution_brief_sha256") or enqueue_handoff.get("current_execution_brief_sha256") or "").strip()
    project_key = str(enqueue_handoff.get("project_key") or "").strip()
    request_id = str(enqueue_handoff.get("request_id") or "").strip()
    task_id = str(enqueue_handoff.get("task_id") or "").strip()
    prepared_task_json = str(manifest_path.expanduser().resolve())
    receipt = {
        "schema": PLAN_WORKLOAD_BINDING_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "execution_brief_json": execution_brief_json,
        "prepared_task_json": prepared_task_json,
        "expected_execution_brief_sha256": expected_brief_sha,
        "workload_binding_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if enqueue_handoff.get("status") != "created":
        receipt.update(
            {
                "status": "error",
                "error": "enqueue handoff is not ready",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"prepared workload unreadable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not isinstance(manifest, dict):
        receipt.update(
            {
                "status": "error",
                "error": "prepared workload manifest is not an object",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    blockers: list[str] = []
    if manifest.get("kind") != "forager_offdesk_prepared_workload":
        blockers.append("prepared_workload_kind_mismatch")
    preflight = manifest.get("preflight") if isinstance(manifest.get("preflight"), dict) else {}
    if preflight.get("ready_for_enqueue") is not True:
        blockers.append("preflight_not_ready_for_enqueue")
    review = preflight.get("review_artifact") if isinstance(preflight.get("review_artifact"), dict) else {}
    if review and (review.get("ready") is not True or str(review.get("decision") or "") != "needs_approval"):
        blockers.append("workload_review_not_ready")
    for key, expected in (("project_key", project_key), ("request_id", request_id), ("task_id", task_id)):
        actual = str(manifest.get(key) or "").strip()
        if not expected or actual != expected:
            blockers.append(f"{key}_mismatch")
    safety = manifest.get("safety") if isinstance(manifest.get("safety"), dict) else {}
    if safety.get("capability") != "dispatch.runtime":
        blockers.append("capability_not_dispatch_runtime")
    if safety.get("approval_required_before_dispatch") is not True:
        blockers.append("dispatch_approval_not_required")
    workload_command = manifest.get("workload_command")
    if not isinstance(workload_command, list) or not workload_command:
        blockers.append("workload_command_missing")
    enqueue_args = manifest.get("enqueue_args")
    if not isinstance(enqueue_args, list) or not enqueue_args:
        blockers.append("enqueue_args_missing")
        enqueue_args = []
    else:
        enqueue_text = " ".join(str(item) for item in enqueue_args)
        if "dispatch.runtime" not in enqueue_text:
            blockers.append("enqueue_missing_dispatch_runtime")
        if "--cmd" not in [str(item) for item in enqueue_args]:
            blockers.append("enqueue_missing_workload_command")
    repo = str(manifest.get("repo") or "").strip()
    out_dir = str(manifest.get("out_dir") or "").strip()
    workload_wrapper = str(manifest.get("workload_wrapper") or "").strip()
    if repo and not pathlib.Path(repo).exists():
        blockers.append("repo_path_missing")
    if out_dir and not pathlib.Path(out_dir).exists():
        blockers.append("out_dir_missing")
    if not workload_wrapper or not pathlib.Path(workload_wrapper).exists():
        blockers.append("workload_wrapper_missing")
    if contains_secret_like_text(manifest):
        blockers.append("manifest_contains_secret_like_text")
    if execution_brief_json:
        try:
            current_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_sha
            if expected_brief_sha and current_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    else:
        blockers.append("execution_brief_missing")
    receipt["prepared_task_sha256"] = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
    receipt["manifest_summary"] = {
        "title": sanitize_text(manifest.get("title") or "", max_chars=120),
        "project_key": manifest.get("project_key"),
        "request_id": manifest.get("request_id"),
        "task_id": manifest.get("task_id"),
        "duration_minutes": manifest.get("duration_minutes"),
        "max_iterations": manifest.get("max_iterations"),
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "repo": repo,
        "out_dir": out_dir,
        "workload_wrapper": workload_wrapper,
    }
    receipt["repo"] = repo
    receipt["out_dir"] = out_dir
    receipt["workload_wrapper"] = workload_wrapper
    receipt["manifest_enqueue_args"] = [str(item) for item in enqueue_args]
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    bound_enqueue_args = [str(item) for item in enqueue_args]
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--project-key", project_key)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--request-id", request_id)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--task-id", task_id)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--brief", execution_brief_json)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--mutation-class", "dispatch.runtime")
    if "--json" not in bound_enqueue_args:
        bound_enqueue_args.append("--json")
    receipt.update(
        {
            "status": "bound",
            "ready_for_local_enqueue_review": True,
            "bound_enqueue_args": bound_enqueue_args,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def command_contains_subsequence(command: list[str], subsequence: list[str]) -> bool:
    if not subsequence:
        return True
    values = [str(item) for item in command]
    limit = len(values) - len(subsequence) + 1
    for index in range(max(0, limit)):
        if values[index : index + len(subsequence)] == subsequence:
            return True
    return False


def create_enqueue_run_from_workload_binding(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    workload_binding: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_ENQUEUE_RUN.json"
    command = workload_binding.get("bound_enqueue_args")
    if not isinstance(command, list):
        command = []
    command = [str(item) for item in command]
    prepared_task_json = str(workload_binding.get("prepared_task_json") or "").strip()
    execution_brief_json = str(workload_binding.get("execution_brief_json") or "").strip()
    project_key = str(workload_binding.get("project_key") or "").strip()
    request_id = str(workload_binding.get("request_id") or "").strip()
    task_id = str(workload_binding.get("task_id") or "").strip()
    receipt = {
        "schema": PLAN_ENQUEUE_RUN_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "prepared_task_json": prepared_task_json,
        "execution_brief_json": execution_brief_json,
        "workload_binding_json": str(workload_binding.get("artifact_path") or ""),
        "enqueue_command": command,
        "queue_mutation_authorized": True,
        "enqueue_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "runtime_authorized": False,
    }
    blockers: list[str] = []
    if workload_binding.get("status") != "bound":
        blockers.append("workload_binding_not_bound")
    if not command:
        blockers.append("enqueue_command_missing")
    elif not command_contains_subsequence(command, ["offdesk", "enqueue", "dispatch.runtime"]):
        blockers.append("enqueue_command_not_dispatch_runtime")
    forbidden_tokens = {"launch", "tick", "poll", "closeout", "ok", "cancel"}
    if any(str(item) in forbidden_tokens for item in command):
        blockers.append("enqueue_command_contains_forbidden_action")
    expected_prepared_sha = str(workload_binding.get("prepared_task_sha256") or "").strip()
    if prepared_task_json:
        try:
            current_prepared_sha = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
        except OSError as error:
            blockers.append("prepared_task_unavailable")
            receipt["prepared_task_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_prepared_task_sha256"] = current_prepared_sha
            if expected_prepared_sha and current_prepared_sha != expected_prepared_sha:
                blockers.append("prepared_task_changed")
    else:
        blockers.append("prepared_task_missing")
    expected_brief_sha = str(workload_binding.get("expected_execution_brief_sha256") or workload_binding.get("current_execution_brief_sha256") or "").strip()
    if execution_brief_json:
        try:
            current_brief_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_brief_sha
            if expected_brief_sha and current_brief_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    else:
        blockers.append("execution_brief_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.enqueue_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
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
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        enqueue_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"enqueue did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "queued",
            "enqueue_output": enqueue_output,
            "task_status": enqueue_output.get("status") if isinstance(enqueue_output, dict) else None,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_runtime_start_from_enqueue_run(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    enqueue_run: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_RUNTIME_START.json"
    project_key = str(enqueue_run.get("project_key") or "").strip()
    request_id = str(enqueue_run.get("request_id") or "").strip()
    task_id = str(enqueue_run.get("task_id") or "").strip()
    prepared_task_json = str(enqueue_run.get("prepared_task_json") or "").strip()
    execution_brief_json = str(enqueue_run.get("execution_brief_json") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "tick",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--limit",
            "1",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_RUNTIME_START_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "prepared_task_json": prepared_task_json,
        "execution_brief_json": execution_brief_json,
        "enqueue_run_json": str(enqueue_run.get("artifact_path") or ""),
        "tick_command": command,
        "runtime_start_authorized": True,
        "tick_authorized": True,
        "execution_authorized": True,
        "closeout_authorized": False,
        "accepted_truth_authorized": False,
    }
    blockers: list[str] = []
    if enqueue_run.get("status") != "queued":
        blockers.append("enqueue_run_not_queued")
    if not project_key or not task_id:
        blockers.append("runtime_start_context_missing")
    expected_prepared_sha = str(enqueue_run.get("current_prepared_task_sha256") or "").strip()
    if prepared_task_json:
        try:
            current_prepared_sha = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
        except OSError as error:
            blockers.append("prepared_task_unavailable")
            receipt["prepared_task_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_prepared_task_sha256"] = current_prepared_sha
            if expected_prepared_sha and current_prepared_sha != expected_prepared_sha:
                blockers.append("prepared_task_changed")
    expected_brief_sha = str(enqueue_run.get("current_execution_brief_sha256") or "").strip()
    if execution_brief_json:
        try:
            current_brief_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_brief_sha
            if expected_brief_sha and current_brief_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_start_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
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
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tick did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    updated_task_ids = tick_output.get("updated_task_ids") if isinstance(tick_output, dict) else []
    launched = int(tick_output.get("launched") or 0) if isinstance(tick_output, dict) else 0
    if launched <= 0 or task_id not in [str(item) for item in updated_task_ids or []]:
        receipt.update(
            {
                "status": "not_started",
                "error": "target task was not launched",
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "launched",
            "tick_output": tick_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_runtime_monitor_from_runtime_start(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    runtime_start: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_RUNTIME_MONITOR.json"
    project_key = str(runtime_start.get("project_key") or "").strip()
    request_id = str(runtime_start.get("request_id") or "").strip()
    task_id = str(runtime_start.get("task_id") or "").strip()
    tick_command = [args.forager_bin]
    tasks_command = [args.forager_bin]
    if args.profile:
        tick_command.extend(["--profile", args.profile])
        tasks_command.extend(["--profile", args.profile])
    tick_command.extend(
        [
            "offdesk",
            "tick",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--limit",
            "0",
            "--json",
        ]
    )
    tasks_command.extend(
        [
            "offdesk",
            "tasks",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_RUNTIME_MONITOR_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "runtime_start_json": str(runtime_start.get("artifact_path") or ""),
        "tick_command": tick_command,
        "tasks_command": tasks_command,
        "monitor_authorized": True,
        "poll_authorized": True,
        "dispatch_authorized": False,
        "closeout_authorized": False,
        "accepted_truth_authorized": False,
    }
    blockers: list[str] = []
    if runtime_start.get("status") != "launched":
        blockers.append("runtime_start_not_launched")
    if not project_key or not task_id:
        blockers.append("runtime_monitor_context_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_process = subprocess.run(
            tick_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_monitor_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["tick_returncode"] = tick_process.returncode
    if tick_process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(tick_process.stderr.strip() or tick_process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_output = json.loads(tick_process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tick did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tasks_process = subprocess.run(
            tasks_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_monitor_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["tasks_returncode"] = tasks_process.returncode
    if tasks_process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(tasks_process.stderr.strip() or tasks_process.stdout.strip(), max_chars=600),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tasks_output = json.loads(tasks_process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tasks did not return JSON: {error}", max_chars=400),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    target_task = None
    if isinstance(tasks_output, list):
        for task in tasks_output:
            if isinstance(task, dict) and str(task.get("task_id") or "") == task_id:
                target_task = task
                break
    if not isinstance(target_task, dict):
        receipt.update(
            {
                "status": "error",
                "error": "target task was not found",
                "tick_output": tick_output,
                "tasks_count": len(tasks_output) if isinstance(tasks_output, list) else None,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    task_status = str(target_task.get("status") or "unknown")
    if task_status in {"launched", "running"}:
        monitor_status = "running"
    elif task_status == "completed":
        monitor_status = "completed"
    elif task_status == "failed":
        monitor_status = "failed"
    elif task_status == "resume_pending":
        monitor_status = "resume_pending"
    else:
        monitor_status = "observed"
    receipt.update(
        {
            "status": monitor_status,
            "task_status": task_status,
            "tick_output": tick_output,
            "target_task": target_task,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_closeout_packet_from_runtime_monitor(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    runtime_monitor: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_PACKET.json"
    project_key = str(runtime_monitor.get("project_key") or "").strip()
    request_id = str(runtime_monitor.get("request_id") or "").strip()
    task_id = str(runtime_monitor.get("task_id") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "closeout",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--dry-run",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_CLOSEOUT_PACKET_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "runtime_monitor_json": str(runtime_monitor.get("artifact_path") or ""),
        "closeout_command": command,
        "closeout_packet_authorized": True,
        "closeout_review_authorized": False,
        "accepted_truth_authorized": False,
        "file_mutation_authorized": False,
    }
    blockers: list[str] = []
    if runtime_monitor.get("status") != "completed" or runtime_monitor.get("task_status") != "completed":
        blockers.append("runtime_monitor_not_completed")
    if not project_key or not task_id:
        blockers.append("closeout_context_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.closeout_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
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
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        closeout_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"closeout did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    summary = closeout_output.get("summary") if isinstance(closeout_output, dict) else {}
    tasks = closeout_output.get("tasks") if isinstance(closeout_output, dict) else []
    matched_task_ids = [
        str(task.get("task_id") or "")
        for task in tasks
        if isinstance(task, dict) and str(task.get("task_id") or "")
    ] if isinstance(tasks, list) else []
    if task_id not in matched_task_ids:
        receipt.update(
            {
                "status": "blocked",
                "error": "closeout did not include target task",
                "closeout_output": closeout_output,
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    open_decisions = closeout_output.get("open_decisions") if isinstance(closeout_output, dict) else []
    verification_commands = closeout_output.get("verification_commands") if isinstance(closeout_output, dict) else []
    receipt.update(
        {
            "status": "created",
            "closeout_id": closeout_output.get("closeout_id") if isinstance(closeout_output, dict) else None,
            "closeout_output": closeout_output,
            "completed_tasks": int(summary.get("completed_tasks") or 0) if isinstance(summary, dict) else 0,
            "open_decision_count": len(open_decisions) if isinstance(open_decisions, list) else 0,
            "verification_command_count": len(verification_commands) if isinstance(verification_commands, list) else 0,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def closeout_review_artifact_dir(closeout_output: dict[str, Any]) -> str:
    artifacts = closeout_output.get("artifacts") if isinstance(closeout_output.get("artifacts"), dict) else {}
    closeout_plan = str(artifacts.get("closeout_plan_json") or "").strip()
    if not closeout_plan:
        return ""
    return str(pathlib.Path(closeout_plan).expanduser().parent)


def closeout_review_command_templates(
    args: argparse.Namespace,
    *,
    artifact_dir: str,
) -> dict[str, list[str]]:
    base = [args.forager_bin]
    if args.profile:
        base.extend(["--profile", args.profile])
    commands: dict[str, list[str]] = {}
    for verdict in ("revise", "blocked", "approved"):
        commands[verdict] = [
            *base,
            "offdesk",
            "closeout-review",
            "--artifact-dir",
            artifact_dir,
            "--verdict",
            verdict,
            "--reviewer",
            "operator",
            "--notes",
            f"<{verdict}-review-notes>",
            "--json",
        ]
    return commands


def closeout_known_followups(closeout_output: dict[str, Any]) -> dict[str, int]:
    summary = closeout_output.get("summary") if isinstance(closeout_output.get("summary"), dict) else {}
    open_decisions = closeout_output.get("open_decisions") if isinstance(closeout_output.get("open_decisions"), list) else []
    documentation = (
        closeout_output.get("documentation_governance")
        if isinstance(closeout_output.get("documentation_governance"), dict)
        else {}
    )
    followups = {
        "open_decisions": len(open_decisions),
        "missing_artifacts": int(summary.get("missing_artifacts") or 0),
        "commercial_review_operations": int(summary.get("operations_requiring_commercial_review") or 0),
        "human_approval_operations": int(summary.get("operations_requiring_human_approval") or 0),
        "archive_candidates": int(summary.get("archive_candidates") or 0),
        "delete_candidates": int(summary.get("delete_candidates") or 0),
        "documentation_recommendations": int(documentation.get("recommendation_count") or 0),
        "documentation_audit_unavailable": 1 if documentation.get("error") else 0,
    }
    return {key: value for key, value in followups.items() if value > 0}


def create_closeout_review_handoff_from_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    closeout_packet: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_REVIEW_HANDOFF.json"
    closeout_output = (
        closeout_packet.get("closeout_output")
        if isinstance(closeout_packet.get("closeout_output"), dict)
        else {}
    )
    closeout_artifact_dir = closeout_review_artifact_dir(closeout_output)
    artifacts = closeout_output.get("artifacts") if isinstance(closeout_output.get("artifacts"), dict) else {}
    closeout_plan_json = str(artifacts.get("closeout_plan_json") or "")
    return_package_markdown = str(artifacts.get("return_package_markdown") or "")
    known_followups = closeout_known_followups(closeout_output)
    known_followup_count = sum(known_followups.values())
    receipt = {
        "schema": PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": closeout_packet.get("project_key"),
        "request_id": closeout_packet.get("request_id"),
        "task_id": closeout_packet.get("task_id"),
        "closeout_id": closeout_output.get("closeout_id"),
        "closeout_packet_json": str(closeout_packet.get("artifact_path") or ""),
        "artifact_dir": closeout_artifact_dir,
        "closeout_plan_json": closeout_plan_json,
        "return_package_markdown": return_package_markdown,
        "known_followups": known_followups,
        "known_followup_count": known_followup_count,
        "approved_verdict_may_accept_truth": known_followup_count == 0,
        "closeout_review_handoff_authorized": True,
        "remote_closeout_review_authorized": False,
        "closeout_review_authorized": False,
        "accepted_truth_authorized": False,
        "file_mutation_authorized": False,
        "local_review_required": True,
        "recommended_next_action": "Run a local closeout-review verdict after reading the closeout artifacts.",
        "artifact_path": str(receipt_path),
    }
    blockers: list[str] = []
    if closeout_packet.get("status") != "created":
        blockers.append("closeout_packet_not_created")
    if not closeout_artifact_dir or not closeout_plan_json:
        blockers.append("closeout_artifact_dir_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "closeout_review_handoff_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "created",
            "local_review_commands": closeout_review_command_templates(
                args,
                artifact_dir=closeout_artifact_dir,
            ),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def closeout_verdict_note(verdict: str, note: str) -> str:
    if note:
        return sanitize_text(note, max_chars=500)
    if verdict == "revise":
        return "Remote Telegram operator recorded revision-required closeout verdict; accepted truth remains blocked."
    if verdict == "approved":
        return "Remote Telegram operator recorded approved closeout verdict; accepted truth follows closeout-review receipt status."
    return "Remote Telegram operator recorded blocked closeout verdict; accepted truth remains blocked."


def create_closeout_verdict_from_handoff(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    handoff: dict[str, Any],
    verdict: str,
    note: str,
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_VERDICT.json"
    closeout_artifact_dir = str(handoff.get("artifact_dir") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "closeout-review",
            "--artifact-dir",
            closeout_artifact_dir,
            "--verdict",
            verdict,
            "--reviewer",
            "telegram-remote-operator",
            "--review-provider",
            "telegram-remote-operator",
            "--notes",
            closeout_verdict_note(verdict, note),
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_CLOSEOUT_VERDICT_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": handoff.get("project_key"),
        "request_id": handoff.get("request_id"),
        "task_id": handoff.get("task_id"),
        "closeout_id": handoff.get("closeout_id"),
        "closeout_review_handoff_json": str(handoff.get("artifact_path") or ""),
        "artifact_dir": closeout_artifact_dir,
        "verdict": verdict,
        "closeout_review_command": command,
        "remote_closeout_review_authorized": verdict in {"approved", "revise", "blocked"},
        "closeout_review_authorized": verdict in {"approved", "revise", "blocked"},
        "closeout_artifact_write_authorized": verdict in {"approved", "revise", "blocked"},
        "accepted_truth_authorized": verdict == "approved",
        "accepted_truth_recorded": False,
        "project_file_mutation_authorized": False,
        "file_mutation_authorized": False,
        "artifact_path": str(receipt_path),
    }
    blockers: list[str] = []
    if verdict not in {"approved", "revise", "blocked"}:
        blockers.append("unsupported_closeout_verdict")
    if handoff.get("status") != "created":
        blockers.append("closeout_review_handoff_not_created")
    if not closeout_artifact_dir:
        blockers.append("closeout_artifact_dir_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.closeout_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
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
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        closeout_review_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"closeout-review did not return JSON: {error}", max_chars=400),
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    closeout_receipt = (
        closeout_review_output.get("closeout_receipt")
        if isinstance(closeout_review_output, dict)
        else {}
    )
    acceptance_status = (
        str(closeout_receipt.get("acceptance_status") or "")
        if isinstance(closeout_receipt, dict)
        else ""
    )
    receipt.update(
        {
            "status": "recorded",
            "acceptance_status": acceptance_status or "unknown",
            "accepted_truth_recorded": acceptance_status == "accepted",
            "closeout_review_output": closeout_review_output,
        }
    )
    write_json(receipt_path, receipt)
    return receipt


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


def is_core_or_slash_command_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if stripped in BUTTON_COMMAND_ALIASES:
        return True
    if not stripped.startswith("/"):
        return False
    try:
        first = shlex.split(stripped)[0]
    except (ValueError, IndexError):
        first = stripped.split(maxsplit=1)[0]
    return normalize_command_name(first) in {"start", "help", "status", "pending", "plans", "show"}


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


def interaction_context_label(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    focus_kind = str(context.get("focus_kind") or "").strip()
    focus_ref = str(context.get("focus_ref") or "").strip()
    focus_label = str(context.get("focus_label") or "").strip()
    if focus_kind == "plan" and focus_ref:
        suffix = f" · {focus_label}" if focus_label else ""
        return f"계획 {focus_ref}{suffix}"
    if focus_kind == "approval" and focus_ref:
        suffix = f" · {focus_label}" if focus_label else ""
        return f"승인 {focus_ref}{suffix}"
    if focus_label:
        return focus_label
    command = str(context.get("command") or "").strip()
    return command


def mobile_card_contract(message: str) -> dict[str, Any]:
    lines = str(message or "").splitlines()
    content_lines = [line.strip() for line in lines if line.strip()]
    warnings: list[str] = []
    if len(lines) > MOBILE_CARD_MAX_LINES:
        warnings.append("too_many_lines")
    if len(str(message or "")) > MOBILE_CARD_MAX_CHARS:
        warnings.append("too_many_chars")
    has_title = bool(content_lines and content_lines[0].startswith("<b>"))
    body_lines = content_lines[1:] if has_title else content_lines
    action_markers = (
        "아래 버튼",
        "로컬에서",
        "다시 확인",
        "직접 의견",
        "직접 입력",
        "세부 내용",
        "다음 조치:",
    )

    def is_action_line(line: str) -> bool:
        return any(marker in line for marker in action_markers)

    has_status_headline = any(
        not line.startswith("기준 ") and not is_action_line(line)
        for line in body_lines
    )
    has_next_action = any(is_action_line(line) for line in body_lines)
    if not has_title:
        warnings.append("missing_title")
    if not has_status_headline:
        warnings.append("missing_status_headline")
    if not has_next_action:
        warnings.append("missing_next_action")
    leaked_terms = [term for term in MOBILE_CARD_FORBIDDEN_TERMS if term in message]
    if leaked_terms:
        warnings.append("forbidden_terms:" + ",".join(leaked_terms))
    return {
        "schema": MOBILE_CARD_CONTRACT_SCHEMA,
        "line_count": len(lines),
        "char_count": len(str(message or "")),
        "max_lines": MOBILE_CARD_MAX_LINES,
        "max_chars": MOBILE_CARD_MAX_CHARS,
        "has_title": has_title,
        "has_status_headline": has_status_headline,
        "has_next_action": has_next_action,
        "warnings": warnings,
    }


def choice_keyboard(context: dict[str, Any] | None = None) -> dict[str, Any]:
    rows: list[list[str]] = []
    seen: set[str] = set()

    def add_row(*labels: str) -> None:
        row: list[str] = []
        for label in labels:
            text = str(label or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            row.append(text)
        if row:
            rows.append(row)

    context_kind = str(context.get("context_kind") or "") if isinstance(context, dict) else ""
    next_command = str(context.get("next_command") or "").strip() if isinstance(context, dict) else ""
    if context_kind == REMOTE_PLAN_SESSION_CONTEXT_KIND:
        choice_labels = (
            context.get("choice_labels") if isinstance(context, dict) else []
        )
        labels = [str(label or "").strip() for label in choice_labels if str(label or "").strip()] if isinstance(choice_labels, list) else []
        for index in range(0, len(labels), 2):
            add_row(*labels[index : index + 2])
        add_row("다시 스캔", "보류")
        add_row("상태", "계획")
        add_row("승인 대기", "도움말")
        for label in CORE_BUTTON_LABELS:
            if label not in seen:
                add_row(label)
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "input_field_placeholder": "번호/프로젝트명 또는 의견을 직접 입력",
        }
    if context_kind == REMOTE_PLAN_INIT_CONTEXT_KIND:
        choice_labels = context.get("choice_labels") if isinstance(context, dict) else []
        labels = [str(label or "").strip() for label in choice_labels if str(label or "").strip()] if isinstance(choice_labels, list) else []
        if labels:
            add_row(*labels[:2])
            add_row(*labels[2:4])
        else:
            add_row("초기화 검토", "다시 선택")
            add_row("보류")
        add_row("상태", "계획")
        add_row("승인 대기", "도움말")
        for label in CORE_BUTTON_LABELS:
            if label not in seen:
                add_row(label)
        return {
            "keyboard": rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
            "input_field_placeholder": "의견이나 프로젝트 경로를 직접 입력",
        }
    if next_command and next_command not in {"/status", "/pending", "/plans --latest", "/help"}:
        add_row(next_command)
    if context_kind == "status_attention":
        add_row("승인 대기", "계획")
        add_row("상태", "도움말")
    elif context_kind == "approval_attention":
        add_row("전체 승인", "상태")
        add_row("승인 대기", "계획")
        add_row("도움말")
    elif context_kind == "plan_attention":
        add_row("계획", "상태")
        add_row("승인 대기", "도움말")
    elif context_kind == "plan_detail":
        add_row("계획", "상태")
        add_row("승인 대기", "도움말")
    else:
        add_row("상태", "승인 대기")
        add_row("계획", "도움말")
    for label in CORE_BUTTON_LABELS:
        if label not in seen:
            add_row(label)
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "의견을 직접 입력할 수 있습니다",
    }


def button_resolves_to(button_text: str, command_text: str) -> bool:
    button = str(button_text or "").strip()
    command = str(command_text or "").strip()
    return bool(command) and (button == command or BUTTON_COMMAND_ALIASES.get(button) == command)


def choice_surface_contract(
    reply_markup: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    keyboard = reply_markup.get("keyboard") if isinstance(reply_markup, dict) else None
    button_texts: list[str] = []
    if isinstance(keyboard, list):
        for row in keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if isinstance(button, str):
                    button_texts.append(button)
                elif isinstance(button, dict):
                    button_texts.append(str(button.get("text") or ""))
    else:
        warnings.append("missing_keyboard")
    for label in CORE_BUTTON_LABELS:
        if label not in button_texts:
            warnings.append(f"missing_button:{label}")
    placeholder = ""
    if isinstance(reply_markup, dict):
        placeholder = str(reply_markup.get("input_field_placeholder") or "")
    if "의견" not in placeholder:
        warnings.append("missing_freeform_placeholder")
    next_command = str(context.get("next_command") or "").strip() if isinstance(context, dict) else ""
    has_contextual_choice = False
    choice_labels = context.get("choice_labels") if isinstance(context, dict) else None
    if isinstance(choice_labels, list) and choice_labels:
        expected = [str(label or "").strip() for label in choice_labels if str(label or "").strip()]
        has_contextual_choice = any(label in button_texts for label in expected)
        if not has_contextual_choice:
            warnings.append("missing_contextual_choice:choice_labels")
    if next_command:
        has_contextual_choice = any(button_resolves_to(button, next_command) for button in button_texts)
        if not has_contextual_choice:
            warnings.append(f"missing_contextual_choice:{next_command}")
    return {
        "schema": CHOICE_SURFACE_CONTRACT_SCHEMA,
        "button_texts": button_texts,
        "has_freeform_placeholder": "의견" in placeholder,
        "context_kind": context.get("context_kind") if isinstance(context, dict) else None,
        "context_command": next_command or None,
        "has_contextual_choice": has_contextual_choice,
        "warnings": warnings,
    }


def help_message(*, profile: Any, generated_at: Any) -> str:
    return "\n".join(
        [
            title_with_profile("Forager 원격 조작", profile),
            "상태, 승인 요청, 계획을 빠르게 확인합니다.",
            "버튼으로 조회하거나 직접 의견을 쓰세요.",
            "직접 입력: /status · /pending · /plans",
        ]
    )


def render_feedback_message(
    *,
    profile: Any,
    generated_at: Any,
    feedback_text: str,
    feedback_kind: str = "freeform_feedback",
    feedback_context: dict[str, Any] | None = None,
    inbox_status: str | None = None,
    agent_intent: dict[str, Any] | None = None,
) -> str:
    is_planning_request = feedback_kind == "planning_request"
    clarifying_question = agent_clarifying_question(agent_intent)
    if clarifying_question:
        return "\n".join(
            [
                title_with_profile("확인 필요", profile),
                "모델이 범위 확인을 요청했습니다.",
                html.escape(clarifying_question),
                "직접 입력으로 범위만 알려주세요.",
            ]
        )
    if inbox_status in {"recorded", "existing"}:
        status_line = "검토 목록에 넣었습니다." if is_planning_request else "의견을 검토 목록에 넣었습니다."
    elif inbox_status == "error":
        status_line = "요청은 저장했지만 검토 등록은 실패했습니다." if is_planning_request else "의견은 저장했지만 검토 목록 등록은 실패했습니다."
    else:
        status_line = "계획 요청을 저장했습니다." if is_planning_request else "의견을 저장했습니다."
    lines = [
        title_with_profile("계획 요청 접수" if is_planning_request else "의견 접수", profile),
        status_line,
    ]
    context_label = interaction_context_label(feedback_context)
    if context_label and not is_planning_request:
        lines.append(f"관련: {html.escape(context_label)}")
    if is_planning_request:
        lines.append("아직 실행은 시작하지 않았습니다.")
        lines.append("로컬에서 계획으로 바꾸세요.")
    else:
        lines.append("로컬에서 검토합니다.")
    return "\n".join(lines)


def agent_clarifying_question(agent_intent: dict[str, Any] | None) -> str | None:
    if not isinstance(agent_intent, dict):
        return None
    if not bool(agent_intent.get("requires_clarification")):
        return None
    question = sanitize_text(str(agent_intent.get("clarifying_question") or "").strip(), max_chars=180)
    return question or None


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


def render_command_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    command_text: str,
    *,
    mode: str,
    feedback_context: dict[str, Any] | None = None,
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
    if parsed.get("command") == "feedback":
        agent_intent = classify_feedback_with_agent(
            args,
            str(parsed.get("feedback_text") or command_text),
            feedback_context=feedback_context,
        )
        if isinstance(agent_intent, dict):
            parsed["agent_intent"] = agent_intent
            parsed["feedback_kind"] = str(
                agent_intent.get("feedback_kind") or parsed.get("feedback_kind") or "freeform_feedback"
            )
            parsed["reason"] = f"agent_intent:{agent_intent.get('intent') or 'unknown'}"
        result["parsed_command"] = parsed
        message_preview = render_feedback_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            feedback_text=str(parsed.get("feedback_text") or command_text),
            feedback_kind=str(parsed.get("feedback_kind") or "freeform_feedback"),
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


def telegram_api(token: str, method: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if hasattr(error, "read") else str(error)
        raise RemoteOperatorTelegramError(f"Telegram API HTTP error ({method}): {detail}") from error
    except (TimeoutError, http.client.RemoteDisconnected, ConnectionError) as error:
        raise RemoteOperatorTelegramError(
            f"Telegram API transport error ({method}): {type(error).__name__}: {error}"
        ) from error
    except urllib.error.URLError as error:
        raise RemoteOperatorTelegramError(f"Telegram API URL error ({method}): {error}") from error
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError(f"Telegram API invalid JSON ({method})") from error
    if not data.get("ok"):
        raise RemoteOperatorTelegramError(f"Telegram API error ({method}): {data}")
    return data


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    if not isinstance(state, dict):
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    state.setdefault("schema", "remote_operator_telegram_state.v1")
    state.setdefault("offset", 0)
    return state


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(path, state)


def last_context_for_chat_hash(state: dict[str, Any], chat_hash: Any) -> dict[str, Any] | None:
    contexts = state.get("last_interaction_context_by_chat")
    if not isinstance(contexts, dict):
        return None
    context = contexts.get(str(chat_hash or ""))
    return context if isinstance(context, dict) else None


def remember_context_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    rendered: dict[str, Any],
) -> None:
    context = rendered.get("interaction_context")
    parsed = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
    if not isinstance(context, dict) or parsed.get("command") == "feedback":
        return
    contexts = state.setdefault("last_interaction_context_by_chat", {})
    if not isinstance(contexts, dict):
        contexts = {}
        state["last_interaction_context_by_chat"] = contexts
    remembered = dict(context)
    remembered["remembered_at"] = utc_now()
    if isinstance(rendered.get("sent_message_id"), int):
        remembered["source_message_id"] = rendered["sent_message_id"]
    contexts[str(chat_hash or "")] = remembered


def remember_context_for_message(
    state: dict[str, Any],
    message: dict[str, Any],
    rendered: dict[str, Any],
) -> None:
    remember_context_for_chat_hash(state, sha256_short(chat_id_for(message)), rendered)


def get_updates(config: dict[str, Any], offset: int, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.replay_update_file:
        value = load_json(args.replay_update_file)
        if isinstance(value, dict) and isinstance(value.get("result"), list):
            raw_updates = value["result"]
        elif isinstance(value, list):
            raw_updates = value
        elif isinstance(value, dict):
            raw_updates = [value]
        else:
            raw_updates = []
        updates = [item for item in raw_updates if isinstance(item, dict)]
        return [
            item
            for item in updates
            if not isinstance(item.get("update_id"), int) or item["update_id"] >= int(offset)
        ]
    data = telegram_api(
        config["token"],
        "getUpdates",
        {
            "offset": int(offset),
            "timeout": max(0, int(args.poll_timeout_sec)),
            "allowed_updates": ["message"],
        },
        timeout_sec=max(int(args.api_timeout_sec), int(args.poll_timeout_sec) + 10),
    )
    updates = data.get("result", [])
    return [item for item in updates if isinstance(item, dict)] if isinstance(updates, list) else []


def send_message(
    config: dict[str, Any],
    chat_id: str,
    message: str,
    args: argparse.Namespace,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> int | None:
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if args.dry_run:
        return None
    data = telegram_api(
        config["token"],
        "sendMessage",
        payload,
        timeout_sec=max(1, int(args.api_timeout_sec)),
    )
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("message_id"), int):
        return int(result["message_id"])
    return None


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
        if active_session and not is_core_or_slash_command_text(text):
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
            feedback_context = last_context_for_chat_hash(state, chat_hash)
            rendered = render_command_result(
                args,
                config,
                text,
                mode="live_once",
                feedback_context=feedback_context,
            )
        rendered["updates_seen"] = len(updates)
        if isinstance(update_id, int):
            rendered["processed_update_id"] = update_id
        parsed_command = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
        if parsed_command.get("command") == "feedback":
            feedback_context = last_context_for_chat_hash(state, chat_hash)
            feedback_result = record_feedback(
                args,
                config,
                message,
                text,
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
                    feedback_text=str(parsed_command.get("feedback_text") or text),
                    feedback_kind=str(parsed_command.get("feedback_kind") or "freeform_feedback"),
                    feedback_context=feedback_context,
                    inbox_status=str(ingest_result.get("decision_feedback_ingest_status") or ""),
                    agent_intent=parsed_command.get("agent_intent")
                    if isinstance(parsed_command.get("agent_intent"), dict)
                    else None,
                )
                rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
                if str(parsed_command.get("feedback_kind") or "") == "planning_request":
                    session = create_remote_plan_session(
                        args,
                        chat_hash=chat_hash,
                        request_text=str(parsed_command.get("feedback_text") or text),
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
        remember_context_for_message(state, message, rendered)
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


def parse_timestamp(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


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
    last_poll_at = parse_timestamp(last_result.get("generated_at") or loop_status.get("generated_at"))
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
    try:
        agent_runtime_status = provider_status(resolve_agent_config(args))
    except RemoteOperatorTelegramError as error:
        agent_runtime_status = {
            "schema": "offdesk_llm_provider_resolution.v1",
            "status": "error",
            "error": sanitize_text(str(error), max_chars=240),
        }
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
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
    }


def send_command_text(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    target_chat_id = str(config.get("target_chat_id") or "").strip()
    if not target_chat_id:
        raise RemoteOperatorTelegramError("target chat id is missing")
    state = load_state(args.state_file)
    feedback_context = last_context_for_chat_hash(state, sha256_short(target_chat_id))
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
