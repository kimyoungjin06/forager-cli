"""Shared forager binary resolution for harness scripts and services.

Services must not point at ``target/debug``: cargo rewrites it mid-build, so
a rebuild can hand a running timer a half-written binary. The stable install
path is ``~/.local/bin/forager`` (refresh deliberately with
``cargo build --release && cp target/release/forager ~/.local/bin/``).

Resolution order: ``$FORAGER_BIN`` -> ``~/.local/bin/forager`` -> newest of
``target/release`` / ``target/debug`` -> ``forager`` on PATH.
"""

from __future__ import annotations

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STABLE_BIN = pathlib.Path.home() / ".local" / "bin" / "forager"


def resolve_forager_bin() -> str:
    explicit = os.environ.get("FORAGER_BIN", "").strip()
    if explicit:
        return explicit
    if STABLE_BIN.exists():
        return str(STABLE_BIN)
    candidates = [
        REPO_ROOT / "target" / "release" / "forager",
        REPO_ROOT / "target" / "debug" / "forager",
    ]
    existing = [path for path in candidates if path.exists()]
    if existing:
        existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return str(existing[0])
    return "forager"
