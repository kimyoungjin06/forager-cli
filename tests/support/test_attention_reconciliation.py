"""Offline transport parity, decision versions and exact watchdog hold safety."""
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from telegram_operator.notifier import (attention_summary, build_attention_notification,
    commit_attention_notification, select_items_to_notify)
from telegram_operator.transport_health import classify_transport_issues
from offdesk_remote_operator_watchdog import classify_listener_issues
from telegram_delivery_holds import watchdog_snapshot, release_watchdog, release, snapshot


class AttentionVersions(unittest.TestCase):
    def surface(self, version="v1", review=True):
        return {"projects":[{"project_key":"nano", "task_items":[{"task_id":"task", "request_id":"run", "status":"completed",
            "requires_operator_review":review, "next_safe_action_kind":"review_required", "attention_version":version}]}]}

    def test_summary_and_push_agree_completed_task_has_inspection_hint(self):
        surface = self.surface()
        summary = attention_summary(surface)
        message = build_attention_notification(surface, {}, reminder_sec=21600)
        self.assertEqual(summary["items"], message["fresh_items"])
        self.assertEqual(summary["top"]["command_hint"], "/tasks")
        self.assertNotIn("cancel", str(summary))
        self.assertEqual(attention_summary(self.surface(review=False))["total"], 0)

    def test_same_version_reminds_changes_and_reopening_notify_without_early_commit(self):
        state = {}
        with patch("telegram_operator.notifier.utc_now", return_value="2026-10-19T00:00:00Z"):
            first = build_attention_notification(self.surface(), state, reminder_sec=21600)
        self.assertEqual(state, {})
        commit_attention_notification(state, first, delivered_keys=("task:task",))
        before = copy.deepcopy(state)
        for at, expected in (("2026-10-19T05:59:59Z",0), ("2026-10-19T06:00:00Z",1)):
            with patch("telegram_operator.notifier.utc_now", return_value=at):
                result = build_attention_notification(self.surface(), state, reminder_sec=21600)
                self.assertEqual(len(result["fresh_items"]), expected)
                self.assertEqual(state, before)
        with patch("telegram_operator.notifier.utc_now", return_value="2026-10-19T00:01:00Z"):
            changed = build_attention_notification(self.surface("v2"), state, reminder_sec=21600)
        self.assertEqual(len(changed["fresh_items"]), 1)
        self.assertEqual(state, before)
        resolved = build_attention_notification(self.surface(review=False), state, reminder_sec=21600)
        commit_attention_notification(state, resolved, delivered_keys=())
        self.assertEqual(state["attention_notified_by_key"], {})
        self.assertEqual(len(build_attention_notification(self.surface(), state, reminder_sec=21600)["fresh_items"]), 1)

    def test_transport_thresholds_are_shared_and_other_faults_have_no_grace(self):
        for count, age, degraded in ((1,10,True),(3,179,True),(2,180,True),(3,180,False),(None,10,False),(1,None,False)):
            listener = {"issues":["last_poll_transport_error"], "consecutive_error_count":count,"failure_age_sec":age}
            expected = classify_transport_issues(listener["issues"],count=count,age=age)
            self.assertEqual(classify_listener_issues(listener),expected)
            self.assertEqual(bool(expected[1]),degraded)
        self.assertEqual(classify_transport_issues(["last_poll_transport_error","last_send_transport_error"],count=1,age=1)[1],[])


class WatchdogHolds(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.state,self.outbox,self.watchdog,self.lock=[self.root/name for name in ("state","outbox","watchdog.json","listener.lock")]
        self.state.write_text('{"notification_unconfirmed_events":{"legacy-digest":{}}}')
        self.outbox.write_text("")
        self.lock.touch()
        self.wlock=self.watchdog.with_suffix(".json.lock")
        self.wlock.touch()
        self.watchdog.write_text(json.dumps({"schema":"remote_operator_telegram_watchdog_state.v1",
            "delivery_attempt":{"at":"2026-10-19T00:00:00Z","kind":"failure","key":"poll_error","status":"unconfirmed"}}))

    def release(self, **kw):
        view,_=watchdog_snapshot(self.state,self.outbox,self.watchdog)
        options=dict(key=view["watchdog"][0]["key"],expected=view["snapshot_sha256"],reason="verified local receipt evidence",confirmed=True)
        options.update(kw)
        return release_watchdog(self.state,self.outbox,self.watchdog,self.lock,**options)

    def test_inspect_release_is_scoped_audited_and_sends_nothing(self):
        original=self.state.read_bytes(),self.outbox.read_bytes()
        before=self.watchdog.read_bytes()
        watchdog_snapshot(self.state,self.outbox,self.watchdog)
        self.assertEqual(before,self.watchdog.read_bytes())
        self.assertFalse(self.release()["sent"])
        row=json.loads(self.watchdog.read_text())
        self.assertEqual(row["delivery_attempt"]["status"],"retry_permitted")
        self.assertEqual(len(row["delivery_reconciliations"]),1)
        self.assertEqual(original,(self.state.read_bytes(),self.outbox.read_bytes()))
        with self.assertRaises(ValueError): self.release()

    def test_stale_unknown_acknowledged_and_locked_refuse_without_writes(self):
        before=self.watchdog.read_bytes()
        for kw in ({"expected":"stale"},{"key":"wrong"},{"confirmed":False}):
            with self.assertRaises(ValueError): self.release(**kw)
        for lockpath in (self.lock,self.wlock):
            with lockpath.open("r+") as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError): self.release()
        self.assertEqual(before,self.watchdog.read_bytes())
        row=json.loads(before)
        row["delivery_attempt"]["message_id"]=73
        self.watchdog.write_text(json.dumps(row))
        with self.assertRaises(ValueError): self.release()
        row["delivery_attempt"]["status"]="unknown"
        self.watchdog.write_text(json.dumps(row))
        with self.assertRaises(ValueError): self.release()

    def test_digest_only_proactive_hold_stays_held(self):
        before=self.state.read_bytes()
        with self.assertRaises(ValueError):
            release(self.state,self.outbox,self.lock,scope="proactive",key="legacy-digest",
                expected=snapshot(self.state,self.outbox)[0]["snapshot_sha256"],reason="unsupported inference",confirmed=True)
        self.assertEqual(before,self.state.read_bytes())


if __name__ == "__main__": unittest.main()
