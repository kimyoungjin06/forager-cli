#!/usr/bin/env python3
"""Bounded read-only runtime sampling; writes only a separate observation log."""
import argparse
import json
from pathlib import Path
import time
import datetime
import subprocess
from telegram_observation_gate import release_identity


def sample(root):
    loop = json.loads((root / "remote_operator_telegram_loop.json").read_text())
    state = json.loads((root / "remote_operator_telegram_state.json").read_text())
    evidence = state.get("session_notification_delivery_evidence", [])
    outbox_path = root / "remote_operator_telegram_reply_outbox.jsonl"
    replies = {}
    if outbox_path.exists():
        for line in outbox_path.read_text().splitlines():
            row = json.loads(line)
            replies[row['delivery_id']] = row
    return {
        "reply_holds": sum(bool(r.get('delivery_unconfirmed')) for r in replies.values() if r.get('status') in {'queued', 'failed'}),
        "poll_count": loop.get("poll_count"), "last_success_at": loop.get("last_success_at"),
        "consecutive_error_count": loop.get("consecutive_error_count"),
        "status_counts": loop.get("status_counts"),
        "proactive_holds": len(state.get("notification_unconfirmed_events", {})),
        "session_holds": sum(bool(x.get("delivery_unconfirmed")) for x in state.get("waiting_notified_by_session", {}).values() if isinstance(x, dict)),
        "evidence": [{k: row.get(k) for k in ("message_id", "at", "binding_sha256", "body_flags", "button_flags")}
                     for row in evidence if isinstance(row, dict)],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval", type=float, default=300)
    parser.add_argument("--release", type=Path, help="Bind every sample to this sealed running release")
    args = parser.parse_args()
    if not 0 < args.hours <= 24 or args.interval < 1:
        parser.error("hours must be within (0,24], interval at least one second")
    end = time.monotonic() + args.hours * 3600
    # Exclusive creation prevents accidentally appending to an unrelated ledger.
    with args.output.open("x") as log:
        while True:
            result = {"observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
            try:
                result.update(sample(args.runtime))
                if args.release:
                    result.update(release_identity(args.release.resolve()))
            except (OSError, ValueError, TypeError, AttributeError, KeyError, subprocess.SubprocessError) as error:
                result["read_error"] = type(error).__name__
            result["complete"] = time.monotonic() >= end
            log.write(json.dumps(result, sort_keys=True) + "\n")
            log.flush()
            if result["complete"]:
                break
            time.sleep(min(args.interval, max(0, end - time.monotonic())))
