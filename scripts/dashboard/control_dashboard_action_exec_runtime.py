#!/usr/bin/env python3
"""Runtime-scoped dashboard mutation and invoke helpers."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import quote, urlparse

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_model_provider_adapter as model_provider_adapter
import aoe_tg_operator_preferences as operator_preferences
import aoe_tg_todo_state as todo_state
import aoe_tg_worker_task_contract as worker_task_contract
import aoe_tg_harness_authoring_adapter as harness_authoring_adapter
from aoe_tg_action_audit import append_action_audit_row
from aoe_tg_orch_task_handlers import (
    _OFFDESK_JUDGE_SYSTEM,
    _offdesk_judge_decision_snapshot,
    _offdesk_judge_prompt,
    _project_alias,
    _runtime_action_link,
)
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view

from control_dashboard_audit import _append_action_audit as _append_dashboard_action_audit
from control_dashboard_action_exec_shared import (
    _DASHBOARD_CHAT_ID,
    _load_gateway_main_module,
    _load_dashboard_manager_state,
    _json,
)
from control_dashboard_action_exec_feedback import (
    persist_canonical_writeback_state,
    persist_manual_step_execution_state,
)
from control_dashboard_common import DashboardAppConfig


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _parse_json_value(raw: Any) -> Any:
    if isinstance(raw, (dict, list, bool, int, float)) or raw is None:
        return raw
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _preference_text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[: max(0, int(limit or 0))]


def _preference_memory_scope_summary(scope: Any, scope_ref: Any = "") -> str:
    scope_token = _preference_text(scope, 32).lower() or "session"
    scope_ref_token = _preference_text(scope_ref, 64)
    if scope_token == "session":
        return "preference_memory_scope=session"
    if scope_ref_token and scope_ref_token not in {"-", "*"}:
        return f"preference_memory_scope={scope_token}:{scope_ref_token}"
    return f"preference_memory_scope={scope_token}"


def _preference_memory_scope_label(scope: Any) -> str:
    scope_token = _preference_text(scope, 32).lower()
    if scope_token == "session":
        return "this task"
    if scope_token == "project":
        return "this project"
    if scope_token == "artifact_kind":
        return "this artifact kind"
    if scope_token == "user_global":
        return "all projects"
    return scope_token or "-"


def _preference_candidate_query(
    *,
    artifact_kind: Any,
    expected_scope: Any,
    key: Any,
) -> str:
    tokens = []
    artifact_token = _preference_text(artifact_kind, 64).lower()
    if artifact_token:
        tokens.append(f"artifact_kind:{artifact_token}")
    scope_token = _preference_text(expected_scope, 32).lower()
    if scope_token:
        tokens.append(f"memory_scope:{scope_token}")
    key_token = _preference_text(key, 96).lower()
    if key_token:
        tokens.append(key_token)
    return " ".join(token for token in tokens if token)


def _preference_candidate_drilldown_links(
    *,
    project_alias: Any,
    artifact_kind: Any,
    expected_scope: Any,
    key: Any,
) -> Dict[str, str]:
    project_token = _preference_text(project_alias, 64).upper()
    query = _preference_candidate_query(
        artifact_kind=artifact_kind,
        expected_scope=expected_scope,
        key=key,
    )
    encoded_query = quote(query, safe="") if query else ""
    audit_href = "/control/audit?focus=preferences"
    if project_token:
        audit_href += f"&project={quote(project_token, safe='')}"
    if encoded_query:
        audit_href += f"&q={encoded_query}"
    audit_href += "&limit=50"
    history_href = "/control/history"
    if encoded_query:
        history_href += f"?q={encoded_query}"
    else:
        history_href += "?"
    if project_token:
        history_href += f"{'&' if '?' in history_href and not history_href.endswith('?') else ''}project={quote(project_token, safe='')}"
    history_href += f"{'&' if '?' in history_href and not history_href.endswith('?') else ''}scope=dashboard&limit=20"
    return {
        "audit_href": audit_href,
        "history_href": history_href,
    }


def _preference_candidate_management_return_path(
    *,
    project_alias: Any,
    artifact_kind: Any,
    expected_scope: Any,
) -> str:
    params = []
    project_token = _preference_text(project_alias, 64).upper()
    if project_token:
        params.append(f"project={quote(project_token, safe='')}")
    artifact_token = _preference_text(artifact_kind, 64).lower()
    if artifact_token:
        params.append(f"artifact={quote(artifact_token, safe='')}")
    scope_token = _preference_text(expected_scope, 32).lower()
    if scope_token:
        params.append(f"scope={quote(scope_token, safe='')}")
    return "/control/preferences" + (f"?{'&'.join(params)}" if params else "")


def _preference_value_signature(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return _preference_text(value, 240)


def _effective_applied_preferences(
    preference_state: Any,
    *,
    artifact_kind: Any,
    project_ref: Any = "",
) -> list[Dict[str, Any]]:
    applicable = operator_preferences.list_applicable_preferences(
        preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
        include_disabled=True,
    )
    return [
        row
        for row in applicable
        if bool(row.get("enabled", False))
        and (
            _preference_text(row.get("prompt_mode"), 32).lower() == "auto"
            or _preference_text(row.get("scope"), 32).lower() == "session"
        )
    ]


def _effective_preference_candidates(
    candidate_state: Any,
    *,
    preference_state: Any,
    artifact_kind: Any,
    project_ref: Any = "",
) -> list[Dict[str, Any]]:
    return operator_preferences.build_preference_candidate_recommendations(
        candidate_state,
        preference_state=preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )


def _preference_refresh_row_signature(row: Dict[str, Any], *, kind: str) -> str:
    artifact = _preference_text(row.get("artifact_kind"), 64).lower()
    key = _preference_text(row.get("key"), 96).lower()
    if kind == "applied":
        return "|".join(
            [
                artifact,
                key,
                _preference_text(row.get("scope"), 32).lower(),
                _preference_text(row.get("scope_ref"), 64),
                _preference_text(row.get("prompt_mode"), 32).lower(),
                str(bool(row.get("enabled", False))).lower(),
                _preference_value_signature(row.get("value")),
            ]
        )
    return "|".join(
        [
            artifact,
            key,
            _preference_text(row.get("expected_scope") or row.get("scope"), 32).lower(),
            _preference_text(row.get("expected_scope_ref") or row.get("scope_ref"), 64),
            _preference_value_signature(row.get("suggested_value", row.get("value"))),
        ]
    )


def _preference_refresh_delta(
    before_rows: list[Dict[str, Any]],
    after_rows: list[Dict[str, Any]],
    *,
    kind: str,
) -> tuple[list[str], list[str]]:
    summarize = (
        operator_preferences.summarize_preference_rule
        if kind == "applied"
        else operator_preferences.summarize_preference_candidate
    )
    before_map = {
        _preference_refresh_row_signature(row, kind=kind): summarize(row)
        for row in before_rows
        if _preference_refresh_row_signature(row, kind=kind)
    }
    after_map = {
        _preference_refresh_row_signature(row, kind=kind): summarize(row)
        for row in after_rows
        if _preference_refresh_row_signature(row, kind=kind)
    }
    added = [
        str(summary).strip()
        for signature, summary in after_map.items()
        if signature not in before_map and str(summary).strip() not in {"", "-"}
    ]
    removed = [
        str(summary).strip()
        for signature, summary in before_map.items()
        if signature not in after_map and str(summary).strip() not in {"", "-"}
    ]
    return added, removed


def _summarize_preference_refresh_diff_sections(sections: Dict[str, list[str]]) -> str:
    labels = []
    for key in ("applied_added", "applied_removed", "candidates_added", "candidates_removed"):
        rows = [str(item).strip() for item in list(sections.get(key) or []) if str(item).strip() not in {"", "-"}]
        if not rows:
            continue
        visible = rows[:2]
        summary = f"{key}={' || '.join(visible)}"
        if len(rows) > len(visible):
            summary += f" | total={len(rows)}"
        labels.append(summary)
    return f"preference_refresh_diff={' ; '.join(labels)}" if labels else "-"


def _build_preference_refresh_diff_summary(
    *,
    before_preference_state: Any,
    after_preference_state: Any,
    before_candidate_state: Any,
    after_candidate_state: Any,
    artifact_kind: Any,
    project_ref: Any = "",
) -> str:
    before_applied = _effective_applied_preferences(
        before_preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )
    after_applied = _effective_applied_preferences(
        after_preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )
    before_candidates = _effective_preference_candidates(
        before_candidate_state,
        preference_state=before_preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )
    after_candidates = _effective_preference_candidates(
        after_candidate_state,
        preference_state=after_preference_state,
        artifact_kind=artifact_kind,
        project_ref=project_ref,
    )
    applied_added, applied_removed = _preference_refresh_delta(before_applied, after_applied, kind="applied")
    candidates_added, candidates_removed = _preference_refresh_delta(before_candidates, after_candidates, kind="candidate")
    return _summarize_preference_refresh_diff_sections(
        {
            "applied_added": applied_added,
            "applied_removed": applied_removed,
            "candidates_added": candidates_added,
            "candidates_removed": candidates_removed,
        }
    )


def _json_with_dashboard_audit(
    payload: Dict[str, Any],
    *,
    config: DashboardAppConfig,
    status: int,
) -> Tuple[int, Dict[str, str], bytes]:
    try:
        _append_dashboard_action_audit(config, payload)
    except Exception:
        pass
    response_payload = dict(payload)
    response_payload["audit_recorded"] = True
    return _json(response_payload, status=status)


def _preference_candidate_action_rows(
    *,
    task_ref: Any,
    runtime_ref: Any,
    project_ref: Any,
    artifact_kind: Any,
    key: Any,
    suggested_value: Any,
    description: Any,
    return_path: Any,
) -> list[Dict[str, Any]]:
    runtime_token = _preference_text(runtime_ref, 64)
    project_token = _preference_text(project_ref, 64)
    artifact_token = _preference_text(artifact_kind, 64).lower()
    key_token = _preference_text(key, 96).lower()
    description_token = _preference_text(description, 240)
    payload_value = json.dumps(suggested_value, ensure_ascii=False)
    rows = []
    for mode, label in (
        ("auto", "promote auto"),
        ("confirm", "promote confirm"),
        ("disable", "mute"),
        ("dismiss", "dismiss"),
    ):
        payload = {
            "task_ref": _preference_text(task_ref, 64),
            "runtime_ref": runtime_token,
            "return_path": _preference_text(return_path, 240),
            "project_ref": project_token,
            "artifact_kind": artifact_token,
            "key": key_token,
            "value_json": payload_value,
            "description": description_token,
            "mode": mode,
        }
        rows.append(
            {
                "label": label,
                "path": "/control/actions/control/operator-preference-candidate",
                "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "mode": "safe",
                "priority": "primary" if mode in {"auto", "confirm"} else "secondary",
                "command": f"/prefs candidate {artifact_token}:{key_token} {mode}",
                "note": "promote, mute, or dismiss this adaptive preference candidate",
            }
        )
    return rows


def _worker_preview_refresh_action(task_ref: Any) -> Dict[str, Any]:
    task_token = _preference_text(task_ref, 64)
    if not task_token:
        return {}
    return {
        "label": "Reopen Preview",
        "path": "/control/actions/task/worker-apply-preview",
        "payload_json": json.dumps({"task_ref": task_token}, ensure_ascii=False, separators=(",", ":")),
        "mode": "safe",
        "priority": "secondary",
        "command": f"/task {task_token} | reopen-preview",
        "note": "refresh the current preview with the updated preference state",
    }


def _preference_management_return_path(value: Any) -> str:
    text = _preference_text(value, 240)
    if not text:
        return "/control/preferences"
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        return "/control/preferences"
    path = str(parsed.path or "").strip() or "/control/preferences"
    if path != "/control/preferences":
        return "/control/preferences"
    return f"{path}?{parsed.query}" if parsed.query else path


def _same_preference_subject(raw: Any, *, artifact_kind: str, key: str) -> bool:
    if not isinstance(raw, dict):
        return False
    return (
        _preference_text(raw.get("artifact_kind"), 64).lower() == artifact_kind
        and _preference_text(raw.get("key"), 96).lower() == key
    )


def _load_task_operator_preference_session_rules(task: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_rules = task.get("background_run_operator_preference_session_rules")
    if not isinstance(raw_rules, list):
        return []
    rules: list[Dict[str, Any]] = []
    for item in raw_rules:
        row = operator_preferences.normalize_preference_rule(item)
        if row and not any(
            _same_preference_subject(existing, artifact_kind=_preference_text(row.get("artifact_kind"), 64).lower(), key=_preference_text(row.get("key"), 96).lower())
            for existing in rules
        ):
            rules.append(row)
    return rules


def _store_task_operator_preference_session_rule(task: Dict[str, Any], raw_rule: Any) -> list[Dict[str, Any]]:
    rule = operator_preferences.normalize_preference_rule(raw_rule)
    rules = _load_task_operator_preference_session_rules(task)
    if not rule:
        task["background_run_operator_preference_session_rules"] = rules
        return rules
    artifact_kind = _preference_text(rule.get("artifact_kind"), 64).lower()
    key = _preference_text(rule.get("key"), 96).lower()
    rules = [
        existing
        for existing in rules
        if not _same_preference_subject(existing, artifact_kind=artifact_kind, key=key)
    ]
    rules.append(rule)
    task["background_run_operator_preference_session_rules"] = rules[-8:]
    return list(task.get("background_run_operator_preference_session_rules") or [])


def _load_task_operator_preference_decisions(task: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_rows = task.get("background_run_operator_preference_decisions")
    if not isinstance(raw_rows, list):
        return []
    decisions: list[Dict[str, Any]] = []
    for item in raw_rows:
        row = operator_preferences.normalize_preference_decision(item)
        if row:
            decisions.append(row)
    return decisions


def _record_task_operator_preference_decision(task: Dict[str, Any], raw_decision: Any) -> list[Dict[str, Any]]:
    decision = operator_preferences.normalize_preference_decision(raw_decision)
    if not decision:
        return _load_task_operator_preference_decisions(task)
    artifact_kind = _preference_text(decision.get("artifact_kind"), 64).lower()
    key = _preference_text(decision.get("key"), 96).lower()
    decisions = [
        row
        for row in _load_task_operator_preference_decisions(task)
        if not _same_preference_subject(row, artifact_kind=artifact_kind, key=key)
    ]
    decisions.insert(0, decision)
    task["background_run_operator_preference_decisions"] = decisions[:8]
    return list(task.get("background_run_operator_preference_decisions") or [])


def _operator_preferences_team_dir_for_ref(paths: Any, manager_state: Dict[str, Any], project_ref: Any = "") -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    project_token = _preference_text(project_ref, 64).strip().lower()
    if project_token:
        for key, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            if project_token in {
                _preference_text(key, 64).lower(),
                _preference_text(entry.get("project_alias"), 64).lower(),
                _preference_text(entry.get("display_name"), 64).lower(),
                _preference_text(entry.get("name"), 64).lower(),
            }:
                resolved = _preference_text(entry.get("team_dir"), 512)
                if resolved:
                    return resolved
    active_key = _preference_text(manager_state.get("active"), 64)
    active_entry = projects.get(active_key) if active_key and isinstance(projects.get(active_key), dict) else {}
    return _preference_text(active_entry.get("team_dir"), 512) or _preference_text(getattr(paths, "team_dir", ""), 512)


def _operator_preferences_project_alias_for_ref(manager_state: Dict[str, Any], project_ref: Any = "") -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    project_token = _preference_text(project_ref, 64).strip().lower()
    if project_token:
        for key, entry in projects.items():
            if not isinstance(entry, dict):
                continue
            if project_token in {
                _preference_text(key, 64).lower(),
                _preference_text(entry.get("project_alias"), 64).lower(),
                _preference_text(entry.get("display_name"), 64).lower(),
                _preference_text(entry.get("name"), 64).lower(),
            }:
                return _preference_text(entry.get("project_alias"), 64).upper() or _preference_text(key, 64).upper()
    active_key = _preference_text(manager_state.get("active"), 64)
    active_entry = projects.get(active_key) if active_key and isinstance(projects.get(active_key), dict) else {}
    return _preference_text(active_entry.get("project_alias"), 64).upper() or active_key.upper()


def _derive_operator_preference_artifact_kind(*, task: Dict[str, Any], update_stub: Dict[str, Any]) -> str:
    explicit = _preference_text(task.get("background_run_operator_preference_artifact_kind"), 64).lower()
    if explicit:
        return explicit
    target_artifacts = [
        _preference_text(item, 240).lower()
        for item in list(update_stub.get("target_artifacts") or [])
        if _preference_text(item, 240)
    ]
    for token in target_artifacts:
        suffix = Path(token).suffix.lower()
        if any(marker in token for marker in ("chart", "plot", "graph", "figure")) or suffix in {
            ".png",
            ".svg",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        }:
            return "chart"
        if suffix in {".md", ".txt", ".doc", ".docx", ".pdf"}:
            return "document"
        if suffix in {".csv", ".tsv", ".xls", ".xlsx"}:
            return "spreadsheet"
    module_kind = _preference_text(task.get("background_run_task_contract_module"), 32).lower()
    return {
        "writing": "document",
        "package": "package",
        "analysis": "analysis",
        "general": "artifact",
    }.get(module_kind, "artifact")


def _derive_operator_preference_artifact_profile(
    *,
    task: Dict[str, Any],
    update_stub: Dict[str, Any],
    artifact_kind: str,
) -> str:
    explicit = _preference_text(task.get("background_run_operator_preference_artifact_profile"), 64).lower()
    if explicit:
        return explicit
    target_artifacts = [
        _preference_text(item, 240).lower()
        for item in list(update_stub.get("target_artifacts") or [])
        if _preference_text(item, 240)
    ]
    hint_fields = [
        *target_artifacts,
        _preference_text(task.get("prompt"), 240).lower(),
        _preference_text(task.get("job_contract_goal"), 240).lower(),
        _preference_text(task.get("job_contract_summary"), 240).lower(),
        _preference_text(task.get("background_run_worker_update_stub_summary"), 240).lower(),
    ]
    hints = " | ".join(token for token in hint_fields if token)
    if artifact_kind == "chart":
        if any(marker in hints for marker in ("bar", "histogram", "column")):
            return "chart_bar"
        if any(marker in hints for marker in ("line", "timeseries", "trend")):
            return "chart_line"
        if "scatter" in hints:
            return "chart_scatter"
    if artifact_kind == "document":
        if any(marker in hints for marker in ("brief", "one-pager", "summary")):
            return "document_brief"
        if "report" in hints:
            return "document_report"
        if any(marker in hints for marker in ("runbook", "guide", "playbook")):
            return "document_guide"
    if artifact_kind == "spreadsheet":
        if any(marker in hints for marker in ("model", "forecast", "projection")):
            return "spreadsheet_model"
        if any(marker in hints for marker in ("tracker", "inventory", "ledger", "backlog")):
            return "spreadsheet_tracker"
    return ""


def _summarize_operator_preference_bucket(prefix: str, rows: list[Dict[str, Any]]) -> str:
    labels = [_preference_text(row.get("summary"), 160) for row in rows if _preference_text(row.get("summary"), 160)]
    if not labels:
        return "-"
    return f"{prefix}=" + " || ".join(labels[:3])


def _operator_preference_decision_origin_label(origin: str) -> str:
    token = _preference_text(origin, 32).lower()
    if token == "candidate":
        return "repeated correction"
    if token == "confirm":
        return "remembered option"
    return "adaptive preference"


def _operator_preference_decision_scope_variants(
    *,
    choice: str,
    label: str,
    artifact_kind: str,
    project_ref: str,
) -> list[Dict[str, str]]:
    token = _preference_text(choice, 32).lower()
    clean_label = _preference_text(label, 64)
    artifact = _preference_text(artifact_kind, 64).lower() or "artifact"
    project = _preference_text(project_ref, 64)
    if token in {"apply_once", "skip_once"}:
        return [
            {
                "label": clean_label,
                "scope": "session",
                "scope_ref": "-",
                "memory_scope": "session",
                "memory_scope_label": "this task",
            }
        ]
    variants = [
        {
            "label": f"{clean_label} · this artifact",
            "scope": "artifact_kind",
            "scope_ref": artifact,
            "memory_scope": "artifact_kind",
            "memory_scope_label": "this artifact kind",
        }
    ]
    if project:
        variants.append(
            {
                "label": f"{clean_label} · this project",
                "scope": "project",
                "scope_ref": project,
                "memory_scope": "project",
                "memory_scope_label": "this project",
            }
        )
    return variants


def _operator_preference_decision_effect_summary(
    *,
    choice: str,
    artifact_kind: str,
    memory_scope: str,
    project_ref: str = "",
) -> str:
    artifact_label = _preference_text(artifact_kind, 32) or "artifact"
    token = _preference_text(choice, 32).lower()
    scope = _preference_text(memory_scope, 32).lower()
    project = _preference_text(project_ref, 64)
    if token == "apply_once":
        return f"apply on this {artifact_label} task only"
    if token == "apply_always":
        if scope == "project" and project:
            return f"apply now and remember across project {project}"
        return f"apply now and remember for future {artifact_label} work"
    if token == "skip_once":
        return f"skip on this {artifact_label} task only"
    if token == "skip_always":
        if scope == "project" and project:
            return f"skip now and suppress future prompts in project {project}"
        return f"skip now and suppress future {artifact_label} prompts"
    return "-"


def _build_operator_preference_decision_groups(
    *,
    task_ref: str,
    artifact_kind: str,
    project_ref: str,
    prompt_groups: list[tuple[str, list[Dict[str, Any]]]],
    return_path: str,
) -> list[Dict[str, Any]]:
    groups: list[Dict[str, Any]] = []
    for origin, prompt_rows in prompt_groups:
        origin_label = _operator_preference_decision_origin_label(origin)
        for row in prompt_rows:
            key = _preference_text(row.get("key"), 96).lower()
            if not key:
                continue
            description = _preference_text(row.get("description"), 240) or key
            summary = _preference_text(row.get("summary"), 240) or description
            source_scope = _preference_text(row.get("scope"), 32).lower() or origin
            scope_ref = _preference_text(row.get("scope_ref"), 64)
            option_actions: list[Dict[str, Any]] = []
            for option in list(row.get("options") or []):
                choice = _preference_text(option.get("choice"), 32).lower()
                label = _preference_text(option.get("label"), 64)
                if choice not in operator_preferences.PREFERENCE_DECISION_CHOICES or not label:
                    continue
                for variant in _operator_preference_decision_scope_variants(
                    choice=choice,
                    label=label,
                    artifact_kind=artifact_kind,
                    project_ref=project_ref,
                ):
                    effect = _operator_preference_decision_effect_summary(
                        choice=choice,
                        artifact_kind=artifact_kind,
                        memory_scope=_preference_text(variant.get("memory_scope"), 32),
                        project_ref=project_ref,
                    )
                    decision_payload = {
                        "task_ref": task_ref,
                        "artifact_kind": artifact_kind,
                        "key": key,
                        "value": row.get("value"),
                        "description": description,
                        "choice": choice,
                        "scope": variant.get("scope"),
                        "scope_ref": variant.get("scope_ref"),
                        "return_path": return_path,
                    }
                    option_actions.append(
                        {
                            "label": f"{key} · {variant.get('label')}",
                            "path": "/control/actions/task/operator-preference-decision",
                            "payload_json": json.dumps(decision_payload, ensure_ascii=False, separators=(",", ":")),
                            "mode": "safe",
                            "priority": "primary" if choice in {"apply_once", "apply_always"} else "secondary",
                            "command": f"/task {task_ref} | pref {key} {choice} {variant.get('memory_scope')}",
                            "note": f"{origin_label} | {effect}" if effect != "-" else origin_label,
                            "memory_policy": choice,
                            "memory_scope": _preference_text(variant.get("memory_scope"), 32),
                            "memory_scope_label": _preference_text(variant.get("memory_scope_label"), 64),
                            "memory_effect": effect,
                            "preference_origin": origin,
                            "preference_key": key,
                        }
                    )
            if not option_actions:
                continue
            groups.append(
                {
                    "key": key,
                    "artifact_kind": artifact_kind,
                    "origin": origin,
                    "origin_label": origin_label,
                    "description": description,
                    "summary": summary,
                    "source_scope": source_scope or "-",
                    "scope_ref": scope_ref or "-",
                    "actions": option_actions,
                }
            )
    return groups


def _summarize_operator_preference_decision_groups(groups: list[Dict[str, Any]]) -> str:
    if not groups:
        return "-"
    labels = []
    for row in groups:
        key = _preference_text(row.get("key"), 96)
        origin = _preference_text(row.get("origin"), 32)
        if not key:
            continue
        labels.append(f"{key}({origin or 'preference'})")
    if not labels:
        return "-"
    summary = "decision_prompts=" + " || ".join(labels[:3])
    if len(labels) > 3:
        summary += f" | total={len(labels)}"
    return summary


def _build_operator_preference_surface(
    *,
    entry: Dict[str, Any],
    alias: str,
    task_ref: str,
    task: Dict[str, Any],
    update_stub: Dict[str, Any],
    return_path: str,
) -> Dict[str, Any]:
    artifact_kind = _derive_operator_preference_artifact_kind(task=task, update_stub=update_stub)
    artifact_profile = _derive_operator_preference_artifact_profile(
        task=task,
        update_stub=update_stub,
        artifact_kind=artifact_kind,
    )
    team_dir = _preference_text(entry.get("team_dir"), 512)
    registry_state = operator_preferences.load_operator_preferences(team_dir) if team_dir else {"rules": []}
    candidate_state = operator_preferences.load_operator_preference_candidates(team_dir) if team_dir else {"candidates": []}
    session_rules = _load_task_operator_preference_session_rules(task)
    combined_state = {
        "rules": [*list(registry_state.get("rules") or []), *session_rules],
    }
    preflight = operator_preferences.build_adaptive_preference_preflight(
        combined_state,
        artifact_kind=artifact_kind,
        artifact_profile=artifact_profile,
        project_ref=alias,
    )
    candidate_rows = operator_preferences.build_preference_candidate_recommendations(
        candidate_state,
        preference_state=combined_state,
        artifact_kind=artifact_kind,
        project_ref=alias,
    )
    candidate_rows = [
        (
            lambda expected_scope, expected_scope_ref: {
            **row,
            "runtime_ref": alias,
            "expected_scope": expected_scope,
            "expected_scope_ref": expected_scope_ref,
            "expected_scope_label": _preference_memory_scope_label(expected_scope),
            **_preference_candidate_drilldown_links(
                project_alias=alias,
                artifact_kind=_preference_text(row.get("artifact_kind"), 64).lower() or artifact_kind,
                expected_scope=expected_scope,
                key=row.get("key"),
            ),
            "actions": _preference_candidate_action_rows(
                task_ref=task_ref,
                runtime_ref=alias,
                project_ref=row.get("project_ref"),
                artifact_kind=_preference_text(row.get("artifact_kind"), 64).lower() or artifact_kind,
                key=row.get("key"),
                suggested_value=row.get("suggested_value"),
                description=row.get("issue") or row.get("description"),
                return_path=_preference_candidate_management_return_path(
                    project_alias=alias,
                    artifact_kind=_preference_text(row.get("artifact_kind"), 64).lower() or artifact_kind,
                    expected_scope=expected_scope,
                ),
            ),
        }
        )(
            _preference_text(row.get("expected_scope"), 32).lower()
            or _preference_text(row.get("scope"), 32).lower()
            or "artifact_kind",
            _preference_text(row.get("expected_scope_ref"), 64)
            or _preference_text(row.get("scope_ref"), 64)
            or _preference_text(row.get("artifact_kind"), 64).lower()
            or "*",
        )
        for row in candidate_rows
    ]
    candidate_keys = {
        _preference_text(row.get("key"), 96).lower()
        for row in candidate_rows
        if _preference_text(row.get("key"), 96)
    }
    confirm_rows = [
        row
        for row in list(preflight.get("confirm") or [])
        if _preference_text(row.get("key"), 96).lower() not in candidate_keys
    ]
    manual_rows = [
        row
        for row in list(preflight.get("manual_only") or [])
        if _preference_text(row.get("scope"), 32).lower() != "session"
        and _preference_text(row.get("key"), 96).lower() not in candidate_keys
    ]
    disabled_rows = [
        row
        for row in list(preflight.get("disabled_defaults") or [])
        if _preference_text(row.get("key"), 96).lower() not in candidate_keys
    ]
    preflight = {
        **preflight,
        "confirm": confirm_rows,
        "manual_only": manual_rows,
        "disabled_defaults": disabled_rows,
    }
    applicable = operator_preferences.list_applicable_preferences(
        combined_state,
        artifact_kind=artifact_kind,
        project_ref=alias,
        include_disabled=True,
    )
    applied_preferences = [
        row
        for row in applicable
        if bool(row.get("enabled", False))
        and (
            _preference_text(row.get("prompt_mode"), 32).lower() == "auto"
            or _preference_text(row.get("scope"), 32).lower() == "session"
        )
    ]
    decision_groups = _build_operator_preference_decision_groups(
        task_ref=task_ref,
        artifact_kind=artifact_kind,
        project_ref=alias,
        prompt_groups=[
            ("confirm", confirm_rows),
            ("candidate", candidate_rows),
        ],
        return_path=return_path,
    )
    decision_actions = [
        action
        for group in decision_groups
        for action in list(group.get("actions") or [])
    ]
    surface = {
        "artifact_kind": artifact_kind,
        "artifact_profile": artifact_profile,
        "preflight": preflight,
        "candidate_preferences": candidate_rows,
        "preflight_summary": operator_preferences.summarize_preference_preflight(preflight),
        "applied_preferences": applied_preferences,
        "applied_preferences_summary": operator_preferences.summarize_applied_preferences(applied_preferences),
        "candidate_summary": operator_preferences.summarize_preference_candidates(candidate_rows),
        "candidate_scope_summary": operator_preferences.summarize_preference_candidate_scopes(candidate_rows),
        "confirm_summary": _summarize_operator_preference_bucket("confirm_preferences", confirm_rows),
        "manual_summary": _summarize_operator_preference_bucket("manual_preferences", manual_rows),
        "disabled_summary": _summarize_operator_preference_bucket("disabled_preferences", disabled_rows),
        "decision_groups": decision_groups,
        "decision_prompt_summary": _summarize_operator_preference_decision_groups(decision_groups),
        "decision_actions": decision_actions,
    }
    task["background_run_operator_preference_artifact_kind"] = artifact_kind
    task["background_run_operator_preference_artifact_profile"] = artifact_profile or "-"
    task["background_run_operator_preference_preflight_summary"] = surface["preflight_summary"]
    task["background_run_operator_preference_applied_summary"] = surface["applied_preferences_summary"]
    task["background_run_operator_preference_candidate_summary"] = surface["candidate_summary"]
    task["background_run_operator_preference_confirm_summary"] = surface["confirm_summary"]
    task["background_run_operator_preference_manual_summary"] = surface["manual_summary"]
    task["background_run_operator_preference_disabled_summary"] = surface["disabled_summary"]
    return surface


def _worker_blocker_lane_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()][:4]
    if isinstance(value, str) and str(value).strip() not in {"", "-"}:
        return [token.strip() for token in str(value).split(",") if token.strip()][:4]
    return []


def _resolve_runtime_entry(*, manager_state: Dict[str, Any], project_ref: str) -> tuple[str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(project_ref or "").strip()
    upper = target.upper()
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if target in {
            str(key).strip(),
            str(entry.get("name", "")).strip(),
            str(entry.get("project_alias", "")).strip(),
            str(entry.get("display_name", "")).strip(),
        } or upper in {
            str(key).strip().upper(),
            str(entry.get("name", "")).strip().upper(),
            str(entry.get("project_alias", "")).strip().upper(),
            str(entry.get("display_name", "")).strip().upper(),
        }:
            return str(key), entry
    raise RuntimeError(f"runtime not found: {project_ref or '-'}")


def _latest_task_for_runtime(entry: Dict[str, Any]) -> Dict[str, Any]:
    tasks = gateway_task_state.ensure_project_tasks(entry)
    if not tasks:
        return {}
    latest: Dict[str, Any] = {}
    latest_at = ""
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        status = runtime_read.normalize_task_status(task.get("status", "pending"))
        if status == "completed":
            continue
        updated_at = str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip()
        if updated_at >= latest_at:
            latest_at = updated_at
            latest = task
            latest.setdefault("request_id", str(request_id).strip())
    if latest:
        return latest
    for request_id, task in tasks.items():
        if isinstance(task, dict):
            latest = task
            latest.setdefault("request_id", str(request_id).strip())
            break
    return latest


def _worker_syncback_ready(task: Dict[str, Any]) -> bool:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    rows_payload = _worker_record_rows_payload(task)
    if list(rows_payload.get("rows") or []):
        return worker_task_contract.worker_task_module_syncback_ready_from_rows(rows_payload)
    records_summary = str(task.get("background_run_worker_records_summary", "")).strip()
    records_kind = ""
    if records_summary not in {"", "-"}:
        records_kind = records_summary.split(" | ", 1)[0].strip()
    raw_records = task.get("background_run_worker_records")
    record_tokens = []
    if isinstance(raw_records, list):
        record_tokens = [str(item).strip() for item in raw_records if str(item).strip()]
    elif isinstance(raw_records, str) and str(raw_records).strip() not in {"", "-"}:
        record_tokens = [str(item).strip() for item in raw_records.split(",") if str(item).strip()]
    if record_tokens:
        return worker_task_contract.worker_task_module_syncback_ready(
            {
                "module_kind": module_kind or ("package" if records_kind == "package_records" else "general"),
                "records_kind": records_kind or ("package_records" if module_kind == "package" else ""),
                "records": record_tokens,
            }
        )
    if records_kind == "package_records":
        return "syncback_record=ready" in records_summary
    return module_kind != "package"


def _worker_record_rows_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower() or "general"
    rows_summary = str(task.get("background_run_worker_record_rows_summary", "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    if module_kind == "general" and rows_kind.endswith("_record_rows"):
        inferred_module = rows_kind.split("_", 1)[0].strip().lower()
        if inferred_module in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = inferred_module
    raw_rows = task.get("background_run_worker_record_rows")
    row_tokens: list[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str) and str(raw_rows).strip() not in {"", "-"}:
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    elif rows_summary not in {"", "-"}:
        row_tokens = [str(item).strip() for item in rows_summary.split(" | ")[1:] if str(item).strip()]
    if row_tokens:
        return {
            "module_kind": module_kind,
            "rows_kind": rows_kind or f"{module_kind}_record_rows",
            "rows": row_tokens,
            "summary_line": rows_summary or "-",
        }
    records_summary = str(task.get("background_run_worker_records_summary", "")).strip()
    records_kind = ""
    if records_summary not in {"", "-"}:
        records_kind = records_summary.split(" | ", 1)[0].strip()
    if module_kind == "general" and records_kind.endswith("_records"):
        inferred_module = records_kind.split("_", 1)[0].strip().lower()
        if inferred_module in worker_task_contract.WORKER_MODULE_KINDS:
            module_kind = inferred_module
    if module_kind not in {"", "-", "general"}:
        gate_state = task.get("background_run_worker_gate_status")
        gate_summary = task.get("background_run_worker_gate_summary")
        gate_payload = (
            {
                "state": gate_state,
                "summary_line": gate_summary,
            }
            if str(gate_state or "").strip() or str(gate_summary or "").strip()
            else None
        )
        profile_state = task.get("background_run_worker_profile_status")
        profile_summary = task.get("background_run_worker_profile_summary")
        profile_payload = (
            {
                "state": profile_state,
                "summary_line": profile_summary,
            }
            if str(profile_state or "").strip() or str(profile_summary or "").strip()
            else None
        )
        checklist_state = task.get("background_run_worker_checklist_status")
        checklist_summary = task.get("background_run_worker_checklist_summary")
        checklist_payload = (
            {
                "state": checklist_state,
                "summary_line": checklist_summary,
            }
            if str(checklist_state or "").strip() or str(checklist_summary or "").strip()
            else None
        )
        item_tokens = task.get("background_run_worker_items")
        item_summary = task.get("background_run_worker_items_summary")
        items_payload = (
            {
                "module_kind": module_kind,
                "items": item_tokens if isinstance(item_tokens, list) else [],
                "summary_line": item_summary,
            }
            if (isinstance(item_tokens, list) and item_tokens) or str(item_summary or "").strip()
            else None
        )
        class_tokens = task.get("background_run_worker_item_classes")
        class_summary = task.get("background_run_worker_item_classes_summary")
        item_classes_payload = (
            {
                "module_kind": module_kind,
                "classes": class_tokens if isinstance(class_tokens, list) else [],
                "summary_line": class_summary,
            }
            if (isinstance(class_tokens, list) and class_tokens) or str(class_summary or "").strip()
            else None
        )
        record_tokens = task.get("background_run_worker_records")
        record_summary = task.get("background_run_worker_records_summary")
        records_payload = (
            {
                "module_kind": module_kind,
                "records": record_tokens if isinstance(record_tokens, list) else [],
                "summary_line": record_summary,
            }
            if (isinstance(record_tokens, list) and record_tokens) or str(record_summary or "").strip()
            else None
        )
        derived = worker_task_contract.derive_worker_task_module_record_rows(
            {
                "module_kind": module_kind,
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
            gate=gate_payload,
            profile=profile_payload,
            checklist=checklist_payload,
            items=items_payload,
            item_classes=item_classes_payload,
            records=records_payload,
        )
        if derived:
            return worker_task_contract.sanitize_worker_task_module_record_rows(derived)
    return {
        "module_kind": module_kind,
        "rows_kind": rows_kind or f"{module_kind}_record_rows",
        "rows": row_tokens,
        "summary_line": rows_summary or "-",
    }


def _worker_preflight_rows_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower() or "general"
    rows_summary = str(task.get("background_run_worker_preflight_rows_summary", "")).strip()
    rows_kind = ""
    if rows_summary not in {"", "-"}:
        rows_kind = rows_summary.split(" | ", 1)[0].strip()
    raw_rows = task.get("background_run_worker_preflight_rows")
    row_tokens: list[str] = []
    if isinstance(raw_rows, list):
        row_tokens = [str(item).strip() for item in raw_rows if str(item).strip()]
    elif isinstance(raw_rows, str) and str(raw_rows).strip() not in {"", "-"}:
        row_tokens = [str(item).strip() for item in raw_rows.split(",") if str(item).strip()]
    if row_tokens or rows_summary not in {"", "-"}:
        return {
            "module_kind": module_kind,
            "rows_kind": rows_kind or f"{module_kind}_preflight_rows",
            "rows": row_tokens,
            "summary_line": rows_summary or "-",
        }
    if module_kind in {"", "-", "general"}:
        return {
            "module_kind": module_kind or "general",
            "rows_kind": "general_preflight_rows",
            "rows": [],
            "summary_line": "-",
        }
    record_rows_payload = _worker_record_rows_payload(task)
    derived = worker_task_contract.derive_worker_task_module_preflight_rows(
        {
            "module_kind": module_kind,
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
        gate={
            "state": task.get("background_run_worker_gate_status"),
            "summary_line": task.get("background_run_worker_gate_summary"),
        },
        profile={
            "state": task.get("background_run_worker_profile_status"),
            "summary_line": task.get("background_run_worker_profile_summary"),
        },
        checklist={
            "state": task.get("background_run_worker_checklist_status"),
            "summary_line": task.get("background_run_worker_checklist_summary"),
        },
        items={
            "module_kind": module_kind,
            "items": (task.get("background_run_worker_items") if isinstance(task.get("background_run_worker_items"), list) else []),
            "summary_line": task.get("background_run_worker_items_summary"),
        },
        item_classes={
            "module_kind": module_kind,
            "classes": (task.get("background_run_worker_item_classes") if isinstance(task.get("background_run_worker_item_classes"), list) else []),
            "summary_line": task.get("background_run_worker_item_classes_summary"),
        },
        records={
            "module_kind": module_kind,
            "records": (task.get("background_run_worker_records") if isinstance(task.get("background_run_worker_records"), list) else []),
            "summary_line": task.get("background_run_worker_records_summary"),
        },
        record_rows=record_rows_payload if list(record_rows_payload.get("rows") or []) else None,
        preflight={
            "module_kind": module_kind,
            "state": task.get("background_run_worker_preflight_status"),
            "summary_line": task.get("background_run_worker_preflight_summary"),
        },
    )
    return {
        "module_kind": module_kind,
        "rows_kind": str(derived.get("rows_kind", "")).strip() or f"{module_kind}_preflight_rows",
        "rows": list(derived.get("rows") or []),
        "summary_line": str(derived.get("summary_line", "")).strip() or "-",
    }


def _worker_apply_ready(task: Dict[str, Any]) -> bool:
    apply_gate = gateway_task_state.derive_task_apply_gate(task)
    if str(apply_gate.get("status", "")).strip() == "blocked":
        return False
    payload = _worker_record_rows_payload(task)
    if list(payload.get("rows") or []):
        return worker_task_contract.worker_task_module_apply_ready(payload)
    return str(payload.get("module_kind", "")).strip().lower() in {"", "-", "general"}


def _worker_apply_not_ready_response(
    *,
    spec: Dict[str, object],
    alias: str,
    payload: Dict[str, Any],
    task: Dict[str, Any],
    label: str,
    request_id: str,
    mode: str,
    outcome_kind: str,
) -> Tuple[int, Dict[str, str], bytes]:
    apply_gate = gateway_task_state.derive_task_apply_gate(task)
    record_rows_payload = _worker_record_rows_payload(task)
    row_detail = str(record_rows_payload.get("summary_line", "")).strip()
    preflight_rows_payload = _worker_preflight_rows_payload(task)
    preflight_rows_detail = str(preflight_rows_payload.get("summary_line", "")).strip()
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            **preflight_rows_payload,
            "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
            "followup_brief_execution_lane_ids": _worker_blocker_lane_ids(task.get("followup_brief_execution_lane_ids")),
            "followup_brief_review_lane_ids": _worker_blocker_lane_ids(task.get("followup_brief_review_lane_ids")),
        },
        mode="apply",
    )
    detail = (
        preflight_rows_detail
        or str(task.get("background_run_worker_preflight_summary", "")).strip()
        or row_detail
        or "worker apply gate not ready"
    )
    suggested_action = str(blocker.get("suggested_action", "")).strip().lower()
    suggested_lane_ids = _worker_blocker_lane_ids(blocker.get("suggested_lane_ids"))
    next_step = f"/task {label}"
    if suggested_action == "followup":
        next_step = f"/followup {label}"
        if suggested_lane_ids:
            next_step += f" lane {','.join(suggested_lane_ids)}"
    elif suggested_action == "followup_execute":
        next_step = f"/followup-exec {label}"
        if suggested_lane_ids:
            next_step += f" lane {','.join(suggested_lane_ids)}"
    elif suggested_action == "judge":
        next_step = f"/orch judge {alias}"
    remediation = str(blocker.get("remediation", "")).strip() or (
        "wait until the module-specific worker gate reports apply-ready rows before promoting or accepting artifact apply"
    )
    reason_code = str(blocker.get("reason_code", "")).strip() or "worker_apply_not_ready"
    apply_gate_reason = str(apply_gate.get("reason_code", "")).strip()
    blocker_reason = str(blocker.get("reason_code", "")).strip()
    gate_preempts_blocker = str(apply_gate.get("status", "")).strip() == "blocked" and (
        not blocker_reason
        or apply_gate_reason in {"job_contract_missing", "phase_checkpoint_blocked", "phase_checkpoint_not_apply_ready"}
    )
    if gate_preempts_blocker:
        detail = str(apply_gate.get("detail", "")).strip() or detail
        next_step = str(apply_gate.get("next_step", "")).strip() or next_step
        remediation = str(apply_gate.get("remediation", "")).strip() or remediation
        reason_code = apply_gate_reason or reason_code
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": mode,
            "source_command": str(spec.get("command", "")).strip() or f"/task {label} | worker-apply-preview",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": outcome_kind,
                "status": "blocked",
                "reason_code": reason_code,
                "detail": detail,
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "worker_record_rows": row_detail or detail,
            "worker_preflight_rows": preflight_rows_detail or detail,
            "worker_blocker": str(blocker.get("summary_line", "")).strip() or detail,
            "worker_blocked_rows": list(blocker.get("blocked_rows") or []),
            "worker_recommended_action": suggested_action or "task_review",
            "worker_recommended_lane_ids": suggested_lane_ids,
            "job_contract": str(apply_gate.get("job_contract_summary", "")).strip() or "-",
            "phase_checkpoint": str(apply_gate.get("phase_checkpoint_summary", "")).strip() or "-",
            "preview": {
                "kind": "worker_apply_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
        },
        status=409,
    )


def _package_syncback_not_ready_response(
    *,
    spec: Dict[str, object],
    alias: str,
    payload: Dict[str, Any],
    latest_task: Dict[str, Any],
    mode: str,
) -> Tuple[int, Dict[str, str], bytes]:
    task_ref = str(latest_task.get("short_id", "")).strip()
    next_step = f"/task {task_ref}" if task_ref else f"/orch status {alias}"
    preflight_detail = str(latest_task.get("background_run_worker_preflight_summary", "")).strip()
    record_rows_payload = _worker_record_rows_payload(latest_task)
    preflight_rows_payload = _worker_preflight_rows_payload(latest_task)
    preflight_rows_detail = str(preflight_rows_payload.get("summary_line", "")).strip()
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        preflight_rows_payload,
        mode="syncback",
    )
    row_detail = str(record_rows_payload.get("summary_line", "")).strip()
    record_detail = str(latest_task.get("background_run_worker_records_summary", "")).strip()
    detail = preflight_rows_detail or preflight_detail or row_detail or record_detail or "package syncback record pending"
    remediation = str(blocker.get("remediation", "")).strip() or "wait until package preflight reports syncback_ready before accepted syncback"
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": mode,
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback {'preview' if mode == 'safe' else 'apply'}",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": "runtime_syncback_preview" if mode == "safe" else "runtime_syncback_apply",
                "status": "blocked",
                "reason_code": str(blocker.get("reason_code", "")).strip() or "package_syncback_not_ready",
                "detail": detail,
            },
            "preview": {
                "kind": "runtime_syncback_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
            "worker_records": record_detail or detail,
            "worker_record_rows": row_detail or detail,
            "worker_preflight": preflight_detail or detail,
            "worker_preflight_rows": preflight_rows_detail or detail,
            "worker_blocker": str(blocker.get("summary_line", "")).strip() or detail,
            "worker_blocked_rows": list(blocker.get("blocked_rows") or []),
            "worker_recommended_action": str(blocker.get("suggested_action", "")).strip().lower() or "task_review",
        },
        status=409,
    )


def _resolve_task_entry(*, manager_state: Dict[str, Any], task_ref: str) -> tuple[str, Dict[str, Any], str, Dict[str, Any]]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(task_ref or "").strip()
    if not target:
        raise RuntimeError("task not found: -")
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        task = gateway_task_state.get_task_record(entry, target)
        if not isinstance(task, dict):
            continue
        request_id = gateway_task_state.resolve_task_request_id(entry, target)
        if request_id:
            return str(key), entry, request_id, task
    raise RuntimeError(f"task not found: {target}")


def _save_manager_state(config: DashboardAppConfig, manager_state: Dict[str, Any]) -> None:
    gateway_main = _load_gateway_main_module()
    gateway_main.save_manager_state(config.manager_state_file, manager_state)


def _worker_update_stub_for_task(task: Dict[str, Any]) -> Dict[str, Any]:
    return worker_task_contract.sanitize_worker_task_update_stub(
        {
            "status": task.get("background_run_worker_update_stub_status"),
            "summary_line": task.get("background_run_worker_update_stub_summary"),
            "target_artifacts": task.get("background_run_worker_update_stub_targets"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        }
    )


def _worker_contract_seed_for_task(*, request_id: str, label: str, task: Dict[str, Any], update_stub: Dict[str, Any]) -> Dict[str, Any]:
    return worker_task_contract.sanitize_worker_task_contract(
        {
            "request_id": request_id,
            "task_id": str(label or "").strip()[:48],
            "task_label": label,
            "status": str(task.get("status", "")).strip() or "-",
            "tf_phase": str(task.get("tf_phase", "")).strip() or "-",
            "pack_profile": "offdesk_execute",
            "objective": str(task.get("prompt", "")).strip() or str(task.get("alias", "")).strip() or label,
            "execution_brief_status": str(task.get("execution_brief_status", "")).strip() or "-",
            "execution_brief_summary": str(task.get("execution_brief_summary", "")).strip() or "-",
            "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
            "followup_brief_summary": str(task.get("followup_brief_summary", "")).strip() or "-",
            "artifact_targets": list(update_stub.get("target_artifacts") or []),
        }
    )


def _worker_apply_preview_payload(
    *,
    alias: str,
    request_id: str,
    label: str,
    task: Dict[str, Any],
    update_stub: Dict[str, Any],
    proposal_ids: list[str],
) -> Dict[str, Any]:
    contract_seed = _worker_contract_seed_for_task(request_id=request_id, label=label, task=task, update_stub=update_stub)
    proposal_payloads = worker_task_contract.derive_worker_artifact_apply_todo_proposals(contract_seed, update_stub)
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, proposal_ids)
    accepted_todo_id = str(task.get("background_run_worker_apply_accept_todo_id", "")).strip()
    next_step = (
        f"/todo {alias} accept {proposal_ids[0]}"
        if proposal_ids
        else (f"/todo {alias}" if accepted_todo_id else f"/task {label} | worker-apply-propose")
    )
    return {
        "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
        "worker_result_summary": str(task.get("background_run_worker_result_summary", "")).strip() or "-",
        "update_stub_summary": str(update_stub.get("summary_line", "")).strip() or "-",
        "proposal_summary": proposal_summary or "-",
        "proposal_ids": proposal_ids,
        "proposal_payloads": proposal_payloads,
        "target_artifacts": list(update_stub.get("target_artifacts") or []),
        "actions": list(update_stub.get("actions") or []),
        "cautions": list(update_stub.get("cautions") or []),
        "evidence_refs": list(update_stub.get("evidence_refs") or []),
        "next_step": next_step,
    }


def _persist_worker_apply_accept_state(
    *,
    entry: Dict[str, Any],
    task: Dict[str, Any],
    request_id: str,
    update_stub: Dict[str, Any],
    preview_payload: Dict[str, Any],
    result: Dict[str, Any],
    accepted_at: str,
) -> None:
    proposals_store, _proposal_seq = todo_state.ensure_todo_proposal_store(entry)
    open_apply_proposal_ids: list[str] = []
    for proposal_id in worker_task_contract.match_worker_update_proposal_ids(
        proposals_store,
        request_id=request_id,
        proposal_payloads=preview_payload.get("proposal_payloads") or [],
    ):
        proposal = todo_state.find_proposal_by_ref(proposals_store, proposal_id)
        if not isinstance(proposal, dict):
            continue
        if todo_state.normalize_proposal_status(proposal.get("status", "open")) != "open":
            continue
        token = str(proposal_id).strip()
        if token and token not in open_apply_proposal_ids:
            open_apply_proposal_ids.append(token)
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, open_apply_proposal_ids)
    apply_accept_summary = worker_task_contract.summarize_worker_artifact_apply_accept_summary(
        proposal_id=result.get("proposal_id"),
        todo_id=result.get("todo_id"),
        target_artifacts=preview_payload.get("target_artifacts") or [],
        accepted_at=accepted_at,
    )
    task["background_run_worker_apply_accept_status"] = "applied"
    task["background_run_worker_apply_accept_summary"] = apply_accept_summary
    task["background_run_worker_apply_accept_proposal_id"] = str(result.get("proposal_id", "")).strip()
    task["background_run_worker_apply_accept_todo_id"] = str(result.get("todo_id", "")).strip()
    task["background_run_worker_apply_accept_at"] = accepted_at
    if open_apply_proposal_ids:
        task["background_run_worker_update_proposal_summary"] = proposal_summary
        task["background_run_worker_update_proposal_ids"] = list(open_apply_proposal_ids)
    else:
        task.pop("background_run_worker_update_proposal_summary", None)
        task.pop("background_run_worker_update_proposal_ids", None)
    task["updated_at"] = accepted_at
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_apply_accept_status"] = "applied"
        task["result"]["background_run_worker_apply_accept_summary"] = apply_accept_summary
        task["result"]["background_run_worker_apply_accept_proposal_id"] = str(result.get("proposal_id", "")).strip()
        task["result"]["background_run_worker_apply_accept_todo_id"] = str(result.get("todo_id", "")).strip()
        task["result"]["background_run_worker_apply_accept_at"] = accepted_at
        if open_apply_proposal_ids:
            task["result"]["background_run_worker_update_proposal_summary"] = proposal_summary
            task["result"]["background_run_worker_update_proposal_ids"] = list(open_apply_proposal_ids)
        else:
            task["result"].pop("background_run_worker_update_proposal_summary", None)
            task["result"].pop("background_run_worker_update_proposal_ids", None)


def _syncback_preview_payload(*, alias: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    updates = []
    for idx, new_line in list(plan.get("updates") or [])[:4]:
        updates.append(f"L{int(idx) + 1}: {str(new_line).strip()[:180]}")
    append_lines = [str(line).strip()[:180] for line in list(plan.get("append_lines") or [])[:4] if str(line).strip()]
    return {
        "kind": "runtime_syncback_preview",
        "project_alias": alias,
        "target_path": str(plan.get("path", "")).strip() or "-",
        "done_count": int(plan.get("done_count", 0) or 0),
        "reopen_count": int(plan.get("reopen_count", 0) or 0),
        "append_count": int(plan.get("append_count", 0) or 0),
        "blocked_count": int(plan.get("blocked_count", 0) or 0),
        "updates": updates,
        "append_lines": append_lines,
        "next_step": f"/todo {alias} syncback apply",
        "runtime_path": _runtime_action_link(alias),
    }


def _summarize_worker_syncback_apply(
    *,
    todo_id: str,
    path: str,
    line_count: int,
    append_count: int,
    done_count: int,
    reopen_count: int,
    blocked_count: int,
    applied_at: str,
) -> str:
    path_token = Path(path).name if str(path).strip() else "-"
    todo_token = str(todo_id or "").strip() or "-"
    return (
        "state=applied | todo={todo} | path={path} | lines={lines} | "
        "done={done} reopen={reopen} append={append} blocked={blocked} | at={at}"
    ).format(
        todo=todo_token,
        path=path_token,
        lines=max(0, int(line_count or 0)),
        done=max(0, int(done_count or 0)),
        reopen=max(0, int(reopen_count or 0)),
        append=max(0, int(append_count or 0)),
        blocked=max(0, int(blocked_count or 0)),
        at=str(applied_at or "").strip() or "-",
    )


def _persist_worker_syncback_apply_state(
    *,
    task: Dict[str, Any],
    result: Dict[str, Any],
    preview: Dict[str, Any],
    applied_at: str,
) -> None:
    summary = _summarize_worker_syncback_apply(
        todo_id=str(task.get("background_run_worker_apply_accept_todo_id", "")).strip(),
        path=str(result.get("path", "")).strip(),
        line_count=int(result.get("line_count", 0) or 0),
        append_count=int(preview.get("append_count", 0) or 0),
        done_count=int(preview.get("done_count", 0) or 0),
        reopen_count=int(preview.get("reopen_count", 0) or 0),
        blocked_count=int(preview.get("blocked_count", 0) or 0),
        applied_at=applied_at,
    )
    task["background_run_worker_syncback_status"] = "applied"
    task["background_run_worker_syncback_summary"] = summary
    task["background_run_worker_syncback_at"] = applied_at
    persist_canonical_writeback_state(
        task,
        headline="Syncback Apply | executed",
        state="executed",
        next_step=f"/sync preview {str(preview.get('project_alias', '')).strip() or '-'} 24h",
        at=applied_at,
        path=str(result.get("path", "")).strip(),
        line_count=int(result.get("line_count", 0) or 0),
        done_count=int(preview.get("done_count", 0) or 0),
        reopen_count=int(preview.get("reopen_count", 0) or 0),
        append_count=int(preview.get("append_count", 0) or 0),
        blocked_count=int(preview.get("blocked_count", 0) or 0),
    )
    task["updated_at"] = applied_at
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_syncback_status"] = "applied"
        task["result"]["background_run_worker_syncback_summary"] = summary
        task["result"]["background_run_worker_syncback_at"] = applied_at


def _execute_runtime_syncback_preview_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    latest_task = _latest_task_for_runtime(entry)
    if (
        isinstance(latest_task, dict)
        and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied"
        and not _worker_syncback_ready(latest_task)
    ):
        return _package_syncback_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            latest_task=latest_task,
            mode="safe",
        )
    try:
        plan = todo_state.preview_syncback_plan(entry)
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
                "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback preview",
                "payload": payload,
                "next_step": f"/todo {alias}",
                "remediation": "restore canonical TODO.md before previewing accepted artifact syncback",
                "outcome": {
                    "kind": "runtime_syncback_preview",
                    "status": "blocked",
                    "reason_code": "syncback_preview_failed",
                    "detail": str(exc).strip() or "-",
                },
                "preview": {
                    "kind": "runtime_syncback_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    preview = _syncback_preview_payload(alias=alias, plan=plan)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback preview",
            "payload": payload,
            "next_step": preview["next_step"],
            "remediation": "inspect the canonical TODO diff before applying accepted artifact syncback",
            "outcome": {
                "kind": "runtime_syncback_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": (
                    "done={done} reopen={reopen} append={append} blocked={blocked}".format(
                        done=preview["done_count"],
                        reopen=preview["reopen_count"],
                        append=preview["append_count"],
                        blocked=preview["blocked_count"],
                    )
                ),
            },
            "preview": preview,
        },
        status=200,
    )


def _execute_runtime_syncback_apply_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    latest_task = _latest_task_for_runtime(entry)
    if (
        isinstance(latest_task, dict)
        and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied"
        and not _worker_syncback_ready(latest_task)
    ):
        return _package_syncback_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            latest_task=latest_task,
            mode="phase2",
        )
    try:
        plan = todo_state.preview_syncback_plan(entry)
    except RuntimeError as exc:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback apply",
                "payload": payload,
                "next_step": f"/todo {alias} syncback preview",
                "remediation": "inspect canonical TODO syncback preview before applying writeback again",
                "outcome": {
                    "kind": "runtime_syncback_apply",
                    "status": "blocked",
                    "reason_code": "syncback_preview_failed",
                    "detail": str(exc).strip() or "-",
                },
                "preview": {
                    "kind": "runtime_syncback_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    result = todo_state.apply_syncback_plan(plan)
    preview = _syncback_preview_payload(alias=alias, plan=plan)
    applied_at = _now_iso()
    if isinstance(latest_task, dict) and str(latest_task.get("background_run_worker_apply_accept_status", "")).strip() == "applied":
        _persist_worker_syncback_apply_state(
            task=latest_task,
            result=result,
            preview=preview,
            applied_at=applied_at,
        )
        _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or f"/todo {alias} syncback apply",
            "payload": payload,
            "next_step": f"/sync preview {alias} 24h",
            "remediation": "verify canonical TODO drift is cleared before applying another accepted artifact syncback",
            "outcome": {
                "kind": "runtime_syncback_apply",
                "status": "executed",
                "reason_code": "completed",
                "detail": (
                    "path={path} lines={lines} done={done} reopen={reopen} append={append} blocked={blocked}".format(
                        path=str(result.get("path", "")).strip() or "-",
                        lines=int(result.get("line_count", 0) or 0),
                        done=preview["done_count"],
                        reopen=preview["reopen_count"],
                        append=preview["append_count"],
                        blocked=preview["blocked_count"],
                    )
                ),
            },
            "preview": preview,
            "result": {
                "path": str(result.get("path", "")).strip() or "-",
                "line_count": int(result.get("line_count", 0) or 0),
            },
            "worker_syncback": (
                str((latest_task or {}).get("background_run_worker_syncback_summary", "")).strip() or "-"
            ),
        },
        status=200,
    )


def _execute_runtime_judge_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    team_dir_raw = str(entry.get("team_dir", "")).strip()
    if not team_dir_raw:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
                "payload": payload,
                "next_step": f"/orch status {alias}",
                "remediation": "restore the runtime team_dir before invoking off-desk judge again",
                "outcome": {
                    "kind": "offdesk_judge",
                    "status": "blocked",
                    "reason_code": "team_dir_missing",
                    "detail": "team_dir missing",
                },
                "preview": {
                    "kind": "runtime_judge",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    team_dir = Path(team_dir_raw).expanduser().resolve()
    latest_task = _latest_task_for_runtime(entry)
    if not latest_task:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
                "payload": payload,
                "next_step": f"/orch status {alias}",
                "remediation": "create or recover a task before invoking off-desk judge again",
                "outcome": {
                    "kind": "offdesk_judge",
                    "status": "blocked",
                    "reason_code": "no_task_available",
                    "detail": "no task available for judge review",
                },
                "preview": {
                    "kind": "runtime_judge",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=latest_task,
        pack_profile_override="review",
    )
    result = model_provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry=entry,
        task=latest_task,
        prompt=_offdesk_judge_prompt(entry, latest_task, team_dir),
        system=_OFFDESK_JUDGE_SYSTEM,
        pack_profile_override="review",
        timeout_sec=120.0,
    )
    ok = bool(result.get("ok"))
    executed = bool(result.get("executed"))
    summary = str(result.get("summary", "-")).strip() or "-"
    response_text = str(result.get("response_text", "")).strip()
    reason_code = str(result.get("reason_code", "")).strip() or ("ok" if ok else "not_executed")
    judge_decision = _offdesk_judge_decision_snapshot(latest_task, response_text)
    audit_team_dir = Path(str(config.team_dir or team_dir)).expanduser().resolve()
    recorded_at = _now_iso()
    append_action_audit_row(
        audit_team_dir,
        headline=f"Offdesk Judge | {'executed' if ok else 'blocked'}",
        status="executed" if ok else "blocked",
        outcome_kind="offdesk_judge",
        outcome_status="executed" if ok else "blocked",
        outcome_reason_code=reason_code,
        outcome_detail=summary,
        next_step=f"/offdesk review {alias}",
        remediation="inspect the judge response together with execution brief, followup brief, and runtime status before acting",
        source_command=f"/orch judge {alias}",
        link_label="runtime detail",
        link_href=_runtime_action_link(alias),
        at=recorded_at,
        extra={
            "response_text": response_text,
            "decision_snapshot": judge_decision,
        }
        if response_text or judge_decision
        else None,
    )
    if isinstance(latest_task, dict):
        persist_manual_step_execution_state(
            latest_task,
            manual_kind="manual_review",
            source_command=str(spec.get("command", "")).strip() or f"/orch judge {alias}",
            state="executed" if ok else "blocked",
            next_step=f"/offdesk review {alias}",
            at=recorded_at,
        )
        _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": ok,
            "implemented": True,
            "executed": executed,
            "status": "executed" if ok else "blocked",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/orch judge {alias}",
            "payload": payload,
            "binding": str(binding.get("summary", "")).strip() or "-",
            "summary": summary,
            "response": response_text or "-",
            "next_step": f"/offdesk review {alias}",
            "remediation": "inspect the judge response together with execution brief, followup brief, and runtime status before acting",
            "outcome": {
                "kind": "offdesk_judge",
                "status": "executed" if ok else "blocked",
                "reason_code": reason_code,
                "detail": summary,
            },
            "task": {
                "request_id": str(latest_task.get("request_id", "")).strip() or "-",
                "label": str(latest_task.get("short_id", "")).strip() or str(latest_task.get("alias", "")).strip() or "-",
                "detail_path": f"/control/tasks/by-request/{str(latest_task.get('request_id', '')).strip()}",
            },
            "preview": {
                "kind": "runtime_judge",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
            "latest_judge_decision": judge_decision,
        },
        status=200 if ok else 409,
    )


def _execute_analysis_review_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    review_kind = str(payload.get("review_kind", "")).strip().lower() or "task_review"
    review_suffix = {
        "task_review": "analysis-review",
        "analysis_review_ready": "analysis-review",
        "contract_review_ready": "contract-review",
        "debug_review_ready": "debug-review",
        "phase_review_ready": "phase-review",
        "package_verification_review": "package-verification-review",
        "package_apply_review": "package-apply-review",
        "package_syncback_review": "package-syncback-review",
        "package_artifact_review": "package-artifact-review",
    }.get(review_kind, review_kind.replace("_", "-") or "task-review")
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
                "remediation": "refresh the task list and retry the analysis review with an existing task ref",
                "outcome": {
                    "kind": "task_review",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    subagent_surface = harness_authoring_adapter.summarize_general_subagent_surface(
        Path(str(entry.get("team_dir", "")).strip() or "."),
        entry=entry,
        task=task,
    )
    planning_lanes_summary = gateway_task_view.planning_lane_operator_summary(task)
    approved_plan_gate_summary = gateway_task_view.approved_plan_gate_operator_summary(task)
    planning_compact_summary = gateway_task_view.planning_review_operator_summary(
        planning_lanes=planning_lanes_summary,
        approved_plan_gate=approved_plan_gate_summary,
    )
    planning_handoff = {
        "job_contract": {
            "status": str(task.get("job_contract_status", "")).strip() or "-",
            "summary": str(task.get("job_contract_summary", "")).strip() or "-",
            "goal": str(task.get("job_contract_goal", "")).strip() or "-",
            "scope": [str(item).strip() for item in (task.get("job_contract_scope") or []) if str(item).strip()],
            "acceptance_checks": [str(item).strip() for item in (task.get("job_contract_acceptance_checks") or []) if str(item).strip()],
            "artifacts_to_touch": [str(item).strip() for item in (task.get("job_contract_artifacts_to_touch") or []) if str(item).strip()],
            "rollback_hint": str(task.get("job_contract_rollback_hint", "")).strip() or "-",
        },
        "debug_packet": {
            "state": str(task.get("debug_packet_state", "")).strip() or "-",
            "summary": str(task.get("debug_packet_summary", "")).strip() or "-",
            "symptom": str(task.get("debug_packet_symptom", "")).strip() or "-",
            "root_cause": str(task.get("debug_packet_root_cause", "")).strip() or "-",
            "evidence": [str(item).strip() for item in (task.get("debug_packet_evidence") or []) if str(item).strip()],
            "failed_attempt": str(task.get("debug_packet_failed_attempt", "")).strip() or "-",
            "next_step": str(task.get("debug_packet_next_step", "")).strip() or "-",
        },
        "phase_checkpoint": {
            "status": str(task.get("phase_checkpoint_status", "")).strip() or "-",
            "current_phase": str(task.get("phase_checkpoint_current_phase", "")).strip() or "-",
            "summary": str(task.get("phase_checkpoint_summary", "")).strip() or "-",
            "rows": [str(item).strip() for item in (task.get("phase_checkpoint_rows") or []) if str(item).strip()],
        },
        "planning_lanes_summary": planning_lanes_summary,
        "approved_plan_gate_summary": approved_plan_gate_summary,
        "planner_lane_summary": str(task.get("planner_lane_summary", "")).strip() or "-",
        "critic_lane_summary": str(task.get("critic_lane_summary", "")).strip() or "-",
        "planning_compact_summary": planning_compact_summary,
    }
    if review_kind in {"contract_review_ready", "debug_review_ready", "phase_review_ready", "analysis_review_ready"}:
        planning_detail = {
            "contract_review_ready": str(task.get("job_contract_summary", "")).strip() or "-",
            "debug_review_ready": str(task.get("debug_packet_summary", "")).strip() or "-",
            "phase_review_ready": str(task.get("phase_checkpoint_summary", "")).strip() or "-",
            "analysis_review_ready": str(task.get("background_run_worker_record_set_summary", "")).strip()
            or str(task.get("background_run_worker_record_rows_summary", "")).strip()
            or "-",
        }.get(review_kind, "-")
        remediation = {
            "contract_review_ready": "inspect the job contract goal, scope, acceptance checks, and artifact targets before dispatching or retrying again",
            "debug_review_ready": "inspect the debug packet symptom, evidence, failed attempt, and next step before retrying or replanning again",
            "phase_review_ready": "inspect the current phase checkpoint rows before retrying, replanning, or applying worker updates",
            "analysis_review_ready": "inspect the analysis record set and blocked findings before resuming the task",
        }.get(review_kind, "inspect the task review packet before resuming operator actions")
        return _json(
            {
                "ok": True,
                "implemented": True,
                "executed": False,
                "status": "preview",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/task {label} | {review_suffix}",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": remediation,
                "outcome": {
                    "kind": "task_review",
                    "status": "preview",
                    "reason_code": review_kind,
                    "detail": planning_detail,
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
                "planning_handoff": planning_handoff,
                "planning_compact_summary": str(planning_handoff.get("planning_compact_summary", "")).strip() or "-",
                "planning_compact": str(planning_handoff.get("planning_compact_summary", "")).strip() or "-",
                "subagent_contract_summary": str(subagent_surface.get("summary", "")).strip() or "-",
                "subagent_evidence_summary": str(subagent_surface.get("artifact_summary", "")).strip() or "-",
                "subagent_artifact_path": str(subagent_surface.get("artifact_path", "")).strip() or "-",
                "subagent_gate_summary": str(subagent_surface.get("gate_summary", "")).strip() or "-",
                "planning_lanes": planning_lanes_summary,
                "approved_plan_gate": approved_plan_gate_summary,
                "job_contract": planning_handoff["job_contract"]["summary"],
                "debug_packet": planning_handoff["debug_packet"]["summary"],
                "phase_checkpoint": planning_handoff["phase_checkpoint"]["summary"],
                "preview": {
                    "kind": "task_review",
                    "review_kind": review_kind,
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=200,
        )
    record_rows_payload = _worker_record_rows_payload(task)
    preflight_rows_payload = _worker_preflight_rows_payload(task)
    blocker = worker_task_contract.derive_worker_task_module_action_blocker(
        {
            **preflight_rows_payload,
            "followup_brief_status": str(task.get("followup_brief_status", "")).strip() or "-",
        },
        mode="apply",
    )
    row_detail = str(record_rows_payload.get("summary_line", "")).strip() or "-"
    preflight_detail = str(preflight_rows_payload.get("summary_line", "")).strip() or "-"
    blocker_summary = str(blocker.get("summary_line", "")).strip() or preflight_detail
    remediation = str(blocker.get("remediation", "")).strip() or "inspect the blocked analysis rows before promoting analysis changes"
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/task {label} | {review_suffix}",
            "payload": payload,
            "next_step": f"/task {label}",
            "remediation": remediation,
                "outcome": {
                    "kind": "task_review",
                "status": "preview",
                "reason_code": str(blocker.get("reason_code", "")).strip() or "task_review",
                "detail": blocker_summary,
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "worker_record_rows": row_detail,
            "worker_preflight_rows": preflight_detail,
            "worker_blocker": blocker_summary,
            "worker_blocked_rows": list(blocker.get("blocked_rows") or []),
            "worker_recommended_action": str(blocker.get("suggested_action", "")).strip().lower() or "task_review",
            "planning_handoff": planning_handoff,
            "subagent_contract_summary": str(subagent_surface.get("summary", "")).strip() or "-",
            "subagent_evidence_summary": str(subagent_surface.get("artifact_summary", "")).strip() or "-",
            "subagent_artifact_path": str(subagent_surface.get("artifact_path", "")).strip() or "-",
            "subagent_gate_summary": str(subagent_surface.get("gate_summary", "")).strip() or "-",
            "preview": {
                "kind": "task_review",
                "review_kind": review_kind,
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "worker_module": str(task.get("background_run_task_contract_module_summary", "")).strip() or "-",
                "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
                "worker_gate": str(task.get("background_run_worker_gate_summary", "")).strip() or "-",
                "worker_profile": str(task.get("background_run_worker_profile_summary", "")).strip() or "-",
                "worker_checklist": str(task.get("background_run_worker_checklist_summary", "")).strip() or "-",
            },
        },
        status=200,
    )


def _execute_general_subagent_support_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
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
                "remediation": "refresh the task list and retry support research with an existing task ref",
                "outcome": {
                    "kind": "general_subagent_support",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    team_dir = Path(str(entry.get("team_dir", "")).strip() or str(config.team_dir or ".")).expanduser().resolve()
    artifact = harness_authoring_adapter.run_general_subagent_support(
        team_dir,
        entry=entry,
        task=task,
    )
    if not artifact:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "safe",
                "source_command": str(spec.get("command", "")).strip() or f"/task {label} | general-research-support",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "inspect the harness authoring contract and selected docs before retrying support research",
                "outcome": {
                    "kind": "general_subagent_support",
                    "status": "blocked",
                    "reason_code": "artifact_not_written",
                    "detail": "support research contract did not produce an artifact",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=500,
        )
    subagent_surface = harness_authoring_adapter.summarize_general_subagent_surface(
        team_dir,
        entry=entry,
        task=task,
    )
    planning_bundle = gateway_task_view.planning_operator_bundle(task)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "completed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or f"/task {label} | general-research-support",
            "payload": payload,
            "next_step": f"/control/tasks/by-request/{request_id}",
            "remediation": "review the bounded evidence artifact before changing planning, dispatch, or apply state",
            "outcome": {
                "kind": "general_subagent_support",
                "status": "completed",
                "reason_code": "artifact_written",
                "detail": str(subagent_surface.get("artifact_summary", "")).strip() or str(artifact.get("artifact_path", "")).strip() or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "preview": {
                "kind": "general_subagent_support",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "general_subagent_executed": True,
            "general_subagent_artifact_path": str(artifact.get("artifact_path", "")).strip() or "-",
            "planning_compact_summary": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "planning_compact": str(planning_bundle.get("planning_compact", "")).strip() or "-",
            "planning_lanes_summary": str(planning_bundle.get("planning_lanes", "")).strip() or "-",
            "planning_lanes": str(planning_bundle.get("planning_lanes", "")).strip() or "-",
            "approved_plan_gate_summary": str(planning_bundle.get("approved_plan_gate", "")).strip() or "-",
            "approved_plan_gate": str(planning_bundle.get("approved_plan_gate", "")).strip() or "-",
            "approved_plan_summary": str(planning_bundle.get("approved_plan", "")).strip() or "-",
            "approved_plan": str(planning_bundle.get("approved_plan", "")).strip() or "-",
            "planner_lane_summary": str(planning_bundle.get("planner_lane", "")).strip() or "-",
            "planner_lane": str(planning_bundle.get("planner_lane", "")).strip() or "-",
            "critic_lane_summary": str(planning_bundle.get("critic_lane", "")).strip() or "-",
            "critic_lane": str(planning_bundle.get("critic_lane", "")).strip() or "-",
            "subagent_contract_summary": str(subagent_surface.get("summary", "")).strip() or str(artifact.get("contract_summary", "")).strip() or "-",
            "subagent_evidence_summary": str(subagent_surface.get("artifact_summary", "")).strip() or str(artifact.get("artifact_summary", "")).strip() or "-",
            "subagent_artifact_path": str(subagent_surface.get("artifact_path", "")).strip() or str(artifact.get("artifact_path", "")).strip() or "-",
            "subagent_gate_summary": str(subagent_surface.get("gate_summary", "")).strip() or str(artifact.get("gate_summary", "")).strip() or "-",
            "subagent_sources": list(artifact.get("sources") or []),
            "subagent_key_findings": list(artifact.get("key_findings") or []),
            "subagent_blocking_issues": list(artifact.get("blocking_issues") or []),
            "subagent_recommended_next_step": str(artifact.get("recommended_next_step", "")).strip() or f"/task {label}",
            "subagent_artifact_refs": list(artifact.get("artifact_refs") or []),
        },
        status=200,
    )


def _execute_worker_update_preview_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
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
                "remediation": "refresh the task list and retry the preview with an existing task ref",
                "outcome": {
                    "kind": "worker_update_preview",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "phase2",
            outcome_kind="worker_apply_propose",
        )
    update_stub = _worker_update_stub_for_task(task)
    proposal_ids = [
        str(item).strip()
        for item in (task.get("background_run_worker_update_proposal_ids") or [])
        if str(item).strip()
    ]
    proposal_summary = worker_task_contract.summarize_worker_update_proposal_summary(update_stub, proposal_ids)
    operator_summary = worker_task_contract.summarize_worker_update_operator_summary(update_stub, proposal_ids)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
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
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current execution rails before previewing an artifact update",
                "outcome": {
                    "kind": "worker_update_preview",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
                "preview": {
                    "kind": "worker_update_preview",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    preference_surface = _build_operator_preference_surface(
        entry=entry,
        alias=alias,
        task_ref=label,
        task=task,
        update_stub=update_stub,
        return_path=str(spec.get("path", "")).strip() or "/control/actions/task/worker-update-preview",
    )
    _save_manager_state(config, manager_state)
    next_step = f"/todo {alias} accept {proposal_ids[0]}" if proposal_ids else f"/task {label}"
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": "inspect the proposed target artifacts and cautions before accepting the worker proposal or mutating any runtime todo",
            "outcome": {
                "kind": "worker_update_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": operator_summary or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "actions": list(preference_surface.get("decision_actions") or []),
            "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
            "applied_preferences": list(preference_surface.get("applied_preferences") or []),
            "preference_candidates": list(preference_surface.get("candidate_preferences") or []),
            "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
            "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
            "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
            "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
            "preference_artifact_kind": str(preference_surface.get("artifact_kind", "")).strip() or "-",
            "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
            "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
            "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
            "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
            "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
            "preview": {
                "kind": "worker_update_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(task.get("background_run_task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(task.get("background_run_worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(update_stub.get("summary_line", "")).strip() or "-",
                "operator_summary": operator_summary or "-",
                "proposal_summary": proposal_summary or "-",
                "proposal_ids": proposal_ids,
                "target_artifacts": list(update_stub.get("target_artifacts") or []),
                "actions": list(update_stub.get("actions") or []),
                "cautions": list(update_stub.get("cautions") or []),
                "evidence_refs": list(update_stub.get("evidence_refs") or []),
                "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
                "preference_artifact_kind": str(preference_surface.get("artifact_kind", "")).strip() or "-",
                "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
                "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
                "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
                "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
                "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
                "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
                "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
                "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
                "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
            },
        },
        status=200,
    )


def _execute_worker_apply_propose_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
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
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry the apply proposal action with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "safe",
            outcome_kind="worker_apply_preview",
        )
    update_stub = _worker_update_stub_for_task(task)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current worker update preview before proposing artifact apply steps",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
                "preview": {
                    "kind": "worker_apply_propose",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=[],
    )
    proposal_payloads = list(preview_payload.get("proposal_payloads") or [])
    if not proposal_payloads:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label}",
                "remediation": "inspect the worker update preview and target artifacts before trying to propose artifact apply steps again",
                "outcome": {
                    "kind": "worker_apply_propose",
                    "status": "blocked",
                    "reason_code": "proposal_payload_missing",
                    "detail": "no artifact apply proposal payloads derived",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    merge_result = todo_state.merge_todo_proposals(
        entry=entry,
        request_id=request_id,
        task=task,
        source_todo_id=str(task.get("source_todo_id", "")).strip(),
        proposals_data=proposal_payloads,
        now_iso=_now_iso,
    )
    proposals_store, _proposal_seq = todo_state.ensure_todo_proposal_store(entry)
    proposal_ids = worker_task_contract.match_worker_update_proposal_ids(
        proposals_store,
        request_id=request_id,
        proposal_payloads=proposal_payloads,
    )
    proposal_summary = worker_task_contract.summarize_worker_artifact_apply_proposal_summary(update_stub, proposal_ids)
    task["background_run_worker_update_proposal_summary"] = proposal_summary
    task["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
    task.setdefault("result", {})
    if isinstance(task.get("result"), dict):
        task["result"]["background_run_worker_update_proposal_summary"] = proposal_summary
        task["result"]["background_run_worker_update_proposal_ids"] = list(proposal_ids or [])
    _save_manager_state(config, manager_state)
    created_ids = [str(item).strip() for item in (merge_result.get("created_ids") or []) if str(item).strip()]
    first_id = created_ids[0] if created_ids else (proposal_ids[0] if proposal_ids else "")
    next_step = f"/todo {alias} accept {first_id}" if first_id else f"/todo {alias} proposals"
    outcome_detail = proposal_summary if proposal_summary not in {"", "-"} else (str(merge_result.get("created_count", 0)) + " proposal(s)")
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": "inspect the apply-oriented worker proposal before accepting it into the runtime todo queue",
            "outcome": {
                "kind": "worker_apply_propose",
                "status": "executed",
                "reason_code": "completed",
                "detail": outcome_detail,
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "proposal": {
                "proposal_ids": proposal_ids,
                "created_ids": created_ids,
                "created_count": int(merge_result.get("created_count", 0) or 0),
                "duplicate_count": int(merge_result.get("duplicate_count", 0) or 0),
                "summary": proposal_summary or "-",
            },
            "preview": {
                "kind": "worker_apply_propose",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": proposal_summary or "-",
                "proposal_payloads": proposal_payloads,
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_worker_apply_preview_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
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
                "remediation": "refresh the task list and retry the artifact-apply preview with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_preview",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    if not _worker_apply_ready(task):
        return _worker_apply_not_ready_response(
            spec=spec,
            alias=alias,
            payload=payload,
            task=task,
            label=label,
            request_id=request_id,
            mode=str(spec.get("mode", "")).strip() or "phase2",
            outcome_kind="worker_apply_accept",
        )
    update_stub = _worker_update_stub_for_task(task)
    if not update_stub or str(update_stub.get("status", "")).strip().lower() in {"", "-", "none"}:
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
                "next_step": f"/task {label}",
                "remediation": "run a bounded worker task first or inspect the current worker update preview before previewing artifact apply",
                "outcome": {
                    "kind": "worker_apply_preview",
                    "status": "blocked",
                    "reason_code": "worker_update_missing",
                    "detail": "worker update stub missing",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    proposal_ids = [
        str(item).strip()
        for item in (task.get("background_run_worker_update_proposal_ids") or [])
        if str(item).strip()
    ]
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=proposal_ids,
    )
    preference_surface = _build_operator_preference_surface(
        entry=entry,
        alias=alias,
        task_ref=label,
        task=task,
        update_stub=update_stub,
        return_path=str(spec.get("path", "")).strip() or "/control/actions/task/worker-apply-preview",
    )
    _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": False,
            "status": "preview",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "safe",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": str(preview_payload.get("next_step", "")).strip() or f"/task {label}",
            "remediation": "inspect the artifact targets and proposal payloads before promoting an artifact-apply proposal into the runtime todo queue",
            "outcome": {
                "kind": "worker_apply_preview",
                "status": "preview",
                "reason_code": "ready",
                "detail": str(preview_payload.get("proposal_summary", "")).strip() or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "actions": list(preference_surface.get("decision_actions") or []),
            "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
            "applied_preferences": list(preference_surface.get("applied_preferences") or []),
            "preference_candidates": list(preference_surface.get("candidate_preferences") or []),
            "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
            "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
            "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
            "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
            "preference_artifact_kind": str(preference_surface.get("artifact_kind", "")).strip() or "-",
            "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
            "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
            "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
            "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
            "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
            "preview": {
                "kind": "worker_apply_preview",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": str(preview_payload.get("proposal_summary", "")).strip() or "-",
                "proposal_ids": list(preview_payload.get("proposal_ids") or []),
                "proposal_payloads": list(preview_payload.get("proposal_payloads") or []),
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
                "preference_decision_groups": list(preference_surface.get("decision_groups") or []),
                "preference_artifact_kind": str(preference_surface.get("artifact_kind", "")).strip() or "-",
                "preference_artifact_profile": str(preference_surface.get("artifact_profile", "")).strip() or "-",
                "preference_preflight_summary": str(preference_surface.get("preflight_summary", "")).strip() or "-",
                "applied_preferences_summary": str(preference_surface.get("applied_preferences_summary", "")).strip() or "-",
                "preference_candidate_summary": str(preference_surface.get("candidate_summary", "")).strip() or "-",
                "preference_candidate_scope_summary": str(preference_surface.get("candidate_scope_summary", "")).strip() or "-",
                "preference_decision_prompt_summary": str(preference_surface.get("decision_prompt_summary", "")).strip() or "-",
                "preference_confirm_summary": str(preference_surface.get("confirm_summary", "")).strip() or "-",
                "preference_manual_summary": str(preference_surface.get("manual_summary", "")).strip() or "-",
                "preference_disabled_summary": str(preference_surface.get("disabled_summary", "")).strip() or "-",
            },
        },
        status=200,
    )


def _execute_worker_apply_accept_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    proposal_ref = str(payload.get("proposal_ref", "")).strip()
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
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": "/control/tasks",
                "remediation": "refresh the task list and retry artifact apply with an existing task ref",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "task_missing",
                    "detail": str(exc),
                },
            },
            status=404,
        )
    alias = _project_alias(entry, key)
    label = str(task.get("short_id", "")).strip() or str(task.get("alias", "")).strip() or request_id
    update_stub = _worker_update_stub_for_task(task)
    preview_payload = _worker_apply_preview_payload(
        alias=alias,
        request_id=request_id,
        label=label,
        task=task,
        update_stub=update_stub,
        proposal_ids=[str(item).strip() for item in (task.get("background_run_worker_update_proposal_ids") or []) if str(item).strip()],
    )
    proposals, _seq = todo_state.ensure_todo_proposal_store(entry)
    proposal = todo_state.find_proposal_by_ref(proposals, proposal_ref)
    if proposal is None:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/task {label} | worker-apply-preview",
                "remediation": "refresh the artifact-apply preview and retry with an open proposal id",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_missing",
                    "detail": f"proposal not found: {proposal_ref or '-'}",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=404,
        )
    proposal_summary = str(proposal.get("summary", "")).strip()
    task_apply_summary = str(task.get("background_run_worker_update_proposal_summary", "")).strip()
    if "apply worker artifact update" not in proposal_summary.lower() and "apply_proposals=" not in task_apply_summary:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an artifact-apply proposal or re-run worker apply propose before accepting it",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_apply",
                    "detail": proposal_summary or "-",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    if todo_state.normalize_proposal_status(proposal.get("status")) != "open":
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an open artifact-apply proposal before accepting it",
                "outcome": {
                    "kind": "worker_apply_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_open",
                    "detail": f"proposal is not open: {str(proposal.get('id', '')).strip() or proposal_ref or '-'}",
                },
                "task": {
                    "request_id": request_id,
                    "label": label,
                    "detail_path": f"/control/tasks/by-request/{request_id}",
                },
            },
            status=409,
        )
    accepted_at = _now_iso()
    result = todo_state.accept_todo_proposal(
        entry=entry,
        proposal=proposal,
        actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
        now=accepted_at,
    )
    _persist_worker_apply_accept_state(
        entry=entry,
        task=task,
        request_id=request_id,
        update_stub=update_stub,
        preview_payload=preview_payload,
        result=result,
        accepted_at=accepted_at,
    )
    _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": f"/todo {alias}",
            "remediation": "inspect the promoted artifact-apply todo row and syncback posture before applying another worker artifact update",
            "outcome": {
                "kind": "worker_apply_accept",
                "status": "executed",
                "reason_code": "completed",
                "detail": str(result.get("summary", "")).strip() or proposal_summary or "-",
            },
            "task": {
                "request_id": request_id,
                "label": label,
                "detail_path": f"/control/tasks/by-request/{request_id}",
            },
            "proposal": {
                "proposal_id": str(result.get("proposal_id", "")).strip() or str(proposal.get("id", "")).strip() or "-",
                "summary": str(result.get("summary", "")).strip() or proposal_summary or "-",
                "created_new": bool(result.get("created_new", False)),
                "todo_id": str(result.get("todo_id", "")).strip() or "-",
                "reason": str(result.get("reason", "")).strip() or "-",
            },
            "preview": {
                "kind": "worker_apply_accept",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
                "detail_path": f"/control/tasks/by-request/{request_id}",
                "task_contract_summary": str(preview_payload.get("task_contract_summary", "")).strip() or "-",
                "worker_result_summary": str(preview_payload.get("worker_result_summary", "")).strip() or "-",
                "update_stub_summary": str(preview_payload.get("update_stub_summary", "")).strip() or "-",
                "proposal_summary": str(preview_payload.get("proposal_summary", "")).strip() or "-",
                "target_artifacts": list(preview_payload.get("target_artifacts") or []),
                "actions": list(preview_payload.get("actions") or []),
                "cautions": list(preview_payload.get("cautions") or []),
                "evidence_refs": list(preview_payload.get("evidence_refs") or []),
            },
        },
        status=200,
    )


def _execute_todo_proposal_action(
    spec: Dict[str, object],
    *,
    config: DashboardAppConfig,
    reject: bool = False,
) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    project_ref = str(payload.get("project_ref", "")).strip()
    proposal_ref = str(payload.get("proposal_ref", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    _paths, manager_state = _load_dashboard_manager_state(config)
    key, entry = _resolve_runtime_entry(manager_state=manager_state, project_ref=project_ref)
    alias = _project_alias(entry, key)
    proposals, _seq = todo_state.ensure_todo_proposal_store(entry)
    proposal = todo_state.find_proposal_by_ref(proposals, proposal_ref)
    if proposal is None:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "refresh the proposal inbox and re-run the action with an open proposal id",
                "outcome": {
                    "kind": "todo_proposal_reject" if reject else "todo_proposal_accept",
                    "status": "blocked",
                    "reason_code": "proposal_missing",
                    "detail": f"proposal not found: {proposal_ref or '-'}",
                },
                "preview": {
                    "kind": "todo_proposal",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=404,
        )
    if todo_state.normalize_proposal_status(proposal.get("status")) != "open":
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": str(spec.get("path", "")).strip() or "-",
                "mode": str(spec.get("mode", "")).strip() or "phase2",
                "source_command": str(spec.get("command", "")).strip() or "-",
                "payload": payload,
                "next_step": f"/todo {alias} proposals",
                "remediation": "pick an open proposal or inspect the existing todo queue before applying another worker update",
                "outcome": {
                    "kind": "todo_proposal_reject" if reject else "todo_proposal_accept",
                    "status": "blocked",
                    "reason_code": "proposal_not_open",
                    "detail": f"proposal is not open: {str(proposal.get('id', '')).strip() or proposal_ref or '-'}",
                },
                "preview": {
                    "kind": "todo_proposal",
                    "project_alias": alias,
                    "runtime_path": _runtime_action_link(alias),
                },
            },
            status=409,
        )
    now = _now_iso()
    if reject:
        result = todo_state.reject_todo_proposal(
            entry=entry,
            proposal=proposal,
            actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
            now=now,
            reason=reason,
        )
        outcome_kind = "todo_proposal_reject"
        next_step = f"/todo {alias} proposals"
        remediation = "inspect remaining open proposals before rejecting another worker suggestion"
    else:
        result = todo_state.accept_todo_proposal(
            entry=entry,
            proposal=proposal,
            actor=f"dashboard:{_DASHBOARD_CHAT_ID}",
            now=now,
        )
        outcome_kind = "todo_proposal_accept"
        next_step = f"/todo {alias}"
        remediation = "inspect the promoted todo row and syncback posture before applying another worker proposal"
    _save_manager_state(config, manager_state)
    return _json(
        {
            "ok": True,
            "implemented": True,
            "executed": True,
            "status": "executed",
            "method": "POST",
            "path": str(spec.get("path", "")).strip() or "-",
            "mode": str(spec.get("mode", "")).strip() or "phase2",
            "source_command": str(spec.get("command", "")).strip() or "-",
            "payload": payload,
            "next_step": next_step,
            "remediation": remediation,
            "outcome": {
                "kind": outcome_kind,
                "status": "executed",
                "reason_code": "completed",
                "detail": str(result.get("summary", "")).strip() or "-",
            },
            "proposal": {
                "proposal_id": str(result.get("proposal_id", "")).strip() or str(proposal.get("id", "")).strip() or "-",
                "summary": str(result.get("summary", "")).strip() or str(proposal.get("summary", "")).strip() or "-",
                "created_new": bool(result.get("created_new", False)),
                "todo_id": str(result.get("todo_id", "")).strip() or "-",
                "reason": str(result.get("reason", "")).strip() or "-",
            },
            "preview": {
                "kind": "todo_proposal",
                "project_alias": alias,
                "runtime_path": _runtime_action_link(alias),
            },
        },
        status=200,
    )
