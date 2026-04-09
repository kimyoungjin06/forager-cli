#!/usr/bin/env python3
"""Read-only Control Dashboard regressions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
DASH_DIR = ROOT / "scripts" / "dashboard"
for path in (GW_DIR, DASH_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from _gateway_test_support import gw  # noqa: E402
import aoe_tg_background_runs as background_runs  # noqa: E402
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter  # noqa: E402
import aoe_tg_operator_summary as operator_summary  # noqa: E402
from aoe_tg_request_contract import build_background_run_ticket  # noqa: E402
import aoe_tg_runtime_read as runtime_read  # noqa: E402
import control_dashboard as dashboard_app  # noqa: E402
import control_dashboard_action_exec_retry as retry_exec  # noqa: E402
import control_dashboard_state as dashboard_state  # noqa: E402
import nightly_session_summary as nightly_summary  # noqa: E402
import aoe_tg_document_registry as document_registry  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


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
    assert "Action Audit" in overview_text
    assert "O2 Alpha" in overview_text
    assert "next_retry_target" in overview_text
    assert "state_root_mode" in overview_text
    assert "legacy" in overview_text
    assert str(team_dir.resolve()) in overview_text
    assert "context_pack" in overview_text
    assert "model_plan" in overview_text
    assert "latest_intent_command" in overview_text
    assert "offdesk_prepare" in overview_text
    assert "selected=offdesk_prepare" in overview_text
    assert "execution_brief" in overview_text
    assert "underspecified" in overview_text
    assert "brief_summary" in overview_text
    assert "blocked=acceptance_gap" in overview_text
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
    assert "action-history-badge" in overview_text
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


def test_control_dashboard_audit_route_renders_recent_file_backed_actions(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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
    assert "Sync Preview | preview" in text
    assert "/sync preview O2 24h" in text
    assert "/control/runtimes/O2" in text


def test_control_dashboard_history_route_renders_query_results(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
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


def test_control_dashboard_task_detail_route_redirects_alias_to_request_id(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "preview_only"
    task["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator must decide the analysis handoff wording"
    gw.save_manager_state(manager_state_file, state)
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
    assert "context_pack_docs" in text
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
    state = gw.load_manager_state(manager_state_file, control_root, team_dir)
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["followup_brief_status"] = "preview_only"
    task["followup_brief_summary"] = "preview_only | execution=L2 | review=R1"
    task["followup_brief_execution_lane_ids"] = ["L2"]
    task["followup_brief_review_lane_ids"] = ["R1"]
    task["followup_brief_reason"] = "operator must decide the analysis handoff wording"
    gw.save_manager_state(manager_state_file, state)
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
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=none" in text
    assert "bg=running/local_background" in text
    assert "execution_brief_summary" in text
    assert "underspecified=1" in text
    assert "background_run_summary" in text
    assert "status running=1" in text
    assert "background_scheduler" in text
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
    assert "reentry_rails" in text
    assert "retry=blocked:underspecified exec=L1 review=R1" in text
    assert "followup=none" in text
    assert "bg=running/local_background" in text
    assert "state_root_mode" in text
    assert str(team_dir.resolve()) in text
    assert "latest_intent_command" in text
    assert "offdesk" in text
    assert "offdesk_prepare" in text
    assert "selected=offdesk_prepare" in text
    assert "first_focus" in text
    assert "오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in text
    assert "execution_brief_summary" in text
    assert "underspecified=1" in text
    assert "background_run_summary" in text
    assert "status running=1" in text
    assert "background_scheduler" in text
    assert "background_scheduler_note" in text
    assert "no queued scheduler head" in text
    assert "context_pack" in text
    assert "model_plan" in text
    assert "obs stale=" in text
    assert "waiting on execution lane(s): L1" in text
    assert "overlapping files: reports/summary.md" in text
    assert "obs_files" in text
    assert "touched=3 conflicts=1" in text
    assert "/control/actions/control/auto-recover" in text
    assert "Auto Recover" in text
    assert "Auto Recover Force" in text
    assert "/control/actions/runtime/sync-preview" in text
    assert "/control/actions/task/followup" in text
    assert "/control/actions/task/retry" in text
    assert "action-section" in text
    assert "/control/tasks/by-request/REQ-1" in text
    assert "/monitor O2" in text
    assert "/task T-001" in text
    assert "/request REQ-1" in text
    assert "/retry T-001" in text
    assert "background_run" in text
    assert "BGT-001" in text
    assert "local_background" in text
    assert "run_lock" in text
    assert "open" in text
    assert "background_slots" in text
    assert "active=0 limit=1" in text
    assert "idle (0/1)" in text


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

    assert sync_status == 200
    assert sync_payload["ok"] is True
    assert sync_payload["implemented"] is True
    assert sync_payload["mode"] == "safe"
    assert sync_payload["source_command"] == "/sync preview O2 48h"
    assert sync_payload["payload"] == {"project_ref": "O2", "window": "48h"}
    assert sync_payload["next_step"] == "/offdesk review O2"
    assert "inspect sync drift" in sync_payload["remediation"]
    assert sync_payload["preview"]["kind"] == "runtime_sync_preview"
    assert sync_payload["preview"]["project_alias"] == "O2"
    assert "quality=" in sync_payload["preview"]["sync_summary"]


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
