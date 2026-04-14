#!/usr/bin/env python3
"""Runtime-scoped dashboard mutation and invoke helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import aoe_tg_action_audit as operator_audit
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_model_provider_adapter as model_provider_adapter
import aoe_tg_todo_state as todo_state
import aoe_tg_worker_task_contract as worker_task_contract
from aoe_tg_action_audit import append_action_audit_row
from aoe_tg_orch_task_handlers import (
    _OFFDESK_JUDGE_SYSTEM,
    _offdesk_judge_prompt,
    _project_alias,
    _runtime_action_link,
)
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as gateway_task_state

from control_dashboard_action_exec_shared import (
    _DASHBOARD_CHAT_ID,
    _load_gateway_main_module,
    _load_dashboard_manager_state,
    _json,
)
from control_dashboard_action_exec_feedback import (
    persist_canonical_writeback_state,
    persist_manual_step_execution_state,
)
from control_dashboard_common import DashboardAppConfig


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _resolve_runtime_entry(*, manager_state: Dict[str, Any], project_ref: str) -> tuple[str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(project_ref or "").strip()
    upper = target.upper()
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if target in {
            str(key).strip(),
            str(entry.get("name", "")).strip(),
            str(entry.get("project_alias", "")).strip(),
            str(entry.get("display_name", "")).strip(),
        } or upper in {
            str(key).strip().upper(),
            str(entry.get("name", "")).strip().upper(),
            str(entry.get("project_alias", "")).strip().upper(),
            str(entry.get("display_name", "")).strip().upper(),
        }:
            return str(key), entry
    raise RuntimeError(f"runtime not found: {project_ref or '-'}")


def _latest_task_for_runtime(entry: Dict[str, Any]) -> Dict[str, Any]:
    tasks = gateway_task_state.ensure_project_tasks(entry)
    if not tasks:
        return {}
    latest: Dict[str, Any] = {}
    latest_at = ""
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        status = runtime_read.normalize_task_status(task.get("status", "pending"))
        if status == "completed":
            continue
        updated_at = str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip()
        if updated_at >= latest_at:
            latest_at = updated_at
            latest = task
            latest.setdefault("request_id", str(request_id).strip())
    if latest:
        return latest
    for request_id, task in tasks.items():
        if isinstance(task, dict):
            latest = task
            latest.setdefault("request_id", str(request_id).strip())
            break
    return latest


def _worker_syncback_ready(task: Dict[str, Any]) -> bool:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    rows_payload = _worker_record_rows_payload(task)
    if list(rows_payload.get("rows") or []):
        return worker_task_contract.worker_task_module_syncback_ready_from_rows(rows_payload)
    records_summary = str(task.get("background_run_worker_records_summary", "")).strip()
    records_kind = ""
    if records_summary not in {"", "-"}:
        records_kind = records_summary.split(" | ", 1)[0].strip()
    raw_records = task.get("background_run_worker_records")
    record_tokens = []
    if isinstance(raw_records, list):
        record_tokens = [str(item).strip() for item in raw_records if str(item).strip()]
    elif isinstance(raw_records, str):
        record_tokens = [str(item).strip() for item in raw_records.split(",") if str(item).strip()]
    if record_tokens:
        return worker_task_contract.worker_task_module_syncback_ready(
            {
                "module_kind": module_kind or ("package" if records_kind == "package_records" else "general"),
                "records_kind": records_kind or ("package_records" if module_kind == "package" else ""),
                "records": record_tokens,
            }
        )
    if records_kind == "package_records":
        return "syncback_record=ready" in records_summary
    return module_kind != "package"


def _worker_record_rows_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower() or "general"
    rows_summary = str(task.get("background_run_worker_record_rows_summary", "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    raw_rows = task.get("background_run_worker_record_rows")
    row_tokens: list[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str):
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    elif rows_summary not in {"", "-"}:
        row_tokens = [str(item).strip() for item in rows_summary.split(" | ")[1:] if str(item).strip()]
    return {
        "module_kind": module_kind,
        "rows_kind": rows_kind or f"{module_kind}_record_rows",
        "rows": row_tokens,
        "summary_line": rows_summary or "-",
    }


def _worker_preflight_rows_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower() or "general"
    rows_summary = str(task.get("background_run_worker_preflight_rows_summary", "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    raw_rows = task.get("background_run_worker_preflight_rows")
    row_tokens: list[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str):
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    if row_tokens or rows_summary not in {"", "-"}:
        return {
            "module_kind": module_kind,
            "rows_kind": rows_kind or f"{module_kind}_preflight_rows",
            "rows": row_tokens,
            "summary_line": rows_summary or "-",
        }
    if module_kind in {"", "-", "general"}:
        return {
            "module_kind": module_kind or "general",
            "rows_kind": "general_preflight_rows",
            "rows": [],
            "summary_line": "-",
        }
    record_rows_payload = _worker_record_rows_payload(task)
    derived = worker_task_contract.derive_worker_task_module_preflight_rows(
        {
            "module_kind": module_kind,
            "module_policy": task.get("background_run_task_contract_policy"),
            "artifact_targets": task.get("background_run_worker_update_stub_targets"),
        },
        {
            "status": task.get("background_run_worker_result_status"),
            "summary": task.get("background_run_worker_result_summary"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        },
        gate={
            "state": task.get("background_run_worker_gate_status"),
            "summary_line": task.get("background_run_worker_gate_summary"),
        },
        profile={
            "state": task.get("background_run_worker_profile_status"),
            "summary_line": task.get("background_run_worker_profile_summary"),
        },
        checklist={
            "state": task.get("background_run_worker_checklist_status"),
            "summary_line": task.get("background_run_worker_checklist_summary"),
        },
        items={
            "module_kind": module_kind,
            "items": (task.get("background_run_worker_items") if isinstance(task.get("background_run_worker_items"), list) else []),
            "summary_line": task.get("background_run_worker_items_summary"),
        },
        item_classes={
            "module_kind": module_kind,
            "classes": (task.get("background_run_worker_item_classes") if isinstance(task.get("background_run_worker_item_classes"), list) else []),
            "summary_line": task.get("background_run_worker_item_classes_summary"),
        },
        records={
            "module_kind": module_kind,
            "records": (task.get("background_run_worker_records") if isinstance(task.get("background_run_worker_records"), list) else []),
            "summary_line": task.get("background_run_worker_records_summary"),
        },
        record_rows=record_rows_payload if list(record_rows_payload.get("rows") or []) else None,
        preflight={
            "module_kind": module_kind,
            "state": task.get("background_run_worker_preflight_status"),
            "summary_line": task.get("background_run_worker_preflight_summary"),
        },
    )
    return {
        "module_kind": module_kind,
        "rows_kind": str(derived.get("rows_kind", "")).strip() or f"{module_kind}_preflight_rows",
        "rows": list(derived.get("rows") or []),
        "summary_line": str(derived.get("summary_line", "")).strip() or "-",
    }


def _worker_apply_ready(task: Dict[str, Any]) -> bool:
    payload = _worker_record_rows_payload(task)
    if list(payload.get("rows") or []):
        return worker_task_contract.worker_task_module_apply_ready(payload)
    return True


def _worker_apply_not_ready_response(
    *,
    spec: Dict[str, object],
    alias: str,
    payload: Dict[str, Any],
    task: Dict[str, Any],
    label: str,
    request_id: str,
    mode: str,
    outcome_kind: str,
) -> Tuple[int, Dict[str, str], bytes]:
    preflight_rows_payload = _worker_preflight_rows_payload(task)
    preflight_rows_detail = str(preflight_rows_payload.get("summary_line", "")).strip()
    row_detail = str(task.get("background_run_worker_record_rows_summary", "")).strip()
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        preflight_rows_payload,
        mode="apply",
    )
    detail = (
        preflight_rows_detail
        or str(task.get("background_run_worker_preflight_summary", "")).strip()
        or row_detail
        or "worker apply gate not ready"
    )
    suggested_action = str(blocker.get("suggested_action", "")).strip().lower()
    next_step = f"/task {label}"
    if suggested_action == "followup":
        next_step = f"/followup {label}"
    elif suggested_action == "judge":
        next_step = f"/orch judge {alias}"
    remediation = str(blocker.get("remediation", "")).strip() or (
        "wait until the module-specific worker gate reports apply-ready rows before promoting or accepting artifact apply"
    )
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": mode,
            "source_command": str(spec.get("command", "")).strip() or f"/task {label} | worker-apply-preview",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": outcome_kind,
                "status": "blocked",
                "reason_code": str(blocker.get("reason_code", "")).strip() or "worker_apply_not_ready",
                "detail": detail,
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "worker_record_rows": row_detail or detail,
            "worker_preflight_rows": preflight_rows_detail or detail,
            "worker_blocker": str(blocker.get("summary_line", "")).strip() or detail,
            "worker_blocked_rows": list(blocker.get("blocked_rows") or []),
            "worker_recommended_action": suggested_action or "task_review",
            "preview": {
                "kind": "worker_apply_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
        },
        status=409,
    )


def _package_syncback_not_ready_response(
    *,
    spec: Dict[str, object],
    alias: str,
    payload: Dict[str, Any],
    latest_task: Dict[str, Any],
    mode: str,
) -> Tuple[int, Dict[str, str], bytes]:
    task_ref = str(latest_task.get("short_id", "")).strip()
    next_step = f"/task {task_ref}" if task_ref else f"/orch status {alias}"
    preflight_detail = str(latest_task.get("background_run_worker_preflight_summary", "")).strip()
    preflight_rows_payload = _worker_preflight_rows_payload(latest_task)
    preflight_rows_detail = str(preflight_rows_payload.get("summary_line", "")).strip()
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        preflight_rows_payload,
        mode="syncback",
    )
    row_detail = str(latest_task.get("background_run_worker_record_rows_summary", "")).strip()
    record_detail = str(latest_task.get("background_run_worker_records_summary", "")).strip()
    detail = preflight_rows_detail or preflight_detail or row_detail or record_detail or "package syncback record pending"
    remediation = str(blocker.get("remediation", "")).strip() or "wait until package preflight reports syncback_ready before accepted syncback"
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": mode,
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback {'preview' if mode == 'safe' else 'apply'}",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": "runtime_syncback_preview" if mode == "safe" else "runtime_syncback_apply",
                "status": "blocked",
                "reason_code": str(blocker.get("reason_code", "")).strip() or "package_syncback_not_ready",
                "detail": detail,
            },
            "preview": {
                "kind": "runtime_syncback_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
            "worker_records": record_detail or detail,
            "worker_record_rows": row_detail or detail,
            "worker_preflight": preflight_detail or detail,
            "worker_preflight_rows": preflight_rows_detail or detail,
            "worker_blocker": str(blocker.get("summary_line", "")).strip() or detail,
            "worker_blocked_rows": list(blocker.get("blocked_rows") or []),
            "worker_recommended_action": str(blocker.get("suggested_action", "")).strip().lower() or "task_review",
        },
        status=409,
    )


def _resolve_task_entry(*, manager_state: Dict[str, Any], task_ref: str) -> tuple[str, Dict[str, Any], str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(task_ref or "").strip()
    if not target:
        raise RuntimeError("task not found: -")
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        task = gateway_task_state.get_task_record(entry, target)
        if not isinstance(task, dict):
            continue
        request_id = gateway_task_state.resolve_task_request_id(entry, target)
        if request_id:
            return str(key), entry, request_id, task
    raise RuntimeError(f"task not found: {target}")


def _save_manager_state(config: DashboardAppConfig, manager_state: Dict[str, Any]) -> None:
    gateway_main = _load_gateway_main_module()
    gateway_main.save_manager_state(config.manager_state_file, manager_state)


def _worker_update_stub_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return worker_task_contract.sanitize_worker_task_update_stub(
        {
            "status": task.get("background_run_worker_update_stub_status"),
            "summary_line": task.get("background_run_worker_update_stub_summary"),
            "target_artifacts": task.get("background_run_worker_update_stub_targets"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        }
    )


def _worker_contract_seed_for_task(*, request_id: str, label: str, task: Dict[str, Any], update_stub: Dict[str, Any]) -> Dict[str, Any]:
    return worker_task_contract.sanitize_worker_task_contract(
        {
            "request_id": request_id,
            "task_id": str(label or "").strip()[:48],
            "task_label": label,
            "status": str(task.get("status", "")).strip() or "-",
            "tf_phase": str(task.get("tf_phase", "")).strip() or "-",
            "pack_profile": "offdesk_execute",
            "objective": str(task.get("prompt", "")).strip() or str(task.get("alias", "")).strip() or label,
            "execution_brief_status": str(task.get("execution_brief_status", "")).strip() or "-",
            "execution_brief_summary": str(task.get("execution_brief_summary", "")).strip() or "-",
            "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
            "followup_brief_summary": str(task.get("followup_brief_summary", "")).strip() or "-",
            "artifact_targets": list(update_stub.get("target_artifacts") or []),
        }
    )


def _worker_apply_preview_payload(
    *,
    alias: str,
    request_id: str,
    label: str,
    task: Dict[str, Any],
    update_stub: Dict[str, Any],
    proposal_ids: list[str],
) -> Dict[str, Any]:
    contract_seed = _worker_contract_seed_for_task(request_id=request_id, label=label, task=task, update_stub=update_stub)
    proposal_payloads = worker_task_contract.derive_worker_artifact_apply_todo_proposals(contract_seed, update_stub)
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, proposal_ids)
    accepted_todo_id = str(task.get("background_run_worker_apply_accept_todo_id", "")).strip()
    next_step = (
        f"/todo {alias} accept {proposal_ids[0]}"
        if proposal_ids
        else (f"/todo {alias}" if accepted_todo_id else f"/task {label} | worker-apply-propose")
    )
    return {
        "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
        "worker_result_summary": str(task.get("background_run_worker_result_summary", "")).strip() or "-",
        "update_stub_summary": str(update_stub.get("summary_line", "")).strip() or "-",
        "proposal_summary": proposal_summary or "-",
        "proposal_ids": proposal_ids,
        "proposal_payloads": proposal_payloads,
        "target_artifacts": list(update_stub.get("target_artifacts") or []),
        "actions": list(update_stub.get("actions") or []),
        "cautions": list(update_stub.get("cautions") or []),
        "evidence_refs": list(update_stub.get("evidence_refs") or []),
        "next_step": next_step,
    }


def _persist_worker_apply_accept_state(
    *,
    entry: Dict[str, Any],
    task: Dict[str, Any],
    request_id: str,
    update_stub: Dict[str, Any],
    preview_payload: Dict[str, Any],
    result: Dict[str, Any],
    accepted_at: str,
) -> None:
    proposals_store, _proposal_seq = todo_state.ensure_todo_proposal_store(entry)
    open_apply_proposal_ids: list[str] = []
    for proposal_id in worker_task_contract.match_worker_update_proposal_ids(
        proposals_store,
        request_id=request_id,
        proposal_payloads=preview_payload.get("proposal_payloads") or [],
    ):
        proposal = todo_state.find_proposal_by_ref(proposals_store, proposal_id)
        if not isinstance(proposal, dict):
            continue
        if todo_state.normalize_proposal_status(proposal.get("status", "open")) != "open":
            continue
        token = str(proposal_id).strip()
        if token and token not in open_apply_proposal_ids:
            open_apply_proposal_ids.append(token)
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, open_apply_proposal_ids)
    apply_accept_summary = worker_task_contract.summarize_worker_artifact_apply_accept_summary(
        proposal_id=result.get("proposal_id"),
        todo_id=result.get("todo_id"),
        target_artifacts=preview_payload.get("target_artifacts") or [],
        accepted_at=accepted_at,
    )
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_summary"] = apply_accept_summary
    task["background_run_worker_apply_accept_proposal_id"] = str(result.get("proposal_id", "")).strip()
    task["background_run_worker_apply_accept_todo_id"] = str(result.get("todo_id", "")).strip()
    task["background_run_worker_apply_accept_at"] = accepted_at
    if open_apply_proposal_ids:
        task["background_run_worker_update_proposal_summary"] = proposal_summary
        task["background_run_worker_update_proposal_ids"] = list(open_apply_proposal_ids)
    else:
        task.pop("background_run_worker_update_proposal_summary", None)
        task.pop("background_run_worker_update_proposal_ids", None)
    task["updated_at"] = accepted_at
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_apply_accept_status"] = "applied"
        task["result"]["background_run_worker_apply_accept_summary"] = apply_accept_summary
        task["result"]["background_run_worker_apply_accept_proposal_id"] = str(result.get("proposal_id", "")).strip()
        task["result"]["background_run_worker_apply_accept_todo_id"] = str(result.get("todo_id", "")).strip()
        task["result"]["background_run_worker_apply_accept_at"] = accepted_at
        if open_apply_proposal_ids:
            task["result"]["background_run_worker_update_proposal_summary"] = proposal_summary
            task["result"]["background_run_worker_update_proposal_ids"] = list(open_apply_proposal_ids)
        else:
            task["result"].pop("background_run_worker_update_proposal_summary", None)
            task["result"].pop("background_run_worker_update_proposal_ids", None)


def _syncback_preview_payload(*, alias: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    updates = []
    for idx, new_line in list(plan.get("updates") or [])[:4]:
        updates.append(f"L{int(idx) + 1}: {str(new_line).strip()[:180]}")
    append_lines = [str(line).strip()[:180] for line in list(plan.get("append_lines") or [])[:4] if str(line).strip()]
    return {
        "kind": "runtime_syncback_preview",
        "project_alias": alias,
        "target_path": str(plan.get("path", "")).strip() or "-",
        "done_count": int(plan.get("done_count", 0) or 0),
        "reopen_count": int(plan.get("reopen_count", 0) or 0),
        "append_count": int(plan.get("append_count", 0) or 0),
        "blocked_count": int(plan.get("blocked_count", 0) or 0),
        "updates": updates,
        "append_lines": append_lines,
        "next_step": f"/todo {alias} syncback apply",
        "runtime_path": _runtime_action_link(alias),
    }


def _summarize_worker_syncback_apply(
    *,
    todo_id: str,
    path: str,
    line_count: int,
    append_count: int,
    done_count: int,
    reopen_count: int,
    blocked_count: int,
    applied_at: str,
) -> str:
    path_token = Path(path).name if str(path).strip() else "-"
    todo_token = str(todo_id or "").strip() or "-"
    return (
        "state=applied | todo={todo} | path={path} | lines={lines} | "
        "done={done} reopen={reopen} append={append} blocked={blocked} | at={at}"
    ).format(
        todo=todo_token,
        path=path_token,
        lines=max(0, int(line_count or 0)),
        done=max(0, int(done_count or 0)),
        reopen=max(0, int(reopen_count or 0)),
        append=max(0, int(append_count or 0)),
        blocked=max(0, int(blocked_count or 0)),
        at=str(applied_at or "").strip() or "-",
    )


def _persist_worker_syncback_apply_state(
    *,
    task: Dict[str, Any],
    result: Dict[str, Any],
    preview: Dict[str, Any],
    applied_at: str,
) -> None:
    summary = _summarize_worker_syncback_apply(
        todo_id=str(task.get("background_run_worker_apply_accept_todo_id", "")).strip(),
        path=str(result.get("path", "")).strip(),
        line_count=int(result.get("line_count", 0) or 0),
        append_count=int(preview.get("append_count", 0) or 0),
        done_count=int(preview.get("done_count", 0) or 0),
        reopen_count=int(preview.get("reopen_count", 0) or 0),
        blocked_count=int(preview.get("blocked_count", 0) or 0),
        applied_at=applied_at,
    )
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_worker_syncback_summary"] = summary
    task["background_run_worker_syncback_at"] = applied_at
    persist_canonical_writeback_state(
        task,
        headline="Syncback Apply | executed",
        state="executed",
        next_step=f"/sync preview {str(preview.get('project_alias', '')).strip() or '-'} 24h",
        at=applied_at,
        path=str(result.get("path", "")).strip(),
        line_count=int(result.get("line_count", 0) or 0),
        done_count=int(preview.get("done_count", 0) or 0),
        reopen_count=int(preview.get("reopen_count", 0) or 0),
        append_count=int(preview.get("append_count", 0) or 0),
        blocked_count=int(preview.get("blocked_count", 0) or 0),
    )
    task["updated_at"] = applied_at
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_syncback_status"] = "applied"
        task["result"]["background_run_worker_syncback_summary"] = summary
        task["result"]["background_run_worker_syncback_at"] = applied_at


def _execute_runtime_syncback_preview_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    latest_task = _latest_task_for_runtime(entry)
    if (
        isinstance(latest_task, dict)
        and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied"
        and not _worker_syncback_ready(latest_task)
    ):
        return _package_syncback_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            latest_task=latest_task,
            mode="safe",
        )
    try:
        plan = todo_state.preview_syncback_plan(entry)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback preview",
                "payload": payload,
                "next_step": f"/todo {alias}",
                "remediation": "restore canonical TODO.md before previewing accepted artifact syncback",
                "outcome": {
                    "kind": "runtime_syncback_preview",
                    "status": "blocked",
                    "reason_code": "syncback_preview_failed",
                    "detail": str(exc).strip() or "-",
                },
                "preview": {
                    "kind": "runtime_syncback_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    preview = _syncback_preview_payload(alias=alias, plan=plan)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback preview",
            "payload": payload,
            "next_step": preview["next_step"],
            "remediation": "inspect the canonical TODO diff before applying accepted artifact syncback",
            "outcome": {
                "kind": "runtime_syncback_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": (
                    "done={done} reopen={reopen} append={append} blocked={blocked}".format(
                        done=preview["done_count"],
                        reopen=preview["reopen_count"],
                        append=preview["append_count"],
                        blocked=preview["blocked_count"],
                    )
                ),
            },
            "preview": preview,
        },
        status=200,
    )


def _execute_runtime_syncback_apply_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    latest_task = _latest_task_for_runtime(entry)
    if (
        isinstance(latest_task, dict)
        and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied"
        and not _worker_syncback_ready(latest_task)
    ):
        return _package_syncback_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            latest_task=latest_task,
            mode="phase2",
        )
    try:
        plan = todo_state.preview_syncback_plan(entry)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback apply",
                "payload": payload,
                "next_step": f"/todo {alias} syncback preview",
                "remediation": "inspect canonical TODO syncback preview before applying writeback again",
                "outcome": {
                    "kind": "runtime_syncback_apply",
                    "status": "blocked",
                    "reason_code": "syncback_preview_failed",
                    "detail": str(exc).strip() or "-",
                },
                "preview": {
                    "kind": "runtime_syncback_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    result = todo_state.apply_syncback_plan(plan)
    preview = _syncback_preview_payload(alias=alias, plan=plan)
    applied_at = _now_iso()
    if isinstance(latest_task, dict) and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied":
        _persist_worker_syncback_apply_state(
            task=latest_task,
            result=result,
            preview=preview,
            applied_at=applied_at,
        )
        _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback apply",
            "payload": payload,
            "next_step": f"/sync preview {alias} 24h",
            "remediation": "verify canonical TODO drift is cleared before applying another accepted artifact syncback",
            "outcome": {
                "kind": "runtime_syncback_apply",
                "status": "executed",
                "reason_code": "completed",
                "detail": (
                    "path={path} lines={lines} done={done} reopen={reopen} append={append} blocked={blocked}".format(
                        path=str(result.get("path", "")).strip() or "-",
                        lines=int(result.get("line_count", 0) or 0),
                        done=preview["done_count"],
                        reopen=preview["reopen_count"],
                        append=preview["append_count"],
                        blocked=preview["blocked_count"],
                    )
                ),
            },
            "preview": preview,
            "result": {
                "path": str(result.get("path", "")).strip() or "-",
                "line_count": int(result.get("line_count", 0) or 0),
            },
            "worker_syncback": (
                str((latest_task or {}).get("background_run_worker_syncback_summary", "")).strip() or "-"
            ),
        },
        status=200,
    )


def _execute_runtime_judge_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    team_dir_raw = str(entry.get("team_dir", "")).strip()
    if not team_dir_raw:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
                "payload": payload,
                "next_step": f"/orch status {alias}",
                "remediation": "restore the runtime team_dir before invoking off-desk judge again",
                "outcome": {
                    "kind": "offdesk_judge",
                    "status": "blocked",
                    "reason_code": "team_dir_missing",
                    "detail": "team_dir missing",
                },
                "preview": {
                    "kind": "runtime_judge",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    team_dir = Path(team_dir_raw).expanduser().resolve()
    latest_task = _latest_task_for_runtime(entry)
    if not latest_task:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
                "payload": payload,
                "next_step": f"/orch status {alias}",
                "remediation": "create or recover a task before invoking off-desk judge again",
                "outcome": {
                    "kind": "offdesk_judge",
                    "status": "blocked",
                    "reason_code": "no_task_available",
                    "detail": "no task available for judge review",
                },
                "preview": {
                    "kind": "runtime_judge",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=latest_task,
        pack_profile_override="review",
    )
    result = model_provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry=entry,
        task=latest_task,
        prompt=_offdesk_judge_prompt(entry, latest_task, team_dir),
        system=_OFFDESK_JUDGE_SYSTEM,
        pack_profile_override="review",
        timeout_sec=120.0,
    )
    ok = bool(result.get("ok"))
    executed = bool(result.get("executed"))
    summary = str(result.get("summary", "-")).strip() or "-"
    response_text = str(result.get("response_text", "")).strip()
    reason_code = str(result.get("reason_code", "")).strip() or ("ok" if ok else "not_executed")
    judge_decision = operator_audit.normalize_offdesk_judge_decision(response_text)
    audit_team_dir = Path(str(config.team_dir or team_dir)).expanduser().resolve()
    recorded_at = _now_iso()
    append_action_audit_row(
        audit_team_dir,
        headline=f"Offdesk Judge | {'executed' if ok else 'blocked'}",
        status="executed" if ok else "blocked",
        outcome_kind="offdesk_judge",
        outcome_status="executed" if ok else "blocked",
        outcome_reason_code=reason_code,
        outcome_detail=summary,
        next_step=f"/offdesk review {alias}",
        remediation="inspect the judge response together with execution brief, followup brief, and runtime status before acting",
        source_command=f"/orch judge {alias}",
        link_label="runtime detail",
        link_href=_runtime_action_link(alias),
        at=recorded_at,
        extra={
            "response_text": response_text,
            "decision_snapshot": judge_decision,
        }
        if response_text or judge_decision
        else None,
    )
    if isinstance(latest_task, dict):
        persist_manual_step_execution_state(
            latest_task,
            manual_kind="manual_review",
            source_command=str(spec.get("command", "")).strip() or f"/orch judge {alias}",
            state="executed" if ok else "blocked",
            next_step=f"/offdesk review {alias}",
            at=recorded_at,
        )
        _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": ok,
            "implemented": True,
            "executed": executed,
            "status": "executed" if ok else "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
            "payload": payload,
            "binding": str(binding.get("summary", "")).strip() or "-",
            "summary": summary,
            "response": response_text or "-",
            "next_step": f"/offdesk review {alias}",
            "remediation": "inspect the judge response together with execution brief, followup brief, and runtime status before acting",
            "outcome": {
                "kind": "offdesk_judge",
                "status": "executed" if ok else "blocked",
                "reason_code": reason_code,
                "detail": summary,
            },
            "task": {
                "request_id": str(latest_task.get("request_id", "")).strip() or "-",
                "label": str(latest_task.get("short_id", "")).strip() or str(latest_task.get("alias", "")).strip() or "-",
                "detail_path": f"/control/tasks/by-request/{str(latest_task.get('request_id', '')).strip()}",
            },
            "preview": {
                "kind": "runtime_judge",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
            "latest_judge_decision": judge_decision,
        },
        status=200 if ok else 409,
    )


def _execute_worker_update_preview_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    try:
        key, entry, request_id, task = _resolve_task_entry(manager_state=manager_state, task_ref=task_ref)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry the preview with an existing task ref",
                "outcome": {
                    "kind": "worker_update_preview",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "phase2",
            outcome_kind="worker_apply_propose",
        )
    update_stub = _worker_update_stub_for_task(task)
    proposal_ids = [
        str(item).strip()
        for item in (task.get("background_run_worker_update_proposal_ids") or [])
        if str(item).strip()
    ]
    proposal_summary = worker_task_contract.summarize_worker_update_proposal_summary(update_stub, proposal_ids)
    operator_summary = worker_task_contract.summarize_worker_update_operator_summary(update_stub, proposal_ids)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current execution rails before previewing an artifact update",
                "outcome": {
                    "kind": "worker_update_preview",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
                "preview": {
                    "kind": "worker_update_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    next_step = f"/todo {alias} accept {proposal_ids[0]}" if proposal_ids else f"/task {label}"
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": "inspect the proposed target artifacts and cautions before accepting the worker proposal or mutating any runtime todo",
            "outcome": {
                "kind": "worker_update_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": operator_summary or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "preview": {
                "kind": "worker_update_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(task.get("background_run_worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(update_stub.get("summary_line", "")).strip() or "-",
                "operator_summary": operator_summary or "-",
                "proposal_summary": proposal_summary or "-",
                "proposal_ids": proposal_ids,
                "target_artifacts": list(update_stub.get("target_artifacts") or []),
                "actions": list(update_stub.get("actions") or []),
                "cautions": list(update_stub.get("cautions") or []),
                "evidence_refs": list(update_stub.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_worker_apply_propose_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    try:
        key, entry, request_id, task = _resolve_task_entry(manager_state=manager_state, task_ref=task_ref)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry the apply proposal action with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "safe",
            outcome_kind="worker_apply_preview",
        )
    update_stub = _worker_update_stub_for_task(task)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current worker update preview before proposing artifact apply steps",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
                "preview": {
                    "kind": "worker_apply_propose",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=[],
    )
    proposal_payloads = list(preview_payload.get("proposal_payloads") or [])
    if not proposal_payloads:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "inspect the worker update preview and target artifacts before trying to propose artifact apply steps again",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "proposal_payload_missing",
                    "detail": "no artifact apply proposal payloads derived",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    merge_result = todo_state.merge_todo_proposals(
        entry=entry,
        request_id=request_id,
        task=task,
        source_todo_id=str(task.get("source_todo_id", "")).strip(),
        proposals_data=proposal_payloads,
        now_iso=_now_iso,
    )
    proposals_store, _proposal_seq = todo_state.ensure_todo_proposal_store(entry)
    proposal_ids = worker_task_contract.match_worker_update_proposal_ids(
        proposals_store,
        request_id=request_id,
        proposal_payloads=proposal_payloads,
    )
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, proposal_ids)
    task["background_run_worker_update_proposal_summary"] = proposal_summary
    task["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_update_proposal_summary"] = proposal_summary
        task["result"]["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
    _save_manager_state(config, manager_state)
    created_ids = [str(item).strip() for item in (merge_result.get("created_ids") or []) if str(item).strip()]
    first_id = created_ids[0] if created_ids else (proposal_ids[0] if proposal_ids else "")
    next_step = f"/todo {alias} accept {first_id}" if first_id else f"/todo {alias} proposals"
    outcome_detail = proposal_summary if proposal_summary not in {"", "-"} else (str(merge_result.get("created_count", 0)) + " proposal(s)")
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": "inspect the apply-oriented worker proposal before accepting it into the runtime todo queue",
            "outcome": {
                "kind": "worker_apply_propose",
                "status": "executed",
                "reason_code": "completed",
                "detail": outcome_detail,
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "proposal": {
                "proposal_ids": proposal_ids,
                "created_ids": created_ids,
                "created_count": int(merge_result.get("created_count", 0) or 0),
                "duplicate_count": int(merge_result.get("duplicate_count", 0) or 0),
                "summary": proposal_summary or "-",
            },
            "preview": {
                "kind": "worker_apply_propose",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": proposal_summary or "-",
                "proposal_payloads": proposal_payloads,
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_worker_apply_preview_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    try:
        key, entry, request_id, task = _resolve_task_entry(manager_state=manager_state, task_ref=task_ref)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry the artifact-apply preview with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_preview",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "phase2",
            outcome_kind="worker_apply_accept",
        )
    update_stub = _worker_update_stub_for_task(task)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current worker update preview before previewing artifact apply",
                "outcome": {
                    "kind": "worker_apply_preview",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    proposal_ids = [
        str(item).strip()
        for item in (task.get("background_run_worker_update_proposal_ids") or [])
        if str(item).strip()
    ]
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=proposal_ids,
    )
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": str(preview_payload.get("next_step", "")).strip() or f"/task {label}",
            "remediation": "inspect the artifact targets and proposal payloads before promoting an artifact-apply proposal into the runtime todo queue",
            "outcome": {
                "kind": "worker_apply_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": str(preview_payload.get("proposal_summary", "")).strip() or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "preview": {
                "kind": "worker_apply_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": str(preview_payload.get("proposal_summary", "")).strip() or "-",
                "proposal_ids": list(preview_payload.get("proposal_ids") or []),
                "proposal_payloads": list(preview_payload.get("proposal_payloads") or []),
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_worker_apply_accept_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    proposal_ref = str(payload.get("proposal_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    try:
        key, entry, request_id, task = _resolve_task_entry(manager_state=manager_state, task_ref=task_ref)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry artifact apply with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    update_stub = _worker_update_stub_for_task(task)
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=[str(item).strip() for item in (task.get("background_run_worker_update_proposal_ids") or []) if str(item).strip()],
    )
    proposals, _seq = todo_state.ensure_todo_proposal_store(entry)
    proposal = todo_state.find_proposal_by_ref(proposals, proposal_ref)
    if proposal is None:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label} | worker-apply-preview",
                "remediation": "refresh the artifact-apply preview and retry with an open proposal id",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_missing",
                    "detail": f"proposal not found: {proposal_ref or '-'}",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=404,
        )
    proposal_summary = str(proposal.get("summary", "")).strip()
    task_apply_summary = str(task.get("background_run_worker_update_proposal_summary", "")).strip()
    if "apply worker artifact update" not in proposal_summary.lower() and "apply_proposals=" not in task_apply_summary:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an artifact-apply proposal or re-run worker apply propose before accepting it",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_apply",
                    "detail": proposal_summary or "-",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    if todo_state.normalize_proposal_status(proposal.get("status")) != "open":
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an open artifact-apply proposal before accepting it",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_open",
                    "detail": f"proposal is not open: {str(proposal.get('id', '')).strip() or proposal_ref or '-'}",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    accepted_at = _now_iso()
    result = todo_state.accept_todo_proposal(
        entry=entry,
        proposal=proposal,
        actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
        now=accepted_at,
    )
    _persist_worker_apply_accept_state(
        entry=entry,
        task=task,
        request_id=request_id,
        update_stub=update_stub,
        preview_payload=preview_payload,
        result=result,
        accepted_at=accepted_at,
    )
    _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": f"/todo {alias}",
            "remediation": "inspect the promoted artifact-apply todo row and syncback posture before applying another worker artifact update",
            "outcome": {
                "kind": "worker_apply_accept",
                "status": "executed",
                "reason_code": "completed",
                "detail": str(result.get("summary", "")).strip() or proposal_summary or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "proposal": {
                "proposal_id": str(result.get("proposal_id", "")).strip() or str(proposal.get("id", "")).strip() or "-",
                "summary": str(result.get("summary", "")).strip() or proposal_summary or "-",
                "created_new": bool(result.get("created_new", False)),
                "todo_id": str(result.get("todo_id", "")).strip() or "-",
                "reason": str(result.get("reason", "")).strip() or "-",
            },
            "preview": {
                "kind": "worker_apply_accept",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": str(preview_payload.get("proposal_summary", "")).strip() or "-",
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_todo_proposal_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
    reject: bool = False,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    proposal_ref = str(payload.get("proposal_ref", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    proposals, _seq = todo_state.ensure_todo_proposal_store(entry)
    proposal = todo_state.find_proposal_by_ref(proposals, proposal_ref)
    if proposal is None:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "refresh the proposal inbox and re-run the action with an open proposal id",
                "outcome": {
                    "kind": "todo_proposal_reject" if reject else "todo_proposal_accept",
                    "status": "blocked",
                    "reason_code": "proposal_missing",
                    "detail": f"proposal not found: {proposal_ref or '-'}",
                },
                "preview": {
                    "kind": "todo_proposal",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=404,
        )
    if todo_state.normalize_proposal_status(proposal.get("status")) != "open":
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an open proposal or inspect the existing todo queue before applying another worker update",
                "outcome": {
                    "kind": "todo_proposal_reject" if reject else "todo_proposal_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_open",
                    "detail": f"proposal is not open: {str(proposal.get('id', '')).strip() or proposal_ref or '-'}",
                },
                "preview": {
                    "kind": "todo_proposal",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    now = _now_iso()
    if reject:
        result = todo_state.reject_todo_proposal(
            entry=entry,
            proposal=proposal,
            actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
            now=now,
            reason=reason,
        )
        outcome_kind = "todo_proposal_reject"
        next_step = f"/todo {alias} proposals"
        remediation = "inspect remaining open proposals before rejecting another worker suggestion"
    else:
        result = todo_state.accept_todo_proposal(
            entry=entry,
            proposal=proposal,
            actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
            now=now,
        )
        outcome_kind = "todo_proposal_accept"
        next_step = f"/todo {alias}"
        remediation = "inspect the promoted todo row and syncback posture before applying another worker proposal"
    _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": outcome_kind,
                "status": "executed",
                "reason_code": "completed",
                "detail": str(result.get("summary", "")).strip() or "-",
            },
            "proposal": {
                "proposal_id": str(result.get("proposal_id", "")).strip() or str(proposal.get("id", "")).strip() or "-",
                "summary": str(result.get("summary", "")).strip() or str(proposal.get("summary", "")).strip() or "-",
                "created_new": bool(result.get("created_new", False)),
                "todo_id": str(result.get("todo_id", "")).strip() or "-",
                "reason": str(result.get("reason", "")).strip() or "-",
            },
            "preview": {
                "kind": "todo_proposal",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
        },
        status=200,
    )
