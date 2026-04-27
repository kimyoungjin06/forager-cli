#!/usr/bin/env python3
"""Executor adapter runtime helpers for launch spec materialization and dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import emit_external_background_handoff
from aoe_tg_request_contract import (
    build_external_runner_gateway_command_launch_spec,
    build_local_tmux_gateway_command_launch_spec,
    build_local_tmux_gateway_run_launch_spec,
)
from aoe_tg_tmux_background_worker import launch_local_tmux_background_ticket


RUNNER_ADAPTER_TARGETS = ("local_tmux", "github_runner", "remote_worker")


def build_gateway_command_launch_spec_for_adapter(
    *,
    runner_target: Any,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    command_text: str,
    simulate_chat_id: str = "local-background",
    no_owner_only: bool = False,
    no_deny_by_default: bool = False,
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    target = normalize_executor_runner_target(runner_target)
    if target == "local_tmux":
        return build_local_tmux_gateway_command_launch_spec(
            request_id=request_id,
            project_key=project_key,
            project_root=project_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            command_text=command_text,
            simulate_chat_id=simulate_chat_id,
            no_owner_only=no_owner_only,
            no_deny_by_default=no_deny_by_default,
            launch_mode=launch_mode,
            source_surface=source_surface,
            created_by=created_by,
        )
    if target in {"github_runner", "remote_worker"}:
        return build_external_runner_gateway_command_launch_spec(
            runner_target=target,
            request_id=request_id,
            project_key=project_key,
            project_root=project_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            command_text=command_text,
            simulate_chat_id=simulate_chat_id,
            no_owner_only=no_owner_only,
            no_deny_by_default=no_deny_by_default,
            launch_mode=launch_mode,
            source_surface=source_surface,
            created_by=created_by,
        )
    return {}


def build_gateway_run_launch_spec_for_adapter(
    *,
    runner_target: Any,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    orch_target: str = "",
    prompt: str,
    roles: Optional[List[str]] = None,
    priority: str = "",
    timeout_sec: int | None = None,
    force_mode: str = "dispatch",
    simulate_chat_id: str = "local-background",
    no_owner_only: bool = False,
    no_deny_by_default: bool = False,
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    target = normalize_executor_runner_target(runner_target)
    if target != "local_tmux":
        return {}
    return build_local_tmux_gateway_run_launch_spec(
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        orch_target=orch_target,
        prompt=prompt,
        roles=roles,
        priority=priority,
        timeout_sec=timeout_sec,
        force_mode=force_mode,
        simulate_chat_id=simulate_chat_id,
        no_owner_only=no_owner_only,
        no_deny_by_default=no_deny_by_default,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
    )


def launch_background_ticket_via_adapter(
    *,
    queue_path: Path,
    ticket_id: str,
    runner_target: Any,
    now_iso: Callable[[], str],
    claimed_by: str = "",
    source_surface: str = "",
    launch_mode: str = "offdesk_manual",
) -> Dict[str, Any]:
    target = normalize_executor_runner_target(runner_target)
    if target == "local_tmux":
        return launch_local_tmux_background_ticket(
            queue_path=queue_path,
            ticket_id=ticket_id,
            now_iso=now_iso,
            claimed_by=claimed_by,
            source_surface=source_surface,
            launch_mode=launch_mode,
        )
    if target in {"github_runner", "remote_worker"}:
        return emit_external_background_handoff(
            queue_path=queue_path,
            ticket_id=ticket_id,
            runner_target=target,
            now_iso=now_iso,
            claimed_by=claimed_by,
            source_surface=source_surface,
            launch_mode=launch_mode,
        )
    return {}
