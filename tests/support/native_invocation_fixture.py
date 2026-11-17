"""Prepare a disposable bound adapter with a fake executable, never a provider."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import offdesk_native_worker as worker


def prepare(root, task, request, project, deadline, mode="fixed"):
    root = Path(root).resolve()
    (root/'.runtime').mkdir(exist_ok=True)
    (root/'.night').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO/'scripts/offdesk_native_worker.py',root/'.runtime/offdesk_native_worker.py')
    binary=root/'.runtime/fake-codex'
    binary.write_text('#!/usr/bin/python3\nimport json,sys\nfrom pathlib import Path\nsys.stdin.read()\nPath(".night/fake-worker-started").write_text("fixture only")\nprint(json.dumps({"type":"turn.completed"}),flush=True)\n')
    binary.chmod(0o700)
    (root/'.night/prompt.txt').write_text('Disposable local fixture, no network calls.\n')
    (root/'test_invocation_fixture.py').write_text('import unittest,time\nfrom pathlib import Path\nclass Fixture(unittest.TestCase):\n def test_receipt(self):\n  with Path(".night/preflight-invocations").open("a") as f: f.write("run\\n")\n  if Path(".night/sleep-entry-preflight").exists(): time.sleep(5)\n  self.assertFalse(Path(".night/fail-entry-preflight").exists())\n')
    files={name:{'kind':'file','sha256':worker.sha(root/name)} for name in worker.source_inventory(root)}
    worker.write_json(root/'.runtime/source_manifest.json',{'snapshot_root':str(root),'files':files})
    checks=[['/usr/bin/python3','-I','-S','-m','unittest','discover','-p','test_invocation_fixture.py','-v']]
    receipt=worker.preflight(root,root/'.runtime/preflight.json',checks,10)
    assert receipt['passed'] and receipt['tests_passed']==1
    spec={'task_id':task,'request_id':request,'project_key':project,'preflight':'.runtime/preflight.json','prompt':'.night/prompt.txt',
          'worker':'.runtime/fake-codex','out':'.night/run','base_url':'http://127.0.0.1:9','model':'fixture-no-provider',
          'deadline':deadline,'max_tool_calls':8,'max_repeated_errors':2,'command_timeout':30,'verify_commands':checks}
    if mode != 'fixed':
        spec['entry_preflight']={'schema':'offdesk_native_entry_preflight.v1','timeout_seconds':1 if mode=='timeout' else 10,'closeout_reserve_seconds':10,'minimum_worker_seconds':5}
        if mode in ('stale','timeout'):
            receipt['completed_at']-=3600
            worker.write_json(root/'.runtime/preflight.json',receipt)
    return worker.bind_invocation(root,spec,'.runtime/invocation.json')


if __name__ == '__main__':
    print(json.dumps(prepare(*sys.argv[1:]),ensure_ascii=False))
