#!/usr/bin/env python3
"""Shared task/offdesk priority action helpers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from aoe_tg_orch_contract import normalize_tf_phase


def external_background_priority_action_snapshot(
    *,
    alias: str,
    task_label: str = "",
    background_run_runner_target: str = "",
    background_run_external_phase: str = "",
    background_run_external_note: str = "",
) -> Dict[str, str]:
    project_alias = str(alias or "").strip()
    runner = str(background_run_runner_target or "").strip().lower()
    phase = str(background_run_external_phase or "").strip().lower()
    note = str(background_run_external_note or "").strip()
    label = str(task_label or "").strip()

    if runner not in {"github_runner", "remote_worker"}:
        return {"action": "", "reason": ""}
    if phase == "result_received":
        return {
            "action": f"/offdesk review {project_alias}" if project_alias else (f"/task {label}" if label else ""),
            "reason": note or f"{runner} result received; inspect task state and decide the next operator action",
        }
    if phase == "pickup_acknowledged":
        return {
            "action": f"/orch bgx-status {project_alias}" if project_alias else "",
            "reason": note or f"{runner} picked up the background run; await result sidecar",
        }
    if phase == "handoff_emitted":
        return {
            "action": f"/orch bgx-status {project_alias}" if project_alias else "",
            "reason": note or f"{runner} handoff emitted; awaiting pickup acknowledgement",
        }
    if phase == "awaiting_external_pickup":
        return {
            "action": f"/orch bgx-status {project_alias}" if project_alias else "",
            "reason": note or f"{runner} launch handed off; waiting for external pickup",
        }
    return {
        "action": f"/orch status {project_alias}" if project_alias and (phase or note) else "",
        "reason": note or (f"{runner} external background lifecycle requires operator status review" if runner and (phase or note) else ""),
    }


def task_lane_target_snapshot(task: Dict[str, Any]) -> Dict[str, List[str]]:
    exec_critic = task.get("exec_critic") if isinstance(task.get("exec_critic"), dict) else {}
    return {
        "rerun_execution_lane_ids": [str(x).strip()[:32] for x in (exec_critic.get("rerun_execution_lane_ids") or []) if str(x).strip()],
        "rerun_review_lane_ids": [str(x).strip()[:32] for x in (exec_critic.get("rerun_review_lane_ids") or []) if str(x).strip()],
        "manual_followup_execution_lane_ids": [
            str(x).strip()[:32] for x in (exec_critic.get("manual_followup_execution_lane_ids") or []) if str(x).strip()
        ],
        "manual_followup_review_lane_ids": [
            str(x).strip()[:32] for x in (exec_critic.get("manual_followup_review_lane_ids") or []) if str(x).strip()
        ],
    }


def task_priority_action_snapshot(
    *,
    label: str,
    tf_phase: str,
    rerun_execution_lane_ids: List[str],
    rerun_review_lane_ids: List[str],
    manual_followup_execution_lane_ids: List[str],
    manual_followup_review_lane_ids: List[str],
    rate_limit: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    safe_label = str(label or "").strip()
    safe_phase = normalize_tf_phase(tf_phase, "queued")
    rerun_exec = [str(x).strip() for x in rerun_execution_lane_ids if str(x).strip()]
    rerun_review = [str(x).strip() for x in rerun_review_lane_ids if str(x).strip()]
    manual_exec = [str(x).strip() for x in manual_followup_execution_lane_ids if str(x).strip()]
    manual_review = [str(x).strip() for x in manual_followup_review_lane_ids if str(x).strip()]

    if safe_phase not in {"needs_retry", "critic_review", "manual_intervention", "blocked"}:
        if rerun_exec or rerun_review:
            safe_phase = "needs_retry"
        elif manual_exec or manual_review:
            safe_phase = "manual_intervention"

    def _lane_suffix(exec_ids: List[str], review_ids: List[str]) -> str:
        lane_tokens = [str(x).strip() for x in exec_ids + review_ids if str(x).strip()]
        if not lane_tokens:
            return ""
        return " lane " + ",".join(lane_tokens)

    if safe_label and safe_phase == "planning":
        return {
            "action": f"/task {safe_label}",
            "reason": "active task is still planning",
        }

    if safe_label and safe_phase == "rate_limited":
        retry_at = str((rate_limit or {}).get("retry_at", "")).strip() if isinstance(rate_limit, dict) else ""
        return {
            "action": f"/task {safe_label}",
            "reason": (
                f"active task is waiting for provider capacity until {retry_at}"
                if retry_at
                else "active task is waiting for provider capacity"
            ),
        }

    if safe_label and safe_phase in {"needs_retry", "critic_review"}:
        lane_bits: List[str] = []
        if rerun_exec:
            lane_bits.append("execution=" + ",".join(rerun_exec))
        if rerun_review:
            lane_bits.append("review=" + ",".join(rerun_review))
        suffix = f" target {'; '.join(lane_bits)}" if lane_bits else ""
        return {
            "action": f"/retry {safe_label}{_lane_suffix(rerun_exec, rerun_review)}",
            "reason": f"active task requires retry ({safe_phase}){suffix}",
        }

    if safe_label and safe_phase in {"manual_intervention", "blocked"}:
        lane_bits: List[str] = []
        if manual_exec:
            lane_bits.append("execution=" + ",".join(manual_exec))
        if manual_review:
            lane_bits.append("review=" + ",".join(manual_review))
        suffix = f" target {'; '.join(lane_bits)}" if lane_bits else ""
        return {
            "action": f"/followup {safe_label}{_lane_suffix(manual_exec, manual_review)}",
            "reason": f"active task requires operator review ({safe_phase}){suffix}",
        }

    return {"action": "", "reason": ""}


def offdesk_priority_action_snapshot(
    *,
    alias: str,
    active_task_label: str,
    active_task_tf_phase: str,
    active_task_execution_brief_status: str = "",
    active_task_execution_brief_blocked_slice: Optional[List[str]] = None,
    active_task_execution_brief_operator_decision: str = "",
    active_task_targets: Optional[Dict[str, List[str]]] = None,
    active_task_rate_limit: Optional[Dict[str, Any]] = None,
    syncback_pending: bool,
    followup_count: int,
    proposal_count: int,
    bootstrap_recommended: bool,
    blocked_count: int,
    open_count: int,
    sync_quality: str,
    sync_quality_warn: bool,
    sync_stale: bool,
    canonical_exists: bool,
    include_ok: bool,
    last_sync_mode: str,
    background_queue_depth: int = 0,
    background_queue_stale_count: int = 0,
    background_queue_runner_targets: Optional[Dict[str, int]] = None,
    background_scheduler_summary: str = "",
    background_worker_status: str = "",
    background_worker_summary: str = "",
    run_lock_mode: str = "",
    run_lock_note: str = "",
    background_slot_runner_target: str = "",
    background_slot_limit: int = 1,
    background_slot_active: int = 0,
    active_task_background_run_status: str = "",
    active_task_background_run_runner_target: str = "",
    active_task_background_run_external_phase: str = "",
    active_task_background_run_external_note: str = "",
) -> Dict[str, str]:
    brief_status = str(active_task_execution_brief_status or "").strip().lower()
    brief_blocked = [str(x).strip() for x in (active_task_execution_brief_blocked_slice or []) if str(x).strip()]
    brief_decision = str(active_task_execution_brief_operator_decision or "").strip()
    background_status = str(active_task_background_run_status or "").strip().lower()
    background_runner = str(active_task_background_run_runner_target or "").strip().lower()
    background_external_phase = str(active_task_background_run_external_phase or "").strip().lower()
    background_external_note = str(active_task_background_run_external_note or "").strip()
    worker_status = str(background_worker_status or "").strip().lower()
    worker_summary = str(background_worker_summary or "").strip()
    lock_mode = str(run_lock_mode or "").strip().lower()
    lock_note = str(run_lock_note or "").strip()
    slot_runner = str(background_slot_runner_target or "").strip().lower()
    slot_limit = max(1, int(background_slot_limit or 1))
    slot_active = max(0, int(background_slot_active or 0))
    slot_saturated = slot_runner in {"local_tmux", "github_runner", "remote_worker"} and slot_active >= slot_limit
    queue_targets = background_queue_runner_targets if isinstance(background_queue_runner_targets, dict) else {}
    local_background_queue_depth = max(0, int(queue_targets.get("local_background", 0) or 0))
    if brief_status in {"underspecified", "operator_decision_required", "infeasible"}:
        reason = brief_decision
        if not reason and brief_blocked:
            reason = f"execution brief blocked by {', '.join(brief_blocked[:4])}"
        if not reason:
            reason = f"execution brief is {brief_status}"
        return {
            "action": f"/offdesk review {alias}",
            "reason": reason,
        }
    if background_runner in {"github_runner", "remote_worker"} and background_status in {"queued", "dispatching", "running"}:
        external_priority = external_background_priority_action_snapshot(
            alias=alias,
            task_label=active_task_label,
            background_run_runner_target=background_runner,
            background_run_external_phase=background_external_phase,
            background_run_external_note=background_external_note,
        )
        if str(external_priority.get("action", "")).strip():
            return external_priority
    if worker_status in {"stale", "error"}:
        reason = worker_summary or f"background worker is {worker_status}"
        return {
            "action": f"/orch bgw-status {alias}",
            "reason": reason,
        }
    if int(background_queue_stale_count or 0) > 0:
        return {
            "action": f"/offdesk review {alias}",
            "reason": f"background queue contains stale tickets ({int(background_queue_stale_count or 0)})",
        }

    task_priority = task_priority_action_snapshot(
        label=active_task_label,
        tf_phase=active_task_tf_phase,
        rerun_execution_lane_ids=list((active_task_targets or {}).get("rerun_execution_lane_ids") or []),
        rerun_review_lane_ids=list((active_task_targets or {}).get("rerun_review_lane_ids") or []),
        manual_followup_execution_lane_ids=list((active_task_targets or {}).get("manual_followup_execution_lane_ids") or []),
        manual_followup_review_lane_ids=list((active_task_targets or {}).get("manual_followup_review_lane_ids") or []),
        rate_limit=active_task_rate_limit if isinstance(active_task_rate_limit, dict) else None,
    )
    task_action = str(task_priority.get("action", "")).strip()
    task_requires_launch = task_action.startswith("/retry ")
    if task_requires_launch and lock_mode == "test_only":
        return {
            "action": f"/orch status {alias}",
            "reason": lock_note or "test_only run lock is active",
        }
    if task_requires_launch and slot_saturated:
        reason = f"background runner slots are saturated ({slot_active}/{slot_limit})"
        if slot_runner:
            reason = f"background runner slots are saturated for {slot_runner} ({slot_active}/{slot_limit})"
        return {
            "action": f"/orch status {alias}",
            "reason": reason,
        }
    if task_action:
        return task_priority
    if lock_mode == "test_only":
        return {
            "action": f"/orch status {alias}",
            "reason": lock_note or "test_only run lock is active",
        }
    if slot_saturated:
        reason = f"background runner slots are saturated ({slot_active}/{slot_limit})"
        if slot_runner:
            reason = f"background runner slots are saturated for {slot_runner} ({slot_active}/{slot_limit})"
        return {
            "action": f"/orch status {alias}",
            "reason": reason,
        }
    if int(background_queue_depth or 0) > 0:
        target_summary = ",".join(
            f"{str(key).strip()}={int(value or 0)}"
            for key, value in sorted(queue_targets.items())
            if str(key).strip() and int(value or 0) > 0
        )
        if local_background_queue_depth > 0 and worker_status in {"", "-", "stopped"}:
            reason = f"background queue has {int(background_queue_depth or 0)} queued/running tickets"
            if target_summary:
                reason += f" ({target_summary})"
            reason += "; local background worker is stopped"
            return {
                "action": f"/orch bgw-start {alias}",
                "reason": reason,
            }
        reason = f"background queue has {int(background_queue_depth or 0)} queued/running tickets"
        if target_summary:
            reason += f" ({target_summary})"
        return {
            "action": f"/orch status {alias}",
            "reason": reason,
        }
    if syncback_pending:
        return {
            "action": f"/todo {alias} syncback preview",
            "reason": "canonical TODO drift pending syncback",
        }
    if followup_count > 0:
        return {
            "action": f"/todo {alias} followup",
            "reason": "manual follow-up backlog pending review",
        }
    if proposal_count > 0:
        return {
            "action": f"/todo {alias} proposals",
            "reason": "open todo proposals pending triage",
        }
    if bootstrap_recommended:
        if not canonical_exists:
            return {
                "action": f"/sync bootstrap {alias} 24h",
                "reason": "bootstrap backlog because canonical TODO.md is missing",
            }
        if canonical_exists and not include_ok:
            return {
                "action": f"/sync bootstrap {alias} 24h",
                "reason": "bootstrap backlog because AOE_TODO.md is not linked to canonical TODO.md",
            }
        if last_sync_mode == "never":
            return {
                "action": f"/sync bootstrap {alias} 24h",
                "reason": "bootstrap backlog because the project has never been synced",
            }
        if sync_quality in {"non_backlog_docs", "unknown"}:
            return {
                "action": f"/sync bootstrap {alias} 24h",
                "reason": f"rebuild backlog from canonical/recent docs; last sync source was {sync_quality}",
            }
        if sync_stale:
            return {
                "action": f"/sync bootstrap {alias} 24h",
                "reason": "refresh stale backlog from canonical/recent project documents",
            }
        return {
            "action": f"/sync bootstrap {alias} 24h",
            "reason": "bootstrap backlog from recent project documents",
        }
    if sync_quality_warn:
        if sync_quality == "discovery":
            return {
                "action": f"/sync preview {alias} 24h",
                "reason": "inspect non-canonical discovery sources before execution",
            }
        if sync_quality == "mixed":
            return {
                "action": f"/sync preview {alias} 24h",
                "reason": "inspect mixed canonical and discovery sync sources before execution",
            }
    if blocked_count > 0 or open_count == 0 or sync_quality_warn:
        if open_count == 0:
            return {
                "action": f"/sync preview {alias} 24h",
                "reason": "review sync sources because there is no runnable backlog",
            }
        if blocked_count > 0:
            return {
                "action": f"/sync preview {alias} 24h",
                "reason": "review sync sources before retrying blocked backlog",
            }
        return {
            "action": f"/sync preview {alias} 24h",
            "reason": "review sync source quality before execution",
        }
    return {
        "action": f"/orch status {alias}",
        "reason": "inspect current project runtime and queue state",
    }
