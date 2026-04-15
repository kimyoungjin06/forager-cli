#!/usr/bin/env python3
"""Shared config and HTTP helpers for the Control Dashboard."""

from __future__ import annotations

import ipaddress
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from control_dashboard_state import resolve_control_paths
from control_dashboard_views import render_template

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "static"
ACTION_PATHS = {
    "/control/actions/chat/send",
    "/control/actions/chat/session-update",
    "/control/actions/chat/session-select-task",
    "/control/actions/task/retry",
    "/control/actions/task/replan",
    "/control/actions/task/followup",
    "/control/actions/task/followup-execute",
    "/control/actions/task/task-review",
    "/control/actions/task/analysis-review",
    "/control/actions/task/worker-update-preview",
    "/control/actions/task/worker-apply-preview",
    "/control/actions/task/worker-apply-propose",
    "/control/actions/task/worker-apply-accept",
    "/control/actions/runtime/judge",
    "/control/actions/runtime/todo-accept",
    "/control/actions/runtime/todo-reject",
    "/control/actions/runtime/sync-preview",
    "/control/actions/runtime/syncback-preview",
    "/control/actions/runtime/syncback-apply",
    "/control/actions/runtime/background-queue-clean",
    "/control/actions/control/auto-recover",
}


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
    import json

    return status, {"Content-Type": "application/json; charset=utf-8"}, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _redirect(location: str) -> Tuple[int, Dict[str, str], bytes]:
    from http import HTTPStatus

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
