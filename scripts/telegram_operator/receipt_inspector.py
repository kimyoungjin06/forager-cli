"""Read-only inspection across Telegram command and Plan receipt evidence."""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from typing import Any

from .common import sha256_short, utc_now
from .persistence import RemoteOperatorStateError, parse_utc_timestamp
from .schemas import (
    PLAN_CLOSEOUT_PACKET_SCHEMA,
    PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
    PLAN_CLOSEOUT_VERDICT_SCHEMA,
    PLAN_DRAFT_SCHEMA,
    PLAN_DRAFT_SELECTION_SCHEMA,
    PLAN_ENQUEUE_HANDOFF_SCHEMA,
    PLAN_ENQUEUE_RUN_SCHEMA,
    PLAN_EXECUTION_BRIEF_SCHEMA,
    PLAN_GATE_REQUEST_SCHEMA,
    PLAN_GATE_RESOLUTION_SCHEMA,
    PLAN_LAUNCH_PREP_SCHEMA,
    PLAN_REGISTRATION_SCHEMA,
    PLAN_REVIEW_SCHEMA,
    PLAN_RUNTIME_MONITOR_SCHEMA,
    PLAN_RUNTIME_START_SCHEMA,
    PLAN_WORKLOAD_BINDING_SCHEMA,
    PROJECT_INIT_PREVIEW_SCHEMA,
    PROJECT_INIT_RUN_SCHEMA,
)
from .update_journal import latest_update_records


RECEIPT_INSPECTION_SCHEMA = "remote_operator_receipt_inspection.v1"
PLAN_SESSION_ID_PATTERN = re.compile(r"^telegram-plan-[a-f0-9]{12}$")
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_COMMAND_EVIDENCE = 100

# Ordered lifecycle stages. Missing files are neutral because project init can
# be skipped for an existing project and a live session is commonly incomplete.
RECEIPT_STAGES = (
    ("project_init_preview", "PROJECT_INIT_PREVIEW.json", PROJECT_INIT_PREVIEW_SCHEMA),
    ("project_init_run", "PROJECT_INIT_RUN.json", PROJECT_INIT_RUN_SCHEMA),
    ("plan_draft", "PLAN_DRAFT_VALIDATION.json", PLAN_DRAFT_SCHEMA),
    (
        "plan_draft_selection",
        "PLAN_DRAFT_SELECTION.json",
        PLAN_DRAFT_SELECTION_SCHEMA,
    ),
    ("plan_registration", "PLAN_REGISTRATION.json", PLAN_REGISTRATION_SCHEMA),
    ("plan_review", "PLAN_REVIEW.json", PLAN_REVIEW_SCHEMA),
    ("plan_launch_prep", "PLAN_LAUNCH_PREP.json", PLAN_LAUNCH_PREP_SCHEMA),
    ("plan_gate_request", "PLAN_GATE_REQUEST.json", PLAN_GATE_REQUEST_SCHEMA),
    ("plan_gate_resolution", "PLAN_GATE_RESOLUTION.json", PLAN_GATE_RESOLUTION_SCHEMA),
    ("plan_execution_brief", "PLAN_EXECUTION_BRIEF.json", PLAN_EXECUTION_BRIEF_SCHEMA),
    ("plan_enqueue_handoff", "PLAN_ENQUEUE_HANDOFF.json", PLAN_ENQUEUE_HANDOFF_SCHEMA),
    ("plan_workload_binding", "PLAN_WORKLOAD_BINDING.json", PLAN_WORKLOAD_BINDING_SCHEMA),
    ("plan_enqueue_run", "PLAN_ENQUEUE_RUN.json", PLAN_ENQUEUE_RUN_SCHEMA),
    ("plan_runtime_start", "PLAN_RUNTIME_START.json", PLAN_RUNTIME_START_SCHEMA),
    ("plan_runtime_monitor", "PLAN_RUNTIME_MONITOR.json", PLAN_RUNTIME_MONITOR_SCHEMA),
    ("plan_closeout_packet", "PLAN_CLOSEOUT_PACKET.json", PLAN_CLOSEOUT_PACKET_SCHEMA),
    (
        "plan_closeout_review_handoff",
        "PLAN_CLOSEOUT_REVIEW_HANDOFF.json",
        PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
    ),
    ("plan_closeout_verdict", "PLAN_CLOSEOUT_VERDICT.json", PLAN_CLOSEOUT_VERDICT_SCHEMA),
)
OPTIONAL_RECEIPT_STAGES = {"plan_draft_selection"}

IDENTIFIER_KEYS = (
    "plan_id",
    "plan_ref",
    "review_id",
    "prep_id",
    "approval_id",
    "request_id",
    "task_id",
    "closeout_id",
    "receipt_id",
)
STATE_KEYS = (
    "status",
    "project_key",
    "decision",
    "verdict",
    "task_status",
    "acceptance_status",
    "evidence_status",
    "verification_status",
    "strategy_key",
    "wiki_promotion_state",
)
TIME_KEYS = ("created_at", "updated_at", "recorded_at", "generated_at")
AUTHORITY_KEYS = (
    "accepted_truth_authorized",
    "approval_authorized",
    "closeout_artifact_write_authorized",
    "closeout_authorized",
    "closeout_packet_authorized",
    "closeout_review_authorized",
    "closeout_review_handoff_authorized",
    "dispatch_authorized",
    "enqueue_authorized",
    "execution_authorized",
    "execution_brief_authorized",
    "file_mutation_authorized",
    "gate_approval_authorized",
    "launch_authorized",
    "launch_preparation_authorized",
    "monitor_authorized",
    "plan_review_authorized",
    "poll_authorized",
    "project_file_mutation_authorized",
    "queue_mutation_authorized",
    "registration_authorized",
    "remote_closeout_review_authorized",
    "runtime_authorized",
    "runtime_start_authorized",
    "tick_authorized",
    "workload_binding_authorized",
)
HASH_KEYS = (
    "current_execution_brief_sha256",
    "current_plan_sha256",
    "current_launch_prep_sha256",
    "current_prepared_task_sha256",
    "current_source_sha256",
    "current_selection_sha256",
    "execution_brief_sha256",
    "comparison_sha256",
    "expected_execution_brief_sha256",
    "expected_launch_prep_sha256",
    "expected_plan_sha256",
    "expected_source_sha256",
    "expected_selection_sha256",
    "launch_prep_sha256",
    "plan_sha256",
    "prepared_task_sha256",
    "source_sha256",
)


def _safe_token(value: Any, *, max_chars: int = 160) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in ("token=", "api_key=", "apikey=", "password=", "secret=")
    ) or re.search(r"\bsk-[a-z0-9]{12,}", lowered):
        return sha256_short(text)
    if len(text) <= max_chars and re.fullmatch(r"[A-Za-z0-9_.:@+-]+", text):
        return text
    return sha256_short(text)


def _receipt_summary(receipt: dict[str, Any]) -> dict[str, Any]:
    identifiers = {
        key: token
        for key in IDENTIFIER_KEYS
        if (token := _safe_token(receipt.get(key))) is not None
    }
    state = {
        key: token
        for key in STATE_KEYS
        if (token := _safe_token(receipt.get(key))) is not None
    }
    timestamps = {
        key: str(receipt[key])
        for key in TIME_KEYS
        if key in receipt and parse_utc_timestamp(receipt.get(key)) is not None
    }
    authority = {
        key: receipt[key]
        for key in AUTHORITY_KEYS
        if key in receipt and isinstance(receipt[key], bool)
    }
    hashes = {
        key: str(receipt[key])
        for key in HASH_KEYS
        if key in receipt
        and isinstance(receipt[key], str)
        and re.fullmatch(r"[a-fA-F0-9]{64}", receipt[key])
    }
    observed_hash = receipt.get("observed_hash")
    if isinstance(observed_hash, str) and re.fullmatch(
        r"sha256:[a-fA-F0-9]{64}", observed_hash
    ):
        hashes["observed_hash"] = observed_hash.lower()
    summary: dict[str, Any] = {
        "schema": receipt.get("schema"),
        "identifiers": identifiers,
        "state": state,
        "authority": authority,
    }
    if timestamps:
        summary["timestamps"] = timestamps
    if hashes:
        summary["hashes"] = hashes
    integrity = {
        key: receipt[key]
        for key in (
            "comparison_hash_matched",
            "observed_hash_matched",
            "selection_hash_matched",
        )
        if isinstance(receipt.get(key), bool)
    }
    if integrity:
        summary["integrity"] = integrity
    blockers = receipt.get("blocking_reasons")
    if isinstance(blockers, list):
        summary["blocking_reasons"] = [
            token
            for item in blockers[:10]
            if (token := _safe_token(item, max_chars=80)) is not None
        ]
    if receipt.get("error"):
        summary["error_present"] = True
        summary["error_hash"] = sha256_short(str(receipt.get("error")))
    return summary


def _inspect_receipt_file(
    session_dir: pathlib.Path,
    *,
    session_id: str,
    stage: str,
    filename: str,
    expected_schema: str,
) -> dict[str, Any]:
    path = session_dir / filename
    base = {
        "stage": stage,
        "filename": filename,
        "expected_schema": expected_schema,
    }
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return {**base, "artifact_status": "not_created"}
    except OSError:
        return {**base, "artifact_status": "invalid", "issues": ["artifact_unreadable"]}
    if path.is_symlink() or not path.is_file():
        return {**base, "artifact_status": "invalid", "issues": ["artifact_not_regular_file"]}
    if stat.st_size > MAX_RECEIPT_BYTES:
        return {**base, "artifact_status": "invalid", "issues": ["artifact_too_large"]}
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return {**base, "artifact_status": "invalid", "issues": ["artifact_invalid_json"]}
    if not isinstance(receipt, dict):
        return {**base, "artifact_status": "invalid", "issues": ["artifact_not_object"]}
    issues: list[str] = []
    if receipt.get("schema") != expected_schema:
        issues.append("artifact_schema_mismatch")
    if str(receipt.get("session_id") or "") != session_id:
        issues.append("artifact_session_mismatch")
    if issues:
        return {
            **base,
            "artifact_status": "invalid",
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "byte_len": len(raw),
            "issues": issues,
        }
    return {
        **base,
        "artifact_status": "available",
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_len": len(raw),
        "receipt": _receipt_summary(receipt),
    }


def _latest_artifact_session_id(root: pathlib.Path) -> str | None:
    try:
        candidates = [
            item
            for item in root.iterdir()
            if item.is_dir()
            and not item.is_symlink()
            and PLAN_SESSION_ID_PATTERN.fullmatch(item.name)
        ]
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RemoteOperatorStateError(
            f"remote receipt directory is unreadable: {type(error).__name__}"
        ) from error
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda item: item.stat().st_mtime_ns).name
    except OSError as error:
        raise RemoteOperatorStateError(
            f"remote receipt session metadata is unreadable: {type(error).__name__}"
        ) from error


def _command_evidence(
    records: dict[int, dict[str, Any]],
    *,
    session_id: str | None,
    update_id: int | None,
) -> tuple[list[dict[str, Any]], bool, int, int]:
    evidence: list[dict[str, Any]] = []
    for current_update_id in sorted(records):
        record = records[current_update_id]
        effect = record.get("effect") if isinstance(record.get("effect"), dict) else {}
        matches = (
            current_update_id == update_id
            if session_id is None and update_id is not None
            else str(effect.get("remote_plan_session_id") or "") == session_id
        )
        if not matches:
            continue
        envelope = (
            record.get("remote_command_envelope")
            if isinstance(record.get("remote_command_envelope"), dict)
            else {}
        )
        envelope_summary = {
            key: token
            for key in (
                "schema",
                "envelope_id",
                "command",
                "command_class",
                "nonce",
                "validation_status",
            )
            if (token := _safe_token(envelope.get(key))) is not None
        }
        for key in ("message_id", "state_changing", "fresh", "valid"):
            value = envelope.get(key)
            if isinstance(value, (bool, int)):
                envelope_summary[key] = value
        for key in ("issued_at", "received_at", "expires_at"):
            value = envelope.get(key)
            if parse_utc_timestamp(value) is not None:
                envelope_summary[key] = str(value)
        validation_issues = envelope.get("validation_issues")
        if isinstance(validation_issues, list):
            envelope_summary["validation_issues"] = [
                token
                for item in validation_issues[:10]
                if (token := _safe_token(item, max_chars=80)) is not None
            ]
        effect_summary = {
            key: token
            for key in (
                "schema",
                "kind",
                "status",
                "authority_domain",
                "stage",
                "receipt_schema",
                "receipt_status",
                "remote_plan_session_id",
            )
            if (token := _safe_token(effect.get(key))) is not None
        }
        if isinstance(effect.get("authorized"), bool):
            effect_summary["authorized"] = effect["authorized"]
        evidence.append(
            {
                "update_id": current_update_id,
                "journal_status": _safe_token(record.get("status")),
                "command_envelope": envelope_summary,
                "effect": effect_summary,
            }
        )
    truncated = len(evidence) > MAX_COMMAND_EVIDENCE
    matching_count = len(evidence)
    refused_count = sum(
        1
        for item in evidence
        if item.get("command_envelope", {}).get("validation_status") == "refused"
    )
    return evidence[-MAX_COMMAND_EVIDENCE:], truncated, matching_count, refused_count


def inspect_remote_receipts(
    artifact_root: pathlib.Path,
    journal_path: pathlib.Path,
    *,
    requested_session_id: str | None = None,
    update_id: int | None = None,
) -> dict[str, Any]:
    """Join one Plan session's command evidence and ordered receipt files."""

    requested = str(requested_session_id or "").strip() or None
    if requested is not None and not PLAN_SESSION_ID_PATTERN.fullmatch(requested):
        raise RemoteOperatorStateError("receipt session id is invalid")
    records = latest_update_records(journal_path)
    selected_record = records.get(update_id) if update_id is not None else None
    update_session_id = None
    if selected_record:
        effect = (
            selected_record.get("effect")
            if isinstance(selected_record.get("effect"), dict)
            else {}
        )
        update_session_id = str(effect.get("remote_plan_session_id") or "").strip() or None
        if update_session_id and not PLAN_SESSION_ID_PATTERN.fullmatch(update_session_id):
            raise RemoteOperatorStateError(
                "selected update has an invalid plan session id"
            )
    if requested and update_session_id and requested != update_session_id:
        raise RemoteOperatorStateError(
            "receipt session id does not match the selected update"
        )
    session_id = requested or update_session_id
    selection_source = (
        "explicit_session"
        if requested
        else "update_link"
        if update_session_id
        else "latest_artifact"
    )
    if session_id is None and update_id is None:
        session_id = _latest_artifact_session_id(artifact_root.expanduser())
    command_evidence, evidence_truncated, command_count, refused_count = _command_evidence(
        records,
        session_id=session_id,
        update_id=update_id,
    )
    if session_id is None:
        return {
            "schema": RECEIPT_INSPECTION_SCHEMA,
            "generated_at": utc_now(),
            "status": "no_plan_session",
            "selection_source": "update_without_plan_session"
            if update_id is not None
            else selection_source,
            "requested_update_id": update_id,
            "session_id": None,
            "command_evidence": command_evidence,
            "command_evidence_truncated": evidence_truncated,
            "command_update_count": command_count,
            "refused_command_count": refused_count,
            "receipt_chain": [],
            "available_receipt_count": 0,
            "invalid_receipt_count": 0,
            "total_receipt_stage_count": len(RECEIPT_STAGES),
            "read_only": True,
            "mutation_authorized": False,
            "approval_authorized": False,
            "execution_authorized": False,
            "accepted_truth_authorized": False,
        }

    session_dir = artifact_root.expanduser() / session_id
    try:
        session_dir.lstat()
    except FileNotFoundError:
        session_issue = "receipt_session_artifacts_not_created"
    except OSError:
        session_issue = "receipt_session_unreadable"
    else:
        if session_dir.is_symlink():
            session_issue = "receipt_session_symlink"
        elif not session_dir.is_dir():
            session_issue = "receipt_session_not_directory"
        else:
            session_issue = None
    if session_issue:
        artifacts_not_created = (
            session_issue == "receipt_session_artifacts_not_created"
        )
        if artifacts_not_created:
            session_status = "empty" if command_count > 0 else "no_plan_session"
        else:
            session_status = "invalid"
        return {
            "schema": RECEIPT_INSPECTION_SCHEMA,
            "generated_at": utc_now(),
            "status": session_status,
            "selection_source": selection_source,
            "requested_update_id": update_id,
            "session_id": session_id,
            "session_issues": [session_issue],
            "command_evidence": command_evidence,
            "command_evidence_truncated": evidence_truncated,
            "command_update_count": command_count,
            "refused_command_count": refused_count,
            "receipt_chain": [],
            "available_receipt_count": 0,
            "invalid_receipt_count": 0 if artifacts_not_created else 1,
            "total_receipt_stage_count": len(RECEIPT_STAGES),
            "read_only": True,
            "mutation_authorized": False,
            "approval_authorized": False,
            "execution_authorized": False,
            "accepted_truth_authorized": False,
        }
    receipt_chain = [
        _inspect_receipt_file(
            session_dir,
            session_id=session_id,
            stage=stage,
            filename=filename,
            expected_schema=expected_schema,
        )
        for stage, filename, expected_schema in RECEIPT_STAGES
    ]
    available = [
        item for item in receipt_chain if item["artifact_status"] == "available"
    ]
    invalid = [item for item in receipt_chain if item["artifact_status"] == "invalid"]
    latest_stage = available[-1]["stage"] if available else None
    latest_index = max(
        (
            index
            for index, item in enumerate(receipt_chain)
            if item["artifact_status"] == "available"
        ),
        default=-1,
    )
    missing_required_stages = [
        receipt_chain[index]["stage"]
        for index in range(2, latest_index + 1)
        if receipt_chain[index]["artifact_status"] == "not_created"
        and receipt_chain[index]["stage"] not in OPTIONAL_RECEIPT_STAGES
    ]
    latest_receipt = (
        available[-1].get("receipt")
        if available and isinstance(available[-1].get("receipt"), dict)
        else {}
    )
    latest_state = (
        latest_receipt.get("state")
        if isinstance(latest_receipt.get("state"), dict)
        else {}
    )
    review_closed = latest_stage == "plan_review" and latest_state.get(
        "decision"
    ) in {"revision_required", "rejected"}
    if invalid or missing_required_stages:
        status = "invalid"
    elif review_closed:
        status = "review_closed"
    elif available and available[-1]["stage"] == "plan_closeout_verdict":
        status = "complete"
    elif available:
        status = "in_progress"
    else:
        status = "empty"
    return {
        "schema": RECEIPT_INSPECTION_SCHEMA,
        "generated_at": utc_now(),
        "status": status,
        "selection_source": selection_source,
        "requested_update_id": update_id,
        "session_id": session_id,
        "artifact_dir_hash": sha256_short(str(session_dir)),
        "command_evidence": command_evidence,
        "command_evidence_truncated": evidence_truncated,
        "command_update_count": command_count,
        "refused_command_count": refused_count,
        "receipt_chain": receipt_chain,
        "available_receipt_count": len(available),
        "invalid_receipt_count": len(invalid),
        "missing_required_stages": missing_required_stages,
        "total_receipt_stage_count": len(RECEIPT_STAGES),
        "latest_available_stage": latest_stage,
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
        "execution_authorized": False,
        "accepted_truth_authorized": False,
    }
