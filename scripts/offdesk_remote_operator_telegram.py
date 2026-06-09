#!/usr/bin/env python3
"""Telegram adapter for read-only Forager Remote Operator projections.

This adapter is intentionally narrow. It maps a small Telegram command surface
to `forager offdesk remote-operator ... --json` projections. It never executes
arbitrary shell text and never resolves approvals, launches work, enqueues
tasks, dispatches runtimes, or mutates project files.
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
import tomllib
import urllib.error
import urllib.request
from typing import Any

from offdesk_llm_endpoint import default_ollama_base_url


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
AGENT_INTENT_SCHEMA = "telegram_agent_intent.v1"
MOBILE_CARD_MAX_LINES = 5
MOBILE_CARD_MAX_CHARS = 360
DEFAULT_AGENT_BASE_URLS = (
    default_ollama_base_url(),
    "http://127.0.0.1:11434",
    "http://localhost:11434",
)
DEFAULT_AGENT_MODEL_CANDIDATES = (
    "qwen3-coder-next:latest",
    "qwen3-coder:30b",
    "qwen2.5-coder:32b",
    "qwen2.5-coder:14b",
)
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
    parser.add_argument("--max-polls", type=int, help="Stop after this many polls; useful for smoke tests.")
    parser.add_argument("--poll-timeout-sec", type=int, default=5)
    parser.add_argument("--api-timeout-sec", type=int, default=20)
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


def safe_config_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def config_section(config: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    current: Any = config
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return safe_config_dict(current)


def load_agent_config_file(path: pathlib.Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RemoteOperatorTelegramError(f"agent config cannot be read: {path}: {error}") from error
    if not isinstance(raw, dict):
        return {}, []
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for section_path in (
        ("offdesk", "remote_operator", "agent"),
        ("remote_operator", "agent"),
        ("remote_operator", "telegram", "agent"),
    ):
        section = config_section(raw, section_path)
        if section:
            merged.update(section)
            sources.append(".".join(section_path))
    return merged, sources


def config_string(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def config_string_list(config: dict[str, Any], *keys: str) -> list[str]:
    values: list[Any] = []
    for key in keys:
        value = config.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, tuple):
            values.extend(list(value))
        elif isinstance(value, str):
            values.extend(csv_values(value) if "," in value else [value])
    return unique_nonempty(values)


def config_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def resolve_agent_config(args: argparse.Namespace) -> dict[str, Any]:
    file_config, config_sources = load_agent_config_file(args.agent_config_file)
    cli_mode = arg_was_provided("--agent-intent-mode")
    env_mode = "OFFDESK_REMOTE_OPERATOR_AGENT_INTENT_MODE" in os.environ
    mode = str(args.agent_intent_mode or "").strip().lower()
    if not cli_mode and not env_mode:
        mode = config_string(file_config, "intent_mode", "mode") or mode or "auto"
    if mode not in {"auto", "off", "required"}:
        raise RemoteOperatorTelegramError(f"unsupported agent intent mode: {mode}")

    provider = ""
    if arg_was_provided("--agent-provider") or "OFFDESK_REMOTE_OPERATOR_AGENT_PROVIDER" in os.environ:
        provider = str(args.agent_provider or "").strip()
    provider = provider or config_string(file_config, "provider") or "ollama"
    provider = provider.strip().lower()

    cli_base_urls = unique_nonempty(args.agent_base_url)
    env_base_urls = unique_nonempty(
        [
            os.environ.get("OFFDESK_REMOTE_OPERATOR_AGENT_BASE_URL"),
            os.environ.get("OFFDESK_LLM_BASE_URL"),
            os.environ.get("OLLAMA_BASE_URL"),
        ]
    )
    config_base_urls = config_string_list(file_config, "base_urls", "base_url")
    base_urls = unique_nonempty(
        cli_base_urls + env_base_urls + config_base_urls + list(DEFAULT_AGENT_BASE_URLS)
    )

    model_values: list[Any] = []
    model_values.extend(args.agent_model)
    if args.agent_model_candidates:
        model_values.extend(csv_values(args.agent_model_candidates))
    model_values.extend(
        [
            os.environ.get("OFFDESK_OLLAMA_MODEL"),
            os.environ.get("OFFDESK_LLM_MODEL"),
        ]
    )
    model_values.extend(config_string_list(file_config, "models", "model"))
    model_values.extend(DEFAULT_AGENT_MODEL_CANDIDATES)
    models = unique_nonempty(model_values)

    timeout_sec = args.agent_timeout_sec
    if not arg_was_provided("--agent-timeout-sec") and "OFFDESK_REMOTE_OPERATOR_AGENT_TIMEOUT_SEC" not in os.environ:
        timeout_sec = config_int(file_config, "timeout_sec", timeout_sec)
    num_ctx = args.agent_num_ctx
    if not arg_was_provided("--agent-num-ctx") and "OFFDESK_REMOTE_OPERATOR_AGENT_NUM_CTX" not in os.environ:
        num_ctx = config_int(file_config, "num_ctx", num_ctx)
    num_predict = args.agent_num_predict
    if not arg_was_provided("--agent-num-predict") and "OFFDESK_REMOTE_OPERATOR_AGENT_NUM_PREDICT" not in os.environ:
        num_predict = config_int(file_config, "num_predict", num_predict)

    return {
        "mode": mode,
        "provider": provider,
        "base_urls": base_urls,
        "models": models,
        "timeout_sec": max(1, int(timeout_sec)),
        "num_ctx": max(512, int(num_ctx)),
        "num_predict": max(64, int(num_predict)),
        "config_file": str(args.agent_config_file),
        "config_sources": config_sources,
    }


def parse_json_object_response(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("agent response was not a JSON object")
    return parsed


def ollama_available_models(base_url: str, timeout_sec: int) -> list[str]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/tags",
        headers={"Content-Type": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=max(1, int(timeout_sec))) as response:
        raw = json.loads(response.read().decode("utf-8"))
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        return []
    return unique_nonempty(
        [item.get("name") for item in models if isinstance(item, dict)]
    )


def select_agent_runtime(agent_config: dict[str, Any]) -> dict[str, Any] | None:
    if agent_config.get("mode") == "off":
        return None
    provider = str(agent_config.get("provider") or "").strip().lower()
    if provider != "ollama":
        if agent_config.get("mode") == "required":
            raise RemoteOperatorTelegramError(f"unsupported agent provider: {provider}")
        return None
    timeout_sec = int(agent_config.get("timeout_sec") or 20)
    candidates = unique_nonempty(list(agent_config.get("models") or []))
    errors: list[str] = []
    for base_url in unique_nonempty(list(agent_config.get("base_urls") or [])):
        try:
            available = ollama_available_models(base_url, min(timeout_sec, 10))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
            errors.append(f"{base_url}:{type(error).__name__}")
            continue
        if not available:
            continue
        model = next((item for item in candidates if item in available), "")
        if not model:
            model = next(
                (
                    item
                    for item in available
                    if "qwen" in item.lower() and "coder" in item.lower()
                ),
                "",
            )
        if not model:
            model = available[0]
        return {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "available_models": available,
            "timeout_sec": timeout_sec,
            "num_ctx": int(agent_config.get("num_ctx") or 8192),
            "num_predict": int(agent_config.get("num_predict") or 768),
            "config_sources": list(agent_config.get("config_sources") or []),
        }
    if agent_config.get("mode") == "required":
        detail = ", ".join(errors[:3]) if errors else "no available Ollama model"
        raise RemoteOperatorTelegramError(f"local agent runtime unavailable: {detail}")
    return None


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
    payload: dict[str, Any] = {
        "model": runtime["model"],
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "num_ctx": int(runtime.get("num_ctx") or 8192),
            "num_predict": int(runtime.get("num_predict") or 768),
        },
    }
    request = urllib.request.Request(
        str(runtime["base_url"]).rstrip("/") + "/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(runtime.get("timeout_sec") or 20)) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return parse_json_object_response(str(raw.get("response") or ""))


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


def render_projection_message(projection: dict[str, Any], *, max_chars: int) -> str:
    command = str(projection.get("command") or "").strip()
    if command == "status":
        message = render_status_message(projection)
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


def render_status_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    lines = [
        title_with_profile("Forager 점검", profile),
        status_headline(payload),
    ]
    summary = status_summary(payload, primary_status_kind(payload))
    if summary:
        lines.append(summary)
    lines.append(status_next_action(payload))
    return "\n".join(lines)


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
) -> str:
    is_planning_request = feedback_kind == "planning_request"
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
    message_preview = render_projection_message(
        projection,
        max_chars=max(200, int(args.max_message_chars)),
    )
    interaction_context = interaction_context_from_projection(projection)
    attach_choice_surface(result, interaction_context)
    result.update(
        {
            "status": "rendered",
            "projection_schema": projection.get("schema"),
            "projection": projection,
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
        feedback_context = last_context_for_chat_hash(state, sha256_short(chat_id_for(message)))
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
                )
                rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
        message_id = send_message(
            config,
            chat_id_for(message),
            rendered["message_preview"],
            args,
            reply_markup=rendered.get("reply_markup_preview")
            if isinstance(rendered.get("reply_markup_preview"), dict)
            else None,
        )
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
    if result.get("status") != "no_update":
        summary["handled_result_count"] = int(summary.get("handled_result_count") or 0) + 1
        summary["last_handled_result"] = result


def loop_status_path(args: argparse.Namespace) -> pathlib.Path | None:
    if args.out:
        return args.out
    return args.loop_status_file


def run_loop(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    summary = loop_summary_base(args, config)
    max_polls = args.max_polls
    status_path = loop_status_path(args)
    try:
        while max_polls is None or int(summary["poll_count"]) < max_polls:
            result = run_once(args, config)
            update_loop_summary(summary, result)
            if status_path:
                write_json(status_path, summary)
            if max_polls is None and result.get("status") != "no_update":
                print(json.dumps(result, ensure_ascii=False), flush=True)
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
    token_configured = bool(config.get("token"))
    if not token_configured:
        issues.append("telegram_bot_token_missing")
    if not config.get("chat_allowlist_configured"):
        issues.append("telegram_chat_allowlist_missing")
    loop_status: dict[str, Any] = {}
    if status_path.exists():
        try:
            loaded = load_json(status_path)
            loop_status = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            issues.append("loop_status_unreadable")
    else:
        issues.append("loop_status_missing")
    last_result = loop_status.get("last_result") if isinstance(loop_status.get("last_result"), dict) else {}
    last_poll_at = parse_timestamp(last_result.get("generated_at") or loop_status.get("generated_at"))
    last_poll_age_sec = None
    if last_poll_at:
        last_poll_age_sec = max(
            0,
            int((dt.datetime.now(dt.timezone.utc) - last_poll_at).total_seconds()),
        )
        if last_poll_age_sec > max(1, int(args.health_max_age_sec)):
            issues.append("last_poll_stale")
    elif loop_status:
        issues.append("last_poll_missing")
    if str(loop_status.get("status") or "") not in {"polling", "max_polls_reached"} and loop_status:
        issues.append("listener_not_polling")
    health_status = "healthy" if not issues else "unhealthy"
    return {
        "schema": HEALTH_SCHEMA,
        "generated_at": utc_now(),
        "profile": args.profile,
        "health_status": health_status,
        "issues": issues,
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
