#!/usr/bin/env python3
"""Mixed request-contract extraction helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List


_HANDOFF_MARKERS = (
    "handoff",
    "handoff 문서",
    "operator handoff",
    "운영 handoff",
    "인수인계",
    "체크리스트",
    "문서",
    "documentation",
    "docs",
)

_REVIEW_NOTE_MARKERS = (
    "reviewer note",
    "review note",
    "reviewer_note",
    "review note",
    "리뷰 노트",
    "검토 노트",
    "review findings",
    "review 결과",
)

_TEST_MARKERS = (
    "regression test",
    "tests",
    "test evidence",
    "회귀 테스트",
    "테스트",
    "검증",
)

_WORK_RESULT_MARKERS = (
    "fix",
    "patch",
    "implement",
    "implementation",
    "code",
    "session_expired",
    "token",
    "login",
    "로그인",
    "수정",
    "구현",
    "패치",
    "토큰",
    "세션",
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


def mixed_request_contract_matches(prompt: str) -> bool:
    src = str(prompt or "")
    return _contains_any(src, _HANDOFF_MARKERS) and _contains_any(src, _REVIEW_NOTE_MARKERS)


def _required_outputs(prompt: str, *, auth_failure_policy: Dict[str, Any] | None = None) -> List[str]:
    src = str(prompt or "")
    outputs = ["work_result"]
    if isinstance(auth_failure_policy, dict) and auth_failure_policy:
        outputs.append("scope_inventory")
    if _contains_any(src, _HANDOFF_MARKERS):
        outputs.append("handoff_doc")
    if _contains_any(src, _REVIEW_NOTE_MARKERS):
        outputs.append("reviewer_note")
    return _dedupe_rows(outputs, limit=8)


def _required_evidence(prompt: str) -> List[str]:
    src = str(prompt or "")
    evidence = ["implementation_delta"]
    if _contains_any(src, _TEST_MARKERS):
        evidence.append("regression_test_evidence")
    if _contains_any(src, _HANDOFF_MARKERS):
        evidence.append("operator_handoff")
    if _contains_any(src, _REVIEW_NOTE_MARKERS):
        evidence.append("review_findings")
    return _dedupe_rows(evidence, limit=8)


def _auth_failure_policy(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "").strip()
    low = src.lower()
    if not _contains_any(low, ("login", "auth", "session", "token", "로그인", "인증", "세션", "토큰")):
        return {}

    codes: List[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", src):
        folded = token.strip().lower()
        if (
            folded not in codes
            and any(marker in folded for marker in ("expired", "invalid", "unauth", "auth", "session", "token", "login"))
        ):
            codes.append(folded)
    codes = codes[:6]
    if not codes:
        return {}

    return {
        "target_failure_codes": codes,
        "mutation_scope": "target-failure-codes-only",
        "require_negative_case_evidence": True,
        "negative_case_policy": "non-target-failures-preserve-existing-auth-state",
    }


def extract_mixed_request_contract(prompt: str) -> Dict[str, Any]:
    src = str(prompt or "").strip()
    auth_failure_policy = _auth_failure_policy(src)
    outputs = _required_outputs(src, auth_failure_policy=auth_failure_policy)
    writer_outputs = [item for item in outputs if item in {"handoff_doc"}]
    review_outputs = [item for item in outputs if item in {"reviewer_note"}]
    execution_outputs = ["work_result"]
    if auth_failure_policy:
        execution_outputs = ["scope_inventory", "work_result"]

    fields: Dict[str, Any] = {
        "deliverable_policy": {
            "work_result_required": True,
            "execution_outputs": execution_outputs,
            "writer_outputs": writer_outputs,
            "review_outputs": review_outputs,
        }
    }
    if auth_failure_policy:
        fields["auth_failure_policy"] = auth_failure_policy

    artifact_contracts: Dict[str, Dict[str, Any]] = {}
    if auth_failure_policy:
        artifact_contracts["scope_inventory"] = {
            "path": "docs/analysis/auth_scope_inventory.md",
            "format": "markdown",
            "required_fields": [
                "public_failure_entrypoints[]",
                "caller_visible_auth_state_surfaces[]",
                "persisted_token_or_session_store_paths[]",
                "excluded_paths_with_reasons[]",
                "target_failure_codes[]",
                "non_target_failures_preserve_existing_auth_state",
                "single_helper_boundary_proof_when_used",
            ],
            "acceptance_notes": [
                "auth_scope_inventory.md is the canonical scope-tracing execution artifact for auth/session mixed requests.",
            ],
        }
    artifact_contracts["work_result"] = {
        "format": "implementation_delta",
        "required_fields": ["changed files", "behavior delta", "test evidence"],
        "acceptance_notes": [
            "work_result is the canonical execution-lane deliverable for mixed requests.",
        ],
    }
    if "handoff_doc" in outputs:
        artifact_contracts["handoff_doc"] = {
            "path": "docs/handoff/operator_handoff.md",
            "format": "markdown",
            "required_fields": ["change summary", "validation status", "operator follow-ups"],
            "acceptance_notes": [
                "operator_handoff.md is the canonical writer-lane handoff artifact.",
            ],
        }
    if "reviewer_note" in outputs:
        artifact_contracts["reviewer_note"] = {
            "path": "docs/reviews/reviewer_note.md",
            "format": "markdown",
            "required_fields": ["severity findings", "regression risks", "test gaps", "uncertainties"],
            "acceptance_notes": [
                "reviewer_note.md is the canonical review-lane output artifact.",
            ],
        }

    summary_outputs = ",".join(outputs[:4]) if outputs else "work_result"
    return {
        "contract_type": "mixed",
        "preset": "mixed",
        "status": "complete",
        "readonly": False,
        "objective": _trim(src, 240),
        "source_prompt": _trim(src, 2000),
        "fields": fields,
        "required_outputs": outputs,
        "required_evidence": _dedupe_rows(
            _required_evidence(src) + (["auth_scope_inventory"] if auth_failure_policy else []),
            limit=8,
        ),
        "missing_fields": [],
        "ambiguity_notes": [],
        "summary": f"mixed | outputs={summary_outputs}",
        "artifact_contracts": artifact_contracts,
    }
