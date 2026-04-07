#!/usr/bin/env python3
"""Project-level execution run lock helpers."""

from __future__ import annotations

from typing import Any, Dict


_RUN_LOCK_MODES = {"open", "test_only"}
_SMALL_TEST_MARKERS = {
    "pytest",
    "unit_test",
    "dashboard_test",
    "small_test",
    "test_small",
    "smoke_test",
    "error_test",
}


def normalize_run_lock_mode(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    return token if token in _RUN_LOCK_MODES else "open"


def project_run_lock_mode(entry: Dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return "open"
    return normalize_run_lock_mode(entry.get("run_lock_mode"))


def launch_counts_as_small_test(
    *,
    launch_mode: str = "",
    source_surface: str = "",
    source_command: str = "",
    launch_spec: Dict[str, Any] | None = None,
) -> bool:
    spec = launch_spec if isinstance(launch_spec, dict) else {}
    scope = str(spec.get("test_scope", "")).strip().lower()
    if scope in {"small", "small_test", "test_small"}:
        return True
    haystack = " ".join(
        [
            str(launch_mode or "").strip().lower(),
            str(source_surface or "").strip().lower(),
            str(source_command or "").strip().lower(),
            str(spec.get("mode", "") or "").strip().lower(),
            str(spec.get("summary", "") or "").strip().lower(),
        ]
    )
    return any(marker in haystack for marker in _SMALL_TEST_MARKERS)


def project_run_lock_blocks_launch(
    entry: Dict[str, Any] | None,
    *,
    launch_mode: str = "",
    source_surface: str = "",
    source_command: str = "",
    launch_spec: Dict[str, Any] | None = None,
) -> bool:
    if project_run_lock_mode(entry) != "test_only":
        return False
    return not launch_counts_as_small_test(
        launch_mode=launch_mode,
        source_surface=source_surface,
        source_command=source_command,
        launch_spec=launch_spec,
    )


def project_run_lock_note(entry: Dict[str, Any] | None) -> str:
    mode = project_run_lock_mode(entry)
    if mode == "test_only":
        return "test_only lock is active; only small test launches are allowed"
    return ""
