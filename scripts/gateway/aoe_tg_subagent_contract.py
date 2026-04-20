#!/usr/bin/env python3
"""Structured support-lane contracts for bounded subagent work."""

from __future__ import annotations

from typing import Any, Dict, List


SUBAGENT_CONTRACT_VERSION = "2026-04-20.v1"
SUBAGENT_RESULT_VERSION = "2026-04-20.v1"
SUBAGENT_KINDS = {"general_research"}
SUBAGENT_CONFIDENCE = {"low", "medium", "high"}


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _safe_token(raw: Any, default: str = "runtime") -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(raw or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return token or default


def _normalize_rows(raw: Any, *, limit: int = 8, item_limit: int = 160) -> List[str]:
    rows = raw if isinstance(raw, list) else []
    out: List[str] = []
    for row in rows:
        token = _trim(row, item_limit)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit or 1))]


def build_general_research_subagent_contract(
    *,
    request_id: Any = "",
    task_ref: Any = "",
    objective: Any,
    backend_descriptor: Dict[str, Any] | None = None,
    relevant_doc_ids: List[str] | None = None,
    relevant_doc_paths: List[str] | None = None,
    context_pack_profile: Any = "",
    context_pack_summary: Any = "",
    vendor_patterns: List[str] | None = None,
) -> Dict[str, Any]:
    request_token = _safe_token(request_id or task_ref, "runtime")
    backend = backend_descriptor if isinstance(backend_descriptor, dict) else {}
    docs = _normalize_rows(relevant_doc_ids or relevant_doc_paths, limit=6, item_limit=128)
    patterns = _normalize_rows(vendor_patterns, limit=8, item_limit=64)
    artifact_path = f"harness_authoring/subagents/{request_token}-general-research.json"
    contract = {
        "version": SUBAGENT_CONTRACT_VERSION,
        "subagent_kind": "general_research",
        "execution_mode": "read_heavy_support",
        "summary": (
            f"general_research | profile={_trim(context_pack_profile, 64) or '-'} | "
            f"docs={len(docs)} | artifact={artifact_path}"
        ),
        "objective": _trim(objective, 320) or "-",
        "input_scope": {
            "context_pack_profile": _trim(context_pack_profile, 64) or "-",
            "context_pack_summary": _trim(context_pack_summary, 320) or "-",
            "doc_refs": docs,
            "vendor_patterns": patterns,
        },
        "ownership": {
            "parent_task": "dispatch_and_gate_owner",
            "subagent": "bounded_evidence_gathering",
        },
        "backend": {
            "backend_kind": _trim((backend or {}).get("backend_kind"), 64) or "filesystem",
            "summary": _trim((backend or {}).get("summary"), 240) or "backend=filesystem",
        },
        "output_artifact": {
            "version": SUBAGENT_RESULT_VERSION,
            "path": artifact_path,
            "required_fields": [
                "summary",
                "confidence",
                "sources",
                "key_findings",
                "blocking_issues",
                "recommended_next_step",
                "artifact_refs",
            ],
        },
        "rules": [
            "read_only_repo_and_docs",
            "no_dispatch_or_apply_decisions",
            "return_structured_support_artifact",
        ],
    }
    return contract


def summarize_subagent_contract(contract: Dict[str, Any] | None) -> str:
    item = contract if isinstance(contract, dict) else {}
    artifact = item.get("output_artifact") if isinstance(item.get("output_artifact"), dict) else {}
    return (
        f"{_trim(item.get('subagent_kind'), 64) or '-'} | "
        f"profile={_trim(((item.get('input_scope') or {}) if isinstance(item.get('input_scope'), dict) else {}).get('context_pack_profile'), 64) or '-'} | "
        f"backend={_trim(((item.get('backend') or {}) if isinstance(item.get('backend'), dict) else {}).get('backend_kind'), 64) or '-'} | "
        f"artifact={_trim(artifact.get('path'), 160) or '-'}"
    )


def normalize_subagent_result_artifact(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    confidence = _trim(raw.get("confidence"), 32).lower()
    if confidence not in SUBAGENT_CONFIDENCE:
        confidence = "medium"
    artifact = {
        "version": _trim(raw.get("version"), 64) or SUBAGENT_RESULT_VERSION,
        "subagent_kind": _trim(raw.get("subagent_kind"), 64).lower() or "general_research",
        "summary": _trim(raw.get("summary"), 320) or "-",
        "confidence": confidence,
        "sources": _normalize_rows(raw.get("sources"), limit=8, item_limit=200),
        "key_findings": _normalize_rows(raw.get("key_findings"), limit=8, item_limit=200),
        "blocking_issues": _normalize_rows(raw.get("blocking_issues"), limit=6, item_limit=200),
        "recommended_next_step": _trim(raw.get("recommended_next_step"), 240) or "-",
        "artifact_refs": _normalize_rows(raw.get("artifact_refs"), limit=8, item_limit=200),
    }
    return artifact
