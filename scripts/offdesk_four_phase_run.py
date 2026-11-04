#!/usr/bin/env python3
"""Sequential, review-pending phases inside one approved Forager task."""
import argparse
import datetime as dt
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import resource
import re
import shutil
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import offdesk_native_worker as worker


def inventory(root):
    rows = {}
    for name in sorted(worker.source_inventory(root)):
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError("Source symlink escapes selected root")
        if path.is_symlink():
            rows[name] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_file():
            rows[name] = {"kind": "file", "sha256": worker.sha(path)}
        else:
            raise ValueError("Non-file source object")
    return rows


def changed_paths(before, after):
    return sorted(name for name in before.keys() | after.keys()
                  if before.get(name) != after.get(name))


def validate_scope(before, after, allowed):
    changed = changed_paths(before, after)
    for name in changed:
        if not any(name.startswith(pattern) if pattern.endswith("/")
                   else fnmatch.fnmatchcase(name, pattern) for pattern in allowed):
            raise ValueError("Change outside phase scope: " + name)
    return changed


def refresh_manifest(root, rows):
    path = root / ".runtime/source_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"] = rows
    worker.write_json(path, manifest)
    return worker.source_identity(root, manifest)


def admit_launcher_files(root, rows, original, selection):
    """Bind exact Forager-created receipts; keep them in the strict inventory."""
    names = set(original["files"])
    if not names <= set(rows):
        raise ValueError("Initial selected source is missing")
    old_rows = [(name, rows[name]["kind"], rows[name].get("target", rows[name].get("sha256")))
                for name in sorted(names)]
    if hashlib.sha256(json.dumps(old_rows).encode()).hexdigest() != selection["initial_source_sha256"]:
        raise ValueError("Initial source identity changed")
    extra = set(rows) - names
    if not extra:
        if selection.get("require_launcher_receipt"):
            raise ValueError("Selected Forager launcher receipt missing")
        return []
    groups = {}
    for name in extra:
        parts = Path(name).parts
        if (len(parts) != 2 or not re.fullmatch(r"\.forager-read-policy-[A-Za-z0-9]+", parts[0])
                or parts[1] not in ("spec.json", "receipt.json", "acknowledged") or rows[name]["kind"] != "file"):
            raise ValueError("Unselected launch-time addition: " + name)
        groups.setdefault(parts[0], set()).add(parts[1])
    if len(groups) != 1 or next(iter(groups.values())) != {"spec.json", "receipt.json", "acknowledged"}:
        raise ValueError("Expected exactly one bound Forager launcher")
    directory = root / next(iter(groups))
    spec = json.loads((directory / "spec.json").read_text())
    receipt = json.loads((directory / "receipt.json").read_text())
    if (spec.get("schema") != "offdesk_tmux_read_policy_spec.v1"
            or spec.get("workdir") != str(root)
            or str(root / ".runtime/offdesk_four_phase_run.py") not in json.dumps(spec.get("arguments"))
            or receipt.get("schema") != "offdesk_tmux_read_policy_receipt.v1"
            or receipt.get("workdir") != str(root)
            or receipt.get("nonce") != spec.get("nonce")
            or (directory / "acknowledged").read_bytes() != spec.get("nonce", "").encode()
            or receipt.get("launch_spec_sha256") != worker.sha(directory / "spec.json")
            or hashlib.sha256(json.dumps(spec.get("policy"), separators=(",", ":")).encode()).hexdigest() != selection["read_policy_sha256"]
            or receipt.get("evidence", {}).get("policy_sha256") != selection["read_policy_sha256"]):
        raise ValueError("Forager launcher receipt does not match this runtime")
    return sorted(extra)


def compact_result(result):
    compact = {k: v for k, v in result.items() if k != "verification_checks"}
    compact["verification_checks"] = [
        {k: v for k, v in check.items() if k not in ("stdout", "stderr")}
        for check in result.get("verification_checks", [])]
    return compact


def prepare_task_context(root, output, phase, run_binding=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", phase["id"]):
        raise ValueError("Task phase requires one safe identifier")
    context = output / (phase["id"] + "-context.json")
    progress = output / (phase["id"] + "-progress.json")
    if any(path.exists() or path.is_symlink() for path in (context, progress)):
        raise ValueError("Existing task context/progress must be reviewed, never reinitialized")
    _, baseline_sha = worker.read_task_json(root, output / "BASELINE.json", 2 * 1024 * 1024)
    scope = phase.get("read_paths", phase["write_paths"])
    if (not isinstance(scope, list) or len(scope) > 64
            or any(not isinstance(path, str) or not 1 <= len(path) <= 2000
                   or Path(path).is_absolute() or ".." in Path(path).parts for path in scope)):
        raise ValueError("Selected context read paths must be bounded relative source patterns")
    patterns = {"FG-N18-1": r"owning_plan|subproject|governance|run_init|ProjectInitArgs",
                "FG-N18-2": r"review|report|preview|attention",
                "FG-N18-3": r"closeout|evidence|completeness|sha256",
                "FG-N18-4": r"shift|ondesk|fixture|failed"}
    focus = re.compile(phase.get("focus_pattern", patterns.get(phase["id"], r"TODO|fn |def ")))
    index = []
    excerpts = []
    for name, row in inventory(root).items():
        if row["kind"] != "file" or not any(
                name.startswith(p) if p.endswith("/") else fnmatch.fnmatchcase(name, p) for p in scope):
            continue
        path = root / name
        if path.stat().st_size > 2 * 1024 * 1024:
            continue
        lines = path.read_text().splitlines()
        hits = [(i, line[:160]) for i, line in enumerate(lines, 1) if focus.search(line)]
        index.append({"path": name, "sha256": worker.sha(path), "lines": len(lines),
                      "matches": hits[:8], "matches_truncated": len(hits) > 8})
        if hits and len(excerpts) < 4:
            excerpts.append(worker.source_excerpt(root, name, max(1, hits[0][0] - 5), 35))
        if len(index) >= 32:
            break
    binding = {"schema": "offdesk_task_progress.v1", "phase": phase["id"],
               "baseline_sha256": baseline_sha, "root_identity": worker.root_identity(root)}
    if run_binding is not None:
        binding["run_binding"] = run_binding
    worker.progress_binding(binding)
    packet = {**binding, "index": index, "excerpts": excerpts,
              "read_paths": scope, "write_paths": phase["write_paths"],
              "review_only": True, "claims_verified": False}
    while len(json.dumps(packet)) > 6000 and excerpts:
        excerpts.pop()
    while len(json.dumps(packet)) > 6000 and index:
        index.pop()
    worker.write_json(context, packet)
    worker.write_json(progress, {**binding, "assessment": "Not yet assessed",
                                 "next_action": "Use the focused source index to identify one demonstrated gap",
                                 "checked_commands": [], "changed_files": []})
    return context, progress, binding


def observe_task_progress(root, path, binding):
    # Model-authored progress guides context recovery; it is never acceptance
    # or independent verification of edits or checks.
    if (path.is_symlink() or not path.resolve().is_relative_to(root)
            or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > worker.MAX_PROGRESS_BYTES):
        return {"status": "unavailable_or_unsafe", "claims_verified": False}
    try:
        data, digest = worker.read_task_json(root, path, worker.MAX_PROGRESS_BYTES)
        worker.validate_progress(data, binding)
        return {"status": "bound_model_report", "sha256": digest,
                "content": data, "claims_verified": False}
    except (OSError, ValueError):
        return {"status": "invalid_progress", "claims_verified": False}


class FourPhaseRun:
    def __init__(self, root, selection, output, clock=time.time, backend=None,
                 council=None, sleeper=time.sleep):
        self.root = root
        self.selection = selection
        self.output = output
        self.clock = clock
        self.backend = backend or self.native_phase
        self.council = council or self.wait_council
        self.sleep = sleeper
        self.completed = []
        self.records = []
        self.calls = 0
        self.active_phase = None
        self.native_calls = {}
        self.started = clock()
        baseline = Path(selection.get("baseline_relative", ".runtime/four-phase-baseline-r33"))
        if baseline.is_absolute() or len(baseline.parts) != 2 or baseline.parts[0] != ".runtime" or baseline.parts[1] == "..":
            raise ValueError("Baseline must be one selected directory under .runtime")
        self.baseline = root / baseline

    def record(self, name, value):
        worker.write_json(self.output / name, value)

    def observe_native_calls(self, context, calls):
        if (self.active_phase is None or not isinstance(context, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", context)
                or type(calls) is not int or calls < 0):
            raise ValueError("Active phase and nonnegative native call observation required")
        key = (self.active_phase, context)
        previous = self.native_calls.get(key, 0)
        if calls < previous:
            raise ValueError("Native call observations cannot decrease")
        self.calls += calls - previous
        self.native_calls[key] = calls
        self.record("NATIVE_TOOL_ACCOUNTING.json", {
            "schema": "forager_native_tool_accounting.v1", "tool_calls": self.calls,
            "native_tool_calls_observed": sum(self.native_calls.values()),
            "contexts": [{"phase": phase, "context": label, "tool_calls": value}
                         for (phase, label), value in sorted(self.native_calls.items())],
            "observation": "unique starts or completion-only events from native guards",
            "canonical_acceptance": False})

    def bindings(self):
        s = self.selection
        if worker.sha(Path(__file__)) != s["orchestrator_sha256"]:
            raise ValueError("Orchestrator identity changed")
        if worker.sha(Path(worker.__file__)) != s["controller_sha256"]:
            raise ValueError("Native controller identity changed")
        for relative, expected in s["native_runtime_hashes"].items():
            if worker.sha(self.root / relative) != expected:
                raise ValueError("Native runtime identity changed")

    def native_phase(self, phase, deadline):
        self.bindings()
        worker.validate_verification_commands(phase["checks"])
        preflight = self.output / (phase["id"] + "-preflight.json")
        receipt = worker.preflight(self.root, preflight,
                                  phase.get("preflight_checks", [["cargo", "fmt", "--check"],
                                   ["cargo", "test", "--offline", "--test", "project_init_cli"]]),
                                  min(900, deadline - self.clock()))
        if not receipt["passed"]:
            raise ValueError("Actual-test preflight failed")
        worker.validate_preflight(self.root, receipt, self.clock())
        self.bindings()
        if worker.sha(self.root / phase["prompt_path"]) != phase["prompt_sha256"]:
            raise ValueError("Phase prompt identity changed")
        context, progress, binding = prepare_task_context(self.root, self.output, phase,
            {key: self.selection[key] for key in ("run_id", "task_id", "request_id")})
        context_sha = worker.sha(context)
        prompt = (self.root / phase["prompt_path"]).read_text()
        prompt += (f"\nTask context index: {context.relative_to(self.root)}. "
                   f"Progress file: {progress.relative_to(self.root)}. Read these first. "
                   "Do not cat whole source/test files or use find over runtime/cache directories. "
                   "Use rg -n on explicit source paths (pipe to head -30), then read at most 60 lines "
                   "with python3 -I -S .runtime/offdesk_native_worker.py excerpt --root . "
                   "--path <source-path> --start <line> --lines 60. "
                   "Set max_output_tokens=1200 on exec_command calls. After at most three discovery "
                   "commands, record reuse/extend/missing and one concrete next action in the progress "
                   "JSON, preserving schema, phase and baseline_sha256. Update it after each edit/check. "
                   + worker.progress_instructions(str(context.relative_to(self.root)), context_sha) +
                   "Do not rewrite the progress JSON manually. "
                   "If context compacts, first use the read-only recovery-context command with "
                   f"--root . --context {context.relative_to(self.root)} "
                   f"--expected-context-sha256 {context_sha} --expected-progress-sha256 <last saved progress hash>. "
                   "Continue its next action; never restart source discovery. Progress is "
                   "model-authored and does not certify checks. No helper authorizes a new session. "
                   "Tool calls are charged when they start; reaching the selected limit interrupts "
                   "the worker, including its final command. Leave calls for progress and the report. "
                   "After a failed test, record its actual failures and fix the identified issue "
                   "before running it again. Changing sleep, tail or echo wrappers is not a fix. "
                   "Use direct test commands or preserve their exit status; a zero pipeline status "
                   "does not make a FAILED test summary pass.\n")
        out = self.output / phase["id"]
        env = worker.worker_env(self.root)
        env["CODEX_OSS_BASE_URL"] = self.selection["base_url"].rstrip("/") + "/v1"
        guard = worker.ProgressGuard(max_calls=phase["max_tool_calls"], repeated_errors=3)
        try:
            result = worker.supervise(
                worker.native_argv(str(self.root / self.selection["native_codex"]),
                                   self.root, self.selection["model"], out / "final.txt", True),
                prompt, self.root, out, deadline, guard,
                env, command_seconds=900,
                progress_observer=lambda: observe_task_progress(self.root, progress, binding),
                request_telemetry=phase.get("request_telemetry", False))
        finally:
            self.observe_native_calls(phase["id"], guard.calls)
        self.record(phase["id"] + "-progress-observation.json",
                    observe_task_progress(self.root, progress, binding))
        return worker.verify_result(result, out, self.root, phase["checks"], deadline)

    def wait_council(self, phase, record):
        checkpoint = phase["checkpoint"]
        request_path = self.output / f"council-{checkpoint}-request.json"
        summary = self.root / phase["summary_path"]
        small_record = {**record, "result": compact_result(record["result"])}
        payload = {"schema": "forager_four_phase_council_request.v1",
                   "run_id": self.selection["run_id"], "checkpoint": checkpoint,
                   "phase": phase["id"], "record": small_record,
                   "summary": summary.read_text()[:4500],
                   "patch_excerpt": self.patch()[:6000], "patch_is_excerpt": True,
                   "gate_sha256": worker.sha(self.output / (phase["id"] + "-gate.json")),
                   "review_only": True}
        self.record(request_path.name, payload)
        request_sha = worker.sha(request_path)
        response = self.output / f"council-{checkpoint}-response.json"
        until = min(self.selection["feature_stop_epoch"], self.clock() + 660)
        while self.clock() < until:
            if response.exists():
                data = json.loads(response.read_text())
                return (data.get("run_id") == self.selection["run_id"]
                        and data.get("request_sha256") == request_sha
                        and data.get("ready_to_continue") is True
                        and data.get("review_only") is True)
            self.sleep(min(.5, max(0, until - self.clock())))
        return False

    def patch(self):
        before = json.loads((self.output / "BASELINE.json").read_text())
        after = inventory(self.root)
        pieces = []
        for name in changed_paths(before, after):
            a, b = self.baseline / name, self.root / name
            def lines(path):
                if not path.exists() and not path.is_symlink():
                    return []
                text = os.readlink(path) if path.is_symlink() else path.read_text()
                return text.splitlines(keepends=True)
            pieces.extend(difflib.unified_diff(lines(a), lines(b),
                                               fromfile="a/" + name, tofile="b/" + name))
        return "".join(pieces)

    def closeout(self, failure, error=None):
        patch_error = None
        try:
            (self.output / "candidate.patch").write_text(self.patch())
            rows = inventory(self.root)
            self.record("FINAL_SOURCE.json", rows)
        except Exception as exc:
            patch_error = type(exc).__name__
            failure = failure or "closeout_patch_failed"
        result = {"schema": "forager_four_phase_result.v1",
                  "run_id": self.selection["run_id"], "task_id": self.selection["task_id"],
                  "request_id": self.selection["request_id"],
                  "ticket_id": self.selection.get("ticket_id"),
                  "status": "failed" if failure else "completed",
                  "exit_code": 1 if failure else 0, "failure": failure,
                  "error": error, "patch_error": patch_error,
                  "completed_phase_gates": self.completed, "tool_calls": self.calls,
                  "native_tool_calls_observed": sum(self.native_calls.values()),
                  "review_state": "pending_user_review", "canonical_acceptance": False,
                  "original_checkout_integrated": False,
                  "records": [{**record, "result": compact_result(record["result"])}
                              for record in self.records],
                  "ended_at": self.clock(), "deadline_epoch": self.selection["stop_epoch"]}
        handoff = ("Forager Offdesk return. Review pending.\n\n"
                   + "Status: " + result["status"] + "\n"
                   + "Completed technical phase gates: " + ", ".join(self.completed) + "\n"
                   + "Failure: " + str(failure) + "\n\n"
                   + "See RUN_RESULT.json, candidate.patch, FINAL_SOURCE.json, phase reports, "
                     "native logs, check results and Council responses. Technical checks are "
                     "not canonical acceptance. No shared checkout integration or deployment.\n")
        (self.output / "HANDOFF.md").write_text(handoff)
        result["evidence"] = [{"kind": "check", "description": "Pending-review return",
                               "path": str((self.output / "HANDOFF.md").relative_to(self.root)),
                               "sha256": worker.sha(self.output / "HANDOFF.md")}]
        self.record("RUN_RESULT.json", result)
        return result

    def run(self):
        worker.independent_root(self.root)
        if (self.output.is_symlink() or (self.output.exists() and any(self.output.iterdir()))
                or self.baseline.exists()):
            raise ValueError("Fresh run paths required; automatic replay forbidden")
        self.output.mkdir(parents=True, mode=0o700, exist_ok=True)
        failure = None
        error = None
        try:
            manifest = json.loads((self.root / ".runtime/source_manifest.json").read_text())
            if self.selection.get("require_launcher_receipt"):
                until = self.clock() + 5
                while self.clock() < until and not list(self.root.glob(".forager-read-policy-*/acknowledged")):
                    self.sleep(.05)
            before = inventory(self.root)
            admitted = admit_launcher_files(self.root, before, manifest, self.selection)
            self.record("LAUNCHER_SOURCE_BINDING.json", {"admitted_exact_files": admitted,
                                                        "project_source_sha256": self.selection["initial_source_sha256"]})
            refresh_manifest(self.root, before)
            self.record("BASELINE.json", before)
            self.baseline.mkdir(mode=0o700)
            for name in before:
                src, dst = self.root / name, self.baseline / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_symlink():
                    dst.symlink_to(os.readlink(src))
                else:
                    shutil.copy2(src, dst)
            self.record("WORKER_STARTED.json", {"pid": os.getpid(), "started_at": self.started,
                                               "run_id": self.selection["run_id"]})
            for phase in self.selection["phases"]:
                bound = (self.selection["stop_epoch"] - 900 if phase["id"] == "FG-N18-4"
                         else self.selection["feature_stop_epoch"])
                remaining = bound - self.clock()
                if remaining < 60:
                    failure = "final_closeout_window"
                    break
                if self.calls >= self.selection["max_total_tool_calls"]:
                    failure = "total_tool_budget_exhausted"
                    break
                self.record("CURRENT_PHASE.json", {"phase": phase["id"], "started_at": self.clock()})
                phase_before = inventory(self.root)
                phase = dict(phase)
                phase["max_tool_calls"] = min(phase["max_tool_calls"],
                                               self.selection["max_total_tool_calls"] - self.calls)
                deadline = min(bound, self.clock() + phase["max_seconds"])
                self.active_phase = phase["id"]
                calls_before = self.calls
                result = self.backend(phase, deadline)
                calls = result.get("tool_calls", 0)
                observed_calls = self.calls - calls_before
                valid_calls = type(calls) is int and calls >= observed_calls
                if valid_calls:
                    self.calls += calls - observed_calls
                self.bindings()
                phase_after = inventory(self.root)
                changed = changed_paths(phase_before, phase_after)
                record = {"phase": phase["id"], "result": result,
                          "changed_paths": changed, "review_state": "pending_user_review"}
                self.records.append(record)
                self.record(phase["id"] + "-gate.json", record)
                if not valid_calls or calls > phase["max_tool_calls"]:
                    failure = "invalid_or_exceeded_phase_tool_budget:" + phase["id"]
                    break
                validate_scope(phase_before, phase_after, phase["write_paths"])
                if (result.get("exit_code") != 0 or result.get("stop_reason") is not None
                        or result.get("verification_state") != "passed"
                        or result.get("turn_completed") is not True):
                    failure = "phase_gate_failed:" + phase["id"]
                    break
                if not (self.root / phase["summary_path"]).is_file():
                    failure = "phase_summary_missing:" + phase["id"]
                    break
                # Adopt only scoped candidate inputs for the next technical check.
                # This does not change the owning TODO or accept phase results.
                source = refresh_manifest(self.root, phase_after)
                record["candidate_source_sha256"] = source
                self.record(phase["id"] + "-gate.json", record)
                self.completed.append(phase["id"])
                if phase.get("checkpoint") and not self.council(phase, record):
                    failure = "council_requires_followup:" + phase["id"]
                    break
        except (KeyboardInterrupt, SystemExit):
            failure = "interrupted"
        except Exception as exc:
            failure = "orchestration_failed"
            error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
        self.active_phase = None
        return self.closeout(failure, error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text())
    root = Path(selection["root"])
    # The retired container cleanup surface must not contact a live daemon
    # while testing source-only candidates in this owned execution root.
    os.environ["DOCKER_HOST"] = "unix://" + str(root / ".runtime/unavailable-docker.sock")
    os.environ.pop("DOCKER_CONTEXT", None)
    resource.setrlimit(resource.RLIMIT_AS, (12 * 1024 ** 3, 12 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU, (21600, 21600))
    worker.verify_outer_boundary(root, Path(selection["outside_canary"]))
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    run = FourPhaseRun(root, selection, root / selection["output_relative"])
    result = run.run()
    print(json.dumps(result))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
