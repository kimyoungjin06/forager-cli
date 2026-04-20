#!/usr/bin/env python3
"""Harness authoring export regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_harness_authoring_export as harness_authoring_export  # noqa: E402
import aoe_tg_subagent_contract as subagent_contract  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


def _write_manager_state(project_root: Path, team_dir: Path, task: dict, *, project_alias: str = "O2") -> Path:
    state_path = team_dir / "orch_manager_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": "alpha",
                "projects": {
                    "alpha": {
                        "name": "alpha",
                        "display_name": "alpha",
                        "project_alias": project_alias,
                        "project_root": str(project_root),
                        "team_dir": str(team_dir),
                        "tasks": {str(task["request_id"]): task},
                        "task_alias_index": {str(task.get("short_id", "")): str(task["request_id"])},
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return state_path


def _write_vendor_root(tmp_path: Path) -> Path:
    vendor_root = tmp_path / "vendor" / "revfactory-harness"
    (vendor_root / "skills" / "harness").mkdir(parents=True, exist_ok=True)
    (vendor_root / "README.md").write_text("# harness\n", encoding="utf-8")
    (vendor_root / "skills" / "harness" / "SKILL.md").write_text("# skill\n", encoding="utf-8")
    return vendor_root


def test_export_harness_authoring_plan_writes_runtime_artifact(tmp_path: Path) -> None:
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
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"project_alias": "O2", "project_root": str(project_root)},
    )
    state_path = _write_manager_state(
        project_root,
        team_dir,
        {
            "request_id": "REQ-1",
            "short_id": "T-101",
            "followup_brief_status": "preview_only",
            "status": "pending",
        },
    )

    result = harness_authoring_export.export_harness_authoring_plan(
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=state_path,
        request_id="REQ-1",
    )

    artifact = Path(result["artifact_path"])
    assert artifact.exists()
    assert artifact.name == "REQ-1.json"
    assert result["artifact_relative_path"] == "harness_authoring/REQ-1.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["context_pack_profile"] == "followup_preview"
    assert payload["request_id"] == "REQ-1"
    assert payload["artifact_backend"]["backend_kind"] == "filesystem"
    assert payload["general_subagent_contract"]["subagent_kind"] == "general_research"


def test_export_harness_authoring_plan_resolves_task_ref_and_vendor_root(tmp_path: Path) -> None:
    project_root = tmp_path / "Beta"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    team_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    workspace_brief.write_workspace_brief(
        team_dir,
        {
            "workspace_key": "beta",
            "project_alias": "O5",
            "project_root": str(project_root),
            "doc_roots": [str(docs_dir)],
            "canonical_todo_path": str(project_root / "TODO.md"),
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"project_alias": "O5", "project_root": str(project_root)},
    )
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
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    state_path = _write_manager_state(
        project_root,
        team_dir,
        {
            "request_id": "REQ-7",
            "short_id": "T-202",
            "phase2_team_preset": "review",
            "execution_brief_status": "executable",
            "status": "pending",
        },
        project_alias="O5",
    )
    vendor_root = _write_vendor_root(tmp_path)
    contract = subagent_contract.build_general_research_subagent_contract(
        request_id="REQ-7",
        task_ref="T-202",
        objective="Collect bounded harness references and local doc evidence.",
        backend_descriptor={"backend_kind": "filesystem", "summary": "backend=filesystem"},
        relevant_doc_ids=["spec-main"],
        context_pack_profile="review",
    )
    subagent_contract.persist_subagent_result_artifact(
        team_dir,
        contract=contract,
        raw_result={
            "summary": "repo scan complete",
            "sources": ["docs/SPEC.md"],
            "key_findings": ["vendor pattern matched"],
            "recommended_next_step": "/task T-202",
        },
    )

    result = harness_authoring_export.export_harness_authoring_plan(
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=state_path,
        project_alias="O5",
        task_ref="T-202",
        vendor_root=str(vendor_root),
    )

    payload = result["plan"]
    assert payload["vendor"]["available"] is True
    assert payload["request_id"] == "REQ-7"
    assert payload["task_short_id"] == "T-202"
    assert payload["selected_doc_ids"] == ["spec-main"]
    assert payload["general_subagent_contract"]["input_scope"]["doc_refs"] == ["spec-main"]
    assert payload["general_subagent_artifact"]["summary"] == "repo scan complete"
    assert payload["general_subagent_artifact_summary"].startswith("general_research | confidence=medium")
