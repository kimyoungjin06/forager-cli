import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


WORKER = r'''
import json, os, pathlib, signal, subprocess, sys, time
subprocess.run(['tmux', '-f', '/dev/null', 'new-session', '-d', '-s',
                'forager_test_owned', 'sleep 120'], check=True)
pid = int(subprocess.check_output(['tmux', 'display-message', '-p',
                                  '-t', 'forager_test_owned', '#{pane_pid}']))
config = pathlib.Path(os.environ['XDG_CONFIG_HOME']) / 'forager'
config.mkdir()
(config / 'sessions.json').write_text('[{"title":"private fixture"}]')
pathlib.Path(sys.argv[1]).write_text(json.dumps(dict(root=os.environ['FORAGER_PRIVATE_TEST_ROOT'],
    home=os.environ['HOME'], config=str(config), pid=pid, worker=os.getpid())))
mode = sys.argv[2]
if mode == 'killed': os.kill(os.getpid(), signal.SIGKILL)
if mode == 'parent_closed': time.sleep(120)
raise SystemExit(17 if mode == 'failed' else 0)
'''


@unittest.skipUnless(os.name == 'posix' and shutil.which('tmux'), 'requires tmux')
class IsolatedProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fg-qa-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = self.root / 'host'
        self.host.mkdir()
        self.sentinel = self.host / 'sessions.json'
        self.sentinel.write_bytes(b'host sentinel, do not modify')
        self.socket = str(self.root / 'unrelated.sock')
        self.tmux = ['tmux', '-f', '/dev/null', '-S', self.socket]
        subprocess.run([*self.tmux, 'new-session', '-d', '-s', 'unrelated', 'sleep 120'], check=True)
        self.addCleanup(lambda: subprocess.run([*self.tmux, 'kill-server'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        self.host_pid = subprocess.check_output([*self.tmux, 'display-message', '-p', '#{pane_pid}'])

    def scenario(self, mode):
        receipt = self.root / 'receipt.json'
        helper = Path(__file__).with_name('isolated_test_process.py')
        env = {**os.environ, 'HOME': str(self.host), 'XDG_CONFIG_HOME': str(self.host),
               'TMUX': self.socket + ',123,0'}
        proc = subprocess.Popen([sys.executable, str(helper), '--test-name', 'fixture', '--',
                                 sys.executable, '-c', WORKER, str(receipt), mode],
                                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        try:
            end = time.monotonic() + 10
            while not receipt.exists() and time.monotonic() < end and proc.poll() is None:
                time.sleep(.02)
            if mode == 'parent_closed':
                self.assertTrue(receipt.exists())
                proc.stdin.close()
                proc.stdin = None
            proc.wait(timeout=15)
            stdout, stderr = proc.communicate(timeout=5)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self.assertEqual(proc.returncode, {'ok': 0, 'failed': 17, 'killed': 137,
                                          'parent_closed': 125}[mode], (stdout, stderr))
        record = json.loads(receipt.read_text())
        self.assertFalse(Path(record['root']).exists())
        self.assertTrue(record['home'].startswith(record['root'] + '/'))
        for key in ['pid', 'worker']:
            with self.assertRaises(ProcessLookupError):
                os.kill(record[key], 0)
        self.assertEqual(self.sentinel.read_bytes(), b'host sentinel, do not modify')
        self.assertEqual(subprocess.check_output([*self.tmux, 'display-message', '-p', '#{pane_pid}']), self.host_pid)

    def test_success_cleans_owned_server_and_preserves_unrelated_state(self):
        self.scenario('ok')

    def test_failure_cleans_owned_server(self):
        self.scenario('failed')

    def test_killed_worker_cleans_owned_server(self):
        self.scenario('killed')

    def test_parent_pipe_close_still_cleans_owned_worker_and_server(self):
        self.scenario('parent_closed')


if __name__ == '__main__':
    unittest.main()
