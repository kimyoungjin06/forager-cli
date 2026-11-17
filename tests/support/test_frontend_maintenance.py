import fcntl
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/forager_frontend_maintenance.py'
spec = importlib.util.spec_from_file_location('maintenance', SCRIPT)
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class Fixture:
    @staticmethod
    def stop_process(proc):
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)

    def frontend(self, command, master, slave, env=None):
        report = self.root / 'terminal-restoration.json'
        # Keep the controlling session alive while observing terminal cleanup.
        # Darwin revokes the child's slave handles when its session leader exits.
        supervisor = r"""
import fcntl, json, subprocess, sys, termios
from pathlib import Path
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
before_attrs = termios.tcgetattr(0)
before = repr(before_attrs)
child = subprocess.Popen(json.loads(sys.argv[2]))
code = child.wait()
after_attrs = termios.tcgetattr(0)
before_config, after_config = list(before_attrs), list(after_attrs)
# PENDIN is pending-input state, not an unrestored terminal configuration.
for attributes in [before_config, after_config]:
    attributes[3] &= ~getattr(termios, 'PENDIN', 0)
Path(sys.argv[1]).write_text(json.dumps({'before': before, 'after': repr(after_attrs),
    'configuration_before': repr(before_config), 'configuration_after': repr(after_config)}))
raise SystemExit(code)
"""
        proc = subprocess.Popen([sys.executable, '-c', supervisor, str(report), json.dumps(command)],
                                env=env, stdin=slave, stdout=slave, stderr=slave,
                                start_new_session=True)
        self.drain_terminal(master)
        self.addCleanup(self.stop_process, proc)
        return proc, report

    def assert_terminal_restored(self, report, before):
        observed = json.loads(report.read_text())
        self.assertEqual(observed['before'], repr(before))
        self.assertEqual(observed['configuration_after'], observed['configuration_before'])

    def terminal_pair(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        return master, slave

    def drain_terminal(self, master):
        stopped = threading.Event()

        def drain():
            # A real terminal consumes output. An unread PTY can block rendering
            # or tmux detach before the frontend can observe maintenance.
            while not stopped.is_set():
                if select.select([master], [], [], .05)[0]:
                    try:
                        if not os.read(master, 65536):
                            return
                    except OSError:
                        return

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        self.addCleanup(lambda: (stopped.set(), reader.join(timeout=1)))

    def guard(self, operation, paths, command, *, candidate=None):
        selected = candidate or self.old
        return maintenance.guard(operation, paths, command, candidate=selected,
                                 expected_candidate_sha256=hashlib.sha256(selected.read_bytes()).hexdigest())

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='fg-mnt-', dir='/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'tmux').mkdir(mode=0o700)
        env = dict(HOME=str(self.root / 'home'), XDG_DATA_HOME=str(self.root / 'data'),
                   XDG_CONFIG_HOME=str(self.root / 'config'), XDG_STATE_HOME=str(self.root / 'state'),
                   XDG_CACHE_HOME=str(self.root / 'cache'), TMUX_TMPDIR=str(self.root / 'tmux'), TERM='xterm-256color')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, env).start()
        for key in ['TMUX', 'TMUX_PANE', 'FORAGER_PROFILE', 'AGENT_OF_EMPIRES_PROFILE']:
            os.environ.pop(key, None)
        self.old = self.root / 'old-forager'
        # A reader needs its own native image. System sleep can be a script
        # or multicall launcher whose executable identity changes when copied.
        source = self.root / 'reader.c'
        source.write_text('#include <unistd.h>\nint main(void) { for (;;) pause(); }\n')
        subprocess.run([shutil.which('cc') or 'cc', str(source), '-o', str(self.old)],
                       check=True, capture_output=True)
        self.old.chmod(0o700)

    def spawn_old(self):
        proc = subprocess.Popen([str(self.old)])
        def stop():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
        self.addCleanup(stop)
        return proc


@unittest.skipUnless(sys.platform.startswith('linux'), 'legacy inventory is Linux only')
class StateTests(Fixture, unittest.TestCase):
    def test_changed_candidate_is_held_without_automatic_restore_or_replay(self):
        maintenance.begin('candidate', 'fixture')
        original = self.old.read_bytes()
        program = 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_bytes(p.read_bytes()+b"changed")'
        result = self.guard('candidate', [self.old], [sys.executable, '-c', program, str(self.old)])
        self.assertEqual(result['command_exit_code'], 0)
        self.assertFalse(result['candidate_verified'])
        self.assertEqual(result['phase'], 'needs_attention')
        self.assertEqual(self.old.read_bytes(), original + b'changed')
        with self.assertRaises(ValueError):
            maintenance.verify('candidate')
        self.old.write_bytes(original)
        self.assertTrue(maintenance.verify('candidate')['candidate_verified'])
        maintenance.finish('candidate')

    def test_wrong_reviewed_candidate_digest_prevents_the_operator_command(self):
        maintenance.begin('wrong-candidate', 'fixture')
        output = self.root / 'must-not-exist'
        with self.assertRaises(ValueError):
            maintenance.guard('wrong-candidate', [self.old], ['touch', str(output)],
                              candidate=self.old, expected_candidate_sha256='0' * 64)
        self.assertFalse(output.exists())
        self.assertEqual(maintenance.status()['phase'], 'prepared')

    def test_status_remains_available_while_the_guarded_command_is_running(self):
        maintenance.begin('status', 'fixture')
        ready, done = self.root / 'ready', self.root / 'done'
        program = "import pathlib,sys,time; r,d=map(pathlib.Path,sys.argv[1:]); r.touch(); end=time.monotonic()+5\nwhile not d.exists() and time.monotonic()<end: time.sleep(.01)\n"
        proc = subprocess.Popen([sys.executable, str(SCRIPT), 'guard', '--operation-id', 'status',
                                 '--reader-executable', str(self.old), '--candidate-executable', str(self.old),
                                 '--expected-candidate-sha256', hashlib.sha256(self.old.read_bytes()).hexdigest(), '--', sys.executable, '-c',
                                 program, str(ready), str(done)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < end:
                time.sleep(.01)
            self.assertTrue(ready.exists())
            started = time.monotonic()
            report = maintenance.status()
            self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(report['phase'], 'executing')
            self.assertEqual(report['instrumented_frontends'], 'coordinator_holds_lease')
        finally:
            done.touch()
            proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        maintenance.finish('status')

    def test_backup_window_reopen_is_held_and_reconciled_without_command_replay(self):
        state = self.root / 'state.json'
        state.write_bytes(b'original state and offset')
        backup = self.root / 'backup.json'
        ready = self.root / 'ready'
        resumed = self.root / 'resumed'
        alias = self.root / 'selector'
        agent = subprocess.Popen(['sleep', '60'])
        self.addCleanup(lambda: (agent.terminate(), agent.wait(timeout=5)))
        maintenance.begin('window', 'local fixture')
        workers = []
        def reopen():
            until = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < until:
                time.sleep(.01)
            if not ready.exists():
                return
            workers.append(self.spawn_old())
            resumed.touch()
        thread = threading.Thread(target=reopen)
        thread.start()
        program = "import pathlib,shutil,sys,time; s,b,r,a,done=map(pathlib.Path,sys.argv[1:]); shutil.copyfile(s,b); r.touch(); end=time.monotonic()+5\nwhile not done.exists() and time.monotonic()<end: time.sleep(.01)\na.write_text('new selected image')\n"
        try:
            result = self.guard('window', [self.old], [sys.executable, '-c', program,
                                       str(state), str(backup), str(ready), str(alias), str(resumed)])
        finally:
            thread.join(timeout=6)
        self.assertEqual(result['phase'], 'needs_attention')
        self.assertTrue(any(r['pid'] == workers[0].pid for r in result['after']['readers']))
        self.assertEqual(state.read_bytes(), backup.read_bytes())
        self.assertIsNone(agent.poll())
        with self.assertRaises(ValueError):
            self.guard('window', [self.old], ['false'])
        workers[0].terminate()
        workers[0].wait(timeout=5)
        self.assertEqual(maintenance.verify('window')['phase'], 'verified')
        self.assertEqual(maintenance.finish('window')['phase'], 'finished')
        self.assertEqual(maintenance.status(), {'status': 'inactive'})
        audit = (maintenance.directory(False) / 'maintenance.audit.v1.jsonl').read_text()
        self.assertIn('needs_attention', audit)
        self.assertEqual(audit.count('"phase":"executing"'), 1)

    def test_a_reopen_after_guard_is_rechecked_before_finish(self):
        maintenance.begin('late', 'fixture')
        self.assertEqual(self.guard('late', [self.old], ['true'])['phase'], 'verified')
        old = self.spawn_old()
        with self.assertRaises(ValueError):
            maintenance.finish('late')
        old.terminate()
        old.wait(timeout=5)
        maintenance.finish('late')

    def test_selection_alias_change_keeps_original_reader_images_pinned(self):
        alias = self.root / 'custom-alias'
        alias.symlink_to(self.old)
        replacement = self.root / 'new-forager'
        shutil.copyfile(shutil.which('true'), replacement)
        replacement.chmod(0o700)
        maintenance.begin('alias', 'fixture')
        program = 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.unlink(); p.symlink_to(sys.argv[2])'
        self.assertEqual(self.guard('alias', [alias], [sys.executable, '-c', program,
                         str(alias), str(replacement)], candidate=replacement)['phase'], 'verified')
        self.assertEqual(alias.resolve(), replacement)
        self.assertEqual(maintenance.verify('alias')['phase'], 'verified')
        maintenance.finish('alias')

    def test_failure_retains_state_and_requires_exact_review_before_cancel(self):
        state = self.root / 'offset'
        state.write_bytes(b'unchanged')
        maintenance.begin('failed', 'fixture')
        result = self.guard('failed', [self.old], ['false'])
        self.assertEqual(result['phase'], 'needs_attention')
        with self.assertRaises(ValueError):
            maintenance.verify('failed')
        with self.assertRaises(ValueError):
            maintenance.finish('failed', cancel=True)
        before = maintenance.status()
        maintenance.finish('failed', cancel=True, expected=before['record_sha256'],
                           reviewed=True, reason='fixture confirmed selected files and state')
        self.assertEqual(state.read_bytes(), b'unchanged')

    def test_unattempted_cancel_can_release_intent_while_frontend_is_still_running(self):
        maintenance.begin('cancel', 'fixture')
        with maintenance.store() as root:
            lock = maintenance.opened(root, 'readers.lock', os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lock, fcntl.LOCK_SH)
            self.assertEqual(maintenance.status()['instrumented_frontends'], 'still_running')
            self.assertEqual(maintenance.finish('cancel', cancel=True)['phase'], 'cancelled')
        finally:
            os.close(lock)

    def test_unreadable_candidate_reader_keeps_the_command_held(self):
        old = self.spawn_old()
        maintenance.begin('unreadable', 'fixture')
        actual_stat = Path.stat
        def deny(path, *args, **kwargs):
            if str(path) == f'/proc/{old.pid}/exe':
                raise PermissionError('fixture unavailable reader')
            return actual_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', deny):
            result = self.guard('unreadable', [self.old], ['false'])
        self.assertFalse(result['command_executed'])
        self.assertIn(old.pid, result['before']['unreadable_live_processes'])


@unittest.skipUnless(os.name == 'posix' and shutil.which('tmux') and os.environ.get('FORAGER_MAINTENANCE_TEST_BINARY'), 'requires compiled frontend and tmux')
class FrontendTests(Fixture, unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'XDG namespace is Linux only')
    def test_relative_xdg_uses_the_same_home_fallback_as_both_frontends(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative-data'}):
                maintenance.begin('relative-xdg', 'fixture')
                for binary in [os.environ['FORAGER_MAINTENANCE_TEST_BINARY'],
                               os.environ['FORAGER_MAINTENANCE_TEST_ALIAS']]:
                    output = subprocess.run([binary, 'session', 'attach', 'missing-fixture'],
                                            capture_output=True, timeout=10)
                    self.assertNotEqual(output.returncode, 0)
                    self.assertIn(b'maintenance is active', output.stderr)
                self.assertEqual(maintenance.directory(False),
                                 self.root / 'home/.local/share/forager/frontend-maintenance')
                self.assertFalse((self.root / 'relative-data').exists())
                self.assertFalse((self.root / 'config').exists())
                maintenance.finish('relative-xdg', cancel=True)
        finally:
            os.chdir(previous)

    def test_idle_tui_exits_through_terminal_cleanup(self):
        app = self.root / 'config/forager' if sys.platform.startswith('linux') else self.root / 'home/.forager'
        app.mkdir(parents=True)
        (app / '.schema_version').write_text('11')
        (app / 'config.toml').write_text('[updates]\ncheck_enabled = false\n[session]\nauto_orchestrator = false\n')
        tools = self.root / 'bin'
        tools.mkdir()
        cli = tools / 'claude'
        cli.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "fixture 1.0"; exit 0; fi\nexit 97\n')
        cli.chmod(0o700)
        env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH']}
        master, slave = self.terminal_pair()
        before = termios.tcgetattr(slave)
        proc, report = self.frontend([os.environ['FORAGER_MAINTENANCE_TEST_BINARY']],
                                     master, slave, env)
        end = time.monotonic() + 10
        while time.monotonic() < end and proc.poll() is None:
            if not termios.tcgetattr(slave)[3] & termios.ICANON:
                break
            time.sleep(.05)
        else:
            self.fail('TUI did not enter raw terminal mode')
        maintenance.begin('idle-tui', 'fixture')
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assert_terminal_restored(report, before)
        maintenance.finish('idle-tui', cancel=True)

    def test_unsafe_gate_symlink_blocks_startup_without_touching_external_file(self):
        maintenance.begin('symlink', 'fixture')
        gate = maintenance.directory(False) / maintenance.GATE
        original = gate.with_suffix('.saved')
        gate.replace(original)
        outside = self.root / 'outside'
        outside.write_bytes(original.read_bytes())
        before = outside.read_bytes()
        gate.symlink_to(outside)
        try:
            with self.assertRaises(OSError):
                maintenance.status()
            output = subprocess.run([os.environ['FORAGER_MAINTENANCE_TEST_BINARY']], capture_output=True, timeout=10)
            self.assertNotEqual(output.returncode, 0)
            self.assertEqual(outside.read_bytes(), before)
            self.assertFalse((self.root / 'config').exists())
        finally:
            gate.unlink()
            original.replace(gate)
        maintenance.finish('symlink', cancel=True)

    def test_admission_blocks_both_aliases_and_go_before_profile_mutation(self):
        maintenance.begin('admission', 'fixture')
        with maintenance.store() as root:
            fd = maintenance.opened(root, 'readers.lock', os.O_RDWR | os.O_CREAT)
        os.close(fd)
        binaries = [os.environ['FORAGER_MAINTENANCE_TEST_BINARY'], os.environ['FORAGER_MAINTENANCE_TEST_ALIAS']]
        for binary in binaries:
            for args in [[], ['go', str(self.root), '--no-attach']]:
                output = subprocess.run([binary, *args], capture_output=True, timeout=10)
                self.assertNotEqual(output.returncode, 0)
                self.assertIn(b'maintenance', output.stderr)
        self.assertFalse((self.root / 'config').exists())
        self.assertEqual(maintenance.finish('admission', cancel=True)['phase'], 'cancelled')

    def attached_case(self, unsafe_policy=None):
        tmux_root = self.root / 'tmux'
        tmux_root.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(['tmux', '-f', '/dev/null', 'new-session', '-d', '-s',
                        'forager_agent_abcdefgh', 'sleep 60'], check=True)
        self.addCleanup(lambda: subprocess.run(['tmux', 'kill-server'], capture_output=True))
        session = 'forager_agent_abcdefgh'
        pid = subprocess.check_output(['tmux', 'display-message', '-p', '-t', session, '#{pane_pid}'])
        identity = maintenance.process_identity(int(pid))[1] if sys.platform.startswith('linux') else None
        app = self.root / 'config/forager' if sys.platform.startswith('linux') else self.root / 'home/.forager'
        profile = app / 'profiles/default'
        profile.mkdir(parents=True)
        (app / '.schema_version').write_text('11')
        (profile / 'sessions.json').write_text(json.dumps([dict(id='abcdefgh', title='agent',
            project_path=str(self.root), tool='bash', created_at='2026-11-04T00:00:00Z')]))
        master, slave = self.terminal_pair()
        before = termios.tcgetattr(slave)
        proc, report = self.frontend([os.environ['FORAGER_MAINTENANCE_TEST_BINARY'],
                                      'session', 'attach', 'abcdefgh'], master, slave)
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if subprocess.check_output(['tmux', 'list-clients']).strip():
                break
            time.sleep(.05)
        else:
            self.fail('frontend did not attach')
        if unsafe_policy == 'session':
            subprocess.run(['tmux', 'set-option', '-t', session, 'destroy-unattached', 'on'], check=True)
        if unsafe_policy == 'server':
            subprocess.run(['tmux', 'set-option', '-s', 'exit-unattached', 'on'], check=True)
        maintenance.begin('attached', 'fixture')
        if unsafe_policy:
            time.sleep(.5)
            self.assertIsNone(proc.poll(), 'unsafe detach must stay held')
            self.assertEqual(subprocess.check_output(['tmux', 'display-message', '-p', '-t', session, '#{pane_pid}']), pid)
            maintenance.finish('attached', cancel=True)
            subprocess.run(['tmux', 'set-option', '-t', session, 'destroy-unattached', 'off'], check=True)
            subprocess.run(['tmux', 'set-option', '-s', 'exit-unattached', 'off'], check=True)
            subprocess.run(['tmux', 'detach-client', '-s', session], check=True)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertEqual(subprocess.check_output(['tmux', 'display-message', '-p', '-t', session, '#{pane_pid}']), pid)
        if identity is not None:
            self.assertEqual(maintenance.process_identity(int(pid))[1], identity)
        self.assert_terminal_restored(report, before)
        if not unsafe_policy:
            maintenance.finish('attached', cancel=True)

    def test_attached_frontend_detaches_its_client_and_preserves_agent_and_terminal(self):
        self.attached_case()

    def test_session_destroy_on_detach_keeps_maintenance_held(self):
        self.attached_case('session')

    def test_server_exit_on_detach_keeps_maintenance_held(self):
        self.attached_case('server')


if __name__ == '__main__':
    unittest.main()
