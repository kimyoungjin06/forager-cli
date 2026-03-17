#!/usr/bin/env python3
"""Generate file-based nightly session summaries for the Recovery Loop."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts" / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "dashboard"))

from control_dashboard_state import (
    DashboardSnapshotDTO,
    RuntimeDetailDTO,
    TaskDetailDTO,
    load_dashboard_runtime_details,
    now_iso,
    task_detail_from_state,
)


def _safe_stamp(iso_text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(iso_text or "").strip()) or "summary"


def _default_output_dir(snapshot: DashboardSnapshotDTO) -> Path:
    return Path(snapshot.team_dir).expanduser().resolve() / "recovery" / "nightly-session-summary"


def _automation_posture(snapshot: DashboardSnapshotDTO) -> str:
    auto_mode = str(snapshot.control_summary.auto_mode).strip().lower()
    offdesk_mode = str(snapshot.control_summary.offdesk_mode).strip().lower()
    if auto_mode not in {"", "off"}:
        return f"auto_active ({auto_mode})"
    if offdesk_mode == "on":
        return "offdesk_only"
    return "inactive"


def _runtime_has_activity(detail: RuntimeDetailDTO) -> bool:
    if detail.active_task_request_id:
        return True
    if detail.completed_task_count > 0 or detail.blocked_task_count > 0 or detail.parked_task_count > 0:
        return True
    if detail.repeat_summary != "-":
        return True
    if detail.status != "ready":
        return True
    return False


def _task_rows_for_runtime(manager_state: Dict[str, Any], detail: RuntimeDetailDTO, *, cap: int = 5) -> List[TaskDetailDTO]:
    rows: List[TaskDetailDTO] = []
    seen: set[str] = set()
    for row in detail.recent_tasks:
        request_id = str(row.request_id or "").strip()
        if not request_id or request_id in seen:
            continue
        task = task_detail_from_state(manager_state, request_id)
        if task is None:
            continue
        rows.append(task)
        seen.add(request_id)
        if len(rows) >= max(1, int(cap)):
            break
    return rows


def _task_summary_dict(task: TaskDetailDTO) -> Dict[str, Any]:
    return {
        "request_id": task.request_id,
        "label": task.label,
        "status": task.status,
        "tf_phase": task.tf_phase,
        "preset": {
            "phase1": task.phase1_role_preset or "-",
            "phase2": task.phase2_team_preset or "-",
        },
        "phase2_shape": task.phase2_shape,
        "phase2_quality": task.phase2_quality,
        "lane_summary": task.lane_summary,
        "rerun_summary": task.rerun_summary,
        "followup_summary": task.followup_summary,
        "completion_contract": {
            "focus": task.completion_focus,
            "done_when": task.completion_done_when,
            "rerun_when": task.completion_rerun_when,
            "manual_followup_when": task.completion_followup_when,
        },
        "backend_summary": task.backend_summary,
        "backend_note": task.backend_note,
        "rate_limit_summary": task.rate_limit_summary,
        "updated_at": task.updated_at,
    }


def build_nightly_session_summary(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Dict[str, Any]:
    generated_at = now_iso()
    snapshot, runtime_details, manager_state = load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    active_details = [detail for detail in runtime_details if _runtime_has_activity(detail)]
    if not active_details:
        active_details = runtime_details

    runtimes: List[Dict[str, Any]] = []
    for detail in active_details:
        task_rows = _task_rows_for_runtime(manager_state, detail)
        runtimes.append(
            {
                "project_key": detail.project_key,
                "project_alias": detail.project_alias,
                "project_label": detail.project_label,
                "runtime_path": detail.runtime_path,
                "status": detail.status,
                "readiness": detail.readiness,
                "attention_summary": detail.attention_summary,
                "priority_action": detail.priority_action,
                "priority_reason": detail.priority_reason,
                "next_focus": detail.next_focus,
                "completed_task_count": detail.completed_task_count,
                "blocked_task_count": detail.blocked_task_count,
                "parked_task_count": detail.parked_task_count,
                "queue_summary": detail.queue_summary,
                "proposal_summary": detail.proposal_summary,
                "sync_summary": detail.sync_summary,
                "provider_pressure_summary": detail.provider_pressure_summary,
                "repeat_summary": detail.repeat_summary,
                "active_task_request_id": detail.active_task_request_id,
                "active_task_label": detail.active_task_label,
                "active_task_phase": detail.active_task_phase,
                "active_task_status": detail.active_task_status,
                "active_task_preset": detail.active_task_preset,
                "active_task_phase2_shape": detail.active_task_phase2_shape,
                "active_task_phase2_quality": detail.active_task_phase2_quality,
                "active_task_completion_contract": {
                    "focus": detail.active_task_completion_focus,
                    "done_when": detail.active_task_completion_done,
                    "rerun_when": detail.active_task_completion_rerun,
                    "manual_followup_when": detail.active_task_completion_followup,
                },
                "active_task_backend": detail.active_task_backend,
                "active_task_backend_note": detail.active_task_backend_note,
                "active_task_rate_limit": detail.active_task_rate_limit,
                "notes": list(detail.notes),
                "task_teams": [_task_summary_dict(task) for task in task_rows],
            }
        )

    return {
        "generated_at": generated_at,
        "snapshot_taken_at": snapshot.snapshot_taken_at,
        "control_root": snapshot.control_root,
        "team_dir": snapshot.team_dir,
        "manager_state_file": snapshot.manager_state_file,
        "control_summary": {
            "auto_mode": snapshot.control_summary.auto_mode,
            "offdesk_mode": snapshot.control_summary.offdesk_mode,
            "automation_posture": _automation_posture(snapshot),
            "provider_capacity_summary": snapshot.control_summary.provider_capacity_summary,
            "next_retry_at": snapshot.control_summary.next_retry_at,
            "next_retry_target": snapshot.control_summary.next_retry_target,
            "repeat_memory_summary": snapshot.control_summary.repeat_memory_summary,
            "active_runtime_count": snapshot.control_summary.active_runtime_count,
            "attention_runtime_count": snapshot.control_summary.attention_runtime_count,
        },
        "source_files": [asdict(row) for row in snapshot.source_files],
        "runtimes": runtimes,
    }


def render_nightly_session_summary(summary: Dict[str, Any]) -> str:
    control = summary.get("control_summary") if isinstance(summary.get("control_summary"), dict) else {}
    runtimes = summary.get("runtimes") if isinstance(summary.get("runtimes"), list) else []
    lines: List[str] = [
        "# Nightly Session Summary",
        "",
        "## Control Plane Summary",
        f"- generated_at: {summary.get('generated_at', '-')}",
        f"- snapshot_taken_at: {summary.get('snapshot_taken_at', '-')}",
        f"- automation_posture: {control.get('automation_posture', '-')}",
        f"- auto_mode: {control.get('auto_mode', '-')}",
        f"- offdesk_mode: {control.get('offdesk_mode', '-')}",
        f"- provider_capacity: {control.get('provider_capacity_summary', '-')}",
        f"- next_retry_at: {control.get('next_retry_at', '-')}",
        f"- next_retry_target: {control.get('next_retry_target', '-')}",
        f"- repeat_memory: {control.get('repeat_memory_summary', '-')}",
        "",
    ]
    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue
        lines.extend(
            [
                f"## {runtime.get('project_alias', '-')} {runtime.get('project_label', '-')}",
                f"- runtime: {runtime.get('readiness', '-')}",
                f"- status: {runtime.get('status', '-')}",
                f"- attention: {runtime.get('attention_summary', '-')}",
                f"- completed_tasks: {runtime.get('completed_task_count', 0)}",
                f"- blocked_tasks: {runtime.get('blocked_task_count', 0)}",
                f"- parked_tasks: {runtime.get('parked_task_count', 0)}",
                f"- first: {runtime.get('priority_action', '-')} | {runtime.get('priority_reason', '-')}",
                f"- next_focus: {runtime.get('next_focus', '-') or '-'}",
                f"- queue: {runtime.get('queue_summary', '-')}",
                f"- proposals: {runtime.get('proposal_summary', '-')}",
                f"- sync: {runtime.get('sync_summary', '-')}",
                f"- provider_pressure: {runtime.get('provider_pressure_summary', '-')}",
                f"- repeat_memory: {runtime.get('repeat_summary', '-')}",
            ]
        )
        active_task_label = str(runtime.get("active_task_label", "")).strip()
        if active_task_label:
            lines.extend(
                [
                    "- active_task:",
                    f"  - label: {active_task_label}",
                    f"  - status: {runtime.get('active_task_status', '-')}/{runtime.get('active_task_phase', '-')}",
                    f"  - preset: {runtime.get('active_task_preset', '-')}",
                    f"  - phase2_shape: {runtime.get('active_task_phase2_shape', '-')}",
                    f"  - phase2_quality: {runtime.get('active_task_phase2_quality', '-')}",
                    f"  - completion_focus: {((runtime.get('active_task_completion_contract') or {}).get('focus', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - done_when: {((runtime.get('active_task_completion_contract') or {}).get('done_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - rerun_when: {((runtime.get('active_task_completion_contract') or {}).get('rerun_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - manual_followup_when: {((runtime.get('active_task_completion_contract') or {}).get('manual_followup_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - backend: {runtime.get('active_task_backend', '-')}",
                    f"  - backend_note: {runtime.get('active_task_backend_note', '-') or '-'}",
                    f"  - rate_limit: {runtime.get('active_task_rate_limit', '-')}",
                ]
            )
        task_teams = runtime.get("task_teams") if isinstance(runtime.get("task_teams"), list) else []
        if task_teams:
            lines.append("- task_teams:")
            for task in task_teams:
                if not isinstance(task, dict):
                    continue
                preset = task.get("preset") if isinstance(task.get("preset"), dict) else {}
                lines.extend(
                    [
                        f"  - {task.get('label', '-')} ({task.get('request_id', '-')})",
                        f"    - status: {task.get('status', '-')}/{task.get('tf_phase', '-')}",
                        "    - preset: phase1={phase1} phase2={phase2}".format(
                            phase1=preset.get("phase1", "-"),
                            phase2=preset.get("phase2", "-"),
                        ),
                        f"    - phase2_shape: {task.get('phase2_shape', '-')}",
                        f"    - phase2_quality: {task.get('phase2_quality', '-')}",
                        f"    - completion_focus: {((task.get('completion_contract') or {}).get('focus', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - done_when: {((task.get('completion_contract') or {}).get('done_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - rerun_when: {((task.get('completion_contract') or {}).get('rerun_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - manual_followup_when: {((task.get('completion_contract') or {}).get('manual_followup_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - lanes: {task.get('lane_summary', '-')}",
                        f"    - rerun: {task.get('rerun_summary', '-')}",
                        f"    - followup: {task.get('followup_summary', '-')}",
                        f"    - backend: {task.get('backend_summary', '-')}",
                        f"    - backend_note: {task.get('backend_note', '-') or '-'}",
                        f"    - rate_limit: {task.get('rate_limit_summary', '-')}",
                    ]
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_nightly_session_summary(
    *,
    summary: Dict[str, Any],
    output_dir: Path,
    write_timestamped_copy: bool = True,
) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_nightly_session_summary(summary)
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    latest_md = output_dir / "latest.md"
    latest_json = output_dir / "latest.json"
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    if write_timestamped_copy:
        stamp = _safe_stamp(str(summary.get("generated_at", "")))
        (output_dir / f"{stamp}.md").write_text(markdown, encoding="utf-8")
        (output_dir / f"{stamp}.json").write_text(payload, encoding="utf-8")
    return latest_md, latest_json


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a nightly session summary artifact")
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--team-dir")
    parser.add_argument("--manager-state-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--latest-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    control_root = Path(args.control_root).expanduser().resolve()
    team_dir = Path(args.team_dir).expanduser().resolve() if args.team_dir else None
    manager_state_file = Path(args.manager_state_file).expanduser().resolve() if args.manager_state_file else None
    summary = build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else (Path(str(summary.get("team_dir", "."))).expanduser().resolve() / "recovery" / "nightly-session-summary")
    )
    latest_md, latest_json = write_nightly_session_summary(
        summary=summary,
        output_dir=output_dir,
        write_timestamped_copy=not bool(args.latest_only),
    )
    print(f"nightly summary written: {latest_md}")
    print(f"nightly summary json: {latest_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
