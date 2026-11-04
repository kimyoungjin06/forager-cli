#!/usr/bin/env python3
"""Coordinate a local frontend cutover; agent sessions and services stay operator-owned."""
import argparse
from contextlib import contextmanager, nullcontext
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

SCHEMA = 'forager.frontend_maintenance.v1'
GATE = 'maintenance.v1.json'
LIMIT = 65536


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def directory(create):
    if sys.platform == 'darwin':
        data = Path.home() / 'Library/Application Support'
    elif sys.platform.startswith('linux'):
        configured = Path(os.environ.get('XDG_DATA_HOME') or '')
        # Match Rust dirs: relative XDG paths fall back to the user's data directory.
        data = configured if configured.is_absolute() else Path.home() / '.local/share'
    else:
        raise ValueError('frontend maintenance is unsupported on this platform')
    if not data.exists():
        if not create:
            return None
        data.mkdir(parents=True, mode=0o700)
    root = data.resolve()
    for name in ['forager', 'frontend-maintenance']:
        root = root / name
        if not root.exists() and not root.is_symlink():
            if not create:
                return None
            root.mkdir(mode=0o700)
        meta = root.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid():
            raise ValueError('maintenance directory is not an owned real directory')
    return root


def opened(root, name, flags):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=root)
    meta = os.fstat(fd)
    if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or meta.st_uid != os.getuid():
        os.close(fd)
        raise ValueError('unsafe maintenance file')
    return fd


@contextmanager
def store(create=True):
    path = directory(create)
    if path is None:
        yield None
        return
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    admission = opened(root, 'admission.lock', os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(admission, fcntl.LOCK_EX)
        yield root
    finally:
        os.close(admission)
        os.close(root)


def load(root):
    try:
        fd = opened(root, GATE, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as f:
        raw = f.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('maintenance record exceeds byte budget')
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or value.get('phase') not in ['prepared', 'executing', 'verified', 'needs_attention', 'finished', 'cancelled']
            or not isinstance(value.get('operation_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value['operation_id'])):
        raise ValueError('unknown maintenance record')
    return value


def write(root, record):
    raw = (json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if len(raw) > LIMIT:
        raise ValueError('maintenance record exceeds byte budget')
    name = '.maintenance-' + uuid.uuid4().hex
    fd = opened(root, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.rename(name, GATE, src_dir_fd=root, dst_dir_fd=root)
        audit = opened(root, 'maintenance.audit.v1.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        with os.fdopen(audit, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.fsync(root)
    finally:
        try:
            os.unlink(name, dir_fd=root)
        except FileNotFoundError:
            pass


def bound(record, operation):
    if record is None or record.get('operation_id') != operation:
        raise ValueError('maintenance operation mismatch')


@contextmanager
def exclusive_readers(root):
    fd = opened(root, 'readers.lock', os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def pin_images(paths):
    if not paths or len(paths) > 32:
        raise ValueError('declare the retained reader executables explicitly')
    pins = []
    for path in paths:
        resolved = Path(path).resolve(strict=True)
        with resolved.open('rb') as f:
            meta = os.fstat(f.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_size > 256 * 1024 * 1024:
                raise ValueError('reader image exceeds byte budget')
            digest = hashlib.sha256()
            for block in iter(lambda: f.read(65536), b''):
                digest.update(block)
            digest = digest.hexdigest()
            after = os.fstat(f.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns):
                raise ValueError('reader image changed')
        pins.append(dict(path=str(resolved), device=meta.st_dev, inode=meta.st_ino,
                         sha256=digest, process_names=sorted({Path(path).name[:15], resolved.name[:15], 'forager', 'aoe'})))
    return pins


def process_identity(pid):
    text = Path(f'/proc/{pid}/stat').read_text()
    fields = text.rsplit(')', 1)[1].split()
    return fields[0], f'linux:{pid}:{fields[19]}', text.split('(', 1)[1].rsplit(')', 1)[0]


def inventory(pins):
    if not sys.platform.startswith('linux'):
        raise ValueError('legacy reader inventory requires Linux; keep maintenance held')
    matches, unknown, vanished, other_unreadable = [], [], 0, 0
    identities = {(p['device'], p['inode']): p for p in pins}
    names = {name for pin in pins for name in pin['process_names']}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit() or int(path.name) == os.getpid():
            continue
        pid = int(path.name)
        observed_name = None
        try:
            if path.stat().st_uid != os.getuid():
                continue
            state, before, observed_name = process_identity(pid)
            if state == 'Z':
                continue
            image = (path / 'exe').stat()
            if (image.st_dev, image.st_ino) not in identities:
                continue
            _, after, _ = process_identity(pid)
            if before != after:
                vanished += 1
                continue
            matches.append(dict(pid=pid, identity=before,
                                executable_sha256=identities[(image.st_dev, image.st_ino)]['sha256']))
        except (FileNotFoundError, ProcessLookupError):
            try:
                state, _, observed_name = process_identity(pid)
                if state != 'Z':
                    if observed_name in names:
                        unknown.append(pid)
                    else:
                        other_unreadable += 1
                else:
                    vanished += 1
            except (FileNotFoundError, ProcessLookupError):
                vanished += 1
        except PermissionError:
            if observed_name is None or observed_name in names:
                unknown.append(pid)
            else:
                other_unreadable += 1
    return dict(readers=matches, unreadable_live_processes=unknown,
                unreadable_other_process_names=other_unreadable,
                vanished_or_zombie_observations=vanished,
                scope='declared native reader images and expected process names; arbitrary process-name changes are not fenced')


def quiescent(view):
    return not view['readers'] and not view['unreadable_live_processes']


def recheck_images(expected):
    pins = pin_images([pin['path'] for pin in expected])
    for pin, prior in zip(pins, expected):
        pin['process_names'] = prior['process_names']
    if pins != expected:
        raise ValueError('retained reader images changed')
    return pins


def begin(operation, reason):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', operation) or not reason.strip():
        raise ValueError('operation ID and reason required')
    with store() as root:
        current = load(root)
        if current is not None:
            bound(current, operation)
            return current
        record = dict(schema=SCHEMA, operation_id=operation, phase='prepared',
                      created_at=now(), reason_sha256=hashlib.sha256(reason.encode()).hexdigest())
        write(root, record)
        return record


def guard(operation, paths, command, *, candidate, expected_candidate_sha256):
    if not command or not candidate or not isinstance(expected_candidate_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_candidate_sha256):
        raise ValueError('an exact operator command and reviewed candidate SHA256 are required')
    with store() as root, exclusive_readers(root):
        record = load(root)
        bound(record, operation)
        if record['phase'] != 'prepared':
            raise ValueError('an attempted command cannot be automatically replayed')
        pins = pin_images(paths)
        selected_candidate = pin_images([candidate])[0]
        if selected_candidate['sha256'] != expected_candidate_sha256:
            raise ValueError('candidate does not match the reviewed executable SHA256')
        before = inventory([*pins, selected_candidate])
        if not quiescent(before):
            return dict(status='held_before_command', before=before, command_executed=False)
        record.update(phase='executing', started_at=now(), selected_readers=pins, before=before,
                      candidate=selected_candidate,
                      command_sha256=hashlib.sha256(json.dumps(command).encode()).hexdigest())
        write(root, record)
        try:
            # Only this explicit operator command runs; no session input or service action is inferred.
            child = subprocess.Popen(command, stdout=sys.stderr, stderr=sys.stderr, close_fds=True)
            code = child.wait()
            after = inventory([*pins, selected_candidate])
            try:
                recheck_images([selected_candidate])
                candidate_verified = True
            except (OSError, ValueError):
                candidate_verified = False
            record.update(command_exit_code=code, after=after, finished_at=now(),
                          candidate_verified=candidate_verified,
                          phase='verified' if code == 0 and quiescent(after) and candidate_verified else 'needs_attention')
        except BaseException:
            record.update(phase='needs_attention', command_outcome='unknown', observed_at=now())
            write(root, record)
            raise
        write(root, record)
        return record


def verify(operation):
    with store() as root, exclusive_readers(root):
        record = load(root)
        bound(record, operation)
        if record.get('command_exit_code') != 0 or record['phase'] not in ['verified', 'needs_attention']:
            raise ValueError('command outcome requires operator review; do not replay')
        pins = recheck_images(record['selected_readers'])
        recheck_images([record['candidate']])
        observed = inventory([*pins, record['candidate']])
        record.update(after=observed, observed_at=now(),
                      candidate_verified=True,
                      phase='verified' if quiescent(observed) else 'needs_attention')
        write(root, record)
        return record


def finish(operation, cancel=False, expected=None, reviewed=False, reason=''):
    with store() as root:
        record = load(root)
        bound(record, operation)
        reader_guard = nullcontext() if cancel and record['phase'] == 'prepared' else exclusive_readers(root)
        with reader_guard:
            if cancel:
                if record['phase'] not in ['prepared', 'cancelled']:
                    actual = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    if not reviewed or expected != actual or not reason.strip():
                        raise ValueError('an attempted cutover needs exact state review before releasing admission')
                record.update(phase='cancelled', reviewed_after_command=reviewed,
                              review_reason_sha256=hashlib.sha256(reason.encode()).hexdigest())
            else:
                if record['phase'] not in ['verified', 'finished']:
                    raise ValueError('cutover verification is incomplete')
                pins = recheck_images(record['selected_readers'])
                recheck_images([record['candidate']])
                if not quiescent(inventory([*pins, record['candidate']])):
                    raise ValueError('a retained reader reopened or changed before finish')
                record['phase'] = 'finished'
            record['closed_at'] = now()
            write(root, record)
            os.unlink(GATE, dir_fd=root)
            os.fsync(root)
            return record


def status():
    path = directory(False)
    if path is None:
        return dict(status='inactive')
    root = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        record = load(root)
        if record is None:
            return dict(status='inactive')
        result = dict(record)
        result['record_sha256'] = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        try:
            fd = opened(root, 'readers.lock', os.O_RDONLY)
        except FileNotFoundError:
            result['instrumented_frontends'] = 'quiescent'
        else:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result['instrumented_frontends'] = 'quiescent'
            except BlockingIOError:
                result['instrumented_frontends'] = 'coordinator_holds_lease' if record['phase'] == 'executing' else 'still_running'
            finally:
                os.close(fd)
        return result
    finally:
        os.close(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['begin', 'status', 'guard', 'verify', 'finish', 'cancel'])
    parser.add_argument('--operation-id')
    parser.add_argument('--reason', default='')
    parser.add_argument('--reader-executable', action='append', default=[])
    parser.add_argument('--candidate-executable')
    parser.add_argument('--expected-candidate-sha256')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--confirm-state-reviewed', action='store_true')
    tokens = sys.argv[1:]
    split = tokens.index('--') if '--' in tokens else len(tokens)
    args = parser.parse_args(tokens[:split])
    command = tokens[split + 1:]
    if args.action != 'guard' and command:
        parser.error('only guard accepts an operator command')
    try:
        if args.action == 'status':
            result = status()
        elif args.action == 'begin':
            result = begin(args.operation_id or '', args.reason)
        elif args.action == 'guard':
            result = guard(args.operation_id, args.reader_executable, command,
                           candidate=args.candidate_executable,
                           expected_candidate_sha256=args.expected_candidate_sha256)
        elif args.action == 'verify':
            result = verify(args.operation_id)
        else:
            result = finish(args.operation_id, cancel=args.action == 'cancel', expected=args.expected_sha256,
                            reviewed=args.confirm_state_reviewed, reason=args.reason)
        print(json.dumps(result, sort_keys=True))
        return 2 if result.get('phase') == 'needs_attention' or result.get('status', '').startswith('held') else 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(status='held', error_type=type(error).__name__)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
