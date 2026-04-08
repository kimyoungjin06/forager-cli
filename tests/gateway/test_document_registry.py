#!/usr/bin/env python3
"""Document registry scanner regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_document_registry as document_registry  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


def test_document_registry_scans_doc_roots_from_workspace_brief(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
    (docs_dir / "REQUEST_CONTRACT_SPEC.md").write_text("# Spec\n", encoding="utf-8")
    workspace_brief.write_workspace_brief(
        team_dir,
        {
            "project_root": str(project_root),
            "project_alias": "O2",
            "doc_roots": [str(docs_dir)],
            "canonical_todo_path": str((team_dir / "AOE_TODO.md").resolve()),
            "onboarding_status": "active",
        },
        project_root=project_root,
    )

    registry = document_registry.load_document_registry(team_dir, project_root=project_root)

    assert len(registry["records"]) == 2
    assert "indexed=2" in registry["summary"]
    assert "canonical=2" in registry["summary"]
    assert "runbook=1" in registry["summary"]
    assert "spec=1" in registry["summary"]


def test_document_registry_write_and_reload_preserves_records(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    note_path = project_root / "knowledge" / "note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Note\n", encoding="utf-8")

    written = document_registry.write_document_registry(
        team_dir,
        {
            "records": [
                {
                    "doc_id": "tp-001",
                    "path": str(note_path),
                    "doc_type": "note",
                    "source_kind": "markdown",
                    "title": "Note",
                    "canonical": False,
                    "freshness_class": "fresh",
                    "ingest_status": "indexed",
                }
            ]
        },
        project_root=project_root,
    )
    reloaded = document_registry.load_document_registry(team_dir, project_root=project_root)
    raw = json.loads((team_dir / "document_registry.json").read_text(encoding="utf-8"))

    assert raw["records"][0]["doc_id"] == "tp-001"
    assert written["records"][0]["path"] == str(note_path.resolve())
    assert reloaded["records"][0]["title"] == "Note"
    assert "indexed=1" in reloaded["summary"]
