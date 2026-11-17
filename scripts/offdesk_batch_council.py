#!/usr/bin/env python3
"""Two bounded, review-only subscription Council checkpoints for one run."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offdesk_native_worker as worker

SCHEMA = {"type": "object", "properties": {
    "decision": {"type": "string", "enum": ["continue", "stop"]},
    "reason": {"type": "string"},
    "unresolved": {"type": "array", "items": {"type": "string"}}},
    "required": ["decision", "reason", "unresolved"], "additionalProperties": False}


def valid_review(value):
    return (isinstance(value, dict) and set(value) == {"decision", "reason", "unresolved"}
            and value["decision"] in ("continue", "stop")
            and isinstance(value["reason"], str) and bool(value["reason"].strip())
            and isinstance(value["unresolved"], list)
            and all(isinstance(x, str) for x in value["unresolved"]))


def review_checkpoint(request, response, host, selection):
    raw = request.read_bytes()
    if len(raw) > 32768:
        raise ValueError("Council request too large")
    data = json.loads(raw)
    if (data.get("run_id") != selection["run_id"]
            or data.get("checkpoint") not in (1, 3)
            or data.get("schema") != "forager_four_phase_council_request.v1"):
        raise ValueError("Council request identity mismatch")
    checkpoint = data["checkpoint"]
    request_sha = worker.sha(request)
    context = json.dumps(data, ensure_ascii=False)
    if len(context) > 15000:
        raise ValueError("Council context exceeds selected bound")
    prompt = ("Review-only Council checkpoint. Use only the supplied evidence. No tools, "
              "commands, file access, implementation or other agents. Return JSON decision "
              "continue or stop, reason, unresolved. Judge whether the technical evidence "
              "permits the next isolated phase; do not accept TODOs or scientific claims. "
              "The patch is an explicitly bounded excerpt; state the review limits. Stop for "
              "failed/unbound gates, scope violations or a blocked/unsafe next phase. "
              "Automatic weekly-quota monitoring was waived "
              "by the user; do not block solely for that.\n" + context)
    schema_path = host / "schema.json"
    last = host / f"council-{checkpoint}-gpt-last.json"
    gpt = [selection["gpt_executable"], "exec", "--ignore-user-config", "--ignore-rules",
           "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--json",
           "--disable", "memories", "--disable", "multi_agent", "--disable", "goals",
           "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "code_mode",
           "--disable", "hooks", "--disable", "apps", "--disable", "browser_use",
           "--disable", "computer_use", "-c", 'model_reasoning_effort="low"',
           "--output-schema", str(schema_path), "-o", str(last), "-"]
    claude = [selection["claude_executable"], "-p", "--tools", "", "--max-turns", "1",
              "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
              "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
              "--output-format", "json", "--json-schema", json.dumps(SCHEMA)]
    reviews = {}
    for provider, argv in (("gpt", gpt), ("claude", claude)):
        label = f"council-{checkpoint}-{provider}"
        claim = host / (label + ".claim.json")
        with claim.open("x") as stream:
            json.dump({"run_id": selection["run_id"], "request_sha256": request_sha,
                       "claimed_at": time.time()}, stream)
        env = {k: v for k, v in os.environ.items() if k not in
               ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "CODEX_OSS_BASE_URL")}
        result = None
        code = None
        with (host / (label + ".stdout.log")).open("wb") as stdout, \
                (host / (label + ".stderr.log")).open("wb") as stderr:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       env=env, cwd=host / "cwd", start_new_session=True)
            try:
                remaining = selection["stop_epoch"] - time.time() - 15
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, 0)
                process.communicate(prompt.encode(), timeout=min(300, remaining))
                code = process.returncode
            finally:
                worker.stop_group(process)
        try:
            if provider == "gpt":
                result = json.loads(last.read_text())
            else:
                wrapper = json.loads((host / (label + ".stdout.log")).read_text())
                result = wrapper.get("structured_output") or json.loads(wrapper["result"])
            if code != 0 or not valid_review(result):
                result = None
        except (OSError, ValueError, KeyError):
            result = None
        if result is None:
            result = {"decision": "stop", "reason": "No valid bounded Council response",
                      "unresolved": ["Review native provider logs before any retry"]}
        reviews[provider] = {"exit_code": code, "review": result}
    if worker.sha(request) != request_sha:
        raise ValueError("Council request changed during review")
    worker.write_json(response, {"run_id": selection["run_id"], "checkpoint": checkpoint,
                                "request_sha256": request_sha, "reviews": reviews,
                                "ready_to_continue": all(x["review"]["decision"] == "continue"
                                                         for x in reviews.values()),
                                "review_only": True, "weekly_percent_hard_cap_enforced": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--host-output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    host = args.host_output
    host.mkdir(mode=0o700)
    (host / "cwd").mkdir(mode=0o700)
    worker.write_json(host / "schema.json", SCHEMA)
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    night = Path(selection["root"]) / selection["output_relative"]
    completed = []
    failure = None
    worker.write_json(host / "SERVICE_STARTED.json", {"pid": os.getpid(), "run_id": selection["run_id"]})
    try:
        while time.time() < selection["stop_epoch"] - 15:
            if (night / "RUN_RESULT.json").exists():
                break
            for checkpoint in (1, 3):
                request = night / f"council-{checkpoint}-request.json"
                response = night / f"council-{checkpoint}-response.json"
                if request.exists() and not response.exists():
                    review_checkpoint(request, response, host, selection)
                    completed.append(checkpoint)
            time.sleep(.5)
    except (Exception, KeyboardInterrupt, SystemExit) as exc:
        failure = type(exc).__name__
    finally:
        worker.write_json(host / "SERVICE_RESULT.json", {"run_id": selection["run_id"],
                                                        "completed": completed, "failure": failure,
                                                        "ended_at": time.time()})
    return 1 if failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
