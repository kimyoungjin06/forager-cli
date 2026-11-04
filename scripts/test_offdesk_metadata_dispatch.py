"""Private controller qualification. All rows and runtime failures are synthetic."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests/support'))
from metadata_dispatch_fixture import build
import offdesk_metadata_dispatch as controller
import offdesk_resume_checkpoint as gate


class DispatchTests(unittest.TestCase):
    def setUp(self):
        module_scope = patch.dict(sys.modules)
        module_scope.start()
        self.addCleanup(module_scope.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cfg, self.pin = build(self.root)
        self.runtime = {'schema': 'offdesk_runtime_binding.v1', 'project_key': 'project',
                        'task_id': 'task', 'request_id': 'request', 'ticket_id': 'ticket-unique'}
        self.env = patch.dict(os.environ, {'FORAGER_OFFDESK_RUNTIME_BINDING': json.dumps(self.runtime)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def save(self):
        for name in self.cfg['input_files']:
            self.cfg['input_files'][name] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        raw = gate.encode(self.cfg)
        (self.root / 'METADATA_SELECTION.json').write_bytes(raw)
        self.pin = hashlib.sha256(raw).hexdigest()

    def run_controller(self):
        return controller.run(self.root, self.pin)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_complete_chain_has_exact_ticket_counts_receipts_and_no_replay(self):
        value = self.run_controller()
        self.assertEqual(value['child_calls'], 4)
        result = json.loads((self.root / 'result.json').read_text())
        self.assertEqual(result['ticket_id'], self.runtime['ticket_id'])
        self.assertEqual(result['counts'], {'compute': 0, 'reuse': 0, 'exclude': 4})
        self.assertEqual([r['processed'] for r in result['checkpoints']], [0, 2, 4, 4])
        self.assertFalse(result['plan_update_authorized'])
        self.assertFalse((self.root / 'entry-4.json').exists())
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_controller()
        self.assertEqual(before, self.snapshot())

    def test_readonly_inspection_leaves_no_artifacts(self):
        before = self.snapshot()
        controller.inspect(self.root, self.pin)
        self.assertEqual(before, self.snapshot())

    def test_common_dotted_task_identity_runs_and_invalid_grammar_refuses_before_attempt(self):
        for value in ['', '.', '..', 'space id', '한글', 'x'*101, 'slash/id']:
            self.assertFalse(controller.valid_identity(value))
        self.assertTrue(controller.valid_identity('C6.3'))
        self.cfg['task_id']='C6.3';self.runtime['task_id']='C6.3';self.save()
        with patch.dict(os.environ,{'FORAGER_OFFDESK_RUNTIME_BINDING':json.dumps(self.runtime)}):
            self.assertEqual(self.run_controller()['status'],'pending_user_review')

    def test_initial_inspection_time_is_charged_before_any_attempt_or_child(self):
        original=controller.inspect;real_clock=time.clock_gettime
        elapsed=[0]
        def inspect(root,pin):
            value=original(root,pin);elapsed[0]=self.cfg['total_seconds']+1
            return value
        with patch.object(controller,'inspect',side_effect=inspect),patch.object(controller.time,'clock_gettime',side_effect=lambda clock:real_clock(clock)+elapsed[0]):
            with self.assertRaisesRegex(ValueError,'original controller window exhausted'):
                self.run_controller()
        self.assertFalse((self.root/'ATTEMPT.json').exists())
        self.assertFalse((self.root/'child-0.intent.json').exists())

    def test_parent_observes_complete_child_exit_and_external_publication_pins(self):
        self.run_controller()
        attempt=json.loads((self.root/'ATTEMPT.json').read_text())
        timing=json.loads((self.root/'CONTROLLER_TIMING.json').read_text())
        self.assertEqual(timing['started_boottime'],attempt['started_boottime'])
        self.assertEqual(len(timing['child_observations']),4)
        for index,row in enumerate(timing['child_observations']):
            self.assertGreaterEqual(row['ended_boottime'],row['started_boottime'])
            self.assertLessEqual(row['elapsed_seconds'],row['timeout_seconds'])
            self.assertEqual(row['process_exit_code'],0)
            result=json.loads((self.root/('child-'+str(index)+'.stdout')).read_text())
            self.assertEqual(row['publication_sha256'],result['publication_sha256'])
            observation=json.loads((self.root/result['publication']).read_text())
            self.assertLessEqual(observation['attempts'][-1]['ended_at'],row['ended_boottime'])

    def test_missing_wrong_or_empty_runtime_binding_refuses_before_attempt(self):
        for runtime in [None, {**self.runtime, 'project_key': 'other'},
                        {**self.runtime, 'request_id': 'other'}, {**self.runtime, 'ticket_id': ''}]:
            with self.subTest(runtime=runtime), patch.dict(os.environ, {'FORAGER_OFFDESK_RUNTIME_BINDING': json.dumps(runtime)}):
                with self.assertRaises(ValueError):
                    self.run_controller()
                self.assertFalse((self.root / 'ATTEMPT.json').exists())

    def test_input_drift_or_symlink_refuses_before_entry(self):
        path = self.root / 'input.jsonl'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'W1', b'W9'))
        with self.assertRaises(ValueError):
            self.run_controller()
        path.unlink()
        target = self.root / 'other'
        target.write_bytes(original)
        path.symlink_to(target)
        with self.assertRaises(OSError):
            self.run_controller()
        self.assertFalse((self.root / 'ATTEMPT.json').exists())

    def test_coherently_rehashed_false_policy_cannot_grant_entry(self):
        policy = json.loads((self.root / 'policy.json').read_text())
        policy['member_policy'] = 'allow'
        (self.root / 'policy.json').write_bytes(gate.encode(policy))
        self.save()
        with self.assertRaises(ValueError):
            self.run_controller()
        self.assertFalse((self.root / 'ATTEMPT.json').exists())

    def test_preexisting_checkpoint_or_entry_never_scans_latest(self):
        for name in ['seed.receipt.json', 'entry-0.json', 'batch-0-2.contract.json', 'child-0.intent.json', 'CONTROLLER_TIMING.json']:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    self.run_controller()
                path.unlink()

    def test_expired_window_refuses_before_reservation(self):
        self.cfg['starts_at'] = time.time() - 121
        self.cfg['stops_at'] = self.cfg['starts_at'] + 120
        self.save()
        with self.assertRaisesRegex(ValueError, 'absolute window'):
            self.run_controller()
        self.assertFalse((self.root / 'ATTEMPT.json').exists())

    def test_mutation_after_reservation_preserves_attempt_without_child(self):
        original = controller.emit
        def mutate(gate_module, root, name, value):
            pin = original(gate_module, root, name, value)
            if name == 'ATTEMPT.json':
                (root / 'input.jsonl').write_text('changed after admission')
            return pin
        with patch.object(controller, 'emit', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.run_controller()
        self.assertTrue((self.root / 'ATTEMPT.json').exists())
        self.assertTrue((self.root / 'FAILURE.json').exists())
        self.assertFalse((self.root / 'child-0.intent.json').exists())

    def test_timeout_kills_owned_child_without_retry_or_seed(self):
        config = json.loads((self.root / 'configuration.json').read_text())
        config['limits'].update(work_seconds=1, shutdown_grace_seconds=0)
        (self.root / 'configuration.json').write_bytes(gate.encode(config))
        self.cfg['child_seconds'] = 1
        self.save()
        real_popen = subprocess.Popen
        children = []
        def sleep_child(argv, **kwargs):
            child = real_popen(['/usr/bin/python3', '-I', '-S', '-c', 'import time; time.sleep(30)'], **kwargs)
            children.append(child)
            return child
        with patch.object(controller.subprocess, 'Popen', side_effect=sleep_child):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.run_controller()
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue((self.root / 'FAILURE.json').exists())
        self.assertFalse((self.root / 'seed.checkpoint.json').exists())

    def test_failed_child_retains_intent_and_blocks_reentry(self):
        real_popen = subprocess.Popen
        children = []
        def failed(argv, **kwargs):
            child = real_popen(['/usr/bin/python3', '-I', '-S', '-c', 'raise SystemExit(7)'], **kwargs)
            children.append(child)
            return child
        with patch.object(controller.subprocess, 'Popen', side_effect=failed):
            with self.assertRaisesRegex(ValueError, 'child failed'):
                self.run_controller()
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].returncode, 7)
        observed=json.loads((self.root/'child-0.observation.json').read_text())
        self.assertEqual(observed['process_exit_code'],7)
        self.assertGreaterEqual(observed['elapsed_seconds'],0)
        self.assertTrue((self.root / 'child-0.intent.json').exists())
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.run_controller()
        self.assertEqual(before, self.snapshot())

    def test_interrupted_wait_kills_owned_child_preserving_intent(self):
        real_popen = subprocess.Popen
        children = []
        def interrupted(argv, **kwargs):
            child = real_popen(['/usr/bin/python3', '-I', '-S', '-c', 'import time; time.sleep(30)'], **kwargs)
            original_wait = child.wait
            calls = []
            def wait(timeout):
                if not calls:
                    calls.append(True)
                    raise InterruptedError('synthetic interrupted wait')
                return original_wait(timeout=timeout)
            child.wait = wait
            children.append(child)
            return child
        with patch.object(controller.subprocess, 'Popen', side_effect=interrupted):
            with self.assertRaises(InterruptedError):
                self.run_controller()
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue((self.root / 'FAILURE.json').exists())
        self.assertTrue((self.root / 'child-0.intent.json').exists())

    def test_publication_then_final_drift_keeps_result_and_failure_evidence(self):
        original = controller.emit
        def mutate(gate_module, root, name, value):
            pin = original(gate_module, root, name, value)
            if name == 'result.json':
                (root / 'policy.json').write_text('changed after result publication')
            return pin
        with patch.object(controller, 'emit', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.run_controller()
        self.assertTrue((self.root / 'result.json').exists())
        self.assertTrue((self.root / 'FAILURE.json').exists())


if __name__ == '__main__':
    unittest.main()
