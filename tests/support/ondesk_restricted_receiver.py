"""Owned synthetic prompt receiver for the per-pane read policy qualification."""
import errno
import hashlib
import json
import pathlib
import re
import select
import subprocess
import sys


receipt, scene, outside = map(pathlib.Path, sys.argv[1:])
# An unrestricted replacement is deliberately started at the end of the test;
# only the original restricted receiver reaches the ready prompt.
for path in [outside, pathlib.Path('/etc/passwd')]:
    try:
        path.read_bytes()
    except OSError as error:
        assert error.errno == errno.EACCES
    else:
        raise AssertionError('outside file readable')
try:
    outside.write_bytes(b'bypass')
except OSError as error:
    assert error.errno == errno.EACCES
else:
    raise AssertionError('outside write allowed')
assert subprocess.run(['/bin/sh', '-c', 'cat "$1"', 'probe', str(outside)],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
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
        break
    message = json.loads(line)
    assert message['schema'] == 'ondesk_return_delivery_message.v1'
    package = pathlib.Path(message['package_path']).read_bytes()
    plan = pathlib.Path(message['plan_path']).read_bytes()
    assert hashlib.sha256(package).hexdigest() == message['package_sha256']
    assert hashlib.sha256(plan).hexdigest() == message['plan_sha256']
    context = json.loads(re.search(rb'```json\n(.*?)\n```', package, re.S)[1])
    assert context['pending_decisions'] == []
    assert context['current_plan_sha256'] == message['plan_sha256']
    h = hashlib.sha256(b'forager.ondesk.return.ack.v1\0' + message['nonce'].encode())
    for data in [package, plan]:
        h.update(len(data).to_bytes(8, 'big'))
        h.update(data)
    evidence = dict(request_id=message['request_id'], resume_mode=context['resume_mode'],
                    package_sha256=message['package_sha256'], plan_sha256=message['plan_sha256'],
                    acknowledgement_token=h.hexdigest(), task_execution_performed=False,
                    real_provider_used=False, outside_read_write_descendant_denied=True)
    with receipt.open('a') as out:
        out.write(json.dumps(evidence) + '\n')
    print('FORAGER_RETURN_ACK ' + message['request_id'] + ' ' + h.hexdigest(), flush=True)
    print(current, flush=True)
