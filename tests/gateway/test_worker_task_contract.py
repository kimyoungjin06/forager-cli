#!/usr/bin/env python3
"""Worker task module taxonomy regressions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_worker_task_contract as worker_task_contract  # noqa: E402


def test_sanitize_worker_task_contract_classifies_analysis_module() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-AN-1",
            "contract_preset": "analysis",
            "objective": "Investigate auth scope drift and compare findings.",
            "required_outputs": ["scope_inventory"],
            "artifact_targets": ["docs/analysis/auth_scope_inventory.md"],
        }
    )

    assert contract["module_kind"] == "analysis"
    assert contract["module_summary"] == "analysis | analysis/review signals"
    assert contract["module_policy"] == "findings_evidence_gate"
    assert contract["module_policy_summary"].startswith(
        "analysis | policy=findings_evidence_gate | result=findings+evidence"
    )
    assert contract["summary"].startswith("module=analysis | task=T-AN-1")


def test_sanitize_worker_task_contract_classifies_writing_module() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-WR-1",
            "contract_preset": "writer",
            "objective": "Draft operator handoff summary and polish the runbook.",
            "required_outputs": ["handoff_doc"],
            "artifact_targets": ["docs/handoff/operator_handoff.md"],
        }
    )

    assert contract["module_kind"] == "writing"
    assert contract["module_summary"] == "writing | writer/doc signals"
    assert contract["module_policy"] == "doc_quality_gate"
    assert contract["module_policy_summary"].startswith(
        "writing | policy=doc_quality_gate | result=draft+handoff"
    )
    assert contract["summary"].startswith("module=writing | task=T-WR-1")


def test_sanitize_worker_task_contract_classifies_package_module() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-PKG-1",
            "contract_preset": "build",
            "objective": "Prepare the release package bundle and archive outputs.",
            "required_outputs": ["release_bundle"],
            "artifact_targets": ["dist/release_bundle.zip"],
        }
    )
    update_stub = worker_task_contract.derive_worker_task_update_stub(
        contract,
        {
            "status": "ready",
            "summary": "release bundle prepared",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": ["dist/release_bundle.zip"],
        },
    )

    assert contract["module_kind"] == "package"
    assert contract["module_summary"] == "package | artifact/package signals"
    assert contract["module_policy"] == "artifact_integrity_gate"
    assert contract["module_policy_summary"].startswith(
        "package | policy=artifact_integrity_gate | result=artifact+verification"
    )
    assert contract["summary"].startswith("module=package | task=T-PKG-1")
    assert update_stub["module_kind"] == "package"
    assert update_stub["summary_line"] == (
        "module=package | status=ready | targets=dist/release_bundle.zip | actions=1 | refs=1"
    )


def test_resolve_worker_module_policy_defaults_to_general() -> None:
    policy = worker_task_contract.resolve_worker_module_policy({"module_kind": "unknown"})

    assert policy["module_kind"] == "general"
    assert policy["policy"] == "general_gate"
    assert policy["summary"].startswith(
        "general | policy=general_gate | result=summary+actions"
    )


def test_analysis_module_review_proposals_preserve_generic_handoff_shape() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-AN-2",
            "contract_preset": "analysis",
            "objective": "Investigate performance drift and collect evidence.",
            "artifact_targets": ["docs/analysis/perf_drift.md"],
        }
    )
    update_stub = worker_task_contract.derive_worker_task_update_stub(
        contract,
        {
            "status": "ready",
            "summary": "findings compiled",
            "actions": ["update docs/analysis/perf_drift.md"],
            "cautions": [],
            "evidence_refs": ["logs/perf.csv"],
        },
    )

    proposals = worker_task_contract.derive_worker_update_todo_proposals(contract, update_stub)

    assert proposals
    assert proposals[0]["kind"] == "handoff"
    assert proposals[0]["priority"] == "P2"
    assert str(proposals[0]["summary"]).startswith("review worker artifact update for T-AN-2")


def test_writing_module_review_and_apply_proposals_split_followup_and_handoff() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-WR-2",
            "contract_preset": "writer",
            "objective": "Draft the operator handoff memo.",
            "artifact_targets": ["docs/handoff/operator_handoff.md"],
        }
    )
    update_stub = worker_task_contract.derive_worker_task_update_stub(
        contract,
        {
            "status": "ready",
            "summary": "draft prepared",
            "actions": ["update docs/handoff/operator_handoff.md"],
            "cautions": [],
            "evidence_refs": ["docs/handoff/operator_handoff.md"],
        },
    )

    review_proposals = worker_task_contract.derive_worker_update_todo_proposals(contract, update_stub)
    apply_proposals = worker_task_contract.derive_worker_artifact_apply_todo_proposals(contract, update_stub)

    assert review_proposals
    assert review_proposals[0]["kind"] == "followup"
    assert review_proposals[0]["priority"] == "P2"
    assert str(review_proposals[0]["summary"]).startswith("review writing draft for T-WR-2")
    assert apply_proposals
    assert apply_proposals[0]["kind"] == "handoff"
    assert apply_proposals[0]["priority"] == "P2"
    assert str(apply_proposals[0]["summary"]).startswith("apply writing artifact update for T-WR-2")


def test_package_module_apply_proposals_escalate_to_p1_handoff() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-PKG-2",
            "contract_preset": "build",
            "objective": "Build the release package.",
            "artifact_targets": ["dist/release_bundle.zip"],
        }
    )
    update_stub = worker_task_contract.derive_worker_task_update_stub(
        contract,
        {
            "status": "ready",
            "summary": "release package built",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": ["dist/release_bundle.zip"],
        },
    )

    proposals = worker_task_contract.derive_worker_artifact_apply_todo_proposals(contract, update_stub)

    assert proposals
    assert proposals[0]["kind"] == "handoff"
    assert proposals[0]["priority"] == "P1"
    assert str(proposals[0]["summary"]).startswith("apply package artifact for T-PKG-2")
