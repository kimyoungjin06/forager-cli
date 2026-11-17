"""Content-free delivery evidence and read-only attention projections."""
import datetime as dt
import hashlib
import re

from .common import utc_now

SCHEMA = 'remote_operator_notification_hold.v1'
KINDS = {'keyboard_cleanup', 'interaction', 'recovery', 'decision', 'task_ready', 'status', 'autonomy'}
SOURCES = {'session', 'waiting_card', 'attention', 'autonomy', 'keyboard_cleanup'}
STATUSES = {'delivery_inflight', 'delivery_unconfirmed', 'delivery_confirmed_pending_commit'}


def new_hold(candidate, digest):
    stamp = utc_now()
    kind = str(getattr(candidate.priority, 'name', '')).lower()
    return dict(schema=SCHEMA, status='delivery_inflight', reserved_at=stamp,
                observed_at=stamp, event_kind=kind if kind in KINDS else 'unknown',
                source_kind=candidate.source if candidate.source in SOURCES else 'other',
                source_sha256=hashlib.sha256(candidate.source.encode()).hexdigest(),
                event_sha256=digest, attempt_count=None, attempt_scope='adapter_send_call',
                attempt_evidence='reserved_not_observed', delivery_outcome='unknown')


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value if parsed.tzinfo is not None else None
    except ValueError:
        return None


def hold_detail(key, record):
    typed = record.get('schema') == SCHEMA and record.get('event_sha256') == key
    ref = key if re.fullmatch('[a-f0-9]{64}', key) else hashlib.sha256(key.encode()).hexdigest()
    count = record.get('attempt_count') if typed else None
    message_id = record.get('confirmed_message_id') if typed else None
    return dict(event_ref_sha256=ref, metadata_status='recorded' if typed else 'legacy_or_incomplete',
                status=record.get('status') if record.get('status') in STATUSES else 'unknown',
                observed_at=_timestamp(record.get('observed_at')) if typed else None,
                event_kind=record.get('event_kind') if typed and record.get('event_kind') in KINDS else 'unknown',
                source_kind=record.get('source_kind') if typed and record.get('source_kind') in SOURCES | {'other'} else 'unknown',
                attempt_count=count if type(count) is int and count >= 0 else None,
                confirmed_message_id=message_id if type(message_id) is int and message_id > 0 else None)


def hold_summary(state):
    if not isinstance(state, dict):
        return dict(status='unreadable', total=None, uncertain_count=None,
                    confirmed_commit_pending_count=None, legacy_or_incomplete_count=None)
    holds = state.get('notification_unconfirmed_events', {})
    if not isinstance(holds, dict) or any(not isinstance(row, dict) for row in holds.values()):
        return dict(status='unreadable', total=None, uncertain_count=None,
                    confirmed_commit_pending_count=None, legacy_or_incomplete_count=None)
    rows = [hold_detail(key, record) for key, record in holds.items()]
    confirmed = sum(row['status'] == 'delivery_confirmed_pending_commit'
                    and row['confirmed_message_id'] is not None for row in rows)
    return dict(status='attention' if rows else 'clear', total=len(rows),
                uncertain_count=len(rows) - confirmed, confirmed_commit_pending_count=confirmed,
                legacy_or_incomplete_count=sum(row['metadata_status'] != 'recorded' for row in rows))
