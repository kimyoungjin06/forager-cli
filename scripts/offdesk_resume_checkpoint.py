"""Bounded local checkpoint evidence, separate from execution or acceptance.

The caller must retain the returned hashes outside the files they authenticate.
This module never runs a worker, changes a checkpoint, or authenticates scientific
claims. Its time checks audit caller-supplied records, not process enforcement.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import stat
import time
import copy
from contextlib import contextmanager

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 65536
STAGES = frozenset({"metadata_policy_bridge"})
EFFECT = "observation_only_no_resume_or_acceptance"


class CheckpointError(ValueError):
    """An expected identity or complete completion record cannot be verified."""


def require(ok, message):
    if not ok:
        raise CheckpointError(message)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), "missing or unknown fields")


def identifier(value):
    require(type(value) is str and 0 < len(value) <= 100
            and all(c.isascii() and (c.isalnum() or c in "_-") for c in value), "invalid identifier")
    return value


def digest(value):
    require(type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value), "expected SHA256 required")
    return value


def relative(value):
    require(type(value) is str and 0 < len(value) <= 1000, "invalid relative path")
    parts = value.split("/")
    require(all(p and p not in (".", "..") and "\x00" not in p for p in parts),
            "noncanonical or escaping path")
    return parts


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def decode(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "duplicate JSON field")
            result[k] = v
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(CheckpointError("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CheckpointError("incomplete or invalid JSON") from error


@contextmanager
def directory(start, components, path_only=False):
    fd = os.dup(start)
    try:
        for part in components:
            access = getattr(os, 'O_PATH', os.O_RDONLY) if path_only else os.O_RDONLY
            nxt = os.open(part, access | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def identity(st):
    return st.st_dev, st.st_ino


def stamp(st):
    return identity(st) + (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)


@contextmanager
def root_handle(root):
    path = Path(os.path.abspath(root))
    # Traverse known ancestors without directory-read authority. Only the selected
    # root is opened for enumeration/fsync; Landlock still denies outside reads.
    base = os.open("/", getattr(os, 'O_PATH', os.O_RDONLY) | os.O_DIRECTORY)
    try:
        with directory(base, path.parts[1:], path_only=True) as traversal:
            fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=traversal)
            try:
                original = identity(os.fstat(fd))
                require(original == identity(os.fstat(traversal)), 'root replaced before open')
                yield fd
                with directory(base, path.parts[1:], path_only=True) as current:
                    require(identity(os.fstat(current)) == original, "root path replaced")
            finally:
                os.close(fd)
    finally:
        os.close(base)


def read_file(root, name, bound, retain=False):
    parts = relative(name)
    with directory(root, parts[:-1]) as parent:
        parent_id = identity(os.fstat(parent))
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "not a single-link regular file")
            require(before.st_size <= bound, "file exceeds declared local bound")
            h = hashlib.sha256()
            chunks = []
            size = 0
            while chunk := os.read(fd, min(1024 * 1024, bound + 1 - size)):
                size += len(chunk)
                require(size <= bound, "file grew beyond bound")
                h.update(chunk)
                if retain:
                    chunks.append(chunk)
            require(stamp(before) == stamp(os.fstat(fd)) and size == before.st_size,
                    "file changed during read")
            with directory(root, parts[:-1]) as current:
                require(identity(os.fstat(current)) == parent_id, "parent directory replaced")
                require(stamp(os.stat(parts[-1], dir_fd=current, follow_symlinks=False)) == stamp(before),
                        "file path replaced")
            return {"path": name, "bytes": size, "sha256": h.hexdigest()}, b"".join(chunks)
        finally:
            os.close(fd)


def bound_json(root, name, expected):
    digest(expected)
    binding, raw = read_file(root, name, MAX_JSON_BYTES, retain=True)
    require(binding["sha256"] == expected, "pinned JSON bytes changed")
    return decode(raw)


def binding_schema(row):
    keys(row, {"path", "bytes", "sha256"})
    relative(row["path"])
    digest(row["sha256"])
    require(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_FILE_BYTES, "invalid artifact size")


def limits_schema(limits):
    keys(limits, {"work_seconds", "shutdown_grace_seconds", "total_seconds"})
    require(all(type(v) is int and v >= 0 for v in limits.values()), "invalid time limits")
    require(limits["work_seconds"] > 0
            and limits["total_seconds"] >= limits["work_seconds"] + limits["shutdown_grace_seconds"],
            "work, grace and total limits disagree")


def contract_schema(contract):
    keys(contract, {"schema", "stage_id", "implementation", "configuration", "inputs", "checkpoints", "limits"})
    require(contract["schema"] == "forager.resume_stage.v1", "unsupported contract schema")
    require(type(contract["stage_id"]) is str and contract["stage_id"] in STAGES, "unsupported stage")
    limits_schema(contract["limits"])
    require(all(type(contract[k]) is list and 1 <= len(contract[k]) <= 32
                for k in ("inputs", "checkpoints")), "empty or excessive artifact set")
    rows = [contract["configuration"], contract["implementation"]] + contract["inputs"] + contract["checkpoints"]
    seen = set()
    for row in rows:
        binding_schema(row)
        require(row["path"] not in seen, "duplicate or ambiguous artifact path")
        seen.add(row["path"])
    require(sum(row["bytes"] for row in rows) <= MAX_TOTAL_BYTES, "artifact set exceeds local bound")
    return rows


def verify_artifacts(root, contract):
    for row in contract_schema(contract):
        current, _ = read_file(root, row["path"], row["bytes"])
        require(current == row, "artifact bytes or size changed: " + row["path"])


def accounting(attempts, limits):
    require(type(attempts) is list and 1 <= len(attempts) <= 64, "complete attempts required")
    ids = set()
    previous = None
    total_work = total_grace = total_wall = 0.0
    for index, row in enumerate(attempts):
        keys(row, {"attempt_id", "started_at", "work_ended_at", "ended_at", "outcome"})
        identifier(row["attempt_id"])
        require(row["attempt_id"] not in ids, "duplicate attempt")
        ids.add(row["attempt_id"])
        times = [row[k] for k in ("started_at", "work_ended_at", "ended_at")]
        require(all(type(t) in (float, int) and math.isfinite(t) and t >= 0 for t in times),
                "finite nonnegative times required")
        start, work_end, end = times
        require(start <= work_end <= end and (previous is None or start >= previous),
                "overlapping or reversed attempt")
        require(type(row["outcome"]) is str and row["outcome"] in {"failed", "interrupted", "completed"},
                "unknown attempt outcome")
        require((row["outcome"] == "completed") == (index == len(attempts) - 1),
                "only the last attempt may complete")
        require(work_end - start <= limits["work_seconds"], "work deadline exceeded")
        require(end - work_end <= limits["shutdown_grace_seconds"], "shutdown grace exceeded")
        total_work += work_end - start
        total_grace += end - work_end
        total_wall += end - start
        previous = end
    elapsed = attempts[-1]["ended_at"] - attempts[0]["started_at"]
    require(elapsed <= limits["total_seconds"], "total elapsed budget exceeded")
    return {"attempt_count": len(attempts), "attempt_wall_seconds": total_wall,
            "work_seconds": total_work, "shutdown_grace_seconds": total_grace,
            "elapsed_window_seconds": elapsed}


def publish(root, destination, value):
    raw = encode(value)
    require(len(raw) <= MAX_JSON_BYTES, "record exceeds local bound")
    parts = relative(destination)
    with directory(root, parts[:-1]) as parent:
        name = ".checkpoint-" + secrets.token_hex(16) + ".tmp"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(raw)
                out.flush()
                os.fsync(out.fileno())
            # A crash between link/unlink can leave two links; readers then refuse.
            os.link(name, parts[-1], src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    return hashlib.sha256(raw).hexdigest()


def freeze(root, contract_path, *, stage_id, implementation, configuration, inputs, checkpoints, limits):
    """Explicitly freeze current local bytes; this is not old-run provenance."""
    require(type(stage_id) is str and stage_id in STAGES, "unsupported stage")
    require(type(inputs) is list and type(checkpoints) is list
            and 1 <= len(inputs) <= 32 and 1 <= len(checkpoints) <= 32, "invalid artifact set")
    for path in [contract_path, configuration, implementation] + inputs + checkpoints:
        relative(path)
    require(contract_path not in [configuration, implementation] + inputs + checkpoints, "contract aliases artifact")
    require(len(set([configuration, implementation] + inputs + checkpoints)) == 2 + len(inputs) + len(checkpoints),
            "duplicate or ambiguous artifact path")
    with root_handle(root) as fd:
        rows = []
        total = 0
        for path in [configuration, implementation] + inputs + checkpoints:
            row, _ = read_file(fd, path, min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total))
            rows.append(row)
            total += row["bytes"]
        contract = {"schema": "forager.resume_stage.v1", "stage_id": stage_id,
                    "configuration": rows[0], "implementation": rows[1], "inputs": rows[2:2 + len(inputs)],
                    "checkpoints": rows[2 + len(inputs):], "limits": limits}
        verify_artifacts(fd, contract)
        result = publish(fd, contract_path, contract)
        verify_artifacts(fd, contract)
        return result


def publish_completion(root, contract_path, expected_contract_sha256, receipt_path, attempts):
    """Publish an immutable complete receipt, retaining all declared attempts."""
    require(receipt_path != contract_path, "receipt aliases contract")
    with root_handle(root) as fd:
        contract = bound_json(fd, contract_path, expected_contract_sha256)
        rows = contract_schema(contract)
        require(receipt_path not in [v["path"] for v in rows], "receipt aliases artifact")
        verify_artifacts(fd, contract)
        costs = accounting(attempts, contract["limits"])
        receipt = {"schema": "forager.resume_completion.v1", "stage_id": contract["stage_id"],
                   "contract_sha256": expected_contract_sha256, "attempts": attempts,
                   "accounting": costs, "effect": EFFECT}
        result = publish(fd, receipt_path, receipt)
        try:
            verify_artifacts(fd, contract)
            bound_json(fd, contract_path, expected_contract_sha256)
        except (CheckpointError, OSError) as error:
            raise CheckpointError("receipt persisted; final source validation failed") from error
        return result


def inspect_resume(root, contract_path, expected_contract_sha256, receipt_path, expected_receipt_sha256):
    """Read-only current-byte observation. Caller must recheck at actual entry."""
    require(contract_path != receipt_path, "receipt aliases contract")
    with root_handle(root) as fd:
        contract = bound_json(fd, contract_path, expected_contract_sha256)
        rows = contract_schema(contract)
        require(receipt_path not in [v["path"] for v in rows], "receipt aliases artifact")
        receipt = bound_json(fd, receipt_path, expected_receipt_sha256)
        keys(receipt, {"schema", "stage_id", "contract_sha256", "attempts", "accounting", "effect"})
        require(receipt["schema"] == "forager.resume_completion.v1"
                and receipt["stage_id"] == contract["stage_id"]
                and receipt["contract_sha256"] == expected_contract_sha256
                and receipt["effect"] == EFFECT, "receipt scope or schema mismatch")
        costs = accounting(receipt["attempts"], contract["limits"])
        keys(receipt["accounting"], set(costs))
        require(type(receipt["accounting"]["attempt_count"]) is int
                and all(type(v) in (int, float) and math.isfinite(v)
                        for k, v in receipt["accounting"].items() if k != "attempt_count"),
                "invalid accounting types")
        require(receipt["accounting"] == costs, "attempt accounting changed")
        verify_artifacts(fd, contract)
        bound_json(fd, receipt_path, expected_receipt_sha256)
        bound_json(fd, contract_path, expected_contract_sha256)
        return {"byte_binding_verified": True, "stage_id": contract["stage_id"],
                "accounting": costs, "resume_authorized": False,
                "scientific_acceptance": False, "effect": EFFECT}


PUBLICATION_SCOPE = "through_receipt_publication_and_revalidation_before_observation_publication"


def publication_now():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def publish_observed_completion(root, contract_path, contract_pin, receipt_path, attempts,
                                window_start, boot):
    """Measure durable receipt publication in a separately pinned observation.

    The observation cannot include its own final fsync in its stored timestamps.
    The caller checks the time after that write; an outer controller can also
    measure through child exit. Neither artifact authenticates its producer.
    """
    require(type(window_start) in (int, float) and math.isfinite(window_start)
            and 0 <= window_start <= publication_now(), "invalid publication window")
    require(type(boot) is str and 0 < len(boot) <= 100, "publication boot identity required")
    declared = copy.deepcopy(attempts)
    require(type(declared) is list and declared, "publication attempts required")
    declared[-1]["ended_at"] = publication_now()
    receipt_pin = publish_completion(root, contract_path, contract_pin, receipt_path, declared)
    observed = copy.deepcopy(declared)
    observed[-1]["ended_at"] = publication_now()
    with root_handle(root) as fd:
        contract = bound_json(fd, contract_path, contract_pin)
        costs = accounting(observed, contract["limits"])
        require(observed[-1]["ended_at"] - window_start <= contract["limits"]["total_seconds"],
                "publication exhausted original total window")
        observation = {"schema": "forager.resume_publication_observation.v1", "effect": EFFECT,
                       "measurement_scope": PUBLICATION_SCOPE, "contract_path": contract_path,
                       "contract_sha256": contract_pin, "receipt_path": receipt_path,
                       "receipt_sha256": receipt_pin, "window_start": window_start, "boot_id": boot,
                       "attempts": observed, "accounting": costs}
        publication = receipt_path + ".publication.json"
        require(publication not in [v["path"] for v in contract_schema(contract)] and publication != contract_path,
                "publication observation aliases contract artifacts")
        pin = publish(fd, publication, observation)
        verify_artifacts(fd, contract)
        after = publication_now()
        require(after - observed[-1]["work_ended_at"] <= contract["limits"]["shutdown_grace_seconds"]
                and after - window_start <= contract["limits"]["total_seconds"],
                "publication observation write exceeded grace or original window")
    return receipt_pin, publication, pin


def inspect_publication(root, contract_path, contract_pin, receipt_path, receipt_pin, publication, publication_pin):
    """Verify the separately retained measurement without authorizing resume."""
    require(type(publication) is str and publication == receipt_path + ".publication.json",
            "exact publication observation path required")
    digest(publication_pin)
    inspect_resume(root, contract_path, contract_pin, receipt_path, receipt_pin)
    with root_handle(root) as fd:
        contract = bound_json(fd, contract_path, contract_pin)
        receipt = bound_json(fd, receipt_path, receipt_pin)
        value = bound_json(fd, publication, publication_pin)
        keys(value, {"schema", "effect", "measurement_scope", "contract_path", "contract_sha256",
                     "receipt_path", "receipt_sha256", "window_start", "boot_id", "attempts", "accounting"})
        require(value["schema"] == "forager.resume_publication_observation.v1" and value["effect"] == EFFECT
                and value["measurement_scope"] == PUBLICATION_SCOPE
                and value["contract_path"] == contract_path and value["contract_sha256"] == contract_pin
                and value["receipt_path"] == receipt_path and value["receipt_sha256"] == receipt_pin,
                "publication observation identity or scope differs")
        measured = value["attempts"]
        costs = accounting(measured, contract["limits"])
        declared = copy.deepcopy(receipt["attempts"])
        require(len(measured) == len(declared) and measured[-1]["ended_at"] >= declared[-1]["ended_at"],
                "publication time precedes declared receipt end")
        declared[-1]["ended_at"] = measured[-1]["ended_at"]
        require(declared == measured and value["accounting"] == costs, "publication attempt accounting differs")
        keys(value["accounting"], set(costs))
        require(type(value["accounting"]["attempt_count"]) is int
                and all(type(v) in (int, float) and math.isfinite(v)
                        for key, v in value["accounting"].items() if key != "attempt_count"),
                "invalid publication accounting types")
        start = value["window_start"]
        require(type(start) in (int, float) and math.isfinite(start) and 0 <= start <= measured[0]["started_at"]
                and measured[-1]["ended_at"] - start <= contract["limits"]["total_seconds"],
                "publication original window differs or is exhausted")
        require(type(value["boot_id"]) is str and 0 < len(value["boot_id"]) <= 100, "publication boot identity missing")
        verify_artifacts(fd, contract)
        bound_json(fd, publication, publication_pin)
        return {"byte_binding_verified": True, "accounting": costs, "attempts": measured,
                "window_start": start, "boot_id": value["boot_id"], "measurement_scope": PUBLICATION_SCOPE,
                "resume_authorized": False, "scientific_acceptance": False}

