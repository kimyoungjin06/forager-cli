"""Source-bound deterministic metadata controller. No model or automatic resume."""
import argparse
import hashlib
import math
import os
from pathlib import Path
import resource
import signal
import stat
import subprocess
import sys
import time
import types

LOADED_SOURCE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RUNTIME = {"offdesk_metadata_dispatch.py", "offdesk_metadata_consumer.py",
           "offdesk_metadata_packet.py", "offdesk_resume_checkpoint.py", "native_worker_bootstrap.py"}
INPUTS = {"consumer.py", "validator.py", "adapter.py", "configuration.json", "input.jsonl",
          "manifest.json", "row_provenance.jsonl", "policy.json"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_identity(value):
    return (type(value) is str and 0 < len(value) <= 100 and value not in ('.', '..')
            and all(c.isascii() and (c.isalnum() or c in '_-.') for c in value))


def read(root, name, limit=8 * 1024 * 1024):
    parts = name.split('/')
    require(all(p and p not in ('.', '..') for p in parts), 'unsafe artifact path')
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY)
    try:
        for part in Path(root).parts[1:] + tuple(parts[:-1]):
            next_fd = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                    'invalid artifact size/type/link count')
            with os.fdopen(file_fd, 'rb', closefd=False) as stream:
                raw = stream.read(limit + 1)
            after = os.fstat(file_fd)
            current = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
            require(len(raw) <= limit and stamp(before) == stamp(after) == stamp(current), 'artifact changed during read')
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def decode(raw):
    import json
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def selection(root, expected):
    require(root.is_absolute() and root.resolve(strict=True) == root, 'noncanonical root')
    raw = read(root, 'METADATA_SELECTION.json', 65536)
    require(digest(raw) == expected, 'selection hash changed')
    cfg = decode(raw)
    require(set(cfg) == {'schema', 'executor_kind', 'project_key', 'task_id', 'request_id',
        'root_device', 'root_inode', 'runtime_files', 'input_files', 'starts_at', 'stops_at',
        'total_seconds', 'child_seconds', 'closeout_seconds', 'rows', 'batch_rows', 'read_policy_sha256'},
        'selection fields differ')
    require(cfg['schema'] == 'offdesk_metadata_selection.v1'
            and cfg['executor_kind'] == 'deterministic_local_no_model', 'unsupported executor')
    for name in ('project_key', 'task_id', 'request_id'):
        require(valid_identity(cfg[name]), 'invalid identity')
    info = root.stat()
    require((info.st_dev, info.st_ino) == (cfg['root_device'], cfg['root_inode']), 'root replaced')
    for name in ('total_seconds', 'child_seconds', 'closeout_seconds', 'rows', 'batch_rows'):
        require(type(cfg[name]) is int, 'integer bound required')
    require(1 <= cfg['rows'] <= 128 and 1 <= cfg['batch_rows'] <= 128
            and math.ceil(cfg['rows'] / cfg['batch_rows']) <= 8, 'row/batch bound exceeded')
    require(1 <= cfg['child_seconds'] <= 20 and 5 <= cfg['closeout_seconds'] <= 20
            and cfg['child_seconds'] + cfg['closeout_seconds'] <= cfg['total_seconds'] <= 120,
            'invalid controller limits')
    require(all(type(cfg[k]) in (int, float) and math.isfinite(cfg[k]) for k in ('starts_at', 'stops_at'))
            and 0 < cfg['stops_at'] - cfg['starts_at'] <= cfg['total_seconds'], 'invalid absolute window')
    runtime = cfg['runtime_files']
    require(set(runtime) == {'.runtime/' + name for name in RUNTIME} and set(cfg['input_files']) == INPUTS,
            'runtime/input inventory differs')
    values = {}
    for name, pin in {**runtime, **cfg['input_files']}.items():
        require(type(pin) is str and len(pin) == 64 and all(c in '0123456789abcdef' for c in pin), 'invalid SHA256')
        values[name] = read(root, name)
        require(digest(values[name]) == pin, 'source/input hash changed: ' + name)
    require(runtime['.runtime/offdesk_metadata_dispatch.py'] == LOADED_SOURCE, 'loaded controller changed')
    for copied, module in [('consumer.py', 'offdesk_metadata_consumer.py'),
                           ('validator.py', 'offdesk_resume_checkpoint.py'), ('adapter.py', 'offdesk_metadata_packet.py')]:
        require(values[copied] == values['.runtime/' + module], 'loaded component differs from declared copy')
    return cfg, values


def modules(root, values):
    loaded = {}
    for name in ('offdesk_resume_checkpoint', 'offdesk_metadata_consumer', 'offdesk_metadata_packet'):
        path = root / '.runtime' / (name + '.py')
        module = types.ModuleType(name)
        module.__file__ = str(path)
        sys.modules[name] = module
        exec(compile(values['.runtime/' + name + '.py'], str(path), 'exec'), module.__dict__)
        loaded[name] = module
    return loaded['offdesk_resume_checkpoint'], loaded['offdesk_metadata_consumer']


def inspect(root, expected):
    cfg, values = selection(root, expected)
    gate, consumer = modules(root, values)
    config = consumer.configuration(decode(values['configuration.json']))
    require(config['schema'] == 'forager.metadata_consumer.v2' and config['batch_rows'] == cfg['batch_rows']
            and config['limits']['total_seconds'] == cfg['total_seconds']
            and config['limits']['work_seconds'] + config['limits']['shutdown_grace_seconds'] <= cfg['child_seconds'],
            'component/controller limits differ')
    require(len(consumer.records(values['input.jsonl'])) == cfg['rows'], 'selected row count differs')
    with gate.root_handle(root) as fd:
        consumer._packet_context(fd, config, values['input.jsonl'])
    selection(root, expected)
    return cfg, values, gate, consumer


def runtime_binding(cfg):
    value = decode(os.environ.get('FORAGER_OFFDESK_RUNTIME_BINDING', 'null'))
    require(type(value) is dict and set(value) == {'schema', 'project_key', 'task_id', 'request_id', 'ticket_id'},
            'dispatcher runtime binding missing')
    require(value['schema'] == 'offdesk_runtime_binding.v1'
            and all(value[k] == cfg[k] for k in ('project_key', 'task_id', 'request_id')),
            'dispatcher identity mismatch')
    ticket = value['ticket_id']
    require(type(ticket) is str and 0 < len(ticket) <= 200
            and all(c.isascii() and (c.isalnum() or c in '_-') for c in ticket), 'invalid dispatched ticket')
    return value


def window(cfg, deadline, closeout=False):
    require(cfg['starts_at'] <= time.time() < cfg['stops_at'], 'absolute window exhausted/not started')
    require(time.clock_gettime(time.CLOCK_BOOTTIME) < deadline - (0 if closeout else cfg['closeout_seconds']),
            'original controller window exhausted')


def emit(gate, root, name, value):
    with gate.root_handle(root) as fd:
        return gate.publish(fd, name, value)


def child(root, expected, previous):
    cfg, _, gate, consumer = inspect(root, expected)
    binding = runtime_binding(cfg)
    attempt = decode(read(root, 'ATTEMPT.json', 65536))
    require(attempt['runtime'] == binding and attempt['selection_sha256'] == expected
            and attempt['boot_id'] == consumer.boot_id(), 'child attempt differs')
    window(cfg, attempt['deadline_boottime'])
    if previous is None:
        return consumer.initialize(root)
    gate.keys(previous, {'contract', 'contract_sha256', 'receipt', 'receipt_sha256', 'publication', 'publication_sha256'})
    return consumer.consume_batch(root, previous['contract'], previous['contract_sha256'],
                                  previous['receipt'], previous['receipt_sha256'], previous['publication'], previous['publication_sha256'])


def run(root, expected):
    initial = time.clock_gettime(time.CLOCK_BOOTTIME)
    entry_wall = time.time()
    cfg, values, gate, consumer = inspect(root, expected)
    binding = runtime_binding(cfg)
    deadline = initial + min(cfg['total_seconds'], cfg['stops_at'] - entry_wall)
    window(cfg, deadline)
    # Preserve ambiguous entries. Nothing infers a latest checkpoint or restarts an attempt.
    for path in root.iterdir():
        require(not (path.name in ('ATTEMPT.json', 'RUNTIME_BINDING.json', 'result.json', 'FAILURE.json', 'CONTROLLER_TIMING.json')
                     or path.name.startswith(('seed.', 'batch-', 'entry-', 'child-', '.checkpoint-'))),
                'attempt/output already exists; no automatic resume')
    emit(gate, root, 'RUNTIME_BINDING.json', binding)
    emit(gate, root, 'ATTEMPT.json', {'schema': 'offdesk_metadata_attempt.v1', 'runtime': binding,
         'selection_sha256': expected, 'boot_id': consumer.boot_id(), 'started_boottime': initial, 'entry_wall_time': entry_wall,
         'deadline_boottime': deadline, 'automatic_retry': False})
    proc = None
    try:
        inspect(root, expected)
        window(cfg, deadline)
        observations = []
        child_observations = []
        previous = None
        seed_identity = None
        batches = math.ceil(cfg['rows'] / cfg['batch_rows'])
        env = {'HOME': str(root), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
               'FORAGER_OFFDESK_RUNTIME_BINDING': os.environ['FORAGER_OFFDESK_RUNTIME_BINDING']}
        bootstrap = values['.runtime/native_worker_bootstrap.py'].decode()
        for index in range(batches + 2):
            window(cfg, deadline)
            inspect(root, expected)
            require(consumer.boot_id() == decode(read(root, 'ATTEMPT.json'))['boot_id'], 'boot changed')
            pins = None if previous is None else {k: previous[k] for k in ('contract', 'contract_sha256', 'receipt', 'receipt_sha256', 'publication', 'publication_sha256')}
            committed_before = {p.name: digest(read(root, p.name)) for p in root.iterdir()
                                if p.name.startswith(('seed.', 'batch-', 'entry-'))} if index == batches + 1 else None
            argv = ['/usr/bin/python3', '-I', '-S', '-B', '-c', bootstrap,
                    str(root / '.runtime/offdesk_metadata_dispatch.py'), LOADED_SOURCE,
                    'child', '--root', str(root), '--expected-selection-sha256', expected,
                    '--previous', gate.encode(pins).decode()]
            stem = 'child-' + str(index)
            emit(gate, root, stem + '.intent.json', {'argv': argv, 'previous': pins, 'runtime': binding})
            timeout = min(cfg['child_seconds'], deadline - consumer.now() - cfg['closeout_seconds'])
            require(timeout > 0, 'no child time remains')
            def file_limit():
                resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
            with (root / (stem + '.stdout')).open('xb') as out, (root / (stem + '.stderr')).open('xb') as err:
                child_started = consumer.now()
                proc = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=err, start_new_session=True, preexec_fn=file_limit)
                try:
                    remaining = timeout - (consumer.now() - child_started)
                    require(remaining > 0, 'child startup exhausted its deadline')
                    proc.wait(timeout=remaining)
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait(timeout=cfg['closeout_seconds'])
                    child_ended = consumer.now()
                    child_observation = {'schema': 'offdesk_metadata_child_observation.v1',
                        'runtime': binding, 'started_boottime': child_started, 'ended_boottime': child_ended,
                        'elapsed_seconds': child_ended - child_started, 'timeout_seconds': timeout,
                        'process_exit_code': proc.returncode}
                    observed_pin = emit(gate, root, stem + '.observation.json', child_observation)
            require(proc.returncode == 0, 'child failed or interrupted; no retry')
            require(child_ended - child_started <= timeout, 'child elapsed deadline exceeded')
            result = decode(read(root, stem + '.stdout', 65536))
            inspect(root, expected)
            require(result['scientific_acceptance'] is False and result['packet_semantics_verified'] is True,
                    'unsupported child result')
            gate.inspect_resume(root, result['contract'], result['contract_sha256'], result['receipt'], result['receipt_sha256'])
            component_cfg, state, _ = consumer._admit(root, result['contract'], result['contract_sha256'], result['receipt'], result['receipt_sha256'], result['publication'], result['publication_sha256'])
            measured = gate.inspect_publication(root, result['contract'], result['contract_sha256'], result['receipt'], result['receipt_sha256'], result['publication'], result['publication_sha256'])
            if index != batches + 1:
                require(child_ended - measured['attempts'][-1]['work_ended_at'] <= component_cfg['limits']['shutdown_grace_seconds'],
                        'child publication/exit exceeded shutdown grace')
            child_observation.update(publication_sha256=result['publication_sha256'], process_observation_sha256=observed_pin)
            child_observations.append(child_observation)
            current_identity = (state['window_start'], state['boot_id'])
            if seed_identity is None:
                seed_identity = current_identity
            require(current_identity == seed_identity, 'checkpoint reset original window or boot')
            offset = min(index * cfg['batch_rows'], cfg['rows'])
            require(result['processed'] == state['processed'] == offset, 'checkpoint sequence differs')
            if previous is not None and index == batches + 1:
                require(result == previous and not (root / ('entry-' + str(cfg['rows']) + '.json')).exists(),
                        'complete noop changed result or reserved another entry')
                require(committed_before == {p.name: digest(read(root, p.name)) for p in root.iterdir()
                                             if p.name.startswith(('seed.', 'batch-', 'entry-'))},
                        'completion noop changed committed artifacts')
            window(cfg, deadline, closeout=True)
            observations.append(result)
            previous = result
        expected_counts = dict.fromkeys(consumer.ACTIONS, 0)
        for row in consumer.records(values['input.jsonl']):
            expected_counts[row['action']] += 1
        require(previous['status'] == 'complete' and previous['counts'] == expected_counts, 'final counts differ')
        emit(gate, root, 'CONTROLLER_TIMING.json', {'schema': 'offdesk_metadata_controller_timing.v1',
             'runtime': binding, 'started_boottime': initial, 'entry_wall_time': entry_wall,
             'deadline_boottime': deadline, 'child_observations': child_observations,
             'scope': 'child_processes_through_exit_not_final_controller_publication'})
        evidence = []
        for name in ['ATTEMPT.json', 'RUNTIME_BINDING.json', 'CONTROLLER_TIMING.json'] + [p[k] for p in observations for k in ('contract', 'receipt', 'publication')]:
            if not any(v['path'] == name for v in evidence):
                evidence.append({'path': name, 'sha256': digest(read(root, name)), 'kind': 'check', 'description': 'Exact local component evidence'})
        inspect(root, expected)
        window(cfg, deadline, closeout=True)
        emit(gate, root, 'result.json', {**binding, 'schema': 'offdesk_metadata_result.v1',
             'selection_sha256': expected, 'input_sha256': cfg['input_files'], 'runtime_sha256': cfg['runtime_files'],
             'read_policy_sha256': cfg['read_policy_sha256'], 'status': 'pending_user_review', 'exit_code': 0,
             'processed': previous['processed'], 'counts': expected_counts, 'checkpoints': observations,
             'evidence': evidence, 'scientific_acceptance': False, 'plan_update_authorized': False,
             'automatic_retry': False, 'provider_calls': 0})
        # A persisted result is evidence, even if this final check subsequently fails.
        inspect(root, expected)
        window(cfg, deadline, closeout=True)
        return {'status': 'pending_user_review', 'provider_calls': 0, 'child_calls': len(observations)}
    except BaseException as error:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=cfg['closeout_seconds'])
        emit(gate, root, 'FAILURE.json', {**binding, 'status': 'failed_review_pending', 'error_kind': type(error).__name__,
             'automatic_retry': False, 'intent_preserved': True})
        raise


def main():
    def interrupted(signum, frame):
        raise InterruptedError('controller interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'run-dispatched', 'child'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--expected-selection-sha256', required=True)
    parser.add_argument('--previous', default='null')
    args = parser.parse_args()
    if args.action == 'inspect':
        inspect(args.root, args.expected_selection_sha256)
        result = {'status': 'prepared_not_launch_authority', 'provider_calls': 0}
    elif args.action == 'child':
        result = child(args.root, args.expected_selection_sha256, decode(args.previous))
    else:
        result = run(args.root, args.expected_selection_sha256)
    import json
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
