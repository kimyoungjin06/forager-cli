import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

from aoe_tg_executor_adapter import (
    EXECUTOR_EXTERNAL_RUNNER_TARGETS,
    EXECUTOR_RUNNER_TARGETS,
    EXECUTOR_SLOT_RUNNER_TARGETS,
    executor_adapter_descriptor,
    executor_capability_snapshot,
    executor_requires_externalizable_launch_spec,
    executor_supports_pickup_ack,
    executor_supports_test_only_harness,
    normalize_executor_runner_target,
)


def test_executor_adapter_inventory_is_stable() -> None:
    assert EXECUTOR_RUNNER_TARGETS == (
        "local_background",
        "local_tmux",
        "github_runner",
        "remote_worker",
    )
    assert EXECUTOR_SLOT_RUNNER_TARGETS == (
        "local_tmux",
        "github_runner",
        "remote_worker",
    )
    assert EXECUTOR_EXTERNAL_RUNNER_TARGETS == EXECUTOR_SLOT_RUNNER_TARGETS


def test_executor_adapter_capabilities_cover_local_and_external_rails() -> None:
    local_background = executor_adapter_descriptor("local_background")
    local_tmux = executor_adapter_descriptor("local_tmux")
    github_runner = executor_adapter_descriptor("github_runner")

    assert local_background.supports_in_process_callback is True
    assert local_background.requires_externalizable_launch_spec is False
    assert local_background.slot_limited is False

    assert local_tmux.supports_serializable_gateway_command is True
    assert local_tmux.requires_externalizable_launch_spec is True
    assert local_tmux.supports_pickup_ack is False

    assert github_runner.requires_externalizable_launch_spec is True
    assert github_runner.supports_pickup_ack is True
    assert github_runner.supports_test_only_harness is True
    assert github_runner.operator_selected_only is True


def test_executor_adapter_helpers_expose_canonical_truth() -> None:
    assert normalize_executor_runner_target(" LOCAL_TMUX ") == "local_tmux"
    assert normalize_executor_runner_target("unknown", default="github_runner") == "github_runner"
    assert executor_requires_externalizable_launch_spec("remote_worker") is True
    assert executor_supports_pickup_ack("remote_worker") is True
    assert executor_supports_test_only_harness("local_tmux") is False

    snapshot = executor_capability_snapshot("github_runner")
    assert snapshot["runner_target"] == "github_runner"
    assert snapshot["adapter_kind"] == "external_handoff"
