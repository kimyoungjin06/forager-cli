"""Phase transition, source-scope and durable failure regressions."""
import json
import hashlib
from pathlib import Path
import sys
import os
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offdesk_four_phase_run as batch
import offdesk_independent_qualification as driver


class FourPhaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("FORAGER_TEST_TEMP_ROOT", "/var/tmp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".runtime").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/allowed.rs").write_text("original\n")
        (self.root / "owning-plan.md").write_text("do not change\n")
        rows = batch.inventory(self.root)
        batch.worker.write_json(self.root / ".runtime/source_manifest.json", {"files": rows})
        source = batch.worker.source_identity(self.root, {"files": rows})
        self.selection = {
            "root": str(self.root), "run_id": "owned-test", "task_id": "task",
            "request_id": "request", "stop_epoch": 12000, "feature_stop_epoch": 8400,
            "initial_source_sha256": source, "max_total_tool_calls": 128,
            "orchestrator_sha256": batch.worker.sha(Path(batch.__file__)),
            "controller_sha256": batch.worker.sha(Path(batch.worker.__file__)),
            "native_runtime_hashes": {},
            "phases": [{"id": f"FG-N18-{i}", "max_seconds": 100,
                        "max_tool_calls": 32, "write_paths": ["src/"], "checks": [],
                        "summary_path": f".night/summary-{i}.md",
                        "checkpoint": i if i in (1, 3) else None} for i in range(1, 5)]}
        self.called = []

    def backend(self, phase, deadline):
        self.called.append(phase["id"])
        summary = self.root / phase["summary_path"]
        summary.parent.mkdir(exist_ok=True)
        summary.write_text("Actual changes and evidence; pending review.\n")
        return {"exit_code": 0, "stop_reason": None, "turn_completed": True,
                "verification_state": "passed", "tool_calls": 1}

    def run_batch(self, backend=None, council=None, clock=lambda: 1000, selection_input=None):
        self.run = batch.FourPhaseRun(self.root, self.selection,
                                     self.root / ".night/run", clock=clock,
                                     backend=backend or self.backend,
                                     council=council or (lambda phase, record: True),
                                     selection_input=selection_input)
        return self.run.run()

    def test_four_gates_run_in_order_without_acceptance(self):
        result = self.run_batch()
        self.assertEqual(self.called, [f"FG-N18-{i}" for i in range(1, 5)])
        self.assertEqual(result["completed_phase_gates"], self.called)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["canonical_acceptance"])
        self.assertEqual(result["review_state"], "pending_user_review")
        self.assertTrue((self.run.output / "HANDOFF.md").is_file())

    def unicode_selection_capture(self, ensure_ascii):
        self.selection['user_note'] = '진행하자. 원본 보존'
        path = self.root / '.runtime/selection.json'
        raw = (json.dumps(self.selection, indent=2, ensure_ascii=ensure_ascii) + '\n').encode()
        path.write_bytes(raw)
        selected, source = batch.read_selection_input(path)
        self.assertEqual(selected, self.selection)
        result = self.run_batch(selection_input=source)
        self.assertEqual(result['status'], 'completed')
        receipt = result['executed_selection']
        self.assertTrue(receipt['verbatim'])
        copied = (self.root / receipt['path']).read_bytes()
        self.assertEqual(copied, raw)
        self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(json.loads(copied), self.selection)
        readable_variant = (json.dumps(self.selection, indent=2, ensure_ascii=not ensure_ascii) + '\n').encode()
        self.assertNotEqual(copied, readable_variant)
        self.assertFalse(result['canonical_acceptance'])

    def test_ascii_escaped_authority_is_published_by_exact_byte_copy(self):
        self.unicode_selection_capture(True)

    def test_utf8_authority_is_published_by_exact_byte_copy(self):
        self.unicode_selection_capture(False)

    def test_changed_parsed_selection_stops_before_backend(self):
        path = self.root / '.runtime/selection.json'
        path.write_text(json.dumps(self.selection))
        _, source = batch.read_selection_input(path)
        self.selection['unexpected_change'] = 'not in executed bytes'
        result = self.run_batch(selection_input=source)
        self.assertEqual(result['failure'], 'orchestration_failed')
        self.assertIn('match its exact input', result['error']['message'])
        self.assertEqual(self.called, [])
        self.assertIsNone(result['executed_selection'])

    def test_selection_links_and_outside_root_files_are_not_admitted(self):
        path = self.root / '.runtime/selection.json'
        path.write_text(json.dumps(self.selection))
        link = self.root / '.runtime/link.json'
        link.symlink_to(path)
        with self.assertRaises((OSError, ValueError)):
            batch.read_selection_input(link)
        with tempfile.TemporaryDirectory(dir=os.environ.get('FORAGER_TEST_TEMP_ROOT', '/var/tmp')) as other:
            outside = Path(other) / 'selection.json'
            outside.write_text(json.dumps(self.selection))
            with self.assertRaises(ValueError):
                batch.read_selection_input(outside)
        self.assertEqual(self.called, [])

    def test_invalid_selected_verification_commands_stop_before_preflight_or_model(self):
        run = batch.FourPhaseRun(self.root, self.selection, self.root / '.night/run')
        phase = dict(self.selection['phases'][0], checks=[['python3', '--version'], []])
        with mock.patch.object(batch.worker, 'preflight') as preflight, mock.patch.object(batch.worker, 'supervise') as native:
            with self.assertRaises(ValueError):
                run.native_phase(phase, 10000)
            preflight.assert_not_called()
            native.assert_not_called()

    def test_read_only_task_context_indexes_source_without_granting_write_scope(self):
        output = self.root / '.night/review'
        output.mkdir(parents=True)
        batch.worker.write_json(output/'BASELINE.json', batch.inventory(self.root))
        phase = dict(self.selection['phases'][0], write_paths=[], read_paths=['src/allowed.rs'], focus_pattern='original')
        context, progress, _ = batch.prepare_task_context(self.root, output, phase)
        packet = json.loads(context.read_text())
        self.assertEqual([row['path'] for row in packet['index']], ['src/allowed.rs'])
        self.assertEqual(packet['read_paths'], ['src/allowed.rs'])
        self.assertEqual(packet['write_paths'], [])
        self.assertFalse(packet['claims_verified'])
        self.assertEqual((self.root/'src/allowed.rs').read_text(), 'original\n')

    def test_completed_and_failed_handoffs_use_consumer_evidence_fields(self):
        result = self.run_batch()
        evidence = result["evidence"][0]
        self.assertEqual(evidence["kind"], "check")
        self.assertEqual(evidence["sha256"], batch.worker.sha(self.root / evidence["path"]))
        self.assertNotIn("reference", evidence)
        failed = self.run.closeout("test_failure")
        self.assertEqual(failed["evidence"][0]["sha256"],
                         batch.worker.sha(self.root / failed["evidence"][0]["path"]))

    def test_focused_context_is_bounded_and_progress_is_identity_bound(self):
        output = self.root / ".night/run"
        output.mkdir(parents=True)
        batch.worker.write_json(output / "BASELINE.json", batch.inventory(self.root))
        (self.root / "src/allowed.rs").write_text("fn owning_plan() {}\n" * 20000)
        context, progress, binding = batch.prepare_task_context(self.root, output, self.selection["phases"][0])
        packet = json.loads(context.read_text())
        self.assertLessEqual(len(json.dumps(packet)), 6000)
        self.assertEqual(packet["index"][0]["lines"], 20000)
        observation = batch.observe_task_progress(self.root, progress, binding)
        self.assertEqual(observation["status"], "bound_model_report")
        self.assertFalse(observation["claims_verified"])
        data = json.loads(progress.read_text())
        data["phase"] = "wrong-phase"
        batch.worker.write_json(progress, data)
        self.assertEqual(batch.observe_task_progress(self.root, progress, binding)["status"], "invalid_progress")
        progress.unlink()
        progress.symlink_to(context)
        self.assertEqual(batch.observe_task_progress(self.root, progress, binding)["status"], "unavailable_or_unsafe")

    def test_context_reinitialization_preserves_saved_progress_and_partial_context(self):
        output = self.root / '.night/run'
        output.mkdir(parents=True)
        batch.worker.write_json(output / 'BASELINE.json', batch.inventory(self.root))
        context, progress, binding = batch.prepare_task_context(self.root, output, self.selection['phases'][0])
        batch.worker.save_progress(self.root, context, 'one edit identified', 'verify that edit', [], ['src/allowed.rs'])
        before = {path: path.read_bytes() for path in (context, progress)}
        with self.assertRaisesRegex(ValueError, 'never reinitialized'):
            batch.prepare_task_context(self.root, output, self.selection['phases'][0])
        self.assertEqual(before, {path: path.read_bytes() for path in (context, progress)})
        progress.unlink()
        with self.assertRaises(ValueError):
            batch.prepare_task_context(self.root, output, self.selection['phases'][0])
        self.assertEqual(context.read_bytes(), before[context])
        self.assertFalse(progress.exists())

    def test_run_bound_progress_round_trip_uses_same_observer_and_recovery_schema(self):
        output = self.root / '.night/run'
        output.mkdir(parents=True)
        batch.worker.write_json(output / 'BASELINE.json', batch.inventory(self.root))
        run_binding = {key: self.selection[key] for key in ('run_id', 'task_id', 'request_id')}
        context, progress, binding = batch.prepare_task_context(self.root, output, self.selection['phases'][0], run_binding)
        context_sha = batch.worker.sha(context)
        receipt = batch.worker.save_progress(self.root, context, 'reuse one helper', 'run the next check',
            ['check not yet independently verified'], ['src/allowed.rs'], context_sha)
        observed = batch.observe_task_progress(self.root, progress, binding)
        recovered = batch.worker.recovery_context(self.root, context, context_sha, receipt['sha256'])
        self.assertEqual(observed['status'], 'bound_model_report')
        self.assertEqual(observed['content'], recovered['progress'])
        self.assertEqual(recovered['binding']['run_binding'], run_binding)
        self.assertFalse(recovered['claims_verified'])
        self.assertFalse(recovered['can_resume_worker'])

    def test_baseline_scope_rejects_parent_absolute_and_nested_paths(self):
        for name in [".runtime/..", "/var/tmp/outside", ".runtime/nested/name"]:
            with self.subTest(name=name):
                selection = dict(self.selection, baseline_relative=name)
                with self.assertRaises(ValueError):
                    batch.FourPhaseRun(self.root, selection, self.root / ".night/run")
                self.assertEqual(self.called, [])
                self.assertFalse((self.root / ".night/run").exists())
        selected = dict(self.selection, baseline_relative=".runtime/valid-baseline")
        run = batch.FourPhaseRun(self.root, selected, self.root / ".night/run")
        self.assertEqual(run.baseline, self.root / ".runtime/valid-baseline")

    def test_nonzero_worker_exit_stops_following_phases(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result["exit_code"] = 1
            return result
        result = self.run_batch(backend)
        self.assertEqual(self.called, ["FG-N18-1"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completed_phase_gates"], [])

    def test_exceeded_budget_preserves_observed_calls_and_failed_phase(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result.update(tool_calls=phase["max_tool_calls"] + 1,
                          stop_reason="tool_call_budget_reached", exit_code=-15)
            return result
        result = self.run_batch(backend)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["tool_calls"], 33)
        self.assertEqual(result["records"][0]["result"]["stop_reason"], "tool_call_budget_reached")
        self.assertEqual(self.called, ["FG-N18-1"])

    def test_failed_verification_and_pending_verification_are_not_success(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result["verification_state"] = "pending"
            return result
        result = self.run_batch(backend)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.called), 1)

    def classified_backend(self, category, phase_id=None):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result.update(verification_state='failed', qualification_failure={
                'schema': 'offdesk_native_command_failure.v1',
                'phase': phase_id or phase['id'], 'category': category,
                'artifact_path': '.night/run/native-command-failure.json',
                'canonical_acceptance': False, 'automatic_retry_authorized': False,
                'selected_checks': [{'evidence': {'reported_counts':
                    {'tests_run': 97, 'passed': 37, 'failed': 60}}}]})
            return result
        return backend

    def test_selected_check_failure_survives_phase_closeout_without_orchestration_exception(self):
        result = self.run_batch(self.classified_backend('verification_failed'))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failure'], 'verification_failed:FG-N18-1')
        self.assertIsNone(result['error'])
        self.assertEqual(result['failure_detail']['selected_checks'][0]['evidence']['reported_counts']['failed'], 60)
        self.assertFalse(result['canonical_acceptance'])
        self.assertEqual(result['completed_phase_gates'], [])
        self.assertEqual(self.called, ['FG-N18-1'])
        self.assertIn('Failure evidence: .night/run/native-command-failure.json',
                      (self.run.output / 'HANDOFF.md').read_text())

    def test_execution_failure_keeps_its_distinct_phase_category(self):
        result = self.run_batch(self.classified_backend('execution_failed'))
        self.assertEqual(result['failure'], 'execution_failed:FG-N18-1')
        self.assertEqual(self.called, ['FG-N18-1'])

    def test_stale_phase_diagnostic_does_not_override_generic_failure(self):
        result = self.run_batch(self.classified_backend('verification_failed', 'OTHER-PHASE'))
        self.assertEqual(result['failure'], 'phase_gate_failed:FG-N18-1')
        self.assertIsNone(result['failure_detail'])

    def test_guarded_selected_failure_is_explicit_even_when_native_turn_is_interrupted(self):
        def backend(phase, deadline):
            result = self.classified_backend('verification_failed')(phase, deadline)
            result.update(exit_code=-15, stop_reason='selected_check_failed', turn_completed=False)
            result['qualification_failure'].update(guard_trigger='selected_check_failed',
                failed_commands=[{'selected_check': True, 'exit_code': 1}],
                primary_native_stop={'stop_reason': 'selected_check_failed', 'exit_code': -15, 'turn_completed': False})
            return result
        result = self.run_batch(backend)
        self.assertEqual(result['failure'], 'verification_failed:FG-N18-1')
        self.assertEqual(result['failure_detail']['primary_native_stop']['exit_code'], -15)
        self.assertEqual(self.called, ['FG-N18-1'])
        self.assertFalse(result['canonical_acceptance'])

    def test_context_stop_retains_secondary_failures_without_becoming_a_check_stop(self):
        def backend(phase, deadline):
            result = self.classified_backend('execution_failed')(phase, deadline)
            result.update(exit_code=-15, stop_reason='context_warning_budget_reached', turn_completed=False)
            result['qualification_failure'].update(guard_trigger='selected_check_failed',
                failed_commands=[{'selected_check': True, 'exit_code': 1}],
                classification_role='secondary_to_native_stop',
                primary_native_stop={'stop_reason': 'context_warning_budget_reached', 'exit_code': -15})
            return result
        result = self.run_batch(backend)
        self.assertEqual(result['failure'], 'phase_gate_failed:FG-N18-1')
        self.assertEqual(result['failure_detail']['category'], 'execution_failed')
        self.assertEqual(result['failure_detail']['primary_native_stop']['stop_reason'], 'context_warning_budget_reached')
        self.assertEqual(self.called, ['FG-N18-1'])

    def test_stop_string_without_matching_host_guard_evidence_does_not_promote_category(self):
        def backend(phase, deadline):
            result = self.classified_backend('verification_failed')(phase, deadline)
            result.update(exit_code=-15, stop_reason='selected_check_failed', turn_completed=False)
            return result
        result = self.run_batch(backend)
        self.assertEqual(result['failure'], 'phase_gate_failed:FG-N18-1')
        self.assertFalse(result['canonical_acceptance'])

    def test_advisory_policy_stop_reaches_handoff_without_claiming_exhaustion(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result.update(exit_code=-15, stop_reason='context_warning_budget_reached',
                turn_completed=False, verification_state='failed', native_stop_observation={
                    'schema': 'offdesk_native_stop_observation.v1', 'phase': phase['id'],
                    'artifact_path': '.night/run/native-stop.json', 'canonical_acceptance': False,
                    'hard_context_exhaustion_confirmed': False,
                    'confirmed_context_transition': False})
            return result
        result = self.run_batch(backend)
        self.assertEqual(result['failure'], 'phase_gate_failed:FG-N18-1')
        self.assertFalse(result['failure_detail']['hard_context_exhaustion_confirmed'])
        self.assertIn('native-stop.json', (self.run.output / 'HANDOFF.md').read_text())

    def test_incomplete_native_turn_is_not_phase_completion(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result["turn_completed"] = False
            return result
        self.assertEqual(self.run_batch(backend)["status"], "failed")

    def test_outside_scope_edit_preserves_failure_and_does_not_adopt(self):
        original = (self.root / ".runtime/source_manifest.json").read_bytes()
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            (self.root / "owning-plan.md").write_text("unexpected change\n")
            return result
        result = self.run_batch(backend)
        self.assertEqual(result["status"], "failed")
        self.assertIn("outside phase scope", result["error"]["message"])
        self.assertEqual((self.root / ".runtime/source_manifest.json").read_bytes(), original)
        self.assertIn("unexpected change", (self.run.output / "candidate.patch").read_text())

    def test_outside_scope_addition_is_detected(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            (self.root / "new-plan.md").write_text("unexpected\n")
            return result
        self.assertEqual(self.run_batch(backend)["status"], "failed")

    def test_outside_scope_deletion_is_detected(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            (self.root / "owning-plan.md").unlink()
            return result
        self.assertEqual(self.run_batch(backend)["status"], "failed")

    def test_allowed_addition_is_bound_for_next_preflight(self):
        def backend(phase, deadline):
            if phase["id"] == "FG-N18-1":
                (self.root / "src/added.rs").write_text("new scoped candidate\n")
            else:
                manifest = json.loads((self.root / ".runtime/source_manifest.json").read_text())
                self.assertIn("src/added.rs", manifest["files"])
                batch.worker.source_identity(self.root, manifest)
            return self.backend(phase, deadline)
        self.assertEqual(self.run_batch(backend)["status"], "completed")

    def test_missing_report_stops_transition(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            (self.root / phase["summary_path"]).unlink()
            return result
        result = self.run_batch(backend)
        self.assertIn("phase_summary_missing", result["failure"])
        self.assertEqual(len(self.called), 1)

    def test_council_stop_does_not_launch_next_phase(self):
        result = self.run_batch(council=lambda phase, record: False)
        self.assertEqual(self.called, ["FG-N18-1"])
        self.assertIn("council_requires_followup", result["failure"])

    def test_exception_has_durable_closeout(self):
        def backend(phase, deadline):
            raise OSError("owned launch failure")
        result = self.run_batch(backend)
        self.assertEqual(result["error"]["type"], "OSError")
        self.assertTrue((self.run.output / "RUN_RESULT.json").is_file())

    def test_host_receipt_exception_preserves_one_actual_command(self):
        def backend(phase, deadline):
            self.called.append(phase['id'])
            self.run.observe_native_calls('context-1', 1)
            raise ValueError('First model progress writer receipt required')
        result = self.run_batch(backend)
        self.assertEqual(result['failure'], 'orchestration_failed')
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(result['native_tool_calls_observed'], 1)
        self.assertEqual(self.called, ['FG-N18-1'])
        saved = json.loads((self.run.output / 'RUN_RESULT.json').read_text())
        ledger = json.loads((self.run.output / 'NATIVE_TOOL_ACCOUNTING.json').read_text())
        self.assertEqual(saved['tool_calls'], ledger['native_tool_calls_observed'])
        self.assertFalse(result['canonical_acceptance'])

    def test_context_snapshots_and_returned_totals_do_not_double_charge(self):
        def backend(phase, deadline):
            self.run.observe_native_calls('context-1', 1)
            self.run.observe_native_calls('context-1', 1)
            self.run.observe_native_calls('context-2', 1)
            self.run.observe_native_calls('context-2', 2)
            result = self.backend(phase, deadline)
            result['tool_calls'] = 3
            return result
        result = self.run_batch(backend)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['tool_calls'], 12)
        self.assertEqual(result['native_tool_calls_observed'], 12)

    def test_underreported_result_cannot_erase_observations_or_continue(self):
        def backend(phase, deadline):
            self.run.observe_native_calls('context-1', 2)
            return self.backend(phase, deadline)
        result = self.run_batch(backend)
        self.assertEqual(result['tool_calls'], 2)
        self.assertEqual(self.called, ['FG-N18-1'])
        self.assertTrue(result['failure'].startswith('invalid_or_exceeded_phase_tool_budget'))

    def test_decreasing_and_noninteger_observations_fail_without_losing_charged_calls(self):
        def backend(phase, deadline):
            self.run.observe_native_calls('context-1', 2)
            for value in (1, -1, True, '3'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.run.observe_native_calls('context-1', value)
            return dict(self.backend(phase, deadline), tool_calls=2)
        result = self.run_batch(backend)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['tool_calls'], 8)

    def test_supervision_exception_preserves_started_in_flight_command(self):
        def backend(phase, deadline):
            guard = batch.worker.ProgressGuard()
            def supervise(*args, **kwargs):
                guard.feed({'type': 'item.started', 'item': {'id': 'in-flight',
                            'type': 'command_execution', 'command': 'selected helper'}})
                raise RuntimeError('native supervision interrupted before return')
            with mock.patch.object(batch.worker, 'supervise', side_effect=supervise):
                return driver.supervise_counted(self.run, 'context-1', batch.worker,
                    [], 'fixture', self.root, self.run.output / 'context-1', deadline, guard, {})
        result = self.run_batch(backend)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['tool_calls'], 1)
        self.assertEqual(result['native_tool_calls_observed'], 1)

    def test_interruption_has_durable_closeout(self):
        def backend(phase, deadline):
            raise KeyboardInterrupt()
        self.assertEqual(self.run_batch(backend)["failure"], "interrupted")

    def test_deadline_expiry_starts_no_phase(self):
        result = self.run_batch(clock=lambda: 8390)
        self.assertEqual(self.called, [])
        self.assertEqual(result["failure"], "final_closeout_window")

    def test_last_phase_can_use_reserved_integration_hour(self):
        clocks = {"now": 1000}
        def backend(phase, deadline):
            if phase["id"] == "FG-N18-3":
                clocks["now"] = 8450
            if phase["id"] == "FG-N18-4":
                self.assertGreater(deadline, self.selection["feature_stop_epoch"])
                self.assertLessEqual(deadline, self.selection["stop_epoch"] - 900)
            return self.backend(phase, deadline)
        self.assertEqual(self.run_batch(backend, clock=lambda: clocks["now"])["status"], "completed")

    def test_total_tool_budget_stops_before_another_worker(self):
        self.selection["max_total_tool_calls"] = 1
        result = self.run_batch()
        self.assertEqual(len(self.called), 1)
        self.assertEqual(result["failure"], "total_tool_budget_exhausted")

    def test_changed_initial_source_is_refused(self):
        (self.root / "src/allowed.rs").write_text("unselected initial drift\n")
        result = self.run_batch()
        self.assertEqual(self.called, [])
        self.assertEqual(result["status"], "failed")

    def test_replay_cannot_overwrite_existing_run(self):
        self.run_batch()
        with self.assertRaises(ValueError):
            self.run.run()

    def test_runtime_identity_change_after_worker_stops_transition(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            self.selection["controller_sha256"] = "unselected"
            return result
        result = self.run_batch(backend)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.called, ["FG-N18-1"])
        self.assertEqual(result['tool_calls'], 1)

    def test_invalid_budget_count_stops_transition(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result["tool_calls"] = -1
            return result
        self.assertEqual(self.run_batch(backend)["status"], "failed")

    def test_wrong_run_council_response_is_refused(self):
        def council(phase, record):
            response = self.run.output / "council-1-response.json"
            batch.worker.write_json(response, {"run_id": "other-run", "ready_to_continue": True})
            return self.run.wait_council(phase, record)
        self.assertIn("council_requires_followup", self.run_batch(council=council)["failure"])

    def test_missing_required_launcher_is_not_admitted(self):
        self.selection["require_launcher_receipt"] = True
        self.run = batch.FourPhaseRun(self.root, self.selection, self.root / ".night/run")
        original = json.loads((self.root / ".runtime/source_manifest.json").read_text())
        with self.assertRaises(ValueError):
            batch.admit_launcher_files(self.root, batch.inventory(self.root), original, self.selection)

    def test_arbitrary_file_under_launcher_prefix_is_not_admitted(self):
        original = json.loads((self.root / ".runtime/source_manifest.json").read_text())
        directory = self.root / ".forager-read-policy-Ab12"
        directory.mkdir()
        (directory / "Cargo.toml").write_text("unselected build input")
        with self.assertRaises(ValueError):
            batch.admit_launcher_files(self.root, batch.inventory(self.root), original, self.selection)

    def test_exact_launcher_evidence_is_kept_in_strict_inventory(self):
        original = json.loads((self.root / ".runtime/source_manifest.json").read_text())
        directory = self.root / ".forager-read-policy-Ab12"
        directory.mkdir()
        policy = {"schema": "fixture-policy"}
        digest = hashlib.sha256(json.dumps(policy, separators=(",", ":")).encode()).hexdigest()
        self.selection["read_policy_sha256"] = digest
        spec = {"schema": "offdesk_tmux_read_policy_spec.v1", "workdir": str(self.root),
                "arguments": [str(self.root / ".runtime/offdesk_four_phase_run.py")],
                "nonce": "fixture", "policy": policy}
        batch.worker.write_json(directory / "spec.json", spec)
        receipt = {"schema": "offdesk_tmux_read_policy_receipt.v1", "workdir": str(self.root),
                   "nonce": "fixture", "launch_spec_sha256": batch.worker.sha(directory / "spec.json"),
                   "evidence": {"policy_sha256": digest}}
        batch.worker.write_json(directory / "receipt.json", receipt)
        (directory / "acknowledged").write_text("fixture")
        rows = batch.inventory(self.root)
        admitted = batch.admit_launcher_files(self.root, rows, original, self.selection)
        self.assertEqual(len(admitted), 3)
        batch.refresh_manifest(self.root, rows)
        manifest = json.loads((self.root / ".runtime/source_manifest.json").read_text())
        self.assertIn(".forager-read-policy-Ab12/spec.json", manifest["files"])

    def test_large_check_output_is_kept_out_of_small_result_sidecar(self):
        def backend(phase, deadline):
            result = self.backend(phase, deadline)
            result["verification_checks"] = [{"argv": ["cargo", "test"], "exit_code": 0,
                                              "stdout": "a" * 100000, "stderr": ""}]
            return result
        result = self.run_batch(backend)
        self.assertEqual(result["status"], "completed")
        self.assertLess((self.run.output / "RUN_RESULT.json").stat().st_size, 65536)
        self.assertGreater((self.run.output / "FG-N18-1-gate.json").stat().st_size, 100000)

    def test_empty_result_directory_prepared_by_forager_is_allowed(self):
        (self.root / ".night/run").mkdir(parents=True)
        self.assertEqual(self.run_batch()["status"], "completed")


if __name__ == "__main__":
    unittest.main()
