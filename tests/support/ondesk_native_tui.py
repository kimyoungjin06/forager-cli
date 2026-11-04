"""Opt-in native Codex qualification in a disposable VM or scoped container."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import uuid


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    container_trial = os.environ.get('FORAGER_RETURN_CONTAINER_TUI_TRIAL') == '1'
    if container_trial:
        assert os.environ.get('FORAGER_RETURN_NATIVE_TUI_TRIAL') != '1'
        assert Path('/.dockerenv').is_file()
        preflight = json.loads(Path('/tmp/forager-container-scope-pass.json').read_text())
        assert preflight['schema'] == 'forager_container_read_scope_probe.v1'
        assert preflight['read_pass'] and preflight['write_denied']
        assert preflight['outside_host_reads_denied'] and preflight['symlink_escape_denied']
        auth_source = Path('/run/forager-auth/auth.json')
        codex_home_root = Path('/home/forager-codex')
        assert codex_home_root.is_dir()
        sandbox_mode = 'danger-full-access'
        evidence = Path('/evidence/native')
    else:
        assert os.environ.get('FORAGER_RETURN_NATIVE_TUI_TRIAL') == '1'
        assert Path('/sys/class/dmi/id/sys_vendor').read_text().strip() == 'QEMU'
        assert not Path('/.dockerenv').exists()
        preflight = json.loads(Path('/tmp/forager-native-sandbox-pass.json').read_text())
        assert preflight['read_pass'] and preflight['write_denied']
        auth_source = Path('/mnt/forager_auth/auth.json')
        codex_home_root = Path('/root')
        sandbox_mode = 'read-only'
        evidence = Path('/tmp/forager-native-evidence')
    assert not Path('/var/run/docker.sock').exists()
    binary, home, root, plan, reference = map(Path, sys.argv[1:])
    setup_binary = binary
    binary = Path(os.environ.get("FORAGER_RETURN_NATIVE_CLI", str(binary)))
    deadline = time.monotonic() + 600
    while not binary.is_file() and time.monotonic() < deadline:
        time.sleep(1)
    assert binary.is_file(), "qualified native CLI artifact was not installed"
    evidence.mkdir(mode=0o700, exist_ok=True)
    (root / 'AGENTS.md').write_text('This is a context-reading fixture. Read only the referenced package and current plan. Do not access authentication files, execute task commands, or modify files.\n')
    profile = home / '.config/forager/profiles/default'
    env = {**os.environ, 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config')}
    for key in ('FORAGER_PROFILE', 'AGENT_OF_EMPIRES_PROFILE', 'FORAGER_DEBUG'):
        env.pop(key, None)
    auth_values = []

    def collect_auth(value):
        if isinstance(value, dict):
            for child in value.values():
                collect_auth(child)
        elif isinstance(value, str) and len(value) > 16:
            auth_values.append(value)

    collect_auth(json.loads(auth_source.read_text()))

    def sanitized(value):
        if isinstance(value, dict):
            return {key: sanitized(child) for key, child in value.items()}
        if isinstance(value, list):
            return [sanitized(child) for child in value]
        if isinstance(value, str):
            for secret in auth_values:
                value = value.replace(secret, '[REDACTED_CREDENTIAL]')
        return value

    sessions = []
    result = {'schema': 'forager_native_return_qualification.v1', 'modes': [],
              'task_execution_authorized': False, 'product_accepted': False,
              'host_policy_changed': False, 'native_tui': True, 'sandbox_preflight': preflight,
              'isolation_boundary': 'scoped_container' if container_trial else 'qemu_guest',
              'driver_sha256': sha(Path(__file__).read_bytes()),
              'setup_cli_sha256': sha(setup_binary.read_bytes()),
              'reader_cli_sha256': sha(binary.read_bytes())}
    frozen = {str(p): p.read_bytes() for p in [plan, root / 'night-review.json',
              profile / 'offdesk_shifts.v1.json', profile / 'offdesk_tasks.json']}

    def command(args, checked=True):
        out = subprocess.run([str(binary), *map(str, args)], env=env,
                             capture_output=True, text=True, timeout=180)
        if checked and out.returncode:
            raise AssertionError(out.stderr.strip())
        return out

    def tmux(args, checked=True):
        return subprocess.run(['tmux', *args], capture_output=True, text=True,
                              check=checked, timeout=30)

    def provider_trace(codex_home):
        frames, incomplete = [], 0
        trace_files = sorted(codex_home.glob('sessions/**/*.jsonl'))
        for trace_file in trace_files:
            data = trace_file.read_bytes()
            assert len(data) <= 4 * 1024 * 1024, "native provider trace exceeds evidence bound"
            for line in data.splitlines():
                try:
                    frame = json.loads(line)
                except ValueError:
                    incomplete += 1
                    continue
                payload = frame.get('payload', {})
                if frame.get('type') == 'response_item' and (
                        payload.get('type') in ('function_call', 'function_call_output',
                                                'custom_tool_call', 'custom_tool_call_output')
                        or (payload.get('type') == 'message'
                            and payload.get('role') in ('user', 'assistant'))):
                    frames.append(frame)
        return {'files': [str(p) for p in trace_files], 'frames': frames,
                'incomplete_lines': incomplete}

    def save():
        for instance in sessions:
            auth = codex_home_root / ('forager-native-' + instance['id']) / 'auth.json'
            if auth.is_file():
                collect_auth(json.loads(auth.read_text()))
        artifacts = {**frozen, **{str(path): path.read_bytes() for path in root.glob('*-native-package.md')}}
        result['bound_artifacts'] = {path: {'sha256': sha(data), 'utf8': data.decode()}
                                     for path, data in artifacts.items()}
        changed = [p for p, data in frozen.items()
                   if not Path(p).is_file() or Path(p).read_bytes() != data]
        ledger = profile / 'ondesk_return_deliveries.v1.json'
        result['delivery_ledger'] = json.loads(ledger.read_text()) if ledger.exists() else None
        result['fixture_state_unchanged'] = not changed
        result['changed_fixture_paths'] = changed
        (evidence / 'native-return.json').write_text(json.dumps(sanitized(result), indent=2) + '\n')
        assert not changed, 'fixture state changed: ' + ', '.join(changed)

    canary = 'forager_native_canary_' + uuid.uuid4().hex[:8]
    tmux(['new-session', '-d', '-s', canary, 'sleep 900'])
    try:
        selected_mode = os.environ.get('FORAGER_RETURN_NATIVE_MODE')
        assert selected_mode in (None, 'existing_session', 'fresh_session')
        for mode in ((selected_mode,) if selected_mode else ('existing_session', 'fresh_session')):
            row = {'mode': mode, 'send_attempts': 0, 'read_claim_observed': False,
                   'busy_refused': False, 'permission_refused': False}
            result['modes'].append(row)
            print('NATIVE_STAGE ' + mode + ' startup', flush=True)
            identifier = uuid.uuid4().hex
            title = mode
            name = 'forager_' + title + '_' + identifier[:8]
            row['tmux_session'] = name
            row['codex_home'] = str(codex_home_root / ('forager-native-' + identifier))
            codex_home = codex_home_root / ('forager-native-' + identifier)
            codex_home.mkdir(mode=0o700)
            shutil.copyfile(auth_source, codex_home / 'auth.json')
            (codex_home / 'auth.json').chmod(0o600)
            (codex_home / 'config.toml').write_text(
                'sandbox_mode = ' + json.dumps(sandbox_mode) + '\napproval_policy = "never"\n'
                'model = "gpt-6-sol"\nmodel_reasoning_effort = "low"\n'
                '[features]\nmulti_agent = false\nmemories = false\n'
                '[projects.' + json.dumps(str(root)) + ']\ntrust_level = "trusted"\n')
            launch = ' '.join(shlex.quote(str(x)) for x in [
                'env', 'CODEX_HOME=' + str(codex_home), 'SHELL=/bin/bash',
                'codex', '--no-daemon', '--no-alt-screen', '-s', sandbox_mode,
                '-a', 'never', '-C', root])
            instance = {'id': identifier, 'title': title, 'project_path': str(root),
                        'command': launch, 'tool': 'codex', 'status': 'idle',
                        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            sessions.append(instance)
            tmux(['new-session', '-d', '-x', '160', '-y', '55', '-s', name,
                  '-c', str(root), launch])
            (profile / 'sessions.json').write_text(json.dumps(sessions))
            package = root / (mode + '-native-package.md')
            export = ['ondesk', 'prompt-package', root, '--morning-review', reference,
                      '--owning-plan', plan, '--project-key', 'project', '--json',
                      '--output', package]
            if mode == 'existing_session':
                export += ['--resume-session', identifier]
            context = json.loads(command(export).stdout)['morning_return']
            assert context['resume_mode'] == mode
            assert context['current_git']['dirty']
            ticket_path = home / (mode + '-native-ticket.json')

            def pane():
                return tmux(['capture-pane', '-p', '-t', name, '-S', '-160']).stdout

            def preview(checked=False):
                return command(['ondesk', 'return', 'preview', '--package', package,
                                '--session', identifier], checked)

            deadline = time.monotonic() + 180
            last_preview_error = None
            last_startup_pane = None
            preview_attempts = 0
            while time.monotonic() < deadline:
                preview_attempts += 1
                try:
                    out = preview()
                    last_preview_error = out.stderr.strip() if out.returncode else None
                except Exception as exc:
                    out = None
                    last_preview_error = str(exc)
                if tmux(['has-session', '-t', name], False).returncode:
                    break
                try:
                    last_startup_pane = pane()
                except subprocess.CalledProcessError:
                    break
                if out is None:
                    time.sleep(1)
                    continue
                if not out.returncode:
                    break
                time.sleep(1)
            else:
                pass
            row['startup_preview_attempts'] = preview_attempts
            if out is None or out.returncode:
                row['startup_pane'] = last_startup_pane
                row['startup_preview_error'] = last_preview_error
                row['startup_session_alive'] = not bool(
                    tmux(['has-session', '-t', name], False).returncode)
                raise AssertionError('native TUI did not expose a qualified empty prompt: '
                                     + str(last_preview_error))
            row['ready_pane'] = pane()
            print('NATIVE_STAGE ' + mode + ' empty_prompt_ready', flush=True)
            before_menu = json.loads(out.stdout)
            ticket_path.write_text(json.dumps(before_menu['ticket']))
            send = ['ondesk', 'return', 'send', ticket_path, '--expected-preview-sha256',
                    before_menu['preview_sha256'], '--confirm-input']
            tmux(['send-keys', '-t', name, '-l', '/permissions'])
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                completion = pane()
                if any('permissions' in line.lower() and
                       any(word in line.lower() for word in ('choose', 'change', 'configure'))
                       for line in completion.splitlines()):
                    break
                time.sleep(.5)
            else:
                row['permission_completion_pane'] = pane()
                raise AssertionError('native permissions command completion was not observed')
            tmux(['send-keys', '-t', name, 'Enter'])
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                menu = pane()
                if ('permission' in menu.lower() or 'approval' in menu.lower()) and (
                        'enter to select' in menu.lower() or '1.' in menu):
                    blocked = preview()
                    row['permission_preview_error'] = blocked.stderr.strip()
                    if blocked.returncode and 'no safe empty task prompt' in blocked.stderr:
                        break
                time.sleep(.5)
            else:
                row['permission_pane'] = pane()
                raise AssertionError('native permissions menu was not observed/refused')
            row['permission_pane'] = menu
            ledger_path = profile / 'ondesk_return_deliveries.v1.json'
            before_refusal = ledger_path.read_bytes() if ledger_path.exists() else None
            refused = command(send, False)
            assert refused.returncode, 'stale ticket injected into native permissions menu'
            assert 'return prompt changed or busy' in refused.stderr
            row['permission_refused'] = True
            print('NATIVE_STAGE ' + mode + ' permission_refused', flush=True)
            row['permission_send_error'] = refused.stderr.strip()
            assert (ledger_path.read_bytes() if ledger_path.exists() else None) == before_refusal
            row['permission_refusal_preserved_ledger'] = True
            tmux(['send-keys', '-t', name, 'Escape'])
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                after_menu = pane()
                if any(line.strip() == '› /permissions' for line in after_menu.splitlines()):
                    tmux(['send-keys', '-t', name, 'C-u'])
                out = preview()
                if not out.returncode:
                    break
                time.sleep(.5)
            else:
                row['post_permission_pane'] = pane()
                row['post_permission_preview_error'] = out.stderr.strip()
                raise AssertionError('native permissions menu did not return to an empty task prompt')
            value = json.loads(out.stdout)
            ticket = value['ticket']
            ticket_path.write_text(json.dumps(ticket))
            request_id = ticket['request_id']
            send = ['ondesk', 'return', 'send', ticket_path, '--expected-preview-sha256',
                    value['preview_sha256'], '--confirm-input']
            row['request_id'] = request_id
            row['ticket'] = ticket
            queued = json.loads(command(send).stdout)
            row['transport_receipt'] = queued
            print('NATIVE_STAGE ' + mode + ' input_queued ' + request_id, flush=True)
            row['send_attempts'] = queued['send_attempts']
            assert queued['state'] == 'input_queued' and queued['send_attempts'] == 1
            assert not queued['read_claim_observed']
            deadline = time.monotonic() + 300
            last_observation = None
            while time.monotonic() < deadline:
                screen = pane()
                if 'Working' in screen or 'esc to interrupt' in screen.lower():
                    blocked = preview()
                    if blocked.returncode and 'no safe empty task prompt' in blocked.stderr:
                        row['busy_refused'] = True
                        row['busy_error'] = blocked.stderr.strip()
                        row['busy_pane_sha256'] = sha(screen.encode())
                        row['busy_pane'] = screen
                observed = command(['ondesk', 'return', 'observe', request_id], False)
                if not observed.returncode:
                    last_observation = json.loads(observed.stdout)
                    if last_observation['read_claim_observed']:
                        row['read_claim_observed'] = True
                        print('NATIVE_STAGE ' + mode + ' read_claim_observed', flush=True)
                        break
                time.sleep(1)
            row['observation'] = last_observation
            screen = pane()
            row['final_pane'] = screen
            row['final_pane_sha256'] = sha(screen.encode())
            package_bytes, plan_bytes = package.read_bytes(), plan.read_bytes()
            token = sha(b'forager.ondesk.return.ack.v1\0' + ticket['nonce'].encode()
                        + len(package_bytes).to_bytes(8, 'big') + package_bytes
                        + len(plan_bytes).to_bytes(8, 'big') + plan_bytes)
            row['expected_marker'] = 'FORAGER_RETURN_ACK ' + request_id + ' ' + token
            row['provider_trace'] = provider_trace(codex_home)
            row['remaining_work_observed'] = any(
                'native-fixture-followup' in part.get('text', '')
                for frame in row['provider_trace']['frames']
                for payload in [frame.get('payload', {})]
                if payload.get('type') == 'message' and payload.get('role') == 'assistant'
                for part in payload.get('content', []) if isinstance(part, dict)
            )
            replay = json.loads(command(send).stdout)
            assert replay['send_attempts'] == 1 and not replay['new_send_attempt']
            row['replay_without_new_input'] = True
            rows = json.loads((profile / 'ondesk_return_deliveries.v1.json').read_text())['entries']
            assert len([x for x in rows if x['ticket']['request_id'] == request_id]) == 1
            tmux(['has-session', '-t', canary])
            row['unrelated_session_survived'] = True
            save()
            assert row['read_claim_observed'], 'native file acknowledgement was not observed; no retry'
            assert row['busy_refused'], 'native busy guard was not observed'
            assert row['remaining_work_observed'], 'model did not report the fixture remaining work'
            tmux(['kill-session', '-t', name])
        result['qualification_pass'] = True
    except Exception as exc:
        result['qualification_pass'] = False
        result['error'] = str(exc)
        raise
    finally:
        try:
            save()
        finally:
            for instance in sessions:
                name = 'forager_' + instance['title'] + '_' + instance['id'][:8]
                tmux(['kill-session', '-t', name], False)
                auth = codex_home_root / ('forager-native-' + instance['id']) / 'auth.json'
                auth.unlink(missing_ok=True)
            tmux(['kill-session', '-t', canary], False)
        print(json.dumps(sanitized(result)), flush=True)


if __name__ == '__main__':
    main()
