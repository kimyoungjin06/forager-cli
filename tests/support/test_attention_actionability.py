"""Stateful attention migration, real failure reminders and exact read-only lookup."""
import argparse
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import offdesk_remote_operator_telegram as operator
from telegram_operator.attention_classification import attention_category
from telegram_operator.command_envelope import classify_command
from telegram_operator.dispatch import inspectable_tasks_from_surface, cancellable_tasks_from_surface
from telegram_operator.notifier import (attention_summary, build_attention_notification,
    commit_attention_notification, render_attention_message, attention_event_key)
from telegram_operator.notification_policy import NotificationCycle, NotificationPriority
from telegram_operator.rendering import mobile_card_contract
from telegram_operator.routing import parse_remote_command

START = '2026-11-01T00:00:00Z'
LATER = '2026-11-08T00:00:00Z'


def task(project='nano', request='run', task_id='same-task', status='completed', version='v1'):
    return {'project_key': project, 'task_items': [{'task_id': task_id, 'request_id': request,
        'status': status, 'requires_operator_review': True, 'attention_version': version,
        'next_safe_action_kind': 'review_required' if status == 'completed' else 'recovery_required',
        'command': 'forager ondesk plan-inspect /tmp/TODO.md --project-key ' + project}]}


def surface(*projects):
    return {'projects': list(projects)}


def notification(view, state, at=START):
    with patch('telegram_operator.notifier.utc_now', return_value=at):
        return build_attention_notification(view, state, reminder_sec=21600)


class ReviewNotifications(unittest.TestCase):
    def test_completed_review_stays_visible_after_a_week_without_repeat(self):
        view, state = surface(task()), {}
        first = notification(view, state)
        key = first['fresh_items'][0]['key']
        commit_attention_notification(state, first, delivered_keys=(key,))
        self.assertEqual(notification(view, state, LATER)['fresh_items'], [])
        self.assertEqual(attention_summary(view)['review_waiting_count'], 1)
        self.assertEqual(view['projects'][0]['task_items'][0]['status'], 'completed')

    def test_failure_reminder_still_fires_and_failed_send_does_not_consume_it(self):
        view, state = surface(task(status='failed')), {}
        first = notification(view, state)
        key = first['fresh_items'][0]['key']
        commit_attention_notification(state, first, delivered_keys=(key,))
        before = copy.deepcopy(state)
        due = notification(view, state, '2026-11-01T06:00:00Z')
        self.assertEqual(len(due['fresh_items']), 1)
        commit_attention_notification(state, due, delivered_keys=())
        self.assertEqual(state, before)
        self.assertEqual(len(notification(view, state, LATER)['fresh_items']), 1)

    def test_review_version_change_and_resolved_then_reopened_are_fresh(self):
        state = {}
        first = notification(surface(task()), state)
        key = first['fresh_items'][0]['key']
        commit_attention_notification(state, first, delivered_keys=(key,))
        changed = notification(surface(task(version='v2')), state, LATER)
        self.assertEqual(len(changed['fresh_items']), 1)
        self.assertEqual(changed['staged_notified_by_key'][key]['first_notified_at'], START)
        resolved = notification(surface(), state, LATER)
        commit_attention_notification(state, resolved, delivered_keys=())
        self.assertEqual(state['attention_notified_by_key'], {})
        self.assertEqual(len(notification(surface(task()), state, LATER)['fresh_items']), 1)

    def test_legacy_delivered_record_migrates_without_a_new_send(self):
        record = {'version': 'v1', 'first_notified_at': START, 'last_notified_at': START}
        state = {'attention_notified_by_key': {'task:same-task': dict(record)}}
        before = copy.deepcopy(state)
        staged = notification(surface(task()), state, LATER)
        self.assertEqual(staged['fresh_items'], [])
        self.assertEqual(state, before)
        key = attention_summary(surface(task()))['top']['key']
        commit_attention_notification(state, staged, delivered_keys=())
        self.assertEqual(state['attention_notified_by_key'], {key: record})
        self.assertEqual(notification(surface(task()), state, LATER)['fresh_items'], [])

    def test_changed_legacy_version_never_commits_an_unsent_new_version(self):
        record = {'version': 'old', 'first_notified_at': START, 'last_notified_at': START}
        state = {'attention_notified_by_key': {'task:same-task': dict(record)}}
        staged = notification(surface(task()), state, LATER)
        self.assertEqual(len(staged['fresh_items']), 1)
        key = staged['fresh_items'][0]['key']
        commit_attention_notification(state, staged, delivered_keys=())
        self.assertEqual(state['attention_notified_by_key'][key], record)
        self.assertEqual(len(notification(surface(task()), state, LATER)['fresh_items']), 1)

    def test_project_and_request_ids_keep_distinct_targets_with_same_task_id(self):
        view = surface(task('nano', 'run-1'), task('overton', 'run-1'), task('nano', 'run-2'))
        state = {'attention_notified_by_key': {'task:same-task': {
            'version': 'v1', 'first_notified_at': START, 'last_notified_at': START}}}
        staged = notification(view, state, LATER)
        self.assertEqual(len(staged['fresh_items']), 3)
        self.assertEqual(len({item['key'] for item in staged['fresh_items']}), 3)
        self.assertIn('task:same-task', staged['staged_notified_by_key'])

    def test_exact_duplicate_identity_is_one_target(self):
        view = surface(task(), task())
        self.assertEqual(attention_summary(view)['task_count'], 1)

    def test_legacy_uncertain_delivery_still_holds_after_key_change(self):
        item = attention_summary(surface(task()))['top']
        legacy_event = 'attention:task:same-task'
        digest = hashlib.sha256(legacy_event.encode()).hexdigest()
        holds = {digest: {'status': 'delivery_unconfirmed'}}
        state = {'notification_unconfirmed_events': holds}
        self.assertEqual(attention_event_key(item, state), legacy_event)
        before = copy.deepcopy(holds)
        cycle = NotificationCycle(lambda _c: self.fail('uncertain delivery replayed'), unconfirmed_events=holds)
        args = argparse.Namespace(attention_notify=True, attention_reminder_sec=21600, profile='default', forager_bin='unused')
        with patch.object(operator, 'export_workstation_surface', return_value=surface(task())):
            operator.scan_and_notify_attention(args, {'target_chat_id': 'fixture'}, state, cycle=cycle)
        self.assertEqual(cycle.deliver()['status'], 'delivery_unconfirmed_hold')
        self.assertEqual(holds, before)

    def test_unknown_hold_ledger_is_not_treated_as_empty(self):
        with self.assertRaises(ValueError):
            attention_event_key(attention_summary(surface(task()))['top'], {'notification_unconfirmed_events': []})

    def test_distinct_review_and_execution_titles_and_mobile_budget(self):
        for status, expected in [('completed', '결과 검토 대기'), ('failed', '실행 복구 확인')]:
            item = attention_summary(surface(task(status=status)))['top']
            key = item['key']
            message = render_attention_message(profile='default', generated_at=LATER, items=[item],
                notified={key: {'first_notified_at': START}})
            self.assertIn(expected, message)
            self.assertIn('/tasks nano same-task run', message)
            self.assertIn('7일 전', message)
            self.assertEqual(mobile_card_contract(message)['warnings'], [])

    def test_unknown_work_and_decisions_keep_periodic_policy(self):
        self.assertEqual(attention_category({'kind': 'decision'})[0], 'decision')
        self.assertEqual(attention_category({'kind': 'task', 'status': 'unknown'})[0], 'unknown')

    def test_routine_completed_review_does_not_preempt_a_decision_or_failure(self):
        review = attention_summary(surface(task()))['top']
        failed = attention_summary(surface(task(status='failed')))['top']
        self.assertGreater(operator._attention_priority(review), NotificationPriority.DECISION)
        self.assertLess(operator._attention_priority(failed), NotificationPriority.DECISION)

    def test_html_and_long_target_card_stays_bounded_and_does_not_execute_a_target(self):
        item = attention_summary(surface(task(project='<>&' * 30, task_id='x' * 100)))['top']
        message = render_attention_message(profile='default', generated_at=LATER, items=[item])
        self.assertEqual(mobile_card_contract(message)['warnings'], [])


class ExactInspection(unittest.TestCase):
    def render(self, command, view):
        parsed = parse_remote_command(command)
        self.assertTrue(parsed['supported'])
        args = argparse.Namespace(forager_bin='fixture', profile='default')
        before = copy.deepcopy(view)
        with patch.object(operator, 'export_workstation_surface', return_value=view):
            result = operator.render_dispatch_command(args, {}, parsed, {'generated_at': START})
        self.assertEqual(view, before)
        self.assertIsNone(result.get('pending_dispatch_confirmation'))
        self.assertEqual(result['mobile_card_contract']['warnings'], [])
        return result['message_preview']

    def test_completed_review_is_visible_and_never_offered_cancel(self):
        view = surface(task())
        self.assertEqual(cancellable_tasks_from_surface(view), [])
        self.assertEqual(len(inspectable_tasks_from_surface(view)), 1)
        message = self.render('/tasks nano same-task run', view)
        self.assertIn('실행 완료', message)
        self.assertIn('forager ondesk plan-inspect', message)
        self.assertNotIn('/cancel-task', message)
        self.assertNotIn('/terminate-task', message)

    def test_running_task_remains_cancellable(self):
        view = surface(task(status='running'))
        self.assertEqual(len(cancellable_tasks_from_surface(view)), 1)
        message = self.render('/tasks nano same-task run', view)
        self.assertIn('/cancel-task same-task', message)

    def test_same_id_different_requests_requires_exact_selection(self):
        view = surface(task('nano', 'first'), task('nano', 'second'))
        ambiguous = self.render('/tasks same-task', view)
        self.assertIn('여러 범위', ambiguous)
        self.assertNotIn('forager ondesk plan-inspect', ambiguous)
        exact = self.render('/tasks nano same-task second', view)
        self.assertIn('실행 완료', exact)

    def test_unknown_task_does_not_fall_back_to_another_project(self):
        message = self.render('/tasks overton same-task run', surface(task()))
        self.assertIn('찾지 못했습니다', message)
        self.assertNotIn('forager ondesk plan-inspect', message)

    def test_exact_running_lookup_with_reused_id_does_not_offer_unscoped_cancel(self):
        view = surface(task('nano', 'first', status='running'), task('nano', 'second', status='running'))
        message = self.render('/tasks nano same-task first', view)
        self.assertIn('실행 중', message)
        self.assertNotIn('/cancel-task', message)

    def test_unreadable_task_collection_is_not_reported_as_no_tasks(self):
        with self.assertRaises(operator.RemoteOperatorTelegramError):
            inspectable_tasks_from_surface({'projects': [{'project_key': 'nano', 'task_items': None}]})

    def test_recovery_notification_inspects_before_any_retirement_choice(self):
        view = {'projects': [], 'accepted_truth_recovery': {'items': [{
            'closeout_id': 'closeout-a', 'stage': 'followup_required', 'acceptance_status': 'pending',
            'next_safe_action': 'Review remaining follow-ups before treating the result as accepted.',
            'action_envelopes': [{'action_kind': 'retire_closeout', 'observed_hash': 'v1'}]}]}}
        item = attention_summary(view)['top']
        self.assertEqual(item['command_hint'], '/recovery closeout-a')
        message = self.render(item['command_hint'], view)
        self.assertIn('남은 후속 검토', message)
        self.assertNotIn('/recover closeout-a retire_closeout', message)

    def test_task_inspection_remains_read_only_and_rejects_excess_arguments(self):
        self.assertEqual(classify_command(parse_remote_command('/tasks nano same-task run'),
                                          plan_session_input=False), ('read_only', False))
        for text in ['/tasks a b c d', '/recovery a b']:
            self.assertFalse(parse_remote_command(text)['supported'])


if __name__ == '__main__':
    unittest.main()
