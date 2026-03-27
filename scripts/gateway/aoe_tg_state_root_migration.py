#!/usr/bin/env python3
"""Copy-first migration helper from legacy `.aoe-team` into centralized state root."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from aoe_tg_chat_aliases import resolve_chat_aliases_file
from aoe_tg_runtime_core import (
    action_audit_path,
    latest_intent_snapshot_path,
    provider_capacity_state_path,
    recovery_summary_dir,
    resolve_centralized_team_dir,
    resolve_project_root,
    stable_project_id,
)


@dataclass(frozen=True)
class MigrationRow:
    label: str
    source: Path
    target: Path
    kind: str
    action: str


@dataclass(frozen=True)
class StateRootMigrationPlan:
    project_root: Path
    source_team_dir: Path
    target_state_root: Path
    target_team_dir: Path
    project_id: str
    rows: List[MigrationRow]


def _legacy_source_team_dir(project_root: Path, explicit_team_dir: Optional[str]) -> Path:
    if explicit_team_dir:
        return Path(explicit_team_dir).expanduser().resolve()
    env_dir = str(os.environ.get("AOE_TEAM_DIR", "")).strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return (project_root / ".aoe-team").resolve()


def _target_state_root(explicit_state_dir: Optional[str]) -> Path:
    raw = str(explicit_state_dir or "").strip() or str(os.environ.get("AOE_STATE_DIR", "")).strip()
    if not raw:
        raise SystemExit("[ERROR] missing AOE_STATE_DIR (set env or pass --state-dir)")
    return Path(raw).expanduser().resolve()


def _default_manager_state_file(team_dir: Path, explicit_manager_state_file: Optional[str]) -> Path:
    if explicit_manager_state_file:
        return Path(explicit_manager_state_file).expanduser().resolve()
    return (team_dir / "orch_manager_state.json").resolve()


def _default_gateway_state_file(team_dir: Path, explicit_state_file: Optional[str]) -> Path:
    if explicit_state_file:
        return Path(explicit_state_file).expanduser().resolve()
    return (team_dir / "telegram_gateway_state.json").resolve()


def _artifact_rows(
    *,
    source_team_dir: Path,
    target_team_dir: Path,
    explicit_state_file: Optional[str] = None,
    explicit_manager_state_file: Optional[str] = None,
    explicit_chat_aliases_file: Optional[str] = None,
) -> List[MigrationRow]:
    rows: List[MigrationRow] = []

    file_pairs = [
        ("gateway_state", _default_gateway_state_file(source_team_dir, explicit_state_file), _default_gateway_state_file(target_team_dir, None)),
        ("manager_state", _default_manager_state_file(source_team_dir, explicit_manager_state_file), _default_manager_state_file(target_team_dir, None)),
        ("chat_aliases", resolve_chat_aliases_file(source_team_dir, explicit_chat_aliases_file), resolve_chat_aliases_file(target_team_dir, None)),
        ("auto_state", source_team_dir / "auto_scheduler.json", target_team_dir / "auto_scheduler.json"),
        ("offdesk_state", source_team_dir / "offdesk_state.json", target_team_dir / "offdesk_state.json"),
        ("provider_capacity", provider_capacity_state_path(source_team_dir), provider_capacity_state_path(target_team_dir)),
        ("latest_intent", latest_intent_snapshot_path(source_team_dir), latest_intent_snapshot_path(target_team_dir)),
        ("action_audit", action_audit_path(source_team_dir), action_audit_path(target_team_dir)),
        ("gateway_events", source_team_dir / "logs" / "gateway_events.jsonl", target_team_dir / "logs" / "gateway_events.jsonl"),
    ]

    for label, source, target in file_pairs:
        if source == target:
            action = "noop"
        elif not source.exists():
            action = "missing"
        elif target.exists():
            action = "skip_exists"
        else:
            action = "copy"
        rows.append(MigrationRow(label=label, source=source, target=target, kind="file", action=action))

    summary_root = recovery_summary_dir(source_team_dir)
    if summary_root.exists():
        for path in sorted(p for p in summary_root.rglob("*") if p.is_file()):
            relative = path.relative_to(summary_root)
            target = recovery_summary_dir(target_team_dir) / relative
            if path == target:
                action = "noop"
            elif target.exists():
                action = "skip_exists"
            else:
                action = "copy"
            rows.append(MigrationRow(label=f"recovery_summary:{relative}", source=path, target=target, kind="file", action=action))
    else:
        rows.append(
            MigrationRow(
                label="recovery_summary",
                source=summary_root,
                target=recovery_summary_dir(target_team_dir),
                kind="dir",
                action="missing",
            )
        )
    return rows


def build_state_root_migration_plan(
    *,
    project_root: Path | str,
    explicit_team_dir: Optional[str] = None,
    explicit_state_dir: Optional[str] = None,
    explicit_state_file: Optional[str] = None,
    explicit_manager_state_file: Optional[str] = None,
    explicit_chat_aliases_file: Optional[str] = None,
) -> StateRootMigrationPlan:
    root = resolve_project_root(str(project_root))
    source_team_dir = _legacy_source_team_dir(root, explicit_team_dir)
    target_state_root = _target_state_root(explicit_state_dir)
    target_team_dir = resolve_centralized_team_dir(root, target_state_root)
    return StateRootMigrationPlan(
        project_root=root,
        source_team_dir=source_team_dir,
        target_state_root=target_state_root,
        target_team_dir=target_team_dir,
        project_id=stable_project_id(root),
        rows=_artifact_rows(
            source_team_dir=source_team_dir,
            target_team_dir=target_team_dir,
            explicit_state_file=explicit_state_file,
            explicit_manager_state_file=explicit_manager_state_file,
            explicit_chat_aliases_file=explicit_chat_aliases_file,
        ),
    )


def apply_state_root_migration(plan: StateRootMigrationPlan, *, force: bool = False) -> List[MigrationRow]:
    applied: List[MigrationRow] = []
    for row in plan.rows:
        if row.action not in {"copy", "skip_exists"}:
            applied.append(row)
            continue
        if row.action == "skip_exists" and not force:
            applied.append(row)
            continue
        if row.kind != "file":
            applied.append(row)
            continue
        if not row.source.exists():
            applied.append(MigrationRow(label=row.label, source=row.source, target=row.target, kind=row.kind, action="missing"))
            continue
        row.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(row.source, row.target)
        applied.append(MigrationRow(label=row.label, source=row.source, target=row.target, kind=row.kind, action="copied"))
    return applied


def render_state_root_migration_plan(
    plan: StateRootMigrationPlan,
    *,
    applied_rows: Optional[Iterable[MigrationRow]] = None,
) -> str:
    rows = list(applied_rows) if applied_rows is not None else plan.rows
    counts = {
        "copy": sum(1 for row in rows if row.action in {"copy", "copied"}),
        "skip_exists": sum(1 for row in rows if row.action == "skip_exists"),
        "missing": sum(1 for row in rows if row.action == "missing"),
        "noop": sum(1 for row in rows if row.action == "noop"),
    }
    lines = [
        "state root migration",
        f"- project_root: {plan.project_root}",
        f"- project_id: {plan.project_id}",
        f"- source_team_dir: {plan.source_team_dir}",
        f"- target_state_root: {plan.target_state_root}",
        f"- target_team_dir: {plan.target_team_dir}",
        f"- copy: {counts['copy']}",
        f"- skip_exists: {counts['skip_exists']}",
        f"- missing: {counts['missing']}",
        f"- noop: {counts['noop']}",
        "",
        "artifacts:",
    ]
    for row in rows:
        lines.append(f"- {row.action} {row.label}")
        lines.append(f"  src: {row.source}")
        lines.append(f"  dst: {row.target}")
    return "\n".join(lines).strip()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy .aoe-team runtime state into AOE_STATE_DIR")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--team-dir")
    parser.add_argument("--state-dir")
    parser.add_argument("--state-file")
    parser.add_argument("--manager-state-file")
    parser.add_argument("--chat-aliases-file")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    plan = build_state_root_migration_plan(
        project_root=args.project_root,
        explicit_team_dir=args.team_dir,
        explicit_state_dir=args.state_dir,
        explicit_state_file=args.state_file,
        explicit_manager_state_file=args.manager_state_file,
        explicit_chat_aliases_file=args.chat_aliases_file,
    )
    if args.apply:
        rows = apply_state_root_migration(plan, force=bool(args.force))
        print(render_state_root_migration_plan(plan, applied_rows=rows))
        return 0
    print(render_state_root_migration_plan(plan))
    print("")
    print("next:")
    print("- rerun with --apply to copy missing artifacts into the centralized state root")
    if any(row.action == "skip_exists" for row in plan.rows):
        print("- add --force to overwrite already-present target files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
