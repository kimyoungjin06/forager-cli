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
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
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
        items=items,
    )
    assert item_classes["summary_line"] == (
        "analysis_item_classes | finding=1 | evidence=1 | gap=0 | caveat=1"
    )
    assert item_classes["classes"] == ["finding=1", "evidence=1", "gap=0", "caveat=1"]
    records = worker_task_contract.derive_worker_task_module_records(
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
        items=items,
        item_classes=item_classes,
    )
    assert records["summary_line"] == (
        "analysis_records | finding_record=update docs/analysis/provider_regressions.md | evidence_record=logs/provider_regressions.csv | caveat_record=keep review lane open"
    )
    assert records["records"] == [
        "finding_record=update docs/analysis/provider_regressions.md",
        "evidence_record=logs/provider_regressions.csv",
        "caveat_record=keep review lane open",
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
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
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
        items=items,
    )
    assert item_classes["summary_line"] == (
        "writing_item_classes | doc=1 | handoff=review | quality=open"
    )
    assert item_classes["classes"] == ["doc=1", "handoff=review", "quality=open"]
    records = worker_task_contract.derive_worker_task_module_records(
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
        items=items,
        item_classes=item_classes,
    )
    assert records["summary_line"] == (
        "writing_records | doc_record=docs/handoff/final_handoff.md | handoff_record=review | quality_record=open"
    )
    assert records["records"] == [
        "doc_record=docs/handoff/final_handoff.md",
        "handoff_record=review",
        "quality_record=open",
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
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
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
        items=items,
    )
    assert item_classes["summary_line"] == (
        "package_item_classes | artifact=1 | verification=0 | integrity=open"
    )
    assert item_classes["classes"] == ["artifact=1", "verification=0", "integrity=open"]
    records = worker_task_contract.derive_worker_task_module_records(
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
        items=items,
        item_classes=item_classes,
    )
    assert records["summary_line"] == (
        "package_records | artifact_record=dist/release_bundle.zip | verification_record=0 | apply_record=ready | syncback_record=pending"
    )
    assert records["records"] == [
        "artifact_record=dist/release_bundle.zip",
        "verification_record=0",
        "apply_record=ready",
        "syncback_record=pending",
    ]


def test_worker_task_module_record_map_parses_package_records() -> None:
    record_map = worker_task_contract.worker_task_module_record_map(
        {
            "module_kind": "package",
            "records_kind": "package_records",
            "records": [
                "artifact_record=dist/release_bundle.zip",
                "verification_record=1",
                "apply_record=ready",
                "syncback_record=ready",
            ],
        }
    )

    assert record_map == {
        "artifact_record": "dist/release_bundle.zip",
        "verification_record": "1",
        "apply_record": "ready",
        "syncback_record": "ready",
    }


def test_worker_task_module_syncback_ready_returns_false_for_pending_package_records() -> None:
    assert worker_task_contract.worker_task_module_syncback_ready(
        {
            "module_kind": "package",
            "records_kind": "package_records",
            "records": [
                "artifact_record=dist/release_bundle.zip",
                "verification_record=1",
                "apply_record=ready",
                "syncback_record=pending",
            ],
        }
    ) is False
    assert worker_task_contract.worker_task_module_syncback_ready(
        {
            "module_kind": "package",
            "records_kind": "package_records",
            "records": [
                "artifact_record=dist/release_bundle.zip",
                "verification_record=1",
                "apply_record=ready",
                "syncback_record=ready",
            ],
        }
    ) is True


def test_analysis_module_record_rows_capture_finding_evidence_and_caveat_states() -> None:
    rows = worker_task_contract.derive_worker_task_module_record_rows(
        {"module_kind": "analysis"},
        {
            "status": "ready",
            "summary": "analysis ready",
            "actions": ["update docs/analysis/provider_regressions.md"],
            "cautions": ["keep review lane open"],
            "evidence_refs": ["logs/provider_regressions.csv"],
        },
        gate={"state": "findings_stable", "summary_line": "state=findings_stable"},
        profile={"state": "findings_stable", "summary_line": "analysis_findings_profile | findings=1 | evidence=1"},
        checklist={"state": "findings_stable", "summary_line": "analysis_checklist | next=validate_caveats"},
        items={
            "module_kind": "analysis",
            "items": [
                "finding:update docs/analysis/provider_regressions.md",
                "evidence:logs/provider_regressions.csv",
                "caveat:keep review lane open",
            ],
        },
        item_classes={
            "module_kind": "analysis",
            "classes": ["finding=1", "evidence=1", "gap=0", "caveat=1"],
        },
        records={
            "module_kind": "analysis",
            "records": [
                "finding_record=update docs/analysis/provider_regressions.md",
                "evidence_record=logs/provider_regressions.csv",
                "caveat_record=keep review lane open",
            ],
        },
    )

    assert rows["summary_line"] == (
        "analysis_record_rows | finding_row=update docs/analysis/provider_regressions.md|state=stable | "
        "evidence_row=logs/provider_regressions.csv|state=attached | "
        "caveat_row=keep review lane open|state=review|note=findings_stable"
    )
    assert rows["rows"] == [
        "finding_row=update docs/analysis/provider_regressions.md|state=stable",
        "evidence_row=logs/provider_regressions.csv|state=attached",
        "caveat_row=keep review lane open|state=review|note=findings_stable",
    ]


def test_writing_module_record_rows_capture_handoff_and_quality_states() -> None:
    rows = worker_task_contract.derive_worker_task_module_record_rows(
        {"module_kind": "writing"},
        {
            "status": "ready",
            "summary": "handoff drafted",
            "actions": ["update docs/handoff/final_handoff.md"],
            "cautions": ["quality gate open"],
            "evidence_refs": ["docs/handoff/final_handoff.md"],
        },
        gate={"state": "quality_open", "summary_line": "state=quality_open"},
        profile={"state": "quality_open", "summary_line": "writing_handoff_profile | handoff=review | quality=open"},
        checklist={"state": "quality_open", "summary_line": "writing_checklist | next=close_quality_gate"},
        items={
            "module_kind": "writing",
            "items": [
                "doc:docs/handoff/final_handoff.md",
                "handoff:review",
                "quality:open",
            ],
        },
        item_classes={
            "module_kind": "writing",
            "classes": ["doc=1", "handoff=review", "quality=open"],
        },
        records={
            "module_kind": "writing",
            "records": [
                "doc_record=docs/handoff/final_handoff.md",
                "handoff_record=review",
                "quality_record=open",
            ],
        },
    )

    assert rows["summary_line"] == (
        "writing_record_rows | doc_row=docs/handoff/final_handoff.md|state=present | "
        "handoff_row=review|state=waiting|note=quality_open | "
        "quality_row=open|state=open|note=quality_open"
    )
    assert rows["rows"] == [
        "doc_row=docs/handoff/final_handoff.md|state=present",
        "handoff_row=review|state=waiting|note=quality_open",
        "quality_row=open|state=open|note=quality_open",
    ]


def test_analysis_module_apply_ready_requires_stable_finding_and_attached_evidence() -> None:
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "analysis",
            "rows_kind": "analysis_record_rows",
            "rows": [
                "finding_row=update docs/analysis/provider_regressions.md|state=stable",
                "evidence_row=logs/provider_regressions.csv|state=attached",
                "caveat_row=keep review lane open|state=review|note=findings_stable",
            ],
        }
    ) is True
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "analysis",
            "rows_kind": "analysis_record_rows",
            "rows": [
                "finding_row=update docs/analysis/provider_regressions.md|state=stable",
                "evidence_row=-|state=missing",
                "gap_row=evidence_missing|state=open|note=review",
            ],
        }
    ) is False


def test_writing_module_apply_ready_requires_ready_handoff_and_quality() -> None:
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "writing",
            "rows_kind": "writing_record_rows",
            "rows": [
                "doc_row=docs/handoff/final_handoff.md|state=present",
                "handoff_row=ready|state=ready|note=handoff_ready",
                "quality_row=ready|state=ready|note=handoff_ready",
            ],
        }
    ) is True
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "writing",
            "rows_kind": "writing_record_rows",
            "rows": [
                "doc_row=docs/handoff/final_handoff.md|state=present",
                "handoff_row=review|state=waiting|note=quality_open",
                "quality_row=open|state=open|note=quality_open",
            ],
        }
    ) is False


def test_package_module_apply_ready_requires_verification_and_apply_ready() -> None:
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "package",
            "rows_kind": "package_record_rows",
            "rows": [
                "artifact_row=dist/release_bundle.zip|state=present",
                "verification_row=1|state=ready",
                "apply_row=ready|state=ready",
                "syncback_row=pending|state=blocked|note=syncback_clean",
            ],
        }
    ) is True
    assert worker_task_contract.worker_task_module_apply_ready(
        {
            "module_kind": "package",
            "rows_kind": "package_record_rows",
            "rows": [
                "artifact_row=dist/release_bundle.zip|state=present",
                "verification_row=0|state=open",
                "apply_row=pending|state=pending",
                "syncback_row=pending|state=blocked|note=artifact_check_open",
            ],
        }
    ) is False


def test_analysis_module_preflight_summarizes_review_readiness() -> None:
    preflight = worker_task_contract.derive_worker_task_module_preflight(
        {"module_kind": "analysis"},
        {"status": "ready", "summary": "analysis ready", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "analysis",
            "rows_kind": "analysis_record_rows",
            "rows": [
                "finding_row=update docs/analysis/provider_regressions.md|state=stable",
                "evidence_row=logs/provider_regressions.csv|state=attached",
                "caveat_row=keep review lane open|state=review|note=findings_stable",
            ],
        },
    )
    assert preflight["summary_line"] == (
        "analysis_preflight | state=review_ready | finding=stable | evidence=attached | gap=- | apply=ready | next=validate_caveats"
    )


def test_writing_module_preflight_summarizes_handoff_open_state() -> None:
    preflight = worker_task_contract.derive_worker_task_module_preflight(
        {"module_kind": "writing"},
        {"status": "ready", "summary": "handoff drafted", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "writing",
            "rows_kind": "writing_record_rows",
            "rows": [
                "doc_row=docs/handoff/final_handoff.md|state=present",
                "handoff_row=review|state=waiting|note=quality_open",
                "quality_row=open|state=open|note=quality_open",
            ],
        },
    )
    assert preflight["summary_line"] == (
        "writing_preflight | state=handoff_open | doc=present | handoff=waiting | quality=open | apply=blocked | next=close_quality_gate"
    )


def test_package_module_preflight_and_syncback_row_ready() -> None:
    rows = {
        "module_kind": "package",
        "rows_kind": "package_record_rows",
        "rows": [
            "artifact_row=dist/release_bundle.zip|state=present",
            "verification_row=1|state=ready",
            "apply_row=ready|state=ready",
            "syncback_row=ready|state=ready|note=syncback_clean",
        ],
    }
    preflight = worker_task_contract.derive_worker_task_module_preflight(
        {"module_kind": "package"},
        {"status": "ready", "summary": "package ready", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows=rows,
    )
    assert worker_task_contract.worker_task_module_syncback_ready_from_rows(rows) is True
    assert preflight["summary_line"] == (
        "package_preflight | state=syncback_ready | verification=ready | apply=ready | syncback=ready | next=syncback_clean"
    )


def test_analysis_module_preflight_rows_capture_review_ready_signals() -> None:
    rows = worker_task_contract.derive_worker_task_module_preflight_rows(
        {"module_kind": "analysis"},
        {"status": "ready", "summary": "analysis ready", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "analysis",
            "rows_kind": "analysis_record_rows",
            "rows": [
                "finding_row=update docs/analysis/provider_regressions.md|state=stable",
                "evidence_row=logs/provider_regressions.csv|state=attached",
                "caveat_row=keep review lane open|state=review|note=findings_stable",
            ],
        },
        preflight={
            "module_kind": "analysis",
            "preflight_kind": "analysis_preflight",
            "state": "review_ready",
            "next_hint": "validate_caveats",
        },
    )
    assert rows["summary_line"] == (
        "analysis_preflight_rows | finding_ready=stable|state=ready|note=findings | "
        "evidence_ready=attached|state=ready|note=evidence | gap_closed=clear|state=ready|note=validate_caveats | "
        "review_ready=review_ready|state=ready|note=validate_caveats"
    )


def test_writing_module_preflight_rows_capture_handoff_blockers() -> None:
    rows = worker_task_contract.derive_worker_task_module_preflight_rows(
        {"module_kind": "writing"},
        {"status": "ready", "summary": "handoff drafted", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "writing",
            "rows_kind": "writing_record_rows",
            "rows": [
                "doc_row=docs/handoff/final_handoff.md|state=present",
                "handoff_row=review|state=waiting|note=quality_open",
                "quality_row=open|state=open|note=quality_open",
            ],
        },
        preflight={
            "module_kind": "writing",
            "preflight_kind": "writing_preflight",
            "state": "handoff_open",
            "next_hint": "close_quality_gate",
        },
    )
    assert rows["summary_line"] == (
        "writing_preflight_rows | doc_present=present|state=ready|note=document | "
        "handoff_ready=waiting|state=blocked|note=handoff | quality_ready=open|state=blocked|note=quality_gate | "
        "writing_ready=handoff_open|state=blocked|note=close_quality_gate"
    )


def test_package_module_preflight_rows_capture_syncback_gate() -> None:
    rows = worker_task_contract.derive_worker_task_module_preflight_rows(
        {"module_kind": "package"},
        {"status": "ready", "summary": "package ready", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "package",
            "rows_kind": "package_record_rows",
            "rows": [
                "artifact_row=dist/release_bundle.zip|state=present",
                "verification_row=1|state=ready",
                "apply_row=ready|state=ready",
                "syncback_row=ready|state=ready|note=syncback_clean",
            ],
        },
        preflight={
            "module_kind": "package",
            "preflight_kind": "package_preflight",
            "state": "syncback_ready",
            "next_hint": "syncback_clean",
        },
    )
    assert rows["summary_line"] == (
        "package_preflight_rows | verification_ready=ready|state=ready|note=verification | "
        "apply_ready=ready|state=ready|note=apply_gate | syncback_ready=ready|state=ready|note=syncback_clean | "
        "package_ready=syncback_ready|state=ready|note=syncback_clean"
    )


def test_package_module_preflight_surfaces_verification_pending_before_apply_stage() -> None:
    preflight = worker_task_contract.derive_worker_task_module_preflight(
        {"module_kind": "package"},
        {"status": "ready", "summary": "package pending verification", "actions": [], "cautions": [], "evidence_refs": []},
        record_rows={
            "module_kind": "package",
            "rows_kind": "package_record_rows",
            "rows": [
                "artifact_row=dist/release_bundle.zip|state=present",
                "verification_row=0|state=open",
                "apply_row=pending|state=pending",
                "syncback_row=pending|state=blocked|note=prepare_syncback",
            ],
        },
    )
    assert preflight["summary_line"] == (
        "package_preflight | state=verification_pending | verification=open | apply=pending | syncback=blocked | next=verify_artifacts"
    )


def test_writing_apply_blocker_prefers_quality_open_reason() -> None:
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            "module_kind": "writing",
            "rows_kind": "writing_preflight_rows",
            "rows": [
                "doc_present=present|state=ready|note=document",
                "handoff_ready=waiting|state=blocked|note=handoff",
                "quality_ready=open|state=blocked|note=quality_gate",
                "writing_ready=handoff_open|state=blocked|note=close_quality_gate",
            ],
        },
        mode="apply",
    )
    assert blocker["reason_code"] == "writing_quality_open"
    assert blocker["suggested_action"] == "followup"
    assert blocker["remediation"] == "close the document quality gate before applying writing changes"
    assert blocker["summary_line"] == (
        "writing_apply_blocker | reason=writing_quality_open | blocked=handoff_ready,quality_ready,writing_ready | next=quality_gate"
    )


def test_writing_apply_blocker_prefers_followup_execute_when_brief_executable() -> None:
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            "module_kind": "writing",
            "rows_kind": "writing_preflight_rows",
            "rows": [
                "doc_present=present|state=ready|note=document",
                "handoff_ready=waiting|state=blocked|note=handoff",
                "quality_ready=open|state=blocked|note=quality_gate",
                "writing_ready=handoff_open|state=blocked|note=close_quality_gate",
            ],
            "followup_brief_status": "partially_executable",
        },
        mode="apply",
    )
    assert blocker["reason_code"] == "writing_quality_open"
    assert blocker["suggested_action"] == "followup_execute"
    assert blocker["remediation"] == "execute the writing follow-up and close the document quality gate before applying changes"


def test_package_syncback_blocker_prefers_syncback_pending_reason() -> None:
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            "module_kind": "package",
            "rows_kind": "package_preflight_rows",
            "rows": [
                "verification_ready=ready|state=ready|note=verification",
                "apply_ready=ready|state=ready|note=apply_gate",
                "syncback_ready=blocked|state=blocked|note=prepare_syncback",
                "package_ready=syncback_pending|state=blocked|note=prepare_syncback",
            ],
        },
        mode="syncback",
    )
    assert blocker["reason_code"] == "package_syncback_pending"
    assert blocker["suggested_action"] == "task_review"
    assert blocker["remediation"] == "prepare syncback readiness before accepted syncback"
    assert blocker["summary_line"] == (
        "package_syncback_blocker | reason=package_syncback_pending | blocked=syncback_ready,package_ready | next=prepare_syncback"
    )


def test_analysis_apply_blocker_prefers_task_review_guidance() -> None:
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            "module_kind": "analysis",
            "rows_kind": "analysis_preflight_rows",
            "rows": [
                "finding_ready=stable|state=ready|note=findings",
                "evidence_ready=missing|state=blocked|note=attach_evidence",
                "gap_closed=open|state=blocked|note=attach_evidence",
                "review_ready=review_open|state=blocked|note=attach_evidence",
            ],
        },
        mode="apply",
    )
    assert blocker["reason_code"] == "analysis_evidence_missing"
    assert blocker["suggested_action"] == "task_review"
    assert blocker["remediation"] == "attach evidence and re-run analysis review before applying changes"


def test_analysis_module_multi_item_records_preserve_counts_and_series() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-AN-9",
            "contract_preset": "analysis",
            "objective": "Review multiple findings and supporting evidence.",
            "artifact_targets": ["reports/analysis/findings.md", "reports/analysis/appendix.md"],
        }
    )
    result = {
        "status": "ready",
        "summary": "analysis compiled",
        "actions": [
            "update reports/analysis/findings.md",
            "update reports/analysis/appendix.md",
        ],
        "cautions": [
            "keep caveat A open",
            "keep caveat B open",
        ],
        "evidence_refs": [
            "logs/findings_a.csv",
            "logs/findings_b.csv",
        ],
    }
    gate = worker_task_contract.derive_worker_task_module_gate(contract, result)
    profile = worker_task_contract.derive_worker_task_module_profile(contract, result, gate=gate)
    checklist = worker_task_contract.derive_worker_task_module_checklist(contract, result, gate=gate, profile=profile)
    items = worker_task_contract.derive_worker_task_module_items(
        contract, result, gate=gate, profile=profile, checklist=checklist
    )
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
        contract, result, gate=gate, profile=profile, checklist=checklist, items=items
    )
    records = worker_task_contract.derive_worker_task_module_records(
        contract,
        result,
        gate=gate,
        profile=profile,
        checklist=checklist,
        items=items,
        item_classes=item_classes,
    )

    assert items["items"] == [
        "finding:update reports/analysis/findings.md",
        "finding:update reports/analysis/appendix.md",
        "evidence:logs/findings_a.csv",
        "evidence:logs/findings_b.csv",
        "caveat:keep caveat A open",
        "caveat:keep caveat B open",
    ]
    assert item_classes["classes"] == ["finding=2", "evidence=2", "gap=0", "caveat=2"]
    assert records["records"] == [
        "finding_record=update reports/analysis/findings.md (+1)",
        "evidence_record=logs/findings_a.csv (+1)",
        "caveat_record=keep caveat A open (+1)",
    ]


def test_writing_module_multi_doc_records_preserve_counts_and_series() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-WR-9",
            "contract_preset": "writer",
            "objective": "Prepare multiple handoff docs.",
            "artifact_targets": ["docs/handoff/final.md", "docs/handoff/checklist.md"],
        }
    )
    result = {
        "status": "ready",
        "summary": "handoff prepared",
        "actions": ["update docs/handoff/final.md", "update docs/handoff/checklist.md"],
        "cautions": [],
        "evidence_refs": ["docs/handoff/final.md"],
    }
    gate = worker_task_contract.derive_worker_task_module_gate(contract, result)
    profile = worker_task_contract.derive_worker_task_module_profile(contract, result, gate=gate)
    checklist = worker_task_contract.derive_worker_task_module_checklist(contract, result, gate=gate, profile=profile)
    items = worker_task_contract.derive_worker_task_module_items(
        contract, result, gate=gate, profile=profile, checklist=checklist
    )
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
        contract, result, gate=gate, profile=profile, checklist=checklist, items=items
    )
    records = worker_task_contract.derive_worker_task_module_records(
        contract,
        result,
        gate=gate,
        profile=profile,
        checklist=checklist,
        items=items,
        item_classes=item_classes,
    )

    assert items["items"] == [
        "doc:docs/handoff/final.md",
        "doc:docs/handoff/checklist.md",
        "handoff:ready",
        "quality:ready",
    ]
    assert item_classes["classes"] == ["doc=2", "handoff=ready", "quality=ready"]
    assert records["records"] == [
        "doc_record=docs/handoff/final.md (+1)",
        "handoff_record=ready",
        "quality_record=ready",
    ]


def test_package_module_multi_artifact_records_preserve_counts_and_series() -> None:
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "task_label": "T-PKG-9",
            "contract_preset": "build",
            "objective": "Verify multiple release artifacts.",
            "artifact_targets": ["dist/release_bundle.zip", "dist/checksums.txt"],
        }
    )
    result = {
        "status": "ready",
        "summary": "package verified",
        "actions": ["update dist/release_bundle.zip", "update dist/checksums.txt"],
        "cautions": [],
        "evidence_refs": ["artifacts/release.sha256", "artifacts/checksums.txt"],
    }
    gate = worker_task_contract.derive_worker_task_module_gate(contract, result)
    profile = worker_task_contract.derive_worker_task_module_profile(contract, result, gate=gate)
    checklist = worker_task_contract.derive_worker_task_module_checklist(contract, result, gate=gate, profile=profile)
    items = worker_task_contract.derive_worker_task_module_items(
        contract, result, gate=gate, profile=profile, checklist=checklist
    )
    item_classes = worker_task_contract.derive_worker_task_module_item_classes(
        contract, result, gate=gate, profile=profile, checklist=checklist, items=items
    )
    records = worker_task_contract.derive_worker_task_module_records(
        contract,
        result,
        gate=gate,
        profile=profile,
        checklist=checklist,
        items=items,
        item_classes=item_classes,
    )

    assert items["items"][:3] == [
        "artifact:dist/release_bundle.zip",
        "artifact:dist/checksums.txt",
        "artifact:artifacts/release.sha256",
    ]
    assert items["items"][-2:] == ["verification:2", "integrity:ready"]
    assert item_classes["classes"] == ["artifact=4", "verification=2", "integrity=ready"]
    assert records["records"] == [
        "artifact_record=dist/release_bundle.zip (+3)",
        "verification_record=2",
        "apply_record=ready",
        "syncback_record=ready",
    ]
