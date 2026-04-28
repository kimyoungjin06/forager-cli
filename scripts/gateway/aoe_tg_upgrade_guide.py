#!/usr/bin/env python3
"""Post-upgrade guidance for owner-only gateway deployments."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set

import aoe_tg_doctor as doctor
import aoe_tg_setup_guide as setup_guide
from aoe_tg_runtime_core import resolve_project_root, resolve_team_dir


EXPECTED_GITHUB_ACTION_MAJORS: Dict[str, str] = {
    "actions/checkout": "v6",
    "actions/setup-python": "v6",
    "actions/upload-artifact": "v7",
    "actions/download-artifact": "v8",
}

ACTION_REF_RE = re.compile(r"uses:\s*(actions/[A-Za-z0-9_.-]+)@(v[0-9]+(?:\.[0-9]+){0,2})")


@dataclass(frozen=True)
class UpgradeStep:
    code: str
    status: str
    summary: str
    command: str = ""
    detail: str = ""


@dataclass(frozen=True)
class UpgradeGuideReport:
    project_root: str
    team_dir: str
    state_root_mode: str
    state_root_path: str
    steps: List[UpgradeStep]
    ready_count: int
    pending_count: int
    warn_count: int
    fail_count: int


def _script_command(root: Path, script_name: str, extra: Sequence[str] = ()) -> str:
    tokens = ["python3", str(root / "scripts" / "gateway" / script_name), "--project-root", str(root), *extra]
    return " ".join(tokens)


def _major(ref: str) -> str:
    return str(ref or "").split(".", 1)[0]


def _workflow_action_refs(root: Path) -> Dict[str, Set[str]]:
    refs: Dict[str, Set[str]] = {}
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        for action, ref in ACTION_REF_RE.findall(text):
            refs.setdefault(action, set()).add(ref)
    return refs


def _workflow_runtime_step(root: Path) -> UpgradeStep:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.exists():
        return UpgradeStep(
            code="github_workflow_actions",
            status="warn",
            summary="GitHub workflow directory missing",
            detail=str(workflows_dir),
        )

    refs = _workflow_action_refs(root)
    mismatches: List[str] = []
    missing: List[str] = []
    matched: List[str] = []
    for action, expected_major in EXPECTED_GITHUB_ACTION_MAJORS.items():
        observed = sorted(refs.get(action, set()))
        if not observed:
            missing.append(f"{action}@{expected_major}")
            continue
        wrong = [ref for ref in observed if _major(ref) != expected_major]
        if wrong:
            mismatches.append(f"{action}: expected {expected_major}, found {', '.join(observed)}")
        else:
            matched.append(f"{action}@{expected_major}")

    if mismatches:
        return UpgradeStep(
            code="github_workflow_actions",
            status="fail",
            summary="GitHub helper actions are not on the expected Node 24-capable majors",
            command="rg -n \"uses:\\s*actions/\" .github/workflows",
            detail="; ".join(mismatches),
        )
    if missing:
        return UpgradeStep(
            code="github_workflow_actions",
            status="warn",
            summary="some expected GitHub helper actions were not found",
            command="rg -n \"uses:\\s*actions/\" .github/workflows",
            detail=", ".join(missing),
        )
    return UpgradeStep(
        code="github_workflow_actions",
        status="ready",
        summary="GitHub helper actions are on Node 24-capable majors",
        detail=", ".join(matched),
    )


def collect_upgrade_guide(
    *,
    project_root: Path | str,
    team_dir: Optional[str] = None,
    state_file: Optional[str] = None,
    manager_state_file: Optional[str] = None,
    chat_aliases_file: Optional[str] = None,
    aoe_orch_bin: Optional[str] = None,
    aoe_team_bin: Optional[str] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> UpgradeGuideReport:
    root = resolve_project_root(str(project_root))
    resolved_team_dir = resolve_team_dir(root, team_dir)
    doctor_report = doctor.collect_doctor_report(
        project_root=root,
        team_dir=team_dir,
        state_file=state_file,
        manager_state_file=manager_state_file,
        chat_aliases_file=chat_aliases_file,
        aoe_orch_bin=aoe_orch_bin,
        aoe_team_bin=aoe_team_bin,
        which=which,
    )
    setup_report = setup_guide.collect_setup_guide(
        project_root=root,
        team_dir=team_dir,
        state_file=state_file,
        manager_state_file=manager_state_file,
        chat_aliases_file=chat_aliases_file,
        aoe_orch_bin=aoe_orch_bin,
        aoe_team_bin=aoe_team_bin,
        which=which,
    )

    doctor_cmd = _script_command(root, "aoe_tg_doctor.py")
    setup_cmd = _script_command(root, "aoe_tg_setup_guide.py")
    migration = next((row for row in doctor_report.checks if row.code == "state_root_legacy_fallback"), None)

    steps: List[UpgradeStep] = []
    if doctor_report.fail_count:
        steps.append(
            UpgradeStep(
                code="doctor_baseline",
                status="fail",
                summary="doctor has failing checks",
                command=doctor_cmd,
                detail=f"ok={doctor_report.ok_count} warn={doctor_report.warn_count} fail={doctor_report.fail_count}",
            )
        )
    elif doctor_report.warn_count:
        steps.append(
            UpgradeStep(
                code="doctor_baseline",
                status="warn",
                summary="doctor has warnings to review",
                command=doctor_cmd,
                detail=f"ok={doctor_report.ok_count} warn={doctor_report.warn_count} fail=0",
            )
        )
    else:
        steps.append(
            UpgradeStep(
                code="doctor_baseline",
                status="ready",
                summary="doctor baseline has no failures or warnings",
                command=doctor_cmd,
                detail=f"ok={doctor_report.ok_count} info={doctor_report.info_count}",
            )
        )

    if migration is not None:
        steps.append(
            UpgradeStep(
                code="state_root_migration",
                status="pending",
                summary="migrate legacy state into the configured centralized root",
                command=migration.next_step,
                detail=migration.detail,
            )
        )
    else:
        steps.append(
            UpgradeStep(
                code="state_root_migration",
                status="ready",
                summary="state root migration is not required",
                detail=f"{doctor_report.state_root_mode} | {doctor_report.state_root_path}",
            )
        )

    critical_setup_pending = [
        row.code
        for row in setup_report.steps
        if row.status == "pending" and row.code in {"runtime_bootstrap", "runtime_env"}
    ]
    if critical_setup_pending:
        steps.append(
            UpgradeStep(
                code="setup_guidance",
                status="pending",
                summary="critical setup steps are still pending",
                command=setup_cmd,
                detail=", ".join(critical_setup_pending),
            )
        )
    else:
        steps.append(
            UpgradeStep(
                code="setup_guidance",
                status="ready",
                summary="critical setup steps are covered",
                command=setup_cmd,
                detail=f"ready={setup_report.ready_count} pending={setup_report.pending_count} warn={setup_report.warn_count}",
            )
        )

    steps.append(_workflow_runtime_step(root))
    steps.append(
        UpgradeStep(
            code="local_gateway_suite",
            status="pending",
            summary="run the full gateway regression suite after upgrade changes",
            command=f"bash {root / 'scripts' / 'gateway_full_test.sh'}",
            detail="required before merging workflow, migration, runtime-state, or setup changes",
        )
    )

    ready_count = sum(1 for row in steps if row.status == "ready")
    pending_count = sum(1 for row in steps if row.status == "pending")
    warn_count = sum(1 for row in steps if row.status == "warn")
    fail_count = sum(1 for row in steps if row.status == "fail")
    return UpgradeGuideReport(
        project_root=str(root),
        team_dir=str(resolved_team_dir),
        state_root_mode=doctor_report.state_root_mode,
        state_root_path=doctor_report.state_root_path,
        steps=steps,
        ready_count=ready_count,
        pending_count=pending_count,
        warn_count=warn_count,
        fail_count=fail_count,
    )


def render_upgrade_guide(report: UpgradeGuideReport) -> str:
    lines = [
        "upgrade guide",
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
            f"- fail: {report.fail_count}",
        ]
    )
    return "\n".join(lines).strip()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show owner-only runtime upgrade guidance")
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
    report = collect_upgrade_guide(
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
        print(render_upgrade_guide(report))
    return 1 if report.fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
