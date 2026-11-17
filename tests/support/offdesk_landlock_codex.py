"""Opt-in real Codex qualification through the manifest-bound read policy."""
import argparse
import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def inspect_packet(root):
    config = json.loads((root / "RUN_CONFIG.json").read_text())
    hashes = {name: sha(root / name) for name in config["input_sha256"]}
    assert hashes == config["input_sha256"], "approved input bytes changed"
    for name, digest in config["support_sha256"].items():
        assert sha(root / name) == digest, "approved support bytes changed"
    outside = Path(config["outside_canary"])
    denied = {}
    for label, path in [("outside", outside), ("symlink", root / "escape"),
                        ("host_auth", Path(config["host_auth"])),
                        ("proc_alias", Path(f"/proc/{os.getppid()}/root{outside}"))]:
        try:
            with path.open("rb") as stream:
                stream.read(1)
        except OSError as error:
            assert error.errno == errno.EACCES, (label, error.errno)
            denied[label] = True
        else:
            raise AssertionError("read policy did not deny " + label)
    try:
        outside.write_bytes(b"escaped")
    except OSError as error:
        assert error.errno == errno.EACCES
    else:
        raise AssertionError("outside write succeeded")
    child = subprocess.run(["/bin/sh", "-c", 'cat "$1"', "probe", str(outside)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert child.returncode != 0, "descendant escaped read policy"
    summary = json.loads((root / "input/r9-summary.json").read_text())
    policy = json.loads((root / "input/r9-policy.json").read_text())["policy"]
    return {"status": "pending_user_review", "input_sha256": hashes,
            "prior_tests_passed": summary["distinct_tests_passed"],
            "network_isolated": policy["network_isolated"], "tmux_covered": False,
            "plan_update_authorized": False, "denied_reads": denied,
            "outside_write_denied": True, "descendant_read_denied": True}


def worker(root):
    config = json.loads((root / "RUN_CONFIG.json").read_text())
    expected = inspect_packet(root)
    write_json(root / "WORKER_PREFLIGHT.json", expected)
    deadline = time.monotonic() + 45
    while not (root / "RUNTIME_BINDING.json").exists():
        assert time.monotonic() < deadline, "launch binding timeout"
        time.sleep(0.1)
    binding = json.loads((root / "RUNTIME_BINDING.json").read_text())
    assert binding["task_id"] == config["task_id"]
    assert binding["request_id"] == config["request_id"]
    argv = [config["codex"], "exec", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--disable", "memories",
            "--disable", "multi_agent", "-c", "project_doc_max_bytes=0",
            "-c", 'model_reasoning_effort="low"', "-s", "danger-full-access",
            "-m", config["model"], "-C", str(root), "--json",
            "--output-schema", str(root / "MODEL_SCHEMA.json"),
            "-o", str(root / "MODEL_REPORT.json"), "-"]
    write_json(root / "MODEL_INVOCATION.json", {"argv": argv, "timeout_sec": 180,
               "filesystem_boundary": "inherited_manifest_bound_landlock",
               "codex_invocations": 1})
    with (root / "model.events.jsonl").open("wb") as log:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.communicate((root / "PROMPT.txt").read_bytes(), timeout=180)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=10)
            raise AssertionError("bounded Codex invocation timed out")
    assert process.returncode == 0, "Codex exit " + str(process.returncode)
    report = json.loads((root / "MODEL_REPORT.json").read_text())
    assert report == expected, "model report does not match independently computed facts"
    commands = []
    usage = []
    for line in (root / "model.events.jsonl").read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "command_execution":
            commands.append(item)
        if event.get("type") == "turn.completed":
            usage.append(event.get("usage", {}))
    assert len(commands) == 1, "expected exactly one observed model command"
    parts = shlex.split(commands[0]["command"])
    inner = parts[-1] if len(parts) == 3 and parts[1] in ("-c", "-lc") else commands[0]["command"]
    assert shlex.split(inner) == ["python3", "-I", "-S", "inspect_packet.py"]
    assert commands[0]["exit_code"] == 0
    assert json.loads(commands[0]["aggregated_output"].strip()) == expected
    assert inspect_packet(root) == expected
    write_json(root / "MODEL_CHECKS.json", {"status": "PASS", "usage": usage,
               "observed_commands": [commands[0]["command"]],
               "input_hashes_unchanged": True, "codex_invocations": 1,
               "filesystem_boundary": "inherited_manifest_bound_landlock"})
    evidence = [{"kind": "check", "description": description,
                 "path": name, "sha256": sha(root / name)} for name, description in [
                    ("MODEL_REPORT.json", "Real Codex report matches independent input and boundary checks"),
                    ("MODEL_CHECKS.json", "Observed model tool command and unchanged input hashes")]]
    write_json(root / "result.json", {**binding, "status": "pending_user_review",
               "exit_code": 0, "plan_update_authorized": False, "evidence": evidence})
    print(json.dumps({"status": "pending_user_review", "checks": "PASS"}))


def controller(args):
    assert os.environ.get("FORAGER_LANDLOCK_CODEX_TRIAL") == "1", "real trial requires explicit opt-in"
    out = args.evidence.resolve()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = out / "task"
    root.mkdir(mode=0o700)
    home = out / "profile-home"
    home.mkdir(mode=0o700)
    profile = home / ".config/forager/profiles/default"
    env = {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config")}
    for key in ("FORAGER_PROFILE", "AGENT_OF_EMPIRES_PROFILE", "FORAGER_DEBUG", "CODEX_HOME"):
        env.pop(key, None)
    counter = 0

    def command(label, values, checked=True):
        nonlocal counter
        counter += 1
        result = subprocess.run([str(args.forager), *map(str, values)], env=env,
                                capture_output=True, text=True, timeout=60)
        (out / f"{counter:02d}-{label}.stdout").write_text(result.stdout)
        (out / f"{counter:02d}-{label}.stderr").write_text(result.stderr)
        if checked:
            assert result.returncode == 0, label + ": " + result.stderr.strip()
        return json.loads(result.stdout) if result.returncode == 0 else None

    host_auth_hash = sha(args.auth_file)
    secrets = []

    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, str) and len(value) > 16:
            secrets.append(value)

    collect(json.loads(args.auth_file.read_text()))
    auth_dir = root / ".codex"
    shift_id = "landlock_real_codex"
    task_id = "landlock_real_codex_task"
    request_id = "landlock_real_codex_request"
    summary = {"schema": "forager_landlock_codex_qualification.v1", "result": "failed",
               "task_id": task_id, "request_id": request_id, "shift_id": shift_id,
               "model": args.model, "forager_sha256": sha(args.forager),
               "driver_sha256": sha(Path(__file__)), "codex_sha256": sha(args.codex),
               "installed_runtime_changed": False, "canonical_plan_updated": False,
               "scientific_acceptance": False, "codex_invocations_budget": 1}
    launched = False
    try:
        probe = command("probe", ["offdesk", "shift", "read-policy-probe", "--json"])
        assert probe["status"] == "verified"
        summary["landlock_abi"] = probe["landlock_abi"]
        (root / "input").mkdir()
        shutil.copyfile(args.r9_summary, root / "input/r9-summary.json")
        shutil.copyfile(args.r9_policy, root / "input/r9-policy.json")
        owning = {"schema": "ondesk_task_plan.v1", "project_key": "qualification",
                  "tasks": [{"task_id": task_id, "goal": "Audit staged R9 facts under the execution read policy",
                             "completion_criteria": ["One real model report, exact input hashes and denied outside reads",
                                                     "One launch and verified supervisor exit; review stays pending"],
                             "depends_on": [], "review_state": "unreviewed", "evidence_refs": []}]}
        source = root / "NEXT_ACTIONS.md"
        source.write_text("# Disposable Forager qualification\n```forager-plan\n" + json.dumps(owning) + "\n```\n")
        source_hash = sha(source)
        shutil.copyfile(Path(__file__), root / "worker.py")
        (root / "inspect_packet.py").write_text('''from pathlib import Path
import json
import runpy
inspect_packet = runpy.run_path(str(Path(__file__).with_name("worker.py")), run_name="fixture")["inspect_packet"]
print(json.dumps(inspect_packet(Path(__file__).resolve().parent)))
''')
        outside = out / "HOST_CANARY.txt"
        outside.write_text("owned outside read/write canary\n")
        os.symlink(outside, root / "escape")
        names = ["input/r9-summary.json", "input/r9-policy.json", "NEXT_ACTIONS.md"]
        expected = {"status": "pending_user_review", "input_sha256": {name: sha(root / name) for name in names},
                    "prior_tests_passed": 425, "network_isolated": False, "tmux_covered": False,
                    "plan_update_authorized": False, "denied_reads": {name: True for name in ["outside", "symlink", "host_auth", "proc_alias"]},
                    "outside_write_denied": True, "descendant_read_denied": True}

        def schema(value):
            if isinstance(value, dict):
                return {"type": "object", "properties": {key: schema(child) for key, child in value.items()},
                        "required": list(value), "additionalProperties": False}
            return {"type": "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "string"}

        write_json(root / "MODEL_SCHEMA.json", schema(expected))
        (root / "PROMPT.txt").write_text(
            "This is one authorized disposable Forager integration trial. Use exactly one shell tool command: "
            "python3 -I -S inspect_packet.py\n"
            "The command reads the staged R9 evidence and owning plan and checks inherited filesystem restrictions. "
            "Read its JSON output and return exactly those fields as your final structured JSON. "
            "Do not invent hashes or infer success without running the command. Do not execute another command, "
            "access credentials, launch other agents, browse, modify inputs, accept work or change any plan. "
            "If the command fails, report failure honestly instead of a passing result.\n")
        config = {"codex": str(args.codex), "model": args.model, "task_id": task_id,
                  "request_id": request_id, "outside_canary": str(outside), "host_auth": str(args.auth_file),
                  "input_sha256": expected["input_sha256"],
                  "support_sha256": {name: sha(root / name) for name in ["worker.py", "inspect_packet.py", "PROMPT.txt", "MODEL_SCHEMA.json"]}}
        write_json(root / "RUN_CONFIG.json", config)
        auth_dir.mkdir(mode=0o700)
        shutil.copyfile(args.auth_file, auth_dir / "auth.json")
        os.chmod(auth_dir / "auth.json", 0o600)
        now = datetime.datetime.now(datetime.timezone.utc)
        start = (now - datetime.timedelta(seconds=10)).isoformat()
        stop = (now + datetime.timedelta(minutes=10)).isoformat()
        plan_path = out / "OFFDESK_PLAN.json"
        write_json(plan_path, {"schema": "offdesk_multiturn_plan.v1", "profile_key": "generic",
                   "decision": {"ready_for_operator_review": True, "ready_for_launch_preparation": False, "ready_for_enqueue": False},
                   "execution_sequence": [{"id": "phase_1", "objective": owning["tasks"][0]["goal"]}],
                   "authority": {"read_only_plan": True, "does_not_authorize": ["enqueue", "launch", "approval", "file movement", "archive", "delete", "wiki promotion", "accepted truth"]}})
        registration = command("plan", ["offdesk", "plan", plan_path, "--project-key", "qualification", "--request-id", request_id, "--task-id", task_id, "--json"])
        plan_id = Path(registration["artifacts"]["registry_dir"]).name
        review = command("plan-review", ["offdesk", "plan-review", plan_id, "--decision", "approved", "--reason", "User authorized one isolated real Codex read-policy trial", "--json"])
        brief = out / "EXECUTION_BRIEF.json"
        write_json(brief, {"request_id": request_id, "task_id": task_id, "project_key": "qualification", "approved": True,
                   "allowed_runtime_mutations": ["dispatch.runtime"], "allowed_canonical_mutations": [], "fresh_until": stop})
        shell_command = shlex.join(["/usr/bin/python3", "-I", "-S", str(root / "worker.py"), "worker", str(root)])
        command("enqueue", ["offdesk", "enqueue", "dispatch.runtime", "--runner", "local-background", "--project-key", "qualification", "--request-id", request_id, "--task-id", task_id, "--cmd", shell_command, "--workdir", root, "--brief", brief, "--mutation-class", "dispatch.runtime", "--artifact", f"ondesk-plan-sha256:{source_hash}={source}", "--result-artifact", root / "result.json", "--log-artifact", root / "worker.log", "--json"])
        prepared = command("prepare", ["offdesk", "shift", "prepare", plan_id, "--review-id", review["review_id"], "--shift-id", shift_id, "--starts-at", start, "--stops-at", stop, "--task-id", task_id, "--read-policy", "linux-landlock-workdir-and-runtime-only", "--json"])
        manifest = prepared["record"]["manifest"]
        pending = command("arm-pending", ["offdesk", "shift", "arm", shift_id, "--json"])
        approval = pending[0]["approval_id"]
        command("arm-approve", ["offdesk", "ok", approval, "--by", "qualification-operator", "--json"])
        command("arm", ["offdesk", "shift", "arm", shift_id, "--approval-id", approval, "--json"])
        held = command("dispatch-held", ["offdesk", "tick", "--shift-id", shift_id, "--task-id", task_id, "--json"])
        assert held["pending_approval"] == 1 and held["launched"] == 0
        assert not (root / "MODEL_INVOCATION.json").exists()
        pending = command("pending", ["offdesk", "pending", "--json"])
        assert len(pending) == 1 and pending[0]["action"] == "dispatch.runtime"
        command("dispatch-approve", ["offdesk", "ok", pending[0]["approval_id"], "--by", "qualification-operator", "--json"])
        tick = command("launch", ["offdesk", "tick", "--shift-id", shift_id, "--task-id", task_id, "--json"])
        assert tick["launched"] == 1
        launched = True
        background = json.loads((profile / "background_runs.json").read_text())[0]
        evidence = background["read_policy_launch_evidence"]
        policy_keys = ["schema", "provider", "minimum_landlock_abi", "runtime_readonly_paths",
                       "device_readwrite_paths", "device_readonly_paths", "hardlink_aliases_covered",
                       "nested_mounts_covered", "preopened_descriptors_covered", "workdir_readwrite",
                       "network_isolated", "metadata_access_covered", "ipc_covered", "inherited_stdio_restricted"]
        assert set(policy_keys) == set(manifest["read_policy"])
        policy_bytes = json.dumps({key: manifest["read_policy"][key] for key in policy_keys},
                                  separators=(",", ":"), ensure_ascii=False).encode()
        assert evidence["policy_sha256"] == hashlib.sha256(policy_bytes).hexdigest()
        assert evidence["working_directory_identity_sha256"] == manifest["task_bindings"][0]["project_root_identity_sha256"]
        assert evidence["restrictions_applied_before_exec"] and evidence["landlock_abi"] >= 5
        summary["launch_evidence"] = evidence
        write_json(root / "RUNTIME_BINDING.json", {"task_id": task_id, "request_id": request_id, "ticket_id": background["ticket_id"]})
        deadline = time.monotonic() + 240
        receipt = root / background["local_exit_receipt"]["relative_path"]
        while not receipt.exists():
            assert time.monotonic() < deadline, "supervisor receipt deadline"
            time.sleep(0.25)
        exit_record = json.loads(receipt.read_text())
        summary["supervisor_exit_status"] = exit_record["status"]
        command("reconcile", ["offdesk", "tick", "--shift-id", shift_id, "--task-id", task_id, "--json"])
        repeated = command("repeat-tick", ["offdesk", "tick", "--shift-id", shift_id, "--task-id", task_id, "--json"])
        assert repeated["launched"] == 0
        review = command("shift-review", ["offdesk", "shift", "review", shift_id, "--json"])
        write_json(out / "SHIFT_REVIEW.json", review)
        record = command("shift-show", ["offdesk", "shift", "show", shift_id, "--json"])
        assert len(record["task_launch_reservations"]) == 1
        assert len(record["task_launch_ticket_receipts"]) == 1
        assert len(record["task_outcome_receipts"]) == 1
        closeout = review["tasks"][0]["closeout_report"]
        assert exit_record["status"] == 0 and (root / "result.json").exists(), "real model worker failed; preserved worker log"
        assert json.loads((root / "MODEL_REPORT.json").read_text()) == expected
        assert closeout["review_disposition"] == "pending_user_review"
        assert closeout["plan_update_authorized"] is False
        assert closeout["last_observed_supervisor_shell_status"] == 0
        assert closeout["result_observation"]["identity"] == "self_reported_exact"
        assert all(item["verification"] == "current_bytes_hash_match_not_claim_verification" for item in closeout["result_observation"]["evidence"])
        assert sha(source) == source_hash
        assert outside.read_text() == "owned outside read/write canary\n"
        summary.update(result="bounded_real_model_qualification_pass", one_launch=True, one_outcome=True,
                       no_redispatch=True, owning_plan_unchanged=True, review_disposition="pending_user_review",
                       model_checks=json.loads((root / "MODEL_CHECKS.json").read_text()))
    except BaseException as error:
        summary["failure"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        try:
            if launched and not (root / "result.json").exists():
                command("cleanup-terminate", ["offdesk", "terminate-task", task_id, "--reason", "bounded qualification cleanup", "--json"], checked=False)
            command("disarm", ["offdesk", "shift", "disarm", shift_id, "--reason", "bounded qualification finished", "--json"], checked=False)
        finally:
            if auth_dir.exists():
                assert not auth_dir.is_symlink()
                shutil.rmtree(auth_dir)
            summary["temporary_auth_removed"] = not auth_dir.exists()
            summary["host_auth_unchanged"] = sha(args.auth_file) == host_auth_hash
            for path in out.rglob("*"):
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024:
                    try:
                        text = path.read_text()
                    except (UnicodeError, OSError):
                        continue
                    clean = text
                    for secret in secrets:
                        clean = clean.replace(secret, "[REDACTED_CREDENTIAL]")
                    if clean != text:
                        path.write_text(clean)
            write_json(out / "TEST_SUMMARY.json", summary)
    print(json.dumps({"result": summary["result"], "evidence": str(out / "TEST_SUMMARY.json")}))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(Path(sys.argv[2]))
        return
    parser = argparse.ArgumentParser()
    for name in ("evidence", "forager", "codex", "auth-file", "r9-summary", "r9-policy"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model", required=True)
    controller(parser.parse_args())


if __name__ == "__main__":
    main()
