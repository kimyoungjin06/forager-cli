import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from telegram_delivery_holds import snapshot, release


class DeliveryHolds(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.state, self.outbox, self.lock = root / "state", root / "outbox", root / "lock"
        self.key = hashlib.sha256(b"waiting:s:p:b").hexdigest()
        self.state.write_text(json.dumps({"notification_unconfirmed_events": {self.key: {}},
            "waiting_notified_by_session": {"s": {"delivery_unconfirmed": True,
                "prompt_identity": "p", "remote_session_identity": {"binding_sha256": "b"}}}}))
        self.outbox.write_text("")
        self.lock.touch()

    def run_release(self, **kwargs):
        options = dict(scope="proactive", key=self.key,
                       expected=snapshot(self.state, self.outbox)[0]["snapshot_sha256"],
                       reason="checked Telegram history", confirmed=True)
        options.update(kwargs)
        return release(self.state, self.outbox, self.lock, **options)

    def test_inspection_is_read_only_and_release_clears_both_gates(self):
        before = self.state.read_bytes(), self.outbox.read_bytes()
        snapshot(self.state, self.outbox)
        self.assertEqual(before, (self.state.read_bytes(), self.outbox.read_bytes()))
        self.assertFalse(self.run_release()["sent"])
        state = json.loads(self.state.read_text())
        self.assertEqual(state["notification_unconfirmed_events"], {})
        self.assertEqual(state["waiting_notified_by_session"], {})
        self.assertEqual(len(state["notification_hold_reconciliations"]), 1)

    def test_stale_unconfirmed_missing_and_active_lock_refuse_without_writes(self):
        before = self.state.read_bytes(), self.outbox.read_bytes()
        for kwargs in ({"expected": "stale"}, {"confirmed": False}, {"reason": ""}, {"key": "missing"}):
            with self.assertRaises(ValueError):
                self.run_release(**kwargs)
        with self.lock.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                self.run_release()
        self.assertEqual(before, (self.state.read_bytes(), self.outbox.read_bytes()))

    def test_reply_release_preserves_attempt_count_and_appends_audit(self):
        row = {"schema": "remote_operator_telegram_reply_outbox.v1", "delivery_id": "telegram_update:1",
               "status": "failed", "update_id": 1, "input_hash": "i", "payload_hash": "p",
               "delivery_unconfirmed": True, "delivery_inflight": True, "attempt_count": 2}
        self.outbox.write_text(json.dumps(row) + "\n")
        self.run_release(scope="replies", key=row["delivery_id"])
        rows = [json.loads(x) for x in self.outbox.read_text().splitlines()]
        self.assertEqual(rows[0], row)
        self.assertEqual(rows[1]["attempt_count"], 2)
        self.assertFalse(rows[1].get("delivery_unconfirmed"))
        self.assertFalse(rows[1]["delivery_inflight"])
        self.assertIn("delivery_reconciliation", rows[1])

    def test_acknowledged_session_cannot_be_retried(self):
        state = json.loads(self.state.read_text())
        state["waiting_notified_by_session"]["s"]["message_id"] = 73
        self.state.write_text(json.dumps(state))
        before = self.state.read_bytes()
        with self.assertRaises(ValueError):
            self.run_release()
        self.assertEqual(before, self.state.read_bytes())


if __name__ == "__main__":
    unittest.main()
