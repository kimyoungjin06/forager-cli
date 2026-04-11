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
        at=_now_iso(),
        extra={
            "response_text": response_text,
            "decision_snapshot": judge_decision,
        }
        if response_text or judge_decision
        else None,
    )
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
    contract_seed = _worker_contract_seed_for_task(request_id=request_id, label=label, task=task, update_stub=update_stub)
    proposal_payloads = worker_task_contract.derive_worker_artifact_apply_todo_proposals(contract_seed, update_stub)
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
                "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(task.get("background_run_worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(update_stub.get("summary_line", "")).strip() or "-",
                "proposal_summary": proposal_summary or "-",
                "proposal_payloads": proposal_payloads,
                "target_artifacts": list(update_stub.get("target_artifacts") or []),
                "actions": list(update_stub.get("actions") or []),
                "cautions": list(update_stub.get("cautions") or []),
                "evidence_refs": list(update_stub.get("evidence_refs") or []),
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
