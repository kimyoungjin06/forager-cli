#!/usr/bin/env python3
"""Shared dashboard state assembly helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_offdesk_flow as offdesk_flow
import aoe_tg_operator_action_contract as operator_action_contract
import aoe_tg_orch_contract as orch_contract
import aoe_tg_ops_policy as ops_policy
import aoe_tg_runtime_core as runtime_core
import aoe_tg_task_view as task_view
import aoe_tg_worker_task_contract as worker_task_contract
from control_dashboard_state_models import ActionButtonDTO


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


def _background_scheduler_note(summary: str) -> str:
    safe = str(summary or "").strip()
    if not safe or safe == "-":
        return "no queued scheduler head"
    parts = [str(part).strip() for part in safe.split(" | ") if str(part).strip()]
    if not parts:
        return "no queued scheduler head"
    starved = next((part for part in parts if "starved=yes" in part), "")
    if starved:
        return f"starvation guard candidate: {starved}"
    return f"scheduler head: {parts[0]}"


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


def _task_command_contract(
    *,
    project_alias: str,
    label: str,
    request_id: str,
    tf_phase: str = "",
    rerun_summary: str = "",
    followup_summary: str = "",
    followup_brief_status: str = "",
    rate_limit_summary: str = "",
    execution_brief_status: str = "",
) -> Dict[str, Any]:
    return operator_action_contract.partition_operator_commands(
        operator_action_contract.task_operator_commands(
            project_alias=project_alias,
            label=label,
            request_id=request_id,
            tf_phase=tf_phase,
            rerun_summary=rerun_summary,
            followup_summary=followup_summary,
            followup_brief_status=followup_brief_status,
            rate_limit_summary=rate_limit_summary,
            execution_brief_status=execution_brief_status,
        )
    )


def _runtime_command_contract(
    *,
    project_alias: str,
    priority_action: str = "",
    has_active_task: bool = False,
    has_rate_limit: bool = False,
    background_queue_stale_count: int = 0,
) -> Dict[str, Any]:
    return operator_action_contract.partition_operator_commands(
        operator_action_contract.runtime_operator_commands(
            project_alias=project_alias,
            priority_action=priority_action,
            has_active_task=has_active_task,
            has_rate_limit=has_rate_limit,
            background_queue_stale_count=background_queue_stale_count,
        )
    )


def _action_button_label(spec: Dict[str, Any]) -> str:
    path = str(spec.get("path", "")).strip()
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    if path == "/control/actions/task/retry":
        lane_ids = [str(item).strip() for item in (payload.get("lane_ids") or []) if str(item).strip()]
        return "Retry" if not lane_ids else f"Retry ({','.join(lane_ids)})"
    if path == "/control/actions/task/followup":
        lane_ids = [str(item).strip() for item in (payload.get("lane_ids") or []) if str(item).strip()]
        return "Follow-up Preview" if not lane_ids else f"Follow-up Preview ({','.join(lane_ids)})"
    if path == "/control/actions/task/followup-execute":
        lane_ids = [str(item).strip() for item in (payload.get("lane_ids") or []) if str(item).strip()]
        return "Follow-up Execute" if not lane_ids else f"Follow-up Execute ({','.join(lane_ids)})"
    if path == "/control/actions/task/worker-update-preview":
        return "Preview Worker Update"
    if path == "/control/actions/task/worker-apply-preview":
        return "Preview Artifact Apply"
    if path == "/control/actions/task/worker-apply-propose":
        return "Propose Artifact Apply"
    if path == "/control/actions/runtime/judge":
        return "Run Offdesk Judge"
    if path == "/control/actions/runtime/todo-accept":
        proposal_ref = str(payload.get("proposal_ref", "")).strip()
        return f"Accept Proposal ({proposal_ref})" if proposal_ref else "Accept Proposal"
    if path == "/control/actions/runtime/todo-reject":
        proposal_ref = str(payload.get("proposal_ref", "")).strip()
        return f"Reject Proposal ({proposal_ref})" if proposal_ref else "Reject Proposal"
    if path == "/control/actions/runtime/sync-preview":
        window = str(payload.get("window", "")).strip()
        return f"Sync Preview ({window})" if window else "Sync Preview"
    if path == "/control/actions/runtime/background-queue-clean":
        return "Background Queue Cleanup"
    if path == "/control/actions/control/auto-recover":
        return "Auto Recover Force" if bool(payload.get("force")) else "Auto Recover"
    return str(spec.get("command", "")).strip() or "Action"


def _build_action_buttons(commands: Iterable[str]) -> List[ActionButtonDTO]:
    buttons: List[ActionButtonDTO] = []
    seen: set[tuple[str, str]] = set()
    for raw_command in commands:
        command = str(raw_command or "").strip()
        if not command:
            continue
        spec = operator_action_contract.http_action_spec(command)
        if not isinstance(spec, dict):
            continue
        payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        key = (str(spec.get("path", "")).strip(), payload_json)
        if key in seen:
            continue
        seen.add(key)
        buttons.append(
            ActionButtonDTO(
                label=_action_button_label(spec),
                command=str(spec.get("command", "")).strip() or command,
                method=str(spec.get("method", "POST")).strip() or "POST",
                path=str(spec.get("path", "")).strip() or "/control",
                mode=str(spec.get("mode", "safe")).strip() or "safe",
                note=str(spec.get("note", "")).strip(),
                payload_json=payload_json,
            )
        )
    return buttons


def _append_unique_action_button(buttons: List[ActionButtonDTO], button: ActionButtonDTO | None) -> List[ActionButtonDTO]:
    if not isinstance(button, ActionButtonDTO):
        return list(buttons)
    key = (str(button.path).strip(), str(button.payload_json).strip())
    replaced = False
    out: List[ActionButtonDTO] = []
    for row in buttons:
        if not isinstance(row, ActionButtonDTO):
            continue
        row_key = (str(row.path).strip(), str(row.payload_json).strip())
        if row_key == key:
            out.append(button)
            replaced = True
            continue
        out.append(row)
    if replaced:
        return out
    return [*buttons, button]


def _replan_auto_route_action_button(
    *,
    label: str,
    request_id: str,
    policy: Dict[str, Any],
) -> ActionButtonDTO | None:
    row = policy if isinstance(policy, dict) else {}
    if str(row.get("status", "")).strip() != "ready":
        return None
    if not bool(row.get("can_auto_apply", False)):
        return None
    if str(row.get("suggested_action", "")).strip() != "retry":
        return None
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if task_ref == "-":
        return None
    suggested_next_step = str(row.get("suggested_next_step", "")).strip()
    target_ref = ""
    if suggested_next_step.startswith("/"):
        parts = suggested_next_step.split()
        if len(parts) >= 2:
            target_ref = str(parts[1]).strip()
    if target_ref and target_ref not in {task_ref, str(request_id).strip()}:
        return None
    confidence = str(row.get("confidence", "")).strip() or "-"
    next_hint = suggested_next_step or f"/retry {task_ref}"
    return ActionButtonDTO(
        label="Apply Judge Auto-Route",
        command=f"/replan {task_ref} | auto_route_apply=true",
        method="POST",
        path="/control/actions/task/replan",
        mode="phase2",
        note=f"judge-backed retry promotion | confidence={confidence} | next={next_hint}",
        payload_json=json.dumps(
            {"task_ref": task_ref, "auto_route_apply": True},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def _replan_manual_route_action_button(
    *,
    project_alias: str,
    label: str,
    request_id: str,
    policy: Dict[str, Any],
) -> ActionButtonDTO | None:
    row = policy if isinstance(policy, dict) else {}
    if str(row.get("status", "")).strip() != "manual_ready":
        return None
    suggested_next_step = str(row.get("suggested_next_step", "")).strip()
    confidence = str(row.get("confidence", "")).strip() or "-"
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    spec = operator_action_contract.http_action_spec(suggested_next_step)
    if not isinstance(spec, dict):
        return None
    path = str(spec.get("path", "")).strip()
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    if path in {"/control/actions/task/followup", "/control/actions/task/followup-execute"}:
        payload_task_ref = str(payload.get("task_ref", "")).strip()
        if payload_task_ref not in {"", "-", task_ref, str(request_id).strip()}:
            return None
    elif path == "/control/actions/runtime/judge":
        payload_project_ref = str(payload.get("project_ref", "")).strip()
        if payload_project_ref not in {"", project_alias}:
            return None
    else:
        return None
    note_prefix = {
        "/control/actions/task/followup": "judge-backed followup handoff",
        "/control/actions/task/followup-execute": "judge-backed followup execute",
        "/control/actions/runtime/judge": "judge-backed manual review",
    }.get(path, "judge-backed manual step")
    return ActionButtonDTO(
        label="Apply Judge Manual Step",
        command=suggested_next_step,
        method=str(spec.get("method", "POST")).strip() or "POST",
        path=path,
        mode=str(spec.get("mode", "safe")).strip() or "safe",
        note=f"{note_prefix} | confidence={confidence} | next={suggested_next_step}",
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    return None


def _worker_update_proposal_accept_button(
    *,
    project_alias: str,
    proposal_ids: Iterable[str],
) -> ActionButtonDTO | None:
    alias = str(project_alias or "").strip()
    if not alias:
        return None
    first = next((str(item).strip() for item in proposal_ids if str(item).strip()), "")
    if not first:
        return None
    return ActionButtonDTO(
        label="Accept Worker Proposal",
        command=f"/todo {alias} accept {first}",
        method="POST",
        path="/control/actions/runtime/todo-accept",
        mode="phase2",
        note=f"promote worker update proposal into runtime todo queue | proposal={first}",
        payload_json=json.dumps({"project_ref": alias, "proposal_ref": first}, ensure_ascii=False, separators=(",", ":")),
    )


def _worker_apply_proposal_accept_button(
    *,
    project_alias: str,
    proposal_ids: Iterable[str],
    proposal_summary: Any = None,
) -> ActionButtonDTO | None:
    summary = str(proposal_summary or "").strip()
    if "apply_proposals=" not in summary:
        return None
    alias = str(project_alias or "").strip()
    if not alias:
        return None
    first = next((str(item).strip() for item in proposal_ids if str(item).strip()), "")
    if not first:
        return None
    return ActionButtonDTO(
        label="Accept Artifact Apply",
        command=f"/todo {alias} accept {first}",
        method="POST",
        path="/control/actions/runtime/todo-accept",
        mode="phase2",
        note=f"promote artifact-apply proposal into runtime todo queue | proposal={first}",
        payload_json=json.dumps({"project_ref": alias, "proposal_ref": first}, ensure_ascii=False, separators=(",", ":")),
    )


def _worker_update_preview_button(
    *,
    label: str,
    request_id: str,
    update_stub: Any,
    proposal_ids: Any = None,
) -> ActionButtonDTO | None:
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if not task_ref or task_ref == "-":
        return None
    stub = worker_task_contract.sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return None
    status = str(stub.get("status", "")).strip().lower()
    if status in {"", "-", "none"}:
        return None
    operator_summary = worker_task_contract.summarize_worker_update_operator_summary(stub, proposal_ids)
    note = (
        f"inspect bounded worker update before accepting any proposal | {operator_summary}"
        if operator_summary not in {"", "-"}
        else "inspect bounded worker update before accepting any proposal"
    )
    return ActionButtonDTO(
        label="Preview Worker Update",
        command=f"/task {task_ref} | worker-update-preview",
        method="POST",
        path="/control/actions/task/worker-update-preview",
        mode="safe",
        note=note,
        payload_json=json.dumps({"task_ref": task_ref}, ensure_ascii=False, separators=(",", ":")),
    )


def _worker_apply_preview_button(
    *,
    label: str,
    request_id: str,
    update_stub: Any,
    proposal_ids: Any = None,
) -> ActionButtonDTO | None:
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if not task_ref or task_ref == "-":
        return None
    stub = worker_task_contract.sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return None
    status = str(stub.get("status", "")).strip().lower()
    if status in {"", "-", "none"}:
        return None
    operator_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(stub, proposal_ids)
    note = (
        f"inspect artifact-apply proposal payloads before proposing or accepting them | {operator_summary}"
        if operator_summary not in {"", "-"}
        else "inspect artifact-apply proposal payloads before proposing or accepting them"
    )
    return ActionButtonDTO(
        label="Preview Artifact Apply",
        command=f"/task {task_ref} | worker-apply-preview",
        method="POST",
        path="/control/actions/task/worker-apply-preview",
        mode="safe",
        note=note,
        payload_json=json.dumps({"task_ref": task_ref}, ensure_ascii=False, separators=(",", ":")),
    )


def _worker_apply_proposal_button(
    *,
    label: str,
    request_id: str,
    update_stub: Any,
    proposal_ids: Any = None,
) -> ActionButtonDTO | None:
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if not task_ref or task_ref == "-":
        return None
    stub = worker_task_contract.sanitize_worker_task_update_stub(update_stub)
    if not stub:
        return None
    status = str(stub.get("status", "")).strip().lower()
    if status in {"", "-", "none"}:
        return None
    existing_ids = [
        str(item).strip()
        for item in (proposal_ids if isinstance(proposal_ids, list) else list(proposal_ids or []))
        if str(item).strip()
    ]
    if existing_ids:
        return None
    operator_summary = worker_task_contract.summarize_worker_update_operator_summary(stub, [])
    note = (
        f"promote the bounded worker update into an artifact-apply proposal | {operator_summary}"
        if operator_summary not in {"", "-"}
        else "promote the bounded worker update into an artifact-apply proposal"
    )
    return ActionButtonDTO(
        label="Propose Artifact Apply",
        command=f"/task {task_ref} | worker-apply-propose",
        method="POST",
        path="/control/actions/task/worker-apply-propose",
        mode="phase2",
        note=note,
        payload_json=json.dumps({"task_ref": task_ref}, ensure_ascii=False, separators=(",", ":")),
    )


def _task_action_buttons(
    *,
    label: str,
    request_id: str,
    phase2_commands: Iterable[str],
    include_followup_preview: bool = True,
) -> tuple[List[ActionButtonDTO], List[ActionButtonDTO]]:
    ref = operator_action_contract.task_command_ref(label, request_id)
    safe_commands: List[str] = [f"/followup {ref}"] if include_followup_preview and ref != "-" else []
    return _build_action_buttons(safe_commands), _build_action_buttons(phase2_commands)


def _runtime_action_buttons(
    *,
    project_alias: str,
    phase2_commands: Iterable[str],
) -> tuple[List[ActionButtonDTO], List[ActionButtonDTO]]:
    alias = str(project_alias or "").strip()
    safe_commands = [f"/sync preview {alias} 24h"] if alias else []
    return _build_action_buttons(safe_commands), _build_action_buttons(phase2_commands)


def _recovery_control_action_buttons() -> List[ActionButtonDTO]:
    return _build_action_buttons(["/auto recover", "/auto recover force"])


def _runtime_path(project_alias: str) -> str:
    return f"/control/runtimes/{quote(str(project_alias or '').strip(), safe='')}"


def _detail_path(request_id: str) -> str:
    return f"/control/tasks/by-request/{quote(str(request_id or '').strip(), safe='')}"


def _recovery_summary_path(team_dir: Path) -> Path:
    return runtime_core.recovery_summary_latest_path(team_dir)


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
