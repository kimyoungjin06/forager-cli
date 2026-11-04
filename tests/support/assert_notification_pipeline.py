"""Consume a real Rust status row; exercise the notification collector offline."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import offdesk_remote_operator_telegram as operator
from telegram_operator.dispatch import classify_session_interaction
from telegram_operator.notification_policy import NotificationCycle

row = json.load(sys.stdin)
assert row["status"] == "waiting"
assert classify_session_interaction(row["notification_capture"]["tail"], "codex")["kind"] == "task_ready"
with tempfile.TemporaryDirectory() as directory:
    status = Path(directory) / "status.json"
    status.write_text(json.dumps({"sessions": [row]}))
    args = argparse.Namespace(
        session_notify=True, dry_run=True, session_status_file=status,
        session_notify_quiet_from_hour=0, session_notify_quiet_to_hour=0,
        session_notify_backoff_sec=1800, session_notify_max_backoff_sec=14400,
        session_notify_max_cards=4,
    )
    state = {"telegram_session_keyboard_schema": "inline_v3"}
    sent = []
    cycle = NotificationCycle(lambda candidate: sent.append(candidate) or None, dry_run=True)
    with patch.object(operator, "summarize_waiting_prompt", return_value="Ready for next task"), patch.object(operator.subprocess, "run", side_effect=AssertionError("unexpected external call")):
        report = operator.scan_and_notify_waiting_sessions(args, {"target_chat_id": "synthetic"}, state, cycle=cycle)
        result = cycle.deliver()
    assert result["delivered_count"] == 1, (report, result)
    assert len(sent) == 1
    assert "Dismiss and keep waiting" not in sent[0].message
    print("rust_status_to_notification_candidate=pass")
