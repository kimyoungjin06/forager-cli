from pathlib import Path
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import offdesk_remote_operator_telegram as operator
from telegram_operator.behavior_core import ActionOutcome, execution_receipt_message


class ButtonTests(unittest.TestCase):
    def test_receipt_key_is_scoped_to_owner_and_original_card(self):
        parsed = {'command': 'session_continue', 'session_continue_session_id': 'abc12345', 'session_continue_hash': '12345678'}
        key = operator.consumed_continue_key(parsed, 'chat', 'user', 5)
        self.assertEqual(key, operator.consumed_continue_key(parsed, 'chat', 'user', 5))
        for chat, user, card in [('other', 'user', 5), ('chat', 'other', 5), ('chat', 'user', 6)]:
            self.assertNotEqual(key, operator.consumed_continue_key(parsed, chat, user, card))
        self.assertIsNone(operator.consumed_continue_key(parsed, 'chat', 'user'))

    def test_known_stale_errors_have_nonexecuting_guidance(self):
        outcome = ActionOutcome('id', 1, None, 'command', 'refused', 'rendered',
                                'session_message', 'refused', False, 'agent_session_input', None)
        for error, phrase in [('already_processed', '이미 처리된'),
                              ('prompt_changed', '대기 상태가'),
                              ('task_ready_context_missing_or_changed', '대기 상태가'),
                              ('session_identity_changed:process_identity_sha256_changed', '실행 정보가')]:
            message = execution_receipt_message(replace(outcome, refusal_reason=error), {}, 'untrusted')
            self.assertIn(phrase, message)
            self.assertIn('최신 상태', message)
            self.assertNotIn('untrusted', message)

    def test_continue_card_is_consumed_and_status_is_readonly(self):
        self.assertTrue(operator.callback_consumes_card('fg1:c:abc12345:12345678'))
        self.assertFalse(operator.callback_consumes_card('fg1:s'))


if __name__ == '__main__':
    unittest.main()
