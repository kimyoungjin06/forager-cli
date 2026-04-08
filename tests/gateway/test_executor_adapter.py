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
import aoe_tg_executor_dispatch as executor_dispatch
import aoe_tg_executor_runtime as executor_runtime


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


def test_executor_dispatch_builds_runner_specific_gateway_specs(monkeypatch) -> None:
    seen = {}

    def _fake_tmux(**kwargs):
        seen["tmux"] = kwargs
        return {"runner_target": "local_tmux", "summary": "tmux"}

    def _fake_external(**kwargs):
        seen["external"] = kwargs
        return {"runner_target": kwargs["runner_target"], "summary": "external"}

    monkeypatch.setattr(executor_dispatch, "build_local_tmux_gateway_command_launch_spec", _fake_tmux)
    monkeypatch.setattr(executor_dispatch, "build_external_runner_gateway_command_launch_spec", _fake_external)

    tmux_spec = executor_dispatch.build_gateway_command_launch_spec_for_adapter(
        runner_target="local_tmux",
        request_id="REQ-1",
        project_key="alpha",
        command_text="/retry T-1 lane L1",
    )
    external_spec = executor_dispatch.build_gateway_command_launch_spec_for_adapter(
        runner_target="github_runner",
        request_id="REQ-2",
        project_key="alpha",
        command_text="/retry T-2 lane L1",
    )

    assert tmux_spec["runner_target"] == "local_tmux"
    assert external_spec["runner_target"] == "github_runner"
    assert seen["tmux"]["request_id"] == "REQ-1"
    assert seen["external"]["runner_target"] == "github_runner"


def test_executor_dispatch_routes_launch_to_runner_adapter(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    def _fake_tmux(**kwargs):
        seen["tmux"] = kwargs
        return {"status": "running", "runner_target": "local_tmux"}

    def _fake_external(**kwargs):
        seen["external"] = kwargs
        return {"status": "running", "runner_target": kwargs["runner_target"]}

    monkeypatch.setattr(executor_dispatch, "launch_local_tmux_background_ticket", _fake_tmux)
    monkeypatch.setattr(executor_dispatch, "emit_external_background_handoff", _fake_external)

    queue_path = tmp_path / "background_runs.json"
    tmux_row = executor_dispatch.launch_background_ticket_via_adapter(
        queue_path=queue_path,
        ticket_id="BGT-1",
        runner_target="local_tmux",
        now_iso=lambda: "2026-04-08T00:00:00+09:00",
    )
    external_row = executor_dispatch.launch_background_ticket_via_adapter(
        queue_path=queue_path,
        ticket_id="BGT-2",
        runner_target="remote_worker",
        now_iso=lambda: "2026-04-08T00:00:00+09:00",
    )

    assert tmux_row["runner_target"] == "local_tmux"
    assert external_row["runner_target"] == "remote_worker"
    assert seen["tmux"]["ticket_id"] == "BGT-1"
    assert seen["external"]["runner_target"] == "remote_worker"


def test_executor_runtime_dispatches_local_background_claimed_ticket(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "background_runs.json"
    updates = []
    errors = []

    monkeypatch.setattr(
        executor_runtime,
        "advance_background_run_ticket",
        lambda queue_path, ticket_id, now_iso, **kwargs: {"ticket_id": ticket_id, **kwargs},
    )

    result = executor_runtime.dispatch_claimed_background_ticket_via_adapter(
        queue_path=queue_path,
        claimed_ticket={"ticket_id": "BGT-LB-1", "runner_target": "local_background"},
        now_iso=lambda: "2026-04-08T00:00:00+09:00",
        run_target=lambda: "ok",
        on_ticket_update=updates.append,
        on_queue_error=lambda name, exc: errors.append((name, str(exc))),
        completed_evidence_bundle=lambda: "status=completed | outcome=test",
    )

    assert result == "ok"
    assert len(updates) == 2
    assert updates[0]["status"] == "running"
    assert updates[1]["status"] == "completed"
    assert not errors


def test_executor_runtime_polls_via_adapter_handlers(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "background_runs.json"
    monkeypatch.setattr(
        executor_runtime,
        "poll_local_tmux_background_tickets",
        lambda **kwargs: {"changed": True, "completed_count": 1, "failed_count": 0},
    )
    monkeypatch.setattr(
        executor_runtime,
        "poll_external_background_tickets",
        lambda **kwargs: {"changed": False, "completed_count": 0, "failed_count": 1, "acknowledged_count": 2},
    )

    result = executor_runtime.poll_background_tickets_via_adapters(
        queue_path=queue_path,
        now_iso=lambda: "2026-04-08T00:00:00+09:00",
    )

    assert result["changed"] is True
    assert result["completed_count"] == 1
    assert result["failed_count"] == 1
    assert result["acknowledged_count"] == 2
    assert result["local_background"]["changed"] is False
