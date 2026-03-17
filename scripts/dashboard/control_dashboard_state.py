#!/usr/bin/env python3
"""Read-only dashboard DTO assembly for the Control Plane board."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_offdesk_flow as offdesk_flow
import aoe_tg_ops_policy as ops_policy
import aoe_tg_orch_contract as orch_contract
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as task_state
import aoe_tg_task_view as task_view


MANAGER_STATE_FILENAME = "orch_manager_state.json"
AUTO_STATE_FILENAME = "auto_scheduler.json"
PROVIDER_CAPACITY_FILENAME = "provider_capacity.json"
RECOVERY_SUMMARY_DIRNAME = "nightly-session-summary"
RECOVERY_SUMMARY_FILENAME = "latest.json"

_LAST_GOOD_JSON: Dict[str, Dict[str, Any]] = {}
_LAST_GOOD_MANAGER_STATE: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class FileFreshnessDTO:
    name: str
    path: str
    exists: bool
    updated_at: str = ""
    stale: bool = False
    error: str = ""


@dataclass(frozen=True)
class ControlSummaryDTO:
    auto_mode: str
    offdesk_mode: str
    provider_capacity_summary: str
    next_retry_at: str
    next_retry_target: str
    repeat_memory_summary: str
    active_runtime_count: int
    attention_runtime_count: int
    snapshot_taken_at: str


@dataclass(frozen=True)
class RuntimeCardDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    severity_score: int
    provider_pressure_score: int
    provider_repeat_count: int
    active_task_request_id: str
    active_task_label: str
    active_task_phase: str
    active_task_status: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_backend: str
    notes: List[str] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActiveTaskRowDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    request_id: str
    label: str
    status: str
    stage: str
    tf_phase: str
    preset: str
    phase2_shape: str
    lane_summary: str
    backend_summary: str
    updated_at: str
    detail_path: str


@dataclass(frozen=True)
class TaskDetailDTO:
    project_key: str
    project_alias: str
    project_label: str
    request_id: str
    label: str
    status: str
    tf_phase: str
    mode: str
    prompt: str
    roles: List[str] = field(default_factory=list)
    verifier_roles: List[str] = field(default_factory=list)
    phase1_summary: str = ""
    phase1_progress: str = ""
    phase1_candidate_roles: List[str] = field(default_factory=list)
    phase1_role_preset: str = ""
    phase2_team_preset: str = ""
    phase2_shape: str = ""
    phase2_quality: str = ""
    lane_summary: str = ""
    rerun_summary: str = ""
    followup_summary: str = ""
    completion_focus: str = ""
    completion_done_when: str = ""
    completion_rerun_when: str = ""
    completion_followup_when: str = ""
    backend_summary: str = ""
    backend_note: str = ""
    rate_limit_summary: str = ""
    updated_at: str = ""
    reference_lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeDetailDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    completed_task_count: int
    blocked_task_count: int
    parked_task_count: int
    queue_summary: str
    proposal_summary: str
    sync_summary: str
    provider_pressure_summary: str
    repeat_summary: str
    active_task_request_id: str
    active_task_label: str
    active_task_path: str
    active_task_phase: str
    active_task_status: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_completion_focus: str
    active_task_completion_done: str
    active_task_completion_rerun: str
    active_task_completion_followup: str
    active_task_backend: str
    active_task_backend_note: str
    active_task_rate_limit: str
    notes: List[str] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)
    recent_tasks: List[ActiveTaskRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveryTaskDTO:
    request_id: str
    label: str
    detail_path: str
    status: str
    tf_phase: str
    preset: str
    phase2_shape: str
    phase2_quality: str
    lane_summary: str
    rerun_summary: str
    followup_summary: str
    completion_focus: str
    completion_done_when: str
    completion_rerun_when: str
    completion_followup_when: str
    backend_summary: str
    backend_note: str
    rate_limit_summary: str


@dataclass(frozen=True)
class RecoveryRuntimeDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    queue_summary: str
    proposal_summary: str
    sync_summary: str
    provider_pressure_summary: str
    repeat_summary: str
    completed_task_count: int
    blocked_task_count: int
    parked_task_count: int
    active_task_label: str
    active_task_path: str
    active_task_status: str
    active_task_phase: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_completion_focus: str
    active_task_completion_done: str
    active_task_completion_rerun: str
    active_task_completion_followup: str
    active_task_backend: str
    active_task_backend_note: str
    active_task_rate_limit: str
    task_teams: List[RecoveryTaskDTO] = field(default_factory=list)


@dataclass(frozen=True)
class RecoverySummaryDTO:
    exists: bool
    artifact_path: str
    updated_at: str
    stale: bool
    error: str
    generated_at: str
    snapshot_taken_at: str
    automation_posture: str
    auto_mode: str
    offdesk_mode: str
    provider_capacity_summary: str
    next_retry_at: str
    next_retry_target: str
    repeat_memory_summary: str
    runtimes: List[RecoveryRuntimeDTO] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardSnapshotDTO:
    control_root: str
    team_dir: str
    manager_state_file: str
    snapshot_taken_at: str
    source_files: List[FileFreshnessDTO]
    control_summary: ControlSummaryDTO
    runtime_cards: List[RuntimeCardDTO]
    attention_runtime_cards: List[RuntimeCardDTO]
    active_task_rows: List[ActiveTaskRowDTO]


@dataclass(frozen=True)
class DashboardSnapshotLoadResult:
    snapshot: DashboardSnapshotDTO
    manager_state: Dict[str, Any]
    provider_state: Dict[str, Any]


@dataclass(frozen=True)
class ControlPaths:
    control_root: Path
    team_dir: Path
    manager_state_file: Path
    auto_state_file: Path
    provider_capacity_file: Path


@dataclass(frozen=True)
class ManagerStateLoadResult:
    state: Dict[str, Any]
    freshness: FileFreshnessDTO


def now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _iso_from_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).astimezone().replace(microsecond=0).isoformat()
    except Exception:
        return ""


def resolve_control_paths(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> ControlPaths:
    root = Path(control_root).expanduser().resolve()
    resolved_manager = Path(manager_state_file).expanduser().resolve() if manager_state_file else None
    if team_dir:
        resolved_team_dir = Path(team_dir).expanduser().resolve()
    elif resolved_manager is not None:
        resolved_team_dir = resolved_manager.parent.resolve()
    else:
        resolved_team_dir = (root / ".aoe-team").resolve()
    if resolved_manager is None:
        resolved_manager = (resolved_team_dir / MANAGER_STATE_FILENAME).resolve()
    return ControlPaths(
        control_root=root,
        team_dir=resolved_team_dir,
        manager_state_file=resolved_manager,
        auto_state_file=(resolved_team_dir / AUTO_STATE_FILENAME).resolve(),
        provider_capacity_file=(resolved_team_dir / PROVIDER_CAPACITY_FILENAME).resolve(),
    )


def _load_json_file(path: Path, *, name: str) -> Tuple[Dict[str, Any], FileFreshnessDTO]:
    key = str(path)
    exists = path.exists()
    updated_at = _iso_from_mtime(path) if exists else ""
    if not exists:
        return {}, FileFreshnessDTO(name=name, path=key, exists=False, updated_at="")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("json root is not an object")
        _LAST_GOOD_JSON[key] = data
        return data, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at)
    except Exception as exc:
        cached = _LAST_GOOD_JSON.get(key)
        if isinstance(cached, dict):
            return cached, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc))
        return {}, FileFreshnessDTO(name=name, path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc))


def _load_manager_state(paths: ControlPaths) -> ManagerStateLoadResult:
    key = str(paths.manager_state_file)
    exists = paths.manager_state_file.exists()
    updated_at = _iso_from_mtime(paths.manager_state_file) if exists else ""
    if not exists:
        state = runtime_read.default_manager_state(paths.control_root, paths.team_dir)
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=False, updated_at=""),
        )

    try:
        json.loads(paths.manager_state_file.read_text(encoding="utf-8"))
        state = runtime_read.load_manager_state(paths.manager_state_file, paths.control_root, paths.team_dir)
        _LAST_GOOD_MANAGER_STATE[key] = state
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at),
        )
    except Exception as exc:
        cached = _LAST_GOOD_MANAGER_STATE.get(key)
        if isinstance(cached, dict):
            return ManagerStateLoadResult(
                state=cached,
                freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc)),
            )
        state = runtime_read.default_manager_state(paths.control_root, paths.team_dir)
        return ManagerStateLoadResult(
            state=state,
            freshness=FileFreshnessDTO(name="manager_state", path=key, exists=True, updated_at=updated_at, stale=True, error=str(exc)),
        )


def _provider_summary_text(memory_state: Dict[str, Any]) -> str:
    task_count = int(memory_state.get("task_count", 0) or 0)
    project_count = int(memory_state.get("project_count", 0) or 0)
    provider_counts = memory_state.get("provider_counts") if isinstance(memory_state.get("provider_counts"), dict) else {}
    parts: List[str] = []
    for key in sorted(provider_counts.keys()):
        try:
            count = int(provider_counts.get(key, 0) or 0)
        except Exception:
            count = 0
        parts.append(f"{key}={count}")
    if task_count or project_count or parts:
        return f"tasks={task_count} projects={project_count} providers={', '.join(parts) if parts else '-'}"
    providers = memory_state.get("providers") if isinstance(memory_state.get("providers"), dict) else {}
    if providers:
        bits: List[str] = []
        for name in sorted(str(key).strip().lower() for key in providers.keys() if str(key).strip()):
            row = providers.get(name) if isinstance(providers.get(name), dict) else {}
            bits.append(f"{name}={int(row.get('blocked_count', 0) or 0)}")
        return "providers=" + ", ".join(bits)
    return "-"


def _repeat_summary_text(memory_state: Dict[str, Any]) -> str:
    count = int(memory_state.get("recovery_repeat_count", 0) or 0)
    last_at = str(memory_state.get("recovery_repeat_last_at", "")).strip()
    repeat = memory_state.get("recovery_repeat") if isinstance(memory_state.get("recovery_repeat"), dict) else {}
    latest = str(repeat.get("summary", "")).strip()
    if count <= 0 and not latest:
        return "-"
    text = f"count={count}"
    if latest:
        text += f" latest={latest}"
    if last_at:
        text += f" last={last_at}"
    return text


def _next_retry_target_text(memory_state: Dict[str, Any]) -> str:
    target = memory_state.get("next_retry_target") if isinstance(memory_state.get("next_retry_target"), dict) else {}
    if not target:
        return "-"
    alias = str(target.get("alias", "")).strip() or "-"
    task_ref = str(target.get("task_ref", "")).strip() or "-"
    providers = str(target.get("providers", "")).strip() or "-"
    degraded = str(target.get("degraded", "")).strip() or "-"
    return f"{alias} {task_ref} providers={providers} degraded={degraded}"


def _compose_phase2_shape(exec_roles: Iterable[str], review_roles: Iterable[str]) -> str:
    exec_text = ",".join(str(role).strip() for role in exec_roles if str(role).strip()) or "-"
    review_text = ",".join(str(role).strip() for role in review_roles if str(role).strip()) or "-"
    return f"exec={exec_text} | review={review_text}"


def _compose_phase2_quality(card: Dict[str, Any]) -> str:
    parts: List[str] = []
    critic = str(card.get("active_task_phase2_quality_critic", "")).strip()
    integration = str(card.get("active_task_phase2_quality_integration", "")).strip()
    evidence = [str(x).strip() for x in (card.get("active_task_phase2_evidence") or []) if str(x).strip()]
    if critic:
        parts.append(f"critic={critic}")
    if integration:
        parts.append(f"integration={integration}")
    if evidence:
        parts.append("evidence=" + " / ".join(evidence[:2]))
    return " | ".join(parts) if parts else "-"


def _compose_task_quality(task: Dict[str, Any]) -> str:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    team_spec = meta.get("phase2_team_spec") if isinstance(meta.get("phase2_team_spec"), dict) else {}
    critic = str(team_spec.get("critic_role", "")).strip()
    integration = str(team_spec.get("integration_role", "")).strip()
    evidence = [str(x).strip() for x in (plan.get("evidence_required") or []) if str(x).strip()]
    parts: List[str] = []
    if critic:
        parts.append(f"critic={critic}")
    if integration:
        parts.append(f"integration={integration}")
    if evidence:
        parts.append("evidence=" + " / ".join(evidence[:2]))
    return " | ".join(parts) if parts else "-"


def _compose_backend_summary(backend: str, profile: str, verdict: str, contract: str) -> str:
    parts = [part for part in [backend, profile] if str(part).strip()]
    if verdict:
        parts.append(f"verdict={verdict}")
    if contract:
        parts.append(f"contract={contract}")
    return " | ".join(parts) if parts else "-"


def _compose_lane_summary(row: Dict[str, Any]) -> str:
    lane_parts: List[str] = [f"E{int(row.get('execution_lane_count', 0) or 0)}/R{int(row.get('review_lane_count', 0) or 0)}"]
    exec_summary = row.get("execution_summary") if isinstance(row.get("execution_summary"), dict) else {}
    review_summary = row.get("review_summary") if isinstance(row.get("review_summary"), dict) else {}
    review_verdicts = row.get("review_verdicts") if isinstance(row.get("review_verdicts"), dict) else {}
    if exec_summary:
        lane_parts.append("exec " + ",".join(f"{key}={value}" for key, value in sorted(exec_summary.items())))
    if review_summary:
        lane_parts.append("review " + ",".join(f"{key}={value}" for key, value in sorted(review_summary.items())))
    if review_verdicts:
        lane_parts.append("verdict " + ",".join(f"{key}={value}" for key, value in sorted(review_verdicts.items())))
    return " | ".join(lane_parts)


def _task_phase1_summary(task: Dict[str, Any]) -> str:
    mode = str(task.get("phase1_mode", "")).strip() or "single"
    rounds = max(0, int(task.get("phase1_rounds", 0) or 0)) or 1
    providers = task_view.dedupe_roles(task.get("phase1_providers") or [])
    return f"{mode} rounds={rounds} providers={', '.join(providers) if providers else '-'}"


def _task_phase1_progress(task: Dict[str, Any]) -> str:
    current_phase = str(task.get("phase1_current_phase", "")).strip() or "planning"
    current_round = max(0, int(task.get("phase1_current_round", 0) or 0))
    current_total = max(0, int(task.get("phase1_current_total_rounds", 0) or 0))
    parts = [current_phase]
    if current_round and current_total:
        parts.append(f"{current_round}/{current_total}")
    provider = str(task.get("phase1_current_provider", "")).strip()
    planner = str(task.get("phase1_current_planner", "")).strip()
    critic = str(task.get("phase1_current_critic", "")).strip()
    if provider:
        parts.append(f"provider={provider}")
    if planner:
        parts.append(f"planner={planner}")
    if critic:
        parts.append(f"critic={critic}")
    return " ".join(parts)


def _task_rerun_summary(task: Dict[str, Any]) -> str:
    critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    exec_ids = [str(x).strip() for x in (critic.get("rerun_execution_lane_ids") or []) if str(x).strip()]
    review_ids = [str(x).strip() for x in (critic.get("rerun_review_lane_ids") or []) if str(x).strip()]
    if not exec_ids and not review_ids:
        return "-"
    return f"execution={','.join(exec_ids) if exec_ids else '-'} | review={','.join(review_ids) if review_ids else '-'}"


def _task_followup_summary(task: Dict[str, Any]) -> str:
    critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    exec_ids = [str(x).strip() for x in (critic.get("manual_followup_execution_lane_ids") or []) if str(x).strip()]
    review_ids = [str(x).strip() for x in (critic.get("manual_followup_review_lane_ids") or []) if str(x).strip()]
    if not exec_ids and not review_ids:
        return "-"
    return f"execution={','.join(exec_ids) if exec_ids else '-'} | review={','.join(review_ids) if review_ids else '-'}"


def _task_rate_limit_summary(task: Dict[str, Any]) -> str:
    rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
    if not rate_limit:
        return "-"
    providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
    retry_at = str(rate_limit.get("retry_at", "")).strip() or "-"
    retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
    return "mode={mode} providers={providers} retry_after={retry_after} retry_at={retry_at}".format(
        mode=str(rate_limit.get("mode", "")).strip() or "-",
        providers=",".join(providers) if providers else "-",
        retry_after=(f"{retry_after}s" if retry_after > 0 else "-"),
        retry_at=retry_at,
    )


def _completion_contract_for_preset(raw: Any) -> Dict[str, str]:
    return orch_contract.preset_completion_contract(raw)


def _runtime_path(project_alias: str) -> str:
    return f"/control/runtimes/{quote(str(project_alias or '').strip(), safe='')}"


def _detail_path(request_id: str) -> str:
    return f"/control/tasks/by-request/{quote(str(request_id or '').strip(), safe='')}"


def _recovery_summary_path(team_dir: Path) -> Path:
    return team_dir / "recovery" / RECOVERY_SUMMARY_DIRNAME / RECOVERY_SUMMARY_FILENAME


def _provider_repeat_counts(provider_state: Dict[str, Any]) -> Dict[str, int]:
    repeat_history = provider_state.get("recovery_repeat_history") if isinstance(provider_state.get("recovery_repeat_history"), list) else []
    repeat_counts: Dict[str, int] = {}
    for row in repeat_history:
        if not isinstance(row, dict):
            continue
        for alias in row.get("aliases") or []:
            token = str(alias or "").strip().upper()
            if token:
                repeat_counts[token] = int(repeat_counts.get(token, 0) or 0) + 1
    return repeat_counts


def _runtime_reports(manager_state: Dict[str, Any], provider_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    reports: List[Dict[str, Any]] = []
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    repeat_counts = _provider_repeat_counts(provider_state)
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        report = dict(offdesk_flow.offdesk_prepare_project_report(manager_state, key, entry))
        alias = str(report.get("alias", "")).strip().upper()
        report["capacity_repeat_count"] = int(repeat_counts.get(alias, 0) or 0)
        reports.append(report)
    return offdesk_flow.sort_offdesk_reports(reports)


def _build_runtime_cards(manager_state: Dict[str, Any], provider_state: Dict[str, Any]) -> List[RuntimeCardDTO]:
    reports = _runtime_reports(manager_state, provider_state)

    cards: List[RuntimeCardDTO] = []
    for row in reports:
        phase1_preset = str(row.get("active_task_phase1_role_preset", "")).strip()
        phase2_preset = str(row.get("active_task_phase2_team_preset", "")).strip()
        alias = str(row.get("alias", "")).strip()
        cards.append(
            RuntimeCardDTO(
                project_key=str(row.get("key", "")).strip() or str(row.get("alias", "")).strip(),
                project_alias=alias,
                project_label=str(row.get("display", "")).strip() or alias,
                runtime_path=_runtime_path(alias),
                status=str(row.get("status", "ready")).strip(),
                readiness=str(row.get("runtime_label", row.get("runtime", "ready"))).strip() or "ready",
                attention_summary=str(row.get("attention_summary", "-")).strip() or "-",
                priority_action=str(row.get("priority_action", "")).strip() or "-",
                priority_reason=str(row.get("priority_reason", "")).strip() or "-",
                next_focus=offdesk_flow.preset_next_focus(phase1_preset, phase2_preset),
                severity_score=int(row.get("severity_score", 0) or 0),
                provider_pressure_score=int(row.get("capacity_pressure_score", 0) or 0),
                provider_repeat_count=int(row.get("capacity_repeat_count", 0) or 0),
                active_task_request_id=str(row.get("active_task_request_id", "")).strip(),
                active_task_label=str(row.get("active_task_label", "")).strip(),
                active_task_phase=str(row.get("active_task_tf_phase", "")).strip(),
                active_task_status=str(row.get("active_task_status", "")).strip(),
                active_task_preset=f"phase1={phase1_preset or '-'} phase2={phase2_preset or phase1_preset or '-'}" if (phase1_preset or phase2_preset) else "-",
                active_task_phase2_shape=_compose_phase2_shape(
                    row.get("active_task_phase2_execution_roles") or [],
                    row.get("active_task_phase2_review_roles") or [],
                ),
                active_task_phase2_quality=_compose_phase2_quality(row),
                active_task_backend=_compose_backend_summary(
                    str(row.get("active_task_backend", "")).strip(),
                    str(row.get("active_task_backend_profile", "")).strip(),
                    str(row.get("active_task_backend_verdict", "")).strip(),
                    str(row.get("active_task_backend_contract", "")).strip(),
                ),
                notes=list(row.get("notes") or []),
                lines=list(row.get("lines") or []),
            )
        )
    return cards


def _build_active_task_rows(manager_state: Dict[str, Any], *, cap: int = 60) -> List[ActiveTaskRowDTO]:
    rows: List[ActiveTaskRowDTO] = []
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key)
        display = str(entry.get("display_name", "")).strip() or str(key)
        tasks = task_state.ensure_project_tasks(entry)
        for request_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            status = runtime_read.normalize_task_status(task.get("status", "pending"))
            if status == "completed":
                continue
            snap = task_state.task_monitor_row_snapshot(
                task,
                str(request_id or "").strip(),
                normalize_task_status=runtime_read.normalize_task_status,
                task_display_label=task_view.task_display_label,
            )
            preset = str(snap.get("phase2_team_preset", "")).strip() or str(snap.get("phase1_role_preset", "")).strip() or "-"
            rows.append(
                ActiveTaskRowDTO(
                    project_key=str(key),
                    project_alias=alias,
                    project_label=display,
                    runtime_path=_runtime_path(alias),
                    request_id=str(snap.get("request_id", "")).strip(),
                    label=str(snap.get("label", "")).strip(),
                    status=str(snap.get("status", "")).strip(),
                    stage=str(snap.get("stage", "")).strip(),
                    tf_phase=str(snap.get("tf_phase", "")).strip(),
                    preset=preset,
                    phase2_shape=_compose_phase2_shape(snap.get("phase2_execution_roles") or [], snap.get("phase2_review_roles") or []),
                    lane_summary=_compose_lane_summary(snap),
                    backend_summary=_compose_backend_summary(
                        str(snap.get("backend", "")).strip(),
                        str(snap.get("backend_profile", "")).strip(),
                        str(snap.get("backend_verdict", "")).strip(),
                        str(snap.get("backend_contract", "")).strip(),
                    ),
                    updated_at=str(snap.get("updated_at", "")).strip(),
                    detail_path=_detail_path(str(request_id or "").strip()),
                )
            )
    rows.sort(key=lambda row: (row.updated_at, row.project_alias, row.request_id), reverse=True)
    return rows[: max(1, int(cap))]


def _build_runtime_recent_task_rows(
    entry: Dict[str, Any],
    *,
    project_key: str,
    project_alias: str,
    project_label: str,
    cap: int = 8,
) -> List[ActiveTaskRowDTO]:
    rows: List[ActiveTaskRowDTO] = []
    tasks = task_state.ensure_project_tasks(entry)
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        status = runtime_read.normalize_task_status(task.get("status", "pending"))
        if status == "canceled":
            continue
        snap = task_state.task_monitor_row_snapshot(
            task,
            str(request_id or "").strip(),
            normalize_task_status=runtime_read.normalize_task_status,
            task_display_label=task_view.task_display_label,
        )
        preset = str(snap.get("phase2_team_preset", "")).strip() or str(snap.get("phase1_role_preset", "")).strip() or "-"
        rows.append(
            ActiveTaskRowDTO(
                project_key=project_key,
                project_alias=project_alias,
                project_label=project_label,
                runtime_path=_runtime_path(project_alias),
                request_id=str(snap.get("request_id", "")).strip(),
                label=str(snap.get("label", "")).strip(),
                status=str(snap.get("status", "")).strip(),
                stage=str(snap.get("stage", "")).strip(),
                tf_phase=str(snap.get("tf_phase", "")).strip(),
                preset=preset,
                phase2_shape=_compose_phase2_shape(snap.get("phase2_execution_roles") or [], snap.get("phase2_review_roles") or []),
                lane_summary=_compose_lane_summary(snap),
                backend_summary=_compose_backend_summary(
                    str(snap.get("backend", "")).strip(),
                    str(snap.get("backend_profile", "")).strip(),
                    str(snap.get("backend_verdict", "")).strip(),
                    str(snap.get("backend_contract", "")).strip(),
                ),
                updated_at=str(snap.get("updated_at", "")).strip(),
                detail_path=_detail_path(str(request_id or "").strip()),
            )
        )
    rows.sort(key=lambda row: (row.updated_at, row.request_id), reverse=True)
    return rows[: max(1, int(cap))]


def _build_task_detail(manager_state: Dict[str, Any], request_id: str) -> Optional[TaskDetailDTO]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(request_id or "").strip()
    if not target:
        return None
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        task = task_state.get_task_record(entry, target)
        if not isinstance(task, dict):
            continue
        rid = task_state.resolve_task_request_id(entry, target)
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key)
        display = str(entry.get("display_name", "")).strip() or str(key)
        shape = task_state.task_phase2_shape_snapshot(task)
        lane = task_state.task_lane_summary_snapshot(task)
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        contract = _completion_contract_for_preset(str(task.get("phase2_team_preset", "")).strip() or str(task.get("phase1_role_preset", "")).strip())
        backend_summary = _compose_backend_summary(
            str(task.get("backend", "") or result.get("backend", "")).strip(),
            str(task.get("backend_profile", "") or result.get("backend_profile", "")).strip(),
            str(task.get("backend_verdict", "") or result.get("backend_verdict", "")).strip(),
            str(task.get("backend_contract", "") or result.get("backend_contract", "")).strip(),
        )
        return TaskDetailDTO(
            project_key=str(key),
            project_alias=alias,
            project_label=display,
            request_id=rid,
            label=task_view.task_display_label(task, fallback_request_id=rid),
            status=runtime_read.normalize_task_status(task.get("status", "pending")),
            tf_phase=str(task.get("tf_phase", "")).strip() or task_view.normalize_tf_phase(task_view.derive_tf_phase(task), "queued"),
            mode=str(task.get("mode", "dispatch")).strip(),
            prompt=str(task.get("prompt", "")).strip(),
            roles=task_view.dedupe_roles(task.get("roles") or []),
            verifier_roles=task_view.dedupe_roles(task.get("verifier_roles") or []),
            phase1_summary=_task_phase1_summary(task),
            phase1_progress=_task_phase1_progress(task),
            phase1_candidate_roles=task_view.dedupe_roles(task.get("phase1_candidate_roles") or []),
            phase1_role_preset=str(task.get("phase1_role_preset", "")).strip(),
            phase2_team_preset=str(task.get("phase2_team_preset", "")).strip(),
            phase2_shape=_compose_phase2_shape(shape["execution_roles"], shape["review_roles"]),
            phase2_quality=_compose_task_quality(task),
            lane_summary=_compose_lane_summary(lane),
            rerun_summary=_task_rerun_summary(task),
            followup_summary=_task_followup_summary(task),
            completion_focus=str(contract.get("focus", "")).strip() or "-",
            completion_done_when=str(contract.get("done_when", "")).strip() or "-",
            completion_rerun_when=str(contract.get("rerun_when", "")).strip() or "-",
            completion_followup_when=str(contract.get("manual_followup_when", "")).strip() or "-",
            backend_summary=backend_summary,
            backend_note=str(task.get("backend_contract_note", "") or result.get("backend_contract_note", "")).strip(),
            rate_limit_summary=_task_rate_limit_summary(task),
            updated_at=str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip(),
            reference_lines=task_view.summarize_task_lifecycle(display, task).splitlines(),
        )
    return None


def _runtime_detail_sync_summary(row: Dict[str, Any]) -> str:
    syncback = row.get("syncback_counts") if isinstance(row.get("syncback_counts"), dict) else {}
    return (
        "quality={quality} | syncback done={done} reopen={reopen} append={append} blocked={blocked}".format(
            quality=str(row.get("sync_quality", "")).strip() or "-",
            done=int(syncback.get("done", 0) or 0),
            reopen=int(syncback.get("reopen", 0) or 0),
            append=int(syncback.get("append", 0) or 0),
            blocked=int(syncback.get("blocked", 0) or 0),
        )
    )


def _runtime_detail_queue_summary(row: Dict[str, Any]) -> str:
    return "open={open} running={running} blocked={blocked} followup={followup} pending={pending}".format(
        open=int(row.get("open", 0) or 0),
        running=int(row.get("running", 0) or 0),
        blocked=int(row.get("blocked_count", 0) or 0),
        followup=int(row.get("followup_count", 0) or 0),
        pending="yes" if bool(row.get("pending_flag", False)) else "no",
    )


def _runtime_detail_proposal_summary(row: Dict[str, Any]) -> str:
    triage = row.get("proposal_triage") if isinstance(row.get("proposal_triage"), dict) else {}
    return "open={open} | priorities={priorities} | kinds={kinds}".format(
        open=int(row.get("proposals", 0) or 0),
        priorities=str(triage.get("priority_summary", "-")).strip() or "-",
        kinds=str(triage.get("kind_summary", "-")).strip() or "-",
    )


def _runtime_detail_provider_pressure_summary(row: Dict[str, Any]) -> str:
    retry_wait_sec = int(row.get("capacity_retry_wait_sec", 0) or 0)
    retry_wait = f"{retry_wait_sec}s" if retry_wait_sec > 0 else "-"
    return "score={score} | providers={providers} | retry_wait={wait}".format(
        score=int(row.get("capacity_pressure_score", 0) or 0),
        providers=int(row.get("capacity_provider_count", 0) or 0),
        wait=retry_wait,
    )


def _runtime_detail_repeat_summary(row: Dict[str, Any]) -> str:
    count = int(row.get("capacity_repeat_count", 0) or 0)
    if count <= 0:
        return "-"
    return f"repeat_count={count}"


def _runtime_active_task_rate_limit_summary(row: Dict[str, Any]) -> str:
    rate_limit = row.get("active_task_rate_limit") if isinstance(row.get("active_task_rate_limit"), dict) else {}
    if not rate_limit:
        return "-"
    providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
    retry_at = str(rate_limit.get("retry_at", "")).strip() or "-"
    retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
    degraded = [str(x).strip() for x in (row.get("active_task_degraded_by") or []) if str(x).strip()]
    return "mode={mode} providers={providers} retry_after={retry_after} retry_at={retry_at} degraded={degraded}".format(
        mode=str(rate_limit.get("mode", "")).strip() or "-",
        providers=",".join(providers) if providers else "-",
        retry_after=(f"{retry_after}s" if retry_after > 0 else "-"),
        retry_at=retry_at,
        degraded=",".join(degraded) if degraded else "-",
    )


def _resolve_runtime_entry(manager_state: Dict[str, Any], project_alias: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    token = str(project_alias or "").strip().upper()
    if not token:
        return None
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        if ops_policy.project_alias(entry, str(key)).upper() == token:
            return str(key), entry
    return None


def _build_runtime_detail(manager_state: Dict[str, Any], provider_state: Dict[str, Any], project_alias: str) -> Optional[RuntimeDetailDTO]:
    resolved = _resolve_runtime_entry(manager_state, project_alias)
    if resolved is None:
        return None
    key, entry = resolved
    reports = _runtime_reports(manager_state, provider_state)
    target_alias = ops_policy.project_alias(entry, key).upper()
    row = next((report for report in reports if str(report.get("alias", "")).strip().upper() == target_alias), None)
    if row is None:
        return None
    queue_snapshot = ops_policy.project_queue_snapshot(entry)
    completed_task_count = 0
    tasks = task_state.ensure_project_tasks(entry)
    for task in tasks.values():
        if not isinstance(task, dict):
            continue
        if runtime_read.normalize_task_status(task.get("status", "pending")) == "completed":
            completed_task_count += 1
    display = str(row.get("display", "")).strip() or target_alias
    phase1_preset = str(row.get("active_task_phase1_role_preset", "")).strip()
    phase2_preset = str(row.get("active_task_phase2_team_preset", "")).strip()
    active_contract = _completion_contract_for_preset(phase2_preset or phase1_preset)
    runtime_path = _runtime_path(target_alias)
    active_request_id = str(row.get("active_task_request_id", "")).strip()
    return RuntimeDetailDTO(
        project_key=key,
        project_alias=target_alias,
        project_label=display,
        runtime_path=runtime_path,
        status=str(row.get("status", "ready")).strip(),
        readiness=str(row.get("runtime_label", "ready")).strip() or "ready",
        attention_summary=str(row.get("attention_summary", "-")).strip() or "-",
        priority_action=str(row.get("priority_action", "-")).strip() or "-",
        priority_reason=str(row.get("priority_reason", "-")).strip() or "-",
        next_focus=offdesk_flow.preset_next_focus(phase1_preset, phase2_preset),
        completed_task_count=completed_task_count,
        blocked_task_count=int(queue_snapshot.get("blocked_count", 0) or 0),
        parked_task_count=int(queue_snapshot.get("parked_count", 0) or 0),
        queue_summary=_runtime_detail_queue_summary(row),
        proposal_summary=_runtime_detail_proposal_summary(row),
        sync_summary=_runtime_detail_sync_summary(row),
        provider_pressure_summary=_runtime_detail_provider_pressure_summary(row),
        repeat_summary=_runtime_detail_repeat_summary(row),
        active_task_request_id=active_request_id,
        active_task_label=str(row.get("active_task_label", "")).strip(),
        active_task_path=_detail_path(active_request_id) if active_request_id else "",
        active_task_phase=str(row.get("active_task_tf_phase", "")).strip(),
        active_task_status=str(row.get("active_task_status", "")).strip(),
        active_task_preset="phase1={phase1} phase2={phase2}".format(
            phase1=phase1_preset or "-",
            phase2=phase2_preset or phase1_preset or "-",
        ) if (phase1_preset or phase2_preset) else "-",
        active_task_phase2_shape=_compose_phase2_shape(
            row.get("active_task_phase2_execution_roles") or [],
            row.get("active_task_phase2_review_roles") or [],
        ),
        active_task_phase2_quality=_compose_phase2_quality(row),
        active_task_completion_focus=str(active_contract.get("focus", "")).strip() or "-",
        active_task_completion_done=str(active_contract.get("done_when", "")).strip() or "-",
        active_task_completion_rerun=str(active_contract.get("rerun_when", "")).strip() or "-",
        active_task_completion_followup=str(active_contract.get("manual_followup_when", "")).strip() or "-",
        active_task_backend=_compose_backend_summary(
            str(row.get("active_task_backend", "")).strip(),
            str(row.get("active_task_backend_profile", "")).strip(),
            str(row.get("active_task_backend_verdict", "")).strip(),
            str(row.get("active_task_backend_contract", "")).strip(),
        ),
        active_task_backend_note=str(row.get("active_task_backend_note", "")).strip(),
        active_task_rate_limit=_runtime_active_task_rate_limit_summary(row),
        notes=list(row.get("notes") or []),
        lines=list(row.get("lines") or []),
        recent_tasks=_build_runtime_recent_task_rows(
            entry,
            project_key=key,
            project_alias=target_alias,
            project_label=display,
        ),
    )


def _build_recovery_task_rows(rows: Iterable[Dict[str, Any]]) -> List[RecoveryTaskDTO]:
    built: List[RecoveryTaskDTO] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        preset = row.get("preset") if isinstance(row.get("preset"), dict) else {}
        contract = row.get("completion_contract") if isinstance(row.get("completion_contract"), dict) else {}
        built.append(
            RecoveryTaskDTO(
                request_id=str(row.get("request_id", "")).strip(),
                label=str(row.get("label", "")).strip() or "-",
                detail_path=_detail_path(str(row.get("request_id", "")).strip()),
                status=str(row.get("status", "")).strip() or "-",
                tf_phase=str(row.get("tf_phase", "")).strip() or "-",
                preset="phase1={phase1} phase2={phase2}".format(
                    phase1=str(preset.get("phase1", "")).strip() or "-",
                    phase2=str(preset.get("phase2", "")).strip() or "-",
                ),
                phase2_shape=str(row.get("phase2_shape", "")).strip() or "-",
                phase2_quality=str(row.get("phase2_quality", "")).strip() or "-",
                lane_summary=str(row.get("lane_summary", "")).strip() or "-",
                rerun_summary=str(row.get("rerun_summary", "")).strip() or "-",
                followup_summary=str(row.get("followup_summary", "")).strip() or "-",
                completion_focus=str(contract.get("focus", "")).strip() or "-",
                completion_done_when=str(contract.get("done_when", "")).strip() or "-",
                completion_rerun_when=str(contract.get("rerun_when", "")).strip() or "-",
                completion_followup_when=str(contract.get("manual_followup_when", "")).strip() or "-",
                backend_summary=str(row.get("backend_summary", "")).strip() or "-",
                backend_note=str(row.get("backend_note", "")).strip(),
                rate_limit_summary=str(row.get("rate_limit_summary", "")).strip() or "-",
            )
        )
    return built


def _build_recovery_runtime_rows(rows: Iterable[Dict[str, Any]]) -> List[RecoveryRuntimeDTO]:
    built: List[RecoveryRuntimeDTO] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        alias = str(row.get("project_alias", "")).strip().upper()
        label = str(row.get("project_label", "")).strip() or alias or "-"
        active_request_id = str(row.get("active_task_request_id", "")).strip()
        active_contract = row.get("active_task_completion_contract") if isinstance(row.get("active_task_completion_contract"), dict) else {}
        built.append(
            RecoveryRuntimeDTO(
                project_key=str(row.get("project_key", "")).strip() or alias,
                project_alias=alias or "-",
                project_label=label,
                runtime_path=_runtime_path(alias),
                status=str(row.get("status", "")).strip() or "-",
                readiness=str(row.get("readiness", "")).strip() or "-",
                attention_summary=str(row.get("attention_summary", "")).strip() or "-",
                priority_action=str(row.get("priority_action", "")).strip() or "-",
                priority_reason=str(row.get("priority_reason", "")).strip() or "-",
                next_focus=str(row.get("next_focus", "")).strip() or "-",
                queue_summary=str(row.get("queue_summary", "")).strip() or "-",
                proposal_summary=str(row.get("proposal_summary", "")).strip() or "-",
                sync_summary=str(row.get("sync_summary", "")).strip() or "-",
                provider_pressure_summary=str(row.get("provider_pressure_summary", "")).strip() or "-",
                repeat_summary=str(row.get("repeat_summary", "")).strip() or "-",
                completed_task_count=int(row.get("completed_task_count", 0) or 0),
                blocked_task_count=int(row.get("blocked_task_count", 0) or 0),
                parked_task_count=int(row.get("parked_task_count", 0) or 0),
                active_task_label=str(row.get("active_task_label", "")).strip(),
                active_task_path=_detail_path(active_request_id) if active_request_id else "",
                active_task_status=str(row.get("active_task_status", "")).strip() or "-",
                active_task_phase=str(row.get("active_task_phase", "")).strip() or "-",
                active_task_preset=str(row.get("active_task_preset", "")).strip() or "-",
                active_task_phase2_shape=str(row.get("active_task_phase2_shape", "")).strip() or "-",
                active_task_phase2_quality=str(row.get("active_task_phase2_quality", "")).strip() or "-",
                active_task_completion_focus=str(active_contract.get("focus", "")).strip() or "-",
                active_task_completion_done=str(active_contract.get("done_when", "")).strip() or "-",
                active_task_completion_rerun=str(active_contract.get("rerun_when", "")).strip() or "-",
                active_task_completion_followup=str(active_contract.get("manual_followup_when", "")).strip() or "-",
                active_task_backend=str(row.get("active_task_backend", "")).strip() or "-",
                active_task_backend_note=str(row.get("active_task_backend_note", "")).strip(),
                active_task_rate_limit=str(row.get("active_task_rate_limit", "")).strip() or "-",
                task_teams=_build_recovery_task_rows(row.get("task_teams") or []),
            )
        )
    return built


def _build_recovery_summary(summary_state: Dict[str, Any], freshness: FileFreshnessDTO) -> RecoverySummaryDTO:
    control = summary_state.get("control_summary") if isinstance(summary_state.get("control_summary"), dict) else {}
    return RecoverySummaryDTO(
        exists=bool(freshness.exists and summary_state),
        artifact_path=freshness.path,
        updated_at=freshness.updated_at,
        stale=bool(freshness.stale),
        error=str(freshness.error or "").strip(),
        generated_at=str(summary_state.get("generated_at", "")).strip() or "-",
        snapshot_taken_at=str(summary_state.get("snapshot_taken_at", "")).strip() or "-",
        automation_posture=str(control.get("automation_posture", "")).strip() or "-",
        auto_mode=str(control.get("auto_mode", "")).strip() or "-",
        offdesk_mode=str(control.get("offdesk_mode", "")).strip() or "-",
        provider_capacity_summary=str(control.get("provider_capacity_summary", "")).strip() or "-",
        next_retry_at=str(control.get("next_retry_at", "")).strip() or "-",
        next_retry_target=str(control.get("next_retry_target", "")).strip() or "-",
        repeat_memory_summary=str(control.get("repeat_memory_summary", "")).strip() or "-",
        runtimes=_build_recovery_runtime_rows(summary_state.get("runtimes") or []),
    )


def resolve_task_request_for_alias(manager_state: Dict[str, Any], project_alias: str, task_short_id: str) -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    alias_token = str(project_alias or "").strip().upper()
    task_token = str(task_short_id or "").strip()
    if not alias_token or not task_token:
        return ""
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("project_alias", "")).strip().upper() != alias_token:
            continue
        resolved = task_state.resolve_task_request_id(entry, task_token)
        return resolved if task_state.get_task_record(entry, resolved) else ""
    return ""


def resolve_task_request_for_alias_route(
    *,
    control_root: Path | str,
    project_alias: str,
    task_short_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> str:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    return resolve_task_request_for_alias(manager_loaded.state, project_alias, task_short_id)


def load_dashboard_snapshot(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> DashboardSnapshotDTO:
    return load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    ).snapshot


def load_dashboard_snapshot_result(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> DashboardSnapshotLoadResult:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    snapshot_taken_at = now_iso()

    manager_loaded = _load_manager_state(paths)
    auto_state, auto_freshness = _load_json_file(paths.auto_state_file, name="auto_state")
    provider_state, provider_freshness = _load_json_file(paths.provider_capacity_file, name="provider_capacity")

    runtime_cards = _build_runtime_cards(manager_loaded.state, provider_state)
    active_rows = _build_active_task_rows(manager_loaded.state)
    attention_cards = [card for card in runtime_cards if card.status in {"blocked", "warn"}][:8]

    auto_mode = str(auto_state.get("mode", "")).strip()
    if not auto_mode:
        auto_mode = "on" if bool(auto_state.get("enabled", False)) else "off"
    offdesk_mode = "on" if bool(auto_state.get("offdesk_enabled", auto_state.get("offdesk_mode") not in {None, "", "off"})) else "off"

    summary = ControlSummaryDTO(
        auto_mode=auto_mode,
        offdesk_mode=offdesk_mode,
        provider_capacity_summary=_provider_summary_text(provider_state),
        next_retry_at=str(provider_state.get("next_retry_at", "")).strip() or "-",
        next_retry_target=_next_retry_target_text(provider_state),
        repeat_memory_summary=_repeat_summary_text(provider_state),
        active_runtime_count=len(runtime_cards),
        attention_runtime_count=len(attention_cards),
        snapshot_taken_at=snapshot_taken_at,
    )

    return DashboardSnapshotLoadResult(
        snapshot=DashboardSnapshotDTO(
            control_root=str(paths.control_root),
            team_dir=str(paths.team_dir),
            manager_state_file=str(paths.manager_state_file),
            snapshot_taken_at=snapshot_taken_at,
            source_files=[manager_loaded.freshness, auto_freshness, provider_freshness],
            control_summary=summary,
            runtime_cards=runtime_cards,
            attention_runtime_cards=attention_cards,
            active_task_rows=active_rows,
        ),
        manager_state=manager_loaded.state,
        provider_state=provider_state,
    )


def load_task_detail(
    *,
    control_root: Path | str,
    request_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Optional[TaskDetailDTO]:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    return _build_task_detail(manager_loaded.state, request_id)


def task_detail_from_state(manager_state: Dict[str, Any], request_id: str) -> Optional[TaskDetailDTO]:
    return _build_task_detail(manager_state, request_id)


def load_dashboard_task_page(
    *,
    control_root: Path | str,
    request_id: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, Optional[TaskDetailDTO]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    return loaded.snapshot, _build_task_detail(loaded.manager_state, request_id)


def load_runtime_detail(
    *,
    control_root: Path | str,
    project_alias: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Optional[RuntimeDetailDTO]:
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    manager_loaded = _load_manager_state(paths)
    provider_state, _provider_freshness = _load_json_file(paths.provider_capacity_file, name="provider_capacity")
    return _build_runtime_detail(manager_loaded.state, provider_state, project_alias)


def load_dashboard_runtime_page(
    *,
    control_root: Path | str,
    project_alias: str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, Optional[RuntimeDetailDTO]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    return loaded.snapshot, _build_runtime_detail(loaded.manager_state, loaded.provider_state, project_alias)


def load_dashboard_runtime_details(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, List[RuntimeDetailDTO], Dict[str, Any]]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    details: List[RuntimeDetailDTO] = []
    for card in loaded.snapshot.runtime_cards:
        detail = _build_runtime_detail(loaded.manager_state, loaded.provider_state, card.project_alias)
        if detail is not None:
            details.append(detail)
    return loaded.snapshot, details, loaded.manager_state


def load_dashboard_recovery_page(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Tuple[DashboardSnapshotDTO, RecoverySummaryDTO]:
    loaded = load_dashboard_snapshot_result(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    paths = resolve_control_paths(control_root=control_root, team_dir=team_dir, manager_state_file=manager_state_file)
    summary_state, freshness = _load_json_file(_recovery_summary_path(paths.team_dir), name="nightly_summary")
    return loaded.snapshot, _build_recovery_summary(summary_state, freshness)
