"""Rehearse bounded path selection and stopped recovery, using only temp data."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from telegram_rollback_guard import check

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--bundle", type=Path, required=True)
parser.add_argument("--unit", type=Path, required=True)
args = parser.parse_args()
manifest = json.loads((args.bundle / "manifest.json").read_text())
for name, expected in manifest["files"].items():
    assert hashlib.sha256((args.bundle / name).read_bytes()).hexdigest() == expected
unit = args.unit.read_text()
command = next(line.removeprefix("ExecStart=") for line in unit.splitlines() if line.startswith("ExecStart="))
tokens = shlex.split(command)
assert tokens[0] == "/usr/bin/python3"
assert tokens[1].endswith("/scripts/offdesk_remote_operator_telegram.py")
binary_index = tokens.index("--forager-bin") + 1
with tempfile.TemporaryDirectory(prefix="forager-cutover-rehearsal-") as directory:
    root = Path(directory)
    release = root / "releases/candidate"
    shutil.copytree(args.bundle / "candidate", release)
    sentinel = root / "unrelated-service"
    sentinel.write_text("unchanged")
    state, outbox = root / "state.json", root / "outbox.jsonl"
    state.write_text(json.dumps({"notification_unconfirmed_events": {"synthetic": {"status": "delivery_inflight"}}}))
    outbox.write_text("")
    original = state.read_bytes(), outbox.read_bytes()
    selected = list(tokens)
    selected[1] = str(release / "scripts/offdesk_remote_operator_telegram.py")
    selected[binary_index] = str(release / "bin/forager")
    assert [i for i, pair in enumerate(zip(tokens, selected)) if pair[0] != pair[1]] == [1, binary_index]
    dropin = root / "release.conf"
    dropin.write_text("[Service]\nExecStart=\nExecStart=" + shlex.join(selected) + "\n")
    assert shlex.split(dropin.read_text().split("ExecStart=")[-1]) == selected
    assert check(state, outbox)["legacy_restart_blocked"]
    # Recovery restores path selection only, with listener still stopped.
    dropin.write_text("[Service]\nExecStart=\nExecStart=" + command + "\n")
    assert shlex.split(dropin.read_text().split("ExecStart=")[-1]) == tokens
    assert original == (state.read_bytes(), outbox.read_bytes())
    assert sentinel.read_text() == "unchanged"
print("temporary_install_rehearsal=pass changed_argv_fields=2 unresolved_state_preserved=true legacy_restart_blocked=true unrelated_sentinel_preserved=true")
