import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from telegram_observation_gate import assess


class GateTests(unittest.TestCase):
    def rows(self):
        start = datetime.datetime(2026, 9, 22, tzinfo=datetime.timezone.utc)
        return [dict(observed_at=(start + datetime.timedelta(minutes=5*i)).isoformat(),
                     last_success_at=(start + datetime.timedelta(minutes=5*i)).isoformat(),
                     release_sha256='a', consecutive_error_count=0, complete=i == 288)
                for i in range(289)]

    def test_complete_is_observation_only(self):
        result = assess(self.rows())
        self.assertEqual(result['observation_gate'], 'pass')
        self.assertFalse(result['product_accepted'])

    def test_release_change_gap_stale_and_holds_reject(self):
        for key, value, reason in [('release_sha256', 'b', 'release_unbound_or_changed'),
                                   ('last_success_at', '2026-01-01T00:00:00+00:00', 'stale_poll'),
                                   ('reply_holds', 1, 'delivery_holds'),
                                   ('read_error', 'ValueError', 'sample_error')]:
            rows = self.rows()
            rows[5][key] = value
            self.assertIn(reason, assess(rows)['reasons'])
        rows = self.rows()
        del rows[4:8]
        self.assertIn('sampling_gap_or_clock_jump', assess(rows)['reasons'])

    def test_short_collection_cannot_pass(self):
        rows = self.rows()[:2]
        rows[-1]['complete'] = True
        self.assertIn('duration_incomplete', assess(rows)['reasons'])


if __name__ == '__main__':
    unittest.main()
