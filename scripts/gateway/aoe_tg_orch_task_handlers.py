#!/usr/bin/env python3
"""Orchestrator task lifecycle handlers for Telegram gateway."""

from datetime import datetime
import json
from pathlib import Path
from urllib.parse import quote
from typing import Any, Callable, Dict, List, Optional

import aoe_tg_background_runs as background_runs
import aoe_tg_context_pack as context_pack
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_model_provider_adapter as model_provider_adapter
import aoe_tg_worker_task_contract as worker_task_contract
from aoe_tg_action_audit import (
    append_action_audit_row,
    load_latest_canonical_mutation_summary_for_runtime,
    load_latest_canonical_writeback_summary_for_runtime,
    load_latest_judge_decision_bridge_summary_for_runtime,
    load_latest_manual_step_summary_for_runtime,
    load_latest_replan_auto_route_status_summary_for_runtime,
    load_latest_replan_auto_routing_policy_summary_for_runtime,
    normalize_offdesk_judge_decision,
    prefer_recent_model_ping_probe_summary,
)
from aoe_tg_executor_runtime import poll_background_tickets_via_adapters
from aoe_tg_local_background_worker import (
    ensure_local_background_daemon,
    run_local_background_ticket,
    stop_local_background_daemon,
)
from aoe_tg_model_endpoint_adapter import summarize_model_endpoint_registry, summarize_model_routing
from aoe_tg_document_registry import summarize_document_registry
from aoe_tg_workspace_brief import summarize_workspace_brief
from aoe_tg_package_paths import package_root
from aoe_tg_request_contract import (
    apply_background_run_ticket_snapshot,
    build_background_launch_spec,
    build_background_run_ticket,
    build_local_background_provider_invoke_launch_spec,
    build_local_background_provider_task_launch_spec,
    select_background_runner_target,
)
from aoe_tg_priority_actions import external_background_priority_action_snapshot
from aoe_tg_run_lock import normalize_run_lock_mode, project_run_lock_mode, project_run_lock_note
from aoe_tg_external_background_worker import (
    emit_external_background_ack,
    emit_external_background_result,
    external_background_ack_path,
    external_background_handoff_path,
    external_background_result_path,
    read_external_background_ack,
    read_external_background_handoff,
    read_external_background_result,
)
from aoe_tg_task_state import derive_background_run_external_snapshot
from aoe_tg_todo_state import ensure_todo_proposal_store, merge_todo_proposals

from aoe_tg_project_runtime import project_hidden_from_ops, project_runtime_issue


_SCENARIO_FILENAME = "AOE_TODO.md"
_BACKGROUND_RUNNER_PREFS = {"local_background", "local_tmux", "github_runner", "remote_worker"}
_RUN_LOCK_MODES = {"open", "test_only"}
_BACKGROUND_SLOT_MIN = 1
_BACKGROUND_SLOT_MAX = 8
_BACKGROUND_WORKER_PING_PROMPT = "Reply with BGW_PING_OK only."
_BACKGROUND_WORKER_PING_SYSTEM = "Return the exact token only."
_MODEL_PING_SPECS = {
    "research": ("RESEARCH_PING_OK", "on_desk_plan"),
    "judge": ("JUDGE_PING_OK", "review"),
    "escalation": ("ESCALATION_PING_OK", "review"),
}
_OFFDESK_JUDGE_SYSTEM = (
    "You are the off-desk judge. Return strict JSON with keys: "
    "verdict, confidence, reasoning, next_step, caution. Keep each value concise."
)
_DEFAULT_SCENARIO_TEMPLATE = """# AOE_TODO.md

Project scenario (per-project, runtime file).

This file is imported into the Control Plane todo queue via `/sync`.
Only task lines are parsed; everything else is ignored.

## Tasks

# Optional: keep your canonical todo in `<project_root>/TODO.md` and include it here.
@include ../TODO.md

# Supported formats (parsed by /sync):
# - [ ] summary        (open; default priority: P2)
# - summary            (open; default priority: P2)
# - 1. summary         (open; default priority: P2)
# - [ ] P1: summary
# - [x] P2: summary
# - P3: summary
#
# Notes:
# - You can include an explicit TODO id to update an existing item:
#   - [ ] TODO-123 P2: Adjust thresholds
# - Done lines ([x]) only mark done when the matching TODO already exists.

# - [ ] P2: (write your task here)

## Examples (ignored by /sync)

```text
- [ ] P1: First task
- [ ] P2: Second task
```
"""


def _runtime_action_link(alias: str) -> str:
    token = str(alias or "").strip()
    return f"/control/runtimes/{quote(token, safe='')}" if token else "-"


def _project_alias(entry: Dict[str, Any], fallback: str) -> str:
    token = str(entry.get("project_alias", "")).strip().upper()
    return token or str(fallback or "").strip() or "-"


def _project_sort_key(key: str, entry: Dict[str, Any]) -> tuple[int, str, str]:
    alias = _project_alias(entry, key)
    token = alias[1:] if alias.startswith("O") else alias
    idx = int(token) if token.isdigit() else 10**9
    return idx, alias, str(key)


def _resolve_registered_project(manager_state: Dict[str, Any], target: Optional[str]) -> tuple[str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict) or not projects:
        raise RuntimeError("no orch projects registered")

    raw = str(target or manager_state.get("active", "default")).strip() or "default"
    raw_key = raw.lower()
    raw_alias = raw.upper()

    direct = projects.get(raw_key)
    if isinstance(direct, dict):
        return raw_key, direct

    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if raw_key == str(key).strip().lower():
            return str(key), entry
        alias = str(entry.get("project_alias", "")).strip().upper()
        if alias and alias == raw_alias:
            return str(key), entry
        display = str(entry.get("display_name", "") or entry.get("name", "")).strip().lower()
        if display and display == raw_key:
            return str(key), entry

    known = ", ".join(sorted(str(k) for k in projects.keys()))
    raise RuntimeError(f"unknown orch project: {raw} (known: {known})")


def _repair_registered_project(
    *,
    args: Any,
    entry: Dict[str, Any],
    key: str,
    resolve_project_root: Callable[[str], Any],
    run_aoe_init: Callable[..., str],
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    project_root = resolve_project_root(str(entry.get("project_root", "") or ""))
    team_dir = Path(str(entry.get("team_dir", "") or project_root / ".aoe-team")).expanduser().resolve()
    entry["project_root"] = str(project_root)
    entry["team_dir"] = str(team_dir)
    alias = _project_alias(entry, key)
    before_issue = project_runtime_issue(entry)
    overview = str(entry.get("overview", "")).strip() or f"{entry.get('display_name') or alias} project orchestration"

    logs: List[str] = []
    logs.append(
        ensure_scenario_file(
            template_root=package_root(),
            team_dir=team_dir,
            dry_run=bool(args.dry_run),
        )
    )
    logs.append(run_aoe_init(args, project_root=project_root, team_dir=team_dir, overview=overview))

    entry["updated_at"] = now_iso()
    after_issue = project_runtime_issue(entry)
    return {
        "key": key,
        "alias": alias,
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "before": before_issue or "ready",
        "after": after_issue or "ready",
        "logs": logs,
        "ready": not bool(after_issue),
    }


def _orch_status_reply_markup(manager_state: Dict[str, Any], key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    alias = _project_alias(entry, key)
    external_snapshot = _latest_external_background_task_snapshot(entry)
    queue_stale_count = 0
    worker_status = ""
    try:
        team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser()
        if str(team_dir):
            queue_snapshot = background_runs.summarize_background_runs_state(
                background_runs.background_runs_state_path(team_dir)
            )
            queue_stale_count = int(queue_snapshot.get("stale_count", 0) or 0)
            worker_snapshot = background_runs.summarize_background_worker_state(
                background_runs.background_worker_state_path(team_dir)
            )
            worker_status = str(worker_snapshot.get("status", "")).strip().lower()
    except Exception:
        queue_stale_count = 0
        worker_status = ""
    raw_lock = manager_state.get("project_lock") if isinstance(manager_state, dict) else {}
    lock_key = ""
    if isinstance(raw_lock, dict) and bool(raw_lock.get("enabled", False)):
        lock_key = str(raw_lock.get("project_key", "")).strip().lower()

    focus_button = "/focus off" if lock_key == str(key).strip().lower() else f"/focus {alias}"
    hide_button = f"/orch {'unhide' if project_hidden_from_ops(entry) else 'hide'} {alias}"
    runner_pref = str(entry.get("background_runner_target", "")).strip().lower() or "local_background"
    runner_toggle = "local_background" if runner_pref == "local_tmux" else "local_tmux"
    run_lock_mode = project_run_lock_mode(entry)
    run_lock_toggle = "open" if run_lock_mode == "test_only" else "test_only"
    slot_runner_target = runner_pref if runner_pref in {"local_tmux", "github_runner", "remote_worker"} else ""
    slot_limit = _background_runner_slot_limit_for_target(entry, slot_runner_target)
    slot_bump_command = (
        f"/orch bg-slots {alias} {slot_runner_target} {slot_limit + 1 if slot_limit < _BACKGROUND_SLOT_MAX else _BACKGROUND_SLOT_MIN}"
        if slot_runner_target
        else f"/orch bg-slots {alias} {slot_limit + 1 if slot_limit < _BACKGROUND_SLOT_MAX else _BACKGROUND_SLOT_MIN}"
    )
    issue = project_runtime_issue(entry)
    if issue:
        keyboard: List[List[Dict[str, str]]] = [
            [{"text": f"/orch repair {alias}"}, {"text": f"/sync preview {alias} 1h"}],
            [{"text": f"/use {alias}"}, {"text": focus_button}, {"text": hide_button}],
            [{"text": f"/orch status {alias}"}, {"text": "/map"}, {"text": "/help"}],
        ]
    else:
        keyboard = [
            [{"text": f"/todo {alias}"}, {"text": f"/todo {alias} followup"}, {"text": f"/orch monitor {alias}"}],
            ([{"text": f"/orch bgq-clean {alias}"}] if queue_stale_count > 0 else []),
            [{"text": f"/orch bg-runner {alias} {runner_toggle}"}],
            [{"text": slot_bump_command}],
            [{"text": f"/orch run-lock {alias} {run_lock_toggle}"}],
            ([{"text": f"/orch bgx-status {alias}"}] if external_snapshot else []),
            (
                [{"text": f"/orch bgx-emit-ack {alias}"}]
                if external_snapshot and run_lock_mode == "test_only" and str(external_snapshot.get("phase", "")).strip().lower() in {"handoff_emitted", "awaiting_external_pickup"}
                else []
            ),
            (
                [
                    {"text": f"/orch bgx-emit-result {alias} completed"},
                    {"text": f"/orch bgx-emit-result {alias} failed"},
                ]
                if external_snapshot and run_lock_mode == "test_only" and str(external_snapshot.get("phase", "")).strip().lower() == "pickup_acknowledged"
                else []
            ),
            (
                [
                    {"text": f"/orch bgx-handoff {alias}"},
                    {"text": f"/orch bgx-ack {alias}"},
                    {"text": f"/orch bgx-result {alias}"},
                ]
                if external_snapshot
                else []
            ),
            [{"text": f"/orch bgw-status {alias}"}]
            + (
                [{"text": f"/orch bgw-stop {alias}"}]
                if worker_status in {"running", "idle"}
                else [{"text": f"/orch bgw-start {alias}"}]
            ),
            (
                [{"text": f"/orch bgw-ping {alias}"}, {"text": f"/orch bgw-task {alias}"}]
                if run_lock_mode == "test_only"
                else []
            ),
            (
                [
                    {"text": f"/orch model-ping {alias} research"},
                    {"text": f"/orch model-ping {alias} escalation"},
                ]
                if run_lock_mode == "test_only"
                else []
            ),
            [{"text": f"/orch judge {alias}"}],
            [{"text": f"/sync preview {alias} 1h"}],
            [{"text": f"/sync {alias} 1h"}, {"text": f"/use {alias}"}, {"text": focus_button}],
            [{"text": hide_button}],
            [{"text": f"/orch status {alias}"}, {"text": "/queue"}, {"text": "/map"}],
        ]
        keyboard = [row for row in keyboard if row]
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": f"예: /todo {alias} 또는 /orch monitor {alias}",
    }


def _background_runner_preference(entry: Dict[str, Any]) -> str:
    token = str(entry.get("background_runner_target", "")).strip().lower()
    return token if token in _BACKGROUND_RUNNER_PREFS else "local_background"


def _background_runner_slot_limit(entry: Dict[str, Any]) -> int:
    return background_runs.background_runner_slot_limit_for_entry(
        entry,
        "",
        default_limit=1,
        max_value=_BACKGROUND_SLOT_MAX,
    )


def _background_runner_slot_limit_for_target(entry: Dict[str, Any], runner_target: str) -> int:
    return background_runs.background_runner_slot_limit_for_entry(
        entry,
        runner_target,
        default_limit=1,
        max_value=_BACKGROUND_SLOT_MAX,
    )


def _background_runner_slot_limits(entry: Dict[str, Any]) -> Dict[str, int]:
    return background_runs.background_runner_slot_limits_for_entry(
        entry,
        default_limit=_background_runner_slot_limit(entry),
        max_value=_BACKGROUND_SLOT_MAX,
    )


def _background_slot_command_target(rest: str) -> tuple[str, int] | None:
    tokens = [str(part).strip() for part in str(rest or "").split() if str(part).strip()]
    if not tokens:
        return None
    if len(tokens) == 1:
        try:
            return "", max(_BACKGROUND_SLOT_MIN, min(int(tokens[0]), _BACKGROUND_SLOT_MAX))
        except Exception:
            return None
    if len(tokens) == 2 and tokens[0].lower() in {"local_tmux", "github_runner", "remote_worker"}:
        try:
            return tokens[0].lower(), max(_BACKGROUND_SLOT_MIN, min(int(tokens[1]), _BACKGROUND_SLOT_MAX))
        except Exception:
            return None
    return None


def _background_runner_status(entry: Dict[str, Any], key: str) -> tuple[str, str, str]:
    preferred = _background_runner_preference(entry)
    launch_spec = build_background_launch_spec(
        request_id="",
        project_key=str(key or "").strip(),
        project_root=str(entry.get("project_root", "") or "").strip(),
        team_dir=str(entry.get("team_dir", "") or "").strip(),
        manager_state_file="",
        runner_target="local_background",
        launch_mode="detached_no_wait",
        source_surface="orch_status",
        created_by="status",
        kind="gateway_dispatch",
        mode="in_process_callback",
        entrypoint="aoe-telegram-gateway",
        argv=["run", "--no-wait"],
        env_keys=["AOE_TEAM_DIR", "AOE_STATE_DIR"],
        externalizable=False,
        blocked_reason="requires in-process callback registry",
    )
    effective = select_background_runner_target(
        preferred_runner_target=preferred,
        launch_spec=launch_spec,
        allow_external_targets=False,
    )
    note = ""
    if preferred != effective:
        note = f"preferred {preferred} is pending until an externalizable launch spec exists"
    return preferred, effective, note


def _project_run_lock_status(entry: Dict[str, Any]) -> tuple[str, str]:
    mode = project_run_lock_mode(entry)
    return mode, project_run_lock_note(entry)


def _latest_external_background_task_snapshot(entry: Dict[str, Any]) -> Dict[str, str]:
    tasks = entry.get("tasks") if isinstance(entry, dict) else {}
    if not isinstance(tasks, dict):
        return {}

    def _sort_key(req_id: str, task: Dict[str, Any]) -> tuple[int, str, str]:
        status = str(task.get("status", "pending")).strip().lower()
        priority = {"running": 4, "pending": 3, "failed": 2, "completed": 1}.get(status, 0)
        updated = str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip()
        return (priority, updated, str(req_id or "").strip())

    best_req = ""
    best_task: Dict[str, Any] | None = None
    for req_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        runner = str(task.get("background_run_runner_target", "")).strip().lower()
        if runner not in {"github_runner", "remote_worker"}:
            continue
        phase = str(task.get("background_run_external_phase", "")).strip().lower()
        note = str(task.get("background_run_external_note", "")).strip()
        if not phase and not note:
            continue
        if best_task is None or _sort_key(str(req_id), task) > _sort_key(best_req, best_task):
            best_req = str(req_id)
            best_task = task

    if not isinstance(best_task, dict):
        return {}
    return {
        "request_id": best_req,
        "label": str(best_task.get("short_id", "")).strip().upper() or str(best_task.get("alias", "")).strip() or best_req,
        "ticket_id": str(best_task.get("background_run_ticket_id", "")).strip(),
        "runner_target": str(best_task.get("background_run_runner_target", "")).strip().lower(),
        "phase": str(best_task.get("background_run_external_phase", "")).strip().lower(),
        "note": str(best_task.get("background_run_external_note", "")).strip(),
    }


def _latest_task_for_model_status(entry: Dict[str, Any]) -> Dict[str, Any]:
    tasks = entry.get("tasks") if isinstance(entry, dict) else {}
    if not isinstance(tasks, dict):
        return {}
    last_request_id = str(entry.get("last_request_id", "")).strip()
    if last_request_id and isinstance(tasks.get(last_request_id), dict):
        return tasks.get(last_request_id) or {}

    def _sort_key(req_id: str, task: Dict[str, Any]) -> tuple[int, str, str]:
        status = str(task.get("status", "pending")).strip().lower()
        priority = {"running": 4, "pending": 3, "failed": 2, "completed": 1}.get(status, 0)
        updated = str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip()
        return (priority, updated, str(req_id or "").strip())

    best_req = ""
    best_task: Dict[str, Any] | None = None
    for req_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        if best_task is None or _sort_key(str(req_id), task) > _sort_key(best_req, best_task):
            best_req = str(req_id)
            best_task = task
    return best_task if isinstance(best_task, dict) else {}


def _offdesk_judge_prompt(entry: Dict[str, Any], task: Dict[str, Any], team_dir: Path) -> str:
    alias = _project_alias(entry, str(entry.get("name", "")).strip())
    project_name = str(entry.get("display_name", "")).strip() or str(entry.get("name", "")).strip() or alias
    task_label = (
        str(task.get("short_id", "")).strip().upper()
        or str(task.get("alias", "")).strip()
        or str(task.get("request_id", "")).strip()
        or "task"
    )
    pack = context_pack.load_context_pack(
        team_dir,
        entry=entry,
        task=task,
        project_root=entry.get("project_root"),
    )
    rerun_summary = str(task.get("rerun_summary", "")).strip() or (
        "retry="
        + (
            str(task.get("rerun_status", "")).strip() or "none"
        )
    )
    followup_summary = str(task.get("followup_summary", "")).strip() or (
        str(task.get("followup_brief_summary", "")).strip() or "followup=none"
    )
    payload = {
        "runtime": alias,
        "project": project_name,
        "task": task_label,
        "request_id": str(task.get("request_id", "")).strip() or "-",
        "status": str(task.get("status", "")).strip() or "-",
        "tf_phase": str(task.get("tf_phase", "")).strip() or "-",
        "execution_brief_status": str(task.get("execution_brief_status", "")).strip() or "-",
        "execution_brief_summary": str(task.get("execution_brief_summary", "")).strip() or "-",
        "execution_brief_operator_decision": str(task.get("execution_brief_operator_decision", "")).strip() or "-",
        "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
        "followup_brief_summary": str(task.get("followup_brief_summary", "")).strip() or "-",
        "rerun_summary": rerun_summary,
        "followup_summary": followup_summary,
        "context_pack_profile": str(pack.get("profile", "")).strip() or "-",
        "context_pack_docs": str(pack.get("docs_summary", "")).strip() or "-",
        "context_pack_excluded": str(pack.get("excluded_summary", "")).strip() or "-",
    }
    return (
        "Review the runtime and task state below and decide whether the operator should continue, "
        "replan, execute follow-up, or hold for manual review.\n"
        "Prefer conservative decisions when blockers or ambiguity remain.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _judge_binding_lines(entry: Dict[str, Any], team_dir: Path) -> tuple[str, str]:
    latest_task = _latest_task_for_model_status(entry)
    if not latest_task:
        return "", ""
    task_label = (
        str(latest_task.get("short_id", "")).strip().upper()
        or str(latest_task.get("alias", "")).strip()
        or str(latest_task.get("request_id", "")).strip()
        or "task"
    )
    binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=latest_task,
    )
    probe = model_endpoint_adapter.probe_task_judge_binding(
        team_dir,
        entry=entry,
        task=latest_task,
    )
    binding_summary = str(binding.get("summary", "")).strip() or "-"
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    probe_summary = prefer_recent_model_ping_probe_summary(
        team_dir,
        project_alias=str(entry.get("project_alias", "")).strip(),
        kind="judge",
        endpoint_id=str(endpoint.get("endpoint_id", "")).strip(),
        probe_status=str(probe.get("probe_status", "")).strip(),
        probe_summary=str(probe.get("summary", "")).strip() or "-",
    )
    return (
        f"judge_binding: {task_label} | {binding_summary}\n",
        f"judge_probe: {task_label} | {probe_summary}\n",
    )


def _escalation_binding_lines(entry: Dict[str, Any], team_dir: Path) -> tuple[str, str]:
    latest_task = _latest_task_for_model_status(entry)
    if not latest_task:
        return "", ""
    task_label = (
        str(latest_task.get("short_id", "")).strip().upper()
        or str(latest_task.get("alias", "")).strip()
        or str(latest_task.get("request_id", "")).strip()
        or "task"
    )
    binding = model_endpoint_adapter.resolve_task_escalation_binding(
        team_dir,
        entry=entry,
        task=latest_task,
    )
    probe = model_endpoint_adapter.probe_task_escalation_binding(
        team_dir,
        entry=entry,
        task=latest_task,
    )
    binding_summary = str(binding.get("summary", "")).strip() or "-"
    probe_summary = str(probe.get("summary", "")).strip() or "-"
    return (
        f"escalation_binding: {task_label} | {binding_summary}\n",
        f"escalation_probe: {task_label} | {probe_summary}\n",
    )


def _external_background_artifact_snapshot(entry: Dict[str, Any]) -> Dict[str, str]:
    snapshot = _latest_external_background_task_snapshot(entry)
    if not snapshot:
        return {}
    team_dir_raw = str(entry.get("team_dir", "") or "").strip()
    ticket_id = str(snapshot.get("ticket_id", "")).strip()
    runner_target = str(snapshot.get("runner_target", "")).strip().lower()
    if not team_dir_raw or not ticket_id or runner_target not in {"github_runner", "remote_worker"}:
        return snapshot
    team_dir = Path(team_dir_raw).expanduser().resolve()
    handoff_path = external_background_handoff_path(team_dir, ticket_id, runner_target)
    ack_path = external_background_ack_path(team_dir, ticket_id, runner_target)
    result_path = external_background_result_path(team_dir, ticket_id, runner_target)
    snapshot["handoff_path"] = str(handoff_path.relative_to(team_dir)).strip()
    snapshot["ack_path"] = str(ack_path.relative_to(team_dir)).strip()
    snapshot["result_path"] = str(result_path.relative_to(team_dir)).strip()
    snapshot["handoff_exists"] = "yes" if handoff_path.exists() else "no"
    snapshot["ack_exists"] = "yes" if ack_path.exists() else "no"
    snapshot["result_exists"] = "yes" if result_path.exists() else "no"
    return snapshot


def _external_background_next_step_for_inspect(alias: str, snapshot: Dict[str, Any]) -> str:
    if str(snapshot.get("result_exists", "")).strip().lower() == "yes":
        return f"/orch bgx-result {alias}"
    if str(snapshot.get("ack_exists", "")).strip().lower() == "yes":
        return f"/orch bgx-ack {alias}"
    if str(snapshot.get("handoff_exists", "")).strip().lower() == "yes":
        return f"/orch bgx-handoff {alias}"
    return f"/orch status {alias}"


def _external_background_artifact_detail(
    entry: Dict[str, Any],
    artifact_kind: str,
) -> Dict[str, Any]:
    snapshot = _external_background_artifact_snapshot(entry)
    if not snapshot:
        return {}

    kind = str(artifact_kind or "").strip().lower()
    if kind not in {"handoff", "ack", "result"}:
        return {}

    team_dir_raw = str(entry.get("team_dir", "") or "").strip()
    if not team_dir_raw:
        return snapshot
    team_dir = Path(team_dir_raw).expanduser().resolve()
    ticket_id = str(snapshot.get("ticket_id", "")).strip()
    runner_target = str(snapshot.get("runner_target", "")).strip().lower()
    if not ticket_id or runner_target not in {"github_runner", "remote_worker"}:
        return snapshot

    if kind == "handoff":
        artifact_path = external_background_handoff_path(team_dir, ticket_id, runner_target)
        parsed = read_external_background_handoff(artifact_path)
    elif kind == "ack":
        artifact_path = external_background_ack_path(team_dir, ticket_id, runner_target)
        parsed = read_external_background_ack(artifact_path)
    else:
        artifact_path = external_background_result_path(team_dir, ticket_id, runner_target)
        parsed = read_external_background_result(artifact_path)

    snapshot["artifact_kind"] = kind
    snapshot["artifact_path"] = str(artifact_path.relative_to(team_dir)).strip()[:240]
    snapshot["artifact_exists"] = "yes" if artifact_path.exists() else "no"
    snapshot["artifact_parsed"] = "yes" if parsed else "no"
    snapshot["artifact_detail"] = parsed
    return snapshot


def _sync_background_run_snapshots_from_queue(entry: Dict[str, Any], queue_path: Path) -> bool:
    tasks = entry.get("tasks") if isinstance(entry.get("tasks"), dict) else {}
    if not isinstance(tasks, dict) or not tasks:
        return False
    state = background_runs.load_background_runs_state(queue_path)
    runs = list(state.get("runs") or [])
    if not runs:
        return False
    latest_by_request: Dict[str, Dict[str, Any]] = {}
    for row in runs:
        if not isinstance(row, dict):
            continue
        request_id = str(row.get("request_id", "")).strip()
        if request_id:
            latest_by_request[request_id] = row
    changed = False
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        ticket = latest_by_request.get(str(request_id).strip())
        if not isinstance(ticket, dict):
            continue
        before = (
            str(task.get("background_run_status", "")).strip(),
            str(task.get("background_run_ticket_id", "")).strip(),
            str(task.get("background_run_evidence_bundle", "")).strip(),
            str(task.get("background_run_runtime_handle", "")).strip(),
            str(task.get("background_run_worker_update_proposal_summary", "")).strip(),
        )
        apply_background_run_ticket_snapshot(task, ticket)
        external_snapshot = derive_background_run_external_snapshot(task)
        if external_snapshot:
            task["background_run_external_phase"] = str(external_snapshot.get("phase", "")).strip()
            task["background_run_external_note"] = str(external_snapshot.get("note", "")).strip()
        else:
            task.pop("background_run_external_phase", None)
            task.pop("background_run_external_note", None)
        task.setdefault("result", {})
        if isinstance(task.get("result"), dict):
            task["result"]["background_run_status"] = str(ticket.get("status", "")).strip()
            task["result"]["background_run_runner_target"] = str(ticket.get("runner_target", "")).strip()
            task["result"]["background_run_ticket_id"] = str(ticket.get("ticket_id", "")).strip()
            bundle = str(ticket.get("evidence_bundle", "")).strip()
            if bundle:
                task["result"]["background_run_evidence_bundle"] = bundle
        launch_spec = ticket.get("launch_spec") if isinstance(ticket.get("launch_spec"), dict) else {}
        update_stub = {
            "status": ticket.get("worker_update_stub_status"),
            "summary_line": ticket.get("worker_update_stub_summary"),
            "target_artifacts": ticket.get("worker_update_stub_targets"),
        }
        proposal_ids: List[str] = []
        proposal_summary = "-"
        if str(ticket.get("status", "")).strip().lower() == "completed":
            proposal_payloads = worker_task_contract.derive_worker_update_todo_proposals(
                launch_spec.get("provider_task_contract_json"),
                update_stub,
            )
            if proposal_payloads:
                merge_todo_proposals(
                    entry=entry,
                    request_id=request_id,
                    task=task,
                    source_todo_id=str(task.get("source_todo_id", "")).strip(),
                    proposals_data=proposal_payloads,
                    now_iso=lambda: datetime.now().astimezone().replace(microsecond=0).isoformat(),
                )
                proposals_store, _proposal_seq = ensure_todo_proposal_store(entry)
                proposal_ids = worker_task_contract.match_worker_update_proposal_ids(
                    proposals_store,
                    request_id=request_id,
                    proposal_payloads=proposal_payloads,
                )
        proposal_summary = worker_task_contract.summarize_worker_update_proposal_summary(update_stub, proposal_ids)
        if proposal_summary not in {"", "-"}:
            task["background_run_worker_update_proposal_summary"] = proposal_summary
            task["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
            if isinstance(task.get("result"), dict):
                task["result"]["background_run_worker_update_proposal_summary"] = proposal_summary
                task["result"]["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
        else:
            task.pop("background_run_worker_update_proposal_summary", None)
            task.pop("background_run_worker_update_proposal_ids", None)
            if isinstance(task.get("result"), dict):
                task["result"].pop("background_run_worker_update_proposal_summary", None)
                task["result"].pop("background_run_worker_update_proposal_ids", None)
        after = (
            str(task.get("background_run_status", "")).strip(),
            str(task.get("background_run_ticket_id", "")).strip(),
            str(task.get("background_run_evidence_bundle", "")).strip(),
            str(task.get("background_run_runtime_handle", "")).strip(),
            str(task.get("background_run_worker_update_proposal_summary", "")).strip(),
        )
        if after != before:
            changed = True
    return changed


def _task_ref_for_actions(task: Dict[str, Any], request_id: str) -> str:
    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    short_id = str(context.get("task_short_id", "")).strip()
    if short_id:
        return short_id
    return str(request_id or "").strip()


def _lane_action_buttons(command: str, ref: str, lane_ids: List[str], *, limit: int = 3) -> List[Dict[str, str]]:
    buttons: List[Dict[str, str]] = []
    seen: set[str] = set()
    for lane_id in lane_ids:
        token = str(lane_id or "").strip()[:32]
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        buttons.append({"text": f"/{command} {ref} lane {token}"})
        if len(buttons) >= max(1, int(limit)):
            break
    return buttons


def _normalize_lane_ids(lane_ids: Optional[List[str]]) -> tuple[List[str], List[str]]:
    execution: List[str] = []
    review: List[str] = []
    seen_exec: set[str] = set()
    seen_review: set[str] = set()
    for item in lane_ids or []:
        token = str(item or "").strip()[:32]
        if not token:
            continue
        upper = token.upper()
        if upper.startswith("L"):
            if upper not in seen_exec:
                seen_exec.add(upper)
                execution.append(token)
        elif upper.startswith("R"):
            if upper not in seen_review:
                seen_review.add(upper)
                review.append(token)
    return execution, review


def _orch_task_reply_markup(key: str, entry: Dict[str, Any], request_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    alias = _project_alias(entry, key)
    ref = _task_ref_for_actions(task, request_id)
    exec_critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    rerun_exec = [str(x).strip() for x in (exec_critic.get("rerun_execution_lane_ids") or []) if str(x).strip()]
    rerun_review = [str(x).strip() for x in (exec_critic.get("rerun_review_lane_ids") or []) if str(x).strip()]
    manual_exec = [str(x).strip() for x in (exec_critic.get("manual_followup_execution_lane_ids") or []) if str(x).strip()]
    manual_review = [str(x).strip() for x in (exec_critic.get("manual_followup_review_lane_ids") or []) if str(x).strip()]
    followup_brief_status = str(task.get("followup_brief_status", "")).strip().lower()
    followup_execute_enabled = followup_brief_status in {"executable", "partially_executable"}
    verdict = str(exec_critic.get("verdict", "")).strip().lower()
    action = str(exec_critic.get("action", "")).strip().lower()

    keyboard: List[List[Dict[str, str]]] = [
        [{"text": f"/check {ref}"}, {"text": f"/task {ref}"}],
    ]
    if rerun_exec or rerun_review or verdict == "retry":
        row = [{"text": f"/retry {ref}"}]
        if action == "replan":
            row.append({"text": f"/replan {ref}"})
        keyboard.append(row)
        retry_lane_buttons = _lane_action_buttons("retry", ref, rerun_exec + rerun_review)
        if retry_lane_buttons:
            keyboard.append(retry_lane_buttons)
        if action == "replan":
            replan_lane_buttons = _lane_action_buttons("replan", ref, rerun_exec + rerun_review)
            if replan_lane_buttons:
                keyboard.append(replan_lane_buttons)
    if manual_exec or manual_review or verdict in {"fail", "intervention"}:
        row = [{"text": f"/followup {ref}"}, {"text": f"/todo {alias} followup"}]
        if followup_execute_enabled:
            row.append({"text": f"/followup-exec {ref}"})
        keyboard.append(row)
        followup_lane_buttons = _lane_action_buttons("followup", ref, manual_exec + manual_review)
        if followup_lane_buttons:
            keyboard.append(followup_lane_buttons)
        keyboard.append([{"text": f"/orch monitor {alias}"}])
    keyboard.append([{"text": f"/orch status {alias}"}, {"text": "/queue"}, {"text": "/map"}])
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": f"예: /retry {ref} 또는 /task {ref}",
    }


def ensure_scenario_file(*, template_root: Path, team_dir: Path, dry_run: bool) -> str:
    """Ensure `.aoe-team/AOE_TODO.md` exists for a project.

    The scheduler (`/sync`) imports this file into the Control Plane todo queue.
    """

    dst = (team_dir / _SCENARIO_FILENAME).resolve()
    if dst.exists():
        return f"[SKIP] scenario exists ({dst})"
    if dry_run:
        return f"[DRY-RUN] scenario create ({dst})"

    team_dir.mkdir(parents=True, exist_ok=True)

    template_path = (template_root / "templates" / "aoe-team" / _SCENARIO_FILENAME).resolve()
    text = _DEFAULT_SCENARIO_TEMPLATE
    try:
        if template_path.exists():
            text = template_path.read_text(encoding="utf-8")
    except Exception:
        # Keep the default template.
        pass

    dst.write_text(text, encoding="utf-8")
    return f"[OK] scenario created ({dst})"

def handle_orch_task_command(
    *,
    cmd: str,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    orch_target: Optional[str],
    orch_add_name: Optional[str],
    orch_add_path: Optional[str],
    orch_add_overview: Optional[str],
    orch_add_init: bool,
    orch_add_spawn: bool,
    orch_add_set_active: bool,
    rest: str,
    orch_check_request_id: Optional[str],
    orch_task_request_id: Optional[str],
    orch_pick_request_id: Optional[str],
    orch_cancel_request_id: Optional[str],
    orch_followup_request_id: Optional[str] = None,
    orch_followup_lane_ids: Optional[List[str]] = None,
    orch_followup_execute_request_id: Optional[str] = None,
    orch_followup_execute_lane_ids: Optional[List[str]] = None,
    send: Callable[..., bool],
    log_event: Callable[..., None],
    get_context: Callable[[Optional[str]], tuple[str, Dict[str, Any], Any]],
    latest_task_request_refs: Callable[..., list[str]],
    set_chat_recent_task_refs: Callable[..., None],
    save_manager_state: Callable[..., None],
    resolve_project_root: Callable[[str], Any],
    is_path_within: Callable[[Any, Any], bool],
    register_orch_project: Callable[..., tuple[str, Dict[str, Any]]],
    run_aoe_init: Callable[..., str],
    run_aoe_spawn: Callable[..., str],
    now_iso: Callable[[], str],
    run_aoe_status: Callable[[Any], str],
    resolve_chat_task_ref: Callable[..., str],
    resolve_task_request_id: Callable[[Dict[str, Any], str], str],
    run_request_query: Callable[[Any, str], Dict[str, Any]],
    sync_task_lifecycle: Callable[..., Optional[Dict[str, Any]]],
    resolve_verifier_candidates: Callable[[str], List[str]],
    touch_chat_recent_task_ref: Callable[..., None],
    set_chat_selected_task_ref: Callable[..., None],
    get_chat_selected_task_ref: Callable[..., str],
    get_task_record: Callable[[Dict[str, Any], str], Optional[Dict[str, Any]]],
    summarize_request_state: Callable[..., str],
    summarize_three_stage_request: Callable[..., str],
    summarize_task_lifecycle: Callable[..., str],
    task_display_label: Callable[..., str],
    cancel_request_assignments: Callable[..., Dict[str, Any]],
    lifecycle_set_stage: Callable[..., None],
    summarize_cancel_result: Callable[..., str],
) -> bool:
    if cmd == "orch-add":
        if not orch_add_name or not orch_add_path:
            send(
                "usage: aoe orch add <name> --path <project_root> [--overview <text>] [--init|--no-init] [--spawn|--no-spawn]",
                context="orch-add usage",
            )
            return True

        project_root = resolve_project_root(orch_add_path)
        if args.workspace_root and not is_path_within(project_root, args.workspace_root):
            send(
                f"error: path must be under workspace root ({args.workspace_root})\npath={project_root}",
                context="orch-add path",
            )
            return True

        team_dir = project_root / ".aoe-team"
        overview = (orch_add_overview or "").strip() or f"{orch_add_name} project orchestration"

        if args.dry_run:
            send(
                "[DRY-RUN] orch add\n"
                f"- name: {orch_add_name}\n"
                f"- path: {project_root}\n"
                f"- team: {team_dir}\n"
                f"- scenario: {team_dir / _SCENARIO_FILENAME} (create_if_missing: yes)\n"
                f"- init: {'yes' if orch_add_init else 'no'}\n"
                f"- spawn: {'yes' if orch_add_spawn else 'no'}\n"
                f"- set_active: {'yes' if orch_add_set_active else 'no'}",
                context="orch-add dry-run",
            )
            return True

        project_root.mkdir(parents=True, exist_ok=True)
        scenario_log = ""
        try:
            scenario_log = ensure_scenario_file(
                template_root=package_root(),
                team_dir=team_dir,
                dry_run=bool(args.dry_run),
            )
        except Exception as exc:
            scenario_log = f"[WARN] scenario create failed ({team_dir / _SCENARIO_FILENAME}): {exc}"
        key, entry = register_orch_project(
            manager_state,
            name=orch_add_name,
            project_root=project_root,
            team_dir=team_dir,
            overview=overview,
            set_active=orch_add_set_active,
        )

        init_logs: List[str] = []
        if scenario_log:
            init_logs.append(scenario_log)
        cfg_exists = (team_dir / "orchestrator.json").exists()
        should_init = orch_add_init or (not cfg_exists)
        if should_init:
            init_logs.append(run_aoe_init(args, project_root=project_root, team_dir=team_dir, overview=overview))

        if orch_add_spawn:
            init_logs.append(run_aoe_spawn(args, project_root=project_root, team_dir=team_dir))

        entry["updated_at"] = now_iso()
        save_manager_state(args.manager_state_file, manager_state)

        lines = [
            f"orch ready: {key}",
            f"root: {entry.get('project_root')}",
            f"team: {entry.get('team_dir')}",
            f"active: {'yes' if manager_state.get('active') == key else 'no'}",
        ]
        if init_logs:
            lines.append("logs:")
            for row in init_logs:
                short = row.strip().splitlines()
                lines.append(short[-1] if short else "(empty)")
        send("\n".join(lines), context="orch-add")
        return True

    if cmd == "orch-repair":
        target_token = str(orch_target or "").strip().lower()
        if target_token in {"all", "*", "global"}:
            projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
            rows: List[tuple[str, Dict[str, Any]]] = []
            if isinstance(projects, dict):
                for key, entry in projects.items():
                    if isinstance(entry, dict):
                        rows.append((str(key), entry))
            rows.sort(key=lambda item: _project_sort_key(item[0], item[1]))
            if not rows:
                send("no orch projects registered", context="orch-repair-all empty", with_menu=True)
                return True
            results: List[Dict[str, Any]] = []
            for key, entry in rows:
                try:
                    results.append(
                        _repair_registered_project(
                            args=args,
                            entry=entry,
                            key=key,
                            resolve_project_root=resolve_project_root,
                            run_aoe_init=run_aoe_init,
                            now_iso=now_iso,
                        )
                    )
                except Exception as exc:
                    results.append(
                        {
                            "key": key,
                            "alias": _project_alias(entry, key),
                            "project_root": str(entry.get("project_root", "") or ""),
                            "team_dir": str(entry.get("team_dir", "") or ""),
                            "before": project_runtime_issue(entry) or "ready",
                            "after": f"failed:{exc}",
                            "logs": [f"[ERROR] {exc}"],
                            "ready": False,
                        }
                    )
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)
            ready_count = sum(1 for row in results if bool(row.get("ready")))
            lines = [
                "orch repair all finished",
                f"- projects: {len(results)}",
                f"- ready: {ready_count}",
                f"- failed: {len(results) - ready_count}",
                "results:",
            ]
            for row in results:
                lines.append(
                    f"- {row.get('alias')} {row.get('key')}: {row.get('before')} -> {row.get('after')}"
                )
            lines.extend(
                [
                    "next:",
                    "- /map",
                    "- /sync preview all 1h",
                ]
            )
            send("\n".join(lines), context="orch-repair-all", with_menu=True)
            return True

        key, entry = _resolve_registered_project(manager_state, orch_target)
        try:
            result = _repair_registered_project(
                args=args,
                entry=entry,
                key=key,
                resolve_project_root=resolve_project_root,
                run_aoe_init=run_aoe_init,
                now_iso=now_iso,
            )
        except Exception as exc:
            send(f"orch repair failed\n- runtime: {key}\n- error: {exc}", context="orch-repair failed", with_menu=True)
            return True
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        lines = [
            "orch repair finished",
            f"- runtime: {result.get('key')} ({result.get('alias')})",
            f"- root: {result.get('project_root')}",
            f"- team: {result.get('team_dir')}",
            f"- before: {result.get('before')}",
            f"- after: {result.get('after')}",
        ]
        logs = result.get("logs") or []
        if isinstance(logs, list) and logs:
            lines.append("logs:")
            for row in logs:
                short = row.strip().splitlines()
                lines.append(f"- {short[-1] if short else '(empty)'}")
        lines.extend(
            [
                "next:",
                f"- /orch status {result.get('alias')}",
                f"- /sync preview {result.get('alias')} 1h",
            ]
        )
        if bool(result.get("ready")):
            lines.append(f"- /todo {result.get('alias')}")
        send(
            "\n".join(lines),
            context="orch-repair",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd in {"status", "orch-status"}:
        try:
            key, entry, p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "orch status blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-status blocked",
                    with_menu=True,
                )
                return True
            raise
        lock = manager_state.get("project_lock") if isinstance(manager_state, dict) else {}
        lock_key = str((lock or {}).get("project_key", "")).strip().lower() if isinstance(lock, dict) and bool(lock.get("enabled", False)) else ""
        lock_line = ""
        if lock_key:
            projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
            lock_entry = projects.get(lock_key) if isinstance(projects, dict) else {}
            lock_alias = str((lock_entry or {}).get("project_alias", "")).strip() or lock_key
            lock_line = f"project_lock: {lock_alias} ({lock_key})\n"
        active_tf_count = 0
        pending_tf = 0
        running_tf = 0
        tasks = entry.get("tasks")
        if isinstance(tasks, dict):
            for task in tasks.values():
                if not isinstance(task, dict):
                    continue
                status = str(task.get("status", "pending")).strip().lower() or "pending"
                if status == "running":
                    running_tf += 1
                elif status == "pending":
                    pending_tf += 1
                else:
                    continue
        active_tf_count = pending_tf + running_tf
        status = ""
        try:
            team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser()
            cfg = (team_dir / "orchestrator.json").resolve() if str(team_dir) else None
            if cfg and not cfg.exists():
                status = (
                    "[WARN] runtime config missing (orchestrator.json)\n"
                    f"missing: {cfg}\n"
                    f"fix: /orch repair {str(entry.get('project_alias', '')).strip() or key}"
                )
            else:
                status = run_aoe_status(p_args)
        except Exception as exc:
            status = f"[WARN] status unavailable: {exc}"
        queue_line = ""
        worker_line = ""
        scheduler_line = ""
        model_routing_line = ""
        model_registry_line = ""
        judge_binding_line = ""
        judge_probe_line = ""
        judge_bridge_line = ""
        replan_auto_routing_policy_line = ""
        replan_auto_route_status_line = ""
        manual_step_line = ""
        canonical_writeback_line = ""
        canonical_mutation_line = ""
        worker_module_line = ""
        worker_policy_line = ""
        worker_gate_line = ""
        worker_profile_line = ""
        worker_checklist_line = ""
        worker_apply_accept_line = ""
        worker_syncback_line = ""
        escalation_binding_line = ""
        escalation_probe_line = ""
        document_registry_line = ""
        workspace_line = ""
        try:
            team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser()
            if str(team_dir):
                queue_path = background_runs.background_runs_state_path(team_dir)
                adapter_poll = poll_background_tickets_via_adapters(queue_path=queue_path, now_iso=now_iso)
                tmux_poll = adapter_poll.get("local_tmux") if isinstance(adapter_poll.get("local_tmux"), dict) else {}
                external_poll = adapter_poll.get("external") if isinstance(adapter_poll.get("external"), dict) else {}
                if (bool(tmux_poll.get("changed")) or bool(external_poll.get("changed"))) and (not args.dry_run):
                    if _sync_background_run_snapshots_from_queue(entry, queue_path):
                        entry["updated_at"] = now_iso()
                        save_manager_state(args.manager_state_file, manager_state)
                queue_snapshot = background_runs.summarize_background_runs_state(queue_path)
                scheduler_snapshot = background_runs.summarize_background_runner_scheduling(
                    queue_path,
                    now_iso=now_iso,
                )
                worker_snapshot = background_runs.summarize_background_worker_state(
                    background_runs.background_worker_state_path(team_dir),
                    now_iso=now_iso,
                )
                model_routing_line = (
                    f"model_routing: {summarize_model_routing(team_dir, entry=entry)}\n"
                )
                model_registry_line = (
                    f"model_registry: {summarize_model_endpoint_registry(team_dir, entry=entry)}\n"
                )
                judge_binding_line, judge_probe_line = _judge_binding_lines(entry, team_dir)
                alias = str(entry.get("project_alias", "")).strip() or str(key).strip()
                latest_judge_decision_bridge_summary = load_latest_judge_decision_bridge_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                latest_replan_auto_routing_policy_summary = load_latest_replan_auto_routing_policy_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                latest_replan_auto_route_status_summary = load_latest_replan_auto_route_status_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                latest_manual_step_summary = load_latest_manual_step_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                latest_canonical_writeback_summary = load_latest_canonical_writeback_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                latest_canonical_mutation_summary = load_latest_canonical_mutation_summary_for_runtime(
                    team_dir,
                    project_alias=alias,
                )
                if latest_judge_decision_bridge_summary not in {"", "-"}:
                    judge_bridge_line = (
                        f"latest_judge_decision_bridge: {latest_judge_decision_bridge_summary}\n"
                    )
                if latest_replan_auto_routing_policy_summary not in {"", "-"}:
                    replan_auto_routing_policy_line = (
                        f"replan_auto_routing_policy: {latest_replan_auto_routing_policy_summary}\n"
                    )
                if latest_replan_auto_route_status_summary not in {"", "-"}:
                    replan_auto_route_status_line = (
                        f"auto_route_status: {latest_replan_auto_route_status_summary}\n"
                    )
                if latest_manual_step_summary not in {"", "-"}:
                    manual_step_line = f"manual_step: {latest_manual_step_summary}\n"
                if latest_canonical_writeback_summary not in {"", "-"}:
                    canonical_writeback_line = (
                        f"canonical_writeback: {latest_canonical_writeback_summary[:240]}\n"
                    )
                if latest_canonical_mutation_summary not in {"", "-"}:
                    canonical_mutation_line = (
                        f"canonical_mutation: {latest_canonical_mutation_summary[:240]}\n"
                    )
                latest_task = _latest_task_for_model_status(entry)
                latest_manual_step_execution_summary = str(
                    (latest_task or {}).get("background_run_manual_step_execution_summary", "")
                ).strip()
                if latest_manual_step_execution_summary not in {"", "-"} and latest_manual_step_summary in {"", "-"}:
                    latest_manual_step_summary = latest_manual_step_execution_summary
                latest_worker_apply_accept_summary = str(
                    (latest_task or {}).get("background_run_worker_apply_accept_summary", "")
                ).strip()
                latest_worker_module_summary = str(
                    (latest_task or {}).get("background_run_task_contract_module_summary", "")
                ).strip()
                latest_worker_module = str(
                    (latest_task or {}).get("background_run_task_contract_module", "")
                ).strip().lower()
                if not latest_worker_module_summary and latest_worker_module not in {"", "-", "general"}:
                    latest_worker_module_summary = latest_worker_module
                latest_worker_policy_summary = str(
                    (latest_task or {}).get("background_run_task_contract_policy_summary", "")
                ).strip()
                if not latest_worker_policy_summary and latest_worker_module not in {"", "-", "general"}:
                    latest_worker_policy_summary = str(
                        worker_task_contract.resolve_worker_module_policy(
                            {"module_kind": latest_worker_module}
                        ).get("summary", "")
                    ).strip()
                if latest_worker_module_summary not in {"", "-"}:
                    worker_module_line = f"worker_module: {latest_worker_module_summary[:240]}\n"
                if latest_worker_policy_summary not in {"", "-"}:
                    worker_policy_line = f"worker_policy: {latest_worker_policy_summary[:240]}\n"
                latest_worker_gate_summary = str(
                    (latest_task or {}).get("background_run_worker_gate_summary", "")
                ).strip()
                if (
                    latest_worker_gate_summary in {"", "-"}
                    and latest_worker_module not in {"", "-", "general"}
                    and (
                        str((latest_task or {}).get("background_run_worker_result_summary", "")).strip()
                        or (latest_task or {}).get("background_run_worker_result_actions")
                    )
                ):
                    latest_worker_gate_summary = str(
                        worker_task_contract.derive_worker_task_module_gate(
                            {
                                "module_kind": latest_worker_module,
                                "module_policy": (latest_task or {}).get("background_run_task_contract_policy"),
                                "artifact_targets": (latest_task or {}).get("background_run_worker_update_stub_targets"),
                            },
                            {
                                "status": (latest_task or {}).get("background_run_worker_result_status"),
                                "summary": (latest_task or {}).get("background_run_worker_result_summary"),
                                "actions": (latest_task or {}).get("background_run_worker_result_actions"),
                                "cautions": (latest_task or {}).get("background_run_worker_result_cautions"),
                                "evidence_refs": (latest_task or {}).get("background_run_worker_result_evidence_refs"),
                            },
                        ).get("summary_line", "")
                    ).strip()
                if latest_worker_gate_summary not in {"", "-"}:
                    worker_gate_line = f"worker_gate: {latest_worker_gate_summary[:240]}\n"
                latest_worker_profile_summary = str(
                    (latest_task or {}).get("background_run_worker_profile_summary", "")
                ).strip()
                if (
                    latest_worker_profile_summary in {"", "-"}
                    and latest_worker_module not in {"", "-", "general"}
                    and latest_worker_gate_summary not in {"", "-"}
                ):
                    latest_worker_profile_summary = str(
                        worker_task_contract.derive_worker_task_module_profile(
                            {
                                "module_kind": latest_worker_module,
                                "module_policy": (latest_task or {}).get("background_run_task_contract_policy"),
                                "artifact_targets": (latest_task or {}).get("background_run_worker_update_stub_targets"),
                            },
                            {
                                "status": (latest_task or {}).get("background_run_worker_result_status"),
                                "summary": (latest_task or {}).get("background_run_worker_result_summary"),
                                "actions": (latest_task or {}).get("background_run_worker_result_actions"),
                                "cautions": (latest_task or {}).get("background_run_worker_result_cautions"),
                                "evidence_refs": (latest_task or {}).get("background_run_worker_result_evidence_refs"),
                            },
                            gate={
                                "state": (latest_task or {}).get("background_run_worker_gate_status"),
                                "summary_line": latest_worker_gate_summary,
                            },
                        ).get("summary_line", "")
                    ).strip()
                if latest_worker_profile_summary not in {"", "-"}:
                    worker_profile_line = f"worker_profile: {latest_worker_profile_summary[:240]}\n"
                latest_worker_checklist_summary = str(
                    (latest_task or {}).get("background_run_worker_checklist_summary", "")
                ).strip()
                if (
                    latest_worker_checklist_summary in {"", "-"}
                    and latest_worker_module not in {"", "-", "general"}
                    and latest_worker_profile_summary not in {"", "-"}
                ):
                    latest_worker_checklist_summary = str(
                        worker_task_contract.derive_worker_task_module_checklist(
                            {
                                "module_kind": latest_worker_module,
                                "module_policy": (latest_task or {}).get("background_run_task_contract_policy"),
                                "artifact_targets": (latest_task or {}).get("background_run_worker_update_stub_targets"),
                            },
                            {
                                "status": (latest_task or {}).get("background_run_worker_result_status"),
                                "summary": (latest_task or {}).get("background_run_worker_result_summary"),
                                "actions": (latest_task or {}).get("background_run_worker_result_actions"),
                                "cautions": (latest_task or {}).get("background_run_worker_result_cautions"),
                                "evidence_refs": (latest_task or {}).get("background_run_worker_result_evidence_refs"),
                            },
                            gate={
                                "state": (latest_task or {}).get("background_run_worker_gate_status"),
                                "summary_line": latest_worker_gate_summary,
                            },
                            profile={
                                "state": (latest_task or {}).get("background_run_worker_profile_status"),
                                "summary_line": latest_worker_profile_summary,
                            },
                        ).get("summary_line", "")
                    ).strip()
                if latest_worker_checklist_summary not in {"", "-"}:
                    worker_checklist_line = f"worker_checklist: {latest_worker_checklist_summary[:240]}\n"
                if latest_worker_apply_accept_summary not in {"", "-"}:
                    worker_apply_accept_line = (
                        f"worker_apply_accept: {latest_worker_apply_accept_summary[:240]}\n"
                    )
                latest_worker_syncback_summary = str(
                    (latest_task or {}).get("background_run_worker_syncback_summary", "")
                ).strip()
                if latest_worker_syncback_summary not in {"", "-"}:
                    worker_syncback_line = (
                        f"worker_syncback: {latest_worker_syncback_summary[:240]}\n"
                    )
                latest_canonical_writeback_task_summary = str(
                    (latest_task or {}).get("background_run_canonical_writeback_summary", "")
                ).strip()
                if latest_canonical_writeback_task_summary not in {"", "-"}:
                    latest_canonical_writeback_summary = latest_canonical_writeback_task_summary
                latest_canonical_mutation_task_summary = str(
                    (latest_task or {}).get("background_run_canonical_mutation_summary", "")
                ).strip()
                if latest_canonical_mutation_task_summary not in {"", "-"}:
                    latest_canonical_mutation_summary = latest_canonical_mutation_task_summary
                manual_step_line = (
                    f"manual_step: {latest_manual_step_summary[:240]}\n"
                    if latest_manual_step_summary not in {"", "-"}
                    else ""
                )
                canonical_writeback_line = (
                    f"canonical_writeback: {latest_canonical_writeback_summary[:240]}\n"
                    if latest_canonical_writeback_summary not in {"", "-"}
                    else ""
                )
                canonical_mutation_line = (
                    f"canonical_mutation: {latest_canonical_mutation_summary[:240]}\n"
                    if latest_canonical_mutation_summary not in {"", "-"}
                    else ""
                )
                escalation_binding_line, escalation_probe_line = _escalation_binding_lines(entry, team_dir)
                workspace_line = (
                    f"workspace: {summarize_workspace_brief(team_dir, entry=entry, project_root=entry.get('project_root'))}\n"
                )
                document_registry_line = (
                    f"document_registry: {summarize_document_registry(team_dir, entry=entry, project_root=entry.get('project_root'))}\n"
                )
                queue_line = f"background_queue: {str(queue_snapshot.get('summary', '-')).strip() or '-'}\n"
                scheduler_line = f"background_scheduler: {str(scheduler_snapshot.get('summary', '-')).strip() or '-'}\n"
                worker_line = f"background_worker: {str(worker_snapshot.get('summary', '-')).strip() or '-'}\n"
        except Exception:
            queue_line = ""
            worker_line = ""
            scheduler_line = ""
            model_routing_line = ""
            model_registry_line = ""
            judge_binding_line = ""
            judge_probe_line = ""
            judge_bridge_line = ""
            replan_auto_routing_policy_line = ""
            replan_auto_route_status_line = ""
            manual_step_line = ""
            canonical_writeback_line = ""
            canonical_mutation_line = ""
            worker_module_line = ""
            worker_policy_line = ""
            worker_gate_line = ""
            worker_profile_line = ""
            worker_checklist_line = ""
            worker_apply_accept_line = ""
            worker_syncback_line = ""
            escalation_binding_line = ""
            escalation_probe_line = ""
            document_registry_line = ""
            workspace_line = ""
        runner_pref, runner_effective, runner_note = _background_runner_status(entry, key)
        run_lock_mode, run_lock_note = _project_run_lock_status(entry)
        preferred_runner = _background_runner_preference(entry)
        slot_runner_target = preferred_runner if preferred_runner in {"local_tmux", "github_runner", "remote_worker"} else ""
        slot_limit = _background_runner_slot_limit_for_target(entry, slot_runner_target)
        active_slots = 0
        slot_summary = "-"
        try:
            team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser()
            if str(team_dir):
                slot_snapshot = background_runs.summarize_background_runner_slots(
                    background_runs.background_runs_state_path(team_dir),
                    entry,
                    selected_runner=slot_runner_target,
                    statuses=["queued", "dispatching", "running"],
                    max_value=_BACKGROUND_SLOT_MAX,
                )
                active_slots = int(slot_snapshot.get("selected_active", 0) or 0)
                slot_summary = str(slot_snapshot.get("summary", "")).strip() or "-"
        except Exception:
            active_slots = 0
            slot_summary = "-"
        runner_line = f"background_runner: pref={runner_pref} | effective={runner_effective}\n"
        runner_note_line = f"background_runner_note: {runner_note}\n" if runner_note else ""
        run_lock_line = f"run_lock: {run_lock_mode}\n"
        run_lock_note_line = f"run_lock_note: {run_lock_note}\n" if run_lock_note else ""
        slot_runner_prefix = f"runner={slot_runner_target} " if slot_runner_target else ""
        slots_line = f"background_slots: {slot_runner_prefix}limit={slot_limit} active={active_slots} | {slot_summary}\n"
        external_snapshot = _latest_external_background_task_snapshot(entry)
        external_line = ""
        external_next_line = ""
        if external_snapshot:
            external_priority = external_background_priority_action_snapshot(
                alias=str(entry.get("project_alias", "")).strip().upper() or str(key).strip(),
                task_label=str(external_snapshot.get("label", "")).strip(),
                background_run_runner_target=str(external_snapshot.get("runner_target", "")).strip(),
                background_run_external_phase=str(external_snapshot.get("phase", "")).strip(),
                background_run_external_note=str(external_snapshot.get("note", "")).strip(),
                run_lock_mode=run_lock_mode,
            )
            external_line = (
                "background_external: {label} | {runner} | {phase} | {note}\n".format(
                    label=external_snapshot.get("label", "-"),
                    runner=external_snapshot.get("runner_target", "-"),
                    phase=external_snapshot.get("phase", "-"),
                    note=external_snapshot.get("note", "-"),
                )
            )
            if str(external_priority.get("action", "")).strip():
                external_next_line = (
                    "background_external_next: {action} | {reason}\n".format(
                        action=str(external_priority.get("action", "")).strip(),
                        reason=str(external_priority.get("reason", "")).strip() or "-",
                    )
                )
        send(
            f"runtime: {key}\nroot: {entry.get('project_root')}\nteam: {entry.get('team_dir')}\n{lock_line}last_request: {entry.get('last_request_id') or '-'}\n"
            f"active_team_count: {active_tf_count} (pending={pending_tf} running={running_tf})\n"
            f"{runner_line}{runner_note_line}{run_lock_line}{run_lock_note_line}{workspace_line}{document_registry_line}{model_routing_line}{model_registry_line}{judge_binding_line}{judge_probe_line}{judge_bridge_line}{replan_auto_routing_policy_line}{replan_auto_route_status_line}{manual_step_line}{canonical_writeback_line}{canonical_mutation_line}{worker_module_line}{worker_policy_line}{worker_gate_line}{worker_profile_line}{worker_checklist_line}{worker_apply_accept_line}{worker_syncback_line}{escalation_binding_line}{escalation_probe_line}{slots_line}{queue_line}{scheduler_line}{worker_line}{external_line}{external_next_line}\n{status}",
            context="status",
            with_menu=False,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-bgq-clean":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "background queue cleanup blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-bgq-clean blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "background queue cleanup blocked\n"
                f"- runtime: {key}\n"
                "- reason: team_dir missing\n"
                f"- next: /orch repair {alias}",
                context="orch-bgq-clean blocked",
                with_menu=True,
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        queue_path = background_runs.background_runs_state_path(team_dir)
        before = background_runs.summarize_background_runs_state(queue_path)
        marked = background_runs.mark_stale_background_run_tickets(queue_path, now_iso=now_iso)
        after = background_runs.summarize_background_runs_state(queue_path)
        send(
            "background queue cleanup\n"
            f"- runtime: {key}\n"
            f"- queue_path: {queue_path}\n"
            f"- before: {str(before.get('summary', '-')).strip() or '-'}\n"
            f"- marked_stale: {int(marked.get('stale_count', 0) or 0)}\n"
            f"- after: {str(after.get('summary', '-')).strip() or '-'}\n"
            "next:\n"
            f"- /orch status {alias}",
            context="orch-bgq-clean",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-bg-runner":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "background runner preference blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-bg-runner blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        target = str(rest or "").strip().lower()
        if target not in _BACKGROUND_RUNNER_PREFS:
            send(
                "usage: /orch bg-runner <O#|name> <local_background|local_tmux|github_runner|remote_worker>",
                context="orch-bg-runner usage",
                with_menu=True,
            )
            return True
        entry["background_runner_target"] = target
        entry["updated_at"] = now_iso()
        save_manager_state(args.manager_state_file, manager_state)
        preferred, effective, note = _background_runner_status(entry, key)
        team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser().resolve()
        append_action_audit_row(
            team_dir,
            headline="Background Runner Target | configured",
            status="configured",
            outcome_kind="background_runner",
            outcome_status="configured",
            outcome_reason_code=preferred,
            outcome_detail=(f"preferred={preferred} | effective={effective}" + (f" | note={note}" if note else "")),
            next_step=f"/orch status {alias}",
            remediation=(note or "future detached/background launches will use this runner preference when eligible"),
            source_command=f"/orch bg-runner {alias} {preferred}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        lines = [
            "background runner preference",
            f"- runtime: {key}",
            f"- preferred: {preferred}",
            f"- effective: {effective}",
        ]
        if note:
            lines.append(f"- note: {note}")
        lines.extend(["next:", f"- /orch status {alias}"])
        send(
            "\n".join(lines),
            context="orch-bg-runner",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-run-lock":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "run lock update blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-run-lock blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        target = normalize_run_lock_mode(rest)
        if target not in _RUN_LOCK_MODES:
            send(
                "usage: /orch run-lock <O#|name> <open|test_only>",
                context="orch-run-lock usage",
                with_menu=True,
            )
            return True
        entry["run_lock_mode"] = target
        entry["updated_at"] = now_iso()
        save_manager_state(args.manager_state_file, manager_state)
        note = project_run_lock_note(entry)
        team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser().resolve()
        append_action_audit_row(
            team_dir,
            headline="Run Lock | configured",
            status="configured",
            outcome_kind="run_lock",
            outcome_status="configured",
            outcome_reason_code=target,
            outcome_detail=(f"mode={target}" + (f" | note={note}" if note else "")),
            next_step=f"/orch status {alias}",
            remediation=(note or "future rerun and detached execution will follow this lock mode"),
            source_command=f"/orch run-lock {alias} {target}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        lines = [
            "run lock",
            f"- runtime: {key}",
            f"- mode: {target}",
        ]
        if note:
            lines.append(f"- note: {note}")
        lines.extend(["next:", f"- /orch status {alias}"])
        send(
            "\n".join(lines),
            context="orch-run-lock",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-bg-slots":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "background slot update blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-bg-slots blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        parsed_slot = _background_slot_command_target(str(rest or "").strip())
        if not parsed_slot:
            send(
                "usage: /orch bg-slots <O#|name> [<local_tmux|github_runner|remote_worker>] <limit>",
                context="orch-bg-slots usage",
                with_menu=True,
            )
            return True
        runner_target, target = parsed_slot
        if runner_target:
            limits = entry.get("background_runner_slot_limits") if isinstance(entry.get("background_runner_slot_limits"), dict) else {}
            limits = dict(limits)
            limits[runner_target] = target
            entry["background_runner_slot_limits"] = limits
        else:
            entry["background_runner_slot_limit"] = target
        entry["updated_at"] = now_iso()
        save_manager_state(args.manager_state_file, manager_state)
        team_dir = Path(str(entry.get("team_dir", "") or "")).expanduser().resolve()
        detail = f"runner_target={runner_target} slot_limit={target}" if runner_target else f"slot_limit={target}"
        append_action_audit_row(
            team_dir,
            headline="Background Slots | configured",
            status="configured",
            outcome_kind="background_slots",
            outcome_status="configured",
            outcome_reason_code=(runner_target or str(target)),
            outcome_detail=detail,
            next_step=f"/orch status {alias}",
            remediation="future background launches for the selected runner will respect this active slot limit",
            source_command=(f"/orch bg-slots {alias} {runner_target} {target}" if runner_target else f"/orch bg-slots {alias} {target}"),
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        limit_summary = " ".join(
            f"{name}={value}"
            for name, value in _background_runner_slot_limits(entry).items()
        )
        send(
            "background slots\n"
            f"- runtime: {key}\n"
            + (f"- runner: {runner_target}\n" if runner_target else "")
            + f"- limit: {target}\n"
            + f"- by_runner: {limit_summary}\n"
            "next:\n"
            f"- /orch status {alias}",
            context="orch-bg-slots",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd in {"orch-bgw-status", "orch-bgw-start", "orch-bgw-stop", "orch-bgw-ping", "orch-bgw-task"}:
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "background worker command blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context=f"{cmd} blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "background worker command blocked\n"
                f"- runtime: {key}\n"
                "- reason: team_dir missing\n"
                f"- next: /orch repair {alias}",
                context=f"{cmd} blocked",
                with_menu=True,
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        queue_path = background_runs.background_runs_state_path(team_dir)
        worker_path = background_runs.background_worker_state_path(team_dir)
        if cmd == "orch-bgw-ping":
            run_lock_mode = project_run_lock_mode(entry)
            if run_lock_mode != "test_only":
                send(
                    "background worker ping blocked\n"
                    f"- runtime: {key}\n"
                    f"- run_lock: {run_lock_mode or 'open'}\n"
                    "- reason: ping harness is limited to test_only runtimes\n"
                    "next:\n"
                    f"- /orch status {alias}",
                    context="orch-bgw-ping blocked",
                    with_menu=True,
                    reply_markup=_orch_status_reply_markup(manager_state, key, entry),
                )
                return True
            created_at = now_iso()
            stamp = "".join(ch for ch in created_at if ch.isdigit())[:20] or "PING"
            request_id = f"REQ-BGW-PING-{alias}-{stamp}"[:96]
            launch_spec = build_local_background_provider_invoke_launch_spec(
                request_id=request_id,
                project_key=str(key or "").strip(),
                project_root=str(entry.get("project_root", "") or "").strip(),
                team_dir=str(team_dir),
                manager_state_file=str(args.manager_state_file),
                launch_mode="orch_bgw_ping",
                source_surface="orch_bgw_ping",
                created_by=f"telegram:{chat_id}",
                prompt=_BACKGROUND_WORKER_PING_PROMPT,
                system=_BACKGROUND_WORKER_PING_SYSTEM,
                timeout_sec=20,
            )
            ticket = build_background_run_ticket(
                request_id=request_id,
                project_key=str(key or "").strip(),
                execution_brief_status="executable",
                runner_target="local_background",
                launch_mode="orch_bgw_ping",
                created_at=created_at,
                created_by=f"telegram:{chat_id}",
                source_surface="orch_bgw_ping",
                status="queued",
                launch_spec=launch_spec,
            )
            background_runs.upsert_background_run_ticket(queue_path, ticket, now_iso=now_iso)
            try:
                run_local_background_ticket(
                    queue_path=queue_path,
                    ticket_id=str(ticket.get("ticket_id", "")).strip(),
                    now_iso=now_iso,
                    run_target=lambda: None,
                    on_ticket_update=lambda _ticket: None,
                    on_queue_error=lambda _event_name, _exc: None,
                    runner_target="local_background",
                    launch_mode="orch_bgw_ping",
                    claimed_by=f"telegram:{chat_id}",
                    source_surface="orch_bgw_ping",
                )
            except Exception:
                pass
            queue_snapshot = background_runs.summarize_background_runs_state(queue_path)
            worker_snapshot = background_runs.summarize_background_worker_state(worker_path, now_iso=now_iso)
            final_ticket = background_runs.get_background_run_ticket(queue_path, str(ticket.get("ticket_id", "")).strip())
            final_status = str(final_ticket.get("status", "")).strip() or "unknown"
            final_runtime = str(final_ticket.get("runtime_summary", "-")).strip() or "-"
            final_evidence = str(final_ticket.get("evidence_bundle", "-")).strip() or "-"
            append_action_audit_row(
                team_dir,
                headline="Background Worker Ping | executed",
                status="executed" if final_status == "completed" else "blocked",
                outcome_kind="background_worker",
                outcome_status="executed" if final_status == "completed" else "blocked",
                outcome_reason_code=(final_status or "unknown").lower(),
                outcome_detail=final_runtime,
                next_step=f"/orch status {alias}",
                remediation="inspect bound worker route, probe status, and queue summary if the ping did not complete",
                source_command=f"/orch bgw-ping {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            send(
                "background worker ping\n"
                f"- runtime: {key}\n"
                f"- ticket: {str(ticket.get('ticket_id', '')).strip() or '-'}\n"
                f"- status: {final_status}\n"
                f"- runtime_summary: {final_runtime}\n"
                f"- evidence: {final_evidence}\n"
                f"- worker: {str(worker_snapshot.get('summary', '-')).strip() or '-'}\n"
                f"- queue: {str(queue_snapshot.get('summary', '-')).strip() or '-'}\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-bgw-ping",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        if cmd == "orch-bgw-task":
            run_lock_mode = project_run_lock_mode(entry)
            if run_lock_mode != "test_only":
                send(
                    "background worker task invoke blocked\n"
                    f"- runtime: {key}\n"
                    f"- run_lock: {run_lock_mode or 'open'}\n"
                    "- reason: task-scoped provider harness is limited to test_only runtimes\n"
                    "next:\n"
                    f"- /orch status {alias}",
                    context="orch-bgw-task blocked",
                    with_menu=True,
                    reply_markup=_orch_status_reply_markup(manager_state, key, entry),
                )
                return True
            latest_task = _latest_task_for_model_status(entry)
            if not latest_task:
                send(
                    "background worker task invoke blocked\n"
                    f"- runtime: {key}\n"
                    "- reason: no latest task found\n"
                    "next:\n"
                    f"- /orch status {alias}",
                    context="orch-bgw-task blocked",
                    with_menu=True,
                    reply_markup=_orch_status_reply_markup(manager_state, key, entry),
                )
                return True
            task_label = str(task_display_label(latest_task)).strip() or (
                str(latest_task.get("short_id", "")).strip()
                or str(latest_task.get("request_id", "")).strip()
                or "-"
            )
            active_bg_status = str(latest_task.get("background_run_status", "")).strip().lower()
            if active_bg_status in {"queued", "dispatching", "running"}:
                send(
                    "background worker task invoke blocked\n"
                    f"- runtime: {key}\n"
                    f"- task: {task_label}\n"
                    f"- background_run: {active_bg_status}\n"
                    "- reason: active background run already exists for the latest task\n"
                    "next:\n"
                    f"- /orch status {alias}",
                    context="orch-bgw-task blocked",
                    with_menu=True,
                    reply_markup=_orch_status_reply_markup(manager_state, key, entry),
                )
                return True
            request_id = str(latest_task.get("request_id", "")).strip()
            if not request_id:
                send(
                    "background worker task invoke blocked\n"
                    f"- runtime: {key}\n"
                    "- reason: latest task is missing request_id\n"
                    "next:\n"
                    f"- /orch status {alias}",
                    context="orch-bgw-task blocked",
                    with_menu=True,
                    reply_markup=_orch_status_reply_markup(manager_state, key, entry),
                )
                return True
            contract = worker_task_contract.build_worker_task_contract(
                team_dir,
                entry=entry,
                task=latest_task,
                project_root=entry.get("project_root"),
                pack_profile_override="offdesk_execute",
            )
            launch_spec = build_local_background_provider_task_launch_spec(
                request_id=request_id,
                project_key=str(key or "").strip(),
                project_root=str(entry.get("project_root", "") or "").strip(),
                team_dir=str(team_dir),
                manager_state_file=str(args.manager_state_file),
                launch_mode="orch_bgw_task",
                source_surface="orch_bgw_task",
                created_by=f"telegram:{chat_id}",
                task_contract_json=json.dumps(contract, ensure_ascii=False),
                task_contract_summary=str(contract.get("summary", "")).strip(),
                task_contract_profile=str(contract.get("pack_profile", "")).strip(),
                task_contract_module=str(contract.get("module_kind", "")).strip(),
                task_contract_module_summary=str(contract.get("module_summary", "")).strip(),
                task_contract_policy=str(contract.get("module_policy", "")).strip(),
                task_contract_policy_summary=str(contract.get("module_policy_summary", "")).strip(),
                timeout_sec=45,
            )
            ticket = build_background_run_ticket(
                request_id=request_id,
                project_key=str(key or "").strip(),
                execution_brief_status=str(latest_task.get("execution_brief_status", "")).strip() or "executable",
                runner_target="local_background",
                launch_mode="orch_bgw_task",
                created_at=now_iso(),
                created_by=f"telegram:{chat_id}",
                source_surface="orch_bgw_task",
                status="queued",
                launch_spec=launch_spec,
            )
            background_runs.upsert_background_run_ticket(queue_path, ticket, now_iso=now_iso)
            try:
                run_local_background_ticket(
                    queue_path=queue_path,
                    ticket_id=str(ticket.get("ticket_id", "")).strip(),
                    now_iso=now_iso,
                    run_target=lambda: None,
                    on_ticket_update=lambda _ticket: None,
                    on_queue_error=lambda _event_name, _exc: None,
                    runner_target="local_background",
                    launch_mode="orch_bgw_task",
                    claimed_by=f"telegram:{chat_id}",
                    source_surface="orch_bgw_task",
                )
            except Exception:
                pass
            if not args.dry_run and _sync_background_run_snapshots_from_queue(entry, queue_path):
                entry["updated_at"] = now_iso()
                save_manager_state(args.manager_state_file, manager_state)
            final_ticket = background_runs.get_background_run_ticket(queue_path, str(ticket.get("ticket_id", "")).strip())
            final_status = str(final_ticket.get("status", "")).strip() or "unknown"
            final_runtime = str(final_ticket.get("runtime_summary", "-")).strip() or "-"
            final_evidence = str(final_ticket.get("evidence_bundle", "-")).strip() or "-"
            final_worker_result = str(final_ticket.get("worker_result_summary", "-")).strip() or "-"
            final_worker_update_stub = str(final_ticket.get("worker_update_stub_summary", "-")).strip() or "-"
            final_worker_proposals = str(latest_task.get("background_run_worker_update_proposal_summary", "")).strip() or "-"
            append_action_audit_row(
                team_dir,
                headline="Background Worker Task Invoke | executed",
                status="executed" if final_status == "completed" else "blocked",
                outcome_kind="background_worker",
                outcome_status="executed" if final_status == "completed" else "blocked",
                outcome_reason_code=(final_status or "unknown").lower(),
                outcome_detail=(f"{final_runtime} | {final_worker_result}" if final_worker_result and final_worker_result != "-" else final_runtime)[:240],
                next_step=f"/orch status {alias}",
                remediation="inspect task detail, context pack, and background evidence before re-running the bounded worker task invoke",
                source_command=f"/orch bgw-task {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            response_hint = final_evidence.split("response=", 1)[1] if "response=" in final_evidence else ""
            if not response_hint:
                response_hint = final_worker_result if final_worker_result and final_worker_result != "-" else "-"
            send(
                "background worker task invoke\n"
                f"- runtime: {key}\n"
                f"- task: {task_label}\n"
                f"- contract: {str(contract.get('summary', '')).strip() or '-'}\n"
                f"- status: {final_status}\n"
                f"- runtime_summary: {final_runtime}\n"
                f"- worker_result: {final_worker_result}\n"
                f"- update_stub: {final_worker_update_stub}\n"
                f"- proposal_stub: {final_worker_proposals}\n"
                f"- response: {response_hint or '-'}\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-bgw-task",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        if cmd == "orch-bgw-start":
            started = ensure_local_background_daemon(
                queue_path=queue_path,
                now_iso=now_iso,
                runner_target="local_background",
                launch_mode="orch_bgw_start",
                claimed_by=f"telegram:{chat_id}",
                source_surface="orch_bgw_start",
                interval_sec=1.0,
                idle_sec=4.0,
                stale_after_sec=900,
                max_items=8,
            )
            worker_snapshot = background_runs.summarize_background_worker_state(worker_path, now_iso=now_iso)
            queue_summary = str(background_runs.summarize_background_runs_state(queue_path).get("summary", "-")).strip() or "-"
            started_ok = bool(started.get("started"))
            append_action_audit_row(
                team_dir,
                headline="Background Worker Start | executed",
                status="executed",
                outcome_kind="background_worker",
                outcome_status="executed",
                outcome_reason_code="started" if started_ok else "already_running",
                outcome_detail=(worker_snapshot.get("summary") or "-"),
                next_step=f"/orch status {alias}",
                remediation=(
                    "inspect /orch status and background_worker.json if the queue does not begin draining"
                    if started_ok
                    else "worker was already running; inspect /orch status and background queue depth before restarting again"
                ),
                source_command=f"/orch bgw-start {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            send(
                "background worker start\n"
                f"- runtime: {key}\n"
                f"- started: {'yes' if bool(started.get('started')) else 'already_running'}\n"
                f"- worker: {str(worker_snapshot.get('summary', '-')).strip() or '-'}\n"
                f"- queue: {queue_summary}\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-bgw-start",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        if cmd == "orch-bgw-stop":
            stopped = stop_local_background_daemon(queue_path=queue_path, wait_sec=2.0)
            worker_snapshot = background_runs.summarize_background_worker_state(worker_path, now_iso=now_iso)
            stopped_ok = bool(stopped.get("stopped"))
            append_action_audit_row(
                team_dir,
                headline="Background Worker Stop | executed",
                status="executed",
                outcome_kind="background_worker",
                outcome_status="executed",
                outcome_reason_code="stopped" if stopped_ok else "already_stopped",
                outcome_detail=(worker_snapshot.get("summary") or "-"),
                next_step=f"/orch status {alias}",
                remediation=(
                    "inspect queued tickets before starting the worker again or cleaning stale queue rows"
                    if stopped_ok
                    else "worker was already stopped; inspect queue depth before taking more action"
                ),
                source_command=f"/orch bgw-stop {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            send(
                "background worker stop\n"
                f"- runtime: {key}\n"
                f"- stopped: {'yes' if bool(stopped.get('stopped')) else 'no'}\n"
                f"- alive: {'yes' if bool(stopped.get('alive')) else 'no'}\n"
                f"- worker: {str(worker_snapshot.get('summary', '-')).strip() or '-'}\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-bgw-stop",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        adapter_poll = poll_background_tickets_via_adapters(queue_path=queue_path, now_iso=now_iso)
        tmux_poll = adapter_poll.get("local_tmux") if isinstance(adapter_poll.get("local_tmux"), dict) else {}
        external_poll = adapter_poll.get("external") if isinstance(adapter_poll.get("external"), dict) else {}
        if (bool(tmux_poll.get("changed")) or bool(external_poll.get("changed"))) and (not args.dry_run):
            if _sync_background_run_snapshots_from_queue(entry, queue_path):
                entry["updated_at"] = now_iso()
                save_manager_state(args.manager_state_file, manager_state)
        worker_snapshot = background_runs.summarize_background_worker_state(worker_path, now_iso=now_iso)
        queue_snapshot = background_runs.summarize_background_runs_state(queue_path)
        append_action_audit_row(
            team_dir,
            headline="Background Worker Status | accepted",
            status="accepted",
            outcome_kind="background_worker",
            outcome_status="accepted",
            outcome_reason_code=str(worker_snapshot.get("status", "")).strip() or "unknown",
            outcome_detail=(
                "worker={worker} | queue={queue}".format(
                    worker=str(worker_snapshot.get("summary", "-")).strip() or "-",
                    queue=str(queue_snapshot.get("summary", "-")).strip() or "-",
                )
            ),
            next_step=f"/orch status {alias}",
            remediation=(
                "start the worker if queue depth is non-zero and status is stopped; inspect stale/error state before retrying execution"
            ),
            source_command=f"/orch bgw-status {alias}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        send(
            "background worker status\n"
            f"- runtime: {key}\n"
            f"- worker: {str(worker_snapshot.get('summary', '-')).strip() or '-'}\n"
            f"- queue: {str(queue_snapshot.get('summary', '-')).strip() or '-'}\n"
            "next:\n"
            f"- /orch status {alias}",
            context="orch-bgw-status",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-model-ping":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "model ping blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-model-ping blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        run_lock_mode = project_run_lock_mode(entry)
        if run_lock_mode != "test_only":
            send(
                "model ping blocked\n"
                f"- runtime: {key}\n"
                f"- run_lock: {run_lock_mode or 'open'}\n"
                "- reason: bounded model harness is limited to test_only runtimes\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-model-ping blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        kind = str(rest or "").strip().lower()
        if kind not in _MODEL_PING_SPECS:
            send(
                "usage: /orch model-ping <O#|name> <research|judge|escalation>",
                context="orch-model-ping usage",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "model ping blocked\n"
                f"- runtime: {key}\n"
                "- reason: team_dir missing\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-model-ping blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        latest_task = _latest_task_for_model_status(entry)
        token, pack_profile = _MODEL_PING_SPECS[kind]
        prompt = f"Reply with {token} only."
        system = "Return the exact token only."
        if kind == "research":
            result = model_provider_adapter.invoke_task_research_stub(
                team_dir,
                entry=entry,
                task=latest_task,
                prompt=prompt,
                system=system,
                pack_profile_override=pack_profile,
            )
        elif kind == "judge":
            result = model_provider_adapter.invoke_task_judge_stub(
                team_dir,
                entry=entry,
                task=latest_task,
                prompt=prompt,
                system=system,
                pack_profile_override=pack_profile,
                timeout_sec=60.0,
            )
        else:
            result = model_provider_adapter.invoke_task_escalation_stub(
                team_dir,
                entry=entry,
                task=latest_task,
                prompt=prompt,
                system=system,
                pack_profile_override=pack_profile,
            )
        ok = bool(result.get("ok"))
        executed = bool(result.get("executed"))
        summary = str(result.get("summary", "-")).strip() or "-"
        response_text = str(result.get("response_text", "")).strip()
        reason_code = str(result.get("reason_code", "")).strip() or ("ok" if ok else "not_executed")
        append_action_audit_row(
            team_dir,
            headline=f"Model Ping {kind.title()} | {'executed' if ok else 'blocked'}",
            status="executed" if ok else "blocked",
            outcome_kind="model_ping",
            outcome_status="executed" if ok else "blocked",
            outcome_reason_code=reason_code,
            outcome_detail=summary,
            next_step=f"/orch status {alias}",
            remediation="inspect binding summary and route probe status if the bounded invoke did not execute",
            source_command=f"/orch model-ping {alias} {kind}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        send(
            "model ping\n"
            f"- runtime: {key}\n"
            f"- kind: {kind}\n"
            f"- executed: {'yes' if executed else 'no'}\n"
            f"- ok: {'yes' if ok else 'no'}\n"
            f"- summary: {summary}\n"
            + (f"- response: {response_text}\n" if response_text else "")
            + "next:\n"
            f"- /orch status {alias}",
            context="orch-model-ping",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "orch-judge":
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "offdesk judge blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context="orch-judge blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "offdesk judge blocked\n"
                f"- runtime: {alias}\n"
                "- reason: team_dir missing\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-judge blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        latest_task = _latest_task_for_model_status(entry)
        if not latest_task:
            send(
                "offdesk judge blocked\n"
                f"- runtime: {alias}\n"
                "- reason: no task available for judge review\n"
                "next:\n"
                f"- /orch status {alias}",
                context="orch-judge blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        task_label = (
            str(latest_task.get("short_id", "")).strip().upper()
            or str(latest_task.get("alias", "")).strip()
            or str(latest_task.get("request_id", "")).strip()
            or "task"
        )
        binding = model_endpoint_adapter.resolve_task_judge_binding(
            team_dir,
            entry=entry,
            task=latest_task,
            pack_profile_override="review",
        )
        result = model_provider_adapter.invoke_task_judge_stub(
            team_dir,
            entry=entry,
            task=latest_task,
            prompt=_offdesk_judge_prompt(entry, latest_task, team_dir),
            system=_OFFDESK_JUDGE_SYSTEM,
            pack_profile_override="review",
            timeout_sec=120.0,
        )
        ok = bool(result.get("ok"))
        executed = bool(result.get("executed"))
        summary = str(result.get("summary", "-")).strip() or "-"
        response_text = str(result.get("response_text", "")).strip()
        reason_code = str(result.get("reason_code", "")).strip() or ("ok" if ok else "not_executed")
        judge_decision = normalize_offdesk_judge_decision(response_text)
        append_action_audit_row(
            team_dir,
            headline=f"Offdesk Judge | {'executed' if ok else 'blocked'}",
            status="executed" if ok else "blocked",
            outcome_kind="offdesk_judge",
            outcome_status="executed" if ok else "blocked",
            outcome_reason_code=reason_code,
            outcome_detail=summary,
            next_step=f"/offdesk review {alias}",
            remediation="inspect the judge response together with execution brief, followup brief, and runtime status before acting",
            source_command=f"/orch judge {alias}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
            extra={
                "response_text": response_text,
                "decision_snapshot": judge_decision,
            }
            if response_text or judge_decision
            else None,
        )
        send(
            "offdesk judge\n"
            f"- runtime: {alias}\n"
            f"- task: {task_label}\n"
            f"- binding: {str(binding.get('summary', '')).strip() or '-'}\n"
            f"- executed: {'yes' if executed else 'no'}\n"
            f"- ok: {'yes' if ok else 'no'}\n"
            f"- summary: {summary}\n"
            + (f"- response: {response_text}\n" if response_text else "")
            + "next:\n"
            f"- /offdesk review {alias}\n"
            f"- /orch status {alias}",
            context="orch-judge",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd in {"orch-bgx-status", "orch-bgx-handoff", "orch-bgx-ack", "orch-bgx-result"}:
        artifact_kind = ""
        if cmd == "orch-bgx-handoff":
            artifact_kind = "handoff"
        elif cmd == "orch-bgx-ack":
            artifact_kind = "ack"
        elif cmd == "orch-bgx-result":
            artifact_kind = "result"
        context_label = "orch-bgx-status" if not artifact_kind else f"orch-bgx-{artifact_kind}"
        headline_label = "External Background Status" if not artifact_kind else f"External Background {artifact_kind.title()}"
        source_command = f"/orch bgx-status {orch_target}" if not artifact_kind else f"/orch bgx-{artifact_kind} {orch_target}"
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "external background status blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context=f"{context_label} blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "external background status blocked\n"
                f"- runtime: {key}\n"
                "- reason: team_dir missing\n"
                f"- next: /orch repair {alias}",
                context=f"{context_label} blocked",
                with_menu=True,
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        queue_path = background_runs.background_runs_state_path(team_dir)
        adapter_poll = poll_background_tickets_via_adapters(queue_path=queue_path, now_iso=now_iso)
        external_poll = adapter_poll.get("external") if isinstance(adapter_poll.get("external"), dict) else {}
        if bool(external_poll.get("changed")) and (not args.dry_run):
            if _sync_background_run_snapshots_from_queue(entry, queue_path):
                entry["updated_at"] = now_iso()
                save_manager_state(args.manager_state_file, manager_state)
        snapshot = _external_background_artifact_snapshot(entry) if not artifact_kind else _external_background_artifact_detail(entry, artifact_kind)
        if not snapshot:
            append_action_audit_row(
                team_dir,
                headline=f"{headline_label} | accepted",
                status="accepted",
                outcome_kind="background_external",
                outcome_status="accepted",
                outcome_reason_code="missing",
                outcome_detail="no external background ticket found for this runtime",
                next_step=f"/orch status {alias}",
                remediation="inspect /orch status and background runner preference before expecting external lifecycle artifacts",
                source_command=source_command.replace(str(orch_target or "").strip(), alias),
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            send(
                f"{headline_label.lower()}\n"
                f"- runtime: {key}\n"
                "- external: none\n"
                "next:\n"
                f"- /orch status {alias}",
                context=context_label,
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        if not artifact_kind:
            external_priority = external_background_priority_action_snapshot(
                alias=alias,
                task_label=str(snapshot.get('label', '')).strip(),
                background_run_runner_target=str(snapshot.get('runner_target', '')).strip(),
                background_run_external_phase=str(snapshot.get('phase', '')).strip(),
                background_run_external_note=str(snapshot.get('note', '')).strip(),
                run_lock_mode=project_run_lock_mode(entry),
            )
            next_step = str(external_priority.get("action", "")).strip()
            if not next_step or next_step == f"/orch bgx-status {alias}":
                next_step = _external_background_next_step_for_inspect(alias, snapshot)
            remediation = str(external_priority.get("reason", "")).strip() or "inspect handoff, pickup ack, and result artifacts before the next operator step"
            append_action_audit_row(
                team_dir,
                headline=f"{headline_label} | accepted",
                status="accepted",
                outcome_kind="background_external",
                outcome_status="accepted",
                outcome_reason_code=str(snapshot.get("phase", "")).strip() or "present",
                outcome_detail=str(snapshot.get("note", "")).strip() or "-",
                next_step=next_step,
                remediation=remediation,
                source_command=f"/orch bgx-status {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            send(
                "external background status\n"
                f"- runtime: {key}\n"
                f"- task: {str(snapshot.get('label', '')).strip() or '-'}\n"
                f"- runner: {str(snapshot.get('runner_target', '')).strip() or '-'}\n"
                f"- phase: {str(snapshot.get('phase', '')).strip() or '-'}\n"
                f"- note: {str(snapshot.get('note', '')).strip() or '-'}\n"
                f"- handoff: {str(snapshot.get('handoff_path', '-')).strip() or '-'} | exists={str(snapshot.get('handoff_exists', '-')).strip() or '-'}\n"
                f"- ack: {str(snapshot.get('ack_path', '-')).strip() or '-'} | exists={str(snapshot.get('ack_exists', '-')).strip() or '-'}\n"
                f"- result: {str(snapshot.get('result_path', '-')).strip() or '-'} | exists={str(snapshot.get('result_exists', '-')).strip() or '-'}\n"
                "next:\n"
                f"- {next_step}",
                context=context_label,
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        artifact_path = str(snapshot.get("artifact_path", "")).strip() or "-"
        artifact_exists = str(snapshot.get("artifact_exists", "")).strip() or "no"
        artifact_parsed = str(snapshot.get("artifact_parsed", "")).strip() or "no"
        detail = snapshot.get("artifact_detail") if isinstance(snapshot.get("artifact_detail"), dict) else {}
        if artifact_kind == "result":
            outcome_reason_code = "result_present" if artifact_exists == "yes" else "result_missing"
            outcome_detail = artifact_path
            append_action_audit_row(
                team_dir,
                headline="External Background Result | accepted",
                status="accepted",
                outcome_kind="background_external",
                outcome_status="accepted",
                outcome_reason_code=outcome_reason_code,
                outcome_detail=outcome_detail,
                next_step=f"/orch bgx-status {alias}",
                remediation="inspect external summary status and then return to /orch bgx-status or /offdesk review",
                source_command=f"/orch bgx-result {alias}",
                link_label="runtime detail",
                link_href=_runtime_action_link(alias),
                at=now_iso(),
            )
            evidence_artifacts = ", ".join(str(item).strip() for item in list(detail.get("evidence_artifacts") or []) if str(item).strip()) or "-"
            send(
                "external background result\n"
                f"- runtime: {key}\n"
                f"- artifact: {artifact_path} | exists={artifact_exists} | parsed={artifact_parsed}\n"
                f"- result_status: {str(detail.get('status', '-')).strip() or '-'}\n"
                f"- reason: {str(detail.get('reason', '-')).strip() or '-'}\n"
                f"- summary: {str(detail.get('summary', '-')).strip() or '-'}\n"
                f"- evidence_bundle: {str(detail.get('evidence_bundle', '-')).strip() or '-'}\n"
                f"- evidence_artifacts: {evidence_artifacts}\n"
                "next:\n"
                f"- /orch bgx-status {alias}",
                context="orch-bgx-result",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        outcome_reason_code = f"{artifact_kind}_{'present' if artifact_exists == 'yes' else 'missing'}"
        outcome_detail = artifact_path
        append_action_audit_row(
            team_dir,
            headline=f"External Background {artifact_kind.title()} | accepted",
            status="accepted",
            outcome_kind="background_external",
            outcome_status="accepted",
            outcome_reason_code=outcome_reason_code,
            outcome_detail=outcome_detail,
            next_step=f"/orch bgx-status {alias}",
            remediation="inspect the external artifact detail and then return to /orch bgx-status",
            source_command=f"/orch bgx-{artifact_kind} {alias}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        evidence_artifacts = ", ".join(str(item).strip() for item in list(detail.get("evidence_artifacts") or []) if str(item).strip()) or "-"
        extra_lines = []
        for key_name in ("status", "worker_id", "summary", "launch_mode", "source_surface", "created_by", "execution_brief_status", "launch_spec_mode", "launch_spec_summary"):
            value = str(detail.get(key_name, "")).strip()
            if value:
                extra_lines.append(f"- {key_name}: {value}")
        if evidence_artifacts != "-":
            extra_lines.append(f"- evidence_artifacts: {evidence_artifacts}")
        detail_block = ("\n".join(extra_lines) + "\n") if extra_lines else ""
        send(
            f"external background {artifact_kind}\n"
            f"- runtime: {key}\n"
            f"- artifact: {artifact_path} | exists={artifact_exists} | parsed={artifact_parsed}\n"
            f"{detail_block}"
            "next:\n"
            f"- /orch bgx-status {alias}",
            context=f"orch-bgx-{artifact_kind}",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd in {"orch-bgx-emit-ack", "orch-bgx-emit-result"}:
        try:
            key, entry, _p_args = get_context(orch_target)
        except Exception as exc:
            text = str(exc)
            if "project lock active:" in text.lower():
                send(
                    "external background harness blocked by project lock\n"
                    f"- {text}\n"
                    "next:\n"
                    "- /focus off\n"
                    "- /map",
                    context=f"{cmd} blocked",
                    with_menu=True,
                )
                return True
            raise
        alias = _project_alias(entry, key)
        run_lock_mode = project_run_lock_mode(entry)
        if run_lock_mode != "test_only":
            send(
                "external background harness blocked\n"
                f"- runtime: {key}\n"
                f"- run_lock: {run_lock_mode or 'open'}\n"
                "- reason: harness commands are limited to test_only runtimes\n"
                "next:\n"
                f"- /orch status {alias}",
                context=f"{cmd} blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if not team_dir_raw:
            send(
                "external background harness blocked\n"
                f"- runtime: {key}\n"
                "- reason: team_dir missing\n"
                "next:\n"
                f"- /orch status {alias}",
                context=f"{cmd} blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        snapshot = _external_background_artifact_snapshot(entry)
        ticket_id = str(snapshot.get("ticket_id", "")).strip()
        runner_target = str(snapshot.get("runner_target", "")).strip().lower()
        if not ticket_id or runner_target not in {"github_runner", "remote_worker"}:
            send(
                "external background harness blocked\n"
                f"- runtime: {key}\n"
                "- reason: no external ticket is available\n"
                "next:\n"
                f"- /orch bgx-status {alias}",
                context=f"{cmd} blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        team_dir = Path(team_dir_raw).expanduser().resolve()
        queue_path = background_runs.background_runs_state_path(team_dir)
        if cmd == "orch-bgx-emit-ack":
            emit_external_background_ack(
                queue_path=queue_path,
                ticket_id=ticket_id,
                runner_target=runner_target,
                now_iso=now_iso,
            )
            poll_result = poll_background_tickets_via_adapters(
                queue_path=queue_path,
                now_iso=now_iso,
                ack_source_command=f"/orch bgx-emit-ack {alias}",
            )
            if bool(poll_result.get("changed")) and (not args.dry_run):
                if _sync_background_run_snapshots_from_queue(entry, queue_path):
                    entry["updated_at"] = now_iso()
                    save_manager_state(args.manager_state_file, manager_state)
            updated_snapshot = _external_background_artifact_snapshot(entry)
            next_step = external_background_priority_action_snapshot(
                alias=alias,
                task_label=str(updated_snapshot.get("label", "")).strip(),
                background_run_runner_target=str(updated_snapshot.get("runner_target", "")).strip(),
                background_run_external_phase=str(updated_snapshot.get("phase", "")).strip(),
                background_run_external_note=str(updated_snapshot.get("note", "")).strip(),
                run_lock_mode=run_lock_mode,
            ).get("action", "") or f"/orch bgx-status {alias}"
            send(
                "external background pickup ack emitted\n"
                f"- runtime: {key}\n"
                f"- runner: {runner_target}\n"
                f"- ticket: {ticket_id}\n"
                f"- ack: {str(updated_snapshot.get('ack_path', '-')).strip() or '-'}\n"
                f"- phase: {str(updated_snapshot.get('phase', '-')).strip() or '-'}\n"
                "next:\n"
                f"- {next_step}",
                context="orch-bgx-emit-ack",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True

        terminal_status = str(rest or "").strip().lower() or "completed"
        if terminal_status not in {"completed", "failed"}:
            send(
                "external background result harness blocked\n"
                f"- runtime: {key}\n"
                f"- reason: invalid result status `{terminal_status}`\n"
                "next:\n"
                f"- /orch bgx-result {alias}",
                context="orch-bgx-emit-result blocked",
                with_menu=True,
                reply_markup=_orch_status_reply_markup(manager_state, key, entry),
            )
            return True
        emit_external_background_result(
            queue_path=queue_path,
            ticket_id=ticket_id,
            runner_target=runner_target,
            now_iso=now_iso,
            status=terminal_status,
        )
        poll_result = poll_background_tickets_via_adapters(
            queue_path=queue_path,
            now_iso=now_iso,
            result_source_command=f"/orch bgx-emit-result {alias} {terminal_status}",
        )
        if bool(poll_result.get("changed")) and (not args.dry_run):
            if _sync_background_run_snapshots_from_queue(entry, queue_path):
                entry["updated_at"] = now_iso()
                save_manager_state(args.manager_state_file, manager_state)
        updated_snapshot = _external_background_artifact_snapshot(entry)
        next_step = external_background_priority_action_snapshot(
            alias=alias,
            task_label=str(updated_snapshot.get("label", "")).strip(),
            background_run_runner_target=str(updated_snapshot.get("runner_target", "")).strip(),
            background_run_external_phase=str(updated_snapshot.get("phase", "")).strip(),
            background_run_external_note=str(updated_snapshot.get("note", "")).strip(),
            run_lock_mode=run_lock_mode,
        ).get("action", "") or f"/offdesk review {alias}"
        send(
            "external background result emitted\n"
            f"- runtime: {key}\n"
            f"- runner: {runner_target}\n"
            f"- ticket: {ticket_id}\n"
            f"- result: {str(updated_snapshot.get('result_path', '-')).strip() or '-'}\n"
            f"- phase: {str(updated_snapshot.get('phase', '-')).strip() or '-'}\n"
            "next:\n"
            f"- {next_step}",
            context="orch-bgx-emit-result",
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

        artifact_path = str(snapshot.get("artifact_path", "")).strip() or "-"
        artifact_exists = str(snapshot.get("artifact_exists", "")).strip() or "no"
        artifact_parsed = str(snapshot.get("artifact_parsed", "")).strip() or "no"
        detail = snapshot.get("artifact_detail") if isinstance(snapshot.get("artifact_detail"), dict) else {}
        next_step = f"/orch bgx-status {alias}"
        remediation = f"inspect external {artifact_kind} state before taking the next rerun or followup action"
        outcome_reason_code = f"{artifact_kind}_{'present' if artifact_exists == 'yes' else 'missing'}"
        outcome_detail = artifact_path if artifact_exists == "yes" else f"{artifact_kind} artifact missing"
        append_action_audit_row(
            team_dir,
            headline=f"{headline_label} | accepted",
            status="accepted",
            outcome_kind="background_external",
            outcome_status="accepted",
            outcome_reason_code=outcome_reason_code,
            outcome_detail=outcome_detail,
            next_step=next_step,
            remediation=remediation,
            source_command=f"/orch bgx-{artifact_kind} {alias}",
            link_label="runtime detail",
            link_href=_runtime_action_link(alias),
            at=now_iso(),
        )
        body_lines = [
            f"external background {artifact_kind}",
            f"- runtime: {key}",
            f"- task: {str(snapshot.get('label', '')).strip() or '-'}",
            f"- runner: {str(snapshot.get('runner_target', '')).strip() or '-'}",
            f"- phase: {str(snapshot.get('phase', '')).strip() or '-'}",
            f"- artifact: {artifact_path} | exists={artifact_exists} | parsed={artifact_parsed}",
        ]
        if artifact_kind == "handoff":
            body_lines.extend(
                [
                    f"- emitted_at: {str(detail.get('emitted_at', '')).strip() or '-'}",
                    f"- launch_mode: {str(detail.get('launch_mode', '')).strip() or '-'}",
                    f"- source_surface: {str(detail.get('source_surface', '')).strip() or '-'}",
                    f"- execution_brief: {str(detail.get('execution_brief_status', '')).strip() or '-'}",
                    f"- launch_spec: {str(detail.get('launch_spec_summary', '')).strip() or '-'}",
                ]
            )
        elif artifact_kind == "ack":
            body_lines.extend(
                [
                    f"- ack_status: {str(detail.get('status', '')).strip() or '-'}",
                    f"- worker_id: {str(detail.get('worker_id', '')).strip() or '-'}",
                    f"- summary: {str(detail.get('summary', '')).strip() or '-'}",
                ]
            )
        else:
            body_lines.extend(
                [
                    f"- result_status: {str(detail.get('status', '')).strip() or '-'}",
                    f"- reason: {str(detail.get('reason', '')).strip() or '-'}",
                    f"- summary: {str(detail.get('summary', '')).strip() or '-'}",
                    f"- evidence_bundle: {str(detail.get('evidence_bundle', '')).strip() or '-'}",
                    f"- evidence_artifacts: {', '.join(str(x).strip() for x in (detail.get('evidence_artifacts') or []) if str(x).strip()) or '-'}",
                ]
            )
        body_lines.extend(["next:", f"- {next_step}"])
        send(
            "\n".join(body_lines),
            context=context_label,
            with_menu=True,
            reply_markup=_orch_status_reply_markup(manager_state, key, entry),
        )
        return True

    if cmd == "request":
        if not rest:
            send("usage: /request <request_or_alias> | aoe request <request_or_alias>", context="request usage")
            return True
        key, entry, p_args = get_context(None)
        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, rest)
        req_id = resolve_task_request_id(entry, req_ref)
        data = run_request_query(p_args, req_id)
        entry["last_request_id"] = str(data.get("request_id", req_id)).strip() or req_id
        entry["updated_at"] = now_iso()
        task = sync_task_lifecycle(
            entry=entry,
            request_data=data,
            prompt="",
            mode="dispatch",
            selected_roles=None,
            verifier_roles=None,
            require_verifier=bool(args.require_verifier),
            verifier_candidates=resolve_verifier_candidates(args.verifier_roles),
        )
        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        send(f"runtime: {key}\n" + summarize_request_state(data, task=task), context="request")
        return True

    if cmd == "orch-check":
        key, entry, p_args = get_context(orch_target)
        req_ref = (
            orch_check_request_id
            or get_chat_selected_task_ref(manager_state, chat_id, key)
            or str(entry.get("last_request_id", "")).strip()
            or ""
        ).strip()
        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, req_ref)
        req_id = resolve_task_request_id(entry, req_ref)
        if not req_id:
            send(f"no request id. usage: aoe orch check [--orch <name>] [<request_or_alias>]\norch={key}", context="orch-check usage")
            return True
        data = run_request_query(p_args, req_id)
        entry["last_request_id"] = str(data.get("request_id", req_id)).strip() or req_id
        entry["updated_at"] = now_iso()
        task = sync_task_lifecycle(
            entry=entry,
            request_data=data,
            prompt="",
            mode="dispatch",
            selected_roles=None,
            verifier_roles=None,
            require_verifier=bool(args.require_verifier),
            verifier_candidates=resolve_verifier_candidates(args.verifier_roles),
        )
        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        send(summarize_three_stage_request(key, data, task=task), context="orch-check")
        return True

    if cmd == "orch-task":
        key, entry, p_args = get_context(orch_target)
        req_ref = (
            orch_task_request_id
            or get_chat_selected_task_ref(manager_state, chat_id, key)
            or str(entry.get("last_request_id", "")).strip()
            or ""
        ).strip()
        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, req_ref)
        req_id = resolve_task_request_id(entry, req_ref)
        if not req_id:
            send(f"no request id. usage: aoe orch task [--orch <name>] [<request_or_alias>]\norch={key}", context="orch-task usage")
            return True

        task = get_task_record(entry, req_id)
        if task is None:
            try:
                data = run_request_query(p_args, req_id)
                task = sync_task_lifecycle(
                    entry=entry,
                    request_data=data,
                    prompt="",
                    mode="dispatch",
                    selected_roles=None,
                    verifier_roles=None,
                    require_verifier=bool(args.require_verifier),
                    verifier_candidates=resolve_verifier_candidates(args.verifier_roles),
                )
                entry["last_request_id"] = str(data.get("request_id", req_id)).strip() or req_id
                entry["updated_at"] = now_iso()
            except Exception:
                task = None

        if task is None:
            send(f"no lifecycle record: request_or_alias={req_ref or req_id} (orch={key})", context="orch-task missing")
            return True

        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        send(
            summarize_task_lifecycle(key, task),
            context="orch-task",
            reply_markup=_orch_task_reply_markup(key, entry, req_id, task),
        )
        return True

    def _load_followup_task(
        req_ref_raw: Optional[str],
        *,
        usage_text: str,
        usage_context: str,
        missing_context: str,
        missing_task_context: str,
    ) -> Optional[tuple[str, Dict[str, Any], str, str, Dict[str, Any], Dict[str, Any]]]:
        key, entry, p_args = get_context(orch_target)
        req_ref = (
            req_ref_raw
            or get_chat_selected_task_ref(manager_state, chat_id, key)
            or str(entry.get("last_request_id", "")).strip()
            or ""
        ).strip()
        if not req_ref:
            send(usage_text.format(orch=key), context=usage_context)
            return None

        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, req_ref)
        req_id = resolve_task_request_id(entry, req_ref)
        if not req_id:
            send(f"task not found: {req_ref} (orch={key})", context=missing_context)
            return None

        task = get_task_record(entry, req_id)
        if task is None:
            try:
                data = run_request_query(p_args, req_id)
                task = sync_task_lifecycle(
                    entry=entry,
                    request_data=data,
                    prompt="",
                    mode="dispatch",
                    selected_roles=None,
                    verifier_roles=None,
                    require_verifier=bool(args.require_verifier),
                    verifier_candidates=resolve_verifier_candidates(args.verifier_roles),
                )
                entry["last_request_id"] = str(data.get("request_id", req_id)).strip() or req_id
                entry["updated_at"] = now_iso()
            except Exception:
                task = None

        if task is None:
            send(f"no lifecycle record: request_or_alias={req_ref or req_id} (orch={key})", context=missing_task_context)
            return None
        exec_critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
        return key, entry, req_ref, req_id, task, exec_critic

    if cmd == "orch-followup":
        loaded = _load_followup_task(
            orch_followup_request_id,
            usage_text="usage: /followup <request_or_alias> [lane <L#|R#,...>] | aoe followup <request_or_alias> [lane <L#|R#,...>]\norch={orch}",
            usage_context="orch-followup usage",
            missing_context="orch-followup missing",
            missing_task_context="orch-followup missing task",
        )
        if loaded is None:
            return True
        key, entry, _req_ref, req_id, task, exec_critic = loaded

        allowed_execution_lane_ids = [
            str(item).strip()[:32]
            for item in (exec_critic.get("manual_followup_execution_lane_ids") or [])
            if str(item).strip()
        ]
        allowed_review_lane_ids = [
            str(item).strip()[:32]
            for item in (exec_critic.get("manual_followup_review_lane_ids") or [])
            if str(item).strip()
        ]
        if not allowed_execution_lane_ids and not allowed_review_lane_ids:
            send(
                f"manual follow-up lanes are not available for this task.\nrequest_id={req_id}\nallowed: none",
                context="orch-followup unavailable",
            )
            return True

        requested_execution_lane_ids, requested_review_lane_ids = _normalize_lane_ids(orch_followup_lane_ids)
        if requested_execution_lane_ids or requested_review_lane_ids:
            allowed_execution_lane_set = set(allowed_execution_lane_ids)
            allowed_review_lane_set = set(allowed_review_lane_ids)
            selected_execution_lane_ids = [
                lane for lane in requested_execution_lane_ids if lane in allowed_execution_lane_set
            ]
            selected_review_lane_ids = [
                lane for lane in requested_review_lane_ids if lane in allowed_review_lane_set
            ]
            if not selected_execution_lane_ids and not selected_review_lane_ids:
                send(
                    (
                        "requested follow-up lanes are not allowed for this task.\nrequest_id={req_id}\n"
                        "allowed execution: {execs}\nallowed review: {reviews}"
                    ).format(
                        req_id=req_id,
                        execs=", ".join(allowed_execution_lane_ids) or "-",
                        reviews=", ".join(allowed_review_lane_ids) or "-",
                    ),
                    context="orch-followup lane invalid",
                )
                return True
        else:
            selected_execution_lane_ids = list(allowed_execution_lane_ids)
            selected_review_lane_ids = list(allowed_review_lane_ids)

        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

        label = task_display_label(task or {}, fallback_request_id=req_id)
        reason = str(exec_critic.get("reason", "")).strip() or str(exec_critic.get("note", "")).strip() or "-"
        lines = [
            f"runtime: {key}",
            "manual follow-up",
            f"task: {label}",
            f"request_id: {req_id}",
            f"execution lanes: {', '.join(selected_execution_lane_ids) or '-'}",
            f"review lanes: {', '.join(selected_review_lane_ids) or '-'}",
            f"reason: {reason}",
            "",
            "next:",
            f"- /task {label}",
            f"- /todo {_project_alias(entry, key)} followup",
            f"- /orch monitor {_project_alias(entry, key)}",
        ]
        if exec_critic.get("rerun_execution_lane_ids") or exec_critic.get("rerun_review_lane_ids"):
            lines.append(f"- /retry {label}")
            if str(exec_critic.get("action", "")).strip().lower() == "replan":
                lines.append(f"- /replan {label}")

        send(
            "\n".join(lines),
            context="orch-followup",
            reply_markup=_orch_task_reply_markup(key, entry, req_id, task),
        )
        return True

    if cmd == "orch-followup-exec":
        loaded = _load_followup_task(
            orch_followup_execute_request_id,
            usage_text="usage: /followup-exec <request_or_alias> [lane <L#|R#,...>] | aoe followup-exec <request_or_alias> [lane <L#|R#,...>]\norch={orch}",
            usage_context="orch-followup-exec usage",
            missing_context="orch-followup-exec missing",
            missing_task_context="orch-followup-exec missing task",
        )
        if loaded is None:
            return True
        key, entry, _req_ref, req_id, task, exec_critic = loaded

        allowed_execution_lane_ids = [
            str(item).strip()[:32]
            for item in (exec_critic.get("manual_followup_execution_lane_ids") or [])
            if str(item).strip()
        ]
        allowed_review_lane_ids = [
            str(item).strip()[:32]
            for item in (exec_critic.get("manual_followup_review_lane_ids") or [])
            if str(item).strip()
        ]
        if not allowed_execution_lane_ids and not allowed_review_lane_ids:
            send(
                f"manual follow-up execute is not available for this task.\nrequest_id={req_id}\nallowed: none",
                context="orch-followup-exec unavailable",
            )
            return True

        requested_execution_lane_ids, requested_review_lane_ids = _normalize_lane_ids(orch_followup_execute_lane_ids)
        if requested_execution_lane_ids or requested_review_lane_ids:
            allowed_execution_lane_set = set(allowed_execution_lane_ids)
            allowed_review_lane_set = set(allowed_review_lane_ids)
            selected_execution_lane_ids = [
                lane for lane in requested_execution_lane_ids if lane in allowed_execution_lane_set
            ]
            selected_review_lane_ids = [
                lane for lane in requested_review_lane_ids if lane in allowed_review_lane_set
            ]
            if not selected_execution_lane_ids and not selected_review_lane_ids:
                send(
                    (
                        "requested follow-up execute lanes are not allowed for this task.\nrequest_id={req_id}\n"
                        "allowed execution: {execs}\nallowed review: {reviews}"
                    ).format(
                        req_id=req_id,
                        execs=", ".join(allowed_execution_lane_ids) or "-",
                        reviews=", ".join(allowed_review_lane_ids) or "-",
                    ),
                    context="orch-followup-exec lane invalid",
                )
                return True
        else:
            selected_execution_lane_ids = list(allowed_execution_lane_ids)
            selected_review_lane_ids = list(allowed_review_lane_ids)

        label = task_display_label(task or {}, fallback_request_id=req_id)
        reason = (
            str(task.get("followup_brief_reason", "")).strip()
            or str(exec_critic.get("reason", "")).strip()
            or str(exec_critic.get("note", "")).strip()
            or "-"
        )
        followup_brief_status = str(task.get("followup_brief_status", "")).strip().lower() or "preview_only"
        alias = _project_alias(entry, key)
        team_dir_raw = str(entry.get("team_dir", "") or "").strip()
        if followup_brief_status not in {"executable", "partially_executable"}:
            if team_dir_raw:
                team_dir = Path(team_dir_raw).expanduser().resolve()
                append_action_audit_row(
                    team_dir,
                    headline="Follow-up Execute | blocked",
                    status="blocked",
                    outcome_kind="followup_execute",
                    outcome_status="blocked",
                    outcome_reason_code="followup_execute_brief_required",
                    outcome_detail=f"status={followup_brief_status} | execution={','.join(selected_execution_lane_ids) or '-'} | review={','.join(selected_review_lane_ids) or '-'}",
                    next_step=f"/followup {label}",
                    remediation="derive an explicit executable FollowupBrief before off-desk execution; current /followup remains preview-only",
                    source_command=f"/followup-exec {label}",
                    link_label="runtime detail",
                    link_href=_runtime_action_link(alias),
                    at=now_iso(),
                )
            send(
                "\n".join(
                    [
                        f"runtime: {key}",
                        "follow-up execute blocked",
                        f"task: {label}",
                        f"request_id: {req_id}",
                        f"followup_brief: {followup_brief_status}",
                        f"execution lanes: {', '.join(selected_execution_lane_ids) or '-'}",
                        f"review lanes: {', '.join(selected_review_lane_ids) or '-'}",
                        f"reason: {reason}",
                        "",
                        "next:",
                        f"- /followup {label}",
                        f"- /task {label}",
                        f"- /offdesk review {alias}",
                    ]
                ),
                context="orch-followup-exec blocked",
                reply_markup=_orch_task_reply_markup(key, entry, req_id, task),
            )
            return True
        return False

    if cmd == "orch-pick":
        key, entry, _p_args = get_context(orch_target)
        req_ref = str(orch_pick_request_id or "").strip()
        if not req_ref:
            limit = 9
            recent_refs = latest_task_request_refs(entry, limit=limit)
            set_chat_recent_task_refs(manager_state, chat_id, key, recent_refs)
            current_sel = get_chat_selected_task_ref(manager_state, chat_id, key)
            if (not current_sel) and recent_refs:
                set_chat_selected_task_ref(manager_state, chat_id, key, recent_refs[0])
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)

            if not recent_refs:
                send(
                    f"runtime: {key}\n"
                    "최근 작업이 없습니다.\n\n"
                    "start: /dispatch <요청>",
                    context="orch-pick empty",
                    with_menu=True,
                )
                return True

            lines = [
                f"runtime: {key}",
                "pick: 최근 작업 선택",
                "",
                "recent:",
            ]
            for idx, rid in enumerate(recent_refs, start=1):
                task = get_task_record(entry, rid) or {}
                label = task_display_label(task, fallback_request_id=rid)
                lines.append(f"- {idx}. {label}")
            lines.append("")
            lines.append("tap: /pick 1..9  (또는 /pick <T-xxx|alias>)")

            keyboard = []
            row = []
            for idx in range(1, len(recent_refs) + 1):
                row.append({"text": f"/pick {idx}"})
                if len(row) >= 3:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)
            keyboard.append([{"text": "/task"}, {"text": "/check"}, {"text": "/monitor 9"}])
            keyboard.append([{"text": "/status"}, {"text": "/map"}, {"text": "/help"}])
            reply_markup = {
                "keyboard": keyboard,
                "resize_keyboard": True,
                "one_time_keyboard": True,
                "input_field_placeholder": "예: /pick 3 또는 /pick T-005",
            }

            send(
                "\n".join(lines),
                context="orch-pick menu",
                with_menu=False,
                reply_markup=reply_markup,
            )
            return True
        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, req_ref)
        req_id = resolve_task_request_id(entry, req_ref)
        if not req_id:
            send(f"task not found: {orch_pick_request_id} (orch={key})", context="orch-pick missing", with_menu=True)
            return True

        task = get_task_record(entry, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

        label = task_display_label(task or {}, fallback_request_id=req_id)
        send(
            "selected task updated\n"
            f"- runtime: {key}\n"
            f"- task: {label}\n"
            f"- request_id: {req_id}\n"
            "next: /check, /task, /retry, /replan, /cancel",
            context="orch-pick",
            with_menu=True,
        )
        return True

    if cmd == "orch-cancel":
        key, entry, p_args = get_context(orch_target)
        req_ref = (
            orch_cancel_request_id
            or get_chat_selected_task_ref(manager_state, chat_id, key)
            or str(entry.get("last_request_id", "")).strip()
            or ""
        ).strip()
        req_ref = resolve_chat_task_ref(manager_state, chat_id, key, req_ref)
        req_id = resolve_task_request_id(entry, req_ref)
        if not req_id:
            send(
                f"no request id. usage: /cancel <request_or_alias> | aoe orch cancel [--orch <name>] [<request_or_alias>]\norch={key}",
                context="orch-cancel usage",
            )
            return True

        state_before = run_request_query(p_args, req_id)
        note = f"canceled by telegram:{chat_id}"
        cancel_result = cancel_request_assignments(p_args, state_before, note=note)
        try:
            state_after = run_request_query(p_args, req_id)
        except Exception:
            state_after = state_before

        entry["last_request_id"] = str(state_after.get("request_id", req_id)).strip() or req_id
        entry["updated_at"] = now_iso()
        task = sync_task_lifecycle(
            entry=entry,
            request_data=state_after,
            prompt="",
            mode="dispatch",
            selected_roles=None,
            verifier_roles=None,
            require_verifier=bool(args.require_verifier),
            verifier_candidates=resolve_verifier_candidates(args.verifier_roles),
        )
        if task is not None:
            lifecycle_set_stage(task, "execution", "failed", note=note)
            lifecycle_set_stage(task, "verification", "failed", note=note)
            lifecycle_set_stage(task, "integration", "failed", note=note)
            lifecycle_set_stage(task, "close", "failed", note=note)
            task["status"] = "failed"
            task["canceled"] = True
            task["canceled_at"] = now_iso()
            task["canceled_by"] = f"telegram:{chat_id}"
            task["updated_at"] = now_iso()

        touch_chat_recent_task_ref(manager_state, chat_id, key, req_id)
        set_chat_selected_task_ref(manager_state, chat_id, key, req_id)
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

        send(
            summarize_cancel_result(key, req_id, task=task, result=cancel_result),
            context="orch-cancel",
            with_menu=True,
        )
        log_event(
            event="dispatch_canceled",
            project=key,
            request_id=req_id,
            task=task,
            stage="close",
            status="failed",
        )
        return True

    return False
