"""Shared producer helpers for Forager Offdesk decision records."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
from typing import Any


DECISION_RECORD_SCHEMA = "decision_record.v1"
DECISION_LEDGER_FILE = "offdesk_decisions.jsonl"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def clean_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    result: list[str] = []
    for item in items:
        if isinstance(item, (dict, list)):
            text = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        if isinstance(item, (dict, list)):
            item_text = json.dumps(item, ensure_ascii=False, sort_keys=True)
        elif item is None:
            item_text = ""
        else:
            item_text = str(item)
        if item_text:
            result[key_text] = item_text
    return result


def trace_ref(kind: str, label: str, reference: Any) -> dict[str, str] | None:
    text = str(reference or "").strip()
    if not text:
        return None
    return {"kind": kind, "label": label, "reference": text}


def stable_decision_id(*parts: Any, prefix: str = "decision") -> str:
    raw = "\n".join(str(part or "") for part in parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def approval_brief_projection(brief: dict[str, Any]) -> dict[str, Any]:
    reply_examples = brief.get("reply_examples")
    if isinstance(reply_examples, dict):
        reply_example_lines = [
            f"{key}: {value}"
            for key, value in string_map(reply_examples).items()
            if str(value).strip()
        ]
    else:
        reply_example_lines = clean_list(reply_examples)

    options: list[dict[str, Any]] = []
    raw_options = brief.get("options")
    if isinstance(raw_options, list):
        for option in raw_options:
            if not isinstance(option, dict):
                continue
            prompt = str(option.get("natural_input_prompt") or "").strip()
            option_row = {
                "id": str(option.get("id") or "").strip(),
                "label": str(option.get("label") or "").strip(),
                "description": str(option.get("description") or "").strip(),
            }
            if prompt:
                option_row["natural_input_prompt"] = prompt
            options.append(option_row)

    projection = {
        "schema": str(brief.get("schema") or "approval_brief.v1"),
        "recommendation": str(brief.get("recommendation") or "").strip(),
        "subject": str(brief.get("subject") or "").strip(),
        "summary_lines": clean_list(brief.get("summary_lines")),
        "scope": str(brief.get("scope") or "").strip(),
        "question": str(brief.get("question") or "").strip(),
        "options": options,
        "why_recommendation": clean_list(brief.get("why_recommendation")),
        "evidence": clean_list(brief.get("evidence") or brief.get("key_evidence")),
        "decision_impacts": string_map(brief.get("decision_impacts")),
        "reply_examples": reply_example_lines,
        "context": string_map(brief.get("context")),
    }
    source = str(brief.get("source") or "").strip()
    if source:
        projection["source"] = source
    return projection


def drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [drop_none(item) for item in value]
    return value


def build_decision_record(
    *,
    decision_id: str,
    project_key: str,
    request_id: str,
    task_id: str,
    raised_by: str,
    source_surface: str,
    materiality: str,
    status: str,
    decision_kind: str,
    summary: str,
    decision_needed: str,
    current_scope: str,
    non_authorized_scope: list[str],
    approval_brief: dict[str, Any] | None = None,
    why_now: list[str] | None = None,
    options: list[dict[str, Any]] | None = None,
    evidence_refs: list[dict[str, str]] | None = None,
    request_trace_refs: list[dict[str, str]] | None = None,
    council_review: dict[str, Any] | None = None,
    route_target: str | None = None,
    route_reason: str | None = None,
    route_policy_basis: list[str] | None = None,
    default_if_no_reply: str | None = None,
    trace_refs: list[dict[str, str]] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    now = created_at or utc_now()
    route = None
    if route_target:
        route = {
            "materiality": materiality,
            "target": route_target,
            "reason": route_reason or "",
            "policy_basis": route_policy_basis or [],
            "default_if_no_reply": default_if_no_reply,
        }
    record = {
        "schema": DECISION_RECORD_SCHEMA,
        "decision_id": decision_id,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "raised_by": raised_by,
        "source_surface": source_surface,
        "materiality": materiality,
        "status": status,
        "created_at": now,
        "updated_at": updated_at or now,
        "decision_request": {
            "kind": decision_kind,
            "summary": summary,
            "decision_needed": decision_needed,
            "why_now": why_now or [],
            "current_scope": current_scope,
            "non_authorized_scope": non_authorized_scope,
            "options": options or [],
            "evidence_refs": evidence_refs or [],
            "trace_refs": request_trace_refs or [],
        },
        "council_review": council_review,
        "route": route,
        "approval_brief": approval_brief_projection(approval_brief) if approval_brief else None,
        "trace_refs": trace_refs or [],
    }
    return drop_none(record)


def append_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_decision_record_artifacts(
    *,
    out_dir: pathlib.Path,
    relay_dir: pathlib.Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    record = request.get("decision_record")
    if not isinstance(record, dict):
        return {"written": False, "reason": "decision_record_missing"}
    record_path = relay_dir / "decision_record.json"
    ledger_path = out_dir / DECISION_LEDGER_FILE
    relay_dir.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    append_jsonl(ledger_path, record)
    return {
        "written": True,
        "decision_id": record.get("decision_id"),
        "record_path": str(record_path),
        "ledger_path": str(ledger_path),
        "schema": record.get("schema"),
    }
