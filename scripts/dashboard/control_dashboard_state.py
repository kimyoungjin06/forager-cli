#!/usr/bin/env python3
"""Read-only dashboard DTO assembly for the Control Plane board."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_runtime_core as runtime_core
import aoe_tg_operator_summary as operator_summary
import aoe_tg_history_search as history_search
import aoe_tg_task_state as task_state

from control_dashboard_state_builders import (
    _build_active_task_rows,
    _build_recovery_summary,
    _build_runtime_cards,
    _build_runtime_detail,
    _build_task_detail,
    _next_retry_target_text,
    _provider_summary_text,
    _recovery_control_action_buttons,
    _recovery_summary_path,
    _repeat_summary_text,
)
from control_dashboard_state_io import (
    ActionAuditRowDTO,
    _action_audit_status_summary,
    ControlPaths,
    FileFreshnessDTO,
    ManagerStateLoadResult,
    _load_json_file,
    _load_latest_command_resolution,
    _load_manager_state,
    _load_recent_action_audit,
    now_iso,
    resolve_control_paths,
)
from control_dashboard_state_models import (
    ActionAuditPageDTO,
    ActiveTaskRowDTO,
    ControlSummaryDTO,
    DashboardSnapshotDTO,
    DashboardSnapshotLoadResult,
    HistorySearchPageDTO,
    HistorySearchRowDTO,
    RecoverySummaryDTO,
    RuntimeCardDTO,
    RuntimeDetailDTO,
    TaskDetailDTO,
)


def _execution_brief_summary_text(runtime_cards: list[RuntimeCardDTO]) -> str:
    counts: dict[str, int] = {}
    for card in runtime_cards:
        token = str(card.active_task_execution_brief_status or "").strip().lower()
        if not token or token == "-":
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return "-"
    order = (
        "executable",
        "partially_executable",
        "underspecified",
        "operator_decision_required",
        "infeasible",
    )
    parts = [f"{key}={counts[key]}" for key in order if counts.get(key)]
    for key in sorted(counts.keys()):
        if key not in order:
            parts.append(f"{key}={counts[key]}")
    return " | ".join(parts) or "-"


def _background_run_summary_text(runtime_cards: list[RuntimeCardDTO]) -> str:
    status_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    queue_depth = 0
    stale_count = 0
    for card in runtime_cards:
        status = str(card.active_task_background_run_status or "").strip().lower()
        target = str(card.active_task_background_run_runner_target or "").strip().lower()
        if status and status != "-":
            status_counts[status] = status_counts.get(status, 0) + 1
        if target and target != "-":
            target_counts[target] = target_counts.get(target, 0) + 1
        queue_depth += int(card.background_queue_depth or 0)
        stale_count += int(card.background_queue_stale_count or 0)
    if not status_counts and not target_counts and queue_depth <= 0 and stale_count <= 0:
        return "-"
    status_order = ("queued", "dispatching", "running", "completed", "failed", "canceled", "stale")
    target_order = ("local_background", "local_tmux", "github_runner", "remote_worker")
    parts: list[str] = []
    status_parts = [f"{key}={status_counts[key]}" for key in status_order if status_counts.get(key)]
    if status_parts:
        parts.append("status " + " ".join(status_parts))
    target_parts = [f"{key}={target_counts[key]}" for key in target_order if target_counts.get(key)]
    if target_parts:
        parts.append("target " + " ".join(target_parts))
    if queue_depth > 0 or stale_count > 0:
        queue_parts = [f"depth={queue_depth}"]
        if stale_count > 0:
            queue_parts.append(f"stale={stale_count}")
        parts.append("queue " + " ".join(queue_parts))
    return " | ".join(parts) or "-"


def _background_worker_summary_text(runtime_cards: list[RuntimeCardDTO]) -> str:
    counts: dict[str, int] = {}
    for card in runtime_cards:
        token = str(card.background_worker_status or "").strip().lower()
        if not token or token == "-":
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return "-"
    order = ("running", "idle", "stopped", "error", "stale")
    parts = [f"{key}={counts[key]}" for key in order if counts.get(key)]
    for key in sorted(counts.keys()):
        if key not in order:
            parts.append(f"{key}={counts[key]}")
    return " | ".join(parts) or "-"


def resolve_task_request_for_alias(manager_state: Dict[str, Any], project_alias: str, task_short_id: str) -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    alias_token = str(project_alias or "").strip().upper()
    task_token = str(task_short_id or "").strip()
    if not alias_token or not task_token:
        return ""
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("project_alias", "")).strip().upper() != alias_token:
            continue
        resolved = task_state.resolve_task_request_id(entry, task_token)
        return resolved if task_state.get_task_record(entry, resolved) else ""
    return ""


def resolve_task_request_for_alias_route(
    *,
    control_root: Path | str,
    project_alias: str,
    task_short_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> str:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    return resolve_task_request_for_alias(manager_loaded.state, project_alias, task_short_id)


def load_dashboard_snapshot(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> DashboardSnapshotDTO:
    return load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    ).snapshot


def load_dashboard_snapshot_result(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> DashboardSnapshotLoadResult:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    snapshot_taken_at = now_iso()

    manager_loaded = _load_manager_state(paths)
    auto_state, auto_freshness = _load_json_file(paths.auto_state_file, name="auto_state")
    provider_state, provider_freshness = _load_json_file(paths.provider_capacity_file, name="provider_capacity")
    latest_intent, latest_intent_freshness, gateway_events_freshness = _load_latest_command_resolution(
        paths.latest_intent_file,
        paths.gateway_events_file,
    )
    action_audit_rows, action_audit_freshness = _load_recent_action_audit(paths.action_audit_file)

    runtime_cards = _build_runtime_cards(manager_loaded.state, provider_state, root_team_dir=paths.team_dir)
    active_rows = _build_active_task_rows(manager_loaded.state)
    attention_cards = [card for card in runtime_cards if card.status in {"blocked", "warn"}][:8]

    auto_mode = str(auto_state.get("mode", "")).strip()
    if not auto_mode:
        auto_mode = "on" if bool(auto_state.get("enabled", False)) else "off"
    offdesk_mode = "on" if bool(auto_state.get("offdesk_enabled", auto_state.get("offdesk_mode") not in {None, "", "off"})) else "off"
    state_root = runtime_core.describe_resolved_team_dir(paths.team_dir)

    summary = ControlSummaryDTO(
        auto_mode=auto_mode,
        offdesk_mode=offdesk_mode,
        state_root_mode=str(state_root.get("mode", "")).strip() or "-",
        state_root_path=str(state_root.get("path", "")).strip() or str(paths.team_dir),
        provider_capacity_summary=_provider_summary_text(provider_state),
        next_retry_at=str(provider_state.get("next_retry_at", "")).strip() or "-",
        next_retry_target=_next_retry_target_text(provider_state),
        repeat_memory_summary=_repeat_summary_text(provider_state),
        execution_brief_summary=_execution_brief_summary_text(runtime_cards),
        background_run_summary=_background_run_summary_text(runtime_cards),
        background_worker_summary=_background_worker_summary_text(runtime_cards),
        latest_intent_command=str(latest_intent.get("command", "")).strip() or "-",
        latest_intent_action=str(latest_intent.get("action", "")).strip() or "-",
        latest_intent_trace=str(latest_intent.get("trace", "")).strip() or "-",
        latest_intent_focus=operator_summary.latest_intent_focus(
            str(latest_intent.get("action", "")).strip(),
            str(latest_intent.get("trace", "")).strip(),
        ),
        active_runtime_count=len(runtime_cards),
        attention_runtime_count=len(attention_cards),
        snapshot_taken_at=snapshot_taken_at,
    )

    return DashboardSnapshotLoadResult(
        snapshot=DashboardSnapshotDTO(
            control_root=str(paths.control_root),
            team_dir=str(paths.team_dir),
            manager_state_file=str(paths.manager_state_file),
            snapshot_taken_at=snapshot_taken_at,
            source_files=[
                manager_loaded.freshness,
                auto_freshness,
                provider_freshness,
                latest_intent_freshness,
                *([gateway_events_freshness] if gateway_events_freshness is not None else []),
                action_audit_freshness,
            ],
            control_summary=summary,
            runtime_cards=runtime_cards,
            attention_runtime_cards=attention_cards,
            active_task_rows=active_rows,
            recent_action_audit_rows=action_audit_rows,
        ),
        manager_state=manager_loaded.state,
        provider_state=provider_state,
    )


def load_task_detail(
    *,
    control_root: Path | str,
    request_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Optional[TaskDetailDTO]:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    return _build_task_detail(manager_loaded.state, request_id, root_team_dir=paths.team_dir)


def task_detail_from_state(manager_state: Dict[str, Any], request_id: str) -> Optional[TaskDetailDTO]:
    return _build_task_detail(manager_state, request_id)


def load_dashboard_task_page(
    *,
    control_root: Path | str,
    request_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, Optional[TaskDetailDTO]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    return loaded.snapshot, _build_task_detail(
        loaded.manager_state,
        request_id,
        root_team_dir=Path(loaded.snapshot.team_dir),
    )


def load_runtime_detail(
    *,
    control_root: Path | str,
    project_alias: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Optional[RuntimeDetailDTO]:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    provider_state, _provider_freshness = _load_json_file(paths.provider_capacity_file, name="provider_capacity")
    return _build_runtime_detail(manager_loaded.state, provider_state, project_alias, root_team_dir=paths.team_dir)


def load_dashboard_runtime_page(
    *,
    control_root: Path | str,
    project_alias: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, Optional[RuntimeDetailDTO]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    return loaded.snapshot, _build_runtime_detail(
        loaded.manager_state,
        loaded.provider_state,
        project_alias,
        root_team_dir=loaded.snapshot.team_dir,
    )


def load_dashboard_runtime_details(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, List[RuntimeDetailDTO], Dict[str, Any]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    details: List[RuntimeDetailDTO] = []
    for card in loaded.snapshot.runtime_cards:
        detail = _build_runtime_detail(
            loaded.manager_state,
            loaded.provider_state,
            card.project_alias,
            root_team_dir=loaded.snapshot.team_dir,
        )
        if detail is not None:
            details.append(detail)
    return loaded.snapshot, details, loaded.manager_state


def load_dashboard_recovery_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, RecoverySummaryDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    summary_state, freshness = _load_json_file(_recovery_summary_path(paths.team_dir), name="nightly_summary")
    return loaded.snapshot, _build_recovery_summary(summary_state, freshness)


def load_dashboard_action_audit_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    limit: int = 50,
) -> Tuple[DashboardSnapshotDTO, ActionAuditPageDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    rows, freshness = _load_recent_action_audit(paths.action_audit_file, limit=max(1, int(limit)))
    return loaded.snapshot, ActionAuditPageDTO(
        exists=bool(freshness.exists),
        audit_path=freshness.path,
        updated_at=freshness.updated_at,
        stale=bool(freshness.stale),
        error=freshness.error,
        total_rows=len(rows),
        status_summary=_action_audit_status_summary(rows),
        rows=rows,
    )


def load_dashboard_history_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    query: str = "",
    project_filter: str = "",
    since: str = "",
    scope: str = "all",
    limit: int = 20,
) -> Tuple[DashboardSnapshotDTO, HistorySearchPageDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    since_label = str(since or "").strip()
    options = history_search.HistorySearchOptions(
        query=" ".join(str(query or "").strip().split()),
        project_filter=str(project_filter or "").strip(),
        since_seconds=history_search._parse_since_seconds(since_label) if since_label else 0,
        since_label=since_label,
        limit=max(1, min(100, int(limit or 20))),
        scope=str(scope or "all").strip().lower() or "all",
    )
    rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=options,
    )
    return loaded.snapshot, HistorySearchPageDTO(
        query=options.query,
        project_filter=options.project_filter,
        since_label=options.since_label,
        scope=options.scope,
        limit=options.limit,
        total_rows=len(rows),
        rows=[
            HistorySearchRowDTO(
                at=row.at,
                scope=row.scope,
                source=row.source,
                project_alias=row.project_alias,
                project_key=row.project_key,
                request_id=row.request_id,
                task_short_id=row.task_short_id,
                task_title=row.task_title,
                action=row.action,
                intent_action=row.intent_action,
                reason_code=row.reason_code,
                phase=row.phase,
                status=row.status,
                summary=row.summary,
                detail=row.detail,
                followup_hint=row.followup_hint,
                raw_ref=row.raw_ref,
            )
            for row in rows
        ],
    )
