#!/usr/bin/env python3
"""Core contract helpers for Orch task / TF planning / verdict flows."""

from __future__ import annotations

from typing import Any, Dict, List

from aoe_tg_tf_event_schema import normalize_followup_proposals, normalize_runtime_events, tf_runtime_event_schema


TASK_PRIORITIES = ("P0", "P1", "P2", "P3")
TASK_SOURCES = ("manual", "sync", "proposal", "retry", "offdesk", "sandbox")
TASK_APPROVAL_MODES = ("policy", "confirm", "none")
TASK_STATUSES = (
    "queued",
    "planning",
    "rate_limited",
    "running",
    "critic_review",
    "needs_retry",
    "manual_intervention",
    "completed",
    "archived",
    "blocked",
)

PLAN_STATUSES = ("draft", "ready", "blocked")
ROLE_KINDS = ("planner", "worker", "critic", "verifier", "writer", "analyst", "reviewer", "engineer")
VERDICT_STATUSES = ("success", "retry", "fail", "intervention")
VERDICT_ACTIONS = ("none", "retry", "replan", "escalate", "abort")
TEAM_EXECUTION_MODES = ("single", "parallel")
TEAM_REVIEW_MODES = ("skip", "single", "parallel")
TEAM_ROLE_PRESETS = ("general", "review", "writer", "analysis", "build", "data", "mixed")
COMPANION_ROLE_MAP = {
    "Codex-Reviewer": "Claude-Reviewer",
    "Codex-Writer": "Claude-Writer",
    "Codex-Analyst": "Claude-Analyst",
}
PRESET_EXEC_ROLE_ORDER = {
    "review": ["Codex-Reviewer", "Claude-Reviewer"],
    "writer": ["Codex-Writer", "Claude-Writer"],
    "analysis": ["Codex-Analyst", "Claude-Analyst"],
    "build": ["Codex-Dev"],
    "data": ["DataEngineer"],
}
PRESET_EVIDENCE_DEFAULTS = {
    "writer": [
        "Draft or handoff artifact is produced.",
        "Output is readable from the operator perspective.",
    ],
    "analysis": [
        "Findings are summarized with concrete evidence.",
        "Open questions or weak spots are called out explicitly.",
    ],
    "build": [
        "Code change or implementation delta is summarized.",
        "Test or regression evidence is captured.",
    ],
    "data": [
        "Schema/null evidence is captured.",
        "Transformed output sample or validation result is recorded.",
    ],
    "review": [
        "Risks are enumerated clearly.",
        "Regression or verifier findings are captured.",
    ],
    "mixed": [
        "Work artifact is produced for the execution lane.",
        "Review, handoff, or risk evidence is captured.",
    ],
}
PRESET_COMPLETION_CONTRACTS = {
    "writer": {
        "primary_output": "draft/report/handoff/spec text",
        "focus": "clarity, completeness, source grounding, unresolved claims",
        "done_when": "deliverable is readable end-to-end, grounded in sources, and open issues are surfaced",
        "rerun_when": "sections are missing, grounding is weak, or the deliverable is malformed",
        "manual_followup_when": "audience, policy wording, or source-of-truth ambiguity needs operator judgment",
    },
    "analysis": {
        "primary_output": "analytical conclusion or ranked findings",
        "focus": "evidence quality, reasoning coherence, missing caveats",
        "done_when": "conclusion is supported by inspectable evidence and explicit caveats",
        "rerun_when": "conclusion is unsupported, evidence joins are weak, or caveats are missing",
        "manual_followup_when": "operator preference or external interpretation decides the conclusion",
    },
    "build": {
        "primary_output": "code/config/integration change",
        "focus": "implementation delta, tests, integration risk",
        "done_when": "implementation delta is clear and verification evidence covers the risky path",
        "rerun_when": "tests fail, patch is incomplete, or integration edges are broken",
        "manual_followup_when": "env/deploy/secret dependency or risky mutation approval blocks closure",
    },
    "data": {
        "primary_output": "transformed dataset/query/report output",
        "focus": "schema correctness, null handling, transform integrity",
        "done_when": "schema/null evidence and sample output or validation result are present",
        "rerun_when": "schema drifts, output is null-heavy, or a pipeline step is broken",
        "manual_followup_when": "business-rule ambiguity or source data quality requires operator judgment",
    },
    "review": {
        "primary_output": "review verdict, critique, or regression assessment",
        "focus": "risk detection, regression coverage, missing evidence",
        "done_when": "review artifact is complete even if the verdict is no change required",
        "rerun_when": "review is shallow, unsupported, or misses required scope",
        "manual_followup_when": "acceptance threshold or risk tradeoff must be decided by the operator",
    },
    "mixed": {
        "primary_output": "work result plus handoff/review output",
        "focus": "execution/review split integrity, handoff quality, unresolved coupling",
        "done_when": "work artifact and handoff/review evidence both exist without lane drift",
        "rerun_when": "work lane is incomplete, handoff drifts, or review lane mismatches the preset",
        "manual_followup_when": "operator must arbitrate packaging, scope, or competing outputs",
    },
    "general": {
        "primary_output": "fallback mixed work result",
        "focus": "basic completion, evidence minimum, risk surfacing",
        "done_when": "task-specific minimum evidence exists and the result is understandable",
        "rerun_when": "result is partial, inconsistent, or unclear",
        "manual_followup_when": "scope remains unclear or the task framing is insufficient",
    },
}


def _is_review_role(role: str) -> bool:
    token = str(role or "").strip().lower()
    return any(key in token for key in ("review", "critic", "verif", "qa"))


def _trim_text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _normalize_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _normalize_int(raw: Any, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    return max(int(minimum), value)


def _normalize_priority(raw: Any, default: str = "P2") -> str:
    token = str(raw or "").strip().upper()
    if token in TASK_PRIORITIES:
        return token
    return default


def _normalize_choice(raw: Any, allowed: tuple[str, ...], default: str) -> str:
    token = str(raw or "").strip().lower()
    for item in allowed:
        if token == item.lower():
            return item
    return default


def _normalize_role_preset(raw: Any, default: str = "general") -> str:
    return _normalize_choice(raw, TEAM_ROLE_PRESETS, default)


def _default_acceptance_criteria(title: str) -> List[str]:
    return [f"{title} has a user-visible result and a reviewer-readable explanation."]


def _role_work_preset(role: str) -> str:
    token = str(role or "").strip().lower()
    if not token or _is_review_role(role):
        return ""
    if "dataengineer" in token or "data engineer" in token or "data" in token:
        return "data"
    if any(key in token for key in ("writer", "doc", "scribe")):
        return "writer"
    if any(key in token for key in ("analyst", "analysis", "research")):
        return "analysis"
    if any(key in token for key in ("dev", "engineer", "builder", "implement")):
        return "build"
    return ""


def _infer_role_preset_from_roles(roles: List[str]) -> str:
    current_roles = _dedupe_roles(roles, limit=12)
    if current_roles and all(_is_review_role(role) for role in current_roles):
        return "review"

    work_presets = [preset for preset in [_role_work_preset(role) for role in current_roles] if preset]
    unique_presets = _dedupe_roles(work_presets, limit=6)
    if len(unique_presets) >= 2:
        return "mixed"
    if len(unique_presets) == 1:
        return _normalize_role_preset(unique_presets[0])
    if any(_is_review_role(role) for role in current_roles):
        return "review"
    return "general"


def _merge_preset_evidence_defaults(
    raw: Any,
    *,
    preset: str,
    title: str,
    acceptance_criteria: List[str],
) -> List[str]:
    explicit = _normalize_text_list(raw, limit=6, item_limit=240)
    if explicit:
        return explicit

    normalized_preset = _normalize_role_preset(preset)
    acceptance = _normalize_text_list(acceptance_criteria, limit=6, item_limit=240)
    default_acceptance = _default_acceptance_criteria(title)
    if acceptance == default_acceptance:
        acceptance = []

    merged: List[str] = []
    for row in PRESET_EVIDENCE_DEFAULTS.get(normalized_preset, []) + acceptance:
        token = _trim_text(row, 240)
        if token and token not in merged:
            merged.append(token)
    return merged or acceptance or default_acceptance


def preset_completion_contract(raw: Any) -> Dict[str, str]:
    preset = _normalize_role_preset(raw)
    contract = PRESET_COMPLETION_CONTRACTS.get(preset, PRESET_COMPLETION_CONTRACTS["general"])
    return {
        "preset": preset,
        "primary_output": str(contract.get("primary_output", "")).strip() or "-",
        "focus": str(contract.get("focus", "")).strip() or "-",
        "done_when": str(contract.get("done_when", "")).strip() or "-",
        "rerun_when": str(contract.get("rerun_when", "")).strip() or "-",
        "manual_followup_when": str(contract.get("manual_followup_when", "")).strip() or "-",
    }


def normalize_tf_phase(raw: Any, default: str = "queued") -> str:
    return _normalize_choice(raw, TASK_STATUSES, default)


def derive_tf_phase(task: Any) -> str:
    data = task if isinstance(task, dict) else {}
    if _normalize_bool(data.get("archived", False), False) or _trim_text(data.get("archived_at", ""), 64):
        return "archived"

    status = str(data.get("status", "pending")).strip().lower()
    stages = data.get("stages")
    if not isinstance(stages, dict):
        stages = {}

    plan_gate_passed = data.get("plan_gate_passed")
    plan_gate_reason = _trim_text(data.get("plan_gate_reason", ""), 240)
    plan_convergence_status = str(data.get("plan_convergence_status", "")).strip().lower()
    plan_stalled_reason = _trim_text(data.get("plan_stalled_reason", ""), 240)
    exec_critic = data.get("exec_critic") if isinstance(data.get("exec_critic"), dict) else {}
    exec_verdict = _normalize_choice(exec_critic.get("verdict"), VERDICT_STATUSES, "")
    rate_limit = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    rate_limit_mode = str(rate_limit.get("mode", "")).strip().lower()

    if status == "completed":
        return "completed"
    if rate_limit_mode == "blocked":
        return "rate_limited"
    if plan_convergence_status == "stalled":
        return "manual_intervention"
    if plan_gate_passed is False or plan_gate_reason:
        return "blocked"
    if exec_verdict == "retry":
        return "needs_retry"
    if exec_verdict in {"fail", "intervention"}:
        return "manual_intervention"

    planning_stage = str(stages.get("planning", "pending")).strip().lower()
    if planning_stage in {"pending", "running"}:
        intake_stage = str(stages.get("intake", "pending")).strip().lower()
        return "queued" if intake_stage == "pending" and planning_stage == "pending" else "planning"

    verification_stage = str(stages.get("verification", "pending")).strip().lower()
    integration_stage = str(stages.get("integration", "pending")).strip().lower()
    close_stage = str(stages.get("close", "pending")).strip().lower()
    execution_stage = str(stages.get("execution", "pending")).strip().lower()
    staffing_stage = str(stages.get("staffing", "pending")).strip().lower()

    if verification_stage in {"running", "done"} or integration_stage in {"running", "done"}:
        return "critic_review"
    if verification_stage == "failed" or integration_stage == "failed" or close_stage == "failed":
        return "manual_intervention"
    if execution_stage == "failed":
        return "manual_intervention"
    if execution_stage in {"running", "done"} or staffing_stage in {"running", "done"} or status == "running":
        return "running"
    if status == "failed":
        return "manual_intervention"
    return "queued"


def derive_tf_phase_reason(task: Any) -> str:
    data = task if isinstance(task, dict) else {}
    exec_critic = data.get("exec_critic") if isinstance(data.get("exec_critic"), dict) else {}
    exec_reason = _trim_text(exec_critic.get("reason", exec_critic.get("fix", "")), 240)
    plan_gate_reason = _trim_text(data.get("plan_gate_reason", ""), 240)
    plan_convergence_status = str(data.get("plan_convergence_status", "")).strip().lower()
    plan_stalled_reason = _trim_text(data.get("plan_stalled_reason", ""), 240)
    rate_limit = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    if str(rate_limit.get("mode", "")).strip().lower() == "blocked":
        providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
        retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
        retry_at = str(rate_limit.get("retry_at", "")).strip()
        parts = []
        if providers:
            parts.append("providers=" + ",".join(providers))
        if retry_after > 0:
            parts.append(f"retry_after={retry_after}s")
        if retry_at:
            parts.append(f"retry_at={retry_at}")
        return "provider capacity unavailable" + (f" ({' '.join(parts)})" if parts else "")
    if plan_convergence_status == "stalled":
        return plan_stalled_reason or "planning stalled"
    if plan_gate_reason:
        return plan_gate_reason
    if exec_reason:
        return exec_reason

    stages = data.get("stages")
    if isinstance(stages, dict):
        for name in ("verification", "integration", "execution", "close"):
            token = str(stages.get(name, "")).strip().lower()
            if token == "failed":
                return f"{name} failed"
    return ""


def _normalize_text_list(raw: Any, *, limit: int, item_limit: int) -> List[str]:
    rows = raw if isinstance(raw, list) else []
    normalized: List[str] = []
    for row in rows:
        token = _trim_text(row, item_limit)
        if token and token not in normalized:
            normalized.append(token)
    return normalized[: max(1, int(limit or 1))]


def _dedupe_roles(rows: Any, *, limit: int = 16) -> List[str]:
    items = rows if isinstance(rows, list) else []
    normalized: List[str] = []
    seen: set[str] = set()
    for row in items:
        token = _trim_text(row, 64)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized[: max(1, int(limit or 1))]


def orch_task_spec_schema() -> Dict[str, Any]:
    return {
        "required_fields": [
            "task_id",
            "project_key",
            "title",
            "objective",
            "priority",
            "source",
            "readonly",
            "approval_mode",
            "requested_roles",
            "acceptance_criteria",
            "retry_budget",
            "status",
        ],
        "allowed_priority": list(TASK_PRIORITIES),
        "allowed_source": list(TASK_SOURCES),
        "allowed_status": list(TASK_STATUSES),
        "allowed_approval_mode": list(TASK_APPROVAL_MODES),
        "notes": [
            "task spec is the only input Orch should hand to a TF planner",
            "queue ownership stays outside TF; the spec may reference todo or proposal ids",
            "backend selection is advisory here and remains policy-owned",
        ],
    }


def normalize_orch_task_spec(
    raw: Any,
    *,
    task_id: str = "",
    request_id: str = "",
    project_key: str = "",
    project_root: str = "",
    source: str = "manual",
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    title = (
        _trim_text(data.get("title", ""), 160)
        or _trim_text(data.get("summary", ""), 160)
        or _trim_text(data.get("objective", ""), 160)
        or "Untitled task"
    )
    objective = (
        _trim_text(data.get("objective", ""), 400)
        or _trim_text(data.get("goal", ""), 400)
        or title
    )
    source_ref_in = data.get("source_ref")
    source_ref = source_ref_in if isinstance(source_ref_in, dict) else {}
    requested_roles = _normalize_text_list(
        data.get("requested_roles", data.get("roles", data.get("workers", []))),
        limit=8,
        item_limit=64,
    )
    if not requested_roles:
        requested_roles = ["Orchestrator"]
    acceptance = _normalize_text_list(
        data.get("acceptance_criteria", data.get("acceptance", data.get("success_criteria", []))),
        limit=6,
        item_limit=240,
    )
    if not acceptance:
        acceptance = _default_acceptance_criteria(title)
    constraints = _normalize_text_list(data.get("constraints", []), limit=8, item_limit=240)

    retry_in = data.get("retry_budget")
    retry_budget = retry_in if isinstance(retry_in, dict) else {}
    return {
        "task_id": _trim_text(data.get("task_id", "") or task_id, 64) or "TASK-UNKNOWN",
        "request_id": _trim_text(data.get("request_id", "") or request_id, 64),
        "project_key": _trim_text(data.get("project_key", "") or project_key, 64),
        "project_root": _trim_text(data.get("project_root", "") or project_root, 240),
        "title": title,
        "objective": objective,
        "priority": _normalize_priority(data.get("priority"), "P2"),
        "source": _normalize_choice(data.get("source"), TASK_SOURCES, _normalize_choice(source, TASK_SOURCES, "manual")),
        "source_ref": {
            "todo_id": _trim_text(source_ref.get("todo_id", data.get("todo_id", "")), 64),
            "proposal_id": _trim_text(source_ref.get("proposal_id", data.get("proposal_id", "")), 64),
            "chat_id": _trim_text(source_ref.get("chat_id", data.get("chat_id", "")), 64),
            "request_id": _trim_text(source_ref.get("request_id", "") or request_id, 64),
        },
        "readonly": _normalize_bool(data.get("readonly", True), True),
        "approval_mode": _normalize_choice(data.get("approval_mode", data.get("approval")), TASK_APPROVAL_MODES, "policy"),
        "requested_roles": requested_roles,
        "constraints": constraints,
        "acceptance_criteria": acceptance,
        "backend_profile": _trim_text(data.get("backend_profile", "default"), 64) or "default",
        "retry_budget": {
            "max_retries": _normalize_int(retry_budget.get("max_retries", data.get("max_retries", 3)), 3, minimum=0),
            "critic_owned": _normalize_bool(retry_budget.get("critic_owned", True), True),
        },
        "status": _normalize_choice(data.get("status"), TASK_STATUSES, "queued"),
    }


def tf_role_assignment_schema() -> Dict[str, Any]:
    return {
        "required_fields": ["role", "kind", "goal", "deliverable", "acceptance"],
        "allowed_kind": list(ROLE_KINDS),
        "notes": [
            "assignments are role-scoped, not backend-scoped",
            "one TF may map several assignments onto the same provider/backend later",
        ],
    }


def normalize_tf_role_assignment(raw: Any, *, fallback_role: str, index: int) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    role = _trim_text(data.get("role", data.get("owner_role", "")), 64) or fallback_role or f"Worker-{index}"
    title = _trim_text(data.get("title", data.get("goal", "")), 160)
    goal = _trim_text(data.get("goal", ""), 400) or title or f"Execute subtask {index}"
    deliverable = _trim_text(data.get("deliverable", title), 240) or goal[:240]
    acceptance = _normalize_text_list(data.get("acceptance", []), limit=4, item_limit=240)
    if not acceptance:
        acceptance = [f"{role} delivers evidence for: {deliverable}"]
    kind = _normalize_choice(data.get("kind"), ROLE_KINDS, "worker")
    return {
        "role": role,
        "kind": kind,
        "goal": goal,
        "deliverable": deliverable,
        "acceptance": acceptance,
    }


def normalize_tf_role_assignments(rows: Any, *, fallback_roles: List[str] | None = None) -> List[Dict[str, Any]]:
    items = rows if isinstance(rows, list) else []
    defaults = [str(row).strip() for row in (fallback_roles or []) if str(row).strip()] or ["Orchestrator"]
    normalized: List[Dict[str, Any]] = []
    seen_roles: set[str] = set()
    for idx, row in enumerate(items, start=1):
        fallback_role = defaults[min(idx - 1, len(defaults) - 1)]
        item = normalize_tf_role_assignment(row, fallback_role=fallback_role, index=idx)
        if item["role"] in seen_roles:
            continue
        seen_roles.add(item["role"])
        normalized.append(item)
    if normalized:
        return normalized
    return [normalize_tf_role_assignment({}, fallback_role=defaults[0], index=1)]


def tf_plan_schema() -> Dict[str, Any]:
    return {
        "required_fields": [
            "status",
            "summary",
            "strategy",
            "assignments",
            "execution_order",
            "critic",
            "evidence_required",
            "blocking_issues",
        ],
        "allowed_status": list(PLAN_STATUSES),
        "role_assignment_schema": tf_role_assignment_schema(),
        "phase2_team_spec_schema": {
            "required_fields": [
                "execution_mode",
                "execution_groups",
                "review_mode",
                "review_groups",
                "team_roles",
                "critic_role",
                "integration_role",
            ],
            "allowed_execution_mode": list(TEAM_EXECUTION_MODES),
            "allowed_review_mode": list(TEAM_REVIEW_MODES),
        },
        "phase2_execution_plan_schema": {
            "required_fields": [
                "execution_mode",
                "execution_lanes",
                "review_mode",
                "review_lanes",
                "parallel_workers",
                "parallel_reviews",
                "readonly",
            ],
            "allowed_execution_mode": list(TEAM_EXECUTION_MODES),
            "allowed_review_mode": list(TEAM_REVIEW_MODES),
        },
        "notes": [
            "blocked plans must explain why they cannot proceed",
            "ready plans should identify a critic role and the minimum evidence needed",
            "phase2_team_spec is execution-facing and should make parallel lanes explicit",
        ],
    }


def _execution_groups_from_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = plan.get("subtasks")
    if not isinstance(rows, list) or not rows:
        rows = plan.get("assignments")
    if not isinstance(rows, list):
        rows = []
    group_map: Dict[str, Dict[str, Any]] = {}
    group_order: List[str] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        role = _trim_text(row.get("owner_role", row.get("role", "")), 64) or "Worker"
        sid = _trim_text(row.get("id", f"S{idx}"), 32) or f"S{idx}"
        title = (
            _trim_text(row.get("title", ""), 160)
            or _trim_text(row.get("goal", ""), 160)
            or sid
        )
        goal = _trim_text(row.get("goal", title), 240) or title
        if role not in group_map:
            group_order.append(role)
            group_map[role] = {
                "group_id": f"E{len(group_order)}",
                "role": role,
                "subtask_ids": [],
                "subtask_titles": [],
                "goals": [],
            }
        group = group_map[role]
        if sid not in group["subtask_ids"]:
            group["subtask_ids"].append(sid)
        if title not in group["subtask_titles"]:
            group["subtask_titles"].append(title)
        if goal not in group["goals"]:
            group["goals"].append(goal)
    if group_order:
        return [group_map[role] for role in group_order]
    return [
        {
            "group_id": "E1",
            "role": "Worker",
            "subtask_ids": ["S1"],
            "subtask_titles": ["Execute task"],
            "goals": ["Execute the requested task with verifiable evidence."],
        }
    ]


def _plan_subtask_payloads(plan: Dict[str, Any]) -> List[Dict[str, str]]:
    rows = plan.get("subtasks")
    if not isinstance(rows, list) or not rows:
        rows = plan.get("assignments")
    if not isinstance(rows, list):
        rows = []
    payloads: List[Dict[str, str]] = []
    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        sid = _trim_text(row.get("id", f"S{idx}"), 32) or f"S{idx}"
        title = _trim_text(row.get("title", row.get("goal", sid)), 160) or sid
        goal = _trim_text(row.get("goal", title), 240) or title
        payloads.append({"id": sid, "title": title, "goal": goal})
    if payloads:
        return payloads
    return [{"id": "S1", "title": "Execute task", "goal": "Execute the requested task with verifiable evidence."}]


def _row_subtask_ids(rows: List[Dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for item in (row.get("subtask_ids") or []):
            token = str(item or "").strip()
            if token:
                out.add(token)
    return out


def _preset_execution_roles(preset: str, available_roles: List[str]) -> List[str]:
    ordered = PRESET_EXEC_ROLE_ORDER.get(_normalize_role_preset(preset), [])
    available = set(_dedupe_roles(available_roles, limit=16))
    return [role for role in ordered if role in available]


def _apply_execution_preset(
    rows: List[Dict[str, Any]],
    *,
    plan: Dict[str, Any],
    available_roles: List[str],
    preset: str,
) -> List[Dict[str, Any]]:
    normalized_preset = _normalize_role_preset(preset)
    if normalized_preset == "general":
        return rows

    preset_roles = _preset_execution_roles(normalized_preset, available_roles)
    if normalized_preset == "mixed":
        payloads = _plan_subtask_payloads(plan)
        payload_ids = {row["id"] for row in payloads}
        worklike_roles = [role for role in _dedupe_roles(available_roles, limit=16) if not _is_review_role(role)]
        if not worklike_roles:
            return rows

        row_map = {
            str(row.get("role", "")).strip(): dict(row)
            for row in rows
            if isinstance(row, dict) and str(row.get("role", "")).strip()
        }
        mixed_rows = [row_map[role] for role in worklike_roles if role in row_map]
        if mixed_rows and payload_ids.issubset(_row_subtask_ids(mixed_rows)):
            return mixed_rows

        return [
            {
                "group_id": f"E{idx}",
                "role": role,
                "subtask_ids": [row["id"] for row in payloads],
                "subtask_titles": [row["title"] for row in payloads],
                "goals": [row["goal"] for row in payloads],
            }
            for idx, role in enumerate(worklike_roles, start=1)
        ]

    if not preset_roles:
        return rows

    row_map = {
        str(row.get("role", "")).strip(): dict(row)
        for row in rows
        if isinstance(row, dict) and str(row.get("role", "")).strip()
    }
    ordered_matches = [row_map[role] for role in preset_roles if role in row_map]
    payload_ids = {row["id"] for row in _plan_subtask_payloads(plan)}
    if ordered_matches and payload_ids.issubset(_row_subtask_ids(ordered_matches)):
        return ordered_matches

    payloads = _plan_subtask_payloads(plan)
    return [
        {
            "group_id": "E1",
            "role": preset_roles[0],
            "subtask_ids": [row["id"] for row in payloads],
            "subtask_titles": [row["title"] for row in payloads],
            "goals": [row["goal"] for row in payloads],
        }
    ]


def _review_roles(
    *,
    roles: List[str],
    verifier_roles: List[str],
    require_verifier: bool,
) -> List[str]:
    available = set(_dedupe_roles(roles, limit=16))

    def _with_companions(rows: List[str]) -> List[str]:
        expanded: List[str] = []
        for role in _dedupe_roles(rows, limit=8):
            expanded.append(role)
            companion = COMPANION_ROLE_MAP.get(role)
            if companion and companion in available:
                expanded.append(companion)
        return _dedupe_roles(expanded, limit=8)

    preferred = _dedupe_roles(verifier_roles, limit=8)
    if preferred:
        return _with_companions(preferred)
    if require_verifier:
        inferred = []
        for role in _dedupe_roles(roles, limit=12):
            token = role.lower()
            if any(key in token for key in ("review", "critic", "verify", "qa")):
                inferred.append(role)
        if inferred:
            return _with_companions(inferred)
        return _with_companions(["Codex-Reviewer"])
    return []


def _default_phase2_roles_for_preset(
    *,
    preset: str,
    execution_groups: List[Dict[str, Any]],
    review_groups: List[Dict[str, Any]],
    team_roles: List[str],
) -> tuple[str, str]:
    normalized_preset = _normalize_role_preset(preset)
    default_critic = review_groups[0]["role"] if review_groups else (team_roles[-1] if team_roles else "Codex-Reviewer")
    default_integration = review_groups[0]["role"] if review_groups else (execution_groups[-1]["role"] if execution_groups else default_critic)

    if normalized_preset in {"writer", "analysis", "build", "data", "mixed"}:
        default_integration = execution_groups[0]["role"] if execution_groups else default_critic
    elif normalized_preset == "review":
        default_integration = default_critic

    return default_critic, default_integration


def _expand_execution_groups_with_companions(
    rows: List[Dict[str, Any]],
    *,
    available_roles: List[str],
) -> List[Dict[str, Any]]:
    available = set(_dedupe_roles(available_roles, limit=16))
    out: List[Dict[str, Any]] = []
    existing_roles = {str(row.get("role", "")).strip() for row in rows if str(row.get("role", "")).strip()}
    for row in rows:
        out.append(row)
        role = str(row.get("role", "")).strip()
        companion = COMPANION_ROLE_MAP.get(role)
        if not companion or companion not in available or companion in existing_roles:
            continue
        companion_row = dict(row)
        companion_row["group_id"] = _trim_text(f"{row.get('group_id', 'E')}C", 16) or "EC"
        companion_row["role"] = companion
        out.append(companion_row)
        existing_roles.add(companion)
    return out


def phase2_team_spec_schema() -> Dict[str, Any]:
    return tf_plan_schema()["phase2_team_spec_schema"]


def phase2_execution_plan_schema() -> Dict[str, Any]:
    return tf_plan_schema()["phase2_execution_plan_schema"]


def normalize_phase2_team_spec(
    raw: Any,
    *,
    plan: Dict[str, Any] | None = None,
    roles: List[str] | None = None,
    verifier_roles: List[str] | None = None,
    require_verifier: bool = False,
) -> Dict[str, Any]:
    plan_data = plan if isinstance(plan, dict) else {}
    default_exec_groups = _execution_groups_from_plan(plan_data)
    all_exec_roles = [str(row.get("role", "")).strip() for row in default_exec_groups if str(row.get("role", "")).strip()]
    meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
    phase2_team_preset = _normalize_role_preset(meta.get("phase2_team_preset"))
    available_roles = _dedupe_roles((roles or []) + all_exec_roles, limit=16)
    review_roles = _review_roles(
        roles=available_roles,
        verifier_roles=list(verifier_roles or []),
        require_verifier=require_verifier,
    )
    data = raw if isinstance(raw, dict) else {}
    exec_rows = data.get("execution_groups")
    if not isinstance(exec_rows, list) or not exec_rows:
        exec_rows = default_exec_groups
    exec_rows = _apply_execution_preset(
        list(exec_rows),
        plan=plan_data,
        available_roles=available_roles,
        preset=phase2_team_preset,
    )

    execution_groups: List[Dict[str, Any]] = []
    for idx, row in enumerate(exec_rows, start=1):
        item = row if isinstance(row, dict) else {}
        role = _trim_text(item.get("role", ""), 64) or default_exec_groups[min(idx - 1, len(default_exec_groups) - 1)]["role"]
        subtask_ids = _normalize_text_list(item.get("subtask_ids", []), limit=8, item_limit=32)
        subtask_titles = _normalize_text_list(item.get("subtask_titles", []), limit=8, item_limit=160)
        goals = _normalize_text_list(item.get("goals", []), limit=8, item_limit=240)
        if not subtask_ids:
            fallback = default_exec_groups[min(idx - 1, len(default_exec_groups) - 1)]
            subtask_ids = list(fallback.get("subtask_ids") or [])
            subtask_titles = list(fallback.get("subtask_titles") or [])
            goals = list(fallback.get("goals") or [])
        execution_groups.append(
            {
                "group_id": _trim_text(item.get("group_id", f"E{idx}"), 16) or f"E{idx}",
                "role": role,
                "subtask_ids": subtask_ids,
                "subtask_titles": subtask_titles,
                "goals": goals,
            }
        )
    execution_groups = _expand_execution_groups_with_companions(
        execution_groups,
        available_roles=available_roles,
    )
    execution_mode = "parallel" if len(execution_groups) > 1 else "single"
    execution_group_ids = [
        str(row.get("group_id", "")).strip()
        for row in execution_groups
        if str(row.get("group_id", "")).strip()
    ]
    default_depends = list(execution_group_ids)

    review_rows = data.get("review_groups")
    if not isinstance(review_rows, list) or not review_rows:
        review_rows = [
            {
                "group_id": f"R{idx}",
                "role": role,
                "kind": "verifier" if role in set(review_roles) else "critic",
                "scope": "phase2_outputs",
                "depends_on": default_depends,
            }
            for idx, role in enumerate(review_roles, start=1)
        ]

    review_groups: List[Dict[str, Any]] = []
    valid_depends = set(default_depends)
    for idx, row in enumerate(review_rows, start=1):
        item = row if isinstance(row, dict) else {}
        role = _trim_text(item.get("role", ""), 64)
        if not role:
            continue
        depends_on = _normalize_text_list(item.get("depends_on", default_depends), limit=8, item_limit=16)
        depends_on = [token for token in depends_on if token in valid_depends]
        if not depends_on and default_depends:
            depends_on = list(default_depends)
        review_groups.append(
            {
                "group_id": _trim_text(item.get("group_id", f"R{idx}"), 16) or f"R{idx}",
                "role": role,
                "kind": _normalize_choice(item.get("kind"), ("critic", "verifier"), "verifier" if role in set(review_roles) else "critic"),
                "scope": _trim_text(item.get("scope", "phase2_outputs"), 160) or "phase2_outputs",
                "depends_on": depends_on,
            }
        )
    review_mode = "skip"
    if review_groups:
        review_mode = "parallel" if len(review_groups) > 1 else "single"

    team_roles = _dedupe_roles(
        [row.get("role", "") for row in execution_groups] + [row.get("role", "") for row in review_groups],
        limit=16,
    )
    default_critic_role, default_integration_role = _default_phase2_roles_for_preset(
        preset=phase2_team_preset,
        execution_groups=execution_groups,
        review_groups=review_groups,
        team_roles=team_roles,
    )
    critic_role = _trim_text(data.get("critic_role", ""), 64) or default_critic_role
    integration_role = _trim_text(data.get("integration_role", ""), 64) or default_integration_role

    return {
        "execution_mode": execution_mode,
        "execution_groups": execution_groups,
        "review_mode": review_mode,
        "review_groups": review_groups,
        "team_roles": team_roles,
        "critic_role": critic_role,
        "integration_role": integration_role,
    }


def normalize_phase2_execution_plan(
    raw: Any,
    *,
    team_spec: Dict[str, Any] | None = None,
    readonly: bool = True,
) -> Dict[str, Any]:
    spec = team_spec if isinstance(team_spec, dict) else {}
    data = raw if isinstance(raw, dict) else {}

    execution_groups = spec.get("execution_groups") if isinstance(spec.get("execution_groups"), list) else []
    review_groups = spec.get("review_groups") if isinstance(spec.get("review_groups"), list) else []
    spec_subtask_ids = _row_subtask_ids(execution_groups)

    exec_rows = data.get("execution_lanes")
    if (
        not isinstance(exec_rows, list)
        or not exec_rows
        or (spec_subtask_ids and not spec_subtask_ids.issubset(_row_subtask_ids(exec_rows)))
    ):
        exec_rows = execution_groups
    exec_parallel_default = len(exec_rows) > 1
    execution_lanes: List[Dict[str, Any]] = []
    for idx, row in enumerate(exec_rows, start=1):
        item = row if isinstance(row, dict) else {}
        subtask_ids = _normalize_text_list(item.get("subtask_ids", []), limit=8, item_limit=32)
        lane_parallel_default = exec_parallel_default if len(subtask_ids) <= 1 else False
        execution_lanes.append(
            {
                "lane_id": _trim_text(item.get("lane_id", item.get("group_id", f"L{idx}")), 16) or f"L{idx}",
                "role": _trim_text(item.get("role", "Worker"), 64) or "Worker",
                "subtask_ids": subtask_ids,
                "parallel": False if len(subtask_ids) > 1 else _normalize_bool(item.get("parallel", lane_parallel_default), lane_parallel_default),
            }
        )
    if not execution_lanes:
        execution_lanes = [{"lane_id": "L1", "role": "Worker", "subtask_ids": ["S1"], "parallel": False}]
    if len(execution_lanes) <= 1:
        for row in execution_lanes:
            row["parallel"] = False
    execution_lane_ids = [
        str(row.get("lane_id", "")).strip()
        for row in execution_lanes
        if str(row.get("lane_id", "")).strip()
    ]
    valid_depends = set(execution_lane_ids)

    review_rows = data.get("review_lanes")
    if not isinstance(review_rows, list) or not review_rows:
        review_rows = review_groups
    review_lanes: List[Dict[str, Any]] = []
    review_parallel_default = len(review_rows) > 1 if isinstance(review_rows, list) else len(review_groups) > 1
    for idx, row in enumerate(review_rows, start=1):
        item = row if isinstance(row, dict) else {}
        role = _trim_text(item.get("role", ""), 64)
        if not role:
            continue
        depends_on = _normalize_text_list(item.get("depends_on", execution_lane_ids), limit=8, item_limit=16)
        depends_on = [token for token in depends_on if token in valid_depends]
        if not depends_on and execution_lane_ids:
            depends_on = list(execution_lane_ids)
        review_lanes.append(
            {
                "lane_id": _trim_text(item.get("lane_id", item.get("group_id", f"R{idx}")), 16) or f"R{idx}",
                "role": role,
                "kind": _normalize_choice(item.get("kind"), ("critic", "verifier"), "verifier"),
                "depends_on": depends_on,
                "parallel": _normalize_bool(item.get("parallel", review_parallel_default), review_parallel_default),
            }
        )
    if len(review_lanes) <= 1:
        for row in review_lanes:
            row["parallel"] = False

    execution_mode = _normalize_choice(data.get("execution_mode"), TEAM_EXECUTION_MODES, str(spec.get("execution_mode", "single") or "single"))
    review_mode = _normalize_choice(data.get("review_mode"), TEAM_REVIEW_MODES, str(spec.get("review_mode", "skip") or "skip"))
    if len(execution_lanes) <= 1:
        execution_mode = "single"
    if not review_lanes:
        review_mode = "skip"
    elif len(review_lanes) == 1:
        review_mode = "single"
    else:
        review_mode = "parallel"

    return {
        "execution_mode": execution_mode,
        "execution_lanes": execution_lanes,
        "review_mode": review_mode,
        "review_lanes": review_lanes,
        "parallel_workers": len(execution_lanes) > 1,
        "parallel_reviews": len(review_lanes) > 1,
        "readonly": _normalize_bool(data.get("readonly", readonly), readonly),
    }


def attach_phase2_team_spec(
    plan: Any,
    *,
    roles: List[str] | None = None,
    verifier_roles: List[str] | None = None,
    require_verifier: bool = False,
    readonly: bool | None = None,
) -> Dict[str, Any]:
    data = dict(plan or {}) if isinstance(plan, dict) else {}
    meta_in = data.get("meta")
    meta = dict(meta_in or {}) if isinstance(meta_in, dict) else {}
    resolved_readonly = _normalize_bool(
        meta.get("readonly", False) if readonly is None else readonly,
        False,
    )
    meta["readonly"] = resolved_readonly
    team_spec = normalize_phase2_team_spec(
        meta.get("phase2_team_spec"),
        plan=data,
        roles=list(roles or []),
        verifier_roles=list(verifier_roles or []),
        require_verifier=require_verifier,
    )
    meta["phase2_team_spec"] = team_spec
    meta["phase2_execution_plan"] = normalize_phase2_execution_plan(
        meta.get("phase2_execution_plan"),
        team_spec=team_spec,
        readonly=resolved_readonly,
    )
    data["meta"] = meta
    return data


def normalize_tf_plan(
    raw: Any,
    *,
    task_spec: Dict[str, Any] | None = None,
    max_assignments: int = 6,
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    spec = normalize_orch_task_spec(task_spec or {})
    meta_in = data.get("meta")
    meta = dict(meta_in or {}) if isinstance(meta_in, dict) else {}
    requested_roles = list(spec.get("requested_roles") or [])
    inferred_role_preset = _infer_role_preset_from_roles(requested_roles)
    phase1_role_preset = _normalize_role_preset(meta.get("phase1_role_preset") or inferred_role_preset)
    phase2_team_preset = _normalize_role_preset(meta.get("phase2_team_preset") or phase1_role_preset)
    assignment_rows = data.get("assignments", data.get("subtasks", []))
    assignments = normalize_tf_role_assignments(
        assignment_rows,
        fallback_roles=requested_roles,
    )[: max(1, int(max_assignments or 1))]
    execution_order = _normalize_text_list(data.get("execution_order", []), limit=len(assignments), item_limit=64)
    if not execution_order:
        execution_order = [row["role"] for row in assignments]

    critic_in = data.get("critic")
    critic_data = critic_in if isinstance(critic_in, dict) else {}
    review_roles = [role for role in requested_roles if _is_review_role(role)]
    critic_role = (
        _trim_text(critic_data.get("role", ""), 64)
        or (review_roles[0] if review_roles else ("Codex-Reviewer" if "Codex-Reviewer" in execution_order else execution_order[-1]))
    )
    status = _normalize_choice(data.get("status"), PLAN_STATUSES, "ready")
    blocking_issues = _normalize_text_list(
        data.get("blocking_issues", data.get("issues", [])),
        limit=6,
        item_limit=240,
    )
    if status == "blocked" and not blocking_issues:
        blocking_issues = ["planner did not provide an executable route"]
    normalized = {
        "status": status,
        "summary": _trim_text(data.get("summary", spec.get("title", "")), 240) or spec["title"],
        "strategy": _trim_text(data.get("strategy", spec.get("objective", "")), 600) or spec["objective"],
        "assignments": assignments,
        "execution_order": execution_order,
        "critic": {
            "required": _normalize_bool(critic_data.get("required", True), True),
            "role": critic_role,
            "exit_on_fail": _normalize_bool(critic_data.get("exit_on_fail", False), False),
        },
        "evidence_required": _merge_preset_evidence_defaults(
            data.get("evidence_required"),
            preset=phase2_team_preset,
            title=str(spec.get("title", "")),
            acceptance_criteria=list(spec.get("acceptance_criteria") or []),
        ),
        "blocking_issues": blocking_issues,
        "meta": {
            **meta,
            "phase1_role_preset": phase1_role_preset,
            "phase2_team_preset": phase2_team_preset,
        },
    }
    return attach_phase2_team_spec(
        normalized,
        roles=requested_roles,
        verifier_roles=review_roles if normalized["critic"]["required"] else [],
        require_verifier=bool(normalized["critic"]["required"]),
        readonly=bool(spec.get("readonly", True)),
    )


def tf_verdict_schema() -> Dict[str, Any]:
    return {
        "required_fields": [
            "status",
            "action",
            "summary",
            "reason",
            "attempt",
            "max_attempts",
            "manual_followup",
            "retry_hint",
            "evidence",
            "artifacts",
        ],
        "allowed_status": list(VERDICT_STATUSES),
        "allowed_action": list(VERDICT_ACTIONS),
        "notes": [
            "verdict is TF-owned output, not queue mutation",
            "follow-up work should be emitted as proposals, not direct backlog mutation",
        ],
    }


def normalize_tf_verdict(
    raw: Any,
    *,
    request_id: str = "",
    tf_id: str = "",
    attempt: int = 1,
    max_attempts: int = 3,
) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    status_map = {
        "ok": "success",
        "pass": "success",
        "success": "success",
        "retry": "retry",
        "replan": "retry",
        "fail": "fail",
        "failed": "fail",
        "error": "fail",
        "intervention": "intervention",
        "escalate": "intervention",
    }
    action_map = {
        "none": "none",
        "retry": "retry",
        "replan": "replan",
        "escalate": "escalate",
        "abort": "abort",
    }
    status = status_map.get(str(data.get("status", data.get("verdict", ""))).strip().lower(), "fail")
    action = action_map.get(str(data.get("action", "")).strip().lower(), "")
    if status == "success":
        action = "none"
    elif status == "retry":
        action = action if action in {"retry", "replan"} else "retry"
    elif status == "intervention":
        action = "escalate"
    else:
        action = action if action in {"abort", "escalate"} else "escalate"
        status = "fail"

    artifacts_in = data.get("artifacts")
    artifacts_rows = artifacts_in if isinstance(artifacts_in, list) else []
    artifacts: List[Dict[str, Any]] = []
    for row in artifacts_rows[:8]:
        if not isinstance(row, dict):
            continue
        path = _trim_text(row.get("path", ""), 240)
        kind = _trim_text(row.get("kind", "note"), 64) or "note"
        summary = _trim_text(row.get("summary", path or kind), 240) or kind
        artifacts.append({"path": path, "kind": kind, "summary": summary})

    evidence = _normalize_text_list(data.get("evidence", []), limit=8, item_limit=240)
    summary = _trim_text(data.get("summary", data.get("reason", "")), 240) or status
    reason = _trim_text(data.get("reason", ""), 400) or summary
    manual_followup = _normalize_bool(data.get("manual_followup", status == "intervention"), status == "intervention")
    return {
        "status": status,
        "action": action,
        "summary": summary,
        "reason": reason,
        "request_id": _trim_text(request_id, 64),
        "tf_id": _trim_text(tf_id, 64),
        "attempt": _normalize_int(data.get("attempt", attempt), attempt, minimum=1),
        "max_attempts": _normalize_int(data.get("max_attempts", max_attempts), max_attempts, minimum=1),
        "manual_followup": manual_followup,
        "retry_hint": _trim_text(data.get("retry_hint", data.get("fix", "")), 400),
        "evidence": evidence,
        "artifacts": artifacts,
    }


def orch_followup_proposal_schema() -> Dict[str, Any]:
    return {
        "required_fields": [
            "summary",
            "priority",
            "kind",
            "reason",
            "source_request_id",
            "source_todo_id",
            "confidence",
            "source_tf_id",
            "owner_role",
            "acceptance",
        ],
        "notes": [
            "proposal inbox is the only safe way for TF to suggest new backlog work",
            "proposal acceptance remains in repo-owned todo state modules",
        ],
    }


def normalize_orch_followup_proposals(
    rows: Any,
    *,
    source_request_id: str,
    source_todo_id: str = "",
    source_tf_id: str = "",
    owner_role: str = "",
) -> List[Dict[str, Any]]:
    base_rows = rows if isinstance(rows, list) else []
    base = normalize_followup_proposals(
        base_rows,
        default_source_request_id=source_request_id,
        default_source_todo_id=source_todo_id,
    )
    normalized: List[Dict[str, Any]] = []
    for row in base:
        acceptance = _normalize_text_list(row.get("acceptance", []), limit=4, item_limit=240)
        if not acceptance:
            acceptance = [f"Proposal can be accepted into backlog without re-reading the full TF transcript: {row['summary']}"]
        normalized.append(
            {
                **row,
                "source_tf_id": _trim_text(source_tf_id or row.get("source_tf_id", ""), 64),
                "owner_role": _trim_text(owner_role or row.get("owner_role", ""), 64),
                "acceptance": acceptance,
            }
        )
    return normalized


def orch_runtime_event_schema() -> Dict[str, Any]:
    schema = dict(tf_runtime_event_schema())
    notes = list(schema.get("notes") or [])
    notes.append("runtime event contract is shared by local and experimental Task Team backends")
    schema["notes"] = notes
    schema["contract"] = "orch.runtime_event.v1"
    return schema


def normalize_orch_runtime_events(
    rows: List[Dict[str, Any]],
    *,
    backend: str,
    source: str,
    now_iso,
) -> List[Dict[str, Any]]:
    return normalize_runtime_events(
        rows,
        default_backend=backend,
        default_source=source,
        now_iso=now_iso,
    )
