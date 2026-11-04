"""Select one proactive notification without consuming deferred work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable

from .common import RemoteOperatorTelegramError
from .config import TelegramControlRevokedError
from .rendering import sanitize_text
from .transport import TelegramDeliveryUnconfirmed

MAX_UNCONFIRMED_EVENTS = 256


class NotificationPriority(IntEnum):
    # Removing a legacy keyboard is a one-time prerequisite for inline cards.
    KEYBOARD_CLEANUP = 0
    INTERACTION = 10
    RECOVERY = 20
    DECISION = 30
    TASK_READY = 40
    STATUS = 50
    AUTONOMY = 60


@dataclass(frozen=True)
class NotificationCandidate:
    event_key: str
    source: str
    priority: NotificationPriority
    message: str
    reply_markup: dict[str, Any] | None
    report: dict[str, Any] = field(repr=False, compare=False)
    on_delivered: Callable[[int | None], None] = field(repr=False, compare=False)
    on_unconfirmed: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def sort_key(self) -> tuple[int, str, str, str]:
        payload = json.dumps(
            [self.message, self.reply_markup], sort_keys=True, ensure_ascii=False
        )
        return (
            int(self.priority), self.event_key, self.source,
            hashlib.sha256(payload.encode()).hexdigest(),
        )


class NotificationCycle:
    """Collect first, then attempt at most one send, including failed sends.

    Delivery callbacks own source bookkeeping. They run only for the selected
    successful delivery; deferred candidates remain discoverable next poll.
    The cycle itself is ephemeral and never persists message text or callbacks.
    """

    def __init__(
        self,
        send: Callable[[NotificationCandidate], int | None],
        dry_run: bool = False,
        unconfirmed_events: dict[str, Any] | None = None,
        persist: Callable[[], None] | None = None,
    ) -> None:
        self._send = send
        self._dry_run = dry_run
        self._persist = persist
        self._unconfirmed_events = unconfirmed_events if unconfirmed_events is not None else {}
        self._candidates: list[NotificationCandidate] = []
        self._result: dict[str, Any] | None = None

    def add(self, candidate: NotificationCandidate) -> None:
        if self._result is not None:
            raise RuntimeError("notification cycle is already closed")
        if not candidate.event_key or not candidate.source:
            raise ValueError("notification candidate requires an event and source")
        self._candidates.append(candidate)

    def _release_hold(self, digest: str) -> None:
        held = self._unconfirmed_events.pop(digest, None)
        try:
            if self._persist is not None:
                self._persist()
        except BaseException:
            if held is not None:
                self._unconfirmed_events[digest] = held
            raise

    def deliver(self) -> dict[str, Any]:
        if self._result is not None:
            return self._result
        unique: dict[str, NotificationCandidate] = {}
        for candidate in sorted(self._candidates, key=NotificationCandidate.sort_key):
            unique.setdefault(candidate.event_key, candidate)
        self._result = {
            "schema": "remote_operator_notification_cycle.v1",
            "status": "idle",
            "candidate_count": len(unique),
            "duplicate_count": len(self._candidates) - len(unique),
            "deferred_count": len(unique),
            "attempted_count": 0,
            "delivered_count": 0,
        }
        if not unique:
            return self._result
        eligible = {}
        for key, candidate in unique.items():
            digest = hashlib.sha256(key.encode()).hexdigest()
            if digest in self._unconfirmed_events or len(self._unconfirmed_events) >= MAX_UNCONFIRMED_EVENTS:
                candidate.report.update({"status": "delivery_unconfirmed_hold", "notified_count": 0})
            else:
                eligible[key] = candidate
        self._result["held_count"] = len(unique) - len(eligible)
        self._result["hold_capacity_reached"] = len(self._unconfirmed_events) >= MAX_UNCONFIRMED_EVENTS
        self._result["deferred_count"] = len(eligible)
        if not eligible:
            self._result["status"] = "delivery_unconfirmed_hold"
            return self._result
        selected = next(iter(eligible.values()))
        self._result.update({
            "status": "sending",
            "selected_source": selected.source,
            "selected_event_sha256": hashlib.sha256(selected.event_key.encode()).hexdigest(),
            "selected_priority": int(selected.priority),
            "deferred_count": len(eligible) - 1,
            "attempted_count": 0,
        })
        digest = self._result["selected_event_sha256"]
        if not self._dry_run:
            self._unconfirmed_events[digest] = {"status": "delivery_inflight"}
            try:
                if self._persist is not None:
                    self._persist()
            except BaseException:
                self._result["status"] = "checkpoint_failed"
                raise
        self._result["attempted_count"] = 1
        try:
            message_id = self._send(selected)
        except TelegramControlRevokedError:
            self._result["status"] = "control_revoked"
            raise
        except TelegramDeliveryUnconfirmed:
            message_id = None
        except (RemoteOperatorTelegramError, OSError) as error:
            if not self._dry_run:
                self._release_hold(digest)
            selected.report.update({
                "status": "send_failed",
                "send_status": "failed",
                "notified_count": 0,
                "attempted_count": 1,
                "error": sanitize_text(str(error), max_chars=240),
            })
            self._result["status"] = "send_failed"
            return self._result
        if not self._dry_run and (type(message_id) is not int or message_id <= 0):
            self._unconfirmed_events[hashlib.sha256(selected.event_key.encode()).hexdigest()] = {
                "status": "delivery_unconfirmed",
            }
            self._result.update({"status": "delivery_unconfirmed", "send_status": "unconfirmed"})
            selected.report.update({
                "status": "delivery_unconfirmed", "send_status": "unconfirmed",
                "notified_count": 0, "attempted_count": 1,
            })
            if selected.on_unconfirmed is not None:
                selected.on_unconfirmed()
            if self._persist is not None:
                self._persist()
            return self._result
        # Mark delivery before committing local bookkeeping. A callback error
        # must never make another call on this cycle send a second card.
        self._result.update({
            "status": "delivered",
            "send_status": "dry_run" if self._dry_run else "sent",
            "delivered_count": 1,
        })
        try:
            selected.on_delivered(message_id)
            if not self._dry_run:
                self._release_hold(digest)
        except Exception:
            self._result["status"] = "delivery_commit_failed"
            raise
        return self._result
