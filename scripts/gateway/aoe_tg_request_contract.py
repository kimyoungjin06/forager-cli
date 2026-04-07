#!/usr/bin/env python3
"""Canonical request-contract extraction and persistence helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shlex
import sys
from typing import Any, Dict, List, Optional

from aoe_tg_orch_roles import classify_dispatch_role_preset, normalize_role_preset
from aoe_tg_request_contract_data import (
    data_request_contract_matches,
    extract_data_request_contract,
)
from aoe_tg_request_contract_mixed import (
    extract_mixed_request_contract,
    mixed_request_contract_matches,
)
from aoe_tg_request_contract_review import (
    extract_review_request_contract,
    review_request_contract_matches,
)


REQUEST_CONTRACT_VERSION = "2026-03-30.v1"
EXECUTION_BRIEF_VERSION = "2026-04-04.v1"
BACKGROUND_RUN_TICKET_VERSION = "2026-04-04.v1"
BACKGROUND_LAUNCH_SPEC_VERSION = "2026-04-06.v1"
EXECUTION_BRIEF_STATUSES = (
    "executable",
    "underspecified",
    "infeasible",
    "partially_executable",
    "operator_decision_required",
)
BACKGROUND_RUN_STATUSES = (
    "queued",
    "dispatching",
    "running",
    "completed",
    "failed",
    "canceled",
    "stale",
)
BACKGROUND_RUNNER_TARGETS = (
    "local_background",
    "local_tmux",
    "github_runner",
    "remote_worker",
)
BACKGROUND_EXTERNAL_RUNNER_TARGETS = (
    "local_tmux",
    "github_runner",
    "remote_worker",
)
BACKGROUND_RUNNER_DEFAULT_MODES = {
    "local_background": "in_process_callback",
    "local_tmux": "tmux_session_json",
    "github_runner": "github_action_json",
    "remote_worker": "remote_worker_json",
}


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _dedupe_rows(rows: List[Any], *, limit: int = 8, text_limit: int = 160) -> List[str]:
    out: List[str] = []
    for item in rows:
        token = _trim(item, text_limit)
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit))]


def _normalize_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _sanitize_contract_fields(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        token = _trim(key, 64)
        if not token:
            continue
        if isinstance(value, dict):
            child: Dict[str, Any] = {}
            for child_key, child_value in value.items():
                child_token = _trim(child_key, 64)
                if not child_token:
                    continue
                if isinstance(child_value, bool):
                    child[child_token] = bool(child_value)
                elif isinstance(child_value, list):
                    child[child_token] = _dedupe_rows(list(child_value), limit=12, text_limit=120)
                else:
                    child[child_token] = _trim(child_value, 240)
            if child:
                out[token] = child
        elif isinstance(value, list):
            out[token] = _dedupe_rows(list(value), limit=12, text_limit=120)
        elif isinstance(value, bool):
            out[token] = bool(value)
        else:
            text = _trim(value, 240)
            if text:
                out[token] = text
    return out


def _sanitize_artifact_contracts(raw: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in raw.items():
        alias = _trim(key, 64)
        if not alias or not isinstance(value, dict):
            continue
        row: Dict[str, Any] = {}
        path = _trim(value.get("path", ""), 200)
        if path:
            row["path"] = path
        fmt = _trim(value.get("format", ""), 32)
        if fmt:
            row["format"] = fmt
        required_fields = _dedupe_rows(list(value.get("required_fields") or []), limit=20, text_limit=120)
        if required_fields:
            row["required_fields"] = required_fields
        notes = _dedupe_rows(list(value.get("acceptance_notes") or []), limit=6, text_limit=240)
        if notes:
            row["acceptance_notes"] = notes
        inference_policy = _sanitize_contract_fields(value.get("inference_policy"))
        if inference_policy:
            row["inference_policy"] = inference_policy
        if row:
            out[alias] = row
    return out


def normalize_request_contract_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    contract_type = normalize_role_preset(raw.get("contract_type") or raw.get("preset") or "general")
    status = _trim(raw.get("status", "complete"), 32).lower() or "complete"
    if status not in {"complete", "incomplete", "ambiguous"}:
        status = "complete"

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", REQUEST_CONTRACT_VERSION), 48) or REQUEST_CONTRACT_VERSION,
        "contract_type": contract_type,
        "preset": normalize_role_preset(raw.get("preset", contract_type) or contract_type),
        "status": status,
    }

    for key in ("objective", "project_key", "intent_action", "source_prompt", "summary", "approval_mode"):
        token = _trim(raw.get(key, ""), 400 if key == "source_prompt" else 240)
        if token:
            snapshot[key] = token
    if "readonly" in raw:
        snapshot["readonly"] = _normalize_bool(raw.get("readonly"), False)

    missing_fields = _dedupe_rows(list(raw.get("missing_fields") or []), limit=12, text_limit=120)
    ambiguity_notes = _dedupe_rows(list(raw.get("ambiguity_notes") or []), limit=8, text_limit=200)
    required_outputs = _dedupe_rows(list(raw.get("required_outputs") or []), limit=12, text_limit=200)
    required_evidence = _dedupe_rows(list(raw.get("required_evidence") or []), limit=12, text_limit=120)
    if missing_fields:
        snapshot["missing_fields"] = missing_fields
    if ambiguity_notes:
        snapshot["ambiguity_notes"] = ambiguity_notes
    if required_outputs:
        snapshot["required_outputs"] = required_outputs
    if required_evidence:
        snapshot["required_evidence"] = required_evidence

    fields = _sanitize_contract_fields(raw.get("fields"))
    if fields:
        snapshot["fields"] = fields

    artifact_contracts = _sanitize_artifact_contracts(raw.get("artifact_contracts"))
    if artifact_contracts:
        snapshot["artifact_contracts"] = artifact_contracts

    if not snapshot.get("summary"):
        parts = [snapshot.get("contract_type", "general"), snapshot.get("status", "complete")]
        if required_outputs:
            parts.append("outputs=" + ",".join(required_outputs[:4]))
        if missing_fields:
            parts.append("missing=" + ",".join(missing_fields[:4]))
        snapshot["summary"] = " | ".join(str(item).strip() for item in parts if str(item).strip())[:400]

    return snapshot


def normalize_execution_brief_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    status = _trim(raw.get("status", ""), 48).lower()
    if status not in EXECUTION_BRIEF_STATUSES:
        status = ""

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", EXECUTION_BRIEF_VERSION), 48) or EXECUTION_BRIEF_VERSION,
    }
    if status:
        snapshot["status"] = status

    for key in ("summary", "operator_decision", "non_goals"):
        token = _trim(raw.get(key, ""), 320)
        if token:
            snapshot[key] = token

    executable_slice = _dedupe_rows(list(raw.get("executable_slice") or []), limit=12, text_limit=160)
    blocked_slice = _dedupe_rows(list(raw.get("blocked_slice") or []), limit=12, text_limit=160)
    if executable_slice:
        snapshot["executable_slice"] = executable_slice
    if blocked_slice:
        snapshot["blocked_slice"] = blocked_slice

    if "offdesk_allowed" in raw:
        snapshot["offdesk_allowed"] = _normalize_bool(raw.get("offdesk_allowed"), False)

    return snapshot


def normalize_background_launch_spec_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", BACKGROUND_LAUNCH_SPEC_VERSION), 48) or BACKGROUND_LAUNCH_SPEC_VERSION,
    }
    runner_target = _trim(raw.get("runner_target", ""), 64).lower()
    if runner_target in BACKGROUND_RUNNER_TARGETS:
        snapshot["runner_target"] = runner_target
    for key, limit in (
        ("spec_id", 96),
        ("kind", 64),
        ("mode", 64),
        ("entrypoint", 160),
        ("command_cwd", 240),
        ("project_root", 240),
        ("team_dir", 240),
        ("manager_state_file", 240),
        ("request_id", 96),
        ("project_key", 64),
        ("launch_mode", 64),
        ("source_surface", 64),
        ("created_by", 96),
        ("blocked_reason", 240),
    ):
        token = _trim(raw.get(key, ""), limit)
        if token:
            snapshot[key] = token
    env_keys = _dedupe_rows(list(raw.get("env_keys") or []), limit=12, text_limit=64)
    argv = _dedupe_rows(list(raw.get("argv") or []), limit=20, text_limit=200)
    command_argv = _dedupe_rows(list(raw.get("command_argv") or []), limit=40, text_limit=240)
    if env_keys:
        snapshot["env_keys"] = env_keys
    if argv:
        snapshot["argv"] = argv
    if command_argv:
        snapshot["command_argv"] = command_argv
    if "externalizable" in raw:
        snapshot["externalizable"] = _normalize_bool(raw.get("externalizable"), False)
    summary = _trim(raw.get("summary", ""), 320)
    if not summary:
        parts: List[str] = []
        kind = str(snapshot.get("kind", "")).strip()
        mode = str(snapshot.get("mode", "")).strip()
        entrypoint = str(snapshot.get("entrypoint", "")).strip()
        externalizable = bool(snapshot.get("externalizable", False))
        if kind:
            parts.append(kind)
        if mode:
            parts.append(f"mode={mode}")
        if entrypoint:
            parts.append(f"entry={entrypoint}")
        parts.append(f"externalizable={'yes' if externalizable else 'no'}")
        blocked_reason = str(snapshot.get("blocked_reason", "")).strip()
        if blocked_reason:
            parts.append(blocked_reason)
        summary = " | ".join(parts)[:320]
    if summary:
        snapshot["summary"] = summary
    return snapshot


def normalize_background_run_ticket_snapshot(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    status = _trim(raw.get("status", ""), 32).lower()
    if status not in BACKGROUND_RUN_STATUSES:
        status = ""
    runner_target = _trim(raw.get("runner_target", ""), 64).lower()
    if runner_target not in BACKGROUND_RUNNER_TARGETS:
        runner_target = ""

    snapshot: Dict[str, Any] = {
        "version": _trim(raw.get("version", BACKGROUND_RUN_TICKET_VERSION), 48) or BACKGROUND_RUN_TICKET_VERSION,
    }
    if status:
        snapshot["status"] = status
    if runner_target:
        snapshot["runner_target"] = runner_target

    for key, limit in (
        ("ticket_id", 96),
        ("request_id", 96),
        ("project_key", 64),
        ("execution_brief_status", 48),
        ("launch_mode", 64),
        ("runtime_handle", 120),
        ("runtime_summary", 240),
        ("created_at", 64),
        ("touched_at", 64),
        ("created_by", 96),
        ("source_surface", 64),
    ):
        token = _trim(raw.get(key, ""), limit)
        if token:
            snapshot[key] = token

    evidence_bundle = raw.get("evidence_bundle")
    evidence_summary = ""
    evidence_artifacts: List[str] = []
    if isinstance(evidence_bundle, dict):
        bundle_id = _trim(evidence_bundle.get("bundle_id", ""), 96)
        bundle_status = _trim(evidence_bundle.get("status", ""), 48)
        final_outcome = _trim(evidence_bundle.get("final_outcome", ""), 96)
        summary = _trim(evidence_bundle.get("summary", ""), 240)
        if summary:
            evidence_summary = summary
        else:
            parts: List[str] = []
            if bundle_id:
                parts.append(f"id={bundle_id}")
            if bundle_status:
                parts.append(f"status={bundle_status}")
            if final_outcome:
                parts.append(f"outcome={final_outcome}")
            evidence_summary = " | ".join(parts)[:240]
        evidence_artifacts = _dedupe_rows(list(evidence_bundle.get("artifacts") or []), limit=8, text_limit=160)
    else:
        evidence_summary = _trim(evidence_bundle, 240)
        evidence_artifacts = _dedupe_rows(list(raw.get("evidence_artifacts") or []), limit=8, text_limit=160)
    if evidence_summary:
        snapshot["evidence_bundle"] = evidence_summary
    if evidence_artifacts:
        snapshot["evidence_artifacts"] = evidence_artifacts

    launch_spec = normalize_background_launch_spec_snapshot(raw.get("launch_spec"))
    if launch_spec:
        snapshot["launch_spec"] = launch_spec

    return snapshot


def build_execution_brief(contract: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_request_contract_snapshot(contract)
    if not snapshot:
        return {}

    contract_status = str(snapshot.get("status", "")).strip().lower()
    missing = list(snapshot.get("missing_fields") or [])
    ambiguity_notes = list(snapshot.get("ambiguity_notes") or [])
    required_outputs = list(snapshot.get("required_outputs") or [])
    artifact_contracts = snapshot.get("artifact_contracts") if isinstance(snapshot.get("artifact_contracts"), dict) else {}

    status = "executable"
    operator_decision = ""
    if contract_status == "ambiguous":
        status = "operator_decision_required" if ambiguity_notes else "underspecified"
        operator_decision = "; ".join(str(item).strip() for item in ambiguity_notes[:3] if str(item).strip())[:320]
    elif contract_status == "incomplete":
        status = "underspecified"

    executable_slice: List[str] = []
    blocked_slice: List[str] = []

    for item in required_outputs[:8]:
        token = str(item or "").strip()
        if token:
            executable_slice.append(token)
    if not executable_slice and artifact_contracts:
        for key in sorted(artifact_contracts.keys())[:8]:
            token = str(key or "").strip()
            if token:
                executable_slice.append(token)

    for item in missing[:8]:
        token = str(item or "").strip()
        if token:
            blocked_slice.append(token)
    for item in ambiguity_notes[:4]:
        token = str(item or "").strip()
        if token and token not in blocked_slice:
            blocked_slice.append(token)

    if status == "operator_decision_required" and not operator_decision and blocked_slice:
        operator_decision = blocked_slice[0][:320]

    summary_parts: List[str] = [status]
    if executable_slice:
        summary_parts.append("do=" + ",".join(executable_slice[:4]))
    if blocked_slice:
        summary_parts.append("blocked=" + ",".join(blocked_slice[:3]))
    summary = " | ".join(part for part in summary_parts if part).strip()[:400]

    return normalize_execution_brief_snapshot(
        {
            "version": EXECUTION_BRIEF_VERSION,
            "status": status,
            "summary": summary,
            "executable_slice": executable_slice,
            "blocked_slice": blocked_slice,
            "operator_decision": operator_decision,
            "offdesk_allowed": status in {"executable", "partially_executable"},
        }
    )


def build_background_run_ticket(
    *,
    request_id: str,
    project_key: str,
    execution_brief_status: str = "",
    runner_target: str = "local_background",
    launch_mode: str = "detached_no_wait",
    created_at: str = "",
    created_by: str = "",
    source_surface: str = "",
    status: str = "queued",
    runtime_handle: str = "",
    runtime_summary: str = "",
    evidence_bundle: Any = "",
    evidence_artifacts: Optional[List[str]] = None,
    launch_spec: Any = None,
    ticket_id: str = "",
    touched_at: str = "",
) -> Dict[str, Any]:
    rid = _trim(request_id, 96)
    pkey = _trim(project_key, 64)
    created = _trim(created_at, 64)
    explicit_ticket = _trim(ticket_id, 96)
    if explicit_ticket:
        resolved_ticket = explicit_ticket
    else:
        stamp = created.replace("-", "").replace(":", "").replace("+", "").replace("T", "").strip()
        seed = rid or pkey or "run"
        resolved_ticket = f"BGT-{seed}-{stamp or 'pending'}"[:96]
    return normalize_background_run_ticket_snapshot(
        {
            "version": BACKGROUND_RUN_TICKET_VERSION,
            "ticket_id": resolved_ticket,
            "request_id": rid,
            "project_key": pkey,
            "execution_brief_status": _trim(execution_brief_status, 48),
            "runner_target": runner_target,
            "launch_mode": launch_mode,
            "runtime_handle": _trim(runtime_handle, 120),
            "runtime_summary": _trim(runtime_summary, 240),
            "created_at": created,
            "touched_at": _trim(touched_at, 64) or created,
            "created_by": _trim(created_by, 96),
            "source_surface": _trim(source_surface, 64),
            "status": status,
            "evidence_bundle": evidence_bundle,
            "evidence_artifacts": list(evidence_artifacts or []),
            "launch_spec": launch_spec if isinstance(launch_spec, dict) else {},
        }
    )


def build_background_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    runner_target: str = "local_background",
    launch_mode: str = "detached_no_wait",
    source_surface: str = "",
    created_by: str = "",
    kind: str = "gateway_dispatch",
    mode: str = "in_process_callback",
    entrypoint: str = "aoe-telegram-gateway",
    argv: Optional[List[str]] = None,
    env_keys: Optional[List[str]] = None,
    command_argv: Optional[List[str]] = None,
    command_cwd: str = "",
    externalizable: bool = False,
    blocked_reason: str = "",
    summary: str = "",
) -> Dict[str, Any]:
    rid = _trim(request_id, 96)
    pkey = _trim(project_key, 64)
    spec_id = f"BLS-{rid or pkey or 'run'}"[:96]
    return normalize_background_launch_spec_snapshot(
        {
            "version": BACKGROUND_LAUNCH_SPEC_VERSION,
            "spec_id": spec_id,
            "kind": _trim(kind, 64),
            "mode": _trim(mode, 64),
            "entrypoint": _trim(entrypoint, 160),
            "project_root": _trim(project_root, 240),
            "team_dir": _trim(team_dir, 240),
            "manager_state_file": _trim(manager_state_file, 240),
            "request_id": rid,
            "project_key": pkey,
            "runner_target": runner_target,
            "launch_mode": launch_mode,
            "source_surface": _trim(source_surface, 64),
            "created_by": _trim(created_by, 96),
            "argv": list(argv or []),
            "env_keys": list(env_keys or []),
            "command_argv": list(command_argv or []),
            "command_cwd": _trim(command_cwd, 240),
            "externalizable": bool(externalizable),
            "blocked_reason": _trim(
                blocked_reason or (
                    "" if externalizable else "requires in-process callback registry"
                ),
                240,
            ),
            "summary": _trim(summary, 320),
        }
    )


def _runner_launch_defaults(runner_target: str) -> Dict[str, Any]:
    token = _trim(runner_target, 64).lower()
    if token == "local_tmux":
        return {
            "kind": "background_dispatch",
            "mode": BACKGROUND_RUNNER_DEFAULT_MODES["local_tmux"],
            "entrypoint": "aoe-background-worker",
            "argv": ["worker-run", "--runner", "local_tmux"],
            "env_keys": ["AOE_TEAM_DIR", "AOE_STATE_DIR", "AOE_ORCH_ALIAS"],
            "externalizable": True,
            "blocked_reason": "",
        }
    if token == "github_runner":
        return {
            "kind": "background_dispatch",
            "mode": BACKGROUND_RUNNER_DEFAULT_MODES["github_runner"],
            "entrypoint": "aoe-background-worker",
            "argv": ["worker-run", "--runner", "github_runner"],
            "env_keys": ["AOE_TEAM_DIR", "AOE_STATE_DIR", "GITHUB_TOKEN", "GITHUB_REPOSITORY"],
            "externalizable": True,
            "blocked_reason": "",
        }
    if token == "remote_worker":
        return {
            "kind": "background_dispatch",
            "mode": BACKGROUND_RUNNER_DEFAULT_MODES["remote_worker"],
            "entrypoint": "aoe-background-worker",
            "argv": ["worker-run", "--runner", "remote_worker"],
            "env_keys": ["AOE_TEAM_DIR", "AOE_STATE_DIR", "AOE_REMOTE_ENDPOINT"],
            "externalizable": True,
            "blocked_reason": "",
        }
    return {
        "kind": "gateway_dispatch",
        "mode": BACKGROUND_RUNNER_DEFAULT_MODES["local_background"],
        "entrypoint": "aoe-telegram-gateway",
        "argv": ["run", "--no-wait"],
        "env_keys": ["AOE_TEAM_DIR", "AOE_STATE_DIR"],
        "externalizable": False,
        "blocked_reason": "requires in-process callback registry",
    }


def build_runner_background_launch_spec(
    *,
    runner_target: str,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
    command_argv: Optional[List[str]] = None,
    command_cwd: str = "",
) -> Dict[str, Any]:
    defaults = _runner_launch_defaults(runner_target)
    return build_background_launch_spec(
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        runner_target=runner_target,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
        kind=str(defaults.get("kind", "")),
        mode=str(defaults.get("mode", "")),
        entrypoint=str(defaults.get("entrypoint", "")),
        argv=list(defaults.get("argv") or []),
        env_keys=list(defaults.get("env_keys") or []),
        command_argv=list(command_argv or []),
        command_cwd=command_cwd,
        externalizable=bool(defaults.get("externalizable", False)),
        blocked_reason=str(defaults.get("blocked_reason", "")),
    )


def build_local_tmux_background_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
    command_argv: Optional[List[str]] = None,
    command_cwd: str = "",
) -> Dict[str, Any]:
    return build_runner_background_launch_spec(
        runner_target="local_tmux",
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
        command_argv=command_argv,
        command_cwd=command_cwd,
    )


def gateway_cli_entrypoint_path() -> str:
    return str(Path(__file__).resolve().with_name("aoe-telegram-gateway.py"))


def build_gateway_simulation_command_argv(
    *,
    project_root: str,
    team_dir: str,
    manager_state_file: str,
    simulate_text: str,
    simulate_chat_id: str = "local-background",
    simulate_live: bool = True,
) -> List[str]:
    argv: List[str] = [
        str(sys.executable or "python3"),
        gateway_cli_entrypoint_path(),
        "--project-root",
        _trim(project_root, 240) or ".",
        "--team-dir",
        _trim(team_dir, 240) or "",
        "--manager-state-file",
        _trim(manager_state_file, 240) or "",
        "--simulate-chat-id",
        _trim(simulate_chat_id, 96) or "local-background",
        "--simulate-text",
        _trim(simulate_text, 1200),
    ]
    if simulate_live:
        argv.append("--simulate-live")
    return [item for item in argv if str(item).strip()]


def build_gateway_run_command_text(
    *,
    prompt: str,
    orch_target: str = "",
    roles: Optional[List[str]] = None,
    priority: str = "",
    timeout_sec: int | None = None,
    force_mode: str = "dispatch",
) -> str:
    prompt_text = _trim(prompt, 2000)
    if not prompt_text:
        return ""
    mode = _trim(force_mode or "dispatch", 32).lower()
    if mode not in {"dispatch", "direct"}:
        mode = "dispatch"
    argv: List[str] = ["aoe"]
    orch_ref = _trim(orch_target, 64)
    if orch_ref:
        argv.extend(["orch", "run", "--orch", orch_ref])
    else:
        argv.append("run")
    argv.append(f"--{mode}")
    role_tokens = _dedupe_rows(list(roles or []), limit=12, text_limit=64)
    if role_tokens:
        argv.extend(["--roles", ",".join(role_tokens)])
    priority_token = _trim(priority, 16).upper()
    if priority_token in {"P1", "P2", "P3"}:
        argv.extend(["--priority", priority_token])
    try:
        timeout_value = int(timeout_sec or 0)
    except Exception:
        timeout_value = 0
    if timeout_value > 0:
        argv.extend(["--timeout-sec", str(timeout_value)])
    argv.append(prompt_text)
    return shlex.join([item for item in argv if str(item).strip()])


def build_local_tmux_gateway_command_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    command_text: str,
    simulate_chat_id: str = "local-background",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    return build_local_tmux_background_launch_spec(
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
        command_argv=build_gateway_simulation_command_argv(
            project_root=project_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            simulate_text=command_text,
            simulate_chat_id=simulate_chat_id,
            simulate_live=True,
        ),
        command_cwd=project_root,
    )


def build_local_tmux_gateway_run_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    orch_target: str = "",
    prompt: str,
    roles: Optional[List[str]] = None,
    priority: str = "",
    timeout_sec: int | None = None,
    force_mode: str = "dispatch",
    simulate_chat_id: str = "local-background",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    return build_local_tmux_gateway_command_launch_spec(
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        command_text=build_gateway_run_command_text(
            prompt=prompt,
            orch_target=orch_target,
            roles=roles,
            priority=priority,
            timeout_sec=timeout_sec,
            force_mode=force_mode,
        ),
        simulate_chat_id=simulate_chat_id,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
    )


def build_github_runner_background_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    return build_runner_background_launch_spec(
        runner_target="github_runner",
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
    )


def build_remote_worker_background_launch_spec(
    *,
    request_id: str,
    project_key: str,
    project_root: str = "",
    team_dir: str = "",
    manager_state_file: str = "",
    launch_mode: str = "offdesk_manual",
    source_surface: str = "",
    created_by: str = "",
) -> Dict[str, Any]:
    return build_runner_background_launch_spec(
        runner_target="remote_worker",
        request_id=request_id,
        project_key=project_key,
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
        launch_mode=launch_mode,
        source_surface=source_surface,
        created_by=created_by,
    )


def background_runner_requires_externalizable_spec(runner_target: Any) -> bool:
    token = _trim(runner_target, 64).lower()
    return token in BACKGROUND_EXTERNAL_RUNNER_TARGETS


def background_run_ticket_external_worker_allowed(ticket: Dict[str, Any]) -> bool:
    snapshot = normalize_background_run_ticket_snapshot(ticket)
    if not snapshot:
        return False
    runner_target = str(snapshot.get("runner_target", "")).strip().lower()
    if not background_runner_requires_externalizable_spec(runner_target):
        return True
    launch_spec = normalize_background_launch_spec_snapshot(snapshot.get("launch_spec"))
    return bool(launch_spec.get("externalizable", False))


def select_background_runner_target(
    *,
    preferred_runner_target: Any = "",
    launch_spec: Any = None,
    allow_external_targets: bool = False,
) -> str:
    preferred = _trim(preferred_runner_target, 64).lower()
    if preferred not in BACKGROUND_RUNNER_TARGETS:
        preferred = ""
    snapshot = normalize_background_launch_spec_snapshot(launch_spec)
    spec_runner = _trim(snapshot.get("runner_target", ""), 64).lower()
    if spec_runner not in BACKGROUND_RUNNER_TARGETS:
        spec_runner = ""
    candidate = preferred or spec_runner or "local_background"
    if candidate == "local_tmux" and bool(snapshot.get("externalizable", False)):
        return "local_tmux"
    if allow_external_targets and candidate in {"github_runner", "remote_worker"} and bool(snapshot.get("externalizable", False)):
        return candidate
    return "local_background"


def _lineage_preset(run_control_mode: str, run_source_task: Optional[Dict[str, Any]]) -> str:
    if str(run_control_mode or "").strip().lower() not in {"retry", "replan", "followup"}:
        return ""
    if not isinstance(run_source_task, dict):
        return ""
    for key in ("request_contract_preset", "phase2_team_preset", "phase1_role_preset"):
        token = normalize_role_preset(run_source_task.get(key, ""))
        if token:
            return token
    return ""


def resolve_request_contract_preset(
    *,
    source_prompt: str,
    selected_roles: Optional[List[str]] = None,
    explicit_preset: str = "",
    run_control_mode: str = "",
    run_source_task: Optional[Dict[str, Any]] = None,
) -> str:
    explicit = normalize_role_preset(explicit_preset)
    if explicit and explicit != "general":
        return explicit

    lineage = _lineage_preset(run_control_mode, run_source_task)
    if lineage and lineage != "general":
        return lineage

    inferred = normalize_role_preset(
        classify_dispatch_role_preset(source_prompt, selected_roles=list(selected_roles or []))
    )
    if data_request_contract_matches(source_prompt):
        return "data"
    if mixed_request_contract_matches(source_prompt) and inferred in {"mixed", "review", "writer", "build", "general", ""}:
        return "mixed"
    if review_request_contract_matches(source_prompt) and inferred in {"", "general", "review"}:
        return "review"
    if inferred == "data" and not data_request_contract_matches(source_prompt):
        return "general"
    return inferred or "general"


def build_request_contract(
    *,
    source_prompt: str,
    selected_roles: Optional[List[str]] = None,
    explicit_preset: str = "",
    run_control_mode: str = "",
    run_source_task: Optional[Dict[str, Any]] = None,
    intent_action: str = "",
    project_key: str = "",
) -> Dict[str, Any]:
    resolved_preset = resolve_request_contract_preset(
        source_prompt=source_prompt,
        selected_roles=selected_roles,
        explicit_preset=explicit_preset,
        run_control_mode=run_control_mode,
        run_source_task=run_source_task,
    )
    if resolved_preset == "data":
        contract = extract_data_request_contract(source_prompt) or {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": "data",
            "preset": "data",
            "status": "incomplete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {},
            "required_outputs": [],
            "required_evidence": [],
            "missing_fields": ["source_path", "target_column", "accepted_input_formats", "normalize_to"],
            "ambiguity_notes": [],
            "summary": "data | incomplete",
            "artifact_contracts": {},
        }
    elif resolved_preset == "mixed":
        contract = extract_mixed_request_contract(source_prompt) or {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": "mixed",
            "preset": "mixed",
            "status": "complete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {
                "deliverable_policy": {
                    "work_result_required": True,
                    "writer_outputs": [],
                    "review_outputs": [],
                }
            },
            "required_outputs": ["work_result"],
            "required_evidence": ["implementation_delta"],
            "missing_fields": [],
            "ambiguity_notes": [],
            "summary": "mixed | text-first",
            "artifact_contracts": {},
        }
    elif resolved_preset == "review":
        contract = extract_review_request_contract(source_prompt) or {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": "review",
            "preset": "review",
            "status": "complete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {},
            "required_outputs": ["review_report"],
            "required_evidence": ["git_diff_scope"],
            "missing_fields": [],
            "ambiguity_notes": [],
            "summary": "review | text-first",
            "artifact_contracts": {},
        }
    else:
        contract = {
            "version": REQUEST_CONTRACT_VERSION,
            "contract_type": resolved_preset,
            "preset": resolved_preset,
            "status": "complete",
            "objective": _trim(source_prompt, 240),
            "source_prompt": _trim(source_prompt, 2000),
            "fields": {},
            "required_outputs": [],
            "required_evidence": [],
            "missing_fields": [],
            "ambiguity_notes": [],
            "summary": f"{resolved_preset or 'general'} | text-first",
            "artifact_contracts": {},
        }

    contract["intent_action"] = _trim(intent_action, 64)
    contract["project_key"] = _trim(project_key, 64)
    return normalize_request_contract_snapshot(contract)


def request_contract_is_blocking(contract: Dict[str, Any]) -> bool:
    snapshot = normalize_request_contract_snapshot(contract)
    return str(snapshot.get("status", "")).strip().lower() in {"incomplete", "ambiguous"}


def request_contract_block_reason(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    missing = list(snapshot.get("missing_fields") or [])
    notes = list(snapshot.get("ambiguity_notes") or [])
    if missing:
        return "missing required contract fields: " + ", ".join(missing[:6])
    if notes:
        return "contract ambiguity: " + "; ".join(str(item).strip() for item in notes[:4] if str(item).strip())
    status = _trim(snapshot.get("status", ""), 32) or "incomplete"
    return f"request contract is {status}"


def request_contract_summary(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    return _trim(snapshot.get("summary", ""), 400)


def execution_brief_is_offdesk_allowed(brief: Dict[str, Any]) -> bool:
    snapshot = normalize_execution_brief_snapshot(brief)
    status = str(snapshot.get("status", "")).strip().lower()
    if status in {"executable", "partially_executable"}:
        return True
    return bool(snapshot.get("offdesk_allowed", False))


def execution_brief_summary(brief: Dict[str, Any]) -> str:
    snapshot = normalize_execution_brief_snapshot(brief)
    return _trim(snapshot.get("summary", ""), 400)


def execution_brief_block_reason(brief: Dict[str, Any]) -> str:
    snapshot = normalize_execution_brief_snapshot(brief)
    status = str(snapshot.get("status", "")).strip().lower() or "underspecified"
    blocked = list(snapshot.get("blocked_slice") or [])
    operator_decision = _trim(snapshot.get("operator_decision", ""), 320)
    if operator_decision:
        return operator_decision
    if blocked:
        return f"{status}: " + ", ".join(str(item).strip() for item in blocked[:6] if str(item).strip())
    summary = _trim(snapshot.get("summary", ""), 320)
    if summary:
        return summary
    return f"execution brief is {status}"


def request_contract_planning_appendix(contract: Dict[str, Any]) -> str:
    snapshot = normalize_request_contract_snapshot(contract)
    if not snapshot:
        return ""

    lines = ["[Request Contract]"]
    lines.append(f"- type: {snapshot.get('contract_type', '-')}")
    lines.append(f"- status: {snapshot.get('status', '-')}")
    lines.append(f"- preset: {snapshot.get('preset', '-')}")
    summary = _trim(snapshot.get("summary", ""), 400)
    if summary:
        lines.append(f"- summary: {summary}")

    fields = snapshot.get("fields") if isinstance(snapshot.get("fields"), dict) else {}
    if fields:
        lines.append("- fields:")
        for key in sorted(fields.keys()):
            value = fields.get(key)
            if isinstance(value, dict):
                items = []
                for child_key in sorted(value.keys()):
                    child_value = value.get(child_key)
                    items.append(f"{child_key}={child_value}")
                lines.append(f"  - {key}: {', '.join(items)}")
            elif isinstance(value, list):
                lines.append(f"  - {key}: {', '.join(str(item).strip() for item in value if str(item).strip())}")
            else:
                lines.append(f"  - {key}: {value}")

    outputs = list(snapshot.get("required_outputs") or [])
    if outputs:
        lines.append("- required_outputs: " + ", ".join(outputs))

    artifact_contracts = snapshot.get("artifact_contracts") if isinstance(snapshot.get("artifact_contracts"), dict) else {}
    if artifact_contracts:
        lines.append("- artifact_contracts:")
        for key in sorted(artifact_contracts.keys()):
            row = artifact_contracts.get(key) if isinstance(artifact_contracts.get(key), dict) else {}
            path = _trim(row.get("path", key), 200) or key
            fmt = _trim(row.get("format", ""), 32) or "-"
            fields_list = list(row.get("required_fields") or [])
            notes = list(row.get("acceptance_notes") or [])
            lines.append(f"  - {key}: path={path} format={fmt}")
            if fields_list:
                lines.append("    required_fields: " + ", ".join(str(item).strip() for item in fields_list[:6] if str(item).strip()))
            if notes:
                lines.append("    notes: " + "; ".join(str(item).strip() for item in notes[:2] if str(item).strip()))

    missing = list(snapshot.get("missing_fields") or [])
    if missing:
        lines.append("- missing_fields: " + ", ".join(missing))
    notes = list(snapshot.get("ambiguity_notes") or [])
    if notes:
        lines.append("- ambiguity_notes: " + "; ".join(str(item).strip() for item in notes))
    return "\n".join(lines)


def request_contract_metadata(contract: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_request_contract_snapshot(contract)
    if not snapshot:
        return {}
    return deepcopy(
        {
            "request_contract_version": snapshot.get("version", REQUEST_CONTRACT_VERSION),
            "request_contract_type": snapshot.get("contract_type", ""),
            "request_contract_status": snapshot.get("status", ""),
            "request_contract_preset": snapshot.get("preset", ""),
            "request_contract_summary": snapshot.get("summary", ""),
            "request_contract_missing_fields": list(snapshot.get("missing_fields") or []),
            "request_contract_required_outputs": list(snapshot.get("required_outputs") or []),
            "request_contract_fields": dict(snapshot.get("fields") or {}),
            "request_contract_artifact_contracts": dict(snapshot.get("artifact_contracts") or {}),
        }
    )


def execution_brief_metadata(brief: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_execution_brief_snapshot(brief)
    if not snapshot:
        return {}
    return deepcopy(
        {
            "execution_brief_version": snapshot.get("version", EXECUTION_BRIEF_VERSION),
            "execution_brief_status": snapshot.get("status", ""),
            "execution_brief_summary": snapshot.get("summary", ""),
            "execution_brief_executable_slice": list(snapshot.get("executable_slice") or []),
            "execution_brief_blocked_slice": list(snapshot.get("blocked_slice") or []),
            "execution_brief_operator_decision": snapshot.get("operator_decision", ""),
            "execution_brief_offdesk_allowed": bool(snapshot.get("offdesk_allowed", False)),
        }
    )


def background_run_ticket_metadata(ticket: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = normalize_background_run_ticket_snapshot(ticket)
    if not snapshot:
        return {}
    launch_spec = normalize_background_launch_spec_snapshot(snapshot.get("launch_spec"))
    return deepcopy(
        {
            "background_run_ticket_version": snapshot.get("version", BACKGROUND_RUN_TICKET_VERSION),
            "background_run_ticket_id": snapshot.get("ticket_id", ""),
            "background_run_status": snapshot.get("status", ""),
            "background_run_runner_target": snapshot.get("runner_target", ""),
            "background_run_launch_mode": snapshot.get("launch_mode", ""),
            "background_run_runtime_handle": snapshot.get("runtime_handle", ""),
            "background_run_runtime_summary": snapshot.get("runtime_summary", ""),
            "background_run_created_at": snapshot.get("created_at", ""),
            "background_run_created_by": snapshot.get("created_by", ""),
            "background_run_source_surface": snapshot.get("source_surface", ""),
            "background_run_request_id": snapshot.get("request_id", ""),
            "background_run_project_key": snapshot.get("project_key", ""),
            "background_run_execution_brief_status": snapshot.get("execution_brief_status", ""),
            "background_run_evidence_bundle": snapshot.get("evidence_bundle", ""),
            "background_run_evidence_artifacts": list(snapshot.get("evidence_artifacts") or []),
            "background_run_launch_spec_id": launch_spec.get("spec_id", ""),
            "background_run_launch_spec_kind": launch_spec.get("kind", ""),
            "background_run_launch_spec_mode": launch_spec.get("mode", ""),
            "background_run_launch_spec_summary": launch_spec.get("summary", ""),
            "background_run_launch_spec_externalizable": bool(launch_spec.get("externalizable", False)),
        }
    )


def apply_request_contract_snapshot(target: Dict[str, Any], contract: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    metadata = request_contract_metadata(contract)
    for key, value in metadata.items():
        if value in ("", None, [], {}):
            target.pop(key, None)
            continue
        target[key] = deepcopy(value)
    return target


def apply_execution_brief_snapshot(target: Dict[str, Any], brief: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    metadata = execution_brief_metadata(brief)
    for key, value in metadata.items():
        if value in ("", None, [], {}):
            target.pop(key, None)
            continue
        target[key] = deepcopy(value)
    return target


def apply_background_run_ticket_snapshot(target: Dict[str, Any], ticket: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(target, dict):
        return {}
    metadata = background_run_ticket_metadata(ticket)
    for key, value in metadata.items():
        if value in ("", None, [], {}):
            target.pop(key, None)
            continue
        target[key] = deepcopy(value)
    return target


def background_run_evidence_artifacts_from_task(task: Dict[str, Any]) -> List[str]:
    if not isinstance(task, dict):
        return []
    artifact_contracts = task.get("request_contract_artifact_contracts")
    if not isinstance(artifact_contracts, dict):
        artifact_contracts = {}
    required_outputs = task.get("request_contract_required_outputs")
    if not isinstance(required_outputs, list):
        required_outputs = []
    artifacts: List[str] = []
    for output in list(required_outputs or [])[:12]:
        alias = _trim(output, 120)
        if not alias:
            continue
        row = artifact_contracts.get(alias) if isinstance(artifact_contracts.get(alias), dict) else {}
        path = _trim((row or {}).get("path", ""), 200) or alias
        if path and path not in artifacts:
            artifacts.append(path)
    return artifacts[:8]


def background_run_evidence_bundle_from_task(
    task: Dict[str, Any],
    *,
    default_status: str = "completed",
    default_outcome: str = "dispatch_flow_returned",
) -> str:
    if not isinstance(task, dict):
        return f"status={default_status} | outcome={default_outcome}"
    parts: List[str] = []
    status = _trim(task.get("status", default_status), 48) or default_status
    outcome = default_outcome
    if status == "completed":
        outcome = "dispatch_completed"
    parts.append(f"status={status}")
    parts.append(f"outcome={outcome}")
    phase = _trim(task.get("tf_phase", ""), 48)
    if phase:
        parts.append(f"phase={phase}")
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    if result:
        if "complete" in result:
            parts.append(f"complete={str(bool(result.get('complete', False))).lower()}")
        verdict = _trim(result.get("verdict", ""), 48)
        if verdict:
            parts.append(f"verdict={verdict}")
    return " | ".join(parts)[:240]
