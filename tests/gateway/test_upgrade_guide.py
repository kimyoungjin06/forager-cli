#!/usr/bin/env python3
"""Gateway upgrade guide regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_upgrade_guide as upgrade_guide


def _fake_which(binary: str) -> str | None:
    if binary == "systemctl":
        return None
    return f"/usr/bin/{binary}"


def _write_minimal_runtime(project_root: Path) -> None:
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (team_dir / "telegram.env").write_text("TOKEN=x\n", encoding="utf-8")


def _write_workflow(project_root: Path, body: str) -> None:
    workflows = project_root / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    (workflows / "gateway-tests.yml").write_text(body, encoding="utf-8")


def test_upgrade_guide_accepts_node24_github_action_majors(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    _write_minimal_runtime(project_root)
    _write_workflow(
        project_root,
        """
name: demo
jobs:
  demo:
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
      - uses: actions/upload-artifact@v7
      - uses: actions/download-artifact@v8
""".strip(),
    )

    report = upgrade_guide.collect_upgrade_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    workflow_step = next(row for row in report.steps if row.code == "github_workflow_actions")
    assert workflow_step.status == "ready"
    assert "actions/checkout@v6" in workflow_step.detail
    assert report.fail_count == 0


def test_upgrade_guide_fails_legacy_github_action_majors(tmp_path: Path) -> None:
    project_root = tmp_path / "demo"
    _write_minimal_runtime(project_root)
    _write_workflow(
        project_root,
        """
name: demo
jobs:
  demo:
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - uses: actions/upload-artifact@v4
      - uses: actions/download-artifact@v4
""".strip(),
    )

    report = upgrade_guide.collect_upgrade_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    workflow_step = next(row for row in report.steps if row.code == "github_workflow_actions")
    assert workflow_step.status == "fail"
    assert "actions/checkout: expected v6, found v4" in workflow_step.detail
    assert "actions/setup-python: expected v6, found v5" in workflow_step.detail
    assert report.fail_count == 1


def test_upgrade_guide_surfaces_state_root_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "demo"
    state_root = tmp_path / "state-root"
    _write_minimal_runtime(project_root)
    _write_workflow(
        project_root,
        """
name: demo
jobs:
  demo:
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
      - uses: actions/upload-artifact@v7
      - uses: actions/download-artifact@v8
""".strip(),
    )
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    report = upgrade_guide.collect_upgrade_guide(
        project_root=project_root,
        aoe_orch_bin="/bin/echo",
        aoe_team_bin="/bin/echo",
        which=_fake_which,
    )

    migration = next(row for row in report.steps if row.code == "state_root_migration")
    assert migration.status == "pending"
    assert "aoe_tg_state_root_migration.py" in migration.command
