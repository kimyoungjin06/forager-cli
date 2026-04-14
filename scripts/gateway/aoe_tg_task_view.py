#!/usr/bin/env python3
"""Task view helpers extracted from the gateway monolith."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from aoe_tg_action_audit import (
    load_latest_action_audit_for_task,
    load_latest_judge_decision_bridge_summary_for_runtime,
    load_latest_replan_auto_route_status_summary_for_runtime,
    load_latest_replan_auto_routing_policy_summary_for_runtime,
)
from aoe_tg_context_pack import load_context_pack
from aoe_tg_operator_summary import task_intent_summary
from aoe_tg_operator_surface import append_operator_status_summary_lines
from aoe_tg_orch_contract import derive_tf_phase, derive_tf_phase_reason, normalize_tf_phase
from aoe_tg_role_aliases import canonicalize_role_name
from aoe_tg_worker_task_contract import (
    derive_worker_task_module_checklist,
    derive_worker_task_module_gate,
    derive_worker_task_module_profile,
    resolve_worker_module_policy,
)
from aoe_tg_team_observatory import (
    observatory_lane_lines,
    observatory_task_line,
    task_team_observatory_snapshot,
)


DEFAULT_PROJECT_ALIAS_MAX = 999
LIFECYCLE_STAGES = (
    "intake",
    "planning",
    "staffing",
    "execution",
    "verification",
    "integration",
    "close",
)


def normalize_project_name(name: str) -> str:
    src = (name or "").strip().lower()
    out = []
    for ch in src:
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    token = "".join(out).strip("._-")
    return token or "default"


def normalize_project_alias(token: str, max_alias: int = DEFAULT_PROJECT_ALIAS_MAX) -> str:
    raw = str(token or "").strip().upper()
    if not raw:
        return ""
    body = raw[1:] if raw.startswith("O") else raw
    if not body.isdigit():
        return ""
    idx = int(body)
    if idx < 1 or idx > int(max_alias):
        return ""
    return f"O{idx}"


def dedupe_roles(roles: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in roles:
        token = canonicalize_role_name(item)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def critic_has_blockers(critic: Dict[str, Any]) -> bool:
    approved = bool(critic.get("approved", True))
    issues = critic.get("issues") or []
    return (not approved) or bool(issues)


def task_display_label(task: Dict[str, Any], fallback_request_id: str = "") -> str:
    short_id = str(task.get("short_id", "")).strip().upper()
    alias = str(task.get("alias", "")).strip()
    if short_id and alias:
        return f"{short_id} | {alias}"
    if alias:
        return alias
    if short_id:
        return short_id
    rid = str(task.get("request_id", "")).strip() or str(fallback_request_id or "").strip()
    return rid if rid else "-"


def task_short_to_tf_id(short_id: str) -> str:
    short = str(short_id or "").strip().upper()
    if not short:
        return ""
    tf_id = re.sub(r"^T-", "TF-", short)
    if tf_id.startswith("TF-"):
        return tf_id
    token = re.sub(r"[^A-Z0-9._-]+", "_", short).strip("._-")
    return f"TF-{token[:24] or 'UNK'}"


def request_to_tf_id(request_id: str) -> str:
    token = re.sub(r"[^A-Z0-9._-]+", "_", str(request_id or "").strip().upper()).strip("._-")
    return f"TF-REQ-{(token[:24] or 'UNK')}"


def build_task_context(
    *,
    request_id: str = "",
    entry: Optional[Dict[str, Any]] = None,
    task: Optional[Dict[str, Any]] = None,
    tf_meta: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    context: Dict[str, str] = {}

    def put(key: str, value: Any, transform: Optional[Callable[[str], str]] = None) -> None:
        token = str(value or "").strip()
        if not token:
            return
        if transform is not None:
            token = transform(token)
        if token:
            context[key] = token

    for source in (extra, task.get("context") if isinstance(task, dict) else None):
        if not isinstance(source, dict):
            continue
        put("project_key", source.get("project_key"), normalize_project_name)
        put("project_alias", source.get("project_alias"), normalize_project_alias)
        put("project_root", source.get("project_root"))
        put("team_dir", source.get("team_dir"))
        put("tf_id", source.get("tf_id"))
        put("task_short_id", source.get("task_short_id"), lambda s: s.upper())
        put("task_alias", source.get("task_alias"))
        put("workdir", source.get("workdir"))
        put("run_dir", source.get("run_dir"))
        put("branch", source.get("branch"))
        put("exec_mode", source.get("exec_mode"))
        put("source_request_id", source.get("source_request_id"))
        put("control_mode", source.get("control_mode"))
        put("intent_command", source.get("intent_command"))
        put("intent_action", source.get("intent_action"))
        put("intent_class", source.get("intent_class"))
        put("intent_trace", source.get("intent_trace"))
        put("gateway_request_id", source.get("gateway_request_id"))

    if isinstance(tf_meta, dict):
        put("project_key", tf_meta.get("project_key"), normalize_project_name)
        put("project_alias", tf_meta.get("project_alias"), normalize_project_alias)
        put("project_root", tf_meta.get("project_root"))
        put("team_dir", tf_meta.get("team_dir"))
        put("tf_id", tf_meta.get("tf_id"))
        put("task_short_id", tf_meta.get("task_short_id"), lambda s: s.upper())
        put("task_alias", tf_meta.get("task_alias"))
        put("workdir", tf_meta.get("workdir"))
        put("run_dir", tf_meta.get("run_dir"))
        put("branch", tf_meta.get("branch"))
        put("exec_mode", tf_meta.get("mode"))
        put("source_request_id", tf_meta.get("source_request_id"))
        put("control_mode", tf_meta.get("control_mode"))
        put("gateway_request_id", tf_meta.get("gateway_request_id") or tf_meta.get("request_id"))

    if isinstance(entry, dict):
        put("project_key", entry.get("name"), normalize_project_name)
        put("project_alias", entry.get("project_alias"), normalize_project_alias)
        put("project_root", entry.get("project_root"))
        put("team_dir", entry.get("team_dir"))

    if isinstance(task, dict):
        put("task_short_id", task.get("short_id"), lambda s: s.upper())
        put("task_alias", task.get("alias"))
        put("source_request_id", task.get("source_request_id"))
        put("control_mode", task.get("control_mode"))
        put("intent_command", task.get("intent_command"))
        put("intent_action", task.get("intent_action"))
        put("intent_class", task.get("intent_class"))
        put("intent_trace", task.get("intent_trace"))

    if context.get("task_short_id"):
        context["tf_id"] = task_short_to_tf_id(context["task_short_id"])
    elif not context.get("tf_id"):
        context["tf_id"] = request_to_tf_id(request_id)

    if request_id and not context.get("gateway_request_id"):
        context["gateway_request_id"] = str(request_id).strip()

    return context


def summarize_task_lifecycle(project_name: str, task: Dict[str, Any]) -> str:
    request_id = str(task.get("request_id", "-")).strip() or "-"
    label = task_display_label(task, fallback_request_id=request_id)
    status = str(task.get("status", "pending"))
    mode = str(task.get("mode", "dispatch"))
    roles = dedupe_roles(task.get("roles") or [])
    verifiers = dedupe_roles(task.get("verifier_roles") or [])
    stages = task.get("stages") or {}

    lines = [
        f"runtime: {project_name}",
        f"task: {label}",
        f"request_id: {request_id}",
        f"status: {status}",
        f"team_phase: {normalize_tf_phase(derive_tf_phase(task), 'queued')}",
        f"mode: {mode}",
        f"roles: {', '.join(roles) if roles else '-'}",
        f"verifier_roles: {', '.join(verifiers) if verifiers else '-'}",
    ]
    tf_phase_reason = str(task.get("tf_phase_reason", "")).strip() or derive_tf_phase_reason(task)
    if tf_phase_reason:
        lines.append(f"team_phase_reason: {tf_phase_reason}")
    phase1_mode = str(task.get("phase1_mode", "")).strip()
    phase1_rounds = max(0, int(task.get("phase1_rounds", 0) or 0))
    phase1_providers = dedupe_roles(task.get("phase1_providers") or [])
    phase1_candidate_roles = dedupe_roles(task.get("phase1_candidate_roles") or [])
    phase1_role_preset = str(task.get("phase1_role_preset", "")).strip()
    phase2_team_preset = str(task.get("phase2_team_preset", "")).strip()
    phase1_current_phase = str(task.get("phase1_current_phase", "")).strip()
    phase1_current_round = max(0, int(task.get("phase1_current_round", 0) or 0))
    phase1_current_total = max(0, int(task.get("phase1_current_total_rounds", 0) or 0))
    phase1_current_provider = str(task.get("phase1_current_provider", "")).strip()
    phase1_current_planner = str(task.get("phase1_current_planner", "")).strip()
    phase1_current_critic = str(task.get("phase1_current_critic", "")).strip()
    if phase1_mode or phase1_rounds or phase1_providers:
        lines.append(
            "phase1: {mode} rounds={rounds} providers={providers}".format(
                mode=phase1_mode or "single",
                rounds=phase1_rounds or 1,
                providers=", ".join(phase1_providers) if phase1_providers else "-",
            )
        )
    if phase1_current_phase or phase1_current_round or phase1_current_provider or phase1_current_planner or phase1_current_critic:
        actor_parts: List[str] = []
        if phase1_current_provider:
            actor_parts.append(f"provider={phase1_current_provider}")
        if phase1_current_planner:
            actor_parts.append(f"planner={phase1_current_planner}")
        if phase1_current_critic:
            actor_parts.append(f"critic={phase1_current_critic}")
        progress_parts = [phase1_current_phase or "planning"]
        if phase1_current_round and phase1_current_total:
            progress_parts.append(f"{phase1_current_round}/{phase1_current_total}")
        progress_parts.extend(actor_parts)
        lines.append("phase1_progress: " + " ".join(progress_parts))
    if phase1_candidate_roles:
        lines.append("phase1_candidate_roles: " + ", ".join(phase1_candidate_roles))
    if phase1_role_preset or phase2_team_preset:
        lines.append(
            "team_preset: phase1={phase1} phase2={phase2}".format(
                phase1=phase1_role_preset or "-",
                phase2=phase2_team_preset or phase1_role_preset or "-",
            )
        )
    rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
    degraded_by = [str(x).strip() for x in ((task.get("result") or {}).get("degraded_by") or []) if str(x).strip()] if isinstance(task.get("result"), dict) else []
    if rate_limit:
        providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
        retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
        retry_at = str(rate_limit.get("retry_at", "")).strip()
        lines.append(
            "rate_limit: mode={mode} providers={providers} retry_after={retry} retry_at={retry_at}".format(
                mode=str(rate_limit.get("mode", "")).strip() or "-",
                providers=", ".join(providers) if providers else "-",
                retry=(f"{retry_after}s" if retry_after > 0 else "-"),
                retry_at=retry_at or "-",
            )
        )
    if degraded_by:
        lines.append("degraded_by: " + ", ".join(degraded_by))

    context = build_task_context(request_id=request_id, task=task)
    if context:
        lines.append(
            "context: {proj} ({key}) / {tf}".format(
                proj=context.get("project_alias", "-"),
                key=context.get("project_key", "-"),
                tf=context.get("tf_id", "-"),
            )
        )
        if context.get("task_alias"):
            lines.append(f"context_alias: {context.get('task_alias')}")
        if context.get("workdir"):
            lines.append(f"context_workdir: {context.get('workdir')}")
        if context.get("run_dir"):
            lines.append(f"context_run_dir: {context.get('run_dir')}")
        if context.get("source_request_id"):
            lines.append(
                "context_lineage: {mode} <- {source}".format(
                    mode=context.get("control_mode", "dispatch") or "dispatch",
                    source=context.get("source_request_id"),
                )
            )
        latest_intent = task_intent_summary(task)
        latest_action = load_latest_action_audit_for_task(context.get("team_dir"), request_id)
        append_operator_status_summary_lines(
            lines,
            latest_intent=latest_intent,
            latest_action=latest_action,
        )
        team_dir_raw = str(context.get("team_dir", "")).strip()
        if team_dir_raw:
            pack = load_context_pack(
                team_dir_raw,
                entry={
                    "name": context.get("project_key", ""),
                    "project_alias": context.get("project_alias", ""),
                    "project_root": context.get("project_root", ""),
                },
                task=task,
                project_root=context.get("project_root", ""),
            )
            lines.append(f"context_pack: {str(pack.get('summary', '')).strip() or '-'}")
            lines.append(f"context_pack_docs: {str(pack.get('docs_summary', '')).strip() or '-'}")
            if str(pack.get("excluded_summary", "")).strip() and str(pack.get("excluded_summary", "")).strip() != "-":
                lines.append(f"context_pack_excluded: {str(pack.get('excluded_summary', '')).strip()}")
            project_alias = str(context.get("project_alias", "")).strip()
            if project_alias:
                latest_judge_decision_bridge_summary = load_latest_judge_decision_bridge_summary_for_runtime(
                    team_dir_raw,
                    project_alias=project_alias,
                )
                latest_replan_auto_routing_policy_summary = load_latest_replan_auto_routing_policy_summary_for_runtime(
                    team_dir_raw,
                    project_alias=project_alias,
                )
                latest_replan_auto_route_status_summary = load_latest_replan_auto_route_status_summary_for_runtime(
                    team_dir_raw,
                    project_alias=project_alias,
                )
                if latest_judge_decision_bridge_summary not in {"", "-"}:
                    lines.append(f"latest_judge_decision_bridge: {latest_judge_decision_bridge_summary}")
                if latest_replan_auto_routing_policy_summary not in {"", "-"}:
                    lines.append(f"replan_auto_routing_policy: {latest_replan_auto_routing_policy_summary}")
                if latest_replan_auto_route_status_summary not in {"", "-"}:
                    lines.append(f"auto_route_status: {latest_replan_auto_route_status_summary}")

    lines.append("lifecycle:")
    for name in LIFECYCLE_STAGES:
        lines.append(f"- {name}: {str(stages.get(name, 'pending'))}")

    plan = task.get("plan")
    if isinstance(plan, dict):
        subtasks = plan.get("subtasks") or []
        meta = plan.get("meta") if isinstance(plan.get("meta"), dict) else {}
        team_spec = meta.get("phase2_team_spec") if isinstance(meta.get("phase2_team_spec"), dict) else {}
        execution_plan = meta.get("phase2_execution_plan") if isinstance(meta.get("phase2_execution_plan"), dict) else {}
        lane_states = task.get("lane_states") if isinstance(task.get("lane_states"), dict) else {}
        execution_lane_state_rows = lane_states.get("execution") if isinstance(lane_states.get("execution"), list) else []
        review_lane_state_rows = lane_states.get("review") if isinstance(lane_states.get("review"), list) else []
        execution_lane_status = {
            str(row.get("lane_id", "")).strip(): str(row.get("status", "")).strip()
            for row in execution_lane_state_rows
            if isinstance(row, dict)
        }
        review_lane_status = {
            str(row.get("lane_id", "")).strip(): str(row.get("status", "")).strip()
            for row in review_lane_state_rows
            if isinstance(row, dict)
        }
        review_lane_verdict = {
            str(row.get("lane_id", "")).strip(): str(row.get("verdict", "")).strip()
            for row in review_lane_state_rows
            if isinstance(row, dict) and str(row.get("verdict", "")).strip()
        }
        review_lane_action = {
            str(row.get("lane_id", "")).strip(): str(row.get("action", "")).strip()
            for row in review_lane_state_rows
            if isinstance(row, dict) and str(row.get("action", "")).strip()
        }
        lane_summary = lane_states.get("summary") if isinstance(lane_states.get("summary"), dict) else {}
        plan_summary = str(plan.get("summary", "")).strip()
        if plan_summary:
            lines.append("plan_summary: " + plan_summary)
        lines.append(f"plan_subtasks: {len(subtasks)}")

        owner_counts: Dict[str, int] = {}
        for row in subtasks:
            if not isinstance(row, dict):
                continue
            role = str(row.get("owner_role", "")).strip() or "Worker"
            owner_counts[role] = owner_counts.get(role, 0) + 1
        if owner_counts:
            lines.append("plan_owner_load: " + ", ".join(f"{role}={cnt}" for role, cnt in owner_counts.items()))

        execution_groups = team_spec.get("execution_groups") if isinstance(team_spec.get("execution_groups"), list) else []
        if execution_groups:
            lines.append(
                "phase2_execution: {mode} lanes={count}".format(
                    mode=str(team_spec.get("execution_mode", "single")).strip() or "single",
                    count=len(execution_groups),
                )
            )
            for row in execution_groups[:6]:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("group_id", "")).strip() or "E"
                role = str(row.get("role", "")).strip() or "Worker"
                subtask_ids = [str(item).strip() for item in (row.get("subtask_ids") or []) if str(item).strip()]
                lines.append(f"- lane {gid} [{role}] -> {', '.join(subtask_ids) if subtask_ids else '-'}")

        review_groups = team_spec.get("review_groups") if isinstance(team_spec.get("review_groups"), list) else []
        if review_groups:
            lines.append(
                "phase2_review: {mode} lanes={count}".format(
                    mode=str(team_spec.get("review_mode", "skip")).strip() or "skip",
                    count=len(review_groups),
                )
            )
            for row in review_groups[:4]:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("group_id", "")).strip() or "R"
                role = str(row.get("role", "")).strip() or "Codex-Reviewer"
                kind = str(row.get("kind", "")).strip() or "verifier"
                lines.append(f"- review {gid} [{role}/{kind}]")
        if team_spec:
            lines.append(
                "phase2_quality: critic={critic} integration={integration}".format(
                    critic=str(team_spec.get("critic_role", "")).strip() or "-",
                    integration=str(team_spec.get("integration_role", "")).strip() or "-",
                )
            )
        evidence_required = [
            str(item).strip()
            for item in (plan.get("evidence_required") or [])
            if str(item).strip()
        ]
        if evidence_required:
            lines.append("phase2_evidence: " + " | ".join(evidence_required[:3]))

        execution_lanes = execution_plan.get("execution_lanes") if isinstance(execution_plan.get("execution_lanes"), list) else []
        review_lanes = execution_plan.get("review_lanes") if isinstance(execution_plan.get("review_lanes"), list) else []
        if not execution_lanes and execution_lane_state_rows:
            execution_lanes = [
                {
                    "lane_id": str(row.get("lane_id", "")).strip(),
                    "role": str(row.get("role", "")).strip(),
                    "subtask_ids": list(row.get("subtask_ids") or []),
                    "parallel": bool(row.get("parallel", False)),
                }
                for row in execution_lane_state_rows
                if isinstance(row, dict) and str(row.get("lane_id", "")).strip()
            ]
        if not review_lanes and review_lane_state_rows:
            review_lanes = [
                {
                    "lane_id": str(row.get("lane_id", "")).strip(),
                    "role": str(row.get("role", "")).strip(),
                    "kind": str(row.get("kind", "")).strip() or "verifier",
                    "depends_on": list(row.get("depends_on") or []),
                    "parallel": bool(row.get("parallel", False)),
                }
                for row in review_lane_state_rows
                if isinstance(row, dict) and str(row.get("lane_id", "")).strip()
            ]
        if execution_plan:
            lines.append(
                "phase2_exec_plan: {mode} workers_parallel={workers} reviews_parallel={reviews} readonly={readonly}".format(
                    mode=str(execution_plan.get("execution_mode", "single")).strip() or "single",
                    workers="yes" if bool(execution_plan.get("parallel_workers", len(execution_lanes) > 1)) else "no",
                    reviews="yes" if bool(execution_plan.get("parallel_reviews", len(review_lanes) > 1)) else "no",
                    readonly="yes" if bool(execution_plan.get("readonly", True)) else "no",
                )
            )
        if lane_summary:
            exec_counts = lane_summary.get("execution") if isinstance(lane_summary.get("execution"), dict) else {}
            review_counts = lane_summary.get("review") if isinstance(lane_summary.get("review"), dict) else {}
            review_verdicts = lane_summary.get("review_verdicts") if isinstance(lane_summary.get("review_verdicts"), dict) else {}
            parts: List[str] = []
            if exec_counts:
                parts.append("exec " + ", ".join(f"{key}={value}" for key, value in sorted(exec_counts.items())))
            if review_counts:
                parts.append("review " + ", ".join(f"{key}={value}" for key, value in sorted(review_counts.items())))
            if review_verdicts:
                parts.append("review_verdict " + ", ".join(f"{key}={value}" for key, value in sorted(review_verdicts.items())))
            if parts:
                lines.append("phase2_lane_state: " + " | ".join(parts))
        for row in execution_lanes[:6]:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("lane_id", "")).strip() or "L"
            role = str(row.get("role", "")).strip() or "Worker"
            subtask_ids = [str(item).strip() for item in (row.get("subtask_ids") or []) if str(item).strip()]
            mode = "parallel" if bool(row.get("parallel", True)) else "serial"
            status = execution_lane_status.get(gid, "")
            suffix = f" [{status}]" if status else ""
            lines.append(f"- exec {gid} [{role}/{mode}]{suffix} -> {', '.join(subtask_ids) if subtask_ids else '-'}")
        for row in review_lanes[:4]:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("lane_id", "")).strip() or "R"
            role = str(row.get("role", "")).strip() or "Codex-Reviewer"
            kind = str(row.get("kind", "")).strip() or "verifier"
            depends = [str(item).strip() for item in (row.get("depends_on") or []) if str(item).strip()]
            mode = "parallel" if bool(row.get("parallel", True)) else "serial"
            suffix = f" after {', '.join(depends)}" if depends else ""
            status = review_lane_status.get(gid, "")
            status_suffix = f" [{status}]" if status else ""
            verdict = review_lane_verdict.get(gid, "")
            action = review_lane_action.get(gid, "")
            verdict_suffix = ""
            if verdict:
                verdict_suffix = f" -> {verdict}"
                if action and action != "none":
                    verdict_suffix += f"/{action}"
            lines.append(f"- critic {gid} [{role}/{kind}/{mode}]{status_suffix}{verdict_suffix}{suffix}")
        observatory = task_team_observatory_snapshot(task)
        if observatory.get("lanes"):
            lines.append(observatory_task_line(observatory))
            lines.extend(observatory_lane_lines(observatory, limit=4))

        for row in subtasks[:6]:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("id", "")).strip() or "S"
            role = str(row.get("owner_role", "")).strip() or "Worker"
            title = str(row.get("title", "")).strip() or str(row.get("goal", "")).strip() or "subtask"
            lines.append(f"- plan {sid} [{role}] {title}")

    critic = task.get("plan_critic")
    if isinstance(critic, dict):
        issues = critic.get("issues") or []
        recs = critic.get("recommendations") or []
        approved = not critic_has_blockers(critic)
        lines.append(f"plan_critic: {'approved' if approved else 'needs_fix'}")
        for item in issues[:4]:
            token = str(item or "").strip()
            if token:
                lines.append("- issue: " + token)
        for item in recs[:4]:
            token = str(item or "").strip()
            if token:
                lines.append("- recommendation: " + token)

    gate = task.get("plan_gate_passed")
    if isinstance(gate, bool):
        lines.append(f"plan_gate: {'passed' if gate else 'blocked'}")
        if gate is False:
            gate_reason = str(task.get("plan_gate_reason", "")).strip()
            if gate_reason:
                lines.append("plan_gate_reason: " + gate_reason[:240])

    execution_brief_status = str(task.get("execution_brief_status", "")).strip().lower()
    execution_brief_summary = str(task.get("execution_brief_summary", "")).strip()
    if execution_brief_status or execution_brief_summary:
        lines.append("execution_brief: " + (execution_brief_status or "-"))
        if execution_brief_summary:
            lines.append("execution_brief_summary: " + execution_brief_summary[:240])
    executable_slice = [str(item).strip() for item in (task.get("execution_brief_executable_slice") or []) if str(item).strip()]
    if executable_slice:
        lines.append("execution_brief_do: " + ", ".join(executable_slice[:6]))
    blocked_slice = [str(item).strip() for item in (task.get("execution_brief_blocked_slice") or []) if str(item).strip()]
    if blocked_slice:
        lines.append("execution_brief_blocked: " + ", ".join(blocked_slice[:6]))
    operator_decision = str(task.get("execution_brief_operator_decision", "")).strip()
    if operator_decision:
        lines.append("execution_brief_decision: " + operator_decision[:240])

    background_run_status = str(task.get("background_run_status", "")).strip().lower()
    background_runner = str(task.get("background_run_runner_target", "")).strip()
    background_ticket = str(task.get("background_run_ticket_id", "")).strip()
    background_launch = str(task.get("background_run_launch_mode", "")).strip()
    if background_run_status or background_runner or background_ticket:
        lines.append("background_run: " + (background_run_status or "-"))
        detail_parts: List[str] = []
        if background_runner:
            detail_parts.append(f"runner={background_runner}")
        if background_ticket:
            detail_parts.append(f"ticket={background_ticket}")
        if background_launch:
            detail_parts.append(f"launch={background_launch}")
        if detail_parts:
            lines.append("background_run_detail: " + " | ".join(detail_parts)[:240])
    background_runtime_handle = str(task.get("background_run_runtime_handle", "")).strip()
    background_runtime_summary = str(task.get("background_run_runtime_summary", "")).strip()
    if background_runtime_handle or background_runtime_summary:
        runtime_parts: List[str] = []
        if background_runtime_handle:
            runtime_parts.append(f"handle={background_runtime_handle}")
        if background_runtime_summary:
            runtime_parts.append(background_runtime_summary)
        lines.append("background_run_runtime: " + " | ".join(runtime_parts)[:240])
    background_external_phase = str(task.get("background_run_external_phase", "")).strip()
    background_external_note = str(task.get("background_run_external_note", "")).strip()
    if background_external_phase or background_external_note:
        external_parts: List[str] = []
        if background_external_phase:
            external_parts.append(background_external_phase)
        if background_external_note:
            external_parts.append(background_external_note)
        lines.append("background_run_external: " + " | ".join(external_parts)[:240])
    background_evidence = str(task.get("background_run_evidence_bundle", "")).strip()
    if background_evidence:
        lines.append("background_run_evidence: " + background_evidence[:240])
    background_artifacts = [str(item).strip() for item in (task.get("background_run_evidence_artifacts") or []) if str(item).strip()]
    if background_artifacts:
        lines.append("background_run_artifacts: " + ", ".join(background_artifacts[:6]))
    background_launch_spec = str(task.get("background_run_launch_spec_summary", "")).strip()
    if background_launch_spec:
        lines.append("background_run_launch_spec: " + background_launch_spec[:240])
    background_model_plan = str(task.get("background_run_model_plan_summary", "")).strip()
    if background_model_plan:
        lines.append("background_run_model_plan: " + background_model_plan[:240])
    background_task_contract = str(task.get("background_run_task_contract_summary", "")).strip()
    if background_task_contract:
        lines.append("background_run_task_contract: " + background_task_contract[:240])
    background_task_contract_module_summary = str(task.get("background_run_task_contract_module_summary", "")).strip()
    background_task_contract_module = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if not background_task_contract_module_summary and background_task_contract_module not in {"", "-", "general"}:
        background_task_contract_module_summary = background_task_contract_module
    if background_task_contract_module_summary:
        lines.append("background_run_worker_module: " + background_task_contract_module_summary[:240])
    background_task_contract_policy_summary = str(task.get("background_run_task_contract_policy_summary", "")).strip()
    if not background_task_contract_policy_summary and background_task_contract_module not in {"", "-", "general"}:
        background_task_contract_policy_summary = str(
            resolve_worker_module_policy({"module_kind": background_task_contract_module}).get("summary", "")
        ).strip()
    if background_task_contract_policy_summary and background_task_contract_policy_summary != "-":
        lines.append("background_run_worker_policy: " + background_task_contract_policy_summary[:240])
    background_worker_gate = str(task.get("background_run_worker_gate_summary", "")).strip()
    if not background_worker_gate and background_task_contract_module not in {"", "-", "general"}:
        background_worker_gate = str(
            derive_worker_task_module_gate(
                {
                    "module_kind": background_task_contract_module,
                    "module_policy": task.get("background_run_task_contract_policy"),
                    "artifact_targets": task.get("background_run_worker_update_stub_targets"),
                },
                {
                    "status": task.get("background_run_worker_result_status"),
                    "summary": task.get("background_run_worker_result_summary"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
            ).get("summary_line", "")
        ).strip()
    if background_worker_gate:
        lines.append("background_run_worker_gate: " + background_worker_gate[:240])
    background_worker_profile = str(task.get("background_run_worker_profile_summary", "")).strip()
    if not background_worker_profile and background_task_contract_module not in {"", "-", "general"}:
        background_worker_profile = str(
            derive_worker_task_module_profile(
                {
                    "module_kind": background_task_contract_module,
                    "module_policy": task.get("background_run_task_contract_policy"),
                    "artifact_targets": task.get("background_run_worker_update_stub_targets"),
                },
                {
                    "status": task.get("background_run_worker_result_status"),
                    "summary": task.get("background_run_worker_result_summary"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
                gate={"state": task.get("background_run_worker_gate_status"), "summary_line": background_worker_gate},
            ).get("summary_line", "")
        ).strip()
    if background_worker_profile:
        lines.append("background_run_worker_profile: " + background_worker_profile[:240])
    background_worker_checklist = str(task.get("background_run_worker_checklist_summary", "")).strip()
    if not background_worker_checklist and background_task_contract_module not in {"", "-", "general"}:
        background_worker_checklist = str(
            derive_worker_task_module_checklist(
                {
                    "module_kind": background_task_contract_module,
                    "module_policy": task.get("background_run_task_contract_policy"),
                    "artifact_targets": task.get("background_run_worker_update_stub_targets"),
                },
                {
                    "status": task.get("background_run_worker_result_status"),
                    "summary": task.get("background_run_worker_result_summary"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
                gate={"state": task.get("background_run_worker_gate_status"), "summary_line": background_worker_gate},
                profile={
                    "state": task.get("background_run_worker_profile_status"),
                    "summary_line": background_worker_profile,
                },
            ).get("summary_line", "")
        ).strip()
    if background_worker_checklist:
        lines.append("background_run_worker_checklist: " + background_worker_checklist[:240])
    background_worker_result = str(task.get("background_run_worker_result_summary", "")).strip()
    if background_worker_result:
        lines.append("background_run_worker_result: " + background_worker_result[:240])
    background_worker_result_actions = [
        str(item).strip()
        for item in (
            (task.get("background_run_worker_result_actions") if isinstance(task.get("background_run_worker_result_actions"), list) else [])
            or []
        )
        if str(item).strip()
    ]
    if background_worker_result_actions:
        lines.append("background_run_worker_actions: " + ", ".join(background_worker_result_actions[:4])[:240])
    background_worker_result_cautions = [
        str(item).strip()
        for item in (
            (task.get("background_run_worker_result_cautions") if isinstance(task.get("background_run_worker_result_cautions"), list) else [])
            or []
        )
        if str(item).strip()
    ]
    if background_worker_result_cautions:
        lines.append("background_run_worker_cautions: " + ", ".join(background_worker_result_cautions[:4])[:240])
    background_worker_result_refs = [
        str(item).strip()
        for item in (
            (task.get("background_run_worker_result_evidence_refs") if isinstance(task.get("background_run_worker_result_evidence_refs"), list) else [])
            or []
        )
        if str(item).strip()
    ]
    if background_worker_result_refs:
        lines.append("background_run_worker_refs: " + ", ".join(background_worker_result_refs[:6])[:240])
    background_worker_update_stub = str(task.get("background_run_worker_update_stub_summary", "")).strip()
    if background_worker_update_stub:
        lines.append("background_run_worker_update_stub: " + background_worker_update_stub[:240])
    background_worker_update_targets = [
        str(item).strip()
        for item in (
            (task.get("background_run_worker_update_stub_targets") if isinstance(task.get("background_run_worker_update_stub_targets"), list) else [])
            or []
        )
        if str(item).strip()
    ]
    if background_worker_update_targets:
        lines.append("background_run_worker_targets: " + ", ".join(background_worker_update_targets[:6])[:240])
    background_worker_update_proposals = str(task.get("background_run_worker_update_proposal_summary", "")).strip()
    if background_worker_update_proposals:
        lines.append("background_run_worker_update_proposals: " + background_worker_update_proposals[:240])
    background_worker_apply_accept = str(task.get("background_run_worker_apply_accept_summary", "")).strip()
    if background_worker_apply_accept:
        lines.append("background_run_worker_apply_accept: " + background_worker_apply_accept[:240])
    background_worker_syncback = str(task.get("background_run_worker_syncback_summary", "")).strip()
    if background_worker_syncback:
        lines.append("background_run_worker_syncback: " + background_worker_syncback[:240])
    background_manual_step_execution = str(task.get("background_run_manual_step_execution_summary", "")).strip()
    if background_manual_step_execution:
        lines.append("background_run_manual_step_execution: " + background_manual_step_execution[:240])
    background_canonical_writeback = str(task.get("background_run_canonical_writeback_summary", "")).strip()
    if background_canonical_writeback:
        lines.append("background_run_canonical_writeback: " + background_canonical_writeback[:240])
    background_canonical_mutation = str(task.get("background_run_canonical_mutation_summary", "")).strip()
    if background_canonical_mutation:
        lines.append("background_run_canonical_mutation: " + background_canonical_mutation[:240])
    background_judge_binding = str(task.get("background_run_model_judge_binding_summary", "")).strip()
    if background_judge_binding:
        lines.append("background_run_model_judge: " + background_judge_binding[:240])
    background_judge_probe = str(task.get("background_run_model_judge_probe_summary", "")).strip()
    if background_judge_probe:
        lines.append("background_run_model_judge_probe: " + background_judge_probe[:240])
    background_escalation_binding = str(task.get("background_run_model_escalation_binding_summary", "")).strip()
    if background_escalation_binding:
        lines.append("background_run_model_escalation: " + background_escalation_binding[:240])
    background_escalation_probe = str(task.get("background_run_model_escalation_probe_summary", "")).strip()
    if background_escalation_probe:
        lines.append("background_run_model_escalation_probe: " + background_escalation_probe[:240])
    reentry_rails_summary = str(task.get("reentry_rails_summary", "")).strip()
    if reentry_rails_summary:
        lines.append("reentry_rails: " + reentry_rails_summary[:240])

    try:
        plan_review_count = max(0, int(task.get("plan_review_count", 0) or 0))
    except Exception:
        plan_review_count = 0
    plan_convergence_status = str(task.get("plan_convergence_status", "")).strip().lower()
    try:
        plan_last_round = max(0, int(task.get("plan_last_round", 0) or 0))
    except Exception:
        plan_last_round = 0
    if plan_review_count or plan_convergence_status or plan_last_round:
        lines.append(
            "plan_convergence: {status} reviews={reviews} last_round={round_no}".format(
                status=plan_convergence_status or "-",
                reviews=plan_review_count or 0,
                round_no=plan_last_round or plan_review_count or 0,
            )
        )
    plan_stalled_reason = str(task.get("plan_stalled_reason", "")).strip()
    if plan_stalled_reason:
        lines.append("plan_stalled_reason: " + plan_stalled_reason[:240])
    plan_issue_history = task.get("plan_issue_history") if isinstance(task.get("plan_issue_history"), list) else []
    if plan_issue_history:
        latest_issue = plan_issue_history[-1] if isinstance(plan_issue_history[-1], dict) else {}
        latest_pass = str(latest_issue.get("review_pass", "")).strip().lower()
        latest_issue_text = str(latest_issue.get("primary_issue", "")).strip()
        if latest_pass or latest_issue_text:
            lines.append(
                "plan_review_focus: {review_pass} | {issue}".format(
                    review_pass=latest_pass or "-",
                    issue=(latest_issue_text[:240] if latest_issue_text else "-"),
                )
            )

    replans = task.get("plan_replans")
    if isinstance(replans, list) and replans:
        lines.append(f"plan_replans: {len(replans)}")
        for row in replans[-3:]:
            if not isinstance(row, dict):
                continue
            attempt = int(row.get("attempt", 0) or 0)
            verdict = str(row.get("critic", "")).strip() or "unknown"
            subtasks = int(row.get("subtasks", 0) or 0)
            lines.append(f"- replan#{attempt}: critic={verdict} subtasks={subtasks}")

    exec_critic = task.get("exec_critic")
    if isinstance(exec_critic, dict):
        verdict = str(exec_critic.get("verdict", "")).strip() or "unknown"
        action = str(exec_critic.get("action", "")).strip() or "-"
        reason = str(exec_critic.get("reason", "")).strip()
        attempt = int(exec_critic.get("attempt", 0) or 0)
        max_attempts = int(exec_critic.get("max_attempts", 0) or 0)
        at = str(exec_critic.get("at", "")).strip()
        lines.append(f"exec_critic: {verdict} (action={action})")
        if attempt and max_attempts:
            lines.append(f"exec_attempts: {attempt}/{max_attempts}")
        if reason:
            lines.append("exec_reason: " + reason[:240])
        if at:
            lines.append("exec_critic_at: " + at)
        rerun_exec = [str(x).strip() for x in (exec_critic.get("rerun_execution_lane_ids") or []) if str(x).strip()]
        rerun_review = [str(x).strip() for x in (exec_critic.get("rerun_review_lane_ids") or []) if str(x).strip()]
        manual_exec = [str(x).strip() for x in (exec_critic.get("manual_followup_execution_lane_ids") or []) if str(x).strip()]
        manual_review = [str(x).strip() for x in (exec_critic.get("manual_followup_review_lane_ids") or []) if str(x).strip()]
        if rerun_exec or rerun_review:
            lines.append(
                "exec_rerun_targets: execution={exec} review={review}".format(
                    exec=", ".join(rerun_exec) if rerun_exec else "-",
                    review=", ".join(rerun_review) if rerun_review else "-",
                )
            )
        if manual_exec or manual_review:
            lines.append(
                "exec_manual_followup_targets: execution={exec} review={review}".format(
                    exec=", ".join(manual_exec) if manual_exec else "-",
                    review=", ".join(manual_review) if manual_review else "-",
                )
            )
    followup_brief_status = str(task.get("followup_brief_status", "")).strip()
    if followup_brief_status:
        lines.append("followup_brief: " + followup_brief_status[:64])
        followup_brief_summary = str(task.get("followup_brief_summary", "")).strip()
        if followup_brief_summary:
            lines.append("followup_brief_summary: " + followup_brief_summary[:240])
        followup_brief_reason = str(task.get("followup_brief_reason", "")).strip()
        if followup_brief_reason:
            lines.append("followup_brief_reason: " + followup_brief_reason[:240])

    result = task.get("result")
    if isinstance(result, dict):
        lines.append(
            "summary: assignments={a} replies={r} complete={c}".format(
                a=int(result.get("assignments", 0) or 0),
                r=int(result.get("replies", 0) or 0),
                c="yes" if bool(result.get("complete", False)) else "no",
            )
        )
        phase2_request_ids = result.get("phase2_request_ids") if isinstance(result.get("phase2_request_ids"), dict) else {}
        if phase2_request_ids:
            def _render_phase2_request_bucket(value: Any) -> str:
                if isinstance(value, list):
                    tokens = [str(item).strip() for item in value if str(item).strip()]
                    return ", ".join(tokens) if tokens else "-"
                token = str(value).strip()
                return token or "-"

            exec_req = _render_phase2_request_bucket(phase2_request_ids.get("execution"))
            review_req = _render_phase2_request_bucket(phase2_request_ids.get("review"))
            lines.append(f"phase2_requests: execution={exec_req} review={review_req}")
        if "phase2_review_triggered" in result:
            lines.append("phase2_review_triggered: " + ("yes" if bool(result.get("phase2_review_triggered")) else "no"))
        review_skip_reason = str(result.get("phase2_review_skipped_reason", "")).strip()
        if review_skip_reason:
            lines.append("phase2_review_skip_reason: " + review_skip_reason[:240])
        failed = result.get("failed_roles") or []
        pending = result.get("pending_roles") or []
        requested_roles = [str(x).strip() for x in (result.get("requested_roles") or []) if str(x).strip()]
        executed_roles = [str(x).strip() for x in (result.get("executed_roles") or []) if str(x).strip()]
        dropped_roles = [str(x).strip() for x in (result.get("dropped_roles") or []) if str(x).strip()]
        added_roles = [str(x).strip() for x in (result.get("added_roles") or []) if str(x).strip()]
        if requested_roles:
            lines.append("requested_roles: " + ", ".join(requested_roles))
        if executed_roles:
            lines.append("executed_roles: " + ", ".join(executed_roles))
        if bool(result.get("role_mismatch", False)):
            lines.append(
                "role_mismatch: dropped={dropped} added={added}".format(
                    dropped=", ".join(dropped_roles) if dropped_roles else "-",
                    added=", ".join(added_roles) if added_roles else "-",
                )
            )
        backend = str(result.get("backend", "")).strip()
        if backend:
            backend_parts = [backend]
            profile = str(result.get("backend_profile", "")).strip()
            if profile:
                backend_parts.append(profile)
            verdict = str(result.get("backend_verdict", "")).strip()
            if verdict:
                backend_parts.append("verdict=" + verdict)
            contract = str(result.get("backend_contract", "")).strip()
            if contract:
                backend_parts.append("contract=" + contract)
            lines.append("backend: " + " | ".join(backend_parts))
            contract_note = str(result.get("backend_contract_note", "")).strip()
            if contract_note:
                lines.append("backend_contract_note: " + contract_note[:240])
        if failed:
            lines.append("failed_roles: " + ", ".join(str(x) for x in failed))
        if pending:
            lines.append("pending_roles: " + ", ".join(str(x) for x in pending))

    history = task.get("history") or []
    if isinstance(history, list) and history:
        lines.append("recent:")
        for ev in history[-6:]:
            if not isinstance(ev, dict):
                continue
            stage = str(ev.get("stage", "")).strip() or "-"
            status = str(ev.get("status", "")).strip() or "-"
            at = str(ev.get("at", "")).strip() or "-"
            note = str(ev.get("note", "")).strip()
            suffix = f" | {note}" if note else ""
            lines.append(f"- {at} {stage}={status}{suffix}")

    return "\n".join(lines)
