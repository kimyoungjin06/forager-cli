"""Regression coverage for failed native workers and preflight bindings."""
import json
import io
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import offdesk_native_worker as worker


def command_event(index, code=0, output='', command='cargo test'):
    return {'type':'item.completed','item':{'id':str(index),'type':'command_execution',
            'command':command,'exit_code':code,'aggregated_output':output}}


class ProgressTests(unittest.TestCase):
    def test_environment_errors_across_different_commands_stop_at_three(self):
        guard=worker.ProgressGuard()
        for i in range(2):
            self.assertIsNone(guard.feed(command_event(i,101,'Permission denied',f'cargo test case{i}')))
        self.assertEqual(guard.feed(command_event(2,101,'Permission denied','cargo check')),'environment_permission_denied')

    def test_duplicate_event_does_not_inflate_failure_count(self):
        guard=worker.ProgressGuard();event=command_event(1,101,'Permission denied')
        guard.feed(event);guard.feed(event)
        self.assertEqual(guard.calls,1);self.assertEqual(guard.failures['environment_permission_denied'],1)

    def test_xml_failure_stops_before_retry_loop(self):
        self.assertEqual(worker.ProgressGuard().feed({'type':'error','message':'Reconnecting... XML syntax error on line 4'}),'tool_protocol_parse_error')

    def test_generic_incomplete_stream_retries_are_bounded(self):
        guard=worker.ProgressGuard(repeated_errors=2)
        error={'type':'error','message':'stream closed before response.completed'}
        self.assertIsNone(guard.feed(error));self.assertEqual(guard.feed(error),'incomplete_provider_stream')

    def test_unusable_ephemeral_tool_and_unknown_process_are_bounded(self):
        guard=worker.ProgressGuard(repeated_errors=2)
        self.assertIsNone(guard.error('Goal tools require a persistent thread.'))
        self.assertEqual(guard.error('Unknown process id 123456'),'unusable_tool_protocol')

    def test_reads_and_call_count_are_bounded(self):
        guard=worker.ProgressGuard(repeated_command=3)
        guard.feed(command_event(1,command='cat long-file'));guard.feed(command_event(2,command='cat long-file'))
        self.assertEqual(guard.feed(command_event(3,command='cat long-file')),'repeated_command_without_resolution')
        guard=worker.ProgressGuard(max_calls=1)
        self.assertEqual(guard.feed(command_event(1)),'tool_call_budget_reached')
        self.assertEqual(guard.calls,1)

    def test_started_commands_charge_once_and_stop_at_the_limit(self):
        guard=worker.ProgressGuard(max_calls=2)
        first=command_event('one');first['type']='item.started'
        self.assertIsNone(guard.feed(first));self.assertIsNone(guard.feed(first))
        self.assertIsNone(guard.feed(command_event('one')))
        self.assertIsNone(guard.feed(first))
        self.assertEqual(guard.calls,1)
        second=command_event('two');second['type']='item.started'
        self.assertEqual(guard.feed(second),'tool_call_budget_reached')
        self.assertEqual(guard.calls,2)
        self.assertEqual(guard.started_items-guard.seen_items,{'two'})

    def test_concurrent_in_flight_and_completion_only_calls_share_budget(self):
        guard=worker.ProgressGuard(max_calls=3)
        for name in ('a','b'):
            event=command_event(name);event['type']='item.started'
            self.assertIsNone(guard.feed(event))
        self.assertEqual(guard.calls,2)
        self.assertEqual(guard.feed(command_event('legacy')),'tool_call_budget_reached')
        self.assertEqual(guard.calls,3)

    def test_invalid_guard_limits_are_rejected_before_native_work(self):
        for field in ('max_calls','repeated_errors','repeated_command','context_warnings'):
            for value in (0,-1,True,1.5,None):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    worker.ProgressGuard(**{field:value})

    def test_replayed_error_notices_do_not_exhaust_budgets(self):
        for message,field in [('multiple compactions may reduce accuracy','warnings'),
                              ('stream disconnected','failures')]:
            with self.subTest(message=message):
                guard=worker.ProgressGuard(repeated_errors=2,context_warnings=2)
                event={'type':'item.completed','item':{'id':'same','type':'error','message':message}}
                self.assertIsNone(guard.feed(event));self.assertIsNone(guard.feed(event))
                self.assertEqual(guard.warnings if field=='warnings' else guard.failures['incomplete_provider_stream'],1)
                event['item']['id']='different'
                self.assertIsNotNone(guard.feed(event))

    def test_changed_notice_payload_counts_and_invalid_ids_are_rejected(self):
        guard=worker.ProgressGuard(context_warnings=2)
        notice=lambda identity,text:{'type':'item.completed','item':{'id':identity,'type':'error','message':text}}
        self.assertIsNone(guard.feed(notice('one','compaction A')))
        self.assertEqual(guard.feed(notice('one','compaction B')),'context_warning_budget_reached')
        for identity in ([],{},3,False,None,''):
            with self.subTest(identity=identity):
                event=notice(identity,'compaction')
                self.assertFalse(worker.valid_native_event(event))
                self.assertEqual(guard.feed(event),'invalid_native_event')
        self.assertEqual(guard.warnings,2)

    def test_failed_unittest_repeats_across_wrappers_and_zero_pipeline_status(self):
        guard=worker.ProgressGuard();guard.check_root=Path('/selected')
        commands=['/usr/bin/bash -lc "cd scripts && python3 -m unittest test_worker -v 2>&1 | tail -40"',
                  'sleep 20; cd /selected/scripts && python3 -I -S -m unittest test_worker -q 2>&1 | tail -5; echo EXIT=0',
                  'cd scripts && /usr/bin/python3 -m unittest test_worker 2>&1 | tail -8; echo EXIT=1']
        for index,command in enumerate(commands):
            result=guard.feed(command_event(index,output='Ran 61 tests in 12.1s\n\nFAILED (failures=3)\nEXIT=0\n',command=command),source_generation='same-source')
            self.assertEqual(result,None if index<2 else 'repeated_failed_check_without_source_change')
        self.assertEqual(guard.calls,3)

    def test_source_changes_allow_another_failed_check_and_replays_do_not_count(self):
        guard=worker.ProgressGuard(repeated_errors=2)
        event=command_event('one',code=1,output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        self.assertIsNone(guard.feed(event,source_generation='before'))
        self.assertIsNone(guard.feed(event,source_generation='before'))
        event['item']['id']='two'
        self.assertIsNone(guard.feed(event,source_generation='after'))
        event['item']['id']='three'
        self.assertEqual(guard.feed(event,source_generation='after'),'repeated_failed_check_without_source_change')

    def test_different_test_selectors_and_printed_documentation_are_not_merged(self):
        guard=worker.ProgressGuard(repeated_errors=2)
        for index,command in enumerate(['python3 -m unittest suite_a','python3 -m unittest suite_b',
                                        'cat failure.log','echo "python3 -m unittest suite_a"',
                                        'echo python3 -m unittest suite_a']):
            self.assertIsNone(guard.feed(command_event(index,output='FAILED (failures=1)',command=command)))
        self.assertIsNone(worker.failed_test_check(command_event('ok',command='python3 -m unittest suite_a',output='Ran 1 test\nOK')['item']))

    def test_cargo_and_pytest_failure_summaries_are_observed(self):
        for command,output in [('cargo test case -- --nocapture','test result: FAILED. 1 passed; 1 failed; 0 ignored; finished in 2s'),
                               ('pytest tests/test_case.py -q','====== 1 failed, 2 passed in 1.1s ======'),
                               ('/usr/bin/env CHECK=1 python3 -I -m pytest tests/test_case.py -q','====== 1 failed, 2 passed in 1.1s ======')]:
            with self.subTest(command=command):
                guard=worker.ProgressGuard(repeated_errors=2)
                self.assertIsNone(guard.feed(command_event(1,output=output,command=command)))
                self.assertEqual(guard.feed(command_event(2,output=output,command=command)),'repeated_failed_check_without_source_change')

    def test_failure_command_parser_is_bounded_and_never_executes_shell_text(self):
        for command in ('python3 -m unittest '+('x '*9000), 'bash -lc "unterminated'):
            with self.subTest(command_length=len(command)):
                self.assertIsNone(worker.failed_test_check(command_event(1,command=command,output='FAILED (failures=1)')['item']))
        with mock.patch.object(worker.subprocess,'Popen',side_effect=AssertionError('must not execute')):
            self.assertIsNotNone(worker.failed_test_check(command_event(1,command='python3 -m unittest test_worker; echo $(false)',output='FAILED (failures=1)')['item']))

    def test_context_warning_does_not_count_as_technical_progress(self):
        guard=worker.ProgressGuard(context_warnings=2)
        event={'type':'item.completed','item':{'type':'error','message':'multiple compactions may reduce accuracy'}}
        self.assertIsNone(guard.feed(event));self.assertEqual(guard.feed(event),'context_warning_budget_reached')


class RootTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.origin=self.base/'origin';self.origin.mkdir();(self.origin/'Cargo.toml').write_text('[package]\nname="fixture"\nversion="0.0.1"\n')
        self.manifest={'snapshot_root':str(self.origin),'files':{'Cargo.toml':{'kind':'file','sha256':worker.sha(self.origin/'Cargo.toml')}}}
        self.snapshot=self.base/'snapshot.json';self.snapshot.write_text(json.dumps(self.manifest));self.root=self.base/'independent'
        worker.prepare(self.snapshot,self.root)

    def tearDown(self):self.tmp.cleanup()

    def receipt(self):
        return {'schema':'offdesk_native_preflight.v1','root':worker.root_identity(self.root),
                'source_sha256':worker.source_identity(self.root,self.manifest),'controller_sha256':worker.sha(Path(worker.__file__)),
                'completed_at':time.time(),'passed':True}

    def test_ancestor_cargo_configuration_and_workspace_rejected(self):
        for relative in ['.cargo/config.toml','Cargo.toml']:
            with self.subTest(relative=relative):
                parent=self.base/('parent'+str(len(relative)));parent.mkdir();p=parent/relative;p.parent.mkdir(exist_ok=True);p.write_text('')
                with self.assertRaises(ValueError):worker.independent_root(parent/'task')

    def test_source_drift_stale_and_failed_preflight_refused(self):
        receipt=self.receipt();worker.validate_preflight(self.root,receipt,time.time())
        for changes in [{'passed':False},{'completed_at':0},{'controller_sha256':'changed'},{'root':{}}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):worker.validate_preflight(self.root,dict(receipt,**changes),time.time())
        (self.root/'Cargo.toml').write_text('changed')
        with self.assertRaises(ValueError):worker.validate_preflight(self.root,receipt,time.time())

    def test_source_symlink_escape_and_object_substitution_refused(self):
        path=self.root/'Cargo.toml';path.unlink();path.symlink_to(self.origin/'Cargo.toml')
        with self.assertRaises(ValueError):worker.source_identity(self.root,self.manifest)

    def test_added_build_script_source_and_cargo_config_invalidate_preflight(self):
        receipt=self.receipt()
        for name in ['build.rs','src/extra.rs','.cargo/config.toml']:
            with self.subTest(name=name):
                path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('added')
                with self.assertRaises(ValueError):worker.validate_preflight(self.root,receipt,time.time())
                path.unlink()
                worker.validate_preflight(self.root,receipt,time.time())

    def test_runtime_and_build_cache_do_not_change_source_identity(self):
        receipt=self.receipt()
        for name in ['.runtime/log.json','.night/run/log','.codex/session','target/debug/cache','scripts/__pycache__/worker.pyc']:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('cache')
        worker.validate_preflight(self.root,receipt,time.time())

    def test_added_directory_symlink_and_deleted_source_are_refused(self):
        receipt=self.receipt();p=self.root/'new-directory';p.symlink_to(self.origin,target_is_directory=True)
        with self.assertRaises(ValueError):worker.validate_preflight(self.root,receipt,time.time())
        p.unlink();(self.root/'Cargo.toml').unlink()
        with self.assertRaises(ValueError):worker.validate_preflight(self.root,receipt,time.time())

    def test_version_only_or_zero_test_preflight_is_refused(self):
        with self.assertRaises(ValueError):worker.preflight(self.root,self.root/'receipt.json',[['cargo','--version']],5)
        result={'argv':['cargo','test'],'exit_code':0,'stdout':'test result: ok. 0 passed; 0 failed','stderr':''}
        with mock.patch.object(worker,'bounded_command',return_value=result):
            receipt=worker.preflight(self.root,self.root/'.runtime/receipt.json',[['cargo','test']],5)
            self.assertFalse(receipt['passed'])

    def test_real_test_count_and_failure_do_not_unlock_model(self):
        result={'argv':['cargo','test'],'exit_code':0,'stdout':'test result: ok. 3 passed; 0 failed','stderr':''}
        with mock.patch.object(worker,'bounded_command',return_value=result):
            receipt=worker.preflight(self.root,self.root/'.runtime/receipt.json',[['cargo','test']],5)
            self.assertTrue(receipt['passed']);self.assertEqual(receipt['tests_passed'],3)
        result['exit_code']=101
        with mock.patch.object(worker,'bounded_command',return_value=result):
            self.assertFalse(worker.preflight(self.root,self.root/'.runtime/receipt.json',[['cargo','test']],5)['passed'])

    def test_preflight_accepts_actual_isolated_unittest_and_records_real_count(self):
        pilot = self.root / 'test_pilot.py'
        pilot.write_text('import unittest\nclass Pilot(unittest.TestCase):\n    def test_real_check(self):\n        self.assertEqual(2 + 3, 5)\n')
        manifest = json.loads((self.root / '.runtime/source_manifest.json').read_text())
        manifest['files']['test_pilot.py'] = {'kind': 'file', 'sha256': worker.sha(pilot)}
        worker.write_json(self.root / '.runtime/source_manifest.json', manifest)
        command = [sys.executable, '-I', '-S', '-m', 'unittest', 'discover', '-p', 'test_pilot.py', '-v']
        receipt = worker.preflight(self.root, self.root / '.runtime/python-preflight.json', [command], 10)
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['tests_passed'], 1)
        self.assertFalse(receipt['launch_authorized'])
        worker.validate_preflight(self.root, receipt, time.time())

    def test_python_version_script_printouts_and_non_test_summaries_do_not_unlock(self):
        for command in [[sys.executable, '--version'], [sys.executable, '-c', 'print("OK")', '-m', 'unittest']]:
            with self.subTest(command=command), mock.patch.object(worker, 'bounded_command') as execute:
                with self.assertRaises(ValueError):
                    worker.preflight(self.root, self.root / '.runtime/preflight.json', [command], 10)
                execute.assert_not_called()
        with mock.patch.object(worker, 'bounded_command', side_effect=[
            {'exit_code': 0, 'stdout': 'test result: ok. 20 passed; 0 failed', 'stderr': ''},
            {'exit_code': 0, 'stdout': 'test result: ok. 0 passed; 0 failed', 'stderr': ''}]):
            receipt = worker.preflight(self.root, self.root / '.runtime/preflight.json', [['cargo', 'fmt'], ['cargo', 'test']], 10)
            self.assertFalse(receipt['passed'])

    def test_unittest_failures_zero_or_only_skipped_checks_do_not_unlock(self):
        for exit_code, stderr in [(1, 'Ran 2 tests in 0.001s\n\nFAILED (failures=1)\n'),
                                  (0, 'Ran 0 tests in 0.001s\n\nOK\n'),
                                  (0, 'Ran 2 tests in 0.001s\n\nOK (skipped=2)\n')]:
            result = {'argv': [sys.executable, '-m', 'unittest'], 'exit_code': exit_code, 'stdout': '', 'stderr': stderr}
            with self.subTest(stderr=stderr), mock.patch.object(worker, 'bounded_command', return_value=result):
                self.assertFalse(worker.preflight(self.root, self.root / '.runtime/preflight.json', [result['argv']], 10)['passed'])
        self.assertEqual(worker.passed_test_count('unittest', {'exit_code': 0, 'stdout': '',
            'stderr': 'Ran 3 tests in 0.010s\n\nOK (skipped=1)\n'}), 2)

    def test_worker_env_keeps_phase_test_temporaries_inside_selected_root(self):
        with mock.patch.dict(os.environ, {'FORAGER_TEST_TEMP_ROOT': '/var/tmp/unselected'}):
            environment = worker.worker_env(self.root)
        self.assertEqual(environment['FORAGER_TEST_TEMP_ROOT'], str(self.root / '.runtime/tmp'))
        self.assertEqual(environment['TMPDIR'], environment['FORAGER_TEST_TEMP_ROOT'])

    def test_external_sandbox_cannot_be_claimed_without_live_denial(self):
        canary=self.base/'outside';canary.write_text('preserve')
        with self.assertRaises(ValueError):worker.verify_outer_boundary(self.root,canary)
        self.assertEqual(canary.read_text(),'preserve')

    def test_default_native_command_keeps_builtin_sandbox_and_disables_goals(self):
        argv=worker.native_argv('codex',self.root,'fixture-model',self.root/'final.txt')
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',argv)
        self.assertIn('workspace-write',argv);self.assertIn('goals',argv);self.assertIn('--skip-git-repo-check',argv)
        self.assertIn('tool_output_token_limit=1200',argv)

    def test_helper_tool_profile_is_explicit_and_keeps_existing_bounds(self):
        default = worker.native_argv('codex',self.root,'fixture-model',self.root/'final.txt')
        narrowed = worker.native_argv('codex',self.root,'fixture-model',self.root/'final.txt',False,True)
        self.assertNotIn('web_search="disabled"', default)
        self.assertIn('web_search="disabled"', narrowed)
        index = narrowed.index('view_image')
        self.assertEqual(narrowed[index-1], '--disable')
        self.assertIn('workspace-write', narrowed)
        self.assertIn('tool_output_token_limit=1200', narrowed)
        self.assertIn('model_context_window=32768', narrowed)
        self.assertNotIn('tools.experimental_request_user_input.enabled=false', default)
        self.assertNotIn('--strict-config', default)
        self.assertIn('tools.experimental_request_user_input.enabled=false', narrowed)
        self.assertIn('--strict-config', narrowed)
        external = worker.native_argv('codex',self.root,'fixture-model',self.root/'final.txt',True,True)
        self.assertIn('tools.experimental_request_user_input.enabled=false', external)
        self.assertIn('--strict-config', external)
        self.assertIn('--dangerously-bypass-approvals-and-sandbox', external)
        self.assertNotIn('--sandbox', external)
        with self.assertRaises(ValueError):
            worker.native_argv('codex',self.root,'fixture-model',self.root/'final.txt',False,'inferred')

    def test_selected_generation_policy_requires_relay_before_starting_native(self):
        with mock.patch.object(worker, '_supervise_native') as native:
            with self.assertRaisesRegex(ValueError, 'requires the request relay'):
                worker.supervise(['unused'], 'prompt', self.root, self.root/'run', time.time()+5,
                    worker.ProgressGuard(), {}, generation_policy={'max_output_tokens':2048})
            native.assert_not_called()

    def test_source_excerpt_bounds_large_files_and_rejects_escapes(self):
        source=self.root/'long.rs';source.write_text(('x'*200+'\n')*300)
        result=worker.source_excerpt(self.root,'long.rs',2,60)
        self.assertLessEqual(len(result['text']),4000)
        self.assertTrue(result['truncated']);self.assertTrue(result['text'].startswith('2: '))
        for path,start,lines in [('long.rs',0,60),('long.rs',1,81),('../origin/Cargo.toml',1,60),(str(source),1,60)]:
            with self.subTest(path=path,start=start,lines=lines):
                with self.assertRaises(ValueError):worker.source_excerpt(self.root,path,start,lines)
        source.unlink();source.symlink_to(self.origin/'Cargo.toml')
        with self.assertRaises(ValueError):worker.source_excerpt(self.root,'long.rs')

    def test_checked_edit_rejects_drift_ambiguity_and_no_effect(self):
        path=self.root/'source.py';path.write_text('value = 1\n')
        request={'path':'source.py','expected_sha256':worker.sha(path),'old':'value = 1','new':'value = 2'}
        for change in [{'expected_sha256':'0'*64},{'old':'missing'},{'new':'value = 1'}]:
            with self.assertRaises(ValueError):worker.edit_source(self.root,dict(request,**change))
            self.assertEqual(path.read_text(),'value = 1\n')
        result=worker.edit_source(self.root,request)
        self.assertTrue(result['changed']);self.assertEqual(path.read_text(),'value = 2\n')
        with self.assertRaises(ValueError):worker.edit_source(self.root,request)
        path.write_text('same\nsame\n')
        with self.assertRaises(ValueError):worker.edit_source(self.root,{'path':'source.py','expected_sha256':worker.sha(path),'old':'same','new':'changed'})

    def test_progress_writer_preserves_identity_and_rejects_unsafe_fields(self):
        context=self.root/'.night/phase-context.json';context.parent.mkdir()
        identity={'schema':'offdesk_task_progress.v1','phase':'phase-1','baseline_sha256':'a'*64}
        worker.write_json(context,identity)
        result=worker.save_progress(self.root,context,'reuse implementation','verify edit',['test passed'],['source.py'])
        saved=json.loads(Path(result['path']).read_text())
        self.assertEqual({k:saved[k] for k in identity},identity)
        self.assertFalse(result['claims_verified'])
        before=Path(result['path']).read_bytes()
        with self.assertRaises(ValueError):worker.save_progress(self.root,context,'x'*1501,'next',[],[])
        self.assertEqual(Path(result['path']).read_bytes(),before)
        Path(result['path']).unlink();Path(result['path']).symlink_to(context)
        with self.assertRaises(ValueError):worker.save_progress(self.root,context,'reuse','next',[],[])


class RecoveryContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / '.night/run'
        self.output.mkdir(parents=True)
        self.baseline = self.output / 'BASELINE.json'
        worker.write_json(self.baseline, {'source.py': {'sha256': 'a' * 64}})
        self.context = self.output / 'phase-context.json'
        self.binding = {'schema': 'offdesk_task_progress.v1', 'phase': 'phase',
                        'baseline_sha256': worker.sha(self.baseline),
                        'root_identity': worker.root_identity(self.root),
                        'run_binding': {'run_id': 'run-1', 'task_id': 'task-1', 'request_id': 'request-1'}}
        worker.write_json(self.context, {**self.binding, 'index': [], 'excerpts': []})
        self.context_sha = worker.sha(self.context)
        self.save()
        self.progress = self.output / 'phase-progress.json'

    def save(self, assessment='reuse existing helper', next_action='run the focused regression', checks=None):
        return worker.save_progress(self.root, self.context, assessment, next_action,
                                    checks or [], ['source.py'], self.context_sha)

    def read(self):
        return worker.recovery_context(self.root, self.context, self.context_sha, worker.sha(self.progress))

    def progress_cli(self, content, stdin=b''):
        argv = [sys.executable, '-I', '-S', worker.__file__, 'progress', '--root', str(self.root),
                '--context', str(self.context), '--expected-context-sha256', self.context_sha, *content]
        process = subprocess.run(argv, input=stdin, capture_output=True, cwd=self.root, timeout=30)
        return process.returncode, json.loads(process.stdout)

    def test_repeated_progress_strings_round_trip_through_fresh_cli_and_recovery(self):
        assessment = "reuse 'quoted' text; $HOME and `echo literal`\n한국어"
        code, receipt = self.progress_cli(['--assessment', assessment, '--next-action', 'one concrete action',
            '--checked-command', 'read selected excerpt', '--checked-command', 'inspect prior failure',
            '--changed-file', 'source.py'])
        self.assertEqual(code, 0)
        saved = self.read()['progress']
        self.assertEqual(saved['assessment'], assessment)
        self.assertEqual(saved['checked_commands'], ['read selected excerpt', 'inspect prior failure'])
        self.assertEqual(saved['changed_files'], ['source.py'])
        self.assertEqual(receipt['sha256'], worker.sha(self.progress))
        self.assertFalse(receipt['claims_verified'])

    def test_stdin_progress_round_trip_and_hash_mismatch_preserves_packet(self):
        fields = {'assessment': 'reuse existing helper', 'next_action': 'run one real check',
                  'checked_commands': ['read selected excerpt'], 'changed_files': []}
        body = json.dumps(fields).encode()
        code, _ = self.progress_cli(['--request-stdin'], body)
        self.assertEqual(code, 0)
        self.assertEqual(self.read()['progress']['checked_commands'], fields['checked_commands'])
        before = self.progress.read_bytes()
        code, held = self.progress_cli(['--request-stdin', '--expected-context-sha256', '0' * 64], body)
        self.assertEqual(code, 2)
        self.assertEqual((held['field'], held['code']), ('context', 'context_hash_mismatch'))
        self.assertEqual(self.progress.read_bytes(), before)

    def test_generated_structured_prompt_example_executes_as_one_bound_command(self):
        runtime = self.root / '.runtime'
        runtime.mkdir()
        (runtime/'offdesk_native_worker.py').write_bytes(Path(worker.__file__).read_bytes())
        instructions = worker.progress_instructions(str(self.context.relative_to(self.root)), self.context_sha)
        command = instructions.split('Structured example (replace the assessment and next action):\n', 1)[1]
        result = subprocess.run(['/bin/sh', '-c', command], cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read()['progress']['next_action'], 'One concrete next action')
        self.assertEqual(self.read()['progress']['checked_commands'], [])
        self.assertFalse(json.loads(result.stdout)['claims_verified'])

    def test_invalid_progress_cli_inputs_explain_field_without_logging_values_or_writing(self):
        common = ['--assessment', 'reuse', '--next-action', 'next']
        cases = [
            (common + ['--checks-json', '[{name:VALUE_SECRET}]'], b'', 'checked_commands', 'invalid_json'),
            (common + ['--checks-json', '[{"name":"VALUE_SECRET"}]'], b'', 'checked_commands', 'invalid_string_array'),
            (common + ['--changed-file', 'VALUE_SECRET' * 50], b'', 'changed_files', 'invalid_string_array'),
            (common + ['--assessment', 'VALUE_SECRET' * 130], b'', 'assessment', 'invalid_text'),
            (common + ['--checked-command', 'VALUE_SECRET', '--checks-json', '[]'], b'', 'checked_commands', 'mixed_input'),
            (['--request-stdin'], b'VALUE_SECRET' * 800, 'request', 'request_too_large'),
            (['--request-stdin'], b'{"assessment":"VALUE_SECRET","assessment":"other"}', 'request', 'duplicate_json_key'),
            (['--request-stdin'], b'{"binding":"VALUE_SECRET"}', 'request', 'invalid_fields'),
            (['--request-stdin'], b'\xffVALUE_SECRET', 'request', 'invalid_json'),
            (common + ['--request-stdin'], b'{}', 'request', 'mixed_input'),
        ]
        before = self.progress.read_bytes()
        for content, body, field, error in cases:
            with self.subTest(error=error, field=field):
                code, payload = self.progress_cli(content, body)
                self.assertEqual(code, 2)
                self.assertEqual((payload['field'], payload['code']), (field, error))
                self.assertTrue(payload['expected'])
                self.assertNotIn('VALUE_SECRET', json.dumps(payload))
                self.assertEqual(self.progress.read_bytes(), before)

    def test_fresh_process_rereads_exact_progress_without_launch_or_acceptance(self):
        expected = self.read()
        before = {path: path.read_bytes() for path in self.output.iterdir()}
        command = [sys.executable, '-I', '-S', worker.__file__, 'recovery-context',
                   '--root', str(self.root), '--context', str(self.context),
                   '--expected-context-sha256', self.context_sha,
                   '--expected-progress-sha256', worker.sha(self.progress)]
        output = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
        self.assertEqual(json.loads(output.stdout), expected)
        self.assertEqual(expected['next_action'], 'run the focused regression')
        self.assertEqual(expected['binding'], self.binding)
        self.assertFalse(expected['claims_verified'])
        self.assertFalse(expected['can_resume_worker'])
        self.assertFalse(expected['provider_calls_authorized'])
        self.assertEqual(before, {path: path.read_bytes() for path in self.output.iterdir()})

    def test_utf8_byte_budget_refuses_oversized_progress_before_write(self):
        before = self.progress.read_bytes()
        with self.assertRaisesRegex(ValueError, 'UTF-8 byte budget'):
            self.save('가' * 1500, '나' * 1500)
        self.assertEqual(before, self.progress.read_bytes())
        with self.assertRaises(ValueError):
            self.save(next_action='')
        self.assertEqual(before, self.progress.read_bytes())

    def test_changed_context_progress_and_baseline_are_refused(self):
        progress_sha = worker.sha(self.progress)
        self.save(next_action='inspect only the edited function')
        with self.assertRaisesRegex(ValueError, 'progress hash changed'):
            worker.recovery_context(self.root, self.context, self.context_sha, progress_sha)

        context = json.loads(self.context.read_text())
        context['index'] = ['changed context']
        worker.write_json(self.context, context)
        before = self.progress.read_bytes()
        with self.assertRaises(ValueError):
            self.save()
        with self.assertRaisesRegex(ValueError, 'context hash changed'):
            self.read()
        self.assertEqual(self.progress.read_bytes(), before)
        worker.write_json(self.context, {**self.binding, 'index': [], 'excerpts': []})
        worker.write_json(self.baseline, {'source.py': 'changed baseline'})
        with self.assertRaisesRegex(ValueError, 'baseline hash changed'):
            self.read()

    def test_same_size_and_mtime_progress_change_is_rejected_by_pinned_hash(self):
        expected = worker.sha(self.progress)
        original = self.progress.read_bytes()
        st = self.progress.stat()
        changed = original.replace(b'"next_action"', b'"NEXT_ACTION"', 1)
        self.assertNotEqual(changed, original)
        self.assertEqual(len(changed), len(original))
        self.progress.write_bytes(changed)
        os.utime(self.progress, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.progress.stat().st_mtime_ns, st.st_mtime_ns)
        with self.assertRaisesRegex(ValueError, 'progress hash changed'):
            worker.recovery_context(self.root, self.context, self.context_sha, expected)

    def test_progress_from_another_phase_or_run_is_preserved_and_refused(self):
        original = json.loads(self.progress.read_text())
        for key, value in [('phase', 'another-phase'), ('run_binding', dict(self.binding['run_binding'], run_id='another-run'))]:
            worker.write_json(self.progress, dict(original, **{key: value}))
            before = self.progress.read_bytes()
            with self.assertRaises(ValueError):
                self.read()
            with self.assertRaises(ValueError):
                self.save()
            self.assertEqual(before, self.progress.read_bytes())

    def test_context_hash_is_required_for_bound_writer_and_legacy_recovery_is_held(self):
        with self.assertRaisesRegex(ValueError, 'task-context hash required'):
            worker.save_progress(self.root, self.context, 'reuse', 'next', [], [])
        self.binding.pop('run_binding')
        worker.write_json(self.context, self.binding)
        self.context_sha = worker.sha(self.context)
        with self.assertRaisesRegex(ValueError, 'root and run/task/request'):
            self.read()

    def test_symlinked_parent_and_hardlinked_progress_are_refused(self):
        self.progress.unlink()
        canary = self.root / 'outside.json'
        worker.write_json(canary, {**self.binding, 'assessment': 'outside', 'next_action': 'stay',
                                   'checked_commands': [], 'changed_files': []})
        os.link(canary, self.progress)
        before = canary.read_bytes()
        with self.assertRaises(ValueError):
            self.read()
        with self.assertRaises(ValueError):
            self.save()
        self.assertEqual(before, canary.read_bytes())
        moved = self.root / 'moved-night'
        self.output.parent.rename(moved)
        self.output.parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.read()
        with self.assertRaises(ValueError):
            self.save()

    def test_fixed_partial_symlink_is_never_used_and_check_claims_stay_unverified(self):
        canary = self.root / 'outside.txt'
        canary.write_text('preserve outside bytes')
        self.progress.with_name(self.progress.name + '.partial').symlink_to(canary)
        self.save(checks=['model claims test passed; independently unverified'])
        report = self.read()
        self.assertEqual(canary.read_text(), 'preserve outside bytes')
        self.assertFalse(report['claims_verified'])
        self.assertEqual(report['progress']['checked_commands'], ['model claims test passed; independently unverified'])


class InputDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_input_worker(self, code, prompt, seconds=3):
        prompt_file = self.root / 'prompt.txt'
        prompt_file.write_text(prompt)
        pid_file = self.root / 'native.pid'
        child_code = ("import os;from pathlib import Path;Path(" + repr(str(pid_file))
                      + ").write_text(str(os.getpid()));" + code)
        controller = (
            "import sys,time,json,os;from pathlib import Path;sys.path.insert(0,"
            + repr(str(Path(worker.__file__).parent)) + ");import offdesk_native_worker as w;"
            + "root=Path(sys.argv[1]);r=w.supervise("
            + repr([sys.executable, '-I', '-S', '-u', '-c', child_code])
            + ",(root/'prompt.txt').read_text(),root,root/'run',time.time()+"
            + repr(seconds) + ",w.ProgressGuard(),dict(os.environ));print(json.dumps(r))")
        process = subprocess.Popen([sys.executable, '-I', '-S', '-u', '-c', controller, str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                self.fail('Supervisor blocked outside its selected deadline while delivering input')
            self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
            return json.loads(stdout)
        finally:
            worker.stop_group(process)
            process.stdout.close()
            process.stderr.close()
            # This PID belongs only to the fake native process created above.
            if pid_file.exists():
                try:
                    os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_deadline_applies_when_native_worker_never_reads_prompt(self):
        result = self.run_input_worker('import time;time.sleep(60)', 'x' * (1024 * 1024), seconds=.3)
        self.assertEqual(result['stop_reason'], 'deadline_reached')
        self.assertEqual(result['verification_state'], 'failed')
        self.assertFalse(result['prompt_delivery_complete'])
        self.assertLess(result['prompt_bytes_sent'], result['prompt_bytes_total'])
        packet = json.loads((self.root / 'run/council-request.json').read_text())
        self.assertFalse(packet['can_resume_worker'])

    def test_nonfinite_deadline_is_rejected_before_worker_launch(self):
        for deadline in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(deadline=deadline), mock.patch.object(worker.subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    worker.supervise(['unused'], 'prompt', self.root, self.root / 'run',
                                     deadline, worker.ProgressGuard(), dict(os.environ))
                launch.assert_not_called()
                self.assertFalse((self.root / 'run').exists())

    def test_large_utf8_prompt_is_delivered_while_native_output_is_drained(self):
        marker = self.root / 'received.sha256'
        code = ("import sys,json,hashlib;"
                "event=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'x'*2048}});"
                "sys.stdout.write((event+'\\n')*128);sys.stdout.flush();"
                "data=sys.stdin.buffer.read();Path(" + repr(str(marker))
                + ").write_text(hashlib.sha256(data).hexdigest());"
                "print(json.dumps({'type':'turn.completed'}),flush=True)")
        prompt = '가🙂' * 100000
        result = self.run_input_worker(code, prompt)
        self.assertIsNone(result['stop_reason'])
        self.assertTrue(result['prompt_delivery_complete'])
        self.assertEqual(result['prompt_bytes_sent'], len(prompt.encode()))
        self.assertEqual(marker.read_text(), worker.hashlib.sha256(prompt.encode()).hexdigest())

    def test_premature_stdin_close_cannot_be_accepted_as_a_completed_turn(self):
        code = "import sys,json,time;os.close(0);print(json.dumps({'type':'turn.completed'}),flush=True);time.sleep(60)"
        result = self.run_input_worker(code, 'x' * (1024 * 1024))
        self.assertEqual(result['stop_reason'], 'native_stdin_closed_before_prompt')
        self.assertFalse(result['prompt_delivery_complete'])
        self.assertEqual(result['verification_state'], 'failed')


class LifecycleTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def run_fake(self,code,limit=3,idle=2,command_timeout=2,deadline_seconds=5,guard=None,**kwargs):
        argv=[sys.executable,'-I','-S','-u','-c',code]
        process=subprocess.Popen(argv,cwd=self.root,env=dict(os.environ),stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        try:
            process.stdin.write(b'prompt');process.stdin.close()
            # Event/timer tests start with queued output, so host-dependent
            # interpreter startup latency is outside their measured interval.
            ready,_,_=select.select([process.stdout,process.stderr],[],[],30)
            self.assertTrue(ready,'fake worker failed to become ready')
            # Input was already delivered to warm up the fake worker. Give the
            # supervisor a real writable pipe without sending that input twice.
            input_reader, input_writer = os.pipe()
            process.stdin = os.fdopen(input_writer, 'wb')
            with mock.patch.object(worker.subprocess,'Popen',return_value=process):
                return worker.supervise(argv,'prompt',self.root,self.root/'run',time.time()+deadline_seconds,
                                        guard or worker.ProgressGuard(repeated_errors=limit),dict(os.environ),
                                        idle_seconds=idle,command_seconds=command_timeout,**kwargs)
        finally:
            worker.stop_group(process)
            if 'input_reader' in locals():
                os.close(input_reader)
            for stream in (process.stdin,process.stdout,process.stderr):stream.close()

    def assert_failure_packet(self,reason):
        result=json.loads((self.root/'run/result.json').read_text())
        packet=json.loads((self.root/'run/council-request.json').read_text())
        self.assertEqual(result['stop_reason'],reason)
        self.assertEqual(result['verification_state'],'failed')
        self.assertEqual(packet['result_sha256'],worker.sha(self.root/'run/result.json'))
        self.assertFalse(packet['can_resume_worker'])

    def test_failure_stops_process_and_creates_council_request_before_phase_success(self):
        code='import json,sys,time;sys.stdin.read();\nfor i in range(9):\n print(json.dumps('+repr(command_event(0,101,'Permission denied'))+').replace('+'\'"id": "0"\''+',\'"id": "\'+str(i)+\'"\'),flush=True);time.sleep(.05)\ntime.sleep(10)'
        result=self.run_fake(code)
        self.assertEqual(result['stop_reason'],'environment_permission_denied');self.assertEqual(result['tool_calls'],3)
        packet=json.loads((self.root/'run/council-request.json').read_text())
        self.assertTrue(packet['review_only']);self.assertFalse(packet['can_resume_worker'])
        self.assertFalse(packet['provider_calls_authorized'])

    def test_xml_parser_error_creates_early_failure_review(self):
        code="import sys,json,time;sys.stdin.read();print(json.dumps({'type':'error','message':'XML syntax error on line 4'}),flush=True);time.sleep(10)"
        result=self.run_fake(code);self.assertEqual(result['stop_reason'],'tool_protocol_parse_error')
        self.assertTrue((self.root/'run/council-request.json').exists())

    def test_replayed_messages_do_not_keep_a_response_alive(self):
        code="import sys,json,time;sys.stdin.read();\nfor i in range(100): print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'working'}}),flush=True);time.sleep(.02)"
        self.assertEqual(self.run_fake(code,idle=.12)['stop_reason'],'response_inactive')

    def test_native_generation_can_finish_an_answer_without_tools_after_idle_interval(self):
        code = ("import sys,json,time;sys.stdin.read();\nfor i in range(12):\n"
                " print(json.dumps({'type':'item.updated','item':{'id':'thought','type':'reasoning','text':str(i)}}),flush=True);time.sleep(.03)\n"
                "print(json.dumps({'type':'item.completed','item':{'id':'answer','type':'agent_message','text':'answer complete'}}),flush=True)\n"
                "print(json.dumps({'type':'turn.completed'}),flush=True)")
        result = self.run_fake(code, idle=.12)
        self.assertIsNone(result['stop_reason'])
        self.assertTrue(result['turn_completed'])
        self.assertEqual(result['completed_tool_calls'], 0)
        self.assertGreater(result['seconds_without_tool_progress'], .12)
        self.assertEqual(result['supervision']['last_response_kind'], 'content')
        self.assertEqual(result['supervision']['activity_state'], 'turn_completed')

    def test_native_generation_can_reach_tools_after_idle_interval(self):
        code = ("import sys,json,time;sys.stdin.read();\nfor i in range(12):\n"
                " print(json.dumps({'type':'item.updated','item':{'id':'thought','type':'reasoning','text':str(i)}}),flush=True);time.sleep(.03)\n"
                f"print(json.dumps({command_event('after-thinking')!r}),flush=True)\n"
                "print(json.dumps({'type':'turn.completed'}),flush=True)")
        result = self.run_fake(code, idle=.12)
        self.assertIsNone(result['stop_reason'])
        self.assertEqual(result['completed_tool_calls'], 1)

    def test_native_response_activity_never_extends_the_selected_deadline(self):
        code = ("import sys,json,time;sys.stdin.read();\nfor i in range(200):\n"
                " print(json.dumps({'type':'item.updated','item':{'id':'thought','type':'reasoning','text':str(i)}}),flush=True);time.sleep(.02)\n")
        result = self.run_fake(code, idle=.1, deadline_seconds=.3)
        self.assertEqual(result['stop_reason'], 'deadline_reached')
        self.assertFalse(result['turn_completed'])
        self.assertEqual(result['completed_tool_calls'], 0)
        self.assertEqual(result['supervision']['last_response_kind'], 'reasoning')

    def test_response_silence_after_generation_still_stops(self):
        code = ("import sys,json,time;sys.stdin.read();"
                "print(json.dumps({'type':'item.completed','item':{'id':'thought','type':'reasoning','text':'first part'}}),flush=True);time.sleep(10)")
        result = self.run_fake(code, idle=.12)
        self.assertEqual(result['stop_reason'], 'response_inactive')
        self.assertEqual(result['supervision']['activity_state'], 'response_inactive')
        self.assertGreaterEqual(result['supervision']['seconds_without_response_progress'], .12)
        self.assert_failure_packet('response_inactive')

    def test_success_requires_completed_native_turn(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        self.assertIsNone(result['stop_reason']);self.assertFalse((self.root/'run/council-request.json').exists())
        self.assertEqual(result['verification_state'],'pending')

    def test_queued_completion_is_drained_before_idle_decision(self):
        code="import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}),flush=True)"
        result=self.run_fake(code,idle=.000000001)
        self.assertIsNone(result['stop_reason']);self.assertTrue(result['turn_completed'])

    def test_progress_snapshot_survives_compaction_stop_without_authorizing_resume(self):
        event={'type':'item.completed','item':{'type':'error','message':'multiple compactions may reduce accuracy'}}
        code='import sys,json;sys.stdin.read();print(json.dumps('+repr(event)+'),flush=True)'
        result=self.run_fake(code,guard=worker.ProgressGuard(context_warnings=1),
                             progress_observer=lambda:{'next_action':'check one edit','claims_verified':False})
        saved=json.loads((self.root/'run/progress-checkpoints.jsonl').read_text())
        self.assertEqual(saved['context_warnings'],1)
        self.assertEqual(saved['observation']['next_action'],'check one edit')
        self.assertFalse(saved['observation']['claims_verified'])
        self.assertEqual(result['stop_reason'],'context_warning_budget_reached')
        self.assertFalse(json.loads((self.root/'run/council-request.json').read_text())['can_resume_worker'])

    def test_active_command_can_outlast_model_idle_timer(self):
        start={'type':'item.started','item':{'id':'long','type':'command_execution','command':'cargo test'}}
        code="import sys,json,time;sys.stdin.read();print(json.dumps("+repr(start)+"),flush=True);time.sleep(.8);print(json.dumps("+repr(command_event('long'))+"),flush=True);print(json.dumps({'type':'turn.completed'}),flush=True)"
        result=self.run_fake(code,idle=.5,command_timeout=2)
        self.assertIsNone(result['stop_reason']);self.assertTrue(result['turn_completed'])

    def test_stalled_command_has_its_own_timeout_despite_repeated_starts(self):
        start={'type':'item.started','item':{'id':'long','type':'command_execution','command':'cargo test'}}
        code="import sys,json,time;sys.stdin.read();\nfor i in range(100): print(json.dumps("+repr(start)+"),flush=True);time.sleep(.02)"
        result=self.run_fake(code,idle=.5,command_timeout=.8)
        self.assertEqual(result['stop_reason'],'active_command_timeout')
        self.assert_failure_packet('active_command_timeout')

    def test_run_deadline_bounds_active_command(self):
        start={'type':'item.started','item':{'id':'long','type':'command_execution','command':'cargo test'}}
        code="import sys,json,time;sys.stdin.read();print(json.dumps("+repr(start)+"),flush=True);time.sleep(10)"
        result=self.run_fake(code,deadline_seconds=.2,idle=.04,command_timeout=10)
        self.assertEqual(result['stop_reason'],'deadline_reached')

    def test_start_limit_stops_worker_with_durable_in_flight_accounting(self):
        start=command_event('first');start['type']='item.started'
        final=command_event('last');final['type']='item.started'
        marker=self.root/'extra-command'
        code="import sys,json,time;from pathlib import Path;sys.stdin.read();print(json.dumps("+repr(start)+"),flush=True);print(json.dumps("+repr(command_event('first'))+"),flush=True);print(json.dumps("+repr(final)+"),flush=True);time.sleep(1);Path("+repr(str(marker))+").write_text('extra')"
        result=self.run_fake(code,guard=worker.ProgressGuard(max_calls=2))
        self.assertEqual(result['stop_reason'],'tool_call_budget_reached')
        self.assertEqual((result['tool_calls'],result['started_tool_calls'],result['completed_tool_calls'],result['in_flight_tool_calls']),(2,2,1,1))
        status=json.loads((self.root/'run/status.json').read_text())
        self.assertTrue(status['terminal']);self.assertEqual(status['stop_reason'],result['stop_reason'])
        self.assertFalse(marker.exists());self.assert_failure_packet('tool_call_budget_reached')

    def test_failed_check_loop_stops_before_deadline_despite_zero_exit_code(self):
        events=[command_event(i,output='FAILED (failures=3)',command=f'sleep {i}; python3 -m unittest test_worker -v 2>&1 | tail -{i+3}') for i in range(5)]
        code="import sys,json,time;sys.stdin.read();\nfor event in "+repr(events)+":\n print(json.dumps(event),flush=True);time.sleep(.05)\ntime.sleep(10)"
        result=self.run_fake(code)
        self.assertEqual(result['stop_reason'],'repeated_failed_check_without_source_change')
        self.assertEqual(result['tool_calls'],3)
        self.assertEqual(list(result['failed_check_counts'].values()),[3])
        self.assert_failure_packet('repeated_failed_check_without_source_change')

    def test_manifested_root_observes_source_repairs_and_excludes_its_logs(self):
        (self.root/'.runtime').mkdir();(self.root/'.runtime/source_manifest.json').write_text('{}')
        source=self.root/'source.py';source.write_text('before')
        first=command_event('a',code=1,output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        second=command_event('b',code=1,output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        third=command_event('c',code=1,output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        code="import sys,json,time;from pathlib import Path;sys.stdin.read();print(json.dumps("+repr(first)+"),flush=True);time.sleep(.25);Path("+repr(str(source))+").write_text('after');print(json.dumps("+repr(second)+"),flush=True);time.sleep(.1);print(json.dumps("+repr(third)+"),flush=True);time.sleep(10)"
        result=worker.supervise([sys.executable,'-I','-S','-u','-c',code],'prompt',self.root,self.root/'.night/run',time.time()+5,worker.ProgressGuard(repeated_errors=2),dict(os.environ))
        self.assertEqual(result['stop_reason'],'repeated_failed_check_without_source_change')
        self.assertEqual(result['tool_calls'],3)
        self.assertEqual(list(result['failed_check_counts'].values()),[2])
        self.assertEqual(result['failed_check_source_generation'],worker.live_source_identity(self.root))

    def test_actual_source_repair_resets_failed_check_streak(self):
        source=self.root/'source.py';source.write_text('before')
        event=command_event('a',output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        other=command_event('b',output='FAILED (failures=1)',command='python3 -m unittest test_worker')
        code="import sys,json,time;from pathlib import Path;sys.stdin.read();print(json.dumps("+repr(event)+"),flush=True);time.sleep(.15);Path("+repr(str(source))+").write_text('after');print(json.dumps("+repr(other)+"),flush=True);print(json.dumps({'type':'turn.completed'}),flush=True)"
        result=self.run_fake(code,guard=worker.ProgressGuard(repeated_errors=2),source_observer=lambda:worker.sha(source))
        self.assertIsNone(result['stop_reason']);self.assertTrue(result['turn_completed'])

    def test_completed_command_restarts_model_idle_timer(self):
        code="import sys,json,time;sys.stdin.read();print(json.dumps("+repr(command_event('done'))+"),flush=True);time.sleep(10)"
        self.assertEqual(self.run_fake(code,idle=.04)['stop_reason'],'response_inactive')

    def test_review_is_bound_one_shot_and_cannot_resume(self):
        self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'error','message':'XML syntax error'}))")
        packet=self.root/'run/council-request.json'
        argv=[sys.executable,'-I','-S','-c',"import sys,json;sys.stdin.read();print(json.dumps({'decision':'rework','reason':'fix environment'}))"]
        result=worker.review_failure(packet,argv,10);self.assertFalse(result['can_resume_worker'])
        with self.assertRaises(FileExistsError):worker.review_failure(packet,argv,10)

    def test_changed_failure_evidence_refuses_reviewer_call(self):
        self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'error','message':'XML syntax error'}))")
        (self.root/'run/stdout.log').write_text('changed')
        with self.assertRaises(ValueError):worker.review_failure(self.root/'run/council-request.json',['false'],2)
        self.assertFalse((self.root/'run/council-review.claim.json').exists())

    def test_stderr_protocol_failures_are_bounded(self):
        code="import sys,time;sys.stdin.read();\nfor i in range(10): print('unsupported call: apply_patch',file=sys.stderr,flush=True);time.sleep(.02)\ntime.sleep(10)"
        self.assertEqual(self.run_fake(code)['stop_reason'],'unusable_tool_protocol')

    def test_malformed_native_event_leaves_a_failure_packet(self):
        result=self.run_fake("import sys;sys.stdin.read();print('[]')")
        self.assertEqual(result['stop_reason'],'invalid_native_event')
        self.assertTrue((self.root/'run/council-request.json').exists())

    def test_malformed_nested_error_creates_durable_failure(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'error','error':'provider disconnected'}),flush=True)")
        self.assertEqual(result['stop_reason'],'invalid_native_event')
        self.assert_failure_packet('invalid_native_event')

    def test_invalid_event_fields_are_rejected_without_parser_exceptions(self):
        for event in [{'type':'turn.failed','error':None},{'type':'error','error':{'message':[]}},
                      {'type':'error','message':42},{'type':'item.completed','item':None},
                      command_event('bad',output={}),{'type':'item.started','item':{'type':'command_execution','id':[]}}]:
            with self.subTest(event=event):
                self.assertEqual(worker.ProgressGuard().feed(event),'invalid_native_event')

    def test_unexpected_guard_exception_still_creates_terminal_failure(self):
        with mock.patch.object(worker.ProgressGuard,'feed',side_effect=RuntimeError('unexpected')):
            self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}),flush=True)")
        self.assert_failure_packet('native_supervisor_error')

    def test_completion_replays_do_not_extend_tool_progress_idle_timer(self):
        event = command_event('replayed', command='read source')
        code = ("import sys,json,time;sys.stdin.read();\nfor _ in range(50):\n"
                f" print(json.dumps({event!r}),flush=True);time.sleep(.02)\n")
        result = self.run_fake(code, idle=.09)
        self.assertEqual(result['stop_reason'], 'response_inactive')
        self.assertEqual(result['completed_tool_calls'], 1)
        self.assertGreaterEqual(result['seconds_without_tool_progress'], .09)
        self.assertLess(result['seconds_without_tool_progress'], .8)
        self.assertIsNotNone(result['last_tool_completed_at'])

    def test_late_invalid_verification_argv_prevents_real_first_command_side_effect(self):
        initial = self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        marker = self.root / 'must-not-run'
        first = [sys.executable, '-I', '-S', '-c', "from pathlib import Path;Path('must-not-run').write_text('ran')"]
        invalid = [[], [sys.executable, None], 'python3', ['   '], [sys.executable, '\0']]
        for index, argv in enumerate(invalid):
            with self.subTest(argv=argv):
                out = self.root / f'check-{index}'
                out.mkdir()
                for name in ('stdout.log', 'stderr.log'):
                    (out / name).write_text('local test evidence')
                result = worker.verify_result(dict(initial), out, self.root, [first, argv], time.time() + 5)
                self.assertFalse(marker.exists())
                self.assertEqual(result['stop_reason'], 'post_edit_verification_error')
                self.assertEqual(result['verification_checks'], [])
                packet = json.loads((out / 'council-request.json').read_text())
                self.assertEqual(packet['result_sha256'], worker.sha(out / 'result.json'))

    def test_native_completion_cannot_override_a_failed_edit_check(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        verdict=worker.verify_result(result,self.root/'run',self.root,[[sys.executable,'-I','-S','-c','raise SystemExit(1)']],time.time()+2)
        self.assertEqual(verdict['stop_reason'],'post_edit_verification_failed')
        packet=json.loads((self.root/'run/council-request.json').read_text())
        self.assertEqual(packet['result_sha256'],worker.sha(self.root/'run/result.json'))

    def test_missing_verification_command_records_failure_and_one_review(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        verdict=worker.verify_result(result,self.root/'run',self.root,[[str(self.root/'missing-command')]],time.time()+2)
        self.assertEqual(verdict['verification_error']['type'],'FileNotFoundError')
        self.assert_failure_packet('post_edit_verification_error')
        argv=[sys.executable,'-I','-S','-c',"import sys,json;sys.stdin.read();print(json.dumps({'decision':'defer','reason':'missing verifier'}))"]
        review=worker.watch_review(self.root/'run/council-request.json',argv,time.time()+15)
        self.assertEqual(review['review']['decision'],'defer')

    def test_verification_interrupt_records_failure(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        with mock.patch.object(worker,'bounded_command',side_effect=KeyboardInterrupt):
            worker.verify_result(result,self.root/'run',self.root,[['check']],time.time()+2)
        self.assert_failure_packet('verification_interrupted')

    def test_verification_sigterm_stops_child_and_records_failure(self):
        marker=self.root/'ready';child_pid=self.root/'child-pid'
        child="import os,time;from pathlib import Path;Path("+repr(str(child_pid))+ ").write_text(str(os.getpid()));Path("+repr(str(marker))+").write_text('ready');time.sleep(30)"
        code="import sys,time,json;from pathlib import Path;sys.path.insert(0,"+repr(str(Path(worker.__file__).parent))+");import offdesk_native_worker as w;root=Path(sys.argv[1]);out=root/'run';r=w.supervise([sys.executable,'-I','-S','-c',\"import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))\"],'prompt',root,out,time.time()+10,w.ProgressGuard(),dict(__import__('os').environ));print(json.dumps(w.verify_result(r,out,root,[[sys.executable,'-I','-S','-c',"+repr(child)+"]],time.time()+10)))"
        process=subprocess.Popen([sys.executable,'-I','-S','-c',code,str(self.root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+30
            while not marker.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists())
            process.terminate();stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,stderr.decode())
            self.assert_failure_packet('verification_interrupted')
            with self.assertRaises(ProcessLookupError):os.kill(int(child_pid.read_text()),0)
        finally:
            if process.poll() is None:process.kill();process.wait()

    def test_watcher_does_not_accept_provisional_native_result(self):
        self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        verdict=worker.watch_review(self.root/'run/council-request.json',['false'],time.time()+.04)
        self.assertEqual(verdict['status'],'review_window_expired')

    def test_verification_missing_or_deadline_records_failure(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        worker.verify_result(result,self.root/'run',self.root,[['check']],time.time()-1)
        self.assert_failure_packet('verification_deadline_reached')

    def test_native_reviewer_can_return_a_bound_response_file(self):
        self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'error','message':'XML syntax error'}))")
        code="import sys,json;from pathlib import Path;sys.stdin.read();Path(sys.argv[1]).write_text(json.dumps({'decision':'defer','reason':'needs review'}));print('native event stream')"
        result=worker.review_failure(self.root/'run/council-request.json',[sys.executable,'-I','-S','-c',code,'{response_path}'],10)
        self.assertEqual(result['review']['decision'],'defer')
        self.assertFalse(result['can_resume_worker'])

    def test_selected_failure_watcher_calls_reviewer_before_any_phase_gate(self):
        self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'error','message':'XML syntax error'}))")
        argv=[sys.executable,'-I','-S','-c',"import sys,json;sys.stdin.read();print(json.dumps({'decision':'rework','reason':'protocol'}))"]
        verdict=worker.watch_review(self.root/'run/council-request.json',argv,time.time()+15)
        self.assertEqual(verdict['review']['decision'],'rework')
        with self.assertRaises(FileExistsError):worker.watch_review(self.root/'run/council-request.json',argv,time.time()+15)

    def test_success_does_not_spend_a_failure_council_call(self):
        result=self.run_fake("import sys,json;sys.stdin.read();print(json.dumps({'type':'turn.completed'}))")
        worker.verify_result(result,self.root/'run',self.root,[[sys.executable,'-I','-S','-c','print("ok")']],time.time()+2)
        verdict=worker.watch_review(self.root/'run/council-request.json',['false'],time.time()+2)
        self.assertEqual(verdict['status'],'verified_without_failure')
        self.assertFalse(verdict['provider_called'])


class NativeInvocationPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-publication-')
        self.root = Path(self.tmp.name)
        (self.root / '.night').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_final_entry_is_not_visible_while_json_is_being_written(self):
        target = self.root / '.night/run.invocation-entry.json'
        original = worker.json.dump

        def observed(value, output, **kwargs):
            self.assertFalse(target.exists(), 'entry is visible before its JSON is complete')
            return original(value, output, **kwargs)

        with mock.patch.object(worker.json, 'dump', side_effect=observed):
            worker.invocation_write(self.root, '.night/run.invocation-entry.json', {'task_id': 'task'})
        self.assertEqual(json.loads(target.read_bytes()), {'task_id': 'task'})
        self.assertEqual(target.stat().st_nlink, 1)

    def test_interrupted_publication_preserves_the_reservation_without_final_output(self):
        target = self.root / '.night/run.invocation-entry.json'
        with mock.patch.object(worker.json, 'dump', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                worker.invocation_write(self.root, '.night/run.invocation-entry.json', {'task_id': 'task'})
        self.assertFalse(target.exists())
        pending = target.with_name(target.name + '.partial')
        self.assertTrue(pending.exists())
        with self.assertRaises(FileExistsError):
            worker.invocation_write(self.root, '.night/run.invocation-entry.json', {'task_id': 'retry'})

    def test_competing_final_file_is_never_overwritten(self):
        target = self.root / '.night/run.invocation-entry.json'
        original = worker.json.dump

        def competing(value, output, **kwargs):
            target.write_text('competing owner')
            return original(value, output, **kwargs)

        with mock.patch.object(worker.json, 'dump', side_effect=competing):
            with self.assertRaises(FileExistsError):
                worker.invocation_write(self.root, '.night/run.invocation-entry.json', {'task_id': 'task'})
        self.assertEqual(target.read_text(), 'competing owner')

    def test_two_publishers_have_one_winner_and_complete_single_link_bytes(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        gate = threading.Barrier(2)

        def publish(owner):
            gate.wait(timeout=5)
            try:
                worker.invocation_write(self.root, '.night/entry.json', {'owner': owner})
                return owner
            except FileExistsError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, ['one', 'two']))
        winners = [owner for owner in results if owner is not None]
        self.assertEqual(len(winners), 1)
        target = self.root / '.night/entry.json'
        self.assertEqual(json.loads(target.read_bytes()), {'owner': winners[0]})
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertFalse((self.root / '.night/entry.json.partial').exists())

    def test_actual_input_mutation_still_fails_the_stability_guard(self):
        target = self.root / '.night/prompt.txt'
        target.write_bytes(b'original')
        original = worker.os.read
        changed = False

        def mutate(fd, size):
            nonlocal changed
            data = original(fd, size)
            if not changed:
                changed = True
                target.write_bytes(b'changed input bytes')
            return data

        with mock.patch.object(worker.os, 'read', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'changed during read'):
                worker.invocation_file(self.root, '.night/prompt.txt')

    def test_unavailable_atomic_publication_keeps_reservation_and_never_falls_back(self):
        library = mock.Mock()
        library.renameat2.return_value = -1
        with mock.patch.object(worker.ctypes, 'CDLL', return_value=library), \
                mock.patch.object(worker.ctypes, 'get_errno', return_value=worker.errno.ENOSYS):
            with self.assertRaises(OSError):
                worker.invocation_write(self.root, '.night/entry.json', {'task_id': 'task'})
        self.assertFalse((self.root / '.night/entry.json').exists())
        self.assertTrue((self.root / '.night/entry.json.partial').exists())
        with self.assertRaises(FileExistsError):
            worker.invocation_write(self.root, '.night/entry.json', {'task_id': 'retry'})


class InvocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='native-invocation-');self.root=Path(self.tmp.name)
        import importlib.util
        spec=importlib.util.spec_from_file_location('native_invocation_fixture',Path(__file__).resolve().parents[1]/'tests/support/native_invocation_fixture.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        self.selection=helper.prepare(self.root,'task','request','project',(worker.dt.datetime.now(worker.dt.timezone.utc)+worker.dt.timedelta(minutes=5)).isoformat())
        self.path='.runtime/invocation.json';self.digest=self.selection['artifact_ref']['artifact_id'].split(':',1)[1]
    def tearDown(self):self.tmp.cleanup()
    def test_pending_entry_reservation_cannot_start_or_replay_the_worker(self):
        pending = self.root / '.night/run.invocation-entry.json.partial'
        pending.write_bytes(b'{')
        with mock.patch.object(worker, 'run_native', side_effect=AssertionError('must not call worker')):
            with self.assertRaises(FileExistsError):
                worker.run_bound(self.root, self.path, self.digest)
        self.assertEqual(pending.read_bytes(), b'{')
        self.assertFalse((self.root / '.night/run.invocation-entry.json').exists())
        self.assertFalse((self.root / '.night/fake-worker-started').exists())
    def test_bound_entry_selects_limits_and_cannot_replay(self):
        with mock.patch.object(worker,'run_native',return_value={'stop_reason':None}) as run:
            result=worker.run_bound(self.root,self.path,self.digest)
            self.assertTrue(result['invocation_binding_verified']);self.assertFalse(result['worker_qualification_complete'])
            args,prompt=run.call_args.args
            self.assertEqual(args.command_timeout,30);self.assertEqual(args.max_tool_calls,8);self.assertEqual(args.base_url,'http://127.0.0.1:9');self.assertIn('Disposable',prompt)
            with self.assertRaises(FileExistsError):worker.run_bound(self.root,self.path,self.digest)
            self.assertEqual(run.call_count,1)
    def test_contract_each_input_and_source_drift_never_call_worker(self):
        for path in [self.path,'.night/prompt.txt','.runtime/preflight.json','.runtime/source_manifest.json','.runtime/fake-codex','.runtime/offdesk_native_worker.py','test_invocation_fixture.py']:
            original=(self.root/path).read_bytes()
            try:
                (self.root/path).write_bytes(original+b'\nchanged')
                with mock.patch.object(worker,'run_native',side_effect=AssertionError('must not execute')):
                    with self.assertRaises(ValueError):worker.run_bound(self.root,self.path,self.digest)
                self.assertFalse((self.root/'.night/run.invocation-entry.json').exists())
            finally:(self.root/path).write_bytes(original)
    def test_invalid_duplicate_and_linked_contracts_fail_before_entry(self):
        raw=(self.root/self.path).read_bytes()
        for changes in [{'max_tool_calls':True},{'deadline':'2000-01-01T00:00:00Z'},{'unknown':True},{'root':{}}]:
            data=json.loads(raw);data.update(changes)
            with self.assertRaises(ValueError):worker.validate_invocation(self.root,data)
        with self.assertRaises(ValueError):worker.invocation_json(b'{"a":1,"a":2}')
        contract=self.root/self.path;contract.unlink();contract.symlink_to(self.root/'.runtime/preflight.json')
        with self.assertRaises(OSError):worker.run_bound(self.root,self.path,self.digest)
    def test_bootstrap_executes_fixture_and_refuses_replay(self):
        result=subprocess.run(self.selection['command'],shell=True,cwd=self.root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertTrue((self.root/'.night/fake-worker-started').exists())
        self.assertEqual(json.loads((self.root/'.night/run/result.json').read_text())['verification_state'],'passed')
        second=subprocess.run(self.selection['command'],shell=True,cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(second.returncode,0)
    def test_bootstrap_rejects_controller_code_substitution_before_execution(self):
        (self.root/'.runtime/offdesk_native_worker.py').write_text('from pathlib import Path\nPath("should-not-execute").touch()\n')
        result=subprocess.run(self.selection['command'],shell=True,cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.root/'should-not-execute').exists())


class EntryPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='native-entry-recheck-');self.root=Path(self.tmp.name)
        import importlib.util
        spec=importlib.util.spec_from_file_location('native_invocation_fixture',Path(__file__).resolve().parents[1]/'tests/support/native_invocation_fixture.py')
        self.helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.helper)
        self.selection=self.helper.prepare(self.root,'task','request','project',(worker.dt.datetime.now(worker.dt.timezone.utc)+worker.dt.timedelta(minutes=5)).isoformat(),'stale')
        self.path='.runtime/invocation.json';self.digest=self.selection['artifact_ref']['artifact_id'].split(':',1)[1]
        self.contract=json.loads((self.root/self.path).read_bytes())
        self.original=(self.root/'.runtime/preflight.json').read_bytes()
    def tearDown(self):self.tmp.cleanup()
    def test_stale_fixed_receipt_remains_held_and_policy_does_not_run_checks_during_validation(self):
        fixed=dict(self.contract);fixed.pop('entry_preflight');fixed['schema']='offdesk_native_invocation.v1'
        with self.assertRaises(ValueError):worker.validate_invocation(self.root,fixed)
        with mock.patch.object(worker,'bounded_command',side_effect=AssertionError('inspection must be inert')):
            worker.validate_invocation(self.root,self.contract)
        self.assertEqual((self.root/'.night/preflight-invocations').read_text().splitlines(),['run'])
        self.assertEqual((self.root/'.runtime/preflight.json').read_bytes(),self.original)
    def test_entry_earns_a_new_counted_receipt_and_reserves_worker_closeout_windows(self):
        with mock.patch.object(worker,'run_native',return_value={'stop_reason':None}) as run:
            result=worker.run_bound(self.root,self.path,self.digest)
            fresh=run.call_args.kwargs['preflight_receipt'];self.assertTrue(fresh['passed']);self.assertEqual(fresh['tests_passed'],1)
            deadline=worker.dt.datetime.fromisoformat(self.contract['deadline']).timestamp()
            self.assertEqual(run.call_args.kwargs['worker_deadline'],deadline-10)
            self.assertEqual(result['entry_preflight_sha256'],worker.sha(self.root/'.night/run.entry-preflight.json'))
            with self.assertRaises((ValueError,OSError)):worker.run_bound(self.root,self.path,self.digest)
            self.assertEqual(run.call_count,1)
        self.assertEqual((self.root/'.runtime/preflight.json').read_bytes(),self.original)
        self.assertEqual(len((self.root/'.night/preflight-invocations').read_text().splitlines()),2)
        self.assertFalse(result['worker_qualification_complete'])
    def test_failed_entry_tests_preserve_evidence_without_calling_model(self):
        (self.root/'.night/fail-entry-preflight').touch()
        with mock.patch.object(worker,'run_native',side_effect=AssertionError('must not call model')):
            result=worker.run_bound(self.root,self.path,self.digest)
        self.assertEqual(result['stop_reason'],'entry_preflight_failed');self.assertFalse(result['provider_called'])
        fresh=json.loads((self.root/'.night/run.entry-preflight.json').read_bytes())
        self.assertFalse(fresh['passed']);self.assertNotEqual(fresh['checks'][0]['exit_code'],0)
        self.assertFalse((self.root/'.night/run.result.json').exists());self.assertFalse((self.root/'.night/fake-worker-started').exists())
        self.assertEqual((self.root/'.runtime/preflight.json').read_bytes(),self.original)
    def test_actual_timed_out_entry_test_never_calls_model(self):
        with tempfile.TemporaryDirectory(prefix='native-entry-timeout-') as td:
            root=Path(td);selected=self.helper.prepare(root,'task','request','project',self.contract['deadline'],'timeout')
            (root/'.night/sleep-entry-preflight').touch()
            digest=selected['artifact_ref']['artifact_id'].split(':',1)[1]
            with mock.patch.object(worker,'run_native',side_effect=AssertionError('must not call model')):
                result=worker.run_bound(root,self.path,digest)
            self.assertEqual(result['stop_reason'],'entry_preflight_failed')
            self.assertTrue(json.loads((root/'.night/run.entry-preflight.json').read_bytes())['checks'][0]['timed_out'])
            self.assertFalse((root/'.night/fake-worker-started').exists())
    def test_insufficient_time_before_checks_or_after_checks_never_starts_model(self):
        short=dict(self.contract);short['deadline']=(worker.dt.datetime.now(worker.dt.timezone.utc)+worker.dt.timedelta(seconds=20)).isoformat()
        with mock.patch.object(worker,'bounded_command',side_effect=AssertionError('must not run checks')):
            with self.assertRaises(ValueError):worker.validate_invocation(self.root,short)
        original=worker.preflight
        now=worker.time.time();deadline=worker.dt.datetime.fromisoformat(self.contract['deadline']).timestamp()
        with mock.patch.object(worker.time,'time',return_value=now) as clock:
            def checked(*args,**kwargs):
                value=original(*args,**kwargs);clock.return_value=deadline-14;return value
            with mock.patch.object(worker,'preflight',side_effect=checked),mock.patch.object(worker,'run_native',side_effect=AssertionError('must not call model')):
                with self.assertRaises(ValueError):worker.run_bound(self.root,self.path,self.digest)
        self.assertTrue(json.loads((self.root/'.night/run.entry-preflight.json').read_bytes())['passed'])
        self.assertFalse((self.root/'.night/fake-worker-started').exists())
    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux owned-child observation')
    def test_sigterm_during_entry_preflight_reaps_check_and_preserves_no_replay_evidence(self):
        import shlex
        (self.root/'.night/sleep-entry-preflight').touch()
        process=subprocess.Popen(shlex.split(self.selection['command']),cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        child=None
        try:
            for _ in range(100):
                if process.poll() is not None: break
                if len((self.root/'.night/preflight-invocations').read_text().splitlines())==2:
                    children=Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
                    if children: child=int(children[0]);break
                time.sleep(0.02)
            self.assertIsNotNone(child)
            process.terminate();process.communicate(timeout=8)
            self.assertNotEqual(process.returncode,0)
            self.assertFalse(Path(f'/proc/{child}').exists())
            self.assertTrue((self.root/'.night/run.invocation-entry.json').exists())
            self.assertTrue((self.root/'.night/run.entry-preflight.json.progress.json').exists())
            self.assertFalse((self.root/'.night/fake-worker-started').exists())
            with self.assertRaises(ValueError):worker.run_bound(self.root,self.path,self.digest)
            self.assertEqual(len((self.root/'.night/preflight-invocations').read_text().splitlines()),2)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.communicate(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=8)
    def test_policy_mutation_unknown_values_and_changed_sources_do_not_inherit_permission(self):
        for patch in [{'timeout_seconds':True},{'timeout_seconds':0},{'closeout_reserve_seconds':4},{'minimum_worker_seconds':86400},{'unknown':True}]:
            changed=json.loads(json.dumps(self.contract));changed['entry_preflight'].update(patch)
            with self.assertRaises(ValueError):worker.validate_invocation(self.root,changed)
        (self.root/'test_invocation_fixture.py').write_text('changed source')
        with mock.patch.object(worker,'bounded_command',side_effect=AssertionError('must not run checks')):
            with self.assertRaises(ValueError):worker.run_bound(self.root,self.path,self.digest)
        self.assertFalse((self.root/'.night/run.invocation-entry.json').exists())


if __name__=='__main__':unittest.main()
