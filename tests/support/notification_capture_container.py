"""Run only inside the disposable container with a read-only Rust test binary."""

import os
from pathlib import Path
import subprocess

assert Path("/.dockerenv").exists()
assert os.environ.get("FORAGER_NOTIFICATION_DISPOSABLE") == "1"
assert not Path("/var/run/docker.sock").exists()
assert not Path("/home/kimyoungjin06").exists()
assert "TMUX" not in os.environ
subprocess.run(["tmux", "new-session", "-d", "-s", "notification_canary", "sleep 120"], check=True)
outcome = subprocess.run([
    "/opt/notification-test-bin", "notification_capture_disposable_",
    "--ignored", "--nocapture", "--test-threads=1",
])
subprocess.run(["tmux", "has-session", "-t", "=notification_canary"], check=True)
print("disposable_canary_survived=true", flush=True)
raise SystemExit(outcome.returncode)
