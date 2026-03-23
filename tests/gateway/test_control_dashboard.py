#!/usr/bin/env python3
"""Read-only Control Dashboard regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
DASH_DIR = ROOT / "scripts" / "dashboard"
for path in (GW_DIR, DASH_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _gateway_test_support import gw  # noqa: E402
import aoe_tg_runtime_read as runtime_read  # noqa: E402
import control_dashboard as dashboard_app  # noqa: E402
import control_dashboard_state as dashboard_state  # noqa: E402
import nightly_session_summary as nightly_summary  # noqa: E402


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
                    "updated_at": "2026-03-16T10:00:00+09:00",
                    "created_at": "2026-03-16T09:55:00+09:00",
                    "plan": {
                        "summary": "analysis plan",
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
                    "lane_states": {
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


def test_runtime_read_matches_gateway_wrapper_state(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)

    via_gateway = gw.load_manager_state(manager_state_file, control_root, team_dir)
    via_runtime_read = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)

    assert via_runtime_read == via_gateway
    assert via_runtime_read["projects"]["alpha"]["tasks"]["REQ-1"]["phase1_role_preset"] == "analysis"


def test_control_dashboard_overview_and_tasks_routes_render_structured_state(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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
    assert "O2 Alpha" in overview_text
    assert "next_retry_target" in overview_text
    assert "latest_intent_command" in overview_text
    assert "offdesk_prepare" in overview_text
    assert "selected=offdesk_prepare" in overview_text
    assert "Action Result" in overview_text
    assert "Raw Payload" in overview_text
    assert "action-result-rows" in overview_text
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


def test_control_dashboard_task_detail_route_redirects_alias_to_request_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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
    assert "control_intent_action" in text
    assert "offdesk_prepare" in text
    assert "first_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "execution=L1 | review=R1" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text
    assert "/monitor O2" in text
    assert "phase2_actions" in text
    assert "/retry T-001" in text
    assert "Follow-up Preview" in text
    assert "/control/actions/task/followup" in text
    assert "/control/actions/task/retry" in text
    assert "data-dashboard-action" in text
    assert "data-action-confirm=\"true\"" in text
    assert "/offdesk review" in text


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
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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
    assert "next=/retry T-001" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "analysis-check" in text
    assert "analysis-followup" in text
    assert "/control/actions/runtime/sync-preview" in text
    assert "Sync Preview (24h)" in text
    assert "/control/actions/task/followup" in text
    assert "/control/actions/task/retry" in text
    assert "/monitor O2" in text
    assert "/todo O2" in text
    assert "phase2_actions" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text


def test_control_dashboard_recovery_route_renders_latest_nightly_summary(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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
    assert "Nightly Session Summary" in text
    assert "automation_posture" in text
    assert "auto_active (fanout)" in text
    assert "O2 Alpha" in text
    assert "analysis-check" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "latest_intent_command" in text
    assert "offdesk" in text
    assert "offdesk_prepare" in text
    assert "selected=offdesk_prepare" in text
    assert "first_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "/control/actions/control/auto-recover" in text
    assert "Auto Recover" in text
    assert "/control/actions/runtime/sync-preview" in text
    assert "/control/actions/task/followup" in text
    assert "/control/actions/task/retry" in text
    assert "phase2_actions" in text
    assert "/control/tasks/by-request/REQ-1" in text
    assert "/monitor O2" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text
    assert "/retry T-001" in text


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
    assert paths.gateway_events_file == (custom_team_dir / "logs" / "gateway_events.jsonl").resolve()


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
    assert followup_payload["preview"]["kind"] == "task_followup"
    assert followup_payload["preview"]["project_alias"] == "O2"
    assert followup_payload["preview"]["request_id"] == "REQ-1"
    assert followup_payload["preview"]["detail_path"] == "/control/tasks/by-request/REQ-1"

    assert sync_status == 200
    assert sync_payload["ok"] is True
    assert sync_payload["implemented"] is True
    assert sync_payload["mode"] == "safe"
    assert sync_payload["source_command"] == "/sync preview O2 48h"
    assert sync_payload["payload"] == {"project_ref": "O2", "window": "48h"}
    assert sync_payload["preview"]["kind"] == "runtime_sync_preview"
    assert sync_payload["preview"]["project_alias"] == "O2"
    assert "quality=" in sync_payload["preview"]["sync_summary"]


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
    assert payload["auto_state"]["enabled"] is True
    assert payload["auto_state"]["command"] == "next"
    assert payload["auto_state"]["recovery_grace_until"] != "-"
    assert payload["messages"][-1]["context"] == "auto-recover"


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
