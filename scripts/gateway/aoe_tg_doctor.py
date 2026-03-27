#!/usr/bin/env python3
"""Owner-only runtime doctor for gateway state, artifact, and binary health."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_chat_aliases import resolve_chat_aliases_file
from aoe_tg_runtime_core import (
    action_audit_path,
    describe_resolved_team_dir,
    latest_intent_snapshot_path,
    provider_capacity_state_path,
    recovery_summary_latest_path,
    resolve_centralized_team_dir,
    resolve_project_root,
    resolve_team_dir,
    stable_project_id,
)


DEFAULT_AOE_ORCH_BIN = str(Path.home() / ".local/bin/aoe-orch")
DEFAULT_AOE_TEAM_BIN = str(Path.home() / ".local/bin/aoe-team")


@dataclass(frozen=True)
class DoctorCheck:
    code: str
    status: str
    summary: str
    detail: str = ""
    next_step: str = ""


@dataclass(frozen=True)
class DoctorReport:
    project_root: str
    team_dir: str
    state_root_mode: str
    state_root_path: str
    project_id: str
    gateway_state_file: str
    manager_state_file: str
    chat_aliases_file: str
    checks: List[DoctorCheck]
    fail_count: int
    warn_count: int
    info_count: int
    ok_count: int


def resolve_manager_state_file(team_dir: Path, explicit_manager_state_file: Optional[str]) -> Path:
    if explicit_manager_state_file:
        return Path(explicit_manager_state_file).expanduser().resolve()
    env_path = (os.environ.get("AOE_ORCH_MANAGER_STATE") or "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    return (team_dir / "orch_manager_state.json").resolve()


def resolve_gateway_state_file(team_dir: Path, explicit_state_file: Optional[str]) -> Path:
    if explicit_state_file:
        return Path(explicit_state_file).expanduser().resolve()
    return (team_dir / "telegram_gateway_state.json").resolve()


def _normalize_json_file(path: Path) -> Dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("json root is not an object")
    return parsed


def _check_dir(*, code: str, label: str, path: Path, required: bool) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            code=code,
            status="warn" if required else "info",
            summary=f"{label} missing",
            detail=str(path),
        )
    if not path.is_dir():
        return DoctorCheck(
            code=code,
            status="fail",
            summary=f"{label} is not a directory",
            detail=str(path),
        )
    return DoctorCheck(code=code, status="ok", summary=f"{label} present", detail=str(path))


def _check_json_artifact(*, code: str, label: str, path: Path, required: bool) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            code=code,
            status="warn" if required else "info",
            summary=f"{label} missing",
            detail=str(path),
        )
    try:
        _normalize_json_file(path)
    except Exception as exc:
        return DoctorCheck(
            code=code,
            status="fail",
            summary=f"{label} unreadable",
            detail=f"{path} | {exc}",
        )
    return DoctorCheck(code=code, status="ok", summary=f"{label} readable", detail=str(path))


def _check_text_artifact(*, code: str, label: str, path: Path, required: bool = False) -> DoctorCheck:
    if not path.exists():
        return DoctorCheck(
            code=code,
            status="warn" if required else "info",
            summary=f"{label} missing",
            detail=str(path),
        )
    return DoctorCheck(code=code, status="ok", summary=f"{label} present", detail=str(path))


def _binary_check(
    *,
    code: str,
    label: str,
    binary: str,
    which: Callable[[str], Optional[str]],
    required: bool = True,
) -> DoctorCheck:
    token = str(binary or "").strip()
    if not token:
        return DoctorCheck(
            code=code,
            status="fail" if required else "warn",
            summary=f"{label} not configured",
        )
    candidate = Path(token).expanduser()
    if candidate.exists():
        return DoctorCheck(code=code, status="ok", summary=f"{label} found", detail=str(candidate.resolve()))
    resolved = which(token)
    if resolved:
        return DoctorCheck(code=code, status="ok", summary=f"{label} found", detail=resolved)
    return DoctorCheck(
        code=code,
        status="fail" if required else "warn",
        summary=f"{label} missing",
        detail=token,
    )


def collect_doctor_report(
    *,
    project_root: Path | str,
    team_dir: Optional[str] = None,
    state_file: Optional[str] = None,
    manager_state_file: Optional[str] = None,
    chat_aliases_file: Optional[str] = None,
    aoe_orch_bin: Optional[str] = None,
    aoe_team_bin: Optional[str] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> DoctorReport:
    root = resolve_project_root(str(project_root))
    resolved_team_dir = resolve_team_dir(root, team_dir)
    state_root = describe_resolved_team_dir(resolved_team_dir)
    gateway_state_file = resolve_gateway_state_file(resolved_team_dir, state_file)
    resolved_manager_state_file = resolve_manager_state_file(resolved_team_dir, manager_state_file)
    resolved_chat_aliases_file = resolve_chat_aliases_file(resolved_team_dir, chat_aliases_file)
    legacy_team_dir = (root / ".aoe-team").resolve()
    runtime_config_file = (resolved_team_dir / "orchestrator.json").resolve()
    state_root_dir = str(os.environ.get("AOE_STATE_DIR", "")).strip()
    explicit_team_env = str(os.environ.get("AOE_TEAM_DIR", "")).strip()
    centralized_team_dir = (
        resolve_centralized_team_dir(root, Path(state_root_dir).expanduser().resolve())
        if state_root_dir
        else None
    )

    checks: List[DoctorCheck] = [
        DoctorCheck(
            code="state_root_selected",
            status="ok",
            summary=f"state root selected: {state_root.get('mode', '-')}",
            detail=state_root.get("path", ""),
        ),
        _check_dir(
            code="team_dir",
            label="team directory",
            path=resolved_team_dir,
            required=True,
        ),
    ]

    if state_root_dir and explicit_team_env:
        checks.append(
            DoctorCheck(
                code="state_root_explicit_override",
                status="warn",
                summary="AOE_TEAM_DIR overrides AOE_STATE_DIR",
                detail=explicit_team_env,
            )
        )
    if state_root_dir and state_root.get("mode") == "legacy":
        checks.append(
            DoctorCheck(
                code="state_root_legacy_fallback",
                status="warn",
                summary="AOE_STATE_DIR configured but legacy state is still active",
                detail=str(legacy_team_dir),
                next_step=(
                    "python3 scripts/gateway/aoe_tg_state_root_migration.py "
                    f"--project-root {root} --state-dir {Path(state_root_dir).expanduser().resolve()}"
                ),
            )
        )
    if centralized_team_dir is not None and centralized_team_dir.exists() and legacy_team_dir.exists():
        checks.append(
            DoctorCheck(
                code="state_root_dual_state",
                status="warn",
                summary="legacy and centralized state directories both exist",
                detail=f"legacy={legacy_team_dir} | centralized={centralized_team_dir}",
            )
        )

    checks.extend(
        [
            _check_text_artifact(
                code="runtime_config_file",
                label="runtime config",
                path=runtime_config_file,
                required=False,
            ),
            _check_json_artifact(
                code="gateway_state_file",
                label="telegram gateway state",
                path=gateway_state_file,
                required=False,
            ),
            _check_json_artifact(
                code="manager_state_file",
                label="manager state",
                path=resolved_manager_state_file,
                required=True,
            ),
            _check_json_artifact(
                code="chat_aliases_file",
                label="chat aliases",
                path=resolved_chat_aliases_file,
                required=False,
            ),
            _check_json_artifact(
                code="provider_capacity_file",
                label="provider capacity",
                path=provider_capacity_state_path(resolved_team_dir),
                required=False,
            ),
            _check_json_artifact(
                code="latest_intent_file",
                label="latest intent",
                path=latest_intent_snapshot_path(resolved_team_dir),
                required=False,
            ),
            _check_text_artifact(
                code="action_audit_file",
                label="dashboard action audit",
                path=action_audit_path(resolved_team_dir),
                required=False,
            ),
            _check_json_artifact(
                code="recovery_summary_file",
                label="nightly recovery summary",
                path=recovery_summary_latest_path(resolved_team_dir),
                required=False,
            ),
            _binary_check(
                code="aoe_orch_bin",
                label="aoe-orch",
                binary=str(aoe_orch_bin or os.environ.get("AOE_ORCH_BIN", DEFAULT_AOE_ORCH_BIN)),
                which=which,
            ),
            _binary_check(
                code="aoe_team_bin",
                label="aoe-team",
                binary=str(aoe_team_bin or os.environ.get("AOE_TEAM_BIN", DEFAULT_AOE_TEAM_BIN)),
                which=which,
            ),
            _binary_check(
                code="tmux_bin",
                label="tmux",
                binary="tmux",
                which=which,
                required=False,
            ),
        ]
    )

    fail_count = sum(1 for row in checks if row.status == "fail")
    warn_count = sum(1 for row in checks if row.status == "warn")
    info_count = sum(1 for row in checks if row.status == "info")
    ok_count = sum(1 for row in checks if row.status == "ok")
    return DoctorReport(
        project_root=str(root),
        team_dir=str(resolved_team_dir),
        state_root_mode=str(state_root.get("mode", "")).strip() or "-",
        state_root_path=str(state_root.get("path", "")).strip() or str(resolved_team_dir),
        project_id=stable_project_id(root),
        gateway_state_file=str(gateway_state_file),
        manager_state_file=str(resolved_manager_state_file),
        chat_aliases_file=str(resolved_chat_aliases_file),
        checks=checks,
        fail_count=fail_count,
        warn_count=warn_count,
        info_count=info_count,
        ok_count=ok_count,
    )


def render_doctor_report(report: DoctorReport) -> str:
    lines = [
        "doctor",
        f"- project_root: {report.project_root}",
        f"- team_dir: {report.team_dir}",
        f"- state_root: {report.state_root_mode} | {report.state_root_path}",
        f"- project_id: {report.project_id}",
        f"- gateway_state_file: {report.gateway_state_file}",
        f"- manager_state_file: {report.manager_state_file}",
        f"- chat_aliases_file: {report.chat_aliases_file}",
        "",
        "checks:",
    ]
    for row in report.checks:
        lines.append(f"- [{row.status}] {row.code}: {row.summary}")
        if row.detail:
            lines.append(f"  detail: {row.detail}")
        if row.next_step:
            lines.append(f"  next: {row.next_step}")
    lines.extend(
        [
            "",
            "summary:",
            f"- ok: {report.ok_count}",
            f"- warn: {report.warn_count}",
            f"- info: {report.info_count}",
            f"- fail: {report.fail_count}",
        ]
    )
    return "\n".join(lines).strip()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check owner-only gateway runtime health")
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
    report = collect_doctor_report(
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
                    **{key: value for key, value in asdict(report).items() if key != "checks"},
                    "checks": [asdict(row) for row in report.checks],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(render_doctor_report(report))
    return 1 if report.fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
