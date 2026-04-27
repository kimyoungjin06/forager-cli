#!/usr/bin/env python3
"""HTTP action routing and previews for the Control Dashboard."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Dict, Tuple
from urllib.parse import urlparse

import aoe_tg_operator_action_contract as operator_action_contract
import aoe_tg_task_state as gateway_task_state

from control_dashboard_action_exec import (
    _execute_analysis_review_action,
    _execute_auto_recover_action,
    _execute_background_queue_clean_action,
    _execute_general_subagent_support_action,
    _execute_operator_preference_candidate_action,
    _preview_background_queue_clean_action,
    _preview_server_guard_pressure_action,
    _execute_chat_send_action,
    _execute_chat_session_select_task_action,
    _execute_chat_session_update_action,
    _execute_followup_action,
    _execute_operator_preference_rule_action,
    _execute_runtime_judge_action,
    _execute_runtime_syncback_apply_action,
    _execute_runtime_syncback_preview_action,
    _execute_retry_action,
    _execute_operator_preference_decision_action,
    _execute_todo_proposal_action,
    _execute_worker_apply_accept_action,
    _execute_worker_apply_preview_action,
    _execute_worker_apply_propose_action,
    _execute_worker_update_preview_action,
)
from control_dashboard_action_exec_feedback import persist_manual_step_execution_state
from control_dashboard_action_exec_shared import _load_dashboard_manager_state, _load_gateway_main_module
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
    if path == "/control/actions/chat/send":
        chat_id = str(payload.get("chat_id", "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        text = str(payload.get("text", "")).strip()
        if not text:
            raise ValueError("text is required")
        mode = str(payload.get("mode", "")).strip().lower() or "raw"
        if mode not in {"raw", "direct", "dispatch", "room_post", "room_use"}:
            raise ValueError("mode must be one of raw, direct, dispatch, room_post, room_use")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"chat-send:{mode}:{chat_id}",
            "note": "send a gateway command through the selected chat session",
            "payload": {"chat_id": chat_id, "text": text, "mode": mode},
        }

    if path == "/control/actions/chat/session-update":
        chat_id = str(payload.get("chat_id", "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        default_mode = str(payload.get("default_mode", "")).strip().lower()
        pending_mode = str(payload.get("pending_mode", "")).strip().lower()
        room = str(payload.get("room", "")).strip()
        lang = str(payload.get("lang", "")).strip().lower()
        report_level = str(payload.get("report_level", "")).strip().lower()
        focus_badge = str(payload.get("focus_badge", "")).strip()
        server_guard_preset_label = str(payload.get("server_guard_preset_label", "")).strip()
        server_guard_pressure_kind = str(payload.get("server_guard_pressure_kind", "")).strip().lower()
        next_step = str(payload.get("next_step", "")).strip()
        remediation = str(payload.get("remediation", "")).strip()
        if default_mode not in {"", "direct", "dispatch"}:
            raise ValueError("default_mode must be one of direct, dispatch, or blank")
        if pending_mode not in {"", "direct", "dispatch"}:
            raise ValueError("pending_mode must be one of direct, dispatch, or blank")
        if lang not in {"", "ko", "en"}:
            raise ValueError("lang must be one of ko, en, or blank")
        if report_level not in {"", "short", "normal", "long"}:
            raise ValueError("report_level must be one of short, normal, long, or blank")
        if focus_badge not in {"", "server-guard"}:
            raise ValueError("focus_badge must be blank or server-guard")
        if server_guard_pressure_kind not in {"", "codex", "python", "tmux", "process"}:
            raise ValueError("server_guard_pressure_kind must be one of codex, python, tmux, process, or blank")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"chat-session-update:{chat_id}",
            "note": "update default mode, pending mode, room, and reporting defaults for a chat session",
            "payload": {
                "chat_id": chat_id,
                "default_mode": default_mode,
                "pending_mode": pending_mode,
                "room": room,
                "lang": lang,
                "report_level": report_level,
                "focus_badge": focus_badge,
                "server_guard_preset_label": server_guard_preset_label,
                "server_guard_pressure_kind": server_guard_pressure_kind,
                "next_step": next_step,
                "remediation": remediation,
            },
        }

    if path == "/control/actions/chat/session-select-task":
        chat_id = str(payload.get("chat_id", "")).strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        project_ref = str(payload.get("project_ref", "")).strip()
        task_ref = str(payload.get("task_ref", "")).strip()
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"chat-session-select-task:{chat_id}",
            "note": "pin or clear the selected task ref for a chat session and runtime",
            "payload": {
                "chat_id": chat_id,
                "project_ref": project_ref,
                "task_ref": task_ref,
            },
        }

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

    if path == "/control/actions/task/subagent-support-run":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/task {task_ref} | general-research-support",
            "note": "materialize bounded general_research evidence without changing dispatch or apply state",
            "payload": {"task_ref": task_ref},
        }

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

    if path in {"/control/actions/task/task-review", "/control/actions/task/analysis-review"}:
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        review_kind = str(payload.get("review_kind", "")).strip().lower() or "task_review"
        command_suffix = {
            "task_review": "analysis-review",
            "package_verification_review": "package-verification-review",
            "package_apply_review": "package-apply-review",
            "package_syncback_review": "package-syncback-review",
            "package_artifact_review": "package-artifact-review",
        }.get(review_kind, review_kind.replace("_", "-") or "task-review")
        return {
            "path": "/control/actions/task/task-review",
            "method": "POST",
            "mode": "safe",
            "command": f"/task {task_ref} | {command_suffix}",
            "note": "inspect the blocked worker rows before escalating or promoting changes",
            "payload": {"task_ref": task_ref, "review_kind": review_kind},
        }

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

    if path == "/control/actions/task/worker-apply-preview":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/task {task_ref} | worker-apply-preview",
            "note": "inspect the artifact-apply proposal payload before proposing or accepting it",
            "payload": {"task_ref": task_ref},
        }

    if path == "/control/actions/task/operator-preference-decision":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        artifact_kind = str(payload.get("artifact_kind", "")).strip().lower()
        if not artifact_kind:
            raise ValueError("artifact_kind is required")
        key = str(payload.get("key", "")).strip().lower()
        if not key:
            raise ValueError("key is required")
        choice = str(payload.get("choice", "")).strip().lower()
        if choice not in {"apply_once", "apply_always", "skip_once", "skip_always"}:
            raise ValueError("choice must be one of apply_once, apply_always, skip_once, skip_always")
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/task {task_ref} | pref {key} {choice}",
            "note": "record an adaptive operator preference decision for the current task and project",
            "payload": {
                "task_ref": task_ref,
                "artifact_kind": artifact_kind,
                "key": key,
                "value": payload.get("value"),
                "description": payload.get("description"),
                "choice": choice,
                "scope": str(payload.get("scope", "")).strip().lower() or "artifact_kind",
                "scope_ref": payload.get("scope_ref"),
                "return_path": str(payload.get("return_path", "")).strip(),
            },
        }

    if path == "/control/actions/task/worker-apply-propose":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        return {
            "path": path,
            "method": "POST",
            "mode": "phase2",
            "command": f"/task {task_ref} | worker-apply-propose",
            "note": "promote the bounded worker update into an apply-oriented proposal",
            "payload": {"task_ref": task_ref},
        }

    if path == "/control/actions/task/worker-apply-accept":
        task_ref = str(payload.get("task_ref", "")).strip()
        proposal_ref = str(payload.get("proposal_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        if not proposal_ref:
            raise ValueError("proposal_ref is required")
        return {
            "path": path,
            "method": "POST",
            "mode": "phase2",
            "command": f"/task {task_ref} | worker-apply-accept {proposal_ref}",
            "note": "accept the artifact-apply proposal into the runtime todo queue",
            "payload": {"task_ref": task_ref, "proposal_ref": proposal_ref},
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

    if path == "/control/actions/runtime/syncback-preview":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        spec = operator_action_contract.http_action_spec(f"/todo {project_ref} syncback preview")
        if spec is None:
            raise ValueError("unsupported syncback preview action contract")
        return spec

    if path == "/control/actions/runtime/syncback-apply":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        spec = operator_action_contract.http_action_spec(f"/todo {project_ref} syncback apply")
        if spec is None:
            raise ValueError("unsupported syncback apply action contract")
        return spec

    if path == "/control/actions/runtime/background-queue-clean":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        spec = operator_action_contract.http_action_spec(f"/orch bgq-clean {project_ref}")
        if spec is None:
            raise ValueError("unsupported background queue cleanup action contract")
        return spec

    if path == "/control/actions/runtime/background-queue-clean-preview":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        return {
            "command": f"/orch bgq-clean {project_ref} preview",
            "mode": "safe",
            "method": "POST",
            "path": path,
            "payload": {"project_ref": project_ref},
            "note": "inspect stale background queue tickets before marking them stale",
        }

    if path == "/control/actions/runtime/server-guard-pressure-preview":
        pressure_kind = str(payload.get("pressure_kind", "")).strip().lower()
        if pressure_kind not in {"codex", "python", "tmux", "process"}:
            raise ValueError("pressure_kind must be one of codex, python, tmux, process")
        return {
            "command": f"/ops pressure {pressure_kind} preview",
            "mode": "safe",
            "method": "POST",
            "path": path,
            "payload": {"pressure_kind": pressure_kind},
            "note": "inspect server guard pressure before opening more manager surfaces or launching more work",
        }

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

    if path == "/control/actions/control/operator-preference-rule":
        artifact_kind = str(payload.get("artifact_kind", "")).strip().lower()
        key = str(payload.get("key", "")).strip().lower()
        scope = str(payload.get("scope", "")).strip().lower() or "artifact_kind"
        mode = str(payload.get("mode", "")).strip().lower()
        if not artifact_kind:
            raise ValueError("artifact_kind is required")
        if not key:
            raise ValueError("key is required")
        if mode not in {"auto", "confirm", "manual_only", "disable", "delete"}:
            raise ValueError("mode must be one of auto, confirm, manual_only, disable, delete")
        value = payload.get("value_json")
        if isinstance(value, str) and str(value).strip():
            try:
                value = json.loads(str(value))
            except Exception:
                value = str(value)
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/prefs rule {artifact_kind}:{key} {mode}",
            "note": "update or remove a persisted adaptive operator preference rule",
            "payload": {
                "runtime_ref": str(payload.get("runtime_ref", "")).strip(),
                "return_path": str(payload.get("return_path", "")).strip(),
                "artifact_kind": artifact_kind,
                "key": key,
                "scope": scope,
                "scope_ref": payload.get("scope_ref"),
                "value_json": value,
                "description": payload.get("description"),
                "mode": mode,
            },
        }

    if path == "/control/actions/control/operator-preference-candidate":
        artifact_kind = str(payload.get("artifact_kind", "")).strip().lower()
        key = str(payload.get("key", "")).strip().lower()
        mode = str(payload.get("mode", "")).strip().lower()
        if not artifact_kind:
            raise ValueError("artifact_kind is required")
        if not key:
            raise ValueError("key is required")
        if mode not in {"auto", "confirm", "disable", "dismiss"}:
            raise ValueError("mode must be one of auto, confirm, disable, dismiss")
        value = payload.get("value_json")
        if isinstance(value, str) and str(value).strip():
            try:
                value = json.loads(str(value))
            except Exception:
                value = str(value)
        return {
            "path": path,
            "method": "POST",
            "mode": "safe",
            "command": f"/prefs candidate {artifact_kind}:{key} {mode}",
            "note": "promote, mute, or dismiss an adaptive operator preference candidate",
            "payload": {
                "task_ref": str(payload.get("task_ref", "")).strip(),
                "runtime_ref": str(payload.get("runtime_ref", "")).strip(),
                "return_path": str(payload.get("return_path", "")).strip(),
                "project_ref": str(payload.get("project_ref", "")).strip(),
                "artifact_kind": artifact_kind,
                "key": key,
                "value_json": value,
                "description": payload.get("description"),
                "mode": mode,
            },
        }

    raise ValueError("unknown action path")



def _preview_followup_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    paths, manager_state = _load_dashboard_manager_state(config)
    detail = load_task_detail(
        control_root=config.control_root,
        team_dir=config.team_dir,
        manager_state_file=config.manager_state_file,
        request_id=task_ref,
    )
    if detail is None:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")
    project_key = ""
    source_task = None
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        task = gateway_task_state.get_task_record(entry, task_ref)
        if isinstance(task, dict):
            project_key = str(key)
            source_task = task
            break
    if isinstance(source_task, dict):
        manual_gate = gateway_task_state.derive_task_manual_gate(source_task)
        if str(manual_gate.get("status", "")).strip() == "blocked":
            return _json(
                {
                    "ok": False,
                    "implemented": True,
                    "executed": False,
                    "status": "blocked",
                    "method": "POST",
                    "path": spec.get("path", "-"),
                    "mode": spec.get("mode", "-"),
                    "source_command": spec.get("command", "-"),
                    "payload": payload,
                    "next_step": str(manual_gate.get("next_step", "")).strip() or f"/task {task_ref}",
                    "remediation": str(manual_gate.get("remediation", "")).strip() or (
                        "inspect the current phase checkpoint before applying judge-backed follow-up steps"
                    ),
                    "outcome": {
                        "kind": "task_followup",
                        "status": "blocked",
                        "reason_code": str(manual_gate.get("reason_code", "")).strip() or "manual_gate_blocked",
                        "detail": str(manual_gate.get("detail", "")).strip() or "-",
                    },
                    "job_contract": str(manual_gate.get("job_contract_summary", "")).strip() or "-",
                    "debug_packet": str(manual_gate.get("debug_packet_summary", "")).strip() or "-",
                    "phase_checkpoint": str(manual_gate.get("phase_checkpoint_summary", "")).strip() or "-",
                    "task": {
                        "project_alias": detail.project_alias,
                        "request_id": detail.request_id,
                        "label": detail.label,
                        "status": detail.status,
                        "tf_phase": detail.tf_phase,
                        "detail_path": f"/control/tasks/by-request/{detail.request_id}",
                        "runtime_path": f"/control/runtimes/{detail.project_alias}",
                    },
                },
                status=409,
            )
    response = _json(
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
    if isinstance(source_task, dict):
        persist_manual_step_execution_state(
            source_task,
            manual_kind="manual_followup",
            source_command=str(spec.get("command", "")).strip() or f"/followup {task_ref}",
            state="preview",
            next_step=(detail.command_hints[0] if detail.command_hints else f"/task {detail.label}"),
            at=datetime.now().astimezone().replace(microsecond=0).isoformat(),
        )
        if project_key:
            gateway_main = _load_gateway_main_module()
            if gateway_main is not None:
                gateway_main.save_manager_state(config.manager_state_file, manager_state)
    return response




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

    if path == "/control/actions/chat/send":
        return _with_action_audit(_execute_chat_send_action(spec, config=config), config=config)

    if path == "/control/actions/chat/session-update":
        return _with_action_audit(_execute_chat_session_update_action(spec, config=config), config=config)

    if path == "/control/actions/chat/session-select-task":
        return _with_action_audit(_execute_chat_session_select_task_action(spec, config=config), config=config)

    if path == "/control/actions/task/followup":
        return _with_action_audit(_preview_followup_action(spec, config=config), config=config)

    if path == "/control/actions/task/subagent-support-run":
        return _with_action_audit(_execute_general_subagent_support_action(spec, config=config), config=config)

    if path == "/control/actions/task/followup-execute":
        return _with_action_audit(_execute_followup_action(spec, config=config), config=config)

    if path in {"/control/actions/task/task-review", "/control/actions/task/analysis-review"}:
        return _with_action_audit(_execute_analysis_review_action(spec, config=config), config=config)

    if path == "/control/actions/task/worker-update-preview":
        return _with_action_audit(_execute_worker_update_preview_action(spec, config=config), config=config)

    if path == "/control/actions/task/worker-apply-preview":
        return _with_action_audit(_execute_worker_apply_preview_action(spec, config=config), config=config)

    if path == "/control/actions/task/operator-preference-decision":
        return _with_action_audit(_execute_operator_preference_decision_action(spec, config=config), config=config)

    if path == "/control/actions/task/worker-apply-propose":
        return _with_action_audit(_execute_worker_apply_propose_action(spec, config=config), config=config)

    if path == "/control/actions/task/worker-apply-accept":
        return _with_action_audit(_execute_worker_apply_accept_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/sync-preview":
        return _with_action_audit(_preview_sync_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/syncback-preview":
        return _with_action_audit(_execute_runtime_syncback_preview_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/syncback-apply":
        return _with_action_audit(_execute_runtime_syncback_apply_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/background-queue-clean":
        return _with_action_audit(_execute_background_queue_clean_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/background-queue-clean-preview":
        return _with_action_audit(_preview_background_queue_clean_action(spec, config=config), config=config)

    if path == "/control/actions/runtime/server-guard-pressure-preview":
        return _with_action_audit(_preview_server_guard_pressure_action(spec, config=config), config=config)

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

    if path == "/control/actions/control/operator-preference-rule":
        return _with_action_audit(_execute_operator_preference_rule_action(spec, config=config), config=config)

    if path == "/control/actions/control/operator-preference-candidate":
        return _with_action_audit(_execute_operator_preference_candidate_action(spec, config=config), config=config)

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
