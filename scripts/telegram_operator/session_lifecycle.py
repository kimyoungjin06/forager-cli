"""Record observed local session stops without guessing why a process exited."""

from __future__ import annotations

import datetime as dt
from typing import Any


LIVE_STATUSES = frozenset({"running", "waiting", "idle", "starting"})
MAX_EVENTS = 100


def observe_session_lifecycle(
    state: dict[str, Any], sessions: list[dict[str, Any]], *, now: float
) -> list[dict[str, Any]]:
    """Return confirmed, undelivered stops after two consecutive observations.

    The first scan seeds a baseline, so historical stopped rows do not page the
    operator. A stop records only the status transition; its cause is unknown.
    """

    observations = state.setdefault("session_lifecycle_by_id", {})
    if not isinstance(observations, dict):
        observations = {}
        state["session_lifecycle_by_id"] = observations
    events = state.setdefault("session_lifecycle_events", [])
    if not isinstance(events, list):
        events = []
        state["session_lifecycle_events"] = events
    observed_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()
    seen: set[str] = set()
    pending: list[dict[str, Any]] = []
    for session in sessions:
        session_id = str(session.get("id") or "")
        status = str(session.get("status") or "")
        if not session_id or status not in LIVE_STATUSES | {"stopped", "error"}:
            continue
        seen.add(session_id)
        entry = observations.get(session_id)
        if not isinstance(entry, dict):
            observations[session_id] = {
                "status": status,
                "last_observed_at": observed_at,
                "last_live_status": status if status in LIVE_STATUSES else None,
                "last_live_at": observed_at if status in LIVE_STATUSES else None,
            }
            continue
        previous_status = str(entry.get("status") or "")
        if status in LIVE_STATUSES:
            if previous_status == "stopped" and entry.get("stop_event"):
                stop_event = entry["stop_event"]
                if stop_event.get("alert_status") == "pending":
                    mark_stop_alert(
                        state, stop_event, status="recovered_before_alert", now=now
                    )
                events.append({
                    "event": "live_after_stop",
                    "session_id": session_id,
                    "observed_at": observed_at,
                    "stop_observed_at": stop_event["observed_at"],
                })
            entry.update({
                "status": status,
                "last_observed_at": observed_at,
                "last_live_status": status,
                "last_live_at": observed_at,
            })
            entry.pop("stopped_scans", None)
            entry.pop("stop_event", None)
            continue
        if status == "stopped" and entry.get("last_live_status"):
            if previous_status != "stopped":
                entry["stopped_scans"] = 1
                entry["stopped_at"] = observed_at
            else:
                entry["stopped_scans"] = min(2, int(entry.get("stopped_scans") or 0) + 1)
            if entry["stopped_scans"] >= 2 and not entry.get("stop_event"):
                event = {
                    "event": "session_stopped",
                    "session_id": session_id,
                    "title": str(session.get("title") or session_id),
                    "project": str(session.get("project") or ""),
                    "tool": str(session.get("tool") or ""),
                    "previous_live_status": entry["last_live_status"],
                    "last_live_at": entry.get("last_live_at"),
                    "observed_at": entry["stopped_at"],
                    "confirmed_at": observed_at,
                    "cause": "unknown",
                    "alert_status": "pending",
                }
                entry["stop_event"] = event
                events.append(event.copy())
            event = entry.get("stop_event")
            if isinstance(event, dict) and event.get("alert_status") == "pending":
                pending.append(event)
        entry["status"] = status
        entry["last_observed_at"] = observed_at
    for session_id in list(observations):
        if session_id not in seen:
            previous = observations[session_id]
            stop_event = previous.get("stop_event") if isinstance(previous, dict) else None
            if isinstance(stop_event, dict) and stop_event.get("alert_status") == "pending":
                mark_stop_alert(state, stop_event, status="removed_before_alert", now=now)
            del observations[session_id]
    if len(events) > MAX_EVENTS:
        del events[:-MAX_EVENTS]
    return pending


def mark_stop_alert(
    state: dict[str, Any], event: dict[str, Any], *, status: str, now: float,
    message_id: int | None = None,
) -> None:
    """Commit a delivery outcome for the exact observed stop episode."""

    event["alert_status"] = status
    event["alert_observed_at"] = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()
    if message_id is not None:
        event["alert_message_id"] = message_id
    for historical in reversed(state.get("session_lifecycle_events") or []):
        if (
            isinstance(historical, dict)
            and historical.get("event") == "session_stopped"
            and historical.get("session_id") == event["session_id"]
            and historical.get("observed_at") == event["observed_at"]
        ):
            historical["alert_status"] = status
            historical["alert_observed_at"] = event["alert_observed_at"]
            if message_id is not None:
                historical["alert_message_id"] = message_id
            break
