"""Read-only release-bound observation assessment, never product acceptance."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess


def release_identity(release):
    manifest_path = release / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name, expected in manifest['files'].items():
        path = (release / name).resolve()
        if not path.is_relative_to(release.resolve()):
            raise ValueError('manifest path escapes release')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('release hash mismatch')
    pid = int(subprocess.check_output([
        'systemctl', '--user', 'show', 'forager-telegram-operator.service',
        '--property=MainPID', '--value'], text=True).strip())
    argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
    script = str(release / 'candidate/scripts/offdesk_remote_operator_telegram.py')
    binary = str(release / 'candidate/bin/forager')
    if len(argv) < 2 or argv[1] != script or '--forager-bin' not in argv or argv[argv.index('--forager-bin') + 1] != binary:
        raise ValueError('running release mismatch')
    return {'release_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(), 'operator_pid': pid}


def assess(rows, hours=24, max_gap=660):
    reasons = []
    if len(rows) < 2:
        reasons.append('insufficient_samples')
    stamps = [datetime.datetime.fromisoformat(r['observed_at']).timestamp() for r in rows]
    duration = stamps[-1] - stamps[0] if stamps else 0
    if duration < hours * 3600:
        reasons.append('duration_incomplete')
    if any(not 0 < b-a <= max_gap for a, b in zip(stamps, stamps[1:])):
        reasons.append('sampling_gap_or_clock_jump')
    identities = {r.get('release_sha256') for r in rows}
    if len(identities) != 1 or None in identities:
        reasons.append('release_unbound_or_changed')
    if any(r.get('read_error') or r.get('consecutive_error_count') != 0 for r in rows):
        reasons.append('sample_error')
    if any(r.get('proactive_holds', 0) or r.get('session_holds', 0) or r.get('reply_holds', 0) for r in rows):
        reasons.append('delivery_holds')
    for row, stamp in zip(rows, stamps):
        success = row.get('last_success_at')
        if not success or not 0 <= stamp - datetime.datetime.fromisoformat(success).timestamp() <= 180:
            reasons.append('stale_poll')
            break
    if not rows or not rows[-1].get('complete'):
        reasons.append('collection_not_complete')
    return {'observation_gate': 'pass' if not reasons else 'pending_or_failed',
            'reasons': sorted(set(reasons)), 'samples': len(rows), 'duration_hours': duration / 3600,
            'product_accepted': False,
            'remaining_gates': ['real_user_delivery', 'card_expiry_and_session_restart', 'notification_accuracy_review']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(assess([json.loads(line) for line in args.log.read_text().splitlines()]), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'observation_gate': 'invalid', 'error_type': type(error).__name__, 'product_accepted': False}))
        raise SystemExit(2)
