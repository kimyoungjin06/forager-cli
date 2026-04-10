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
    assert any(row["headline"] == "Sync Preview | preview" for row in summary["recent_action_audit"])
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
    assert runtimes[0]["latest_replan_auto_route_summary"] == "Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001"
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
    assert "## Recent Dashboard Actions" in markdown
    assert "Sync Preview | preview" in markdown
    assert "link: runtime detail -> /control/runtimes/O2" in markdown
    assert "background_queue:" in markdown
    assert "latest_judge: Offdesk Judge | next=/offdesk review O2 | endpoint=codex_cli-gpt-5-4 provider=codex_cli model=gpt-5.4 status=completed" in markdown
    assert "latest_judge_decision: action=retry | verdict=continue | confidence=medium | next=/retry T-001 | brief executable" in markdown
    assert "latest_judge_decision_bridge: mode=promoted_next_step | action=retry | verdict=continue | confidence=medium | next=/retry T-001 | auto=yes" in markdown
    assert "replan_auto_decision: from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | auto=yes" in markdown
    assert "replan_auto_routing_policy: status=ready | from=replan | to=retry | confidence=medium | next=/retry T-001 | mode=promoted_next_step | confirm=yes" in markdown
    assert "latest_replan_auto_route: Replan Auto Route | applied | next=/retry T-001 | retry_command=/retry T-001" in markdown
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
    assert any(row["headline"] == "Sync Preview | preview" for row in payload["recent_action_audit"])
