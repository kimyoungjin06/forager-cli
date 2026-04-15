#!/usr/bin/env python3
"""Read-only dashboard DTO assembly for the Control Plane board."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_runtime_core as runtime_core
import aoe_tg_operator_summary as operator_summary
import aoe_tg_history_search as history_search
import aoe_tg_chat_aliases as chat_aliases
import aoe_tg_chat_state as chat_state
import aoe_tg_room_handlers as room_handlers
import aoe_tg_task_state as task_state

from control_dashboard_server_guard import build_server_guard, write_server_guard_snapshot
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
    ChatConsolePageDTO,
    ChatRoomLineDTO,
    ChatSessionDTO,
    ChatSessionPresetDTO,
    ChatTimelineEntryDTO,
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
    server_guard = build_server_guard(
        control_root=paths.control_root,
        team_dir=paths.team_dir,
        runtime_cards=runtime_cards,
    )
    server_guard_snapshot_path, server_guard_snapshot_updated_at = write_server_guard_snapshot(
        team_dir=paths.team_dir,
        snapshot_taken_at=snapshot_taken_at,
        guard=server_guard,
    )
    server_guard = replace(
        server_guard,
        snapshot_path=server_guard_snapshot_path,
        snapshot_updated_at=server_guard_snapshot_updated_at,
    )

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
        server_guard=server_guard,
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


def _chat_selected_task_summary(row: Dict[str, Any]) -> str:
    selected = row.get("selected_task_refs") if isinstance(row.get("selected_task_refs"), dict) else {}
    if not selected:
        return "-"
    parts: list[str] = []
    for key, request_id in sorted(selected.items()):
        token = str(request_id or "").strip()
        if not token:
            continue
        parts.append(f"{key}:{token}")
        if len(parts) >= 3:
            break
    return " | ".join(parts) if parts else "-"


def _chat_recent_task_summary(row: Dict[str, Any]) -> str:
    recent = row.get("recent_task_refs") if isinstance(row.get("recent_task_refs"), dict) else {}
    if not recent:
        return "-"
    parts: list[str] = []
    for key, refs in sorted(recent.items()):
        if not isinstance(refs, list):
            continue
        count = len([str(item or "").strip() for item in refs if str(item or "").strip()])
        if count <= 0:
            continue
        parts.append(f"{key}:{count}")
        if len(parts) >= 3:
            break
    return " | ".join(parts) if parts else "-"


def _chat_session_project_context(
    manager_state: Dict[str, Any],
    *,
    raw_session: Dict[str, Any],
    selected_room: str,
) -> tuple[str, str]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    selected = raw_session.get("selected_task_refs") if isinstance(raw_session.get("selected_task_refs"), dict) else {}
    recent = raw_session.get("recent_task_refs") if isinstance(raw_session.get("recent_task_refs"), dict) else {}
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        project_alias = str(entry.get("project_alias", "")).strip()
        if not project_alias:
            continue
        if key in selected or key in recent:
            return str(key).strip(), project_alias
    room_head = str(selected_room or "").strip().split("/", 1)[0]
    if room_head and room_head.lower() != room_handlers.DEFAULT_ROOM_NAME:
        room_key = ""
        for key, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("project_alias", "")).strip().upper() == room_head.upper():
                room_key = str(key).strip()
                break
        return room_key, room_head
    active_key = str(manager_state.get("active", "")).strip()
    active_entry = projects.get(active_key) if active_key and isinstance(projects.get(active_key), dict) else {}
    return active_key, str(active_entry.get("project_alias", "")).strip()


def _chat_session_recent_task_refs(row: Dict[str, Any], *, project_key: str) -> list[str]:
    key = str(project_key or "").strip()
    if not key:
        return []
    recent = row.get("recent_task_refs") if isinstance(row.get("recent_task_refs"), dict) else {}
    values = recent.get(key)
    if not isinstance(values, list):
        return []
    refs: list[str] = []
    for item in values:
        token = str(item or "").strip()
        if token and token not in refs:
            refs.append(token)
        if len(refs) >= 8:
            break
    return refs


def _build_chat_timeline_entries(
    *,
    room: str,
    room_tail: list[ChatRoomLineDTO],
    recent_chat_actions: list[ActionAuditRowDTO],
    limit: int = 20,
) -> list[ChatTimelineEntryDTO]:
    rows: list[ChatTimelineEntryDTO] = []
    for action in recent_chat_actions:
        rows.append(
            ChatTimelineEntryDTO(
                at=str(action.at or "-").strip() or "-",
                source="reply" if str(action.outcome_kind or "").strip() == "chat_send" else "session",
                headline=str(action.headline or "-").strip() or "-",
                badge=str(action.outcome_kind or action.status or "-").strip() or "-",
                body=str(action.transcript_preview or action.outcome_detail or "-").strip() or "-",
                command=str(action.source_command or "").strip(),
                next_step=str(action.next_step or "").strip(),
                room=room,
            )
        )
    for line in room_tail:
        rows.append(
            ChatTimelineEntryDTO(
                at=str(line.at or "-").strip() or "-",
                source="room",
                headline=str(line.actor or "-").strip() or "-",
                badge=str(line.kind or "-").strip() or "-",
                body=str(line.text or "-").strip() or "-",
                room=room,
            )
        )
    rows.sort(key=lambda row: (str(row.at).strip(), str(row.source).strip()), reverse=True)
    return rows[: max(1, int(limit))]


def _chat_room_presets(*, project_alias: str, selected_room: str, rooms: list[str]) -> list[str]:
    tokens: list[str] = []
    def _add(token: str) -> None:
        safe = str(token or "").strip()
        if safe and safe not in tokens:
            tokens.append(safe)
    _add(room_handlers.DEFAULT_ROOM_NAME)
    project_token = str(project_alias or "").strip()
    if project_token:
        _add(project_token)
        _add(f"{project_token}/analysis")
        _add(f"{project_token}/writing")
        _add(f"{project_token}/package")
        _add(f"{project_token}/review")
    _add(selected_room)
    for room in rooms:
        _add(room)
        if len(tokens) >= 10:
            break
    return tokens[:10]


def _chat_session_presets(*, project_alias: str, selected_room: str) -> list[ChatSessionPresetDTO]:
    alias = str(project_alias or "").strip()
    presets: list[ChatSessionPresetDTO] = [
        ChatSessionPresetDTO(
            label="Global Direct",
            room=room_handlers.DEFAULT_ROOM_NAME,
            default_mode="direct",
            pending_mode="",
            lang="ko",
            report_level="short",
            note="short direct replies on the global rail",
        )
    ]
    if alias:
        presets.extend(
            [
                ChatSessionPresetDTO(
                    label="Analysis Rail",
                    room=f"{alias}/analysis",
                    default_mode="dispatch",
                    pending_mode="",
                    lang="ko",
                    report_level="long",
                    note="analysis findings, evidence, caveats",
                ),
                ChatSessionPresetDTO(
                    label="Writing Rail",
                    room=f"{alias}/writing",
                    default_mode="dispatch",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note="drafts, handoff, quality gate",
                ),
                ChatSessionPresetDTO(
                    label="Package Rail",
                    room=f"{alias}/package",
                    default_mode="dispatch",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note="artifact verification, apply, syncback",
                ),
                ChatSessionPresetDTO(
                    label="Review Rail",
                    room=f"{alias}/review",
                    default_mode="direct",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note="operator review and escalation",
                ),
            ]
        )
    current = str(selected_room or "").strip()
    if current and current not in {preset.room for preset in presets}:
        presets.append(
            ChatSessionPresetDTO(
                label="Current Room",
                room=current,
                default_mode="dispatch",
                pending_mode="",
                lang="ko",
                report_level="normal",
                note="keep the current room selection",
            )
        )
    return presets[:6]


def _load_recent_chat_action_rows(paths: ControlPaths, *, chat_id: str, limit: int = 8) -> list[ActionAuditRowDTO]:
    rows: list[ActionAuditRowDTO] = []
    raw_rows: list[dict[str, Any]] = []
    if paths.action_audit_file.exists():
        try:
            with paths.action_audit_file.open("r", encoding="utf-8") as handle:
                for line in handle:
                    token = str(line or "").strip()
                    if not token:
                        continue
                    try:
                        parsed = json.loads(token)
                    except Exception:
                        continue
                    if isinstance(parsed, dict):
                        raw_rows.append(parsed)
        except Exception:
            raw_rows = []
    for raw in reversed(raw_rows):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("outcome_kind", "")).strip() not in {"chat_send", "chat_session_update", "chat_session_select_task"}:
            continue
        row = ActionAuditRowDTO(
            at=str(raw.get("at", "")).strip() or "-",
            headline=str(raw.get("headline", "")).strip() or "-",
            status=str(raw.get("status", "")).strip() or "unknown",
            outcome_kind=str(raw.get("outcome_kind", "")).strip() or "-",
            outcome_status=str(raw.get("outcome_status", "")).strip() or str(raw.get("status", "")).strip() or "unknown",
            outcome_reason_code=str(raw.get("outcome_reason_code", "")).strip() or "-",
            outcome_detail=str(raw.get("outcome_detail", "")).strip() or "-",
            next_step=str(raw.get("next_step", "")).strip() or "-",
            remediation=str(raw.get("remediation", "")).strip() or "-",
            link_label=str(raw.get("link_label", "")).strip() or "-",
            link_href=str(raw.get("link_href", "")).strip() or "-",
            source_command=str(raw.get("source_command", "")).strip() or "-",
            focus_badge=str(raw.get("focus_badge", "")).strip(),
            chat_id=str(raw.get("chat_id", "")).strip(),
            transcript_preview=str(raw.get("transcript_preview", "")).strip(),
        )
        if chat_id and row.chat_id and row.chat_id != chat_id:
            continue
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def load_dashboard_chat_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    selected_chat_id: str = "",
) -> tuple[DashboardSnapshotDTO, ChatConsolePageDTO]:
    snapshot_result = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    aliases = chat_aliases.load_chat_aliases(paths.chat_aliases_file)
    alias_by_chat_id = {str(chat_id).strip(): str(alias).strip() for alias, chat_id in aliases.items()}
    raw_sessions = (
        snapshot_result.manager_state.get("chat_sessions")
        if isinstance(snapshot_result.manager_state.get("chat_sessions"), dict)
        else {}
    )
    sessions: list[ChatSessionDTO] = []
    for chat_id, raw in raw_sessions.items():
        chat_token = str(chat_id or "").strip()
        if not chat_token:
            continue
        row = raw if isinstance(raw, dict) else {}
        sanitized = chat_state.sanitize_chat_session_row(row) if isinstance(row, dict) else {}
        session_row = sanitized if sanitized else row
        sessions.append(
            ChatSessionDTO(
                chat_id=chat_token,
                chat_alias=alias_by_chat_id.get(chat_token, ""),
                updated_at=str(session_row.get("updated_at", "")).strip() or "-",
                default_mode=str(session_row.get("default_mode", "")).strip() or "off",
                pending_mode=str(session_row.get("pending_mode", "")).strip() or "none",
                lang=str(session_row.get("lang", "")).strip() or chat_state.DEFAULT_UI_LANG,
                report_level=str(session_row.get("report_level", "")).strip() or chat_state.DEFAULT_REPORT_LEVEL,
                room=str(session_row.get("room", "")).strip() or room_handlers.DEFAULT_ROOM_NAME,
                selected_task_summary=_chat_selected_task_summary(session_row),
                recent_task_summary=_chat_recent_task_summary(session_row),
            )
        )
    sessions.sort(key=lambda row: (row.updated_at, row.chat_id), reverse=True)

    selected_token = str(selected_chat_id or "").strip()
    selected_session = next((row for row in sessions if row.chat_id == selected_token), None)
    if selected_session is None and sessions:
        selected_session = sessions[0]
    selected_token = selected_session.chat_id if selected_session is not None else selected_token

    sessions = [
        ChatSessionDTO(
            chat_id=row.chat_id,
            chat_alias=row.chat_alias,
            updated_at=row.updated_at,
            default_mode=row.default_mode,
            pending_mode=row.pending_mode,
            lang=row.lang,
            report_level=row.report_level,
            room=row.room,
            selected_task_summary=row.selected_task_summary,
            recent_task_summary=row.recent_task_summary,
            is_selected=(row.chat_id == selected_token),
        )
        for row in sessions
    ]

    selected_room = selected_session.room if selected_session is not None else room_handlers.DEFAULT_ROOM_NAME
    selected_session_row = raw_sessions.get(selected_token) if isinstance(raw_sessions.get(selected_token), dict) else {}
    room_tail = [
        ChatRoomLineDTO(
            at=str(row.get("ts", "")).strip() or "-",
            actor=str(row.get("actor", "")).strip() or "-",
            kind=str(row.get("kind", "")).strip() or "-",
            text=" ".join(str(row.get("text", "")).strip().split()) or "-",
        )
        for row in room_handlers.tail_room_events(team_dir=paths.team_dir, room=selected_room, limit=20)
        if isinstance(row, dict)
    ]
    rooms = [name for name, _mt in room_handlers.list_rooms(team_dir=paths.team_dir, limit=24)]
    if selected_room and selected_room not in rooms:
        rooms.insert(0, selected_room)
    selected_project_key, selected_project_alias = _chat_session_project_context(
        snapshot_result.manager_state,
        raw_session=selected_session_row if isinstance(selected_session_row, dict) else {},
        selected_room=selected_room,
    )
    selected_task_ref = (
        chat_state.get_chat_selected_task_ref(snapshot_result.manager_state, selected_token, selected_project_key)
        if selected_project_key
        else ""
    )
    selected_recent_task_refs = (
        chat_state.get_chat_recent_task_refs(snapshot_result.manager_state, selected_token, selected_project_key)
        if selected_project_key
        else _chat_session_recent_task_refs(
            selected_session_row if isinstance(selected_session_row, dict) else {},
            project_key=selected_project_key,
        )
    )
    recent_chat_actions = _load_recent_chat_action_rows(paths, chat_id=selected_token, limit=8)
    timeline_entries = _build_chat_timeline_entries(
        room=selected_room,
        room_tail=room_tail,
        recent_chat_actions=recent_chat_actions,
        limit=20,
    )

    return snapshot_result.snapshot, ChatConsolePageDTO(
        selected_chat_id=selected_session.chat_id if selected_session is not None else selected_token,
        selected_chat_alias=selected_session.chat_alias if selected_session is not None else alias_by_chat_id.get(selected_token, ""),
        selected_room=selected_room,
        selected_project_key=selected_project_key,
        selected_project_alias=selected_project_alias,
        selected_task_ref=selected_task_ref,
        selected_default_mode=selected_session.default_mode if selected_session is not None else "off",
        selected_pending_mode=selected_session.pending_mode if selected_session is not None else "none",
        selected_lang=selected_session.lang if selected_session is not None else chat_state.DEFAULT_UI_LANG,
        selected_report_level=selected_session.report_level if selected_session is not None else chat_state.DEFAULT_REPORT_LEVEL,
        rooms=rooms,
        room_presets=_chat_room_presets(project_alias=selected_project_alias, selected_room=selected_room, rooms=rooms),
        session_presets=_chat_session_presets(project_alias=selected_project_alias, selected_room=selected_room),
        selected_recent_task_refs=selected_recent_task_refs,
        sessions=sessions,
        room_tail=room_tail,
        timeline_entries=timeline_entries,
        recent_chat_actions=recent_chat_actions,
        send_mode_options={
            "raw": "As Typed",
            "direct": "One-shot Direct",
            "dispatch": "One-shot Dispatch",
            "room_post": "Room Post",
            "room_use": "Use Room",
        },
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
    return loaded.snapshot, _build_recovery_summary(
        summary_state,
        freshness,
        manager_state=loaded.manager_state,
        root_team_dir=paths.team_dir,
    )


def load_dashboard_action_audit_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    focus: str = "",
    limit: int = 50,
) -> Tuple[DashboardSnapshotDTO, ActionAuditPageDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    rows, freshness = _load_recent_action_audit(paths.action_audit_file, limit=max(1, int(limit)))
    focus_filter = str(focus or "").strip().lower() or "all"
    if focus_filter not in {"all", "auto-route", "judge", "retry"}:
        focus_filter = "all"
    filtered_rows = rows
    if focus_filter != "all":
        filtered_rows = [row for row in rows if str(getattr(row, "focus_badge", "")).strip() == focus_filter]
    focus_counts: Dict[str, int] = {}
    for row in rows:
        badge = str(getattr(row, "focus_badge", "")).strip()
        if not badge:
            continue
        focus_counts[badge] = int(focus_counts.get(badge, 0) or 0) + 1
    focus_summary = " | ".join(
        f"{label}={focus_counts[label]}" for label in sorted(focus_counts.keys())
    ) or "-"
    return loaded.snapshot, ActionAuditPageDTO(
        exists=bool(freshness.exists),
        audit_path=freshness.path,
        updated_at=freshness.updated_at,
        stale=bool(freshness.stale),
        error=freshness.error,
        limit=max(1, int(limit)),
        total_rows=len(filtered_rows),
        status_summary=_action_audit_status_summary(filtered_rows),
        focus_summary=focus_summary,
        focus_filter=focus_filter,
        focus_counts={"all": len(rows), **focus_counts},
        rows=filtered_rows,
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
