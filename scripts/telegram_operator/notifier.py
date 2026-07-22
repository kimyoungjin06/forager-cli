"""Proactive attention notifications for the Telegram operator.

The live poller only responds to incoming messages. For urgent handling the
operator also needs to be told, unprompted, when something is waiting: an open
decision the harness raised, or an accepted-truth recovery follow-up. This
module turns the current workstation surface into a short, deduplicated
notification card pointing at the exact command to run.

It is read-only: it inspects the operator-safe surface and sends one card. It
never mutates project or runtime state.
"""

from __future__ import annotations

import html
from typing import Any

from .common import utc_now
from .dispatch import open_decision_actions, open_recovery_actions
from .persistence import parse_utc_timestamp
from .rendering import sanitize_text, title_with_profile

ATTENTION_NOTIFICATION_SCHEMA = "telegram_attention_notification.v1"


def attention_items_from_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract actionable attention items (open decisions, recovery follow-ups)."""

    items: list[dict[str, Any]] = []
    for decision in open_decision_actions(surface):
        actions = decision.get("actions") or []
        if not actions:
            continue
        decision_id = str(decision.get("decision_id") or "").strip()
        if not decision_id:
            continue
        first_action = str(actions[0].get("action_kind") or "").strip()
        items.append(
            {
                "kind": "decision",
                "key": f"decision:{decision_id}",
                "id": decision_id,
                "title": sanitize_text(str(decision.get("title") or decision_id), max_chars=80),
                "command_hint": f"/decision {decision_id} {first_action}".strip(),
            }
        )
    for recovery in open_recovery_actions(surface):
        actions = recovery.get("actions") or []
        if not actions:
            continue
        closeout_id = str(recovery.get("closeout_id") or "").strip()
        if not closeout_id:
            continue
        first_action = str(actions[0].get("action_kind") or "").strip()
        items.append(
            {
                "kind": "recovery",
                "key": f"recovery:{closeout_id}",
                "id": closeout_id,
                "title": sanitize_text(
                    str(recovery.get("next_safe_action") or closeout_id), max_chars=80
                ),
                "command_hint": f"/recover {closeout_id} {first_action}".strip(),
            }
        )
    return items


def tasks_needing_review_from_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Tasks the surface flags for operator review (stuck, failed, resume-pending)."""

    items: list[dict[str, Any]] = []
    projects = surface.get("projects")
    if not isinstance(projects, list):
        return items
    for project in projects:
        if not isinstance(project, dict):
            continue
        task_items = project.get("task_items")
        if not isinstance(task_items, list):
            continue
        for item in task_items:
            if not isinstance(item, dict):
                continue
            if not item.get("requires_operator_review"):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            items.append(
                {
                    "kind": "task",
                    "key": f"task:{task_id}",
                    "id": task_id,
                    "title": sanitize_text(str(item.get("title") or task_id), max_chars=80),
                    "status": str(item.get("status") or ""),
                    "command_hint": f"/cancel-task {task_id}",
                }
            )
    return items


def attention_summary(surface: dict[str, Any]) -> dict[str, Any]:
    """Aggregate everything waiting for the operator into one triage summary.

    Combines open decisions, accepted-truth recovery follow-ups, and tasks the
    surface flags for review. The top item is the single most urgent action,
    prioritizing decisions, then recovery, then tasks.
    """

    combined = attention_items_from_surface(surface)
    decisions = [item for item in combined if item.get("kind") == "decision"]
    recovery = [item for item in combined if item.get("kind") == "recovery"]
    tasks = tasks_needing_review_from_surface(surface)
    ordered = decisions + recovery + tasks
    return {
        "schema": ATTENTION_NOTIFICATION_SCHEMA,
        "decision_count": len(decisions),
        "recovery_count": len(recovery),
        "task_count": len(tasks),
        "total": len(ordered),
        "top": ordered[0] if ordered else None,
        "items": ordered,
    }


def select_items_to_notify(
    items: list[dict[str, Any]],
    notified: dict[str, Any],
    *,
    now: str,
    reminder_sec: int,
) -> list[dict[str, Any]]:
    """Return items to notify now and update the notified registry in place.

    An item is notified the first time it appears. If reminder_sec > 0, an item
    still present after that interval is notified again. Items no longer present
    are pruned so a resolved-then-reopened target notifies afresh.
    """

    present_keys = {str(item.get("key") or "") for item in items if item.get("key")}
    # Prune registry entries for items that are no longer waiting.
    for key in list(notified.keys()):
        if key not in present_keys:
            del notified[key]

    now_dt = parse_utc_timestamp(now)
    to_notify: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("key") or "")
        if not key:
            continue
        record = notified.get(key)
        if record is None:
            to_notify.append(item)
            notified[key] = {"first_notified_at": now, "last_notified_at": now}
            continue
        if reminder_sec > 0 and now_dt is not None:
            last = parse_utc_timestamp(record.get("last_notified_at"))
            if last is not None and (now_dt - last).total_seconds() >= reminder_sec:
                to_notify.append(item)
                record["last_notified_at"] = now
    return to_notify


def render_attention_message(*, profile: Any, generated_at: Any, items: list[dict[str, Any]]) -> str:
    lines = [title_with_profile("조치 필요", profile)]
    total = len(items)
    if total == 1:
        lines.append(f"1건이 대기 중입니다.")
    else:
        lines.append(f"{total}건이 대기 중입니다.")
    for item in items[:2]:
        lines.append(f"{html.escape(str(item.get('title') or ''))} → {html.escape(str(item.get('command_hint') or ''))}")
    lines.append("다음 조치: /decisions · /recovery")
    return "\n".join(lines)


def attention_notified_state(state: dict[str, Any]) -> dict[str, Any]:
    notified = state.get("attention_notified_by_key")
    if not isinstance(notified, dict):
        notified = {}
        state["attention_notified_by_key"] = notified
    return notified


def build_attention_notification(
    surface: dict[str, Any],
    state: dict[str, Any],
    *,
    reminder_sec: int,
) -> dict[str, Any]:
    """Compute the attention items to notify and mark them in ``state``.

    Returns a result describing the pending items and which ones are fresh; the
    caller renders and sends the card for ``fresh_items``.
    """

    items = attention_items_from_surface(surface)
    notified = attention_notified_state(state)
    fresh = select_items_to_notify(items, notified, now=utc_now(), reminder_sec=reminder_sec)
    return {
        "schema": ATTENTION_NOTIFICATION_SCHEMA,
        "pending_count": len(items),
        "fresh_items": fresh,
    }
