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

import json
import subprocess
import uuid
from typing import Any

from .common import RemoteOperatorTelegramError, utc_now
from .persistence import parse_utc_timestamp
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
    except OSError as error:
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


def export_workstation_surface(forager_bin: str, profile: str) -> dict[str, Any]:
    surface = run_forager_json(
        forager_bin,
        profile,
        ["ondesk", "workstation-surface", "--json"],
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
        "created_at": now,
        "ttl_sec": expiry_seconds,
    }


def store_confirmation(state: dict[str, Any], chat_hash: str | None, confirmation: dict[str, Any]) -> None:
    pending = state.setdefault("pending_dispatch_confirmations_by_chat", {})
    if not isinstance(pending, dict):
        pending = {}
        state["pending_dispatch_confirmations_by_chat"] = pending
    # Only one pending confirmation per chat: a new request supersedes the old.
    pending[str(chat_hash or "")] = confirmation


def confirmation_is_fresh(confirmation: dict[str, Any]) -> bool:
    created = parse_utc_timestamp(confirmation.get("created_at"))
    if created is None:
        return False
    ttl = int(confirmation.get("ttl_sec") or DEFAULT_CONFIRMATION_TTL_SEC)
    age = (parse_utc_timestamp(utc_now()) - created).total_seconds()
    return age <= ttl


def clear_confirmation(state: dict[str, Any], chat_hash: str | None) -> bool:
    pending = state.get("pending_dispatch_confirmations_by_chat")
    if not isinstance(pending, dict):
        return False
    return pending.pop(str(chat_hash or ""), None) is not None


def pop_confirmation(
    state: dict[str, Any],
    chat_hash: str | None,
    token: str,
) -> dict[str, Any] | None:
    pending = state.get("pending_dispatch_confirmations_by_chat")
    if not isinstance(pending, dict):
        return None
    key = str(chat_hash or "")
    confirmation = pending.get(key)
    if not isinstance(confirmation, dict):
        return None
    if str(confirmation.get("token") or "") != str(token or "").strip():
        return None
    # A matched token is single-use whether or not the chain later succeeds.
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
