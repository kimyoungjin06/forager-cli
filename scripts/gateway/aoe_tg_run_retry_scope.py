#!/usr/bin/env python3
"""Retry-scoped phase2 plan filtering helpers."""

import copy
from typing import Any, Dict, List, Optional

from aoe_tg_orch_contract import normalize_phase2_execution_plan, normalize_phase2_team_spec


def _dedupe_role_tokens(rows: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for row in rows:
        token = str(row or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized


def _lane_id_token(row: Dict[str, Any]) -> str:
    return str(row.get("lane_id", row.get("group_id", "")) or "").strip()[:32]


def _should_filter_retry_phase2_plan(
    *,
    run_control_mode: str,
    run_source_task: Optional[Dict[str, Any]],
    retry_critic: Optional[Dict[str, Any]] = None,
    selected_execution_lane_ids: Optional[List[str]] = None,
    selected_review_lane_ids: Optional[List[str]] = None,
) -> bool:
    if selected_execution_lane_ids or selected_review_lane_ids:
        return bool(run_control_mode in {"retry", "replan", "followup"} and isinstance(run_source_task, dict))
    if run_control_mode == "followup" and isinstance(run_source_task, dict):
        critic = retry_critic if isinstance(retry_critic, dict) else run_source_task.get("exec_critic")
        if not isinstance(critic, dict):
            return False
        return bool(
            critic.get("manual_followup_execution_lane_ids")
            or critic.get("manual_followup_review_lane_ids")
        )
    if run_control_mode != "retry" or not isinstance(run_source_task, dict):
        return bool(
            run_control_mode == "retry"
            and isinstance(retry_critic, dict)
            and str(retry_critic.get("verdict", "")).strip().lower() == "retry"
            and str(retry_critic.get("action", "")).strip().lower() != "replan"
        )
    critic = retry_critic if isinstance(retry_critic, dict) else run_source_task.get("exec_critic")
    if not isinstance(critic, dict):
        return False
    verdict = str(critic.get("verdict", "")).strip().lower()
    action = str(critic.get("action", "")).strip().lower()
    return verdict == "retry" and action != "replan"


def filter_phase2_retry_scope(
    *,
    plan_data: Optional[Dict[str, Any]],
    run_control_mode: str,
    run_source_task: Optional[Dict[str, Any]],
    retry_critic: Optional[Dict[str, Any]] = None,
    selected_execution_lane_ids: Optional[List[str]] = None,
    selected_review_lane_ids: Optional[List[str]] = None,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    if not _should_filter_retry_phase2_plan(
        run_control_mode=run_control_mode,
        run_source_task=run_source_task,
        retry_critic=retry_critic,
        selected_execution_lane_ids=selected_execution_lane_ids,
        selected_review_lane_ids=selected_review_lane_ids,
    ):
        return plan_data, {}
    if not isinstance(plan_data, dict):
        return plan_data, {}

    critic = retry_critic if isinstance(retry_critic, dict) else (
        run_source_task.get("exec_critic") if isinstance(run_source_task, dict) else {}
    )
    meta = plan_data.get("meta") if isinstance(plan_data.get("meta"), dict) else {}
    exec_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
    if not exec_plan:
        return plan_data, {}

    execution_rows = exec_plan.get("execution_lanes") if isinstance(exec_plan.get("execution_lanes"), list) else []
    review_rows = exec_plan.get("review_lanes") if isinstance(exec_plan.get("review_lanes"), list) else []
    if not execution_rows:
        return plan_data, {}
    is_followup = run_control_mode == "followup"

    has_operator_lane_selector = bool(selected_execution_lane_ids or selected_review_lane_ids)
    if is_followup:
        target_exec_source = (
            selected_execution_lane_ids
            if has_operator_lane_selector
            else (critic.get("manual_followup_execution_lane_ids") or [])
        )
        target_review_source = []
    else:
        target_exec_source = selected_execution_lane_ids if has_operator_lane_selector else (critic.get("rerun_execution_lane_ids") or [])
        target_review_source = selected_review_lane_ids if has_operator_lane_selector else (critic.get("rerun_review_lane_ids") or [])
    target_exec_ids = {
        str(item).strip()[:32]
        for item in (target_exec_source or [])
        if str(item).strip()
    }
    target_review_ids = {
        str(item).strip()[:32]
        for item in (target_review_source or [])
        if str(item).strip()
    }

    if target_review_ids and not target_exec_ids:
        for row in review_rows:
            if not isinstance(row, dict):
                continue
            if _lane_id_token(row) not in target_review_ids:
                continue
            for lane_id in (row.get("depends_on") or []):
                token = str(lane_id).strip()[:32]
                if token:
                    target_exec_ids.add(token)

    filtered_execution = [
        copy.deepcopy(row)
        for row in execution_rows
        if isinstance(row, dict) and (not target_exec_ids or _lane_id_token(row) in target_exec_ids)
    ]
    if not filtered_execution:
        return plan_data, {}

    selected_exec_ids = {
        _lane_id_token(row) for row in filtered_execution if isinstance(row, dict) and _lane_id_token(row)
    }
    selected_subtask_ids = {
        str(item).strip()[:32]
        for row in filtered_execution
        if isinstance(row, dict)
        for item in (row.get("subtask_ids") or [])
        if str(item).strip()
    }

    filtered_review: List[Dict[str, Any]] = []
    if not is_followup:
        for row in review_rows:
            if not isinstance(row, dict):
                continue
            lane_id = _lane_id_token(row)
            depends_on = {
                str(item).strip()[:32]
                for item in (row.get("depends_on") or [])
                if str(item).strip()
            }
            if target_review_ids:
                if lane_id in target_review_ids:
                    filtered_review.append(copy.deepcopy(row))
                continue
            if not depends_on or depends_on.intersection(selected_exec_ids):
                filtered_review.append(copy.deepcopy(row))

    execution_roles = _dedupe_role_tokens(
        [str(row.get("role", "")).strip() for row in filtered_execution if isinstance(row, dict)]
    )
    review_roles = _dedupe_role_tokens(
        [str(row.get("role", "")).strip() for row in filtered_review if isinstance(row, dict)]
    )
    planned_roles = _dedupe_role_tokens(execution_roles + review_roles)
    if not planned_roles:
        return plan_data, {}

    filtered_plan = copy.deepcopy(plan_data)
    if isinstance(filtered_plan.get("subtasks"), list):
        filtered_plan["subtasks"] = [
            row
            for row in filtered_plan["subtasks"]
            if isinstance(row, dict)
            and (
                (selected_subtask_ids and str(row.get("id", "")).strip()[:32] in selected_subtask_ids)
                or (not selected_subtask_ids and str(row.get("owner_role", "")).strip() in planned_roles)
            )
        ]
    if isinstance(filtered_plan.get("assignments"), list):
        filtered_plan["assignments"] = [
            row
            for row in filtered_plan["assignments"]
            if isinstance(row, dict)
            and (
                (selected_subtask_ids and str(row.get("subtask_id", row.get("id", ""))).strip()[:32] in selected_subtask_ids)
                or str(row.get("role", "")).strip() in planned_roles
            )
        ]
    if isinstance(filtered_plan.get("execution_order"), list):
        filtered_plan["execution_order"] = [
            str(role).strip()
            for role in filtered_plan["execution_order"]
            if str(role).strip() in planned_roles
        ]

    meta_out = filtered_plan.get("meta") if isinstance(filtered_plan.get("meta"), dict) else {}
    team_spec = meta_out.get("phase2_team_spec") if isinstance(meta_out.get("phase2_team_spec"), dict) else {}
    exec_groups = team_spec.get("execution_groups") if isinstance(team_spec.get("execution_groups"), list) else []
    review_groups = team_spec.get("review_groups") if isinstance(team_spec.get("review_groups"), list) else []
    filtered_exec_groups = [
        copy.deepcopy(row)
        for row in exec_groups
        if isinstance(row, dict) and _lane_id_token(row) in selected_exec_ids
    ]
    filtered_review_groups = [
        copy.deepcopy(row)
        for row in review_groups
        if isinstance(row, dict)
        and (
            _lane_id_token(row) in {_lane_id_token(item) for item in filtered_review if isinstance(item, dict)}
            or not str(row.get("group_id", "")).strip()
        )
    ]
    filtered_team_spec = normalize_phase2_team_spec(
        {
            **team_spec,
            "execution_groups": filtered_exec_groups,
            "review_groups": filtered_review_groups,
            "team_roles": planned_roles,
        },
        plan=filtered_plan,
        roles=planned_roles,
        verifier_roles=review_roles,
        require_verifier=bool(review_roles),
    )
    filtered_exec_plan = normalize_phase2_execution_plan(
        {
            **exec_plan,
            "execution_lanes": filtered_execution,
            "review_lanes": filtered_review,
            "execution_mode": "parallel" if len(filtered_execution) > 1 else "single",
            "review_mode": (
                "parallel"
                if len(filtered_review) > 1
                else ("single" if filtered_review else "skip")
            ),
            "parallel_workers": len(filtered_execution) > 1,
            "parallel_reviews": len(filtered_review) > 1,
        },
        team_spec=filtered_team_spec,
        readonly=bool(exec_plan.get("readonly", True)),
    )
    meta_out["phase2_team_spec"] = filtered_team_spec
    meta_out["phase2_execution_plan"] = filtered_exec_plan
    filtered_plan["meta"] = meta_out
    return filtered_plan, {
        "rerun_execution_lane_ids": sorted(selected_exec_ids),
        "rerun_review_lane_ids": [
            _lane_id_token(row) for row in filtered_review if isinstance(row, dict) and _lane_id_token(row)
        ],
        "execution_roles": execution_roles,
        "review_roles": review_roles,
        "planned_roles": planned_roles,
        "subtask_ids": sorted(selected_subtask_ids),
    }
