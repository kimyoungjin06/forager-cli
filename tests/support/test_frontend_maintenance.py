import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pty
import shutil
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
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='fg-mnt-', dir='/tmp')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = dict(HOME=str(self.root / 'home'), XDG_DATA_HOME=str(self.root / 'data'),
                   XDG_CONFIG_HOME=str(self.root / 'config'), XDG_STATE_HOME=str(self.root / 'state'),
                   XDG_CACHE_HOME=str(self.root / 'cache'), TMUX_TMPDIR=str(self.root / 'tmux'), TERM='xterm-256color')
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, env).start()
        for key in ['TMUX', 'TMUX_PANE', 'FORAGER_PROFILE', 'AGENT_OF_EMPIRES_PROFILE']:
            os.environ.pop(key, None)
        self.old = self.root / 'old-forager'
        shutil.copyfile(shutil.which('sleep'), self.old)
        self.old.chmod(0o700)

    def spawn_old(self):
        proc = subprocess.Popen([str(self.old), '60'])
        def stop():
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
        self.addCleanup(stop)
        return proc


@unittest.skipUnless(sys.platform.startswith('linux'), 'legacy inventory is Linux only')
class StateTests(Fixture, unittest.TestCase):
    def test_status_remains_available_while_the_guarded_command_is_running(self):
        maintenance.begin('status', 'fixture')
        ready, done = self.root / 'ready', self.root / 'done'
        program = "import pathlib,sys,time; r,d=map(pathlib.Path,sys.argv[1:]); r.touch(); end=time.monotonic()+5\nwhile not d.exists() and time.monotonic()<end: time.sleep(.01)\n"
        proc = subprocess.Popen([sys.executable, str(SCRIPT), 'guard', '--operation-id', 'status',
                                 '--reader-executable', str(self.old), '--', sys.executable, '-c',
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
            result = maintenance.guard('window', [self.old], [sys.executable, '-c', program,
                                       str(state), str(backup), str(ready), str(alias), str(resumed)])
        finally:
            thread.join(timeout=6)
        self.assertEqual(result['phase'], 'needs_attention')
        self.assertTrue(any(r['pid'] == workers[0].pid for r in result['after']['readers']))
        self.assertEqual(state.read_bytes(), backup.read_bytes())
        self.assertIsNone(agent.poll())
        with self.assertRaises(ValueError):
            maintenance.guard('window', [self.old], ['false'])
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
        self.assertEqual(maintenance.guard('late', [self.old], ['true'])['phase'], 'verified')
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
        self.assertEqual(maintenance.guard('alias', [alias], [sys.executable, '-c', program,
                         str(alias), str(replacement)])['phase'], 'verified')
        self.assertEqual(alias.resolve(), replacement)
        self.assertEqual(maintenance.verify('alias')['phase'], 'verified')
        maintenance.finish('alias')

    def test_failure_retains_state_and_requires_exact_review_before_cancel(self):
        state = self.root / 'offset'
        state.write_bytes(b'unchanged')
        maintenance.begin('failed', 'fixture')
        result = maintenance.guard('failed', [self.old], ['false'])
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
            result = maintenance.guard('unreadable', [self.old], ['false'])
        self.assertFalse(result['command_executed'])
        self.assertIn(old.pid, result['before']['unreadable_live_processes'])


@unittest.skipUnless(os.name == 'posix' and shutil.which('tmux') and os.environ.get('FORAGER_MAINTENANCE_TEST_BINARY'), 'requires compiled frontend and tmux')
class FrontendTests(Fixture, unittest.TestCase):
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
        tmux_root.mkdir(mode=0o700)
        subprocess.run(['tmux', '-f', '/dev/null', 'new-session', '-d', '-s',
                        'forager_agent_abcdefgh', 'sleep 60'], check=True)
        self.addCleanup(lambda: subprocess.run(['tmux', 'kill-server'], capture_output=True))
        session = 'forager_agent_abcdefgh'
        pid = subprocess.check_output(['tmux', 'display-message', '-p', '-t', session, '#{pane_pid}'])
        app = self.root / 'config/forager' if sys.platform.startswith('linux') else self.root / 'home/.forager'
        profile = app / 'profiles/default'
        profile.mkdir(parents=True)
        (app / '.schema_version').write_text('11')
        (profile / 'sessions.json').write_text(json.dumps([dict(id='abcdefgh', title='agent',
            project_path=str(self.root), tool='bash', created_at='2026-11-04T00:00:00Z')]))
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = termios.tcgetattr(slave)
        def terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        proc = subprocess.Popen([os.environ['FORAGER_MAINTENANCE_TEST_BINARY'], 'session', 'attach', 'abcdefgh'],
                                stdin=slave, stdout=slave, stderr=slave, preexec_fn=terminal)
        self.addCleanup(lambda: proc.poll() is None and (proc.terminate(), proc.wait(timeout=5)))
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
        self.assertEqual(termios.tcgetattr(slave), before)
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
