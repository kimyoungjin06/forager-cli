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


def test_harness_authoring_plan_reports_missing_vendor_with_explicit_missing_root(tmp_path: Path) -> None:
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
        vendor_root=tmp_path / "missing-vendor",
    )

    assert plan["vendor"]["available"] is False
    assert plan["context_pack_profile"] == "followup_preview"
    assert plan["artifact_backend"]["backend_kind"] == "filesystem"
    assert plan["general_subagent_contract"]["subagent_kind"] == "general_research"
    assert plan["general_subagent_contract"]["output_artifact"]["path"].endswith("req-1-general-research.json")
    assert plan["general_subagent_summary"].startswith("general_research | profile=followup_preview")
    assert plan["general_subagent_artifact"] == {}
    assert plan["general_subagent_artifact_summary"] == "-"
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
    assert plan["general_subagent_contract"]["input_scope"]["doc_refs"] == ["spec-main"]
    assert plan["general_subagent_contract"]["backend"]["backend_kind"] == "filesystem"
    assert "vendor=ready" in plan["summary"]
    assert plan["selected_doc_paths"][0].endswith("SPEC.md")


def test_run_general_subagent_support_persists_bounded_evidence_artifact(tmp_path: Path) -> None:
    project_root = tmp_path / "Gamma"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    vendor_root = tmp_path / "vendor" / "revfactory-harness"
    team_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (docs_dir / "SPEC.md").write_text("# spec\n", encoding="utf-8")
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
            "workspace_key": "gamma",
            "project_alias": "O7",
            "project_root": str(project_root),
            "doc_roots": [str(docs_dir)],
            "background_runner_target": "local_background",
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"project_alias": "O7", "project_root": str(project_root)},
    )

    payload = harness_authoring_adapter.run_general_subagent_support(
        team_dir,
        entry={"project_alias": "O7", "project_root": str(project_root)},
        task={"request_id": "REQ-9", "short_id": "T-303", "phase2_team_preset": "review", "execution_brief_status": "executable"},
        vendor_root=vendor_root,
    )

    artifact_path = team_dir / "harness_authoring" / "subagents" / "req-9-general-research.json"
    assert artifact_path.exists()
    assert payload["artifact_path"] == "harness_authoring/subagents/req-9-general-research.json"
    assert payload["summary"].startswith("bounded evidence ready")
    assert payload["recommended_next_step"] == "/task T-303"
    assert any(source.endswith("SPEC.md") for source in payload["sources"])
    assert any(source.endswith("README.md") for source in payload["sources"])
    assert "context_pack=review | docs=1 | doc_ids=spec-main" in payload["key_findings"]
    assert payload["blocking_issues"] == []
