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
    assert "control_intent_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "execution=L1 | review=R1" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text
    assert "/monitor O2" in text
    assert "/retry T-001" in text
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
    assert "control_intent_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "evidence quality, reasoning coherence, missing caveats" in text
    assert "analysis-check" in text
    assert "analysis-followup" in text
    assert "/monitor O2" in text
    assert "/todo O2" in text
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
    assert "latest_intent_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
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
