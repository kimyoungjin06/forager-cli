"""Disposable receiver; the optional real reader requires separate trial mounts."""
import hashlib
import json
import os
import pathlib
import re
import select
import shutil
import signal
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receive(receipt, binary):
    print("READY_FOR_RETURN", flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        package = pathlib.Path(message["package_path"])
        plan = pathlib.Path(message["plan_path"])
        result = {"request_id": message["request_id"], "acknowledged_read": False}
        if digest(package) != message["package_sha256"]:
            result["reason"] = "package_changed"
        elif digest(plan) != message["plan_sha256"]:
            result["reason"] = "plan_changed"
        else:
            context = json.loads(re.search(r"```json\n(.*?)\n```", package.read_text(), re.S)[1])
            current = json.loads(subprocess.check_output([
                binary, "ondesk", "plan-inspect", str(plan), "--project-key", "project"
            ], text=True))
            task = next(row for row in current["plan"]["tasks"]
                        if row["task_id"] == message["task_id"])
            assert context["current_plan_sha256"] == current["source_sha256"] == message["plan_sha256"]
            assert context["pending_decisions"] == []
            assert task["review_state"] == "accepted"
            if context["resume_mode"] == "existing_session":
                assert context["session_id"] == message["session_id"]
            result.update(acknowledged_read=True, plan_sha256=message["plan_sha256"],
                          package_sha256=message["package_sha256"], task_id=task["task_id"],
                          review_state=task["review_state"], resume_mode=context["resume_mode"])
        with receipt.open("a") as out:
            out.write(json.dumps(result) + "\n")
            out.flush()
        print("ACK_RETURN:" + json.dumps(result), flush=True)


def qualify(status_path, envelope_path, receipt):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
    from telegram_operator.dispatch import apply_session_text_input
    envelope = json.loads(envelope_path.read_text())
    status = json.loads(status_path.read_text())
    row = status["sessions"][0]
    original = status_path.read_bytes()
    identity = row["remote_session_identity"]
    message = json.dumps(envelope)
    assert len(message) < 2000

    def send(payload=message, **extra):
        return apply_session_text_input(
            "forager", "default", row["id"], payload, status_file=status_path,
            require_exact_id=True, expected_identity=identity,
            require_remote_identity=True, **extra)

    stale = send(expected_hash="stale-fixture-prompt")
    assert not stale["ok"] and stale["error"] == "prompt_changed", stale
    row["status"] = "stopped"
    status_path.write_text(json.dumps(status))
    stopped = send()
    assert not stopped["ok"] and stopped["error"] == "session_not_live:stopped", stopped
    status_path.write_bytes(original)
    wrong = apply_session_text_input(
        "forager", "default", row["id"], message, status_file=status_path,
        require_exact_id=True, expected_identity={**identity, "session_id": "wrong-target"},
        require_remote_identity=True)
    assert not wrong["ok"] and "target_session_id_mismatch" in wrong["error"], wrong
    assert not receipt.exists(), "a rejected action reached the receiver"

    def records(count):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            rows = [json.loads(x) for x in receipt.read_text().splitlines()] if receipt.exists() else []
            if len(rows) == count:
                return rows
            time.sleep(.05)
        raise AssertionError("receiver acknowledgement missing")

    package = pathlib.Path(envelope["package_path"])
    plan = pathlib.Path(envelope["plan_path"])
    plan_before = plan.read_bytes()
    plan.write_bytes(plan_before + b"\nconcurrent fixture edit\n")
    stale_plan_delivery = send()
    assert stale_plan_delivery["ok"], stale_plan_delivery
    rejected = records(1)
    assert rejected[0]["acknowledged_read"] is False and rejected[0]["reason"] == "plan_changed"
    plan.write_bytes(plan_before)
    before = package.read_bytes()
    package.write_bytes(before + b"\nchanged after export\n")
    changed_delivery = send()
    assert changed_delivery["ok"], changed_delivery
    rejected = records(2)
    assert rejected[-1]["acknowledged_read"] is False and rejected[-1]["reason"] == "package_changed"
    package.write_bytes(before)
    delivered = send()
    assert delivered["ok"], delivered
    rows = records(3)
    ack = rows[-1]
    assert ack["acknowledged_read"] is True
    assert ack["package_sha256"] == digest(package)
    assert ack["plan_sha256"] == digest(pathlib.Path(envelope["plan_path"]))
    print(json.dumps({"acknowledged_read": True, "ack_count": sum(x["acknowledged_read"] for x in rows),
                      "stale_prompt_rejected": True, "stopped_target_rejected": True,
                      "wrong_identity_rejected": True, "receiver_rejected_changed_package": True,
                      "receiver_rejected_changed_plan": True,
                      "delivery_status": delivered["delivery_status"], "ack": ack}))


def receive_production(receipt, binary, scene):
    previous = None
    while True:
        current = scene.read_text()
        if current != previous:
            print('\033[2J\033[H' + current, flush=True)
            previous = current
        if not select.select([sys.stdin], [], [], .05)[0]:
            continue
        line = sys.stdin.readline()
        if not line:
            return
        message = json.loads(line)
        assert message['schema'] == 'ondesk_return_delivery_message.v1'
        package = pathlib.Path(message['package_path'])
        plan = pathlib.Path(message['plan_path'])
        assert digest(package) == message['package_sha256']
        assert digest(plan) == message['plan_sha256']
        context = json.loads(re.search(r'```json\n(.*?)\n```', package.read_text(), re.S)[1])
        inspected = json.loads(subprocess.check_output([
            binary, 'ondesk', 'plan-inspect', str(plan), '--project-key', context['project_key']
        ], text=True))
        assert inspected['source_sha256'] == context['current_plan_sha256']
        assert context['pending_decisions'] == []
        h = hashlib.sha256(b'forager.ondesk.return.ack.v1\0' + message['nonce'].encode())
        for path in [package, plan]:
            data = path.read_bytes()
            h.update(len(data).to_bytes(8, 'big'))
            h.update(data)
        evidence = {'request_id': message['request_id'], 'resume_mode': context['resume_mode'],
                    'package_sha256': digest(package), 'plan_sha256': digest(plan),
                    'acknowledgement_token': h.hexdigest(), 'task_execution_performed': False}
        marker = 'FORAGER_RETURN_ACK ' + message['request_id'] + ' ' + h.hexdigest()
        if os.environ.get('FORAGER_RETURN_REAL_TRIAL') == '1' and context['resume_mode'] == 'existing_session':
            trial = real_read_trial(message, package.parent, marker)
            evidence['real_harness_trial'] = trial
            marker = trial.get('verified_ack_line', '')
        else:
            evidence['real_provider_used'] = False
        with receipt.open('a') as out:
            out.write(json.dumps(evidence) + '\n')
        if marker:
            print(marker, flush=True)
        else:
            print('REAL_RETURN_READ_FAILED ' + json.dumps(evidence), flush=True)
        print(current, flush=True)


def real_read_trial(message, workdir, expected_marker):
    """One bounded Codex exec invocation; never synthesize the model's acknowledgement."""
    assert pathlib.Path('/.dockerenv').is_file()
    assert not pathlib.Path('/var/run/docker.sock').exists()
    assert pathlib.Path('/trial-tools/codex').is_file()
    assert pathlib.Path('/trial-auth/auth.json').is_file()
    trial = pathlib.Path('/tmp/real-return-trial')
    trial.mkdir(mode=0o700)
    home = trial / 'codex-home'
    home.mkdir(mode=0o700)
    shutil.copyfile('/trial-auth/auth.json', home / 'auth.json')
    (home / 'auth.json').chmod(0o600)
    schema = trial / 'schema.json'
    schema.write_text(json.dumps({'type': 'object', 'properties': {
        'ack_line': {'type': 'string'}, 'package_sha256': {'type': 'string'},
        'plan_sha256': {'type': 'string'}, 'remaining_work': {'type': 'string'},
        'task_execution_performed': {'type': 'boolean'}},
        'required': ['ack_line', 'package_sha256', 'plan_sha256', 'remaining_work',
                     'task_execution_performed'], 'additionalProperties': False}))
    output = trial / 'last.json'
    prompt = (json.dumps(message) + '\nRead the two exact referenced files using tools. '
              'Verify their SHA256 values and compute the requested token from their exact bytes. '
              'Return only the required JSON. Describe remaining work from the saved context. '
              'Do not change any files or run task commands. Do not read authentication files. '
              'This is one context-reading trial, not task execution or user acceptance.')
    env = {**os.environ, 'CODEX_HOME': str(home), 'HOME': '/tmp/home',
           'PATH': '/trial-tools:' + os.environ['PATH']}
    args = ['/trial-tools/codex', '-a', 'never', 'exec', '--ignore-user-config',
            '--ignore-rules', '--ephemeral', '--sandbox', 'read-only',
            '--skip-git-repo-check', '--json', '--output-schema', str(schema),
            '-o', str(output), '-C', str(workdir), '-']
    started = time.monotonic()
    timed_out = False
    with (trial / 'events.jsonl').open('w') as out, (trial / 'stderr.log').open('w') as err:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                text=True, env=env, start_new_session=True)
        try:
            proc.communicate(prompt, timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
    result = {'real_provider_used': True, 'harness': 'codex_exec_via_controlled_receiver',
              'native_tui_qualified': False, 'invocations': 1, 'timeout_seconds': 120,
              'timed_out': timed_out, 'exit_code': proc.returncode,
              'elapsed_seconds': round(time.monotonic() - started, 2),
              'task_execution_performed': False, 'product_accepted': False}
    events = []
    for line in (trial / 'events.jsonl').read_text().splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    result['event_types'] = sorted({e.get('type', 'unknown') for e in events})
    result['usage'] = [e['usage'] for e in events if 'usage' in e]
    result['command_execution_count'] = sum(e.get('item', {}).get('type') == 'command_execution'
                                            and e.get('type') == 'item.completed' for e in events)
    stderr = (trial / 'stderr.log').read_text()
    result['stderr_sha256'] = hashlib.sha256(stderr.encode()).hexdigest()
    diagnostic = stderr + json.dumps(events)
    result['failure_signals'] = [name for name, pattern in [
        ('sandbox_permission_denied', r'(?:bwrap|sandbox)[^\n]{0,500}(?:Operation not permitted|Permission denied)'),
        ('unsupported_argument', r'unexpected argument|unrecognized option'),
        ('usage_limit', r'usage limit|quota exceeded|insufficient_quota'),
        ('authentication_error', r'Unauthorized|authentication failed|401 Unauthorized'),
        ('connection_error', r'connection refused|Connection reset|error sending request'),
    ] if re.search(pattern, diagnostic, re.I)]
    if proc.returncode == 0 and output.is_file():
        try:
            answer = json.loads(output.read_text())
            assert answer['ack_line'] == expected_marker
            assert answer['package_sha256'] == message['package_sha256']
            assert answer['plan_sha256'] == message['plan_sha256']
            assert answer['task_execution_performed'] is False
            assert result['command_execution_count'] > 0
            assert answer['remaining_work'].strip()
            result.update(verified_ack_line=answer['ack_line'], answer=answer)
        except (ValueError, KeyError, AssertionError):
            result['verification_failed'] = True
    # Raw provider logs and the disposable credential copy remain only in tmpfs.
    (home / 'auth.json').unlink(missing_ok=True)
    return result


if __name__ == "__main__":
    assert pathlib.Path("/.dockerenv").is_file(), "disposable container required"
    assert not pathlib.Path("/var/run/docker.sock").exists()
    if sys.argv[1] == "receive":
        receive(pathlib.Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "qualify":
        qualify(*(pathlib.Path(arg) for arg in sys.argv[2:]))
    elif sys.argv[1] == "receive-production":
        receive_production(pathlib.Path(sys.argv[2]), sys.argv[3], pathlib.Path(sys.argv[4]))
    else:
        raise SystemExit("unknown fixture mode")
