#!/usr/bin/env python3
"""Artifact backend filesystem seam regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_artifact_backend as artifact_backend  # noqa: E402


def test_filesystem_backend_round_trips_context_pack_and_audit_rows(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    backend = artifact_backend.artifact_backend(team_dir)

    written = backend.write_context_pack(
        request_id="REQ-1",
        profile="on_desk_plan",
        payload={"request_id": "REQ-1", "profile": "on_desk_plan", "summary": "profile=on_desk_plan"},
    )
    appended = backend.append_action_audit_row(
        {
            "headline": "Retry | blocked",
            "status": "blocked",
            "source_command": "/retry T-001",
            "outcome_kind": "retry_run",
        }
    )

    assert written.exists()
    assert backend.load_context_pack(request_id="REQ-1", profile="on_desk_plan")["summary"] == "profile=on_desk_plan"
    assert appended is True
    assert backend.load_action_audit_rows()[-1]["headline"] == "Retry | blocked"


def test_filesystem_backend_writes_recovery_summary_and_external_artifacts(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    backend = artifact_backend.artifact_backend(team_dir)

    latest_md, latest_json = backend.write_recovery_summary(
        markdown="# Nightly\n",
        payload=json.dumps({"generated_at": "2026-04-20T10:00:00+09:00"}, ensure_ascii=False, indent=2) + "\n",
        stamp="20260420T1000000900",
        write_timestamped_copy=True,
    )
    handoff_path = backend.write_external_background_artifact(
        kind="handoffs",
        ticket_id="BGT-1",
        runner_target="github_runner",
        payload={"ticket_id": "BGT-1", "status": "running"},
    )

    assert latest_md.name == "latest.md"
    assert latest_json.name == "latest.json"
    assert (latest_md.parent / "20260420T1000000900.json").exists()
    assert backend.relative_artifact_path(handoff_path) == "background_run_handoffs/github-runner-bgt-1.json"
    assert backend.read_external_background_artifact(
        kind="handoffs",
        ticket_id="BGT-1",
        runner_target="github_runner",
    )["ticket_id"] == "BGT-1"
