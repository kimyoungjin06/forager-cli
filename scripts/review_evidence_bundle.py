#!/usr/bin/env python3
"""Review a TwinPaper evidence bundle before Offdesk model work."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any


CASE_NAME = "evidence_bundle_review"
VALID_DECISIONS = {"sufficient", "insufficient", "conflicting", "needs_operator"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def targeted_count(bundle: dict[str, Any], term: str) -> int:
    excerpts = bundle.get("runlog", {}).get("targeted_excerpts", {})
    rows = excerpts.get(term, []) if isinstance(excerpts, dict) else []
    return len(rows) if isinstance(rows, list) else 0


def artifact_count(bundle: dict[str, Any], group: str) -> int:
    records = bundle.get("artifacts", {}).get(group, [])
    return len(records) if isinstance(records, list) else 0


def review_bundle(bundle_path: pathlib.Path, bundle: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    missing_evidence: list[str] = []

    if bundle.get("kind") != "twinpaper_evidence_bundle":
        blockers.append("wrong_bundle_kind")
    if bundle.get("version") != 1:
        warnings.append("unexpected_bundle_version")

    source_files = bundle.get("source_files", {})
    for rel in ("AGENTS.md", "docs/operations/RunLog.md"):
        source = source_files.get(rel) if isinstance(source_files, dict) else None
        if not isinstance(source, dict) or source.get("exists") is not True:
            blockers.append(f"missing_source:{rel}")

    required_terms = ("no-option", "singlex", "validated_candidate", "p/q", "restart_stability")
    for term in required_terms:
        if targeted_count(bundle, term) == 0:
            missing_evidence.append(f"runlog_excerpt_missing:{term}")

    if targeted_count(bundle, "direction-review") == 0 and targeted_count(bundle, "direction_review") == 0:
        missing_evidence.append("runlog_excerpt_missing:direction_review")
    if targeted_count(bundle, "openexplore") == 0 and targeted_count(bundle, "open-explore") == 0:
        warnings.append("runlog_excerpt_missing:openexplore")
    if artifact_count(bundle, "direction_review") == 0:
        missing_evidence.append("artifact_missing:direction_review")
    if artifact_count(bundle, "paired_preset_summary") == 0:
        warnings.append("artifact_missing:paired_preset_summary")

    entrypoints = bundle.get("entrypoints", {})
    for rel in (
        "modules/03_regspec_machine/scripts/run_module_03.sh",
        "modules/03_regspec_machine/regspec_machine/orchestrator.py",
        "modules/03_regspec_machine/tests/test_orchestrator.py",
    ):
        entry = entrypoints.get(rel) if isinstance(entrypoints, dict) else None
        if not isinstance(entry, dict) or entry.get("exists") is not True:
            blockers.append(f"missing_entrypoint:{rel}")

    current_state = bundle.get("current_state", {})
    baseline_status = current_state.get("baseline_evidence_status") if isinstance(current_state, dict) else None
    if baseline_status in (None, "missing_or_not_in_bundle"):
        missing_evidence.append("baseline_evidence_status_missing")

    if blockers:
        decision = "insufficient"
    elif missing_evidence:
        decision = "insufficient"
    else:
        decision = "sufficient"

    return {
        "case": CASE_NAME,
        "passed": decision == "sufficient",
        "decision": decision,
        "review_stage_decision": decision,
        "reviewed_artifact": str(bundle_path),
        "baseline_evidence_status": baseline_status,
        "claim_status": current_state.get("claim_status") if isinstance(current_state, dict) else None,
        "blockers": blockers,
        "blocking_reasons": blockers + missing_evidence,
        "missing_evidence": missing_evidence,
        "warnings": warnings,
        "counterarguments": [
            "The bundle proves evidence availability, not final research correctness.",
            "Latest RunLog and metadata excerpts can still be superseded by uncommitted local artifacts outside the configured repo.",
        ],
    }


def write_markdown(path: pathlib.Path, result: dict[str, Any]) -> None:
    lines = [
        "# Evidence Bundle Review",
        "",
        f"- reviewed_artifact: `{result['reviewed_artifact']}`",
        f"- decision: `{result['decision']}`",
        f"- baseline_evidence_status: `{result.get('baseline_evidence_status')}`",
        f"- claim_status: `{result.get('claim_status')}`",
        "",
        "## Blocking Reasons",
        "",
        *(f"- {item}" for item in result["blocking_reasons"]),
        "",
        "## Warnings",
        "",
        *(f"- {item}" for item in result["warnings"]),
        "",
        "## Counterarguments",
        "",
        *(f"- {item}" for item in result["counterarguments"]),
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    bundle_path = args.bundle.expanduser().resolve()
    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bundle = load_json(bundle_path)
        if not isinstance(bundle, dict):
            raise ValueError("bundle is not a JSON object")
        result = review_bundle(bundle_path, bundle)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        result = {
            "case": CASE_NAME,
            "passed": False,
            "decision": "insufficient",
            "review_stage_decision": "insufficient",
            "reviewed_artifact": str(bundle_path),
            "baseline_evidence_status": None,
            "claim_status": None,
            "blockers": ["bundle_unreadable"],
            "blocking_reasons": ["bundle_unreadable"],
            "missing_evidence": [],
            "warnings": [],
            "counterarguments": [repr(error)],
        }
    decision = result["decision"]
    if decision not in VALID_DECISIONS:
        result["decision"] = "insufficient"
        result["review_stage_decision"] = "insufficient"
        result["passed"] = False
        result["blocking_reasons"].append("invalid_review_decision")
    artifact = {
        "created_at": utc_now(),
        "kind": "evidence_bundle_review",
        "bundle": str(bundle_path),
        "decision": result["decision"],
        "review_stage_decision": result["review_stage_decision"],
        "blocking_reasons": result["blocking_reasons"],
        "summary": {
            "total": 1,
            "passed": 1 if result["passed"] else 0,
            "failed": 0 if result["passed"] else 1,
            "decision_counts": {result["decision"]: 1},
        },
        "results": [result],
        "passed": result["passed"],
    }
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_path.with_name("EVIDENCE_REVIEW.md"), result)
    print(json.dumps({"out": str(out_path), "decision": result["decision"], "passed": result["passed"]}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
