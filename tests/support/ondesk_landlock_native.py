"""Opt-in native TUI reading through Forager's integrated per-pane restriction."""
import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def verify_native_trace(frames, message, expected_marker=None):
    payloads = [frame['payload'] for frame in frames]
    calls = [p for p in payloads if p.get('type') in ('function_call', 'custom_tool_call')]
    assert len(calls) == 1, 'expected exactly one native model tool call'
    call = calls[0]
    if call['type'] == 'function_call':
        assert call['name'] == 'exec_command', 'unexpected function tool'
        arguments = json.loads(call['arguments'])
        assert set(arguments) == {'cmd'}, 'unexpected command options'
        assert shlex.split(arguments['cmd']) == ['python3', '-I', '-S', 'read_return.py']
    else:
        # Recognize only this bounded code-mode wrapper, never evaluate provider code.
        assert call['name'] == 'exec', 'unexpected custom tool'
        assert re.fullmatch(
            r'''\s*(?:const|let)\s+(?P<variable>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*await\s+'''
            r'''tools\s*\.\s*exec_command\s*\(\s*\{\s*(?:cmd|"cmd"|'cmd')\s*:\s*'''
            r'''(?P<quote>["'])python3 -I -S read_return\.py(?P=quote)\s*\}\s*\)\s*;\s*'''
            r'''text\s*\(\s*(?P=variable)\s*\.\s*output\s*\)\s*;?\s*''',
            call['input']), 'unexpected code-mode command'
    outputs = [p for p in payloads if p.get('type') in
               ('function_call_output', 'custom_tool_call_output') and p.get('call_id') == call['call_id']]
    assert len(outputs) == 1, 'missing or duplicated tool output'
    output = outputs[0]['output']
    texts = [output] if isinstance(output, str) else [part.get('text', '') for part in output]
    reports = []
    for text in texts:
        for line in text.splitlines():
            try:
                report = json.loads(line)
            except ValueError:
                continue
            if isinstance(report, dict) and 'ack_line' in report:
                reports.append(report)
    assert len(reports) == 1, 'missing or duplicated helper report'
    report = reports[0]
    assert report['denied_reads'] == dict(outside=True, symlink=True, host_auth=True, proc_alias=True)
    assert report['outside_write_denied'] is True and report['descendant_read_denied'] is True
    assert report['task_execution_performed'] is False and report['plan_update_authorized'] is False
    assert report['remaining_work'] == 'native-fixture-followup'
    for key in ['package_sha256', 'plan_sha256']:
        assert report[key] == message[key], 'helper input hash mismatch'
    marker = report['ack_line']
    assert re.fullmatch('FORAGER_RETURN_ACK ' + re.escape(message['request_id']) + r' [0-9a-f]{64}', marker)
    if expected_marker is not None:
        assert marker == expected_marker, 'independently recomputed ACK mismatch'
    final = '\n'.join(part.get('text', '') for p in payloads
                      if p.get('type') == 'message' and p.get('role') == 'assistant'
                      and p.get('phase') == 'final_answer' for part in p.get('content', []))
    assert marker in final and 'native-fixture-followup' in final, 'assistant final answer missing'
    deliveries = []
    for p in payloads:
        if p.get('type') != 'message' or p.get('role') != 'user':
            continue
        for part in p.get('content', []):
            try:
                value = json.loads(part.get('text', ''))
            except ValueError:
                continue
            if isinstance(value, dict) and value.get('schema') == 'ondesk_return_delivery_message.v1':
                deliveries.append(value)
    assert deliveries == [message], 'expected one exact delivered model message'
    return dict(observed_tool_calls=calls, helper_report=report, expected_marker=marker,
                provider_delivery_count=1)


def audit_saved_trial(evidence):
    original = json.loads((evidence / 'TEST_SUMMARY.json').read_text())
    frames = json.loads((evidence / 'provider-trace.json').read_text())
    ticket = original['ticket']
    messages = []
    for frame in frames:
        p = frame['payload']
        if p.get('type') == 'message' and p.get('role') == 'user':
            for part in p.get('content', []):
                try:
                    value = json.loads(part.get('text', ''))
                except ValueError:
                    continue
                if isinstance(value, dict) and value.get('schema') == 'ondesk_return_delivery_message.v1':
                    messages.append(value)
    assert len(messages) == 1
    message = messages[0]
    for key in ['request_id', 'nonce', 'package_sha256']:
        assert message[key] == ticket[key]
    assert message['plan_sha256'] == ticket['current_plan_sha256']
    assert message['package_path'] == ticket['package'] and message['plan_path'] == ticket['owning_plan']
    assert original['native_user_turns_sent'] == 1
    assert original['transport_receipt']['send_attempts'] == 1
    assert original['observation']['state'] == 'agent_read_claim_observed'
    assert original['observation']['read_claim_observed'] is True
    for key in ['permission_refused_without_input', 'busy_refused', 'temporary_auth_removed',
                'host_auth_unchanged', 'canonical_nano_todo_unchanged', 'installed_cli_unchanged',
                'outside_canary_unchanged', 'frozen_artifacts_unchanged', 'owned_session_stopped']:
        assert original[key] is True, key
    manifest_path = evidence / 'INPUT_MANIFEST.json'
    exact_bytes_verified = manifest_path.is_file()
    expected_marker = None
    if exact_bytes_verified:
        manifest = json.loads(manifest_path.read_text())
        saved = {}
        for path, item in manifest.items():
            source = (evidence / item['file']).resolve()
            assert source.is_relative_to(evidence.resolve()), 'saved input path escaped evidence'
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            assert digest == item['sha256'] == original['input_sha256'][path]
            saved[path] = data
        package = saved[message['package_path']]
        plan = saved[message['plan_path']]
        assert hashlib.sha256(package).hexdigest() == message['package_sha256']
        assert hashlib.sha256(plan).hexdigest() == message['plan_sha256']
        assert json.loads(saved[str(Path(original['root']) / 'RETURN_ENVELOPE.json')]) == message
        data = (b'forager.ondesk.return.ack.v1\0' + message['nonce'].encode()
                + len(package).to_bytes(8, 'big') + package + len(plan).to_bytes(8, 'big') + plan)
        expected_marker = 'FORAGER_RETURN_ACK ' + message['request_id'] + ' ' + hashlib.sha256(data).hexdigest()
    verified = verify_native_trace(frames, message, expected_marker)
    full_evidence = exact_bytes_verified and original.get('replay_without_new_input', False) and original.get('unrelated_owned_session_survived', False)
    limitations = ['Saved-evidence validation is not an additional live run']
    if not original['qualification_pass']:
        limitations.append('Original live test failure is retained')
    if not exact_bytes_verified:
        limitations.append('Exact input bytes are unavailable; no independent saved-byte ACK recomputation')
    if not original.get('replay_without_new_input', False):
        limitations.append('Live replay was not verified')
    if not original.get('unrelated_owned_session_survived', False):
        limitations.append('Unrelated-session survival was not verified')
    result = dict(schema='forager_landlock_native_saved_trace_audit_r12.v1',
                  saved_trace_audit_pass=True, original_test_pass=original['qualification_pass'],
                  original_error=original.get('error'), native_user_turns_sent=1,
                  model_reinvoked=False, verify_native_trace=verified,
                  independent_ack_byte_recomputation=exact_bytes_verified,
                  live_replay_exercised=original.get('replay_without_new_input', False),
                  unrelated_session_survival_observed=original.get('unrelated_owned_session_survived', False),
                  full_native_evidence_audit_pass=full_evidence,
                  full_native_qualification_pass=original['qualification_pass'] and full_evidence,
                  limitations=limitations)
    write_json(evidence / 'SAVED_TRACE_AUDIT.json', result)
    print(json.dumps(result))


def boundary(root):
    config = json.loads((root / 'RUN_CONFIG.json').read_text())
    denied = {}
    for label, path in [('outside', Path(config['outside'])), ('symlink', root / 'native-escape'),
                        ('host_auth', Path(config['auth_source'])),
                        ('proc_alias', Path(f"/proc/{os.getppid()}/root{config['outside']}"))]:
        try:
            path.read_bytes()
        except OSError as error:
            assert error.errno == errno.EACCES, (label, error.errno)
            denied[label] = True
        else:
            raise AssertionError('outside read allowed: ' + label)
    try:
        Path(config['outside']).write_bytes(b'bypass')
    except OSError as error:
        assert error.errno == errno.EACCES
    else:
        raise AssertionError('outside write allowed')
    child = subprocess.run(['/bin/sh', '-c', 'cat "$1"', 'probe', config['outside']],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert child.returncode != 0, 'descendant read escaped'
    return dict(denied_reads=denied, outside_write_denied=True, descendant_read_denied=True)


def read_return(root):
    result = boundary(root)
    message = json.loads((root / 'RETURN_ENVELOPE.json').read_text())
    package = Path(message['package_path']).read_bytes()
    plan = Path(message['plan_path']).read_bytes()
    assert hashlib.sha256(package).hexdigest() == message['package_sha256']
    assert hashlib.sha256(plan).hexdigest() == message['plan_sha256']
    context = json.loads(package.split(b'\n```json\n', 1)[1].split(b'\n```\n\n', 1)[0])
    assert context['pending_decisions'] == []
    assert context['current_plan_sha256'] == message['plan_sha256']
    assert b'native-fixture-followup' in package
    h = hashlib.sha256(b'forager.ondesk.return.ack.v1\0' + message['nonce'].encode())
    for data in [package, plan]:
        h.update(len(data).to_bytes(8, 'big'))
        h.update(data)
    result.update(package_sha256=message['package_sha256'], plan_sha256=message['plan_sha256'],
                  ack_line='FORAGER_RETURN_ACK ' + message['request_id'] + ' ' + h.hexdigest(),
                  remaining_work='native-fixture-followup', task_execution_performed=False,
                  plan_update_authorized=False)
    return result


def worker(root):
    config = json.loads((root / 'RUN_CONFIG.json').read_text())
    write_json(root / 'WORKER_PREFLIGHT.json', boundary(root))
    argv = [config['codex'], '--no-daemon', '--no-alt-screen', '-s', 'danger-full-access',
            '-a', 'never', '-m', config['model'], '-C', str(root),
            '--disable', 'memories', '--disable', 'multi_agent',
            '-c', 'model_reasoning_effort="low"', '-c', 'check_for_update_on_startup=false']
    write_json(root / 'NATIVE_INVOCATION.json', dict(argv=argv, native_user_turn_budget=1,
               filesystem_boundary='inherited_forager_per_pane_landlock', shared_daemon=False))
    env = dict(os.environ, CODEX_HOME=str(root / '.codex'), SHELL='/bin/bash')
    os.execve(config['codex'], argv, env)


def main():
    assert os.environ.get('FORAGER_RETURN_LANDLOCK_NATIVE_TRIAL') == '1'
    binary, home, root, plan, reference = map(Path, sys.argv[1:])
    evidence = Path(os.environ['FORAGER_RETURN_LANDLOCK_NATIVE_EVIDENCE'])
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    codex = Path(os.environ['FORAGER_RETURN_LANDLOCK_CODEX'])
    auth_source = Path(os.environ['FORAGER_RETURN_LANDLOCK_AUTH_FILE'])
    model = os.environ['FORAGER_RETURN_LANDLOCK_MODEL']
    profile = home / '.config/forager/profiles/default'
    auth_home = root / '.codex'
    auth_home.mkdir(mode=0o700)
    auth_copy = auth_home / 'auth.json'
    auth_before = sha(auth_source)
    nano = Path('/home/kimyoungjin06/Desktop/Workspace/1.1.4.KISTI_NanoClustering/TODO.md')
    nano_before = sha(nano)
    installed = Path('/home/kimyoungjin06/.local/bin/forager')
    installed_before = sha(installed)
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'))
    for key in ['AGENT_OF_EMPIRES_PROFILE', 'FORAGER_PROFILE', 'FORAGER_DEBUG', 'AGENT_OF_EMPIRES_DEBUG']:
        env.pop(key, None)
    identifier = uuid.uuid4().hex
    title = 'read_policy_native'
    name = 'forager_' + title + '_' + identifier[:8]
    canary = 'forager_test_r12_' + uuid.uuid4().hex[:12]
    outside = home / 'native-private-canary'
    outside.write_bytes(b'outside-private-native')
    (root / 'native-escape').symlink_to(outside)
    frozen = {str(p): p.read_bytes() for p in [plan, root / 'night-review.json',
              profile / 'offdesk_shifts.v1.json', profile / 'offdesk_tasks.json']}
    secrets = []

    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and len(value) > 16:
            secrets.append(value)

    collect(json.loads(auth_source.read_text()))

    def sanitize(value):
        if isinstance(value, dict):
            return {k: sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, '[REDACTED_CREDENTIAL]')
        return value

    result = dict(schema='forager_landlock_native_return_r12.v1', qualification_pass=False,
                  mode='existing_session', requested_model=model, native_tui=True,
                  native_user_turns_sent=0, automatic_retry=False, installed_runtime_changed=False,
                  task_execution_authorized=False, canonical_plan_updated=False,
                  forager_sha256=sha(binary), codex_sha256=sha(codex), driver_sha256=sha(__file__),
                  root=str(root), profile=str(profile), tmux_session=name,
                  limitations=['Shared tmux server and IPC outside child restriction',
                               'Whole staged workdir including temporary auth is visible',
                               'Network metadata preopened descriptors hardlink aliases nested mounts not covered',
                               'One protected existing-session context return, not a reopened prior Codex conversation',
                               'Read claim does not authorize tasks or acceptance'])

    def command(args, checked=True):
        out = subprocess.run([str(binary), *map(str, args)], env=env,
                             capture_output=True, text=True, timeout=30)
        if checked and out.returncode:
            raise AssertionError(out.stderr.strip())
        return out

    def tmux(args, checked=True):
        return subprocess.run(['tmux', *args], check=checked, capture_output=True,
                              text=True, timeout=15)

    def pane():
        return tmux(['capture-pane', '-p', '-t', name + ':0.0', '-S', '-200']).stdout

    def trace():
        frames = []
        for path in sorted(auth_home.glob('sessions/**/*.jsonl')):
            data = path.read_bytes()
            assert len(data) <= 4 * 1024 * 1024
            for line in data.splitlines():
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get('type') == 'response_item':
                    frames.append(frame)
        return frames

    try:
        shutil.copyfile(auth_source, auth_copy)
        auth_copy.chmod(0o600)
        (auth_home / 'config.toml').write_text(
            'cli_auth_credentials_store = "file"\nmodel_reasoning_effort = "low"\n'
            '[projects.' + json.dumps(str(root)) + ']\ntrust_level = "trusted"\n')
        (root / 'AGENTS.md').write_text(
            'This is a bounded native return reading qualification. After receiving the '
            'ondesk_return_delivery_message.v1 JSON, use the shell command tool exactly once '
            'to execute: python3 -I -S read_return.py\n'
            'If using the code-mode exec tool, use this fixed wrapper: '
            'const r = await tools.exec_command({cmd:"python3 -I -S read_return.py"}); text(r.output);\n'
            'The pre-staged RETURN_ENVELOPE.json contains that exact delivery message. '
            'The helper reads both referenced files, verifies hashes, checks denied outside '
            'reads/writes and descendants, and calculates the acknowledgement. Print its '
            'ack_line unchanged and report remaining_work. Do not run tasks, edit files, '
            'read authentication files, spawn agents, or call other tools.\n')
        shutil.copyfile(__file__, root / 'native_trial.py')
        shutil.copyfile(__file__, root / 'read_return.py')
        write_json(root / 'RUN_CONFIG.json', dict(codex=str(codex), model=model,
                   outside=str(outside), auth_source=str(auth_source)))
        write_json(root / 'RETURN_ENVELOPE.json', {})
        for path in [root / 'AGENTS.md', root / 'native_trial.py', root / 'read_return.py', root / 'RUN_CONFIG.json']:
            frozen[str(path)] = path.read_bytes()
        launch_command = 'exec ' + shlex.join(['/usr/bin/python3', '-I', '-S',
                                               str(root / 'native_trial.py'), '--worker', str(root)])
        instance = dict(id=identifier, title=title, project_path=str(root), command=launch_command,
                        tool='codex', status='idle', created_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        assert not (profile / 'sessions.json').exists(), 'owned fixture already contains session registrations'
        write_json(profile / 'sessions.json', [instance])
        tmux(['new-session', '-d', '-s', canary, '/bin/sleep', '900'])
        launched = json.loads(command(['ondesk', 'return', 'launch-restricted', '--session', identifier]).stdout)
        result['launch'] = launched
        receipt = Path(launched['read_policy_receipt'])
        tmux(['resize-window', '-t', name + ':0', '-x', '160', '-y', '55'])
        package = root / 'existing-native-package.md'
        context = json.loads(command(['ondesk', 'prompt-package', root, '--morning-review', reference,
                            '--owning-plan', plan, '--project-key', 'project', '--resume-session', identifier,
                            '--output', package, '--json']).stdout)['morning_return']
        assert context['resume_mode'] == 'existing_session' and not context['pending_decisions']
        frozen[str(package)] = package.read_bytes()

        def preview():
            return command(['ondesk', 'return', 'preview', '--package', package,
                            '--session', identifier, '--read-policy-receipt', receipt], False)

        deadline = time.monotonic() + 120
        out = None
        while time.monotonic() < deadline:
            out = preview()
            result['startup_preview_error'] = out.stderr.strip()
            result['startup_pane'] = pane()
            if not out.returncode:
                break
            if tmux(['has-session', '-t', name], False).returncode:
                break
            time.sleep(.5)
        assert out is not None and not out.returncode, 'native TUI did not expose a safe prompt: ' + result.get('startup_preview_error', '')
        result['ready_pane'] = pane()
        result['worker_preflight'] = json.loads((root / 'WORKER_PREFLIGHT.json').read_text())
        result['native_invocation'] = json.loads((root / 'NATIVE_INVOCATION.json').read_text())
        result['native_pid_executable'] = os.readlink('/proc/' + str(launched['binding']['receipt']['child_pid']) + '/exe')
        assert result['native_pid_executable'] == str(codex)
        print('R12_NATIVE_STAGE empty_prompt_ready', flush=True)
        value = json.loads(out.stdout)
        ticket = value['ticket']
        ticket_path = home / 'native-return-ticket.json'
        write_json(ticket_path, ticket)
        message = json.loads(value['message_preview'])
        write_json(root / 'RETURN_ENVELOPE.json', message)
        frozen[str(root / 'RETURN_ENVELOPE.json')] = (root / 'RETURN_ENVELOPE.json').read_bytes()
        send = ['ondesk', 'return', 'send', ticket_path, '--expected-preview-sha256',
                value['preview_sha256'], '--confirm-input']
        result['ticket'] = ticket
        result['input_sha256'] = {path: hashlib.sha256(data).hexdigest() for path, data in frozen.items()}
        # A native menu is exercised without submitting any model prompt.
        tmux(['send-keys', '-t', name + ':0.0', '-l', '/permissions'])
        time.sleep(.5)
        tmux(['send-keys', '-t', name + ':0.0', 'Enter'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            screen = pane()
            blocked = preview()
            if ('permission' in screen.lower() or 'approval' in screen.lower()) and ('enter to select' in screen.lower() or '1.' in screen) and blocked.returncode:
                break
            time.sleep(.5)
        else:
            raise AssertionError('native permissions menu not observed/refused')
        result['permission_pane'] = screen
        refused = command(send, False)
        assert refused.returncode and 'return prompt changed or busy' in refused.stderr
        assert not (profile / 'ondesk_return_deliveries.v1.json').exists()
        result['permission_refused_without_input'] = True
        tmux(['send-keys', '-t', name + ':0.0', 'Escape'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            screen = pane()
            if any(line.strip() == '› /permissions' for line in screen.splitlines()):
                tmux(['send-keys', '-t', name + ':0.0', 'C-u'])
            out = preview()
            if not out.returncode:
                break
            time.sleep(.5)
        assert not out.returncode, 'native menu did not return to empty prompt'
        value = json.loads(out.stdout)
        ticket = value['ticket']
        write_json(ticket_path, ticket)
        write_json(root / 'RETURN_ENVELOPE.json', json.loads(value['message_preview']))
        frozen[str(root / 'RETURN_ENVELOPE.json')] = (root / 'RETURN_ENVELOPE.json').read_bytes()
        result['ticket'] = ticket
        send = ['ondesk', 'return', 'send', ticket_path, '--expected-preview-sha256',
                value['preview_sha256'], '--confirm-input']
        queued = json.loads(command(send).stdout)
        result['transport_receipt'] = queued
        assert queued['state'] == 'input_queued' and queued['send_attempts'] == 1
        result['native_user_turns_sent'] = 1
        print('R12_NATIVE_STAGE input_queued ' + ticket['request_id'], flush=True)
        deadline = time.monotonic() + 180
        observed = None
        result['busy_refused'] = False
        while time.monotonic() < deadline:
            screen = pane()
            if 'esc to interrupt' in screen.lower() or '• Working' in screen:
                blocked = preview()
                if blocked.returncode and 'no safe empty task prompt' in blocked.stderr:
                    result['busy_refused'] = True
                    result['busy_pane'] = screen
            out = command(['ondesk', 'return', 'observe', ticket['request_id']], False)
            if not out.returncode:
                observed = json.loads(out.stdout)
                if observed['read_claim_observed']:
                    break
            time.sleep(.5)
        result['observation'] = observed
        result['final_pane'] = pane()
        replay = json.loads(command(send).stdout)
        result['replay_receipt'] = replay
        assert replay['send_attempts'] == 1 and not replay['new_send_attempt']
        result['replay_without_new_input'] = True
        entries = json.loads((profile / 'ondesk_return_deliveries.v1.json').read_text())['entries']
        result['delivery_ledger'] = entries
        assert len(entries) == 1 and entries[0]['state'] == 'agent_read_claim_observed'
        tmux(['has-session', '-t', canary])
        result['unrelated_owned_session_name'] = canary
        result['unrelated_owned_session_survived'] = True
        frames = trace()
        result['provider_trace'] = frames
        write_json(evidence / 'provider-trace.json', sanitize(frames))
        assert observed and observed['read_claim_observed'], 'native read acknowledgement absent; no retry'
        package_bytes, plan_bytes = package.read_bytes(), plan.read_bytes()
        h = hashlib.sha256(b'forager.ondesk.return.ack.v1\0' + ticket['nonce'].encode())
        for data in [package_bytes, plan_bytes]:
            h.update(len(data).to_bytes(8, 'big'))
            h.update(data)
        marker = 'FORAGER_RETURN_ACK ' + ticket['request_id'] + ' ' + h.hexdigest()
        result.update(verify_native_trace(frames, json.loads(value['message_preview']), marker))
        assert result['busy_refused'], 'native busy refusal not observed'
        result['qualification_pass'] = True
    except Exception as error:
        result['error'] = str(error)
        try:
            result['failure_pane'] = pane()
            result['provider_trace'] = trace()
        except (subprocess.CalledProcessError, OSError):
            pass
        raise
    finally:
        cleanup_errors = []
        for operation in [lambda: command(['session', 'stop', identifier], False),
                          lambda: tmux(['kill-session', '-t', name], False),
                          lambda: tmux(['kill-session', '-t', canary], False)]:
            try:
                operation()
            except (OSError, subprocess.TimeoutExpired) as error:
                cleanup_errors.append(str(error))
        if auth_copy.exists():
            collect(json.loads(auth_copy.read_text()))
        auth_copy.unlink(missing_ok=True)
        result['temporary_auth_removed'] = not auth_copy.exists()
        result['host_auth_unchanged'] = sha(auth_source) == auth_before
        result['canonical_nano_todo_unchanged'] = sha(nano) == nano_before
        result['installed_cli_unchanged'] = sha(installed) == installed_before
        result['outside_canary_unchanged'] = outside.read_bytes() == b'outside-private-native'
        result['frozen_artifacts_unchanged'] = all(Path(p).read_bytes() == b for p, b in frozen.items())
        result['input_sha256'] = {path: hashlib.sha256(data).hexdigest() for path, data in frozen.items()}
        result['owned_session_stopped'] = bool(tmux(['has-session', '-t', name], False).returncode)
        result['cleanup_errors'] = cleanup_errors
        cleanup_pass = not cleanup_errors and all(result[key] for key in [
            'temporary_auth_removed', 'host_auth_unchanged', 'canonical_nano_todo_unchanged',
            'installed_cli_unchanged', 'outside_canary_unchanged', 'frozen_artifacts_unchanged',
            'owned_session_stopped'])
        result['qualification_pass'] = result['qualification_pass'] and cleanup_pass
        # Preserve exact input bytes even when the enclosing Rust TempDir is dropped.
        artifacts = evidence / 'inputs'
        artifacts.mkdir(exist_ok=True)
        manifest = {}
        for index, (path, data) in enumerate(frozen.items()):
            assert not any(secret.encode() in data for secret in secrets), 'credential in frozen input'
            saved = artifacts / str(index)
            saved.write_bytes(data)
            manifest[path] = dict(file=str(saved.relative_to(evidence)), sha256=hashlib.sha256(data).hexdigest())
        write_json(evidence / 'INPUT_MANIFEST.json', manifest)
        write_json(evidence / 'provider-trace.json', sanitize(result.get('provider_trace', [])))
        write_json(evidence / 'TEST_SUMMARY.json', sanitize(result))
        print(json.dumps(sanitize({k: v for k, v in result.items() if k != 'provider_trace'})), flush=True)
        assert cleanup_pass, 'native qualification cleanup or unchanged-input check failed'


if __name__ == '__main__':
    if sys.argv[1:2] == ['--audit']:
        audit_saved_trial(Path(sys.argv[2]))
    elif sys.argv[1:2] == ['--worker']:
        worker(Path(sys.argv[2]))
    elif Path(__file__).name == 'read_return.py':
        print(json.dumps(read_return(Path.cwd())))
    else:
        main()
