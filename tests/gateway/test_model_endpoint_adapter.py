#!/usr/bin/env python3
"""Model endpoint registry and routing regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


def test_task_model_plan_uses_context_pack_profile(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "judge-claude",
                        "provider_kind": "anthropic",
                        "model": "claude-opus-4.1",
                        "enabled": True,
                    },
                    {
                        "endpoint_id": "ollama-qwen3",
                        "provider_kind": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "qwen3-coder:30b",
                        "enabled": True,
                    },
                    {
                        "endpoint_id": "ollama-gptoss",
                        "provider_kind": "ollama",
                        "base_url": "http://127.0.0.1:11434",
                        "model": "gpt-oss:120b",
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
                "profile": "hybrid_local_exec",
                "routes": {
                    "offdesk_judge": {"endpoint_id": "judge-claude"},
                    "background_worker_primary": {"endpoint_id": "ollama-qwen3"},
                    "background_worker_escalation": {"endpoint_id": "ollama-gptoss"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    plan = model_endpoint_adapter.resolve_task_model_plan(
        team_dir,
        entry={"project_root": str(tmp_path), "model_routing_profile": "hybrid_local_exec"},
        task={
            "request_id": "REQ-1",
            "control_mode": "followup",
            "followup_brief_status": "partially_executable",
        },
    )

    assert plan["pack_profile"] == "followup_execute"
    assert "pack=followup_execute" in plan["summary"]
    assert "worker=bg=ollama-qwen3:qwen3-coder:30b" in plan["summary"]
    assert "judge=judge=judge-claude:claude-opus-4.1" in plan["summary"]
    assert "escalation=bgx=ollama-gptoss:gpt-oss:120b" in plan["summary"]


def test_resolve_task_worker_binding_prefers_background_ticket_metadata(tmp_path: Path) -> None:
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

    binding = model_endpoint_adapter.resolve_task_worker_binding(
        team_dir,
        task={
            "background_run_model_plan_summary": "pack=review | worker=bg=ollama-qwen3:qwen3-coder:30b",
            "background_run_model_worker_route_id": "background_worker_primary",
            "background_run_model_worker_endpoint_id": "ollama-qwen3",
        },
    )

    assert binding["source"] == "background_ticket"
    assert binding["bound"] is True
    assert binding["endpoint"]["model"] == "qwen3-coder:30b"


def test_probe_model_route_reports_ollama_tags(tmp_path: Path) -> None:
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

    def _fake_fetch(url: str, *, timeout_sec: float = 3.0):
        assert url == "http://172.16.0.37:11434/api/tags"
        assert timeout_sec == 3.0
        return {
            "models": [
                {"name": "qwen3-coder:30b"},
                {"name": "gemma4:26b"},
            ]
        }

    result = model_endpoint_adapter.probe_model_route(
        team_dir,
        "background_worker_primary",
        fetch_json=_fake_fetch,
    )

    assert result["ok"] is True
    assert result["probe_status"] == "ok"
    assert result["model_present"] is True
    assert "qwen3-coder:30b" in result["available_model_names"]


def test_probe_task_judge_binding_reports_missing_api_key_for_anthropic(tmp_path: Path, monkeypatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "judge-claude",
                        "provider_kind": "anthropic",
                        "model": "claude-opus-4.1",
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
                    "offdesk_judge": {"endpoint_id": "judge-claude"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = model_endpoint_adapter.probe_task_judge_binding(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1", "followup_brief_status": "preview_only"},
    )

    assert result["ok"] is False
    assert result["route_id"] == "offdesk_judge"
    assert result["probe_status"] == "missing_api_key"
    assert "missing_api_key" in result["summary"]
    assert "provider=anthropic" in result["summary"]


def test_probe_model_endpoint_reports_claude_cli_logged_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    endpoint = {
        "endpoint_id": "claude-cli-opus",
        "provider_kind": "claude_code_cli",
        "model": "opus",
        "enabled": True,
    }
    monkeypatch.setattr(model_endpoint_adapter.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(
        model_endpoint_adapter,
        "_run_subprocess",
        lambda argv, timeout_sec=5.0: {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "team"}),
            "stderr": "",
        },
    )

    result = model_endpoint_adapter.probe_model_endpoint(endpoint)

    assert result["ok"] is True
    assert result["probe_status"] == "logged_in"
    assert "provider=claude_code_cli" in result["summary"]


def test_summarize_deferred_model_binding_probe_marks_claude_cli_as_deferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "claude-cli-opus",
                        "provider_kind": "claude_code_cli",
                        "model": "opus",
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
                    "offdesk_judge": {"endpoint_id": "claude-cli-opus"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(model_endpoint_adapter.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1"},
    )
    result = model_endpoint_adapter.summarize_deferred_model_binding_probe(binding, default_label="judge")

    assert result["ok"] is False
    assert result["probe_status"] == "deferred_login_probe"
    assert "provider=claude_code_cli" in result["summary"]


def test_summarize_deferred_model_binding_probe_marks_bound_ollama_as_deferred(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "ollama-gptoss",
                        "provider_kind": "ollama",
                        "base_url": "http://172.16.0.37:11434",
                        "model": "gpt-oss:120b",
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
                    "background_worker_escalation": {"endpoint_id": "ollama-gptoss"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    binding = model_endpoint_adapter.resolve_task_escalation_binding(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1", "tf_phase": "review"},
        pack_profile_override="review",
    )
    summary = model_endpoint_adapter.summarize_deferred_model_binding_probe(
        binding,
        default_label="background_worker_escalation",
    )

    assert binding["bound"] is True
    assert binding["summary"] == "bgx=ollama-gptoss:gpt-oss:120b"
    assert summary["probe_status"] == "deferred_live_probe"
    assert summary["route_id"] == "background_worker_escalation"
    assert "endpoint=ollama-gptoss provider=ollama status=deferred_live_probe" == summary["summary"]
