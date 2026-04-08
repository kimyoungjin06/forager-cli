#!/usr/bin/env python3
"""Harness authoring adapter regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_harness_authoring_adapter as harness_authoring_adapter  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


def test_harness_authoring_plan_reports_missing_vendor_by_default(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    team_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    workspace_brief.write_workspace_brief(
        team_dir,
        {
            "workspace_key": "alpha",
            "project_alias": "O2",
            "project_root": str(project_root),
            "doc_roots": [str(docs_dir)],
            "canonical_todo_path": str(project_root / "TODO.md"),
            "background_runner_target": "local_tmux",
            "model_routing_profile": "hybrid_local_exec",
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"project_alias": "O2", "project_root": str(project_root)},
    )

    plan = harness_authoring_adapter.build_harness_authoring_plan(
        team_dir,
        entry={"project_alias": "O2", "project_root": str(project_root)},
        task={"request_id": "REQ-1", "followup_brief_status": "preview_only"},
    )

    assert plan["vendor"]["available"] is False
    assert plan["context_pack_profile"] == "followup_preview"
    assert plan["authoring_targets"]["agents_dir"].endswith("/.claude/agents")
    assert "vendor=missing" in plan["summary"]


def test_harness_authoring_plan_detects_vendor_layout_and_selected_docs(tmp_path: Path) -> None:
    project_root = tmp_path / "Beta"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    vendor_root = tmp_path / "vendor" / "revfactory-harness"
    team_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (vendor_root / "skills" / "harness").mkdir(parents=True, exist_ok=True)
    (vendor_root / "README.md").write_text("# harness\n", encoding="utf-8")
    (vendor_root / "skills" / "harness" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    (team_dir / "document_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "doc_id": "spec-main",
                        "path": str((docs_dir / "SPEC.md").resolve()),
                        "doc_type": "spec",
                        "canonical": True,
                        "freshness_class": "fresh",
                    }
                ],
                "summary": "indexed=1 canonical=1 stale=0 kinds=spec=1",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    workspace_brief.write_workspace_brief(
        team_dir,
        {
            "workspace_key": "beta",
            "project_alias": "O5",
            "project_root": str(project_root),
            "doc_roots": [str(docs_dir)],
            "background_runner_target": "local_background",
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"project_alias": "O5", "project_root": str(project_root)},
    )

    plan = harness_authoring_adapter.build_harness_authoring_plan(
        team_dir,
        entry={"project_alias": "O5", "project_root": str(project_root)},
        task={"request_id": "REQ-7", "phase2_team_preset": "review", "execution_brief_status": "executable"},
        vendor_root=vendor_root,
    )

    assert plan["vendor"]["available"] is True
    assert plan["vendor"]["patterns"] == list(harness_authoring_adapter.REVFACTORY_HARNESS_PATTERNS)
    assert plan["context_pack_profile"] == "review"
    assert plan["selected_doc_ids"] == ["spec-main"]
    assert "vendor=ready" in plan["summary"]
