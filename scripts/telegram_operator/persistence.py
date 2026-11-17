"""State persistence helpers for the Telegram remote operator."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from .common import RemoteOperatorTelegramError, load_json, utc_now, write_json


STATE_SCHEMA = "remote_operator_telegram_state.v1"
CHAT_HISTORY_MAX_ENTRIES = 12
CHAT_HISTORY_ENTRY_MAX_CHARS = 400
CONTROL_SCOPED_STATE_KEYS = (
    "notification_unconfirmed_events",
    "session_notification_delivery_evidence",
    "attention_notified_by_key",
    "chat_focus_by_chat",
    "chat_history_by_chat",
    "last_interaction_context_by_chat",
    "orchestrator_bootstrap_digest",
    "pending_dispatch_confirmations_by_chat",
    "remote_plan_sessions_by_chat",
    "session_focus_by_chat",
    "session_input_no_effect_by_key",
    "telegram_session_keyboard_schema",
    "waiting_ids_last_scan",
    "waiting_notified_by_session",
)


class RemoteOperatorStateError(RemoteOperatorTelegramError):
    """Raised when listener state cannot be trusted enough to continue."""


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": STATE_SCHEMA, "offset": 0}
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        raise RemoteOperatorStateError(
            f"listener state is unreadable and was not reset: {path} ({type(error).__name__})"
        ) from error
    if not isinstance(state, dict):
        raise RemoteOperatorStateError(
            f"listener state must be a JSON object and was not reset: {path}"
        )
    schema = state.get("schema")
    if schema is None:
        state["schema"] = STATE_SCHEMA
    elif schema != STATE_SCHEMA:
        raise RemoteOperatorStateError(
            f"unsupported listener state schema {schema!r}: {path}"
        )
    offset = state.get("offset", 0)
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise RemoteOperatorStateError(
            f"listener state offset must be a non-negative integer: {path}"
        )
    state["offset"] = offset
    return state


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(path, state)


def bind_control_generation(
    state: dict[str, Any], generation: str
) -> dict[str, Any]:
    """Bind ephemeral controller state to one local credential generation."""

    current = str(generation or "").strip()
    if not current:
        raise RemoteOperatorStateError("control generation must not be empty")
    previous = str(state.get("control_generation_sha256") or "").strip()
    if previous == current:
        return {
            "status": "unchanged",
            "changed": False,
            "previous_generation": previous,
            "current_generation": current,
            "cleared_state": {},
        }

    cleared: dict[str, int] = {}
    for key in CONTROL_SCOPED_STATE_KEYS:
        value = state.pop(key, None)
        if isinstance(value, dict):
            cleared[key] = len(value)
        elif isinstance(value, list):
            cleared[key] = len(value)
        elif value is not None:
            cleared[key] = 1
    previous_offset = int(state.get("offset") or 0)
    state["offset"] = 0
    state["control_generation_sha256"] = current
    state["control_generation_bound_at"] = utc_now()
    state["control_generation_sequence"] = int(
        state.get("control_generation_sequence") or 0
    ) + 1
    return {
        "status": "rotated" if previous else "initialized",
        "changed": True,
        "previous_generation": previous or None,
        "current_generation": current,
        "previous_offset": previous_offset,
        "cleared_state": cleared,
    }


def parse_utc_timestamp(value: Any) -> dt.datetime | None:
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


def last_context_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    max_age_sec: int | None = None,
) -> dict[str, Any] | None:
    contexts = state.get("last_interaction_context_by_chat")
    if not isinstance(contexts, dict):
        return None
    context = contexts.get(str(chat_hash or ""))
    if not isinstance(context, dict):
        return None
    if max_age_sec is None or max_age_sec < 0:
        return context
    remembered_at = parse_utc_timestamp(context.get("remembered_at"))
    if remembered_at is None:
        return None
    age_sec = int((dt.datetime.now(dt.timezone.utc) - remembered_at).total_seconds())
    if age_sec > max_age_sec:
        return None
    return context


def chat_history_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    max_age_sec: int | None = None,
) -> list[dict[str, Any]]:
    histories = state.get("chat_history_by_chat")
    if not isinstance(histories, dict):
        return []
    entries = histories.get(str(chat_hash or ""))
    if not isinstance(entries, list):
        return []
    now = dt.datetime.now(dt.timezone.utc)
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if max_age_sec is not None and max_age_sec >= 0:
            at = parse_utc_timestamp(entry.get("at"))
            if at is None or (now - at).total_seconds() > max_age_sec:
                continue
        result.append(entry)
    return result[-CHAT_HISTORY_MAX_ENTRIES:]


def append_chat_history(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    role: str,
    text: Any,
) -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return
    histories = state.setdefault("chat_history_by_chat", {})
    if not isinstance(histories, dict):
        histories = {}
        state["chat_history_by_chat"] = histories
    key = str(chat_hash or "")
    entries = histories.get(key)
    if not isinstance(entries, list):
        entries = []
    entries.append(
        {
            "at": utc_now(),
            "role": str(role),
            "text": cleaned[:CHAT_HISTORY_ENTRY_MAX_CHARS],
        }
    )
    histories[key] = entries[-CHAT_HISTORY_MAX_ENTRIES:]


def project_focus_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    max_age_sec: int,
) -> dict[str, Any] | None:
    focuses = state.get("chat_focus_by_chat")
    if not isinstance(focuses, dict):
        return None
    focus = focuses.get(str(chat_hash or ""))
    if not isinstance(focus, dict):
        return None
    if max_age_sec >= 0:
        updated_at = parse_utc_timestamp(focus.get("updated_at"))
        now = dt.datetime.now(dt.timezone.utc)
        if updated_at is None or (now - updated_at).total_seconds() > max_age_sec:
            return None
    return focus


def session_focus_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    max_age_sec: int,
) -> dict[str, Any] | None:
    focuses = state.get("session_focus_by_chat")
    if not isinstance(focuses, dict):
        return None
    focus = focuses.get(str(chat_hash or ""))
    if not isinstance(focus, dict):
        return None
    if max_age_sec >= 0:
        updated_at = parse_utc_timestamp(focus.get("updated_at"))
        now = dt.datetime.now(dt.timezone.utc)
        if updated_at is None or (now - updated_at).total_seconds() > max_age_sec:
            return None
    return focus


def remember_session_focus(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    session_id: str,
    project: str,
    title: str,
    tool: str,
    prompt_hash: str = "",
    prompt_observation_hash: str = "",
    source: str,
    remote_session_identity: dict[str, Any] | None = None,
) -> None:
    if not str(session_id or "").strip():
        return
    focuses = state.setdefault("session_focus_by_chat", {})
    if not isinstance(focuses, dict):
        focuses = {}
        state["session_focus_by_chat"] = focuses
    focus = {
        "session_id": str(session_id),
        "project": str(project),
        "title": str(title),
        "tool": str(tool),
        "prompt_hash": str(prompt_hash),
        "prompt_identity": str(prompt_hash),
        "prompt_observation_hash": str(prompt_observation_hash),
        "source": str(source),
        "updated_at": utc_now(),
    }
    if isinstance(remote_session_identity, dict):
        focus["remote_session_identity"] = remote_session_identity
    focuses[str(chat_hash or "")] = focus


def clear_session_focus_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    *,
    session_id: str | None = None,
    sources: set[str] | None = None,
) -> bool:
    focuses = state.get("session_focus_by_chat")
    if not isinstance(focuses, dict):
        return False
    key = str(chat_hash or "")
    focus = focuses.get(key)
    if not isinstance(focus, dict):
        return False
    if session_id is not None and str(focus.get("session_id") or "") != str(session_id):
        return False
    if sources is not None and str(focus.get("source") or "") not in sources:
        return False
    focuses.pop(key, None)
    return True


def remember_context_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    rendered: dict[str, Any],
) -> None:
    context = rendered.get("interaction_context")
    parsed = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
    key = str(chat_hash or "")
    if parsed.get("command") == "chat":
        contexts = state.get("last_interaction_context_by_chat")
        # A model clarification is a real short-lived thread. Ordinary chat
        # must not refresh an older status card, and answering a clarification
        # consumes that thread instead of leaking it into future topics.
        if isinstance(context, dict) and context.get("context_kind") == "chat_clarification":
            if not isinstance(contexts, dict):
                contexts = {}
                state["last_interaction_context_by_chat"] = contexts
            remembered = dict(context)
            remembered["remembered_at"] = utc_now()
            if isinstance(rendered.get("sent_message_id"), int):
                remembered["source_message_id"] = rendered["sent_message_id"]
            contexts[key] = remembered
        elif isinstance(contexts, dict) and isinstance(contexts.get(key), dict) and contexts[key].get(
            "context_kind"
        ) == "chat_clarification":
            contexts.pop(key, None)
        return
    # Feedback and remember results carry the previous card's context;
    # re-storing it would refresh remembered_at and defeat context expiry.
    if not isinstance(context, dict) or parsed.get("command") in {"feedback", "remember"}:
        return
    contexts = state.setdefault("last_interaction_context_by_chat", {})
    if not isinstance(contexts, dict):
        contexts = {}
        state["last_interaction_context_by_chat"] = contexts
    remembered = dict(context)
    remembered["remembered_at"] = utc_now()
    if isinstance(rendered.get("sent_message_id"), int):
        remembered["source_message_id"] = rendered["sent_message_id"]
    contexts[key] = remembered
