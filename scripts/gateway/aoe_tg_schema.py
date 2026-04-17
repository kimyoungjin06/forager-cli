#!/usr/bin/env python3
"""Schema coercion helpers for planner / critic payloads."""

from __future__ import annotations

from typing import Any, Dict, List

from aoe_tg_orch_contract import normalize_phase2_execution_plan, normalize_phase2_team_spec
from aoe_tg_orch_roles import classify_dispatch_role_preset, normalize_role_preset
from aoe_tg_request_contract import normalize_request_contract_snapshot
from aoe_tg_request_contract_data import data_request_contract_acceptance_floor


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


def _normalize_text_list(rows: Any, *, limit: int = 8, item_limit: int = 120) -> List[str]:
    if not isinstance(rows, list):
        return []
    out: List[str] = []
    for item in rows:
        token = _trim_text(item, item_limit)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit))]


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


def _role_family(role: str) -> str:
    token = str(role or "").strip()
    if not token:
        return ""
    low = token.lower()
    if _is_review_like_role(token):
        return "review"
    if any(marker in low for marker in ("writer", "doc", "scribe")):
        return "writer"
    if any(marker in low for marker in ("analyst", "analysis", "research")):
        return "analysis"
    if "data" in low:
        return "data"
    if _is_build_like_role(token):
        return "build"
    return "other"


def _coerce_owner_role_for_preset(role: str, *, preset: str, worker_roles: List[str]) -> str:
    normalized_preset = normalize_role_preset(preset)
    if normalized_preset in {"general", "mixed"}:
        if role in worker_roles:
            return role
        family = _role_family(role)
        same_family = [item for item in worker_roles if _role_family(item) == family]
        if same_family:
            return same_family[0]
        if family == "analysis":
            buildlike = [item for item in worker_roles if _role_family(item) in {"build", "data"}]
            if buildlike:
                return buildlike[0]
        return worker_roles[0] if worker_roles else role

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


def _mixed_review_output_tokens(request_contract: Dict[str, Any] | None) -> List[str]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "mixed":
        return []
    fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
    deliverable_policy = fields.get("deliverable_policy") if isinstance(fields.get("deliverable_policy"), dict) else {}
    review_outputs = _normalize_text_list(deliverable_policy.get("review_outputs", []), limit=8, item_limit=64)
    artifact_contracts = snapshot.get("artifact_contracts") if isinstance(snapshot.get("artifact_contracts"), dict) else {}
    tokens: List[str] = []
    for output in review_outputs:
        low = output.lower()
        if low and low not in tokens:
            tokens.append(low)
        artifact = artifact_contracts.get(output)
        if isinstance(artifact, dict):
            path = str(artifact.get("path", "")).strip().lower()
            if path and path not in tokens:
                tokens.append(path)
    tokens.extend(["reviewer note", "reviewer_note", "review-lane", "review lane", "리뷰 노트", "검토 노트"])
    out: List[str] = []
    for token in tokens:
        item = str(token or "").strip().lower()
        if item and item not in out:
            out.append(item)
    return out


def _preferred_mixed_review_role(worker_roles: List[str]) -> str:
    reviewers = [item for item in worker_roles if _is_review_like_role(item)]
    if reviewers:
        return reviewers[0]
    return worker_roles[-1] if worker_roles else "Reviewer"


def _repair_mixed_review_owned_subtask(
    *,
    title: str,
    goal: str,
    role: str,
    worker_roles: List[str],
    request_contract: Dict[str, Any] | None,
) -> tuple[str, str, str]:
    tokens = _mixed_review_output_tokens(request_contract)
    if not tokens:
        return title, goal, role
    context = "\n".join((str(title or ""), str(goal or ""))).lower()
    if not any(token in context for token in tokens if token):
        return title, goal, role

    repaired_role = _preferred_mixed_review_role(worker_roles)
    repaired_title = title
    if _contains_any(context, ["reviewer_note", "reviewer note", "review-lane", "review lane", "리뷰 노트", "검토 노트", "작성", "draft", "write"]):
        repaired_title = "Draft reviewer note"
    repaired_goal = (
        "Write docs/reviews/reviewer_note.md from docs/analysis/auth_scope_inventory.md, work_result, and "
        "docs/handoff/operator_handoff.md. Record severity findings, regression risks, test gaps, and uncertainties."
    )
    return repaired_title, repaired_goal, repaired_role


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


def _data_acceptance_floor(
    *,
    user_prompt: str,
    preset: str,
    role: str,
    title: str,
    goal: str,
) -> List[str]:
    normalized_preset = normalize_role_preset(preset)
    if normalized_preset != "data":
        return []
    if not _role_matches_preset(role, "data"):
        return []

    prompt_context = str(user_prompt or "")
    task_context = "\n".join((str(title or ""), str(goal or "")))
    context = "\n".join((prompt_context, task_context))
    data_markers = [
        "csv",
        "schema",
        "null",
        "sample",
        "dataset",
        "table",
        "column",
        "데이터",
        "스키마",
        "결측",
        "샘플",
        "컬럼",
        "정규화",
    ]
    if not _contains_any(context, data_markers):
        return []

    task_has_schema_signal = _contains_any(
        task_context,
        ["schema", "null", "sample", "report", "스키마", "결측", "샘플", "리포트", "요약"],
    )
    task_has_transform_signal = _contains_any(
        task_context,
        ["normalize", "normalized", "transform", "정규화", "변환", "표준화", "month", "월별", "month column", "컬럼"],
    )
    prompt_has_transform_policy = _contains_any(
        prompt_context,
        [
            "yyyy/mm",
            "yyyy-mm",
            "yyyy.mm",
            "zero-pad",
            "zero pad",
            "unparseable",
            "out-of-range",
            "parse 불가",
            "범위를 벗어난",
            "원본 행",
            "원값",
            "anomaly",
            "month",
        ],
    )

    transform_floor = [
        "Transform acceptance binds the source CSV path and target month column explicitly.",
        (
            "Normalization rules enumerate the only accepted month input formats and the exact YYYY-MM zero-pad output rule; "
            "similar variants such as YYYY/M, YYYY-M, and YYYY.M must stay anomalies instead of being normalized."
        ),
        "Failure handling defines how invalid, unparseable, or out-of-range month values are recorded and whether original rows and month values are preserved.",
    ]
    schema_floor = [
        "Schema evidence covers every output column with inferred_type and type_rule, not a partial column list.",
        "Schema or null evidence reports null_count and observed_non_null_count for every output column.",
        "Sample evidence is taken from the transformed output and is sufficient to inspect formatting and null handling.",
    ]

    floor: List[str] = []
    if task_has_schema_signal:
        floor.extend(schema_floor)
        if task_has_transform_signal or prompt_has_transform_policy:
            floor.extend(transform_floor)
    elif task_has_transform_signal or prompt_has_transform_policy:
        floor.extend(transform_floor)
        floor.extend(schema_floor)
    else:
        floor.extend(schema_floor)

    out: List[str] = []
    for item in floor:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token[:240])
    return out[:3]


def _data_request_contract_floor(
    *,
    request_contract: Dict[str, Any] | None,
    title: str,
    goal: str,
) -> List[str]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if not snapshot:
        return []
    return data_request_contract_acceptance_floor(
        request_contract=snapshot,
        title=title,
        goal=goal,
    )


def _review_request_contract_floor(
    *,
    request_contract: Dict[str, Any] | None,
    title: str,
    goal: str,
) -> List[str]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "review":
        return []
    context = "\n".join((str(title or ""), str(goal or ""))).lower()
    floor = [
        "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, and declared review_evidence artifacts remain allowed write targets.",
    ]
    diff_range_policy = snapshot.get("fields", {}).get("diff_range_policy", {})
    if isinstance(diff_range_policy, dict) and diff_range_policy and _contains_any(
        context,
        ["diff", "scope", "range", "entrypoint", "로그인", "범위", "경계"],
    ):
        floor.append(
            "Canonical diff evidence records recent matching candidates, changed files, excluded candidates, and dirty-worktree exclusions as inspectable input for downstream review gating."
        )
    auth_scope_policy = snapshot.get("fields", {}).get("auth_scope_policy", {})
    if isinstance(auth_scope_policy, dict) and auth_scope_policy and _contains_any(
        context,
        ["login", "auth", "session", "token", "entrypoint", "scope", "로그인", "인증", "세션", "토큰", "경계"],
    ):
        floor.append(
            "Auth/session scope evidence enumerates login entrypoints, caller-visible state transitions, persisted session or token-store paths, excluded caller or storage paths with reasons, or proof that one helper is the only reachable boundary; record that evidence inside severity_rationale.md and do not create a separate scope inventory artifact."
        )
    if _contains_any(context, ["severity", "risk", "impact", "심각도", "리스크", "영향"]):
        floor.append(
            "Severity evidence records affected files or paths, user-visible impact, and exact diff or code evidence as inspectable input for downstream severity gating."
        )
    if _contains_any(context, ["test gap", "uncertainty", "coverage", "테스트 공백", "불확실성", "커버리지"]):
        floor.append(
            "Coverage and uncertainty evidence stay separate: missing coverage or unchecked paths are explicit, and unresolved assumptions or excluded paths keep reasons for downstream review gating."
        )
    quality_gate_policy = snapshot.get("fields", {}).get("quality_gate_policy", {})
    if isinstance(quality_gate_policy, dict) and quality_gate_policy:
        floor.append(
            "If canonical diff scope or required review sections stay incomplete, leave explicit missing-evidence markers for downstream rerun gating."
        )
    return floor


def _repair_review_report_owned_subtask(
    *,
    title: str,
    goal: str,
    role: str,
    request_contract: Dict[str, Any] | None,
) -> tuple[str, str, str]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "review":
        return title, goal, role
    if not _is_review_like_role(role):
        return title, goal, role
    context = "\n".join((str(title or ""), str(goal or ""))).lower()
    report_write_markers = [
        "review_report",
        "review report",
        "write review_report",
        "write review report",
        "final review report",
        "최종 보고서",
        "보고서 작성",
        "review_report.md",
        "rerun or done",
        "done or rerun",
        "rerun/done",
        "done/rerun",
    ]
    if not _contains_any(context, report_write_markers):
        return title, goal, role
    return (
        "Review evidence consolidation",
        "Consolidate canonical diff scope, severity rationale, test coverage gaps, and unresolved uncertainties into evidence that downstream review gating can validate.",
        role,
    )


def _repair_repeated_review_evidence_subtasks(
    *,
    subtasks: List[Dict[str, Any]],
    request_contract: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "review":
        return subtasks
    if len(subtasks) < 2:
        return subtasks

    auth_scope_policy = snapshot.get("fields", {}).get("auth_scope_policy", {})
    has_auth_scope = isinstance(auth_scope_policy, dict) and bool(auth_scope_policy)
    quality_gate_policy = snapshot.get("fields", {}).get("quality_gate_policy", {})
    has_quality_gate = isinstance(quality_gate_policy, dict) and bool(quality_gate_policy)
    stage_specs: List[tuple[str, str, List[str]]] = [
        (
            "Canonical diff 후보와 변경 파일 근거 수집",
            "Enumerate recent matching commits, collect changed-file evidence, excluded candidates, and dirty-worktree exclusions for downstream review gating.",
                [
                    "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                    "Canonical diff evidence records recent matching candidates, changed files, excluded candidates, and dirty-worktree exclusions as inspectable input for downstream review gating.",
                    "Canonical diff evidence remains inspectable input for downstream review gating.",
                ],
        ),
        (
            "Auth/session 경계와 severity 근거 수집",
            "Trace login or auth failure boundaries from the canonical diff evidence, identify entrypoints, caller-visible state transitions, persisted session or token stores, and record that boundary plus impact evidence inside severity_rationale.md for downstream severity gating.",
            [
                "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                "Auth/session scope evidence enumerates login entrypoints, caller-visible state transitions, persisted session or token-store paths, excluded caller or storage paths with reasons, or proof that one helper is the only reachable boundary; record that evidence inside severity_rationale.md and do not create a separate scope inventory artifact.",
                "Severity evidence records affected files or paths, user-visible impact, and exact diff or code evidence as inspectable input for downstream severity gating.",
            ],
        ),
        (
            "테스트 근거와 잔여 불확실성 수집",
            "Collect missing coverage, unchecked paths, unresolved assumptions, and excluded-path reasons for downstream rerun gating.",
            [
                "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                "Coverage and uncertainty evidence stay separate: missing coverage or unchecked paths are explicit, and unresolved assumptions or excluded paths keep reasons for downstream review gating.",
                "If canonical diff scope or required review sections stay incomplete, leave explicit missing-evidence markers for downstream rerun gating.",
            ],
        ),
    ]
    if not has_auth_scope:
        stage_specs[1] = (
            "Severity 근거 수집",
            "Collect concrete changed-file evidence, affected paths, and user-visible impact notes for downstream severity gating.",
            [
                "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                "Severity evidence records affected files or paths, user-visible impact, and exact diff or code evidence as inspectable input for downstream severity gating.",
                "Severity evidence stays tied to concrete changed files and inspectable by the review lane.",
            ],
        )
    else:
        stage_specs = [
            (
                "Auth/session 경계와 candidate scope 근거 수집",
                "Trace login or auth failure boundaries first, identify entrypoints, caller-visible state transitions, persisted session or token stores, excluded caller or storage paths with reasons, and capture the path inventory that later canonical diff selection must cover.",
                [
                    "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                    "Auth/session scope evidence enumerates login entrypoints, caller-visible state transitions, persisted session or token-store paths, excluded caller or storage paths with reasons, or proof that one helper is the only reachable boundary; record that evidence inside severity_rationale.md and do not create a separate scope inventory artifact.",
                    "Scope evidence must exist before canonical diff selection so non-login entrypoints or persisted-store paths are not excluded from the final review range.",
                ],
            ),
            (
                "Canonical diff 범위와 severity 근거 수집",
                "Using the auth/session scope evidence, enumerate recent matching commits, select one canonical diff range, record excluded candidates and dirty-worktree exclusions, and collect changed-file impact evidence for downstream severity gating.",
                [
                    "Review-only flow stays readonly; execution subtasks gather scope, severity, and test-gap evidence without mutating canonical persisted outputs, but declared review_evidence artifacts and review_report.md remain allowed write targets.",
                    "Canonical diff evidence records recent matching candidates, changed files, excluded candidates, and dirty-worktree exclusions as inspectable input for downstream review gating.",
                    "Severity evidence records affected files or paths, user-visible impact, and exact diff or code evidence as inspectable input for downstream severity gating.",
                ],
            ),
            stage_specs[2],
        ]
    if not has_quality_gate:
        stage_specs[2] = (
            stage_specs[2][0],
            stage_specs[2][1],
            stage_specs[2][2][:2] + ["Test gaps and unresolved uncertainties remain explicit evidence for downstream review gating."],
        )

    def _stage_index(row: Dict[str, Any]) -> int | None:
        context = "\n".join((str(row.get("title", "")).strip(), str(row.get("goal", "")).strip())).lower()
        if has_auth_scope:
            if _contains_any(context, ["test gap", "uncertainty", "coverage", "테스트 공백", "불확실성", "커버리지"]):
                return 2
            if _contains_any(
                context,
                ["login", "auth", "session", "token", "entrypoint", "scope", "로그인", "인증", "세션", "토큰", "경계", "persisted", "caller-visible"],
            ):
                return 0
            if _contains_any(context, ["diff", "range", "changed files", "commit", "severity", "risk", "impact", "범위", "변경 파일", "커밋", "심각도", "리스크"]):
                return 1
            return None
        if _contains_any(context, ["test gap", "uncertainty", "coverage", "테스트 공백", "불확실성", "커버리지"]):
            return 2
        if _contains_any(
            context,
            ["login", "auth", "session", "token", "entrypoint", "severity", "risk", "impact", "로그인", "인증", "세션", "토큰", "심각도", "리스크"],
        ):
            return 1
        if _contains_any(context, ["diff", "scope", "range", "changed files", "commit", "범위", "경계", "변경 파일", "커밋"]):
            return 0
        return None

    def _is_consolidation_row(row: Dict[str, Any]) -> bool:
        context = "\n".join((str(row.get("title", "")).strip(), str(row.get("goal", "")).strip())).lower()
        return _contains_any(
            context,
            [
                "review evidence consolidation",
                "review_report",
                "review report",
                "final review report",
                "최종 보고서",
                "보고서 작성",
                "rerun or done",
                "done or rerun",
                "downstream review gating can validate",
            ],
        )

    titles = [str(row.get("title", "")).strip().lower() for row in subtasks if isinstance(row, dict)]
    all_consolidation = bool(titles) and len(set(titles)) == 1 and titles[0] == "review evidence consolidation"

    assigned: Dict[int, Dict[str, Any]] = {}
    unassigned: List[Dict[str, Any]] = []
    changed = False
    for row in subtasks:
        if not isinstance(row, dict):
            continue
        stage_idx = _stage_index(row)
        consolidation = _is_consolidation_row(row)
        if consolidation or stage_idx is None or stage_idx in assigned:
            unassigned.append(dict(row))
            if consolidation or stage_idx in assigned:
                changed = True
            continue
        assigned[stage_idx] = dict(row)

    missing = [idx for idx in range(len(stage_specs)) if idx not in assigned]
    force_canonicalize = has_quality_gate
    if not changed and not missing and not all_consolidation and not force_canonicalize:
        return subtasks

    for row in unassigned:
        if not missing:
            break
        assigned[missing.pop(0)] = row
        changed = True

    repaired: List[Dict[str, Any]] = []
    for stage_idx in range(len(stage_specs)):
        row = assigned.get(stage_idx)
        if not isinstance(row, dict):
            continue
        title, goal, acceptance = stage_specs[stage_idx]
        updated = dict(row)
        updated["title"] = title
        updated["goal"] = goal
        updated["acceptance"] = acceptance[:3]
        repaired.append(updated)

    if not repaired:
        return subtasks

    for idx, row in enumerate(repaired):
        deps: List[str] = []
        if idx == 1 and repaired:
            deps = [str(repaired[0].get("id", "")).strip()]
        elif idx >= 2:
            deps = [
                str(repaired[j].get("id", "")).strip()
                for j in range(idx)
                if str(repaired[j].get("id", "")).strip()
            ]
        if deps:
            row["depends_on"] = deps[:4]
        else:
            row.pop("depends_on", None)

    valid_ids = {str(row.get("id", "")).strip() for row in repaired if isinstance(row, dict)}
    for row in repaired:
        if not isinstance(row, dict):
            continue
        depends = [
            token
            for token in [str(item).strip() for item in (row.get("depends_on") or []) if str(item).strip()]
            if token in valid_ids and token != str(row.get("id", "")).strip()
        ]
        if depends:
            row["depends_on"] = depends[:4]
        else:
            row.pop("depends_on", None)
    return repaired


def _repair_review_plan_summary(
    *,
    summary: str,
    subtasks: List[Dict[str, Any]],
    request_contract: Dict[str, Any] | None,
) -> str:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "review":
        return summary
    fields = snapshot.get("fields", {}) if isinstance(snapshot.get("fields"), dict) else {}
    has_auth_scope = isinstance(fields.get("auth_scope_policy"), dict) and bool(fields.get("auth_scope_policy"))
    has_quality_gate = isinstance(fields.get("quality_gate_policy"), dict) and bool(fields.get("quality_gate_policy"))
    if has_auth_scope:
        parts = [
            "review",
            "auth/session scope -> canonical diff+severity -> test gaps+uncertainties",
        ]
    else:
        parts = [
            "review",
            "canonical diff -> severity -> test gaps+uncertainties",
        ]
    if has_quality_gate:
        parts.append("review lane validates review_report and rerun gate")
    else:
        parts.append("review lane validates review_report")
    repaired = " | ".join(parts)
    return repaired[:240]


def _mixed_request_contract_floor(
    *,
    request_contract: Dict[str, Any] | None,
    role: str,
    title: str,
    goal: str,
) -> List[str]:
    snapshot = normalize_request_contract_snapshot(request_contract or {})
    if normalize_role_preset(snapshot.get("preset", "")) != "mixed":
        return []
    context = "\n".join((str(title or ""), str(goal or ""))).lower()
    artifact_contracts = snapshot.get("artifact_contracts") if isinstance(snapshot.get("artifact_contracts"), dict) else {}
    fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
    auth_failure_policy = fields.get("auth_failure_policy") if isinstance(fields.get("auth_failure_policy"), dict) else {}
    scope_contract = artifact_contracts.get("scope_inventory") if isinstance(artifact_contracts.get("scope_inventory"), dict) else {}
    scope_path = _trim_text(scope_contract.get("path", "docs/analysis/auth_scope_inventory.md"), 200) or "docs/analysis/auth_scope_inventory.md"
    floor: List[str] = []
    strong_implementation_context = _contains_any(
        context,
        ["fix", "patch", "implement", "implementation", "code", "test", "regression", "구현", "패치", "테스트", "회귀"],
    )
    scope_context = _contains_any(
        context,
        ["trace", "scope", "boundary", "entrypoint", "inventory", "경계", "추적", "범위", "inventory"],
    )
    handoff_context = "handoff_doc" in artifact_contracts and _contains_any(
        context,
        ["handoff", "operator", "docs", "document", "문서", "인수인계", "checklist", "체크리스트", "운영자 handoff", "운영 인계"],
    )
    implementation_context = strong_implementation_context or (_contains_any(context, ["수정"]) and not scope_context)
    if "work_result" in artifact_contracts and implementation_context and not handoff_context:
        floor.append(
            "Execution-owned work_result records the implementation delta, changed auth or session paths, and concrete regression test evidence that downstream handoff and review lanes will cite."
        )
        target_codes = _normalize_text_list(auth_failure_policy.get("target_failure_codes", []), limit=6, item_limit=48)
        if auth_failure_policy:
            floor.append(
                f"Implementation cites {scope_path} as the canonical auth/session scope inventory and covers every listed public failure entrypoint, caller-visible auth state surface, and persisted token/session store path."
            )
        if target_codes:
            floor.append(
                f"For every inventory entry, implementation and regression evidence prove invalidation is limited to {', '.join(target_codes)} and that non-target auth failures preserve the listed caller-visible auth state and persisted store path."
            )
        floor.append(
            f"Implementation subtasks explicitly show which {scope_path} entries reset caller-visible auth state, which listed persisted token or session store paths are cleared, and which regression tests prove each inventory entry."
        )
    if not _is_review_like_role(role) and scope_context and _contains_any(
        context,
        ["login", "auth", "session", "token", "entrypoint", "scope", "boundary", "trace", "로그인", "인증", "세션", "토큰", "경계", "추적"],
    ):
        floor.append(
            f"Auth/session scope evidence writes {scope_path} before implementation starts."
        )
        floor.append(
            f"{scope_path} records public failure entrypoints, caller-visible auth state surfaces, persisted token/session store paths, and excluded paths with reasons."
        )
        target_codes = _normalize_text_list(auth_failure_policy.get("target_failure_codes", []), limit=6, item_limit=48)
        if target_codes:
            floor.append(
                f"{scope_path} proves whether one helper is the only boundary; {', '.join(target_codes)} is in-scope, and non-target auth failures preserve existing auth state."
            )
        else:
            floor.append(
                f"{scope_path} proves whether one helper is the only boundary and records that no alternate public entrypoint or persisted store path remains in scope."
            )
    if handoff_context and not _is_review_like_role(role):
        floor.append(
            "Operator handoff updates docs/handoff/operator_handoff.md with change summary, validation status, and concrete operator follow-up or rollback notes."
        )
    if "reviewer_note" in artifact_contracts and _is_review_like_role(role):
        floor.append(
            "Review-owned reviewer_note updates docs/reviews/reviewer_note.md from docs/analysis/auth_scope_inventory.md, work_result, and docs/handoff/operator_handoff.md with severity findings, regression risks, test gaps, and uncertainties."
        )
    if "reviewer_note" in artifact_contracts and not handoff_context and _contains_any(
        context,
        ["review", "reviewer", "risk", "note", "리뷰", "검토", "리스크"],
    ):
        floor.append(
            "Execution subtasks prepare implementation, test, or handoff evidence for the review lane; the review lane, not execution, writes reviewer_note.md from that evidence."
        )
    if "reviewer_note" in artifact_contracts and "handoff_doc" in artifact_contracts and not implementation_context:
        floor.append(
            "Reviewer note is review-owned output, while operator handoff remains execution or writer-owned evidence; do not collapse them into one artifact."
        )
    return floor


def _merge_acceptance_floor(acceptance: List[str], floor: List[str]) -> List[str]:
    base: List[str] = []
    for item in list(acceptance or []):
        token = str(item or "").strip()
        if token and token not in base:
            base.append(token[:240])

    floor_rows: List[str] = []
    for item in list(floor or []):
        token = str(item or "").strip()
        if token and token not in floor_rows:
            floor_rows.append(token[:240])

    if not floor_rows:
        return base[:3]

    keep_slots = max(0, 3 - len(floor_rows))
    out: List[str] = []
    for item in base[:keep_slots]:
        if item not in out:
            out.append(item)
    for item in floor_rows:
        if item not in out:
            out.append(item)
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

    request_contract = normalize_request_contract_snapshot(meta_in.get("request_contract"))
    contract_preset = normalize_role_preset(request_contract.get("preset", "")) if request_contract else ""

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
        contract_preset
        or meta_in.get("phase1_role_preset")
        or classify_dispatch_role_preset(user_prompt, selected_roles=worker_roles)
    )
    phase2_team_preset = normalize_role_preset(meta_in.get("phase2_team_preset") or phase1_role_preset)
    approval_mode = _normalize_approval_mode(meta_in.get("approval_mode", "policy"))
    contract_readonly = _normalize_bool(request_contract.get("readonly", False), False) if request_contract else False
    readonly = _normalize_bool(meta_in.get("readonly", contract_readonly), contract_readonly)

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
        if phase2_team_preset == "review":
            title, goal, role = _repair_review_report_owned_subtask(
                title=title,
                goal=goal,
                role=role,
                request_contract=request_contract,
            )
        if phase2_team_preset == "mixed":
            title, goal, role = _repair_mixed_review_owned_subtask(
                title=title,
                goal=goal,
                role=role,
                worker_roles=worker_roles,
                request_contract=request_contract,
            )

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
        acceptance = _merge_acceptance_floor(
            acceptance,
            _data_acceptance_floor(
                user_prompt=user_prompt,
                preset=phase2_team_preset,
                role=role,
                title=title,
                goal=goal,
            ),
        )
        acceptance = _merge_acceptance_floor(
            acceptance,
            _data_request_contract_floor(
                request_contract=request_contract,
                title=title,
                goal=goal,
            ),
        )
        acceptance = _merge_acceptance_floor(
            acceptance,
            _review_request_contract_floor(
                request_contract=request_contract,
                title=title,
                goal=goal,
            ),
        )
        acceptance = _merge_acceptance_floor(
            acceptance,
            _mixed_request_contract_floor(
                request_contract=request_contract,
                role=role,
                title=title,
                goal=goal,
            ),
        )

        depends_on = [
            str(item).strip()[:32]
            for item in (row.get("depends_on") or [])
            if str(item).strip()
        ]
        if depends_on:
            depends_on = [token for token in depends_on if token != sid[:32]]

        item = {
            "id": sid[:32],
            "title": title[:160],
            "goal": goal[:400],
            "owner_role": role[:64],
            "acceptance": acceptance[:3],
        }
        if depends_on:
            item["depends_on"] = depends_on[:4]
        normalized.append(item)

    if phase2_team_preset == "review":
        normalized = _repair_repeated_review_evidence_subtasks(
            subtasks=normalized,
            request_contract=request_contract,
        )
        summary = _repair_review_plan_summary(
            summary=summary,
            subtasks=normalized,
            request_contract=request_contract,
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
    if request_contract:
        plan_payload["meta"]["request_contract"] = request_contract

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
