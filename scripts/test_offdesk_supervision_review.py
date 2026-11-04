"""Deterministic stop-causality and recovery-order regressions, without models."""
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import offdesk_supervision_review as review


class StopReviewTests(unittest.TestCase):
    def setUp(self):
        self.row = {'request_id': 1, 'method': 'POST', 'path': '/v1/responses',
                    'headers_at': 104, 'first_byte_at': 104, 'last_byte_at': 279,
                    'response_bytes': 10000, 'status': 200, 'error_class': None,
                    'terminal_frame_observed': False}
        self.summary = {'native_stop': {'reason': 'no_tool_progress', 'observed_at': 280,
                                      'active_requests': [self.row]}, 'events_dropped': 0,
                        'requests': []}
        self.result = {'stop_reason': 'no_tool_progress', 'started_tool_calls': 0,
                       'completed_tool_calls': 0, 'in_flight_tool_calls': 0,
                       'seconds_without_tool_progress': 180}
        self.events = [{'event': 'stream_frame', 'observed_at': 279,
                        'request_id': 1, 'kind': 'reasoning'}]

    def observe(self):
        return review.review_stop(self.summary, self.result, self.events, freshness_seconds=5)

    def state(self):
        return self.observe()['inference_observations'][0]['state']

    def test_streaming_reasoning_does_not_become_tool_progress_or_timeout_extension(self):
        result = self.observe()
        self.assertEqual(result['tool_state'], 'no_command_dispatched')
        self.assertEqual(self.state(), 'recent_classified_frames')
        self.assertEqual(result['seconds_without_tool_progress'], 180)
        for field in ('stop_policy_changed', 'runtime_resume_authorized', 'source_correctness_established'):
            self.assertFalse(result[field])
        self.assertIsNone(result['backend_cancellation_acknowledged'])

    def test_wire_states_distinguish_no_response_silence_and_framing_uncertainty(self):
        original = copy.deepcopy(self.row)
        cases = [({'headers_at': None, 'first_byte_at': None, 'last_byte_at': None}, 'awaiting_headers'),
                 ({'first_byte_at': None, 'last_byte_at': None}, 'awaiting_first_byte'),
                 ({'last_byte_at': 270}, 'no_recent_response_bytes'),
                 ({'last_byte_at': 281}, 'timestamp_inconsistent'),
                 ({'status': 500}, 'http_error_observed'),
                 ({'error_class': 'ConnectionResetError'}, 'local_transport_error_observed')]
        for fields, expected in cases:
            with self.subTest(expected=expected):
                self.row.update(original)
                self.row.update(fields)
                self.assertEqual(self.state(), expected)
        self.row.update(original)
        self.events[0]['observed_at'] = 270
        self.assertEqual(self.state(), 'recent_bytes_without_recent_classified_frame')

    def test_post_cancel_terminal_and_error_cannot_rewrite_stop_cause(self):
        self.summary['requests'] = [{**self.row, 'error_class': 'AttributeError',
                                     'terminal_frame_observed': True}]
        self.events.extend([{'event': 'stream_frame', 'observed_at': 281,
                             'request_id': 1, 'kind': 'terminal'},
                            {'event': 'request_finished', 'observed_at': 282,
                             **self.summary['requests'][0]}])
        result = self.observe()
        self.assertEqual(result['events_after_stop_excluded'], 2)
        self.assertEqual(self.state(), 'recent_classified_frames')
        self.assertFalse(result['inference_observations'][0]['terminal_observed_before_stop'])

    def test_completed_response_is_independent_of_native_command_dispatch(self):
        self.summary['native_stop']['active_requests'] = []
        self.events.append({'event': 'request_finished', 'observed_at': 279.5,
                            **self.row, 'terminal_frame_observed': True})
        result = self.observe()
        self.assertEqual(self.state(), 'response_finished')
        self.assertEqual(result['tool_state'], 'no_command_dispatched')
        self.assertTrue(result['inference_observations'][0]['terminal_observed_before_stop'])

    def test_metadata_only_missing_and_dropped_observations_are_not_inference_success(self):
        self.row['path'] = '/api/tags'
        result = self.observe()
        self.assertFalse(result['inference_observation_available'])
        self.summary['events_dropped'] = 3
        self.assertFalse(self.observe()['event_log_complete'])

    def selected_control_fixture(self):
        finished = {'event':'request_finished', 'event_sequence':1, 'observed_at':279.5,
                    **self.row, 'request_id':2, 'terminal_frame_observed':True}
        after = {'event':'native_event', 'event_sequence':3, 'observed_at':281,
                 'native_type':'turn.completed', 'item_type':'other', 'notice':None}
        self.events[0]['event_sequence'] = 2
        events = [finished, self.events[0], after]
        controls = [finished, after]
        self.summary['events_dropped'] = 20
        self.summary['event_retention'] = {'schema':'offdesk_event_retention.v1',
            'control_events_written':2, 'control_events_dropped':0}
        return events, controls

    def test_control_and_detail_merge_deduplicates_without_hiding_frame_loss(self):
        events, controls = self.selected_control_fixture()
        result = review.review_stop(self.summary, self.result, events, freshness_seconds=5, control_events=controls)
        self.assertEqual(result['events_after_stop_excluded'], 1)
        self.assertTrue(result['control_log_complete'])
        self.assertFalse(result['event_log_complete'])
        self.assertTrue(result['control_and_detail_identity_deduplicated'])
        self.assertEqual(len(result['inference_observations']), 2)
        self.assertEqual(result['inference_observations'][0]['classified_frame_kinds_before_stop'], {'reasoning':1})

    def test_missing_conflicting_or_unordered_control_identity_refused(self):
        events, controls = self.selected_control_fixture()
        for mode in ('count', 'conflict', 'duplicate', 'unordered', 'detail-in-control'):
            e, c = copy.deepcopy(events), copy.deepcopy(controls)
            if mode == 'count': c.pop()
            if mode == 'conflict': c[0]['terminal_frame_observed'] = False
            if mode == 'duplicate': c[1] = copy.deepcopy(c[0])
            if mode == 'unordered': c.reverse()
            if mode == 'detail-in-control': c[1] = copy.deepcopy(e[1])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                review.review_stop(self.summary, self.result, e, freshness_seconds=5, control_events=c)

    def test_independent_control_loss_is_reported(self):
        events, controls = self.selected_control_fixture()
        self.summary['event_retention']['control_events_dropped'] = 4
        result = review.review_stop(self.summary, self.result, events, freshness_seconds=5, control_events=controls)
        self.assertFalse(result['control_log_complete'])
        self.assertFalse(result['event_log_complete'])

    def test_pre_stop_frame_aggregate_remains_available_after_frame_log_exhaustion(self):
        self.events = []
        self.summary['events_dropped'] = 123
        self.row.update(frames=6000, frame_kind_counts={'reasoning':6000},
                        last_frame_kind='reasoning', last_frame_at=279.9)
        result = self.observe()
        row = result['inference_observations'][0]
        self.assertEqual(row['frame_kind_counts_at_stop'], {'reasoning':6000})
        self.assertEqual(row['last_frame_kind_at_stop'], 'reasoning')
        self.assertEqual(row['classified_frame_kinds_before_stop'], {})
        self.assertAlmostEqual(row['last_classified_frame_age_seconds'], .1)
        self.assertEqual(row['state'], 'recent_classified_frames')
        self.assertFalse(result['event_log_complete'])
        self.assertFalse(result['runtime_resume_authorized'])
        self.summary['requests'] = [{**self.row, 'frames':99999, 'terminal_frame_observed':True}]
        self.assertFalse(self.observe()['inference_observations'][0]['terminal_observed_before_stop'])

    def test_inconsistent_or_post_stop_frame_snapshot_refused(self):
        original = copy.deepcopy(self.row)
        for mode in ('total', 'kind', 'post-stop'):
            self.row.clear()
            self.row.update(original, frames=1, frame_kind_counts={'reasoning':1},
                            last_frame_kind='reasoning', last_frame_at=279)
            if mode == 'total': self.row['frames'] = 2
            if mode == 'kind': self.row['frame_kind_counts'] = {'private-arbitrary-key':1}
            if mode == 'post-stop': self.row['last_frame_at'] = 281
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.observe()

    def test_cli_requires_explicit_control_input_and_verifies_its_hash(self):
        events, controls = self.selected_control_fixture()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = ''.join(json.dumps(row)+'\n' for row in controls).encode()
            self.summary['event_retention']['control_sha256'] = hashlib.sha256(raw).hexdigest()
            (root/'summary.json').write_text(json.dumps(self.summary))
            (root/'result.json').write_text(json.dumps(self.result))
            (root/'events.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in events))
            (root/'control.jsonl').write_bytes(raw)
            argv = [sys.executable,'-I','-S',review.__file__,'--summary',str(root/'summary.json'),
                    '--native-result',str(root/'result.json'),'--events',str(root/'events.jsonl'),
                    '--control-events',str(root/'control.jsonl'),'--freshness-seconds','5']
            before = {p.name:p.read_bytes() for p in root.iterdir()}
            result = subprocess.run(argv,capture_output=True,text=True,check=True)
            parsed = json.loads(result.stdout)
            self.assertEqual(len(parsed['input_sha256']), 4)
            self.assertTrue(parsed['control_log_complete'])
            self.assertEqual(before, {p.name:p.read_bytes() for p in root.iterdir()})
            (root/'control.jsonl').write_bytes(raw+b'\n')
            result = subprocess.run(argv,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('hash changed',result.stderr)

    def test_concurrent_requests_keep_individual_states_and_tool_clock_separate(self):
        self.summary['native_stop']['active_requests'].append(
            {**self.row, 'request_id': 2, 'last_byte_at': 270})
        self.result.update(started_tool_calls=1, in_flight_tool_calls=1)
        result = self.observe()
        self.assertEqual(result['tool_state'], 'command_in_flight')
        self.assertEqual([r['state'] for r in result['inference_observations']],
                         ['recent_classified_frames', 'no_recent_response_bytes'])
        self.result.update(started_tool_calls=0, completed_tool_calls=1, in_flight_tool_calls=0)
        self.assertEqual(self.observe()['tool_state'], 'command_completed')

    def test_invalid_clock_and_mismatched_stop_fail_closed(self):
        for value in (math.nan, math.inf, -1, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.review_stop(self.summary, self.result, self.events, freshness_seconds=value)
        self.result['stop_reason'] = 'deadline_reached'
        with self.assertRaises(ValueError):
            self.observe()

    def test_cli_is_read_only_and_emits_only_selected_metadata(self):
        self.row['private_content'] = 'DO_NOT_EMIT'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'summary.json').write_text(json.dumps(self.summary))
            (root / 'result.json').write_text(json.dumps(self.result))
            (root / 'events.jsonl').write_text('\n'.join(map(json.dumps, self.events)))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            result = subprocess.run([sys.executable, '-I', '-S', review.__file__,
                                     '--summary', str(root / 'summary.json'),
                                     '--native-result', str(root / 'result.json'),
                                     '--events', str(root / 'events.jsonl'),
                                     '--freshness-seconds', '5'], capture_output=True, text=True, check=True)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            self.assertNotIn('DO_NOT_EMIT', result.stdout)
            self.assertEqual(len(json.loads(result.stdout)['input_sha256']), 3)


class RecoveryReviewTests(unittest.TestCase):
    def setUp(self):
        self.argv = ['python3', '-I', '-S', '.runtime/offdesk_native_worker.py', 'recovery-context',
                     '--expected-progress-sha256', 'pinned']
        self.pin = {'progress_sha256': 'pinned', 'claims_verified': False}

    def event(self, kind='item.completed', identifier='recovery', command=None, receipt=None, exit_code=0):
        import shlex
        return {'type': kind, 'item': {'id': identifier, 'type': 'command_execution',
                'command': command or shlex.join(self.argv), 'exit_code': exit_code,
                'aggregated_output': json.dumps(self.pin if receipt is None else receipt)}}

    def check(self, events):
        return review.recovery_order(events, self.argv, self.pin)

    def test_host_prepared_pin_and_reasoning_without_commands_do_not_prove_recovery(self):
        result = self.check([{'type': 'item.completed', 'item': {'type': 'reasoning'}}])
        self.assertEqual(result['state'], 'not_dispatched')
        self.assertFalse(result['first_recovery_verified'])

    def test_recovery_requires_first_start_and_exact_pinned_arguments_and_receipt(self):
        cases = [([self.event('item.started')], 'not_completed'),
                 ([self.event(exit_code=1)], 'command_failed'),
                 ([self.event(receipt={'progress_sha256': 'stale'})], 'receipt_mismatch'),
                 ([self.event(command='echo recovery')], 'wrong_first_command'),
                 ([self.event('item.started', 'edit', 'edit source'), self.event()], 'wrong_first_command'),
                 ([self.event(command='python3 -I -S .runtime/offdesk_native_worker.py recovery-context')],
                  'wrong_first_command')]
        for events, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(self.check(events)['state'], expected)

    def test_exact_recovery_receipt_with_replays_is_only_post_execution_verification(self):
        result = self.check([self.event('item.started'), self.event(), self.event()])
        self.assertTrue(result['first_recovery_verified'])
        self.assertEqual(result['observed_unique_commands'], 1)
        self.assertFalse(result['admission_fencing'])
        self.assertFalse(result['implementation_qualified'])
        self.assertFalse(result['runtime_resume_authorized'])

    def test_selected_root_cd_uses_the_same_restricted_parser_as_native_receipts(self):
        import shlex
        root = Path('/var/tmp/recovery root')
        raw = shlex.join(['/usr/bin/bash', '-lc', 'cd ' + shlex.quote(str(root))
                          + ' && ' + shlex.join(self.argv)])
        result = review.recovery_order([self.event(command=raw)], self.argv, self.pin, root)
        self.assertTrue(result['first_recovery_verified'])
        for unsafe in (raw.replace(str(root), '/var/tmp/other'),
                       shlex.join(['/usr/bin/bash', '-lc', shlex.join(self.argv) + '; echo extra'])):
            with self.subTest(command=unsafe):
                result = review.recovery_order([self.event(command=unsafe)], self.argv, self.pin, root)
                self.assertFalse(result['first_recovery_verified'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
