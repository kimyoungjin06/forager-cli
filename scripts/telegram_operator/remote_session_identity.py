"""Transport-neutral live-session identity validation.

The Rust status surface observes the local machine, project and worktree roots,
harness launch, durable session row, and current tmux session, pane, and process.
Remote transports store that exact envelope and use this module only to compare
it with a fresh status observation before applying a pending action.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


REMOTE_SESSION_IDENTITY_SCHEMA = "remote_session_identity.v1"
BOUND_IDENTITY_FIELDS = (
    "machine_identity_sha256",
    "project_key",
    "project_root_identity_sha256",
    "worktree_kind",
    "worktree_identity_sha256",
    "harness_kind",
    "harness_launch_sha256",
    "session_id",
    "session_record_identity_sha256",
    "transport_kind",
    "transport_session_identity_sha256",
    "pane_identity_sha256",
    "process_identity_sha256",
)
SHA256_FIELDS = {
    "machine_identity_sha256",
    "project_root_identity_sha256",
    "worktree_identity_sha256",
    "harness_launch_sha256",
    "session_record_identity_sha256",
    "transport_session_identity_sha256",
    "pane_identity_sha256",
    "process_identity_sha256",
    "binding_sha256",
}
VALID_WORKTREE_KINDS = {"direct_project_root", "managed_worktree"}


def canonical_binding_sha256(identity: dict[str, Any]) -> str:
    payload = copy.deepcopy(identity)
    payload.pop("binding_sha256", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def identity_issue(identity: Any) -> str | None:
    if not isinstance(identity, dict):
        return "identity_missing"
    expected_fields = {"schema", "binding_sha256", *BOUND_IDENTITY_FIELDS}
    if set(identity) != expected_fields:
        return "identity_fields_invalid"
    if identity.get("schema") != REMOTE_SESSION_IDENTITY_SCHEMA:
        return "identity_schema_invalid"
    for field in SHA256_FIELDS:
        value = identity.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            return f"{field}_invalid"
    for field in (
        "harness_kind",
        "session_id",
        "transport_kind",
    ):
        value = identity.get(field)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return f"{field}_invalid"
    project_key = identity.get("project_key")
    if project_key is not None and (
        not isinstance(project_key, str)
        or not project_key.strip()
        or len(project_key) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in project_key)
    ):
        return "project_key_invalid"
    if identity.get("worktree_kind") not in VALID_WORKTREE_KINDS:
        return "worktree_kind_invalid"
    if identity.get("transport_kind") != "tmux":
        return "transport_kind_unsupported"
    if identity.get("binding_sha256") != canonical_binding_sha256(identity):
        return "binding_sha256_mismatch"
    return None


def signed_identity(identity: dict[str, Any]) -> dict[str, Any]:
    """Return a canonically bound copy, primarily for deterministic fixtures."""

    bound = copy.deepcopy(identity)
    bound["binding_sha256"] = canonical_binding_sha256(bound)
    issue = identity_issue(bound)
    if issue:
        raise ValueError(issue)
    return bound


def session_identity_issue(session: Any) -> str | None:
    if not isinstance(session, dict):
        return "session_missing"
    identity = session.get("remote_session_identity")
    issue = identity_issue(identity)
    if issue:
        return issue
    assert isinstance(identity, dict)
    if str(session.get("id") or "") != identity.get("session_id"):
        return "session_id_row_mismatch"
    tool = str(session.get("tool") or "")
    if tool and tool != identity.get("harness_kind"):
        return "harness_kind_row_mismatch"
    project = session.get("project")
    if project is not None and project != identity.get("project_key"):
        return "project_key_row_mismatch"
    return None


def notification_capture_tail(session: Any) -> str:
    """Accept only an explicit status capture bound to the same valid identity.

    A missing producer capability must never fall back to a name-based tmux
    read. This envelope is trusted local IPC, not a signature for remote input.
    """

    if session_identity_issue(session):
        return ""
    capture = session.get("notification_capture")
    if not isinstance(capture, dict) or capture.get("schema") != "notification_capture.v1":
        return ""
    if capture.get("binding_sha256") != session["remote_session_identity"]["binding_sha256"]:
        return ""
    tail = capture.get("tail")
    return tail if isinstance(tail, str) else ""


def identity_replacement_issue(
    expected: Any, current: Any
) -> str | None:
    expected_issue = identity_issue(expected)
    if expected_issue:
        return f"expected_{expected_issue}"
    current_issue = identity_issue(current)
    if current_issue:
        return f"current_{current_issue}"
    assert isinstance(expected, dict) and isinstance(current, dict)
    for field in BOUND_IDENTITY_FIELDS:
        if expected.get(field) != current.get(field):
            return f"{field}_changed"
    if expected.get("binding_sha256") != current.get("binding_sha256"):
        return "binding_sha256_changed"
    return None


def pending_action_identity_issue(
    expected: Any,
    status: Any,
) -> str | None:
    issue = identity_issue(expected)
    if issue:
        return f"expected_{issue}"
    if not isinstance(status, dict):
        return "status_missing"
    rows = [row for row in status.get("sessions") or [] if isinstance(row, dict)]
    assert isinstance(expected, dict)
    matches = [row for row in rows if row.get("id") == expected.get("session_id")]
    if len(matches) != 1:
        return "session_missing" if not matches else "session_ambiguous"
    row_issue = session_identity_issue(matches[0])
    if row_issue:
        return f"current_{row_issue}"
    return identity_replacement_issue(
        expected,
        matches[0].get("remote_session_identity"),
    )
