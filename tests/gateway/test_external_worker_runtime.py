import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

from aoe_tg_background_runs import background_runs_state_path, load_background_runs_state, upsert_background_run_ticket
from aoe_tg_external_background_worker import (
    emit_external_background_handoff,
    external_background_ack_path,
    external_background_result_path,
    poll_external_background_tickets,
)
from aoe_tg_external_worker_runtime import external_background_log_path, run_external_background_worker_once
from aoe_tg_request_contract import build_background_run_ticket, build_runner_background_launch_spec


def _fixed_now() -> str:
    return "2026-04-08T10:00:00+0900"


def _queue_external_ticket(
    *,
    team_dir: Path,
    ticket_id: str,
    runner_target: str,
    command_argv: list[str],
    command_cwd: Path,
) -> Path:
    queue_path = background_runs_state_path(team_dir)
    ticket = build_background_run_ticket(
        ticket_id=ticket_id,
        request_id=f"REQ-{ticket_id}",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target=runner_target,
        launch_mode="dashboard_retry",
        created_at="2026-04-08T09:59:00+0900",
        created_by="pytest",
        source_surface="test_external_worker_runtime",
        status="queued",
        launch_spec=build_runner_background_launch_spec(
            runner_target=runner_target,
            request_id=f"REQ-{ticket_id}",
            project_key="twinpaper",
            project_root=str(command_cwd),
            team_dir=str(team_dir),
            manager_state_file=str(team_dir / "orch_manager_state.json"),
            launch_mode="dashboard_retry",
            source_surface="test_external_worker_runtime",
            created_by="pytest",
            command_argv=command_argv,
            command_cwd=str(command_cwd),
        ),
    )
    upsert_background_run_ticket(queue_path, ticket, now_iso=_fixed_now)
    launched = emit_external_background_handoff(
        queue_path=queue_path,
        ticket_id=ticket_id,
        runner_target=runner_target,
        now_iso=_fixed_now,
        claimed_by="pytest",
        source_surface="test_external_worker_runtime",
        launch_mode="dashboard_retry",
    )
    assert launched["status"] == "running"
    return queue_path


def test_external_worker_run_acknowledges_handoff_and_writes_completed_result(tmp_path: Path) -> None:
    queue_path = _queue_external_ticket(
        team_dir=tmp_path,
        ticket_id="BGT-GHA-RUN-001",
        runner_target="github_runner",
        command_argv=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('worker-output.txt').write_text('ok', encoding='utf-8')",
        ],
        command_cwd=tmp_path,
    )

    result = run_external_background_worker_once(
        team_dir=tmp_path,
        runner_target="github_runner",
        ticket_id="BGT-GHA-RUN-001",
        worker_id="pytest-gha-worker",
        timeout_sec=30,
        now_iso=_fixed_now,
    )

    assert result["processed"] is True
    assert result["status"] == "completed"
    assert result["exit_code"] == 0
    assert result["ack_artifact"] == "background_run_acks/github-runner-bgt-gha-run-001.json"
    assert result["result_artifact"] == "background_run_results/github-runner-bgt-gha-run-001.json"
    assert result["log_artifact"] == "background_run_logs/github-runner-bgt-gha-run-001.log"
    assert (tmp_path / "worker-output.txt").read_text(encoding="utf-8") == "ok"

    ack_payload = json.loads(
        external_background_ack_path(tmp_path, "BGT-GHA-RUN-001", "github_runner").read_text(encoding="utf-8")
    )
    assert ack_payload["worker_id"] == "pytest-gha-worker"
    assert ack_payload["summary"] == "external worker accepted handoff"

    result_payload = json.loads(
        external_background_result_path(tmp_path, "BGT-GHA-RUN-001", "github_runner").read_text(encoding="utf-8")
    )
    assert result_payload["status"] == "completed"
    assert "exit_code=0" in result_payload["evidence_bundle"]
    assert result_payload["evidence_artifacts"] == ["background_run_logs/github-runner-bgt-gha-run-001.log"]

    polled = poll_external_background_tickets(queue_path=queue_path, now_iso=_fixed_now)
    assert polled["completed_count"] == 1
    row = load_background_runs_state(queue_path)["runs"][0]
    assert row["status"] == "completed"
    assert "background_run_acks/github-runner-bgt-gha-run-001.json" in (row.get("evidence_artifacts") or [])
    assert "background_run_results/github-runner-bgt-gha-run-001.json" in (row.get("evidence_artifacts") or [])
    assert "background_run_logs/github-runner-bgt-gha-run-001.log" in (row.get("evidence_artifacts") or [])


def test_external_worker_run_writes_failed_result_for_nonzero_exit(tmp_path: Path) -> None:
    _queue_external_ticket(
        team_dir=tmp_path,
        ticket_id="BGT-REMOTE-RUN-001",
        runner_target="remote_worker",
        command_argv=[sys.executable, "-c", "import sys; print('bad exit'); sys.exit(7)"],
        command_cwd=tmp_path,
    )

    result = run_external_background_worker_once(
        team_dir=tmp_path,
        runner_target="remote_worker",
        ticket_id="BGT-REMOTE-RUN-001",
        timeout_sec=30,
        now_iso=_fixed_now,
    )

    assert result["processed"] is True
    assert result["status"] == "failed"
    assert result["exit_code"] == 7
    assert result["reason"] == "exit_code_7"
    result_payload = json.loads(
        external_background_result_path(tmp_path, "BGT-REMOTE-RUN-001", "remote_worker").read_text(encoding="utf-8")
    )
    assert result_payload["status"] == "failed"
    assert result_payload["reason"] == "exit_code_7"
    log_text = external_background_log_path(tmp_path, "BGT-REMOTE-RUN-001", "remote_worker").read_text(
        encoding="utf-8"
    )
    assert "bad exit" in log_text


def test_external_worker_cli_processes_one_remote_handoff(tmp_path: Path) -> None:
    _queue_external_ticket(
        team_dir=tmp_path,
        ticket_id="BGT-REMOTE-CLI-001",
        runner_target="remote_worker",
        command_argv=[sys.executable, "-c", "print('cli ok')"],
        command_cwd=tmp_path,
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-background-worker.py"),
            "worker-run",
            "--runner",
            "remote_worker",
            "--team-dir",
            str(tmp_path),
            "--ticket-id",
            "BGT-REMOTE-CLI-001",
            "--timeout-sec",
            "30",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["processed_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["results"][0]["status"] == "completed"
    assert external_background_result_path(tmp_path, "BGT-REMOTE-CLI-001", "remote_worker").exists()
