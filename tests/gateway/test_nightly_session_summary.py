#!/usr/bin/env python3
"""Nightly session summary artifact regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
DASH_DIR = ROOT / "scripts" / "dashboard"
TEST_DIR = ROOT / "tests" / "gateway"
for path in (GW_DIR, DASH_DIR, TEST_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import aoe_tg_action_audit as action_audit  # noqa: E402
import nightly_session_summary as nightly_summary  # noqa: E402
from test_control_dashboard import _build_runtime  # noqa: E402


def test_build_nightly_session_summary_uses_runtime_state_contract(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_worker_syncback_summary"] = (
        "state=applied | todo=TODO-002 | path=TODO.md | lines=14 | done=1 reopen=0 append=1 blocked=0 | at=2026-04-09T11:07:00+09:00"
    )
    task["background_run_worker_syncback_at"] = "2026-04-09T11:07:00+09:00"
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
        at="2026-04-09T11:10:00+09:00",
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
        at="2026-04-09T11:13:00+09:00",
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
            "planning_handoff": {
                "job_contract": {
                    "status": "blocked",
                    "planning_mode": "standard",
                    "summary": "status=blocked | plan=standard | scope=0 | checks=0 | artifacts=0",
                },
                "approved_plan": {
                    "status": "blocked",
                    "summary": "approved_plan=blocked | subtasks=0 | reviews=1 | issue=contract_gap",
                },
                "debug_packet": {
                    "state": "blocked",
                    "summary": "state=blocked | symptom=execution_brief_blocked | evidence=1 | next=/offdesk review",
                    "symptom": "execution_brief_blocked",
                    "failed_attempt": "critic=planning_gate",
                    "next_step": "/offdesk review",
                },
                "phase_checkpoint": {
                    "status": "blocked",
                    "current_phase": "plan",
                    "summary": "status=blocked | current=plan | plan=blocked|note=contract_gap",
                },
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
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Syncback Apply | executed",
        status="executed",
        outcome_kind="runtime_syncback_apply",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="path=TODO.md lines=14 done=1 reopen=0 append=1 blocked=0",
        next_step="/sync preview O2 24h",
        remediation="-",
        source_command="/todo O2 syncback apply",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:07:00+09:00",
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
        at="2026-04-09T11:12:00+09:00",
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
            }
        },
    )

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )

    control = summary["control_summary"]
    runtimes = summary["runtimes"]

    assert summary["team_dir"] == str(team_dir)
    assert control["auto_mode"] == "fanout"
    assert control["offdesk_mode"] == "on"
    assert control["provider_capacity_summary"] == "tasks=1 projects=1 providers=claude=1"
    assert control["latest_intent_command"] == "offdesk"
    assert control["latest_intent_action"] == "offdesk_prepare"
    assert "selected=offdesk_prepare" in control["latest_intent_trace"]
    assert control["latest_intent_focus"] == "오늘 밤 scope, provider capacity, auto posture를 먼저 점검"
    assert isinstance(control["server_guard"], dict)
    assert control["server_guard"]["summary"] != "-"
    assert control["server_guard"]["focus_label"] == "Codex Pressure"
    assert control["server_guard"]["action_copy"] == "start with Chat, then keep Global Direct narrow"
    assert control["server_guard"]["priority_link_label"] == "Chat"
    assert control["server_guard"]["priority_link_note"] == "trim chat fanout first"
    assert "recommended_actions" in control["server_guard"]
    assert any(row["link_href"] == "/control/runtimes/O2" for row in summary["recent_action_audit"])
    assert runtimes[0]["project_alias"] == "O2"
    assert runtimes[0]["completed_task_count"] == 1
    assert "/monitor O2" in runtimes[0]["operator_hints"]
    assert "/offdesk review O2" in runtimes[0]["operator_hints"]
    assert runtimes[0]["active_task_phase2_actions"] == []
    assert runtimes[0]["active_task_label"] == "T-001 | analysis-check"
    assert runtimes[0]["background_worker_summary"] != "-"
    assert runtimes[0]["background_queue_summary"] != "-"
    assert "depth=" in runtimes[0]["background_queue_summary"]
    assert runtimes[0]["background_scheduler_summary"] == "-"
    assert runtimes[0]["background_scheduler_note"] == "no queued scheduler head"
    assert runtimes[0]["latest_judge_summary"] == "Offdesk Judge | next=/offdesk review O2 | endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed"
    assert runtimes[0]["latest_judge_decision_summary"] == "action=retry | verdict=continue | confidence=medium | next=/retry T-001 | brief executable"
    assert runtimes[0]["latest_judge_decision_bridge_summary"] == "mode=promoted_next_step | action=retry | verdict=continue | confidence=medium | next=/retry T-001 | auto=yes"
    assert runtimes[0]["latest_replan_auto_decision_summary"] == "from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | auto=yes"
    assert runtimes[0]["latest_replan_auto_routing_policy_summary"] == "status=ready | from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | confirm=yes"
    assert runtimes[0]["latest_planning_handoff_summary"] == (
        "contract=status=blocked | plan=standard | scope=0 | checks=0 | artifacts=0"
        " | debug=state=blocked | symptom=execution_brief_blocked | evidence=1 | next=/offdesk review"
        " | phase=status=blocked | current=plan | plan=blocked|note=contract_gap"
        " | approved_plan=blocked | subtasks=0 | reviews=1 | issue=contract_gap"
    )
    assert "draft via" in runtimes[0]["latest_planning_review_summary"]
    assert "review via" in runtimes[0]["latest_planning_review_summary"]
    assert "dispatch waits for critic-approved plan" in runtimes[0]["latest_planning_review_summary"]
    assert runtimes[0]["latest_replan_auto_route_summary"] == "Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001"
    assert runtimes[0]["latest_replan_auto_route_status_summary"] == "ready+applied=/retry T-001 | at=2026-04-09T11:06:00+09:00"
    assert runtimes[0]["latest_canonical_writeback_summary"].startswith(
        "Syncback Apply | executed | state=executed | next=/sync preview O2 24h | at=2026-04-09T11:07:00+09:00 |"
    )
    assert runtimes[0]["active_task_background_run_worker_syncback_summary"].startswith(
        "state=applied | todo=TODO-002 | path=TODO.md"
    )
    assert runtimes[0]["active_task_reentry_rails_summary"] == "retry=blocked:underspecified exec=L1 review=R1 | followup=none | bg=running/local_background"
    assert runtimes[0]["run_lock_mode"] == "open"
    assert runtimes[0]["background_slot_limit"] == 1
    assert runtimes[0]["background_slot_active"] == 0
    assert runtimes[0]["background_slot_pressure"].startswith("idle (0/1)")
    assert "github_runner=0/1" in runtimes[0]["background_slot_pressure"]
    assert runtimes[0]["task_teams"][0]["request_id"] == "REQ-1"
    assert runtimes[0]["active_task_completion_contract"]["focus"] == "evidence quality, reasoning coherence, missing caveats"
    assert runtimes[0]["task_teams"][0]["completion_contract"]["done_when"] == "conclusion is supported by inspectable evidence and explicit caveats"


def test_write_nightly_session_summary_creates_latest_and_timestamped_files(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    state = json.loads(manager_state_file.read_text(encoding="utf-8"))
    task = state["projects"]["alpha"]["tasks"]["REQ-1"]
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_worker_syncback_summary"] = (
        "state=applied | todo=TODO-002 | path=TODO.md | lines=14 | done=1 reopen=0 append=1 blocked=0 | at=2026-04-09T11:07:00+09:00"
    )
    task["background_run_worker_syncback_at"] = "2026-04-09T11:07:00+09:00"
    manager_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output_dir = tmp_path / "summary-out"
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
        at="2026-04-09T11:12:00+09:00",
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
        at="2026-04-09T11:13:00+09:00",
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
            "planning_handoff": {
                "job_contract": {
                    "status": "blocked",
                    "planning_mode": "standard",
                    "summary": "status=blocked | plan=standard | scope=0 | checks=0 | artifacts=0",
                },
                "approved_plan": {
                    "status": "blocked",
                    "summary": "approved_plan=blocked | subtasks=0 | reviews=1 | issue=contract_gap",
                },
                "debug_packet": {
                    "state": "blocked",
                    "summary": "state=blocked | symptom=execution_brief_blocked | evidence=1 | next=/offdesk review",
                    "symptom": "execution_brief_blocked",
                    "failed_attempt": "critic=planning_gate",
                    "next_step": "/offdesk review",
                },
                "phase_checkpoint": {
                    "status": "blocked",
                    "current_phase": "plan",
                    "summary": "status=blocked | current=plan | plan=blocked|note=contract_gap",
                },
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
    assert action_audit.append_action_audit_row(
        team_dir,
        headline="Syncback Apply | executed",
        status="executed",
        outcome_kind="runtime_syncback_apply",
        outcome_status="executed",
        outcome_reason_code="completed",
        outcome_detail="path=TODO.md lines=14 done=1 reopen=0 append=1 blocked=0",
        next_step="/sync preview O2 24h",
        remediation="-",
        source_command="/todo O2 syncback apply",
        link_label="Runtime O2",
        link_href="/control/runtimes/O2",
        at="2026-04-09T11:07:00+09:00",
    )

    summary = nightly_summary.build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    latest_md, latest_json = nightly_summary.write_nightly_session_summary(summary=summary, output_dir=output_dir)

    assert latest_md.exists()
    assert latest_json.exists()
    assert (output_dir / "latest.md").exists()
    assert (output_dir / "latest.json").exists()

    timestamped_md = sorted(path for path in output_dir.glob("*.md") if path.name != "latest.md")
    timestamped_json = sorted(path for path in output_dir.glob("*.json") if path.name != "latest.json")
    assert timestamped_md
    assert timestamped_json

    markdown = latest_md.read_text(encoding="utf-8")
    payload = json.loads(latest_json.read_text(encoding="utf-8"))

    assert "# Nightly Session Summary" in markdown
    assert "## O2 Alpha" in markdown
    assert "latest_intent_command: offdesk" in markdown
    assert "latest_intent_action: offdesk_prepare" in markdown
    assert "latest_intent_trace: selected=offdesk_prepare" in markdown
    assert "first_focus: 오늘 밤 scope, provider capacity, auto posture를 먼저 점검" in markdown
    assert "server_guard:" in markdown
    assert "server_guard_focus: Codex Pressure" in markdown
    assert "server_guard_action_copy: start with Chat, then keep Global Direct narrow" in markdown
    assert "server_guard_priority_link: Chat | trim chat fanout first" in markdown
    assert "server_guard_snapshot:" in markdown
    assert "## Recent Dashboard Actions" in markdown
    assert "Sync Preview | preview" in markdown
    assert "link: runtime detail -> /control/runtimes/O2" in markdown
    assert "background_queue:" in markdown
    assert "latest_judge: Offdesk Judge | next=/offdesk review O2 | endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed" in markdown
    assert "latest_judge_decision: action=retry | verdict=continue | confidence=medium | next=/retry T-001 | brief executable" in markdown
    assert "latest_judge_decision_bridge: mode=promoted_next_step | action=retry | verdict=continue | confidence=medium | next=/retry T-001 | auto=yes" in markdown
    assert "replan_auto_decision: from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | auto=yes" in markdown
    assert "replan_auto_routing_policy: status=ready | from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | confirm=yes" in markdown
    assert "planning_compact: draft via" in markdown
    assert "dispatch waits for critic-approved plan" in markdown
    assert "planning_handoff: contract=status=blocked | plan=standard | scope=0 | checks=0 | artifacts=0 | debug=state=blocked | symptom=execution_brief_blocked | evidence=1 | next=/offdesk review | phase=status=blocked | current=plan | plan=blocked|note=contract_gap | approved_plan=blocked | subtasks=0 | reviews=1 | issue=contract_gap" in markdown
    assert "latest_replan_auto_route: Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001" in markdown
    assert "auto_route_status: ready+applied=/retry T-001 | at=2026-04-09T11:06:00+09:00" in markdown
    assert "canonical_writeback: Syncback Apply | executed | state=executed | next=/sync preview O2 24h | at=2026-04-09T11:07:00+09:00 | path=TODO.md lines=14 done=1 reopen=0 append=1 blocked=0" in markdown
    assert "worker_syncback: state=applied | todo=TODO-002 | path=TODO.md | lines=14 | done=1 reopen=0 append=1 blocked=0 | at=2026-04-09T11:07:00+09:00" in markdown
    assert "reentry_rails: retry=blocked:underspecified exec=L1 review=R1 | followup=none | bg=running/local_background" in markdown
    assert "run_lock: open" in markdown
    assert "background_slots: active=0 limit=1" in markdown
    assert "background_slot_pressure: idle (0/1)" in markdown
    assert "github_runner=0/1" in markdown
    assert "background_worker_summary:" in markdown
    assert "background_scheduler:" in markdown
    assert "background_scheduler_note: no queued scheduler head" in markdown
    assert "background_queue_depth:" in markdown
    assert "operator_hints: /offdesk review O2, /monitor O2, /todo O2, /offdesk review" in markdown
    assert "analysis-check (REQ-1)" in markdown
    assert "completion_focus: evidence quality, reasoning coherence, missing caveats" in markdown
    assert payload["runtimes"][0]["project_alias"] == "O2"
    assert "server_guard" in payload["control_summary"]
    assert any(row["headline"] == "Sync Preview | preview" for row in payload["recent_action_audit"])
