#!/usr/bin/env python3
"""Task Team observability helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_EXECUTION_STALE_THRESHOLD_SEC = 1800
DEFAULT_REVIEW_STALE_THRESHOLD_SEC = 1200


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    token = str(raw or "").strip()
    if not token:
        return None
    normalized = token.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now_utc(now: Optional[datetime] = None) -> datetime:
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _duration_text(seconds: int) -> str:
    value = max(0, int(seconds or 0))
    if value < 60:
        return f"{value}s"
    minutes, sec = divmod(value, 60)
    if minutes < 60:
        return f"{minutes}m" if sec == 0 else f"{minutes}m{sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if minutes == 0 else f"{hours}h{minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d" if hours == 0 else f"{days}d{hours}h"


def _execution_plan_rows(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    rows = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    return [row for row in rows if isinstance(row, dict) and str(row.get("lane_id", "")).strip()]


def _review_plan_rows(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    rows = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []
    return [row for row in rows if isinstance(row, dict) and str(row.get("lane_id", "")).strip()]


def _merge_lane_rows(
    phase: str,
    plan_rows: List[Dict[str, Any]],
    state_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    ordered: List[Dict[str, Any]] = []
    state_by_lane: Dict[str, Dict[str, Any]] = {}
    for row in state_rows:
        if not isinstance(row, dict):
            continue
        lane_id = str(row.get("lane_id", "")).strip()
        if lane_id:
            state_by_lane[lane_id] = row
    seen: set[str] = set()

    def build_row(base: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        lane_id = str((state or {}).get("lane_id", "")).strip() or str((base or {}).get("lane_id", "")).strip()
        role = str((state or {}).get("role", "")).strip() or str((base or {}).get("role", "")).strip() or "Worker"
        kind = str((state or {}).get("kind", "")).strip() or str((base or {}).get("kind", "")).strip()
        status = str((state or {}).get("status", "")).strip() or "pending"
        reason = str((state or {}).get("reason", "")).strip()
        depends_on = [str(x).strip() for x in ((state or {}).get("depends_on") or (base or {}).get("depends_on") or []) if str(x).strip()]
        waiting_on = [str(x).strip() for x in ((state or {}).get("waiting_on") or []) if str(x).strip()]
        subtask_ids = [str(x).strip() for x in ((state or {}).get("subtask_ids") or (base or {}).get("subtask_ids") or []) if str(x).strip()]
        verdict = str((state or {}).get("verdict", "")).strip()
        action = str((state or {}).get("action", "")).strip()
        row: Dict[str, Any] = {
            "lane_id": lane_id,
            "phase": phase,
            "role": role,
            "kind": kind or ("verifier" if phase == "review" else ""),
            "status": status,
            "reason": reason,
        }
        if depends_on:
            row["depends_on"] = depends_on
        if waiting_on:
            row["waiting_on"] = waiting_on
        if subtask_ids:
            row["subtask_ids"] = subtask_ids
        if verdict:
            row["verdict"] = verdict
        if action:
            row["action"] = action
        for key in (
            "request_id",
            "started_at",
            "last_event_at",
            "last_event_kind",
            "backend",
            "outcome_reason_code",
        ):
            token = str((state or {}).get(key, "")).strip()
            if token:
                row[key] = token
        touched_files = [str(x).strip() for x in ((state or {}).get("touched_files") or []) if str(x).strip()]
        if touched_files:
            row["touched_files"] = touched_files
        return row

    for base in plan_rows:
        lane_id = str(base.get("lane_id", "")).strip()
        if not lane_id:
            continue
        ordered.append(build_row(base, state_by_lane.get(lane_id, {})))
        seen.add(lane_id)
    for row in state_rows:
        if not isinstance(row, dict):
            continue
        lane_id = str(row.get("lane_id", "")).strip()
        if not lane_id or lane_id in seen:
            continue
        ordered.append(build_row({}, row))
    return ordered


def _lane_note(row: Dict[str, Any]) -> str:
    reason = str(row.get("reason", "")).strip()
    if reason:
        return reason
    verdict = str(row.get("verdict", "")).strip()
    action = str(row.get("action", "")).strip()
    if verdict and action and action != "none":
        return f"verdict={verdict}/{action}"
    if verdict:
        return f"verdict={verdict}"
    waiting_on = [str(x).strip() for x in (row.get("waiting_on") or []) if str(x).strip()]
    if waiting_on:
        return "waiting on " + ",".join(waiting_on)
    depends_on = [str(x).strip() for x in (row.get("depends_on") or []) if str(x).strip()]
    if depends_on:
        return "depends on " + ",".join(depends_on)
    subtask_ids = [str(x).strip() for x in (row.get("subtask_ids") or []) if str(x).strip()]
    if subtask_ids:
        return "subtasks=" + ",".join(subtask_ids[:3])
    return "-"


def _stale_threshold_for_row(row: Dict[str, Any]) -> int:
    if str(row.get("phase", "")).strip() == "review":
        return DEFAULT_REVIEW_STALE_THRESHOLD_SEC
    return DEFAULT_EXECUTION_STALE_THRESHOLD_SEC


def _stale_candidate(row: Dict[str, Any]) -> bool:
    status = str(row.get("status", "")).strip().lower()
    return status in {"running", "pending", "waiting_on_dependencies"}


def _bottleneck_sort_key(row: Dict[str, Any]) -> Tuple[int, int, int, str]:
    status = str(row.get("status", "")).strip().lower()
    phase = str(row.get("phase", "")).strip().lower()
    if status == "failed":
        status_rank = 0
    elif status == "waiting_on_dependencies":
        status_rank = 1
    elif bool(row.get("is_stale")):
        status_rank = 2
    elif status == "running":
        status_rank = 3
    elif status == "pending":
        status_rank = 4
    else:
        status_rank = 5
    phase_rank = 0 if phase == "review" and status_rank <= 1 else 1
    idle_rank = -int(row.get("idle_sec", 0) or 0)
    return status_rank, phase_rank, idle_rank, str(row.get("lane_id", "")).strip()


def _build_first_focus(bottleneck: Dict[str, Any], *, freshness_scope: str) -> str:
    lane_id = str(bottleneck.get("lane_id", "")).strip()
    if not lane_id:
        return "-"
    status = str(bottleneck.get("status", "")).strip()
    reason = str(bottleneck.get("reason", "")).strip() or _lane_note(bottleneck)
    if status == "failed":
        return f"inspect failed lane {lane_id} first: {reason}"
    if status == "waiting_on_dependencies":
        return f"inspect blocked lane {lane_id} first: {reason}"
    if bool(bottleneck.get("is_stale")):
        return f"inspect stale lane {lane_id} first ({freshness_scope}-scoped freshness)"
    return f"inspect lane {lane_id} first"


def task_team_observatory_snapshot(
    task: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
    execution_state_rows = lane_states.get("execution") if isinstance(lane_states.get("execution"), list) else []
    review_state_rows = lane_states.get("review") if isinstance(lane_states.get("review"), list) else []
    execution_rows = _merge_lane_rows("execution", _execution_plan_rows(task), execution_state_rows)
    review_rows = _merge_lane_rows("review", _review_plan_rows(task), review_state_rows)
    lane_rows = execution_rows + review_rows
    if not lane_rows:
        return {
            "freshness_scope": "",
            "task_updated_at": "",
            "task_created_at": "",
            "stale_lane_count": 0,
            "bottleneck_lane_id": "",
            "bottleneck_reason": "",
            "bottleneck_status": "",
            "first_focus": "-",
            "headline": "-",
            "lanes": [],
        }

    current = _now_utc(now)
    created_at = str(task.get("created_at", "")).strip()
    updated_at = str(task.get("updated_at", "")).strip() or created_at
    created_dt = _parse_iso_dt(created_at) or current
    updated_dt = _parse_iso_dt(updated_at) or created_dt
    freshness_scope = "task"

    lanes: List[Dict[str, Any]] = []
    for row in lane_rows:
        lane_started_at = str(row.get("started_at", "")).strip() or created_at
        lane_last_event_at = str(row.get("last_event_at", "")).strip() or updated_at
        lane_started_dt = _parse_iso_dt(lane_started_at) or created_dt
        lane_last_event_dt = _parse_iso_dt(lane_last_event_at) or updated_dt
        lane_age_sec = max(0, int((current - lane_started_dt).total_seconds()))
        lane_idle_sec = max(0, int((current - lane_last_event_dt).total_seconds()))
        lane_freshness_scope = "lane" if str(row.get("last_event_at", "")).strip() else "task"
        threshold = _stale_threshold_for_row(row)
        is_stale = _stale_candidate(row) and lane_idle_sec >= threshold
        note = _lane_note(row)
        lane = dict(row)
        lane.update(
            {
                "started_at": lane_started_at,
                "last_event_at": lane_last_event_at,
                "last_event_kind": str(row.get("last_event_kind", "")).strip() or "task_updated",
                "age_sec": lane_age_sec,
                "idle_sec": lane_idle_sec,
                "age_text": _duration_text(lane_age_sec),
                "idle_text": _duration_text(lane_idle_sec),
                "stale_threshold_sec": threshold,
                "is_stale": is_stale,
                "freshness_scope": lane_freshness_scope,
                "note": note,
            }
        )
        lanes.append(lane)

    scopes = {str(row.get("freshness_scope", "")).strip() or "task" for row in lanes if isinstance(row, dict)}
    if scopes == {"lane"}:
        freshness_scope = "lane"
    elif "lane" in scopes and "task" in scopes:
        freshness_scope = "mixed"

    stale_count = sum(1 for row in lanes if bool(row.get("is_stale")))
    ranked = sorted(lanes, key=_bottleneck_sort_key)
    bottleneck = ranked[0] if ranked else {}
    bottleneck_lane_id = str(bottleneck.get("lane_id", "")).strip()
    bottleneck_reason = (
        str(bottleneck.get("reason", "")).strip()
        or str(bottleneck.get("note", "")).strip()
        or str(bottleneck.get("status", "")).strip()
    )
    bottleneck_status = str(bottleneck.get("status", "")).strip()
    headline = (
        f"stale={stale_count} bottleneck={bottleneck_lane_id or '-'}"
        + (f"/{bottleneck_status}" if bottleneck_status else "")
        + (f" note={bottleneck_reason}" if bottleneck_reason and bottleneck_reason != "-" else "")
        + f" freshness={freshness_scope}"
    )
    return {
        "freshness_scope": freshness_scope,
        "task_updated_at": updated_at,
        "task_created_at": created_at,
        "stale_lane_count": stale_count,
        "bottleneck_lane_id": bottleneck_lane_id,
        "bottleneck_reason": bottleneck_reason or "-",
        "bottleneck_status": bottleneck_status or "-",
        "first_focus": _build_first_focus(bottleneck, freshness_scope=freshness_scope),
        "headline": headline,
        "lanes": lanes,
    }


def observatory_task_line(snapshot: Dict[str, Any]) -> str:
    bottleneck_lane_id = str(snapshot.get("bottleneck_lane_id", "")).strip() or "-"
    bottleneck_reason = str(snapshot.get("bottleneck_reason", "")).strip() or "-"
    return "team_observatory: stale={stale} bottleneck={lane}/{reason} freshness={scope}".format(
        stale=int(snapshot.get("stale_lane_count", 0) or 0),
        lane=bottleneck_lane_id,
        reason=bottleneck_reason,
        scope=str(snapshot.get("freshness_scope", "")).strip() or "-",
    )


def observatory_lane_lines(snapshot: Dict[str, Any], *, limit: int = 4) -> List[str]:
    lines: List[str] = []
    for row in (snapshot.get("lanes") or [])[: max(0, int(limit))]:
        if not isinstance(row, dict):
            continue
        lines.append(
            "- obs {lane_id} [{phase}/{role}] {status} age={age} idle={idle}{stale} note={note}".format(
                lane_id=str(row.get("lane_id", "")).strip() or "-",
                phase=str(row.get("phase", "")).strip() or "-",
                role=str(row.get("role", "")).strip() or "-",
                status=str(row.get("status", "")).strip() or "-",
                age=str(row.get("age_text", "")).strip() or "-",
                idle=str(row.get("idle_text", "")).strip() or "-",
                stale=" stale=yes" if bool(row.get("is_stale")) else "",
                note=str(row.get("note", "")).strip() or "-",
            )
        )
    return lines


def observatory_monitor_line(snapshot: Dict[str, Any]) -> str:
    bottleneck_lane_id = str(snapshot.get("bottleneck_lane_id", "")).strip()
    first_focus = str(snapshot.get("first_focus", "")).strip() or "-"
    if not bottleneck_lane_id and int(snapshot.get("stale_lane_count", 0) or 0) <= 0:
        return ""
    bottleneck = next(
        (
            row
            for row in (snapshot.get("lanes") or [])
            if isinstance(row, dict) and str(row.get("lane_id", "")).strip() == bottleneck_lane_id
        ),
        {},
    )
    idle_text = str(bottleneck.get("idle_text", "")).strip() or "-"
    status = str(bottleneck.get("status", "")).strip() or "-"
    return "observatory: stale={stale} bottleneck={lane}/{status} idle={idle} | first={focus}".format(
        stale=int(snapshot.get("stale_lane_count", 0) or 0),
        lane=bottleneck_lane_id or "-",
        status=status,
        idle=idle_text,
        focus=first_focus,
    )
