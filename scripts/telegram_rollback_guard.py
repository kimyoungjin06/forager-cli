#!/usr/bin/env python3
"""Read-only precondition check. A pass is not permission to restore old state."""
import argparse
import json
from pathlib import Path


def check(state_path, outbox_path):
    state = json.loads(state_path.read_text())
    if not isinstance(state, dict):
        raise ValueError("invalid state")
    holds = state.get("notification_unconfirmed_events", {})
    sessions = state.get("waiting_notified_by_session", {})
    if not isinstance(holds, dict) or not isinstance(sessions, dict):
        raise ValueError("invalid hold ledger")
    latest = {}
    for line in outbox_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get("schema") != "remote_operator_telegram_reply_outbox.v1":
            raise ValueError("invalid outbox row")
        if row.get("status") not in {"queued", "failed", "delivered", "revoked"} or not isinstance(row.get("delivery_id"), str):
            raise ValueError("unknown outbox transition")
        latest[row["delivery_id"]] = row
    # Even apparently retryable pending replies require reconciliation before
    # an older sender is used. Do not infer non-delivery from a missing flag.
    pending = sum(row["status"] in {"queued", "failed"} for row in latest.values())
    session_holds = sum(bool(row.get("delivery_unconfirmed")) for row in sessions.values() if isinstance(row, dict))
    if any(not isinstance(row, dict) for row in sessions.values()):
        raise ValueError("invalid session record")
    return {"legacy_restart_blocked": bool(holds or pending or session_holds),
            "proactive_holds": len(holds), "session_holds": session_holds,
            "unresolved_replies": pending, "state_modified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(args.state, args.outbox)
    except (OSError, ValueError, TypeError, KeyError):
        result = {"legacy_restart_blocked": True, "reason": "unreadable_or_invalid_state", "state_modified": False}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(2 if result["legacy_restart_blocked"] else 0)
