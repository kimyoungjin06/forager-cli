"""Pure Telegram command routing helpers."""

from __future__ import annotations

import shlex
from typing import Any


BUTTON_COMMAND_ALIASES = {
    "상태": "/status",
    "승인 대기": "/pending",
    "전체 승인": "/pending --all",
    "계획": "/plans --latest",
    "도움말": "/help",
}
CORE_BUTTON_LABELS = ("상태", "승인 대기", "계획", "도움말")
CORE_OR_SLASH_COMMANDS = {
    "start",
    "help",
    "status",
    "pending",
    "plans",
    "show",
    "chat",
    "ask",
    "feedback",
    "note",
    "remember",
    "plan",
    "plan_request",
    "decisions",
    "decision",
    "confirm",
    "cancel",
}
SESSION_INPUT_COMMANDS = {"select", "choose", "path", "workload", "session_input", "plan_input"}
DISPATCH_BUTTON_ALIASES = {
    "결정 목록": "/decisions",
    "취소": "/cancel",
}


def normalize_command_name(raw: str) -> str:
    text = raw.strip()
    if text.startswith("/"):
        text = text[1:]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip().lower().replace("-", "_")


def unsupported_command(command_text: str, reason: str) -> dict[str, Any]:
    return {
        "supported": False,
        "command": None,
        "argv": [],
        "reason": reason,
        "command_text": command_text,
    }


def parse_remote_command(command_text: str) -> dict[str, Any]:
    text = str(command_text or "").strip()
    if not text:
        return unsupported_command(text, "empty_command")
    original_text = text
    alias = BUTTON_COMMAND_ALIASES.get(text) or DISPATCH_BUTTON_ALIASES.get(text)
    if alias:
        text = alias
    if not text.startswith("/"):
        return {
            "supported": True,
            "command": "chat",
            "argv": [],
            "reason": "plain_text_chat",
            "command_text": original_text,
            "chat_text": original_text,
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
    if command in {"chat", "ask"}:
        chat_text = " ".join(args).strip()
        if not chat_text:
            return unsupported_command(original_text, "chat_requires_text")
        return {
            "supported": True,
            "command": "chat",
            "argv": [],
            "reason": "explicit_chat_command",
            "command_text": original_text,
            "chat_text": chat_text,
        }
    if command in {"feedback", "note"}:
        feedback_text = " ".join(args).strip()
        if not feedback_text:
            return unsupported_command(original_text, f"{command}_requires_text")
        return {
            "supported": True,
            "command": "feedback",
            "argv": [],
            "reason": f"explicit_{command}_command",
            "command_text": original_text,
            "feedback_text": feedback_text,
            "feedback_kind": "freeform_feedback",
        }
    if command == "remember":
        remember_text = " ".join(args).strip()
        if not remember_text:
            return unsupported_command(original_text, "remember_requires_text")
        return {
            "supported": True,
            "command": "remember",
            "argv": [],
            "reason": "explicit_remember_command",
            "command_text": original_text,
            "remember_text": remember_text,
        }
    if command in {"plan", "plan_request"}:
        plan_text = " ".join(args).strip()
        if not plan_text:
            return unsupported_command(original_text, "plan_requires_text")
        return {
            "supported": True,
            "command": "plan_request",
            "argv": [],
            "reason": "explicit_plan_command",
            "command_text": original_text,
            "feedback_text": plan_text,
            "plan_text": plan_text,
            "feedback_kind": "planning_request",
        }
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
    if command == "decisions":
        if args:
            return unsupported_command(original_text, "decisions_accepts_no_arguments")
        return {
            "supported": True,
            "command": "decisions",
            "argv": [],
            "reason": "explicit_decisions_command",
            "command_text": original_text,
        }
    if command == "decision":
        return parse_decision_command(original_text, args)
    if command == "confirm":
        token = args[0].strip() if args else ""
        if not token:
            return unsupported_command(original_text, "confirm_requires_token")
        return {
            "supported": True,
            "command": "confirm",
            "argv": [],
            "reason": "explicit_confirm_command",
            "command_text": original_text,
            "confirm_token": token,
        }
    if command == "cancel":
        return {
            "supported": True,
            "command": "cancel",
            "argv": [],
            "reason": "explicit_cancel_command",
            "command_text": original_text,
        }
    return unsupported_command(original_text, "unsupported_remote_operator_command")


def parse_decision_command(command_text: str, args: list[str]) -> dict[str, Any]:
    if len(args) < 2:
        return unsupported_command(command_text, "decision_requires_id_and_action")
    decision_id = args[0].strip()
    action_kind = args[1].strip().lower()
    note = " ".join(args[2:]).strip()
    if not decision_id or not action_kind:
        return unsupported_command(command_text, "decision_requires_id_and_action")
    return {
        "supported": True,
        "command": "decision",
        "argv": [],
        "reason": "explicit_decision_command",
        "command_text": command_text,
        "decision_id": decision_id,
        "decision_action_kind": action_kind,
        "decision_note": note,
    }


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
    return normalize_command_name(first) in CORE_OR_SLASH_COMMANDS


def remote_plan_session_command_payload(text: str) -> str | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("/"):
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if not tokens:
        return None
    command = normalize_command_name(tokens[0])
    if command not in SESSION_INPUT_COMMANDS:
        return None
    payload = " ".join(tokens[1:]).strip()
    return payload or None
