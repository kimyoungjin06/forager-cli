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
import os
import urllib.request
from typing import Any, Dict, List, Tuple

from aoe_tg_context_pack import load_context_pack
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


def effective_model_routing_profile(entry: Any, policy: Any) -> str:
    project_profile = project_model_routing_profile(entry)
    policy_data = policy if isinstance(policy, dict) else {}
    policy_profile = _trim(policy_data.get("profile"), 64) or "default"
    if project_profile and project_profile != "default":
        return project_profile
    return policy_profile or "default"


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
        "profile": effective_model_routing_profile(entry, policy),
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
    policy = load_model_routing_policy(team_dir)
    profile = effective_model_routing_profile(entry, policy)
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


def resolve_task_model_plan(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    task_data = task if isinstance(task, dict) else {}
    entry_data = entry if isinstance(entry, dict) else {}
    pack = load_context_pack(
        team_dir,
        entry=entry_data,
        task=task_data,
        project_root=entry_data.get("project_root"),
    )
    profile = _trim(pack_profile_override, 64).lower() or _trim(pack.get("profile"), 64) or "on_desk_plan"
    worker_profiles = {"offdesk_execute", "review", "followup_execute", "incident_recovery"}
    judge_profiles = {"offdesk_execute", "review", "followup_preview", "followup_execute", "incident_recovery"}
    use_worker = profile in worker_profiles
    use_judge = profile in judge_profiles
    worker_route = resolve_model_route(team_dir, "background_worker_primary", entry=entry_data) if use_worker else {}
    escalation_route = (
        resolve_model_route(team_dir, "background_worker_escalation", entry=entry_data) if use_worker else {}
    )
    judge_route = resolve_model_route(team_dir, "offdesk_judge", entry=entry_data) if use_judge else {}
    parts = [f"pack={profile}"]
    parts.append(
        "worker={value}".format(
            value=_trim(worker_route.get("summary"), 240) if worker_route else "none"
        )
    )
    parts.append(
        "judge={value}".format(
            value=_trim(judge_route.get("summary"), 240) if judge_route else "none"
        )
    )
    if use_worker:
        parts.append(
            "escalation={value}".format(
                value=_trim(escalation_route.get("summary"), 240) if escalation_route else "none"
            )
        )
    return {
        "pack_profile": profile,
        "worker_route": worker_route if isinstance(worker_route, dict) else {},
        "judge_route": judge_route if isinstance(judge_route, dict) else {},
        "escalation_route": escalation_route if isinstance(escalation_route, dict) else {},
        "summary": " | ".join(parts),
    }


def resolve_model_binding_snapshot(
    team_dir: Any,
    route_id: str,
    *,
    entry: Any = None,
    endpoint_id_override: Any = "",
) -> Dict[str, Any]:
    route = resolve_model_route(team_dir, route_id, entry=entry)
    registry = load_model_endpoint_registry(team_dir)
    index = _registry_index(registry)
    endpoint_id = _trim(endpoint_id_override, 64).lower() or _trim(route.get("endpoint_id"), 64).lower()
    endpoint = index.get(endpoint_id) if endpoint_id else None
    endpoint_data = endpoint if isinstance(endpoint, dict) else {}
    return {
        "route": route,
        "endpoint": endpoint_data,
        "endpoint_id": endpoint_id,
        "bound": bool(route.get("bound")) and bool(endpoint_data),
        "summary": _trim(route.get("summary"), 240) or "-",
    }


def resolve_task_worker_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    task_data = task if isinstance(task, dict) else {}
    route_id = _trim(task_data.get("background_run_model_worker_route_id"), 64).lower() or "background_worker_primary"
    endpoint_id = _trim(task_data.get("background_run_model_worker_endpoint_id"), 64).lower()
    if not endpoint_id and not _trim(task_data.get("background_run_model_plan_summary"), 64):
        plan = resolve_task_model_plan(
            team_dir,
            entry=entry,
            task=task_data,
            pack_profile_override=pack_profile_override,
        )
        worker = plan.get("worker_route") if isinstance(plan.get("worker_route"), dict) else {}
        route_id = _trim(worker.get("route_id"), 64).lower() or route_id
        endpoint_id = _trim(worker.get("endpoint_id"), 64).lower()
    binding = resolve_model_binding_snapshot(team_dir, route_id, entry=entry, endpoint_id_override=endpoint_id)
    binding["source"] = "background_ticket" if _trim(task_data.get("background_run_model_plan_summary"), 64) else "task_plan"
    return binding


def resolve_task_research_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    entry_data = entry if isinstance(entry, dict) else {}
    profile = _trim(pack_profile_override, 64).lower()
    if not profile:
        pack = load_context_pack(
            team_dir,
            entry=entry_data,
            task=task if isinstance(task, dict) else {},
            project_root=entry_data.get("project_root"),
        )
        profile = _trim(pack.get("profile"), 64) or "on_desk_plan"
    binding = resolve_model_binding_snapshot(team_dir, "research_synthesis", entry=entry_data)
    binding["source"] = "task_plan"
    binding["pack_profile"] = profile
    binding["plan_summary"] = f"pack={profile} | research={_trim(binding.get('summary'), 240) or 'none'}"
    return binding


def resolve_task_judge_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    plan = resolve_task_model_plan(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    judge = plan.get("judge_route") if isinstance(plan.get("judge_route"), dict) else {}
    route_id = _trim(judge.get("route_id"), 64).lower() or "offdesk_judge"
    endpoint_id = _trim(judge.get("endpoint_id"), 64).lower()
    binding = resolve_model_binding_snapshot(team_dir, route_id, entry=entry, endpoint_id_override=endpoint_id)
    binding["source"] = "task_plan"
    binding["pack_profile"] = _trim(plan.get("pack_profile"), 64)
    binding["plan_summary"] = _trim(plan.get("summary"), 320)
    return binding


def resolve_task_escalation_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
) -> Dict[str, Any]:
    plan = resolve_task_model_plan(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    escalation = plan.get("escalation_route") if isinstance(plan.get("escalation_route"), dict) else {}
    route_id = _trim(escalation.get("route_id"), 64).lower() or "background_worker_escalation"
    endpoint_id = _trim(escalation.get("endpoint_id"), 64).lower()
    binding = resolve_model_binding_snapshot(team_dir, route_id, entry=entry, endpoint_id_override=endpoint_id)
    binding["source"] = "task_plan"
    binding["pack_profile"] = _trim(plan.get("pack_profile"), 64)
    binding["plan_summary"] = _trim(plan.get("summary"), 320)
    return binding


def resolve_background_ticket_worker_binding(team_dir: Any, ticket: Any, *, entry: Any = None) -> Dict[str, Any]:
    ticket_data = ticket if isinstance(ticket, dict) else {}
    launch_spec = ticket_data.get("launch_spec") if isinstance(ticket_data.get("launch_spec"), dict) else {}
    route_id = (
        _trim(launch_spec.get("model_worker_route_id"), 64).lower()
        or _trim(ticket_data.get("model_worker_route_id"), 64).lower()
        or "background_worker_primary"
    )
    endpoint_id = (
        _trim(launch_spec.get("model_worker_endpoint_id"), 64).lower()
        or _trim(ticket_data.get("model_worker_endpoint_id"), 64).lower()
    )
    binding = resolve_model_binding_snapshot(team_dir, route_id, entry=entry, endpoint_id_override=endpoint_id)
    binding["source"] = "launch_spec" if _trim(launch_spec.get("model_plan_summary"), 64) else "background_ticket"
    binding["pack_profile"] = _trim(launch_spec.get("model_pack_profile"), 64)
    binding["plan_summary"] = _trim(launch_spec.get("model_plan_summary"), 320)
    return binding


def _default_fetch_json(url: str, *, timeout_sec: float = 3.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=max(0.1, float(timeout_sec or 3.0))) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _default_api_key_env(provider_kind: str, explicit: Any) -> str:
    token = _trim(explicit, 128)
    if token:
        return token
    if provider_kind == "anthropic":
        return "ANTHROPIC_API_KEY"
    if provider_kind == "openai":
        return "OPENAI_API_KEY"
    return ""


def probe_model_endpoint(
    endpoint: Any,
    *,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    row = endpoint if isinstance(endpoint, dict) else {}
    endpoint_id = _trim(row.get("endpoint_id"), 64).lower()
    provider_kind = _trim(row.get("provider_kind"), 64).lower() or "custom"
    base_url = _trim(row.get("base_url"), 240).rstrip("/")
    model = _trim(row.get("model"), 128)
    enabled = bool(row.get("enabled"))
    if not endpoint_id:
        return {"ok": False, "probe_status": "missing_endpoint", "summary": "endpoint=missing"}
    if not enabled:
        return {
            "ok": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "probe_status": "disabled",
            "summary": f"endpoint={endpoint_id} status=disabled",
        }
    api_key_env = _default_api_key_env(provider_kind, row.get("api_key_env"))
    if provider_kind in {"anthropic", "openai"} and not _trim(os.environ.get(api_key_env), 800):
        return {
            "ok": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "probe_status": "missing_api_key",
            "summary": f"endpoint={endpoint_id} provider={provider_kind} status=missing_api_key env={api_key_env or '-'}",
        }
    if provider_kind != "ollama":
        return {
            "ok": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "probe_status": "deferred_live_probe",
            "summary": f"endpoint={endpoint_id} provider={provider_kind} status=deferred_live_probe",
        }
    if not base_url:
        return {
            "ok": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "probe_status": "missing_base_url",
            "summary": f"endpoint={endpoint_id} provider=ollama status=missing_base_url",
        }
    fetch = fetch_json if callable(fetch_json) else _default_fetch_json
    try:
        payload = fetch(f"{base_url}/api/tags", timeout_sec=timeout_sec)
    except Exception as exc:
        return {
            "ok": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "base_url": base_url,
            "model": model,
            "probe_status": "request_failed",
            "error": _trim(exc, 240),
            "summary": f"endpoint={endpoint_id} provider=ollama status=request_failed",
        }
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    names = [
        _trim(item.get("name"), 128)
        for item in models
        if isinstance(item, dict) and _trim(item.get("name"), 128)
    ][:24]
    model_present = (model in names) if model else False
    return {
        "ok": bool(model_present),
        "endpoint_id": endpoint_id,
        "provider_kind": provider_kind,
        "base_url": base_url,
        "model": model,
        "probe_status": "ok" if model_present else "model_missing",
        "available_model_names": names,
        "available_model_count": len(names),
        "model_present": bool(model_present),
        "summary": (
            f"endpoint={endpoint_id} provider=ollama model={model or '-'} "
            f"present={'yes' if model_present else 'no'} available={len(names)}"
        ),
    }


def probe_model_route(
    team_dir: Any,
    route_id: str,
    *,
    entry: Any = None,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    binding = resolve_model_binding_snapshot(team_dir, route_id, entry=entry)
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    if not binding.get("bound"):
        return {
            "ok": False,
            "route_id": _trim(route.get("route_id"), 64).lower() or _trim(route_id, 64).lower(),
            "probe_status": "unbound",
            "summary": _trim(route.get("summary"), 240) or f"route={route_id} status=unbound",
            "binding": binding,
        }
    result = probe_model_endpoint(endpoint, timeout_sec=timeout_sec, fetch_json=fetch_json)
    result["route_id"] = _trim(route.get("route_id"), 64).lower() or _trim(route_id, 64).lower()
    result["binding"] = binding
    return result


def probe_background_ticket_worker_binding(
    team_dir: Any,
    ticket: Any,
    *,
    entry: Any = None,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    binding = resolve_background_ticket_worker_binding(team_dir, ticket, entry=entry)
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    if not binding.get("bound"):
        return {
            "ok": False,
            "probe_status": "unbound",
            "summary": _trim(route.get("summary"), 240) or "worker_route=unbound",
            "binding": binding,
        }
    result = probe_model_endpoint(endpoint, timeout_sec=timeout_sec, fetch_json=fetch_json)
    result["binding"] = binding
    result["route_id"] = _trim(route.get("route_id"), 64).lower() or "background_worker_primary"
    return result


def summarize_deferred_model_binding_probe(binding: Any, *, default_label: str = "route") -> Dict[str, Any]:
    row = binding if isinstance(binding, dict) else {}
    route = row.get("route") if isinstance(row.get("route"), dict) else {}
    endpoint = row.get("endpoint") if isinstance(row.get("endpoint"), dict) else {}
    route_id = _trim(route.get("route_id"), 64).lower() or _trim(default_label, 64).lower() or "route"
    route_summary = _trim(route.get("summary"), 240) or f"{route_id}=unbound"
    if not row.get("bound"):
        return {
            "ok": False,
            "route_id": route_id,
            "probe_status": "unbound",
            "summary": route_summary,
            "binding": row,
        }
    endpoint_id = _trim(endpoint.get("endpoint_id"), 64) or "-"
    provider_kind = _trim(endpoint.get("provider_kind"), 64).lower() or "custom"
    api_key_env = _default_api_key_env(provider_kind, endpoint.get("api_key_env"))
    if provider_kind in {"anthropic", "openai"} and not _trim(os.environ.get(api_key_env), 800):
        return {
            "ok": False,
            "route_id": route_id,
            "probe_status": "missing_api_key",
            "summary": f"endpoint={endpoint_id} provider={provider_kind} status=missing_api_key env={api_key_env or '-'}",
            "binding": row,
        }
    if provider_kind != "ollama":
        return {
            "ok": False,
            "route_id": route_id,
            "probe_status": "deferred_live_probe",
            "summary": f"endpoint={endpoint_id} provider={provider_kind} status=deferred_live_probe",
            "binding": row,
        }
    return {
        "ok": False,
        "route_id": route_id,
        "probe_status": "deferred_live_probe",
        "summary": f"endpoint={endpoint_id} provider=ollama status=deferred_live_probe",
        "binding": row,
    }


def probe_task_judge_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    binding = resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    if not binding.get("bound"):
        return {
            "ok": False,
            "probe_status": "unbound",
            "summary": _trim(route.get("summary"), 240) or "judge_route=unbound",
            "binding": binding,
        }
    result = probe_model_endpoint(endpoint, timeout_sec=timeout_sec, fetch_json=fetch_json)
    result["binding"] = binding
    result["route_id"] = _trim(route.get("route_id"), 64).lower() or "offdesk_judge"
    return result


def probe_task_research_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    binding = resolve_task_research_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    if not binding.get("bound"):
        return {
            "ok": False,
            "probe_status": "unbound",
            "summary": _trim(route.get("summary"), 240) or "research_route=unbound",
            "binding": binding,
        }
    result = probe_model_endpoint(endpoint, timeout_sec=timeout_sec, fetch_json=fetch_json)
    result["binding"] = binding
    result["route_id"] = _trim(route.get("route_id"), 64).lower() or "research_synthesis"
    return result


def probe_task_escalation_binding(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    pack_profile_override: Any = None,
    timeout_sec: float = 3.0,
    fetch_json: Any = None,
) -> Dict[str, Any]:
    binding = resolve_task_escalation_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
    if not binding.get("bound"):
        return {
            "ok": False,
            "probe_status": "unbound",
            "summary": _trim(route.get("summary"), 240) or "escalation_route=unbound",
            "binding": binding,
        }
    result = probe_model_endpoint(endpoint, timeout_sec=timeout_sec, fetch_json=fetch_json)
    result["binding"] = binding
    result["route_id"] = _trim(route.get("route_id"), 64).lower() or "background_worker_escalation"
    return result


def launch_spec_model_plan_metadata(
    plan: Any,
    *,
    judge_binding: Any = None,
    judge_probe: Any = None,
    escalation_binding: Any = None,
    escalation_probe: Any = None,
) -> Dict[str, str]:
    data = plan if isinstance(plan, dict) else {}
    worker = data.get("worker_route") if isinstance(data.get("worker_route"), dict) else {}
    judge = data.get("judge_route") if isinstance(data.get("judge_route"), dict) else {}
    escalation = data.get("escalation_route") if isinstance(data.get("escalation_route"), dict) else {}
    judge_binding_data = judge_binding if isinstance(judge_binding, dict) else {}
    judge_probe_data = judge_probe if isinstance(judge_probe, dict) else {}
    escalation_binding_data = escalation_binding if isinstance(escalation_binding, dict) else {}
    escalation_probe_data = escalation_probe if isinstance(escalation_probe, dict) else {}
    return {
        "model_pack_profile": _trim(data.get("pack_profile"), 64),
        "model_plan_summary": _trim(data.get("summary"), 320),
        "model_worker_route_id": _trim(worker.get("route_id"), 64),
        "model_judge_route_id": _trim(judge.get("route_id"), 64),
        "model_escalation_route_id": _trim(escalation.get("route_id"), 64),
        "model_worker_endpoint_id": _trim(worker.get("endpoint_id"), 64),
        "model_judge_endpoint_id": _trim(judge.get("endpoint_id"), 64),
        "model_escalation_endpoint_id": _trim(escalation.get("endpoint_id"), 64),
        "model_judge_binding_summary": _trim(judge_binding_data.get("summary"), 240),
        "model_judge_probe_status": _trim(judge_probe_data.get("probe_status"), 64),
        "model_judge_probe_summary": _trim(judge_probe_data.get("summary"), 240),
        "model_escalation_binding_summary": _trim(escalation_binding_data.get("summary"), 240),
        "model_escalation_probe_status": _trim(escalation_probe_data.get("probe_status"), 64),
        "model_escalation_probe_summary": _trim(escalation_probe_data.get("summary"), 240),
    }
