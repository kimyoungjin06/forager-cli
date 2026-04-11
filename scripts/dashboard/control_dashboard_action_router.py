#!/usr/bin/env python3
"""HTTP action routing and previews for the Control Dashboard."""

from __future__ import annotations

import json
from typing import Dict, Tuple
from urllib.parse import urlparse

import aoe_tg_operator_action_contract as operator_action_contract

from control_dashboard_action_exec import (
    _execute_auto_recover_action,
    _execute_background_queue_clean_action,
    _execute_followup_action,
    _execute_runtime_judge_action,
    _execute_retry_action,
    _execute_todo_proposal_action,
    _execute_worker_update_preview_action,
)
from control_dashboard_audit import _with_action_audit
from control_dashboard_common import (
    ACTION_PATHS,
    DashboardAppConfig,
    _bad_request,
    _json,
    _method_not_allowed,
    _normalize_lane_ids,
    _not_found_json,
    _unsupported_media_type,
)
from control_dashboard_state import load_dashboard_runtime_details, load_task_detail



def _action_spec_for_request(path: str, payload: Dict[str, object]) -> Dict[str, object]:
    if path in {"/control/actions/task/retry", "/control/actions/task/replan"}:
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        lane_ids = _normalize_lane_ids(payload.get("lane_ids"))
        auto_route_apply_raw = payload.get("auto_route_apply", False)
        if not isinstance(auto_route_apply_raw, bool):
            raise ValueError("auto_route_apply must be a boolean")
        command = f"{'/replan' if path.endswith('/replan') else '/retry'} {task_ref}"
        if lane_ids:
            command += " lane " + ",".join(lane_ids)
        spec = operator_action_contract.http_action_spec(command)
        if spec is None:
            raise ValueError("unsupported retry/replan action contract")
        spec["payload"] = dict(spec.get("payload") or {})
        if auto_route_apply_raw:
            spec["payload"]["auto_route_apply"] = True
        return spec

    if path == "/control/actions/task/followup":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        lane_ids = _normalize_lane_ids(payload.get("lane_ids"))
        command = f"/followup {task_ref}"
        if lane_ids:
            command += " lane " + ",".join(lane_ids)
        spec = operator_action_contract.http_action_spec(command)
        if spec is None:
            raise ValueError("unsupported followup action contract")
        return spec

    if path == "/control/actions/task/followup-execute":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        lane_ids = _normalize_lane_ids(payload.get("lane_ids"))
        command = f"/followup-exec {task_ref}"
        if lane_ids:
            command += " lane " + ",".join(lane_ids)
        spec = operator_action_contract.http_action_spec(command)
        if spec is None:
            raise ValueError("unsupported followup execute action contract")
        return spec

    if path == "/control/actions/task/worker-update-preview":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/task {task_ref} | worker-update-preview",
            "note": "inspect bounded worker update before accepting any proposal",
            "payload": {"task_ref": task_ref},
        }

    if path == "/control/actions/runtime/judge":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        spec = operator_action_contract.http_action_spec(f"/orch judge {project_ref}")
        if spec is None:
            raise ValueError("unsupported runtime judge action contract")
        return spec

    if path in {"/control/actions/runtime/todo-accept", "/control/actions/runtime/todo-reject"}:
        project_ref = str(payload.get("project_ref", "")).strip()
        proposal_ref = str(payload.get("proposal_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        if not proposal_ref:
            raise ValueError("proposal_ref is required")
        reason = str(payload.get("reason", "")).strip()
        command = f"/todo {project_ref} {'reject' if path.endswith('reject') else 'accept'} {proposal_ref}"
        if reason and path.endswith("reject"):
            command += f" {reason}"
        spec = operator_action_contract.http_action_spec(command)
        if spec is None:
            raise ValueError("unsupported todo proposal action contract")
        return spec

    if path == "/control/actions/runtime/sync-preview":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        window = str(payload.get("window", "24h")).strip() or "24h"
        spec = operator_action_contract.http_action_spec(f"/sync preview {project_ref} {window}")
        if spec is None:
            raise ValueError("unsupported sync preview action contract")
        return spec

    if path == "/control/actions/runtime/background-queue-clean":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        spec = operator_action_contract.http_action_spec(f"/orch bgq-clean {project_ref}")
        if spec is None:
            raise ValueError("unsupported background queue cleanup action contract")
        return spec

    if path == "/control/actions/control/auto-recover":
        force_raw = payload.get("force", False)
        if isinstance(force_raw, bool):
            force = force_raw
        else:
            raise ValueError("force must be a boolean")
        spec = operator_action_contract.http_action_spec("/auto recover force" if force else "/auto recover")
        if spec is None:
            raise ValueError("unsupported auto recover action contract")
        return spec

    raise ValueError("unknown action path")



def _preview_followup_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    detail = load_task_detail(
        control_root=config.control_root,
        team_dir=config.team_dir,
        manager_state_file=config.manager_state_file,
        request_id=task_ref,
    )
    if detail is None:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")
    return _json(
        {
            "ok": True,
            "implemented": True,
            "status": "preview",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "next_step": (detail.command_hints[0] if detail.command_hints else f"/task {detail.label}"),
            "remediation": "inspect the follow-up reason and choose the matching task or backlog drill-down before mutating anything",
            "preview": {
                "kind": "task_followup",
                "project_alias": detail.project_alias,
                "request_id": detail.request_id,
                "label": detail.label,
                "tf_phase": detail.tf_phase,
                "followup_summary": detail.followup_summary or "-",
                "completion_followup_when": detail.completion_followup_when or "-",
                "command_hints": list(detail.command_hints),
                "phase2_action_hints": list(detail.phase2_action_hints),
                "detail_path": f"/control/tasks/by-request/{detail.request_id}",
                "runtime_path": f"/control/runtimes/{detail.project_alias}",
            },
        },
        status=200,
    )




def _preview_sync_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    snapshot, runtime_details, _manager_state = load_dashboard_runtime_details(
        control_root=config.control_root,
        team_dir=config.team_dir,
        manager_state_file=config.manager_state_file,
    )
    token = project_ref.lower()
    detail = next(
        (
            row
            for row in runtime_details
            if project_ref
            and token
            in {
                str(row.project_alias).strip().lower(),
                str(row.project_key).strip().lower(),
                str(row.project_label).strip().lower(),
            }
        ),
        None,
    )
    if detail is None:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"runtime not found: {project_ref}")
    return _json(
        {
            "ok": True,
            "implemented": True,
            "status": "preview",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "snapshot_taken_at": snapshot.snapshot_taken_at,
            "next_step": (detail.runtime_command_hints[0] if detail.runtime_command_hints else f"/monitor {detail.project_alias}"),
            "remediation": "inspect sync drift and provider pressure first, then decide whether runtime sync is worth executing",
            "preview": {
                "kind": "runtime_sync_preview",
                "project_alias": detail.project_alias,
                "project_label": detail.project_label,
                "sync_summary": detail.sync_summary or "-",
                "queue_summary": detail.queue_summary or "-",
                "provider_pressure_summary": detail.provider_pressure_summary or "-",
                "next_focus": detail.next_focus or "-",
                "runtime_command_hints": list(detail.runtime_command_hints),
                "runtime_phase2_action_hints": list(detail.runtime_phase2_action_hints),
                "runtime_path": detail.runtime_path,
            },
        },
        status=200,
    )



def build_dashboard_action_response(
    raw_path: str,
    *,
    body: bytes,
    content_type: str,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    parsed = urlparse(raw_path)
    path = parsed.path or "/control"
    if path not in ACTION_PATHS:
        from control_dashboard import _is_known_dashboard_get_route  # local import to avoid cycle at import time

        if _is_known_dashboard_get_route(path):
            return _method_not_allowed(path=path, allowed="GET")
        return _not_found_json(path=path, message=f"unknown route: {path}")
    if "application/json" not in str(content_type or "").lower():
        return _unsupported_media_type(path=path, content_type=content_type)
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except Exception as exc:
        return _bad_request("invalid json body", path=path, details=str(exc))
    if not isinstance(payload, dict):
        return _bad_request("json body must be an object", path=path)
    try:
        spec = _action_spec_for_request(path, payload)
    except ValueError as exc:
        return _bad_request(str(exc), path=path)

    if path == "/control/actions/task/followup":
        return _with_action_audit(_preview_followup_action(spec, config=config), config=config)

    if path == "/control/actions/task/followup-execute":
        return _with_action_audit(_execute_followup_action(spec, config=config), config=config)

    if path == "/control/actions/task/worker-update-preview":
        return _with_action_audit(_execute_worker_update_preview_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/sync-preview":
        return _with_action_audit(_preview_sync_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/background-queue-clean":
        return _with_action_audit(_execute_background_queue_clean_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/judge":
        return _with_action_audit(_execute_runtime_judge_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/todo-accept":
        return _with_action_audit(_execute_todo_proposal_action(spec, config=config, reject=False), config=config)

    if path == "/control/actions/runtime/todo-reject":
        return _with_action_audit(_execute_todo_proposal_action(spec, config=config, reject=True), config=config)

    if path in {"/control/actions/task/retry", "/control/actions/task/replan"}:
        return _with_action_audit(_execute_retry_action(spec, config=config), config=config)

    if path == "/control/actions/control/auto-recover":
        return _with_action_audit(_execute_auto_recover_action(spec, config=config), config=config)

    return _with_action_audit(
        _json(
            {
                "ok": False,
                "implemented": False,
                "status": "not_implemented",
                "method": "POST",
                "path": path,
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": spec.get("payload", {}),
                "note": spec.get("note", "-"),
            },
            status=501,
        ),
        config=config,
    )
