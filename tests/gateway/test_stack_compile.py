#!/usr/bin/env python3
"""Stack manifest compiler regressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_stack_compile as stack_compile  # noqa: E402


def test_stack_compile_writes_workspace_and_model_artifacts(tmp_path: Path) -> None:
    project_root = tmp_path / "Alpha"
    team_dir = project_root / ".aoe-team"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    team_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (project_root / "docs" / "RUNBOOK.md").write_text("# RUNBOOK\n", encoding="utf-8")
    manifest_path = project_root / "aoe_stack.json"
    env_path = project_root / ".env"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "profile": "hybrid_local_exec",
                "workspace": {
                    "workspace_key": "alpha",
                    "project_alias": "O2",
                    "project_overview": "alpha runtime",
                    "doc_roots": ["docs"],
                    "canonical_todo_path": "TODO.md",
                    "canonical_runbook_paths": ["docs/RUNBOOK.md"],
                    "background_runner_target": "local_tmux",
                    "run_lock_mode_default": "test_only",
                    "background_runner_slot_limits": {"local_tmux": 1, "github_runner": 2},
                },
                "models": {
                    "qwen_local": {
                        "provider_kind": "ollama",
                        "base_url_env": "OLLAMA_BASE_URL",
                        "model": "qwen3-coder:30b",
                        "route": "background_worker_primary",
                    },
                    "gptoss_local": {
                        "provider_kind": "ollama",
                        "base_url_env": "OLLAMA_BASE_URL",
                        "model": "gpt-oss:120b",
                        "route": "background_worker_escalation",
                    },
                    "judge_claude": {
                        "provider_kind": "anthropic",
                        "model": "claude-opus-4.1",
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "route": "offdesk_judge",
                        "local": False,
                    },
                },
                "harness": {
                    "on_desk": {"kind": "claude_code"},
                    "off_desk": {"kind": "aoe_orch_control"},
                    "off_desk_executor": {"kind": "local_tmux"},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env_path.write_text("OLLAMA_BASE_URL=http://172.16.0.37:11434\n", encoding="utf-8")

    result = stack_compile.compile_stack(
        manifest_path=manifest_path,
        team_dir=team_dir,
        project_root=project_root,
        env_file=env_path,
    )

    workspace = json.loads((team_dir / "workspace_brief.json").read_text(encoding="utf-8"))
    endpoints = json.loads((team_dir / "model_endpoints.json").read_text(encoding="utf-8"))
    routing = json.loads((team_dir / "model_routing.json").read_text(encoding="utf-8"))

    assert workspace["project_alias"] == "O2"
    assert workspace["background_runner_target"] == "local_tmux"
    assert workspace["run_lock_mode_default"] == "test_only"
    assert workspace["doc_roots"] == [str(docs_dir.resolve())]
    assert workspace["canonical_todo_path"] == str((project_root / "TODO.md").resolve())
    assert "runner=local_tmux" in workspace["summary"]

    endpoint_rows = endpoints["endpoints"]
    qwen_row = next(row for row in endpoint_rows if row["model"] == "qwen3-coder:30b")
    judge_row = next(row for row in endpoint_rows if row["model"] == "claude-opus-4.1")
    assert qwen_row["base_url"] == "http://172.16.0.37:11434"
    assert qwen_row["local"] is True
    assert judge_row["api_key_env"] == "ANTHROPIC_API_KEY"
    assert judge_row["local"] is False

    assert routing["profile"] == "hybrid_local_exec"
    assert routing["routes"]["background_worker_primary"]["endpoint_id"] == qwen_row["endpoint_id"]
    assert routing["routes"]["offdesk_judge"]["endpoint_id"] == judge_row["endpoint_id"]

    assert "on_desk=claude_code" in result["harness_summary"]
    assert "bg=" in result["routing_summary"]
    assert "enabled=3" in result["registry_summary"]


def test_stack_compile_explicit_route_ref_overrides_inferred_binding(tmp_path: Path) -> None:
    project_root = tmp_path / "Beta"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = project_root / "aoe_stack.json"
    env_path = project_root / ".env"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "workspace": {
                    "project_alias": "O5",
                    "background_runner_target": "github_runner",
                },
                "models": {
                    "qwen_local": {
                        "provider_kind": "ollama",
                        "base_url_env": "OLLAMA_BASE_URL",
                        "model": "qwen3-coder:30b",
                        "route": "background_worker_primary",
                    },
                    "gemma_local": {
                        "provider_kind": "ollama",
                        "base_url_env": "OLLAMA_BASE_URL",
                        "model": "gemma4:26b",
                    },
                },
                "routing": {
                    "profile": "hybrid_local_exec",
                    "routes": {
                        "background_worker_primary": {"endpoint_ref": "gemma_local"},
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    env_path.write_text("OLLAMA_BASE_URL=http://172.16.0.37:11434\n", encoding="utf-8")

    stack_compile.compile_stack(
        manifest_path=manifest_path,
        team_dir=team_dir,
        project_root=project_root,
        env_file=env_path,
    )
    endpoints = json.loads((team_dir / "model_endpoints.json").read_text(encoding="utf-8"))["endpoints"]
    routing = json.loads((team_dir / "model_routing.json").read_text(encoding="utf-8"))

    gemma_id = next(row["endpoint_id"] for row in endpoints if row["model"] == "gemma4:26b")
    qwen_id = next(row["endpoint_id"] for row in endpoints if row["model"] == "qwen3-coder:30b")

    assert gemma_id != qwen_id
    assert routing["routes"]["background_worker_primary"]["endpoint_id"] == gemma_id
