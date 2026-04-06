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

import nightly_session_summary as nightly_summary  # noqa: E402
from test_control_dashboard import _build_runtime  # noqa: E402


def test_build_nightly_session_summary_uses_runtime_state_contract(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)

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
    assert summary["recent_action_audit"][0]["headline"] == "Sync Preview | preview"
    assert summary["recent_action_audit"][0]["link_href"] == "/control/runtimes/O2"
    assert runtimes[0]["project_alias"] == "O2"
    assert runtimes[0]["completed_task_count"] == 1
    assert "/monitor O2" in runtimes[0]["operator_hints"]
    assert "/offdesk review O2" in runtimes[0]["operator_hints"]
    assert runtimes[0]["active_task_phase2_actions"] == []
    assert runtimes[0]["active_task_label"] == "T-001 | analysis-check"
    assert runtimes[0]["background_worker_summary"] != "-"
    assert runtimes[0]["background_queue_summary"] != "-"
    assert "depth=" in runtimes[0]["background_queue_summary"]
    assert runtimes[0]["task_teams"][0]["request_id"] == "REQ-1"
    assert runtimes[0]["active_task_completion_contract"]["focus"] == "evidence quality, reasoning coherence, missing caveats"
    assert runtimes[0]["task_teams"][0]["completion_contract"]["done_when"] == "conclusion is supported by inspectable evidence and explicit caveats"


def test_write_nightly_session_summary_creates_latest_and_timestamped_files(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    team_dir, manager_state_file, _project_root = _build_runtime(control_root)
    output_dir = tmp_path / "summary-out"

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
    assert "background_worker_summary:" in markdown
    assert "background_queue_depth:" in markdown
    assert "operator_hints: /offdesk review O2, /monitor O2, /todo O2, /offdesk review" in markdown
    assert "analysis-check (REQ-1)" in markdown
    assert "completion_focus: evidence quality, reasoning coherence, missing caveats" in markdown
    assert payload["runtimes"][0]["project_alias"] == "O2"
    assert payload["recent_action_audit"][0]["headline"] == "Sync Preview | preview"
