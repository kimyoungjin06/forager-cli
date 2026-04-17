#!/usr/bin/env python3
"""Task store, normalization, alias, and lifecycle mutation helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from aoe_tg_action_audit import load_latest_action_audit_for_runtime
from aoe_tg_operator_summary import runtime_latest_intent_summary
from aoe_tg_operator_surface import append_operator_status_summary_lines
from aoe_tg_orch_contract import derive_tf_phase, derive_tf_phase_reason, normalize_tf_phase
from aoe_tg_priority_actions import task_lane_target_snapshot, task_priority_action_snapshot
from aoe_tg_request_contract import (
    apply_background_run_ticket_snapshot,
    apply_execution_brief_snapshot,
    apply_job_contract_snapshot,
    apply_request_contract_snapshot,
    build_request_contract,
    build_execution_brief,
    build_job_contract,
    job_contract_has_body,
    normalize_background_run_ticket_snapshot,
    normalize_execution_brief_snapshot,
    normalize_job_contract_snapshot,
    normalize_request_contract_snapshot,
)
from aoe_tg_team_observatory import observatory_monitor_line, task_team_observatory_snapshot
import aoe_tg_worker_task_contract as worker_task_contract


LANE_STATES = ("pending", "running", "done", "failed", "waiting_on_dependencies")
LANE_VERDICTS = ("success", "retry", "fail", "intervention")
PLAN_CONVERGENCE_STATUSES = ("ready", "blocked", "stalled", "failed", "pending")
FOLLOWUP_BRIEF_VERSION = "2026-04-06.v1"
FOLLOWUP_BRIEF_STATUSES = ("preview_only", "executable", "partially_executable")
DEBUG_PACKET_VERSION = "2026-04-16.v1"
DEBUG_PACKET_STATES = ("clean", "watch", "blocked", "active")
PHASE_CHECKPOINT_VERSION = "2026-04-16.v1"
PHASE_CHECKPOINT_STATUSES = ("ready", "active", "blocked", "done")
_BRIEF_BLOCKED_STATUSES = {"underspecified", "operator_decision_required", "infeasible"}
_EXTERNAL_BACKGROUND_RUNNERS = {"github_runner", "remote_worker"}


def _normalize_lane_status(raw: Any, default: str = "pending") -> str:
    token = str(raw or "").strip().lower()
    if token in LANE_STATES:
        return token
    if token in {"error", "fail"}:
        return "failed"
    if token in {"complete", "completed", "success"}:
        return "done"
    if token in {"in_progress", "in-progress", "working", "active"}:
        return "running"
    if token in {"blocked", "queued"}:
        return "pending"
    return default


def _merge_role_status(prev: str, raw: Any) -> str:
    token = _normalize_lane_status(raw)
    order = {"failed": 4, "running": 3, "done": 2, "pending": 1}
    return token if order.get(token, 0) >= order.get(prev, 0) else prev


def _normalize_lane_verdict(raw: Any, default: str = "") -> str:
    token = str(raw or "").strip().lower()
    if token in LANE_VERDICTS:
        return token
    if token in {"ok", "pass"}:
        return "success"
    if token in {"failed", "error"}:
        return "fail"
    if token in {"escalate"}:
        return "intervention"
    return default


def _normalize_plan_convergence_status(raw: Any, default: str = "") -> str:
    token = str(raw or "").strip().lower()
    if token in PLAN_CONVERGENCE_STATUSES:
        return token
    return default


def _normalize_followup_brief_status(raw: Any, default: str = "") -> str:
    token = str(raw or "").strip().lower()
    if token in FOLLOWUP_BRIEF_STATUSES:
        return token
    return default


def _normalize_followup_lane_ids(raw: Any, *, limit: int = 8) -> List[str]:
    rows = raw if isinstance(raw, list) else []
    out: List[str] = []
    for row in rows:
        token = str(row or "").strip()[:32]
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit or 1))]


def _normalize_small_rows(raw: Any, *, limit: int = 8, text_limit: int = 160) -> List[str]:
    rows = raw if isinstance(raw, list) else []
    out: List[str] = []
    for row in rows:
        token = str(row or "").strip()[: max(1, int(text_limit or 1))]
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit or 1))]


def normalize_followup_brief_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    status = _normalize_followup_brief_status(raw.get("status"))
    if not status:
        return {}
    execution_lane_ids = _normalize_followup_lane_ids(raw.get("execution_lane_ids"))
    review_lane_ids = _normalize_followup_lane_ids(raw.get("review_lane_ids"))
    summary = str(raw.get("summary", "")).strip()
    reason = str(raw.get("reason", "")).strip()[:240]
    if not summary:
        parts = [status]
        if execution_lane_ids:
            parts.append("execution=" + ",".join(execution_lane_ids))
        if review_lane_ids:
            parts.append("review=" + ",".join(review_lane_ids))
        summary = " | ".join(parts)[:320]
    return {
        "version": str(raw.get("version", FOLLOWUP_BRIEF_VERSION)).strip() or FOLLOWUP_BRIEF_VERSION,
        "status": status,
        "summary": summary[:320],
        "execution_lane_ids": execution_lane_ids,
        "review_lane_ids": review_lane_ids,
        "reason": reason,
    }


def build_followup_brief_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    exec_critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    execution_lane_ids = _normalize_followup_lane_ids(exec_critic.get("manual_followup_execution_lane_ids"))
    review_lane_ids = _normalize_followup_lane_ids(exec_critic.get("manual_followup_review_lane_ids"))
    if not execution_lane_ids and not review_lane_ids:
        return {}
    status = "preview_only"
    if execution_lane_ids and review_lane_ids:
        status = "partially_executable"
    elif execution_lane_ids:
        status = "executable"
    return normalize_followup_brief_snapshot(
        {
            "version": FOLLOWUP_BRIEF_VERSION,
            "status": status,
            "execution_lane_ids": execution_lane_ids,
            "review_lane_ids": review_lane_ids,
            "reason": str(exec_critic.get("reason", exec_critic.get("note", "")) or "").strip(),
        }
    )


def apply_followup_brief_snapshot(target: Dict[str, Any], brief: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_followup_brief_snapshot(brief)
    if not snapshot or not isinstance(target, dict):
        return target
    target["followup_brief_version"] = snapshot.get("version", FOLLOWUP_BRIEF_VERSION)
    target["followup_brief_status"] = snapshot.get("status", "")
    target["followup_brief_summary"] = snapshot.get("summary", "")
    target["followup_brief_execution_lane_ids"] = list(snapshot.get("execution_lane_ids") or [])
    target["followup_brief_review_lane_ids"] = list(snapshot.get("review_lane_ids") or [])
    target["followup_brief_reason"] = snapshot.get("reason", "")
    return target


def normalize_debug_packet_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    state = str(raw.get("state", "")).strip().lower()
    if state not in DEBUG_PACKET_STATES:
        return {}
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", DEBUG_PACKET_VERSION)).strip() or DEBUG_PACKET_VERSION,
        "state": state,
    }
    for key in ("summary", "symptom", "root_cause", "failed_attempt", "next_step"):
        token = str(raw.get(key, "")).strip()
        if token:
            snapshot[key] = token[:320 if key == "summary" else 240]
    evidence = _normalize_small_rows(raw.get("evidence"), limit=10, text_limit=160)
    if evidence:
        snapshot["evidence"] = evidence
    if not snapshot.get("summary"):
        snapshot["summary"] = " | ".join(
            [
                f"state={state}",
                f"symptom={snapshot.get('symptom', '-')}",
                f"evidence={len(snapshot.get('evidence') or [])}",
                f"next={snapshot.get('next_step', '-')}",
            ]
        )[:320]
    return snapshot


def apply_debug_packet_snapshot(target: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_debug_packet_snapshot(packet)
    if not snapshot or not isinstance(target, dict):
        return target
    target["debug_packet_version"] = snapshot.get("version", DEBUG_PACKET_VERSION)
    target["debug_packet_state"] = snapshot.get("state", "")
    target["debug_packet_summary"] = snapshot.get("summary", "")
    target["debug_packet_symptom"] = snapshot.get("symptom", "")
    target["debug_packet_root_cause"] = snapshot.get("root_cause", "")
    target["debug_packet_evidence"] = list(snapshot.get("evidence") or [])
    target["debug_packet_failed_attempt"] = snapshot.get("failed_attempt", "")
    target["debug_packet_next_step"] = snapshot.get("next_step", "")
    return target


def _task_ref_label(task: Dict[str, Any]) -> str:
    for key in ("short_id", "alias", "request_id"):
        token = str(task.get(key, "")).strip()
        if token:
            return token
    return "task"


def build_debug_packet_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    task_ref = _task_ref_label(task)
    execution_brief_status = str(task.get("execution_brief_status", "")).strip().lower()
    blocked_slice = _normalize_small_rows(task.get("execution_brief_blocked_slice"), limit=8, text_limit=120)
    missing_fields = _normalize_small_rows(task.get("request_contract_missing_fields"), limit=8, text_limit=120)
    worker_preflight_rows = _normalize_small_rows(task.get("background_run_worker_preflight_rows"), limit=8, text_limit=160)
    worker_result_cautions = _normalize_small_rows(task.get("background_run_worker_result_cautions"), limit=6, text_limit=160)
    evidence: List[str] = []
    evidence.extend(blocked_slice)
    evidence.extend(missing_fields)
    evidence.extend(worker_preflight_rows)
    evidence.extend(worker_result_cautions)
    evidence.extend(_normalize_small_rows(task.get("background_run_evidence_artifacts"), limit=6, text_limit=160))
    if isinstance(task.get("result"), dict):
        evidence.extend(_normalize_small_rows((task.get("result") or {}).get("degraded_by"), limit=6, text_limit=120))
    evidence = _normalize_small_rows(evidence, limit=10, text_limit=160)

    worker_blocker: Dict[str, Any] = {}
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind and module_kind != "general":
        worker_blocker = worker_task_contract.derive_worker_task_module_action_blocker(
            {
                "module_kind": module_kind,
                "rows_kind": str(task.get("background_run_worker_preflight_rows_summary", "")).strip().split(" | ", 1)[0] or "",
                "rows": worker_preflight_rows,
                "summary_line": str(task.get("background_run_worker_preflight_rows_summary", "")).strip() or "-",
                "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
            },
            mode="apply",
        )

    status = str(task.get("status", "")).strip().lower()
    followup_status = str(task.get("followup_brief_status", "")).strip().lower()
    rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
    state = "clean"
    symptom = ""
    root_cause = ""
    next_step = "-"

    if execution_brief_status in _BRIEF_BLOCKED_STATUSES:
        state = "blocked"
        symptom = "execution_brief_blocked"
        root_cause = (
            str(task.get("execution_brief_operator_decision", "")).strip()
            or ", ".join(blocked_slice[:4])
            or str(task.get("plan_gate_reason", "")).strip()
            or str(task.get("execution_brief_summary", "")).strip()
        )
        next_step = "/offdesk review"
    elif str(worker_blocker.get("summary_line", "")).strip():
        state = "blocked"
        symptom = "worker_gate_blocked"
        root_cause = (
            str(worker_blocker.get("remediation", "")).strip()
            or str(worker_blocker.get("summary_line", "")).strip()
        )
        next_step = str(worker_blocker.get("next_step", "")).strip() or f"/task {task_ref}"
        evidence = _normalize_small_rows(
            list(worker_blocker.get("blocked_rows") or []) + evidence,
            limit=10,
            text_limit=160,
        )
    elif status == "failed" or any(
        str(((task.get("stages") or {}).get(name, ""))).strip().lower() == "failed"
        for name in ("execution", "verification", "integration", "close")
    ):
        state = "blocked"
        symptom = "task_failed"
        root_cause = (
            str(task.get("backend_contract_note", "")).strip()
            or str(((task.get("exec_critic") or {}).get("reason", ""))).strip()
            or "task entered failed state"
        )
        next_step = f"/retry {task_ref}"
    elif followup_status == "preview_only":
        state = "watch"
        symptom = "followup_preview_only"
        root_cause = str(task.get("followup_brief_reason", "")).strip() or "operator-owned follow-up remains"
        next_step = f"/followup {task_ref}"
    elif rate_limit:
        state = "watch"
        symptom = "rate_limited"
        providers = ",".join(str(item).strip() for item in (rate_limit.get("limited_providers") or []) if str(item).strip()) or "-"
        root_cause = f"providers={providers} retry_at={str(rate_limit.get('retry_at', '')).strip() or '-'}"
        next_step = f"/check {task_ref}"
    elif str(task.get("background_run_status", "")).strip().lower() in {"queued", "dispatching", "running"}:
        state = "active"
        symptom = "background_run_inflight"
        root_cause = str(task.get("background_run_runtime_summary", "")).strip() or str(task.get("background_run_status", "")).strip()
        next_step = f"/task {task_ref}"

    failed_attempt_parts: List[str] = []
    exec_critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    critic_verdict = str(exec_critic.get("verdict", "")).strip()
    critic_action = str(exec_critic.get("action", "")).strip()
    if critic_verdict:
        try:
            attempt = int(exec_critic.get("attempt", 0) or 0)
            max_attempts = int(exec_critic.get("max_attempts", 0) or 0)
        except Exception:
            attempt = 0
            max_attempts = 0
        critic_text = critic_verdict
        if critic_action:
            critic_text += f"/{critic_action}"
        if attempt and max_attempts:
            critic_text += f" {attempt}/{max_attempts}"
        failed_attempt_parts.append("critic=" + critic_text)
    backend = str(task.get("backend", "")).strip()
    backend_verdict = str(task.get("backend_verdict", "")).strip()
    if backend or backend_verdict:
        failed_attempt_parts.append("backend=" + "/".join(part for part in (backend, backend_verdict) if part))
    background_status = str(task.get("background_run_status", "")).strip()
    if background_status:
        failed_attempt_parts.append("background=" + background_status)

    return normalize_debug_packet_snapshot(
        {
            "version": DEBUG_PACKET_VERSION,
            "state": state,
            "symptom": symptom or "none",
            "root_cause": root_cause or ("no immediate debug focus" if state == "clean" else "-"),
            "failed_attempt": " | ".join(part for part in failed_attempt_parts if part)[:240],
            "next_step": next_step,
            "evidence": evidence,
        }
    )


def normalize_phase_checkpoint_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    status = str(raw.get("status", "")).strip().lower()
    if status not in PHASE_CHECKPOINT_STATUSES:
        return {}
    current_phase = str(raw.get("current_phase", "")).strip().lower() or "plan"
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", PHASE_CHECKPOINT_VERSION)).strip() or PHASE_CHECKPOINT_VERSION,
        "status": status,
        "current_phase": current_phase,
    }
    summary = str(raw.get("summary", "")).strip()
    if summary:
        snapshot["summary"] = summary[:320]
    rows = _normalize_small_rows(raw.get("rows"), limit=8, text_limit=200)
    if rows:
        snapshot["rows"] = rows
    if not snapshot.get("summary"):
        snapshot["summary"] = " | ".join(
            [f"status={status}", f"current={current_phase}", *list(snapshot.get("rows") or [])[:4]]
        )[:320]
    return snapshot


def apply_phase_checkpoint_snapshot(target: Dict[str, Any], checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_phase_checkpoint_snapshot(checkpoint)
    if not snapshot or not isinstance(target, dict):
        return target
    target["phase_checkpoint_version"] = snapshot.get("version", PHASE_CHECKPOINT_VERSION)
    target["phase_checkpoint_status"] = snapshot.get("status", "")
    target["phase_checkpoint_current_phase"] = snapshot.get("current_phase", "")
    target["phase_checkpoint_summary"] = snapshot.get("summary", "")
    target["phase_checkpoint_rows"] = list(snapshot.get("rows") or [])
    return target


def build_phase_checkpoint_snapshot(task: Dict[str, Any], debug_packet: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    debug_snapshot = normalize_debug_packet_snapshot(debug_packet or build_debug_packet_snapshot(task))
    job_status = str(task.get("job_contract_status", "")).strip().lower() or "ready"
    tf_phase = str(task.get("tf_phase", "")).strip().lower() or normalize_tf_phase(derive_tf_phase(task), "queued")
    stages = task.get("stages") if isinstance(task.get("stages"), dict) else {}
    planning_stage = str(stages.get("planning", "")).strip().lower()
    staffing_stage = str(stages.get("staffing", "")).strip().lower()
    execution_stage = str(stages.get("execution", "")).strip().lower()
    verification_stage = str(stages.get("verification", "")).strip().lower()
    integration_stage = str(stages.get("integration", "")).strip().lower()
    close_stage = str(stages.get("close", "")).strip().lower()
    background_status = str(task.get("background_run_status", "")).strip().lower()
    followup_status = str(task.get("followup_brief_status", "")).strip().lower()

    rows: List[str] = []
    row_states: Dict[str, str] = {}

    def _append_row(kind: str, state: str, note: str) -> None:
        row_states[kind] = state
        token = f"{kind}={state}"
        if note:
            token += f"|note={note[:120]}"
        rows.append(token[:200])

    plan_note = (
        str(task.get("plan_gate_reason", "")).strip()
        or str(task.get("execution_brief_summary", "")).strip()
        or str(task.get("job_contract_summary", "")).strip()
    )
    if execution_stage in {"running", "done"} or staffing_stage == "done":
        _append_row("plan", "done", plan_note or "execution started")
    elif job_status == "blocked" or str(task.get("execution_brief_status", "")).strip().lower() in _BRIEF_BLOCKED_STATUSES or task.get("plan_gate_passed") is False:
        _append_row("plan", "blocked", plan_note or "plan contract blocked")
    elif planning_stage == "running" or tf_phase == "planning":
        _append_row("plan", "active", plan_note or "plan review in progress")
    else:
        _append_row("plan", "ready", plan_note or "plan contract ready")

    implement_note = (
        str(task.get("lane_summary", "")).strip()
        or str(task.get("background_run_runtime_summary", "")).strip()
        or str(task.get("backend_contract_note", "")).strip()
    )
    if execution_stage == "failed" or str(task.get("status", "")).strip().lower() == "failed":
        _append_row("implement", "blocked", implement_note or "execution failed")
    elif execution_stage == "done":
        _append_row("implement", "done", implement_note or "execution complete")
    elif execution_stage == "running" or background_status in {"queued", "dispatching", "running"} or staffing_stage in {"running", "done"}:
        _append_row("implement", "active", implement_note or "execution in progress")
    else:
        _append_row("implement", "ready", implement_note or "implementation lane ready")

    verify_note = (
        str(task.get("followup_brief_reason", "")).strip()
        or str(task.get("background_run_worker_preflight_summary", "")).strip()
        or str(debug_snapshot.get("root_cause", "")).strip()
    )
    if verification_stage == "failed" or str(debug_snapshot.get("symptom", "")).strip() in {"worker_gate_blocked", "followup_preview_only"}:
        _append_row("verify", "blocked", verify_note or "verification blocked")
    elif verification_stage == "done":
        _append_row("verify", "done", verify_note or "verification complete")
    elif verification_stage == "running" or followup_status in {"executable", "partially_executable"} or integration_stage == "running":
        _append_row("verify", "active", verify_note or "verification in progress")
    else:
        _append_row("verify", "ready", verify_note or "verification lane ready")

    handoff_note = (
        str(task.get("background_run_canonical_writeback_summary", "")).strip()
        or str(task.get("background_run_canonical_mutation_summary", "")).strip()
        or str(task.get("background_run_worker_syncback_summary", "")).strip()
        or str(task.get("background_run_status", "")).strip()
    )
    if close_stage == "failed":
        _append_row("handoff", "blocked", handoff_note or "handoff failed")
    elif close_stage == "done" or str(task.get("status", "")).strip().lower() == "completed":
        _append_row("handoff", "done", handoff_note or "handoff complete")
    elif close_stage == "running" or integration_stage == "running" or background_status in {"queued", "dispatching", "running"}:
        _append_row("handoff", "active", handoff_note or "handoff in progress")
    else:
        _append_row("handoff", "ready", handoff_note or "handoff ready")

    overall = "ready"
    if all(row_states.get(kind) == "done" for kind in ("plan", "implement", "verify", "handoff")):
        overall = "done"
    elif any(state == "blocked" for state in row_states.values()):
        overall = "blocked"
    elif any(state == "active" for state in row_states.values()):
        overall = "active"
    current_phase = "done"
    for kind in ("plan", "implement", "verify", "handoff"):
        if row_states.get(kind) != "done":
            current_phase = kind
            break
    return normalize_phase_checkpoint_snapshot(
        {
            "version": PHASE_CHECKPOINT_VERSION,
            "status": overall,
            "current_phase": current_phase,
            "summary": " | ".join(
                [
                    f"status={overall}",
                    f"current={current_phase}",
                    *[f"{kind}={row_states.get(kind, '-')}" for kind in ("plan", "implement", "verify", "handoff")],
                ]
            )[:320],
            "rows": rows,
        }
    )


def refresh_task_planning_primitives(
    task: Dict[str, Any],
    *,
    request_contract: Optional[Dict[str, Any]] = None,
    execution_brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}

    explicit_request_contract_snapshot = (
        normalize_request_contract_snapshot(request_contract)
        if isinstance(request_contract, dict)
        else {}
    )
    explicit_execution_brief_snapshot = (
        normalize_execution_brief_snapshot(execution_brief)
        if isinstance(execution_brief, dict)
        else {}
    )
    explicit_request_contract = bool(explicit_request_contract_snapshot)
    explicit_execution_brief = bool(explicit_execution_brief_snapshot)

    request_contract_snapshot = (
        explicit_request_contract_snapshot
        if explicit_request_contract
        else normalize_request_contract_snapshot(
            {
            "version": task.get("request_contract_version"),
            "contract_type": task.get("request_contract_type"),
            "preset": task.get("request_contract_preset"),
            "status": task.get("request_contract_status"),
            "summary": task.get("request_contract_summary"),
            "missing_fields": task.get("request_contract_missing_fields"),
            "required_outputs": task.get("request_contract_required_outputs"),
            "fields": task.get("request_contract_fields"),
            "artifact_contracts": task.get("request_contract_artifact_contracts"),
        }
        )
    )
    if request_contract_snapshot:
        apply_request_contract_snapshot(task, request_contract_snapshot)

    execution_brief_snapshot: Dict[str, Any] = {}
    if explicit_execution_brief:
        execution_brief_snapshot = explicit_execution_brief_snapshot
    elif explicit_request_contract and request_contract_snapshot:
        execution_brief_snapshot = build_execution_brief(request_contract_snapshot)
    else:
        execution_brief_snapshot = normalize_execution_brief_snapshot(
            {
                "version": task.get("execution_brief_version"),
                "status": task.get("execution_brief_status"),
                "summary": task.get("execution_brief_summary"),
                "executable_slice": task.get("execution_brief_executable_slice"),
                "blocked_slice": task.get("execution_brief_blocked_slice"),
                "operator_decision": task.get("execution_brief_operator_decision"),
                "offdesk_allowed": task.get("execution_brief_offdesk_allowed"),
            }
        )
    if not execution_brief_snapshot and request_contract_snapshot:
        execution_brief_snapshot = build_execution_brief(request_contract_snapshot)
    if execution_brief_snapshot:
        apply_execution_brief_snapshot(task, execution_brief_snapshot)
    else:
        for key in (
            "execution_brief_version",
            "execution_brief_status",
            "execution_brief_summary",
            "execution_brief_executable_slice",
            "execution_brief_blocked_slice",
            "execution_brief_operator_decision",
            "execution_brief_offdesk_allowed",
        ):
            task.pop(key, None)

    job_contract_snapshot: Dict[str, Any] = {}
    if request_contract_snapshot and (explicit_request_contract or explicit_execution_brief):
        job_contract_snapshot = build_job_contract(request_contract_snapshot, execution_brief_snapshot)
    else:
        job_contract_snapshot = normalize_job_contract_snapshot(
            {
                "version": task.get("job_contract_version"),
                "status": task.get("job_contract_status"),
                "planning_mode": task.get("job_contract_planning_mode"),
                "summary": task.get("job_contract_summary"),
                "goal": task.get("job_contract_goal"),
                "scope": task.get("job_contract_scope"),
                "non_goals": task.get("job_contract_non_goals"),
                "risks": task.get("job_contract_risks"),
                "acceptance_checks": task.get("job_contract_acceptance_checks"),
                "artifacts_to_touch": task.get("job_contract_artifacts_to_touch"),
                "rollback_hint": task.get("job_contract_rollback_hint"),
            }
        )
    if not job_contract_snapshot and request_contract_snapshot:
        job_contract_snapshot = build_job_contract(request_contract_snapshot, execution_brief_snapshot)
    if not job_contract_snapshot and execution_brief_snapshot:
        fallback_outputs = [
            str(item).strip()
            for item in (
                list(execution_brief_snapshot.get("executable_slice") or [])
                + list(task.get("background_run_worker_update_stub_targets") or [])
            )
            if str(item).strip()
        ]
        fallback_evidence = [
            str(item).strip()
            for item in (
                list(task.get("background_run_worker_result_evidence_refs") or [])
                + list(task.get("background_run_evidence_artifacts") or [])
            )
            if str(item).strip()
        ]
        fallback_artifact_contracts: Dict[str, Dict[str, Any]] = {}
        for token in fallback_outputs[:8]:
            fallback_artifact_contracts[token] = {"path": token}
        readonly = False
        plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
        meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
        phase2_execution_plan = (
            meta.get("phase2_execution_plan")
            if isinstance(meta.get("phase2_execution_plan"), dict)
            else {}
        )
        if isinstance(phase2_execution_plan, dict):
            readonly = bool(phase2_execution_plan.get("readonly", False))
        job_contract_snapshot = build_job_contract(
            {
                "version": task.get("request_contract_version"),
                "contract_type": str(task.get("phase2_team_preset", "")).strip()
                or str(task.get("phase1_role_preset", "")).strip()
                or "general",
                "preset": str(task.get("phase2_team_preset", "")).strip()
                or str(task.get("phase1_role_preset", "")).strip()
                or "general",
                "status": "complete"
                if str(execution_brief_snapshot.get("status", "")).strip().lower() not in _BRIEF_BLOCKED_STATUSES
                else "incomplete",
                "objective": str(task.get("prompt", "")).strip(),
                "summary": str(task.get("execution_brief_summary", "")).strip()
                or str(task.get("prompt", "")).strip(),
                "required_outputs": fallback_outputs,
                "required_evidence": fallback_evidence,
                "missing_fields": list(execution_brief_snapshot.get("blocked_slice") or []),
                "artifact_contracts": fallback_artifact_contracts,
                "readonly": readonly,
            },
            execution_brief_snapshot,
        )
    if job_contract_snapshot:
        apply_job_contract_snapshot(task, job_contract_snapshot)
    else:
        for key in (
            "job_contract_version",
            "job_contract_status",
            "job_contract_planning_mode",
            "job_contract_summary",
            "job_contract_goal",
            "job_contract_scope",
            "job_contract_non_goals",
            "job_contract_risks",
            "job_contract_acceptance_checks",
            "job_contract_artifacts_to_touch",
            "job_contract_rollback_hint",
        ):
            task.pop(key, None)

    debug_packet_snapshot: Dict[str, Any] = {}
    if not (explicit_request_contract or explicit_execution_brief):
        debug_packet_snapshot = normalize_debug_packet_snapshot(
            {
                "version": task.get("debug_packet_version"),
                "state": task.get("debug_packet_state"),
                "summary": task.get("debug_packet_summary"),
                "symptom": task.get("debug_packet_symptom"),
                "root_cause": task.get("debug_packet_root_cause"),
                "evidence": task.get("debug_packet_evidence"),
                "failed_attempt": task.get("debug_packet_failed_attempt"),
                "next_step": task.get("debug_packet_next_step"),
            }
        )
    if not debug_packet_snapshot:
        debug_packet_snapshot = build_debug_packet_snapshot(task)
    if debug_packet_snapshot:
        apply_debug_packet_snapshot(task, debug_packet_snapshot)
    else:
        for key in (
            "debug_packet_version",
            "debug_packet_state",
            "debug_packet_summary",
            "debug_packet_symptom",
            "debug_packet_root_cause",
            "debug_packet_evidence",
            "debug_packet_failed_attempt",
            "debug_packet_next_step",
        ):
            task.pop(key, None)

    phase_checkpoint_snapshot: Dict[str, Any] = {}
    if not (explicit_request_contract or explicit_execution_brief):
        phase_checkpoint_snapshot = normalize_phase_checkpoint_snapshot(
            {
                "version": task.get("phase_checkpoint_version"),
                "status": task.get("phase_checkpoint_status"),
                "current_phase": task.get("phase_checkpoint_current_phase"),
                "summary": task.get("phase_checkpoint_summary"),
                "rows": task.get("phase_checkpoint_rows"),
            }
        )
    if not phase_checkpoint_snapshot:
        phase_checkpoint_snapshot = build_phase_checkpoint_snapshot(task, debug_packet_snapshot)
    if phase_checkpoint_snapshot:
        apply_phase_checkpoint_snapshot(task, phase_checkpoint_snapshot)
    else:
        for key in (
            "phase_checkpoint_version",
            "phase_checkpoint_status",
            "phase_checkpoint_current_phase",
            "phase_checkpoint_summary",
            "phase_checkpoint_rows",
        ):
            task.pop(key, None)
    return task


def derive_task_dispatch_gate(task: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    refresh_task_planning_primitives(task)
    task_ref = _task_ref_label(task)
    contract_snapshot = normalize_job_contract_snapshot(
        {
            "version": task.get("job_contract_version"),
            "status": task.get("job_contract_status"),
            "planning_mode": task.get("job_contract_planning_mode"),
            "summary": task.get("job_contract_summary"),
            "goal": task.get("job_contract_goal"),
            "scope": task.get("job_contract_scope"),
            "non_goals": task.get("job_contract_non_goals"),
            "risks": task.get("job_contract_risks"),
            "acceptance_checks": task.get("job_contract_acceptance_checks"),
            "artifacts_to_touch": task.get("job_contract_artifacts_to_touch"),
            "rollback_hint": task.get("job_contract_rollback_hint"),
        }
    )
    contract_status = str(task.get("job_contract_status", "")).strip().lower()
    contract_summary = str(task.get("job_contract_summary", "")).strip() or "-"
    has_contract_body = job_contract_has_body(contract_snapshot)
    debug_state = str(task.get("debug_packet_state", "")).strip().lower()
    debug_summary = str(task.get("debug_packet_summary", "")).strip() or "-"
    phase_status = str(task.get("phase_checkpoint_status", "")).strip().lower()
    phase_current = str(task.get("phase_checkpoint_current_phase", "")).strip().lower()
    phase_summary = str(task.get("phase_checkpoint_summary", "")).strip() or "-"
    status = "ready"
    reason_code = "ready"
    remediation = "dispatch contract is ready"
    next_step = f"/task {task_ref}"
    detail = contract_summary
    if contract_status in {"", "-"} or not has_contract_body:
        status = "blocked"
        reason_code = "job_contract_missing"
        remediation = "capture the job contract goal, scope, acceptance checks, and rollback hint before dispatching a new run"
        detail = "job contract missing"
    elif debug_state in {"", "-"} or debug_summary in {"", "-"}:
        status = "blocked"
        reason_code = "debug_packet_missing"
        remediation = "derive and review the debug packet before retrying, replanning, or dispatching another execution step"
        detail = "debug packet missing"
    elif debug_state == "clean":
        status = "blocked"
        reason_code = "debug_packet_not_ready"
        remediation = "capture the current symptom, failed attempt, and next debug step before retrying, replanning, or dispatching another execution step"
        detail = debug_summary
    return {
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "summary": "dispatch_gate | contract={contract} | debug={debug} | phase={phase}/{current}".format(
            contract=contract_status or "-",
            debug=debug_state or "-",
            phase=phase_status or "-",
            current=phase_current or "-",
        )[:320],
        "remediation": remediation,
        "next_step": next_step,
        "job_contract_status": contract_status or "-",
        "job_contract_summary": contract_summary,
        "debug_packet_state": debug_state or "-",
        "debug_packet_summary": debug_summary,
        "debug_packet_next_step": str(task.get("debug_packet_next_step", "")).strip() or "-",
        "phase_checkpoint_status": phase_status or "-",
        "phase_checkpoint_current_phase": phase_current or "-",
        "phase_checkpoint_summary": phase_summary,
    }


def derive_task_apply_gate(task: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    refresh_task_planning_primitives(task)
    task_ref = _task_ref_label(task)
    contract_snapshot = normalize_job_contract_snapshot(
        {
            "version": task.get("job_contract_version"),
            "status": task.get("job_contract_status"),
            "planning_mode": task.get("job_contract_planning_mode"),
            "summary": task.get("job_contract_summary"),
            "goal": task.get("job_contract_goal"),
            "scope": task.get("job_contract_scope"),
            "non_goals": task.get("job_contract_non_goals"),
            "risks": task.get("job_contract_risks"),
            "acceptance_checks": task.get("job_contract_acceptance_checks"),
            "artifacts_to_touch": task.get("job_contract_artifacts_to_touch"),
            "rollback_hint": task.get("job_contract_rollback_hint"),
        }
    )
    contract_status = str(task.get("job_contract_status", "")).strip().lower()
    contract_summary = str(task.get("job_contract_summary", "")).strip() or "-"
    has_contract_body = job_contract_has_body(contract_snapshot)
    phase_status = str(task.get("phase_checkpoint_status", "")).strip().lower()
    phase_current = str(task.get("phase_checkpoint_current_phase", "")).strip().lower()
    phase_summary = str(task.get("phase_checkpoint_summary", "")).strip() or "-"
    status = "ready"
    reason_code = "ready"
    remediation = "apply contract is ready"
    next_step = f"/task {task_ref}"
    detail = phase_summary
    if contract_status in {"", "-"} or not has_contract_body:
        status = "blocked"
        reason_code = "job_contract_missing"
        remediation = "capture the job contract goal, scope, acceptance checks, and rollback hint before applying worker artifacts"
        detail = "job contract missing"
    elif contract_status == "blocked":
        status = "blocked"
        reason_code = "job_contract_blocked"
        remediation = "resolve the blocked job contract scope or acceptance gaps before applying worker artifacts"
        detail = contract_summary
    elif phase_status == "blocked":
        status = "blocked"
        reason_code = "phase_checkpoint_blocked"
        remediation = "clear the current checkpoint blocker before applying worker artifacts"
        detail = phase_summary
    elif phase_current in {"", "-", "plan", "implement"}:
        status = "blocked"
        reason_code = "phase_checkpoint_not_apply_ready"
        remediation = "wait until the task reaches verify or handoff before applying worker artifacts"
        detail = phase_summary
    return {
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "summary": "apply_gate | contract={contract} | phase={phase}/{current}".format(
            contract=contract_status or "-",
            phase=phase_status or "-",
            current=phase_current or "-",
        )[:320],
        "remediation": remediation,
        "next_step": next_step,
        "job_contract_status": contract_status or "-",
        "job_contract_summary": contract_summary,
        "phase_checkpoint_status": phase_status or "-",
        "phase_checkpoint_current_phase": phase_current or "-",
        "phase_checkpoint_summary": phase_summary,
    }


def derive_task_manual_gate(task: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    refresh_task_planning_primitives(task)
    task_ref = _task_ref_label(task)
    contract_snapshot = normalize_job_contract_snapshot(
        {
            "version": task.get("job_contract_version"),
            "status": task.get("job_contract_status"),
            "planning_mode": task.get("job_contract_planning_mode"),
            "summary": task.get("job_contract_summary"),
            "goal": task.get("job_contract_goal"),
            "scope": task.get("job_contract_scope"),
            "non_goals": task.get("job_contract_non_goals"),
            "risks": task.get("job_contract_risks"),
            "acceptance_checks": task.get("job_contract_acceptance_checks"),
            "artifacts_to_touch": task.get("job_contract_artifacts_to_touch"),
            "rollback_hint": task.get("job_contract_rollback_hint"),
        }
    )
    contract_status = str(task.get("job_contract_status", "")).strip().lower()
    contract_summary = str(task.get("job_contract_summary", "")).strip() or "-"
    debug_state = str(task.get("debug_packet_state", "")).strip().lower()
    debug_summary = str(task.get("debug_packet_summary", "")).strip() or "-"
    phase_status = str(task.get("phase_checkpoint_status", "")).strip().lower()
    phase_current = str(task.get("phase_checkpoint_current_phase", "")).strip().lower()
    phase_summary = str(task.get("phase_checkpoint_summary", "")).strip() or "-"
    phase_rows = [str(item).strip().lower() for item in (task.get("phase_checkpoint_rows") or []) if str(item).strip()]
    manual_phase_ready = phase_current in {"verify", "handoff"}
    if not manual_phase_ready:
        for item in phase_rows:
            phase_name, _, rest = item.partition("=")
            row_state = rest.split("|", 1)[0].strip().lower() if rest else ""
            if phase_name in {"verify", "handoff"} and row_state in {"ready", "active", "done", "running"}:
                manual_phase_ready = True
                break
    status = "ready"
    reason_code = "ready"
    remediation = "manual route is ready"
    next_step = f"/task {task_ref}"
    detail = phase_summary
    if phase_status == "blocked" and not manual_phase_ready:
        status = "blocked"
        reason_code = "phase_checkpoint_blocked"
        remediation = "clear the current checkpoint blocker before applying judge-backed manual steps"
        detail = phase_summary
    elif not manual_phase_ready:
        status = "blocked"
        reason_code = "phase_checkpoint_not_manual_ready"
        remediation = "wait until the task reaches verify or handoff before applying judge-backed manual steps"
        detail = phase_summary
    return {
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "summary": "manual_gate | contract={contract} | debug={debug} | phase={phase}/{current}".format(
            contract=contract_status or "-",
            debug=debug_state or "-",
            phase=phase_status or "-",
            current=phase_current or "-",
        )[:320],
        "remediation": remediation,
        "next_step": next_step,
        "job_contract_status": contract_status or "-",
        "job_contract_summary": contract_summary,
        "debug_packet_state": debug_state or "-",
        "debug_packet_summary": debug_summary,
        "phase_checkpoint_status": phase_status or "-",
        "phase_checkpoint_current_phase": phase_current or "-",
        "phase_checkpoint_summary": phase_summary,
    }


def build_reentry_rails_summary(task: Dict[str, Any]) -> str:
    if not isinstance(task, dict):
        return ""
    targets = task_lane_target_snapshot(task)
    rerun_exec = [str(x).strip() for x in (targets.get("rerun_execution_lane_ids") or []) if str(x).strip()]
    rerun_review = [str(x).strip() for x in (targets.get("rerun_review_lane_ids") or []) if str(x).strip()]
    rerun_status = "none"
    brief_status = str(task.get("execution_brief_status", "")).strip().lower()
    if rerun_exec or rerun_review:
        if brief_status in _BRIEF_BLOCKED_STATUSES:
            rerun_status = f"blocked:{brief_status}"
        elif brief_status == "partially_executable":
            rerun_status = "partial"
        else:
            rerun_status = "ready"
    followup_status = str(task.get("followup_brief_status", "")).strip().lower()
    followup_exec = [str(x).strip() for x in (task.get("followup_brief_execution_lane_ids") or []) if str(x).strip()]
    followup_review = [str(x).strip() for x in (task.get("followup_brief_review_lane_ids") or []) if str(x).strip()]
    if not followup_status:
        followup_status = "none"
    background_status = str(task.get("background_run_status", "")).strip().lower()
    background_runner = str(task.get("background_run_runner_target", "")).strip().lower()
    background_part = ""
    if background_status or background_runner:
        background_part = "bg={status}{runner}".format(
            status=background_status or "-",
            runner=(f"/{background_runner}" if background_runner else ""),
        )
    summary_parts = [
        "retry={status}{exec_part}{review_part}".format(
            status=rerun_status,
            exec_part=(f" exec={','.join(rerun_exec)}" if rerun_exec else ""),
            review_part=(f" review={','.join(rerun_review)}" if rerun_review else ""),
        ),
        "followup={status}{exec_part}{review_part}".format(
            status=followup_status,
            exec_part=(f" exec={','.join(followup_exec)}" if followup_exec else ""),
            review_part=(f" review={','.join(followup_review)}" if followup_review else ""),
        ),
    ]
    if background_part:
        summary_parts.append(background_part)
    return " | ".join(summary_parts)[:320]


def _background_run_artifact_paths(task: Dict[str, Any]) -> List[str]:
    return [
        str(item).strip()
        for item in (task.get("background_run_evidence_artifacts") or [])
        if str(item).strip()
    ]


def _find_background_artifact(task: Dict[str, Any], prefix: str) -> str:
    for item in _background_run_artifact_paths(task):
        if item.startswith(prefix):
            return item[:240]
    return ""


def derive_background_run_external_snapshot(task: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(task, dict):
        return {}
    runner_target = str(task.get("background_run_runner_target", "")).strip().lower()
    if runner_target not in _EXTERNAL_BACKGROUND_RUNNERS:
        return {}
    status = str(task.get("background_run_status", "")).strip().lower()
    runtime_summary = str(task.get("background_run_runtime_summary", "")).strip()
    evidence_bundle = str(task.get("background_run_evidence_bundle", "")).strip()
    handoff_path = _find_background_artifact(task, "background_run_handoffs/")
    ack_path = _find_background_artifact(task, "background_run_acks/")
    result_path = _find_background_artifact(task, "background_run_results/")

    phase = ""
    note = ""
    if result_path or status in {"completed", "failed"}:
        phase = "result_received"
        note = result_path or evidence_bundle or status
    elif ack_path or "external_pickup_acknowledged" in evidence_bundle or "ack=" in runtime_summary:
        phase = "pickup_acknowledged"
        note = ack_path or runtime_summary or evidence_bundle
    elif handoff_path or "external_handoff_emitted" in evidence_bundle or "_handoff=" in runtime_summary:
        phase = "handoff_emitted"
        note = handoff_path or runtime_summary or evidence_bundle
    elif status in {"queued", "dispatching", "running"}:
        phase = "awaiting_external_pickup"
        note = f"{runner_target} awaiting pickup"

    if not phase:
        return {}
    return {
        "phase": phase[:64],
        "note": note[:240],
    }


def _normalize_plan_issue_codes(raw: Any, *, limit: int = 12) -> List[str]:
    rows = raw if isinstance(raw, list) else []
    normalized: List[str] = []
    for row in rows:
        token = str(row or "").strip().lower()
        if not token or token in normalized:
            continue
        normalized.append(token[:64])
    return normalized[: max(1, int(limit or 1))]


def _normalize_plan_issue_history(raw: Any, *, keep: int = 20) -> List[Dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    normalized: List[Dict[str, Any]] = []
    for item in rows[-max(1, int(keep or 1)) :]:
        if not isinstance(item, dict):
            continue
        try:
            round_no = max(1, int(item.get("round", 0) or 0))
        except Exception:
            round_no = 1
        row: Dict[str, Any] = {"round": round_no}
        review_pass = str(item.get("review_pass", "")).strip().lower()
        if review_pass in {"contract", "execution", "verification"}:
            row["review_pass"] = review_pass
        status = str(item.get("status", "")).strip().lower()
        if status in {"approved", "issues"}:
            row["status"] = status
        primary_issue = str(item.get("primary_issue", "")).strip()
        if primary_issue:
            row["primary_issue"] = primary_issue[:240]
        issue_codes = _normalize_plan_issue_codes(item.get("issue_codes"), limit=8)
        if issue_codes:
            row["issue_codes"] = issue_codes
        try:
            issue_count = max(0, int(item.get("issue_count", 0) or 0))
        except Exception:
            issue_count = 0
        row["issue_count"] = issue_count
        for key in ("provider", "planner_provider", "critic_provider"):
            token = str(item.get(key, "")).strip()
            if token:
                row[key] = token[:64]
        normalized.append(row)
    return normalized


def _normalize_lane_state_rows(raw_rows: Any, *, kind: str) -> List[Dict[str, Any]]:
    rows = raw_rows if isinstance(raw_rows, list) else []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lane_id = str(row.get("lane_id", "")).strip() or str(row.get("id", "")).strip()
        if not lane_id:
            continue
        item: Dict[str, Any] = {
            "lane_id": lane_id[:32],
            "role": str(row.get("role", "")).strip()[:64] or ("Codex-Reviewer" if kind == "review" else "Worker"),
            "status": _normalize_lane_status(row.get("status")),
        }
        if kind == "execution":
            subtask_ids = [str(x).strip()[:32] for x in (row.get("subtask_ids") or []) if str(x).strip()]
            if subtask_ids:
                item["subtask_ids"] = subtask_ids
        else:
            item["kind"] = str(row.get("kind", "")).strip()[:32] or "verifier"
            depends = [str(x).strip()[:32] for x in (row.get("depends_on") or []) if str(x).strip()]
            if depends:
                item["depends_on"] = depends
            waiting = [str(x).strip()[:32] for x in (row.get("waiting_on") or []) if str(x).strip()]
            if waiting:
                item["waiting_on"] = waiting
            verdict = _normalize_lane_verdict(row.get("verdict"))
            if verdict:
                item["verdict"] = verdict
            action = str(row.get("action", "")).strip().lower()
            if action:
                item["action"] = action[:32]
        reason = str(row.get("reason", "")).strip()
        if reason:
            item["reason"] = reason[:240]
        for key in (
            "request_id",
            "started_at",
            "last_event_at",
            "last_event_kind",
            "backend",
            "outcome_reason_code",
        ):
            token = str(row.get(key, "")).strip()
            if token:
                item[key] = token[:240]
        try:
            tool_count = int(row.get("tool_count", 0) or 0)
        except Exception:
            tool_count = 0
        if tool_count > 0:
            item["tool_count"] = tool_count
        touched_files = [str(x).strip()[:240] for x in (row.get("touched_files") or []) if str(x).strip()]
        if touched_files:
            item["touched_files"] = touched_files[:8]
        normalized.append(item)
    return normalized


def _lane_state_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {name: 0 for name in LANE_STATES}
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = _normalize_lane_status(row.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return {key: value for key, value in counts.items() if value}


def _lane_verdict_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts = {name: 0 for name in LANE_VERDICTS}
    for row in rows:
        if not isinstance(row, dict):
            continue
        verdict = _normalize_lane_verdict(row.get("verdict"))
        if verdict:
            counts[verdict] = counts.get(verdict, 0) + 1
    return {key: value for key, value in counts.items() if value}


def _dedupe_phase2_roles(rows: Any) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip()
        if not role or role in seen:
            continue
        seen.add(role)
        result.append(role)
    return result


def _phase2_shape_roles(task: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    team_spec = meta.get("phase2_team_spec") if isinstance(meta.get("phase2_team_spec"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}

    exec_roles = _dedupe_phase2_roles(team_spec.get("execution_groups"))
    review_roles = _dedupe_phase2_roles(team_spec.get("review_groups"))
    if exec_roles or review_roles:
        return exec_roles, review_roles

    exec_roles = _dedupe_phase2_roles(exec_plan.get("execution_lanes"))
    review_roles = _dedupe_phase2_roles(exec_plan.get("review_lanes"))
    return exec_roles, review_roles


def task_phase2_shape_snapshot(task: Dict[str, Any]) -> Dict[str, List[str]]:
    exec_roles, review_roles = _phase2_shape_roles(task)
    return {
        "execution_roles": list(exec_roles),
        "review_roles": list(review_roles),
    }


def derive_role_execution_snapshot(
    requested_roles: Iterable[str],
    executed_roles: Iterable[str],
    *,
    dedupe_roles: Callable[[Iterable[str]], List[str]],
) -> Dict[str, Any]:
    requested = dedupe_roles(requested_roles or [])
    executed = dedupe_roles(executed_roles or [])
    executed_lookup = {str(role).strip().lower(): str(role).strip() for role in executed if str(role).strip()}
    requested_lookup = {str(role).strip().lower(): str(role).strip() for role in requested if str(role).strip()}
    dropped = [role for role in requested if str(role).strip().lower() not in executed_lookup]
    added = [role for role in executed if str(role).strip().lower() not in requested_lookup]
    return {
        "requested_roles": requested,
        "executed_roles": executed,
        "dropped_roles": dropped,
        "added_roles": added,
        "role_mismatch": bool(dropped or added),
    }


def _execution_lane_catalog(task: Dict[str, Any]) -> List[str]:
    return [row["lane_id"] for row in _execution_lane_rows(task)]


def _execution_lane_rows(task: Dict[str, Any]) -> List[Dict[str, str]]:
    lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
    execution_rows = lane_states.get("execution") if isinstance(lane_states.get("execution"), list) else []
    normalized_rows = [
        {
            "lane_id": str(row.get("lane_id", "")).strip()[:32],
            "role": str(row.get("role", "")).strip()[:64],
        }
        for row in execution_rows
        if isinstance(row, dict) and str(row.get("lane_id", "")).strip()
    ]
    if normalized_rows:
        return normalized_rows
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    execution_lanes = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    return [
        {
            "lane_id": str(row.get("lane_id", "")).strip()[:32],
            "role": str(row.get("role", "")).strip()[:64],
        }
        for row in execution_lanes
        if isinstance(row, dict) and str(row.get("lane_id", "")).strip()
    ]


def _phase2_quality_roles(task: Dict[str, Any]) -> Tuple[str, str]:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    team_spec = meta.get("phase2_team_spec") if isinstance(meta.get("phase2_team_spec"), dict) else {}
    critic_role = str(team_spec.get("critic_role", "")).strip()
    integration_role = str(team_spec.get("integration_role", "")).strip()
    return critic_role, integration_role


def _latest_backend_verdict_event(runtime_events: Any) -> Dict[str, Any]:
    if not isinstance(runtime_events, list):
        return {}
    rows: List[Dict[str, Any]] = []
    for row in runtime_events:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind", "")).strip().lower()
        stage = str(row.get("stage", "")).strip().lower()
        if kind != "verdict" and stage != "verdict.emitted":
            continue
        rows.append(row)
    return rows[-1] if rows else {}


def derive_backend_snapshot(request_data: Dict[str, Any]) -> Dict[str, Any]:
    backend = str(request_data.get("backend", "")).strip()
    profile = str(request_data.get("backend_profile", "")).strip()
    selection_reason = str(request_data.get("backend_selection_reason", "")).strip()
    verdict = str(request_data.get("verdict", "")).strip().lower()
    verdict_event = _latest_backend_verdict_event(request_data.get("runtime_events"))
    payload = verdict_event.get("payload") if isinstance(verdict_event.get("payload"), dict) else {}
    contract_ok = payload.get("contract_ok")
    contract = ""
    if contract_ok is not None:
        contract = "pass" if bool(contract_ok) else "drift"
    note = ""
    replies = request_data.get("replies") if isinstance(request_data.get("replies"), list) else []
    for item in replies:
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip() != "Codex-Reviewer":
            continue
        body = str(item.get("body", "")).strip()
        if not body:
            continue
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- contract gaps:") or stripped.startswith("- contract check:"):
                note = stripped.removeprefix("- ").strip()
                break
        if note:
            break
    return {
        "backend": backend,
        "backend_profile": profile,
        "backend_selection_reason": selection_reason,
        "backend_verdict": verdict,
        "backend_contract": contract,
        "backend_contract_note": note[:240],
    }


def task_lane_summary_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
    lane_summary = lane_states.get("summary") if isinstance(lane_states.get("summary"), dict) else {}
    execution = lane_summary.get("execution") if isinstance(lane_summary.get("execution"), dict) else {}
    review = lane_summary.get("review") if isinstance(lane_summary.get("review"), dict) else {}
    review_verdicts = lane_summary.get("review_verdicts") if isinstance(lane_summary.get("review_verdicts"), dict) else {}

    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    execution_lanes = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    review_lanes = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []

    return {
        "execution_lane_count": len(execution_lanes),
        "review_lane_count": len(review_lanes),
        "execution": dict(execution),
        "review": dict(review),
        "review_verdicts": dict(review_verdicts),
    }


def task_monitor_row_snapshot(
    task: Dict[str, Any],
    request_id: str,
    *,
    normalize_task_status: Callable[[Any], str],
    task_display_label: Callable[[Dict[str, Any], str], str],
) -> Dict[str, Any]:
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    shape = task_phase2_shape_snapshot(task)
    lane = task_lane_summary_snapshot(task)
    observatory = task_team_observatory_snapshot(task)
    return {
        "request_id": str(request_id or "").strip(),
        "label": task_display_label(task, str(request_id or "").strip()),
        "status": normalize_task_status(task.get("status", "pending")),
        "stage": str(task.get("stage", "pending")).strip().lower() or "pending",
        "tf_phase": normalize_tf_phase(derive_tf_phase(task), "queued"),
        "updated_at": str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip(),
        "phase1_role_preset": str(task.get("phase1_role_preset", "")).strip(),
        "phase2_team_preset": str(task.get("phase2_team_preset", "")).strip(),
        "phase2_execution_roles": shape["execution_roles"],
        "phase2_review_roles": shape["review_roles"],
        "execution_lane_count": int(lane.get("execution_lane_count", 0) or 0),
        "review_lane_count": int(lane.get("review_lane_count", 0) or 0),
        "execution_summary": dict(lane.get("execution") or {}),
        "review_summary": dict(lane.get("review") or {}),
        "review_verdicts": dict(lane.get("review_verdicts") or {}),
        "backend": str(task.get("backend", "") or result.get("backend", "")).strip(),
        "backend_profile": str(task.get("backend_profile", "") or result.get("backend_profile", "")).strip(),
        "backend_verdict": str(task.get("backend_verdict", "") or result.get("backend_verdict", "")).strip(),
        "backend_contract": str(task.get("backend_contract", "") or result.get("backend_contract", "")).strip(),
        "backend_contract_note": str(task.get("backend_contract_note", "") or result.get("backend_contract_note", "")).strip(),
        "task_team_observatory": observatory,
        "task_team_observatory_headline": str(observatory.get("headline", "")).strip(),
        "task_team_observatory_first_focus": str(observatory.get("first_focus", "")).strip(),
    }


def _derive_exec_critic_lane_targets(task: Dict[str, Any], critic: Dict[str, Any]) -> Dict[str, List[str]]:
    lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
    review_rows = lane_states.get("review") if isinstance(lane_states.get("review"), list) else []
    if not review_rows:
        return {
            "rerun_execution_lane_ids": [],
            "rerun_review_lane_ids": [],
            "manual_followup_execution_lane_ids": [],
            "manual_followup_review_lane_ids": [],
        }

    explicit_rerun_exec = [str(x).strip()[:32] for x in (critic.get("rerun_execution_lane_ids") or []) if str(x).strip()]
    explicit_rerun_review = [str(x).strip()[:32] for x in (critic.get("rerun_review_lane_ids") or []) if str(x).strip()]
    explicit_manual_exec = [str(x).strip()[:32] for x in (critic.get("manual_followup_execution_lane_ids") or []) if str(x).strip()]
    explicit_manual_review = [str(x).strip()[:32] for x in (critic.get("manual_followup_review_lane_ids") or []) if str(x).strip()]

    review_done_or_failed = [
        row for row in review_rows
        if isinstance(row, dict) and str(row.get("status", "")).strip().lower() in {"done", "failed", "running"}
    ]
    if not review_done_or_failed:
        review_done_or_failed = [row for row in review_rows if isinstance(row, dict)]

    critic_role, integration_role = _phase2_quality_roles(task)
    if critic_role:
        critic_review_rows = [
            row for row in review_done_or_failed
            if isinstance(row, dict) and str(row.get("role", "")).strip() == critic_role
        ]
        if critic_review_rows:
            review_done_or_failed = critic_review_rows

    derived_review_lane_ids = [str(row.get("lane_id", "")).strip()[:32] for row in review_done_or_failed if str(row.get("lane_id", "")).strip()]
    derived_exec_lane_ids: List[str] = []
    for row in review_done_or_failed:
        if not isinstance(row, dict):
            continue
        for lane_id in (row.get("depends_on") or []):
            token = str(lane_id).strip()[:32]
            if token and token not in derived_exec_lane_ids:
                derived_exec_lane_ids.append(token)

    integration_lane_ids: List[str] = []
    if integration_role:
        integration_lane_ids = [
            str(row.get("lane_id", "")).strip()[:32]
            for row in _execution_lane_rows(task)
            if isinstance(row, dict)
            and str(row.get("lane_id", "")).strip()
            and str(row.get("role", "")).strip() == integration_role
        ]
    if integration_lane_ids:
        if derived_exec_lane_ids:
            narrowed = [lane_id for lane_id in derived_exec_lane_ids if lane_id in integration_lane_ids]
            if narrowed:
                derived_exec_lane_ids = narrowed
        else:
            derived_exec_lane_ids = integration_lane_ids
    if not derived_exec_lane_ids:
        derived_exec_lane_ids = _execution_lane_catalog(task)

    return {
        "rerun_execution_lane_ids": explicit_rerun_exec or derived_exec_lane_ids,
        "rerun_review_lane_ids": explicit_rerun_review or derived_review_lane_ids,
        "manual_followup_execution_lane_ids": explicit_manual_exec or derived_exec_lane_ids,
        "manual_followup_review_lane_ids": explicit_manual_review or derived_review_lane_ids,
    }


def apply_review_lane_verdicts(task: Dict[str, Any], critic: Optional[Dict[str, Any]] = None) -> None:
    lane_states = task.get("lane_states")
    if not isinstance(lane_states, dict):
        return
    review_rows = lane_states.get("review")
    if not isinstance(review_rows, list) or not review_rows:
        return

    critic_data = critic if isinstance(critic, dict) else (
        task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    )
    verdict = _normalize_lane_verdict(critic_data.get("verdict"))
    action = str(critic_data.get("action", "")).strip().lower()
    reason = str(critic_data.get("reason", critic_data.get("fix", "")) or "").strip()[:240]

    if not verdict:
        for row in review_rows:
            if not isinstance(row, dict):
                continue
            row.pop("verdict", None)
            row.pop("action", None)
        summary = lane_states.get("summary")
        if isinstance(summary, dict):
            summary.pop("review_verdicts", None)
        return

    applied = False
    for row in review_rows:
        if not isinstance(row, dict):
            continue
        row["verdict"] = verdict
        if action:
            row["action"] = action[:32]
        else:
            row.pop("action", None)
        if reason and str(row.get("status", "")).strip().lower() in {"done", "failed", "running"}:
            row["reason"] = reason
        applied = True

    if applied:
        summary = lane_states.get("summary")
        if not isinstance(summary, dict):
            summary = {}
            lane_states["summary"] = summary
        summary["review_verdicts"] = _lane_verdict_counts(review_rows)


def derive_lane_states(
    task: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
    meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    execution_lanes = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    review_lanes = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []

    if not execution_lanes and not review_lanes:
        return {}

    role_status: Dict[str, str] = {}
    lane_role_status: Dict[Tuple[str, str], str] = {}
    lane_role_rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    status_rank = {"failed": 4, "running": 3, "done": 2, "pending": 1, "waiting_on_dependencies": 1}
    for row in snapshot.get("rows") or []:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role", "")).strip()
        if not role:
            continue
        status = str(row.get("status", "pending")).strip().lower() or "pending"
        role_status[role] = _merge_role_status(role_status.get(role, "pending"), status)
        lane_id = str(row.get("lane_id", "")).strip()
        if lane_id:
            lane_role_status[(lane_id, role)] = _merge_role_status(
                lane_role_status.get((lane_id, role), "pending"),
                status,
            )
            key = (lane_id, role)
            prev = lane_role_rows.get(key)
            if prev is None or status_rank.get(status, 0) >= status_rank.get(str(prev.get("status", "")).strip().lower(), 0):
                lane_role_rows[key] = dict(row)

    complete = bool(snapshot.get("complete", False))
    pending_roles = {str(x).strip() for x in (snapshot.get("pending_roles") or []) if str(x).strip()}
    done_roles = {str(x).strip() for x in (snapshot.get("done_roles") or []) if str(x).strip()}
    failed_roles = {str(x).strip() for x in (snapshot.get("failed_roles") or []) if str(x).strip()}

    def execution_status_for(role: str, lane_id: str = "") -> Tuple[str, str]:
        if lane_id:
            current_lane = lane_role_status.get((lane_id, role), "pending")
            if current_lane == "failed":
                return "failed", "lane role failed"
            if current_lane == "done":
                return "done", ""
            if current_lane == "running":
                return "running", ""
        current = role_status.get(role, "pending")
        if role in failed_roles or current == "failed":
            return "failed", "lane role failed"
        if role in done_roles or current == "done":
            return "done", ""
        if current == "running":
            return "running", ""
        if complete and role in pending_roles:
            return "failed", "request completed before lane finished"
        return "pending", ""

    execution_rows: List[Dict[str, Any]] = []
    execution_status_by_lane: Dict[str, str] = {}
    for row in execution_lanes:
        if not isinstance(row, dict):
            continue
        lane_id = str(row.get("lane_id", "")).strip()
        if not lane_id:
            continue
        role = str(row.get("role", "")).strip() or "Worker"
        status, reason = execution_status_for(role, lane_id)
        item: Dict[str, Any] = {
            "lane_id": lane_id,
            "role": role,
            "status": status,
            "parallel": bool(row.get("parallel", True)),
        }
        lane_meta = lane_role_rows.get((lane_id, role), {})
        for key in ("request_id", "started_at", "last_event_at", "last_event_kind", "backend", "outcome_reason_code"):
            token = str(lane_meta.get(key, "")).strip()
            if token:
                item[key] = token
        try:
            tool_count = int(lane_meta.get("tool_count", 0) or 0)
        except Exception:
            tool_count = 0
        if tool_count > 0:
            item["tool_count"] = tool_count
        touched_files = [str(x).strip() for x in (lane_meta.get("touched_files") or []) if str(x).strip()]
        if touched_files:
            item["touched_files"] = touched_files
        subtask_ids = [str(x).strip() for x in (row.get("subtask_ids") or []) if str(x).strip()]
        if subtask_ids:
            item["subtask_ids"] = subtask_ids
        if reason:
            item["reason"] = reason
        execution_rows.append(item)
        execution_status_by_lane[lane_id] = status

    review_rows_out: List[Dict[str, Any]] = []
    for row in review_lanes:
        if not isinstance(row, dict):
            continue
        lane_id = str(row.get("lane_id", "")).strip()
        if not lane_id:
            continue
        role = str(row.get("role", "")).strip() or "Codex-Reviewer"
        depends = [str(x).strip() for x in (row.get("depends_on") or []) if str(x).strip()]
        waiting_on = [
            lane for lane in depends if execution_status_by_lane.get(lane, "pending") not in {"done"}
        ]
        if waiting_on:
            failed_waiting = [lane for lane in waiting_on if execution_status_by_lane.get(lane) == "failed"]
            reason = (
                "waiting on failed execution lane(s): " + ", ".join(failed_waiting)
                if failed_waiting
                else "waiting on execution lane(s): " + ", ".join(waiting_on)
            )
            status = "waiting_on_dependencies"
        else:
            status, reason = execution_status_for(role, lane_id)
        item = {
            "lane_id": lane_id,
            "role": role,
            "kind": str(row.get("kind", "")).strip() or "verifier",
            "status": status,
            "parallel": bool(row.get("parallel", True)),
        }
        lane_meta = lane_role_rows.get((lane_id, role), {})
        for key in ("request_id", "started_at", "last_event_at", "last_event_kind", "backend", "outcome_reason_code"):
            token = str(lane_meta.get(key, "")).strip()
            if token:
                item[key] = token
        try:
            tool_count = int(lane_meta.get("tool_count", 0) or 0)
        except Exception:
            tool_count = 0
        if tool_count > 0:
            item["tool_count"] = tool_count
        touched_files = [str(x).strip() for x in (lane_meta.get("touched_files") or []) if str(x).strip()]
        if touched_files:
            item["touched_files"] = touched_files
        if depends:
            item["depends_on"] = depends
        if waiting_on:
            item["waiting_on"] = waiting_on
        if reason:
            item["reason"] = reason
        review_rows_out.append(item)

    return {
        "execution": execution_rows,
        "review": review_rows_out,
        "summary": {
            "execution": _lane_state_counts(execution_rows),
            "review": _lane_state_counts(review_rows_out),
        },
    }


def refresh_task_tf_state(task: Dict[str, Any]) -> None:
    task["tf_phase"] = normalize_tf_phase(derive_tf_phase(task), "queued")
    reason = derive_tf_phase_reason(task)
    if reason:
        task["tf_phase_reason"] = reason
    else:
        task.pop("tf_phase_reason", None)


def apply_exec_critic_lifecycle(
    task: Dict[str, Any],
    critic: Dict[str, Any],
    *,
    lifecycle_set_stage: Callable[..., None],
) -> None:
    task["exec_critic"] = dict(critic or {})
    verdict = str((critic or {}).get("verdict", "")).strip().lower()
    action = str((critic or {}).get("action", "")).strip().lower()
    reason = str((critic or {}).get("reason", "")).strip()[:240]
    lane_targets = _derive_exec_critic_lane_targets(task, critic if isinstance(critic, dict) else {})
    if verdict == "retry":
        task["exec_critic"]["rerun_execution_lane_ids"] = list(lane_targets["rerun_execution_lane_ids"])
        task["exec_critic"]["rerun_review_lane_ids"] = list(lane_targets["rerun_review_lane_ids"])
        task["exec_critic"].pop("manual_followup_execution_lane_ids", None)
        task["exec_critic"].pop("manual_followup_review_lane_ids", None)
    elif verdict in {"fail", "intervention"}:
        task["exec_critic"]["manual_followup_execution_lane_ids"] = list(lane_targets["manual_followup_execution_lane_ids"])
        task["exec_critic"]["manual_followup_review_lane_ids"] = list(lane_targets["manual_followup_review_lane_ids"])
        task["exec_critic"].pop("rerun_execution_lane_ids", None)
        task["exec_critic"].pop("rerun_review_lane_ids", None)
    else:
        task["exec_critic"].pop("rerun_execution_lane_ids", None)
        task["exec_critic"].pop("rerun_review_lane_ids", None)
        task["exec_critic"].pop("manual_followup_execution_lane_ids", None)
        task["exec_critic"].pop("manual_followup_review_lane_ids", None)

    if verdict == "success":
        lifecycle_set_stage(task=task, stage="integration", status="done", note="exec critic approved")
        close_state = str(((task.get("stages") or {}).get("close", "pending"))).strip().lower()
        if close_state != "done":
            lifecycle_set_stage(task=task, stage="close", status="running", note="awaiting final result packaging")
    elif verdict == "retry":
        if action == "replan":
            lifecycle_set_stage(task=task, stage="planning", status="running", note=reason or "critic requested replan")
        else:
            lifecycle_set_stage(task=task, stage="execution", status="running", note=reason or "critic requested retry")
        lifecycle_set_stage(task=task, stage="integration", status="running", note=reason or f"critic requested {action or 'retry'}")
        lifecycle_set_stage(task=task, stage="close", status="pending")
    elif verdict in {"fail", "intervention"}:
        lifecycle_set_stage(task=task, stage="integration", status="failed", note=reason or verdict)
        lifecycle_set_stage(task=task, stage="close", status="failed", note=reason or verdict)

    apply_review_lane_verdicts(task, critic)
    followup_brief = build_followup_brief_snapshot(task)
    if followup_brief:
        apply_followup_brief_snapshot(task, followup_brief)
    else:
        for key in (
            "followup_brief_version",
            "followup_brief_status",
            "followup_brief_summary",
            "followup_brief_execution_lane_ids",
            "followup_brief_review_lane_ids",
            "followup_brief_reason",
        ):
            task.pop(key, None)
    refresh_task_tf_state(task)


def sanitize_task_record(
    raw_task: Dict[str, Any],
    req_id: str,
    *,
    dedupe_roles: Callable[[Iterable[str]], List[str]],
    lifecycle_stages: Iterable[str],
    normalize_stage_status: Callable[[Any], str],
    normalize_task_status: Callable[[Any], str],
    now_iso: Callable[[], str],
    history_limit: int,
    normalize_task_plan_schema: Callable[..., Dict[str, Any]],
    normalize_plan_critic_payload: Callable[..., Dict[str, Any]],
    normalize_plan_replans_payload: Callable[..., List[Dict[str, Any]]],
    plan_critic_primary_issue: Callable[..., str],
    normalize_exec_critic_payload: Callable[..., Dict[str, Any]],
    build_task_context: Callable[..., Dict[str, str]],
) -> Dict[str, Any]:
    task = dict(raw_task or {})
    rid = str(req_id or task.get("request_id", "")).strip()
    task["request_id"] = rid
    task["mode"] = str(task.get("mode", "dispatch")).strip().lower() or "dispatch"
    if task["mode"] not in {"dispatch", "direct"}:
        task["mode"] = "dispatch"
    task["prompt"] = str(task.get("prompt", "")).strip()
    task["roles"] = dedupe_roles(task.get("roles") or [])
    task["verifier_roles"] = dedupe_roles(task.get("verifier_roles") or [])
    task["require_verifier"] = bool(task.get("require_verifier", False))

    stage_names = tuple(lifecycle_stages)
    raw_stages = task.get("stages")
    stages: Dict[str, str] = {}
    if isinstance(raw_stages, dict):
        for stage_name in stage_names:
            stages[stage_name] = normalize_stage_status(raw_stages.get(stage_name, "pending"))
    else:
        for stage_name in stage_names:
            stages[stage_name] = "pending"
    task["stages"] = stages

    stage = str(task.get("stage", "")).strip().lower()
    if stage not in stage_names:
        stage = "intake"
        for stage_name in stage_names:
            if stages.get(stage_name) in {"running", "done", "failed"}:
                stage = stage_name
    task["stage"] = stage

    history_in = task.get("history")
    history: List[Dict[str, Any]] = []
    if isinstance(history_in, list):
        for item in history_in[-int(history_limit) :]:
            if not isinstance(item, dict):
                continue
            row_stage = str(item.get("stage", "")).strip().lower()
            if row_stage not in stage_names:
                continue
            row_status = normalize_stage_status(item.get("status", "pending"))
            row: Dict[str, Any] = {
                "at": str(item.get("at", "")).strip() or now_iso(),
                "stage": row_stage,
                "status": row_status,
            }
            note = str(item.get("note", "")).strip()
            if note:
                row["note"] = note[:400]
            history.append(row)
    task["history"] = history

    task["status"] = normalize_task_status(task.get("status", "pending"))
    task["created_at"] = str(task.get("created_at", "")).strip() or now_iso()
    task["updated_at"] = str(task.get("updated_at", "")).strip() or now_iso()
    result = task.get("result")
    task["result"] = result if isinstance(result, dict) else {}

    short_id = str(task.get("short_id", "")).strip().upper()
    alias = str(task.get("alias", "")).strip()
    if short_id:
        task["short_id"] = short_id
    if alias:
        task["alias"] = alias

    control_mode = str(task.get("control_mode", "")).strip().lower()
    if control_mode:
        task["control_mode"] = control_mode[:32]
    source_request_id = str(task.get("source_request_id", "")).strip()
    if source_request_id:
        task["source_request_id"] = source_request_id[:128]
    intent_command = str(task.get("intent_command", "")).strip()
    if intent_command:
        task["intent_command"] = intent_command[:64]
    intent_action = str(task.get("intent_action", "")).strip()
    if intent_action:
        task["intent_action"] = intent_action[:64]
    intent_class = str(task.get("intent_class", "")).strip()
    if intent_class:
        task["intent_class"] = intent_class[:32]
    intent_trace = str(task.get("intent_trace", "")).strip()
    if intent_trace:
        task["intent_trace"] = intent_trace[:400]
    intent_recorded_at = str(task.get("intent_recorded_at", "")).strip()
    if intent_recorded_at:
        task["intent_recorded_at"] = intent_recorded_at[:64]
    retry_of = str(task.get("retry_of", "")).strip()
    if retry_of:
        task["retry_of"] = retry_of[:128]
    replan_of = str(task.get("replan_of", "")).strip()
    if replan_of:
        task["replan_of"] = replan_of[:128]
    followup_of = str(task.get("followup_of", "")).strip()
    if followup_of:
        task["followup_of"] = followup_of[:128]

    for child_key in ("retry_children", "replan_children", "followup_children"):
        raw_children = task.get(child_key)
        if isinstance(raw_children, list):
            normalized_children = []
            seen_children: Set[str] = set()
            for item in raw_children:
                token = str(item or "").strip()
                if not token or token in seen_children:
                    continue
                seen_children.add(token)
                normalized_children.append(token[:128])
            if normalized_children:
                task[child_key] = normalized_children

    initiator_chat_id = str(task.get("initiator_chat_id", "")).strip()
    if initiator_chat_id:
        task["initiator_chat_id"] = initiator_chat_id[:64]
    todo_id = str(task.get("todo_id", "")).strip()
    if todo_id:
        task["todo_id"] = todo_id[:64]

    todo_priority = str(task.get("todo_priority", "")).strip().upper()
    if todo_priority in {"P1", "P2", "P3"}:
        task["todo_priority"] = todo_priority
    todo_status = str(task.get("todo_status", "")).strip().lower()
    if todo_status:
        task["todo_status"] = todo_status[:32]

    request_contract_snapshot = normalize_request_contract_snapshot(
        {
            "version": task.get("request_contract_version"),
            "contract_type": task.get("request_contract_type"),
            "preset": task.get("request_contract_preset"),
            "status": task.get("request_contract_status"),
            "summary": task.get("request_contract_summary"),
            "missing_fields": task.get("request_contract_missing_fields"),
            "required_outputs": task.get("request_contract_required_outputs"),
            "fields": task.get("request_contract_fields"),
            "artifact_contracts": task.get("request_contract_artifact_contracts"),
        }
    )
    if request_contract_snapshot:
        apply_request_contract_snapshot(task, request_contract_snapshot)
    else:
        for key in (
            "request_contract_version",
            "request_contract_type",
            "request_contract_status",
            "request_contract_preset",
            "request_contract_summary",
            "request_contract_missing_fields",
            "request_contract_required_outputs",
            "request_contract_fields",
            "request_contract_artifact_contracts",
        ):
            task.pop(key, None)

    followup_brief_snapshot = normalize_followup_brief_snapshot(
        {
            "version": task.get("followup_brief_version"),
            "status": task.get("followup_brief_status"),
            "summary": task.get("followup_brief_summary"),
            "execution_lane_ids": task.get("followup_brief_execution_lane_ids"),
            "review_lane_ids": task.get("followup_brief_review_lane_ids"),
            "reason": task.get("followup_brief_reason"),
        }
    )
    if not followup_brief_snapshot:
        followup_brief_snapshot = build_followup_brief_snapshot(task)
    if followup_brief_snapshot:
        apply_followup_brief_snapshot(task, followup_brief_snapshot)
    else:
        for key in (
            "followup_brief_version",
            "followup_brief_status",
            "followup_brief_summary",
            "followup_brief_execution_lane_ids",
            "followup_brief_review_lane_ids",
            "followup_brief_reason",
        ):
            task.pop(key, None)

    background_run_snapshot = normalize_background_run_ticket_snapshot(
        {
            "version": task.get("background_run_ticket_version"),
            "ticket_id": task.get("background_run_ticket_id"),
            "status": task.get("background_run_status"),
            "runner_target": task.get("background_run_runner_target"),
            "launch_mode": task.get("background_run_launch_mode"),
            "runtime_handle": task.get("background_run_runtime_handle"),
            "runtime_summary": task.get("background_run_runtime_summary"),
            "worker_result_status": task.get("background_run_worker_result_status"),
            "worker_result_summary": task.get("background_run_worker_result_summary"),
            "worker_gate_status": task.get("background_run_worker_gate_status"),
            "worker_gate_summary": task.get("background_run_worker_gate_summary"),
            "worker_profile_status": task.get("background_run_worker_profile_status"),
            "worker_profile_summary": task.get("background_run_worker_profile_summary"),
            "worker_checklist_status": task.get("background_run_worker_checklist_status"),
            "worker_checklist_summary": task.get("background_run_worker_checklist_summary"),
            "worker_items_summary": task.get("background_run_worker_items_summary"),
            "worker_items": task.get("background_run_worker_items"),
            "worker_item_classes_summary": task.get("background_run_worker_item_classes_summary"),
            "worker_item_classes": task.get("background_run_worker_item_classes"),
            "worker_records_summary": task.get("background_run_worker_records_summary"),
            "worker_records": task.get("background_run_worker_records"),
            "worker_record_rows_summary": task.get("background_run_worker_record_rows_summary"),
            "worker_record_rows": task.get("background_run_worker_record_rows"),
            "worker_record_set_summary": task.get("background_run_worker_record_set_summary"),
            "worker_record_set": task.get("background_run_worker_record_set"),
            "worker_preflight_status": task.get("background_run_worker_preflight_status"),
            "worker_preflight_summary": task.get("background_run_worker_preflight_summary"),
            "worker_preflight_rows_summary": task.get("background_run_worker_preflight_rows_summary"),
            "worker_preflight_rows": task.get("background_run_worker_preflight_rows"),
            "worker_result_actions": task.get("background_run_worker_result_actions"),
            "worker_result_cautions": task.get("background_run_worker_result_cautions"),
            "worker_result_evidence_refs": task.get("background_run_worker_result_evidence_refs"),
            "worker_update_stub_status": task.get("background_run_worker_update_stub_status"),
            "worker_update_stub_summary": task.get("background_run_worker_update_stub_summary"),
            "worker_update_stub_targets": task.get("background_run_worker_update_stub_targets"),
            "created_at": task.get("background_run_created_at"),
            "created_by": task.get("background_run_created_by"),
            "source_surface": task.get("background_run_source_surface"),
            "request_id": task.get("background_run_request_id"),
            "project_key": task.get("background_run_project_key"),
            "execution_brief_status": task.get("background_run_execution_brief_status"),
            "evidence_bundle": task.get("background_run_evidence_bundle"),
            "evidence_artifacts": task.get("background_run_evidence_artifacts"),
            "launch_spec": {
                "version": task.get("background_run_launch_spec_version"),
                "spec_id": task.get("background_run_launch_spec_id"),
                "kind": task.get("background_run_launch_spec_kind"),
                "mode": task.get("background_run_launch_spec_mode"),
                "summary": task.get("background_run_launch_spec_summary"),
                "externalizable": task.get("background_run_launch_spec_externalizable"),
                "provider_task_contract_profile": task.get("background_run_task_contract_profile"),
                "provider_task_contract_summary": task.get("background_run_task_contract_summary"),
                "provider_task_contract_module": task.get("background_run_task_contract_module"),
                "provider_task_contract_module_summary": task.get("background_run_task_contract_module_summary"),
                "provider_task_contract_policy": task.get("background_run_task_contract_policy"),
                "provider_task_contract_policy_summary": task.get("background_run_task_contract_policy_summary"),
                "model_pack_profile": task.get("background_run_model_pack_profile"),
                "model_plan_summary": task.get("background_run_model_plan_summary"),
                "model_worker_route_id": task.get("background_run_model_worker_route_id"),
                "model_judge_route_id": task.get("background_run_model_judge_route_id"),
                "model_escalation_route_id": task.get("background_run_model_escalation_route_id"),
                "model_worker_endpoint_id": task.get("background_run_model_worker_endpoint_id"),
                "model_judge_endpoint_id": task.get("background_run_model_judge_endpoint_id"),
                "model_escalation_endpoint_id": task.get("background_run_model_escalation_endpoint_id"),
                "model_worker_binding_summary": task.get("background_run_model_worker_binding_summary"),
                "model_worker_probe_status": task.get("background_run_model_worker_probe_status"),
                "model_worker_probe_summary": task.get("background_run_model_worker_probe_summary"),
                "model_judge_binding_summary": task.get("background_run_model_judge_binding_summary"),
                "model_judge_probe_status": task.get("background_run_model_judge_probe_status"),
                "model_judge_probe_summary": task.get("background_run_model_judge_probe_summary"),
                "model_escalation_binding_summary": task.get("background_run_model_escalation_binding_summary"),
                "model_escalation_probe_status": task.get("background_run_model_escalation_probe_status"),
                "model_escalation_probe_summary": task.get("background_run_model_escalation_probe_summary"),
            },
        }
    )
    if background_run_snapshot:
        apply_background_run_ticket_snapshot(task, background_run_snapshot)
    else:
        for key in (
            "background_run_ticket_version",
            "background_run_ticket_id",
            "background_run_status",
            "background_run_runner_target",
            "background_run_launch_mode",
            "background_run_runtime_handle",
            "background_run_runtime_summary",
            "background_run_worker_result_status",
            "background_run_worker_result_summary",
            "background_run_worker_gate_status",
            "background_run_worker_gate_summary",
            "background_run_worker_profile_status",
            "background_run_worker_profile_summary",
            "background_run_worker_checklist_status",
            "background_run_worker_checklist_summary",
            "background_run_worker_items_summary",
            "background_run_worker_items",
            "background_run_worker_item_classes_summary",
            "background_run_worker_item_classes",
            "background_run_worker_records_summary",
            "background_run_worker_records",
            "background_run_worker_record_rows_summary",
            "background_run_worker_record_rows",
            "background_run_worker_record_set_summary",
            "background_run_worker_record_set",
            "background_run_worker_preflight_status",
            "background_run_worker_preflight_summary",
            "background_run_worker_preflight_rows_summary",
            "background_run_worker_preflight_rows",
            "background_run_worker_result_actions",
            "background_run_worker_result_cautions",
            "background_run_worker_result_evidence_refs",
            "background_run_worker_update_stub_status",
            "background_run_worker_update_stub_summary",
            "background_run_worker_update_stub_targets",
            "background_run_created_at",
            "background_run_created_by",
            "background_run_source_surface",
            "background_run_request_id",
            "background_run_project_key",
            "background_run_execution_brief_status",
            "background_run_evidence_bundle",
            "background_run_evidence_artifacts",
            "background_run_launch_spec_version",
            "background_run_launch_spec_id",
            "background_run_launch_spec_kind",
            "background_run_launch_spec_mode",
            "background_run_launch_spec_summary",
            "background_run_launch_spec_externalizable",
            "background_run_task_contract_profile",
            "background_run_task_contract_summary",
            "background_run_task_contract_module",
            "background_run_task_contract_module_summary",
            "background_run_task_contract_policy",
            "background_run_task_contract_policy_summary",
            "background_run_model_pack_profile",
            "background_run_model_plan_summary",
            "background_run_model_worker_route_id",
            "background_run_model_judge_route_id",
            "background_run_model_escalation_route_id",
            "background_run_model_worker_endpoint_id",
            "background_run_model_judge_endpoint_id",
            "background_run_model_escalation_endpoint_id",
            "background_run_model_worker_binding_summary",
            "background_run_model_worker_probe_status",
            "background_run_model_worker_probe_summary",
            "background_run_model_judge_binding_summary",
            "background_run_model_judge_probe_status",
            "background_run_model_judge_probe_summary",
            "background_run_model_escalation_binding_summary",
            "background_run_model_escalation_probe_status",
            "background_run_model_escalation_probe_summary",
        ):
            task.pop(key, None)

    background_run_external_snapshot = derive_background_run_external_snapshot(task)
    if background_run_external_snapshot:
        task["background_run_external_phase"] = background_run_external_snapshot.get("phase", "")
        task["background_run_external_note"] = background_run_external_snapshot.get("note", "")
    else:
        task.pop("background_run_external_phase", None)
        task.pop("background_run_external_note", None)

    reentry_rails_summary = build_reentry_rails_summary(task)
    if reentry_rails_summary:
        task["reentry_rails_summary"] = reentry_rails_summary
    else:
        task.pop("reentry_rails_summary", None)

    plan = task.get("plan")
    if isinstance(plan, dict):
        workers = []
        raw_meta = plan.get("meta")
        if isinstance(raw_meta, dict) and isinstance(raw_meta.get("worker_roles"), list):
            for row in raw_meta.get("worker_roles") or []:
                token = str(row or "").strip()
                if token and token not in workers:
                    workers.append(token)
        if not workers:
            workers = dedupe_roles((task.get("plan_roles") or []) + (task.get("roles") or [])) or ["Worker"]
        max_subtasks = 0
        raw_subtasks = plan.get("subtasks")
        if isinstance(raw_subtasks, list):
            max_subtasks = len(raw_subtasks)
        task["plan"] = normalize_task_plan_schema(
            plan,
            user_prompt=str(task.get("prompt", "")).strip(),
            workers=workers,
            max_subtasks=max_subtasks or 4,
        )
    plan_critic = task.get("plan_critic")
    if isinstance(plan_critic, dict):
        task["plan_critic"] = normalize_plan_critic_payload(plan_critic, max_items=8)
    plan_roles = task.get("plan_roles")
    if isinstance(plan_roles, list):
        task["plan_roles"] = dedupe_roles(plan_roles)
    plan_replans = task.get("plan_replans")
    if isinstance(plan_replans, list):
        task["plan_replans"] = normalize_plan_replans_payload(plan_replans, keep=history_limit)
    try:
        plan_review_count = max(0, int(task.get("plan_review_count", 0) or 0))
    except Exception:
        plan_review_count = 0
    if plan_review_count > 0:
        task["plan_review_count"] = plan_review_count
    else:
        task.pop("plan_review_count", None)
    plan_issue_history = _normalize_plan_issue_history(task.get("plan_issue_history"), keep=history_limit)
    if plan_issue_history:
        task["plan_issue_history"] = plan_issue_history
    else:
        task.pop("plan_issue_history", None)
    plan_issue_codes = _normalize_plan_issue_codes(task.get("plan_issue_codes"))
    if not plan_issue_codes and plan_issue_history:
        merged_codes: List[str] = []
        for row in plan_issue_history:
            for code in list(row.get("issue_codes") or []):
                token = str(code or "").strip().lower()
                if token and token not in merged_codes:
                    merged_codes.append(token[:64])
        plan_issue_codes = merged_codes[:12]
    if plan_issue_codes:
        task["plan_issue_codes"] = plan_issue_codes
    else:
        task.pop("plan_issue_codes", None)
    convergence_status = _normalize_plan_convergence_status(task.get("plan_convergence_status"))
    if convergence_status:
        task["plan_convergence_status"] = convergence_status
    else:
        task.pop("plan_convergence_status", None)
    plan_stalled_reason = str(task.get("plan_stalled_reason", "")).strip()
    if plan_stalled_reason:
        task["plan_stalled_reason"] = plan_stalled_reason[:240]
    else:
        task.pop("plan_stalled_reason", None)
    try:
        plan_last_round = max(0, int(task.get("plan_last_round", 0) or 0))
    except Exception:
        plan_last_round = 0
    if plan_last_round > 0:
        task["plan_last_round"] = plan_last_round
    else:
        task.pop("plan_last_round", None)
    if isinstance(task.get("plan_gate_passed"), bool):
        task["plan_gate_passed"] = bool(task.get("plan_gate_passed"))
    plan_gate_reason = str(task.get("plan_gate_reason", "")).strip()
    if plan_gate_reason:
        task["plan_gate_reason"] = plan_gate_reason[:240]
    elif task.get("plan_gate_passed") is False and isinstance(task.get("plan_critic"), dict):
        lead_issue = plan_critic_primary_issue(task["plan_critic"], limit=240)
        if lead_issue:
            task["plan_gate_reason"] = lead_issue

    exec_critic = task.get("exec_critic")
    if isinstance(exec_critic, dict):
        task["exec_critic"] = normalize_exec_critic_payload(
            exec_critic,
            attempt_no=int(exec_critic.get("attempt", 1) or 1),
            max_attempts=int(exec_critic.get("max_attempts", 1) or 1),
            at=str(exec_critic.get("at", "")).strip() or now_iso(),
        )

    lane_states = task.get("lane_states")
    if isinstance(lane_states, dict):
        execution_rows = _normalize_lane_state_rows(lane_states.get("execution"), kind="execution")
        review_rows = _normalize_lane_state_rows(lane_states.get("review"), kind="review")
        if execution_rows or review_rows:
            task["lane_states"] = {
                "execution": execution_rows,
                "review": review_rows,
                "summary": {
                    "execution": _lane_state_counts(execution_rows),
                    "review": _lane_state_counts(review_rows),
                },
            }
            apply_review_lane_verdicts(task)
        else:
            task.pop("lane_states", None)

    context = build_task_context(
        request_id=rid,
        task=task,
        extra=(task.get("context") if isinstance(task.get("context"), dict) else None),
    )
    if context:
        task["context"] = context

    result = task.get("result")
    if isinstance(result, dict):
        role_snapshot = derive_role_execution_snapshot(
            result.get("requested_roles") or task.get("roles") or [],
            result.get("executed_roles") or result.get("done_roles") or task.get("roles") or [],
            dedupe_roles=dedupe_roles,
        )
        result.update(role_snapshot)
        if str(task.get("job_contract_summary", "")).strip():
            result["job_contract_summary"] = str(task.get("job_contract_summary", "")).strip()
        if str(task.get("execution_brief_summary", "")).strip():
            result["execution_brief_summary"] = str(task.get("execution_brief_summary", "")).strip()

    refresh_task_planning_primitives(task, request_contract=request_contract_snapshot)

    refresh_task_tf_state(task)

    return task


def ensure_project_tasks(entry: Dict[str, Any]) -> Dict[str, Any]:
    tasks = entry.get("tasks")
    if not isinstance(tasks, dict):
        tasks = {}
        entry["tasks"] = tasks
    return tasks


def normalize_task_alias_key(raw: str) -> str:
    src = str(raw or "").strip().lower()
    out: List[str] = []
    sep = False
    for ch in src:
        if ch.isalnum():
            out.append(ch)
            sep = False
        else:
            if not sep:
                out.append("-")
                sep = True
    return "".join(out).strip("-")


def parse_task_seq_from_short_id(short_id: str) -> int:
    src = str(short_id or "").strip().upper()
    if not src.startswith("T-"):
        return 0
    tail = src[2:]
    return int(tail) if tail.isdigit() else 0


def format_task_short_id(seq: int) -> str:
    value = max(1, int(seq))
    return f"T-{value:03d}" if value < 1000 else f"T-{value}"


def derive_task_alias_base(prompt: str) -> str:
    src = str(prompt or "").strip()
    if not src:
        return "task"

    cleaned: List[str] = []
    for ch in src:
        if ch.isalnum() or ch in {" ", "-", "_"}:
            cleaned.append(ch)
        else:
            cleaned.append(" ")

    tokens = [t.lower() for t in "".join(cleaned).split() if t]
    if not tokens:
        return "task"

    stop = {
        "the",
        "a",
        "an",
        "to",
        "for",
        "and",
        "or",
        "of",
        "해주세요",
        "해줘",
        "요청",
        "작업",
        "진행",
        "지금",
        "바로",
        "좀",
    }
    picked = [t for t in tokens if t not in stop] or tokens

    alias = "-".join(picked[:5]).strip("-_")
    if len(alias) > 48:
        alias = alias[:48].rstrip("-_")
    return alias or "task"


def ensure_task_alias_meta(entry: Dict[str, Any]) -> Tuple[Dict[str, str], int]:
    raw_index = entry.get("task_alias_index")
    if not isinstance(raw_index, dict):
        raw_index = {}
        entry["task_alias_index"] = raw_index

    alias_index: Dict[str, str] = {}
    for key, rid in raw_index.items():
        key_norm = normalize_task_alias_key(str(key or ""))
        rid_norm = str(rid or "").strip()
        if key_norm and rid_norm:
            alias_index[key_norm] = rid_norm
    entry["task_alias_index"] = alias_index

    raw_seq = entry.get("task_seq")
    try:
        seq = max(0, int(raw_seq or 0))
    except Exception:
        seq = 0
    entry["task_seq"] = seq
    return alias_index, seq


def rebuild_task_alias_index(entry: Dict[str, Any]) -> None:
    tasks = ensure_project_tasks(entry)
    _, seq = ensure_task_alias_meta(entry)

    alias_index: Dict[str, str] = {}
    max_seq = max(0, int(seq))

    for req_id, task in tasks.items():
        rid = str(req_id or "").strip()
        if not rid or not isinstance(task, dict):
            continue

        short_id = str(task.get("short_id", "")).strip().upper()
        alias = str(task.get("alias", "")).strip()

        if short_id:
            alias_index[normalize_task_alias_key(short_id)] = rid
            max_seq = max(max_seq, parse_task_seq_from_short_id(short_id))
        if alias:
            alias_index[normalize_task_alias_key(alias)] = rid

    entry["task_alias_index"] = alias_index
    entry["task_seq"] = max_seq


def assign_task_alias(
    entry: Dict[str, Any],
    task: Dict[str, Any],
    prompt: str,
    *,
    rebuild_index: bool = True,
) -> None:
    alias_index, seq = ensure_task_alias_meta(entry)

    req_id = str(task.get("request_id", "")).strip()
    if not req_id:
        return

    short_id = str(task.get("short_id", "")).strip().upper()
    if not short_id:
        next_seq = max(seq, 0)
        while True:
            next_seq += 1
            candidate = format_task_short_id(next_seq)
            key = normalize_task_alias_key(candidate)
            owner = alias_index.get(key)
            if not owner or owner == req_id:
                short_id = candidate
                task["short_id"] = short_id
                entry["task_seq"] = next_seq
                break

    alias = str(task.get("alias", "")).strip()
    if not alias:
        base = derive_task_alias_base(prompt or str(task.get("prompt", "")).strip() or short_id.lower())
        candidate = base
        suffix = 2
        while True:
            key = normalize_task_alias_key(candidate)
            owner = alias_index.get(key)
            if not owner or owner == req_id:
                alias = candidate
                task["alias"] = alias
                break
            candidate = f"{base}-{suffix}"
            suffix += 1

    if rebuild_index:
        rebuild_task_alias_index(entry)


def backfill_task_aliases(entry: Dict[str, Any]) -> None:
    tasks = ensure_project_tasks(entry)
    if not tasks:
        ensure_task_alias_meta(entry)
        return

    rows = sorted(tasks.items(), key=lambda kv: str((kv[1] or {}).get("created_at", "")))
    for req_id, task in rows:
        if not isinstance(task, dict):
            continue
        rid = str(req_id or "").strip()
        if not rid:
            continue
        if not str(task.get("request_id", "")).strip():
            task["request_id"] = rid
        assign_task_alias(entry, task, prompt=str(task.get("prompt", "")), rebuild_index=False)

    rebuild_task_alias_index(entry)


def resolve_task_request_id(entry: Dict[str, Any], request_or_alias: str) -> str:
    token = str(request_or_alias or "").strip()
    if not token:
        return ""

    tasks = ensure_project_tasks(entry)
    if token in tasks:
        return token

    alias_index, _ = ensure_task_alias_meta(entry)
    if not alias_index and tasks:
        backfill_task_aliases(entry)
        alias_index, _ = ensure_task_alias_meta(entry)

    norm = normalize_task_alias_key(token)
    mapped = alias_index.get(norm, "")
    if mapped and mapped in tasks:
        return mapped

    for rid, task in tasks.items():
        if not isinstance(task, dict):
            continue
        short_id = str(task.get("short_id", "")).strip().upper()
        alias = str(task.get("alias", "")).strip()
        if token.upper() == short_id:
            return rid
        if norm and norm == normalize_task_alias_key(alias):
            return rid

    return token


def latest_task_request_refs(entry: Dict[str, Any], limit: int = 12) -> List[str]:
    tasks = ensure_project_tasks(entry)
    if not tasks:
        return []
    backfill_task_aliases(entry)
    rows = sorted(tasks.items(), key=lambda kv: str((kv[1] or {}).get("updated_at", "")), reverse=True)
    cap = max(1, min(50, int(limit)))
    out: List[str] = []
    for req_id, task in rows[:cap]:
        if isinstance(task, dict):
            rid = str(req_id or "").strip()
            if rid:
                out.append(rid)
    return out


def trim_project_tasks(tasks: Dict[str, Any], keep: int) -> None:
    if len(tasks) <= int(keep):
        return
    ordered = sorted(tasks.items(), key=lambda kv: str((kv[1] or {}).get("updated_at", "")), reverse=True)
    keep_keys = {key for key, _ in ordered[: max(1, int(keep))]}
    for key in list(tasks.keys()):
        if key not in keep_keys:
            tasks.pop(key, None)


def get_task_record(entry: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
    token = resolve_task_request_id(entry, request_id)
    if not token:
        return None
    tasks = ensure_project_tasks(entry)
    item = tasks.get(token)
    return item if isinstance(item, dict) else None


def ensure_task_record(
    entry: Dict[str, Any],
    *,
    request_id: str,
    prompt: str,
    mode: str,
    roles: List[str],
    verifier_roles: List[str],
    require_verifier: bool,
    now_iso: Callable[[], str],
    dedupe_roles: Callable[[Iterable[str]], List[str]],
    build_task_context: Callable[..., Dict[str, str]],
    lifecycle_stages: Iterable[str],
    keep_limit: int,
    intent_command: str = "",
    intent_action: str = "",
    intent_class: str = "",
    intent_trace: str = "",
    request_contract: Optional[Dict[str, Any]] = None,
    execution_brief: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    token = str(request_id or "").strip()
    tasks = ensure_project_tasks(entry)
    now = now_iso()

    item = tasks.get(token)
    if not isinstance(item, dict):
        item = {
            "request_id": token,
            "mode": mode,
            "prompt": prompt.strip(),
            "roles": dedupe_roles(roles),
            "verifier_roles": dedupe_roles(verifier_roles),
            "require_verifier": bool(require_verifier),
            "status": "running",
            "stage": "intake",
            "stages": {name: "pending" for name in lifecycle_stages},
            "history": [],
            "created_at": now,
            "updated_at": now,
            "result": {},
        }
        tasks[token] = item
    else:
        if prompt:
            item["prompt"] = prompt.strip()
        if mode:
            item["mode"] = mode
        if roles:
            item["roles"] = dedupe_roles(roles)
        if verifier_roles:
            item["verifier_roles"] = dedupe_roles(verifier_roles)
        item["require_verifier"] = bool(require_verifier)
        item["updated_at"] = now

    next_intent_command = str(intent_command or "").strip()
    next_intent_action = str(intent_action or "").strip()
    next_intent_class = str(intent_class or "").strip()
    next_intent_trace = str(intent_trace or "").strip()
    if next_intent_command:
        item["intent_command"] = next_intent_command
    if next_intent_action:
        item["intent_action"] = next_intent_action
    if next_intent_class:
        item["intent_class"] = next_intent_class
    if next_intent_trace:
        item["intent_trace"] = next_intent_trace[:400]
    if next_intent_command or next_intent_action or next_intent_class or next_intent_trace:
        item["intent_recorded_at"] = now

    request_contract_snapshot = normalize_request_contract_snapshot(
        request_contract
        if isinstance(request_contract, dict)
        else {
            "version": item.get("request_contract_version"),
            "contract_type": item.get("request_contract_type"),
            "preset": item.get("request_contract_preset"),
            "status": item.get("request_contract_status"),
            "summary": item.get("request_contract_summary"),
            "missing_fields": item.get("request_contract_missing_fields"),
            "required_outputs": item.get("request_contract_required_outputs"),
            "fields": item.get("request_contract_fields"),
            "artifact_contracts": item.get("request_contract_artifact_contracts"),
        }
    )
    if (
        not request_contract_snapshot
        and str(item.get("mode", mode or "")).strip().lower() == "dispatch"
        and str(item.get("prompt", prompt or "")).strip()
    ):
        request_contract_snapshot = build_request_contract(
            source_prompt=str(item.get("prompt", prompt or "")).strip(),
            selected_roles=list(item.get("roles") or roles or []),
        )
    if request_contract_snapshot or isinstance(execution_brief, dict) or str(item.get("mode", mode or "")).strip().lower() == "dispatch":
        refresh_task_planning_primitives(
            item,
            request_contract=request_contract_snapshot,
            execution_brief=execution_brief if isinstance(execution_brief, dict) else None,
        )

    assign_task_alias(entry, item, prompt=prompt, rebuild_index=False)
    item["context"] = build_task_context(request_id=token, entry=entry, task=item)
    trim_project_tasks(tasks, keep=keep_limit)
    rebuild_task_alias_index(entry)
    return item


def lifecycle_set_stage(
    task: Dict[str, Any],
    *,
    stage: str,
    status: str,
    note: str = "",
    lifecycle_stages: Iterable[str],
    normalize_stage_status: Callable[[Any], str],
    now_iso: Callable[[], str],
    history_limit: int,
) -> None:
    stage_names = tuple(lifecycle_stages)
    if stage not in stage_names:
        return

    stages = task.get("stages")
    if not isinstance(stages, dict):
        stages = {name: "pending" for name in stage_names}
        task["stages"] = stages

    prev = str(stages.get(stage, "pending"))
    next_status = normalize_stage_status(status or "pending")
    if prev == next_status and not note:
        return

    stages[stage] = next_status
    task["stage"] = stage

    history = task.get("history")
    if not isinstance(history, list):
        history = []

    event: Dict[str, Any] = {"at": now_iso(), "stage": stage, "status": next_status}
    if note:
        event["note"] = note
    history.append(event)
    if len(history) > int(history_limit):
        history = history[-int(history_limit) :]

    task["history"] = history
    task["updated_at"] = event["at"]


def summarize_task_monitor(
    project_name: str,
    entry: Dict[str, Any],
    *,
    limit: int,
    normalize_task_status: Callable[[Any], str],
    dedupe_roles: Callable[[Iterable[str]], List[str]],
    task_display_label: Callable[[Dict[str, Any], str], str],
    lifecycle_stages: Iterable[str],
) -> str:
    tasks = ensure_project_tasks(entry)
    if not tasks:
        return f"runtime: {project_name}\n작업이 없습니다."

    backfill_task_aliases(entry)
    rows = sorted(tasks.items(), key=lambda kv: str((kv[1] or {}).get("updated_at", "")), reverse=True)
    cap = max(1, min(50, int(limit)))
    stage_names = tuple(lifecycle_stages)

    counts = {"pending": 0, "running": 0, "completed": 0, "failed": 0}
    invalid_stage_rows = 0
    for _, task in rows:
        if not isinstance(task, dict):
            continue
        status = normalize_task_status(task.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
        stage = str(task.get("stage", "")).strip().lower()
        if stage and stage not in stage_names:
            invalid_stage_rows += 1

    lines = [
        f"runtime: {project_name}",
        f"task monitor: latest {cap}",
        "format: label | status/stage | roles | updated",
        "summary: total={total} running={running} completed={completed} failed={failed} pending={pending}".format(
            total=len(rows),
            running=counts.get("running", 0),
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            pending=counts.get("pending", 0),
        ),
    ]
    if invalid_stage_rows:
        lines.append(f"warning: invalid lifecycle stage rows={invalid_stage_rows}")
    latest_intent = runtime_latest_intent_summary(entry)
    latest_action = load_latest_action_audit_for_runtime(
        entry.get("team_dir"),
        project_alias=entry.get("project_alias", ""),
        request_ids=[str(req_id or "").strip() for req_id, _task in rows if str(req_id or "").strip()],
    )
    append_operator_status_summary_lines(
        lines,
        latest_intent=latest_intent,
        latest_action=latest_action,
    )

    def _phase2_request_count(value: Any) -> int:
        if isinstance(value, list):
            return len([str(item).strip() for item in value if str(item).strip()])
        if isinstance(value, str):
            return 1 if value.strip() else 0
        return 0

    for idx, (req_id, task) in enumerate(rows[:cap], start=1):
        if not isinstance(task, dict):
            continue
        label = task_display_label(task, str(req_id or "").strip())
        status = normalize_task_status(task.get("status", "pending"))
        stage = str(task.get("stage", "pending")).strip().lower() or "pending"
        if stage not in stage_names:
            stage = "pending"
        tf_phase = normalize_tf_phase(derive_tf_phase(task), "queued")
        roles = dedupe_roles(task.get("roles") or [])
        role_text = ", ".join(roles[:2])
        if len(roles) > 2:
            role_text += f" +{len(roles) - 2}"
        plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
        meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
        exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
        exec_lanes = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
        review_lanes = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []
        shape_exec_roles, shape_review_roles = _phase2_shape_roles(task)
        lane_text = ""
        if exec_lanes or review_lanes:
            lane_text = f" | lanes E{len(exec_lanes)}/R{len(review_lanes)}"
        lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
        lane_summary = lane_states.get("summary") if isinstance(lane_states.get("summary"), dict) else {}
        exec_summary = lane_summary.get("execution") if isinstance(lane_summary.get("execution"), dict) else {}
        review_summary = lane_summary.get("review") if isinstance(lane_summary.get("review"), dict) else {}
        review_verdicts = lane_summary.get("review_verdicts") if isinstance(lane_summary.get("review_verdicts"), dict) else {}
        lane_parts: List[str] = []
        phase1_parts: List[str] = []
        phase1_mode = str(task.get("phase1_mode", "")).strip()
        phase1_rounds = max(0, int(task.get("phase1_rounds", 0) or 0))
        phase1_providers = dedupe_roles(task.get("phase1_providers") or [])
        phase1_current_phase = str(task.get("phase1_current_phase", "")).strip()
        phase1_current_round = max(0, int(task.get("phase1_current_round", 0) or 0))
        phase1_current_total = max(0, int(task.get("phase1_current_total_rounds", 0) or 0))
        phase1_current_provider = str(task.get("phase1_current_provider", "")).strip()
        phase1_current_planner = str(task.get("phase1_current_planner", "")).strip()
        phase1_current_critic = str(task.get("phase1_current_critic", "")).strip()
        phase1_role_preset = str(task.get("phase1_role_preset", "")).strip()
        if tf_phase == "planning" and (phase1_mode or phase1_rounds or phase1_providers):
            phase1_token = "phase1 {mode} {rounds}".format(
                mode=phase1_mode or "single",
                rounds=(
                    f"{phase1_current_round}/{phase1_current_total}"
                    if phase1_current_round and phase1_current_total
                    else str(phase1_rounds or 1)
                ),
            )
            phase1_parts.append(phase1_token)
            if phase1_providers:
                phase1_parts.append("providers=" + ",".join(phase1_providers))
            current_actor = phase1_current_provider or phase1_current_planner or phase1_current_critic
            if current_actor:
                phase1_parts.append("now=" + current_actor)
            if phase1_current_phase:
                phase1_parts.append("step=" + phase1_current_phase)
            if phase1_role_preset:
                phase1_parts.append("preset=" + phase1_role_preset)
        if exec_summary:
            lane_parts.append("exec " + ",".join(f"{key}={value}" for key, value in sorted(exec_summary.items())))
        if review_summary:
            lane_parts.append("review " + ",".join(f"{key}={value}" for key, value in sorted(review_summary.items())))
        if review_verdicts:
            lane_parts.append("review_verdict " + ",".join(f"{key}={value}" for key, value in sorted(review_verdicts.items())))
        if shape_exec_roles or shape_review_roles:
            lane_parts.append(
                "shape E:{exec_roles} R:{review_roles}".format(
                    exec_roles=",".join(shape_exec_roles) if shape_exec_roles else "-",
                    review_roles=",".join(shape_review_roles) if shape_review_roles else "-",
                )
            )
        lane_targets = task_lane_target_snapshot(task)
        rerun_exec = list(lane_targets.get("rerun_execution_lane_ids") or [])
        rerun_review = list(lane_targets.get("rerun_review_lane_ids") or [])
        manual_exec = list(lane_targets.get("manual_followup_execution_lane_ids") or [])
        manual_review = list(lane_targets.get("manual_followup_review_lane_ids") or [])
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        phase2_request_ids = result.get("phase2_request_ids") if isinstance(result.get("phase2_request_ids"), dict) else {}
        linked_request_ids = result.get("linked_request_ids") if isinstance(result.get("linked_request_ids"), list) else []
        exec_request_count = _phase2_request_count(phase2_request_ids.get("execution"))
        review_request_count = _phase2_request_count(phase2_request_ids.get("review"))
        linked_request_count = len([str(item).strip() for item in linked_request_ids if str(item).strip()])
        dropped_roles = [str(x).strip() for x in (result.get("dropped_roles") or []) if str(x).strip()]
        added_roles = [str(x).strip() for x in (result.get("added_roles") or []) if str(x).strip()]
        degraded_by = [str(x).strip() for x in (result.get("degraded_by") or []) if str(x).strip()]
        rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
        backend = str(result.get("backend", "") or task.get("backend", "")).strip()
        backend_profile = str(result.get("backend_profile", "") or task.get("backend_profile", "")).strip()
        backend_verdict = str(result.get("backend_verdict", "") or task.get("backend_verdict", "")).strip()
        backend_contract = str(result.get("backend_contract", "") or task.get("backend_contract", "")).strip()
        if exec_request_count or review_request_count or linked_request_count or bool(result.get("phase2_parallelized", False)):
            request_parts = [f"reqs E{exec_request_count}/R{review_request_count}"]
            if linked_request_count:
                request_parts.append(f"linked={linked_request_count}")
            if bool(result.get("phase2_parallelized", False)):
                request_parts.append("parallel=yes")
            lane_parts.append(" ".join(request_parts))
        if backend:
            backend_parts = [backend]
            if backend_profile:
                backend_parts.append(backend_profile)
            if backend_verdict:
                backend_parts.append(backend_verdict)
            if backend_contract:
                backend_parts.append(backend_contract)
            lane_parts.append("backend " + "/".join(backend_parts))
        if lane_parts:
            lane_text += " [" + " | ".join(lane_parts) + "]"
        if phase1_parts:
            lane_text += " <" + " ".join(phase1_parts) + ">"
        target_parts: List[str] = []
        if rerun_exec or rerun_review:
            target_parts.append(
                "rerun E:{exec_ids} R:{review_ids}".format(
                    exec_ids=",".join(rerun_exec) if rerun_exec else "-",
                    review_ids=",".join(rerun_review) if rerun_review else "-",
                )
            )
        if manual_exec or manual_review:
            target_parts.append(
                "followup E:{exec_ids} R:{review_ids}".format(
                    exec_ids=",".join(manual_exec) if manual_exec else "-",
                    review_ids=",".join(manual_review) if manual_review else "-",
                )
            )
        if bool(result.get("role_mismatch", False)):
            target_parts.append(
                "roles drop:{dropped} add:{added}".format(
                    dropped=",".join(dropped_roles) if dropped_roles else "-",
                    added=",".join(added_roles) if added_roles else "-",
                )
            )
        if degraded_by:
            target_parts.append("degraded=" + ",".join(degraded_by))
        if tf_phase == "rate_limited" and rate_limit:
            providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
            retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
            retry_at = str(rate_limit.get("retry_at", "")).strip()
            target_parts.append(
                "rate_limit {providers} {retry} {retry_at}".format(
                    providers="providers=" + ",".join(providers) if providers else "providers=-",
                    retry=(f"retry={retry_after}s" if retry_after > 0 else "retry=-"),
                    retry_at=(f"retry_at={retry_at}" if retry_at else "retry_at=-"),
                )
            )
        if target_parts:
            lane_text += " {" + " | ".join(target_parts) + "}"
        updated = str(task.get("updated_at", "")).strip() or "-"
        lines.append(f"- {idx}. {label} | {status}/{stage}/{tf_phase} | {role_text or '-'}{lane_text} | {updated}")
        priority_action = task_priority_action_snapshot(
            label=label,
            tf_phase=tf_phase,
            rerun_execution_lane_ids=rerun_exec,
            rerun_review_lane_ids=rerun_review,
            manual_followup_execution_lane_ids=manual_exec,
            manual_followup_review_lane_ids=manual_review,
            rate_limit=rate_limit,
        )
        first_action = str(priority_action.get("action", "")).strip()
        if first_action:
            lines.append(f"  first: {first_action} | {str(priority_action.get('reason', '')).strip() or '-'}")
        observatory = task_team_observatory_snapshot(task)
        observatory_line = observatory_monitor_line(observatory)
        if observatory_line:
            lines.append(f"  {observatory_line}")

    lines.append("")
    lines.append("alias map (number/label -> request_id):")
    for idx, (req_id, task) in enumerate(rows[:cap], start=1):
        if not isinstance(task, dict):
            continue
        lines.append(f"- {idx}. {task_display_label(task, str(req_id or '').strip())} -> {req_id}")
    lines.append("")
    lines.append(
        "quick actions: /check <번호|label> /task <번호|label> "
        "/retry <번호|label> [lane <L#|R#>] /replan <번호|label> [lane <L#|R#>] /cancel <번호|label>"
    )
    return "\n".join(lines)


def normalize_role_rows(data: Dict[str, Any], *, dedupe_roles: Callable[[Iterable[str]], List[str]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def _copy_observability_fields(target: Dict[str, Any], source: Dict[str, Any]) -> None:
        for key in (
            "request_id",
            "started_at",
            "last_event_at",
            "last_event_kind",
            "backend",
            "outcome_reason_code",
        ):
            token = str(source.get(key, "")).strip()
            if token:
                target[key] = token
        try:
            tool_count = int(source.get("tool_count", 0) or 0)
        except Exception:
            tool_count = 0
        if tool_count > 0:
            target["tool_count"] = tool_count
        touched_files = [str(item).strip() for item in (source.get("touched_files") or []) if str(item).strip()]
        if touched_files:
            target["touched_files"] = touched_files

    role_states = data.get("role_states")
    if isinstance(role_states, list):
        for item in role_states:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if not role:
                continue
            status = str(item.get("status", "pending")).strip().lower() or "pending"
            row = {"role": role, "status": status}
            lane_id = str(item.get("lane_id", "")).strip()
            if lane_id:
                row["lane_id"] = lane_id
            phase2_stage = str(item.get("phase2_stage", "")).strip().lower()
            if phase2_stage:
                row["phase2_stage"] = phase2_stage
            _copy_observability_fields(row, item)
            rows.append(row)

    if rows:
        return rows

    roles_obj = data.get("roles")
    if isinstance(roles_obj, list) and roles_obj and isinstance(roles_obj[0], dict):
        for item in roles_obj:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            if not role:
                continue
            status = str(item.get("status", "pending")).strip().lower() or "pending"
            row = {"role": role, "status": status}
            lane_id = str(item.get("lane_id", "")).strip()
            if lane_id:
                row["lane_id"] = lane_id
            phase2_stage = str(item.get("phase2_stage", "")).strip().lower()
            if phase2_stage:
                row["phase2_stage"] = phase2_stage
            _copy_observability_fields(row, item)
            rows.append(row)
        if rows:
            return rows

    done_set = {str(x).strip() for x in (data.get("done_roles") or []) if str(x).strip()}
    failed_set = {str(x).strip() for x in (data.get("failed_roles") or []) if str(x).strip()}
    pending_set = {
        str(x).strip()
        for x in (data.get("pending_roles") or data.get("unresolved_roles") or [])
        if str(x).strip()
    }

    if isinstance(roles_obj, list):
        for item in roles_obj:
            role = str(item).strip()
            if not role:
                continue
            if role in failed_set:
                status = "failed"
            elif role in done_set:
                status = "done"
            elif role in pending_set:
                status = "pending"
            else:
                status = "pending"
            rows.append({"role": role, "status": status})
        if rows:
            return rows

    all_roles = dedupe_roles(list(done_set) + list(failed_set) + list(pending_set))
    for role in all_roles:
        if role in failed_set:
            status = "failed"
        elif role in done_set:
            status = "done"
        else:
            status = "pending"
        rows.append({"role": role, "status": status})
    return rows


def extract_request_snapshot(data: Dict[str, Any], *, dedupe_roles: Callable[[Iterable[str]], List[str]]) -> Dict[str, Any]:
    rows = normalize_role_rows(data, dedupe_roles=dedupe_roles)
    counts = data.get("counts") or {}

    assignments = int(counts.get("assignments", 0) or 0)
    replies = int(counts.get("replies", 0) or 0)
    if assignments <= 0:
        assignments = len(rows)
    if replies <= 0:
        replies = len(data.get("replies") or [])

    done_roles: Set[str] = set()
    failed_roles: Set[str] = set()
    pending_roles: Set[str] = set()

    for row in rows:
        role = str(row.get("role", "")).strip()
        status = str(row.get("status", "pending")).strip().lower()
        if not role:
            continue
        if status in {"failed", "error", "fail"}:
            failed_roles.add(role)
        elif status == "done":
            done_roles.add(role)
        else:
            pending_roles.add(role)

    for role in data.get("done_roles") or []:
        token = str(role).strip()
        if token:
            done_roles.add(token)
            pending_roles.discard(token)
            failed_roles.discard(token)

    for role in data.get("failed_roles") or []:
        token = str(role).strip()
        if token:
            failed_roles.add(token)
            done_roles.discard(token)
            pending_roles.discard(token)

    for role in data.get("pending_roles") or data.get("unresolved_roles") or []:
        token = str(role).strip()
        if token and token not in done_roles and token not in failed_roles:
            pending_roles.add(token)

    request_id = str(data.get("request_id", "")).strip()
    gateway_request_id = str(data.get("gateway_request_id", "")).strip()
    complete = bool(data.get("complete", False))
    return {
        "request_id": request_id,
        "gateway_request_id": gateway_request_id,
        "rows": rows,
        "assignments": assignments,
        "replies": replies,
        "complete": complete,
        "done_roles": sorted(done_roles),
        "failed_roles": sorted(failed_roles),
        "pending_roles": sorted(pending_roles),
    }


def sync_task_lifecycle(
    entry: Dict[str, Any],
    request_data: Dict[str, Any],
    *,
    prompt: str,
    mode: str,
    selected_roles: Optional[List[str]],
    verifier_roles: Optional[List[str]],
    require_verifier: bool,
    verifier_candidates: List[str],
    dedupe_roles: Callable[[Iterable[str]], List[str]],
    ensure_task_record: Callable[..., Dict[str, Any]],
    lifecycle_set_stage: Callable[..., None],
    normalize_task_status: Callable[[Any], str],
    sync_task_exec_context: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, str]],
    intent_command: str = "",
    intent_action: str = "",
    intent_class: str = "",
    intent_trace: str = "",
) -> Optional[Dict[str, Any]]:
    snap = extract_request_snapshot(request_data, dedupe_roles=dedupe_roles)
    request_id = str(snap.get("gateway_request_id", "") or snap.get("request_id", "")).strip()
    if not request_id:
        return None

    rows = snap.get("rows") or []
    inferred_roles = [str(x.get("role", "")).strip() for x in rows if str(x.get("role", "")).strip()]
    roles = dedupe_roles(selected_roles or inferred_roles)

    verifier_keys = {str(c or "").strip().lower() for c in verifier_candidates if str(c or "").strip()}
    inferred_verifiers = [r for r in roles if r.lower() in verifier_keys]
    verifiers = dedupe_roles(verifier_roles or inferred_verifiers)

    task = ensure_task_record(
        entry=entry,
        request_id=request_id,
        prompt=prompt,
        mode=mode,
        roles=roles,
        verifier_roles=verifiers,
        require_verifier=require_verifier,
        intent_command=intent_command,
        intent_action=intent_action,
        intent_class=intent_class,
        intent_trace=intent_trace,
    )
    dispatch_metadata = (
        request_data.get("dispatch_metadata")
        if isinstance(request_data.get("dispatch_metadata"), dict)
        else {}
    )
    request_contract_snapshot = normalize_request_contract_snapshot(
        {
            "version": dispatch_metadata.get("request_contract_version"),
            "contract_type": dispatch_metadata.get("request_contract_type"),
            "preset": dispatch_metadata.get("request_contract_preset"),
            "status": dispatch_metadata.get("request_contract_status"),
            "summary": dispatch_metadata.get("request_contract_summary"),
            "missing_fields": dispatch_metadata.get("request_contract_missing_fields"),
            "required_outputs": dispatch_metadata.get("request_contract_required_outputs"),
            "fields": dispatch_metadata.get("request_contract_fields"),
            "artifact_contracts": dispatch_metadata.get("request_contract_artifact_contracts"),
        }
    )
    if request_contract_snapshot:
        apply_request_contract_snapshot(task, request_contract_snapshot)

    assignments = int(snap.get("assignments", 0) or 0)
    replies = int(snap.get("replies", 0) or 0)
    complete = bool(snap.get("complete", False))
    done_roles = set(str(x) for x in (snap.get("done_roles") or []))
    failed_roles = set(str(x) for x in (snap.get("failed_roles") or []))
    pending_roles = set(str(x) for x in (snap.get("pending_roles") or []))

    lifecycle_set_stage(task, "intake", "done")
    lifecycle_set_stage(task, "planning", "done")

    staffing_status = "done" if assignments > 0 else ("running" if roles else "pending")
    lifecycle_set_stage(task, "staffing", staffing_status)

    if failed_roles:
        execution_status = "failed"
    elif complete and assignments > 0 and not pending_roles:
        execution_status = "done"
    elif assignments > 0:
        execution_status = "running"
    else:
        execution_status = "pending"
    lifecycle_set_stage(task, "execution", execution_status)

    ver_note = ""
    if require_verifier:
        if not verifiers:
            verification_status = "failed"
            ver_note = "no verifier role assigned"
        elif any(v in failed_roles for v in verifiers):
            verification_status = "failed"
            ver_note = "verifier role failed"
        elif all(v in done_roles for v in verifiers):
            verification_status = "done"
        elif complete and execution_status == "done":
            verification_status = "failed"
            ver_note = "verifier gate not satisfied"
        elif execution_status in {"running", "done"}:
            verification_status = "running"
        elif execution_status == "failed":
            verification_status = "failed"
        else:
            verification_status = "pending"
    else:
        if execution_status == "done":
            verification_status = "done"
        elif execution_status == "failed":
            verification_status = "failed"
        elif execution_status == "running":
            verification_status = "running"
        else:
            verification_status = "pending"

    lifecycle_set_stage(task, "verification", verification_status, note=ver_note)

    if execution_status == "failed" or verification_status == "failed":
        integration_status = "failed"
    elif verification_status == "done" and (replies > 0 or complete):
        integration_status = "done"
    elif execution_status == "running" or verification_status == "running":
        integration_status = "running"
    else:
        integration_status = "pending"
    lifecycle_set_stage(task, "integration", integration_status)

    if integration_status == "failed":
        close_status = "failed"
    elif integration_status == "done" and complete:
        close_status = "done"
    elif execution_status == "running" or verification_status == "running":
        close_status = "running"
    else:
        close_status = "pending"
    lifecycle_set_stage(task, "close", close_status)

    if close_status == "failed" or verification_status == "failed" or execution_status == "failed":
        overall = "failed"
    elif close_status == "done":
        overall = "completed"
    elif close_status == "running" or execution_status == "running" or verification_status == "running":
        overall = "running"
    else:
        overall = "pending"

    task["status"] = normalize_task_status(overall)
    task["roles"] = roles
    task["verifier_roles"] = verifiers
    task["require_verifier"] = bool(require_verifier)
    task["result"] = {
        "assignments": assignments,
        "replies": replies,
        "complete": complete,
        "done_roles": sorted(done_roles),
        "failed_roles": sorted(failed_roles),
        "pending_roles": sorted(pending_roles),
    }
    if request_contract_snapshot:
        task["result"]["request_contract_type"] = str(request_contract_snapshot.get("contract_type", "")).strip()
        task["result"]["request_contract_status"] = str(request_contract_snapshot.get("status", "")).strip()
        if str(request_contract_snapshot.get("summary", "")).strip():
            task["result"]["request_contract_summary"] = str(request_contract_snapshot.get("summary", "")).strip()
        brief = build_execution_brief(request_contract_snapshot)
        if str(brief.get("status", "")).strip():
            task["result"]["execution_brief_status"] = str(brief.get("status", "")).strip()
        if str(brief.get("summary", "")).strip():
            task["result"]["execution_brief_summary"] = str(brief.get("summary", "")).strip()
        job_contract = build_job_contract(request_contract_snapshot, brief)
        if str(job_contract.get("summary", "")).strip():
            task["result"]["job_contract_summary"] = str(job_contract.get("summary", "")).strip()
    rate_limit = request_data.get("rate_limit") if isinstance(request_data.get("rate_limit"), dict) else {}
    if rate_limit:
        task["rate_limit"] = dict(rate_limit)
        task["result"]["rate_limit"] = dict(rate_limit)
    else:
        task.pop("rate_limit", None)
        task["result"].pop("rate_limit", None)
    degraded_by = [str(x).strip() for x in (request_data.get("degraded_by") or []) if str(x).strip()]
    if degraded_by:
        task["result"]["degraded_by"] = degraded_by
    else:
        task["result"].pop("degraded_by", None)
    requested_roles = request_data.get("requested_roles") if isinstance(request_data.get("requested_roles"), list) else roles
    executed_roles = request_data.get("executed_roles") if isinstance(request_data.get("executed_roles"), list) else inferred_roles
    task["result"].update(
        derive_role_execution_snapshot(
            requested_roles,
            executed_roles,
            dedupe_roles=dedupe_roles,
        )
    )
    backend_snapshot = derive_backend_snapshot(request_data)
    if backend_snapshot.get("backend"):
        for key, value in backend_snapshot.items():
            if value in ("", None):
                task["result"].pop(key, None)
                task.pop(key, None)
                continue
            task["result"][key] = value
            task[key] = value
    linked_request_ids = request_data.get("linked_request_ids")
    if isinstance(linked_request_ids, list) and linked_request_ids:
        task["result"]["linked_request_ids"] = [
            str(value).strip()
            for value in linked_request_ids
            if str(value).strip()
        ]
    phase2_request_ids = request_data.get("phase2_request_ids")
    if isinstance(phase2_request_ids, dict) and phase2_request_ids:
        normalized_phase2_request_ids: Dict[str, Any] = {}
        for key, value in phase2_request_ids.items():
            bucket = str(key).strip()
            if not bucket:
                continue
            if isinstance(value, list):
                tokens = [str(item).strip() for item in value if str(item).strip()]
                if tokens:
                    normalized_phase2_request_ids[bucket] = tokens
            else:
                token = str(value).strip()
                if token:
                    normalized_phase2_request_ids[bucket] = token
        if normalized_phase2_request_ids:
            task["result"]["phase2_request_ids"] = normalized_phase2_request_ids
    if "phase2_review_triggered" in request_data:
        task["result"]["phase2_review_triggered"] = bool(request_data.get("phase2_review_triggered"))
    review_skip = str(request_data.get("phase2_review_skipped_reason", "")).strip()
    if review_skip:
        task["result"]["phase2_review_skipped_reason"] = review_skip[:240]
    lane_states = derive_lane_states(task, snap)
    if lane_states:
        task["lane_states"] = lane_states
        apply_review_lane_verdicts(task)
    else:
        task.pop("lane_states", None)
    refresh_task_planning_primitives(task, request_contract=request_contract_snapshot)
    refresh_task_tf_state(task)
    sync_task_exec_context(entry, task)
    return task
