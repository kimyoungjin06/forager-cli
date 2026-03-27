#!/usr/bin/env python3
"""Gateway doctor regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_doctor as doctor


def _fake_which(binary: str) -> str | None:
    if binary == "tmux":
        return None
    return f"/usr/bin/{binary}"


def test_doctor_uses_explicit_team_dir_for_default_gateway_state(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    explicit_team_dir = tmp_path / "external-team"
    explicit_team_dir.mkdir(parents=True, exist_ok=True)
    (explicit_team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")

    report = doctor.collect_doctor_report(
        project_root=project_root,
        team_dir=str(explicit_team_dir),
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    assert report.team_dir == str(explicit_team_dir.resolve())
    assert report.gateway_state_file == str((explicit_team_dir / "telegram_gateway_state.json").resolve())
    assert report.manager_state_file == str((explicit_team_dir / "orch_manager_state.json").resolve())


def test_doctor_warns_when_aoe_state_dir_falls_back_to_legacy(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "demo"
    legacy_team_dir = project_root / ".aoe-team"
    state_root = tmp_path / "state-root"
    legacy_team_dir.mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    report = doctor.collect_doctor_report(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    fallback = next(row for row in report.checks if row.code == "state_root_legacy_fallback")
    assert report.state_root_mode == "legacy"
    assert fallback.status == "warn"
    assert "aoe_tg_state_root_migration.py" in fallback.next_step


def test_doctor_invalid_manager_state_fails_and_json_main_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orch_manager_state.json").write_text("{broken", encoding="utf-8")

    rc = doctor.main(
        [
            "--project-root",
            str(project_root),
            "--aoe-orch-bin",
            "/bin/echo",
            "--aoe-team-bin",
            "/bin/echo",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    check = next(row for row in payload["checks"] if row["code"] == "manager_state_file")
    assert rc == 1
    assert check["status"] == "fail"
    assert "unreadable" in check["summary"]


def test_doctor_missing_tmux_warns_without_failing(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")

    report = doctor.collect_doctor_report(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    tmux_check = next(row for row in report.checks if row.code == "tmux_bin")
    assert tmux_check.status == "warn"
    assert report.fail_count == 0
