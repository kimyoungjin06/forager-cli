#!/usr/bin/env python3
"""Read-only dashboard DTO assembly for the Control Plane board."""

from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_runtime_core as runtime_core
import aoe_tg_action_audit as action_audit
from aoe_tg_artifact_backend import load_jsonl_rows
import aoe_tg_operator_summary as operator_summary
import aoe_tg_operator_preferences as operator_preferences
import aoe_tg_history_search as history_search
import aoe_tg_chat_aliases as chat_aliases
import aoe_tg_chat_state as chat_state
import aoe_tg_room_handlers as room_handlers
import aoe_tg_task_state as task_state
import aoe_tg_task_view as task_view

from control_dashboard_server_guard import build_server_guard, server_guard_pressure_policy, write_server_guard_snapshot
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
    _infer_action_audit_project_alias,
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
    ChatRoomOptionDTO,
    ChatSessionDTO,
    ChatSessionPresetDTO,
    ChatTimelineEntryDTO,
    ControlSummaryDTO,
    DashboardSnapshotDTO,
    DashboardSnapshotLoadResult,
    HistorySearchPageDTO,
    HistorySearchRowDTO,
    OperatorPreferenceArtifactSummaryDTO,
    OperatorPreferenceCandidateDTO,
    OperatorPreferenceMemoryScopeSummaryDTO,
    OperatorPreferenceProjectSummaryDTO,
    OperatorPreferenceRuleDTO,
    OperatorPreferencesPageDTO,
    RecoverySummaryDTO,
    RuntimeCardDTO,
    RuntimeDetailDTO,
    ServerGuardThreadDTO,
    ServerGuardActionGroupDTO,
    TaskDetailDTO,
)


_ROOM_TEXT_SHORTCUT_VALUES = ["decision", "checkpoint", "followup"]


def _preference_count_summary(rules: List[Dict[str, Any]]) -> str:
    enabled = 0
    auto = 0
    confirm = 0
    manual_only = 0
    disabled = 0
    for rule in rules:
        if bool(rule.get("enabled", False)):
            enabled += 1
            prompt_mode = str(rule.get("prompt_mode", "")).strip().lower()
            if prompt_mode == "auto":
                auto += 1
            elif prompt_mode == "manual_only":
                manual_only += 1
            else:
                confirm += 1
        else:
            disabled += 1
    return (
        f"rules={len(rules)}"
        f" | enabled={enabled}"
        f" | auto={auto}"
        f" | confirm={confirm}"
        f" | manual={manual_only}"
        f" | disabled={disabled}"
    )


def _candidate_count_summary(candidates: List[Dict[str, Any]]) -> str:
    promoted_ready = 0
    for candidate in candidates:
        if int(candidate.get("occurrence_count", 0) or 0) >= operator_preferences.PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD:
            promoted_ready += 1
    return (
        f"candidates={len(candidates)}"
        f" | ready={promoted_ready}"
        f" | threshold={operator_preferences.PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD}"
    )


def _preference_rule_sort_key(row: Dict[str, Any]) -> tuple[object, ...]:
    return (
        str(row.get("artifact_kind", "")).strip(),
        {"project": 0, "artifact_kind": 1, "user_global": 2, "session": 3}.get(str(row.get("scope", "")).strip(), 9),
        str(row.get("scope_ref", "")).strip(),
        str(row.get("key", "")).strip(),
    )


def _preference_candidate_sort_key(row: Dict[str, Any]) -> tuple[object, ...]:
    return (
        str(row.get("artifact_kind", "")).strip(),
        -int(row.get("occurrence_count", 0) or 0),
        str(row.get("key", "")).strip(),
        str(row.get("project_ref", "")).strip(),
    )


def _preference_project_alias(project_key: str, entry: Dict[str, Any]) -> str:
    return str(entry.get("project_alias", "")).strip().upper() or str(project_key or "").strip() or "-"


def _preference_project_label(project_key: str, entry: Dict[str, Any]) -> str:
    return (
        str(entry.get("display_name", "")).strip()
        or str(entry.get("name", "")).strip()
        or _preference_project_alias(project_key, entry)
    )


def _preference_project_filter_value(project_key: str, entry: Dict[str, Any]) -> str:
    return _preference_project_alias(project_key, entry) or str(project_key or "").strip()


def _preference_project_matches_filter(project_key: str, entry: Dict[str, Any], project_filter: str) -> bool:
    token = " ".join(str(project_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    candidates = {
        str(project_key or "").strip().lower(),
        str(entry.get("project_alias", "")).strip().lower(),
        str(entry.get("display_name", "")).strip().lower(),
        str(entry.get("name", "")).strip().lower(),
    }
    return token in {item for item in candidates if item}


def _audit_project_matches_row(row: ActionAuditRowDTO, project_filter: str) -> bool:
    token = " ".join(str(project_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    return token == str(row.project_alias or "").strip().lower()


def _audit_artifact_matches_row(row: ActionAuditRowDTO, artifact_filter: str) -> bool:
    token = " ".join(str(artifact_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    return token == str(row.preference_artifact_kind or "").strip().lower()


def _audit_memory_scope_labels(row: ActionAuditRowDTO) -> List[str]:
    prefix = "preference_memory_scope="
    scope_summary = str(row.preference_memory_scope_summary or "").strip()
    if not scope_summary.lower().startswith(prefix):
        return []
    return [
        str(item).strip()
        for item in scope_summary[len(prefix):].split("||")
        if str(item).strip() and str(item).strip() not in {"-"}
    ]


def _audit_memory_scope_kinds(row: ActionAuditRowDTO) -> List[str]:
    kinds: List[str] = []
    for label in _audit_memory_scope_labels(row):
        kind = str(label).split(":", 1)[0].strip().lower()
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def _audit_memory_scope_matches_row(row: ActionAuditRowDTO, memory_scope_filter: str) -> bool:
    token = " ".join(str(memory_scope_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    labels = [label.lower() for label in _audit_memory_scope_labels(row)]
    if any(label == token or label.startswith(f"{token}:") for label in labels):
        return True
    return token in _audit_memory_scope_kinds(row)


def _audit_preference_refresh_diff_kinds(row: ActionAuditRowDTO) -> List[str]:
    summary = str(row.preference_refresh_diff_summary or "").strip()
    if not summary or summary == "-":
        return []
    prefix = "preference_refresh_diff="
    body = summary[len(prefix):] if summary.lower().startswith(prefix) else summary
    kinds: List[str] = []
    for chunk in body.split(";"):
        key = str(chunk).split("=", 1)[0].strip().lower()
        if key in {"applied_added", "applied_removed", "candidates_added", "candidates_removed"} and key not in kinds:
            kinds.append(key)
    return kinds


def _audit_chat_event_kinds(row: ActionAuditRowDTO) -> List[str]:
    kinds: List[str] = []
    if str(row.chat_reply_summary or "").strip() not in {"", "-"}:
        kinds.append("reply")
    if str(row.chat_room_change_summary or "").strip() not in {"", "-"}:
        kinds.append("room_change")
    if str(row.outcome_kind or "").strip().lower() in {"chat_session_update", "chat_session_select_task"}:
        kinds.append("session")
    return kinds


def _audit_chat_mode(row: ActionAuditRowDTO) -> str:
    command = str(row.source_command or "").strip().lower()
    if command.startswith("/direct "):
        return "direct"
    if command.startswith("/dispatch "):
        return "dispatch"
    if command.startswith("/room post "):
        return "room_post"
    if command.startswith("/room use "):
        return "room_use"
    if str(row.chat_id or "").strip() and (
        str(row.outcome_kind or "").strip().lower() == "chat_send"
        or "chat send" in str(row.headline or "").strip().lower()
    ):
        return "raw"
    return ""


def _preference_file_presence_summary(files: List[FileFreshnessDTO]) -> str:
    if not files:
        return "-"
    present = sum(1 for row in files if bool(row.exists))
    stale = sum(1 for row in files if bool(row.stale))
    missing = max(0, len(files) - present)
    parts = [f"present={present}/{len(files)}"]
    if missing > 0:
        parts.append(f"missing={missing}")
    if stale > 0:
        parts.append(f"stale={stale}")
    return " | ".join(parts)


def _preferences_filter_path(*, project_filter: str = "", artifact_filter: str = "", scope_filter: str = "") -> str:
    params: list[str] = []
    if str(project_filter or "").strip():
        params.append(f"project={quote(str(project_filter).strip(), safe='')}")
    if str(artifact_filter or "").strip():
        params.append(f"artifact={quote(str(artifact_filter).strip(), safe='')}")
    if str(scope_filter or "").strip():
        params.append(f"scope={quote(str(scope_filter).strip(), safe='')}")
    if not params:
        return "/control/preferences"
    return "/control/preferences?" + "&".join(params)


def _preference_prompt_mode_summary(
    rules: List[OperatorPreferenceRuleDTO],
) -> str:
    counts = {"auto": 0, "confirm": 0, "manual_only": 0, "disabled": 0}
    for row in rules:
        if not row.enabled:
            counts["disabled"] += 1
            continue
        prompt_mode = str(row.prompt_mode or "").strip().lower()
        if prompt_mode == "auto":
            counts["auto"] += 1
        elif prompt_mode == "manual_only":
            counts["manual_only"] += 1
        else:
            counts["confirm"] += 1
    return (
        f"auto={counts['auto']}"
        f" | confirm={counts['confirm']}"
        f" | manual={counts['manual_only']}"
        f" | disabled={counts['disabled']}"
    )


def _preference_memory_scope_label(scope: str) -> str:
    token = str(scope or "").strip().lower()
    if token == "session":
        return "this task"
    if token == "project":
        return "this project"
    if token == "artifact_kind":
        return "this artifact kind"
    if token == "user_global":
        return "all projects"
    return token or "-"


def _preference_memory_scope_matches_filter(scope: str, scope_filter: str) -> bool:
    token = " ".join(str(scope_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    return str(scope or "").strip().lower() == token


def _preference_memory_scope_rows(
    rules: List[OperatorPreferenceRuleDTO],
    candidates: List[OperatorPreferenceCandidateDTO],
    *,
    project_filter: str = "",
    artifact_filter: str = "",
    scope_filter: str = "",
) -> List[OperatorPreferenceMemoryScopeSummaryDTO]:
    scope_order = ("session", "project", "artifact_kind", "user_global")
    discovered_scopes = {
        str(row.scope or "").strip().lower()
        for row in rules
        if str(row.scope or "").strip()
    } | {
        str(row.expected_scope or "").strip().lower()
        for row in candidates
        if str(row.expected_scope or "").strip()
    }
    ordered_scopes = [
        *scope_order,
        *sorted(scope for scope in discovered_scopes if scope not in set(scope_order)),
    ]
    rows: List[OperatorPreferenceMemoryScopeSummaryDTO] = []
    for scope in ordered_scopes:
        matching = [row for row in rules if str(row.scope or "").strip().lower() == scope]
        matching_candidates = [
            row
            for row in candidates
            if str(row.expected_scope or "").strip().lower() == scope
        ]
        query_tokens = [f"memory_scope:{scope}"]
        if str(artifact_filter or "").strip():
            query_tokens.append(f"artifact_kind:{str(artifact_filter).strip().lower()}")
        query = quote(" ".join(query_tokens), safe="")
        history_href = f"/control/history?q={query}"
        if str(project_filter or "").strip():
            history_href += f"&project={quote(str(project_filter).strip(), safe='')}"
        history_href += "&scope=dashboard&limit=20"
        rows.append(
            OperatorPreferenceMemoryScopeSummaryDTO(
                scope=scope,
                scope_label=_preference_memory_scope_label(scope),
                filter_value=scope,
                filter_href=_preferences_filter_path(
                    project_filter=project_filter,
                    artifact_filter=artifact_filter,
                    scope_filter=scope,
                ),
                audit_href=(
                    "/control/audit?focus=preferences"
                    + (f"&project={quote(str(project_filter).strip(), safe='')}" if str(project_filter or "").strip() else "")
                    + f"&q={query}&limit=50"
                ),
                history_href=history_href,
                is_selected=bool(scope_filter) and _preference_memory_scope_matches_filter(scope, scope_filter),
                rule_count=len(matching),
                candidate_count=len(matching_candidates),
                ready_candidate_count=sum(
                    1
                    for row in matching_candidates
                    if int(row.hits or 0) >= operator_preferences.PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD
                ),
                enabled_count=sum(1 for row in matching if row.enabled),
                disabled_count=sum(1 for row in matching if not row.enabled),
                prompt_mode_summary=_preference_prompt_mode_summary(matching),
                artifact_summary=_compact_counter_summary(
                    [row.artifact_kind for row in [*matching, *matching_candidates]]
                ),
                project_summary=_preference_project_list_summary(
                    [row.project_alias for row in [*matching, *matching_candidates]]
                ),
            )
        )
    return rows


def _preference_project_list_summary(project_aliases: List[str]) -> str:
    values = sorted({str(item or "").strip() for item in project_aliases if str(item or "").strip()})
    return ", ".join(values) if values else "-"


def _preference_artifact_matches_filter(artifact_kind: str, artifact_filter: str) -> bool:
    token = " ".join(str(artifact_filter or "").strip().split()).lower()
    if not token or token == "all":
        return True
    return str(artifact_kind or "").strip().lower() == token


def _audit_query_matches_row(row: ActionAuditRowDTO, query_filter: str) -> bool:
    tokens = [token for token in str(query_filter or "").strip().lower().split() if token]
    if not tokens:
        return True
    memory_scope_alias = ""
    artifact_alias = ""
    refresh_diff_alias = ""
    chat_event_alias = ""
    chat_mode_alias = ""
    labels = _audit_memory_scope_labels(row)
    if labels:
        memory_scope_alias = " | ".join(f"memory_scope:{label}" for label in labels)
    preference_artifact_kind = str(row.preference_artifact_kind or "").strip().lower()
    if preference_artifact_kind and preference_artifact_kind != "-":
        artifact_alias = f"artifact_kind:{preference_artifact_kind}"
    refresh_diff_kinds = _audit_preference_refresh_diff_kinds(row)
    if refresh_diff_kinds:
        refresh_diff_alias = " | ".join(
            [*(f"refresh_diff:{kind}" for kind in refresh_diff_kinds), *(f"diff:{kind}" for kind in refresh_diff_kinds)]
        )
    chat_event_kinds = _audit_chat_event_kinds(row)
    if chat_event_kinds:
        chat_event_alias = " | ".join(f"chat_event:{kind}" for kind in chat_event_kinds)
    chat_mode = _audit_chat_mode(row)
    if chat_mode:
        chat_mode_alias = f"chat_mode:{chat_mode}"
    haystack = " | ".join(
        value
        for value in (
            row.headline,
            row.headline_summary,
            row.outcome_detail,
            row.outcome_reason_code,
            row.next_step,
            row.remediation,
            row.source_command,
            row.chat_reply_summary,
            row.chat_room_change_summary,
            row.applied_preferences_summary,
            row.preference_candidate_summary,
            row.preference_candidate_scope_summary,
            row.preference_decision_summary,
            row.preference_artifact_kind,
            artifact_alias,
            row.preference_memory_scope_summary,
            row.preference_refresh_diff_summary,
            memory_scope_alias,
            refresh_diff_alias,
            chat_event_alias,
            chat_mode_alias,
        )
        if str(value or "").strip()
    ).lower()
    return all(token in haystack for token in tokens)


def _compact_counter_summary(values: List[str], *, empty_label: str = "-") -> str:
    counts: Dict[str, int] = {}
    for item in values:
        token = str(item or "").strip() or empty_label
        counts[token] = int(counts.get(token, 0) or 0) + 1
    if not counts:
        return "-"
    return " | ".join(f"{token}={counts[token]}" for token in sorted(counts.keys()))


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
    server_guard_latest_action_summary, server_guard_latest_action_path = _latest_server_guard_action(action_audit_rows)
    server_guard_latest_result_summary, server_guard_latest_result_path = _latest_server_guard_result(action_audit_rows)
    server_guard_preview_actions = [row for row in server_guard.recommended_actions if str(row.path or "").strip()]
    server_guard_preview_groups = _build_server_guard_preview_groups(
        server_guard_preview_actions,
        reason_summary=server_guard.reason_summary,
        note=server_guard.note,
    )
    server_guard_threads = _build_server_guard_thread_cards(action_audit_rows, limit=2)

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
        server_guard_latest_action_summary=server_guard_latest_action_summary,
        server_guard_latest_action_path=server_guard_latest_action_path,
        server_guard_latest_result_summary=server_guard_latest_result_summary,
        server_guard_latest_result_path=server_guard_latest_result_path,
        server_guard_preview_actions=server_guard_preview_actions,
        server_guard_preview_groups=server_guard_preview_groups,
        server_guard_threads=server_guard_threads,
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


def _chat_selected_task_ref(
    manager_state: Dict[str, Any],
    *,
    raw_session: Dict[str, Any],
    chat_id: str,
    project_key: str,
) -> str:
    key = str(project_key or "").strip()
    if key:
        selected = chat_state.get_chat_selected_task_ref(manager_state, chat_id, key)
        if selected:
            return selected
    selected_map = raw_session.get("selected_task_refs") if isinstance(raw_session.get("selected_task_refs"), dict) else {}
    if not selected_map:
        return ""
    active_key = str(manager_state.get("active", "")).strip()
    if key and active_key and key == active_key:
        fallback = str(selected_map.get("active", "")).strip()
        if fallback:
            return fallback
    if len(selected_map) == 1:
        only_ref = next(iter(selected_map.values()), "")
        return str(only_ref or "").strip()
    return ""


def _chat_selected_task_record(
    manager_state: Dict[str, Any],
    *,
    project_key: str,
    task_ref: str,
) -> Dict[str, Any]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(str(project_key or "").strip())
    if not isinstance(entry, dict):
        return {}
    resolved_request_id = task_state.resolve_task_request_id(entry, task_ref)
    task = task_state.get_task_record(entry, resolved_request_id) if resolved_request_id else None
    return task if isinstance(task, dict) else {}


def _build_chat_timeline_entries(
    *,
    room: str,
    room_tail: list[ChatRoomLineDTO],
    recent_chat_actions: list[ActionAuditRowDTO],
    limit: int = 20,
) -> list[ChatTimelineEntryDTO]:
    rows: list[ChatTimelineEntryDTO] = []
    pressure_preview_kinds = {
        "codex_process_pressure_preview",
        "python_process_pressure_preview",
        "tmux_process_pressure_preview",
        "process_pressure_preview",
    }
    consumed: set[int] = set()
    for index, action in enumerate(recent_chat_actions):
        if index in consumed:
            continue
        outcome_kind = str(action.outcome_kind or "").strip()
        focus_badge = str(action.focus_badge or "").strip()
        if outcome_kind == "chat_session_update" and focus_badge == "server-guard":
            matched_index = None
            matched_preview = None
            for older_index in range(index + 1, len(recent_chat_actions)):
                older = recent_chat_actions[older_index]
                if older_index in consumed:
                    continue
                if str(older.focus_badge or "").strip() != "server-guard":
                    continue
                if str(older.outcome_kind or "").strip() not in pressure_preview_kinds:
                    continue
                if action.chat_id and older.chat_id and action.chat_id != older.chat_id:
                    continue
                matched_index = older_index
                matched_preview = older
                break
            if matched_preview is not None and matched_index is not None:
                rows.append(
                    ChatTimelineEntryDTO(
                        at=str(action.at or "-").strip() or "-",
                        source="thread",
                        headline="Server Guard Preset Thread",
                        badge="server-guard",
                        body=(
                            f"{str(matched_preview.headline or '-').strip() or '-'}"
                            f" -> {str(action.headline or '-').strip() or '-'}"
                        ),
                        command=str(action.source_command or "").strip(),
                        next_step=str(action.next_step or "").strip(),
                        room=room,
                        detail_href=(
                            f"/control/audit?focus=server-guard&chat={action.chat_id}&limit=20"
                            if str(action.chat_id or "").strip()
                            else "/control/audit?focus=server-guard&limit=20"
                        ),
                        detail_label="Open Thread Detail",
                    )
                )
                consumed.add(index)
                consumed.add(matched_index)
                continue
        rows.append(
            ChatTimelineEntryDTO(
                at=str(action.at or "-").strip() or "-",
                source="reply" if outcome_kind == "chat_send" else "session",
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


def _build_server_guard_thread_card(recent_chat_actions: list[ActionAuditRowDTO]) -> ServerGuardThreadDTO:
    cards = _build_server_guard_thread_cards(recent_chat_actions, limit=1)
    return cards[0] if cards else ServerGuardThreadDTO()


def _build_server_guard_thread_cards(
    recent_chat_actions: list[ActionAuditRowDTO],
    *,
    limit: int = 3,
) -> list[ServerGuardThreadDTO]:
    pressure_preview_kinds = {
        "codex_process_pressure_preview",
        "python_process_pressure_preview",
        "tmux_process_pressure_preview",
        "process_pressure_preview",
    }
    pressure_labels = {
        "codex_process_pressure_preview": "Codex Pressure",
        "python_process_pressure_preview": "Python Pressure",
        "tmux_process_pressure_preview": "Tmux Pressure",
        "process_pressure_preview": "Process Pressure",
    }
    pressure_keys = {
        "codex_process_pressure_preview": "codex",
        "python_process_pressure_preview": "python",
        "tmux_process_pressure_preview": "tmux",
        "process_pressure_preview": "process",
    }
    cards: list[ServerGuardThreadDTO] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for index, action in enumerate(recent_chat_actions):
        if str(action.outcome_kind or "").strip() != "chat_session_update":
            continue
        if str(action.focus_badge or "").strip() != "server-guard":
            continue
        for older in recent_chat_actions[index + 1 :]:
            if str(older.focus_badge or "").strip() != "server-guard":
                continue
            if str(older.outcome_kind or "").strip() not in pressure_preview_kinds:
                continue
            if action.chat_id and older.chat_id and action.chat_id != older.chat_id:
                continue
            detail_href = (
                f"/control/audit?focus=server-guard&chat={action.chat_id}&limit=20"
                if str(action.chat_id or "").strip()
                else "/control/audit?focus=server-guard&limit=20"
            )
            pair_key = (
                str(action.chat_id or "").strip(),
                str(older.headline or "").strip(),
                str(action.headline or "").strip(),
            )
            if pair_key in seen_pairs:
                break
            seen_pairs.add(pair_key)
            pressure_kind_key = pressure_keys.get(str(older.outcome_kind or "").strip(), "")
            pressure_policy = server_guard_pressure_policy(pressure_kind_key)
            cards.append(
                ServerGuardThreadDTO(
                    exists=True,
                    preview_headline=str(older.headline or "-").strip() or "-",
                    apply_headline=str(action.headline or "-").strip() or "-",
                    subagent_evidence_summary=str(action.subagent_evidence_summary or "-").strip() or "-",
                    subagent_artifact_path=str(action.subagent_artifact_path or "-").strip() or "-",
                    subagent_gate_summary=str(action.subagent_gate_summary or "-").strip() or "-",
                    pressure_kind_key=pressure_kind_key,
                    pressure_kind_label=pressure_labels.get(str(older.outcome_kind or "").strip(), ""),
                    action_sentence=str(pressure_policy.get("action_sentence", "")).strip(),
                    priority_link_label=str(pressure_policy.get("priority_link_label", "")).strip(),
                    priority_link_note=str(pressure_policy.get("priority_link_note", "")).strip(),
                    preset_diff_summary=str(action.chat_preset_diff_summary or "-").strip() or "-",
                    chat_id=str(action.chat_id or "").strip(),
                    at=str(action.at or "-").strip() or "-",
                    command=str(action.source_command or "-").strip() or "-",
                    next_step=str(action.next_step or "-").strip() or "-",
                    detail_href=detail_href,
                    detail_label="Open Thread Detail",
                    chat_href=(
                        f"/control/chat?chat={action.chat_id}"
                        if str(action.chat_id or "").strip()
                        else "/control/chat"
                    ),
                    audit_href=detail_href,
                    health_href="/control/health/view",
                )
            )
            break
        if len(cards) >= max(1, int(limit)):
            break
    return cards[: max(1, int(limit))]


def _build_server_guard_preview_groups(
    actions: list[Any],
    *,
    reason_summary: str = "",
    note: str = "",
) -> list[ServerGuardActionGroupDTO]:
    canonical_order = [
        ("codex", "Codex Pressure"),
        ("python", "Python Pressure"),
        ("tmux", "Tmux Pressure"),
        ("process", "Process Pressure"),
        ("queue", "Queue Cleanup"),
        ("other", "Other Preview"),
    ]
    buckets: dict[str, list[Any]] = {key: [] for key, _label in canonical_order}
    for row in list(actions or []):
        path = str(getattr(row, "path", "") or "").strip()
        payload_json = str(getattr(row, "payload_json", "") or "").strip()
        key = "other"
        if path == "/control/actions/runtime/server-guard-pressure-preview":
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {}
            pressure_kind = str(payload.get("pressure_kind", "")).strip().lower()
            if pressure_kind in {"codex", "python", "tmux", "process"}:
                key = pressure_kind
        elif path == "/control/actions/runtime/background-queue-clean-preview":
            key = "queue"
        buckets.setdefault(key, []).append(row)
    reason_tokens = [str(token or "").strip() for token in str(reason_summary or "").split(" | ") if str(token or "").strip()]
    reason_to_group = (
        ("queue", "queue"),
        ("codex_process", "codex"),
        ("python_process", "python"),
        ("tmux_process", "tmux"),
        ("total_process", "process"),
    )
    dominant_key = "other"
    for token in reason_tokens:
        matched = next((group for prefix, group in reason_to_group if token.startswith(prefix)), "")
        if matched and buckets.get(matched):
            dominant_key = matched
            break
    ordered_keys = [dominant_key] + [key for key, _label in canonical_order if key != dominant_key]
    groups: list[ServerGuardActionGroupDTO] = []
    for key in ordered_keys:
        rows = [row for row in buckets.get(key, []) if getattr(row, "path", "") or getattr(row, "href", "")]
        if not rows:
            continue
        policy = server_guard_pressure_policy(key, fallback_note=note)
        group_note = str(policy.get("group_note", "")).strip() if key == dominant_key else ""
        groups.append(
            ServerGuardActionGroupDTO(
                key=key,
                label=str(policy.get("label", "")).strip() or dict(canonical_order).get(key, key.title()),
                note=group_note,
                operator_sentence=str(policy.get("operator_sentence", "")).strip(),
                action_sentence=str(policy.get("action_sentence", "")).strip(),
                focus_preset_label=str(policy.get("focus_preset_label", "")).strip(),
                priority_link_label=str(policy.get("priority_link_label", "")).strip(),
                priority_link_note=str(policy.get("priority_link_note", "")).strip(),
                actions=rows,
            )
        )
    return groups


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


def _chat_room_parts(room: str) -> tuple[str, str]:
    token = str(room or "").strip().strip("/")
    if not token or token.lower() == room_handlers.DEFAULT_ROOM_NAME:
        return "", ""
    head, sep, tail = token.partition("/")
    return head.strip(), tail.strip() if sep else ""


def _chat_room_lane_label(room: str) -> str:
    project_alias, lane = _chat_room_parts(room)
    if not project_alias and not lane:
        return "global rail"
    if project_alias and not lane:
        return f"{project_alias} root rail"
    if project_alias and lane:
        return f"{project_alias} / {lane} rail"
    return f"{lane or room} rail"


def _chat_room_difference_summary(*, room: str, selected_room: str) -> str:
    current = str(selected_room or "").strip()
    target = str(room or "").strip()
    if target == current:
        return "current rail"
    selected_project, selected_lane = _chat_room_parts(current)
    target_project, target_lane = _chat_room_parts(target)
    parts: list[str] = []
    selected_scope = selected_project or "global"
    target_scope = target_project or "global"
    if target_scope == selected_scope:
        parts.append("same project" if target_project else "same global scope")
    else:
        parts.append(f"project {selected_scope} -> {target_scope}")
    if target_lane == selected_lane:
        parts.append("same lane" if target_lane else "same root rail")
    else:
        parts.append(f"target {_chat_room_lane_label(target)}")
    return " | ".join(parts[:2]) if parts else "-"


def _chat_room_options(*, selected_room: str, rooms: list[str]) -> list[ChatRoomOptionDTO]:
    options: list[ChatRoomOptionDTO] = []
    seen: set[str] = set()
    for room in [str(selected_room or "").strip(), *[str(item or "").strip() for item in rooms]]:
        if not room or room in seen:
            continue
        seen.add(room)
        options.append(
            ChatRoomOptionDTO(
                room=room,
                lane_label=_chat_room_lane_label(room),
                difference_summary=_chat_room_difference_summary(room=room, selected_room=selected_room),
                is_selected=(room == str(selected_room or "").strip()),
            )
        )
    return options


def _chat_room_options_summary(options: list[ChatRoomOptionDTO]) -> str:
    hidden = [row for row in options if not row.is_selected]
    if not hidden:
        return "-"
    preview = ", ".join(row.lane_label for row in hidden[:3])
    if len(hidden) > 3:
        preview = f"{preview} +{len(hidden) - 3}"
    return f"{preview} | {len(hidden)} alternative rails"


def _chat_session_presets(
    *,
    project_alias: str,
    selected_room: str,
    selected_task: Dict[str, Any] | None = None,
) -> list[ChatSessionPresetDTO]:
    alias = str(project_alias or "").strip()
    presets: list[ChatSessionPresetDTO] = [
        ChatSessionPresetDTO(
            label="Global Direct",
            room=room_handlers.DEFAULT_ROOM_NAME,
            default_mode="direct",
            pending_mode="",
            lang="ko",
            report_level="short",
            note=task_view.planning_preset_operator_note(
                selected_task,
                base_note="short direct replies on the global rail",
            ),
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
                    note=task_view.planning_preset_operator_note(
                        selected_task,
                        base_note="analysis findings, evidence, caveats",
                    ),
                ),
                ChatSessionPresetDTO(
                    label="Writing Rail",
                    room=f"{alias}/writing",
                    default_mode="dispatch",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note=task_view.planning_preset_operator_note(
                        selected_task,
                        base_note="drafts, handoff, quality gate",
                    ),
                ),
                ChatSessionPresetDTO(
                    label="Package Rail",
                    room=f"{alias}/package",
                    default_mode="dispatch",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note=task_view.planning_preset_operator_note(
                        selected_task,
                        base_note="artifact verification, apply, syncback",
                    ),
                ),
                ChatSessionPresetDTO(
                    label="Review Rail",
                    room=f"{alias}/review",
                    default_mode="direct",
                    pending_mode="",
                    lang="ko",
                    report_level="normal",
                    note=task_view.planning_preset_operator_note(
                        selected_task,
                        base_note="operator review and escalation",
                    ),
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
                note=task_view.planning_preset_operator_note(
                    selected_task,
                    base_note="keep the current room selection",
                ),
            )
        )
    return presets[:6]


def _chat_preset_slug(label: str) -> str:
    return "-".join(token for token in str(label or "").strip().lower().replace("/", " ").split() if token)


def _chat_select_preset(
    presets: list[ChatSessionPresetDTO],
    *,
    token: str,
) -> ChatSessionPresetDTO | None:
    selected = str(token or "").strip()
    if not selected:
        return None
    selected_slug = _chat_preset_slug(selected)
    for row in presets:
        if str(row.label).strip() == selected or _chat_preset_slug(row.label) == selected_slug:
            return row
    return None


def _chat_live_preview_preset(
    *,
    preview_groups: list[ServerGuardActionGroupDTO],
    session_presets: list[ChatSessionPresetDTO],
) -> ChatSessionPresetDTO | None:
    if not preview_groups or not session_presets:
        return None
    preferred_label = str(preview_groups[0].focus_preset_label or "").strip()
    if not preferred_label:
        return None
    return _chat_select_preset(session_presets, token=preferred_label)


def _chat_live_preview_preset_note(
    *,
    preview_groups: list[ServerGuardActionGroupDTO],
    preset: ChatSessionPresetDTO | None,
    selected_task: Dict[str, Any] | None = None,
) -> str:
    row = preset if isinstance(preset, ChatSessionPresetDTO) else None
    if row is None:
        return ""
    pressure_note = ""
    if preview_groups:
        lead = preview_groups[0]
        pressure_note = str(lead.action_sentence or lead.operator_sentence or "").strip()
    return task_view.planning_operator_note(
        selected_task,
        notes=[pressure_note, str(row.note or "").strip()],
    )


def _chat_recommended_session_presets(
    *,
    server_guard: ControlSummaryDTO | None,
    session_presets: list[ChatSessionPresetDTO],
) -> list[ChatSessionPresetDTO]:
    if not session_presets:
        return []
    reason_summary = ""
    if isinstance(server_guard, ControlSummaryDTO):
        reason_summary = str(server_guard.server_guard.reason_summary or "").strip()
    tokens = [token.strip() for token in reason_summary.split("|") if token.strip()]

    def _has(prefix: str) -> bool:
        return any(token.startswith(prefix) for token in tokens)

    preferred_labels: list[str]
    if _has("codex_process") or _has("total_process") or _has("memory") or _has("load"):
        preferred_labels = ["Global Direct", "Review Rail", "Current Room"]
    elif _has("python_process"):
        preferred_labels = ["Review Rail", "Global Direct", "Current Room"]
    elif _has("queue") or _has("disk"):
        preferred_labels = ["Package Rail", "Review Rail", "Current Room"]
    else:
        preferred_labels = ["Analysis Rail", "Writing Rail", "Package Rail"]

    selected: list[ChatSessionPresetDTO] = []
    seen: set[str] = set()
    by_label = {row.label: row for row in session_presets}
    for label in preferred_labels:
        row = by_label.get(label)
        if row is None or row.label in seen:
            continue
        selected.append(row)
        seen.add(row.label)
    for row in session_presets:
        if row.label in seen:
            continue
        selected.append(row)
        seen.add(row.label)
        if len(selected) >= 4:
            break
    return selected[:4]


def _latest_server_guard_summary(
    rows: list[ActionAuditRowDTO],
    *,
    include_status: bool,
    outcome_kinds: set[str] | None = None,
) -> tuple[str, str]:
    for row in rows:
        if str(getattr(row, "focus_badge", "")).strip() != "server-guard":
            continue
        if outcome_kinds is not None and str(getattr(row, "outcome_kind", "")).strip() not in outcome_kinds:
            continue
        summary = _server_guard_row_summary(row, include_status=include_status)
        href = str(getattr(row, "link_href", "")).strip()
        if not href or href == "-":
            href = "/control/audit?focus=server-guard"
        return summary, href
    return "-", "/control/audit?focus=server-guard"


def _latest_server_guard_action(rows: list[ActionAuditRowDTO]) -> tuple[str, str]:
    return _latest_server_guard_summary(rows, include_status=False)


def _latest_server_guard_result(rows: list[ActionAuditRowDTO]) -> tuple[str, str]:
    return _latest_server_guard_summary(
        rows,
        include_status=True,
        outcome_kinds={
            "background_queue_cleanup_preview",
            "background_queue_cleanup",
            "auto_recover",
            "codex_process_pressure_preview",
            "python_process_pressure_preview",
            "tmux_process_pressure_preview",
            "process_pressure_preview",
            "chat_session_update",
        },
    )


def _planning_compact_suffix(value: str) -> str:
    token = str(value or "").strip()
    if not token or token == "-":
        return "-"
    if token.startswith("planning_compact="):
        return token
    if token.startswith("planning="):
        token = token[len("planning="):].strip()
    return f"planning_compact={token}"


def _subagent_evidence_suffix(value: str) -> str:
    token = str(value or "").strip()
    if not token or token == "-":
        return "-"
    if token.startswith("subagent_evidence="):
        return token
    return f"subagent_evidence={token}"


def _subagent_gate_suffix(value: str) -> str:
    token = str(value or "").strip()
    if not token or token == "-":
        return "-"
    if token.startswith("subagent_gate="):
        return token
    return f"subagent_gate={token}"


def _row_planning_compact_summary(row: ActionAuditRowDTO) -> str:
    compact = str(getattr(row, "planning_compact_summary", "")).strip()
    if compact and compact != "-":
        return compact
    return task_view.planning_compact_operator_summary(
        planning_compact=str(getattr(row, "planning_compact_summary", "")).strip()
        or str(getattr(row, "planning_review_summary", "")).strip(),
        approved_plan=str(getattr(row, "approved_plan_summary", "")).strip(),
    )


def _server_guard_row_summary(row: ActionAuditRowDTO, *, include_status: bool) -> str:
    headline = (
        str(getattr(row, "headline_summary", "")).strip()
        or str(getattr(row, "headline", "")).strip()
        or "-"
    )
    planning_summary = _planning_compact_suffix(_row_planning_compact_summary(row))
    subagent_summary = _subagent_evidence_suffix(str(getattr(row, "subagent_evidence_summary", "")).strip())
    subagent_gate_summary = _subagent_gate_suffix(str(getattr(row, "subagent_gate_summary", "")).strip())
    at = str(getattr(row, "at", "")).strip() or "-"
    next_step = str(getattr(row, "next_step", "")).strip() or "-"
    parts = [headline]
    if include_status:
        status = str(getattr(row, "outcome_status", "")).strip() or str(getattr(row, "status", "")).strip() or "-"
        parts.append(f"status={status}")
    parts.extend((f"at={at}", f"next={next_step}"))
    if planning_summary != "-":
        parts.append(planning_summary)
    if subagent_summary != "-" and subagent_summary not in headline:
        parts.append(subagent_summary)
    if subagent_gate_summary != "-" and subagent_gate_summary not in headline:
        parts.append(subagent_gate_summary)
    return " | ".join(parts)


def _load_recent_chat_action_rows(paths: ControlPaths, *, chat_id: str, limit: int = 8) -> list[ActionAuditRowDTO]:
    rows: list[ActionAuditRowDTO] = []
    raw_rows: list[dict[str, Any]] = load_jsonl_rows(paths.action_audit_file)
    for raw in reversed(raw_rows):
        if not isinstance(raw, dict):
            continue
        outcome_kind = str(raw.get("outcome_kind", "")).strip()
        if outcome_kind not in {
            "chat_send",
            "chat_session_update",
            "chat_session_select_task",
            "codex_process_pressure_preview",
            "python_process_pressure_preview",
            "tmux_process_pressure_preview",
            "process_pressure_preview",
        }:
            continue
        focus_badge = str(raw.get("focus_badge", "")).strip()
        if not focus_badge and outcome_kind in {
            "codex_process_pressure_preview",
            "python_process_pressure_preview",
            "tmux_process_pressure_preview",
            "process_pressure_preview",
        }:
            focus_badge = "server-guard"
        planning_compact_summary = action_audit.summarize_retry_replan_planning_compact_handoff(
            raw.get("planning_handoff"),
            row=raw,
        )
        approved_plan_summary = action_audit.summarize_retry_replan_approved_plan_handoff(
            raw.get("planning_handoff"),
            row=raw,
        )
        planning_compact_summary = task_view.planning_compact_operator_summary(
            planning_compact=planning_compact_summary,
            approved_plan=approved_plan_summary,
        )
        row = ActionAuditRowDTO(
            at=str(raw.get("at", "")).strip() or "-",
            headline=str(raw.get("headline", "")).strip() or "-",
            headline_summary=action_audit.summarize_action_audit_headline(raw),
            chat_reply_summary=action_audit.summarize_chat_reply_compact(raw),
            chat_room_change_summary=action_audit.summarize_chat_room_change_compact(raw),
            project_alias=_infer_action_audit_project_alias(raw),
            planning_compact_summary=planning_compact_summary,
            subagent_contract_summary=str(
                raw.get("subagent_contract_summary") or raw.get("general_subagent_summary") or "-"
            ).strip()
            or "-",
            subagent_evidence_summary=str(
                raw.get("subagent_evidence_summary") or raw.get("general_subagent_artifact_summary") or "-"
            ).strip()
            or "-",
            subagent_artifact_path=str(
                raw.get("subagent_artifact_path") or raw.get("general_subagent_artifact_path") or "-"
            ).strip()
            or "-",
            subagent_gate_summary=(
                str(raw.get("subagent_gate_summary") or raw.get("subagent_blocking_issue_summary") or "").strip()
                or action_audit.summarize_subagent_gate_compact_row(raw)
                or "-"
            ),
            approved_plan_summary=approved_plan_summary,
            status=str(raw.get("status", "")).strip() or "unknown",
            outcome_kind=outcome_kind or "-",
            outcome_status=str(raw.get("outcome_status", "")).strip() or str(raw.get("status", "")).strip() or "unknown",
            outcome_reason_code=str(raw.get("outcome_reason_code", "")).strip() or "-",
            outcome_detail=str(raw.get("outcome_detail", "")).strip() or "-",
            next_step=str(raw.get("next_step", "")).strip() or "-",
            remediation=str(raw.get("remediation", "")).strip() or "-",
            link_label=str(raw.get("link_label", "")).strip() or "-",
            link_href=str(raw.get("link_href", "")).strip() or "-",
            source_command=str(raw.get("source_command", "")).strip() or "-",
            focus_badge=focus_badge,
            chat_id=str(raw.get("chat_id", "")).strip(),
            transcript_preview=str(raw.get("transcript_preview", "")).strip(),
            chat_preset_diff_summary=str(raw.get("chat_preset_diff_summary", "")).strip(),
            applied_preferences_summary=action_audit.summarize_applied_preferences_compact(raw),
            preference_candidate_summary=action_audit.summarize_preference_candidates_compact(raw),
            preference_decision_summary=action_audit.summarize_preference_decisions_compact(raw),
            thread_href=(
                f"/control/audit?focus=server-guard&chat={str(raw.get('chat_id', '')).strip()}&limit=20"
                if focus_badge == "server-guard" and str(raw.get("chat_id", "")).strip()
                else ""
            ),
            thread_label="Open Thread Detail" if focus_badge == "server-guard" and str(raw.get("chat_id", "")).strip() else "",
        )
        if chat_id and row.chat_id and row.chat_id != chat_id:
            continue
        rows.append(row)
        if len(rows) >= max(1, int(limit)):
            break
    return rows


def _chat_last_send_mode_from_actions(rows: list[ActionAuditRowDTO]) -> str:
    for row in rows:
        if str(row.outcome_kind or "").strip() != "chat_send":
            continue
        command = str(row.source_command or "").strip().lower()
        if command.startswith("/direct "):
            return "direct"
        if command.startswith("/dispatch "):
            return "dispatch"
        if command.startswith("/room post "):
            return "room_post"
        if command.startswith("/room use "):
            return "room_use"
        if command:
            return "raw"
    return ""


def load_dashboard_chat_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    selected_chat_id: str = "",
    selected_preset: str = "",
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
    room_options = _chat_room_options(selected_room=selected_room, rooms=rooms)
    selected_project_key, selected_project_alias = _chat_session_project_context(
        snapshot_result.manager_state,
        raw_session=selected_session_row if isinstance(selected_session_row, dict) else {},
        selected_room=selected_room,
    )
    selected_task_ref = _chat_selected_task_ref(
        snapshot_result.manager_state,
        raw_session=selected_session_row if isinstance(selected_session_row, dict) else {},
        chat_id=selected_token,
        project_key=selected_project_key,
    )
    selected_task = _chat_selected_task_record(
        snapshot_result.manager_state,
        project_key=selected_project_key,
        task_ref=selected_task_ref,
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
    selected_send_mode = (
        chat_state.get_chat_last_send_mode(snapshot_result.manager_state, selected_token)
        or _chat_last_send_mode_from_actions(recent_chat_actions)
        or "raw"
    )
    server_guard_thread = _build_server_guard_thread_card(recent_chat_actions)
    server_guard_threads = _build_server_guard_thread_cards(recent_chat_actions, limit=3)
    timeline_entries = _build_chat_timeline_entries(
        room=selected_room,
        room_tail=room_tail,
        recent_chat_actions=recent_chat_actions,
        limit=20,
    )
    room_presets = _chat_room_presets(project_alias=selected_project_alias, selected_room=selected_room, rooms=rooms)
    session_presets = _chat_session_presets(
        project_alias=selected_project_alias,
        selected_room=selected_room,
        selected_task=selected_task,
    )
    deep_link_preset = _chat_select_preset(session_presets, token=selected_preset)
    live_preview_preset = _chat_live_preview_preset(
        preview_groups=snapshot_result.snapshot.control_summary.server_guard_preview_groups,
        session_presets=session_presets,
    )

    selected_task_planning_bundle = task_view.planning_operator_bundle(selected_task)

    return snapshot_result.snapshot, ChatConsolePageDTO(
        selected_chat_id=selected_session.chat_id if selected_session is not None else selected_token,
        selected_chat_alias=selected_session.chat_alias if selected_session is not None else alias_by_chat_id.get(selected_token, ""),
        selected_room=selected_room,
        selected_project_key=selected_project_key,
        selected_project_alias=selected_project_alias,
        selected_task_ref=selected_task_ref,
        selected_default_mode=selected_session.default_mode if selected_session is not None else "off",
        selected_pending_mode=selected_session.pending_mode if selected_session is not None else "none",
        selected_send_mode=selected_send_mode,
        selected_lang=selected_session.lang if selected_session is not None else chat_state.DEFAULT_UI_LANG,
        selected_report_level=selected_session.report_level if selected_session is not None else chat_state.DEFAULT_REPORT_LEVEL,
        selected_task_planning_lanes_summary=str(
            selected_task_planning_bundle.get("planning_lanes", "")
        ).strip()
        or "-",
        selected_task_approved_plan_gate_summary=str(
            selected_task_planning_bundle.get("approved_plan_gate", "")
        ).strip()
        or "-",
        selected_task_planning_compact_summary=str(
            selected_task_planning_bundle.get("planning_compact", "")
        ).strip()
        or "-",
        selected_task_planner_lane_summary=str(
            selected_task_planning_bundle.get("planner_lane", "")
        ).strip()
        or "-",
        selected_task_critic_lane_summary=str(
            selected_task_planning_bundle.get("critic_lane", "")
        ).strip()
        or "-",
        selected_task_approved_plan_summary=str(
            selected_task_planning_bundle.get("approved_plan", "")
        ).strip()
        or "-",
        rooms=rooms,
        room_options=room_options,
        room_options_summary=_chat_room_options_summary(room_options),
        room_presets=room_presets,
        room_preset_options=_chat_room_options(selected_room=selected_room, rooms=room_presets),
        session_presets=session_presets,
        recommended_session_presets=_chat_recommended_session_presets(
            server_guard=snapshot_result.snapshot.control_summary,
            session_presets=session_presets,
        ),
        deep_link_preset_label=deep_link_preset.label if deep_link_preset is not None else "",
        deep_link_preset_note=deep_link_preset.note if deep_link_preset is not None else "",
        deep_link_preset_room=deep_link_preset.room if deep_link_preset is not None else "",
        deep_link_preset_default_mode=deep_link_preset.default_mode if deep_link_preset is not None else "",
        deep_link_preset_pending_mode=deep_link_preset.pending_mode if deep_link_preset is not None else "",
        deep_link_preset_lang=deep_link_preset.lang if deep_link_preset is not None else "",
        deep_link_preset_report_level=deep_link_preset.report_level if deep_link_preset is not None else "",
        live_preview_preset_label=live_preview_preset.label if live_preview_preset is not None else "",
        live_preview_preset_note=_chat_live_preview_preset_note(
            preview_groups=snapshot_result.snapshot.control_summary.server_guard_preview_groups,
            preset=live_preview_preset,
            selected_task=selected_task,
        ),
        live_preview_preset_room=live_preview_preset.room if live_preview_preset is not None else "",
        live_preview_preset_default_mode=live_preview_preset.default_mode if live_preview_preset is not None else "",
        live_preview_preset_pending_mode=live_preview_preset.pending_mode if live_preview_preset is not None else "",
        live_preview_preset_lang=live_preview_preset.lang if live_preview_preset is not None else "",
        live_preview_preset_report_level=live_preview_preset.report_level if live_preview_preset is not None else "",
        selected_recent_task_refs=selected_recent_task_refs,
        sessions=sessions,
        room_tail=room_tail,
        server_guard_thread=server_guard_thread,
        server_guard_threads=server_guard_threads,
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


def load_dashboard_preferences_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    project_filter: str = "",
    artifact_filter: str = "",
    scope_filter: str = "",
) -> Tuple[DashboardSnapshotDTO, OperatorPreferencesPageDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    projects = loaded.manager_state.get("projects") if isinstance(loaded.manager_state.get("projects"), dict) else {}
    active_key = str(loaded.manager_state.get("active", "")).strip()
    active_entry = projects.get(active_key) if active_key and isinstance(projects.get(active_key), dict) else {}
    active_team_dir = Path(str(active_entry.get("team_dir", "")).strip() or str(paths.team_dir)).expanduser().resolve()
    registry_path = runtime_core.operator_preferences_path(active_team_dir)
    candidate_path = runtime_core.operator_preference_candidates_path(active_team_dir)
    _registry_state, registry_freshness = _load_json_file(registry_path, name="operator_preferences")
    _candidate_state, candidate_freshness = _load_json_file(candidate_path, name="operator_preference_candidates")
    project_filter_token = " ".join(str(project_filter or "").strip().split())
    artifact_filter_token = " ".join(str(artifact_filter or "").strip().split()).lower()
    scope_filter_token = " ".join(str(scope_filter or "").strip().split()).lower()
    project_rows: list[OperatorPreferenceProjectSummaryDTO] = []
    selected_project_rule_rows: list[OperatorPreferenceRuleDTO] = []
    selected_project_candidate_rows: list[OperatorPreferenceCandidateDTO] = []
    for project_key, raw_entry in projects.items():
        if not isinstance(raw_entry, dict):
            continue
        project_alias = _preference_project_alias(str(project_key or "").strip(), raw_entry)
        project_label = _preference_project_label(str(project_key or "").strip(), raw_entry)
        runtime_ref = _preference_project_filter_value(str(project_key or "").strip(), raw_entry) or project_alias
        runtime_path = f"/control/runtimes/{project_alias}" if project_alias and project_alias != "-" else "-"
        project_team_dir = Path(
            str(raw_entry.get("team_dir", "")).strip() or str(paths.team_dir)
        ).expanduser().resolve()
        project_registry_path = runtime_core.operator_preferences_path(project_team_dir)
        project_candidate_path = runtime_core.operator_preference_candidates_path(project_team_dir)
        project_registry_state, project_registry_freshness = _load_json_file(
            project_registry_path,
            name="operator_preferences",
        )
        project_candidate_state, project_candidate_freshness = _load_json_file(
            project_candidate_path,
            name="operator_preference_candidates",
        )
        normalized_project_registry = operator_preferences.normalize_preference_state(project_registry_state)
        normalized_project_candidates = operator_preferences.normalize_preference_candidates_state(project_candidate_state)
        project_rules = sorted(
            [
                operator_preferences.normalize_preference_rule(item)
                for item in list(normalized_project_registry.get("rules") or [])
                if item
            ],
            key=_preference_rule_sort_key,
        )
        project_candidates = sorted(
            [
                operator_preferences.normalize_preference_candidate(item)
                for item in list(normalized_project_candidates.get("candidates") or [])
                if item
            ],
            key=_preference_candidate_sort_key,
        )
        is_selected = _preference_project_matches_filter(str(project_key or "").strip(), raw_entry, project_filter_token)
        filter_value = _preference_project_filter_value(str(project_key or "").strip(), raw_entry)
        project_rows.append(
            OperatorPreferenceProjectSummaryDTO(
                project_key=str(project_key or "").strip() or "-",
                project_alias=project_alias,
                project_label=project_label,
                project_team_dir=str(project_team_dir),
                runtime_ref=runtime_ref,
                runtime_path=runtime_path,
                filter_value=filter_value,
                filter_href=_preferences_filter_path(
                    project_filter=filter_value,
                    artifact_filter=artifact_filter_token,
                    scope_filter=scope_filter_token,
                ),
                is_active=bool(active_key and str(project_key or "").strip() == active_key),
                is_selected=is_selected,
                registry_file=project_registry_freshness,
                candidate_file=project_candidate_freshness,
                rule_count=len(project_rules),
                candidate_count=len(project_candidates),
                rule_summary=_preference_count_summary(project_rules),
                candidate_summary=_candidate_count_summary(project_candidates),
                scope_summary=_compact_counter_summary([str(row.get("scope", "")).strip() for row in project_rules]),
                artifact_summary=_compact_counter_summary(
                    [str(row.get("artifact_kind", "")).strip() for row in project_rules + project_candidates]
                ),
            )
        )
        if not is_selected:
            continue
        for row in project_rules:
            if not row:
                continue
            selected_project_rule_rows.append(
                OperatorPreferenceRuleDTO(
                    project_key=str(project_key or "").strip() or "-",
                    project_alias=project_alias,
                    project_label=project_label,
                    project_team_dir=str(project_team_dir),
                    runtime_ref=runtime_ref,
                    runtime_path=runtime_path,
                    id=str(row.get("id", "")).strip() or "-",
                    key=str(row.get("key", "")).strip() or "-",
                    artifact_kind=str(row.get("artifact_kind", "")).strip() or "-",
                    scope=str(row.get("scope", "")).strip() or "-",
                    scope_ref=str(row.get("scope_ref", "")).strip() or "-",
                    value_summary=operator_preferences._value_label(row.get("value")),
                    value_json=json.dumps(row.get("value"), ensure_ascii=False),
                    description=str(row.get("description", "")).strip() or "-",
                    enabled=bool(row.get("enabled", False)),
                    prompt_mode=str(row.get("prompt_mode", "")).strip() or "-",
                    source=str(row.get("source", "")).strip() or "-",
                    confidence=str(row.get("confidence", "")).strip() or "-",
                    summary=operator_preferences.summarize_preference_rule(row),
                    updated_at=str(row.get("updated_at", "")).strip() or "-",
                )
            )
        for row in project_candidates:
            if not row:
                continue
            candidate_artifact_kind = str(row.get("artifact_kind", "")).strip() or "-"
            candidate_project_ref = str(row.get("project_ref", "")).strip() or "-"
            expected_scope, expected_scope_ref = operator_preferences.preference_candidate_scope(
                artifact_kind=candidate_artifact_kind,
                project_ref="" if candidate_project_ref == "-" else candidate_project_ref,
            )
            selected_project_candidate_rows.append(
                OperatorPreferenceCandidateDTO(
                    project_key=str(project_key or "").strip() or "-",
                    project_alias=project_alias,
                    project_label=project_label,
                    project_team_dir=str(project_team_dir),
                    runtime_ref=runtime_ref,
                    runtime_path=runtime_path,
                    id=str(row.get("id", "")).strip() or "-",
                    key=str(row.get("key", "")).strip() or "-",
                    artifact_kind=candidate_artifact_kind,
                    project_ref=candidate_project_ref,
                    expected_scope=expected_scope,
                    expected_scope_label=_preference_memory_scope_label(expected_scope),
                    expected_scope_ref=expected_scope_ref,
                    suggested_value_summary=operator_preferences._value_label(row.get("suggested_value")),
                    suggested_value_json=json.dumps(row.get("suggested_value"), ensure_ascii=False),
                    issue=str(row.get("issue", "")).strip() or "-",
                    hits=max(1, int(row.get("occurrence_count", 1) or 1)),
                    source_refs_summary=", ".join(
                        str(item).strip() for item in list(row.get("source_refs") or []) if str(item).strip()
                    )
                    or "-",
                    summary=operator_preferences.summarize_preference_candidate(row),
                    updated_at=str(row.get("updated_at", "")).strip() or "-",
                )
            )
    project_rows = sorted(
        project_rows,
        key=lambda row: (
            0 if row.is_selected else 1,
            0 if row.is_active else 1,
            str(row.project_alias or row.project_key),
        ),
    )
    selected_projects = [row for row in project_rows if row.is_selected]
    selected_scope_summary = "all runtimes"
    if project_filter_token:
        if len(selected_projects) == 1:
            selected_scope_summary = f"{selected_projects[0].project_alias} {selected_projects[0].project_label}".strip()
        elif selected_projects:
            selected_scope_summary = f"matched={len(selected_projects)}"
        else:
            selected_scope_summary = f"unmatched ({project_filter_token})"
    selected_filter_value = (
        selected_projects[0].filter_value if project_filter_token and len(selected_projects) == 1 else project_filter_token
    )
    artifact_keys = sorted(
        {
            str(row.artifact_kind or "").strip() or "-"
            for row in [
                *[
                    item
                    for item in selected_project_rule_rows
                    if _preference_memory_scope_matches_filter(item.scope, scope_filter_token)
                ],
                *[
                    item
                    for item in selected_project_candidate_rows
                    if _preference_memory_scope_matches_filter(item.expected_scope, scope_filter_token)
                ],
            ]
            if str(getattr(row, "artifact_kind", "") or "").strip()
        }
    )
    selected_artifact_summary = "all artifacts"
    if artifact_filter_token:
        if artifact_filter_token in {token.lower() for token in artifact_keys}:
            selected_artifact_summary = artifact_filter_token
        else:
            selected_artifact_summary = f"unmatched ({artifact_filter_token})"
    scope_keys = sorted(
        {
            str(row.scope or "").strip().lower()
            for row in selected_project_rule_rows
            if str(row.scope or "").strip()
        }
        | {
            str(row.expected_scope or "").strip().lower()
            for row in selected_project_candidate_rows
            if str(row.expected_scope or "").strip()
        }
    )
    selected_memory_scope_summary = "all scopes"
    if scope_filter_token:
        if scope_filter_token in scope_keys or scope_filter_token in {"session", "project", "artifact_kind", "user_global"}:
            selected_memory_scope_summary = _preference_memory_scope_label(scope_filter_token)
        else:
            selected_memory_scope_summary = f"unmatched ({scope_filter_token})"
    artifact_rows = [
        OperatorPreferenceArtifactSummaryDTO(
            artifact_kind=artifact_kind,
            filter_value=artifact_kind,
            filter_href=_preferences_filter_path(
                project_filter=selected_filter_value,
                artifact_filter=artifact_kind,
                scope_filter=scope_filter_token,
            ),
            audit_href=(
                "/control/audit?focus=preferences"
                + (f"&project={quote(selected_filter_value, safe='')}" if selected_filter_value else "")
                + "&q="
                + quote(
                    " ".join(
                        token
                        for token in (
                            f"artifact_kind:{artifact_kind}",
                            f"memory_scope:{scope_filter_token}" if scope_filter_token else "",
                        )
                        if token
                    ),
                    safe="",
                )
                + "&limit=50"
            ),
            history_href=(
                "/control/history"
                + f"?q={quote(' '.join(token for token in (f'artifact_kind:{artifact_kind}', f'memory_scope:{scope_filter_token}' if scope_filter_token else '') if token), safe='')}"
                + (f"&project={quote(selected_filter_value, safe='')}" if selected_filter_value else "")
                + "&scope=dashboard&limit=20"
            ),
            is_selected=bool(artifact_filter_token) and _preference_artifact_matches_filter(artifact_kind, artifact_filter_token),
            rule_count=len(
                [
                    row
                    for row in selected_project_rule_rows
                    if row.artifact_kind == artifact_kind
                    and _preference_memory_scope_matches_filter(row.scope, scope_filter_token)
                ]
            ),
            candidate_count=len(
                [
                    row
                    for row in selected_project_candidate_rows
                    if row.artifact_kind == artifact_kind
                    and _preference_memory_scope_matches_filter(row.expected_scope, scope_filter_token)
                ]
            ),
            ready_candidate_count=len(
                [
                    row
                    for row in selected_project_candidate_rows
                    if row.artifact_kind == artifact_kind
                    and _preference_memory_scope_matches_filter(row.expected_scope, scope_filter_token)
                    and int(row.hits or 0) >= operator_preferences.PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD
                ]
            ),
            prompt_mode_summary=_preference_prompt_mode_summary(
                [
                    row
                    for row in selected_project_rule_rows
                    if row.artifact_kind == artifact_kind
                    and _preference_memory_scope_matches_filter(row.scope, scope_filter_token)
                ]
            ),
            project_summary=_preference_project_list_summary(
                [
                    row.project_alias
                    for row in [*selected_project_rule_rows, *selected_project_candidate_rows]
                    if row.artifact_kind == artifact_kind
                    and _preference_memory_scope_matches_filter(
                        getattr(row, "scope", getattr(row, "expected_scope", "")),
                        scope_filter_token,
                    )
                ]
            ),
        )
        for artifact_kind in artifact_keys
    ]
    artifact_scoped_rule_rows = [
        row
        for row in selected_project_rule_rows
        if _preference_artifact_matches_filter(row.artifact_kind, artifact_filter_token)
    ]
    artifact_scoped_candidate_rows = [
        row
        for row in selected_project_candidate_rows
        if _preference_artifact_matches_filter(row.artifact_kind, artifact_filter_token)
    ]
    candidate_rows = [
        row
        for row in artifact_scoped_candidate_rows
        if _preference_memory_scope_matches_filter(row.expected_scope, scope_filter_token)
    ]
    rule_rows = [
        row
        for row in artifact_scoped_rule_rows
        if _preference_memory_scope_matches_filter(row.scope, scope_filter_token)
    ]
    memory_scope_rows = _preference_memory_scope_rows(
        artifact_scoped_rule_rows,
        artifact_scoped_candidate_rows,
        project_filter=selected_filter_value,
        artifact_filter=artifact_filter_token,
        scope_filter=scope_filter_token,
    )
    return loaded.snapshot, OperatorPreferencesPageDTO(
        project_alias=str(active_entry.get("project_alias", "")).strip().upper() or "-",
        project_label=str(active_entry.get("display_name", "")).strip() or str(active_entry.get("name", "")).strip() or "-",
        project_team_dir=str(active_team_dir),
        return_path=_preferences_filter_path(
            project_filter=selected_filter_value,
            artifact_filter=artifact_filter_token,
            scope_filter=scope_filter_token,
        ),
        registry_file=registry_freshness,
        candidate_file=candidate_freshness,
        rule_summary=_preference_count_summary(
            [
                {
                    "enabled": row.enabled,
                    "prompt_mode": row.prompt_mode,
                }
                for row in rule_rows
            ]
        ),
        candidate_summary=_candidate_count_summary(
            [
                {
                    "occurrence_count": row.hits,
                }
                for row in candidate_rows
            ]
        ),
        scope_summary=_compact_counter_summary([row.scope for row in rule_rows]),
        artifact_summary=_compact_counter_summary([row.artifact_kind for row in [*rule_rows, *candidate_rows]]),
        project_filter=selected_filter_value,
        artifact_filter=artifact_filter_token,
        memory_scope_filter=scope_filter_token,
        selected_scope_summary=selected_scope_summary,
        selected_artifact_summary=selected_artifact_summary,
        selected_memory_scope_summary=selected_memory_scope_summary,
        visible_project_count=len(selected_projects),
        total_project_count=len(project_rows),
        registry_file_summary=_preference_file_presence_summary([row.registry_file for row in selected_projects]),
        candidate_file_summary=_preference_file_presence_summary([row.candidate_file for row in selected_projects]),
        clear_project_href=_preferences_filter_path(artifact_filter=artifact_filter_token, scope_filter=scope_filter_token),
        clear_artifact_href=_preferences_filter_path(project_filter=selected_filter_value, scope_filter=scope_filter_token),
        clear_memory_scope_href=_preferences_filter_path(
            project_filter=selected_filter_value,
            artifact_filter=artifact_filter_token,
        ),
        projects=project_rows,
        artifact_rows=artifact_rows,
        memory_scope_rows=memory_scope_rows,
        rules=rule_rows,
        candidates=candidate_rows,
    )


def load_dashboard_action_audit_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    focus: str = "",
    project_filter: str = "",
    chat_id: str = "",
    query: str = "",
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
    project_token = " ".join(str(project_filter or "").strip().split()).upper()
    chat_filter = str(chat_id or "").strip()
    query_filter = " ".join(str(query or "").strip().split())
    query_tokens = [token for token in query_filter.split() if token]
    artifact_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("artifact_kind:") and token.split(":", 1)[1].strip()
    ]
    artifact_filter_token = artifact_query_tokens[0] if artifact_query_tokens else ""
    memory_scope_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("memory_scope:") and token.split(":", 1)[1].strip()
    ]
    memory_scope_filter_token = (
        memory_scope_query_tokens[0].split(":", 1)[0].strip().lower()
        if memory_scope_query_tokens else ""
    )
    artifact_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("artifact_kind:")
    )
    memory_scope_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("memory_scope:")
    )
    refresh_diff_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if (
            token.lower().startswith("refresh_diff:")
            or token.lower().startswith("diff:")
        )
        and token.split(":", 1)[1].strip()
    ]
    refresh_diff_filter_token = refresh_diff_query_tokens[0] if refresh_diff_query_tokens else ""
    refresh_diff_query_base = " ".join(
        token
        for token in query_tokens
        if not token.lower().startswith("refresh_diff:")
        and not token.lower().startswith("diff:")
    )
    chat_event_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("chat_event:") and token.split(":", 1)[1].strip()
    ]
    chat_event_filter_token = chat_event_query_tokens[0] if chat_event_query_tokens else ""
    chat_event_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("chat_event:")
    )
    chat_mode_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("chat_mode:") and token.split(":", 1)[1].strip()
    ]
    chat_mode_filter_token = chat_mode_query_tokens[0] if chat_mode_query_tokens else ""
    chat_mode_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("chat_mode:")
    )
    if focus_filter not in {"all", "auto-route", "judge", "retry", "server-guard", "preferences"}:
        focus_filter = "all"
    filtered_rows = rows
    if focus_filter != "all":
        filtered_rows = [row for row in rows if str(getattr(row, "focus_badge", "")).strip() == focus_filter]
    if chat_filter:
        filtered_rows = [row for row in filtered_rows if str(getattr(row, "chat_id", "")).strip() == chat_filter]
    project_count_rows = filtered_rows
    if query_filter:
        project_count_rows = [row for row in project_count_rows if _audit_query_matches_row(row, query_filter)]
    project_counts: Dict[str, int] = {}
    for row in project_count_rows:
        alias = str(getattr(row, "project_alias", "")).strip().upper()
        if not alias or alias == "-":
            continue
        project_counts[alias] = int(project_counts.get(alias, 0) or 0) + 1
    artifact_count_rows = filtered_rows
    if project_token:
        artifact_count_rows = [row for row in artifact_count_rows if _audit_project_matches_row(row, project_token)]
    if artifact_query_base:
        artifact_count_rows = [row for row in artifact_count_rows if _audit_query_matches_row(row, artifact_query_base)]
    artifact_counts: Dict[str, int] = {}
    for row in artifact_count_rows:
        artifact = str(getattr(row, "preference_artifact_kind", "")).strip().lower()
        if not artifact or artifact == "-":
            continue
        artifact_counts[artifact] = int(artifact_counts.get(artifact, 0) or 0) + 1
    memory_scope_count_rows = filtered_rows
    if project_token:
        memory_scope_count_rows = [row for row in memory_scope_count_rows if _audit_project_matches_row(row, project_token)]
    if memory_scope_query_base:
        memory_scope_count_rows = [row for row in memory_scope_count_rows if _audit_query_matches_row(row, memory_scope_query_base)]
    memory_scope_counts: Dict[str, int] = {}
    for row in memory_scope_count_rows:
        for scope_kind in _audit_memory_scope_kinds(row):
            memory_scope_counts[scope_kind] = int(memory_scope_counts.get(scope_kind, 0) or 0) + 1
    refresh_diff_count_rows = filtered_rows
    if project_token:
        refresh_diff_count_rows = [row for row in refresh_diff_count_rows if _audit_project_matches_row(row, project_token)]
    if refresh_diff_query_base:
        refresh_diff_count_rows = [row for row in refresh_diff_count_rows if _audit_query_matches_row(row, refresh_diff_query_base)]
    refresh_diff_counts: Dict[str, int] = {}
    for row in refresh_diff_count_rows:
        for diff_kind in _audit_preference_refresh_diff_kinds(row):
            refresh_diff_counts[diff_kind] = int(refresh_diff_counts.get(diff_kind, 0) or 0) + 1
    chat_event_count_rows = filtered_rows
    if project_token:
        chat_event_count_rows = [row for row in chat_event_count_rows if _audit_project_matches_row(row, project_token)]
    if chat_event_query_base:
        chat_event_count_rows = [row for row in chat_event_count_rows if _audit_query_matches_row(row, chat_event_query_base)]
    chat_event_counts: Dict[str, int] = {}
    for row in chat_event_count_rows:
        for event_kind in _audit_chat_event_kinds(row):
            chat_event_counts[event_kind] = int(chat_event_counts.get(event_kind, 0) or 0) + 1
    chat_mode_count_rows = filtered_rows
    if project_token:
        chat_mode_count_rows = [row for row in chat_mode_count_rows if _audit_project_matches_row(row, project_token)]
    if chat_mode_query_base:
        chat_mode_count_rows = [row for row in chat_mode_count_rows if _audit_query_matches_row(row, chat_mode_query_base)]
    chat_mode_counts: Dict[str, int] = {}
    for row in chat_mode_count_rows:
        mode = _audit_chat_mode(row)
        if not mode:
            continue
        chat_mode_counts[mode] = int(chat_mode_counts.get(mode, 0) or 0) + 1
    if project_token:
        filtered_rows = [row for row in filtered_rows if _audit_project_matches_row(row, project_token)]
    if query_filter:
        filtered_rows = [row for row in filtered_rows if _audit_query_matches_row(row, query_filter)]
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
        project_filter=project_token,
        artifact_filter=artifact_filter_token,
        memory_scope_filter=memory_scope_filter_token,
        refresh_diff_filter=refresh_diff_filter_token,
        chat_event_filter=chat_event_filter_token,
        chat_mode_filter=chat_mode_filter_token,
        chat_filter=chat_filter,
        query_filter=query_filter,
        artifact_query_base=artifact_query_base,
        memory_scope_query_base=memory_scope_query_base,
        refresh_diff_query_base=refresh_diff_query_base,
        chat_event_query_base=chat_event_query_base,
        chat_mode_query_base=chat_mode_query_base,
        focus_counts={"all": len(rows), **focus_counts},
        project_counts=project_counts,
        artifact_counts=artifact_counts,
        memory_scope_counts=memory_scope_counts,
        refresh_diff_counts=refresh_diff_counts,
        chat_event_counts=chat_event_counts,
        chat_mode_counts=chat_mode_counts,
        rows=filtered_rows,
    )


def load_dashboard_history_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
    query: str = "",
    chat_filter: str = "",
    project_filter: str = "",
    since: str = "",
    scope: str = "all",
    compact_mode: bool = False,
    limit: int = 20,
) -> Tuple[DashboardSnapshotDTO, HistorySearchPageDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    since_label = str(since or "").strip()
    query_text = " ".join(str(query or "").strip().split())
    query_tokens = [token for token in query_text.split() if token]
    chat_event_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("chat_event:") and token.split(":", 1)[1].strip()
    ]
    chat_event_filter = chat_event_query_tokens[0] if chat_event_query_tokens else ""
    chat_event_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("chat_event:")
    )
    chat_mode_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("chat_mode:") and token.split(":", 1)[1].strip()
    ]
    chat_mode_filter = chat_mode_query_tokens[0] if chat_mode_query_tokens else ""
    chat_mode_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("chat_mode:")
    )
    room_kind_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("room_kind:") and token.split(":", 1)[1].strip()
    ]
    room_kind_filter = room_kind_query_tokens[0] if room_kind_query_tokens else ""
    room_kind_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("room_kind:")
    )
    room_actor_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("room_actor:") and token.split(":", 1)[1].strip()
    ]
    room_actor_filter = room_actor_query_tokens[0] if room_actor_query_tokens else ""
    room_actor_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("room_actor:")
    )
    room_text_query_tokens = [
        token.split(":", 1)[1].strip().lower()
        for token in query_tokens
        if token.lower().startswith("room_text:") and token.split(":", 1)[1].strip()
    ]
    room_text_filters = [
        value for value in room_text_query_tokens if value
    ]
    room_text_query_base = " ".join(
        token for token in query_tokens if not token.lower().startswith("room_text:")
    )
    options = history_search.HistorySearchOptions(
        query=query_text,
        chat_filter=str(chat_filter or "").strip(),
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
    chat_event_count_rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=replace(options, query=chat_event_query_base),
    )
    chat_event_counts: Dict[str, int] = {}
    for row in chat_event_count_rows:
        for event_kind in _history_row_chat_event_kinds(row):
            chat_event_counts[event_kind] = int(chat_event_counts.get(event_kind, 0) or 0) + 1
    chat_mode_count_rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=replace(options, query=chat_mode_query_base),
    )
    chat_mode_counts: Dict[str, int] = {}
    for row in chat_mode_count_rows:
        mode = _history_row_chat_mode(row)
        if not mode:
            continue
        chat_mode_counts[mode] = int(chat_mode_counts.get(mode, 0) or 0) + 1
    room_kind_count_rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=replace(options, query=room_kind_query_base),
    )
    room_kind_counts: Dict[str, int] = {}
    for row in room_kind_count_rows:
        room_kind = _history_row_room_kind(row)
        if not room_kind:
            continue
        room_kind_counts[room_kind] = int(room_kind_counts.get(room_kind, 0) or 0) + 1
    room_kind_values = _ordered_room_kind_values(room_kind_counts)
    room_actor_count_rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=replace(options, query=room_actor_query_base),
    )
    room_actor_counts: Dict[str, int] = {}
    for row in room_actor_count_rows:
        room_actor = _history_row_room_actor(row)
        if not room_actor:
            continue
        room_actor_counts[room_actor] = int(room_actor_counts.get(room_actor, 0) or 0) + 1
    room_actor_values = _ordered_room_actor_values(room_actor_counts)
    room_text_count_rows = history_search.search_history_rows(
        team_dir=loaded.snapshot.team_dir,
        manager_state=loaded.manager_state,
        options=replace(options, query=room_text_query_base),
    )
    room_text_counts: Dict[str, int] = {}
    room_text_latest_rank: Dict[str, int] = {}
    room_text_latest_at: Dict[str, str] = {}
    for row_index, row in enumerate(room_text_count_rows):
        for token in _history_row_room_text_tokens(row):
            room_text_counts[token] = int(room_text_counts.get(token, 0) or 0) + 1
            room_text_latest_rank.setdefault(token, row_index)
            room_text_latest_at.setdefault(token, str(getattr(row, "at", "") or "").strip())
    room_text_values = _ordered_room_text_values(room_text_counts, latest_rank=room_text_latest_rank)
    room_text_hints = {
        value: _room_text_hint(
            value,
            latest_rank=room_text_latest_rank,
            latest_at=room_text_latest_at,
        )
        for value in room_text_values
    }
    fallback_pressure_key = (
        str(loaded.snapshot.control_summary.server_guard_preview_groups[0].key).strip().lower()
        if loaded.snapshot.control_summary.server_guard_preview_groups
        else "other"
    )
    return loaded.snapshot, HistorySearchPageDTO(
        query=options.query,
        compact_mode=bool(compact_mode),
        chat_event_filter=chat_event_filter,
        chat_event_query_base=chat_event_query_base,
        chat_mode_filter=chat_mode_filter,
        chat_mode_query_base=chat_mode_query_base,
        room_kind_filter=room_kind_filter,
        room_kind_query_base=room_kind_query_base,
        room_actor_filter=room_actor_filter,
        room_actor_query_base=room_actor_query_base,
        room_text_filters=room_text_filters,
        room_text_query_base=room_text_query_base,
        chat_filter=options.chat_filter,
        project_filter=options.project_filter,
        since_label=options.since_label,
        scope=options.scope,
        limit=options.limit,
        total_rows=len(rows),
        chat_event_counts=chat_event_counts,
        chat_mode_counts=chat_mode_counts,
        room_kind_counts=room_kind_counts,
        room_kind_values=room_kind_values,
        room_actor_counts=room_actor_counts,
        room_actor_values=room_actor_values,
        room_text_counts=room_text_counts,
        room_text_values=room_text_values,
        room_text_hints=room_text_hints,
        rows=[
            HistorySearchRowDTO(
                at=row.at,
                scope=row.scope,
                source=row.source,
                chat_id=getattr(row, "chat_id", ""),
                chat_mode=getattr(row, "chat_mode", ""),
                room=getattr(row, "room", ""),
                actor=getattr(row, "actor", ""),
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
                summary_highlight_html=_history_row_summary_highlight_html(
                    row,
                    room_text_filters=room_text_filters,
                    room_kind_filter=room_kind_filter,
                    room_actor_filter=room_actor_filter,
                ),
                detail_highlight_html=_history_row_detail_highlight_html(
                    row,
                    room_text_filters=room_text_filters,
                    room_kind_filter=room_kind_filter,
                    room_actor_filter=room_actor_filter,
                ),
                planning_compact_summary=getattr(row, "planning_compact_summary", ""),
                subagent_contract_summary=getattr(row, "subagent_contract_summary", ""),
                subagent_evidence_summary=getattr(row, "subagent_evidence_summary", ""),
                subagent_artifact_path=getattr(row, "subagent_artifact_path", ""),
                subagent_gate_summary=getattr(row, "subagent_gate_summary", ""),
                approved_plan_summary=getattr(row, "approved_plan_summary", ""),
                followup_hint=row.followup_hint,
                raw_ref=row.raw_ref,
                pressure_kind_label=_history_row_pressure_label(row, fallback_key=fallback_pressure_key),
                pressure_kind_note=_history_row_pressure_note(row, fallback_key=fallback_pressure_key),
            )
            for row in rows
        ],
    )


def _history_row_pressure_key(row: history_search.HistoryRow, *, fallback_key: str = "other") -> str:
    blob = " ".join(
        str(token or "").strip().lower()
        for token in (
            getattr(row, "summary", ""),
            getattr(row, "detail", ""),
            getattr(row, "action", ""),
            getattr(row, "reason_code", ""),
            getattr(row, "followup_hint", ""),
            getattr(row, "raw_ref", ""),
        )
        if str(token or "").strip()
    )
    patterns = (
        ("queue", ("queue", "cleanup", "bgq-clean", "stale runtime", "stale queue")),
        ("codex", ("codex", "global-direct")),
        ("python", ("python", "package-rail", "package rail")),
        ("tmux", ("tmux", "review-rail", "review rail")),
        ("process", ("process", "analysis-rail", "analysis rail")),
    )
    for key, tokens in patterns:
        if any(token in blob for token in tokens):
            return key
    return str(fallback_key or "other").strip().lower() or "other"


def _history_row_pressure_label(row: history_search.HistoryRow, *, fallback_key: str = "other") -> str:
    policy = server_guard_pressure_policy(_history_row_pressure_key(row, fallback_key=fallback_key))
    return str(policy.get("label", "")).strip()


def _history_row_pressure_note(row: history_search.HistoryRow, *, fallback_key: str = "other") -> str:
    policy = server_guard_pressure_policy(_history_row_pressure_key(row, fallback_key=fallback_key))
    return str(policy.get("action_sentence", "")).strip() or str(policy.get("priority_link_note", "")).strip()


def _history_row_chat_event_kinds(row: history_search.HistoryRow) -> List[str]:
    blob = " ".join(
        str(token or "").strip().lower()
        for token in (
            getattr(row, "summary", ""),
            getattr(row, "detail", ""),
        )
        if str(token or "").strip()
    )
    kinds: List[str] = []
    if "chat_reply=" in blob or "chat_event:reply" in blob:
        kinds.append("reply")
    if "chat_room_change=" in blob or "chat_event:room_change" in blob:
        kinds.append("room_change")
    if "chat_event:session" in blob:
        kinds.append("session")
    return kinds


def _history_row_chat_mode(row: history_search.HistoryRow) -> str:
    return str(getattr(row, "chat_mode", "") or "").strip().lower()


def _history_row_room_kind(row: history_search.HistoryRow) -> str:
    if str(getattr(row, "scope", "") or "").strip().lower() != "room":
        return ""
    action = str(getattr(row, "action", "") or "").strip().lower()
    if not action.startswith("room_"):
        return ""
    return action.split("_", 1)[1].strip()


def _history_row_room_actor(row: history_search.HistoryRow) -> str:
    if str(getattr(row, "scope", "") or "").strip().lower() != "room":
        return ""
    return str(getattr(row, "actor", "") or "").strip().lower()


def _history_row_room_text_tokens(row: history_search.HistoryRow) -> List[str]:
    if str(getattr(row, "scope", "") or "").strip().lower() != "room":
        return []
    detail = str(getattr(row, "detail", "") or "").strip().lower()
    if not detail:
        return []
    values: List[str] = []
    seen: set[str] = set()
    for token in detail.split():
        if not token.startswith("room_text:"):
            continue
        value = token.split(":", 1)[1].strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _history_row_detail_highlight_html(
    row: history_search.HistoryRow,
    *,
    room_text_filters: List[str],
    room_kind_filter: str,
    room_actor_filter: str,
) -> str:
    return _history_row_text_highlight_html(
        str(getattr(row, "detail", "") or "").strip(),
        scope=str(getattr(row, "scope", "") or "").strip().lower(),
        room_text_filters=room_text_filters,
        room_kind_filter=room_kind_filter,
        room_actor_filter=room_actor_filter,
    )


def _history_row_summary_highlight_html(
    row: history_search.HistoryRow,
    *,
    room_text_filters: List[str],
    room_kind_filter: str,
    room_actor_filter: str,
) -> str:
    return _history_row_text_highlight_html(
        str(getattr(row, "summary", "") or "").strip(),
        scope=str(getattr(row, "scope", "") or "").strip().lower(),
        room_text_filters=room_text_filters,
        room_kind_filter=room_kind_filter,
        room_actor_filter=room_actor_filter,
    )


def _history_row_text_highlight_html(
    text: str,
    *,
    scope: str,
    room_text_filters: List[str],
    room_kind_filter: str,
    room_actor_filter: str,
) -> str:
    if scope != "room":
        return ""
    if not text:
        return ""
    highlight_tokens = {
        str(value).strip()
        for value in room_text_filters
        if str(value).strip()
    }
    if str(room_kind_filter or "").strip():
        highlight_tokens.add(str(room_kind_filter).strip())
    if str(room_actor_filter or "").strip():
        highlight_tokens.add(str(room_actor_filter).strip())
    if not highlight_tokens:
        return ""
    highlighted = html.escape(text)
    for token in sorted(highlight_tokens, key=len, reverse=True):
        pattern = re.compile(re.escape(html.escape(token)), re.IGNORECASE)
        highlighted = pattern.sub(lambda match: f"<mark>{match.group(0)}</mark>", highlighted)
    return highlighted


def _ordered_room_kind_values(counts: Dict[str, int]) -> List[str]:
    preferred = ["note", "reply", "decision"]
    seen: set[str] = set()
    ordered: List[str] = []
    for value in preferred:
        if int(counts.get(value, 0) or 0) <= 0:
            continue
        ordered.append(value)
        seen.add(value)
    for value in sorted(str(key).strip().lower() for key in counts.keys() if str(key).strip()):
        if value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _ordered_room_actor_values(counts: Dict[str, int]) -> List[str]:
    preferred = ["operator", "codex", "planner"]
    seen: set[str] = set()
    ordered: List[str] = []
    for value in preferred:
        if int(counts.get(value, 0) or 0) <= 0:
            continue
        ordered.append(value)
        seen.add(value)
    for value in sorted(str(key).strip().lower() for key in counts.keys() if str(key).strip()):
        if value in seen:
            continue
        ordered.append(value)
        seen.add(value)
    return ordered


def _ordered_room_text_values(
    counts: Dict[str, int],
    *,
    latest_rank: Dict[str, int] | None = None,
    limit: int = 3,
) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    adaptive_limit = limit
    ranked_counts = sorted(
        (int(count or 0) for count in counts.values() if int(count or 0) > 0),
        reverse=True,
    )
    if len(ranked_counts) >= 4 and ranked_counts[3] >= 2:
        adaptive_limit = max(adaptive_limit, 4)
    if len(ranked_counts) >= 5 and ranked_counts[4] >= 2:
        adaptive_limit = max(adaptive_limit, 5)
    for value in _ROOM_TEXT_SHORTCUT_VALUES:
        if int(counts.get(value, 0) or 0) <= 0:
            continue
        ordered.append(value)
        seen.add(value)
        if len(ordered) >= adaptive_limit:
            return ordered
    dynamic_values = sorted(
        (
            (
                str(key).strip().lower(),
                int(count or 0),
                int((latest_rank or {}).get(str(key).strip().lower(), 10**6)),
            )
            for key, count in counts.items()
            if str(key).strip()
        ),
        key=lambda item: (-item[1], item[2], item[0]),
    )
    for value, _count, _latest in dynamic_values:
        if value in seen:
            continue
        ordered.append(value)
        seen.add(value)
        if len(ordered) >= adaptive_limit:
            break
    return ordered


def _short_room_history_at(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    if "T" in token:
        time_token = token.split("T", 1)[1]
        if len(time_token) >= 5:
            return time_token[:5]
    if len(token) >= 16:
        return token[:16]
    return token


def _room_text_hint(
    value: str,
    *,
    latest_rank: Dict[str, int],
    latest_at: Dict[str, str],
) -> str:
    rank = int(latest_rank.get(str(value).strip().lower(), 10**6))
    latest_label = _short_room_history_at(latest_at.get(str(value).strip().lower(), ""))
    if rank == 0:
        prefix = "latest"
    elif rank <= 2:
        prefix = "recent"
    else:
        prefix = "older"
    return f"{prefix} {latest_label}".strip()
