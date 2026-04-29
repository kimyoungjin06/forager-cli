#!/usr/bin/env python3
"""Read-only retention policy and disk hygiene report."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Sequence

import aoe_tg_gateway_state as gateway_state
import aoe_tg_room_runtime as room_runtime
import aoe_tg_tf_exec as tf_exec
from aoe_tg_runtime_core import (
    action_audit_path,
    provider_capacity_state_path,
    recovery_summary_dir,
    resolve_project_root,
    resolve_team_dir,
)


DEFAULT_GATEWAY_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_GATEWAY_LOG_KEEP_FILES = 5
DEFAULT_GATEWAY_DEDUP_KEEP = 2000
DEFAULT_FAILED_QUEUE_KEEP = 200
DEFAULT_FAILED_QUEUE_TTL_HOURS = 168
DEFAULT_TF_EXEC_CACHE_TTL_HOURS = 72
DEFAULT_ROOM_RETENTION_DAYS = 14
DEFAULT_ACTION_AUDIT_RETENTION_DAYS = 14
DEFAULT_ACTION_AUDIT_KEEP_ROWS = 500
DEFAULT_TF_EXEC_MAP_FILE = "tf_exec_map.json"


@dataclass(frozen=True)
class RetentionKnob:
    name: str
    value: Any
    default: Any
    unit: str
    note: str


@dataclass(frozen=True)
class PathObservation:
    path: str
    exists: bool
    kind: str
    file_count: int
    bytes: int
    row_count: int
    note: str = ""


@dataclass(frozen=True)
class RetentionPolicyRow:
    storage_class: str
    status: str
    policy: str
    cleanup_surface: str
    knobs: List[RetentionKnob]
    paths: List[PathObservation]
    notes: List[str]


@dataclass(frozen=True)
class RetentionReport:
    project_root: str
    team_dir: str
    generated_at: str
    policy_doc: str
    rows: List[RetentionPolicyRow]
    warn_count: int
    checked_path_count: int
    existing_bytes: int


def _int_from_env(raw: object, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(raw if raw is not None else "").strip())
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))


def _line_count(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except Exception:
        return 0


def _observe_path(path: Path, *, note: str = "", max_files: int = 10000) -> PathObservation:
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        return PathObservation(
            path=str(resolved),
            exists=False,
            kind="missing",
            file_count=0,
            bytes=0,
            row_count=0,
            note=note,
        )
    if resolved.is_file():
        try:
            size = resolved.stat().st_size
        except OSError:
            size = 0
        return PathObservation(
            path=str(resolved),
            exists=True,
            kind="file",
            file_count=1,
            bytes=size,
            row_count=_line_count(resolved) if resolved.suffix == ".jsonl" else 0,
            note=note,
        )
    if not resolved.is_dir():
        return PathObservation(
            path=str(resolved),
            exists=True,
            kind="other",
            file_count=0,
            bytes=0,
            row_count=0,
            note=note,
        )

    file_count = 0
    total_bytes = 0
    truncated = False
    try:
        for child in resolved.rglob("*"):
            if not child.is_file():
                continue
            file_count += 1
            try:
                total_bytes += child.stat().st_size
            except OSError:
                pass
            if file_count >= max_files:
                truncated = True
                break
    except Exception:
        truncated = True
    suffix = " (truncated)" if truncated else ""
    return PathObservation(
        path=str(resolved),
        exists=True,
        kind="dir",
        file_count=file_count,
        bytes=total_bytes,
        row_count=0,
        note=(note + suffix).strip(),
    )


def _knob(name: str, value: Any, default: Any, unit: str, note: str) -> RetentionKnob:
    return RetentionKnob(name=name, value=value, default=default, unit=unit, note=note)


def _gateway_log_max_bytes() -> int:
    return _int_from_env(
        os.environ.get("AOE_GATEWAY_LOG_MAX_BYTES"),
        DEFAULT_GATEWAY_LOG_MAX_BYTES,
        minimum=64 * 1024,
        maximum=256 * 1024 * 1024,
    )


def _gateway_log_keep_files() -> int:
    return _int_from_env(
        os.environ.get("AOE_GATEWAY_LOG_KEEP_FILES"),
        DEFAULT_GATEWAY_LOG_KEEP_FILES,
        minimum=1,
        maximum=30,
    )


def _action_audit_retention_days() -> int:
    return _int_from_env(
        os.environ.get("AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS"),
        DEFAULT_ACTION_AUDIT_RETENTION_DAYS,
        minimum=0,
        maximum=3650,
    )


def _action_audit_keep_rows() -> int:
    return _int_from_env(
        os.environ.get("AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS"),
        DEFAULT_ACTION_AUDIT_KEEP_ROWS,
        minimum=10,
        maximum=10000,
    )


def _warn_if(condition: bool, note: str, notes: List[str]) -> bool:
    if condition:
        notes.append(note)
        return True
    return False


def build_retention_report(*, project_root: Path | str, team_dir: Optional[str] = None) -> RetentionReport:
    root = resolve_project_root(str(project_root))
    resolved_team_dir = resolve_team_dir(root, team_dir)
    policy_doc = root / "docs" / "STORAGE_RETENTION_POLICY.md"

    dedup_keep = gateway_state.dedup_keep_limit(
        int_from_env=_int_from_env,
        default_keep=DEFAULT_GATEWAY_DEDUP_KEEP,
    )
    failed_keep = gateway_state.failed_queue_keep_limit(
        int_from_env=_int_from_env,
        default_keep=DEFAULT_FAILED_QUEUE_KEEP,
    )
    failed_ttl_hours = gateway_state.failed_queue_ttl_hours(
        int_from_env=_int_from_env,
        default_ttl_hours=DEFAULT_FAILED_QUEUE_TTL_HOURS,
    )
    room_retention_days = room_runtime.room_retention_days(
        int_from_env=_int_from_env,
        default_room_retention_days=DEFAULT_ROOM_RETENTION_DAYS,
    )
    tf_artifact_policy = tf_exec.normalize_tf_exec_retention()
    tf_exec_ttl_hours = tf_exec.tf_exec_cache_ttl_hours(
        int_from_env=_int_from_env,
        default_ttl_hours=DEFAULT_TF_EXEC_CACHE_TTL_HOURS,
    )
    action_audit_retention_days = _action_audit_retention_days()
    action_audit_keep_rows = _action_audit_keep_rows()

    rows: List[RetentionPolicyRow] = []

    rows.append(
        RetentionPolicyRow(
            storage_class="canonical_runtime_state",
            status="ready",
            policy="Keep current live state; never treat these files as disposable caches.",
            cleanup_surface="No automatic cleanup. Use backup/migration flows, not /gc.",
            knobs=[],
            paths=[
                _observe_path(resolved_team_dir / "orch_manager_state.json", note="manager state"),
                _observe_path(resolved_team_dir / "auto_scheduler.json", note="scheduler state"),
                _observe_path(provider_capacity_state_path(resolved_team_dir), note="provider capacity memory"),
                _observe_path(resolved_team_dir / "telegram_gateway_state.json", note="gateway poll/replay state"),
            ],
            notes=[
                "Missing files usually mean the runtime has not initialized that surface yet.",
                "Retention is controlled by state migration and backup policy, not TTL.",
            ],
        )
    )

    rows.append(
        RetentionPolicyRow(
            storage_class="evidence_and_artifacts",
            status="manual",
            policy="Retain long enough for morning recovery and audit; prune only under explicit archival rules.",
            cleanup_surface="Manual archival policy; learned runbook and nightly summary readers consume this evidence.",
            knobs=[],
            paths=[
                _observe_path(recovery_summary_dir(resolved_team_dir), note="nightly recovery summaries"),
                _observe_path(resolved_team_dir / "background_run_results", note="external/background run results"),
                _observe_path(resolved_team_dir / "background_run_handoffs", note="external/background run handoffs"),
                _observe_path(root / "docs" / "investigations_mo" / "registry", note="project evidence registry"),
            ],
            notes=[
                "This class intentionally has no automatic TTL yet.",
                "Move old material into archive storage before adding destructive cleanup.",
            ],
        )
    )

    ephemeral_notes: List[str] = []
    ephemeral_warn = False
    ephemeral_warn |= _warn_if(
        tf_artifact_policy == "all",
        "AOE_TF_ARTIFACT_POLICY=all keeps TF execution artifacts indefinitely.",
        ephemeral_notes,
    )
    ephemeral_warn |= _warn_if(
        tf_artifact_policy != "all" and tf_exec_ttl_hours <= 0,
        "AOE_TF_EXEC_CACHE_TTL_HOURS=0 disables success-cache TTL cleanup.",
        ephemeral_notes,
    )
    if not ephemeral_notes:
        ephemeral_notes.append("TF execution cache is bounded by artifact policy and TTL.")
    rows.append(
        RetentionPolicyRow(
            storage_class="ephemeral_runtime_artifacts",
            status="warn" if ephemeral_warn else "ready",
            policy="TTL-based cleanup is expected; transient run output should not silently become evidence.",
            cleanup_surface="/gc, /gc force, and TF execution artifact cleanup",
            knobs=[
                _knob(
                    "AOE_TF_ARTIFACT_POLICY",
                    tf_artifact_policy,
                    "success-only",
                    "policy",
                    "success-only|all|none; all ignores TTL",
                ),
                _knob(
                    "AOE_TF_EXEC_CACHE_TTL_HOURS",
                    tf_exec_ttl_hours,
                    DEFAULT_TF_EXEC_CACHE_TTL_HOURS,
                    "hours",
                    "0 disables TTL cleanup; ignored when policy=all",
                ),
            ],
            paths=[
                _observe_path(resolved_team_dir / "tf_runs", note="TF execution run directories"),
                _observe_path(resolved_team_dir / DEFAULT_TF_EXEC_MAP_FILE, note="TF execution artifact map"),
            ],
            notes=ephemeral_notes,
        )
    )

    logs_notes: List[str] = []
    logs_warn = False
    logs_warn |= _warn_if(
        room_retention_days <= 0,
        "AOE_ROOM_RETENTION_DAYS=0 disables room log cleanup.",
        logs_notes,
    )
    logs_warn |= _warn_if(
        failed_ttl_hours <= 0,
        "AOE_GATEWAY_FAILED_TTL_HOURS=0 disables failed replay queue TTL; keep-count cap still applies.",
        logs_notes,
    )
    logs_warn |= _warn_if(
        action_audit_retention_days <= 0,
        "AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS=0 disables time-based audit pruning; row cap still applies.",
        logs_notes,
    )
    if not logs_notes:
        logs_notes.append("Logs, rooms, replay queue, and dashboard action audit all have bounded retention knobs.")
    rows.append(
        RetentionPolicyRow(
            storage_class="logs_and_rooms",
            status="warn" if logs_warn else "ready",
            policy="Retain for operational debugging and replay; rotate or prune on explicit retention windows.",
            cleanup_surface="/gc status, /gc, gateway event log rotation, failed queue normalization, dashboard audit pruning",
            knobs=[
                _knob(
                    "AOE_GATEWAY_LOG_MAX_BYTES",
                    _gateway_log_max_bytes(),
                    DEFAULT_GATEWAY_LOG_MAX_BYTES,
                    "bytes",
                    "gateway JSONL rotation threshold",
                ),
                _knob(
                    "AOE_GATEWAY_LOG_KEEP_FILES",
                    _gateway_log_keep_files(),
                    DEFAULT_GATEWAY_LOG_KEEP_FILES,
                    "files",
                    "rotated gateway event logs to retain",
                ),
                _knob(
                    "AOE_GATEWAY_DEDUP_KEEP",
                    dedup_keep,
                    DEFAULT_GATEWAY_DEDUP_KEEP,
                    "tokens",
                    "recent update/message dedup cache cap",
                ),
                _knob(
                    "AOE_GATEWAY_FAILED_KEEP",
                    failed_keep,
                    DEFAULT_FAILED_QUEUE_KEEP,
                    "rows",
                    "failed replay queue cap",
                ),
                _knob(
                    "AOE_GATEWAY_FAILED_TTL_HOURS",
                    failed_ttl_hours,
                    DEFAULT_FAILED_QUEUE_TTL_HOURS,
                    "hours",
                    "0 disables failed replay queue TTL",
                ),
                _knob(
                    "AOE_ROOM_RETENTION_DAYS",
                    room_retention_days,
                    DEFAULT_ROOM_RETENTION_DAYS,
                    "days",
                    "0 disables room log cleanup",
                ),
                _knob(
                    "AOE_DASHBOARD_ACTION_AUDIT_RETENTION_DAYS",
                    action_audit_retention_days,
                    DEFAULT_ACTION_AUDIT_RETENTION_DAYS,
                    "days",
                    "0 disables time-based audit pruning",
                ),
                _knob(
                    "AOE_DASHBOARD_ACTION_AUDIT_KEEP_ROWS",
                    action_audit_keep_rows,
                    DEFAULT_ACTION_AUDIT_KEEP_ROWS,
                    "rows",
                    "dashboard action audit row cap",
                ),
            ],
            paths=[
                _observe_path(resolved_team_dir / "logs", note="gateway and room logs"),
                _observe_path(resolved_team_dir / "logs" / "rooms", note="room logs pruned by /gc"),
                _observe_path(action_audit_path(resolved_team_dir), note="dashboard action audit"),
                _observe_path(resolved_team_dir / "telegram_gateway_state.json", note="failed queue and dedup cache live here"),
            ],
            notes=logs_notes,
        )
    )

    checked_path_count = sum(len(row.paths) for row in rows)
    existing_bytes = sum(path.bytes for row in rows for path in row.paths if path.exists)
    warn_count = sum(1 for row in rows if row.status == "warn")
    return RetentionReport(
        project_root=str(root),
        team_dir=str(resolved_team_dir),
        generated_at=datetime.now().astimezone().replace(microsecond=0).isoformat(),
        policy_doc=str(policy_doc.resolve()),
        rows=rows,
        warn_count=warn_count,
        checked_path_count=checked_path_count,
        existing_bytes=existing_bytes,
    )


def render_retention_report(report: RetentionReport) -> str:
    lines = [
        "retention policy disk hygiene report",
        f"- project_root: {report.project_root}",
        f"- team_dir: {report.team_dir}",
        f"- policy_doc: {report.policy_doc}",
        f"- checked_paths: {report.checked_path_count}",
        f"- existing_bytes: {report.existing_bytes}",
        f"- warnings: {report.warn_count}",
        "",
        "storage classes:",
    ]
    for row in report.rows:
        lines.append(f"- {row.storage_class} [{row.status}]")
        lines.append(f"  policy: {row.policy}")
        lines.append(f"  cleanup: {row.cleanup_surface}")
        if row.knobs:
            lines.append("  knobs:")
            for knob in row.knobs:
                lines.append(
                    f"  - {knob.name}: {knob.value} {knob.unit} "
                    f"(default {knob.default}; {knob.note})"
                )
        if row.paths:
            lines.append("  paths:")
            for path in row.paths:
                row_count = f", rows={path.row_count}" if path.row_count else ""
                note = f", {path.note}" if path.note else ""
                lines.append(
                    f"  - {path.path}: {path.kind}, exists={str(path.exists).lower()}, "
                    f"files={path.file_count}, bytes={path.bytes}{row_count}{note}"
                )
        if row.notes:
            lines.append("  notes:")
            for note in row.notes:
                lines.append(f"  - {note}")
    return "\n".join(lines).strip()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report retention policy and disk hygiene surfaces.")
    parser.add_argument("--project-root", default=".", help="project root")
    parser.add_argument("--team-dir", default=None, help="override .aoe-team directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    report = build_retention_report(project_root=args.project_root, team_dir=args.team_dir)
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(render_retention_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
