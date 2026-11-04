"""Direct isolated runtime probes with the sealed production binary and Python.

Real tmux and real CLI storage/identity; synthetic Telegram transport and agent.
Never mount host config, credentials, tmux socket or Docker socket.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

assert Path('/.dockerenv').exists()
assert not Path('/home/kimyoungjin06').exists()
assert not Path('/var/run/docker.sock').exists()
assert 'TMUX' not in os.environ
release = Path('/release')
manifest = json.loads((release / 'manifest.json').read_text())
for name, expected in manifest['files'].items():
    assert hashlib.sha256((release / name).read_bytes()).hexdigest() == expected, name
sys.path.insert(0, str(release / 'candidate/scripts'))
import offdesk_remote_operator_telegram as operator
from telegram_operator.dispatch import apply_session_text_input, classify_session_interaction, session_prompt_identity
from telegram_operator.notification_policy import NotificationCycle
from telegram_operator.agent import explicit_session_message_intent

binary = str(release / 'candidate/bin/forager')
root = Path('/tmp/lifecycle')
root.mkdir()
bin_dir = root / 'bin'
bin_dir.mkdir()
shutil.copy2('/source/tests/support/disposable_echo_agent.py', bin_dir / 'codex')
(bin_dir / 'codex').chmod(0o755)
os.environ['PATH'] = str(bin_dir) + ':/test-bin:/usr/bin:/bin'
scene, received = root / 'scene.txt', root / 'received.jsonl'
READY = 'Fixture task complete.\n› Ask Codex to do anything\ngpt-6-astra high · fixture · Main [default]\n'
scene.write_text(READY)
project = root / 'project'
project.mkdir()


def run(*argv):
    try:
        return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=25)
    except subprocess.CalledProcessError as error:
        print(error.output, flush=True)
        raise


def status():
    rows = json.loads(run(binary, '-p', 'lifecycle-test', 'status', '--json', '--notification-capture'))['sessions']
    assert len(rows) == 1, rows
    return rows[0]


def wait_for(predicate, label):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError(label)


def receipt(name, **evidence):
    print(json.dumps({'probe': name, 'result': 'pass', **evidence}, ensure_ascii=False), flush=True)


def messages():
    return [json.loads(line) for line in received.read_text().splitlines()] if received.exists() else []


run('tmux', 'new-session', '-d', '-s', 'lifecycle_canary', 'sleep 300')
run('tmux', 'set-environment', '-g', 'PATH', os.environ['PATH'])
run(binary, '-p', 'lifecycle-test', 'add', str(project), '--title', 'Wrt', '--cmd', f'codex {scene} {received}')
run(binary, '-p', 'lifecycle-test', 'session', 'start', status()['id'])
wait_for(lambda: status()['status'] == 'waiting', 'fixture did not become waiting')
row = status()
assert row['remote_session_identity']['binding_sha256'] == row['notification_capture']['binding_sha256']
receipt('live_status_and_capture_bound', session_id=row['id'])

intent = explicit_session_message_intent('Wrt에게 아래처럼 입력해줘.\n\n그럼 다음 작업은 뭘 하면 좋을까?', {'live_agent_sessions': [row]})
assert intent['session_target_status'] == 'resolved', intent
tail = row['notification_capture']['tail']
prompt = session_prompt_identity(tail, 'codex', interaction=classify_session_interaction(tail, 'codex'))
outcome = apply_session_text_input(binary, 'lifecycle-test', row['id'], intent['session_message'],
    require_exact_id=True, expected_identity=row['remote_session_identity'], require_remote_identity=True,
    expected_prompt_identity=prompt, expected_interaction_kind='task_ready')
assert outcome['ok'], outcome
wait_for(lambda: messages() == [intent['session_message']], 'literal payload not received exactly once')
receipt('literal_message_exactly_received', message_count=len(messages()), pane_reacted=outcome.get('pane_reacted'))

scene.write_text('Which target should I use?\n› 1. Development\n  2. Production\nEnter to select\n')
wait_for(lambda: 'Which target' in status().get('notification_capture', {}).get('tail', ''), 'scene not refreshed')
before = messages()
outcome = apply_session_text_input(binary, 'lifecycle-test', row['id'], 'MUST_NOT_ARRIVE',
    require_exact_id=True, expected_identity=row['remote_session_identity'], require_remote_identity=True,
    expected_prompt_identity=prompt, expected_interaction_kind='task_ready')
assert not outcome['ok'] and outcome['error'] == 'prompt_changed', outcome
assert messages() == before
receipt('old_prompt_rejected_without_input')

scene.write_text(READY)
wait_for(lambda: classify_session_interaction(status().get('notification_capture', {}).get('tail', ''), 'codex')['kind'] == 'task_ready', 'ready scene missing')
old = status()
tmux_name = next(name for name in run('tmux', 'list-sessions', '-F', '#S').splitlines() if name.endswith(old['id'][:8]))
run('tmux', 'respawn-pane', '-k', '-t', tmux_name, f'codex {scene} {received}')
wait_for(lambda: status()['status'] == 'waiting', 'replacement not ready')
fresh = status()
assert fresh['remote_session_identity']['binding_sha256'] != old['remote_session_identity']['binding_sha256']
outcome = apply_session_text_input(binary, 'lifecycle-test', old['id'], 'OLD_IDENTITY_MUST_NOT_ARRIVE',
    require_exact_id=True, expected_identity=old['remote_session_identity'], require_remote_identity=True)
assert not outcome['ok'] and outcome['error'].startswith('session_identity_changed:'), outcome
assert messages() == before
receipt('real_pane_replacement_rejects_old_identity', reason=outcome['error'])
outcome = apply_session_text_input(binary, 'lifecycle-test', fresh['id'], 'FRESH_IDENTITY_OK',
    require_exact_id=True, expected_identity=fresh['remote_session_identity'], require_remote_identity=True)
assert outcome['ok'], outcome
wait_for(lambda: messages() == before + ['FRESH_IDENTITY_OK'], 'new identity delivery missing')
receipt('fresh_identity_received_after_replacement')

cases = [
    ('ready', READY, 1),
    ('passive_wait', 'No action is required. Codex will keep waiting.\n› 1. Dismiss and keep waiting\n  2. Interrupt\n', 0),
    ('real_menu', 'Which target should I use?\n› 1. Development\n  2. Production\nEnter to select\n', 1),
    ('wrapped_menu', 'Which target should I use?\n› 1. Development environment\n     using the isolated fixture data\n  2. Production environment\n     requiring explicit confirmation\nEnter to select\n', 1),
    ('old_passive_new_menu', 'No action is required. Codex will keep waiting.\n› 1. Dismiss and keep waiting\n  2. Interrupt\n\nWhich target should I use?\n› 1. Development\n  2. Production\nEnter to select\n', 1),
    ('old_menu_new_ready', 'Which target should I use?\n› 1. Development\n  2. Production\nEnter to select\n\n' + READY, 1),
    ('running', '• Working (3s • esc to interrupt)\n', 0),
]
status_file = root / 'notification-status.json'
args = argparse.Namespace(
    session_notify=True, dry_run=True, session_status_file=status_file,
    session_notify_quiet_from_hour=0, session_notify_quiet_to_hour=0,
    session_notify_backoff_sec=1800, session_notify_max_backoff_sec=14400,
    session_notify_max_cards=4,
)
for label, screen, expected in cases:
    scene.write_text(screen)
    time.sleep(.25)
    current = status()
    status_file.write_text(json.dumps({'sessions': [current]}))
    candidates = []
    cycle = NotificationCycle(lambda candidate: candidates.append(candidate) or None, dry_run=True)
    with patch.object(operator, 'summarize_waiting_prompt', return_value='Fixture interaction'):
        report = operator.scan_and_notify_waiting_sessions(
            args, {'target_chat_id': 'synthetic'}, {'telegram_session_keyboard_schema': 'inline_v3'}, cycle=cycle)
        cycle.deliver()
    assert len(candidates) == expected, (label, current, report, len(candidates))
    if candidates:
        assert 'Dismiss and keep waiting' not in candidates[0].message
    receipt('screen_' + label, rust_status=current['status'], candidates=len(candidates))

checkout = Path('/tmp/forager-telegram-isolated-20260922')
shutil.copytree('/source', checkout, ignore=shutil.ignore_patterns('target', '.git', '__pycache__'))
shutil.copytree(release / 'candidate/scripts', checkout / 'scripts', dirs_exist_ok=True)
(checkout / 'target/debug').mkdir(parents=True)
shutil.copy2(binary, checkout / 'target/debug/forager')
integration = '/source/target/debug/deps/remote_operator_telegram-8d0697623af9ab8b'
for name in (
    'remote_operator_telegram_inline_continue_is_prompt_and_identity_bound',
    'remote_operator_telegram_stale_mutation_is_refused_before_handler',
    'remote_operator_telegram_stale_read_only_command_remains_available',
):
    output = subprocess.check_output([integration, name, '--exact', '--test-threads=1'],
                                     cwd=checkout, text=True, stderr=subprocess.STDOUT, timeout=45)
    assert '1 passed; 0 failed' in output, output
    receipt(name)

run('tmux', 'has-session', '-t', '=lifecycle_canary')
print('runtime_lifecycle_probes=pass host_state_untouched=true transport=synthetic agent=echo', flush=True)
