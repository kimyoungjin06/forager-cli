"""Transport-bound validation envelopes for inbound Telegram commands."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any


COMMAND_ENVELOPE_SCHEMA = "remote_command_envelope.v1"
DEFAULT_COMMAND_TTL_SEC = 300
DEFAULT_FUTURE_SKEW_SEC = 30

READ_ONLY_COMMANDS = {
    "help",
    "guide",
    "status",
    "pending",
    "plans",
    "show",
    "decisions",
    "recovery",
    "runtime",
    "run_list",
    "tasks",
    "attention",
    "receipts",
}
RECORD_COMMANDS = {"feedback", "remember", "plan_request"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso_from_epoch(value: int | float) -> str:
    return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc).isoformat()


def command_envelope_nonce(
    *,
    update_id: int,
    message_id: int,
    input_hash: str,
    chat_hash: str,
    user_hash: str,
) -> str:
    """Derive a unique nonce from Telegram's server IDs and bound input."""

    material = json.dumps(
        {
            "transport": "telegram",
            "update_id": update_id,
            "message_id": message_id,
            "input_hash": input_hash,
            "chat_id_hash": chat_hash,
            "user_id_hash": user_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"tg-{_sha256(material)[:32]}"


def command_envelope_id(envelope: dict[str, Any]) -> str:
    material = json.dumps(
        {
            "schema": COMMAND_ENVELOPE_SCHEMA,
            "transport": "telegram",
            "update_id": envelope.get("update_id"),
            "message_id": envelope.get("message_id"),
            "input_hash": envelope.get("input_hash"),
            "chat_id_hash": envelope.get("chat_id_hash"),
            "user_id_hash": envelope.get("user_id_hash"),
            "command": envelope.get("command"),
            "command_class": envelope.get("command_class"),
            "nonce": envelope.get("nonce"),
            "issued_at": envelope.get("issued_at"),
            "ttl_sec": envelope.get("ttl_sec"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"rce-{_sha256(material)[:24]}"


def classify_command(
    parsed_command: dict[str, Any], *, plan_session_input: bool
) -> tuple[str, bool]:
    """Return the audit class and whether freshness is required."""

    if plan_session_input:
        return "plan_session", True
    if not parsed_command.get("supported"):
        return "unsupported", False
    command = str(parsed_command.get("command") or "unknown")
    if command in READ_ONLY_COMMANDS:
        return "read_only", False
    if command in {"wiki", "projects"}:
        selector = str(parsed_command.get("project_selector") or "").strip()
        return ("context_selection", True) if selector else ("read_only", False)
    if command in RECORD_COMMANDS:
        return "record", True
    if command == "chat":
        # Plain text can be routed to a live local agent or create a plan
        # confirmation after model classification. Validate it before running
        # that classifier so a stale update can never reach either mutation.
        return "delegation_capable_chat", True
    if command in {
        "session_send",
        "session_approve",
        "session_deny",
        "session_continue",
    }:
        return "agent_session_control", True
    return "guarded_control", True


def build_command_envelope(
    *,
    update_id: int,
    message_id: int | None,
    message_date: int | None,
    input_hash: str,
    chat_hash: str,
    user_hash: str,
    parsed_command: dict[str, Any],
    plan_session_input: bool,
    ttl_sec: int = DEFAULT_COMMAND_TTL_SEC,
    received_epoch: float | None = None,
    future_skew_sec: int = DEFAULT_FUTURE_SKEW_SEC,
) -> dict[str, Any]:
    """Build and validate one inbound command before any handler runs."""

    received_epoch = (
        dt.datetime.now(dt.timezone.utc).timestamp()
        if received_epoch is None
        else float(received_epoch)
    )
    command_class, requires_freshness = classify_command(
        parsed_command, plan_session_input=plan_session_input
    )
    command = (
        "remote_plan_session_input"
        if plan_session_input
        else str(parsed_command.get("command") or "unsupported")
    )
    integrity_issues: list[str] = []
    freshness_issues: list[str] = []
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        integrity_issues.append("invalid_update_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        integrity_issues.append("invalid_message_id")
    if not str(input_hash or "").strip():
        integrity_issues.append("missing_input_hash")
    if not str(chat_hash or "").strip():
        integrity_issues.append("missing_chat_identity")
    if not str(user_hash or "").strip():
        integrity_issues.append("missing_user_identity")

    issued_at = None
    expires_at = None
    age_sec = None
    fresh = False
    valid_message_date = (
        not isinstance(message_date, bool) and isinstance(message_date, int)
    )
    if not valid_message_date:
        freshness_issues.append("message_timestamp_missing")
    elif ttl_sec <= 0:
        freshness_issues.append("invalid_command_ttl")
    else:
        issued_at = _iso_from_epoch(message_date)
        expires_at = _iso_from_epoch(message_date + ttl_sec)
        age_sec = round(received_epoch - message_date, 3)
        if age_sec < -max(0, future_skew_sec):
            freshness_issues.append("message_timestamp_in_future")
        elif age_sec > ttl_sec:
            freshness_issues.append("command_expired")
        else:
            fresh = True

    validation_issues = list(integrity_issues)
    if requires_freshness:
        validation_issues.extend(freshness_issues)
    valid = not validation_issues
    nonce = None
    if not integrity_issues:
        nonce = command_envelope_nonce(
            update_id=update_id,
            message_id=message_id,
            input_hash=input_hash,
            chat_hash=chat_hash,
            user_hash=user_hash,
        )
    envelope = {
        "schema": COMMAND_ENVELOPE_SCHEMA,
        "transport": "telegram",
        "update_id": update_id,
        "message_id": message_id,
        "input_hash": input_hash,
        "chat_id_hash": chat_hash,
        "user_id_hash": user_hash,
        "command": command,
        "command_class": command_class,
        "state_changing": requires_freshness,
        "requires_freshness": requires_freshness,
        "nonce": nonce,
        "nonce_valid": nonce is not None,
        "issued_at": issued_at,
        "received_at": _iso_from_epoch(received_epoch),
        "expires_at": expires_at,
        "ttl_sec": ttl_sec,
        "age_sec": age_sec,
        "fresh": fresh,
        "freshness_issues": freshness_issues,
        "valid": valid,
        "validation_status": "accepted" if valid else "refused",
        "validation_issues": validation_issues,
    }
    envelope["envelope_id"] = command_envelope_id(envelope)
    return envelope


def command_envelope_binding_issue(
    envelope: dict[str, Any],
    *,
    update_id: int,
    input_hash: str,
    chat_hash: str,
    user_hash: str,
) -> str | None:
    """Verify the persisted envelope still matches the current update."""

    if envelope.get("schema") != COMMAND_ENVELOPE_SCHEMA:
        return "unsupported_command_envelope_schema"
    expected = {
        "update_id": update_id,
        "input_hash": input_hash,
        "chat_id_hash": chat_hash,
        "user_id_hash": user_hash,
    }
    for key, value in expected.items():
        if envelope.get(key) != value:
            return f"command_envelope_{key}_mismatch"
    message_id = envelope.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        return "command_envelope_invalid_message_id"
    nonce = command_envelope_nonce(
        update_id=update_id,
        message_id=message_id,
        input_hash=input_hash,
        chat_hash=chat_hash,
        user_hash=user_hash,
    )
    if envelope.get("nonce") != nonce or not envelope.get("nonce_valid"):
        return "command_envelope_nonce_mismatch"
    if envelope.get("envelope_id") != command_envelope_id(envelope):
        return "command_envelope_id_mismatch"
    validation_issues = envelope.get("validation_issues")
    if not isinstance(validation_issues, list):
        return "command_envelope_validation_issues_invalid"
    valid = not validation_issues
    if envelope.get("valid") is not valid:
        return "command_envelope_validation_status_mismatch"
    expected_status = "accepted" if valid else "refused"
    if envelope.get("validation_status") != expected_status:
        return "command_envelope_validation_status_mismatch"
    if envelope.get("state_changing") != envelope.get("requires_freshness"):
        return "command_envelope_freshness_contract_mismatch"
    if envelope.get("requires_freshness") and not envelope.get("fresh") and valid:
        return "command_envelope_freshness_contract_mismatch"
    return None
