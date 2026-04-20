#!/usr/bin/env python3
"""Workspace onboarding artifact helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_runtime_core import model_endpoint_registry_path, model_routing_policy_path, workspace_brief_path

_WORKSPACE_STATUSES = {"draft", "validated", "active", "stale"}
_DOC_CANDIDATE_DIRS = ("docs", "notes", "knowledge", "design")
_RUNBOOK_CANDIDATES = ("docs/RUNBOOK.md", "RUNBOOK.md", "docs/runbook.md", "runbook.md")
_DEFAULT_DOC_IGNORE_GLOBS = (
    ".git/**",
    ".venv/**",
    "node_modules/**",
    ".aoe-team/**",
    "dist/**",
    "build/**",
    "coverage/**",
    ".pytest_cache/**",
)


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _normalize_status(raw: Any, default: str = "draft") -> str:
    token = _trim(raw, 32).lower()
    if token in _WORKSPACE_STATUSES:
        return token
    return default if default in _WORKSPACE_STATUSES else "draft"


def _normalize_path_list(raw: Any, *, limit: int = 12) -> List[str]:
    source = raw if isinstance(raw, list) else []
    out: List[str] = []
    seen: set[str] = set()
    for item in source:
        token = _trim(item, 400)
        if not token:
            continue
        try:
            normalized = str(Path(token).expanduser().resolve())
        except Exception:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


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


def _relative_label(path: str, project_root: Path) -> str:
    token = _trim(path, 400)
    if not token:
        return "-"
    try:
        rel = Path(token).resolve().relative_to(project_root.resolve())
        return rel.as_posix()
    except Exception:
        return Path(token).name or token


def _discover_doc_roots(project_root: Path) -> List[str]:
    rows: List[str] = []
    for name in _DOC_CANDIDATE_DIRS:
        path = (project_root / name).resolve()
        if path.exists() and path.is_dir():
            rows.append(str(path))
    return rows


def _discover_canonical_todo_path(project_root: Path, team_dir: Path) -> str:
    candidates = (
        team_dir / "AOE_TODO.md",
        project_root / "TODO.md",
        project_root / "todo.md",
    )
    for path in candidates:
        if path.exists() and path.is_file():
            return str(path.resolve())
    return ""


def _discover_runbook_paths(project_root: Path) -> List[str]:
    rows: List[str] = []
    for rel in _RUNBOOK_CANDIDATES:
        path = (project_root / rel).resolve()
        if path.exists() and path.is_file():
            rows.append(str(path))
    return rows


def _derive_state_root(team_dir: Path) -> str:
    env_root = _trim(os.environ.get("AOE_STATE_DIR"), 400)
    if env_root:
        try:
            return str(Path(env_root).expanduser().resolve())
        except Exception:
            return env_root
    return str(team_dir.parent.resolve())


def _derive_onboarding_status(project_root: Path, team_dir: Path, notes: Iterable[str], explicit: str = "") -> str:
    if explicit:
        return _normalize_status(explicit, "validated")
    if not project_root.exists() or not team_dir.exists():
        return "stale"
    if any(note in {"project_root_missing", "team_dir_missing"} for note in notes):
        return "stale"
    return "validated"


def summarize_workspace_brief_payload(payload: Dict[str, Any]) -> str:
    project_root = Path(str(payload.get("project_root", "") or ".")).expanduser().resolve()
    doc_roots = [str(item).strip() for item in (payload.get("doc_roots") or []) if str(item).strip()]
    todo_path = _trim(payload.get("canonical_todo_path"), 400)
    docs_label = "-"
    if doc_roots:
        labels = [_relative_label(item, project_root) for item in doc_roots[:2]]
        docs_label = ",".join(labels)
        if len(doc_roots) > 2:
            docs_label += f"(+{len(doc_roots) - 2})"
    return "status={status} docs={docs} todo={todo} routing={routing} runner={runner}".format(
        status=_trim(payload.get("onboarding_status"), 32) or "draft",
        docs=docs_label,
        todo=_relative_label(todo_path, project_root) if todo_path else "-",
        routing=_trim(payload.get("model_routing_profile"), 64) or "default",
        runner=_trim(payload.get("background_runner_target"), 64) or "local_background",
    )


def sanitize_workspace_brief(
    raw: Any,
    *,
    project_root: Any = "",
    team_dir: Any = "",
    entry: Any = None,
    artifact_path: str = "",
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    entry_data = entry if isinstance(entry, dict) else {}
    resolved_project_root = Path(
        _trim(project_root, 400)
        or _trim(data.get("project_root"), 400)
        or _trim(entry_data.get("project_root"), 400)
        or "."
    ).expanduser().resolve()
    resolved_team_dir = Path(
        _trim(team_dir, 400)
        or _trim(data.get("team_dir"), 400)
        or _trim(entry_data.get("team_dir"), 400)
        or resolved_project_root / ".aoe-team"
    ).expanduser().resolve()
    notes = _normalize_string_list(data.get("validation_notes"), limit=12)
    if not resolved_project_root.exists() and "project_root_missing" not in notes:
        notes.append("project_root_missing")
    if not resolved_team_dir.exists() and "team_dir_missing" not in notes:
        notes.append("team_dir_missing")
    doc_roots = _normalize_path_list(data.get("doc_roots")) or _discover_doc_roots(resolved_project_root)
    canonical_todo_path = _trim(data.get("canonical_todo_path"), 400) or _discover_canonical_todo_path(
        resolved_project_root, resolved_team_dir
    )
    if not canonical_todo_path and "canonical_todo_missing" not in notes:
        notes.append("canonical_todo_missing")
    code_roots = _normalize_path_list(data.get("code_roots")) or [str(resolved_project_root)]
    runbook_paths = _normalize_path_list(data.get("canonical_runbook_paths")) or _discover_runbook_paths(resolved_project_root)
    ignore_globs = _normalize_string_list(data.get("doc_ignore_globs"), limit=24) or list(_DEFAULT_DOC_IGNORE_GLOBS)
    slot_limits = data.get("background_runner_slot_limits")
    if not isinstance(slot_limits, dict):
        slot_limits = entry_data.get("background_runner_slot_limits")
    if not isinstance(slot_limits, dict):
        slot_limits = {}
    normalized = {
        "version": max(1, int(data.get("version", 1) or 1)),
        "artifact_path": _trim(artifact_path, 400),
        "workspace_key": (
            _trim(data.get("workspace_key"), 128)
            or _trim(entry_data.get("name"), 128).lower()
            or _trim(entry_data.get("project_alias"), 32).lower()
            or "default"
        ),
        "project_alias": _trim(data.get("project_alias"), 32) or _trim(entry_data.get("project_alias"), 32) or "O1",
        "project_root": str(resolved_project_root),
        "state_root": _trim(data.get("state_root"), 400) or _derive_state_root(resolved_team_dir),
        "team_dir": str(resolved_team_dir),
        "project_overview": _trim(data.get("project_overview"), 240) or _trim(entry_data.get("overview"), 240),
        "code_roots": code_roots,
        "doc_roots": doc_roots,
        "doc_ignore_globs": ignore_globs,
        "canonical_todo_path": canonical_todo_path,
        "canonical_runbook_paths": runbook_paths,
        "model_routing_profile": _trim(data.get("model_routing_profile"), 64)
        or _trim(entry_data.get("model_routing_profile"), 64)
        or "default",
        "background_runner_target": _trim(data.get("background_runner_target"), 64)
        or _trim(entry_data.get("background_runner_target"), 64)
        or "local_background",
        "run_lock_mode_default": _trim(data.get("run_lock_mode_default"), 32)
        or _trim(entry_data.get("run_lock_mode"), 32)
        or "open",
        "background_runner_slot_limits": {
            "local_tmux": max(1, min(8, int((slot_limits or {}).get("local_tmux", 1) or 1))),
            "github_runner": max(1, min(8, int((slot_limits or {}).get("github_runner", 1) or 1))),
            "remote_worker": max(1, min(8, int((slot_limits or {}).get("remote_worker", 1) or 1))),
        },
        "endpoint_registry_path": _trim(data.get("endpoint_registry_path"), 400)
        or str(model_endpoint_registry_path(resolved_team_dir)),
        "routing_policy_path": _trim(data.get("routing_policy_path"), 400)
        or str(model_routing_policy_path(resolved_team_dir)),
        "onboarding_status": _derive_onboarding_status(
            resolved_project_root,
            resolved_team_dir,
            notes,
            explicit=_trim(data.get("onboarding_status"), 32),
        ),
        "validation_notes": notes,
        "summary": "",
    }
    normalized["summary"] = summarize_workspace_brief_payload(normalized)
    return normalized


def build_workspace_brief(*, project_root: Any, team_dir: Any, entry: Any = None) -> Dict[str, Any]:
    return sanitize_workspace_brief(
        {},
        project_root=project_root,
        team_dir=team_dir,
        entry=entry,
        artifact_path=str(workspace_brief_path(team_dir)),
    )


def load_workspace_brief(team_dir: Any, *, entry: Any = None, project_root: Any = "") -> Dict[str, Any]:
    backend = artifact_backend(team_dir)
    path = backend.workspace_brief_path()
    payload = backend.load_workspace_brief()
    if not payload and not path.exists():
        return build_workspace_brief(project_root=project_root, team_dir=team_dir, entry=entry)
    return sanitize_workspace_brief(
        payload,
        project_root=project_root,
        team_dir=team_dir,
        entry=entry,
        artifact_path=str(path),
    )


def write_workspace_brief(team_dir: Any, payload: Any, *, project_root: Any = "", entry: Any = None) -> Dict[str, Any]:
    path = artifact_backend(team_dir).workspace_brief_path()
    normalized = sanitize_workspace_brief(
        payload,
        project_root=project_root,
        team_dir=team_dir,
        entry=entry,
        artifact_path=str(path),
    )
    artifact_backend(team_dir).write_workspace_brief(normalized)
    return normalized


def summarize_workspace_brief(team_dir: Any, *, entry: Any = None, project_root: Any = "") -> str:
    brief = load_workspace_brief(team_dir, entry=entry, project_root=project_root)
    return str(brief.get("summary", "")).strip() or summarize_workspace_brief_payload(brief)
