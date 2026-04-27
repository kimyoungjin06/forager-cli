#!/usr/bin/env python3
"""Structured operator preference registry for adaptive artifact preflight."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import aoe_tg_runtime_core as runtime_core


PREFERENCE_REGISTRY_VERSION = "2026-04-22.v1"
PREFERENCE_CANDIDATE_VERSION = "2026-04-22.v1"

PREFERENCE_KINDS = {"preference", "correction_rule", "style_default", "avoidance_rule"}
PREFERENCE_SCOPES = {"session", "artifact_kind", "project", "user_global"}
PREFERENCE_PROMPT_MODES = {"auto", "confirm", "manual_only"}
PREFERENCE_CONFIDENCE = {"explicit", "repeated", "inferred"}
PREFERENCE_SOURCES = {"explicit_user", "repeated_correction", "operator_promoted", "seeded_default", "request_decision"}
PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD = 2
PREFERENCE_DECISION_CHOICES = {
    "apply_once",
    "apply_always",
    "skip_once",
    "skip_always",
}
PREFERENCE_PERSIST_MODES = {"none", "enable", "disable"}

PREFERENCE_DECISION_OPTION_ROWS = (
    {
        "choice": "apply_once",
        "label": "이번만 적용",
        "apply_now": True,
        "persist_mode": "none",
    },
    {
        "choice": "apply_always",
        "label": "앞으로도 적용",
        "apply_now": True,
        "persist_mode": "enable",
    },
    {
        "choice": "skip_once",
        "label": "이번만 제외",
        "apply_now": False,
        "persist_mode": "none",
    },
    {
        "choice": "skip_always",
        "label": "앞으로 제외",
        "apply_now": False,
        "persist_mode": "disable",
    },
)

PREFERENCE_SEEDED_DEFAULT_ROWS = {
    "chart": (
        {
            "key": "legend_position",
            "value": "bottom",
            "description": "Ask whether the legend should move below the chart before applying a crowded chart revision.",
            "kind": "style_default",
            "prompt_mode": "confirm",
        },
        {
            "key": "show_source_note",
            "value": True,
            "description": "Consider adding a source note below the chart when the evidence origin matters.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
        {
            "key": "color_palette",
            "value": "accessible",
            "description": "Consider an accessible palette when chart readability may be at risk.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
    "document": (
        {
            "key": "preserve_heading_structure",
            "value": True,
            "description": "Confirm whether the existing heading structure should be preserved before rewriting the document.",
            "kind": "avoidance_rule",
            "prompt_mode": "confirm",
        },
        {
            "key": "explicit_open_questions_section",
            "value": True,
            "description": "Consider collecting unresolved risks or open questions in a dedicated section.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
    "spreadsheet": (
        {
            "key": "preserve_formula_cells",
            "value": True,
            "description": "Confirm whether formula cells should be preserved before editing a spreadsheet artifact.",
            "kind": "avoidance_rule",
            "prompt_mode": "confirm",
        },
        {
            "key": "freeze_header_row",
            "value": True,
            "description": "Consider freezing the header row when the sheet is meant for repeated operator review.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
}

PREFERENCE_SEEDED_PROFILE_ROWS = {
    "chart_bar": (
        {
            "key": "show_bar_value_labels",
            "value": True,
            "description": "Consider showing value labels when a bar chart is being tuned for quick operator review.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
        {
            "key": "category_order",
            "value": "descending",
            "description": "Confirm whether categories should be sorted to make the bar ranking easier to scan.",
            "kind": "style_default",
            "prompt_mode": "confirm",
        },
    ),
    "chart_line": (
        {
            "key": "highlight_latest_point",
            "value": True,
            "description": "Consider highlighting the latest point when the line chart is meant to show trend continuation.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
        {
            "key": "time_axis_granularity",
            "value": "preserve_source",
            "description": "Confirm whether the time axis granularity should stay aligned with the source series.",
            "kind": "avoidance_rule",
            "prompt_mode": "confirm",
        },
    ),
    "document_brief": (
        {
            "key": "frontload_recommendation",
            "value": True,
            "description": "Consider leading with the recommendation or conclusion when the artifact is a brief.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
    "document_report": (
        {
            "key": "preserve_evidence_sections",
            "value": True,
            "description": "Confirm whether evidence and findings sections should stay distinct in a report-style document.",
            "kind": "avoidance_rule",
            "prompt_mode": "confirm",
        },
    ),
    "spreadsheet_model": (
        {
            "key": "highlight_input_cells",
            "value": True,
            "description": "Consider highlighting input cells separately when the spreadsheet behaves like a model.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
    "spreadsheet_tracker": (
        {
            "key": "freeze_status_columns",
            "value": True,
            "description": "Consider freezing leading status columns when the sheet is an operational tracker.",
            "kind": "style_default",
            "prompt_mode": "manual_only",
        },
    ),
}


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _normalize_text(raw: Any, limit: int = 240) -> str:
    return " ".join(str(raw or "").strip().split())[: max(0, int(limit or 0))]


def _normalize_iso(raw: Any) -> str:
    return _trim(raw, 64)


def _normalize_value(raw: Any) -> Any:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, list):
        out: List[Any] = []
        for item in raw:
            value = _normalize_value(item)
            if value not in out:
                out.append(value)
        return out[:8]
    if isinstance(raw, dict):
        out: Dict[str, Any] = {}
        for key, value in raw.items():
            token = _trim(key, 64)
            if not token:
                continue
            out[token] = _normalize_value(value)
            if len(out) >= 8:
                break
        return out
    return _trim(raw, 160)


def _value_label(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return ",".join(_trim(item, 48) for item in value if _trim(item, 48)) or "-"
    if isinstance(value, dict):
        parts = [f"{_trim(key, 32)}={_trim(val, 48)}" for key, val in value.items()]
        return ",".join(parts) or "-"
    return _trim(value, 120) or "-"


def _has_preference_value(value: Any) -> bool:
    if value is None:
        return False
    if value == "":
        return False
    if value == []:
        return False
    if value == {}:
        return False
    return True


def _normalize_scope(scope: Any, *, artifact_kind: str = "") -> Tuple[str, str]:
    token = _trim(scope, 32).lower()
    if token not in PREFERENCE_SCOPES:
        token = "user_global"
    if token == "artifact_kind":
        return token, _trim(artifact_kind, 64).lower() or "*"
    if token == "user_global":
        return token, "*"
    return token, ""


def _decision_option_row(choice: str) -> Dict[str, Any]:
    token = _trim(choice, 32).lower()
    for row in PREFERENCE_DECISION_OPTION_ROWS:
        if row["choice"] == token:
            return dict(row)
    return dict(PREFERENCE_DECISION_OPTION_ROWS[0])


def preference_decision_options() -> List[Dict[str, Any]]:
    return [dict(row) for row in PREFERENCE_DECISION_OPTION_ROWS]


def seeded_preference_rules(*, artifact_kind: Any, artifact_profile: Any = "") -> List[Dict[str, Any]]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    profile = _trim(artifact_profile, 64).lower()
    rows = [
        *list(PREFERENCE_SEEDED_DEFAULT_ROWS.get(artifact) or []),
        *list(PREFERENCE_SEEDED_PROFILE_ROWS.get(profile) or []),
    ]
    normalized_by_key: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []
    for item in rows:
        rule = normalize_preference_rule(
            {
                **dict(item),
                "artifact_kind": artifact,
                "scope": "user_global",
                "scope_ref": "*",
                "enabled": True,
                "source": "seeded_default",
                "confidence": "inferred",
            }
        )
        key = _trim(rule.get("key"), 96).lower()
        if not rule or not key:
            continue
        if key not in ordered_keys:
            ordered_keys.append(key)
        normalized_by_key[key] = rule
    return [normalized_by_key[key] for key in ordered_keys if key in normalized_by_key]


def normalize_preference_rule(raw: Any, *, now_iso: str = "") -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    artifact_kind = _trim(row.get("artifact_kind"), 64).lower() or "generic"
    kind = _trim(row.get("kind"), 32).lower()
    if kind not in PREFERENCE_KINDS:
        kind = "preference"
    prompt_mode = _trim(row.get("prompt_mode"), 32).lower()
    if prompt_mode not in PREFERENCE_PROMPT_MODES:
        prompt_mode = "confirm"
    scope = _trim(row.get("scope"), 32).lower()
    scope_ref = _trim(row.get("scope_ref"), 64)
    if scope not in PREFERENCE_SCOPES:
        if scope_ref and scope_ref != "*":
            scope = "project"
        else:
            scope = "user_global"
    if scope == "artifact_kind":
        scope_ref = artifact_kind
    elif scope == "user_global":
        scope_ref = "*"
    elif scope == "project":
        scope_ref = scope_ref or "*"
    confidence = _trim(row.get("confidence"), 32).lower()
    if confidence not in PREFERENCE_CONFIDENCE:
        confidence = "explicit"
    source = _trim(row.get("source"), 32).lower()
    if source not in PREFERENCE_SOURCES:
        source = "explicit_user"
    key = _trim(row.get("key"), 96).lower()
    if not key:
        key = _trim(row.get("id"), 96).lower()
    if not key:
        return {}
    created_at = _normalize_iso(row.get("created_at")) or now_iso
    updated_at = _normalize_iso(row.get("updated_at")) or created_at or now_iso
    return {
        "id": _trim(row.get("id"), 128) or f"{artifact_kind}:{scope}:{scope_ref}:{key}",
        "key": key,
        "artifact_kind": artifact_kind,
        "kind": kind,
        "scope": scope,
        "scope_ref": scope_ref,
        "value": _normalize_value(row.get("value")),
        "enabled": bool(row.get("enabled", True)),
        "prompt_mode": prompt_mode,
        "description": _normalize_text(row.get("description"), 240) or key,
        "source": source,
        "confidence": confidence,
        "promotion_reason": _normalize_text(row.get("promotion_reason"), 240),
        "last_confirmed_at": _normalize_iso(row.get("last_confirmed_at")) or updated_at,
        "review_after": _normalize_iso(row.get("review_after")),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def summarize_preference_rule(raw: Any) -> str:
    row = normalize_preference_rule(raw)
    if not row:
        return "-"
    state = "on" if row.get("enabled", False) else "off"
    prompt_mode = _trim(row.get("prompt_mode"), 16) or "-"
    scope = _trim(row.get("scope"), 16) or "-"
    scope_ref = _trim(row.get("scope_ref"), 32) or "-"
    if scope_ref == "*":
        scope_ref = scope
    return (
        f"{_trim(row.get('key'), 72) or '-'}={_value_label(row.get('value'))}"
        f" | {state} | {prompt_mode} | {scope}:{scope_ref}"
    )


def normalize_preference_candidate(raw: Any, *, now_iso: str = "") -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    artifact_kind = _trim(row.get("artifact_kind"), 64).lower() or "generic"
    key = _trim(row.get("key"), 96).lower()
    if not key:
        return {}
    source_refs = row.get("source_refs") if isinstance(row.get("source_refs"), list) else []
    normalized_refs = []
    for item in source_refs:
        token = _trim(item, 160)
        if token and token not in normalized_refs:
            normalized_refs.append(token)
    created_at = _normalize_iso(row.get("created_at")) or now_iso
    updated_at = _normalize_iso(row.get("updated_at")) or created_at or now_iso
    prompt_mode = _trim(row.get("suggested_prompt_mode"), 32).lower()
    if prompt_mode not in PREFERENCE_PROMPT_MODES:
        prompt_mode = "confirm"
    return {
        "id": _trim(row.get("id"), 128) or f"{artifact_kind}:{key}:{_trim(row.get('project_ref'), 64) or '*'}",
        "artifact_kind": artifact_kind,
        "key": key,
        "project_ref": _trim(row.get("project_ref"), 64),
        "suggested_value": _normalize_value(row.get("suggested_value")),
        "issue": _normalize_text(row.get("issue"), 240),
        "suggested_prompt_mode": prompt_mode,
        "occurrence_count": max(1, int(row.get("occurrence_count", 1) or 1)),
        "source_refs": normalized_refs[:8],
        "created_at": created_at,
        "updated_at": updated_at,
    }


def preference_candidate_scope(*, artifact_kind: Any, project_ref: Any = "") -> Tuple[str, str]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    project = _trim(project_ref, 64)
    if project and project != "*":
        return "project", project
    return "artifact_kind", artifact or "*"


def summarize_preference_candidate(raw: Any) -> str:
    row = normalize_preference_candidate(raw)
    if not row:
        return "-"
    return (
        f"{_trim(row.get('key'), 72) or '-'}={_value_label(row.get('suggested_value'))}"
        f" | hits={int(row.get('occurrence_count', 0) or 0)}"
        f" | issue={_trim(row.get('issue'), 96) or '-'}"
    )


def normalize_preference_state(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    rules = []
    for item in list(row.get("rules") or []):
        normalized = normalize_preference_rule(item)
        if normalized and all(existing.get("id") != normalized.get("id") for existing in rules):
            rules.append(normalized)
    return {
        "version": _trim(row.get("version"), 64) or PREFERENCE_REGISTRY_VERSION,
        "rules": rules,
    }


def normalize_preference_candidates_state(raw: Any) -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    candidates = []
    for item in list(row.get("candidates") or []):
        normalized = normalize_preference_candidate(item)
        if normalized and all(existing.get("id") != normalized.get("id") for existing in candidates):
            candidates.append(normalized)
    return {
        "version": _trim(row.get("version"), 64) or PREFERENCE_CANDIDATE_VERSION,
        "candidates": candidates,
    }


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_operator_preferences(team_dir: Any) -> Dict[str, Any]:
    path = runtime_core.operator_preferences_path(team_dir)
    return normalize_preference_state(_read_json_file(path))


def save_operator_preferences(team_dir: Any, state: Any) -> Path:
    path = runtime_core.operator_preferences_path(team_dir)
    normalized = normalize_preference_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_operator_preference_candidates(team_dir: Any) -> Dict[str, Any]:
    path = runtime_core.operator_preference_candidates_path(team_dir)
    return normalize_preference_candidates_state(_read_json_file(path))


def save_operator_preference_candidates(team_dir: Any, state: Any) -> Path:
    path = runtime_core.operator_preference_candidates_path(team_dir)
    normalized = normalize_preference_candidates_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def record_preference_candidate(
    team_dir: Any,
    *,
    artifact_kind: Any,
    key: Any,
    suggested_value: Any,
    issue: Any,
    project_ref: Any = "",
    source_ref: Any = "",
    suggested_prompt_mode: Any = "confirm",
    now_iso: str = "",
) -> Dict[str, Any]:
    state = load_operator_preference_candidates(team_dir)
    candidates = list(state.get("candidates") or [])
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    token = _trim(key, 96).lower()
    project_token = _trim(project_ref, 64)
    existing_index = -1
    for index, row in enumerate(candidates):
        if (
            _trim(row.get("artifact_kind"), 64).lower() == artifact
            and _trim(row.get("key"), 96).lower() == token
            and _trim(row.get("project_ref"), 64) == project_token
        ):
            existing_index = index
            break
    if existing_index >= 0:
        row = dict(candidates[existing_index])
        row["occurrence_count"] = max(1, int(row.get("occurrence_count", 1) or 1)) + 1
        refs = list(row.get("source_refs") or [])
        if _trim(source_ref, 160) and _trim(source_ref, 160) not in refs:
            refs.append(_trim(source_ref, 160))
        row["source_refs"] = refs[:8]
        row["suggested_value"] = _normalize_value(suggested_value)
        row["issue"] = _normalize_text(issue, 240) or _normalize_text(row.get("issue"), 240)
        row["suggested_prompt_mode"] = _trim(suggested_prompt_mode, 32).lower() or row.get("suggested_prompt_mode", "confirm")
        row["updated_at"] = now_iso or _normalize_iso(row.get("updated_at"))
        normalized = normalize_preference_candidate(row, now_iso=now_iso)
        candidates[existing_index] = normalized
    else:
        candidate = normalize_preference_candidate(
            {
                "artifact_kind": artifact,
                "key": token,
                "project_ref": project_token,
                "suggested_value": suggested_value,
                "issue": issue,
                "suggested_prompt_mode": suggested_prompt_mode,
                "occurrence_count": 1,
                "source_refs": [_trim(source_ref, 160)] if _trim(source_ref, 160) else [],
            },
            now_iso=now_iso,
        )
        normalized = candidate
        candidates.append(candidate)
    state["candidates"] = candidates
    save_operator_preference_candidates(team_dir, state)
    return normalized


def _matches_candidate_scope(candidate: Dict[str, Any], *, artifact_kind: str, project_ref: str) -> bool:
    if _trim(candidate.get("artifact_kind"), 64).lower() not in {artifact_kind, "*"}:
        return False
    candidate_project = _trim(candidate.get("project_ref"), 64)
    if candidate_project in {"", "*"}:
        return True
    return bool(project_ref) and candidate_project == project_ref


def list_applicable_preference_candidates(
    state: Any,
    *,
    artifact_kind: Any,
    project_ref: Any = "",
) -> List[Dict[str, Any]]:
    candidates_state = normalize_preference_candidates_state(state)
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    project_token = _trim(project_ref, 64)
    matched: List[Dict[str, Any]] = []
    for item in list(candidates_state.get("candidates") or []):
        candidate = normalize_preference_candidate(item)
        if not candidate:
            continue
        if not _matches_candidate_scope(candidate, artifact_kind=artifact, project_ref=project_token):
            continue
        matched.append(candidate)
    matched.sort(
        key=lambda row: (
            -max(0, int(row.get("occurrence_count", 0) or 0)),
            _trim(row.get("key"), 96).lower(),
        )
    )
    return matched


def normalize_preference_decision(raw: Any, *, now_iso: str = "") -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    choice = _trim(row.get("choice"), 32).lower()
    if choice not in PREFERENCE_DECISION_CHOICES:
        apply_now = bool(row.get("apply_now", True))
        persist_mode = _trim(row.get("persist_mode"), 16).lower()
        if persist_mode not in PREFERENCE_PERSIST_MODES:
            persist_mode = "none"
        if apply_now and persist_mode == "enable":
            choice = "apply_always"
        elif apply_now:
            choice = "apply_once"
        elif persist_mode == "disable":
            choice = "skip_always"
        else:
            choice = "skip_once"
    option = _decision_option_row(choice)
    artifact_kind = _trim(row.get("artifact_kind"), 64).lower() or "generic"
    scope = _trim(row.get("scope"), 32).lower()
    if scope not in PREFERENCE_SCOPES:
        scope = "artifact_kind" if option["persist_mode"] != "none" else "session"
    scope_ref = _trim(row.get("scope_ref"), 64)
    if scope == "artifact_kind":
        scope_ref = artifact_kind
    elif scope == "user_global":
        scope_ref = "*"
    elif scope == "session":
        scope_ref = "-"
    elif scope == "project":
        scope_ref = scope_ref or "*"
    key = _trim(row.get("key"), 96).lower()
    if not key:
        return {}
    return {
        "choice": choice,
        "label": option["label"],
        "apply_now": bool(option["apply_now"]),
        "persist_mode": option["persist_mode"],
        "scope": scope,
        "scope_ref": scope_ref,
        "artifact_kind": artifact_kind,
        "key": key,
        "value": _normalize_value(row.get("value")),
        "description": _normalize_text(row.get("description"), 240) or key,
        "decided_at": _normalize_iso(row.get("decided_at")) or now_iso,
    }


def summarize_preference_decision(raw: Any) -> str:
    row = normalize_preference_decision(raw)
    if not row:
        return "-"
    scope = _trim(row.get("scope"), 16) or "-"
    scope_ref = _trim(row.get("scope_ref"), 32) or "-"
    return (
        f"{_trim(row.get('key'), 72) or '-'}={_value_label(row.get('value'))}"
        f" | {row.get('label', '-')}"
        f" | {scope}:{scope_ref}"
    )


def _matches_scope(rule: Dict[str, Any], *, artifact_kind: str, project_ref: str) -> bool:
    if _trim(rule.get("artifact_kind"), 64).lower() not in {artifact_kind, "*"}:
        return False
    scope = _trim(rule.get("scope"), 32).lower()
    scope_ref = _trim(rule.get("scope_ref"), 64)
    if scope == "session":
        return scope_ref in {"", "-", "*"}
    if scope == "user_global":
        return True
    if scope == "artifact_kind":
        return scope_ref in {"*", artifact_kind}
    if scope == "project":
        return bool(project_ref) and scope_ref in {"*", project_ref}
    return False


def list_applicable_preferences(
    state: Any,
    *,
    artifact_kind: Any,
    project_ref: Any = "",
    include_disabled: bool = True,
) -> List[Dict[str, Any]]:
    registry = normalize_preference_state(state)
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    project_token = _trim(project_ref, 64)
    matched: List[Dict[str, Any]] = []
    for item in list(registry.get("rules") or []):
        rule = normalize_preference_rule(item)
        if not rule:
            continue
        if not include_disabled and not bool(rule.get("enabled", False)):
            continue
        if not _matches_scope(rule, artifact_kind=artifact, project_ref=project_token):
            continue
        matched.append(rule)
    matched.sort(
        key=lambda row: (
            {"session": 0, "project": 1, "artifact_kind": 2, "user_global": 3}.get(
                _trim(row.get("scope"), 32).lower(),
                9,
            ),
            {"auto": 0, "confirm": 1, "manual_only": 2}.get(str(row.get("prompt_mode", "")).strip(), 9),
            0 if bool(row.get("enabled", False)) else 1,
            str(row.get("key", "")).strip(),
        )
    )
    effective: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in matched:
        key = _trim(row.get("key"), 96).lower()
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        effective.append(row)
    return effective


def build_adaptive_preference_preflight(
    state: Any,
    *,
    artifact_kind: Any,
    artifact_profile: Any = "",
    project_ref: Any = "",
) -> Dict[str, Any]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    profile = _trim(artifact_profile, 64).lower()
    project_token = _trim(project_ref, 64)
    auto_apply: List[Dict[str, Any]] = []
    confirm: List[Dict[str, Any]] = []
    manual_only: List[Dict[str, Any]] = []
    disabled_defaults: List[Dict[str, Any]] = []
    explicit_rules = list_applicable_preferences(state, artifact_kind=artifact, project_ref=project_token, include_disabled=True)
    seeded_rules = [
        row
        for row in seeded_preference_rules(artifact_kind=artifact, artifact_profile=profile)
        if _trim(row.get("key"), 96).lower() not in {
            _trim(existing.get("key"), 96).lower() for existing in explicit_rules
        }
    ]
    for rule in [*explicit_rules, *seeded_rules]:
        row = {
            "key": _trim(rule.get("key"), 96).lower(),
            "description": _normalize_text(rule.get("description"), 240) or _trim(rule.get("key"), 96),
            "value": rule.get("value"),
            "summary": summarize_preference_rule(rule),
            "scope": _trim(rule.get("scope"), 32).lower(),
            "scope_ref": _trim(rule.get("scope_ref"), 64),
            "artifact_kind": artifact,
            "options": preference_decision_options(),
        }
        if not bool(rule.get("enabled", False)):
            disabled_defaults.append(row)
        elif _trim(rule.get("prompt_mode"), 32).lower() == "auto":
            auto_apply.append(row)
        elif _trim(rule.get("prompt_mode"), 32).lower() == "manual_only":
            manual_only.append(row)
        else:
            confirm.append(row)
    return {
        "artifact_kind": artifact,
        "artifact_profile": profile,
        "project_ref": project_token,
        "auto_apply": auto_apply,
        "confirm": confirm,
        "manual_only": manual_only,
        "disabled_defaults": disabled_defaults,
    }


def summarize_preference_preflight(raw: Any) -> str:
    row = raw if isinstance(raw, dict) else {}
    if not row:
        return "-"
    profile = _trim(row.get("artifact_profile"), 32)
    return (
        f"preflight={_trim(row.get('artifact_kind'), 32) or '-'}"
        + (f" | profile={profile}" if profile else "")
        + f" | auto={len(list(row.get('auto_apply') or []))}"
        + f" | confirm={len(list(row.get('confirm') or []))}"
        + f" | manual={len(list(row.get('manual_only') or []))}"
        + f" | disabled={len(list(row.get('disabled_defaults') or []))}"
    )


def summarize_applied_preferences(raw: Any) -> str:
    rows = raw if isinstance(raw, list) else []
    labels = [summarize_preference_rule(item) for item in rows]
    labels = [item for item in labels if item not in {"", "-"}]
    if not labels:
        return "-"
    return "applied_preferences=" + " || ".join(labels[:3])


def summarize_preference_decisions(raw: Any) -> str:
    rows = raw if isinstance(raw, list) else []
    labels = [summarize_preference_decision(item) for item in rows]
    labels = [item for item in labels if item not in {"", "-"}]
    if not labels:
        return "-"
    return "preference_decisions=" + " || ".join(labels[:3])


def build_preference_candidate_recommendations(
    candidate_state: Any,
    *,
    preference_state: Any,
    artifact_kind: Any,
    project_ref: Any = "",
    min_occurrence_count: int = PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD,
) -> List[Dict[str, Any]]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    project_token = _trim(project_ref, 64)
    threshold = max(1, int(min_occurrence_count or PREFERENCE_CANDIDATE_PROMOTION_THRESHOLD))
    existing_keys = {
        _trim(row.get("key"), 96).lower()
        for row in list_applicable_preferences(
            preference_state,
            artifact_kind=artifact,
            project_ref=project_token,
            include_disabled=True,
        )
        if _trim(row.get("key"), 96)
    }
    recommendations: List[Dict[str, Any]] = []
    for candidate in list_applicable_preference_candidates(
        candidate_state,
        artifact_kind=artifact,
        project_ref=project_token,
    ):
        key = _trim(candidate.get("key"), 96).lower()
        if not key or key in existing_keys:
            continue
        hits = max(0, int(candidate.get("occurrence_count", 0) or 0))
        if hits < threshold:
            continue
        candidate_project_ref = _trim(candidate.get("project_ref"), 64)
        candidate_scope, candidate_scope_ref = preference_candidate_scope(
            artifact_kind=artifact,
            project_ref=candidate_project_ref,
        )
        recommendations.append(
            {
                "key": key,
                "description": _normalize_text(candidate.get("issue"), 240) or key,
                "value": candidate.get("suggested_value"),
                "suggested_value": candidate.get("suggested_value"),
                "summary": summarize_preference_candidate(candidate),
                "scope": candidate_scope,
                "scope_ref": candidate_scope_ref,
                "artifact_kind": artifact,
                "occurrence_count": hits,
                "issue": _normalize_text(candidate.get("issue"), 240),
                "project_ref": candidate_project_ref,
                "expected_scope": candidate_scope,
                "expected_scope_ref": candidate_scope_ref,
                "source_refs": list(candidate.get("source_refs") or [])[:8],
                "options": preference_decision_options(),
            }
        )
    return recommendations


def summarize_preference_candidates(raw: Any) -> str:
    rows = raw if isinstance(raw, list) else []
    labels = [summarize_preference_candidate(item) for item in rows]
    labels = [item for item in labels if item not in {"", "-"}]
    if not labels:
        return "-"
    return "preference_candidates=" + " || ".join(labels[:3])


def summarize_preference_candidate_scopes(raw: Any) -> str:
    rows = raw if isinstance(raw, list) else []
    labels: List[str] = []
    for item in rows:
        row = item if isinstance(item, dict) else {}
        key = _trim(row.get("key"), 96).lower()
        expected_scope = _trim(row.get("expected_scope"), 32).lower() or _trim(row.get("scope"), 32).lower()
        expected_scope_ref = _trim(row.get("expected_scope_ref"), 64) or _trim(row.get("scope_ref"), 64)
        if not expected_scope:
            expected_scope, expected_scope_ref = preference_candidate_scope(
                artifact_kind=row.get("artifact_kind"),
                project_ref=row.get("project_ref"),
            )
        if expected_scope == "session":
            scope_label = "session"
        elif expected_scope_ref and expected_scope_ref not in {"-", "*"}:
            scope_label = f"{expected_scope}:{expected_scope_ref}"
        else:
            scope_label = expected_scope or "-"
        label = f"{key}:{scope_label}" if key else scope_label
        if label not in labels and label not in {"", "-"}:
            labels.append(label)
    if not labels:
        return "-"
    return "preference_candidate_scopes=" + " || ".join(labels[:3])


def _upsert_preference_rule(
    state: Dict[str, Any],
    *,
    key: str,
    artifact_kind: str,
    scope: str,
    scope_ref: str,
    value: Any,
    description: str,
    enabled: bool,
    prompt_mode: str = "confirm",
    source: str = "request_decision",
    confidence: str = "",
    promotion_reason: str = "",
    now_iso: str = "",
) -> Dict[str, Any]:
    rules = list(state.get("rules") or [])
    existing_index = -1
    for index, raw_rule in enumerate(rules):
        if (
            _trim(raw_rule.get("key"), 96).lower() == key
            and _trim(raw_rule.get("artifact_kind"), 64).lower() == artifact_kind
            and _trim(raw_rule.get("scope"), 32).lower() == scope
            and _trim(raw_rule.get("scope_ref"), 64) == scope_ref
        ):
            existing_index = index
            break
    existing = dict(rules[existing_index]) if existing_index >= 0 else {}
    normalized = normalize_preference_rule(
        {
            **existing,
            "key": key,
            "artifact_kind": artifact_kind,
            "scope": scope,
            "scope_ref": scope_ref,
            "value": value if _has_preference_value(value) else existing.get("value"),
            "description": description or existing.get("description"),
            "enabled": enabled,
            "prompt_mode": _trim(prompt_mode, 32).lower() or existing.get("prompt_mode", "confirm"),
            "source": _trim(source, 32).lower() or existing.get("source", "request_decision"),
            "confidence": _trim(confidence, 32).lower() or existing.get("confidence", "explicit"),
            "promotion_reason": _normalize_text(promotion_reason, 240) or existing.get("promotion_reason", ""),
            "last_confirmed_at": now_iso or existing.get("last_confirmed_at", ""),
            "created_at": existing.get("created_at", now_iso),
            "updated_at": now_iso or existing.get("updated_at", ""),
        },
        now_iso=now_iso,
    )
    if existing_index >= 0:
        rules[existing_index] = normalized
    else:
        rules.append(normalized)
    state["rules"] = rules
    return normalized


def upsert_operator_preference_rule(
    team_dir: Any,
    *,
    key: Any,
    artifact_kind: Any,
    scope: Any = "artifact_kind",
    scope_ref: Any = "",
    value: Any = None,
    description: Any = "",
    enabled: bool = True,
    prompt_mode: Any = "confirm",
    source: Any = "explicit_user",
    confidence: Any = "explicit",
    promotion_reason: Any = "",
    now_iso: str = "",
) -> Dict[str, Any]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    token = _trim(key, 96).lower()
    if not token:
        return {}
    normalized_scope, normalized_scope_ref = _normalize_scope(
        scope,
        artifact_kind=artifact,
    )
    if normalized_scope == "project":
        normalized_scope_ref = _trim(scope_ref, 64) or "*"
    state = load_operator_preferences(team_dir)
    normalized = _upsert_preference_rule(
        state,
        key=token,
        artifact_kind=artifact,
        scope=normalized_scope,
        scope_ref=normalized_scope_ref,
        value=value,
        description=_normalize_text(description, 240) or token,
        enabled=bool(enabled),
        prompt_mode=_trim(prompt_mode, 32).lower() or "confirm",
        source=_trim(source, 32).lower() or "explicit_user",
        confidence=_trim(confidence, 32).lower() or "explicit",
        promotion_reason=_normalize_text(promotion_reason, 240),
        now_iso=now_iso,
    )
    save_operator_preferences(team_dir, state)
    return normalized


def delete_operator_preference_rule(
    team_dir: Any,
    *,
    key: Any,
    artifact_kind: Any,
    scope: Any,
    scope_ref: Any = "",
) -> Dict[str, Any]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    token = _trim(key, 96).lower()
    normalized_scope, normalized_scope_ref = _normalize_scope(
        scope,
        artifact_kind=artifact,
    )
    if normalized_scope == "project":
        normalized_scope_ref = _trim(scope_ref, 64) or "*"
    state = load_operator_preferences(team_dir)
    rules = list(state.get("rules") or [])
    removed: Dict[str, Any] = {}
    kept: List[Dict[str, Any]] = []
    for item in rules:
        rule = normalize_preference_rule(item)
        if (
            not removed
            and _trim(rule.get("key"), 96).lower() == token
            and _trim(rule.get("artifact_kind"), 64).lower() == artifact
            and _trim(rule.get("scope"), 32).lower() == normalized_scope
            and _trim(rule.get("scope_ref"), 64) == normalized_scope_ref
        ):
            removed = rule
            continue
        if rule:
            kept.append(rule)
    state["rules"] = kept
    save_operator_preferences(team_dir, state)
    return removed


def delete_operator_preference_candidate(
    team_dir: Any,
    *,
    artifact_kind: Any,
    key: Any,
    project_ref: Any = "",
) -> Dict[str, Any]:
    artifact = _trim(artifact_kind, 64).lower() or "generic"
    token = _trim(key, 96).lower()
    project_token = _trim(project_ref, 64)
    state = load_operator_preference_candidates(team_dir)
    candidates = list(state.get("candidates") or [])
    removed: Dict[str, Any] = {}
    kept: List[Dict[str, Any]] = []
    for item in candidates:
        candidate = normalize_preference_candidate(item)
        if (
            not removed
            and _trim(candidate.get("artifact_kind"), 64).lower() == artifact
            and _trim(candidate.get("key"), 96).lower() == token
            and _trim(candidate.get("project_ref"), 64) == project_token
        ):
            removed = candidate
            continue
        if candidate:
            kept.append(candidate)
    state["candidates"] = kept
    save_operator_preference_candidates(team_dir, state)
    return removed


def build_request_preference_override(raw: Any, *, now_iso: str = "") -> Dict[str, Any]:
    decision = normalize_preference_decision(raw, now_iso=now_iso)
    if not decision:
        return {}
    return normalize_preference_rule(
        {
            "key": decision.get("key"),
            "artifact_kind": decision.get("artifact_kind"),
            "scope": "session",
            "scope_ref": "-",
            "value": decision.get("value"),
            "description": decision.get("description"),
            "enabled": bool(decision.get("apply_now", False)),
            "prompt_mode": "manual_only",
            "source": "request_decision",
            "confidence": "explicit",
            "last_confirmed_at": decision.get("decided_at", now_iso),
            "created_at": decision.get("decided_at", now_iso),
            "updated_at": decision.get("decided_at", now_iso),
        },
        now_iso=now_iso,
    )


def apply_preference_decision(
    team_dir: Any,
    *,
    decision: Any,
    now_iso: str = "",
) -> Dict[str, Any]:
    normalized = normalize_preference_decision(decision, now_iso=now_iso)
    if not normalized:
        return {
            "ok": False,
            "decision_summary": "-",
            "request_override": {},
            "persisted_rule": {},
            "persisted": False,
            "registry_path": str(runtime_core.operator_preferences_path(team_dir)),
        }
    state = load_operator_preferences(team_dir)
    persisted_rule: Dict[str, Any] = {}
    if normalized.get("persist_mode") in {"enable", "disable"} and normalized.get("scope") != "session":
        persisted_rule = _upsert_preference_rule(
            state,
            key=_trim(normalized.get("key"), 96).lower(),
            artifact_kind=_trim(normalized.get("artifact_kind"), 64).lower() or "generic",
            scope=_trim(normalized.get("scope"), 32).lower(),
            scope_ref=_trim(normalized.get("scope_ref"), 64),
            value=normalized.get("value"),
            description=_normalize_text(normalized.get("description"), 240),
            enabled=bool(normalized.get("persist_mode") == "enable"),
            prompt_mode="auto" if normalized.get("persist_mode") == "enable" else "confirm",
            now_iso=now_iso or _normalize_iso(normalized.get("decided_at")),
        )
        save_operator_preferences(team_dir, state)
    request_override = build_request_preference_override(normalized, now_iso=now_iso or _normalize_iso(normalized.get("decided_at")))
    return {
        "ok": True,
        "decision_summary": summarize_preference_decision(normalized),
        "request_override": request_override,
        "persisted_rule": persisted_rule,
        "persisted": bool(persisted_rule),
        "registry_path": str(runtime_core.operator_preferences_path(team_dir)),
    }
