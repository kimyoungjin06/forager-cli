#!/usr/bin/env python3
"""Task-scoped worker contract compiler for bounded background model invokes."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from aoe_tg_context_pack import load_context_pack


WORKER_TASK_CONTRACT_VERSION = "2026-04-10.v1"
WORKER_TASK_RESULT_VERSION = "2026-04-11.v1"
WORKER_TASK_MODULE_GATE_VERSION = "2026-04-13.v1"
WORKER_TASK_MODULE_PROFILE_VERSION = "2026-04-13.v1"
WORKER_TASK_MODULE_CHECKLIST_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_ITEMS_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_ITEM_CLASSES_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_RECORDS_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_RECORD_ROWS_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_PREFLIGHT_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_PREFLIGHT_ROWS_VERSION = "2026-04-14.v1"
WORKER_TASK_MODULE_ACTION_BLOCKER_VERSION = "2026-04-15.v1"
WORKER_TASK_UPDATE_STUB_VERSION = "2026-04-11.v1"
WORKER_TASK_PROPOSAL_STUB_VERSION = "2026-04-11.v1"
WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION = "2026-04-11.v1"
WORKER_MODULE_KINDS = ("analysis", "writing", "package", "general")
WORKER_MODULE_POLICY_DEFAULTS = {
    "analysis": {
        "policy": "findings_evidence_gate",
        "result_focus": "findings+evidence",
        "apply_gate": "advisory_review",
        "loop_mode": "evidence_review",
        "proposal_kind": "handoff",
        "apply_kind": "handoff",
        "proposal_priority": "P2",
        "apply_priority": "P2",
        "repeat_when": "evidence_missing",
        "stop_when": "findings_stable",
    },
    "writing": {
        "policy": "doc_quality_gate",
        "result_focus": "draft+handoff",
        "apply_gate": "review_before_syncback",
        "loop_mode": "draft_review",
        "proposal_kind": "followup",
        "apply_kind": "handoff",
        "proposal_priority": "P2",
        "apply_priority": "P2",
        "repeat_when": "quality_gate_open",
        "stop_when": "handoff_ready",
    },
    "package": {
        "policy": "artifact_integrity_gate",
        "result_focus": "artifact+verification",
        "apply_gate": "strict_syncback",
        "loop_mode": "build_verify",
        "proposal_kind": "handoff",
        "apply_kind": "handoff",
        "proposal_priority": "P1",
        "apply_priority": "P1",
        "repeat_when": "artifact_check_open",
        "stop_when": "syncback_clean",
    },
    "general": {
        "policy": "general_gate",
        "result_focus": "summary+actions",
        "apply_gate": "standard_review",
        "loop_mode": "single_pass",
        "proposal_kind": "followup",
        "apply_kind": "followup",
        "proposal_priority": "P2",
        "apply_priority": "P2",
        "repeat_when": "operator_requests_retry",
        "stop_when": "summary_ready",
    },
}
WORKER_TASK_SYSTEM = (
    "You are the bounded background worker. Return strict JSON with keys: "
    "status, summary, actions, cautions, evidence_refs. Keep every field concise."
)


def _trim(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    return str(value).strip()[: max(0, int(limit or 0))]


def _uniq(values: Any, *, limit: int = 6, text_limit: int = 240) -> List[str]:
    rows = values if isinstance(values, list) else []
    out: List[str] = []
    for row in rows:
        token = _trim(row, text_limit)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit or 1))]


def _task_label(task: Dict[str, Any]) -> str:
    return (
        _trim(task.get("short_id"), 48).upper()
        or _trim(task.get("alias"), 96)
        or _trim(task.get("request_id"), 96)
        or "task"
    )


def _summary(contract: Dict[str, Any]) -> str:
    parts = [
        f"module={_trim(contract.get('module_kind'), 48) or '-'}",
        f"task={_trim(contract.get('task_label'), 64) or '-'}",
        f"pack={_trim(contract.get('pack_profile'), 64) or '-'}",
        f"brief={_trim(contract.get('execution_brief_status'), 48) or '-'}",
        f"docs={len(contract.get('doc_paths') or [])}",
    ]
    return " | ".join(parts)[:320]


def resolve_worker_module_policy(raw: Any) -> Dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    module_kind = _trim(source.get("module_kind"), 48).lower()
    if module_kind not in WORKER_MODULE_KINDS:
        module_kind = "general"
    defaults = dict(WORKER_MODULE_POLICY_DEFAULTS.get(module_kind) or WORKER_MODULE_POLICY_DEFAULTS["general"])
    policy = _trim(source.get("module_policy"), 64) or str(defaults.get("policy", "")).strip() or "general_gate"
    result_focus = _trim(source.get("module_result_focus"), 96) or str(defaults.get("result_focus", "")).strip() or "-"
    apply_gate = _trim(source.get("module_apply_gate"), 96) or str(defaults.get("apply_gate", "")).strip() or "-"
    loop_mode = _trim(source.get("module_loop_mode"), 64) or str(defaults.get("loop_mode", "")).strip() or "-"
    proposal_kind = _trim(source.get("module_proposal_kind"), 32).lower() or str(defaults.get("proposal_kind", "")).strip() or "followup"
    if proposal_kind not in {"followup", "handoff", "risk", "debt"}:
        proposal_kind = "followup"
    apply_kind = _trim(source.get("module_apply_kind"), 32).lower() or str(defaults.get("apply_kind", "")).strip() or "followup"
    if apply_kind not in {"followup", "handoff", "risk", "debt"}:
        apply_kind = "followup"
    proposal_priority = _trim(source.get("module_proposal_priority"), 8).upper() or str(defaults.get("proposal_priority", "")).strip() or "P2"
    apply_priority = _trim(source.get("module_apply_priority"), 8).upper() or str(defaults.get("apply_priority", "")).strip() or "P2"
    if proposal_priority not in {"P1", "P2", "P3"}:
        proposal_priority = "P2"
    if apply_priority not in {"P1", "P2", "P3"}:
        apply_priority = "P2"
    repeat_when = _trim(source.get("module_repeat_when"), 96) or str(defaults.get("repeat_when", "")).strip() or "-"
    stop_when = _trim(source.get("module_stop_when"), 96) or str(defaults.get("stop_when", "")).strip() or "-"
    summary = _trim(source.get("module_policy_summary"), 240)
    if not summary:
        summary = (
            f"{module_kind} | policy={policy} | result={result_focus} | "
            f"apply={apply_gate} | loop={loop_mode}"
        )[:240]
    return {
        "module_kind": module_kind,
        "policy": policy,
        "result_focus": result_focus,
        "apply_gate": apply_gate,
        "loop_mode": loop_mode,
        "proposal_kind": proposal_kind,
        "apply_kind": apply_kind,
        "proposal_priority": proposal_priority,
        "apply_priority": apply_priority,
        "repeat_when": repeat_when,
        "stop_when": stop_when,
        "summary": summary,
    }


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    low = str(text or "").strip().lower()
    return any(token in low for token in tokens)


def _module_from_source(source: Dict[str, Any]) -> Dict[str, str]:
    explicit = _trim(source.get("module_kind"), 48).lower()
    if explicit in WORKER_MODULE_KINDS:
        reason = _trim(source.get("module_reason"), 240) or "explicit"
        return {
            "kind": explicit,
            "reason": reason,
            "summary": f"{explicit} | {reason}"[:240],
        }
    preset = _trim(
        source.get("contract_preset")
        or source.get("request_contract_preset")
        or source.get("phase2_team_preset")
        or source.get("phase1_role_preset"),
        48,
    ).lower()
    objective = _trim(source.get("objective"), 320)
    outputs = " ".join(_uniq(source.get("required_outputs"), limit=12, text_limit=120))
    targets = " ".join(_uniq(source.get("artifact_targets"), limit=12, text_limit=160))
    docs = " ".join(_uniq(source.get("doc_paths"), limit=12, text_limit=160))
    constraints = " ".join(_uniq(source.get("constraints"), limit=12, text_limit=120))
    haystack = " ".join(part for part in (objective, outputs, targets, docs, constraints, preset) if part).lower()
    package_tokens = (
        "package",
        "packaging",
        "bundle",
        "archive",
        "release",
        "artifact apply",
        "artifact update",
        "dist/",
        "wheel",
        "sdist",
        ".tar",
        ".zip",
        "installer",
    )
    writing_tokens = (
        "write",
        "writer",
        "writing",
        "document",
        "docs/",
        "report",
        "summary",
        "runbook",
        "handoff",
        "readme",
        "spec",
        "draft",
        "reviewer_note",
    )
    analysis_tokens = (
        "analy",
        "research",
        "investig",
        "compare",
        "benchmark",
        "scope",
        "audit",
        "finding",
        "inventory",
        "diagnostic",
        "evidence",
    )
    if _contains_any(haystack, package_tokens):
        return {
            "kind": "package",
            "reason": "artifact/package signals",
            "summary": "package | artifact/package signals",
        }
    if preset in {"analysis", "review", "data"} or _contains_any(haystack, analysis_tokens):
        return {
            "kind": "analysis",
            "reason": "analysis/review signals",
            "summary": "analysis | analysis/review signals",
        }
    if preset == "writer" or _contains_any(haystack, writing_tokens):
        return {
            "kind": "writing",
            "reason": "writer/doc signals",
            "summary": "writing | writer/doc signals",
        }
    return {
        "kind": "general",
        "reason": "general task",
        "summary": "general | fallback",
    }


def sanitize_worker_task_contract(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    module = _module_from_source(source)
    module_policy = resolve_worker_module_policy(
        {
            **source,
            "module_kind": module.get("kind"),
        }
    )
    contract = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_CONTRACT_VERSION,
        "request_id": _trim(source.get("request_id"), 96),
        "task_id": _trim(source.get("task_id"), 48),
        "task_label": _trim(source.get("task_label"), 96) or "task",
        "project_alias": _trim(source.get("project_alias"), 32),
        "project_label": _trim(source.get("project_label"), 96),
        "status": _trim(source.get("status"), 48) or "-",
        "tf_phase": _trim(source.get("tf_phase"), 48) or "-",
        "contract_preset": _trim(source.get("contract_preset"), 48) or "-",
        "module_kind": str(module.get("kind", "")).strip() or "general",
        "module_reason": str(module.get("reason", "")).strip() or "-",
        "module_summary": str(module.get("summary", "")).strip() or "-",
        "module_policy": str(module_policy.get("policy", "")).strip() or "general_gate",
        "module_result_focus": str(module_policy.get("result_focus", "")).strip() or "-",
        "module_apply_gate": str(module_policy.get("apply_gate", "")).strip() or "-",
        "module_loop_mode": str(module_policy.get("loop_mode", "")).strip() or "-",
        "module_proposal_kind": str(module_policy.get("proposal_kind", "")).strip() or "followup",
        "module_apply_kind": str(module_policy.get("apply_kind", "")).strip() or "followup",
        "module_proposal_priority": str(module_policy.get("proposal_priority", "")).strip() or "P2",
        "module_apply_priority": str(module_policy.get("apply_priority", "")).strip() or "P2",
        "module_repeat_when": str(module_policy.get("repeat_when", "")).strip() or "-",
        "module_stop_when": str(module_policy.get("stop_when", "")).strip() or "-",
        "module_policy_summary": str(module_policy.get("summary", "")).strip() or "-",
        "pack_profile": _trim(source.get("pack_profile"), 64) or "offdesk_execute",
        "objective": _trim(source.get("objective"), 320) or "-",
        "execution_brief_status": _trim(source.get("execution_brief_status"), 48) or "-",
        "execution_brief_summary": _trim(source.get("execution_brief_summary"), 320) or "-",
        "execution_brief_operator_decision": _trim(source.get("execution_brief_operator_decision"), 320) or "-",
        "followup_brief_status": _trim(source.get("followup_brief_status"), 48) or "-",
        "followup_brief_summary": _trim(source.get("followup_brief_summary"), 320) or "-",
        "reentry_rails_summary": _trim(source.get("reentry_rails_summary"), 320) or "-",
        "constraints": _uniq(source.get("constraints"), limit=6, text_limit=200),
        "doc_paths": _uniq(source.get("doc_paths"), limit=6, text_limit=200),
        "required_outputs": _uniq(source.get("required_outputs"), limit=8, text_limit=160),
        "artifact_targets": _uniq(source.get("artifact_targets"), limit=8, text_limit=160),
        "known_failures": _uniq(source.get("known_failures"), limit=4, text_limit=200),
        "unresolved_questions": _uniq(source.get("unresolved_questions"), limit=4, text_limit=200),
    }
    contract["summary"] = _trim(source.get("summary"), 320) or _summary(contract)
    return contract


def load_worker_task_contract(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return sanitize_worker_task_contract(raw)
    text = _trim(raw, 16000)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return sanitize_worker_task_contract(parsed)


def _result_summary(result: Dict[str, Any]) -> str:
    status = _trim(result.get("status"), 48) or "-"
    summary = _trim(result.get("summary"), 160) or "-"
    parts = [f"status={status}", summary]
    action_count = len(result.get("actions") or [])
    caution_count = len(result.get("cautions") or [])
    evidence_count = len(result.get("evidence_refs") or [])
    if action_count:
        parts.append(f"actions={action_count}")
    if caution_count:
        parts.append(f"cautions={caution_count}")
    if evidence_count:
        parts.append(f"refs={evidence_count}")
    return " | ".join(parts)[:320]


def _join_text(values: Any) -> str:
    rows = values if isinstance(values, list) else []
    return " ".join(str(item).strip().lower() for item in rows if str(item).strip())


def _readyish_status(status: str) -> bool:
    token = _trim(status, 48).lower()
    return token in {"ready", "done", "complete", "completed", "ok", "success", "stable"}


def sanitize_worker_task_result(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    result = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_RESULT_VERSION,
        "status": _trim(source.get("status"), 48) or "-",
        "summary": _trim(source.get("summary"), 240) or "-",
        "actions": _uniq(source.get("actions"), limit=4, text_limit=160),
        "cautions": _uniq(source.get("cautions"), limit=4, text_limit=160),
        "evidence_refs": _uniq(source.get("evidence_refs"), limit=8, text_limit=160),
    }
    result["summary_line"] = _trim(source.get("summary_line"), 320) or _result_summary(result)
    return result


def load_worker_task_result(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return sanitize_worker_task_result(raw)
    text = _trim(raw, 16000)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return sanitize_worker_task_result(parsed)


def sanitize_worker_task_module_gate(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_GATE_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "gate": _trim(source.get("gate"), 96) or "-",
        "state": _trim(source.get("state"), 64) or "-",
        "focus_summary": _trim(source.get("focus_summary"), 240) or "-",
        "repeat_hint": _trim(source.get("repeat_hint"), 96) or "-",
        "stop_hint": _trim(source.get("stop_hint"), 96) or "-",
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = [f"state={row['state']}"]
        if row["focus_summary"] not in {"", "-"}:
            parts.append(row["focus_summary"])
        if row["repeat_hint"] not in {"", "-"}:
            parts.append(f"repeat={row['repeat_hint']}")
        elif row["stop_hint"] not in {"", "-"}:
            parts.append(f"stop={row['stop_hint']}")
        row["summary_line"] = " | ".join(parts)[:320]
    return row


def derive_worker_task_module_gate(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    stub = (
        sanitize_worker_task_update_stub(update_stub)
        if isinstance(update_stub, dict)
        else derive_worker_task_update_stub(contract_row, result_row)
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    policy = _trim(contract_row.get("module_policy"), 96) or resolve_worker_module_policy(contract_row).get("policy", "-")
    actions = list(result_row.get("actions") or [])
    cautions = list(result_row.get("cautions") or [])
    refs = list(result_row.get("evidence_refs") or [])
    targets = list((stub or {}).get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    caution_text = _join_text(cautions)
    readyish = _readyish_status(str(result_row.get("status", "")))

    if module_kind == "analysis":
        findings_count = max(len(actions), 1 if _trim(result_row.get("summary"), 240) not in {"", "-"} else 0)
        refs_count = len(refs)
        evidence_open = refs_count == 0 or any(
            token in caution_text for token in ("evidence", "missing", "gap", "unclear", "unsourced")
        )
        state = "evidence_open" if evidence_open else ("findings_stable" if readyish else "review_needed")
        repeat_hint = "evidence_missing" if state == "evidence_open" else "-"
        stop_hint = "findings_stable" if state == "findings_stable" else "-"
        focus_summary = f"findings={findings_count} | refs={refs_count}"
    elif module_kind == "writing":
        doc_count = max(
            len([token for token in targets if _trim(token, 160)]),
            1 if actions or _trim(result_row.get("summary"), 240) not in {"", "-"} else 0,
        )
        refs_count = len(refs)
        quality_open = any(
            token in caution_text
            for token in ("quality", "tone", "style", "placeholder", "review", "copyedit", "polish")
        )
        if quality_open:
            state = "quality_open"
        elif readyish and doc_count > 0 and refs_count > 0:
            state = "handoff_ready"
        elif doc_count > 0:
            state = "draft_ready"
        else:
            state = "writing_review"
        repeat_hint = "quality_gate_open" if state == "quality_open" else "-"
        stop_hint = "handoff_ready" if state == "handoff_ready" else "-"
        focus_summary = f"docs={doc_count} | refs={refs_count}"
    elif module_kind == "package":
        artifact_count = max(
            len([token for token in targets if _trim(token, 160)]),
            1 if actions or _trim(result_row.get("summary"), 240) not in {"", "-"} else 0,
        )
        refs_count = len(refs)
        integrity_open = refs_count == 0 or any(
            token in caution_text
            for token in ("verify", "verification", "integrity", "mismatch", "missing", "fail", "checksum")
        )
        if integrity_open:
            state = "artifact_check_open"
        elif readyish and artifact_count > 0 and refs_count > 0:
            state = "integrity_ready"
        elif artifact_count > 0:
            state = "package_ready"
        else:
            state = "package_review"
        repeat_hint = "artifact_check_open" if state == "artifact_check_open" else "-"
        stop_hint = "syncback_clean" if state == "integrity_ready" else "-"
        focus_summary = f"artifacts={artifact_count} | refs={refs_count}"
    else:
        action_count = len(actions)
        refs_count = len(refs)
        caution_count = len(cautions)
        state = "summary_ready" if readyish and caution_count == 0 else "review_open"
        repeat_hint = "operator_requests_retry" if state == "review_open" else "-"
        stop_hint = "summary_ready" if state == "summary_ready" else "-"
        focus_summary = f"actions={action_count} | refs={refs_count}"

    return sanitize_worker_task_module_gate(
        {
            "module_kind": module_kind,
            "gate": policy,
            "state": state,
            "focus_summary": focus_summary,
            "repeat_hint": repeat_hint,
            "stop_hint": stop_hint,
        }
    )


def summarize_worker_task_module_gate(raw: Any) -> str:
    row = sanitize_worker_task_module_gate(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_profile(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_PROFILE_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "profile_kind": _trim(source.get("profile_kind"), 96) or "-",
        "state": _trim(source.get("state"), 64) or "-",
        "focus_summary": _trim(source.get("focus_summary"), 240) or "-",
        "counts_summary": _trim(source.get("counts_summary"), 240) or "-",
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["profile_kind"] not in {"", "-"}:
            parts.append(row["profile_kind"])
        parts.append(f"state={row['state']}")
        if row["focus_summary"] not in {"", "-"}:
            parts.append(row["focus_summary"])
        if row["counts_summary"] not in {"", "-"}:
            parts.append(row["counts_summary"])
        row["summary_line"] = " | ".join(parts)[:320]
    return row


def derive_worker_task_module_profile(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    stub = (
        sanitize_worker_task_update_stub(update_stub)
        if isinstance(update_stub, dict)
        else derive_worker_task_update_stub(contract_row, result_row)
    )
    gate_row = (
        sanitize_worker_task_module_gate(gate)
        if isinstance(gate, dict)
        else derive_worker_task_module_gate(contract_row, result_row, update_stub=stub)
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    actions = list(result_row.get("actions") or [])
    cautions = list(result_row.get("cautions") or [])
    refs = list(result_row.get("evidence_refs") or [])
    targets = list((stub or {}).get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    state = _trim(gate_row.get("state"), 64) or "-"

    if module_kind == "analysis":
        findings = max(len(actions), 1 if _trim(result_row.get("summary"), 240) not in {"", "-"} else 0)
        evidence = len(refs)
        gaps = max(0, findings - evidence)
        return sanitize_worker_task_module_profile(
            {
                "module_kind": module_kind,
                "profile_kind": "analysis_findings_profile",
                "state": state,
                "focus_summary": f"findings={findings} | evidence={evidence} | gaps={gaps}",
                "counts_summary": f"targets={len(targets)} | cautions={len(cautions)}",
            }
        )

    if module_kind == "writing":
        docs = max(len([token for token in targets if _trim(token, 160)]), 1 if actions else 0)
        quality = "open" if state == "quality_open" else "ready"
        if state == "handoff_ready":
            handoff = "ready"
        elif state == "draft_ready":
            handoff = "draft"
        else:
            handoff = "review"
        return sanitize_worker_task_module_profile(
            {
                "module_kind": module_kind,
                "profile_kind": "writing_handoff_profile",
                "state": state,
                "focus_summary": f"docs={docs} | handoff={handoff} | quality={quality}",
                "counts_summary": f"refs={len(refs)} | cautions={len(cautions)}",
            }
        )

    if module_kind == "package":
        artifacts = max(len([token for token in targets if _trim(token, 160)]), 1 if actions else 0)
        verification = len(refs)
        integrity = "ready" if state == "integrity_ready" else "open"
        return sanitize_worker_task_module_profile(
            {
                "module_kind": module_kind,
                "profile_kind": "package_verification_profile",
                "state": state,
                "focus_summary": f"artifacts={artifacts} | verification={verification} | integrity={integrity}",
                "counts_summary": f"targets={len(targets)} | cautions={len(cautions)}",
            }
        )

    return sanitize_worker_task_module_profile(
        {
            "module_kind": module_kind,
            "profile_kind": "general_result_profile",
            "state": state,
            "focus_summary": f"actions={len(actions)} | refs={len(refs)} | cautions={len(cautions)}",
            "counts_summary": f"targets={len(targets)}",
        }
    )


def summarize_worker_task_module_profile(raw: Any) -> str:
    row = sanitize_worker_task_module_profile(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_checklist(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_CHECKLIST_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "checklist_kind": _trim(source.get("checklist_kind"), 96) or "-",
        "state": _trim(source.get("state"), 64) or "-",
        "next_hint": _trim(source.get("next_hint"), 160) or "-",
        "checkpoints": _uniq(source.get("checkpoints"), limit=6, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["checklist_kind"] not in {"", "-"}:
            parts.append(row["checklist_kind"])
        parts.append(f"state={row['state']}")
        if row["checkpoints"]:
            parts.append(",".join(list(row["checkpoints"])[:3]))
        if row["next_hint"] not in {"", "-"}:
            parts.append(f"next={row['next_hint']}")
        row["summary_line"] = " | ".join(parts)[:320]
    return row


def derive_worker_task_module_checklist(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    stub = (
        sanitize_worker_task_update_stub(update_stub)
        if isinstance(update_stub, dict)
        else derive_worker_task_update_stub(contract_row, result_row)
    )
    gate_row = (
        sanitize_worker_task_module_gate(gate)
        if isinstance(gate, dict)
        else derive_worker_task_module_gate(contract_row, result_row, update_stub=stub)
    )
    profile_row = (
        sanitize_worker_task_module_profile(profile)
        if isinstance(profile, dict)
        else derive_worker_task_module_profile(contract_row, result_row, update_stub=stub, gate=gate_row)
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    state = _trim(gate_row.get("state"), 64) or "-"
    actions = list(result_row.get("actions") or [])
    refs = list(result_row.get("evidence_refs") or [])
    cautions = list(result_row.get("cautions") or [])
    targets = list((stub or {}).get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])

    if module_kind == "analysis":
        findings = max(len(actions), 1 if _trim(result_row.get("summary"), 240) not in {"", "-"} else 0)
        evidence = len(refs)
        gaps = max(0, findings - evidence)
        checkpoints = [
            f"findings={findings}",
            f"evidence={evidence}",
            f"gaps={gaps}",
        ]
        next_hint = "fill_evidence_gaps" if gaps > 0 else "validate_caveats"
        return sanitize_worker_task_module_checklist(
            {
                "module_kind": module_kind,
                "checklist_kind": "analysis_checklist",
                "state": state,
                "checkpoints": checkpoints,
                "next_hint": next_hint,
            }
        )

    if module_kind == "writing":
        docs = max(len([token for token in targets if _trim(token, 160)]), 1 if actions else 0)
        handoff = "ready" if "handoff=ready" in str(profile_row.get("summary_line", "")) else (
            "draft" if "handoff=draft" in str(profile_row.get("summary_line", "")) else "review"
        )
        quality = "open" if state == "quality_open" else "ready"
        checkpoints = [
            f"docs={docs}",
            f"handoff={handoff}",
            f"quality={quality}",
        ]
        next_hint = "close_quality_gate" if quality == "open" else "handoff_ready"
        return sanitize_worker_task_module_checklist(
            {
                "module_kind": module_kind,
                "checklist_kind": "writing_checklist",
                "state": state,
                "checkpoints": checkpoints,
                "next_hint": next_hint,
            }
        )

    if module_kind == "package":
        artifacts = max(len([token for token in targets if _trim(token, 160)]), 1 if actions else 0)
        verification = len(refs)
        integrity = "ready" if state == "integrity_ready" else "open"
        checkpoints = [
            f"artifacts={artifacts}",
            f"verification={verification}",
            f"integrity={integrity}",
        ]
        next_hint = "verify_artifacts" if integrity == "open" else "syncback_clean"
        return sanitize_worker_task_module_checklist(
            {
                "module_kind": module_kind,
                "checklist_kind": "package_checklist",
                "state": state,
                "checkpoints": checkpoints,
                "next_hint": next_hint,
            }
        )

    checkpoints = [
        f"actions={len(actions)}",
        f"refs={len(refs)}",
        f"cautions={len(cautions)}",
    ]
    next_hint = "operator_review"
    return sanitize_worker_task_module_checklist(
        {
            "module_kind": module_kind,
            "checklist_kind": "general_checklist",
            "state": state,
            "checkpoints": checkpoints,
            "next_hint": next_hint,
        }
    )


def summarize_worker_task_module_checklist(raw: Any) -> str:
    row = sanitize_worker_task_module_checklist(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_items(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_ITEMS_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "items_kind": _trim(source.get("items_kind"), 96) or "-",
        "items": _uniq(source.get("items"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["items_kind"] not in {"", "-"}:
            parts.append(row["items_kind"])
        if row["items"]:
            parts.append(",".join(list(row["items"])[:4]))
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_items(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    stub = (
        sanitize_worker_task_update_stub(update_stub)
        if isinstance(update_stub, dict)
        else derive_worker_task_update_stub(contract_row, result_row)
    )
    gate_row = (
        sanitize_worker_task_module_gate(gate)
        if isinstance(gate, dict)
        else derive_worker_task_module_gate(contract_row, result_row, update_stub=stub)
    )
    profile_row = (
        sanitize_worker_task_module_profile(profile)
        if isinstance(profile, dict)
        else derive_worker_task_module_profile(contract_row, result_row, update_stub=stub, gate=gate_row)
    )
    checklist_row = (
        sanitize_worker_task_module_checklist(checklist)
        if isinstance(checklist, dict)
        else derive_worker_task_module_checklist(
            contract_row, result_row, update_stub=stub, gate=gate_row, profile=profile_row
        )
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    actions = [item for item in list(result_row.get("actions") or []) if _trim(item, 160)]
    refs = [item for item in list(result_row.get("evidence_refs") or []) if _trim(item, 160)]
    cautions = [item for item in list(result_row.get("cautions") or []) if _trim(item, 160)]
    targets = [item for item in list((stub or {}).get("target_artifacts") or []) if _trim(item, 160)]

    if module_kind == "analysis":
        items: List[str] = []
        if actions:
            items.append(f"finding:{_trim(actions[0], 96)}")
        if refs:
            items.append(f"evidence:{_trim(refs[0], 96)}")
        if "gaps=0" not in str(checklist_row.get("summary_line", "")):
            items.append("gap:evidence_missing")
        elif cautions:
            items.append(f"caveat:{_trim(cautions[0], 96)}")
        return sanitize_worker_task_module_items(
            {"module_kind": module_kind, "items_kind": "analysis_items", "items": items}
        )

    if module_kind == "writing":
        items = []
        if targets:
            items.append(f"doc:{_trim(targets[0], 96)}")
        if "handoff=ready" in str(profile_row.get("summary_line", "")):
            items.append("handoff:ready")
        elif "handoff=draft" in str(profile_row.get("summary_line", "")):
            items.append("handoff:draft")
        else:
            items.append("handoff:review")
        items.append("quality:open" if "quality=open" in str(profile_row.get("summary_line", "")) else "quality:ready")
        return sanitize_worker_task_module_items(
            {"module_kind": module_kind, "items_kind": "writing_items", "items": items}
        )

    if module_kind == "package":
        items = []
        if targets:
            items.append(f"artifact:{_trim(targets[0], 96)}")
        items.append(f"verification:{len(refs)}")
        items.append(
            "integrity:ready"
            if "integrity=ready" in str(profile_row.get("summary_line", ""))
            else "integrity:open"
        )
        return sanitize_worker_task_module_items(
            {"module_kind": module_kind, "items_kind": "package_items", "items": items}
        )

    items = []
    if actions:
        items.append(f"action:{_trim(actions[0], 96)}")
    if refs:
        items.append(f"ref:{_trim(refs[0], 96)}")
    return sanitize_worker_task_module_items(
        {"module_kind": module_kind, "items_kind": "general_items", "items": items}
    )


def summarize_worker_task_module_items(raw: Any) -> str:
    row = sanitize_worker_task_module_items(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_item_classes(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_ITEM_CLASSES_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "classes_kind": _trim(source.get("classes_kind"), 96) or "-",
        "classes": _uniq(source.get("classes"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["classes_kind"] not in {"", "-"}:
            parts.append(row["classes_kind"])
        parts.extend(list(row["classes"])[:4])
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_item_classes(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
    items: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    items_row = (
        sanitize_worker_task_module_items(items)
        if isinstance(items, dict)
        else derive_worker_task_module_items(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
        )
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    item_tokens = [str(item).strip() for item in list(items_row.get("items") or []) if str(item).strip()]

    def _count(prefix: str) -> int:
        return len([token for token in item_tokens if token.startswith(prefix)])

    def _suffix(prefix: str, fallback: str = "-") -> str:
        for token in item_tokens:
            if token.startswith(prefix):
                return _trim(token.split(":", 1)[-1], 96) or fallback
        return fallback

    if module_kind == "analysis":
        return sanitize_worker_task_module_item_classes(
            {
                "module_kind": module_kind,
                "classes_kind": "analysis_item_classes",
                "classes": [
                    f"finding={_count('finding:')}",
                    f"evidence={_count('evidence:')}",
                    f"gap={_count('gap:')}",
                    f"caveat={_count('caveat:')}",
                ],
            }
        )

    if module_kind == "writing":
        return sanitize_worker_task_module_item_classes(
            {
                "module_kind": module_kind,
                "classes_kind": "writing_item_classes",
                "classes": [
                    f"doc={_count('doc:')}",
                    f"handoff={_suffix('handoff:')}",
                    f"quality={_suffix('quality:')}",
                ],
            }
        )

    if module_kind == "package":
        return sanitize_worker_task_module_item_classes(
            {
                "module_kind": module_kind,
                "classes_kind": "package_item_classes",
                "classes": [
                    f"artifact={_count('artifact:')}",
                    f"verification={_suffix('verification:')}",
                    f"integrity={_suffix('integrity:')}",
                ],
            }
        )

    return sanitize_worker_task_module_item_classes(
        {
            "module_kind": module_kind,
            "classes_kind": "general_item_classes",
            "classes": [
                f"action={_count('action:')}",
                f"ref={_count('ref:')}",
            ],
        }
    )


def summarize_worker_task_module_item_classes(raw: Any) -> str:
    row = sanitize_worker_task_module_item_classes(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_records(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_RECORDS_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "records_kind": _trim(source.get("records_kind"), 96) or "-",
        "records": _uniq(source.get("records"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["records_kind"] not in {"", "-"}:
            parts.append(row["records_kind"])
        parts.extend(list(row["records"])[:4])
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_records(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
    items: Any = None,
    item_classes: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    stub = (
        sanitize_worker_task_update_stub(update_stub)
        if isinstance(update_stub, dict)
        else derive_worker_task_update_stub(contract_row, result_row)
    )
    items_row = (
        sanitize_worker_task_module_items(items)
        if isinstance(items, dict)
        else derive_worker_task_module_items(
            contract_row,
            result_row,
            update_stub=stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
        )
    )
    item_classes_row = (
        sanitize_worker_task_module_item_classes(item_classes)
        if isinstance(item_classes, dict)
        else derive_worker_task_module_item_classes(
            contract_row,
            result_row,
            update_stub=stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
            items=items_row,
        )
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    item_tokens = [str(item).strip() for item in list(items_row.get("items") or []) if str(item).strip()]
    class_tokens = [str(item).strip() for item in list(item_classes_row.get("classes") or []) if str(item).strip()]
    target_tokens = [str(item).strip() for item in list((stub or {}).get("target_artifacts") or []) if str(item).strip()]

    def _first(prefix: str, fallback: str = "-") -> str:
        for token in item_tokens:
            if token.startswith(prefix):
                return _trim(token.split(":", 1)[-1], 96) or fallback
        return fallback

    def _class_value(prefix: str, fallback: str = "-") -> str:
        for token in class_tokens:
            if token.startswith(prefix):
                return _trim(token.split("=", 1)[-1], 96) or fallback
        return fallback

    if module_kind == "analysis":
        records = [
            f"finding_record={_first('finding:')}",
            f"evidence_record={_first('evidence:')}",
        ]
        gap_value = _class_value("gap=", "0")
        if gap_value not in {"0", "-", ""}:
            records.append("gap_record=evidence_missing")
        else:
            records.append(f"caveat_record={_first('caveat:')}")
        return sanitize_worker_task_module_records(
            {
                "module_kind": module_kind,
                "records_kind": "analysis_records",
                "records": records,
            }
        )

    if module_kind == "writing":
        records = [
            f"doc_record={_first('doc:')}",
            f"handoff_record={_class_value('handoff=')}",
            f"quality_record={_class_value('quality=')}",
        ]
        return sanitize_worker_task_module_records(
            {
                "module_kind": module_kind,
                "records_kind": "writing_records",
                "records": records,
            }
        )

    if module_kind == "package":
        integrity = _class_value("integrity=", "open")
        verification = _class_value("verification=", "0")
        apply_state = "ready" if target_tokens else "pending"
        syncback_state = "ready" if integrity == "ready" and target_tokens else "pending"
        records = [
            f"artifact_record={_first('artifact:')}",
            f"verification_record={verification}",
            f"apply_record={apply_state}",
            f"syncback_record={syncback_state}",
        ]
        return sanitize_worker_task_module_records(
            {
                "module_kind": module_kind,
                "records_kind": "package_records",
                "records": records,
            }
        )

    records = [
        f"action_record={_first('action:')}",
        f"ref_record={_first('ref:')}",
    ]
    return sanitize_worker_task_module_records(
        {
            "module_kind": module_kind,
            "records_kind": "general_records",
            "records": records,
        }
    )


def summarize_worker_task_module_records(raw: Any) -> str:
    row = sanitize_worker_task_module_records(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def worker_task_module_record_map(raw: Any) -> Dict[str, str]:
    row = sanitize_worker_task_module_records(raw)
    mapping: Dict[str, str] = {}
    for token in list(row.get("records") or []):
        safe = str(token).strip()
        if not safe or "=" not in safe:
            continue
        key, value = safe.split("=", 1)
        safe_key = _trim(key, 64)
        if not safe_key:
            continue
        mapping[safe_key] = _trim(value, 160)
    return mapping


def worker_task_module_syncback_ready(raw: Any) -> bool:
    row = sanitize_worker_task_module_records(raw)
    if row.get("module_kind") != "package" and row.get("records_kind") != "package_records":
        return True
    return worker_task_module_record_map(row).get("syncback_record") == "ready"


def sanitize_worker_task_module_record_rows(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_RECORD_ROWS_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "rows_kind": _trim(source.get("rows_kind"), 96) or "-",
        "rows": _uniq(source.get("rows"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["rows_kind"] not in {"", "-"}:
            parts.append(row["rows_kind"])
        parts.extend(list(row["rows"])[:4])
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def worker_task_module_record_row_map(raw: Any) -> Dict[str, Dict[str, str]]:
    row = sanitize_worker_task_module_record_rows(raw)
    mapping: Dict[str, Dict[str, str]] = {}
    for token in list(row.get("rows") or []):
        safe = str(token).strip()
        if not safe:
            continue
        parts = [part.strip() for part in safe.split("|") if str(part).strip()]
        if not parts or "=" not in parts[0]:
            continue
        key, value = parts[0].split("=", 1)
        safe_key = _trim(key, 64)
        if not safe_key:
            continue
        entry: Dict[str, str] = {"value": _trim(value, 160)}
        for extra in parts[1:]:
            if "=" not in extra:
                continue
            extra_key, extra_value = extra.split("=", 1)
            safe_extra_key = _trim(extra_key, 64)
            if not safe_extra_key:
                continue
            entry[safe_extra_key] = _trim(extra_value, 160)
        mapping[safe_key] = entry
    return mapping


def worker_task_module_apply_ready(raw: Any) -> bool:
    row = sanitize_worker_task_module_record_rows(raw)
    module_kind = str(row.get("module_kind", "")).strip().lower()
    rows_kind = str(row.get("rows_kind", "")).strip()
    row_map = worker_task_module_record_row_map(row)
    if module_kind == "analysis" or rows_kind == "analysis_record_rows":
        finding_state = row_map.get("finding_row", {}).get("state", "")
        evidence_state = row_map.get("evidence_row", {}).get("state", "")
        gap_state = row_map.get("gap_row", {}).get("state", "")
        return finding_state == "stable" and evidence_state == "attached" and gap_state != "open"
    if module_kind == "writing" or rows_kind == "writing_record_rows":
        doc_state = row_map.get("doc_row", {}).get("state", "")
        handoff_state = row_map.get("handoff_row", {}).get("state", "")
        quality_state = row_map.get("quality_row", {}).get("state", "")
        return doc_state == "present" and handoff_state == "ready" and quality_state == "ready"
    if module_kind == "package" or rows_kind == "package_record_rows":
        artifact_state = row_map.get("artifact_row", {}).get("state", "")
        verification_state = row_map.get("verification_row", {}).get("state", "")
        apply_state = row_map.get("apply_row", {}).get("state", "")
        return artifact_state == "present" and verification_state == "ready" and apply_state == "ready"
    return True


def worker_task_module_syncback_ready_from_rows(raw: Any) -> bool:
    row = sanitize_worker_task_module_record_rows(raw)
    module_kind = str(row.get("module_kind", "")).strip().lower()
    rows_kind = str(row.get("rows_kind", "")).strip()
    if module_kind != "package" and rows_kind != "package_record_rows":
        return True
    row_map = worker_task_module_record_row_map(row)
    artifact_state = row_map.get("artifact_row", {}).get("state", "")
    verification_state = row_map.get("verification_row", {}).get("state", "")
    apply_state = row_map.get("apply_row", {}).get("state", "")
    syncback_state = row_map.get("syncback_row", {}).get("state", "")
    return (
        artifact_state == "present"
        and verification_state == "ready"
        and apply_state == "ready"
        and syncback_state == "ready"
    )


def sanitize_worker_task_module_preflight(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_PREFLIGHT_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "preflight_kind": _trim(source.get("preflight_kind"), 96) or "-",
        "state": _trim(source.get("state"), 64) or "-",
        "signals": _uniq(source.get("signals"), limit=8, text_limit=120),
        "next_hint": _trim(source.get("next_hint"), 96) or "-",
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["preflight_kind"] not in {"", "-"}:
            parts.append(row["preflight_kind"])
        if row["state"] not in {"", "-"}:
            parts.append(f"state={row['state']}")
        parts.extend(list(row["signals"])[:4])
        if row["next_hint"] not in {"", "-"}:
            parts.append(f"next={row['next_hint']}")
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_preflight(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
    items: Any = None,
    item_classes: Any = None,
    records: Any = None,
    record_rows: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    rows_row = sanitize_worker_task_module_record_rows(record_rows) if isinstance(record_rows, dict) else {}
    if not list(rows_row.get("rows") or []):
        rows_row = derive_worker_task_module_record_rows(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
            items=items,
            item_classes=item_classes,
            records=records,
        )
    if not rows_row:
        return {}
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    row_map = worker_task_module_record_row_map(rows_row)

    def _signal(key: str, fallback: str = "-") -> str:
        return f"{key}={_trim(fallback, 64) or '-'}"

    if module_kind == "analysis":
        finding_state = row_map.get("finding_row", {}).get("state", "") or "-"
        evidence_state = row_map.get("evidence_row", {}).get("state", "") or "-"
        gap_state = row_map.get("gap_row", {}).get("state", "") or "-"
        review_ready = worker_task_module_apply_ready(rows_row)
        next_hint = "validate_caveats" if review_ready else "attach_evidence"
        return sanitize_worker_task_module_preflight(
            {
                "module_kind": module_kind,
                "preflight_kind": "analysis_preflight",
                "state": "review_ready" if review_ready else "review_open",
                "signals": [
                    _signal("finding", finding_state),
                    _signal("evidence", evidence_state),
                    _signal("gap", gap_state or "-"),
                    _signal("apply", "ready" if review_ready else "blocked"),
                ],
                "next_hint": next_hint,
            }
        )

    if module_kind == "writing":
        doc_state = row_map.get("doc_row", {}).get("state", "") or "-"
        handoff_state = row_map.get("handoff_row", {}).get("state", "") or "-"
        quality_state = row_map.get("quality_row", {}).get("state", "") or "-"
        handoff_ready = worker_task_module_apply_ready(rows_row)
        next_hint = "handoff_ready" if handoff_ready else "close_quality_gate"
        return sanitize_worker_task_module_preflight(
            {
                "module_kind": module_kind,
                "preflight_kind": "writing_preflight",
                "state": "handoff_ready" if handoff_ready else "handoff_open",
                "signals": [
                    _signal("doc", doc_state),
                    _signal("handoff", handoff_state),
                    _signal("quality", quality_state),
                    _signal("apply", "ready" if handoff_ready else "blocked"),
                ],
                "next_hint": next_hint,
            }
        )

    if module_kind == "package":
        verification_state = row_map.get("verification_row", {}).get("state", "") or "-"
        apply_state = row_map.get("apply_row", {}).get("state", "") or "-"
        syncback_state = row_map.get("syncback_row", {}).get("state", "") or "-"
        apply_ready = worker_task_module_apply_ready(rows_row)
        syncback_ready = worker_task_module_syncback_ready_from_rows(rows_row)
        state = "syncback_ready" if syncback_ready else ("apply_ready" if apply_ready else "artifact_open")
        next_hint = "syncback_clean" if syncback_ready else ("prepare_syncback" if apply_ready else "artifact_check_open")
        return sanitize_worker_task_module_preflight(
            {
                "module_kind": module_kind,
                "preflight_kind": "package_preflight",
                "state": state,
                "signals": [
                    _signal("verification", verification_state),
                    _signal("apply", apply_state),
                    _signal("syncback", syncback_state),
                ],
                "next_hint": next_hint,
            }
        )

    return sanitize_worker_task_module_preflight(
        {
            "module_kind": module_kind,
            "preflight_kind": "general_preflight",
            "state": "ready" if worker_task_module_apply_ready(rows_row) else "open",
            "signals": [_signal("apply", "ready" if worker_task_module_apply_ready(rows_row) else "blocked")],
            "next_hint": "review",
        }
    )


def summarize_worker_task_module_preflight(raw: Any) -> str:
    row = sanitize_worker_task_module_preflight(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_preflight_rows(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_PREFLIGHT_ROWS_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "rows_kind": _trim(source.get("rows_kind"), 96) or "-",
        "rows": _uniq(source.get("rows"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["rows_kind"] not in {"", "-"}:
            parts.append(row["rows_kind"])
        parts.extend(list(row["rows"])[:4])
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_preflight_rows(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
    items: Any = None,
    item_classes: Any = None,
    records: Any = None,
    record_rows: Any = None,
    preflight: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    rows_row = sanitize_worker_task_module_record_rows(record_rows) if isinstance(record_rows, dict) else {}
    if not list(rows_row.get("rows") or []):
        rows_row = derive_worker_task_module_record_rows(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
            items=items,
            item_classes=item_classes,
            records=records,
        )
    if not rows_row:
        return {}
    preflight_row = sanitize_worker_task_module_preflight(preflight) if isinstance(preflight, dict) else {}
    if _trim(preflight_row.get("state"), 64) in {"", "-"}:
        preflight_row = derive_worker_task_module_preflight(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate,
            profile=profile,
            checklist=checklist,
            items=items,
            item_classes=item_classes,
            records=records,
            record_rows=rows_row,
        )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    row_map = worker_task_module_record_row_map(rows_row)
    preflight_state = _trim(preflight_row.get("state"), 64).lower() or "-"
    next_hint = _trim(preflight_row.get("next_hint"), 96) or "-"

    def _row(label: str, value: str, state: str, *, note: str = "") -> str:
        token = f"{label}={_trim(value, 96) or '-'}|state={_trim(state, 48) or '-'}"
        if note:
            token += f"|note={_trim(note, 48) or '-'}"
        return token[:160]

    if module_kind == "analysis":
        finding_state = row_map.get("finding_row", {}).get("state", "") or "-"
        evidence_state = row_map.get("evidence_row", {}).get("state", "") or "-"
        gap_state = row_map.get("gap_row", {}).get("state", "") or "clear"
        review_ready = preflight_state == "review_ready"
        rows = [
            _row("finding_ready", finding_state, "ready" if finding_state == "stable" else "blocked", note="findings"),
            _row("evidence_ready", evidence_state, "ready" if evidence_state == "attached" else "blocked", note="evidence"),
            _row("gap_closed", gap_state if gap_state != "-" else "clear", "blocked" if gap_state == "open" else "ready", note=next_hint),
            _row("review_ready", preflight_state, "ready" if review_ready else "blocked", note=next_hint),
        ]
        return sanitize_worker_task_module_preflight_rows(
            {"module_kind": module_kind, "rows_kind": "analysis_preflight_rows", "rows": rows}
        )

    if module_kind == "writing":
        doc_state = row_map.get("doc_row", {}).get("state", "") or "-"
        handoff_state = row_map.get("handoff_row", {}).get("state", "") or "-"
        quality_state = row_map.get("quality_row", {}).get("state", "") or "-"
        handoff_ready = preflight_state == "handoff_ready"
        rows = [
            _row("doc_present", doc_state, "ready" if doc_state == "present" else "blocked", note="document"),
            _row("handoff_ready", handoff_state, "ready" if handoff_state == "ready" else "blocked", note="handoff"),
            _row("quality_ready", quality_state, "ready" if quality_state == "ready" else "blocked", note="quality_gate"),
            _row("writing_ready", preflight_state, "ready" if handoff_ready else "blocked", note=next_hint),
        ]
        return sanitize_worker_task_module_preflight_rows(
            {"module_kind": module_kind, "rows_kind": "writing_preflight_rows", "rows": rows}
        )

    if module_kind == "package":
        verification_state = row_map.get("verification_row", {}).get("state", "") or "-"
        apply_state = row_map.get("apply_row", {}).get("state", "") or "-"
        syncback_state = row_map.get("syncback_row", {}).get("state", "") or "-"
        syncback_ready = preflight_state == "syncback_ready"
        apply_ready = preflight_state in {"syncback_ready", "apply_ready"}
        rows = [
            _row("verification_ready", verification_state, "ready" if verification_state == "ready" else "blocked", note="verification"),
            _row("apply_ready", apply_state, "ready" if apply_state == "ready" else "blocked", note="apply_gate"),
            _row("syncback_ready", syncback_state, "ready" if syncback_state == "ready" else "blocked", note=next_hint),
            _row("package_ready", preflight_state, "ready" if syncback_ready or apply_ready else "blocked", note=next_hint),
        ]
        return sanitize_worker_task_module_preflight_rows(
            {"module_kind": module_kind, "rows_kind": "package_preflight_rows", "rows": rows}
        )

    return sanitize_worker_task_module_preflight_rows(
        {
            "module_kind": module_kind,
            "rows_kind": "general_preflight_rows",
            "rows": [_row("apply_ready", preflight_state, "ready" if preflight_state == "ready" else "blocked", note=next_hint)],
        }
    )


def summarize_worker_task_module_preflight_rows(raw: Any) -> str:
    row = sanitize_worker_task_module_preflight_rows(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def sanitize_worker_task_module_action_blocker(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_MODULE_ACTION_BLOCKER_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48).lower() or "general",
        "mode": _trim(source.get("mode"), 32).lower() or "apply",
        "blocker_kind": _trim(source.get("blocker_kind"), 96) or "-",
        "reason_code": _trim(source.get("reason_code"), 96) or "-",
        "next_hint": _trim(source.get("next_hint"), 96) or "-",
        "blocked_rows": _uniq(source.get("blocked_rows"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320)
    if not row["summary_line"]:
        parts = []
        if row["blocker_kind"] not in {"", "-"}:
            parts.append(row["blocker_kind"])
        if row["reason_code"] not in {"", "-"}:
            parts.append(f"reason={row['reason_code']}")
        if row["blocked_rows"]:
            labels = []
            for token in list(row["blocked_rows"])[:3]:
                label = str(token).split("=", 1)[0].strip()
                if label:
                    labels.append(label)
            if labels:
                parts.append(f"blocked={','.join(labels)}")
        if row["next_hint"] not in {"", "-"}:
            parts.append(f"next={row['next_hint']}")
        row["summary_line"] = " | ".join(parts)[:320] if parts else "-"
    return row


def derive_worker_task_module_action_blocker(
    raw: Any,
    *,
    mode: str = "apply",
) -> Dict[str, Any]:
    rows_row = sanitize_worker_task_module_preflight_rows(raw)
    module_kind = _trim(rows_row.get("module_kind"), 48).lower() or "general"
    row_map = worker_task_module_record_row_map(rows_row)
    safe_mode = _trim(mode, 32).lower() or "apply"

    def _entry(label: str) -> Dict[str, str]:
        return row_map.get(label, {})

    def _blocked(*labels: str) -> List[str]:
        out: List[str] = []
        for label in labels:
            entry = _entry(label)
            if entry.get("state") == "blocked":
                token = f"{label}={entry.get('value', '-')}"
                if entry.get("state"):
                    token += f"|state={entry.get('state')}"
                if entry.get("note"):
                    token += f"|note={entry.get('note')}"
                out.append(token[:160])
        return out

    reason_code = "worker_apply_not_ready"
    next_hint = "-"
    blocked_rows: List[str] = []

    if module_kind == "analysis":
        if _entry("evidence_ready").get("state") == "blocked":
            reason_code = "analysis_evidence_missing"
            next_hint = _entry("evidence_ready").get("note", "") or "attach_evidence"
        elif _entry("gap_closed").get("state") == "blocked":
            reason_code = "analysis_gap_open"
            next_hint = _entry("gap_closed").get("note", "") or "validate_caveats"
        elif _entry("finding_ready").get("state") == "blocked":
            reason_code = "analysis_findings_open"
            next_hint = _entry("finding_ready").get("note", "") or "findings"
        else:
            reason_code = "analysis_review_not_ready"
            next_hint = _entry("review_ready").get("note", "") or "review"
        blocked_rows = _blocked("finding_ready", "evidence_ready", "gap_closed", "review_ready")
    elif module_kind == "writing":
        if _entry("quality_ready").get("state") == "blocked":
            reason_code = "writing_quality_open"
            next_hint = _entry("quality_ready").get("note", "") or "close_quality_gate"
        elif _entry("handoff_ready").get("state") == "blocked":
            reason_code = "writing_handoff_waiting"
            next_hint = _entry("handoff_ready").get("note", "") or "handoff"
        elif _entry("doc_present").get("state") == "blocked":
            reason_code = "writing_doc_missing"
            next_hint = _entry("doc_present").get("note", "") or "document"
        else:
            reason_code = "writing_handoff_not_ready"
            next_hint = _entry("writing_ready").get("note", "") or "close_quality_gate"
        blocked_rows = _blocked("doc_present", "handoff_ready", "quality_ready", "writing_ready")
    elif module_kind == "package":
        if safe_mode == "syncback" and _entry("syncback_ready").get("state") == "blocked":
            reason_code = "package_syncback_pending"
            next_hint = _entry("syncback_ready").get("note", "") or "prepare_syncback"
        elif _entry("verification_ready").get("state") == "blocked":
            reason_code = "package_verification_open"
            next_hint = _entry("verification_ready").get("note", "") or "verification"
        elif _entry("apply_ready").get("state") == "blocked":
            reason_code = "package_apply_pending"
            next_hint = _entry("apply_ready").get("note", "") or "apply_gate"
        elif _entry("syncback_ready").get("state") == "blocked":
            reason_code = "package_syncback_pending"
            next_hint = _entry("syncback_ready").get("note", "") or "prepare_syncback"
        else:
            reason_code = "package_artifact_open"
            next_hint = _entry("package_ready").get("note", "") or "artifact_check_open"
        blocked_rows = _blocked("verification_ready", "apply_ready", "syncback_ready", "package_ready")
    else:
        blocked_rows = list(rows_row.get("rows") or [])[:3]
        next_hint = "-"

    return sanitize_worker_task_module_action_blocker(
        {
            "module_kind": module_kind,
            "mode": safe_mode,
            "blocker_kind": f"{module_kind}_{safe_mode}_blocker",
            "reason_code": reason_code,
            "next_hint": next_hint,
            "blocked_rows": blocked_rows,
        }
    )


def summarize_worker_task_module_action_blocker(raw: Any) -> str:
    row = sanitize_worker_task_module_action_blocker(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def derive_worker_task_module_record_rows(
    contract: Any,
    result: Any,
    *,
    update_stub: Any = None,
    gate: Any = None,
    profile: Any = None,
    checklist: Any = None,
    items: Any = None,
    item_classes: Any = None,
    records: Any = None,
) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not contract_row or not result_row:
        return {}
    gate_row = (
        sanitize_worker_task_module_gate(gate)
        if isinstance(gate, dict)
        else derive_worker_task_module_gate(contract_row, result_row, update_stub=update_stub)
    )
    profile_row = (
        sanitize_worker_task_module_profile(profile)
        if isinstance(profile, dict)
        else derive_worker_task_module_profile(contract_row, result_row, update_stub=update_stub, gate=gate_row)
    )
    checklist_row = (
        sanitize_worker_task_module_checklist(checklist)
        if isinstance(checklist, dict)
        else derive_worker_task_module_checklist(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate_row,
            profile=profile_row,
        )
    )
    items_row = (
        sanitize_worker_task_module_items(items)
        if isinstance(items, dict)
        else derive_worker_task_module_items(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate_row,
            profile=profile_row,
            checklist=checklist_row,
        )
    )
    item_classes_row = (
        sanitize_worker_task_module_item_classes(item_classes)
        if isinstance(item_classes, dict)
        else derive_worker_task_module_item_classes(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate_row,
            profile=profile_row,
            checklist=checklist_row,
            items=items_row,
        )
    )
    records_row = (
        sanitize_worker_task_module_records(records)
        if isinstance(records, dict)
        else derive_worker_task_module_records(
            contract_row,
            result_row,
            update_stub=update_stub,
            gate=gate_row,
            profile=profile_row,
            checklist=checklist_row,
            items=items_row,
            item_classes=item_classes_row,
        )
    )
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    gate_state = _trim(gate_row.get("state"), 64).lower() or "-"
    checklist_state = _trim(checklist_row.get("state"), 64).lower() or "-"
    record_map = worker_task_module_record_map(records_row)

    def _row(label: str, value: str, state: str, *, note: str = "") -> str:
        token = f"{label}={_trim(value, 96) or '-'}|state={_trim(state, 48) or '-'}"
        if note:
            token += f"|note={_trim(note, 48) or '-'}"
        return token[:160]

    if module_kind == "analysis":
        gap_open = record_map.get("gap_record") == "evidence_missing"
        rows = [
            _row(
                "finding_row",
                record_map.get("finding_record", "-"),
                "stable" if gate_state == "findings_stable" else "open",
            ),
            _row(
                "evidence_row",
                record_map.get("evidence_record", "-"),
                "attached" if not gap_open else "missing",
            ),
        ]
        if gap_open:
            rows.append(_row("gap_row", "evidence_missing", "open", note=checklist_state or "review"))
        else:
            rows.append(
                _row(
                    "caveat_row",
                    record_map.get("caveat_record", "-"),
                    "review" if record_map.get("caveat_record", "-") not in {"", "-"} else "clear",
                    note=checklist_state or "validate_caveats",
                )
            )
        return sanitize_worker_task_module_record_rows(
            {"module_kind": module_kind, "rows_kind": "analysis_record_rows", "rows": rows}
        )

    if module_kind == "writing":
        quality_state = "open" if record_map.get("quality_record") == "open" else "ready"
        handoff_state = "ready" if record_map.get("handoff_record") == "ready" else "waiting"
        rows = [
            _row("doc_row", record_map.get("doc_record", "-"), "present"),
            _row("handoff_row", record_map.get("handoff_record", "-"), handoff_state, note=checklist_state or "handoff"),
            _row("quality_row", record_map.get("quality_record", "-"), quality_state, note=checklist_state or "quality_gate"),
        ]
        return sanitize_worker_task_module_record_rows(
            {"module_kind": module_kind, "rows_kind": "writing_record_rows", "rows": rows}
        )

    if module_kind == "package":
        rows = [
            _row("artifact_row", record_map.get("artifact_record", "-"), "present"),
            _row(
                "verification_row",
                record_map.get("verification_record", "-"),
                "ready" if record_map.get("verification_record") not in {"", "-", "0"} else "open",
            ),
            _row("apply_row", record_map.get("apply_record", "-"), record_map.get("apply_record", "-")),
            _row(
                "syncback_row",
                record_map.get("syncback_record", "-"),
                "ready" if record_map.get("syncback_record") == "ready" else "blocked",
                note=checklist_state or "syncback",
            ),
        ]
        return sanitize_worker_task_module_record_rows(
            {"module_kind": module_kind, "rows_kind": "package_record_rows", "rows": rows}
        )

    rows = [
        _row("action_row", record_map.get("action_record", "-"), "ready"),
        _row("ref_row", record_map.get("ref_record", "-"), "attached"),
    ]
    return sanitize_worker_task_module_record_rows(
        {"module_kind": module_kind, "rows_kind": "general_record_rows", "rows": rows}
    )


def summarize_worker_task_module_record_rows(raw: Any) -> str:
    row = sanitize_worker_task_module_record_rows(raw)
    return _trim(row.get("summary_line"), 320) or "-"


def build_worker_task_contract(
    team_dir: Path | str,
    *,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path | str] = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    entry_data = entry if isinstance(entry, dict) else {}
    task_data = task if isinstance(task, dict) else {}
    team_path = Path(team_dir).expanduser().resolve()
    pack = load_context_pack(
        team_path,
        entry=entry_data,
        task=task_data,
        project_root=project_root or entry_data.get("project_root"),
    )
    pack_profile = _trim(pack_profile_override, 64) or _trim(pack.get("profile"), 64) or "offdesk_execute"
    return sanitize_worker_task_contract(
        {
            "version": WORKER_TASK_CONTRACT_VERSION,
            "request_id": _trim(task_data.get("request_id"), 96),
            "task_id": _trim(task_data.get("short_id"), 48),
            "task_label": _task_label(task_data),
            "project_alias": _trim(entry_data.get("project_alias"), 32),
            "project_label": _trim(entry_data.get("display_name") or entry_data.get("name"), 96),
            "status": _trim(task_data.get("status"), 48),
            "tf_phase": _trim(task_data.get("tf_phase"), 48),
            "contract_preset": _trim(
                task_data.get("request_contract_preset")
                or task_data.get("phase2_team_preset")
                or task_data.get("phase1_role_preset"),
                48,
            ),
            "pack_profile": pack_profile,
            "objective": _trim(pack.get("objective") or task_data.get("prompt") or task_data.get("alias"), 320),
            "execution_brief_status": _trim(task_data.get("execution_brief_status"), 48),
            "execution_brief_summary": _trim(task_data.get("execution_brief_summary"), 320),
            "execution_brief_operator_decision": _trim(task_data.get("execution_brief_operator_decision"), 320),
            "followup_brief_status": _trim(task_data.get("followup_brief_status"), 48),
            "followup_brief_summary": _trim(task_data.get("followup_brief_summary"), 320),
            "reentry_rails_summary": _trim(task_data.get("reentry_rails_summary"), 320),
            "constraints": list(pack.get("constraints") or []),
            "doc_paths": [
                _trim(row.get("path"), 200)
                for row in (pack.get("relevant_docs") or [])
                if isinstance(row, dict) and _trim(row.get("path"), 200)
            ],
            "required_outputs": [
                _trim(row, 160)
                for row in (task_data.get("request_contract_required_outputs") or [])
                if _trim(row, 160)
            ],
            "artifact_targets": [
                _trim(((task_data.get("request_contract_artifact_contracts") or {}).get(alias) or {}).get("path"), 160)
                or _trim(alias, 160)
                for alias in (task_data.get("request_contract_required_outputs") or [])
                if _trim(alias, 160)
            ],
            "known_failures": list(pack.get("known_failures") or []),
            "unresolved_questions": list(pack.get("unresolved_questions") or []),
        }
    )


def render_worker_task_prompt(contract: Any) -> Dict[str, str]:
    row = load_worker_task_contract(contract)
    if not row:
        return {"system": WORKER_TASK_SYSTEM, "prompt": "", "summary": "-"}
    payload = {
        "runtime": _trim(row.get("project_alias"), 32) or "-",
        "project": _trim(row.get("project_label"), 96) or "-",
        "task": _trim(row.get("task_label"), 96) or "-",
        "request_id": _trim(row.get("request_id"), 96) or "-",
        "status": _trim(row.get("status"), 48) or "-",
        "tf_phase": _trim(row.get("tf_phase"), 48) or "-",
        "contract_preset": _trim(row.get("contract_preset"), 48) or "-",
        "module_kind": _trim(row.get("module_kind"), 48) or "-",
        "module_reason": _trim(row.get("module_reason"), 240) or "-",
        "module_summary": _trim(row.get("module_summary"), 240) or "-",
        "module_policy": _trim(row.get("module_policy"), 64) or "-",
        "module_result_focus": _trim(row.get("module_result_focus"), 96) or "-",
        "module_apply_gate": _trim(row.get("module_apply_gate"), 96) or "-",
        "module_loop_mode": _trim(row.get("module_loop_mode"), 64) or "-",
        "module_proposal_kind": _trim(row.get("module_proposal_kind"), 32) or "-",
        "module_apply_kind": _trim(row.get("module_apply_kind"), 32) or "-",
        "module_proposal_priority": _trim(row.get("module_proposal_priority"), 8) or "-",
        "module_apply_priority": _trim(row.get("module_apply_priority"), 8) or "-",
        "module_repeat_when": _trim(row.get("module_repeat_when"), 96) or "-",
        "module_stop_when": _trim(row.get("module_stop_when"), 96) or "-",
        "module_policy_summary": _trim(row.get("module_policy_summary"), 240) or "-",
        "pack_profile": _trim(row.get("pack_profile"), 64) or "-",
        "objective": _trim(row.get("objective"), 320) or "-",
        "execution_brief_status": _trim(row.get("execution_brief_status"), 48) or "-",
        "execution_brief_summary": _trim(row.get("execution_brief_summary"), 320) or "-",
        "execution_brief_operator_decision": _trim(row.get("execution_brief_operator_decision"), 320) or "-",
        "followup_brief_status": _trim(row.get("followup_brief_status"), 48) or "-",
        "followup_brief_summary": _trim(row.get("followup_brief_summary"), 320) or "-",
        "reentry_rails_summary": _trim(row.get("reentry_rails_summary"), 320) or "-",
        "constraints": list(row.get("constraints") or []),
        "doc_paths": list(row.get("doc_paths") or []),
        "required_outputs": list(row.get("required_outputs") or []),
        "artifact_targets": list(row.get("artifact_targets") or []),
        "known_failures": list(row.get("known_failures") or []),
        "unresolved_questions": list(row.get("unresolved_questions") or []),
    }
    prompt = (
        "Use the bounded worker task contract below.\n"
        "Do not invent missing context beyond the listed constraints and doc paths.\n"
        "Return strict JSON only.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return {
        "system": WORKER_TASK_SYSTEM,
        "prompt": prompt,
        "summary": _trim(row.get("summary"), 320) or _summary(row),
    }


def _infer_action_paths(actions: List[str]) -> List[str]:
    inferred: List[str] = []
    pattern = re.compile(r"\b(?:update|write|create|edit)\s+([A-Za-z0-9_./-]+\.[A-Za-z0-9_-]+|[A-Za-z0-9_./-]+)", re.IGNORECASE)
    for action in actions:
        text = _trim(action, 240)
        if not text:
            continue
        match = pattern.search(text)
        if not match:
            continue
        token = _trim(match.group(1), 160)
        if token and token not in inferred:
            inferred.append(token)
    return inferred[:6]


def _update_stub_summary(row: Dict[str, Any]) -> str:
    module_kind = _trim(row.get("module_kind"), 48) or "-"
    status = _trim(row.get("status"), 48) or "-"
    targets = list(row.get("target_artifacts") or [])
    actions = list(row.get("actions") or [])
    refs = list(row.get("evidence_refs") or [])
    target_text = ",".join(targets[:2]) if targets else "-"
    parts: List[str] = []
    if module_kind not in {"", "-", "general"}:
        parts.append(f"module={module_kind}")
    parts.extend([f"status={status}", f"targets={target_text}"])
    if actions:
        parts.append(f"actions={len(actions)}")
    if refs:
        parts.append(f"refs={len(refs)}")
    return " | ".join(parts)[:320]


def sanitize_worker_task_update_stub(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_UPDATE_STUB_VERSION,
        "module_kind": _trim(source.get("module_kind"), 48) or "-",
        "module_summary": _trim(source.get("module_summary"), 240) or "-",
        "module_policy": _trim(source.get("module_policy"), 64) or "-",
        "module_policy_summary": _trim(source.get("module_policy_summary"), 240) or "-",
        "status": _trim(source.get("status"), 48) or "-",
        "target_artifacts": _uniq(source.get("target_artifacts"), limit=8, text_limit=160),
        "actions": _uniq(source.get("actions"), limit=4, text_limit=160),
        "cautions": _uniq(source.get("cautions"), limit=4, text_limit=160),
        "evidence_refs": _uniq(source.get("evidence_refs"), limit=8, text_limit=160),
    }
    row["summary_line"] = _trim(source.get("summary_line"), 320) or _update_stub_summary(row)
    return row


def derive_worker_task_update_stub(contract: Any, result: Any) -> Dict[str, Any]:
    contract_row = load_worker_task_contract(contract)
    result_row = load_worker_task_result(result)
    if not result_row:
        return {}
    actions = list(result_row.get("actions") or [])
    cautions = list(result_row.get("cautions") or [])
    refs = list(result_row.get("evidence_refs") or [])
    targets = []
    for token in list(contract_row.get("artifact_targets") or []) + _infer_action_paths(actions) + refs:
        clean = _trim(token, 160)
        if clean and clean not in targets:
            targets.append(clean)
    status = "ready" if (targets or actions or refs) else "none"
    return sanitize_worker_task_update_stub(
        {
            "module_kind": contract_row.get("module_kind"),
            "module_summary": contract_row.get("module_summary"),
            "module_policy": contract_row.get("module_policy"),
            "module_policy_summary": contract_row.get("module_policy_summary"),
            "status": status,
            "target_artifacts": targets,
            "actions": actions,
            "cautions": cautions,
            "evidence_refs": refs,
        }
    )


def _proposal_key(text: Any) -> str:
    return " ".join(str(text or "").strip().split()).lower()[:240]


def _priority_rank(token: Any) -> int:
    raw = _trim(token, 8).upper()
    return {"P1": 1, "P2": 2, "P3": 3}.get(raw, 9)


def _priority_from_rank(rank: int) -> str:
    return {1: "P1", 2: "P2", 3: "P3"}.get(int(rank or 0), "P2")


def _module_priority_floor(module_kind: str, *, apply: bool = False) -> str:
    defaults = dict(WORKER_MODULE_POLICY_DEFAULTS.get(module_kind) or WORKER_MODULE_POLICY_DEFAULTS["general"])
    key = "apply_priority" if apply else "proposal_priority"
    token = _trim(defaults.get(key), 8).upper()
    return token if token in {"P1", "P2", "P3"} else "P2"


def _module_proposal_kind(module_kind: str, *, apply: bool = False) -> str:
    defaults = dict(WORKER_MODULE_POLICY_DEFAULTS.get(module_kind) or WORKER_MODULE_POLICY_DEFAULTS["general"])
    key = "apply_kind" if apply else "proposal_kind"
    token = _trim(defaults.get(key), 32).lower()
    return token if token in {"followup", "handoff", "risk", "debt"} else "followup"


def _module_proposal_confidence(module_kind: str, *, apply: bool = False) -> float:
    table = {
        ("analysis", False): 0.68,
        ("analysis", True): 0.58,
        ("writing", False): 0.74,
        ("writing", True): 0.78,
        ("package", False): 0.82,
        ("package", True): 0.88,
        ("general", False): 0.64,
        ("general", True): 0.72,
    }
    return float(table.get((module_kind, bool(apply)), table[("general", bool(apply))]))


def _proposal_priority(update_stub: Dict[str, Any], *, apply: bool = False) -> str:
    module_kind = _trim(update_stub.get("module_kind"), 48).lower() or "general"
    floor = _module_priority_floor(module_kind, apply=apply)
    caution_text = " ".join(str(item).strip().lower() for item in (update_stub.get("cautions") or []) if str(item).strip())
    if any(token in caution_text for token in ("risk", "blocked", "error", "fail", "manual")):
        return "P1"
    base = "P2" if list(update_stub.get("target_artifacts") or []) else "P3"
    return _priority_from_rank(min(_priority_rank(base), _priority_rank(floor)))


def _proposal_summary_for_module(
    module_kind: str,
    *,
    task_label: str,
    target: str = "",
    action_head: str = "",
    apply: bool = False,
) -> str:
    target_token = _trim(target, 160)
    action_token = _trim(action_head, 160)
    if apply:
        if module_kind == "package":
            return (f"apply package artifact for {task_label}: {target_token or action_token or 'artifact update'}")[:600]
        if module_kind == "writing":
            return (f"apply writing artifact update for {task_label}: {target_token or action_token or 'doc update'}")[:600]
        if target_token:
            return (f"apply worker artifact update for {task_label}: {target_token}")[:600]
        if action_token:
            return (f"apply worker follow-up update for {task_label}: {action_token}")[:600]
        return (f"apply worker update for {task_label}")[:600]
    if module_kind == "package":
        return (f"review package output for {task_label}: {target_token or action_token or 'artifact verification'}")[:600]
    if module_kind == "writing":
        return (f"review writing draft for {task_label}: {target_token or action_token or 'draft update'}")[:600]
    if target_token:
        return (f"review worker artifact update for {task_label}: {target_token}")[:600]
    if action_token:
        return (f"review worker follow-up for {task_label}: {action_token}")[:600]
    return (f"review worker update stub for {task_label}")[:600]


def derive_worker_update_todo_proposals(contract: Any, update_stub: Any) -> List[Dict[str, Any]]:
    contract_row = load_worker_task_contract(contract)
    stub = sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return []
    status = _trim(stub.get("status"), 48).lower()
    if status in {"", "-", "none"}:
        return []
    task_label = _trim(contract_row.get("task_label"), 96) or "task"
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    reason = _trim(stub.get("summary_line"), 240) or _update_stub_summary(stub)
    priority = _proposal_priority({**stub, "module_kind": module_kind}, apply=False)
    proposal_kind = _module_proposal_kind(module_kind, apply=False)
    confidence = _module_proposal_confidence(module_kind, apply=False)
    targets = list(stub.get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    actions = list(stub.get("actions") or [])
    proposals: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets[:3]:
        clean = _trim(target, 160)
        if not clean:
            continue
        summary = _proposal_summary_for_module(module_kind, task_label=task_label, target=clean, apply=False)
        key = _proposal_key(summary)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            {
                "version": WORKER_TASK_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": proposal_kind,
                "reason": reason,
                "confidence": confidence,
                "created_by": "worker",
                "source_file": clean,
                "source_reason": "worker_update_stub",
            }
        )
    if not proposals:
        action_head = _trim(actions[0] if actions else "", 160)
        summary = _proposal_summary_for_module(module_kind, task_label=task_label, action_head=action_head, apply=False)
        proposals.append(
            {
                "version": WORKER_TASK_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": proposal_kind,
                "reason": reason,
                "confidence": confidence,
                "created_by": "worker",
                "source_reason": "worker_update_stub",
            }
        )
    return proposals


def derive_worker_artifact_apply_todo_proposals(contract: Any, update_stub: Any) -> List[Dict[str, Any]]:
    contract_row = load_worker_task_contract(contract)
    stub = sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return []
    status = _trim(stub.get("status"), 48).lower()
    if status in {"", "-", "none"}:
        return []
    task_label = _trim(contract_row.get("task_label"), 96) or "task"
    module_kind = _trim(contract_row.get("module_kind"), 48).lower() or "general"
    reason = _trim(stub.get("summary_line"), 240) or _update_stub_summary(stub)
    priority = _proposal_priority({**stub, "module_kind": module_kind}, apply=True)
    proposal_kind = _module_proposal_kind(module_kind, apply=True)
    confidence = _module_proposal_confidence(module_kind, apply=True)
    targets = list(stub.get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    actions = list(stub.get("actions") or [])
    proposals: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets[:3]:
        clean = _trim(target, 160)
        if not clean:
            continue
        summary = _proposal_summary_for_module(module_kind, task_label=task_label, target=clean, apply=True)
        key = _proposal_key(summary)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            {
                "version": WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": proposal_kind,
                "reason": reason,
                "confidence": confidence,
                "created_by": "worker",
                "source_file": clean,
                "source_reason": "worker_artifact_apply",
            }
        )
    if not proposals:
        action_head = _trim(actions[0] if actions else "", 160)
        summary = _proposal_summary_for_module(module_kind, task_label=task_label, action_head=action_head, apply=True)
        proposals.append(
            {
                "version": WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": proposal_kind,
                "reason": reason,
                "confidence": confidence,
                "created_by": "worker",
                "source_reason": "worker_artifact_apply",
            }
        )
    return proposals


def match_worker_update_proposal_ids(
    proposals_store: Any,
    *,
    request_id: Any,
    proposal_payloads: Any,
) -> List[str]:
    store = proposals_store if isinstance(proposals_store, list) else []
    payloads = proposal_payloads if isinstance(proposal_payloads, list) else []
    request_token = _trim(request_id, 128)
    target_keys = {
        _proposal_key(row.get("summary"))
        for row in payloads
        if isinstance(row, dict) and _proposal_key(row.get("summary"))
    }
    if not target_keys:
        return []
    ids: List[str] = []
    for row in store:
        if not isinstance(row, dict):
            continue
        if request_token and _trim(row.get("source_request_id"), 128) != request_token:
            continue
        if _proposal_key(row.get("summary")) not in target_keys:
            continue
        proposal_id = _trim(row.get("id"), 32)
        if proposal_id and proposal_id not in ids:
            ids.append(proposal_id)
    return ids[:8]


def summarize_worker_update_proposal_summary(update_stub: Any, proposal_ids: Any) -> str:
    stub = sanitize_worker_task_update_stub(update_stub)
    ids = _uniq(proposal_ids, limit=8, text_limit=32)
    if not stub and not ids:
        return "-"
    target_text = ",".join(list(stub.get("target_artifacts") or [])[:2]) if stub else "-"
    parts = []
    status = _trim((stub or {}).get("status"), 48) or "-"
    module_kind = _trim((stub or {}).get("module_kind"), 48) or "-"
    if module_kind not in {"", "-", "general"}:
        parts.append(f"module={module_kind}")
    if status != "-":
        parts.append(f"status={status}")
    if ids:
        parts.append(f"proposals={len(ids)}")
        parts.append(f"ids={','.join(ids[:2])}")
    if target_text and target_text != "-":
        parts.append(f"targets={target_text}")
    return " | ".join(parts)[:320] if parts else "-"


def summarize_worker_update_operator_summary(update_stub: Any, proposal_ids: Any) -> str:
    stub = sanitize_worker_task_update_stub(update_stub)
    proposal_summary = summarize_worker_update_proposal_summary(stub, proposal_ids)
    if proposal_summary != "-":
        return proposal_summary
    if not stub:
        return "-"
    return _trim(stub.get("summary_line"), 320) or _update_stub_summary(stub)


def summarize_worker_module_line(raw: Any) -> str:
    source = raw if isinstance(raw, dict) else {}
    module_kind = _trim(source.get("module_kind"), 48) or "-"
    module_summary = _trim(source.get("module_summary"), 240) or "-"
    module_policy_summary = _trim(source.get("module_policy_summary"), 240) or "-"
    if module_kind in {"", "-", "general"} and module_policy_summary in {
        "",
        "-",
        "general | policy=general_gate | result=summary+actions | apply=standard_review | loop=single_pass",
    }:
        return "-"
    parts: List[str] = []
    if module_kind not in {"", "-"}:
        parts.append(module_kind)
    if module_summary not in {"", "-"}:
        parts.append(module_summary)
    if module_policy_summary not in {"", "-"}:
        parts.append(module_policy_summary)
    return " | ".join(parts)[:320] or "-"


def summarize_worker_artifact_apply_proposal_summary(update_stub: Any, proposal_ids: Any) -> str:
    stub = sanitize_worker_task_update_stub(update_stub)
    ids = _uniq(proposal_ids, limit=8, text_limit=32)
    if not stub and not ids:
        return "-"
    target_text = ",".join(list(stub.get("target_artifacts") or [])[:2]) if stub else "-"
    parts = []
    status = _trim((stub or {}).get("status"), 48) or "-"
    module_kind = _trim((stub or {}).get("module_kind"), 48) or "-"
    if module_kind not in {"", "-", "general"}:
        parts.append(f"module={module_kind}")
    if status != "-":
        parts.append(f"status={status}")
    if ids:
        parts.append(f"apply_proposals={len(ids)}")
        parts.append(f"ids={','.join(ids[:2])}")
    if target_text and target_text != "-":
        parts.append(f"targets={target_text}")
    return " | ".join(parts)[:320] if parts else "-"


def summarize_worker_artifact_apply_accept_summary(
    *,
    proposal_id: Any,
    todo_id: Any,
    target_artifacts: Any,
    accepted_at: Any,
) -> str:
    proposal_token = _trim(proposal_id, 32)
    todo_token = _trim(todo_id, 32)
    accepted_token = _trim(accepted_at, 64)
    targets = _uniq(target_artifacts, limit=4, text_limit=160)
    parts = ["state=applied"]
    if todo_token:
        parts.append(f"todo={todo_token}")
    if proposal_token:
        parts.append(f"proposal={proposal_token}")
    if targets:
        parts.append(f"targets={','.join(targets[:2])}")
    if accepted_token:
        parts.append(f"at={accepted_token}")
    return " | ".join(parts)[:320]
