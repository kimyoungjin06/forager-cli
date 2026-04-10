#!/usr/bin/env python3
"""Read-only Control Dashboard server."""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts" / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "dashboard"))
if str(ROOT / "scripts" / "gateway") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "gateway"))

# Compatibility imports kept here because tests and local workflows patch these modules via control_dashboard.
import aoe_tg_management_handlers as management_handlers
import aoe_tg_run_handlers as run_handlers
import aoe_tg_scheduler_control_handlers as scheduler_control_handlers

from control_dashboard_actions import (
    _execute_followup_run_transition,
    _execute_retry_run_transition,
    _load_dashboard_manager_state,
    build_dashboard_action_response,
)
from control_dashboard_audit import _append_action_audit as _append_action_audit_impl
from control_dashboard_audit import _load_existing_action_audit_rows as _load_existing_action_audit_rows_impl
from control_dashboard_common import (
    ACTION_PATHS,
    DashboardAppConfig,
    _html,
    _json,
    _method_not_allowed,
    _not_found,
    _redirect,
    _serve_static,
    validate_loopback_host,
)
from control_dashboard_state import (
    load_dashboard_action_audit_page,
    load_dashboard_history_page,
    load_dashboard_recovery_page,
    load_dashboard_runtime_page,
    load_dashboard_snapshot,
    load_dashboard_task_page,
    load_task_detail,
    resolve_task_request_for_alias_route,
)
from control_dashboard_views import render_template


# Wrappers are kept for test compatibility. In particular, test_control_dashboard monkeypatches
# _load_existing_action_audit_rows on this module and expects _append_action_audit to respect it.
def _load_existing_action_audit_rows(path: Path):
    return _load_existing_action_audit_rows_impl(path)



def _append_action_audit(config: DashboardAppConfig, payload: Dict[str, object]) -> None:
    _append_action_audit_impl(config, payload, load_existing_rows=_load_existing_action_audit_rows)



def _is_known_dashboard_get_route(path: str) -> bool:
    if path in {"", "/", "/control", "/control/offdesk", "/control/recovery", "/control/audit", "/control/history", "/control/tasks", "/control/health"}:
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/control/runtimes/"):
        return bool(unquote(path.removeprefix("/control/runtimes/")).strip())
    if path.startswith("/control/tasks/by-request/"):
        return bool(unquote(path.removeprefix("/control/tasks/by-request/")).strip())
    parts = [token for token in path.split("/") if token]
    return len(parts) == 4 and parts[0] == "control" and parts[2] == "tasks"



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

    if path in {"", "/"}:
        return _redirect("/control")

    if path == "/control":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(render_template("dashboard/overview.html", page_title="Control Dashboard", snapshot=snapshot, current_path=path))

    if path == "/control/offdesk":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(render_template("dashboard/offdesk.html", page_title="Offdesk Prep", snapshot=snapshot, current_path=path))

    if path == "/control/recovery":
        snapshot, recovery = load_dashboard_recovery_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(render_template("dashboard/recovery.html", page_title="Recovery", snapshot=snapshot, recovery=recovery, current_path=path))

    if path == "/control/audit":
        query = parse_qs(parsed.query or "", keep_blank_values=False)
        snapshot, audit = load_dashboard_action_audit_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
            focus=str((query.get("focus") or ["all"])[0]),
        )
        return _html(render_template("dashboard/audit.html", page_title="Action Audit", snapshot=snapshot, audit=audit, current_path=path))

    if path == "/control/history":
        query = parse_qs(parsed.query or "", keep_blank_values=False)
        snapshot, history = load_dashboard_history_page(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
            query=str((query.get("q") or [""])[0]),
            project_filter=str((query.get("project") or [""])[0]),
            since=str((query.get("since") or [""])[0]),
            scope=str((query.get("scope") or ["all"])[0]),
            limit=int(str((query.get("limit") or ["20"])[0] or "20")),
        )
        return _html(
            render_template(
                "dashboard/history.html",
                page_title="History Search",
                snapshot=snapshot,
                history=history,
                current_path=path,
            )
        )

    if path == "/control/tasks":
        snapshot = load_dashboard_snapshot(
            control_root=config.control_root,
            team_dir=config.team_dir,
            manager_state_file=config.manager_state_file,
        )
        return _html(render_template("dashboard/tasks.html", page_title="Active Tasks", snapshot=snapshot, current_path=path))

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

    request_prefix = "/control/tasks/by-request/"
    if path.startswith(request_prefix):
        request_id = unquote(path[len(request_prefix) :]).strip()
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
