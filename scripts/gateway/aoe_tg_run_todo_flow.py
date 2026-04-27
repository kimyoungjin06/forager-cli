#!/usr/bin/env python3
"""Todo and follow-up helper wrappers for run handlers."""

from typing import Any, Callable, Dict, List, Optional

from aoe_tg_exec_pipeline import (
    attach_todo_to_task_and_entry as exec_attach_todo_to_task_and_entry,
    cleanup_terminal_todo_gate as exec_cleanup_terminal_todo_gate,
    effective_todo_token as exec_effective_todo_token,
    finalize_todo_after_run as exec_finalize_todo_after_run,
    find_project_todo_item as exec_find_project_todo_item,
    find_todo_proposal_row as exec_find_todo_proposal_row,
    maybe_capture_todo_proposals as exec_maybe_capture_todo_proposals,
    maybe_send_manual_followup_alert as exec_maybe_send_manual_followup_alert,
    task_label_for_todo as exec_task_label_for_todo,
)

_BLOCKED_MANUAL_FOLLOWUP_THRESHOLD = 2


def _task_label_for_todo(task: Optional[Dict[str, Any]], fallback_request_id: str) -> str:
    return exec_task_label_for_todo(task, fallback_request_id)


def _find_project_todo_item(entry: Dict[str, Any], todo_id: str) -> Optional[Dict[str, Any]]:
    return exec_find_project_todo_item(entry, todo_id)


def _attach_todo_to_task_and_entry(
    *,
    entry: Dict[str, Any],
    chat_id: str,
    todo_id: str,
    req_id: str,
    task: Optional[Dict[str, Any]],
    now_iso: Callable[[], str],
) -> None:
    exec_attach_todo_to_task_and_entry(
        entry=entry,
        chat_id=chat_id,
        todo_id=todo_id,
        req_id=req_id,
        task=task,
        now_iso=now_iso,
    )


def _effective_todo_token(
    *,
    entry: Dict[str, Any],
    chat_id: str,
    todo_id: str,
    run_auto_source: str,
) -> str:
    return exec_effective_todo_token(
        entry=entry,
        chat_id=chat_id,
        todo_id=todo_id,
        run_auto_source=run_auto_source,
    )


def _maybe_send_manual_followup_alert(
    *,
    entry: Dict[str, Any],
    todo_id: str,
    project_key: str,
    send: Callable[..., bool],
    now_iso: Callable[[], str],
) -> bool:
    return exec_maybe_send_manual_followup_alert(
        entry=entry,
        todo_id=todo_id,
        project_key=project_key,
        send=send,
        now_iso=now_iso,
    )


def _find_todo_proposal_row(entry: Dict[str, Any], proposal_id: str) -> Optional[Dict[str, Any]]:
    return exec_find_todo_proposal_row(entry, proposal_id)


def _maybe_capture_todo_proposals(
    *,
    args: Any,
    entry: Dict[str, Any],
    key: str,
    p_args: Any,
    prompt: str,
    state: Dict[str, Any],
    req_id: str,
    task: Optional[Dict[str, Any]],
    todo_id: str,
    send: Callable[..., bool],
    log_event: Callable[..., None],
    now_iso: Callable[[], str],
    extract_todo_proposals: Callable[..., List[Dict[str, Any]]],
    merge_todo_proposals: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    return exec_maybe_capture_todo_proposals(
        args=args,
        entry=entry,
        key=key,
        p_args=p_args,
        prompt=prompt,
        state=state,
        req_id=req_id,
        task=task,
        todo_id=todo_id,
        send=send,
        log_event=log_event,
        now_iso=now_iso,
        extract_todo_proposals=extract_todo_proposals,
        merge_todo_proposals=merge_todo_proposals,
    )


def _finalize_todo_after_run(
    *,
    entry: Dict[str, Any],
    todo_id: str,
    status: str,
    exec_verdict: str,
    exec_reason: str,
    req_id: str,
    task: Optional[Dict[str, Any]],
    now_iso: Callable[[], str],
) -> None:
    exec_finalize_todo_after_run(
        entry=entry,
        todo_id=todo_id,
        status=status,
        exec_verdict=exec_verdict,
        exec_reason=exec_reason,
        req_id=req_id,
        task=task,
        now_iso=now_iso,
        manual_followup_threshold=_BLOCKED_MANUAL_FOLLOWUP_THRESHOLD,
    )


def _cleanup_terminal_todo_gate(
    *,
    entry: Dict[str, Any],
    chat_id: str,
    todo_id: str,
    pending_todo_used: bool,
    run_auto_source: str,
    reason: str,
    now_iso: Callable[[], str],
) -> bool:
    return exec_cleanup_terminal_todo_gate(
        entry=entry,
        chat_id=chat_id,
        todo_id=todo_id,
        pending_todo_used=pending_todo_used,
        run_auto_source=run_auto_source,
        reason=reason,
        now_iso=now_iso,
        manual_followup_threshold=_BLOCKED_MANUAL_FOLLOWUP_THRESHOLD,
    )

