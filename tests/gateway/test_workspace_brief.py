#!/usr/bin/env python3
"""Workspace onboarding artifact regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


def test_workspace_brief_defaults_to_project_docs_and_todo(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")

    brief = workspace_brief.load_workspace_brief(
        team_dir,
        entry={
            "project_alias": "O2",
            "project_root": str(project_root),
            "background_runner_target": "local_tmux",
            "model_routing_profile": "hybrid_local_exec",
        },
        project_root=project_root,
    )

    assert brief["onboarding_status"] == "validated"
    assert brief["doc_roots"] == [str(docs_dir.resolve())]
    assert brief["canonical_todo_path"] == str((team_dir / "AOE_TODO.md").resolve())
    assert "status=validated" in brief["summary"]
    assert "docs=docs" in brief["summary"]
    assert "todo=.aoe-team/AOE_TODO.md" in brief["summary"]
    assert "routing=hybrid_local_exec" in brief["summary"]
    assert "runner=local_tmux" in brief["summary"]


def test_workspace_brief_write_and_reload_preserves_registered_doc_roots(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    custom_docs = project_root / "knowledge"
    custom_docs.mkdir(parents=True, exist_ok=True)

    written = workspace_brief.write_workspace_brief(
        team_dir,
        {
            "workspace_key": "twinpaper",
            "project_alias": "O7",
            "project_root": str(project_root),
            "doc_roots": [str(custom_docs)],
            "canonical_todo_path": "",
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"background_runner_target": "github_runner"},
    )
    reloaded = workspace_brief.load_workspace_brief(team_dir, project_root=project_root)
    raw = json.loads((team_dir / "workspace_brief.json").read_text(encoding="utf-8"))

    assert raw["onboarding_status"] == "active"
    assert written["doc_roots"] == [str(custom_docs.resolve())]
    assert reloaded["doc_roots"] == [str(custom_docs.resolve())]
    assert "docs=knowledge" in reloaded["summary"]
