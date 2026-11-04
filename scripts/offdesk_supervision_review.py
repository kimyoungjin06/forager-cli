"""Offline observations from a native stop snapshot; never a runtime policy."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import shlex
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from offdesk_request_telemetry import INFERENCE, FRAME_KINDS, is_control_event


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Nonnegative finite observation required')
    return value


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Nonnegative integer count required')
    return value


def review_stop(summary, native_result, events, *, freshness_seconds, control_events=None):
    """Describe local evidence as of the pre-cancellation stop, without grace."""
    if number(freshness_seconds) == 0:
        raise ValueError('Positive diagnostic freshness interval required')
    stop = summary['native_stop']
    cutoff = number(stop['observed_at'])
    if stop['reason'] != native_result['stop_reason']:
        raise ValueError('Native result and stop snapshot must describe the same stop')
    started = count(native_result['started_tool_calls'])
    completed = count(native_result['completed_tool_calls'])
    in_flight = count(native_result['in_flight_tool_calls'])
    if in_flight > started:
        raise ValueError('In-flight count exceeds observed starts')
    tool_state = ('command_in_flight' if in_flight else
                  'no_command_dispatched' if not started and not completed else
                  'no_command_completion' if not completed else 'command_completed')
    control_complete = None
    if control_events is not None:
        retention = summary.get('event_retention', {})
        if (retention.get('schema') != 'offdesk_event_retention.v1'
                or count(retention.get('control_events_written')) != len(control_events)):
            raise ValueError('Selected control journal must match its retention record')
        control_complete = count(retention.get('control_events_dropped')) == 0
        merged = {}
        for channel, rows in [('detail', events), ('control', control_events)]:
            seen = set()
            previous = 0
            for event in rows:
                sequence = count(event.get('event_sequence'))
                if sequence <= previous or sequence in seen:
                    raise ValueError('Each journal must have strictly increasing event identity')
                previous = sequence
                seen.add(sequence)
                if channel == 'control' and not is_control_event(event.get('event'), event):
                    raise ValueError('Control journal contains an unselected detail event')
                if sequence in merged and merged[sequence] != event:
                    raise ValueError('Journals disagree on the same event identity')
                merged[sequence] = event
        events = [merged[key] for key in sorted(merged)]
    before = [event for event in events if number(event['observed_at']) <= cutoff]
    # Final summary rows may include cancellation errors or terminal frames
    # observed after the stop. Only pre-stop events and its snapshot are causal.
    requests = {event['request_id']: dict(event) for event in before
                if event['event'] == 'request_finished'}
    active_ids = set()
    for row in stop['active_requests']:
        requests[row['request_id']] = dict(row)
        active_ids.add(row['request_id'])
    rows = []
    for identifier, row in sorted(requests.items()):
        if row['method'] != 'POST' or row['path'] not in INFERENCE:
            continue
        frames = [event for event in before if event['event'] == 'stream_frame'
                  and event['request_id'] == identifier]
        kinds = collections.Counter(event['kind'] if event['kind'] in (
            'reasoning', 'content', 'tool', 'terminal', 'other') else 'other' for event in frames)
        last_byte = row.get('last_byte_at')
        age = None if last_byte is None else cutoff - number(last_byte)
        frame_age = None if not frames else cutoff - max(event['observed_at'] for event in frames)
        snapshot_counts = None
        last_kind = None
        if 'frame_kind_counts' in row:
            snapshot_counts = row['frame_kind_counts']
            if (not isinstance(snapshot_counts, dict) or set(snapshot_counts) - FRAME_KINDS
                    or sum(count(value) for value in snapshot_counts.values()) != count(row.get('frames'))):
                raise ValueError('Snapshot frame totals must be complete bounded metadata')
            if snapshot_counts:
                last_kind = row.get('last_frame_kind')
                snapshot_frame_at = number(row.get('last_frame_at'))
                if last_kind not in snapshot_counts or snapshot_frame_at > cutoff:
                    raise ValueError('Frame snapshot cannot use post-stop observations')
                frame_age = cutoff - snapshot_frame_at
        status = row.get('status')
        state = ('local_transport_error_observed' if row.get('error_class') else
                 'http_error_observed' if status is not None and not 200 <= status < 300 else
                 'response_finished' if identifier not in active_ids else
                 'awaiting_headers' if row.get('headers_at') is None else
                 'awaiting_first_byte' if row.get('first_byte_at') is None else
                 'timestamp_inconsistent' if age is None or age < 0 else
                 'no_recent_response_bytes' if age > freshness_seconds else
                 'recent_classified_frames' if frame_age is not None and frame_age <= freshness_seconds else
                 'recent_bytes_without_recent_classified_frame')
        rows.append({'request_id': identifier, 'state': state, 'http_status': status,
                     'active_at_stop': identifier in active_ids,
                     'last_byte_age_seconds': age, 'last_classified_frame_age_seconds': frame_age,
                     'response_bytes': count(row['response_bytes']),
                     'classified_frame_kinds_before_stop': dict(kinds),
                     'frame_kind_counts_at_stop': snapshot_counts,
                     'last_frame_kind_at_stop': last_kind,
                     'terminal_observed_before_stop': bool(row.get('terminal_frame_observed'))
                         or kinds['terminal'] > 0})
    dropped = count(summary['events_dropped'])
    return {'schema': 'offdesk_supervision_review.v1', 'as_of': cutoff,
            'selected_stop_reason': stop['reason'], 'tool_state': tool_state,
            'seconds_without_tool_progress': number(native_result['seconds_without_tool_progress']),
            'inference_observations': rows,
            'inference_observation_available': bool(rows),
            'diagnostic_freshness_seconds': freshness_seconds,
            'events_after_stop_excluded': len(events) - len(before),
            'event_log_complete': dropped == 0,
            'control_log_complete': control_complete,
            'control_and_detail_identity_deduplicated': control_events is not None,
            'classified_frames_are_protocol_conformance_proof': False,
            'clock_basis': 'local_wall_time_snapshot; native idle duration is monotonic',
            'stop_policy_changed': False, 'runtime_resume_authorized': False,
            'source_correctness_established': False, 'backend_cancellation_acknowledged': None}


def recovery_order(events, expected_argv, expected_receipt, root=None):
    """Audit actual first-command order/receipt; a host packet is insufficient."""
    from offdesk_independent_qualification import checked_command_argv
    expected = checked_command_argv(shlex.join(expected_argv), root)
    commands = []
    completions = {}
    seen = set()
    for event in events:
        item = event.get('item', {})
        if event.get('type') not in ('item.started', 'item.completed') or item.get('type') != 'command_execution':
            continue
        if item['id'] not in seen:
            seen.add(item['id'])
            commands.append(item)
        if event['type'] == 'item.completed' and item['id'] not in completions:
            completions[item['id']] = item
    state = 'not_dispatched'
    if commands:
        first = commands[0]
        try:
            argv = checked_command_argv(first['command'], root)
        except ValueError:
            argv = None
        if argv != expected:
            state = 'wrong_first_command'
        elif first['id'] not in completions:
            state = 'not_completed'
        else:
            done = completions[first['id']]
            state = 'command_failed' if done.get('exit_code') != 0 else 'receipt_mismatch'
            if state != 'command_failed':
                try:
                    receipt = json.loads(done['aggregated_output'])
                except (ValueError, KeyError, TypeError):
                    receipt = None
                if receipt == expected_receipt:
                    state = 'verified'
    return {'state': state, 'observed_unique_commands': len(commands),
            'first_recovery_verified': state == 'verified',
            'post_execution_observation_only': True, 'admission_fencing': False,
            'implementation_qualified': False, 'runtime_resume_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--native-result', type=Path, required=True)
    parser.add_argument('--events', type=Path, required=True)
    parser.add_argument('--control-events', type=Path,
                        help='Explicit separately bounded control journal; never inferred from paths')
    parser.add_argument('--freshness-seconds', type=float, required=True,
                        help='Diagnostic interval only; never extends the selected stop')
    args = parser.parse_args()
    blobs = {}
    for key in ('summary', 'native_result', 'events', *(['control_events'] if args.control_events else [])):
        with getattr(args, key).open('rb') as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('Offline input exceeds 8 MiB')
        blobs[key] = data
    summary = json.loads(blobs['summary'])
    controls = None
    if args.control_events:
        if hashlib.sha256(blobs['control_events']).hexdigest() != summary.get('event_retention', {}).get('control_sha256'):
            raise ValueError('Selected control journal hash changed')
        controls = [json.loads(line) for line in blobs['control_events'].splitlines() if line.strip()]
    review = review_stop(summary, json.loads(blobs['native_result']),
                         [json.loads(line) for line in blobs['events'].splitlines() if line.strip()],
                         freshness_seconds=args.freshness_seconds, control_events=controls)
    review['input_sha256'] = {key: hashlib.sha256(data).hexdigest() for key, data in blobs.items()}
    print(json.dumps(review, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
