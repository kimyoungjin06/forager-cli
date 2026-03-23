#!/usr/bin/env python3
"""Read-only Control Dashboard server."""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts" / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "dashboard"))
if str(ROOT / "scripts" / "gateway") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "gateway"))

import aoe_tg_chat_state as chat_state
import aoe_tg_management_handlers as management_handlers
import aoe_tg_operator_action_contract as operator_action_contract
import aoe_tg_retry_handlers as retry_handlers
import aoe_tg_runtime_read as runtime_read
import aoe_tg_scheduler_control_handlers as scheduler_control_handlers
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view

from control_dashboard_state import (
    load_dashboard_recovery_page,
    load_dashboard_runtime_details,
    load_dashboard_snapshot,
    load_dashboard_runtime_page,
    load_dashboard_task_page,
    load_task_detail,
    resolve_control_paths,
    resolve_task_request_for_alias_route,
)
from control_dashboard_views import render_template


STATIC_ROOT = ROOT / "static"
ACTION_PATHS = {
    "/control/actions/task/retry",
    "/control/actions/task/followup",
    "/control/actions/runtime/sync-preview",
    "/control/actions/control/auto-recover",
}
_DASHBOARD_CHAT_ID = "dashboard-http"
_DASHBOARD_CHAT_ROLE = "owner"


@dataclass(frozen=True)
class DashboardAppConfig:
    control_root: Path
    team_dir: Optional[Path]
    manager_state_file: Optional[Path]
    host: str
    port: int


def validate_loopback_host(host: str) -> str:
    token = str(host or "").strip()
    if token in {"localhost", "127.0.0.1", "::1"}:
        return token
    try:
        addr = ipaddress.ip_address(token)
    except ValueError as exc:
        raise SystemExit(f"Phase 1 dashboard only supports loopback bind: {host}") from exc
    if not addr.is_loopback:
        raise SystemExit(f"Phase 1 dashboard only supports loopback bind: {host}")
    return token


def _html(body: str, status: int = 200) -> Tuple[int, Dict[str, str], bytes]:
    return status, {"Content-Type": "text/html; charset=utf-8"}, body.encode("utf-8")


def _json(payload: Dict[str, object], status: int = 200) -> Tuple[int, Dict[str, str], bytes]:
    return status, {"Content-Type": "application/json; charset=utf-8"}, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _redirect(location: str) -> Tuple[int, Dict[str, str], bytes]:
    return HTTPStatus.FOUND, {"Location": location}, b""


def _not_found(message: str) -> Tuple[int, Dict[str, str], bytes]:
    return _html(render_template("dashboard/not_found.html", page_title="Not Found", message=message), status=404)


def _not_found_json(*, path: str, message: str) -> Tuple[int, Dict[str, str], bytes]:
    return _json(
        {
            "ok": False,
            "error": "not_found",
            "path": path,
            "message": message,
        },
        status=404,
    )


def _method_not_allowed(*, path: str, allowed: str) -> Tuple[int, Dict[str, str], bytes]:
    status, headers, body = _json(
        {
            "ok": False,
            "error": "method_not_allowed",
            "path": path,
            "allowed": allowed,
        },
        status=405,
    )
    headers["Allow"] = allowed
    return status, headers, body


def _bad_request(message: str, *, path: str, details: object | None = None) -> Tuple[int, Dict[str, str], bytes]:
    payload: Dict[str, object] = {
        "ok": False,
        "error": "bad_request",
        "path": path,
        "message": message,
    }
    if details is not None:
        payload["details"] = details
    return _json(payload, status=400)


def _unsupported_media_type(*, path: str, content_type: str) -> Tuple[int, Dict[str, str], bytes]:
    return _json(
        {
            "ok": False,
            "error": "unsupported_media_type",
            "path": path,
            "content_type": content_type or "-",
            "expected": "application/json",
        },
        status=415,
    )


def _serve_static(path: str) -> Tuple[int, Dict[str, str], bytes]:
    rel = path.removeprefix("/")
    target = (ROOT / rel).resolve()
    if not str(target).startswith(str(STATIC_ROOT.resolve())) or not target.exists() or not target.is_file():
        return _not_found("static asset not found")
    mime, _ = mimetypes.guess_type(str(target))
    return 200, {"Content-Type": mime or "application/octet-stream"}, target.read_bytes()


def _normalize_lane_ids(raw: object) -> list[str]:
    if raw is None or raw == "":
        return []
    if not isinstance(raw, list):
        raise ValueError("lane_ids must be a list of strings")
    lane_ids: list[str] = []
    for item in raw:
        token = str(item or "").strip()
        if not token:
            continue
        lane_ids.append(token)
    return lane_ids


def _truncate_text(raw: Any, limit: int = 240) -> str:
    text = " ".join(str(raw or "").strip().split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _dashboard_paths(config: DashboardAppConfig):
    return resolve_control_paths(
        control_root=config.control_root,
        team_dir=config.team_dir,
        manager_state_file=config.manager_state_file,
    )


def _dashboard_action_args(config: DashboardAppConfig) -> Any:
    paths = _dashboard_paths(config)
    return SimpleNamespace(
        control_root=str(paths.control_root),
        project_root=str(paths.control_root),
        team_dir=str(paths.team_dir),
        manager_state_file=paths.manager_state_file,
        dry_run=False,
        require_verifier=False,
        verifier_roles="",
    )


def _load_dashboard_manager_state(config: DashboardAppConfig) -> tuple[Any, Dict[str, Any]]:
    paths = _dashboard_paths(config)
    state = runtime_read.load_manager_state(paths.manager_state_file, paths.control_root, paths.team_dir)
    return paths, state


def _make_send_collector(messages: List[Dict[str, Any]]):
    def _send(text: Any, *, context: str = "", with_menu: bool = False, reply_markup: Any = None, **_kwargs: Any) -> bool:
        messages.append(
            {
                "text": str(text or "").strip(),
                "context": str(context or "").strip(),
                "with_menu": bool(with_menu),
                "reply_markup_present": bool(reply_markup),
            }
        )
        return True

    return _send


def _dashboard_get_context_factory(manager_state: Dict[str, Any], paths: Any):
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}

    def _get_context(raw_target: Optional[str]) -> tuple[str, Dict[str, Any], Any]:
        target = str(raw_target or "").strip()
        if target:
            upper = target.upper()
            for key, entry in projects.items():
                if not isinstance(entry, dict):
                    continue
                alias = str(entry.get("project_alias", "")).strip().upper()
                if str(key) == target or alias == upper:
                    return str(key), entry, SimpleNamespace(
                        project_root=Path(str(entry.get("project_root", paths.control_root))).expanduser().resolve(),
                        team_dir=Path(str(entry.get("team_dir", paths.team_dir))).expanduser().resolve(),
                        manager_state_file=paths.manager_state_file,
                        require_verifier=False,
                        verifier_roles="",
                    )
        active = str(manager_state.get("active", "")).strip()
        entry = projects.get(active) if active and isinstance(projects.get(active), dict) else None
        if isinstance(entry, dict):
            return active, entry, SimpleNamespace(
                project_root=Path(str(entry.get("project_root", paths.control_root))).expanduser().resolve(),
                team_dir=Path(str(entry.get("team_dir", paths.team_dir))).expanduser().resolve(),
                manager_state_file=paths.manager_state_file,
                require_verifier=False,
                verifier_roles="",
            )
        raise RuntimeError(f"runtime not found: {raw_target or '-'}")

    return _get_context


def _find_task_project_key(manager_state: Dict[str, Any], task_ref: str) -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(task_ref or "").strip()
    if not target:
        return ""
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if gateway_task_state.get_task_record(entry, target):
            return str(key)
    return ""


def _is_known_dashboard_get_route(path: str) -> bool:
    if path in {"", "/", "/control", "/control/offdesk", "/control/recovery", "/control/tasks", "/control/health"}:
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/control/runtimes/"):
        return bool(unquote(path.removeprefix("/control/runtimes/")).strip())
    if path.startswith("/control/tasks/by-request/"):
        return bool(unquote(path.removeprefix("/control/tasks/by-request/")).strip())

    parts = [token for token in path.split("/") if token]
    return len(parts) == 4 and parts[0] == "control" and parts[2] == "tasks"


def _action_spec_for_request(path: str, payload: Dict[str, object]) -> Dict[str, object]:
    if path == "/control/actions/task/retry":
        task_ref = str(payload.get("task_ref", "")).strip()
        if not task_ref:
            raise ValueError("task_ref is required")
        lane_ids = _normalize_lane_ids(payload.get("lane_ids"))
        command = f"/retry {task_ref}"
        if lane_ids:
            command += " lane " + ",".join(lane_ids)
        spec = operator_action_contract.http_action_spec(command)
        if spec is None:
            raise ValueError("unsupported retry action contract")
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

    if path == "/control/actions/runtime/sync-preview":
        project_ref = str(payload.get("project_ref", "")).strip()
        if not project_ref:
            raise ValueError("project_ref is required")
        window = str(payload.get("window", "24h")).strip() or "24h"
        spec = operator_action_contract.http_action_spec(f"/sync preview {project_ref} {window}")
        if spec is None:
            raise ValueError("unsupported sync preview action contract")
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
                "detail_path": f"/control/tasks/by-request/{quote(detail.request_id, safe='')}",
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


def _execute_retry_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    paths, manager_state = _load_dashboard_manager_state(config)
    project_key = _find_task_project_key(manager_state, task_ref)
    if not project_key:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")

    messages: List[Dict[str, Any]] = []
    transition = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-retry",
        args=_dashboard_action_args(config),
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        orch_target=project_key,
        orch_retry_request_id=task_ref,
        orch_replan_request_id=None,
        orch_retry_lane_ids=list(payload.get("lane_ids") or []),
        orch_replan_lane_ids=None,
        send=_make_send_collector(messages),
        get_context=_dashboard_get_context_factory(manager_state, paths),
        get_chat_selected_task_ref=chat_state.get_chat_selected_task_ref,
        resolve_chat_task_ref=chat_state.resolve_chat_task_ref,
        resolve_task_request_id=gateway_task_state.resolve_task_request_id,
        get_task_record=gateway_task_state.get_task_record,
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=gateway_task_view.dedupe_roles,
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
    )
    if not isinstance(transition, dict):
        return _json(
            {
                "ok": False,
                "error": "retry_transition_unavailable",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
            },
            status=500,
        )

    if bool(transition.get("terminal")):
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
                "messages": messages,
            },
            status=409,
        )

    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "accepted_transition",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
                "prompt_preview": _truncate_text(transition.get("run_prompt", ""), 160) or "-",
            },
            "messages": messages,
            "note": "retry transition prepared; dashboard run execution bridge is still pending",
        },
        status=202,
    )


def _execute_auto_recover_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    args = _dashboard_action_args(config)
    paths, manager_state = _load_dashboard_manager_state(config)
    messages: List[Dict[str, Any]] = []

    handled = scheduler_control_handlers.handle_scheduler_control_command(
        cmd="auto",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        chat_role=_DASHBOARD_CHAT_ROLE,
        rest="recover force" if bool(payload.get("force")) else "recover",
        send=_make_send_collector(messages),
        get_default_mode=lambda *_args, **_kwargs: "",
        get_pending_mode=lambda *_args, **_kwargs: "",
        get_chat_report_level=lambda *_args, **_kwargs: "normal",
        get_chat_room=lambda *_args, **_kwargs: management_handlers.DEFAULT_OFFDESK_ROOM,
        set_default_mode=lambda *_args, **_kwargs: None,
        set_chat_report_level=lambda *_args, **_kwargs: None,
        set_chat_room=lambda *_args, **_kwargs: None,
        clear_default_mode=lambda *_args, **_kwargs: False,
        clear_pending_mode=lambda *_args, **_kwargs: False,
        clear_confirm_action=lambda *_args, **_kwargs: False,
        clear_chat_report_level=lambda *_args, **_kwargs: False,
        save_manager_state=lambda *_args, **_kwargs: None,
        resolve_project_entry=management_handlers._resolve_project_entry,
        project_lock_row=management_handlers._project_lock_row,
        project_lock_label=management_handlers._project_lock_label,
        parse_replace_sync_flag=management_handlers._parse_replace_sync_flag,
        normalize_prefetch_token=management_handlers._normalize_prefetch_token,
        prefetch_display=management_handlers._prefetch_display,
        compact_reason=management_handlers._compact_reason,
        status_report_level=management_handlers._status_report_level,
        focused_project_snapshot_lines=management_handlers._focused_project_snapshot_lines,
        ops_scope_summary=management_handlers._ops_scope_summary,
        ops_scope_compact_lines=lambda state, limit, detail_level: management_handlers._ops_scope_compact_lines(
            state,
            limit=limit,
            detail_level=detail_level,
        ),
        offdesk_prepare_targets=management_handlers._offdesk_prepare_targets,
        offdesk_prepare_project_report=management_handlers._offdesk_prepare_project_report,
        sort_offdesk_reports=management_handlers._sort_offdesk_reports,
        offdesk_review_reply_markup=lambda *_args, **_kwargs: {},
        offdesk_prepare_reply_markup=lambda *_args, **_kwargs: {},
        auto_state_path=management_handlers._auto_state_path,
        offdesk_state_path=management_handlers._offdesk_state_path,
        provider_capacity_state_path=management_handlers._provider_capacity_state_path,
        load_auto_state=management_handlers._load_auto_state,
        save_auto_state=management_handlers._save_auto_state,
        load_offdesk_state=management_handlers._load_offdesk_state,
        save_offdesk_state=management_handlers._save_offdesk_state,
        load_provider_capacity_state=management_handlers._load_provider_capacity_state,
        save_provider_capacity_state=management_handlers._save_provider_capacity_state,
        scheduler_session_name=management_handlers._scheduler_session_name,
        tmux_has_session=management_handlers._tmux_has_session,
        tmux_auto_command=management_handlers._tmux_auto_command,
        now_iso=management_handlers._now_iso,
        default_auto_interval_sec=management_handlers.DEFAULT_AUTO_INTERVAL_SEC,
        default_auto_idle_sec=management_handlers.DEFAULT_AUTO_IDLE_SEC,
        default_auto_max_failures=management_handlers.DEFAULT_AUTO_MAX_FAILURES,
        default_offdesk_command=management_handlers.DEFAULT_OFFDESK_COMMAND,
        default_offdesk_prefetch=management_handlers.DEFAULT_OFFDESK_PREFETCH,
        default_offdesk_prefetch_since=management_handlers.DEFAULT_OFFDESK_PREFETCH_SINCE,
        default_offdesk_report_level=management_handlers.DEFAULT_OFFDESK_REPORT_LEVEL,
        default_offdesk_room=management_handlers.DEFAULT_OFFDESK_ROOM,
    )

    if not handled:
        return _json(
            {
                "ok": False,
                "error": "auto_recover_unhandled",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
            },
            status=500,
        )

    auto_state = management_handlers._load_auto_state(management_handlers._auto_state_path(args))
    provider_state = management_handlers._load_provider_capacity_state(management_handlers._provider_capacity_state_path(args))
    last_context = str(messages[-1].get("context", "")).strip() if messages else ""
    blocked = last_context == "auto-recover-blocked"

    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": not blocked,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "messages": messages,
            "auto_state": {
                "enabled": bool(auto_state.get("enabled", False)),
                "command": str(auto_state.get("command", "")).strip() or "-",
                "recovered_at": str(auto_state.get("recovered_at", "")).strip() or "-",
                "recovery_grace_until": str(auto_state.get("recovery_grace_until", "")).strip() or "-",
            },
            "provider_capacity": {
                "next_retry_at": str(provider_state.get("next_retry_at", "")).strip() or "-",
                "repeat_count": int(provider_state.get("recovery_repeat_count", 0) or 0),
            },
            "team_dir": str(paths.team_dir),
        },
        status=409 if blocked else 200,
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
        return _preview_followup_action(spec, config=config)

    if path == "/control/actions/runtime/sync-preview":
        return _preview_sync_action(spec, config=config)

    if path == "/control/actions/task/retry":
        return _execute_retry_action(spec, config=config)

    if path == "/control/actions/control/auto-recover":
        return _execute_auto_recover_action(spec, config=config)

    return _json(
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
    )


def build_dashboard_response(raw_path: str, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    parsed = urlparse(raw_path)
    path = parsed.path or "/control"

    if path in ACTION_PATHS:
        return _method_not_allowed(path=path, allowed="POST")

    if path.startswith("/static/"):
        return _serve_static(path)

    if path == "/control/health":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _json(
            {
                "ok": True,
                "snapshot_taken_at": snapshot.snapshot_taken_at,
                "active_runtime_count": snapshot.control_summary.active_runtime_count,
                "attention_runtime_count": snapshot.control_summary.attention_runtime_count,
            }
        )

    if path == "/" or path == "":
        return _redirect("/control")

    if path == "/control":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(
            render_template(
                "dashboard/overview.html",
                page_title="Control Dashboard",
                snapshot=snapshot,
                current_path=path,
            )
        )

    if path == "/control/offdesk":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(
            render_template(
                "dashboard/offdesk.html",
                page_title="Offdesk Prep",
                snapshot=snapshot,
                current_path=path,
            )
        )

    if path == "/control/recovery":
        snapshot, recovery = load_dashboard_recovery_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(
            render_template(
                "dashboard/recovery.html",
                page_title="Recovery",
                snapshot=snapshot,
                recovery=recovery,
                current_path=path,
            )
        )

    if path == "/control/tasks":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(
            render_template(
                "dashboard/tasks.html",
                page_title="Active Tasks",
                snapshot=snapshot,
                current_path=path,
            )
        )

    runtime_prefix = "/control/runtimes/"
    if path.startswith(runtime_prefix):
        project_alias = unquote(path[len(runtime_prefix) :]).strip()
        if not project_alias:
            return _not_found("missing runtime alias")
        snapshot, detail = load_dashboard_runtime_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
            project_alias=project_alias,
        )
        if detail is None:
            return _not_found(f"runtime not found: {project_alias}")
        return _html(
            render_template(
                "dashboard/runtime_detail.html",
                page_title=f"Runtime {detail.project_alias}",
                snapshot=snapshot,
                detail=detail,
                current_path=path,
            )
        )

    prefix = "/control/tasks/by-request/"
    if path.startswith(prefix):
        request_id = unquote(path[len(prefix) :]).strip()
        if not request_id:
            return _not_found("missing request id")
        snapshot, detail = load_dashboard_task_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
            request_id=request_id,
        )
        if detail is None:
            return _not_found(f"task not found: {request_id}")
        return _html(
            render_template(
                "dashboard/task_detail.html",
                page_title=f"Task {detail.label}",
                snapshot=snapshot,
                detail=detail,
                current_path=path,
            )
        )

    parts = [token for token in path.split("/") if token]
    if len(parts) == 4 and parts[0] == "control" and parts[2] == "tasks":
        project_alias = unquote(parts[1])
        task_short_id = unquote(parts[3])
        request_id = resolve_task_request_for_alias_route(
            control_root=config.control_root,
            project_alias=project_alias,
            task_short_id=task_short_id,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        if not request_id:
            detail = load_task_detail(
                control_root=config.control_root,
                team_dir=config.team_dir,
                manager_state_file=config.manager_state_file,
                request_id=task_short_id,
            )
            if detail is None or detail.project_alias != project_alias.upper():
                return _not_found(f"task not found: {project_alias}/{task_short_id}")
            request_id = detail.request_id
        return _redirect(f"/control/tasks/by-request/{quote(request_id, safe='')}")

    return _not_found(f"unknown route: {path}")


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "ControlDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802
        config: DashboardAppConfig = self.server.dashboard_config  # type: ignore[attr-defined]
        status, headers, body = build_dashboard_response(self.path, config)
        self.send_response(int(status))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        config: DashboardAppConfig = self.server.dashboard_config  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length > 0 else b"{}"
        status, headers, response_body = build_dashboard_action_response(
            self.path,
            body=body,
            content_type=str(self.headers.get("Content-Type", "")).strip(),
            config=config,
        )
        self.send_response(int(status))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if response_body:
            self.wfile.write(response_body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Control Dashboard")
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--team-dir")
    parser.add_argument("--manager-state-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    host = validate_loopback_host(args.host)
    config = DashboardAppConfig(
        control_root=Path(args.control_root).expanduser().resolve(),
        team_dir=Path(args.team_dir).expanduser().resolve() if args.team_dir else None,
        manager_state_file=Path(args.manager_state_file).expanduser().resolve() if args.manager_state_file else None,
        host=host,
        port=int(args.port),
    )
    server = ThreadingHTTPServer((host, config.port), DashboardRequestHandler)
    server.dashboard_config = config  # type: ignore[attr-defined]
    print(f"Control Dashboard listening on http://{host}:{config.port}/control")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
