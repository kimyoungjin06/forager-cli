"""Feedback persistence and optional Plan intake transitions.

This module owns the durable domain work that follows an admitted feedback or
Plan request. It deliberately returns data only. Telegram presentation remains
separate, so direct Plan requests and confirmed natural-language captures use
the same receipt and session transition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Any

from .agent import classify_feedback_kind
from .common import append_jsonl, utc_now, write_json
from .plan_workflow import store_remote_plan_session
from .receipts import create_remote_plan_session
from .redaction import public_remote_plan_session
from .rendering import sanitize_text


FEEDBACK_WORKFLOW_SCHEMA = "remote_operator_feedback_workflow.v1"


def decision_feedback_ingest_command(
    args: argparse.Namespace,
    feedback_path: pathlib.Path,
) -> list[str]:
    argv = [args.forager_bin]
    if args.profile:
        argv.extend(["--profile", args.profile])
    argv.extend(
        [
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            str(feedback_path),
            "--json",
        ]
    )
    return argv


def ingest_feedback_decision(
    args: argparse.Namespace,
    feedback_record: dict[str, Any],
) -> dict[str, Any]:
    if not args.decision_feedback_ingest:
        return {"decision_feedback_ingest_status": "disabled"}
    fingerprint = hashlib.sha256(
        json.dumps(feedback_record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    message_id = feedback_record.get("message_id")
    suffix = str(message_id) if message_id is not None else fingerprint
    feedback_path = (
        args.feedback_ingest_dir
        / f"telegram_feedback_{suffix}_{fingerprint}.json"
    )
    write_json(feedback_path, feedback_record)
    command = decision_feedback_ingest_command(args, feedback_path)
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(
                str(error), max_chars=300
            ),
        }
    if process.returncode != 0:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(
                process.stderr.strip() or process.stdout.strip(),
                max_chars=300,
            ),
        }
    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": "decision ingest did not return JSON",
        }
    return {
        "decision_feedback_ingest_status": (
            "recorded" if report.get("appended") is True else "existing"
        ),
        "decision_feedback_ingest_file": str(feedback_path),
        "decision_feedback_decision_id": report.get("decision_id"),
        "decision_feedback_appended": bool(report.get("appended")),
    }


@dataclass(frozen=True)
class FeedbackWorkflowOutcome:
    """One completed feedback receipt transition with optional Plan intake."""

    feedback_record: dict[str, Any]
    feedback_result: dict[str, Any]
    ingest_result: dict[str, Any]
    plan_session: dict[str, Any] | None

    def public_record(self) -> dict[str, Any]:
        command = str(self.feedback_record.get("workflow_command") or "feedback")
        session_id = (
            str(self.plan_session.get("session_id") or "")
            if isinstance(self.plan_session, dict)
            else ""
        )
        identity = {
            "chat_id_hash": self.feedback_record.get("chat_id_hash"),
            "user_id_hash": self.feedback_record.get("user_id_hash"),
            "message_id": self.feedback_record.get("message_id"),
            "command": command,
            "feedback_kind": self.feedback_record.get("feedback_kind"),
            "decision_id": self.ingest_result.get("decision_feedback_decision_id"),
            "plan_session_id": session_id or None,
        }
        transition_sha = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "schema": FEEDBACK_WORKFLOW_SCHEMA,
            "transition_id": f"rfw-{transition_sha[:24]}",
            "command": command,
            "feedback_kind": self.feedback_record.get("feedback_kind"),
            "feedback_recorded": bool(self.feedback_result.get("feedback_recorded")),
            "decision_receipt_status": self.ingest_result.get(
                "decision_feedback_ingest_status"
            ),
            "plan_session_started": bool(session_id),
            "plan_session_id": session_id or None,
            "plan_stage": (
                str(self.plan_session.get("stage") or "") or None
                if isinstance(self.plan_session, dict)
                else None
            ),
        }

    def result_fields(self) -> dict[str, Any]:
        """Return public domain fields without presentation or raw feedback."""

        fields = dict(self.feedback_result)
        fields.update(self.ingest_result)
        if isinstance(self.plan_session, dict):
            fields["remote_plan_session"] = public_remote_plan_session(
                self.plan_session
            )
        fields["feedback_workflow"] = self.public_record()
        return fields


def process_feedback_workflow(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    chat_hash: str,
    user_hash: str,
    message_id: int | None,
    text: str,
    parsed_command: dict[str, Any],
    feedback_context: dict[str, Any] | None,
) -> FeedbackWorkflowOutcome:
    """Persist feedback, ingest its decision receipt, and start Plan intake."""

    parsed_kind = str(parsed_command.get("feedback_kind") or "").strip()
    feedback_kind = (
        parsed_kind
        if parsed_kind in {"freeform_feedback", "planning_request"}
        else classify_feedback_kind(text)
    )
    workflow_command = str(parsed_command.get("command") or "feedback")
    if workflow_command not in {"feedback", "plan_request"}:
        raise ValueError("unsupported_feedback_workflow_command")
    agent_intent = (
        parsed_command.get("agent_intent")
        if isinstance(parsed_command.get("agent_intent"), dict)
        else None
    )
    record = {
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": utc_now(),
        "profile": args.profile,
        "chat_id_hash": chat_hash,
        "user_id_hash": user_hash,
        "message_id": message_id,
        "feedback_text": sanitize_text(text, max_chars=2000),
        "feedback_kind": feedback_kind,
        "target_chat_id_hash": config.get("target_chat_id_hash"),
        "feedback_context": feedback_context,
        "workflow_command": workflow_command,
    }
    if agent_intent:
        record["agent_intent"] = agent_intent
    append_jsonl(args.feedback_file, record)
    feedback_result = {
        "feedback_recorded": True,
        "feedback_file": str(args.feedback_file),
        "feedback_text_chars": len(str(text or "")),
        "feedback_context": feedback_context,
    }
    ingest_result = ingest_feedback_decision(args, record)
    plan_session = None
    if workflow_command == "plan_request":
        plan_session = create_remote_plan_session(
            args,
            chat_hash=chat_hash,
            request_text=text,
            parsed_command=parsed_command,
            feedback_context=feedback_context,
            decision_id=ingest_result.get("decision_feedback_decision_id"),
        )
        store_remote_plan_session(state, chat_hash, plan_session)
    return FeedbackWorkflowOutcome(
        feedback_record=record,
        feedback_result=feedback_result,
        ingest_result=ingest_result,
        plan_session=plan_session,
    )
