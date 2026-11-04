"""Final transport wording must follow effects, not generated claims."""
from pathlib import Path
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from telegram_operator.behavior_core import ActionOutcome, execution_receipt_message


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.none = ActionOutcome('id', 1, None, 'command', 'observed', 'rendered',
                                  'none', 'none', False, 'none', None)

    def test_session_request_cannot_claim_success_without_effect(self):
        for command in ('session_send', 'session_continue', 'session_approve'):
            text = execution_receipt_message(self.none, {'parsed_command': {'command': command}}, 'DONE!')
            self.assertIn('세션 입력 없음', text)
            self.assertNotIn('DONE', text)

    def test_success_does_not_claim_work_completion(self):
        outcome = replace(self.none, effect_kind='session_message', effect_status='applied', effect_authorized=True)
        text = execution_receipt_message(outcome, {}, 'task completed')
        self.assertIn('입력 전송됨', text)
        self.assertIn('아직 확인되지', text)
        self.assertNotIn('task completed', text)

    def test_failed_dispatch_does_not_claim_no_side_effect(self):
        outcome = replace(self.none, effect_kind='session_message', effect_status='refused')
        text = execution_receipt_message(outcome, {}, 'delivered')
        self.assertIn('성공 확인 없음', text)

    def test_chat_receipt_is_explicit_and_non_session_actions_unchanged(self):
        text = execution_receipt_message(self.none, {'parsed_command': {'command': 'chat'}}, 'Answer')
        self.assertIn('실행 기록: 세션 입력 없음', text)
        self.assertIn('실행 증거가 아닙니다', text)
        self.assertEqual(execution_receipt_message(self.none, {}, 'status'), 'status')


if __name__ == '__main__':
    unittest.main()
