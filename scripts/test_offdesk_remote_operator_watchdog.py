"""Offline watchdog incidents, recovery delivery and classification regressions."""

import argparse
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import offdesk_remote_operator_watchdog as watchdog


NOW = dt.datetime(2026, 10, 19, 3, 0, tzinfo=dt.timezone.utc)


class FrozenDateTime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.args = argparse.Namespace(
            profile="test", env_file=self.root / "telegram.env",
            loop_status_file=self.root / "loop.json", state_file=self.root / "state.json",
            service_name="test-listener.service", systemctl_bin="systemctl", systemd_mode="off",
            health_max_age_sec=180, alert_min_interval_sec=1800, api_timeout_sec=1,
            dry_run=False, out=None,
        )
        self.args.env_file.write_text("TELEGRAM_BOT_TOKEN=fake\nTELEGRAM_OWNER_CHAT_ID=fake\n")
        for mock in (
            patch.object(watchdog.dt, "datetime", FrozenDateTime),
            patch.object(watchdog.urllib.request, "urlopen", side_effect=AssertionError("no network allowed")),
        ):
            mock.start()
            self.addCleanup(mock.stop)
        self.send_patcher = patch.object(watchdog, "send_alert", return_value=73)
        self.send = self.send_patcher.start()
        self.addCleanup(self.send_patcher.stop)
        self.loop()

    def loop(self, status="no_update", count=0, success_age=10, poll_age=1):
        value = {
            "status": "polling", "generated_at": (NOW - dt.timedelta(hours=1)).isoformat(),
            "consecutive_error_count": count,
            "last_success_at": (NOW - dt.timedelta(seconds=success_age)).isoformat(),
            "last_result": {"generated_at": (NOW - dt.timedelta(seconds=poll_age)).isoformat(),
                            "status": status, "error": "Telegram API URL error: dns_error (getUpdates)"},
        }
        self.args.loop_status_file.write_text(json.dumps(value))
        return value

    def state(self):
        return json.loads(self.args.state_file.read_text())

    def open_incident(self):
        self.loop("poll_error", 5, 300)
        report = watchdog.build_report(self.args)
        self.assertTrue(report["alert"]["sent"])
        self.assertTrue(self.state()["active_incident"]["delivered"])
        return report

    def test_isolated_errors_are_degraded_without_alert_or_write(self):
        for count, age in [(1, 10), (2, 600), (3, 179)]:
            with self.subTest(count=count, age=age):
                self.loop("poll_error", count, age)
                report = watchdog.build_report(self.args)
                self.assertEqual(report["health_status"], "degraded")
                self.assertEqual(report["issues"], [])
                self.assertIn("last_poll_transport_error", report["warnings"])
                self.assertEqual(report["alert"]["reason"], "transient_transport_error")
                self.assertFalse(self.args.state_file.exists())
        self.send.assert_not_called()

    def test_sustained_errors_require_both_count_and_duration(self):
        self.loop("poll_error", 3, 180)
        report = watchdog.build_report(self.args)
        self.assertEqual(report["health_status"], "unhealthy")
        self.assertEqual(report["alert"]["kind"], "failure")
        message = report["alert"]["message_preview"]
        self.assertNotIn("restart", message)
        self.assertNotIn("야간주행: 불가", message)
        self.assertIn("dns_error", report["listener"]["last_error"])

    def test_missing_or_invalid_history_does_not_hide_failure(self):
        for count in [None, 0, True, "1"]:
            with self.subTest(count=count):
                self.loop("poll_error", count, 10)
                self.args.dry_run = True
                self.assertEqual(watchdog.build_report(self.args)["health_status"], "unhealthy")

    def test_new_listener_uses_start_time_until_first_success(self):
        value = self.loop("poll_error", 1, 10)
        value["last_success_at"] = None
        value["generated_at"] = (NOW - dt.timedelta(seconds=10)).isoformat()
        self.args.loop_status_file.write_text(json.dumps(value))
        self.assertEqual(watchdog.build_report(self.args)["health_status"], "degraded")

    def test_stale_heartbeat_bypasses_transport_grace(self):
        self.loop("poll_error", 1, 10, poll_age=181)
        report = watchdog.build_report(self.args)
        self.assertEqual(report["health_status"], "unhealthy")
        self.assertIn("last_poll_stale", report["issues"])

    def test_missing_listener_is_immediate_failure(self):
        self.args.loop_status_file.unlink()
        report = watchdog.build_report(self.args)
        self.assertIn("loop_status_missing", report["issues"])
        self.assertTrue(report["alert"]["sent"])

    def test_failed_service_is_immediate_and_can_recommend_restart(self):
        self.loop("poll_error", 1, 10)
        with patch.object(watchdog, "systemd_probe", return_value={
            "issues": ["systemd_service_failed"], "warnings": [], "active_state": "failed",
        }):
            report = watchdog.build_report(self.args)
        self.assertEqual(report["health_status"], "unhealthy")
        self.assertIn("restart test-listener.service", report["alert"]["message_preview"])

    def test_internal_or_state_error_is_not_a_transient_network_error(self):
        for status in ["loop_error", "state_error", "send_failed"]:
            self.loop(status, 1, 10)
            self.args.dry_run = True
            self.assertEqual(watchdog.build_report(self.args)["health_status"], "unhealthy")

    def test_failure_reminder_is_rate_limited(self):
        self.open_incident()
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "rate_limited")
        self.assertEqual(self.send.call_count, 1)

    def test_recovery_is_sent_once_for_a_delivered_incident_across_reloads(self):
        self.open_incident()
        self.loop()
        report = watchdog.build_report(self.args)
        self.assertEqual(report["alert"]["kind"], "recovery")
        self.assertTrue(report["alert"]["sent"])
        self.assertIn("별도 확인", report["alert"]["message_preview"])
        self.assertIsNone(self.state()["active_incident"])
        watchdog.build_report(self.args)
        self.assertEqual(self.send.call_count, 2)

    def test_degraded_does_not_prematurely_close_an_incident(self):
        self.open_incident()
        self.loop("poll_error", 1, 10)
        report = watchdog.build_report(self.args)
        self.assertEqual(report["health_status"], "degraded")
        self.assertFalse(report["alert"]["needed"])
        self.assertTrue(self.state()["active_incident"]["delivered"])
        self.assertEqual(self.send.call_count, 1)

    def test_healthy_start_does_not_send_unsolicited_recovery(self):
        self.args.state_file.write_text(json.dumps({"schema": watchdog.STATE_SCHEMA,
            "last_health_status": "unhealthy", "last_alert_key": "last_poll_transport_error"}))
        before = self.args.state_file.read_bytes()
        report = watchdog.build_report(self.args)
        self.assertFalse(report["alert"]["needed"])
        self.assertEqual(before, self.args.state_file.read_bytes())
        self.send.assert_not_called()

    def test_dry_run_has_no_delivery_or_state_mutation(self):
        self.open_incident()
        self.send.reset_mock()
        before = self.args.state_file.read_bytes()
        self.loop()
        self.args.dry_run = True
        report = watchdog.build_report(self.args)
        self.assertEqual(report["alert"]["kind"], "recovery")
        self.assertEqual(report["alert"]["reason"], "dry_run")
        self.assertEqual(before, self.args.state_file.read_bytes())
        self.send.assert_not_called()

    def test_definite_recovery_refusal_is_rate_limited_and_keeps_incident(self):
        self.open_incident()
        self.loop()
        self.send.side_effect = watchdog.WatchdogError("explicit API refusal")
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "send_failed")
        self.assertTrue(self.state()["active_incident"]["delivered"])
        self.send.reset_mock()
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "rate_limited")
        self.send.assert_not_called()

    def test_uncertain_recovery_is_held_without_automatic_resend(self):
        self.open_incident()
        self.loop()
        self.send.side_effect = watchdog.WatchdogDeliveryUnconfirmed("timeout")
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "delivery_unconfirmed")
        state = self.state()
        state["delivery_attempt"]["at"] = "2000-01-01T00:00:00+00:00"
        self.args.state_file.write_text(json.dumps(state))
        self.send.reset_mock()
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "delivery_unconfirmed_hold")
        self.send.assert_not_called()

    def test_interruption_after_attempt_write_does_not_repeat_recovery(self):
        self.open_incident()
        self.loop()
        self.send.side_effect = SystemExit("interrupted")
        with self.assertRaises(SystemExit):
            watchdog.build_report(self.args)
        self.send.reset_mock()
        self.assertEqual(watchdog.build_report(self.args)["alert"]["reason"], "delivery_unconfirmed_hold")
        self.send.assert_not_called()

    def test_uncertain_failure_alert_is_not_automatically_repeated(self):
        self.loop("poll_error", 5, 300)
        self.send.side_effect = watchdog.WatchdogDeliveryUnconfirmed("timeout")
        report = watchdog.build_report(self.args)
        self.assertEqual(report["alert"]["reason"], "delivery_unconfirmed")
        self.assertEqual(report["delivery_attention"], "delivery_unconfirmed_hold")
        self.assertNotIn("active_incident", self.state())
        self.send.reset_mock()
        report = watchdog.build_report(self.args)
        self.assertEqual(report["alert"]["reason"], "delivery_unconfirmed_hold")
        self.assertEqual(report["delivery_attention"], "delivery_unconfirmed_hold")
        self.send.assert_not_called()

    def test_unreadable_delivery_state_does_not_send_duplicate_alert(self):
        self.loop("poll_error", 5, 300)
        self.args.state_file.write_text("{broken")
        report = watchdog.build_report(self.args)
        self.assertEqual(report["health_status"], "unhealthy")
        self.assertEqual(report["alert"]["reason"], "watchdog_state_unreadable")
        self.assertEqual(self.args.state_file.read_text(), "{broken")
        self.send.assert_not_called()

    def test_atomic_state_write_preserves_previous_record_on_replace_failure(self):
        self.args.state_file.write_text('{"schema":"previous"}')
        with patch.object(watchdog.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                watchdog.write_json(self.args.state_file, {"schema": "next"})
        self.assertEqual(self.state(), {"schema": "previous"})

    def test_positive_delivery_receipt_and_explicit_refusal(self):
        self.send_patcher.stop()
        config = {"token": "fake", "target_chat_id": "fake"}
        with patch.object(watchdog.urllib.request, "urlopen", return_value=io.BytesIO(
            b'{"ok":true,"result":{"message_id":73}}',
        )):
            self.assertEqual(watchdog.send_alert(config, "test", self.args), 73)
        with patch.object(watchdog.urllib.request, "urlopen", return_value=io.BytesIO(b'{"ok":false}')):
            with self.assertRaises(watchdog.WatchdogError) as caught:
                watchdog.send_alert(config, "test", self.args)
            self.assertNotIsInstance(caught.exception, watchdog.WatchdogDeliveryUnconfirmed)

    def test_degraded_check_exits_successfully_without_marking_service_failed(self):
        self.loop("poll_error", 1, 10)
        with patch.object(watchdog, "parse_args", return_value=self.args), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(watchdog.main(), 0)

    def test_delivery_uncertainty_is_redacted_and_requires_a_receipt(self):
        self.send_patcher.stop()
        for failure in [TimeoutError("secret"), urllib.error.URLError("secret")]:
            with self.subTest(failure=failure), patch.object(watchdog.urllib.request, "urlopen", side_effect=failure):
                with self.assertRaises(watchdog.WatchdogDeliveryUnconfirmed) as caught:
                    watchdog.telegram_api("secret-token", "sendMessage", {}, 1)
                self.assertNotIn("secret", str(caught.exception))
        for body in [b"not json", b"[]", b'{"ok":1}', b'{"ok":true}']:
            with self.subTest(body=body), patch.object(watchdog.urllib.request, "urlopen", return_value=io.BytesIO(body)):
                with self.assertRaises(watchdog.WatchdogDeliveryUnconfirmed):
                    watchdog.send_alert({"token": "fake", "target_chat_id": "fake"}, "test", self.args)


if __name__ == "__main__":
    unittest.main()
