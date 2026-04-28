import sys
import json
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
from aoe_tg_background_runs import (
    load_background_runs_state,
    upsert_background_run_ticket,
)
from aoe_tg_request_contract import (
    build_background_run_ticket,
    build_local_background_provider_invoke_launch_spec,
)
from aoe_tg_tmux_background_worker import local_tmux_log_path, local_tmux_result_path


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


def test_executor_runtime_dispatches_provider_invoke_local_background_ticket(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "background_runs.json"
    updates = []
    errors = []

    monkeypatch.setattr(
        executor_runtime,
        "advance_background_run_ticket",
        lambda queue_path, ticket_id, now_iso, **kwargs: {"ticket_id": ticket_id, **kwargs},
    )
    monkeypatch.setattr(
        executor_runtime.model_endpoint_adapter,
        "probe_background_ticket_worker_binding",
        lambda team_dir, ticket: {
            "ok": True,
            "probe_status": "ok",
            "summary": "endpoint=ollama-qwen3 status=ok",
            "binding": {"bound": True, "summary": "bg=ollama-qwen3:qwen3-coder:30b"},
        },
    )
    monkeypatch.setattr(
        executor_runtime.model_provider_adapter,
        "invoke_background_ticket_worker",
        lambda team_dir, *, ticket: {
            "ok": True,
            "executed": True,
            "route_id": "background_worker_primary",
            "endpoint_id": "ollama-qwen3",
            "model": "qwen3-coder:30b",
            "response_text": "QUEUE_OK",
            "task_result_status": "ready",
            "task_result_summary": "status=ready | queue summary drafted | actions=1 | refs=1",
            "task_result_actions": ["update reports/summary.md"],
            "task_result_cautions": ["keep review lane open"],
            "task_result_evidence_refs": ["reports/summary.md"],
            "task_update_stub_status": "ready",
            "task_update_stub_summary": "status=ready | targets=reports/summary.md | actions=1 | refs=1",
            "task_update_stub_targets": ["reports/summary.md"],
        },
    )

    launch_spec = build_local_background_provider_invoke_launch_spec(
        request_id="REQ-PROVIDER-1",
        project_key="alpha",
        project_root=str(tmp_path),
        team_dir=str(tmp_path),
        prompt="Reply with QUEUE_OK only.",
    )
    result = executor_runtime.dispatch_claimed_background_ticket_via_adapter(
        queue_path=queue_path,
        claimed_ticket={"ticket_id": "BGT-LB-2", "runner_target": "local_background", "launch_spec": launch_spec},
        now_iso=lambda: "2026-04-08T00:00:00+09:00",
        run_target=lambda: (_ for _ in ()).throw(AssertionError("provider invoke path should bypass callback target")),
        on_ticket_update=updates.append,
        on_queue_error=lambda name, exc: errors.append((name, str(exc))),
    )

    assert result["ok"] is True
    assert len(updates) == 2
    assert updates[0]["status"] == "running"
    assert updates[0]["runtime_summary"].startswith("provider_invoke_started")
    assert updates[1]["status"] == "completed"
    assert "provider_invoke_ok" in updates[1]["evidence_bundle"]
    assert updates[1]["worker_result_status"] == "ready"
    assert updates[1]["worker_result_summary"] == "status=ready | queue summary drafted | actions=1 | refs=1"
    assert updates[1]["worker_result_evidence_refs"] == ["reports/summary.md"]
    assert updates[1]["worker_update_stub_status"] == "ready"
    assert updates[1]["worker_update_stub_summary"] == "status=ready | targets=reports/summary.md | actions=1 | refs=1"
    assert updates[1]["worker_update_stub_targets"] == ["reports/summary.md"]
    assert updates[1]["evidence_artifacts"] == ["reports/summary.md"]
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


def test_executor_runtime_adapter_poll_persists_local_tmux_result_status(tmp_path: Path) -> None:
    queue_path = tmp_path / "background_runs.json"
    ticket_id = "BGT-TMUX-ADAPTER-001"
    upsert_background_run_ticket(
        queue_path,
        build_background_run_ticket(
            ticket_id=ticket_id,
            request_id="REQ-TMUX-ADAPTER-001",
            project_key="alpha",
            execution_brief_status="partially_executable",
            runner_target="local_tmux",
            launch_mode="dashboard_followup_execute",
            created_at="2026-04-28T12:30:09+09:00",
            created_by="dashboard:dashboard-http",
            source_surface="dashboard_followup_execute",
            status="running",
            runtime_handle="aoe_bg_bgt_tmux_adapter_001",
            runtime_summary="tmux_session=aoe_bg_bgt_tmux_adapter_001",
            evidence_bundle="status=running | outcome=tmux_session_started",
        ),
        now_iso=lambda: "2026-04-28T12:30:10+09:00",
    )
    result_path = local_tmux_result_path(tmp_path, ticket_id)
    log_path = local_tmux_log_path(tmp_path, ticket_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({"ticket_id": ticket_id, "exit_code": 0}) + "\n", encoding="utf-8")
    log_path.write_text("tmux run completed\n", encoding="utf-8")

    result = executor_runtime.poll_background_tickets_via_adapters(
        queue_path=queue_path,
        now_iso=lambda: "2026-04-28T12:38:03+09:00",
    )

    assert result["changed"] is True
    assert result["completed_count"] == 1
    assert result["local_tmux"]["completed_ticket_ids"] == [ticket_id]
    row = (load_background_runs_state(queue_path).get("runs") or [])[0]
    assert row["status"] == "completed"
    assert row["touched_at"] == "2026-04-28T12:38:03+09:00"
    assert row["evidence_bundle"] == (
        "status=completed | outcome=tmux_exit_code | exit_code=0 | "
        "log=background_run_logs/bgt-tmux-adapter-001.log"
    )
    assert "background_run_results/bgt-tmux-adapter-001.json" in row["evidence_artifacts"]
    assert "background_run_logs/bgt-tmux-adapter-001.log" in row["evidence_artifacts"]
