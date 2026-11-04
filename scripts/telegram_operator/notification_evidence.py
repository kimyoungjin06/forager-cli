"""Bounded local evidence, without terminal text, labels, or chat identifiers."""

from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any

from .common import utc_now

EVIDENCE_KEY = "session_notification_delivery_evidence"
MAX_RECORDS = 256
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def phrase_flags(text: str) -> dict[str, bool]:
    normalized = re.sub(r"\s+", " ", html.unescape(text)).casefold()
    return {
        "dismiss_and_keep_waiting": "dismiss and keep waiting" in normalized,
        "no_action_required": "no action is required" in normalized,
    }


def payload_evidence(message: str, markup: dict[str, Any] | None) -> dict[str, Any]:
    labels = "\n".join(
        str(button.get("text") or "")
        for row in (markup or {}).get("inline_keyboard", [])
        for button in row
    )
    payload = json.dumps([message, markup], sort_keys=True, ensure_ascii=False)
    return {
        "schema": "session_notification_payload_evidence.v1",
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "body_flags": phrase_flags(message),
        "button_flags": phrase_flags(labels),
    }


def record_delivery(
    state: dict[str, Any], evidence: dict[str, Any], *,
    message_id: int | None, binding_sha256: str, now: float, dry_run: bool,
) -> None:
    # An absent API acknowledgement is not proof of delivery. Test previews
    # also must not inflate the historical sent-message count.
    if dry_run or type(message_id) is not int or message_id <= 0:
        return
    records = state.get(EVIDENCE_KEY)
    retained = [
        row for row in records if isinstance(row, dict)
        and type(row.get("at")) in (int, float)
        and now - MAX_AGE_SECONDS <= row["at"] <= now
        and row.get("message_id") != message_id
    ] if isinstance(records, list) else []
    retained.append({
        **evidence, "message_id": message_id, "at": now, "delivered_at": utc_now(),
        "binding_sha256": binding_sha256,
    })
    state[EVIDENCE_KEY] = retained[-MAX_RECORDS:]
