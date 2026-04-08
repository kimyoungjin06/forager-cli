#!/usr/bin/env python3
"""Model endpoint registry and routing policy helpers.

This module provides a canonical, swappable seam for model-serving endpoints.
It does not execute model calls. It only owns:

- endpoint registry normalization
- route policy normalization
- route -> endpoint resolution
- operator-readable summaries
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from aoe_tg_runtime_core import model_endpoint_registry_path, model_routing_policy_path


MODEL_ROUTE_IDS: Tuple[str, ...] = (
    "on_desk_primary",
    "research_synthesis",
    "offdesk_judge",
    "background_worker_primary",
    "background_worker_escalation",
)

MODEL_ENDPOINT_KINDS: Tuple[str, ...] = (
    "anthropic",
    "google",
    "openai",
    "openai_compatible",
    "ollama",
    "custom",
)

_DEFAULT_ROUTE_HINTS: Dict[str, Dict[str, str]] = {
    "on_desk_primary": {
        "family_hint": "anthropic",
        "model_hint": "claude-sonnet",
        "summary_label": "ondesk",
    },
    "research_synthesis": {
        "family_hint": "google",
        "model_hint": "gemini-2.5-pro",
        "summary_label": "research",
    },
    "offdesk_judge": {
        "family_hint": "anthropic",
        "model_hint": "claude-opus-4.1",
        "summary_label": "judge",
    },
    "background_worker_primary": {
        "family_hint": "ollama",
        "model_hint": "qwen3-coder",
        "summary_label": "bg",
    },
    "background_worker_escalation": {
        "family_hint": "openai_compatible",
        "model_hint": "gpt-oss-or-gemma4",
        "summary_label": "bgx",
    },
}


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _bool_from_raw(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    token = _trim(raw, 32).lower()
    if not token:
        return default
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_list(raw: Any, *, limit: int = 12, item_limit: int = 128) -> List[str]:
    source = raw if isinstance(raw, list) else []
    seen: set[str] = set()
    out: List[str] = []
    for item in source:
        token = _trim(item, item_limit)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def normalize_model_endpoint_kind(raw: Any, default: str = "custom") -> str:
    token = _trim(raw, 64).lower()
    if token in MODEL_ENDPOINT_KINDS:
        return token
    fallback = _trim(default, 64).lower()
    return fallback if fallback in MODEL_ENDPOINT_KINDS else "custom"


def _local_flag_from_endpoint(kind: str, base_url: str, explicit: Any) -> bool:
    if explicit not in {None, ""}:
        return _bool_from_raw(explicit, False)
    if kind == "ollama":
        return True
    token = _trim(base_url, 240).lower()
    if not token:
        return False
    return any(
        marker in token
        for marker in (
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "::1",
            ".local",
        )
    )


def _normalize_route_roles(raw: Any) -> List[str]:
    out: List[str] = []
    for token in _normalize_list(raw, limit=len(MODEL_ROUTE_IDS), item_limit=64):
        normalized = _trim(token, 64).lower()
        if normalized in MODEL_ROUTE_IDS and normalized not in out:
            out.append(normalized)
    return out


def _sanitize_endpoint_row(raw: Any) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    endpoint_id = _trim(data.get("endpoint_id"), 64).lower().replace(" ", "-")
    kind = normalize_model_endpoint_kind(data.get("provider_kind"), "custom")
    base_url = _trim(data.get("base_url"), 240)
    model = _trim(data.get("model"), 128)
    api_key_env = _trim(data.get("api_key_env"), 128)
    enabled = _bool_from_raw(data.get("enabled"), False)
    local = _local_flag_from_endpoint(kind, base_url, data.get("local"))
    supports_tools = _bool_from_raw(data.get("supports_tools"), kind in {"openai", "openai_compatible"})
    supports_json = _bool_from_raw(data.get("supports_json"), True)
    roles = _normalize_route_roles(data.get("roles"))
    notes = _trim(data.get("notes"), 240)
    summary_bits = [kind]
    if model:
        summary_bits.append(model)
    summary_bits.append("local" if local else "remote")
    if supports_tools:
        summary_bits.append("tools")
    return {
        "endpoint_id": endpoint_id,
        "provider_kind": kind,
        "base_url": base_url,
        "model": model,
        "api_key_env": api_key_env,
        "enabled": enabled,
        "local": local,
        "supports_tools": supports_tools,
        "supports_json": supports_json,
        "roles": roles,
        "notes": notes,
        "summary": " | ".join(part for part in summary_bits if part),
    }


def sanitize_model_endpoint_registry(raw: Any, *, config_path: str = "") -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    source = data.get("endpoints") if isinstance(data.get("endpoints"), list) else []
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in source:
        row = _sanitize_endpoint_row(item)
        endpoint_id = str(row.get("endpoint_id", "")).strip()
        if not endpoint_id or endpoint_id in seen:
            continue
        seen.add(endpoint_id)
        rows.append(row)
    return {
        "version": max(1, int(data.get("version", 1) or 1)),
        "config_path": _trim(config_path, 400),
        "endpoints": rows,
    }


def load_model_endpoint_registry(team_dir: Any, explicit: str = "") -> Dict[str, Any]:
    try:
        path = model_endpoint_registry_path(explicit or team_dir)
    except Exception:
        return sanitize_model_endpoint_registry({}, config_path="")
    if not path.exists():
        return sanitize_model_endpoint_registry({}, config_path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return sanitize_model_endpoint_registry({}, config_path=str(path))
    return sanitize_model_endpoint_registry(payload, config_path=str(path))


def _sanitize_route_row(route_id: str, raw: Any) -> Dict[str, Any]:
    defaults = _DEFAULT_ROUTE_HINTS.get(route_id, {})
    data = raw if isinstance(raw, dict) else {}
    return {
        "route_id": route_id,
        "endpoint_id": _trim(data.get("endpoint_id"), 64).lower().replace(" ", "-"),
        "family_hint": _trim(data.get("family_hint"), 64).lower() or str(defaults.get("family_hint", "")).strip(),
        "model_hint": _trim(data.get("model_hint"), 128) or str(defaults.get("model_hint", "")).strip(),
        "fallback_ids": [item.lower().replace(" ", "-") for item in _normalize_list(data.get("fallback_ids"), limit=6, item_limit=64)],
        "summary_label": _trim(data.get("summary_label"), 24).lower() or str(defaults.get("summary_label", "")).strip(),
        "notes": _trim(data.get("notes"), 240),
    }


def sanitize_model_routing_policy(raw: Any, *, config_path: str = "") -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    source_routes = data.get("routes") if isinstance(data.get("routes"), dict) else {}
    routes = {
        route_id: _sanitize_route_row(route_id, source_routes.get(route_id))
        for route_id in MODEL_ROUTE_IDS
    }
    return {
        "version": max(1, int(data.get("version", 1) or 1)),
        "config_path": _trim(config_path, 400),
        "profile": _trim(data.get("profile"), 64) or "default",
        "routes": routes,
    }


def load_model_routing_policy(team_dir: Any, explicit: str = "") -> Dict[str, Any]:
    try:
        path = model_routing_policy_path(explicit or team_dir)
    except Exception:
        return sanitize_model_routing_policy({}, config_path="")
    if not path.exists():
        return sanitize_model_routing_policy({}, config_path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return sanitize_model_routing_policy({}, config_path=str(path))
    return sanitize_model_routing_policy(payload, config_path=str(path))


def project_model_routing_profile(entry: Any) -> str:
    data = entry if isinstance(entry, dict) else {}
    return _trim(data.get("model_routing_profile"), 64) or "default"


def _registry_index(registry: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = registry.get("endpoints") if isinstance(registry.get("endpoints"), list) else []
    return {
        str(row.get("endpoint_id", "")).strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("endpoint_id", "")).strip()
    }


def resolve_model_route(team_dir: Any, route_id: str, *, entry: Any = None) -> Dict[str, Any]:
    route_token = _trim(route_id, 64).lower()
    if route_token not in MODEL_ROUTE_IDS:
        return {}
    registry = load_model_endpoint_registry(team_dir)
    policy = load_model_routing_policy(team_dir)
    route = ((policy.get("routes") or {}) if isinstance(policy.get("routes"), dict) else {}).get(route_token) or {}
    route = route if isinstance(route, dict) else {}
    index = _registry_index(registry)
    endpoint_id = _trim(route.get("endpoint_id"), 64).lower()
    endpoint = index.get(endpoint_id) if endpoint_id else None
    bound = bool(isinstance(endpoint, dict) and endpoint.get("enabled"))
    summary_label = _trim(route.get("summary_label"), 24).lower() or route_token
    model_hint = _trim(route.get("model_hint"), 128)
    family_hint = _trim(route.get("family_hint"), 64).lower()
    effective_model = _trim((endpoint or {}).get("model"), 128) or model_hint
    effective_kind = _trim((endpoint or {}).get("provider_kind"), 64).lower() or family_hint
    return {
        "route_id": route_token,
        "profile": project_model_routing_profile(entry),
        "summary_label": summary_label,
        "endpoint_id": endpoint_id,
        "bound": bound,
        "family_hint": family_hint,
        "model_hint": model_hint,
        "effective_model": effective_model,
        "effective_kind": effective_kind,
        "effective_local": bool((endpoint or {}).get("local")),
        "effective_supports_tools": bool((endpoint or {}).get("supports_tools")),
        "notes": _trim(route.get("notes"), 240),
        "summary": (
            f"{summary_label}={endpoint_id}:{effective_model}"
            if bound and endpoint_id
            else f"{summary_label}=unbound:{model_hint or family_hint or '-'}"
        ),
    }


def summarize_model_routing(team_dir: Any, *, entry: Any = None) -> str:
    profile = project_model_routing_profile(entry)
    parts = [f"profile={profile}"]
    for route_id in MODEL_ROUTE_IDS:
        snapshot = resolve_model_route(team_dir, route_id, entry=entry)
        summary = _trim(snapshot.get("summary"), 240)
        if summary:
            parts.append(summary)
    return " | ".join(parts) if parts else "-"


def summarize_model_endpoint_registry(team_dir: Any, *, entry: Any = None) -> str:
    registry = load_model_endpoint_registry(team_dir)
    policy = load_model_routing_policy(team_dir)
    rows = registry.get("endpoints") if isinstance(registry.get("endpoints"), list) else []
    enabled_rows = [row for row in rows if isinstance(row, dict) and bool(row.get("enabled"))]
    local_count = sum(1 for row in enabled_rows if bool(row.get("local")))
    kind_counts: Dict[str, int] = {}
    for row in enabled_rows:
        kind = _trim(row.get("provider_kind"), 64).lower() or "custom"
        kind_counts[kind] = int(kind_counts.get(kind, 0) or 0) + 1
    route_count = len(MODEL_ROUTE_IDS)
    bound_count = 0
    for route_id in MODEL_ROUTE_IDS:
        route = ((policy.get("routes") or {}) if isinstance(policy.get("routes"), dict) else {}).get(route_id) or {}
        endpoint_id = _trim((route if isinstance(route, dict) else {}).get("endpoint_id"), 64).lower()
        if endpoint_id and any(str(row.get("endpoint_id", "")).strip() == endpoint_id and bool(row.get("enabled")) for row in enabled_rows):
            bound_count += 1
    kinds = ", ".join(f"{key}={kind_counts[key]}" for key in sorted(kind_counts.keys())) or "-"
    return "enabled={enabled} bound={bound}/{routes} local={local} kinds={kinds}".format(
        enabled=len(enabled_rows),
        bound=bound_count,
        routes=route_count,
        local=local_count,
        kinds=kinds,
    )
