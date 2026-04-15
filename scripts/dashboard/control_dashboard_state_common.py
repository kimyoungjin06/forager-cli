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


def _worker_syncback_ready(
    task: Dict[str, Any],
    *,
    module_key: str = "background_run_task_contract_module",
    records_summary_key: str = "background_run_worker_records_summary",
    records_key: str = "background_run_worker_records",
    record_rows_summary_key: str = "background_run_worker_record_rows_summary",
    record_rows_key: str = "background_run_worker_record_rows",
) -> bool:
    module_kind = str(task.get(module_key, "")).strip().lower()
    rows_payload = _worker_record_rows_payload(
        task,
        module_key=module_key,
        record_rows_summary_key=record_rows_summary_key,
        record_rows_key=record_rows_key,
    )
    if list(rows_payload.get("rows") or []):
        return worker_task_contract.worker_task_module_syncback_ready_from_rows(rows_payload)
    records_summary = str(task.get(records_summary_key, "")).strip()
    records_kind = ""
    if records_summary not in {"", "-"}:
        records_kind = records_summary.split(" | ", 1)[0].strip()
    raw_records = task.get(records_key)
    record_tokens: List[str] = []
    if isinstance(raw_records, list):
        record_tokens = [str(item).strip() for item in raw_records if str(item).strip()]
    elif isinstance(raw_records, str) and str(raw_records).strip() not in {"", "-"}:
        record_tokens = [str(item).strip() for item in raw_records.split(",") if str(item).strip()]
    if record_tokens:
        return worker_task_contract.worker_task_module_syncback_ready(
            {
                "module_kind": module_kind or ("package" if records_kind == "package_records" else "general"),
                "records_kind": records_kind or ("package_records" if module_kind == "package" else ""),
                "records": record_tokens,
            }
        )
    if records_kind == "package_records":
        return "syncback_record=ready" in records_summary
    return module_kind != "package"


def _worker_record_rows_payload(
    task: Dict[str, Any],
    *,
    module_key: str = "background_run_task_contract_module",
    record_rows_summary_key: str = "background_run_worker_record_rows_summary",
    record_rows_key: str = "background_run_worker_record_rows",
) -> Dict[str, Any]:
    prefix = "active_task_" if str(module_key).startswith("active_task_") else ""

    def _field(base_key: str) -> Any:
        return task.get(f"{prefix}{base_key}")

    module_kind = str(task.get(module_key, "")).strip().lower() or "general"
    rows_summary = str(task.get(record_rows_summary_key, "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    if module_kind == "general" and rows_kind.endswith("_record_rows"):
        inferred_module = rows_kind.split("_", 1)[0].strip().lower()
        if inferred_module in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = inferred_module
    raw_rows = task.get(record_rows_key)
    row_tokens: List[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str) and str(raw_rows).strip() not in {"", "-"}:
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    elif rows_summary not in {"", "-"}:
        row_tokens = [str(item).strip() for item in rows_summary.split(" | ")[1:] if str(item).strip()]
    if row_tokens:
        return {
            "module_kind": module_kind,
            "rows_kind": rows_kind or f"{module_kind}_record_rows",
            "rows": row_tokens,
            "summary_line": rows_summary or "-",
        }
    records_summary = str(_field("background_run_worker_records_summary") or "").strip()
    records_kind = ""
    if records_summary not in {"", "-"}:
        records_kind = records_summary.split(" | ", 1)[0].strip()
    if module_kind == "general" and records_kind.endswith("_records"):
        inferred_module = records_kind.split("_", 1)[0].strip().lower()
        if inferred_module in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = inferred_module
    if module_kind not in {"", "-", "general"}:
        gate_state = _field("background_run_worker_gate_status")
        gate_summary = _field("background_run_worker_gate_summary")
        gate_payload = (
            {
                "state": gate_state,
                "summary_line": gate_summary,
            }
            if str(gate_state or "").strip() or str(gate_summary or "").strip()
            else None
        )
        profile_state = _field("background_run_worker_profile_status")
        profile_summary = _field("background_run_worker_profile_summary")
        profile_payload = (
            {
                "state": profile_state,
                "summary_line": profile_summary,
            }
            if str(profile_state or "").strip() or str(profile_summary or "").strip()
            else None
        )
        checklist_state = _field("background_run_worker_checklist_status")
        checklist_summary = _field("background_run_worker_checklist_summary")
        checklist_payload = (
            {
                "state": checklist_state,
                "summary_line": checklist_summary,
            }
            if str(checklist_state or "").strip() or str(checklist_summary or "").strip()
            else None
        )
        item_tokens = _field("background_run_worker_items")
        item_summary = _field("background_run_worker_items_summary")
        items_payload = (
            {
                "module_kind": module_kind,
                "items": item_tokens if isinstance(item_tokens, list) else [],
                "summary_line": item_summary,
            }
            if (isinstance(item_tokens, list) and item_tokens) or str(item_summary or "").strip()
            else None
        )
        class_tokens = _field("background_run_worker_item_classes")
        class_summary = _field("background_run_worker_item_classes_summary")
        item_classes_payload = (
            {
                "module_kind": module_kind,
                "classes": class_tokens if isinstance(class_tokens, list) else [],
                "summary_line": class_summary,
            }
            if (isinstance(class_tokens, list) and class_tokens) or str(class_summary or "").strip()
            else None
        )
        record_tokens = _field("background_run_worker_records")
        record_summary = _field("background_run_worker_records_summary")
        records_payload = (
            {
                "module_kind": module_kind,
                "records": record_tokens if isinstance(record_tokens, list) else [],
                "summary_line": record_summary,
            }
            if (isinstance(record_tokens, list) and record_tokens) or str(record_summary or "").strip()
            else None
        )
        derived = worker_task_contract.derive_worker_task_module_record_rows(
            {
                "module_kind": module_kind,
                "module_policy": _field("background_run_task_contract_policy"),
                "artifact_targets": _field("background_run_worker_update_stub_targets"),
            },
            {
                "status": _field("background_run_worker_result_status"),
                "summary": _field("background_run_worker_result_summary"),
                "actions": _field("background_run_worker_result_actions"),
                "cautions": _field("background_run_worker_result_cautions"),
                "evidence_refs": _field("background_run_worker_result_evidence_refs"),
            },
            gate=gate_payload,
            profile=profile_payload,
            checklist=checklist_payload,
            items=items_payload,
            item_classes=item_classes_payload,
            records=records_payload,
        )
        if derived:
            return worker_task_contract.sanitize_worker_task_module_record_rows(derived)
    return {
        "module_kind": module_kind,
        "rows_kind": rows_kind or f"{module_kind}_record_rows",
        "rows": row_tokens,
        "summary_line": rows_summary or "-",
    }


def _worker_apply_ready(
    task: Dict[str, Any],
    *,
    module_key: str = "background_run_task_contract_module",
    record_rows_summary_key: str = "background_run_worker_record_rows_summary",
    record_rows_key: str = "background_run_worker_record_rows",
) -> bool:
    payload = _worker_record_rows_payload(
        task,
        module_key=module_key,
        record_rows_summary_key=record_rows_summary_key,
        record_rows_key=record_rows_key,
    )
    if list(payload.get("rows") or []):
        return worker_task_contract.worker_task_module_apply_ready(payload)
    return str(payload.get("module_kind", "")).strip().lower() in {"", "-", "general"}


def _worker_preflight_rows_payload(
    task: Dict[str, Any],
    *,
    module_key: str = "background_run_task_contract_module",
    preflight_rows_summary_key: str = "background_run_worker_preflight_rows_summary",
    preflight_rows_key: str = "background_run_worker_preflight_rows",
    preflight_summary_key: str = "background_run_worker_preflight_summary",
    preflight_status_key: str = "background_run_worker_preflight_status",
    record_rows_summary_key: str = "background_run_worker_record_rows_summary",
    record_rows_key: str = "background_run_worker_record_rows",
    result_status_key: str = "background_run_worker_result_status",
    result_summary_key: str = "background_run_worker_result_summary",
    result_actions_key: str = "background_run_worker_result_actions",
    result_cautions_key: str = "background_run_worker_result_cautions",
    result_evidence_refs_key: str = "background_run_worker_result_evidence_refs",
) -> Dict[str, Any]:
    module_kind = str(task.get(module_key, "")).strip().lower() or "general"
    rows_summary = str(task.get(preflight_rows_summary_key, "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    if module_kind == "general" and rows_kind.endswith("_preflight_rows"):
        inferred_module = rows_kind.split("_", 1)[0].strip().lower()
        if inferred_module in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = inferred_module
    raw_rows = task.get(preflight_rows_key)
    row_tokens: List[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str) and str(raw_rows).strip() not in {"", "-"}:
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    elif rows_summary not in {"", "-"}:
        row_tokens = [str(item).strip() for item in rows_summary.split(" | ")[1:] if str(item).strip()]
    if row_tokens:
        return worker_task_contract.sanitize_worker_task_module_preflight_rows(
            {
                "module_kind": module_kind,
                "rows_kind": rows_kind or f"{module_kind}_preflight_rows",
                "rows": row_tokens,
                "summary_line": rows_summary or "-",
            }
        )
    record_rows = _worker_record_rows_payload(
        task,
        module_key=module_key,
        record_rows_summary_key=record_rows_summary_key,
        record_rows_key=record_rows_key,
    )
    if module_kind == "general":
        record_module_kind = str(record_rows.get("module_kind", "")).strip().lower()
        if record_module_kind in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = record_module_kind
    if list(record_rows.get("rows") or []):
        derived = worker_task_contract.derive_worker_task_module_preflight_rows(
            {"module_kind": module_kind},
            {
                "status": task.get(result_status_key),
                "summary": task.get(result_summary_key),
                "actions": task.get(result_actions_key),
                "cautions": task.get(result_cautions_key),
                "evidence_refs": task.get(result_evidence_refs_key),
            },
            record_rows=record_rows,
            preflight={
                "state": task.get(preflight_status_key),
                "summary_line": task.get(preflight_summary_key),
            },
        )
        if derived:
            return worker_task_contract.sanitize_worker_task_module_preflight_rows(derived)
    return worker_task_contract.sanitize_worker_task_module_preflight_rows(
        {
            "module_kind": module_kind,
            "rows_kind": rows_kind or f"{module_kind}_preflight_rows",
            "rows": [],
            "summary_line": rows_summary or "-",
        }
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
    if path == "/control/actions/task/worker-apply-accept":
        proposal_ref = str(payload.get("proposal_ref", "")).strip()
        return f"Accept Artifact Apply ({proposal_ref})" if proposal_ref else "Accept Artifact Apply"
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
    if path == "/control/actions/runtime/syncback-preview":
        return "Preview Accepted Syncback"
    if path == "/control/actions/runtime/syncback-apply":
        return "Apply Accepted Syncback"
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
    label = {
        "/control/actions/task/followup": "Apply Judge Followup",
        "/control/actions/task/followup-execute": "Apply Judge Execute Step",
        "/control/actions/runtime/judge": "Run Judge Manual Review",
    }.get(path, "Apply Judge Manual Step")
    note_prefix = {
        "/control/actions/task/followup": "judge-backed followup handoff",
        "/control/actions/task/followup-execute": "judge-backed followup execute",
        "/control/actions/runtime/judge": "judge-backed manual review",
    }.get(path, "judge-backed manual step")
    return ActionButtonDTO(
        label=label,
        command=suggested_next_step,
        method=str(spec.get("method", "POST")).strip() or "POST",
        path=path,
        mode=str(spec.get("mode", "safe")).strip() or "safe",
        note=f"{note_prefix} | confidence={confidence} | next={suggested_next_step}",
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    return None


def _worker_blocker_lane_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:4]
    if isinstance(value, str) and str(value).strip() not in {"", "-"}:
        return [token.strip() for token in str(value).split(",") if token.strip()][:4]
    return []


def _worker_blocker_action_button(
    *,
    project_alias: str,
    label: str,
    request_id: str,
    task: Dict[str, Any],
    followup_brief_status_key: str = "followup_brief_status",
    followup_brief_execution_lane_ids_key: str = "followup_brief_execution_lane_ids",
    followup_brief_review_lane_ids_key: str = "followup_brief_review_lane_ids",
    module_key: str = "background_run_task_contract_module",
    preflight_rows_summary_key: str = "background_run_worker_preflight_rows_summary",
    preflight_rows_key: str = "background_run_worker_preflight_rows",
    preflight_summary_key: str = "background_run_worker_preflight_summary",
    preflight_status_key: str = "background_run_worker_preflight_status",
    record_rows_summary_key: str = "background_run_worker_record_rows_summary",
    record_rows_key: str = "background_run_worker_record_rows",
    result_status_key: str = "background_run_worker_result_status",
    result_summary_key: str = "background_run_worker_result_summary",
    result_actions_key: str = "background_run_worker_result_actions",
    result_cautions_key: str = "background_run_worker_result_cautions",
    result_evidence_refs_key: str = "background_run_worker_result_evidence_refs",
) -> ActionButtonDTO | None:
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if task_ref == "-":
        return None
    alias = str(project_alias or "").strip().upper()
    payload = _worker_preflight_rows_payload(
        task,
        module_key=module_key,
        preflight_rows_summary_key=preflight_rows_summary_key,
        preflight_rows_key=preflight_rows_key,
        preflight_summary_key=preflight_summary_key,
        preflight_status_key=preflight_status_key,
        record_rows_summary_key=record_rows_summary_key,
        record_rows_key=record_rows_key,
        result_status_key=result_status_key,
        result_summary_key=result_summary_key,
        result_actions_key=result_actions_key,
        result_cautions_key=result_cautions_key,
        result_evidence_refs_key=result_evidence_refs_key,
    )
    if not list(payload.get("rows") or []):
        return None
    payload["followup_brief_status"] = str(task.get(followup_brief_status_key, "")).strip() or "-"
    payload["followup_brief_execution_lane_ids"] = _worker_blocker_lane_ids(task.get(followup_brief_execution_lane_ids_key))
    payload["followup_brief_review_lane_ids"] = _worker_blocker_lane_ids(task.get(followup_brief_review_lane_ids_key))
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(payload, mode="apply")
    suggested_action = str(blocker.get("suggested_action", "")).strip().lower()
    suggested_lane_ids = _worker_blocker_lane_ids(blocker.get("suggested_lane_ids"))
    module_kind = str(payload.get("module_kind", "")).strip().lower()
    command = ""
    custom_label = ""
    if suggested_action == "followup":
        command = f"/followup {task_ref}"
        if suggested_lane_ids:
            command += f" lane {','.join(suggested_lane_ids)}"
        custom_label = "Resolve Writing Blocker" if module_kind == "writing" else "Resolve Worker Blocker"
    elif suggested_action == "followup_execute":
        command = f"/followup-exec {task_ref}"
        if suggested_lane_ids:
            command += f" lane {','.join(suggested_lane_ids)}"
        custom_label = "Resolve Writing Execute Blocker" if module_kind == "writing" else "Resolve Worker Execute Blocker"
    elif suggested_action in {
        "task_review",
        "package_verification_review",
        "package_apply_review",
        "package_syncback_review",
        "package_artifact_review",
    }:
        payload_json = json.dumps(
            {"task_ref": task_ref, "review_kind": suggested_action or "task_review"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        blocker_summary = str(blocker.get("summary_line", "")).strip()
        remediation = str(blocker.get("remediation", "")).strip()
        note = " | ".join(part for part in [blocker_summary, remediation] if part and part != "-")[:320]
        if suggested_action == "package_verification_review":
            button_label = "Review Package Verification"
            command = f"/task {task_ref} | package-verification-review"
        elif suggested_action == "package_apply_review":
            button_label = "Review Package Apply Gate"
            command = f"/task {task_ref} | package-apply-review"
        elif suggested_action == "package_syncback_review":
            button_label = "Review Package Syncback"
            command = f"/task {task_ref} | package-syncback-review"
        elif suggested_action == "package_artifact_review":
            button_label = "Review Package Integrity"
            command = f"/task {task_ref} | package-artifact-review"
        else:
            button_label = "Resolve Analysis Blocker" if module_kind == "analysis" else "Review Worker Blocker"
            command = f"/task {task_ref} | analysis-review"
        return ActionButtonDTO(
            label=button_label,
            command=command,
            method="POST",
            path="/control/actions/task/task-review",
            mode="safe",
            note=note,
            payload_json=payload_json,
        )
    elif suggested_action == "judge" and alias:
        command = f"/orch judge {alias}"
        custom_label = "Resolve Analysis Blocker" if module_kind == "analysis" else "Resolve Worker Blocker"
    else:
        return None
    spec = operator_action_contract.http_action_spec(command)
    if not isinstance(spec, dict):
        return None
    payload_json = json.dumps(
        spec.get("payload") if isinstance(spec.get("payload"), dict) else {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    blocker_summary = str(blocker.get("summary_line", "")).strip()
    remediation = str(blocker.get("remediation", "")).strip()
    note = " | ".join(part for part in [blocker_summary, remediation] if part and part != "-")[:320]
    return ActionButtonDTO(
        label=custom_label,
        command=str(spec.get("command", "")).strip() or command,
        method=str(spec.get("method", "POST")).strip() or "POST",
        path=str(spec.get("path", "")).strip() or "/control",
        mode=str(spec.get("mode", "safe")).strip() or "safe",
        note=note,
        payload_json=payload_json,
    )


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
    label: str,
    request_id: str,
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
    task_ref = operator_action_contract.task_command_ref(label, request_id)
    if not task_ref or task_ref == "-":
        return None
    return ActionButtonDTO(
        label="Accept Artifact Apply",
        command=f"/task {task_ref} | worker-apply-accept {first}",
        method="POST",
        path="/control/actions/task/worker-apply-accept",
        mode="phase2",
        note=f"accept artifact-apply proposal into runtime todo queue | proposal={first}",
        payload_json=json.dumps({"task_ref": task_ref, "proposal_ref": first}, ensure_ascii=False, separators=(",", ":")),
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


def _worker_apply_syncback_preview_button(*, project_alias: str) -> ActionButtonDTO | None:
    alias = str(project_alias or "").strip().upper()
    if not alias:
        return None
    spec = operator_action_contract.http_action_spec(f"/todo {alias} syncback preview")
    if not isinstance(spec, dict):
        return None
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    return ActionButtonDTO(
        label="Preview Accepted Syncback",
        command=str(spec.get("command", "")).strip() or f"/todo {alias} syncback preview",
        method=str(spec.get("method", "POST")).strip() or "POST",
        path=str(spec.get("path", "")).strip() or "/control/actions/runtime/syncback-preview",
        mode=str(spec.get("mode", "safe")).strip() or "safe",
        note="inspect the canonical TODO writeback plan for the accepted artifact apply",
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _worker_apply_syncback_apply_button(*, project_alias: str) -> ActionButtonDTO | None:
    alias = str(project_alias or "").strip().upper()
    if not alias:
        return None
    spec = operator_action_contract.http_action_spec(f"/todo {alias} syncback apply")
    if not isinstance(spec, dict):
        return None
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    return ActionButtonDTO(
        label="Apply Accepted Syncback",
        command=str(spec.get("command", "")).strip() or f"/todo {alias} syncback apply",
        method=str(spec.get("method", "POST")).strip() or "POST",
        path=str(spec.get("path", "")).strip() or "/control/actions/runtime/syncback-apply",
        mode=str(spec.get("mode", "phase2")).strip() or "phase2",
        note="write the accepted artifact apply back into canonical TODO.md",
        payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
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
