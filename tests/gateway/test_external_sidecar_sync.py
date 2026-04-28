import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

from aoe_tg_background_runs import background_runs_state_path, load_background_runs_state, upsert_background_run_ticket
from aoe_tg_external_background_worker import (
    emit_external_background_ack,
    emit_external_background_handoff,
    emit_external_background_result,
    external_background_ack_path,
)
from aoe_tg_external_sidecar_sync import (
    default_github_actions_artifact_name,
    download_and_import_github_external_sidecars,
    import_external_background_sidecars,
)
from aoe_tg_external_worker_runtime import external_background_log_path
from aoe_tg_request_contract import build_background_run_ticket, build_runner_background_launch_spec


def _fixed_now() -> str:
    return "2026-04-28T11:00:00+0900"


def _seed_local_running_ticket(
    *,
    team_dir: Path,
    ticket_id: str,
    runner_target: str,
) -> Path:
    queue_path = background_runs_state_path(team_dir)
    ticket = build_background_run_ticket(
        ticket_id=ticket_id,
        request_id=f"REQ-{ticket_id}",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target=runner_target,
        launch_mode="dashboard_retry",
        created_at="2026-04-28T10:59:00+0900",
        created_by="pytest",
        source_surface="test_external_sidecar_sync",
        status="queued",
        launch_spec=build_runner_background_launch_spec(
            runner_target=runner_target,
            request_id=f"REQ-{ticket_id}",
            project_key="twinpaper",
            project_root=str(team_dir.parent),
            team_dir=str(team_dir),
            manager_state_file=str(team_dir / "orch_manager_state.json"),
            launch_mode="dashboard_retry",
            source_surface="test_external_sidecar_sync",
            created_by="pytest",
            command_argv=[sys.executable, "-c", "print('sync ok')"],
            command_cwd=str(team_dir.parent),
        ),
    )
    upsert_background_run_ticket(queue_path, ticket, now_iso=_fixed_now)
    launched = emit_external_background_handoff(
        queue_path=queue_path,
        ticket_id=ticket_id,
        runner_target=runner_target,
        now_iso=_fixed_now,
        claimed_by="pytest",
        source_surface="test_external_sidecar_sync",
        launch_mode="dashboard_retry",
    )
    assert launched["status"] == "running"
    return queue_path


def _write_remote_sidecars(
    *,
    artifact_team_dir: Path,
    ticket_id: str,
    runner_target: str,
    status: str = "completed",
) -> None:
    queue_path = artifact_team_dir / "background_runs.json"
    emit_external_background_ack(
        queue_path=queue_path,
        ticket_id=ticket_id,
        runner_target=runner_target,
        now_iso=_fixed_now,
        worker_id="github-actions:pytest",
        summary="workflow picked up handoff",
    )
    log_path = external_background_log_path(artifact_team_dir, ticket_id, runner_target)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"ticket_id={ticket_id}\nrunner_target={runner_target}\n", encoding="utf-8")
    emit_external_background_result(
        queue_path=queue_path,
        ticket_id=ticket_id,
        runner_target=runner_target,
        now_iso=_fixed_now,
        status=status,
        reason="exit_code_0" if status == "completed" else "exit_code_9",
        summary=f"remote worker {status}",
        evidence_bundle=(
            f"status={status} | outcome=external_worker_exit_code | "
            f"exit_code={'0' if status == 'completed' else '9'} | "
            f"log=background_run_logs/{runner_target.replace('_', '-')}-{ticket_id.lower()}.log"
        ),
        evidence_artifacts=[f"background_run_logs/{runner_target.replace('_', '-')}-{ticket_id.lower()}.log"],
    )


def test_import_external_sidecars_from_actions_artifact_dir_and_poll(tmp_path: Path) -> None:
    team_dir = tmp_path / "local" / ".aoe-team"
    artifact_dir = tmp_path / "downloaded-artifact"
    ticket_id = "BGT-GHA-SYNC-001"
    queue_path = _seed_local_running_ticket(team_dir=team_dir, ticket_id=ticket_id, runner_target="github_runner")
    _write_remote_sidecars(artifact_team_dir=artifact_dir, ticket_id=ticket_id, runner_target="github_runner")

    result = import_external_background_sidecars(
        team_dir=team_dir,
        artifact_root=artifact_dir,
        ticket_id=ticket_id,
        runner_target="github_runner",
        poll_after_import=True,
        now_iso=_fixed_now,
    )

    assert result["ok"] is True
    assert result["copied_count"] == 3
    assert result["imported"]["ack_imported"] is True
    assert result["imported"]["result_status"] == "completed"
    assert result["poll_result"]["completed_count"] == 1
    row = load_background_runs_state(queue_path)["runs"][0]
    assert row["status"] == "completed"
    assert "background_run_acks/github-runner-bgt-gha-sync-001.json" in (row.get("evidence_artifacts") or [])
    assert "background_run_results/github-runner-bgt-gha-sync-001.json" in (row.get("evidence_artifacts") or [])
    assert "background_run_logs/github-runner-bgt-gha-sync-001.log" in (row.get("evidence_artifacts") or [])


def test_external_sidecar_sync_cli_imports_zip_artifact(tmp_path: Path) -> None:
    team_dir = tmp_path / "local" / ".aoe-team"
    source_root = tmp_path / "artifact-root"
    artifact_team_dir = source_root / ".aoe-team"
    ticket_id = "BGT-REMOTE-SYNC-001"
    queue_path = _seed_local_running_ticket(team_dir=team_dir, ticket_id=ticket_id, runner_target="remote_worker")
    _write_remote_sidecars(
        artifact_team_dir=artifact_team_dir,
        ticket_id=ticket_id,
        runner_target="remote_worker",
        status="failed",
    )
    zip_path = tmp_path / "worker-sidecars.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in source_root.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(source_root))

    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-external-sidecar-sync.py"),
            "import-artifact",
            "--team-dir",
            str(team_dir),
            "--artifact-root",
            str(zip_path),
            "--ticket-id",
            ticket_id,
            "--runner",
            "remote_worker",
            "--poll",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["copied_count"] == 3
    assert payload["imported"]["result_status"] == "failed"
    row = load_background_runs_state(queue_path)["runs"][0]
    assert row["status"] == "failed"
    assert "exit_code=9" in row["evidence_bundle"]


def test_external_sidecar_sync_does_not_overwrite_existing_sidecar_by_default(tmp_path: Path) -> None:
    team_dir = tmp_path / "local" / ".aoe-team"
    artifact_dir = tmp_path / "downloaded-artifact"
    ticket_id = "BGT-GHA-NO-CLOBBER-001"
    _seed_local_running_ticket(team_dir=team_dir, ticket_id=ticket_id, runner_target="github_runner")
    existing_ack = external_background_ack_path(team_dir, ticket_id, "github_runner")
    existing_ack.parent.mkdir(parents=True, exist_ok=True)
    existing_ack.write_text(
        json.dumps(
            {
                "ticket_id": ticket_id,
                "status": "acknowledged",
                "worker_id": "existing",
                "summary": "keep me",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_remote_sidecars(artifact_team_dir=artifact_dir, ticket_id=ticket_id, runner_target="github_runner")

    result = import_external_background_sidecars(
        team_dir=team_dir,
        artifact_root=artifact_dir,
        ticket_id=ticket_id,
        runner_target="github_runner",
        poll_after_import=False,
        now_iso=_fixed_now,
    )

    assert result["ok"] is True
    ack_result = next(item for item in result["copy_results"] if item["kind"] == "ack")
    assert ack_result["status"] == "skipped_existing"
    assert json.loads(existing_ack.read_text(encoding="utf-8"))["summary"] == "keep me"


def test_download_and_import_github_external_sidecars_uses_gh_download_then_polls(tmp_path: Path) -> None:
    team_dir = tmp_path / "local" / ".aoe-team"
    artifact_dir = tmp_path / "downloaded-artifact"
    ticket_id = "BGT-GHA-GH-DL-001"
    queue_path = _seed_local_running_ticket(team_dir=team_dir, ticket_id=ticket_id, runner_target="github_runner")
    _write_remote_sidecars(artifact_team_dir=artifact_dir, ticket_id=ticket_id, runner_target="github_runner")
    seen: dict[str, list[str]] = {}

    def _fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        download_dir = Path(command[command.index("--dir") + 1])
        download_dir.mkdir(parents=True, exist_ok=True)
        for child in artifact_dir.iterdir():
            target = download_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        return subprocess.CompletedProcess(command, 0, stdout="downloaded\n", stderr="")

    result = download_and_import_github_external_sidecars(
        team_dir=team_dir,
        run_id="123456789",
        ticket_id=ticket_id,
        runner_target="github_runner",
        repo="kimyoungjin06/aoe_orch_control",
        poll_after_import=True,
        now_iso=_fixed_now,
        command_runner=_fake_runner,
    )

    assert result["ok"] is True
    assert result["github_download"]["artifact_name"] == default_github_actions_artifact_name(
        ticket_id=ticket_id,
        runner_target="github_runner",
    )
    assert seen["command"][:4] == ["gh", "run", "download", "123456789"]
    assert "--repo" in seen["command"]
    assert "kimyoungjin06/aoe_orch_control" in seen["command"]
    assert result["copied_count"] == 3
    assert result["poll_result"]["completed_count"] == 1
    assert load_background_runs_state(queue_path)["runs"][0]["status"] == "completed"


def test_external_sidecar_sync_cli_downloads_github_artifact_with_fake_gh(tmp_path: Path) -> None:
    team_dir = tmp_path / "local" / ".aoe-team"
    artifact_dir = tmp_path / "downloaded-artifact"
    ticket_id = "BGT-REMOTE-GH-DL-001"
    queue_path = _seed_local_running_ticket(team_dir=team_dir, ticket_id=ticket_id, runner_target="remote_worker")
    _write_remote_sidecars(artifact_team_dir=artifact_dir, ticket_id=ticket_id, runner_target="remote_worker")
    fake_gh = tmp_path / "fake-gh"
    fake_gh.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os, pathlib, shutil, sys",
                "args = sys.argv[1:]",
                "download_dir = pathlib.Path(args[args.index('--dir') + 1])",
                "download_dir.mkdir(parents=True, exist_ok=True)",
                "source = pathlib.Path(os.environ['AOE_FAKE_ARTIFACT_SOURCE'])",
                "for child in source.iterdir():",
                "    target = download_dir / child.name",
                "    if child.is_dir():",
                "        shutil.copytree(child, target)",
                "    else:",
                "        shutil.copy2(child, target)",
                "print('fake download')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)

    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-external-sidecar-sync.py"),
            "download-github-artifact",
            "--team-dir",
            str(team_dir),
            "--run-id",
            "987654321",
            "--ticket-id",
            ticket_id,
            "--runner",
            "remote_worker",
            "--gh-bin",
            str(fake_gh),
            "--poll",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "AOE_FAKE_ARTIFACT_SOURCE": str(artifact_dir)},
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["github_download"]["run_id"] == "987654321"
    assert payload["copied_count"] == 3
    assert payload["poll_result"]["completed_count"] == 1
    assert load_background_runs_state(queue_path)["runs"][0]["status"] == "completed"
