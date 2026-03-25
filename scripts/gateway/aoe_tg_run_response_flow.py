#!/usr/bin/env python3
"""Reply markup and result sending helpers for run handlers."""

from typing import Any, Callable, Dict, Optional

from aoe_tg_exec_results import (
    confirmed_result_reply_markup as exec_confirmed_result_reply_markup,
    early_gate_reply_markup as exec_early_gate_reply_markup,
    intervention_reply_markup as exec_intervention_reply_markup,
    send_dispatch_exception as exec_send_dispatch_exception,
    send_dispatch_result as exec_send_dispatch_result,
    send_exec_critic_intervention as exec_send_exec_critic_intervention,
)
from aoe_tg_run_guards import (
    confirm_required_reply_markup as guard_confirm_required_reply_markup,
    rate_limit_reply_markup as guard_rate_limit_reply_markup,
)


def _confirm_required_reply_markup() -> Dict[str, Any]:
    return guard_confirm_required_reply_markup()


def _rate_limit_reply_markup(entry: Optional[Dict[str, Any]] = None, key: str = "") -> Dict[str, Any]:
    return guard_rate_limit_reply_markup(entry, key)


def _confirmed_result_reply_markup(entry: Dict[str, Any], key: str) -> Dict[str, Any]:
    return exec_confirmed_result_reply_markup(entry, key)


def _early_gate_reply_markup(entry: Dict[str, Any], key: str) -> Dict[str, Any]:
    return exec_early_gate_reply_markup(entry, key)


def _intervention_reply_markup(entry: Dict[str, Any], key: str, req_id: str = "") -> Dict[str, Any]:
    return exec_intervention_reply_markup(entry, key, req_id)


def _send_exec_critic_intervention(
    *,
    entry: Dict[str, Any],
    key: str,
    final_req_id: str,
    verdict: str,
    reason: str,
    exec_attempt: int,
    exec_max_attempts: int,
    send: Callable[..., bool],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    if callable(record_outcome):
        record_outcome(
            {
                "kind": "retry_run",
                "status": "blocked",
                "reason_code": "exec_critic",
                "task_request_id": str(final_req_id or "").strip(),
                "next_step": f"/task {final_req_id}" if str(final_req_id or "").strip() else "/task",
                "detail": str(reason or "").strip() or "exec critic intervention needed",
            }
        )
    exec_send_exec_critic_intervention(
        entry=entry,
        key=key,
        final_req_id=final_req_id,
        verdict=verdict,
        reason=reason,
        exec_attempt=exec_attempt,
        exec_max_attempts=exec_max_attempts,
        send=send,
    )


def _send_dispatch_exception(
    *,
    entry: Dict[str, Any],
    key: str,
    todo_id: str,
    reason: str,
    send: Callable[..., bool],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    if callable(record_outcome):
        record_outcome(
            {
                "kind": "retry_run",
                "status": "blocked",
                "reason_code": "dispatch_exception",
                "next_step": "/offdesk review",
                "detail": str(reason or "").strip() or "dispatch failed before request start",
            }
        )
    exec_send_dispatch_exception(
        entry=entry,
        key=key,
        todo_id=todo_id,
        reason=reason,
        send=send,
    )



def _send_dispatch_result(
    *,
    args: Any,
    key: str,
    entry: Dict[str, Any],
    p_args: Any,
    prompt: str,
    state: Dict[str, Any],
    req_id: str,
    task: Optional[Dict[str, Any]],
    run_control_mode: str,
    run_source_request_id: str,
    run_auto_source: str,
    send: Callable[..., bool],
    log_event: Callable[..., None],
    summarize_task_lifecycle: Callable[[str, Dict[str, Any]], str],
    synthesize_orchestrator_response: Callable[[Any, str, Dict[str, Any]], str],
    render_run_response: Callable[..., str],
    finalize_request_reply_messages: Callable[..., Dict[str, Any]],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> bool:
    return exec_send_dispatch_result(
        args=args,
        key=key,
        entry=entry,
        p_args=p_args,
        prompt=prompt,
        state=state,
        req_id=req_id,
        task=task,
        run_control_mode=run_control_mode,
        run_source_request_id=run_source_request_id,
        run_auto_source=run_auto_source,
        send=send,
        log_event=log_event,
        summarize_task_lifecycle=summarize_task_lifecycle,
        synthesize_orchestrator_response=synthesize_orchestrator_response,
        render_run_response=render_run_response,
        finalize_request_reply_messages=finalize_request_reply_messages,
        record_outcome=record_outcome,
    )

