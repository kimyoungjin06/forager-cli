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
import aoe_tg_worker_task_contract as worker_task_contract  # noqa: E402
from aoe_tg_request_contract import build_local_background_provider_invoke_launch_spec  # noqa: E402
from aoe_tg_request_contract import build_local_background_provider_task_launch_spec  # noqa: E402


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


def test_invoke_model_binding_executes_openai_responses(tmp_path: Path, monkeypatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "openai-judge",
                        "provider_kind": "openai",
                        "model": "gpt-5.4",
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
                    "offdesk_judge": {"endpoint_id": "openai-judge"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "https://api.openai.com/v1/responses"
        assert payload["model"] == "gpt-5.4"
        assert payload["input"] == "judge this"
        assert payload["instructions"] == "system prompt"
        assert timeout_sec == 30.0
        return {"output_text": "openai: ok"}

    result = provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1"},
        prompt="judge this",
        system="system prompt",
        pack_profile_override="review",
        post_json=_fake_post,
    )

    assert result["kind"] == "judge"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["provider_kind"] == "openai"
    assert result["model"] == "gpt-5.4"
    assert result["response_text"] == "openai: ok"


def test_invoke_model_binding_executes_claude_code_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(provider_adapter.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    def _fake_run(argv, *, timeout_sec=30.0, cwd=""):
        assert argv[0] == "/usr/bin/claude"
        assert "-p" in argv
        assert "--model" in argv
        assert "opus" in argv
        return {"ok": True, "exit_code": 0, "stdout": "CLI_JUDGE_OK", "stderr": ""}

    result = provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry={"project_root": str(tmp_path)},
        task={"request_id": "REQ-1"},
        prompt="judge this",
        system="Return the exact token only.",
        post_json=_fake_run,
    )

    assert result["kind"] == "judge"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["provider_kind"] == "claude_code_cli"
    assert result["response_text"] == "CLI_JUDGE_OK"


def test_invoke_model_binding_falls_back_from_claude_cli_to_anthropic_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
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
                    },
                    {
                        "endpoint_id": "anthropic-judge",
                        "provider_kind": "anthropic",
                        "model": "claude-opus-4.1",
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
                    "offdesk_judge": {
                        "endpoint_id": "claude-cli-opus",
                        "fallback_ids": ["anthropic-judge"],
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(provider_adapter.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    def _hybrid_call(arg1, arg2=None, *, timeout_sec=30.0, cwd=""):
        if isinstance(arg1, list):
            return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "not logged in"}
        assert arg1 == "https://api.anthropic.com/v1/messages"
        assert isinstance(arg2, dict)
        return {"content": [{"type": "text", "text": "FALLBACK_OK"}]}

    result = provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry={"project_root": str(tmp_path)},
        task={"request_id": "REQ-1"},
        prompt="judge this",
        system="Return the exact token only.",
        post_json=_hybrid_call,
    )

    assert result["ok"] is True
    assert result["executed"] is True
    assert result["provider_kind"] == "anthropic"
    assert result["response_text"] == "FALLBACK_OK"
    assert result["fallback_used"] is True
    assert result["fallback_from_endpoint_id"] == "claude-cli-opus"


def test_invoke_model_binding_executes_anthropic_messages(tmp_path: Path, monkeypatch) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    (team_dir / "model_endpoints.json").write_text(
        json.dumps(
            {
                "version": 1,
                "endpoints": [
                    {
                        "endpoint_id": "anthropic-judge",
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
                    "offdesk_judge": {"endpoint_id": "anthropic-judge"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "https://api.anthropic.com/v1/messages"
        assert payload["model"] == "claude-opus-4.1"
        assert payload["system"] == "system prompt"
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "decide now"
        return {"content": [{"type": "text", "text": "anthropic: ok"}]}

    result = provider_adapter.invoke_task_judge_stub(
        team_dir,
        entry={"model_routing_profile": "hybrid_local_exec"},
        task={"request_id": "REQ-1"},
        prompt="decide now",
        system="system prompt",
        pack_profile_override="review",
        post_json=_fake_post,
    )

    assert result["kind"] == "judge"
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["provider_kind"] == "anthropic"
    assert result["model"] == "claude-opus-4.1"
    assert result["response_text"] == "anthropic: ok"


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


def test_invoke_background_ticket_worker_renders_task_contract_when_prompt_missing(tmp_path: Path) -> None:
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
                "routes": {"background_worker_primary": {"endpoint_id": "ollama-qwen3"}},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    contract = worker_task_contract.sanitize_worker_task_contract(
        {
            "request_id": "REQ-1",
            "task_id": "T-001",
            "task_label": "T-001",
            "project_alias": "O2",
            "project_label": "Alpha",
            "pack_profile": "offdesk_execute",
            "objective": "Ship a bounded worker summary.",
            "execution_brief_status": "executable",
            "execution_brief_summary": "do=summary",
            "required_outputs": ["summary_report"],
            "artifact_targets": ["reports/summary.md"],
            "constraints": ["run_lock=test_only"],
            "doc_paths": ["docs/RUNBOOK.md"],
        }
    )
    launch_spec = build_local_background_provider_task_launch_spec(
        request_id="REQ-1",
        project_key="alpha",
        project_root=str(tmp_path),
        team_dir=str(team_dir),
        task_contract_json=json.dumps(contract, ensure_ascii=False),
        task_contract_summary=str(contract.get("summary", "")).strip(),
        task_contract_profile="offdesk_execute",
        timeout_sec=19,
    )

    def _fake_post(url: str, payload: dict, *, timeout_sec: float = 30.0):
        assert url == "http://172.16.0.37:11434/api/generate"
        assert payload["model"] == "qwen3-coder:30b"
        assert "Ship a bounded worker summary." in payload["prompt"]
        assert "\"module_kind\": \"writing\"" in payload["prompt"]
        assert "\"module_policy\": \"doc_quality_gate\"" in payload["prompt"]
        assert "\"module_repeat_when\": \"quality_gate_open\"" in payload["prompt"]
        assert "\"doc_paths\": [" in payload["prompt"]
        assert payload["system"] == worker_task_contract.WORKER_TASK_SYSTEM
        assert timeout_sec == 19.0
        return {
            "response": json.dumps(
                {
                    "status": "ready",
                    "summary": "worker summary drafted",
                    "actions": ["update reports/summary.md"],
                    "cautions": ["keep review lane open"],
                    "evidence_refs": ["reports/summary.md"],
                }
            ),
            "done": True,
        }

    result = provider_adapter.invoke_background_ticket_worker(
        team_dir,
        ticket={"ticket_id": "BGT-1", "launch_spec": launch_spec},
        post_json=_fake_post,
    )

    assert result["ok"] is True
    assert result["executed"] is True
    assert result["task_contract_summary"] == contract["summary"]
    assert result["task_result_status"] == "ready"
    assert result["task_result_summary"] == "status=ready | worker summary drafted | actions=1 | cautions=1 | refs=1"
    assert result["task_gate_status"] == "quality_open"
    assert result["task_gate_summary"] == "state=quality_open | docs=1 | refs=1 | repeat=quality_gate_open"
    assert result["task_result_actions"] == ["update reports/summary.md"]
    assert result["task_result_cautions"] == ["keep review lane open"]
    assert result["task_result_evidence_refs"] == ["reports/summary.md"]
    assert result["task_update_stub_status"] == "ready"
    assert result["task_contract_summary"].startswith("module=writing | task=T-001 | pack=offdesk_execute")
    assert result["task_update_stub_summary"] == (
        "module=writing | status=ready | targets=reports/summary.md | actions=1 | refs=1"
    )
    assert result["task_update_stub_targets"] == ["reports/summary.md"]
