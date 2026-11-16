"""Guarded remote execution surface for the Telegram operator.

This module lets the operator drive the existing receipt-gated ondesk
executors from Telegram without widening their authority. Every mutation
follows the same shape the local web bridge uses:

1. Export a fresh operator-safe ``workstation_surface.v1``.
2. Rebuild the executable action envelope from that surface, never from
   operator-supplied JSON.
3. Require an explicit one-tap confirmation bound to the decision id,
   action kind, and observed hash, with a nonce and TTL.
4. Run the canonical ondesk executor chain, which independently
   re-validates the observed hash, nonce, and expiry before recording a
   receipt.

The module never executes arbitrary shell text and never records accepted
truth. It only orchestrates commands the CLI already exposes.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import time
import uuid
from typing import Any

from .common import RemoteOperatorTelegramError, utc_now
from .persistence import parse_utc_timestamp
from .remote_session_identity import pending_action_identity_issue
from .rendering import sanitize_text

CONFIRMATION_SCHEMA = "telegram_dispatch_confirmation.v1"
DISPATCH_RESULT_SCHEMA = "telegram_dispatch_result.v1"
DEFAULT_CONFIRMATION_TTL_SEC = 300
WORKSTATION_SURFACE_SCHEMA = "workstation_surface.v1"
ACTION_ENVELOPE_SCHEMA = "action_envelope.v1"


def run_forager_json(
    forager_bin: str,
    profile: str,
    argv_tail: list[str],
    *,
    label: str,
) -> dict[str, Any]:
    """Run a forager subcommand that emits a single JSON object on stdout."""

    argv = [forager_bin]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(argv_tail)
    try:
        process = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, ValueError) as error:
        # ValueError covers operator text with an embedded NUL byte, which
        # subprocess rejects; convert it to a handled adapter error so it
        # cannot escape into the poll loop.
        raise RemoteOperatorTelegramError(f"{label} could not start: {error}") from error
    if process.returncode != 0:
        detail = sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=240)
        raise RemoteOperatorTelegramError(f"{label} failed: {detail}")
    try:
        parsed = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError(f"{label} did not return JSON") from error
    if not isinstance(parsed, dict):
        raise RemoteOperatorTelegramError(f"{label} did not return a JSON object")
    return parsed


def export_workstation_surface(
    forager_bin: str,
    profile: str,
    *,
    shift_id: str | None = None,
    current_run_id: str | None = None,
) -> dict[str, Any]:
    argv = ["ondesk", "workstation-surface"]
    if shift_id:
        argv.extend(["--shift-id", shift_id])
    if current_run_id:
        argv.extend(["--current-run-id", current_run_id])
    argv.append("--json")
    surface = run_forager_json(
        forager_bin,
        profile,
        argv,
        label="workstation surface export",
    )
    if surface.get("schema") != WORKSTATION_SURFACE_SCHEMA:
        raise RemoteOperatorTelegramError(
            f"unexpected workstation surface schema: {surface.get('schema')}"
        )
    redaction = surface.get("redaction")
    if not isinstance(redaction, dict) or redaction.get("operator_safe") is not True:
        raise RemoteOperatorTelegramError("refusing non-operator-safe workstation surface")
    return surface


def decision_inbox_items(surface: dict[str, Any]) -> list[dict[str, Any]]:
    inbox = surface.get("decision_inbox")
    if not isinstance(inbox, dict):
        return []
    items = inbox.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def open_decision_actions(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize open decisions and their available action kinds."""

    decisions: list[dict[str, Any]] = []
    for item in decision_inbox_items(surface):
        envelopes = item.get("action_envelopes")
        actions: list[dict[str, Any]] = []
        if isinstance(envelopes, list):
            for envelope in envelopes:
                if not isinstance(envelope, dict):
                    continue
                action_kind = str(envelope.get("action_kind") or "").strip()
                if not action_kind:
                    continue
                latest = envelope.get("latest_receipt")
                stale = bool(latest.get("stale")) if isinstance(latest, dict) else False
                actions.append(
                    {
                        "action_kind": action_kind,
                        "observed_hash": str(envelope.get("observed_hash") or ""),
                        "requires_confirmation": bool(envelope.get("requires_confirmation")),
                        "stale": stale,
                    }
                )
        decisions.append(
            {
                "decision_id": str(item.get("decision_id") or ""),
                "title": sanitize_text(str(item.get("title") or ""), max_chars=120),
                "status": str(item.get("status") or ""),
                "actions": actions,
            }
        )
    return decisions


def find_action_envelope(
    surface: dict[str, Any],
    decision_id: str,
    action_kind: str,
) -> dict[str, Any] | None:
    wanted_decision = str(decision_id or "").strip()
    wanted_action = str(action_kind or "").strip().lower()
    for item in decision_inbox_items(surface):
        if str(item.get("decision_id") or "").strip() != wanted_decision:
            continue
        envelopes = item.get("action_envelopes")
        if not isinstance(envelopes, list):
            return None
        for envelope in envelopes:
            if not isinstance(envelope, dict):
                continue
            if str(envelope.get("action_kind") or "").strip().lower() == wanted_action:
                return envelope
    return None


def available_action_kinds(surface: dict[str, Any], decision_id: str) -> list[str]:
    for decision in open_decision_actions(surface):
        if decision["decision_id"] == str(decision_id or "").strip():
            return [action["action_kind"] for action in decision["actions"]]
    return []


def recovery_surface_items(surface: dict[str, Any]) -> list[dict[str, Any]]:
    recovery = surface.get("accepted_truth_recovery")
    if not isinstance(recovery, dict):
        return []
    items = recovery.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def open_recovery_actions(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize accepted-truth recovery follow-ups and their action kinds."""

    recoveries: list[dict[str, Any]] = []
    for item in recovery_surface_items(surface):
        envelopes = item.get("action_envelopes")
        actions: list[dict[str, Any]] = []
        if isinstance(envelopes, list):
            for envelope in envelopes:
                if not isinstance(envelope, dict):
                    continue
                action_kind = str(envelope.get("action_kind") or "").strip()
                if not action_kind:
                    continue
                latest = envelope.get("latest_receipt")
                stale = bool(latest.get("stale")) if isinstance(latest, dict) else False
                actions.append(
                    {
                        "action_kind": action_kind,
                        "observed_hash": str(envelope.get("observed_hash") or ""),
                        "stale": stale,
                    }
                )
        recoveries.append(
            {
                "closeout_id": str(item.get("closeout_id") or ""),
                "stage": str(item.get("stage") or ""),
                "acceptance_status": str(item.get("acceptance_status") or ""),
                "next_safe_action": sanitize_text(
                    str(item.get("next_safe_action") or ""), max_chars=120
                ),
                "actions": actions,
            }
        )
    return recoveries


def find_recovery_envelope(
    surface: dict[str, Any],
    closeout_id: str,
    action_kind: str,
) -> dict[str, Any] | None:
    wanted_closeout = str(closeout_id or "").strip()
    wanted_action = str(action_kind or "").strip().lower()
    for item in recovery_surface_items(surface):
        if str(item.get("closeout_id") or "").strip() != wanted_closeout:
            continue
        envelopes = item.get("action_envelopes")
        if not isinstance(envelopes, list):
            return None
        for envelope in envelopes:
            if not isinstance(envelope, dict):
                continue
            if str(envelope.get("action_kind") or "").strip().lower() == wanted_action:
                return envelope
    return None


def available_recovery_action_kinds(surface: dict[str, Any], closeout_id: str) -> list[str]:
    for recovery in open_recovery_actions(surface):
        if recovery["closeout_id"] == str(closeout_id or "").strip():
            return [action["action_kind"] for action in recovery["actions"]]
    return []


def build_confirmation(
    *,
    kind: str,
    target_id: str,
    action_kind: str,
    observed_hash: str,
    note: str,
    chat_hash: str | None,
    user_hash: str | None = None,
    ttl_sec: int = DEFAULT_CONFIRMATION_TTL_SEC,
) -> dict[str, Any]:
    now = utc_now()
    token = uuid.uuid4().hex[:12]
    expiry_seconds = max(30, int(ttl_sec))
    return {
        "schema": CONFIRMATION_SCHEMA,
        "token": token,
        "kind": str(kind or "decision").strip().lower(),
        "target_id": str(target_id or "").strip(),
        "action_kind": str(action_kind or "").strip().lower(),
        "observed_hash": str(observed_hash or ""),
        "note": sanitize_text(note, max_chars=400),
        "chat_id_hash": chat_hash,
        "user_id_hash": user_hash,
        "created_at": now,
        "ttl_sec": expiry_seconds,
    }


def store_confirmation(
    state: dict[str, Any],
    chat_hash: str | None,
    user_hash: str | None,
    confirmation: dict[str, Any],
) -> dict[str, Any] | None:
    """Store a pending confirmation, returning any it superseded.

    Only one pending confirmation per private chat: a new request from the same
    authenticated operator supersedes the old, so the previous token stops
    matching on /confirm. The returned previous confirmation lets callers tell
    the operator a pending action was replaced.
    """

    pending = state.setdefault("pending_dispatch_confirmations_by_chat", {})
    if not isinstance(pending, dict):
        pending = {}
        state["pending_dispatch_confirmations_by_chat"] = pending
    key = str(chat_hash or "")
    identity = str(user_hash or "")
    if not key or not identity:
        raise RemoteOperatorTelegramError(
            "confirmation requires chat and user identity hashes"
        )
    confirmation["chat_id_hash"] = key
    confirmation["user_id_hash"] = identity
    generation = str(state.get("control_generation_sha256") or "").strip()
    if generation:
        confirmation["control_generation_sha256"] = generation
    previous = pending.get(key)
    pending[key] = confirmation
    return previous if isinstance(previous, dict) else None


def confirmation_is_fresh(confirmation: dict[str, Any]) -> bool:
    created = parse_utc_timestamp(confirmation.get("created_at"))
    if created is None:
        return False
    ttl = int(confirmation.get("ttl_sec") or DEFAULT_CONFIRMATION_TTL_SEC)
    age = (parse_utc_timestamp(utc_now()) - created).total_seconds()
    return age <= ttl


def clear_confirmation(
    state: dict[str, Any],
    chat_hash: str | None,
    user_hash: str | None,
    token: str = "",
) -> bool:
    pending = state.get("pending_dispatch_confirmations_by_chat")
    if not isinstance(pending, dict):
        return False
    key = str(chat_hash or "")
    confirmation = pending.get(key)
    if not isinstance(confirmation, dict):
        return False
    if str(confirmation.get("user_id_hash") or "") != str(user_hash or ""):
        return False
    expected_token = str(token or "").strip()
    if expected_token and str(confirmation.get("token") or "") != expected_token:
        return False
    del pending[key]
    return True


def pop_confirmation(
    state: dict[str, Any],
    chat_hash: str | None,
    user_hash: str | None,
    token: str,
) -> dict[str, Any] | None:
    pending = state.get("pending_dispatch_confirmations_by_chat")
    if not isinstance(pending, dict):
        return None
    key = str(chat_hash or "")
    confirmation = pending.get(key)
    if not isinstance(confirmation, dict):
        return None
    if str(confirmation.get("user_id_hash") or "") != str(user_hash or ""):
        return None
    current_generation = str(state.get("control_generation_sha256") or "").strip()
    confirmation_generation = str(
        confirmation.get("control_generation_sha256") or ""
    ).strip()
    if current_generation and confirmation_generation != current_generation:
        del pending[key]
        return None
    token = str(token or "").strip()
    # An empty token confirms the single pending confirmation for this chat (the
    # "확인" button path); a non-empty token must match the pending one exactly
    # (explicit /confirm <token>).
    if token and str(confirmation.get("token") or "") != token:
        return None
    # A matched confirmation is single-use whether or not the chain later succeeds.
    del pending[key]
    return confirmation


def apply_decision_action(
    forager_bin: str,
    profile: str,
    envelope: dict[str, Any],
    *,
    note: str,
) -> dict[str, Any]:
    """Run the full ondesk decision executor chain for one action envelope.

    Returns a compact result summary. The CLI re-validates the envelope's
    observed hash, nonce, and expiry, so a stale envelope is rejected here
    with a receipt rather than mutating anything.
    """

    result: dict[str, Any] = {
        "schema": DISPATCH_RESULT_SCHEMA,
        "ok": False,
        "stage": "action_envelope",
        "decision_id": str((envelope.get("target_ref") or {}).get("decision_id") or ""),
        "action_kind": str(envelope.get("action_kind") or ""),
    }
    envelope_path = _write_temp_envelope(envelope)
    try:
        validated = run_forager_json(
            forager_bin,
            profile,
            ["ondesk", "action-envelope", "--envelope", str(envelope_path), "--json"],
            label="action envelope validation",
        )
        receipt = validated.get("receipt") if isinstance(validated.get("receipt"), dict) else {}
        receipt_status = str(receipt.get("result_status") or "")
        result["receipt_id"] = receipt.get("receipt_id")
        result["receipt_status"] = receipt_status
        # action-envelope records a read-only "validated_preview" receipt when
        # the envelope still matches the current decision ledger; anything else
        # (or a stale flag) means the envelope must not proceed.
        if receipt.get("stale") or receipt_status != "validated_preview":
            result["stage"] = "action_envelope_rejected"
            result["error"] = sanitize_text(
                str(receipt.get("reason") or "envelope rejected"), max_chars=240
            )
            return result

        preflight = run_forager_json(
            forager_bin,
            profile,
            ["ondesk", "action-preflight", "--receipt-id", str(receipt["receipt_id"]), "--json"],
            label="action preflight",
        )
        preflight_obj = preflight.get("preflight") if isinstance(preflight.get("preflight"), dict) else {}
        result["preflight_id"] = preflight_obj.get("preflight_id")
        if str(preflight_obj.get("result_status") or "") != "ready_for_executor":
            result["stage"] = "action_preflight_rejected"
            result["error"] = sanitize_text(
                str(preflight_obj.get("reason") or "preflight not ready"), max_chars=240
            )
            return result

        decision_argv = [
            "ondesk",
            "action-decision",
            "--preflight-id",
            str(preflight_obj["preflight_id"]),
            "--json",
        ]
        cleaned_note = sanitize_text(note, max_chars=400)
        if cleaned_note:
            decision_argv.extend(["--note", cleaned_note])
        execution = run_forager_json(
            forager_bin,
            profile,
            decision_argv,
            label="decision action",
        )
        execution_obj = execution.get("execution") if isinstance(execution.get("execution"), dict) else {}
        execution_status = str(execution_obj.get("result_status") or "")
        result["execution_id"] = execution_obj.get("execution_id")
        result["execution_status"] = execution_status
        result["decision"] = execution_obj.get("decision")
        result["decision_appended"] = bool(execution.get("decision_appended"))

        closeout = run_forager_json(
            forager_bin,
            profile,
            [
                "ondesk",
                "action-closeout",
                "--execution-id",
                str(execution_obj["execution_id"]),
                "--json",
            ],
            label="decision action closeout",
        )
        closeout_obj = closeout.get("closeout") if isinstance(closeout.get("closeout"), dict) else {}
        result["closeout_status"] = str(closeout_obj.get("result_status") or "")
        result["closeout_appended"] = bool(closeout.get("closeout_appended"))

        if execution_status == "applied":
            result["ok"] = True
            result["stage"] = "applied"
        else:
            # A "blocked" execution is a valid, recorded outcome (e.g. revise
            # without a direction note), not a transport failure.
            result["stage"] = "execution_not_applied"
            result["error"] = sanitize_text(
                str(execution_obj.get("reason") or f"execution {execution_status}"),
                max_chars=240,
            )
        return result
    finally:
        try:
            envelope_path.unlink()
        except OSError:
            pass


CANCELLABLE_TASK_STATUSES_EXCLUDED = {"completed", "cancelled"}


def find_tmux_session_name(session_id: str) -> str | None:
    """Resolve the tmux session whose name ends with the 8-char id suffix."""

    suffix = "_" + str(session_id or "")[:8]
    try:
        listing = subprocess.run(
            ["tmux", "ls", "-F", "#S"], check=False, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    names = [name for name in listing.stdout.splitlines() if name.endswith(suffix)]
    return names[0] if len(names) == 1 else None


def capture_session_tail(tmux_name: str, *, lines: int = 30) -> str:
    """Read-only tail of a tmux pane, for summarizing what the agent asks."""

    try:
        capture = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", tmux_name], check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    rows = [line.rstrip() for line in capture.stdout.splitlines()]
    rows = [line for line in rows if line.strip()]
    return "\n".join(rows[-lines:])


def pane_prompt_hash(tail: str) -> str:
    """Stable short fingerprint of the visible prompt, for informed consent."""

    return hashlib.sha256(tail.encode("utf-8")).hexdigest()[:8] if tail else ""


def capture_notification_tail(tmux_name: str, *, lines: int = 15) -> str:
    """Pin a single pane and reject ambiguous or changing capture targets.

    This does not replace remote identity validation at action time. Until
    status exports an exact capture handle, multi-pane sessions fail closed.
    """

    def observe() -> str:
        result = subprocess.run(
            ["tmux", "list-panes", "-s", "-t", f"={tmux_name}", "-F",
             "#{session_name}|#{session_id}|#{pane_id}|#{pane_pid}|#{pid}"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=10,
        )
        rows = result.stdout.splitlines()
        if result.returncode != 0 or len(rows) != 1:
            return ""
        fields = rows[0].split("|")
        if (len(fields) != 5 or fields[0] != tmux_name
                or not re.fullmatch(r"\$\d+", fields[1])
                or not re.fullmatch(r"%\d+", fields[2])
                or not all(value.isdigit() for value in fields[3:])):
            return ""
        return rows[0]

    try:
        before = observe()
        if not before:
            return ""
        capture = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", before.split("|")[2]],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=10,
        )
        if capture.returncode != 0 or observe() != before:
            return ""
    except (OSError, subprocess.SubprocessError):
        return ""
    rows = [row.rstrip() for row in capture.stdout.splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


PROMPT_OPTION_RE = re.compile(r"^\s*(?:[❯›>]\s*)?(\d)[.)]\s+(\S.{0,50})")

SESSION_INTERACTION_SCHEMA = "telegram_session_interaction.v1"
CODEX_STARTUP_TRUST_MARKERS = (
    "do you trust the contents of this directory",
    "trusting the directory allows project-local config",
)
PERMISSION_PROMPT_MARKERS = (
    "do you trust",
    "trust the contents",
    "trust the files",
    "allow codex",
    "allow claude",
    "allow this command",
    "permission required",
    "yes, allow",
    "approve this",
    "run this command?",
    "execute this action?",
    "(y/n)",
    "[y/n]",
)


def parse_prompt_options(tail: str) -> list[dict[str, str]]:
    """Extract numbered choices (e.g. '1. Yes  2. No') from a prompt pane."""

    options: dict[str, str] = {}
    separated = False
    for line in tail.splitlines():
        match = PROMPT_OPTION_RE.match(line)
        if match:
            if separated or match.group(1) in options:
                options = {}
            options[match.group(1)] = match.group(2).strip()
            separated = False
        elif line.strip() and options:
            # Keep only the last contiguous choice block, not an old menu's keys.
            separated = True
    if len(options) < 2:
        return []
    return [{"key": key, "label": options[key]} for key in sorted(options)][:4]


def current_interaction_tail(tail: str) -> str:
    """Discard history before an explicit newer cursor or menu question.

    This is a conservative text boundary, not proof of a live dialog. Wrapped
    choices without an unambiguous block remain outside the action contract.
    """
    lines = [line for line in str(tail or "").splitlines() if line.strip()][-15:]
    option_rows = [i for i, line in enumerate(lines) if PROMPT_OPTION_RE.match(line)]
    cursor_rows = [
        i for i, line in enumerate(lines)
        if not PROMPT_OPTION_RE.match(line)
        and (line.strip() in {">", "codex>"} or line.strip().startswith(("›", "❯")))
    ]
    if cursor_rows and (not option_rows or cursor_rows[-1] > option_rows[-1]):
        lines = lines[cursor_rows[-1]:]
    elif option_rows:
        start = option_rows[-1]
        while start > 0 and PROMPT_OPTION_RE.match(lines[start - 1]):
            # A repeated option 1 begins a new menu even without a heading.
            if PROMPT_OPTION_RE.match(lines[start]).group(1) == "1":
                break
            start -= 1
        questions = [i for i in range(start) if lines[i].rstrip().endswith("?")]
        if questions:
            lines = lines[questions[-1]:]
    return "\n".join(lines)


def classify_session_interaction(tail: str, tool: str) -> dict[str, Any]:
    """Classify one terminal interaction without granting execution authority.

    A local model may summarize the pane later, but only these deterministic
    markers decide which actions a Telegram card may expose. Permission and
    selection prompts always take precedence over ordinary task cursors.
    """

    tail = current_interaction_tail(tail)
    clean_lines = [line.strip() for line in str(tail or "").splitlines() if line.strip()]
    recent_lines = clean_lines[-15:]
    recent = "\n".join(recent_lines).lower()
    options = parse_prompt_options(tail)
    normalized_tool = str(tool or "").strip().lower()

    startup_trust_marker = next(
        (marker for marker in CODEX_STARTUP_TRUST_MARKERS if marker in recent),
        "",
    )
    update_option_labels = {
        str(option.get("label") or "").strip().lower() for option in options
    }
    codex_update_prompt = (
        normalized_tool == "codex"
        and "update available" in recent
        and any(label.startswith("update now") for label in update_option_labels)
        and any(label == "skip" or label.startswith("skip until") for label in update_option_labels)
    )

    permission_marker = next(
        (marker for marker in PERMISSION_PROMPT_MARKERS if marker in recent),
        "",
    )
    passive_wait = normalized_tool == "codex" and (
        any("dismiss and keep waiting" in label for label in update_option_labels)
        if options else (
            "dismiss and keep waiting" in recent
            or ("no action is required" in recent and "codex will keep waiting" in recent)
        )
    )
    menu_marker = any(
        re.match(r"^\s*[❯›>]\s*\d[.)]\s+", line)
        for line in str(tail or "").splitlines()
    ) or any(hint in recent for hint in ("enter to select", "use arrow keys", "↑/↓"))
    if passive_wait:
        kind = "passive_wait"
        reason = "codex_no_action_required"
        options = []
    elif normalized_tool == "codex" and startup_trust_marker:
        kind = "startup_trust"
        reason = f"codex_startup_trust:{startup_trust_marker}"
    elif codex_update_prompt:
        kind = "maintenance_update"
        reason = "codex_self_update_selection"
    elif permission_marker:
        kind = "permission"
        reason = f"permission_marker:{permission_marker}"
    elif options and menu_marker:
        kind = "selection"
        reason = "numbered_selection"
    else:
        options = []
        task_cursor = ""
        for line in reversed(recent_lines[-10:]):
            if line in {">", "codex>"}:
                task_cursor = line
                break
            cursor = line[:1]
            if cursor not in {"›", "❯"}:
                continue
            rest = line[1:].strip()
            if rest and re.match(r"^[1-4][.)]\s", rest):
                continue
            task_cursor = cursor
            break
        if task_cursor and normalized_tool in {"codex", "claude", "opencode"}:
            kind = "task_ready"
            reason = f"ordinary_task_cursor:{normalized_tool}"
        elif any(line.rstrip().endswith("?") for line in recent_lines[-8:]):
            kind = "question"
            reason = "question_at_pane_tail"
        else:
            kind = "unknown"
            reason = "no_authoritative_interaction_marker"

    return {
        "schema": SESSION_INTERACTION_SCHEMA,
        "kind": kind,
        "reason": reason,
        "options": options,
        "continuation_allowed": kind == "task_ready",
    }


SESSION_PROMPT_SEMANTICS_SCHEMA = "telegram_session_prompt_semantics.v1"
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PROMPT_FOOTER_PREFIXES = (
    "press enter",
    "enter to select",
    "enter to confirm",
    "esc to cancel",
    "tab/arrow",
    "arrow keys",
    "ctrl+c",
    "esc to interrupt",
)


def _semantic_prompt_line(raw_line: str) -> str:
    """Normalize one pane row while retaining action-relevant wording."""

    value = _ANSI_ESCAPE_RE.sub("", str(raw_line or "")).replace("\u00a0", " ")
    value = re.sub(r"^[\s│┃╎┊╭╰╮╯─━┄┅┈┉]+", "", value)
    value = re.sub(r"^[❯›>⏺•●○◉✦✢▣△]+\s*", "", value).strip()
    if not value:
        return ""
    lowered = value.lower()
    if re.fullmatch(r"elapsed\s*:?\s*\d+(?:\.\d+)?\s*(?:s|sec|seconds|m|min|minutes)", lowered):
        return ""
    if lowered.startswith(_PROMPT_FOOTER_PREFIXES):
        return ""
    if "esc to interrupt" in lowered:
        return ""
    if re.fullmatch(
        r"(?:working|thinking|processing|running)(?:\s*[.…]+)?(?:\s*\([^)]*\))?",
        lowered,
    ):
        return ""
    if re.fullmatch(
        r"(?:gpt|claude|sonnet|opus|haiku|gemini|qwen|minimax)[a-z0-9._:+-]*",
        lowered,
    ):
        return ""
    if lowered.startswith("build ·"):
        return ""
    # Navigation hints can share a row with an unnumbered option list. Keep
    # the labels while removing only the footer suffix.
    value = re.split(r"\s+[⇆↵]\s*(?:select|enter)\b", value, maxsplit=1)[0]
    # Some TUIs append token/context/cost telemetry to an otherwise semantic
    # row. Strip only that characteristic trailing cluster; numbers inside a
    # command or question remain exact action material.
    value = re.sub(
        r"(?:^|\s{2,})\d[\d,]*\s+\d+(?:\.\d+)?%\s+\(\$\d+(?:\.\d+)?\)\s*$",
        "",
        value,
    )
    value = re.sub(r"\s+", " ", value).strip()
    return value if re.search(r"[0-9A-Za-z가-힣]", value) else ""


def session_prompt_semantics(
    tail: str,
    tool: str,
    *,
    interaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the stable, action-relevant view of one waiting prompt.

    Raw pane bytes remain a separate observation. This projection excludes
    cursor chrome, navigation hints, clocks, costs, progress percentages, and
    durations while retaining the prompt wording and exact ordered options.
    """

    classified = (
        interaction
        if isinstance(interaction, dict)
        else classify_session_interaction(tail, tool)
    )
    semantic_lines: list[str] = []
    for raw_line in str(tail or "").splitlines():
        if PROMPT_OPTION_RE.match(raw_line):
            continue
        line = _semantic_prompt_line(raw_line)
        if line:
            semantic_lines.append(line)
    prompt_text = "\n".join(semantic_lines[-8:])[-1200:]
    options = [
        {
            "key": str(option.get("key") or "").strip(),
            "label": re.sub(
                r"\s+", " ", str(option.get("label") or "").strip()
            ),
        }
        for option in classified.get("options") or []
        if isinstance(option, dict)
        and str(option.get("key") or "").strip()
        and str(option.get("label") or "").strip()
    ]
    return {
        "schema": SESSION_PROMPT_SEMANTICS_SCHEMA,
        "tool": str(tool or "").strip().lower(),
        "interaction_kind": str(classified.get("kind") or "unknown"),
        "prompt_text": prompt_text,
        "options": options,
    }


def session_prompt_identity(
    tail: str,
    tool: str,
    *,
    interaction: dict[str, Any] | None = None,
) -> str:
    """Return a 64-bit identity for the semantic waiting prompt."""

    semantics = session_prompt_semantics(tail, tool, interaction=interaction)
    return hashlib.sha256(
        json.dumps(semantics, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# How long to wait for the pane to react to a keystroke before calling it a
# no-op. Agent TUIs repaint the moment a prompt is dismissed, so a pane still
# byte-identical after this window never consumed the key.
SESSION_INPUT_SETTLE_SEC = 2.5
SESSION_INPUT_POLL_SEC = 0.25
SESSION_TEXT_MAX_CHARS = 2000


def pane_settled_after_input(tmux_name: str, before_hash: str) -> tuple[bool, str]:
    """Poll the pane until it differs from ``before_hash`` or the wait expires.

    Returns ``(changed, latest_hash)``.
    """

    deadline = time.monotonic() + SESSION_INPUT_SETTLE_SEC
    current = before_hash
    while time.monotonic() < deadline:
        time.sleep(SESSION_INPUT_POLL_SEC)
        current = pane_prompt_hash(capture_session_tail(tmux_name, lines=15))
        if current != before_hash:
            return True, current
    return False, current


def apply_session_input(
    forager_bin: str,
    profile: str,
    session_id: str,
    action: str,
    *,
    status_file: Any = None,
    expected_hash: str = "",
    expected_prompt_identity: str = "",
    expected_interaction_kind: str = "",
    option_key: str = "",
    expected_identity: dict[str, Any] | None = None,
    require_remote_identity: bool = False,
) -> dict[str, Any]:
    """Send one approve/deny keystroke to a waiting supervised session.

    Re-verifies at confirm time that the session still waits for input,
    resolves its tmux session by the id suffix in the session name, then
    sends a numeric shortcut, Enter, or Escape. Claude arrow-navigation menus
    use the observed cursor to select the exact requested row before Enter.
    It never types free-form text into the agent.

    Success means the pane actually reacted. ``tmux send-keys`` exits 0 once
    the key is queued, which says nothing about the agent consuming it, so a
    pane that never repaints reports ``no_effect`` rather than a success the
    operator would keep retrying.
    """

    result: dict[str, Any] = {"ok": False, "kind": "session_input", "action": action}
    if status_file is not None:
        try:
            status = json.loads(pathlib.Path(status_file).read_text())
        except (OSError, json.JSONDecodeError) as error:
            result["error"] = f"status_unavailable:{error}"
            return result
    else:
        status = run_forager_json(forager_bin, profile, ["status", "--json"], label="session status")
    rows = [row for row in status.get("sessions") or [] if isinstance(row, dict)]
    matches = [row for row in rows if str(row.get("id") or "").startswith(session_id)]
    if len(matches) != 1:
        result["error"] = "session_not_found" if not matches else "session_id_ambiguous"
        return result
    session = matches[0]
    result["session_title"] = str(session.get("title") or "")
    result["project"] = str(session.get("project") or "")
    if require_remote_identity and not isinstance(expected_identity, dict):
        result["error"] = "session_identity_missing"
        return result
    if isinstance(expected_identity, dict):
        result["remote_session_identity_sha256"] = expected_identity.get("binding_sha256")
        if str(session.get("id") or "") != str(expected_identity.get("session_id") or ""):
            result["error"] = "session_identity_changed:target_session_id_mismatch"
            return result
        identity_error = pending_action_identity_issue(expected_identity, status)
        if identity_error:
            result["error"] = f"session_identity_changed:{identity_error}"
            return result
    if str(session.get("status") or "") != "waiting":
        result["error"] = f"session_not_waiting:{session.get('status')}"
        return result
    tmux_name = find_tmux_session_name(str(session.get("id") or ""))
    if tmux_name is None:
        result["error"] = "tmux_session_not_found"
        return result
    before_tail = capture_session_tail(tmux_name, lines=15)
    before_hash = pane_prompt_hash(before_tail)
    current_interaction = classify_session_interaction(
        before_tail, str(session.get("tool") or "")
    )
    current_prompt_identity = session_prompt_identity(
        before_tail,
        str(session.get("tool") or ""),
        interaction=current_interaction,
    )
    result["prompt_observation_hash"] = before_hash
    result["prompt_identity"] = current_prompt_identity
    result["interaction_kind"] = current_interaction.get("kind")
    if expected_prompt_identity and (
        current_prompt_identity != expected_prompt_identity
        or (
            expected_interaction_kind
            and current_interaction.get("kind") != expected_interaction_kind
        )
    ):
        result["error"] = "prompt_changed"
        result["new_hash"] = current_prompt_identity
        result["new_prompt_identity"] = current_prompt_identity
        result["new_observation_hash"] = before_hash
        result["prompt_line"] = sanitize_text(
            before_tail.splitlines()[-1] if before_tail else "", max_chars=100
        )
        return result
    if expected_hash and before_hash != expected_hash:
        # Informed consent: the keystroke applies to the prompt the operator
        # saw on the card. If the pane moved on, refuse and report the new
        # state instead of blind-approving whatever is there now.
        result["error"] = "prompt_changed"
        result["new_hash"] = before_hash
        result["prompt_line"] = sanitize_text(
            before_tail.splitlines()[-1] if before_tail else "", max_chars=100
        )
        return result
    keys = []
    if action == "approve" and option_key:
        key = option_key
        if (str(session.get("tool") or "").lower() == "claude"
                and "enter to select" in before_tail.lower()
                and "↑/↓" in before_tail):
            options = current_interaction.get("options") or []
            option_keys = [item["key"] for item in options]
            cursors = [match.group(1) for line in before_tail.splitlines()
                       if (match := re.match(r"^\s*[❯›>]\s*(\d)[.)]\s+", line))]
            if (len(cursors) != 1 or cursors[0] not in option_keys
                    or option_key not in option_keys):
                result["error"] = "selection_cursor_unavailable"
                return result
            distance = option_keys.index(option_key) - option_keys.index(cursors[0])
            keys = ["Down" if distance > 0 else "Up"] * abs(distance) + ["Enter"]
    else:
        key = "Enter" if action == "approve" else "Escape"
    result["key"] = key
    keys = keys or [key]
    result["keys"] = keys
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_name, *keys],
            check=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        result["error"] = f"tmux_failed:{error}"
        return result
    changed, after_hash = pane_settled_after_input(tmux_name, before_hash)
    result["pane_hash"] = after_hash
    if not changed:
        result["error"] = "no_effect"
        return result
    result["ok"] = True
    return result


def apply_session_text_input(
    forager_bin: str,
    profile: str,
    session_id: str,
    text: str,
    *,
    status_file: Any = None,
    expected_hash: str = "",
    expected_prompt_identity: str = "",
    expected_interaction_kind: str = "",
    require_exact_id: bool = False,
    expected_identity: dict[str, Any] | None = None,
    require_remote_identity: bool = False,
) -> dict[str, Any]:
    """Deliver one literal operator message to a live supervised agent pane.

    The target must resolve to exactly one current session in running, idle, or
    waiting state. An expected pane hash binds a reply to the prompt the
    operator saw. The message and Enter are sent as one tmux command sequence,
    without a shell, then the pane must repaint before delivery is reported.
    """

    message = str(text or "").replace("\x00", " ").strip()
    result: dict[str, Any] = {
        "ok": False,
        "kind": "session_message",
        "action": "send_text",
        "session_id": str(session_id or ""),
    }
    if not str(session_id or "").strip():
        result["error"] = "session_id_missing"
        return result
    if not message:
        result["error"] = "empty_message"
        return result
    if len(message) > SESSION_TEXT_MAX_CHARS:
        result["error"] = "message_too_long"
        result["max_chars"] = SESSION_TEXT_MAX_CHARS
        return result
    if status_file is not None:
        try:
            status = json.loads(pathlib.Path(status_file).read_text())
        except (OSError, json.JSONDecodeError) as error:
            result["error"] = f"status_unavailable:{error}"
            return result
    else:
        status = run_forager_json(
            forager_bin, profile, ["status", "--json"], label="session status"
        )
    rows = [row for row in status.get("sessions") or [] if isinstance(row, dict)]
    if require_exact_id:
        matches = [row for row in rows if str(row.get("id") or "") == session_id]
        if not matches and any(
            str(row.get("id") or "").startswith(session_id) for row in rows
        ):
            result["error"] = "session_id_not_exact"
            return result
    else:
        matches = [row for row in rows if str(row.get("id") or "").startswith(session_id)]
    if len(matches) != 1:
        result["error"] = "session_not_found" if not matches else "session_id_ambiguous"
        return result
    session = matches[0]
    result["session_id"] = str(session.get("id") or "")
    result["session_title"] = str(session.get("title") or "")
    result["project"] = str(session.get("project") or "")
    result["tool"] = str(session.get("tool") or "")
    if require_remote_identity and not isinstance(expected_identity, dict):
        result["error"] = "session_identity_missing"
        return result
    if isinstance(expected_identity, dict):
        result["remote_session_identity_sha256"] = expected_identity.get("binding_sha256")
        if str(session.get("id") or "") != str(expected_identity.get("session_id") or ""):
            result["error"] = "session_identity_changed:target_session_id_mismatch"
            return result
        identity_error = pending_action_identity_issue(expected_identity, status)
        if identity_error:
            result["error"] = f"session_identity_changed:{identity_error}"
            return result
    session_status = str(session.get("status") or "")
    result["session_status"] = session_status
    if session_status not in {"running", "idle", "waiting"}:
        result["error"] = f"session_not_live:{session_status}"
        return result
    tmux_name = find_tmux_session_name(str(session.get("id") or ""))
    if tmux_name is None:
        result["error"] = "tmux_session_not_found"
        return result
    before_tail = capture_session_tail(tmux_name, lines=15)
    before_hash = pane_prompt_hash(before_tail)
    current_interaction = classify_session_interaction(
        before_tail, str(session.get("tool") or "")
    )
    current_prompt_identity = session_prompt_identity(
        before_tail,
        str(session.get("tool") or ""),
        interaction=current_interaction,
    )
    result["prompt_observation_hash"] = before_hash
    result["prompt_identity"] = current_prompt_identity
    result["interaction_kind"] = current_interaction.get("kind")
    if expected_prompt_identity and (
        current_prompt_identity != expected_prompt_identity
        or (
            expected_interaction_kind
            and current_interaction.get("kind") != expected_interaction_kind
        )
    ):
        result["error"] = "prompt_changed"
        result["new_hash"] = current_prompt_identity
        result["new_prompt_identity"] = current_prompt_identity
        result["new_observation_hash"] = before_hash
        result["prompt_line"] = sanitize_text(
            before_tail.splitlines()[-1] if before_tail else "", max_chars=100
        )
        return result
    if expected_hash and before_hash != expected_hash:
        result["error"] = "prompt_changed"
        result["new_hash"] = before_hash
        result["prompt_line"] = sanitize_text(
            before_tail.splitlines()[-1] if before_tail else "", max_chars=100
        )
        return result
    result["message_hash"] = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    result["message_chars"] = len(message)
    buffer_name = f"forager-telegram-{uuid.uuid4().hex}"
    try:
        subprocess.run(
            [
                "tmux",
                "load-buffer",
                "-b",
                buffer_name,
                "-",
                ";",
                "paste-buffer",
                "-d",
                "-p",
                "-b",
                buffer_name,
                "-t",
                tmux_name,
                ";",
                "send-keys",
                "-t",
                tmux_name,
                "Enter",
            ],
            check=True,
            timeout=10,
            input=message,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        try:
            subprocess.run(
                ["tmux", "delete-buffer", "-b", buffer_name],
                check=False,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        result["error"] = f"tmux_failed:{error}"
        return result
    changed, after_hash = pane_settled_after_input(tmux_name, before_hash)
    result["pane_hash"] = after_hash
    result["pane_reacted"] = changed
    result["delivery_status"] = "pane_reacted" if changed else "input_queued"
    # tmux accepting the literal paste and Enter is the strongest generic
    # delivery proof available across Codex and Claude TUIs. A repaint is useful
    # evidence, but it is not an application-level acknowledgement and a quiet
    # pane does not prove that the input was dropped.
    result["ok"] = True
    return result


def cancellable_tasks_from_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """List tasks that can still be cancelled, drawn from project task rows."""

    tasks: list[dict[str, Any]] = []
    projects = surface.get("projects")
    if not isinstance(projects, list):
        return tasks
    for project in projects:
        if not isinstance(project, dict):
            continue
        items = project.get("task_items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").strip().lower()
            if status in CANCELLABLE_TASK_STATUSES_EXCLUDED:
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            tasks.append(
                {
                    "task_id": task_id,
                    "status": status,
                    "title": sanitize_text(str(item.get("title") or task_id), max_chars=80),
                    "project_key": str(
                        item.get("project_key") or project.get("project_key") or ""
                    ),
                }
            )
    return tasks


def inspectable_tasks_from_surface(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain completed review targets without granting them cancellation."""
    tasks = []
    projects = surface.get("projects")
    if not isinstance(projects, list):
        raise RemoteOperatorTelegramError("workstation project list is unreadable")
    for project in projects:
        if not isinstance(project, dict):
            continue
        items = project.get("task_items")
        if not isinstance(items, list):
            raise RemoteOperatorTelegramError("workstation task list is unreadable")
        for item in items:
            if not isinstance(item, dict) or not item.get("task_id"):
                continue
            cancellable = str(item.get("status") or "").lower() not in CANCELLABLE_TASK_STATUSES_EXCLUDED
            if cancellable or item.get("requires_operator_review"):
                tasks.append({**item, "project_key": str(project.get("project_key") or ""),
                              "cancellable": cancellable})
    for task in tasks:
        task["cancellation_hint_safe"] = sum(row["task_id"] == task["task_id"] for row in tasks) == 1
    return tasks


def apply_cancel_task(
    forager_bin: str,
    profile: str,
    task_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Mark a durable offdesk task cancelled.

    This is the fail-safe direction (it stops the harness from continuing or
    resuming the task) but it does not kill an already-running background
    process; the caller surfaces that nuance to the operator.
    """

    result: dict[str, Any] = {
        "schema": DISPATCH_RESULT_SCHEMA,
        "ok": False,
        "kind": "cancel_task",
        "task_id": str(task_id or ""),
    }
    argv = ["offdesk", "cancel-task", str(task_id)]
    cleaned = sanitize_text(reason, max_chars=200)
    if cleaned:
        argv.extend(["--reason", cleaned])
    argv.append("--json")
    try:
        report = run_forager_json(forager_bin, profile, argv, label="cancel task")
    except RemoteOperatorTelegramError as error:
        result["stage"] = "cancel_failed"
        result["error"] = sanitize_text(str(error), max_chars=240)
        return result
    result["ok"] = True
    result["stage"] = "cancelled" if report.get("changed") else "unchanged"
    result["changed"] = bool(report.get("changed"))
    result["previous_status"] = report.get("previous_status")
    result["status"] = report.get("status")
    result["message"] = sanitize_text(str(report.get("message") or ""), max_chars=160)
    return result


def apply_terminate_task(
    forager_bin: str,
    profile: str,
    task_id: str,
    *,
    reason: str,
    terminated_by: str = "telegram",
) -> dict[str, Any]:
    """Stop the verified local runtime and cancel its durable task."""

    result: dict[str, Any] = {
        "schema": DISPATCH_RESULT_SCHEMA,
        "ok": False,
        "kind": "terminate_task",
        "task_id": str(task_id or ""),
    }
    actor = sanitize_text(terminated_by, max_chars=120) or "telegram"
    argv = ["offdesk", "terminate-task", str(task_id), "--by", actor]
    cleaned = sanitize_text(reason, max_chars=200)
    if cleaned:
        argv.extend(["--reason", cleaned])
    argv.append("--json")
    try:
        report = run_forager_json(forager_bin, profile, argv, label="terminate task")
    except RemoteOperatorTelegramError as error:
        result["stage"] = "termination_refused"
        result["error"] = sanitize_text(str(error), max_chars=240)
        return result

    task = report.get("task") if isinstance(report.get("task"), dict) else {}
    background = (
        report.get("background") if isinstance(report.get("background"), dict) else {}
    )
    result.update(
        {
            "ok": True,
            "stage": "terminated" if background.get("changed") else "already_stopped",
            "changed": bool(report.get("changed")),
            "runtime_terminated": bool(background.get("changed")),
            "runtime_was_alive": bool(background.get("runtime_was_alive")),
            "runtime_alive": bool(background.get("runtime_alive")),
            "status": task.get("status"),
            "previous_status": task.get("previous_status"),
            "ticket_id": background.get("ticket_id"),
            "runner_kind": background.get("runner_kind"),
            "evidence": sanitize_text(str(background.get("evidence") or ""), max_chars=160),
        }
    )
    return result


def apply_operator_pause(
    forager_bin: str,
    profile: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Engage the global operator pause: halt all new offdesk dispatch."""

    result: dict[str, Any] = {"schema": DISPATCH_RESULT_SCHEMA, "ok": False, "kind": "pause"}
    argv = ["offdesk", "pause", "--by", "telegram"]
    cleaned = sanitize_text(reason, max_chars=200)
    if cleaned:
        argv.extend(["--reason", cleaned])
    argv.append("--json")
    try:
        state = run_forager_json(forager_bin, profile, argv, label="operator pause")
    except RemoteOperatorTelegramError as error:
        result["error"] = sanitize_text(str(error), max_chars=240)
        return result
    result["ok"] = True
    result["paused"] = bool(state.get("paused"))
    result["reason"] = sanitize_text(str(state.get("reason") or ""), max_chars=160)
    return result


def apply_operator_resume(forager_bin: str, profile: str) -> dict[str, Any]:
    """Clear the global operator pause so new dispatch can proceed again."""

    result: dict[str, Any] = {"schema": DISPATCH_RESULT_SCHEMA, "ok": False, "kind": "resume"}
    try:
        state = run_forager_json(
            forager_bin,
            profile,
            ["offdesk", "unpause", "--by", "telegram", "--json"],
            label="operator resume",
        )
    except RemoteOperatorTelegramError as error:
        result["error"] = sanitize_text(str(error), max_chars=240)
        return result
    result["ok"] = True
    result["paused"] = bool(state.get("paused"))
    return result


def runtime_dispatch_items(surface: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = surface.get("runtime_dispatch")
    if not isinstance(runtime, dict):
        return []
    items = runtime.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def open_runtime_dispatch(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize post-closeout handoffs ready for runtime dispatch."""

    rows: list[dict[str, Any]] = []
    for item in runtime_dispatch_items(surface):
        latest = item.get("latest_receipt")
        already_queued = (
            str(latest.get("result_status") or "") == "queued"
            if isinstance(latest, dict)
            else False
        )
        rows.append(
            {
                "closeout_id": str(item.get("closeout_id") or ""),
                "decision_id": str(item.get("decision_id") or ""),
                "title": sanitize_text(str(item.get("title") or ""), max_chars=120),
                "stage": str(item.get("stage") or ""),
                "already_queued": already_queued,
            }
        )
    return rows


def runtime_dispatch_item(surface: dict[str, Any], closeout_id: str) -> dict[str, Any] | None:
    wanted = str(closeout_id or "").strip()
    for item in runtime_dispatch_items(surface):
        if str(item.get("closeout_id") or "").strip() == wanted:
            return item
    return None


def apply_runtime_dispatch(
    forager_bin: str,
    profile: str,
    *,
    closeout_id: str,
    runner: str,
    command: str,
) -> dict[str, Any]:
    """Preflight a receipted closeout and queue a runtime task.

    runtime-preflight re-verifies the closeout against the latest canonical
    decision receipt at dispatch time, so a changed decision is rejected here
    rather than queued. The queued command runs later only through
    `forager offdesk tick` and the scheduler gate; this call does not launch a
    process.
    """

    result: dict[str, Any] = {
        "schema": DISPATCH_RESULT_SCHEMA,
        "ok": False,
        "stage": "runtime_preflight",
        "kind": "runtime",
        "closeout_id": str(closeout_id or ""),
        "runner": str(runner or ""),
        "command_preview": sanitize_text(command, max_chars=120),
    }
    preflight = run_forager_json(
        forager_bin,
        profile,
        ["ondesk", "runtime-preflight", "--closeout-id", str(closeout_id), "--json"],
        label="runtime dispatch preflight",
    )
    pf = preflight.get("preflight") if isinstance(preflight.get("preflight"), dict) else {}
    result["preflight_id"] = pf.get("preflight_id")
    if str(pf.get("result_status") or "") != "ready_for_runtime_dispatch":
        result["stage"] = "runtime_preflight_rejected"
        result["error"] = sanitize_text(
            str(pf.get("reason") or "runtime preflight not ready"), max_chars=240
        )
        return result

    dispatch = run_forager_json(
        forager_bin,
        profile,
        [
            "ondesk",
            "runtime-dispatch",
            "--preflight-id",
            str(pf["preflight_id"]),
            "--runner",
            str(runner),
            "--cmd",
            str(command),
            "--json",
        ],
        label="runtime dispatch",
    )
    receipt = dispatch.get("receipt") if isinstance(dispatch.get("receipt"), dict) else {}
    result["receipt_id"] = receipt.get("receipt_id")
    result["task_id"] = receipt.get("task_id")
    dispatch_status = str(receipt.get("result_status") or "")
    result["dispatch_status"] = dispatch_status
    result["task_enqueued"] = bool(dispatch.get("task_enqueued"))
    if dispatch_status == "queued":
        result["ok"] = True
        result["stage"] = "queued"
    else:
        result["stage"] = "runtime_dispatch_not_queued"
        result["error"] = sanitize_text(
            str(receipt.get("reason") or f"runtime dispatch {dispatch_status}"), max_chars=240
        )
    return result


def apply_recovery_action(
    forager_bin: str,
    profile: str,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Validate an accepted-truth recovery envelope and record its receipt.

    This intentionally stops at validation: recording accepted truth or
    running the fallback command remains a separate explicit local step that
    this surface does not perform.
    """

    result: dict[str, Any] = {
        "schema": DISPATCH_RESULT_SCHEMA,
        "ok": False,
        "stage": "recovery_envelope",
        "kind": "recovery",
        "closeout_id": str((envelope.get("target_ref") or {}).get("closeout_id") or ""),
        "action_kind": str(envelope.get("action_kind") or ""),
    }
    envelope_path = _write_temp_envelope(envelope)
    try:
        validated = run_forager_json(
            forager_bin,
            profile,
            [
                "ondesk",
                "accepted-truth-recovery-envelope",
                "--envelope",
                str(envelope_path),
                "--json",
            ],
            label="recovery envelope validation",
        )
        receipt = validated.get("receipt") if isinstance(validated.get("receipt"), dict) else {}
        receipt_status = str(receipt.get("result_status") or "")
        result["receipt_id"] = receipt.get("receipt_id")
        result["receipt_status"] = receipt_status
        if receipt.get("stale") or receipt_status != "validated_preview":
            result["stage"] = "recovery_envelope_rejected"
            result["error"] = sanitize_text(
                str(receipt.get("reason") or "recovery envelope rejected"), max_chars=240
            )
            return result
        result["ok"] = True
        result["stage"] = "recovery_validated"
        result["local_execution_required"] = True
        result["next_local_command"] = sanitize_text(
            str(receipt.get("allowed_command") or ""), max_chars=2000
        )
        return result
    finally:
        try:
            envelope_path.unlink()
        except OSError:
            pass


def _write_temp_envelope(envelope: dict[str, Any]):
    import os
    import pathlib
    import tempfile

    directory = pathlib.Path(tempfile.gettempdir()) / "forager_telegram_dispatch"
    directory.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="envelope_", suffix=".json", dir=directory)
    with os.fdopen(handle, "w", encoding="utf-8") as writer:
        writer.write(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
    return pathlib.Path(name)
