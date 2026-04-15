#!/usr/bin/env python3
"""Chat console execution helpers for dashboard actions."""

from __future__ import annotations

import subprocess
from typing import Dict, Tuple

import aoe_tg_chat_state as chat_state
import aoe_tg_request_contract as request_contract

from control_dashboard_action_exec_shared import _load_dashboard_manager_state, _load_gateway_main_module
from control_dashboard_common import DashboardAppConfig, _dashboard_paths, _json


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

    paths, manager_state = _load_dashboard_manager_state(config)
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

    return _json(
        {
            "ok": True,
            "status": "completed",
            "path": str(spec.get("path", "")).strip() or "/control/actions/chat/session-update",
            "source_command": (
                f"chat-session {chat_id} default={current_default_mode} pending={current_pending_mode} "
                f"room={current_room} lang={current_lang} report={current_report_level}"
            ),
            "chat_id": chat_id,
            "default_mode": current_default_mode,
            "pending_mode": current_pending_mode,
            "room": current_room,
            "lang": current_lang,
            "report_level": current_report_level,
            "next_step": f"/control/chat?chat={chat_id}",
            "remediation": "-",
            "outcome": {
                "kind": "chat_session_update",
                "status": "completed",
                "reason_code": "-",
                "detail": f"default={current_default_mode} pending={current_pending_mode} room={current_room}",
            },
        },
        status=200,
    )
