#!/usr/bin/env python3
"""Read-only Control Dashboard regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import base64
import os
import json
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
DASH_DIR = ROOT / "scripts" / "dashboard"
for path in (GW_DIR, DASH_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pytest  # noqa: E402
from _gateway_test_support import gw  # noqa: E402
import aoe_tg_action_audit as action_audit  # noqa: E402
import aoe_tg_background_runs as background_runs  # noqa: E402
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter  # noqa: E402
import aoe_tg_operator_summary as operator_summary  # noqa: E402
import aoe_tg_orch_task_handlers as orch_task_handlers  # noqa: E402
from aoe_tg_request_contract import build_background_run_ticket  # noqa: E402
import aoe_tg_runtime_read as runtime_read  # noqa: E402
import aoe_tg_subagent_contract as subagent_contract  # noqa: E402
import control_dashboard as dashboard_app  # noqa: E402
import control_dashboard_audit as dashboard_action_audit  # noqa: E402
import control_dashboard_action_exec_chat as chat_exec  # noqa: E402
import control_dashboard_action_exec_retry as retry_exec  # noqa: E402
import control_dashboard_action_exec_runtime as runtime_exec  # noqa: E402
import control_dashboard_server_guard as server_guard  # noqa: E402
import control_dashboard_state as dashboard_state  # noqa: E402
import nightly_session_summary as nightly_summary  # noqa: E402
import aoe_tg_document_registry as document_registry  # noqa: E402
import aoe_tg_operator_preferences as operator_preferences  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402
from _dashboard_planning_compat import (  # noqa: E402
    LEGACY_PLANNING_REVIEW_SUMMARY,
    legacy_planning_review_payload,
    rewrite_latest_nightly_runtime_with_legacy_planning_review_key,
)


class _DashboardActionFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key): str(value or "") for key, value in attrs}
        if tag == "form":
            self._current = {
                "action": attr_map.get("action", ""),
                "method": attr_map.get("method", ""),
                "attrs": attr_map,
                "inputs": {},
            }
            return
        if tag != "input" or self._current is None:
            return
        name = attr_map.get("name", "")
        if not name:
            return
        inputs = self._current.setdefault("inputs", {})
        if not isinstance(inputs, dict):
            return
        value = attr_map.get("value", "")
        current = inputs.get(name)
        if current is None:
            inputs[name] = value
        elif isinstance(current, list):
            current.append(value)
        else:
            inputs[name] = [current, value]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _dashboard_action_forms(html: str) -> list[dict[str, object]]:
    parser = _DashboardActionFormParser()
    parser.feed(html)
    return [
        form
        for form in parser.forms
        if isinstance(form.get("attrs"), dict) and "data-dashboard-action" in form["attrs"]
    ]


def _dashboard_action_form(html: str, *, command: str, action: str) -> dict[str, object]:
    matches = []
    for form in _dashboard_action_forms(html):
        attrs = form.get("attrs") if isinstance(form.get("attrs"), dict) else {}
        if attrs.get("data-action-command") == command and form.get("action") == action:
            matches.append(form)
    assert len(matches) == 1
    return matches[0]


def _submit_dashboard_action_form(
    form: dict[str, object],
    *,
    config: dashboard_app.DashboardAppConfig,
) -> tuple[int, dict[str, str], bytes]:
    inputs = form.get("inputs") if isinstance(form.get("inputs"), dict) else {}
    return dashboard_app.build_dashboard_action_response(
        str(form.get("action", "")),
        body=json.dumps(inputs).encode("utf-8"),
        content_type="application/json",
        config=config,
    )


class _CdpWebSocket:
    def __init__(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 80)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        self._socket = socket.create_connection((host, port), timeout=5)
        self._socket.settimeout(5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._socket.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._socket.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"DevTools websocket upgrade failed: {response[:160]!r}")
        self._next_id = 0

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=8)
        except Exception:
            pass
        try:
            self._socket.close()
        except Exception:
            pass

    def command(self, method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        self._next_id += 1
        message_id = self._next_id
        self._send_text(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._recv_text())
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"DevTools command failed: {message['error']}")
                return message

    def _send_text(self, text: str) -> None:
        self._send_frame(text.encode("utf-8"), opcode=1)

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, length: int) -> bytes:
        chunks = []
        remaining = length
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise RuntimeError("DevTools websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return payload.decode("utf-8")
            if opcode == 8:
                raise RuntimeError("DevTools websocket closed")
            if opcode == 9:
                self._send_frame(payload, opcode=10)


def _find_chrome_for_dashboard_smoke() -> str:
    env_path = os.environ.get("AOE_DASHBOARD_CHROME", "").strip()
    candidates = [env_path] if env_path else []
    candidates.extend(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"])
    for candidate in candidates:
        if not candidate:
            continue
        found = shutil.which(candidate) if "/" not in candidate else candidate
        if found and Path(found).exists():
            return found
    pytest.skip("Chrome/Chromium is not available for dashboard browser smoke")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_chrome_cdp_page(chrome_path: str, target_url: str, tmp_path: Path) -> tuple[subprocess.Popen[str], _CdpWebSocket]:
    profile_dir = tmp_path / "chrome-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    debugging_port = _free_tcp_port()
    command = [
        chrome_path,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        f"--remote-debugging-port={debugging_port}",
        f"--user-data-dir={profile_dir}",
        "about:blank",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    http_base = f"http://127.0.0.1:{debugging_port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chrome exited early with code {process.returncode}")
        try:
            with urlopen(f"{http_base}/json/version", timeout=0.5):
                break
        except Exception:
            time.sleep(0.05)
    else:
        process.terminate()
        raise RuntimeError("Chrome did not expose a DevTools endpoint")
    new_target = f"{http_base}/json/new?{quote(target_url, safe='')}"
    for method in ("PUT", "GET"):
        try:
            request = Request(new_target, method=method) if method == "PUT" else new_target
            with urlopen(request, timeout=5) as response:
                target = json.loads(response.read().decode("utf-8"))
            break
        except Exception:
            if method == "GET":
                process.terminate()
                raise
    cdp = _CdpWebSocket(str(target["webSocketDebuggerUrl"]))
    cdp.command("Page.navigate", {"url": target_url})
    return process, cdp


def _cdp_eval(cdp: _CdpWebSocket, expression: str) -> object:
    response = cdp.command(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if isinstance(result, dict) and result.get("exceptionDetails"):
        raise AssertionError(result["exceptionDetails"])
    remote = result.get("result") if isinstance(result.get("result"), dict) else {}
    return remote.get("value")


def _wait_for_cdp_eval(cdp: _CdpWebSocket, expression: str, *, timeout: float = 5.0) -> object:
    deadline = time.time() + timeout
    last_value: object = None
    while time.time() < deadline:
        last_value = _cdp_eval(cdp, expression)
        if last_value:
            return last_value
        time.sleep(0.05)
    raise AssertionError(f"condition did not become truthy: {expression!r}; last={last_value!r}")


def _build_runtime(control_root: Path) -> tuple[Path, Path, Path]:
    team_dir = control_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state_file = team_dir / "orch_manager_state.json"

    project_root = control_root / "Alpha"
    project_team_dir = project_root / ".aoe-team"
    project_team_dir.mkdir(parents=True, exist_ok=True)
    (project_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (project_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")

    state = gw.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state["project_lock"] = {}
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "runtime alpha",
        "last_request_id": "REQ-1",
        "tasks": {
            "REQ-1": gw.sanitize_task_record(
                {
                    "request_id": "REQ-1",
                    "short_id": "T-001",
                    "alias": "analysis-check",
                    "prompt": "Summarize findings and highlight weak spots.",
                    "status": "running",
                    "stage": "planning",
                    "roles": ["Codex-Analyst", "Claude-Analyst", "Codex-Reviewer", "Claude-Reviewer"],
                    "verifier_roles": ["Codex-Reviewer", "Claude-Reviewer"],
                    "phase1_mode": "ensemble",
                    "phase1_rounds": 3,
                    "phase1_providers": ["codex", "claude"],
                    "phase1_current_phase": "planning",
                    "phase1_current_round": 1,
                    "phase1_current_total_rounds": 3,
                    "phase1_role_preset": "analysis",
                    "phase2_team_preset": "analysis",
                    "backend": "autogen_core",
                    "backend_profile": "sandbox",
                    "backend_verdict": "fail",
                    "backend_contract": "drift",
                    "backend_contract_note": "contract gaps: expected work execution role for preset",
                    "execution_brief_status": "underspecified",
                    "execution_brief_summary": "underspecified | do=reports/summary.md | blocked=acceptance_gap",
                    "execution_brief_executable_slice": ["reports/summary.md"],
                    "execution_brief_blocked_slice": ["acceptance_gap"],
                    "execution_brief_operator_decision": "confirm acceptance scope before off-desk execution",
                    "job_contract_status": "ready",
                    "job_contract_planning_mode": "standard",
                    "job_contract_summary": "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1",
                    "job_contract_goal": "Summarize findings and highlight weak spots with evidence.",
                    "job_contract_scope": ["reports/summary.md"],
                    "job_contract_non_goals": ["publish final report"],
                    "job_contract_risks": ["evidence gaps may require manual follow-up"],
                    "job_contract_acceptance_checks": ["findings are supported by concrete evidence"],
                    "job_contract_artifacts_to_touch": ["reports/summary.md"],
                    "job_contract_rollback_hint": "limit mutations to the declared summary artifact",
                    "debug_packet_state": "active",
                    "debug_packet_summary": "state=active | symptom=review_retry_requested | next=/task T-001",
                    "debug_packet_symptom": "review_retry_requested",
                    "debug_packet_root_cause": "review lane requested a targeted rerun",
                    "debug_packet_evidence": ["exec_critic=retry", "lane=L1"],
                    "debug_packet_failed_attempt": "background run BGT-001 is awaiting review",
                    "debug_packet_next_step": "/task T-001",
                    "phase_checkpoint_status": "active",
                    "phase_checkpoint_current_phase": "verify",
                    "phase_checkpoint_summary": "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready",
                    "phase_checkpoint_rows": [
                        "plan=done|note=approved plan",
                        "implement=done|note=execution complete",
                        "verify=active|note=review retry ready",
                        "handoff=ready|note=handoff ready",
                    ],
                    "background_run_ticket_id": "BGT-001",
                    "background_run_status": "running",
                    "background_run_runner_target": "local_background",
                    "background_run_launch_mode": "offdesk_manual",
                    "background_run_runtime_handle": "aoe_bg_bgt_001",
                    "background_run_runtime_summary": "tmux_session=aoe_bg_bgt_001",
                    "background_run_launch_spec_summary": "gateway_dispatch | mode=in_process_callback | entry=aoe-telegram-gateway | externalizable=no",
                    "background_run_evidence_bundle": "status=pending | outcome=awaiting_review",
                    "background_run_evidence_artifacts": ["review_evidence/git_diff_scope.md"],
                    "updated_at": "2026-03-16T10:00:00+09:00",
                    "created_at": "2026-03-16T09:55:00+09:00",
                    "plan": {
                        "summary": "analysis plan",
                        "subtasks": [
                            {
                                "id": "S1",
                                "owner_role": "Codex-Analyst",
                                "title": "Refresh findings summary",
                            }
                        ],
                        "evidence_required": [
                            "Findings are summarized with concrete evidence.",
                            "Open questions or weak spots are called out explicitly.",
                        ],
                        "meta": {
                            "phase1_role_preset": "analysis",
                            "phase2_team_preset": "analysis",
                            "phase2_team_spec": {
                                "execution_groups": [
                                    {"role": "Codex-Analyst"},
                                    {"role": "Claude-Analyst"},
                                ],
                                "review_groups": [
                                    {"role": "Codex-Reviewer"},
                                    {"role": "Claude-Reviewer"},
                                ],
                                "critic_role": "Codex-Reviewer",
                                "integration_role": "Codex-Analyst",
                            },
                            "phase2_execution_plan": {
                                "execution_lanes": [
                                    {"lane_id": "L1", "role": "Codex-Analyst"},
                                    {"lane_id": "L2", "role": "Claude-Analyst"},
                                ],
                                "review_lanes": [
                                    {"lane_id": "R1", "role": "Codex-Reviewer"},
                                    {"lane_id": "R2", "role": "Claude-Reviewer"},
                                ],
                            },
                        },
                    },
                    "plan_critic": {
                        "approved": True,
                        "issues": [],
                        "recommendations": ["ready for targeted retry"],
                    },
                    "plan_review_count": 3,
                    "plan_convergence_status": "ready",
                    "plan_gate_passed": True,
                    "lane_states": {
                        "execution": [
                            {
                                "lane_id": "L1",
                                "role": "Codex-Analyst",
                                "status": "running",
                                "subtask_ids": ["S1"],
                                "touched_files": ["reports/summary.md", "src/analysis.py"],
                            },
                            {
                                "lane_id": "L2",
                                "role": "Claude-Analyst",
                                "status": "pending",
                                "subtask_ids": ["S2"],
                            },
                        ],
                        "review": [
                            {
                                "lane_id": "R1",
                                "role": "Codex-Reviewer",
                                "kind": "verifier",
                                "status": "waiting_on_dependencies",
                                "depends_on": ["L1"],
                                "waiting_on": ["L1"],
                                "reason": "waiting on execution lane(s): L1",
                                "verdict": "retry",
                                "action": "rerun",
                                "touched_files": ["reports/summary.md", "docs/review.md"],
                            },
                            {
                                "lane_id": "R2",
                                "role": "Claude-Reviewer",
                                "kind": "verifier",
                                "status": "pending",
                                "depends_on": ["L2"],
                            },
                        ],
                        "summary": {
                            "execution": {"running": 1},
                            "review": {"waiting_on_dependencies": 1},
                            "review_verdicts": {"retry": 1},
                        }
                    },
                    "exec_critic": {
                        "verdict": "retry",
                        "rerun_execution_lane_ids": ["L1"],
                        "rerun_review_lane_ids": ["R1"],
                    },
                    "result": {
                        "backend": "autogen_core",
                        "backend_profile": "sandbox",
                        "backend_verdict": "fail",
                        "backend_contract": "drift",
                        "backend_contract_note": "contract gaps: expected work execution role for preset",
                    },
                    "context": {
                        "project_key": "alpha",
                        "project_alias": "O2",
                        "task_short_id": "T-001",
                    },
                },
                "REQ-1",
            ),
            "REQ-2": gw.sanitize_task_record(
                {
                    "request_id": "REQ-2",
                    "short_id": "T-002",
                    "alias": "analysis-followup",
                    "prompt": "Close out the completed findings summary.",
                    "status": "completed",
                    "stage": "completed",
                    "roles": ["Codex-Analyst", "Codex-Reviewer"],
                    "verifier_roles": ["Codex-Reviewer"],
                    "phase1_role_preset": "analysis",
                    "phase2_team_preset": "analysis",
                    "updated_at": "2026-03-16T09:40:00+09:00",
                    "created_at": "2026-03-16T09:20:00+09:00",
                    "plan": {
                        "summary": "completed analysis followup",
                        "meta": {
                            "phase1_role_preset": "analysis",
                            "phase2_team_preset": "analysis",
                            "phase2_team_spec": {
                                "execution_groups": [{"role": "Codex-Analyst"}],
                                "review_groups": [{"role": "Codex-Reviewer"}],
                                "critic_role": "Codex-Reviewer",
                                "integration_role": "Codex-Analyst",
                            },
                            "phase2_execution_plan": {
                                "execution_lanes": [{"lane_id": "L1", "role": "Codex-Analyst"}],
                                "review_lanes": [{"lane_id": "R1", "role": "Codex-Reviewer"}],
                            },
                        },
                    },
                    "context": {
                        "project_key": "alpha",
                        "project_alias": "O2",
                        "task_short_id": "T-002",
                    },
                },
                "REQ-2",
            ),
        },
        "task_alias_index": {"T001": "REQ-1", "ANALYSISCHECK": "REQ-1", "T002": "REQ-2", "ANALYSISFOLLOWUP": "REQ-2"},
        "task_seq": 2,
        "todos": [{"id": "TODO-1", "summary": "Review findings", "priority": "P1", "status": "running"}],
        "todo_seq": 1,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-03-16T09:50:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-03-16T09:00:00+09:00",
        "updated_at": "2026-03-16T10:00:00+09:00",
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (team_dir / "auto_scheduler.json").write_text(
        json.dumps({"enabled": True, "mode": "fanout", "offdesk_enabled": True}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (team_dir / "provider_capacity.json").write_text(
        json.dumps(
            {
                "task_count": 1,
                "project_count": 1,
                "provider_counts": {"claude": 1},
                "next_retry_at": "2026-03-16T10:30:00+09:00",
                "next_retry_target": {
                    "alias": "O2",
                    "task_ref": "T-001",
                    "providers": "claude",
                    "degraded": "claude_rate_limit->codex",
                },
                "recovery_repeat_count": 1,
                "recovery_repeat_last_at": "2026-03-16T09:40:00+09:00",
                "recovery_repeat": {"summary": "O2 repeated cooldown"},
                "providers": {
                    "claude": {
                        "blocked_count": 1,
                        "project_count": 1,
                        "next_retry_at": "2026-03-16T10:30:00+09:00",
                        "cooldown_level": "elevated",
                        "retry_wait_bucket": "medium",
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    logs_dir = team_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "gateway_events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-03-16T09:58:00+09:00",
                        "event": "command_resolved",
                        "status": "accepted",
                        "detail": "cmd=offdesk action=offdesk_review class=status trace=selected=offdesk_review; matched=timing:퇴근 전,review:검토; safe_mode=prefer_control_review_over_dispatch",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-16T09:59:00+09:00",
                        "event": "command_resolved",
                        "status": "accepted",
                        "detail": "cmd=offdesk action=offdesk_prepare class=status trace=selected=offdesk_prepare; matched=timing:오늘 밤,prepare:점검; safe_mode=prefer_control_review_over_dispatch",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    operator_summary.save_latest_command_resolution(
        team_dir,
        command="offdesk",
        action="offdesk_prepare",
        intent_class="status",
        trace="selected=offdesk_prepare; matched=timing:오늘 밤,prepare:점검; safe_mode=prefer_control_review_over_dispatch",
        recorded_at="2026-03-16T09:59:00+09:00",
    )
    action_audit_dir = team_dir / "dashboard"
    action_audit_dir.mkdir(parents=True, exist_ok=True)
    (action_audit_dir / "action-history.jsonl").write_text(
        json.dumps(
            {
                "at": "2026-03-16T09:57:00+09:00",
                "headline": "Sync Preview | preview",
                "status": "preview",
                "next_step": "/monitor O2",
                "remediation": "inspect sync drift before executing any runtime mutation",
                "link_label": "runtime detail",
                "link_href": "/control/runtimes/O2",
                "source_command": "/sync preview O2 24h",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    background_runs.upsert_background_run_ticket(
        background_runs.background_runs_state_path(project_root / ".aoe-team"),
        {
            "ticket_id": "BGT-001",
            "request_id": "REQ-1",
            "project_key": "alpha",
            "execution_brief_status": "underspecified",
            "runner_target": "local_background",
            "launch_mode": "offdesk_manual",
            "created_by": "dashboard-fixture",
            "source_surface": "offdesk",
            "status": "running",
            "created_at": "2026-03-16T09:55:00+09:00",
            "evidence_bundle": "status=pending | outcome=awaiting_review",
            "evidence_artifacts": ["review_evidence/git_diff_scope.md"],
        },
        now_iso=lambda: "2026-03-16T10:00:00+09:00",
    )
    background_runs.update_background_worker_state(
        background_runs.background_worker_state_path(project_root / ".aoe-team"),
        now_iso=lambda: "2026-03-16T10:00:05+09:00",
        status="running",
        runner_target="local_background",
        mode="thread_daemon",
        thread_name="aoe-local-bg-10001",
        pid=10001,
        started_at="2026-03-16T09:55:00+09:00",
        heartbeat_at="2026-03-16T10:00:05+09:00",
        last_reason="drained:1",
        claimed_count=1,
        drain_cycles=2,
        queue_depth=1,
        queue_stale_count=0,
        queue_summary="depth=1 | status running=1 | target local_background=1",
    )
    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    return team_dir, manager_state_file, project_root


def _write_project_flow_fixture(project_root: Path, *, project_alias: str = "O2") -> None:
    registry_dir = project_root / "docs" / "investigations_mo" / "registry"
    project_dir = project_root / "docs" / "investigations_mo" / "projects" / project_alias
    tf_dir = project_dir / "tfs" / "TF-002"
    registry_dir.mkdir(parents=True, exist_ok=True)
    tf_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "project_registry.md").write_text(
        "\n".join(
            [
                "| project_alias | purpose | status | ongoing_doc | note_doc |",
                "| --- | --- | --- | --- | --- |",
                f"| {project_alias} | Alpha project flow | active | `docs/investigations_mo/projects/{project_alias}/ongoing.md` | `docs/investigations_mo/projects/{project_alias}/note.md` |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "project_lock.yaml").write_text(
        "\n".join(
            [
                f"active_project: {project_alias}",
                "active_tf: TF-002",
                "active_paths:",
                f"  project_ongoing: docs/investigations_mo/projects/{project_alias}/ongoing.md",
                f"  project_note: docs/investigations_mo/projects/{project_alias}/note.md",
                f"  tf_report: docs/investigations_mo/projects/{project_alias}/tfs/TF-002/report.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "tf_registry.md").write_text(
        "\n".join(
            [
                "| tf_id | project_alias | objective | status | exec_verdict | owner | created_at | closed_at | report_doc |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                f"| TF-001 | {project_alias} | Closed setup | closed | success | codex | 2026-04-01 | 2026-04-02 | `docs/investigations_mo/projects/{project_alias}/tfs/TF-001/report.md` |",
                f"| TF-002 | {project_alias} | Dashboard card | running |  | codex | 2026-04-03 |  | `docs/investigations_mo/projects/{project_alias}/tfs/TF-002/report.md` |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "handoff_index.csv").write_text(
        "project_alias,tf_id,request_id,status,report_doc\n"
        f"{project_alias},TF-002,REQ-1,running,docs/investigations_mo/projects/{project_alias}/tfs/TF-002/report.md\n",
        encoding="utf-8",
    )
    (registry_dir / "tf_close_index.csv").write_text(
        "project_alias,tf_id,task_label,request_id,status,exec_verdict,closed_at,report_doc,archive_bundle\n"
        f"{project_alias},TF-001,Closed setup,REQ-OLD,closed,success,2026-04-02,docs/investigations_mo/projects/{project_alias}/tfs/TF-001/report.md,\n",
        encoding="utf-8",
    )
    (project_dir / "ongoing.md").write_text(
        "\n".join(
            [
                f"# {project_alias}",
                "",
                "## Objective",
                "- Connect document flow to runtime detail.",
                "",
                "## Todo Queue",
                "| todo_id | summary | priority | status |",
                "| --- | --- | --- | --- |",
                "| TODO-001 | Render Document Flow card | P1 | open |",
                "| TODO-002 | Close old setup note | P2 | closed |",
                "",
                "## Open Decisions",
                "- Keep dashboard document flow read-only.",
                "",
                "## Blockers",
                "- Recovery excerpt is still separate.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_dir / "note.md").write_text(
        "## Accepted Project Decisions\n- Reuse Project Flow compiler output.\n",
        encoding="utf-8",
    )
    (tf_dir / "report.md").write_text("## Outcome\n- Dashboard card wiring is in progress.\n", encoding="utf-8")
    (project_dir / "tfs" / "TF-001").mkdir(parents=True, exist_ok=True)
    (project_dir / "tfs" / "TF-001" / "report.md").write_text(
        "## Outcome\n- Closed setup.\n",
        encoding="utf-8",
    )


def _mark_task_planning_gate_blocked(task: dict) -> None:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    plan["subtasks"] = []
    task["plan"] = plan
    task["plan_critic"] = {
        "approved": False,
        "issues": ["contract_gap"],
        "recommendations": [],
    }
    task["plan_review_count"] = 1
    task["plan_convergence_status"] = "blocked"
    task["plan_gate_passed"] = False


def _persist_general_subagent_artifact(project_root: Path, *, request_id: str = "REQ-1", task_ref: str = "T-001") -> None:
    project_team_dir = project_root / ".aoe-team"
    contract = subagent_contract.build_general_research_subagent_contract(
        request_id=request_id,
        task_ref=task_ref,
        objective="Collect bounded harness references and local doc evidence.",
        backend_descriptor={"backend_kind": "filesystem", "summary": "backend=filesystem"},
        relevant_doc_ids=["runbook", "spec-main"],
        context_pack_profile="followup_preview",
        context_pack_summary="profile=followup_preview docs=2 canonical=1",
        vendor_patterns=["producer_reviewer", "supervisor"],
    )
    subagent_contract.persist_subagent_result_artifact(
        project_team_dir,
        contract=contract,
        raw_result={
            "summary": "repo scan complete",
            "confidence": "high",
            "sources": ["docs/RUNBOOK.md", "docs/SPEC.md"],
            "key_findings": ["harness patterns mapped", "local docs aligned"],
            "blocking_issues": ["vendor notes still need a local delta check"],
            "recommended_next_step": "/task T-001",
            "artifact_refs": ["harness_authoring/plan.json"],
        },
    )

def test_runtime_read_matches_gateway_wrapper_state(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)

    via_gateway = gw.load_manager_state(manager_state_file, control_root, team_dir)
    via_runtime_read = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)

    assert via_runtime_read == via_gateway
    assert via_runtime_read["projects"]["alpha"]["tasks"]["REQ-1"]["phase1_role_preset"] == "analysis"


def test_control_dashboard_overview_and_tasks_routes_render_structured_state(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_planner_providers"] = ["codex"]
    task["phase1_critic_providers"] = ["claude"]
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for execution"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    gw.save_manager_state(manager_state_file, state)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:00:00+09:00",
        extra={
            "response_text": json.dumps(
                {
                    "verdict": "continue",
                    "confidence": "medium",
                    "reasoning": "brief executable",
                    "next_step": "/retry T-001",
                    "caution": "review lane remains",
                }
            )
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Retry | blocked",
        status="blocked",
        outcome_kind="retry_run",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked retry",
        next_step="/retry T-001",
        remediation="judge decision reuse: action=retry next=/retry T-001",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:05:00+09:00",
        extra={
            "latest_judge_decision_bridge": {
                "source": "latest_offdesk_judge",
                "verdict": "continue",
                "confidence": "medium",
                "recommended_action": "retry",
                "candidate_next_step": "/retry T-001",
                "applied": True,
                "applied_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
            },
            "replan_auto_decision": {
                "source": "latest_offdesk_judge",
                "current_action": "replan",
                "suggested_action": "retry",
                "suggested_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "bridge_applied": True,
                "supports_auto_decision": True,
                "can_auto_apply": True,
                "confidence": "medium",
            },
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "ready",
                "current_action": "replan",
                "suggested_action": "retry",
                "suggested_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
                "can_auto_apply": True,
                "requires_operator_confirmation": True,
                "confidence": "medium",
            },
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="applied",
        outcome_detail="retry_command=/retry T-001",
        next_step="/retry T-001",
        remediation="-",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:06:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    overview_status, overview_headers, overview_body = dashboard_app.build_dashboard_response("/control", config)
    tasks_status, tasks_headers, tasks_body = dashboard_app.build_dashboard_response("/control/tasks", config)
    health_status, health_headers, health_body = dashboard_app.build_dashboard_response("/control/health", config)

    overview_text = overview_body.decode("utf-8")
    tasks_text = tasks_body.decode("utf-8")
    health = json.loads(health_body.decode("utf-8"))

    assert overview_status == 200
    assert overview_headers["Content-Type"].startswith("text/html")
    assert "Control Summary" in overview_text
    assert "Ops Manager Rail" in overview_text
    assert "Action Audit" in overview_text
    assert "auto-route" in overview_text
    assert "O2 Alpha" in overview_text
    assert "next_retry_target" in overview_text
    assert "state_root_mode" in overview_text
    assert "legacy" in overview_text
    assert str(team_dir.resolve()) in overview_text
    assert "context_pack" in overview_text
    assert "model_plan" in overview_text
    assert "latest_judge" in overview_text
    assert "Offdesk Judge" in overview_text
    assert "codex_cli-gpt-5-4" in overview_text
    assert "latest_judge_decision" in overview_text
    assert "action=retry | verdict=continue | confidence=medium | next=/retry T-001 | brief executable" in overview_text
    assert "latest_judge_decision_bridge" in overview_text
    assert "mode=promoted_next_step | action=retry | verdict=continue | confidence=medium | next=/retry T-001 | auto=yes" in overview_text
    assert "replan_auto_decision" in overview_text
    assert "from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | auto=yes" in overview_text
    assert "replan_auto_routing_policy" in overview_text
    assert "status=ready | from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | confirm=yes" in overview_text
    assert "latest_replan_auto_route" in overview_text
    assert "Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001" in overview_text
    assert "auto_route" in overview_text
    assert "ready+applied=/retry T-001 | at=2026-04-09T11:06:00+09:00 | apply=dashboard button | api:auto_route_apply=true" in overview_text
    assert "auto_route_status" in overview_text
    assert "ready+applied=/retry T-001 | at=2026-04-09T11:06:00+09:00" in overview_text
    assert "Decision Signals" in overview_text
    assert "Execution Rails" in overview_text
    assert "latest_intent_command" in overview_text
    assert "offdesk_prepare" in overview_text
    assert "selected=offdesk_prepare" in overview_text
    assert "server_guard" in overview_text
    assert "server_guard_note" in overview_text
    assert "server_guard_snapshot" in overview_text
    assert "server_guard_latest_action" in overview_text
    assert "server_guard_latest_result" in overview_text
    assert "focus_preset" in overview_text
    assert "priority_link" in overview_text
    assert "operator_sentence" in overview_text
    assert "Server Guard Health Card" in overview_text
    assert "Server Guard Audit" in overview_text
    assert "Open Health JSON" in overview_text
    assert "No recent server guard preset thread yet." in overview_text
    assert "Run one of the server guard actions above" in overview_text
    assert "server-guard" in overview_text
    assert "priority-followup" in overview_text
    assert "secondary-followup" in overview_text
    assert "execution_brief" in overview_text
    assert "underspecified" in overview_text
    assert "brief_summary" in overview_text
    assert "blocked=acceptance_gap" in overview_text
    assert "planning_lanes" in overview_text
    assert "draft via codex | review via claude" in overview_text
    assert "approved_plan_gate" in overview_text
    assert "dispatch unlocked after critic approval | review via claude" in overview_text
    assert "approved_plan" in overview_text
    assert "reentry_rails" in overview_text
    assert "retry=blocked:underspecified exec=L1 review=R1" in overview_text
    assert "followup=none" in overview_text
    assert "bg=running/local_background" in overview_text
    assert "execution_brief_summary" in overview_text
    assert "underspecified=1" in overview_text
    assert "background_run_summary" in overview_text
    assert "status running=1" in overview_text
    assert "target local_background=1" in overview_text
    assert "background_scheduler" in overview_text
    assert "background_worker_summary" in overview_text
    assert "status=running" in overview_text
    assert "Project Progress Board" in overview_text
    assert "reports/summary.md" in overview_text
    assert "acceptance_gap" in overview_text
    assert "confirm acceptance scope before off-desk execution" in overview_text
    assert "background_run" in overview_text
    assert "BGT-001" in overview_text
    assert "run_lock" in overview_text
    assert "open" in overview_text
    assert "background_slots" in overview_text
    assert "active=0 limit=1" in overview_text
    assert "idle (0/1)" in overview_text
    assert "runtime_handle" in overview_text
    assert "aoe_bg_bgt_001" in overview_text
    assert "runtime_summary" in overview_text
    assert "tmux_session=aoe_bg_bgt_001" in overview_text
    assert "awaiting_review" in overview_text
    assert "Action Result" in overview_text
    assert "Clear Local History" in overview_text
    assert "Raw Payload" in overview_text
    assert "action-result-rows" in overview_text
    assert "action-result-links" in overview_text
    assert "action-result-history" in overview_text
    assert "planning_compact" in overview_text
    assert "preset_diff · live_preview" in overview_text
    assert "action-result-emphasis-badge" in overview_text
    assert "action-history-badge" in overview_text
    assert "/control/audit?focus=auto-route" in overview_text
    assert "/control/audit?limit=20" in overview_text
    assert "/control/audit?focus=judge&amp;limit=20" in overview_text
    assert "remediation" in overview_text
    assert "Sync Preview | preview" in overview_text
    assert "/control/runtimes/O2" in overview_text
    assert tasks_status == 200
    assert tasks_headers["Content-Type"].startswith("text/html")
    assert "Active Tasks" in tasks_text
    assert "analysis" in tasks_text
    assert "exec=Codex-Analyst,Claude-Analyst | review=Codex-Reviewer,Claude-Reviewer" in tasks_text
    assert "autogen_core | sandbox | verdict=fail | contract=drift" in tasks_text
    assert health_status == 200
    assert health_headers["Content-Type"].startswith("application/json")
    assert health["ok"] is True
    assert health["active_runtime_count"] == 1
    assert "server_guard" in health
    assert "status" in health["server_guard"]
    assert "summary" in health["server_guard"]
    assert "next_step" in health["server_guard"]
    assert "snapshot_path" in health["server_guard"]
    assert "recommended_actions" in health["server_guard"]
    assert any(
        str(row.get("note") or "").strip()
        for row in (health["server_guard"].get("recommended_actions") or [])
        if row.get("href") == "/control/health/view"
    )


def test_control_dashboard_chat_console_route_renders_sessions_and_room_tail(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_planner_providers"] = ["codex"]
    task["phase1_critic_providers"] = ["claude"]
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for execution"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
            "recent_task_refs": {"done": ["REQ-2"]},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (team_dir / "telegram_chat_aliases.json").write_text(
        json.dumps({"1": "123456"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    room_dir = team_dir / "logs" / "rooms" / "O2" / "analysis"
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "2026-04-15.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-04-15T11:12:00+09:00",
                "actor": "operator",
                "kind": "note",
                "text": "analysis room tail line",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    other_room_dir = team_dir / "logs" / "rooms" / "O2" / "review"
    other_room_dir.mkdir(parents=True, exist_ok=True)
    (other_room_dir / "2026-04-14.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-04-14T09:00:00+09:00",
                "actor": "operator",
                "kind": "note",
                "text": "review rail tail line",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cross_project_room_dir = team_dir / "logs" / "rooms" / "O3" / "analysis"
    cross_project_room_dir.mkdir(parents=True, exist_ok=True)
    (cross_project_room_dir / "2026-04-13.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-04-13T08:00:00+09:00",
                "actor": "operator",
                "kind": "note",
                "text": "cross project rail tail line",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (team_dir / "dashboard" / "action-history.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2026-04-15T11:15:00+09:00",
                        "headline": "Chat Send | completed",
                        "status": "completed",
                        "outcome_kind": "chat_send",
                        "outcome_status": "completed",
                        "outcome_reason_code": "-",
                        "outcome_detail": "direct reply ok",
                        "next_step": "/task T-001",
                        "remediation": "-",
                        "link_label": "Chat Console",
                        "link_href": "/control/chat?chat=123456",
                        "source_command": "/direct how is the runtime?",
                        "chat_id": "123456",
                        "transcript_preview": "direct reply ok\n- next: /task T-001",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "at": "2026-04-15T11:14:00+09:00",
                        "headline": "Chat Session Update | completed",
                        "status": "completed",
                        "outcome_kind": "chat_session_update",
                        "outcome_status": "completed",
                        "outcome_reason_code": "-",
                        "outcome_detail": "default=dispatch pending=none room=O2/analysis",
                        "next_step": "/control/chat?chat=123456",
                        "remediation": "-",
                        "link_label": "Chat Console",
                        "link_href": "/control/chat?chat=123456",
                        "source_command": "chat-session 123456 default=dispatch pending=none room=O2/analysis",
                        "chat_id": "123456",
                        "transcript_preview": "session updated\n- room: O2/analysis",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "at": "2026-04-15T11:13:00+09:00",
                        "headline": "Chat Session Task | completed",
                        "status": "completed",
                        "outcome_kind": "chat_session_select_task",
                        "outcome_status": "completed",
                        "outcome_reason_code": "-",
                        "outcome_detail": "O2:REQ-1",
                        "next_step": "/control/chat?chat=123456",
                        "remediation": "-",
                        "link_label": "Chat Console",
                        "link_href": "/control/chat?chat=123456",
                        "source_command": "chat-session-select-task:123456:O2:REQ-1",
                        "chat_id": "123456",
                        "transcript_preview": "selected task updated\n- selected_task: REQ-1",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/chat?chat=123456&preset=global-direct", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Chat Console" in text
    assert "Chat Composer" in text
    assert "Advanced Ops Manager" in text
    assert 'class="panel info-fold chat-ops-details" open' in text
    assert "auto-opened for blocked" in text
    assert "Ops Manager Rail" not in text
    assert "server_guard_latest_action" in text
    assert "server_guard_latest_result" in text
    assert "123456" in text
    assert "O2/analysis" in text
    assert "analysis room tail line" in text
    assert "planning_lanes" in text
    assert "draft via codex | review via claude" in text
    assert "approved_plan_gate" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "approved_plan" in text
    assert "/control/actions/chat/send" in text
    assert "/control/actions/chat/session-update" in text
    assert '<option value="direct" selected>One-shot Direct</option>' in text
    assert "One-shot Direct" in text
    assert "quick room rails" in text
    assert "quick-room-options" in text
    assert "O2 root rail" in text
    assert "active room rail" in text
    assert "all room rails" not in text
    assert "chat-room-focus-card" in text
    assert "other room rails" in text
    assert "O2 / review rail" in text
    assert "O3 / analysis rail" in text
    assert "same project | target O2 / review rail" in text
    assert "project O2 -&gt; O3 | same lane" in text or "project O2 -> O3 | same lane" in text
    assert "injectChatSendReplyIntoFeed" in text
    assert "injectChatRoomSwitchBanner" in text
    assert "syncChatRoomOptionUI" in text
    assert "data-chat-selected-room-badge" in text
    assert "data-chat-selected-room-label" in text
    assert "data-chat-room-option-list" in text
    assert "data-chat-reply-preview" in text
    assert "data-chat-room-change" in text
    assert "data-chat-id" in text
    assert "data-chat-mode" in text
    assert "data-source-command" in text
    assert "chatReplyPreview" in text
    assert "chatRoomChange" in text
    assert "deriveChatMode" in text
    assert "buildChatEventFacetLinks" in text
    assert "appendChatEventHistoryLinks" in text
    assert "appendInlineLinks" in text
    assert "appendChatModeBadge" in text
    assert "buildChatModeFacetLinks" in text
    assert "appendChatModeHistoryLinks" in text
    assert "appendChatActionHistoryLinks" in text
    assert "chat-room-option-hidden" in text
    assert "scrollChatFeedToReply" in text
    assert "chat-message-transient" in text
    assert "chat-mode-badge" in text
    assert "chat-mode-direct" in text
    assert "/control/audit?chat=123456&amp;q=chat_event%3Areply&amp;limit=20" in text
    assert "/control/history?chat=123456&amp;q=chat_event%3Areply&amp;scope=dashboard&amp;limit=20" in text
    assert "/control/audit?chat=123456&amp;q=chat_event%3Asession&amp;limit=20" in text
    assert "/control/history?chat=123456&amp;q=chat_event%3Asession&amp;scope=dashboard&amp;limit=20" in text
    assert "/control/audit?chat=123456&amp;q=chat_mode%3Adirect&amp;limit=20" in text
    assert "/control/history?chat=123456&amp;q=chat_mode%3Adirect&amp;scope=dashboard&amp;limit=20" in text
    assert "/control/history?q=room%3AO2/analysis&amp;scope=room&amp;limit=20" in text
    assert "buildChatModeFacetLinks(" in text
    assert "mode audit" in text
    assert "mode history" in text
    assert "event audit" in text
    assert "event history" in text
    assert "room history" in text
    assert "Send Chat Message" in text
    assert "Update Session Controls" in text
    assert "Update Selected Task" in text
    assert "/control/actions/chat/session-select-task" in text
    assert "Conversation Feed" in text
    assert "Recent Room Rails" not in text
    assert "chat-message-bubble" in text
    assert "chat-message-command" in text
    assert "chat-feed-event" in text
    assert "direct reply ok" in text
    assert "session updated" in text
    assert "selected task updated" in text
    assert "deep_link_preset" in text
    assert "Analysis Rail" in text
    assert "Review Rail" in text
    assert "Global Direct" in text
    assert "short direct replies on the global rail" in text
    assert "live_preview_focus" in text
    assert "Codex Pressure" in text
    assert "consolidate chat and operator sessions" in text
    assert "operator_sentence" in text
    assert "trim chat fanout first, then widen operator surfaces" in text
    assert "live_preview_preset" in text
    assert "Apply Global Direct" in text
    assert "server-guard-live-preview:codex:123456:Global Direct" in text
    assert "planning_compact" in text
    assert "active planner=codex critic=claude" in text
    assert "preset_planning_compact" in text


def test_control_dashboard_overview_seeds_action_result_history_with_preference_memory_scope(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | project",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/actions/task/worker-apply-preview",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-1",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert 'data-preference-artifact-kind="chart"' in text
    assert "data-preference-memory-scope=\"preference_memory_scope=project:O2\"" in text
    assert "preference_memory_scope=project:O2" in text
    assert "memory audit" in text
    assert "memory history" in text
    assert "Preference Decision | project" in text


def test_control_dashboard_overview_seeds_action_result_history_with_preference_candidate_scopes(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Candidate | project",
        status="preview",
        outcome_kind="worker_apply_preview",
        outcome_status="preview",
        outcome_reason_code="ready",
        outcome_detail="show_source_note=true",
        next_step="/task T-001",
        remediation="-",
        source_command="/control/actions/task/worker-apply-preview",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-1",
        at="2026-04-09T11:12:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_candidates": [
                {
                    "artifact_kind": "chart",
                    "key": "show_source_note",
                    "expected_scope": "project",
                    "expected_scope_ref": "O2",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert 'data-preference-artifact-kind="chart"' in text
    assert 'data-preference-candidate-scopes="preference_candidate_scopes=show_source_note:project:O2"' in text
    assert "preference_candidate_scopes=show_source_note:project:O2" in text
    assert "scope audit" in text
    assert "scope history" in text
    assert "Preference Candidate | project" in text


def test_control_dashboard_overview_embeds_preference_candidate_renderer(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "preference_candidates" in text
    assert "Expected Scope" in text
    assert "Suggested Value" in text
    assert "open audit" in text
    assert "open history" in text
    assert "Actions" in text
    assert "actionRows" in text
    assert "preference_refresh_diff" in text
    assert "buildPreferenceRefreshDiff" in text
    assert "preferenceRefreshDiff" in text
    assert "data-preference-refresh-diff" in text
    assert "preference-refresh-diff-group" in text
    assert "preference-refresh-diff-badge" in text


def test_action_audit_headline_appends_approved_plan_for_generic_blocked_rows() -> None:
    row = {
        "headline": "Dispatch Phase2 | blocked",
        "status": "blocked",
        "outcome_kind": "dispatch_phase2",
        "outcome_status": "blocked",
        "outcome_reason_code": "approved_plan_blocked",
        "approved_plan_summary": "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance",
        "subagent_evidence_summary": "general_research | confidence=high | sources=2 | findings=2 | blocking=1",
        "planning_handoff": {
            "planning_compact_summary": "draft via codex | review via claude | dispatch waits for critic-approved plan",
        },
    }

    summary = action_audit.summarize_action_audit_headline(row)

    assert "reason=approved_plan_blocked" in summary
    assert "planning=draft via codex | review via claude | dispatch waits for critic-approved plan" in summary
    assert "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance" in summary
    assert "subagent_evidence=general_research | confidence=high | sources=2 | findings=2 | blocking=1" in summary


def test_action_audit_headline_appends_operator_preference_summaries() -> None:
    row = {
        "headline": "Chart Render | executed",
        "status": "executed",
        "outcome_kind": "artifact_render",
        "outcome_status": "executed",
        "outcome_reason_code": "chart_ready",
        "applied_preferences": [
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "enabled": True,
                "prompt_mode": "auto",
                "scope": "artifact_kind",
            }
        ],
        "preference_candidates": [
            {
                "artifact_kind": "chart",
                "key": "show_source_note",
                "suggested_value": True,
                "issue": "source note keeps getting re-added during review",
                "occurrence_count": 2,
            }
        ],
        "preference_decisions": [
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "choice": "apply_always",
                "scope": "artifact_kind",
            }
        ],
    }

    summary = action_audit.summarize_action_audit_headline(row)

    assert "reason=chart_ready" in summary
    assert "applied_preferences=legend_position=bottom" in summary
    assert "preference_candidates=show_source_note=true" in summary
    assert "preference_decisions=legend_position=bottom" in summary
    assert "preference_memory_scope=artifact_kind:chart" in summary


# Legacy planning compact compatibility


def test_legacy_action_audit_planning_compact_handoff_reads_top_level_review_key() -> None:
    summary = action_audit.summarize_retry_replan_planning_compact_handoff(
        {},
        row=legacy_planning_review_payload(),
    )

    assert summary == f"planning={LEGACY_PLANNING_REVIEW_SUMMARY}"


def test_legacy_control_dashboard_append_action_audit_backfills_planning_compact_from_review_key(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    dashboard_app._append_action_audit(
        config,
        {
            "path": "/control/actions/task/followup",
            "status": "blocked",
            "source_command": "/followup T-001",
            "next_step": "/task T-001",
            "remediation": "-",
            **legacy_planning_review_payload(),
            "preview": {"detail_path": "/control/tasks/by-request/REQ-1"},
        },
    )

    rows = [json.loads(line) for line in (team_dir / "dashboard" / "action-history.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    row = rows[-1]

    assert row["planning_compact_summary"] == LEGACY_PLANNING_REVIEW_SUMMARY
    assert "planning_review_summary" not in row


def test_action_audit_replan_auto_operator_status_uses_planning_compact_label() -> None:
    summary = action_audit.summarize_replan_auto_operator_status(
        policy={
            "status": "contract_review_ready",
            "suggested_next_step": "/task T-001",
            "planning_feedback_source": "job_contract",
            "planning_feedback_state": "blocked",
        },
        route_row={},
    )

    assert summary == "planning_compact=/task T-001 | source=job_contract | state=blocked | reused"


def test_control_dashboard_history_route_uses_approved_plan_headline_summary_for_blocked_rows(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Dispatch Phase2 | blocked",
        status="blocked",
        outcome_kind="dispatch_phase2",
        outcome_status="blocked",
        outcome_reason_code="approved_plan_blocked",
        outcome_detail="queue gate waiting",
        next_step="/task T-001",
        remediation="inspect approved plan blockers before dispatch",
        source_command="/dispatch task T-001",
        link_label="Task T-001",
        link_href="/control/tasks/by-request/REQ-1",
        at="2026-04-15T12:00:00+09:00",
        extra={
            "approved_plan_summary": "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance",
            "subagent_contract_summary": "general_research | profile=followup_preview | backend=filesystem | artifact=harness_authoring/subagents/req-1-general-research.json",
            "subagent_evidence_summary": "general_research | confidence=high | sources=2 | findings=2 | blocking=1",
            "subagent_artifact_path": "harness_authoring/subagents/req-1-general-research.json",
            "planning_handoff": {
                "planning_compact_summary": "draft via codex | review via claude | dispatch waits for critic-approved plan",
            },
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?q=approved_plan_blocked&scope=dashboard",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Dispatch Phase2 | blocked | reason=approved_plan_blocked | planning=draft via codex | review via claude | dispatch waits for critic-approved plan | approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance" in text
    assert "planning_compact: draft via codex | review via claude | dispatch waits for critic-approved plan | approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance" in text
    assert "subagent_contract: general_research | profile=followup_preview | backend=filesystem | artifact=harness_authoring/subagents/req-1-general-research.json" in text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in text


def test_control_dashboard_post_chat_send_route_executes_gateway_simulation(
    tmp_path: Path, monkeypatch
) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    (team_dir / "telegram_chat_aliases.json").write_text(
        json.dumps({"1": "123456"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    def _fake_run_gateway_chat_send(argv: list[str], *, timeout_sec: int = 180) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        captured["timeout_sec"] = timeout_sec
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="direct reply ok\n- next: /task T-001\n",
            stderr="",
        )

    monkeypatch.setattr(chat_exec, "_run_gateway_chat_send", _fake_run_gateway_chat_send)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/send",
        body=json.dumps(
            {
                "chat_id": "123456",
                "mode": "direct",
                "text": "how is the runtime?",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    argv = [str(item) for item in captured.get("argv", [])]

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["ok"] is True
    assert payload["mode"] == "direct"
    assert payload["chat_id"] == "123456"
    assert payload["source_command"] == "/direct how is the runtime?"
    assert payload["last_send_mode"] == "direct"
    assert "direct reply ok" in payload["reply_text"]
    assert "--simulate-chat-id" in argv
    assert "123456" in argv
    assert "--chat-aliases-file" in argv
    updated_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    audit_rows = [
        json.loads(line)
        for line in (team_dir / "dashboard" / "action-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert updated_state["chat_sessions"]["123456"]["last_cmd_args"]["chat_send_mode"] == "direct"
    assert audit_rows[-1]["chat_reply_summary"].startswith("direct reply ok")

    audit_status, _audit_headers, audit_body = dashboard_app.build_dashboard_response("/control/audit?chat=123456", config)
    audit_text = audit_body.decode("utf-8")
    assert audit_status == 200
    assert "chat_reply=direct reply ok" in audit_text


def test_control_dashboard_post_chat_send_room_use_preserves_room_state(
    tmp_path: Path, monkeypatch
) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {"123456": {"room": "O2/analysis"}}
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (team_dir / "telegram_chat_aliases.json").write_text(
        json.dumps({"1": "123456"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    def _fake_run_gateway_chat_send(argv: list[str], *, timeout_sec: int = 180) -> subprocess.CompletedProcess[str]:
        updated = json.loads(manager_state_file.read_text(encoding="utf-8"))
        updated.setdefault("chat_sessions", {}).setdefault("123456", {})["room"] = "O2/review"
        manager_state_file.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="room switched to O2/review\n",
            stderr="",
        )

    monkeypatch.setattr(chat_exec, "_run_gateway_chat_send", _fake_run_gateway_chat_send)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/send",
        body=json.dumps(
            {
                "chat_id": "123456",
                "mode": "room_use",
                "text": "O2/review",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    updated_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    audit_rows = [
        json.loads(line)
        for line in (team_dir / "dashboard" / "action-history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["mode"] == "room_use"
    assert payload["previous_room"] == "O2/analysis"
    assert payload["selected_room"] == "O2/review"
    assert payload["room_target"] == "O2/review"
    assert payload["room_changed"] is True
    assert payload["room_change_summary"] == "switched from O2/analysis to O2/review"
    assert updated_state["chat_sessions"]["123456"]["room"] == "O2/review"
    assert updated_state["chat_sessions"]["123456"]["last_cmd_args"]["chat_send_mode"] == "room_use"
    assert audit_rows[-1]["chat_room_change_summary"] == "switched from O2/analysis to O2/review"

    audit_status, _audit_headers, audit_body = dashboard_app.build_dashboard_response("/control/audit?chat=123456", config)
    audit_text = audit_body.decode("utf-8")
    assert audit_status == 200
    assert "chat_room_change=switched from O2/analysis to O2/review" in audit_text

    history_status, _history_headers, history_body = dashboard_app.build_dashboard_response(
        "/control/history?q=O2%2Freview&scope=dashboard",
        config,
    )
    history_text = history_body.decode("utf-8")
    assert history_status == 200
    assert "chat_room_change=switched from O2/analysis to O2/review" in history_text


def test_control_dashboard_post_chat_session_update_route_persists_defaults(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {"123456": {"default_mode": "direct", "room": "global"}}
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/session-update",
        body=json.dumps(
            {
                "chat_id": "123456",
                "default_mode": "dispatch",
                "pending_mode": "direct",
                "room": "O2/writing",
                "lang": "en",
                "report_level": "long",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    saved = json.loads(manager_state_file.read_text(encoding="utf-8"))
    session = saved["chat_sessions"]["123456"]

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["ok"] is True
    assert payload["default_mode"] == "dispatch"
    assert payload["pending_mode"] == "direct"
    assert payload["room"] == "O2/writing"
    assert payload["lang"] == "en"
    assert payload["report_level"] == "long"
    assert payload["next_step"] == "/control/chat?chat=123456"
    assert session["default_mode"] == "dispatch"
    assert session["pending_mode"] == "direct"
    assert session["room"] == "O2/writing"
    assert session["lang"] == "en"
    assert session["report_level"] == "long"

    audit_status, _audit_headers, audit_body = dashboard_app.build_dashboard_response(
        "/control/audit?chat=123456&q=chat_event%3Asession&limit=20",
        config,
    )
    audit_text = audit_body.decode("utf-8")
    assert audit_status == 200
    assert "chat_event_filter" in audit_text
    assert "session=1" in audit_text
    assert "O2/writing" in audit_text

    history_status, _history_headers, history_body = dashboard_app.build_dashboard_response(
        "/control/history?chat=123456&q=chat_event%3Asession&scope=dashboard&limit=20",
        config,
    )
    history_text = history_body.decode("utf-8")
    assert history_status == 200
    assert "chat_event_filter" in history_text
    assert "session=1" in history_text
    assert "O2/writing" in history_text


def test_control_dashboard_post_chat_session_select_task_route_persists_selected_task(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {"123456": {"room": "O2/analysis"}}
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/session-select-task",
        body=json.dumps(
            {
                "chat_id": "123456",
                "project_ref": "O2",
                "task_ref": "T-001",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    saved = json.loads(manager_state_file.read_text(encoding="utf-8"))
    session = saved["chat_sessions"]["123456"]

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["ok"] is True
    assert payload["project_alias"] == "O2"
    assert payload["selected_task_ref"] == "REQ-1"
    assert payload["next_step"] == "/control/chat?chat=123456"
    assert session["selected_task_refs"]["alpha"] == "REQ-1"


def test_control_dashboard_overview_surfaces_chat_console_link(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "room": "O2/analysis",
            "selected_task_refs": {"alpha": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (team_dir / "telegram_chat_aliases.json").write_text(
        json.dumps({"1": "123456"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    status, headers, body = dashboard_app.build_dashboard_response("/control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "/control/chat?chat=123456" in text
    assert "Open Chat 1" in text


def test_control_dashboard_audit_route_renders_recent_file_backed_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="judge_policy_ready",
        outcome_detail="retry_command=/retry T-001",
        next_step="/retry T-001",
        remediation="-",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:06:00+09:00",
        extra={
            "subagent_contract_summary": "general_research | profile=followup_preview | backend=filesystem | artifact=harness_authoring/subagents/req-1-general-research.json",
            "subagent_evidence_summary": "general_research | confidence=high | sources=2 | findings=2 | blocking=1",
            "subagent_artifact_path": "harness_authoring/subagents/req-1-general-research.json",
            "applied_preferences": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "enabled": True,
                    "prompt_mode": "auto",
                    "scope": "artifact_kind",
                }
            ],
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "artifact_kind",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/audit", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Action Audit" in text
    assert "action-history.jsonl" in text
    assert "status_summary" in text
    assert "preview=1" in text
    assert "focus_summary" in text
    assert "auto-route=1" in text
    assert "all=2" in text
    assert "Sync Preview | preview" in text
    assert "Replan Auto Route | applied" in text
    assert "retry_command=/retry T-001" in text
    assert "subagent_contract: general_research | profile=followup_preview | backend=filesystem | artifact=harness_authoring/subagents/req-1-general-research.json" in text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "applied_preferences=legend_position=bottom" in text
    assert "preference_decisions=legend_position=bottom" in text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in text
    assert "/sync preview O2 24h" in text
    assert "/control/runtimes/O2" in text
    assert "auto-route" in text


def test_control_dashboard_action_audit_wrapper_records_json_payload(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    before_rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    body = json.dumps(
        {
            "ok": True,
            "status": "completed",
            "path": "/control/actions/chat/send",
            "source_command": "/direct wrapper smoke test",
            "chat_id": "123456",
            "reply_text": "wrapper reply ok",
            "room_change_summary": "switched from O2/analysis to O2/review",
            "outcome": {
                "kind": "chat_send",
                "status": "completed",
                "reason_code": "-",
                "detail": "wrapper reply ok",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    status, headers, wrapped = dashboard_action_audit._with_action_audit(
        (200, {"Content-Type": "application/json; charset=utf-8"}, body),
        config=config,
    )

    after_rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert wrapped == body
    assert after_rows
    assert before_rows[-1]["source_command"] != after_rows[-1]["source_command"]
    assert after_rows[-1]["source_command"] == "/direct wrapper smoke test"
    assert after_rows[-1]["chat_reply_summary"] == "wrapper reply ok"
    assert after_rows[-1]["chat_room_change_summary"] == "switched from O2/analysis to O2/review"


def test_control_dashboard_history_route_surfaces_debug_packet_handoff_details(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/task T-001",
        remediation="inspect planning primitives before rerouting",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T11:07:00+09:00",
        extra={
            "planning_handoff": {
                "planning_compact_summary": "draft via codex | review via claude | dispatch waits for critic-approved plan",
                "job_contract": {
                    "status": "ready",
                    "summary": "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1",
                },
                "approved_plan": {
                    "status": "blocked",
                    "summary": "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance",
                },
                "debug_packet": {
                    "state": "blocked",
                    "summary": "state=blocked | symptom=background_run_inflight | evidence=1 | next=/task T-001",
                    "symptom": "background_run_inflight",
                    "failed_attempt": "/retry T-001 lane L1",
                    "next_step": "/task T-001",
                },
                "phase_checkpoint": {
                    "status": "blocked",
                    "current_phase": "verify",
                    "summary": "status=blocked | current=verify | verify=blocked|note=verification_gap",
                },
            }
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?q=background_run_inflight&scope=dashboard",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Replan | blocked | reason=planning_gate | debug=blocked | symptom=background_run_inflight | planning=draft via codex | review via claude | dispatch waits for critic-approved plan" in text
    assert "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance" in text
    assert "background_run_inflight" in text
    assert "attempt=/retry T-001 lane L1" in text
    assert "next=/task T-001" in text


def test_control_dashboard_audit_route_surfaces_debug_packet_handoff_headline_summary(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/task T-001",
        remediation="inspect planning primitives before rerouting",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T11:07:00+09:00",
        extra={
            "subagent_contract_summary": "general_research | profile=followup_preview | backend=filesystem | artifact=harness_authoring/subagents/req-1-general-research.json",
            "subagent_evidence_summary": "general_research | confidence=high | sources=2 | findings=2 | blocking=1",
            "subagent_artifact_path": "harness_authoring/subagents/req-1-general-research.json",
            "planning_handoff": {
                "planning_compact_summary": "draft via codex | review via claude | dispatch waits for critic-approved plan",
                "approved_plan": {
                    "status": "blocked",
                    "summary": "approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance",
                },
                "debug_packet": {
                    "state": "blocked",
                    "summary": "state=blocked | symptom=background_run_inflight | evidence=1 | next=/task T-001",
                    "symptom": "background_run_inflight",
                    "failed_attempt": "/retry T-001 lane L1",
                    "next_step": "/task T-001",
                }
            }
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/audit", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Replan | blocked | reason=planning_gate | debug=blocked | symptom=background_run_inflight | planning=draft via codex | review via claude | dispatch waits for critic-approved plan | approved_plan=blocked" in text
    assert "planning_compact: draft via codex | review via claude | dispatch waits for critic-approved plan | approved_plan=blocked | subtasks=1 | reviews=2 | issue=missing acceptance" in text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in text
    assert "planning critic blocked replan" in text


def test_control_dashboard_audit_route_filters_by_focus_badge(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="judge_policy_ready",
        outcome_detail="retry_command=/retry T-001",
        next_step="/retry T-001",
        remediation="-",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:06:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/audit?focus=auto-route", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "focus_filter" in text
    assert "auto-route=1" in text
    assert "auto-route" in text
    assert "Replan Auto Route | applied" in text
    assert "<span>total_rows</span><strong>1</strong>" in text


def test_control_dashboard_audit_route_surfaces_server_guard_focus_badge(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Background Queue Cleanup Preview | preview",
        status="preview",
        outcome_kind="background_queue_cleanup_preview",
        outcome_status="preview",
        outcome_reason_code="stale_present",
        outcome_detail="stale_count=1 | summary=running=1 stale=1",
        next_step="/orch status O2",
        remediation="inspect stale queue tickets before mutating background queue state",
        source_command="/orch bgq-clean O2 preview",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:16:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/audit?focus=server-guard", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "server-guard=1" in text
    assert "focus_filter" in text
    assert "Background Queue Cleanup Preview | preview" in text
    assert "server_guard_focus" in text
    assert "Codex Pressure" in text
    assert "action_copy" in text
    assert "start with Chat, then keep Global Direct narrow" in text


def test_control_dashboard_audit_route_preserves_focus_and_limit_query(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 420, "python": 120, "tmux": 3, "codex": 12})
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="judge_policy_ready",
        outcome_detail="retry_command=/retry T-001",
        next_step="/retry T-001",
        remediation="-",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:06:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/audit?focus=auto-route&limit=1", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert '<option value="auto-route" selected>' in text
    assert 'name="limit"' in text
    assert 'value="1"' in text
    assert "<span>limit</span><strong>1</strong>" in text
    assert "/control/audit?limit=1" in text
    assert "/control/audit?focus=judge&amp;limit=1" in text


def test_control_dashboard_audit_route_filters_preferences_by_query(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Rule | chart",
        status="executed",
        outcome_kind="operator_preference_rule",
        outcome_status="executed",
        outcome_reason_code="rule_updated",
        outcome_detail="legend_position=bottom | artifact_kind:chart",
        next_step="/control/preferences?artifact=chart",
        remediation="-",
        source_command="/prefs rule chart:legend_position auto",
        link_label="preferences",
        link_href="/control/preferences?artifact=chart",
        at="2026-04-09T11:06:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "applied_preferences_summary": "applied_preferences=legend_position=bottom | artifact_kind:chart",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Rule | document",
        status="executed",
        outcome_kind="operator_preference_rule",
        outcome_status="executed",
        outcome_reason_code="rule_updated",
        outcome_detail="preserve_heading_structure=true | artifact_kind:document",
        next_step="/control/preferences?artifact=document",
        remediation="-",
        source_command="/prefs rule document:preserve_heading_structure confirm",
        link_label="preferences",
        link_href="/control/preferences?artifact=document",
        at="2026-04-09T11:07:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "document",
            "applied_preferences_summary": "applied_preferences=preserve_heading_structure=true | artifact_kind:document",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&q=artifact_kind:chart&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "query_filter" in text
    assert "artifact_kind:chart" in text
    assert "Preference Rule | chart" in text
    assert "<span>total_rows</span><strong>1</strong>" in text
    assert "q=artifact_kind%3Achart" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="artifact_kind:chart",
        limit=20,
    )
    assert audit.total_rows == 1
    assert audit.query_filter == "artifact_kind:chart"
    assert len(audit.rows) == 1
    assert audit.rows[0].headline == "Preference Rule | chart"
    assert audit.rows[0].preference_artifact_kind == "chart"


def test_control_dashboard_audit_route_surfaces_chat_event_facets_with_chat_context(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | reply",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="direct reply ok",
        next_step="-",
        remediation="-",
        source_command="/direct how is the runtime?",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "direct reply ok",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | dispatch",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="dispatch reply ok",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/dispatch summarize the pending work",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:08:30+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "dispatch reply ok",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | room post",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="room post reply",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/room post recap status",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:08:45+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "room post reply",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | room use",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="room changed",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/room use O2/review",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "chat_id": "123456",
            "chat_room_change_summary": "switched from O2/analysis to O2/review",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | raw",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="plain text reply",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="how is the runtime?",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "plain text reply",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?chat=123456&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "chat_event_filter" in text
    assert "chat_mode_filter" in text
    assert "reply=4" in text
    assert "room_change=1" in text
    assert "direct=1" in text
    assert "dispatch=1" in text
    assert "room_post=1" in text
    assert "room_use=1" in text
    assert "raw=1" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_event%3Areply&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_event%3Aroom_change&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_mode%3Adirect&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_mode%3Adispatch&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_mode%3Aroom_post&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_mode%3Aroom_use&limit=20" in text
    assert "/control/audit?focus=all&chat=123456&q=chat_mode%3Araw&limit=20" in text
    assert "chat-mode-direct" in text
    assert "chat-mode-dispatch" in text
    assert "chat-mode-room_post" in text
    assert "chat-mode-room_use" in text
    assert "chat-mode-raw" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        chat_id="123456",
        limit=20,
    )
    assert audit.chat_filter == "123456"
    assert audit.chat_event_filter == ""
    assert audit.chat_event_query_base == ""
    assert audit.chat_mode_filter == ""
    assert audit.chat_mode_query_base == ""
    assert audit.chat_event_counts == {"reply": 4, "room_change": 1}
    assert audit.chat_mode_counts == {"direct": 1, "dispatch": 1, "room_post": 1, "room_use": 1, "raw": 1}
    assert audit.total_rows == 5

    _snapshot, selected_audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        chat_id="123456",
        query="chat_event:room_change chat_mode:room_use",
        limit=20,
    )
    assert selected_audit.chat_event_filter == "room_change"
    assert selected_audit.chat_event_query_base == "chat_mode:room_use"
    assert selected_audit.chat_mode_filter == "room_use"
    assert selected_audit.chat_mode_query_base == "chat_event:room_change"
    assert selected_audit.chat_event_counts == {"room_change": 1}
    assert selected_audit.chat_mode_counts == {"room_use": 1}
    assert selected_audit.total_rows == 1
    assert len(selected_audit.rows) == 1
    assert selected_audit.rows[0].chat_room_change_summary == "chat_room_change=switched from O2/analysis to O2/review"


def test_control_dashboard_audit_route_filters_preferences_by_memory_scope_query(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | project",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/actions/task/worker-apply-preview",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-1",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | artifact",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/actions/task/worker-apply-preview",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always artifact_kind",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-2",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "artifact_kind",
                    "scope_ref": "chart",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&q=memory_scope:project&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "memory_scope:project" in text
    assert "Preference Decision | project" in text
    assert "preference_memory_scope=project:O2" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="memory_scope:project",
        limit=20,
    )
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert audit.rows[0].preference_memory_scope_summary == "preference_memory_scope=project:O2"

    _snapshot, combined_audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="memory_scope:project artifact_kind:chart",
        limit=20,
    )
    assert combined_audit.total_rows == 1
    assert len(combined_audit.rows) == 1


def test_control_dashboard_audit_route_filters_preferences_by_project(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Rule | O2 chart",
        status="executed",
        outcome_kind="operator_preference_rule",
        outcome_status="executed",
        outcome_reason_code="rule_updated",
        outcome_detail="legend_position=bottom | artifact_kind:chart",
        next_step="/control/preferences?project=O2&artifact=chart",
        remediation="-",
        source_command="/prefs rule chart:legend_position auto",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart",
        at="2026-04-09T11:06:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Rule | O3 document",
        status="executed",
        outcome_kind="operator_preference_rule",
        outcome_status="executed",
        outcome_reason_code="rule_updated",
        outcome_detail="preserve_heading_structure=true | artifact_kind:document",
        next_step="/control/preferences?project=O3&artifact=document",
        remediation="-",
        source_command="/prefs rule document:preserve_heading_structure confirm",
        link_label="preferences",
        link_href="/control/preferences?project=O3&artifact=document",
        at="2026-04-09T11:07:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O3",
            "preference_artifact_kind": "document",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&project=O3&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "project_filter" in text
    assert "O3" in text
    assert "Preference Rule | O3 document" in text
    assert "<span>total_rows</span><strong>1</strong>" in text
    assert 'name="project" value="O3"' in text
    assert "/control/audit?focus=all&project=O3&limit=20" in text
    assert "O2=1" in text
    assert "O3=1" in text
    assert "/control/audit?focus=preferences&project=O2&limit=20" in text
    assert "/control/audit?focus=preferences&limit=20" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O3",
        limit=20,
    )
    assert audit.project_filter == "O3"
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert audit.rows[0].project_alias == "O3"
    assert audit.project_counts == {"O2": 1, "O3": 1}
    assert audit.artifact_counts == {"document": 1}


def test_control_dashboard_audit_route_surfaces_artifact_facets_with_project_and_query_context(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 chart",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/preferences?project=O2&artifact=chart",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 document",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="preserve_heading_structure=true",
        next_step="/control/preferences?project=O2&artifact=document",
        remediation="-",
        source_command="/task T-001 | pref preserve_heading_structure apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=document",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "document",
            "preference_decisions": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O3 chart",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/preferences?project=O3&artifact=chart",
        remediation="-",
        source_command="/task T-002 | pref legend_position apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O3&artifact=chart",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O3",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O3",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&project=O2&q=memory_scope:project&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "artifact_filter" in text
    assert "chart=1" in text
    assert "document=1" in text
    assert "/control/audit?focus=preferences&project=O2&q=memory_scope%3Aproject%20artifact_kind%3Achart&limit=20" in text
    assert "/control/audit?focus=preferences&project=O2&q=memory_scope%3Aproject%20artifact_kind%3Adocument&limit=20" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="memory_scope:project",
        limit=20,
    )
    assert audit.project_filter == "O2"
    assert audit.artifact_filter == ""
    assert audit.artifact_query_base == "memory_scope:project"
    assert audit.artifact_counts == {"chart": 1, "document": 1}
    assert audit.total_rows == 2

    _snapshot, selected_audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="memory_scope:project artifact_kind:chart",
        limit=20,
    )
    assert selected_audit.artifact_filter == "chart"
    assert selected_audit.artifact_query_base == "memory_scope:project"
    assert selected_audit.artifact_counts == {"chart": 1, "document": 1}
    assert selected_audit.total_rows == 1
    assert len(selected_audit.rows) == 1
    assert selected_audit.rows[0].preference_artifact_kind == "chart"


def test_control_dashboard_audit_route_surfaces_memory_scope_facets_with_project_and_artifact_context(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 chart project",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/preferences?project=O2&artifact=chart",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 chart artifact",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="show_source_note=true",
        next_step="/control/preferences?project=O2&artifact=chart",
        remediation="-",
        source_command="/task T-001 | pref show_source_note apply_always artifact_kind",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "show_source_note",
                    "value": True,
                    "choice": "apply_always",
                    "scope": "artifact_kind",
                    "scope_ref": "chart",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 document project",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="preserve_heading_structure=true",
        next_step="/control/preferences?project=O2&artifact=document",
        remediation="-",
        source_command="/task T-001 | pref preserve_heading_structure apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=document",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "document",
            "preference_decisions": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&project=O2&q=artifact_kind:chart&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "memory_scope_filter" in text
    assert "project=1" in text
    assert "artifact_kind=1" in text
    assert "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20memory_scope%3Aproject&limit=20" in text
    assert "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20memory_scope%3Aartifact_kind&limit=20" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="artifact_kind:chart",
        limit=20,
    )
    assert audit.project_filter == "O2"
    assert audit.artifact_filter == "chart"
    assert audit.memory_scope_filter == ""
    assert audit.memory_scope_query_base == "artifact_kind:chart"
    assert audit.memory_scope_counts == {"project": 1, "artifact_kind": 1}
    assert audit.total_rows == 2

    _snapshot, selected_audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="artifact_kind:chart memory_scope:project",
        limit=20,
    )
    assert selected_audit.memory_scope_filter == "project"
    assert selected_audit.memory_scope_query_base == "artifact_kind:chart"
    assert selected_audit.memory_scope_counts == {"project": 1, "artifact_kind": 1}
    assert selected_audit.total_rows == 1
    assert len(selected_audit.rows) == 1
    assert selected_audit.rows[0].preference_memory_scope_summary == "preference_memory_scope=project:O2"


def test_control_dashboard_audit_route_surfaces_refresh_diff_facets_with_project_and_artifact_context(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 chart applied",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/preferences?project=O2&artifact=chart",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
            "preference_refresh_diff_summary": "preference_refresh_diff=applied_added=legend_position=bottom | on | manual_only | session:-",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Candidate | O2 chart promoted",
        status="executed",
        outcome_kind="operator_preference_candidate",
        outcome_status="executed",
        outcome_reason_code="candidate_promoted",
        outcome_detail="show_source_note=true",
        next_step="/control/preferences?project=O2&artifact=chart&scope=project",
        remediation="-",
        source_command="/prefs candidate chart:show_source_note auto",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=chart&scope=project",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "chart",
            "preference_refresh_diff_summary": (
                "preference_refresh_diff=applied_added=show_source_note=true | on | auto | project:O2 ; "
                "candidates_removed=show_source_note=true | hits=2 | issue=source note was missing"
            ),
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | O2 document applied",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="preserve_heading_structure=true",
        next_step="/control/preferences?project=O2&artifact=document",
        remediation="-",
        source_command="/task T-001 | pref preserve_heading_structure apply_always project",
        link_label="preferences",
        link_href="/control/preferences?project=O2&artifact=document",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "focus_badge": "preferences",
            "project_alias": "O2",
            "preference_artifact_kind": "document",
            "preference_refresh_diff_summary": "preference_refresh_diff=applied_added=preserve_heading_structure=true | on | auto | project:O2",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/audit?focus=preferences&project=O2&q=artifact_kind:chart&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "refresh_diff_filter" in text
    assert "applied_added=2" in text
    assert "candidates_removed=1" in text
    assert "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20refresh_diff%3Aapplied_added&limit=20" in text
    assert "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20refresh_diff%3Acandidates_removed&limit=20" in text

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="artifact_kind:chart",
        limit=20,
    )
    assert audit.project_filter == "O2"
    assert audit.artifact_filter == "chart"
    assert audit.refresh_diff_filter == ""
    assert audit.refresh_diff_query_base == "artifact_kind:chart"
    assert audit.refresh_diff_counts == {"applied_added": 2, "candidates_removed": 1}
    assert audit.total_rows == 2

    _snapshot, selected_audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        project_filter="O2",
        query="artifact_kind:chart refresh_diff:candidates_removed",
        limit=20,
    )
    assert selected_audit.refresh_diff_filter == "candidates_removed"
    assert selected_audit.refresh_diff_query_base == "artifact_kind:chart"
    assert selected_audit.refresh_diff_counts == {"applied_added": 2, "candidates_removed": 1}
    assert selected_audit.total_rows == 1
    assert len(selected_audit.rows) == 1
    assert "candidates_removed" in selected_audit.rows[0].preference_refresh_diff_summary


def test_control_dashboard_history_route_filters_preferences_by_artifact_query(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Candidate | chart",
        status="executed",
        outcome_kind="operator_preference_candidate",
        outcome_status="executed",
        outcome_reason_code="candidate_promoted",
        outcome_detail="show_source_note=true",
        next_step="/control/preferences?artifact=chart",
        remediation="-",
        source_command="/prefs candidate chart:show_source_note auto",
        link_label="preferences",
        link_href="/control/preferences?artifact=chart",
        at="2026-04-09T11:08:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "applied_preferences_summary": "applied_preferences=show_source_note=true",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Candidate | document",
        status="executed",
        outcome_kind="operator_preference_candidate",
        outcome_status="executed",
        outcome_reason_code="candidate_promoted",
        outcome_detail="explicit_open_questions_section=true",
        next_step="/control/preferences?artifact=document",
        remediation="-",
        source_command="/prefs candidate document:explicit_open_questions_section confirm",
        link_label="preferences",
        link_href="/control/preferences?artifact=document",
        at="2026-04-09T11:09:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "document",
            "applied_preferences_summary": "applied_preferences=explicit_open_questions_section=true",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?q=artifact_kind:chart&scope=dashboard",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "History Search" in text
    assert "artifact_kind:chart" in text
    assert "Preference Candidate | chart" in text
    assert "<span>rows</span><strong>1</strong>" in text

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="artifact_kind:chart",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert len(history.rows) == 1
    assert history.rows[0].summary == "Preference Candidate | chart | reason=candidate_promoted | applied_preferences=show_source_note=true"
    assert "artifact_kind:chart" in history.rows[0].detail


def test_control_dashboard_history_route_filters_preferences_by_memory_scope_query(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | project",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/actions/task/worker-apply-preview",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always project",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-1",
        at="2026-04-09T11:10:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "project",
                    "scope_ref": "O2",
                }
            ],
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Preference Decision | artifact",
        status="executed",
        outcome_kind="operator_preference_decision",
        outcome_status="executed",
        outcome_reason_code="registry_updated",
        outcome_detail="legend_position=bottom",
        next_step="/control/actions/task/worker-apply-preview",
        remediation="-",
        source_command="/task T-001 | pref legend_position apply_always artifact_kind",
        link_label="task detail",
        link_href="/control/tasks/by-request/REQ-2",
        at="2026-04-09T11:11:00+09:00",
        extra={
            "focus_badge": "preferences",
            "preference_artifact_kind": "chart",
            "preference_decisions": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "choice": "apply_always",
                    "scope": "artifact_kind",
                    "scope_ref": "chart",
                }
            ],
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?q=memory_scope:project&scope=dashboard",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "memory_scope:project" in text
    assert "Preference Decision | project" in text

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="memory_scope:project",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert len(history.rows) == 1
    assert "preference_memory_scope=project:O2" in history.rows[0].summary
    assert "memory_scope:project:O2" in history.rows[0].detail

    _snapshot, combined_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="memory_scope:project artifact_kind:chart",
        scope="dashboard",
        limit=20,
    )
    assert combined_history.total_rows == 1
    assert len(combined_history.rows) == 1


def test_control_dashboard_history_route_surfaces_chat_event_facets(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | reply",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="direct reply ok",
        next_step="-",
        remediation="-",
        source_command="/direct how is the runtime?",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:12:00+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "direct reply ok",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | dispatch",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="dispatch reply ok",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/dispatch summarize the pending work",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:12:30+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "dispatch reply ok",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | room post",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="room post reply",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/room post recap status",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:12:45+09:00",
        extra={
            "chat_id": "123456",
            "chat_reply_summary": "room post reply",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | room use",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="room changed",
        next_step="/control/chat?chat=123456",
        remediation="-",
        source_command="/room use O2/review",
        link_label="chat console",
        link_href="/control/chat?chat=123456",
        at="2026-04-09T11:13:00+09:00",
        extra={
            "chat_id": "123456",
            "chat_room_change_summary": "switched from O2/analysis to O2/review",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | other chat",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="other chat reply",
        next_step="/control/chat?chat=999999",
        remediation="-",
        source_command="/direct other chat",
        link_label="chat console",
        link_href="/control/chat?chat=999999",
        at="2026-04-09T11:14:00+09:00",
        extra={
            "chat_id": "999999",
            "chat_reply_summary": "other chat reply",
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Chat Send | raw",
        status="completed",
        outcome_kind="chat_send",
        outcome_status="completed",
        outcome_reason_code="-",
        outcome_detail="plain text reply",
        next_step="/control/chat?chat=888888",
        remediation="-",
        source_command="summarize current room",
        link_label="chat console",
        link_href="/control/chat?chat=888888",
        at="2026-04-09T11:15:00+09:00",
        extra={
            "chat_id": "888888",
            "chat_reply_summary": "plain text reply",
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?scope=dashboard&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "chat_event_filter" in text
    assert "chat_mode_filter" in text
    assert "reply=5" in text
    assert "room_change=1" in text
    assert "direct=2" in text
    assert "dispatch=1" in text
    assert "room_post=1" in text
    assert "room_use=1" in text
    assert "raw=1" in text
    assert "/control/history?q=chat_event%3Areply&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_event%3Aroom_change&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_mode%3Adirect&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_mode%3Adispatch&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_mode%3Aroom_post&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_mode%3Aroom_use&scope=dashboard&limit=20" in text
    assert "/control/history?q=chat_mode%3Araw&scope=dashboard&limit=20" in text
    assert '/control/chat?chat=123456' in text
    assert "chat-mode-direct" in text
    assert "chat-mode-dispatch" in text
    assert "chat-mode-room_post" in text
    assert "chat-mode-room_use" in text
    assert "chat-mode-raw" in text

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        scope="dashboard",
        limit=20,
    )
    assert history.chat_event_filter == ""
    assert history.chat_event_query_base == ""
    assert history.chat_mode_filter == ""
    assert history.chat_mode_query_base == ""
    assert history.chat_event_counts == {"reply": 5, "room_change": 1}
    assert history.chat_mode_counts == {"direct": 2, "dispatch": 1, "room_post": 1, "room_use": 1, "raw": 1}
    assert history.total_rows >= 6

    _snapshot, filtered_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        chat_filter="123456",
        scope="dashboard",
        limit=20,
    )
    assert filtered_history.chat_filter == "123456"
    assert filtered_history.chat_event_counts == {"reply": 3, "room_change": 1}
    assert filtered_history.chat_mode_counts == {"direct": 1, "dispatch": 1, "room_post": 1, "room_use": 1}
    assert filtered_history.total_rows == 4

    _snapshot, selected_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        chat_filter="123456",
        query="chat_event:room_change chat_mode:room_use",
        scope="dashboard",
        limit=20,
    )
    assert selected_history.chat_event_filter == "room_change"
    assert selected_history.chat_event_query_base == "chat_mode:room_use"
    assert selected_history.chat_mode_filter == "room_use"
    assert selected_history.chat_mode_query_base == "chat_event:room_change"
    assert selected_history.chat_filter == "123456"
    assert selected_history.chat_event_counts == {"room_change": 1}
    assert selected_history.chat_mode_counts == {"room_use": 1}
    assert selected_history.total_rows == 1
    assert len(selected_history.rows) == 1
    assert selected_history.rows[0].chat_mode == "room_use"
    assert "chat_room_change=switched from O2/analysis to O2/review" in selected_history.rows[0].detail


def test_control_dashboard_history_route_surfaces_room_scope_rows(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    room_dir = team_dir / "logs" / "rooms" / "O2" / "analysis"
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "2026-04-15.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-15T11:12:00+09:00",
                        "actor": "operator",
                        "kind": "note",
                        "text": "analysis room tail line",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-04-15T11:13:00+09:00",
                        "actor": "codex",
                        "kind": "reply",
                        "text": "analysis followup detail",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-04-15T11:14:00+09:00",
                        "actor": "planner",
                        "kind": "decision",
                        "text": "analysis decision checkpoint",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/history?q=room%3AO2%2Fanalysis&scope=room&limit=20",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "room_kind_filter" in text
    assert "room_actor_filter" in text
    assert "room_text_filter" in text
    assert "room O2/analysis" in text
    assert '/control/history?q=room%3AO2/analysis&scope=room&limit=20' in text
    assert "note=1" in text
    assert "reply=1" in text
    assert "decision=1" in text
    assert "operator=1" in text
    assert "codex=1" in text
    assert "planner=1" in text
    assert "/control/history?q=room%3AO2/analysis%20room_kind%3Anote&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_kind%3Areply&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_kind%3Adecision&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_actor%3Aoperator&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_actor%3Acodex&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_actor%3Aplanner&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_text%3Adecision&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_text%3Acheckpoint&scope=room&limit=20" in text
    assert "/control/history?q=room%3AO2/analysis%20room_text%3Afollowup&scope=room&limit=20" in text
    assert "latest 11:14" in text
    assert "recent 11:13" in text
    assert '/control/history?q=room%3AO2/analysis%20room_kind%3Adecision&scope=room&limit=20">room_kind decision</a>' in text
    assert '/control/history?q=room%3AO2/analysis%20room_actor%3Aplanner&scope=room&limit=20">actor planner</a>' in text
    assert "analysis room tail line" in text
    assert "analysis followup detail" in text
    assert "analysis decision checkpoint" in text
    assert '<option value="room" selected>room</option>' in text
    assert "/room tail 20" in text
    assert text.index("note=1") < text.index("reply=1") < text.index("decision=1")
    assert text.index("operator=1") < text.index("codex=1") < text.index("planner=1")
    assert text.index(">decision=1</a>") < text.index(">checkpoint=1</a>") < text.index(">followup=1</a>")
    assert "room-kind-note" in text
    assert "room-kind-reply" in text
    assert "room-kind-decision" in text
    assert "room-actor-operator" in text
    assert "room-actor-codex" in text
    assert "room-actor-planner" in text

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O2/analysis",
        scope="room",
        limit=20,
    )
    assert history.scope == "room"
    assert history.room_kind_filter == ""
    assert history.room_kind_query_base == "room:O2/analysis"
    assert history.room_actor_filter == ""
    assert history.room_actor_query_base == "room:O2/analysis"
    assert history.room_text_filters == []
    assert history.room_text_query_base == "room:O2/analysis"
    assert history.room_kind_counts == {"note": 1, "reply": 1, "decision": 1}
    assert history.room_kind_values == ["note", "reply", "decision"]
    assert history.room_actor_counts == {"operator": 1, "codex": 1, "planner": 1}
    assert history.room_actor_values == ["operator", "codex", "planner"]
    assert history.room_text_counts == {"decision": 1, "checkpoint": 1, "followup": 1}
    assert history.room_text_values == ["decision", "checkpoint", "followup"]
    assert history.room_text_hints == {
        "decision": "latest 11:14",
        "checkpoint": "latest 11:14",
        "followup": "recent 11:13",
    }
    assert history.total_rows == 3
    assert history.rows[0].room == "O2/analysis"
    assert history.rows[0].actor == "planner"
    assert history.rows[0].scope == "room"
    assert history.rows[0].followup_hint == "/room tail 20"

    _snapshot, selected_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O2/analysis room_kind:reply",
        scope="room",
        limit=20,
    )
    assert selected_history.room_kind_filter == "reply"
    assert selected_history.room_kind_query_base == "room:O2/analysis"
    assert selected_history.room_kind_counts == {"note": 1, "reply": 1, "decision": 1}
    assert selected_history.room_kind_values == ["note", "reply", "decision"]
    assert selected_history.total_rows == 1
    assert len(selected_history.rows) == 1
    assert selected_history.rows[0].action == "room_reply"
    assert "room_kind:reply" in selected_history.rows[0].detail
    assert "analysis followup detail" in selected_history.rows[0].detail

    selected_status, _selected_headers, selected_body = dashboard_app.build_dashboard_response(
        "/control/history?q=room%3AO2%2Fanalysis%20room_kind%3Areply&scope=room&limit=20",
        config,
    )
    selected_text = selected_body.decode("utf-8")
    assert selected_status == 200
    assert "room-kind-reply active-focus" in selected_text
    assert "<mark>reply</mark>" in selected_text

    _snapshot, actor_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O2/analysis room_actor:planner",
        scope="room",
        limit=20,
    )
    assert actor_history.room_actor_filter == "planner"
    assert actor_history.room_actor_query_base == "room:O2/analysis"
    assert actor_history.room_actor_counts == {"operator": 1, "codex": 1, "planner": 1}
    assert actor_history.room_actor_values == ["operator", "codex", "planner"]
    assert actor_history.total_rows == 1
    assert len(actor_history.rows) == 1
    assert actor_history.rows[0].actor == "planner"
    assert "room_actor:planner" in actor_history.rows[0].detail
    assert "analysis decision checkpoint" in actor_history.rows[0].detail

    actor_status, _actor_headers, actor_body = dashboard_app.build_dashboard_response(
        "/control/history?q=room%3AO2%2Fanalysis%20room_actor%3Aplanner%20room_text%3Adecision&scope=room&limit=20",
        config,
    )
    actor_text = actor_body.decode("utf-8")
    assert actor_status == 200
    assert "room-actor-planner active-focus" in actor_text
    assert ">decision=1</span>" in actor_text
    assert "all room text" in actor_text
    assert "<mark>decision</mark>" in actor_text
    assert "<mark>planner</mark>" in actor_text

    _snapshot, text_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O2/analysis room_text:decision",
        scope="room",
        limit=20,
    )
    assert text_history.room_text_filters == ["decision"]
    assert text_history.room_text_query_base == "room:O2/analysis"
    assert text_history.total_rows == 1
    assert len(text_history.rows) == 1
    assert text_history.rows[0].actor == "planner"
    assert "<mark>decision</mark>" in text_history.rows[0].summary_highlight_html
    assert "analysis decision checkpoint" in text_history.rows[0].detail
    assert "<mark>decision</mark>" in text_history.rows[0].detail_highlight_html

    _snapshot, compact_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O2/analysis room_text:decision",
        scope="room",
        compact_mode=True,
        limit=20,
    )
    assert compact_history.compact_mode is True

    compact_status, _compact_headers, compact_body = dashboard_app.build_dashboard_response(
        "/control/history?q=room%3AO2%2Fanalysis%20room_text%3Adecision&scope=room&compact=1&limit=20",
        config,
    )
    compact_text = compact_body.decode("utf-8")
    assert compact_status == 200
    assert "compact</span><strong>on</strong>" in compact_text
    assert 'name="compact" value="1" checked' in compact_text
    assert "&scope=room&compact=1&limit=20" in compact_text
    assert "<mark>decision</mark>" in compact_text


def test_control_dashboard_history_route_derives_dynamic_room_text_shortcuts(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    room_dir = team_dir / "logs" / "rooms" / "O3" / "review"
    room_dir.mkdir(parents=True, exist_ok=True)
    (room_dir / "2026-04-16.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-04-16T10:11:00+09:00",
                        "actor": "operator",
                        "kind": "note",
                        "text": "bridge handoff rollback triage",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-04-16T10:12:00+09:00",
                        "actor": "codex",
                        "kind": "reply",
                        "text": "bridge handoff rollback",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-04-16T10:13:00+09:00",
                        "actor": "planner",
                        "kind": "note",
                        "text": "bridge triage",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O3/review",
        scope="room",
        compact_mode=True,
        limit=20,
    )
    assert history.compact_mode is True
    assert history.room_text_filters == []
    assert history.room_text_query_base == "room:O3/review"
    assert history.room_text_counts == {"bridge": 3, "handoff": 2, "rollback": 2, "triage": 2}
    assert history.room_text_values == ["bridge", "triage", "handoff", "rollback"]
    assert history.room_text_hints == {
        "bridge": "latest 10:13",
        "triage": "latest 10:13",
        "handoff": "recent 10:12",
        "rollback": "recent 10:12",
    }

    _snapshot, selected_history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="room:O3/review room_text:rollback",
        scope="room",
        limit=20,
    )
    assert selected_history.room_text_filters == ["rollback"]
    assert selected_history.room_text_query_base == "room:O3/review"
    assert selected_history.room_text_values == ["bridge", "triage", "handoff", "rollback"]
    assert selected_history.room_text_hints["rollback"] == "recent 10:12"
    assert selected_history.total_rows == 2


def test_control_dashboard_history_route_renders_query_results(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 420, "python": 120, "tmux": 3, "codex": 12})
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/history?q=offdesk_prepare&scope=control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "History Search" in text
    assert "offdesk_prepare" in text
    assert "latest intent | offdesk | offdesk_prepare" in text
    assert "/offdesk review" in text
    assert "history_focus" in text
    assert "Python Pressure" in text
    assert "action_copy" in text
    assert "start with Health, then keep Package Rail narrow" in text
    assert "pressure-kind-badge" in text


def test_control_dashboard_task_detail_route_redirects_alias_to_request_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "preview_only"
    task["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator must decide the analysis handoff wording"
    task["phase1_mode"] = "ensemble"
    task["phase1_rounds"] = 3
    task["phase1_current_round"] = 3
    task["phase1_current_total_rounds"] = 3
    task["phase1_current_phase"] = "verification"
    task["phase1_current_provider"] = "codex"
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_providers"] = ["codex", "claude"]
    task["phase1_planner_providers"] = ["codex"]
    task["phase1_critic_providers"] = ["claude"]
    task["plan"] = {
        "summary": "approved analysis plan",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Analyst", "title": "Re-check evidence links"},
            {"id": "S2", "owner_role": "Codex-Reviewer", "title": "Validate caveats"},
        ],
    }
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for execution"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_task_contract_module"] = "analysis"
    task["background_run_task_contract_module_summary"] = "analysis | analysis/review signals"
    task["background_run_task_contract_policy_summary"] = (
        "analysis | policy=findings_evidence_gate | result=findings+evidence | "
        "apply=advisory_review | loop=evidence_review"
    )
    task["background_run_worker_gate_summary"] = "state=findings_stable | findings=1 | refs=1 | stop=findings_stable"
    task["background_run_worker_profile_summary"] = (
        "analysis_findings_profile | state=findings_stable | findings=1 | evidence=1 | gaps=0 | targets=2 | cautions=1"
    )
    task["background_run_worker_checklist_summary"] = (
        "analysis_checklist | state=findings_stable | findings=1,evidence=1,gaps=0 | next=validate_caveats"
    )
    task["background_run_worker_items_summary"] = (
        "analysis_items | finding:update reports/summary.md,evidence:reports/summary.md"
    )
    task["background_run_worker_items"] = [
        "finding:update reports/summary.md",
        "evidence:reports/summary.md",
    ]
    task["background_run_worker_item_classes_summary"] = (
        "analysis_item_classes | finding=1 | evidence=1 | gap=0 | caveat=0"
    )
    task["background_run_worker_item_classes"] = [
        "finding=1",
        "evidence=1",
        "gap=0",
        "caveat=0",
    ]
    task["background_run_worker_records_summary"] = (
        "analysis_records | finding_record=update reports/summary.md | evidence_record=reports/summary.md | caveat_record=-"
    )
    task["background_run_worker_records"] = [
        "finding_record=update reports/summary.md",
        "evidence_record=reports/summary.md",
        "caveat_record=-",
    ]
    task["background_run_worker_record_rows_summary"] = (
        "analysis_record_rows | finding_row=update reports/summary.md|state=stable | "
        "evidence_row=reports/summary.md|state=attached | caveat_row=-|state=clear|note=findings_stable"
    )
    task["background_run_worker_record_rows"] = [
        "finding_row=update reports/summary.md|state=stable",
        "evidence_row=reports/summary.md|state=attached",
        "caveat_row=-|state=clear|note=findings_stable",
    ]
    task["background_run_worker_record_set_summary"] = (
        "analysis_record_set | finding=1 | evidence=1 | caveat=1"
    )
    task["background_run_worker_record_set"] = [
        {"kind": "finding", "label": "update reports/summary.md", "state": "stable", "note": "action"},
        {"kind": "evidence", "label": "reports/summary.md", "state": "attached", "note": "ref"},
        {"kind": "caveat", "label": "keep review lane open", "state": "review", "note": "validate_caveats"},
    ]
    task["background_run_worker_preflight_summary"] = (
        "analysis_preflight | state=review_ready | finding=stable | evidence=attached | gap=- | apply=ready | next=validate_caveats"
    )
    task["background_run_worker_preflight_rows_summary"] = (
        "analysis_preflight_rows | finding_ready=stable|state=ready|note=findings | "
        "evidence_ready=attached|state=ready|note=evidence | gap_closed=clear|state=ready|note=validate_caveats | "
        "review_ready=review_ready|state=ready|note=validate_caveats"
    )
    task["background_run_worker_preflight_rows"] = [
        "finding_ready=stable|state=ready|note=findings",
        "evidence_ready=attached|state=ready|note=evidence",
        "gap_closed=clear|state=ready|note=validate_caveats",
        "review_ready=review_ready|state=ready|note=validate_caveats",
    ]
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)
    _persist_general_subagent_artifact(project_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    redirect_status, redirect_headers, redirect_body = dashboard_app.build_dashboard_response("/control/O2/tasks/T-001", config)
    detail_status, detail_headers, detail_body = dashboard_app.build_dashboard_response("/control/tasks/by-request/REQ-1", config)

    text = detail_body.decode("utf-8")

    assert redirect_status == 302
    assert redirect_headers["Location"] == "/control/tasks/by-request/REQ-1"
    assert redirect_body == b""
    assert detail_status == 200
    assert detail_headers["Content-Type"].startswith("text/html")
    assert "Task T-001 | analysis-check" in text
    assert "phase1=analysis phase2=analysis" in text
    assert "exec=Codex-Analyst,Claude-Analyst | review=Codex-Reviewer,Claude-Reviewer" in text
    assert "critic=Codex-Reviewer | integration=Codex-Analyst" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "conclusion is supported by inspectable evidence and explicit caveats" in text
    assert "execution_brief" in text
    assert "brief_summary" in text
    assert "job_contract" in text
    assert "job_goal" in text
    assert "job_scope" in text
    assert "job_acceptance" in text
    assert "planning_compact" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "planning_lanes" in text
    assert "draft via codex | review via claude" in text
    assert "planner_lane" in text
    assert "critic_lane" in text
    assert "critic_review" in text
    assert "native_review" in text
    assert "approved_plan_gate" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "ready for execution" in text
    assert "approved_plan" in text
    assert "approved analysis plan" in text
    assert "Re-check evidence links" in text
    assert "debug_packet" in text
    assert "debug_symptom" in text
    assert "debug_next" in text
    assert "phase_checkpoint" in text
    assert "checkpoint_current" in text
    assert "planning_handoff" in text
    assert "followup_brief" in text
    assert "preview_only" in text
    assert "followup_exec_lanes" in text
    assert "L2" in text
    assert "followup_review_lanes" in text
    assert "R1" in text
    assert "followup_reason" in text
    assert "analysis handoff wording" in text
    assert "context_pack" in text
    assert "profile=followup_preview" in text
    assert "/control/chat" in text
    assert "Open Chat Console" in text
    assert "context_pack_docs" in text
    assert "subagent_contract" in text
    assert "general_research | profile=followup_preview | backend=filesystem" in text
    assert "subagent_evidence" in text
    assert "general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_artifact" in text
    assert "harness_authoring/subagents/req-1-general-research.json" in text
    assert "judge_binding" in text
    assert "judge=unbound:claude-opus-4.1" in text
    assert "judge_probe" in text
    assert "status=unbound" in text
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=preview_only exec=L2 review=R1" in text
    assert "bg=running/local_background" in text
    assert "background_run" in text
    assert "runner_target" in text
    assert "local_background" in text
    assert "run_lock" in text
    assert "open" in text
    assert "background_slots" in text
    assert "active=0 limit=1" in text
    assert "idle (0/1)" in text
    assert "background_ticket" in text
    assert "BGT-001" in text
    assert "runtime_handle" in text
    assert "aoe_bg_bgt_001" in text
    assert "runtime_summary" in text
    assert "tmux_session=aoe_bg_bgt_001" in text
    assert "launch_spec" in text
    assert "gateway_dispatch | mode=in_process_callback" in text
    assert "background_task_contract" in text
    assert "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2" in text
    assert "background_worker_module" in text
    assert "analysis | analysis/review signals" in text
    assert "background_worker_policy" in text
    assert "policy=findings_evidence_gate | result=findings+evidence" in text
    assert "background_worker_gate" in text
    assert "state=findings_stable | findings=1 | refs=1 | stop=findings_stable" in text
    assert "background_worker_profile" in text
    assert "analysis_findings_profile | state=findings_stable | findings=1 | evidence=1 | gaps=0 | targets=2 | cautions=1" in text
    assert "background_worker_checklist" in text
    assert "analysis_checklist | state=findings_stable | findings=1,evidence=1,gaps=0 | next=validate_caveats" in text
    assert "background_worker_items" in text
    assert "/control/chat" in text
    assert "Open Chat Console" in text
    assert "analysis_items | finding:update reports/summary.md,evidence:reports/summary.md" in text
    assert "background_worker_item_tokens" in text
    assert "finding:update reports/summary.md, evidence:reports/summary.md" in text
    assert "background_worker_item_classes" in text
    assert "analysis_item_classes | finding=1 | evidence=1 | gap=0 | caveat=0" in text
    assert "background_worker_item_class_tokens" in text
    assert "finding=1, evidence=1, gap=0, caveat=0" in text
    assert "background_worker_records" in text
    assert "analysis_records | finding_record=update reports/summary.md | evidence_record=reports/summary.md | caveat_record=-" in text
    assert "background_worker_record_tokens" in text
    assert "finding_record=update reports/summary.md, evidence_record=reports/summary.md, caveat_record=-" in text
    assert "background_worker_record_rows" in text
    assert (
        "analysis_record_rows | finding_row=update reports/summary.md|state=stable | evidence_row=reports/summary.md|state=attached | caveat_row=-|state=clear|note=findings_stable"
        in text
    )
    assert "background_worker_record_row_tokens" in text
    assert (
        "finding_row=update reports/summary.md|state=stable, evidence_row=reports/summary.md|state=attached, caveat_row=-|state=clear|note=findings_stable"
        in text
    )
    assert "background_worker_record_set" in text
    assert "analysis_record_set | finding=1 | evidence=1 | caveat=1" in text
    assert "background_worker_record_set_tokens" in text
    assert (
        "finding:update reports/summary.md|state=stable|note=action, evidence:reports/summary.md|state=attached|note=ref, caveat:keep review lane open|state=review|note=validate_caveats"
        in text
    )
    assert "background_worker_preflight" in text
    assert (
        "analysis_preflight | state=review_ready | finding=stable | evidence=attached | gap=- | apply=ready | next=validate_caveats"
        in text
    )
    assert "background_worker_preflight_rows" in text
    assert (
        "analysis_preflight_rows | finding_ready=stable|state=ready|note=findings | evidence_ready=attached|state=ready|note=evidence | gap_closed=clear|state=ready|note=validate_caveats | review_ready=review_ready|state=ready|note=validate_caveats"
        in text
    )
    assert "background_worker_preflight_row_tokens" in text
    assert (
        "finding_ready=stable|state=ready|note=findings, evidence_ready=attached|state=ready|note=evidence, gap_closed=clear|state=ready|note=validate_caveats, review_ready=review_ready|state=ready|note=validate_caveats"
        in text
    )
    assert "background_worker_result" in text
    assert "status=ready | worker summary drafted | actions=1 | refs=1" in text
    assert "background_worker_actions" in text
    assert "update reports/summary.md" in text
    assert "background_worker_cautions" in text
    assert "keep review lane open" in text
    assert "background_worker_refs" in text
    assert "background_worker_update" in text
    assert "status=ready | proposals=1 | ids=PROP-001 | targets=reports/summary.md" in text
    assert "background_worker_update_stub" in text
    assert "status=ready | targets=reports/summary.md | actions=1 | refs=1" in text
    assert "background_worker_targets" in text
    assert "background_worker_proposals" in text
    assert "evidence_bundle" in text
    assert "awaiting_review" in text
    assert "control_intent_action" in text
    assert "offdesk_prepare" in text
    assert "first_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "execution=L1 | review=R1" in text
    assert "Task Team Observatory" in text
    assert "task-scoped freshness fallback" in text
    assert "waiting on execution lane(s): L1" in text
    assert "conflict_file_count" in text
    assert "touched_file_count" in text
    assert "files=2" in text
    assert "conflicts=1" in text
    assert "reports/summary.md" in text
    assert "R1" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text
    assert "/monitor O2" in text
    assert "/offdesk review" in text
    assert "Follow-up Preview" in text
    assert "/control/actions/task/followup" in text
    assert "data-dashboard-action" in text


def test_control_dashboard_task_detail_route_loads_hidden_project_by_request_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["projects"]["alpha"]["ops_hidden"] = True
    state["projects"]["alpha"]["ops_hidden_reason"] = "internal fallback project"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    detail_status, detail_headers, detail_body = dashboard_app.build_dashboard_response("/control/tasks/by-request/REQ-1", config)
    text = detail_body.decode("utf-8")

    assert detail_status == 200
    assert detail_headers["Content-Type"].startswith("text/html")
    assert "Task T-001 | analysis-check" in text
    assert "action-section" in text
    assert "action-cluster-title" in text
    assert "/offdesk review" in text


def test_control_dashboard_runtime_detail_route_loads_hidden_project_by_alias(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    alpha = state["projects"]["alpha"]
    alpha["ops_hidden"] = True
    alpha["ops_hidden_reason"] = "project on hold"
    alpha["paused"] = True
    alpha["paused_reason"] = "awaiting archive decision"
    for task in alpha["tasks"].values():
        task["status"] = "completed"
        task["stage"] = "completed"
    alpha["todos"][0]["status"] = "done"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    detail_status, detail_headers, detail_body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    text = detail_body.decode("utf-8")

    assert "O2" not in {card.project_alias for card in snapshot.runtime_cards}
    assert detail_status == 200
    assert detail_headers["Content-Type"].startswith("text/html")
    assert "O2 Alpha" in text
    assert "ops_hidden: project on hold" in text
    assert "ops_scope" in text
    assert "analysis-check" in text
    assert "analysis-followup" in text


def test_control_dashboard_state_resolves_alias_route_via_request_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)

    resolved = dashboard_state.resolve_task_request_for_alias_route(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        project_alias="O2",
        task_short_id="T-001",
    )

    assert resolved == "REQ-1"


def test_control_dashboard_runtime_detail_route_renders_runtime_scope(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "preview_only"
    task["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator must decide the analysis handoff wording"
    task["phase1_mode"] = "ensemble"
    task["phase1_rounds"] = 3
    task["phase1_current_round"] = 3
    task["phase1_current_total_rounds"] = 3
    task["phase1_current_phase"] = "verification"
    task["phase1_current_provider"] = "codex"
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_providers"] = ["codex", "claude"]
    task["phase1_planner_providers"] = ["codex"]
    task["phase1_critic_providers"] = ["claude"]
    task["plan"] = {
        "summary": "approved analysis plan",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Analyst", "title": "Re-check evidence links"},
            {"id": "S2", "owner_role": "Codex-Reviewer", "title": "Validate caveats"},
        ],
    }
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for execution"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_task_contract_module"] = "analysis"
    task["background_run_task_contract_module_summary"] = "analysis | analysis/review signals"
    task["background_run_task_contract_policy_summary"] = (
        "analysis | policy=findings_evidence_gate | result=findings+evidence | "
        "apply=advisory_review | loop=evidence_review"
    )
    task["background_run_worker_gate_summary"] = "state=findings_stable | findings=1 | refs=1 | stop=findings_stable"
    task["background_run_worker_profile_summary"] = (
        "analysis_findings_profile | state=findings_stable | findings=1 | evidence=1 | gaps=0 | targets=2 | cautions=1"
    )
    task["background_run_worker_checklist_summary"] = (
        "analysis_checklist | state=findings_stable | findings=1,evidence=1,gaps=0 | next=validate_caveats"
    )
    task["background_run_worker_items_summary"] = (
        "analysis_items | finding:update reports/summary.md,evidence:reports/summary.md"
    )
    task["background_run_worker_items"] = [
        "finding:update reports/summary.md",
        "evidence:reports/summary.md",
    ]
    task["background_run_worker_item_classes_summary"] = (
        "analysis_item_classes | finding=1 | evidence=1 | gap=0 | caveat=0"
    )
    task["background_run_worker_item_classes"] = [
        "finding=1",
        "evidence=1",
        "gap=0",
        "caveat=0",
    ]
    task["background_run_worker_records_summary"] = (
        "analysis_records | finding_record=update reports/summary.md | evidence_record=reports/summary.md | caveat_record=-"
    )
    task["background_run_worker_records"] = [
        "finding_record=update reports/summary.md",
        "evidence_record=reports/summary.md",
        "caveat_record=-",
    ]
    task["background_run_worker_record_rows_summary"] = (
        "analysis_record_rows | finding_row=update reports/summary.md|state=stable | "
        "evidence_row=reports/summary.md|state=attached | caveat_row=-|state=clear|note=findings_stable"
    )
    task["background_run_worker_record_rows"] = [
        "finding_row=update reports/summary.md|state=stable",
        "evidence_row=reports/summary.md|state=attached",
        "caveat_row=-|state=clear|note=findings_stable",
    ]
    task["background_run_worker_record_set_summary"] = (
        "analysis_record_set | finding=1 | evidence=1 | caveat=1"
    )
    task["background_run_worker_record_set"] = [
        {"kind": "finding", "label": "update reports/summary.md", "state": "stable", "note": "action"},
        {"kind": "evidence", "label": "reports/summary.md", "state": "attached", "note": "ref"},
        {"kind": "caveat", "label": "keep review lane open", "state": "review", "note": "validate_caveats"},
    ]
    task["background_run_worker_preflight_summary"] = (
        "analysis_preflight | state=review_ready | finding=stable | evidence=attached | gap=- | apply=ready | next=validate_caveats"
    )
    task["background_run_worker_preflight_rows_summary"] = (
        "analysis_preflight_rows | finding_ready=stable|state=ready|note=findings | "
        "evidence_ready=attached|state=ready|note=evidence | gap_closed=clear|state=ready|note=validate_caveats | "
        "review_ready=review_ready|state=ready|note=validate_caveats"
    )
    task["background_run_worker_preflight_rows"] = [
        "finding_ready=stable|state=ready|note=findings",
        "evidence_ready=attached|state=ready|note=evidence",
        "gap_closed=clear|state=ready|note=validate_caveats",
        "review_ready=review_ready|state=ready|note=validate_caveats",
    ]
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)
    _persist_general_subagent_artifact(project_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Runtime O2" in text
    assert "open=0 running=1 blocked=0 followup=0 pending=no" in text
    assert "open=0 | priorities=- | kinds=-" in text
    assert "score=0 | providers=0 | retry_wait=-" in text
    assert "control_intent_action" in text
    assert "offdesk_prepare" in text
    assert "first_focus" in text
    assert "next=/offdesk review O2" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "analysis-check" in text
    assert "execution_brief" in text
    assert "underspecified" in text
    assert "brief_summary" in text
    assert "job_contract" in text
    assert "job_goal" in text
    assert "job_scope" in text
    assert "job_acceptance" in text
    assert "planning_compact" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "planning_lanes" in text
    assert "draft via codex | review via claude" in text
    assert "planner_lane" in text
    assert "critic_lane" in text
    assert "critic_review" in text
    assert "native_review" in text
    assert "approved_plan_gate" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "ready for execution" in text
    assert "approved_plan" in text
    assert "approved analysis plan" in text
    assert "Re-check evidence links" in text
    assert "debug_packet" in text
    assert "debug_symptom" in text
    assert "debug_next" in text
    assert "phase_checkpoint" in text
    assert "checkpoint_current" in text
    assert "planning_handoff" in text
    assert "brief_do" in text
    assert "reports/summary.md" in text
    assert "brief_blocked" in text
    assert "acceptance_gap" in text
    assert "brief_decision" in text
    assert "confirm acceptance scope before off-desk execution" in text
    assert "followup_brief" in text
    assert "preview_only | execution=L2 | review=R1" in text
    assert "followup_exec_lanes" in text
    assert "followup_review_lanes" in text
    assert "followup_reason" in text
    assert "analysis handoff wording" in text
    assert "context_pack" in text
    assert "profile=followup_preview" in text
    assert "context_pack_docs" in text
    assert "subagent_contract" in text
    assert "general_research | profile=followup_preview | backend=filesystem" in text
    assert "subagent_evidence" in text
    assert "general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_artifact" in text
    assert "harness_authoring/subagents/req-1-general-research.json" in text
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=preview_only exec=L2 review=R1" in text
    assert "bg=running/local_background" in text
    assert "background_run" in text
    assert "runner_target" in text
    assert "local_background" in text
    assert "background_scheduler" in text
    assert "background_ticket" in text
    assert "BGT-001" in text
    assert "runtime_handle" in text
    assert "aoe_bg_bgt_001" in text
    assert "runtime_summary" in text
    assert "tmux_session=aoe_bg_bgt_001" in text
    assert "launch_spec" in text
    assert "gateway_dispatch | mode=in_process_callback" in text
    assert "background_task_contract" in text
    assert "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2" in text
    assert "background_worker_module" in text
    assert "analysis | analysis/review signals" in text
    assert "background_worker_policy" in text
    assert "policy=findings_evidence_gate | result=findings+evidence" in text
    assert "background_worker_gate" in text
    assert "state=findings_stable | findings=1 | refs=1 | stop=findings_stable" in text
    assert "background_worker_profile" in text
    assert "analysis_findings_profile | state=findings_stable | findings=1 | evidence=1 | gaps=0 | targets=2 | cautions=1" in text
    assert "background_worker_checklist" in text
    assert "analysis_checklist | state=findings_stable | findings=1,evidence=1,gaps=0 | next=validate_caveats" in text
    assert "background_worker_items" in text
    assert "analysis_items | finding:update reports/summary.md,evidence:reports/summary.md" in text
    assert "background_worker_item_tokens" in text
    assert "finding:update reports/summary.md, evidence:reports/summary.md" in text
    assert "background_worker_item_classes" in text
    assert "analysis_item_classes | finding=1 | evidence=1 | gap=0 | caveat=0" in text
    assert "background_worker_item_class_tokens" in text
    assert "finding=1, evidence=1, gap=0, caveat=0" in text
    assert "background_worker_records" in text
    assert "analysis_records | finding_record=update reports/summary.md | evidence_record=reports/summary.md | caveat_record=-" in text
    assert "background_worker_record_tokens" in text
    assert "finding_record=update reports/summary.md, evidence_record=reports/summary.md, caveat_record=-" in text
    assert "background_worker_record_rows" in text
    assert (
        "analysis_record_rows | finding_row=update reports/summary.md|state=stable | evidence_row=reports/summary.md|state=attached | caveat_row=-|state=clear|note=findings_stable"
        in text
    )
    assert "background_worker_record_row_tokens" in text
    assert (
        "finding_row=update reports/summary.md|state=stable, evidence_row=reports/summary.md|state=attached, caveat_row=-|state=clear|note=findings_stable"
        in text
    )
    assert "background_worker_record_set" in text
    assert "analysis_record_set | finding=1 | evidence=1 | caveat=1" in text
    assert "background_worker_record_set_tokens" in text
    assert (
        "finding:update reports/summary.md|state=stable|note=action, evidence:reports/summary.md|state=attached|note=ref, caveat:keep review lane open|state=review|note=validate_caveats"
        in text
    )
    assert "background_worker_preflight" in text
    assert (
        "analysis_preflight | state=review_ready | finding=stable | evidence=attached | gap=- | apply=ready | next=validate_caveats"
        in text
    )
    assert "background_worker_preflight_rows" in text
    assert (
        "analysis_preflight_rows | finding_ready=stable|state=ready|note=findings | evidence_ready=attached|state=ready|note=evidence | gap_closed=clear|state=ready|note=validate_caveats | review_ready=review_ready|state=ready|note=validate_caveats"
        in text
    )
    assert "background_worker_preflight_row_tokens" in text
    assert (
        "finding_ready=stable|state=ready|note=findings, evidence_ready=attached|state=ready|note=evidence, gap_closed=clear|state=ready|note=validate_caveats, review_ready=review_ready|state=ready|note=validate_caveats"
        in text
    )
    assert "background_worker_result" in text
    assert "status=ready | worker summary drafted | actions=1 | refs=1" in text
    assert "background_worker_actions" in text
    assert "update reports/summary.md" in text
    assert "background_worker_cautions" in text
    assert "keep review lane open" in text
    assert "background_worker_refs" in text
    assert "background_worker_update" in text
    assert "status=ready | proposals=1 | ids=PROP-001 | targets=reports/summary.md" in text
    assert "background_worker_update_stub" in text
    assert "status=ready | targets=reports/summary.md | actions=1 | refs=1" in text
    assert "background_worker_targets" in text
    assert "background_worker_proposals" in text
    assert "evidence_bundle" in text
    assert "awaiting_review" in text
    assert "analysis-followup" in text
    assert "/control/actions/runtime/sync-preview" in text
    assert "Sync Preview (24h)" in text
    assert "/control/actions/task/followup" in text
    assert "/offdesk review" in text
    assert "/monitor O2" in text
    assert "/todo O2" in text
    assert "action-section" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text


def test_control_dashboard_runtime_detail_renders_document_flow_card(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    _write_project_flow_fixture(project_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    snapshot, detail = dashboard_state.load_dashboard_runtime_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        project_alias="O2",
    )
    status, headers, body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    text = body.decode("utf-8")

    assert snapshot.control_summary.active_runtime_count >= 1
    assert detail is not None
    assert detail.document_flow.summary.startswith("alias=O2 status=active drift=none")
    assert detail.document_flow.drift_level == "none"
    assert detail.document_flow.objective == "Connect document flow to runtime detail."
    assert detail.document_flow.next_steps == ["TODO-001: Render Document Flow card"]
    assert detail.document_flow.latest_tf_report_path == "docs/investigations_mo/projects/O2/tfs/TF-002/report.md"
    assert detail.document_flow.open_tf_ids == ["TF-002"]
    assert detail.document_flow.recent_closed_tf_ids == ["TF-001"]
    assert "registry: docs/investigations_mo/registry/project_registry.md" in detail.document_flow.evidence_refs
    assert "runtime_request: REQ-1" in detail.document_flow.evidence_refs
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Document Flow" in text
    assert "Connect document flow to runtime detail." in text
    assert "TODO-001: Render Document Flow card" in text
    assert "Keep dashboard document flow read-only." in text
    assert "Recovery excerpt is still separate." in text
    assert "project-flow/O2/latest.json" in text
    assert "docs/investigations_mo/projects/O2/tfs/TF-002/report.md" in text


def test_control_dashboard_runtime_detail_surfaces_model_routing_summary(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    (project_team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "claude-sonnet-shell",
                        "provider_kind": "anthropic",
                        "model": "claude-sonnet-4",
                        "enabled": True,
                        "supports_tools": True,
                    },
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_team_dir / "model_routing.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profile": "default",
                "routes": {
                    "on_desk_primary": {"endpoint_id": "claude-sonnet-shell"},
                    "background_worker_primary": {"endpoint_id": "ollama-qwen3"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    workspace_brief.write_workspace_brief(
        project_team_dir,
        {
            "project_root": str(project_root),
            "project_alias": "O2",
            "onboarding_status": "active",
            "doc_roots": [str((project_root / "docs").resolve())],
            "canonical_todo_path": str((project_team_dir / "AOE_TODO.md").resolve()),
        },
        project_root=project_root,
        entry={"background_runner_target": "local_background"},
    )
    document_registry.write_document_registry(
        project_team_dir,
        {
            "records": [
                {
                    "doc_id": "alpha-runbook",
                    "path": str((project_root / "docs" / "RUNBOOK.md").resolve()),
                    "doc_type": "runbook",
                    "source_kind": "markdown",
                    "title": "Runbook",
                    "canonical": True,
                    "freshness_class": "fresh",
                    "ingest_status": "indexed",
                }
            ]
        },
        project_root=project_root,
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "model_routing" in text
    assert "profile=default" in text
    assert "ondesk=claude-sonnet-shell:claude-sonnet-4" in text
    assert "bg=ollama-qwen3:qwen3-coder:30b" in text
    assert "model_plan" in text
    assert "worker=none" in text
    assert "judge=none" in text
    assert "judge_binding" in text
    assert "judge=unbound:claude-opus-4.1" in text
    assert "judge_probe" in text
    assert "status=unbound" in text
    assert "workspace" in text
    assert "status=active" in text
    assert "document_registry" in text
    assert "indexed=1 canonical=1" in text
    assert "model_registry" in text
    assert "enabled=2 bound=2/5 local=1 kinds=anthropic=1, ollama=1" in text
    assert "latest_judge" in text
    assert "latest_judge_decision" in text


def test_control_dashboard_runtime_and_task_detail_prefer_recent_judge_model_ping(
    tmp_path: Path, monkeypatch
) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    audit_dir = project_team_dir / "dashboard"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "action-history.jsonl").write_text(
        json.dumps(
            {
                "at": "2026-04-09T10:01:00+09:00",
                "headline": "Model Ping Judge | executed",
                "status": "executed",
                "outcome_kind": "model_ping",
                "outcome_status": "executed",
                "outcome_reason_code": "ok",
                "outcome_detail": "endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
                "next_step": "/orch status O2",
                "remediation": "inspect binding summary and route probe status if the bounded invoke did not execute",
                "source_command": "/orch model-ping O2 judge",
                "link_href": "/control/runtimes/O2",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        model_endpoint_adapter,
        "resolve_task_judge_binding",
        lambda *args, **kwargs: {
            "bound": True,
            "summary": "judge=claude_code_cli-opus:opus",
            "endpoint": {
                "endpoint_id": "claude_code_cli-opus",
                "provider_kind": "claude_code_cli",
            },
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, _headers, body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    runtime_text = body.decode("utf-8")
    assert status == 200
    assert "judge_binding" in runtime_text
    assert "judge=claude_code_cli-opus:opus" in runtime_text
    assert "judge_probe" in runtime_text
    assert "status=last_invoke_ok" in runtime_text

    status, _headers, body = dashboard_app.build_dashboard_response("/control/tasks/by-request/REQ-1", config)
    task_text = body.decode("utf-8")
    assert status == 200
    assert "judge_binding" in task_text
    assert "judge=claude_code_cli-opus:opus" in task_text
    assert "judge_probe" in task_text
    assert "status=last_invoke_ok" in task_text


def test_control_dashboard_surfaces_external_background_phase_in_runtime_and_offdesk(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_runner_target"] = "github_runner"
    task["background_run_status"] = "running"
    task["background_run_ticket_id"] = "BGT-GHA-ACK-001"
    task["background_run_launch_mode"] = "dashboard_retry"
    task["background_run_runtime_handle"] = "background_run_handoffs/github-runner-bgt-gha-ack-001.json"
    task["background_run_runtime_summary"] = (
        "github_runner_handoff=background_run_handoffs/github-runner-bgt-gha-ack-001.json"
        " | ack=background_run_acks/github-runner-bgt-gha-ack-001.json"
    )
    task["background_run_evidence_bundle"] = (
        "status=running | outcome=external_pickup_acknowledged"
        " | ack=background_run_acks/github-runner-bgt-gha-ack-001.json"
    )
    task["background_run_evidence_artifacts"] = [
        "background_run_handoffs/github-runner-bgt-gha-ack-001.json",
        "background_run_acks/github-runner-bgt-gha-ack-001.json",
    ]
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    runtime_status, runtime_headers, runtime_body = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)
    offdesk_status, offdesk_headers, offdesk_body = dashboard_app.build_dashboard_response("/control/offdesk", config)

    runtime_text = runtime_body.decode("utf-8")
    offdesk_text = offdesk_body.decode("utf-8")

    assert runtime_status == 200
    assert runtime_headers["Content-Type"].startswith("text/html")
    assert "background_external" in runtime_text
    assert "pickup_acknowledged" in runtime_text
    assert "background_run_acks/github-runner-bgt-gha-ack-001.json" in runtime_text

    assert offdesk_status == 200
    assert offdesk_headers["Content-Type"].startswith("text/html")
    assert "background_external" in offdesk_text
    assert "pickup_acknowledged" in offdesk_text
    assert "background_run_acks/github-runner-bgt-gha-ack-001.json" in offdesk_text


def test_control_dashboard_offdesk_route_shows_execution_brief_snapshot(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_planner_providers"] = ["codex"]
    task["phase1_critic_providers"] = ["claude"]
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for execution"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/offdesk", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Offdesk Prep" in text
    assert "execution_brief" in text
    assert "underspecified" in text
    assert "brief_summary" in text
    assert "blocked=acceptance_gap" in text
    assert "planning_lanes" in text
    assert "draft via codex | review via claude" in text
    assert "approved_plan_gate" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "approved_plan" in text
    assert "planning_compact" in text
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=none" in text
    assert "bg=running/local_background" in text
    assert "execution_brief_summary" in text
    assert "underspecified=1" in text
    assert "background_run_summary" in text
    assert "status running=1" in text
    assert "server_guard" in text
    assert "server_guard_note" in text
    assert "server_guard_snapshot" in text
    assert "planning_lanes" in text
    assert "draft via codex | review via claude" in text
    assert "approved_plan_gate" in text
    assert "dispatch unlocked after critic approval | review via claude" in text
    assert "approved_plan" in text
    assert "Open Health JSON" in text
    assert "server-guard" in text
    assert "background_scheduler" in text
    assert "Decision Signals" in text
    assert "Execution Rails" in text
    assert "Project Progress Board" in text
    assert "reports/summary.md" in text
    assert "acceptance_gap" in text
    assert "confirm acceptance scope before off-desk execution" in text
    assert "local_background" in text
    assert "run_lock" in text
    assert "open" in text
    assert "background_slots" in text
    assert "active=0 limit=1" in text
    assert "idle (0/1)" in text
    assert "BGT-001" in text
    assert "runtime_handle" in text
    assert "aoe_bg_bgt_001" in text
    assert "runtime_summary" in text
    assert "tmux_session=aoe_bg_bgt_001" in text
    assert "awaiting_review" in text
    assert "context_pack" in text
    assert "model_plan" in text
    assert "/offdesk review O2" in text


def test_control_dashboard_recovery_route_renders_latest_nightly_summary(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "room": "O2/review",
            "selected_task_refs": {"alpha": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (team_dir / "telegram_chat_aliases.json").write_text(
        json.dumps({"1": "123456"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:05:00+09:00",
        extra={
            "response_text": json.dumps(
                {
                    "verdict": "continue",
                    "confidence": "medium",
                    "reasoning": "brief executable",
                    "next_step": "/retry T-001",
                    "caution": "review lane remains",
                }
            )
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="applied",
        outcome_detail="retry_command=/retry T-001",
        next_step="/retry T-001",
        remediation="-",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:06:00+09:00",
    )
    _persist_general_subagent_artifact(project_root)
    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/recovery?focus=server-guard", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Nightly Session Summary" in text
    assert "automation_posture" in text
    assert "auto_active (fanout)" in text
    assert "O2 Alpha" in text
    assert "analysis-check" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=none" in text
    assert "bg=running/local_background" in text
    assert "latest_replan_auto_route" in text
    assert "Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001" in text
    assert "auto_route" in text
    assert "applied=/retry T-001 | at=2026-04-09T11:06:00+09:00" in text
    assert "auto_route_status" in text
    assert "applied=/retry T-001 | at=2026-04-09T11:06:00+09:00" in text
    assert "state_root_mode" in text
    assert str(team_dir.resolve()) in text
    assert "latest_intent_command" in text
    assert "offdesk" in text
    assert "offdesk_prepare" in text
    assert "selected=offdesk_prepare" in text
    assert "server_guard" in text
    assert "server_guard_reasons" in text
    assert "server_guard_note" in text
    assert "server_guard_next" in text
    assert "server_guard_focus" in text
    assert "server_guard_action_copy" in text
    assert "server_guard_priority_link" in text
    assert "server_guard_snapshot" in text
    assert "server_guard_latest_action" in text
    assert "server_guard_latest_result" in text
    assert "focus_filter" in text
    assert "server-guard" in text
    assert "Open Health JSON" in text
    assert "nightly_planning_compact" in text
    assert "nightly_subagent_evidence" in text
    assert "nightly_subagent_gate" in text
    assert "first_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "subagent_contract" in text
    assert "general_research | profile=" in text
    assert "backend=filesystem" in text
    assert "subagent_evidence" in text
    assert "subagent_evidence=general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_gate" in text
    assert "subagent_gate=vendor notes still need a local delta check" in text
    assert "general_research | confidence=high | sources=2 | findings=2 | blocking=1" in text
    assert "subagent_artifact" in text
    assert "harness_authoring/subagents/req-1-general-research.json" in text
    assert "execution_brief_summary" in text
    assert "underspecified=1" in text
    assert "background_run_summary" in text
    assert "status running=1" in text
    assert "background_scheduler" in text
    assert "background_scheduler_note" in text
    assert "no queued scheduler head" in text
    assert "Decision Signals" in text


def test_legacy_control_dashboard_recovery_route_reads_nightly_planning_review_key(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    _mark_task_planning_gate_blocked(task)
    task["background_run_operator_preference_preflight_summary"] = "preflight=chart | auto=1 | confirm=0 | manual=0 | disabled=0"
    task["background_run_operator_preference_applied_summary"] = "applied_preferences=show_source_note=true | on | auto | artifact_kind:chart"
    task["background_run_operator_preference_candidate_summary"] = (
        "preference_candidates=legend_position=bottom | hits=2 | issue=legend keeps overlapping the plotted bars"
    )
    task["background_run_operator_preference_decision_summary"] = (
        "preference_decisions=legend_position=bottom | 앞으로도 적용 | artifact_kind:chart"
    )
    gw.save_manager_state(manager_state_file, state)
    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _latest_md, latest_json = nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    rewrite_latest_nightly_runtime_with_legacy_planning_review_key(latest_json)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/recovery", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "nightly_planning_compact" in text
    assert "draft via codex" in text
    assert "review via" in text
    assert "dispatch blocked until critic clears issues" in text
    assert "BGT-001" in text
    assert "local_background" in text
    assert "preference_preflight" in text
    assert "preference_candidates=legend_position=bottom" in text
    assert "run_lock" in text
    assert "open" in text
    assert "background_slots" in text
    assert "active=0 limit=1" in text
    assert "idle (0/1)" in text


def test_control_dashboard_recovery_hides_package_syncback_actions_when_record_pending(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_worker_records_summary"] = (
        "package_records | artifact_record=dist/release_bundle.zip | verification_record=1 | apply_record=ready | syncback_record=pending"
    )
    task["background_run_worker_records"] = [
        "artifact_record=dist/release_bundle.zip",
        "verification_record=1",
        "apply_record=ready",
        "syncback_record=pending",
    ]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_summary"] = (
        "state=applied | todo=TODO-002 | proposal=PROP-001 | targets=dist/release_bundle.zip | at=2026-04-10T10:06:00+09:00"
    )
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    gw.save_manager_state(manager_state_file, state)

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/recovery", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Preview Accepted Syncback" not in text
    assert "Apply Accepted Syncback" not in text


def test_resolve_control_paths_uses_manager_state_parent_for_sidecar_files(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    control_root.mkdir(parents=True, exist_ok=True)
    custom_team_dir = tmp_path / "custom" / ".aoe-team"
    custom_team_dir.mkdir(parents=True, exist_ok=True)
    manager_state_file = custom_team_dir / "orch_manager_state.json"

    paths = dashboard_state.resolve_control_paths(
        control_root=control_root,
        manager_state_file=manager_state_file,
    )

    assert paths.team_dir == custom_team_dir.resolve()
    assert paths.manager_state_file == manager_state_file.resolve()
    assert paths.auto_state_file == (custom_team_dir / "auto_scheduler.json").resolve()
    assert paths.provider_capacity_file == (custom_team_dir / "provider_capacity.json").resolve()
    assert paths.latest_intent_file == (custom_team_dir / "control" / "latest-intent.json").resolve()
    assert paths.gateway_events_file == (custom_team_dir / "logs" / "gateway_events.jsonl").resolve()
    assert paths.action_audit_file == (custom_team_dir / "dashboard" / "action-history.jsonl").resolve()


def test_resolve_control_paths_uses_runtime_core_default_team_dir_when_state_root_enabled(
    tmp_path: Path, monkeypatch
) -> None:
    control_root = tmp_path / "control"
    state_root = tmp_path / "state-root"
    control_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    paths = dashboard_state.resolve_control_paths(control_root=control_root)

    assert paths.team_dir == gw.resolve_team_dir(control_root, None)
    assert paths.team_dir.parent == state_root.resolve()
    assert paths.manager_state_file == (paths.team_dir / "orch_manager_state.json").resolve()


def test_control_dashboard_prefers_latest_intent_snapshot_over_gateway_events(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    operator_summary.save_latest_command_resolution(
        team_dir,
        command="offdesk",
        action="offdesk_review",
        intent_class="status",
        trace="selected=offdesk_review; matched=review:검토",
        recorded_at="2026-03-16T10:05:00+09:00",
    )
    logs_dir = team_dir / "logs"
    (logs_dir / "gateway_events.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-03-16T10:06:00+09:00",
                "event": "command_resolved",
                "status": "accepted",
                "detail": "cmd=run action=dispatch_task class=work trace=selected=dispatch_task; matched=work:작성",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, _headers, body = dashboard_app.build_dashboard_response("/control", config)
    text = body.decode("utf-8")

    assert status == 200
    assert "latest_intent_action" in text
    assert "offdesk_review" in text
    assert "selected=offdesk_review; matched=review:검토" in text
    assert "dispatch_task" not in text


def test_latest_intent_snapshot_does_not_overwrite_newer_record(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)

    operator_summary.save_latest_command_resolution(
        team_dir,
        command="offdesk",
        action="offdesk_prepare",
        intent_class="status",
        trace="selected=offdesk_prepare; matched=timing:오늘 밤,prepare:점검",
        recorded_at="2026-03-16T10:05:00+09:00",
    )
    operator_summary.save_latest_command_resolution(
        team_dir,
        command="offdesk",
        action="offdesk_review",
        intent_class="status",
        trace="selected=offdesk_review; matched=review:검토",
        recorded_at="2026-03-16T10:04:00+09:00",
    )

    latest = operator_summary.load_latest_command_resolution(team_dir)

    assert latest["command"] == "offdesk"
    assert latest["action"] == "offdesk_prepare"
    assert "오늘 밤" in latest["trace"]


def test_dashboard_task_page_uses_single_manager_snapshot(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)

    call_count = 0
    original = dashboard_state._load_manager_state

    def counting_loader(paths):
        nonlocal call_count
        call_count += 1
        return original(paths)

    monkeypatch.setattr(dashboard_state, "_load_manager_state", counting_loader)

    snapshot, detail = dashboard_state.load_dashboard_task_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )

    assert call_count == 1
    assert snapshot.control_summary.active_runtime_count == 1
    assert detail is not None
    assert detail.request_id == "REQ-1"


def test_control_dashboard_get_action_route_returns_405(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/actions/task/retry", config)
    payload = json.loads(body.decode("utf-8"))

    assert status == 405
    assert headers["Allow"] == "POST"
    assert payload["error"] == "method_not_allowed"
    assert payload["path"] == "/control/actions/task/retry"


def test_control_dashboard_post_retry_route_executes_retry_bridge(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    def _fake_execute_retry_run_transition(transition, *, config, manager_state, paths, source_command, payload):
        return dashboard_app._json(
            {
                "ok": True,
                "implemented": True,
                "executed": True,
                "status": "executed",
                "method": "POST",
                "path": "/control/actions/task/retry",
                "mode": "phase2",
                "source_command": source_command,
                "payload": payload,
                "task": {
                    "request_id": "REQ-RETRY",
                    "label": "T-003 | retry-run",
                    "status": "running",
                    "tf_phase": "planning",
                    "detail_path": "/control/tasks/by-request/REQ-RETRY",
                },
                "next_step": "/task T-003 | retry-run",
                "remediation": "review the updated task detail and lane state before repeating another retry",
                "transition": {
                    "cmd": "run",
                    "orch_target": "alpha",
                    "run_control_mode": "retry",
                    "run_source_request_id": "REQ-1",
                    "run_force_mode": "dispatch",
                    "execution_lane_ids": ["L1"],
                    "review_lane_ids": ["R1"],
                },
            },
            status=200,
        )

    monkeypatch.setattr(dashboard_app, "_execute_retry_run_transition", _fake_execute_retry_run_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1", "R1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["implemented"] is True
    assert payload["executed"] is True
    assert payload["status"] == "executed"
    assert payload["mode"] == "phase2"
    assert payload["source_command"] == "/retry T-001 lane L1,R1"
    assert payload["transition"]["cmd"] == "run"
    assert payload["transition"]["run_control_mode"] == "retry"
    assert payload["transition"]["run_source_request_id"] == "REQ-1"
    assert payload["transition"]["execution_lane_ids"] == ["L1"]
    assert payload["transition"]["review_lane_ids"] == ["R1"]
    assert payload["transition"]["orch_target"] == "alpha"
    assert payload["task"]["request_id"] == "REQ-RETRY"
    assert payload["task"]["detail_path"] == "/control/tasks/by-request/REQ-RETRY"
    assert payload["next_step"] == "/task T-003 | retry-run"
    assert "review the updated task detail" in payload["remediation"]


def test_control_dashboard_post_retry_route_uses_local_tmux_background_when_preferred(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "local_tmux"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_now_iso", lambda: "2026-04-06T12:00:00+09:00")

    class _GatewayMain:
        @staticmethod
        def save_manager_state(path, state):
            Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_load_gateway_main_module", lambda: _GatewayMain)

    def _fake_launch(*, queue_path, ticket_id, runner_target="", now_iso, claimed_by="", source_surface="", launch_mode="offdesk_manual"):
        claimed = background_runs.claim_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            runner_target=runner_target or "local_tmux",
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        assert claimed["status"] == "dispatching"
        return background_runs.advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="running",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            runtime_handle="aoe_bg_retry_req_1",
            runtime_summary="tmux_session=aoe_bg_retry_req_1",
            evidence_bundle="status=running | outcome=tmux_session_started | session=aoe_bg_retry_req_1",
        )

    monkeypatch.setattr(retry_exec, "launch_background_ticket_via_adapter", lambda **kwargs: _fake_launch(**kwargs))

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/retry T-001"
    assert payload["next_step"] == "/orch status O2"
    assert payload["background_run"]["runner_target"] == "local_tmux"
    assert payload["background_run"]["status"] == "running"
    assert payload["background_run"]["runtime_handle"] == "aoe_bg_retry_req_1"
    assert payload["background_run"]["model_plan"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    assert payload["background_run"]["model_pack_profile"] == "review"
    assert payload["background_run"]["model_worker_route_id"] == "background_worker_primary"
    assert payload["transition"]["run_source_request_id"] == "REQ-1"
    assert payload["task"]["request_id"] == "REQ-1"
    assert payload["task"]["detail_path"] == "/control/tasks/by-request/REQ-1"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert task["background_run_runner_target"] == "local_tmux"
    assert task["background_run_status"] == "running"
    assert task["background_run_ticket_id"].startswith("BGT-REQ-1-")
    assert task["background_run_runtime_handle"] == "aoe_bg_retry_req_1"
    assert task["background_run_model_pack_profile"] == "review"
    assert task["background_run_model_plan_summary"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    assert task["background_run_model_judge_binding_summary"] == "judge=unbound:claude-opus-4.1"
    assert task["background_run_model_judge_probe_status"] == "unbound"
    assert task["background_run_model_escalation_binding_summary"] == "bgx=unbound:gpt-oss-or-gemma4"
    assert task["background_run_model_escalation_probe_status"] == "unbound"
    queue_path = Path(updated["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    rows = background_runs.load_background_runs_state(queue_path).get("runs") or []
    launched = [row for row in rows if str(row.get("ticket_id", "")).startswith("BGT-REQ-1-")]
    assert len(launched) == 1
    assert launched[0]["runner_target"] == "local_tmux"
    assert launched[0]["status"] == "running"
    assert launched[0]["runtime_handle"] == "aoe_bg_retry_req_1"
    assert launched[0]["launch_spec"]["model_pack_profile"] == "review"
    assert launched[0]["launch_spec"]["model_plan_summary"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    assert launched[0]["launch_spec"]["model_judge_binding_summary"] == "judge=unbound:claude-opus-4.1"
    assert launched[0]["launch_spec"]["model_judge_probe_status"] == "unbound"
    assert launched[0]["launch_spec"]["model_escalation_binding_summary"] == "bgx=unbound:gpt-oss-or-gemma4"
    assert launched[0]["launch_spec"]["model_escalation_probe_status"] == "unbound"


def test_control_dashboard_post_retry_route_emits_github_runner_handoff_when_preferred(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "github_runner"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_now_iso", lambda: "2026-04-07T13:00:00+09:00")

    class _GatewayMain:
        @staticmethod
        def save_manager_state(path, state):
            Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_load_gateway_main_module", lambda: _GatewayMain)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/retry T-001"
    assert payload["next_step"] == "/orch status O2"
    assert payload["background_run"]["runner_target"] == "github_runner"
    assert payload["background_run"]["status"] == "running"
    assert payload["background_run"]["model_plan"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    assert payload["background_run"]["model_pack_profile"] == "review"
    runtime_handle = payload["background_run"]["runtime_handle"]
    assert runtime_handle.startswith("background_run_handoffs/")
    assert runtime_handle.endswith(".json")
    assert payload["background_run"]["runtime_summary"] == f"github_runner_handoff={runtime_handle}"
    assert payload["transition"]["run_source_request_id"] == "REQ-1"
    assert payload["task"]["request_id"] == "REQ-1"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert task["background_run_runner_target"] == "github_runner"
    assert task["background_run_status"] == "running"
    assert task["background_run_ticket_id"].startswith("BGT-REQ-1-")
    assert task["background_run_runtime_handle"] == runtime_handle
    assert task["background_run_model_pack_profile"] == "review"
    assert task["background_run_model_plan_summary"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    queue_path = Path(updated["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    rows = background_runs.load_background_runs_state(queue_path).get("runs") or []
    launched = [row for row in rows if str(row.get("ticket_id", "")).startswith("BGT-REQ-1-")]
    assert len(launched) == 1
    assert launched[0]["runner_target"] == "github_runner"
    assert launched[0]["status"] == "running"
    assert launched[0]["runtime_handle"] == runtime_handle
    assert launched[0]["launch_spec"]["model_pack_profile"] == "review"
    assert launched[0]["launch_spec"]["model_plan_summary"] == (
        "pack=review | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    handoff_path = Path(updated["projects"]["alpha"]["team_dir"]) / runtime_handle
    assert handoff_path.exists()
    handoff_payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff_payload["runner_target"] == "github_runner"
    assert handoff_payload["ticket_id"] == launched[0]["ticket_id"]
    assert handoff_payload["launch_spec"]["externalizable"] is True
    assert handoff_payload["launch_spec"]["mode"] == "github_action_json"
    assert any("/retry REQ-1" in token for token in (handoff_payload["launch_spec"].get("command_argv") or []))


def test_control_dashboard_post_retry_route_blocks_when_run_lock_is_test_only(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["run_lock_mode"] = "test_only"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["error"] == "run_lock_test_only"
    assert payload["next_step"] == "/orch run-lock O2 open"
    assert "only small test launches are allowed" in payload["remediation"]


def test_control_dashboard_post_retry_route_blocks_when_background_slots_are_exhausted(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "local_tmux"
    state["projects"]["alpha"]["background_runner_slot_limit"] = 1
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    queue_path = Path(state["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    background_runs.upsert_background_run_ticket(
        queue_path,
        build_background_run_ticket(
            ticket_id="BGT-BUSY-001",
            request_id="REQ-BUSY-001",
            project_key="alpha",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="dashboard_retry",
            created_at="2026-04-07T14:00:00+09:00",
            created_by="dashboard:control",
            source_surface="dashboard_retry",
            status="running",
        ),
        now_iso=lambda: "2026-04-07T14:00:01+09:00",
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["error"] == "background_runner_slots_exhausted"
    assert payload["next_step"] == "/orch bg-slots O2 local_tmux 2"
    assert "slots are saturated for local_tmux (1/1)" in payload["remediation"]


def test_control_dashboard_post_retry_route_ignores_busy_other_runner_slots(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "local_tmux"
    state["projects"]["alpha"]["background_runner_slot_limit"] = 1
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    queue_path = Path(state["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    background_runs.upsert_background_run_ticket(
        queue_path,
        build_background_run_ticket(
            ticket_id="BGT-EXT-001",
            request_id="REQ-EXT-001",
            project_key="alpha",
            execution_brief_status="executable",
            runner_target="github_runner",
            launch_mode="dashboard_retry",
            created_at="2026-04-07T14:00:00+09:00",
            created_by="dashboard:control",
            source_surface="dashboard_retry",
            status="running",
        ),
        now_iso=lambda: "2026-04-07T14:00:01+09:00",
    )

    monkeypatch.setattr(retry_exec, "_now_iso", lambda: "2026-04-07T14:10:00+09:00")

    class _GatewayMain:
        @staticmethod
        def save_manager_state(path, state):
            Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_load_gateway_main_module", lambda: _GatewayMain)

    def _fake_launch(*, queue_path, ticket_id, runner_target="", now_iso, claimed_by="", source_surface="", launch_mode="offdesk_manual"):
        claimed = background_runs.claim_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            runner_target=runner_target or "local_tmux",
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        assert claimed["status"] == "dispatching"
        return background_runs.advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="running",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            runtime_handle="aoe_bg_retry_req_1",
            runtime_summary="tmux_session=aoe_bg_retry_req_1",
            evidence_bundle="status=running | outcome=tmux_session_started | session=aoe_bg_retry_req_1",
        )

    monkeypatch.setattr(retry_exec, "launch_background_ticket_via_adapter", lambda **kwargs: _fake_launch(**kwargs))

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["background_run"]["runner_target"] == "local_tmux"


def test_control_dashboard_post_replan_route_uses_local_tmux_background_when_preferred(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "local_tmux"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_now_iso", lambda: "2026-04-06T12:30:00+09:00")

    class _GatewayMain:
        @staticmethod
        def save_manager_state(path, state):
            Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_load_gateway_main_module", lambda: _GatewayMain)

    def _fake_launch(*, queue_path, ticket_id, runner_target="", now_iso, claimed_by="", source_surface="", launch_mode="offdesk_manual"):
        claimed = background_runs.claim_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            runner_target=runner_target or "local_tmux",
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        assert claimed["status"] == "dispatching"
        assert launch_mode == "dashboard_replan"
        assert source_surface == "dashboard_replan"
        return background_runs.advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="running",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            runtime_handle="aoe_bg_replan_req_1",
            runtime_summary="tmux_session=aoe_bg_replan_req_1",
            evidence_bundle="status=running | outcome=tmux_session_started | session=aoe_bg_replan_req_1",
        )

    monkeypatch.setattr(retry_exec, "launch_background_ticket_via_adapter", lambda **kwargs: _fake_launch(**kwargs))

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/replan T-001 lane L1"
    assert payload["next_step"] == "/orch status O2"
    assert payload["background_run"]["runner_target"] == "local_tmux"
    assert payload["background_run"]["status"] == "running"
    assert payload["background_run"]["runtime_handle"] == "aoe_bg_replan_req_1"
    assert payload["transition"]["run_control_mode"] == "replan"
    assert payload["transition"]["execution_lane_ids"] == ["L1"]
    assert payload["task"]["request_id"] == "REQ-1"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert task["background_run_runner_target"] == "local_tmux"
    assert task["background_run_status"] == "running"
    assert task["background_run_runtime_handle"] == "aoe_bg_replan_req_1"
    queue_path = Path(updated["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    rows = background_runs.load_background_runs_state(queue_path).get("runs") or []
    launched = [row for row in rows if str(row.get("ticket_id", "")).startswith("BGT-REQ-1-")]
    assert len(launched) == 1
    assert launched[0]["runner_target"] == "local_tmux"
    assert launched[0]["status"] == "running"
    assert launched[0]["launch_mode"] == "dashboard_replan"


def test_control_dashboard_post_retry_route_blocked_includes_context_specific_remediation(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    def _fake_execute_retry_run_transition(transition, *, config, manager_state, paths, source_command, payload):
        return dashboard_app._json(
            {
                "ok": False,
                "implemented": True,
                "executed": True,
                "status": "blocked",
                "method": "POST",
                "path": "/control/actions/task/retry",
                "mode": "phase2",
                "source_command": source_command,
                "payload": payload,
                "messages": [{"context": "planning-gate", "text": "plan gate blocked"}],
                "next_step": "/offdesk review",
                "remediation": "inspect planning critic issues and approval blockers in /task and /offdesk review before retrying again",
            },
            status=409,
        )

    monkeypatch.setattr(dashboard_app, "_execute_retry_run_transition", _fake_execute_retry_run_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/offdesk review"
    assert "approval blockers" in payload["remediation"]


def test_control_dashboard_post_retry_route_terminal_block_prefers_judge_next_step(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:00:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"continue\",\"confidence\":\"medium\",\"reasoning\":\"brief executable\",\"next_step\":\"/retry T-001\",\"caution\":\"review lane remains\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/orch judge O2"
    assert "/orch judge" in payload["remediation"]
    assert "latest judge: Offdesk Judge" in payload["remediation"]
    assert "endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed" in payload["remediation"]
    assert payload["latest_judge"]["headline"] == "Offdesk Judge"
    assert payload["latest_judge"]["next_step"] == "/offdesk review O2"
    assert payload["latest_judge_decision"]["verdict"] == "continue"
    assert payload["latest_judge_decision"]["recommended_action"] == "retry"
    assert payload["latest_judge_decision_bridge"]["applied"] is False
    assert payload["latest_judge_decision_bridge"]["recommended_action"] == "retry"
    assert payload["replan_auto_decision"] == {}
    assert payload["replan_auto_routing_policy"] == {}


def test_control_dashboard_post_replan_route_terminal_block_reuses_job_contract_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["phase1_mode"] = "ensemble"
    task["phase1_rounds"] = 1
    task["phase1_current_round"] = 1
    task["phase1_current_total_rounds"] = 1
    task["phase1_current_phase"] = "verification"
    task["phase1_current_provider"] = "codex"
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_providers"] = ["codex", "claude"]
    task["plan"] = {
        "summary": "baseline ready",
        "subtasks": [{"id": "S1", "owner_role": "Codex-Analyst", "title": "Re-check evidence links"}],
    }
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": []}
    task["plan_review_count"] = 1
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    task["job_contract_status"] = "blocked"
    task["job_contract_summary"] = "status=blocked | acceptance gap still needs operator review"
    task["debug_packet_state"] = "blocked"
    task["debug_packet_summary"] = "state=blocked | symptom=contract_gap | next=/task T-001"
    task["debug_packet_next_step"] = "/task T-001"
    task["phase_checkpoint_status"] = "blocked"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=blocked | current=verify | verify=blocked|note=contract_gap"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=blocked|note=contract_gap",
    ]
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:05:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"continue\",\"confidence\":\"medium\",\"reasoning\":\"brief executable\",\"next_step\":\"/retry T-001\",\"caution\":\"review lane remains\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/task T-001"
    assert payload["source_command"] == "/replan T-001 lane L1"
    assert "/orch judge" in payload["remediation"]
    assert "latest judge: Offdesk Judge" in payload["remediation"]
    assert "judge decision reuse: action=retry next=/retry T-001" in payload["remediation"]
    assert "planning primitives reused: source=job_contract next=/task T-001" in payload["remediation"]
    assert "endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed" in payload["remediation"]
    assert payload["latest_judge"]["headline"] == "Offdesk Judge"
    assert payload["latest_judge"]["next_step"] == "/offdesk review O2"
    assert payload["latest_judge_decision"]["verdict"] == "continue"
    assert payload["latest_judge_decision"]["recommended_action"] == "retry"
    assert payload["latest_judge_decision_bridge"]["applied"] is True
    assert payload["latest_judge_decision_bridge"]["applied_next_step"] == "/retry T-001"
    assert payload["job_contract"].startswith("status=blocked")
    assert payload["debug_packet"].startswith("state=blocked")
    assert payload["phase_checkpoint"].startswith("status=blocked")
    assert payload["planning_handoff"]["approved_plan"]["status"] == "approved"
    assert payload["planning_handoff"]["job_contract"]["status"] == "blocked"
    assert payload["planning_handoff"]["debug_packet"]["state"] == "blocked"
    assert payload["planning_handoff"]["phase_checkpoint"]["status"] == "blocked"
    assert payload["replan_auto_decision"]["current_action"] == "replan"
    assert payload["replan_auto_decision"]["suggested_action"] == "task_review"
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/task T-001"
    assert payload["replan_auto_decision"]["decision_mode"] == "planning_primitive_reuse"
    assert payload["replan_auto_decision"]["bridge_applied"] is True
    assert payload["replan_auto_decision"]["can_auto_apply"] is False
    assert payload["replan_auto_decision"]["planning_feedback_source"] == "job_contract"
    assert payload["replan_auto_decision"]["planning_feedback_applied"] is True
    assert payload["replan_auto_routing_policy"]["status"] == "contract_review_ready"
    assert payload["replan_auto_routing_policy"]["current_action"] == "replan"
    assert payload["replan_auto_routing_policy"]["suggested_action"] == "task_review"
    assert payload["replan_auto_routing_policy"]["suggested_next_step"] == "/task T-001"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert payload["replan_auto_routing_policy"]["can_auto_apply"] is False
    assert (
        action_audit.load_latest_judge_decision_bridge_summary_for_runtime(team_dir, project_alias="O2")
        == "mode=promoted_next_step | action=retry | verdict=continue | confidence=medium | next=/retry T-001 | auto=yes"
    )
    assert (
        action_audit.load_latest_replan_auto_decision_summary_for_runtime(team_dir, project_alias="O2")
        == "from=replan | to=task_review | confidence=medium | next=/task T-001 | mode=planning_primitive_reuse | reuse=job_contract"
    )
    assert (
        action_audit.load_latest_replan_auto_routing_policy_summary_for_runtime(team_dir, project_alias="O2")
        == "status=contract_review_ready | from=replan | to=task_review | confidence=medium | next=/task T-001 | mode=planning_primitive_reuse | gate=job_contract"
    )
    latest_policy = action_audit.load_latest_replan_auto_routing_policy_for_runtime(team_dir, project_alias="O2")
    assert latest_policy["planning_handoff"]["debug_packet"]["state"] == "blocked"
    assert latest_policy["planning_handoff"]["debug_packet"]["failed_attempt"] != "-"
    assert latest_policy["planning_handoff_summary"].startswith("contract=status=blocked")
    latest_blocked_row = action_audit.load_latest_action_audit_for_runtime_kind(team_dir, project_alias="O2", outcome_kind="replan")
    assert latest_blocked_row["headline_summary"].startswith("Replan | blocked | reason=")
    assert "| debug=blocked | symptom=" in latest_blocked_row["headline_summary"]
    assert "debug=blocked" in latest_blocked_row["outcome_detail"]
    assert "symptom=" in latest_blocked_row["outcome_detail"]
    assert "attempt=" in latest_blocked_row["outcome_detail"]
    assert "next=" in latest_blocked_row["outcome_detail"]


def test_control_dashboard_post_replan_route_reuses_phase_checkpoint_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    task["job_contract_goal"] = "Summarize findings with supporting evidence"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_acceptance_checks"] = ["attach evidence to the summary update"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "limit mutations to declared artifact targets before retry"
    task["debug_packet_state"] = "active"
    task["debug_packet_summary"] = "state=active | symptom=background_run_inflight | evidence=1 | next=/task T-001"
    task["debug_packet_next_step"] = "/task T-001"
    task["phase_checkpoint_status"] = "blocked"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=blocked | current=verify | verify=blocked|note=verification_gap"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=contract_ready",
        "implement=done|note=execution_complete",
        "verify=blocked|note=verification_gap",
    ]
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:05:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"continue\",\"confidence\":\"medium\",\"reasoning\":\"brief executable\",\"next_step\":\"/retry T-001\",\"caution\":\"review lane remains\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["next_step"] == "/task T-001"
    assert payload["planning_handoff"]["phase_checkpoint"]["status"] == "blocked"
    assert payload["planning_handoff"]["phase_checkpoint"]["current_phase"] == "verify"
    assert payload["replan_auto_decision"]["planning_feedback_source"] == "phase_checkpoint"
    assert payload["replan_auto_decision"]["planning_feedback_applied"] is True
    assert payload["replan_auto_decision"]["decision_mode"] == "planning_primitive_reuse"
    assert payload["replan_auto_routing_policy"]["status"] == "phase_review_ready"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert payload["replan_auto_routing_policy"]["can_auto_apply"] is False
    assert "planning primitives reused: source=phase_checkpoint next=/task T-001" in payload["remediation"]


def test_dashboard_surfaces_phase_review_ready_task_review_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["phase_checkpoint_status"] = "blocked"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=blocked | current=verify | verify=blocked|note=verification_gap"
    task["phase_checkpoint_rows"] = ["verify=blocked|note=verification_gap"]
    gw.save_manager_state(manager_state_file, state)

    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/task T-001",
        remediation="planning primitives reused: source=phase_checkpoint next=/task T-001",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:07:00+09:00",
        extra={
            "replan_auto_decision": {
                "source": "latest_offdesk_judge",
                "current_action": "replan",
                "suggested_action": "task_review",
                "suggested_next_step": "/task T-001",
                "decision_mode": "planning_primitive_reuse",
                "bridge_applied": True,
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "confidence": "medium",
                "planning_feedback_source": "phase_checkpoint",
                "planning_feedback_state": "blocked",
                "planning_feedback_summary": "status=blocked | current=verify | verify=blocked|note=verification_gap",
                "planning_feedback_next_step": "/task T-001",
                "planning_feedback_suggested_action": "task_review",
                "planning_feedback_applied": True,
                "phase_checkpoint_status": "blocked",
                "phase_checkpoint_current_phase": "verify",
                "phase_checkpoint_summary": "status=blocked | current=verify | verify=blocked|note=verification_gap",
            },
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "phase_review_ready",
                "current_action": "replan",
                "suggested_action": "task_review",
                "suggested_next_step": "/task T-001",
                "decision_mode": "planning_primitive_reuse",
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "requires_operator_confirmation": False,
                "confidence": "medium",
                "planning_feedback_source": "phase_checkpoint",
                "planning_feedback_state": "blocked",
                "planning_feedback_summary": "status=blocked | current=verify | verify=blocked|note=verification_gap",
                "planning_feedback_applied": True,
                "phase_checkpoint_status": "blocked",
                "phase_checkpoint_current_phase": "verify",
                "phase_checkpoint_summary": "status=blocked | current=verify | verify=blocked|note=verification_gap",
            },
        },
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")

    expected_payload = '{"task_ref":"T-001","review_kind":"phase_review_ready"}'
    assert any(
        btn.label == "Review Phase Checkpoint"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Review Phase Checkpoint"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert task_detail is not None
    assert any(
        btn.label == "Review Phase Checkpoint"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_payload
        for btn in task_detail.safe_action_buttons
    )
    assert runtime_detail.latest_manual_step_summary == "task_review=/task T-001 | gate=phase_checkpoint | waiting_for_operator"

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/task-review",
        body=json.dumps({"task_ref": "T-001", "review_kind": "phase_review_ready"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "preview"
    assert payload["outcome"]["reason_code"] == "phase_review_ready"
    assert payload["planning_handoff"]["phase_checkpoint"]["status"] == "blocked"
    assert payload["planning_handoff"]["phase_checkpoint"]["current_phase"] != "-"


def test_control_dashboard_post_replan_route_surfaces_manual_ready_followup_policy(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:06:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"hold\",\"confidence\":\"medium\",\"reasoning\":\"needs operator handoff\",\"next_step\":\"/followup T-001\",\"caution\":\"manual wording still required\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/followup T-001"
    assert payload["latest_judge_decision"]["recommended_action"] == "followup"
    assert payload["replan_auto_decision"]["suggested_action"] == "followup"
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/followup T-001"
    assert payload["replan_auto_decision"]["can_auto_apply"] is False
    assert payload["replan_auto_routing_policy"]["status"] == "manual_ready"
    assert payload["replan_auto_routing_policy"]["suggested_action"] == "followup"
    assert payload["replan_auto_routing_policy"]["suggested_next_step"] == "/followup T-001"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is True
    assert payload["replan_auto_routing_policy"]["can_auto_apply"] is False


def test_control_dashboard_post_replan_route_surfaces_manual_ready_followup_execute_policy(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:06:00+09:00",
        extra={
            "response_text": (
                "{\"verdict\":\"hold\",\"confidence\":\"medium\",\"reasoning\":\"execution slice is safe\","
                "\"recommended_action\":\"followup_execute\",\"next_step\":\"/followup-exec T-001 lane L2\","
                "\"caution\":\"keep review lane manual\"}"
            ),
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/followup-exec T-001 lane L2"
    assert payload["latest_judge_decision"]["recommended_action"] == "followup_execute"
    assert payload["replan_auto_decision"]["suggested_action"] == "followup_execute"
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/followup-exec T-001 lane L2"
    assert payload["replan_auto_decision"]["can_auto_apply"] is False
    assert payload["replan_auto_routing_policy"]["status"] == "manual_ready"
    assert payload["replan_auto_routing_policy"]["suggested_action"] == "followup_execute"
    assert payload["replan_auto_routing_policy"]["suggested_next_step"] == "/followup-exec T-001 lane L2"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is True
    assert payload["replan_auto_routing_policy"]["can_auto_apply"] is False


def test_control_dashboard_post_replan_route_reuses_previewed_manual_followup_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_manual_step_execution_status"] = "preview"
    task["background_run_manual_step_execution_kind"] = "manual_followup"
    task["background_run_manual_step_execution_command"] = "/followup T-001"
    task["background_run_manual_step_execution_next_step"] = "/task T-001"
    task["background_run_manual_step_execution_summary"] = (
        "manual_followup=/followup T-001 | state=preview | next=/task T-001 | at=2026-04-10T10:12:00+09:00"
    )
    task["background_run_manual_step_execution_at"] = "2026-04-10T10:12:00+09:00"
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"hold\",\"confidence\":\"medium\",\"reasoning\":\"needs operator handoff\",\"next_step\":\"/followup T-001\",\"caution\":\"manual wording still required\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["next_step"] == "/task T-001"
    assert payload["replan_auto_decision"]["manual_feedback_state"] == "preview"
    assert payload["replan_auto_decision"]["manual_feedback_applied"] is True
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/task T-001"
    assert payload["replan_auto_routing_policy"]["status"] == "manual_progressed"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert "manual step reused" in payload["remediation"]
    _snapshot, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    assert runtime_detail.latest_manual_step_summary == "manual_followup=/task T-001 | state=preview | reused"


def test_control_dashboard_post_replan_route_reuses_executed_manual_followup_execute_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_manual_step_execution_status"] = "executed"
    task["background_run_manual_step_execution_kind"] = "manual_execute"
    task["background_run_manual_step_execution_command"] = "/followup-exec T-001 lane L2"
    task["background_run_manual_step_execution_next_step"] = "/task T-001"
    task["background_run_manual_step_execution_summary"] = (
        "manual_execute=/followup-exec T-001 lane L2 | state=executed | next=/task T-001 | at=2026-04-10T10:13:00+09:00"
    )
    task["background_run_manual_step_execution_at"] = "2026-04-10T10:13:00+09:00"
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "response_text": (
                "{\"verdict\":\"hold\",\"confidence\":\"medium\",\"reasoning\":\"execution slice is safe\","
                "\"recommended_action\":\"followup_execute\",\"next_step\":\"/followup-exec T-001 lane L2\","
                "\"caution\":\"keep review lane manual\"}"
            ),
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["next_step"] == "/task T-001"
    assert payload["replan_auto_decision"]["manual_feedback_state"] == "executed"
    assert payload["replan_auto_decision"]["manual_feedback_applied"] is True
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/task T-001"
    assert payload["replan_auto_routing_policy"]["status"] == "manual_progressed"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert "manual step reused" in payload["remediation"]
    _snapshot, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    assert runtime_detail.latest_manual_step_summary == "manual_execute=/task T-001 | state=executed | reused"


def test_control_dashboard_post_replan_route_reuses_canonical_writeback_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_canonical_writeback_status"] = "executed"
    task["background_run_canonical_writeback_next_step"] = "/sync preview O2 24h"
    task["background_run_canonical_writeback_summary"] = (
        "Syncback Apply | executed | state=executed | next=/sync preview O2 24h | "
        "at=2026-04-10T10:14:00+09:00 | path=TODO.md lines=8 done=0 reopen=0 append=1 blocked=0"
    )
    task["background_run_canonical_writeback_at"] = "2026-04-10T10:14:00+09:00"
    task["background_run_canonical_mutation_status"] = "executed"
    task["background_run_canonical_mutation_kind"] = "todo_syncback"
    task["background_run_canonical_mutation_profile"] = "append_only"
    task["background_run_canonical_mutation_path"] = "TODO.md"
    task["background_run_canonical_mutation_summary"] = (
        "todo_syncback:append_only | path=TODO.md | lines=8 | done=0 reopen=0 append=1 blocked=0 | "
        "state=executed | at=2026-04-10T10:14:00+09:00"
    )
    task["background_run_canonical_mutation_at"] = "2026-04-10T10:14:00+09:00"
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "response_text": (
                "{\"verdict\":\"hold\",\"confidence\":\"medium\",\"reasoning\":\"execution slice is safe\","
                "\"recommended_action\":\"followup_execute\",\"next_step\":\"/followup-exec T-001 lane L2\","
                "\"caution\":\"keep review lane manual\"}"
            ),
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["next_step"] == "/sync preview O2 24h"
    assert payload["replan_auto_decision"]["canonical_feedback_applied"] is True
    assert payload["replan_auto_decision"]["canonical_feedback_kind"] == "todo_syncback"
    assert payload["replan_auto_decision"]["canonical_feedback_profile"] == "append_only"
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/sync preview O2 24h"
    assert payload["replan_auto_decision"]["decision_mode"] == "canonical_writeback_reuse"
    assert payload["replan_auto_routing_policy"]["status"] == "mutation_progressed"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert "canonical mutation reused" in payload["remediation"]
    _snapshot, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    assert runtime_detail.latest_replan_auto_operator_summary == (
        "mutation=/sync preview O2 24h | kind=todo_syncback:append_only | reused | reuse=canonical_writeback"
    )


def test_control_dashboard_post_replan_route_reuses_analysis_record_set_feedback(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "decision_snapshot": {
                "verdict": "hold",
                "confidence": "medium",
                "reasoning": "analysis evidence still missing",
                "next_step": "/orch judge O2",
                "caution": "close evidence gaps before retry",
                "analysis_record_set": "analysis_record_set | finding=1 | evidence=1 | gap=1",
                "analysis_record_set_records": [
                    {"kind": "finding", "label": "summary", "state": "stable", "note": "action"},
                    {"kind": "evidence", "label": "missing", "state": "missing", "note": "attach_evidence"},
                    {"kind": "gap", "label": "evidence_missing", "state": "open", "note": "attach_evidence"},
                ],
            },
        },
    )

    def _fake_resolve_retry_replan_transition(*, send, **_kwargs):
        send("plan gate blocked", context="planning-gate")
        return {"terminal": True}

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["next_step"] == "/task T-001"
    assert payload["latest_judge_decision"]["analysis_record_set"] == "analysis_record_set | finding=1 | evidence=1 | gap=1"
    assert payload["replan_auto_decision"]["suggested_action"] == "task_review"
    assert payload["replan_auto_decision"]["suggested_next_step"] == "/task T-001"
    assert payload["replan_auto_decision"]["decision_mode"] == "analysis_record_set_reuse"
    assert payload["replan_auto_decision"]["analysis_feedback_open_kinds"] == "evidence,gap"
    assert payload["replan_auto_decision"]["analysis_feedback_applied"] is True
    assert payload["replan_auto_routing_policy"]["status"] == "analysis_review_ready"
    assert payload["replan_auto_routing_policy"]["requires_operator_confirmation"] is False
    assert "analysis records reused" in payload["remediation"]


def test_control_dashboard_post_replan_route_auto_routes_to_retry_when_confirmed(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    task["job_contract_goal"] = "Summarize findings with supporting evidence"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_acceptance_checks"] = ["attach evidence to the summary update"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "limit mutations to declared artifact targets before retry"
    task["debug_packet_state"] = "active"
    task["debug_packet_summary"] = "state=active | symptom=background_run_inflight | evidence=1 | next=/task T-001"
    task["debug_packet_next_step"] = "/task T-001"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done|note=contract_ready | implement=done|note=execution_complete | verify=active|note=judge_retry_ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=contract_ready",
        "implement=done|note=execution_complete",
        "verify=active|note=judge_retry_ready",
    ]
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:05:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"continue\",\"confidence\":\"medium\",\"reasoning\":\"brief executable\",\"next_step\":\"/retry T-001\",\"caution\":\"review lane remains\"}",
        },
    )

    def _fake_resolve_retry_replan_transition(*, cmd, send, **_kwargs):
        if cmd == "orch-replan":
            send("plan gate blocked", context="planning-gate")
            return {"terminal": True}
        if cmd == "orch-retry":
            return {
                "cmd": "run",
                "rest": "",
                "orch_target": "alpha",
                "run_prompt": "retry it",
                "run_force_mode": "dispatch",
                "run_control_mode": "retry",
                "run_source_request_id": "REQ-1",
                "run_source_task": {"request_id": "REQ-1"},
                "run_selected_execution_lane_ids": ["L1"],
                "run_selected_review_lane_ids": [],
            }
        raise AssertionError(cmd)

    def _fake_execute_retry_run_transition(transition, *, config, manager_state, paths, source_command, payload):
        return dashboard_app._json(
            {
                "ok": True,
                "implemented": True,
                "executed": True,
                "status": "executed",
                "method": "POST",
                "path": "/control/actions/task/retry",
                "mode": "phase2",
                "source_command": source_command,
                "payload": payload,
                "next_step": "/task T-001 | analysis-check",
                "remediation": "review the updated task detail and lane state before repeating another retry",
            },
            status=200,
        )

    monkeypatch.setattr(retry_exec.retry_handlers, "resolve_retry_replan_transition", _fake_resolve_retry_replan_transition)
    monkeypatch.setattr(dashboard_app, "_execute_retry_run_transition", _fake_execute_retry_run_transition)

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/replan",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L1"], "auto_route_apply": True}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["auto_route_applied"] is True
    assert payload["auto_routed_from"] == "/replan T-001 lane L1"
    assert payload["auto_route_policy_source"] == "replan_auto_routing_policy"
    assert payload["source_command"] == "/retry T-001 lane L1"
    assert payload["payload"]["task_ref"] == "T-001"
    assert payload["payload"]["lane_ids"] == ["L1"]
    assert payload["payload"]["auto_route_source"] == "replan_auto_routing_policy"
    assert payload["replan_auto_routing_policy"]["status"] == "ready"
    row = action_audit.load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias="O2",
        outcome_kind="replan_auto_route",
    )
    assert row["headline"] == "Replan Auto Route | applied"
    assert row["next_step"] == "/retry T-001 lane L1"
    assert row["outcome_detail"] == "retry_command=/retry T-001 lane L1"


def test_control_dashboard_post_followup_and_sync_preview_routes_return_200_preview(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    followup_status, _followup_headers, followup_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["R1"]}).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        config=config,
    )
    sync_status, _sync_headers, sync_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/sync-preview",
        body=json.dumps({"project_ref": "O2", "window": "48h"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    followup_payload = json.loads(followup_body.decode("utf-8"))
    sync_payload = json.loads(sync_body.decode("utf-8"))

    assert followup_status == 200
    assert followup_payload["ok"] is True
    assert followup_payload["implemented"] is True
    assert followup_payload["mode"] == "safe"
    assert followup_payload["source_command"] == "/followup T-001 lane R1"
    assert followup_payload["payload"] == {"task_ref": "T-001", "lane_ids": ["R1"]}
    assert followup_payload["next_step"] == "/task T-001"
    assert "inspect the follow-up reason" in followup_payload["remediation"]
    assert followup_payload["preview"]["kind"] == "task_followup"
    assert followup_payload["preview"]["project_alias"] == "O2"
    assert followup_payload["preview"]["request_id"] == "REQ-1"
    assert followup_payload["preview"]["detail_path"] == "/control/tasks/by-request/REQ-1"
    assert followup_payload["preview"]["runtime_path"] == "/control/runtimes/O2"
    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_manual_step_execution_status"] == "preview"
    assert updated_task["background_run_manual_step_execution_summary"].startswith(
        "manual_followup=/followup T-001 lane R1 | state=preview | next=/task T-001 |"
    )

    assert sync_status == 200
    assert sync_payload["ok"] is True
    assert sync_payload["implemented"] is True
    assert sync_payload["mode"] == "safe"
    assert sync_payload["source_command"] == "/sync preview O2 48h"
    assert sync_payload["payload"] == {"project_ref": "O2", "window": "48h"}
    assert sync_payload["next_step"] == "/offdesk review O2"
    assert "inspect sync drift" in sync_payload["remediation"]
    assert sync_payload["preview"]["kind"] == "runtime_sync_preview"


def test_control_dashboard_post_runtime_syncback_preview_and_apply_routes_return_expected_payload(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["todo_proposals"] = [
        {
            "id": "PROP-001",
            "summary": "apply worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "kind": "handoff",
            "status": "accepted",
            "source_request_id": "REQ-1",
            "created_by": "worker",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:06:00+09:00",
            "accepted_todo_id": "TODO-002",
        }
    ]
    state["projects"]["alpha"]["todos"] = [
        {
            "id": "TODO-002",
            "summary": "apply worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "status": "open",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:06:00+09:00",
        }
    ]
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_worker_records_summary"] = (
        "package_records | artifact_record=reports/summary.md | verification_record=1 | apply_record=ready | syncback_record=ready"
    )
    task["background_run_worker_records"] = [
        "artifact_record=reports/summary.md",
        "verification_record=1",
        "apply_record=ready",
        "syncback_record=ready",
    ]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_todo_id"] = "TODO-002"
    task["background_run_worker_apply_accept_proposal_id"] = "PROP-001"
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/syncback-preview",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    apply_status, _apply_headers, apply_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/syncback-apply",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    preview_payload = json.loads(preview_body.decode("utf-8"))
    apply_payload = json.loads(apply_body.decode("utf-8"))

    assert preview_status == 200
    assert preview_payload["ok"] is True
    assert preview_payload["status"] == "preview"
    assert preview_payload["source_command"] == "/todo O2 syncback preview"
    assert preview_payload["payload"] == {"project_ref": "O2"}
    assert preview_payload["next_step"] == "/todo O2 syncback apply"
    assert preview_payload["preview"]["kind"] == "runtime_syncback_preview"
    assert preview_payload["preview"]["append_count"] >= 1
    assert "canonical TODO diff" in preview_payload["remediation"]

    assert apply_status == 200
    assert apply_payload["ok"] is True
    assert apply_payload["status"] == "executed"
    assert apply_payload["source_command"] == "/todo O2 syncback apply"
    assert apply_payload["next_step"] == "/sync preview O2 24h"
    assert apply_payload["outcome"]["kind"] == "runtime_syncback_apply"
    assert apply_payload["result"]["line_count"] >= 1
    assert "completed" == apply_payload["outcome"]["reason_code"]
    assert apply_payload["worker_syncback"].startswith("state=applied | todo=TODO-002 | path=TODO.md |")
    canonical_text = (project_root / "TODO.md").read_text(encoding="utf-8")
    assert "apply worker artifact update for T-001: reports/summary.md" in canonical_text
    assert preview_payload["preview"]["project_alias"] == "O2"
    assert preview_payload["preview"]["target_path"].endswith("TODO.md")
    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_worker_syncback_status"] == "applied"
    assert updated_task["background_run_worker_syncback_summary"].startswith(
        "state=applied | todo=TODO-002 | path=TODO.md |"
    )
    assert updated_task["background_run_canonical_writeback_status"] == "executed"
    assert updated_task["background_run_canonical_writeback_next_step"] == "/sync preview O2 24h"
    assert updated_task["background_run_canonical_writeback_summary"].startswith(
        "Syncback Apply | executed | state=executed | next=/sync preview O2 24h |"
    )
    assert updated_task["background_run_canonical_mutation_status"] == "executed"
    assert updated_task["background_run_canonical_mutation_kind"] == "todo_syncback"
    assert updated_task["background_run_canonical_mutation_profile"] == "append_only"
    assert updated_task["background_run_canonical_mutation_path"] == "TODO.md"
    assert updated_task["background_run_canonical_mutation_summary"].startswith(
        "todo_syncback:append_only | path=TODO.md | lines="
    )
    _snapshot2, runtime_details, _state2 = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    assert runtime_detail.latest_canonical_mutation_summary.startswith(
        "todo_syncback:append_only | path=TODO.md | lines="
    )
    assert runtime_detail.latest_canonical_writeback_summary.startswith(
        "Syncback Apply | executed | state=executed | next=/sync preview O2 24h |"
    )


def test_dashboard_hides_package_syncback_buttons_until_record_ready(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_worker_records_summary"] = (
        "package_records | artifact_record=dist/release_bundle.zip | verification_record=1 | apply_record=ready | syncback_record=pending"
    )
    task["background_run_worker_records"] = [
        "artifact_record=dist/release_bundle.zip",
        "verification_record=1",
        "apply_record=ready",
        "syncback_record=pending",
    ]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_summary"] = (
        "state=applied | todo=TODO-002 | proposal=PROP-001 | targets=dist/release_bundle.zip | at=2026-04-10T10:06:00+09:00"
    )
    task["background_run_worker_apply_accept_proposal_id"] = "PROP-001"
    task["background_run_worker_apply_accept_todo_id"] = "TODO-002"
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )

    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    syncback_labels = {"Preview Accepted Syncback", "Apply Accepted Syncback"}

    assert task_detail is not None
    assert not any(btn.label in syncback_labels for btn in runtime_card.runtime_safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_card.runtime_phase2_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_detail.active_task_safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_detail.active_task_phase2_action_buttons)
    assert not any(btn.label in syncback_labels for btn in task_detail.safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in task_detail.phase2_action_buttons)


def test_control_dashboard_syncback_routes_block_when_package_record_pending(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_worker_records_summary"] = (
        "package_records | artifact_record=dist/release_bundle.zip | verification_record=1 | apply_record=ready | syncback_record=pending"
    )
    task["background_run_worker_records"] = [
        "artifact_record=dist/release_bundle.zip",
        "verification_record=1",
        "apply_record=ready",
        "syncback_record=pending",
    ]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_todo_id"] = "TODO-002"
    task["background_run_worker_apply_accept_proposal_id"] = "PROP-001"
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/syncback-preview",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    apply_status, _apply_headers, apply_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/syncback-apply",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    preview_payload = json.loads(preview_body.decode("utf-8"))
    apply_payload = json.loads(apply_body.decode("utf-8"))

    assert preview_status == 409
    assert preview_payload["status"] == "blocked"
    assert preview_payload["outcome"]["reason_code"] == "package_syncback_pending"
    assert preview_payload["next_step"] == "/task T-001"
    assert preview_payload["remediation"] == "prepare syncback readiness before accepted syncback"
    assert "syncback_record=pending" in preview_payload["worker_records"]
    assert "package_syncback_blocker | reason=package_syncback_pending" in preview_payload["worker_blocker"]
    assert preview_payload["worker_recommended_action"] == "package_syncback_review"

    assert apply_status == 409
    assert apply_payload["status"] == "blocked"
    assert apply_payload["outcome"]["reason_code"] == "package_syncback_pending"
    assert apply_payload["next_step"] == "/task T-001"
    assert apply_payload["remediation"] == "prepare syncback readiness before accepted syncback"
    assert "syncback_record=pending" in apply_payload["worker_records"]


def test_control_dashboard_syncback_routes_prefer_package_record_rows_gate(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_worker_records_summary"] = (
        "package_records | artifact_record=dist/release_bundle.zip | verification_record=1 | apply_record=ready | syncback_record=ready"
    )
    task["background_run_worker_records"] = [
        "artifact_record=dist/release_bundle.zip",
        "verification_record=1",
        "apply_record=ready",
        "syncback_record=ready",
    ]
    task["background_run_worker_record_rows_summary"] = (
        "package_record_rows | artifact_row=dist/release_bundle.zip|state=present | "
        "verification_row=1|state=ready | apply_row=ready|state=ready | "
        "syncback_row=pending|state=blocked|note=prepare_syncback"
    )
    task["background_run_worker_record_rows"] = [
        "artifact_row=dist/release_bundle.zip|state=present",
        "verification_row=1|state=ready",
        "apply_row=ready|state=ready",
        "syncback_row=pending|state=blocked|note=prepare_syncback",
    ]
    task["background_run_worker_preflight_rows_summary"] = (
        "package_preflight_rows | verification_ready=ready|state=ready|note=verification | "
        "apply_ready=ready|state=ready|note=apply_gate | syncback_ready=blocked|state=blocked|note=prepare_syncback | "
        "package_ready=syncback_pending|state=blocked|note=prepare_syncback"
    )
    task["background_run_worker_preflight_rows"] = [
        "verification_ready=ready|state=ready|note=verification",
        "apply_ready=ready|state=ready|note=apply_gate",
        "syncback_ready=blocked|state=blocked|note=prepare_syncback",
        "package_ready=syncback_pending|state=blocked|note=prepare_syncback",
    ]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_todo_id"] = "TODO-002"
    task["background_run_worker_apply_accept_proposal_id"] = "PROP-001"
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/syncback-preview",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    preview_payload = json.loads(preview_body.decode("utf-8"))

    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "package_syncback_pending"
    assert preview_payload["next_step"] == "/task T-001"
    assert preview_payload["remediation"] == "prepare syncback readiness before accepted syncback"
    assert "syncback_record=ready" in preview_payload["worker_records"]
    assert "syncback_row=pending|state=blocked" in preview_payload["worker_record_rows"]
    assert "syncback_ready=blocked|state=blocked" in preview_payload["worker_preflight_rows"]
    assert "package_syncback_blocker | reason=package_syncback_pending" in preview_payload["worker_blocker"]
    assert preview_payload["worker_recommended_action"] == "package_syncback_review"


def test_dashboard_and_routes_surface_package_verification_review_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "package"
    task["background_run_task_contract_module_summary"] = "package | artifact_integrity_gate"
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=dist/release_bundle.zip | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["dist/release_bundle.zip"]
    task["background_run_worker_record_rows_summary"] = (
        "package_record_rows | artifact_row=dist/release_bundle.zip|state=present | "
        "verification_row=0|state=open|note=verify_artifacts | apply_row=ready|state=ready | "
        "syncback_row=pending|state=blocked|note=prepare_syncback"
    )
    task["background_run_worker_record_rows"] = [
        "artifact_row=dist/release_bundle.zip|state=present",
        "verification_row=0|state=open|note=verify_artifacts",
        "apply_row=ready|state=ready",
        "syncback_row=pending|state=blocked|note=prepare_syncback",
    ]
    task["background_run_worker_preflight_rows_summary"] = (
        "package_preflight_rows | verification_ready=open|state=blocked|note=verify_artifacts | "
        "apply_ready=ready|state=ready|note=apply_gate | syncback_ready=blocked|state=blocked|note=verify_artifacts | "
        "package_ready=verification_pending|state=blocked|note=verify_artifacts"
    )
    task["background_run_worker_preflight_rows"] = [
        "verification_ready=open|state=blocked|note=verify_artifacts",
        "apply_ready=ready|state=ready|note=apply_gate",
        "syncback_ready=blocked|state=blocked|note=verify_artifacts",
        "package_ready=verification_pending|state=blocked|note=verify_artifacts",
    ]
    gw.save_manager_state(manager_state_file, state)

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")

    assert task_detail is not None
    assert any(
        btn.label == "Review Package Verification"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == '{"task_ref":"T-001","review_kind":"package_verification_review"}'
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Review Package Verification"
        and btn.path == "/control/actions/task/task-review"
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Review Package Verification"
        and btn.path == "/control/actions/task/task-review"
        for btn in task_detail.safe_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "package_verification_open"
    assert preview_payload["next_step"] == "/task T-001"
    assert preview_payload["worker_recommended_action"] == "package_verification_review"
    assert "package_apply_blocker | reason=package_verification_open" in preview_payload["worker_blocker"]


def test_dashboard_surfaces_replan_auto_route_action_buttons(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:05:00+09:00",
        extra={
            "response_text": json.dumps(
                {
                    "verdict": "continue",
                    "confidence": "medium",
                    "reasoning": "brief executable",
                    "next_step": "/retry T-001",
                    "caution": "review lane remains",
                }
            )
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/retry T-001",
        remediation="judge decision reuse: action=retry next=/retry T-001",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "latest_judge_decision_bridge": {
                "source": "latest_offdesk_judge",
                "verdict": "continue",
                "confidence": "medium",
                "recommended_action": "retry",
                "candidate_next_step": "/retry T-001",
                "applied": True,
                "applied_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
            },
            "replan_auto_decision": {
                "source": "latest_offdesk_judge",
                "current_action": "replan",
                "suggested_action": "retry",
                "suggested_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "bridge_applied": True,
                "supports_auto_decision": True,
                "can_auto_apply": True,
                "confidence": "medium",
            },
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "ready",
                "current_action": "replan",
                "suggested_action": "retry",
                "suggested_next_step": "/retry T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
                "can_auto_apply": True,
                "requires_operator_confirmation": True,
                "confidence": "medium",
            },
        },
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    expected_payload = '{"task_ref":"T-001","auto_route_apply":true}'
    assert any(
        btn.label == "Apply Judge Auto-Route"
        and btn.path == "/control/actions/task/replan"
        and btn.payload_json == expected_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Apply Judge Auto-Route"
        and btn.path == "/control/actions/task/replan"
        and btn.payload_json == expected_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )
    assert runtime_detail.latest_replan_auto_route_summary == "-"
    assert runtime_detail.latest_replan_auto_route_status_summary == "ready=/retry T-001 | waiting_for_apply"
    assert task_detail is not None
    assert any(
        btn.label == "Apply Judge Auto-Route"
        and btn.path == "/control/actions/task/replan"
        and btn.payload_json == expected_payload
        for btn in task_detail.phase2_action_buttons
    )
    assert task_detail.latest_replan_auto_route_summary == "-"
    assert task_detail.latest_replan_auto_route_status_summary == "ready=/retry T-001 | waiting_for_apply"


def test_dashboard_gates_dispatch_phase2_actions_when_job_contract_body_is_missing(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["prompt"] = ""
    task["request_contract_type"] = ""
    task["request_contract_preset"] = ""
    task["request_contract_status"] = ""
    task["request_contract_summary"] = ""
    task["request_contract_missing_fields"] = []
    task["request_contract_required_outputs"] = []
    task["request_contract_fields"] = {}
    task["request_contract_artifact_contracts"] = {}
    task["execution_brief_status"] = ""
    task["execution_brief_summary"] = ""
    task["execution_brief_executable_slice"] = []
    task["execution_brief_blocked_slice"] = []
    task["execution_brief_operator_decision"] = ""
    task["job_contract_status"] = ""
    task["job_contract_planning_mode"] = ""
    task["job_contract_summary"] = ""
    task["job_contract_goal"] = ""
    task["job_contract_scope"] = []
    task["job_contract_non_goals"] = []
    task["job_contract_risks"] = []
    task["job_contract_acceptance_checks"] = []
    task["job_contract_artifacts_to_touch"] = []
    task["job_contract_rollback_hint"] = ""
    task["background_run_worker_update_stub_targets"] = []
    task["background_run_worker_result_evidence_refs"] = []
    task["background_run_evidence_artifacts"] = []
    gw.save_manager_state(manager_state_file, state)

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    blocked_paths = {
        "/control/actions/task/retry",
        "/control/actions/task/replan",
        "/control/actions/task/followup-execute",
    }
    assert all(btn.path not in blocked_paths for btn in runtime_card.runtime_phase2_action_buttons)
    assert all(btn.path not in blocked_paths for btn in runtime_detail.active_task_phase2_action_buttons)
    assert task_detail is not None
    assert all(btn.path not in blocked_paths for btn in task_detail.phase2_action_buttons)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    for action_path in ("/control/actions/task/retry", "/control/actions/task/replan"):
        status, headers, body = dashboard_app.build_dashboard_action_response(
            action_path,
            body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
            content_type="application/json",
            config=config,
        )
        payload = json.loads(body.decode("utf-8"))
        assert status == 409
        assert headers["Content-Type"].startswith("application/json")
        assert payload["status"] == "blocked"
        assert payload["outcome"]["reason_code"] == "job_contract_missing"
        assert payload["next_step"] == "/task T-001"
        assert "job_contract" in payload
        assert "debug_packet" in payload
        assert "phase_checkpoint" in payload


def test_dashboard_gates_dispatch_phase2_actions_when_approved_plan_is_blocked(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=reports/summary.md"
    task["job_contract_goal"] = "close the acceptance gap"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "revert summary wording if the evidence check fails"
    task["debug_packet_state"] = "active"
    task["debug_packet_summary"] = "state=active | symptom=background_run_inflight | next=/task T-001"
    task["debug_packet_symptom"] = "background_run_inflight"
    task["debug_packet_next_step"] = "/task T-001"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["phase1_mode"] = "ensemble"
    task["phase1_rounds"] = 3
    task["phase1_current_round"] = 3
    task["phase1_current_total_rounds"] = 3
    task["phase1_current_phase"] = "verification"
    task["phase1_current_provider"] = "codex"
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_providers"] = ["codex", "claude"]
    task["plan"] = {
        "summary": "needs one more critic pass",
        "subtasks": [{"id": "S1", "owner_role": "Codex-Analyst", "title": "Re-check evidence links"}],
    }
    task["plan_critic"] = {"approved": False, "issues": ["missing acceptance"], "recommendations": []}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "blocked"
    task["plan_gate_passed"] = False
    task["plan_gate_reason"] = "missing acceptance"
    gw.save_manager_state(manager_state_file, state)

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    blocked_paths = {
        "/control/actions/task/retry",
        "/control/actions/task/replan",
        "/control/actions/task/followup-execute",
    }
    assert all(btn.path not in blocked_paths for btn in runtime_card.runtime_phase2_action_buttons)
    assert all(btn.path not in blocked_paths for btn in runtime_detail.active_task_phase2_action_buttons)
    assert task_detail is not None
    assert all(btn.path not in blocked_paths for btn in task_detail.phase2_action_buttons)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    for action_path in ("/control/actions/task/retry", "/control/actions/task/replan"):
        status, headers, body = dashboard_app.build_dashboard_action_response(
            action_path,
            body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
            content_type="application/json",
            config=config,
        )
        payload = json.loads(body.decode("utf-8"))
        assert status == 409
        assert headers["Content-Type"].startswith("application/json")
        assert payload["status"] == "blocked"
        assert payload["outcome"]["reason_code"] == "approved_plan_blocked"
        assert payload["next_step"] == "/task T-001"
        assert payload["approved_plan"].startswith("approved_plan=blocked")
        assert payload["outcome"]["detail"].startswith("approved_plan=blocked")


def test_dashboard_surfaces_manual_ready_and_worker_proposal_action_buttons(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | target=reports/summary.md"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    state["projects"]["alpha"]["todo_proposals"] = [
        {
            "id": "PROP-001",
            "summary": "review worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "kind": "handoff",
            "status": "open",
            "source_request_id": "REQ-1",
            "created_by": "worker",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:05:00+09:00",
        }
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:06:00+09:00",
        extra={
            "response_text": json.dumps(
                {
                    "verdict": "hold",
                    "confidence": "medium",
                    "reasoning": "needs operator handoff",
                    "next_step": "/followup T-001",
                    "caution": "manual wording still required",
                }
            )
        },
    )
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/followup T-001",
        remediation="judge decision reuse: action=followup next=/followup T-001",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:07:00+09:00",
        extra={
            "latest_judge_decision_bridge": {
                "source": "latest_offdesk_judge",
                "verdict": "hold",
                "confidence": "medium",
                "recommended_action": "followup",
                "candidate_next_step": "/followup T-001",
                "applied": True,
                "applied_next_step": "/followup T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
            },
            "replan_auto_decision": {
                "source": "latest_offdesk_judge",
                "current_action": "replan",
                "suggested_action": "followup",
                "suggested_next_step": "/followup T-001",
                "decision_mode": "promoted_next_step",
                "bridge_applied": True,
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "confidence": "medium",
            },
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "manual_ready",
                "current_action": "replan",
                "suggested_action": "followup",
                "suggested_next_step": "/followup T-001",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "requires_operator_confirmation": True,
                "confidence": "medium",
            },
        },
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    expected_manual_payload = '{"task_ref":"T-001","lane_ids":[]}'
    expected_preview_payload = '{"task_ref":"T-001"}'
    expected_support_payload = '{"task_ref":"T-001"}'
    expected_proposal_payload = '{"project_ref":"O2","proposal_ref":"PROP-001"}'
    assert any(
        btn.label == "Apply Judge Followup"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_manual_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Preview Worker Update"
        and btn.path == "/control/actions/task/worker-update-preview"
        and btn.payload_json == expected_preview_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Run Support Research"
        and btn.path == "/control/actions/task/subagent-support-run"
        and btn.payload_json == expected_support_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Accept Worker Proposal"
        and btn.path == "/control/actions/runtime/todo-accept"
        and btn.payload_json == expected_proposal_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Apply Judge Followup"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_manual_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Preview Worker Update"
        and btn.path == "/control/actions/task/worker-update-preview"
        and btn.payload_json == expected_preview_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Run Support Research"
        and btn.path == "/control/actions/task/subagent-support-run"
        and btn.payload_json == expected_support_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Accept Worker Proposal"
        and btn.path == "/control/actions/runtime/todo-accept"
        and btn.payload_json == expected_proposal_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )
    assert task_detail is not None
    assert any(
        btn.label == "Apply Judge Followup"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_manual_payload
        for btn in task_detail.safe_action_buttons
    )
    assert any(
        btn.label == "Preview Worker Update"
        and btn.path == "/control/actions/task/worker-update-preview"
        and btn.payload_json == expected_preview_payload
        for btn in task_detail.safe_action_buttons
    )
    assert any(
        btn.label == "Run Support Research"
        and btn.path == "/control/actions/task/subagent-support-run"
        and btn.payload_json == expected_support_payload
        for btn in task_detail.safe_action_buttons
    )
    assert any(
        btn.label == "Accept Worker Proposal"
        and btn.path == "/control/actions/runtime/todo-accept"
        and btn.payload_json == expected_proposal_payload
        for btn in task_detail.phase2_action_buttons
    )

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    recovery_text = dashboard_app.build_dashboard_response("/control/recovery", config)[2].decode("utf-8")
    assert "Apply Judge Followup" in recovery_text
    assert "Preview Worker Update" in recovery_text
    assert "Run Support Research" in recovery_text
    assert "Accept Worker Proposal" in recovery_text


def test_dashboard_surfaces_manual_ready_followup_execute_and_worker_apply_button(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | target=reports/summary.md"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    task["job_contract_goal"] = "Summarize findings with supporting evidence"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_acceptance_checks"] = ["attach evidence to the summary update"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "limit mutations to declared artifact targets before apply"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = (
        "status=active | current=verify | plan=done|note=contract_ready | "
        "implement=done|note=execution_complete | verify=active|note=judge_manual_ready"
    )
    task["phase_checkpoint_rows"] = [
        "plan=done|note=contract_ready",
        "implement=done|note=execution_complete",
        "verify=active|note=judge_manual_ready",
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/followup-exec T-001 lane L2",
        remediation="judge decision reuse: action=followup_execute next=/followup-exec T-001 lane L2",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:07:00+09:00",
        extra={
            "latest_judge_decision_bridge": {
                "source": "latest_offdesk_judge",
                "verdict": "hold",
                "confidence": "medium",
                "recommended_action": "followup_execute",
                "candidate_next_step": "/followup-exec T-001 lane L2",
                "applied": True,
                "applied_next_step": "/followup-exec T-001 lane L2",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
            },
            "replan_auto_decision": {
                "source": "latest_offdesk_judge",
                "current_action": "replan",
                "suggested_action": "followup_execute",
                "suggested_next_step": "/followup-exec T-001 lane L2",
                "decision_mode": "promoted_next_step",
                "bridge_applied": True,
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "confidence": "medium",
            },
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "manual_ready",
                "current_action": "replan",
                "suggested_action": "followup_execute",
                "suggested_next_step": "/followup-exec T-001 lane L2",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "requires_operator_confirmation": True,
                "confidence": "medium",
            },
        },
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    expected_manual_payload = '{"task_ref":"T-001","lane_ids":["L2"]}'
    expected_apply_payload = '{"task_ref":"T-001"}'
    assert any(
        btn.label == "Apply Judge Execute Step"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_manual_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Preview Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-preview"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Propose Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-propose"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Apply Judge Execute Step"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_manual_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )
    assert any(
        btn.label == "Preview Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-preview"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Propose Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-propose"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )
    assert task_detail is not None
    assert any(
        btn.label == "Apply Judge Execute Step"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_manual_payload
        for btn in task_detail.phase2_action_buttons
    )
    assert runtime_detail.latest_manual_step_summary == (
        "manual_execute=/followup-exec T-001 lane L2 | confidence=medium | waiting_for_operator"
    )
    assert any(
        btn.label == "Preview Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-preview"
        and btn.payload_json == expected_apply_payload
        for btn in task_detail.safe_action_buttons
    )
    assert any(
        btn.label == "Propose Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-propose"
        and btn.payload_json == expected_apply_payload
        for btn in task_detail.phase2_action_buttons
    )


def test_runtime_and_task_detail_surface_latest_replan_auto_route_summary(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="judge_policy_ready",
        outcome_detail="retry_command=/retry T-001 lane L1",
        next_step="/retry T-001 lane L1",
        remediation="inspect the retried task outcome and judge policy reuse before applying another auto-route",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:08:00+09:00",
    )

    _snapshot, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    assert runtime_detail.latest_replan_auto_route_summary == (
        "Replan Auto Route | applied | next=/retry T-001 lane L1 | retry_command=/retry T-001 lane L1"
    )
    assert runtime_detail.latest_replan_auto_operator_summary == (
        "applied=/retry T-001 lane L1 | at=2026-04-10T10:08:00+09:00"
    )
    assert runtime_detail.latest_replan_auto_route_status_summary == (
        "applied=/retry T-001 lane L1 | at=2026-04-10T10:08:00+09:00"
    )
    assert task_detail is not None
    assert task_detail.latest_replan_auto_route_summary == (
        "Replan Auto Route | applied | next=/retry T-001 lane L1 | retry_command=/retry T-001 lane L1"
    )
    assert task_detail.latest_replan_auto_operator_summary == (
        "applied=/retry T-001 lane L1 | at=2026-04-10T10:08:00+09:00"
    )
    runtime_html = dashboard_app.build_dashboard_response(
        "/control/runtimes/O2",
        dashboard_app.DashboardAppConfig(
            control_root=control_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            host="127.0.0.1",
            port=8765,
        ),
    )[2].decode("utf-8")
    task_html = dashboard_app.build_dashboard_response(
        "/control/tasks/by-request/REQ-1",
        dashboard_app.DashboardAppConfig(
            control_root=control_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            host="127.0.0.1",
            port=8765,
        ),
    )[2].decode("utf-8")
    assert "Decision Signals" in runtime_html
    assert "Decision Signals" in task_html
    assert task_detail.latest_replan_auto_route_status_summary == (
        "applied=/retry T-001 lane L1 | at=2026-04-10T10:08:00+09:00"
    )


def test_control_dashboard_post_runtime_todo_accept_route_promotes_worker_proposal(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["todo_proposals"] = [
        {
            "id": "PROP-001",
            "summary": "review worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "kind": "handoff",
            "status": "open",
            "source_request_id": "REQ-1",
            "created_by": "worker",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:05:00+09:00",
        }
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/todo-accept",
        body=json.dumps({"project_ref": "O2", "proposal_ref": "PROP-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/todo O2 accept PROP-001"
    assert payload["proposal"]["proposal_id"] == "PROP-001"
    assert payload["proposal"]["todo_id"] == "TODO-002"
    assert payload["proposal"]["created_new"] is True
    assert payload["next_step"] == "/todo O2"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    proposal = updated["projects"]["alpha"]["todo_proposals"][0]
    assert proposal["status"] == "accepted"
    assert proposal["accepted_todo_id"] == "TODO-002"
    todos = updated["projects"]["alpha"]["todos"]
    assert any(row["id"] == "TODO-002" and row["summary"] == "review worker artifact update for T-001: reports/summary.md" for row in todos)


def test_control_dashboard_post_task_worker_update_preview_route_returns_preview(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-update-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "preview"
    assert payload["source_command"] == "/task T-001 | worker-update-preview"
    assert payload["next_step"] == "/todo O2 accept PROP-001"
    assert payload["preview"]["update_stub_summary"] == "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    assert payload["preview"]["proposal_summary"] == "status=ready | proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    assert payload["preview"]["target_artifacts"] == ["reports/summary.md"]
    assert payload["preview"]["actions"] == ["update reports/summary.md"]
    assert payload["preview"]["cautions"] == ["keep review lane open"]
    assert payload["preview"]["evidence_refs"] == ["reports/summary.md"]


def test_control_dashboard_post_task_worker_apply_propose_route_creates_apply_proposal(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-propose",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/task T-001 | worker-apply-propose"
    assert payload["next_step"] == "/todo O2 accept PROP-001"
    assert payload["proposal"]["created_count"] == 1
    assert payload["proposal"]["proposal_ids"] == ["PROP-001"]
    assert payload["proposal"]["summary"] == "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    assert payload["preview"]["proposal_payloads"][0]["summary"] == "apply worker artifact update for T-001: reports/summary.md"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_worker_update_proposal_ids"] == ["PROP-001"]
    assert updated_task["background_run_worker_update_proposal_summary"] == (
        "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    )
    proposals = updated["projects"]["alpha"]["todo_proposals"]
    assert len(proposals) == 1
    assert proposals[0]["summary"] == "apply worker artifact update for T-001: reports/summary.md"
    assert proposals[0]["status"] == "open"


def test_control_dashboard_post_task_worker_apply_preview_route_returns_preview(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=reports/summary.md"
    task["job_contract_goal"] = "close the acceptance gap"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "revert summary wording if the evidence check fails"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "preview"
    assert payload["source_command"] == "/task T-001 | worker-apply-preview"
    assert payload["next_step"] == "/todo O2 accept PROP-001"
    assert payload["preview"]["proposal_summary"] == "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    assert payload["preview"]["proposal_ids"] == ["PROP-001"]
    assert payload["preview"]["proposal_payloads"][0]["summary"] == "apply worker artifact update for T-001: reports/summary.md"
    assert payload["preview"]["target_artifacts"] == ["reports/summary.md"]


def test_control_dashboard_worker_apply_preview_surfaces_operator_preference_preflight_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "show_source_note",
                    "value": True,
                    "description": "Always include the source note below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                },
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                },
            ]
        },
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=figures/chart.png"
    task["job_contract_goal"] = "close the chart acceptance gap"
    task["job_contract_scope"] = ["figures/chart.png"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["keep the chart readable and support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["figures/chart.png"]
    task["job_contract_rollback_hint"] = "revert the chart update if the verification lane rejects the evidence"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update figures/chart.png"]
    task["background_run_worker_result_cautions"] = ["keep legend readable"]
    task["background_run_worker_result_evidence_refs"] = ["figures/chart.png"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=figures/chart.png"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["preference_artifact_kind"] == "chart"
    assert "preflight=chart" in payload["preference_preflight_summary"]
    assert "applied_preferences=show_source_note=true" in payload["applied_preferences_summary"]
    assert "confirm_preferences=legend_position=bottom" in payload["preference_confirm_summary"]
    assert len(payload["actions"]) == 6
    assert payload["preference_decision_prompt_summary"] == "decision_prompts=legend_position(confirm)"
    assert len(payload["preference_decision_groups"]) == 1
    assert payload["preference_decision_groups"][0]["key"] == "legend_position"
    assert payload["preference_decision_groups"][0]["origin"] == "confirm"
    assert len(payload["preference_decision_groups"][0]["actions"]) == 6
    assert all(row["path"] == "/control/actions/task/operator-preference-decision" for row in payload["actions"])
    assert payload["actions"][0]["label"].startswith("legend_position")
    assert payload["actions"][0]["memory_policy"] == "apply_once"
    assert payload["actions"][0]["memory_scope"] == "session"
    assert any(row["memory_scope"] == "project" for row in payload["actions"])
    assert payload["preview"]["preference_artifact_kind"] == "chart"
    assert payload["preview"]["preference_decision_prompt_summary"] == "decision_prompts=legend_position(confirm)"


def test_control_dashboard_worker_apply_preview_surfaces_repeated_preference_candidates(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preference_candidates(
        project_team_dir,
        {
            "candidates": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "project_ref": "O2",
                    "suggested_value": "bottom",
                    "issue": "legend keeps overlapping the plotted bars",
                    "occurrence_count": 2,
                    "source_refs": ["REQ-1", "REQ-2"],
                }
            ]
        },
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=figures/chart.png"
    task["job_contract_goal"] = "close the chart acceptance gap"
    task["job_contract_scope"] = ["figures/chart.png"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["keep the chart readable and support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["figures/chart.png"]
    task["job_contract_rollback_hint"] = "revert the chart update if the verification lane rejects the evidence"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update figures/chart.png"]
    task["background_run_worker_result_cautions"] = ["keep legend readable"]
    task["background_run_worker_result_evidence_refs"] = ["figures/chart.png"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=figures/chart.png"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["preference_artifact_kind"] == "chart"
    assert "preference_candidates=legend_position=bottom" in payload["preference_candidate_summary"]
    assert payload["preference_confirm_summary"] == "-"
    assert len(payload["actions"]) == 6
    assert payload["preference_decision_prompt_summary"] == "decision_prompts=legend_position(candidate)"
    assert len(payload["preference_decision_groups"]) == 1
    assert payload["preference_decision_groups"][0]["origin"] == "candidate"
    assert payload["preference_decision_groups"][0]["source_scope"] == "project"
    assert payload["preference_decision_groups"][0]["scope_ref"] == "O2"
    assert len(payload["preference_decision_groups"][0]["actions"]) == 6
    assert payload["preference_candidates"][0]["expected_scope"] == "project"
    assert payload["preference_candidates"][0]["expected_scope_ref"] == "O2"
    assert payload["preference_candidates"][0]["expected_scope_label"] == "this project"
    assert payload["preference_candidate_scope_summary"] == "preference_candidate_scopes=legend_position:project:O2"
    assert payload["preference_candidates"][0]["audit_href"] == "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20memory_scope%3Aproject%20legend_position&limit=50"
    assert payload["preference_candidates"][0]["history_href"] == "/control/history?q=artifact_kind%3Achart%20memory_scope%3Aproject%20legend_position&project=O2&scope=dashboard&limit=20"
    assert len(payload["preference_candidates"][0]["actions"]) == 4
    assert [row["label"] for row in payload["preference_candidates"][0]["actions"]] == [
        "promote auto",
        "promote confirm",
        "mute",
        "dismiss",
    ]
    assert payload["preference_candidates"][0]["actions"][0]["path"] == "/control/actions/control/operator-preference-candidate"
    assert "\"task_ref\":\"T-001\"" in payload["preference_candidates"][0]["actions"][0]["payload_json"]
    assert "\"return_path\":\"/control/preferences?project=O2&artifact=chart&scope=project\"" in payload["preference_candidates"][0]["actions"][0]["payload_json"]
    assert all(row["path"] == "/control/actions/task/operator-preference-decision" for row in payload["actions"])
    assert payload["actions"][0]["label"].startswith("legend_position")


def test_control_dashboard_worker_apply_preview_surfaces_seeded_document_preferences(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=docs/notes.md"
    task["job_contract_goal"] = "close the acceptance gap"
    task["job_contract_scope"] = ["docs/notes.md"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["docs/notes.md"]
    task["job_contract_rollback_hint"] = "revert summary wording if the evidence check fails"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update docs/notes.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["docs/notes.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=docs/notes.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["docs/notes.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=docs/notes.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["preference_artifact_kind"] == "document"
    assert "preflight=document" in payload["preference_preflight_summary"]
    assert "confirm_preferences=preserve_heading_structure=true" in payload["preference_confirm_summary"]
    assert "manual_preferences=explicit_open_questions_section=true" in payload["preference_manual_summary"]
    assert len(payload["actions"]) == 6
    assert payload["preference_decision_prompt_summary"] == "decision_prompts=preserve_heading_structure(confirm)"
    assert len(payload["preference_decision_groups"]) == 1
    assert len(payload["preference_decision_groups"][0]["actions"]) == 6
    assert all(row["path"] == "/control/actions/task/operator-preference-decision" for row in payload["actions"])
    assert payload["actions"][0]["label"].startswith("preserve_heading_structure")


def test_control_dashboard_worker_apply_preview_surfaces_chart_bar_profile_preferences(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close bar chart readability gap | scope=figures/bar-chart.png"
    task["job_contract_goal"] = "close the bar chart readability gap"
    task["job_contract_scope"] = ["figures/bar-chart.png"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["keep the bar chart readable and preserve the ranking signal"]
    task["job_contract_artifacts_to_touch"] = ["figures/bar-chart.png"]
    task["job_contract_rollback_hint"] = "revert the bar chart update if the verification lane rejects the evidence"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update figures/bar-chart.png"]
    task["background_run_worker_result_cautions"] = ["keep the ranking easy to scan"]
    task["background_run_worker_result_evidence_refs"] = ["figures/bar-chart.png"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/bar-chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/bar-chart.png"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=figures/bar-chart.png"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["preference_artifact_kind"] == "chart"
    assert payload["preference_artifact_profile"] == "chart_bar"
    assert "profile=chart_bar" in payload["preference_preflight_summary"]
    assert "confirm_preferences=legend_position=bottom" in payload["preference_confirm_summary"]
    assert "category_order=descending" in payload["preference_confirm_summary"]
    assert "show_bar_value_labels=true" in payload["preference_manual_summary"]
    assert len(payload["actions"]) == 12
    assert payload["preference_decision_prompt_summary"] == (
        "decision_prompts=legend_position(confirm) || category_order(confirm)"
    )
    assert [row["key"] for row in payload["preference_decision_groups"]] == [
        "legend_position",
        "category_order",
    ]
    assert all(len(row["actions"]) == 6 for row in payload["preference_decision_groups"])


def test_control_dashboard_worker_apply_preview_surfaces_confirm_and_candidate_decision_groups(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.save_operator_preference_candidates(
        project_team_dir,
        {
            "candidates": [
                {
                    "artifact_kind": "chart",
                    "key": "show_source_note",
                    "project_ref": "O2",
                    "suggested_value": True,
                    "issue": "source provenance keeps getting added by hand",
                    "occurrence_count": 2,
                    "source_refs": ["REQ-1", "REQ-2"],
                }
            ]
        },
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=figures/chart.png"
    task["job_contract_goal"] = "close the chart acceptance gap"
    task["job_contract_scope"] = ["figures/chart.png"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["keep the chart readable and support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["figures/chart.png"]
    task["job_contract_rollback_hint"] = "revert the chart update if the verification lane rejects the evidence"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update figures/chart.png"]
    task["background_run_worker_result_cautions"] = ["keep legend readable"]
    task["background_run_worker_result_evidence_refs"] = ["figures/chart.png"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=figures/chart.png"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["preference_confirm_summary"] == "confirm_preferences=legend_position=bottom | on | confirm | artifact_kind:chart"
    assert "preference_candidates=show_source_note=true" in payload["preference_candidate_summary"]
    assert len(payload["actions"]) == 12
    assert payload["preference_decision_prompt_summary"] == (
        "decision_prompts=legend_position(confirm) || show_source_note(candidate)"
    )
    assert [row["origin"] for row in payload["preference_decision_groups"]] == ["confirm", "candidate"]
    assert [row["key"] for row in payload["preference_decision_groups"]] == ["legend_position", "show_source_note"]
    assert payload["preference_decision_groups"][1]["source_scope"] == "project"
    assert payload["preference_decision_groups"][1]["scope_ref"] == "O2"
    assert payload["preference_candidates"][0]["expected_scope"] == "project"
    assert payload["preference_candidate_scope_summary"] == "preference_candidate_scopes=show_source_note:project:O2"
    assert payload["preference_candidates"][0]["audit_href"] == "/control/audit?focus=preferences&project=O2&q=artifact_kind%3Achart%20memory_scope%3Aproject%20show_source_note&limit=50"
    assert payload["preference_candidates"][0]["history_href"] == "/control/history?q=artifact_kind%3Achart%20memory_scope%3Aproject%20show_source_note&project=O2&scope=dashboard&limit=20"
    assert len(payload["preference_candidates"][0]["actions"]) == 4
    assert payload["preference_candidates"][0]["actions"][1]["label"] == "promote confirm"
    assert "\"task_ref\":\"T-001\"" in payload["preference_candidates"][0]["actions"][1]["payload_json"]
    assert "\"return_path\":\"/control/preferences?project=O2&artifact=chart&scope=project\"" in payload["preference_candidates"][0]["actions"][1]["payload_json"]
    assert all(len(row["actions"]) == 6 for row in payload["preference_decision_groups"])


def test_worker_apply_preview_persists_operator_preference_summaries_to_runtime_and_task_surfaces(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "show_source_note",
                    "value": True,
                    "description": "Always include the source note below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.save_operator_preference_candidates(
        project_team_dir,
        {
            "candidates": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "project_ref": "O2",
                    "suggested_value": "bottom",
                    "issue": "legend keeps overlapping the plotted bars",
                    "occurrence_count": 2,
                    "source_refs": ["REQ-1", "REQ-2"],
                }
            ]
        },
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "deep"
    task["job_contract_summary"] = "ready | goal=close acceptance gap | scope=figures/chart.png"
    task["job_contract_goal"] = "close the chart acceptance gap"
    task["job_contract_scope"] = ["figures/chart.png"]
    task["job_contract_non_goals"] = ["publish final report"]
    task["job_contract_acceptance_checks"] = ["keep the chart readable and support the conclusion with explicit evidence"]
    task["job_contract_artifacts_to_touch"] = ["figures/chart.png"]
    task["job_contract_rollback_hint"] = "revert the chart update if the verification lane rejects the evidence"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "verify"
    task["phase_checkpoint_summary"] = "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready"
    task["phase_checkpoint_rows"] = [
        "plan=done|note=approved plan",
        "implement=done|note=execution complete",
        "verify=active|note=review in progress",
        "handoff=ready|note=handoff ready",
    ]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update figures/chart.png"]
    task["background_run_worker_result_cautions"] = ["keep legend readable"]
    task["background_run_worker_result_evidence_refs"] = ["figures/chart.png"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=figures/chart.png"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert "preference_candidates=legend_position=bottom" in payload["preference_candidate_summary"]

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")

    assert "preflight=chart" in runtime_card.active_task_background_run_operator_preference_preflight_summary
    assert "applied_preferences=show_source_note=true" in runtime_card.active_task_background_run_operator_preference_applied_summary
    assert "preference_candidates=legend_position=bottom" in runtime_card.active_task_background_run_operator_preference_candidate_summary
    assert "preflight=chart" in runtime_detail.active_task_background_run_operator_preference_preflight_summary
    assert task_detail is not None
    assert "applied_preferences=show_source_note=true" in task_detail.background_run_operator_preference_applied_summary
    assert "preference_candidates=legend_position=bottom" in task_detail.background_run_operator_preference_candidate_summary

    detail_status, _detail_headers, detail_body = dashboard_app.build_dashboard_response(
        "/control/tasks/by-request/REQ-1",
        config,
    )
    assert detail_status == 200
    detail_text = detail_body.decode("utf-8")
    assert "background_preference_preflight" in detail_text
    assert "preference_candidates=legend_position=bottom" in detail_text


def test_control_dashboard_operator_preference_decision_route_persists_rule_and_task_override(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/operator-preference-decision",
        body=json.dumps(
            {
                "task_ref": "T-001",
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "description": "Keep the legend below the chart.",
                "choice": "apply_always",
                "return_path": "/control/actions/task/worker-apply-preview",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["outcome"]["kind"] == "operator_preference_decision"
    assert payload["outcome"]["reason_code"] == "registry_updated"
    assert "legend_position=bottom" in payload["applied_preferences_summary"]
    assert "legend_position=bottom" in payload["preference_decision_summary"]
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff=applied_added=legend_position=bottom | on | manual_only | session:-"
    )
    assert payload["refresh_action"]["path"] == "/control/actions/task/worker-apply-preview"
    assert payload["actions"][0]["path"] == "/control/actions/task/worker-apply-preview"

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="manual_only",
        limit=20,
    )
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert "manual_only" in audit.rows[0].preference_refresh_diff_summary

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="manual_only",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert len(history.rows) == 1
    assert "preference_refresh_diff=" in history.rows[0].detail

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_operator_preference_artifact_kind"] == "chart"
    assert updated_task["background_run_operator_preference_session_rules"][0]["key"] == "legend_position"
    assert updated_task["background_run_operator_preference_decisions"][0]["choice"] == "apply_always"

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [row for row in registry["rules"] if row["key"] == "legend_position" and row["artifact_kind"] == "chart"]
    assert matching
    assert matching[0]["enabled"] is True


def test_control_dashboard_operator_preference_decision_route_records_one_off_candidate(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/operator-preference-decision",
        body=json.dumps(
            {
                "task_ref": "T-001",
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "description": "Keep the legend below the chart.",
                "choice": "apply_once",
                "return_path": "/control/actions/task/worker-apply-preview",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["outcome"]["reason_code"] == "session_override_recorded"
    assert "legend_position=bottom" in payload["applied_preferences_summary"]

    candidates = operator_preferences.load_operator_preference_candidates(project_team_dir)
    assert len(candidates["candidates"]) == 1
    assert candidates["candidates"][0]["key"] == "legend_position"
    assert candidates["candidates"][0]["occurrence_count"] == 1
    assert candidates["candidates"][0]["project_ref"] == "O2"


def test_control_dashboard_operator_preference_decision_route_persists_project_scoped_rule(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=figures/chart.png | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["figures/chart.png"]
    gw.save_manager_state(manager_state_file, state)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/operator-preference-decision",
        body=json.dumps(
            {
                "task_ref": "T-001",
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "description": "Keep the legend below the chart.",
                "choice": "apply_always",
                "scope": "project",
                "scope_ref": "O2",
                "return_path": "/control/actions/task/worker-apply-preview",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["outcome"]["reason_code"] == "registry_updated"
    assert "project:O2" in payload["preference_decision_summary"]

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [
        row
        for row in registry["rules"]
        if row["key"] == "legend_position"
        and row["artifact_kind"] == "chart"
        and row["scope"] == "project"
        and row["scope_ref"] == "O2"
    ]
    assert matching
    assert matching[0]["enabled"] is True


def test_control_dashboard_preferences_page_renders_aggregate_registry_and_candidates(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    manager_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    beta_root = control_root / "Beta"
    beta_team_dir = beta_root / ".aoe-team"
    beta_team_dir.mkdir(parents=True, exist_ok=True)
    (beta_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (beta_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (beta_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    manager_state["projects"]["beta"] = {
        "name": "beta",
        "display_name": "Beta",
        "project_alias": "O3",
        "project_root": str(beta_root),
        "team_dir": str(beta_team_dir),
        "overview": "runtime beta",
        "last_request_id": "",
        "tasks": {},
        "task_alias_index": {},
        "task_seq": 0,
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-04-22T09:40:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-04-22T09:00:00+09:00",
        "updated_at": "2026-04-22T09:50:00+09:00",
    }
    manager_state_file.write_text(json.dumps(manager_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    operator_preferences.save_operator_preferences(
        beta_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "description": "Keep the heading structure stable.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        beta_team_dir,
        artifact_kind="document",
        key="explicit_open_questions_section",
        suggested_value=True,
        issue="brief revisions repeatedly missed an open questions section",
        project_ref="O3",
        source_ref="REQ-BETA-1",
        now_iso="2026-04-22T10:10:00+09:00",
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/preferences", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Operator Preferences" in text
    assert "Preferences" in text
    assert "legend_position" in text
    assert "preserve_heading_structure" in text
    assert "show_source_note" in text
    assert "explicit_open_questions_section" in text
    assert "Project Registries" in text
    assert "O2 Alpha" in text
    assert "O3 Beta" in text
    assert 'name="runtime_ref" value="O2"' in text
    assert 'name="runtime_ref" value="O3"' in text
    assert 'name="return_path" value="/control/preferences"' in text
    assert "Artifact Breakdown" in text
    assert "Memory Scope Breakdown" in text
    assert "/control/actions/control/operator-preference-rule" in text
    assert "/control/actions/control/operator-preference-candidate" in text
    assert "/control/preferences?artifact=chart" in text
    assert "/control/preferences?artifact=document" in text
    assert "/control/preferences?scope=artifact_kind" in text
    assert "/control/history?q=artifact_kind%3Achart&amp;scope=dashboard&amp;limit=20" in text
    assert "/control/history?q=artifact_kind%3Adocument&amp;scope=dashboard&amp;limit=20" in text
    assert "/control/history?q=memory_scope%3Aartifact_kind&amp;scope=dashboard&amp;limit=20" in text

    _snapshot, preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    artifact_rows = {row.artifact_kind: row for row in preferences.artifact_rows}
    assert set(artifact_rows.keys()) == {"chart", "document"}
    assert artifact_rows["chart"].rule_count == 1
    assert artifact_rows["chart"].candidate_count == 1
    assert artifact_rows["chart"].ready_candidate_count == 0
    assert artifact_rows["chart"].prompt_mode_summary == "auto=1 | confirm=0 | manual=0 | disabled=0"
    assert artifact_rows["chart"].project_summary == "O2"
    assert artifact_rows["chart"].filter_href == "/control/preferences?artifact=chart"
    assert artifact_rows["chart"].audit_href == "/control/audit?focus=preferences&q=artifact_kind%3Achart&limit=50"
    assert artifact_rows["chart"].history_href == "/control/history?q=artifact_kind%3Achart&scope=dashboard&limit=20"
    assert artifact_rows["document"].rule_count == 1
    assert artifact_rows["document"].candidate_count == 1
    assert artifact_rows["document"].ready_candidate_count == 0
    assert artifact_rows["document"].prompt_mode_summary == "auto=0 | confirm=1 | manual=0 | disabled=0"
    assert artifact_rows["document"].project_summary == "O3"
    assert artifact_rows["document"].filter_href == "/control/preferences?artifact=document"
    assert artifact_rows["document"].audit_href == "/control/audit?focus=preferences&q=artifact_kind%3Adocument&limit=50"
    assert artifact_rows["document"].history_href == "/control/history?q=artifact_kind%3Adocument&scope=dashboard&limit=20"
    scope_rows = {row.scope: row for row in preferences.memory_scope_rows}
    assert scope_rows["artifact_kind"].rule_count == 2
    assert scope_rows["artifact_kind"].filter_href == "/control/preferences?scope=artifact_kind"
    assert scope_rows["artifact_kind"].history_href == "/control/history?q=memory_scope%3Aartifact_kind&scope=dashboard&limit=20"


def test_control_dashboard_preferences_page_filters_to_selected_project(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note kept getting restored by hand for this project",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    manager_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    beta_root = control_root / "Beta"
    beta_team_dir = beta_root / ".aoe-team"
    beta_team_dir.mkdir(parents=True, exist_ok=True)
    (beta_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (beta_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (beta_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    manager_state["projects"]["beta"] = {
        "name": "beta",
        "display_name": "Beta",
        "project_alias": "O3",
        "project_root": str(beta_root),
        "team_dir": str(beta_team_dir),
        "overview": "runtime beta",
        "last_request_id": "",
        "tasks": {},
        "task_alias_index": {},
        "task_seq": 0,
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-04-22T09:40:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-04-22T09:00:00+09:00",
        "updated_at": "2026-04-22T09:50:00+09:00",
    }
    manager_state_file.write_text(json.dumps(manager_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    operator_preferences.save_operator_preferences(
        beta_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "description": "Keep the heading structure stable.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        beta_team_dir,
        artifact_kind="document",
        key="explicit_open_questions_section",
        suggested_value=True,
        issue="brief revisions repeatedly missed an open questions section",
        project_ref="O3",
        source_ref="REQ-BETA-1",
        now_iso="2026-04-22T10:10:00+09:00",
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/preferences?project=O3", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "selected_scope" in text
    assert "O3 Beta" in text
    assert "preserve_heading_structure" in text
    assert "explicit_open_questions_section" in text
    assert "Memory Scope Breakdown" in text
    assert "legend_position" not in text
    assert 'name="runtime_ref" value="O3"' in text
    assert 'name="runtime_ref" value="O2"' not in text
    assert 'name="return_path" value="/control/preferences?project=O3"' in text
    assert "Artifact Breakdown" in text

    _snapshot, preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        project_filter="O3",
    )
    assert len(preferences.artifact_rows) == 1
    assert preferences.artifact_rows[0].artifact_kind == "document"
    assert preferences.artifact_rows[0].rule_count == 1
    assert preferences.artifact_rows[0].candidate_count == 1
    assert preferences.artifact_rows[0].project_summary == "O3"
    assert preferences.artifact_rows[0].audit_href == "/control/audit?focus=preferences&project=O3&q=artifact_kind%3Adocument&limit=50"
    assert preferences.artifact_rows[0].history_href == "/control/history?q=artifact_kind%3Adocument&project=O3&scope=dashboard&limit=20"
    memory_scope_rows = {row.scope: row for row in preferences.memory_scope_rows}
    assert memory_scope_rows["artifact_kind"].rule_count == 1
    assert memory_scope_rows["artifact_kind"].audit_href == "/control/audit?focus=preferences&project=O3&q=memory_scope%3Aartifact_kind&limit=50"
    assert memory_scope_rows["artifact_kind"].history_href == "/control/history?q=memory_scope%3Aartifact_kind&project=O3&scope=dashboard&limit=20"


def test_control_dashboard_preferences_page_filters_to_selected_artifact(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    manager_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    beta_root = control_root / "Beta"
    beta_team_dir = beta_root / ".aoe-team"
    beta_team_dir.mkdir(parents=True, exist_ok=True)
    (beta_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (beta_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (beta_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    manager_state["projects"]["beta"] = {
        "name": "beta",
        "display_name": "Beta",
        "project_alias": "O3",
        "project_root": str(beta_root),
        "team_dir": str(beta_team_dir),
        "overview": "runtime beta",
        "last_request_id": "",
        "tasks": {},
        "task_alias_index": {},
        "task_seq": 0,
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-04-22T09:40:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-04-22T09:00:00+09:00",
        "updated_at": "2026-04-22T09:50:00+09:00",
    }
    manager_state_file.write_text(json.dumps(manager_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    operator_preferences.save_operator_preferences(
        beta_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "description": "Keep the heading structure stable.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        beta_team_dir,
        artifact_kind="document",
        key="explicit_open_questions_section",
        suggested_value=True,
        issue="brief revisions repeatedly missed an open questions section",
        project_ref="O3",
        source_ref="REQ-BETA-1",
        now_iso="2026-04-22T10:10:00+09:00",
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/preferences?artifact=document", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "selected_artifact" in text
    assert "preserve_heading_structure" in text
    assert "explicit_open_questions_section" in text
    assert "legend_position" not in text
    assert "show_source_note" not in text
    assert 'name="return_path" value="/control/preferences?artifact=document"' in text
    assert "/control/preferences?artifact=chart" in text

    _snapshot, preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        artifact_filter="document",
    )
    assert preferences.selected_artifact_summary == "document"
    assert len(preferences.artifact_rows) == 2
    selected_rows = [row for row in preferences.artifact_rows if row.is_selected]
    assert len(selected_rows) == 1
    assert selected_rows[0].artifact_kind == "document"
    assert preferences.return_path == "/control/preferences?artifact=document"
    assert selected_rows[0].history_href == "/control/history?q=artifact_kind%3Adocument&scope=dashboard&limit=20"
    scope_rows = {row.scope: row for row in preferences.memory_scope_rows}
    assert scope_rows["artifact_kind"].history_href == "/control/history?q=memory_scope%3Aartifact_kind%20artifact_kind%3Adocument&scope=dashboard&limit=20"
    assert [row.key for row in preferences.rules] == ["preserve_heading_structure"]
    assert [row.key for row in preferences.candidates] == ["explicit_open_questions_section"]


def test_control_dashboard_preferences_page_surfaces_memory_scope_breakdown(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                },
                {
                    "artifact_kind": "chart",
                    "key": "category_order",
                    "value": "descending",
                    "description": "Keep chart categories in descending order for this project.",
                    "scope": "project",
                    "scope_ref": "O2",
                    "prompt_mode": "confirm",
                    "enabled": True,
                },
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note kept getting restored by hand for this project",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    manager_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    beta_root = control_root / "Beta"
    beta_team_dir = beta_root / ".aoe-team"
    beta_team_dir.mkdir(parents=True, exist_ok=True)
    (beta_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (beta_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (beta_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    manager_state["projects"]["beta"] = {
        "name": "beta",
        "display_name": "Beta",
        "project_alias": "O3",
        "project_root": str(beta_root),
        "team_dir": str(beta_team_dir),
        "overview": "runtime beta",
        "last_request_id": "",
        "tasks": {},
        "task_alias_index": {},
        "task_seq": 0,
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-04-22T09:40:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-04-22T09:00:00+09:00",
        "updated_at": "2026-04-22T09:50:00+09:00",
    }
    manager_state_file.write_text(json.dumps(manager_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    operator_preferences.save_operator_preferences(
        beta_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "document",
                    "key": "preserve_heading_structure",
                    "value": True,
                    "description": "Keep the heading structure stable across teams.",
                    "scope": "user_global",
                    "prompt_mode": "confirm",
                    "enabled": False,
                }
            ]
        },
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/preferences", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Memory Scope Breakdown" in text
    assert "/control/audit?focus=preferences&amp;q=memory_scope%3Aproject&amp;limit=50" in text
    assert "/control/history?q=memory_scope%3Auser_global&amp;scope=dashboard&amp;limit=20" in text

    _snapshot, preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    scope_rows = {row.scope: row for row in preferences.memory_scope_rows}
    assert set(scope_rows.keys()) == {"session", "project", "artifact_kind", "user_global"}
    assert scope_rows["session"].rule_count == 0
    assert scope_rows["session"].enabled_count == 0
    assert scope_rows["artifact_kind"].rule_count == 1
    assert scope_rows["artifact_kind"].artifact_summary == "chart=1"
    assert scope_rows["artifact_kind"].project_summary == "O2"
    assert scope_rows["project"].rule_count == 1
    assert scope_rows["project"].candidate_count == 1
    assert scope_rows["project"].ready_candidate_count == 0
    assert scope_rows["project"].enabled_count == 1
    assert scope_rows["project"].disabled_count == 0
    assert scope_rows["project"].prompt_mode_summary == "auto=0 | confirm=1 | manual=0 | disabled=0"
    assert scope_rows["project"].history_href == "/control/history?q=memory_scope%3Aproject&scope=dashboard&limit=20"
    assert scope_rows["user_global"].rule_count == 1
    assert scope_rows["user_global"].candidate_count == 0
    assert scope_rows["user_global"].enabled_count == 0
    assert scope_rows["user_global"].disabled_count == 1
    assert scope_rows["user_global"].artifact_summary == "document=1"
    assert scope_rows["user_global"].project_summary == "O3"


def test_control_dashboard_preferences_page_filters_to_selected_memory_scope(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                },
                {
                    "artifact_kind": "chart",
                    "key": "category_order",
                    "value": "descending",
                    "description": "Keep chart categories in descending order for this project.",
                    "scope": "project",
                    "scope_ref": "O2",
                    "prompt_mode": "confirm",
                    "enabled": True,
                },
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="color_palette",
        suggested_value="accessible",
        issue="chart revisions repeatedly needed an accessible palette",
        source_ref="REQ-2",
        now_iso="2026-04-22T10:02:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/preferences?artifact=chart&scope=project", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "selected_memory_scope" in text
    assert "this project" in text
    assert "category_order" in text
    assert "legend_position" not in text
    assert "show_source_note" in text
    assert "color_palette" not in text
    assert 'name="return_path" value="/control/preferences?artifact=chart&amp;scope=project"' in text
    assert "/control/preferences?artifact=chart" in text
    assert "/control/preferences?artifact=chart&amp;scope=artifact_kind" in text
    assert "/control/audit?focus=preferences&amp;q=memory_scope%3Aproject%20artifact_kind%3Achart&amp;limit=50" in text

    _snapshot, preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        artifact_filter="chart",
        scope_filter="project",
    )
    assert preferences.selected_memory_scope_summary == "this project"
    assert preferences.return_path == "/control/preferences?artifact=chart&scope=project"
    assert [row.key for row in preferences.rules] == ["category_order"]
    assert [row.key for row in preferences.candidates] == ["show_source_note"]
    assert preferences.candidates[0].expected_scope == "project"
    assert preferences.candidates[0].expected_scope_ref == "O2"
    scope_rows = {row.scope: row for row in preferences.memory_scope_rows}
    assert scope_rows["project"].is_selected is True
    assert scope_rows["project"].candidate_count == 1
    assert scope_rows["project"].filter_href == "/control/preferences?artifact=chart&scope=project"
    assert scope_rows["project"].audit_href == "/control/audit?focus=preferences&q=memory_scope%3Aproject%20artifact_kind%3Achart&limit=50"
    assert scope_rows["project"].history_href == "/control/history?q=memory_scope%3Aproject%20artifact_kind%3Achart&scope=dashboard&limit=20"
    artifact_rows = {row.artifact_kind: row for row in preferences.artifact_rows}
    assert artifact_rows["chart"].filter_href == "/control/preferences?artifact=chart&scope=project"
    assert artifact_rows["chart"].history_href == "/control/history?q=artifact_kind%3Achart%20memory_scope%3Aproject&scope=dashboard&limit=20"

    _snapshot, artifact_scope_preferences = dashboard_state.load_dashboard_preferences_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        artifact_filter="chart",
        scope_filter="artifact_kind",
    )
    assert [row.key for row in artifact_scope_preferences.rules] == ["legend_position"]
    assert [row.key for row in artifact_scope_preferences.candidates] == ["color_palette"]
    assert artifact_scope_preferences.candidates[0].expected_scope == "artifact_kind"
    assert artifact_scope_preferences.candidates[0].expected_scope_ref == "chart"
    artifact_scope_rows = {row.scope: row for row in artifact_scope_preferences.memory_scope_rows}
    assert artifact_scope_rows["artifact_kind"].candidate_count == 1


def test_control_dashboard_preferences_page_destructive_forms_require_confirmation(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-2",
        now_iso="2026-04-22T10:05:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/preferences?project=O2&artifact=chart",
        config,
    )
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")

    def attrs_for(command: str, action: str) -> dict[str, str]:
        form = _dashboard_action_form(text, command=command, action=action)
        attrs = form.get("attrs") if isinstance(form.get("attrs"), dict) else {}
        return {str(key): str(value) for key, value in attrs.items()}

    rule_auto = attrs_for("pref-rule:legend_position:auto", "/control/actions/control/operator-preference-rule")
    rule_disable = attrs_for("pref-rule:legend_position:disable", "/control/actions/control/operator-preference-rule")
    rule_delete = attrs_for("pref-rule:legend_position:delete", "/control/actions/control/operator-preference-rule")
    candidate_auto = attrs_for("pref-candidate:show_source_note:auto", "/control/actions/control/operator-preference-candidate")
    candidate_confirm = attrs_for("pref-candidate:show_source_note:confirm", "/control/actions/control/operator-preference-candidate")
    candidate_disable = attrs_for("pref-candidate:show_source_note:disable", "/control/actions/control/operator-preference-candidate")
    candidate_dismiss = attrs_for("pref-candidate:show_source_note:dismiss", "/control/actions/control/operator-preference-candidate")

    assert "data-action-confirm" not in rule_auto
    assert rule_disable["data-action-confirm"] == "true"
    assert rule_disable["data-action-confirm-message"] == "Turn off preference rule legend_position for chart?"
    assert rule_delete["data-action-confirm"] == "true"
    assert rule_delete["data-action-confirm-message"] == "Delete preference rule legend_position for chart?"

    assert "data-action-confirm" not in candidate_auto
    assert "data-action-confirm" not in candidate_confirm
    assert candidate_disable["data-action-confirm"] == "true"
    assert candidate_disable["data-action-confirm-message"] == "Mute preference candidate show_source_note for chart?"
    assert candidate_dismiss["data-action-confirm"] == "true"
    assert candidate_dismiss["data-action-confirm-message"] == "Dismiss preference candidate show_source_note for chart?"


def test_control_dashboard_preferences_browser_submit_updates_action_result(tmp_path: Path) -> None:
    chrome_path = _find_chrome_for_dashboard_smoke()
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-2",
        now_iso="2026-04-22T10:05:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=0,
    )
    server = dashboard_app.ThreadingHTTPServer(("127.0.0.1", 0), dashboard_app.DashboardRequestHandler)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=int(server.server_address[1]),
    )
    server.dashboard_config = config  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process: subprocess.Popen[str] | None = None
    cdp: _CdpWebSocket | None = None
    try:
        target_url = f"http://127.0.0.1:{config.port}/control/preferences?project=O2&artifact=chart"
        process, cdp = _open_chrome_cdp_page(chrome_path, target_url, tmp_path)
        _wait_for_cdp_eval(cdp, "document.readyState === 'complete' || document.readyState === 'interactive'")
        submit_result = _cdp_eval(
            cdp,
            """
            (() => {
              window.__dashboardFetches = [];
              const nativeFetch = window.fetch.bind(window);
              window.fetch = async (path, options = {}) => {
                window.__dashboardFetches.push({
                  path: String(path),
                  method: String(options.method || ""),
                  contentType: String((options.headers || {})["Content-Type"] || ""),
                  body: String(options.body || "")
                });
                return nativeFetch(path, options);
              };
              const form = document.querySelector('form[data-action-command="pref-candidate:show_source_note:auto"]');
              if (!form) {
                return { ok: false, reason: "candidate_form_missing" };
              }
              form.requestSubmit();
              return { ok: true, action: form.action };
            })()
            """,
        )
        assert isinstance(submit_result, dict)
        assert submit_result["ok"] is True
        result = _wait_for_cdp_eval(
            cdp,
            """
            (() => {
              const panel = document.querySelector("#action-result");
              const summary = document.querySelector("#action-result-summary")?.textContent || "";
              const rows = document.querySelector("#action-result-rows")?.textContent || "";
              const links = Array.from(document.querySelectorAll("#action-result-links a")).map((anchor) => ({
                text: anchor.textContent || "",
                href: anchor.getAttribute("href") || ""
              }));
              const body = document.querySelector("#action-result-body")?.textContent || "";
              if (!body.includes('"status": "executed"') || !body.includes("show_source_note")) {
                return false;
              }
              return {
                hidden: panel ? panel.classList.contains("hidden") : true,
                summary,
                rows,
                links,
                body,
                fetches: window.__dashboardFetches || []
              };
            })()
            """,
            timeout=8,
        )
        assert isinstance(result, dict)
        assert result["hidden"] is False
        assert "Candidate show_source_note -> promote auto" in str(result["summary"])
        assert "status=executed" in str(result["summary"])
        assert "preference_memory_scope" in str(result["rows"])
        assert "preference_refresh_diff" in str(result["rows"])
        links = result.get("links")
        assert isinstance(links, list)
        assert {"text": "refresh preferences", "href": "/control/preferences?project=O2&artifact=chart"} in links
        fetches = result.get("fetches")
        assert isinstance(fetches, list)
        assert len(fetches) == 1
        fetch_payload = json.loads(str(fetches[0]["body"]))
        assert fetches[0]["method"] == "POST"
        assert fetches[0]["contentType"] == "application/json"
        assert fetch_payload["runtime_ref"] == "O2"
        assert fetch_payload["artifact_kind"] == "chart"
        assert fetch_payload["key"] == "show_source_note"
        assert fetch_payload["mode"] == "auto"

        cancel_result = _cdp_eval(
            cdp,
            """
            (() => {
              window.__dashboardFetches = [];
              window.__confirmMessage = "";
              window.confirm = (message) => {
                window.__confirmMessage = String(message);
                return false;
              };
              const form = document.querySelector('form[data-action-command="pref-rule:legend_position:delete"]');
              if (!form) {
                return { ok: false, reason: "delete_form_missing" };
              }
              form.requestSubmit();
              return {
                ok: true,
                confirmMessage: window.__confirmMessage,
                fetchCount: window.__dashboardFetches.length
              };
            })()
            """,
        )
        assert isinstance(cancel_result, dict)
        assert cancel_result["ok"] is True
        assert cancel_result["confirmMessage"] == "Delete preference rule legend_position for chart?"
        assert cancel_result["fetchCount"] == 0

        refresh_click = _cdp_eval(
            cdp,
            """
            (() => {
              const link = Array.from(document.querySelectorAll("#action-result-links a")).find((anchor) => (
                (anchor.textContent || "").trim() === "refresh preferences"
                && anchor.getAttribute("href") === "/control/preferences?project=O2&artifact=chart"
              ));
              if (!link) {
                return { ok: false, reason: "refresh_link_missing" };
              }
              link.click();
              return { ok: true, href: link.getAttribute("href") || "" };
            })()
            """,
        )
        assert isinstance(refresh_click, dict)
        assert refresh_click == {"ok": True, "href": "/control/preferences?project=O2&artifact=chart"}

        refreshed_page = _wait_for_cdp_eval(
            cdp,
            """
            (() => {
              if (document.readyState !== "complete" && document.readyState !== "interactive") {
                return false;
              }
              if (window.location.pathname !== "/control/preferences" || window.location.search !== "?project=O2&artifact=chart") {
                return false;
              }
              const body = document.body ? (document.body.textContent || "") : "";
              const promotedRule = document.querySelector('form[data-action-command="pref-rule:show_source_note:auto"]');
              const staleCandidate = document.querySelector('form[data-action-command^="pref-candidate:show_source_note:"]');
              if (!promotedRule || staleCandidate) {
                return false;
              }
              return {
                href: window.location.pathname + window.location.search,
                hasPromotedRule: Boolean(promotedRule),
                hasStaleCandidate: Boolean(staleCandidate),
                hasEmptyCandidateCopy: body.includes("no candidate recommendations")
              };
            })()
            """,
            timeout=8,
        )
        assert refreshed_page == {
            "href": "/control/preferences?project=O2&artifact=chart",
            "hasPromotedRule": True,
            "hasStaleCandidate": False,
            "hasEmptyCandidateCopy": True,
        }
    finally:
        if cdp is not None:
            cdp.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        server.shutdown()
        server.server_close()

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    assert [row["key"] for row in registry["rules"]] == ["legend_position", "show_source_note"]
    assert registry["rules"][0]["key"] == "legend_position"
    assert registry["rules"][1]["scope"] == "project"
    candidates = operator_preferences.load_operator_preference_candidates(project_team_dir)
    assert candidates["candidates"] == []


def test_control_dashboard_preferences_page_rule_form_submit_updates_registry(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/preferences?project=O2&artifact=chart&scope=artifact_kind",
        config,
    )
    text = body.decode("utf-8")
    form = _dashboard_action_form(
        text,
        command="pref-rule:legend_position:auto",
        action="/control/actions/control/operator-preference-rule",
    )
    inputs = form["inputs"]

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert inputs == {
        "runtime_ref": "O2",
        "return_path": "/control/preferences?project=O2&artifact=chart&scope=artifact_kind",
        "artifact_kind": "chart",
        "key": "legend_position",
        "scope": "artifact_kind",
        "scope_ref": "chart",
        "value_json": "\"bottom\"",
        "description": "Keep the legend below the chart.",
        "mode": "auto",
    }

    action_status, action_headers, action_body = _submit_dashboard_action_form(form, config=config)
    payload = json.loads(action_body.decode("utf-8"))

    assert action_status == 200
    assert action_headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/prefs rule chart:legend_position auto"
    assert payload["next_step"] == "/control/preferences?project=O2&artifact=chart&scope=artifact_kind"
    assert payload["preference_memory_scope_summary"] == "preference_memory_scope=artifact_kind:chart"
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff=applied_added=legend_position=bottom | on | auto | artifact_kind:chart"
    )

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="memory_scope:artifact_kind applied_added",
        limit=20,
    )
    assert audit.total_rows == 1
    assert audit.rows[0].preference_memory_scope_summary == "preference_memory_scope=artifact_kind:chart"
    assert "applied_added=legend_position=bottom" in audit.rows[0].preference_refresh_diff_summary

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [row for row in registry["rules"] if row["key"] == "legend_position" and row["artifact_kind"] == "chart"]
    assert matching
    assert matching[0]["prompt_mode"] == "auto"


def test_control_dashboard_preferences_page_candidate_form_submit_promotes_candidate(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-2",
        now_iso="2026-04-22T10:05:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response(
        "/control/preferences?project=O2&artifact=chart&scope=project",
        config,
    )
    text = body.decode("utf-8")
    form = _dashboard_action_form(
        text,
        command="pref-candidate:show_source_note:auto",
        action="/control/actions/control/operator-preference-candidate",
    )
    inputs = form["inputs"]

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert inputs == {
        "runtime_ref": "O2",
        "return_path": "/control/preferences?project=O2&artifact=chart&scope=project",
        "artifact_kind": "chart",
        "key": "show_source_note",
        "project_ref": "O2",
        "value_json": "true",
        "description": "source note was missing in repeated chart revisions",
        "mode": "auto",
    }

    action_status, action_headers, action_body = _submit_dashboard_action_form(form, config=config)
    payload = json.loads(action_body.decode("utf-8"))

    assert action_status == 200
    assert action_headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/prefs candidate chart:show_source_note auto"
    assert payload["next_step"] == "/control/preferences?project=O2&artifact=chart&scope=project"
    assert payload["preference_candidate_scope_summary"] == "preference_candidate_scopes=show_source_note:project:O2"
    assert payload["preference_memory_scope_summary"] == "preference_memory_scope=project:O2"
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff="
        "applied_added=show_source_note=true | on | auto | project:O2 ; "
        "candidates_removed=show_source_note=true | hits=2 | issue=source note was missing in repeated chart revisions"
    )

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="memory_scope:project candidates_removed",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert "preference_refresh_diff=" in history.rows[0].detail

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [row for row in registry["rules"] if row["key"] == "show_source_note" and row["artifact_kind"] == "chart"]
    assert matching
    assert matching[0]["scope"] == "project"
    assert matching[0]["scope_ref"] == "O2"
    assert matching[0]["prompt_mode"] == "auto"
    candidates = operator_preferences.load_operator_preference_candidates(project_team_dir)
    assert candidates["candidates"] == []


def test_control_dashboard_operator_preference_rule_action_updates_registry(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/operator-preference-rule",
        body=json.dumps(
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "scope": "artifact_kind",
                "scope_ref": "chart",
                "value_json": json.dumps("bottom", ensure_ascii=False),
                "description": "Keep the legend below the chart.",
                "mode": "auto",
                "return_path": "/control/preferences?project=O2&artifact=chart&scope=artifact_kind",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["focus_badge"] == "preferences"
    assert payload["project_alias"] == "O2"
    assert payload["next_step"] == "/control/preferences?project=O2&artifact=chart&scope=artifact_kind"
    assert payload["outcome"]["kind"] == "operator_preference_rule"
    assert payload["outcome"]["reason_code"] == "rule_updated"
    assert "legend_position=bottom" in payload["applied_preferences_summary"]
    assert payload["preference_memory_scope_summary"] == "preference_memory_scope=artifact_kind:chart"
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff=applied_added=legend_position=bottom | on | auto | artifact_kind:chart"
    )
    assert payload["preview"]["detail_path"] == "/control/preferences?project=O2&artifact=chart&scope=artifact_kind"

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="applied_added",
        limit=20,
    )
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert audit.rows[0].preference_memory_scope_summary == "preference_memory_scope=artifact_kind:chart"
    assert "applied_added=legend_position=bottom" in audit.rows[0].preference_refresh_diff_summary

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="applied_added",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert len(history.rows) == 1
    assert "preference_refresh_diff=" in history.rows[0].detail

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [row for row in registry["rules"] if row["key"] == "legend_position" and row["artifact_kind"] == "chart"]
    assert matching
    assert matching[0]["enabled"] is True
    assert matching[0]["prompt_mode"] == "auto"


def test_control_dashboard_operator_preference_rule_action_delete_records_refresh_diff(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/operator-preference-rule",
        body=json.dumps(
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "scope": "artifact_kind",
                "scope_ref": "chart",
                "mode": "delete",
                "return_path": "/control/preferences?project=O2&artifact=chart&scope=artifact_kind",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["outcome"]["reason_code"] == "rule_deleted"
    assert payload["preference_memory_scope_summary"] == "preference_memory_scope=artifact_kind:chart"
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff=applied_removed=legend_position=bottom | on | auto | artifact_kind:chart"
    )

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="applied_removed",
        limit=20,
    )
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert audit.rows[0].preference_memory_scope_summary == "preference_memory_scope=artifact_kind:chart"
    assert "applied_removed=legend_position=bottom" in audit.rows[0].preference_refresh_diff_summary

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    assert registry["rules"] == []


def test_control_dashboard_operator_preference_rule_action_invalid_return_path_falls_back_to_preferences(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.save_operator_preferences(
        project_team_dir,
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "bottom",
                    "description": "Keep the legend below the chart.",
                    "scope": "artifact_kind",
                    "prompt_mode": "confirm",
                    "enabled": True,
                }
            ]
        },
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/operator-preference-rule",
        body=json.dumps(
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "scope": "artifact_kind",
                "scope_ref": "chart",
                "value_json": json.dumps("bottom", ensure_ascii=False),
                "description": "Keep the legend below the chart.",
                "mode": "auto",
                "return_path": "https://example.com/control/preferences?project=O2",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["next_step"] == "/control/preferences"
    assert payload["preview"]["detail_path"] == "/control/preferences"


def test_control_dashboard_operator_preference_candidate_action_promotes_and_clears_candidate(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    project_team_dir = project_root / ".aoe-team"
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-1",
        now_iso="2026-04-22T10:00:00+09:00",
    )
    operator_preferences.record_preference_candidate(
        project_team_dir,
        artifact_kind="chart",
        key="show_source_note",
        suggested_value=True,
        issue="source note was missing in repeated chart revisions",
        project_ref="O2",
        source_ref="REQ-2",
        now_iso="2026-04-22T10:05:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/operator-preference-candidate",
        body=json.dumps(
            {
                "task_ref": "T-001",
                "artifact_kind": "chart",
                "key": "show_source_note",
                "project_ref": "O2",
                "value_json": json.dumps(True, ensure_ascii=False),
                "description": "source note was missing in repeated chart revisions",
                "mode": "auto",
                "return_path": "/control/preferences?project=O2&artifact=chart&scope=project",
            }
        ).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["focus_badge"] == "preferences"
    assert payload["project_alias"] == "O2"
    assert payload["outcome"]["kind"] == "operator_preference_candidate"
    assert payload["outcome"]["reason_code"] == "candidate_promoted"
    assert payload["next_step"] == "/control/preferences?project=O2&artifact=chart&scope=project"
    assert "show_source_note=true" in payload["applied_preferences_summary"]
    assert "show_source_note=true" in payload["preference_candidate_summary"]
    assert payload["preference_candidate_scope_summary"] == "preference_candidate_scopes=show_source_note:project:O2"
    assert payload["preference_memory_scope_summary"] == "preference_memory_scope=project:O2"
    assert payload["preference_refresh_diff_summary"] == (
        "preference_refresh_diff="
        "applied_added=show_source_note=true | on | auto | project:O2 ; "
        "candidates_removed=show_source_note=true | hits=2 | issue=source note was missing in repeated chart revisions"
    )
    assert payload["refresh_action"]["path"] == "/control/actions/task/worker-apply-preview"
    assert payload["refresh_action"]["payload_json"] == "{\"task_ref\":\"T-001\"}"
    assert payload["actions"][0]["label"] == "Reopen Preview"
    assert payload["actions"][0]["path"] == "/control/actions/task/worker-apply-preview"
    assert payload["preview"]["detail_path"] == "/control/preferences?project=O2&artifact=chart&scope=project"

    _snapshot, audit = dashboard_state.load_dashboard_action_audit_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        focus="preferences",
        query="candidates_removed",
        limit=20,
    )
    assert audit.total_rows == 1
    assert len(audit.rows) == 1
    assert "candidates_removed=show_source_note=true" in audit.rows[0].preference_refresh_diff_summary

    _snapshot, history = dashboard_state.load_dashboard_history_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        query="candidates_removed",
        scope="dashboard",
        limit=20,
    )
    assert history.total_rows == 1
    assert len(history.rows) == 1
    assert "preference_refresh_diff=" in history.rows[0].detail

    registry = operator_preferences.load_operator_preferences(project_team_dir)
    matching = [row for row in registry["rules"] if row["key"] == "show_source_note" and row["artifact_kind"] == "chart"]
    assert matching
    assert matching[0]["prompt_mode"] == "auto"
    assert matching[0]["enabled"] is True
    assert matching[0]["scope"] == "project"
    assert matching[0]["scope_ref"] == "O2"

    candidates = operator_preferences.load_operator_preference_candidates(project_team_dir)
    assert candidates["candidates"] == []


def test_dashboard_blocks_worker_apply_until_phase_checkpoint_reaches_verify_or_handoff(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "analysis"
    task["background_run_task_contract_module_summary"] = "analysis | findings_evidence_gate"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = (
        "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    )
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    task["background_run_worker_record_rows_summary"] = (
        "analysis_record_rows | finding_row=update reports/summary.md|state=stable | "
        "evidence_row=reports/summary.md|state=attached | caveat_row=-|state=clear|note=findings_stable"
    )
    task["background_run_worker_record_rows"] = [
        "finding_row=update reports/summary.md|state=stable",
        "evidence_row=reports/summary.md|state=attached",
        "caveat_row=-|state=clear|note=findings_stable",
    ]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    task["job_contract_goal"] = "Summarize findings with supporting evidence"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_non_goals"] = ["avoid unrelated source mutations"]
    task["job_contract_risks"] = ["review wording before handoff"]
    task["job_contract_acceptance_checks"] = ["attach evidence to the summary update"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "limit mutations to declared artifact targets and verify acceptance checks before apply"
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "implement"
    task["phase_checkpoint_summary"] = (
        "status=active | current=implement | plan=ready|note=contract_captured | "
        "implement=running|note=worker_apply_pending"
    )
    task["phase_checkpoint_rows"] = [
        "plan=ready|note=contract_captured",
        "implement=running|note=worker_apply_pending",
    ]
    gw.save_manager_state(manager_state_file, state)

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    blocked_apply_paths = {
        "/control/actions/task/worker-apply-preview",
        "/control/actions/task/worker-apply-propose",
        "/control/actions/task/worker-apply-accept",
    }
    assert all(btn.path not in blocked_apply_paths for btn in runtime_card.runtime_safe_action_buttons)
    assert all(btn.path not in blocked_apply_paths for btn in runtime_card.runtime_phase2_action_buttons)
    assert all(btn.path not in blocked_apply_paths for btn in runtime_detail.active_task_safe_action_buttons)
    assert all(btn.path not in blocked_apply_paths for btn in runtime_detail.active_task_phase2_action_buttons)
    assert task_detail is not None
    assert all(btn.path not in blocked_apply_paths for btn in task_detail.safe_action_buttons)
    assert all(btn.path not in blocked_apply_paths for btn in task_detail.phase2_action_buttons)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "blocked"
    assert payload["outcome"]["reason_code"] == "phase_checkpoint_not_apply_ready"
    assert payload["next_step"] == "/task T-001"
    assert payload["job_contract"] == "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    assert payload["phase_checkpoint"] == (
        "status=active | current=implement | plan=ready|note=contract_captured | "
        "implement=running|note=worker_apply_pending"
    )


def test_dashboard_blocks_manual_routes_until_phase_checkpoint_reaches_verify_or_handoff(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["job_contract_status"] = "ready"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=ready | plan=standard | scope=1 | checks=1 | artifacts=1"
    task["job_contract_goal"] = "Summarize findings with supporting evidence"
    task["job_contract_scope"] = ["reports/summary.md"]
    task["job_contract_non_goals"] = ["avoid unrelated source mutations"]
    task["job_contract_risks"] = ["review wording before handoff"]
    task["job_contract_acceptance_checks"] = ["attach evidence to the summary update"]
    task["job_contract_artifacts_to_touch"] = ["reports/summary.md"]
    task["job_contract_rollback_hint"] = "limit mutations to declared artifact targets before applying judge-backed steps"
    task["debug_packet_state"] = "active"
    task["debug_packet_summary"] = "state=active | symptom=background_run_inflight | evidence=1 | next=/task T-001"
    task["debug_packet_symptom"] = "background_run_inflight"
    task["debug_packet_evidence"] = ["runtime handle still attached"]
    task["debug_packet_failed_attempt"] = "/retry T-001 lane L1"
    task["debug_packet_next_step"] = "/task T-001"
    task["phase1_mode"] = "ensemble"
    task["phase1_rounds"] = 3
    task["phase1_current_round"] = 3
    task["phase1_current_total_rounds"] = 3
    task["phase1_current_phase"] = "verification"
    task["phase1_current_provider"] = "codex"
    task["phase1_current_planner"] = "codex"
    task["phase1_current_critic"] = "claude"
    task["phase1_providers"] = ["codex", "claude"]
    task["plan"] = {
        "summary": "approved manual followup plan",
        "subtasks": [{"id": "S1", "owner_role": "Codex-Analyst", "title": "Prepare manual handoff"}],
    }
    task["plan_critic"] = {"approved": True, "issues": [], "recommendations": ["ready for manual followup"]}
    task["plan_review_count"] = 3
    task["plan_convergence_status"] = "ready"
    task["plan_gate_passed"] = True
    task["phase_checkpoint_status"] = "active"
    task["phase_checkpoint_current_phase"] = "implement"
    task["phase_checkpoint_summary"] = (
        "status=active | current=implement | plan=done|note=contract_ready | "
        "implement=running|note=manual_step_not_ready"
    )
    task["phase_checkpoint_rows"] = [
        "plan=done|note=contract_ready",
        "implement=running|note=manual_step_not_ready",
    ]
    task["followup_brief_status"] = "partially_executable"
    task["followup_brief_summary"] = "partially_executable | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator keeps the review slice"
    task["exec_critic"] = {
        "manual_followup_execution_lane_ids": ["L2"],
        "manual_followup_review_lane_ids": ["R1"],
        "reason": "operator keeps the review slice",
    }
    gw.save_manager_state(manager_state_file, state)

    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Replan | blocked",
        status="blocked",
        outcome_kind="replan",
        outcome_status="blocked",
        outcome_reason_code="planning_gate",
        outcome_detail="planning critic blocked replan",
        next_step="/followup-exec T-001 lane L2",
        remediation="judge decision reuse: action=followup_execute next=/followup-exec T-001 lane L2",
        source_command="/replan T-001 lane L1",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-10T10:07:00+09:00",
        extra={
            "replan_auto_routing_policy": {
                "source": "latest_offdesk_judge",
                "status": "manual_ready",
                "current_action": "replan",
                "suggested_action": "followup_execute",
                "suggested_next_step": "/followup-exec T-001 lane L2",
                "decision_mode": "promoted_next_step",
                "supports_auto_decision": True,
                "can_auto_apply": False,
                "requires_operator_confirmation": True,
                "confidence": "medium",
            },
        },
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        request_id="REQ-1",
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    blocked_manual_paths = {
        "/control/actions/task/followup",
        "/control/actions/task/followup-execute",
    }
    assert all(btn.path not in blocked_manual_paths for btn in runtime_card.runtime_safe_action_buttons)
    assert all(btn.path not in blocked_manual_paths for btn in runtime_card.runtime_phase2_action_buttons)
    assert all(btn.path not in blocked_manual_paths for btn in runtime_detail.active_task_safe_action_buttons)
    assert all(btn.path not in blocked_manual_paths for btn in runtime_detail.active_task_phase2_action_buttons)
    assert task_detail is not None
    assert all(btn.path not in blocked_manual_paths for btn in task_detail.safe_action_buttons)
    assert all(btn.path not in blocked_manual_paths for btn in task_detail.phase2_action_buttons)

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["R1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    assert preview_status == 409
    assert preview_headers["Content-Type"].startswith("application/json")
    assert preview_payload["outcome"]["reason_code"] == "phase_checkpoint_not_manual_ready"
    assert preview_payload["next_step"] == "/task T-001"
    assert preview_payload["phase_checkpoint"] == (
        "status=active | current=implement | plan=done|note=contract_ready | "
        "implement=running|note=manual_step_not_ready"
    )

    execute_status, execute_headers, execute_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup-execute",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L2"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    execute_payload = json.loads(execute_body.decode("utf-8"))
    assert execute_status == 409
    assert execute_headers["Content-Type"].startswith("application/json")
    assert execute_payload["outcome"]["reason_code"] == "phase_checkpoint_not_manual_ready"
    assert execute_payload["next_step"] == "/task T-001"
    assert execute_payload["phase_checkpoint"] == (
        "status=active | current=implement | plan=done|note=contract_ready | "
        "implement=running|note=manual_step_not_ready"
    )


def test_control_dashboard_post_task_worker_apply_accept_route_promotes_apply_proposal(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_summary"] = "task=T-001 | pack=offdesk_execute | brief=underspecified | docs=2"
    task["background_run_worker_result_summary"] = "status=ready | worker summary drafted | actions=1 | refs=1"
    task["background_run_worker_result_actions"] = ["update reports/summary.md"]
    task["background_run_worker_result_cautions"] = ["keep review lane open"]
    task["background_run_worker_result_evidence_refs"] = ["reports/summary.md"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    state["projects"]["alpha"]["todo_proposals"] = [
        {
            "id": "PROP-001",
            "summary": "apply worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "kind": "handoff",
            "status": "open",
            "source_request_id": "REQ-1",
            "created_by": "worker",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:05:00+09:00",
        }
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-accept",
        body=json.dumps({"task_ref": "T-001", "proposal_ref": "PROP-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/task T-001 | worker-apply-accept PROP-001"
    assert payload["outcome"]["kind"] == "worker_apply_accept"
    assert payload["proposal"]["proposal_id"] == "PROP-001"
    assert payload["proposal"]["todo_id"] == "TODO-002"
    assert payload["next_step"] == "/todo O2"
    assert payload["preview"]["proposal_summary"] == "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    assert payload["preview"]["target_artifacts"] == ["reports/summary.md"]

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    proposal = updated["projects"]["alpha"]["todo_proposals"][0]
    assert proposal["status"] == "accepted"
    assert proposal["accepted_todo_id"] == "TODO-002"
    todos = updated["projects"]["alpha"]["todos"]
    assert any(row["id"] == "TODO-002" and row["summary"] == "apply worker artifact update for T-001: reports/summary.md" for row in todos)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_worker_apply_accept_status"] == "applied"
    assert updated_task["background_run_worker_apply_accept_todo_id"] == "TODO-002"
    assert updated_task["background_run_worker_apply_accept_proposal_id"] == "PROP-001"
    assert "state=applied | todo=TODO-002 | proposal=PROP-001 | targets=reports/summary.md | at=" in (
        updated_task["background_run_worker_apply_accept_summary"]
    )
    assert not updated_task.get("background_run_worker_update_proposal_ids")
    assert not updated_task.get("background_run_worker_update_proposal_summary")


def test_dashboard_and_routes_gate_writing_apply_actions_when_quality_open(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    _persist_general_subagent_artifact(project_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "writing"
    task["background_run_task_contract_module_summary"] = "writing | writer/doc signals"
    task["followup_brief_status"] = "preview_only"
    task["followup_brief_summary"] = "preview_only | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=docs/handoff/final_handoff.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["docs/handoff/final_handoff.md"]
    task["background_run_worker_record_rows_summary"] = (
        "writing_record_rows | doc_row=docs/handoff/final_handoff.md|state=present | "
        "handoff_row=review|state=waiting|note=quality_open | quality_row=open|state=open|note=quality_open"
    )
    task["background_run_worker_record_rows"] = [
        "doc_row=docs/handoff/final_handoff.md|state=present",
        "handoff_row=review|state=waiting|note=quality_open",
        "quality_row=open|state=open|note=quality_open",
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    apply_labels = {"Preview Artifact Apply", "Propose Artifact Apply", "Accept Artifact Apply"}
    expected_followup_payload = '{"task_ref":"T-001","lane_ids":["R1"]}'

    assert task_detail is not None
    assert not any(btn.label in apply_labels for btn in runtime_card.runtime_safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_card.runtime_phase2_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_detail.active_task_safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_detail.active_task_phase2_action_buttons)
    assert not any(btn.label in apply_labels for btn in task_detail.safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in task_detail.phase2_action_buttons)
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in task_detail.safe_action_buttons
    )

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    _snapshot3, recovery = dashboard_state.load_dashboard_recovery_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    recovery_runtime = next(row for row in recovery.runtimes if row.project_alias == "O2")
    recovery_task = next(row for row in recovery_runtime.task_teams if row.request_id == "REQ-1")
    assert recovery_runtime.latest_subagent_gate_summary.startswith("subagent_gate=")
    assert recovery_runtime.active_task_general_subagent_gate_summary.startswith("subagent_gate=")
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in recovery_runtime.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in recovery_runtime.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in recovery_task.safe_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    propose_status, _propose_headers, propose_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-propose",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    preview_payload = json.loads(preview_body.decode("utf-8"))
    propose_payload = json.loads(propose_body.decode("utf-8"))

    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "writing_quality_open"
    assert preview_payload["next_step"] == "/followup T-001 lane R1"
    assert preview_payload["remediation"] == "close the document quality gate before applying writing changes"
    assert "writing_record_rows" in preview_payload["worker_record_rows"]
    assert "writing_apply_blocker | reason=writing_quality_open" in preview_payload["worker_blocker"]
    assert preview_payload["worker_recommended_action"] == "followup"
    assert preview_payload["worker_recommended_lane_ids"] == ["R1"]
    assert propose_status == 409
    assert propose_payload["outcome"]["reason_code"] == "writing_quality_open"
    assert propose_payload["next_step"] == "/followup T-001 lane R1"
    assert "quality_row=open|state=open" in propose_payload["worker_record_rows"]
    assert "quality_ready=open|state=blocked" in propose_payload["worker_preflight_rows"]


def test_dashboard_and_routes_derive_writing_blocker_rows_when_missing(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "writing"
    task["background_run_task_contract_module_summary"] = "writing | writer/doc signals"
    task["followup_brief_summary"] = "preview_only | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=docs/handoff/final_handoff.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["docs/handoff/final_handoff.md"]
    task["background_run_worker_result_status"] = "completed"
    task["background_run_worker_result_summary"] = "draft handoff prepared"
    task["background_run_worker_result_actions"] = ["refresh final handoff"]
    task["background_run_worker_result_cautions"] = ["quality polish needed before handoff"]
    task["background_run_worker_result_evidence_refs"] = ["docs/style-guide.md"]
    task["followup_brief_status"] = "preview_only"
    task.pop("background_run_worker_gate_status", None)
    task.pop("background_run_worker_gate_summary", None)
    task.pop("background_run_worker_profile_status", None)
    task.pop("background_run_worker_profile_summary", None)
    task.pop("background_run_worker_checklist_status", None)
    task.pop("background_run_worker_checklist_summary", None)
    task.pop("background_run_worker_items_summary", None)
    task.pop("background_run_worker_items", None)
    task.pop("background_run_worker_item_classes_summary", None)
    task.pop("background_run_worker_item_classes", None)
    task.pop("background_run_worker_records_summary", None)
    task.pop("background_run_worker_records", None)
    task.pop("background_run_worker_record_rows_summary", None)
    task.pop("background_run_worker_record_rows", None)
    task.pop("background_run_worker_preflight_summary", None)
    task.pop("background_run_worker_preflight_rows_summary", None)
    task.pop("background_run_worker_preflight_rows", None)
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    apply_labels = {"Preview Artifact Apply", "Propose Artifact Apply", "Accept Artifact Apply"}
    expected_followup_payload = '{"task_ref":"T-001","lane_ids":["R1"]}'

    assert task_detail is not None
    assert not any(btn.label in apply_labels for btn in runtime_card.runtime_safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_card.runtime_phase2_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_detail.active_task_safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in runtime_detail.active_task_phase2_action_buttons)
    assert not any(btn.label in apply_labels for btn in task_detail.safe_action_buttons)
    assert not any(btn.label in apply_labels for btn in task_detail.phase2_action_buttons)
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in task_detail.safe_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))

    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "writing_quality_open"
    assert preview_payload["next_step"] == "/followup T-001 lane R1"
    assert preview_payload["worker_recommended_action"] == "followup"
    assert preview_payload["worker_recommended_lane_ids"] == ["R1"]
    assert "writing_record_rows" in preview_payload["worker_record_rows"]
    assert "quality_row=open|state=open" in preview_payload["worker_record_rows"]
    assert "quality_ready=open|state=blocked" in preview_payload["worker_preflight_rows"]


def test_dashboard_surfaces_analysis_blocker_review_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "analysis"
    task["background_run_task_contract_module_summary"] = "analysis | analysis/review signals"
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/findings.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/findings.md"]
    task["background_run_worker_record_rows_summary"] = (
        "analysis_record_rows | finding_row=summary|state=stable | "
        "evidence_row=missing|state=missing|note=attach_evidence | gap_row=open|state=open|note=attach_evidence"
    )
    task["background_run_worker_record_rows"] = [
        "finding_row=summary|state=stable",
        "evidence_row=missing|state=missing|note=attach_evidence",
        "gap_row=open|state=open|note=attach_evidence",
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    expected_review_payload = '{"task_ref":"T-001","review_kind":"task_review"}'

    assert task_detail is not None
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in task_detail.safe_action_buttons
    )

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )
    _snapshot3, recovery = dashboard_state.load_dashboard_recovery_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    recovery_runtime = next(row for row in recovery.runtimes if row.project_alias == "O2")
    recovery_task = next(row for row in recovery_runtime.task_teams if row.request_id == "REQ-1")
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in recovery_runtime.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in recovery_runtime.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Analysis Blocker"
        and btn.path == "/control/actions/task/task-review"
        and btn.payload_json == expected_review_payload
        for btn in recovery_task.safe_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "analysis_evidence_missing"
    assert preview_payload["next_step"] == "/task T-001"
    assert preview_payload["worker_recommended_action"] == "task_review"

    review_status, _review_headers, review_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/task-review",
        body=json.dumps({"task_ref": "T-001", "review_kind": "task_review"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    review_payload = json.loads(review_body.decode("utf-8"))
    assert review_status == 200
    assert review_payload["status"] == "preview"
    assert review_payload["outcome"]["kind"] == "task_review"
    assert review_payload["outcome"]["reason_code"] == "analysis_evidence_missing"
    assert review_payload["next_step"] == "/task T-001"
    assert review_payload["worker_recommended_action"] == "task_review"
    assert "analysis_preflight_rows" in review_payload["worker_preflight_rows"]


def test_dashboard_surfaces_writing_execute_blocker_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "writing"
    task["background_run_task_contract_module_summary"] = "writing | writer/doc signals"
    task["followup_brief_status"] = "partially_executable"
    task["followup_brief_summary"] = "partially_executable | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=docs/handoff/final_handoff.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["docs/handoff/final_handoff.md"]
    task["background_run_worker_record_rows_summary"] = (
        "writing_record_rows | doc_row=docs/handoff/final_handoff.md|state=missing|note=document | "
        "handoff_row=ready|state=ready|note=handoff | quality_row=ready|state=ready|note=quality_gate"
    )
    task["background_run_worker_record_rows"] = [
        "doc_row=docs/handoff/final_handoff.md|state=missing|note=document",
        "handoff_row=ready|state=ready|note=handoff",
        "quality_row=ready|state=ready|note=quality_gate",
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    expected_execute_payload = '{"task_ref":"T-001","lane_ids":["L2"]}'

    assert task_detail is not None
    assert any(
        btn.label == "Resolve Writing Execute Blocker"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_execute_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Execute Blocker"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_execute_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Execute Blocker"
        and btn.path == "/control/actions/task/followup-execute"
        and btn.payload_json == expected_execute_payload
        for btn in task_detail.phase2_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "writing_doc_missing"
    assert preview_payload["next_step"] == "/followup-exec T-001 lane L2"
    assert preview_payload["worker_recommended_action"] == "followup_execute"
    assert preview_payload["worker_recommended_lane_ids"] == ["L2"]


def test_dashboard_surfaces_writing_review_lane_blockers_even_when_execute_available(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "writing"
    task["background_run_task_contract_module_summary"] = "writing | writer/doc signals"
    task["followup_brief_status"] = "partially_executable"
    task["followup_brief_summary"] = "partially_executable | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=docs/handoff/final_handoff.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["docs/handoff/final_handoff.md"]
    task["background_run_worker_record_rows_summary"] = (
        "writing_record_rows | doc_row=docs/handoff/final_handoff.md|state=present | "
        "handoff_row=review|state=waiting|note=quality_open | quality_row=open|state=open|note=quality_open"
    )
    task["background_run_worker_record_rows"] = [
        "doc_row=docs/handoff/final_handoff.md|state=present",
        "handoff_row=review|state=waiting|note=quality_open",
        "quality_row=open|state=open|note=quality_open",
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")
    expected_followup_payload = '{"task_ref":"T-001","lane_ids":["R1"]}'

    assert task_detail is not None
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Resolve Writing Blocker"
        and btn.path == "/control/actions/task/followup"
        and btn.payload_json == expected_followup_payload
        for btn in task_detail.safe_action_buttons
    )

    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/worker-apply-preview",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    assert preview_status == 409
    assert preview_payload["outcome"]["reason_code"] == "writing_quality_open"
    assert preview_payload["next_step"] == "/followup T-001 lane R1"
    assert preview_payload["worker_recommended_action"] == "followup"
    assert preview_payload["worker_recommended_lane_ids"] == ["R1"]
    assert preview_payload["remediation"] == "close the document quality gate before applying writing changes"


def test_offdesk_judge_prompt_includes_worker_blocker_context(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    entry = state["projects"]["alpha"]
    task = entry["tasks"]["REQ-1"]
    task["background_run_task_contract_module"] = "analysis"
    task["background_run_task_contract_module_summary"] = "analysis | analysis/review signals"
    task["background_run_worker_gate_summary"] = "state=evidence_missing | refs=0"
    task["background_run_worker_profile_summary"] = "analysis_findings_profile | findings=1 | evidence=0 | gaps=1"
    task["background_run_worker_checklist_summary"] = "analysis_checklist | findings=1,evidence=0,gaps=1 | next=attach_evidence"
    task["background_run_worker_record_rows_summary"] = (
        "analysis_record_rows | finding_row=summary|state=stable | "
        "evidence_row=missing|state=missing|note=attach_evidence | gap_row=open|state=open|note=attach_evidence"
    )
    task["background_run_worker_record_rows"] = [
        "finding_row=summary|state=stable",
        "evidence_row=missing|state=missing|note=attach_evidence",
        "gap_row=open|state=open|note=attach_evidence",
    ]
    task["background_run_worker_record_set_summary"] = "analysis_record_set | finding=1 | evidence=1 | gap=1"
    task["background_run_worker_record_set"] = [
        {"kind": "finding", "label": "summary", "state": "stable", "note": "action"},
        {"kind": "evidence", "label": "missing", "state": "missing", "note": "attach_evidence"},
        {"kind": "gap", "label": "evidence_missing", "state": "open", "note": "attach_evidence"},
    ]

    prompt = orch_task_handlers._offdesk_judge_prompt(entry, task, Path(entry["team_dir"]))

    assert '"worker_module": "analysis | analysis/review signals"' in prompt
    assert '"worker_record_set": "analysis_record_set | finding=1 | evidence=1 | gap=1"' in prompt
    assert '"worker_record_set_records": [' in prompt
    assert '"kind": "evidence"' in prompt
    assert '"label": "missing"' in prompt
    assert '"worker_record_rows": "analysis_record_rows | finding_row=summary|state=stable | evidence_row=missing|state=missing|note=attach_evidence | gap_row=open|state=open|note=attach_evidence"' in prompt
    assert '"worker_blocker": "analysis_apply_blocker | reason=analysis_evidence_missing' in prompt
    assert '"worker_blocked_rows": [' in prompt


def test_dashboard_surfaces_worker_apply_accept_summary_and_hides_apply_buttons(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_summary"] = (
        "state=applied | todo=TODO-002 | proposal=PROP-001 | targets=reports/summary.md | at=2026-04-10T10:06:00+09:00"
    )
    task["background_run_worker_apply_accept_proposal_id"] = "PROP-001"
    task["background_run_worker_apply_accept_todo_id"] = "TODO-002"
    task["background_run_worker_apply_accept_at"] = "2026-04-10T10:06:00+09:00"
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_worker_syncback_summary"] = (
        "state=applied | todo=TODO-002 | path=TODO.md | lines=14 | done=1 reopen=0 append=1 blocked=0 | at=2026-04-10T10:07:00+09:00"
    )
    task["background_run_worker_syncback_at"] = "2026-04-10T10:07:00+09:00"
    task.pop("background_run_worker_update_proposal_summary", None)
    task.pop("background_run_worker_update_proposal_ids", None)
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    task_detail = dashboard_state.load_task_detail(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        request_id="REQ-1",
    )

    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")

    assert runtime_card.active_task_background_run_worker_apply_accept_summary.startswith("state=applied | todo=TODO-002")
    assert runtime_card.active_task_background_run_worker_syncback_summary.startswith("state=applied | todo=TODO-002 | path=TODO.md")
    assert runtime_detail.active_task_background_run_worker_apply_accept_summary.startswith("state=applied | todo=TODO-002")
    assert runtime_detail.active_task_background_run_worker_syncback_summary.startswith(
        "state=applied | todo=TODO-002 | path=TODO.md"
    )
    assert task_detail is not None
    assert task_detail.background_run_worker_apply_accept_summary.startswith("state=applied | todo=TODO-002")
    assert task_detail.background_run_worker_syncback_summary.startswith("state=applied | todo=TODO-002 | path=TODO.md")

    blocked_labels = {"Preview Artifact Apply", "Propose Artifact Apply", "Accept Artifact Apply"}
    assert not any(btn.label in blocked_labels for btn in runtime_card.runtime_safe_action_buttons)
    assert not any(btn.label in blocked_labels for btn in runtime_card.runtime_phase2_action_buttons)
    assert not any(btn.label in blocked_labels for btn in runtime_detail.active_task_safe_action_buttons)
    assert not any(btn.label in blocked_labels for btn in runtime_detail.active_task_phase2_action_buttons)
    assert not any(btn.label in blocked_labels for btn in task_detail.safe_action_buttons)
    assert not any(btn.label in blocked_labels for btn in task_detail.phase2_action_buttons)
    syncback_labels = {"Preview Accepted Syncback", "Apply Accepted Syncback"}
    assert not any(btn.label in syncback_labels for btn in runtime_card.runtime_safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_card.runtime_phase2_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_detail.active_task_safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in runtime_detail.active_task_phase2_action_buttons)
    assert not any(btn.label in syncback_labels for btn in task_detail.safe_action_buttons)
    assert not any(btn.label in syncback_labels for btn in task_detail.phase2_action_buttons)


def test_dashboard_surfaces_apply_preview_and_accept_labels(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_update_stub_status"] = "ready"
    task["background_run_worker_update_stub_summary"] = "status=ready | target=reports/summary.md"
    task["background_run_worker_update_stub_targets"] = ["reports/summary.md"]
    task["background_run_worker_update_proposal_summary"] = "status=ready | apply_proposals=1 | ids=PROP-001 | targets=reports/summary.md"
    task["background_run_worker_update_proposal_ids"] = ["PROP-001"]
    state["projects"]["alpha"]["todo_proposals"] = [
        {
            "id": "PROP-001",
            "summary": "apply worker artifact update for T-001: reports/summary.md",
            "priority": "P2",
            "kind": "handoff",
            "status": "open",
            "source_request_id": "REQ-1",
            "created_by": "worker",
            "created_at": "2026-04-10T10:05:00+09:00",
            "updated_at": "2026-04-10T10:05:00+09:00",
        }
    ]
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    _snapshot2, runtime_details, _state = dashboard_state.load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    runtime_card = next(card for card in snapshot.runtime_cards if card.project_alias == "O2")
    runtime_detail = next(detail for detail in runtime_details if detail.project_alias == "O2")

    expected_apply_payload = '{"task_ref":"T-001"}'
    expected_accept_payload = '{"task_ref":"T-001","proposal_ref":"PROP-001"}'
    assert any(
        btn.label == "Preview Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-preview"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_card.runtime_safe_action_buttons
    )
    assert any(
        btn.label == "Accept Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-accept"
        and btn.payload_json == expected_accept_payload
        for btn in runtime_card.runtime_phase2_action_buttons
    )
    assert any(
        btn.label == "Preview Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-preview"
        and btn.payload_json == expected_apply_payload
        for btn in runtime_detail.active_task_safe_action_buttons
    )
    assert any(
        btn.label == "Accept Artifact Apply"
        and btn.path == "/control/actions/task/worker-apply-accept"
        and btn.payload_json == expected_accept_payload
        for btn in runtime_detail.active_task_phase2_action_buttons
    )


def test_control_dashboard_post_runtime_judge_route_executes_bound_review(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["tasks"]["REQ-1"]["background_run_task_contract_module"] = "analysis"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["background_run_task_contract_module_summary"] = "module=analysis | findings_evidence_gate"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["background_run_worker_record_set_summary"] = (
        "analysis_record_set | finding=1 | evidence=1 | gap=1"
    )
    state["projects"]["alpha"]["tasks"]["REQ-1"]["background_run_worker_record_set"] = [
        {"kind": "finding", "label": "summary", "state": "stable", "note": "action"},
        {"kind": "evidence", "label": "missing", "state": "missing", "note": "attach_evidence"},
        {"kind": "gap", "label": "evidence_missing", "state": "open", "note": "attach_evidence"},
    ]
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(
        runtime_exec.model_endpoint_adapter,
        "resolve_task_judge_binding",
        lambda *args, **kwargs: {"summary": "judge=codex_cli-gpt-5-4:gpt-5.4"},
    )
    monkeypatch.setattr(
        runtime_exec.model_provider_adapter,
        "invoke_task_judge_stub",
        lambda *args, **kwargs: {
            "ok": True,
            "executed": True,
            "summary": "endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed",
            "response_text": json.dumps(
                {
                    "verdict": "continue",
                    "confidence": "medium",
                    "reasoning": "brief executable",
                    "next_step": "/retry T-001",
                }
            ),
            "reason_code": "completed",
        },
    )

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/judge",
        body=json.dumps({"project_ref": "O2"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/orch judge O2"
    assert payload["binding"] == "judge=codex_cli-gpt-5-4:gpt-5.4"
    assert payload["latest_judge_decision"]["verdict"] == "continue"
    assert payload["latest_judge_decision"]["analysis_record_set"] == "analysis_record_set | finding=1 | evidence=1 | gap=1"
    assert payload["latest_judge_decision"]["analysis_record_set_records"][1]["kind"] == "evidence"
    row = action_audit.load_latest_action_audit_for_runtime_kind(team_dir, project_alias="O2", outcome_kind="offdesk_judge")
    assert row["headline"] == "Offdesk Judge | executed"
    assert row["outcome_detail"] == "endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed"
    decision = action_audit.load_latest_offdesk_judge_decision_for_runtime(team_dir, project_alias="O2")
    assert decision["analysis_record_set"] == "analysis_record_set | finding=1 | evidence=1 | gap=1"
    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_manual_step_execution_status"] == "executed"
    assert updated_task["background_run_manual_step_execution_summary"].startswith(
        "manual_review=/orch judge O2 | state=executed | next=/offdesk review O2 |"
    )


def test_control_dashboard_post_followup_execute_route_blocks_preview_only_brief(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_status"] = "preview_only"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_execution_lane_ids"] = ["L2"]
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_review_lane_ids"] = ["R1"]
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_reason"] = "operator must decide analysis handoff wording"
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup-execute",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L2"]}).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["status"] == "blocked"
    assert payload["error"] == "followup_execute_brief_required"
    assert payload["mode"] == "phase2"
    assert payload["source_command"] == "/followup-exec T-001 lane L2"
    assert payload["next_step"] == "/followup T-001"
    assert "safe preview only" in payload["remediation"]
    assert payload["task"]["followup_brief_status"] == "preview_only"
    assert payload["task"]["followup_brief_execution_lanes"] == "L2"
    assert payload["task"]["followup_brief_review_lanes"] == "R1"


def test_control_dashboard_post_followup_execute_route_runs_partially_executable_brief(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "partially_executable"
    task["followup_brief_summary"] = "partially_executable | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator must still approve the review wording"
    task["exec_critic"] = {
        "manual_followup_execution_lane_ids": ["L2"],
        "manual_followup_review_lane_ids": ["R1"],
        "reason": "operator must still approve the review wording",
    }
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    def _fake_handle_run_or_unknown_command(*, ctx, deps):
        assert ctx.run_control_mode == "followup"
        assert ctx.run_source_request_id == "REQ-1"
        assert ctx.run_selected_execution_lane_ids == ["L2"]
        assert ctx.run_selected_review_lane_ids == []
        deps.core.record_outcome(
            {
                "kind": "retry_run",
                "status": "executed",
                "reason_code": "followup_execution_started",
                "next_step": "/task T-001",
                "detail": "follow-up execution started from L2",
            }
        )
        return True

    monkeypatch.setattr(dashboard_app.run_handlers, "handle_run_or_unknown_command", _fake_handle_run_or_unknown_command)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup-execute",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L2"]}).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["status"] == "executed"
    assert payload["mode"] == "phase2"
    assert payload["source_command"] == "/followup-exec T-001 lane L2"
    assert payload["transition"]["run_control_mode"] == "followup"
    assert payload["transition"]["execution_lane_ids"] == ["L2"]
    assert payload["transition"]["review_lane_ids"] == []
    assert payload["outcome"]["kind"] == "followup_execute"
    assert payload["next_step"] == "/task T-001"
    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    updated_task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert updated_task["background_run_manual_step_execution_status"] == "executed"
    assert updated_task["background_run_manual_step_execution_summary"].startswith(
        "manual_execute=/followup-exec T-001 lane L2 | state=executed | next=/task T-001 |"
    )


def test_control_dashboard_post_followup_execute_route_uses_local_tmux_background_when_preferred(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["background_runner_target"] = "local_tmux"
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "partially_executable"
    task["followup_brief_summary"] = "partially_executable | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator keeps the review slice"
    task["exec_critic"] = {
        "manual_followup_execution_lane_ids": ["L2"],
        "manual_followup_review_lane_ids": ["R1"],
        "reason": "operator keeps the review slice",
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_now_iso", lambda: "2026-04-07T10:00:00+09:00")

    class _GatewayMain:
        @staticmethod
        def save_manager_state(path, state):
            Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(retry_exec, "_load_gateway_main_module", lambda: _GatewayMain)

    def _fake_launch(*, queue_path, ticket_id, runner_target="", now_iso, claimed_by="", source_surface="", launch_mode="offdesk_manual"):
        claimed = background_runs.claim_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            runner_target=runner_target or "local_tmux",
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        assert claimed["status"] == "dispatching"
        return background_runs.advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="running",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            runtime_handle="aoe_bg_followup_req_1",
            runtime_summary="tmux_session=aoe_bg_followup_req_1",
            evidence_bundle="status=running | outcome=tmux_session_started | session=aoe_bg_followup_req_1",
        )

    monkeypatch.setattr(retry_exec, "launch_background_ticket_via_adapter", lambda **kwargs: _fake_launch(**kwargs))

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup-execute",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L2"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/followup-exec T-001 lane L2"
    assert payload["background_run"]["runner_target"] == "local_tmux"
    assert payload["background_run"]["status"] == "running"
    assert payload["background_run"]["runtime_handle"] == "aoe_bg_followup_req_1"
    assert payload["background_run"]["model_plan"] == (
        "pack=followup_execute | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    assert payload["background_run"]["model_pack_profile"] == "followup_execute"
    assert payload["transition"]["run_control_mode"] == "followup"
    assert payload["next_step"] == "/orch status O2"

    updated = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    task = updated["projects"]["alpha"]["tasks"]["REQ-1"]
    assert task["background_run_model_pack_profile"] == "followup_execute"
    assert task["background_run_model_plan_summary"] == (
        "pack=followup_execute | worker=bg=unbound:qwen3-coder | judge=judge=unbound:claude-opus-4.1 | escalation=bgx=unbound:gpt-oss-or-gemma4"
    )
    queue_path = Path(updated["projects"]["alpha"]["team_dir"]) / "background_runs.json"
    rows = background_runs.load_background_runs_state(queue_path).get("runs") or []
    launched = [row for row in rows if str(row.get("ticket_id", "")).startswith("BGT-REQ-1-")]
    assert len(launched) == 1
    command_argv = list(launched[0]["launch_spec"].get("command_argv") or [])
    assert "--no-owner-only" in command_argv
    assert "--no-deny-by-default" in command_argv


def test_control_dashboard_post_action_route_appends_file_backed_audit_row(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["R1"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    audit_file = team_dir / "dashboard" / "action-history.jsonl"
    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    latest = rows[-1]

    assert status == 200
    assert payload["status"] == "preview"
    assert latest["headline"] == "Follow-up Preview | preview"
    assert latest["status"] == "preview"
    assert latest["next_step"] == "/task T-001"
    assert latest["link_label"] == "task detail"
    assert latest["link_href"] == "/control/tasks/by-request/REQ-1"
    assert latest["source_command"] == "/followup T-001 lane R1"


def test_control_dashboard_post_followup_execute_route_appends_blocked_audit_row(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_status"] = "preview_only"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_execution_lane_ids"] = ["L2"]
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_review_lane_ids"] = ["R1"]
    state["projects"]["alpha"]["tasks"]["REQ-1"]["followup_brief_reason"] = "operator must decide analysis handoff wording"
    gw.save_manager_state(manager_state_file, state)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup-execute",
        body=json.dumps({"task_ref": "T-001", "lane_ids": ["L2"]}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    audit_file = team_dir / "dashboard" / "action-history.jsonl"
    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    latest = rows[-1]

    assert status == 409
    assert payload["status"] == "blocked"
    assert latest["headline"] == "Follow-up Execute | blocked"
    assert latest["status"] == "blocked"
    assert latest["next_step"] == "/followup T-001"
    assert latest["source_command"] == "/followup-exec T-001 lane L2"


def test_control_dashboard_action_audit_prunes_old_and_excess_rows(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    audit_file = team_dir / "dashboard" / "action-history.jsonl"
    audit_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "at": "2000-01-01T00:00:00+00:00",
                        "headline": "Old Preview | preview",
                        "status": "preview",
                        "next_step": "/monitor O2",
                        "remediation": "-",
                        "link_label": "runtime detail",
                        "link_href": "/control/runtimes/O2",
                        "source_command": "/sync preview O2 24h",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "at": "2099-01-01T00:00:00+00:00",
                        "headline": "Kept Preview | preview",
                        "status": "preview",
                        "next_step": "/monitor O2",
                        "remediation": "-",
                        "link_label": "runtime detail",
                        "link_href": "/control/runtimes/O2",
                        "source_command": "/sync preview O2 24h",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS", "1")
    monkeypatch.setenv("AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS", "2")

    status, _headers, _body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup",
        body=json.dumps({"task_ref": "T-001"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert status == 200
    assert len(rows) == 2
    assert all(row["headline"] != "Old Preview | preview" for row in rows)
    assert rows[-1]["headline"] == "Follow-up Preview | preview"


def test_control_dashboard_action_audit_appends_concurrently_without_row_loss(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    original_loader = dashboard_app._load_existing_action_audit_rows

    def _slow_loader(path):
        rows = original_loader(path)
        time.sleep(0.01)
        return rows

    monkeypatch.setattr(dashboard_app, "_load_existing_action_audit_rows", _slow_loader)
    monkeypatch.setenv("AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS", "0")

    def _append(idx: int) -> None:
        dashboard_app._append_action_audit(
            config,
            {
                "path": "/control/actions/task/followup",
                "status": "preview",
                "source_command": f"/followup T-{idx:03d}",
                "next_step": f"/task T-{idx:03d}",
                "remediation": "-",
                "preview": {
                    "detail_path": f"/control/tasks/by-request/REQ-{idx:03d}",
                },
            },
        )

    audit_file = team_dir / "dashboard" / "action-history.jsonl"
    baseline_rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_append, range(1, 13)))

    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    appended_rows = [row for row in rows if row.get("source_command", "").startswith("/followup T-")]

    assert len(rows) == len(baseline_rows) + 12
    assert len(appended_rows) == 12
    assert {row["source_command"] for row in appended_rows} == {f"/followup T-{idx:03d}" for idx in range(1, 13)}


def test_control_dashboard_post_auto_recover_executes_with_default_force_false(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    monkeypatch.setattr(dashboard_app.management_handlers, "_tmux_auto_command", lambda args, action: (True, f"stub:{action}"))
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/auto-recover",
        body=b"{}",
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["executed"] is True
    assert payload["status"] == "executed"
    assert payload["source_command"] == "/auto recover"
    assert payload["payload"] == {"force": False}
    assert payload["next_step"] == "/auto status"
    assert "verify recovery grace" in payload["remediation"]
    assert payload["auto_state"]["enabled"] is True
    assert payload["auto_state"]["command"] == "next"
    assert payload["auto_state"]["recovery_grace_until"] != "-"
    assert payload["messages"][-1]["context"] == "auto-recover"
    assert payload["server_guard_pressure_kind"] == "codex"
    assert payload["planning_compact"].startswith("draft via codex, claude | review via codex, claude")
    assert payload["subagent_contract_summary"].startswith("general_research | profile=on_desk_plan")
    assert payload["general_subagent_executed"] is True
    assert payload["subagent_evidence_summary"].startswith("general_research | confidence=")
    assert payload["subagent_gate_summary"].startswith("subagent_gate=")
    assert "sources=" in payload["subagent_evidence_summary"]
    assert "findings=" in payload["subagent_evidence_summary"]
    assert "blocking=" in payload["subagent_evidence_summary"]
    assert payload["subagent_artifact_path"] == "harness_authoring/subagents/req-1-general-research.json"
    assert [row.get("label") for row in (payload.get("actions") or [])][:2] == [
        "Open Recovery",
        "Open Offdesk Prep",
    ]
    assert any(
        row.get("path") == "/control/actions/task/subagent-support-run"
        and row.get("payload_json") == '{"task_ref":"T-001"}'
        for row in (payload.get("actions") or [])
    )


def test_control_dashboard_post_auto_recover_skips_auto_subagent_when_process_pressure_is_dominant(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    monkeypatch.setattr(dashboard_app.management_handlers, "_tmux_auto_command", lambda args, action: (True, f"stub:{action}"))
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 980, "python": 20, "tmux": 10, "codex": 10})

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/auto-recover",
        body=b"{}",
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert payload["executed"] is True
    assert payload["server_guard_pressure_kind"] == "process"
    assert payload["general_subagent_executed"] is False
    assert payload["subagent_evidence_summary"] == "-"
    assert payload["subagent_gate_summary"] == "-"


def test_control_dashboard_post_background_queue_clean_marks_stale_tickets(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    queue_path = background_runs.background_runs_state_path(project_root / ".aoe-team")
    background_runs.upsert_background_run_ticket(
        queue_path,
        {
            "ticket_id": "BGT-STALE-1",
            "request_id": "REQ-STALE-1",
            "project_key": "alpha",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "dashboard-http",
            "source_surface": "offdesk",
            "status": "running",
            "created_at": "2026-03-16T07:00:00+09:00",
        },
        now_iso=lambda: "2026-03-16T07:00:00+09:00",
    )

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/background-queue-clean",
        body=b'{"project_ref":"O2"}',
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    summary = background_runs.summarize_background_runs_state(queue_path)

    assert status == 200
    assert payload["ok"] is True
    assert payload["implemented"] is True
    assert payload["executed"] is True
    assert payload["source_command"] == "/orch bgq-clean O2"
    assert payload["outcome"]["kind"] == "background_queue_cleanup"
    assert payload["outcome"]["reason_code"] == "stale_marked"
    assert payload["next_step"] == "/orch status O2"
    assert summary["stale_count"] >= 1


def test_control_dashboard_post_background_queue_clean_preview_returns_queue_state(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    queue_path = background_runs.background_runs_state_path(project_root / ".aoe-team")
    background_runs.upsert_background_run_ticket(
        queue_path,
        {
            "ticket_id": "BGT-STALE-PREVIEW",
            "request_id": "REQ-STALE-PREVIEW",
            "project_key": "alpha",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "dashboard-http",
            "source_surface": "offdesk",
            "status": "stale",
            "created_at": "2026-03-16T07:00:00+09:00",
        },
        now_iso=lambda: "2026-03-16T07:00:00+09:00",
    )

    before = background_runs.summarize_background_runs_state(queue_path)
    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/background-queue-clean-preview",
        body=b'{"project_ref":"O2"}',
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    after = background_runs.summarize_background_runs_state(queue_path)

    assert status == 200
    assert payload["status"] == "preview"
    assert payload["executed"] is False
    assert payload["source_command"] == "/orch bgq-clean O2 preview"
    assert payload["outcome"]["kind"] == "background_queue_cleanup_preview"
    assert payload["outcome"]["reason_code"] == "stale_present"
    assert payload["preview"]["before"]["stale_count"] >= 1
    assert before["stale_count"] == after["stale_count"]


def test_control_dashboard_post_server_guard_pressure_preview_returns_host_context(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"codex"}',
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    dashboard_app._append_action_audit(config, payload)
    overview_status, _overview_headers, overview_body = dashboard_app.build_dashboard_response("/control", config)
    health_status, _health_headers, health_body = dashboard_app.build_dashboard_response("/control/health", config)
    overview_text = overview_body.decode("utf-8")
    health = json.loads(health_body.decode("utf-8"))

    assert status == 200
    assert payload["status"] == "preview"
    assert payload["executed"] is False
    assert payload["source_command"] == "/ops pressure codex preview"
    assert payload["chat_id"] == "123456"
    assert payload["outcome"]["kind"] == "codex_process_pressure_preview"
    assert payload["next_step"] == "/control/chat"
    assert "codex_process_high" in " | ".join(payload["preview"]["matching_reasons"])
    assert payload["preview"]["process_summary"].startswith("total=")
    assert any(row.get("path") == "/control/actions/chat/session-update" for row in (payload.get("actions") or []))
    assert any("\"chat_id\":\"123456\"" in str(row.get("payload_json", "")) for row in (payload.get("actions") or []))
    assert any("\"default_mode\":\"direct\"" in str(row.get("payload_json", "")) for row in (payload.get("actions") or []))
    assert any(
        row.get("path") == "/control/actions/task/subagent-support-run"
        and row.get("payload_json") == '{"task_ref":"T-001"}'
        for row in (payload.get("actions") or [])
    )
    assert payload["planning_compact"].startswith("draft via codex, claude | review via codex, claude")
    assert payload["subagent_evidence_summary"] == "-"
    assert any(row.get("href") == "/control/chat?chat=123456&preset=global-direct" for row in (payload.get("links") or []))
    assert any(row.get("href") == "/control/history?q=codex&scope=control" for row in (payload.get("links") or []))
    assert any(row.get("href") == "/control/health/view" for row in (payload.get("links") or []))

    assert overview_status == 200
    assert "Preview Codex Pressure" in overview_text
    assert "live_preview_actions" in overview_text
    assert "live_preview_actions · Codex Pressure" in overview_text
    assert "No recent server guard preset thread yet." in overview_text
    assert "codex process pressure is elevated" in overview_text
    assert "server_guard_latest_result" in overview_text
    assert "Codex Pressure Preview | preview" in overview_text
    assert health_status == 200
    assert health["server_guard_latest_result_summary"].startswith("Codex Pressure Preview")
    assert "planning_compact=draft via" in health["server_guard_latest_result_summary"]
    assert any(
        row.get("path") == "/control/actions/runtime/server-guard-pressure-preview"
        and "\"pressure_kind\":\"codex\"" in str(row.get("payload_json", ""))
        for row in (health.get("server_guard", {}).get("recommended_actions") or [])
    )
    assert any(
        row.get("href") == "/control/chat?preset=global-direct"
        for row in (health.get("server_guard", {}).get("recommended_actions") or [])
    )
    assert any(
        row.get("href") == "/control/chat?preset=global-direct"
        and row.get("note") == "start with Chat, then keep Global Direct narrow"
        for row in (health.get("server_guard", {}).get("recommended_actions") or [])
    )
    assert any(
        row.get("href") == "/control/health/view"
        for row in (health.get("server_guard", {}).get("recommended_actions") or [])
    )


def test_control_dashboard_server_guard_preview_groups_follow_dominant_reason(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 420, "python": 120, "tmux": 3, "codex": 12})

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    assert "python_process_warn" in snapshot.control_summary.server_guard.reason_summary
    assert snapshot.control_summary.server_guard_preview_groups
    assert snapshot.control_summary.server_guard_preview_groups[0].key == "python"
    assert snapshot.control_summary.server_guard_preview_groups[0].label == "Python Pressure"
    assert snapshot.control_summary.server_guard_preview_groups[0].operator_sentence == "check host churn first, then revisit package and worker rails"
    assert snapshot.control_summary.server_guard_preview_groups[0].action_sentence == "start with Health, then keep Package Rail narrow"
    assert snapshot.control_summary.server_guard_preview_groups[0].focus_preset_label == "Package Rail"
    assert snapshot.control_summary.server_guard_preview_groups[0].priority_link_label == "Health"
    assert snapshot.control_summary.server_guard_preview_groups[0].priority_link_note == "check host churn first"


def test_control_dashboard_chat_live_preview_preset_follows_dominant_reason(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "dispatch",
            "pending_mode": "",
            "lang": "ko",
            "report_level": "normal",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 420, "python": 120, "tmux": 3, "codex": 12})

    _snapshot, chat_page = dashboard_state.load_dashboard_chat_page(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        selected_chat_id="123456",
    )

    assert chat_page.live_preview_preset_label == "Package Rail"
    assert chat_page.live_preview_preset_room == "O2/package"
    assert chat_page.live_preview_preset_default_mode == "dispatch"


def test_control_dashboard_server_guard_preset_apply_updates_latest_result_and_chat_timeline(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _persist_general_subagent_artifact(project_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"codex"}',
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    preset_payload = json.loads((preview_payload.get("actions") or [])[0]["payload_json"])

    apply_status, _apply_headers, apply_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/session-update",
        body=json.dumps(preset_payload).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    apply_payload = json.loads(apply_body.decode("utf-8"))
    dashboard_app._append_action_audit(config, apply_payload)
    overview_status, _overview_headers, overview_body = dashboard_app.build_dashboard_response("/control", config)
    chat_status, _chat_headers, chat_body = dashboard_app.build_dashboard_response("/control/chat?chat=123456", config)
    health_status, _health_headers, health_body = dashboard_app.build_dashboard_response("/control/health", config)
    overview_text = overview_body.decode("utf-8")
    chat_text = chat_body.decode("utf-8")
    health = json.loads(health_body.decode("utf-8"))

    assert preview_status == 200
    assert apply_status == 200
    assert apply_payload["focus_badge"] == "server-guard"
    assert apply_payload["server_guard_preset_label"] == "Apply Global Direct"
    assert apply_payload["next_step"] == "/control/chat?chat=123456&preset=global-direct"
    assert "room:O2/analysis->global" in apply_payload["chat_preset_diff_summary"]
    assert apply_payload["subagent_contract_summary"].startswith("general_research | profile=on_desk_plan")
    assert apply_payload["subagent_evidence_summary"] == "general_research | confidence=high | sources=2 | findings=2 | blocking=1"
    assert apply_payload["subagent_artifact_path"] == "harness_authoring/subagents/req-1-general-research.json"
    assert apply_payload["subagent_gate_summary"] == "subagent_gate=vendor notes still need a local delta check"
    assert apply_payload["general_subagent_executed"] is False
    assert [row.get("label") for row in (apply_payload.get("actions") or [])][:3] == [
        "Open Chat Console",
        "Open Server Guard Audit",
        "Open Health View",
    ]
    assert [row.get("priority") for row in (apply_payload.get("actions") or [])][:3] == [
        "primary",
        "secondary",
        "secondary",
    ]
    assert [row.get("pressure_kind_label") for row in (apply_payload.get("actions") or [])][:3] == [
        "Codex Pressure",
        "Codex Pressure",
        "Codex Pressure",
    ]
    action_notes = [row.get("note") for row in (apply_payload.get("actions") or [])][:3]
    assert action_notes[0].startswith("start with Chat, then keep Global Direct narrow")
    assert "dispatch unlocked after critic approval" in action_notes[0]
    assert action_notes[1:] == [
        "inspect the full server-guard action trail",
        "inspect host pressure after switching the chat rail",
    ]
    assert any(row.get("href") == "/control/health/view" for row in (apply_payload.get("actions") or []))
    assert any(row.get("href") == "/control/audit?focus=server-guard" for row in (apply_payload.get("actions") or []))
    assert any(
        row.get("path") == "/control/actions/task/subagent-support-run"
        and row.get("payload_json") == '{"task_ref":"T-001"}'
        for row in (apply_payload.get("actions") or [])
    )

    assert overview_status == 200
    assert "Apply Global Direct | completed" in overview_text
    assert "priority_link" in overview_text
    assert "action_copy" in overview_text
    assert "start with Chat, then keep Global Direct narrow" in overview_text
    assert "pressure-kind-badge" in overview_text
    assert "planning_compact: draft via" in overview_text
    assert "approved_plan=" in overview_text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in overview_text
    assert "subagent_gate: subagent_gate=vendor notes still need a local delta check" in overview_text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in overview_text
    assert chat_status == 200
    assert "Server Guard Preset Threads" in chat_text
    assert "Apply Global Direct | completed" in chat_text
    assert "chat_session" in chat_text
    assert ">123456<" in chat_text
    assert "preset_diff" in chat_text
    assert "/control/chat?chat=123456" in chat_text
    assert "/control/health/view" in chat_text
    assert "server-guard-preset:codex:123456:Apply Global Direct" in chat_text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in chat_text
    assert "subagent_gate: subagent_gate=vendor notes still need a local delta check" in chat_text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in chat_text
    assert health_status == 200
    assert health["server_guard_latest_result_summary"].startswith("Apply Global Direct | completed")
    assert "planning_compact=draft via" in health["server_guard_latest_result_summary"]
    assert "dispatch unlocked after critic approval" in health["server_guard_latest_result_summary"]
    assert "subagent_evidence=general_research | confidence=high | sources=2 | findings=2 | blocking=1" in health["server_guard_latest_result_summary"]

    audit_status, _audit_headers, audit_body = dashboard_app.build_dashboard_response("/control/audit?focus=server-guard&chat=123456&limit=20", config)
    recovery_status, _recovery_headers, recovery_body = dashboard_app.build_dashboard_response("/control/recovery", config)
    audit_text = audit_body.decode("utf-8")
    recovery_text = recovery_body.decode("utf-8")
    assert audit_status == 200
    assert "chat_filter" in audit_text
    assert "123456" in audit_text
    assert "Apply Global Direct | completed" in audit_text
    assert recovery_status == 200
    assert "Server Guard Preset Threads" in recovery_text
    assert "Apply Global Direct | completed" in recovery_text
    assert "/control/chat?chat=123456" in recovery_text
    assert "/control/health/view" in recovery_text
    assert "subagent_evidence: general_research | confidence=high | sources=2 | findings=2 | blocking=1" in recovery_text
    assert "subagent_gate: subagent_gate=vendor notes still need a local delta check" in recovery_text
    assert "subagent_artifact: harness_authoring/subagents/req-1-general-research.json" in recovery_text


def test_control_dashboard_runs_general_subagent_support_action_from_task(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    artifact_path = project_root / ".aoe-team" / "harness_authoring" / "subagents" / "req-1-general-research.json"
    assert not artifact_path.exists()

    status, headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/subagent-support-run",
        body=b'{"task_ref":"T-001"}',
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))
    task_text = dashboard_app.build_dashboard_response("/control/tasks/by-request/REQ-1", config)[2].decode("utf-8")
    runtime_text = dashboard_app.build_dashboard_response("/control/runtimes/O2", config)[2].decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    assert payload["status"] == "completed"
    assert payload["outcome"]["kind"] == "general_subagent_support"
    assert payload["outcome"]["reason_code"] == "artifact_written"
    assert payload["task"]["request_id"] == "REQ-1"
    assert payload["task"]["detail_path"] == "/control/tasks/by-request/REQ-1"
    assert payload["preview"]["runtime_path"] == "/control/runtimes/O2"
    assert payload["general_subagent_executed"] is True
    assert payload["subagent_contract_summary"].startswith("general_research | profile=on_desk_plan | backend=filesystem")
    assert payload["subagent_evidence_summary"].startswith("general_research | confidence=")
    assert payload["subagent_artifact_path"] == "harness_authoring/subagents/req-1-general-research.json"
    assert payload["planning_compact"].startswith("draft via codex, claude | review via codex, claude")
    assert "dispatch unlocked after critic approval" in payload["planning_compact"]
    assert payload["subagent_key_findings"]
    assert payload["subagent_artifact_refs"]
    assert artifact_path.exists()
    assert "subagent_contract" in task_text
    assert "general_research | profile=on_desk_plan | backend=filesystem" in task_text
    assert "subagent_evidence" in task_text
    assert "harness_authoring/subagents/req-1-general-research.json" in task_text
    assert "subagent_evidence" in runtime_text
    assert "harness_authoring/subagents/req-1-general-research.json" in runtime_text


def test_control_dashboard_recovery_surfaces_chat_session_on_compact_server_guard_history(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})

    executed_flags = []
    for pressure_kind in ("codex", "python"):
        preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
            "/control/actions/runtime/server-guard-pressure-preview",
            body=json.dumps({"pressure_kind": pressure_kind}).encode("utf-8"),
            content_type="application/json",
            config=config,
        )
        preview_payload = json.loads(preview_body.decode("utf-8"))
        dashboard_app._append_action_audit(config, preview_payload)
        preset_payload = json.loads((preview_payload.get("actions") or [])[0]["payload_json"])
        apply_status, _apply_headers, _apply_body = dashboard_app.build_dashboard_action_response(
            "/control/actions/chat/session-update",
            body=json.dumps(preset_payload).encode("utf-8"),
            content_type="application/json",
            config=config,
        )
        apply_payload = json.loads(_apply_body.decode("utf-8"))
        dashboard_app._append_action_audit(config, apply_payload)
        assert preview_status == 200
        assert apply_status == 200
        executed_flags.append(bool(apply_payload.get("general_subagent_executed")))
        assert apply_payload["subagent_evidence_summary"].startswith("general_research | confidence=")
        assert apply_payload["subagent_gate_summary"].startswith("subagent_gate=")
        assert "sources=" in apply_payload["subagent_evidence_summary"]
        assert "findings=" in apply_payload["subagent_evidence_summary"]
        assert "blocking=" in apply_payload["subagent_evidence_summary"]
        if pressure_kind == "python":
            assert [row.get("label") for row in (apply_payload.get("actions") or [])][:3] == [
                "Open Health View",
                "Open Chat Console",
                "Open Server Guard Audit",
            ]
            assert [row.get("priority") for row in (apply_payload.get("actions") or [])][:3] == [
                "primary",
                "secondary",
                "secondary",
            ]
            assert [row.get("pressure_kind_label") for row in (apply_payload.get("actions") or [])][:3] == [
                "Python Pressure",
                "Python Pressure",
                "Python Pressure",
            ]
            notes = [row.get("note") for row in (apply_payload.get("actions") or [])][:3]
            assert notes[0].startswith("start with Health, then keep Package Rail narrow")
            assert notes[1:] == [
                "inspect the selected chat session after applying the server-guard preset",
                "inspect the full server-guard action trail",
            ]
            assert [row.get("pressure_kind_label") for row in (apply_payload.get("actions") or [])][:3] == [
                "Python Pressure",
                "Python Pressure",
                "Python Pressure",
            ]
    assert any(executed_flags)

    recovery_status, _recovery_headers, recovery_body = dashboard_app.build_dashboard_response("/control/recovery", config)
    recovery_text = recovery_body.decode("utf-8")

    assert recovery_status == 200
    assert "Server Guard Preset Threads" in recovery_text
    assert "Apply Package Rail | completed" in recovery_text
    assert "Apply Global Direct | completed" in recovery_text
    assert "Codex Pressure" in recovery_text
    assert "Python Pressure" in recovery_text
    assert recovery_text.count("chat_session") >= 2
    assert recovery_text.count(">123456<") >= 2
    assert "/control/chat?chat=123456&amp;preset=package-rail" in recovery_text
    assert recovery_text.count(">Chat Console<") >= 1
    assert recovery_text.count(">Action Audit<") >= 1
    assert recovery_text.count(">Open Health View<") >= 1
    assert "server-guard-thread-highlight" in recovery_text
    assert "server-guard-thread-priority-note" in recovery_text
    assert "action_copy" in recovery_text
    assert "subagent_evidence" in recovery_text
    assert "subagent_gate" in recovery_text
    assert "general_research | confidence=" in recovery_text
    assert "start with Chat, then keep Global Direct narrow" in recovery_text
    assert "start with Health, then keep Package Rail narrow" in recovery_text


def test_control_dashboard_server_guard_preset_apply_skips_auto_subagent_for_process_pressure(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
            "selected_task_refs": {"active": "REQ-1"},
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 980, "python": 20, "tmux": 10, "codex": 10})

    preview_status, _preview_headers, preview_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"process"}',
        content_type="application/json",
        config=config,
    )
    preview_payload = json.loads(preview_body.decode("utf-8"))
    preset_payload = json.loads((preview_payload.get("actions") or [])[0]["payload_json"])

    apply_status, _apply_headers, apply_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/chat/session-update",
        body=json.dumps(preset_payload).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    apply_payload = json.loads(apply_body.decode("utf-8"))

    assert preview_status == 200
    assert apply_status == 200
    assert apply_payload["server_guard_pressure_kind"] == "process"
    assert apply_payload["general_subagent_executed"] is False
    assert apply_payload["subagent_evidence_summary"] == "-"
    assert apply_payload["subagent_gate_summary"] == "-"
    assert [row.get("label") for row in (apply_payload.get("actions") or [])][:3] == [
        "Open Server Guard Audit",
        "Open Health View",
        "Open Chat Console",
    ]


def test_control_dashboard_audit_and_recovery_surface_server_guard_latest_result(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    status, _headers, _body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"codex"}',
        content_type="application/json",
        config=config,
    )
    assert status == 200
    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    nightly_summary.write_nightly_session_summary(
        summary=summary,
        output_dir=team_dir / "recovery" / "nightly-session-summary",
        write_timestamped_copy=False,
    )

    audit_status, _audit_headers, audit_body = dashboard_app.build_dashboard_response("/control/audit?focus=server-guard", config)
    recovery_status, _recovery_headers, recovery_body = dashboard_app.build_dashboard_response("/control/recovery", config)
    audit_text = audit_body.decode("utf-8")
    recovery_text = recovery_body.decode("utf-8")

    assert audit_status == 200
    assert "server_guard_latest_action" in audit_text
    assert "server_guard_latest_result" in audit_text
    assert "Codex Pressure" in audit_text
    assert recovery_status == 200
    assert "server_guard_latest_action" in recovery_text
    assert "server_guard_latest_result" in recovery_text
    assert "Codex Pressure" in recovery_text


def test_control_dashboard_health_view_renders_operator_health_card(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 320, "python": 24, "tmux": 3, "codex": 75})
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    status, headers, body = dashboard_app.build_dashboard_response("/control/health/view", config)
    text = body.decode("utf-8")

    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert "Host Health View" in text
    assert "Server Guard Health Card" in text
    assert "Health Summary" in text
    assert "Recommended Actions" in text
    assert "latest_action" in text
    assert "latest_result" in text
    assert "focus_preset" in text
    assert "priority_link" in text
    assert "operator_sentence" in text
    assert "action_copy" in text
    assert "trim chat fanout first, then widen operator surfaces" in text
    assert "start with Chat, then keep Global Direct narrow" in text
    assert "Open Health JSON" in text


def test_control_dashboard_server_guard_pressure_links_are_preset_specific(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    state["chat_sessions"] = {
        "123456": {
            "updated_at": "2026-04-15T11:10:00+09:00",
            "default_mode": "on",
            "pending_mode": "direct",
            "lang": "ko",
            "report_level": "full",
            "room": "O2/analysis",
        }
    }
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 700, "python": 90, "tmux": 25, "codex": 10})
    py_status, _py_headers, py_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"python"}',
        content_type="application/json",
        config=config,
    )
    py_payload = json.loads(py_body.decode("utf-8"))
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 950, "python": 20, "tmux": 65, "codex": 10})
    tmux_status, _tmux_headers, tmux_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"tmux"}',
        content_type="application/json",
        config=config,
    )
    tmux_payload = json.loads(tmux_body.decode("utf-8"))
    monkeypatch.setattr(server_guard, "_proc_counts", lambda: {"total": 980, "python": 20, "tmux": 10, "codex": 10})
    proc_status, _proc_headers, proc_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/server-guard-pressure-preview",
        body=b'{"pressure_kind":"process"}',
        content_type="application/json",
        config=config,
    )
    proc_payload = json.loads(proc_body.decode("utf-8"))

    assert py_status == 200
    assert any(row.get("href") == "/control/chat?chat=123456&preset=package-rail" for row in (py_payload.get("links") or []))
    assert any("\"room\":\"O2/package\"" in str(row.get("payload_json", "")) for row in (py_payload.get("actions") or []))
    assert tmux_status == 200
    assert any(row.get("href") == "/control/chat?chat=123456&preset=review-rail" for row in (tmux_payload.get("links") or []))
    assert any("\"room\":\"O2/review\"" in str(row.get("payload_json", "")) for row in (tmux_payload.get("actions") or []))
    assert proc_status == 200
    assert any(row.get("href") == "/control/chat?chat=123456&preset=analysis-rail" for row in (proc_payload.get("links") or []))
    assert any("\"room\":\"O2/analysis\"" in str(row.get("payload_json", "")) for row in (proc_payload.get("actions") or []))


def test_control_dashboard_overview_surfaces_server_guard_cleanup_preview_action(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, project_root = _build_runtime(control_root)
    queue_path = background_runs.background_runs_state_path(project_root / ".aoe-team")
    background_runs.upsert_background_run_ticket(
        queue_path,
        {
            "ticket_id": "BGT-STALE-OVERVIEW",
            "request_id": "REQ-STALE-OVERVIEW",
            "project_key": "alpha",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "dashboard-http",
            "source_surface": "offdesk",
            "status": "stale",
            "created_at": "2026-03-16T07:00:00+09:00",
        },
        now_iso=lambda: "2026-03-16T07:00:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    overview_status, _headers, overview_body = dashboard_app.build_dashboard_response("/control", config)
    health_status, _health_headers, health_body = dashboard_app.build_dashboard_response("/control/health", config)
    overview_text = overview_body.decode("utf-8")
    health = json.loads(health_body.decode("utf-8"))

    assert overview_status == 200
    assert "Preview Queue Cleanup" in overview_text
    assert "/control/actions/runtime/background-queue-clean-preview" in overview_text
    assert health_status == 200
    assert any(
        row.get("path") == "/control/actions/runtime/background-queue-clean-preview"
        for row in (health.get("server_guard", {}).get("recommended_actions") or [])
    )


def test_control_dashboard_post_auto_recover_blocked_includes_retry_at_remediation(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    def _fake_handle_scheduler_control_command(**kwargs):
        send = kwargs["send"]
        kwargs["record_outcome"](
            {
                "kind": "auto_recover",
                "status": "blocked",
                "reason_code": "provider_capacity_blocked",
                "next_step": "/offdesk review",
                "detail": "next_retry_at=2026-03-16T10:30:00+09:00",
            }
        )
        send("auto recovery blocked", context="auto-recover-blocked")
        return True

    monkeypatch.setattr(dashboard_app.scheduler_control_handlers, "handle_scheduler_control_command", _fake_handle_scheduler_control_command)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/auto-recover",
        body=b"{}",
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["status"] == "blocked"
    assert payload["next_step"] == "/offdesk review"
    assert payload["outcome"]["reason_code"] == "provider_capacity_blocked"
    assert "retry_at=2026-03-16T10:30:00+09:00" in payload["remediation"]


def test_control_dashboard_post_auto_recover_requires_structured_outcome(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    def _fake_handle_scheduler_control_command(**kwargs):
        send = kwargs["send"]
        send("legacy auto recover message", context="auto-recover")
        return True

    monkeypatch.setattr(dashboard_app.scheduler_control_handlers, "handle_scheduler_control_command", _fake_handle_scheduler_control_command)

    status, _headers, body = dashboard_app.build_dashboard_action_response(
        "/control/actions/control/auto-recover",
        body=b"{}",
        content_type="application/json",
        config=config,
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 500
    assert payload["status"] == "contract_missing"
    assert payload["outcome"]["reason_code"] == "outcome_missing"
    assert "structured outcome rows" in payload["remediation"]


def test_control_dashboard_runtime_detail_renders_background_queue_cleanup_button_and_sorts_urgent_runtime_first(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    manager_state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    beta_root = control_root / "Beta"
    beta_team_dir = beta_root / ".aoe-team"
    beta_team_dir.mkdir(parents=True, exist_ok=True)
    (beta_team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (beta_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (beta_team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    manager_state["projects"]["beta"] = {
        "name": "beta",
        "display_name": "Beta",
        "project_alias": "O3",
        "project_root": str(beta_root),
        "team_dir": str(beta_team_dir),
        "overview": "runtime beta",
        "last_request_id": "",
        "tasks": {},
        "task_alias_index": {},
        "task_seq": 0,
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
        "system_project": False,
        "ops_hidden": False,
        "paused": False,
        "last_sync_at": "2026-03-16T09:20:00+09:00",
        "last_sync_mode": "scenario",
        "created_at": "2026-03-16T09:00:00+09:00",
        "updated_at": "2026-03-16T09:30:00+09:00",
    }
    manager_state_file.write_text(json.dumps(manager_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    background_runs.upsert_background_run_ticket(
        background_runs.background_runs_state_path(beta_team_dir),
        {
            "ticket_id": "BGT-BETA-1",
            "request_id": "REQ-BETA-1",
            "project_key": "beta",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "dashboard-http",
            "source_surface": "offdesk",
            "status": "stale",
            "created_at": "2026-03-16T09:00:00+09:00",
        },
        now_iso=lambda: "2026-03-16T09:00:00+09:00",
    )
    background_runs.upsert_background_run_ticket(
        background_runs.background_runs_state_path((control_root / "Alpha" / ".aoe-team")),
        {
            "ticket_id": "BGT-ALPHA-1",
            "request_id": "REQ-ALPHA-1",
            "project_key": "alpha",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "dashboard-http",
            "source_surface": "offdesk",
            "status": "running",
            "created_at": "2026-03-16T09:45:00+09:00",
        },
        now_iso=lambda: "2026-03-16T09:45:00+09:00",
    )
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    snapshot = dashboard_state.load_dashboard_snapshot(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    status, _headers, body = dashboard_app.build_dashboard_response("/control/runtimes/O3", config)
    text = body.decode("utf-8")

    assert [card.project_alias for card in snapshot.runtime_cards[:2]] == ["O3", "O2"]
    assert status == 200
    assert "/control/actions/runtime/background-queue-clean" in text
    assert "Background Queue Cleanup" in text


def test_execute_retry_run_transition_prefers_recorded_outcome_contract(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    paths, manager_state = dashboard_app._load_dashboard_manager_state(config)
    task = manager_state["projects"]["alpha"]["tasks"]["REQ-1"]
    _mark_task_planning_gate_blocked(task)
    task["job_contract_status"] = "blocked"
    task["job_contract_planning_mode"] = "standard"
    task["job_contract_summary"] = "status=blocked | plan=standard | scope=0 | checks=0 | artifacts=0"
    task["debug_packet_state"] = "blocked"
    task["debug_packet_summary"] = "state=blocked | symptom=execution_brief_blocked | evidence=1 | next=/offdesk review"
    task["debug_packet_symptom"] = "execution_brief_blocked"
    task["debug_packet_failed_attempt"] = "critic=planning_gate"
    task["debug_packet_next_step"] = "/offdesk review"
    task["phase_checkpoint_status"] = "blocked"
    task["phase_checkpoint_current_phase"] = "plan"
    task["phase_checkpoint_summary"] = "status=blocked | current=plan | plan=blocked|note=contract_gap"
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Offdesk Judge",
        status="executed",
        outcome_kind="offdesk_judge",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed",
        next_step="/offdesk review O2",
        remediation="-",
        source_command="/orch judge O2",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T10:05:00+09:00",
        extra={
            "response_text": "{\"verdict\":\"continue\",\"confidence\":\"medium\",\"reasoning\":\"brief executable\",\"next_step\":\"/retry T-001\",\"caution\":\"review lane remains\"}",
        },
    )

    def _fake_handle_run_or_unknown_command(*, ctx, deps):
        deps.core.record_outcome(
            {
                "kind": "retry_run",
                "status": "blocked",
                "reason_code": "planning_gate",
                "next_step": "/offdesk review",
                "detail": "critic issues remain after auto-replan",
            }
        )
        deps.core.send("unrelated body", context="result")
        return True

    monkeypatch.setattr(dashboard_app.run_handlers, "handle_run_or_unknown_command", _fake_handle_run_or_unknown_command)

    status, _headers, body = dashboard_app._execute_retry_run_transition(
        {
            "cmd": "run",
            "rest": "",
            "orch_target": "alpha",
            "run_prompt": "retry it",
            "run_force_mode": "dispatch",
            "run_control_mode": "retry",
            "run_source_request_id": "REQ-1",
            "run_source_task": {"request_id": "REQ-1"},
            "run_selected_execution_lane_ids": ["L1"],
            "run_selected_review_lane_ids": ["R1"],
        },
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command="/retry T-001 lane L1,R1",
        payload={"task_ref": "T-001", "lane_ids": ["L1", "R1"]},
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 409
    assert payload["status"] == "blocked"
    assert payload["outcome"]["reason_code"] == "planning_gate"
    assert payload["next_step"] == "/orch judge O2"
    assert "/orch judge" in payload["remediation"]
    assert "approval blockers" in payload["remediation"]
    assert "latest judge: Offdesk Judge" in payload["remediation"]
    assert "endpoint=claude_code_cli-opus provider=claude_code_cli model=opus status=completed" in payload["remediation"]
    assert payload["latest_judge"]["headline"] == "Offdesk Judge"
    assert payload["latest_judge"]["detail"].startswith("endpoint=claude_code_cli-opus")
    assert payload["latest_judge_decision"]["verdict"] == "continue"
    assert payload["latest_judge_decision"]["recommended_action"] == "retry"
    assert payload["latest_judge_decision_bridge"]["applied"] is False
    assert payload["job_contract"].startswith("status=blocked")
    assert payload["debug_packet"].startswith("state=blocked")
    assert payload["phase_checkpoint"].startswith("status=blocked")
    assert payload["replan_auto_decision"] == {}
    assert payload["replan_auto_routing_policy"] == {}


def test_execute_retry_run_transition_requires_structured_outcome(tmp_path: Path, monkeypatch) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )
    paths, manager_state = dashboard_app._load_dashboard_manager_state(config)

    def _fake_handle_run_or_unknown_command(*, ctx, deps):
        deps.core.send("legacy body without outcome", context="run-dispatched")
        return True

    monkeypatch.setattr(dashboard_app.run_handlers, "handle_run_or_unknown_command", _fake_handle_run_or_unknown_command)

    status, _headers, body = dashboard_app._execute_retry_run_transition(
        {
            "cmd": "run",
            "rest": "",
            "orch_target": "alpha",
            "run_prompt": "retry it",
            "run_force_mode": "dispatch",
            "run_control_mode": "retry",
            "run_source_request_id": "REQ-1",
            "run_source_task": {"request_id": "REQ-1"},
        },
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command="/retry T-001",
        payload={"task_ref": "T-001"},
    )
    payload = json.loads(body.decode("utf-8"))

    assert status == 500
    assert payload["status"] == "contract_missing"
    assert payload["outcome"]["reason_code"] == "outcome_missing"
    assert "structured outcome rows" in payload["remediation"]


def test_control_dashboard_post_safe_action_route_returns_404_for_unknown_target(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    followup_status, _followup_headers, followup_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/followup",
        body=json.dumps({"task_ref": "T-999"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )
    sync_status, _sync_headers, sync_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/runtime/sync-preview",
        body=json.dumps({"project_ref": "OX"}).encode("utf-8"),
        content_type="application/json",
        config=config,
    )

    followup_payload = json.loads(followup_body.decode("utf-8"))
    sync_payload = json.loads(sync_body.decode("utf-8"))

    assert followup_status == 404
    assert followup_payload["error"] == "not_found"
    assert "task not found" in followup_payload["message"]

    assert sync_status == 404
    assert sync_payload["error"] == "not_found"
    assert "runtime not found" in sync_payload["message"]


def test_control_dashboard_post_action_route_rejects_invalid_payload_and_content_type(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    bad_status, _bad_headers, bad_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=b'{"lane_ids":["L1"]}',
        content_type="application/json",
        config=config,
    )
    type_status, _type_headers, type_body = dashboard_app.build_dashboard_action_response(
        "/control/actions/task/retry",
        body=b"task_ref=T-001",
        content_type="text/plain",
        config=config,
    )

    bad_payload = json.loads(bad_body.decode("utf-8"))
    type_payload = json.loads(type_body.decode("utf-8"))

    assert bad_status == 400
    assert bad_payload["error"] == "bad_request"
    assert "task_ref is required" in bad_payload["message"]

    assert type_status == 415
    assert type_payload["error"] == "unsupported_media_type"


def test_control_dashboard_post_non_action_routes_return_405_or_404(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    config = dashboard_app.DashboardAppConfig(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        host="127.0.0.1",
        port=8765,
    )

    method_status, method_headers, method_body = dashboard_app.build_dashboard_action_response(
        "/control",
        body=b"{}",
        content_type="application/json",
        config=config,
    )
    missing_status, _missing_headers, missing_body = dashboard_app.build_dashboard_action_response(
        "/control/missing",
        body=b"{}",
        content_type="application/json",
        config=config,
    )

    method_payload = json.loads(method_body.decode("utf-8"))
    missing_payload = json.loads(missing_body.decode("utf-8"))

    assert method_status == 405
    assert method_headers["Allow"] == "GET"
    assert method_payload["error"] == "method_not_allowed"

    assert missing_status == 404
    assert missing_payload["error"] == "not_found"
