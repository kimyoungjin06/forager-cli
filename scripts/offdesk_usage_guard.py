#!/usr/bin/env python3
"""Observe subscription weekly usage and gate one explicitly selected command.

Only the owned command is stopped. No changes to provider credentials, service
configuration, canonical plans, or unrelated sessions are performed.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

SCHEMA = "offdesk_weekly_usage.v1"
STATE_SCHEMA = "offdesk_weekly_usage_budget.v1"
WEEK_MINUTES = 7 * 24 * 60
NATIVE_GPT_RESET_JITTER_SECONDS = 1
NIGHTLY_LIMIT_POINTS = 5
NIGHTLY_TIMEZONE = dt.timezone(dt.timedelta(hours=9), "Asia/Seoul")


def timestamp(value: str) -> float:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone is required")
    return parsed.timestamp()


def number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError("Usage must be numeric")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError("Usage must be between 0 and 100")
    return value


def normalize(provider: str, account: str, buckets: dict, observed_at: float) -> dict:
    if not isinstance(account, str) or not account.strip() or not buckets or not math.isfinite(observed_at):
        raise ValueError("Account identity, observation time and weekly meters required")
    meters = {}
    for name, (used, reset) in buckets.items():
        reset = number_epoch(reset)
        meters[name] = {"used_percent": number(used), "resets_at": reset}
    return {"schema": SCHEMA, "provider": provider, "account": account,
            "observed_at": observed_at, "weekly_meters": meters}


def number_epoch(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("Reset must be a finite epoch timestamp")
    return float(value)


def codex_snapshot(payload: dict, account: str, now: float) -> dict:
    # Prefer the complete bucket map. The legacy rateLimits entry can alias one
    # model bucket and must not be added as a second independent allowance.
    snapshots = payload.get("rateLimitsByLimitId")
    if snapshots is None:
        snapshots = {"default": payload.get("rateLimits")}
    if not isinstance(snapshots, dict):
        raise ValueError("Missing Codex rate-limit map")
    buckets = {}
    for key, snapshot in snapshots.items():
        if not isinstance(snapshot, dict):
            continue
        for window in ("primary", "secondary"):
            meter = snapshot.get(window)
            if isinstance(meter, dict) and meter.get("windowDurationMins") == WEEK_MINUTES:
                buckets[f"{key}:{window}"] = (meter.get("usedPercent"), meter.get("resetsAt"))
    return normalize("gpt", account, buckets, now)


def claude_snapshot(payload: dict, account: str, now: float) -> dict:
    # Official Claude Code status-line input, available after an API response.
    # Session cost, context-window tokens and spend_limit are not weekly quota.
    week = payload.get("rate_limits", {}).get("seven_day", {})
    return normalize("claude", account,
                     {"seven_day": (week.get("used_percentage"), week.get("resets_at"))}, now)


def codex_account_key(account: dict, authentication: dict) -> str:
    account_id = authentication.get("tokens", {}).get("account_id")
    if account.get("type") != "chatgpt" or not account.get("email") or not account_id:
        raise ValueError("A stable subscription account identity is required")
    # Bind the workspace identity as well as the account read. Token rotation
    # must not change identity, and no credential material enters the snapshot.
    identity = {key: account.get(key) for key in ("type", "email", "planType")}
    identity["account_id"] = account_id
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def read_codex(timeout: float = 20) -> dict:
    proc = subprocess.Popen(["codex", "app-server", "--stdio"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    buffer = b""
    until = time.monotonic() + timeout

    def request(identifier: int, method: str, params: dict) -> dict:
        nonlocal buffer
        proc.stdin.write((json.dumps({"id": identifier, "method": method, "params": params}) + "\n").encode())
        proc.stdin.flush()
        while time.monotonic() < until:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                message = json.loads(line)
                if message.get("id") == identifier:
                    if "error" in message:
                        raise ValueError("Codex usage read unavailable")
                    return message["result"]
            if selector.select(max(0, until - time.monotonic())):
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError("Codex app server exited")
                buffer += chunk
                if len(buffer) > 4 * 1024 * 1024:
                    raise ValueError("Codex response exceeds limit")
        raise ValueError("Codex usage read timed out")

    try:
        request(1, "initialize", {"clientInfo": {"name": "forager_usage_guard", "version": "1"}})
        proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        account = request(2, "account/read", {"refreshToken": False}).get("account")
        if not account:
            raise ValueError("A subscription account identity is required")
        auth_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        identity = codex_account_key(account, json.loads((auth_root / "auth.json").read_text()))
        payload = request(3, "account/rateLimits/read", {"excludeResetCreditDetails": True})
        return codex_snapshot(payload, identity, time.time())
    finally:
        selector.close()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_sample(sample: dict, provider: str, now: float, max_age: float) -> None:
    if sample.get("schema") != SCHEMA or sample.get("provider") != provider:
        raise ValueError("Sample schema/provider mismatch")
    observed = number_epoch(sample["observed_at"])
    if not 0 <= now - observed <= max_age:
        raise ValueError("Sample is stale or from the future")
    normalized = normalize(provider, sample["account"],
                           {key: (m["used_percent"], m["resets_at"])
                            for key, m in sample["weekly_meters"].items()}, observed)
    if any(m["resets_at"] <= now for m in normalized["weekly_meters"].values()):
        raise ValueError("Weekly window expired")


def same_weekly_reset(provider: str, key: str, initial: object, current: object, now: float) -> bool:
    first, observed = number_epoch(initial), number_epoch(current)
    if first == observed:
        return True
    # Native Codex weekly epochs can alternate by one whole second. Compare to
    # the original anchor, never a previous sample, and never extend its window.
    return (provider == "gpt" and key in ("codex:primary", "codex:secondary")
            and first.is_integer() and observed.is_integer()
            and abs(observed - first) <= NATIVE_GPT_RESET_JITTER_SECONDS
            and now < min(first, observed))


def nightly_window(now: float) -> tuple[str, float]:
    # The operational night crosses midnight and ends at the agreed 08:00 KST.
    local = dt.datetime.fromtimestamp(number_epoch(now), NIGHTLY_TIMEZONE)
    night = (local - dt.timedelta(hours=8)).date()
    stop = dt.datetime.combine(night + dt.timedelta(days=1), dt.time(8), NIGHTLY_TIMEZONE)
    return night.isoformat(), stop.timestamp()


def budget_contract(state: dict) -> dict:
    contract = {k: state[k] for k in ("schema", "deadline", "baselines", "limit_percentage_points")}
    if "nightly" in state:
        contract["nightly"] = state["nightly"]
    return contract


def json_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_nightly(state: dict, now: float, max_age: float) -> None:
    if "nightly" not in state:
        return
    scope = state["nightly"]
    fields = {"schema", "night", "timezone", "started_at", "stops_at"}
    amended = isinstance(scope, dict) and scope.get("schema") == "offdesk_nightly_budget.v2"
    if amended:
        fields.add("allowance_amendment")
    if not isinstance(scope, dict) or set(scope) != fields:
        raise ValueError("Invalid nightly budget identity")
    started = number_epoch(scope["started_at"])
    night, stop = nightly_window(started)
    if (scope["schema"] != ("offdesk_nightly_budget.v2" if amended else "offdesk_nightly_budget.v1") or scope["timezone"] != "Asia/Seoul"
            or scope["night"] != night or scope["stops_at"] != stop or state["deadline"] != stop
            or state["limit_percentage_points"] != (10 if amended else NIGHTLY_LIMIT_POINTS) or started > now):
        raise ValueError("Nightly identity, window or approved allowance changed")
    if amended:
        amendment = scope["allowance_amendment"]
        keys = {"schema", "request_id", "authority_ref", "approved_at", "previous_contract_sha256",
                "previous_state_sha256", "previous_status_sha256", "request_sha256",
                "from_percentage_points", "to_percentage_points", "providers"}
        if not isinstance(amendment, dict) or set(amendment) != keys:
            raise ValueError("Invalid nightly allowance amendment")
        if (amendment["schema"] != "offdesk_nightly_allowance_amendment.v1"
                or amendment["from_percentage_points"] != 5 or amendment["to_percentage_points"] != 10
                or amendment["providers"] != ["gpt"] or set(state["baselines"]) != {"gpt"}
                or not started <= number_epoch(amendment["approved_at"]) <= now
                or amendment["approved_at"] >= stop):
            raise ValueError("Allowance amendment scope or time differs")
        for key in ("request_id", "authority_ref"):
            if not isinstance(amendment[key], str) or not 0 < len(amendment[key]) <= 2000:
                raise ValueError("Allowance amendment authority missing")
        for key in ("previous_contract_sha256", "previous_state_sha256", "previous_status_sha256", "request_sha256"):
            if not isinstance(amendment[key], str) or len(amendment[key]) != 64 or any(c not in "0123456789abcdef" for c in amendment[key]):
                raise ValueError("Invalid allowance amendment hash")
        previous = copy.deepcopy(budget_contract(state))
        previous["limit_percentage_points"] = 5
        previous["nightly"].pop("allowance_amendment")
        previous["nightly"]["schema"] = "offdesk_nightly_budget.v1"
        if json_digest(previous) != amendment["previous_contract_sha256"]:
            raise ValueError("Original nightly budget changed under amendment")
    for baseline in state["baselines"].values():
        if not 0 <= started - number_epoch(baseline["observed_at"]) <= max_age:
            raise ValueError("Nightly baseline must be fresh at the original start")


def evaluate(state: dict, samples: dict, now: float, max_age: float = 300) -> dict:
    reasons = []
    usage = {}
    issues = {}
    if state.get("schema") != STATE_SCHEMA:
        raise ValueError("Budget schema mismatch")
    validate_nightly(state, now, max_age)
    if now >= state["deadline"]:
        reasons.append("deadline_reached")
    for provider, baseline in state["baselines"].items():
        try:
            current = samples[provider]
            valid_sample(current, provider, now, max_age)
            if current["account"] != baseline["account"]:
                raise ValueError("Account identity changed")
            if set(current["weekly_meters"]) != set(baseline["weekly_meters"]):
                raise ValueError("Weekly meter set changed")
            usage[provider] = {}
            for key, initial in baseline["weekly_meters"].items():
                meter = current["weekly_meters"][key]
                if not same_weekly_reset(provider, key, initial["resets_at"], meter["resets_at"], now):
                    raise ValueError("Weekly reset changed; no renewed allowance")
                high = state["high_water"][provider][key]
                if meter["used_percent"] < high:
                    raise ValueError("Usage decreased; reconciliation required")
                state["high_water"][provider][key] = meter["used_percent"]
                delta = meter["used_percent"] - initial["used_percent"]
                usage[provider][key] = delta
                if delta >= state["limit_percentage_points"] or meter["used_percent"] >= 100:
                    reasons.append(f"{provider}:{key}:budget_reached")
        except (ValueError, KeyError, TypeError) as error:
            reasons.append(f"{provider}:usage_unknown_or_changed")
            issues[provider] = str(error) if isinstance(error, ValueError) else "Invalid or missing meter fields"
    # A stop/unknown condition latches for this run. A fresh baseline requires a
    # new explicit state file; a reset or later successful poll cannot rearm it.
    if state.get("held"):
        reasons.append("previous_hold_requires_review")
    state["held"] = bool(reasons)
    return {"allowed": not reasons, "reasons": reasons, "issues": issues, "increment_percentage_points": usage,
            "observed_at": now, "account_usage_includes_other_sessions": True,
            "inflight_call_can_overshoot": True}


def evaluate_bound(payload: dict) -> dict:
    """Reuse the guard on caller-bounded snapshots without polling or file I/O."""
    state = payload["state"]
    contract = budget_contract(state)
    if contract != payload["contract"] or state["schema"] != STATE_SCHEMA:
        raise ValueError("Approved weekly budget changed")
    now = number_epoch(payload["now"])
    age = number_epoch(payload["max_age"])
    limit = number(state["limit_percentage_points"])
    if not 0 < age <= 300 or limit <= 0 or type(state["held"]) is not bool:
        raise ValueError("Invalid weekly budget policy")
    number_epoch(state["deadline"])
    providers = set(state["baselines"])
    if not providers or not providers <= {"gpt", "claude"} or set(state["high_water"]) != providers:
        raise ValueError("Invalid weekly budget provider scope")
    for provider, baseline in state["baselines"].items():
        observed = number_epoch(baseline["observed_at"])
        valid_sample(baseline, provider, observed, age)
        meters = baseline["weekly_meters"]
        if observed > now or not 1 <= len(meters) <= 64 or set(state["high_water"][provider]) != set(meters):
            raise ValueError("Invalid baseline or high-water scope")
        for key, meter in meters.items():
            if number(state["high_water"][provider][key]) < meter["used_percent"]:
                raise ValueError("High-water mark precedes baseline")
    return {"state": state, "verdict": evaluate(state, payload["samples"], now, age)}


def load_samples(paths: dict) -> dict:
    samples = {}
    for provider, path in paths.items():
        try:
            samples[provider] = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
    return samples


def initialize(samples: dict, deadline: float, now: float, limit: float, max_age: float) -> dict:
    if not samples or deadline <= now or not 0 < limit <= 100:
        raise ValueError("Fresh samples, future deadline and a valid budget required")
    for provider, sample in samples.items():
        if provider not in ("gpt", "claude"):
            raise ValueError("Unknown provider")
        valid_sample(sample, provider, now, max_age)
    return {"schema": STATE_SCHEMA, "deadline": deadline, "baselines": samples,
            "limit_percentage_points": limit, "held": False,
            "high_water": {p: {k: m["used_percent"] for k, m in s["weekly_meters"].items()}
                           for p, s in samples.items()}}


def initialize_nightly(root: Path, paths: dict, now: float, max_age: float = 300) -> dict:
    """Create or resume the one budget in the selected profile for this night."""
    if not root.is_dir() or not paths or not 0 < max_age <= 300:
        raise ValueError("Existing selected profile, provider samples and age <=300 required")
    root = root.resolve()
    night, stop = nightly_window(now)
    path = root / f"offdesk_weekly_night_{night}.json"
    # The deterministic name and shared evaluator lock prevent reconnects,
    # midnight, new request IDs or simultaneous init calls from rebasing usage.
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode) or os.fstat(lock.fileno()).st_nlink != 1:
            raise ValueError("Nightly lock must be an unaliased regular file")
        fcntl.flock(lock, fcntl.LOCK_EX)
        samples = load_samples(paths)
        created = not path.exists()
        if created:
            if set(samples) != set(paths):
                raise ValueError("Every selected provider needs a fresh nightly baseline")
            state = initialize(samples, stop, now, NIGHTLY_LIMIT_POINTS, max_age)
            state["nightly"] = {"schema": "offdesk_nightly_budget.v1", "night": night,
                                "timezone": "Asia/Seoul", "started_at": now, "stops_at": stop}
        else:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor) as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 131072:
                    raise ValueError("Nightly state must be a bounded unaliased regular file")
                state = json.load(stream)
            if not isinstance(state.get("nightly"), dict) or state["nightly"].get("night") != night or set(state["baselines"]) != set(paths):
                raise ValueError("Existing nightly state identity/provider scope differs; no rebaseline")
        verdict = evaluate_bound({"state": state, "contract": budget_contract(state), "samples": samples,
                                  "now": now, "max_age": max_age})["verdict"]
        if created:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        else:
            atomic_json(path, state)
        atomic_json(Path(str(path) + ".status.json"), verdict)
    return {"state_path": str(path), "initialized": created, "reused": not created,
            "nightly": state["nightly"], "limit_percentage_points": state["limit_percentage_points"],
            "baseline_used_percent": {p: {k: m["used_percent"] for k, m in s["weekly_meters"].items()}
                                      for p, s in state["baselines"].items()},
            "sampled_ceiling_percent": {p: {k: min(100, m["used_percent"] + state["limit_percentage_points"])
                                               for k, m in s["weekly_meters"].items()}
                                        for p, s in state["baselines"].items()},
            "verdict": verdict}


def _amendment_json(path: Path) -> tuple[dict, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 131072:
            raise ValueError("Bound amendment input must be a small unaliased regular file")
        raw = stream.read(131073)
    def unique(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate amendment input key")
            value[key] = item
        return value
    def nonfinite(value):
        raise ValueError("Nonfinite amendment input")
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError("Amendment object required")
    return value, hashlib.sha256(raw).hexdigest()


def _emit_amendment(path: Path, value: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def amend_nightly(state_path: Path, paths: dict, request_path: Path, receipt_path: Path,
                  now: float, max_age: float = 300) -> dict:
    """Apply one explicit five-to-ten-point grant without rebasing this night."""
    if state_path.resolve() != state_path or set(paths) != {"gpt"} or not 0 < max_age <= 300:
        raise ValueError("Exact nightly state and GPT sample scope required")
    request, request_sha = _amendment_json(request_path)
    keys = {"schema", "request_id", "authority_ref", "state_path", "state_sha256", "status_sha256",
            "contract_sha256", "from_percentage_points", "to_percentage_points"}
    if (set(request) != keys or request["schema"] != "offdesk_nightly_allowance_request.v1"
            or request["state_path"] != str(state_path) or request["from_percentage_points"] != 5
            or request["to_percentage_points"] != 10
            or any(not isinstance(request[k], str) or not 0 < len(request[k]) <= 2000 for k in ("request_id", "authority_ref"))):
        raise ValueError("Explicit five-to-ten-point request and authority required")
    if receipt_path.parent.resolve() != receipt_path.parent:
        raise ValueError("Canonical existing receipt directory required")
    descriptor = os.open(str(state_path) + ".lock", os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r+") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Invalid nightly amendment lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = Path(str(receipt_path) + ".intent.json")
        if os.path.lexists(receipt_path) or os.path.lexists(intent):
            raise ValueError("Amendment receipt or intent already exists; review without replay")
        state, state_sha = _amendment_json(state_path)
        prior_status, status_sha = _amendment_json(Path(str(state_path) + ".status.json"))
        night, stop = nightly_window(now)
        if (state_sha != request["state_sha256"] or status_sha != request["status_sha256"]
                or json_digest(budget_contract(state)) != request["contract_sha256"]
                or state_path.name != f"offdesk_weekly_night_{night}.json"
                or state.get("nightly", {}).get("schema") != "offdesk_nightly_budget.v1"
                or set(state["baselines"]) != {"gpt"} or state["deadline"] != stop or now >= stop
                or state["held"] is not True or prior_status.get("allowed") is not False
                or prior_status.get("issues") != {}):
            raise ValueError("Nightly amendment source, identity or budget-only hold differs")
        reasons = prior_status.get("reasons")
        if (not isinstance(reasons, list) or not any(isinstance(r, str) and r.startswith("gpt:") and r.endswith(":budget_reached") for r in reasons)
                or any(not isinstance(r, str) or not (r == "previous_hold_requires_review" or r.startswith("gpt:") and r.endswith(":budget_reached")) for r in reasons)):
            raise ValueError("Only a reviewed budget-reached hold can be rearmed")
        samples = {p: _amendment_json(path)[0] for p, path in paths.items()}
        checked = copy.deepcopy(state); checked["held"] = False
        previous = evaluate_bound({"state": checked, "contract": budget_contract(state),
                                   "samples": samples, "now": now, "max_age": max_age})["verdict"]
        if previous["issues"] or any(not r.endswith(":budget_reached") for r in previous["reasons"]):
            raise ValueError("Fresh sample has an unknown, changed or expired budget identity")
        candidate = checked
        candidate["limit_percentage_points"] = 10
        candidate["nightly"]["schema"] = "offdesk_nightly_budget.v2"
        candidate["nightly"]["allowance_amendment"] = {
            "schema": "offdesk_nightly_allowance_amendment.v1", "request_id": request["request_id"],
            "authority_ref": request["authority_ref"], "approved_at": now,
            "previous_contract_sha256": request["contract_sha256"], "previous_state_sha256": state_sha,
            "previous_status_sha256": status_sha, "request_sha256": request_sha,
            "from_percentage_points": 5, "to_percentage_points": 10, "providers": ["gpt"]}
        candidate["held"] = False
        verdict = evaluate_bound({"state": candidate, "contract": budget_contract(candidate),
                                  "samples": samples, "now": now, "max_age": max_age})["verdict"]
        if _amendment_json(request_path)[1] != request_sha:
            raise ValueError("Amendment request changed before publication")
        _emit_amendment(intent, {"schema": "nightly_allowance_amendment_intent.v1", "request": request,
                                "request_sha256": request_sha, "before_state": state, "after_state": candidate})
        atomic_json(state_path, candidate)
        directory = os.open(state_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        returned, after_sha = _amendment_json(state_path)
        if returned != candidate:
            raise ValueError("Amended state changed before receipt publication")
        atomic_json(Path(str(state_path) + ".status.json"), verdict)
        result = {"schema": "nightly_allowance_amendment_receipt.v1", "state_path": str(state_path),
                  "request_id": request["request_id"], "before_sha256": state_sha, "after_sha256": after_sha,
                  "original_baselines": state["baselines"], "high_water": candidate["high_water"],
                  "limit_percentage_points": 10, "new_baseline": False, "verdict": verdict}
        _emit_amendment(receipt_path, result)
        return result


def check_files(state_path: Path, paths: dict, max_age: float, refresh_codex: bool = False) -> dict:
    # The same lock serializes polling and guarded commands using this budget.
    with Path(str(state_path) + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(state_path.read_text())
        samples = load_samples(paths)
        if refresh_codex:
            try:
                sample = read_codex(timeout=max(0.1, min(20, state["deadline"] - time.time())))
                atomic_json(paths["gpt"], sample)
                samples["gpt"] = sample
            except (OSError, ValueError, KeyError, TypeError):
                samples.pop("gpt", None)
        verdict = evaluate(state, samples, time.time(), max_age)
        atomic_json(state_path, state)
        atomic_json(Path(str(state_path) + ".status.json"), verdict)
        return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["capture-codex", "capture-claude", "init", "init-nightly", "amend-nightly", "check", "watch", "run", "evaluate-stdin"])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--state-dir", type=Path, help="Selected profile directory for one shared nightly budget")
    parser.add_argument("--gpt-sample", type=Path)
    parser.add_argument("--claude-sample", type=Path)
    parser.add_argument("--account-key", help="Stable, operator-bound Claude account fingerprint")
    parser.add_argument("--deadline", help="RFC3339 with explicit timezone")
    parser.add_argument("--limit-percentage-points", type=float, default=NIGHTLY_LIMIT_POINTS)
    parser.add_argument("--max-age-seconds", type=float, default=300)
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--refresh-codex", action="store_true", help="Read native Codex weekly meters each poll")
    parser.add_argument("--amendment-request", type=Path)
    parser.add_argument("--amendment-receipt", type=Path)
    parser.add_argument("--confirm-nightly-amendment", action="store_true")
    argv = sys.argv[1:]
    separator = argv.index("--") if "--" in argv else len(argv)
    command = argv[separator + 1:]
    args = parser.parse_args(argv[:separator])
    if args.action == "evaluate-stdin":
        payload = json.load(sys.stdin)
        # Interpreter startup and scheduling can lag the caller's timestamp.
        # Admission must not make an expired sample fresh using an older clock.
        payload["now"] = max(number_epoch(payload["now"]), time.time())
        print(json.dumps(evaluate_bound(payload), allow_nan=False))
        return 0
    if not all(math.isfinite(v) and v > 0 for v in [args.max_age_seconds, args.interval_seconds]):
        parser.error("Age and interval must be positive")
    paths = {p: v for p, v in [("gpt", args.gpt_sample), ("claude", args.claude_sample)] if v}
    if args.action == "amend-nightly":
        if not (args.confirm_nightly_amendment and args.state and args.amendment_request and args.amendment_receipt) or args.state_dir or args.deadline or args.refresh_codex or command or args.limit_percentage_points != NIGHTLY_LIMIT_POINTS:
            parser.error("amend-nightly requires exact state/request/receipt and explicit confirmation")
        result = amend_nightly(args.state, paths, args.amendment_request, args.amendment_receipt, time.time(), args.max_age_seconds)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["verdict"]["allowed"] else 2
    if args.refresh_codex and ("gpt" not in paths or args.interval_seconds < 60):
        parser.error("Codex refresh requires --gpt-sample and an interval of at least 60 seconds")
    if args.action.startswith("capture-"):
        if args.out is None:
            parser.error("--out required")
        value = read_codex() if args.action == "capture-codex" else claude_snapshot(json.load(sys.stdin), args.account_key, time.time())
        atomic_json(args.out, value)
        print(json.dumps({"captured": value["provider"], "weekly_meter_count": len(value["weekly_meters"])}))
        return 0
    if args.action == "init-nightly":
        if args.state_dir is None or args.state is not None or args.deadline or args.limit_percentage_points != NIGHTLY_LIMIT_POINTS:
            parser.error("init-nightly requires --state-dir; its date, 08:00 KST deadline and five-point limit are fixed")
        result = initialize_nightly(args.state_dir, paths, time.time(), args.max_age_seconds)
        print(json.dumps(result, allow_nan=False))
        return 0 if result["verdict"]["allowed"] else 2
    if args.state_dir is not None:
        parser.error("--state-dir is only for init-nightly")
    if args.state is None or not paths:
        parser.error("--state and at least one provider sample required")
    if args.action == "init":
        if args.state.exists() or not args.deadline:
            parser.error("New state path and --deadline required; existing baselines cannot be overwritten")
        samples = load_samples(paths)
        if set(samples) != set(paths):
            raise ValueError("Every selected provider needs a valid sample")
        state = initialize(samples, timestamp(args.deadline), time.time(), args.limit_percentage_points, args.max_age_seconds)
        # O_EXCL prevents a concurrent initializer from replacing the baseline.
        with args.state.open("x") as stream:
            json.dump(state, stream, indent=2, allow_nan=False)
        print(json.dumps({"initialized": True, "providers": sorted(samples)}))
        return 0
    if args.action == "check":
        verdict = check_files(args.state, paths, args.max_age_seconds, args.refresh_codex)
        print(json.dumps(verdict))
        return 0 if verdict["allowed"] else 2
    if args.action == "run" and not command:
        parser.error("run requires -- COMMAND ARG...")
    # One caller owns one process group. Do not select a PID from a status file.
    process = None
    run_lock = Path(str(args.state) + ".run.lock").open("a")
    previous_term_handler = signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    try:
        fcntl.flock(run_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            verdict = check_files(args.state, paths, args.max_age_seconds, args.refresh_codex)
            if not verdict["allowed"]:
                print(json.dumps(verdict), flush=True)
                return 2
            if args.action == "run" and process is None:
                process = subprocess.Popen(command, start_new_session=True)
            if process is not None and process.poll() is not None:
                return process.returncode
            remaining = json.loads(args.state.read_text())["deadline"] - time.time()
            time.sleep(max(0.01, min(args.interval_seconds, remaining, 60)))
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            # The leader can exit before its ordinary background children.
            # The group belongs to this invocation even after leader exit.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        run_lock.close()
        signal.signal(signal.SIGTERM, previous_term_handler)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Never print provider payloads, credentials or subprocess stderr.
        print(json.dumps({"allowed": False, "reason": "usage_guard_unavailable", "error_type": type(error).__name__}))
        raise SystemExit(2)
