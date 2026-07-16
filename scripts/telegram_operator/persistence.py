"""State persistence helpers for the Telegram remote operator."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

from .common import load_json, utc_now, write_json


STATE_SCHEMA = "remote_operator_telegram_state.v1"


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": STATE_SCHEMA, "offset": 0}
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {"schema": STATE_SCHEMA, "offset": 0}
    if not isinstance(state, dict):
        return {"schema": STATE_SCHEMA, "offset": 0}
    state.setdefault("schema", STATE_SCHEMA)
    state.setdefault("offset", 0)
    return state


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(path, state)


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


def remember_context_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    rendered: dict[str, Any],
) -> None:
    context = rendered.get("interaction_context")
    parsed = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
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
    contexts[str(chat_hash or "")] = remembered
