#!/usr/bin/env python3
"""Control Action API contract helpers.

This module defines the stable action surface Control Plane may use when it
decides whether a plaintext request should become:

- a status lookup
- a read-only inspection
- a real work dispatch
- a control-plane mutation

The goal is to keep this seam backend-agnostic so later adapters
(Telegram/CLI/MCP) can share one normalized contract.
"""

from __future__ import annotations

from typing import Any, Dict, List


INTENT_CLASSES = ("status", "inspect", "work", "control")
RISK_LEVELS = ("safe", "runtime_mutation", "canonical_mutation")


ACTION_ALIASES = {
    "map": "list_projects",
    "projects": "list_projects",
    "list_projects": "list_projects",
    "focus": "focus_project",
    "use": "focus_project",
    "focus_project": "focus_project",
    "unlock": "clear_focus",
    "focus_off": "clear_focus",
    "clear_focus": "clear_focus",
    "status": "get_project_status",
    "project_status": "get_project_status",
    "get_project_status": "get_project_status",
    "monitor": "monitor_project",
    "monitor_project": "monitor_project",
    "queue": "get_queue",
    "get_queue": "get_queue",
    "task": "get_task",
    "get_task": "get_task",
    "followup": "list_followups",
    "followups": "list_followups",
    "list_followups": "list_followups",
    "sync_preview": "sync_preview",
    "preview_sync": "sync_preview",
    "sync_bootstrap": "sync_bootstrap",
    "bootstrap": "sync_bootstrap",
    "recover": "sync_bootstrap",
    "sync": "sync_apply",
    "sync_apply": "sync_apply",
    "sync_salvage": "sync_salvage",
    "salvage": "sync_salvage",
    "dispatch": "dispatch_task",
    "run": "dispatch_task",
    "work": "dispatch_task",
    "dispatch_task": "dispatch_task",
    "retry": "retry_task",
    "retry_task": "retry_task",
    "replan": "replan_task",
    "replan_task": "replan_task",
    "accept_proposal": "accept_proposal",
    "proposal_accept": "accept_proposal",
    "reject_proposal": "reject_proposal",
    "proposal_reject": "reject_proposal",
    "syncback_preview": "syncback_preview",
    "syncback_apply": "syncback_apply",
    "offdesk_prepare": "offdesk_prepare",
    "offdesk_review": "offdesk_review",
    "offdesk_start": "offdesk_start",
    "offdesk_on": "offdesk_start",
    "offdesk_status": "offdesk_status",
}


ACTION_CATALOG: Dict[str, Dict[str, Any]] = {
    "list_projects": {
        "family": "project_registry",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": False,
        "operator_surface": ["/map"],
        "required_args": [],
        "notes": ["Return visible project registry, scope, and quick project labels."],
    },
    "focus_project": {
        "family": "project_registry",
        "intent_class": "control",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/use O#", "/focus O#"],
        "required_args": [],
        "notes": ["Pin subsequent plain-language work to one project until cleared."],
    },
    "clear_focus": {
        "family": "project_registry",
        "intent_class": "control",
        "risk_level": "runtime_mutation",
        "requires_project": False,
        "operator_surface": ["/focus off", "/unlock"],
        "required_args": [],
        "notes": ["Clear project lock / focus state."],
    },
    "get_project_status": {
        "family": "project_status",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/orch status O#"],
        "required_args": [],
        "notes": ["Return compact project state, queue counts, latest task, and lock state."],
    },
    "monitor_project": {
        "family": "project_status",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/monitor", "/orch monitor O#"],
        "required_args": [],
        "notes": ["Return active task and Task Team lifecycle detail for operator monitoring."],
    },
    "get_queue": {
        "family": "backlog",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/queue", "/todo O#"],
        "required_args": [],
        "notes": ["Return runnable open todo rows, blocked head, and follow-up summary."],
    },
    "get_task": {
        "family": "task_runtime",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": False,
        "operator_surface": ["/task T-###", "/task REQ-..."],
        "required_args": ["task_ref"],
        "notes": ["Return detailed task lifecycle, tf_phase, lane state, and artifacts."],
    },
    "list_followups": {
        "family": "backlog",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/todo O# followup", "/queue followup"],
        "required_args": [],
        "notes": ["Return blocked/manual_followup backlog needing operator action."],
    },
    "sync_preview": {
        "family": "backlog_sync",
        "intent_class": "inspect",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/sync preview O# 24h"],
        "required_args": [],
        "notes": ["Read sources and show what would change without mutating queue state."],
    },
    "sync_apply": {
        "family": "backlog_sync",
        "intent_class": "work",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/sync O# 24h", "/sync replace O#"],
        "required_args": [],
        "notes": ["Mutate runtime queue from canonical/recent/salvage sources."],
    },
    "sync_bootstrap": {
        "family": "backlog_sync",
        "intent_class": "work",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/sync bootstrap O# 24h"],
        "required_args": [],
        "notes": ["Bootstrap queue from recent docs plus salvage when canonical backlog is missing or unreliable."],
    },
    "sync_salvage": {
        "family": "backlog_sync",
        "intent_class": "inspect",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/sync salvage O# 24h"],
        "required_args": [],
        "notes": ["Recover queue/proposals from recent work documents when canonical TODO is missing."],
    },
    "dispatch_task": {
        "family": "tf_execution",
        "intent_class": "work",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/dispatch ...", "plain text work request"],
        "required_args": ["objective"],
        "notes": ["Create or reuse a task, run Phase1 planning, then execute TF lanes."],
    },
    "retry_task": {
        "family": "tf_execution",
        "intent_class": "work",
        "risk_level": "runtime_mutation",
        "requires_project": False,
        "operator_surface": ["/retry T-###"],
        "required_args": ["task_ref"],
        "notes": ["Retry an existing task, possibly using lane-targeted rerun metadata."],
    },
    "replan_task": {
        "family": "tf_execution",
        "intent_class": "work",
        "risk_level": "runtime_mutation",
        "requires_project": False,
        "operator_surface": ["/replan T-###"],
        "required_args": ["task_ref"],
        "notes": ["Re-enter Phase1 planning for an existing task."],
    },
    "accept_proposal": {
        "family": "proposal_inbox",
        "intent_class": "control",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/todo accept PROP-###"],
        "required_args": ["proposal_ref"],
        "notes": ["Promote a follow-up proposal into runtime todo queue."],
    },
    "reject_proposal": {
        "family": "proposal_inbox",
        "intent_class": "control",
        "risk_level": "runtime_mutation",
        "requires_project": True,
        "operator_surface": ["/todo reject PROP-### reason"],
        "required_args": ["proposal_ref"],
        "notes": ["Reject a follow-up proposal while preserving audit trail."],
    },
    "syncback_preview": {
        "family": "canonical_backlog",
        "intent_class": "inspect",
        "risk_level": "safe",
        "requires_project": True,
        "operator_surface": ["/todo O# syncback preview"],
        "required_args": [],
        "notes": ["Show drift between runtime queue/proposals and canonical TODO.md."],
    },
    "syncback_apply": {
        "family": "canonical_backlog",
        "intent_class": "control",
        "risk_level": "canonical_mutation",
        "requires_project": True,
        "operator_surface": ["/todo O# syncback apply"],
        "required_args": [],
        "notes": ["Apply approved runtime drift back into canonical TODO.md."],
    },
    "offdesk_prepare": {
        "family": "offdesk",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": False,
        "operator_surface": ["/offdesk prepare"],
        "required_args": [],
        "notes": ["Compute readiness, drift, followups, and syncback warnings before night runs."],
    },
    "offdesk_review": {
        "family": "offdesk",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": False,
        "operator_surface": ["/offdesk review"],
        "required_args": [],
        "notes": ["List only flagged projects and direct operator to corrective actions."],
    },
    "offdesk_start": {
        "family": "offdesk",
        "intent_class": "control",
        "risk_level": "runtime_mutation",
        "requires_project": False,
        "operator_surface": ["/offdesk on", "/auto on"],
        "required_args": [],
        "notes": ["Start automation after readiness checks are acceptable."],
    },
    "offdesk_status": {
        "family": "offdesk",
        "intent_class": "status",
        "risk_level": "safe",
        "requires_project": False,
        "operator_surface": ["/offdesk status", "/auto status"],
        "required_args": [],
        "notes": ["Return compact automation state, scope, and latest result/failure."],
    },
}


def _contains_any(text: str, markers: List[str] | tuple[str, ...]) -> bool:
    low = _trim_text(text, 4000).lower()
    return any(str(marker).lower() in low for marker in markers)


def infer_mother_orch_action_call(
    prompt: str,
    *,
    default_project_key: str = "",
    has_active_task: bool = False,
) -> Dict[str, Any]:
    text = _trim_text(prompt, 2000)
    low = text.lower()
    if not text:
        raise RuntimeError("missing plaintext prompt for Control Plane action inference")

    offdesk_prepare_markers = ("offdesk prepare", "오프데스크 준비", "퇴근 준비", "야간 준비")
    offdesk_review_markers = ("offdesk review", "오프데스크 검토", "퇴근 검토", "야간 검토")
    offdesk_start_markers = ("offdesk on", "퇴근모드 시작", "오프데스크 시작", "야간 실행 시작")
    offdesk_status_markers = ("offdesk status", "auto status", "오프데스크 상태", "자동 상태")
    offdesk_scope_markers = (
        "offdesk",
        "오프데스크",
        "퇴근모드",
        "퇴근 모드",
        "야간",
        "night run",
        "nightly",
    )
    offdesk_timing_markers = (
        "퇴근 전",
        "퇴근전에",
        "오늘 밤",
        "오늘밤",
        "복귀 후",
        "복귀후",
        "내일 아침",
        "morning",
    )
    offdesk_prepare_scope_markers = (
        "준비",
        "세팅",
        "setup",
        "prepare",
        "preflight",
        "점검",
    )
    offdesk_review_scope_markers = (
        "검토",
        "리뷰",
        "review",
        "확인",
        "정리",
        "할일",
        "todo",
        "후속",
        "followup",
        "follow-up",
        "리스크",
        "경고",
        "문제",
    )
    map_markers = ("프로젝트 목록", "프로젝트들", "workspace", "워크스페이스", "map", "프로젝트 맵")
    queue_markers = ("queue", "대기열", "todo", "할일", "백로그")
    followup_markers = ("followup", "follow-up", "후속", "수동개입", "manual followup")
    sync_markers = ("sync", "동기화", "불러와", "가져와", "salvage")
    sync_bootstrap_markers = ("sync bootstrap", "bootstrap sync", "동기화 bootstrap", "부트스트랩 동기화")
    preview_markers = ("preview", "미리보기", "보기만", "점검")
    status_markers = ("상태", "진행", "결과", "언제", "모니터", "monitor", "progress", "result", "status")
    reporting_markers = (
        "보고",
        "보고서",
        "리포트",
        "문서",
        "작성 관점",
        "handoff",
        "writeup",
        "draft",
    )
    inspect_markers = (
        "확인",
        "정리",
        "조사",
        "분석",
        "검토",
        "살펴",
        "요약",
        "찾아",
        "봐줘",
        "check",
        "inspect",
        "review",
        "analyze",
        "investigate",
        "summarize",
    )
    work_markers = (
        "수정",
        "고쳐",
        "고치",
        "구현",
        "작성",
        "반영",
        "푸시",
        "push",
        "커밋",
        "commit",
        "머지",
        "merge",
        "설치",
        "실행",
        "진행",
        "진행해",
        "fix",
        "implement",
        "write",
        "apply",
        "ship",
        "deploy",
    )

    explicit_prepare_hits = _matching_markers(low, offdesk_prepare_markers)
    explicit_review_hits = _matching_markers(low, offdesk_review_markers)
    explicit_start_hits = _matching_markers(low, offdesk_start_markers)
    explicit_status_hits = _matching_markers(low, offdesk_status_markers)
    offdesk_scope_hits = _matching_markers(low, offdesk_scope_markers)
    offdesk_timing_hits = _matching_markers(low, offdesk_timing_markers)
    offdesk_prepare_hits = _matching_markers(low, offdesk_prepare_scope_markers)
    offdesk_review_hits = _matching_markers(low, offdesk_review_scope_markers)
    map_hits = _matching_markers(low, map_markers)
    queue_hits = _matching_markers(low, queue_markers)
    followup_hits = _matching_markers(low, followup_markers)
    sync_hits = _matching_markers(low, sync_markers)
    sync_bootstrap_hits = _matching_markers(low, sync_bootstrap_markers)
    preview_hits = _matching_markers(low, preview_markers)
    status_hits = _matching_markers(low, status_markers)
    reporting_hits = _matching_markers(low, reporting_markers)
    inspect_hits = _matching_markers(low, inspect_markers)
    work_hits = _matching_markers(low, work_markers)

    def _return(
        action_row: Dict[str, Any],
        *,
        matched: Dict[str, List[str]],
        safe_mode: str = "",
        why_not_dispatch: str = "",
    ) -> Dict[str, Any]:
        payload = dict(action_row)
        payload["intent_trace"] = _build_intent_trace(
            str(action_row.get("action", "")),
            matched=matched,
            safe_mode=safe_mode,
            why_not_dispatch=why_not_dispatch,
        )
        return normalize_mother_orch_action_call(payload, default_project_key=default_project_key)

    if explicit_prepare_hits:
        return _return({"action": "offdesk_prepare"}, matched={"offdesk_prepare": explicit_prepare_hits})
    if explicit_review_hits:
        return _return({"action": "offdesk_review"}, matched={"offdesk_review": explicit_review_hits})
    if explicit_start_hits:
        return _return({"action": "offdesk_start"}, matched={"offdesk_start": explicit_start_hits})
    if explicit_status_hits:
        return _return({"action": "offdesk_status"}, matched={"offdesk_status": explicit_status_hits})
    if offdesk_scope_hits:
        safe_mode = "prefer_control_over_dispatch" if work_hits else ""
        why_not_dispatch = "offdesk scope markers outrank work markers" if safe_mode else ""
        if offdesk_review_hits:
            return _return(
                {"action": "offdesk_review"},
                matched={"offdesk_scope": offdesk_scope_hits, "review": offdesk_review_hits, "work": work_hits},
                safe_mode=safe_mode,
                why_not_dispatch=why_not_dispatch,
            )
        if offdesk_prepare_hits:
            return _return(
                {"action": "offdesk_prepare"},
                matched={"offdesk_scope": offdesk_scope_hits, "prepare": offdesk_prepare_hits, "work": work_hits},
                safe_mode=safe_mode,
                why_not_dispatch=why_not_dispatch,
            )
        return _return(
            {"action": "offdesk_status"},
            matched={"offdesk_scope": offdesk_scope_hits, "work": work_hits},
            safe_mode=safe_mode,
            why_not_dispatch=why_not_dispatch,
        )
    if offdesk_timing_hits and reporting_hits and work_hits:
        return _return(
            {
                "action": "dispatch_task",
                "project_key": default_project_key,
                "objective": text,
                "readonly": False,
            },
            matched={"timing": offdesk_timing_hits, "reporting": reporting_hits, "work": work_hits},
            safe_mode="prefer_dispatch_for_reporting_work",
        )
    if offdesk_timing_hits and (offdesk_review_hits or offdesk_prepare_hits):
        safe_mode = "prefer_control_review_over_dispatch" if work_hits else "prefer_control_review"
        why_not_dispatch = "recovery/offdesk timing markers outrank work markers" if work_hits else ""
        if offdesk_review_hits:
            return _return(
                {"action": "offdesk_review"},
                matched={"timing": offdesk_timing_hits, "review": offdesk_review_hits, "work": work_hits},
                safe_mode=safe_mode,
                why_not_dispatch=why_not_dispatch,
            )
        return _return(
            {"action": "offdesk_prepare"},
            matched={"timing": offdesk_timing_hits, "prepare": offdesk_prepare_hits, "work": work_hits},
            safe_mode=safe_mode,
            why_not_dispatch=why_not_dispatch,
        )

    if followup_hits:
        return _return({"action": "list_followups", "project_key": default_project_key}, matched={"followup": followup_hits})
    if queue_hits:
        return _return({"action": "get_queue", "project_key": default_project_key}, matched={"queue": queue_hits})
    if map_hits:
        return _return({"action": "list_projects"}, matched={"map": map_hits})

    if sync_hits or sync_bootstrap_hits:
        if sync_bootstrap_hits:
            action = "sync_bootstrap"
        else:
            action = "sync_preview" if preview_hits else "sync_apply"
        return _return(
            {"action": action, "project_key": default_project_key, "window": "24h"},
            matched={"sync": sync_bootstrap_hits or sync_hits, "preview": preview_hits},
        )

    if work_hits:
        return _return(
            {
                "action": "dispatch_task",
                "project_key": default_project_key,
                "objective": text,
                "readonly": False,
            },
            matched={"work": work_hits},
        )

    if inspect_hits or reporting_hits:
        return _return(
            {
                "action": "dispatch_task",
                "project_key": default_project_key,
                "objective": text,
                "readonly": True,
            },
            matched={"inspect": inspect_hits, "reporting": reporting_hits},
        )

    if has_active_task and status_hits:
        return _return({"action": "monitor_project", "project_key": default_project_key}, matched={"status": status_hits})

    if status_hits and not work_hits:
        return _return({"action": "monitor_project", "project_key": default_project_key}, matched={"status": status_hits})

    return _return(
        {
            "action": "dispatch_task",
            "project_key": default_project_key,
            "objective": text,
            "readonly": True,
        },
        matched={"fallback": ["readonly-dispatch"]},
    )


def action_call_to_resolved_command(call: Any) -> Dict[str, Any]:
    row = call if isinstance(call, dict) else {}
    action = _canonical_action_name(row.get("action"))
    project_key = _trim_text(row.get("project_key", ""), 64)
    args = row.get("args") if isinstance(row.get("args"), dict) else {}

    if action == "list_projects":
        return {"cmd": "orch-list"}
    if action == "focus_project":
        return {"cmd": "orch-use", "orch_target": project_key or None}
    if action == "clear_focus":
        return {"cmd": "focus", "rest": "off"}
    if action == "get_project_status":
        return {"cmd": "orch-status", "orch_target": project_key or None}
    if action == "monitor_project":
        return {"cmd": "orch-monitor", "orch_target": project_key or None}
    if action == "get_queue":
        return {"cmd": "queue", "rest": project_key}
    if action == "get_task":
        return {"cmd": "orch-task", "orch_task_request_id": _trim_text(args.get("task_ref", ""), 120)}
    if action == "list_followups":
        rest = f"{project_key} followup".strip() if project_key else "followup"
        return {"cmd": "todo", "rest": rest}
    if action == "sync_preview":
        window = _trim_text(args.get("window", "24h"), 32) or "24h"
        rest = " ".join(part for part in ("preview", project_key, window) if part)
        return {"cmd": "sync", "rest": rest}
    if action == "sync_apply":
        window = _trim_text(args.get("window", "24h"), 32) or "24h"
        replace = bool(args.get("replace", False))
        rest = " ".join(part for part in (("replace" if replace else ""), project_key, window) if part)
        return {"cmd": "sync", "rest": rest}
    if action == "sync_bootstrap":
        window = _trim_text(args.get("window", "24h"), 32) or "24h"
        rest = " ".join(part for part in ("bootstrap", project_key, window) if part)
        return {"cmd": "sync", "rest": rest}
    if action == "sync_salvage":
        window = _trim_text(args.get("window", "24h"), 32) or "24h"
        rest = " ".join(part for part in ("salvage", project_key, window) if part)
        return {"cmd": "sync", "rest": rest}
    if action == "dispatch_task":
        return {
            "cmd": "run",
            "run_prompt": _trim_text(args.get("objective", ""), 1200),
            "run_force_mode": "dispatch",
            "run_auto_source": f"orch-action:{row.get('intent_class', 'work')}",
        }
    if action == "retry_task":
        lane_ids = args.get("lane_ids") or args.get("lane_ref") or []
        if isinstance(lane_ids, str):
            lane_ids = [lane_ids]
        return {
            "cmd": "orch-retry",
            "orch_retry_request_id": _trim_text(args.get("task_ref", ""), 120),
            "orch_retry_lane_ids": [_trim_text(item, 32) for item in lane_ids if _trim_text(item, 32)],
        }
    if action == "replan_task":
        lane_ids = args.get("lane_ids") or args.get("lane_ref") or []
        if isinstance(lane_ids, str):
            lane_ids = [lane_ids]
        return {
            "cmd": "orch-replan",
            "orch_replan_request_id": _trim_text(args.get("task_ref", ""), 120),
            "orch_replan_lane_ids": [_trim_text(item, 32) for item in lane_ids if _trim_text(item, 32)],
        }
    if action == "accept_proposal":
        return {"cmd": "todo", "rest": f"accept {_trim_text(args.get('proposal_ref', ''), 120)}".strip()}
    if action == "reject_proposal":
        reason = _trim_text(args.get("reason", ""), 240)
        rest = f"reject {_trim_text(args.get('proposal_ref', ''), 120)} {reason}".strip()
        return {"cmd": "todo", "rest": rest}
    if action == "syncback_preview":
        rest = f"{project_key} syncback preview".strip() if project_key else "syncback preview"
        return {"cmd": "todo", "rest": rest}
    if action == "syncback_apply":
        rest = f"{project_key} syncback apply".strip() if project_key else "syncback apply"
        return {"cmd": "todo", "rest": rest}
    if action == "offdesk_prepare":
        return {"cmd": "offdesk", "rest": "prepare"}
    if action == "offdesk_review":
        return {"cmd": "offdesk", "rest": "review"}
    if action == "offdesk_start":
        return {"cmd": "offdesk", "rest": "on"}
    if action == "offdesk_status":
        return {"cmd": "offdesk", "rest": "status"}
    raise RuntimeError(f"unsupported Control Plane action mapping: {action}")


def _trim_text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _normalize_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on", "y"}:
        return True
    if token in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _canonical_action_name(raw: Any) -> str:
    token = _trim_text(raw, 64).lower().replace(" ", "_")
    if not token:
        raise RuntimeError("missing Control Plane action name")
    canonical = ACTION_ALIASES.get(token, token)
    if canonical not in ACTION_CATALOG:
        raise RuntimeError(f"unknown Control Plane action: {raw}")
    return canonical


def _normalize_project_key(raw: Any, default_project_key: str = "") -> str:
    token = _trim_text(raw, 64) or _trim_text(default_project_key, 64)
    return token


def _matching_markers(text: str, markers: Any) -> List[str]:
    if not isinstance(markers, (list, tuple)):
        return []
    hits: List[str] = []
    for item in markers:
        token = _trim_text(item, 64).lower()
        if token and token in text and token not in hits:
            hits.append(token)
    return hits


def _build_intent_trace(
    selected_action: str,
    *,
    matched: Dict[str, List[str]] | None = None,
    safe_mode: str = "",
    why_not_dispatch: str = "",
) -> str:
    parts: List[str] = [f"selected={_trim_text(selected_action, 64)}"]
    if isinstance(matched, dict):
        matched_rows: List[str] = []
        for key, values in matched.items():
            if not isinstance(values, list) or not values:
                continue
            shown = "|".join(_trim_text(item, 32) for item in values[:2] if _trim_text(item, 32))
            if shown:
                matched_rows.append(f"{_trim_text(key, 32)}:{shown}")
        if matched_rows:
            parts.append(f"matched={','.join(matched_rows[:4])}")
    if safe_mode:
        parts.append(f"safe_mode={_trim_text(safe_mode, 64)}")
    if why_not_dispatch:
        parts.append(f"why_not_dispatch={_trim_text(why_not_dispatch, 160)}")
    return _trim_text("; ".join(part for part in parts if part), 400)


def mother_orch_action_api_schema() -> Dict[str, Any]:
    actions: Dict[str, Any] = {}
    for name, row in ACTION_CATALOG.items():
        actions[name] = {
            "family": row["family"],
            "intent_class": row["intent_class"],
            "risk_level": row["risk_level"],
            "requires_project": bool(row["requires_project"]),
            "required_args": list(row["required_args"]),
            "operator_surface": list(row["operator_surface"]),
            "notes": list(row["notes"]),
        }
    return {
        "contract": "mother_orch.action_api.v1",
        "intent_classes": list(INTENT_CLASSES),
        "risk_levels": list(RISK_LEVELS),
        "actions": actions,
        "notes": [
            "This is the stable control-plane seam Control Plane should use before any MCP adapter is added.",
            "Adapters may convert Telegram/CLI/MCP requests into this call envelope, but must not bypass it.",
            "Action API owns intent and mutation boundaries; execution backends stay downstream.",
        ],
    }


def list_mother_orch_actions() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name in sorted(ACTION_CATALOG.keys()):
        row = ACTION_CATALOG[name]
        rows.append(
            {
                "action": name,
                "family": row["family"],
                "intent_class": row["intent_class"],
                "risk_level": row["risk_level"],
                "requires_project": bool(row["requires_project"]),
                "required_args": list(row["required_args"]),
                "operator_surface": list(row["operator_surface"]),
            }
        )
    return rows


def normalize_mother_orch_action_call(
    raw: Any,
    *,
    default_project_key: str = "",
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {"action": raw}
    action = _canonical_action_name(data.get("action"))
    definition = ACTION_CATALOG[action]

    args_in = data.get("args")
    args = dict(args_in) if isinstance(args_in, dict) else {}
    project_key = _normalize_project_key(
        data.get("project_key", args.get("project_key", "")),
        default_project_key=default_project_key,
    )
    if definition["requires_project"] and not project_key:
        raise RuntimeError(f"{action} requires project_key")

    normalized_args: Dict[str, Any] = {}
    if action in {"get_task", "retry_task", "replan_task"}:
        task_ref = _trim_text(data.get("task_ref", args.get("task_ref", "")), 120)
        if not task_ref:
            raise RuntimeError(f"{action} requires task_ref")
        normalized_args["task_ref"] = task_ref
    elif action in {"accept_proposal", "reject_proposal"}:
        proposal_ref = _trim_text(data.get("proposal_ref", args.get("proposal_ref", "")), 120)
        if not proposal_ref:
            raise RuntimeError(f"{action} requires proposal_ref")
        normalized_args["proposal_ref"] = proposal_ref
        reject_reason = _trim_text(data.get("reason", args.get("reason", "")), 240)
        if action == "reject_proposal" and reject_reason:
            normalized_args["reason"] = reject_reason
    elif action == "dispatch_task":
        objective = _trim_text(
            data.get("objective", data.get("prompt", args.get("objective", args.get("prompt", "")))),
            1200,
        )
        if not objective:
            raise RuntimeError("dispatch_task requires objective")
        normalized_args["objective"] = objective
        normalized_args["title"] = _trim_text(data.get("title", args.get("title", "")), 160) or objective[:160]
        normalized_args["readonly"] = _normalize_bool(data.get("readonly", args.get("readonly", False)), False)
        roles = args.get("requested_roles", data.get("requested_roles", []))
        if isinstance(roles, list):
            normalized_args["requested_roles"] = [_trim_text(item, 64) for item in roles if _trim_text(item, 64)]
    elif action in {"sync_preview", "sync_apply", "sync_salvage", "sync_bootstrap"}:
        normalized_args["window"] = _trim_text(data.get("window", args.get("window", "")), 32) or "24h"
        if action == "sync_apply":
            normalized_args["replace"] = _normalize_bool(data.get("replace", args.get("replace", False)), False)
    elif action in {"syncback_preview", "syncback_apply"}:
        normalized_args["include_blocked_notes"] = _normalize_bool(
            data.get("include_blocked_notes", args.get("include_blocked_notes", True)),
            True,
        )

    for key in definition["required_args"]:
        if key not in normalized_args:
            raise RuntimeError(f"{action} requires {key}")

    row = {
        "action": action,
        "family": definition["family"],
        "intent_class": definition["intent_class"],
        "risk_level": definition["risk_level"],
        "project_key": project_key,
        "readonly": definition["risk_level"] == "safe"
        if action != "dispatch_task"
        else bool(normalized_args.get("readonly", False)),
        "mutates_runtime": definition["risk_level"] in {"runtime_mutation", "canonical_mutation"},
        "mutates_canonical": definition["risk_level"] == "canonical_mutation",
        "operator_surface": list(definition["operator_surface"]),
        "args": normalized_args,
    }
    intent_trace = _trim_text(data.get("intent_trace", ""), 400)
    if intent_trace:
        row["intent_trace"] = intent_trace
    return row
