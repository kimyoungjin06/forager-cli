#!/usr/bin/env python3
"""Bound one selected native local worker; Forager still owns launch authority.

Prepare and preflight are inert. Run requires a fresh, exact preflight and must
be invoked through the ordinary approved Forager runner for Offdesk work.
Failure review is a separate, explicit command and never resumes the worker.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import ctypes
import datetime as dt
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid

RUNTIME_DIRS = (".runtime", ".night", ".codex")
SOURCE_EXCLUDED_ROOTS = (*RUNTIME_DIRS, "target", ".git")
PROGRESS_BINDING_KEYS = ("schema", "phase", "baseline_sha256", "root_identity", "run_binding")
MAX_PROGRESS_BYTES = 8192
MAX_CONTEXT_BYTES = 12000


class ProgressInputError(ValueError):
    def __init__(self, field: str, code: str, expected: str):
        super().__init__(expected)
        self.field, self.code, self.expected = field, code, expected


@contextlib.contextmanager
def night_parent(root: Path, path: Path):
    """Hold the selected .night parent without following directory links."""
    root = root.resolve()
    relative = path.relative_to(root) if path.is_absolute() else path
    if (not relative.parts or relative.parts[0] != ".night" or len(relative.parts) < 2
            or any(part in (".", "..") for part in relative.parts)):
        raise ValueError("Task file must stay inside the selected .night directory")
    descriptors = []
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        identity = os.fstat(descriptor)
        for part in relative.parts[:-1]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=descriptor)
            descriptors.append(descriptor)
        yield descriptor, relative.name
        current = root.stat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise ValueError("Execution root was replaced during task-file access")
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise
        raise ValueError("Unsafe or unavailable task-file path") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_task_json(root: Path, path: Path, limit: int) -> tuple[dict, str]:
    with night_parent(root, path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError("Task file must be a bounded regular file with one link")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            after = os.fstat(descriptor)
            active = os.stat(name, dir_fd=parent, follow_symlinks=False)
            snapshot = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns, row.st_nlink)
            if len(raw) > limit or snapshot(before) != snapshot(after) or snapshot(after) != snapshot(active):
                raise ValueError("Task file changed during bounded read")
        finally:
            os.close(descriptor)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Task JSON must contain one object")
    return data, hashlib.sha256(raw).hexdigest()


def progress_binding(data: dict) -> dict:
    identity = {key: data[key] for key in PROGRESS_BINDING_KEYS if key in data}
    baseline = identity.get("baseline_sha256")
    phase = identity.get("phase")
    if (identity.get("schema") != "offdesk_task_progress.v1"
            or not isinstance(phase, str) or not 1 <= len(phase) <= 100
            or not isinstance(baseline, str) or len(baseline) != 64
            or any(c not in "0123456789abcdef" for c in baseline)):
        raise ValueError("Valid progress identity required")
    if "root_identity" in identity:
        root = identity["root_identity"]
        if (not isinstance(root, dict) or set(root) != {"path", "device", "inode"}
                or not isinstance(root["path"], str) or len(root["path"]) > 2000
                or any(type(root[key]) is not int or root[key] < 0 for key in ("device", "inode"))):
            raise ValueError("Invalid progress root identity")
    if "run_binding" in identity:
        run = identity["run_binding"]
        if (not isinstance(run, dict) or set(run) != {"run_id", "task_id", "request_id"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in run.values())):
            raise ValueError("Invalid progress run binding")
    return identity


def validate_progress(data: dict, identity: dict) -> None:
    if progress_binding(data) != identity:
        raise ProgressInputError("binding", "binding_changed", "Preserve the exact task progress identity")
    for key in ("assessment", "next_action"):
        if not isinstance(data.get(key), str) or not 1 <= len(data[key]) <= 1500:
            raise ProgressInputError(key, "invalid_text", "A string of 1 to 1500 characters is required")
    for key in ("checked_commands", "changed_files"):
        if (not isinstance(data.get(key), list) or len(data[key]) > 16
                or any(not isinstance(value, str) or len(value) > 500 for value in data[key])):
            raise ProgressInputError(key, "invalid_string_array",
                                     "An array of at most 16 strings, each at most 500 characters; objects are not allowed")


def parse_progress_json(raw: str | bytes, field: str):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProgressInputError(field, "duplicate_json_key", "JSON object keys must be unique")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=object_pairs)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        raise ProgressInputError(field, "invalid_json",
                                 "Valid JSON is required; use repeated string options or --request-stdin to avoid shell JSON quoting") from None


def progress_cli_fields(args) -> dict:
    if args.request_stdin:
        if any(getattr(args, key) is not None for key in
               ("assessment", "next_action", "checks_json", "changes_json", "checked_command", "changed_file")):
            raise ProgressInputError("request", "mixed_input", "Use --request-stdin without other progress content options")
        raw = sys.stdin.buffer.read(MAX_PROGRESS_BYTES + 1)
        if len(raw) > MAX_PROGRESS_BYTES:
            raise ProgressInputError("request", "request_too_large", "The UTF-8 JSON request must not exceed 8192 bytes")
        fields = parse_progress_json(raw, "request")
        if not isinstance(fields, dict) or set(fields) != {"assessment", "next_action", "checked_commands", "changed_files"}:
            raise ProgressInputError("request", "invalid_fields",
                                     "One object with exactly assessment, next_action, checked_commands and changed_files is required")
        return fields
    fields = {"assessment": args.assessment, "next_action": args.next_action}
    for field, repeated, legacy in (("checked_commands", args.checked_command, args.checks_json),
                                     ("changed_files", args.changed_file, args.changes_json)):
        if repeated is not None and legacy is not None:
            raise ProgressInputError(field, "mixed_input", "Use repeated string options or the JSON option, not both")
        fields[field] = repeated if repeated is not None else parse_progress_json(legacy, field) if legacy is not None else []
    return fields


def progress_instructions(context: str, context_sha: str) -> str:
    command = shlex.join(["python3", "-I", "-S", ".runtime/offdesk_native_worker.py", "progress",
                          "--root", ".", "--context", context, "--expected-context-sha256", context_sha])
    return (f"Save progress using {command} --assessment 'Your actual assessment' "
            "--next-action 'One concrete next action'. Add --checked-command 'One action you actually performed' "
            "for each checked action, and --changed-file 'One source path you actually changed' for each changed file. "
            "Omit these options when there are none; do not invent completed checks or edits. "
            "assessment and next_action are strings of 1-1500 characters; checked_commands and changed_files "
            "are arrays of at most 16 strings of at most 500 characters, never objects. Total saved UTF-8 size <=8192 bytes. "
            "For complex text use the same root/context/hash options plus --request-stdin and send one JSON object "
            "with exactly those four fields through a quoted heredoc. Legacy JSON examples: "
            "--checks-json '[\"read selected excerpt\"]' --changes-json '[]'. "
            "A held result includes field/code/expected; correct that input before retrying. "
            "The writer preserves identity; model-reported checks remain unverified.\n"
            f"Structured example (replace the assessment and next action):\n{command} --request-stdin <<'FORAGER_PROGRESS_JSON'\n"
            '{"assessment":"Your actual assessment","next_action":"One concrete next action",'
            '"checked_commands":[],"changed_files":[]}\nFORAGER_PROGRESS_JSON\n')


def recovery_context(root: Path, context: Path, expected_context_sha256: str,
                     expected_progress_sha256: str) -> dict:
    """Read one pinned checkpoint; never launch a session or certify model claims."""
    root = root.resolve()
    data, context_sha = read_task_json(root, context, MAX_CONTEXT_BYTES)
    if not context.name.endswith("-context.json") or context_sha != expected_context_sha256:
        raise ValueError("Task context hash changed")
    identity = progress_binding(data)
    if identity.get("root_identity") != root_identity(root) or "run_binding" not in identity:
        raise ValueError("Recovery requires exact root and run/task/request binding")
    baseline_path = context.parent / "BASELINE.json"
    _, baseline_sha = read_task_json(root, baseline_path, 2 * 1024 * 1024)
    if baseline_sha != identity["baseline_sha256"]:
        raise ValueError("Task baseline hash changed")
    progress = context.with_name(context.name.removesuffix("-context.json") + "-progress.json")
    saved, progress_sha = read_task_json(root, progress, MAX_PROGRESS_BYTES)
    if progress_sha != expected_progress_sha256:
        raise ValueError("Task progress hash changed")
    validate_progress(saved, identity)
    # A fresh process can recheck these receipts. Model-reported checks remain unverified.
    if read_task_json(root, context, MAX_CONTEXT_BYTES)[1] != context_sha:
        raise ValueError("Task context changed during recovery read")
    if read_task_json(root, progress, MAX_PROGRESS_BYTES)[1] != progress_sha:
        raise ValueError("Task progress changed during recovery read")
    if read_task_json(root, baseline_path, 2 * 1024 * 1024)[1] != baseline_sha:
        raise ValueError("Task baseline changed during recovery read")
    return {"schema": "offdesk_recovery_context.v1", "binding": identity,
            "context_sha256": context_sha, "progress_sha256": progress_sha,
            "progress": saved, "next_action": saved["next_action"],
            "claims_verified": False, "read_only": True,
            "can_resume_worker": False, "provider_calls_authorized": False}


def source_inventory(root: Path) -> set[str]:
    files = set()
    for directory, directories, names in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        for name in directories[:]:
            path = Path(directory) / name
            if ((relative == Path(".") and name in SOURCE_EXCLUDED_ROOTS)
                    or name == "__pycache__"):
                directories.remove(name)
            elif path.is_symlink():
                files.add(str(path.relative_to(root)))
                directories.remove(name)
        files.update(str((Path(directory) / name).relative_to(root)) for name in names)
    return files


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_excerpt(root: Path, relative: str, start: int = 1, lines: int = 60) -> dict:
    path = root / relative
    if (Path(relative).is_absolute() or relative not in source_inventory(root)
            or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink()
            or not path.is_file() or path.stat().st_nlink != 1
            or path.stat().st_size > 2 * 1024 * 1024
            or type(start) is not int or start < 1 or type(lines) is not int or not 1 <= lines <= 80):
        raise ValueError("Selected regular source file and bounded positive line range required")
    text = path.read_text()
    selected = text.splitlines()[start - 1:start - 1 + lines]
    chunks = []
    used = 0
    for number, line in enumerate(selected, start):
        chunk = f"{number}: {line}\n"
        if used + len(chunk) > 4000:
            break
        chunks.append(chunk)
        used += len(chunk)
    return {"path": relative, "sha256": sha(path), "start": start,
            "lines_emitted": len(chunks), "text": "".join(chunks),
            "truncated": len(chunks) < len(selected)}


def edit_source(root: Path, request: dict) -> dict:
    relative = request["path"]
    source_excerpt(root, relative, 1, 1)
    path = root / relative
    before = path.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    old, new = request["old"], request["new"]
    if (digest != request["expected_sha256"] or not isinstance(old, str)
            or not isinstance(new, str) or not old or old == new
            or len(old) > 16000 or len(new) > 16000 or before.decode().count(old) != 1):
        raise ValueError("Exact source hash and exactly one nonempty, changed replacement required")
    changed = before.decode().replace(old, new, 1).encode()
    if changed == before:
        raise ValueError("Source edit had no effect")
    temporary = path.with_name("." + path.name + ".forager-edit")
    with temporary.open("xb") as stream:
        stream.write(changed)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(path.stat().st_mode)
    if path.is_symlink() or sha(path) != digest:
        raise ValueError("Source changed before replacement; edit candidate preserved")
    os.replace(temporary, path)
    return {"path": relative, "before_sha256": digest, "after_sha256": sha(path),
            "changed": True, "canonical_acceptance": False}


def save_progress(root: Path, context: Path, assessment: str, next_action: str,
                  checks: list, changes: list, expected_context_sha256: str | None = None) -> dict:
    root = root.resolve()
    if not context.name.endswith("-context.json"):
        raise ProgressInputError("context", "invalid_context", "A bounded regular task-context.json inside .night is required")
    data, context_sha = read_task_json(root, context, MAX_CONTEXT_BYTES)
    identity = progress_binding(data)
    if ((expected_context_sha256 is not None and context_sha != expected_context_sha256)
            or ("run_binding" in identity and expected_context_sha256 is None)):
        raise ProgressInputError("context", "context_hash_mismatch", "Exact reviewed task-context hash required; preserve and report changed binding")
    if "root_identity" in identity and identity["root_identity"] != root_identity(root):
        raise ProgressInputError("root", "root_identity_mismatch", "Use the original selected execution root; report changed identity")
    saved = {**identity, "assessment": assessment, "next_action": next_action,
             "checked_commands": checks, "changed_files": changes}
    validate_progress(saved, identity)
    raw = (json.dumps(saved, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_PROGRESS_BYTES:
        raise ProgressInputError("request", "progress_too_large", "Saved progress including binding must fit the 8192 UTF-8 byte budget")
    path = context.with_name(context.name.removesuffix("-context.json") + "-progress.json")
    try:
        previous, previous_sha = read_task_json(root, path, MAX_PROGRESS_BYTES)
        validate_progress(previous, identity)
    except FileNotFoundError:
        previous_sha = None
    with night_parent(root, path) as (parent, name):
        temporary = name + "." + uuid.uuid4().hex + ".partial"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if read_task_json(root, context, MAX_CONTEXT_BYTES)[1] != context_sha:
                raise ProgressInputError("context", "context_changed", "Context changed before write; preserve the reviewed binding")
            try:
                _, current_sha = read_task_json(root, path, MAX_PROGRESS_BYTES)
            except FileNotFoundError:
                current_sha = None
            if previous_sha != current_sha:
                raise ProgressInputError("progress", "progress_changed", "Progress changed before replacement; preserve and review the competing packet")
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
    return {"path": str(path), "sha256": read_task_json(root, path, MAX_PROGRESS_BYTES)[1],
            "claims_verified": False}


def independent_root(root: Path) -> None:
    if root.is_symlink() or not root.is_absolute() or root == Path("/") or root.resolve() != root:
        raise ValueError("An absolute independent execution root is required")
    for parent in root.parents:
        if any((parent / name).exists() for name in (".cargo/config", ".cargo/config.toml", "Cargo.toml")):
            raise ValueError("Execution root inherits ancestor Cargo configuration or workspace")


def root_identity(root: Path) -> dict:
    s = root.stat()
    return {"path": str(root), "device": s.st_dev, "inode": s.st_ino}


def source_identity(root: Path, manifest: dict) -> str:
    rows = []
    if source_inventory(root) != set(manifest["files"]):
        raise ValueError("Source inventory changed; additions and deletions require a new selected baseline")
    for name, item in sorted(manifest["files"].items()):
        relative = Path(name)
        if (not relative.parts or relative.is_absolute() or ".." in relative.parts
                or relative.parts[0] in SOURCE_EXCLUDED_ROOTS or "__pycache__" in relative.parts):
            raise ValueError("Invalid source-manifest path")
        path = root / relative
        if path.is_symlink() != (item["kind"] == "symlink"):
            raise ValueError("Source object type changed")
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Source path escapes execution root")
        value = os.readlink(path) if item["kind"] == "symlink" else sha(path)
        rows.append((name, item["kind"], value))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def prepare(snapshot_path: Path, root: Path) -> dict:
    independent_root(root)
    if root.exists():
        raise ValueError("Preparation requires a new execution root")
    snapshot = json.loads(snapshot_path.read_text())
    origin = Path(snapshot["snapshot_root"])
    source_identity(origin, snapshot)
    for name, item in snapshot["files"].items():
        if item["kind"] == "file" and sha(origin / name) != item["sha256"]:
            raise ValueError("Preserved input hash changed")
        if item["kind"] == "symlink" and os.readlink(origin / name) != item["target"]:
            raise ValueError("Preserved symlink changed")
    root.mkdir(mode=0o700, parents=True)
    for name, item in snapshot["files"].items():
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if item["kind"] == "symlink":
            destination.symlink_to(os.readlink(origin / name))
        else:
            shutil.copy2(origin / name, destination)
            destination.chmod(0o755 if (origin / name).stat().st_mode & 0o111 else 0o644)
    (root / ".runtime").mkdir(mode=0o700)
    write_json(root / ".runtime/source_manifest.json", snapshot)
    expected = source_identity(origin, snapshot)
    if source_identity(root, snapshot) != expected:
        raise ValueError("Copied execution input differs")
    return {"root": root_identity(root), "source_sha256": expected,
            "snapshot_sha256": sha(snapshot_path), "launch_authorized": False}


def worker_env(root: Path) -> dict:
    env = dict(os.environ)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    toolchain = root / ".runtime/toolchain/bin"
    if toolchain.exists():
        env.update(PATH=str(toolchain) + ":/usr/local/bin:/usr/bin:/bin",
                   CARGO_HOME=str(root / ".runtime/cargo"), CARGO_NET_OFFLINE="true", CARGO_BUILD_JOBS="2")
    env["TMPDIR"] = str(root / ".runtime/tmp")
    Path(env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    env["FORAGER_TEST_TEMP_ROOT"] = env["TMPDIR"]
    return env


def stop_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def bounded_command(argv: list[str], root: Path, timeout: float, env: dict) -> dict:
    process = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    previous_term = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return {"argv": argv, "exit_code": process.returncode,
                "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")}
    except subprocess.TimeoutExpired:
        stop_group(process)
        stdout, stderr = process.communicate()
        return {"argv": argv, "exit_code": process.returncode, "timed_out": True,
                "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace")}
    finally:
        stop_group(process)
        signal.signal(signal.SIGTERM, previous_term)


def test_runner(argv: list[str]) -> str | None:
    if not isinstance(argv, list) or not argv or any(not isinstance(value, str) for value in argv):
        return None
    if len(argv) > 1 and Path(argv[0]).name == "cargo" and argv[1] == "test":
        return "cargo"
    import re
    if not re.fullmatch(r"python3(?:\.\d+)?", Path(argv[0]).name):
        return None
    index = 1
    while index < len(argv) and argv[index] in ("-I", "-S", "-B", "-E", "-s", "-u"):
        index += 1
    return "unittest" if argv[index:index + 2] == ["-m", "unittest"] else None


def passed_test_count(runner: str | None, result: dict) -> int:
    import re
    if result.get("exit_code") != 0 or result.get("timed_out"):
        return 0
    if runner == "cargo":
        return sum(int(n) for n in re.findall(r"test result: ok\. (\d+) passed; 0 failed", result["stdout"]))
    if runner == "unittest":
        match = re.search(r"(?m)^Ran (\d+) tests? in [0-9.]+s\s+OK(?: \(skipped=(\d+)\))?\s*\Z", result["stderr"])
        if match:
            return max(0, int(match[1]) - int(match[2] or 0))
    return 0


def preflight(root: Path, output: Path, commands: list[list[str]], timeout: float) -> dict:
    independent_root(root)
    if not commands or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Actual test commands and a positive timeout required")
    if not all(isinstance(c, list) and c and all(isinstance(value, str) for value in c) for c in commands):
        raise ValueError("Explicit command argv required")
    runners = [test_runner(command) for command in commands]
    if not any(runners):
        raise ValueError("Preflight requires an actual cargo test or python3 -m unittest")
    manifest = json.loads((root / ".runtime/source_manifest.json").read_text())
    before = source_identity(root, manifest)
    results = []
    deadline = time.monotonic() + timeout
    for argv in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append({"argv": argv, "exit_code": None, "timed_out": True,
                            "stdout": "", "stderr": "Preflight time budget exhausted"})
            break
        write_json(output.with_name(output.name + ".progress.json"),
                   {"root": root_identity(root), "current_command": argv, "completed_checks": len(results)})
        result = bounded_command(argv, root, remaining, worker_env(root))
        results.append(result)
        if result["exit_code"] != 0 or result.get("timed_out"):
            break
    test_count = sum(passed_test_count(runner, result) for runner, result in zip(runners, results))
    passed = len(results) == len(commands) and all(r["exit_code"] == 0 for r in results) and test_count > 0
    if source_identity(root, manifest) != before:
        passed = False
    receipt = {"schema": "offdesk_native_preflight.v1", "root": root_identity(root),
               "source_sha256": before, "controller_sha256": sha(Path(__file__)),
               "completed_at": time.time(), "passed": passed, "tests_passed": test_count, "checks": results,
               "launch_authorized": False}
    write_json(output, receipt)
    return receipt


def validate_preflight_identity(root: Path, receipt: dict) -> None:
    independent_root(root)
    manifest = json.loads((root / ".runtime/source_manifest.json").read_text())
    if (receipt.get("schema") != "offdesk_native_preflight.v1" or receipt.get("passed") is not True
            or receipt.get("root") != root_identity(root)
            or receipt.get("controller_sha256") != sha(Path(__file__))
            or receipt.get("source_sha256") != source_identity(root, manifest)):
        raise ValueError("Preflight is failed or bound to different inputs")


def validate_preflight(root: Path, receipt: dict, now: float, max_age: float = 900) -> None:
    validate_preflight_identity(root, receipt)
    completed = receipt.get("completed_at", 0)
    if type(completed) not in (int, float) or not math.isfinite(completed) or not 0 <= now - completed <= max_age:
        raise ValueError("Preflight is stale or has an invalid completion time")


def failed_test_check(item: dict, root: Path | None = None) -> tuple | None:
    """Identify common failed test summaries, never certify a successful check."""
    command = item.get("command", "")
    if len(command) > 16384:
        return None
    try:
        for _ in range(4):
            argv = shlex.split(command)
            if len(argv) >= 3 and Path(argv[0]).name in ("bash", "sh", "zsh") and argv[1] in ("-c", "-lc"):
                command = argv[2]
            else:
                break
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    if len(tokens) > 2048:
        return None
    checks = []
    cwd = str(root) if root is not None else "."
    for index, token in enumerate(tokens):
        previous = index - 1
        while previous >= 0 and (re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[previous])
                                 or Path(tokens[previous]).name == "env"):
            previous -= 1
        if previous >= 0 and tokens[previous] not in (";", "&&", "||", "|", "&", "("):
            continue
        if token == "cd" and index + 1 < len(tokens):
            cwd = os.path.normpath(os.path.join(cwd, tokens[index + 1]))
        name = Path(token).name
        if name not in ("python", "python3", "cargo", "pytest", "pytest-3") and not re.fullmatch(r"python3\.\d+", name):
            continue
        arguments = []
        for value in tokens[index + 1:]:
            if value in (";", "&&", "||", "|", "&", ")") or ">" in value or "<" in value:
                break
            arguments.append(value)
        if name in ("python", "python3") or re.fullmatch(r"python3\.\d+", name):
            module = 0
            while module < len(arguments) and arguments[module] in ("-I", "-S", "-u", "-B"):
                module += 1
            if (arguments[module:module + 1] != ["-m"]
                    or arguments[module + 1:module + 2] not in (["unittest"], ["pytest"])):
                continue
            runner, arguments = arguments[module + 1], arguments[module + 2:]
        elif name == "cargo" and arguments[:1] == ["test"]:
            runner, arguments = "cargo-test", arguments[1:]
        elif name in ("pytest", "pytest-3"):
            runner = "pytest"
        else:
            continue
        checks.append((cwd, runner, tuple(a for a in arguments if a not in ("-v", "-vv", "-q", "--verbose", "--quiet", "--nocapture"))))
    if not checks:
        return None
    text = re.sub(r"\x1b\[[0-9;]*m", "", item.get("aggregated_output", "")[-65536:])
    summaries = re.findall(r"(?m)^FAILED(?: \([^\n]*\))?\s*$", text)
    summaries += re.findall(r"(?m)^test result: FAILED\. [^\n]*?\d+ failed", text)
    summaries += re.findall(r"(?m)^=+[^\n]*?\b\d+ failed", text)
    if not summaries:
        return None
    return tuple(checks), tuple(" ".join(row.split()) for row in summaries)


def live_source_identity(root: Path) -> str:
    manifest = {"files": {name: {"kind": "symlink" if (root / name).is_symlink() else "file"}
                          for name in source_inventory(root)}}
    return source_identity(root, manifest)


class ProgressGuard:
    def __init__(self, repeated_errors: int = 3, max_calls: int = 128,
                 repeated_command: int = 8, context_warnings: int = 3):
        if any(type(value) is not int or value <= 0 for value in
               (repeated_errors, max_calls, repeated_command, context_warnings)):
            raise ValueError("Positive integer native guard limits required")
        self.repeated_errors = repeated_errors
        self.max_calls = max_calls
        self.repeated_command = repeated_command
        self.context_warnings = context_warnings
        self.failures = collections.Counter()
        self.commands = collections.Counter()
        self.calls = 0
        self.warnings = 0
        self.seen_items = set()
        self.charged_items = set()
        self.started_items = set()
        self.seen_notices = set()
        self.failed_checks = collections.Counter()
        self.source_generation = None
        self.check_root = None

    def error(self, text: str) -> str | None:
        lower = text.lower()
        if any(marker in lower for marker in ("xml syntax error", "qwen tool call parsing failed")):
            return "tool_protocol_parse_error"
        if "stream closed before response.completed" in lower or "stream disconnected" in lower:
            fingerprint = "incomplete_provider_stream"
        elif "unsupported call:" in lower or "unknown process id" in lower or "persistent thread" in lower:
            fingerprint = "unusable_tool_protocol"
        elif "permission denied" in lower:
            fingerprint = "environment_permission_denied"
        else:
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
        self.failures[fingerprint] += 1
        return fingerprint if self.failures[fingerprint] >= self.repeated_errors else None

    def feed(self, event: dict, source_generation: str | None = None) -> str | None:
        if not valid_native_event(event):
            return "invalid_native_event"
        item = event.get("item", {})
        kind = event.get("type")
        if kind in ("item.started", "item.completed") and item.get("type") == "command_execution":
            identifier = item.get("id")
            command = item.get("command", "")
            if kind == "item.started":
                self.started_items.add(identifier)
            elif identifier in self.seen_items:
                return None
            else:
                self.seen_items.add(identifier)
            if identifier not in self.charged_items:
                self.charged_items.add(identifier)
                self.calls += 1
                self.commands[command] += 1
                # Native events are observations, not pre-execution admission.
                # Stop at the limit itself; the final command may be interrupted.
                if self.calls >= self.max_calls:
                    return "tool_call_budget_reached"
                if self.commands[command] >= self.repeated_command:
                    return "repeated_command_without_resolution"
            if kind == "item.started":
                return None
            check = failed_test_check(item, self.check_root)
            if check is not None:
                if source_generation != self.source_generation:
                    self.failed_checks.clear()
                    self.source_generation = source_generation
                self.failed_checks[check] += 1
                if self.failed_checks[check] >= self.repeated_errors:
                    return "repeated_failed_check_without_source_change"
                return None
            if self.calls >= self.max_calls:
                return "tool_call_budget_reached"
            if item.get("exit_code") not in (None, 0):
                return self.error(item.get("aggregated_output", "") or str(item.get("exit_code")))
        if kind == "item.completed" and item.get("type") == "error":
            text = item.get("message", "")
            identifier = item.get("id")
            if identifier:
                notice = (identifier, hashlib.sha256(text.encode()).hexdigest())
                if notice in self.seen_notices:
                    return None
                self.seen_notices.add(notice)
            if "compaction" in text.lower():
                self.warnings += 1
                return "context_warning_budget_reached" if self.warnings >= self.context_warnings else None
            return self.error(text)
        if kind in ("error", "turn.failed"):
            text = event.get("message") or event.get("error", {}).get("message", "provider failed")
            return self.error(text)
        return None


def valid_native_event(event: object) -> bool:
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        return False
    item = event.get("item", {})
    if not isinstance(item, dict):
        return False
    for container in (event, item):
        if "message" in container and not isinstance(container["message"], str):
            return False
    if "error" in event:
        error = event["error"]
        if not isinstance(error, dict) or not isinstance(error.get("message", ""), str):
            return False
    if item.get("type") == "error" and "id" in item:
        if not isinstance(item["id"], str) or not item["id"]:
            return False
    if item.get("type") == "command_execution":
        if not isinstance(item.get("id"), str) or not item["id"] or not isinstance(item.get("command", ""), str):
            return False
        if not isinstance(item.get("aggregated_output", ""), str):
            return False
        if item.get("exit_code") is not None and type(item["exit_code"]) is not int:
            return False
    return True


def verify_outer_boundary(root: Path, canary: Path) -> None:
    if canary.resolve().is_relative_to(root.resolve()) or canary.stat().st_uid != os.getuid():
        raise ValueError("A same-owner outside-root canary is required")
    for flags in (os.O_RDONLY, os.O_WRONLY):
        try:
            descriptor = os.open(canary, flags)
        except OSError as error:
            if error.errno != errno.EACCES:
                raise ValueError("Cannot verify outer filesystem restrictions") from error
        else:
            os.close(descriptor)
            raise ValueError("Outer filesystem restriction is absent")


def native_argv(executable: str, root: Path, model: str, final: Path, external: bool = False,
                helper_tool_profile: bool = False) -> list[str]:
    if type(helper_tool_profile) is not bool:
        raise ValueError("Explicit helper tool-profile selection required")
    return [executable, "exec", "--oss", "--local-provider", "ollama", "--model", model,
            "--ignore-user-config", "--ignore-rules", "--ephemeral", "--json", "--skip-git-repo-check",
            *( ["--dangerously-bypass-approvals-and-sandbox"] if external else ["--sandbox", "workspace-write", "-c", 'approval_policy="never"'] ), "--disable", "goals",
            "--disable", "memories", "--disable", "multi_agent", "--disable", "hooks",
            "--disable", "apps", "--disable", "browser_use", "--disable", "computer_use",
            "--disable", "code_mode", "-c", "model_context_window=32768",
            "-c", "model_auto_compact_token_limit=22000", "-c", "tool_output_token_limit=1200",
            "-c", "check_for_update_on_startup=false",
            *( ["-c", 'web_search="disabled"', "--disable", "view_image",
                "-c", "tools.experimental_request_user_input.enabled=false", "--strict-config"] if helper_tool_profile else [] ),
            "-C", str(root), "-o", str(final), "-"]


def supervise(argv: list[str], prompt: str, root: Path, output: Path, deadline: float,
              guard: ProgressGuard, env: dict, idle_seconds: float = 180,
              command_seconds: float = 900, progress_observer=None, source_observer=None,
              request_telemetry: bool = False, generation_policy=None) -> dict:
    if type(request_telemetry) is not bool:
        raise ValueError("Request telemetry must be explicitly boolean")
    if generation_policy is not None and not request_telemetry:
        raise ValueError("Selected generation policy requires the request relay")
    options = dict(idle_seconds=idle_seconds, command_seconds=command_seconds,
                   progress_observer=progress_observer, source_observer=source_observer)
    if not request_telemetry:
        return _supervise_native(argv, prompt, root, output, deadline, guard, env, **options)
    from offdesk_request_telemetry import RequestTelemetry
    evidence = output.parent / (output.name + "-requests")
    with RequestTelemetry(env["CODEX_OSS_BASE_URL"], evidence, deadline, generation_policy=generation_policy) as probe:
        selected_env = dict(env, CODEX_OSS_BASE_URL=probe.base_url)
        result = _supervise_native(argv, prompt, root, output, deadline, guard, selected_env,
                                   native_event_observer=probe.native_event,
                                   native_stop_observer=probe.observe_stop,
                                   response_progress_observer=probe.response_progress, **options)
    result["request_telemetry"] = {"summary": str(evidence / "summary.json"),
                                   "events": str(evidence / "events.jsonl"),
                                   "control_events": str(evidence / "control-events.jsonl"),
                                   "native_stop_snapshot": str(evidence / "native-stop.json"),
                                   "backend_retargeted": False, "connection_reuse": False}
    write_json(output / "result.json", result)
    if result["stop_reason"]:
        packet = json.loads((output / "council-request.json").read_text())
        failure_packet(output, result, packet["failure_counts"])
    return result


def _supervise_native(argv: list[str], prompt: str, root: Path, output: Path, deadline: float,
                      guard: ProgressGuard, env: dict, idle_seconds: float = 180,
                      command_seconds: float = 900, progress_observer=None, source_observer=None,
                      native_event_observer=None, native_stop_observer=None,
                      response_progress_observer=None) -> dict:
    if any(not math.isfinite(v) or v <= 0 for v in (idle_seconds, command_seconds)):
        raise ValueError("Positive finite idle and active-command timeouts required")
    if output.exists() or not math.isfinite(deadline) or deadline <= time.time():
        raise ValueError("Fresh output directory and future deadline required")
    guard.check_root = root
    if source_observer is None and (root / ".runtime/source_manifest.json").is_file():
        source_observer = lambda: live_source_identity(root)
    output.mkdir(mode=0o700, parents=True)
    process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": b"", "stderr": b""}
    logs = {name: (output / (name + ".log")).open("wb") for name in buffers}
    reason = None
    stop_observed = False
    stop_supervision = None

    def observe_stop_once():
        nonlocal stop_observed, stop_supervision
        if not stop_observed:
            stop_observed = True
            if stop_supervision is None:
                stop_supervision = supervision_snapshot()
            if native_stop_observer is not None:
                with contextlib.suppress(Exception):
                    native_stop_observer(reason)

    last_progress = time.monotonic()
    last_response = None
    seen_response_updates = set()
    last_tool_completed_at = None
    completed_turn = False
    turn_finished_at = None
    active_commands = {}
    logged_bytes = 0
    prompt_bytes = prompt.encode()
    prompt_sent = 0

    def supervision_snapshot():
        nonlocal last_response
        if response_progress_observer is not None:
            observed = response_progress_observer()
            if observed is not None and (last_response is None or observed["monotonic"] > last_response["monotonic"]):
                last_response = observed
        now = time.monotonic()
        response_age = None if last_response is None else max(0, now - last_response["monotonic"])
        activity_age = max(0, now - max(last_progress, last_response["monotonic"] if last_response else last_progress))
        state = ("turn_completed" if completed_turn else "command_in_flight" if active_commands else
                 "response_inactive" if activity_age >= idle_seconds else
                 "recent_response_progress" if response_age is not None and response_age < idle_seconds else
                 "awaiting_response")
        return {"policy": "response_completion.v1", "observed_at": time.time(), "activity_state": state,
                "response_idle_seconds": idle_seconds, "seconds_without_response_progress": response_age,
                "seconds_without_activity": activity_age,
                "last_response_progress_at": None if last_response is None else last_response["observed_at"],
                "last_response_kind": None if last_response is None else last_response["kind"]}

    previous_term = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        if prompt_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        else:
            process.stdin.close()
        while selector.get_map() and reason is None:
            if time.time() >= deadline:
                reason = "deadline_reached"
                break
            now = time.monotonic()
            if any(now - started >= command_seconds for started in active_commands.values()):
                reason = "active_command_timeout"
                break
            if turn_finished_at is not None and now - turn_finished_at >= 5:
                reason = "worker_did_not_exit"
                break
            supervision = supervision_snapshot()
            next_timeout = (turn_finished_at + 5 if turn_finished_at is not None
                            else min(started + command_seconds for started in active_commands.values())
                            if active_commands else now + max(0, idle_seconds - supervision["seconds_without_activity"]))
            wait = min(1, max(0, deadline - time.time()), max(0, next_timeout - now))
            for key, _ in selector.select(wait):
                name = key.data
                if name == "stdin":
                    try:
                        prompt_sent += os.write(key.fd, prompt_bytes[prompt_sent:prompt_sent + 4096])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        reason = "native_stdin_closed_before_prompt"
                    if reason or prompt_sent == len(prompt_bytes):
                        selector.unregister(key.fileobj)
                        process.stdin.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                logs[name].write(chunk)
                logs[name].flush()
                logged_bytes += len(chunk)
                if logged_bytes > 32 * 1024 * 1024:
                    reason = "log_size_budget_reached"
                    break
                buffers[name] += chunk
                if len(buffers[name]) > 16 * 1024 * 1024:
                    reason = "event_size_budget_reached"
                    break
                while b"\n" in buffers[name] and reason is None:
                    line, buffers[name] = buffers[name].split(b"\n", 1)
                    if name == "stderr":
                        text = line.decode(errors="replace")
                        if any(word in text for word in ("unsupported call:", "XML syntax error", "persistent thread", "Unknown process id")):
                            reason = guard.error(text)
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        reason = "invalid_native_event"
                        break
                    if not valid_native_event(event):
                        reason = "invalid_native_event"
                        break
                    if native_event_observer is not None:
                        native_event_observer(event)
                    item = event.get("item", {})
                    text = item.get("text")
                    if (event["type"] in ("item.updated", "item.completed")
                            and item.get("type") in ("reasoning", "agent_message")
                            and isinstance(text, str) and text.strip()):
                        identity = (str(item.get("id", "")), item["type"],
                                    hashlib.sha256(text.encode(errors="replace")).digest())
                        if identity not in seen_response_updates:
                            seen_response_updates.add(identity)
                            last_response = {"monotonic": time.monotonic(), "observed_at": time.time(),
                                             "kind": "reasoning" if item["type"] == "reasoning" else "content"}
                    new_completion = (event.get("type") == "item.completed"
                                      and item.get("type") == "command_execution"
                                      and item.get("id") not in guard.seen_items)
                    generation = None
                    if (source_observer is not None and event.get("type") == "item.completed"
                            and item.get("type") == "command_execution"
                            and item.get("id") not in guard.seen_items
                            and failed_test_check(item, root) is not None):
                        generation = source_observer()
                    reason = guard.feed(event, source_generation=generation)
                    if item.get("type") == "command_execution" and event.get("type") == "item.started":
                        if item["id"] not in guard.seen_items:
                            active_commands.setdefault(item["id"], time.monotonic())
                    if item.get("type") == "command_execution" and event.get("type") == "item.completed":
                        active_commands.pop(item["id"], None)
                        if new_completion:
                            last_progress = time.monotonic()
                            last_tool_completed_at = time.time()
                    if progress_observer is not None and event.get("type") == "item.completed" and (
                            item.get("type") == "command_execution"
                            or (item.get("type") == "error" and "compaction" in item.get("message", "").lower())):
                        snapshot = {"tool_calls": guard.calls, "context_warnings": guard.warnings,
                                    "observation": progress_observer(), "observed_at": time.time()}
                        with (output / "progress-checkpoints.jsonl").open("a") as stream:
                            stream.write(json.dumps(snapshot, allow_nan=False) + "\n")
                    if event.get("type") == "turn.completed":
                        completed_turn = True
                        turn_finished_at = time.monotonic()
                        if active_commands:
                            reason = "native_turn_completed_with_active_commands"
            # Drain queued events before declaring idle. Slow scheduling or
            # local log I/O must not discard already-available tool completion.
            # Streaming generation can outlast the tool-idle interval. Its
            # activity never increments useful-work counters or extends deadline.
            supervision = supervision_snapshot()
            if (reason is None and not completed_turn and not active_commands
                    and supervision["seconds_without_activity"] >= idle_seconds):
                reason = "response_inactive"
                stop_supervision = supervision
            write_json(output / "status.json", {"pid": process.pid, "tool_calls": guard.calls,
                                               "started_tool_calls": len(guard.started_items),
                                               "completed_tool_calls": len(guard.seen_items),
                                               "in_flight_tool_calls": len(guard.started_items - guard.seen_items),
                                               "last_tool_completed_at": last_tool_completed_at,
                                               "seconds_without_tool_progress": time.monotonic() - last_progress,
                                               "supervision": supervision,
                                               "stop_reason": reason, "deadline": deadline})
        if reason:
            observe_stop_once()
            stop_group(process)
        try:
            process.wait(timeout=max(0.1, min(5, deadline - time.time())))
        except subprocess.TimeoutExpired:
            reason = reason or "worker_did_not_exit"
            stop_group(process)
        if reason is None and prompt_sent != len(prompt_bytes):
            reason = "native_stdin_closed_before_prompt"
        reason = reason or (None if process.returncode == 0 and completed_turn else "native_worker_failed")
    except (KeyboardInterrupt, SystemExit):
        reason = "supervisor_interrupted"
    except Exception:
        reason = "native_supervisor_error"
    finally:
        observe_stop_once()
        stop_group(process)
        selector.close()
        process.stdin.close()
        for stream in logs.values():
            stream.close()
        process.stdout.close()
        process.stderr.close()
        signal.signal(signal.SIGTERM, previous_term)
    result = {"schema": "offdesk_native_worker_result.v1", "run_id": uuid.uuid4().hex,
              "root": root_identity(root), "exit_code": process.returncode, "stop_reason": reason,
              "turn_completed": completed_turn, "tool_calls": guard.calls,
              "prompt_bytes_total": len(prompt_bytes), "prompt_bytes_sent": prompt_sent,
              "prompt_delivery_complete": prompt_sent == len(prompt_bytes),
              "tool_call_accounting": "unique_started_or_completion_only",
              "started_tool_calls": len(guard.started_items),
              "completed_tool_calls": len(guard.seen_items),
              "in_flight_tool_calls": len(guard.started_items - guard.seen_items),
              "last_tool_completed_at": last_tool_completed_at,
              "seconds_without_tool_progress": time.monotonic() - last_progress,
              "supervision": stop_supervision,
              "failed_check_counts": {hashlib.sha256(json.dumps(check).encode()).hexdigest(): count
                                      for check, count in guard.failed_checks.items()},
              "failed_check_source_generation": guard.source_generation,
              "verification_state": "failed" if reason else "pending",
              "review_state": "pending_user_review", "ended_at": time.time()}
    write_json(output / "status.json", {"pid": process.pid, "tool_calls": guard.calls,
               "started_tool_calls": len(guard.started_items), "completed_tool_calls": len(guard.seen_items),
               "in_flight_tool_calls": len(guard.started_items - guard.seen_items),
               "supervision": result["supervision"],
               "stop_reason": reason, "deadline": deadline, "terminal": True})
    write_json(output / "result.json", result)
    if reason:
        failure_packet(output, result, {**dict(guard.failures),
            **{"failed_test_check:" + key: count for key, count in result["failed_check_counts"].items()}})
    return result


def failure_packet(output: Path, result: dict, failure_counts: dict) -> None:
    packet = {"schema": "offdesk_native_failure_council.v1", "run_id": result["run_id"],
                  "result": result, "result_sha256": sha(output / "result.json"),
                  "log_sha256": {n: sha(output / (n + ".log")) for n in ("stdout", "stderr")},
                  "failure_counts": failure_counts, "review_only": True,
                  "can_resume_worker": False, "provider_calls_authorized": False}
    write_json(output / "council-request.json", packet)


def validate_verification_commands(commands, allow_empty: bool = False) -> None:
    if not isinstance(commands, list) or (not commands and not allow_empty):
        raise ValueError("A complete nonempty verification command list is required")
    for argv in commands:
        if (not isinstance(argv, list) or not argv or not isinstance(argv[0], str) or not argv[0].strip()
                or any(not isinstance(value, str) or "\0" in value for value in argv)):
            raise ValueError("Every verification argv must be a nonempty list of strings with a nonblank program")


def verify_result(result: dict, output: Path, root: Path, commands: list[list[str]], deadline: float) -> dict:
    if result["stop_reason"] is not None:
        return result
    checks = []
    result["verification_state"] = "running"
    write_json(output / "result.json", result)
    try:
        validate_verification_commands(commands, allow_empty=True)
        for argv in commands:
            remaining = deadline - time.time()
            if remaining <= 0:
                result["stop_reason"] = "verification_deadline_reached"
                break
            check = bounded_command(argv, root, remaining, worker_env(root))
            checks.append(check)
            if check["exit_code"] != 0 or check.get("timed_out"):
                result["stop_reason"] = "post_edit_verification_failed"
                break
    except (KeyboardInterrupt, SystemExit):
        result["stop_reason"] = "verification_interrupted"
    except Exception as error:
        result["stop_reason"] = "post_edit_verification_error"
        result["verification_error"] = {"type": type(error).__name__}
    result["verification_checks"] = checks
    if isinstance(commands, list) and not commands:
        result["stop_reason"] = "post_edit_verification_missing"
    result["verification_state"] = "failed" if result["stop_reason"] else "passed"
    result["verification_ended_at"] = time.time()
    write_json(output / "result.json", result)
    if result["stop_reason"]:
        failure_packet(output, result, {})
    return result


def review_failure(packet_path: Path, argv: list[str], timeout: float) -> dict:
    packet = json.loads(packet_path.read_text())
    output = packet_path.parent
    if (not argv or not math.isfinite(timeout) or timeout <= 0
            or packet.get("schema") != "offdesk_native_failure_council.v1"
            or set(packet.get("log_sha256", {})) != {"stdout", "stderr"}
            or packet.get("review_only") is not True or packet.get("can_resume_worker") is not False
            or sha(output / "result.json") != packet["result_sha256"]
            or json.loads((output / "result.json").read_text())["run_id"] != packet["run_id"]
            or any(sha(output / (name + ".log")) != digest for name, digest in packet["log_sha256"].items())):
        raise ValueError("Failure evidence changed")
    claim = output / "council-review.claim.json"
    with claim.open("x") as stream:
        json.dump({"run_id": packet["run_id"], "claimed_at": time.time(), "argv": argv}, stream)
    prompt = "Review this failed run using only the bound evidence. Return JSON decision rework or defer and reason. Do not implement, resume, accept tasks or use tools.\n" + json.dumps(packet)
    response_path = output / "council-response.json"
    if response_path.exists():
        raise ValueError("Existing review response requires reconciliation")
    selected_argv = [a.replace("{response_path}", str(response_path)) for a in argv]
    review_env = {k: v for k, v in os.environ.items() if k not in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL"
    )}
    process = subprocess.Popen(selected_argv, cwd=output, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, env=review_env)
    try:
        stdout, stderr = process.communicate(prompt.encode(), timeout=timeout)
        (output / "council-review.stdout.log").write_bytes(stdout)
        (output / "council-review.stderr.log").write_bytes(stderr)
        verdict = json.loads(response_path.read_text() if response_path.exists() else stdout)
        if process.returncode != 0 or not isinstance(verdict, dict) or verdict.get("decision") not in ("rework", "defer"):
            raise ValueError("No valid review verdict")
        result = {"run_id": packet["run_id"], "review": verdict, "review_only": True, "can_resume_worker": False}
    except (ValueError, subprocess.TimeoutExpired):
        stop_group(process)
        stdout, stderr = process.communicate()
        (output / "council-review.stdout.log").write_bytes(stdout)
        (output / "council-review.stderr.log").write_bytes(stderr)
        write_json(output / "council-review.json", {"run_id": packet["run_id"], "status": "review_failed",
                                                  "review_only": True, "can_resume_worker": False})
        raise
    finally:
        stop_group(process)
    write_json(output / "council-review.json", result)
    return result


def watch_review(packet_path: Path, argv: list[str], deadline: float, timeout: float = 120) -> dict:
    # This host-side role is selected separately from the restricted worker.
    # It has no standing schedule and can consume only this exact failure.
    while time.time() < deadline:
        if packet_path.exists():
            return review_failure(packet_path, argv, min(timeout, max(0.1, deadline - time.time())))
        result_path = packet_path.parent / "result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            if (result.get("verification_state") == "passed"
                    and result.get("verification_checks") and result.get("stop_reason") is None):
                return {"status": "verified_without_failure", "provider_called": False}
        time.sleep(min(0.2, max(0, deadline - time.time())))
    return {"status": "review_window_expired", "provider_called": False}


@contextlib.contextmanager
def invocation_parent(root: Path, relative: str):
    path = Path(relative)
    if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] not in (".runtime", ".night")
            or any(part in (".", "..") for part in path.parts) or str(path) != relative):
        raise ValueError("Invocation files must be normalized .runtime or .night paths")
    descriptors = []
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        for part in path.parts[:-1]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            descriptors.append(descriptor)
        yield descriptor, path.name
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)


def invocation_file(root: Path, relative: str, limit: int = 1048576, contents: bool = True):
    with invocation_parent(root, relative) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError("Invalid invocation input file")
            digest = hashlib.sha256(); chunks = []; total = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk: break
                total += len(chunk)
                if total > limit: raise ValueError("Invocation input exceeds limit")
                digest.update(chunk)
                if contents: chunks.append(chunk)
            stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
            if stamp(before) != stamp(os.fstat(fd)) or stamp(before) != stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)):
                raise ValueError("Invocation input changed during read")
            return b"".join(chunks), digest.hexdigest()
        finally: os.close(fd)


def invocation_write(root: Path, relative: str, value: dict):
    if not sys.platform.startswith('linux'):
        raise ValueError('Bound invocation publication requires Linux renameat2')
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    with invocation_parent(root, relative) as (parent, name):
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, 'Invocation output already exists', relative)
        # The pending file is a durable one-attempt reservation. Preserve it on
        # interruption; readers must never consume its partially written bytes.
        pending = name + '.partial'
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'w') as output:
            os.fsync(parent)
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n'); output.flush(); os.fsync(output.fileno())
        # RENAME_NOREPLACE publishes complete single-link bytes without replacing
        # another owner and without a link/unlink metadata race for readers.
        if rename(parent, os.fsencode(pending), parent, os.fsencode(name), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), relative)
        os.fsync(parent)


def invocation_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("Duplicate invocation JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def invocation_entry_policy(contract: dict):
    policy = contract.get('entry_preflight')
    if contract.get('schema') == 'offdesk_native_invocation.v1':
        if policy is not None: raise ValueError('Fixed invocation cannot select entry preflight')
        return None
    if (contract.get('schema') != 'offdesk_native_invocation.v2' or not isinstance(policy, dict)
            or set(policy) != {'schema','timeout_seconds','closeout_reserve_seconds','minimum_worker_seconds'}
            or policy['schema'] != 'offdesk_native_entry_preflight.v1'):
        raise ValueError('Explicit entry preflight policy required')
    for name, low, high in [('timeout_seconds',1,3600),('closeout_reserve_seconds',5,3600),('minimum_worker_seconds',1,86400)]:
        if type(policy[name]) is not int or not low <= policy[name] <= high:
            raise ValueError('Invalid entry preflight/time reserve limit')
    if sum(policy[k] for k in ('timeout_seconds','closeout_reserve_seconds','minimum_worker_seconds')) > 86400:
        raise ValueError('Entry preflight policy exceeds maximum window')
    return policy


def invocation_remaining(contract: dict, policy: dict, include_preflight: bool):
    deadline=dt.datetime.fromisoformat(contract['deadline'].replace('Z','+00:00')).timestamp()
    required=policy['closeout_reserve_seconds']+policy['minimum_worker_seconds']
    if include_preflight: required+=policy['timeout_seconds']
    if deadline-time.time() < required: raise ValueError('Insufficient time for selected entry preflight, worker and closeout reserve')
    return deadline-policy['closeout_reserve_seconds']


def invocation_recheck_commands(receipt: dict):
    checks=receipt.get('checks')
    if not isinstance(checks,list) or not 1 <= len(checks) <= 128:
        raise ValueError('Bound preflight check argv required')
    commands=[]
    for check in checks:
        if not isinstance(check,dict) or check.get('exit_code') != 0 or check.get('timed_out'):
            raise ValueError('Bound preflight contains an unsuccessful check')
        commands.append(check.get('argv'))
    validate_verification_commands(commands)
    if not any(test_runner(command) for command in commands):
        raise ValueError('Entry preflight requires recorded actual test commands')
    return commands


def validate_invocation(root: Path, contract: dict):
    independent_root(root)
    fields = {"schema", "root", "task_id", "request_id", "project_key", "controller", "source_manifest", "preflight", "prompt", "worker", "source_sha256", "out", "base_url", "model", "deadline", "max_tool_calls", "max_repeated_errors", "command_timeout", "verify_commands"}
    policy=invocation_entry_policy(contract)
    if policy is not None: fields.add('entry_preflight')
    if set(contract) != fields or contract["root"] != root_identity(root):
        raise ValueError("Invocation shape or root identity changed")
    for name in ("task_id", "request_id", "project_key", "model", "out", "base_url", "deadline"):
        if not isinstance(contract[name], str) or not 1 <= len(contract[name]) <= 200 or any(ord(c) < 32 for c in contract[name]):
            raise ValueError("Invalid invocation identity/model")
    for name, maximum in (("max_tool_calls", 4096), ("max_repeated_errors", 100), ("command_timeout", 86400)):
        if type(contract[name]) is not int or not 1 <= contract[name] <= maximum:
            raise ValueError("Invalid invocation limit")
    deadline = dt.datetime.fromisoformat(contract["deadline"].replace('Z', '+00:00'))
    if deadline.tzinfo is None or not time.time() < deadline.timestamp() <= time.time() + 86400:
        raise ValueError("Invocation requires a future bounded deadline")
    parsed = urllib.parse.urlsplit(contract["base_url"])
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or len(contract["base_url"]) > 2048):
        raise ValueError("Invalid explicit invocation endpoint")
    for name in ('controller', 'source_manifest', 'preflight', 'prompt', 'worker'):
        if not isinstance(contract[name], dict) or set(contract[name]) != {'path', 'sha256'} or any(not isinstance(v, str) for v in contract[name].values()):
            raise ValueError("Invalid invocation input shape")
    if contract["controller"]["path"] != '.runtime/offdesk_native_worker.py' or contract["source_manifest"]["path"] != '.runtime/source_manifest.json':
        raise ValueError("Invalid bound controller/source manifest path")
    data = {}
    for name in ('controller', 'source_manifest', 'preflight', 'prompt', 'worker'):
        item = contract[name]
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise ValueError("Invalid invocation input binding")
        if name in ('preflight', 'worker') and not item['path'].startswith('.runtime/'):
            raise ValueError("Invocation runtime input path required")
        if name == 'prompt' and not item['path'].startswith('.night/'):
            raise ValueError("Invocation prompt must be under .night")
        raw, digest = invocation_file(root, item['path'], 268435456 if name == 'worker' else 1048576, name != 'worker')
        if digest != item['sha256']: raise ValueError("Invocation input hash changed")
        data[name] = raw
    if contract['controller']['sha256'] != sha(Path(__file__)):
        raise ValueError("Invocation controller differs from the executing adapter")
    if not contract['out'].startswith('.night/'):
        raise ValueError("Invocation output must stay under .night")
    with invocation_parent(root, contract['out'] + '.invocation-entry.json') as (parent, name):
        try:
            os.stat(name + '.partial', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(errno.EEXIST, 'Invocation entry publication already reserved; reconcile without replay', contract['out'])
    if (root/contract['out']).exists() or (root/contract['out']).is_symlink():
        raise ValueError('Fresh invocation output directory required')
    preflight = invocation_json(data['preflight'])
    if (preflight.get('passed') is not True or type(preflight.get('tests_passed')) is not int or preflight['tests_passed'] <= 0
            or preflight.get('source_sha256') != contract['source_sha256']):
        raise ValueError("Invocation requires a successful counted preflight")
    if policy is None:
        validate_preflight(root, preflight, time.time())
    else:
        completed=preflight.get('completed_at')
        if type(completed) not in (int,float) or not math.isfinite(completed) or completed > time.time():
            raise ValueError('Invalid recorded preflight completion time')
        validate_preflight_identity(root,preflight)
        invocation_recheck_commands(preflight)
        invocation_remaining(contract,policy,True)
        for suffix in ('.entry-preflight.json','.entry-preflight.json.progress.json','.entry-preflight.json.partial','.entry-preflight.json.progress.json.partial','.result.json'):
            if os.path.lexists(root/(contract['out']+suffix)):
                raise ValueError('Entry preflight/output evidence already exists; reconcile without replay')
    validate_verification_commands(contract['verify_commands'])
    return data['prompt'].decode('utf-8')


def bind_invocation(root: Path, spec: dict, output: str):
    spec_fields = {'task_id','request_id','project_key','preflight','prompt','worker','out','base_url','model','deadline','max_tool_calls','max_repeated_errors','command_timeout','verify_commands'}
    if 'entry_preflight' in spec: spec_fields.add('entry_preflight')
    if set(spec) != spec_fields: raise ValueError("Explicit invocation choices required")
    if not output.startswith(".runtime/"): raise ValueError("Invocation contract must be under .runtime")
    contract = {key: value for key, value in spec.items() if key not in ('preflight','prompt','worker')}
    contract.update(schema='offdesk_native_invocation.v2' if 'entry_preflight' in spec else 'offdesk_native_invocation.v1', root=root_identity(root))
    for name, path in [('controller','.runtime/offdesk_native_worker.py'), ('source_manifest','.runtime/source_manifest.json'), *[(key,spec[key]) for key in ('preflight','prompt','worker')]]:
        _, digest = invocation_file(root, path, 268435456 if name == 'worker' else 1048576, False)
        contract[name] = {'path':path,'sha256':digest}
    raw, _ = invocation_file(root, spec['preflight'])
    contract['source_sha256'] = invocation_json(raw)['source_sha256']
    validate_invocation(root, contract)
    invocation_write(root, output, contract)
    _, digest = invocation_file(root, output, 65536)
    bootstrap = Path(__file__).with_name('native_worker_bootstrap.py').read_text()
    quote = lambda value: "'" + value.replace("'", "'\"'\"'") + "'"
    argv = ['/usr/bin/python3','-I','-S','-c',bootstrap,str(root / contract['controller']['path']),contract['controller']['sha256'],
            'run-bound','--root',str(root),'--contract',output,'--expected-contract-sha256',digest]
    return {'schema':'offdesk_native_invocation_selection.v1','command':' '.join(quote(s) for s in argv),
            'artifact_ref':{'artifact_id':'native-worker-invocation-sha256:'+digest,'path':str(root/output),'present':True},
            'provider_id':'ollama','model':contract['model'],'result_artifact_path':str(root/(contract['out']+'.result.json')),
            'launch_authorized':False,'worker_qualification_complete':False}


def run_bound(root: Path, path: str, expected: str):
    raw, digest = invocation_file(root, path, 65536)
    if digest != expected: raise ValueError("Invocation contract hash changed")
    contract = invocation_json(raw)
    prompt = validate_invocation(root, contract)
    # Reserve once before the worker starts. Uncertain entry is never auto-replayed.
    invocation_write(root, contract['out'] + '.invocation-entry.json',
        {'schema':'offdesk_native_invocation_entry.v1','invocation_sha256':digest,'task_id':contract['task_id'],
         'request_id':contract['request_id'],'entered_at':time.time(),'worker_qualification_complete':False})
    args = argparse.Namespace(root=root, out=root/contract['out'], preflight=root/contract['preflight']['path'],
        prompt=root/contract['prompt']['path'], deadline=contract['deadline'], base_url=contract['base_url'], model=contract['model'],
        codex=str(root/contract['worker']['path']), max_tool_calls=contract['max_tool_calls'], max_repeated_errors=contract['max_repeated_errors'],
        command_timeout=contract['command_timeout'], verify_commands_json=json.dumps(contract['verify_commands']),
        external_sandbox=False, boundary_canary=None, request_telemetry=False)
    policy=invocation_entry_policy(contract)
    if policy is None:
        value=run_native(args,prompt)
    else:
        # This is approved task work, never a preview action or a receipt rewrite.
        invocation_remaining(contract,policy,True)
        raw,_=invocation_file(root,contract['preflight']['path'])
        commands=invocation_recheck_commands(invocation_json(raw))
        fresh_path=contract['out']+'.entry-preflight.json'
        fresh=preflight(root,root/fresh_path,commands,policy['timeout_seconds'])
        with invocation_parent(root,fresh_path) as (parent,_): os.fsync(parent)
        _,fresh_hash=invocation_file(root,fresh_path)
        if not fresh['passed']:
            return {'schema':'offdesk_native_invocation_result.v1','stop_reason':'entry_preflight_failed',
                    'invocation_sha256':digest,'entry_preflight_sha256':fresh_hash,'provider_called':False,
                    'worker_qualification_complete':False,'review_state':'pending_user_review'}
        validate_preflight(root,fresh,time.time())
        worker_deadline=invocation_remaining(contract,policy,False)
        value=run_native(args,prompt,worker_deadline=worker_deadline,preflight_receipt=fresh)
        value.update(entry_preflight_sha256=fresh_hash,entry_preflight_tests_passed=fresh['tests_passed'])

    value.update(invocation_sha256=digest, invocation_binding_verified=True, worker_qualification_complete=False)
    if value.get('stop_reason') is None:
        invocation_write(root, contract['out']+'.result.json', value)
    return value


def run_native(args, prompt_text=None, worker_deadline=None, preflight_receipt=None):
    root = args.root
    validate_preflight(root, preflight_receipt if preflight_receipt is not None else json.loads(args.preflight.read_text()), time.time())
    if args.external_sandbox:
        if args.boundary_canary is None:
            raise ValueError("External sandbox requires an outside-root canary")
        verify_outer_boundary(root, args.boundary_canary)
    if not args.out.resolve().is_relative_to(root.resolve()):
        raise ValueError("Worker evidence must stay inside execution root")
    parsed = urllib.parse.urlsplit(args.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid explicit local endpoint")
    env = worker_env(root);env["CODEX_OSS_BASE_URL"] = args.base_url.rstrip("/") + "/v1"
    deadline = dt.datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
    if (deadline.tzinfo is None or args.max_tool_calls <= 0 or args.max_repeated_errors <= 0
            or not math.isfinite(args.command_timeout) or args.command_timeout <= 0):
        raise ValueError("Explicit timezone and positive worker limits required")
    verify_commands = json.loads(args.verify_commands_json)
    validate_verification_commands(verify_commands)
    value = supervise(native_argv(args.codex, root, args.model, args.out / "final.txt", args.external_sandbox),
                      prompt_text if prompt_text is not None else args.prompt.read_text(), root, args.out, worker_deadline if worker_deadline is not None else deadline.timestamp(),
                      ProgressGuard(repeated_errors=args.max_repeated_errors, max_calls=args.max_tool_calls), env,
                      command_seconds=args.command_timeout, request_telemetry=args.request_telemetry)
    value = verify_result(value, args.out, root, verify_commands, deadline.timestamp())
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--snapshot", type=Path, required=True)
    prep.add_argument("--root", type=Path, required=True)
    check = sub.add_parser("preflight")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--out", type=Path, required=True)
    check.add_argument("--commands-json", required=True)
    check.add_argument("--timeout", type=float, default=900)
    excerpt = sub.add_parser("excerpt")
    excerpt.add_argument("--root", type=Path, required=True)
    excerpt.add_argument("--path", required=True)
    excerpt.add_argument("--start", type=int, default=1)
    excerpt.add_argument("--lines", type=int, default=60)
    edit = sub.add_parser("edit")
    edit.add_argument("--root", type=Path, required=True)
    edit.add_argument("--request", type=Path, required=True)
    progress = sub.add_parser("progress")
    progress.add_argument("--root", type=Path, required=True)
    progress.add_argument("--context", type=Path, required=True)
    progress.add_argument("--assessment", help="Actual assessment, 1-1500 characters")
    progress.add_argument("--next-action", help="One concrete next action, 1-1500 characters")
    progress.add_argument("--checked-command", action="append", help="One checked action string, <=500 characters; repeat up to 16 times")
    progress.add_argument("--changed-file", action="append", help="One changed source path string, <=500 characters; repeat up to 16 times")
    progress.add_argument("--checks-json", help='Legacy array of strings, for example: ["read selected excerpt"]')
    progress.add_argument("--changes-json", help='Legacy array of source-path strings, or []')
    progress.add_argument("--request-stdin", action="store_true", help="Read a <=8192-byte JSON object containing the four progress content fields from stdin")
    progress.add_argument("--expected-context-sha256")
    recovery = sub.add_parser("recovery-context")
    recovery.add_argument("--root", type=Path, required=True)
    recovery.add_argument("--context", type=Path, required=True)
    recovery.add_argument("--expected-context-sha256", required=True)
    recovery.add_argument("--expected-progress-sha256", required=True)
    run = sub.add_parser("run")
    for name in ("root", "out", "preflight", "prompt"):
        run.add_argument("--" + name, type=Path, required=True)
    for name in ("deadline", "base-url", "model", "verify-commands-json"):
        run.add_argument("--" + name, required=True)
    run.add_argument("--codex", default="codex")
    run.add_argument("--max-tool-calls", type=int, default=128)
    run.add_argument("--max-repeated-errors", type=int, default=3)
    run.add_argument("--command-timeout", type=float, default=900)
    run.add_argument("--external-sandbox", action="store_true")
    run.add_argument("--request-telemetry", action="store_true")
    run.add_argument("--boundary-canary", type=Path)
    review = sub.add_parser("review-failure")
    review.add_argument("--packet", type=Path, required=True)
    review.add_argument("--argv-json", required=True)
    review.add_argument("--timeout", type=float, default=120)
    watch = sub.add_parser("watch-review")
    watch.add_argument("--packet", type=Path, required=True)
    watch.add_argument("--argv-json", required=True)
    watch.add_argument("--deadline", required=True)
    watch.add_argument("--timeout", type=float, default=120)
    bind = sub.add_parser("bind-invocation")
    bind.add_argument("--root", type=Path, required=True)
    bind.add_argument("--spec", required=True)
    bind.add_argument("--out", required=True)
    bound = sub.add_parser("run-bound")
    bound.add_argument("--root", type=Path, required=True)
    bound.add_argument("--contract", required=True)
    bound.add_argument("--expected-contract-sha256", required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        value = prepare(args.snapshot, args.root)
    elif args.action == "preflight":
        value = preflight(args.root, args.out, json.loads(args.commands_json), args.timeout)
    elif args.action == "excerpt":
        value = source_excerpt(args.root, args.path, args.start, args.lines)
    elif args.action == "edit":
        value = edit_source(args.root, json.loads(args.request.read_text()))
    elif args.action == "progress":
        fields = progress_cli_fields(args)
        value = save_progress(args.root, args.context, fields["assessment"], fields["next_action"],
                              fields["checked_commands"], fields["changed_files"], args.expected_context_sha256)
    elif args.action == "recovery-context":
        value = recovery_context(args.root, args.context, args.expected_context_sha256,
                                 args.expected_progress_sha256)
    elif args.action == "review-failure":
        value = review_failure(args.packet, json.loads(args.argv_json), args.timeout)
    elif args.action == "watch-review":
        deadline = dt.datetime.fromisoformat(args.deadline.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            raise ValueError("An explicit review-window timezone is required")
        value = watch_review(args.packet, json.loads(args.argv_json), deadline.timestamp(), args.timeout)
    elif args.action == "bind-invocation":
        raw, _ = invocation_file(args.root, args.spec, 65536)
        value = bind_invocation(args.root, invocation_json(raw), args.out)
    elif args.action == "run-bound":
        value = run_bound(args.root, args.contract, args.expected_contract_sha256)
    else:
        value = run_native(args)
    print(json.dumps(value, ensure_ascii=False))
    return 0 if value.get("passed", value.get("stop_reason") is None) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        payload = {"status": "held", "reason": type(error).__name__}
        if isinstance(error, ProgressInputError):
            payload.update(field=error.field, code=error.code, expected=error.expected)
        print(json.dumps(payload))
        raise SystemExit(2)
