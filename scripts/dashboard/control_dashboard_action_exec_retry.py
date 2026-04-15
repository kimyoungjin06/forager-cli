#!/usr/bin/env python3
"""Retry execution bridge for dashboard mutation actions."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aoe_tg_background_runs as background_runs
import aoe_tg_chat_state as chat_state
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
from aoe_tg_executor_dispatch import (
    build_gateway_command_launch_spec_for_adapter,
    launch_background_ticket_via_adapter,
)
from aoe_tg_request_contract import (
    apply_background_run_ticket_snapshot,
    build_background_run_ticket,
    select_background_runner_target,
)
import aoe_tg_retry_handlers as retry_handlers
from aoe_tg_run_lock import project_run_lock_blocks_launch, project_run_lock_mode, project_run_lock_note
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view
from aoe_tg_action_audit import (
    append_action_audit_row,
    load_latest_action_audit_for_runtime_kind,
    load_latest_offdesk_judge_decision_for_runtime,
)

from control_dashboard_action_exec_shared import (
    _DASHBOARD_CHAT_ID,
    _build_dashboard_retry_run_deps,
    _dashboard_action_args,
    _dashboard_get_context_factory,
    _dashboard_run_args,
    _find_task_project_key,
    _json,
    _latest_recorded_outcome,
    _load_gateway_main_module,
    _load_dashboard_manager_state,
    _make_send_collector,
    _missing_outcome_response,
)
from control_dashboard_action_exec_feedback import (
    derive_canonical_writeback_feedback,
    derive_manual_step_feedback,
    persist_manual_step_execution_state,
)
from control_dashboard_common import DashboardAppConfig, _not_found_json

_RETRY_BLOCKED_REMEDIATIONS = {
    "planning-gate": "run /orch judge for the runtime first, then inspect planning critic issues and approval blockers in /task and /offdesk review before retrying again",
    "dispatch-exception": "inspect dispatch exception output and backend notes in the task detail before attempting another retry",
    "exec-critic": "run /orch judge for the runtime first, then inspect exec critic verdict and lane rerun targets in /task before retrying again",
    "verifier-gate failed": "inspect verifier findings and required verifier roles in /task before retrying again",
    "run usage": "inspect the retry command payload and lane selection before retrying again",
    "unknown command": "inspect the retry action contract and command mapping before retrying again",
    "empty prompt": "inspect the source task prompt in the runtime lifecycle before retrying again",
}

_RETRY_JUDGE_FIRST_REASON_CODES = {"planning_gate", "exec_critic"}
_RETRY_JUDGE_FIRST_CONTEXTS = {"planning-gate", "exec-critic"}


def _retry_blocked_remediation_for_reason(reason_code: str, detail: str = "") -> str:
    token = str(reason_code or "").strip().lower().replace("-", "_")
    if token == "planning_gate":
        return _RETRY_BLOCKED_REMEDIATIONS["planning-gate"]
    if token == "dispatch_exception":
        return _RETRY_BLOCKED_REMEDIATIONS["dispatch-exception"]
    if token == "exec_critic":
        return _RETRY_BLOCKED_REMEDIATIONS["exec-critic"]
    if token == "verifier_gate_failed":
        return _RETRY_BLOCKED_REMEDIATIONS["verifier-gate failed"]
    if token == "verifier_gate_setup":
        return "assign or enable the required verifier role before retrying the runtime again"
    return str(detail or "").strip() or "inspect the task and runtime state before retrying again"


def _retry_blocked_next_step_for_reason(
    reason_code: str,
    *,
    entry: Dict[str, Any],
    fallback: str = "/offdesk review",
) -> str:
    token = str(reason_code or "").strip().lower().replace("-", "_")
    if token in _RETRY_JUDGE_FIRST_REASON_CODES:
        return f"/orch judge {_project_status_ref(str(entry.get('name', '')).strip(), entry)}"
    return fallback


def _retry_blocked_next_step_for_contexts(
    contexts: List[str],
    *,
    entry: Dict[str, Any],
    fallback: str = "/offdesk review",
) -> str:
    for context in contexts:
        token = str(context or "").strip().lower()
        if token in _RETRY_JUDGE_FIRST_CONTEXTS:
            return f"/orch judge {_project_status_ref(str(entry.get('name', '')).strip(), entry)}"
    return fallback



def _retry_blocked_remediation(contexts: List[str]) -> str:
    for context in contexts:
        token = str(context or "").strip()
        if token in _RETRY_BLOCKED_REMEDIATIONS:
            return _RETRY_BLOCKED_REMEDIATIONS[token]
    return "run /orch judge for the runtime first, then inspect planning or critic blockers in /offdesk review before re-running retry"


def _latest_judge_summary_payload(*, team_dir: Path, entry: Dict[str, Any]) -> Dict[str, str]:
    alias = _project_status_ref(str(entry.get("name", "")).strip(), entry)
    row = load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=alias,
        outcome_kind="offdesk_judge",
    )
    if not row:
        return {}
    return {
        "headline": str(row.get("headline", "")).strip() or "-",
        "next_step": str(row.get("next_step", "")).strip() or "-",
        "detail": str(row.get("outcome_detail", "")).strip() or "-",
        "at": str(row.get("at", "")).strip() or "-",
    }


def _latest_judge_decision_payload(*, team_dir: Path, entry: Dict[str, Any]) -> Dict[str, Any]:
    alias = _project_status_ref(str(entry.get("name", "")).strip(), entry)
    decision = load_latest_offdesk_judge_decision_for_runtime(team_dir, project_alias=alias)
    if not isinstance(decision, dict) or not decision:
        return {}
    payload: Dict[str, Any] = {
        "verdict": str(decision.get("verdict", "")).strip() or "-",
        "confidence": str(decision.get("confidence", "")).strip() or "-",
        "reasoning": str(decision.get("reasoning", "")).strip() or "-",
        "next_step": str(decision.get("next_step", "")).strip() or "-",
        "caution": str(decision.get("caution", "")).strip() or "-",
        "recommended_action": str(decision.get("recommended_action", "")).strip() or "-",
        "at": str(decision.get("at", "")).strip() or "-",
    }
    worker_module = str(decision.get("worker_module", "")).strip() or "-"
    worker_record_set = str(decision.get("worker_record_set", "")).strip() or "-"
    analysis_record_set = str(decision.get("analysis_record_set", "")).strip() or "-"
    worker_record_set_records = [item for item in (decision.get("worker_record_set_records") or []) if isinstance(item, dict)]
    analysis_record_set_records = [item for item in (decision.get("analysis_record_set_records") or []) if isinstance(item, dict)]
    if worker_module != "-":
        payload["worker_module"] = worker_module
    if worker_record_set != "-":
        payload["worker_record_set"] = worker_record_set
    if worker_record_set_records:
        payload["worker_record_set_records"] = worker_record_set_records
    if analysis_record_set != "-":
        payload["analysis_record_set"] = analysis_record_set
    if analysis_record_set_records:
        payload["analysis_record_set_records"] = analysis_record_set_records
    return payload


def _retry_blocked_remediation_with_latest_judge(remediation: str, latest_judge: Dict[str, Any]) -> str:
    if not isinstance(latest_judge, dict) or not latest_judge:
        return remediation
    parts: List[str] = []
    headline = str(latest_judge.get("headline", "")).strip()
    detail = str(latest_judge.get("detail", "")).strip()
    at = str(latest_judge.get("at", "")).strip()
    if headline and headline != "-":
        parts.append(headline)
    if detail and detail != "-":
        parts.append(detail)
    if at and at != "-":
        parts.append(f"at={at}")
    if not parts:
        return remediation
    base = str(remediation or "").strip()
    suffix = f"latest judge: {' | '.join(parts)}"
    return f"{base}; {suffix}" if base else suffix


def _command_head(command_text: str) -> str:
    token = str(command_text or "").strip()
    if not token:
        return ""
    return token.split()[0].strip().lower()


def _promote_blocked_next_step_from_latest_judge(
    current_next_step: str,
    *,
    latest_judge_decision: Dict[str, Any],
    source_command: str,
) -> str:
    current = str(current_next_step or "").strip()
    if not current.lower().startswith("/orch judge "):
        return current
    if not isinstance(latest_judge_decision, dict) or not latest_judge_decision:
        return current
    candidate = str(latest_judge_decision.get("next_step", "")).strip()
    if not candidate or candidate == "-" or candidate == current:
        return current
    current_head = _command_head(source_command)
    candidate_head = _command_head(candidate)
    if candidate_head and candidate_head == current_head:
        return current
    return candidate if candidate.startswith("/") else current


def _latest_judge_decision_bridge(
    current_next_step: str,
    *,
    latest_judge_decision: Dict[str, Any],
    source_command: str,
) -> Tuple[str, Dict[str, Any]]:
    current = str(current_next_step or "").strip()
    if not isinstance(latest_judge_decision, dict) or not latest_judge_decision:
        return current, {}
    promoted = _promote_blocked_next_step_from_latest_judge(
        current,
        latest_judge_decision=latest_judge_decision,
        source_command=source_command,
    )
    recommended_action = str(latest_judge_decision.get("recommended_action", "")).strip() or "-"
    candidate_next_step = str(latest_judge_decision.get("next_step", "")).strip() or "-"
    bridge = {
        "source": "latest_offdesk_judge",
        "verdict": str(latest_judge_decision.get("verdict", "")).strip() or "-",
        "confidence": str(latest_judge_decision.get("confidence", "")).strip() or "-",
        "recommended_action": recommended_action,
        "reasoning": str(latest_judge_decision.get("reasoning", "")).strip() or "-",
        "caution": str(latest_judge_decision.get("caution", "")).strip() or "-",
        "candidate_next_step": candidate_next_step,
        "applied": promoted != current,
        "applied_next_step": promoted if promoted != current else "-",
        "decision_mode": "promoted_next_step" if promoted != current else "observe_only",
        "supports_auto_decision": recommended_action in {
            "retry",
            "replan",
            "followup",
            "followup_execute",
            "review",
            "manual_review",
            "judge",
        },
    }
    return promoted, bridge


def _retry_blocked_remediation_with_judge_bridge(remediation: str, bridge: Dict[str, Any]) -> str:
    if not isinstance(bridge, dict) or not bridge:
        return remediation
    if not bool(bridge.get("applied")):
        return remediation
    action = str(bridge.get("recommended_action", "")).strip() or "-"
    next_step = str(bridge.get("applied_next_step", "")).strip() or "-"
    suffix = f"judge decision reuse: action={action} next={next_step}"
    base = str(remediation or "").strip()
    return f"{base}; {suffix}" if base else suffix


def _retry_blocked_remediation_with_manual_feedback(remediation: str, decision: Dict[str, Any]) -> str:
    if not isinstance(decision, dict) or not bool(decision.get("manual_feedback_applied", False)):
        return remediation
    state = str(decision.get("manual_feedback_state", "")).strip() or "-"
    next_step = str(decision.get("manual_feedback_next_step", "")).strip() or "-"
    summary = str(decision.get("manual_feedback_summary", "")).strip() or "-"
    suffix = f"manual step reused: state={state} next={next_step}"
    if summary and summary != "-":
        suffix = f"{suffix} | {summary}"
    base = str(remediation or "").strip()
    return f"{base}; {suffix}" if base else suffix


def _retry_blocked_remediation_with_canonical_feedback(remediation: str, decision: Dict[str, Any]) -> str:
    if not isinstance(decision, dict) or not bool(decision.get("canonical_feedback_applied", False)):
        return remediation
    kind = str(decision.get("canonical_feedback_kind", "")).strip() or "-"
    profile = str(decision.get("canonical_feedback_profile", "")).strip() or "-"
    next_step = str(decision.get("canonical_feedback_next_step", "")).strip() or "-"
    summary = str(decision.get("canonical_feedback_summary", "")).strip() or "-"
    suffix = f"canonical mutation reused: kind={kind}:{profile} next={next_step}"
    if summary and summary != "-":
        suffix = f"{suffix} | {summary}"
    base = str(remediation or "").strip()
    return f"{base}; {suffix}" if base else suffix


def _retry_blocked_remediation_with_analysis_feedback(remediation: str, decision: Dict[str, Any]) -> str:
    if not isinstance(decision, dict) or not bool(decision.get("analysis_feedback_applied", False)):
        return remediation
    open_kinds = str(decision.get("analysis_feedback_open_kinds", "")).strip() or "-"
    next_step = str(decision.get("analysis_feedback_next_step", "")).strip() or "-"
    summary = str(decision.get("analysis_feedback_summary", "")).strip() or "-"
    suffix = f"analysis records reused: open={open_kinds} next={next_step}"
    if summary and summary != "-":
        suffix = f"{suffix} | {summary}"
    base = str(remediation or "").strip()
    return f"{base}; {suffix}" if base else suffix


def _append_blocked_retry_replan_audit(
    *,
    team_dir: Path,
    entry: Dict[str, Any],
    source_command: str,
    blocked: bool,
    reason_code: str,
    detail: str,
    next_step: str,
    remediation: str,
    latest_judge_decision_bridge: Dict[str, Any],
    replan_auto_decision: Dict[str, Any],
    replan_auto_routing_policy: Dict[str, Any],
    now_iso: Any,
) -> None:
    if not blocked or not isinstance(entry, dict):
        return
    alias = _project_status_ref(str(entry.get("name", "")).strip(), entry)
    command_head = _command_head(source_command)
    if command_head not in {"/retry", "/replan"}:
        return
    label = "Replan" if command_head == "/replan" else "Retry"
    outcome_kind = "replan" if command_head == "/replan" else "retry_run"
    append_action_audit_row(
        team_dir,
        headline=f"{label} | blocked",
        status="blocked",
        outcome_kind=outcome_kind,
        outcome_status="blocked",
        outcome_reason_code=str(reason_code or "").strip() or "-",
        outcome_detail=str(detail or "").strip() or "-",
        next_step=str(next_step or "").strip() or "-",
        remediation=str(remediation or "").strip() or "-",
        source_command=str(source_command or "").strip() or f"{command_head} {alias}",
        link_label=f"Runtime {alias}",
        link_href=f"/control/runtimes/{alias}",
        at=now_iso(),
        extra={
            "latest_judge_decision_bridge": dict(latest_judge_decision_bridge or {}),
            "replan_auto_decision": dict(replan_auto_decision or {}),
            "replan_auto_routing_policy": dict(replan_auto_routing_policy or {}),
        },
    )


def _analysis_feedback_task_ref(source_task: Dict[str, Any]) -> str:
    if not isinstance(source_task, dict):
        return ""
    return (
        str(source_task.get("short_id", "")).strip().upper()
        or str(source_task.get("alias", "")).strip()
        or str(source_task.get("request_id", "")).strip()
    )


def _analysis_record_set_feedback(
    *,
    source_task: Dict[str, Any],
    latest_judge_decision: Dict[str, Any],
    suggested_action: str,
) -> Dict[str, Any]:
    if suggested_action not in {"manual_review", "review", "judge"}:
        return {}
    decision = latest_judge_decision if isinstance(latest_judge_decision, dict) else {}
    records = [item for item in (decision.get("analysis_record_set_records") or []) if isinstance(item, dict)]
    if not records:
        return {}
    open_kinds: List[str] = []
    for item in records:
        kind = str(item.get("kind", "")).strip().lower()
        state = str(item.get("state", "")).strip().lower()
        if kind == "evidence" and state in {"missing", "open", "pending", "blocked"}:
            if kind not in open_kinds:
                open_kinds.append(kind)
        elif kind == "gap" and state in {"open", "pending", "blocked", "review"}:
            if kind not in open_kinds:
                open_kinds.append(kind)
        elif kind == "finding" and state in {"open", "pending", "draft", "review"}:
            if kind not in open_kinds:
                open_kinds.append(kind)
    if not open_kinds:
        return {}
    task_ref = _analysis_feedback_task_ref(source_task)
    next_step = f"/task {task_ref}" if task_ref else "-"
    return {
        "state": "open",
        "summary": str(decision.get("analysis_record_set", "")).strip() or "-",
        "next_step": next_step,
        "open_kinds": ",".join(open_kinds),
        "applied": next_step.startswith("/task "),
    }


def _replan_auto_decision_stub(
    *,
    source_command: str,
    next_step: str,
    latest_judge_decision: Dict[str, Any],
    latest_judge_decision_bridge: Dict[str, Any],
    source_task: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if _command_head(source_command) != "/replan":
        return {}
    decision = latest_judge_decision if isinstance(latest_judge_decision, dict) else {}
    bridge = latest_judge_decision_bridge if isinstance(latest_judge_decision_bridge, dict) else {}
    suggested_action = str(bridge.get("recommended_action", "")).strip() or str(decision.get("recommended_action", "")).strip() or "-"
    suggested_next_step = (
        str(bridge.get("applied_next_step", "")).strip()
        or str(bridge.get("candidate_next_step", "")).strip()
        or str(decision.get("next_step", "")).strip()
        or str(next_step or "").strip()
        or "-"
    )
    manual_step_feedback = derive_manual_step_feedback(
        source_task if isinstance(source_task, dict) else {},
        suggested_action=suggested_action,
        suggested_next_step=suggested_next_step,
    )
    manual_feedback_applied = bool(manual_step_feedback.get("can_reuse_next_step", False))
    canonical_writeback_feedback = derive_canonical_writeback_feedback(
        source_task if isinstance(source_task, dict) else {},
        suggested_action=suggested_action,
    )
    canonical_feedback_applied = bool(canonical_writeback_feedback.get("can_reuse_next_step", False))
    analysis_record_feedback = _analysis_record_set_feedback(
        source_task=source_task if isinstance(source_task, dict) else {},
        latest_judge_decision=decision,
        suggested_action=suggested_action,
    )
    analysis_feedback_applied = bool(analysis_record_feedback.get("applied", False))
    if manual_feedback_applied:
        suggested_next_step = str(manual_step_feedback.get("next_step", "")).strip() or suggested_next_step
        canonical_feedback_applied = False
        analysis_feedback_applied = False
    elif canonical_feedback_applied:
        suggested_next_step = (
            str(canonical_writeback_feedback.get("next_step", "")).strip() or suggested_next_step
        )
        analysis_feedback_applied = False
    elif analysis_feedback_applied:
        suggested_action = "task_review"
        suggested_next_step = str(analysis_record_feedback.get("next_step", "")).strip() or suggested_next_step
    decision_mode = str(bridge.get("decision_mode", "")).strip() or ("judge_signal" if decision else "none")
    if manual_feedback_applied:
        decision_mode = "manual_feedback_reuse"
    elif canonical_feedback_applied:
        decision_mode = "canonical_writeback_reuse"
    elif analysis_feedback_applied:
        decision_mode = "analysis_record_set_reuse"
    return {
        "source": "latest_offdesk_judge",
        "current_action": "replan",
        "suggested_action": suggested_action,
        "suggested_next_step": suggested_next_step,
        "decision_mode": decision_mode,
        "bridge_applied": bool(bridge.get("applied", False)),
        "supports_auto_decision": bool(bridge.get("supports_auto_decision", False)),
        "can_auto_apply": suggested_action in {"retry", "replan"} and suggested_next_step.startswith("/"),
        "reasoning": str(decision.get("reasoning", "")).strip() or "-",
        "caution": str(decision.get("caution", "")).strip() or "-",
        "confidence": str(decision.get("confidence", "")).strip() or "-",
        "manual_feedback_state": str(manual_step_feedback.get("state", "")).strip() or "-",
        "manual_feedback_summary": str(manual_step_feedback.get("summary", "")).strip() or "-",
        "manual_feedback_next_step": str(manual_step_feedback.get("next_step", "")).strip() or "-",
        "manual_feedback_applied": manual_feedback_applied,
        "canonical_feedback_status": str(canonical_writeback_feedback.get("status", "")).strip() or "-",
        "canonical_feedback_summary": str(canonical_writeback_feedback.get("summary", "")).strip() or "-",
        "canonical_feedback_next_step": str(canonical_writeback_feedback.get("next_step", "")).strip() or "-",
        "canonical_feedback_kind": str(canonical_writeback_feedback.get("kind", "")).strip() or "-",
        "canonical_feedback_profile": str(canonical_writeback_feedback.get("profile", "")).strip() or "-",
        "canonical_feedback_applied": canonical_feedback_applied,
        "analysis_feedback_state": str(analysis_record_feedback.get("state", "")).strip() or "-",
        "analysis_feedback_summary": str(analysis_record_feedback.get("summary", "")).strip() or "-",
        "analysis_feedback_next_step": str(analysis_record_feedback.get("next_step", "")).strip() or "-",
        "analysis_feedback_open_kinds": str(analysis_record_feedback.get("open_kinds", "")).strip() or "-",
        "analysis_feedback_applied": analysis_feedback_applied,
    }


def _replan_auto_routing_policy(
    *,
    source_command: str,
    replan_auto_decision: Dict[str, Any],
) -> Dict[str, Any]:
    if _command_head(source_command) != "/replan":
        return {}
    decision = replan_auto_decision if isinstance(replan_auto_decision, dict) else {}
    if not decision:
        return {}
    suggested_action = str(decision.get("suggested_action", "")).strip() or "-"
    suggested_next_step = str(decision.get("suggested_next_step", "")).strip() or "-"
    supports_auto_decision = bool(decision.get("supports_auto_decision", False))
    can_auto_apply = bool(decision.get("can_auto_apply", False)) and suggested_next_step.startswith("/")
    manual_feedback_state = str(decision.get("manual_feedback_state", "")).strip() or "-"
    manual_feedback_summary = str(decision.get("manual_feedback_summary", "")).strip() or "-"
    manual_feedback_applied = bool(decision.get("manual_feedback_applied", False))
    canonical_feedback_status = str(decision.get("canonical_feedback_status", "")).strip() or "-"
    canonical_feedback_summary = str(decision.get("canonical_feedback_summary", "")).strip() or "-"
    canonical_feedback_kind = str(decision.get("canonical_feedback_kind", "")).strip() or "-"
    canonical_feedback_profile = str(decision.get("canonical_feedback_profile", "")).strip() or "-"
    canonical_feedback_applied = bool(decision.get("canonical_feedback_applied", False))
    analysis_feedback_state = str(decision.get("analysis_feedback_state", "")).strip() or "-"
    analysis_feedback_summary = str(decision.get("analysis_feedback_summary", "")).strip() or "-"
    analysis_feedback_open_kinds = str(decision.get("analysis_feedback_open_kinds", "")).strip() or "-"
    analysis_feedback_applied = bool(decision.get("analysis_feedback_applied", False))
    manual_ready = (
        supports_auto_decision
        and not can_auto_apply
        and suggested_next_step.startswith("/")
        and suggested_action in {"followup", "followup_execute", "manual_review", "review", "judge"}
    )
    if manual_feedback_applied and suggested_action in {"followup", "followup_execute", "manual_review", "review", "judge"}:
        status = "manual_progressed"
        requires_operator_confirmation = False
    elif canonical_feedback_applied and suggested_action in {"followup", "followup_execute"}:
        status = "mutation_progressed"
        requires_operator_confirmation = False
    elif analysis_feedback_applied and suggested_action == "task_review":
        status = "analysis_review_ready"
        requires_operator_confirmation = False
    else:
        status = "ready" if can_auto_apply else ("manual_ready" if manual_ready else ("observe_only" if supports_auto_decision else "unavailable"))
        requires_operator_confirmation = can_auto_apply or manual_ready
    return {
        "source": str(decision.get("source", "")).strip() or "latest_offdesk_judge",
        "status": status,
        "current_action": str(decision.get("current_action", "")).strip() or "replan",
        "suggested_action": suggested_action,
        "suggested_next_step": suggested_next_step,
        "decision_mode": str(decision.get("decision_mode", "")).strip() or "none",
        "supports_auto_decision": supports_auto_decision,
        "can_auto_apply": can_auto_apply,
        "requires_operator_confirmation": requires_operator_confirmation,
        "reasoning": str(decision.get("reasoning", "")).strip() or "-",
        "caution": str(decision.get("caution", "")).strip() or "-",
        "confidence": str(decision.get("confidence", "")).strip() or "-",
        "manual_feedback_state": manual_feedback_state,
        "manual_feedback_summary": manual_feedback_summary,
        "manual_feedback_applied": manual_feedback_applied,
        "canonical_feedback_status": canonical_feedback_status,
        "canonical_feedback_summary": canonical_feedback_summary,
        "canonical_feedback_kind": canonical_feedback_kind,
        "canonical_feedback_profile": canonical_feedback_profile,
        "canonical_feedback_applied": canonical_feedback_applied,
        "analysis_feedback_state": analysis_feedback_state,
        "analysis_feedback_summary": analysis_feedback_summary,
        "analysis_feedback_open_kinds": analysis_feedback_open_kinds,
        "analysis_feedback_applied": analysis_feedback_applied,
    }


def _payload_bool(payload: Dict[str, Any], key: str) -> bool:
    if not isinstance(payload, dict):
        return False
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "on"}


def _normalize_lane_ids(items: Any) -> List[str]:
    lane_ids: List[str] = []
    for row in list(items or []):
        token = str(row or "").strip()[:32]
        if token and token not in lane_ids:
            lane_ids.append(token)
    return lane_ids


def _retry_command_text_from_policy(
    *,
    task_ref: str,
    lane_ids: List[str],
    policy: Dict[str, Any],
) -> str:
    command = str((policy or {}).get("suggested_next_step", "")).strip()
    if not command.startswith("/retry "):
        command = f"/retry {str(task_ref or '').strip()}".strip()
    if lane_ids and " lane " not in command:
        command += " lane " + ",".join(lane_ids)
    return command


def _append_replan_auto_route_audit(
    *,
    team_dir: Path,
    entry: Dict[str, Any],
    source_command: str,
    retry_command: str,
    policy: Dict[str, Any],
    now_iso: Any,
) -> None:
    append_action_audit_row(
        team_dir,
        headline="Replan Auto Route | applied",
        status="executed",
        outcome_kind="replan_auto_route",
        outcome_status="executed",
        outcome_reason_code="judge_policy_ready",
        outcome_detail=f"retry_command={str(retry_command or '').strip() or '-'}",
        next_step=str(retry_command or "").strip() or "-",
        remediation="inspect the retried task outcome and judge policy reuse before applying another auto-route",
        source_command=str(source_command or "").strip() or "-",
        link_label=f"Runtime {_project_status_ref(str(entry.get('name', '')).strip(), entry)}",
        link_href=f"/control/runtimes/{_project_status_ref(str(entry.get('name', '')).strip(), entry)}",
        at=now_iso(),
        extra={"replan_auto_routing_policy": dict(policy or {})},
    )


def _json_with_extra_fields(
    response: Tuple[int, Dict[str, str], bytes],
    *,
    extra: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes]:
    status, headers, body = response
    content_type = str((headers or {}).get("Content-Type", "")).strip().lower()
    if "application/json" not in content_type:
        return response
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return response
    if not isinstance(payload, dict):
        return response
    payload.update(extra or {})
    return _json(payload, status=status)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _project_status_ref(project_key: str, entry: Dict[str, Any]) -> str:
    alias = str(entry.get("project_alias", "")).strip().upper()
    return alias or str(project_key or "").strip() or "-"


def _retry_command_text(transition: Dict[str, Any]) -> str:
    source_request_id = str(transition.get("run_source_request_id", "")).strip()
    if not source_request_id:
        return ""
    lane_ids: List[str] = []
    for row in list(transition.get("run_selected_execution_lane_ids") or []) + list(
        transition.get("run_selected_review_lane_ids") or []
    ):
        token = str(row or "").strip()[:32]
        if token and token not in lane_ids:
            lane_ids.append(token)
    run_control_mode = str(transition.get("run_control_mode", "")).strip().lower()
    if run_control_mode == "followup":
        head = "/followup-exec"
    else:
        head = "/replan" if run_control_mode == "replan" else "/retry"
    command = f"{head} {source_request_id}"
    if lane_ids:
        command += " lane " + ",".join(lane_ids)
    return command


def _set_task_background_ticket(task: Dict[str, Any], ticket: Dict[str, Any], *, current_ts: str) -> None:
    apply_background_run_ticket_snapshot(task, ticket)
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_status"] = str(ticket.get("status", "")).strip()
        task["result"]["background_run_runner_target"] = str(ticket.get("runner_target", "")).strip()
        task["result"]["background_run_ticket_id"] = str(ticket.get("ticket_id", "")).strip()
        bundle = str(ticket.get("evidence_bundle", "")).strip()
        if bundle:
            task["result"]["background_run_evidence_bundle"] = bundle
    task["updated_at"] = current_ts


def _run_lock_block_response(
    *,
    entry: Dict[str, Any],
    transition: Dict[str, Any],
    source_command: str,
    payload: Dict[str, Any],
    path: str,
) -> Tuple[int, Dict[str, str], bytes] | None:
    mode = project_run_lock_mode(entry)
    note = project_run_lock_note(entry)
    if mode != "test_only":
        return None
    alias = _project_status_ref(str(transition.get("orch_target", "")).strip(), entry)
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "error": "run_lock_test_only",
            "method": "POST",
            "path": path,
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
            },
            "next_step": f"/orch run-lock {alias} open",
            "remediation": note or "clear the test-only run lock before launching a non-test retry or follow-up job",
            "outcome": {
                "kind": "run_lock",
                "status": "blocked",
                "reason_code": "run_lock_test_only",
                "detail": f"run_lock_mode={mode}",
            },
        },
        status=409,
    )


def _background_slots_exhausted_response(
    *,
    entry: Dict[str, Any],
    transition: Dict[str, Any],
    source_command: str,
    payload: Dict[str, Any],
    path: str,
    runner_target: str,
    active_slots: int,
    slot_limit: int,
) -> Tuple[int, Dict[str, str], bytes]:
    alias = _project_status_ref(str(transition.get("orch_target", "")).strip(), entry)
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "error": "background_runner_slots_exhausted",
            "method": "POST",
            "path": path,
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
            },
            "next_step": f"/orch bg-slots {alias} {runner_target} {slot_limit + 1 if slot_limit < 8 else slot_limit}",
            "remediation": f"background runner slots are saturated for {runner_target} ({active_slots}/{slot_limit}); wait for current jobs to finish or raise that runner limit deliberately",
            "outcome": {
                "kind": "background_slots",
                "status": "blocked",
                "reason_code": "background_runner_slots_exhausted",
                "detail": f"runner_target={runner_target} active={active_slots} limit={slot_limit}",
            },
        },
        status=409,
    )


def _maybe_execute_retry_background_runner(
    transition: Dict[str, Any],
    *,
    manager_state: Dict[str, Any],
    paths: Any,
    source_command: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes] | None:
    project_key = str(transition.get("orch_target", "")).strip()
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else None
    if not isinstance(entry, dict):
        return None
    source_request_id = str(transition.get("run_source_request_id", "")).strip()
    team_dir_raw = str(entry.get("team_dir", "")).strip()
    command_text = _retry_command_text(transition)
    if not source_request_id or not team_dir_raw or not command_text:
        return None

    control_mode = str(transition.get("run_control_mode", "")).strip().lower()
    if control_mode == "followup":
        action_path = "/control/actions/task/followup-execute"
        launch_mode = "dashboard_followup_execute"
        source_surface = "dashboard_followup_execute"
        outcome_kind = "followup_execute"
        success_reason_code = "background_tmux_followup_started"
    elif control_mode == "replan":
        action_path = "/control/actions/task/replan"
        launch_mode = "dashboard_replan"
        source_surface = "dashboard_replan"
        outcome_kind = "retry_run"
        success_reason_code = "background_tmux_replan_started"
    else:
        action_path = "/control/actions/task/retry"
        launch_mode = "dashboard_retry"
        source_surface = "dashboard_retry"
        outcome_kind = "retry_run"
        success_reason_code = "background_tmux_started"

    preferred_runner = str(entry.get("background_runner_target", "")).strip().lower()
    if preferred_runner not in {"local_tmux", "github_runner", "remote_worker"}:
        return None
    project_root = str(entry.get("project_root", "")).strip() or str(paths.control_root)
    manager_state_file = str(paths.manager_state_file)
    launch_spec = build_gateway_command_launch_spec_for_adapter(
        runner_target=preferred_runner,
        request_id=source_request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir_raw,
        manager_state_file=manager_state_file,
        command_text=command_text,
        simulate_chat_id=_DASHBOARD_CHAT_ID,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=f"dashboard:{_DASHBOARD_CHAT_ID}",
    )
    selected_runner = select_background_runner_target(
        preferred_runner_target=preferred_runner,
        launch_spec=launch_spec,
        allow_external_targets=True,
    )
    if selected_runner not in {"local_tmux", "github_runner", "remote_worker"}:
        return None
    if project_run_lock_blocks_launch(
        entry,
        launch_mode=launch_mode,
        source_surface=source_surface,
        source_command=command_text,
        launch_spec=launch_spec,
    ):
        return _run_lock_block_response(
            entry=entry,
            transition=transition,
            source_command=source_command,
            payload=payload,
            path=action_path,
        )
    slot_snapshot = background_runs.summarize_background_runner_slots(
        background_runs.background_runs_state_path(Path(team_dir_raw)),
        entry,
        selected_runner=selected_runner,
        statuses=["dispatching", "running"],
        max_value=8,
    )
    slot_limit = int(slot_snapshot.get("selected_limit", 1) or 1)
    active_slots = int(slot_snapshot.get("selected_active", 0) or 0)
    if active_slots >= slot_limit:
        return _background_slots_exhausted_response(
            entry=entry,
            transition=transition,
            source_command=source_command,
            payload=payload,
            path=action_path,
            runner_target=selected_runner,
            active_slots=active_slots,
            slot_limit=slot_limit,
        )

    queue_path = background_runs.background_runs_state_path(Path(team_dir_raw))
    current_ts = _now_iso()
    source_task = gateway_task_state.get_task_record(entry, source_request_id)
    pack_profile_override = "followup_execute" if control_mode == "followup" else "review"
    model_plan = model_endpoint_adapter.resolve_task_model_plan(
        team_dir_raw,
        entry=entry,
        task=source_task,
        pack_profile_override=pack_profile_override,
    )
    judge_binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir_raw,
        entry=entry,
        task=source_task,
        pack_profile_override=pack_profile_override,
    )
    judge_probe = model_endpoint_adapter.summarize_deferred_model_binding_probe(
        judge_binding,
        default_label="offdesk_judge",
    )
    escalation_binding = model_endpoint_adapter.resolve_task_escalation_binding(
        team_dir_raw,
        entry=entry,
        task=source_task,
        pack_profile_override=pack_profile_override,
    )
    escalation_probe = model_endpoint_adapter.summarize_deferred_model_binding_probe(
        escalation_binding,
        default_label="background_worker_escalation",
    )
    launch_spec.update(
        model_endpoint_adapter.launch_spec_model_plan_metadata(
            model_plan,
            judge_binding=judge_binding,
            judge_probe=judge_probe,
            escalation_binding=escalation_binding,
            escalation_probe=escalation_probe,
        )
    )
    ticket = build_background_run_ticket(
        request_id=source_request_id,
        project_key=project_key,
        execution_brief_status=str((source_task or {}).get("execution_brief_status", "executable")).strip() or "executable",
        runner_target=selected_runner,
        launch_mode=launch_mode,
        created_at=current_ts,
        created_by=f"dashboard:{_DASHBOARD_CHAT_ID}",
        source_surface=source_surface,
        status="queued",
        launch_spec=launch_spec,
    )
    ticket = background_runs.upsert_background_run_ticket(queue_path, ticket, now_iso=_now_iso)
    launched = launch_background_ticket_via_adapter(
        queue_path=queue_path,
        ticket_id=str(ticket.get("ticket_id", "")).strip(),
        runner_target=selected_runner,
        now_iso=_now_iso,
        claimed_by=f"dashboard:{_DASHBOARD_CHAT_ID}",
        source_surface=source_surface,
        launch_mode=launch_mode,
    )
    ticket_snapshot = launched if isinstance(launched, dict) and launched else ticket

    if isinstance(source_task, dict):
        _set_task_background_ticket(source_task, ticket_snapshot, current_ts=_now_iso())
        entry["updated_at"] = _now_iso()
        gateway_main = _load_gateway_main_module()
        gateway_main.save_manager_state(paths.manager_state_file, manager_state)

    project_ref = _project_status_ref(project_key, entry)
    task_payload = None
    if isinstance(source_task, dict):
        task_payload = {
            "request_id": source_request_id,
            "label": gateway_task_view.task_display_label(source_task, fallback_request_id=source_request_id),
            "status": str(source_task.get("status", "")).strip() or "-",
            "tf_phase": str(source_task.get("tf_phase", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{source_request_id}",
        }
    ticket_launch_spec = ticket_snapshot.get("launch_spec") if isinstance(ticket_snapshot.get("launch_spec"), dict) else {}
    background_payload = {
        "ticket_id": str(ticket_snapshot.get("ticket_id", "")).strip() or "-",
        "status": str(ticket_snapshot.get("status", "")).strip() or "-",
        "runner_target": str(ticket_snapshot.get("runner_target", "")).strip() or selected_runner,
        "runtime_handle": str(ticket_snapshot.get("runtime_handle", "")).strip() or "-",
        "runtime_summary": str(ticket_snapshot.get("runtime_summary", "")).strip() or "-",
        "launch_spec": str(ticket_launch_spec.get("summary", "")).strip() or "-",
        "model_plan": str(ticket_launch_spec.get("model_plan_summary", "")).strip() or "-",
        "model_pack_profile": str(ticket_launch_spec.get("model_pack_profile", "")).strip() or "-",
        "model_worker_route_id": str(ticket_launch_spec.get("model_worker_route_id", "")).strip() or "-",
        "model_judge_route_id": str(ticket_launch_spec.get("model_judge_route_id", "")).strip() or "-",
        "model_escalation_route_id": str(ticket_launch_spec.get("model_escalation_route_id", "")).strip() or "-",
    }
    blocked = str(ticket_snapshot.get("status", "")).strip().lower() == "failed"
    detail = str(ticket_snapshot.get("evidence_bundle", "")).strip() or "background_runner_launch_failed"
    if selected_runner == "local_tmux":
        remediation = (
            f"inspect tmux availability or switch /orch bg-runner {project_ref} local_background before retrying again"
            if blocked
            else "inspect tmux session state and runtime status before issuing another background rerun"
        )
    else:
        remediation = (
            f"inspect the emitted {selected_runner} handoff manifest or switch /orch bg-runner {project_ref} local_background before retrying again"
            if blocked
            else f"inspect the emitted {selected_runner} handoff manifest and downstream runner pickup before issuing another background rerun"
        )
    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": True,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": action_path,
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
            },
            "task": task_payload,
            "background_run": background_payload,
            "next_step": f"/orch status {project_ref}",
            "remediation": remediation,
            "outcome": {
                "kind": outcome_kind,
                "status": "blocked" if blocked else "executed",
                "reason_code": ("background_tmux_launch_failed" if selected_runner == "local_tmux" else "background_external_handoff_failed") if blocked else (
                    success_reason_code if selected_runner == "local_tmux" else f"background_{selected_runner}_handoff_started"
                ),
                "detail": detail,
            },
        },
        status=409 if blocked else 200,
    )



def _execute_retry_run_transition(
    transition: Dict[str, Any],
    *,
    config: DashboardAppConfig,
    manager_state: Dict[str, Any],
    paths: Any,
    source_command: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes]:
    import aoe_tg_run_handlers as run_handlers

    messages: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    source_task = transition.get("run_source_task") if isinstance(transition.get("run_source_task"), dict) else None
    args = _dashboard_run_args(config, source_task=source_task)
    ctx = run_handlers.build_run_context(
        cmd=str(transition.get("cmd", "run")).strip() or "run",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        text=str(transition.get("run_prompt", "")).strip(),
        rest=str(transition.get("rest", "")).strip(),
        orch_target=str(transition.get("orch_target", "")).strip() or None,
        run_prompt=str(transition.get("run_prompt", "")).strip(),
        run_roles_override=transition.get("run_roles_override"),
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=transition.get("run_no_wait_override"),
        run_force_mode=str(transition.get("run_force_mode", "")).strip() or None,
        run_auto_source="dashboard_retry",
        run_control_mode=str(transition.get("run_control_mode", "")).strip(),
        run_source_request_id=str(transition.get("run_source_request_id", "")).strip(),
        run_source_task=source_task,
        run_selected_execution_lane_ids=list(transition.get("run_selected_execution_lane_ids") or []),
        run_selected_review_lane_ids=list(transition.get("run_selected_review_lane_ids") or []),
    )
    deps = _build_dashboard_retry_run_deps(
        config=config,
        manager_state=manager_state,
        paths=paths,
        messages=messages,
        events=events,
        outcomes=outcomes,
    )
    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)
    if not handled:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "unhandled",
                "path": "/control/actions/task/retry",
                "source_command": source_command,
                "payload": payload,
                "messages": messages,
                "events": events,
                "remediation": "inspect the runtime task detail and retry contract before attempting another retry bridge",
            },
            status=500,
        )

    project_key = str(transition.get("orch_target", "")).strip()
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else {}
    executed_request_id = str(entry.get("last_request_id", "")).strip() if isinstance(entry, dict) else ""
    executed_task = gateway_task_state.get_task_record(entry, executed_request_id) if isinstance(entry, dict) and executed_request_id else None
    outcome = _latest_recorded_outcome(outcomes, kind="retry_run")
    if not outcome:
        return _missing_outcome_response(
            path="/control/actions/task/retry",
            source_command=source_command,
            payload=payload,
            kind="retry_run",
            messages=messages,
            events=events,
            remediation="inspect the retry handler contract; dashboard actions now require structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"
    task_payload = None
    if isinstance(executed_task, dict) and executed_request_id:
        task_payload = {
            "request_id": executed_request_id,
            "label": gateway_task_view.task_display_label(executed_task, fallback_request_id=executed_request_id),
            "status": str(executed_task.get("status", "")).strip() or "-",
            "tf_phase": str(executed_task.get("tf_phase", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{executed_request_id}",
        }
    next_step = str(outcome.get("next_step", "")).strip() or ("/offdesk review" if blocked else (f"/task {task_payload['label']}" if isinstance(task_payload, dict) else "/monitor"))
    remediation = "review the updated task detail and lane state before repeating another retry" if not blocked else "inspect the structured retry outcome and planning contract before repeating another retry"
    reason_code = str(outcome.get("reason_code", "")).strip() or "-"
    detail_note = str(outcome.get("detail", "")).strip()
    if blocked:
        next_step = _retry_blocked_next_step_for_reason(reason_code, entry=entry, fallback=next_step)
        remediation = _retry_blocked_remediation_for_reason(reason_code, detail_note)
    latest_judge = _latest_judge_summary_payload(team_dir=paths.team_dir, entry=entry) if blocked and isinstance(entry, dict) else {}
    latest_judge_decision = _latest_judge_decision_payload(team_dir=paths.team_dir, entry=entry) if blocked and isinstance(entry, dict) else {}
    latest_judge_decision_bridge: Dict[str, Any] = {}
    replan_auto_decision: Dict[str, Any] = {}
    replan_auto_routing_policy: Dict[str, Any] = {}
    if blocked:
        next_step, latest_judge_decision_bridge = _latest_judge_decision_bridge(
            next_step,
            latest_judge_decision=latest_judge_decision,
            source_command=source_command,
        )
        replan_auto_decision = _replan_auto_decision_stub(
            source_command=source_command,
            next_step=next_step,
            latest_judge_decision=latest_judge_decision,
            latest_judge_decision_bridge=latest_judge_decision_bridge,
            source_task=source_task if isinstance(source_task, dict) else executed_task,
        )
        replan_auto_routing_policy = _replan_auto_routing_policy(
            source_command=source_command,
            replan_auto_decision=replan_auto_decision,
        )
        if str(replan_auto_routing_policy.get("status", "")).strip() in {"manual_progressed", "mutation_progressed", "analysis_review_ready"}:
            progressed_next_step = str(replan_auto_routing_policy.get("suggested_next_step", "")).strip()
            if progressed_next_step.startswith("/"):
                next_step = progressed_next_step
        remediation = _retry_blocked_remediation_with_latest_judge(remediation, latest_judge)
        remediation = _retry_blocked_remediation_with_judge_bridge(remediation, latest_judge_decision_bridge)
        remediation = _retry_blocked_remediation_with_manual_feedback(remediation, replan_auto_decision)
        remediation = _retry_blocked_remediation_with_canonical_feedback(remediation, replan_auto_decision)
        remediation = _retry_blocked_remediation_with_analysis_feedback(remediation, replan_auto_decision)
        _append_blocked_retry_replan_audit(
            team_dir=paths.team_dir,
            entry=entry,
            source_command=source_command,
            blocked=blocked,
            reason_code=reason_code,
            detail=str(outcome.get("detail", "")).strip() if outcome else "-",
            next_step=next_step,
            remediation=remediation,
            latest_judge_decision_bridge=latest_judge_decision_bridge,
            replan_auto_decision=replan_auto_decision,
            replan_auto_routing_policy=replan_auto_routing_policy,
            now_iso=_now_iso,
        )
    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": True,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": "/control/actions/task/retry",
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
            },
            "messages": messages,
            "events": events,
            "outcome": {
                "kind": "retry_run",
                "status": "blocked" if blocked else "executed",
                "reason_code": reason_code,
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "task": task_payload,
            "next_step": next_step,
            "remediation": remediation,
            "latest_judge": latest_judge,
            "latest_judge_decision": latest_judge_decision,
            "latest_judge_decision_bridge": latest_judge_decision_bridge,
            "replan_auto_decision": replan_auto_decision,
            "replan_auto_routing_policy": replan_auto_routing_policy,
        },
        status=409 if blocked else 200,
    )


def _execute_followup_run_transition(
    transition: Dict[str, Any],
    *,
    config: DashboardAppConfig,
    manager_state: Dict[str, Any],
    paths: Any,
    source_command: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes]:
    import aoe_tg_run_handlers as run_handlers

    messages: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    source_task = transition.get("run_source_task") if isinstance(transition.get("run_source_task"), dict) else None
    args = _dashboard_run_args(config, source_task=source_task)
    ctx = run_handlers.build_run_context(
        cmd=str(transition.get("cmd", "run")).strip() or "run",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        text=str(transition.get("run_prompt", "")).strip(),
        rest=str(transition.get("rest", "")).strip(),
        orch_target=str(transition.get("orch_target", "")).strip() or None,
        run_prompt=str(transition.get("run_prompt", "")).strip(),
        run_roles_override=transition.get("run_roles_override"),
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=transition.get("run_no_wait_override"),
        run_force_mode=str(transition.get("run_force_mode", "")).strip() or None,
        run_auto_source="dashboard_followup_execute",
        run_control_mode=str(transition.get("run_control_mode", "")).strip(),
        run_source_request_id=str(transition.get("run_source_request_id", "")).strip(),
        run_source_task=source_task,
        run_selected_execution_lane_ids=list(transition.get("run_selected_execution_lane_ids") or []),
        run_selected_review_lane_ids=list(transition.get("run_selected_review_lane_ids") or []),
    )
    deps = _build_dashboard_retry_run_deps(
        config=config,
        manager_state=manager_state,
        paths=paths,
        messages=messages,
        events=events,
        outcomes=outcomes,
    )
    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)
    if not handled:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "unhandled",
                "path": "/control/actions/task/followup-execute",
                "source_command": source_command,
                "payload": payload,
                "messages": messages,
                "events": events,
                "remediation": "inspect the runtime task detail and follow-up execution contract before attempting the bridge again",
            },
            status=500,
        )

    project_key = str(transition.get("orch_target", "")).strip()
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else {}
    executed_request_id = str(entry.get("last_request_id", "")).strip() if isinstance(entry, dict) else ""
    executed_task = gateway_task_state.get_task_record(entry, executed_request_id) if isinstance(entry, dict) and executed_request_id else None
    outcome = _latest_recorded_outcome(outcomes, kind="retry_run")
    if not outcome:
        return _missing_outcome_response(
            path="/control/actions/task/followup-execute",
            source_command=source_command,
            payload=payload,
            kind="followup_execute",
            messages=messages,
            events=events,
            remediation="inspect the follow-up handler contract; dashboard follow-up execution requires structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"
    task_payload = None
    if isinstance(executed_task, dict) and executed_request_id:
        task_payload = {
            "request_id": executed_request_id,
            "label": gateway_task_view.task_display_label(executed_task, fallback_request_id=executed_request_id),
            "status": str(executed_task.get("status", "")).strip() or "-",
            "tf_phase": str(executed_task.get("tf_phase", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{executed_request_id}",
        }
    next_step = str(outcome.get("next_step", "")).strip() or (f"/task {task_payload['label']}" if isinstance(task_payload, dict) else "/monitor")
    reason_code = str(outcome.get("reason_code", "")).strip() or "-"
    detail_note = str(outcome.get("detail", "")).strip()
    remediation = (
        "review the updated task detail and remaining preview-only follow-up slice before repeating follow-up execution"
        if not blocked
        else _retry_blocked_remediation_for_reason(reason_code, detail_note)
    )
    if isinstance(source_task, dict):
        persist_manual_step_execution_state(
            source_task,
            manual_kind="manual_execute",
            source_command=source_command or "/followup-exec",
            state="blocked" if blocked else "executed",
            next_step=next_step,
            at=_now_iso(),
        )
        gateway_main = _load_gateway_main_module()
        gateway_main.save_manager_state(paths.manager_state_file, manager_state)
    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": True,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": "/control/actions/task/followup-execute",
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
            },
            "messages": messages,
            "events": events,
            "outcome": {
                "kind": "followup_execute",
                "status": "blocked" if blocked else "executed",
                "reason_code": reason_code,
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "task": task_payload,
            "next_step": next_step,
            "remediation": remediation,
        },
        status=409 if blocked else 200,
    )

def _execute_retry_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    command_text = str(spec.get("command", "")).strip()
    is_replan = command_text.startswith("/replan ")
    paths, manager_state = _load_dashboard_manager_state(config)
    project_key = _find_task_project_key(manager_state, task_ref)
    if not project_key:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if isinstance(projects.get(project_key), dict) else {}
    source_request_id = gateway_task_state.resolve_task_request_id(entry, task_ref) if isinstance(entry, dict) else ""
    source_task = gateway_task_state.get_task_record(entry, source_request_id) if isinstance(entry, dict) and source_request_id else None

    messages: List[Dict[str, Any]] = []
    transition = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-replan" if is_replan else "orch-retry",
        args=_dashboard_action_args(config),
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        orch_target=project_key,
        orch_retry_request_id=None if is_replan else task_ref,
        orch_replan_request_id=task_ref if is_replan else None,
        orch_followup_execute_request_id=None,
        orch_retry_lane_ids=[] if is_replan else list(payload.get("lane_ids") or []),
        orch_replan_lane_ids=list(payload.get("lane_ids") or []) if is_replan else None,
        orch_followup_execute_lane_ids=None,
        send=_make_send_collector(messages),
        get_context=_dashboard_get_context_factory(manager_state, paths),
        get_chat_selected_task_ref=chat_state.get_chat_selected_task_ref,
        resolve_chat_task_ref=chat_state.resolve_chat_task_ref,
        resolve_task_request_id=gateway_task_state.resolve_task_request_id,
        get_task_record=gateway_task_state.get_task_record,
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=gateway_task_view.dedupe_roles,
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
    )
    if not isinstance(transition, dict):
        return _json(
            {
                "ok": False,
                "error": "retry_transition_unavailable",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect the task lifecycle first; retry transition could not be derived from the current runtime state",
            },
            status=500,
        )

    if bool(transition.get("terminal")):
        blocked_contexts = [str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()]
        latest_judge = _latest_judge_summary_payload(team_dir=paths.team_dir, entry=entry) if isinstance(entry, dict) else {}
        latest_judge_decision = _latest_judge_decision_payload(team_dir=paths.team_dir, entry=entry) if isinstance(entry, dict) else {}
        error_code = (
            "followup_execute_brief_required"
            if "orch-followup-exec blocked" in blocked_contexts
            else "followup_execute_blocked"
        )
        next_step = _retry_blocked_next_step_for_contexts(
            blocked_contexts,
            entry=entry,
            fallback="/offdesk review",
        )
        next_step, latest_judge_decision_bridge = _latest_judge_decision_bridge(
            next_step,
            latest_judge_decision=latest_judge_decision,
            source_command=str(spec.get("command", "-")),
        )
        replan_auto_decision = _replan_auto_decision_stub(
            source_command=str(spec.get("command", "-")),
            next_step=next_step,
            latest_judge_decision=latest_judge_decision,
            latest_judge_decision_bridge=latest_judge_decision_bridge,
            source_task=source_task if isinstance(source_task, dict) else {},
        )
        replan_auto_routing_policy = _replan_auto_routing_policy(
            source_command=str(spec.get("command", "-")),
            replan_auto_decision=replan_auto_decision,
        )
        if str(replan_auto_routing_policy.get("status", "")).strip() in {"manual_progressed", "mutation_progressed", "analysis_review_ready"}:
            progressed_next_step = str(replan_auto_routing_policy.get("suggested_next_step", "")).strip()
            if progressed_next_step.startswith("/"):
                next_step = progressed_next_step
        remediation = _retry_blocked_remediation_with_latest_judge(
            _retry_blocked_remediation([str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()]),
            latest_judge,
        )
        remediation = _retry_blocked_remediation_with_judge_bridge(remediation, latest_judge_decision_bridge)
        remediation = _retry_blocked_remediation_with_manual_feedback(remediation, replan_auto_decision)
        remediation = _retry_blocked_remediation_with_canonical_feedback(remediation, replan_auto_decision)
        remediation = _retry_blocked_remediation_with_analysis_feedback(remediation, replan_auto_decision)
        if (
            is_replan
            and _payload_bool(payload, "auto_route_apply")
            and str(replan_auto_routing_policy.get("status", "")).strip() == "ready"
            and str(replan_auto_routing_policy.get("suggested_action", "")).strip() == "retry"
        ):
            lane_ids = _normalize_lane_ids(payload.get("lane_ids"))
            retry_command = _retry_command_text_from_policy(
                task_ref=task_ref,
                lane_ids=lane_ids,
                policy=replan_auto_routing_policy,
            )
            _append_replan_auto_route_audit(
                team_dir=paths.team_dir,
                entry=entry,
                source_command=str(spec.get("command", "-")),
                retry_command=retry_command,
                policy=replan_auto_routing_policy,
                now_iso=_now_iso,
            )
            retry_payload: Dict[str, Any] = {
                "task_ref": task_ref,
                "auto_route_source": "replan_auto_routing_policy",
            }
            if lane_ids:
                retry_payload["lane_ids"] = lane_ids
            retry_response = _execute_retry_action(
                {
                    "path": "/control/actions/task/retry",
                    "mode": spec.get("mode", "-"),
                    "command": retry_command,
                    "payload": retry_payload,
                },
                config=config,
            )
            return _json_with_extra_fields(
                retry_response,
                extra={
                    "auto_route_applied": True,
                    "auto_routed_from": str(spec.get("command", "-")),
                    "auto_route_policy_source": "replan_auto_routing_policy",
                    "replan_auto_routing_policy": replan_auto_routing_policy,
                },
            )
        _append_blocked_retry_replan_audit(
            team_dir=paths.team_dir,
            entry=entry,
            source_command=str(spec.get("command", "-")),
            blocked=True,
            reason_code=error_code,
            detail="; ".join(str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()) or "-",
            next_step=next_step,
            remediation=remediation,
            latest_judge_decision_bridge=latest_judge_decision_bridge,
            replan_auto_decision=replan_auto_decision,
            replan_auto_routing_policy=replan_auto_routing_policy,
            now_iso=_now_iso,
        )
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "error": error_code,
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "messages": messages,
                "next_step": next_step,
                "remediation": remediation,
                "latest_judge": latest_judge,
                "latest_judge_decision": latest_judge_decision,
                "latest_judge_decision_bridge": latest_judge_decision_bridge,
                "replan_auto_decision": replan_auto_decision,
                "replan_auto_routing_policy": replan_auto_routing_policy,
            },
            status=409,
        )
    if isinstance(projects.get(project_key), dict):
        run_lock_response = _run_lock_block_response(
            entry=projects.get(project_key),
            transition=transition,
            source_command=command_text or ("/replan" if is_replan else "/retry"),
            payload=payload,
            path=str(spec.get("path", "")).strip() or "/control/actions/task/retry",
        )
        if run_lock_response is not None:
            return run_lock_response
    background_result = _maybe_execute_retry_background_runner(
        transition,
        manager_state=manager_state,
        paths=paths,
        source_command=str(spec.get("command", "")).strip() or "/retry",
        payload=payload,
    )
    if background_result is not None:
        return background_result
    import sys

    compatibility_module = sys.modules.get("control_dashboard")
    execute_retry = getattr(compatibility_module, "_execute_retry_run_transition", _execute_retry_run_transition)
    return execute_retry(
        transition,
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command=command_text or ("/replan" if is_replan else "/retry"),
        payload=payload,
    )


def _execute_followup_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    command_text = str(spec.get("command", "")).strip()
    paths, manager_state = _load_dashboard_manager_state(config)
    project_key = _find_task_project_key(manager_state, task_ref)
    if not project_key:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else {}
    source_request_id = gateway_task_state.resolve_task_request_id(entry, task_ref) if isinstance(entry, dict) else ""
    source_task = gateway_task_state.get_task_record(entry, source_request_id) if isinstance(entry, dict) and source_request_id else None
    task_payload = None
    if isinstance(source_task, dict) and source_request_id:
        task_payload = {
            "project_alias": str(entry.get("project_alias", "")).strip() or project_key,
            "request_id": source_request_id,
            "label": gateway_task_view.task_display_label(source_task, fallback_request_id=source_request_id),
            "status": str(source_task.get("status", "")).strip() or "-",
            "tf_phase": str(source_task.get("tf_phase", "")).strip() or "-",
            "followup_brief_status": str(source_task.get("followup_brief_status", "")).strip() or "-",
            "followup_brief_summary": str(source_task.get("followup_brief_summary", "")).strip() or "-",
            "followup_brief_execution_lanes": ",".join(list(source_task.get("followup_brief_execution_lane_ids") or [])) or "-",
            "followup_brief_review_lanes": ",".join(list(source_task.get("followup_brief_review_lane_ids") or [])) or "-",
            "followup_brief_reason": str(source_task.get("followup_brief_reason", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{source_request_id}",
            "runtime_path": f"/control/runtimes/{str(entry.get('project_alias', '')).strip() or project_key}",
        }

    messages: List[Dict[str, Any]] = []
    transition = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-followup-exec",
        args=_dashboard_action_args(config),
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        orch_target=project_key,
        orch_retry_request_id=None,
        orch_replan_request_id=None,
        orch_followup_execute_request_id=task_ref,
        orch_retry_lane_ids=None,
        orch_replan_lane_ids=None,
        orch_followup_execute_lane_ids=list(payload.get("lane_ids") or []),
        send=_make_send_collector(messages),
        get_context=_dashboard_get_context_factory(manager_state, paths),
        get_chat_selected_task_ref=chat_state.get_chat_selected_task_ref,
        resolve_chat_task_ref=chat_state.resolve_chat_task_ref,
        resolve_task_request_id=gateway_task_state.resolve_task_request_id,
        get_task_record=gateway_task_state.get_task_record,
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=gateway_task_view.dedupe_roles,
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
    )
    if not isinstance(transition, dict):
        return _json(
            {
                "ok": False,
                "error": "followup_execute_transition_unavailable",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect the FollowupBrief and task lifecycle; follow-up transition could not be derived from the current runtime state",
            },
            status=500,
        )

    if bool(transition.get("terminal")):
        blocked_contexts = [str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()]
        error_code = (
            "followup_execute_brief_required"
            if "orch-followup-exec blocked" in blocked_contexts
            else "followup_execute_blocked"
        )
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "error": error_code,
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "messages": messages,
                "next_step": f"/followup {task_ref}",
                "remediation": "derive an explicit executable FollowupBrief before off-desk execution; current /followup remains a safe preview only",
                "task": task_payload,
            },
            status=409,
        )
    if isinstance(entry, dict):
        run_lock_response = _run_lock_block_response(
            entry=entry,
            transition=transition,
            source_command=command_text or "/followup-exec",
            payload=payload,
            path=str(spec.get("path", "")).strip() or "/control/actions/task/followup-execute",
        )
        if run_lock_response is not None:
            return run_lock_response
    background_result = _maybe_execute_retry_background_runner(
        transition,
        manager_state=manager_state,
        paths=paths,
        source_command=command_text or "/followup-exec",
        payload=payload,
    )
    if background_result is not None:
        return background_result
    import sys

    compatibility_module = sys.modules.get("control_dashboard")
    execute_followup = getattr(compatibility_module, "_execute_followup_run_transition", _execute_followup_run_transition)
    return execute_followup(
        transition,
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command=command_text or "/followup-exec",
        payload=payload,
    )
