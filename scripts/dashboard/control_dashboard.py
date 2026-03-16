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
from typing import Dict, Optional, Tuple
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts" / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "dashboard"))

from control_dashboard_state import (
    load_dashboard_snapshot,
    load_dashboard_runtime_page,
    load_dashboard_task_page,
    load_task_detail,
    resolve_task_request_for_alias_route,
)
from control_dashboard_views import render_template


STATIC_ROOT = ROOT / "static"


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


def _serve_static(path: str) -> Tuple[int, Dict[str, str], bytes]:
    rel = path.removeprefix("/")
    target = (ROOT / rel).resolve()
    if not str(target).startswith(str(STATIC_ROOT.resolve())) or not target.exists() or not target.is_file():
        return _not_found("static asset not found")
    mime, _ = mimetypes.guess_type(str(target))
    return 200, {"Content-Type": mime or "application/octet-stream"}, target.read_bytes()


def build_dashboard_response(raw_path: str, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    parsed = urlparse(raw_path)
    path = parsed.path or "/control"

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
