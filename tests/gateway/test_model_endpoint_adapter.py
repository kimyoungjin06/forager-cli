#!/usr/bin/env python3
"""Model endpoint registry and routing regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter  # noqa: E402


def test_model_routing_defaults_to_unbound_hints_without_config(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)

    routing_summary = model_endpoint_adapter.summarize_model_routing(team_dir)
    registry_summary = model_endpoint_adapter.summarize_model_endpoint_registry(team_dir)

    assert "profile=default" in routing_summary
    assert "ondesk=unbound:claude-sonnet" in routing_summary
    assert "research=unbound:gemini-2.5-pro" in routing_summary
    assert "judge=unbound:claude-opus-4.1" in routing_summary
    assert "bg=unbound:qwen3-coder" in routing_summary
    assert "enabled=0 bound=0/5 local=0 kinds=-" == registry_summary


def test_model_routing_binds_registered_endpoints(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "claude-sonnet-shell",
                        "provider_kind": "anthropic",
                        "model": "claude-sonnet-4",
                        "enabled": True,
                        "local": False,
                        "supports_tools": True,
                    },
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (team_dir / "model_routing.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profile": "default",
                "routes": {
                    "on_desk_primary": {
                        "endpoint_id": "claude-sonnet-shell",
                    },
                    "background_worker_primary": {
                        "endpoint_id": "ollama-qwen3",
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    route = model_endpoint_adapter.resolve_model_route(team_dir, "background_worker_primary")
    routing_summary = model_endpoint_adapter.summarize_model_routing(team_dir)
    registry_summary = model_endpoint_adapter.summarize_model_endpoint_registry(team_dir)

    assert route["bound"] is True
    assert route["effective_kind"] == "ollama"
    assert route["effective_model"] == "qwen3-coder:30b"
    assert route["effective_local"] is True
    assert "ondesk=claude-sonnet-shell:claude-sonnet-4" in routing_summary
    assert "bg=ollama-qwen3:qwen3-coder:30b" in routing_summary
    assert "enabled=2 bound=2/5 local=1 kinds=anthropic=1, ollama=1" == registry_summary
