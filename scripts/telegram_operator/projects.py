"""Project registry: the single source of truth for multi-project routing.

The registry file (default ``~/.config/forager/projects.toml``) maps each
managed project to its workspace path patterns, forager session group, and
wiki knowledge plane. Fan-out (routing operator input to a project) and
fan-in (aggregating status across projects) both resolve through it.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from typing import Any

PROJECT_REGISTRY_SCHEMA = "forager_project_registry.v1"


def default_registry_path() -> pathlib.Path:
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return pathlib.Path(
        os.environ.get("FORAGER_PROJECT_REGISTRY", str(cfg / "forager" / "projects.toml"))
    )


def load_registry(path: pathlib.Path | None = None) -> dict[str, dict[str, Any]]:
    """Return {project_key: entry} or {} when the registry is absent/invalid."""

    registry_path = path or default_registry_path()
    try:
        raw = tomllib.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return {}
    if raw.get("schema") != PROJECT_REGISTRY_SCHEMA:
        return {}
    projects = raw.get("projects")
    if not isinstance(projects, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        patterns = [
            str(item).strip()
            for item in (entry.get("workspace_patterns") or [])
            if str(item).strip()
        ]
        normalized[str(key)] = {
            "key": str(key),
            "display_name": str(entry.get("display_name") or key),
            "workspace_patterns": patterns,
            "session_group": str(entry.get("session_group") or "").strip() or None,
            "wiki_profile": str(entry.get("wiki_profile") or "").strip() or None,
        }
    return normalized


def resolve_project_for_path(
    path: str, registry: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Match a filesystem path to a project via substring workspace patterns."""

    text = str(path or "")
    if not text:
        return None
    for entry in registry.values():
        for pattern in entry.get("workspace_patterns") or []:
            if pattern and pattern in text:
                return entry
    return None


def registry_summary(registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact projection of the registry for prompts and cards."""

    return [
        {
            "key": entry["key"],
            "display_name": entry["display_name"],
            "wiki_profile": entry.get("wiki_profile"),
        }
        for entry in registry.values()
    ]
