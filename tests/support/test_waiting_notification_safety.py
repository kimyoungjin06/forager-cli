"""Offline regressions. No real tmux, provider, or Telegram calls are allowed."""

import argparse
import errno
import http.client
import io
import json
import socket
import ssl
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import offdesk_remote_operator_telegram as operator
from telegram_operator import dispatch, transport, reply_outbox
from telegram_operator.remote_session_identity import signed_identity, notification_capture_tail
from telegram_operator.notification_evidence import (
    EVIDENCE_KEY, MAX_RECORDS, MAX_AGE_SECONDS, payload_evidence, record_delivery,
)
from telegram_operator.notification_policy import NotificationCycle, NotificationCandidate, NotificationPriority
from telegram_operator.common import RemoteOperatorTelegramError


PASSIVE = """No action is required. Codex will keep waiting.
› 1. Dismiss and keep waiting
  2. Interrupt"""
MENU = """Which target should I use?
› 1. Development
  2. Production
Enter to select"""


class WaitingNotificationSafety(unittest.TestCase):
    def test_claude_arrow_menu_uses_cursor_relative_keys(self):
        tail = ("DB 컬럼명 철자를 어떻게 할까요?\n"
                "❯ 1. 연도로 통일\n  2. DB만 년도 유지\n  3. 뷰로 양쪽 제공\n"
                "Enter to select · ↑/↓ to navigate · Tab to switch questions")
        status = {"sessions": [{"id": "abc12345", "tool": "claude", "status": "waiting"}]}
        cases = [
            (tail, "1", "approve", ["Enter"], ""),
            (tail, "3", "approve", ["Down", "Down", "Enter"], ""),
            (tail.replace("❯ 1.", "  1.").replace("  3.", "❯ 3."),
             "1", "approve", ["Up", "Up", "Enter"], ""),
            (tail.replace("❯", " "), "1", "approve", [], "selection_cursor_unavailable"),
            (tail, "4", "approve", [], "selection_cursor_unavailable"),
            (tail, "", "deny", ["Escape"], ""),
        ]
        for pane, option, action, keys, error in cases:
            with self.subTest(option=option, action=action, keys=keys), \
                    patch.object(dispatch, "run_forager_json", return_value=status), \
                    patch.object(dispatch, "find_tmux_session_name", return_value="isolated"), \
                    patch.object(dispatch, "capture_session_tail", return_value=pane), \
                    patch.object(dispatch, "pane_settled_after_input", return_value=(True, "changed")), \
                    patch.object(dispatch.subprocess, "run") as send:
                result = dispatch.apply_session_input("unused", "test", "abc12345", action,
                    option_key=option, expected_prompt_identity=dispatch.session_prompt_identity(pane, "claude"))
                self.assertEqual(result.get("error", ""), error)
                if error:
                    send.assert_not_called()
                else:
                    self.assertTrue(result["ok"])
                    self.assertEqual(send.call_args.args[0], ["tmux", "send-keys", "-t", "isolated", *keys])
        with patch.object(dispatch, "run_forager_json", return_value=status), \
                patch.object(dispatch, "find_tmux_session_name", return_value="isolated"), \
                patch.object(dispatch, "capture_session_tail", return_value=tail), \
                patch.object(dispatch, "pane_settled_after_input", return_value=(False, "same")), \
                patch.object(dispatch.subprocess, "run") as send:
            stale = dispatch.apply_session_input("unused", "test", "abc12345", "approve",
                option_key="1", expected_prompt_identity="stale")
            self.assertEqual(stale["error"], "prompt_changed")
            send.assert_not_called()
            unchanged = dispatch.apply_session_input("unused", "test", "abc12345", "approve",
                option_key="1", expected_prompt_identity=dispatch.session_prompt_identity(tail, "claude"))
            self.assertEqual(unchanged["error"], "no_effect")
            self.assertEqual(send.call_count, 1)

    def test_task_ready_episode_updates_one_card_and_rearms_only_after_running(self):
        identity = signed_identity({
            "schema": "remote_session_identity.v1",
            **{key: "a" * 64 for key in (
                "machine_identity_sha256", "project_root_identity_sha256",
                "worktree_identity_sha256", "harness_launch_sha256",
                "session_record_identity_sha256", "transport_session_identity_sha256",
                "pane_identity_sha256", "process_identity_sha256",
            )},
            "project_key": "test", "worktree_kind": "direct_project_root",
            "harness_kind": "codex", "session_id": "abc12345", "transport_kind": "tmux",
        })
        with tempfile.TemporaryDirectory() as directory:
            status_file = Path(directory) / "status.json"
            args = argparse.Namespace(session_notify=True, dry_run=False,
                session_status_file=status_file, session_notify_quiet_from_hour=0,
                session_notify_quiet_to_hour=0, session_notify_backoff_sec=1800,
                session_notify_max_backoff_sec=14400, session_notify_max_cards=4)
            state = {"telegram_session_keyboard_schema": "inline_v3"}
            deliveries = []
            def scan(status, tail, now, edit_ok=True):
                status_file.write_text(json.dumps({"sessions": [{
                    "id": "abc12345", "tool": "codex", "status": status,
                    "remote_session_identity": identity,
                    "notification_capture": {"schema":"notification_capture.v1",
                        "binding_sha256":identity["binding_sha256"], "tail":tail},
                }]}))
                def send(candidate):
                    deliveries.append(candidate)
                    return len(deliveries) + 70
                cycle = NotificationCycle(send, dry_run=False)
                with patch.object(operator.time, "time", return_value=now), patch.object(operator, "summarize_waiting_prompt", return_value="fixture"), patch.object(operator, "edit_message", return_value=edit_ok) as edit:
                    result = operator.scan_and_notify_waiting_sessions(args, {"target_chat_id":"fake"}, state, cycle=cycle)
                    cycle.deliver()
                    return result, edit.call_count
            scan("waiting", "Completed first task.\n›", 1000)
            self.assertEqual(len(deliveries), 1)
            original = state["waiting_notified_by_session"]["abc12345"]["prompt_identity"]
            result, edits = scan("waiting", "Completed first task with changed wording.\n›", 1070)
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(edits, 1)
            self.assertEqual(result["muted"][0]["reason"], "same_task_ready_episode")
            updated = state["waiting_notified_by_session"]["abc12345"]["prompt_identity"]
            self.assertNotEqual(original, updated)
            scan("waiting", "Another repaint.\n›", 1140, edit_ok=False)
            self.assertEqual(state["waiting_notified_by_session"]["abc12345"]["prompt_identity"], updated)
            scan("idle", "", 1300)
            scan("waiting", "Another repaint.\n›", 2000)
            self.assertEqual(len(deliveries), 1)
            scan("running", "", 2070)
            scan("waiting", "Another repaint.\n›", 2140)
            self.assertEqual(len(deliveries), 1)
            scan("running", "", 2210)
            scan("running", "", 2280)
            scan("waiting", "Completed second task.\n›", 2350)
            self.assertEqual(len(deliveries), 2)
            state["waiting_notified_by_session"]["abc12345"]["task_ready_input_confirmed"] = True
            scan("waiting", "Completed very short third task.\n›", 2380)
            self.assertEqual(len(deliveries), 2)
            scan("waiting", "Completed very short third task.\n›", 2420)
            self.assertEqual(len(deliveries), 3)
            identity = signed_identity({**identity, "process_identity_sha256": "b" * 64})
            scan("waiting", "Completed very short third task.\n›", 2500)
            self.assertEqual(len(deliveries), 4)
            scan("waiting", MENU, 2580)
            self.assertEqual(len(deliveries), 5)

    def proactive_cycle(self, state, callback=lambda _: None):
        args = argparse.Namespace(dry_run=False, state_file=self.state_file)
        cycle = operator._standalone_notification_cycle(args, {}, state)
        cycle.add(NotificationCandidate(
            event_key="crash-test", source="session", priority=NotificationPriority.INTERACTION,
            message="private test", reply_markup=None, report={}, on_delivered=callback,
        ))
        return cycle

    def test_proactive_pre_send_write_failure_prevents_network(self):
        state = {}
        with patch.object(operator, "save_state", side_effect=OSError("disk failure")), patch.object(operator, "send_message") as send:
            cycle = self.proactive_cycle(state)
            with self.assertRaises(OSError):
                cycle.deliver()
            send.assert_not_called()
            self.assertEqual(cycle.deliver()["attempted_count"], 0)
        with patch.object(operator, "send_message") as send:
            self.assertEqual(self.proactive_cycle(state).deliver()["status"], "delivery_unconfirmed_hold")
            send.assert_not_called()

    def test_proactive_crash_retains_durable_hold_without_success_commit(self):
        state, committed = {}, []
        def crash(*args, **kwargs):
            stored = json.loads(self.state_file.read_text())
            self.assertEqual(len(stored["notification_unconfirmed_events"]), 1)
            self.assertNotIn("private test", self.state_file.read_text())
            raise SystemExit("injected crash")
        with patch.object(operator, "send_message", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.proactive_cycle(state, committed.append).deliver()
        self.assertEqual(committed, [])
        reloaded = json.loads(self.state_file.read_text())
        with patch.object(operator, "send_message") as send:
            self.assertEqual(self.proactive_cycle(reloaded).deliver()["status"], "delivery_unconfirmed_hold")
            send.assert_not_called()

    def test_proactive_commit_failure_holds_in_memory_and_on_disk(self):
        for callback_fails in (False, True):
            with self.subTest(callback_fails=callback_fails):
                state = {}
                real_save = operator.save_state
                saves = []
                def save(path, value):
                    saves.append(True)
                    if len(saves) == 2:
                        raise OSError("final checkpoint failed")
                    real_save(path, value)
                def commit(mid):
                    if callback_fails:
                        raise ValueError("callback failed")
                    state["acknowledged_message_id"] = mid
                with patch.object(operator, "save_state", side_effect=save), patch.object(operator, "send_message", return_value=73):
                    with self.assertRaises((OSError, ValueError)):
                        self.proactive_cycle(state, commit).deliver()
                reloaded = json.loads(self.state_file.read_text())
                self.assertNotIn("acknowledged_message_id", reloaded)
                with patch.object(operator, "send_message") as send:
                    for candidate_state in (state, reloaded):
                        self.assertEqual(self.proactive_cycle(candidate_state).deliver()["status"], "delivery_unconfirmed_hold")
                    send.assert_not_called()

    def test_proactive_success_persists_bookkeeping_with_hold_removal(self):
        state = {}
        with patch.object(operator, "send_message", return_value=73):
            result = self.proactive_cycle(state, lambda mid: state.update(acknowledged_message_id=mid)).deliver()
        stored = json.loads(self.state_file.read_text())
        self.assertEqual(result["delivered_count"], 1)
        self.assertEqual(stored["acknowledged_message_id"], 73)
        self.assertEqual(stored["notification_unconfirmed_events"], {})

    def test_new_cursor_discards_old_menu_and_permission(self):
        for old in (MENU, PASSIVE, "Permission required\n› 1. Yes, allow\n2. No"):
            with self.subTest(old=old):
                result = dispatch.classify_session_interaction(old + "\nDone.\n›", "codex")
                self.assertEqual(result["kind"], "task_ready")
                self.assertEqual(result["options"], [])

    def test_new_menu_question_discards_old_permission_and_trust(self):
        for old in ("Permission required", "Do you trust the contents of this directory?\n› 1. Yes\n2. No"):
            with self.subTest(old=old):
                result = dispatch.classify_session_interaction(old + "\n" + MENU, "codex")
                self.assertEqual(result["kind"], "selection")
        permission = MENU + "\nRun this command?\n› 1. Yes, allow\n2. No"
        self.assertEqual(dispatch.classify_session_interaction(permission, "codex")["kind"], "permission")

    def test_reply_crash_before_receipt_is_held_and_write_failure_prevents_send(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.jsonl"
            args = argparse.Namespace(reply_outbox_file=path, dry_run=False)
            record = reply_outbox.queue_reply(
                path, update_id=1, input_hash="input", chat_id="chat", message="test",
                reply_markup=None, result={"status": "ok"}, control_generation_sha256="generation",
            )
            with patch.object(reply_outbox, "append_jsonl", side_effect=OSError("write failure")), patch.object(operator, "send_message") as send:
                with self.assertRaises(reply_outbox.RemoteOperatorStateError):
                    operator.attempt_reply_delivery(args, {}, record)
                send.assert_not_called()
            def crash(*args, **kwargs):
                durable = reply_outbox.latest_delivery_records(path)[record["delivery_id"]]
                self.assertTrue(durable["delivery_unconfirmed"])
                self.assertTrue(durable["delivery_inflight"])
                raise SystemExit("injected process boundary")
            with patch.object(operator, "send_message", side_effect=crash):
                with self.assertRaises(SystemExit):
                    operator.attempt_reply_delivery(args, {}, record)
            self.assertEqual(reply_outbox.pending_deliveries(path), [])
            with patch.object(operator, "send_message") as send:
                self.assertEqual(operator.attempt_reply_delivery(args, {}, record)["status"], "delivery_unconfirmed")
                send.assert_not_called()

    def test_reply_refusal_releases_inflight_hold_and_counts_attempts_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.jsonl"
            args = argparse.Namespace(reply_outbox_file=path, dry_run=False)
            record = reply_outbox.queue_reply(
                path, update_id=1, input_hash="input", chat_id="chat", message="test",
                reply_markup=None, result={"status": "ok"}, control_generation_sha256="generation",
            )
            with patch.object(operator, "send_message", side_effect=RemoteOperatorTelegramError("refused")):
                operator.attempt_reply_delivery(args, {}, record)
            pending = reply_outbox.pending_deliveries(path)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["attempt_count"], 1)
            with patch.object(operator, "send_message", return_value=73):
                delivered = operator.attempt_reply_delivery(args, {}, pending[0])["delivery"]
            self.assertEqual(delivered["attempt_count"], 2)
            self.assertFalse(delivered.get("delivery_unconfirmed"))

    def test_old_passive_text_does_not_hide_new_menu(self):
        for prefix in ("Earlier output: Dismiss and keep waiting", PASSIVE):
            result = dispatch.classify_session_interaction(prefix + "\n" + MENU, "codex")
            self.assertEqual(result["kind"], "selection")
            self.assertEqual([item["label"] for item in result["options"]], ["Development", "Production"])
        self.assertEqual(dispatch.classify_session_interaction(MENU + "\n" + PASSIVE, "codex")["kind"], "passive_wait")

    def test_latest_options_do_not_inherit_old_keys(self):
        text = "› 1. Old\n2. Other\n3. Stale\n4. Stale\n" + MENU
        self.assertEqual(len(dispatch.parse_prompt_options(text)), 2)

    def test_uncertain_reply_survives_reload_compaction_and_does_not_block_other_replies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.jsonl"
            args = argparse.Namespace(reply_outbox_file=path, dry_run=False)
            def queue(update_id):
                return reply_outbox.queue_reply(
                    path, update_id=update_id, input_hash=str(update_id), chat_id="chat",
                    message="test", reply_markup=None, result={"status": "ok"},
                    control_generation_sha256="generation",
                )
            first = queue(1)
            with patch.object(operator, "send_message", side_effect=transport.TelegramDeliveryUnconfirmed("timeout")) as send:
                result = operator.attempt_reply_delivery(args, {}, first)
                self.assertEqual(result["status"], "delivery_unconfirmed")
                self.assertEqual(send.call_count, 1)
            second = queue(2)
            self.assertEqual(reply_outbox.pending_deliveries(path), [second])
            self.assertEqual(reply_outbox.outbox_health_issue(path), "reply_delivery_unconfirmed")
            self.assertEqual(reply_outbox.outbox_inspection(path)["unconfirmed_count"], 1)
            reply_outbox.compact_reply_outbox(path, committed_before_update_id=99)
            with patch.object(operator, "send_message", return_value=73) as send:
                self.assertEqual(operator.attempt_reply_delivery(args, {}, first)["status"], "delivery_unconfirmed")
                send.assert_not_called()
                self.assertEqual(operator.attempt_reply_delivery(args, {}, second)["status"], "delivered")
                self.assertEqual(send.call_count, 1)
            revoked = reply_outbox.revoke_mismatched_deliveries(path, current_generation_sha256="new")
            self.assertEqual([item["update_id"] for item in revoked], [1])
            self.assertIsNone(reply_outbox.outbox_health_issue(path))

    def test_transport_uncertainty_is_typed_and_redacted(self):
        for failure in (
            TimeoutError("private-token"), urllib.error.URLError("private-token"),
            OSError("private-token"),
            http.client.RemoteDisconnected("private-token"),
            http.client.IncompleteRead(b"private-token", 100),
            urllib.error.HTTPError("private-token", 502, "bad", {}, io.BytesIO(b"secret")),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(transport.urllib.request, "urlopen", side_effect=failure):
                    with self.assertRaises(transport.TelegramDeliveryUnconfirmed) as caught:
                        transport.telegram_api("token", "sendMessage", {}, 1)
                self.assertNotIn("private-token", str(caught.exception))
                self.assertNotIn("secret", str(caught.exception))
        for body in (b"not json", b"\xff", b"[]", b'{"ok": 1}', b'{}'):
            with self.subTest(body=body):
                with patch.object(transport.urllib.request, "urlopen", return_value=io.BytesIO(body)):
                    with self.assertRaises(transport.TelegramDeliveryUnconfirmed):
                        transport.telegram_api("token", "sendMessage", {}, 1)
        with patch.object(transport.urllib.request, "urlopen", side_effect=TimeoutError()):
            with self.assertRaises(RemoteOperatorTelegramError) as caught:
                transport.telegram_api("token", "getUpdates", {}, 1)
            self.assertNotIsInstance(caught.exception, transport.TelegramDeliveryUnconfirmed)

    def test_url_failures_preserve_safe_diagnostic_categories(self):
        cases = (
            (socket.gaierror(-2, "private-token"), "dns_error"),
            (TimeoutError("private-token"), "timeout"),
            (ssl.SSLCertVerificationError(1, "private-token"), "tls_certificate_error"),
            (ssl.SSLError(1, "private-token"), "tls_error"),
            (ConnectionRefusedError("private-token"), "connection_refused"),
            (ConnectionResetError("private-token"), "connection_reset"),
            (OSError(errno.ENETUNREACH, "private-token"), "network_unreachable"),
            ("private-token", "url_error"),
        )
        for cause, category in cases:
            with self.subTest(category=category), patch.object(
                transport.urllib.request, "urlopen", side_effect=urllib.error.URLError(cause),
            ):
                with self.assertRaises(RemoteOperatorTelegramError) as caught:
                    transport.telegram_api("secret-token", "getUpdates", {}, 1)
                self.assertIn(category, str(caught.exception))
                self.assertNotIn("private-token", str(caught.exception))
                self.assertNotIn("secret-token", str(caught.exception))
                self.assertNotIsInstance(caught.exception, transport.TelegramDeliveryUnconfirmed)

    def test_explicit_api_refusal_is_not_uncertain(self):
        with patch.object(transport.urllib.request, "urlopen", return_value=io.BytesIO(b'{"ok":false}')):
            with self.assertRaises(RemoteOperatorTelegramError) as caught:
                transport.telegram_api("token", "sendMessage", {}, 1)
            self.assertNotIsInstance(caught.exception, transport.TelegramDeliveryUnconfirmed)

    def test_send_requires_positive_non_boolean_receipt(self):
        args = argparse.Namespace(dry_run=False, api_timeout_sec=1)
        with patch.object(transport, "assert_telegram_config_current"):
            for value in (True, False, 0, -1, "73", None):
                with self.subTest(value=value), patch.object(
                    transport, "telegram_api", return_value={"ok": True, "result": {"message_id": value}},
                ):
                    with self.assertRaises(transport.TelegramDeliveryUnconfirmed):
                        transport.send_message({"token": "test"}, "test", "test", args)

    def test_shared_uncertainty_survives_reload_for_all_proactive_sources(self):
        args = argparse.Namespace(dry_run=False, state_file=self.state_file)
        for source in ("session", "attention", "autonomy", "bootstrap"):
            with self.subTest(source=source):
                state, committed = {}, []
                def candidate(key):
                    return NotificationCandidate(
                        event_key=key, source=source, priority=NotificationPriority.INTERACTION,
                        message="private message", reply_markup=None, report={},
                        on_delivered=committed.append,
                    )
                with patch.object(operator, "send_message", side_effect=transport.TelegramDeliveryUnconfirmed("timeout")) as send:
                    cycle = operator._standalone_notification_cycle(args, {}, state)
                    cycle.add(candidate("a"))
                    self.assertEqual(cycle.deliver()["status"], "delivery_unconfirmed")
                    self.assertEqual(send.call_count, 1)
                state = json.loads(json.dumps(state))
                self.assertNotIn("private message", json.dumps(state))
                with patch.object(operator, "send_message", return_value=73) as send:
                    cycle = operator._standalone_notification_cycle(args, {}, state)
                    cycle.add(candidate("a"))
                    self.assertEqual(cycle.deliver()["status"], "delivery_unconfirmed_hold")
                    send.assert_not_called()
                    cycle = operator._standalone_notification_cycle(args, {}, state)
                    cycle.add(candidate("a"))
                    cycle.add(candidate("b"))
                    result = cycle.deliver()
                    self.assertEqual(result["held_count"], 1)
                    self.assertEqual(result["delivered_count"], 1)
                    self.assertEqual(result["deferred_count"], 0)
                self.assertEqual(committed, [73])

    def test_uncertainty_capacity_and_corrupt_ledger_fail_closed(self):
        args = argparse.Namespace(dry_run=False, state_file=self.state_file)
        with self.assertRaises(RemoteOperatorTelegramError):
            operator._standalone_notification_cycle(args, {}, {"notification_unconfirmed_events": []})
        state = {"notification_unconfirmed_events": {str(i): {} for i in range(256)}}
        with patch.object(operator, "send_message") as send:
            cycle = operator._standalone_notification_cycle(args, {}, state)
            cycle.add(NotificationCandidate(
                event_key="new", source="session", priority=NotificationPriority.INTERACTION,
                message="test", reply_markup=None, report={}, on_delivered=lambda _: None,
            ))
            self.assertTrue(cycle.deliver()["hold_capacity_reached"])
            send.assert_not_called()
        self.assertEqual(len(state["notification_unconfirmed_events"]), 256)

    def test_refusal_retries_and_dry_run_do_not_create_uncertainty(self):
        state = {}
        args = argparse.Namespace(dry_run=False, state_file=self.state_file)
        def cycle():
            result = operator._standalone_notification_cycle(args, {}, state)
            result.add(NotificationCandidate(
                event_key="same", source="attention", priority=NotificationPriority.INTERACTION,
                message="test", reply_markup=None, report={}, on_delivered=lambda _: None,
            ))
            return result
        with patch.object(operator, "send_message", side_effect=RemoteOperatorTelegramError("refused")):
            self.assertEqual(cycle().deliver()["status"], "send_failed")
        self.assertEqual(state["notification_unconfirmed_events"], {})
        with patch.object(operator, "send_message", return_value=73):
            self.assertEqual(cycle().deliver()["delivered_count"], 1)
        args.dry_run = True
        with patch.object(operator, "send_message", return_value=None):
            self.assertEqual(cycle().deliver()["send_status"], "dry_run")
        self.assertEqual(state["notification_unconfirmed_events"], {})

    def test_unconfirmed_cycle_never_commits_or_sends_fallback(self):
        for message_id in (None, False, 0, -1, "73"):
            with self.subTest(message_id=message_id):
                calls, committed, held = [], [], []
                cycle = NotificationCycle(lambda item: calls.append(item.event_key) or message_id)
                report = {}
                for key in ("a", "b"):
                    cycle.add(NotificationCandidate(
                        event_key=key, source="session", priority=NotificationPriority.INTERACTION,
                        message="test", reply_markup=None, report=report,
                        on_delivered=lambda mid: committed.append(mid),
                        on_unconfirmed=lambda: held.append(True),
                    ))
                result = cycle.deliver()
                self.assertEqual(result["status"], "delivery_unconfirmed")
                self.assertEqual(result["delivered_count"], 0)
                self.assertEqual(report["notified_count"], 0)
                self.assertEqual(calls, ["a"])
                self.assertEqual(committed, [])
                self.assertEqual(held, [True])
                self.assertEqual(cycle.deliver(), result)
                self.assertEqual(calls, ["a"])

    def test_evidence_separates_body_buttons_without_retaining_text(self):
        body = "private-project secret-token No action is required"
        evidence = payload_evidence(body, {"inline_keyboard": [[{
            "text": "Dismiss and keep waiting", "callback_data": "private-id",
        }]]})
        self.assertTrue(evidence["body_flags"]["no_action_required"])
        self.assertFalse(evidence["body_flags"]["dismiss_and_keep_waiting"])
        self.assertTrue(evidence["button_flags"]["dismiss_and_keep_waiting"])
        self.assertNotIn("private", json.dumps(evidence))
        self.assertNotIn("secret-token", json.dumps(evidence))

    def test_evidence_requires_acknowledgement_and_deduplicates(self):
        state = {}
        evidence = payload_evidence("test", None)
        for message_id, dry_run in ((None, False), (True, False), (0, False), (10, True)):
            record_delivery(state, evidence, message_id=message_id, binding_sha256="a" * 64, now=100, dry_run=dry_run)
        self.assertNotIn(EVIDENCE_KEY, state)
        for _ in range(2):
            record_delivery(state, evidence, message_id=10, binding_sha256="a" * 64, now=100, dry_run=False)
        self.assertEqual(len(state[EVIDENCE_KEY]), 1)

    def test_evidence_retention_is_bounded(self):
        state = {}
        evidence = payload_evidence("test", None)
        for message_id in range(1, MAX_RECORDS + 4):
            record_delivery(state, evidence, message_id=message_id, binding_sha256="a" * 64, now=100, dry_run=False)
        self.assertEqual(len(state[EVIDENCE_KEY]), MAX_RECORDS)
        record_delivery(state, evidence, message_id=999, binding_sha256="a" * 64, now=101 + MAX_AGE_SECONDS, dry_run=False)
        self.assertEqual(len(state[EVIDENCE_KEY]), 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_file = Path(temporary.name) / "state.json"
        self.no_process = patch.object(
            subprocess, "run", side_effect=AssertionError("unexpected subprocess")
        )
        self.no_process.start()
        self.addCleanup(self.no_process.stop)

    def test_passive_wait_has_no_actions(self):
        interaction = dispatch.classify_session_interaction(PASSIVE, "codex")
        self.assertEqual(interaction["kind"], "passive_wait")
        self.assertEqual(interaction["options"], [])
        self.assertFalse(interaction["continuation_allowed"])
        markup = operator.session_interaction_markup(
            interaction, session_id="abc12345", prompt_hash="12345678"
        )
        self.assertEqual(len(markup["inline_keyboard"]), 1)
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "fg1:s")

    def test_numbered_report_is_not_a_menu(self):
        for suffix, expected in [("", "unknown"), ("\n›", "task_ready")]:
            interaction = dispatch.classify_session_interaction(
                "Completed work:\n1. Updated documentation\n2. Added tests" + suffix,
                "codex",
            )
            self.assertEqual(interaction["kind"], expected)
            self.assertEqual(interaction["options"], [])

    def test_actual_menu_and_permission_remain_actionable(self):
        self.assertEqual(dispatch.classify_session_interaction(MENU, "codex")["kind"], "selection")
        prompt = "Allow this command?\n1. Yes\n2. No"
        self.assertEqual(dispatch.classify_session_interaction(prompt, "codex")["kind"], "permission")

    def test_elapsed_churn_does_not_rearm_prompt(self):
        before, after = MENU + "\nElapsed: 10s", MENU + "\nElapsed: 11s"
        self.assertNotEqual(dispatch.pane_prompt_hash(before), dispatch.pane_prompt_hash(after))
        self.assertEqual(dispatch.session_prompt_identity(before, "codex"), dispatch.session_prompt_identity(after, "codex"))
        self.assertNotEqual(dispatch.session_prompt_identity(MENU, "codex"), dispatch.session_prompt_identity(MENU.replace("Production", "Staging"), "codex"))

    def capture_with(self, responses):
        with patch.object(subprocess, "run", side_effect=responses) as run:
            result = dispatch.capture_notification_tail("fake_session")
        return result, run.call_args_list

    @staticmethod
    def response(text, code=0):
        return subprocess.CompletedProcess([], code, stdout=text)

    def test_capture_pins_exact_pane(self):
        identity = self.response("fake_session|$1|%7|100|200\n")
        result, calls = self.capture_with([identity, self.response(MENU), identity])
        self.assertEqual(result, MENU)
        self.assertEqual(calls[1].args[0], ["tmux", "capture-pane", "-p", "-t", "%7"])
        self.assertIn("=fake_session", calls[0].args[0])

    def test_ambiguous_and_failed_capture_are_rejected(self):
        identity = self.response("fake_session|$1|%7|100|200\n")
        for responses in [
            [self.response(identity.stdout * 2)],
            [self.response(identity.stdout, 1)],
            [self.response("wrong_session|$1|%7|100|200\n")],
            [identity, self.response(MENU, 1)],
            [identity, self.response(MENU), self.response("fake_session|$1|%8|101|200\n")],
            [subprocess.TimeoutExpired("tmux", 10)],
        ]:
            with self.subTest(responses=responses):
                self.assertEqual(self.capture_with(responses)[0], "")

    def test_scanner_suppresses_without_summary_or_send(self):
        identity = signed_identity({
            "schema": "remote_session_identity.v1",
            **{field: "1" * 64 for field in (
                "machine_identity_sha256", "project_root_identity_sha256",
                "worktree_identity_sha256", "harness_launch_sha256",
                "session_record_identity_sha256", "transport_session_identity_sha256",
                "pane_identity_sha256", "process_identity_sha256",
            )},
            "project_key": "test", "worktree_kind": "direct_project_root",
            "harness_kind": "codex", "session_id": "abc12345", "transport_kind": "tmux",
        })
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            status.write_text(json.dumps({"sessions": [{
                "id": "abc12345", "tool": "codex", "status": "waiting",
                "remote_session_identity": identity,
            }]}))
            args = argparse.Namespace(
                session_notify=True, dry_run=True, session_status_file=status,
                session_notify_quiet_from_hour=0, session_notify_quiet_to_hour=0,
            )
            class Cycle:
                def add(self, candidate):
                    raise AssertionError("unexpected notification")
            for tail in (PASSIVE, "", "1. Finished docs\n2. Added tests"):
                payload = json.loads(status.read_text())
                payload["sessions"][0]["notification_capture"] = {
                    "schema": "notification_capture.v1",
                    "binding_sha256": identity["binding_sha256"], "tail": tail,
                }
                status.write_text(json.dumps(payload))
                with self.subTest(tail=tail), patch.object(dispatch, "find_tmux_session_name", return_value="fake_session"), patch.object(dispatch, "capture_notification_tail", return_value=tail), patch.object(operator, "summarize_waiting_prompt", side_effect=AssertionError("unexpected summary")):
                    result = operator.scan_and_notify_waiting_sessions(
                        args, {"target_chat_id": "fake"}, {}, cycle=Cycle()
                    )
                    self.assertEqual(result["status"], "no_new_waiting")
                    self.assertEqual(result["sent"], [])
                    self.assertEqual(len(result["muted"]), 1)

            row = payload["sessions"][0]
            row["notification_capture"]["tail"] = MENU
            self.assertEqual(notification_capture_tail(row), MENU)
            for field, value in (("binding_sha256", "0" * 64), ("schema", "unknown"), ("tail", 42)):
                with self.subTest(field=field):
                    original = row["notification_capture"][field]
                    row["notification_capture"][field] = value
                    self.assertEqual(notification_capture_tail(row), "")
                    row["notification_capture"][field] = original
            del row["notification_capture"]
            self.assertEqual(notification_capture_tail(row), "")

            row["notification_capture"] = {
                "schema": "notification_capture.v1",
                "binding_sha256": identity["binding_sha256"], "tail": MENU,
            }
            status.write_text(json.dumps({"sessions": [row]}))
            args.session_notify_backoff_sec = 1800
            args.session_notify_max_backoff_sec = 14400
            args.session_notify_max_cards = 4
            for outcome in ("sent", "dry_run", "failed", "no_ack"):
                args.dry_run = outcome == "dry_run"
                state = {"telegram_session_keyboard_schema": "inline_v3"}
                def send(candidate):
                    if outcome == "failed":
                        raise RemoteOperatorTelegramError("fake send failure")
                    return None if outcome == "no_ack" else 73
                cycle = NotificationCycle(send, dry_run=args.dry_run)
                with self.subTest(outcome=outcome), patch.object(operator, "summarize_waiting_prompt", return_value="Synthetic summary"):
                    result = operator.scan_and_notify_waiting_sessions(
                        args, {"target_chat_id": "fake"}, state, cycle=cycle
                    )
                    delivery = cycle.deliver()
                    if outcome == "sent":
                        self.assertEqual(len(state[EVIDENCE_KEY]), 1)
                        self.assertEqual(state[EVIDENCE_KEY][0]["message_id"], 73)
                        self.assertEqual(result["sent"][0]["delivery_evidence_status"], "acknowledged")
                        self.assertNotIn("Synthetic", json.dumps(state[EVIDENCE_KEY]))
                    else:
                        self.assertNotIn(EVIDENCE_KEY, state)
                    if outcome == "no_ack":
                        self.assertEqual(delivery["status"], "delivery_unconfirmed")
                        self.assertEqual(result["sent"], [])
                        self.assertEqual(result["notified_count"], 0)
                        self.assertTrue(state["waiting_notified_by_session"]["abc12345"]["delivery_unconfirmed"])
                        self.assertNotIn("session_focus_by_chat", state)
                        restored = json.loads(json.dumps(state))
                        later = operator.time.time() + 3600
                        with patch.object(operator.time, "time", return_value=later):
                            held = operator.scan_and_notify_waiting_sessions(
                                args, {"target_chat_id": "fake"}, restored, cycle=Cycle()
                            )
                        self.assertEqual(held["muted"][0]["reason"], "delivery_unconfirmed_hold")
                        # A genuinely different request remains deliverable.
                        row["notification_capture"]["tail"] = MENU.replace("Production", "Staging")
                        status.write_text(json.dumps({"sessions": [row]}))
                        next_cycle = NotificationCycle(lambda candidate: 74)
                        with patch.object(operator.time, "time", return_value=later):
                            operator.scan_and_notify_waiting_sessions(
                                args, {"target_chat_id": "fake"}, restored, cycle=next_cycle
                            )
                            self.assertEqual(next_cycle.deliver()["delivered_count"], 1)
                        self.assertEqual(restored[EVIDENCE_KEY][0]["message_id"], 74)


if __name__ == "__main__":
    unittest.main()
