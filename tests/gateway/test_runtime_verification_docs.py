from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PHASE2 = ROOT / "docs" / "runtime_verification" / "phase2"

EXPECTED_PHASE2_SCENARIOS = [
    "build/B1_happy_path.md",
    "build/B2_rerun_path.md",
    "build/B3_manual_followup_path.md",
    "data/D1_happy_path.md",
    "data/D2_rerun_path.md",
    "data/D3_manual_followup_path.md",
    "mixed/M1_happy_path.md",
    "mixed/M2_rerun_path.md",
    "mixed/M3_manual_followup_path.md",
    "review/R1_happy_path.md",
    "review/R2_rerun_path.md",
    "review/R3_manual_followup_execute.md",
    "review/R3_manual_followup_preview.md",
    "review/R4_external_background_rail.md",
]

INVENTORY_SIMPLE_SCENARIOS = [
    "B1. Happy Path",
    "B2. Rerun Path",
    "B3. Manual Followup Path",
    "D1. Happy Path",
    "D2. Rerun Path",
    "D3. Manual Followup Path",
    "R1. Happy Path",
    "R2. Rerun Path",
    "R4. External Background Rail Support Proof",
    "M1. Happy Path",
    "M2. Rerun Path",
    "M3. Manual Followup Path",
]


def _section(text: str, heading: str, next_heading_pattern: str) -> str:
    match = re.search(
        rf"^{re.escape(heading)}\n(?P<body>.*?)(?=^{next_heading_pattern}|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing section: {heading}"
    return match.group("body")


def _scenario_status(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^- status:\n\s+- `([^`]+)`", text, re.MULTILINE)
    assert match is not None, f"{path.relative_to(ROOT)} is missing a top-level status"
    return match.group(1)


def test_phase2_runtime_scenario_docs_are_complete_and_promoted() -> None:
    actual = sorted(
        path.relative_to(PHASE2).as_posix()
        for path in PHASE2.rglob("*.md")
        if path.name != "TEMPLATE.md"
    )

    assert actual == sorted(EXPECTED_PHASE2_SCENARIOS)

    for rel_path in EXPECTED_PHASE2_SCENARIOS:
        assert _scenario_status(PHASE2 / rel_path) == "executed_done"


def test_runtime_readme_layout_and_status_list_every_phase2_scenario() -> None:
    text = (ROOT / "docs" / "runtime_verification" / "README.md").read_text(
        encoding="utf-8"
    )
    layout = _section(text, "## Layout", r"## ")
    current_first_wave = _section(text, "## Current First Wave", r"## ")

    for rel_path in EXPECTED_PHASE2_SCENARIOS:
        assert f"`{rel_path}`" in layout
        assert f"`{rel_path}`" in current_first_wave


def test_live_runtime_inventory_exposes_promoted_status_for_each_scenario() -> None:
    text = (ROOT / "docs" / "LIVE_RUNTIME_VERIFICATION_SCENARIOS.md").read_text(
        encoding="utf-8"
    )

    for title in INVENTORY_SIMPLE_SCENARIOS:
        section = _section(text, f"#### {title}", r"#### |### ")
        assert "- current status:\n  - `executed_done`" in section

    r3_section = _section(text, "#### R3. Manual Followup Path", r"#### |### ")
    assert (
        "`preview_surface` completed the first read-only live rehearsal and is now "
        "`executed_done`"
    ) in r3_section
    assert (
        "`execute_surface` completed one isolated `local_tmux` live rehearsal and is now "
        "`executed_done`"
    ) in r3_section
    assert (
        "`docs/runtime_verification/phase2/review/R3_manual_followup_preview.md`"
        in r3_section
    )
    assert (
        "`docs/runtime_verification/phase2/review/R3_manual_followup_execute.md`"
        in r3_section
    )
