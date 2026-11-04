"""Offline relay regressions, without Telegram or real session input."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from telegram_operator import agent


class RelaySafety(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            'live_agent_sessions': [{'id': 'abc123', 'title': 'Wrt', 'project': 'NanoClustering'}],
            'registered_projects': [{'key': 'NanoClustering', 'aliases': ['Nano']}],
        }
        self.request = 'Nano:Wrt 에게 아래처럼 입력해줘.\n\n그럼 다음 작업은 뭘 하면 좋을까?'

    def resolve(self, text=None):
        return agent.explicit_session_message_intent(text or self.request, self.snapshot)

    def test_exact_incident_routes_without_model(self):
        with patch.object(agent, 'resolve_agent_config', side_effect=AssertionError('model called')):
            result = agent.chat_with_agent(None, self.request, operator_snapshot=self.snapshot)
        self.assertEqual(result['session_id'], 'abc123')
        self.assertEqual(result['session_message'], '그럼 다음 작업은 뭘 하면 좋을까?')
        self.assertEqual(result['session_target_status'], 'resolved')

    def test_wrong_project_does_not_select_unique_title(self):
        self.assertTrue(self.resolve(self.request.replace('Nano:', 'Unknown:'))['requires_clarification'])

    def test_unique_project_abbreviation_and_collision(self):
        self.snapshot['registered_projects'][0]['aliases'] = []
        self.assertEqual(self.resolve()['session_id'], 'abc123')
        self.snapshot['registered_projects'].append({'key': 'NanoOther', 'aliases': []})
        self.assertTrue(self.resolve()['requires_clarification'])
        self.assertTrue(self.resolve(self.request.replace('Nano:', 'Nan:'))['requires_clarification'])

    def test_duplicate_titles_are_not_selected(self):
        self.snapshot['live_agent_sessions'].append({'id': 'def456', 'title': 'Wrt', 'project': 'NanoClustering'})
        self.assertTrue(self.resolve()['requires_clarification'])

    def test_reply_card_conflict_refused_but_sticky_focus_does_not_override_explicit_target(self):
        self.snapshot['conversation_session'] = {'id': 'other', 'source': 'reply_card'}
        self.assertTrue(self.resolve()['requires_clarification'])
        self.snapshot['conversation_session']['source'] = 'sticky'
        self.assertEqual(self.resolve()['session_id'], 'abc123')

    def test_payload_preserved_and_length_rejected(self):
        header = 'Nano:Wrt에게 아래처럼 입력해줘\n'
        payload = 'first line\n  indented "quotes" $variable\n전달해줘'
        self.assertEqual(self.resolve(header + payload)['session_message'], payload)
        self.assertTrue(self.resolve(header + 'x' * 2001)['requires_clarification'])

    def test_unknown_and_ambiguous_project_alias_refused(self):
        self.snapshot['registered_projects'].append({'key': 'Other', 'aliases': ['Nano']})
        self.assertTrue(self.resolve()['requires_clarification'])
        self.snapshot['live_agent_sessions'] = []
        self.assertTrue(self.resolve()['requires_clarification'])

    def test_questions_quotes_negation_are_not_literal_commands(self):
        for text in ['Nano:Wrt에게 아래처럼 입력해줘도 될까?\nhello',
                     '> Nano:Wrt에게 아래처럼 입력해줘.\nhello',
                     'Nano:Wrt에게 아래처럼 입력하지 말아줘.\nhello']:
            result = self.resolve(text)
            self.assertTrue(result is None or result.get('requires_clarification'))

    def test_nonexecuted_promise_is_replaced(self):
        intent = {'status': 'classified', 'intent': 'chat', 'assistant_reply': 'Wrt에 메시지를 전달할게요.'}
        result = agent.validate_session_message_intent(intent, chat_text='전달 요청', operator_snapshot=self.snapshot)
        self.assertEqual(result['reason'], 'unexecuted_delivery_claim')
        self.assertIn('보내지 않았습니다', result['assistant_reply'])
        plain = {**intent, 'assistant_reply': '현재 입력 대기 상태입니다.'}
        self.assertEqual(agent.validate_session_message_intent(plain, chat_text='상태?', operator_snapshot=self.snapshot), plain)

    def test_wrong_model_answer_cannot_lose_literal_payload(self):
        intent = {'status': 'classified', 'intent': 'chat', 'assistant_reply': '전달할게요.'}
        result = agent.validate_session_message_intent(intent, chat_text=self.request, operator_snapshot=self.snapshot)
        self.assertEqual(result['session_target_status'], 'resolved')


if __name__ == '__main__':
    unittest.main()
