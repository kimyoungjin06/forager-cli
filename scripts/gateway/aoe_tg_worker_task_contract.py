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
WORKER_TASK_UPDATE_STUB_VERSION = "2026-04-11.v1"
WORKER_TASK_PROPOSAL_STUB_VERSION = "2026-04-11.v1"
WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION = "2026-04-11.v1"
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
        f"task={_trim(contract.get('task_label'), 64) or '-'}",
        f"pack={_trim(contract.get('pack_profile'), 64) or '-'}",
        f"brief={_trim(contract.get('execution_brief_status'), 48) or '-'}",
        f"docs={len(contract.get('doc_paths') or [])}",
    ]
    return " | ".join(parts)[:320]


def sanitize_worker_task_contract(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    contract = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_CONTRACT_VERSION,
        "request_id": _trim(source.get("request_id"), 96),
        "task_id": _trim(source.get("task_id"), 48),
        "task_label": _trim(source.get("task_label"), 96) or "task",
        "project_alias": _trim(source.get("project_alias"), 32),
        "project_label": _trim(source.get("project_label"), 96),
        "status": _trim(source.get("status"), 48) or "-",
        "tf_phase": _trim(source.get("tf_phase"), 48) or "-",
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
    status = _trim(row.get("status"), 48) or "-"
    targets = list(row.get("target_artifacts") or [])
    actions = list(row.get("actions") or [])
    refs = list(row.get("evidence_refs") or [])
    target_text = ",".join(targets[:2]) if targets else "-"
    parts = [f"status={status}", f"targets={target_text}"]
    if actions:
        parts.append(f"actions={len(actions)}")
    if refs:
        parts.append(f"refs={len(refs)}")
    return " | ".join(parts)[:320]


def sanitize_worker_task_update_stub(raw: Any) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    row = {
        "version": _trim(source.get("version"), 48) or WORKER_TASK_UPDATE_STUB_VERSION,
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
            "status": status,
            "target_artifacts": targets,
            "actions": actions,
            "cautions": cautions,
            "evidence_refs": refs,
        }
    )


def _proposal_key(text: Any) -> str:
    return " ".join(str(text or "").strip().split()).lower()[:240]


def _proposal_priority(update_stub: Dict[str, Any]) -> str:
    caution_text = " ".join(str(item).strip().lower() for item in (update_stub.get("cautions") or []) if str(item).strip())
    if any(token in caution_text for token in ("risk", "blocked", "error", "fail", "manual")):
        return "P1"
    if list(update_stub.get("target_artifacts") or []):
        return "P2"
    return "P3"


def derive_worker_update_todo_proposals(contract: Any, update_stub: Any) -> List[Dict[str, Any]]:
    contract_row = load_worker_task_contract(contract)
    stub = sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return []
    status = _trim(stub.get("status"), 48).lower()
    if status in {"", "-", "none"}:
        return []
    task_label = _trim(contract_row.get("task_label"), 96) or "task"
    reason = _trim(stub.get("summary_line"), 240) or _update_stub_summary(stub)
    priority = _proposal_priority(stub)
    targets = list(stub.get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    actions = list(stub.get("actions") or [])
    proposals: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets[:3]:
        clean = _trim(target, 160)
        if not clean:
            continue
        summary = f"review worker artifact update for {task_label}: {clean}"[:600]
        key = _proposal_key(summary)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            {
                "version": WORKER_TASK_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": "handoff",
                "reason": reason,
                "confidence": 0.72,
                "created_by": "worker",
                "source_file": clean,
                "source_reason": "worker_update_stub",
            }
        )
    if not proposals:
        action_head = _trim(actions[0] if actions else "", 160)
        summary = (
            f"review worker follow-up for {task_label}: {action_head}"[:600]
            if action_head
            else f"review worker update stub for {task_label}"[:600]
        )
        proposals.append(
            {
                "version": WORKER_TASK_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": "followup",
                "reason": reason,
                "confidence": 0.64,
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
    reason = _trim(stub.get("summary_line"), 240) or _update_stub_summary(stub)
    priority = _proposal_priority(stub)
    targets = list(stub.get("target_artifacts") or []) or list(contract_row.get("artifact_targets") or [])
    actions = list(stub.get("actions") or [])
    proposals: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for target in targets[:3]:
        clean = _trim(target, 160)
        if not clean:
            continue
        summary = f"apply worker artifact update for {task_label}: {clean}"[:600]
        key = _proposal_key(summary)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            {
                "version": WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": "handoff",
                "reason": reason,
                "confidence": 0.8,
                "created_by": "worker",
                "source_file": clean,
                "source_reason": "worker_artifact_apply",
            }
        )
    if not proposals:
        action_head = _trim(actions[0] if actions else "", 160)
        summary = (
            f"apply worker follow-up update for {task_label}: {action_head}"[:600]
            if action_head
            else f"apply worker update for {task_label}"[:600]
        )
        proposals.append(
            {
                "version": WORKER_TASK_APPLY_PROPOSAL_STUB_VERSION,
                "summary": summary,
                "priority": priority,
                "kind": "followup",
                "reason": reason,
                "confidence": 0.72,
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


def summarize_worker_artifact_apply_proposal_summary(update_stub: Any, proposal_ids: Any) -> str:
    stub = sanitize_worker_task_update_stub(update_stub)
    ids = _uniq(proposal_ids, limit=8, text_limit=32)
    if not stub and not ids:
        return "-"
    target_text = ",".join(list(stub.get("target_artifacts") or [])[:2]) if stub else "-"
    parts = []
    status = _trim((stub or {}).get("status"), 48) or "-"
    if status != "-":
        parts.append(f"status={status}")
    if ids:
        parts.append(f"apply_proposals={len(ids)}")
        parts.append(f"ids={','.join(ids[:2])}")
    if target_text and target_text != "-":
        parts.append(f"targets={target_text}")
    return " | ".join(parts)[:320] if parts else "-"
