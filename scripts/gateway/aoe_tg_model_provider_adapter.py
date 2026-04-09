#!/usr/bin/env python3
"""Provider-side invocation helpers for modular model routes."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict

import aoe_tg_model_endpoint_adapter as endpoint_adapter


def _trim(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _default_post_json(url: str, payload: Dict[str, Any], *, timeout_sec: float = 30.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(0.1, float(timeout_sec or 30.0))) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return raw if isinstance(raw, dict) else {}


def invoke_model_binding(
    binding: Any,
    *,
    prompt: Any,
    system: Any = "",
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding_data = binding if isinstance(binding, dict) else {}
    endpoint = binding_data.get("endpoint") if isinstance(binding_data.get("endpoint"), dict) else {}
    route = binding_data.get("route") if isinstance(binding_data.get("route"), dict) else {}
    route_id = _trim(route.get("route_id"), 64).lower() or "route"
    endpoint_id = _trim(endpoint.get("endpoint_id"), 64).lower()
    provider_kind = _trim(endpoint.get("provider_kind"), 64).lower() or "custom"
    base_url = _trim(endpoint.get("base_url"), 240).rstrip("/")
    model = _trim(endpoint.get("model"), 128)
    prompt_text = _trim(prompt, 8000)
    system_text = _trim(system, 4000)
    if not binding_data.get("bound"):
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "reason_code": "model_route_unbound",
            "summary": _trim(binding_data.get("summary"), 240) or f"{route_id}=unbound",
            "binding": binding_data,
        }
    if not prompt_text:
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "reason_code": "empty_prompt",
            "summary": f"route={route_id} endpoint={endpoint_id or '-'} reason=empty_prompt",
            "binding": binding_data,
        }
    if provider_kind != "ollama":
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "reason_code": "unsupported_provider_invoke",
            "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind or '-'} status=unsupported_invoke",
            "binding": binding_data,
        }
    if not base_url or not model:
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "reason_code": "missing_endpoint_metadata",
            "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider=ollama status=missing_metadata",
            "binding": binding_data,
        }
    invoke = post_json if callable(post_json) else _default_post_json
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt_text,
        "stream": False,
    }
    if system_text:
        payload["system"] = system_text
    try:
        response = invoke(f"{base_url}/api/generate", payload, timeout_sec=timeout_sec)
    except Exception as exc:
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "model": model,
            "reason_code": "provider_request_failed",
            "error": _trim(exc, 240),
            "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider=ollama status=request_failed",
            "binding": binding_data,
        }
    text = _trim(response.get("response"), 8000)
    done = bool(response.get("done"))
    eval_count = int(response.get("eval_count", 0) or 0)
    prompt_eval_count = int(response.get("prompt_eval_count", 0) or 0)
    return {
        "ok": bool(text),
        "executed": True,
        "route_id": route_id,
        "endpoint_id": endpoint_id,
        "provider_kind": provider_kind,
        "model": model,
        "done": done,
        "response_text": text,
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
        "summary": (
            f"route={route_id} endpoint={endpoint_id or '-'} provider=ollama "
            f"model={model or '-'} done={'yes' if done else 'no'} "
            f"prompt_eval={prompt_eval_count} eval={eval_count}"
        ),
        "binding": binding_data,
        "raw": response if isinstance(response, dict) else {},
    }


def invoke_task_judge_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "judge"
    return result


def invoke_task_escalation_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_escalation_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "escalation"
    return result
