"""Provider-free refusal and publication checks on tiny disposable files."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import offdesk_resume_checkpoint as gate


class ResumeCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ["config", "implementation", "input", "checkpoint"]:
            (self.root / name).write_bytes(b"baseline")
        self.limits = {"work_seconds": 10, "shutdown_grace_seconds": 3, "total_seconds": 30}
        self.pin = gate.freeze(self.root, "contract.json", stage_id="metadata_policy_bridge",
                               configuration="config", implementation="implementation", inputs=["input"],
                               checkpoints=["checkpoint"], limits=self.limits)
        self.attempts = [
            {"attempt_id": "failed-1", "started_at": 0, "work_ended_at": 3,
             "ended_at": 4, "outcome": "failed"},
            {"attempt_id": "manual-2", "started_at": 10, "work_ended_at": 12,
             "ended_at": 13, "outcome": "completed"}]

    def complete(self, attempts=None):
        return gate.publish_completion(self.root, "contract.json", self.pin, "receipt.json",
                                       self.attempts if attempts is None else attempts)

    def inspect(self, receipt_pin):
        return gate.inspect_resume(self.root, "contract.json", self.pin, "receipt.json", receipt_pin)

    def replace_same_stat(self, path):
        st = path.stat()
        path.write_bytes(b"tampered")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(path.stat().st_size, st.st_size)
        self.assertEqual(path.stat().st_mtime_ns, st.st_mtime_ns)

    def repin(self, filename, value):
        raw = gate.encode(value)
        (self.root / filename).write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def test_complete_receipt_reopens_read_only_and_keeps_failed_costs(self):
        receipt_pin = self.complete()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.inspect(receipt_pin)
        self.assertTrue(result["byte_binding_verified"])
        self.assertFalse(result["resume_authorized"])
        self.assertFalse(result["scientific_acceptance"])
        self.assertEqual(result["accounting"]["attempt_count"], 2)
        self.assertEqual(result["accounting"]["attempt_wall_seconds"], 7)
        self.assertEqual(result["accounting"]["elapsed_window_seconds"], 13)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def observed(self, clock=None):
        attempts=[{"attempt_id":"measured","started_at":10,"work_ended_at":12,"ended_at":12,"outcome":"completed"}]
        with patch.object(gate,"publication_now",side_effect=clock or [12,12,14,15]):
            return gate.publish_observed_completion(self.root,"contract.json",self.pin,"receipt.json",attempts,0,"fixture-boot")

    def test_observed_publication_charges_receipt_write_and_keeps_declared_receipt_distinct(self):
        receipt,publication,pin=self.observed()
        declared=json.loads((self.root/"receipt.json").read_text())
        self.assertEqual(declared["accounting"]["shutdown_grace_seconds"],0)
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        observed=gate.inspect_publication(self.root,"contract.json",self.pin,"receipt.json",receipt,publication,pin)
        self.assertEqual(observed["accounting"]["shutdown_grace_seconds"],2)
        self.assertEqual(observed["attempts"][-1]["ended_at"],14)
        self.assertFalse(observed["resume_authorized"])
        self.assertEqual(observed["measurement_scope"],gate.PUBLICATION_SCOPE)
        self.assertEqual({p.name:p.read_bytes() for p in self.root.iterdir()},before)

    def test_slow_receipt_or_observation_write_cannot_return_completion(self):
        for times in [[12,12,16],[12,12,14,16]]:
            with self.subTest(times=times):
                with self.assertRaises(gate.CheckpointError):self.observed(times)
                self.assertTrue((self.root/"receipt.json").exists())
                for name in ['receipt.json','receipt.json.publication.json']:
                    if (self.root/name).exists():(self.root/name).unlink()

    def test_rehashed_publication_cannot_change_attempts_boot_scope_or_receipt_binding(self):
        receipt,publication,pin=self.observed();original=json.loads((self.root/publication).read_text())
        for field,value in [('receipt_sha256','a'*64),('measurement_scope','all_process_time'),('accounting',{}),('window_start',11)]:
            altered={**original,field:value};newpin=self.repin(publication,altered)
            with self.subTest(field=field),self.assertRaises(gate.CheckpointError):
                gate.inspect_publication(self.root,'contract.json',self.pin,'receipt.json',receipt,publication,newpin)
        self.repin(publication,original)
        with self.assertRaises(gate.CheckpointError):
            gate.inspect_publication(self.root,'contract.json',self.pin,'receipt.json',receipt,publication,'0'*64)

    def test_same_stat_input_checkpoint_and_configuration_changes_are_rejected(self):
        receipt_pin = self.complete()
        for name in ["input", "checkpoint", "config", "implementation"]:
            with self.subTest(name=name):
                self.replace_same_stat(self.root / name)
                with self.assertRaises(gate.CheckpointError):
                    self.inspect(receipt_pin)
                (self.root / name).write_bytes(b"baseline")

    def test_missing_or_partial_receipt_cannot_enable_resume(self):
        for content in [None, b'{"schema":', b"{}"]:
            with self.subTest(content=content):
                if content is not None:
                    (self.root / "receipt.json").write_bytes(content)
                pin = hashlib.sha256(content or b"").hexdigest()
                with self.assertRaises((gate.CheckpointError, OSError)):
                    self.inspect(pin)

    def test_pins_are_required_and_changed_contract_or_receipt_is_rejected(self):
        receipt_pin = self.complete()
        for pin in [None, "", "f" * 64]:
            with self.subTest(pin=pin), self.assertRaises(gate.CheckpointError):
                self.inspect(pin)
        for name in ["receipt.json", "contract.json"]:
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(gate.CheckpointError):
                self.inspect(receipt_pin)
            path.write_bytes(original)

    def test_rebound_receipt_wrong_stage_schema_or_accounting_is_rejected(self):
        receipt_pin = self.complete()
        original = json.loads((self.root / "receipt.json").read_text())
        for key, value in [("stage_id", "other"), ("schema", "other"),
                           ("effect", "authorize_resume"), ("contract_sha256", "a" * 64),
                           ("accounting", {"attempt_count": 1})]:
            with self.subTest(key=key):
                altered = {**original, key: value}
                with self.assertRaises(gate.CheckpointError):
                    self.inspect(self.repin("receipt.json", altered))

    def test_duplicate_json_fields_and_nonfinite_values_fail_even_when_repinned(self):
        for raw in [b'{"schema":1,"schema":2}', b'{"x":NaN}', b'{"x":Infinity}']:
            (self.root / "receipt.json").write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(gate.CheckpointError):
                self.inspect(hashlib.sha256(raw).hexdigest())

    def test_unsupported_stage_schema_duplicates_and_wrong_paths_fail(self):
        self.complete()
        original = json.loads((self.root / "contract.json").read_text())
        alterations = [
            {"stage_id": "graph"}, {"stage_id": []}, {"schema": "other"},
            {"inputs": []}, {"inputs": [original["configuration"]]},
            {"inputs": [{**original["inputs"][0], "path": "../input"}]},
            {"extra_authority": True},
        ]
        for alteration in alterations:
            with self.subTest(alteration=alteration):
                self.pin = self.repin("contract.json", {**original, **alteration})
                with self.assertRaises(gate.CheckpointError):
                    self.inspect(hashlib.sha256((self.root / "receipt.json").read_bytes()).hexdigest())

    def test_symlink_or_hardlink_artifact_is_refused(self):
        receipt_pin = self.complete()
        path = self.root / "input"
        saved = self.root / "saved"
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises((gate.CheckpointError, OSError)):
            self.inspect(receipt_pin)
        path.unlink()
        os.link(saved, path)
        with self.assertRaises((gate.CheckpointError, OSError)):
            self.inspect(receipt_pin)

    def test_symlink_parent_or_root_is_refused(self):
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "bytes").write_bytes(b"baseline")
        (self.root / "alias").symlink_to(sub, target_is_directory=True)
        for root, input_path in [(self.root, "alias/bytes"), (self.root / "alias", "bytes")]:
            with self.subTest(root=root), self.assertRaises(OSError):
                gate.freeze(root, "new.json", stage_id="metadata_policy_bridge",
                            configuration=input_path, implementation="implementation", inputs=["input"],
                            checkpoints=["checkpoint"], limits=self.limits)

    def test_changed_checkpoint_refuses_publication_without_receipt(self):
        self.replace_same_stat(self.root / "checkpoint")
        with self.assertRaises(gate.CheckpointError):
            self.complete()
        self.assertFalse((self.root / "receipt.json").exists())

    def test_time_budget_includes_all_attempts_and_idle_window(self):
        bad = []
        for fields in [
            {"work_ended_at": 21, "ended_at": 22},
            {"ended_at": 16},
            {"started_at": 40, "work_ended_at": 42, "ended_at": 43},
            {"started_at": 2}, {"started_at": float("nan")},
            {"attempt_id": "failed-1"}, {"outcome": "failed"},
            {"outcome": []}, {"work_ended_at": True},
        ]:
            attempts = copy.deepcopy(self.attempts)
            attempts[-1].update(fields)
            bad.append(attempts)
        attempts = copy.deepcopy(self.attempts)
        attempts[0]["outcome"] = "completed"
        bad += [attempts, [], [{"outcome": "completed"}]]
        for attempts in bad:
            with self.subTest(attempts=attempts), self.assertRaises(gate.CheckpointError):
                self.complete(attempts)
            self.assertFalse((self.root / "receipt.json").exists())

    def test_existing_contract_and_receipt_are_never_overwritten(self):
        receipt_pin = self.complete()
        before = (self.root / "receipt.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.complete()
        self.assertEqual((self.root / "receipt.json").read_bytes(), before)
        self.assertTrue(self.inspect(receipt_pin)["byte_binding_verified"])

    def test_two_publishers_cannot_replace_each_others_receipt(self):
        def publish():
            try:
                return self.complete()
            except FileExistsError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: publish(), range(2)))
        pins = [v for v in results if v]
        self.assertEqual(len(pins), 1)
        self.assertTrue(self.inspect(pins[0])["byte_binding_verified"])
        self.assertEqual(list(self.root.glob(".checkpoint-*.tmp")), [])

    def test_publication_error_preserves_sources_and_leaves_no_partial_final(self):
        before = (self.root / "checkpoint").read_bytes()
        with patch.object(gate.os, "link", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                self.complete()
        self.assertFalse((self.root / "receipt.json").exists())
        self.assertEqual(list(self.root.glob(".checkpoint-*.tmp")), [])
        self.assertEqual((self.root / "checkpoint").read_bytes(), before)

    def test_late_drift_keeps_receipt_but_cannot_enable_resume_or_replay(self):
        real_publish = gate.publish
        def publish_then_drift(root, path, value):
            result = real_publish(root, path, value)
            self.replace_same_stat(self.root / "input")
            return result
        with patch.object(gate, "publish", side_effect=publish_then_drift):
            with self.assertRaisesRegex(gate.CheckpointError, "receipt persisted"):
                self.complete()
        self.assertTrue((self.root / "receipt.json").exists())
        pin = hashlib.sha256((self.root / "receipt.json").read_bytes()).hexdigest()
        with self.assertRaises(gate.CheckpointError):
            self.inspect(pin)
        (self.root / "input").write_bytes(b"baseline")
        with self.assertRaises(FileExistsError):
            self.complete()
        self.assertFalse(self.inspect(pin)["resume_authorized"])

    def test_oversized_input_rejected_before_body_read(self):
        with (self.root / "large").open("wb") as f:
            f.truncate(gate.MAX_FILE_BYTES + 1)
        with patch.object(gate.os, "read", side_effect=AssertionError("must not read")):
            with self.assertRaises(gate.CheckpointError):
                gate.freeze(self.root, "new.json", stage_id="metadata_policy_bridge",
                            configuration="large", implementation="implementation", inputs=["input"],
                            checkpoints=["checkpoint"], limits=self.limits)

    def test_total_byte_limit_prevents_over_budget_body_read(self):
        with patch.object(gate, "MAX_TOTAL_BYTES", 16):
            with self.assertRaises(gate.CheckpointError):
                gate.freeze(self.root, "new.json", stage_id="metadata_policy_bridge",
                            configuration="config", implementation="implementation", inputs=["input"],
                            checkpoints=["checkpoint"], limits=self.limits)
        self.assertFalse((self.root / "new.json").exists())

    def test_inconsistent_limits_and_duplicate_artifact_freeze_refused(self):
        for limits, inputs in [({**self.limits, "total_seconds": 10}, ["input"]),
                               ({**self.limits, "work_seconds": True}, ["input"]),
                               (self.limits, ["config"])]:
            with self.subTest(limits=limits, inputs=inputs), self.assertRaises(gate.CheckpointError):
                gate.freeze(self.root, "new.json", stage_id="metadata_policy_bridge",
                            configuration="config", implementation="implementation", inputs=inputs,
                            checkpoints=["checkpoint"], limits=limits)

    def test_read_time_mutation_and_nonregular_files_fail_closed(self):
        real_read = gate.os.read
        changed = False
        def mutate_after_read(fd, size):
            nonlocal changed
            data = real_read(fd, size)
            if not changed:
                changed = True
                self.replace_same_stat(self.root / "config")
            return data
        with patch.object(gate.os, "read", side_effect=mutate_after_read):
            with self.assertRaises(gate.CheckpointError):
                gate.freeze(self.root, "new.json", stage_id="metadata_policy_bridge",
                            implementation="implementation", configuration="config",
                            inputs=["input"], checkpoints=["checkpoint"], limits=self.limits)
        (self.root / "config").unlink()
        os.mkfifo(self.root / "config")
        with self.assertRaises(gate.CheckpointError):
            gate.freeze(self.root, "new.json", stage_id="metadata_policy_bridge",
                        implementation="implementation", configuration="config",
                        inputs=["input"], checkpoints=["checkpoint"], limits=self.limits)

    def test_root_replacement_during_publication_cannot_return_success(self):
        root = self.root / "live"
        root.mkdir()
        for name in ["config", "implementation", "input", "checkpoint"]:
            (root / name).write_bytes(b"baseline")
        real_publish = gate.publish
        def replace_root(fd, path, value):
            result = real_publish(fd, path, value)
            root.rename(self.root / "retained-old-root")
            root.mkdir()
            return result
        with patch.object(gate, "publish", side_effect=replace_root):
            with self.assertRaises(gate.CheckpointError):
                gate.freeze(root, "contract.json", stage_id="metadata_policy_bridge",
                            implementation="implementation", configuration="config",
                            inputs=["input"], checkpoints=["checkpoint"], limits=self.limits)
        self.assertTrue((self.root / "retained-old-root/contract.json").exists())
        self.assertEqual(list(root.iterdir()), [])

    def test_accounting_bool_cannot_masquerade_as_a_number(self):
        self.complete(self.attempts[-1:])
        value = json.loads((self.root / "receipt.json").read_text())
        value["accounting"]["attempt_count"] = True
        with self.assertRaises(gate.CheckpointError):
            self.inspect(self.repin("receipt.json", value))


if __name__ == "__main__":
    unittest.main()

