"""Plan-session stage receipt and packet builders for the Telegram operator.

These functions construct the canonical stage artifacts (plan draft, launch
prep, execution brief, enqueue handoff, workload binding, runtime start/monitor,
closeout packet/review/verdict) and the project-init packet. Some run the
forager CLI as a subprocess to validate or produce receipts. They hold no
listener state; the plan-session state machine calls them and threads the
results back into the session.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
from typing import Any

from .common import load_json, utc_now, write_json
from .health import action_readiness, readiness_from_agent_intent
from .project_candidates import (
    REPO_ROOT,
    is_git_repo,
    project_marker_names,
    scan_project_candidates,
)
from .rendering import sanitize_text
from .schemas import (
    PLAN_CLOSEOUT_PACKET_SCHEMA,
    PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
    PLAN_CLOSEOUT_VERDICT_SCHEMA,
    PLAN_DRAFT_AUTHORITY_DENIALS,
    PLAN_DRAFT_SCHEMA,
    PLAN_ENQUEUE_HANDOFF_SCHEMA,
    PLAN_ENQUEUE_RUN_SCHEMA,
    PLAN_EXECUTION_BRIEF_SCHEMA,
    PLAN_LAUNCH_PREP_SCHEMA,
    PLAN_RUNTIME_MONITOR_SCHEMA,
    PLAN_RUNTIME_START_SCHEMA,
    PLAN_WORKLOAD_BINDING_SCHEMA,
    PROJECT_INIT_PREVIEW_SCHEMA,
    PROJECT_INIT_RUN_SCHEMA,
    REMOTE_PLAN_SESSION_SCHEMA,
)


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_id(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def contains_secret_like_text(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    markers = ("token=", "api_key=", "apikey=", "password=", "secret=")
    return any(marker in text for marker in markers) or re.search(r"\bsk-[a-z0-9]{12,}", text) is not None


def ensure_cli_option(argv: list[str], flag: str, value: str) -> list[str]:
    output = [str(item) for item in argv]
    if flag in output:
        index = output.index(flag)
        if index + 1 < len(output):
            output[index + 1] = value
        else:
            output.append(value)
    else:
        output.extend([flag, value])
    return output


def create_remote_plan_session(
    args: argparse.Namespace,
    *,
    chat_hash: str,
    request_text: str,
    parsed_command: dict[str, Any],
    feedback_context: dict[str, Any] | None,
    decision_id: Any = None,
) -> dict[str, Any]:
    agent_intent = parsed_command.get("agent_intent") if isinstance(parsed_command.get("agent_intent"), dict) else None
    build_plan_readiness = readiness_from_agent_intent(agent_intent)
    seed = f"{utc_now()}|{chat_hash}|{request_text}"
    session = {
        "schema": REMOTE_PLAN_SESSION_SCHEMA,
        "session_id": "telegram-plan-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12],
        "profile": args.profile,
        "chat_id_hash": chat_hash,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "stage": "project_selection",
        "request_text": sanitize_text(request_text, max_chars=1200),
        "feedback_kind": str(parsed_command.get("feedback_kind") or "planning_request"),
        "feedback_context": feedback_context,
        "agent_intent": agent_intent,
        "decision_feedback_decision_id": decision_id,
        "execution_authorized": False,
        "approval_authorized": False,
        "action_readiness": {
            "project_scan": action_readiness(
                "project_scan",
                "healthy",
                reason="workspace_scan_available",
                allowed_actions=["project_selection", "manual_path_check"],
            ),
            "build_plan": build_plan_readiness
            if isinstance(build_plan_readiness, dict)
            else action_readiness(
                "build_plan",
                "healthy",
                reason="agent_intent_not_required",
                allowed_actions=["project_scan", "plan_draft"],
                blocked_actions=["arbitrary_launch", "shell"],
                recovery_hint="실행은 reviewed bound task만 가능",
            ),
            "start_offdesk": action_readiness(
                "start_offdesk",
                "guarded",
                reason="reviewed_bound_task_only",
                allowed_actions=["bound_enqueue_run", "task_scoped_start", "task_scoped_monitor"],
                blocked_actions=["arbitrary_launch", "shell", "accepted_truth"],
                recovery_hint="계획 승인, 게이트, 브리프, 워크로드 binding 후 대상 task만 시작",
            ),
        },
        "candidates": scan_project_candidates(
            args,
            request_text=request_text,
            agent_intent=agent_intent,
        ),
    }
    return session


def root_marker_summary(path: pathlib.Path) -> list[str]:
    return project_marker_names(path)


def documentation_summary(path: pathlib.Path) -> list[str]:
    names = (
        "README.md",
        "README_KO.md",
        "AGENTS.md",
        "CURRENT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    )
    return [name for name in names if (path / name).exists()]


def entrypoint_summary(path: pathlib.Path) -> list[str]:
    names = ("Cargo.toml", "pyproject.toml", "package.json", "Makefile", "justfile", "uv.lock")
    return [name for name in names if (path / name).exists()]


def project_init_command_preview(args: argparse.Namespace, candidate: dict[str, Any]) -> list[str]:
    workspace_path = str(candidate.get("workspace_path") or "").strip()
    project_key = str(candidate.get("project_key") or "project").strip() or "project"
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "project",
            "init",
            workspace_path,
            "--project-key",
            project_key,
            "--include-git",
            "--json",
        ]
    )
    return command


def create_project_init_preview(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = pathlib.Path(str(candidate.get("workspace_path") or ""))
    preview = {
        "schema": PROJECT_INIT_PREVIEW_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": candidate.get("project_key"),
        "display_name": candidate.get("display_name"),
        "workspace_path": str(workspace_path),
        "workspace_path_hint": candidate.get("workspace_path_hint"),
        "path_exists": workspace_path.exists(),
        "path_is_dir": workspace_path.is_dir(),
        "root_markers": root_marker_summary(workspace_path) if workspace_path.is_dir() else [],
        "documentation_sources": documentation_summary(workspace_path) if workspace_path.is_dir() else [],
        "entrypoints": entrypoint_summary(workspace_path) if workspace_path.is_dir() else [],
        "is_git_repo": is_git_repo(workspace_path) if workspace_path.is_dir() else False,
        "dirty": candidate.get("dirty"),
        "recommended_next_command": project_init_command_preview(args, candidate)
        if workspace_path.is_dir()
        else [],
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
        "notes": [
            "This preview does not run project init.",
            "Telegram selection does not authorize launch, approval, shell, or git mutation.",
        ],
    }
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    artifact_path = artifact_dir / "PROJECT_INIT_PREVIEW.json"
    write_json(artifact_path, preview)
    preview["artifact_path"] = str(artifact_path)
    return preview


def run_project_init_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    workspace_path = pathlib.Path(str(candidate.get("workspace_path") or ""))
    command = project_init_command_preview(args, candidate)
    run = {
        "schema": PROJECT_INIT_RUN_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": candidate.get("project_key"),
        "display_name": candidate.get("display_name"),
        "workspace_path": str(workspace_path),
        "workspace_path_hint": candidate.get("workspace_path_hint"),
        "command": command,
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    artifact_path = artifact_dir / "PROJECT_INIT_RUN.json"
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.project_init_timeout_sec)),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    run["returncode"] = process.returncode
    if process.returncode != 0:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(process.stderr.strip() or process.stdout.strip(), max_chars=600),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    try:
        output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        run.update(
            {
                "status": "error",
                "error": sanitize_text(f"project init did not return JSON: {error}", max_chars=400),
                "artifact_path": str(artifact_path),
            }
        )
        write_json(artifact_path, run)
        return run
    run.update(
        {
            "status": "created",
            "project_init_output": output,
            "artifact_path": str(artifact_path),
        }
    )
    write_json(artifact_path, run)
    return run


def build_multiturn_plan_draft(session: dict[str, Any], init_run: dict[str, Any]) -> dict[str, Any]:
    output = init_run.get("project_init_output") if isinstance(init_run.get("project_init_output"), dict) else {}
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    project_key = str(output.get("project_key") or init_run.get("project_key") or "project")
    request_text = sanitize_text(session.get("request_text") or "", max_chars=500)
    plan_id = f"telegram_plan_{sha256_id(str(session.get('session_id') or '') + ':' + project_key)}"
    module_count = int(summary.get("module_candidate_count") or 0)
    evidence_count = int(summary.get("evidence_source_count") or 0)
    blocker_count = int(summary.get("module_operation_preflight_blocker_count") or 0)
    return {
        "schema": "offdesk_multiturn_plan.v1",
        "plan_id": plan_id,
        "created_at": utc_now(),
        "profile_key": "telegram_remote_plan",
        "profile_name": "Telegram Remote Plan Draft",
        "project_key": project_key,
        "source": {
            "schema": PROJECT_INIT_RUN_SCHEMA,
            "session_id": session.get("session_id"),
            "project_init_id": output.get("id"),
            "read_only_project_state": output.get("read_only_project_state") is True,
            "requires_operator_review": output.get("requires_operator_review") is not False,
        },
        "request": {
            "transport": "telegram",
            "operator_request": request_text,
        },
        "project_summary": {
            "module_candidate_count": module_count,
            "evidence_source_count": evidence_count,
            "module_operation_preflight_blocker_count": blocker_count,
            "ready_for_ondesk_start": bool(summary.get("ready_for_ondesk_start", False)),
            "ready_for_offdesk_runtime": bool(summary.get("ready_for_offdesk_runtime", False)),
        },
        "decision": {
            "ready_for_operator_review": True,
            "ready_for_launch_preparation": False,
            "ready_for_enqueue": False,
            "reason": "Telegram created a bounded draft from a project initialization packet; operator review is required before registration or launch preparation.",
        },
        "execution_sequence": [
            {
                "id": "review_initialization_packet",
                "objective": "Review the project initialization packet, first reads, candidate modules, and blockers before any runtime work.",
                "stop_condition": "Stop when the operator confirms scope, blockers, and evidence are understood.",
            },
            {
                "id": "prepare_registered_plan",
                "objective": "Convert the reviewed draft into a registered Offdesk plan only after operator review.",
                "stop_condition": "Stop at plan registration; launch preparation and runtime dispatch remain separate approvals.",
            },
        ],
        "authority": {
            "read_only_plan": True,
            "does_not_authorize": PLAN_DRAFT_AUTHORITY_DENIALS,
        },
    }


def create_plan_draft(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    init_run: dict[str, Any],
) -> dict[str, Any]:
    output = init_run.get("project_init_output") if isinstance(init_run.get("project_init_output"), dict) else {}
    project_key = str(output.get("project_key") or init_run.get("project_key") or "project")
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    plan_path = artifact_dir / "OFFDESK_PLAN_DRAFT.json"
    receipt_path = artifact_dir / "PLAN_DRAFT_VALIDATION.json"
    plan = build_multiturn_plan_draft(session, init_run)
    write_json(plan_path, plan)
    plan_sha256 = sha256_hex(plan_path.read_bytes())
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
            "--dry-run",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_DRAFT_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_artifact_path": str(plan_path),
        "plan_sha256": plan_sha256,
        "validation_command": command,
        "dry_run": True,
        "execution_authorized": False,
        "approval_authorized": False,
        "runtime_authorized": False,
    }
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_draft_timeout_sec)),
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
        validation_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"plan dry-run did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "validated",
            "validation_output": validation_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def prepare_plan_launch_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    output = review.get("review_output") if isinstance(review.get("review_output"), dict) else {}
    artifacts = output.get("artifacts") if isinstance(output.get("artifacts"), dict) else {}
    project_key = str(output.get("project_key") or review.get("project_key") or "project")
    plan_ref = str(review.get("plan_ref") or output.get("plan_id") or "").strip()
    review_id = str(output.get("review_id") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_LAUNCH_PREP.json"
    copied_source_json = str(review.get("copied_source_json") or artifacts.get("copied_source_json") or "").strip()
    expected_source_sha = str(review.get("expected_source_sha256") or output.get("source_sha256") or "").strip()
    review_record_json = str(artifacts.get("review_record_json") or output.get("review_record_json") or "").strip()
    receipt = {
        "schema": PLAN_LAUNCH_PREP_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "plan_ref": plan_ref,
        "review_id": review_id,
        "copied_source_json": copied_source_json,
        "review_record_json": review_record_json,
        "expected_source_sha256": expected_source_sha,
        "launch_preparation_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if not plan_ref or not review_id:
        receipt.update(
            {
                "status": "error",
                "error": "approved plan review id unavailable",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if copied_source_json and expected_source_sha:
        try:
            current_sha = sha256_hex(pathlib.Path(copied_source_json).read_bytes())
        except OSError as error:
            receipt.update(
                {
                    "status": "error",
                    "error": sanitize_text(f"registered plan source unavailable: {error}", max_chars=400),
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
        receipt["current_source_sha256"] = current_sha
        if current_sha != expected_source_sha:
            receipt.update(
                {
                    "status": "stale",
                    "error": "registered plan source changed after plan review",
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
            "plan-launch-prep",
            plan_ref,
            "--review-id",
            review_id,
            "--prepared-by",
            "telegram",
            "--notes",
            "Telegram operator requested a launch-preparation packet; runtime gate approval remains separate.",
            "--json",
        ]
    )
    receipt["launch_prep_command"] = command
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.plan_launch_prep_timeout_sec)),
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
        launch_prep_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"launch prep did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "prepared" if launch_prep_output.get("schema") == "offdesk_plan_launch_prep.v1" else "created",
            "launch_prep_output": launch_prep_output,
            "artifact_path": str(receipt_path),
        }
    )
    artifacts = launch_prep_output.get("artifacts") if isinstance(launch_prep_output.get("artifacts"), dict) else {}
    launch_prep_json = str(artifacts.get("launch_prep_json") or "").strip()
    if launch_prep_json:
        receipt["launch_prep_json"] = launch_prep_json
        try:
            receipt["launch_prep_sha256"] = sha256_hex(pathlib.Path(launch_prep_json).read_bytes())
        except OSError as error:
            receipt["launch_prep_hash_error"] = sanitize_text(str(error), max_chars=200)
    write_json(receipt_path, receipt)
    return receipt


def create_execution_brief_from_gate_resolution(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    gate_resolution: dict[str, Any],
) -> dict[str, Any]:
    resolution_output = (
        gate_resolution.get("resolution_output")
        if isinstance(gate_resolution.get("resolution_output"), dict)
        else {}
    )
    decision = str(gate_resolution.get("decision") or "")
    project_key = str(gate_resolution.get("project_key") or resolution_output.get("project_key") or "project")
    request_id = str(gate_resolution.get("request_id") or resolution_output.get("request_id") or session.get("session_id") or "")
    task_id = str(gate_resolution.get("task_id") or resolution_output.get("task_id") or "")
    approval_id = str(gate_resolution.get("approval_id") or resolution_output.get("approval_id") or "")
    launch_prep_json = str(gate_resolution.get("launch_prep_json") or "").strip()
    expected_launch_prep_sha = str(gate_resolution.get("expected_launch_prep_sha256") or gate_resolution.get("current_launch_prep_sha256") or "").strip()
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    brief_path = artifact_dir / "EXECUTION_BRIEF.json"
    receipt_path = artifact_dir / "PLAN_EXECUTION_BRIEF.json"
    receipt = {
        "schema": PLAN_EXECUTION_BRIEF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "approval_id": approval_id,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "launch_prep_json": launch_prep_json,
        "expected_launch_prep_sha256": expected_launch_prep_sha,
        "execution_brief_json": str(brief_path),
        "execution_brief_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if decision != "approved" or str(resolution_output.get("status") or "") != "approved":
        receipt.update(
            {
                "status": "error",
                "error": "gate approval is not approved",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not project_key or not request_id or not task_id or not approval_id:
        receipt.update(
            {
                "status": "error",
                "error": "approved gate resolution is missing execution context",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if launch_prep_json and expected_launch_prep_sha:
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
        if current_sha != expected_launch_prep_sha:
            receipt.update(
                {
                    "status": "stale",
                    "error": "launch-preparation packet changed after gate approval",
                    "artifact_path": str(receipt_path),
                }
            )
            write_json(receipt_path, receipt)
            return receipt
    fresh_until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
        minutes=max(1, int(args.execution_brief_ttl_minutes))
    )
    execution_brief = {
        "request_id": request_id,
        "task_id": task_id,
        "project_key": project_key,
        "approved": True,
        "allowed_runtime_mutations": ["dispatch.runtime"],
        "allowed_canonical_mutations": [],
        "fresh_until": fresh_until.isoformat(),
    }
    write_json(brief_path, execution_brief)
    receipt.update(
        {
            "status": "created",
            "execution_brief": execution_brief,
            "execution_brief_sha256": sha256_hex(brief_path.read_bytes()),
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_enqueue_handoff_from_execution_brief(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    execution_brief_receipt: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_ENQUEUE_HANDOFF.json"
    execution_brief_json = str(execution_brief_receipt.get("execution_brief_json") or "").strip()
    project_key = str(execution_brief_receipt.get("project_key") or "project").strip()
    request_id = str(execution_brief_receipt.get("request_id") or session.get("session_id") or "").strip()
    task_id = str(execution_brief_receipt.get("task_id") or "").strip()
    expected_brief_sha = str(execution_brief_receipt.get("execution_brief_sha256") or "").strip()
    command_template = [args.forager_bin]
    if args.profile:
        command_template.extend(["--profile", args.profile])
    command_template.extend(
        [
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            project_key,
            "--request-id",
            request_id,
            "--task-id",
            task_id,
            "--brief",
            execution_brief_json,
            "--mutation-class",
            "dispatch.runtime",
            "--cmd",
            "<reviewed-workload-command-required>",
            "--workdir",
            "<reviewed-project-workdir-required>",
            "--log-artifact",
            "<reviewed-log-artifact-required>",
            "--result-artifact",
            "<reviewed-result-artifact-required>",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_ENQUEUE_HANDOFF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "execution_brief_json": execution_brief_json,
        "expected_execution_brief_sha256": expected_brief_sha,
        "command_template": command_template,
        "prepared_workload_required": True,
        "reviewed_workload_command_required": True,
        "required_local_review": [
            "read EXECUTION_BRIEF.json",
            "confirm a reviewed workload command",
            "confirm workdir and artifacts",
            "run enqueue locally only after review",
        ],
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if execution_brief_receipt.get("status") != "created" or not execution_brief_json:
        receipt.update(
            {
                "status": "error",
                "error": "execution brief receipt is not ready",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not project_key or not request_id or not task_id:
        receipt.update(
            {
                "status": "error",
                "error": "execution brief is missing enqueue context",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        current_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
    except OSError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"execution brief unavailable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["current_execution_brief_sha256"] = current_sha
    if expected_brief_sha and current_sha != expected_brief_sha:
        receipt.update(
            {
                "status": "stale",
                "error": "execution brief changed after approval",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "created",
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def bind_prepared_workload_to_execution_brief(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    enqueue_handoff: dict[str, Any],
    manifest_path: pathlib.Path,
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_WORKLOAD_BINDING.json"
    execution_brief_json = str(enqueue_handoff.get("execution_brief_json") or "").strip()
    expected_brief_sha = str(enqueue_handoff.get("expected_execution_brief_sha256") or enqueue_handoff.get("current_execution_brief_sha256") or "").strip()
    project_key = str(enqueue_handoff.get("project_key") or "").strip()
    request_id = str(enqueue_handoff.get("request_id") or "").strip()
    task_id = str(enqueue_handoff.get("task_id") or "").strip()
    prepared_task_json = str(manifest_path.expanduser().resolve())
    receipt = {
        "schema": PLAN_WORKLOAD_BINDING_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "execution_brief_json": execution_brief_json,
        "prepared_task_json": prepared_task_json,
        "expected_execution_brief_sha256": expected_brief_sha,
        "workload_binding_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "enqueue_authorized": False,
        "runtime_authorized": False,
    }
    if enqueue_handoff.get("status") != "created":
        receipt.update(
            {
                "status": "error",
                "error": "enqueue handoff is not ready",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"prepared workload unreadable: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    if not isinstance(manifest, dict):
        receipt.update(
            {
                "status": "error",
                "error": "prepared workload manifest is not an object",
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    blockers: list[str] = []
    if manifest.get("kind") != "forager_offdesk_prepared_workload":
        blockers.append("prepared_workload_kind_mismatch")
    preflight = manifest.get("preflight") if isinstance(manifest.get("preflight"), dict) else {}
    if preflight.get("ready_for_enqueue") is not True:
        blockers.append("preflight_not_ready_for_enqueue")
    review = preflight.get("review_artifact") if isinstance(preflight.get("review_artifact"), dict) else {}
    if review and (review.get("ready") is not True or str(review.get("decision") or "") != "needs_approval"):
        blockers.append("workload_review_not_ready")
    for key, expected in (("project_key", project_key), ("request_id", request_id), ("task_id", task_id)):
        actual = str(manifest.get(key) or "").strip()
        if not expected or actual != expected:
            blockers.append(f"{key}_mismatch")
    safety = manifest.get("safety") if isinstance(manifest.get("safety"), dict) else {}
    if safety.get("capability") != "dispatch.runtime":
        blockers.append("capability_not_dispatch_runtime")
    if safety.get("approval_required_before_dispatch") is not True:
        blockers.append("dispatch_approval_not_required")
    workload_command = manifest.get("workload_command")
    if not isinstance(workload_command, list) or not workload_command:
        blockers.append("workload_command_missing")
    enqueue_args = manifest.get("enqueue_args")
    if not isinstance(enqueue_args, list) or not enqueue_args:
        blockers.append("enqueue_args_missing")
        enqueue_args = []
    else:
        enqueue_text = " ".join(str(item) for item in enqueue_args)
        if "dispatch.runtime" not in enqueue_text:
            blockers.append("enqueue_missing_dispatch_runtime")
        if "--cmd" not in [str(item) for item in enqueue_args]:
            blockers.append("enqueue_missing_workload_command")
    repo = str(manifest.get("repo") or "").strip()
    out_dir = str(manifest.get("out_dir") or "").strip()
    workload_wrapper = str(manifest.get("workload_wrapper") or "").strip()
    if repo and not pathlib.Path(repo).exists():
        blockers.append("repo_path_missing")
    if out_dir and not pathlib.Path(out_dir).exists():
        blockers.append("out_dir_missing")
    if not workload_wrapper or not pathlib.Path(workload_wrapper).exists():
        blockers.append("workload_wrapper_missing")
    if contains_secret_like_text(manifest):
        blockers.append("manifest_contains_secret_like_text")
    if execution_brief_json:
        try:
            current_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_sha
            if expected_brief_sha and current_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    else:
        blockers.append("execution_brief_missing")
    receipt["prepared_task_sha256"] = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
    receipt["manifest_summary"] = {
        "title": sanitize_text(manifest.get("title") or "", max_chars=120),
        "project_key": manifest.get("project_key"),
        "request_id": manifest.get("request_id"),
        "task_id": manifest.get("task_id"),
        "duration_minutes": manifest.get("duration_minutes"),
        "max_iterations": manifest.get("max_iterations"),
        "provider": manifest.get("provider"),
        "model": manifest.get("model"),
        "repo": repo,
        "out_dir": out_dir,
        "workload_wrapper": workload_wrapper,
    }
    receipt["repo"] = repo
    receipt["out_dir"] = out_dir
    receipt["workload_wrapper"] = workload_wrapper
    receipt["manifest_enqueue_args"] = [str(item) for item in enqueue_args]
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    bound_enqueue_args = [str(item) for item in enqueue_args]
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--project-key", project_key)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--request-id", request_id)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--task-id", task_id)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--brief", execution_brief_json)
    bound_enqueue_args = ensure_cli_option(bound_enqueue_args, "--mutation-class", "dispatch.runtime")
    if "--json" not in bound_enqueue_args:
        bound_enqueue_args.append("--json")
    receipt.update(
        {
            "status": "bound",
            "ready_for_local_enqueue_review": True,
            "bound_enqueue_args": bound_enqueue_args,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def command_contains_subsequence(command: list[str], subsequence: list[str]) -> bool:
    if not subsequence:
        return True
    values = [str(item) for item in command]
    limit = len(values) - len(subsequence) + 1
    for index in range(max(0, limit)):
        if values[index : index + len(subsequence)] == subsequence:
            return True
    return False


def create_enqueue_run_from_workload_binding(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    workload_binding: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_ENQUEUE_RUN.json"
    command = workload_binding.get("bound_enqueue_args")
    if not isinstance(command, list):
        command = []
    command = [str(item) for item in command]
    prepared_task_json = str(workload_binding.get("prepared_task_json") or "").strip()
    execution_brief_json = str(workload_binding.get("execution_brief_json") or "").strip()
    project_key = str(workload_binding.get("project_key") or "").strip()
    request_id = str(workload_binding.get("request_id") or "").strip()
    task_id = str(workload_binding.get("task_id") or "").strip()
    receipt = {
        "schema": PLAN_ENQUEUE_RUN_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "prepared_task_json": prepared_task_json,
        "execution_brief_json": execution_brief_json,
        "workload_binding_json": str(workload_binding.get("artifact_path") or ""),
        "enqueue_command": command,
        "queue_mutation_authorized": True,
        "enqueue_authorized": True,
        "approval_authorized": False,
        "gate_approval_authorized": False,
        "execution_authorized": False,
        "launch_authorized": False,
        "runtime_authorized": False,
    }
    blockers: list[str] = []
    if workload_binding.get("status") != "bound":
        blockers.append("workload_binding_not_bound")
    if not command:
        blockers.append("enqueue_command_missing")
    elif not command_contains_subsequence(command, ["offdesk", "enqueue", "dispatch.runtime"]):
        blockers.append("enqueue_command_not_dispatch_runtime")
    forbidden_tokens = {"launch", "tick", "poll", "closeout", "ok", "cancel"}
    if any(str(item) in forbidden_tokens for item in command):
        blockers.append("enqueue_command_contains_forbidden_action")
    expected_prepared_sha = str(workload_binding.get("prepared_task_sha256") or "").strip()
    if prepared_task_json:
        try:
            current_prepared_sha = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
        except OSError as error:
            blockers.append("prepared_task_unavailable")
            receipt["prepared_task_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_prepared_task_sha256"] = current_prepared_sha
            if expected_prepared_sha and current_prepared_sha != expected_prepared_sha:
                blockers.append("prepared_task_changed")
    else:
        blockers.append("prepared_task_missing")
    expected_brief_sha = str(workload_binding.get("expected_execution_brief_sha256") or workload_binding.get("current_execution_brief_sha256") or "").strip()
    if execution_brief_json:
        try:
            current_brief_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_brief_sha
            if expected_brief_sha and current_brief_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    else:
        blockers.append("execution_brief_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.enqueue_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
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
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        enqueue_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"enqueue did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "queue_mutation_authorized": False,
                "enqueue_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "queued",
            "enqueue_output": enqueue_output,
            "task_status": enqueue_output.get("status") if isinstance(enqueue_output, dict) else None,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_runtime_start_from_enqueue_run(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    enqueue_run: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_RUNTIME_START.json"
    project_key = str(enqueue_run.get("project_key") or "").strip()
    request_id = str(enqueue_run.get("request_id") or "").strip()
    task_id = str(enqueue_run.get("task_id") or "").strip()
    prepared_task_json = str(enqueue_run.get("prepared_task_json") or "").strip()
    execution_brief_json = str(enqueue_run.get("execution_brief_json") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "tick",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--limit",
            "1",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_RUNTIME_START_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "prepared_task_json": prepared_task_json,
        "execution_brief_json": execution_brief_json,
        "enqueue_run_json": str(enqueue_run.get("artifact_path") or ""),
        "tick_command": command,
        "runtime_start_authorized": True,
        "tick_authorized": True,
        "execution_authorized": True,
        "closeout_authorized": False,
        "accepted_truth_authorized": False,
    }
    blockers: list[str] = []
    if enqueue_run.get("status") != "queued":
        blockers.append("enqueue_run_not_queued")
    if not project_key or not task_id:
        blockers.append("runtime_start_context_missing")
    expected_prepared_sha = str(enqueue_run.get("current_prepared_task_sha256") or "").strip()
    if prepared_task_json:
        try:
            current_prepared_sha = sha256_hex(pathlib.Path(prepared_task_json).read_bytes())
        except OSError as error:
            blockers.append("prepared_task_unavailable")
            receipt["prepared_task_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_prepared_task_sha256"] = current_prepared_sha
            if expected_prepared_sha and current_prepared_sha != expected_prepared_sha:
                blockers.append("prepared_task_changed")
    expected_brief_sha = str(enqueue_run.get("current_execution_brief_sha256") or "").strip()
    if execution_brief_json:
        try:
            current_brief_sha = sha256_hex(pathlib.Path(execution_brief_json).read_bytes())
        except OSError as error:
            blockers.append("execution_brief_unavailable")
            receipt["execution_brief_error"] = sanitize_text(str(error), max_chars=220)
        else:
            receipt["current_execution_brief_sha256"] = current_brief_sha
            if expected_brief_sha and current_brief_sha != expected_brief_sha:
                blockers.append("execution_brief_changed")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_start_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
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
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tick did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    updated_task_ids = tick_output.get("updated_task_ids") if isinstance(tick_output, dict) else []
    launched = int(tick_output.get("launched") or 0) if isinstance(tick_output, dict) else 0
    if launched <= 0 or task_id not in [str(item) for item in updated_task_ids or []]:
        receipt.update(
            {
                "status": "not_started",
                "error": "target task was not launched",
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
                "runtime_start_authorized": False,
                "tick_authorized": False,
                "execution_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "launched",
            "tick_output": tick_output,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_runtime_monitor_from_runtime_start(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    runtime_start: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_RUNTIME_MONITOR.json"
    project_key = str(runtime_start.get("project_key") or "").strip()
    request_id = str(runtime_start.get("request_id") or "").strip()
    task_id = str(runtime_start.get("task_id") or "").strip()
    tick_command = [args.forager_bin]
    tasks_command = [args.forager_bin]
    if args.profile:
        tick_command.extend(["--profile", args.profile])
        tasks_command.extend(["--profile", args.profile])
    tick_command.extend(
        [
            "offdesk",
            "tick",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--limit",
            "0",
            "--json",
        ]
    )
    tasks_command.extend(
        [
            "offdesk",
            "tasks",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_RUNTIME_MONITOR_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "runtime_start_json": str(runtime_start.get("artifact_path") or ""),
        "tick_command": tick_command,
        "tasks_command": tasks_command,
        "monitor_authorized": True,
        "poll_authorized": True,
        "dispatch_authorized": False,
        "closeout_authorized": False,
        "accepted_truth_authorized": False,
    }
    blockers: list[str] = []
    if runtime_start.get("status") != "launched":
        blockers.append("runtime_start_not_launched")
    if not project_key or not task_id:
        blockers.append("runtime_monitor_context_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_process = subprocess.run(
            tick_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_monitor_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["tick_returncode"] = tick_process.returncode
    if tick_process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(tick_process.stderr.strip() or tick_process.stdout.strip(), max_chars=600),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tick_output = json.loads(tick_process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tick did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "monitor_authorized": False,
                "poll_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tasks_process = subprocess.run(
            tasks_command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.runtime_monitor_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt["tasks_returncode"] = tasks_process.returncode
    if tasks_process.returncode != 0:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(tasks_process.stderr.strip() or tasks_process.stdout.strip(), max_chars=600),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        tasks_output = json.loads(tasks_process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"tasks did not return JSON: {error}", max_chars=400),
                "tick_output": tick_output,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    target_task = None
    if isinstance(tasks_output, list):
        for task in tasks_output:
            if isinstance(task, dict) and str(task.get("task_id") or "") == task_id:
                target_task = task
                break
    if not isinstance(target_task, dict):
        receipt.update(
            {
                "status": "error",
                "error": "target task was not found",
                "tick_output": tick_output,
                "tasks_count": len(tasks_output) if isinstance(tasks_output, list) else None,
                "artifact_path": str(receipt_path),
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    task_status = str(target_task.get("status") or "unknown")
    if task_status in {"launched", "running"}:
        monitor_status = "running"
    elif task_status == "completed":
        monitor_status = "completed"
    elif task_status == "failed":
        monitor_status = "failed"
    elif task_status == "resume_pending":
        monitor_status = "resume_pending"
    else:
        monitor_status = "observed"
    receipt.update(
        {
            "status": monitor_status,
            "task_status": task_status,
            "tick_output": tick_output,
            "target_task": target_task,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def create_closeout_packet_from_runtime_monitor(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    runtime_monitor: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_PACKET.json"
    project_key = str(runtime_monitor.get("project_key") or "").strip()
    request_id = str(runtime_monitor.get("request_id") or "").strip()
    task_id = str(runtime_monitor.get("task_id") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "closeout",
            "--project-key",
            project_key,
            "--task-id",
            task_id,
            "--dry-run",
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_CLOSEOUT_PACKET_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "runtime_monitor_json": str(runtime_monitor.get("artifact_path") or ""),
        "closeout_command": command,
        "closeout_packet_authorized": True,
        "closeout_review_authorized": False,
        "accepted_truth_authorized": False,
        "file_mutation_authorized": False,
    }
    blockers: list[str] = []
    if runtime_monitor.get("status") != "completed" or runtime_monitor.get("task_status") != "completed":
        blockers.append("runtime_monitor_not_completed")
    if not project_key or not task_id:
        blockers.append("closeout_context_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.closeout_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
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
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        closeout_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"closeout did not return JSON: {error}", max_chars=400),
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    summary = closeout_output.get("summary") if isinstance(closeout_output, dict) else {}
    tasks = closeout_output.get("tasks") if isinstance(closeout_output, dict) else []
    matched_task_ids = [
        str(task.get("task_id") or "")
        for task in tasks
        if isinstance(task, dict) and str(task.get("task_id") or "")
    ] if isinstance(tasks, list) else []
    if task_id not in matched_task_ids:
        receipt.update(
            {
                "status": "blocked",
                "error": "closeout did not include target task",
                "closeout_output": closeout_output,
                "artifact_path": str(receipt_path),
                "closeout_packet_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    open_decisions = closeout_output.get("open_decisions") if isinstance(closeout_output, dict) else []
    verification_commands = closeout_output.get("verification_commands") if isinstance(closeout_output, dict) else []
    receipt.update(
        {
            "status": "created",
            "closeout_id": closeout_output.get("closeout_id") if isinstance(closeout_output, dict) else None,
            "closeout_output": closeout_output,
            "completed_tasks": int(summary.get("completed_tasks") or 0) if isinstance(summary, dict) else 0,
            "open_decision_count": len(open_decisions) if isinstance(open_decisions, list) else 0,
            "verification_command_count": len(verification_commands) if isinstance(verification_commands, list) else 0,
            "artifact_path": str(receipt_path),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def closeout_review_artifact_dir(closeout_output: dict[str, Any]) -> str:
    artifacts = closeout_output.get("artifacts") if isinstance(closeout_output.get("artifacts"), dict) else {}
    closeout_plan = str(artifacts.get("closeout_plan_json") or "").strip()
    if not closeout_plan:
        return ""
    return str(pathlib.Path(closeout_plan).expanduser().parent)


def closeout_review_command_templates(
    args: argparse.Namespace,
    *,
    artifact_dir: str,
) -> dict[str, list[str]]:
    base = [args.forager_bin]
    if args.profile:
        base.extend(["--profile", args.profile])
    commands: dict[str, list[str]] = {}
    for verdict in ("revise", "blocked", "approved"):
        commands[verdict] = [
            *base,
            "offdesk",
            "closeout-review",
            "--artifact-dir",
            artifact_dir,
            "--verdict",
            verdict,
            "--reviewer",
            "operator",
            "--notes",
            f"<{verdict}-review-notes>",
            "--json",
        ]
    return commands


def closeout_known_followups(closeout_output: dict[str, Any]) -> dict[str, int]:
    summary = closeout_output.get("summary") if isinstance(closeout_output.get("summary"), dict) else {}
    open_decisions = closeout_output.get("open_decisions") if isinstance(closeout_output.get("open_decisions"), list) else []
    documentation = (
        closeout_output.get("documentation_governance")
        if isinstance(closeout_output.get("documentation_governance"), dict)
        else {}
    )
    followups = {
        "open_decisions": len(open_decisions),
        "missing_artifacts": int(summary.get("missing_artifacts") or 0),
        "commercial_review_operations": int(summary.get("operations_requiring_commercial_review") or 0),
        "human_approval_operations": int(summary.get("operations_requiring_human_approval") or 0),
        "archive_candidates": int(summary.get("archive_candidates") or 0),
        "delete_candidates": int(summary.get("delete_candidates") or 0),
        "documentation_recommendations": int(documentation.get("recommendation_count") or 0),
        "documentation_audit_unavailable": 1 if documentation.get("error") else 0,
    }
    return {key: value for key, value in followups.items() if value > 0}


def create_closeout_review_handoff_from_packet(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    closeout_packet: dict[str, Any],
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_REVIEW_HANDOFF.json"
    closeout_output = (
        closeout_packet.get("closeout_output")
        if isinstance(closeout_packet.get("closeout_output"), dict)
        else {}
    )
    closeout_artifact_dir = closeout_review_artifact_dir(closeout_output)
    artifacts = closeout_output.get("artifacts") if isinstance(closeout_output.get("artifacts"), dict) else {}
    closeout_plan_json = str(artifacts.get("closeout_plan_json") or "")
    return_package_markdown = str(artifacts.get("return_package_markdown") or "")
    known_followups = closeout_known_followups(closeout_output)
    known_followup_count = sum(known_followups.values())
    receipt = {
        "schema": PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": closeout_packet.get("project_key"),
        "request_id": closeout_packet.get("request_id"),
        "task_id": closeout_packet.get("task_id"),
        "closeout_id": closeout_output.get("closeout_id"),
        "closeout_packet_json": str(closeout_packet.get("artifact_path") or ""),
        "artifact_dir": closeout_artifact_dir,
        "closeout_plan_json": closeout_plan_json,
        "return_package_markdown": return_package_markdown,
        "known_followups": known_followups,
        "known_followup_count": known_followup_count,
        "approved_verdict_may_accept_truth": known_followup_count == 0,
        "closeout_review_handoff_authorized": True,
        "remote_closeout_review_authorized": False,
        "closeout_review_authorized": False,
        "accepted_truth_authorized": False,
        "file_mutation_authorized": False,
        "local_review_required": True,
        "recommended_next_action": "Run a local closeout-review verdict after reading the closeout artifacts.",
        "artifact_path": str(receipt_path),
    }
    blockers: list[str] = []
    if closeout_packet.get("status") != "created":
        blockers.append("closeout_packet_not_created")
    if not closeout_artifact_dir or not closeout_plan_json:
        blockers.append("closeout_artifact_dir_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "closeout_review_handoff_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    receipt.update(
        {
            "status": "created",
            "local_review_commands": closeout_review_command_templates(
                args,
                artifact_dir=closeout_artifact_dir,
            ),
        }
    )
    write_json(receipt_path, receipt)
    return receipt


def closeout_verdict_note(verdict: str, note: str) -> str:
    if note:
        return sanitize_text(note, max_chars=500)
    if verdict == "revise":
        return "Remote Telegram operator recorded revision-required closeout verdict; accepted truth remains blocked."
    if verdict == "approved":
        return "Remote Telegram operator recorded approved closeout verdict; accepted truth follows closeout-review receipt status."
    return "Remote Telegram operator recorded blocked closeout verdict; accepted truth remains blocked."


def create_closeout_verdict_from_handoff(
    args: argparse.Namespace,
    *,
    session: dict[str, Any],
    handoff: dict[str, Any],
    verdict: str,
    note: str,
) -> dict[str, Any]:
    artifact_dir = args.remote_plan_artifact_dir.expanduser() / str(session.get("session_id") or "session")
    receipt_path = artifact_dir / "PLAN_CLOSEOUT_VERDICT.json"
    closeout_artifact_dir = str(handoff.get("artifact_dir") or "").strip()
    command = [args.forager_bin]
    if args.profile:
        command.extend(["--profile", args.profile])
    command.extend(
        [
            "offdesk",
            "closeout-review",
            "--artifact-dir",
            closeout_artifact_dir,
            "--verdict",
            verdict,
            "--reviewer",
            "telegram-remote-operator",
            "--review-provider",
            "telegram-remote-operator",
            "--notes",
            closeout_verdict_note(verdict, note),
            "--json",
        ]
    )
    receipt = {
        "schema": PLAN_CLOSEOUT_VERDICT_SCHEMA,
        "created_at": utc_now(),
        "session_id": session.get("session_id"),
        "profile": args.profile,
        "project_key": handoff.get("project_key"),
        "request_id": handoff.get("request_id"),
        "task_id": handoff.get("task_id"),
        "closeout_id": handoff.get("closeout_id"),
        "closeout_review_handoff_json": str(handoff.get("artifact_path") or ""),
        "artifact_dir": closeout_artifact_dir,
        "verdict": verdict,
        "closeout_review_command": command,
        "remote_closeout_review_authorized": verdict in {"approved", "revise", "blocked"},
        "closeout_review_authorized": verdict in {"approved", "revise", "blocked"},
        "closeout_artifact_write_authorized": verdict in {"approved", "revise", "blocked"},
        "accepted_truth_authorized": verdict == "approved",
        "accepted_truth_recorded": False,
        "project_file_mutation_authorized": False,
        "file_mutation_authorized": False,
        "artifact_path": str(receipt_path),
    }
    blockers: list[str] = []
    if verdict not in {"approved", "revise", "blocked"}:
        blockers.append("unsupported_closeout_verdict")
    if handoff.get("status") != "created":
        blockers.append("closeout_review_handoff_not_created")
    if not closeout_artifact_dir:
        blockers.append("closeout_artifact_dir_missing")
    if blockers:
        receipt.update(
            {
                "status": "blocked",
                "blocking_reasons": blockers,
                "error": blockers[0],
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, int(args.closeout_timeout_sec)),
            cwd=REPO_ROOT,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"{type(error).__name__}: {error}", max_chars=400),
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
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
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    try:
        closeout_review_output = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        receipt.update(
            {
                "status": "error",
                "error": sanitize_text(f"closeout-review did not return JSON: {error}", max_chars=400),
                "remote_closeout_review_authorized": False,
                "closeout_review_authorized": False,
                "closeout_artifact_write_authorized": False,
            }
        )
        write_json(receipt_path, receipt)
        return receipt
    closeout_receipt = (
        closeout_review_output.get("closeout_receipt")
        if isinstance(closeout_review_output, dict)
        else {}
    )
    acceptance_status = (
        str(closeout_receipt.get("acceptance_status") or "")
        if isinstance(closeout_receipt, dict)
        else ""
    )
    receipt.update(
        {
            "status": "recorded",
            "acceptance_status": acceptance_status or "unknown",
            "accepted_truth_recorded": acceptance_status == "accepted",
            "closeout_review_output": closeout_review_output,
        }
    )
    write_json(receipt_path, receipt)
    return receipt
