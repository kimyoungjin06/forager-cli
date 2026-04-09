#!/usr/bin/env python3
"""Seed helper for modular model endpoint configs.

This writes:
- <team_dir>/model_endpoints.json
- <team_dir>/model_routing.json

It is intended for swappable endpoint binding, not for live inference.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
from aoe_tg_runtime_core import model_endpoint_registry_path, model_routing_policy_path


def _trim(raw: Any, limit: int = 128) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _slug(raw: str) -> str:
    out = []
    last_dash = False
    for char in _trim(raw, 128).lower():
        if char.isalnum():
            out.append(char)
            last_dash = False
            continue
        if not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-") or "endpoint"


def _endpoint_id(prefix: str, model_name: str) -> str:
    return f"{prefix}-{_slug(model_name)}"[:64]


def _build_ollama_endpoint(base_url: str, model_name: str, roles: list[str]) -> Dict[str, Any]:
    return {
        "endpoint_id": _endpoint_id("ollama", model_name),
        "provider_kind": "ollama",
        "base_url": _trim(base_url, 240),
        "model": _trim(model_name, 128),
        "enabled": True,
        "local": True,
        "supports_tools": False,
        "supports_json": True,
        "roles": roles,
    }


def _build_remote_endpoint(
    *,
    provider_kind: str,
    model_name: str,
    roles: list[str],
    base_url: str = "",
    api_key_env: str = "",
    supports_tools: bool | None = None,
) -> Dict[str, Any]:
    normalized_kind = model_endpoint_adapter.normalize_model_endpoint_kind(provider_kind, "custom")
    tools_default = normalized_kind in {"openai", "openai_compatible"}
    return {
        "endpoint_id": _endpoint_id(normalized_kind, model_name),
        "provider_kind": normalized_kind,
        "base_url": _trim(base_url, 240),
        "model": _trim(model_name, 128),
        "api_key_env": _trim(api_key_env, 128),
        "enabled": True,
        "local": False,
        "supports_tools": tools_default if supports_tools is None else bool(supports_tools),
        "supports_json": True,
        "roles": roles,
    }


def build_ollama_seed_payload(
    *,
    base_url: str,
    qwen_model: str,
    gpt_oss_model: str,
    gemma_model: str,
    judge_provider: str = "",
    judge_model: str = "",
    judge_base_url: str = "",
    judge_api_key_env: str = "",
    profile: str = "hybrid_local_exec",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    endpoints = []
    route_ids: Dict[str, str] = {}

    qwen_token = _trim(qwen_model, 128)
    if qwen_token:
        row = _build_ollama_endpoint(base_url, qwen_token, ["background_worker_primary"])
        endpoints.append(row)
        route_ids["background_worker_primary"] = str(row["endpoint_id"])

    gpt_oss_token = _trim(gpt_oss_model, 128)
    if gpt_oss_token:
        row = _build_ollama_endpoint(base_url, gpt_oss_token, ["background_worker_escalation"])
        endpoints.append(row)
        route_ids["background_worker_escalation"] = str(row["endpoint_id"])

    gemma_token = _trim(gemma_model, 128)
    if gemma_token:
        row = _build_ollama_endpoint(base_url, gemma_token, [])
        endpoints.append(row)
        route_ids["research_synthesis"] = str(row["endpoint_id"])

    judge_provider_token = model_endpoint_adapter.normalize_model_endpoint_kind(judge_provider, "custom")
    judge_model_token = _trim(judge_model, 128)
    if judge_provider_token and judge_provider_token != "custom" and judge_model_token:
        row = _build_remote_endpoint(
            provider_kind=judge_provider_token,
            model_name=judge_model_token,
            roles=["offdesk_judge"],
            base_url=judge_base_url,
            api_key_env=judge_api_key_env,
        )
        endpoints.append(row)
        route_ids["offdesk_judge"] = str(row["endpoint_id"])

    registry = model_endpoint_adapter.sanitize_model_endpoint_registry(
        {
            "version": 1,
            "endpoints": endpoints,
        }
    )
    policy = model_endpoint_adapter.sanitize_model_routing_policy(
        {
            "version": 1,
            "profile": _trim(profile, 64) or "hybrid_local_exec",
            "routes": {
                "on_desk_primary": {
                    "family_hint": "anthropic",
                    "model_hint": "claude-sonnet-4",
                },
                "research_synthesis": {
                    "endpoint_id": route_ids.get("research_synthesis", ""),
                    "family_hint": "google",
                    "model_hint": "gemini-2.5-pro",
                },
                "offdesk_judge": {
                    "endpoint_id": route_ids.get("offdesk_judge", ""),
                    "family_hint": "anthropic",
                    "model_hint": "claude-opus-4.1",
                },
                "background_worker_primary": {
                    "endpoint_id": route_ids.get("background_worker_primary", ""),
                },
                "background_worker_escalation": {
                    "endpoint_id": route_ids.get("background_worker_escalation", ""),
                },
            },
        }
    )
    return registry, policy


def write_ollama_seed_files(
    *,
    team_dir: Path,
    base_url: str,
    qwen_model: str,
    gpt_oss_model: str,
    gemma_model: str,
    judge_provider: str = "",
    judge_model: str = "",
    judge_base_url: str = "",
    judge_api_key_env: str = "",
    profile: str = "hybrid_local_exec",
) -> Dict[str, str]:
    registry, policy = build_ollama_seed_payload(
        base_url=base_url,
        qwen_model=qwen_model,
        gpt_oss_model=gpt_oss_model,
        gemma_model=gemma_model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_base_url=judge_base_url,
        judge_api_key_env=judge_api_key_env,
        profile=profile,
    )
    team_dir = Path(team_dir).expanduser().resolve()
    team_dir.mkdir(parents=True, exist_ok=True)
    registry_path = model_endpoint_registry_path(team_dir)
    policy_path = model_routing_policy_path(team_dir)
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "registry_path": str(registry_path),
        "policy_path": str(policy_path),
        "routing_summary": model_endpoint_adapter.summarize_model_routing(team_dir),
        "registry_summary": model_endpoint_adapter.summarize_model_endpoint_registry(team_dir),
    }


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Seed modular model endpoint config files for an Ollama server.")
    p.add_argument("--team-dir", required=True, help="target team_dir where model_endpoints.json and model_routing.json will be written")
    p.add_argument("--ollama-base-url", required=True, help="Ollama base URL, for example http://172.16.0.37:11434")
    p.add_argument("--qwen-model", default="", help="Qwen coding model name")
    p.add_argument("--gpt-oss-model", default="", help="gpt-oss model name")
    p.add_argument("--gemma-model", default="", help="Gemma model name")
    p.add_argument("--judge-provider", default="", help="optional judge provider kind: anthropic|openai|openai_compatible")
    p.add_argument("--judge-model", default="", help="optional judge model name")
    p.add_argument("--judge-base-url", default="", help="optional custom base URL for judge endpoint")
    p.add_argument("--judge-api-key-env", default="", help="optional env var name for judge API key")
    p.add_argument("--profile", default="hybrid_local_exec", help="routing profile label")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    result = write_ollama_seed_files(
        team_dir=Path(args.team_dir),
        base_url=args.ollama_base_url,
        qwen_model=args.qwen_model,
        gpt_oss_model=args.gpt_oss_model,
        gemma_model=args.gemma_model,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        judge_base_url=args.judge_base_url,
        judge_api_key_env=args.judge_api_key_env,
        profile=args.profile,
    )
    print("model endpoint seed written")
    print(f"- registry_path: {result['registry_path']}")
    print(f"- policy_path: {result['policy_path']}")
    print(f"- model_routing: {result['routing_summary']}")
    print(f"- model_registry: {result['registry_summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
