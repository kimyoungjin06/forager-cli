"""Independent qualification audit and bounded implementation regressions."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import offdesk_independent_qualification as driver
import offdesk_four_phase_run as batch
import offdesk_native_worker as worker
import offdesk_qualification_packet as packet_tools


def command(identifier, raw, output=None, code=0, started=False):
    return {'type': 'item.started' if started else 'item.completed', 'item': {
        'id': identifier, 'type': 'command_execution', 'command': raw,
        'exit_code': code, 'aggregated_output': json.dumps(output or {})}}


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.phase = {'write_paths': ['scripts/offdesk_request_telemetry.py'],
                      'checks': [['/usr/bin/python3', '-I', '-S', '.runtime/test_adapter.py'],
                                 ['/usr/bin/python3', '-I', '-S', '.runtime/verify_stream_frames.py']]}
        self.before = {'scripts/offdesk_request_telemetry.py': {'kind': 'file', 'sha256': 'before'}}
        self.after = {'scripts/offdesk_request_telemetry.py': {'kind': 'file', 'sha256': 'after'}}
        self.pin = {'fixture': 'checked recovery'}
        self.rows = [command('recovery', 'python3 -I -S .runtime/offdesk_native_worker.py recovery-context', self.pin)['item']]
        self.rows += [command('check-' + str(i), shlex.join(argv))['item'] for i, argv in enumerate(self.phase['checks'])]
        self.rows += [self.progress('final')]

    def progress(self, sha):
        return command(sha, 'python3 -I -S .runtime/offdesk_native_worker.py progress',
                       {'sha256': sha, 'claims_verified': False})['item']

    def audit(self):
        return driver.implementation_audit(self.before, self.after, self.phase, self.rows, self.pin, 'final')

    def test_progress_guidance_requires_a_new_standalone_call_and_audit_rejects_combining(self):
        prompt = driver.plain_progress_instructions('.night/context.json', 'context-sha')
        self.assertIn('separate single command', prompt)
        self.assertIn('Never join it to an edit heredoc', prompt)
        self.assertIn('new command-execution call', prompt)
        combined = ("cat > scripts/offdesk_request_telemetry.py <<'PY'\n# fixture\nPY\n"
                    "python3 -I -S .runtime/offdesk_native_worker.py progress")
        self.rows.insert(1, command('combined-save', combined)['item'])
        with self.assertRaisesRegex(ValueError, 'Helper invocations must use one simple'):
            self.audit()

    def test_r53_checker_inspections_are_not_executions(self):
        reads = ["bash -lc 'cat .runtime/test_adapter.py'",
                 'cat -n .runtime/verify_stream_frames.py',
                 "sed -n '38,64p' .runtime/verify_stream_frames.py",
                 "cat .runtime/test_adapter.py; echo '-----'; cat .runtime/verify_stream_frames.py",
                 'wc -l .runtime/test_adapter.py .runtime/verify_stream_frames.py 2>/dev/null',
                 'cat .runtime/verify_stream_frames.py | head -30']
        for i, raw in enumerate(reads):
            self.rows.insert(1, command('read-' + str(i), raw)['item'])
        result = self.audit()
        self.assertEqual(len(result['explicit_selected_check_invocations']), 2)
        self.assertEqual(result['intermediate_progress_saves'], 0)

    def test_inspection_alone_cannot_satisfy_a_check(self):
        self.rows[1] = command('read', 'cat .runtime/test_adapter.py')['item']
        with self.assertRaisesRegex(ValueError, 'exactly once'):
            self.audit()

    def test_actual_nested_execution_is_rejected(self):
        commands = ["bash -lc 'echo extra; python3 -I -S .runtime/verify_stream_frames.py'",
                    'cat .runtime/test_adapter.py && python3 -I -S .runtime/test_adapter.py',
                    'echo $(python3 -I -S .runtime/test_adapter.py)',
                    'echo `python3 -I -S .runtime/test_adapter.py`',
                    'bash -c "bash -c \'python3 -I -S .runtime/test_adapter.py\'"',
                    'env python3 -I -S .runtime/test_adapter.py',
                    'cat .runtime/test_adapter.py > .runtime/test_adapter.py',
                    "sed -n '1e python3 .runtime/test_adapter.py' .runtime/test_adapter.py",
                    'rg --pre=python3 .runtime/test_adapter.py']
        for raw in commands:
            with self.subTest(raw=raw):
                original = list(self.rows)
                self.rows.insert(1, command('nested', raw)['item'])
                with self.assertRaisesRegex(ValueError, 'nested'):
                    self.audit()
                self.rows = original

    def test_duplicate_missing_and_failed_checks_are_rejected(self):
        originals = copy.deepcopy(self.rows)
        for mode in ('duplicate', 'missing', 'failed'):
            with self.subTest(mode=mode):
                self.rows = copy.deepcopy(originals)
                if mode == 'duplicate': self.rows.insert(2, copy.deepcopy(self.rows[1]))
                if mode == 'missing': del self.rows[1]
                if mode == 'failed': self.rows[1]['exit_code'] = 1
                with self.assertRaises(ValueError): self.audit()

    def test_two_intermediate_saves_and_exact_final_receipt(self):
        self.rows.insert(1, self.progress('inspected'))
        self.rows.insert(2, self.progress('edited'))
        self.assertEqual(self.audit()['intermediate_progress_saves'], 2)
        self.rows.insert(3, self.progress('excess'))
        with self.assertRaisesRegex(ValueError, 'intermediate'): self.audit()

    def test_stale_duplicate_final_and_verified_claim_are_rejected(self):
        originals = copy.deepcopy(self.rows)
        for mode in ('stale', 'duplicate', 'claims'):
            with self.subTest(mode=mode):
                self.rows = copy.deepcopy(originals)
                if mode == 'stale': self.rows[-1] = self.progress('stale')
                if mode == 'duplicate': self.rows.insert(1, self.progress('final'))
                if mode == 'claims': self.rows[-1]['aggregated_output'] = json.dumps({'sha256': 'final', 'claims_verified': True})
                with self.assertRaisesRegex(ValueError, 'final progress'): self.audit()

    def test_scope_noop_symlink_and_deletion_are_rejected(self):
        for after in (self.before, {}, {'scripts/offdesk_request_telemetry.py': {'kind': 'symlink'}},
                      {**self.after, 'scripts/test_offdesk_request_telemetry.py': {'kind': 'file'}}):
            with self.subTest(after=after), self.assertRaises(ValueError):
                driver.implementation_audit(self.before, after, self.phase, self.rows, self.pin, 'final')

    def test_stdin_progress_is_rejected(self):
        self.rows[-1]['command'] += ' --request-stdin'
        with self.assertRaisesRegex(ValueError, 'plain string'): self.audit()


class HelperCommandTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/var/tmp/selected root's task")
        self.helper = 'python3 -I -S .runtime/offdesk_native_worker.py progress'
        self.receipt = {'sha256': 'saved', 'claims_verified': False}

    def wrapped(self, inner):
        return shlex.join(['/usr/bin/bash', '-lc', inner])

    def parse(self, raw):
        return driver.helper_receipt(command('helper', raw, self.receipt)['item'], 'progress', self.root)

    def test_r59_exact_root_prefix_and_literal_assessment_preserve_receipt(self):
        raw = self.helper + ' --assessment ' + shlex.quote('actual; (literal) $text `text` && text')
        for value in (raw, self.wrapped(raw), 'cd ' + shlex.quote(str(self.root)) + ' && ' + raw,
                      self.wrapped('cd ' + shlex.quote(str(self.root)) + ' && ' + raw)):
            with self.subTest(command=value):
                self.assertEqual(self.parse(value), self.receipt)

    def test_only_exact_known_root_and_one_simple_command_are_recognized(self):
        prefix = 'cd ' + shlex.quote(str(self.root)) + ' && '
        invalid = ['cd /var/tmp/other && ' + self.helper,
                   'cd . && ' + self.helper,
                   'cd ' + shlex.quote(str(self.root / 'sub' / '..')) + ' && ' + self.helper,
                   prefix + 'cd /tmp && ' + self.helper,
                   prefix + self.helper + '; echo extra',
                   prefix + self.helper + ' && echo extra',
                   prefix + self.helper + ' | cat',
                   prefix + self.helper + ' > result.json',
                   prefix + self.helper + '\necho extra',
                   prefix + 'env ' + self.helper,
                   prefix + self.wrapped(self.helper),
                   self.helper.replace('python3', '/var/tmp/python3', 1),
                   self.helper + ' --assessment "$(echo expanded)"',
                   self.helper + ' --assessment `echo expanded`',
                   self.helper + ' --assessment $VALUE',
                   self.helper + ' --assessment unquoted*',
                   self.helper + ' # extra',
                   'echo ' + shlex.quote(self.helper)]
        for value in invalid:
            with self.subTest(command=value), self.assertRaises(ValueError):
                self.parse(self.wrapped(value))
        with self.assertRaises(ValueError):
            driver.helper_receipt(command('unknown', prefix + self.helper, self.receipt)['item'], 'progress')
        with self.assertRaises(ValueError):
            self.parse(shlex.join(['/var/tmp/bash', '-lc', self.helper]))

    def test_failed_wrong_action_and_extra_output_are_still_refused(self):
        item = command('helper', 'cd ' + shlex.quote(str(self.root)) + ' && ' + self.helper,
                       self.receipt)['item']
        with self.assertRaises(ValueError):
            driver.helper_receipt({**item, 'exit_code': 1}, 'progress', self.root)
        with self.assertRaises(ValueError):
            driver.helper_receipt(item, 'recovery-context', self.root)
        for output in ('{}\n{}', '[]', '{bad', json.dumps(self.receipt) + ' unexpected'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                driver.helper_receipt({**item, 'aggregated_output': output}, 'progress', self.root)

    def test_recovery_checks_and_progress_share_root_normalization_without_extra_credit(self):
        audit = AuditTests()
        audit.setUp()
        for item in audit.rows:
            item['command'] = self.wrapped('cd ' + shlex.quote(str(self.root)) + ' && ' + item['command'])
        result = driver.implementation_audit(audit.before, audit.after, audit.phase,
                                           audit.rows, audit.pin, 'final', self.root)
        self.assertEqual(len(result['explicit_selected_check_invocations']), 2)
        self.assertTrue(result['final_progress_receipt_verified'])
        audit.rows.insert(1, command('nested', self.wrapped('cd ' + shlex.quote(str(self.root))
            + ' && ' + self.helper + '; echo extra'), self.receipt)['item'])
        with self.assertRaisesRegex(ValueError, 'simple selected command'):
            driver.implementation_audit(audit.before, audit.after, audit.phase,
                                        audit.rows, audit.pin, 'final', self.root)


class ImplementationGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = {'selected.py': 'before'}
        self.progress = {'status': 'bound_model_report', 'sha256': 'latest', 'claims_verified': False}
        self.records = {}
        self.guard = driver.ImplementationGuard(32, lambda: dict(self.source),
            lambda: dict(self.progress), lambda name, value: self.records.__setitem__(name, value))

    def test_varied_read_loop_stops_on_fifth_observation(self):
        for i, raw in enumerate(('cat source.py', 'head -30 source.py', 'wc -l source.py',
                                 "sed -n '1,60p' source.py", 'rg -n def source.py')):
            reason = self.guard.feed(command(str(i), raw, started=True))
            self.assertEqual(reason, 'inspection_budget_reached_without_edit' if i == 4 else None)
        self.assertEqual(self.guard.calls, 5)

    def test_replayed_start_and_completion_share_inspection_credit(self):
        start = command('read', 'cat source.py', started=True)
        for event in (start, start, command('read', 'cat source.py'), command('read', 'cat source.py')):
            self.assertIsNone(self.guard.feed(event))
        self.assertEqual(len(self.guard.inspection_items), 1)
        self.assertEqual(self.guard.calls, 1)

    def test_progress_save_does_not_reset_inspection_count(self):
        self.guard.feed(command('read', 'cat source.py'))
        self.guard.feed(command('save', 'python3 -I -S .runtime/offdesk_native_worker.py progress'))
        self.assertEqual(len(self.guard.inspection_items), 1)
        self.assertEqual(self.guard.calls, 2)

    def test_excerpt_helper_counts_as_inspection(self):
        self.guard.feed(command('excerpt', 'python3 -I -S .runtime/offdesk_native_worker.py excerpt --root . --path source.py --start 1 --lines 60',
                                {'text': '1: source\n', 'lines_emitted': 1}))
        self.assertEqual(len(self.guard.inspection_items), 1)

    def read_output(self, raw, text, identifier='read', started=False):
        event = command(identifier, raw, started=started)
        event['item']['aggregated_output'] = text
        return event

    def test_line_ceiling_counts_completed_output_once_including_compound_reads(self):
        for raw in ("sed -n '220,310p' source.py", 'cat first.py; cat second.py',
                    'grep -n def source.py | head -60',
                    'cd /var/tmp/selected && cat source.py',
                    shlex.join(['/usr/bin/bash', '-lc', 'cd /var/tmp/selected && cat source.py'])):
            with self.subTest(raw=raw):
                self.setUp()
                self.guard.check_root = Path('/var/tmp/selected')
                start = self.read_output(raw, '', started=True)
                self.assertIsNone(self.guard.feed(start))
                completed = self.read_output(raw, 'x\r\n' * 60)
                self.assertIsNone(self.guard.feed(completed))
                self.assertIsNone(self.guard.feed(completed))
                self.assertEqual(self.guard.calls, 1)
                self.assertEqual(len(self.guard.inspection_outputs), 1)
                self.assertEqual(self.guard.inspection_outputs['read']['lines'], 60)
                self.assertEqual(self.guard.feed(self.read_output(raw, 'x\n' * 60 + 'tail', 'overflow')),
                                 'inspection_output_limit_exceeded')

    def test_utf8_byte_ceiling_and_output_after_edit_remain_bounded(self):
        self.source['selected.py'] = 'edited'
        self.assertIsNone(self.guard.feed(self.read_output('head -c 8192 source.py', '\u00e9' * 4096)))
        self.assertTrue(self.guard.edit_observed)
        self.assertEqual(len(self.guard.inspection_items), 0)
        self.assertEqual(self.guard.feed(self.read_output('cat source.py', '\u00e9' * 4097, 'overflow')),
                         'inspection_output_limit_exceeded')
        row = self.records['IMPLEMENTATION_GUARD_STATE.json']
        self.assertEqual(row['inspection_outputs'][-1]['utf8_bytes'], 8194)
        self.assertNotIn('\u00e9', json.dumps(row, ensure_ascii=False))
        self.assertFalse(row['admission_fencing'])

    def test_excerpt_json_cannot_hide_decoded_source_lines(self):
        raw = 'python3 -I -S .runtime/offdesk_native_worker.py excerpt --root . --path source.py --lines 80'
        for i, suffix in enumerate(('', ' | cat', '; echo done')):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.guard.feed(command('excerpt-' + str(i), raw + suffix,
                    {'text': 'source\n' * 61, 'lines_emitted': 61})), 'inspection_output_limit_exceeded')
        raw += ' | cat'
        self.assertEqual(self.guard.feed(command('excerpt', raw,
            {'text': 'source\n' * 61, 'lines_emitted': 61})), 'inspection_output_limit_exceeded')
        self.assertEqual(self.guard.inspection_outputs['excerpt']['source_lines'], 61)
        self.assertEqual(self.guard.inspection_outputs['excerpt']['lines'], 1)

    def test_missing_invalid_utf8_and_mismatched_excerpt_are_unverifiable(self):
        raw = 'python3 -I -S .runtime/offdesk_native_worker.py excerpt --root . --path source.py'
        events = [self.read_output('cat source.py', '\ud800', 'unicode'),
                  command('excerpt', raw, {'text': 'one\ntwo\n', 'lines_emitted': 1}),
                  command('empty-excerpt', raw)]
        missing = command('missing', 'cat source.py')
        del missing['item']['aggregated_output']
        events.append(missing)
        for event in events:
            with self.subTest(identifier=event['item']['id']):
                self.assertEqual(self.guard.feed(event), 'inspection_output_unverifiable')

    def test_unrecognized_commands_do_not_claim_output_coverage(self):
        self.assertIsNone(self.guard.feed(self.read_output('python3 inspect.py', 'x\n' * 61)))
        self.assertEqual(self.guard.inspection_outputs, {})
        for raw in ("cat 'unterminated", 'bash -lc "cat \'unterminated"'):
            self.assertFalse(driver.inspection_command(raw))

    def test_output_limit_does_not_replace_shared_command_stop(self):
        guard = driver.ImplementationGuard(1, lambda: {}, lambda: {}, lambda *args: None)
        reason = guard.feed(self.read_output('cat source.py', 'x\n' * 61))
        self.assertEqual(reason, 'tool_call_budget_reached')
        self.assertEqual(guard.calls, 1)
        self.assertEqual(guard.inspection_outputs['read']['stop_reason'], 'inspection_output_limit_exceeded')

    def test_supervisor_reaps_on_visible_inspection_output_overflow(self):
        with tempfile.TemporaryDirectory(prefix='forager-inspection-output-',
                dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp')) as tmp:
            root = Path(tmp)
            event = self.read_output("sed -n '220,310p' source.py", 'source\n' * 91)
            code = ('import sys,json,time;sys.stdin.read();print(json.dumps('
                    + repr(event) + '),flush=True);time.sleep(10)')
            result = worker.supervise([sys.executable, '-I', '-S', '-c', code], 'offline fixture',
                root, root / 'run', time.time() + 5, self.guard, dict(os.environ), idle_seconds=1)
            self.assertEqual(result['stop_reason'], 'inspection_output_limit_exceeded')
            self.assertEqual(result['completed_tool_calls'], 1)
            self.assertEqual(result['in_flight_tool_calls'], 0)
            self.assertFalse(result['turn_completed'])
            pid = json.loads((root / 'run/status.json').read_text())['pid']
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)

    def test_exact_root_prefixed_reads_still_exhaust_the_four_read_guard(self):
        self.guard.check_root = Path('/var/tmp/selected root')
        for index in range(5):
            raw = shlex.join(['/usr/bin/bash', '-lc', 'cd '
                + shlex.quote(str(self.guard.check_root)) + ' && cat source.py'])
            result = self.guard.feed(command(str(index), raw, started=True))
            self.assertEqual(result, 'inspection_budget_reached_without_edit' if index == 4 else None)
        self.assertEqual(self.guard.calls, 5)

    def test_actual_source_change_ends_pre_edit_inspection_budget(self):
        self.guard.feed(command('read', 'cat source.py'))
        self.source['selected.py'] = 'edited'
        for i in range(8): self.assertIsNone(self.guard.feed(command(str(i), 'cat source' + str(i) + '.py')))
        self.assertTrue(self.guard.edit_observed)
        self.assertEqual(len(self.guard.inspection_items), 1)

    def test_compaction_preserves_latest_progress_without_new_context(self):
        self.progress['sha256'] = 'updated-intermediate'
        event = {'type': 'item.completed', 'item': {'type': 'error', 'id': 'notice', 'message': 'compaction warning'}}
        self.assertEqual(self.guard.feed(event), 'context_warning_budget_reached')
        observation = self.records['COMPACTION_PROGRESS_OBSERVATION.json']
        self.assertEqual(observation['observation']['sha256'], 'updated-intermediate')
        self.assertFalse(observation['automatic_resume_authorized'])
        self.progress['sha256'] = 'different'
        self.guard.feed(event)
        self.assertEqual(self.records['COMPACTION_PROGRESS_OBSERVATION.json'], observation)
        self.assertEqual(self.guard.warnings, 1)

    def test_exact_advisory_is_a_policy_stop_without_exhaustion_or_recovery_claim(self):
        event = {'type': 'item.completed', 'item': {'type': 'error', 'id': 'notice',
                                                   'message': driver.COMPACTION_ADVISORY}}
        original = copy.deepcopy(event)
        self.assertEqual(self.guard.feed(event), 'context_warning_budget_reached')
        notice = self.guard.notice_observations[0]
        self.assertEqual(notice['kind'], 'compaction_advisory')
        self.assertEqual(notice['context_warning_limit'], 1)
        self.assertFalse(notice['hard_context_exhaustion_confirmed'])
        self.assertFalse(notice['confirmed_context_transition'])
        self.assertEqual(event, original)
        self.assertEqual(self.guard.calls, 0)

    def test_first_literal_selected_failure_stops_once_and_retains_late_observations(self):
        capture = mock.Mock(return_value={'capture_status': 'fixture'})
        checks = [['/usr/bin/python3', '-I', '-S', '.runtime/check.py']]
        guard = driver.ImplementationGuard(32, lambda: {}, lambda: {}, lambda *args: None,
                                           selected_checks=checks, failure_capture=capture)
        event = command('selected', 'python3 -I -S .runtime/check.py', code=1)
        self.assertEqual(guard.feed(event), driver.SELECTED_CHECK_FAILED)
        self.assertEqual(guard.feed(event), driver.SELECTED_CHECK_FAILED)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(guard.calls, 1)
        self.assertEqual(guard.feed(command('late', "python3 -c 'pass'")), driver.SELECTED_CHECK_FAILED)
        self.assertEqual(guard.calls, 2)
        self.assertEqual(len(guard.completed_commands), 2)

    def test_selected_success_and_checker_text_inside_progress_do_not_trigger_stop(self):
        capture = mock.Mock()
        guard = driver.ImplementationGuard(32, lambda: {}, lambda: {}, lambda *args: None,
            selected_checks=[['/usr/bin/python3', '-I', '-S', '.runtime/check.py']], failure_capture=capture,
            start_capture=lambda *args: {'capture_status': 'captured'})
        self.assertIsNone(guard.feed(command('passed', 'python3 -I -S .runtime/check.py', started=True)))
        self.assertIsNone(guard.feed(command('passed', 'python3 -I -S .runtime/check.py')))
        raw = shlex.join(['python3', '-I', '-S', '.runtime/offdesk_native_worker.py', 'progress',
                          '--checked-command', 'python3 -I -S .runtime/check.py'])
        self.assertIsNone(guard.feed(command('save', raw, code=1)))
        capture.assert_not_called()

    def test_failed_capture_still_stops_without_retry(self):
        capture = mock.Mock(side_effect=OSError('fixture failure'))
        guard = driver.ImplementationGuard(32, lambda: {}, lambda: {}, lambda *args: None,
            selected_checks=[['/usr/bin/python3', '.runtime/check.py']], failure_capture=capture)
        self.assertEqual(guard.feed(command('selected', 'python3 .runtime/check.py', code=1)),
                         driver.SELECTED_CHECK_FAILED)
        self.assertEqual(guard.first_failed_check['capture_status'], 'failed')
        self.assertIsNone(guard.first_failed_check['check_evidence']['reported_counts'])
        self.assertEqual(capture.call_count, 1)

    def test_selected_failure_reaps_worker_before_any_later_write(self):
        with tempfile.TemporaryDirectory(prefix='forager-selected-failure-',
                dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp')) as tmp:
            root = Path(tmp)
            capture = mock.Mock(return_value={'capture_status': 'control-only fixture'})
            guard = driver.ImplementationGuard(32, lambda: {}, lambda: {}, lambda *args: None,
                selected_checks=[['/usr/bin/python3', '-I', '-S', '.runtime/check.py']],
                failure_capture=capture)
            event = command('selected', 'python3 -I -S .runtime/check.py', code=1)
            code = ('import sys,json,time;from pathlib import Path;sys.stdin.read();'
                    'print(json.dumps(' + repr(event) + '),flush=True);'
                    "time.sleep(10);Path('late-write').write_text('unexpected')")
            result = worker.supervise([sys.executable, '-I', '-S', '-c', code],
                'Offline control fixture, no checker or model', root, root / 'run',
                time.time() + 5, guard, dict(os.environ), idle_seconds=1)
            self.assertEqual(result['stop_reason'], driver.SELECTED_CHECK_FAILED)
            self.assertFalse(result['turn_completed'])
            self.assertNotEqual(result['exit_code'], 0)
            self.assertFalse((root / 'late-write').exists())
            self.assertEqual(result['completed_tool_calls'], 1)
            self.assertEqual(capture.call_count, 1)

    def test_invalid_progress_is_preserved_without_claiming_recovery(self):
        self.progress = {'status': 'invalid_progress', 'claims_verified': False}
        reason = self.guard.feed({'type': 'item.completed', 'item': {'type': 'error', 'id': 'notice', 'message': 'compaction'}})
        self.assertEqual(reason, 'context_warning_budget_reached')
        self.assertEqual(self.records['COMPACTION_PROGRESS_OBSERVATION.json']['observation']['status'], 'invalid_progress')

    def test_shared_command_limit_remains_in_force(self):
        guard = driver.ImplementationGuard(2, lambda: {}, lambda: {}, lambda *args: None)
        self.assertIsNone(guard.feed(command('1', 'cat source.py', started=True)))
        self.assertEqual(guard.feed(command('2', 'cat source.py', started=True)), 'tool_call_budget_reached')

    def test_prompt_names_budget_intermediates_and_exact_progress_path(self):
        prompt = driver.implementation_instructions('.night/task-context.json', '.night/task-progress.json', 'context-sha')
        for text in ('four bounded read commands', 'two intermediate', '.night/task-progress.json', 'context-sha', 'does not grant another context'):
            self.assertIn(text, prompt)

    def test_prompt_gives_the_selected_bounded_read_and_check_first_recovery(self):
        prompt = driver.implementation_instructions('context', 'progress', 'sha', ['scripts/a b.py'])
        self.assertIn("inspect --root . --path 'scripts/a b.py' --start 1 --lines 40", prompt)
        for text in ('60 lines', '8192 UTF-8 bytes', 'status=refused', 'not tool progress',
                     'Do not reread the inline checker', 'run the selected check'):
            self.assertIn(text, prompt)

    def test_exact_r195_checker_dump_after_edit_still_stops(self):
        self.source['selected.py'] = 'edited'
        raw = "/usr/bin/bash -lc 'sed -n 1,122p .runtime/check_stream_helper.py'"
        self.assertEqual(self.guard.feed(self.read_output(raw, 'source\n' * 122)),
                         'inspection_output_limit_exceeded')
        self.assertTrue(self.guard.edit_observed)
        self.assertEqual(self.guard.calls, 1)
        self.assertEqual(self.guard.nonprogress_items, set())

    def test_bounded_refusal_is_charged_once_and_allows_a_smaller_read(self):
        self.source['selected.py'] = 'edited'
        raw = ('python3 -I -S .runtime/offdesk_native_worker.py inspect --root . '
               '--path selected.py --lines 122')
        refused = worker.inspection_refusal('inspection_range_limit')
        for event in (command('range', raw, started=True), command('range', raw, refused),
                      command('range', raw, refused)):
            self.assertIsNone(self.guard.feed(event))
        self.assertEqual(self.guard.calls, 1)
        self.assertEqual(self.guard.nonprogress_items, {'range'})
        receipt = {'schema': worker.INSPECTION_SCHEMA, 'status': 'ok',
                   'text': '1: source\n', 'lines_emitted': 1}
        self.assertIsNone(self.guard.feed(command('narrow', raw.replace('122', '40'), receipt)))
        self.assertEqual(self.guard.calls, 2)
        self.assertEqual(len(self.guard.seen_items - self.guard.nonprogress_items), 1)
        self.assertEqual(self.records['IMPLEMENTATION_GUARD_STATE.json']['policy_refusals'], 1)

    def test_forged_or_compound_refusal_is_not_recoverable(self):
        self.source['selected.py'] = 'edited'
        raw = ('python3 -I -S .runtime/offdesk_native_worker.py inspect --root . '
               '--path selected.py --lines 122')
        refused = worker.inspection_refusal('inspection_range_limit')
        for i, (cmd, receipt) in enumerate(((raw, {**refused, 'text': 'hidden source'}),
                (raw, {**refused, 'reason': 'invented'}), (raw + ' | cat', refused))):
            with self.subTest(command=cmd, reason=receipt['reason']):
                self.assertEqual(self.guard.feed(command(str(i), cmd, receipt)),
                                 'inspection_output_unverifiable')
        self.assertEqual(self.guard.nonprogress_items, set())

    def test_refusal_never_overrides_the_shared_call_or_pre_edit_scope_budget(self):
        raw = ('python3 -I -S .runtime/offdesk_native_worker.py inspect --root . '
               '--path selected.py --lines 122')
        refused = worker.inspection_refusal('inspection_range_limit')
        guard = driver.ImplementationGuard(1, lambda: {}, lambda: {}, lambda *a: None)
        self.assertEqual(guard.feed(command('last', raw, refused)), 'tool_call_budget_reached')
        self.assertEqual(guard.calls, 1)
        for i in range(5):
            reason = self.guard.feed(command(str(i), raw + f' --start {i + 1}', refused))
            self.assertEqual(reason, 'inspection_budget_reached_without_edit' if i == 4 else None)
        self.assertEqual(self.guard.calls, 5)

    def test_supervisor_reaps_a_worker_at_inspection_limit(self):
        with tempfile.TemporaryDirectory(prefix='forager-independent-guard-',
                dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp')) as tmp:
            root = Path(tmp)
            output = root / 'run'
            guard = driver.ImplementationGuard(32, lambda: {}, lambda: {}, lambda name, value: self.records.__setitem__(name, value))
            events = [command(str(i), 'cat source' + str(i) + '.py', started=True) for i in range(5)]
            code = 'import sys,json,time;sys.stdin.read();events=' + repr(events) + ';\nfor event in events: print(json.dumps(event),flush=True)\ntime.sleep(10)'
            result = worker.supervise([sys.executable, '-I', '-S', '-c', code], 'offline fixture', root, output,
                time.time() + 5, guard, dict(os.environ), idle_seconds=1)
            self.assertEqual(result['stop_reason'], 'inspection_budget_reached_without_edit')
            self.assertNotEqual(result['exit_code'], 0)
            self.assertFalse(result['turn_completed'])
            pid = json.loads((output / 'status.json').read_text())['pid']
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)


class ReadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.source, self.records = {'source': 'before'}, {}
        self.paths = ['scripts/source.py', 'scripts/other.py']
        self.guard = driver.ImplementationGuard(32, lambda: dict(self.source), lambda: {},
            lambda n, v: self.records.__setitem__(n, v), read_paths=self.paths)
        self.guard.check_root = Path('/var/tmp/selected root')

    def feed(self, identifier, raw, started=True):
        return self.guard.feed(command(identifier, raw, started=started))

    def test_compound_inline_checker_read_stops_and_keeps_call(self):
        raw = "wc -l scripts/other.py .runtime/check.py && sed -n '1,40p' .runtime/check.py"
        self.assertEqual(self.feed('r63-read', raw), 'inspection_path_not_selected')
        self.assertEqual(self.guard.calls, 1)
        row = self.records['IMPLEMENTATION_GUARD_STATE.json']
        self.assertEqual(row['inspection_starts_without_edit'], 1)
        self.assertFalse(row['admission_fencing'])

    def test_root_wrappers_absolute_paths_and_leading_dot_share_identity(self):
        self.assertIsNone(self.feed('first', "sed -n '041,080p' ./scripts/source.py"))
        raw = shlex.join(['/usr/bin/bash', '-lc', 'cd ' + shlex.quote(str(self.guard.check_root))
            + ' && sed -n 41,80p ' + shlex.quote(str(self.guard.check_root / 'scripts/source.py'))])
        self.assertEqual(self.feed('second', raw), 'duplicate_pre_edit_inspection')
        self.assertEqual(self.guard.calls, 2)

    def test_replayed_item_is_not_another_read_or_call(self):
        raw = 'cat scripts/source.py'
        for event in (command('same', raw, started=True), command('same', raw, started=True),
                      command('same', raw), command('same', raw)):
            self.assertIsNone(self.guard.feed(event))
        self.assertEqual(self.guard.calls, 1)
        self.assertEqual(len(self.guard.inspection_policy), 1)
        self.assertEqual(self.feed('new-item', raw, started=False), 'duplicate_pre_edit_inspection')

    def test_changed_ranges_and_files_are_allowed_but_progress_does_not_reset_duplicates(self):
        self.assertIsNone(self.feed('one', 'sed -n 1,20p scripts/source.py'))
        self.assertIsNone(self.feed('two', 'sed -n 21,40p scripts/source.py'))
        self.assertIsNone(self.feed('other', 'sed -n 1,20p scripts/other.py'))
        self.assertIsNone(self.feed('save', 'python3 -I -S .runtime/offdesk_native_worker.py progress'))
        self.assertEqual(self.feed('again', 'sed -n 1,20p scripts/source.py'), 'duplicate_pre_edit_inspection')

    def test_piped_stdin_filters_and_grep_patterns_are_not_mistaken_for_paths(self):
        self.assertIsNone(self.feed('grep', "grep -n 'SENSITIVE_PATTERN' scripts/source.py | head -60"))
        self.assertIsNone(self.feed('bytes', 'head -c 100 scripts/other.py'))
        self.assertNotIn('SENSITIVE_PATTERN', json.dumps(self.records))
        self.assertEqual(self.feed('repeat', "grep -n 'SENSITIVE_PATTERN' ./scripts/source.py | head -30"),
                         'duplicate_pre_edit_inspection')

    def test_parent_and_external_absolute_reads_stop(self):
        for i, path in enumerate(('../scripts/source.py', 'scripts/../scripts/source.py', '/tmp/source.py')):
            self.assertEqual(self.feed(str(i), 'cat ' + path), 'inspection_path_not_selected')

    def test_excerpt_helper_targets_and_default_selectors_are_checked(self):
        base = 'python3 -I -S .runtime/offdesk_native_worker.py excerpt --root . --path '
        self.assertIsNone(self.feed('first', base + 'scripts/source.py | cat'))
        self.assertEqual(self.feed('same', base + './scripts/source.py --start 1 --lines 60'),
                         'duplicate_pre_edit_inspection')
        self.assertEqual(self.feed('checker', base + '.runtime/check.py'), 'inspection_path_not_selected')
        self.assertEqual(self.feed('bad', base + 'scripts/source.py --start unknown'), 'inspection_scope_unverifiable')

    def test_source_edit_ends_pre_edit_scope_and_duplicate_rules(self):
        self.assertIsNone(self.feed('first', 'cat scripts/source.py'))
        self.source['source'] = 'edited'
        self.assertIsNone(self.feed('checker-after-edit', 'cat .runtime/check.py'))
        self.assertIsNone(self.feed('repeat-after-edit', 'cat scripts/source.py'))
        self.assertEqual(len(self.guard.inspection_policy), 1)

    def test_missing_or_invalid_scope_is_rejected(self):
        for paths in (None, [], ['/tmp/source.py'], ['../source.py'], ['.'], [False],
                      ['bad\0path'], ['scripts/'], ['scripts/*.py']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                driver.selected_read_paths(paths)

    def test_shared_command_stop_has_precedence_over_policy(self):
        guard = driver.ImplementationGuard(1, lambda: {}, lambda: {}, lambda *a: None,
                                           read_paths=self.paths)
        self.assertEqual(guard.feed(command('one', 'cat .runtime/check.py', started=True)),
                         'tool_call_budget_reached')
        self.assertEqual(guard.inspection_policy[0]['stop_reason'], 'inspection_path_not_selected')


class HelperStageAuditTests(unittest.TestCase):
    def setUp(self):
        original = AuditTests()
        original.setUp()
        self.phase = {'implementation_stage': packet_tools.HELPER_STAGE,
                      'write_paths': [packet_tools.HELPER_TARGET],
                      'checks': [packet_tools.helper_check_argv('.runtime/check_stream_helper.py')]}
        self.before = original.before
        self.after = {**self.before, packet_tools.HELPER_TARGET: {'kind': 'file', 'sha256': 'new'}}
        self.pin = original.pin
        self.rows = [original.rows[0], command('check', shlex.join(self.phase['checks'][0]))['item'],
                     original.progress('final')]

    def audit(self):
        return driver.implementation_audit(self.before, self.after, self.phase, self.rows, self.pin, 'final')

    def test_helper_creation_is_explicit_and_cannot_relax_legacy_relay_audit(self):
        self.assertEqual(self.audit()['changed_paths'], [packet_tools.HELPER_TARGET])
        del self.phase['implementation_stage']
        with self.assertRaisesRegex(ValueError, 'selected telemetry'): self.audit()

    def test_preexisting_helper_telemetry_edit_deletion_or_symlink_fails(self):
        original_before, original_after = copy.deepcopy(self.before), copy.deepcopy(self.after)
        for mode in ('preexisting', 'telemetry', 'deleted', 'symlink'):
            self.before, self.after = copy.deepcopy(original_before), copy.deepcopy(original_after)
            if mode == 'preexisting': self.before[packet_tools.HELPER_TARGET] = {'kind': 'file', 'sha256': 'old'}
            if mode == 'telemetry': self.after['scripts/offdesk_request_telemetry.py']['sha256'] = 'changed'
            if mode == 'deleted': del self.after[packet_tools.HELPER_TARGET]
            if mode == 'symlink': self.after[packet_tools.HELPER_TARGET] = {'kind': 'symlink', 'target': 'other'}
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.audit()

    def test_stage_packet_check_and_scope_must_all_agree(self):
        packet = {'schema': packet_tools.HELPER_PACKET_SCHEMA, 'write_paths': [packet_tools.HELPER_TARGET],
                  'checker_path': '.runtime/check_stream_helper.py'}
        self.assertEqual(driver.selected_stage_packet(self.phase, packet), packet_tools.HELPER_STAGE)
        original = copy.deepcopy(self.phase)
        for mode in ('implicit', 'unknown', 'scope', 'extra-check', 'model-host-same'):
            self.phase = copy.deepcopy(original)
            if mode == 'implicit': del self.phase['implementation_stage']
            if mode == 'unknown': self.phase['implementation_stage'] = 'guessed-stage'
            if mode == 'scope': self.phase['write_paths'].append('scripts/offdesk_request_telemetry.py')
            if mode == 'extra-check': self.phase['checks'].append(['true'])
            if mode == 'model-host-same': self.phase['checks'][0][-1] = '.night/helper-host-check.json'
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                driver.selected_stage_packet(self.phase, packet)

    def test_checker_as_nonfinal_argument_still_detects_nested_invocation(self):
        self.rows.insert(1, command('nested', 'echo extra; /usr/bin/python3 -I -S .runtime/check_stream_helper.py')['item'])
        with self.assertRaisesRegex(ValueError, 'nested'): self.audit()

    def test_exact_recovery_final_progress_and_one_check_remain_required(self):
        original = copy.deepcopy(self.rows)
        for mode in ('wrong-recovery', 'missing-check', 'duplicate', 'failed', 'wrong-final'):
            self.rows = copy.deepcopy(original)
            if mode == 'wrong-recovery': self.rows[0]['aggregated_output'] = '{}'
            if mode == 'missing-check': del self.rows[1]
            if mode == 'duplicate': self.rows.insert(1, copy.deepcopy(self.rows[1]))
            if mode == 'failed': self.rows[1]['exit_code'] = 1
            if mode == 'wrong-final': self.rows[-1]['aggregated_output'] = '{"sha256":"wrong","claims_verified":false}'
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.audit()


class HelperStageWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='forager-helper-wiring-',
            dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts/offdesk_request_telemetry.py').write_text('# unchanged relay fixture\n')
        (self.root / '.runtime').mkdir()
        fixtures = Path(__file__).resolve().parent.parent / 'tests/fixtures/stream_helper'
        for name in ('contract.json', 'HELPER_VECTORS.json', 'check_stream_helper.py'):
            shutil.copyfile(fixtures / name, self.root / '.runtime' / name)
        packet = packet_tools.build_helper_packet(self.root, '.runtime/contract.json',
            '.runtime/check_stream_helper.py', '.runtime/HELPER_VECTORS.json')
        self.packet = packet
        (self.root / '.runtime/PACKET.json').write_bytes(packet_tools.packet_bytes(packet))
        self.out = self.root / '.night/qualification'
        self.out.mkdir(parents=True)
        worker.write_json(self.out / 'BASELINE.json', batch.inventory(self.root))
        self.phase = {'id': 'HELPER-FIXTURE', 'implementation_stage': packet_tools.HELPER_STAGE,
                      'write_paths': [packet_tools.HELPER_TARGET], 'read_paths': [packet_tools.HELPER_TARGET],
                      'checks': [packet_tools.helper_check_argv('.runtime/check_stream_helper.py')],
                      'preflight_checks': [], 'implementation_next_action': 'Create only the selected helper',
                      'prompt_path': '.runtime/unused.txt', 'max_tool_calls': 32,
                      'summary_path': '.night/helper-stage-report.md',
                      'delivery_packet': {'path': '.runtime/PACKET.json',
                                          'sha256': worker.sha(self.root / '.runtime/PACKET.json')}}
        self.records, self.prompts, self.observed, self.host_calls = {}, [], [], []
        selection = {'run_id': 'helper-offline', 'task_id': self.phase['id'], 'request_id': 'helper-request',
                     'boundary_canary': str(self.root.parent / 'not-used'),
                     'native_codex': '.runtime/not-a-model', 'model': 'fixture-only', 'base_url': 'http://127.0.0.1:1'}
        self.run = types.SimpleNamespace(root=self.root, output=self.out, selection=selection,
            bindings=lambda: None, record=lambda name, value: self.records.__setitem__(name, copy.deepcopy(value)),
            observe_native_calls=lambda label, calls: self.observed.append((label, calls)))
        self.fake = types.SimpleNamespace(**vars(worker))
        self.fake.verify_outer_boundary = lambda *args: None
        self.fake.preflight = lambda *args: {}
        self.fake.validate_preflight = lambda *args: None
        self.fake.native_argv = lambda *args: []

    def report(self):
        # Canned evidence exercises reader wiring. No decoder or model executes.
        return {'schema': 'forager_stream_helper_checks.v1', 'status': 'passed',
                'source_root': str(self.root), 'target_sha256': worker.sha(self.root / packet_tools.HELPER_TARGET),
                'tests_run': 97, 'passed': 97, 'failed': 0, 'errors': 0, 'qualification_complete': False,
                'cases': [{'id': row[0] + '/' + style, 'passed': True} for row in self.packet['cases']
                          for style in ('whole', 'bytewise', 'three-byte')]
                         + [{'id': 'independent-instances', 'passed': True}]}

    def execute(self, mode='pass'):
        def supervise(argv, prompt, root, output, deadline, guard, env, **options):
            self.native_options.append(dict(options))
            for variable, name in [('HOME', 'home'), ('CODEX_HOME', 'codex-home')]:
                self.assertEqual(env[variable], str(root / '.runtime' / name))
                self.assertTrue(Path(env[variable]).is_dir())
                self.assertFalse(Path(env[variable]).is_symlink())
            self.prompts.append(prompt)
            first = len(self.prompts) == 1
            output.mkdir()
            events = [{'type': 'thread.started', 'thread_id': 'fixture-1' if first else 'fixture-2'}]
            context = self.out / (self.phase['id'] + '-context.json')
            stop_reason = None

            def completed(name, raw, receipt, code=0):
                nonlocal stop_reason
                if name == 'model-check':
                    started = command(name, raw, started=True)
                    stop_reason = stop_reason or guard.feed(started)
                    events.append(started)
                event = command(name, raw, receipt, code=code)
                stop_reason = stop_reason or guard.feed(event)
                events.append(event)

            if first:
                receipt = worker.save_progress(root, context, 'Fixture checkpoint, not a model',
                    self.phase['implementation_next_action'], [], [], worker.sha(context))
                completed('saved-initial', 'python3 -I -S .runtime/offdesk_native_worker.py progress', receipt)
            else:
                raw = prompt.splitlines()[0].removeprefix('First command must be exactly: ')
                pin = self.records['PINNED_RECOVERY.json']
                recovered = worker.recovery_context(root, context, pin['context_sha256'], pin['progress_sha256'])
                completed('recovered', raw, recovered)
                (root / packet_tools.HELPER_TARGET).write_text('# Scope-only fixture. No decoder implementation.\n')
                completed('edited', "python3 -c 'pass'", {},
                          code=1 if mode in ('edit-fails', 'compaction-with-command-failure') else 0)
                report = self.report()
                if mode == 'bad-model-report': report['tests_run'] = 0
                check_fails = mode.startswith('check-fails')
                if check_fails:
                    report.update(status='failed', passed=37, failed=60)
                    for i, row in enumerate(report['cases']): row['passed'] = i < 37
                    if mode == 'check-fails-stale-source': report['target_sha256'] = 'stale'
                if mode != 'check-fails-missing-report' and not mode.startswith('pending-'):
                    worker.write_json(root / '.night/helper-model-check.json', report)
                if mode == 'check-fails-collision':
                    (root / '.runtime/snapshot-canary').write_text('preserve\n')
                    (self.out / (self.phase['id'] + '-failed-check-source')).symlink_to(root / '.runtime/snapshot-canary')
                summary = {key: report[key] for key in ('status', 'tests_run', 'passed', 'failed', 'errors')}
                if mode.startswith('pending-'):
                    started = command('model-check', shlex.join(self.phase['checks'][0]), started=True)
                    stop_reason = guard.feed(started)
                    events.append(started)
                    if mode == 'pending-overlap':
                        (root / packet_tools.HELPER_TARGET).write_text('# Edit before overlap notice\n')
                        overlap = command('overlap', "python3 -c 'pass'", started=True)
                        stop_reason = guard.feed(overlap)
                        events.append(overlap)
                    else:
                        stop_reason = 'active_command_timeout'
                else:
                    completed('model-check', shlex.join(self.phase['checks'][0]), summary,
                              code=1 if check_fails else 0)
                if mode == 'check-start-tampered':
                    start = guard.check_observations[0]['start_snapshot']['sources'][0]
                    (root / start['path']).write_text('# Changed start capture\n')
                if mode == 'check-fails-later-edit':
                    (root / packet_tools.HELPER_TARGET).write_text('# Later unverified source\n')
                if mode == 'check-fails-capture-tampered':
                    (root / guard.first_failed_check['source']['path']).write_text('# Tampered snapshot\n')
                if mode in ('compaction', 'compaction-with-command-failure'):
                    event = {'type': 'item.completed', 'item': {'type': 'error', 'id': 'notice',
                                                               'message': driver.COMPACTION_ADVISORY}}
                    stop_reason = guard.feed(event)
                    events.append(event)
                if stop_reason is None:
                    receipt = worker.save_progress(root, context, 'Fixture change, not a model',
                        'Await review', ['fixture checker receipt'], [packet_tools.HELPER_TARGET], worker.sha(context))
                    completed('saved-final', 'python3 -I -S .runtime/offdesk_native_worker.py progress', receipt)
            log = '{' if mode == 'check-fails-truncated-log' and not first else ''.join(json.dumps(event) + '\n' for event in events)
            (output / 'stdout.log').write_text(log)
            (output / 'stderr.log').write_text('')
            return {'run_id': 'offline-fixture', 'exit_code': -15 if stop_reason else 0,
                    'stop_reason': stop_reason, 'turn_completed': stop_reason is None, 'tool_calls': guard.calls,
                    'started_tool_calls': guard.calls, 'completed_tool_calls': len(guard.seen_items),
                    'in_flight_tool_calls': len(guard.started_items - guard.seen_items)}

        def verify(result, output, root, commands, deadline):
            self.host_calls.append(commands)
            if mode == 'actual-host-rejection':
                return worker.verify_result(result, output, root, commands, deadline)
            report = self.report()
            if mode == 'bad-host-report': report['cases'][-1]['id'] = 'invented'
            worker.write_json(root / '.night/helper-host-check.json', report)
            if mode == 'host-mutates-model': (root / '.night/helper-model-check.json').write_text('{}')
            if mode == 'host-mutates-source': (root / packet_tools.HELPER_TARGET).write_text('# changed by host\n')
            return {**result, 'verification_state': 'passed',
                    'verification_checks': [{'argv': argv, 'exit_code': 0} for argv in commands]}

        self.native_options = []
        self.fake.supervise = supervise
        self.fake.verify_result = verify
        with mock.patch.object(driver, 'verify_selected_model') as metadata:
            self.metadata_check = metadata
            return driver.qualify(self.run, self.phase, time.time() + 60, batch, self.fake)

    def test_pending_timeout_preserves_primary_stop_unknown_counts_and_no_host_check(self):
        result = self.execute('pending-timeout')
        self.assertEqual(result['stop_reason'], 'active_command_timeout')
        self.assertFalse(result['turn_completed'])
        self.assertIsNone(result['qualification_failure'])
        stop = result['native_stop_observation']
        observed = stop['selected_check_observation']
        self.assertEqual(observed['pending_at_native_stop'], ['model-check'])
        self.assertIsNone(observed['reported_counts'])
        self.assertEqual(observed['checks'][0]['start_capture_integrity'], 'verified')
        self.assertFalse(observed['checks'][0]['completion_observed'])
        self.assertFalse((self.root / '.night/helper-model-check.json').exists())
        self.assertFalse(self.host_calls)
        self.assertNotIn('HOST_IMPLEMENTATION_AUDIT', self.records)

    def test_pending_overlap_stops_and_retains_start_source_even_after_an_edit(self):
        result = self.execute('pending-overlap')
        self.assertEqual(result['stop_reason'], 'selected_check_overlap')
        observed = result['native_stop_observation']['selected_check_observation']
        self.assertFalse(observed['checks'][0]['current_source_matches_observed_start'])
        body = json.loads((self.root / observed['artifact_path']).read_text())
        source = body['checks'][0]['start_snapshot']['sources'][0]
        self.assertEqual((self.root / source['path']).read_text(), '# Scope-only fixture. No decoder implementation.\n')
        self.assertIsNone(body['reported_counts'])
        self.assertFalse(self.host_calls)

    def test_changed_start_capture_holds_without_fabricating_a_native_stop(self):
        result = self.execute('check-start-tampered')
        self.assertIsNone(result['stop_reason'])
        self.assertTrue(result['turn_completed'])
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['verification_state'], 'failed')
        stop = result['native_stop_observation']
        self.assertEqual(stop['host_hold_reason'], 'selected_check_observation_incomplete')
        self.assertIsNone(stop['primary_native_stop']['stop_reason'])
        self.assertEqual(stop['selected_check_observation']['checks'][0]['start_capture_integrity'], 'unverified')
        self.assertFalse(self.host_calls)

    def test_two_context_receipts_separate_checks_and_pending_stage_report(self):
        self.fake.native_argv = mock.Mock(return_value=[])
        result = self.execute()
        self.assertEqual(result['verification_state'], 'passed')
        self.assertEqual(self.observed, [('context-1', 1), ('context-2', 4)])
        self.assertEqual(self.host_calls, [[packet_tools.helper_check_argv('.runtime/check_stream_helper.py', host=True)]])
        receipts = self.records['HELPER_CHECK_RECEIPTS.json']
        self.assertNotEqual(receipts['model']['path'], receipts['host']['path'])
        self.assertEqual(receipts['model']['sha256'], worker.sha(self.root / receipts['model']['path']))
        self.assertFalse(receipts['qualification_complete'])
        self.assertTrue(receipts['later_full_relay_integration_required'])
        self.assertEqual(self.records['HOST_IMPLEMENTATION_AUDIT.json']['changed_paths'], [packet_tools.HELPER_TARGET])
        self.assertTrue(self.records['CONTEXT_TRANSITION.json']['recovery_first_command_verified'])
        self.assertIn('Reported memory counters still require independent code review',
                      (self.root / self.phase['summary_path']).read_text())
        self.assertTrue(all('pure helper' in prompt for prompt in self.prompts))
        self.assertEqual(self.records['NATIVE_TOOL_PROFILE.json']['web_search'], 'disabled')
        self.assertFalse(self.records['NATIVE_TOOL_PROFILE.json']['view_image'])
        self.assertEqual(self.records['NATIVE_TOOL_PROFILE.json']['profile'], 'helper_commands.v1')
        self.assertFalse(self.records['NATIVE_TOOL_PROFILE.json']['request_user_input'])
        self.assertTrue(self.records['NATIVE_TOOL_PROFILE.json']['strict_config'])
        self.assertFalse(self.records['NATIVE_TOOL_PROFILE.json']['launch_authorized_by_this_record'])
        self.assertFalse(self.records['NATIVE_TOOL_PROFILE.json']['strict_command_only_qualified'])
        self.assertEqual([call.args[-1] for call in self.fake.native_argv.call_args_list], [True, True])
        self.assertTrue(all('generation_policy' not in options for options in self.native_options))
        self.assertNotIn('NATIVE_REASONING_POLICY.json', self.records)
        self.assertTrue(all(not call.kwargs for call in self.fake.native_argv.call_args_list))

    def test_failed_selected_check_returns_verification_failure_without_host_run_or_retry(self):
        result = self.execute('check-fails')
        self.assertEqual(result['verification_state'], 'failed')
        detail = result['qualification_failure']
        self.assertEqual(detail['category'], 'verification_failed')
        self.assertEqual(detail['selected_checks'][0]['evidence']['reported_counts'],
                         {'status': 'failed', 'tests_run': 97, 'passed': 37, 'failed': 60, 'errors': 0})
        self.assertFalse(detail['selected_checks'][0]['evidence']['independent_host_verified'])
        self.assertEqual(result['verification_checks'], [])
        self.assertEqual(self.host_calls, [])
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(len(detail['selected_checks']), 1)
        self.assertFalse(detail['canonical_acceptance'])
        self.assertFalse(detail['automatic_retry_authorized'])
        self.assertIn(self.phase['id'] + '-native-command-failure.json', self.records)
        self.assertNotIn('HOST_REPORT_RECEIPT.json', self.records)
        self.assertEqual(result['stop_reason'], driver.SELECTED_CHECK_FAILED)
        self.assertFalse(result['turn_completed'])
        captured = detail['first_failed_check_snapshot']
        self.assertEqual(captured['capture_integrity'], 'verified')
        self.assertEqual(captured['check_evidence']['reported_counts']['failed'], 60)
        self.assertTrue(captured['current_source_matches_snapshot'])
        self.assertFalse(captured['model_progress_rewritten'])
        self.assertNotIn('saved-final', [item['id'] for item in driver.completed_commands(self.out / 'context-2')])

    def test_later_source_change_keeps_snapshot_counts_and_current_counts_unknown(self):
        result = self.execute('check-fails-later-edit')
        detail = result['qualification_failure']
        captured = detail['first_failed_check_snapshot']
        self.assertEqual(captured['check_evidence']['reported_counts']['passed'], 37)
        self.assertFalse(captured['current_source_matches_snapshot'])
        self.assertIsNone(detail['selected_checks'][0]['evidence']['reported_counts'])
        self.assertEqual((self.root / captured['source']['path']).read_text(),
                         '# Scope-only fixture. No decoder implementation.\n')
        self.assertEqual(json.loads((self.root / captured['report']['path']).read_text())['failed'], 60)
        self.assertEqual(self.host_calls, [])

    def test_changed_snapshot_is_not_credited_as_verified_stored_evidence(self):
        result = self.execute('check-fails-capture-tampered')
        captured = result['qualification_failure']['first_failed_check_snapshot']
        self.assertEqual(captured['capture_integrity'], 'unverified')
        self.assertIsNone(captured['check_evidence']['reported_counts'])
        self.assertEqual(captured['captured_check_evidence']['reported_counts']['failed'], 60)
        self.assertEqual(self.host_calls, [])

    def test_snapshot_collision_does_not_overwrite_and_still_stops(self):
        result = self.execute('check-fails-collision')
        self.assertEqual(result['stop_reason'], driver.SELECTED_CHECK_FAILED)
        captured = result['qualification_failure']['first_failed_check_snapshot']
        self.assertIsNone(captured['source'])
        self.assertIsNone(captured['check_evidence']['reported_counts'])
        self.assertEqual((self.root / '.runtime/snapshot-canary').read_text(), 'preserve\n')
        self.assertEqual(self.host_calls, [])

    def test_truncated_native_log_retains_observed_failure_without_host_execution(self):
        result = self.execute('check-fails-truncated-log')
        detail = result['qualification_failure']
        self.assertEqual(detail['command_log_read_error'], 'JSONDecodeError')
        self.assertEqual(detail['selected_checks'][0]['evidence']['reported_counts']['failed'], 60)
        self.assertEqual(result['verification_checks'], [])
        self.assertEqual(self.host_calls, [])

    def test_compaction_keeps_primary_policy_stop_and_secondary_command_failure(self):
        result = self.execute('compaction-with-command-failure')
        self.assertEqual(result['stop_reason'], 'context_warning_budget_reached')
        detail = result['qualification_failure']
        self.assertEqual(detail['category'], 'execution_failed')
        self.assertEqual(detail['classification_role'], 'secondary_to_native_stop')
        self.assertEqual(detail['primary_native_stop']['exit_code'], -15)
        self.assertIsNone(detail['first_failed_check_snapshot'])
        self.assertEqual(result['native_stop_observation']['notice_observations'][0]['kind'], 'compaction_advisory')
        self.assertEqual(self.host_calls, [])

    def test_advisory_without_failed_commands_stays_a_native_stop_observation(self):
        result = self.execute('compaction')
        self.assertIsNone(result['qualification_failure'])
        stop = result['native_stop_observation']
        self.assertFalse(stop['hard_context_exhaustion_confirmed'])
        self.assertFalse(stop['confirmed_context_transition'])
        self.assertEqual(stop['primary_native_stop']['stop_reason'], 'context_warning_budget_reached')
        self.assertEqual(self.host_calls, [])

    def test_missing_failed_report_keeps_counts_unknown_and_still_blocks_acceptance(self):
        result = self.execute('check-fails-missing-report')
        evidence = result['qualification_failure']['selected_checks'][0]['evidence']
        self.assertEqual(result['qualification_failure']['category'], 'verification_failed')
        self.assertEqual(evidence['status'], 'unverified')
        self.assertIsNone(evidence['reported_counts'])
        self.assertEqual(evidence['error_class'], 'FileNotFoundError')
        self.assertEqual(self.host_calls, [])

    def test_stale_failed_report_is_not_reported_as_current_test_counts(self):
        result = self.execute('check-fails-stale-source')
        evidence = result['qualification_failure']['selected_checks'][0]['evidence']
        self.assertEqual(evidence['status'], 'unverified')
        self.assertIsNone(evidence['reported_counts'])
        self.assertEqual(self.host_calls, [])

    def test_failed_arbitrary_edit_is_execution_failure_without_selected_check_credit(self):
        result = self.execute('edit-fails')
        self.assertEqual(result['qualification_failure']['category'], 'execution_failed')
        failed = result['qualification_failure']['failed_commands']
        self.assertEqual([row['native_item_id'] for row in failed], ['edited'])
        self.assertFalse(failed[0]['selected_check'])
        self.assertNotIn('command', failed[0])
        self.assertEqual(self.host_calls, [])

    def failed_report_fixture(self):
        (self.root / packet_tools.HELPER_TARGET).write_text('# fixture, no decoder\n')
        report = self.report();report.update(status='failed', passed=37, failed=60)
        for i, row in enumerate(report['cases']): row['passed'] = i < 37
        summary = {key: report[key] for key in ('status', 'tests_run', 'passed', 'failed', 'errors')}
        item = command('check', shlex.join(self.phase['checks'][0]), summary, code=1)['item']
        return report, item

    def test_failed_report_requires_full_case_identity_counts_and_native_summary(self):
        report, item = self.failed_report_fixture()
        path = self.root / '.night/helper-model-check.json'
        variants = [('tests_run', True), ('passed', 38), ('failed', 59), ('errors', 1),
                    ('source_root', str(self.root.parent)), ('target_sha256', 'stale'),
                    ('cases', report['cases'][:-1]), ('qualification_complete', True)]
        for field, value in variants:
            invalid = copy.deepcopy(report);invalid[field] = value;worker.write_json(path, invalid)
            evidence = driver.failed_helper_check_evidence(self.root, self.packet, item, worker)
            with self.subTest(field=field):
                self.assertEqual(evidence['status'], 'unverified')
                self.assertIsNone(evidence['reported_counts'])
        worker.write_json(path, report)
        item['aggregated_output'] = json.dumps({'status': 'failed', 'tests_run': 97,
                                               'passed': 0, 'failed': 97, 'errors': 0})
        evidence = driver.failed_helper_check_evidence(self.root, self.packet, item, worker)
        self.assertIsNone(evidence['reported_counts'])

    def test_progress_text_mentioning_checker_is_not_a_selected_check(self):
        raw = shlex.join(['python3', '-I', '-S', '.runtime/offdesk_native_worker.py', 'progress',
                         '--checked-command', shlex.join(self.phase['checks'][0])])
        detail = driver.native_command_failure(self.phase,
            [command('save', raw, {}, code=1)['item']], self.root, self.packet, worker)
        self.assertEqual(detail['category'], 'execution_failed')
        self.assertEqual(detail['selected_checks'], [])

    def test_duplicate_failed_checks_do_not_bind_the_last_report_as_one_attempt(self):
        report, item = self.failed_report_fixture()
        worker.write_json(self.root / '.night/helper-model-check.json', report)
        other = {**item, 'id': 'second-check'}
        detail = driver.native_command_failure(self.phase, [item, other], self.root, self.packet, worker)
        self.assertEqual(len(detail['failed_commands']), 2)
        for check in detail['selected_checks']:
            self.assertEqual(check['evidence']['reason'], 'multiple_selected_invocations')
            self.assertIsNone(check['evidence']['reported_counts'])

    def test_unknown_native_exit_is_not_zero_filled_as_a_test_failure(self):
        _, item = self.failed_report_fixture();item['exit_code'] = None
        detail = driver.native_command_failure(self.phase, [item], self.root, self.packet, worker)
        self.assertEqual(detail['category'], 'execution_failed')
        self.assertIsNone(detail['failed_commands'][0]['exit_code'])
        self.assertFalse(detail['failed_commands'][0]['exit_status_verified'])
        self.assertIsNone(detail['selected_checks'][0]['evidence']['reported_counts'])

    def test_selected_reasoning_policy_binds_both_native_contexts_without_output_cap(self):
        policy = {'schema':'offdesk_native_reasoning_effort.v1',
                  'model':self.run.selection['model'], 'effort':'low'}
        self.phase['reasoning_policy'] = policy
        self.fake.native_argv = mock.Mock(return_value=[])
        result = self.execute()
        self.assertEqual(result['verification_state'], 'passed')
        self.assertEqual([call.kwargs for call in self.fake.native_argv.call_args_list],
                         [{'reasoning_effort':'low'}, {'reasoning_effort':'low'}])
        self.assertTrue(all('generation_policy' not in options for options in self.native_options))
        record = self.records['NATIVE_REASONING_POLICY.json']
        self.assertEqual(record['policy'], policy)
        self.assertFalse(record['wire_or_backend_effect_qualified'])
        self.assertFalse(record['launch_authorized_by_this_record'])

    def test_invalid_reasoning_policy_refuses_before_metadata_or_native(self):
        base = {'schema':'offdesk_native_reasoning_effort.v1',
                'model':self.run.selection['model'], 'effort':'low'}
        for policy in (None, {}, dict(base, model='unselected'), dict(base, effort='high'),
                       dict(base, effort=True), dict(base, extra='unselected')):
            self.phase['reasoning_policy'] = policy
            with self.assertRaises(ValueError):
                self.execute()
            self.metadata_check.assert_not_called()
            self.assertEqual(self.prompts, [])

    def test_reasoning_policy_is_only_for_an_explicit_helper_selection(self):
        policy = {'schema':'offdesk_native_reasoning_effort.v1', 'model':'fixture', 'effort':'low'}
        with self.assertRaisesRegex(ValueError, 'helper model'):
            driver.selected_reasoning_policy({'reasoning_policy':policy}, 'fixture', False)

    def test_selected_generation_policy_is_bound_to_both_helper_contexts(self):
        policy = {'schema':'offdesk_ollama_responses_generation.v1',
                  'model':self.run.selection['model'],'max_output_tokens':2048}
        self.phase['generation_policy'] = policy
        result = self.execute()
        self.assertEqual(result['verification_state'], 'passed')
        self.assertEqual([options['generation_policy'] for options in self.native_options], [policy,policy])
        record=self.records['NATIVE_GENERATION_POLICY.json']
        self.assertEqual(record['policy'],policy)
        self.assertFalse(record['server_enforcement_qualified'])
        self.assertFalse(record['launch_authorized_by_this_record'])

    def test_invalid_generation_policy_refuses_before_metadata_or_native(self):
        for policy in (None, {}, {'schema':'offdesk_ollama_responses_generation.v1',
                                 'model':'unselected','max_output_tokens':2048}):
            self.phase['generation_policy']=policy
            with self.assertRaises(ValueError): self.execute()
            self.metadata_check.assert_not_called()
            self.assertEqual(self.prompts,[])

    def test_generation_policy_cannot_be_selected_by_legacy_relay_stage(self):
        policy={'schema':'offdesk_ollama_responses_generation.v1','model':'fixture','max_output_tokens':2048}
        with self.assertRaisesRegex(ValueError,'helper stage'):
            driver.selected_generation_policy({'generation_policy':policy},'fixture',False)

    def test_missing_or_zero_count_model_report_prevents_host_check(self):
        with self.assertRaisesRegex(ValueError, 'Complete bound helper'): self.execute('bad-model-report')
        self.assertEqual(self.host_calls, [])
        self.assertNotIn('HOST_REPORT_RECEIPT.json', self.records)

    def test_exit_zero_with_wrong_host_case_inventory_is_not_a_pass(self):
        with self.assertRaisesRegex(ValueError, 'Complete bound helper'): self.execute('bad-host-report')
        self.assertNotIn('HOST_REPORT_RECEIPT.json', self.records)

    def test_report_requires_exact_root_source_counts_and_complete_case_identity(self):
        (self.root / packet_tools.HELPER_TARGET).write_text('# fixture, no decoder\n')
        report = self.report()
        path = self.root / '.night/helper-model-check.json'
        worker.write_json(path, report)
        driver.helper_check_receipt(self.root, '.night/helper-model-check.json', self.packet, worker)
        variants = [('tests_run', True), ('passed', 96), ('failed', 1), ('errors', 1),
                    ('target_sha256', 'stale'), ('source_root', str(self.root.parent)),
                    ('qualification_complete', True), ('status', 'failed'),
                    ('cases', report['cases'][:-1]), ('cases', [None] * 97)]
        for field, value in variants:
            invalid = copy.deepcopy(report)
            invalid[field] = value
            worker.write_json(path, invalid)
            with self.subTest(field=field), self.assertRaises(ValueError):
                driver.helper_check_receipt(self.root, '.night/helper-model-check.json', self.packet, worker)

    def test_changed_checker_between_contexts_is_not_redelivered(self):
        original_writer = worker.save_progress

        def changed_after_first_checkpoint(*args, **kwargs):
            receipt = original_writer(*args, **kwargs)
            (self.root / '.runtime/check_stream_helper.py').write_text('# changed immutable input\n')
            return receipt

        with mock.patch.object(worker, 'save_progress', side_effect=changed_after_first_checkpoint):
            with self.assertRaisesRegex(ValueError, 'differs'): self.execute()
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.host_calls, [])

    def test_host_cannot_overwrite_model_report(self):
        with self.assertRaisesRegex(ValueError, 'Model helper check report changed'):
            self.execute('host-mutates-model')

    def test_host_cannot_supply_or_change_the_implementation(self):
        with self.assertRaisesRegex(ValueError, 'host helper checks changed source'):
            self.execute('host-mutates-source')

    def test_real_independent_checker_rejects_scope_only_fixture(self):
        result = self.execute('actual-host-rejection')
        self.assertEqual(result['verification_state'], 'failed')
        self.assertIsNotNone(result['stop_reason'])
        self.assertNotIn('HOST_REPORT_RECEIPT.json', self.records)
        self.assertTrue((self.root / '.night/helper-model-check.json').exists())
        report = json.loads((self.root / '.night/helper-host-check.json').read_text())
        self.assertEqual(report['status'], 'target_unavailable')
        self.assertEqual(report['tests_run'], 0)

    def test_stale_report_stops_before_any_context_or_metadata_call(self):
        worker.write_json(self.root / '.night/helper-host-check.json', {})
        with mock.patch.object(driver, 'verify_selected_model') as identity:
            with self.assertRaisesRegex(ValueError, 'Existing helper check reports'):
                driver.qualify(self.run, self.phase, time.time() + 60, batch, self.fake)
        identity.assert_not_called()
        self.assertEqual(self.prompts, [])

    def test_cold_native_homes_are_private_and_existing_state_is_preserved(self):
        for name in ('home', 'codex-home'):
            self.assertFalse((self.root / '.runtime' / name).exists())
        env = driver.native_environment(self.root, self.run.selection, self.fake)
        for key in ('HOME', 'CODEX_HOME'):
            path = Path(env[key])
            self.assertTrue(path.is_dir())
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        sentinel = Path(env['CODEX_HOME']) / 'retained-fixture.json'
        sentinel.write_text('Preserve existing candidate state.\n')
        self.assertEqual(driver.native_environment(self.root, self.run.selection, self.fake)['CODEX_HOME'], env['CODEX_HOME'])
        self.assertEqual(sentinel.read_text(), 'Preserve existing candidate state.\n')

    def test_unsafe_home_path_stops_before_metadata_or_native_entry(self):
        for name in ('home', 'codex-home'):
            for kind in ('symlink', 'file'):
                path = self.root / '.runtime' / name
                if path.exists():
                    path.rmdir()
                if kind == 'symlink': path.symlink_to(self.root.parent, target_is_directory=True)
                else: path.write_text('Preserve this invalid candidate input.\n')
                with self.subTest(name=name, kind=kind), mock.patch.object(driver, 'verify_selected_model') as identity:
                    with self.assertRaisesRegex(ValueError, 'Native home must be a regular directory'):
                        driver.qualify(self.run, self.phase, time.time() + 60, batch, self.fake)
                    identity.assert_not_called()
                    self.assertEqual(self.prompts, [])
                path.unlink()

    def test_native_runtime_parent_cannot_be_a_symlink_or_file(self):
        root = self.root / 'invalid-runtime-fixture'
        root.mkdir()
        runtime = root / '.runtime'
        for kind in ('symlink', 'file'):
            if kind == 'symlink': runtime.symlink_to(self.root / '.runtime', target_is_directory=True)
            else: runtime.write_text('Not a directory.\n')
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'regular selected runtime'):
                driver.native_environment(root, self.run.selection, self.fake)
            runtime.unlink()


class DriverWiringTests(unittest.TestCase):
    def test_new_packet_module_must_be_hash_bound_with_the_driver(self):
        root = Path(driver.__file__).resolve().parent.parent
        paths = [Path(driver.__file__).resolve(), Path(packet_tools.__file__).resolve()]
        bindings = {str(p.relative_to(root)): worker.sha(p) for p in paths}
        driver.verify_driver_files(root, {'native_runtime_hashes': bindings})
        del bindings[str(paths[1].relative_to(root))]
        with self.assertRaisesRegex(ValueError, 'packet module hashes'):
            driver.verify_driver_files(root, {'native_runtime_hashes': bindings})

    def test_second_context_uses_guard_and_budget_prompt_without_inference(self):
        with tempfile.TemporaryDirectory(prefix='forager-independent-wiring-',
                dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp')) as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / 'scripts/offdesk_request_telemetry.py').write_text('def frame():\n    pass\n')
            (root / '.runtime').mkdir()
            contract = {'operator_implementation_patch_supplied': False,
                        'write_paths': ['scripts/offdesk_request_telemetry.py'],
                        'requirements': ['Independent fixture implementation; no inferred acceptance.']}
            (root / '.runtime/TASK_CONTRACT.json').write_text(json.dumps(contract))
            checker = 'class Cases:\n' + ''.join(
                f"    def test_{i:02d}(self): self.expected('fixture-{i}', b'', 0, False)\n"
                for i in range(12))
            (root / '.runtime/check.py').write_text(checker)
            packet = packet_tools.build_packet(root, 'scripts/offdesk_request_telemetry.py',
                '.runtime/TASK_CONTRACT.json', '.runtime/check.py', [(1, 2)])
            (root / '.runtime/PACKET.json').write_bytes(packet_tools.packet_bytes(packet))
            for name in ('checkpoint-prompt.txt', 'implementation-prompt.txt'):
                (root / '.runtime' / name).write_text('Offline prompt fixture.\n')
            out = root / '.night/qualification'
            out.mkdir(parents=True)
            worker.write_json(out / 'BASELINE.json', batch.inventory(root))
            phase = {'id': 'OFFLINE-WIRING', 'write_paths': ['scripts/offdesk_request_telemetry.py'],
                     'read_paths': ['scripts/offdesk_request_telemetry.py'],
                     'checks': [['/usr/bin/python3', '-I', '-S', '.runtime/check.py']],
                     'preflight_checks': [], 'implementation_next_action': 'Implement selected fixture',
                     'prompt_path': '.runtime/implementation-prompt.txt', 'max_tool_calls': 32,
                     'delivery_packet': {'path': '.runtime/PACKET.json',
                                         'sha256': worker.sha(root / '.runtime/PACKET.json')}}
            selection = {'run_id': 'offline', 'task_id': phase['id'], 'request_id': 'offline-request',
                         'boundary_canary': str(root.parent / 'offline-canary'),
                         'native_codex': '.runtime/no-native-runtime', 'model': 'offline',
                         'base_url': 'http://127.0.0.1:1'}
            records, prompts, guards, observations = {}, [], [], []
            run = types.SimpleNamespace(root=root, output=out, selection=selection,
                bindings=lambda: None, record=lambda name, value: records.__setitem__(name, value),
                observe_native_calls=lambda label, calls: observations.append((label, calls)))
            fake = types.SimpleNamespace(**vars(worker))
            fake.verify_outer_boundary = lambda *args: None
            fake.preflight = lambda *args: {}
            fake.validate_preflight = lambda *args: None
            fake.native_argv = mock.Mock(return_value=[])

            def supervise(argv, prompt, selected_root, output, deadline, guard, env, **options):
                prompts.append(prompt)
                guards.append(guard)
                first = len(prompts) == 1
                if first:
                    context = out / (phase['id'] + '-context.json')
                    receipt = worker.save_progress(root, context, 'Offline fixture assessment',
                        phase['implementation_next_action'], [], [], worker.sha(context))
                    helper = shlex.join(['python3', '-I', '-S', '.runtime/offdesk_native_worker.py',
                        'progress', '--root', '.', '--context', str(context.relative_to(root)),
                        '--expected-context-sha256', worker.sha(context)])
                    event = command('save', shlex.join(['/usr/bin/bash', '-lc',
                        'cd ' + shlex.quote(str(root)) + ' && ' + helper]), receipt)
                    guard.feed(event)
                    output.mkdir()
                    (output / 'stdout.log').write_text(json.dumps(event) + '\n')
                return {'stop_reason': None if first else 'offline_capture_only', 'turn_completed': first,
                        'tool_calls': 1 if first else 0, 'started_tool_calls': 1 if first else 0,
                        'completed_tool_calls': 1 if first else 0, 'in_flight_tool_calls': 0}

            fake.supervise = supervise
            with mock.patch.object(driver, 'verify_selected_model') as identity:
                result = driver.qualify(run, phase, time.time() + 60, batch, fake)
            self.assertEqual(identity.call_count, 2)
            self.assertEqual(result['stop_reason'], 'offline_capture_only')
            self.assertEqual([call.args[-1] for call in fake.native_argv.call_args_list], [False, False])
            self.assertEqual(observations, [('context-1', 1), ('context-2', 0)])
            self.assertIsInstance(guards[1], driver.ImplementationGuard)
            self.assertEqual(guards[1].max_calls, 31)
            self.assertEqual(guards[1].read_paths, {'scripts/offdesk_request_telemetry.py'})
            self.assertEqual(len(prompts), 2)
            first_line = prompts[1].splitlines()[0]
            self.assertTrue(first_line.startswith('First command must be exactly: '))
            expected_recovery = ['python3', '-I', '-S', '.runtime/offdesk_native_worker.py',
                'recovery-context', '--root', '.', '--context',
                '.night/qualification/OFFLINE-WIRING-context.json',
                '--expected-context-sha256', records['PINNED_RECOVERY.json']['context_sha256'],
                '--expected-progress-sha256', records['PINNED_RECOVERY.json']['progress_sha256']]
            self.assertEqual(shlex.split(first_line.removeprefix('First command must be exactly: ')),
                             expected_recovery)
            self.assertEqual(prompts[1].count('First command must be exactly:'), 1)
            self.assertLess(prompts[1].index(first_line), prompts[1].index('VERIFIED TASK INPUT'))
            self.assertIn('four bounded read commands', prompts[1])
            self.assertLess(prompts[1].index('Edit first after recovery'), prompts[1].index('VERIFIED TASK INPUT'))
            self.assertIn('60 lines and 8192 UTF-8 bytes', prompts[1])
            self.assertIn('.night/qualification/OFFLINE-WIRING-progress.json', prompts[1])
            self.assertIn('PINNED_RECOVERY.json', records)
            for prompt in prompts:
                self.assertIn('--assessment', prompt)
                self.assertIn('--checked-command', prompt)
                self.assertNotIn('--request-stdin', prompt)
                self.assertNotIn('--checks-json', prompt)
                self.assertNotIn('<<', prompt)
                self.assertIn('VERIFIED TASK INPUT', prompt)
                self.assertIn('Independent fixture implementation', prompt)
                self.assertIn('def frame():', prompt)
                self.assertIn('Case test_11:', prompt)
            self.assertIn('do not cat the index/contract/checker', prompts[0])
            self.assertIn('First command must be exactly:', prompts[1])
            self.assertFalse(records['INLINE_DELIVERY_RECEIPT.json']['model_visibility_confirmed'])
            self.assertEqual(batch.inventory(root)['scripts/offdesk_request_telemetry.py']['sha256'],
                             worker.sha(root / 'scripts/offdesk_request_telemetry.py'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
