import unittest

from offdesk_serial_handoff import HandoffError, dispatch_after_reconciliation


class HandoffTests(unittest.TestCase):
    def test_known_hold_reconciles_before_one_successful_launch(self):
        events = []
        reports = iter([{"launched": 0, "bounded_shift": {"hold_reasons": ["shift_workdir_occupied"]}}, {"launched": 1}])

        def tick():
            events.append("tick")
            return next(reports)

        result = dispatch_after_reconciliation(tick, lambda: events.append("exact_ticket_poll"), sleep=lambda _: None)
        self.assertEqual(result["launched"], 1)
        self.assertEqual(events, ["tick", "exact_ticket_poll", "tick"])

    def test_occupied_workdir_stops_without_forcing_or_worker_retry(self):
        now = [0]
        polls = []

        def sleep(seconds):
            now[0] += seconds

        with self.assertRaises(HandoffError):
            dispatch_after_reconciliation(lambda: {"launched": 0, "bounded_shift": {"hold_reasons": ["shift_workdir_occupied"]}},
                lambda: polls.append("read_persist_existing_ticket"), max_wait=4, clock=lambda: now[0], sleep=sleep)
        self.assertEqual(now[0], 4)
        self.assertEqual(len(polls), 2)

    def test_other_gate_fails_without_polling_or_retrying(self):
        events = []
        with self.assertRaises(HandoffError):
            dispatch_after_reconciliation(lambda: {"launched": 0, "bounded_shift": {"hold_reasons": ["approval_missing"]}},
                lambda: events.append("should_not_poll"), sleep=lambda _: events.append("should_not_sleep"))
        self.assertEqual(events, [])
