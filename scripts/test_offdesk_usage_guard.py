"""Deterministic quota decisions and owned-command lifecycle checks."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import offdesk_usage_guard as guard

NOW = 1791468000.0


def sample(provider="gpt", used=20, reset=NOW + 10000, observed=NOW):
    return guard.normalize(provider, provider + "-account", {"weekly": (used, reset)}, observed)


class UsageGuardTests(unittest.TestCase):
    def state(self, providers=("gpt",)):
        return guard.initialize({p: sample(p) for p in providers}, NOW + 1000, NOW, 10, 300)

    def test_separate_provider_budgets_and_exact_boundary(self):
        state = self.state(("gpt", "claude"))
        result = guard.evaluate(state, {"gpt": sample(used=29.9), "claude": sample("claude", 20)}, NOW)
        self.assertTrue(result["allowed"])
        result = guard.evaluate(state, {"gpt": sample(used=30), "claude": sample("claude", 20)}, NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("gpt:weekly:budget_reached", result["reasons"])

    def test_ten_points_is_not_ten_percent_of_remaining(self):
        state = guard.initialize({"gpt": sample(used=80)}, NOW + 1000, NOW, 10, 300)
        self.assertTrue(guard.evaluate(state, {"gpt": sample(used=89)}, NOW)["allowed"])
        self.assertFalse(guard.evaluate(state, {"gpt": sample(used=90)}, NOW)["allowed"])

    def test_missing_stale_future_nonfinite_and_expired_samples_hold(self):
        cases = [{}, {"gpt": sample(observed=NOW - 301)}, {"gpt": sample(observed=NOW + 1)},
                 {"gpt": sample(reset=NOW)}]
        invalid = sample(); invalid["weekly_meters"]["weekly"]["used_percent"] = float("nan")
        cases.append({"gpt": invalid})
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(guard.evaluate(self.state(), payload, NOW)["allowed"])

    def test_reset_change_never_grants_a_new_budget(self):
        result = guard.evaluate(self.state(), {"gpt": sample(reset=NOW + 20000)}, NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("Weekly reset changed", result["issues"]["gpt"])

    def native_sample(self, used=20, offset=0, reset=NOW+10000):
        return guard.normalize("gpt","gpt-account",{"codex:primary":(used,reset+offset)},NOW)

    def test_native_whole_second_variation_keeps_original_baseline_and_allowance(self):
        state=guard.initialize({"gpt":self.native_sample()},NOW+1000,NOW,10,300)
        baseline=copy.deepcopy(state["baselines"])
        for offset,used in [(1,20),(-1,25),(0,29.9)]:
            verdict=guard.evaluate(state,{"gpt":self.native_sample(used,offset)},NOW)
            self.assertTrue(verdict["allowed"],verdict)
            self.assertEqual(verdict["increment_percentage_points"]["gpt"]["codex:primary"],used-20)
            self.assertEqual(state["baselines"],baseline)
        self.assertFalse(guard.evaluate(state,{"gpt":self.native_sample(30,1)},NOW)["allowed"])
        self.assertEqual(state["baselines"],baseline)

    def test_native_epoch_variation_is_anchored_not_a_drifting_window(self):
        state=guard.initialize({"gpt":self.native_sample()},NOW+1000,NOW,10,300)
        self.assertTrue(guard.evaluate(state,{"gpt":self.native_sample(offset=1)},NOW)["allowed"])
        self.assertFalse(guard.evaluate(state,{"gpt":self.native_sample(offset=2)},NOW)["allowed"])
        later=guard.evaluate(state,{"gpt":self.native_sample()},NOW)
        self.assertFalse(later["allowed"])
        self.assertIn("previous_hold_requires_review",later["reasons"])

    def test_native_secondary_bucket_uses_the_same_anchor_without_rewriting_raw_data(self):
        initial=self.native_sample()
        initial['weekly_meters']['codex:secondary']=initial['weekly_meters'].pop('codex:primary')
        state=guard.initialize({'gpt':initial},NOW+1000,NOW,10,300)
        current=copy.deepcopy(initial);current['weekly_meters']['codex:secondary']['resets_at']+=1
        raw=copy.deepcopy(current)
        self.assertTrue(guard.evaluate(state,{'gpt':current},NOW)['allowed'])
        self.assertEqual(current,raw)
        self.assertEqual(state['baselines']['gpt'],initial)

    def test_epoch_jitter_does_not_cover_other_providers_meters_fractional_or_rollover(self):
        for provider,meter,offset in [("claude","codex:primary",1),("gpt","weekly",1),
                                      ("gpt","model:primary",1),("gpt","codex:primary",0.5),
                                      ("gpt","codex:primary",604800)]:
            with self.subTest(provider=provider,meter=meter,offset=offset):
                initial=guard.normalize(provider,provider+'-account',{meter:(20,NOW+10000)},NOW)
                current=guard.normalize(provider,provider+'-account',{meter:(20,NOW+10000+offset)},NOW)
                state=guard.initialize({provider:initial},NOW+1000,NOW,10,300)
                self.assertFalse(guard.evaluate(state,{provider:current},NOW)["allowed"])
        initial=guard.normalize("gpt","gpt-account",{"codex:primary":(20,NOW)},NOW-1)
        state=guard.initialize({"gpt":initial},NOW+1000,NOW-1,10,300)
        self.assertFalse(guard.evaluate(state,{"gpt":self.native_sample(reset=NOW,offset=1)},NOW)["allowed"])

    def test_native_jitter_cannot_hide_account_scope_decrease_or_unknown(self):
        for mode in ['account','meter','decrease','stale','unknown']:
            with self.subTest(mode=mode):
                state=guard.initialize({"gpt":self.native_sample()},NOW+1000,NOW,10,300)
                self.assertTrue(guard.evaluate(state,{"gpt":self.native_sample(25,1)},NOW)["allowed"])
                current=self.native_sample(25,-1)
                if mode=='account':current['account']='different'
                elif mode=='meter':current['weekly_meters']['new']=copy.deepcopy(current['weekly_meters']['codex:primary'])
                elif mode=='decrease':current['weekly_meters']['codex:primary']['used_percent']=24
                elif mode=='stale':current['observed_at']=NOW-301
                self.assertFalse(guard.evaluate(state,{} if mode=='unknown' else {'gpt':current},NOW)["allowed"])
                self.assertFalse(guard.evaluate(state,{'gpt':self.native_sample(25)},NOW)["allowed"])

    def test_decrease_and_account_change_require_reconciliation(self):
        state = self.state()
        self.assertTrue(guard.evaluate(state, {"gpt": sample(used=25)}, NOW)["allowed"])
        self.assertFalse(guard.evaluate(state, {"gpt": sample(used=24)}, NOW)["allowed"])
        changed = sample(); changed["account"] = "another-account"
        self.assertFalse(guard.evaluate(self.state(), {"gpt": changed}, NOW)["allowed"])

    def test_missing_or_added_meter_cannot_unlock_allowance(self):
        changed = sample(); changed["weekly_meters"]["new-model"] = copy.deepcopy(changed["weekly_meters"]["weekly"])
        self.assertFalse(guard.evaluate(self.state(), {"gpt": changed}, NOW)["allowed"])

    def test_unknown_hold_is_latched_after_a_later_good_poll(self):
        state = self.state()
        guard.evaluate(state, {}, NOW)
        self.assertFalse(guard.evaluate(state, {"gpt": sample()}, NOW)["allowed"])

    def test_deadline_blocks_even_below_budget(self):
        self.assertFalse(guard.evaluate(self.state(), {"gpt": sample(observed=NOW + 1000)}, NOW + 1000)["allowed"])

    def test_codex_uses_all_weekly_buckets_ignoring_short_window_and_alias(self):
        weekly = {"usedPercent": 35, "windowDurationMins": 10080, "resetsAt": NOW + 10000}
        short = {"usedPercent": 95, "windowDurationMins": 300, "resetsAt": NOW + 500}
        value = guard.codex_snapshot({"rateLimits": {"secondary": weekly}, "rateLimitsByLimitId": {
            "codex": {"primary": short, "secondary": weekly}, "model": {"primary": weekly}}}, "account", NOW)
        self.assertEqual(set(value["weekly_meters"]), {"codex:secondary", "model:primary"})

    def test_codex_no_weekly_meter_is_unknown(self):
        with self.assertRaises(ValueError):
            guard.codex_snapshot({"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300}}}, "account", NOW)

    def test_workspace_changes_but_token_rotation_does_not_change_identity(self):
        account = {"type": "chatgpt", "email": "test@example.invalid", "planType": "pro"}
        original = guard.codex_account_key(account, {"tokens": {"account_id": "a", "access_token": "secret-1"}})
        rotated = guard.codex_account_key(account, {"tokens": {"account_id": "a", "access_token": "secret-2"}})
        changed = guard.codex_account_key(account, {"tokens": {"account_id": "b"}})
        self.assertEqual(original, rotated)
        self.assertNotEqual(original, changed)
        self.assertNotIn("secret", original)
        with self.assertRaises(ValueError):
            guard.codex_account_key(account, {})

    def test_claude_uses_official_weekly_fields_not_token_or_cost_fields(self):
        value = guard.claude_snapshot({"cost": {"total_cost_usd": 900}, "context_window": {"used_percentage": 99},
                                      "rate_limits": {"seven_day": {"used_percentage": 0, "resets_at": NOW + 10000}}}, "bound-account", NOW)
        self.assertEqual(value["weekly_meters"]["seven_day"]["used_percent"], 0)
        for payload in [{}, {"rate_limits": {"five_hour": {"used_percentage": 20}}}]:
            with self.assertRaises(ValueError):
                guard.claude_snapshot(payload, "bound-account", NOW)

    def test_invalid_baseline_and_naive_deadline_rejected(self):
        for limit in [0, 101, float("nan")]:
            with self.assertRaises(ValueError):
                guard.initialize({"gpt": sample()}, NOW + 1000, NOW, limit, 300)
        with self.assertRaises(ValueError):
            guard.timestamp("2026-10-09T08:00:00")

    def test_provider_read_failure_does_not_use_a_cached_good_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / "state.json"; payload = root / "sample.json"
            guard.atomic_json(state, self.state()); guard.atomic_json(payload, sample())
            with mock.patch.object(guard, "read_codex", side_effect=ValueError("unavailable")), mock.patch.object(guard.time, "time", return_value=NOW):
                self.assertFalse(guard.check_files(state, {"gpt": payload}, 300, True)["allowed"])


class GuardCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.source = self.root / "sample.json"; self.state = self.root / "state.json"
        self.write_sample(20)
        guard.atomic_json(self.state, guard.initialize({"gpt": json.loads(self.source.read_text())}, time.time() + 20, time.time(), 10, 300))
        self.base = [sys.executable, str(Path(guard.__file__).resolve()), "run", "--state", str(self.state),
                     "--gpt-sample", str(self.source), "--interval-seconds", "0.05", "--"]

    def tearDown(self):
        self.tmp.cleanup()

    def write_sample(self, used):
        now = time.time()
        # Keep reset identity fixed across samples.
        reset = getattr(self, "reset", now + 10000); self.reset = reset
        guard.atomic_json(self.source, guard.normalize("gpt", "account", {"weekly": (used, reset)}, now))

    def test_success_and_denied_command_are_observable(self):
        marker = self.root / "ran"
        command = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        result = subprocess.run(self.base + command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(marker.exists()); marker.unlink()
        self.write_sample(30)
        result = subprocess.run(self.base + command, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertFalse(marker.exists())

    def test_budget_stop_and_signal_stop_leave_unrelated_process_alive(self):
        for mode in ["budget", "signal"]:
            with self.subTest(mode=mode):
                # Start a fresh explicit budget for each independent test run.
                self.write_sample(20)
                guard.atomic_json(self.state, guard.initialize({"gpt": json.loads(self.source.read_text())}, time.time() + 20, time.time(), 10, 300))
                marker = self.root / (mode + ".heartbeat")
                child = [sys.executable, "-c", f"import time; from pathlib import Path; p=Path({str(marker)!r});\nwhile True: p.write_text(str(time.time())); time.sleep(.02)"]
                survivor = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"])
                process = subprocess.Popen(self.base + child, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    until = time.monotonic() + 3
                    while not marker.exists() and time.monotonic() < until: time.sleep(.02)
                    self.assertTrue(marker.exists())
                    if mode == "budget": self.write_sample(30)
                    else: process.send_signal(signal.SIGTERM)
                    process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 2 if mode == "budget" else 143)
                    before = marker.read_text(); time.sleep(.15)
                    self.assertEqual(before, marker.read_text())
                    self.assertIsNone(survivor.poll())
                finally:
                    if process.poll() is None: process.kill(); process.communicate()
                    survivor.terminate(); survivor.wait()

    def test_cli_options_after_action_and_baseline_replay(self):
        path = self.root / "second-state.json"
        command = [sys.executable, str(Path(guard.__file__).resolve()), "init", "--state", str(path),
                   "--gpt-sample", str(self.source), "--deadline", "2099-01-01T00:00:00Z"]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        before = path.read_bytes()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(before, path.read_bytes())

    def test_successful_leader_does_not_leave_background_group_children(self):
        marker = self.root / "background.heartbeat"
        child_code = f"import time; from pathlib import Path; p=Path({str(marker)!r});\nwhile True: p.write_text(str(time.time())); time.sleep(.02)"
        leader_code = f"import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c',{child_code!r}]); p=Path({str(marker)!r});\nwhile not p.exists(): time.sleep(.01)"
        result = subprocess.run(self.base + [sys.executable, "-c", leader_code], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        before = marker.read_text(); time.sleep(.15)
        self.assertEqual(before, marker.read_text())


if __name__ == "__main__":
    unittest.main()
