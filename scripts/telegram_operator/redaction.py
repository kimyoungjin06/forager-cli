"""Public redaction projections for Telegram plan-session artifacts.

These are pure transforms: given an internal receipt/session dict, they
return the operator-safe subset sent back to Telegram. They hold no state
and perform no I/O.
"""

from __future__ import annotations

import json
from typing import Any

from .common import sha256_short
from .project_candidates import public_project_candidate


def public_remote_plan_session(session: dict[str, Any]) -> dict[str, Any]:
    public = dict(session)
    public["candidates"] = [
        public_project_candidate(candidate)
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    selected = session.get("selected_candidate")
    if isinstance(selected, dict):
        public["selected_candidate"] = public_project_candidate(selected)
    preview = session.get("project_init_preview")
    if isinstance(preview, dict):
        public["project_init_preview"] = public_project_init_preview(preview)
    run = session.get("project_init_run")
    if isinstance(run, dict):
        public["project_init_run"] = public_project_init_run(run)
    draft = session.get("plan_draft")
    if isinstance(draft, dict):
        public["plan_draft"] = public_plan_draft(draft)
    registration = session.get("plan_registration")
    if isinstance(registration, dict):
        public["plan_registration"] = public_plan_registration(registration)
    review = session.get("plan_review")
    if isinstance(review, dict):
        public["plan_review"] = public_plan_review(review)
    launch_prep = session.get("plan_launch_prep")
    if isinstance(launch_prep, dict):
        public["plan_launch_prep"] = public_plan_launch_prep(launch_prep)
    gate_request = session.get("plan_gate_request")
    if isinstance(gate_request, dict):
        public["plan_gate_request"] = public_plan_gate_request(gate_request)
    gate_resolution = session.get("plan_gate_resolution")
    if isinstance(gate_resolution, dict):
        public["plan_gate_resolution"] = public_plan_gate_resolution(gate_resolution)
    execution_brief = session.get("plan_execution_brief")
    if isinstance(execution_brief, dict):
        public["plan_execution_brief"] = public_plan_execution_brief(execution_brief)
    enqueue_handoff = session.get("plan_enqueue_handoff")
    if isinstance(enqueue_handoff, dict):
        public["plan_enqueue_handoff"] = public_plan_enqueue_handoff(enqueue_handoff)
    workload_binding = session.get("plan_workload_binding")
    if isinstance(workload_binding, dict):
        public["plan_workload_binding"] = public_plan_workload_binding(workload_binding)
    enqueue_run = session.get("plan_enqueue_run")
    if isinstance(enqueue_run, dict):
        public["plan_enqueue_run"] = public_plan_enqueue_run(enqueue_run)
    runtime_start = session.get("plan_runtime_start")
    if isinstance(runtime_start, dict):
        public["plan_runtime_start"] = public_plan_runtime_start(runtime_start)
    runtime_monitor = session.get("plan_runtime_monitor")
    if isinstance(runtime_monitor, dict):
        public["plan_runtime_monitor"] = public_plan_runtime_monitor(runtime_monitor)
    closeout_packet = session.get("plan_closeout_packet")
    if isinstance(closeout_packet, dict):
        public["plan_closeout_packet"] = public_plan_closeout_packet(closeout_packet)
    closeout_review_handoff = session.get("plan_closeout_review_handoff")
    if isinstance(closeout_review_handoff, dict):
        public["plan_closeout_review_handoff"] = public_plan_closeout_review_handoff(
            closeout_review_handoff
        )
    closeout_verdict = session.get("plan_closeout_verdict")
    if isinstance(closeout_verdict, dict):
        public["plan_closeout_verdict"] = public_plan_closeout_verdict(closeout_verdict)
    return public


def public_project_init_preview(preview: dict[str, Any]) -> dict[str, Any]:
    public = dict(preview)
    workspace_path = str(public.pop("workspace_path", "") or "")
    if workspace_path:
        public["workspace_path_hash"] = sha256_short(workspace_path)
    if "recommended_next_command" in public:
        public["recommended_next_command"] = [
            "<workspace_path>" if workspace_path and str(item) == workspace_path else str(item)
            for item in public.get("recommended_next_command", [])
        ]
    return public


def public_project_init_run(run: dict[str, Any]) -> dict[str, Any]:
    public = dict(run)
    workspace_path = str(public.pop("workspace_path", "") or "")
    if workspace_path:
        public["workspace_path_hash"] = sha256_short(workspace_path)
    command = public.get("command")
    if isinstance(command, list):
        public["command"] = [
            "<workspace_path>" if workspace_path and str(item) == workspace_path else str(item)
            for item in command
        ]
    output = public.get("project_init_output")
    if isinstance(output, dict):
        public["project_init_output"] = public_project_init_output(output)
    return public


def public_project_init_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("project_root", "artifact_dir"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    return public


def public_plan_draft(draft: dict[str, Any]) -> dict[str, Any]:
    public = dict(draft)
    plan_path = str(public.pop("plan_artifact_path", "") or "")
    if plan_path:
        public["plan_artifact_path_hash"] = sha256_short(plan_path)
    command = public.get("validation_command")
    if isinstance(command, list):
        public["validation_command"] = [
            "<plan_draft_path>" if plan_path and str(item) == plan_path else str(item)
            for item in command
        ]
    output = public.get("validation_output")
    if isinstance(output, dict):
        public["validation_output"] = public_offdesk_plan_registration_output(output)
    return public


def public_plan_registration(registration: dict[str, Any]) -> dict[str, Any]:
    public = dict(registration)
    plan_path = str(public.pop("plan_artifact_path", "") or "")
    if plan_path:
        public["plan_artifact_path_hash"] = sha256_short(plan_path)
    command = public.get("registration_command")
    if isinstance(command, list):
        public["registration_command"] = [
            "<plan_draft_path>" if plan_path and str(item) == plan_path else str(item)
            for item in command
        ]
    output = public.get("registration_output")
    if isinstance(output, dict):
        public["registration_output"] = public_offdesk_plan_registration_output(output)
    return public


def public_offdesk_plan_registration_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    source_path = str(public.pop("source_path", "") or "")
    if source_path:
        public["source_path_hash"] = sha256_short(source_path)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_review(review: dict[str, Any]) -> dict[str, Any]:
    public = dict(review)
    plan_ref = str(public.get("plan_ref") or "")
    if plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["plan_ref_hash"] = sha256_short(str(public.pop("plan_ref")))
    for key in ("registration_json", "copied_source_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("review_command")
    if isinstance(command, list) and plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["review_command"] = [
            "<plan_ref>" if str(item) == plan_ref else str(item)
            for item in command
        ]
    output = public.get("review_output")
    if isinstance(output, dict):
        public["review_output"] = public_offdesk_plan_review_output(output)
    return public


def public_offdesk_plan_review_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("registration_path", "review_file"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_launch_prep(prep: dict[str, Any]) -> dict[str, Any]:
    public = dict(prep)
    plan_ref = str(public.get("plan_ref") or "")
    if plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["plan_ref_hash"] = sha256_short(str(public.pop("plan_ref")))
    for key in ("copied_source_json", "review_record_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("launch_prep_command")
    if isinstance(command, list) and plan_ref and ("/" in plan_ref or "\\" in plan_ref):
        public["launch_prep_command"] = [
            "<plan_ref>" if str(item) == plan_ref else str(item)
            for item in command
        ]
    output = public.get("launch_prep_output")
    if isinstance(output, dict):
        public["launch_prep_output"] = public_offdesk_plan_launch_prep_output(output)
    return public


def public_offdesk_plan_launch_prep_output(output: dict[str, Any]) -> dict[str, Any]:
    public = dict(output)
    for key in ("registration_path", "source_path", "review_record_json", "selected_plan_path"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    reads = public.get("required_first_reads")
    if isinstance(reads, list):
        public["required_first_reads"] = [
            sha256_short(str(item))
            for item in reads
            if str(item or "").strip()
        ]
    artifacts = public.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            if str(value or "").strip()
            else None
            for key, value in artifacts.items()
        }
    return public


def public_plan_gate_request(gate_request: dict[str, Any]) -> dict[str, Any]:
    public = dict(gate_request)
    launch_prep_json = str(public.pop("launch_prep_json", "") or "")
    if launch_prep_json:
        public["launch_prep_json_hash"] = sha256_short(launch_prep_json)
    command = public.get("gate_command")
    if isinstance(command, list) and launch_prep_json:
        public["gate_command"] = [
            "<launch_prep_json>" if str(item) == launch_prep_json else str(item)
            for item in command
        ]
    return public


def public_plan_gate_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    public = dict(resolution)
    launch_prep_json = str(public.pop("launch_prep_json", "") or "")
    if launch_prep_json:
        public["launch_prep_json_hash"] = sha256_short(launch_prep_json)
    pending = public.get("pending_approval")
    if isinstance(pending, dict):
        public["pending_approval"] = public_approval_for_resolution(pending)
    output = public.get("resolution_output")
    if isinstance(output, dict):
        public["resolution_output"] = public_approval_for_resolution(output)
    return public


def public_approval_for_resolution(approval: dict[str, Any]) -> dict[str, Any]:
    public = dict(approval)
    metadata = public.get("metadata")
    if isinstance(metadata, dict):
        public["metadata_hash"] = sha256_short(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        public.pop("metadata", None)
    return public


def public_plan_execution_brief(brief: dict[str, Any]) -> dict[str, Any]:
    public = dict(brief)
    for key in ("execution_brief_json", "launch_prep_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    output = public.get("execution_brief")
    if isinstance(output, dict):
        public["execution_brief"] = dict(output)
    return public


def public_plan_enqueue_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    public = dict(handoff)
    execution_brief_json = str(public.pop("execution_brief_json", "") or "")
    if execution_brief_json:
        public["execution_brief_json_hash"] = sha256_short(execution_brief_json)
    command = public.get("command_template")
    if isinstance(command, list) and execution_brief_json:
        public["command_template"] = [
            "<execution_brief_json>" if str(item) == execution_brief_json else str(item)
            for item in command
        ]
    return public


def public_plan_workload_binding(binding: dict[str, Any]) -> dict[str, Any]:
    public = dict(binding)
    path_values: dict[str, str] = {}
    for key in ("prepared_task_json", "execution_brief_json", "repo", "out_dir", "workload_wrapper"):
        value = str(public.pop(key, "") or "")
        if value:
            path_values[key] = value
            public[f"{key}_hash"] = sha256_short(value)
    for key in ("bound_enqueue_args", "manifest_enqueue_args"):
        command = public.get(key)
        if isinstance(command, list):
            sanitized = []
            for item in command:
                item_text = str(item)
                for path_key, path_value in sorted(
                    path_values.items(),
                    key=lambda item: len(item[1]),
                    reverse=True,
                ):
                    if path_value and path_value in item_text:
                        item_text = item_text.replace(path_value, f"<{path_key}>")
                sanitized.append(item_text)
            public[key] = sanitized
    manifest_summary = public.get("manifest_summary")
    if isinstance(manifest_summary, dict):
        summary = dict(manifest_summary)
        for key in ("repo", "out_dir", "workload_wrapper"):
            value = str(summary.pop(key, "") or "")
            if value:
                summary[f"{key}_hash"] = sha256_short(value)
        public["manifest_summary"] = summary
    return public


def public_plan_enqueue_run(enqueue_run: dict[str, Any]) -> dict[str, Any]:
    public = dict(enqueue_run)
    for key in ("workload_binding_json", "prepared_task_json", "execution_brief_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("enqueue_command")
    if isinstance(command, list):
        public["enqueue_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("enqueue_command", None)
    output = public.get("enqueue_output")
    if isinstance(output, dict):
        public["enqueue_output"] = public_offdesk_task_view(output)
    return public


def public_plan_runtime_start(runtime_start: dict[str, Any]) -> dict[str, Any]:
    public = dict(runtime_start)
    for key in ("enqueue_run_json", "prepared_task_json", "execution_brief_json"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("tick_command")
    if isinstance(command, list):
        public["tick_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("tick_command", None)
    output = public.get("tick_output")
    if isinstance(output, dict):
        public["tick_output"] = public_tick_output(output)
    return public


def public_plan_runtime_monitor(runtime_monitor: dict[str, Any]) -> dict[str, Any]:
    public = dict(runtime_monitor)
    for key in ("runtime_start_json",):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    for key in ("tick_command", "tasks_command"):
        command = public.get(key)
        if isinstance(command, list):
            public[f"{key}_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
            public.pop(key, None)
    output = public.get("tick_output")
    if isinstance(output, dict):
        public["tick_output"] = public_tick_output(output)
    target_task = public.get("target_task")
    if isinstance(target_task, dict):
        public["target_task"] = public_offdesk_task_view(target_task)
    return public


def public_plan_closeout_packet(closeout_packet: dict[str, Any]) -> dict[str, Any]:
    public = dict(closeout_packet)
    for key in ("runtime_monitor_json",):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("closeout_command")
    if isinstance(command, list):
        public["closeout_command_hash"] = sha256_short(json.dumps(command, ensure_ascii=False, sort_keys=True))
        public.pop("closeout_command", None)
    output = public.get("closeout_output")
    if isinstance(output, dict):
            public["closeout_output"] = public_closeout_output(output)
    return public


def public_plan_closeout_review_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    public = dict(handoff)
    for key in ("closeout_packet_json", "artifact_dir", "closeout_plan_json", "return_package_markdown"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    commands = public.get("local_review_commands")
    if isinstance(commands, dict):
        public["local_review_command_hashes"] = {
            str(key): sha256_short(json.dumps(value, ensure_ascii=False, sort_keys=True))
            for key, value in commands.items()
            if isinstance(value, list)
        }
        public.pop("local_review_commands", None)
    return public


def public_plan_closeout_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    public = dict(verdict)
    for key in ("closeout_review_handoff_json", "artifact_dir"):
        value = str(public.pop(key, "") or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    command = public.get("closeout_review_command")
    if isinstance(command, list):
        public["closeout_review_command_hash"] = sha256_short(
            json.dumps(command, ensure_ascii=False, sort_keys=True)
        )
        public.pop("closeout_review_command", None)
    output = public.get("closeout_review_output")
    if isinstance(output, dict):
        public["closeout_review_output"] = public_closeout_review_output(output)
    return public


def public_closeout_review_output(output: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in (
        "review_id",
        "closeout_id",
        "verdict",
        "read_only_project_state",
        "applies_file_operations",
    ):
        if key in output:
            public[key] = output.get(key)
    receipt = output.get("closeout_receipt")
    if isinstance(receipt, dict):
        public["closeout_receipt"] = {
            key: receipt.get(key)
            for key in (
                "schema",
                "receipt_id",
                "closeout_id",
                "verdict",
                "acceptance_status",
                "evidence_status",
                "verification_status",
                "retention_review",
                "wiki_promotion_state",
                "stale_task_count",
                "next_safe_action",
            )
            if key in receipt
        }
        for key in ("open_decisions", "missing_evidence", "required_first_reads", "unsafe_operations"):
            value = receipt.get(key)
            if isinstance(value, list):
                public["closeout_receipt"][f"{key}_count"] = len(value)
    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    return public


def public_closeout_output(output: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in (
        "closeout_id",
        "dry_run",
        "operator_requested_dry_run",
        "read_only_project_state",
    ):
        if key in output:
            public[key] = output.get(key)
    for key in ("summary", "filters"):
        value = output.get(key)
        if isinstance(value, dict):
            public[key] = value
    review_contract = output.get("review_contract")
    if isinstance(review_contract, dict):
        public["review_contract"] = {
            key: review_contract.get(key)
            for key in ("provider", "required", "required_verdicts")
            if key in review_contract
        }
    artifacts = output.get("artifacts")
    if isinstance(artifacts, dict):
        public["artifacts"] = {
            key: sha256_short(str(value))
            for key, value in artifacts.items()
            if str(value or "").strip()
        }
    open_decisions = output.get("open_decisions")
    if isinstance(open_decisions, list):
        public["open_decision_count"] = len(open_decisions)
    verification_commands = output.get("verification_commands")
    if isinstance(verification_commands, list):
        public["verification_command_count"] = len(verification_commands)
    return public


def public_tick_output(output: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "expired_approvals",
        "polled_background",
        "launched",
        "pending_approval",
        "completed",
        "failed",
        "resume_pending",
        "provider_deferred",
        "provider_retargeted",
        "skipped",
        "stale_lock_replaced",
        "updated_task_ids",
    }
    return {key: value for key, value in output.items() if key in allowed}


def public_offdesk_task_view(task: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "task_id",
        "request_id",
        "project_key",
        "status",
        "capability_id",
        "runner_kind",
        "background_ticket_id",
        "attempt_count",
        "last_gate_status",
        "mutation_class",
        "artifact_kind",
        "agent_mode",
        "provider_id",
        "model",
        "preview",
        "reason",
        "next_safe_action",
    }
    public = {key: value for key, value in task.items() if key in allowed}
    for key in ("workdir", "log_artifact_path", "result_artifact_path"):
        value = str(task.get(key) or "")
        if value:
            public[f"{key}_hash"] = sha256_short(value)
    return public
