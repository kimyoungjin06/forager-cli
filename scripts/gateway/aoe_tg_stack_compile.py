#!/usr/bin/env python3
"""Compile a declarative stack manifest plus env overlay into canonical runtime artifacts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from aoe_tg_artifact_backend import artifact_backend
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_workspace_brief as workspace_brief
from aoe_tg_runtime_core import model_endpoint_registry_path, model_routing_policy_path


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _bool(raw: Any, default: bool = False) -> bool:
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


def _slug(raw: Any, default: str = "item") -> str:
    token = _trim(raw, 128).lower()
    out: List[str] = []
    last_dash = False
    for char in token:
        if char.isalnum():
            out.append(char)
            last_dash = False
            continue
        if not last_dash:
            out.append("-")
            last_dash = True
    value = "".join(out).strip("-")
    return value or default


def _resolve_path(project_root: Path, raw: Any) -> str:
    token = _trim(raw, 400)
    if not token:
        return ""
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = project_root / path
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _resolve_path_list(project_root: Path, raw: Any, *, limit: int = 16) -> List[str]:
    source = raw if isinstance(raw, list) else []
    out: List[str] = []
    seen: set[str] = set()
    for item in source:
        resolved = _resolve_path(project_root, item)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
        if len(out) >= limit:
            break
    return out


def load_env_overlay(path: Path | str) -> Dict[str, str]:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {}
    out: Dict[str, str] = {}
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        out[key] = value
    return out


def load_stack_manifest(path: Path | str) -> Dict[str, Any]:
    target = Path(path).expanduser().resolve()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("stack manifest must be a JSON object")
    return payload


def _env_value(name: Any, env_overlay: Dict[str, str]) -> str:
    token = _trim(name, 128)
    if not token:
        return ""
    return _trim(env_overlay.get(token) or os.environ.get(token), 400)


def _normalize_route_ids(raw: Any) -> List[str]:
    source = raw if isinstance(raw, list) else [raw]
    out: List[str] = []
    for item in source:
        token = _trim(item, 64).lower()
        if token in model_endpoint_adapter.MODEL_ROUTE_IDS and token not in out:
            out.append(token)
    return out


def _compile_model_endpoints(
    models: Any,
    *,
    env_overlay: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, str]]:
    source = models if isinstance(models, dict) else {}
    endpoints: List[Dict[str, Any]] = []
    key_to_endpoint_id: Dict[str, str] = {}
    inferred_routes: Dict[str, str] = {}
    seen_endpoint_ids: set[str] = set()
    for model_key, raw in source.items():
        data = raw if isinstance(raw, dict) else {}
        provider_kind = model_endpoint_adapter.normalize_model_endpoint_kind(
            data.get("provider_kind") or data.get("provider"),
            "custom",
        )
        model_name = _trim(data.get("model"), 128)
        if not model_name:
            continue
        endpoint_id = _trim(data.get("endpoint_id"), 64).lower().replace(" ", "-")
        if not endpoint_id:
            endpoint_id = f"{provider_kind}-{_slug(model_name, default=_slug(model_key, default='endpoint'))}"[:64]
        if endpoint_id in seen_endpoint_ids:
            continue
        base_url = _trim(data.get("base_url"), 240)
        if not base_url:
            base_url = _env_value(data.get("base_url_env"), env_overlay)
        if not base_url and provider_kind == "ollama":
            base_url = _env_value("OLLAMA_BASE_URL", env_overlay)
        route_ids = _normalize_route_ids(data.get("routes") or data.get("route"))
        row = {
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "base_url": base_url,
            "model": model_name,
            "api_key_env": _trim(data.get("api_key_env"), 128),
            "enabled": _bool(data.get("enabled"), True),
            "local": data.get("local") if data.get("local") not in {None, ""} else (provider_kind == "ollama"),
            "supports_tools": _bool(data.get("supports_tools"), provider_kind in {"openai", "openai_compatible"}),
            "supports_json": _bool(data.get("supports_json"), True),
            "roles": route_ids,
            "notes": _trim(data.get("notes"), 240),
        }
        normalized = model_endpoint_adapter.sanitize_model_endpoint_registry({"endpoints": [row]}).get("endpoints") or []
        if not normalized:
            continue
        endpoints.append(normalized[0])
        seen_endpoint_ids.add(endpoint_id)
        key_to_endpoint_id[_trim(model_key, 64)] = endpoint_id
        for route_id in route_ids:
            inferred_routes.setdefault(route_id, endpoint_id)
    return endpoints, key_to_endpoint_id, inferred_routes


def _compile_routing_policy(
    routing: Any,
    *,
    endpoint_refs: Dict[str, str],
    inferred_routes: Dict[str, str],
    profile: str,
) -> Dict[str, Any]:
    routing_data = routing if isinstance(routing, dict) else {}
    source_routes = routing_data.get("routes") if isinstance(routing_data.get("routes"), dict) else {}
    routes: Dict[str, Dict[str, Any]] = {}
    for route_id in model_endpoint_adapter.MODEL_ROUTE_IDS:
        raw = source_routes.get(route_id) if isinstance(source_routes.get(route_id), dict) else {}
        endpoint_ref = _trim(raw.get("endpoint_ref") or raw.get("endpoint_key") or raw.get("model_key"), 64)
        endpoint_id = _trim(raw.get("endpoint_id"), 64).lower().replace(" ", "-")
        if not endpoint_id and endpoint_ref:
            endpoint_id = endpoint_refs.get(endpoint_ref, "")
        if not endpoint_id:
            endpoint_id = inferred_routes.get(route_id, "")
        route_payload: Dict[str, Any] = {}
        if endpoint_id:
            route_payload["endpoint_id"] = endpoint_id
        for key in ("family_hint", "model_hint", "summary_label", "notes"):
            value = _trim(raw.get(key), 240 if key == "notes" else 128)
            if value:
                route_payload[key] = value
        fallback_ids = [
            _trim(item, 64).lower().replace(" ", "-")
            for item in (raw.get("fallback_ids") if isinstance(raw.get("fallback_ids"), list) else [])
            if _trim(item, 64)
        ]
        if fallback_ids:
            route_payload["fallback_ids"] = fallback_ids[:6]
        routes[route_id] = route_payload
    return model_endpoint_adapter.sanitize_model_routing_policy(
        {
            "version": 1,
            "profile": _trim(routing_data.get("profile"), 64) or _trim(profile, 64) or "default",
            "routes": routes,
        }
    )


def compile_stack(
    *,
    manifest_path: Path | str,
    team_dir: Path | str,
    project_root: Path | str,
    env_file: Path | str | None = None,
) -> Dict[str, Any]:
    manifest = load_stack_manifest(manifest_path)
    env_overlay = load_env_overlay(env_file) if env_file else {}
    resolved_project_root = Path(project_root).expanduser().resolve()
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    workspace_data = manifest.get("workspace") if isinstance(manifest.get("workspace"), dict) else {}
    harness_data = manifest.get("harness") if isinstance(manifest.get("harness"), dict) else {}
    routing_profile = (
        _trim(manifest.get("profile"), 64)
        or _trim((manifest.get("routing") or {}).get("profile"), 64)
        or _trim(workspace_data.get("model_routing_profile"), 64)
        or "default"
    )
    endpoints, endpoint_refs, inferred_routes = _compile_model_endpoints(
        manifest.get("models"),
        env_overlay=env_overlay,
    )
    registry = model_endpoint_adapter.sanitize_model_endpoint_registry(
        {
            "version": 1,
            "endpoints": endpoints,
        },
        config_path=str(model_endpoint_registry_path(resolved_team_dir)),
    )
    policy = _compile_routing_policy(
        manifest.get("routing"),
        endpoint_refs=endpoint_refs,
        inferred_routes=inferred_routes,
        profile=routing_profile,
    )
    workspace_payload = {
        "version": 1,
        "workspace_key": _trim(workspace_data.get("workspace_key"), 128)
        or _trim(manifest.get("workspace_key"), 128)
        or _slug(resolved_project_root.name, default="default"),
        "project_alias": _trim(workspace_data.get("project_alias"), 32) or "O1",
        "project_root": str(resolved_project_root),
        "team_dir": str(resolved_team_dir),
        "project_overview": _trim(workspace_data.get("project_overview"), 240)
        or _trim(workspace_data.get("overview"), 240),
        "code_roots": _resolve_path_list(resolved_project_root, workspace_data.get("code_roots"))
        or [str(resolved_project_root)],
        "doc_roots": _resolve_path_list(resolved_project_root, workspace_data.get("doc_roots")),
        "doc_ignore_globs": workspace_data.get("doc_ignore_globs") if isinstance(workspace_data.get("doc_ignore_globs"), list) else [],
        "canonical_todo_path": _resolve_path(resolved_project_root, workspace_data.get("canonical_todo_path")),
        "canonical_runbook_paths": _resolve_path_list(resolved_project_root, workspace_data.get("canonical_runbook_paths")),
        "model_routing_profile": routing_profile,
        "background_runner_target": _trim(workspace_data.get("background_runner_target"), 64)
        or _trim(((harness_data.get("off_desk_executor") or {}) if isinstance(harness_data.get("off_desk_executor"), dict) else {}).get("kind"), 64)
        or "local_background",
        "run_lock_mode_default": _trim(workspace_data.get("run_lock_mode_default"), 32) or "open",
        "background_runner_slot_limits": workspace_data.get("background_runner_slot_limits") if isinstance(workspace_data.get("background_runner_slot_limits"), dict) else {},
        "onboarding_status": _trim(workspace_data.get("onboarding_status"), 32) or "validated",
        "validation_notes": workspace_data.get("validation_notes") if isinstance(workspace_data.get("validation_notes"), list) else [],
        "endpoint_registry_path": str(model_endpoint_registry_path(resolved_team_dir)),
        "routing_policy_path": str(model_routing_policy_path(resolved_team_dir)),
    }
    written_workspace = workspace_brief.write_workspace_brief(
        resolved_team_dir,
        workspace_payload,
        project_root=resolved_project_root,
        entry={
            "project_alias": workspace_payload["project_alias"],
            "project_root": str(resolved_project_root),
            "team_dir": str(resolved_team_dir),
            "overview": workspace_payload.get("project_overview", ""),
            "background_runner_target": workspace_payload["background_runner_target"],
            "background_runner_slot_limits": workspace_payload["background_runner_slot_limits"],
            "model_routing_profile": routing_profile,
            "run_lock_mode": workspace_payload["run_lock_mode_default"],
        },
    )
    backend = artifact_backend(resolved_team_dir)
    registry_path = backend.write_model_endpoint_registry(registry)
    routing_path = backend.write_model_routing_policy(policy)
    harness_summary = "on_desk={on_desk} | off_desk={off_desk} | executor={executor}".format(
        on_desk=_trim(((harness_data.get("on_desk") or {}) if isinstance(harness_data.get("on_desk"), dict) else {}).get("kind"), 64) or "-",
        off_desk=_trim(((harness_data.get("off_desk") or {}) if isinstance(harness_data.get("off_desk"), dict) else {}).get("kind"), 64) or "-",
        executor=_trim(((harness_data.get("off_desk_executor") or {}) if isinstance(harness_data.get("off_desk_executor"), dict) else {}).get("kind"), 64) or "-",
    )
    return {
        "manifest_path": str(Path(manifest_path).expanduser().resolve()),
        "env_file": str(Path(env_file).expanduser().resolve()) if env_file else "",
        "workspace_brief_path": str(workspace_brief.workspace_brief_path(resolved_team_dir)),
        "endpoint_registry_path": str(registry_path),
        "routing_policy_path": str(routing_path),
        "workspace_summary": written_workspace.get("summary", ""),
        "routing_summary": model_endpoint_adapter.summarize_model_routing(
            resolved_team_dir,
            entry={"model_routing_profile": routing_profile},
        ),
        "registry_summary": model_endpoint_adapter.summarize_model_endpoint_registry(resolved_team_dir),
        "harness_summary": harness_summary,
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compile a stack manifest and env overlay into canonical AOE runtime artifacts.")
    p.add_argument("--project-root", required=True, help="project root for relative path resolution")
    p.add_argument("--team-dir", required=True, help="target .aoe-team directory for compiled artifacts")
    p.add_argument("--manifest", required=True, help="path to aoe stack manifest JSON")
    p.add_argument("--env-file", default="", help="optional env overlay file for base URLs and similar runtime values")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    result = compile_stack(
        manifest_path=Path(args.manifest),
        team_dir=Path(args.team_dir),
        project_root=Path(args.project_root),
        env_file=Path(args.env_file) if _trim(args.env_file) else None,
    )
    print("stack compile complete")
    print(f"- workspace: {result['workspace_summary']}")
    print(f"- harness: {result['harness_summary']}")
    print(f"- model_routing: {result['routing_summary']}")
    print(f"- model_registry: {result['registry_summary']}")
    print(f"- workspace_brief_path: {result['workspace_brief_path']}")
    print(f"- endpoint_registry_path: {result['endpoint_registry_path']}")
    print(f"- routing_policy_path: {result['routing_policy_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
