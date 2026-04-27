#!/usr/bin/env python3
"""Document registry scanner and summary helpers."""

from __future__ import annotations

import fnmatch
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_runtime_core import document_registry_path
from aoe_tg_workspace_brief import load_workspace_brief

_DOC_SUFFIXES = {".md", ".pdf", ".docx"}
_DOC_TYPES = {"spec", "runbook", "adr", "ops", "research", "incident", "reference", "note"}
_SOURCE_KINDS = {"markdown", "pdf", "docx", "external", "other"}


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _normalize_string_list(raw: Any, *, limit: int = 16, item_limit: int = 240) -> List[str]:
    source = raw if isinstance(raw, list) else []
    out: List[str] = []
    seen: set[str] = set()
    for item in source:
        token = _trim(item, item_limit)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _normalize_doc_type(raw: Any) -> str:
    token = _trim(raw, 64).lower()
    return token if token in _DOC_TYPES else "note"


def _normalize_source_kind(raw: Any) -> str:
    token = _trim(raw, 32).lower()
    return token if token in _SOURCE_KINDS else "other"


def _freshness_class(path: Path) -> str:
    try:
        age_sec = max(0.0, time.time() - float(path.stat().st_mtime))
    except Exception:
        return "stale"
    age_days = age_sec / 86400.0
    if age_days <= 30:
        return "fresh"
    if age_days <= 90:
        return "review_soon"
    return "stale"


def _source_kind_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "markdown"
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    return "other"


def _classify_doc_type(path: Path) -> str:
    token = path.as_posix().lower()
    name = path.name.lower()
    stem = path.stem.lower()
    if name == "runbook.md" or "/runbook" in token:
        return "runbook"
    if name.startswith("adr-") or "/adr/" in token or "/adrs/" in token:
        return "adr"
    if stem.endswith("_spec") or name.endswith("_spec.md") or "/spec" in token:
        return "spec"
    if "incident" in token or "postmortem" in token:
        return "incident"
    if "benchmark" in token or "research" in token:
        return "research"
    if "reference" in token or "refs" in token:
        return "reference"
    if "runbook" in token or "ops" in token or "operating" in token:
        return "ops"
    return "note"


def _summary_title(path: Path) -> str:
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    return stem or path.name


def _path_matches_ignore(path: Path, *, project_root: Path, doc_root: Path, ignore_globs: Iterable[str]) -> bool:
    try:
        rel_project = path.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        rel_project = path.name
    try:
        rel_doc = path.resolve().relative_to(doc_root.resolve()).as_posix()
    except Exception:
        rel_doc = path.name
    for pattern in ignore_globs:
        token = _trim(pattern, 240)
        if not token:
            continue
        if fnmatch.fnmatch(rel_project, token) or fnmatch.fnmatch(rel_doc, token):
            return True
    return False


def _doc_id(workspace_key: str, project_root: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        rel = path.name
    digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:10]
    return f"{_trim(workspace_key, 64) or 'workspace'}-{digest}"


def _iter_doc_files(doc_root: Path) -> Iterable[Path]:
    for candidate in doc_root.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in _DOC_SUFFIXES:
            continue
        yield candidate.resolve()


def _scan_document_records(workspace: Dict[str, Any]) -> List[Dict[str, Any]]:
    project_root = Path(str(workspace.get("project_root", "") or ".")).expanduser().resolve()
    runbooks = {
        str(Path(item).expanduser().resolve())
        for item in (workspace.get("canonical_runbook_paths") or [])
        if str(item).strip()
    }
    ignore_globs = list(workspace.get("doc_ignore_globs") or [])
    workspace_key = _trim(workspace.get("workspace_key"), 128) or "workspace"
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for root_raw in (workspace.get("doc_roots") or []):
        root_token = _trim(root_raw, 400)
        if not root_token:
            continue
        doc_root = Path(root_token).expanduser().resolve()
        if not doc_root.exists() or not doc_root.is_dir():
            continue
        for path in _iter_doc_files(doc_root):
            if _path_matches_ignore(path, project_root=project_root, doc_root=doc_root, ignore_globs=ignore_globs):
                continue
            path_key = str(path)
            if path_key in seen:
                continue
            seen.add(path_key)
            doc_type = _classify_doc_type(path)
            source_kind = _source_kind_for_path(path)
            canonical = path_key in runbooks or doc_type in {"spec", "runbook", "adr"}
            freshness = _freshness_class(path)
            out.append(
                {
                    "doc_id": _doc_id(workspace_key, project_root, path),
                    "workspace_key": workspace_key,
                    "path": path_key,
                    "doc_type": doc_type,
                    "source_kind": source_kind,
                    "title": _summary_title(path),
                    "owner": "",
                    "tags": [],
                    "keywords": [],
                    "summary_card": f"{doc_type} | {freshness} | {path.name}",
                    "canonical": canonical,
                    "freshness_class": freshness,
                    "updated_at": str(int(path.stat().st_mtime)),
                    "depends_on": [],
                    "supersedes": [],
                    "related_runtime_surfaces": [],
                    "ingest_status": "indexed",
                }
            )
    out.sort(key=lambda row: (str(row.get("doc_type", "")), str(row.get("path", ""))))
    return out


def summarize_document_registry_payload(payload: Dict[str, Any]) -> str:
    rows = payload.get("records") if isinstance(payload.get("records"), list) else []
    indexed = 0
    canonical = 0
    stale = 0
    kinds: Dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("ingest_status", "")).strip() != "indexed":
            continue
        indexed += 1
        if bool(row.get("canonical", False)):
            canonical += 1
        if str(row.get("freshness_class", "")).strip() == "stale":
            stale += 1
        kind = _normalize_doc_type(row.get("doc_type"))
        kinds[kind] = kinds.get(kind, 0) + 1
    kind_summary = ", ".join(f"{key}={kinds[key]}" for key in sorted(kinds)) if kinds else "-"
    return f"indexed={indexed} canonical={canonical} stale={stale} kinds={kind_summary}"


def sanitize_document_registry(
    raw: Any,
    *,
    team_dir: Any,
    project_root: Any = "",
    entry: Any = None,
    artifact_path: str = "",
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    workspace = load_workspace_brief(team_dir, entry=entry, project_root=project_root)
    source_rows = data.get("records") if isinstance(data.get("records"), list) else []
    rows: List[Dict[str, Any]] = []
    if source_rows:
        for item in source_rows:
            row = item if isinstance(item, dict) else {}
            path_token = _trim(row.get("path"), 400)
            if not path_token:
                continue
            resolved = Path(path_token).expanduser().resolve()
            doc_type = _normalize_doc_type(row.get("doc_type") or _classify_doc_type(resolved))
            source_kind = _normalize_source_kind(row.get("source_kind") or _source_kind_for_path(resolved))
            rows.append(
                {
                    "doc_id": _trim(row.get("doc_id"), 160) or _doc_id(
                        _trim(workspace.get("workspace_key"), 128),
                        Path(str(workspace.get("project_root", "") or ".")).expanduser().resolve(),
                        resolved,
                    ),
                    "workspace_key": _trim(row.get("workspace_key"), 128) or _trim(workspace.get("workspace_key"), 128),
                    "path": str(resolved),
                    "doc_type": doc_type,
                    "source_kind": source_kind,
                    "title": _trim(row.get("title"), 240) or _summary_title(resolved),
                    "owner": _trim(row.get("owner"), 120),
                    "tags": _normalize_string_list(row.get("tags"), limit=12, item_limit=64),
                    "keywords": _normalize_string_list(row.get("keywords"), limit=12, item_limit=64),
                    "summary_card": _trim(row.get("summary_card"), 240) or f"{doc_type} | {resolved.name}",
                    "canonical": bool(row.get("canonical", False)),
                    "freshness_class": _trim(row.get("freshness_class"), 32) or _freshness_class(resolved),
                    "updated_at": _trim(row.get("updated_at"), 64),
                    "depends_on": _normalize_string_list(row.get("depends_on"), limit=12, item_limit=160),
                    "supersedes": _normalize_string_list(row.get("supersedes"), limit=12, item_limit=160),
                    "related_runtime_surfaces": _normalize_string_list(
                        row.get("related_runtime_surfaces"), limit=12, item_limit=64
                    ),
                    "ingest_status": _trim(row.get("ingest_status"), 32) or "indexed",
                }
            )
    else:
        rows = _scan_document_records(workspace)
    normalized = {
        "version": max(1, int(data.get("version", 1) or 1)),
        "artifact_path": _trim(artifact_path, 400) or str(document_registry_path(team_dir)),
        "workspace_key": _trim(data.get("workspace_key"), 128) or _trim(workspace.get("workspace_key"), 128) or "workspace",
        "records": rows,
        "summary": "",
    }
    normalized["summary"] = summarize_document_registry_payload(normalized)
    return normalized


def build_document_registry(*, team_dir: Any, project_root: Any = "", entry: Any = None) -> Dict[str, Any]:
    return sanitize_document_registry(
        {},
        team_dir=team_dir,
        project_root=project_root,
        entry=entry,
        artifact_path=str(document_registry_path(team_dir)),
    )


def load_document_registry(team_dir: Any, *, project_root: Any = "", entry: Any = None) -> Dict[str, Any]:
    backend = artifact_backend(team_dir)
    path = backend.document_registry_path()
    payload = backend.load_document_registry()
    if not payload and not path.exists():
        return build_document_registry(team_dir=team_dir, project_root=project_root, entry=entry)
    return sanitize_document_registry(
        payload,
        team_dir=team_dir,
        project_root=project_root,
        entry=entry,
        artifact_path=str(path),
    )


def write_document_registry(team_dir: Any, payload: Any, *, project_root: Any = "", entry: Any = None) -> Dict[str, Any]:
    path = artifact_backend(team_dir).document_registry_path()
    normalized = sanitize_document_registry(
        payload,
        team_dir=team_dir,
        project_root=project_root,
        entry=entry,
        artifact_path=str(path),
    )
    artifact_backend(team_dir).write_document_registry(normalized)
    return normalized


def summarize_document_registry(team_dir: Any, *, project_root: Any = "", entry: Any = None) -> str:
    registry = load_document_registry(team_dir, project_root=project_root, entry=entry)
    return str(registry.get("summary", "")).strip() or summarize_document_registry_payload(registry)
