#!/usr/bin/env python3
"""Shared task feedback persistence for dashboard execution actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def _manual_kind_for_suggested_action(action: str) -> str:
    token = str(action or "").strip().lower()
    if token in {"manual_review", "review", "judge"}:
        return "manual_review"
    if token == "followup_execute":
        return "manual_execute"
    if token == "followup":
        return "manual_followup"
    return ""


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


def summarize_canonical_mutation(
    *,
    state: str,
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
        "path={path} | lines={lines} | done={done} reopen={reopen} append={append} blocked={blocked} | "
        "state={state} | at={at}"
    ).format(
        path=path_token,
        lines=max(0, int(line_count or 0)),
        done=max(0, int(done_count or 0)),
        reopen=max(0, int(reopen_count or 0)),
        append=max(0, int(append_count or 0)),
        blocked=max(0, int(blocked_count or 0)),
        state=str(state or "").strip() or "-",
        at=str(at or "").strip() or "-",
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
    mutation_summary = summarize_canonical_mutation(
        state=state,
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
    task["background_run_canonical_mutation_status"] = str(state or "").strip() or "-"
    task["background_run_canonical_mutation_summary"] = mutation_summary
    task["background_run_canonical_mutation_at"] = str(at or "").strip() or "-"
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_canonical_writeback_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_canonical_writeback_summary"] = summary
        task["result"]["background_run_canonical_writeback_at"] = str(at or "").strip() or "-"
        task["result"]["background_run_canonical_mutation_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_canonical_mutation_summary"] = mutation_summary
        task["result"]["background_run_canonical_mutation_at"] = str(at or "").strip() or "-"
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


def derive_manual_step_feedback(
    task: Dict[str, Any],
    *,
    suggested_action: str,
    suggested_next_step: str,
) -> Dict[str, Any]:
    if not isinstance(task, dict) or not task:
        return {}
    expected_kind = _manual_kind_for_suggested_action(suggested_action)
    if not expected_kind:
        return {}
    kind = str(task.get("background_run_manual_step_execution_kind", "")).strip().lower()
    if kind != expected_kind:
        return {}
    state = str(task.get("background_run_manual_step_execution_status", "")).strip() or "-"
    command = str(task.get("background_run_manual_step_execution_command", "")).strip() or "-"
    next_step = str(task.get("background_run_manual_step_execution_next_step", "")).strip() or "-"
    at = str(task.get("background_run_manual_step_execution_at", "")).strip() or "-"
    summary = str(task.get("background_run_manual_step_execution_summary", "")).strip() or "-"
    suggested_next = str(suggested_next_step or "").strip()
    can_reuse_next_step = state in {"preview", "executed"} and next_step.startswith("/")
    return {
        "kind": expected_kind,
        "state": state,
        "command": command,
        "next_step": next_step,
        "at": at,
        "summary": summary,
        "matches_action": True,
        "matches_next_step": bool(
            suggested_next
            and suggested_next in {command, next_step}
        ),
        "can_reuse_next_step": can_reuse_next_step,
    }
