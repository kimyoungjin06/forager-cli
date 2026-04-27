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
    mutation = classify_canonical_mutation(
        path=path,
        line_count=line_count,
        done_count=done_count,
        reopen_count=reopen_count,
        append_count=append_count,
        blocked_count=blocked_count,
    )
    kind = str(mutation.get("kind", "")).strip() or "-"
    profile = str(mutation.get("profile", "")).strip() or "-"
    path_token = Path(str(path or "").strip()).name if str(path or "").strip() else "-"
    return (
        "{kind}:{profile} | path={path} | lines={lines} | done={done} reopen={reopen} append={append} blocked={blocked} | "
        "state={state} | at={at}"
    ).format(
        kind=kind,
        profile=profile,
        path=path_token,
        lines=max(0, int(line_count or 0)),
        done=max(0, int(done_count or 0)),
        reopen=max(0, int(reopen_count or 0)),
        append=max(0, int(append_count or 0)),
        blocked=max(0, int(blocked_count or 0)),
        state=str(state or "").strip() or "-",
        at=str(at or "").strip() or "-",
    )


def classify_canonical_mutation(
    *,
    path: str,
    line_count: int,
    done_count: int,
    reopen_count: int,
    append_count: int,
    blocked_count: int,
) -> Dict[str, Any]:
    path_token = Path(str(path or "").strip()).name
    path_upper = path_token.upper()
    if path_upper in {"TODO", "TODO.MD", "TODO.TXT"} or path_upper.startswith("TODO."):
        kind = "todo_syncback"
    elif path_token.lower().endswith(".md"):
        kind = "markdown_syncback"
    else:
        kind = "artifact_syncback"
    counts = {
        "done": max(0, int(done_count or 0)),
        "reopen": max(0, int(reopen_count or 0)),
        "append": max(0, int(append_count or 0)),
        "blocked": max(0, int(blocked_count or 0)),
    }
    positive = [name for name, value in counts.items() if value > 0]
    if not positive:
        profile = "line_only" if max(0, int(line_count or 0)) > 0 else "noop"
    elif positive == ["done"]:
        profile = "done_only"
    elif positive == ["reopen"]:
        profile = "reopen_only"
    elif positive == ["append"]:
        profile = "append_only"
    elif positive == ["blocked"]:
        profile = "blocked_only"
    elif positive == ["append", "done"] or positive == ["done", "append"]:
        profile = "append_done"
    elif positive == ["append", "reopen"] or positive == ["reopen", "append"]:
        profile = "append_reopen"
    elif positive == ["done", "reopen"] or positive == ["reopen", "done"]:
        profile = "done_reopen"
    elif positive == ["append", "blocked"] or positive == ["blocked", "append"]:
        profile = "append_blocked"
    elif positive == ["done", "blocked"] or positive == ["blocked", "done"]:
        profile = "done_blocked"
    else:
        profile = "mixed"
    return {
        "kind": kind,
        "profile": profile,
        "path": path_token or "-",
        "line_count": max(0, int(line_count or 0)),
        "done_count": counts["done"],
        "reopen_count": counts["reopen"],
        "append_count": counts["append"],
        "blocked_count": counts["blocked"],
    }


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
    mutation = classify_canonical_mutation(
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
    task["background_run_canonical_writeback_next_step"] = str(next_step or "").strip() or "-"
    task["background_run_canonical_writeback_path"] = str(mutation.get("path", "")).strip() or "-"
    task["background_run_canonical_mutation_status"] = str(state or "").strip() or "-"
    task["background_run_canonical_mutation_summary"] = mutation_summary
    task["background_run_canonical_mutation_at"] = str(at or "").strip() or "-"
    task["background_run_canonical_mutation_kind"] = str(mutation.get("kind", "")).strip() or "-"
    task["background_run_canonical_mutation_profile"] = str(mutation.get("profile", "")).strip() or "-"
    task["background_run_canonical_mutation_path"] = str(mutation.get("path", "")).strip() or "-"
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_canonical_writeback_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_canonical_writeback_summary"] = summary
        task["result"]["background_run_canonical_writeback_at"] = str(at or "").strip() or "-"
        task["result"]["background_run_canonical_writeback_next_step"] = str(next_step or "").strip() or "-"
        task["result"]["background_run_canonical_writeback_path"] = str(mutation.get("path", "")).strip() or "-"
        task["result"]["background_run_canonical_mutation_status"] = str(state or "").strip() or "-"
        task["result"]["background_run_canonical_mutation_summary"] = mutation_summary
        task["result"]["background_run_canonical_mutation_at"] = str(at or "").strip() or "-"
        task["result"]["background_run_canonical_mutation_kind"] = str(mutation.get("kind", "")).strip() or "-"
        task["result"]["background_run_canonical_mutation_profile"] = str(mutation.get("profile", "")).strip() or "-"
        task["result"]["background_run_canonical_mutation_path"] = str(mutation.get("path", "")).strip() or "-"
    return summary


def derive_canonical_writeback_feedback(
    task: Dict[str, Any],
    *,
    suggested_action: str,
) -> Dict[str, Any]:
    if not isinstance(task, dict) or not task:
        return {}
    action = str(suggested_action or "").strip().lower()
    if action not in {"followup", "followup_execute"}:
        return {}
    writeback_status = str(task.get("background_run_canonical_writeback_status", "")).strip().lower()
    mutation_status = str(task.get("background_run_canonical_mutation_status", "")).strip().lower()
    syncback_status = str(task.get("background_run_worker_syncback_status", "")).strip().lower()
    if writeback_status not in {"executed", "applied"}:
        return {}
    if mutation_status not in {"executed", "applied"}:
        return {}
    if syncback_status not in {"applied", "executed", ""}:
        return {}
    next_step = str(task.get("background_run_canonical_writeback_next_step", "")).strip() or "-"
    summary = str(task.get("background_run_canonical_writeback_summary", "")).strip() or "-"
    kind = str(task.get("background_run_canonical_mutation_kind", "")).strip() or "-"
    profile = str(task.get("background_run_canonical_mutation_profile", "")).strip() or "-"
    path = str(task.get("background_run_canonical_mutation_path", "")).strip() or "-"
    at = str(task.get("background_run_canonical_writeback_at", "")).strip() or "-"
    can_reuse_next_step = next_step.startswith("/")
    return {
        "status": writeback_status or "-",
        "mutation_status": mutation_status or "-",
        "syncback_status": syncback_status or "-",
        "summary": summary,
        "next_step": next_step,
        "kind": kind,
        "profile": profile,
        "path": path,
        "at": at,
        "can_reuse_next_step": can_reuse_next_step,
    }


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
