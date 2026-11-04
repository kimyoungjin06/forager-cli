"""Recheck a known serial occupancy hold without retrying a worker or model."""

import time


class HandoffError(ValueError):
    pass


def dispatch_after_reconciliation(tick, reconcile, max_wait=60, clock=time.monotonic, sleep=time.sleep):
    if not 0 <= max_wait <= 60:
        raise HandoffError("invalid serial reconciliation window")
    stop = clock() + max_wait
    while True:
        report = tick()
        if report.get("launched") == 1:
            return report
        reasons = report.get("bounded_shift", {}).get("hold_reasons", [])
        if report.get("launched") != 0 or reasons != ["shift_workdir_occupied"]:
            raise HandoffError("unexpected dispatch gate: " + str(report))
        if clock() >= stop:
            raise HandoffError("previous workdir is still occupied after bounded reconciliation")
        # The callback may only reconcile the already completed exact ticket.
        # Never force a release, edit a task or resubmit the worker command.
        reconcile()
        sleep(min(2, max(0, stop - clock())))
