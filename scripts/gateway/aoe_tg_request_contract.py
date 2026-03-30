#!/usr/bin/env python3
"""Canonical request-contract extraction and persistence helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from aoe_tg_orch_roles import classify_dispatch_role_preset, normalize_role_preset
from aoe_tg_request_contract_data import (
    data_request_contract_matches,
    extract_data_request_contract,
)


REQUEST_CONTRACT_VERSION = "2026-03-30.v1"


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _dedupe_rows(rows: List[Any], *, limit: int = 8, text_limit: int = 160) -> List[str]:
    out: List[str] = []
    for item in rows:
        token = _trim(item, text_limit)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit))]


def _normalize_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _sanitize_contract_fields(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        token = _trim(key, 64)
        if not token:
            continue
        if isinstance(value, dict):
            child: Dict[str, Any] = {}
            for child_key, child_value in value.items():
                child_token = _trim(child_key, 64)
                if not child_token:
                    continue
                if isinstance(child_value, bool):
                    child[child_token] = bool(child_value)
                elif isinstance(child_value, list):
                    child[child_token] = _dedupe_rows(list(child_value), limit=12, text_limit=120)
                else:
                    child[child_token] = _trim(child_value, 240)
            if child:
                out[token] = child
        elif isinstance(value, list):
            out[token] = _dedupe_rows(list(value), limit=12, text_limit=120)
        elif isinstance(value, bool):
            out[token] = bool(value)
        else:
            text = _trim(value, 240)
            if text:
                out[token] = text
    return out


def _sanitize_artifact_contracts(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        alias = _trim(key, 64)
        if not alias or not isinstance(value, dict):
            continue
        row: Dict[str, Any] = {}
        path = _trim(value.get("path", ""), 200)
        if path:
            row["path"] = path
        fmt = _trim(value.get("format", ""), 32)
        if fmt:
            row["format"] = fmt
        required_fields = _dedupe_rows(list(value.get("required_fields") or []), limit=12, text_limit=120)
        if required_fields:
            row["required_fields"] = required_fields
        notes = _dedupe_rows(list(value.get("acceptance_notes") or []), limit=6, text_limit=240)
        if notes:
            row["acceptance_notes"] = notes
        if row:
            out[alias] = row
    return out


def normalize_request_contract_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    contract_type = normalize_role_preset(raw.get("contract_type") or raw.get("preset") or "general")
    status = _trim(raw.get("status", "complete"), 32).lower() or "complete"
    if status not in {"complete", "incomplete", "ambiguous"}:
        status = "complete"

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", REQUEST_CONTRACT_VERSION), 48) or REQUEST_CONTRACT_VERSION,
        "contract_type": contract_type,
        "preset": normalize_role_preset(raw.get("preset", contract_type) or contract_type),
        "status": status,
    }

    for key in ("objective", "project_key", "intent_action", "source_prompt", "summary", "approval_mode"):
        token = _trim(raw.get(key, ""), 400 if key == "source_prompt" else 240)
        if token:
            snapshot[key] = token
    if "readonly" in raw:
        snapshot["readonly"] = _normalize_bool(raw.get("readonly"), False)

    missing_fields = _dedupe_rows(list(raw.get("missing_fields") or []), limit=12, text_limit=120)
    ambiguity_notes = _dedupe_rows(list(raw.get("ambiguity_notes") or []), limit=8, text_limit=200)
    required_outputs = _dedupe_rows(list(raw.get("required_outputs") or []), limit=12, text_limit=200)
    required_evidence = _dedupe_rows(list(raw.get("required_evidence") or []), limit=12, text_limit=120)
    if missing_fields:
        snapshot["missing_fields"] = missing_fields
    if ambiguity_notes:
        snapshot["ambiguity_notes"] = ambiguity_notes
    if required_outputs:
        snapshot["required_outputs"] = required_outputs
    if required_evidence:
        snapshot["required_evidence"] = required_evidence

    fields = _sanitize_contract_fields(raw.get("fields"))
    if fields:
        snapshot["fields"] = fields

    artifact_contracts = _sanitize_artifact_contracts(raw.get("artifact_contracts"))
    if artifact_contracts:
        snapshot["artifact_contracts"] = artifact_contracts

    if not snapshot.get("summary"):
        parts = [snapshot.get("contract_type", "general"), snapshot.get("status", "complete")]
        if required_outputs:
            parts.append("outputs=" + ",".join(required_outputs[:4]))
        if missing_fields:
            parts.append("missing=" + ",".join(missing_fields[:4]))
        snapshot["summary"] = " | ".join(str(item).strip() for item in parts if str(item).strip())[:400]

    return snapshot


def _lineage_preset(run_control_mode: str, run_source_task: Optional[Dict[str, Any]]) -> str:
    if str(run_control_mode or "").strip().lower() not in {"retry", "replan", "followup"}:
        return ""
    if not isinstance(run_source_task, dict):
        return ""
    for key in ("request_contract_preset", "phase2_team_preset", "phase1_role_preset"):
        token = normalize_role_preset(run_source_task.get(key, ""))
        if token:
            return token
    return ""


def resolve_request_contract_preset(
    *,
    source_prompt: str,
    selected_roles: Optional[List[str]] = None,
    explicit_preset: str = "",
    run_control_mode: str = "",
    run_source_task: Optional[Dict[str, Any]] = None,
) -> str:
    explicit = normalize_role_preset(explicit_preset)
    if explicit and explicit != "general":
        return explicit

    lineage = _lineage_preset(run_control_mode, run_source_task)
    if lineage and lineage != "general":
        return lineage

    if data_request_contract_matches(source_prompt):
        return "data"

    inferred = normalize_role_preset(
        classify_dispatch_role_preset(source_prompt, selected_roles=list(selected_roles or []))
    )
    if inferred == "data" and not data_request_contract_matches(source_prompt):
        return "general"
    return inferred or "general"


def build_request_contract(
    *,
    source_prompt: str,
    selected_roles: Optional[List[str]] = None,
    explicit_preset: str = "",
    run_control_mode: str = "",
    run_source_task: Optional[Dict[str, Any]] = None,
    intent_action: str = "",
    project_key: str = "",
) -> Dict[str, Any]:
    resolved_preset = resolve_request_contract_preset(
        source_prompt=source_prompt,
        selected_roles=selected_roles,
        explicit_preset=explicit_preset,
        run_control_mode=run_control_mode,
        run_source_task=run_source_task,
    )
    if resolved_preset == "data":
        contract = extract_data_request_contract(source_prompt) or {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": "data",
            "preset": "data",
            "status": "incomplete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {},
            "required_outputs": [],
            "required_evidence": [],
            "missing_fields": ["source_path", "target_column", "accepted_input_formats", "normalize_to"],
            "ambiguity_notes": [],
            "summary": "data | incomplete",
            "artifact_contracts": {},
        }
    else:
        contract = {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": resolved_preset,
            "preset": resolved_preset,
            "status": "complete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {},
            "required_outputs": [],
            "required_evidence": [],
            "missing_fields": [],
            "ambiguity_notes": [],
            "summary": f"{resolved_preset or 'general'} | text-first",
            "artifact_contracts": {},
        }

    contract["intent_action"] = _trim(intent_action, 64)
    contract["project_key"] = _trim(project_key, 64)
    return normalize_request_contract_snapshot(contract)


def request_contract_is_blocking(contract: Dict[str, Any]) -> bool:
    snapshot = normalize_request_contract_snapshot(contract)
    return str(snapshot.get("status", "")).strip().lower() in {"incomplete", "ambiguous"}


def request_contract_block_reason(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    missing = list(snapshot.get("missing_fields") or [])
    notes = list(snapshot.get("ambiguity_notes") or [])
    if missing:
        return "missing required contract fields: " + ", ".join(missing[:6])
    if notes:
        return "contract ambiguity: " + "; ".join(str(item).strip() for item in notes[:4] if str(item).strip())
    status = _trim(snapshot.get("status", ""), 32) or "incomplete"
    return f"request contract is {status}"


def request_contract_summary(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    return _trim(snapshot.get("summary", ""), 400)


def request_contract_planning_appendix(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    if not snapshot:
        return ""

    lines = ["[Request Contract]"]
    lines.append(f"- type: {snapshot.get('contract_type', '-')}")
    lines.append(f"- status: {snapshot.get('status', '-')}")
    lines.append(f"- preset: {snapshot.get('preset', '-')}")
    summary = _trim(snapshot.get("summary", ""), 400)
    if summary:
        lines.append(f"- summary: {summary}")

    fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
    if fields:
        lines.append("- fields:")
        for key in sorted(fields.keys()):
            value = fields.get(key)
            if isinstance(value, dict):
                items = []
                for child_key in sorted(value.keys()):
                    child_value = value.get(child_key)
                    items.append(f"{child_key}={child_value}")
                lines.append(f"  - {key}: {', '.join(items)}")
            elif isinstance(value, list):
                lines.append(f"  - {key}: {', '.join(str(item).strip() for item in value if str(item).strip())}")
            else:
                lines.append(f"  - {key}: {value}")

    outputs = list(snapshot.get("required_outputs") or [])
    if outputs:
        lines.append("- required_outputs: " + ", ".join(outputs))

    artifact_contracts = snapshot.get("artifact_contracts") if isinstance(snapshot.get("artifact_contracts"), dict) else {}
    if artifact_contracts:
        lines.append("- artifact_contracts:")
        for key in sorted(artifact_contracts.keys()):
            row = artifact_contracts.get(key) if isinstance(artifact_contracts.get(key), dict) else {}
            path = _trim(row.get("path", key), 200) or key
            fmt = _trim(row.get("format", ""), 32) or "-"
            fields_list = list(row.get("required_fields") or [])
            notes = list(row.get("acceptance_notes") or [])
            lines.append(f"  - {key}: path={path} format={fmt}")
            if fields_list:
                lines.append("    required_fields: " + ", ".join(str(item).strip() for item in fields_list[:6] if str(item).strip()))
            if notes:
                lines.append("    notes: " + "; ".join(str(item).strip() for item in notes[:2] if str(item).strip()))

    missing = list(snapshot.get("missing_fields") or [])
    if missing:
        lines.append("- missing_fields: " + ", ".join(missing))
    notes = list(snapshot.get("ambiguity_notes") or [])
    if notes:
        lines.append("- ambiguity_notes: " + "; ".join(str(item).strip() for item in notes))
    return "\n".join(lines)


def request_contract_metadata(contract: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_request_contract_snapshot(contract)
    if not snapshot:
        return {}
    return deepcopy(
        {
            "request_contract_version": snapshot.get("version", REQUEST_CONTRACT_VERSION),
            "request_contract_type": snapshot.get("contract_type", ""),
            "request_contract_status": snapshot.get("status", ""),
            "request_contract_preset": snapshot.get("preset", ""),
            "request_contract_summary": snapshot.get("summary", ""),
            "request_contract_missing_fields": list(snapshot.get("missing_fields") or []),
            "request_contract_required_outputs": list(snapshot.get("required_outputs") or []),
            "request_contract_fields": dict(snapshot.get("fields") or {}),
            "request_contract_artifact_contracts": dict(snapshot.get("artifact_contracts") or {}),
        }
    )


def apply_request_contract_snapshot(target: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    metadata = request_contract_metadata(contract)
    for key, value in metadata.items():
        if value in ("", None, [], {}):
            target.pop(key, None)
            continue
        target[key] = deepcopy(value)
    return target
