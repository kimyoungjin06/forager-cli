#!/usr/bin/env python3
"""Shared task feedback persistence for dashboard execution actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def summarize_canonical_writeback(
    *,
    headline: str = "Syncback Apply | executed",
    state: str,
    next_step: str,
    at: str,
    path: str,
    line_count: int,
    done_count: int,
    reopen_count: int,
    append_count: int,
    blocked_count: int,
) -> str:
    path_token = Path(str(path or "").strip()).name if str(path or "").strip() else "-"
    return (
        "{headline} | state={state} | next={next_step} | at={at} | "
        "path={path} lines={lines} done={done} reopen={reopen} append={append} blocked={blocked}"
    ).format(
        headline=str(headline or "").strip() or "Canonical Writeback",
        state=str(state or "").strip() or "-",
        next_step=str(next_step or "").strip() or "-",
        at=str(at or "").strip() or "-",
        path=path_token,
        lines=max(0, int(line_count or 0)),
        done=max(0, int(done_count or 0)),
        reopen=max(0, int(reopen_count or 0)),
        append=max(0, int(append_count or 0)),
        blocked=max(0, int(blocked_count or 0)),
    )


def persist_canonical_writeback_state(
    task: Dict[str, Any],
    *,
    headline: str = "Syncback Apply | executed",
    state: str,
    next_step: str,
    at: str,
    path: str,
    line_count: int,
    done_count: int,
    reopen_count: int,
    append_count: int,
    blocked_count: int,
) -> str:
    summary = summarize_canonical_writeback(
        headline=headline,
        state=state,
        next_step=next_step,
        at=at,
        path=path,
        line_count=line_count,
        done_count=done_count,
        reopen_count=reopen_count,
        append_count=append_count,
        blocked_count=blocked_count,
    )
    task["background_run_canonical_writeback_status"] = str(state or "").strip() or "-"
    task["background_run_canonical_writeback_summary"] = summary
    task["background_run_canonical_writeback_at"] = str(at or "").strip() or "-"
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_canonical_writeback_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_canonical_writeback_summary"] = summary
        task["result"]["background_run_canonical_writeback_at"] = str(at or "").strip() or "-"
    return summary


def summarize_manual_step_execution(
    *,
    manual_kind: str,
    source_command: str,
    state: str,
    next_step: str,
    at: str,
) -> str:
    kind = str(manual_kind or "").strip().lower()
    label = {
        "manual_review": "manual_review",
        "manual_execute": "manual_execute",
        "manual_followup": "manual_followup",
    }.get(kind, "manual_step")
    return "{label}={command} | state={state} | next={next_step} | at={at}".format(
        label=label,
        command=str(source_command or "").strip() or "-",
        state=str(state or "").strip() or "-",
        next_step=str(next_step or "").strip() or "-",
        at=str(at or "").strip() or "-",
    )


def persist_manual_step_execution_state(
    task: Dict[str, Any],
    *,
    manual_kind: str,
    source_command: str,
    state: str,
    next_step: str,
    at: str,
) -> str:
    summary = summarize_manual_step_execution(
        manual_kind=manual_kind,
        source_command=source_command,
        state=state,
        next_step=next_step,
        at=at,
    )
    task["background_run_manual_step_execution_status"] = str(state or "").strip() or "-"
    task["background_run_manual_step_execution_kind"] = str(manual_kind or "").strip() or "-"
    task["background_run_manual_step_execution_command"] = str(source_command or "").strip() or "-"
    task["background_run_manual_step_execution_next_step"] = str(next_step or "").strip() or "-"
    task["background_run_manual_step_execution_summary"] = summary
    task["background_run_manual_step_execution_at"] = str(at or "").strip() or "-"
    task["updated_at"] = str(at or "").strip() or task.get("updated_at", "")
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_manual_step_execution_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_manual_step_execution_kind"] = str(manual_kind or "").strip() or "-"
        task["result"]["background_run_manual_step_execution_command"] = str(source_command or "").strip() or "-"
        task["result"]["background_run_manual_step_execution_next_step"] = str(next_step or "").strip() or "-"
        task["result"]["background_run_manual_step_execution_summary"] = summary
        task["result"]["background_run_manual_step_execution_at"] = str(at or "").strip() or "-"
    return summary
