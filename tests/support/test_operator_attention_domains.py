import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from telegram_operator.delivery_attention import hold_detail, hold_summary
from telegram_operator.health import listener_health
from telegram_operator.notification_policy import NotificationCandidate, NotificationCycle, NotificationPriority
from telegram_operator.rendering import render_attention_summary_message
from telegram_delivery_holds import snapshot, release


class AttentionDomains(unittest.TestCase):
    def candidate(self, event, commit=lambda _id: None, source='session'):
        return NotificationCandidate(event, source, NotificationPriority.INTERACTION,
            'PRIVATE_BODY_DO_NOT_STORE', {'private': 'PRIVATE_BUTTON'}, {}, commit)

    def test_future_hold_has_safe_identity_time_and_observed_attempt_evidence(self):
        holds, reservations = {}, []
        event = 'private-event-value'
        def persist():
            reservations.append(copy.deepcopy(holds))
        cycle = NotificationCycle(lambda _c: None, unconfirmed_events=holds, persist=persist)
        cycle.add(self.candidate(event, source='PRIVATE_SOURCE'))
        self.assertEqual(cycle.deliver()['status'], 'delivery_unconfirmed')
        key = hashlib.sha256(event.encode()).hexdigest()
        row = holds[key]
        self.assertEqual(row['attempt_count'], 1)
        self.assertIsNone(reservations[0][key]['attempt_count'])
        self.assertEqual(row['source_kind'], 'other')
        self.assertEqual(row['event_kind'], 'interaction')
        self.assertTrue(row['observed_at'])
        for secret in ['PRIVATE_BODY_DO_NOT_STORE', 'PRIVATE_BUTTON', 'PRIVATE_SOURCE', event]:
            self.assertNotIn(secret, json.dumps(holds))
        self.assertEqual(hold_detail(key, row)['metadata_status'], 'recorded')

    def test_legacy_hold_remains_unchanged_and_attempt_and_time_stay_unknown(self):
        event = 'legacy'
        key = hashlib.sha256(event.encode()).hexdigest()
        holds = {key: {'status': 'delivery_unconfirmed'}}
        before = copy.deepcopy(holds)
        cycle = NotificationCycle(lambda _c: self.fail('legacy hold replayed'), unconfirmed_events=holds)
        cycle.add(self.candidate(event))
        self.assertEqual(cycle.deliver()['status'], 'delivery_unconfirmed_hold')
        detail = hold_detail(key, holds[key])
        self.assertIsNone(detail['observed_at'])
        self.assertIsNone(detail['attempt_count'])
        self.assertEqual(holds, before)

    def test_confirmed_receipt_survives_failed_bookkeeping_and_cannot_be_retried(self):
        holds = {}
        event = 'waiting:s:p:b'
        def fail(_id):
            raise RuntimeError('fixture commit failure')
        cycle = NotificationCycle(lambda _c: 55, unconfirmed_events=holds, persist=lambda: None)
        cycle.add(self.candidate(event, commit=fail))
        with self.assertRaises(RuntimeError):
            cycle.deliver()
        self.assertEqual(hold_summary({'notification_unconfirmed_events': holds})['confirmed_commit_pending_count'], 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, outbox, lock = root / 'state', root / 'outbox', root / 'lock'
            state.write_text(json.dumps({'offset': 0, 'notification_unconfirmed_events': holds,
                'waiting_notified_by_session': {'s': {'delivery_unconfirmed': True, 'prompt_identity': 'p',
                    'remote_session_identity': {'binding_sha256': 'b'}}}}))
            outbox.write_text('')
            lock.touch()
            before = state.read_bytes()
            view = snapshot(state, outbox)[0]
            key = hashlib.sha256(event.encode()).hexdigest()
            for scope, target in [('proactive', key), ('sessions', 's')]:
                with self.assertRaises(ValueError):
                    release(state, outbox, lock, scope=scope, key=target, expected=view['snapshot_sha256'],
                            confirmed=True, reason='fixture invalid retry')
                self.assertEqual(state.read_bytes(), before)

    def health_fixture(self, *, reply_uncertain=False, pending_reply=False, poll_error=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stamp = dt.datetime.now(dt.timezone.utc)
            key = hashlib.sha256(b'legacy').hexdigest()
            state = root / 'state'
            state.write_text(json.dumps({'offset': 0, 'notification_unconfirmed_events': {key: {'status': 'delivery_unconfirmed'}}}))
            outbox = root / 'outbox'
            row = dict(schema='remote_operator_telegram_reply_outbox.v1', delivery_id='telegram_update:1',
                       status='failed', update_id=1, input_hash='i', payload_hash='p', delivery_unconfirmed=True)
            rows = [row] if reply_uncertain else []
            if pending_reply:
                rows.append({**row, 'delivery_id': 'telegram_update:2', 'update_id': 2,
                             'delivery_unconfirmed': False})
            outbox.write_text(''.join(json.dumps(item) + '\n' for item in rows))
            journal = root / 'journal'
            journal.write_text('')
            loop = root / 'loop'
            loop.write_text(json.dumps(dict(status='polling', generated_at=stamp.isoformat(),
                last_result=dict(status='poll_error' if poll_error else 'no_update', generated_at=stamp.isoformat()),
                consecutive_error_count=4 if poll_error else 0,
                last_success_at=(stamp - dt.timedelta(minutes=5)).isoformat())))
            args = argparse.Namespace(state_file=state, reply_outbox_file=outbox, update_journal_file=journal,
                                      loop_status_file=loop, profile='fixture', health_max_age_sec=120,
                                      env_file=root / 'env', enable_runtime_dispatch=False)
            config = dict(token='fixture', chat_allowlist_configured=True, user_allowlist_configured=True)
            before = {p: p.read_bytes() for p in [state, outbox, journal, loop]}
            with patch('telegram_operator.health.resolve_agent_runtime_status', return_value={'status': 'disabled'}), \
                 patch('telegram_operator.health.workspace_roots', return_value=[]), \
                 patch('telegram_operator.health.load_registry', return_value={}):
                report = listener_health(args, config)
            self.assertEqual(before, {p: p.read_bytes() for p in before})
            return report

    def test_uncertain_delivery_is_separate_from_a_healthy_polling_listener(self):
        report = self.health_fixture(reply_uncertain=True)
        self.assertEqual(report['health_status'], 'degraded')
        self.assertEqual(report['attention_domains']['listener']['status'], 'healthy')
        self.assertEqual(report['attention_domains']['delivery']['status'], 'attention')
        self.assertEqual(report['attention_domains']['project_review']['status'], 'not_inspected')
        self.assertEqual(report['attention_domains']['delivery']['proactive_holds']['legacy_or_incomplete_count'], 1)

    def test_delivery_hold_does_not_mask_a_real_listener_failure(self):
        report = self.health_fixture(poll_error=True)
        self.assertEqual(report['health_status'], 'unhealthy')
        self.assertIn('last_poll_transport_error', report['transport_issues'])

    def test_pending_reply_is_unhealthy_even_alongside_uncertain_delivery(self):
        report = self.health_fixture(reply_uncertain=True, pending_reply=True)
        self.assertEqual(report['health_status'], 'unhealthy')
        self.assertIn('reply_delivery_pending', report['transport_issues'])
        self.assertEqual(report['attention_domains']['listener']['status'], 'healthy')
        self.assertEqual(report['attention_domains']['delivery']['status'], 'blocked')
        self.assertIn('reply_delivery_unconfirmed', report['attention_domains']['delivery']['warnings'])
        self.assertEqual(report['reply_outbox']['pending_count'], 1)
        self.assertEqual(report['reply_outbox']['unconfirmed_count'], 1)

    def test_pending_reply_alone_keeps_the_delivery_failure_visible(self):
        report = self.health_fixture(pending_reply=True)
        self.assertEqual(report['health_status'], 'unhealthy')
        self.assertIn('reply_delivery_pending', report['transport_issues'])
        self.assertEqual(report['attention_domains']['listener']['status'], 'healthy')
        self.assertEqual(report['attention_domains']['delivery']['status'], 'blocked')

    def test_corrupt_delivery_metadata_is_unknown_instead_of_zero(self):
        summary = hold_summary({'notification_unconfirmed_events': []})
        self.assertEqual(summary['status'], 'unreadable')
        self.assertIsNone(summary['total'])

    def test_project_review_card_has_explicit_scope(self):
        message = render_attention_summary_message(profile='fixture', generated_at='',
            summary={'total': 1, 'task_count': 1, 'top': {'project_key': 'project-a', 'title': 'Review pending'}})
        self.assertIn('프로젝트 검토·작업 요약', message)
        self.assertIn('프로젝트: project-a', message)


if __name__ == '__main__':
    unittest.main()
