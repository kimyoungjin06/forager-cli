#!/usr/bin/env python3
"""Chat console execution helpers for dashboard actions."""

from __future__ import annotations

import subprocess
import json
from typing import Dict, Tuple

import aoe_tg_harness_authoring_adapter as harness_authoring_adapter
import aoe_tg_chat_state as chat_state
import aoe_tg_request_contract as request_contract
import aoe_tg_task_state as task_state
import aoe_tg_task_view as task_view

from control_dashboard_action_exec_shared import _load_dashboard_manager_state, _load_gateway_main_module
from control_dashboard_common import DashboardAppConfig, _dashboard_paths, _json
from control_dashboard_server_guard import (
    server_guard_pressure_policy,
    server_guard_should_auto_run_general_research,
)


def _resolve_chat_project(manager_state: Dict[str, object], project_ref: str) -> tuple[str, str, Dict[str, object]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    token = str(project_ref or "").strip()
    active_key = str(manager_state.get("active", "")).strip()
    if token:
        token_upper = token.upper()
        token_lower = token.lower()
        for key, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            if str(key).strip().lower() == token_lower:
                return str(key).strip(), str(entry.get("project_alias", "")).strip(), entry
            if str(entry.get("project_alias", "")).strip().upper() == token_upper:
                return str(key).strip(), str(entry.get("project_alias", "")).strip(), entry
    if active_key:
        active_entry = projects.get(active_key) if isinstance(projects.get(active_key), dict) else {}
        if isinstance(active_entry, dict):
            return active_key, str(active_entry.get("project_alias", "")).strip(), active_entry
    return "", "", {}


def _resolve_chat_task_request(project_entry: Dict[str, object], raw_ref: str) -> str:
    token = str(raw_ref or "").strip()
    if not token:
        return ""
    resolved = task_state.resolve_task_request_id(project_entry, token)
    if resolved and task_state.get_task_record(project_entry, resolved):
        return resolved
    return ""


def _chat_project_ref_from_room(room: str) -> str:
    token = str(room or "").strip()
    if not token:
        return ""
    head = token.split("/", 1)[0].strip()
    return head if head.upper().startswith("O") else ""


def _resolve_selected_chat_task(manager_state: Dict[str, object], chat_id: str, room: str) -> Dict[str, object]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    session_row = chat_state.get_chat_session_row(manager_state, chat_id, create=False)
    project_ref = _chat_project_ref_from_room(room)
    project_key, _project_alias, project_entry = _resolve_chat_project(manager_state, project_ref)
    if not project_key:
        active_key = str(manager_state.get("active", "")).strip()
        active_entry = projects.get(active_key) if isinstance(projects.get(active_key), dict) else {}
        if active_key and isinstance(active_entry, dict):
            project_key, project_entry = active_key, active_entry
    if not project_key or not isinstance(project_entry, dict):
        return {}
    selected_ref = chat_state.get_chat_selected_task_ref(manager_state, chat_id, project_key)
    selected_map = session_row.get("selected_task_refs") if isinstance(session_row.get("selected_task_refs"), dict) else {}
    if not selected_ref:
        selected_ref = str(selected_map.get("active", "")).strip()
    if not selected_ref and isinstance(selected_map, dict):
        for value in selected_map.values():
            candidate = str(value or "").strip()
            if candidate:
                selected_ref = candidate
                break
    request_id = _resolve_chat_task_request(project_entry, selected_ref)
    if not request_id:
        return {}
    record = task_state.get_task_record(project_entry, request_id)
    return record if isinstance(record, dict) else {}


def _resolve_selected_chat_task_context(
    manager_state: Dict[str, object],
    chat_id: str,
    room: str,
) -> tuple[Dict[str, object], Dict[str, object], str]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    session_row = chat_state.get_chat_session_row(manager_state, chat_id, create=False)
    project_ref = _chat_project_ref_from_room(room)
    project_key, _project_alias, project_entry = _resolve_chat_project(manager_state, project_ref)
    if not project_key:
        active_key = str(manager_state.get("active", "")).strip()
        active_entry = projects.get(active_key) if isinstance(projects.get(active_key), dict) else {}
        if active_key and isinstance(active_entry, dict):
            project_key, project_entry = active_key, active_entry
    if not project_key or not isinstance(project_entry, dict):
        return {}, {}, ""
    selected_ref = chat_state.get_chat_selected_task_ref(manager_state, chat_id, project_key)
    selected_map = session_row.get("selected_task_refs") if isinstance(session_row.get("selected_task_refs"), dict) else {}
    if not selected_ref:
        selected_ref = str(selected_map.get("active", "")).strip()
    if not selected_ref and isinstance(selected_map, dict):
        for value in selected_map.values():
            candidate = str(value or "").strip()
            if candidate:
                selected_ref = candidate
                break
    request_id = _resolve_chat_task_request(project_entry, selected_ref)
    if not request_id:
        return {}, project_entry, ""
    record = task_state.get_task_record(project_entry, request_id)
    return (record if isinstance(record, dict) else {}), project_entry, request_id


def _chat_send_command_text(*, mode: str, text: str) -> str:
    prompt = str(text or "").strip()
    token = str(mode or "").strip().lower() or "raw"
    if token == "direct":
        return f"/direct {prompt}"
    if token == "dispatch":
        return f"/dispatch {prompt}"
    if token == "room_post":
        return f"/room post {prompt}"
    if token == "room_use":
        return f"/room use {prompt}"
    return prompt


def _run_gateway_chat_send(argv: list[str], *, timeout_sec: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=max(30, int(timeout_sec)))


def _execute_chat_send_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    chat_id = str(payload.get("chat_id", "")).strip()
    raw_text = str(payload.get("text", "")).strip()
    mode = str(payload.get("mode", "")).strip().lower() or "raw"
    command_text = _chat_send_command_text(mode=mode, text=raw_text)
    paths = _dashboard_paths(config)
    argv = request_contract.build_gateway_simulation_command_argv(
        project_root=str(config.control_root),
        team_dir=str(paths.team_dir),
        manager_state_file=str(paths.manager_state_file),
        simulate_text=command_text,
        simulate_chat_id=chat_id,
        simulate_live=True,
    )
    argv.extend(
        [
            "--chat-aliases-file",
            str(paths.chat_aliases_file),
            "--allow-chat-ids",
            chat_id,
        ]
    )
    result = _run_gateway_chat_send(argv)
    stdout = str(result.stdout or "").strip()
    stderr = str(result.stderr or "").strip()
    reply_text = stdout or stderr or "-"
    reply_lines = [line for line in reply_text.splitlines() if str(line).strip()][:40]
    ok = result.returncode == 0
    return _json(
        {
            "ok": ok,
            "status": "completed" if ok else "failed",
            "path": str(spec.get("path", "")).strip() or "/control/actions/chat/send",
            "source_command": command_text,
            "chat_id": chat_id,
            "mode": mode,
            "reply_text": reply_text,
            "reply_lines": reply_lines,
            "gateway_returncode": int(result.returncode),
            "command_argv": argv,
            "next_step": "-" if ok else "/control/chat",
            "remediation": "" if ok else "inspect gateway stderr or retry with an explicit direct/dispatch mode",
            "outcome": {
                "kind": "chat_send",
                "status": "completed" if ok else "failed",
                "reason_code": "-" if ok else "gateway_chat_send_failed",
                "detail": reply_lines[0] if reply_lines else reply_text,
            },
        },
        status=200 if ok else 500,
    )


def _execute_chat_session_update_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    chat_id = str(payload.get("chat_id", "")).strip()
    default_mode = str(payload.get("default_mode", "")).strip().lower()
    pending_mode = str(payload.get("pending_mode", "")).strip().lower()
    room = str(payload.get("room", "")).strip()
    lang = str(payload.get("lang", "")).strip().lower()
    report_level = str(payload.get("report_level", "")).strip().lower()
    focus_badge = str(payload.get("focus_badge", "")).strip()
    server_guard_preset_label = str(payload.get("server_guard_preset_label", "")).strip()
    server_guard_pressure_kind = str(payload.get("server_guard_pressure_kind", "")).strip().lower()
    next_step = str(payload.get("next_step", "")).strip()
    remediation = str(payload.get("remediation", "")).strip()

    paths, manager_state = _load_dashboard_manager_state(config)
    previous_default_mode = chat_state.get_default_mode(manager_state, chat_id) or "off"
    previous_pending_mode = chat_state.get_pending_mode(manager_state, chat_id) or "none"
    previous_room = chat_state.get_chat_room(manager_state, chat_id) or "-"
    previous_lang = chat_state.get_chat_lang(manager_state, chat_id) or "-"
    previous_report_level = chat_state.get_chat_report_level(manager_state, chat_id) or "-"
    if default_mode in {"dispatch", "direct"}:
        chat_state.set_default_mode(manager_state, chat_id, default_mode)
    else:
        chat_state.clear_default_mode(manager_state, chat_id)
    if pending_mode in {"dispatch", "direct"}:
        chat_state.set_pending_mode(manager_state, chat_id, pending_mode)
    else:
        chat_state.clear_pending_mode(manager_state, chat_id)
    chat_state.set_chat_room(manager_state, chat_id, room)
    if lang in {"ko", "en"}:
        chat_state.set_chat_lang(manager_state, chat_id, lang)
    if report_level in {"short", "normal", "long"}:
        chat_state.set_chat_report_level(manager_state, chat_id, report_level)

    gateway_main = _load_gateway_main_module()
    gateway_main.save_manager_state(paths.manager_state_file, manager_state)

    current_room = chat_state.get_chat_room(manager_state, chat_id)
    current_default_mode = chat_state.get_default_mode(manager_state, chat_id) or "off"
    current_pending_mode = chat_state.get_pending_mode(manager_state, chat_id) or "none"
    current_lang = chat_state.get_chat_lang(manager_state, chat_id)
    current_report_level = chat_state.get_chat_report_level(manager_state, chat_id)
    selected_task, selected_task_entry, _selected_request_id = _resolve_selected_chat_task_context(
        manager_state,
        chat_id,
        current_room,
    )
    planning_bundle = task_view.planning_operator_bundle(selected_task)
    subagent_surface = harness_authoring_adapter.summarize_general_subagent_surface(
        str(selected_task_entry.get("team_dir", "")).strip() or paths.team_dir,
        entry=selected_task_entry,
        task=selected_task,
    )
    general_subagent_executed = False
    auto_subagent_allowed = (
        focus_badge == "server-guard"
        and server_guard_should_auto_run_general_research(server_guard_pressure_kind)
    )
    if auto_subagent_allowed and isinstance(selected_task, dict) and selected_task:
        ensured_surface = harness_authoring_adapter.ensure_general_subagent_support_surface(
            str(selected_task_entry.get("team_dir", "")).strip() or paths.team_dir,
            entry=selected_task_entry,
            task=selected_task,
        )
        if isinstance(ensured_surface, dict):
            subagent_surface = ensured_surface
            general_subagent_executed = bool(ensured_surface.get("executed", False))
    effective_next_step = next_step or f"/control/chat?chat={chat_id}"
    effective_source_command = (
        f"server-guard-preset:{server_guard_pressure_kind or '-'}:{chat_id}:{server_guard_preset_label}"
        if focus_badge == "server-guard" and server_guard_preset_label
        else (
            f"chat-session {chat_id} default={current_default_mode} pending={current_pending_mode} "
            f"room={current_room} lang={current_lang} report={current_report_level}"
        )
    )
    detail = f"default={current_default_mode} pending={current_pending_mode} room={current_room}"
    if focus_badge == "server-guard" and server_guard_preset_label:
        detail = f"{server_guard_preset_label} | {detail}"
    diff_parts = []
    if previous_default_mode != current_default_mode:
        diff_parts.append(f"default:{previous_default_mode}->{current_default_mode}")
    if previous_pending_mode != current_pending_mode:
        diff_parts.append(f"pending:{previous_pending_mode}->{current_pending_mode}")
    if previous_room != current_room:
        diff_parts.append(f"room:{previous_room}->{current_room}")
    if previous_lang != current_lang:
        diff_parts.append(f"lang:{previous_lang}->{current_lang}")
    if previous_report_level != current_report_level:
        diff_parts.append(f"report:{previous_report_level}->{current_report_level}")
    preset_diff_summary = " | ".join(diff_parts) if diff_parts else "no change"
    followup_actions = []
    if focus_badge == "server-guard" and server_guard_preset_label:
        policy = server_guard_pressure_policy(server_guard_pressure_kind)
        pressure_kind_label = str(policy.get("label", "")).strip()
        chat_action = {
            "label": "Open Chat Console",
            "href": effective_next_step if effective_next_step.startswith("/control/chat") else f"/control/chat?chat={chat_id}",
            "note": "inspect the selected chat session after applying the server-guard preset",
            "priority": "secondary",
            "pressure_kind_label": pressure_kind_label,
        }
        health_action = {
            "label": "Open Health View",
            "href": "/control/health/view",
            "note": "inspect host pressure after switching the chat rail",
            "priority": "secondary",
            "pressure_kind_label": pressure_kind_label,
        }
        audit_action = {
            "label": "Open Server Guard Audit",
            "href": "/control/audit?focus=server-guard",
            "note": "inspect the full server-guard action trail",
            "priority": "secondary",
            "pressure_kind_label": pressure_kind_label,
        }
        action_map = {"chat": chat_action, "health": health_action, "audit": audit_action}
        order = tuple(policy.get("followup_order", ("chat", "health", "audit")))
        primary_note = (
            str(policy.get("action_sentence", "")).strip()
            or str(policy.get("operator_sentence", "")).strip()
            or str(policy.get("priority_link_note", "")).strip()
        )
        primary_note = task_view.planning_operator_note(
            selected_task,
            notes=[primary_note],
        )
        first_key = next((token for token in order if token in action_map), "")
        for token in order:
            if token not in action_map:
                continue
            row = dict(action_map[token])
            if token == first_key:
                row["priority"] = "primary"
                if primary_note:
                    row["note"] = primary_note
            followup_actions.append(row)
        if not followup_actions:
            followup_actions = [chat_action, health_action, audit_action]
        selected_task_ref = (
            str(selected_task.get("short_id", "")).strip()
            or str(selected_task.get("alias", "")).strip()
            or str(_selected_request_id or "").strip()
        )
        if selected_task_ref:
            support_note = task_view.planning_operator_note(
                selected_task,
                notes=["materialize bounded general_research evidence for the selected task before changing dispatch or apply state"],
            )
            followup_actions.append(
                {
                    "label": "Run Support Research",
                    "path": "/control/actions/task/subagent-support-run",
                    "payload_json": json.dumps({"task_ref": selected_task_ref}, ensure_ascii=False, separators=(",", ":")),
                    "command": f"/task {selected_task_ref} | general-research-support",
                    "mode": "safe",
                    "note": support_note,
                    "priority": "secondary",
                    "pressure_kind_label": pressure_kind_label,
                }
            )

    return _json(
        {
            "ok": True,
            "status": "completed",
            "path": str(spec.get("path", "")).strip() or "/control/actions/chat/session-update",
            "source_command": effective_source_command,
            "chat_id": chat_id,
            "default_mode": current_default_mode,
            "pending_mode": current_pending_mode,
            "room": current_room,
            "lang": current_lang,
            "report_level": current_report_level,
            "focus_badge": focus_badge,
            "server_guard_preset_label": server_guard_preset_label,
            "server_guard_pressure_kind": server_guard_pressure_kind,
            "chat_preset_diff_summary": preset_diff_summary,
            "planning_compact": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "planning_compact_summary": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "planning_lanes": str(planning_bundle.get("planning_lanes", "")).strip() or "-",
            "planning_lanes_summary": str(planning_bundle.get("planning_lanes", "")).strip() or "-",
            "approved_plan_gate": str(planning_bundle.get("approved_plan_gate", "")).strip() or "-",
            "approved_plan_gate_summary": str(planning_bundle.get("approved_plan_gate", "")).strip() or "-",
            "planner_lane": str(planning_bundle.get("planner_lane", "")).strip() or "-",
            "planner_lane_summary": str(planning_bundle.get("planner_lane", "")).strip() or "-",
            "critic_lane": str(planning_bundle.get("critic_lane", "")).strip() or "-",
            "critic_lane_summary": str(planning_bundle.get("critic_lane", "")).strip() or "-",
            "approved_plan": str(planning_bundle.get("approved_plan", "")).strip() or "-",
            "approved_plan_summary": str(planning_bundle.get("approved_plan", "")).strip() or "-",
            "subagent_contract_summary": str(subagent_surface.get("summary", "")).strip() or "-",
            "subagent_evidence_summary": str(subagent_surface.get("artifact_summary", "")).strip() or "-",
            "subagent_artifact_path": str(subagent_surface.get("artifact_path", "")).strip() or "-",
            "subagent_gate_summary": str(subagent_surface.get("gate_summary", "")).strip() or "-",
            "general_subagent_executed": general_subagent_executed,
            "actions": followup_actions,
            "reply_text": (
                f"{server_guard_preset_label + chr(10) if focus_badge == 'server-guard' and server_guard_preset_label else ''}"
                f"session updated\n"
                f"- default_mode: {current_default_mode}\n"
                f"- pending_mode: {current_pending_mode}\n"
                f"- room: {current_room}\n"
                f"- lang: {current_lang}\n"
                f"- report: {current_report_level}"
            ),
            "next_step": effective_next_step,
            "remediation": remediation or "-",
            "outcome": {
                "kind": "chat_session_update",
                "status": "completed",
                "reason_code": "-",
                "detail": detail,
            },
        },
        status=200,
    )


def _execute_chat_session_select_task_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    chat_id = str(payload.get("chat_id", "")).strip()
    project_ref = str(payload.get("project_ref", "")).strip()
    task_ref = str(payload.get("task_ref", "")).strip()

    paths, manager_state = _load_dashboard_manager_state(config)
    project_key, project_alias, project_entry = _resolve_chat_project(manager_state, project_ref)
    if not project_key or not isinstance(project_entry, dict):
        return _json(
            {
                "ok": False,
                "status": "blocked",
                "path": str(spec.get("path", "")).strip() or "/control/actions/chat/session-select-task",
                "chat_id": chat_id,
                "project_ref": project_ref or "-",
                "error": "project_not_found",
                "message": "project_ref did not resolve to a known runtime",
                "next_step": f"/control/chat?chat={chat_id}",
            },
            status=400,
        )

    resolved_request_id = _resolve_chat_task_request(project_entry, task_ref) if task_ref else ""
    if task_ref and not resolved_request_id:
        return _json(
            {
                "ok": False,
                "status": "blocked",
                "path": str(spec.get("path", "")).strip() or "/control/actions/chat/session-select-task",
                "chat_id": chat_id,
                "project_ref": project_alias or project_key,
                "task_ref": task_ref,
                "error": "task_not_found",
                "message": "task_ref did not resolve for the selected runtime",
                "next_step": f"/control/chat?chat={chat_id}",
            },
            status=400,
        )

    chat_state.set_chat_selected_task_ref(manager_state, chat_id, project_key, resolved_request_id)

    gateway_main = _load_gateway_main_module()
    gateway_main.save_manager_state(paths.manager_state_file, manager_state)

    selected_task_ref = chat_state.get_chat_selected_task_ref(manager_state, chat_id, project_key)
    recent_task_refs = chat_state.get_chat_recent_task_refs(manager_state, chat_id, project_key)
    detail = f"{project_alias or project_key}:{selected_task_ref or '-'}"
    return _json(
        {
            "ok": True,
            "status": "completed",
            "path": str(spec.get("path", "")).strip() or "/control/actions/chat/session-select-task",
            "source_command": f"chat-session-select-task:{chat_id}:{project_alias or project_key}:{selected_task_ref or '-'}",
            "chat_id": chat_id,
            "project_key": project_key,
            "project_alias": project_alias or project_key,
            "selected_task_ref": selected_task_ref or "-",
            "recent_task_refs": recent_task_refs,
            "reply_text": (
                f"selected task updated\n"
                f"- project: {project_alias or project_key}\n"
                f"- selected_task: {selected_task_ref or '-'}\n"
                f"- recent: {', '.join(recent_task_refs) if recent_task_refs else '-'}"
            ),
            "next_step": f"/control/chat?chat={chat_id}",
            "remediation": "-" if selected_task_ref else "set a task_ref to pin a task for this chat session",
            "outcome": {
                "kind": "chat_session_select_task",
                "status": "completed",
                "reason_code": "-",
                "detail": detail,
            },
        },
        status=200,
    )
