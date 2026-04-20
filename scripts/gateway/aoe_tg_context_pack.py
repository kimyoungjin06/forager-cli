#!/usr/bin/env python3
"""Read-only context pack compiler for task-scoped knowledge selection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_document_registry import load_document_registry
from aoe_tg_runtime_core import context_pack_path
from aoe_tg_workspace_brief import load_workspace_brief


CONTEXT_PACK_COMPILER_VERSION = "v0"
DEFAULT_DOC_BUDGET = 4
SUPPORTED_PROFILES = {
    "on_desk_plan",
    "offdesk_execute",
    "review",
    "followup_preview",
    "followup_execute",
    "incident_recovery",
}
PROFILE_DOC_PRIORITY = {
    "on_desk_plan": ["spec", "adr", "reference", "research", "runbook", "ops", "note"],
    "offdesk_execute": ["runbook", "spec", "adr", "ops", "reference", "note"],
    "review": ["runbook", "spec", "adr", "incident", "ops", "reference", "note"],
    "followup_preview": ["runbook", "spec", "adr", "ops", "reference", "note"],
    "followup_execute": ["runbook", "spec", "adr", "ops", "reference", "note"],
    "incident_recovery": ["incident", "runbook", "ops", "spec", "adr", "reference", "note"],
}
FRESHNESS_RANK = {"fresh": 0, "review_soon": 1, "stale": 2}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _safe_token(raw: Any, default: str = "-") -> str:
    token = str(raw or "").strip()
    return token or default


def _pack_token(raw: Any, default: str = "runtime") -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw or "").strip()).strip("._-")
    return token or default


def _project_root_path(project_root: Any) -> Optional[Path]:
    raw = str(project_root or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _short_objective(raw: Any, fallback: str = "-") -> str:
    text = " ".join(str(raw or "").split()).strip()
    if not text:
        return fallback
    if len(text) <= 120:
        return text
    return text[:117].rstrip() + "..."


def _compact_path(path: Any, project_root: Optional[Path]) -> str:
    try:
        resolved = Path(str(path or "")).expanduser().resolve()
    except Exception:
        return str(path or "").strip() or "-"
    if project_root is not None:
        try:
            return str(resolved.relative_to(project_root))
        except Exception:
            pass
    return str(resolved)


def _derive_profile(task: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    item = task if isinstance(task, dict) else {}
    control_mode = str(item.get("control_mode", "")).strip().lower()
    followup_status = str(item.get("followup_brief_status", "")).strip().lower()
    execution_status = str(item.get("execution_brief_status", "")).strip().lower()
    preset = (
        str(item.get("phase2_team_preset", "")).strip().lower()
        or str(item.get("phase1_role_preset", "")).strip().lower()
    )
    task_status = str(item.get("status", "")).strip().lower()
    if followup_status == "preview_only":
        return "followup_preview", "followup_brief_preview_only"
    if control_mode == "followup" or followup_status in {"executable", "partially_executable"}:
        return "followup_execute", "followup_execute_lane_scope"
    if preset == "review":
        return "review", "review_preset"
    if task_status in {"blocked", "failed"} and "incident" in preset:
        return "incident_recovery", "incident_blocked_task"
    if control_mode in {"retry", "replan"} or execution_status in {"executable", "partially_executable"}:
        return "offdesk_execute", "execution_brief_ready"
    return "on_desk_plan", "task_runtime_default"


def _doc_priority(profile: str, doc_type: str) -> int:
    order = PROFILE_DOC_PRIORITY.get(profile) or PROFILE_DOC_PRIORITY["on_desk_plan"]
    try:
        return order.index(str(doc_type or "").strip().lower())
    except ValueError:
        return len(order) + 1


def _sorted_registry_records(profile: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _sort_key(record: Dict[str, Any]) -> Tuple[int, int, int, str]:
        return (
            _doc_priority(profile, record.get("doc_type")),
            0 if bool(record.get("canonical")) else 1,
            FRESHNESS_RANK.get(str(record.get("freshness_class", "")).strip().lower(), 9),
            str(record.get("path", "")).strip().lower(),
        )

    return sorted([row for row in records if isinstance(row, dict)], key=_sort_key)


def _select_relevant_docs(
    profile: str,
    registry: Dict[str, Any],
    *,
    project_root: Optional[Path],
    budget: int = DEFAULT_DOC_BUDGET,
) -> Tuple[List[Dict[str, str]], List[str]]:
    records = _sorted_registry_records(profile, list(registry.get("records") or []))
    canonical_records = [record for record in records if bool(record.get("canonical"))]
    noncanonical_records = [record for record in records if not bool(record.get("canonical"))]
    selected_records: List[Dict[str, Any]] = canonical_records[: max(1, int(budget))]
    fill_target = min(max(1, int(budget)), 2)
    if len(selected_records) < fill_target:
        selected_records.extend(noncanonical_records[: fill_target - len(selected_records)])
    relevant: List[Dict[str, str]] = []
    excluded: List[str] = []
    for record in selected_records:
        doc_type = str(record.get("doc_type", "")).strip() or "reference"
        canonical = bool(record.get("canonical"))
        freshness = str(record.get("freshness_class", "")).strip() or "unknown"
        path_text = _compact_path(record.get("path"), project_root)
        why = f"canonical_{doc_type}" if canonical else f"profile_match_{doc_type}"
        if freshness == "stale":
            why += "_stale"
        relevant.append(
            {
                "doc_id": _safe_token(record.get("doc_id"), path_text),
                "path": path_text,
                "why_included": why,
                "freshness_class": freshness,
            }
        )
    selected_ids = {str(record.get("doc_id", "")).strip() or str(record.get("path", "")).strip() for record in selected_records}
    remaining_records = [
        record
        for record in records
        if (str(record.get("doc_id", "")).strip() or str(record.get("path", "")).strip()) not in selected_ids
    ]
    for record in remaining_records[:3]:
        path_text = _compact_path(record.get("path"), project_root)
        reasons: List[str] = []
        if not bool(record.get("canonical")):
            reasons.append("noncanonical")
        if str(record.get("freshness_class", "")).strip().lower() == "stale":
            reasons.append("stale")
        reasons.append("bounded_pack")
        excluded.append(f"{path_text}: {'/'.join(reasons)}")
    remaining = max(0, len(remaining_records) - len(excluded))
    if remaining > 0:
        excluded.append(f"remaining_docs={remaining}")
    return relevant, excluded


def _pack_constraints(task: Optional[Dict[str, Any]], entry: Optional[Dict[str, Any]]) -> List[str]:
    item = task if isinstance(task, dict) else {}
    runtime = entry if isinstance(entry, dict) else {}
    constraints: List[str] = []
    execution_blocked = [
        str(value).strip()
        for value in (item.get("execution_brief_blocked_slice") or [])
        if str(value).strip()
    ]
    if execution_blocked:
        constraints.append("execution_blocked=" + ",".join(execution_blocked[:3]))
    followup_reason = str(item.get("followup_brief_reason", "")).strip()
    if followup_reason:
        constraints.append("followup_reason=" + _short_objective(followup_reason))
    run_lock_mode = str(runtime.get("run_lock_mode", "")).strip()
    if run_lock_mode:
        constraints.append(f"run_lock={run_lock_mode}")
    runner = str(runtime.get("background_runner_target", "")).strip()
    if runner:
        constraints.append(f"runner={runner}")
    return constraints[:4]


def _pack_known_failures(task: Optional[Dict[str, Any]]) -> List[str]:
    item = task if isinstance(task, dict) else {}
    failures: List[str] = []
    backend_note = str(item.get("backend_contract_note", "")).strip()
    if backend_note:
        failures.append(_short_objective(backend_note))
    background_external_note = str(item.get("background_run_external_note", "")).strip()
    if background_external_note:
        failures.append(_short_objective(background_external_note))
    return failures[:3]


def _runtime_context(task: Optional[Dict[str, Any]]) -> Dict[str, str]:
    item = task if isinstance(task, dict) else {}
    return {
        "execution_brief": _safe_token(item.get("execution_brief_summary")),
        "followup_brief": _safe_token(item.get("followup_brief_summary")),
        "reentry_rails": _safe_token(item.get("reentry_rails_summary")),
        "background_run": _safe_token(item.get("background_run_status")),
    }


def _pack_summary(pack: Dict[str, Any]) -> str:
    docs = pack.get("relevant_docs") or []
    excluded = pack.get("excluded_context") or []
    canonical_count = sum(1 for row in docs if "canonical_" in str(row.get("why_included", "")))
    return (
        f"profile={_safe_token(pack.get('profile'))} "
        f"docs={len(docs)} "
        f"canonical={canonical_count} "
        f"excluded={len(excluded)} "
        f"reason={_safe_token(pack.get('compile_reason'))}"
    )


def _pack_docs_summary(pack: Dict[str, Any]) -> str:
    docs = [_safe_token(row.get("path")) for row in (pack.get("relevant_docs") or []) if isinstance(row, dict)]
    return ", ".join(docs[:4]) or "-"


def _pack_excluded_summary(pack: Dict[str, Any]) -> str:
    values = [str(item).strip() for item in (pack.get("excluded_context") or []) if str(item).strip()]
    return " | ".join(values[:3]) or "-"


def sanitize_context_pack(
    raw: Optional[Dict[str, Any]],
    *,
    team_dir: Path,
    project_root: Optional[Path] = None,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    item = task if isinstance(task, dict) else {}
    runtime = entry if isinstance(entry, dict) else {}
    backend = artifact_backend(team_dir)
    profile, compile_reason = _derive_profile(item)
    request_id = _safe_token(source.get("request_id") or item.get("request_id"), "")
    task_id = _safe_token(source.get("task_id") or item.get("short_id"), "")
    workspace_key = _safe_token(
        source.get("workspace_key") or runtime.get("name") or runtime.get("project_alias") or "default"
    )
    relevant_docs = [row for row in (source.get("relevant_docs") or []) if isinstance(row, dict)]
    excluded = [str(item).strip() for item in (source.get("excluded_context") or []) if str(item).strip()]
    sanitized = {
        "pack_id": _safe_token(
            source.get("pack_id"),
            f"{_pack_token(request_id or task_id or workspace_key)}-{profile}",
        ),
        "workspace_key": workspace_key,
        "request_id": request_id,
        "task_id": task_id,
        "profile": _safe_token(source.get("profile"), profile),
        "compile_reason": _safe_token(source.get("compile_reason"), compile_reason),
        "objective": _short_objective(source.get("objective") or item.get("prompt") or item.get("alias") or task_id),
        "constraints": [str(value).strip() for value in (source.get("constraints") or []) if str(value).strip()],
        "relevant_docs": relevant_docs,
        "runtime_context": source.get("runtime_context") if isinstance(source.get("runtime_context"), dict) else {},
        "known_failures": [str(value).strip() for value in (source.get("known_failures") or []) if str(value).strip()],
        "unresolved_questions": [
            str(value).strip() for value in (source.get("unresolved_questions") or []) if str(value).strip()
        ],
        "excluded_context": excluded,
        "budget": source.get("budget") if isinstance(source.get("budget"), dict) else {
            "target_doc_count": DEFAULT_DOC_BUDGET,
            "target_token_envelope": 12000,
        },
        "compiled_at": _safe_token(source.get("compiled_at"), _now_iso()),
        "compiler_version": _safe_token(source.get("compiler_version"), CONTEXT_PACK_COMPILER_VERSION),
    }
    sanitized["summary"] = _pack_summary(sanitized)
    sanitized["docs_summary"] = _pack_docs_summary(sanitized)
    sanitized["excluded_summary"] = _pack_excluded_summary(sanitized)
    if request_id:
        sanitized["artifact_path"] = str(
            backend.context_pack_path(request_id=request_id, profile=sanitized["profile"])
        )
    elif task_id:
        sanitized["artifact_path"] = str(
            backend.context_pack_path(request_id=task_id, profile=sanitized["profile"])
        )
    else:
        sanitized["artifact_path"] = ""
    return sanitized


def build_context_pack(
    team_dir: Path | str,
    *,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    team_path = Path(team_dir).expanduser().resolve()
    project_path = _project_root_path(project_root) or _project_root_path((entry or {}).get("project_root"))
    workspace = load_workspace_brief(team_path, entry=entry, project_root=project_path)
    registry = load_document_registry(team_path, entry=entry, project_root=project_path)
    profile, compile_reason = _derive_profile(task)
    relevant_docs, excluded = _select_relevant_docs(profile, registry, project_root=project_path)
    unresolved_questions: List[str] = []
    execution_decision = str((task or {}).get("execution_brief_operator_decision", "")).strip()
    if execution_decision:
        unresolved_questions.append(_short_objective(execution_decision))
    if str((task or {}).get("followup_brief_status", "")).strip().lower() == "preview_only":
        unresolved_questions.append("followup execute remains operator-owned")
    pack = {
        "workspace_key": _safe_token(workspace.get("workspace_key"), _safe_token((entry or {}).get("name"), "default")),
        "request_id": _safe_token((task or {}).get("request_id"), ""),
        "task_id": _safe_token((task or {}).get("short_id"), ""),
        "profile": profile,
        "compile_reason": compile_reason,
        "objective": _short_objective((task or {}).get("prompt") or (task or {}).get("alias")),
        "constraints": _pack_constraints(task, entry),
        "relevant_docs": relevant_docs,
        "runtime_context": _runtime_context(task),
        "known_failures": _pack_known_failures(task),
        "unresolved_questions": unresolved_questions[:3],
        "excluded_context": excluded,
        "budget": {"target_doc_count": DEFAULT_DOC_BUDGET, "target_token_envelope": 12000},
        "compiled_at": _now_iso(),
        "compiler_version": CONTEXT_PACK_COMPILER_VERSION,
    }
    return sanitize_context_pack(pack, team_dir=team_path, project_root=project_path, entry=entry, task=task)


def load_context_pack(
    team_dir: Path | str,
    *,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    team_path = Path(team_dir).expanduser().resolve()
    backend = artifact_backend(team_path)
    project_path = _project_root_path(project_root) or _project_root_path((entry or {}).get("project_root"))
    profile, _compile_reason = _derive_profile(task)
    request_id = _safe_token((task or {}).get("request_id"), "")
    if request_id:
        artifact_path = backend.context_pack_path(request_id=request_id, profile=profile)
        if artifact_path.exists():
            raw = backend.load_context_pack(request_id=request_id, profile=profile)
            return sanitize_context_pack(raw, team_dir=team_path, project_root=project_path, entry=entry, task=task)
    return build_context_pack(team_path, entry=entry, task=task, project_root=project_path)


def persist_context_pack(
    team_dir: Path | str,
    *,
    pack: Optional[Dict[str, Any]] = None,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path | str] = None,
) -> Dict[str, Any]:
    team_path = Path(team_dir).expanduser().resolve()
    backend = artifact_backend(team_path)
    project_path = _project_root_path(project_root) or _project_root_path((entry or {}).get("project_root"))
    sanitized = sanitize_context_pack(
        pack if isinstance(pack, dict) else build_context_pack(team_path, entry=entry, task=task, project_root=project_path),
        team_dir=team_path,
        project_root=project_path,
        entry=entry,
        task=task,
    )
    request_id = _safe_token(sanitized.get("request_id"), "")
    profile = _safe_token(sanitized.get("profile"), "default")
    if request_id:
        backend.write_context_pack(request_id=request_id, profile=profile, payload=sanitized)
        sanitized["artifact_path"] = str(backend.context_pack_path(request_id=request_id, profile=profile))
    return sanitized


def summarize_context_pack(
    team_dir: Path | str,
    *,
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path | str] = None,
) -> str:
    return str(
        load_context_pack(team_dir, entry=entry, task=task, project_root=project_root).get("summary", "")
    ).strip() or "-"
