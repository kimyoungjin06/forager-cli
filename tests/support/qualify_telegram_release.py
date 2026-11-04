"""Disposable-only qualification of packaged Python and optimized binary."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

assert Path("/.dockerenv").exists()
assert not Path("/home/kimyoungjin06").exists()
root = Path("/release")
manifest = json.loads((root / "manifest.json").read_text())
for name, expected in manifest["files"].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
checkout = Path("/tmp/forager-telegram-isolated-20260922")
shutil.copytree("/source", checkout, ignore=shutil.ignore_patterns("target", ".git", "__pycache__"))
shutil.copytree(root / "candidate/scripts", checkout / "scripts", dirs_exist_ok=True)
(checkout / "target/debug").mkdir(parents=True)
shutil.copy2(root / "candidate/bin/forager", checkout / "target/debug/forager")
os.environ["FORAGER_NOTIFICATION_DISPOSABLE"] = "1"
subprocess.run(["tmux", "new-session", "-d", "-s", "release_canary", "sleep 600"], check=True)
library = "/source/target/debug/deps/forager-b53f72c6a48466ea"
commands = [
    [library, "tmux::status_detection::tests", "--test-threads=1"],
    [library, "session::remote_identity::tests", "--test-threads=1"],
    [library, "session::storage::tests", "--test-threads=1"],
    [library, "notification_capture_disposable_", "--ignored", "--test-threads=1"],
    ["python3", str(checkout / "tests/support/test_waiting_notification_safety.py")],
    ["/source/target/debug/deps/remote_operator_telegram-8d0697623af9ab8b", "--test-threads=1"],
]
for command in commands:
    result = subprocess.run(command, cwd=checkout, timeout=300)
    subprocess.run(["tmux", "has-session", "-t", "=release_canary"], check=True)
    if result.returncode:
        raise SystemExit(result.returncode)
print("packaged_release_qualification=pass canary_survived=true", flush=True)
