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


def test_analysis_module_gate_prefers_findings_stable_when_refs_exist() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-AN-3",
            "contract_preset": "analysis",
            "objective": "Investigate provider regressions and collect evidence.",
            "artifact_targets": ["docs/analysis/provider_regressions.md"],
        }
    )

    gate = worker_task_contract.derive_worker_task_module_gate(
        contract,
        {
            "status": "ready",
            "summary": "findings compiled",
            "actions": ["update docs/analysis/provider_regressions.md"],
            "cautions": ["keep review lane open"],
            "evidence_refs": ["logs/provider_regressions.csv"],
        },
    )

    assert gate["state"] == "findings_stable"
    assert gate["summary_line"] == "state=findings_stable | findings=1 | refs=1 | stop=findings_stable"
    profile = worker_task_contract.derive_worker_task_module_profile(
        contract,
        {
            "status": "ready",
            "summary": "findings compiled",
            "actions": ["update docs/analysis/provider_regressions.md"],
            "cautions": ["keep review lane open"],
            "evidence_refs": ["logs/provider_regressions.csv"],
        },
        gate=gate,
    )
    assert profile["summary_line"] == (
        "analysis_findings_profile | state=findings_stable | findings=1 | evidence=1 | gaps=0 | targets=2 | cautions=1"
    )
    checklist = worker_task_contract.derive_worker_task_module_checklist(
        contract,
        {
            "status": "ready",
            "summary": "findings compiled",
            "actions": ["update docs/analysis/provider_regressions.md"],
            "cautions": ["keep review lane open"],
            "evidence_refs": ["logs/provider_regressions.csv"],
        },
        gate=gate,
        profile=profile,
    )
    assert checklist["summary_line"] == (
        "analysis_checklist | state=findings_stable | findings=1,evidence=1,gaps=0 | next=validate_caveats"
    )
    items = worker_task_contract.derive_worker_task_module_items(
        contract,
        {
            "status": "ready",
            "summary": "findings compiled",
            "actions": ["update docs/analysis/provider_regressions.md"],
            "cautions": ["keep review lane open"],
            "evidence_refs": ["logs/provider_regressions.csv"],
        },
        gate=gate,
        profile=profile,
        checklist=checklist,
    )
    assert items["summary_line"] == (
        "analysis_items | finding:update docs/analysis/provider_regressions.md,evidence:logs/provider_regressions.csv,caveat:keep review lane open"
    )
    assert items["items"] == [
        "finding:update docs/analysis/provider_regressions.md",
        "evidence:logs/provider_regressions.csv",
        "caveat:keep review lane open",
    ]


def test_writing_module_gate_surfaces_quality_open_from_review_cautions() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-WR-3",
            "contract_preset": "writer",
            "objective": "Draft the final operator handoff.",
            "artifact_targets": ["docs/handoff/final_handoff.md"],
        }
    )

    gate = worker_task_contract.derive_worker_task_module_gate(
        contract,
        {
            "status": "ready",
            "summary": "draft prepared",
            "actions": ["update docs/handoff/final_handoff.md"],
            "cautions": ["quality review still open"],
            "evidence_refs": ["docs/handoff/final_handoff.md"],
        },
    )

    assert gate["state"] == "quality_open"
    assert gate["summary_line"] == "state=quality_open | docs=1 | refs=1 | repeat=quality_gate_open"
    profile = worker_task_contract.derive_worker_task_module_profile(
        contract,
        {
            "status": "ready",
            "summary": "draft prepared",
            "actions": ["update docs/handoff/final_handoff.md"],
            "cautions": ["quality review still open"],
            "evidence_refs": ["docs/handoff/final_handoff.md"],
        },
        gate=gate,
    )
    assert profile["summary_line"] == (
        "writing_handoff_profile | state=quality_open | docs=1 | handoff=review | quality=open | refs=1 | cautions=1"
    )
    checklist = worker_task_contract.derive_worker_task_module_checklist(
        contract,
        {
            "status": "ready",
            "summary": "draft prepared",
            "actions": ["update docs/handoff/final_handoff.md"],
            "cautions": ["quality review still open"],
            "evidence_refs": ["docs/handoff/final_handoff.md"],
        },
        gate=gate,
        profile=profile,
    )
    assert checklist["summary_line"] == (
        "writing_checklist | state=quality_open | docs=1,handoff=review,quality=open | next=close_quality_gate"
    )
    items = worker_task_contract.derive_worker_task_module_items(
        contract,
        {
            "status": "ready",
            "summary": "draft prepared",
            "actions": ["update docs/handoff/final_handoff.md"],
            "cautions": ["quality review still open"],
            "evidence_refs": ["docs/handoff/final_handoff.md"],
        },
        gate=gate,
        profile=profile,
        checklist=checklist,
    )
    assert items["summary_line"] == (
        "writing_items | doc:docs/handoff/final_handoff.md,handoff:review,quality:open"
    )
    assert items["items"] == [
        "doc:docs/handoff/final_handoff.md",
        "handoff:review",
        "quality:open",
    ]


def test_package_module_gate_surfaces_artifact_check_open_without_verification_refs() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-PKG-3",
            "contract_preset": "build",
            "objective": "Prepare the release package and verify artifacts.",
            "artifact_targets": ["dist/release_bundle.zip"],
        }
    )

    gate = worker_task_contract.derive_worker_task_module_gate(
        contract,
        {
            "status": "ready",
            "summary": "package built",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": [],
        },
    )

    assert gate["state"] == "artifact_check_open"
    assert gate["summary_line"] == "state=artifact_check_open | artifacts=1 | refs=0 | repeat=artifact_check_open"
    profile = worker_task_contract.derive_worker_task_module_profile(
        contract,
        {
            "status": "ready",
            "summary": "package built",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": [],
        },
        gate=gate,
    )
    assert profile["summary_line"] == (
        "package_verification_profile | state=artifact_check_open | artifacts=1 | verification=0 | integrity=open | targets=1 | cautions=0"
    )
    checklist = worker_task_contract.derive_worker_task_module_checklist(
        contract,
        {
            "status": "ready",
            "summary": "package built",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": [],
        },
        gate=gate,
        profile=profile,
    )
    assert checklist["summary_line"] == (
        "package_checklist | state=artifact_check_open | artifacts=1,verification=0,integrity=open | next=verify_artifacts"
    )
    items = worker_task_contract.derive_worker_task_module_items(
        contract,
        {
            "status": "ready",
            "summary": "package built",
            "actions": ["update dist/release_bundle.zip"],
            "cautions": [],
            "evidence_refs": [],
        },
        gate=gate,
        profile=profile,
        checklist=checklist,
    )
    assert items["summary_line"] == (
        "package_items | artifact:dist/release_bundle.zip,verification:0,integrity:open"
    )
    assert items["items"] == [
        "artifact:dist/release_bundle.zip",
        "verification:0",
        "integrity:open",
    ]
