#!/usr/bin/env python3
"""Shared operator action contract regressions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
MOD_FILE = GW_DIR / "aoe_tg_operator_action_contract.py"

if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

_spec = importlib.util.spec_from_file_location("aoe_tg_operator_action_contract_mod", MOD_FILE)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_classify_operator_command_distinguishes_safe_and_phase2() -> None:
    safe = mod.classify_operator_command("/task T-001")
    phase2 = mod.classify_operator_command("/retry T-001")

    assert safe["bucket"] == "safe"
    assert safe["mutation"] == "safe"
    assert phase2["bucket"] == "phase2"
    assert phase2["mutation"] == "runtime_mutation"


def test_classify_operator_command_handles_preview_and_bootstrap_paths() -> None:
    preview = mod.classify_operator_command("/sync preview O2 24h")
    bootstrap = mod.classify_operator_command("/sync bootstrap O2 24h")

    assert preview["bucket"] == "safe"
    assert preview["note"] == "read-only sync inspection"
    assert bootstrap["bucket"] == "phase2"
    assert bootstrap["mutation"] == "runtime_mutation"


def test_partition_task_operator_commands_splits_phase2_actions() -> None:
    rows = mod.partition_operator_commands(
        mod.task_operator_commands(
            project_alias="O2",
            label="T-001 | analysis-check",
            request_id="REQ-1",
            tf_phase="needs_retry",
            rerun_summary="execution=L1 | review=R1",
            followup_summary="-",
            rate_limit_summary="-",
        )
    )

    assert "/task T-001" in rows["safe"]
    assert "/request REQ-1" in rows["safe"]
    assert "/monitor O2" in rows["safe"]
    assert "/offdesk review" in rows["safe"]
    assert "/retry T-001" in rows["phase2"]


def test_partition_runtime_operator_commands_moves_mutating_priority_to_phase2() -> None:
    rows = mod.partition_operator_commands(
        mod.runtime_operator_commands(
            project_alias="O2",
            priority_action="/sync bootstrap O2 24h",
            has_active_task=True,
            has_rate_limit=False,
        )
    )

    assert "/sync bootstrap O2 24h" in rows["phase2"]
    assert "/monitor O2" in rows["safe"]
    assert "/todo O2" in rows["safe"]
    assert "/offdesk review" in rows["safe"]


def test_partition_runtime_operator_commands_adds_background_queue_cleanup_for_stale_queue() -> None:
    rows = mod.partition_operator_commands(
        mod.runtime_operator_commands(
            project_alias="O2",
            priority_action="/offdesk review O2",
            has_active_task=True,
            has_rate_limit=False,
            background_queue_stale_count=2,
        )
    )

    assert "/orch bgq-clean O2" in rows["phase2"]
    assert "/offdesk review O2" in rows["safe"]


def test_http_action_spec_maps_retry_to_post_contract() -> None:
    row = mod.http_action_spec("/retry T-001 lane L1,R1")

    assert row is not None
    assert row["mode"] == "phase2"
    assert row["method"] == "POST"
    assert row["path"] == "/control/actions/task/retry"
    assert row["payload"] == {"task_ref": "T-001", "lane_ids": ["L1", "R1"]}


def test_http_action_spec_maps_followup_to_safe_post_contract() -> None:
    row = mod.http_action_spec("/followup T-001 lane L2")

    assert row is not None
    assert row["mode"] == "safe"
    assert row["path"] == "/control/actions/task/followup"
    assert row["payload"] == {"task_ref": "T-001", "lane_ids": ["L2"]}


def test_http_action_spec_maps_sync_preview_to_safe_runtime_contract() -> None:
    row = mod.http_action_spec("/sync preview O2 24h")

    assert row is not None
    assert row["mode"] == "safe"
    assert row["path"] == "/control/actions/runtime/sync-preview"
    assert row["payload"] == {"project_ref": "O2", "window": "24h"}


def test_http_action_spec_maps_bgq_clean_to_phase2_runtime_contract() -> None:
    row = mod.http_action_spec("/orch bgq-clean O2")

    assert row is not None
    assert row["mode"] == "phase2"
    assert row["path"] == "/control/actions/runtime/background-queue-clean"
    assert row["payload"] == {"project_ref": "O2"}


def test_classify_operator_command_handles_background_worker_lifecycle_commands() -> None:
    status = mod.classify_operator_command("/orch bgw-status O2")
    start = mod.classify_operator_command("/orch bgw-start O2")
    stop = mod.classify_operator_command("/orch bgw-stop O2")

    assert status["bucket"] == "safe"
    assert status["scope"] == "runtime"
    assert start["bucket"] == "phase2"
    assert start["mutation"] == "runtime_mutation"
    assert stop["bucket"] == "phase2"


def test_http_action_spec_maps_auto_recover_force_to_phase2_contract() -> None:
    row = mod.http_action_spec("/auto recover force")

    assert row is not None
    assert row["mode"] == "phase2"
    assert row["path"] == "/control/actions/control/auto-recover"
    assert row["payload"] == {"force": True}
