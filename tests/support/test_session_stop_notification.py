"""Offline checks for observed session-stop alerts."""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import offdesk_remote_operator_telegram as operator
from telegram_operator.notification_policy import NotificationCycle
from telegram_operator.session_lifecycle import observe_session_lifecycle


def row(session_id: str, status: str) -> dict:
    return {
        "id": session_id,
        "status": status,
        "title": "Wrt" if session_id == "wrt" else session_id,
        "project": "nanoclustering",
        "tool": "codex",
    }


class SessionStopNotification(unittest.TestCase):
    def test_only_live_to_stopped_after_two_scans_creates_event(self):
        state = {}
        historical = [row(f"old-{i}", "stopped") for i in range(70)]
        self.assertEqual(observe_session_lifecycle(state, historical, now=1000), [])
        self.assertEqual(observe_session_lifecycle(state, historical, now=1060), [])
        self.assertEqual(state["session_lifecycle_events"], [])
        self.assertEqual(observe_session_lifecycle(state, historical + [row("wrt", "running")], now=1120), [])
        self.assertEqual(observe_session_lifecycle(state, historical + [row("wrt", "stopped")], now=1180), [])
        pending = observe_session_lifecycle(state, historical + [row("wrt", "stopped")], now=1240)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["cause"], "unknown")
        self.assertEqual(pending[0]["previous_live_status"], "running")
        self.assertEqual(len(state["session_lifecycle_events"]), 1)
        self.assertEqual(len(observe_session_lifecycle(state, historical + [row("wrt", "stopped")], now=1300)), 1)
        observe_session_lifecycle(state, historical + [row("wrt", "running")], now=1360)
        self.assertEqual(observe_session_lifecycle(state, historical + [row("wrt", "stopped")], now=1420), [])
        self.assertEqual(len(observe_session_lifecycle(state, historical + [row("wrt", "stopped")], now=1480)), 1)
        self.assertEqual(len([x for x in state["session_lifecycle_events"] if x["event"] == "session_stopped"]), 2)

    def test_alert_is_single_delivery_and_records_unknown_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            args = argparse.Namespace(
                session_notify=True, dry_run=False, session_status_file=status_file,
                session_notify_quiet_from_hour=0, session_notify_quiet_to_hour=0,
                session_notify_backoff_sec=1800, session_notify_max_backoff_sec=14400,
                session_notify_max_cards=4,
            )
            state = {"telegram_session_keyboard_schema": "inline_v3"}
            sent = []

            def scan(status: str, now: float):
                status_file.write_text(json.dumps({"sessions": [row("wrt", status)]}))
                cycle = NotificationCycle(lambda candidate: sent.append(candidate) or len(sent))
                with patch.object(operator.time, "time", return_value=now):
                    result = operator.scan_and_notify_waiting_sessions(
                        args, {"target_chat_id": "fake"}, state, cycle=cycle
                    )
                    cycle.deliver()
                return result

            scan("running", 1000)
            scan("stopped", 1060)
            self.assertEqual(sent, [])
            result = scan("stopped", 1120)
            self.assertEqual(result["status"], "notified")
            self.assertEqual(result["stopped_session_id"], "wrt")
            self.assertEqual(len(sent), 1)
            self.assertIn("종료 원인은 확인되지 않았습니다", sent[0].message)
            self.assertIsNone(sent[0].reply_markup)
            scan("stopped", 1180)
            self.assertEqual(len(sent), 1)
            event = state["session_lifecycle_events"][0]
            self.assertEqual(event["alert_status"], "delivered")
            self.assertEqual(event["alert_message_id"], 1)
            self.assertEqual(event["cause"], "unknown")

    def test_quiet_hours_defer_until_open_and_recovery_cancels_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            args = argparse.Namespace(
                session_notify=True, dry_run=False, session_status_file=status_file,
                session_notify_quiet_from_hour=22, session_notify_quiet_to_hour=8,
                session_notify_backoff_sec=1800, session_notify_max_backoff_sec=14400,
                session_notify_max_cards=4,
            )
            state = {"telegram_session_keyboard_schema": "inline_v3"}
            sent = []

            def scan(status: str, now: float, quiet: bool):
                status_file.write_text(json.dumps({"sessions": [row("wrt", status)]}))
                cycle = NotificationCycle(lambda candidate: sent.append(candidate) or len(sent))
                with patch.object(operator.time, "time", return_value=now), \
                     patch.object(operator, "in_quiet_hours", return_value=quiet):
                    operator.scan_and_notify_waiting_sessions(
                        args, {"target_chat_id": "fake"}, state, cycle=cycle
                    )
                    cycle.deliver()

            scan("running", 1000, True)
            scan("stopped", 1060, True)
            scan("stopped", 1120, True)
            self.assertEqual(sent, [])
            scan("stopped", 1180, False)
            self.assertEqual(len(sent), 1)
            scan("running", 1240, False)
            scan("stopped", 1300, True)
            scan("stopped", 1360, True)
            scan("running", 1420, False)
            self.assertEqual(len(sent), 1)
            stops = [x for x in state["session_lifecycle_events"] if x["event"] == "session_stopped"]
            self.assertEqual(stops[-1]["alert_status"], "recovered_before_alert")

    def test_failed_send_retries_but_unconfirmed_send_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            args = argparse.Namespace(
                session_notify=True, dry_run=False, session_status_file=status_file,
                session_notify_quiet_from_hour=0, session_notify_quiet_to_hour=0,
                session_notify_backoff_sec=1800, session_notify_max_backoff_sec=14400,
                session_notify_max_cards=4,
            )
            state = {"telegram_session_keyboard_schema": "inline_v3"}

            def scan(status: str, now: float, sender):
                status_file.write_text(json.dumps({"sessions": [row("wrt", status)]}))
                cycle = NotificationCycle(sender)
                with patch.object(operator.time, "time", return_value=now):
                    operator.scan_and_notify_waiting_sessions(
                        args, {"target_chat_id": "fake"}, state, cycle=cycle
                    )
                    return cycle.deliver()

            scan("running", 1000, lambda _: 1)
            scan("stopped", 1060, lambda _: 1)

            def fail(_):
                raise operator.RemoteOperatorTelegramError("offline")

            self.assertEqual(scan("stopped", 1120, fail)["status"], "send_failed")
            self.assertEqual(state["session_lifecycle_events"][0]["alert_status"], "pending")
            self.assertEqual(scan("stopped", 1180, lambda _: None)["status"], "delivery_unconfirmed")
            self.assertEqual(state["session_lifecycle_events"][0]["alert_status"], "delivery_unconfirmed")
            self.assertEqual(scan("stopped", 1240, lambda _: 1)["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
