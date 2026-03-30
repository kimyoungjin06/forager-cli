#!/usr/bin/env python3
"""Phase1 ensemble planning helpers.

Phase1 is planner-only:
- same mission is given to Codex and Claude
- plans are critiqued and shared for multiple rounds
- only after the plan is stable do we enter Phase2 execution
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_provider_fallback import (
    build_rate_limit_snapshot,
    extract_retry_after_sec,
    fallback_provider_for,
    is_rate_limit_error,
    load_provider_capacity_state,
    proactive_fallback_provider,
)
from aoe_tg_schema import (
    apply_plan_critic_approval_mode,
    default_plan_critic_payload,
    normalize_plan_critic_payload,
    plan_critic_primary_issue,
    plan_payload_approval_mode,
)


def _trim_text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _dedupe_lines(rows: List[str], *, limit: int) -> List[str]:
    out: List[str] = []
    for row in rows:
        token = _trim_text(row, 240)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit or 1))]


def _normalize_plan_issue_code(issue: Any) -> str:
    low = str(issue or "").strip().lower()
    if not low:
        return ""
    if "missing required contract fields" in low or "contract_incomplete" in low:
        return "contract_incomplete"
    if "contract ambiguity" in low or "contract_ambiguous" in low:
        return "contract_ambiguous"
    if "acceptance" in low or "완료조건" in low or "검증 기준" in low:
        return "acceptance_gap"
    if "artifact" in low or "산출물" in low or any(token in low for token in (".json", ".md", ".csv")):
        return "artifact_contract_gap"
    if "readonly" in low or "read-only" in low:
        return "readonly_drift"
    if "depends_on" in low or "dependency" in low or "의존" in low:
        return "invalid_dependency"
    if "owner" in low or "lane" in low or "role" in low or "소유" in low:
        return "ownership_gap"
    if "approval" in low or "dri" in low or "승인" in low:
        return "approval_gap"
    return "critic_issue"


def _issue_codes_from_critic(critic: Dict[str, Any]) -> List[str]:
    codes: List[str] = []
    for issue in list((critic or {}).get("issues") or []):
        code = _normalize_plan_issue_code(issue)
        if code and code not in codes:
            codes.append(code)
    return codes[:8]


def _dedupe_issue_codes(rows: List[Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for code in list(row.get("issue_codes") or []):
            token = str(code or "").strip().lower()
            if token and token not in out:
                out.append(token[:64])
    return out[:12]


def _detect_stalled_issue(rows: List[Dict[str, Any]]) -> str:
    primaries = [
        str(row.get("primary_issue", "")).strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("primary_issue", "")).strip()
    ]
    if len(primaries) < 2:
        return ""
    if primaries[-1] and primaries[-1] == primaries[-2]:
        return primaries[-1][:240]
    return ""


def _issue_row(*, round_no: int, provider: str, critic: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_plan_critic_payload(critic or {}, max_items=8)
    primary_issue = plan_critic_primary_issue(normalized, limit=240)
    row: Dict[str, Any] = {
        "round": max(1, int(round_no or 1)),
        "provider": str(provider or "").strip()[:64],
        "status": "approved" if not list(normalized.get("issues") or []) else "issues",
        "issue_count": len(list(normalized.get("issues") or [])),
        "issue_codes": _issue_codes_from_critic(normalized),
    }
    if primary_issue:
        row["primary_issue"] = primary_issue
    return row


def _auth_session_scope_guidance(user_prompt: str) -> str:
    low = str(user_prompt or "").strip().lower()
    markers = (
        "login",
        "log in",
        "signin",
        "sign in",
        "auth",
        "session",
        "token",
        "expiry",
        "expired",
        "로그인",
        "인증",
        "세션",
        "토큰",
        "만료",
    )
    if not any(marker in low for marker in markers):
        return ""
    return (
        "- auth/session/login expiry류 요청이면 먼저 실제 실패 경계(entrypoint, caller-visible state, persisted session/token store)를 추적하는 scope 확인 단계를 포함하라\n"
        "- helper 함수 하나만으로 충분하다고 단정하지 말고, 그것이 유일한 공개 경계인지 확인하거나 다른 호출 지점/저장소 경로를 검토했음을 acceptance에 명시하라\n"
    )


def _single_execution_role_guidance(workers: List[str]) -> str:
    execution_roles = [
        role
        for role in (workers or [])
        if role and not any(key in str(role).lower() for key in ("review", "critic", "verif", "qa"))
    ]
    if len(execution_roles) != 1:
        return ""
    role = str(execution_roles[0]).strip()
    return (
        f"- 현재 execution role이 `{role}` 하나뿐이면 single serial lane도 허용된다. 병렬 lane을 억지로 만들지 마라\n"
        "- 대신 scope 확인, 구현, 테스트, evidence 단계의 순차 의존성과 각 단계 산출물을 명시해 dispatch 가능성을 보여라\n"
    )


def _single_execution_role_critic_guidance(workers: List[str]) -> str:
    execution_roles = [
        role
        for role in (workers or [])
        if role and not any(key in str(role).lower() for key in ("review", "critic", "verif", "qa"))
    ]
    if len(execution_roles) != 1:
        return ""
    role = str(execution_roles[0]).strip()
    return (
        f"- execution role이 `{role}` 하나뿐이면 single serial lane 자체만으로 blocker를 만들지 마라\n"
        "- 대신 단계별 산출물, 순차 의존성, reviewer lane 연계가 명확한지 본다\n"
    )


def _planner_prompt(
    *,
    user_prompt: str,
    provider: str,
    workers: List[str],
    max_subtasks: int,
    round_no: int,
    total_rounds: int,
    shared_feedback: str,
) -> str:
    feedback = f"\n공유된 이전 회차 피드백:\n{shared_feedback}\n" if shared_feedback else ""
    scope_guidance = _auth_session_scope_guidance(user_prompt)
    serial_guidance = _single_execution_role_guidance(workers)
    return (
        "너는 TF Phase1 planner다. 지금은 실행이 아니라 계획 수립 단계다.\n"
        "같은 미션이 여러 planner(Codex/Claude)에게 병렬로 전달되고, 각 회차마다 서로의 비판 내용을 반영해 계획을 개선한다.\n"
        "반드시 JSON 객체만 출력한다. 설명 문장 금지.\n"
        "JSON 스키마:\n"
        "{\n"
        '  "summary": "한 줄 요약",\n'
        '  "subtasks": [\n'
        '    {"id":"S1", "title":"...", "goal":"...", "owner_role":"ROLE", "acceptance":["..."]}\n'
        "  ]\n"
        "}\n"
        "규칙:\n"
        f"- planner_provider: {provider}\n"
        f"- round: {round_no}/{total_rounds}\n"
        f"- owner_role은 다음 중 하나만 사용: {', '.join(workers)}\n"
        f"- subtasks는 1~{max(1, int(max_subtasks))}개\n"
        "- 각 subtask는 겹치지 않는 산출물과 검증 기준을 가져야 한다\n"
        "- 실행팀이 병렬로 일할 수 있으면 독립 가능한 단위로 분해한다\n"
        "- Codex-Reviewer/critic이 최종 검증할 수 있도록 acceptance를 구체적으로 쓴다\n"
        "- reviewer/verifier/QA/independent review 자체를 별도 execution subtask로 만들지 마라\n"
        "- 독립 리뷰, 회귀 판정, 승인 확인은 subtask가 아니라 acceptance/evidence로 남기고 Phase2 review lane이 담당하게 하라\n"
        f"{scope_guidance}"
        f"{serial_guidance}"
        "- approval_mode는 기본적으로 policy다. 최종 승인/복귀는 Control Plane operator가 맡고, Task Team 내부 역할에 가짜 DRI/최종 승인자를 만들지 마라\n"
        "- 사람 승인 필요는 acceptance/evidence/manual follow-up 성격으로 표현하라\n"
        "- 계획이 덜 완성됐으면 범위를 줄이고, ambiguity를 드러내라\n"
        f"{feedback}\n"
        f"사용자 요청:\n{user_prompt.strip()}\n"
    )


def _critic_prompt(
    *,
    user_prompt: str,
    provider: str,
    planner_provider: str,
    plan: Dict[str, Any],
    round_no: int,
    total_rounds: int,
) -> str:
    payload = json.dumps(plan, ensure_ascii=False)
    scope_guidance = _auth_session_scope_guidance(user_prompt)
    serial_guidance = _single_execution_role_critic_guidance(list(plan.get("meta", {}).get("worker_roles") or []))
    return (
        "너는 TF Phase1 critic이다. 아래 계획이 실제 실행 단계(Phase2)로 넘어갈 만큼 충분히 구체적인지 비판적으로 검토해라.\n"
        "반드시 JSON 객체만 출력한다. 설명 문장 금지.\n"
        "JSON 스키마:\n"
        "{\n"
        '  "approved": true|false,\n'
        '  "issues": ["..."],\n'
        '  "recommendations": ["..."]\n'
        "}\n"
        "규칙:\n"
        f"- critic_provider: {provider}\n"
        f"- planner_provider: {planner_provider}\n"
        f"- round: {round_no}/{total_rounds}\n"
        "- execution gap, role mismatch, acceptance weakness, hidden dependency를 우선 지적한다\n"
        "- plans that are too broad or dispatch 책임이 모호하면 승인하지 마라\n"
        "- issues는 정말 dispatch를 막을 문제만 적는다\n\n"
        "- review/approval/QA를 별도 execution subtask로 넣은 계획은 blocker로 지적한다. 그런 요구는 Phase2 review lane의 acceptance/evidence로 표현되어야 한다\n"
        f"{scope_guidance}"
        f"{serial_guidance}"
        "- auth/session/login expiry류 계획이 helper 함수 하나만 실제 실패 경계라고 가정하면 blocker로 지적한다\n"
        "- operator approval/recovery는 Task Team 바깥의 Control Plane 책임이다\n"
        "- reviewer/critic role이 있다는 이유만으로 human approver/DRI 부재를 blocker로 만들지 마라\n"
        "- approval 필요성은 acceptance/evidence/manual follow-up으로 남겨라\n\n"
        f"사용자 요청:\n{user_prompt.strip()}\n\n"
        f"plan:\n{payload}\n"
    )


def _candidate_score(critic: Dict[str, Any], plan: Dict[str, Any]) -> tuple[int, int, int]:
    issues = critic.get("issues") or []
    approved = bool(critic.get("approved", True)) and not bool(issues)
    subtasks = plan.get("subtasks") or []
    return (
        0 if approved else 1,
        len(issues),
        -len(subtasks) if isinstance(subtasks, list) else 0,
    )


def _render_shared_feedback(round_candidates: List[Dict[str, Any]], *, best_idx: int) -> str:
    lines: List[str] = []
    for idx, row in enumerate(round_candidates, start=1):
        provider = str(row.get("provider", "")).strip() or f"planner-{idx}"
        plan = row.get("plan") if isinstance(row.get("plan"), dict) else {}
        critic = row.get("critic") if isinstance(row.get("critic"), dict) else default_plan_critic_payload()
        summary = _trim_text(plan.get("summary", ""), 180) or "no summary"
        marker = "best" if (idx - 1) == best_idx else "alt"
        lines.append(f"[{provider}|{marker}] {summary}")
        for issue in (critic.get("issues") or [])[:3]:
            lines.append(f"- issue: {_trim_text(issue, 200)}")
        for rec in (critic.get("recommendations") or [])[:3]:
            lines.append(f"- fix: {_trim_text(rec, 200)}")
    return "\n".join(lines).strip()


def _run_parallel_calls(
    providers: List[str],
    run_one: Callable[[str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if len(providers) <= 1:
        return [run_one(providers[0])] if providers else []

    max_workers = max(1, len(providers))
    ordered: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aoe-phase1") as pool:
        futures = {provider: pool.submit(run_one, provider) for provider in providers}
        for provider, fut in futures.items():
            try:
                ordered[provider] = fut.result()
            except Exception as exc:
                ordered[provider] = {
                    "provider": provider,
                    "plan": None,
                    "critic": {
                        "approved": False,
                        "issues": [f"{provider} execution failed: {_trim_text(exc, 180)}"],
                        "recommendations": [],
                    },
                }
    return [ordered[p] for p in providers if p in ordered]


def _run_provider_with_rate_limit_fallback(
    *,
    provider: str,
    run_provider_execs: Dict[str, Callable[[str, int], str]],
    prompt: str,
    timeout_sec: int,
    phase: str,
    round_no: int,
    rounds: int,
    report_progress: Optional[Callable[..., None]],
    provider_capacity_state: Optional[Dict[str, Any]] = None,
) -> tuple[str, str, bool]:
    proactive_fallback = proactive_fallback_provider(
        provider,
        memory_state=provider_capacity_state or {},
        available_providers=run_provider_execs.keys(),
    )
    if proactive_fallback and callable(run_provider_execs.get(proactive_fallback)):
        if callable(report_progress):
            report_progress(
                phase=phase,
                detail=f"phase1 round {round_no}/{rounds} provider={provider} cooldown fallback={proactive_fallback}",
                attempt=round_no,
                total=rounds,
            )
        return run_provider_execs[proactive_fallback](prompt, timeout_sec), proactive_fallback, True
    try:
        return run_provider_execs[provider](prompt, timeout_sec), provider, False
    except Exception as exc:
        detail = _trim_text(exc, 240)
        fallback = fallback_provider_for(provider)
        if fallback and fallback != provider and callable(run_provider_execs.get(fallback)) and is_rate_limit_error(detail):
            if callable(report_progress):
                report_progress(
                    phase=phase,
                    detail=f"phase1 round {round_no}/{rounds} provider={provider} rate_limited fallback={fallback}",
                    attempt=round_no,
                    total=rounds,
                )
            return run_provider_execs[fallback](prompt, timeout_sec), fallback, True
        raise


def run_phase1_ensemble_planning(
    *,
    args: Any,
    user_prompt: str,
    available_roles: List[str],
    selected_roles: Optional[List[str]] = None,
    role_preset: str = "",
    request_contract: Optional[Dict[str, Any]] = None,
    normalize_task_plan_payload: Callable[..., Dict[str, Any]],
    parse_json_object_from_text: Callable[[str], Optional[Dict[str, Any]]],
    run_provider_execs: Dict[str, Callable[[str, int], str]],
    plan_roles_from_subtasks: Callable[[Optional[Dict[str, Any]]], List[str]],
    report_progress: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    workers = [str(r).strip() for r in (available_roles or []) if str(r).strip()] or ["Codex-Reviewer"]
    providers_csv = str(getattr(args, "plan_phase1_providers", "codex,claude") or "codex,claude")
    preferred = []
    for token in providers_csv.split(","):
        item = str(token or "").strip().lower()
        if item and item not in preferred:
            preferred.append(item)
    if not preferred:
        preferred = ["codex", "claude"]

    providers = [name for name in preferred if callable(run_provider_execs.get(name))]
    if not providers:
        return {
            "plan_data": None,
            "plan_critic": default_plan_critic_payload(),
            "plan_roles": [],
            "plan_replans": [],
            "plan_error": "no planning providers available",
            "plan_gate_blocked": True,
            "plan_gate_reason": "no planning providers available",
            "plan_review_count": 0,
            "plan_issue_codes": ["critic_issue"],
            "plan_issue_history": [],
            "plan_convergence_status": "blocked",
            "plan_stalled_reason": "",
            "plan_last_round": 0,
            "phase1_rounds": 0,
            "phase1_mode": "ensemble",
            "phase1_providers": [],
        }

    rounds = max(3, int(getattr(args, "plan_phase1_rounds", 3) or 3))
    max_subtasks = max(1, int(getattr(args, "plan_max_subtasks", 3) or 3))
    planner_timeout = max(60, min(int(getattr(args, "orch_command_timeout_sec", 240) or 240), 240))
    critic_timeout = max(45, min(int(getattr(args, "orch_command_timeout_sec", 180) or 180), 180))

    best_plan: Optional[Dict[str, Any]] = None
    best_critic: Dict[str, Any] = default_plan_critic_payload()
    best_roles: List[str] = []
    shared_feedback = ""
    plan_replans: List[Dict[str, Any]] = []
    plan_issue_history: List[Dict[str, Any]] = []
    degraded_by: List[str] = []
    retry_after_sec = 60
    provider_capacity_state = load_provider_capacity_state(getattr(args, "team_dir", ""))

    for round_no in range(1, rounds + 1):
        def _run_planner_provider(provider: str) -> Dict[str, Any]:
            executed_provider = provider
            used_fallback = False
            if callable(report_progress):
                report_progress(
                    phase="planner",
                    detail=f"phase1 round {round_no}/{rounds} provider={provider}",
                    attempt=round_no,
                    total=rounds,
                )
            planner_prompt = _planner_prompt(
                user_prompt=user_prompt,
                provider=provider,
                workers=workers,
                max_subtasks=max_subtasks,
                round_no=round_no,
                total_rounds=rounds,
                shared_feedback=shared_feedback,
            )
            try:
                raw_plan, executed_provider, used_fallback = _run_provider_with_rate_limit_fallback(
                    provider=provider,
                    run_provider_execs=run_provider_execs,
                    prompt=planner_prompt,
                    timeout_sec=planner_timeout,
                    phase="planner",
                    round_no=round_no,
                    rounds=rounds,
                    report_progress=report_progress,
                    provider_capacity_state=provider_capacity_state,
                )
                parsed_plan = parse_json_object_from_text(raw_plan)
                plan = normalize_task_plan_payload(
                    parsed_plan,
                    user_prompt=user_prompt,
                    workers=workers,
                    max_subtasks=max_subtasks,
                    meta_overrides={
                        "worker_roles": list(selected_roles or []),
                        "phase1_role_preset": role_preset,
                        "phase2_team_preset": role_preset,
                        "request_contract": request_contract or {},
                    },
                )
            except Exception as exc:
                return {
                    "provider": provider,
                    "executed_provider": executed_provider,
                    "rate_limit_fallback": used_fallback,
                    "plan": None,
                    "critic": {
                        "approved": False,
                        "issues": [f"{provider} planner failed: {_trim_text(exc, 180)}"],
                        "recommendations": [],
                    },
                }

            issues: List[str] = []
            recommendations: List[str] = []
            approvals: List[bool] = []

            def _run_critic_provider(critic_provider: str) -> Dict[str, Any]:
                executed_critic_provider = critic_provider
                used_fallback = False
                if callable(report_progress):
                    report_progress(
                        phase="critic",
                        detail=f"phase1 round {round_no}/{rounds} planner={provider} critic={critic_provider}",
                        attempt=round_no,
                        total=rounds,
                    )
                critic_prompt = _critic_prompt(
                    user_prompt=user_prompt,
                    provider=critic_provider,
                    planner_provider=provider,
                    plan=plan,
                    round_no=round_no,
                    total_rounds=rounds,
                )
                try:
                    raw_critic, executed_critic_provider, used_fallback = _run_provider_with_rate_limit_fallback(
                        provider=critic_provider,
                        run_provider_execs=run_provider_execs,
                        prompt=critic_prompt,
                        timeout_sec=critic_timeout,
                        phase="critic",
                        round_no=round_no,
                        rounds=rounds,
                        report_progress=report_progress,
                        provider_capacity_state=provider_capacity_state,
                    )
                    parsed_critic = parse_json_object_from_text(raw_critic)
                    normalized = apply_plan_critic_approval_mode(
                        parsed_critic,
                        approval_mode=plan_payload_approval_mode(plan),
                        max_items=5,
                    )
                    if used_fallback:
                        normalized = dict(normalized)
                        normalized["executed_provider"] = executed_critic_provider
                        normalized["rate_limit_fallback"] = True
                    return normalized
                except Exception as exc:
                    return {
                        "approved": False,
                        "issues": [f"{critic_provider} critic failed: {_trim_text(exc, 180)}"],
                        "recommendations": [],
                    }

            critic_rows = _run_parallel_calls(providers, _run_critic_provider)
            for critic in critic_rows:
                approvals.append(bool(critic.get("approved", True)) and not bool(critic.get("issues") or []))
                issues.extend([_trim_text(item, 240) for item in (critic.get("issues") or [])])
                recommendations.extend([_trim_text(item, 240) for item in (critic.get("recommendations") or [])])

            aggregate_critic = {
                "approved": bool(approvals) and all(approvals),
                "issues": _dedupe_lines(issues, limit=8),
                "recommendations": _dedupe_lines(recommendations, limit=8),
            }
            return {
                "provider": provider,
                "executed_provider": executed_provider,
                "rate_limit_fallback": used_fallback,
                "plan": plan,
                "critic": aggregate_critic,
            }

        round_candidates = _run_parallel_calls(providers, _run_planner_provider)
        for row in round_candidates:
            if bool(row.get("rate_limit_fallback")):
                origin = str(row.get("provider", "")).strip().lower()
                executed = str(row.get("executed_provider", "")).strip().lower()
                token = f"{origin}_rate_limit->{executed}"
                if origin and executed and token not in degraded_by:
                    degraded_by.append(token)

        viable = [row for row in round_candidates if isinstance(row.get("plan"), dict)]
        if not viable:
            limited: List[str] = []
            for row in round_candidates:
                critic = row.get("critic") if isinstance(row.get("critic"), dict) else {}
                issues = [str(item).strip() for item in (critic.get("issues") or []) if str(item).strip()]
                if issues and all(is_rate_limit_error(item) for item in issues):
                    provider = str(row.get("provider", "")).strip().lower()
                    if provider and provider not in limited:
                        limited.append(provider)
                    retry_after_sec = max(retry_after_sec, max(extract_retry_after_sec(item) for item in issues))
            return {
                "plan_data": None,
                "plan_critic": default_plan_critic_payload(),
                "plan_roles": [],
                "plan_replans": plan_replans,
                "plan_error": f"phase1 round {round_no}: no valid planner output",
                "plan_gate_blocked": True,
                "plan_gate_reason": (
                    f"phase1 providers rate limited: {', '.join(limited)}"
                    if limited
                    else f"phase1 round {round_no}: no valid planner output"
                ),
                "plan_review_count": len(plan_issue_history),
                "plan_issue_codes": _dedupe_issue_codes(plan_issue_history),
                "plan_issue_history": list(plan_issue_history),
                "plan_convergence_status": "blocked",
                "plan_stalled_reason": "",
                "plan_last_round": round_no,
                "phase1_rounds": round_no,
                "phase1_mode": "ensemble",
                "phase1_providers": providers,
                "rate_limit": (
                    build_rate_limit_snapshot(
                        mode="blocked",
                        limited_providers=limited,
                        retry_after_sec=retry_after_sec,
                    )
                    if limited
                    else {}
                ),
            }

        scored = sorted(
            enumerate(viable),
            key=lambda item: _candidate_score(item[1]["critic"], item[1]["plan"]),
        )
        best_idx, best_candidate = scored[0]
        best_plan = best_candidate["plan"]
        best_critic = best_candidate["critic"]
        best_roles = plan_roles_from_subtasks(best_plan)
        plan_issue_history.append(
            _issue_row(
                round_no=round_no,
                provider=str(best_candidate.get("provider", "")).strip(),
                critic=best_critic,
            )
        )
        plan_replans.append(
            {
                "attempt": round_no,
                "critic": "approved" if not bool(best_critic.get("issues") or []) and bool(best_critic.get("approved", True)) else "needs_fix",
                "subtasks": len(best_plan.get("subtasks") or []),
                "providers": providers[:],
                "best_provider": best_candidate["provider"],
                "issues": len(best_critic.get("issues") or []),
            }
        )
        shared_feedback = _render_shared_feedback(viable, best_idx=best_idx)

    plan_gate_blocked = bool(getattr(args, "plan_block_on_critic", True)) and bool(best_critic.get("issues") or [])
    plan_gate_reason = plan_critic_primary_issue(best_critic, limit=240) if plan_gate_blocked else ""
    plan_review_count = len(plan_issue_history)
    plan_issue_codes = _dedupe_issue_codes(plan_issue_history)
    plan_stalled_reason = _detect_stalled_issue(plan_issue_history)
    plan_convergence_status = "ready"
    if plan_gate_blocked:
        plan_convergence_status = "stalled" if plan_stalled_reason else "blocked"
    elif plan_review_count < 3:
        plan_gate_blocked = True
        plan_gate_reason = f"planning convergence requires at least 3 critical reviews (got {plan_review_count})"
        plan_convergence_status = "blocked"
    return {
        "plan_data": best_plan,
        "plan_critic": best_critic,
        "plan_roles": best_roles,
        "plan_replans": plan_replans,
        "plan_error": "",
        "plan_gate_blocked": plan_gate_blocked,
        "plan_gate_reason": plan_gate_reason,
        "plan_review_count": plan_review_count,
        "plan_issue_codes": plan_issue_codes,
        "plan_issue_history": plan_issue_history,
        "plan_convergence_status": plan_convergence_status,
        "plan_stalled_reason": plan_stalled_reason,
        "plan_last_round": rounds,
        "phase1_rounds": rounds,
        "phase1_mode": "ensemble",
        "phase1_providers": providers,
        "rate_limit": (
            build_rate_limit_snapshot(
                mode="degraded",
                limited_providers=[token.split("_rate_limit->", 1)[0] for token in degraded_by],
                degraded_by=degraded_by,
                retry_after_sec=retry_after_sec,
            )
            if degraded_by
            else {}
        ),
    }
