#!/usr/bin/env python3
"""Gateway setup guide regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_setup_guide as setup_guide


def _which_with_systemd(binary: str) -> str | None:
    mapping = {
        "systemctl": "/usr/bin/systemctl",
        "tmux": "/usr/bin/tmux",
    }
    return mapping.get(binary, f"/usr/bin/{binary}")


def _which_without_systemd(binary: str) -> str | None:
    mapping = {
        "tmux": "/usr/bin/tmux",
    }
    return mapping.get(binary)


def test_setup_guide_suggests_runtime_bootstrap_and_env_copy_when_missing(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"

    report = setup_guide.collect_setup_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_which_with_systemd,
    )

    bootstrap = next(row for row in report.steps if row.code == "runtime_bootstrap")
    env_step = next(row for row in report.steps if row.code == "runtime_env")
    assert bootstrap.status == "pending"
    assert "bootstrap_runtime_templates.sh" in bootstrap.command
    assert env_step.status == "pending"
    assert env_step.summary == "create telegram.env"


def test_setup_guide_surfaces_state_root_migration_when_legacy_fallback_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "demo"
    legacy_team_dir = project_root / ".aoe-team"
    state_root = tmp_path / "state-root"
    legacy_team_dir.mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    report = setup_guide.collect_setup_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_which_with_systemd,
    )

    migration = next(row for row in report.steps if row.code == "state_root_migration")
    assert migration.status == "pending"
    assert "aoe_tg_state_root_migration.py" in migration.command


def test_setup_guide_warns_when_systemd_prerequisites_are_missing(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (team_dir / "telegram.env").write_text("TOKEN=x\n", encoding="utf-8")

    report = setup_guide.collect_setup_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_which_without_systemd,
    )

    systemd_step = next(row for row in report.steps if row.code == "systemd_install")
    assert systemd_step.status == "warn"
    assert "systemctl" in systemd_step.detail
