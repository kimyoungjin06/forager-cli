#!/usr/bin/env python3
"""Shared operator action contract helpers for dashboard and operator surfaces."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


_TASK_LABEL_RE = re.compile(r"\bT-\d+\b")


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _dedupe_commands(rows: Iterable[str], *, limit: int = 8) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for row in rows:
        token = _trim(row)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= max(1, int(limit)):
            break
    return out


def task_command_ref(label: str, request_id: str) -> str:
    match = _TASK_LABEL_RE.search(_trim(label, 256))
    if match:
        return match.group(0)
    return _trim(request_id, 128) or "-"


def task_operator_commands(
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
) -> List[str]:
    alias = _trim(project_alias, 32)
    ref = task_command_ref(label, request_id)
    brief_status = _trim(execution_brief_status, 64).lower()
    brief_blocked = brief_status in {"underspecified", "operator_decision_required", "infeasible"}
    followup_status = _trim(followup_brief_status, 64).lower()
    followup_executable = followup_status in {"executable", "partially_executable"}
    hints = [
        f"/task {ref}",
        f"/request {request_id}",
        f"/monitor {alias}",
    ]
    phase = _trim(tf_phase, 64).lower()
    if phase in {"planning", "running", "critic_review", "blocked", "manual_intervention", "rate_limited", "needs_retry"}:
        hints.append("/offdesk review")
    if rate_limit_summary and rate_limit_summary != "-":
        hints.append("/auto status")
    if not brief_blocked and rerun_summary and rerun_summary != "-":
        hints.append(f"/retry {ref}")
    if not brief_blocked and followup_summary and followup_summary != "-":
        hints.append(f"/followup {ref}")
    if not brief_blocked and followup_executable:
        hints.append(f"/followup-exec {ref}")
    if not brief_blocked and followup_summary and followup_summary != "-":
        hints.append(f"/todo {alias} followup")
    return _dedupe_commands(hints, limit=8)


def runtime_operator_commands(
    *,
    project_alias: str,
    priority_action: str = "",
    has_active_task: bool = False,
    has_rate_limit: bool = False,
    background_queue_stale_count: int = 0,
) -> List[str]:
    alias = _trim(project_alias, 32)
    hints = [
        _trim(priority_action),
        f"/orch bgq-clean {alias}" if alias and int(background_queue_stale_count or 0) > 0 else "",
        f"/monitor {alias}",
        f"/todo {alias}",
        "/offdesk review" if has_active_task else "",
        "/auto status" if has_rate_limit else "",
    ]
    return _dedupe_commands(hints, limit=8)


def classify_operator_command(command: str) -> Dict[str, str]:
    raw = _trim(command, 240)
    low = raw.lower()
    tokens = low.split()
    head = tokens[0] if tokens else ""
    second = tokens[1] if len(tokens) > 1 else ""
    third = tokens[2] if len(tokens) > 2 else ""
    fourth = tokens[3] if len(tokens) > 3 else ""

    bucket = "safe"
    mutation = "safe"
    scope = "generic"
    note = ""

    if head in {"/task", "/request", "/monitor", "/map", "/queue", "/help"}:
        scope = "inspect"
        note = "read-only drill-down"
    elif head == "/orch":
        if second in {"status", "monitor", "list"}:
            scope = "runtime"
            note = "read-only runtime status"
        elif second in {"bgw-status", "worker-status"}:
            scope = "runtime"
            note = "read-only background worker status"
        elif second in {"bgw-start", "worker-start", "bgw-stop", "worker-stop"}:
            bucket = "phase2"
            mutation = "runtime_mutation"
            scope = "runtime"
            note = "background worker lifecycle mutation candidate"
        elif second == "bgq-clean":
            bucket = "phase2"
            mutation = "runtime_mutation"
            scope = "runtime"
            note = "background queue cleanup mutation candidate"
        else:
            bucket = "phase2"
            mutation = "runtime_mutation"
            scope = "runtime"
            note = "runtime mutation candidate"
    elif head == "/todo":
        scope = "runtime"
        action = second
        if action.startswith("o") and action[1:].isdigit():
            action = third
        if action in {"accept", "promote", "reject", "drop"}:
            bucket = "phase2"
            mutation = "runtime_mutation"
            note = "proposal inbox mutation candidate"
        elif third == "syncback" and fourth == "preview":
            note = "read-only canonical preview"
        elif third == "syncback" and fourth == "apply":
            bucket = "phase2"
            mutation = "canonical_mutation"
            scope = "canonical"
            note = "canonical mutation candidate"
        elif third in {"followup", "proposals"} or second in {"followup", "proposals", "next"}:
            note = "read-only backlog inspection"
        else:
            note = "read-only backlog drill-down"
    elif head == "/sync":
        scope = "sync"
        if second == "preview":
            note = "read-only sync inspection"
        else:
            bucket = "phase2"
            mutation = "runtime_mutation"
            note = "runtime mutation candidate"
    elif head in {"/retry", "/replan"}:
        bucket = "phase2"
        mutation = "runtime_mutation"
        scope = "task"
        note = "task rerun mutation candidate"
    elif head in {"/followup-exec", "/followup-run"}:
        bucket = "phase2"
        mutation = "runtime_mutation"
        scope = "task"
        note = "task follow-up execution mutation candidate"
    elif head == "/followup":
        scope = "task"
        note = "read-only follow-up inspection"
    elif head == "/offdesk":
        scope = "control"
        if second in {"prepare", "review", "status"}:
            note = "read-only control inspection"
        else:
            bucket = "phase2"
            mutation = "control_mutation"
            note = "control mutation candidate"
    elif head == "/auto":
        scope = "control"
        if second == "status":
            note = "read-only control inspection"
        else:
            bucket = "phase2"
            mutation = "control_mutation"
            note = "control mutation candidate"
    else:
        scope = "generic"
        note = "reference command; keep read-only until explicitly classified"

    return {
        "command": raw,
        "bucket": bucket,
        "mutation": mutation,
        "scope": scope,
        "note": note,
    }


def partition_operator_commands(commands: Iterable[str], *, safe_limit: int = 6, phase2_limit: int = 4) -> Dict[str, Any]:
    normalized = _dedupe_commands(commands, limit=max(int(safe_limit or 0) + int(phase2_limit or 0), 8))
    contracts = [classify_operator_command(command) for command in normalized]
    safe_commands = [row["command"] for row in contracts if str(row.get("bucket", "")).strip() == "safe"][: max(1, int(safe_limit))]
    phase2_commands = [row["command"] for row in contracts if str(row.get("bucket", "")).strip() == "phase2"][
        : max(1, int(phase2_limit))
    ]
    return {
        "safe": safe_commands,
        "phase2": phase2_commands,
        "contracts": contracts,
    }


def http_action_spec(command: str) -> Dict[str, Any] | None:
    raw = _trim(command, 240)
    low = raw.lower()
    tokens = raw.split()
    if not tokens:
        return None

    head = tokens[0].lower()
    second = tokens[1].lower() if len(tokens) > 1 else ""
    third = tokens[2] if len(tokens) > 2 else ""
    fourth = tokens[3].lower() if len(tokens) > 3 else ""

    lane_ids: List[str] = []
    if "lane" in [token.lower() for token in tokens]:
        idx = next((i for i, token in enumerate(tokens) if token.lower() == "lane"), -1)
        if idx >= 0 and idx + 1 < len(tokens):
            lane_ids = [item.strip() for item in tokens[idx + 1].split(",") if item.strip()]

    if head == "/retry" and len(tokens) >= 2:
        return {
            "command": raw,
            "mode": "phase2",
            "method": "POST",
            "path": "/control/actions/task/retry",
            "payload": {
                "task_ref": tokens[1],
                "lane_ids": lane_ids,
            },
            "note": "rerun a task team using existing retry handlers",
        }

    if head == "/replan" and len(tokens) >= 2:
        return {
            "command": raw,
            "mode": "phase2",
            "method": "POST",
            "path": "/control/actions/task/replan",
            "payload": {
                "task_ref": tokens[1],
                "lane_ids": lane_ids,
            },
            "note": "re-enter phase1 planning for a task team using existing replan handlers",
        }

    if head == "/followup" and len(tokens) >= 2:
        return {
            "command": raw,
            "mode": "safe",
            "method": "POST",
            "path": "/control/actions/task/followup",
            "payload": {
                "task_ref": tokens[1],
                "lane_ids": lane_ids,
            },
            "note": "inspect manual follow-up targets using the existing followup handler",
        }

    if head in {"/followup-exec", "/followup-run"} and len(tokens) >= 2:
        return {
            "command": raw,
            "mode": "phase2",
            "method": "POST",
            "path": "/control/actions/task/followup-execute",
            "payload": {
                "task_ref": tokens[1],
                "lane_ids": lane_ids,
            },
            "note": "attempt explicit follow-up execution using a followup brief instead of the preview surface",
        }

    if head == "/orch" and second == "judge" and len(tokens) >= 3:
        return {
            "command": raw,
            "mode": "safe",
            "method": "POST",
            "path": "/control/actions/runtime/judge",
            "payload": {
                "project_ref": tokens[2],
            },
            "note": "run the bound off-desk judge for the runtime using the latest task context",
        }

    if head == "/todo":
        token_offset = 0
        project_ref = ""
        action = second
        if second and second.upper().startswith("O") and second[1:].isdigit():
            project_ref = tokens[1]
            token_offset = 1
            action = tokens[2].lower() if len(tokens) >= 3 else ""
        proposal_token = tokens[2 + token_offset] if len(tokens) >= 3 + token_offset else ""
        if action in {"accept", "promote"} and project_ref and proposal_token:
            return {
                "command": raw,
                "mode": "phase2",
                "method": "POST",
                "path": "/control/actions/runtime/todo-accept",
                "payload": {
                    "project_ref": project_ref,
                    "proposal_ref": proposal_token,
                },
                "note": "promote a worker or follow-up proposal into the runtime todo queue",
            }
        if action in {"reject", "drop"} and project_ref and proposal_token:
            return {
                "command": raw,
                "mode": "phase2",
                "method": "POST",
                "path": "/control/actions/runtime/todo-reject",
                "payload": {
                    "project_ref": project_ref,
                    "proposal_ref": proposal_token,
                    "reason": " ".join(tokens[3 + token_offset :]).strip(),
                },
                "note": "reject a worker or follow-up proposal while preserving the audit trail",
            }

    if head == "/sync" and second == "preview" and len(tokens) >= 3:
        return {
            "command": raw,
            "mode": "safe",
            "method": "POST",
            "path": "/control/actions/runtime/sync-preview",
            "payload": {
                "project_ref": third,
                "window": tokens[3] if len(tokens) >= 4 else "24h",
            },
            "note": "inspect sync sources without mutating runtime state",
        }

    if head == "/todo":
        project_ref = ""
        action = second
        offset = 0
        if second.startswith("o") and second[1:].isdigit():
            project_ref = second.upper()
            action = third
            offset = 1
        if action == "syncback":
            syncback_mode = fourth if offset else third
            if syncback_mode == "preview" and project_ref:
                return {
                    "command": raw,
                    "mode": "safe",
                    "method": "POST",
                    "path": "/control/actions/runtime/syncback-preview",
                    "payload": {
                        "project_ref": project_ref,
                    },
                    "note": "inspect canonical TODO syncback before mutating TODO.md",
                }
            if syncback_mode == "apply" and project_ref:
                return {
                    "command": raw,
                    "mode": "phase2",
                    "method": "POST",
                    "path": "/control/actions/runtime/syncback-apply",
                    "payload": {
                        "project_ref": project_ref,
                    },
                    "note": "apply accepted runtime todo drift back into canonical TODO.md",
                }

    if head == "/orch" and second == "bgq-clean" and len(tokens) >= 3:
        return {
            "command": raw,
            "mode": "phase2",
            "method": "POST",
            "path": "/control/actions/runtime/background-queue-clean",
            "payload": {
                "project_ref": tokens[2],
            },
            "note": "mark stale background queue tickets and refresh runtime queue state",
        }

    if head == "/auto" and second == "recover":
        force = len(tokens) >= 3 and tokens[2].lower() == "force"
        return {
            "command": raw,
            "mode": "phase2",
            "method": "POST",
            "path": "/control/actions/control/auto-recover",
            "payload": {
                "force": bool(force),
            },
            "note": "resume control automation through the existing auto recover path",
        }

    return None
