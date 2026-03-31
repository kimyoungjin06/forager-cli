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
    src = str(prompt or "")
    outputs = ["review_report"]
    if _contains_any(src, ("changed files", "변경 파일")):
        outputs.append("changed_files")
    if _contains_any(src, ("severity", "심각도")):
        outputs.append("severity_findings")
    if _contains_any(src, ("test gap", "테스트 공백")):
        outputs.append("test_gaps")
    if _contains_any(src, ("uncertainty", "불확실성")):
        outputs.append("uncertainties")
    return _dedupe_rows(outputs, limit=8)


def _required_evidence(prompt: str) -> List[str]:
    src = str(prompt or "")
    evidence = ["git_diff_scope"]
    if _contains_any(src, ("severity", "심각도")):
        evidence.append("severity_rationale")
    if _contains_any(src, ("test gap", "테스트 공백")):
        evidence.append("test_coverage_gap")
    if _contains_any(src, ("uncertainty", "불확실성")):
        evidence.append("open_uncertainties")
    return _dedupe_rows(evidence, limit=8)


def extract_review_request_contract(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "").strip()
    diff_range_policy = _diff_range_policy(src)
    auth_scope_policy = _auth_scope_policy(src)
    fields: Dict[str, Any] = {}
    if diff_range_policy:
        fields["diff_range_policy"] = diff_range_policy
    if auth_scope_policy:
        fields["auth_scope_policy"] = auth_scope_policy
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
            "review_report": {
                "path": "review_report.md",
                "format": "markdown",
                "required_fields": ["severity findings", "changed files", "test gaps", "uncertainties"],
                "acceptance_notes": [
                    "review_report.md is the canonical review-only output artifact.",
                ],
            }
        },
    }
