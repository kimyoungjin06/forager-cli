#!/usr/bin/env python3
"""Deterministically review a completed TwinPaper Offdesk workload result."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from typing import Any


CASE_REQUIRING_REFS = {
    "research_reportability_status_json",
    "critique_open_explore_direction_change",
}
EXPECTED_COMMANDS = (
    "modules/03_regspec_machine/scripts/run_module_03.sh plan",
    "modules/03_regspec_machine/scripts/run_module_03.sh single-nooption --exec",
    "modules/03_regspec_machine/scripts/run_module_03.sh single-singlex --exec",
)
CANONICAL_BLOCKING_ANCHOR_IDS = {
    "primary_objective_gate",
}
CANONICAL_BLOCKING_ANCHOR_STATUSES = {
    "failed",
    "missing",
    "unknown",
}
CANONICAL_BLOCKING_REASON_CODES = {
    "executed_primary_gate_failed",
    "missing_evidence",
    "insufficient_restart_stability",
    "insufficient_pq_evidence",
    "insufficient_validated_candidate",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, help="Write review JSON here.")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def default_out_path(result_path: pathlib.Path) -> pathlib.Path:
    return result_path.parent / "result_review" / "results.json"


def add_finding(
    findings: list[dict[str, Any]],
    *,
    severity: str,
    category: str,
    message: str,
    iteration: int | None = None,
    case: str | None = None,
    response_path: str | None = None,
    evidence: str | None = None,
    suggested_action: str | None = None,
) -> None:
    findings.append(
        {
            "severity": severity,
            "category": category,
            "iteration": iteration,
            "case": case,
            "message": message,
            "response_path": response_path,
            "evidence": evidence,
            "suggested_action": suggested_action,
        }
    )


def load_evidence_text(summary: dict[str, Any]) -> tuple[pathlib.Path | None, str]:
    bundle_value = summary.get("evidence_bundle_path")
    if not bundle_value:
        return None, ""
    bundle_path = pathlib.Path(str(bundle_value))
    parts: list[str] = []
    if bundle_path.exists():
        parts.append(read_text(bundle_path))
    markdown_path = bundle_path.with_name("EVIDENCE.md")
    if markdown_path.exists():
        parts.append(read_text(markdown_path))
    return bundle_path, "\n".join(parts).lower()


def has_inline_evidence_refs(text: str) -> bool:
    patterns = (
        r"\bevidence_refs\b",
        r"\bsource_lines\b",
        r"\bsources?\b",
        r"\brunlog\s+l\d+\b",
        r"\bl\d{2,}\b",
        r"\bdata/metadata/",
    )
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def distinguishes_exploratory_from_promotion_gate(text: str) -> bool:
    """Return true when the response keeps exploratory evidence separate from gate evidence."""
    lowered = text.lower()
    has_exploratory_signal = any(
        marker in lowered
        for marker in (
            "exploratory",
            "open-explore signals",
            "validated_candidate",
            "p/q",
            "best_p_validation",
        )
    )
    has_gate_signal = any(
        marker in lowered
        for marker in (
            "promotion-gate comparability",
            "promotion-gate comparable",
            "promotion gate comparable",
            "promotion-gate evidence",
            "promotion comparable",
            "not promotion comparable",
            "promotion-ready",
            "promotion-readiness",
            "primary_objective_gate",
            "primary objective gate",
            "same primary objective",
        )
    )
    has_comparability_signal = any(
        marker in lowered
        for marker in (
            "comparability gap",
            "comparability gaps",
            "non-comparable",
            "not directly comparable",
            "directly comparable",
            "configuration drift",
            "gate-equivalent",
            "identical gate",
            "same gate",
            "same primary objective",
            "same threshold",
            "same thresholds",
            "identical threshold",
            "identical thresholds",
            "same criteria",
            "identical criteria",
            "restart-comparable",
            "restart comparable",
            "restart comparability",
        )
    )
    return has_exploratory_signal and has_gate_signal and has_comparability_signal


def response_for_record(record: dict[str, Any], findings: list[dict[str, Any]]) -> str:
    response_path_value = record.get("response_path")
    if not response_path_value:
        add_finding(
            findings,
            severity="blocker",
            category="missing_response_path",
            message="Record has no response_path.",
            iteration=record.get("iteration"),
            case=record.get("case"),
        )
        return ""
    response_path = pathlib.Path(str(response_path_value))
    if not response_path.exists():
        add_finding(
            findings,
            severity="blocker",
            category="missing_response_file",
            message="response_path does not exist.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=str(response_path),
        )
        return ""
    raw_path_value = record.get("raw_response_path")
    if raw_path_value and not pathlib.Path(str(raw_path_value)).exists():
        add_finding(
            findings,
            severity="warning",
            category="missing_raw_response_file",
            message="raw_response_path does not exist.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=str(response_path),
            evidence=str(raw_path_value),
        )
    return read_text(response_path)


def review_command_case(record: dict[str, Any], response: str, findings: list[dict[str, Any]]) -> None:
    lines = [line.strip() for line in response.splitlines() if line.strip()]
    response_path = record.get("response_path")
    if len(lines) != 3:
        add_finding(
            findings,
            severity="warning",
            category="command_shape_drift",
            message="Module03 command response should contain exactly three non-empty lines.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
            evidence=f"line_count={len(lines)}",
            suggested_action="Use a line-exact command contract for operator-command cases.",
        )
        return
    normalized = [line.removeprefix("./") for line in lines]
    if tuple(normalized) != EXPECTED_COMMANDS:
        add_finding(
            findings,
            severity="warning",
            category="command_exact_contract_mismatch",
            message="Command response is runnable but not equal to the canonical command contract.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
            evidence=json.dumps({"actual": lines, "expected": EXPECTED_COMMANDS}, ensure_ascii=False),
            suggested_action="Require exact command lines instead of substring anchors.",
        )
    if any(line.startswith("./") for line in lines):
        add_finding(
            findings,
            severity="warning",
            category="command_noncanonical_relative_prefix",
            message="Command uses './modules/...' while the canonical contract uses repo-relative 'modules/...'.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
            evidence="\n".join(lines),
            suggested_action="For operator surfaces, make canonical command formatting exact.",
        )


def review_json_case(record: dict[str, Any], findings: list[dict[str, Any]]) -> None:
    parsed = record.get("json")
    response_path = record.get("response_path")
    if not isinstance(parsed, dict):
        add_finding(
            findings,
            severity="blocker",
            category="json_case_missing_parsed_json",
            message="JSON-format case passed without parsed JSON in the record.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
        )
        return
    if parsed.get("evidence_bundle_used") is not True and record.get("case") != "code_cancel_idempotency_patch_plan_json":
        add_finding(
            findings,
            severity="warning",
            category="evidence_bundle_ack_missing",
            message="JSON case does not explicitly acknowledge evidence_bundle_used=true.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
        )
    if record.get("case") == "research_reportability_status_json":
        review_reportability_blocking_anchors(record, parsed, findings)


def review_reportability_blocking_anchors(
    record: dict[str, Any],
    parsed: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    anchors = parsed.get("blocking_anchors")
    response_path = record.get("response_path")
    if parsed.get("reportability_contract_schema") != "reportability_contract.v1":
        add_finding(
            findings,
            severity="blocker",
            category="reportability_contract_schema_missing",
            message="Reportability JSON does not declare reportability_contract.v1.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
            suggested_action="Emit reportability_contract_schema before relying on typed blocking_anchors.",
        )
    if not isinstance(anchors, list):
        add_finding(
            findings,
            severity="blocker",
            category="reportability_blocking_anchors_missing",
            message="Reportability JSON lacks machine-readable blocking_anchors.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
            suggested_action="Emit canonical blocking_anchors separately from human-readable blocking_evidence.",
        )
        return

    primary_gate_anchor: dict[str, Any] | None = None
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            add_finding(
                findings,
                severity="blocker",
                category="reportability_blocking_anchor_invalid",
                message="Reportability blocking anchor is not an object.",
                iteration=record.get("iteration"),
                case=record.get("case"),
                response_path=response_path,
                evidence=f"blocking_anchors[{index}]",
            )
            continue
        anchor_id = anchor.get("id")
        status = anchor.get("status")
        reason_code = anchor.get("reason_code")
        evidence_refs = anchor.get("evidence_refs")
        invalid_parts = []
        if anchor_id not in CANONICAL_BLOCKING_ANCHOR_IDS:
            invalid_parts.append(f"id={anchor_id}")
        if status not in CANONICAL_BLOCKING_ANCHOR_STATUSES:
            invalid_parts.append(f"status={status}")
        if reason_code not in CANONICAL_BLOCKING_REASON_CODES:
            invalid_parts.append(f"reason_code={reason_code}")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            invalid_parts.append("evidence_refs")
        if invalid_parts:
            add_finding(
                findings,
                severity="blocker",
                category="reportability_blocking_anchor_invalid",
                message="Reportability blocking anchor is not canonical.",
                iteration=record.get("iteration"),
                case=record.get("case"),
                response_path=response_path,
                evidence=f"blocking_anchors[{index}]: {', '.join(invalid_parts)}",
                suggested_action="Use exact canonical id/status/reason_code values and non-empty evidence_refs.",
            )
        if anchor_id == "primary_objective_gate":
            primary_gate_anchor = anchor

    requires_primary_gate_failure = (
        parsed.get("baseline_evidence_status") == "executed_primary_gate_failed"
    )
    if requires_primary_gate_failure and primary_gate_anchor is None:
        add_finding(
            findings,
            severity="blocker",
            category="reportability_primary_gate_anchor_missing",
            message="Reportability JSON says the primary gate failed but lacks the primary_objective_gate anchor.",
            iteration=record.get("iteration"),
            case=record.get("case"),
            response_path=response_path,
        )


def review_research_or_critique(
    record: dict[str, Any],
    response: str,
    evidence_text: str,
    findings: list[dict[str, Any]],
) -> None:
    case = str(record.get("case"))
    response_path = record.get("response_path")
    lowered = response.lower()
    if not has_inline_evidence_refs(response):
        add_finding(
            findings,
            severity="warning",
            category="missing_inline_evidence_refs",
            message="Research/critique response has no explicit evidence refs or source lines.",
            iteration=record.get("iteration"),
            case=case,
            response_path=response_path,
            suggested_action="Require source_lines or evidence_refs for claims derived from the evidence bundle.",
        )
    if "open-explore" in lowered or "openexplore" in lowered:
        distinguishes_gate_scope = distinguishes_exploratory_from_promotion_gate(response)
        openexplore_has_validated = "openexplore" in evidence_text and "validated_candidate" in evidence_text
        says_missing_validated = re.search(
            r"open-?explore.{0,220}(?:no|lack|without).{0,140}validated_candidate",
            lowered,
            flags=re.DOTALL,
        )
        if openexplore_has_validated and says_missing_validated and not distinguishes_gate_scope:
            add_finding(
                findings,
                severity="warning",
                category="possible_evidence_conflict",
                message="Response may conflate missing promotion-gate evidence with missing open-explore validated_candidate evidence.",
                iteration=record.get("iteration"),
                case=case,
                response_path=response_path,
                evidence="Evidence bundle contains openexplore and validated_candidate material.",
                suggested_action="Distinguish exploratory validated candidates from promotion-ready direction-review gates.",
            )
        says_missing_pq = re.search(
            r"open-?explore.{0,220}(?:no|lack|without|does not include).{0,140}(?:p/q|p-value|q-value|p validation|q validation|q_value|best_p)",
            lowered,
            flags=re.DOTALL,
        )
        if (
            says_missing_pq
            and ("q≈" in evidence_text or "p/q" in evidence_text or "best_p_validation" in evidence_text)
            and not distinguishes_gate_scope
        ):
            add_finding(
                findings,
                severity="warning",
                category="possible_evidence_conflict",
                message="Response may overstate absence of open-explore p/q evidence.",
                iteration=record.get("iteration"),
                case=case,
                response_path=response_path,
                evidence="Evidence bundle contains p/q or q-value material.",
                suggested_action="State whether p/q evidence is absent, incomplete, or merely not promotion-gate comparable.",
            )
    if "no regression test coverage" in lowered and "test_api.py" in lowered:
        add_finding(
            findings,
            severity="warning",
            category="scope_overreach",
            message="Response cites regression coverage while also saying no regression coverage exists.",
            iteration=record.get("iteration"),
            case=case,
            response_path=response_path,
            suggested_action="Separate general governance regression tests from open-explore-specific coverage.",
        )


def learning_candidates(findings: list[dict[str, Any]], result_path: pathlib.Path) -> list[dict[str, Any]]:
    categories = {str(finding.get("category")) for finding in findings}
    candidates: list[dict[str, Any]] = []
    if "missing_inline_evidence_refs" in categories:
        candidates.append(
            {
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "twinpaper",
                "agent_modes": ["writing", "critique"],
                "claim": "TwinPaper research and critique outputs can pass anchor checks while lacking source refs.",
                "ai_instruction": "For TwinPaper evidence-bundle work, include source_lines or evidence_refs for nontrivial claims.",
                "human_summary": "A 12/12 Offdesk run was usable but did not consistently expose citation-level evidence in prose outputs.",
                "evidence_refs": [str(result_path)],
            }
        )
    if "command_noncanonical_relative_prefix" in categories or "command_exact_contract_mismatch" in categories:
        candidates.append(
            {
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "twinpaper",
                "agent_modes": ["development"],
                "claim": "Runnable command answers can still drift from the canonical operator command surface.",
                "ai_instruction": "Emit exact repo-relative Module03 command lines when asked for operator commands.",
                "human_summary": "The workload accepted './modules/...' commands, but the canonical operator format is 'modules/...'.",
                "evidence_refs": [str(result_path)],
            }
        )
    if "possible_evidence_conflict" in categories:
        candidates.append(
            {
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "twinpaper",
                "agent_modes": ["critique", "writing"],
                "claim": "Open-explore critique can confuse exploratory evidence with promotion-ready direction-review evidence.",
                "ai_instruction": "When discussing open-explore, distinguish exploratory validated candidates from promotion-gate or primary-objective evidence.",
                "human_summary": "Post-run review found language that may overstate missing open-explore evidence instead of saying it is not promotion comparable.",
                "evidence_refs": [str(result_path)],
            }
        )
    return candidates


def evaluate_result(result_path: pathlib.Path, result: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
        add_finding(
            findings,
            severity="blocker",
            category="missing_summary",
            message="Result JSON has no summary object.",
        )
    records = result.get("records", [])
    if not isinstance(records, list):
        records = []
        add_finding(
            findings,
            severity="blocker",
            category="missing_records",
            message="Result JSON has no records array.",
        )

    bundle_path, evidence_text = load_evidence_text(summary)
    if bundle_path is None or not bundle_path.exists():
        add_finding(
            findings,
            severity="blocker",
            category="missing_evidence_bundle",
            message="Summary evidence_bundle_path is missing or unreadable.",
            evidence=str(bundle_path) if bundle_path else None,
        )
    if summary.get("evidence_review_decision") != "sufficient":
        add_finding(
            findings,
            severity="blocker",
            category="evidence_review_not_sufficient",
            message="Completed run did not record a sufficient evidence review.",
            evidence=str(summary.get("evidence_review_decision")),
        )

    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("passed") is not True:
            add_finding(
                findings,
                severity="blocker",
                category="workload_case_failed",
                message="A workload case failed before post-run review.",
                iteration=record.get("iteration"),
                case=record.get("case"),
                response_path=record.get("response_path"),
                evidence=json.dumps(
                    {
                        "failure_category": record.get("failure_category"),
                        "must_missing": record.get("must_missing"),
                        "json_failures": record.get("json_failures"),
                    },
                    ensure_ascii=False,
                ),
            )
        response = response_for_record(record, findings)
        if record.get("format_json") is True:
            review_json_case(record, findings)
        if record.get("case") == "module03_root_entrypoint":
            review_command_case(record, response, findings)
        if record.get("case") in CASE_REQUIRING_REFS:
            review_research_or_critique(record, response, evidence_text, findings)

    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding.get("severity"))
        category = str(finding.get("category"))
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
    blocker_count = severity_counts.get("blocker", 0)
    warning_count = severity_counts.get("warning", 0)
    if blocker_count:
        decision = "blocked"
        next_action = "Fix blockers before treating this run as usable."
    elif warning_count:
        decision = "usable_with_followups"
        next_action = "Treat the run as usable, but convert warnings into prompt or wiki improvements."
    else:
        decision = "clean"
        next_action = "Run is clean under deterministic post-run review."

    return {
        "created_at": utc_now(),
        "kind": "twinpaper_offdesk_result_review",
        "version": 1,
        "result_path": str(result_path),
        "evidence_bundle_path": str(bundle_path) if bundle_path else None,
        "decision": decision,
        "passed": blocker_count == 0,
        "summary": {
            "result_total": summary.get("total"),
            "result_passed": summary.get("passed"),
            "result_failed": summary.get("failed"),
            "result_verdict": (summary.get("assessment") or {}).get("overall_verdict")
            if isinstance(summary.get("assessment"), dict)
            else None,
            "severity_counts": severity_counts,
            "category_counts": category_counts,
            "next_action": next_action,
        },
        "findings": findings,
        "learning_candidates": learning_candidates(findings, result_path),
    }


def write_markdown(path: pathlib.Path, review: dict[str, Any]) -> None:
    lines = [
        "# TwinPaper Offdesk Result Review",
        "",
        f"- result_path: `{review['result_path']}`",
        f"- evidence_bundle_path: `{review.get('evidence_bundle_path')}`",
        f"- decision: `{review['decision']}`",
        f"- passed: `{review['passed']}`",
        f"- next_action: `{review['summary']['next_action']}`",
        "",
        "## Finding Summary",
        "",
        "```json",
        json.dumps(review["summary"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Findings",
        "",
    ]
    if review["findings"]:
        for finding in review["findings"]:
            prefix = f"- {finding['severity']} / {finding['category']}"
            location = ""
            if finding.get("iteration") is not None:
                location = f" iteration={finding['iteration']} case={finding.get('case')}"
            lines.append(f"{prefix}{location}: {finding['message']}")
            if finding.get("suggested_action"):
                lines.append(f"  suggested_action: {finding['suggested_action']}")
    else:
        lines.append("- No findings.")
    lines.extend(["", "## Learning Candidates", ""])
    if review["learning_candidates"]:
        for candidate in review["learning_candidates"]:
            lines.append(f"- {candidate['kind']} / {candidate['scope']}:{candidate['scope_ref']}: {candidate['claim']}")
            lines.append(f"  instruction: {candidate['ai_instruction']}")
    else:
        lines.append("- None.")
    write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    result_path = args.result.expanduser().resolve()
    out_path = (args.out or default_out_path(result_path)).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = load_json(result_path)
        if not isinstance(result, dict):
            raise ValueError("result is not a JSON object")
        review = evaluate_result(result_path, result)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        review = {
            "created_at": utc_now(),
            "kind": "twinpaper_offdesk_result_review",
            "version": 1,
            "result_path": str(result_path),
            "evidence_bundle_path": None,
            "decision": "blocked",
            "passed": False,
            "summary": {
                "severity_counts": {"blocker": 1},
                "category_counts": {"result_unreadable": 1},
                "next_action": "Fix unreadable result artifact before review.",
            },
            "findings": [
                {
                    "severity": "blocker",
                    "category": "result_unreadable",
                    "iteration": None,
                    "case": None,
                    "message": repr(error),
                    "response_path": None,
                    "evidence": None,
                    "suggested_action": None,
                }
            ],
            "learning_candidates": [],
        }
    write_text(out_path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    write_markdown(out_path.with_name("RESULT_REVIEW.md"), review)
    print(
        json.dumps(
            {
                "out": str(out_path),
                "decision": review["decision"],
                "passed": review["passed"],
                "findings": len(review["findings"]),
            },
            ensure_ascii=False,
        )
    )
    return 0 if review["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
