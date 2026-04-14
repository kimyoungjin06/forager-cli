#!/usr/bin/env python3
"""Executor adapter runtime helpers for background ticket lifecycle handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_model_provider_adapter as model_provider_adapter
from aoe_tg_background_runs import advance_background_run_ticket, upsert_background_run_ticket
from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import poll_external_background_tickets
from aoe_tg_tmux_background_worker import poll_local_tmux_background_tickets


def dispatch_claimed_background_ticket_via_adapter(
    *,
    queue_path: Path,
    claimed_ticket: Dict[str, Any],
    now_iso: Callable[[], str],
    run_target: Callable[[], Any],
    on_ticket_update: Callable[[Dict[str, Any]], None],
    on_queue_error: Callable[[str, Exception], None],
    completed_evidence_artifacts: Callable[[], list[str]] | None = None,
    completed_evidence_bundle: Callable[[], str] | None = None,
) -> Any:
    ticket = claimed_ticket if isinstance(claimed_ticket, dict) else {}
    token = str(ticket.get("ticket_id", "")).strip()
    runner_target = normalize_executor_runner_target(ticket.get("runner_target", ""), "local_background") or "local_background"
    if runner_target != "local_background" or not token:
        return run_target()

    worker_probe = model_endpoint_adapter.probe_background_ticket_worker_binding(queue_path.parent, ticket)
    binding = worker_probe.get("binding") if isinstance(worker_probe.get("binding"), dict) else {}
    binding_summary = str(binding.get("summary", "")).strip() if binding.get("bound") else ""
    probe_status = str(worker_probe.get("probe_status", "")).strip()
    launch_spec = dict(ticket.get("launch_spec") or {}) if isinstance(ticket.get("launch_spec"), dict) else {}
    launch_kind = str(launch_spec.get("kind", "")).strip().lower()
    probe_summary = str(worker_probe.get("summary", "")).strip()
    if binding_summary:
        launch_spec["model_worker_binding_summary"] = binding_summary
    if probe_status:
        launch_spec["model_worker_probe_status"] = probe_status
    if probe_summary:
        launch_spec["model_worker_probe_summary"] = probe_summary
    if launch_spec:
        ticket["launch_spec"] = launch_spec
        ticket = upsert_background_run_ticket(queue_path, ticket, now_iso=now_iso) or ticket
    if binding.get("bound") and (not bool(worker_probe.get("ok"))):
        try:
            failed = advance_background_run_ticket(
                queue_path,
                token,
                now_iso=now_iso,
                status="failed",
                runner_target=runner_target,
                evidence_bundle=f"status=failed | reason=model_route_probe_failed | probe={probe_status or 'failed'}",
            )
            if failed:
                on_ticket_update(failed)
        except Exception as exc:  # pragma: no cover - defensive path
            on_queue_error("background_run_state_write_failed", exc)
        raise RuntimeError(f"model_route_probe_failed:{probe_status or 'failed'}")

    provider_invoke_result: Dict[str, Any] = {}
    provider_failure_bundle = ""

    def _invoke_provider_ticket() -> Dict[str, Any]:
        nonlocal provider_invoke_result, provider_failure_bundle
        provider_invoke_result = model_provider_adapter.invoke_background_ticket_worker(
            queue_path.parent,
            ticket=ticket,
        )
        if provider_invoke_result.get("ok"):
            return provider_invoke_result
        reason_code = str(provider_invoke_result.get("reason_code", "")).strip() or "provider_invoke_failed"
        summary_text = str(provider_invoke_result.get("summary", "")).strip()
        parts = [f"status=failed", f"reason={reason_code[:120]}"]
        if summary_text:
            parts.append(summary_text[:160])
        provider_failure_bundle = " | ".join(parts)
        raise RuntimeError(reason_code)

    active_run_target = _invoke_provider_ticket if launch_kind == "provider_invoke" else run_target

    try:
        runtime_summary = "provider_invoke_started" if launch_kind == "provider_invoke" else "dispatch_flow_started"
        if binding_summary:
            runtime_summary += f" | worker={binding_summary}"
        if probe_status and probe_status != "unbound":
            runtime_summary += f" | probe={probe_status}"
        evidence_bundle = (
            "status=running | outcome=provider_invoke_started"
            if launch_kind == "provider_invoke"
            else "status=running | outcome=dispatch_flow_started"
        )
        if probe_status and probe_status != "unbound":
            evidence_bundle += f" | worker_probe={probe_status}"
        running = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="running",
            runner_target=runner_target,
            runtime_summary=runtime_summary,
            evidence_bundle=evidence_bundle,
        )
        if running:
            on_ticket_update(running)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_state_write_failed", exc)

    try:
        result = active_run_target()
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
        try:
            failed = advance_background_run_ticket(
                queue_path,
                token,
                now_iso=now_iso,
                status="failed",
                runner_target=runner_target,
                evidence_bundle=provider_failure_bundle or f"status=failed | reason={reason[:160]}",
            )
            if failed:
                on_ticket_update(failed)
        except Exception as queue_exc:  # pragma: no cover - defensive path
            on_queue_error("background_run_state_write_failed", queue_exc)
        raise

    try:
        completed_artifacts = list(completed_evidence_artifacts() or []) if callable(completed_evidence_artifacts) else []
        completed_runtime_summary = ""
        completed_bundle = (
            str(completed_evidence_bundle() or "").strip()
            if callable(completed_evidence_bundle)
            else "status=completed | outcome=dispatch_flow_returned"
        )
        if provider_invoke_result:
            route_id = str(provider_invoke_result.get("route_id", "")).strip() or "background_worker_primary"
            endpoint_id = str(provider_invoke_result.get("endpoint_id", "")).strip() or "-"
            model_name = str(provider_invoke_result.get("model", "")).strip() or "-"
            response_text = str(provider_invoke_result.get("response_text", "")).strip()
            contract_summary = str(provider_invoke_result.get("task_contract_summary", "")).strip()
            task_result_status = str(provider_invoke_result.get("task_result_status", "")).strip()
            task_result_summary = str(provider_invoke_result.get("task_result_summary", "")).strip()
            task_gate_status = str(provider_invoke_result.get("task_gate_status", "")).strip()
            task_gate_summary = str(provider_invoke_result.get("task_gate_summary", "")).strip()
            task_profile_status = str(provider_invoke_result.get("task_profile_status", "")).strip()
            task_profile_summary = str(provider_invoke_result.get("task_profile_summary", "")).strip()
            task_checklist_status = str(provider_invoke_result.get("task_checklist_status", "")).strip()
            task_checklist_summary = str(provider_invoke_result.get("task_checklist_summary", "")).strip()
            task_items_summary = str(provider_invoke_result.get("task_items_summary", "")).strip()
            task_item_classes_summary = str(provider_invoke_result.get("task_item_classes_summary", "")).strip()
            task_records_summary = str(provider_invoke_result.get("task_records_summary", "")).strip()
            task_record_rows_summary = str(provider_invoke_result.get("task_record_rows_summary", "")).strip()
            task_result_actions = [
                str(item).strip()
                for item in (provider_invoke_result.get("task_result_actions") or [])
                if str(item).strip()
            ]
            task_result_cautions = [
                str(item).strip()
                for item in (provider_invoke_result.get("task_result_cautions") or [])
                if str(item).strip()
            ]
            task_result_evidence_refs = [
                str(item).strip()
                for item in (provider_invoke_result.get("task_result_evidence_refs") or [])
                if str(item).strip()
            ]
            task_update_stub_status = str(provider_invoke_result.get("task_update_stub_status", "")).strip()
            task_update_stub_summary = str(provider_invoke_result.get("task_update_stub_summary", "")).strip()
            task_update_stub_targets = [
                str(item).strip()
                for item in (provider_invoke_result.get("task_update_stub_targets") or [])
                if str(item).strip()
            ]
            completed_runtime_summary = (
                f"provider_invoke_completed | route={route_id} | endpoint={endpoint_id} | model={model_name}"
            )[:240]
            if contract_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {contract_summary}"[:240]
            if task_result_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_result_summary}"[:240]
            if task_gate_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_gate_summary}"[:240]
            if task_profile_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_profile_summary}"[:240]
            if task_checklist_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_checklist_summary}"[:240]
            if task_items_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_items_summary}"[:240]
            if task_item_classes_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_item_classes_summary}"[:240]
            if task_records_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_records_summary}"[:240]
            if task_record_rows_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_record_rows_summary}"[:240]
            if task_update_stub_summary:
                completed_runtime_summary = f"{completed_runtime_summary} | {task_update_stub_summary}"[:240]
            bundle_parts = [
                "status=completed",
                "outcome=provider_invoke_ok",
                f"route={route_id}",
                f"endpoint={endpoint_id}",
                f"model={model_name}",
            ]
            if contract_summary:
                bundle_parts.append(contract_summary[:80])
            if task_result_status:
                bundle_parts.append(f"worker={task_result_status[:48]}")
            if task_gate_status:
                bundle_parts.append(f"gate={task_gate_status[:48]}")
            if task_profile_status:
                bundle_parts.append(f"profile={task_profile_status[:48]}")
            if task_checklist_status:
                bundle_parts.append(f"check={task_checklist_status[:48]}")
            if response_text:
                bundle_parts.append(f"response={response_text[:80]}")
            if task_result_summary:
                bundle_parts.append(task_result_summary[:80])
            if task_update_stub_status:
                bundle_parts.append(f"update={task_update_stub_status[:48]}")
            completed_bundle = " | ".join(bundle_parts)[:240]
            for ref in task_result_evidence_refs:
                if ref and ref not in completed_artifacts:
                    completed_artifacts.append(ref)
            for target in task_update_stub_targets:
                if target and target not in completed_artifacts:
                    completed_artifacts.append(target)
        completed = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="completed",
            runner_target=runner_target,
            runtime_summary=completed_runtime_summary,
            worker_result_status=str(provider_invoke_result.get("task_result_status", "")).strip(),
            worker_result_summary=str(provider_invoke_result.get("task_result_summary", "")).strip(),
            worker_gate_status=str(provider_invoke_result.get("task_gate_status", "")).strip(),
            worker_gate_summary=str(provider_invoke_result.get("task_gate_summary", "")).strip(),
            worker_profile_status=str(provider_invoke_result.get("task_profile_status", "")).strip(),
            worker_profile_summary=str(provider_invoke_result.get("task_profile_summary", "")).strip(),
            worker_checklist_status=str(provider_invoke_result.get("task_checklist_status", "")).strip(),
            worker_checklist_summary=str(provider_invoke_result.get("task_checklist_summary", "")).strip(),
            worker_items_summary=str(provider_invoke_result.get("task_items_summary", "")).strip(),
            worker_items=list(provider_invoke_result.get("task_items") or []),
            worker_item_classes_summary=str(provider_invoke_result.get("task_item_classes_summary", "")).strip(),
            worker_item_classes=list(provider_invoke_result.get("task_item_classes") or []),
            worker_records_summary=str(provider_invoke_result.get("task_records_summary", "")).strip(),
            worker_records=list(provider_invoke_result.get("task_records") or []),
            worker_record_rows_summary=str(provider_invoke_result.get("task_record_rows_summary", "")).strip(),
            worker_record_rows=list(provider_invoke_result.get("task_record_rows") or []),
            worker_result_actions=list(provider_invoke_result.get("task_result_actions") or []),
            worker_result_cautions=list(provider_invoke_result.get("task_result_cautions") or []),
            worker_result_evidence_refs=list(provider_invoke_result.get("task_result_evidence_refs") or []),
            worker_update_stub_status=str(provider_invoke_result.get("task_update_stub_status", "")).strip(),
            worker_update_stub_summary=str(provider_invoke_result.get("task_update_stub_summary", "")).strip(),
            worker_update_stub_targets=list(provider_invoke_result.get("task_update_stub_targets") or []),
            evidence_bundle=completed_bundle or "status=completed | outcome=dispatch_flow_returned",
            evidence_artifacts=completed_artifacts,
        )
        if completed:
            on_ticket_update(completed)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_state_write_failed", exc)
    return result


def poll_background_tickets_via_adapters(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    ack_source_command: str = "",
    result_source_command: str = "",
) -> Dict[str, Any]:
    tmux_poll = poll_local_tmux_background_tickets(
        queue_path=queue_path,
        now_iso=now_iso,
    )
    external_poll = poll_external_background_tickets(
        queue_path=queue_path,
        now_iso=now_iso,
        ack_source_command=ack_source_command,
        result_source_command=result_source_command,
    )
    local_background_poll = {
        "changed": False,
        "completed_count": 0,
        "failed_count": 0,
        "completed_ticket_ids": [],
        "failed_ticket_ids": [],
    }
    return {
        "changed": bool(tmux_poll.get("changed")) or bool(external_poll.get("changed")),
        "local_background": local_background_poll,
        "local_tmux": tmux_poll,
        "external": external_poll,
        "completed_count": int(tmux_poll.get("completed_count", 0) or 0) + int(external_poll.get("completed_count", 0) or 0),
        "failed_count": int(tmux_poll.get("failed_count", 0) or 0) + int(external_poll.get("failed_count", 0) or 0),
        "acknowledged_count": int(external_poll.get("acknowledged_count", 0) or 0),
    }
