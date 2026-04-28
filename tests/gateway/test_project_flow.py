#!/usr/bin/env python3
"""Project flow compiler regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_project_flow as project_flow  # noqa: E402


def _write_registry(project_root: Path) -> None:
    registry_dir = project_root / "docs" / "investigations_mo" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "project_registry.md").write_text(
        "\n".join(
            [
                "| project_alias | purpose | status | ongoing_doc | note_doc |",
                "| --- | --- | --- | --- | --- |",
                "| O7 | Alpha compiler | active | `docs/investigations_mo/projects/O7/ongoing.md` | `docs/investigations_mo/projects/O7/note.md` |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "project_lock.yaml").write_text(
        "\n".join(
            [
                "active_project: O7",
                "active_tf: TF-002",
                "active_paths:",
                "  project_ongoing: docs/investigations_mo/projects/O7/ongoing.md",
                "  project_note: docs/investigations_mo/projects/O7/note.md",
                "  tf_report: docs/investigations_mo/projects/O7/tfs/TF-002/report.md",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "tf_registry.md").write_text(
        "\n".join(
            [
                "| tf_id | project_alias | objective | status | exec_verdict | owner | created_at | closed_at | report_doc |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| TF-001 | O7 | First pass | closed | success | codex | 2026-04-01 | 2026-04-02 | `docs/investigations_mo/projects/O7/tfs/TF-001/report.md` |",
                "| TF-002 | O7 | Compiler baseline | running |  | codex | 2026-04-03 |  | `docs/investigations_mo/projects/O7/tfs/TF-002/report.md` |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "tf_close_index.csv").write_text(
        "project_alias,tf_id,task_label,request_id,status,exec_verdict,closed_at,report_doc,archive_bundle\n"
        "O7,TF-001,First pass,REQ-OLD,closed,success,2026-04-02,docs/investigations_mo/projects/O7/tfs/TF-001/report.md,\n",
        encoding="utf-8",
    )


def _write_project_docs(project_root: Path) -> None:
    project_dir = project_root / "docs" / "investigations_mo" / "projects" / "O7"
    (project_dir / "tfs" / "TF-002").mkdir(parents=True, exist_ok=True)
    (project_dir / "tfs" / "TF-001").mkdir(parents=True, exist_ok=True)
    (project_dir / "ongoing.md").write_text(
        "\n".join(
            [
                "# O7",
                "",
                "## Objective",
                "- Deliver compiled project flow handoff for runtime and document state.",
                "",
                "## Todo Queue",
                "| todo_id | summary | priority | status |",
                "| --- | --- | --- | --- |",
                "| TODO-001 | Publish compiler artifact | P1 | open |",
                "| TODO-002 | Wire dashboard card | P2 | closed |",
                "",
                "## Open Decisions",
                "- Keep the compiler read-only in phase 1.",
                "",
                "## Blockers",
                "- Provider pressure needs observation.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_dir / "note.md").write_text(
        "## Accepted Project Decisions\n- Preserve registry paths as relative evidence.\n",
        encoding="utf-8",
    )
    (project_dir / "tfs" / "TF-002" / "report.md").write_text(
        "## Objective\n- Compiler baseline report.\n\n## Open Risks\n- None beyond dashboard wiring.\n",
        encoding="utf-8",
    )
    (project_dir / "tfs" / "TF-001" / "report.md").write_text(
        "## Outcome\n- Closed first pass.\n",
        encoding="utf-8",
    )


def test_project_flow_compiles_runtime_and_document_signals(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    _write_registry(project_root)
    _write_project_docs(project_root)
    manager_state = {
        "projects": {
            "alpha": {
                "project_alias": "O7",
                "display_name": "Alpha",
                "last_request_id": "REQ-7",
                "tasks": {
                    "REQ-7": {
                        "request_id": "REQ-7",
                        "short_id": "T-007",
                        "status": "running",
                        "prompt": "Compile project flow",
                        "stages": {"planning": "done", "execution": "running"},
                    }
                },
            }
        }
    }

    payload = project_flow.write_project_flow(
        team_dir,
        project_root=project_root,
        manager_state=manager_state,
        project_alias="O7",
        compiled_at="2026-04-28T10:00:00+0900",
    )
    raw = json.loads((team_dir / "project-flow" / "O7" / "latest.json").read_text(encoding="utf-8"))

    assert payload["project_purpose"] == "Alpha compiler"
    assert payload["active_in_lock"] is True
    assert payload["runtime_status"] == "active"
    assert payload["active_request_ids"] == ["REQ-7"]
    assert payload["active_task_short_ids"] == ["T-007"]
    assert payload["latest_runtime_phase"] == "running"
    assert payload["latest_tf_report_path"] == "docs/investigations_mo/projects/O7/tfs/TF-002/report.md"
    assert payload["open_tf_ids"] == ["TF-002"]
    assert payload["recent_closed_tf_ids"] == ["TF-001"]
    assert payload["document_objective"] == "Deliver compiled project flow handoff for runtime and document state."
    assert payload["document_next_steps"] == ["TODO-001: Publish compiler artifact"]
    assert "Keep the compiler read-only in phase 1." in payload["document_open_decisions"]
    assert payload["drift_level"] == "none"
    assert raw["artifact_path"] == "project-flow/O7/latest.json"


def test_project_flow_marks_active_doc_runtime_drift(tmp_path: Path) -> None:
    project_root = tmp_path / "MissingDocs"
    team_dir = project_root / ".aoe-team"
    registry_dir = project_root / "docs" / "investigations_mo" / "registry"
    team_dir.mkdir(parents=True, exist_ok=True)
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "project_registry.md").write_text(
        "\n".join(
            [
                "| project_alias | purpose | status | ongoing_doc | note_doc |",
                "| --- | --- | --- | --- | --- |",
                "| O8 | Missing docs | active | `docs/investigations_mo/projects/O8/ongoing.md` | `docs/investigations_mo/projects/O8/note.md` |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (registry_dir / "project_lock.yaml").write_text(
        "active_project: O8\nactive_tf: TF-404\nactive_paths:\n  project_ongoing: docs/investigations_mo/projects/O8/ongoing.md\n",
        encoding="utf-8",
    )

    payload = project_flow.write_project_flow(
        team_dir,
        project_root=project_root,
        manager_state={"projects": {}},
        project_alias="O8",
        compiled_at="2026-04-28T10:00:00+0900",
    )

    assert payload["runtime_status"] == "missing"
    assert payload["doc_without_runtime_signal"] is True
    assert payload["runtime_without_doc_signal"] is False
    assert payload["drift_level"] == "warning"
    assert "docs/investigations_mo/projects/O8/ongoing.md" in payload["stale_doc_refs"]
    assert "document_objective_missing" in payload["drift_reasons"]
