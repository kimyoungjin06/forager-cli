#!/usr/bin/env python3
"""Chat console execution helpers for dashboard actions."""

from __future__ import annotations

import subprocess
from typing import Dict, Tuple

import aoe_tg_request_contract as request_contract

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
