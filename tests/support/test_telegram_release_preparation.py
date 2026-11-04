"""Offline preparation checks, with no service, Telegram or tmux access."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from telegram_rollback_guard import check
from prepare_telegram_release_bundle import prepare


class ReleasePreparation(unittest.TestCase):
    def test_guard_preserves_state_and_blocks_every_unresolved_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, outbox = Path(tmp) / "state.json", Path(tmp) / "outbox.jsonl"
            for value, rows, blocked in (
                ({}, [], False),
                ({"notification_unconfirmed_events": {"hash": {}}}, [], True),
                ({"waiting_notified_by_session": {"session": {"delivery_unconfirmed": True}}}, [], True),
                ({}, ["queued"], True),
                ({}, ["failed"], True),
                ({}, ["failed", "delivered"], False),
                ({}, ["failed", "revoked"], False),
            ):
                with self.subTest(value=value, rows=rows):
                    state.write_text(json.dumps(value))
                    outbox.write_text("".join(json.dumps({
                        "schema": "remote_operator_telegram_reply_outbox.v1",
                        "delivery_id": "test", "status": status,
                    }) + "\n" for status in rows))
                    before = state.read_bytes(), outbox.read_bytes()
                    self.assertEqual(check(state, outbox)["legacy_restart_blocked"], blocked)
                    self.assertEqual(before, (state.read_bytes(), outbox.read_bytes()))

    def test_guard_rejects_corrupt_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, outbox = Path(tmp) / "state.json", Path(tmp) / "outbox.jsonl"
            outbox.write_text("")
            for data in ("[]", "broken", '{"notification_unconfirmed_events": []}'):
                state.write_text(data)
                with self.assertRaises(ValueError):
                    check(state, outbox)
            state.write_text("{}")
            outbox.write_text('{"schema":"unknown"}\n')
            with self.assertRaises(ValueError):
                check(state, outbox)

    def test_packager_rejects_invalid_inventory_before_staging(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory = root / "inventory.json"
            inventory.write_text('{"baseline":"expected", "files": {}}')
            with patch("prepare_telegram_release_bundle.git", return_value="different"):
                with self.assertRaises(ValueError):
                    prepare(root, inventory, root, root / "output", installed_binary=inventory)
            self.assertFalse((root / "output").exists())

    def test_packager_copies_explicit_private_binary_and_refuses_reused_destination(self):
        import hashlib
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, installed = root / "source", root / "installed"
            source.mkdir()
            installed.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            (source / ".git/info/exclude").write_text("target/\n")
            subprocess.run(["git", "-C", str(source), "-c", "user.name=fixture", "-c",
                            "user.email=fixture@example.invalid", "commit", "-q", "--allow-empty",
                            "-m", "fixture baseline"], check=True)
            names = ["scripts/offdesk_remote_operator_telegram.py", "scripts/offdesk_llm_endpoint.py",
                     "scripts/telegram_operator/__init__.py"]
            for name in names:
                candidate = source / name
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_text("# inert fixture\n")
                original = installed / Path(name).relative_to("scripts")
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(candidate.read_bytes())
            binary = source / "target/release/forager"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"candidate binary")
            subprocess.run(["git", "-C", str(source), "add", "scripts"], check=True)
            baseline = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            inventory = root / "inventory.json"
            inventory.write_text(json.dumps({"baseline": baseline, "files": {
                name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names},
            }))
            private = root / "private-runtime-forager"
            private.write_bytes(b"exact active private binary")
            output = root / "bundle"
            receipt = prepare(source, inventory, installed, output, installed_binary=private)
            self.assertFalse(receipt["deployment_ready"])
            self.assertEqual((output / "rollback-original/bin/forager").read_bytes(), private.read_bytes())
            self.assertEqual((output / "candidate/bin/forager").read_bytes(), binary.read_bytes())
            before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()}
            with self.assertRaises(FileExistsError):
                prepare(source, inventory, installed, output, installed_binary=private)
            self.assertEqual(before, {str(p.relative_to(output)): p.read_bytes() for p in output.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
