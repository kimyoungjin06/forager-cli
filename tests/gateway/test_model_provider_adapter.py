#!/usr/bin/env python3
"""Provider-side model invocation regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_model_endpoint_adapter as endpoint_adapter  # noqa: E402
import aoe_tg_model_provider_adapter as provider_adapter  # noqa: E402
from aoe_tg_request_contract import build_local_background_provider_invoke_launch_spec  # noqa: E402


def test_invoke_model_binding_returns_unbound_without_execution(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    binding = endpoint_adapter.resolve_model_binding_snapshot(team_dir, "background_worker_primary")

    result = provider_adapter.invoke_model_binding(binding, prompt="hello")

    assert result["ok"] is False
    assert result["executed"] is False
    assert result["reason_code"] == "model_route_unbound"


def test_invoke_model_binding_executes_ollama_generate(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    }
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "background_worker_primary": {"endpoint_id": "ollama-qwen3"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    binding = endpoint_adapter.resolve_model_binding_snapshot(team_dir, "background_worker_primary")

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "qwen3-coder:30b"
        assert payload["prompt"] == "summarize"
        assert payload["system"] == "system prompt"
        assert payload["stream"] is False
        assert timeout_sec == 30.0
        return {
            "response": "ok: summarized",
            "done": True,
            "prompt_eval_count": 12,
            "eval_count": 48,
        }

    result = provider_adapter.invoke_model_binding(
        binding,
        prompt="summarize",
        system="system prompt",
        post_json=_fake_post,
    )

    assert result["ok"] is True
    assert result["executed"] is True
    assert result["provider_kind"] == "ollama"
    assert result["model"] == "qwen3-coder:30b"
    assert result["response_text"] == "ok: summarized"
    assert result["done"] is True
    assert result["prompt_eval_count"] == 12
    assert result["eval_count"] == 48


def test_invoke_task_judge_stub_uses_resolved_judge_route(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-gemma4",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "gemma4:26b",
                        "enabled": True,
                    }
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "offdesk_judge": {"endpoint_id": "ollama-gemma4"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "gemma4:26b"
        assert payload["prompt"] == "review this"
        return {"response": "judge: proceed", "done": True}

    result = provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1", "followup_brief_status": "preview_only"},
        prompt="review this",
        pack_profile_override="followup_preview",
        post_json=_fake_post,
    )

    assert result["kind"] == "judge"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["route_id"] == "offdesk_judge"
    assert result["model"] == "gemma4:26b"
    assert result["response_text"] == "judge: proceed"


def test_invoke_task_research_stub_uses_research_route(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-gemma4",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "gemma4:26b",
                        "enabled": True,
                    }
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "research_synthesis": {"endpoint_id": "ollama-gemma4"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "gemma4:26b"
        assert payload["prompt"] == "research this"
        return {"response": "research: ok", "done": True}

    result = provider_adapter.invoke_task_research_stub(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1"},
        prompt="research this",
        pack_profile_override="on_desk_plan",
        post_json=_fake_post,
    )

    assert result["kind"] == "research"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["route_id"] == "research_synthesis"
    assert result["model"] == "gemma4:26b"
    assert result["response_text"] == "research: ok"


def test_invoke_task_worker_stub_uses_worker_route_with_pack_override(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    }
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "background_worker_primary": {"endpoint_id": "ollama-qwen3"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "qwen3-coder:30b"
        assert payload["prompt"] == "execute this"
        return {"response": "worker: ok", "done": True}

    result = provider_adapter.invoke_task_worker_stub(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1"},
        prompt="execute this",
        pack_profile_override="offdesk_execute",
        post_json=_fake_post,
    )

    assert result["kind"] == "worker"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["route_id"] == "background_worker_primary"
    assert result["model"] == "qwen3-coder:30b"
    assert result["response_text"] == "worker: ok"


def test_invoke_background_ticket_worker_uses_launch_spec_prompt_and_route(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    }
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "background_worker_primary": {"endpoint_id": "ollama-qwen3"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    launch_spec = build_local_background_provider_invoke_launch_spec(
        request_id="REQ-1",
        project_key="alpha",
        project_root=str(tmp_path),
        team_dir=str(team_dir),
        prompt="reply with ticket ok",
        system="system prompt",
        timeout_sec=17,
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "qwen3-coder:30b"
        assert payload["prompt"] == "reply with ticket ok"
        assert payload["system"] == "system prompt"
        assert timeout_sec == 17.0
        return {"response": "ticket: ok", "done": True}

    result = provider_adapter.invoke_background_ticket_worker(
        team_dir,
        ticket={"ticket_id": "BGT-1", "launch_spec": launch_spec},
        post_json=_fake_post,
    )

    assert result["kind"] == "background_worker"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["route_id"] == "background_worker_primary"
    assert result["response_text"] == "ticket: ok"
