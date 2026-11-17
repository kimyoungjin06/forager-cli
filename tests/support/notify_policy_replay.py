"""Replay a night of one continuously-waiting session through the notifier.

Test-only driver for tests/remote_operator_notify_policy.rs. Stubs the Telegram
transport and the tmux probe, runs one-minute polls from 20:26 to the next
morning, and prints the resulting card count as JSON.

Usage: notify_policy_replay.py <scripts_dir> <workdir> <scenario>
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time as real_time

scripts_dir, workdir, scenario = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, scripts_dir)

import offdesk_remote_operator_telegram as mod  # noqa: E402
import telegram_operator.dispatch as dispatch  # noqa: E402
from telegram_operator.remote_session_identity import signed_identity  # noqa: E402

session_identity = signed_identity(
    {
        "schema": "remote_session_identity.v1",
        "machine_identity_sha256": "1" * 64,
        "project_key": "scoreboard",
        "project_root_identity_sha256": "2" * 64,
        "worktree_kind": "direct_project_root",
        "worktree_identity_sha256": "3" * 64,
        "harness_kind": "codex",
        "harness_launch_sha256": "4" * 64,
        "session_id": "dd33247c689b41aa",
        "session_record_identity_sha256": "5" * 64,
        "transport_kind": "tmux",
        "transport_session_identity_sha256": "6" * 64,
        "pane_identity_sha256": "7" * 64,
        "process_identity_sha256": "8" * 64,
    }
)

status_file = workdir / "status.json"
status_file.write_text(
    json.dumps(
        {
            "sessions": [
                {
                    "id": "dd33247c689b41aa",
                    "title": "codex",
                    "project": "scoreboard",
                    "status": "waiting",
                    "tool": "codex",
                    "remote_session_identity": session_identity,
                }
            ]
        }
    )
)


class FakeClock:
    """Drop-in for the module's `time`, so a night passes in milliseconds."""

    def __init__(self, start: float) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def localtime(self, when: float | None = None):
        return real_time.localtime(self.now if when is None else when)

    def sleep(self, _seconds: float) -> None:
        return None


sent: list[str] = []
mod.send_message = lambda cfg, chat, msg, args, reply_markup=None: (
    sent.append(msg) or len(sent)
)
mod.edit_message = lambda *a, **k: None
mod.summarize_waiting_prompt = lambda args, tail: "승인 대기"
dispatch.find_tmux_session_name = lambda session_id: "fake_tmux"
# The pane never changes: this is the frozen prompt from 2026-08-06.
pane = {"tail": "Allow this command?\n❯ 1. Yes\n2. No"}
dispatch.capture_session_tail = lambda name, lines=15: pane["tail"]
dispatch.capture_notification_tail = lambda name, lines=15: pane["tail"]

clock = FakeClock(real_time.mktime(real_time.strptime("2026-08-06 20:26", "%Y-%m-%d %H:%M")))
mod.time = clock

args = argparse.Namespace(
    session_notify=True,
    session_status_file=status_file,
    forager_bin="forager",
    profile="default",
    session_notify_backoff_sec=1800,
    session_notify_max_backoff_sec=14400,
    session_notify_max_cards=4,
    session_notify_quiet_from_hour=23,
    session_notify_quiet_to_hour=8,
    dry_run=True,
)
if scenario == "legacy":
    # The 2026-08-06 configuration: flat 30-minute cadence, no cap, no quiet hours.
    args.session_notify_max_backoff_sec = 1800
    args.session_notify_max_cards = 0
    args.session_notify_quiet_from_hour = 8
    args.session_notify_quiet_to_hour = 8

state: dict = {}
if scenario == "changed":
    # Four cards exhausted prompt A, but prompt B appeared without the session
    # leaving waiting between polls. The per-prompt cap must reset for B.
    old_hash = dispatch.session_prompt_identity("old frozen prompt pane", "codex")
    state = {
        "waiting_ids_last_scan": ["dd33247c689b41aa"],
        "waiting_notified_by_session": {
            "dd33247c689b41aa": {
                "at": clock.now - 3600,
                "cards": 4,
                "prompt_hash": old_hash,
            }
        },
    }
    pane["tail"] = "Allow NEW prompt?\n❯ 1. Yes\n2. No"
config = {"target_chat_id": "1"}
stamps: list[str] = []
overnight = 0
tapped = False

for _ in range(int(14.2 * 60)):
    capture_status = json.loads(status_file.read_text())
    capture_status["sessions"][0]["notification_capture"] = {
        "schema": "notification_capture.v1",
        "binding_sha256": session_identity["binding_sha256"],
        "tail": pane["tail"],
    }
    status_file.write_text(json.dumps(capture_status))
    before = len(sent)
    mod.scan_and_notify_waiting_sessions(args, config, state)
    if len(sent) > before:
        hour = clock.localtime().tm_hour
        stamps.append(real_time.strftime("%H:%M", clock.localtime()))
        if hour >= 23 or hour < 8:
            overnight += 1
        if scenario == "tapped" and not tapped:
            # The operator taps the first card. The pane does not move, so the
            # dispatcher reports no_effect and the prompt is marked dead.
            prompt_hash = dispatch.session_prompt_identity(
                dispatch.capture_session_tail("fake_tmux", lines=15),
                "codex",
            )
            mod.record_session_input_outcome(
                state,
                {
                    "dispatch_result": {
                        "kind": "session_input",
                        "ok": False,
                        "error": "no_effect",
                    },
                    "parsed_command": {
                        "session_input_session_id": "dd33247c",
                        "session_input_hash": prompt_hash,
                    },
                },
            )
            tapped = True
    clock.now += 60

print(json.dumps({"cards": len(sent), "overnight": overnight, "stamps": stamps}))
