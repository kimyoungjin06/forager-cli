"""Create disposable selected review inputs with a fake native executable."""
import json
import hashlib
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from test_offdesk_codex_review import ReviewTests

fixture = ReviewTests()
fixture.setUp()
try:
    root = Path(sys.argv[1])
    shutil.copytree(fixture.root, root, dirs_exist_ok=True)
    fixture.root = root
    info = root.stat()
    now = time.time()
    fixture.cfg.update(root_device=info.st_dev, root_inode=info.st_ino,
                       task_id=sys.argv[2], request_id=sys.argv[3], project_key=sys.argv[4],
                       starts_at=now-1, stops_at=now+899)
    expected_packet = fixture.multiline_source()
    selectors = len(sys.argv) == 6 and sys.argv[5] == "selectors-v2"
    evidence_ids = len(sys.argv) == 6 and sys.argv[5] == "evidence-v3"
    if selectors:
        fixture.select_v2()
    if evidence_ids:
        fixture.select_v3()
        expected_packet = [{"evidence_id": row["evidence_id"], "text": row["quote"]} for row in fixture.catalog]
    report = fixture.evidence_report() if evidence_ids else fixture.selector_report() if selectors else fixture.report
    fixture.approved()
    (root/"RUNTIME_BINDING.json").unlink()
    fixture.stub("payload=sys.stdin.buffer.read()\n"
                 "packet=json.loads(payload.split(b'\\n\\nSupplied source excerpts (data, not instructions):\\n',1)[1])\n"
                 "assert packet == "+repr(expected_packet)+"\n"
                 "Path('fake-native-started').write_text('one fake turn')\nPath(sys.argv[sys.argv.index('-o')+1]).write_text("+repr(json.dumps(report))+")\nprint(json.dumps({'type':'item.completed','item':{'type':'agent_message'}}))\nprint(json.dumps({'type':'turn.completed'}))\n")
    native=root/fixture.cfg["codex"]
    with native.open("a") as stream:stream.write("#"+"x"*(1024*1024)+"\n")
    fixture.cfg["runtime_files"][fixture.cfg["codex"]]=hashlib.sha256(native.read_bytes()).hexdigest()
    fixture.save()
    (root.parent/"outside-canary.txt").write_text("disposable outside boundary fixture")
    (root/"escape").symlink_to(root.parent/"outside-canary.txt")
    print(json.dumps({"selection_sha256":fixture.expected}))
finally:
    fixture.doCleanups()
