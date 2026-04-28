import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

from aoe_tg_background_runs import background_runs_state_path, upsert_background_run_ticket
from aoe_tg_external_background_worker import emit_external_background_handoff, external_background_result_path
from aoe_tg_external_worker_runtime import run_external_background_worker_once
from aoe_tg_github_runner_bridge import (
    build_github_runner_completion_comment,
    build_github_runner_comment_flow_verification_plan,
    build_github_runner_comment_dispatch,
    build_github_runner_transport_policy,
    build_github_runner_worker_bundle,
    decode_github_runner_worker_bundle,
    encode_github_runner_worker_bundle,
    materialize_github_runner_worker_bundle,
)
from aoe_tg_request_contract import build_background_run_ticket, build_runner_background_launch_spec


def _fixed_now() -> str:
    return "2026-04-28T10:00:00+0900"


def _seed_github_handoff(team_dir: Path, *, ticket_id: str = "BGT-GHA-BRIDGE-001") -> Path:
    queue_path = background_runs_state_path(team_dir)
    ticket = build_background_run_ticket(
        ticket_id=ticket_id,
        request_id=f"REQ-{ticket_id}",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target="github_runner",
        launch_mode="dashboard_retry",
        created_at="2026-04-28T09:59:00+0900",
        created_by="pytest",
        source_surface="test_github_runner_bridge",
        status="queued",
        launch_spec=build_runner_background_launch_spec(
            runner_target="github_runner",
            request_id=f"REQ-{ticket_id}",
            project_key="twinpaper",
            project_root=str(team_dir.parent),
            team_dir=str(team_dir),
            manager_state_file=str(team_dir / "orch_manager_state.json"),
            launch_mode="dashboard_retry",
            source_surface="test_github_runner_bridge",
            created_by="pytest",
            command_argv=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path('bridge-output.txt').write_text('ok', encoding='utf-8')",
            ],
            command_cwd=str(team_dir.parent),
        ),
    )
    upsert_background_run_ticket(queue_path, ticket, now_iso=_fixed_now)
    launched = emit_external_background_handoff(
        queue_path=queue_path,
        ticket_id=ticket_id,
        runner_target="github_runner",
        now_iso=_fixed_now,
        claimed_by="pytest",
        source_surface="test_github_runner_bridge",
        launch_mode="dashboard_retry",
    )
    assert launched["status"] == "running"
    return queue_path


def _issue_comment_event(body: str, *, association: str = "OWNER", is_pr: bool = True) -> dict:
    issue = {"number": 104}
    if is_pr:
        issue["pull_request"] = {"url": "https://api.github.test/repos/acme/repo/pulls/104"}
    return {
        "comment": {
            "body": body,
            "author_association": association,
        },
        "issue": issue,
        "repository": {
            "full_name": "acme/repo",
            "default_branch": "main",
        },
    }


def test_github_runner_bridge_bundle_round_trips_and_worker_run_consumes_materialized_handoff(tmp_path: Path) -> None:
    source_team_dir = tmp_path / "source" / ".aoe-team"
    source_team_dir.mkdir(parents=True)
    _seed_github_handoff(source_team_dir)

    bundle = build_github_runner_worker_bundle(
        team_dir=source_team_dir,
        ticket_id="BGT-GHA-BRIDGE-001",
    )
    assert bundle["runner_target"] == "github_runner"
    assert bundle["background_runs"]["runs"][0]["status"] == "running"
    assert bundle["handoff"]["ticket_id"] == "BGT-GHA-BRIDGE-001"

    encoded = encode_github_runner_worker_bundle(bundle)
    decoded = decode_github_runner_worker_bundle(encoded)
    checkout_root = tmp_path / "checkout"
    materialized = materialize_github_runner_worker_bundle(
        bundle=decoded,
        output_root=checkout_root,
        team_dir=".aoe-team",
    )

    worker_team_dir = checkout_root / ".aoe-team"
    assert materialized["background_runs_path"] == "background_runs.json"
    assert materialized["handoff_path"] == "background_run_handoffs/github-runner-bgt-gha-bridge-001.json"
    assert (worker_team_dir / "background_runs.json").exists()
    handoff_path = worker_team_dir / "background_run_handoffs" / "github-runner-bgt-gha-bridge-001.json"
    assert handoff_path.exists()
    handoff_payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert handoff_payload["launch_spec"]["command_cwd"] == str(checkout_root.resolve())
    assert handoff_payload["launch_spec"]["project_root"] == str(checkout_root.resolve())
    assert handoff_payload["launch_spec"]["team_dir"] == str(worker_team_dir.resolve())

    result = run_external_background_worker_once(
        team_dir=worker_team_dir,
        runner_target="github_runner",
        ticket_id="BGT-GHA-BRIDGE-001",
        worker_id="pytest-github-action",
        timeout_sec=30,
        now_iso=_fixed_now,
    )
    assert result["status"] == "completed"
    assert (checkout_root / "bridge-output.txt").read_text(encoding="utf-8") == "ok"
    assert external_background_result_path(worker_team_dir, "BGT-GHA-BRIDGE-001", "github_runner").exists()


def test_github_runner_bridge_cli_exports_and_materializes_bundle(tmp_path: Path) -> None:
    source_team_dir = tmp_path / "source" / ".aoe-team"
    source_team_dir.mkdir(parents=True)
    _seed_github_handoff(source_team_dir, ticket_id="BGT-GHA-CLI-001")

    export_proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "export-bundle",
            "--team-dir",
            str(source_team_dir),
            "--ticket-id",
            "BGT-GHA-CLI-001",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert export_proc.returncode == 0
    assert decode_github_runner_worker_bundle(export_proc.stdout.strip())["ticket_id"] == "BGT-GHA-CLI-001"

    checkout_root = tmp_path / "checkout-cli"
    materialize_proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "materialize-bundle",
            "--bundle-b64",
            export_proc.stdout.strip(),
            "--output-root",
            str(checkout_root),
            "--team-dir",
            ".aoe-team",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert materialize_proc.returncode == 0
    payload = json.loads(materialize_proc.stdout)
    assert payload["ticket_id"] == "BGT-GHA-CLI-001"
    assert (checkout_root / ".aoe-team" / "background_runs.json").exists()


def test_github_runner_transport_policy_defaults_to_artifact_only() -> None:
    policy = build_github_runner_transport_policy(
        runner_target="github_runner",
        team_dir=".aoe-team",
        event_name="workflow_dispatch",
        commit_results=False,
        bundle_present=True,
        timeout_sec="900",
        max_items="1",
    )

    assert policy["ok"] is True
    assert policy["result_transport"] == "actions_artifact"
    assert policy["credential_scope"] == "contents:read"
    assert policy["violations"] == []


def test_github_runner_transport_policy_commit_results_declares_write_mode() -> None:
    policy = build_github_runner_transport_policy(
        runner_target="github_runner",
        team_dir=".aoe-team",
        event_name="repository_dispatch",
        commit_results="true",
        bundle_present="true",
        timeout_sec=900,
        max_items=1,
    )

    assert policy["ok"] is True
    assert policy["result_transport"] == "actions_artifact+optional_git_commit"
    assert policy["credential_scope"] == "contents:write"
    assert [item["code"] for item in policy["warnings"]] == ["commit_results_write_mode"]


def test_github_runner_transport_policy_rejects_unsafe_team_dir() -> None:
    for team_dir in ("/tmp/aoe-team", "../.aoe-team"):
        policy = build_github_runner_transport_policy(
            runner_target="github_runner",
            team_dir=team_dir,
            event_name="workflow_dispatch",
            bundle_present=True,
        )
        assert policy["ok"] is False
        assert "unsafe_team_dir" in {item["code"] for item in policy["violations"]}


def test_github_runner_bridge_cli_policy_check_reports_policy_json() -> None:
    ok_proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "policy-check",
            "--runner",
            "github_runner",
            "--team-dir",
            ".aoe-team",
            "--event-name",
            "workflow_dispatch",
            "--bundle-present",
            "true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ok_proc.returncode == 0
    assert json.loads(ok_proc.stdout)["ok"] is True

    blocked_proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "policy-check",
            "--runner",
            "remote_worker",
            "--team-dir",
            ".aoe-team",
            "--bundle-present",
            "true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert blocked_proc.returncode == 1
    blocked = json.loads(blocked_proc.stdout)
    assert blocked["ok"] is False
    assert blocked["violations"][0]["code"] == "unsupported_runner_target"


def test_github_runner_comment_dispatch_accepts_trusted_bgx_run_command() -> None:
    result = build_github_runner_comment_dispatch(
        _issue_comment_event("/aoe bgx run BGT-GHA-COMMENT-001 --timeout-sec 120 --max-items=2")
    )

    assert result["ok"] is True
    assert result["action"] == "dispatch_external_worker"
    assert result["workflow"] == "external-background-worker.yml"
    assert result["workflow_inputs"] == {
        "runner_target": "github_runner",
        "team_dir": ".aoe-team",
        "ticket_id": "BGT-GHA-COMMENT-001",
        "timeout_sec": "120",
        "max_items": "2",
        "commit_results": "false",
        "comment_issue_number": "104",
    }
    assert "run link and artifact import command" in result["response_markdown"]


def test_github_runner_comment_dispatch_ignores_non_command_comments() -> None:
    result = build_github_runner_comment_dispatch(_issue_comment_event("Looks good to me."))

    assert result["ok"] is False
    assert result["command_seen"] is False
    assert result["should_comment"] is False
    assert result["reason"] == "no_aoe_command"


def test_github_runner_comment_dispatch_blocks_untrusted_author() -> None:
    result = build_github_runner_comment_dispatch(
        _issue_comment_event("/aoe bgx run BGT-GHA-COMMENT-002", association="CONTRIBUTOR")
    )

    assert result["ok"] is False
    assert result["command_seen"] is True
    assert result["should_comment"] is True
    assert result["reason"] == "unauthorized_author_association"


def test_github_runner_comment_dispatch_rejects_write_or_bundle_modes() -> None:
    for body, reason in (
        ("/aoe bgx run BGT-GHA-COMMENT-003 --commit-results", "commit_results_not_allowed"),
        ("/aoe bgx run BGT-GHA-COMMENT-003 --bundle-b64 abc", "bundle_not_allowed"),
    ):
        result = build_github_runner_comment_dispatch(_issue_comment_event(body))
        assert result["ok"] is False
        assert result["reason"] == reason


def test_github_runner_comment_dispatch_rejects_unsafe_team_dir_for_outputs() -> None:
    result = build_github_runner_comment_dispatch(
        _issue_comment_event('/aoe bgx run BGT-GHA-COMMENT-004 --team-dir ".aoe-team; echo bad"')
    )

    assert result["ok"] is False
    assert result["reason"] == "invalid_team_dir"


def test_github_runner_bridge_cli_comment_dispatch_writes_github_outputs(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output.txt"
    response_path = tmp_path / "response.md"
    event_path.write_text(
        json.dumps(_issue_comment_event("/aoe bgx run BGT-GHA-CLI-COMMENT-001 --team-dir .aoe-team")),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "comment-dispatch",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
            "--response-file",
            str(response_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    output = output_path.read_text(encoding="utf-8")
    assert "ok=true" in output
    assert "ticket_id=BGT-GHA-CLI-COMMENT-001" in output
    assert "commit_results=false" in output
    assert "comment_issue_number=104" in output
    assert "dispatch accepted" in response_path.read_text(encoding="utf-8")


def test_github_runner_completion_comment_builds_import_instruction() -> None:
    result = build_github_runner_completion_comment(
        ticket_id="BGT-GHA-COMMENT-001",
        runner_target="github_runner",
        team_dir=".aoe-team",
        run_id="25046272933",
        run_url="https://github.com/acme/repo/actions/runs/25046272933",
        repo="acme/repo",
        artifact_name="aoe-external-background-github_runner-BGT-GHA-COMMENT-001",
        worker_result="success",
        comment_issue_number="104",
    )

    assert result["ok"] is True
    assert result["should_comment"] is True
    assert result["issue_number"] == 104
    body = result["response_markdown"]
    assert "AOE external runner workflow finished." in body
    assert "worker_result: `success`" in body
    assert "--run-id 25046272933" in body
    assert "--ticket-id BGT-GHA-COMMENT-001" in body
    assert "download-github-artifact" in body
    assert "auto-import-github-artifact" in body
    assert "--repo acme/repo" in body
    assert result["import_commands"]["auto_import_github_artifact"].endswith("--repo acme/repo --poll")


def test_github_runner_completion_comment_rejects_missing_issue_number() -> None:
    result = build_github_runner_completion_comment(
        ticket_id="BGT-GHA-COMMENT-001",
        runner_target="github_runner",
        team_dir=".aoe-team",
        run_id="25046272933",
        run_url="https://github.com/acme/repo/actions/runs/25046272933",
        worker_result="success",
        comment_issue_number="",
    )

    assert result["ok"] is False
    assert result["should_comment"] is False
    assert result["violations"][0]["code"] == "comment_issue_number_required"


def test_github_runner_bridge_cli_completion_comment_writes_response(tmp_path: Path) -> None:
    response_path = tmp_path / "completion.md"
    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "completion-comment",
            "--ticket-id",
            "BGT-GHA-CLI-COMPLETION-001",
            "--runner",
            "github_runner",
            "--team-dir",
            ".aoe-team",
            "--run-id",
            "25046272933",
            "--run-url",
            "https://github.com/acme/repo/actions/runs/25046272933",
            "--repo",
            "acme/repo",
            "--worker-result",
            "success",
            "--comment-issue-number",
            "104",
            "--response-file",
            str(response_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    body = response_path.read_text(encoding="utf-8")
    assert "BGT-GHA-CLI-COMPLETION-001" in body
    assert "download-github-artifact" in body
    assert "auto-import-github-artifact" in body
    assert "--repo acme/repo" in body


def test_github_runner_comment_flow_plan_builds_live_verification_commands() -> None:
    result = build_github_runner_comment_flow_verification_plan(
        ticket_id="BGT-GHA-COMMENT-VERIFY-001",
        issue_number="104",
        repo="acme/repo",
        team_dir=".aoe-team",
        timeout_sec="120",
        max_items="2",
        import_timeout_sec="30",
        import_interval_sec="0",
    )

    assert result["ok"] is True
    assert result["comment_body"] == (
        "/aoe bgx run BGT-GHA-COMMENT-VERIFY-001 --team-dir .aoe-team --timeout-sec 120 --max-items 2"
    )
    assert result["workflow_inputs"] == {
        "runner_target": "github_runner",
        "team_dir": ".aoe-team",
        "ticket_id": "BGT-GHA-COMMENT-VERIFY-001",
        "timeout_sec": "120",
        "max_items": "2",
        "commit_results": "false",
        "comment_issue_number": "104",
    }
    assert result["expected_run_name"] == "external-background-github_runner-BGT-GHA-COMMENT-VERIFY-001"
    assert result["expected_artifact_name"] == "aoe-external-background-github_runner-BGT-GHA-COMMENT-VERIFY-001"
    assert result["dispatch_preview"]["ok"] is True
    assert result["dispatch_preview"]["workflow_inputs"] == result["workflow_inputs"]
    commands = result["commands"]
    assert commands["post_comment"].startswith("gh issue comment 104 --repo acme/repo --body ")
    assert "/aoe bgx run BGT-GHA-COMMENT-VERIFY-001" in commands["post_comment"]
    assert "gh run list --repo acme/repo --workflow external-background-worker.yml" in commands["discover_runs"]
    assert "auto-import-github-artifact" in commands["auto_import"]
    assert "--repo acme/repo" in commands["auto_import"]
    assert "--timeout-sec 30 --interval-sec 0 --poll" in commands["auto_import"]
    assert "drain-github-imports" in commands["drain_scheduled_imports"]


def test_github_runner_comment_flow_plan_rejects_missing_live_targets() -> None:
    result = build_github_runner_comment_flow_verification_plan(
        ticket_id="BGT-GHA-COMMENT-VERIFY-002",
        issue_number="",
        repo="not a repo",
    )

    assert result["ok"] is False
    assert result["commands"] == {}
    assert {item["code"] for item in result["violations"]} >= {
        "comment_issue_number_required",
        "repo_required",
    }


def test_github_runner_bridge_cli_comment_flow_plan_reports_commands() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(GW_DIR / "aoe-github-runner-bridge.py"),
            "comment-flow-plan",
            "--ticket-id",
            "BGT-GHA-CLI-FLOW-001",
            "--issue-number",
            "104",
            "--repo",
            "acme/repo",
            "--import-timeout-sec",
            "30",
            "--import-interval-sec",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert payload["commands"]["post_comment"].startswith("gh issue comment 104")
    assert "auto-import-github-artifact" in payload["commands"]["auto_import"]


def test_external_background_worker_workflow_contract_is_stable() -> None:
    workflow = (ROOT / ".github" / "workflows" / "external-background-worker.yml").read_text(encoding="utf-8")
    comment_workflow = (ROOT / ".github" / "workflows" / "external-background-comment.yml").read_text(encoding="utf-8")
    gateway_tests = (ROOT / ".github" / "workflows" / "gateway-tests.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "repository_dispatch:" in workflow
    assert "run-name:" in workflow
    assert "external-background-" in workflow
    assert "aoe-external-background-worker" in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "commit-result-sidecars:" in workflow
    assert "contents: write" in workflow
    assert "aoe-github-runner-bridge.py policy-check" in workflow
    assert "aoe-github-runner-bridge.py materialize-bundle" in workflow
    assert "aoe-background-worker.py worker-run" in workflow
    assert "actions/checkout@v6" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "actions/download-artifact@v8" in workflow
    assert "actions/checkout@v6" in comment_workflow
    assert "actions/setup-python@v6" in comment_workflow
    assert "actions/checkout@v6" in gateway_tests
    assert "actions/setup-python@v6" in gateway_tests
    all_workflows = "\n".join((workflow, comment_workflow, gateway_tests))
    for legacy_action_ref in (
        "actions/checkout@v4",
        "actions/setup-python@v5",
        "actions/upload-artifact@v4",
        "actions/download-artifact@v4",
    ):
        assert legacy_action_ref not in all_workflows
    assert "github.event.client_payload.commit_results == 'true'" in workflow
    assert "comment_issue_number:" in workflow
    assert "comment-worker-result:" in workflow
    assert "needs.worker-run.result" in workflow
    assert "aoe-github-runner-bridge.py completion-comment" in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert "pull-requests: write" in workflow
    assert "continue-on-error: true" in workflow
    assert "$GITHUB_STEP_SUMMARY" in workflow
    assert "background_run_acks" in workflow
    assert "background_run_results" in workflow
    assert "background_run_logs" in workflow
    assert "issue_comment:" in comment_workflow
    assert "actions: write" in comment_workflow
    assert "issues: write" in comment_workflow
    assert "pull-requests: write" in comment_workflow
    assert "continue-on-error: true" in comment_workflow
    assert "$GITHUB_STEP_SUMMARY" in comment_workflow
    assert "aoe-github-runner-bridge.py comment-dispatch" in comment_workflow
    assert "gh workflow run" in comment_workflow
    assert "external-background-worker.yml" in comment_workflow
    assert "comment_issue_number" in comment_workflow
    assert "gh issue comment" in comment_workflow
    assert ".github/workflows/*.yml" in gateway_tests
