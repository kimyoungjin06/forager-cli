#!/usr/bin/env python3
"""Learned runbook extraction regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_learned_runbook as learned_runbook


def _write_action_audit_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_learned_runbook_extracts_repeated_action_audit_blockers(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    for minute in ("10", "15"):
        _write_action_audit_row(
            audit_path,
            {
                "at": f"2026-04-29T09:{minute}:00+09:00",
                "headline": "Retry | blocked",
                "status": "blocked",
                "outcome_kind": "retry_run",
                "outcome_status": "blocked",
                "outcome_reason_code": "planning_gate",
                "outcome_detail": "planning critic blocked retry",
                "next_step": "/retry T-001",
                "remediation": "inspect planning critic issues before retrying again",
                "source_command": "/replan T-001 lane L1",
            },
        )
    _write_action_audit_row(
        audit_path,
        {
            "at": "2026-04-29T09:20:00+09:00",
            "headline": "Syncback Apply | executed",
            "status": "executed",
            "outcome_kind": "runtime_syncback_apply",
            "outcome_status": "executed",
            "outcome_reason_code": "completed",
            "next_step": "/sync preview O2 24h",
            "remediation": "verify canonical TODO drift is cleared",
            "source_command": "/todo O2 syncback apply",
        },
    )

    report = learned_runbook.build_learned_runbook_report(
        project_root=project_root,
        min_count=2,
    )

    assert report.observation_count == 3
    assert report.candidate_count == 1
    candidate = report.candidates[0]
    assert candidate.reason_code == "planning_gate"
    assert candidate.occurrence_count == 2
    assert candidate.outcome_kinds == ["retry_run"]
    assert candidate.source_types == ["action_audit"]
    assert candidate.remediation == "inspect planning critic issues before retrying again"


def test_learned_runbook_reads_nightly_summary_recent_actions(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    summary_dir = team_dir / "recovery" / "nightly-session-summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    for minute in ("10", "15"):
        summary_rows.append(
            {
                "at": f"2026-04-29T10:{minute}:00+09:00",
                "headline": "Follow-up Execute | blocked",
                "status": "blocked",
                "outcome_kind": "followup_execute",
                "outcome_status": "blocked",
                "outcome_reason_code": "followup_execute_brief_required",
                "next_step": "/followup T-501",
                "remediation": "derive an explicit executable FollowupBrief before off-desk execution",
                "source_command": "/followup-exec T-501 lane L2",
            }
        )
    (summary_dir / "latest.json").write_text(
        json.dumps({"generated_at": "2026-04-29T10:20:00+09:00", "recent_action_audit": summary_rows}, ensure_ascii=False),
        encoding="utf-8",
    )

    report = learned_runbook.build_learned_runbook_report(project_root=project_root, min_count=2)

    assert report.candidate_count == 1
    candidate = report.candidates[0]
    assert candidate.reason_code == "followup_execute_brief_required"
    assert candidate.source_types == ["nightly_summary"]
    assert candidate.next_step == "/followup T-501"


def test_learned_runbook_render_and_write_doc(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    for idx in range(2):
        _write_action_audit_row(
            audit_path,
            {
                "at": f"2026-04-29T11:0{idx}:00+09:00",
                "headline": "Background Runner | blocked",
                "status": "blocked",
                "outcome_kind": "background_run",
                "outcome_status": "blocked",
                "outcome_reason_code": "background_runner_slots_exhausted",
                "next_step": "/orch bgx-status O2",
                "remediation": "wait for current jobs to finish or raise that runner limit deliberately",
                "source_command": "/retry T-777",
            },
        )
    report = learned_runbook.build_learned_runbook_report(project_root=project_root, min_count=2)
    text = learned_runbook.render_learned_runbook(report)
    output = learned_runbook.write_learned_runbook(
        report=report,
        output_path=project_root / "docs" / "runbooks" / "learned-recovery-runbook.md",
    )

    assert "# Learned Recovery Runbook" in text
    assert "background_runner_slots_exhausted" in text
    assert output.exists()
    assert "raise that runner limit deliberately" in output.read_text(encoding="utf-8")


def test_learned_runbook_main_outputs_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    for idx in range(2):
        _write_action_audit_row(
            audit_path,
            {
                "at": f"2026-04-29T12:0{idx}:00+09:00",
                "headline": "Retry | blocked",
                "status": "blocked",
                "outcome_kind": "retry_run",
                "outcome_status": "blocked",
                "outcome_reason_code": "debug_packet_missing",
                "next_step": "/task T-901",
                "remediation": "derive and review the debug packet before retrying",
                "source_command": "/retry T-901",
            },
        )

    rc = learned_runbook.main(["--project-root", str(project_root), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["reason_code"] == "debug_packet_missing"


def test_learned_runbook_ignores_repeated_configuration_events(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    for idx in range(3):
        _write_action_audit_row(
            audit_path,
            {
                "at": f"2026-04-29T13:0{idx}:00+09:00",
                "headline": "Run Lock | configured",
                "status": "configured",
                "outcome_kind": "run_lock",
                "outcome_status": "configured",
                "outcome_reason_code": "open",
                "next_step": "/orch status O1",
                "remediation": "future rerun and detached execution will follow this lock mode",
                "source_command": "/orch run-lock O1 open",
            },
        )

    report = learned_runbook.build_learned_runbook_report(project_root=project_root, min_count=2)

    assert report.observation_count == 3
    assert report.candidate_count == 0
