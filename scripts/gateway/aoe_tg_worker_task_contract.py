#!/usr/bin/env python3
"""Task-scoped worker contract compiler for bounded background model invokes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from aoe_tg_context_pack import load_context_pack


WORKER_TASK_CONTRACT_VERSION = "2026-04-10.v1"
WORKER_TASK_RESULT_VERSION = "2026-04-11.v1"
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
