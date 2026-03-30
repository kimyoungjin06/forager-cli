#!/usr/bin/env python3
"""Schema coercion helpers for planner / critic payloads."""

from __future__ import annotations

from typing import Any, Dict, List

from aoe_tg_orch_contract import normalize_phase2_execution_plan, normalize_phase2_team_spec
from aoe_tg_orch_roles import classify_dispatch_role_preset, normalize_role_preset


def _trim_text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _normalize_approval_mode(raw: Any) -> str:
    token = str(raw or "").strip().lower()
    if token in {"policy", "confirm", "none"}:
        return token
    return "policy"


def _normalize_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on", "y"}:
        return True
    if token in {"0", "false", "no", "off", "n"}:
        return False
    return bool(default)


def _contains_any(text: str, markers: List[str]) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    return any(token in low for token in markers if token)


def _is_build_like_role(role: str) -> bool:
    low = str(role or "").strip().lower()
    if not low:
        return False
    return any(token in low for token in ("dev", "engineer", "builder", "implement"))


def _is_review_like_role(role: str) -> bool:
    low = str(role or "").strip().lower()
    if not low:
        return False
    return any(token in low for token in ("review", "critic", "verif", "qa"))


def _role_matches_preset(role: str, preset: str) -> bool:
    low = str(role or "").strip().lower()
    normalized_preset = normalize_role_preset(preset)
    if normalized_preset in {"general", "mixed"}:
        return True
    if normalized_preset == "build":
        return _is_build_like_role(role)
    if normalized_preset == "writer":
        return any(token in low for token in ("writer", "doc", "scribe"))
    if normalized_preset == "analysis":
        return any(token in low for token in ("analyst", "analysis", "research"))
    if normalized_preset == "data":
        return "data" in low
    if normalized_preset == "review":
        return _is_review_like_role(role)
    return False


def _coerce_owner_role_for_preset(role: str, *, preset: str, worker_roles: List[str]) -> str:
    normalized_preset = normalize_role_preset(preset)
    if normalized_preset in {"general", "mixed"}:
        return role

    preferred = [item for item in worker_roles if _role_matches_preset(item, normalized_preset)]
    if not preferred:
        return role
    if role in preferred:
        return role
    if normalized_preset != "review" and _is_review_like_role(role):
        return preferred[0]
    if not _role_matches_preset(role, normalized_preset):
        return preferred[0]
    return preferred[0]


def _build_acceptance_floor(
    *,
    user_prompt: str,
    preset: str,
    role: str,
    title: str,
    goal: str,
) -> List[str]:
    normalized_preset = normalize_role_preset(preset)
    if normalized_preset != "build":
        return []
    if not _is_build_like_role(role):
        return []

    auth_markers = [
        "login",
        "log in",
        "signin",
        "sign in",
        "auth",
        "session",
        "token",
        "expiry",
        "expired",
        "credential",
        "세션",
        "로그인",
        "인증",
        "토큰",
        "만료",
    ]
    context = "\n".join((str(user_prompt or ""), str(title or ""), str(goal or "")))
    if not _contains_any(context, auth_markers):
        return []

    return [
        "Caller-visible or persisted auth/session state changes are explicit, not only helper return values.",
        "Verification covers the failure path state after the login/session error, starting from an existing auth/session state and including stored token/session invalidation when applicable.",
    ]


def _merge_acceptance_floor(acceptance: List[str], floor: List[str]) -> List[str]:
    out: List[str] = []
    for item in list(acceptance or []) + list(floor or []):
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token[:240])
    return out[:3]


def default_plan_critic_payload() -> Dict[str, Any]:
    return {"approved": True, "issues": [], "recommendations": []}


def default_exec_critic_payload(
    *,
    verdict: str = "fail",
    action: str = "escalate",
    reason: str = "critic_parse_error",
    fix: str = "",
    attempt_no: int = 1,
    max_attempts: int = 3,
    at: str = "",
    rerun_execution_lane_ids: List[str] | None = None,
    rerun_review_lane_ids: List[str] | None = None,
    manual_followup_execution_lane_ids: List[str] | None = None,
    manual_followup_review_lane_ids: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "verdict": verdict,
        "action": action,
        "reason": _trim_text(reason, 200),
        "fix": _trim_text(fix, 600),
        "attempt": max(1, int(attempt_no or 1)),
        "max_attempts": max(1, int(max_attempts or 1)),
        "at": str(at or "").strip(),
        "rerun_execution_lane_ids": [str(x).strip()[:32] for x in (rerun_execution_lane_ids or []) if str(x).strip()],
        "rerun_review_lane_ids": [str(x).strip()[:32] for x in (rerun_review_lane_ids or []) if str(x).strip()],
        "manual_followup_execution_lane_ids": [
            str(x).strip()[:32] for x in (manual_followup_execution_lane_ids or []) if str(x).strip()
        ],
        "manual_followup_review_lane_ids": [
            str(x).strip()[:32] for x in (manual_followup_review_lane_ids or []) if str(x).strip()
        ],
    }


def normalize_task_plan_payload(
    parsed: Any,
    *,
    user_prompt: str,
    workers: List[str],
    max_subtasks: int,
    meta_overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    role_map = {str(r).strip().lower(): str(r).strip() for r in (workers or []) if str(r).strip()}
    worker_list = list(role_map.values()) or ["Worker"]

    summary = ""
    raw_subtasks: List[Any] = []
    meta_in: Dict[str, Any] = {}
    if isinstance(parsed, dict):
        summary = str(parsed.get("summary", "")).strip()
        if isinstance(parsed.get("subtasks"), list):
            raw_subtasks = parsed.get("subtasks") or []
        if isinstance(parsed.get("meta"), dict):
            meta_in = dict(parsed.get("meta") or {})
    if isinstance(meta_overrides, dict):
        meta_in.update({str(key): value for key, value in meta_overrides.items()})

    meta_worker_roles = meta_in.get("worker_roles")
    worker_roles: List[str] = []
    if isinstance(meta_worker_roles, list):
        for row in meta_worker_roles:
            token = str(row or "").strip()
            if token and token not in worker_roles:
                worker_roles.append(token[:64])
    if not worker_roles:
        worker_roles = worker_list[:]

    phase1_role_preset = normalize_role_preset(
        meta_in.get("phase1_role_preset") or classify_dispatch_role_preset(user_prompt, selected_roles=worker_roles)
    )
    phase2_team_preset = normalize_role_preset(meta_in.get("phase2_team_preset") or phase1_role_preset)
    approval_mode = _normalize_approval_mode(meta_in.get("approval_mode", "policy"))
    readonly = _normalize_bool(meta_in.get("readonly", False), False)

    normalized: List[Dict[str, Any]] = []
    for i, row in enumerate(raw_subtasks, start=1):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id", f"S{i}")).strip() or f"S{i}"
        title = str(row.get("title", "")).strip() or str(row.get("goal", "")).strip() or f"Subtask {i}"
        goal = str(row.get("goal", "")).strip() or title

        role_raw = str(row.get("owner_role", row.get("role", ""))).strip()
        if role_raw and role_raw.lower() in role_map:
            role = role_map[role_raw.lower()]
        elif role_raw:
            role = role_raw
        else:
            role = worker_list[min(i - 1, len(worker_list) - 1)]
        role = _coerce_owner_role_for_preset(role, preset=phase2_team_preset, worker_roles=worker_roles)

        acceptance: List[str] = []
        raw_acceptance = row.get("acceptance")
        if isinstance(raw_acceptance, list):
            for item in raw_acceptance:
                token = str(item or "").strip()
                if token:
                    acceptance.append(token[:240])
        if not acceptance:
            acceptance = [f"{title} 결과가 사용자 요청과 직접 연결되어 설명된다."]
        acceptance = _merge_acceptance_floor(
            acceptance,
            _build_acceptance_floor(
                user_prompt=user_prompt,
                preset=phase2_team_preset,
                role=role,
                title=title,
                goal=goal,
            ),
        )

        normalized.append(
            {
                "id": sid[:32],
                "title": title[:160],
                "goal": goal[:400],
                "owner_role": role[:64],
                "acceptance": acceptance[:3],
            }
        )

    limit = max(1, int(max_subtasks or 1))
    normalized = normalized[:limit]
    if not normalized:
        normalized = [
            {
                "id": "S1",
                "title": "요청 핵심 실행",
                "goal": str(user_prompt or "").strip() or "사용자 요청 실행",
                "owner_role": worker_list[0],
                "acceptance": ["요청에 대한 실행/검증 결과가 사용자 관점으로 정리된다."],
            }
        ]

    if not summary:
        summary = f"subtasks={len(normalized)}"

    plan_payload = {
        "summary": summary[:240],
        "subtasks": normalized,
        "meta": {
            "max_subtasks": limit,
            "worker_roles": worker_roles,
            "phase1_role_preset": phase1_role_preset,
            "phase2_team_preset": phase2_team_preset,
            "approval_mode": approval_mode,
            "readonly": readonly,
        },
    }

    raw_phase2 = meta_in.get("phase2_team_spec")
    if raw_phase2 is None and isinstance(parsed, dict):
        raw_phase2 = parsed.get("phase2_team_spec")
    verifier_roles = [
        role
        for role in worker_roles
        if any(key in str(role).lower() for key in ("review", "critic", "verif", "qa"))
    ]

    plan_payload["meta"]["phase2_team_spec"] = normalize_phase2_team_spec(
        raw_phase2,
        plan=plan_payload,
        roles=worker_roles,
        verifier_roles=verifier_roles,
        require_verifier=bool(verifier_roles),
    )
    raw_phase2_exec = meta_in.get("phase2_execution_plan")
    if raw_phase2_exec is None and isinstance(parsed, dict):
        raw_phase2_exec = parsed.get("phase2_execution_plan")
    plan_payload["meta"]["phase2_execution_plan"] = normalize_phase2_execution_plan(
        raw_phase2_exec,
        team_spec=plan_payload["meta"]["phase2_team_spec"],
        readonly=readonly,
    )
    return plan_payload


def normalize_plan_critic_payload(parsed: Any, *, max_items: int = 5) -> Dict[str, Any]:
    approved = True
    issues: List[str] = []
    recommendations: List[str] = []

    if isinstance(parsed, dict):
        approved = bool(parsed.get("approved", True))
        raw_issues = parsed.get("issues")
        if isinstance(raw_issues, list):
            for item in raw_issues:
                token = str(item or "").strip()
                if token:
                    issues.append(token[:240])
        raw_recs = parsed.get("recommendations")
        if isinstance(raw_recs, list):
            for item in raw_recs:
                token = str(item or "").strip()
                if token:
                    recommendations.append(token[:240])

    return {
        "approved": approved,
        "issues": issues[: max(1, int(max_items or 1))],
        "recommendations": recommendations[: max(1, int(max_items or 1))],
    }


def plan_payload_approval_mode(plan: Any) -> str:
    if isinstance(plan, dict):
        meta = plan.get("meta")
        if isinstance(meta, dict):
            return _normalize_approval_mode(meta.get("approval_mode", "policy"))
    return "policy"


def _is_policy_approval_issue(issue: str) -> bool:
    low = str(issue or "").strip().lower()
    if not low:
        return False
    approval_markers = (
        "dri",
        "approver",
        "approval",
        "final approval",
        "human approval",
        "operator approval",
        "sign-off",
        "signoff",
        "승인자",
        "최종 승인",
        "사람 승인",
        "인간 승인",
        "결정권자",
    )
    return any(marker in low for marker in approval_markers)


def apply_plan_critic_approval_mode(
    parsed: Any,
    *,
    approval_mode: str,
    max_items: int = 5,
) -> Dict[str, Any]:
    payload = normalize_plan_critic_payload(parsed, max_items=max_items)
    mode = _normalize_approval_mode(approval_mode)
    if mode == "confirm":
        return payload

    moved: List[str] = []
    kept_issues: List[str] = []
    for issue in payload.get("issues") or []:
        token = _trim_text(issue, 240)
        if token and _is_policy_approval_issue(token):
            moved.append(token)
        elif token:
            kept_issues.append(token)

    recommendations = [str(item).strip()[:240] for item in (payload.get("recommendations") or []) if str(item).strip()]
    for issue in moved:
        if mode == "none":
            note = _trim_text(f"approval_not_required_note: {issue}", 240)
        else:
            note = _trim_text(f"approval_policy_note: {issue}", 240)
        if note and note not in recommendations:
            recommendations.append(note)

    approved = bool(payload.get("approved", True))
    if moved and not kept_issues:
        approved = True

    return {
        "approved": approved,
        "issues": kept_issues[: max(1, int(max_items or 1))],
        "recommendations": recommendations[: max(1, int(max_items or 1))],
    }


def plan_critic_primary_issue(parsed: Any, *, limit: int = 240) -> str:
    payload = normalize_plan_critic_payload(parsed, max_items=1)
    issues = payload.get("issues") or []
    if not issues:
        return ""
    return _trim_text(issues[0], limit)


def normalize_plan_replans_payload(raw: Any, *, keep: int = 80) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw[-max(1, int(keep or 1)) :]:
        if not isinstance(item, dict):
            continue
        try:
            attempt = max(1, int(item.get("attempt", 0) or 0))
        except Exception:
            attempt = 1
        critic = str(item.get("critic", "")).strip().lower()
        if critic not in {"approved", "needs_fix"}:
            critic = "unknown"
        try:
            subtasks = max(0, int(item.get("subtasks", 0) or 0))
        except Exception:
            subtasks = 0
        rows.append({"attempt": attempt, "critic": critic, "subtasks": subtasks})
    return rows


def normalize_exec_critic_payload(
    parsed: Any,
    *,
    attempt_no: int,
    max_attempts: int,
    at: str,
) -> Dict[str, Any]:
    verdict = "fail"
    action = "escalate"
    reason = "critic_parse_error"
    fix = ""
    rerun_execution_lane_ids: List[str] = []
    rerun_review_lane_ids: List[str] = []
    manual_followup_execution_lane_ids: List[str] = []
    manual_followup_review_lane_ids: List[str] = []

    if isinstance(parsed, dict):
        verdict_map = {
            "success": "success",
            "ok": "success",
            "pass": "success",
            "retry": "retry",
            "retriable": "retry",
            "fail": "fail",
            "failed": "fail",
            "error": "fail",
            "성공": "success",
            "재시도": "retry",
            "재실행": "retry",
            "실패": "fail",
        }
        action_map = {
            "none": "none",
            "noop": "none",
            "retry": "retry",
            "replan": "replan",
            "escalate": "escalate",
        }
        vraw = str(parsed.get("verdict", "")).strip().lower()
        araw = str(parsed.get("action", "")).strip().lower()
        verdict = verdict_map.get(vraw, verdict)
        action = action_map.get(araw, "")
        reason = _trim_text(parsed.get("reason", "") or reason, 200) or reason
        fix = _trim_text(parsed.get("fix", ""), 600)
        rerun_execution_lane_ids = [str(x).strip()[:32] for x in (parsed.get("rerun_execution_lane_ids") or []) if str(x).strip()]
        rerun_review_lane_ids = [str(x).strip()[:32] for x in (parsed.get("rerun_review_lane_ids") or []) if str(x).strip()]
        manual_followup_execution_lane_ids = [
            str(x).strip()[:32] for x in (parsed.get("manual_followup_execution_lane_ids") or []) if str(x).strip()
        ]
        manual_followup_review_lane_ids = [
            str(x).strip()[:32] for x in (parsed.get("manual_followup_review_lane_ids") or []) if str(x).strip()
        ]

    if verdict == "success":
        action = "none"
    elif verdict == "retry":
        action = action if action in {"retry", "replan"} else "retry"
    else:
        verdict = "fail"
        action = "escalate"

    return default_exec_critic_payload(
        verdict=verdict,
        action=action,
        reason=reason,
        fix=fix,
        attempt_no=attempt_no,
        max_attempts=max_attempts,
        at=at,
        rerun_execution_lane_ids=rerun_execution_lane_ids,
        rerun_review_lane_ids=rerun_review_lane_ids,
        manual_followup_execution_lane_ids=manual_followup_execution_lane_ids,
        manual_followup_review_lane_ids=manual_followup_review_lane_ids,
    )
