import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/observe_telegram_notifications.py'


class ObservationTests(unittest.TestCase):
    def test_bounded_readonly_collection_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loop = root / 'remote_operator_telegram_loop.json'
            state = root / 'remote_operator_telegram_state.json'
            loop.write_text(json.dumps({'poll_count': 7, 'consecutive_error_count': 0}))
            state.write_text(json.dumps({'session_notification_delivery_evidence': [
                {'message_id': 23, 'body': 'private message', 'body_flags': {'dismiss': False}}
            ]}))
            before = (loop.read_bytes(), state.read_bytes())
            output = root / 'observation.jsonl'
            command = [sys.executable, str(SCRIPT), '--runtime', str(root),
                       '--output', str(output), '--hours', '0.00003', '--interval', '1']
            subprocess.run(command, check=True, capture_output=True, timeout=5)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertTrue(rows[-1]['complete'])
            self.assertEqual(rows[-1]['poll_count'], 7)
            self.assertNotIn('private message', output.read_text())
            self.assertEqual(before, (loop.read_bytes(), state.read_bytes()))
            saved = output.read_bytes()
            result = subprocess.run(command, capture_output=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(saved, output.read_bytes())

    def test_missing_runtime_is_recorded_not_silenced(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'observation.jsonl'
            subprocess.run([sys.executable, str(SCRIPT), '--runtime', directory,
                            '--output', str(output), '--hours', '0.00003'],
                           check=True, capture_output=True, timeout=5)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertTrue(rows[-1]['complete'])
            self.assertEqual(rows[-1]['read_error'], 'FileNotFoundError')


if __name__ == '__main__':
    unittest.main()
