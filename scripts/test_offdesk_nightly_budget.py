"""Nightly baseline lifecycle checks with synthetic usage; no provider calls."""
import concurrent.futures
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import offdesk_usage_guard as guard

START = guard.timestamp("2026-11-02T23:50:00+09:00")
RESET = START + 7 * 86400


class NightlyBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="forager-night-budget-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {"gpt": self.root / "offdesk_weekly_gpt.json"}
        self.sample(25)

    def sample(self, used, now=START, provider="gpt", reset=RESET):
        self.paths.setdefault(provider, self.root / f"offdesk_weekly_{provider}.json")
        value = guard.normalize(provider, provider + "-fixture-account", {"week": (used, reset)}, now)
        guard.atomic_json(self.paths[provider], value)
        return value

    def begin(self, now=START):
        return guard.initialize_nightly(self.root, self.paths, now)

    def state(self, result):
        return json.loads(Path(result["state_path"]).read_text())

    def test_old_weekly_total_does_not_block_new_night_and_exact_five_point_boundary_holds(self):
        old = guard.initialize({"gpt": self.sample(10)}, START + 10000, START, 10, 300)
        self.assertFalse(guard.evaluate(old, {"gpt": self.sample(25)}, START)["allowed"])
        result = self.begin()
        self.assertTrue(result["verdict"]["allowed"])
        self.assertEqual(result["baseline_used_percent"]["gpt"]["week"], 25)
        self.assertEqual(result["sampled_ceiling_percent"]["gpt"]["week"], 30)
        self.sample(29.9)
        self.assertTrue(self.begin()["verdict"]["allowed"])
        self.sample(30)
        self.assertIn("gpt:week:budget_reached", self.begin()["verdict"]["reasons"])
        self.assertEqual(old["baselines"]["gpt"]["weekly_meters"]["week"]["used_percent"], 10)

    def amendment(self, used=30):
        first = self.begin()
        self.sample(used)
        self.begin()
        path = Path(first["state_path"])
        state, state_sha = guard._amendment_json(path)
        _, status_sha = guard._amendment_json(Path(str(path) + ".status.json"))
        request = self.root / "grant-request.json"
        guard.atomic_json(request, {
            "schema": "offdesk_nightly_allowance_request.v1", "request_id": "operator-approved-once",
            "authority_ref": "User approved total ten points from original baseline",
            "state_path": str(path), "state_sha256": state_sha, "status_sha256": status_sha,
            "contract_sha256": guard.json_digest(guard.budget_contract(state)),
            "from_percentage_points": 5, "to_percentage_points": 10})
        return path, request, self.root / "grant-receipt.json", state

    def test_explicit_amendment_preserves_original_identity_and_reconnect_limit(self):
        path, request, receipt, before = self.amendment()
        result = guard.amend_nightly(path, self.paths, request, receipt, START)
        self.assertTrue(result["verdict"]["allowed"])
        after = json.loads(path.read_text())
        self.assertEqual(after["baselines"], before["baselines"])
        self.assertEqual(after["deadline"], before["deadline"])
        self.assertEqual(after["high_water"], before["high_water"])
        self.assertEqual(after["nightly"]["started_at"], before["nightly"]["started_at"])
        self.assertEqual(after["limit_percentage_points"], 10)
        self.assertTrue(Path(str(receipt) + ".intent.json").is_file())
        resumed = self.begin()
        self.assertTrue(resumed["reused"])
        self.assertEqual(resumed["sampled_ceiling_percent"]["gpt"]["week"], 35)
        self.sample(35)
        self.assertFalse(self.begin()["verdict"]["allowed"])
        self.sample(40, START + 86400)
        self.assertEqual(self.begin(START + 86400)["limit_percentage_points"], 5)

    def test_amendment_never_clears_reached_new_ceiling(self):
        path, request, receipt, before = self.amendment(35)
        result = guard.amend_nightly(path, self.paths, request, receipt, START)
        self.assertFalse(result["verdict"]["allowed"])
        self.assertTrue(json.loads(path.read_text())["held"])
        self.assertEqual(result["original_baselines"], before["baselines"])

    def test_amendment_replay_and_partial_publication_do_not_mutate_state(self):
        for mode in ["receipt", "intent", "replay"]:
            with self.subTest(mode=mode):
                path, request, receipt, _ = self.amendment()
                if mode == "replay":
                    guard.amend_nightly(path, self.paths, request, receipt, START)
                else:
                    (receipt if mode == "receipt" else Path(str(receipt) + ".intent.json")).write_text("preserve")
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    guard.amend_nightly(path, self.paths, request, receipt, START)
                self.assertEqual(path.read_bytes(), before)
                for item in self.root.glob("grant-*"):
                    item.unlink()
                path.unlink()
                Path(str(path) + ".status.json").unlink()
                self.sample(25)

    def test_amendment_rejects_stale_source_unknown_hold_and_bad_fresh_sample(self):
        for mode in ["sha", "authority", "unknown_hold", "stale", "account", "reset", "decrease"]:
            with self.subTest(mode=mode):
                path, request, receipt, _ = self.amendment()
                proposal = json.loads(request.read_text())
                if mode == "sha": proposal["state_sha256"] = "0" * 64
                if mode == "authority": proposal["authority_ref"] = ""
                if mode == "unknown_hold":
                    status_path = Path(str(path) + ".status.json")
                    status = json.loads(status_path.read_text())
                    status["reasons"].append("unreviewed_failure")
                    guard.atomic_json(status_path, status)
                    proposal["status_sha256"] = guard._amendment_json(status_path)[1]
                sample = json.loads(self.paths["gpt"].read_text())
                if mode == "stale": sample["observed_at"] -= 301
                if mode == "account": sample["account"] = "other"
                if mode == "reset": sample["weekly_meters"]["week"]["resets_at"] += 604800
                if mode == "decrease": sample["weekly_meters"]["week"]["used_percent"] = 29
                guard.atomic_json(self.paths["gpt"], sample)
                guard.atomic_json(request, proposal)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    guard.amend_nightly(path, self.paths, request, receipt, START)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(receipt.exists())
                path.unlink()
                self.sample(25)

    def test_amended_contract_rejects_original_baseline_or_grant_tampering(self):
        path, request, receipt, _ = self.amendment()
        guard.amend_nightly(path, self.paths, request, receipt, START)
        original = json.loads(path.read_text())
        for mode in ["baseline", "grant", "dropped", "scope"]:
            state = copy.deepcopy(original)
            if mode == "baseline": state["baselines"]["gpt"]["observed_at"] -= 1
            if mode == "grant": state["nightly"]["allowance_amendment"]["previous_contract_sha256"] = "0" * 64
            if mode == "dropped": state["nightly"].pop("allowance_amendment")
            if mode == "scope": state["nightly"]["allowance_amendment"]["providers"] = ["claude"]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                guard.evaluate_bound({"state": state, "contract": guard.budget_contract(state),
                                      "samples": guard.load_samples(self.paths), "now": START, "max_age": 300})

    def test_cli_amendment_requires_explicit_confirmation(self):
        path, request, receipt, _ = self.amendment()
        before = path.read_bytes()
        result = subprocess.run([sys.executable, "-I", "-S", str(Path(guard.__file__).resolve()),
                                 "amend-nightly", "--state", str(path), "--gpt-sample", str(self.paths["gpt"]),
                                 "--amendment-request", str(request), "--amendment-receipt", str(receipt)],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_midnight_reconnect_reuses_baseline_and_held_flag_without_moving_deadline(self):
        first = self.begin()
        original = guard.budget_contract(self.state(first))
        midnight = guard.timestamp("2026-11-03T00:10:00+09:00")
        self.sample(29, midnight)
        resumed = self.begin(midnight)
        self.assertTrue(resumed["reused"])
        self.assertEqual(first["state_path"], resumed["state_path"])
        self.assertEqual(guard.budget_contract(self.state(resumed)), original)
        self.sample(30, midnight)
        self.assertFalse(self.begin(midnight)["verdict"]["allowed"])
        self.sample(30, midnight + 1)
        held = self.begin(midnight + 1)
        self.assertIn("previous_hold_requires_review", held["verdict"]["reasons"])
        self.assertEqual(guard.budget_contract(self.state(held)), original)

    def test_next_night_gets_fresh_baseline_with_old_state_bytes_preserved(self):
        first = self.begin()
        path = Path(first["state_path"])
        before = path.read_bytes()
        tomorrow = START + 86400
        self.sample(40, tomorrow)
        second = self.begin(tomorrow)
        self.assertTrue(second["initialized"])
        self.assertNotEqual(first["state_path"], second["state_path"])
        self.assertEqual(second["sampled_ceiling_percent"]["gpt"]["week"], 45)
        self.assertEqual(path.read_bytes(), before)

    def test_eight_am_boundary_and_provider_remaining_balance(self):
        before = guard.timestamp("2026-11-03T07:59:59+09:00")
        boundary = before + 1
        self.assertEqual(guard.nightly_window(before), ("2026-11-02", boundary))
        self.assertEqual(guard.nightly_window(boundary), ("2026-11-03", boundary + 86400))
        self.sample(98)
        first = self.begin()
        self.assertEqual(first["sampled_ceiling_percent"]["gpt"]["week"], 100)
        self.sample(100)
        self.assertFalse(self.begin()["verdict"]["allowed"])

    def test_providers_have_separate_five_point_increments(self):
        self.sample(60, provider="claude")
        first = self.begin()
        self.assertEqual(first["sampled_ceiling_percent"], {"gpt": {"week": 30}, "claude": {"week": 65}})
        self.sample(65, provider="claude")
        result = self.begin()
        self.assertEqual(result["verdict"]["increment_percentage_points"]["gpt"]["week"], 0)
        self.assertIn("claude:week:budget_reached", result["verdict"]["reasons"])

    def test_missing_stale_or_reset_change_holds_same_night_and_cannot_rebaseline(self):
        for mode in ["missing", "stale", "reset", "account", "decrease"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sample_path = root / "sample.json"
                value = self.sample(25)
                guard.atomic_json(sample_path, value)
                paths = {"gpt": sample_path}
                first = guard.initialize_nightly(root, paths, START)
                baseline = guard.budget_contract(self.state(first))
                if mode == "missing":
                    sample_path.unlink()
                else:
                    if mode == "stale": value["observed_at"] -= 301
                    if mode == "reset": value["weekly_meters"]["week"]["resets_at"] += 604800
                    if mode == "account": value["account"] = "other"
                    if mode == "decrease": value["weekly_meters"]["week"]["used_percent"] = 24
                    guard.atomic_json(sample_path, value)
                held = guard.initialize_nightly(root, paths, START)
                self.assertFalse(held["verdict"]["allowed"])
                self.assertEqual(guard.budget_contract(self.state(held)), baseline)
                guard.atomic_json(sample_path, self.sample(25))
                self.assertIn("previous_hold_requires_review", guard.initialize_nightly(root, paths, START)["verdict"]["reasons"])

    def test_nightly_scope_is_in_bound_contract_and_cannot_change_start_allowance_or_end(self):
        state = self.state(self.begin())
        original = guard.budget_contract(copy.deepcopy(state))
        for field, value in [("started_at", START + 1), ("night", "2026-11-03"), ("stops_at", START + 900)]:
            altered = copy.deepcopy(state)
            altered["nightly"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                guard.evaluate_bound({"state": altered, "contract": original, "samples": {}, "now": START, "max_age": 300})
        for mutate in [lambda s: s.update(limit_percentage_points=10), lambda s: s.update(deadline=START + 900)]:
            altered = copy.deepcopy(state)
            mutate(altered)
            with self.assertRaises(ValueError):
                guard.evaluate_bound({"state": altered, "contract": guard.budget_contract(altered), "samples": {}, "now": START, "max_age": 300})

    def test_concurrent_reconnects_create_only_one_original_start(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(lambda _: self.begin(), range(3)))
        self.assertEqual(sum(r["initialized"] for r in results), 1)
        self.assertEqual(len({r["state_path"] for r in results}), 1)
        self.assertTrue(all(r["verdict"]["allowed"] for r in results))

    def test_symlink_state_or_lock_is_refused_without_touching_target(self):
        for suffix in ["", ".lock"]:
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "untouched"
                target.write_text("preserve")
                path = root / ("offdesk_weekly_night_2026-11-02.json" + suffix)
                path.symlink_to(target)
                with self.assertRaises((OSError, ValueError)):
                    guard.initialize_nightly(root, self.paths, START)
                self.assertEqual(target.read_text(), "preserve")

    def test_cli_defaults_to_five_points_and_nightly_cli_reuses_one_path(self):
        import time
        now = time.time()
        self.sample(25, now, reset=now + 604800)
        command = [sys.executable, "-I", "-S", str(Path(guard.__file__).resolve())]
        generic = self.root / "explicit.json"
        result = subprocess.run(command + ["init", "--state", str(generic), "--gpt-sample", str(self.paths["gpt"]),
                                           "--deadline", "2099-01-01T00:00:00Z"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(generic.read_text())["limit_percentage_points"], 5)
        args = ["init-nightly", "--state-dir", str(self.root), "--gpt-sample", str(self.paths["gpt"])]
        first = subprocess.run(command + args, capture_output=True)
        second = subprocess.run(command + args, capture_output=True)
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertTrue(json.loads(second.stdout)["reused"])
        self.assertEqual(json.loads(first.stdout)["state_path"], json.loads(second.stdout)["state_path"])
        invalid = subprocess.run(command + args + ["--limit-percentage-points", "10"], capture_output=True)
        self.assertNotEqual(invalid.returncode, 0)


if __name__ == "__main__":
    unittest.main()
