"""Telegram remote plan-session state machine.

The multi-stage plan workflow: project selection and path resolution, and
the stage transitions (init preview/create, draft, registration, review,
launch prep, gate request/resolution, execution brief, enqueue handoff,
workload binding, enqueue run, runtime start/monitor, closeout packet/review/
verdict). handle_remote_plan_session_input is the entry point; run_once in the
main script routes an active session's input here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import shlex
import subprocess
from typing import Any

from .base import (
    attach_choice_surface,
    result_base,
)
from .common import (
    utc_now,
    write_json,
)
from .plan_messages import (
    render_plan_closeout_packet_failed_message,
    render_plan_closeout_packet_message,
    render_plan_closeout_required_message,
    render_plan_closeout_review_handoff_failed_message,
    render_plan_closeout_review_handoff_message,
    render_plan_closeout_verdict_failed_message,
    render_plan_closeout_verdict_message,
    render_plan_draft_compared_message,
    render_plan_draft_failed_message,
    render_plan_draft_required_message,
    render_plan_draft_selected_message,
    render_plan_enqueue_handoff_created_message,
    render_plan_enqueue_handoff_failed_message,
    render_plan_enqueue_handoff_required_message,
    render_plan_enqueue_run_done_message,
    render_plan_enqueue_run_failed_message,
    render_plan_enqueue_run_required_message,
    render_plan_execution_brief_created_message,
    render_plan_execution_brief_failed_message,
    render_plan_execution_brief_required_message,
    render_plan_gate_request_created_message,
    render_plan_gate_request_failed_message,
    render_plan_gate_request_required_message,
    render_plan_gate_resolution_done_message,
    render_plan_gate_resolution_failed_message,
    render_plan_gate_resolution_hash_required_message,
    render_plan_gate_resolution_required_message,
    render_plan_launch_prep_failed_message,
    render_plan_launch_prep_prepared_message,
    render_plan_launch_prep_required_message,
    render_plan_registered_message,
    render_plan_registration_failed_message,
    render_plan_registration_required_message,
    render_plan_registration_stale_message,
    render_plan_review_approved_message,
    render_plan_review_failed_message,
    render_plan_review_hash_required_message,
    render_plan_review_rejected_message,
    render_plan_review_required_message,
    render_plan_review_revision_required_message,
    render_plan_runtime_monitor_failed_message,
    render_plan_runtime_monitor_message,
    render_plan_runtime_monitor_required_message,
    render_plan_runtime_start_failed_message,
    render_plan_runtime_start_required_message,
    render_plan_runtime_started_message,
    render_plan_workload_binding_failed_message,
    render_plan_workload_bound_message,
    render_plan_workload_path_required_message,
    render_project_init_created_message,
    render_project_init_failed_message,
    render_project_init_preview_message,
    render_project_init_preview_required_message,
    render_project_path_required_message,
    render_project_selected_message,
    render_project_selection_deferred_message,
    render_project_selection_message,
    render_remote_plan_note_message,
)
from .project_candidates import (
    build_project_candidate,
    discover_project_paths,
    manual_project_candidate,
    ranked_project_candidates,
    request_tokens,
    scan_project_candidates,
    truncate_label,
    workspace_roots,
)
from .receipts import (
    bind_prepared_workload_to_execution_brief,
    create_closeout_packet_from_runtime_monitor,
    create_closeout_review_handoff_from_packet,
    create_closeout_verdict_from_handoff,
    create_enqueue_handoff_from_execution_brief,
    create_enqueue_run_from_workload_binding,
    create_execution_brief_from_gate_resolution,
    create_plan_draft,
    create_project_init_preview,
    create_runtime_monitor_from_runtime_start,
    create_runtime_start_from_enqueue_run,
    prepare_plan_launch_packet,
    run_project_init_packet,
    select_plan_draft,
    sha256_hex,
    sha256_id,
)
from .redaction import public_remote_plan_session
from .rendering import (
    REMOTE_PLAN_INIT_CONTEXT_KIND,
    REMOTE_PLAN_SESSION_CONTEXT_KIND,
    mobile_card_contract,
    plan_context_choice_labels,
    plan_context_identity,
    plan_session_callback_id,
    sanitize_text,
)
from .schemas import (
    INTERACTION_CONTEXT_SCHEMA,
    PLAN_GATE_REQUEST_SCHEMA,
    PLAN_GATE_RESOLUTION_SCHEMA,
    PLAN_REGISTRATION_SCHEMA,
    PLAN_REVIEW_SCHEMA,
    PLAN_DRAFT_SELECTION_SCHEMA,
)


def remote_plan_choice_label(candidate: dict[str, Any]) -> str:
    rank = int(candidate.get("rank") or 0)
    name = truncate_label(candidate.get("display_name") or candidate.get("project_key"), max_chars=22)
    return f"{rank} {name}".strip()


def remote_plan_selection_context(session: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    return {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": "remote_plan_project_selection",
        "profile": session.get("profile") or "default",
        "context_kind": REMOTE_PLAN_SESSION_CONTEXT_KIND,
        "focus_kind": "remote_plan_session",
        "focus_ref": session.get("session_id"),
        "workflow_stage": session.get("stage"),
        "focus_label": "계획 대상 선택",
        "next_command": None,
        "choice_labels": [remote_plan_choice_label(candidate) for candidate in candidates],
    }


def remote_plan_init_context(session: dict[str, Any]) -> dict[str, Any]:
    context = _remote_plan_init_context(session)
    context["workflow_stage"] = session.get("stage")
    return context


def _remote_plan_init_context(session: dict[str, Any]) -> dict[str, Any]:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    stage = str(session.get("stage") or "")
    if stage in {"plan_drafts_compared", "plan_draft_selection_failed"}:
        comparison = (
            session.get("plan_draft_comparison")
            if isinstance(session.get("plan_draft_comparison"), dict)
            else {}
        )
        labels = [
            str(item.get("selection_label") or "").strip()
            for item in comparison.get("candidates") or []
            if isinstance(item, dict)
            and item.get("status") == "validated"
            and str(item.get("selection_label") or "").strip()
        ]
        labels.append("보류")
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {"plan_registered", "plan_review_failed"}:
        labels = plan_review_choice_labels(session)
        if labels:
            labels.append("보류")
        else:
            labels = ["다시 선택", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {"plan_review_revision_required", "plan_review_rejected"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["다시 선택", "보류"],
        }
    if stage in {"plan_gate_request_created", "plan_gate_resolution_failed"}:
        labels = gate_resolution_choice_labels(session)
        if labels:
            labels.append("보류")
        else:
            labels = ["보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage == "plan_gate_approved":
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["실행 브리프 생성", "보류"],
        }
    if stage in {"plan_execution_brief_created", "plan_enqueue_handoff_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["큐 등록 검토", "보류"],
        }
    if stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["워크로드 패킷 연결", "보류"],
        }
    if stage in {"plan_workload_bound", "plan_enqueue_run_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["큐 등록 실행", "보류"],
        }
    if stage in {"plan_enqueued", "plan_runtime_start_failed"}:
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": ["실행 시작", "보류"],
        }
    if stage in {"plan_runtime_started", "plan_runtime_monitored", "plan_runtime_monitor_failed"}:
        monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
        if stage == "plan_runtime_monitored" and monitor.get("task_status") == "completed":
            labels = ["마무리 패킷 생성", "실행 상태 확인", "보류"]
        else:
            labels = ["실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {
        "plan_closeout_packet_created",
        "plan_closeout_packet_failed",
        "plan_closeout_review_handoff_created",
        "plan_closeout_review_handoff_failed",
    }:
        labels = ["실행 상태 확인", "보류"]
        if stage == "plan_closeout_packet_created":
            labels = ["마무리 검토 준비", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_packet_failed":
            labels = ["마무리 패킷 생성", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_review_handoff_failed":
            labels = ["마무리 검토 준비", "실행 상태 확인", "보류"]
        if stage == "plan_closeout_review_handoff_created":
            labels = ["승인 기록", "수정 요청 기록", "차단 기록", "실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage in {"plan_closeout_verdict_recorded", "plan_closeout_verdict_failed"}:
        labels = ["실행 상태 확인", "보류"]
        if stage == "plan_closeout_verdict_failed":
            labels = ["수정 요청 기록", "차단 기록", "실행 상태 확인", "보류"]
        return {
            "schema": INTERACTION_CONTEXT_SCHEMA,
            "command": "remote_plan_init_review",
            "profile": session.get("profile") or "default",
            "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
            "focus_kind": "remote_plan_session",
            "focus_ref": session.get("session_id"),
            "focus_label": candidate.get("display_name") or "프로젝트",
            "next_command": None,
            "choice_labels": labels,
        }
    if stage == "project_init_previewed":
        primary = "초기화 생성"
    elif stage in {"plan_review_approved", "plan_launch_prep_failed"}:
        primary = "실행 준비 검토"
    elif stage in {"plan_launch_prep_prepared", "plan_gate_request_failed"}:
        primary = "게이트 요청"
    elif stage == "plan_draft_validated":
        primary = "계획 등록"
    elif stage in {"project_init_created", "plan_draft_failed"}:
        primary = "계획 초안 생성"
    elif stage == "plan_registration_failed":
        registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
        primary = "계획 초안 생성" if registration.get("status") == "stale" else "계획 등록"
    else:
        primary = "초기화 검토"
    return {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": "remote_plan_init_review",
        "profile": session.get("profile") or "default",
        "context_kind": REMOTE_PLAN_INIT_CONTEXT_KIND,
        "focus_kind": "remote_plan_session",
        "focus_ref": session.get("session_id"),
        "focus_label": candidate.get("display_name") or "프로젝트",
        "next_command": None,
        "choice_labels": [primary, "다시 선택", "보류"],
    }


PLAN_CALLBACK_PAYLOAD_RE = re.compile(
    r"^@([0-9a-f]{8}):([0-9a-f]{16}):([0-9]{1,2})$"
)


def resolve_remote_plan_callback_payload(
    session: dict[str, Any], payload: str
) -> tuple[str | None, str | None]:
    """Resolve a bounded Plan callback against the current session context."""

    value = str(payload or "").strip()
    match = PLAN_CALLBACK_PAYLOAD_RE.fullmatch(value)
    if not match:
        return value, None
    sid, expected_context_hash, raw_index = match.groups()
    session_id = str(session.get("session_id") or "")
    if plan_session_callback_id(session_id) != sid:
        return None, "plan_session_changed"
    context = (
        remote_plan_selection_context(session)
        if str(session.get("stage") or "") == "project_selection"
        else remote_plan_init_context(session)
    )
    if plan_context_identity(context) != expected_context_hash:
        return None, "plan_context_changed"
    labels = plan_context_choice_labels(context)
    index = int(raw_index)
    if index >= len(labels):
        return None, "plan_choice_missing"
    return labels[index], None


def project_init_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "초기화 생성",
        "초기화 패킷 생성",
        "project init 생성",
        "project init run",
        "create init",
        "create project init",
    }


def plan_draft_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "계획 초안 생성",
        "계획초안 생성",
        "초안 생성",
        "plan draft",
        "create plan draft",
        "draft plan",
    }


def plan_draft_candidate_for_text(
    comparison: dict[str, Any], text: str
) -> dict[str, Any] | None:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return None
    for candidate in comparison.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("status") != "validated":
            continue
        values = {
            str(candidate.get("selection_label") or "").strip().lower(),
            str(candidate.get("strategy_key") or "").strip().lower(),
            str(candidate.get("strategy_label") or "").strip().lower(),
            str(candidate.get("comparison_rank") or "").strip().lower(),
        }
        if normalized in values:
            return candidate
    return None


def plan_registration_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "계획 등록",
        "계획등록",
        "plan register",
        "register plan",
        "create plan registration",
    }


PLAN_REVIEW_ACTION_ALIASES = {
    "계획 승인": "approved",
    "계획승인": "approved",
    "승인": "approved",
    "plan approve": "approved",
    "approve plan": "approved",
    "approve": "approved",
    "수정 요청": "revision_required",
    "계획 수정": "revision_required",
    "수정 필요": "revision_required",
    "request revision": "revision_required",
    "revision required": "revision_required",
    "계획 거절": "rejected",
    "계획 거부": "rejected",
    "거절": "rejected",
    "reject plan": "rejected",
    "rejected": "rejected",
}


def plan_review_request(text: str) -> tuple[str | None, str | None]:
    normalized = " ".join(str(text or "").strip().lower().split())
    for label, decision in PLAN_REVIEW_ACTION_ALIASES.items():
        if normalized == label:
            return decision, None
        prefix = f"{label} "
        if normalized.startswith(prefix):
            return decision, normalized[len(prefix) :].strip() or None
    return None, None


def plan_launch_prep_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 준비 검토",
        "실행준비 검토",
        "실행 준비",
        "실행준비",
        "launch prep",
        "plan launch prep",
        "prepare launch",
        "prepare launch packet",
    }


def plan_gate_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "게이트 요청",
        "게이트요청",
        "승인 요청",
        "승인요청",
        "gate request",
        "request gate",
        "create gate request",
        "request approval",
    }


GATE_APPROVAL_ACTION_ALIASES = {
    "게이트 승인": True,
    "게이트승인": True,
    "실행 승인": True,
    "실행승인": True,
    "approve gate": True,
    "gate approve": True,
    "approve approval": True,
    "게이트 거절": False,
    "게이트거절": False,
    "게이트 반려": False,
    "실행 거절": False,
    "실행거절": False,
    "deny gate": False,
    "gate deny": False,
    "deny approval": False,
}


def gate_approval_request(text: str) -> tuple[bool | None, str | None]:
    normalized = " ".join(str(text or "").strip().lower().split())
    for label, approve in GATE_APPROVAL_ACTION_ALIASES.items():
        if normalized == label:
            return approve, None
        prefix = f"{label} "
        if normalized.startswith(prefix):
            return approve, normalized[len(prefix) :].strip() or None
    return None, None


def gate_approval_approve_text(text: str) -> bool:
    return gate_approval_request(text)[0] is True


def gate_approval_deny_text(text: str) -> bool:
    return gate_approval_request(text)[0] is False


def execution_brief_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 브리프 생성",
        "실행브리프 생성",
        "브리프 생성",
        "execution brief",
        "create execution brief",
        "runtime brief",
    }


def enqueue_handoff_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "큐 등록 검토",
        "큐등록 검토",
        "큐 등록 준비",
        "enqueue 검토",
        "enqueue 준비",
        "enqueue handoff",
        "create enqueue handoff",
        "prepare enqueue",
    }


def workload_binding_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "워크로드 패킷 연결",
        "워크로드패킷 연결",
        "prepared_task 연결",
        "prepared task 연결",
        "bind workload",
        "workload binding",
        "connect workload",
    }


def enqueue_run_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "큐 등록 실행",
        "큐등록 실행",
        "큐 등록",
        "enqueue 실행",
        "enqueue run",
        "run enqueue",
        "execute enqueue",
    }


def runtime_start_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 시작",
        "실행시작",
        "런타임 시작",
        "start runtime",
        "runtime start",
        "start execution",
    }


def runtime_monitor_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "실행 상태 확인",
        "실행상태 확인",
        "상태 확인",
        "런타임 상태 확인",
        "monitor runtime",
        "runtime monitor",
        "check runtime",
        "check execution",
    }


def closeout_packet_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "마무리 패킷 생성",
        "마무리 생성",
        "closeout 생성",
        "closeout 패킷 생성",
        "closeout packet",
        "create closeout",
        "create closeout packet",
    }


def closeout_review_handoff_create_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "마무리 검토 준비",
        "마무리검토 준비",
        "closeout 검토 준비",
        "closeout-review 준비",
        "closeout review 준비",
        "prepare closeout review",
        "closeout review handoff",
    }


def closeout_verdict_request(text: str) -> tuple[str | None, str]:
    raw = str(text or "").strip()
    normalized = raw.lower()
    aliases: list[tuple[str, tuple[str, ...]]] = [
        (
            "revise",
            (
                "수정 요청 기록",
                "수정요청 기록",
                "수정 필요 기록",
                "revision required",
                "record revise",
                "revise closeout",
            ),
        ),
        (
            "blocked",
            (
                "차단 기록",
                "블락 기록",
                "blocked 기록",
                "record blocked",
                "block closeout",
            ),
        ),
        (
            "approved",
            (
                "승인 기록",
                "승인",
                "approved 기록",
                "record approved",
                "approve closeout",
                "accept closeout",
            ),
        ),
    ]
    for verdict, values in aliases:
        for value in values:
            lowered = value.lower()
            if normalized == lowered:
                return verdict, ""
            if normalized.startswith(f"{lowered}:") or normalized.startswith(f"{lowered} -"):
                note = raw[len(value) :].lstrip(" :-")
                return verdict, sanitize_text(note, max_chars=500)
    return None, ""


def register_plan_draft(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    draft: dict[str, Any],
) -> dict[str, Any]:
    project_key = str(draft.get("project_key") or "project")
    plan_path = pathlib.Path(str(draft.get("plan_artifact_path") or ""))
    expected_plan_sha256 = draft.get("plan_sha256")
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_REGISTRATION.json"
    receipt = {
        "schema": PLAN_REGISTRATION_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_artifact_path": str(plan_path),
        "expected_plan_sha256": expected_plan_sha256,
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    if draft.get("schema") == PLAN_DRAFT_SELECTION_SCHEMA:
        selection_path = pathlib.Path(str(draft.get("artifact_path") or ""))
        receipt["selection_receipt_path"] = str(selection_path)
        receipt["expected_selection_sha256"] = draft.get("receipt_sha256")
        try:
            selection_raw = selection_path.read_bytes()
            selection_receipt = json.loads(selection_raw)
        except (OSError, json.JSONDecodeError) as error:
            receipt.update(
                {
                    "status": "error",
                    "error": sanitize_text(
                        f"plan selection receipt unavailable: {error}",
                        max_chars=400,
                    ),
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
        current_selection_sha256 = sha256_hex(selection_raw)
        receipt["current_selection_sha256"] = current_selection_sha256
        selection_matches = (
            isinstance(selection_receipt, dict)
            and draft.get("receipt_sha256") == current_selection_sha256
            and selection_receipt.get("schema") == PLAN_DRAFT_SELECTION_SCHEMA
            and selection_receipt.get("session_id") == session.get("session_id")
            and selection_receipt.get("status") == "selected"
            and selection_receipt.get("strategy_key") == draft.get("strategy_key")
        )
        receipt["selection_hash_matched"] = selection_matches
        if not selection_matches:
            receipt.update(
                {
                    "status": "stale",
                    "error": "plan selection receipt changed before registration",
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
        project_key = str(selection_receipt.get("project_key") or "project")
        plan_path = pathlib.Path(
            str(selection_receipt.get("plan_artifact_path") or "")
        )
        expected_plan_sha256 = selection_receipt.get("plan_sha256")
        receipt.update(
            {
                "project_key": project_key,
                "plan_artifact_path": str(plan_path),
                "expected_plan_sha256": expected_plan_sha256,
            }
        )
    try:
        current_sha = sha256_hex(plan_path.read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan draft unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_plan_sha256"] = current_sha
    if expected_plan_sha256 != current_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "plan draft changed after validation",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "plan",
            str(plan_path),
            "--project-key",
            project_key,
            "--request-id",
            str(session.get("session_id") or ""),
            "--json",
        ]
    )
    receipt["registration_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_registration_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        registration_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan registration did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "registered",
            "registration_output": registration_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def registered_plan_ref(registration: dict[str, Any]) -> str:
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    plan_id = str(output.get("plan_id") or "").strip()
    if plan_id:
        return plan_id
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    registry_dir = str(artifacts.get("registry_dir") or "").strip()
    if registry_dir:
        return pathlib.Path(registry_dir).name
    return ""


def plan_registration_observed_hash(registration: dict[str, Any]) -> str:
    output = (
        registration.get("registration_output")
        if isinstance(registration.get("registration_output"), dict)
        else {}
    )
    source_sha256 = str(output.get("source_sha256") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", source_sha256):
        return f"sha256:{source_sha256}"
    return ""


def plan_review_action_token(session: dict[str, Any], observed_hash: str) -> str:
    session_id = str(session.get("session_id") or "").strip()
    if not session_id or not observed_hash:
        return ""
    payload = f"{session_id}|{observed_hash}|plan_review.v1"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def plan_review_choice_labels(session: dict[str, Any]) -> list[str]:
    registration = (
        session.get("plan_registration")
        if isinstance(session.get("plan_registration"), dict)
        else {}
    )
    observed_hash = plan_registration_observed_hash(registration)
    token = plan_review_action_token(session, observed_hash)
    if not token:
        return []
    return [
        f"계획 승인 {token}",
        f"수정 요청 {token}",
        f"계획 거절 {token}",
    ]


def review_registered_plan(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    registration: dict[str, Any],
    decision: str,
    received_action_token: str | None,
) -> dict[str, Any]:
    if decision not in {"approved", "revision_required", "rejected"}:
        raise ValueError(f"unsupported plan review decision: {decision}")
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or registration.get("project_key") or "project")
    plan_ref = registered_plan_ref(registration)
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_REVIEW.json"
    copied_source_json = str(artifacts.get("copied_source_json") or "").strip()
    expected_source_sha = str(output.get("source_sha256") or "").strip().lower()
    observed_hash = plan_registration_observed_hash(registration)
    expected_action_token = plan_review_action_token(session, observed_hash)
    receipt = {
        "schema": PLAN_REVIEW_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_ref": plan_ref,
        "registration_json": str(artifacts.get("registration_json") or ""),
        "copied_source_json": copied_source_json,
        "expected_source_sha256": expected_source_sha,
        "decision": decision,
        "observed_hash": observed_hash,
        "action_token": expected_action_token,
        "received_action_token": str(received_action_token or ""),
        "observed_hash_matched": False,
        "plan_review_authorized": True,
        "approval_authorized": False,
        "execution_authorized": False,
        "launch_preparation_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not plan_ref:
        receipt.update(
            {
                "status": "error",
                "error": "registered plan id unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not observed_hash or not expected_action_token:
        receipt.update(
            {
                "status": "error",
                "error": "registered plan observed hash unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not copied_source_json or not expected_source_sha:
        receipt.update(
            {
                "status": "error",
                "error": "registered plan source evidence unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if str(received_action_token or "").strip().lower() != expected_action_token:
        receipt.update(
            {
                "status": "stale",
                "error": "plan review action does not match the displayed plan hash",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(copied_source_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(
                    f"registered plan source unavailable: {error}", max_chars=400
                ),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_source_sha256"] = current_sha
    if current_sha != expected_source_sha or f"sha256:{current_sha}" != observed_hash:
        receipt.update(
            {
                "status": "stale",
                "error": "registered plan source changed after registration",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["observed_hash_matched"] = True
    cli_decision = "revision-required" if decision == "revision_required" else decision
    reason = {
        "approved": "Telegram operator approved the registered plan for a separate launch-preparation review.",
        "revision_required": "Telegram operator requested revision of the registered plan before launch preparation.",
        "rejected": "Telegram operator rejected the registered plan direction.",
    }[decision]
    follow_up = {
        "approved": "Prepare launch packet in a separate command.",
        "revision_required": "Revise and register a new plan before another review.",
        "rejected": "Discard or replace the plan before another review.",
    }[decision]
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "plan-review",
            plan_ref,
            "--decision",
            cli_decision,
            "--reviewer",
            "telegram",
            "--reason",
            reason,
            "--follow-up",
            follow_up,
        ]
    )
    if decision == "revision_required":
        command.extend(
            [
                "--blocker",
                "Operator requested plan revision before launch preparation.",
            ]
        )
    elif decision == "rejected":
        command.extend(
            [
                "--blocker",
                "Operator rejected the current plan direction.",
            ]
        )
    command.append("--json")
    receipt["review_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_review_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        review_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan review did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if review_output.get("decision") != decision:
        receipt.update(
            {
                "status": "error",
                "error": "plan review returned an unexpected decision",
                "review_output": review_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": decision,
            "review_output": review_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def request_gate_for_launch_prep(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    launch_prep: dict[str, Any],
) -> dict[str, Any]:
    output = launch_prep.get("launch_prep_output") if isinstance(launch_prep.get("launch_prep_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or launch_prep.get("project_key") or "project")
    request_id = str(output.get("request_id") or session.get("session_id") or "telegram_request")
    task_id = str(output.get("task_id") or "").strip()
    if not task_id:
        task_id = f"telegram_gate_{sha256_id(str(session.get('session_id') or '') + ':' + str(output.get('prep_id') or 'launch_prep'))}"
    launch_prep_json = str(launch_prep.get("launch_prep_json") or artifacts.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(launch_prep.get("launch_prep_sha256") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_GATE_REQUEST.json"
    receipt = {
        "schema": PLAN_GATE_REQUEST_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "capability_id": "dispatch.runtime",
        "mutation_class": "dispatch.runtime",
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "gate_request_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not launch_prep_json:
        receipt.update(
            {
                "status": "error",
                "error": "launch-preparation packet path unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"launch-preparation packet unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_launch_prep_sha256"] = current_sha
    receipt["observed_hash"] = f"sha256:{current_sha}"
    receipt["observed_hash_matched"] = not expected_launch_prep_sha or (
        current_sha == expected_launch_prep_sha
    )
    if expected_launch_prep_sha and current_sha != expected_launch_prep_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "launch-preparation packet changed after preparation",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    prep_id = str(output.get("prep_id") or "launch-prep")
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "gate",
            "dispatch.runtime",
            "--project-key",
            project_key,
            "--request-id",
            request_id,
            "--task-id",
            task_id,
            "--mutation-class",
            "dispatch.runtime",
            "--preview",
            f"Prepare dispatch.runtime approval from launch-prep {prep_id}.",
            "--reason",
            "Telegram requested a gate evaluation from a read-only launch-preparation packet; local approval remains required.",
            "--source-surface",
            "telegram.remote_operator",
            "--json",
        ]
    )
    receipt["gate_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        gate_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"gate did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "pending_approval" if gate_output.get("status") == "pending_approval" else str(gate_output.get("status") or "evaluated"),
            "gate_output": gate_output,
            "artifact_path": str(receipt_path),
        }
    )
    receipt["action_token"] = gate_resolution_action_token(session, receipt)
    write_json(receipt_path, receipt)
    return receipt


def gate_request_approval(gate_request: dict[str, Any]) -> dict[str, Any]:
    output = gate_request.get("gate_output") if isinstance(gate_request.get("gate_output"), dict) else {}
    approval = output.get("approval") if isinstance(output.get("approval"), dict) else {}
    return approval


def gate_request_observed_hash(gate_request: dict[str, Any]) -> str:
    current_sha = str(gate_request.get("current_launch_prep_sha256") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{64}", current_sha):
        return f"sha256:{current_sha}"
    observed_hash = str(gate_request.get("observed_hash") or "").strip().lower()
    if re.fullmatch(r"sha256:[a-f0-9]{64}", observed_hash):
        return observed_hash
    return ""


def gate_resolution_action_token(
    session: dict[str, Any], gate_request: dict[str, Any]
) -> str:
    session_id = str(session.get("session_id") or "").strip()
    approval_id = str(gate_request_approval(gate_request).get("approval_id") or "").strip()
    observed_hash = gate_request_observed_hash(gate_request)
    if not session_id or not approval_id or not observed_hash:
        return ""
    payload = f"{session_id}|{approval_id}|{observed_hash}|gate_resolution.v1"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def gate_resolution_choice_labels(session: dict[str, Any]) -> list[str]:
    gate_request = (
        session.get("plan_gate_request")
        if isinstance(session.get("plan_gate_request"), dict)
        else {}
    )
    token = gate_resolution_action_token(session, gate_request)
    if not token:
        return []
    return [f"게이트 승인 {token}", f"게이트 거절 {token}"]


def pending_approval_snapshot(
    args: argparse.Namespace,
    *,
    approval_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(["offdesk", "pending", "--json"])
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, sanitize_text(f"{type(error).__name__}: {error}", max_chars=400)
    if process.returncode != 0:
        return None, sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600)
    try:
        approvals = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        return None, sanitize_text(f"pending approvals did not return JSON: {error}", max_chars=400)
    if not isinstance(approvals, list):
        return None, "pending approvals output was not a list"
    for approval in approvals:
        if isinstance(approval, dict) and str(approval.get("approval_id") or "") == approval_id:
            return approval, None
    return None, "pending approval not found"


def approval_matches_gate_request(
    approval: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    mismatches: list[str] = []
    for key in ("approval_id", "action", "project_key", "request_id", "task_id"):
        if str(approval.get(key) or "") != str(expected.get(key) or ""):
            mismatches.append(f"{key}_mismatch")
    if str(approval.get("status") or "") != "pending":
        mismatches.append("status_not_pending")
    if str(approval.get("source_surface") or "") != "telegram.remote_operator":
        mismatches.append("source_surface_mismatch")
    return mismatches


def resolve_gate_approval(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    gate_request: dict[str, Any],
    approve: bool,
    received_action_token: str | None,
) -> dict[str, Any]:
    expected = gate_request_approval(gate_request)
    approval_id = str(expected.get("approval_id") or "").strip()
    project_key = str(gate_request.get("project_key") or expected.get("project_key") or "project")
    request_id = str(gate_request.get("request_id") or expected.get("request_id") or session.get("session_id") or "")
    task_id = str(gate_request.get("task_id") or expected.get("task_id") or "")
    launch_prep_json = str(gate_request.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(gate_request.get("expected_launch_prep_sha256") or gate_request.get("current_launch_prep_sha256") or "").strip()
    observed_hash = gate_request_observed_hash(gate_request)
    expected_action_token = gate_resolution_action_token(session, gate_request)
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_GATE_RESOLUTION.json"
    decision = "approved" if approve else "denied"
    receipt = {
        "schema": PLAN_GATE_RESOLUTION_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "decision": decision,
        "approval_id": approval_id,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "observed_hash": observed_hash,
        "action_token": expected_action_token,
        "received_action_token": str(received_action_token or ""),
        "observed_hash_matched": False,
        "approval_resolution_authorized": False,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not approval_id:
        receipt.update(
            {
                "status": "error",
                "error": "approval id unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not observed_hash or not expected_action_token:
        receipt.update(
            {
                "status": "error",
                "error": "launch-preparation observed hash unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if str(received_action_token or "").strip().lower() != expected_action_token:
        receipt.update(
            {
                "status": "stale",
                "error": "gate action does not match the displayed launch-preparation hash",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not launch_prep_json:
        receipt.update(
            {
                "status": "error",
                "error": "launch-preparation packet path unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(
                    f"launch-preparation packet unavailable: {error}", max_chars=400
                ),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_launch_prep_sha256"] = current_sha
    if (
        (expected_launch_prep_sha and current_sha != expected_launch_prep_sha)
        or f"sha256:{current_sha}" != observed_hash
    ):
        receipt.update(
            {
                "status": "stale",
                "error": "launch-preparation packet changed after gate request",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["observed_hash_matched"] = True
    pending, pending_error = pending_approval_snapshot(args, approval_id=approval_id)
    if pending_error or pending is None:
        receipt.update(
            {
                "status": "stale",
                "error": pending_error or "pending approval unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["pending_approval"] = pending
    mismatches = approval_matches_gate_request(
        pending,
        {
            "approval_id": approval_id,
            "action": "dispatch.runtime",
            "project_key": project_key,
            "request_id": request_id,
            "task_id": task_id,
        },
    )
    if mismatches:
        receipt.update(
            {
                "status": "stale",
                "error": "pending approval no longer matches gate request",
                "mismatches": mismatches,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["approval_resolution_authorized"] = True
    receipt["approval_authorized"] = bool(approve)
    receipt["gate_approval_authorized"] = bool(approve)
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "ok" if approve else "cancel",
            approval_id,
            "--by",
            "telegram",
            "--json",
        ]
    )
    receipt["resolution_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.gate_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["returncode"] = process.returncode
    if process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        resolution_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"approval resolution did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": decision,
            "resolution_output": resolution_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def resolve_prepared_task_path(text: str) -> pathlib.Path | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = [raw]
    candidates = tokens or [raw]
    if raw.endswith(".json") and raw not in candidates:
        candidates.append(raw)
    for token in candidates:
        if not token or token.startswith("-"):
            continue
        path = pathlib.Path(token).expanduser()
        if not path.is_absolute():
            path = (pathlib.Path.cwd() / path).resolve()
        if path.exists() and path.is_file() and path.name == "prepared_task.json":
            return path
    return None


def remote_plan_sessions_by_chat(state: dict[str, Any]) -> dict[str, Any]:
    sessions = state.setdefault("remote_plan_sessions_by_chat", {})
    if not isinstance(sessions, dict):
        sessions = {}
        state["remote_plan_sessions_by_chat"] = sessions
    return sessions


def active_remote_plan_session(state: dict[str, Any], chat_hash: str) -> dict[str, Any] | None:
    session = remote_plan_sessions_by_chat(state).get(str(chat_hash or ""))
    if not isinstance(session, dict):
        return None
    if str(session.get("stage") or "") in {
        "project_selection",
        "project_selected",
        "project_manual_input",
        "project_path_required",
        "project_init_previewed",
        "project_init_created",
        "project_init_failed",
        "plan_drafts_compared",
        "plan_draft_selection_failed",
        "plan_draft_validated",
        "plan_draft_failed",
        "plan_registered",
        "plan_registration_failed",
        "plan_review_approved",
        "plan_review_revision_required",
        "plan_review_rejected",
        "plan_review_failed",
        "plan_launch_prep_prepared",
        "plan_launch_prep_failed",
        "plan_gate_request_created",
        "plan_gate_request_failed",
        "plan_gate_approved",
        "plan_execution_brief_created",
        "plan_execution_brief_failed",
        "plan_enqueue_handoff_created",
        "plan_enqueue_handoff_failed",
        "plan_workload_path_required",
        "plan_workload_binding_failed",
        "plan_workload_bound",
        "plan_enqueue_run_failed",
        "plan_enqueued",
        "plan_runtime_started",
        "plan_runtime_start_failed",
        "plan_runtime_monitored",
        "plan_runtime_monitor_failed",
        "plan_closeout_packet_created",
        "plan_closeout_packet_failed",
        "plan_closeout_review_handoff_created",
        "plan_closeout_review_handoff_failed",
        "plan_closeout_verdict_recorded",
        "plan_closeout_verdict_failed",
    }:
        return session
    return None


def store_remote_plan_session(state: dict[str, Any], chat_hash: str, session: dict[str, Any]) -> None:
    session["updated_at"] = utc_now()
    remote_plan_sessions_by_chat(state)[str(chat_hash or "")] = session


def remote_plan_defer_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"보류", "취소", "나중에", "hold", "cancel"}


def remote_plan_rescan_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"다시 스캔", "재스캔", "rescan", "scan again"}


def remote_plan_search_request_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return any(marker in normalized for marker in ("검색", "찾아", "찾아봐", "search", "scan"))


def remote_plan_reselect_text(text: str) -> bool:
    return str(text or "").strip().lower() in {"다시 선택", "재선택", "reselect", "choose again"}


def remote_plan_init_review_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    return normalized in {
        "초기화 검토",
        "초기화",
        "init review",
        "project init",
        "project init preview",
    }


def remote_plan_action_text(text: str) -> bool:
    return any(
        predicate(text)
        for predicate in (
            remote_plan_defer_text,
            remote_plan_rescan_text,
            remote_plan_reselect_text,
            remote_plan_init_review_text,
            project_init_create_text,
            plan_draft_create_text,
            plan_registration_create_text,
            plan_launch_prep_create_text,
            plan_gate_request_text,
            gate_approval_approve_text,
            gate_approval_deny_text,
            execution_brief_create_text,
            enqueue_handoff_create_text,
            workload_binding_request_text,
            enqueue_run_create_text,
            runtime_start_create_text,
            runtime_monitor_text,
            closeout_packet_create_text,
            closeout_review_handoff_create_text,
        )
    ) or plan_review_request(text)[0] is not None or closeout_verdict_request(text)[0] is not None


def candidate_directly_selected(candidate: dict[str, Any], text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    rank = str(candidate.get("rank") or "").strip()
    if rank and re.match(rf"^\s*{re.escape(rank)}\s*(번)?(\s|$)", normalized):
        return True
    values = (
        candidate.get("display_name"),
        candidate.get("project_key"),
        candidate.get("workspace_path_hint"),
    )
    for value in values:
        option = str(value or "").strip().lower()
        if option and normalized in {option, f"{rank} {option}".strip(), f"{rank}번 {option}".strip()}:
            return True
    return False


def remote_plan_project_selection_text(
    args: argparse.Namespace,
    session: dict[str, Any],
    text: str,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if resolve_manual_project_path(args, normalized):
        return True
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    if any(candidate_directly_selected(candidate, normalized) for candidate in candidates):
        return True
    # Allow exact workspace names that are not currently visible in the limited candidate list.
    roots = workspace_roots(args)
    lowered = normalized.lower()
    if len(normalized) <= 80 and not any(marker in lowered for marker in ("?", "？", "뭐", "왜", "설명", "알려")):
        for path in discover_project_paths(roots):
            if lowered == path.name.lower():
                return True
    return False


def remote_plan_session_should_handle_text(
    args: argparse.Namespace,
    session: dict[str, Any],
    text: str,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    stage = str(session.get("stage") or "")
    if remote_plan_action_text(normalized):
        return True
    if stage == "project_selection":
        return remote_plan_project_selection_text(args, session, normalized)
    if stage == "project_path_required":
        return bool(resolve_manual_project_path(args, normalized)) or remote_plan_search_request_text(normalized)
    if stage in {"plan_drafts_compared", "plan_draft_selection_failed"}:
        comparison = (
            session.get("plan_draft_comparison")
            if isinstance(session.get("plan_draft_comparison"), dict)
            else {}
        )
        return plan_draft_candidate_for_text(comparison, normalized) is not None
    if stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"}:
        return bool(resolve_prepared_task_path(normalized))
    return False


def append_remote_plan_note(session: dict[str, Any], text: str) -> None:
    notes = session.setdefault("operator_notes", [])
    if not isinstance(notes, list):
        notes = []
        session["operator_notes"] = notes
    notes.append(
        {
            "noted_at": utc_now(),
            "text": sanitize_text(text, max_chars=800),
        }
    )


def resolve_manual_project_path(args: argparse.Namespace, text: str) -> pathlib.Path | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    direct = pathlib.Path(raw).expanduser()
    if direct.is_absolute() and direct.exists() and direct.is_dir():
        return direct.resolve()
    for root in workspace_roots(args):
        candidate = (root / raw).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def candidate_from_manual_path(args: argparse.Namespace, path: pathlib.Path, text: str) -> dict[str, Any]:
    roots = workspace_roots(args)
    return build_project_candidate(
        path,
        roots=roots,
        tokens=request_tokens(text, path.name),
        rank=1,
    )


def candidate_matches_selection(candidate: dict[str, Any], text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    rank = str(candidate.get("rank") or "").strip()
    if rank and re.match(rf"^\s*{re.escape(rank)}\s*(번)?(\s|$)", normalized):
        return True
    for value in (
        candidate.get("display_name"),
        candidate.get("project_key"),
        candidate.get("workspace_path_hint"),
    ):
        option = str(value or "").strip().lower()
        if option and (normalized == option or option in normalized or normalized in option):
            return True
        option_tokens = re.findall(r"[a-z0-9]{3,}|[가-힣]{2,}", option)
        if any(token in normalized for token in option_tokens if len(token) >= 3):
            return True
    return False


def selected_candidate_for_text(session: dict[str, Any], text: str) -> dict[str, Any] | None:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    for candidate in candidates:
        if candidate_matches_selection(candidate, text):
            return candidate
    return None


def workspace_candidate_for_text(
    args: argparse.Namespace,
    *,
    text: str,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    resolved = resolve_manual_project_path(args, text)
    if resolved:
        candidate = candidate_from_manual_path(args, resolved, text)
        candidate["resolved_by"] = "workspace_search"
        return candidate
    candidates = ranked_project_candidates(
        args,
        request_text=" ".join([str(request_text or ""), str(text or "")]),
        agent_intent=agent_intent,
    )
    for candidate in candidates:
        if candidate_matches_selection(candidate, text):
            candidate["resolved_by"] = "workspace_search"
            return candidate
    return None


def handle_remote_plan_session_input(
    args: argparse.Namespace,
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    chat_hash: str,
    session: dict[str, Any],
    text: str,
    mode: str,
) -> dict[str, Any]:
    result = result_base(args, config, mode)
    result["command_text"] = sanitize_text(text, max_chars=400)
    normalized = str(text or "").strip()
    parsed = {
        "supported": True,
        "command": "remote_plan_selection",
        "argv": [],
        "command_text": normalized,
        "session_id": session.get("session_id"),
    }
    stage = str(session.get("stage") or "")
    plan_review_decision, plan_review_token = plan_review_request(normalized)
    gate_approval_decision, gate_approval_token = gate_approval_request(normalized)
    plan_draft_comparison = (
        session.get("plan_draft_comparison")
        if isinstance(session.get("plan_draft_comparison"), dict)
        else {}
    )
    selected_plan_draft = plan_draft_candidate_for_text(
        plan_draft_comparison, normalized
    )
    if remote_plan_defer_text(normalized):
        session["stage"] = "deferred"
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "deferred"}
        message_preview = render_project_selection_deferred_message(profile=args.profile)
        attach_choice_surface(result, None)
    elif remote_plan_rescan_text(normalized):
        agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
        session["candidates"] = scan_project_candidates(
            args,
            request_text=str(session.get("request_text") or ""),
            agent_intent=agent_intent,
        )
        session["stage"] = "project_selection"
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "rescanned"}
        message_preview = render_project_selection_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_selection_context(session))
    elif remote_plan_reselect_text(normalized):
        session["stage"] = "project_selection"
        session.pop("selected_candidate", None)
        session.pop("project_init_preview", None)
        session.pop("project_init_run", None)
        session.pop("plan_draft", None)
        session.pop("plan_draft_comparison", None)
        session.pop("plan_registration", None)
        session.pop("plan_review", None)
        session.pop("plan_launch_prep", None)
        session.pop("plan_gate_request", None)
        session.pop("plan_gate_resolution", None)
        session.pop("plan_execution_brief", None)
        session.pop("plan_enqueue_handoff", None)
        session.pop("plan_workload_binding", None)
        session.pop("plan_enqueue_run", None)
        session.pop("plan_runtime_start", None)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "reselect"}
        message_preview = render_project_selection_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_selection_context(session))
    elif stage == "project_selection":
        candidate = selected_candidate_for_text(session, normalized)
        if not candidate:
            agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
            candidate = workspace_candidate_for_text(
                args,
                text=normalized,
                request_text=str(session.get("request_text") or ""),
                agent_intent=agent_intent,
            )
        if candidate:
            session["stage"] = "project_selected"
            session["selected_candidate"] = candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "selected_by_search"
                if candidate.get("resolved_by") == "workspace_search"
                else "selected",
                "selected_project_key": candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            manual_candidate = manual_project_candidate(normalized)
            session["stage"] = "project_manual_input"
            session["selected_candidate"] = manual_candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "manual_input",
                "selected_project_key": manual_candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage == "project_path_required":
        resolved = resolve_manual_project_path(args, normalized)
        if resolved:
            candidate = candidate_from_manual_path(args, resolved, normalized)
            session["stage"] = "project_selected"
            session["selected_candidate"] = candidate
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "path_confirmed",
                "selected_project_key": candidate.get("project_key"),
            }
            message_preview = render_project_selected_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        elif remote_plan_search_request_text(normalized):
            selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
            agent_intent = session.get("agent_intent") if isinstance(session.get("agent_intent"), dict) else None
            search_text = " ".join(
                [
                    str(selected.get("display_name") or selected.get("project_key") or ""),
                    str(session.get("request_text") or ""),
                ]
            )
            candidate = workspace_candidate_for_text(
                args,
                text=search_text,
                request_text=str(session.get("request_text") or ""),
                agent_intent=agent_intent,
            )
            if candidate:
                session["stage"] = "project_selected"
                session["selected_candidate"] = candidate
                store_remote_plan_session(state, chat_hash, session)
                result["parsed_command"] = {
                    **parsed,
                    "selection_status": "path_resolved_by_search",
                    "selected_project_key": candidate.get("project_key"),
                }
                message_preview = render_project_selected_message(profile=args.profile, session=session)
                attach_choice_surface(result, remote_plan_init_context(session))
            else:
                append_remote_plan_note(session, normalized)
                store_remote_plan_session(state, chat_hash, session)
                result["parsed_command"] = {**parsed, "selection_status": "path_unresolved"}
                message_preview = render_project_path_required_message(profile=args.profile, session=session)
                attach_choice_surface(result, remote_plan_init_context(session))
        else:
            append_remote_plan_note(session, normalized)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "path_unresolved"}
            message_preview = render_project_path_required_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif project_init_create_text(normalized):
        selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
        if stage != "project_init_previewed" or not isinstance(session.get("project_init_preview"), dict):
            result["parsed_command"] = {**parsed, "selection_status": "preview_required"}
            message_preview = render_project_init_preview_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            run = run_project_init_packet(args, session=session, candidate=selected)
            session["project_init_run"] = run
            if run.get("status") == "created":
                session["stage"] = "project_init_created"
                message_preview = render_project_init_created_message(profile=args.profile, session=session)
                selection_status = "init_created"
            else:
                session["stage"] = "project_init_failed"
                message_preview = render_project_init_failed_message(profile=args.profile, session=session)
                selection_status = "init_failed"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": selected.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif plan_launch_prep_create_text(normalized):
        review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
        if stage not in {"plan_review_approved", "plan_launch_prep_failed"} or review.get("status") != "approved":
            result["parsed_command"] = {**parsed, "selection_status": "plan_review_required"}
            message_preview = render_plan_launch_prep_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            launch_prep = prepare_plan_launch_packet(args, session=session, review=review)
            session["plan_launch_prep"] = launch_prep
            if launch_prep.get("status") == "prepared":
                session["stage"] = "plan_launch_prep_prepared"
                message_preview = render_plan_launch_prep_prepared_message(profile=args.profile, session=session)
                selection_status = "plan_launch_prep_prepared"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_launch_prep_failed"
                message_preview = render_plan_launch_prep_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_launch_prep_stale" if launch_prep.get("status") == "stale" else "plan_launch_prep_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": launch_prep.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif execution_brief_create_text(normalized):
        gate_resolution = (
            session.get("plan_gate_resolution")
            if isinstance(session.get("plan_gate_resolution"), dict)
            else {}
        )
        if stage not in {"plan_gate_approved", "plan_execution_brief_failed"} or gate_resolution.get("status") != "approved":
            result["parsed_command"] = {**parsed, "selection_status": "gate_approval_required"}
            message_preview = render_plan_execution_brief_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            brief = create_execution_brief_from_gate_resolution(
                args,
                session=session,
                gate_resolution=gate_resolution,
            )
            session["plan_execution_brief"] = brief
            if brief.get("status") == "created":
                session["stage"] = "plan_execution_brief_created"
                message_preview = render_plan_execution_brief_created_message(profile=args.profile, session=session)
                selection_status = "plan_execution_brief_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_execution_brief_failed"
                message_preview = render_plan_execution_brief_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_execution_brief_stale"
                    if brief.get("status") == "stale"
                    else "plan_execution_brief_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": brief.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif enqueue_handoff_create_text(normalized):
        execution_brief = (
            session.get("plan_execution_brief")
            if isinstance(session.get("plan_execution_brief"), dict)
            else {}
        )
        if stage not in {"plan_execution_brief_created", "plan_enqueue_handoff_failed"} or execution_brief.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "execution_brief_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            handoff = create_enqueue_handoff_from_execution_brief(
                args,
                session=session,
                execution_brief_receipt=execution_brief,
            )
            session["plan_enqueue_handoff"] = handoff
            if handoff.get("status") == "created":
                session["stage"] = "plan_enqueue_handoff_created"
                message_preview = render_plan_enqueue_handoff_created_message(profile=args.profile, session=session)
                selection_status = "plan_enqueue_handoff_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_enqueue_handoff_failed"
                message_preview = render_plan_enqueue_handoff_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_enqueue_handoff_stale"
                    if handoff.get("status") == "stale"
                    else "plan_enqueue_handoff_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": handoff.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif enqueue_run_create_text(normalized):
        workload_binding = (
            session.get("plan_workload_binding")
            if isinstance(session.get("plan_workload_binding"), dict)
            else {}
        )
        if stage not in {"plan_workload_bound", "plan_enqueue_run_failed"} or workload_binding.get("status") != "bound":
            result["parsed_command"] = {**parsed, "selection_status": "workload_binding_required"}
            message_preview = render_plan_enqueue_run_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            enqueue_run = create_enqueue_run_from_workload_binding(
                args,
                session=session,
                workload_binding=workload_binding,
            )
            session["plan_enqueue_run"] = enqueue_run
            if enqueue_run.get("status") == "queued":
                session["stage"] = "plan_enqueued"
                message_preview = render_plan_enqueue_run_done_message(profile=args.profile, session=session)
                selection_status = "plan_enqueued"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_enqueue_run_failed"
                message_preview = render_plan_enqueue_run_failed_message(profile=args.profile, session=session)
                selection_status = "plan_enqueue_run_blocked" if enqueue_run.get("status") == "blocked" else "plan_enqueue_run_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": enqueue_run.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif runtime_start_create_text(normalized):
        enqueue_run = (
            session.get("plan_enqueue_run")
            if isinstance(session.get("plan_enqueue_run"), dict)
            else {}
        )
        if stage not in {"plan_enqueued", "plan_runtime_start_failed"} or enqueue_run.get("status") != "queued":
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_run_required"}
            message_preview = render_plan_runtime_start_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            runtime_start = create_runtime_start_from_enqueue_run(
                args,
                session=session,
                enqueue_run=enqueue_run,
            )
            session["plan_runtime_start"] = runtime_start
            if runtime_start.get("status") == "launched":
                session["stage"] = "plan_runtime_started"
                message_preview = render_plan_runtime_started_message(profile=args.profile, session=session)
                selection_status = "plan_runtime_started"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_runtime_start_failed"
                message_preview = render_plan_runtime_start_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_runtime_start_blocked"
                    if runtime_start.get("status") in {"blocked", "not_started"}
                    else "plan_runtime_start_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": runtime_start.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif runtime_monitor_text(normalized):
        runtime_start = (
            session.get("plan_runtime_start")
            if isinstance(session.get("plan_runtime_start"), dict)
            else {}
        )
        if stage not in {"plan_runtime_started", "plan_runtime_monitored", "plan_runtime_monitor_failed", "plan_closeout_packet_created"} or runtime_start.get("status") != "launched":
            result["parsed_command"] = {**parsed, "selection_status": "runtime_start_required"}
            message_preview = render_plan_runtime_monitor_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            runtime_monitor = create_runtime_monitor_from_runtime_start(
                args,
                session=session,
                runtime_start=runtime_start,
            )
            session["plan_runtime_monitor"] = runtime_monitor
            if runtime_monitor.get("status") in {"running", "completed", "failed", "resume_pending", "observed"}:
                session["stage"] = "plan_runtime_monitored"
                message_preview = render_plan_runtime_monitor_message(profile=args.profile, session=session)
                selection_status = "plan_runtime_monitored"
            else:
                session["stage"] = "plan_runtime_monitor_failed"
                message_preview = render_plan_runtime_monitor_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_runtime_monitor_blocked"
                    if runtime_monitor.get("status") == "blocked"
                    else "plan_runtime_monitor_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": runtime_monitor.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_packet_create_text(normalized):
        runtime_monitor = (
            session.get("plan_runtime_monitor")
            if isinstance(session.get("plan_runtime_monitor"), dict)
            else {}
        )
        if stage not in {"plan_runtime_monitored", "plan_closeout_packet_failed"} or runtime_monitor.get("task_status") != "completed":
            result["parsed_command"] = {**parsed, "selection_status": "runtime_completion_required"}
            message_preview = render_plan_closeout_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            closeout_packet = create_closeout_packet_from_runtime_monitor(
                args,
                session=session,
                runtime_monitor=runtime_monitor,
            )
            session["plan_closeout_packet"] = closeout_packet
            if closeout_packet.get("status") == "created":
                session["stage"] = "plan_closeout_packet_created"
                message_preview = render_plan_closeout_packet_message(profile=args.profile, session=session)
                selection_status = "plan_closeout_packet_created"
            else:
                session["stage"] = "plan_closeout_packet_failed"
                message_preview = render_plan_closeout_packet_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_closeout_packet_blocked"
                    if closeout_packet.get("status") == "blocked"
                    else "plan_closeout_packet_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": closeout_packet.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_review_handoff_create_text(normalized):
        closeout_packet = (
            session.get("plan_closeout_packet")
            if isinstance(session.get("plan_closeout_packet"), dict)
            else {}
        )
        if stage not in {"plan_closeout_packet_created", "plan_closeout_review_handoff_failed"} or closeout_packet.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "closeout_packet_required"}
            message_preview = render_plan_closeout_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            handoff = create_closeout_review_handoff_from_packet(
                args,
                session=session,
                closeout_packet=closeout_packet,
            )
            session["plan_closeout_review_handoff"] = handoff
            if handoff.get("status") == "created":
                session["stage"] = "plan_closeout_review_handoff_created"
                message_preview = render_plan_closeout_review_handoff_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = "plan_closeout_review_handoff_created"
            else:
                session["stage"] = "plan_closeout_review_handoff_failed"
                message_preview = render_plan_closeout_review_handoff_failed_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = (
                    "plan_closeout_review_handoff_blocked"
                    if handoff.get("status") == "blocked"
                    else "plan_closeout_review_handoff_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": handoff.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif closeout_verdict_request(normalized)[0] is not None:
        requested_verdict, requested_note = closeout_verdict_request(normalized)
        handoff = (
            session.get("plan_closeout_review_handoff")
            if isinstance(session.get("plan_closeout_review_handoff"), dict)
            else {}
        )
        if stage not in {"plan_closeout_review_handoff_created", "plan_closeout_verdict_failed"} or handoff.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "closeout_review_handoff_required"}
            message_preview = render_plan_closeout_review_handoff_failed_message(
                profile=args.profile,
                session={
                    **session,
                    "plan_closeout_review_handoff": {
                        "error": "마무리 검토 준비가 먼저 필요합니다.",
                    },
                },
            )
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            closeout_verdict = create_closeout_verdict_from_handoff(
                args,
                session=session,
                handoff=handoff,
                verdict=str(requested_verdict or ""),
                note=requested_note,
            )
            session["plan_closeout_verdict"] = closeout_verdict
            if closeout_verdict.get("status") == "recorded":
                session["stage"] = "plan_closeout_verdict_recorded"
                message_preview = render_plan_closeout_verdict_message(profile=args.profile, session=session)
                selection_status = "plan_closeout_verdict_recorded"
            else:
                session["stage"] = "plan_closeout_verdict_failed"
                message_preview = render_plan_closeout_verdict_failed_message(
                    profile=args.profile,
                    session=session,
                )
                selection_status = (
                    "plan_closeout_verdict_blocked"
                    if closeout_verdict.get("status") == "blocked"
                    else "plan_closeout_verdict_failed"
                )
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": closeout_verdict.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif workload_binding_request_text(normalized):
        handoff = (
            session.get("plan_enqueue_handoff")
            if isinstance(session.get("plan_enqueue_handoff"), dict)
            else {}
        )
        if stage not in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"} or handoff.get("status") != "created":
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_handoff_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            session["stage"] = "plan_workload_path_required"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "workload_path_required"}
            message_preview = render_plan_workload_path_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage in {"plan_enqueue_handoff_created", "plan_workload_path_required", "plan_workload_binding_failed"} and resolve_prepared_task_path(normalized):
        handoff = (
            session.get("plan_enqueue_handoff")
            if isinstance(session.get("plan_enqueue_handoff"), dict)
            else {}
        )
        manifest_path = resolve_prepared_task_path(normalized)
        if handoff.get("status") != "created" or manifest_path is None:
            result["parsed_command"] = {**parsed, "selection_status": "enqueue_handoff_required"}
            message_preview = render_plan_enqueue_handoff_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            binding = bind_prepared_workload_to_execution_brief(
                args,
                session=session,
                enqueue_handoff=handoff,
                manifest_path=manifest_path,
            )
            session["plan_workload_binding"] = binding
            if binding.get("status") == "bound":
                session["stage"] = "plan_workload_bound"
                message_preview = render_plan_workload_bound_message(profile=args.profile, session=session)
                selection_status = "plan_workload_bound"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_workload_binding_failed"
                message_preview = render_plan_workload_binding_failed_message(profile=args.profile, session=session)
                selection_status = "plan_workload_binding_blocked" if binding.get("status") == "blocked" else "plan_workload_binding_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": binding.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif gate_approval_decision is not None:
        gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
        expected_gate_token = gate_resolution_action_token(session, gate_request)
        if (
            stage not in {"plan_gate_request_created", "plan_gate_resolution_failed"}
            or gate_request.get("status") != "pending_approval"
        ):
            result["parsed_command"] = {**parsed, "selection_status": "gate_request_required"}
            message_preview = render_plan_gate_resolution_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        elif not gate_approval_token or gate_approval_token != expected_gate_token:
            result["parsed_command"] = {
                **parsed,
                "selection_status": "plan_gate_observed_hash_required",
                "gate_decision": "approved" if gate_approval_decision else "denied",
            }
            message_preview = render_plan_gate_resolution_hash_required_message(
                profile=args.profile
            )
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            resolution = resolve_gate_approval(
                args,
                session=session,
                gate_request=gate_request,
                approve=gate_approval_decision,
                received_action_token=gate_approval_token,
            )
            session["plan_gate_resolution"] = resolution
            if resolution.get("status") in {"approved", "denied"}:
                session["stage"] = "plan_gate_approved" if resolution.get("status") == "approved" else "plan_gate_denied"
                message_preview = render_plan_gate_resolution_done_message(profile=args.profile, session=session)
                selection_status = f"plan_gate_{resolution.get('status')}"
                choice_context = remote_plan_init_context(session) if resolution.get("status") == "approved" else None
            else:
                session["stage"] = "plan_gate_resolution_failed"
                message_preview = render_plan_gate_resolution_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_gate_resolution_stale"
                    if resolution.get("status") == "stale"
                    else "plan_gate_resolution_failed"
                )
                choice_context = None
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": resolution.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_gate_request_text(normalized):
        launch_prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
        if stage not in {"plan_launch_prep_prepared", "plan_gate_request_failed"} or launch_prep.get("status") != "prepared":
            result["parsed_command"] = {**parsed, "selection_status": "launch_prep_required"}
            message_preview = render_plan_gate_request_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            gate_request = request_gate_for_launch_prep(args, session=session, launch_prep=launch_prep)
            session["plan_gate_request"] = gate_request
            if gate_request.get("status") == "pending_approval":
                session["stage"] = "plan_gate_request_created"
                message_preview = render_plan_gate_request_created_message(profile=args.profile, session=session)
                selection_status = "plan_gate_request_created"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_gate_request_failed"
                message_preview = render_plan_gate_request_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_gate_request_stale" if gate_request.get("status") == "stale" else "plan_gate_request_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": gate_request.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_review_decision is not None:
        registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
        if stage not in {"plan_registered", "plan_review_failed"} or registration.get("status") != "registered":
            result["parsed_command"] = {**parsed, "selection_status": "plan_registration_required"}
            message_preview = render_plan_review_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        elif not plan_review_token:
            result["parsed_command"] = {
                **parsed,
                "selection_status": "plan_review_observed_hash_required",
                "review_decision": plan_review_decision,
            }
            message_preview = render_plan_review_hash_required_message(
                profile=args.profile
            )
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            review = review_registered_plan(
                args,
                session=session,
                registration=registration,
                decision=plan_review_decision,
                received_action_token=plan_review_token,
            )
            session["plan_review"] = review
            if review.get("status") == "approved":
                session["stage"] = "plan_review_approved"
                message_preview = render_plan_review_approved_message(profile=args.profile, session=session)
                selection_status = "plan_review_approved"
                choice_context = remote_plan_init_context(session)
            elif review.get("status") == "revision_required":
                session["stage"] = "plan_review_revision_required"
                message_preview = render_plan_review_revision_required_message(
                    profile=args.profile, session=session
                )
                selection_status = "plan_review_revision_required"
                choice_context = remote_plan_init_context(session)
            elif review.get("status") == "rejected":
                session["stage"] = "plan_review_rejected"
                message_preview = render_plan_review_rejected_message(
                    profile=args.profile, session=session
                )
                selection_status = "plan_review_rejected"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_review_failed"
                message_preview = render_plan_review_failed_message(profile=args.profile, session=session)
                selection_status = (
                    "plan_review_stale" if review.get("status") == "stale" else "plan_review_failed"
                )
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "review_decision": plan_review_decision,
                "selected_project_key": review.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif stage in {"plan_drafts_compared", "plan_draft_selection_failed"} and selected_plan_draft:
        draft = select_plan_draft(
            args,
            session=session,
            comparison=plan_draft_comparison,
            candidate=selected_plan_draft,
        )
        session["plan_draft"] = draft
        if draft.get("status") == "selected":
            session["stage"] = "plan_draft_validated"
            message_preview = render_plan_draft_selected_message(
                profile=args.profile, session=session
            )
            selection_status = "plan_draft_selected"
        else:
            session["stage"] = "plan_draft_selection_failed"
            message_preview = render_plan_draft_failed_message(
                profile=args.profile, session=session
            )
            selection_status = (
                "plan_draft_selection_stale"
                if draft.get("status") == "stale"
                else "plan_draft_selection_failed"
            )
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {
            **parsed,
            "selection_status": selection_status,
            "selected_project_key": draft.get("project_key"),
            "selected_strategy_key": draft.get("strategy_key"),
        }
        attach_choice_surface(result, remote_plan_init_context(session))
    elif plan_registration_create_text(normalized):
        draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
        if stage != "plan_draft_validated" or draft.get("status") not in {
            "validated",
            "selected",
        }:
            result["parsed_command"] = {**parsed, "selection_status": "plan_draft_required"}
            message_preview = render_plan_registration_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            registration = register_plan_draft(args, session=session, draft=draft)
            session["plan_registration"] = registration
            if registration.get("status") == "registered":
                session["stage"] = "plan_registered"
                message_preview = render_plan_registered_message(profile=args.profile, session=session)
                selection_status = "plan_registered"
                choice_context = remote_plan_init_context(session)
            elif registration.get("status") == "stale":
                session["stage"] = "plan_registration_failed"
                message_preview = render_plan_registration_stale_message(profile=args.profile)
                selection_status = "plan_registration_stale"
                choice_context = remote_plan_init_context(session)
            else:
                session["stage"] = "plan_registration_failed"
                message_preview = render_plan_registration_failed_message(profile=args.profile, session=session)
                selection_status = "plan_registration_failed"
                choice_context = remote_plan_init_context(session)
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": registration.get("project_key"),
            }
            attach_choice_surface(result, choice_context)
    elif plan_draft_create_text(normalized):
        init_run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
        if init_run.get("status") != "created" or not isinstance(init_run.get("project_init_output"), dict):
            result["parsed_command"] = {**parsed, "selection_status": "init_required"}
            message_preview = render_plan_draft_required_message(profile=args.profile)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            comparison = create_plan_draft(
                args, session=session, init_run=init_run
            )
            session["plan_draft_comparison"] = comparison
            session.pop("plan_draft", None)
            if comparison.get("status") == "compared":
                session["stage"] = "plan_drafts_compared"
                message_preview = render_plan_draft_compared_message(
                    profile=args.profile, session=session
                )
                selection_status = "plan_drafts_compared"
            else:
                session["stage"] = "plan_draft_failed"
                message_preview = render_plan_draft_failed_message(profile=args.profile, session=session)
                selection_status = "plan_draft_failed"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": selection_status,
                "selected_project_key": init_run.get("project_key"),
            }
            attach_choice_surface(result, remote_plan_init_context(session))
    elif remote_plan_init_review_text(normalized):
        selected = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
        if selected.get("manual_input") and not selected.get("workspace_path"):
            session["stage"] = "project_path_required"
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {**parsed, "selection_status": "path_required"}
            message_preview = render_project_path_required_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
        else:
            preview = create_project_init_preview(args, session=session, candidate=selected)
            session["stage"] = "project_init_previewed"
            session["project_init_preview"] = preview
            store_remote_plan_session(state, chat_hash, session)
            result["parsed_command"] = {
                **parsed,
                "selection_status": "init_previewed",
                "selected_project_key": selected.get("project_key"),
            }
            message_preview = render_project_init_preview_message(profile=args.profile, session=session)
            attach_choice_surface(result, remote_plan_init_context(session))
    elif stage == "plan_workload_path_required":
        append_remote_plan_note(session, normalized)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "workload_path_unresolved"}
        message_preview = render_plan_workload_path_required_message(profile=args.profile)
        attach_choice_surface(result, remote_plan_init_context(session))
    else:
        append_remote_plan_note(session, normalized)
        store_remote_plan_session(state, chat_hash, session)
        result["parsed_command"] = {**parsed, "selection_status": "note_added"}
        message_preview = render_remote_plan_note_message(profile=args.profile, session=session)
        attach_choice_surface(result, remote_plan_init_context(session))
    result.update(
        {
            "status": "rendered",
            "projection": None,
            "remote_plan_session": public_remote_plan_session(session),
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result
