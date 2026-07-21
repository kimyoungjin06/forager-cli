#!/usr/bin/env python3
"""Armed-gated runner for overnight autonomy tasks (tick / distill / brief).

systemd timers call this every interval, but nothing runs unless the operator
has armed autonomy for a bounded window (normally via the Telegram proposal
card, or manually with --arm). The armed state is a small JSON file in the
profile directory; expiry auto-disarms, and `forager offdesk pause` still
stops everything regardless of the armed state.

  offdesk_autonomy_run.py --arm [--until-hour 9] [--by telegram]
  offdesk_autonomy_run.py --disarm | --status
  offdesk_autonomy_run.py --task tick|distill|brief   (called by timers)

Tasks while armed:
  tick    `forager offdesk tick` heartbeat: launches only already-approved
          work, polls running probes, runs the learning-signal scan.
  distill runs the operator-owned playbook `~/.config/forager/nightly_distill.sh`
          if present (a sample is written on --arm if missing).
  brief   sends the morning /attention card via the Telegram listener script
          plus a one-line wiki candidate-queue summary, then disarms.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ARMED_FILE = "offdesk_autonomy_armed.json"
ARMED_SCHEMA = "offdesk_autonomy_armed.v1"


def profile_dir(profile: str) -> pathlib.Path:
    home = pathlib.Path.home()
    if sys.platform.startswith("linux"):
        cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        return cfg / "forager" / "profiles" / profile
    return home / ".forager" / "profiles" / profile


def armed_path(profile: str) -> pathlib.Path:
    return profile_dir(profile) / ARMED_FILE


def load_state(profile: str) -> dict | None:
    path = armed_path(profile)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def is_armed(profile: str) -> bool:
    state = load_state(profile)
    if not state or not state.get("armed"):
        return False
    try:
        until = dt.datetime.fromisoformat(str(state.get("until")))
    except ValueError:
        return False
    if dt.datetime.now(dt.timezone.utc) >= until:
        disarm(profile, reason="window expired")
        return False
    return True


def arm(profile: str, until_hour: int, by: str, reason: str) -> dict:
    now = dt.datetime.now().astimezone()
    until = now.replace(hour=until_hour, minute=0, second=0, microsecond=0)
    if until <= now:
        until += dt.timedelta(days=1)
    state = {
        "schema": ARMED_SCHEMA,
        "armed": True,
        "armed_at": now.isoformat(),
        "until": until.astimezone(dt.timezone.utc).isoformat(),
        "until_local": until.isoformat(),
        "by": by,
        "reason": reason,
    }
    path = armed_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1) + "\n")
    ensure_sample_playbook()
    return state


def disarm(profile: str, reason: str) -> None:
    path = armed_path(profile)
    if path.exists():
        state = load_state(profile) or {}
        state.update({"armed": False, "disarmed_at": dt.datetime.now().astimezone().isoformat(), "disarm_reason": reason})
        path.write_text(json.dumps(state, indent=1) + "\n")


def forager_bin() -> str:
    return os.environ.get("FORAGER_BIN", str(REPO / "target" / "debug" / "forager"))


def log(msg: str) -> None:
    print(f"[{dt.datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}", flush=True)


def playbook_path() -> pathlib.Path:
    cfg = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return cfg / "forager" / "nightly_distill.sh"


SAMPLE_PLAYBOOK = """#!/bin/bash
# Operator-owned nightly distillation playbook, run while autonomy is armed.
# Edit freely; keep everything candidate-only (nothing here may promote).
set -u
export OFFDESK_LLM_BASE_URL="${OFFDESK_LLM_BASE_URL:-http://172.16.0.37:11434}"
export OFFDESK_LLM_MODEL="${OFFDESK_LLM_MODEL:-qwen3-coder:30b}"
REPO="__REPO__"
OUT=~/.cache/forager/nightly-distill/$(date +%Y%m%d)
mkdir -p "$OUT"

"$REPO/scripts/offdesk_wiki_mine_sessions.py" \\
  --sessions-dir ~/.codex/sessions --sessions-dir ~/.claude/projects \\
  --project-map 1.2.8.TwinPaper=twinpaper-review:twinpaper \\
  --project-map 1.2.6.1.Overton_OpenAlex=overton-openalex \\
  --project-map 1.4.5.Local_Map_Analysis=lrnm \\
  --project-map 1.1.4.KISTI_NanoClustering=nanoclustering \\
  --project-map 1.4.4.Sciscape=sciscape \\
  --max-sessions 40 --record --out-dir "$OUT/mine"

for P in twinpaper-review overton-openalex lrnm nanoclustering sciscape forager-ops; do
  "$REPO/scripts/offdesk_wiki_prereview.py" --profile "$P" \\
    --num-ctx 32768 --num-predict 12288 \\
    --packet "$OUT/$P-packet.md" || true
done
echo "nightly distill done: $OUT"
"""


def ensure_sample_playbook() -> None:
    path = playbook_path()
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_PLAYBOOK.replace("__REPO__", str(REPO)))
    path.chmod(0o755)


def task_tick(profile: str) -> int:
    result = subprocess.run(
        [forager_bin(), "-p", profile, "offdesk", "tick", "--limit", "2", "--json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"tick failed: {(result.stderr or result.stdout)[:200]}")
        return 1
    try:
        report = json.loads(result.stdout)
        log(
            "tick ok: launched={launched} completed={completed} failed={failed} held={held} learning={learning_signals_emitted}".format(
                **{k: report.get(k, 0) for k in ("launched", "completed", "failed", "held", "learning_signals_emitted")}
            )
        )
    except ValueError:
        log("tick ok (unparsed report)")
    return 0


def task_distill() -> int:
    path = playbook_path()
    if not path.exists():
        log("no nightly playbook; skipping distill")
        return 0
    log(f"running playbook {path}")
    result = subprocess.run(["/bin/bash", str(path)], capture_output=True, text=True, timeout=3 * 3600)
    tail = (result.stdout or "").strip().splitlines()[-3:]
    for line in tail:
        log(f"playbook: {line}")
    if result.returncode != 0:
        log(f"playbook exit {result.returncode}: {(result.stderr or '')[:200]}")
    return 0  # distill failure must not mark the unit failed; morning brief still runs


def task_brief(profile: str) -> int:
    # 1) the existing read-only attention card through the listener script
    listener = REPO / "scripts" / "offdesk_remote_operator_telegram.py"
    subprocess.run(
        [sys.executable, str(listener), "--send-command-text", "/attention", "--forager-bin", forager_bin()],
        capture_output=True, text=True, timeout=120,
    )
    # 2) wiki candidate-queue summary as a plain follow-up line
    counts = []
    for prof in ("twinpaper-review", "overton-openalex", "lrnm", "nanoclustering", "sciscape", "forager-ops"):
        result = subprocess.run(
            [forager_bin(), "-p", prof, "offdesk", "wiki", "candidates", "--json"],
            capture_output=True, text=True,
        )
        try:
            n = len(json.loads(result.stdout))
        except ValueError:
            n = 0
        if n:
            counts.append(f"{prof} {n}")
    line = "위키 후보 대기: " + (" · ".join(counts) if counts else "없음") + "\n패킷: ~/.cache/forager/nightly-distill/"
    try:
        sys.path.insert(0, str(REPO / "scripts"))
        from offdesk_remote_operator_telegram import DEFAULT_TELEGRAM_ENV_FILE  # type: ignore
        from telegram_operator.config import resolve_telegram_config  # type: ignore
        from telegram_operator.transport import send_message  # type: ignore

        config = resolve_telegram_config(DEFAULT_TELEGRAM_ENV_FILE, required=True)
        wire = argparse.Namespace(api_timeout_sec=45, dry_run=False)
        send_message(config, config["target_chat_id"], line, wire)
    except Exception as error:  # noqa: BLE001 - brief must not crash the unit
        log(f"wiki summary send skipped: {error}")
    log(line.replace("\n", " | "))
    disarm_profile = os.environ.get("FORAGER_PROFILE", profile)
    disarm(disarm_profile, reason="morning brief sent; window closed")
    log("autonomy disarmed for the day")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", choices=["tick", "distill", "brief"])
    group.add_argument("--arm", action="store_true")
    group.add_argument("--disarm", action="store_true")
    group.add_argument("--status", action="store_true")
    parser.add_argument("--until-hour", type=int, default=9, help="Local hour when the armed window closes (default 09:00).")
    parser.add_argument("--by", default="cli")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    if args.arm:
        state = arm(args.profile, args.until_hour, args.by, args.reason)
        print(json.dumps(state, indent=1))
        return 0
    if args.disarm:
        disarm(args.profile, reason=args.reason or "manual disarm")
        print("disarmed")
        return 0
    if args.status:
        state = load_state(args.profile)
        print(json.dumps({"armed": is_armed(args.profile), "state": state}, indent=1))
        return 0

    if not is_armed(args.profile):
        return 0  # silent, cheap exit: timers fire all day but nothing runs unarmed
    if args.task == "tick":
        return task_tick(args.profile)
    if args.task == "distill":
        return task_distill()
    if args.task == "brief":
        return task_brief(args.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
