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
    summarize_subagent_contract,
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
        "document_count": len(records),
        "general_subagent_contract": general_subagent_contract,
        "general_subagent_summary": summarize_subagent_contract(general_subagent_contract),
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
