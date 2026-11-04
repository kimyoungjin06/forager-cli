#!/usr/bin/env python3
"""Inspect holds; explicitly release one verified-undelivered event while stopped."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

from telegram_operator.common import append_jsonl, utc_now
from contextlib import contextmanager, ExitStack
from telegram_operator.persistence import load_state, save_state
from telegram_operator.reply_outbox import latest_delivery_records


def snapshot(state_path, outbox_path):
    before = hashlib.sha256(state_path.read_bytes() + b"\0" + outbox_path.read_bytes()).hexdigest()
    state = load_state(state_path)
    replies = latest_delivery_records(outbox_path)
    holds = state.get("notification_unconfirmed_events", {})
    sessions = state.get("waiting_notified_by_session", {})
    if not isinstance(holds, dict) or not isinstance(sessions, dict):
        raise ValueError("invalid hold ledger")
    if any(not isinstance(row, dict) for row in sessions.values()):
        raise ValueError("invalid session ledger")
    after = hashlib.sha256(state_path.read_bytes() + b"\0" + outbox_path.read_bytes()).hexdigest()
    if before != after:
        raise ValueError("state changed during inspection; inspect again")
    view = {
        "schema": "telegram_delivery_holds.v1", "snapshot_sha256": after,
        "proactive": sorted(holds),
        "sessions": sorted(sid for sid, row in sessions.items() if row.get("delivery_unconfirmed")),
        "replies": sorted(key for key, row in replies.items()
                          if row.get("status") in {"queued", "failed"}
                          and row.get("delivery_unconfirmed")),
    }
    return view, state, replies


def release(state_path, outbox_path, lock_path, *, scope, key, expected, reason, confirmed):
    if not confirmed or not reason.strip():
        raise ValueError("requires verified non-delivery and an operator reason")
    if not lock_path.exists() or lock_path.is_symlink():
        raise ValueError("existing listener lock required")
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        view, state, replies = snapshot(state_path, outbox_path)
        if expected != view["snapshot_sha256"]:
            raise ValueError("stale snapshot; inspect again")
        if key not in view[scope]:
            raise ValueError("exact held target not found")
        audit = {"at": utc_now(), "scope": scope, "key": key,
                 "decision": "retry_verified_not_delivered",
                 "reason_sha256": hashlib.sha256(reason.encode()).hexdigest(),
                 "before_sha256": expected}
        if scope == "replies":
            row = dict(replies[key])
            if type(row.get("sent_message_id")) is int and row["sent_message_id"] > 0:
                raise ValueError("acknowledged delivery cannot be released for retry")
            row.pop("delivery_unconfirmed", None)
            row.update(status="failed", delivery_inflight=False, recorded_at=utc_now(),
                       last_error="operator verified non-delivery; retry permitted",
                       delivery_reconciliation=audit)
            append_jsonl(outbox_path, row, durable=True)
        else:
            holds = state.setdefault("notification_unconfirmed_events", {})
            sessions = state.setdefault("waiting_notified_by_session", {})
            if scope == "sessions":
                targets = [key]
            else:
                targets = []
                for sid, row in sessions.items():
                    identity = row.get("remote_session_identity") or {}
                    prompt = row.get("prompt_identity") or row.get("prompt_hash") or ""
                    event = f"waiting:{sid}:{prompt}:{identity.get('binding_sha256') or ''}"
                    if hashlib.sha256(event.encode()).hexdigest() == key:
                        targets.append(sid)
                if not targets:
                    raise ValueError("digest-only or unbound notification stays held")
                del holds[key]
            for sid in targets:
                row = sessions[sid]
                if type(row.get("message_id")) is int and row["message_id"] > 0:
                    raise ValueError("acknowledged delivery cannot be released for retry")
                del sessions[sid]
                identity = row.get("remote_session_identity") or {}
                prompt = row.get("prompt_identity") or row.get("prompt_hash") or ""
                event = f"waiting:{sid}:{prompt}:{identity.get('binding_sha256') or ''}"
                holds.pop(hashlib.sha256(event.encode()).hexdigest(), None)
            history = state.setdefault("notification_hold_reconciliations", [])
            if not isinstance(history, list):
                raise ValueError("invalid reconciliation history")
            state["notification_hold_reconciliations"] = (history + [audit])[-256:]
            save_state(state_path, state)
        return {"status": "retry_permitted", "scope": scope, "key": key, "sent": False}
    finally:
        os.close(fd)


def watchdog_snapshot(state_path, outbox_path, watchdog_path):
    base, _, _ = snapshot(state_path, outbox_path)
    raw = watchdog_path.read_bytes()
    record = json.loads(raw)
    if not isinstance(record, dict) or record.get("schema") != "remote_operator_telegram_watchdog_state.v1":
        raise ValueError("unsupported watchdog state")
    attempt = record.get("delivery_attempt")
    if not isinstance(attempt, dict) or attempt.get("status") not in {"sending", "unconfirmed", "delivered", "failed", "retry_permitted"}:
        raise ValueError("invalid or legacy watchdog attempt; keep held")
    if not attempt.get("at") or attempt.get("kind") not in {"failure", "recovery"} or not isinstance(attempt.get("key"), str):
        raise ValueError("incomplete watchdog attempt; keep held")
    key = hashlib.sha256(json.dumps(attempt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    after, _, _ = snapshot(state_path, outbox_path)
    if base != after or watchdog_path.read_bytes() != raw:
        raise ValueError("state changed during inspection")
    return {**base, "snapshot_sha256": hashlib.sha256(base["snapshot_sha256"].encode() + b"\0" + raw).hexdigest(),
            "watchdog": [{"key": key, "status": attempt["status"], "kind": attempt["kind"],
                          "at": attempt["at"], "message_id": attempt.get("message_id")}],
            "watchdog_snapshot_sha256": hashlib.sha256(raw).hexdigest()}, record


@contextmanager
def existing_lock(path):
    if not path.exists() or path.is_symlink():
        raise ValueError("existing stopped-writer lock required")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def release_watchdog(state_path, outbox_path, watchdog_path, listener_lock, *, key, expected, reason, confirmed):
    if not confirmed or not reason.strip():
        raise ValueError("requires verified non-delivery and an operator reason")
    with ExitStack() as locks:
        locks.enter_context(existing_lock(listener_lock))
        locks.enter_context(existing_lock(watchdog_path.with_suffix(watchdog_path.suffix + ".lock")))
        view, record = watchdog_snapshot(state_path, outbox_path, watchdog_path)
        if view["snapshot_sha256"] != expected or view["watchdog"][0]["key"] != key:
            raise ValueError("stale snapshot or wrong watchdog attempt")
        attempt = record["delivery_attempt"]
        if attempt["status"] not in {"sending", "unconfirmed"} or type(attempt.get("message_id")) is int and attempt["message_id"] > 0:
            raise ValueError("only unacknowledged uncertain attempts may be released")
        history = record.get("delivery_reconciliations", [])
        if not isinstance(history, list):
            raise ValueError("invalid watchdog reconciliation history")
        audit = {"at": utc_now(), "scope": "watchdog", "key": key,
                 "decision": "retry_verified_not_delivered", "before_sha256": expected,
                 "reason_sha256": hashlib.sha256(reason.encode()).hexdigest()}
        record["delivery_reconciliations"] = (history + [audit])[-256:]
        record["delivery_attempt"] = {**attempt, "status": "retry_permitted", "reconciliation": audit}
        from offdesk_remote_operator_watchdog import write_json
        write_json(watchdog_path, record)
    return {"status": "retry_permitted", "scope": "watchdog", "key": key, "sent": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--outbox", type=Path, required=True)
    parser.add_argument("--listener-lock", type=Path)
    parser.add_argument("--watchdog-state", type=Path)
    parser.add_argument("--release", choices=("proactive", "sessions", "replies", "watchdog"))
    parser.add_argument("--key")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--reason", default="")
    parser.add_argument("--confirmed-not-delivered", action="store_true")
    args = parser.parse_args()
    try:
        if args.release:
            if args.listener_lock is None:
                raise ValueError("listener lock is required")
            if args.release == "watchdog":
                if args.watchdog_state is None:
                    raise ValueError("watchdog state is required")
                result = release_watchdog(args.state, args.outbox, args.watchdog_state, args.listener_lock,
                    key=args.key, expected=args.expected_sha256, reason=args.reason, confirmed=args.confirmed_not_delivered)
            else:
                result = release(args.state, args.outbox, args.listener_lock, scope=args.release,
                    key=args.key, expected=args.expected_sha256, reason=args.reason, confirmed=args.confirmed_not_delivered)
        else:
            result = (watchdog_snapshot(args.state, args.outbox, args.watchdog_state)[0]
                      if args.watchdog_state else snapshot(args.state, args.outbox)[0])
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
        print(json.dumps({"status": "refused", "error_type": type(error).__name__}))
        raise SystemExit(2)
