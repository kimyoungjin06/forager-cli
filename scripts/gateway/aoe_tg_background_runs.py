#!/usr/bin/env python3
"""Background run ticket state helpers."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from aoe_tg_executor_adapter import (
    EXECUTOR_SLOT_RUNNER_TARGETS,
    normalize_executor_runner_target,
)
from aoe_tg_request_contract import (
    background_run_ticket_external_worker_allowed,
    background_runner_requires_externalizable_spec,
    normalize_background_run_ticket_snapshot,
)


BACKGROUND_RUNS_FILENAME = "background_runs.json"
BACKGROUND_WORKER_FILENAME = "background_worker.json"
BACKGROUND_WORKER_STATUSES = (
    "running",
    "idle",
    "stopped",
    "error",
)
BACKGROUND_RUN_CLAIM_LAUNCH_PRIORITY = {
    "dashboard_followup_execute": 10,
    "dashboard_replan": 20,
    "dashboard_retry": 30,
    "offdesk_manual": 40,
    "detached_no_wait": 50,
}
BACKGROUND_RUN_CLAIM_STARVATION_SEC = 900
SLOT_RUNNER_TARGETS = EXECUTOR_SLOT_RUNNER_TARGETS


def background_runs_state_path(team_dir: Path, *, filename: str = BACKGROUND_RUNS_FILENAME) -> Path:
    return Path(team_dir).expanduser().resolve() / filename


def background_worker_state_path(team_dir: Path, *, filename: str = BACKGROUND_WORKER_FILENAME) -> Path:
    return Path(team_dir).expanduser().resolve() / filename


def _empty_state() -> Dict[str, Any]:
    return {
        "version": "2026-04-04.v1",
        "updated_at": "",
        "runs": [],
    }


def _empty_worker_state() -> Dict[str, Any]:
    return {
        "version": "2026-04-06.v1",
        "updated_at": "",
        "status": "stopped",
    }


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _normalize_int(raw: Any, default: int = 0) -> int:
    try:
        value = int(raw or default)
    except Exception:
        value = int(default)
    return max(0, value)


def normalize_background_runner_slot_limit(raw: Any, default: int = 1, *, max_value: int = 32) -> int:
    try:
        value = int(raw or default)
    except Exception:
        value = int(default)
    return max(1, min(value, max_value))


def normalize_background_runner_slot_limits(
    raw: Any,
    *,
    default_limit: int = 1,
    max_value: int = 32,
) -> Dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, int] = {}
    for runner_target in SLOT_RUNNER_TARGETS:
        normalized[runner_target] = normalize_background_runner_slot_limit(
            source.get(runner_target),
            default_limit,
            max_value=max_value,
        )
    return normalized


def background_runner_slot_limit_for_entry(
    entry: Dict[str, Any] | None,
    runner_target: str = "",
    *,
    default_limit: int = 1,
    max_value: int = 32,
) -> int:
    row = entry if isinstance(entry, dict) else {}
    default_slot_limit = normalize_background_runner_slot_limit(
        row.get("background_runner_slot_limit"),
        default_limit,
        max_value=max_value,
    )
    runner = normalize_executor_runner_target(runner_target)
    if runner not in SLOT_RUNNER_TARGETS:
        return default_slot_limit
    limits = normalize_background_runner_slot_limits(
        row.get("background_runner_slot_limits"),
        default_limit=default_slot_limit,
        max_value=max_value,
    )
    return int(limits.get(runner, default_slot_limit) or default_slot_limit)


def background_runner_slot_limits_for_entry(
    entry: Dict[str, Any] | None,
    *,
    default_limit: int = 1,
    max_value: int = 32,
) -> Dict[str, int]:
    row = entry if isinstance(entry, dict) else {}
    default_slot_limit = normalize_background_runner_slot_limit(
        row.get("background_runner_slot_limit"),
        default_limit,
        max_value=max_value,
    )
    return normalize_background_runner_slot_limits(
        row.get("background_runner_slot_limits"),
        default_limit=default_slot_limit,
        max_value=max_value,
    )


def background_runner_slot_pressure(limit: int, active: int) -> str:
    safe_limit = max(1, int(limit or 1))
    safe_active = max(0, int(active or 0))
    if safe_active >= safe_limit:
        return f"saturated ({safe_active}/{safe_limit})"
    if safe_active > 0:
        return f"active ({safe_active}/{safe_limit})"
    return f"idle (0/{safe_limit})"


def load_background_runs_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    runs: List[Dict[str, Any]] = []
    for row in list(data.get("runs") or []):
        snapshot = normalize_background_run_ticket_snapshot(row)
        if snapshot:
            runs.append(snapshot)
    loaded = _empty_state()
    loaded["version"] = str(data.get("version", loaded["version"])).strip() or loaded["version"]
    loaded["updated_at"] = str(data.get("updated_at", "")).strip()
    loaded["runs"] = runs
    return loaded


def normalize_background_worker_state_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    status = _trim(raw.get("status", "stopped"), 32).lower() or "stopped"
    if status not in BACKGROUND_WORKER_STATUSES:
        status = "stopped"

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", "2026-04-06.v1"), 48) or "2026-04-06.v1",
        "status": status,
    }
    runner_target = _trim(raw.get("runner_target", ""), 64).lower()
    if runner_target:
        snapshot["runner_target"] = runner_target
    for key, limit in (
        ("mode", 32),
        ("thread_name", 96),
        ("started_at", 64),
        ("heartbeat_at", 64),
        ("stopped_at", 64),
        ("last_reason", 160),
        ("last_ticket_id", 96),
        ("last_claimed_at", 64),
        ("queue_summary", 240),
    ):
        token = _trim(raw.get(key, ""), limit)
        if token:
            snapshot[key] = token
    for key in ("pid", "claimed_count", "drain_cycles", "queue_depth", "queue_stale_count"):
        value = _normalize_int(raw.get(key), 0)
        if value > 0 or key in {"claimed_count", "drain_cycles", "queue_depth", "queue_stale_count"}:
            snapshot[key] = value
    return snapshot


def load_background_worker_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_worker_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_worker_state()
    snapshot = normalize_background_worker_state_snapshot(data)
    if not snapshot:
        return _empty_worker_state()
    loaded = _empty_worker_state()
    loaded.update(snapshot)
    loaded["updated_at"] = _trim(data.get("updated_at", ""), 64)
    return loaded


def save_background_runs_state(
    path: Path,
    state: Dict[str, Any],
    *,
    now_iso: Callable[[], str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _empty_state()
    payload["version"] = str(state.get("version", payload["version"])).strip() or payload["version"]
    payload["updated_at"] = now_iso()
    payload["runs"] = [
        normalize_background_run_ticket_snapshot(row)
        for row in list(state.get("runs") or [])
        if normalize_background_run_ticket_snapshot(row)
    ]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_background_worker_state(
    path: Path,
    state: Dict[str, Any],
    *,
    now_iso: Callable[[], str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _empty_worker_state()
    payload.update(normalize_background_worker_state_snapshot(state))
    payload["updated_at"] = now_iso()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_background_worker_state(
    path: Path,
    *,
    now_iso: Callable[[], str],
    status: str = "",
    runner_target: str = "",
    mode: str = "",
    thread_name: str = "",
    started_at: str = "",
    heartbeat_at: str = "",
    stopped_at: str = "",
    last_reason: str = "",
    last_ticket_id: str = "",
    last_claimed_at: str = "",
    queue_summary: str = "",
    pid: int | None = None,
    claimed_count: int | None = None,
    drain_cycles: int | None = None,
    queue_depth: int | None = None,
    queue_stale_count: int | None = None,
) -> Dict[str, Any]:
    current = load_background_worker_state(path)
    updated = dict(current)
    for key, value in (
        ("status", status),
        ("runner_target", runner_target),
        ("mode", mode),
        ("thread_name", thread_name),
        ("started_at", started_at),
        ("heartbeat_at", heartbeat_at),
        ("stopped_at", stopped_at),
        ("last_reason", last_reason),
        ("last_ticket_id", last_ticket_id),
        ("last_claimed_at", last_claimed_at),
        ("queue_summary", queue_summary),
    ):
        token = _trim(value, 240 if key == "queue_summary" else 160)
        if token:
            updated[key] = token
    for key, value in (
        ("pid", pid),
        ("claimed_count", claimed_count),
        ("drain_cycles", drain_cycles),
        ("queue_depth", queue_depth),
        ("queue_stale_count", queue_stale_count),
    ):
        if value is not None:
            updated[key] = _normalize_int(value, 0)
    save_background_worker_state(path, updated, now_iso=now_iso)
    return load_background_worker_state(path)


def upsert_background_run_ticket(
    path: Path,
    ticket: Dict[str, Any],
    *,
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    snapshot = normalize_background_run_ticket_snapshot(ticket)
    if not snapshot:
        return {}
    snapshot["touched_at"] = now_iso()
    state = load_background_runs_state(path)
    ticket_id = str(snapshot.get("ticket_id", "")).strip()
    runs: List[Dict[str, Any]] = []
    replaced = False
    for row in list(state.get("runs") or []):
        existing = normalize_background_run_ticket_snapshot(row)
        if not existing:
            continue
        if ticket_id and str(existing.get("ticket_id", "")).strip() == ticket_id:
            runs.append(snapshot)
            replaced = True
        else:
            runs.append(existing)
    if not replaced:
        runs.append(snapshot)
    state["runs"] = runs[-200:]
    save_background_runs_state(path, state, now_iso=now_iso)
    return snapshot


def get_background_run_ticket(path: Path, ticket_id: str) -> Dict[str, Any]:
    token = str(ticket_id or "").strip()
    if not token:
        return {}
    state = load_background_runs_state(path)
    for row in list(state.get("runs") or []):
        snapshot = normalize_background_run_ticket_snapshot(row)
        if snapshot and str(snapshot.get("ticket_id", "")).strip() == token:
            return snapshot
    return {}


def list_background_run_tickets(
    path: Path,
    *,
    statuses: List[str] | None = None,
    runner_target: str = "",
) -> List[Dict[str, Any]]:
    allowed_statuses = {
        str(item or "").strip().lower()
        for item in list(statuses or [])
        if str(item or "").strip()
    }
    requested_runner = normalize_executor_runner_target(runner_target)
    rows: List[Dict[str, Any]] = []
    state = load_background_runs_state(path)
    for row in list(state.get("runs") or []):
        snapshot = normalize_background_run_ticket_snapshot(row)
        if not snapshot:
            continue
        status = str(snapshot.get("status", "")).strip().lower()
        if allowed_statuses and status not in allowed_statuses:
            continue
        row_runner = normalize_executor_runner_target(snapshot.get("runner_target", ""))
        if requested_runner and row_runner and row_runner != requested_runner:
            continue
        rows.append(snapshot)
    return rows


def count_background_run_tickets(
    path: Path,
    *,
    statuses: List[str] | None = None,
    runner_targets: List[str] | None = None,
) -> int:
    allowed_statuses = {
        str(item or "").strip().lower()
        for item in list(statuses or [])
        if str(item or "").strip()
    }
    allowed_targets = {
        str(item or "").strip().lower()
        for item in list(runner_targets or [])
        if str(item or "").strip()
    }
    count = 0
    for snapshot in list_background_run_tickets(path, statuses=list(allowed_statuses) or None):
        row_target = normalize_executor_runner_target(snapshot.get("runner_target", ""))
        if allowed_targets and row_target not in allowed_targets:
            continue
        count += 1
    return count


def count_background_run_tickets_by_runner(
    path: Path,
    *,
    statuses: List[str] | None = None,
    runner_targets: List[str] | None = None,
) -> Dict[str, int]:
    allowed_targets = [
        str(item or "").strip().lower()
        for item in list(runner_targets or SLOT_RUNNER_TARGETS)
        if str(item or "").strip()
    ]
    counts: Dict[str, int] = {target: 0 for target in allowed_targets}
    for snapshot in list_background_run_tickets(path, statuses=statuses):
        row_target = normalize_executor_runner_target(snapshot.get("runner_target", ""))
        if row_target not in counts:
            continue
        counts[row_target] = int(counts.get(row_target, 0) or 0) + 1
    return counts


def summarize_background_runner_slots(
    path: Path,
    entry: Dict[str, Any] | None,
    *,
    selected_runner: str = "",
    statuses: List[str] | None = None,
    max_value: int = 32,
) -> Dict[str, Any]:
    limits = background_runner_slot_limits_for_entry(
        entry,
        default_limit=1,
        max_value=max_value,
    )
    active_by_runner = count_background_run_tickets_by_runner(
        path,
        statuses=statuses,
        runner_targets=list(SLOT_RUNNER_TARGETS),
    )
    selected = normalize_executor_runner_target(selected_runner)
    if selected not in SLOT_RUNNER_TARGETS:
        selected = ""
    selected_limit = (
        int(limits.get(selected, 0) or 0)
        if selected
        else normalize_background_runner_slot_limit(
            (entry or {}).get("background_runner_slot_limit") if isinstance(entry, dict) else 1,
            1,
            max_value=max_value,
        )
    )
    selected_active = int(active_by_runner.get(selected, 0) or 0) if selected else 0
    summary = " ".join(
        f"{runner_target}={int(active_by_runner.get(runner_target, 0) or 0)}/{int(limits.get(runner_target, 1) or 1)}"
        for runner_target in SLOT_RUNNER_TARGETS
    )
    return {
        "selected_runner": selected,
        "selected_limit": selected_limit,
        "selected_active": selected_active,
        "selected_pressure": background_runner_slot_pressure(selected_limit, selected_active),
        "default_limit": normalize_background_runner_slot_limit(
            (entry or {}).get("background_runner_slot_limit") if isinstance(entry, dict) else 1,
            1,
            max_value=max_value,
        ),
        "limits_by_runner": limits,
        "active_by_runner": active_by_runner,
        "total_active": sum(int(active_by_runner.get(key, 0) or 0) for key in SLOT_RUNNER_TARGETS),
        "summary": summary,
    }


def claim_background_run_ticket(
    path: Path,
    ticket_id: str,
    *,
    now_iso: Callable[[], str],
    runner_target: str = "local_background",
    launch_mode: str = "",
    claimed_by: str = "",
    source_surface: str = "",
) -> Dict[str, Any]:
    token = str(ticket_id or "").strip()
    if not token:
        return {}
    current = get_background_run_ticket(path, token)
    if not current:
        return {}
    current_status = str(current.get("status", "")).strip().lower()
    if current_status not in {"queued", "stale"}:
        return current
    requested_runner = normalize_executor_runner_target(
        runner_target or current.get("runner_target", ""),
        "local_background",
    ) or "local_background"
    if (
        background_runner_requires_externalizable_spec(requested_runner)
        and not background_run_ticket_external_worker_allowed(
            {
                **current,
                "runner_target": requested_runner,
            }
        )
    ):
        return advance_background_run_ticket(
            path,
            token,
            now_iso=now_iso,
            status="failed",
            runner_target=requested_runner,
            launch_mode=str(launch_mode or current.get("launch_mode", "")).strip(),
            created_by=str(claimed_by or current.get("created_by", "")).strip(),
            source_surface=str(source_surface or current.get("source_surface", "")).strip(),
            evidence_bundle="status=failed | reason=launch_spec_not_externalizable",
        )
    return advance_background_run_ticket(
        path,
        token,
        now_iso=now_iso,
        status="dispatching",
        runner_target=requested_runner,
        launch_mode=str(launch_mode or current.get("launch_mode", "")).strip(),
        created_by=str(claimed_by or current.get("created_by", "")).strip(),
        source_surface=str(source_surface or current.get("source_surface", "")).strip(),
        evidence_bundle="status=dispatching | outcome=worker_claimed",
    )


def claim_next_background_run_ticket(
    path: Path,
    *,
    now_iso: Callable[[], str],
    runner_target: str = "local_background",
    launch_mode: str = "",
    claimed_by: str = "",
    source_surface: str = "",
) -> Dict[str, Any]:
    requested_runner = normalize_executor_runner_target(runner_target, "local_background") or "local_background"
    candidates = list_background_run_tickets(
        path,
        statuses=["queued", "stale"],
        runner_target=requested_runner,
    )
    if not candidates:
        return {}
    candidates = sort_background_run_claim_candidates(candidates, now_iso=now_iso)
    return claim_background_run_ticket(
        path,
        str(candidates[0].get("ticket_id", "")).strip(),
        now_iso=now_iso,
        runner_target=requested_runner,
        launch_mode=launch_mode,
        claimed_by=claimed_by,
        source_surface=source_surface,
    )


def advance_background_run_ticket(
    path: Path,
    ticket_id: str,
    *,
    now_iso: Callable[[], str],
    status: str = "",
    runner_target: str = "",
    launch_mode: str = "",
    created_by: str = "",
    source_surface: str = "",
    execution_brief_status: str = "",
    runtime_handle: str = "",
    runtime_summary: str = "",
    worker_result_status: str = "",
    worker_result_summary: str = "",
    worker_gate_status: str = "",
    worker_gate_summary: str = "",
    worker_profile_status: str = "",
    worker_profile_summary: str = "",
    worker_checklist_status: str = "",
    worker_checklist_summary: str = "",
    worker_items_summary: str = "",
    worker_items: List[Any] | None = None,
    worker_result_actions: List[Any] | None = None,
    worker_result_cautions: List[Any] | None = None,
    worker_result_evidence_refs: List[Any] | None = None,
    worker_update_stub_status: str = "",
    worker_update_stub_summary: str = "",
    worker_update_stub_targets: List[Any] | None = None,
    evidence_bundle: str = "",
    evidence_artifacts: List[Any] | None = None,
) -> Dict[str, Any]:
    token = str(ticket_id or "").strip()
    if not token:
        return {}
    current = get_background_run_ticket(path, token)
    if not current:
        return {}
    updated = dict(current)
    if str(status or "").strip():
        updated["status"] = str(status or "").strip()
    if str(runner_target or "").strip():
        updated["runner_target"] = str(runner_target or "").strip()
    if str(launch_mode or "").strip():
        updated["launch_mode"] = str(launch_mode or "").strip()
    if str(created_by or "").strip():
        updated["created_by"] = str(created_by or "").strip()
    if str(source_surface or "").strip():
        updated["source_surface"] = str(source_surface or "").strip()
    if str(execution_brief_status or "").strip():
        updated["execution_brief_status"] = str(execution_brief_status or "").strip()
    if str(runtime_handle or "").strip():
        updated["runtime_handle"] = str(runtime_handle or "").strip()
    if str(runtime_summary or "").strip():
        updated["runtime_summary"] = str(runtime_summary or "").strip()
    if str(worker_result_status or "").strip():
        updated["worker_result_status"] = str(worker_result_status or "").strip()
    if str(worker_result_summary or "").strip():
        updated["worker_result_summary"] = str(worker_result_summary or "").strip()
    if str(worker_gate_status or "").strip():
        updated["worker_gate_status"] = str(worker_gate_status or "").strip()
    if str(worker_gate_summary or "").strip():
        updated["worker_gate_summary"] = str(worker_gate_summary or "").strip()
    if str(worker_profile_status or "").strip():
        updated["worker_profile_status"] = str(worker_profile_status or "").strip()
    if str(worker_profile_summary or "").strip():
        updated["worker_profile_summary"] = str(worker_profile_summary or "").strip()
    if str(worker_checklist_status or "").strip():
        updated["worker_checklist_status"] = str(worker_checklist_status or "").strip()
    if str(worker_checklist_summary or "").strip():
        updated["worker_checklist_summary"] = str(worker_checklist_summary or "").strip()
    if str(worker_items_summary or "").strip():
        updated["worker_items_summary"] = str(worker_items_summary or "").strip()
    if worker_items is not None:
        updated["worker_items"] = list(worker_items or [])
    if worker_result_actions is not None:
        updated["worker_result_actions"] = list(worker_result_actions or [])
    if worker_result_cautions is not None:
        updated["worker_result_cautions"] = list(worker_result_cautions or [])
    if worker_result_evidence_refs is not None:
        updated["worker_result_evidence_refs"] = list(worker_result_evidence_refs or [])
    if str(worker_update_stub_status or "").strip():
        updated["worker_update_stub_status"] = str(worker_update_stub_status or "").strip()
    if str(worker_update_stub_summary or "").strip():
        updated["worker_update_stub_summary"] = str(worker_update_stub_summary or "").strip()
    if worker_update_stub_targets is not None:
        updated["worker_update_stub_targets"] = list(worker_update_stub_targets or [])
    if str(evidence_bundle or "").strip():
        updated["evidence_bundle"] = str(evidence_bundle or "").strip()
    if evidence_artifacts is not None:
        updated["evidence_artifacts"] = list(evidence_artifacts or [])
    return upsert_background_run_ticket(path, updated, now_iso=now_iso)


def _parse_iso_datetime(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def background_run_claim_sort_key(snapshot: Dict[str, Any]) -> tuple[int, str, str, str]:
    row = normalize_background_run_ticket_snapshot(snapshot)
    if not row:
        return (999, "", "", "")
    launch_mode = str(row.get("launch_mode", "")).strip().lower()
    launch_priority = int(BACKGROUND_RUN_CLAIM_LAUNCH_PRIORITY.get(launch_mode, 100) or 100)
    created_at = str(row.get("created_at", "")).strip()
    touched_at = str(row.get("touched_at", "")).strip()
    time_key = created_at or touched_at
    ticket_id = str(row.get("ticket_id", "")).strip()
    return (launch_priority, time_key, touched_at, ticket_id)


def background_run_claim_age_sec(snapshot: Dict[str, Any], *, now_iso: Callable[[], str]) -> int:
    row = normalize_background_run_ticket_snapshot(snapshot)
    if not row:
        return 0
    current_dt = _parse_iso_datetime(now_iso())
    created = _parse_iso_datetime(row.get("created_at"))
    if current_dt is None or created is None:
        return 0
    return max(0, int((current_dt - created).total_seconds()))


def sort_background_run_claim_candidates(
    candidates: List[Dict[str, Any]],
    *,
    now_iso: Callable[[], str],
    starvation_sec: int = BACKGROUND_RUN_CLAIM_STARVATION_SEC,
) -> List[Dict[str, Any]]:
    normalized = [normalize_background_run_ticket_snapshot(row) for row in candidates]
    normalized = [row for row in normalized if row]
    if not normalized:
        return []
    base_sorted = sorted(normalized, key=background_run_claim_sort_key)
    starved_rows = [
        row
        for row in base_sorted
        if background_run_claim_age_sec(row, now_iso=now_iso) >= max(1, int(starvation_sec or 1))
    ]
    if not starved_rows:
        return base_sorted
    starved_ids = {str(item.get("ticket_id", "")).strip() for item in starved_rows}
    return sorted(
        starved_rows,
        key=lambda row: (
            -(background_run_claim_age_sec(row, now_iso=now_iso)),
            background_run_claim_sort_key(row),
        ),
    ) + [row for row in base_sorted if str(row.get("ticket_id", "")).strip() not in starved_ids]


def summarize_background_runner_scheduling(
    path: Path,
    *,
    now_iso: Callable[[], str] | None = None,
) -> Dict[str, Any]:
    per_runner: Dict[str, Dict[str, Any]] = {}
    parts: List[str] = []
    current_iso = now_iso or (lambda: datetime.now(timezone.utc).isoformat())
    for runner_target in ("local_background", "local_tmux", "github_runner", "remote_worker"):
        queued_rows = sort_background_run_claim_candidates(
            list_background_run_tickets(path, statuses=["queued", "stale"], runner_target=runner_target),
            now_iso=current_iso,
        )
        head = queued_rows[0] if queued_rows else {}
        head_ticket_id = str(head.get("ticket_id", "")).strip()
        head_launch_mode = str(head.get("launch_mode", "")).strip().lower()
        head_age_sec = background_run_claim_age_sec(head, now_iso=current_iso) if head else 0
        head_starved = bool(head_age_sec >= max(1, int(BACKGROUND_RUN_CLAIM_STARVATION_SEC or 1))) if head else False
        row = {
            "queued_count": len(queued_rows),
            "head_ticket_id": head_ticket_id,
            "head_launch_mode": head_launch_mode,
            "head_age_sec": head_age_sec,
            "head_starved": head_starved,
        }
        per_runner[runner_target] = row
        if head_ticket_id:
            parts.append(
                f"{runner_target}:head={head_ticket_id}/{head_launch_mode or '-'} queued={len(queued_rows)}"
                + (" starved=yes" if head_starved else "")
            )
    return {
        "by_runner": per_runner,
        "summary": " | ".join(parts) if parts else "-",
    }


def summarize_background_runs_state(path: Path) -> Dict[str, Any]:
    state = load_background_runs_state(path)
    status_counts: Dict[str, int] = {}
    target_counts: Dict[str, int] = {}
    runs = list(state.get("runs") or [])
    for row in runs:
        snapshot = normalize_background_run_ticket_snapshot(row)
        if not snapshot:
            continue
        status = str(snapshot.get("status", "")).strip().lower()
        target = normalize_executor_runner_target(snapshot.get("runner_target", ""))
        if status:
            status_counts[status] = int(status_counts.get(status, 0) or 0) + 1
        if target:
            target_counts[target] = int(target_counts.get(target, 0) or 0) + 1
    depth = sum(int(status_counts.get(key, 0) or 0) for key in ("queued", "dispatching", "running"))
    stale_count = int(status_counts.get("stale", 0) or 0)
    status_order = ("queued", "dispatching", "running", "completed", "failed", "canceled", "stale")
    target_order = ("local_background", "local_tmux", "github_runner", "remote_worker")
    parts: List[str] = [f"depth={depth}"]
    status_parts = [f"{key}={status_counts[key]}" for key in status_order if status_counts.get(key)]
    if status_parts:
        parts.append("status " + " ".join(status_parts))
    target_parts = [f"{key}={target_counts[key]}" for key in target_order if target_counts.get(key)]
    if target_parts:
        parts.append("target " + " ".join(target_parts))
    return {
        "count": len(runs),
        "depth": depth,
        "stale_count": stale_count,
        "status_counts": status_counts,
        "target_counts": target_counts,
        "summary": " | ".join(parts) if parts else "-",
        "updated_at": str(state.get("updated_at", "")).strip(),
    }


def mark_stale_background_run_tickets(
    path: Path,
    *,
    now_iso: Callable[[], str],
    stale_after_sec: int = 3600,
    statuses: List[str] | None = None,
) -> Dict[str, Any]:
    state = load_background_runs_state(path)
    rows = list(state.get("runs") or [])
    if not rows:
        return {"stale_count": 0, "changed": False}
    current_dt = _parse_iso_datetime(now_iso())
    if current_dt is None:
        return {"stale_count": 0, "changed": False}
    stale_statuses = {
        str(item or "").strip().lower()
        for item in list(statuses or ["dispatching", "running"])
        if str(item or "").strip()
    }
    changed = False
    stale_count = 0
    updated_rows: List[Dict[str, Any]] = []
    for row in rows:
        snapshot = normalize_background_run_ticket_snapshot(row)
        if not snapshot:
            continue
        status = str(snapshot.get("status", "")).strip().lower()
        touched = _parse_iso_datetime(snapshot.get("touched_at") or snapshot.get("created_at"))
        if status in stale_statuses and touched is not None:
            age_sec = (current_dt - touched).total_seconds()
            if age_sec >= max(1, int(stale_after_sec or 1)):
                snapshot["status"] = "stale"
                snapshot["evidence_bundle"] = f"status=stale | reason=timeout:{status}"
                snapshot["touched_at"] = now_iso()
                changed = True
                stale_count += 1
        updated_rows.append(snapshot)
    if changed:
        save_background_runs_state(
            path,
            {
                "version": state.get("version", "2026-04-04.v1"),
                "updated_at": state.get("updated_at", ""),
                "runs": updated_rows,
            },
            now_iso=now_iso,
        )
    return {"stale_count": stale_count, "changed": changed}


def summarize_background_worker_state(
    path: Path,
    *,
    stale_after_sec: int = 180,
    now_iso: Callable[[], str] | None = None,
) -> Dict[str, Any]:
    snapshot = load_background_worker_state(path)
    status = str(snapshot.get("status", "stopped")).strip().lower() or "stopped"
    heartbeat_at = str(snapshot.get("heartbeat_at", "")).strip()
    stale = False
    if status in {"running", "idle"} and heartbeat_at and callable(now_iso):
        current_dt = _parse_iso_datetime(now_iso())
        heartbeat_dt = _parse_iso_datetime(heartbeat_at)
        if current_dt is not None and heartbeat_dt is not None:
            stale = (current_dt - heartbeat_dt).total_seconds() >= max(1, int(stale_after_sec or 1))
    effective_status = "stale" if stale else status
    parts: List[str] = [f"status={effective_status}"]
    runner_target = str(snapshot.get("runner_target", "")).strip()
    if runner_target:
        parts.append(f"target={runner_target}")
    pid = int(snapshot.get("pid", 0) or 0)
    if pid > 0:
        parts.append(f"pid={pid}")
    queue_depth = int(snapshot.get("queue_depth", 0) or 0)
    queue_stale_count = int(snapshot.get("queue_stale_count", 0) or 0)
    parts.append(f"queue={queue_depth}")
    if queue_stale_count > 0:
        parts.append(f"stale_queue={queue_stale_count}")
    claimed_count = int(snapshot.get("claimed_count", 0) or 0)
    if claimed_count > 0:
        parts.append(f"claimed={claimed_count}")
    last_reason = str(snapshot.get("last_reason", "")).strip()
    if last_reason:
        parts.append(f"reason={last_reason}")
    return {
        "status": effective_status,
        "runner_target": runner_target or "-",
        "heartbeat_at": heartbeat_at or "-",
        "stale": stale,
        "queue_depth": queue_depth,
        "queue_stale_count": queue_stale_count,
        "claimed_count": claimed_count,
        "summary": " | ".join(parts) if parts else "-",
    }
