#!/usr/bin/env python3
"""Bootstrap/setup guidance for owner-only gateway deployments."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, List, Optional

import aoe_tg_doctor as doctor
from aoe_tg_runtime_core import resolve_project_root, resolve_team_dir


@dataclass(frozen=True)
class SetupStep:
    code: str
    status: str
    summary: str
    command: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SetupGuideReport:
    project_root: str
    team_dir: str
    state_root_mode: str
    state_root_path: str
    steps: List[SetupStep]
    pending_count: int
    warn_count: int
    ready_count: int


def collect_setup_guide(
    *,
    project_root: Path | str,
    team_dir: Optional[str] = None,
    state_file: Optional[str] = None,
    manager_state_file: Optional[str] = None,
    chat_aliases_file: Optional[str] = None,
    aoe_orch_bin: Optional[str] = None,
    aoe_team_bin: Optional[str] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> SetupGuideReport:
    root = resolve_project_root(str(project_root))
    resolved_team_dir = resolve_team_dir(root, team_dir)
    report = doctor.collect_doctor_report(
        project_root=root,
        team_dir=team_dir,
        state_file=state_file,
        manager_state_file=manager_state_file,
        chat_aliases_file=chat_aliases_file,
        aoe_orch_bin=aoe_orch_bin,
        aoe_team_bin=aoe_team_bin,
        which=which,
    )
    team_path = Path(report.team_dir)
    runtime_config = team_path / "orchestrator.json"
    env_file = team_path / "telegram.env"
    env_sample = team_path / "telegram.env.sample"
    install_user_services = root / "scripts" / "systemd" / "install_user_services.sh"
    dashboard_cmd = (
        f"python3 {root / 'scripts' / 'dashboard' / 'control_dashboard.py'} "
        f"--control-root {root} --host 127.0.0.1 --port 8765"
    )
    doctor_cmd = f"python3 {root / 'scripts' / 'gateway' / 'aoe_tg_doctor.py'} --project-root {root}"

    steps: List[SetupStep] = []

    if not team_path.exists() or not runtime_config.exists():
        steps.append(
            SetupStep(
                code="runtime_bootstrap",
                status="pending",
                summary="bootstrap runtime templates",
                command=(
                    f"bash {root / 'scripts' / 'team' / 'bootstrap_runtime_templates.sh'} "
                    f"--project-root {root} --team-dir {resolved_team_dir}"
                ),
                detail="creates .aoe-team templates, orchestrator.json, team.json, AOE_TODO.md, sample env, workers, and wrappers",
            )
        )
    else:
        steps.append(
            SetupStep(
                code="runtime_bootstrap",
                status="ready",
                summary="runtime templates already present",
                detail=str(runtime_config),
            )
        )

    if not env_file.exists():
        env_summary = "create telegram.env from sample" if env_sample.exists() else "create telegram.env"
        env_detail = str(env_sample) if env_sample.exists() else str(env_file)
        steps.append(
            SetupStep(
                code="runtime_env",
                status="pending",
                summary=env_summary,
                command=f"cp {env_sample} {env_file}" if env_sample.exists() else "",
                detail=env_detail,
            )
        )
    else:
        steps.append(
            SetupStep(
                code="runtime_env",
                status="ready",
                summary="telegram.env present",
                detail=str(env_file),
            )
        )

    legacy_fallback = next((row for row in report.checks if row.code == "state_root_legacy_fallback"), None)
    if legacy_fallback is not None:
        steps.append(
            SetupStep(
                code="state_root_migration",
                status="pending",
                summary="migrate legacy state into centralized root",
                command=legacy_fallback.next_step,
                detail=legacy_fallback.detail,
            )
        )
    else:
        steps.append(
            SetupStep(
                code="state_root_migration",
                status="ready",
                summary="state root does not require migration",
                detail=f"{report.state_root_mode} | {report.state_root_path}",
            )
        )

    systemctl_path = which("systemctl")
    tmux_row = next((row for row in report.checks if row.code == "tmux_bin"), None)
    if install_user_services.exists() and systemctl_path and (tmux_row is None or tmux_row.status != "fail"):
        steps.append(
            SetupStep(
                code="systemd_install",
                status="pending",
                summary="install user services",
                command=f"bash {install_user_services}",
                detail="renders user units and enables aoe-telegram stack + heal timer",
            )
        )
    else:
        missing = []
        if not install_user_services.exists():
            missing.append("install_user_services.sh")
        if not systemctl_path:
            missing.append("systemctl")
        if tmux_row is not None and tmux_row.status == "fail":
            missing.append("tmux")
        steps.append(
            SetupStep(
                code="systemd_install",
                status="warn",
                summary="systemd install prerequisites incomplete",
                detail=", ".join(missing) or "missing prerequisites",
            )
        )

    steps.append(
        SetupStep(
            code="dashboard_local",
            status="pending",
            summary="run local control dashboard",
            command=dashboard_cmd,
            detail="loopback-only dashboard for control/recovery/action audit/history",
        )
    )
    steps.append(
        SetupStep(
            code="doctor_rerun",
            status="pending" if report.fail_count or report.warn_count else "ready",
            summary="rerun runtime doctor after setup changes" if report.fail_count or report.warn_count else "doctor baseline is already clean",
            command=doctor_cmd if report.fail_count or report.warn_count else doctor_cmd,
            detail=f"current doctor summary: ok={report.ok_count} warn={report.warn_count} fail={report.fail_count}",
        )
    )

    pending_count = sum(1 for row in steps if row.status == "pending")
    warn_count = sum(1 for row in steps if row.status == "warn")
    ready_count = sum(1 for row in steps if row.status == "ready")
    return SetupGuideReport(
        project_root=str(root),
        team_dir=str(resolved_team_dir),
        state_root_mode=report.state_root_mode,
        state_root_path=report.state_root_path,
        steps=steps,
        pending_count=pending_count,
        warn_count=warn_count,
        ready_count=ready_count,
    )


def render_setup_guide(report: SetupGuideReport) -> str:
    lines = [
        "setup guide",
        f"- project_root: {report.project_root}",
        f"- team_dir: {report.team_dir}",
        f"- state_root: {report.state_root_mode} | {report.state_root_path}",
        "",
        "steps:",
    ]
    for row in report.steps:
        lines.append(f"- [{row.status}] {row.code}: {row.summary}")
        if row.command:
            lines.append(f"  command: {row.command}")
        if row.detail:
            lines.append(f"  detail: {row.detail}")
    lines.extend(
        [
            "",
            "summary:",
            f"- ready: {report.ready_count}",
            f"- pending: {report.pending_count}",
            f"- warn: {report.warn_count}",
        ]
    )
    return "\n".join(lines).strip()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show owner-only runtime setup guidance")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--team-dir")
    parser.add_argument("--state-file")
    parser.add_argument("--manager-state-file")
    parser.add_argument("--chat-aliases-file")
    parser.add_argument("--aoe-orch-bin")
    parser.add_argument("--aoe-team-bin")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    report = collect_setup_guide(
        project_root=args.project_root,
        team_dir=args.team_dir,
        state_file=args.state_file,
        manager_state_file=args.manager_state_file,
        chat_aliases_file=args.chat_aliases_file,
        aoe_orch_bin=args.aoe_orch_bin,
        aoe_team_bin=args.aoe_team_bin,
    )
    if args.json:
        print(
            json.dumps(
                {
                    **{key: value for key, value in asdict(report).items() if key != "steps"},
                    "steps": [asdict(row) for row in report.steps],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_setup_guide(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
