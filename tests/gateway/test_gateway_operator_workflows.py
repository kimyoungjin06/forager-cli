#!/usr/bin/env python3
"""Gateway operator workflow regression tests."""

import json
import subprocess
import time

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_run_detached_flow as run_detached_flow

from aoe_tg_background_runs import (
    advance_background_run_ticket,
    background_worker_state_path,
    background_runs_state_path,
    claim_background_run_ticket,
    claim_next_background_run_ticket,
    list_background_run_tickets,
    load_background_worker_state,
    load_background_runs_state,
    mark_stale_background_run_tickets,
    summarize_background_worker_state,
    summarize_background_runs_state,
    update_background_worker_state,
    upsert_background_run_ticket,
)
from aoe_tg_local_background_worker import (
    drain_local_background_queue,
    drain_local_background_queue_once,
    ensure_local_background_daemon,
    register_local_background_run,
    run_local_background_ticket,
    stop_local_background_daemon,
)
from aoe_tg_external_background_worker import (
    external_background_ack_path,
    emit_external_background_handoff,
    external_background_handoff_path,
    external_background_result_path,
    poll_external_background_tickets,
)
from aoe_tg_tmux_background_worker import (
    build_local_tmux_session_name,
    launch_local_tmux_background_ticket,
    local_tmux_log_path,
    local_tmux_result_path,
    poll_local_tmux_background_tickets,
)
from aoe_tg_request_contract import (
    background_run_evidence_artifacts_from_task,
    background_run_evidence_bundle_from_task,
    build_background_launch_spec,
    build_external_runner_gateway_command_launch_spec,
    build_github_runner_background_launch_spec,
    build_gateway_run_command_text,
    build_gateway_simulation_command_argv,
    build_local_tmux_background_launch_spec,
    build_local_tmux_gateway_command_launch_spec,
    build_local_tmux_gateway_run_launch_spec,
    build_remote_worker_background_launch_spec,
    build_runner_background_launch_spec,
    build_background_run_ticket,
    gateway_cli_entrypoint_path,
    select_background_runner_target,
)

def test_orch_map_reply_markup_contains_use_focus_status_todo_and_active_sync_actions() -> None:
    state = _empty_state()
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": "/tmp/TwinPaper",
        "team_dir": "/tmp/TwinPaper/.aoe-team",
    }
    state["active"] = "twinpaper"

    markup = overview._orch_map_reply_markup(state)

    assert isinstance(markup, dict)
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    assert "/use O1" not in buttons
    assert "/focus O1" not in buttons
    assert "/use O2" in buttons
    assert "/focus O2" in buttons
    assert "/orch status O1" not in buttons
    assert "/todo O1" not in buttons
    assert "/todo O1 followup" not in buttons
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/todo O2 followup" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/sync O2 1h" in buttons
    assert "/queue" in buttons
    assert "/next" in buttons


def test_orch_map_reply_markup_narrows_to_locked_project() -> None:
    state = _empty_state()
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": "/tmp/TwinPaper",
        "team_dir": "/tmp/TwinPaper/.aoe-team",
    }
    state["active"] = "twinpaper"
    gw.set_project_lock(state, "twinpaper")

    markup = overview._orch_map_reply_markup(state)

    assert isinstance(markup, dict)
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    assert "/use O1" not in buttons
    assert "/focus O1" not in buttons
    assert "/use O2" in buttons
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/todo O2 followup" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/sync O2 1h" in buttons
    assert "/focus off" in buttons


def test_orch_map_reply_markup_includes_repair_for_unready_project(tmp_path: Path) -> None:
    state = _empty_state()
    team_dir = tmp_path / "TwinPaper" / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(tmp_path / "TwinPaper"),
        "team_dir": str(team_dir),
    }

    markup = overview._orch_map_reply_markup(state)

    assert isinstance(markup, dict)
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    assert "/orch repair O2" in buttons


def test_resolve_message_command_parses_slash_orch_repair() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch repair O2",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-repair"
    assert resolved.orch_target == "O2"


def test_resolve_message_command_parses_slash_orch_bgq_clean() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch bgq-clean O2",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-bgq-clean"
    assert resolved.orch_target == "O2"


def test_resolve_message_command_parses_slash_orch_bgw_start() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch bgw-start O2",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-bgw-start"
    assert resolved.orch_target == "O2"


def test_resolve_message_command_parses_slash_orch_bg_runner() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch bg-runner O2 local_tmux",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-bg-runner"
    assert resolved.orch_target == "O2"
    assert resolved.rest == "local_tmux"


def test_resolve_message_command_parses_slash_orch_run_lock() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch run-lock O2 test_only",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-run-lock"
    assert resolved.orch_target == "O2"
    assert resolved.rest == "test_only"


def test_resolve_message_command_parses_slash_orch_bg_slots() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch bg-slots O2 2",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-bg-slots"
    assert resolved.orch_target == "O2"
    assert resolved.rest == "2"


def test_resolve_message_command_parses_slash_orch_bg_slots_with_runner() -> None:
    resolved = resolver.resolve_message_command(
        text="/orch bg-slots O2 github_runner 3",
        slash_only=False,
        manager_state=_empty_state(),
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-bg-slots"
    assert resolved.orch_target == "O2"
    assert resolved.rest == "github_runner 3"


def test_orch_repair_rebuilds_missing_runtime(tmp_path: Path) -> None:
    state = _empty_state()
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "overview": "Twin project orchestration",
        "tasks": {},
    }
    state["active"] = "twinpaper"

    messages = []

    def _send(msg: str, **kwargs):
        messages.append(msg)
        return True

    def _run_aoe_init(args, project_root: Path, team_dir: Path, overview: str) -> str:
        team_dir.mkdir(parents=True, exist_ok=True)
        (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
        return "[OK] initialized"

    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-repair",
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=_send,
        log_event=lambda **kwargs: None,
        get_context=lambda target: (_ for _ in ()).throw(RuntimeError("not used")),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=_run_aoe_init,
        run_aoe_spawn=lambda *args, **kwargs: "[SKIP] spawn",
        now_iso=lambda: "2026-03-07T18:30:00+0900",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert (team_dir / "orchestrator.json").exists()
    assert (team_dir / "AOE_TODO.md").exists()
    assert messages
    assert "orch repair finished" in messages[-1]
    assert "- after: ready" in messages[-1]


def test_orch_bgq_clean_marks_stale_background_queue_tickets(tmp_path: Path) -> None:
    state = _empty_state()
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "overview": "Twin project orchestration",
        "tasks": {},
    }
    state["active"] = "twinpaper"
    queue_path = background_runs_state_path(team_dir)
    upsert_background_run_ticket(
        queue_path,
        {
            "ticket_id": "BGT-STALE-1",
            "request_id": "REQ-STALE-1",
            "project_key": "twinpaper",
            "execution_brief_status": "executable",
            "runner_target": "local_background",
            "launch_mode": "detached_no_wait",
            "created_by": "test",
            "source_surface": "offdesk",
            "status": "running",
            "created_at": "2026-03-16T07:00:00+09:00",
        },
        now_iso=lambda: "2026-03-16T07:00:00+09:00",
    )

    sent = []

    def _send(msg: str, **kwargs):
        sent.append((msg, kwargs.get("context", ""), kwargs.get("reply_markup")))
        return True

    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-bgq-clean",
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
            require_verifier=False,
            verifier_roles="",
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=_send,
        log_event=lambda **kwargs: None,
        get_context=lambda target: ("twinpaper", state["projects"]["twinpaper"], argparse.Namespace(team_dir=team_dir)),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-03-16T10:30:00+09:00",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    snapshot = summarize_background_runs_state(queue_path)

    assert handled is True
    assert sent
    text, context, reply_markup = sent[-1]
    assert context == "orch-bgq-clean"
    assert "background queue cleanup" in text
    assert "- marked_stale: 1" in text
    assert snapshot["stale_count"] >= 1
    buttons = [btn["text"] for row in (reply_markup or {}).get("keyboard", []) for btn in row]
    assert "/orch status O2" in buttons


def test_orch_bgw_start_and_status_and_stop_manage_local_background_daemon(tmp_path: Path) -> None:
    state = _empty_state()
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (team_dir / "AOE_TODO.md").write_text("../TODO.md\n", encoding="utf-8")
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "overview": "Twin project orchestration",
        "tasks": {},
    }
    state["active"] = "twinpaper"
    sent = []

    def _send(msg: str, **kwargs):
        sent.append((msg, kwargs.get("context", ""), kwargs.get("reply_markup")))
        return True

    common_kwargs = dict(
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
            require_verifier=False,
            verifier_roles="",
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=_send,
        log_event=lambda **kwargs: None,
        get_context=lambda target: ("twinpaper", state["projects"]["twinpaper"], argparse.Namespace(team_dir=team_dir)),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-04-06T12:30:00+09:00",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bgw-start", **common_kwargs) is True
    start_text, start_context, start_markup = sent[-1]
    assert start_context == "orch-bgw-start"
    assert "background worker start" in start_text
    assert "- started: yes" in start_text
    start_buttons = [btn["text"] for row in (start_markup or {}).get("keyboard", []) for btn in row]
    assert "/orch bgw-stop O2" in start_buttons

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bgw-status", **common_kwargs) is True
    status_text, status_context, _status_markup = sent[-1]
    assert status_context == "orch-bgw-status"
    assert "background worker status" in status_text
    assert "status=running" in status_text or "status=idle" in status_text

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bgw-stop", **common_kwargs) is True
    stop_text, stop_context, stop_markup = sent[-1]
    assert stop_context == "orch-bgw-stop"
    assert "background worker stop" in stop_text
    assert "- stopped: yes" in stop_text
    stop_buttons = [btn["text"] for row in (stop_markup or {}).get("keyboard", []) for btn in row]
    assert "/orch bgw-start O2" in stop_buttons

    audit_file = team_dir / "dashboard" / "action-history.jsonl"
    rows = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["source_command"] for row in rows[-3:]] == [
        "/orch bgw-start O2",
        "/orch bgw-status O2",
        "/orch bgw-stop O2",
    ]
    assert rows[-3]["headline"] == "Background Worker Start | executed"
    assert rows[-3]["next_step"] == "/orch status O2"
    assert "background_worker.json" in rows[-3]["remediation"]
    assert rows[-2]["headline"] == "Background Worker Status | accepted"
    assert rows[-2]["link_href"] == "/control/runtimes/O2"
    assert rows[-1]["headline"] == "Background Worker Stop | executed"
    assert "queued tickets" in rows[-1]["remediation"]


def test_orch_status_surfaces_external_background_phase(tmp_path: Path) -> None:
    state = _empty_state()
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "overview": "Twin project orchestration",
        "tasks": {
            "REQ-EXT-001": {
                "request_id": "REQ-EXT-001",
                "short_id": "T-401",
                "status": "running",
                "updated_at": "2026-04-07T22:10:00+09:00",
                "background_run_runner_target": "github_runner",
                "background_run_external_phase": "pickup_acknowledged",
                "background_run_external_note": "background_run_acks/github-runner-bgt-ext-001.json",
            }
        },
    }
    state["active"] = "twinpaper"
    sent = []

    common_kwargs = dict(
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
            require_verifier=False,
            verifier_roles="",
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=lambda msg, **kwargs: sent.append((msg, kwargs.get("context", ""), kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        get_context=lambda target: ("twinpaper", state["projects"]["twinpaper"], argparse.Namespace(team_dir=team_dir)),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-04-07T22:20:00+09:00",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-status", **common_kwargs) is True
    text, context, _reply_markup = sent[-1]
    assert context == "status"
    assert "background_external: T-401 | github_runner | pickup_acknowledged | background_run_acks/github-runner-bgt-ext-001.json" in text


def test_orch_bg_runner_sets_preference_and_status_surfaces_effective_runner(tmp_path: Path) -> None:
    state = _empty_state()
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "overview": "Twin project orchestration",
        "tasks": {},
    }
    state["active"] = "twinpaper"
    sent = []

    def _send(msg: str, **kwargs):
        sent.append((msg, kwargs.get("context", ""), kwargs.get("reply_markup")))
        return True

    common_kwargs = dict(
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
            require_verifier=False,
            verifier_roles="",
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="local_tmux",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=_send,
        log_event=lambda **kwargs: None,
        get_context=lambda target: ("twinpaper", state["projects"]["twinpaper"], argparse.Namespace(team_dir=team_dir)),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-04-06T12:30:00+09:00",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bg-runner", **common_kwargs) is True
    pref_text, pref_context, pref_markup = sent[-1]
    assert pref_context == "orch-bg-runner"
    assert "background runner preference" in pref_text
    assert "- preferred: local_tmux" in pref_text
    assert "- effective: local_background" in pref_text
    assert "externalizable launch spec" in pref_text
    assert state["projects"]["twinpaper"]["background_runner_target"] == "local_tmux"
    buttons = [btn["text"] for row in (pref_markup or {}).get("keyboard", []) for btn in row]
    assert "/orch bg-runner O2 local_background" in buttons

    assert orch_task_handlers.handle_orch_task_command(cmd="orch-status", **common_kwargs) is True
    status_text, status_context, _ = sent[-1]
    assert status_context == "status"
    assert "background_runner: pref=local_tmux | effective=local_background" in status_text
    assert "background_runner_note: preferred local_tmux is pending until an externalizable launch spec exists" in status_text

    common_kwargs["rest"] = "test_only"
    assert orch_task_handlers.handle_orch_task_command(cmd="orch-run-lock", **common_kwargs) is True
    lock_text, lock_context, _ = sent[-1]
    assert lock_context == "orch-run-lock"
    assert "- mode: test_only" in lock_text
    assert state["projects"]["twinpaper"]["run_lock_mode"] == "test_only"

    common_kwargs["rest"] = ""
    assert orch_task_handlers.handle_orch_task_command(cmd="orch-status", **common_kwargs) is True
    status_text, status_context, _ = sent[-1]
    assert status_context == "status"
    assert "run_lock: test_only" in status_text
    assert "run_lock_note: test_only lock is active; only small test launches are allowed" in status_text

    common_kwargs["rest"] = "2"
    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bg-slots", **common_kwargs) is True
    slots_text, slots_context, _ = sent[-1]
    assert slots_context == "orch-bg-slots"
    assert "- limit: 2" in slots_text
    assert state["projects"]["twinpaper"]["background_runner_slot_limit"] == 2

    common_kwargs["rest"] = "github_runner 3"
    assert orch_task_handlers.handle_orch_task_command(cmd="orch-bg-slots", **common_kwargs) is True
    slots_text, slots_context, _ = sent[-1]
    assert slots_context == "orch-bg-slots"
    assert "- runner: github_runner" in slots_text
    assert "- limit: 3" in slots_text
    assert state["projects"]["twinpaper"]["background_runner_slot_limits"]["github_runner"] == 3

    common_kwargs["rest"] = ""
    assert orch_task_handlers.handle_orch_task_command(cmd="orch-status", **common_kwargs) is True
    status_text, status_context, _ = sent[-1]
    assert status_context == "status"
    assert "background_slots: runner=local_tmux limit=2 active=0" in status_text
    assert "github_runner=0/3" in status_text


def test_no_wait_detach_uses_local_tmux_when_serializable_launch_spec_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    upsert_background_run_ticket(
        team_dir / "background_runs.json",
        build_background_run_ticket(
            ticket_id="BGT-EXTERNAL-BUSY-001",
            request_id="REQ-EXTERNAL-BUSY-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="github_runner",
            launch_mode="dashboard_retry",
            created_at="2026-03-13T18:54:00+0900",
            created_by="dashboard:control",
            source_surface="dashboard_retry",
            status="running",
        ),
        now_iso=lambda: "2026-03-13T18:54:01+0900",
    )
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "background_runner_target": "local_tmux",
                "todos": [],
            }
        }
    }
    sent = []
    logged = []
    saves = []
    daemon = {}
    launched = {}

    monkeypatch.setattr(
        run_detached_flow,
        "ensure_local_background_daemon",
        lambda **kwargs: daemon.update(kwargs) or {"started": True, "thread_name": "aoe-local-bg-test", "runner_target": kwargs.get("runner_target", "")},
    )
    monkeypatch.setattr(
        run_detached_flow,
        "launch_local_tmux_background_ticket",
        lambda **kwargs: launched.update(kwargs)
        or advance_background_run_ticket(
            kwargs["queue_path"],
            kwargs["ticket_id"],
            now_iso=kwargs["now_iso"],
            status="running",
            runner_target="local_tmux",
            launch_mode="detached_no_wait",
            created_by=kwargs.get("claimed_by", ""),
            source_surface=kwargs.get("source_surface", ""),
            runtime_handle="aoe_bg_req_detached_tmux",
            runtime_summary="tmux_session=aoe_bg_req_detached_tmux",
            evidence_bundle="status=running | outcome=tmux_session_started | session=aoe_bg_req_detached_tmux",
            evidence_artifacts=["background_run_logs/req-detached-tmux.log"],
        ),
    )

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saves.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should be detached when --no-wait is set")),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: "REQ-DETACHED-TMUX",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-13T18:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert daemon["runner_target"] == "local_background"
    assert launched["launch_mode"] == "detached_no_wait"
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DETACHED-TMUX"]
    assert task["background_run_runner_target"] == "local_tmux"
    assert task["background_run_status"] == "running"
    assert task["background_run_runtime_handle"] == "aoe_bg_req_detached_tmux"
    assert task["background_run_launch_spec_summary"].startswith("background_dispatch | mode=tmux_session_json")
    assert task["background_run_launch_spec_summary"].endswith("externalizable=yes")
    queue_file = team_dir / "background_runs.json"
    rows = json.loads(queue_file.read_text(encoding="utf-8")).get("runs") or []
    row = next(row for row in rows if str(row.get("ticket_id", "")).startswith("BGT-REQ-DETACHED-TMUX-"))
    assert row["runner_target"] == "local_tmux"
    assert row["status"] == "running"
    assert row["launch_spec"]["externalizable"] is True
    assert row["launch_spec"]["mode"] == "tmux_session_json"
    assert row["launch_spec"]["command_argv"][1] == gateway_cli_entrypoint_path()
    assert "--simulate-text" in row["launch_spec"]["command_argv"]
    assert "aoe orch run --orch O2 --dispatch --roles Codex-Dev --priority P2 --timeout-sec 120 'run it'" in row["launch_spec"]["command_argv"]


def test_no_wait_detach_falls_back_to_local_background_without_manager_state_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "background_runner_target": "local_tmux",
                "todos": [],
            }
        }
    }
    daemon = {}

    monkeypatch.setattr(
        run_detached_flow,
        "ensure_local_background_daemon",
        lambda **kwargs: daemon.update(kwargs) or {"started": True, "thread_name": "aoe-local-bg-test", "runner_target": kwargs.get("runner_target", "")},
    )

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file="",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda *args, **kwargs: True,
            log_event=lambda **kwargs: None,
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda *args, **kwargs: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should be detached when --no-wait is set")),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: "REQ-DETACHED-FALLBACK",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-13T18:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert daemon["runner_target"] == "local_background"
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DETACHED-FALLBACK"]
    assert task["background_run_runner_target"] == "local_background"
    assert task["background_run_launch_spec_summary"].startswith("gateway_dispatch | mode=in_process_callback")


def test_no_wait_detach_blocks_when_run_lock_is_test_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "background_runner_target": "local_tmux",
                "run_lock_mode": "test_only",
                "todos": [],
            }
        }
    }
    sent = []
    logged = []

    monkeypatch.setattr(
        run_detached_flow,
        "ensure_local_background_daemon",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("daemon should not start under test-only run lock")),
    )
    monkeypatch.setattr(
        run_detached_flow,
        "launch_local_tmux_background_ticket",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("tmux launch should not start under test-only run lock")),
    )

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda *args, **kwargs: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should be detached when --no-wait is set")),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: "REQ-DETACHED-LOCKED",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-13T18:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert sent
    assert sent[-1][0] == "dispatch-detach blocked"
    assert "/orch run-lock O2 open" in sent[-1][1]
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DETACHED-LOCKED"]
    assert task["background_run_status"] == "failed"
    assert task["background_run_evidence_bundle"] == "status=failed | reason=run_lock_test_only"
    assert any(row.get("event") == "dispatch_detach_blocked" for row in logged)


def test_no_wait_detach_blocks_when_background_slots_are_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    queue_path = team_dir / "background_runs.json"
    upsert_background_run_ticket(
        queue_path,
        build_background_run_ticket(
            ticket_id="BGT-BUSY-001",
            request_id="REQ-BUSY-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="dashboard_retry",
            created_at="2026-04-07T11:00:00+09:00",
            created_by="dashboard:control",
            source_surface="dashboard_retry",
            status="running",
        ),
        now_iso=lambda: "2026-04-07T11:00:01+09:00",
    )
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "background_runner_target": "local_tmux",
                "background_runner_slot_limit": 1,
                "todos": [],
            }
        }
    }
    sent = []
    logged = []

    monkeypatch.setattr(
        run_detached_flow,
        "ensure_local_background_daemon",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("daemon should not start when slots are exhausted")),
    )
    monkeypatch.setattr(
        run_detached_flow,
        "launch_local_tmux_background_ticket",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("tmux launch should not start when slots are exhausted")),
    )

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda *args, **kwargs: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should be detached when --no-wait is set")),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: "REQ-DETACHED-SLOTS",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-13T18:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert sent
    assert sent[-1][0] == "dispatch-detach blocked"
    assert "/orch bg-slots O2 local_tmux 2" in sent[-1][1]
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DETACHED-SLOTS"]
    assert task["background_run_status"] == "failed"
    assert task["background_run_evidence_bundle"] == "status=failed | reason=background_runner_slots_exhausted"
    assert any(row.get("event") == "dispatch_detach_blocked" for row in logged)


def test_orch_repair_all_repairs_multiple_projects(tmp_path: Path) -> None:
    state = _empty_state()
    for key, alias in [("twinpaper", "O2"), ("nano", "O3")]:
        project_root = tmp_path / key
        team_dir = project_root / ".aoe-team"
        team_dir.mkdir(parents=True, exist_ok=True)
        state["projects"][key] = {
            "name": key,
            "display_name": key,
            "project_alias": alias,
            "project_root": str(project_root),
            "team_dir": str(team_dir),
            "overview": f"{key} orchestration",
            "tasks": {},
        }

    messages = []

    def _send(msg: str, **kwargs):
        messages.append(msg)
        return True

    def _run_aoe_init(args, project_root: Path, team_dir: Path, overview: str) -> str:
        (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
        return "[OK] initialized"

    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-repair",
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=tmp_path / "manager_state.json",
            dry_run=False,
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="all",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=_send,
        log_event=lambda **kwargs: None,
        get_context=lambda target: (_ for _ in ()).throw(RuntimeError("not used")),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=_run_aoe_init,
        run_aoe_spawn=lambda *args, **kwargs: "[SKIP] spawn",
        now_iso=lambda: "2026-03-07T18:30:00+0900",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert messages
    assert "orch repair all finished" in messages[-1]
    assert "- projects: 3" in messages[-1]
    assert "- ready: 3" in messages[-1]


def test_orch_status_under_other_focus_returns_operator_message() -> None:
    state = _empty_state()
    messages = []

    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-status",
        args=argparse.Namespace(
            project_root=ROOT,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            dry_run=True,
        ),
        manager_state=state,
        chat_id="939062873",
        orch_target="O2",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        send=lambda msg, **kwargs: messages.append(msg) or True,
        log_event=lambda **kwargs: None,
        get_context=lambda target: (_ for _ in ()).throw(
            RuntimeError("project lock active: O4 (local_map_analysis). requested=O2 (twinpaper). use /focus off or /focus O4")
        ),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda path, state: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-03-07T18:30:00+0900",
        run_aoe_status=lambda p_args: "",
        resolve_chat_task_ref=lambda *args, **kwargs: "",
        resolve_task_request_id=lambda entry, ref: "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda *args, **kwargs: None,
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda *args, **kwargs: "",
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert messages
    assert "orch status blocked by project lock" in messages[-1]


def test_todo_reply_markup_contains_run_done_and_drilldown_actions() -> None:
    entry = {"project_alias": "O2"}
    active_rows = [
        {"id": "TODO-001", "status": "blocked", "blocked_bucket": "manual_followup"},
        {"id": "TODO-002", "status": "running"},
        {"id": "TODO-003", "status": "blocked"},
    ]

    markup = todo_handlers._todo_reply_markup("twinpaper", entry, active_rows)

    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    for expected in [
        "/todo next",
        "/todo followup",
        "/orch status O2",
        "/sync preview O2 1h",
        "/todo ackrun 1",
        "/todo ack 1",
        "/todo done 1",
        "/todo done 2",
        "/todo done 3",
        "/sync O2 1h",
        "/queue",
        "/next",
        "/map",
        "/help",
    ]:
        assert expected in buttons


def test_todo_reply_markup_omits_ack_buttons_without_manual_followup() -> None:
    entry = {"project_alias": "O2"}
    active_rows = [
        {"id": "TODO-001", "status": "open"},
        {"id": "TODO-002", "status": "running"},
        {"id": "TODO-003", "status": "blocked"},
    ]

    markup = todo_handlers._todo_reply_markup("twinpaper", entry, active_rows)

    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    assert "/todo ack 1" not in buttons


def test_todo_list_shows_block_count_and_reason(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "need owner input",
                        "priority": "P1",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                        "blocked_reason": "plan gate: critic unresolved after auto-replan",
                    }
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T00:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    assert "manual_followup:" in sent[-1]
    assert "blocked x2 [manual_followup] | plan gate: critic unresolved after auto-replan" in sent[-1]


def test_todo_followup_lists_only_manual_followup_rows(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "need owner input",
                        "priority": "P1",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                        "blocked_reason": "plan gate: critic unresolved after auto-replan",
                    },
                    {
                        "id": "TODO-002",
                        "summary": "regular open task",
                        "priority": "P2",
                        "status": "open",
                    },
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="followup",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T00:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    text = sent[-1]
    assert "todo followup: count=1 active=2" in text
    assert "manual_followup:" in text
    assert "tip: /todo ackrun <번호|TODO-xxx>" in text
    assert "TODO-001" in text
    assert "TODO-002" not in text


def test_todo_syncback_preview_reports_done_append_and_blocked_notes(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text(
        "# Tasks\n- [ ] phase1 rerun\n- [ ] existing backlog\n",
        encoding="utf-8",
    )
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "updated_at": "2026-03-07T21:00:00+0900",
                "todos": [
                    {"id": "TODO-001", "summary": "phase1 rerun", "priority": "P1", "status": "done"},
                    {
                        "id": "TODO-002",
                        "summary": "need owner input",
                        "priority": "P2",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                        "blocked_reason": "plan gate: critic unresolved",
                    },
                ],
                "todo_proposals": [
                    {"id": "PROP-001", "summary": "accepted follow-up", "priority": "P2", "status": "accepted"}
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="syncback preview",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T21:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    text = sent[-1]
    assert "todo syncback preview" in text
    assert "- runtime: twinpaper (O2)" in text
    assert "- mark_done: 1" in text
    assert "- append_new: 1" in text
    assert "- blocked_notes: 1" in text
    assert "- - [ ] P2: accepted follow-up" in text


def test_todo_ack_reopens_blocked_followup_and_clears_blocked_meta(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "need owner input",
                        "priority": "P1",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                        "blocked_alerted_at": "2026-03-07T00:30:00+0900",
                        "blocked_reason": "plan gate: critic unresolved after auto-replan",
                    }
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="ack 1",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result == {"terminal": True}
    row = manager_state["projects"]["twinpaper"]["todos"][0]
    assert row["status"] == "open"
    assert "blocked_count" not in row
    assert "blocked_bucket" not in row
    assert "blocked_alerted_at" not in row
    assert "blocked_reason" not in row
    assert sent
    assert "todo acknowledged" in sent[-1]
    assert "- reopened: yes" in sent[-1]
    assert "- cleared_followup: yes" in sent[-1]


def test_todo_ackrun_reopens_blocked_followup_and_returns_run_transition(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "need owner input",
                        "priority": "P1",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                        "blocked_alerted_at": "2026-03-07T00:30:00+0900",
                        "blocked_reason": "plan gate: critic unresolved after auto-replan",
                    }
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="ackrun 1",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result["terminal"] is False
    assert result["cmd"] == "run"
    assert result["orch_target"] == "twinpaper"
    assert result["run_prompt"] == "need owner input"
    assert result["run_force_mode"] == "dispatch"
    assert result["run_auto_source"] == "todo-ackrun"
    row = manager_state["projects"]["twinpaper"]["todos"][0]
    assert row["status"] == "open"
    assert "blocked_count" not in row
    assert "blocked_bucket" not in row
    assert "blocked_alerted_at" not in row
    assert "blocked_reason" not in row
    assert manager_state["projects"]["twinpaper"]["pending_todo"]["todo_id"] == "TODO-001"
    assert sent
    assert "todo ackrun selected" in sent[-1]
    assert "- reopened: yes" in sent[-1]
    assert "- cleared_followup: yes" in sent[-1]


def test_todo_ack_rejects_non_blocked_row(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "regular open task",
                        "priority": "P2",
                        "status": "open",
                    }
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="ack 1",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    assert "todo ack blocked: target is not blocked" in sent[-1]


def test_todo_ackrun_rejects_non_blocked_row(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "regular open task",
                        "priority": "P2",
                        "status": "open",
                    }
                ],
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="ackrun 1",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    assert "todo ackrun blocked: target is not blocked" in sent[-1]


def test_merge_todo_proposals_dedupes_existing_open_proposals_and_todos() -> None:
    entry = {
        "todos": [
            {"id": "TODO-001", "summary": "existing todo"},
        ],
        "todo_seq": 1,
        "todo_proposals": [
            {
                "id": "PROP-001",
                "summary": "existing proposal",
                "priority": "P2",
                "kind": "followup",
                "status": "open",
            }
        ],
        "todo_proposal_seq": 1,
    }

    merged = todo_handlers.merge_todo_proposals(
        entry=entry,
        request_id="REQ-123",
        task={"short_id": "T-123"},
        source_todo_id="TODO-009",
        proposals_data=[
            {"summary": "existing proposal", "priority": "P1", "kind": "followup", "reason": "dup", "confidence": 0.9},
            {"summary": "existing todo", "priority": "P2", "kind": "risk", "reason": "dup", "confidence": 0.6},
            {"summary": "new actionable follow-up", "priority": "P1", "kind": "followup", "reason": "new", "confidence": 0.8},
        ],
        now_iso=lambda: "2026-03-09T09:00:00+0900",
    )

    assert merged["created_count"] == 1
    assert merged["duplicate_count"] == 2
    assert entry["todo_proposals"][-1]["summary"] == "new actionable follow-up"
    assert entry["todo_proposals"][-1]["source_request_id"] == "REQ-123"


def test_todo_state_module_matches_handler_merge_and_syncback_preview(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "TODO.md").write_text(
        "# Tasks\n- [ ] phase1 rerun\n- [ ] existing backlog\n",
        encoding="utf-8",
    )

    entry_a = {
        "project_root": str(project_root),
        "updated_at": "2026-03-09T09:00:00+0900",
        "todos": [
            {"id": "TODO-001", "summary": "phase1 rerun", "priority": "P1", "status": "done"},
            {
                "id": "TODO-002",
                "summary": "need owner input",
                "priority": "P2",
                "status": "blocked",
                "blocked_count": 2,
                "blocked_bucket": "manual_followup",
                "blocked_reason": "plan gate: critic unresolved",
            },
        ],
        "todo_seq": 2,
        "todo_proposals": [
            {"id": "PROP-001", "summary": "accepted follow-up", "priority": "P2", "status": "accepted"}
        ],
        "todo_proposal_seq": 1,
    }
    entry_b = copy.deepcopy(entry_a)

    merge_kwargs = dict(
        request_id="REQ-123",
        task={"short_id": "T-123"},
        source_todo_id="TODO-009",
        proposals_data=[
            {"summary": "existing backlog", "priority": "P2", "kind": "risk", "reason": "dup", "confidence": 0.6},
            {"summary": "new actionable follow-up", "priority": "P1", "kind": "followup", "reason": "new", "confidence": 0.8},
        ],
        now_iso=lambda: "2026-03-09T09:00:00+0900",
    )

    merged_a = todo_handlers.merge_todo_proposals(entry=entry_a, **merge_kwargs)
    merged_b = todo_state.merge_todo_proposals(entry=entry_b, **merge_kwargs)

    assert merged_a == merged_b
    assert entry_a == entry_b

    plan_a = todo_handlers._preview_syncback_plan(entry_a)
    plan_b = todo_state.preview_syncback_plan(entry_b)
    assert plan_a == plan_b


def test_todo_state_module_matches_handler_accept_and_reject_mutations(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    base_entry = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(team_dir),
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [
            {
                "id": "PROP-001",
                "summary": "write release checklist",
                "priority": "P1",
                "kind": "handoff",
                "status": "open",
                "reason": "deployment notes are missing",
                "confidence": 0.9,
                "source_request_id": "REQ-100",
                "source_todo_id": "TODO-000",
            },
            {
                "id": "PROP-002",
                "summary": "collect schema debt",
                "priority": "P2",
                "kind": "debt",
                "status": "open",
                "reason": "schema drift remains",
                "confidence": 0.7,
                "source_request_id": "REQ-101",
            },
        ],
        "todo_proposal_seq": 2,
    }
    manager_state = {"projects": {"twinpaper": copy.deepcopy(base_entry)}}
    entry_state = copy.deepcopy(base_entry)
    sent: list[str] = []

    def _ctx(_raw: str):
        return ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir))

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="accept 1",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=_ctx,
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-09T10:00:00+0900",
    )
    assert result == {"terminal": True}

    state_accept = todo_state.accept_todo_proposal(
        entry=entry_state,
        proposal=entry_state["todo_proposals"][0],
        actor="telegram:939062873",
        now="2026-03-09T10:00:00+0900",
    )
    assert state_accept["todo_id"] == "TODO-001"
    assert manager_state["projects"]["twinpaper"] == entry_state

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="reject 1 duplicate debt",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=_ctx,
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-09T10:05:00+0900",
    )
    assert result == {"terminal": True}

    state_reject = todo_state.reject_todo_proposal(
        entry=entry_state,
        proposal=entry_state["todo_proposals"][1],
        actor="telegram:939062873",
        now="2026-03-09T10:05:00+0900",
        reason="duplicate debt",
    )
    assert state_reject["reason"] == "duplicate debt"
    assert manager_state["projects"]["twinpaper"] == entry_state


def test_todo_policy_helpers_cover_syncback_and_proposal_to_todo_rules() -> None:
    row = {
        "id": "TODO-010",
        "summary": "prepare deployment checklist",
        "priority": "P1",
        "status": "running",
        "created_by": "tf-proposal",
        "created_from_request_id": "REQ-900",
    }
    assert todo_policy.todo_row_syncback_target_status(row) == "open"
    assert todo_policy.todo_row_syncback_appendable(row) is True

    done_row = {"summary": "phase1 rerun", "priority": "P2", "status": "done"}
    assert todo_policy.todo_row_syncback_target_status(done_row) == "done"
    assert todo_policy.format_canonical_todo_line("P2", "phase1 rerun", status="done") == "- [x] P2: phase1 rerun"

    proposal = {
        "id": "PROP-001",
        "summary": "write release checklist",
        "priority": "P1",
        "kind": "handoff",
        "status": "accepted",
        "source_request_id": "REQ-100",
        "source_todo_id": "TODO-000",
        "source_file": "docs/handoff.md",
        "source_section": "Next steps",
        "source_reason": "handoff section bullet",
        "source_line": 12,
    }
    todo_row = todo_policy.proposal_to_todo_row(proposal, todo_id="TODO-001", now="2026-03-09T10:00:00+0900")
    assert todo_row["proposal_id"] == "PROP-001"
    assert todo_row["proposal_kind"] == "handoff"
    assert todo_row["created_from_request_id"] == "REQ-100"
    assert todo_row["source_file"] == "docs/handoff.md"
    assert todo_row["source_line"] == 12

    accepted = todo_policy.accepted_proposals_for_syncback(
        [
            {"id": "PROP-001", "status": "accepted"},
            {"id": "PROP-002", "status": "open"},
            {"id": "PROP-003", "status": "rejected"},
        ]
    )
    assert [row["id"] for row in accepted] == ["PROP-001"]


def test_todo_proposals_list_accept_and_reject_flow(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
                "todo_seq": 0,
                "todo_proposals": [
                    {
                        "id": "PROP-001",
                        "summary": "write release checklist",
                        "priority": "P1",
                        "kind": "handoff",
                        "status": "open",
                        "reason": "deployment notes are missing",
                        "confidence": 0.9,
                        "source_request_id": "REQ-100",
                        "source_todo_id": "TODO-000",
                    },
                    {
                        "id": "PROP-002",
                        "summary": "collect schema debt",
                        "priority": "P2",
                        "kind": "debt",
                        "status": "open",
                        "reason": "schema drift remains",
                        "confidence": 0.7,
                        "source_request_id": "REQ-101",
                    },
                ],
                "todo_proposal_seq": 2,
            }
        }
    }
    sent: list[tuple[str, dict | None]] = []

    def _send(body: str, **kwargs) -> bool:
        sent.append((body, kwargs.get("reply_markup")))
        return True

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="proposals",
        send=_send,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-09T09:00:00+0900",
    )

    assert result == {"terminal": True}
    assert "todo proposals: open=2" in sent[-1][0]
    buttons = [btn["text"] for row in (sent[-1][1] or {}).get("keyboard", []) for btn in row]
    assert "/todo accept 1" in buttons
    assert "/todo reject 1" in buttons

    sent.clear()
    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="accept 1",
        send=_send,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-09T09:05:00+0900",
    )

    assert result == {"terminal": True}
    proposal = manager_state["projects"]["twinpaper"]["todo_proposals"][0]
    assert proposal["status"] == "accepted"
    assert proposal["accepted_todo_id"] == "TODO-001"
    assert manager_state["projects"]["twinpaper"]["todos"][0]["proposal_id"] == "PROP-001"
    assert "todo proposal accepted" in sent[-1][0]

    sent.clear()
    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="reject 1 duplicate debt",
        send=_send,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-09T09:10:00+0900",
    )

    assert result == {"terminal": True}
    proposal = manager_state["projects"]["twinpaper"]["todo_proposals"][1]
    assert proposal["status"] == "rejected"
    assert proposal["rejected_reason"] == "duplicate debt"
    assert "todo proposal rejected" in sent[-1][0]


def test_todo_next_pending_includes_ok_and_clear_pending_buttons(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "pending_todo": {"todo_id": "TODO-009", "chat_id": "939062873", "selected_at": "2026-03-07T00:50:00+0900"},
                "todos": [
                    {"id": "TODO-001", "summary": "regular open task", "priority": "P2", "status": "open"},
                ],
            }
        }
    }
    sent: list[tuple[str, dict | None]] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="next",
        send=lambda body, **kwargs: sent.append((body, kwargs.get("reply_markup"))) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    text, markup = sent[-1]
    assert "todo next blocked: pending todo exists" in text
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/ok" in buttons
    assert "/clear pending" in buttons
    assert "/todo next force" in buttons
    assert "/todo O2" in buttons


def test_todo_next_ignores_blocked_rows_when_open_todo_exists(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "blocked first",
                        "priority": "P1",
                        "status": "blocked",
                    },
                    {
                        "id": "TODO-002",
                        "summary": "open second",
                        "priority": "P2",
                        "status": "open",
                    },
                ],
            }
        }
    }
    saved: list[Path] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="next",
        send=lambda body, **kwargs: True,
        get_context=lambda raw: (
            "twinpaper",
            manager_state["projects"]["twinpaper"],
            argparse.Namespace(project_root=project_root, team_dir=team_dir),
        ),
        save_manager_state=lambda path, manager_state: saved.append(path),
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result["terminal"] is False
    assert result["cmd"] == "run"
    assert result["orch_target"] == "twinpaper"
    assert result["run_prompt"] == "open second"
    assert result["run_force_mode"] == "dispatch"
    pending = manager_state["projects"]["twinpaper"]["pending_todo"]
    assert pending["todo_id"] == "TODO-002"
    assert saved == [team_dir / "orch_manager_state.json"]


def test_todo_ackrun_pending_includes_ok_and_force_buttons(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "pending_todo": {"todo_id": "TODO-009", "chat_id": "939062873", "selected_at": "2026-03-07T00:50:00+0900"},
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "need owner input",
                        "priority": "P1",
                        "status": "blocked",
                        "blocked_count": 2,
                        "blocked_bucket": "manual_followup",
                    }
                ],
            }
        }
    }
    sent: list[tuple[str, dict | None]] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=team_dir / "orch_manager_state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="O2",
        rest="ackrun 1",
        send=lambda body, **kwargs: sent.append((body, kwargs.get("reply_markup"))) or True,
        get_context=lambda raw: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=project_root, team_dir=team_dir)),
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T01:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    text, markup = sent[-1]
    assert "todo ackrun blocked: pending todo exists" in text
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/ok" in buttons
    assert "/clear pending" in buttons
    assert "/todo O2 ackrun TODO-001 force" in buttons
    assert "/todo O2" in buttons


def test_confirm_required_reply_markup_contains_ok_cancel_and_clear_pending() -> None:
    state = _empty_state()
    sent: list[tuple[str, dict | None]] = []
    saved: list[Path] = []

    handled = run_handlers._handle_run_rate_limit_and_confirm(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=state,
        chat_id="939062873",
        key="twinpaper",
        entry={"project_alias": "O2"},
        run_auto_source="default",
        run_force_mode="dispatch",
        orch_target="O2",
        prompt="rm -rf /tmp/demo",
        summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
        detect_high_risk_prompt=lambda prompt: "destructive_delete",
        set_confirm_action=lambda *args, **kwargs: gw.set_confirm_action(*args, **kwargs),
        save_manager_state=lambda path, manager_state: saved.append(path),
        send=lambda body, **kwargs: sent.append((body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
    )

    assert handled is True
    assert sent
    text, markup = sent[-1]
    assert "고위험 자동실행 감지" in text
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/ok" in buttons
    assert "/cancel" in buttons
    assert "/clear pending" in buttons


def test_confirm_required_for_orch_action_dispatch_prompt() -> None:
    state = _empty_state()
    sent: list[tuple[str, dict | None]] = []

    handled = run_handlers._handle_run_rate_limit_and_confirm(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=state,
        chat_id="939062873",
        key="twinpaper",
        entry={"project_alias": "O2"},
        run_auto_source="orch-action:work",
        run_force_mode="dispatch",
        orch_target="O2",
        prompt="rm -rf /tmp/demo",
        summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
        detect_high_risk_prompt=lambda prompt: "destructive_delete",
        set_confirm_action=lambda *args, **kwargs: gw.set_confirm_action(*args, **kwargs),
        save_manager_state=lambda path, manager_state: None,
        send=lambda body, **kwargs: sent.append((body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
    )

    assert handled is True
    assert sent
    assert "고위험 자동실행 감지" in sent[-1][0]


def test_confirmed_run_does_not_reprompt_high_risk_confirmation() -> None:
    state = _empty_state()
    sent: list[tuple[str, dict | None]] = []

    handled = run_handlers._handle_run_rate_limit_and_confirm(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=state,
        chat_id="939062873",
        key="twinpaper",
        entry={"project_alias": "O2"},
        run_auto_source="confirmed",
        run_force_mode="dispatch",
        orch_target="O2",
        prompt="rm -rf /tmp/demo",
        summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
        detect_high_risk_prompt=lambda prompt: "destructive_delete",
        set_confirm_action=lambda *args, **kwargs: gw.set_confirm_action(*args, **kwargs),
        save_manager_state=lambda path, manager_state: None,
        send=lambda body, **kwargs: sent.append((body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
    )

    assert handled is False
    assert not sent


def test_rate_limit_running_reply_markup_uses_project_context_actions() -> None:
    state = _empty_state()
    sent: list[tuple[str, str, dict | None]] = []

    handled = run_handlers._handle_run_rate_limit_and_confirm(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=1,
            chat_daily_cap=20,
        ),
        manager_state=state,
        chat_id="939062873",
        key="twinpaper",
        entry={"project_alias": "O2"},
        run_auto_source="default",
        run_force_mode="dispatch",
        orch_target="O2",
        prompt="implement feature",
        summarize_chat_usage=lambda manager_state, chat_id: (1, 0),
        detect_high_risk_prompt=lambda prompt: "",
        set_confirm_action=lambda *args, **kwargs: None,
        save_manager_state=lambda path, manager_state: None,
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
    )

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "rate-limit-running"
    assert "동시 실행 한도를 초과했습니다" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/monitor" in buttons
    assert "/check" in buttons
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons


def test_rate_limit_daily_reply_markup_uses_global_actions_without_project_context() -> None:
    state = _empty_state()
    sent: list[tuple[str, str, dict | None]] = []

    handled = run_handlers._handle_run_rate_limit_and_confirm(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=3,
            chat_daily_cap=1,
        ),
        manager_state=state,
        chat_id="939062873",
        key="",
        entry=None,
        run_auto_source="default",
        run_force_mode="dispatch",
        orch_target=None,
        prompt="implement feature",
        summarize_chat_usage=lambda manager_state, chat_id: (0, 1),
        detect_high_risk_prompt=lambda prompt: "",
        set_confirm_action=lambda *args, **kwargs: None,
        save_manager_state=lambda path, manager_state: None,
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
    )

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "rate-limit-daily"
    assert "일일 실행 한도에 도달했습니다" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/monitor" in buttons
    assert "/check" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons
    assert "/help" in buttons


def test_enforce_dispatch_policies_verifier_gate_setup_adds_project_quick_actions() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    result = run_handlers._enforce_dispatch_policies(
        dispatch_mode=True,
        args=argparse.Namespace(require_verifier=True),
        key="twinpaper",
        entry={"project_alias": "O2"},
        selected_roles=["Codex-Dev"],
        available_roles=["Codex-Dev"],
        verifier_candidates=["Codex-Reviewer"],
        plan_gate_blocked=False,
        plan_gate_reason="",
        plan_replans=[],
        ensure_verifier_roles=lambda **kwargs: (["Codex-Dev"], [], False, []),
        dispatch_roles="Codex-Dev",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )

    assert result.terminal is True
    assert result.terminal_reason == "verifier gate: no verifier role is available"
    assert sent
    context, body, markup = sent[-1]
    assert context == "verifier-gate setup"
    assert "no verifier role is available" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/monitor" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons


def test_enforce_dispatch_policies_planning_gate_adds_project_quick_actions() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    result = run_handlers._enforce_dispatch_policies(
        dispatch_mode=True,
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry={"project_alias": "O2"},
        selected_roles=["Codex-Dev"],
        available_roles=["Codex-Dev", "Codex-Reviewer"],
        verifier_candidates=["Codex-Reviewer"],
        plan_gate_blocked=True,
        plan_gate_reason="critic unresolved after auto-replan",
        plan_replans=[{"attempt": 1}],
        ensure_verifier_roles=lambda **kwargs: (["Codex-Dev"], ["Codex-Reviewer"], False, ["Codex-Reviewer"]),
        dispatch_roles="Codex-Dev",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )

    assert result.terminal is True
    assert result.terminal_reason == "plan gate: critic unresolved after auto-replan"
    assert sent
    context, body, markup = sent[-1]
    assert context == "planning-gate"
    assert "plan gate blocked" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/monitor" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons


def test_run_guards_module_matches_run_guard_exports() -> None:
    assert run_handlers._confirm_required_reply_markup() == run_guards.confirm_required_reply_markup()
    assert run_handlers._rate_limit_reply_markup({"project_alias": "O2"}, "twinpaper") == run_guards.rate_limit_reply_markup({"project_alias": "O2"}, "twinpaper")
    assert run_handlers._rate_limit_reply_markup(None, "") == run_guards.rate_limit_reply_markup(None, "")

    guard_run = run_handlers._resolve_effective_run_options(
        p_args=argparse.Namespace(priority="P2", orch_timeout_sec=120, no_wait=False),
        run_priority_override="P1",
        run_timeout_override=30,
        run_no_wait_override=True,
    )
    guard_mod = run_guards.resolve_effective_run_options(
        p_args=argparse.Namespace(priority="P2", orch_timeout_sec=120, no_wait=False),
        run_priority_override="P1",
        run_timeout_override=30,
        run_no_wait_override=True,
    )
    assert guard_run == guard_mod

    preview_run = run_handlers._build_dry_run_preview(
        key="twinpaper",
        dispatch_mode=True,
        prompt="implement feature",
        dispatch_roles="Codex-Reviewer",
        require_verifier=True,
        verifier_roles=["Codex-Reviewer"],
        verifier_added=False,
        run_control_mode="retry",
        run_source_request_id="REQ-1",
        planning_enabled=True,
        reuse_source_plan=False,
        plan_data={"subtasks": [{"id": "S1"}]},
        plan_replans=[{"attempt": 1}],
        plan_gate_blocked=False,
        plan_error="",
        effective_priority="P1",
        effective_timeout=60,
        effective_no_wait=False,
    )
    preview_mod = run_guards.build_dry_run_preview(
        key="twinpaper",
        dispatch_mode=True,
        prompt="implement feature",
        dispatch_roles="Codex-Reviewer",
        require_verifier=True,
        verifier_roles=["Codex-Reviewer"],
        verifier_added=False,
        run_control_mode="retry",
        run_source_request_id="REQ-1",
        planning_enabled=True,
        reuse_source_plan=False,
        plan_data={"subtasks": [{"id": "S1"}]},
        plan_replans=[{"attempt": 1}],
        plan_gate_blocked=False,
        plan_error="",
        effective_priority="P1",
        effective_timeout=60,
        effective_no_wait=False,
    )
    assert preview_run == preview_mod

    sent_run: list[tuple[str, str, dict | None]] = []
    sent_mod: list[tuple[str, str, dict | None]] = []
    logged_run: list[dict] = []
    logged_mod: list[dict] = []
    common = dict(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            chat_max_running=1,
            chat_daily_cap=20,
        ),
        manager_state=_empty_state(),
        chat_id="939062873",
        key="twinpaper",
        entry={"project_alias": "O2"},
        run_auto_source="default",
        run_force_mode="dispatch",
        orch_target="O2",
        prompt="implement feature",
        summarize_chat_usage=lambda manager_state, chat_id: (1, 0),
        detect_high_risk_prompt=lambda prompt: "",
        set_confirm_action=lambda *args, **kwargs: None,
        save_manager_state=lambda path, manager_state: None,
    )
    handled_run = run_handlers._handle_run_rate_limit_and_confirm(
        send=lambda body, **kwargs: sent_run.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged_run.append(kwargs),
        **common,
    )
    handled_mod = run_guards.handle_run_rate_limit_and_confirm(
        send=lambda body, **kwargs: sent_mod.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged_mod.append(kwargs),
        **common,
    )
    assert handled_run == handled_mod == True
    assert sent_run == sent_mod
    assert logged_run == logged_mod

    policy_sent_run: list[tuple[str, str, dict | None]] = []
    policy_sent_mod: list[tuple[str, str, dict | None]] = []
    policy_run = run_handlers._enforce_dispatch_policies(
        dispatch_mode=True,
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry={"project_alias": "O2"},
        selected_roles=["Codex-Dev"],
        available_roles=["Codex-Dev", "Codex-Reviewer"],
        verifier_candidates=["Codex-Reviewer"],
        plan_gate_blocked=True,
        plan_gate_reason="critic unresolved after auto-replan",
        plan_replans=[{"attempt": 1}],
        ensure_verifier_roles=lambda **kwargs: (["Codex-Dev"], ["Codex-Reviewer"], False, ["Codex-Reviewer"]),
        dispatch_roles="Codex-Dev",
        send=lambda body, **kwargs: policy_sent_run.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    policy_mod = run_guards.enforce_dispatch_policies(
        dispatch_mode=True,
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry={"project_alias": "O2"},
        selected_roles=["Codex-Dev"],
        available_roles=["Codex-Dev", "Codex-Reviewer"],
        verifier_candidates=["Codex-Reviewer"],
        plan_gate_blocked=True,
        plan_gate_reason="critic unresolved after auto-replan",
        plan_replans=[{"attempt": 1}],
        ensure_verifier_roles=lambda **kwargs: (["Codex-Dev"], ["Codex-Reviewer"], False, ["Codex-Reviewer"]),
        dispatch_roles="Codex-Dev",
        send=lambda body, **kwargs: policy_sent_mod.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    assert policy_run == policy_mod
    assert policy_sent_run == policy_sent_mod

    confirm_state_a = _empty_state()
    confirm_state_b = copy.deepcopy(confirm_state_a)
    gw.set_confirm_action(confirm_state_a, chat_id="939062873", mode="dispatch", prompt="rm -rf /tmp/demo", risk="destructive_delete")
    gw.set_confirm_action(confirm_state_b, chat_id="939062873", mode="dispatch", prompt="rm -rf /tmp/demo", risk="destructive_delete")
    saved_a: list[Path] = []
    saved_b: list[Path] = []
    confirm_sent_a: list[tuple[str, dict]] = []
    confirm_sent_b: list[tuple[str, dict]] = []

    result_a = run_handlers.resolve_confirm_run_transition(
        cmd="confirm-run",
        args=argparse.Namespace(dry_run=False, manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json", confirm_ttl_sec=300),
        manager_state=confirm_state_a,
        chat_id="939062873",
        orch_target="O2",
        send=lambda body, **kwargs: confirm_sent_a.append((body, kwargs)) or True,
        get_confirm_action=gw.get_confirm_action,
        parse_iso_ts=gw.parse_iso_ts,
        clear_confirm_action=gw.clear_confirm_action,
        save_manager_state=lambda path, manager_state: saved_a.append(path),
    )
    result_b = run_guards.resolve_confirm_run_transition(
        cmd="confirm-run",
        args=argparse.Namespace(dry_run=False, manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json", confirm_ttl_sec=300),
        manager_state=confirm_state_b,
        chat_id="939062873",
        orch_target="O2",
        send=lambda body, **kwargs: confirm_sent_b.append((body, kwargs)) or True,
        get_confirm_action=gw.get_confirm_action,
        parse_iso_ts=gw.parse_iso_ts,
        clear_confirm_action=gw.clear_confirm_action,
        save_manager_state=lambda path, manager_state: saved_b.append(path),
    )
    assert result_a == result_b
    assert confirm_state_a == confirm_state_b
    assert confirm_sent_a == confirm_sent_b
    assert saved_a == saved_b


def test_send_dispatch_result_adds_project_quick_actions_for_confirmed_result() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    handled = run_handlers._send_dispatch_result(
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry={"project_alias": "O2"},
        p_args=argparse.Namespace(),
        prompt="dangerous but approved",
        state={"complete": True},
        req_id="REQ-1",
        task=None,
        run_control_mode="normal",
        run_source_request_id="",
        run_auto_source="confirmed",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        summarize_task_lifecycle=lambda key, task: "",
        synthesize_orchestrator_response=lambda p_args, prompt, state: "synthed",
        render_run_response=lambda state, task=None: "run result",
        finalize_request_reply_messages=lambda args, req_id: {},
    )

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "result"
    assert body == "run result"
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/todo O2" in buttons
    assert "/orch status O2" in buttons
    assert "/monitor" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons


def test_send_dispatch_result_adds_project_quick_actions_for_confirmed_synth() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    handled = run_handlers._send_dispatch_result(
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry={"project_alias": "O2"},
        p_args=argparse.Namespace(),
        prompt="dangerous but approved",
        state={"complete": True, "replies": [{"role": "Codex-Reviewer", "text": "ok"}]},
        req_id="REQ-1",
        task=None,
        run_control_mode="normal",
        run_source_request_id="",
        run_auto_source="confirmed",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        summarize_task_lifecycle=lambda key, task: "",
        synthesize_orchestrator_response=lambda p_args, prompt, state: "synthed",
        render_run_response=lambda state, task=None: "run result",
        finalize_request_reply_messages=lambda args, req_id: {},
    )

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "synth"
    assert body == "synthed"
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/todo O2" in buttons
    assert "/orch status O2" in buttons
    assert "/monitor" in buttons


def test_send_dispatch_result_adds_project_quick_actions_for_verifier_gate_failed() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    handled = run_handlers._send_dispatch_result(
        args=argparse.Namespace(require_verifier=True),
        key="twinpaper",
        entry={"project_alias": "O2"},
        p_args=argparse.Namespace(),
        prompt="needs verification",
        state={"complete": True},
        req_id="REQ-9",
        task={"stages": {"verification": "failed"}},
        run_control_mode="normal",
        run_source_request_id="",
        run_auto_source="todo-next",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        summarize_task_lifecycle=lambda key, task: "verification failed summary",
        synthesize_orchestrator_response=lambda p_args, prompt, state: "synthed",
        render_run_response=lambda state, task=None: "run result",
        finalize_request_reply_messages=lambda args, req_id: {},
    )

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "verifier-gate failed"
    assert body == "verification failed summary"
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/task REQ-9" in buttons
    assert "/replan REQ-9" in buttons
    assert "/retry REQ-9" in buttons
    assert "/todo O2" in buttons
    assert "/orch status O2" in buttons
    assert "/monitor" in buttons


def test_send_exec_critic_intervention_adds_project_quick_actions() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    run_handlers._send_exec_critic_intervention(
        entry={"project_alias": "O2"},
        key="twinpaper",
        final_req_id="REQ-7",
        verdict="retry",
        reason="critic unresolved after repair",
        exec_attempt=2,
        exec_max_attempts=3,
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )

    assert sent
    context, body, markup = sent[-1]
    assert context == "exec-critic"
    assert "exec critic: intervention needed" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/task REQ-7" in buttons
    assert "/replan REQ-7" in buttons
    assert "/retry REQ-7" in buttons
    assert "/todo O2" in buttons
    assert "/orch status O2" in buttons
    assert "/monitor" in buttons


def test_send_dispatch_exception_adds_project_quick_actions() -> None:
    sent: list[tuple[str, str, dict | None]] = []

    run_handlers._send_dispatch_exception(
        entry={"project_alias": "O2"},
        key="twinpaper",
        todo_id="TODO-001",
        reason="missing orchestrator.json",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )

    assert sent
    context, body, markup = sent[-1]
    assert context == "dispatch-exception"
    assert "dispatch failed before request start" in body
    assert "- reason: missing orchestrator.json" in body
    assert "- todo: TODO-001" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert "/monitor" in buttons
    assert "/sync preview O2 1h" in buttons
    assert "/queue" in buttons
    assert "/map" in buttons


def test_exec_results_module_matches_run_response_exports() -> None:
    entry = {"project_alias": "O2"}
    assert run_handlers._confirmed_result_reply_markup(entry, "twinpaper") == exec_results.confirmed_result_reply_markup(entry, "twinpaper")
    assert run_handlers._early_gate_reply_markup(entry, "twinpaper") == exec_results.early_gate_reply_markup(entry, "twinpaper")
    assert run_handlers._intervention_reply_markup(entry, "twinpaper", "REQ-9") == exec_results.intervention_reply_markup(entry, "twinpaper", "REQ-9")

    sent_run: list[tuple[str, str, dict | None]] = []
    sent_mod: list[tuple[str, str, dict | None]] = []
    run_handlers._send_exec_critic_intervention(
        entry=entry,
        key="twinpaper",
        final_req_id="REQ-7",
        verdict="retry",
        reason="critic unresolved after repair",
        exec_attempt=2,
        exec_max_attempts=3,
        send=lambda body, **kwargs: sent_run.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    exec_results.send_exec_critic_intervention(
        entry=entry,
        key="twinpaper",
        final_req_id="REQ-7",
        verdict="retry",
        reason="critic unresolved after repair",
        exec_attempt=2,
        exec_max_attempts=3,
        send=lambda body, **kwargs: sent_mod.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    assert sent_run == sent_mod

    exc_run: list[tuple[str, str, dict | None]] = []
    exc_mod: list[tuple[str, str, dict | None]] = []
    run_handlers._send_dispatch_exception(
        entry=entry,
        key="twinpaper",
        todo_id="TODO-001",
        reason="missing orchestrator.json",
        send=lambda body, **kwargs: exc_run.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    exec_results.send_dispatch_exception(
        entry=entry,
        key="twinpaper",
        todo_id="TODO-001",
        reason="missing orchestrator.json",
        send=lambda body, **kwargs: exc_mod.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
    )
    assert exc_run == exc_mod

    result_run: list[tuple[str, str, dict | None]] = []
    result_mod: list[tuple[str, str, dict | None]] = []
    log_run: list[dict] = []
    log_mod: list[dict] = []
    common = dict(
        args=argparse.Namespace(require_verifier=False),
        key="twinpaper",
        entry=entry,
        p_args=argparse.Namespace(),
        prompt="dangerous but approved",
        state={"complete": True, "replies": [{"role": "Codex-Reviewer", "text": "ok"}]},
        req_id="REQ-1",
        task=None,
        run_control_mode="normal",
        run_source_request_id="",
        run_auto_source="confirmed",
        summarize_task_lifecycle=lambda key, task: "",
        synthesize_orchestrator_response=lambda p_args, prompt, state: "synthed",
        render_run_response=lambda state, task=None: "run result",
        finalize_request_reply_messages=lambda args, req_id: {},
    )
    handled_run = run_handlers._send_dispatch_result(
        send=lambda body, **kwargs: result_run.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: log_run.append(kwargs),
        **common,
    )
    handled_mod = exec_results.send_dispatch_result(
        send=lambda body, **kwargs: result_mod.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: log_mod.append(kwargs),
        **common,
    )
    assert handled_run == handled_mod == True
    assert result_run == result_mod
    assert log_run == log_mod


def test_handle_run_or_unknown_command_sends_dispatch_exception_and_returns_true(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [
                    {"id": "TODO-001", "summary": "open row", "priority": "P1", "status": "open"},
                ],
                "pending_todo": {"todo_id": "TODO-001", "chat_id": "939062873", "selected_at": "2026-03-07T00:00:00+0900"},
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    saved: list[Path] = []

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=False,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=False,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="open row",
        rest="open row",
        orch_target="O2",
        run_prompt="open row",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="todo-next",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task={"todo_id": "TODO-001"},
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saved.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: {},
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("missing orchestrator.json")),
            create_request_id=lambda: "REQ-EXC",
            ensure_task_record=lambda **kwargs: {},
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=lambda *args, **kwargs: None,
            set_chat_selected_task_ref=lambda *args, **kwargs: None,
            now_iso=lambda: "2026-03-07T01:00:00+0900",
            sync_task_lifecycle=lambda **kwargs: None,
            lifecycle_set_stage=lambda *args, **kwargs: None,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert sent
    context, body, markup = sent[-1]
    assert context == "dispatch-exception"
    assert "missing orchestrator.json" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/orch status O2" in buttons
    assert "/todo O2" in buttons
    assert manager_state["projects"]["twinpaper"]["todos"][0]["status"] == "blocked"
    assert "pending_todo" not in manager_state["projects"]["twinpaper"]
    assert saved
    assert any(evt.get("event") == "dispatch_failed" and evt.get("error_code") == "E_DISPATCH" for evt in logged)


def test_handle_run_or_unknown_command_materializes_provisional_task_before_plan_gate(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    saved: list[Path] = []

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="plan it",
        rest="plan it",
        orch_target="O2",
        run_prompt="plan it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saved.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (
                kwargs.get("report_progress") and kwargs["report_progress"](phase="planner", detail="phase1 round 1/3 provider=codex")
            ) or {
                "plan_data": {"summary": "blocked", "subtasks": []},
                "plan_critic": {"approved": False, "issues": [{"issue": "missing acceptance"}], "recommendations": []},
                "plan_roles": ["Codex-Dev"],
                "plan_replans": [{"attempt": 1}],
                "plan_error": "",
                "plan_gate_blocked": True,
                "plan_gate_reason": "missing acceptance",
                "plan_review_count": 3,
                "plan_issue_codes": ["acceptance_gap"],
                "plan_issue_history": [
                    {
                        "round": 1,
                        "review_pass": "contract",
                        "status": "issues",
                        "primary_issue": "missing acceptance",
                        "issue_codes": ["acceptance_gap"],
                        "issue_count": 1,
                        "provider": "codex",
                    },
                    {
                        "round": 2,
                        "review_pass": "execution",
                        "status": "issues",
                        "primary_issue": "missing acceptance",
                        "issue_codes": ["acceptance_gap"],
                        "issue_count": 1,
                        "provider": "claude",
                    },
                    {
                        "round": 3,
                        "review_pass": "verification",
                        "status": "issues",
                        "primary_issue": "missing acceptance",
                        "issue_codes": ["acceptance_gap"],
                        "issue_count": 1,
                        "provider": "codex",
                    },
                ],
                "plan_convergence_status": "stalled",
                "plan_stalled_reason": "missing acceptance",
                "plan_last_round": 3,
                "phase1_mode": "ensemble",
                "phase1_rounds": 3,
                "phase1_providers": ["codex", "claude"],
            },
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run")),
            create_request_id=lambda: "REQ-PLAN",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-12T23:40:00+0900",
            sync_task_lifecycle=lambda **kwargs: None,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-PLAN"]
    assert task["request_id"] == "REQ-PLAN"
    assert task["tf_phase"] == "blocked"
    assert task["tf_phase_reason"] == "missing acceptance"
    assert task["status"] == "failed"
    assert task["phase1_mode"] == "ensemble"
    assert task["phase1_rounds"] == 3
    assert task["phase1_providers"] == ["codex", "claude"]
    assert task["phase1_current_phase"] == "planner"
    assert task["phase1_current_round"] == 1
    assert task["phase1_current_total_rounds"] == 3
    assert task["phase1_current_provider"] == "codex"
    assert task["phase1_candidate_roles"] == ["Codex-Dev"]
    assert task["plan_gate_reason"] == "missing acceptance"
    assert task["plan_review_count"] == 3
    assert task["plan_issue_codes"] == ["acceptance_gap"]
    assert task["plan_issue_history"][-1]["review_pass"] == "verification"
    assert task["plan_convergence_status"] == "stalled"
    assert task["plan_stalled_reason"] == "missing acceptance"
    assert task["plan_last_round"] == 3
    assert task["stages"]["planning"] == "failed"
    assert task["stages"]["close"] == "failed"
    assert manager_state["projects"]["twinpaper"]["last_request_id"] == "REQ-PLAN"
    assert sent[-1][0] == "planning-gate"
    planning_evt = next(evt for evt in logged if evt.get("event") == "planning_planner")
    assert planning_evt["project"] == "twinpaper"
    assert planning_evt["request_id"] == "REQ-PLAN"
    assert planning_evt["task_short_id"] == task["short_id"]
    assert saved


def test_handle_run_or_unknown_command_preserves_plan_on_dispatch_failure(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    saved: list[Path] = []

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saved.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: ["Codex-Dev"],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (
                kwargs.get("report_progress") and kwargs["report_progress"](phase="planner", detail="phase1 round 1/3 provider=codex")
            ) or {
                "plan_data": {
                    "summary": "ready",
                    "subtasks": [
                        {
                            "id": "S1",
                            "title": "Do work",
                            "goal": "execute the task",
                            "owner_role": "Codex-Dev",
                            "acceptance": ["deliver the requested artifact"],
                        }
                    ],
                },
                "plan_critic": {"approved": True, "issues": [], "recommendations": []},
                "plan_roles": ["Codex-Dev"],
                "plan_replans": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
                "plan_error": "",
                "plan_gate_blocked": False,
                "plan_gate_reason": "",
                "plan_review_count": 3,
                "plan_issue_codes": [],
                "plan_issue_history": [
                    {
                        "round": 1,
                        "review_pass": "contract",
                        "status": "approved",
                        "primary_issue": "",
                        "issue_codes": [],
                        "issue_count": 0,
                        "provider": "codex",
                    },
                    {
                        "round": 2,
                        "review_pass": "execution",
                        "status": "approved",
                        "primary_issue": "",
                        "issue_codes": [],
                        "issue_count": 0,
                        "provider": "claude",
                    },
                    {
                        "round": 3,
                        "review_pass": "verification",
                        "status": "approved",
                        "primary_issue": "",
                        "issue_codes": [],
                        "issue_count": 0,
                        "provider": "codex",
                    },
                ],
                "plan_convergence_status": "ready",
                "plan_stalled_reason": "",
                "plan_last_round": 3,
                "phase1_mode": "ensemble",
                "phase1_rounds": 3,
                "phase1_providers": ["codex", "claude"],
            },
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("aoe-orch run dry-run failed: [ERROR] no target roles found for orchestration")
            ),
            create_request_id=lambda: "REQ-DISPATCH-FAIL",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-31T00:10:00+0900",
            sync_task_lifecycle=lambda **kwargs: None,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DISPATCH-FAIL"]
    assert task["status"] == "failed"
    assert task["phase1_mode"] == "ensemble"
    assert task["phase1_rounds"] == 3
    assert task["plan_review_count"] == 3
    assert task["plan_convergence_status"] == "ready"
    assert task["plan_last_round"] == 3
    assert task["plan_issue_history"][-1]["review_pass"] == "verification"
    assert task["plan"]["subtasks"][0]["owner_role"] == "Codex-Dev"
    assert any(evt.get("event") == "dispatch_failed" and evt.get("error_code") == "E_DISPATCH" for evt in logged)
    assert sent[-1][0] == "dispatch-exception"
    assert saved


def test_handle_run_or_unknown_command_blocks_incomplete_data_contract_before_planning(tmp_path: Path) -> None:
    project_root = tmp_path / "DataDemo"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "default": {
                "name": "default",
                "display_name": "default",
                "project_alias": "O1",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    saved: list[Path] = []

    def _phase1_ensemble_planning(*args, **kwargs):
        raise AssertionError("planner must not run when the request contract is incomplete")

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="월별 집계 CSV를 정규화하고 스키마 체크, null 요약, 샘플 5행을 함께 남겨줘.",
        rest="월별 집계 CSV를 정규화하고 스키마 체크, null 요약, 샘플 5행을 함께 남겨줘.",
        orch_target="O1",
        run_prompt="월별 집계 CSV를 정규화하고 스키마 체크, null 요약, 샘플 5행을 함께 남겨줘.",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saved.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["DataEngineer"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["DataEngineer", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=_phase1_ensemble_planning,
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "default",
                manager_state["projects"]["default"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="DataEngineer,Codex-Reviewer",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run")),
            create_request_id=lambda: "REQ-DATA-BLOCK",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-30T15:00:00+0900",
            sync_task_lifecycle=lambda **kwargs: None,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert sent[-1][0] == "contract-incomplete"
    assert "source_path" in sent[-1][1]
    assert "target_column" in sent[-1][1]
    task = manager_state["projects"]["default"]["tasks"]["REQ-DATA-BLOCK"]
    assert task["request_contract_type"] == "data"
    assert task["request_contract_status"] == "incomplete"
    assert task["request_contract_missing_fields"] == [
        "source_path",
        "target_column",
        "accepted_input_formats",
        "normalize_to",
    ]
    assert task["execution_brief_status"] == "underspecified"
    assert task["execution_brief_blocked_slice"] == [
        "source_path",
        "target_column",
        "accepted_input_formats",
        "normalize_to",
    ]
    assert task["tf_phase"] == "blocked"
    assert task["stages"]["planning"] == "failed"
    assert task["stages"]["close"] == "failed"
    assert any(evt.get("event") == "contract_incomplete" for evt in logged)
    assert saved


def test_handle_run_or_unknown_command_reuses_provisional_request_id_for_dispatch(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    metadata_seen: list[dict] = []

    def _run_aoe_orch(_p_args, _prompt, **kwargs):
        metadata = dict(kwargs.get("metadata") or {})
        metadata_seen.append(metadata)
        req_id = str(metadata.get("request_id", "")).strip()
        return {
            "request_id": req_id,
            "complete": True,
            "roles": ["Codex-Dev"],
            "role_states": [{"role": "Codex-Dev", "status": "done"}],
            "replies": [{"role": "Codex-Dev", "text": "done"}],
            "counts": {"assignments": 1, "replies": 1},
            "done_roles": ["Codex-Dev"],
            "failed_roles": [],
            "pending_roles": [],
        }

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: ["Codex-Dev"],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: {
                "plan_data": {"summary": "ready", "subtasks": [{"id": "S1", "owner_role": "Codex-Dev", "title": "Implement", "goal": "do it", "acceptance": ["done"]}]},
                "plan_critic": {"approved": True, "issues": [], "recommendations": []},
                "plan_roles": ["Codex-Dev"],
                "plan_replans": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
                "plan_error": "",
                "plan_gate_blocked": False,
                "plan_gate_reason": "",
                "phase1_mode": "ensemble",
                "phase1_rounds": 3,
                "phase1_providers": ["codex", "claude"],
            },
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=_run_aoe_orch,
            create_request_id=lambda: "REQ-DISPATCH",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-12T23:50:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert metadata_seen
    assert metadata_seen[0]["request_id"] == "REQ-DISPATCH"
    tasks = manager_state["projects"]["twinpaper"]["tasks"]
    assert list(tasks.keys()) == ["REQ-DISPATCH"]
    task = tasks["REQ-DISPATCH"]
    assert task["request_id"] == "REQ-DISPATCH"
    assert task["status"] == "completed"
    assert task["tf_phase"] == "completed"
    assert any(row[0] in {"result", "synth"} for row in sent)


def test_handle_run_or_unknown_command_no_wait_detaches_after_provisional_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []
    saves: list[Path] = []
    detached: dict[str, Any] = {}
    daemon: dict[str, Any] = {}

    def _fake_start_background_dispatch_flow(*, name: str, target):
        detached["name"] = name
        detached["target"] = target
        detached["started"] = True
        return object()

    monkeypatch.setattr(run_handlers, "_start_background_dispatch_flow", _fake_start_background_dispatch_flow)
    monkeypatch.setattr(
        run_detached_flow,
        "ensure_local_background_daemon",
        lambda **kwargs: daemon.update(kwargs) or {"started": True, "thread_name": "aoe-local-bg-test", "runner_target": "local_background"},
    )

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: logged.append(kwargs),
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: saves.append(path),
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("planning should be detached when --no-wait is set")
            ),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: "REQ-DETACHED",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-13T18:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert detached == {}
    assert daemon["runner_target"] == "local_background"
    assert daemon["launch_mode"] == "detached_no_wait"
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-DETACHED"]
    assert task["request_id"] == "REQ-DETACHED"
    assert task["status"] == "running"
    assert task["tf_phase"] == "planning"
    assert task["roles"] == ["Codex-Dev"]
    assert task["phase1_role_preset"] == "build"
    assert task["phase2_team_preset"] == "build"
    assert task["phase1_current_phase"] == "planner"
    assert task["phase1_current_round"] == 1
    assert task["phase1_current_total_rounds"] == 3
    assert task["background_run_status"] == "queued"
    assert task["background_run_runner_target"] == "local_background"
    assert task["background_run_launch_mode"] == "detached_no_wait"
    assert task["background_run_source_surface"] == "run_no_wait"
    assert task["background_run_request_id"] == "REQ-DETACHED"
    assert task["background_run_project_key"] == "twinpaper"
    assert task["background_run_execution_brief_status"] == "executable"
    assert task["background_run_ticket_id"].startswith("BGT-REQ-DETACHED-")
    assert task["result"]["background_run_status"] == "queued"
    assert task["result"]["background_run_runner_target"] == "local_background"
    assert task["result"]["background_run_ticket_id"].startswith("BGT-REQ-DETACHED-")
    queue_file = team_dir / "background_runs.json"
    assert queue_file.exists()
    queue_state = json.loads(queue_file.read_text(encoding="utf-8"))
    rows = queue_state.get("runs") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["ticket_id"].startswith("BGT-REQ-DETACHED-")
    assert row["status"] == "queued"
    assert row["runner_target"] == "local_background"
    assert row["launch_mode"] == "detached_no_wait"
    assert row["source_surface"] == "run_no_wait"
    assert row["request_id"] == "REQ-DETACHED"
    assert row["project_key"] == "twinpaper"
    assert manager_state["projects"]["twinpaper"]["last_request_id"] == "REQ-DETACHED"
    assert saves
    assert sent
    context, body, markup = sent[-1]
    assert context == "planning-accepted"
    assert "accepted: T-001" in body
    assert "status: planning" in body
    assert "/task T-001" in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/task T-001" in buttons
    assert "/monitor" in buttons
    assert "/offdesk review O2" in buttons
    assert any(evt.get("event") == "dispatch_detached" for evt in logged)


def test_background_run_ticket_helpers_advance_queue_state(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    launch_spec = build_background_launch_spec(
        request_id="REQ-DETACHED",
        project_key="twinpaper",
        project_root=str(tmp_path / "project"),
        team_dir=str(tmp_path),
        manager_state_file=str(tmp_path / "orch_manager_state.json"),
        runner_target="local_background",
        launch_mode="detached_no_wait",
        source_surface="run_no_wait",
        created_by="telegram:939062873",
        argv=["run", "--no-wait"],
        env_keys=["AOE_TEAM_DIR", "AOE_STATE_DIR"],
        externalizable=False,
    )
    seeded = upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-REQ-DETACHED-001",
            request_id="REQ-DETACHED",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
            launch_spec=launch_spec,
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    assert seeded["status"] == "queued"

    claimed = claim_background_run_ticket(
        queue_file,
        "BGT-REQ-DETACHED-001",
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="local_background",
        launch_mode="detached_no_wait",
        claimed_by="thread:REQ-DETACHED",
        source_surface="run_no_wait",
    )
    assert claimed["status"] == "dispatching"
    assert claimed["evidence_bundle"] == "status=dispatching | outcome=worker_claimed"

    running = advance_background_run_ticket(
        queue_file,
        "BGT-REQ-DETACHED-001",
        now_iso=lambda: "2026-03-13T18:55:03+0900",
        status="running",
        evidence_bundle="status=running | outcome=dispatch_flow_started",
    )
    assert running["status"] == "running"

    completed = advance_background_run_ticket(
        queue_file,
        "BGT-REQ-DETACHED-001",
        now_iso=lambda: "2026-03-13T18:55:04+0900",
        status="completed",
        evidence_bundle="status=completed | outcome=dispatch_flow_returned",
        evidence_artifacts=["review_report.md"],
    )
    assert completed["status"] == "completed"
    assert completed["evidence_artifacts"] == ["review_report.md"]

    rows = load_background_runs_state(queue_file).get("runs") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["ticket_id"] == "BGT-REQ-DETACHED-001"
    assert row["status"] == "completed"
    assert row["evidence_bundle"] == "status=completed | outcome=dispatch_flow_returned"
    assert row["evidence_artifacts"] == ["review_report.md"]
    assert row["launch_spec"]["spec_id"] == "BLS-REQ-DETACHED"
    assert row["launch_spec"]["externalizable"] is False


def test_local_background_worker_claims_and_completes_ticket(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-REQ-WORKER-001",
            request_id="REQ-WORKER",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    updates: list[dict[str, Any]] = []
    queue_errors: list[str] = []

    result = run_local_background_ticket(
        queue_path=queue_file,
        ticket_id="BGT-REQ-WORKER-001",
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        run_target=lambda: "ok",
        on_ticket_update=lambda ticket: updates.append(dict(ticket)),
        on_queue_error=lambda event_name, exc: queue_errors.append(f"{event_name}:{exc}"),
        runner_target="local_background",
        launch_mode="detached_no_wait",
        claimed_by="thread:REQ-WORKER",
        source_surface="run_no_wait",
        completed_evidence_artifacts=lambda: ["review_report.md"],
    )

    assert result == "ok"
    assert queue_errors == []
    assert [row["status"] for row in updates] == ["dispatching", "running", "completed"]
    state = load_background_runs_state(queue_file)
    rows = state.get("runs") or []
    assert len(rows) == 1
    row = rows[0]
    assert row["ticket_id"] == "BGT-REQ-WORKER-001"
    assert row["status"] == "completed"
    assert row["evidence_bundle"] == "status=completed | outcome=dispatch_flow_returned"
    assert row["evidence_artifacts"] == ["review_report.md"]


def test_background_run_evidence_artifacts_from_task_uses_request_contract_paths() -> None:
    task = {
        "request_contract_required_outputs": ["review_report", "severity_rationale"],
        "request_contract_artifact_contracts": {
            "review_report": {"path": "review_report.md"},
            "severity_rationale": {"path": "review_evidence/severity_rationale.md"},
        },
    }

    artifacts = background_run_evidence_artifacts_from_task(task)

    assert artifacts == ["review_report.md", "review_evidence/severity_rationale.md"]


def test_background_run_evidence_bundle_from_task_uses_task_outcome_fields() -> None:
    task = {
        "status": "completed",
        "tf_phase": "completed",
        "result": {
            "complete": True,
            "verdict": "success",
        },
    }

    bundle = background_run_evidence_bundle_from_task(task)

    assert bundle == "status=completed | outcome=dispatch_completed | phase=completed | complete=true | verdict=success"


def test_background_run_queue_claims_next_matching_ticket(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    for ticket_id, runner_target, status in [
        ("BGT-001", "github_runner", "queued"),
        ("BGT-002", "local_background", "running"),
        ("BGT-003", "local_background", "queued"),
        ("BGT-004", "local_background", "queued"),
    ]:
        upsert_background_run_ticket(
            queue_file,
            build_background_run_ticket(
                ticket_id=ticket_id,
                request_id=ticket_id.replace("BGT", "REQ"),
                project_key="twinpaper",
                execution_brief_status="executable",
                runner_target=runner_target,
                launch_mode="detached_no_wait",
                created_at="2026-03-13T18:55:00+0900",
                created_by="telegram:939062873",
                source_surface="run_no_wait",
                status=status,
            ),
            now_iso=lambda: "2026-03-13T18:55:01+0900",
        )

    queued = list_background_run_tickets(queue_file, statuses=["queued"], runner_target="local_background")
    assert [row["ticket_id"] for row in queued] == ["BGT-003", "BGT-004"]

    claimed = claim_next_background_run_ticket(
        queue_file,
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="local_background",
        launch_mode="detached_no_wait",
        claimed_by="worker:local_background",
        source_surface="background_queue",
    )

    assert claimed["ticket_id"] == "BGT-003"
    assert claimed["status"] == "dispatching"
    state = load_background_runs_state(queue_file)
    rows = {row["ticket_id"]: row for row in (state.get("runs") or [])}
    assert rows["BGT-003"]["status"] == "dispatching"
    assert rows["BGT-004"]["status"] == "queued"
    assert rows["BGT-001"]["status"] == "queued"


def test_background_run_external_runner_requires_externalizable_launch_spec(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-EXT-001",
            request_id="REQ-EXT-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="github_runner",
            launch_mode="offdesk_manual",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
            launch_spec=build_background_launch_spec(
                request_id="REQ-EXT-001",
                project_key="twinpaper",
                runner_target="github_runner",
                launch_mode="offdesk_manual",
                source_surface="run_no_wait",
                created_by="telegram:939062873",
                mode="in_process_callback",
                externalizable=False,
            ),
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    claimed = claim_background_run_ticket(
        queue_file,
        "BGT-EXT-001",
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="github_runner",
        launch_mode="offdesk_manual",
        claimed_by="worker:github_runner",
        source_surface="background_queue",
    )

    assert claimed["ticket_id"] == "BGT-EXT-001"
    assert claimed["status"] == "failed"
    assert claimed["evidence_bundle"] == "status=failed | reason=launch_spec_not_externalizable"


def test_background_run_external_runner_claims_when_launch_spec_is_externalizable(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-EXT-OK-001",
            request_id="REQ-EXT-OK-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="github_runner",
            launch_mode="offdesk_manual",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
            launch_spec=build_background_launch_spec(
                request_id="REQ-EXT-OK-001",
                project_key="twinpaper",
                runner_target="github_runner",
                launch_mode="offdesk_manual",
                source_surface="run_no_wait",
                created_by="telegram:939062873",
                mode="subprocess_json",
                entrypoint="aoe-background-worker",
                externalizable=True,
                blocked_reason="",
            ),
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    claimed = claim_background_run_ticket(
        queue_file,
        "BGT-EXT-OK-001",
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="github_runner",
        launch_mode="offdesk_manual",
        claimed_by="worker:github_runner",
        source_surface="background_queue",
    )

    assert claimed["ticket_id"] == "BGT-EXT-OK-001"
    assert claimed["status"] == "dispatching"
    assert claimed["evidence_bundle"] == "status=dispatching | outcome=worker_claimed"


def test_select_background_runner_target_defaults_to_local_background() -> None:
    assert select_background_runner_target() == "local_background"
    assert select_background_runner_target(preferred_runner_target="github_runner") == "local_background"
    assert select_background_runner_target(
        launch_spec=build_background_launch_spec(
            request_id="REQ-SEL-001",
            project_key="twinpaper",
            runner_target="local_background",
            externalizable=False,
        )
    ) == "local_background"


def test_select_background_runner_target_allows_externalizable_local_tmux_only() -> None:
    tmux_spec = build_local_tmux_background_launch_spec(
        request_id="REQ-SEL-002",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        launch_mode="offdesk_manual",
        source_surface="offdesk_review",
        created_by="telegram:939062873",
    )
    github_spec = build_github_runner_background_launch_spec(
        request_id="REQ-SEL-003",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        launch_mode="offdesk_manual",
        source_surface="offdesk_review",
        created_by="telegram:939062873",
    )

    assert select_background_runner_target(
        preferred_runner_target="local_tmux",
        launch_spec=tmux_spec,
    ) == "local_tmux"
    assert select_background_runner_target(
        preferred_runner_target="github_runner",
        launch_spec=github_spec,
    ) == "local_background"
    assert select_background_runner_target(
        preferred_runner_target="github_runner",
        launch_spec=github_spec,
        allow_external_targets=True,
    ) == "github_runner"


def test_local_tmux_launch_spec_can_embed_command_payload() -> None:
    spec = build_local_tmux_background_launch_spec(
        request_id="REQ-TMUX-001",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        launch_mode="offdesk_manual",
        source_surface="offdesk_review",
        created_by="telegram:939062873",
        command_argv=["python3", "-c", "print('ok')"],
        command_cwd="/tmp/twinpaper",
    )

    assert spec["command_argv"] == ["python3", "-c", "print('ok')"]
    assert spec["command_cwd"] == "/tmp/twinpaper"


def test_build_gateway_simulation_command_argv_uses_gateway_entrypoint() -> None:
    argv = build_gateway_simulation_command_argv(
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        simulate_text="/retry T-101",
        simulate_chat_id="local-bg",
    )

    assert argv[0]
    assert argv[1] == gateway_cli_entrypoint_path()
    assert "--project-root" in argv
    assert "/tmp/twinpaper" in argv
    assert "--team-dir" in argv
    assert "/tmp/twinpaper/.aoe-team" in argv
    assert "--manager-state-file" in argv
    assert "/tmp/twinpaper/.aoe-team/orch_manager_state.json" in argv
    assert "--simulate-live" in argv
    assert "--simulate-chat-id" in argv
    assert "local-bg" in argv
    assert "--simulate-text" in argv
    assert "/retry T-101" in argv


def test_build_local_tmux_gateway_command_launch_spec_embeds_gateway_payload() -> None:
    spec = build_local_tmux_gateway_command_launch_spec(
        request_id="REQ-TMUX-CLI-001",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        command_text="/retry T-101 lane L1",
        simulate_chat_id="local-bg",
        launch_mode="offdesk_manual",
        source_surface="offdesk_review",
        created_by="telegram:939062873",
    )

    assert spec["runner_target"] == "local_tmux"
    assert spec["externalizable"] is True
    assert spec["command_cwd"] == "/tmp/twinpaper"
    assert spec["command_argv"][1] == gateway_cli_entrypoint_path()
    assert "--simulate-text" in spec["command_argv"]
    assert "/retry T-101 lane L1" in spec["command_argv"]


def test_build_gateway_run_command_text_preserves_dispatch_options() -> None:
    command_text = build_gateway_run_command_text(
        prompt="run it",
        orch_target="O2",
        roles=["Codex-Dev", "Codex-Reviewer"],
        priority="P1",
        timeout_sec=300,
        force_mode="dispatch",
    )

    assert command_text == "aoe orch run --orch O2 --dispatch --roles Codex-Dev,Codex-Reviewer --priority P1 --timeout-sec 300 'run it'"


def test_build_local_tmux_gateway_run_launch_spec_embeds_run_cli_payload() -> None:
    spec = build_local_tmux_gateway_run_launch_spec(
        request_id="REQ-TMUX-RUN-001",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        orch_target="O2",
        prompt="run it",
        roles=["Codex-Dev"],
        priority="P2",
        timeout_sec=120,
        force_mode="dispatch",
        simulate_chat_id="local-bg",
        launch_mode="detached_no_wait",
        source_surface="run_no_wait",
        created_by="telegram:939062873",
    )

    assert spec["runner_target"] == "local_tmux"
    assert spec["externalizable"] is True
    assert spec["command_cwd"] == "/tmp/twinpaper"
    assert "--simulate-text" in spec["command_argv"]
    assert "aoe orch run --orch O2 --dispatch --roles Codex-Dev --priority P2 --timeout-sec 120 'run it'" in spec["command_argv"]


def test_build_external_runner_gateway_command_launch_spec_embeds_gateway_payload() -> None:
    spec = build_external_runner_gateway_command_launch_spec(
        runner_target="github_runner",
        request_id="REQ-GHA-001",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        command_text="/retry T-101 lane L1",
        simulate_chat_id="gha-bg",
        launch_mode="dashboard_retry",
        source_surface="dashboard_retry",
        created_by="dashboard:control",
    )

    assert spec["runner_target"] == "github_runner"
    assert spec["mode"] == "github_action_json"
    assert spec["externalizable"] is True
    assert spec["command_cwd"] == "/tmp/twinpaper"
    assert spec["command_argv"][1] == gateway_cli_entrypoint_path()
    assert "/retry T-101 lane L1" in spec["command_argv"]


def test_emit_external_background_handoff_writes_manifest(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    team_dir = tmp_path
    ticket = build_background_run_ticket(
        ticket_id="BGT-GHA-001",
        request_id="REQ-GHA-001",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target="github_runner",
        launch_mode="dashboard_retry",
        created_at="2026-04-07T10:00:00+0900",
        created_by="dashboard:control",
        source_surface="dashboard_retry",
        status="queued",
        launch_spec=build_external_runner_gateway_command_launch_spec(
            runner_target="github_runner",
            request_id="REQ-GHA-001",
            project_key="twinpaper",
            project_root=str(tmp_path),
            team_dir=str(team_dir),
            manager_state_file=str(team_dir / "orch_manager_state.json"),
            command_text="/retry T-101",
            simulate_chat_id="gha-bg",
            launch_mode="dashboard_retry",
            source_surface="dashboard_retry",
            created_by="dashboard:control",
        ),
    )
    upsert_background_run_ticket(queue_file, ticket, now_iso=lambda: "2026-04-07T10:00:01+0900")

    launched = emit_external_background_handoff(
        queue_path=queue_file,
        ticket_id="BGT-GHA-001",
        runner_target="github_runner",
        now_iso=lambda: "2026-04-07T10:00:02+0900",
        claimed_by="dashboard:control",
        source_surface="dashboard_retry",
        launch_mode="dashboard_retry",
    )

    assert launched["status"] == "running"
    assert launched["runner_target"] == "github_runner"
    handoff_rel = launched["runtime_handle"]
    assert handoff_rel.endswith("github-runner-bgt-gha-001.json")
    assert launched["runtime_summary"] == f"github_runner_handoff={handoff_rel}"
    handoff_path = external_background_handoff_path(team_dir, "BGT-GHA-001", "github_runner")
    assert handoff_path.exists()
    payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert payload["runner_target"] == "github_runner"
    assert payload["ticket_id"] == "BGT-GHA-001"
    assert payload["launch_spec"]["externalizable"] is True
    assert handoff_rel in (launched.get("evidence_artifacts") or [])


def test_poll_external_background_tickets_marks_completed_from_result_file(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    team_dir = tmp_path
    ticket = build_background_run_ticket(
        ticket_id="BGT-REMOTE-001",
        request_id="REQ-REMOTE-001",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target="remote_worker",
        launch_mode="dashboard_retry",
        created_at="2026-04-07T10:10:00+0900",
        created_by="dashboard:control",
        source_surface="dashboard_retry",
        status="running",
        runtime_handle="background_run_handoffs/remote-worker-bgt-remote-001.json",
        runtime_summary="remote_worker_handoff=background_run_handoffs/remote-worker-bgt-remote-001.json",
        evidence_artifacts=["background_run_handoffs/remote-worker-bgt-remote-001.json"],
    )
    upsert_background_run_ticket(queue_file, ticket, now_iso=lambda: "2026-04-07T10:10:01+0900")
    result_path = external_background_result_path(team_dir, "BGT-REMOTE-001", "remote_worker")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "ticket_id": "BGT-REMOTE-001",
                "status": "completed",
                "summary": "remote worker completed retry",
                "evidence_artifacts": ["reports/external-summary.md"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = poll_external_background_tickets(
        queue_path=queue_file,
        now_iso=lambda: "2026-04-07T10:10:02+0900",
    )

    assert result["changed"] is True
    assert result["completed_count"] == 1
    updated = load_background_runs_state(queue_file)["runs"][0]
    assert updated["status"] == "completed"
    assert updated["runner_target"] == "remote_worker"
    assert updated["runtime_handle"] == "background_run_handoffs/remote-worker-bgt-remote-001.json"
    assert "external_result" in updated["evidence_bundle"]
    assert "background_run_results/remote-worker-bgt-remote-001.json" in (updated.get("evidence_artifacts") or [])
    assert "reports/external-summary.md" in (updated.get("evidence_artifacts") or [])


def test_poll_external_background_tickets_records_pickup_acknowledgement(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    team_dir = tmp_path
    ticket = build_background_run_ticket(
        ticket_id="BGT-GHA-ACK-001",
        request_id="REQ-GHA-ACK-001",
        project_key="twinpaper",
        execution_brief_status="executable",
        runner_target="github_runner",
        launch_mode="dashboard_retry",
        created_at="2026-04-07T10:20:00+0900",
        created_by="dashboard:control",
        source_surface="dashboard_retry",
        status="running",
        runtime_handle="background_run_handoffs/github-runner-bgt-gha-ack-001.json",
        runtime_summary="github_runner_handoff=background_run_handoffs/github-runner-bgt-gha-ack-001.json",
        evidence_artifacts=["background_run_handoffs/github-runner-bgt-gha-ack-001.json"],
    )
    upsert_background_run_ticket(queue_file, ticket, now_iso=lambda: "2026-04-07T10:20:01+0900")
    ack_path = external_background_ack_path(team_dir, "BGT-GHA-ACK-001", "github_runner")
    ack_path.parent.mkdir(parents=True, exist_ok=True)
    ack_path.write_text(
        json.dumps(
            {
                "ticket_id": "BGT-GHA-ACK-001",
                "status": "running",
                "worker_id": "gha-runner-01",
                "summary": "workflow accepted handoff",
                "evidence_artifacts": ["logs/github-runner-start.txt"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = poll_external_background_tickets(
        queue_path=queue_file,
        now_iso=lambda: "2026-04-07T10:20:02+0900",
    )

    assert result["changed"] is True
    assert result["acknowledged_count"] == 1
    updated = load_background_runs_state(queue_file)["runs"][0]
    assert updated["status"] == "running"
    assert "external_pickup_acknowledged" in updated["evidence_bundle"]
    assert "worker=gha-runner-01" in updated["evidence_bundle"]
    assert "ack=background_run_acks/github-runner-bgt-gha-ack-001.json" in updated["evidence_bundle"]
    assert updated["runtime_summary"].endswith("| ack=background_run_acks/github-runner-bgt-gha-ack-001.json")
    assert "background_run_acks/github-runner-bgt-gha-ack-001.json" in (updated.get("evidence_artifacts") or [])
    assert "logs/github-runner-start.txt" in (updated.get("evidence_artifacts") or [])


def test_launch_local_tmux_background_ticket_starts_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-TMUX-001",
            request_id="REQ-TMUX-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="offdesk_manual",
            created_at="2026-04-06T10:00:00+0900",
            created_by="telegram:939062873",
            source_surface="offdesk_review",
            status="queued",
            launch_spec=build_local_tmux_background_launch_spec(
                request_id="REQ-TMUX-001",
                project_key="twinpaper",
                project_root=str(tmp_path),
                team_dir=str(tmp_path / ".aoe-team"),
                manager_state_file=str(tmp_path / ".aoe-team" / "orch_manager_state.json"),
                launch_mode="offdesk_manual",
                source_surface="offdesk_review",
                created_by="telegram:939062873",
                command_argv=["python3", "-c", "print('tmux')"],
                command_cwd=str(tmp_path),
            ),
        ),
        now_iso=lambda: "2026-04-06T10:00:01+0900",
    )
    launched = {}
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: launched.update({"cmd": cmd}) or subprocess.CompletedProcess(cmd, 0, "", ""))

    result = launch_local_tmux_background_ticket(
        queue_path=queue_file,
        ticket_id="BGT-TMUX-001",
        now_iso=lambda: "2026-04-06T10:00:02+0900",
        claimed_by="worker:local_tmux",
        source_surface="background_queue",
        launch_mode="offdesk_manual",
    )

    assert result["status"] == "running"
    assert result["runtime_handle"] == build_local_tmux_session_name("BGT-TMUX-001")
    assert result["runtime_summary"] == f"tmux_session={build_local_tmux_session_name('BGT-TMUX-001')}"
    assert "tmux_session_started" in result["evidence_bundle"]
    assert build_local_tmux_session_name("BGT-TMUX-001") in result["evidence_bundle"]
    assert "background_run_logs/bgt-tmux-001.log" in result["evidence_bundle"]
    assert "background_run_logs/bgt-tmux-001.log" in (result.get("evidence_artifacts") or [])
    assert "background_run_results/bgt-tmux-001.json" in (result.get("evidence_artifacts") or [])
    assert launched["cmd"][:4] == ["tmux", "new-session", "-d", "-s"]
    assert "background_run_results" in launched["cmd"][-1]
    assert "background_run_logs" in launched["cmd"][-1]


def test_launch_local_tmux_background_ticket_fails_without_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-TMUX-002",
            request_id="REQ-TMUX-002",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="offdesk_manual",
            created_at="2026-04-06T10:00:00+0900",
            created_by="telegram:939062873",
            source_surface="offdesk_review",
            status="queued",
            launch_spec=build_local_tmux_background_launch_spec(
                request_id="REQ-TMUX-002",
                project_key="twinpaper",
                project_root=str(tmp_path),
                team_dir=str(tmp_path / ".aoe-team"),
                manager_state_file=str(tmp_path / ".aoe-team" / "orch_manager_state.json"),
                launch_mode="offdesk_manual",
                source_surface="offdesk_review",
                created_by="telegram:939062873",
            ),
        ),
        now_iso=lambda: "2026-04-06T10:00:01+0900",
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)

    result = launch_local_tmux_background_ticket(
        queue_path=queue_file,
        ticket_id="BGT-TMUX-002",
        now_iso=lambda: "2026-04-06T10:00:02+0900",
        claimed_by="worker:local_tmux",
        source_surface="background_queue",
        launch_mode="offdesk_manual",
    )

    assert result["status"] == "failed"
    assert result["evidence_bundle"] == "status=failed | reason=launch_spec_missing_command"


def test_poll_local_tmux_background_tickets_marks_completed_from_result_file(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    ticket_id = "BGT-TMUX-POLL-001"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id=ticket_id,
            request_id="REQ-TMUX-POLL-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="offdesk_manual",
            created_at="2026-04-06T10:00:00+0900",
            created_by="telegram:939062873",
            source_surface="offdesk_review",
            status="running",
            runtime_handle="aoe_bg_tmux_poll_001",
            runtime_summary="tmux_session=aoe_bg_tmux_poll_001",
        ),
        now_iso=lambda: "2026-04-06T10:00:01+0900",
    )
    result_path = local_tmux_result_path(tmp_path, ticket_id)
    log_path = local_tmux_log_path(tmp_path, ticket_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({"ticket_id": ticket_id, "exit_code": 0}) + "\n", encoding="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("tmux run ok\n", encoding="utf-8")

    result = poll_local_tmux_background_tickets(
        queue_path=queue_file,
        now_iso=lambda: "2026-04-06T10:00:02+0900",
    )

    assert result["changed"] is True
    assert result["completed_count"] == 1
    rows = load_background_runs_state(queue_file).get("runs") or []
    row = rows[0]
    assert row["status"] == "completed"
    assert row["evidence_bundle"] == "status=completed | outcome=tmux_exit_code | exit_code=0 | log=background_run_logs/bgt-tmux-poll-001.log"
    assert "background_run_results/bgt-tmux-poll-001.json" in row["evidence_artifacts"]
    assert "background_run_logs/bgt-tmux-poll-001.log" in row["evidence_artifacts"]


def test_poll_local_tmux_background_tickets_marks_failed_when_session_disappears(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    queue_file = tmp_path / "background_runs.json"
    ticket_id = "BGT-TMUX-POLL-002"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id=ticket_id,
            request_id="REQ-TMUX-POLL-002",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="offdesk_manual",
            created_at="2026-04-06T10:00:00+0900",
            created_by="telegram:939062873",
            source_surface="offdesk_review",
            status="running",
            runtime_handle="aoe_bg_tmux_poll_002",
            runtime_summary="tmux_session=aoe_bg_tmux_poll_002",
        ),
        now_iso=lambda: "2026-04-06T10:00:01+0900",
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/tmux" if name == "tmux" else None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "", ""))

    result = poll_local_tmux_background_tickets(
        queue_path=queue_file,
        now_iso=lambda: "2026-04-06T10:00:02+0900",
    )

    assert result["changed"] is True
    assert result["failed_count"] == 1
    rows = load_background_runs_state(queue_file).get("runs") or []
    row = rows[0]
    assert row["status"] == "failed"
    assert row["evidence_bundle"] == "status=failed | reason=tmux_session_missing_result"
    assert "background_run_results/bgt-tmux-poll-002.json" in row["evidence_artifacts"]
    assert "background_run_logs/bgt-tmux-poll-002.log" in row["evidence_artifacts"]


def test_sync_background_run_snapshots_from_queue_updates_task_record(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    entry = {
        "tasks": {
            "REQ-TMUX-SYNC-001": {
                "request_id": "REQ-TMUX-SYNC-001",
                "background_run_status": "running",
                "background_run_ticket_id": "BGT-OLD",
                "result": {},
            }
        }
    }
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-TMUX-SYNC-001",
            request_id="REQ-TMUX-SYNC-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_tmux",
            launch_mode="dashboard_retry",
            created_at="2026-04-06T10:00:00+0900",
            created_by="dashboard:dashboard-http",
            source_surface="dashboard_retry",
            status="completed",
            runtime_handle="aoe_bg_tmux_sync_001",
            runtime_summary="tmux_session=aoe_bg_tmux_sync_001",
            evidence_bundle="status=completed | outcome=tmux_exit_code | exit_code=0",
            evidence_artifacts=["background_run_results/bgt-tmux-sync-001.json"],
        ),
        now_iso=lambda: "2026-04-06T10:00:01+0900",
    )

    changed = orch_task_handlers._sync_background_run_snapshots_from_queue(entry, queue_file)

    assert changed is True
    task = entry["tasks"]["REQ-TMUX-SYNC-001"]
    assert task["background_run_ticket_id"] == "BGT-TMUX-SYNC-001"
    assert task["background_run_status"] == "completed"
    assert task["background_run_runtime_handle"] == "aoe_bg_tmux_sync_001"
    assert task["result"]["background_run_status"] == "completed"
    assert task["result"]["background_run_ticket_id"] == "BGT-TMUX-SYNC-001"


def test_external_runner_launch_spec_builders_are_externalizable() -> None:
    common = dict(
        request_id="REQ-EXT-BUILD",
        project_key="twinpaper",
        project_root="/tmp/twinpaper",
        team_dir="/tmp/twinpaper/.aoe-team",
        manager_state_file="/tmp/twinpaper/.aoe-team/orch_manager_state.json",
        launch_mode="offdesk_manual",
        source_surface="offdesk_review",
        created_by="telegram:939062873",
    )

    local_tmux = build_local_tmux_background_launch_spec(**common)
    github_runner = build_github_runner_background_launch_spec(**common)
    remote_worker = build_remote_worker_background_launch_spec(**common)
    generic_local_tmux = build_runner_background_launch_spec(runner_target="local_tmux", **common)

    assert local_tmux["runner_target"] == "local_tmux"
    assert local_tmux["mode"] == "tmux_session_json"
    assert local_tmux["entrypoint"] == "aoe-background-worker"
    assert local_tmux["argv"] == ["worker-run", "--runner", "local_tmux"]
    assert local_tmux["env_keys"] == ["AOE_TEAM_DIR", "AOE_STATE_DIR", "AOE_ORCH_ALIAS"]
    assert local_tmux["externalizable"] is True
    assert "blocked_reason" not in local_tmux

    assert github_runner["runner_target"] == "github_runner"
    assert github_runner["mode"] == "github_action_json"
    assert github_runner["argv"] == ["worker-run", "--runner", "github_runner"]
    assert github_runner["env_keys"] == ["AOE_TEAM_DIR", "AOE_STATE_DIR", "GITHUB_TOKEN", "GITHUB_REPOSITORY"]
    assert github_runner["externalizable"] is True

    assert remote_worker["runner_target"] == "remote_worker"
    assert remote_worker["mode"] == "remote_worker_json"
    assert remote_worker["argv"] == ["worker-run", "--runner", "remote_worker"]
    assert remote_worker["env_keys"] == ["AOE_TEAM_DIR", "AOE_STATE_DIR", "AOE_REMOTE_ENDPOINT"]
    assert remote_worker["externalizable"] is True

    assert generic_local_tmux == local_tmux


def test_background_run_queue_drain_uses_registered_handler(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-REG-001",
            request_id="REQ-REG-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    updates: list[str] = []
    ran: list[str] = []
    queue_errors: list[str] = []
    assert register_local_background_run(
        ticket_id="BGT-REG-001",
        run_target=lambda: ran.append("ran") or "ok",
        on_ticket_update=lambda ticket: updates.append(str(ticket.get("status", "")).strip()),
        on_queue_error=lambda event_name, exc: queue_errors.append(f"{event_name}:{exc}"),
        completed_evidence_artifacts=lambda: ["review_report.md"],
    ) is True

    claimed = drain_local_background_queue_once(
        queue_path=queue_file,
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="local_background",
        launch_mode="detached_no_wait",
        claimed_by="worker:local_background",
        source_surface="background_queue",
    )

    assert claimed["ticket_id"] == "BGT-REG-001"
    assert ran == ["ran"]
    assert queue_errors == []
    assert updates == ["dispatching", "running", "completed"]
    rows = {row["ticket_id"]: row for row in (load_background_runs_state(queue_file).get("runs") or [])}
    assert rows["BGT-REG-001"]["status"] == "completed"
    assert rows["BGT-REG-001"]["evidence_artifacts"] == ["review_report.md"]


def test_background_run_queue_drain_consumes_multiple_registered_tickets(tmp_path: Path) -> None:
    queue_file = tmp_path / "background_runs.json"
    for ticket_id in ["BGT-REG-101", "BGT-REG-102"]:
        upsert_background_run_ticket(
            queue_file,
            build_background_run_ticket(
                ticket_id=ticket_id,
                request_id=ticket_id.replace("BGT", "REQ"),
                project_key="twinpaper",
                execution_brief_status="executable",
                runner_target="local_background",
                launch_mode="detached_no_wait",
                created_at="2026-03-13T18:55:00+0900",
                created_by="telegram:939062873",
                source_surface="run_no_wait",
                status="queued",
            ),
            now_iso=lambda: "2026-03-13T18:55:01+0900",
        )

    ran: list[str] = []
    for ticket_id in ["BGT-REG-101", "BGT-REG-102"]:
        assert register_local_background_run(
            ticket_id=ticket_id,
            run_target=lambda token=ticket_id: ran.append(token) or "ok",
            on_ticket_update=lambda ticket: None,
            on_queue_error=lambda event_name, exc: None,
            completed_evidence_artifacts=lambda: ["review_report.md"],
        ) is True

    result = drain_local_background_queue(
        queue_path=queue_file,
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="local_background",
        launch_mode="detached_no_wait",
        claimed_by="worker:local_background",
        source_surface="background_queue",
        max_items=8,
    )

    assert result["claimed_count"] == 2
    assert result["claimed_ticket_ids"] == ["BGT-REG-101", "BGT-REG-102"]
    assert ran == ["BGT-REG-101", "BGT-REG-102"]
    rows = {row["ticket_id"]: row for row in (load_background_runs_state(queue_file).get("runs") or [])}
    assert rows["BGT-REG-101"]["status"] == "completed"
    assert rows["BGT-REG-102"]["status"] == "completed"


def test_local_background_daemon_drains_queue_and_writes_worker_state(tmp_path: Path) -> None:
    queue_file = background_runs_state_path(tmp_path)
    worker_state_file = background_worker_state_path(tmp_path)
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-DAEMON-001",
            request_id="REQ-DAEMON-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
        ),
        now_iso=lambda: "2026-03-13T18:55:01+0900",
    )

    ran: list[str] = []
    assert register_local_background_run(
        ticket_id="BGT-DAEMON-001",
        run_target=lambda: ran.append("ok") or "ok",
        on_ticket_update=lambda ticket: None,
        on_queue_error=lambda event_name, exc: None,
        completed_evidence_artifacts=lambda: ["review_report.md"],
        completed_evidence_bundle=lambda: "status=completed | outcome=daemon_drain",
    ) is True

    started = ensure_local_background_daemon(
        queue_path=queue_file,
        now_iso=lambda: "2026-03-13T18:55:02+0900",
        runner_target="local_background",
        claimed_by="daemon:test",
        source_surface="test",
        interval_sec=0.05,
        idle_sec=0.05,
        max_items=4,
    )
    assert started["runner_target"] == "local_background"

    deadline = time.time() + 2.0
    rows = {}
    while time.time() < deadline:
        rows = {row["ticket_id"]: row for row in (load_background_runs_state(queue_file).get("runs") or [])}
        if rows.get("BGT-DAEMON-001", {}).get("status") == "completed":
            break
        time.sleep(0.05)

    stopped = stop_local_background_daemon(queue_path=queue_file, wait_sec=1.0)
    assert stopped["stopped"] is True
    assert ran == ["ok"]
    assert rows["BGT-DAEMON-001"]["status"] == "completed"
    worker_state = load_background_worker_state(worker_state_file)
    worker_summary = summarize_background_worker_state(
        worker_state_file,
        now_iso=lambda: "2026-03-13T18:55:05+0900",
    )
    assert worker_state["thread_name"]
    assert int(worker_state.get("claimed_count", 0) or 0) >= 1
    assert worker_summary["status"] in {"stopped", "running", "idle"}
    assert "queue=" in worker_summary["summary"]


def test_background_run_summary_and_stale_marking(tmp_path: Path) -> None:
    queue_file = background_runs_state_path(tmp_path)
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-STALE-001",
            request_id="REQ-STALE-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:55:00+0900",
            touched_at="2026-03-13T18:55:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="running",
        ),
        now_iso=lambda: "2026-03-13T18:55:00+0900",
    )
    upsert_background_run_ticket(
        queue_file,
        build_background_run_ticket(
            ticket_id="BGT-QUEUE-001",
            request_id="REQ-QUEUE-001",
            project_key="twinpaper",
            execution_brief_status="executable",
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at="2026-03-13T18:56:00+0900",
            touched_at="2026-03-13T18:56:00+0900",
            created_by="telegram:939062873",
            source_surface="run_no_wait",
            status="queued",
        ),
        now_iso=lambda: "2026-03-13T18:56:00+0900",
    )

    before = summarize_background_runs_state(queue_file)
    assert before["depth"] == 2
    assert before["stale_count"] == 0

    marked = mark_stale_background_run_tickets(
        queue_file,
        now_iso=lambda: "2026-03-13T20:00:00+0900",
        stale_after_sec=1800,
    )
    assert marked == {"stale_count": 1, "changed": True}

    after = summarize_background_runs_state(queue_file)
    assert after["depth"] == 1
    assert after["stale_count"] == 1
    rows = {row["ticket_id"]: row for row in (load_background_runs_state(queue_file).get("runs") or [])}
    assert rows["BGT-STALE-001"]["status"] == "stale"
    assert rows["BGT-QUEUE-001"]["status"] == "queued"


@pytest.mark.parametrize(
    ("alias", "display_name", "prompt", "available_roles", "expected_roles", "expected_preset"),
    [
        (
            "O2",
            "BuildProject",
            "로그인 버그를 수정하고 회귀 리스크도 같이 검토해줘.",
            ["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
            ["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
            "build",
        ),
        (
            "O3",
            "DataProject",
            "CSV 적재 흐름의 null/스키마 문제를 점검하고 정리해줘.",
            ["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
            ["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
            "data",
        ),
        (
            "O4",
            "ReviewProject",
            "현재 변경사항을 검토하고 각각 교차검증해서 리스크를 짚어줘.",
            ["Codex-Reviewer", "Claude-Reviewer"],
            ["Codex-Reviewer", "Claude-Reviewer"],
            "review",
        ),
        (
            "O5",
            "MixedProject",
            "로그인 수정안과 handoff 문서를 함께 준비하고 회귀 리스크도 검토해줘.",
            ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "mixed",
        ),
    ],
)
def test_handle_run_or_unknown_command_no_wait_keeps_forced_dispatch_presets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias: str,
    display_name: str,
    prompt: str,
    available_roles: list[str],
    expected_roles: list[str],
    expected_preset: str,
) -> None:
    project_root = tmp_path / display_name
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            display_name.lower(): {
                "name": display_name.lower(),
                "display_name": display_name,
                "project_alias": alias,
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    sent: list[tuple[str, str, dict | None]] = []

    monkeypatch.setattr(run_handlers, "_start_background_dispatch_flow", lambda **kwargs: object())

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=False,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text=prompt,
        rest=prompt,
        orch_target=alias,
        run_prompt=prompt,
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=True,
        run_force_mode="dispatch",
        run_auto_source="default-intent",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: None,
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda path, manager_state: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=orch_roles.choose_auto_dispatch_roles,
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir, roles=available_roles: roles,
            parse_roles_csv=lambda csv: [token.strip() for token in str(csv or "").split(",") if token.strip()],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: [],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planning should be detached when --no-wait is set")),
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                display_name.lower(),
                manager_state["projects"][display_name.lower()],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("dispatch should not run inline")),
            create_request_id=lambda: f"REQ-{alias}",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-16T10:05:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=lambda **kwargs: {"verdict": "success", "reason": ""},
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    entry = manager_state["projects"][display_name.lower()]
    task = entry["tasks"][entry["last_request_id"]]
    assert task["tf_phase"] == "planning"
    assert task["roles"] == expected_roles
    assert task["phase1_role_preset"] == expected_preset
    assert task["phase2_team_preset"] == expected_preset
    assert sent
    context, body, markup = sent[-1]
    assert context == "planning-accepted"
    assert "/offdesk review " + alias in body
    buttons = [btn["text"] for row in (markup or {}).get("keyboard", []) for btn in row]
    assert "/offdesk review " + alias in buttons


def test_filter_phase2_retry_scope_limits_plan_to_target_lanes() -> None:
    plan_data = {
        "summary": "ready",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Dev", "title": "Implement", "goal": "do impl"},
            {"id": "S2", "owner_role": "Codex-Writer", "title": "Document", "goal": "write handoff"},
        ],
        "meta": {
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"group_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_groups": [
                    {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"group_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "team_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "critic_role": "Codex-Reviewer",
                "integration_role": "Codex-Reviewer",
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"lane_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"lane_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "parallel_workers": True,
                "parallel_reviews": True,
                "readonly": True,
            },
        },
    }
    filtered, scope = run_handlers._filter_phase2_retry_scope(
        plan_data=plan_data,
        run_control_mode="retry",
        run_source_task={
            "exec_critic": {
                "verdict": "retry",
                "action": "retry",
                "rerun_execution_lane_ids": ["L2"],
                "rerun_review_lane_ids": ["R2"],
            }
        },
    )

    assert filtered is not None
    meta = filtered["meta"]
    exec_plan = meta["phase2_execution_plan"]
    assert [row["lane_id"] for row in exec_plan["execution_lanes"]] == ["L2"]
    assert [row["lane_id"] for row in exec_plan["review_lanes"]] == ["R2"]
    assert [row["id"] for row in filtered["subtasks"]] == ["S2"]
    assert scope["planned_roles"] == ["Codex-Writer", "Codex-Reviewer"]


def test_filter_phase2_retry_scope_honors_operator_selected_lane_subset() -> None:
    plan_data = {
        "summary": "ready",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Dev", "title": "Implement", "goal": "do impl"},
            {"id": "S2", "owner_role": "Codex-Writer", "title": "Document", "goal": "write handoff"},
        ],
        "meta": {
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"group_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_groups": [
                    {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"group_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "team_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "critic_role": "Codex-Reviewer",
                "integration_role": "Codex-Reviewer",
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"lane_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"lane_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "parallel_workers": True,
                "parallel_reviews": True,
                "readonly": True,
            },
        },
    }

    filtered, scope = run_handlers._filter_phase2_retry_scope(
        plan_data=plan_data,
        run_control_mode="retry",
        run_source_task={
            "exec_critic": {
                "verdict": "retry",
                "action": "retry",
                "rerun_execution_lane_ids": ["L1", "L2"],
                "rerun_review_lane_ids": ["R1", "R2"],
            }
        },
        selected_execution_lane_ids=["L1"],
    )

    assert filtered is not None
    exec_plan = filtered["meta"]["phase2_execution_plan"]
    assert [row["lane_id"] for row in exec_plan["execution_lanes"]] == ["L1"]
    assert [row["lane_id"] for row in exec_plan["review_lanes"]] == ["R1"]
    assert scope["rerun_execution_lane_ids"] == ["L1"]
    assert scope["rerun_review_lane_ids"] == ["R1"]


def test_filter_phase2_retry_scope_uses_execution_only_slice_for_followup() -> None:
    plan_data = {
        "summary": "ready",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Dev", "title": "Implement", "goal": "do impl"},
            {"id": "S2", "owner_role": "Codex-Writer", "title": "Document", "goal": "write handoff"},
        ],
        "meta": {
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"group_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_groups": [
                    {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"group_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "team_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "critic_role": "Codex-Reviewer",
                "integration_role": "Codex-Reviewer",
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"lane_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"lane_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "parallel_workers": True,
                "parallel_reviews": True,
                "readonly": True,
            },
        },
    }

    filtered, scope = run_handlers._filter_phase2_retry_scope(
        plan_data=plan_data,
        run_control_mode="followup",
        run_source_task={
            "exec_critic": {
                "manual_followup_execution_lane_ids": ["L2"],
                "manual_followup_review_lane_ids": ["R2"],
            }
        },
    )

    assert filtered is not None
    exec_plan = filtered["meta"]["phase2_execution_plan"]
    assert [row["lane_id"] for row in exec_plan["execution_lanes"]] == ["L2"]
    assert exec_plan["review_lanes"] == []
    assert scope["rerun_execution_lane_ids"] == ["L2"]
    assert scope["rerun_review_lane_ids"] == []


def test_handle_run_or_unknown_command_retry_filters_phase2_dispatch_to_target_lanes(tmp_path: Path) -> None:
    project_root = tmp_path / "TwinPaper"
    team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "todos": [],
            }
        }
    }
    metadata_seen: list[dict[str, Any]] = []
    sent: list[tuple[str, str, dict | None]] = []
    critic_calls = {"count": 0}

    def _run_aoe_orch(_p_args, _prompt, **kwargs):
        metadata = copy.deepcopy(kwargs.get("metadata") or {})
        metadata_seen.append(metadata)
        req_id = str(metadata.get("request_id", "")).strip() or f"REQ-{len(metadata_seen)}"
        return {
            "request_id": req_id,
            "complete": True,
            "roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            "role_states": [
                {"role": "Codex-Dev", "status": "done"},
                {"role": "Codex-Writer", "status": "done"},
                {"role": "Codex-Reviewer", "status": "done"},
            ],
            "replies": [{"role": "Codex-Reviewer", "text": "done"}],
            "counts": {"assignments": 1, "replies": 1},
            "done_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            "failed_roles": [],
            "pending_roles": [],
        }

    def _critique_task_result(*_args, **_kwargs):
        critic_calls["count"] += 1
        if critic_calls["count"] == 1:
            return {
                "verdict": "retry",
                "action": "retry",
                "reason": "rerun writer lane only",
                "rerun_execution_lane_ids": ["L2"],
                "rerun_review_lane_ids": ["R2"],
            }
        return {"verdict": "success", "action": "none", "reason": ""}

    plan_payload = {
        "summary": "ready",
        "subtasks": [
            {"id": "S1", "owner_role": "Codex-Dev", "title": "Implement", "goal": "do impl", "acceptance": ["done"]},
            {"id": "S2", "owner_role": "Codex-Writer", "title": "Document", "goal": "write handoff", "acceptance": ["done"]},
        ],
        "meta": {
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"group_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_groups": [
                    {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"group_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "team_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "critic_role": "Codex-Reviewer",
                "integration_role": "Codex-Reviewer",
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "L1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": True},
                    {"lane_id": "L2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": True},
                ],
                "review_mode": "parallel",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"], "parallel": True},
                    {"lane_id": "R2", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"], "parallel": True},
                ],
                "parallel_workers": True,
                "parallel_reviews": True,
                "readonly": True,
            },
        },
    }

    ctx = run_handlers.build_run_context(
        cmd="run",
        args=argparse.Namespace(
            dry_run=False,
            manager_state_file=team_dir / "orch_manager_state.json",
            auto_dispatch=False,
            require_verifier=False,
            verifier_roles="",
            task_planning=True,
            plan_phase1_ensemble=True,
            plan_max_subtasks=6,
            plan_auto_replan=False,
            plan_replan_attempts=0,
            plan_block_on_critic=True,
            exec_critic=True,
            exec_critic_retry_max=3,
            chat_max_running=3,
            chat_daily_cap=20,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        text="run it",
        rest="run it",
        orch_target="O2",
        run_prompt="run it",
        run_roles_override=None,
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        run_force_mode="dispatch",
        run_auto_source="default",
        run_control_mode="normal",
        run_source_request_id="",
        run_source_task=None,
    )

    deps = run_handlers.RunDeps(
        core=run_handlers.RunCoreDeps(
            send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
            log_event=lambda **kwargs: None,
            help_text=lambda: "help",
        ),
        guard=run_handlers.RunGuardDeps(
            summarize_chat_usage=lambda manager_state, chat_id: (0, 0),
            detect_high_risk_prompt=lambda prompt: "",
            set_confirm_action=lambda *args, **kwargs: None,
            save_manager_state=lambda *args, **kwargs: None,
        ),
        planning=run_handlers.RunPlanningDeps(
            choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            resolve_verifier_candidates=lambda text: [],
            load_orchestrator_roles=lambda team_dir: ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            parse_roles_csv=lambda csv: [token for token in str(csv or "").split(",") if token],
            ensure_verifier_roles=lambda **kwargs: (kwargs.get("selected_roles", []), [], False, []),
            available_worker_roles=lambda roles: roles,
            normalize_task_plan_payload=lambda payload, **kwargs: payload or {},
            build_task_execution_plan=lambda **kwargs: {},
            critique_task_execution_plan=lambda **kwargs: {"approved": True, "issues": [], "recommendations": []},
            critic_has_blockers=lambda critic: False,
            repair_task_execution_plan=lambda **kwargs: {},
            plan_roles_from_subtasks=lambda payload: ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            build_planned_dispatch_prompt=lambda prompt, plan_data, plan_critic: prompt,
            phase1_ensemble_planning=lambda *args, **kwargs: {
                "plan_data": copy.deepcopy(plan_payload),
                "plan_critic": {"approved": True, "issues": [], "recommendations": []},
                "plan_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "plan_replans": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
                "plan_error": "",
                "plan_gate_blocked": False,
                "plan_gate_reason": "",
                "phase1_mode": "ensemble",
                "phase1_rounds": 3,
                "phase1_providers": ["codex", "claude"],
            },
        ),
        routing=run_handlers.RunRoutingDeps(
            get_context=lambda raw: (
                "twinpaper",
                manager_state["projects"]["twinpaper"],
                argparse.Namespace(
                    project_root=project_root,
                    team_dir=team_dir,
                    roles="Codex-Dev,Codex-Writer,Codex-Reviewer",
                    priority="P2",
                    orch_timeout_sec=120,
                    no_wait=False,
                ),
            ),
            run_orchestrator_direct=lambda p_args, prompt: "direct",
            run_aoe_orch=_run_aoe_orch,
            create_request_id=lambda: "REQ-RETRY",
            ensure_task_record=lambda **kwargs: gw.ensure_task_record(
                kwargs["entry"],
                kwargs["request_id"],
                kwargs["prompt"],
                kwargs["mode"],
                kwargs["roles"],
                kwargs["verifier_roles"],
                kwargs["require_verifier"],
            ),
            finalize_request_reply_messages=lambda *args, **kwargs: {},
            touch_chat_recent_task_ref=gw.touch_chat_recent_task_ref,
            set_chat_selected_task_ref=gw.set_chat_selected_task_ref,
            now_iso=lambda: "2026-03-12T23:55:00+0900",
            sync_task_lifecycle=gw.sync_task_lifecycle,
            lifecycle_set_stage=gw.lifecycle_set_stage,
            summarize_task_lifecycle=lambda key, task: "",
            synthesize_orchestrator_response=lambda p_args, prompt, state: "",
            critique_task_result=_critique_task_result,
            extract_todo_proposals=lambda *args, **kwargs: [],
            merge_todo_proposals=lambda **kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
            render_run_response=lambda state, task=None: "result",
        ),
    )

    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)

    assert handled is True
    assert len(metadata_seen) == 2
    first_plan = metadata_seen[0]["phase2_execution_plan"]
    second_plan = metadata_seen[1]["phase2_execution_plan"]
    assert [row["lane_id"] for row in first_plan["execution_lanes"]] == ["L1", "L2"]
    assert [row["lane_id"] for row in second_plan["execution_lanes"]] == ["L2"]
    assert [row["lane_id"] for row in second_plan["review_lanes"]] == ["R2"]
    task = manager_state["projects"]["twinpaper"]["tasks"]["REQ-RETRY"]
    assert task["status"] == "completed"
    assert task["tf_phase"] == "completed"
    assert any(row[0] in {"result", "synth"} for row in sent)


def test_todo_next_blocks_unready_project(tmp_path: Path) -> None:
    team_dir = tmp_path / "TwinPaper" / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(tmp_path / "TwinPaper"),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "run broken project",
                        "priority": "P1",
                        "status": "open",
                    }
                ],
                "todo_seq": 1,
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=tmp_path / "state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="twinpaper",
        rest="next",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda target: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace()),
        save_manager_state=lambda *args, **kwargs: None,
        now_iso=lambda: "2026-03-07T18:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    assert "todo next blocked: project runtime is not ready" in sent[-1]
    assert "/orch status O2" in sent[-1]


def test_todo_next_resumes_rate_limited_todo_after_retry_at(tmp_path: Path) -> None:
    team_dir = tmp_path / "TwinPaper" / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state = {
        "projects": {
            "twinpaper": {
                "name": "twinpaper",
                "display_name": "TwinPaper",
                "project_alias": "O2",
                "project_root": str(tmp_path / "TwinPaper"),
                "team_dir": str(team_dir),
                "todos": [
                    {
                        "id": "TODO-001",
                        "summary": "resume me",
                        "priority": "P1",
                        "status": "running",
                        "updated_at": "2026-03-14T00:00:00+0900",
                    },
                    {
                        "id": "TODO-002",
                        "summary": "leave later",
                        "priority": "P2",
                        "status": "open",
                    },
                ],
                "tasks": {
                    "r1": {
                        "request_id": "r1",
                        "todo_id": "TODO-001",
                        "status": "running",
                        "tf_phase": "rate_limited",
                        "rate_limit": {
                            "mode": "blocked",
                            "limited_providers": ["codex", "claude"],
                            "retry_after_sec": 180,
                            "retry_at": "2000-01-01T00:00:00+00:00",
                        },
                    }
                },
                "todo_seq": 2,
            }
        }
    }
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=tmp_path / "state.json"),
        manager_state=manager_state,
        chat_id="939062873",
        chat_role="admin",
        orch_target="twinpaper",
        rest="next",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda target: ("twinpaper", manager_state["projects"]["twinpaper"], argparse.Namespace(project_root=tmp_path / "TwinPaper", team_dir=team_dir)),
        save_manager_state=lambda *args, **kwargs: None,
        now_iso=lambda: "2026-03-14T01:00:00+0900",
    )

    assert result["terminal"] is False
    assert result["run_prompt"] == "resume me"
    assert manager_state["projects"]["twinpaper"]["pending_todo"]["todo_id"] == "TODO-001"
    assert sent
    assert "todo next resumed" in sent[-1]


def test_todo_with_explicit_other_project_under_focus_returns_operator_message(tmp_path: Path) -> None:
    state = gw.default_manager_state(tmp_path, tmp_path / ".aoe-team")
    twin_root = tmp_path / "TwinPaper"
    nano_root = tmp_path / "Nano"
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(twin_root),
        "team_dir": str(twin_root / ".aoe-team"),
        "tasks": {},
    }
    state["projects"]["nano"] = {
        "name": "nano",
        "display_name": "Nano",
        "project_alias": "O3",
        "project_root": str(nano_root),
        "team_dir": str(nano_root / ".aoe-team"),
        "tasks": {},
    }
    gw.set_project_lock(state, "twinpaper")
    sent: list[str] = []

    result = todo_handlers.handle_todo_command(
        cmd="todo",
        args=argparse.Namespace(dry_run=False, manager_state_file=tmp_path / "state.json"),
        manager_state=state,
        chat_id="939062873",
        chat_role="admin",
        orch_target=None,
        rest="O3 next",
        send=lambda body, **kwargs: sent.append(body) or True,
        get_context=lambda raw_target: (
            lambda k, e: (k, e, argparse.Namespace(project_root=Path(e["project_root"]), team_dir=Path(e["team_dir"])))
        )(*gw.get_manager_project(state, raw_target)),
        save_manager_state=lambda *args, **kwargs: None,
        now_iso=lambda: "2026-03-07T18:05:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    assert "todo blocked by project lock" in sent[-1]
    assert "/focus off" in sent[-1]


def test_cleanup_terminal_todo_gate_blocks_pending_todo_and_clears_pending() -> None:
    entry = {
        "todos": [
            {
                "id": "TODO-001",
                "summary": "broken queued item",
                "priority": "P2",
                "status": "open",
            }
        ],
        "pending_todo": {
            "todo_id": "TODO-001",
            "chat_id": "939062873",
            "selected_at": "2026-03-06T23:43:25+0900",
        },
    }

    changed = run_handlers._cleanup_terminal_todo_gate(
        entry=entry,
        chat_id="939062873",
        todo_id="",
        pending_todo_used=False,
        run_auto_source="todo:next",
        reason="plan gate: critic unresolved after auto-replan",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
    )

    todo = entry["todos"][0]
    assert changed is True
    assert "pending_todo" not in entry
    assert todo["status"] == "blocked"
    assert todo["blocked_reason"] == "plan gate: critic unresolved after auto-replan"
    assert todo["updated_at"] == "2026-03-07T00:00:00+0900"


def test_finalize_todo_after_run_increments_blocked_count_and_clears_it_on_success() -> None:
    entry = {
        "todos": [
            {
                "id": "TODO-001",
                "summary": "blocked row",
                "priority": "P1",
                "status": "blocked",
                "blocked_count": 2,
                "blocked_reason": "old plan gate",
            }
        ]
    }

    run_handlers._finalize_todo_after_run(
        entry=entry,
        todo_id="TODO-001",
        status="failed",
        exec_verdict="retry",
        exec_reason="critic unresolved",
        req_id="REQ-2",
        task={"short_id": "T-102"},
        now_iso=lambda: "2026-03-07T00:10:00+0900",
    )

    todo = entry["todos"][0]
    assert todo["status"] == "blocked"
    assert todo["blocked_count"] == 3
    assert todo["blocked_bucket"] == "manual_followup"
    assert todo["blocked_request_id"] == "REQ-2"
    assert todo["blocked_reason"] == "critic unresolved"

    run_handlers._finalize_todo_after_run(
        entry=entry,
        todo_id="TODO-001",
        status="completed",
        exec_verdict="success",
        exec_reason="",
        req_id="REQ-3",
        task={"short_id": "T-103"},
        now_iso=lambda: "2026-03-07T00:20:00+0900",
    )

    assert todo["status"] == "done"
    assert "blocked_count" not in todo
    assert "blocked_bucket" not in todo
    assert "blocked_reason" not in todo
    assert "blocked_request_id" not in todo


def test_manual_followup_alert_is_sent_only_once() -> None:
    entry = {
        "project_alias": "O4",
        "todos": [
            {
                "id": "TODO-004",
                "summary": "need owner input",
                "priority": "P1",
                "status": "blocked",
                "blocked_count": 2,
                "blocked_bucket": "manual_followup",
                "blocked_reason": "critic unresolved after repair",
            }
        ],
    }
    sent: list[tuple[str, str, dict | None]] = []

    first = run_handlers._maybe_send_manual_followup_alert(
        entry=entry,
        todo_id="TODO-004",
        project_key="local_map_analysis",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        now_iso=lambda: "2026-03-07T00:30:00+0900",
    )
    second = run_handlers._maybe_send_manual_followup_alert(
        entry=entry,
        todo_id="TODO-004",
        project_key="local_map_analysis",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        now_iso=lambda: "2026-03-07T00:31:00+0900",
    )

    assert first is True
    assert second is False
    assert entry["todos"][0]["blocked_alerted_at"] == "2026-03-07T00:30:00+0900"
    assert len(sent) == 1
    assert sent[0][0] == "manual-followup-alert"
    assert "manual follow-up needed" in sent[0][1]
    assert "TODO-004" in sent[0][1]
    assert "/todo O4 followup" in sent[0][1]
    assert "/queue followup" in sent[0][1]
    markup = sent[0][2] or {}
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    assert "/todo O4 followup" in buttons
    assert "/queue followup" in buttons


def test_capture_todo_proposals_merges_and_alerts() -> None:
    entry = {
        "project_alias": "O2",
        "todos": [
            {
                "id": "TODO-010",
                "summary": "existing task",
                "priority": "P2",
                "status": "done",
            }
        ],
        "todo_seq": 10,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []

    result = run_handlers._maybe_capture_todo_proposals(
        args=argparse.Namespace(dry_run=False),
        entry=entry,
        key="twinpaper",
        p_args=argparse.Namespace(),
        prompt="run release prep",
        state={
            "complete": True,
            "replies": [
                {"role": "Codex-Writer", "body": "Release note draft is done. We still need a deployment checklist."}
            ],
        },
        req_id="REQ-900",
        task={"todo_id": "TODO-010", "short_id": "T-900"},
        todo_id="TODO-010",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged.append(kwargs),
        now_iso=lambda: "2026-03-09T10:00:00+0900",
        extract_todo_proposals=lambda *args, **kwargs: [
            {
                "summary": "prepare deployment checklist",
                "priority": "P1",
                "kind": "handoff",
                "reason": "release note mentions it is still missing",
                "confidence": 0.88,
            }
        ],
        merge_todo_proposals=todo_handlers.merge_todo_proposals,
    )

    assert result["created_count"] == 1
    assert entry["todo_proposals"][0]["id"] == "PROP-001"
    assert entry["todo_proposals"][0]["source_request_id"] == "REQ-900"
    assert sent
    assert sent[-1][0] == "todo-proposals-alert"
    assert "new todo proposals" in sent[-1][1]
    assert "prepare deployment checklist" in sent[-1][1]
    buttons = [btn["text"] for row in (sent[-1][2] or {}).get("keyboard", []) for btn in row]
    assert "/todo proposals" in buttons
    assert "/todo accept PROP-001" in buttons
    assert any(evt.get("event") == "todo_proposals_created" for evt in logged)


def test_capture_todo_proposals_prefers_backend_native_payload() -> None:
    entry = {
        "project_alias": "O4",
        "todos": [],
        "todo_seq": 0,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
    }
    sent: list[tuple[str, str, dict | None]] = []
    logged: list[dict] = []

    result = run_handlers._maybe_capture_todo_proposals(
        args=argparse.Namespace(dry_run=False),
        entry=entry,
        key="local_map_analysis",
        p_args=argparse.Namespace(),
        prompt="writer handoff prompt",
        state={
            "complete": True,
            "replies": [{"role": "Codex-Writer", "body": "hints are present but should not be reparsed"}],
            "followup_proposals": [
                {
                    "summary": "Draft the machine-readable summary table from the canonical backlog",
                    "priority": "P1",
                    "kind": "handoff",
                    "reason": "backend-native writer handoff proposal",
                    "confidence": 0.81,
                }
            ],
        },
        req_id="REQ-901",
        task={"todo_id": "TODO-015", "short_id": "T-901"},
        todo_id="TODO-015",
        send=lambda body, **kwargs: sent.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged.append(kwargs),
        now_iso=lambda: "2026-03-11T21:00:00+0900",
        extract_todo_proposals=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("extractor should not run when backend payload exists")),
        merge_todo_proposals=todo_handlers.merge_todo_proposals,
    )

    assert result["created_count"] == 1
    assert entry["todo_proposals"][0]["source_request_id"] == "REQ-901"
    assert entry["todo_proposals"][0]["kind"] == "handoff"
    assert any(evt.get("event") == "todo_proposals_backend_payload" for evt in logged)
    assert sent[-1][0] == "todo-proposals-alert"


def test_exec_pipeline_module_matches_run_terminal_todo_helpers() -> None:
    entry_a = {
        "project_alias": "O4",
        "todos": [
            {
                "id": "TODO-004",
                "summary": "need owner input",
                "priority": "P1",
                "status": "blocked",
                "blocked_count": 2,
                "blocked_bucket": "manual_followup",
                "blocked_reason": "critic unresolved after repair",
            }
        ],
        "pending_todo": {
            "todo_id": "TODO-004",
            "chat_id": "939062873",
            "selected_at": "2026-03-06T23:43:25+0900",
        },
    }
    entry_b = copy.deepcopy(entry_a)
    sent_a: list[tuple[str, str, dict | None]] = []
    sent_b: list[tuple[str, str, dict | None]] = []

    changed_a = run_handlers._cleanup_terminal_todo_gate(
        entry=entry_a,
        chat_id="939062873",
        todo_id="",
        pending_todo_used=False,
        run_auto_source="todo:next",
        reason="plan gate: critic unresolved after auto-replan",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
    )
    changed_b = exec_pipeline.cleanup_terminal_todo_gate(
        entry=entry_b,
        chat_id="939062873",
        todo_id="",
        pending_todo_used=False,
        run_auto_source="todo:next",
        reason="plan gate: critic unresolved after auto-replan",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
        manual_followup_threshold=2,
    )

    assert changed_a == changed_b
    assert entry_a == entry_b

    first_a = run_handlers._maybe_send_manual_followup_alert(
        entry=entry_a,
        todo_id="TODO-004",
        project_key="local_map_analysis",
        send=lambda body, **kwargs: sent_a.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        now_iso=lambda: "2026-03-07T00:30:00+0900",
    )
    first_b = exec_pipeline.maybe_send_manual_followup_alert(
        entry=entry_b,
        todo_id="TODO-004",
        project_key="local_map_analysis",
        send=lambda body, **kwargs: sent_b.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        now_iso=lambda: "2026-03-07T00:30:00+0900",
    )

    assert first_a == first_b == True
    assert sent_a == sent_b
    assert entry_a == entry_b


def test_exec_pipeline_module_matches_run_dispatch_sync_and_proposal_capture() -> None:
    entry_a = {"project_alias": "O2", "last_request_id": ""}
    entry_b = copy.deepcopy(entry_a)
    manager_state_a = {"projects": {"twinpaper": entry_a}}
    manager_state_b = {"projects": {"twinpaper": entry_b}}
    touches_a: list[tuple] = []
    touches_b: list[tuple] = []
    selects_a: list[tuple] = []
    selects_b: list[tuple] = []
    run_calls_a: list[dict] = []
    run_calls_b: list[dict] = []

    def _run_aoe_orch(*_args, **_kwargs):
        run_calls_a.append(dict(_kwargs))
        return {"request_id": "REQ-123", "complete": False, "replies": []}

    def _run_aoe_orch_b(*_args, **_kwargs):
        run_calls_b.append(dict(_kwargs))
        return {"request_id": "REQ-123", "complete": False, "replies": []}

    def _sync_task_lifecycle(**kwargs):
        return {
            "request_id": kwargs["request_data"]["request_id"],
            "status": "running",
            "stages": {"verification": "pending"},
        }

    result_a = run_handlers._dispatch_and_sync_task(
        p_args=argparse.Namespace(),
        dispatch_prompt="dispatch prompt",
        chat_id="939062873",
        dispatch_roles="Codex-Reviewer",
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        dispatch_metadata={"phase2_execution_plan": {"execution_mode": "single", "execution_lanes": []}},
        key="twinpaper",
        entry=entry_a,
        manager_state=manager_state_a,
        prompt="original prompt",
        selected_roles=["Codex-Reviewer"],
        verifier_roles=[],
        require_verifier=False,
        verifier_candidates=[],
        run_aoe_orch=_run_aoe_orch,
        touch_chat_recent_task_ref=lambda *args: touches_a.append(args),
        set_chat_selected_task_ref=lambda *args: selects_a.append(args),
        now_iso=lambda: "2026-03-11T10:00:00+09:00",
        sync_task_lifecycle=_sync_task_lifecycle,
    )
    result_b = exec_pipeline.dispatch_and_sync_task(
        p_args=argparse.Namespace(),
        dispatch_prompt="dispatch prompt",
        chat_id="939062873",
        dispatch_roles="Codex-Reviewer",
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=None,
        dispatch_metadata={"phase2_execution_plan": {"execution_mode": "single", "execution_lanes": []}},
        key="twinpaper",
        entry=entry_b,
        manager_state=manager_state_b,
        prompt="original prompt",
        selected_roles=["Codex-Reviewer"],
        verifier_roles=[],
        require_verifier=False,
        verifier_candidates=[],
        run_aoe_orch=_run_aoe_orch_b,
        touch_chat_recent_task_ref=lambda *args: touches_b.append(args),
        set_chat_selected_task_ref=lambda *args: selects_b.append(args),
        now_iso=lambda: "2026-03-11T10:00:00+09:00",
        sync_task_lifecycle=_sync_task_lifecycle,
    )

    assert result_a == result_b
    assert entry_a == entry_b
    assert touches_a == touches_b
    assert selects_a == selects_b
    assert run_calls_a == run_calls_b
    assert run_calls_a[0]["metadata"]["phase2_execution_plan"]["execution_mode"] == "single"

    proposal_entry_a = {
        "project_alias": "O2",
        "todos": [{"id": "TODO-010", "summary": "existing task", "priority": "P2", "status": "done"}],
        "todo_seq": 10,
        "todo_proposals": [],
        "todo_proposal_seq": 0,
    }
    proposal_entry_b = copy.deepcopy(proposal_entry_a)
    sent_a: list[tuple[str, str, dict | None]] = []
    sent_b: list[tuple[str, str, dict | None]] = []
    logged_a: list[dict] = []
    logged_b: list[dict] = []

    common_kwargs = dict(
        args=argparse.Namespace(dry_run=False),
        key="twinpaper",
        p_args=argparse.Namespace(),
        prompt="run release prep",
        state={
            "complete": True,
            "replies": [{"role": "Codex-Writer", "body": "Release note draft is done. We still need a deployment checklist."}],
        },
        req_id="REQ-900",
        task={"todo_id": "TODO-010", "short_id": "T-900"},
        todo_id="TODO-010",
        now_iso=lambda: "2026-03-09T10:00:00+0900",
        extract_todo_proposals=lambda *args, **kwargs: [
            {
                "summary": "prepare deployment checklist",
                "priority": "P1",
                "kind": "handoff",
                "reason": "release note mentions it is still missing",
                "confidence": 0.88,
            }
        ],
        merge_todo_proposals=todo_handlers.merge_todo_proposals,
    )

    proposals_a = run_handlers._maybe_capture_todo_proposals(
        entry=proposal_entry_a,
        send=lambda body, **kwargs: sent_a.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged_a.append(kwargs),
        **common_kwargs,
    )
    proposals_b = exec_pipeline.maybe_capture_todo_proposals(
        entry=proposal_entry_b,
        send=lambda body, **kwargs: sent_b.append((kwargs.get("context", ""), body, kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: logged_b.append(kwargs),
        **common_kwargs,
    )

    assert proposals_a == proposals_b
    assert proposal_entry_a == proposal_entry_b
    assert sent_a == sent_b
    assert logged_a == logged_b


def test_apply_scenario_items_to_entry_prunes_stale_sync_open_todos() -> None:
    entry = {
        "todos": [
            {
                "id": "TODO-001",
                "summary": "old sync row",
                "priority": "P2",
                "status": "open",
                "created_by": "sync:telegram:test",
            },
            {
                "id": "TODO-002",
                "summary": "manual row",
                "priority": "P2",
                "status": "open",
                "created_by": "manual:user",
            },
        ],
        "todo_seq": 2,
        "pending_todo": {"todo_id": "TODO-001", "chat_id": "939062873"},
    }

    counts = sched._apply_scenario_items_to_entry(
        entry=entry,
        items=[{"summary": "new sync row", "priority": "P1", "status": "open"}],
        chat_id="939062873",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
        dry_run=False,
        source_mode="fallback:files",
        sources=["TODO.md"],
        prune_missing=True,
    )

    assert counts["added"] == 1
    assert counts["pruned"] == 1
    assert "pending_todo" not in entry
    rows = {row["id"]: row for row in entry["todos"]}
    assert rows["TODO-001"]["status"] == "canceled"
    assert rows["TODO-001"]["canceled_reason"] == "sync_prune_missing"
    assert rows["TODO-002"]["status"] == "open"
    assert rows["TODO-003"]["summary"] == "new sync row"


def test_apply_scenario_items_to_entry_prunes_only_same_sync_group() -> None:
    entry = {
        "todos": [
            {
                "id": "TODO-001",
                "summary": "old project sync row",
                "priority": "P2",
                "status": "open",
                "created_by": "sync:telegram:test",
                "sync_managed": True,
                "sync_group": "todo_files",
            },
            {
                "id": "TODO-002",
                "summary": "old ops sync row",
                "priority": "P2",
                "status": "open",
                "created_by": "sync:telegram:test",
                "sync_managed": True,
                "sync_group": "ops",
            },
        ],
        "todo_seq": 2,
    }

    counts = sched._apply_scenario_items_to_entry(
        entry=entry,
        items=[
            {
                "summary": "fresh project sync row",
                "priority": "P1",
                "status": "open",
                "sync_group": "todo_files",
                "sync_source_class": "todo_file",
                "sync_confidence": 0.92,
            }
        ],
        chat_id="939062873",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
        dry_run=False,
        source_mode="fallback:files",
        sources=["notes/project_todo.md"],
        prune_missing=True,
    )

    rows = {row["id"]: row for row in entry["todos"]}
    assert counts["pruned"] == 1
    assert rows["TODO-001"]["status"] == "canceled"
    assert rows["TODO-002"]["status"] == "open"


def test_apply_scenario_items_to_entry_prunes_blocked_stale_sync_rows() -> None:
    entry = {
        "todos": [
            {
                "id": "TODO-001",
                "summary": "stale blocked sync row",
                "priority": "P2",
                "status": "blocked",
                "created_by": "sync:telegram:test",
                "blocked_reason": "old plan gate",
                "sync_group": "todo_files",
            },
        ],
        "todo_seq": 1,
    }

    counts = sched._apply_scenario_items_to_entry(
        entry=entry,
        items=[
            {
                "summary": "fresh project sync row",
                "priority": "P1",
                "status": "open",
                "sync_group": "todo_files",
                "sync_source_class": "todo_file",
                "sync_confidence": 0.92,
            }
        ],
        chat_id="939062873",
        now_iso=lambda: "2026-03-07T00:00:00+0900",
        dry_run=False,
        source_mode="fallback:files",
        sources=["notes/project_todo.md"],
        prune_missing=True,
    )

    rows = {row["id"]: row for row in entry["todos"]}
    assert counts["pruned"] == 1
    assert rows["TODO-001"]["status"] == "canceled"
    assert rows["TODO-001"]["canceled_reason"] == "sync_prune_missing"
    assert "blocked_reason" not in rows["TODO-001"]


def test_sync_replace_blocks_partial_scope_since_window(tmp_path: Path) -> None:
    state = gw.default_manager_state(tmp_path, tmp_path / ".aoe-team")
    twin_root = tmp_path / "TwinPaper"
    (twin_root / ".aoe-team").mkdir(parents=True, exist_ok=True)
    state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(twin_root),
        "team_dir": str(twin_root / ".aoe-team"),
        "tasks": {},
    }

    sent: list[str] = []
    args = argparse.Namespace(dry_run=False, manager_state_file=tmp_path / ".aoe-team" / "orch_manager_state.json")

    def _send(body: str, **_kwargs) -> bool:
        sent.append(body)
        return True

    def _get_context(raw_target: str | None):
        key, entry = gw.get_manager_project(state, raw_target)
        return key, entry, argparse.Namespace(project_root=Path(entry["project_root"]), team_dir=Path(entry["team_dir"]))

    result = sched.handle_scheduler_command(
        cmd="sync",
        args=args,
        manager_state=state,
        chat_id="939062873",
        chat_role="admin",
        orch_target=None,
        rest="replace O2 1h",
        send=_send,
        get_context=_get_context,
        save_manager_state=lambda path, manager_state: None,
        now_iso=lambda: "2026-03-07T00:00:00+0900",
    )

    assert result == {"terminal": True}
    assert sent
    text = sent[-1]
    assert "sync prune blocked" in text
    assert "avoid canceling unrelated todos" in text
    assert "/sync replace <O#|name>" in text


def test_orch_status_reply_markup_contains_monitor_todo_sync_and_focus_controls(tmp_path: Path) -> None:
    manager_state = _empty_state()
    team_dir = tmp_path / "TwinPaper" / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(tmp_path / "TwinPaper"),
        "team_dir": str(team_dir),
    }
    entry = manager_state["projects"]["twinpaper"]

    markup = orch_task_handlers._orch_status_reply_markup(manager_state, "twinpaper", entry)
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]
    for expected in [
        "/todo O2",
        "/todo O2 followup",
        "/orch monitor O2",
        "/sync preview O2 1h",
        "/sync O2 1h",
        "/use O2",
        "/focus O2",
        "/queue",
        "/next",
        "/map",
    ]:
        assert expected in buttons

    gw.set_project_lock(manager_state, "twinpaper")
    markup2 = orch_task_handlers._orch_status_reply_markup(manager_state, "twinpaper", entry)
    buttons2 = [btn["text"] for row in markup2.get("keyboard", []) for btn in row]
    assert "/focus off" in buttons2
    assert "/focus O2" not in buttons2


def test_orch_task_reply_markup_exposes_lane_retry_and_followup_actions(tmp_path: Path) -> None:
    manager_state = _empty_state()
    team_dir = tmp_path / "TwinPaper" / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "orchestrator.json").write_text("{}", encoding="utf-8")
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(tmp_path / "TwinPaper"),
        "team_dir": str(team_dir),
    }
    entry = manager_state["projects"]["twinpaper"]
    task = {
        "request_id": "REQ-123",
        "context": {"task_short_id": "T-123"},
        "exec_critic": {
            "verdict": "retry",
            "action": "replan",
            "rerun_execution_lane_ids": ["L2"],
            "rerun_review_lane_ids": ["R2"],
            "manual_followup_execution_lane_ids": ["L2"],
            "manual_followup_review_lane_ids": ["R2"],
        },
    }

    markup = orch_task_handlers._orch_task_reply_markup("twinpaper", entry, "REQ-123", task)
    buttons = [btn["text"] for row in markup.get("keyboard", []) for btn in row]

    for expected in [
        "/check T-123",
        "/task T-123",
        "/retry T-123",
        "/retry T-123 lane L2",
        "/retry T-123 lane R2",
        "/replan T-123",
        "/replan T-123 lane L2",
        "/replan T-123 lane R2",
        "/followup T-123",
        "/followup T-123 lane L2",
        "/followup T-123 lane R2",
        "/todo O2 followup",
        "/orch monitor O2",
        "/orch status O2",
        "/queue",
        "/map",
    ]:
        assert expected in buttons


def test_resolve_message_command_parses_retry_lane_selector() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")

    resolved = resolver.resolve_message_command(
        text="/retry T-123 lane L2,R1",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-retry"
    assert resolved.orch_retry_request_id == "T-123"
    assert resolved.orch_retry_lane_ids == ["L2", "R1"]


def test_resolve_message_command_parses_followup_lane_selector() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")

    resolved = resolver.resolve_message_command(
        text="/followup T-123 lane L2,R1",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-followup"
    assert resolved.orch_followup_request_id == "T-123"
    assert resolved.orch_followup_lane_ids == ["L2", "R1"]


def test_resolve_message_command_parses_followup_execute_lane_selector() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")

    resolved = resolver.resolve_message_command(
        text="/followup-exec T-123 lane L2,R1",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "orch-followup-exec"
    assert resolved.orch_followup_execute_request_id == "T-123"
    assert resolved.orch_followup_execute_lane_ids == ["L2", "R1"]


def test_resolve_message_command_parses_history_search() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")

    resolved = resolver.resolve_message_command(
        text="/history search planning_gate --project O3",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "history"
    assert resolved.rest == "search planning_gate --project O3"


def test_resolve_retry_replan_transition_rejects_invalid_lane_selector() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "prompt": "retry target",
                "roles": ["Codex-Dev", "Codex-Reviewer"],
                "exec_critic": {
                    "verdict": "retry",
                    "action": "retry",
                    "rerun_execution_lane_ids": ["L2"],
                    "rerun_review_lane_ids": ["R2"],
                },
            }
        },
    }
    sent: list[tuple[str, str]] = []
    result = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-retry",
        args=argparse.Namespace(require_verifier=False, verifier_roles=""),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_retry_request_id="REQ-123",
        orch_replan_request_id=None,
        orch_followup_execute_request_id=None,
        orch_retry_lane_ids=["L1"],
        orch_replan_lane_ids=None,
        orch_followup_execute_lane_ids=None,
        send=lambda text, **kwargs: sent.append((text, kwargs.get("context", ""))) or True,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        get_chat_selected_task_ref=lambda *_args, **_kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=lambda rows: [str(item).strip() for item in rows if str(item).strip()],
        touch_chat_recent_task_ref=lambda *_args, **_kwargs: None,
        set_chat_selected_task_ref=lambda *_args, **_kwargs: None,
    )

    assert result == {"terminal": True}
    assert sent
    assert "requested lanes are not allowed" in sent[-1][0]


def test_resolve_retry_replan_transition_preserves_selected_lane_targets() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "prompt": "retry target",
                "roles": ["Codex-Dev", "Codex-Reviewer"],
                "exec_critic": {
                    "verdict": "retry",
                    "action": "retry",
                    "rerun_execution_lane_ids": ["L1", "L2"],
                    "rerun_review_lane_ids": ["R1", "R2"],
                },
            }
        },
    }
    result = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-retry",
        args=argparse.Namespace(require_verifier=False, verifier_roles=""),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_retry_request_id="REQ-123",
        orch_replan_request_id=None,
        orch_followup_execute_request_id=None,
        orch_retry_lane_ids=["L2", "R2"],
        orch_replan_lane_ids=None,
        orch_followup_execute_lane_ids=None,
        send=lambda *_args, **_kwargs: True,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        get_chat_selected_task_ref=lambda *_args, **_kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=lambda rows: [str(item).strip() for item in rows if str(item).strip()],
        touch_chat_recent_task_ref=lambda *_args, **_kwargs: None,
        set_chat_selected_task_ref=lambda *_args, **_kwargs: None,
    )

    assert isinstance(result, dict)
    assert result["terminal"] is False
    assert result["run_selected_execution_lane_ids"] == ["L2"]
    assert result["run_selected_review_lane_ids"] == ["R2"]


def test_resolve_followup_execute_transition_uses_execution_slice_only() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "prompt": "followup target",
                "roles": ["Codex-Dev", "Codex-Reviewer"],
                "followup_brief_status": "partially_executable",
                "followup_brief_execution_lane_ids": ["L2"],
                "followup_brief_review_lane_ids": ["R2"],
                "exec_critic": {
                    "manual_followup_execution_lane_ids": ["L2"],
                    "manual_followup_review_lane_ids": ["R2"],
                },
            }
        },
    }

    result = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-followup-exec",
        args=argparse.Namespace(require_verifier=False, verifier_roles=""),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_retry_request_id=None,
        orch_replan_request_id=None,
        orch_followup_execute_request_id="REQ-123",
        orch_retry_lane_ids=None,
        orch_replan_lane_ids=None,
        orch_followup_execute_lane_ids=["L2"],
        send=lambda *_args, **_kwargs: True,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        get_chat_selected_task_ref=lambda *_args, **_kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=lambda rows: [str(item).strip() for item in rows if str(item).strip()],
        touch_chat_recent_task_ref=lambda *_args, **_kwargs: None,
        set_chat_selected_task_ref=lambda *_args, **_kwargs: None,
    )

    assert isinstance(result, dict)
    assert result["terminal"] is False
    assert result["run_control_mode"] == "followup"
    assert result["run_selected_execution_lane_ids"] == ["L2"]
    assert result["run_selected_review_lane_ids"] == []


def test_resolve_followup_execute_transition_rejects_review_lane_selection() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "prompt": "followup target",
                "roles": ["Codex-Dev", "Codex-Reviewer"],
                "followup_brief_status": "partially_executable",
                "followup_brief_execution_lane_ids": ["L2"],
                "followup_brief_review_lane_ids": ["R2"],
                "exec_critic": {
                    "manual_followup_execution_lane_ids": ["L2"],
                    "manual_followup_review_lane_ids": ["R2"],
                },
            }
        },
    }
    sent: list[tuple[str, str]] = []
    result = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-followup-exec",
        args=argparse.Namespace(require_verifier=False, verifier_roles=""),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_retry_request_id=None,
        orch_replan_request_id=None,
        orch_followup_execute_request_id="REQ-123",
        orch_retry_lane_ids=None,
        orch_replan_lane_ids=None,
        orch_followup_execute_lane_ids=["R2"],
        send=lambda text, **kwargs: sent.append((text, kwargs.get("context", ""))) or True,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        get_chat_selected_task_ref=lambda *_args, **_kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=lambda rows: [str(item).strip() for item in rows if str(item).strip()],
        touch_chat_recent_task_ref=lambda *_args, **_kwargs: None,
        set_chat_selected_task_ref=lambda *_args, **_kwargs: None,
    )

    assert result == {"terminal": True}
    assert sent
    assert "only supports execution lanes" in sent[-1][0]


def test_orch_followup_rejects_invalid_lane_selector() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "status": "failed",
                "prompt": "followup target",
                "context": {"task_short_id": "T-123"},
                "exec_critic": {
                    "verdict": "intervention",
                    "action": "manual_followup",
                    "reason": "Need operator review",
                    "manual_followup_execution_lane_ids": ["L2"],
                    "manual_followup_review_lane_ids": ["R2"],
                },
            }
        },
    }
    sent: list[tuple[str, str]] = []
    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-followup",
        args=argparse.Namespace(
            require_verifier=False,
            verifier_roles="",
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            dry_run=True,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        orch_followup_request_id="REQ-123",
        orch_followup_lane_ids=["L1"],
        send=lambda text, **kwargs: sent.append((text, kwargs.get("context", ""))) or True,
        log_event=lambda **kwargs: None,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda *args, **kwargs: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-03-13T12:00:00+0900",
        run_aoe_status=lambda *args, **kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda task, fallback_request_id="": str((task or {}).get("context", {}).get("task_short_id") or fallback_request_id),
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert sent
    assert sent[-1][1] == "orch-followup lane invalid"
    assert "requested follow-up lanes are not allowed" in sent[-1][0]


def test_orch_followup_summarizes_allowed_lane_targets() -> None:
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(ROOT / ".aoe-team"),
        "last_request_id": "REQ-123",
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "status": "failed",
                "prompt": "followup target",
                "context": {"task_short_id": "T-123"},
                "exec_critic": {
                    "verdict": "intervention",
                    "action": "manual_followup",
                    "reason": "Need operator review",
                    "manual_followup_execution_lane_ids": ["L2"],
                    "manual_followup_review_lane_ids": ["R2"],
                    "rerun_execution_lane_ids": ["L2"],
                },
            }
        },
    }
    sent: list[tuple[str, str, object]] = []
    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-followup",
        args=argparse.Namespace(
            require_verifier=False,
            verifier_roles="",
            manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
            dry_run=True,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        orch_followup_request_id="REQ-123",
        orch_followup_lane_ids=["L2", "R2"],
        send=lambda text, **kwargs: sent.append((text, kwargs.get("context", ""), kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(ROOT / ".aoe-team"))),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda *args, **kwargs: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-03-13T12:00:00+0900",
        run_aoe_status=lambda *args, **kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda task, fallback_request_id="": str((task or {}).get("context", {}).get("task_short_id") or fallback_request_id),
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert sent
    text, context, reply_markup = sent[-1]
    assert context == "orch-followup"
    assert "manual follow-up" in text
    assert "execution lanes: L2" in text
    assert "review lanes: R2" in text
    assert "Need operator review" in text
    buttons = [btn["text"] for row in (reply_markup or {}).get("keyboard", []) for btn in row]
    assert "/followup T-123" in buttons
    assert "/followup T-123 lane L2" in buttons
    assert "/followup T-123 lane R2" in buttons


def test_orch_followup_execute_blocks_preview_only_followup_brief(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    manager_state_file = team_dir / "orch_manager_state.json"
    manager_state = _empty_state()
    manager_state["projects"]["twinpaper"] = {
        "name": "twinpaper",
        "display_name": "TwinPaper",
        "project_alias": "O2",
        "project_root": str(ROOT),
        "team_dir": str(team_dir),
        "last_request_id": "REQ-123",
        "tasks": {
            "REQ-123": {
                "request_id": "REQ-123",
                "status": "failed",
                "prompt": "followup target",
                "context": {"task_short_id": "T-123"},
                "followup_brief_status": "preview_only",
                "followup_brief_summary": "preview_only | execution=L2 | review=R2",
                "followup_brief_execution_lane_ids": ["L2"],
                "followup_brief_review_lane_ids": ["R2"],
                "followup_brief_reason": "operator decision required",
                "exec_critic": {
                    "verdict": "intervention",
                    "action": "manual_followup",
                    "reason": "Need operator review",
                    "manual_followup_execution_lane_ids": ["L2"],
                    "manual_followup_review_lane_ids": ["R2"],
                },
            }
        },
    }
    sent: list[tuple[str, str, object]] = []
    handled = orch_task_handlers.handle_orch_task_command(
        cmd="orch-followup-exec",
        args=argparse.Namespace(
            require_verifier=False,
            verifier_roles="",
            manager_state_file=manager_state_file,
            dry_run=True,
        ),
        manager_state=manager_state,
        chat_id="939062873",
        orch_target="twinpaper",
        orch_add_name=None,
        orch_add_path=None,
        orch_add_overview=None,
        orch_add_init=True,
        orch_add_spawn=True,
        orch_add_set_active=True,
        rest="",
        orch_check_request_id=None,
        orch_task_request_id=None,
        orch_pick_request_id=None,
        orch_cancel_request_id=None,
        orch_followup_request_id=None,
        orch_followup_lane_ids=None,
        orch_followup_execute_request_id="REQ-123",
        orch_followup_execute_lane_ids=["L2"],
        send=lambda text, **kwargs: sent.append((text, kwargs.get("context", ""), kwargs.get("reply_markup"))) or True,
        log_event=lambda **kwargs: None,
        get_context=lambda orch: (str(orch or "twinpaper"), manager_state["projects"]["twinpaper"], argparse.Namespace(team_dir=str(team_dir))),
        latest_task_request_refs=lambda *args, **kwargs: [],
        set_chat_recent_task_refs=lambda *args, **kwargs: None,
        save_manager_state=lambda *args, **kwargs: None,
        resolve_project_root=lambda raw: Path(raw).expanduser().resolve(),
        is_path_within=lambda path, root: True,
        register_orch_project=lambda *args, **kwargs: ("", {}),
        run_aoe_init=lambda *args, **kwargs: "",
        run_aoe_spawn=lambda *args, **kwargs: "",
        now_iso=lambda: "2026-04-06T20:20:00+0900",
        run_aoe_status=lambda *args, **kwargs: "",
        resolve_chat_task_ref=lambda *_args, **_kwargs: "REQ-123",
        resolve_task_request_id=lambda entry, ref: ref if ref in entry.get("tasks", {}) else "",
        run_request_query=lambda *args, **kwargs: {},
        sync_task_lifecycle=lambda *args, **kwargs: None,
        resolve_verifier_candidates=lambda text: [],
        touch_chat_recent_task_ref=lambda *args, **kwargs: None,
        set_chat_selected_task_ref=lambda *args, **kwargs: None,
        get_chat_selected_task_ref=lambda *args, **kwargs: "",
        get_task_record=lambda entry, req_id: entry.get("tasks", {}).get(req_id),
        summarize_request_state=lambda *args, **kwargs: "",
        summarize_three_stage_request=lambda *args, **kwargs: "",
        summarize_task_lifecycle=lambda *args, **kwargs: "",
        task_display_label=lambda task, fallback_request_id="": str((task or {}).get("context", {}).get("task_short_id") or fallback_request_id),
        cancel_request_assignments=lambda *args, **kwargs: {},
        lifecycle_set_stage=lambda *args, **kwargs: None,
        summarize_cancel_result=lambda *args, **kwargs: "",
    )

    assert handled is True
    assert sent
    text, context, _reply_markup = sent[-1]
    assert context == "orch-followup-exec blocked"
    assert "follow-up execute blocked" in text
    assert "followup_brief: preview_only" in text
    assert "- /followup T-123" in text


def test_resolve_message_command_auto_routes_plain_text_from_direct_bias() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")
    gw.set_default_mode(manager_state, "939062873", "direct")

    resolved = resolver.resolve_message_command(
        text="결측치 규칙을 검토해줘",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "run"
    assert resolved.run_force_mode == "dispatch"
    assert resolved.run_auto_source.startswith("orch-action:")


def test_resolve_message_command_forces_dispatch_for_repo_mutation_prompt() -> None:
    manager_state = gw.default_manager_state(ROOT, ROOT / ".aoe-team")
    gw.set_default_mode(manager_state, "939062873", "direct")

    resolved = resolver.resolve_message_command(
        text="KRISS 폴더에서 키워드 추세기가 왜 안 되는지 확인하고 고쳐서 다시 푸시해줘",
        slash_only=False,
        manager_state=manager_state,
        chat_id="939062873",
        dry_run=True,
        manager_state_file=ROOT / ".aoe-team" / "orch_manager_state.json",
        get_pending_mode=gw.get_pending_mode,
        get_default_mode=gw.get_default_mode,
        clear_pending_mode=gw.clear_pending_mode,
        save_manager_state=lambda path, state: None,
    )

    assert resolved.cmd == "run"
    assert resolved.run_force_mode == "dispatch"
    assert resolved.run_auto_source.startswith("orch-action:")


def test_parse_quick_message_supports_routine_aliases() -> None:
    assert tg_parse.parse_quick_message("todo") == {"cmd": "todo", "rest": ""}
    assert tg_parse.parse_quick_message("다음 할일") == {"cmd": "todo", "rest": "next"}
    assert tg_parse.parse_quick_message("sync preview 1h") == {"cmd": "sync", "rest": "preview 1h"}
    assert tg_parse.parse_quick_message("동기화 미리보기 O2 3h") == {"cmd": "sync", "rest": "preview O2 3h"}
    assert tg_parse.parse_quick_message("오프데스크") == {"cmd": "offdesk", "rest": "status"}
    assert tg_parse.parse_quick_message("퇴근모드") == {"cmd": "offdesk", "rest": "on"}
    assert tg_parse.parse_quick_message("자동 상태") == {"cmd": "auto", "rest": "status"}


def test_parse_quick_message_keeps_non_command_plain_text_free() -> None:
    assert tg_parse.parse_quick_message("동기화가 계속 꼬이는 이유를 분석해줘") is None
    assert tg_parse.parse_quick_message("자동 실행을 검토해줘") is None
