#!/usr/bin/env python3
"""Operator preference dashboard mutation actions."""

from __future__ import annotations

from typing import Any, Dict, Tuple

import aoe_tg_operator_preferences as operator_preferences
from aoe_tg_orch_task_handlers import _project_alias, _runtime_action_link

from control_dashboard_action_exec_shared import _load_dashboard_manager_state, _json
from control_dashboard_action_exec_runtime import (
    _build_operator_preference_surface,
    _build_preference_refresh_diff_summary,
    _json_with_dashboard_audit,
    _load_task_operator_preference_session_rules,
    _now_iso,
    _operator_preferences_project_alias_for_ref,
    _operator_preferences_team_dir_for_ref,
    _parse_json_value,
    _preference_management_return_path,
    _preference_memory_scope_summary,
    _preference_text,
    _record_task_operator_preference_decision,
    _resolve_task_entry,
    _save_manager_state,
    _store_task_operator_preference_session_rule,
    _worker_preview_refresh_action,
    _worker_update_stub_for_task,
)
from control_dashboard_common import DashboardAppConfig


def _execute_operator_preference_decision_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = _preference_text(payload.get("task_ref"), 64)
    return_path = _preference_text(payload.get("return_path"), 160) or "/control/tasks"
    artifact_kind = _preference_text(payload.get("artifact_kind"), 64).lower()
    _paths, manager_state = _load_dashboard_manager_state(config)
    try:
        key, entry, request_id, task = _resolve_task_entry(manager_state=manager_state, task_ref=task_ref)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry the operator preference decision with an existing task ref",
                "outcome": {
                    "kind": "operator_preference_decision",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    decision = operator_preferences.normalize_preference_decision(
        {
            "artifact_kind": payload.get("artifact_kind"),
            "key": payload.get("key"),
            "value": payload.get("value"),
            "description": payload.get("description"),
            "choice": payload.get("choice"),
            "scope": payload.get("scope") or "artifact_kind",
            "scope_ref": payload.get("scope_ref"),
            "decided_at": _now_iso(),
        }
    )
    if not decision:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "provide a concrete preference key, artifact_kind, and choice before recording the decision",
                "outcome": {
                    "kind": "operator_preference_decision",
                    "status": "blocked",
                    "reason_code": "decision_invalid",
                    "detail": "preference decision payload is incomplete",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=400,
        )
    project_team_dir = _preference_text(entry.get("team_dir"), 512)
    if not project_team_dir:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": return_path,
                "remediation": "restore the project-local .aoe-team path before recording adaptive preference decisions",
                "outcome": {
                    "kind": "operator_preference_decision",
                    "status": "blocked",
                    "reason_code": "team_dir_missing",
                    "detail": "project team_dir is missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    artifact_kind = _preference_text(decision.get("artifact_kind"), 64).lower()
    before_registry_state = operator_preferences.load_operator_preferences(project_team_dir)
    before_candidate_state = operator_preferences.load_operator_preference_candidates(project_team_dir)
    before_session_rules = _load_task_operator_preference_session_rules(task)
    before_preference_state = {
        "rules": [*list(before_registry_state.get("rules") or []), *before_session_rules],
    }
    apply_result = operator_preferences.apply_preference_decision(
        project_team_dir,
        decision=decision,
        now_iso=_preference_text(decision.get("decided_at"), 64) or _now_iso(),
    )
    request_override = apply_result.get("request_override") if isinstance(apply_result.get("request_override"), dict) else {}
    if request_override:
        _store_task_operator_preference_session_rule(task, request_override)
    if _preference_text(decision.get("choice"), 32).lower() in {"apply_once", "skip_once"}:
        operator_preferences.record_preference_candidate(
            project_team_dir,
            artifact_kind=decision.get("artifact_kind"),
            key=decision.get("key"),
            suggested_value=decision.get("value"),
            issue=decision.get("description"),
            project_ref=alias,
            source_ref=request_id,
            suggested_prompt_mode="confirm",
            now_iso=_preference_text(decision.get("decided_at"), 64) or _now_iso(),
        )
    decisions = _record_task_operator_preference_decision(task, decision)
    task["updated_at"] = _now_iso()
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_operator_preference_decision_summary"] = str(apply_result.get("decision_summary", "")).strip() or "-"
    after_registry_state = operator_preferences.load_operator_preferences(project_team_dir)
    after_candidate_state = operator_preferences.load_operator_preference_candidates(project_team_dir)
    after_preference_state = {
        "rules": [*list(after_registry_state.get("rules") or []), *_load_task_operator_preference_session_rules(task)],
    }
    preference_refresh_diff_summary = _build_preference_refresh_diff_summary(
        before_preference_state=before_preference_state,
        after_preference_state=after_preference_state,
        before_candidate_state=before_candidate_state,
        after_candidate_state=after_candidate_state,
        artifact_kind=artifact_kind,
        project_ref=alias,
    )
    _save_manager_state(config, manager_state)
    update_stub = _worker_update_stub_for_task(task)
    preference_surface = _build_operator_preference_surface(
        entry=entry,
        alias=alias,
        task_ref=label,
        task=task,
        update_stub=update_stub,
        return_path=return_path,
    )
    _save_manager_state(config, manager_state)
    reopen_action = _worker_preview_refresh_action(label)
    followup_actions = ([reopen_action] if reopen_action else []) + list(preference_surface.get("decision_actions") or [])
    decision_summary = str(apply_result.get("decision_summary", "")).strip() or operator_preferences.summarize_preference_decision(decision)
    return _json_with_dashboard_audit(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "project_alias": alias or "-",
            "focus_badge": "preferences",
            "next_step": return_path,
            "remediation": "reopen the current preview to verify the updated applied preferences before continuing the artifact workflow",
            "outcome": {
                "kind": "operator_preference_decision",
                "status": "executed",
                "reason_code": "registry_updated" if bool(apply_result.get("persisted")) else "session_override_recorded",
                "detail": decision_summary or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "refresh_action": reopen_action or {},
            "actions": followup_actions,
            "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
            "applied_preferences": list(preference_surface.get("applied_preferences") or []),
            "preference_candidates": list(preference_surface.get("candidate_preferences") or []),
            "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
            "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
            "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
            "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
            "preference_decisions": decisions,
            "preference_decision_summary": operator_preferences.summarize_preference_decisions(decisions),
            "preference_artifact_kind": str(preference_surface.get("artifact_kind", "")).strip() or "-",
            "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
            "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
            "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
            "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
            "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
            "preference_refresh_diff_summary": preference_refresh_diff_summary,
            "preview": {
                "kind": "operator_preference_decision",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "return_path": return_path,
                "decision_summary": decision_summary or "-",
                "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
                "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
                "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
                "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
                "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
                "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
                "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
                "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
                "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
                "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
                "preference_refresh_diff_summary": preference_refresh_diff_summary,
            },
        },
        config=config,
        status=200,
    )


def _execute_operator_preference_rule_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    paths, manager_state = _load_dashboard_manager_state(config)
    operator_team_dir = _operator_preferences_team_dir_for_ref(paths, manager_state, payload.get("runtime_ref"))
    project_alias = _operator_preferences_project_alias_for_ref(manager_state, payload.get("runtime_ref"))
    return_path = _preference_management_return_path(payload.get("return_path"))
    task_ref = _preference_text(payload.get("task_ref"), 64)
    mode = _preference_text(payload.get("mode"), 32).lower()
    artifact_kind = _preference_text(payload.get("artifact_kind"), 64).lower()
    key = _preference_text(payload.get("key"), 96).lower()
    scope = _preference_text(payload.get("scope"), 32).lower() or "artifact_kind"
    scope_ref = payload.get("scope_ref")
    if not artifact_kind or not key:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "next_step": return_path,
                "remediation": "provide artifact_kind and key before mutating a preference rule",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_rule",
                    "status": "blocked",
                    "reason_code": "rule_invalid",
                    "detail": "missing artifact_kind or key",
                },
                "preview": {"detail_path": return_path},
            },
            status=400,
        )
    before_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
    before_candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
    before_preference_state = {"rules": list(before_registry_state.get("rules") or [])}
    if mode == "delete":
        removed = operator_preferences.delete_operator_preference_rule(
            operator_team_dir,
            key=key,
            artifact_kind=artifact_kind,
            scope=scope,
            scope_ref=scope_ref,
        )
        if not removed:
            return _json(
                {
                    "ok": False,
                    "implemented": True,
                    "executed": False,
                    "status": "blocked",
                    "method": "POST",
                    "path": str(spec.get("path", "")).strip() or "-",
                    "mode": str(spec.get("mode", "")).strip() or "safe",
                    "source_command": str(spec.get("command", "")).strip() or "-",
                    "payload": payload,
                    "project_alias": project_alias or "-",
                    "preference_artifact_kind": artifact_kind or "-",
                    "next_step": return_path,
                    "remediation": "refresh the preference registry and retry against an existing rule row",
                    "focus_badge": "preferences",
                    "outcome": {
                        "kind": "operator_preference_rule",
                        "status": "blocked",
                        "reason_code": "rule_missing",
                        "detail": f"rule not found: {artifact_kind}:{scope}:{key}",
                    },
                    "preview": {"detail_path": return_path},
                },
                status=404,
            )
        detail = operator_preferences.summarize_preference_rule(removed)
        removed_scope = _preference_text(removed.get("scope"), 32).lower() or scope
        removed_scope_ref = _preference_text(removed.get("scope_ref"), 64)
        after_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
        after_candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
        preference_refresh_diff_summary = _build_preference_refresh_diff_summary(
            before_preference_state=before_preference_state,
            after_preference_state={"rules": list(after_registry_state.get("rules") or [])},
            before_candidate_state=before_candidate_state,
            after_candidate_state=after_candidate_state,
            artifact_kind=artifact_kind,
            project_ref=project_alias or (removed_scope_ref if removed_scope == "project" else ""),
        )
        preference_memory_scope_summary = _preference_memory_scope_summary(removed_scope, removed_scope_ref)
        return _json_with_dashboard_audit(
            {
                "ok": True,
                "implemented": True,
                "executed": True,
                "status": "executed",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "review the remaining rules and candidates to keep the adaptive registry tight",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_rule",
                    "status": "executed",
                    "reason_code": "rule_deleted",
                    "detail": detail or "-",
                },
                "preference_decision_summary": "preference_decisions=registry rule removed",
                "preference_memory_scope_summary": preference_memory_scope_summary,
                "preference_refresh_diff_summary": preference_refresh_diff_summary,
                "preview": {"detail_path": return_path},
            },
            config=config,
            status=200,
        )
    if mode not in {"auto", "confirm", "manual_only", "disable"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "use one of auto, confirm, manual_only, disable, or delete for rule actions",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_rule",
                    "status": "blocked",
                    "reason_code": "mode_invalid",
                    "detail": mode or "-",
                },
                "preview": {"detail_path": return_path},
            },
            status=400,
        )
    existing = next(
        (
            operator_preferences.normalize_preference_rule(item)
            for item in list(before_registry_state.get("rules") or [])
            if operator_preferences.normalize_preference_rule(item)
            and _preference_text(item.get("key"), 96).lower() == key
            and _preference_text(item.get("artifact_kind"), 64).lower() == artifact_kind
            and _preference_text(item.get("scope"), 32).lower() == scope
            and _preference_text(item.get("scope_ref"), 64)
            == operator_preferences.normalize_preference_rule(
                {
                    "artifact_kind": artifact_kind,
                    "key": key,
                    "scope": scope,
                    "scope_ref": scope_ref,
                }
            ).get("scope_ref", "")
        ),
        {},
    )
    value = _parse_json_value(payload.get("value_json"))
    if value is None and existing:
        value = existing.get("value")
    description = _preference_text(payload.get("description"), 240) or _preference_text(existing.get("description"), 240) or key
    updated = operator_preferences.upsert_operator_preference_rule(
        operator_team_dir,
        key=key,
        artifact_kind=artifact_kind,
        scope=scope,
        scope_ref=scope_ref,
        value=value,
        description=description,
        enabled=bool(mode != "disable"),
        prompt_mode="confirm" if mode == "disable" else mode,
        source="explicit_user",
        confidence=_preference_text(existing.get("confidence"), 32) or "explicit",
        promotion_reason=_preference_text(existing.get("promotion_reason"), 240),
        now_iso=_now_iso(),
    )
    detail = operator_preferences.summarize_preference_rule(updated)
    updated_scope = _preference_text(updated.get("scope"), 32).lower() or scope
    updated_scope_ref = _preference_text(updated.get("scope_ref"), 64)
    after_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
    after_candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
    preference_refresh_diff_summary = _build_preference_refresh_diff_summary(
        before_preference_state=before_preference_state,
        after_preference_state={"rules": list(after_registry_state.get("rules") or [])},
        before_candidate_state=before_candidate_state,
        after_candidate_state=after_candidate_state,
        artifact_kind=artifact_kind,
        project_ref=project_alias or (updated_scope_ref if updated_scope == "project" else ""),
    )
    preference_memory_scope_summary = _preference_memory_scope_summary(updated_scope, updated_scope_ref)
    return _json_with_dashboard_audit(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "project_alias": project_alias or "-",
            "preference_artifact_kind": artifact_kind or "-",
            "next_step": return_path,
            "remediation": "re-run a worker preview to see how the updated prompt mode changes the adaptive preflight",
            "focus_badge": "preferences",
            "outcome": {
                "kind": "operator_preference_rule",
                "status": "executed",
                "reason_code": "rule_updated",
                "detail": detail or "-",
            },
            "applied_preferences_summary": f"applied_preferences={detail}" if detail and detail != "-" else "-",
            "preference_decision_summary": "preference_decisions=registry rule updated",
            "preference_memory_scope_summary": preference_memory_scope_summary,
            "preference_refresh_diff_summary": preference_refresh_diff_summary,
            "preview": {"detail_path": return_path},
        },
        config=config,
        status=200,
    )


def _execute_operator_preference_candidate_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    paths, manager_state = _load_dashboard_manager_state(config)
    operator_team_dir = _operator_preferences_team_dir_for_ref(paths, manager_state, payload.get("runtime_ref"))
    project_alias = _operator_preferences_project_alias_for_ref(manager_state, payload.get("runtime_ref"))
    return_path = _preference_management_return_path(payload.get("return_path"))
    task_ref = _preference_text(payload.get("task_ref"), 64)
    mode = _preference_text(payload.get("mode"), 32).lower()
    artifact_kind = _preference_text(payload.get("artifact_kind"), 64).lower()
    key = _preference_text(payload.get("key"), 96).lower()
    project_ref = _preference_text(payload.get("project_ref"), 64)
    before_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
    if not artifact_kind or not key:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "provide artifact_kind and key before mutating a preference candidate",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_candidate",
                    "status": "blocked",
                    "reason_code": "candidate_invalid",
                    "detail": "missing artifact_kind or key",
                },
                "preview": {"detail_path": return_path},
            },
            status=400,
        )
    candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
    before_candidate_state = candidate_state
    before_preference_state = {"rules": list(before_registry_state.get("rules") or [])}
    candidate = next(
        (
            operator_preferences.normalize_preference_candidate(item)
            for item in list(candidate_state.get("candidates") or [])
            if operator_preferences.normalize_preference_candidate(item)
            and _preference_text(item.get("artifact_kind"), 64).lower() == artifact_kind
            and _preference_text(item.get("key"), 96).lower() == key
            and _preference_text(item.get("project_ref"), 64) == project_ref
        ),
        {},
    )
    if not candidate:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "refresh the candidate queue and retry against an existing candidate row",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_candidate",
                    "status": "blocked",
                    "reason_code": "candidate_missing",
                    "detail": f"candidate not found: {artifact_kind}:{project_ref or '*'}:{key}",
                },
                "preview": {"detail_path": return_path},
            },
            status=404,
        )
    candidate_summary = operator_preferences.summarize_preference_candidate(candidate)
    candidate_scope, candidate_scope_ref = operator_preferences.preference_candidate_scope(
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )
    candidate_memory_scope_summary = _preference_memory_scope_summary(candidate_scope, candidate_scope_ref)
    reopen_action = _worker_preview_refresh_action(task_ref)
    candidate_scope_summary = operator_preferences.summarize_preference_candidate_scopes(
        [
            {
                "artifact_kind": artifact_kind,
                "key": key,
                "expected_scope": candidate_scope,
                "expected_scope_ref": candidate_scope_ref,
            }
        ]
    )
    if mode == "dismiss":
        operator_preferences.delete_operator_preference_candidate(
            operator_team_dir,
            artifact_kind=artifact_kind,
            key=key,
            project_ref=project_ref,
        )
        after_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
        after_candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
        preference_refresh_diff_summary = _build_preference_refresh_diff_summary(
            before_preference_state=before_preference_state,
            after_preference_state={"rules": list(after_registry_state.get("rules") or [])},
            before_candidate_state=before_candidate_state,
            after_candidate_state=after_candidate_state,
            artifact_kind=artifact_kind,
            project_ref=project_alias or project_ref,
        )
        return _json_with_dashboard_audit(
            {
                "ok": True,
                "implemented": True,
                "executed": True,
                "status": "executed",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "watch future previews to see if this preference pattern reappears before promoting a new rule",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_candidate",
                    "status": "executed",
                    "reason_code": "candidate_dismissed",
                    "detail": candidate_summary or "-",
                },
                "preference_candidate_summary": candidate_summary or "-",
                "preference_candidate_scope_summary": candidate_scope_summary or "-",
                "preference_memory_scope_summary": candidate_memory_scope_summary,
                "preference_refresh_diff_summary": preference_refresh_diff_summary,
                "refresh_action": reopen_action or {},
                "actions": [reopen_action] if reopen_action else [],
                "preview": {"detail_path": return_path},
            },
            config=config,
            status=200,
        )
    if mode not in {"auto", "confirm", "disable"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "project_alias": project_alias or "-",
                "preference_artifact_kind": artifact_kind or "-",
                "next_step": return_path,
                "remediation": "use auto, confirm, disable, or dismiss for candidate actions",
                "focus_badge": "preferences",
                "outcome": {
                    "kind": "operator_preference_candidate",
                    "status": "blocked",
                    "reason_code": "mode_invalid",
                    "detail": mode or "-",
                },
                "preview": {"detail_path": return_path},
            },
            status=400,
        )
    operator_preferences.delete_operator_preference_candidate(
        operator_team_dir,
        artifact_kind=artifact_kind,
        key=key,
        project_ref=project_ref,
    )
    updated = operator_preferences.upsert_operator_preference_rule(
        operator_team_dir,
        key=key,
        artifact_kind=artifact_kind,
        scope=candidate_scope,
        scope_ref=candidate_scope_ref,
        value=_parse_json_value(payload.get("value_json")),
        description=_preference_text(payload.get("description"), 240) or _preference_text(candidate.get("issue"), 240) or key,
        enabled=bool(mode != "disable"),
        prompt_mode="confirm" if mode == "disable" else mode,
        source="operator_promoted",
        confidence="repeated",
        promotion_reason=_preference_text(candidate.get("issue"), 240),
        now_iso=_now_iso(),
    )
    after_registry_state = operator_preferences.load_operator_preferences(operator_team_dir)
    after_candidate_state = operator_preferences.load_operator_preference_candidates(operator_team_dir)
    preference_refresh_diff_summary = _build_preference_refresh_diff_summary(
        before_preference_state=before_preference_state,
        after_preference_state={"rules": list(after_registry_state.get("rules") or [])},
        before_candidate_state=before_candidate_state,
        after_candidate_state=after_candidate_state,
        artifact_kind=artifact_kind,
        project_ref=project_alias or project_ref,
    )
    detail = operator_preferences.summarize_preference_rule(updated)
    return _json_with_dashboard_audit(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "project_alias": project_alias or "-",
            "preference_artifact_kind": artifact_kind or "-",
            "next_step": return_path,
            "remediation": "re-run a worker preview for the matching artifact kind to validate the promoted rule behavior",
            "focus_badge": "preferences",
            "outcome": {
                "kind": "operator_preference_candidate",
                "status": "executed",
                "reason_code": "candidate_promoted",
                "detail": detail or "-",
            },
            "applied_preferences_summary": f"applied_preferences={detail}" if detail and detail != "-" else "-",
            "preference_candidate_summary": candidate_summary or "-",
            "preference_candidate_scope_summary": candidate_scope_summary or "-",
            "preference_memory_scope_summary": candidate_memory_scope_summary,
            "preference_decision_summary": f"preference_decisions={detail}" if detail and detail != "-" else "-",
            "preference_refresh_diff_summary": preference_refresh_diff_summary,
            "refresh_action": reopen_action or {},
            "actions": [reopen_action] if reopen_action else [],
            "preview": {"detail_path": return_path},
        },
        config=config,
        status=200,
    )
