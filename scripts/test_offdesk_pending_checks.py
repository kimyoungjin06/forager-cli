"""Controlled children and source fixtures only; no model or generated decoder."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import types
import unittest

import offdesk_four_phase_run as batch
import offdesk_independent_qualification as driver
import offdesk_native_worker as worker


def event(identifier='check', started=True, raw=None, code=0):
    return {'type': 'item.started' if started else 'item.completed', 'item': {
        'type': 'command_execution', 'id': identifier,
        'command': raw or 'python3 -I -S .runtime/check.py',
        'exit_code': None if started else code, 'aggregated_output': ''}}


class PendingCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forager-pending-check-',
            dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'scripts/selected.py'
        self.source.parent.mkdir()
        self.source.write_text('# Scope-only control source, no decoder\n')
        (self.root / '.runtime').mkdir()
        self.out = self.root / '.night/qualification'
        self.out.mkdir(parents=True)
        self.phase = {'id': 'PENDING-FIXTURE', 'write_paths': ['scripts/selected.py']}
        self.run = types.SimpleNamespace(root=self.root, output=self.out)
        self.records = {}
        self.guard = driver.ImplementationGuard(32, lambda: batch.inventory(self.root),
            lambda: {'claims_verified': False},
            lambda name, value: self.records.__setitem__(name, copy.deepcopy(value)),
            selected_checks=[['/usr/bin/python3', '-I', '-S', '.runtime/check.py']],
            failure_capture=lambda item: {'capture_status': 'failure-control-only'},
            start_capture=lambda item, source: driver.capture_selected_check_start(
                self.run, self.phase, item, source, batch))
        self.guard.check_root = self.root

    def observe(self, result=None):
        if result is None:
            result = {'stop_reason': 'active_command_timeout', 'exit_code': -15, 'turn_completed': False}
        receipt = driver.selected_check_observation(self.run, self.phase, self.guard, result, worker, batch)
        return receipt, json.loads((self.root / receipt['artifact_path']).read_text())

    def test_start_captures_bytes_once_and_matching_completion_closes_pending_check(self):
        self.assertIsNone(self.guard.feed(event()))
        snapshot = self.guard.pending_check['start_snapshot']
        self.assertEqual((self.root / snapshot['sources'][0]['path']).read_bytes(), self.source.read_bytes())
        self.assertIsNone(self.guard.feed(event()))
        self.assertIsNone(self.guard.feed(event(started=False)))
        self.assertIsNone(self.guard.pending_check)
        self.assertEqual(len(self.guard.check_observations), 1)
        self.assertEqual(self.guard.calls, 1)
        receipt, body = self.observe({'stop_reason': None, 'exit_code': 0, 'turn_completed': True})
        self.assertEqual(receipt['checks'][0]['start_capture_integrity'], 'verified')
        self.assertEqual(body['pending_at_native_stop'], [])
        self.assertFalse(body['checks'][0]['checker_loaded_source_identity_confirmed'])
        self.assertIsNone(body['reported_counts'])

    def test_another_command_is_overlap_even_without_source_change(self):
        self.guard.feed(event())
        self.assertEqual(self.guard.feed(event('other', raw="python3 -c 'pass'")), 'selected_check_overlap')
        self.assertTrue(self.guard.pending_check['source_unchanged_at_last_observation'])
        self.assertEqual(self.guard.calls, 2)
        receipt, body = self.observe()
        self.assertEqual(receipt['pending_at_native_stop'], ['check'])
        self.assertEqual(body['checks'][0]['overlapping_commands'][0]['native_item_id'], 'other')

    def test_completion_only_overlap_is_still_observed_and_charged(self):
        self.guard.feed(event())
        self.assertEqual(self.guard.feed(event('other', started=False, raw="python3 -c 'pass'")),
                         'selected_check_overlap')
        self.assertEqual(self.guard.calls, 2)

    def test_source_change_is_detected_on_a_noncommand_event(self):
        self.guard.feed(event())
        prior = self.guard.pending_check['start_snapshot']['sources'][0]
        self.source.write_text('# changed before any completed result\n')
        notice = {'type': 'item.completed', 'item': {'id': 'message', 'type': 'agent_message', 'text': 'Waiting'}}
        self.assertEqual(self.guard.feed(notice), 'selected_check_source_changed')
        receipt, body = self.observe()
        self.assertEqual((self.root / prior['path']).read_text(), '# Scope-only control source, no decoder\n')
        self.assertFalse(receipt['checks'][0]['current_source_matches_observed_start'])
        self.assertIsNone(body['reported_counts'])

    def test_zero_exit_does_not_erase_observed_source_change(self):
        self.guard.feed(event())
        self.source.write_text('# changed while selected check was pending\n')
        self.assertEqual(self.guard.feed(event(started=False)), 'selected_check_source_changed')
        self.assertIsNone(self.guard.pending_check)
        self.assertEqual(self.guard.feed({'type': 'turn.completed'}), 'selected_check_source_changed')

    def test_changed_command_with_same_native_id_is_not_a_matching_completion(self):
        self.guard.feed(event())
        self.assertEqual(self.guard.feed(event(raw="python3 -c 'pass'")), 'selected_check_identity_changed')

    def test_turn_completion_while_checker_is_pending_stops_without_counting_it_complete(self):
        self.guard.feed(event())
        self.assertEqual(self.guard.feed({'type': 'turn.completed'}), 'selected_check_incomplete')
        receipt, body = self.observe()
        self.assertFalse(receipt['checks'][0]['completion_observed'])
        self.assertIsNone(body['checks'][0]['exit_code'])

    def test_zero_exit_without_start_has_unknown_source_and_cannot_pass(self):
        self.assertEqual(self.guard.feed(event(started=False)), 'selected_check_start_unobserved')
        receipt, body = self.observe()
        self.assertFalse(receipt['checks'][0]['start_observed'])
        self.assertEqual(receipt['checks'][0]['start_capture_integrity'], 'unverified')
        self.assertIsNone(body['checks'][0]['source_identity'])

    def test_missing_capture_callback_stops_instead_of_claiming_a_snapshot(self):
        self.guard.start_capture = None
        self.assertEqual(self.guard.feed(event()), 'selected_check_capture_failed')
        receipt, body = self.observe()
        self.assertEqual(receipt['checks'][0]['start_capture_integrity'], 'unverified')
        self.assertIsNone(body['reported_counts'])

    def test_snapshot_collision_preserves_existing_file_and_stops(self):
        prefix = self.phase['id'] + '-check-start-' + hashlib.sha256(b'check').hexdigest()
        collision = self.out / (prefix + '-source-0')
        collision.write_text('preserve existing artifact\n')
        self.assertEqual(self.guard.feed(event()), 'selected_check_capture_failed')
        self.assertEqual(collision.read_text(), 'preserve existing artifact\n')
        receipt, body = self.observe()
        self.assertEqual(receipt['checks'][0]['start_capture_integrity'], 'unverified')
        self.assertIsNone(body['reported_counts'])

    def test_source_symlink_is_not_followed_for_a_start_capture(self):
        other = self.root / '.runtime/canary'
        other.write_text('retain\n')
        self.source.unlink()
        self.source.symlink_to(other)
        self.assertEqual(self.guard.feed(event()), 'selected_check_capture_failed')
        self.assertEqual(other.read_text(), 'retain\n')
        self.assertFalse(self.guard.pending_check['start_snapshot']['sources'])

    def test_tampered_start_copy_is_not_credited_as_verified(self):
        self.guard.feed(event())
        snapshot = self.guard.pending_check['start_snapshot']['sources'][0]
        self.guard.feed(event(started=False))
        (self.root / snapshot['path']).write_text('# tampered stored copy\n')
        receipt, body = self.observe()
        self.assertEqual(receipt['checks'][0]['start_capture_integrity'], 'unverified')
        self.assertTrue(receipt['checks'][0]['current_source_matches_observed_start'])
        self.assertIsNone(body['reported_counts'])

    def test_shared_command_limit_remains_primary_with_pending_overlap_evidence(self):
        self.guard.max_calls = 1
        self.guard.feed(event())
        self.assertEqual(self.guard.feed(event('other', raw="python3 -c 'pass'")), 'tool_call_budget_reached')
        self.assertEqual(self.guard.check_stop_reason, 'selected_check_overlap')

    def test_real_local_child_timeout_retains_pending_source_and_reaps(self):
        code = ('import sys,json,time;from pathlib import Path;sys.stdin.read();'
                'print(json.dumps(' + repr(event()) + '),flush=True);'
                "time.sleep(10);Path('late-write').write_text('unexpected')")
        result = worker.supervise([sys.executable, '-I', '-S', '-c', code],
            'Control event only, no checker or model', self.root, self.root / '.night/control',
            time.time() + 5, self.guard, dict(os.environ), idle_seconds=1, command_seconds=.2)
        self.assertEqual(result['stop_reason'], 'active_command_timeout')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertFalse(result['turn_completed'])
        self.assertEqual(result['completed_tool_calls'], 0)
        self.assertFalse((self.root / 'late-write').exists())
        receipt, body = self.observe(result)
        self.assertEqual(body['primary_native_stop']['stop_reason'], 'active_command_timeout')
        self.assertEqual(receipt['pending_at_native_stop'], ['check'])
        self.assertIsNone(body['reported_counts'])

    def test_overlap_reaps_but_preserves_an_edit_that_already_happened(self):
        overlap = event('edit', raw="python3 -c 'pass'")
        code = ('import sys,json,time;from pathlib import Path;sys.stdin.read();'
                'print(json.dumps(' + repr(event()) + '),flush=True);time.sleep(.15);'
                "Path('scripts/selected.py').write_text('# edit before its start notice\\n');"
                'print(json.dumps(' + repr(overlap) + '),flush=True);'
                "time.sleep(10);Path('late-write').write_text('unexpected')")
        result = worker.supervise([sys.executable, '-I', '-S', '-c', code],
            'Controlled overlap, no model or checker', self.root, self.root / '.night/control',
            time.time() + 5, self.guard, dict(os.environ), idle_seconds=1, command_seconds=2)
        self.assertEqual(result['stop_reason'], 'selected_check_overlap')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertEqual(self.source.read_text(), '# edit before its start notice\n')
        self.assertFalse((self.root / 'late-write').exists())
        receipt, body = self.observe(result)
        self.assertFalse(receipt['checks'][0]['current_source_matches_observed_start'])
        self.assertFalse(body['admission_fencing'])


if __name__ == '__main__':
    unittest.main()
