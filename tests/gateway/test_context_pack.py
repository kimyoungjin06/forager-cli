#!/usr/bin/env python3
"""Context pack compiler regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_context_pack as context_pack  # noqa: E402
import aoe_tg_runtime_core as runtime_core  # noqa: E402
import aoe_tg_workspace_brief as workspace_brief  # noqa: E402


def test_context_pack_compiles_followup_preview_with_canonical_docs(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "RUNBOOK.md").write_text("# Runbook\n", encoding="utf-8")
    (docs_dir / "REQUEST_CONTRACT_SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (docs_dir / "notes.md").write_text("# Notes\n", encoding="utf-8")
    workspace_brief.write_workspace_brief(
        team_dir,
        {
            "project_root": str(project_root),
            "project_alias": "O2",
            "doc_roots": [str(docs_dir)],
            "onboarding_status": "active",
        },
        project_root=project_root,
        entry={"background_runner_target": "local_tmux"},
    )

    pack = context_pack.load_context_pack(
        team_dir,
        entry={"name": "alpha", "project_alias": "O2", "project_root": str(project_root)},
        task={
            "request_id": "REQ-1",
            "short_id": "T-001",
            "prompt": "Review the manual follow-up preview path.",
            "followup_brief_status": "preview_only",
            "followup_brief_reason": "operator must confirm handoff wording",
            "phase2_team_preset": "review",
        },
        project_root=project_root,
    )

    assert pack["profile"] == "followup_preview"
    assert "profile=followup_preview" in pack["summary"]
    assert "docs=2" in pack["summary"]
    assert pack["docs_summary"] == "docs/RUNBOOK.md, docs/REQUEST_CONTRACT_SPEC.md"
    assert "notes.md: noncanonical/bounded_pack" in pack["excluded_summary"]


def test_context_pack_loads_existing_artifact_for_request_profile(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = runtime_core.context_pack_path(team_dir, request_id="REQ-9", profile="offdesk_execute")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            {
                "pack_id": "REQ-9-offdesk_execute",
                "request_id": "REQ-9",
                "profile": "offdesk_execute",
                "compile_reason": "persisted_pack",
                "objective": "Retry lane L1 only",
                "relevant_docs": [
                    {
                        "doc_id": "doc-1",
                        "path": "docs/RUNBOOK.md",
                        "why_included": "canonical_runbook",
                        "freshness_class": "fresh",
                    }
                ],
                "excluded_context": ["docs/notes.md: bounded_pack"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    pack = context_pack.load_context_pack(
        team_dir,
        entry={"name": "twinpaper", "project_root": str(project_root)},
        task={"request_id": "REQ-9", "control_mode": "retry"},
        project_root=project_root,
    )

    assert pack["compile_reason"] == "persisted_pack"
    assert pack["docs_summary"] == "docs/RUNBOOK.md"
    assert "excluded=1" in pack["summary"]
