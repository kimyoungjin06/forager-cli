"""Real small-consumer integration; all roots and process failures are disposable."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import offdesk_metadata_consumer as consumer
import offdesk_resume_checkpoint as gate


class MetadataConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.actions = ["compute", "reuse", "exclude", "compute"]
        self.write_input()
        self.config = {"schema": "forager.metadata_consumer.v1", "batch_rows": 2,
                       "limits": {"work_seconds": 10, "shutdown_grace_seconds": 2, "total_seconds": 120}}
        (self.root / "configuration.json").write_bytes(gate.encode(self.config))
        (self.root / "consumer.py").write_bytes(Path(consumer.__file__).read_bytes())
        (self.root / "validator.py").write_bytes(Path(gate.__file__).read_bytes())

    def write_input(self):
        rows = [{"work_id": "W" + str(i + 1), "local_idx": i, "action": action}
                for i, action in enumerate(self.actions)]
        (self.root / "input.jsonl").write_bytes(b"".join(gate.encode(r) for r in rows))

    def step(self, previous):
        return consumer.consume_batch(self.root, previous["contract"], previous["contract_sha256"],
                                      previous["receipt"], previous["receipt_sha256"], previous.get("publication"), previous.get("publication_sha256"))

    def snapshot(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.iterdir()}

    def same_stat_change(self, name):
        path = self.root / name
        st = path.stat()
        raw = path.read_bytes()
        path.write_bytes(b"X" + raw[1:])
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(path.stat().st_size, st.st_size)
        self.assertEqual(path.stat().st_mtime_ns, st.st_mtime_ns)

    def test_committed_batch_survives_process_exit_and_new_process_resumes(self):
        scripts = str(Path(consumer.__file__).parent)
        program = ("import sys,json,os;sys.path.insert(0,sys.argv[1]);"
                   "import offdesk_metadata_consumer as c;"
                   "p=c.initialize(sys.argv[2]);"
                   "r=c.consume_batch(sys.argv[2],p['contract'],p['contract_sha256'],p['receipt'],p['receipt_sha256'],p['publication'],p['publication_sha256']);"
                   "print(json.dumps(r),flush=True);os._exit(7)")
        first = subprocess.run([sys.executable, "-c", program, scripts, str(self.root)],
                               capture_output=True, text=True, timeout=20)
        self.assertEqual(first.returncode, 7, first.stderr)
        saved = json.loads(first.stdout)
        self.assertEqual(saved["processed"], 2)
        preserved = self.snapshot()
        second = subprocess.run([sys.executable, str(Path(consumer.__file__)), "batch", str(self.root),
                                 "--contract", saved["contract"], "--contract-sha256", saved["contract_sha256"],
                                 "--receipt", saved["receipt"], "--receipt-sha256", saved["receipt_sha256"], "--publication", saved["publication"], "--publication-sha256", saved["publication_sha256"]],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(second.returncode, 0, second.stderr)
        result = json.loads(second.stdout)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"], {"compute": 2, "reuse": 1, "exclude": 1})
        for name, digest in preserved.items():
            self.assertEqual(self.snapshot()[name], digest)
        before = self.snapshot()
        self.assertEqual(self.step(result), result)
        self.assertEqual(self.snapshot(), before)

    def test_uncommitted_interruption_retains_entry_and_cannot_replay(self):
        seed = consumer.initialize(self.root)
        with patch.object(gate, "publish_completion", side_effect=RuntimeError("interrupted before receipt")):
            with self.assertRaises(RuntimeError):
                self.step(seed)
        self.assertTrue((self.root / "entry-0.json").exists())
        self.assertTrue((self.root / "batch-0-2.checkpoint.json").exists())
        self.assertFalse((self.root / "batch-0-2.receipt.json").exists())
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            self.step(seed)
        self.assertEqual(self.snapshot(), before)

    def test_publication_pins_are_required_and_changed_observation_cannot_resume(self):
        seed=consumer.initialize(self.root)
        with self.assertRaisesRegex(gate.CheckpointError,'publication observation path'):
            consumer.consume_batch(self.root,seed['contract'],seed['contract_sha256'],seed['receipt'],seed['receipt_sha256'])
        path=self.root/seed['publication'];before=path.read_bytes();path.write_bytes(before+b' ')
        with self.assertRaises(gate.CheckpointError):self.step(seed)
        self.assertFalse((self.root/'entry-0.json').exists())
        value=json.loads(before);value['boot_id']='another-boot'
        path.write_bytes(gate.encode(value));seed['publication_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(gate.CheckpointError,'publication changed original window or boot'):
            self.step(seed)

    def test_same_stat_changes_refuse_before_entry(self):
        seed = consumer.initialize(self.root)
        for name in ["input.jsonl", "configuration.json", "consumer.py", "validator.py"]:
            path = self.root / name
            original = path.read_bytes()
            with self.subTest(name=name):
                self.same_stat_change(name)
                with self.assertRaises((gate.CheckpointError, OSError)):
                    self.step(seed)
                self.assertFalse((self.root / "entry-0.json").exists())
                path.write_bytes(original)

    def test_revalidation_after_entry_rejects_source_drift(self):
        seed = consumer.initialize(self.root)
        real = consumer._admit
        calls = 0
        def admit(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.same_stat_change("input.jsonl")
            return real(*args)
        with patch.object(consumer, "_admit", side_effect=admit):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        self.assertTrue((self.root / "entry-0.json").exists())
        self.assertFalse((self.root / "batch-0-2.checkpoint.json").exists())

    def test_new_contract_cannot_launder_changed_input_after_computation(self):
        seed = consumer.initialize(self.root)
        real = gate.freeze
        def freeze_after_drift(*args, **kwargs):
            self.same_stat_change("input.jsonl")
            return real(*args, **kwargs)
        with patch.object(gate, "freeze", side_effect=freeze_after_drift):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        self.assertFalse((self.root / "batch-0-2.receipt.json").exists())
        self.assertTrue((self.root / "entry-0.json").exists())

    def test_old_checkpoint_cannot_duplicate_a_completed_batch(self):
        seed = consumer.initialize(self.root)
        first = self.step(seed)
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            self.step(seed)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.step(first)["processed"], 4)

    def test_two_consumers_reserve_at_most_one_batch(self):
        seed = consumer.initialize(self.root)
        def step():
            try:
                return self.step(seed)
            except FileExistsError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: step(), range(2)))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(len(list(self.root.glob("batch-*.receipt.json"))), 1)

    def test_original_window_and_boot_are_not_renewed(self):
        seed = consumer.initialize(self.root)
        with patch.object(consumer, "now", return_value=consumer.now() + 1000):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        with patch.object(consumer, "boot_id", return_value="different-boot"):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        self.assertFalse((self.root / "entry-0.json").exists())
        with self.assertRaises(FileExistsError):
            consumer.initialize(self.root)

    def test_semantically_wrong_but_repinned_checkpoint_is_rejected(self):
        seed = consumer.initialize(self.root)
        path = self.root / "seed.checkpoint.json"
        state = json.loads(path.read_text())
        state["processed"] = 1
        state["counts"] = {"compute": 0, "reuse": 1, "exclude": 0}
        path.write_bytes(gate.encode(state))
        pin = gate.freeze(self.root, "forged.contract.json", stage_id="metadata_policy_bridge",
                          implementation="consumer.py", configuration="configuration.json",
                          inputs=["input.jsonl", "validator.py"], checkpoints=["seed.checkpoint.json"],
                          limits=self.config["limits"])
        stamp = consumer.now()
        receipt, publication, publication_pin = gate.publish_observed_completion(self.root, "forged.contract.json", pin, "forged.receipt.json",
                                         [{"attempt_id": "forged", "started_at": stamp,
                                           "work_ended_at": stamp, "ended_at": stamp, "outcome": "completed"}],
                                         json.loads((self.root/"seed.checkpoint.json").read_text())["window_start"], consumer.boot_id())
        with self.assertRaisesRegex(gate.CheckpointError, "prefix counts"):
            consumer.consume_batch(self.root, "forged.contract.json", pin, "forged.receipt.json", receipt, publication, publication_pin)
        self.assertFalse((self.root / "entry-1.json").exists())

    def test_invalid_roster_never_initializes_checkpoint(self):
        rows = [b'{"work_id":"W1","local_idx":true,"action":"compute"}\n',
                b'{"work_id":"W01","local_idx":0,"action":"compute"}\n',
                b'{"work_id":"W1","local_idx":0,"action":"unknown"}\n',
                b'{"work_id":"W1","local_idx":0,"action":"compute","action":"reuse"}\n',
                b'{"work_id":"W1","local_idx":0,"action":"compute"}\n' * 2]
        for raw in rows:
            with self.subTest(raw=raw):
                (self.root / "input.jsonl").write_bytes(raw)
                with self.assertRaises(gate.CheckpointError):
                    consumer.initialize(self.root)
                self.assertFalse((self.root / "seed.checkpoint.json").exists())

    def test_empty_selection_finishes_without_filler_or_entry(self):
        self.actions = []
        self.write_input()
        seed = consumer.initialize(self.root)
        before = self.snapshot()
        self.assertEqual(self.step(seed)["status"], "complete")
        self.assertEqual(self.snapshot(), before)

    def test_cli_batch_requires_explicit_pins(self):
        process = subprocess.run([sys.executable, str(Path(consumer.__file__)), "batch", str(self.root)],
                                 capture_output=True, text=True, timeout=20)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((self.root / "seed.checkpoint.json").exists())



class MetadataConsumerV2Tests(unittest.TestCase):
    write_input = MetadataConsumerTests.write_input
    step = MetadataConsumerTests.step
    snapshot = MetadataConsumerTests.snapshot
    same_stat_change = MetadataConsumerTests.same_stat_change

    def setUp(self):
        import offdesk_metadata_packet as packet
        self.packet = packet
        MetadataConsumerTests.setUp(self)
        self.source_rows = [
            {"work_id": "W1", "publication_year": 1970, "local_idx": 321,
             "p0_action": 3, "vector_present": False},
            {"work_id": "W2", "publication_year": 2009, "local_idx": 8,
             "p0_action": 1, "vector_present": True},
            {"work_id": "W3", "publication_year": None, "local_idx": None,
             "p0_action": None, "vector_present": False}]
        policy = {"schema": "incremental_embedding_usage_exclusion_v1", "query_policy": "deny",
                  "member_policy": "deny", "denied_work_ids": ["W1", "W2", "W3"],
                  "diagnostic_compute_allowed": True, "live_applied": False}
        source_raw = b"".join(gate.encode(r) for r in self.source_rows)
        policy_raw = gate.encode(policy)
        context = {"purpose": packet.PURPOSE, "source_vintage": "synthetic-fixture-only",
                   "source_artifact_sha256": "a" * 64, "source_contract_sha256": "b" * 64,
                   "selected_rows_sha256": hashlib.sha256(source_raw).hexdigest(),
                   "policy_snapshot_sha256": hashlib.sha256(policy_raw).hexdigest(),
                   "selection_start": 0, "selection_rows": 3}
        payload, provenance, manifest = packet.build_packet(source_raw, policy_raw, context)
        (self.root / "input.jsonl").write_bytes(payload)
        (self.root / "row_provenance.jsonl").write_bytes(provenance)
        (self.root / "manifest.json").write_bytes(gate.encode(manifest))
        (self.root / "policy.json").write_bytes(policy_raw)
        (self.root / "adapter.py").write_bytes(Path(packet.__file__).read_bytes())
        self.config["schema"] = "forager.metadata_consumer.v2"
        self.config["packet"] = {k: v for k, v in context.items() if k not in ["selection_start", "selection_rows"]}
        self.refresh_manifest_pin()

    def refresh_manifest_pin(self):
        self.config["packet"]["manifest_sha256"] = hashlib.sha256((self.root / "manifest.json").read_bytes()).hexdigest()
        (self.root / "configuration.json").write_bytes(gate.encode(self.config))

    def forged_resume(self, inputs):
        pin = gate.freeze(self.root, "forged.contract.json", stage_id="metadata_policy_bridge",
                          implementation="consumer.py", configuration="configuration.json",
                          inputs=inputs, checkpoints=["seed.checkpoint.json"], limits=self.config["limits"])
        stamp = consumer.now()
        receipt, publication, publication_pin = gate.publish_observed_completion(self.root, "forged.contract.json", pin, "forged.receipt.json",
                                         [{"attempt_id": "forged", "started_at": stamp,
                                           "work_ended_at": stamp, "ended_at": stamp, "outcome": "completed"}],
                                         json.loads((self.root/"seed.checkpoint.json").read_text())["window_start"], consumer.boot_id())
        return {"contract": "forged.contract.json", "contract_sha256": pin,
                "receipt": "forged.receipt.json", "receipt_sha256": receipt, "publication": publication, "publication_sha256": publication_pin}

    def test_v2_contract_contains_policy_provenance_and_resumes(self):
        seed = consumer.initialize(self.root)
        contract = json.loads((self.root / seed["contract"]).read_text())
        self.assertEqual([r["path"] for r in contract["inputs"]],
                         ["input.jsonl", "validator.py", "adapter.py", "manifest.json", "row_provenance.jsonl", "policy.json"])
        first = self.step(seed)
        self.assertTrue(first["packet_semantics_verified"])
        result = self.step(first)
        self.assertEqual(result["counts"], {"compute": 0, "reuse": 0, "exclude": 3})
        self.assertFalse(result["scientific_acceptance"])
        before = self.snapshot()
        self.assertEqual(self.step(result), result)
        self.assertEqual(self.snapshot(), before)

    def test_v2_new_process_reopens_committed_checkpoint(self):
        seed = consumer.initialize(self.root)
        first = self.step(seed)
        result = subprocess.run(
            [sys.executable, str(Path(consumer.__file__)), "batch", str(self.root),
             "--contract", first["contract"], "--contract-sha256", first["contract_sha256"],
             "--receipt", first["receipt"], "--receipt-sha256", first["receipt_sha256"], "--publication", first["publication"], "--publication-sha256", first["publication_sha256"]],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value["packet_semantics_verified"])
        self.assertEqual(value["processed"], 3)

    def test_v2_all_packet_byte_changes_hold_before_entry(self):
        seed = consumer.initialize(self.root)
        for name in ["adapter.py", "manifest.json", "row_provenance.jsonl", "policy.json"]:
            original = (self.root / name).read_bytes()
            self.same_stat_change(name)
            with self.subTest(name=name), self.assertRaises((gate.CheckpointError, OSError)):
                self.step(seed)
            self.assertFalse((self.root / "entry-0.json").exists())
            (self.root / name).write_bytes(original)

    def test_v2_selected_purpose_epoch_source_mismatch_hold(self):
        original = copy.deepcopy(self.config)
        for key, value in [("purpose", "bc_exclusion"), ("source_vintage", "wrong-epoch"),
                           ("source_artifact_sha256", "c" * 64), ("source_contract_sha256", "c" * 64),
                           ("selected_rows_sha256", "c" * 64), ("policy_snapshot_sha256", "c" * 64)]:
            self.config = copy.deepcopy(original)
            self.config["packet"][key] = value
            (self.root / "configuration.json").write_bytes(gate.encode(self.config))
            with self.subTest(key=key), self.assertRaises(gate.CheckpointError):
                consumer.initialize(self.root)
            self.assertFalse((self.root / "seed.checkpoint.json").exists())

    def test_v2_rechecks_packet_after_entry(self):
        seed = consumer.initialize(self.root)
        real = consumer._admit
        calls = 0
        def changed(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.same_stat_change("row_provenance.jsonl")
            return real(*args)
        with patch.object(consumer, "_admit", side_effect=changed):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        self.assertTrue((self.root / "entry-0.json").exists())
        self.assertFalse((self.root / "batch-0-2.checkpoint.json").exists())

    def test_v2_omitted_extra_inputs_cannot_be_coherently_rebound(self):
        consumer.initialize(self.root)
        forged = self.forged_resume(["input.jsonl", "validator.py"])
        with self.assertRaisesRegex(gate.CheckpointError, "omitted"):
            self.step(forged)
        self.assertFalse((self.root / "entry-0.json").exists())

    def test_v1_downgrade_with_packet_artifacts_refused(self):
        consumer.initialize(self.root)
        self.config.pop("packet")
        self.config["schema"] = "forager.metadata_consumer.v1"
        (self.root / "configuration.json").write_bytes(gate.encode(self.config))
        forged = self.forged_resume(["input.jsonl", "validator.py"])
        with self.assertRaisesRegex(gate.CheckpointError, "explicit v2"):
            self.step(forged)
        self.assertFalse((self.root / "entry-0.json").exists())

    def test_v2_uncommitted_entry_refuses_replay(self):
        seed = consumer.initialize(self.root)
        with patch.object(gate, "publish_completion", side_effect=RuntimeError("fixture interruption")):
            with self.assertRaises(RuntimeError):
                self.step(seed)
        before = self.snapshot()
        with self.assertRaises(FileExistsError):
            self.step(seed)
        self.assertEqual(self.snapshot(), before)

    def test_v2_new_contract_cannot_launder_provenance_drift(self):
        seed = consumer.initialize(self.root)
        real = gate.freeze
        def changed(*args, **kwargs):
            self.same_stat_change("row_provenance.jsonl")
            return real(*args, **kwargs)
        with patch.object(gate, "freeze", side_effect=changed):
            with self.assertRaises(gate.CheckpointError):
                self.step(seed)
        self.assertFalse((self.root / "batch-0-2.receipt.json").exists())

    def test_v2_unbound_policy_member_with_coherent_hashes_refused(self):
        policy = json.loads((self.root / "policy.json").read_text())
        policy["denied_work_ids"].remove("W3")
        raw = gate.encode(policy)
        (self.root / "policy.json").write_bytes(raw)
        manifest = json.loads((self.root / "manifest.json").read_text())
        manifest["source"]["policy_snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
        (self.root / "manifest.json").write_bytes(gate.encode(manifest))
        self.config["packet"]["policy_snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
        self.refresh_manifest_pin()
        with self.assertRaisesRegex(gate.CheckpointError, "unbound"):
            consumer.initialize(self.root)
        self.assertFalse((self.root / "seed.checkpoint.json").exists())

    def test_v2_live_flag_or_diagnostic_permission_cannot_be_relabelled(self):
        original = json.loads((self.root / "manifest.json").read_text())
        for field, value in [("policy_live_applied_in_snapshot", True),
                             ("diagnostic_compute_allowed_in_snapshot", False),
                             ("ready_for_launch", True)]:
            manifest = {**original, field: value}
            (self.root / "manifest.json").write_bytes(gate.encode(manifest))
            self.refresh_manifest_pin()
            with self.subTest(field=field), self.assertRaises(gate.CheckpointError):
                consumer.initialize(self.root)
            self.assertFalse((self.root / "seed.checkpoint.json").exists())

    def test_v2_adapter_implementation_and_missing_file_hold(self):
        self.same_stat_change("adapter.py")
        with self.assertRaisesRegex(gate.CheckpointError, "adapter differs"):
            consumer.initialize(self.root)
        self.assertFalse((self.root / "seed.checkpoint.json").exists())
        (self.root / "adapter.py").write_bytes(Path(self.packet.__file__).read_bytes())
        seed = consumer.initialize(self.root)
        (self.root / "policy.json").unlink()
        with self.assertRaises(OSError):
            self.step(seed)
        self.assertFalse((self.root / "entry-0.json").exists())


if __name__ == "__main__":
    unittest.main()
