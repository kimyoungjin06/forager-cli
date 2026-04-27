#!/usr/bin/env python3
"""Executor adapter capability descriptors for background execution rails."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple


EXECUTOR_RUNNER_TARGETS: Tuple[str, ...] = (
    "local_background",
    "local_tmux",
    "github_runner",
    "remote_worker",
)
EXECUTOR_SLOT_RUNNER_TARGETS: Tuple[str, ...] = (
    "local_tmux",
    "github_runner",
    "remote_worker",
)
EXECUTOR_EXTERNAL_RUNNER_TARGETS: Tuple[str, ...] = (
    "local_tmux",
    "github_runner",
    "remote_worker",
)


@dataclass(frozen=True)
class ExecutorAdapterDescriptor:
    runner_target: str
    adapter_kind: str
    requires_externalizable_launch_spec: bool
    supports_in_process_callback: bool
    supports_serializable_gateway_command: bool
    supports_result_polling: bool
    supports_pickup_ack: bool
    supports_test_only_harness: bool
    slot_limited: bool
    operator_selected_only: bool
    summary: str


_EXECUTOR_ADAPTERS: Dict[str, ExecutorAdapterDescriptor] = {
    "local_background": ExecutorAdapterDescriptor(
        runner_target="local_background",
        adapter_kind="in_process_callback",
        requires_externalizable_launch_spec=False,
        supports_in_process_callback=True,
        supports_serializable_gateway_command=False,
        supports_result_polling=False,
        supports_pickup_ack=False,
        supports_test_only_harness=False,
        slot_limited=False,
        operator_selected_only=False,
        summary="same-process callback worker owned by the control plane",
    ),
    "local_tmux": ExecutorAdapterDescriptor(
        runner_target="local_tmux",
        adapter_kind="local_tmux_session",
        requires_externalizable_launch_spec=True,
        supports_in_process_callback=False,
        supports_serializable_gateway_command=True,
        supports_result_polling=True,
        supports_pickup_ack=False,
        supports_test_only_harness=False,
        slot_limited=True,
        operator_selected_only=False,
        summary="local tmux-backed adapter for serializable gateway command payloads",
    ),
    "github_runner": ExecutorAdapterDescriptor(
        runner_target="github_runner",
        adapter_kind="external_handoff",
        requires_externalizable_launch_spec=True,
        supports_in_process_callback=False,
        supports_serializable_gateway_command=True,
        supports_result_polling=True,
        supports_pickup_ack=True,
        supports_test_only_harness=True,
        slot_limited=True,
        operator_selected_only=True,
        summary="external GitHub runner handoff adapter with ack/result sidecars",
    ),
    "remote_worker": ExecutorAdapterDescriptor(
        runner_target="remote_worker",
        adapter_kind="external_handoff",
        requires_externalizable_launch_spec=True,
        supports_in_process_callback=False,
        supports_serializable_gateway_command=True,
        supports_result_polling=True,
        supports_pickup_ack=True,
        supports_test_only_harness=True,
        slot_limited=True,
        operator_selected_only=True,
        summary="external remote worker handoff adapter with ack/result sidecars",
    ),
}


def _trim(raw: Any, limit: int = 64) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def normalize_executor_runner_target(raw: Any, default: str = "") -> str:
    token = _trim(raw, 64).lower()
    if token in EXECUTOR_RUNNER_TARGETS:
        return token
    fallback = _trim(default, 64).lower()
    return fallback if fallback in EXECUTOR_RUNNER_TARGETS else ""


def executor_adapter_descriptor(raw: Any, default: str = "") -> ExecutorAdapterDescriptor:
    token = normalize_executor_runner_target(raw, default)
    if not token:
        token = "local_background"
    return _EXECUTOR_ADAPTERS[token]


def executor_requires_externalizable_launch_spec(raw: Any) -> bool:
    return bool(executor_adapter_descriptor(raw).requires_externalizable_launch_spec)


def executor_is_slot_limited(raw: Any) -> bool:
    return bool(executor_adapter_descriptor(raw).slot_limited)


def executor_supports_test_only_harness(raw: Any) -> bool:
    return bool(executor_adapter_descriptor(raw).supports_test_only_harness)


def executor_supports_pickup_ack(raw: Any) -> bool:
    return bool(executor_adapter_descriptor(raw).supports_pickup_ack)


def executor_operator_selected_only(raw: Any) -> bool:
    return bool(executor_adapter_descriptor(raw).operator_selected_only)


def executor_capability_snapshot(raw: Any, default: str = "") -> Dict[str, Any]:
    descriptor = executor_adapter_descriptor(raw, default)
    return asdict(descriptor)
