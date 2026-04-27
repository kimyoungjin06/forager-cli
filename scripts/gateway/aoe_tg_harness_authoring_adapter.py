#!/usr/bin/env python3
"""Read-only adapter seam for upstream harness authoring modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_context_pack import load_context_pack
from aoe_tg_document_registry import load_document_registry
from aoe_tg_subagent_contract import (
    build_general_research_subagent_contract,
    load_subagent_result_artifact,
    persist_subagent_result_artifact,
    summarize_subagent_contract,
    summarize_subagent_gate_compact,
    summarize_subagent_result_artifact,
)
from aoe_tg_workspace_brief import load_workspace_brief


REVFACTORY_HARNESS_REPO = "https://github.com/revfactory/harness"
REVFACTORY_HARNESS_PATTERNS = (
    "pipeline",
    "fan_out_fan_in",
    "expert_pool",
    "producer_reviewer",
    "supervisor",
    "hierarchical_delegation",
)


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _dedupe_rows(rows: List[str], *, limit: int = 8, item_limit: int = 240) -> List[str]:
    out: List[str] = []
    for item in rows:
        token = _trim(item, item_limit)
        if token and token not in out:
            out.append(token)
        if len(out) >= limit:
            break
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_vendor_harness_root() -> Path:
    return _repo_root() / "vendor" / "revfactory-harness"


def _project_root(entry: Any, workspace: Any) -> Path | None:
    for candidate in (
        _trim((entry or {}).get("project_root"), 400) if isinstance(entry, dict) else "",
        _trim((workspace or {}).get("project_root"), 400) if isinstance(workspace, dict) else "",
    ):
        if not candidate:
            continue
        try:
            return Path(candidate).expanduser().resolve()
        except Exception:
            continue
    return None


def inspect_vendor_harness(vendor_root: Any = "") -> Dict[str, Any]:
    root = Path(vendor_root).expanduser().resolve() if _trim(vendor_root, 400) else default_vendor_harness_root()
    readme = root / "README.md"
    skill = root / "skills" / "harness" / "SKILL.md"
    available = root.exists() and readme.exists() and skill.exists()
    return {
        "vendor_root": str(root),
        "available": bool(available),
        "repo_url": REVFACTORY_HARNESS_REPO,
        "readme_path": str(readme),
        "skill_path": str(skill),
        "patterns": list(REVFACTORY_HARNESS_PATTERNS),
        "summary": (
            f"vendor=ready root={root.name} patterns={len(REVFACTORY_HARNESS_PATTERNS)}"
            if available
            else f"vendor=missing root={root}"
        ),
    }


def build_harness_authoring_plan(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    vendor_root: Any = "",
) -> Dict[str, Any]:
    workspace = load_workspace_brief(
        team_dir,
        entry=entry,
        project_root=(entry or {}).get("project_root") if isinstance(entry, dict) else "",
    )
    registry = load_document_registry(
        team_dir,
        entry=entry,
        project_root=(entry or {}).get("project_root") if isinstance(entry, dict) else "",
    )
    pack = load_context_pack(
        team_dir,
        entry=entry,
        task=task,
        project_root=(entry or {}).get("project_root") if isinstance(entry, dict) else "",
    )
    vendor = inspect_vendor_harness(vendor_root)
    project_root = _project_root(entry, workspace)
    backend = artifact_backend(team_dir).descriptor()
    claude_root = (project_root / ".claude") if project_root is not None else Path(team_dir).expanduser().resolve() / ".claude"
    agents_dir = claude_root / "agents"
    skills_dir = claude_root / "skills"
    records = registry.get("records") if isinstance(registry.get("records"), list) else []
    relevant_docs = pack.get("relevant_docs") if isinstance(pack.get("relevant_docs"), list) else []
    general_subagent_contract = build_general_research_subagent_contract(
        request_id=(task or {}).get("request_id") if isinstance(task, dict) else "",
        task_ref=(task or {}).get("short_id") if isinstance(task, dict) else "",
        objective=(
            "Collect bounded upstream harness references, relevant local docs, and reusable operator evidence "
            "for harness authoring without owning dispatch/apply decisions."
        ),
        backend_descriptor=backend,
        relevant_doc_ids=[
            _trim(item.get("doc_id"), 128)
            for item in relevant_docs
            if isinstance(item, dict) and _trim(item.get("doc_id"), 128)
        ],
        relevant_doc_paths=[
            _trim(item.get("path"), 240)
            for item in relevant_docs
            if isinstance(item, dict) and _trim(item.get("path"), 240)
        ],
        context_pack_profile=_trim(pack.get("profile"), 64),
        context_pack_summary=_trim(pack.get("summary"), 320),
        vendor_patterns=list(REVFACTORY_HARNESS_PATTERNS),
    )
    general_subagent_artifact = load_subagent_result_artifact(team_dir, contract=general_subagent_contract)
    return {
        "adapter_kind": "upstream_harness_authoring",
        "repo_url": REVFACTORY_HARNESS_REPO,
        "vendor": vendor,
        "artifact_backend": backend,
        "workspace_key": _trim(workspace.get("workspace_key"), 128) or "default",
        "project_alias": _trim(workspace.get("project_alias"), 32) or "O1",
        "project_root": str(project_root) if project_root is not None else "",
        "team_dir": str(Path(team_dir).expanduser().resolve()),
        "context_pack_profile": _trim(pack.get("profile"), 64) or "on_desk_plan",
        "context_pack_summary": _trim(pack.get("summary"), 320) or "-",
        "document_registry_summary": _trim(registry.get("summary"), 320) or "-",
        "selected_doc_ids": [
            _trim(item.get("doc_id"), 128)
            for item in relevant_docs
            if isinstance(item, dict) and _trim(item.get("doc_id"), 128)
        ][:6],
        "selected_doc_paths": [
            _trim(item.get("path"), 240)
            for item in relevant_docs
            if isinstance(item, dict) and _trim(item.get("path"), 240)
        ][:6],
        "document_count": len(records),
        "general_subagent_contract": general_subagent_contract,
        "general_subagent_summary": summarize_subagent_contract(general_subagent_contract),
        "general_subagent_artifact": general_subagent_artifact,
        "general_subagent_artifact_summary": summarize_subagent_result_artifact(general_subagent_artifact),
        "authoring_targets": {
            "claude_root": str(claude_root),
            "agents_dir": str(agents_dir),
            "skills_dir": str(skills_dir),
        },
        "patterns": list(REVFACTORY_HARNESS_PATTERNS),
        "summary": "vendor={vendor} pack={pack} docs={docs} selected={selected} outputs={agents},{skills}".format(
            vendor="ready" if vendor.get("available") else "missing",
            pack=_trim(pack.get("profile"), 64) or "-",
            docs=len(records),
            selected=len(relevant_docs),
            agents=agents_dir,
            skills=skills_dir,
        ),
    }


def summarize_harness_authoring_plan(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    vendor_root: Any = "",
) -> str:
    plan = build_harness_authoring_plan(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    return _trim(plan.get("summary"), 400) or "-"


def summarize_general_subagent_surface(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    vendor_root: Any = "",
) -> Dict[str, str]:
    if not isinstance(task, dict) or not task:
        return {
            "summary": "-",
            "artifact_summary": "-",
            "artifact_path": "-",
        }
    plan = build_harness_authoring_plan(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    artifact = plan.get("general_subagent_artifact") if isinstance(plan.get("general_subagent_artifact"), dict) else {}
    return {
        "summary": _trim(plan.get("general_subagent_summary"), 320) or "-",
        "artifact_summary": _trim(plan.get("general_subagent_artifact_summary"), 320) or "-",
        "artifact_path": _trim(artifact.get("artifact_path"), 240) or "-",
        "gate_summary": _trim(artifact.get("gate_summary"), 240) or summarize_subagent_gate_compact(artifact),
    }


def ensure_general_subagent_support_surface(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    vendor_root: Any = "",
) -> Dict[str, Any]:
    surface = summarize_general_subagent_surface(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    executed = False
    if (
        isinstance(task, dict)
        and task
        and str(surface.get("artifact_summary", "")).strip() in {"", "-"}
    ):
        artifact = run_general_subagent_support(team_dir, entry=entry, task=task, vendor_root=vendor_root)
        if artifact:
            executed = True
            surface = summarize_general_subagent_surface(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    result = dict(surface)
    result["executed"] = executed
    return result


def run_general_subagent_support(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    vendor_root: Any = "",
) -> Dict[str, Any]:
    if not isinstance(task, dict) or not task:
        return {}
    plan = build_harness_authoring_plan(team_dir, entry=entry, task=task, vendor_root=vendor_root)
    contract = plan.get("general_subagent_contract") if isinstance(plan.get("general_subagent_contract"), dict) else {}
    if not contract:
        return {}
    backend = artifact_backend(team_dir)
    project_alias = _trim(plan.get("project_alias"), 32) or "-"
    context_profile = _trim(plan.get("context_pack_profile"), 64) or "on_desk_plan"
    request_id = _trim(task.get("request_id"), 128)
    task_ref = _trim(task.get("short_id"), 64) or _trim(task.get("alias"), 64)
    selected_doc_ids = _dedupe_rows([str(item) for item in (plan.get("selected_doc_ids") or [])], limit=6, item_limit=128)
    selected_doc_paths = _dedupe_rows([str(item) for item in (plan.get("selected_doc_paths") or [])], limit=6, item_limit=240)
    vendor = plan.get("vendor") if isinstance(plan.get("vendor"), dict) else {}
    vendor_available = bool(vendor.get("available"))
    vendor_sources = _dedupe_rows(
        [
            _trim(vendor.get("readme_path"), 240),
            _trim(vendor.get("skill_path"), 240),
        ],
        limit=2,
        item_limit=240,
    )
    sources = _dedupe_rows([*selected_doc_paths, *vendor_sources], limit=8, item_limit=240)
    key_findings = _dedupe_rows(
        [
            f"context_pack={context_profile} | docs={len(selected_doc_paths)} | doc_ids={','.join(selected_doc_ids[:3]) or '-'}",
            f"vendor={'ready' if vendor_available else 'missing'} | patterns={len(list(plan.get('patterns') or []))}",
            f"project={project_alias} | backend={_trim(((plan.get('artifact_backend') or {}) if isinstance(plan.get('artifact_backend'), dict) else {}).get('backend_kind'), 64) or 'filesystem'}",
            _trim(plan.get("document_registry_summary"), 240),
        ],
        limit=6,
        item_limit=240,
    )
    blocking_issues = _dedupe_rows(
        [
            (
                f"vendor_harness_missing:{_trim(vendor.get('vendor_root'), 200)}"
                if not vendor_available
                else ""
            ),
            "no_selected_docs_in_context_pack" if not selected_doc_paths else "",
            "document_registry_empty" if "indexed=0" in _trim(plan.get("document_registry_summary"), 240) else "",
        ],
        limit=6,
        item_limit=240,
    )
    artifact_refs = _dedupe_rows(
        [
            backend.relative_artifact_path(
                backend.harness_authoring_plan_path(request_id=request_id, task_ref=task_ref)
            ),
            backend.relative_artifact_path(
                backend.context_pack_path(request_id=request_id or task_ref or "runtime", profile=context_profile)
            ),
        ],
        limit=8,
        item_limit=240,
    )
    raw_result = {
        "subagent_kind": "general_research",
        "summary": (
            f"bounded evidence ready | vendor={'ready' if vendor_available else 'missing'} | "
            f"docs={len(selected_doc_paths)} | findings={len(key_findings)}"
        ),
        "confidence": "medium" if blocking_issues else "high",
        "sources": sources,
        "key_findings": key_findings,
        "blocking_issues": blocking_issues,
        "recommended_next_step": f"/task {task_ref}" if task_ref else (f"/task {request_id}" if request_id else "/control/offdesk"),
        "artifact_refs": artifact_refs,
    }
    payload = persist_subagent_result_artifact(team_dir, contract=contract, raw_result=raw_result)
    if payload:
        payload["contract_summary"] = summarize_subagent_contract(contract)
    return payload
