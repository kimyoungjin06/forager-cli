#!/usr/bin/env python3
"""Read-only helpers for dashboard action audit state."""

from __future__ import annotations

import fcntl
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from aoe_tg_runtime_core import action_audit_path as runtime_action_audit_path

ACTION_AUDIT_DIRNAME = "dashboard"
ACTION_AUDIT_FILENAME = "action-history.jsonl"


def _action_audit_now() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def compact_action_text(raw: Any, limit: int = 120) -> str:
    text = " ".join(str(raw or "").strip().split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def load_latest_action_audit(team_dir: Any) -> Dict[str, str]:
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    return _normalize_latest_action_row(rows[-1])


def _action_audit_path(team_dir: Any) -> Path:
    token = str(team_dir or "").strip()
    return runtime_action_audit_path(token)


def _normalize_latest_action_row(row: Dict[str, Any]) -> Dict[str, str]:
    return {
        "headline": str(row.get("headline", "")).strip() or "-",
        "headline_summary": summarize_action_audit_headline(row),
        "status": str(row.get("status", "")).strip() or "unknown",
        "outcome_kind": str(row.get("outcome_kind", "")).strip() or "-",
        "outcome_status": str(row.get("outcome_status", "")).strip() or str(row.get("status", "")).strip() or "unknown",
        "outcome_reason_code": str(row.get("outcome_reason_code", "")).strip() or "-",
        "outcome_detail": str(row.get("outcome_detail", "")).strip() or "-",
        "next_step": str(row.get("next_step", "")).strip() or "-",
        "remediation": str(row.get("remediation", "")).strip() or "-",
        "source_command": str(row.get("source_command", "")).strip() or "-",
    }


def _parse_json_object_from_text(text: Any) -> Dict[str, Any]:
    src = str(text or "").strip()
    if not src:
        return {}
    try:
        obj = json.loads(src)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(src):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(src[idx:])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _classify_canonical_mutation_from_counts(
    *,
    path: str,
    line_count: int,
    done_count: int,
    reopen_count: int,
    append_count: int,
    blocked_count: int,
) -> Dict[str, Any]:
    path_token = Path(str(path or "").strip()).name
    path_upper = path_token.upper()
    if path_upper in {"TODO", "TODO.MD", "TODO.TXT"} or path_upper.startswith("TODO."):
        kind = "todo_syncback"
    elif path_token.lower().endswith(".md"):
        kind = "markdown_syncback"
    else:
        kind = "artifact_syncback"
    counts = {
        "done": max(0, int(done_count or 0)),
        "reopen": max(0, int(reopen_count or 0)),
        "append": max(0, int(append_count or 0)),
        "blocked": max(0, int(blocked_count or 0)),
    }
    positive = [name for name, value in counts.items() if value > 0]
    if not positive:
        profile = "line_only" if max(0, int(line_count or 0)) > 0 else "noop"
    elif positive == ["done"]:
        profile = "done_only"
    elif positive == ["reopen"]:
        profile = "reopen_only"
    elif positive == ["append"]:
        profile = "append_only"
    elif positive == ["blocked"]:
        profile = "blocked_only"
    elif positive == ["append", "done"] or positive == ["done", "append"]:
        profile = "append_done"
    elif positive == ["append", "reopen"] or positive == ["reopen", "append"]:
        profile = "append_reopen"
    elif positive == ["done", "reopen"] or positive == ["reopen", "done"]:
        profile = "done_reopen"
    elif positive == ["append", "blocked"] or positive == ["blocked", "append"]:
        profile = "append_blocked"
    elif positive == ["done", "blocked"] or positive == ["blocked", "done"]:
        profile = "done_blocked"
    else:
        profile = "mixed"
    return {
        "kind": kind,
        "profile": profile,
        "path": path_token or "-",
        "line_count": max(0, int(line_count or 0)),
        "done_count": counts["done"],
        "reopen_count": counts["reopen"],
        "append_count": counts["append"],
        "blocked_count": counts["blocked"],
    }


def _judge_recommended_action(next_step: str, verdict: str) -> str:
    step = str(next_step or "").strip().lower()
    if step.startswith("/replan "):
        return "replan"
    if step.startswith("/retry "):
        return "retry"
    if step.startswith("/followup "):
        return "followup"
    if step.startswith("/followup-exec "):
        return "followup_execute"
    if step.startswith("/offdesk review") or step.startswith("/orch judge "):
        return "manual_review"
    token = str(verdict or "").strip().lower()
    if token in {"continue", "retry", "replan", "escalate", "hold"}:
        return token
    return "review"


def _normalize_offdesk_judge_record_set_records(raw: Any) -> list[Dict[str, str]]:
    if not isinstance(raw, list):
        return []
    normalized: list[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        label = str(item.get("label", "")).strip()
        state = str(item.get("state", "")).strip()
        note = str(item.get("note", "")).strip()
        if not any((kind, label, state, note)):
            continue
        normalized.append(
            {
                "kind": kind or "-",
                "label": label or "-",
                "state": state or "-",
                "note": note or "-",
            }
        )
        if len(normalized) >= 8:
            break
    return normalized


def normalize_offdesk_judge_decision(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return {}
    verdict = str(row.get("verdict", "")).strip() or "-"
    confidence = str(row.get("confidence", "")).strip() or "-"
    reasoning = str(row.get("reasoning", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    caution = str(row.get("caution", "")).strip() or "-"
    payload: Dict[str, Any] = {
        "verdict": verdict,
        "confidence": confidence,
        "reasoning": reasoning,
        "next_step": next_step,
        "caution": caution,
        "recommended_action": _judge_recommended_action(next_step, verdict),
    }
    worker_module = str(row.get("worker_module", "")).strip() or "-"
    worker_record_set = str(row.get("worker_record_set", "")).strip() or "-"
    worker_record_set_records = _normalize_offdesk_judge_record_set_records(row.get("worker_record_set_records"))
    analysis_record_set = str(row.get("analysis_record_set", "")).strip() or "-"
    analysis_record_set_records = _normalize_offdesk_judge_record_set_records(row.get("analysis_record_set_records"))
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


def summarize_offdesk_judge_decision(decision: Any) -> str:
    row = decision if isinstance(decision, dict) else normalize_offdesk_judge_decision(decision)
    if not isinstance(row, dict) or not row:
        return "-"
    action = str(row.get("recommended_action", "")).strip() or "-"
    verdict = str(row.get("verdict", "")).strip() or "-"
    confidence = str(row.get("confidence", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    reasoning = str(row.get("reasoning", "")).strip() or "-"
    parts = [f"action={action}", f"verdict={verdict}"]
    if confidence != "-":
        parts.append(f"confidence={confidence}")
    if next_step != "-":
        parts.append(f"next={next_step}")
    if reasoning != "-":
        parts.append(reasoning)
    return " | ".join(parts) if parts else "-"


def normalize_latest_judge_decision_bridge(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return {}
    return {
        "source": str(row.get("source", "")).strip() or "latest_offdesk_judge",
        "verdict": str(row.get("verdict", "")).strip() or "-",
        "confidence": str(row.get("confidence", "")).strip() or "-",
        "recommended_action": str(row.get("recommended_action", "")).strip() or "-",
        "reasoning": str(row.get("reasoning", "")).strip() or "-",
        "caution": str(row.get("caution", "")).strip() or "-",
        "candidate_next_step": str(row.get("candidate_next_step", "")).strip() or "-",
        "applied": bool(row.get("applied", False)),
        "applied_next_step": str(row.get("applied_next_step", "")).strip() or "-",
        "decision_mode": str(row.get("decision_mode", "")).strip() or ("promoted_next_step" if bool(row.get("applied", False)) else "observe_only"),
        "supports_auto_decision": bool(row.get("supports_auto_decision", False)),
    }


def summarize_latest_judge_decision_bridge(bridge: Any) -> str:
    row = bridge if isinstance(bridge, dict) else normalize_latest_judge_decision_bridge(bridge)
    if not isinstance(row, dict) or not row:
        return "-"
    action = str(row.get("recommended_action", "")).strip() or "-"
    verdict = str(row.get("verdict", "")).strip() or "-"
    confidence = str(row.get("confidence", "")).strip() or "-"
    decision_mode = str(row.get("decision_mode", "")).strip() or "-"
    next_step = (
        str(row.get("applied_next_step", "")).strip()
        if bool(row.get("applied", False))
        else str(row.get("candidate_next_step", "")).strip()
    ) or "-"
    parts = [f"mode={decision_mode}", f"action={action}", f"verdict={verdict}"]
    if confidence != "-":
        parts.append(f"confidence={confidence}")
    if next_step != "-":
        parts.append(f"next={next_step}")
    if bool(row.get("supports_auto_decision", False)):
        parts.append("auto=yes")
    return " | ".join(parts)


def _normalize_debug_packet_snapshot_for_audit(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    state = str(raw.get("state", "")).strip().lower()
    if state not in {"clean", "watch", "blocked", "active"}:
        return {}
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", "")).strip() or "2026-04-16.v1",
        "state": state,
    }
    for key in ("summary", "symptom", "root_cause", "failed_attempt", "next_step"):
        token = str(raw.get(key, "")).strip()
        if token:
            snapshot[key] = token[:320 if key == "summary" else 240]
    evidence = [
        str(item).strip()[:160]
        for item in (raw.get("evidence") or [])
        if str(item).strip()
    ]
    if evidence:
        snapshot["evidence"] = evidence[:10]
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


def _normalize_job_contract_snapshot_for_audit(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    status = str(raw.get("status", "")).strip().lower()
    planning_mode = str(raw.get("planning_mode", "")).strip().lower()
    summary = str(raw.get("summary", "")).strip()
    goal = str(raw.get("goal", "")).strip()
    rollback_hint = str(raw.get("rollback_hint", "")).strip()
    rows = {
        "scope": [str(item).strip()[:160] for item in (raw.get("scope") or []) if str(item).strip()],
        "non_goals": [str(item).strip()[:160] for item in (raw.get("non_goals") or []) if str(item).strip()],
        "risks": [str(item).strip()[:160] for item in (raw.get("risks") or []) if str(item).strip()],
        "acceptance_checks": [
            str(item).strip()[:160] for item in (raw.get("acceptance_checks") or []) if str(item).strip()
        ],
        "artifacts_to_touch": [
            str(item).strip()[:160] for item in (raw.get("artifacts_to_touch") or []) if str(item).strip()
        ],
    }
    if not any(
        (
            status,
            planning_mode,
            summary,
            goal,
            rollback_hint,
            *rows.values(),
        )
    ):
        return {}
    if status not in {"ready", "blocked"}:
        status = "ready"
    if planning_mode not in {"standard", "lightweight", "full"}:
        planning_mode = "standard"
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", "")).strip() or "2026-04-16.v1",
        "status": status or "ready",
        "planning_mode": planning_mode or "standard",
    }
    if summary:
        snapshot["summary"] = summary[:320]
    if goal:
        snapshot["goal"] = goal[:240]
    if rollback_hint:
        snapshot["rollback_hint"] = rollback_hint[:240]
    for key, value in rows.items():
        if value:
            snapshot[key] = value[:12]
    if not snapshot.get("summary"):
        snapshot["summary"] = " | ".join(
            [
                f"status={snapshot.get('status', 'ready')}",
                f"plan={snapshot.get('planning_mode', 'standard')}",
                f"scope={len(snapshot.get('scope') or [])}",
                f"checks={len(snapshot.get('acceptance_checks') or [])}",
                f"artifacts={len(snapshot.get('artifacts_to_touch') or [])}",
            ]
        )[:320]
    return snapshot


def _normalize_approved_plan_snapshot_for_audit(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    summary = str(raw.get("summary", "")).strip()
    status = str(raw.get("status", "")).strip().lower()
    if status not in {"missing", "pending", "blocked", "approved"}:
        lowered = summary.lower()
        for candidate in ("missing", "pending", "blocked", "approved"):
            if f"approved_plan={candidate}" in lowered:
                status = candidate
                break
    artifact_rows = [
        str(item).strip()[:200]
        for item in (raw.get("artifact_rows") or [])
        if str(item).strip()
    ]
    subtask_count = max(0, int(raw.get("subtask_count", 0) or 0))
    review_count = max(0, int(raw.get("review_count", 0) or 0))
    if not any((status, summary, artifact_rows, subtask_count, review_count)):
        return {}
    if status not in {"missing", "pending", "blocked", "approved"}:
        return {}
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", "")).strip() or "2026-04-17.v1",
        "status": status,
        "subtask_count": subtask_count,
        "review_count": review_count,
    }
    if summary:
        snapshot["summary"] = summary[:320]
    if artifact_rows:
        snapshot["artifact_rows"] = artifact_rows[:8]
    if not snapshot.get("summary"):
        snapshot["summary"] = " | ".join(
            [
                f"approved_plan={status}",
                f"subtasks={subtask_count}",
                f"reviews={review_count}",
            ]
        )[:320]
    return snapshot


def _normalize_phase_checkpoint_snapshot_for_audit(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    status = str(raw.get("status", "")).strip().lower()
    if status not in {"ready", "active", "blocked", "done"}:
        return {}
    current_phase = str(raw.get("current_phase", "")).strip().lower() or "plan"
    snapshot: Dict[str, Any] = {
        "version": str(raw.get("version", "")).strip() or "2026-04-16.v1",
        "status": status,
        "current_phase": current_phase,
    }
    summary = str(raw.get("summary", "")).strip()
    if summary:
        snapshot["summary"] = summary[:320]
    rows = [
        str(item).strip()[:200]
        for item in (raw.get("rows") or [])
        if str(item).strip()
    ]
    if rows:
        snapshot["rows"] = rows[:8]
    if not snapshot.get("summary"):
        snapshot["summary"] = " | ".join(
            [f"status={status}", f"current={current_phase}", *list(snapshot.get("rows") or [])[:4]]
        )[:320]
    return snapshot


def normalize_planning_handoff_snapshot(raw: Any, *, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(payload, dict):
        payload = {}
    source = row if isinstance(row, dict) else {}
    job_contract = _normalize_job_contract_snapshot_for_audit(payload.get("job_contract"))
    if not job_contract:
        job_contract = _normalize_job_contract_snapshot_for_audit(
            {
                "status": source.get("job_contract_status"),
                "summary": source.get("job_contract_summary"),
            }
        )
    debug_packet = _normalize_debug_packet_snapshot_for_audit(payload.get("debug_packet"))
    if not debug_packet:
        debug_packet = _normalize_debug_packet_snapshot_for_audit(
            {
                "state": source.get("debug_packet_state"),
                "summary": source.get("debug_packet_summary"),
                "next_step": source.get("debug_packet_next_step"),
            }
        )
    approved_plan = _normalize_approved_plan_snapshot_for_audit(payload.get("approved_plan"))
    if not approved_plan:
        approved_plan = _normalize_approved_plan_snapshot_for_audit(
            {
                "status": source.get("approved_plan_status"),
                "summary": source.get("approved_plan_summary"),
                "artifact_rows": source.get("approved_plan_artifact_rows"),
                "subtask_count": source.get("approved_plan_subtask_count"),
                "review_count": source.get("approved_plan_review_count"),
            }
        )
    phase_checkpoint = _normalize_phase_checkpoint_snapshot_for_audit(payload.get("phase_checkpoint"))
    if not phase_checkpoint:
        phase_checkpoint = _normalize_phase_checkpoint_snapshot_for_audit(
            {
                "status": source.get("phase_checkpoint_status"),
                "current_phase": source.get("phase_checkpoint_current_phase"),
                "summary": source.get("phase_checkpoint_summary"),
            }
        )
    if not any((job_contract, debug_packet, approved_plan, phase_checkpoint)):
        return {}
    return {
        "job_contract": job_contract,
        "debug_packet": debug_packet,
        "approved_plan": approved_plan,
        "phase_checkpoint": phase_checkpoint,
    }


def summarize_planning_handoff_snapshot(raw: Any) -> str:
    handoff = raw if isinstance(raw, dict) else normalize_planning_handoff_snapshot(raw)
    if not isinstance(handoff, dict) or not handoff:
        return "-"
    parts: List[str] = []
    job_contract = handoff.get("job_contract") if isinstance(handoff.get("job_contract"), dict) else {}
    debug_packet = handoff.get("debug_packet") if isinstance(handoff.get("debug_packet"), dict) else {}
    approved_plan = handoff.get("approved_plan") if isinstance(handoff.get("approved_plan"), dict) else {}
    phase_checkpoint = handoff.get("phase_checkpoint") if isinstance(handoff.get("phase_checkpoint"), dict) else {}
    if job_contract:
        parts.append("contract=" + (str(job_contract.get("summary", "")).strip() or "-"))
    if debug_packet:
        parts.append("debug=" + (str(debug_packet.get("summary", "")).strip() or "-"))
    if phase_checkpoint:
        parts.append("phase=" + (str(phase_checkpoint.get("summary", "")).strip() or "-"))
    if approved_plan:
        parts.append(str(approved_plan.get("summary", "")).strip() or "-")
    return " | ".join(parts) if parts else "-"


def summarize_retry_replan_debug_handoff(raw: Any, *, row: Optional[Dict[str, Any]] = None) -> str:
    handoff = normalize_planning_handoff_snapshot(raw, row=row)
    if not isinstance(handoff, dict) or not handoff:
        return "-"
    debug_packet = handoff.get("debug_packet") if isinstance(handoff.get("debug_packet"), dict) else {}
    if not debug_packet:
        return "-"
    parts: List[str] = []
    state = str(debug_packet.get("state", "")).strip() or "-"
    symptom = str(debug_packet.get("symptom", "")).strip() or "-"
    failed_attempt = compact_action_text(str(debug_packet.get("failed_attempt", "")).strip() or "-", limit=64)
    if state not in {"", "-"}:
        parts.append(f"debug={state}")
    if symptom not in {"", "-"}:
        parts.append(f"symptom={symptom}")
    elif failed_attempt not in {"", "-"}:
        parts.append(f"attempt={failed_attempt}")
    return " | ".join(parts) if parts else "-"


def summarize_retry_replan_approved_plan_handoff(raw: Any, *, row: Optional[Dict[str, Any]] = None) -> str:
    handoff = normalize_planning_handoff_snapshot(raw, row=row)
    if not isinstance(handoff, dict) or not handoff:
        return "-"
    approved_plan = handoff.get("approved_plan") if isinstance(handoff.get("approved_plan"), dict) else {}
    if not approved_plan:
        return "-"
    status = str(approved_plan.get("status", "")).strip().lower()
    if status in {"", "-", "approved"}:
        return "-"
    summary = str(approved_plan.get("summary", "")).strip() or f"approved_plan={status}"
    return compact_action_text(summary, limit=96)


def summarize_action_audit_headline(raw: Any) -> str:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return "-"
    headline = str(row.get("headline", "")).strip() or "-"
    reason_code = str(row.get("outcome_reason_code", "")).strip() or "-"
    if reason_code not in {"", "-"} and "reason=" not in headline:
        headline = f"{headline} | reason={reason_code}"
    outcome_kind = str(row.get("outcome_kind", "")).strip()
    status = str(row.get("status", "")).strip() or str(row.get("outcome_status", "")).strip()
    if outcome_kind in {"retry_run", "replan"} and status == "blocked":
        debug_summary = summarize_retry_replan_debug_handoff(row.get("planning_handoff"), row=row)
        if debug_summary not in {"", "-"} and debug_summary not in headline:
            headline = f"{headline} | {debug_summary}"
        approved_plan_summary = summarize_retry_replan_approved_plan_handoff(row.get("planning_handoff"), row=row)
        if approved_plan_summary not in {"", "-"} and approved_plan_summary not in headline:
            headline = f"{headline} | {approved_plan_summary}"
    return headline


def normalize_replan_auto_decision(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return {}
    planning_handoff = normalize_planning_handoff_snapshot(
        row.get("planning_handoff"),
        row=row,
    )
    return {
        "source": str(row.get("source", "")).strip() or "latest_offdesk_judge",
        "current_action": str(row.get("current_action", "")).strip() or "-",
        "suggested_action": str(row.get("suggested_action", "")).strip() or "-",
        "suggested_next_step": str(row.get("suggested_next_step", "")).strip() or "-",
        "decision_mode": str(row.get("decision_mode", "")).strip() or "-",
        "bridge_applied": bool(row.get("bridge_applied", False)),
        "supports_auto_decision": bool(row.get("supports_auto_decision", False)),
        "can_auto_apply": bool(row.get("can_auto_apply", False)),
        "reasoning": str(row.get("reasoning", "")).strip() or "-",
        "caution": str(row.get("caution", "")).strip() or "-",
        "confidence": str(row.get("confidence", "")).strip() or "-",
        "manual_feedback_state": str(row.get("manual_feedback_state", "")).strip() or "-",
        "manual_feedback_summary": str(row.get("manual_feedback_summary", "")).strip() or "-",
        "manual_feedback_next_step": str(row.get("manual_feedback_next_step", "")).strip() or "-",
        "manual_feedback_applied": bool(row.get("manual_feedback_applied", False)),
        "canonical_feedback_status": str(row.get("canonical_feedback_status", "")).strip() or "-",
        "canonical_feedback_summary": str(row.get("canonical_feedback_summary", "")).strip() or "-",
        "canonical_feedback_next_step": str(row.get("canonical_feedback_next_step", "")).strip() or "-",
        "canonical_feedback_kind": str(row.get("canonical_feedback_kind", "")).strip() or "-",
        "canonical_feedback_profile": str(row.get("canonical_feedback_profile", "")).strip() or "-",
        "canonical_feedback_applied": bool(row.get("canonical_feedback_applied", False)),
        "analysis_feedback_state": str(row.get("analysis_feedback_state", "")).strip() or "-",
        "analysis_feedback_summary": str(row.get("analysis_feedback_summary", "")).strip() or "-",
        "analysis_feedback_next_step": str(row.get("analysis_feedback_next_step", "")).strip() or "-",
        "analysis_feedback_open_kinds": str(row.get("analysis_feedback_open_kinds", "")).strip() or "-",
        "analysis_feedback_applied": bool(row.get("analysis_feedback_applied", False)),
        "planning_feedback_source": str(row.get("planning_feedback_source", "")).strip() or "-",
        "planning_feedback_state": str(row.get("planning_feedback_state", "")).strip() or "-",
        "planning_feedback_summary": str(row.get("planning_feedback_summary", "")).strip() or "-",
        "planning_feedback_next_step": str(row.get("planning_feedback_next_step", "")).strip() or "-",
        "planning_feedback_suggested_action": str(row.get("planning_feedback_suggested_action", "")).strip() or "-",
        "planning_feedback_applied": bool(row.get("planning_feedback_applied", False)),
        "job_contract_status": str(row.get("job_contract_status", "")).strip() or "-",
        "job_contract_summary": str(row.get("job_contract_summary", "")).strip() or "-",
        "approved_plan_status": (
            str(row.get("approved_plan_status", "")).strip()
            or str((planning_handoff.get("approved_plan") or {}).get("status", "")).strip()
            or "-"
        ),
        "approved_plan_summary": (
            str(row.get("approved_plan_summary", "")).strip()
            or str((planning_handoff.get("approved_plan") or {}).get("summary", "")).strip()
            or "-"
        ),
        "debug_packet_state": str(row.get("debug_packet_state", "")).strip() or "-",
        "debug_packet_summary": str(row.get("debug_packet_summary", "")).strip() or "-",
        "debug_packet_next_step": str(row.get("debug_packet_next_step", "")).strip() or "-",
        "phase_checkpoint_status": str(row.get("phase_checkpoint_status", "")).strip() or "-",
        "phase_checkpoint_current_phase": str(row.get("phase_checkpoint_current_phase", "")).strip() or "-",
        "phase_checkpoint_summary": str(row.get("phase_checkpoint_summary", "")).strip() or "-",
        "planning_handoff": planning_handoff,
        "planning_handoff_summary": summarize_planning_handoff_snapshot(planning_handoff),
    }


def summarize_replan_auto_decision(decision: Any) -> str:
    row = decision if isinstance(decision, dict) else normalize_replan_auto_decision(decision)
    if not isinstance(row, dict) or not row:
        return "-"
    parts = [
        f"from={str(row.get('current_action', '')).strip() or '-'}",
        f"to={str(row.get('suggested_action', '')).strip() or '-'}",
    ]
    confidence = str(row.get("confidence", "")).strip() or "-"
    next_step = str(row.get("suggested_next_step", "")).strip() or "-"
    decision_mode = str(row.get("decision_mode", "")).strip() or "-"
    if confidence != "-":
        parts.append(f"confidence={confidence}")
    if next_step != "-":
        parts.append(f"next={next_step}")
    if decision_mode != "-":
        parts.append(f"mode={decision_mode}")
    if bool(row.get("manual_feedback_applied", False)):
        parts.append("reuse=manual_feedback")
    elif bool(row.get("canonical_feedback_applied", False)):
        kind = str(row.get("canonical_feedback_kind", "")).strip() or "-"
        profile = str(row.get("canonical_feedback_profile", "")).strip() or "-"
        parts.append(f"reuse={kind}:{profile}")
    elif bool(row.get("analysis_feedback_applied", False)):
        parts.append("reuse=analysis_record_set")
    elif bool(row.get("planning_feedback_applied", False)):
        source = str(row.get("planning_feedback_source", "")).strip() or "-"
        parts.append(f"reuse={source}")
    if bool(row.get("can_auto_apply", False)):
        parts.append("auto=yes")
    return " | ".join(parts)


def normalize_replan_auto_routing_policy(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else _parse_json_object_from_text(raw)
    if not isinstance(row, dict) or not row:
        return {}
    planning_handoff = normalize_planning_handoff_snapshot(
        row.get("planning_handoff"),
        row=row,
    )
    return {
        "source": str(row.get("source", "")).strip() or "latest_offdesk_judge",
        "status": str(row.get("status", "")).strip() or "-",
        "current_action": str(row.get("current_action", "")).strip() or "-",
        "suggested_action": str(row.get("suggested_action", "")).strip() or "-",
        "suggested_next_step": str(row.get("suggested_next_step", "")).strip() or "-",
        "decision_mode": str(row.get("decision_mode", "")).strip() or "-",
        "supports_auto_decision": bool(row.get("supports_auto_decision", False)),
        "can_auto_apply": bool(row.get("can_auto_apply", False)),
        "requires_operator_confirmation": bool(row.get("requires_operator_confirmation", False)),
        "reasoning": str(row.get("reasoning", "")).strip() or "-",
        "caution": str(row.get("caution", "")).strip() or "-",
        "confidence": str(row.get("confidence", "")).strip() or "-",
        "manual_feedback_state": str(row.get("manual_feedback_state", "")).strip() or "-",
        "manual_feedback_summary": str(row.get("manual_feedback_summary", "")).strip() or "-",
        "manual_feedback_applied": bool(row.get("manual_feedback_applied", False)),
        "canonical_feedback_status": str(row.get("canonical_feedback_status", "")).strip() or "-",
        "canonical_feedback_summary": str(row.get("canonical_feedback_summary", "")).strip() or "-",
        "canonical_feedback_kind": str(row.get("canonical_feedback_kind", "")).strip() or "-",
        "canonical_feedback_profile": str(row.get("canonical_feedback_profile", "")).strip() or "-",
        "canonical_feedback_applied": bool(row.get("canonical_feedback_applied", False)),
        "analysis_feedback_state": str(row.get("analysis_feedback_state", "")).strip() or "-",
        "analysis_feedback_summary": str(row.get("analysis_feedback_summary", "")).strip() or "-",
        "analysis_feedback_open_kinds": str(row.get("analysis_feedback_open_kinds", "")).strip() or "-",
        "analysis_feedback_applied": bool(row.get("analysis_feedback_applied", False)),
        "planning_feedback_source": str(row.get("planning_feedback_source", "")).strip() or "-",
        "planning_feedback_state": str(row.get("planning_feedback_state", "")).strip() or "-",
        "planning_feedback_summary": str(row.get("planning_feedback_summary", "")).strip() or "-",
        "planning_feedback_applied": bool(row.get("planning_feedback_applied", False)),
        "job_contract_status": str(row.get("job_contract_status", "")).strip() or "-",
        "job_contract_summary": str(row.get("job_contract_summary", "")).strip() or "-",
        "approved_plan_status": (
            str(row.get("approved_plan_status", "")).strip()
            or str((planning_handoff.get("approved_plan") or {}).get("status", "")).strip()
            or "-"
        ),
        "approved_plan_summary": (
            str(row.get("approved_plan_summary", "")).strip()
            or str((planning_handoff.get("approved_plan") or {}).get("summary", "")).strip()
            or "-"
        ),
        "debug_packet_state": str(row.get("debug_packet_state", "")).strip() or "-",
        "debug_packet_summary": str(row.get("debug_packet_summary", "")).strip() or "-",
        "debug_packet_next_step": str(row.get("debug_packet_next_step", "")).strip() or "-",
        "phase_checkpoint_status": str(row.get("phase_checkpoint_status", "")).strip() or "-",
        "phase_checkpoint_current_phase": str(row.get("phase_checkpoint_current_phase", "")).strip() or "-",
        "phase_checkpoint_summary": str(row.get("phase_checkpoint_summary", "")).strip() or "-",
        "planning_handoff": planning_handoff,
        "planning_handoff_summary": summarize_planning_handoff_snapshot(planning_handoff),
    }


def summarize_replan_auto_routing_policy(policy: Any) -> str:
    row = policy if isinstance(policy, dict) else normalize_replan_auto_routing_policy(policy)
    if not isinstance(row, dict) or not row:
        return "-"
    parts = [
        f"status={str(row.get('status', '')).strip() or '-'}",
        f"from={str(row.get('current_action', '')).strip() or '-'}",
        f"to={str(row.get('suggested_action', '')).strip() or '-'}",
    ]
    confidence = str(row.get("confidence", "")).strip() or "-"
    next_step = str(row.get("suggested_next_step", "")).strip() or "-"
    decision_mode = str(row.get("decision_mode", "")).strip() or "-"
    if confidence != "-":
        parts.append(f"confidence={confidence}")
    if next_step != "-":
        parts.append(f"next={next_step}")
    if decision_mode != "-":
        parts.append(f"mode={decision_mode}")
    if bool(row.get("requires_operator_confirmation", False)):
        parts.append("confirm=yes")
    elif bool(row.get("can_auto_apply", False)):
        parts.append("auto=yes")
    elif bool(row.get("planning_feedback_applied", False)):
        parts.append(f"gate={str(row.get('planning_feedback_source', '')).strip() or '-'}")
    return " | ".join(parts)


def _merge_replan_audit_context(payload: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    merged = payload if isinstance(payload, dict) else _parse_json_object_from_text(payload)
    if not isinstance(merged, dict):
        merged = {}
    enriched = dict(merged)
    for key in (
        "job_contract_status",
        "job_contract_summary",
        "approved_plan_status",
        "approved_plan_summary",
        "debug_packet_state",
        "debug_packet_summary",
        "debug_packet_next_step",
        "phase_checkpoint_status",
        "phase_checkpoint_current_phase",
        "phase_checkpoint_summary",
    ):
        if str(enriched.get(key, "")).strip():
            continue
        if key in row:
            enriched[key] = row.get(key)
    if "planning_handoff" not in enriched and "planning_handoff" in row:
        enriched["planning_handoff"] = row.get("planning_handoff")
    return enriched


def _latest_action_headline(latest_action: Dict[str, str]) -> str:
    headline_summary = str(latest_action.get("headline_summary", "")).strip() or "-"
    if headline_summary != "-":
        return headline_summary
    headline = str(latest_action.get("headline", "")).strip() or "-"
    reason_code = str(latest_action.get("outcome_reason_code", "")).strip() or "-"
    if reason_code in {"", "-"}:
        return headline
    if "reason=" in headline:
        return headline
    return f"{headline} | reason={reason_code}"


def _load_action_audit_rows(team_dir: Any) -> List[Dict[str, Any]]:
    token = str(team_dir or "").strip()
    if not token:
        return []
    path = _action_audit_path(token)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                rows.append(row)
    except Exception:
        return []
    return rows


def append_action_audit_row(
    team_dir: Any,
    *,
    headline: Any,
    status: Any,
    outcome_kind: Any,
    outcome_status: Any,
    outcome_reason_code: Any,
    outcome_detail: Any,
    next_step: Any,
    remediation: Any,
    source_command: Any,
    link_label: Any = "-",
    link_href: Any = "-",
    at: Any = "",
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    token = str(team_dir or "").strip()
    source = str(source_command or "").strip()
    if not token or not source:
        return False
    path = _action_audit_path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": str(at or "").strip() or _action_audit_now(),
        "headline": str(headline or "").strip() or "-",
        "status": str(status or "").strip() or "unknown",
        "outcome_kind": str(outcome_kind or "").strip() or "-",
        "outcome_status": str(outcome_status or "").strip() or str(status or "").strip() or "unknown",
        "outcome_reason_code": str(outcome_reason_code or "").strip() or "-",
        "outcome_detail": str(outcome_detail or "").strip() or "-",
        "next_step": str(next_step or "").strip() or "-",
        "remediation": str(remediation or "").strip() or "-",
        "link_label": str(link_label or "").strip() or "-",
        "link_href": str(link_href or "").strip() or "-",
        "source_command": source,
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            token = str(key or "").strip()
            if not token or token in row:
                continue
            row[token] = value
    try:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return False
    return True


def load_latest_action_audit_for_task(team_dir: Any, request_id: Any) -> Dict[str, str]:
    token = str(request_id or "").strip()
    if not token:
        return {}
    task_path = f"/control/tasks/by-request/{quote(token, safe='')}"
    rows = _load_action_audit_rows(team_dir)
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() == task_path:
            return _normalize_latest_action_row(row)
    return {}


def load_latest_action_audit_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
    request_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}" if alias else ""
    task_paths = {
        f"/control/tasks/by-request/{quote(str(item).strip(), safe='')}"
        for item in (request_ids or [])
        if str(item).strip()
    }
    for row in reversed(rows):
        link_href = str(row.get("link_href", "")).strip()
        if runtime_path and link_href == runtime_path:
            return _normalize_latest_action_row(row)
        if task_paths and link_href in task_paths:
            return _normalize_latest_action_row(row)
    return {}


def load_latest_action_audit_for_runtime_kind(
    team_dir: Any,
    *,
    project_alias: Any,
    outcome_kind: Any,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    kind = str(outcome_kind or "").strip()
    if not alias or not kind:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != kind:
            continue
        normalized = _normalize_latest_action_row(row)
        normalized["at"] = str(row.get("at", "")).strip() or "-"
        return normalized
    return {}


def load_latest_offdesk_judge_decision_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, Any]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != "offdesk_judge":
            continue
        decision = normalize_offdesk_judge_decision(row.get("decision_snapshot") or row.get("response_text"))
        if not decision:
            continue
        decision["at"] = str(row.get("at", "")).strip() or "-"
        return decision
    return {}


def load_latest_offdesk_judge_decision_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    decision = load_latest_offdesk_judge_decision_for_runtime(team_dir, project_alias=project_alias)
    return summarize_offdesk_judge_decision(decision)


def load_latest_judge_decision_bridge_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, Any]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        bridge = normalize_latest_judge_decision_bridge(row.get("latest_judge_decision_bridge"))
        if not bridge:
            continue
        bridge["at"] = str(row.get("at", "")).strip() or "-"
        bridge["headline"] = str(row.get("headline", "")).strip() or "-"
        return bridge
    return {}


def load_latest_judge_decision_bridge_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    bridge = load_latest_judge_decision_bridge_for_runtime(team_dir, project_alias=project_alias)
    return summarize_latest_judge_decision_bridge(bridge)


def load_latest_replan_auto_decision_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, Any]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        decision = normalize_replan_auto_decision(
            _merge_replan_audit_context(row.get("replan_auto_decision"), row)
        )
        if not decision:
            continue
        decision["at"] = str(row.get("at", "")).strip() or "-"
        decision["headline"] = str(row.get("headline", "")).strip() or "-"
        return decision
    return {}


def load_latest_replan_auto_decision_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    decision = load_latest_replan_auto_decision_for_runtime(team_dir, project_alias=project_alias)
    return summarize_replan_auto_decision(decision)


def load_latest_replan_auto_routing_policy_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, Any]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        policy = normalize_replan_auto_routing_policy(
            _merge_replan_audit_context(row.get("replan_auto_routing_policy"), row)
        )
        if not policy:
            continue
        policy["at"] = str(row.get("at", "")).strip() or "-"
        policy["headline"] = str(row.get("headline", "")).strip() or "-"
        return policy
    return {}


def load_latest_replan_auto_routing_policy_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    policy = load_latest_replan_auto_routing_policy_for_runtime(team_dir, project_alias=project_alias)
    return summarize_replan_auto_routing_policy(policy)


def load_latest_replan_auto_route_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> Dict[str, Any]:
    alias = str(project_alias or "").strip()
    if not alias:
        return {}
    row = load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=alias,
        outcome_kind="replan_auto_route",
    )
    if not row:
        return {}
    enriched = dict(row)
    enriched["at"] = str(row.get("at", "")).strip() or "-"
    return enriched


def summarize_latest_replan_auto_route(row: Any) -> str:
    if not isinstance(row, dict) or not row:
        return "-"
    headline = str(row.get("headline", "")).strip()
    state = "-"
    if "|" in headline:
        state = str(headline.split("|", 1)[1]).strip() or "-"
    if state == "-":
        state = str(row.get("status", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    detail = str(row.get("outcome_detail", "")).strip() or "-"
    at = str(row.get("at", "")).strip() or "-"
    return f"state={state} | next={next_step} | at={at} | {detail}"


def summarize_replan_auto_operator_status(
    *,
    policy: Any,
    route_row: Any,
) -> str:
    normalized_policy = normalize_replan_auto_routing_policy(policy)
    normalized_route = route_row if isinstance(route_row, dict) else {}
    ready_status = str(normalized_policy.get("status", "")).strip().lower()
    ready_next = str(normalized_policy.get("suggested_next_step", "")).strip() or "-"
    applied_next = str(normalized_route.get("next_step", "")).strip() or "-"
    applied_at = str(normalized_route.get("at", "")).strip() or "-"
    if ready_status == "ready" and ready_next not in {"", "-"} and applied_next not in {"", "-"}:
        if ready_next == applied_next:
            return f"ready+applied={applied_next} | at={applied_at}"
        return f"ready={ready_next} | applied={applied_next} | at={applied_at}"
    if ready_status == "ready" and ready_next not in {"", "-"}:
        return f"ready={ready_next} | waiting_for_apply"
    if ready_status == "manual_progressed" and ready_next not in {"", "-"}:
        feedback_state = str(normalized_policy.get("manual_feedback_state", "")).strip() or "-"
        suggested_action = str(normalized_policy.get("suggested_action", "")).strip().lower()
        if suggested_action in {"manual_review", "review", "judge"}:
            return f"manual_review={ready_next} | state={feedback_state} | reused"
        if suggested_action == "followup_execute":
            return f"manual_execute={ready_next} | state={feedback_state} | reused"
        if suggested_action == "followup":
            return f"manual_followup={ready_next} | state={feedback_state} | reused"
        return f"manual={ready_next} | state={feedback_state} | reused"
    if ready_status == "mutation_progressed" and ready_next not in {"", "-"}:
        kind = str(normalized_policy.get("canonical_feedback_kind", "")).strip() or "-"
        profile = str(normalized_policy.get("canonical_feedback_profile", "")).strip() or "-"
        return f"mutation={ready_next} | kind={kind}:{profile} | reused"
    if ready_status == "analysis_review_ready" and ready_next not in {"", "-"}:
        feedback_state = str(normalized_policy.get("analysis_feedback_state", "")).strip() or "-"
        return f"analysis_review={ready_next} | state={feedback_state} | reused"
    if ready_status in {"contract_review_ready", "debug_review_ready", "phase_review_ready"} and ready_next not in {"", "-"}:
        source = str(normalized_policy.get("planning_feedback_source", "")).strip() or "-"
        state = str(normalized_policy.get("planning_feedback_state", "")).strip() or "-"
        return f"planning_review={ready_next} | source={source} | state={state} | reused"
    if ready_status == "manual_ready" and ready_next not in {"", "-"}:
        suggested_action = str(normalized_policy.get("suggested_action", "")).strip().lower()
        if suggested_action in {"manual_review", "review", "judge"}:
            return f"manual_review={ready_next} | waiting_for_operator"
        if suggested_action == "followup_execute":
            return f"manual_execute={ready_next} | waiting_for_operator"
        if suggested_action == "followup":
            return f"manual_followup={ready_next} | waiting_for_operator"
        return f"manual={ready_next} | waiting_for_operator"
    if applied_next not in {"", "-"}:
        return f"applied={applied_next} | at={applied_at}"
    return "-"


def summarize_replan_auto_operator_summary(
    *,
    policy: Any,
    route_row: Any,
) -> str:
    status_summary = summarize_replan_auto_operator_status(policy=policy, route_row=route_row)
    if status_summary == "-":
        return "-"
    normalized_policy = normalize_replan_auto_routing_policy(policy)
    if (
        str(normalized_policy.get("status", "")).strip() == "ready"
        and bool(normalized_policy.get("can_auto_apply", False))
        and str(normalized_policy.get("suggested_action", "")).strip() == "retry"
        and str(normalized_policy.get("suggested_next_step", "")).strip().startswith("/")
    ):
        return f"{status_summary} | apply=dashboard button | api:auto_route_apply=true"
    if (
        str(normalized_policy.get("status", "")).strip() == "manual_ready"
        and str(normalized_policy.get("suggested_next_step", "")).strip().startswith("/")
    ):
        return f"{status_summary} | do={str(normalized_policy.get('suggested_next_step', '')).strip()}"
    if str(normalized_policy.get("status", "")).strip() == "manual_progressed":
        return f"{status_summary} | reuse=task_truth"
    if str(normalized_policy.get("status", "")).strip() == "mutation_progressed":
        return f"{status_summary} | reuse=canonical_writeback"
    if str(normalized_policy.get("status", "")).strip() == "analysis_review_ready":
        return f"{status_summary} | reuse=analysis_record_set"
    if str(normalized_policy.get("status", "")).strip() in {"contract_review_ready", "debug_review_ready", "phase_review_ready"}:
        source = str(normalized_policy.get("planning_feedback_source", "")).strip() or "-"
        return f"{status_summary} | reuse={source}"
    return status_summary


def load_latest_replan_auto_route_status_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    row = load_latest_replan_auto_route_for_runtime(team_dir, project_alias=project_alias)
    policy = load_latest_replan_auto_routing_policy_for_runtime(team_dir, project_alias=project_alias)
    return summarize_replan_auto_operator_status(policy=policy, route_row=row)


def load_latest_replan_auto_operator_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    row = load_latest_replan_auto_route_for_runtime(team_dir, project_alias=project_alias)
    policy = load_latest_replan_auto_routing_policy_for_runtime(team_dir, project_alias=project_alias)
    return summarize_replan_auto_operator_summary(policy=policy, route_row=row)


def summarize_latest_manual_step(policy: Any) -> str:
    normalized_policy = normalize_replan_auto_routing_policy(policy)
    policy_status = str(normalized_policy.get("status", "")).strip().lower()
    if policy_status not in {
        "manual_ready",
        "manual_progressed",
        "analysis_review_ready",
        "contract_review_ready",
        "debug_review_ready",
        "phase_review_ready",
    }:
        return "-"
    next_step = str(normalized_policy.get("suggested_next_step", "")).strip() or "-"
    suggested_action = str(normalized_policy.get("suggested_action", "")).strip().lower()
    confidence = str(normalized_policy.get("confidence", "")).strip() or "-"
    planning_feedback_source = str(normalized_policy.get("planning_feedback_source", "")).strip() or "-"
    if policy_status in {"analysis_review_ready", "contract_review_ready", "debug_review_ready", "phase_review_ready"}:
        if policy_status == "analysis_review_ready":
            return f"analysis_review={next_step} | gate=analysis_record_set | waiting_for_operator"
        return f"task_review={next_step} | gate={planning_feedback_source} | waiting_for_operator"
    if policy_status == "manual_progressed":
        feedback_state = str(normalized_policy.get("manual_feedback_state", "")).strip() or "-"
        if suggested_action in {"manual_review", "review", "judge"}:
            return f"manual_review={next_step} | state={feedback_state} | reused"
        if suggested_action == "followup_execute":
            return f"manual_execute={next_step} | state={feedback_state} | reused"
        if suggested_action == "followup":
            return f"manual_followup={next_step} | state={feedback_state} | reused"
        return f"manual={next_step} | state={feedback_state} | reused"
    if suggested_action in {"manual_review", "review", "judge"}:
        return f"manual_review={next_step} | confidence={confidence} | waiting_for_operator"
    if suggested_action == "followup_execute":
        return f"manual_execute={next_step} | confidence={confidence} | waiting_for_operator"
    if suggested_action == "followup":
        return f"manual_followup={next_step} | confidence={confidence} | waiting_for_operator"
    return f"manual={next_step} | confidence={confidence} | waiting_for_operator"


def load_latest_manual_step_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    policy = load_latest_replan_auto_routing_policy_for_runtime(team_dir, project_alias=project_alias)
    return summarize_latest_manual_step(policy)


def summarize_latest_canonical_writeback(row: Any) -> str:
    if not isinstance(row, dict) or not row:
        return "-"
    headline = str(row.get("headline", "")).strip() or "Canonical Writeback"
    state = str(row.get("status", "")).strip() or "-"
    next_step = str(row.get("next_step", "")).strip() or "-"
    detail = str(row.get("outcome_detail", "")).strip() or "-"
    at = str(row.get("at", "")).strip() or "-"
    return f"{headline} | state={state} | next={next_step} | at={at} | {detail}"


def summarize_latest_canonical_mutation(row: Any) -> str:
    if not isinstance(row, dict) or not row:
        return "-"
    detail = str(row.get("outcome_detail", "")).strip()
    state = str(row.get("status", "")).strip() or "-"
    at = str(row.get("at", "")).strip() or "-"
    match = re.search(
        r"path=(?P<path>\S+)\s+lines=(?P<lines>\d+)\s+done=(?P<done>\d+)\s+reopen=(?P<reopen>\d+)\s+append=(?P<append>\d+)\s+blocked=(?P<blocked>\d+)",
        detail,
    )
    if not match:
        return "-"
    path = str(match.group("path") or "").strip()
    lines = int(match.group("lines") or 0)
    done = int(match.group("done") or 0)
    reopen = int(match.group("reopen") or 0)
    append = int(match.group("append") or 0)
    blocked = int(match.group("blocked") or 0)
    mutation = _classify_canonical_mutation_from_counts(
        path=path,
        line_count=lines,
        done_count=done,
        reopen_count=reopen,
        append_count=append,
        blocked_count=blocked,
    )
    return (
        "{kind}:{profile} | path={path} | lines={lines} | done={done} reopen={reopen} append={append} blocked={blocked} | state={state} | at={at}"
    ).format(
        kind=str(mutation.get("kind", "")).strip() or "-",
        profile=str(mutation.get("profile", "")).strip() or "-",
        path=str(mutation.get("path", "")).strip() or "-",
        lines=lines,
        done=done,
        reopen=reopen,
        append=append,
        blocked=blocked,
        state=state,
        at=at,
    )


def load_latest_canonical_writeback_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    row = load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=project_alias,
        outcome_kind="runtime_syncback_apply",
    )
    if not row:
        return "-"
    enriched = dict(row)
    enriched["at"] = str(row.get("at", "")).strip() or "-"
    return summarize_latest_canonical_writeback(enriched)


def load_latest_canonical_mutation_summary_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
) -> str:
    row = load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=project_alias,
        outcome_kind="runtime_syncback_apply",
    )
    if not row:
        return "-"
    enriched = dict(row)
    enriched["at"] = str(row.get("at", "")).strip() or "-"
    return summarize_latest_canonical_mutation(enriched)


def load_latest_model_ping_audit_for_runtime(
    team_dir: Any,
    *,
    project_alias: Any,
    kind: Any,
) -> Dict[str, str]:
    alias = str(project_alias or "").strip()
    ping_kind = str(kind or "").strip().lower()
    if not alias or not ping_kind:
        return {}
    rows = _load_action_audit_rows(team_dir)
    if not rows:
        return {}
    runtime_path = f"/control/runtimes/{quote(alias, safe='')}"
    suffix = f" {ping_kind}"
    for row in reversed(rows):
        if str(row.get("link_href", "")).strip() != runtime_path:
            continue
        if str(row.get("outcome_kind", "")).strip() != "model_ping":
            continue
        source_command = str(row.get("source_command", "")).strip()
        if not source_command.endswith(suffix):
            continue
        normalized = _normalize_latest_action_row(row)
        normalized["at"] = str(row.get("at", "")).strip() or "-"
        return normalized
    return {}


def prefer_recent_model_ping_probe_summary(
    team_dir: Any,
    *,
    project_alias: Any,
    kind: Any,
    endpoint_id: Any = "",
    probe_status: Any = "",
    probe_summary: Any = "",
) -> str:
    current_status = str(probe_status or "").strip().lower()
    current_summary = str(probe_summary or "").strip() or "-"
    if current_status not in {"probe_timeout", "deferred_live_probe", "unsupported_probe"}:
        return current_summary
    latest_ping = load_latest_model_ping_audit_for_runtime(
        team_dir,
        project_alias=project_alias,
        kind=kind,
    )
    if not latest_ping:
        return current_summary
    if str(latest_ping.get("status", "")).strip().lower() != "executed":
        return current_summary
    detail = str(latest_ping.get("outcome_detail", "")).strip() or "-"
    binding_endpoint_id = str(endpoint_id or "").strip()
    if binding_endpoint_id and f"endpoint={binding_endpoint_id}" not in detail:
        return current_summary
    return f"status=last_invoke_ok | {detail}"


def append_latest_action_lines(
    lines: List[str],
    latest_action: Dict[str, str],
    *,
    compact_reason: Optional[callable] = None,
    line_prefix: str = "",
) -> None:
    if not isinstance(latest_action, dict) or not latest_action:
        return
    headline = _latest_action_headline(latest_action)
    next_step = str(latest_action.get("next_step", "")).strip() or "-"
    remediation = str(latest_action.get("remediation", "")).strip() or "-"
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    if headline != "-":
        lines.append(f"{line_prefix}latest_action: {headline}")
    if next_step != "-":
        lines.append(f"{line_prefix}latest_action_next: {next_step}")
    if remediation != "-":
        lines.append(f"{line_prefix}latest_action_note: {formatter(remediation, 120)}")


def append_latest_action_summary_line(
    lines: List[str],
    latest_action: Dict[str, str],
    *,
    compact_reason: Optional[callable] = None,
    line_prefix: str = "",
    note_limit: int = 88,
) -> None:
    if not isinstance(latest_action, dict) or not latest_action:
        return
    headline = _latest_action_headline(latest_action)
    next_step = str(latest_action.get("next_step", "")).strip() or "-"
    remediation = str(latest_action.get("remediation", "")).strip() or "-"
    formatter = compact_reason if callable(compact_reason) else compact_action_text
    parts: List[str] = []
    if headline != "-":
        parts.append(headline)
    if next_step != "-":
        parts.append(f"next={next_step}")
    if remediation != "-":
        parts.append(formatter(remediation, note_limit))
    if parts:
        lines.append(f"{line_prefix}latest_action: " + " | ".join(parts))
