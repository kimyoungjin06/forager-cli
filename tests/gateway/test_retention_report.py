#!/usr/bin/env python3
"""Retention report regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_retention_report as retention_report


def _row(report: retention_report.RetentionReport, storage_class: str) -> retention_report.RetentionPolicyRow:
    return next(row for row in report.rows if row.storage_class == storage_class)


def _knobs(row: retention_report.RetentionPolicyRow) -> dict:
    return {knob.name: knob for knob in row.knobs}


def _clear_retention_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AOE_STATE_DIR",
        "AOE_TEAM_DIR",
        "AOE_GATEWAY_LOG_MAX_BYTES",
        "AOE_GATEWAY_LOG_KEEP_FILES",
        "AOE_GATEWAY_DEDUP_KEEP",
        "AOE_GATEWAY_FAILED_KEEP",
        "AOE_GATEWAY_FAILED_TTL_HOURS",
        "AOE_ROOM_RETENTION_DAYS",
        "AOE_TF_ARTIFACT_POLICY",
        "AOE_TF_EXEC_CACHE_TTL_HOURS",
        "AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS",
        "AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_retention_report_surfaces_default_policy_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_retention_env(monkeypatch)
    project_root = tmp_path / "demo"
    project_root.mkdir()

    report = retention_report.build_retention_report(project_root=project_root)

    assert [row.storage_class for row in report.rows] == [
        "canonical_runtime_state",
        "evidence_and_artifacts",
        "ephemeral_runtime_artifacts",
        "logs_and_rooms",
    ]
    assert report.warn_count == 0
    assert report.checked_path_count == 14

    ephemeral_knobs = _knobs(_row(report, "ephemeral_runtime_artifacts"))
    assert ephemeral_knobs["AOE_TF_ARTIFACT_POLICY"].value == "success-only"
    assert ephemeral_knobs["AOE_TF_EXEC_CACHE_TTL_HOURS"].value == 72

    log_knobs = _knobs(_row(report, "logs_and_rooms"))
    assert log_knobs["AOE_ROOM_RETENTION_DAYS"].value == 14
    assert log_knobs["AOE_GATEWAY_FAILED_TTL_HOURS"].value == 168
    assert log_knobs["AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS"].value == 500


def test_retention_report_warns_when_cleanup_windows_are_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_retention_env(monkeypatch)
    project_root = tmp_path / "demo"
    project_root.mkdir()
    monkeypatch.setenv("AOE_ROOM_RETENTION_DAYS", "0")
    monkeypatch.setenv("AOE_GATEWAY_FAILED_TTL_HOURS", "0")
    monkeypatch.setenv("AOE_TF_ARTIFACT_POLICY", "all")
    monkeypatch.setenv("AOE_TF_EXEC_CACHE_TTL_HOURS", "0")
    monkeypatch.setenv("AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS", "0")

    report = retention_report.build_retention_report(project_root=project_root)

    assert report.warn_count == 2
    assert _row(report, "ephemeral_runtime_artifacts").status == "warn"
    assert _row(report, "logs_and_rooms").status == "warn"
    assert "keeps TF execution artifacts indefinitely" in "\n".join(
        _row(report, "ephemeral_runtime_artifacts").notes
    )
    assert "disables room log cleanup" in "\n".join(_row(report, "logs_and_rooms").notes)


def test_retention_report_counts_jsonl_rows_and_renders_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_retention_env(monkeypatch)
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    audit_path = team_dir / "dashboard" / "action-history.jsonl"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text('{"status":"executed"}\n{"status":"blocked"}\n', encoding="utf-8")

    report = retention_report.build_retention_report(project_root=project_root)
    text = retention_report.render_retention_report(report)

    assert "retention policy disk hygiene report" in text
    assert "AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS" in text
    action_path = next(
        path for path in _row(report, "logs_and_rooms").paths if path.note == "dashboard action audit"
    )
    assert action_path.row_count == 2


def test_retention_report_main_outputs_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_retention_env(monkeypatch)
    project_root = tmp_path / "demo"
    project_root.mkdir()

    rc = retention_report.main(["--project-root", str(project_root), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["warn_count"] == 0
    assert payload["rows"][0]["storage_class"] == "canonical_runtime_state"
