#!/usr/bin/env python3
"""Seed helper regressions for modular model endpoint configs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_model_endpoint_seed as seed_mod  # noqa: E402


def test_build_ollama_seed_payload_binds_background_routes() -> None:
    registry, policy = seed_mod.build_ollama_seed_payload(
        base_url="http://172.16.0.37:11434",
        qwen_model="qwen3-coder:30b",
        gpt_oss_model="gpt-oss:120b",
        gemma_model="gemma4:26b",
    )

    endpoints = registry["endpoints"]
    assert len(endpoints) == 3
    assert {row["provider_kind"] for row in endpoints} == {"ollama"}
    assert policy["profile"] == "hybrid_local_exec"
    assert policy["routes"]["background_worker_primary"]["endpoint_id"].startswith("ollama-qwen3-coder-30b")
    assert policy["routes"]["background_worker_escalation"]["endpoint_id"].startswith("ollama-gpt-oss-120b")
    assert policy["routes"]["research_synthesis"]["endpoint_id"].startswith("ollama-gemma4-26b")
    assert policy["routes"]["offdesk_judge"]["endpoint_id"] == ""


def test_write_ollama_seed_files_writes_registry_and_policy(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    result = seed_mod.write_ollama_seed_files(
        team_dir=team_dir,
        base_url="http://172.16.0.37:11434",
        qwen_model="qwen3-coder:30b",
        gpt_oss_model="gpt-oss:120b",
        gemma_model="gemma4:26b",
    )

    registry_path = team_dir / "model_endpoints.json"
    policy_path = team_dir / "model_routing.json"
    assert registry_path.exists()
    assert policy_path.exists()

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    assert registry["endpoints"][0]["base_url"] == "http://172.16.0.37:11434"
    assert policy["routes"]["background_worker_primary"]["endpoint_id"]
    assert policy["routes"]["research_synthesis"]["endpoint_id"]
    assert policy["routes"]["offdesk_judge"]["endpoint_id"] == ""
    assert "profile=hybrid_local_exec" in result["routing_summary"]
    assert "bg=ollama-qwen3-coder-30b:qwen3-coder:30b" in result["routing_summary"]
    assert "research=ollama-gemma4-26b:gemma4:26b" in result["routing_summary"]
    assert "enabled=3 bound=3/5 local=3 kinds=ollama=3" == result["registry_summary"]
