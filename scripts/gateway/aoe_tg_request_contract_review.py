#!/usr/bin/env python3
"""Review request-contract extraction helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List


_REVIEW_MARKERS = (
    "review",
    "risk",
    "regression",
    "severity",
    "test gap",
    "uncertainty",
    "검토",
    "리스크",
    "회귀",
    "심각도",
    "테스트 공백",
    "불확실성",
)

_RECENT_PATCH_MARKERS = (
    "recent patch",
    "recent change",
    "latest patch",
    "최근 패치",
    "최근 변경",
    "최근 로그인 패치",
)

_RERUN_MARKERS = (
    "rerun",
    "retry",
    "do not mark done",
    "do not close as done",
    "if scope is missing",
    "if evidence is missing",
    "done으로 닫지 말고",
    "완료로 닫지 말고",
    "근거가 부족하면",
    "범위 근거가 부족하면",
    "재실행",
    "재시도",
)

_REVIEW_REPORT_SCOPE_FIELDS = (
    "canonical diff range",
    "excluded candidates",
    "dirty-worktree exclusions",
)

_REVIEW_REPORT_CORE_FIELDS = (
    "changed files",
    "severity findings",
    "test gaps",
    "uncertainties",
)

_REVIEW_REPORT_REQUIRED_FIELDS = list(_REVIEW_REPORT_SCOPE_FIELDS + _REVIEW_REPORT_CORE_FIELDS)
_AUTH_SCOPE_REVIEW_FIELDS = [
    "entrypoints",
    "caller-visible state transitions",
    "persisted session/token stores",
    "excluded paths",
    "helper-only boundary proof",
]
_REVIEW_EVIDENCE_CONTRACTS = {
    "git_diff_scope": {
        "path": "review_evidence/git_diff_scope.md",
        "format": "markdown",
        "required_fields": [
            "canonical diff range",
            "changed files",
            "excluded candidates",
            "dirty-worktree exclusions",
        ],
        "acceptance_notes": [
            "Select and record one canonical diff range from git-history candidates.",
            "List excluded candidates and dirty-worktree exclusions separately instead of folding them into the canonical range.",
        ],
    },
    "severity_rationale": {
        "path": "review_evidence/severity_rationale.md",
        "format": "markdown",
        "required_fields": [
            "entrypoints",
            "caller-visible state transitions",
            "persisted session/token stores",
            "excluded paths",
            "helper-only boundary proof",
            "affected files",
            "user-visible impact",
            "severity rationale",
        ],
        "acceptance_notes": [
            "Tie each severity rationale to concrete changed files or paths and user-visible impact evidence.",
            "Record auth/session boundary evidence inside review_evidence/severity_rationale.md; do not create a separate scope inventory artifact.",
        ],
    },
    "test_coverage_gap": {
        "path": "review_evidence/test_coverage_gap.md",
        "format": "markdown",
        "required_fields": [
            "missing coverage",
            "unchecked paths",
        ],
        "acceptance_notes": [
            "Keep missing coverage and unchecked paths explicit instead of implying they were reviewed.",
        ],
    },
    "open_uncertainties": {
        "path": "review_evidence/open_uncertainties.md",
        "format": "markdown",
        "required_fields": [
            "unresolved assumptions",
            "excluded paths",
        ],
        "acceptance_notes": [
            "Record unresolved assumptions and excluded paths with reasons so rerun decisions stay inspectable.",
        ],
    },
}


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _contains_any(text: str, markers: List[str] | tuple[str, ...]) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    return any(str(marker).strip().lower() in low for marker in markers if str(marker).strip())


def _dedupe_rows(rows: List[str], *, limit: int = 8) -> List[str]:
    out: List[str] = []
    for item in rows:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit))]


def review_request_contract_matches(prompt: str) -> bool:
    return _contains_any(prompt, _REVIEW_MARKERS)


def _extract_scope_anchor_terms(prompt: str) -> List[str]:
    src = str(prompt or "")
    terms: List[str] = []
    for pattern, token in (
        (r"\blogin\b", "login"),
        (r"\bauth\b", "auth"),
        (r"\bsession\b", "session"),
        (r"\btoken\b", "token"),
        (r"로그인", "login"),
        (r"인증", "auth"),
        (r"세션", "session"),
        (r"토큰", "token"),
    ):
        if re.search(pattern, src, flags=re.IGNORECASE):
            terms.append(token)
    return _dedupe_rows(terms, limit=6)


def _diff_range_policy(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "")
    anchors = _extract_scope_anchor_terms(src)
    mentions_recent = _contains_any(src, _RECENT_PATCH_MARKERS) or bool(anchors)
    if not mentions_recent:
        return {}
    return {
        "scope_source": "git-history",
        "candidate_selection_rule": "enumerate-recent-relevant-commits-then-pick-one-canonical-range",
        "candidate_match_terms": anchors or ["recent_patch"],
        "dirty_worktree_policy": "exclude-uncommitted-from-canonical-range-and-record-separately",
        "range_strategy": "single-canonical-diff-range-with-excluded-candidates",
        "record_excluded_candidates": True,
    }


def _auth_scope_policy(prompt: str) -> Dict[str, Any]:
    anchors = _extract_scope_anchor_terms(prompt)
    if not any(token in {"login", "auth", "session", "token"} for token in anchors):
        return {}
    return {
        "entrypoint_required": True,
        "caller_visible_state_required": True,
        "persisted_store_required": True,
        "record_excluded_paths": True,
        "helper_only_boundary_requires_proof": True,
    }


def _required_outputs(prompt: str) -> List[str]:
    return ["review_report"]


def _required_evidence(prompt: str) -> List[str]:
    src = str(prompt or "")
    evidence = ["git_diff_scope"]
    if _contains_any(src, ("severity", "심각도")):
        evidence.append("severity_rationale")
    if _contains_any(src, ("test gap", "테스트 공백")):
        evidence.append("test_coverage_gap")
    if _contains_any(src, ("uncertainty", "uncertainties", "불확실성")):
        evidence.append("open_uncertainties")
    return _dedupe_rows(evidence, limit=8)


def _execution_outputs(prompt: str) -> List[str]:
    return _required_evidence(prompt)


def _quality_gate_policy(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "")
    if not _contains_any(src, _RERUN_MARKERS):
        return {}
    required_sections = list(_REVIEW_REPORT_SCOPE_FIELDS)
    if _auth_scope_policy(src):
        required_sections.extend([field for field in _AUTH_SCOPE_REVIEW_FIELDS if field not in required_sections])
    required_sections.extend([field for field in _REVIEW_REPORT_CORE_FIELDS if field not in required_sections])
    return {
        "branch_on_failure": "rerun",
        "done_forbidden_on_failure": True,
        "diff_scope_gate": True,
        "section_completeness_gate": True,
        "required_sections": required_sections,
        "required_evidence": ["git_diff_scope", "severity_rationale", "test_coverage_gap", "open_uncertainties"],
        "failure_reasons": [
            "diff_scope_missing",
            "excluded_candidates_missing",
            "dirty_worktree_exclusions_missing",
            "severity_evidence_missing",
            "test_gap_missing",
            "uncertainty_missing",
        ],
    }


def extract_review_request_contract(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "").strip()
    diff_range_policy = _diff_range_policy(src)
    auth_scope_policy = _auth_scope_policy(src)
    quality_gate_policy = _quality_gate_policy(src)
    review_report_required_fields = list(_REVIEW_REPORT_SCOPE_FIELDS)
    if auth_scope_policy:
        review_report_required_fields.extend([field for field in _AUTH_SCOPE_REVIEW_FIELDS if field not in review_report_required_fields])
    review_report_required_fields.extend([field for field in _REVIEW_REPORT_CORE_FIELDS if field not in review_report_required_fields])
    fields: Dict[str, Any] = {}
    if diff_range_policy:
        fields["diff_range_policy"] = diff_range_policy
    if auth_scope_policy:
        fields["auth_scope_policy"] = auth_scope_policy
        fields["auth_scope_output_policy"] = {
            "record_within_review_report": True,
            "separate_scope_inventory_forbidden": True,
        }
    if quality_gate_policy:
        fields["quality_gate_policy"] = quality_gate_policy
    fields["deliverable_policy"] = {
        "execution_outputs": _execution_outputs(src),
        "review_outputs": ["review_report"],
    }
    anchors = _extract_scope_anchor_terms(src)
    if anchors:
        fields["scope_anchor_terms"] = anchors

    return {
        "contract_type": "review",
        "preset": "review",
        "status": "complete",
        "readonly": True,
        "objective": _trim(src, 240),
        "source_prompt": _trim(src, 2000),
        "fields": fields,
        "required_outputs": _required_outputs(src),
        "required_evidence": _required_evidence(src),
        "missing_fields": [],
        "ambiguity_notes": [],
        "summary": "review | text-first",
        "artifact_contracts": {
            **{
                key: {
                    **value,
                    "acceptance_notes": [
                        f"{value['path']} is an execution-owned evidence artifact for downstream review gating.",
                        *list(value.get("acceptance_notes") or []),
                    ],
                }
                for key, value in _REVIEW_EVIDENCE_CONTRACTS.items()
                if key in _execution_outputs(src)
            },
            "review_report": {
                "path": "review_report.md",
                "format": "markdown",
                "required_fields": review_report_required_fields,
                "acceptance_notes": [
                    "review_report.md is the canonical review-only output artifact.",
                    "Auth/session scope evidence, when required, stays inside review_report.md; do not create a separate scope inventory artifact.",
                ],
            }
        },
    }
