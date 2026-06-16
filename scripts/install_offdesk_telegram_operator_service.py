#!/usr/bin/env python3
"""Install the Forager Telegram remote-operator as a systemd user service."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_TELEGRAM_ENV",
        "/home/kimyoungjin06/Desktop/Workspace/aoe_orch_control/.aoe-team/telegram.env",
    )
)
DEFAULT_CACHE_DIR = pathlib.Path.home() / ".cache" / "forager"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-name", default="forager-telegram-operator.service")
    parser.add_argument("--repo-root", type=pathlib.Path, default=REPO_ROOT)
    parser.add_argument("--python-bin", default=sys.executable or "python3")
    parser.add_argument("--forager-bin", type=pathlib.Path, default=REPO_ROOT / "target" / "debug" / "forager")
    parser.add_argument("--env-file", type=pathlib.Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    parser.add_argument("--state-file", type=pathlib.Path, default=DEFAULT_CACHE_DIR / "remote_operator_telegram_state.json")
    parser.add_argument("--feedback-file", type=pathlib.Path, default=DEFAULT_CACHE_DIR / "remote_operator_telegram_feedback.jsonl")
    parser.add_argument("--feedback-ingest-dir", type=pathlib.Path, default=DEFAULT_CACHE_DIR / "remote_operator_telegram_feedback_ingest")
    parser.add_argument("--loop-status-file", type=pathlib.Path, default=DEFAULT_CACHE_DIR / "remote_operator_telegram_loop.json")
    parser.add_argument("--poll-timeout-sec", type=int, default=30)
    parser.add_argument("--api-timeout-sec", type=int, default=45)
    parser.add_argument("--poll-error-backoff-sec", type=int, default=5)
    parser.add_argument("--install", action="store_true", help="Write the unit into ~/.config/systemd/user.")
    parser.add_argument("--enable", action="store_true", help="Enable the service after installing it.")
    parser.add_argument("--start", action="store_true", help="Start the service after installing it.")
    parser.add_argument("--restart", action="store_true", help="Restart the service after installing it.")
    parser.add_argument("--dry-run", action="store_true", help="Print the unit and do not write or call systemctl.")
    return parser.parse_args()


def systemd_arg(value: Any) -> str:
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "systemd" / "user"


def service_path(service_name: str) -> pathlib.Path:
    return service_dir() / service_name


def render_unit(args: argparse.Namespace) -> str:
    script = args.repo_root / "scripts" / "offdesk_remote_operator_telegram.py"
    command = [
        args.python_bin,
        script,
        "--profile",
        args.profile,
        "--forager-bin",
        args.forager_bin,
        "--env-file",
        args.env_file,
        "--state-file",
        args.state_file,
        "--feedback-file",
        args.feedback_file,
        "--feedback-ingest-dir",
        args.feedback_ingest_dir,
        "--loop-status-file",
        args.loop_status_file,
        "--poll-timeout-sec",
        args.poll_timeout_sec,
        "--api-timeout-sec",
        args.api_timeout_sec,
        "--poll-error-backoff-sec",
        args.poll_error_backoff_sec,
    ]
    exec_start = " ".join(systemd_arg(item) for item in command)
    return "\n".join(
        [
            "[Unit]",
            "Description=Forager Telegram remote operator",
            "After=network-online.target",
            "Wants=network-online.target",
            "StartLimitIntervalSec=0",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={systemd_arg(args.repo_root)}",
            f"ExecStart={exec_start}",
            "Restart=always",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def systemctl_user(*args: str) -> None:
    subprocess.run(["systemctl", "--user", *args], check=True)


def main() -> int:
    args = parse_args()
    unit = render_unit(args)
    path = service_path(args.service_name)
    report: dict[str, Any] = {
        "schema": "forager_telegram_operator_systemd_install.v1",
        "service_name": args.service_name,
        "service_path": str(path),
        "repo_root": str(args.repo_root),
        "loop_status_file": str(args.loop_status_file),
        "installed": False,
        "enabled": False,
        "started": False,
        "restarted": False,
    }
    if args.dry_run or not args.install:
        report["unit_preview"] = unit
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit, encoding="utf-8")
    report["installed"] = True
    systemctl_user("daemon-reload")
    if args.enable:
        systemctl_user("enable", args.service_name)
        report["enabled"] = True
    if args.restart:
        systemctl_user("restart", args.service_name)
        report["restarted"] = True
    elif args.start:
        systemctl_user("start", args.service_name)
        report["started"] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
