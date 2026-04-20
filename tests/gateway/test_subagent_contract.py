#!/usr/bin/env python3
"""Structured subagent contract regressions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_subagent_contract as subagent_contract  # noqa: E402


def test_general_research_subagent_contract_is_bounded_and_structured() -> None:
    contract = subagent_contract.build_general_research_subagent_contract(
        request_id="REQ-7",
        objective="Collect bounded harness references and local doc evidence.",
        backend_descriptor={"backend_kind": "filesystem", "summary": "backend=filesystem"},
        relevant_doc_ids=["spec-main"],
        context_pack_profile="review",
        context_pack_summary="profile=review docs=1 canonical=1",
        vendor_patterns=["hierarchical_delegation", "supervisor"],
    )

    assert contract["subagent_kind"] == "general_research"
    assert contract["execution_mode"] == "read_heavy_support"
    assert contract["ownership"]["parent_task"] == "dispatch_and_gate_owner"
    assert contract["output_artifact"]["path"] == "harness_authoring/subagents/req-7-general-research.json"
    assert "summary" in contract["output_artifact"]["required_fields"]
    assert subagent_contract.summarize_subagent_contract(contract).startswith("general_research | profile=review")


def test_normalize_subagent_result_artifact_coerces_missing_fields() -> None:
    artifact = subagent_contract.normalize_subagent_result_artifact(
        {
            "summary": "repo scan complete",
            "sources": ["docs/RUNBOOK.md", "docs/SPEC.md"],
            "key_findings": ["spec drift found"],
            "recommended_next_step": "/task T-001",
        }
    )

    assert artifact["subagent_kind"] == "general_research"
    assert artifact["confidence"] == "medium"
    assert artifact["summary"] == "repo scan complete"
    assert artifact["sources"] == ["docs/RUNBOOK.md", "docs/SPEC.md"]
    assert artifact["blocking_issues"] == []
