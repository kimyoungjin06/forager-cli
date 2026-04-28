#!/usr/bin/env python3
"""GitHub Actions bridge helpers for external background handoffs."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_background_runs import (
    background_runs_state_path,
    get_background_run_ticket,
    load_background_runs_state,
)
from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import external_background_handoff_path


GITHUB_RUNNER_BUNDLE_VERSION = "2026-04-28.v1"
GITHUB_RUNNER_BUNDLE_KIND = "github_runner_worker_bundle"
GITHUB_RUNNER_TRANSPORT_POLICY_VERSION = "2026-04-28.v1"
GITHUB_RUNNER_TRANSPORT_POLICY_KIND = "github_runner_transport_policy"


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _boolish(raw: Any) -> bool:
    token = _trim(raw, 32).lower()
    return token in {"1", "true", "yes", "y", "on"}


def _intish(raw: Any, default: int) -> int:
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _resolve_team_dir(raw: Path | str) -> Path:
    return Path(raw).expanduser().resolve()


def _relative_artifact_path(team_dir: Path, path: Path) -> str:
    return artifact_backend(team_dir).relative_artifact_path(path)


def build_github_runner_transport_policy(
    *,
    runner_target: str = "github_runner",
    team_dir: Path | str = ".aoe-team",
    event_name: str = "",
    commit_results: Any = False,
    bundle_present: Any = False,
    timeout_sec: Any = 900,
    max_items: Any = 1,
) -> Dict[str, Any]:
    raw_target = _trim(runner_target, 64).lower()
    target = normalize_executor_runner_target(raw_target, "")
    raw_team_dir = _trim(team_dir, 240) or ".aoe-team"
    team_path = Path(raw_team_dir).expanduser()
    event = _trim(event_name, 80).lower()
    commit_mode = _boolish(commit_results)
    has_bundle = _boolish(bundle_present)
    timeout_value = _intish(timeout_sec, -1)
    max_items_value = _intish(max_items, -1)
    violations = []
    warnings = []

    if target != "github_runner":
        violations.append(
            {
                "code": "unsupported_runner_target",
                "detail": f"github_runner workflow supports only runner_target=github_runner, got {raw_target or '-'}",
            }
        )
    if not raw_team_dir or raw_team_dir == ".":
        violations.append(
            {
                "code": "unsafe_team_dir",
                "detail": "team_dir must name a relative runtime directory inside the checkout",
            }
        )
    if team_path.is_absolute() or ".." in team_path.parts:
        violations.append(
            {
                "code": "unsafe_team_dir",
                "detail": "team_dir must be relative and must not contain parent-directory traversal",
            }
        )
    if event and event not in {"workflow_dispatch", "repository_dispatch"}:
        warnings.append(
            {
                "code": "unknown_event_name",
                "detail": f"event_name={event} is not a standard external worker trigger",
            }
        )
    if timeout_value < 1 or timeout_value > 21600:
        violations.append(
            {
                "code": "timeout_out_of_range",
                "detail": "timeout_sec must be between 1 and 21600 seconds",
            }
        )
    if max_items_value < 1 or max_items_value > 50:
        violations.append(
            {
                "code": "max_items_out_of_range",
                "detail": "max_items must be between 1 and 50",
            }
        )
    if commit_mode:
        warnings.append(
            {
                "code": "commit_results_write_mode",
                "detail": "commit_results writes sidecars back with contents:write; prefer artifact import for routine pickup",
            }
        )
    if not has_bundle:
        warnings.append(
            {
                "code": "bundle_absent",
                "detail": "no handoff bundle was provided; worker will rely on sidecars already present in the checkout",
            }
        )

    result_transport = "actions_artifact+optional_git_commit" if commit_mode else "actions_artifact"
    credential_scope = "contents:write" if commit_mode else "contents:read"
    ok = not violations
    return {
        "version": GITHUB_RUNNER_TRANSPORT_POLICY_VERSION,
        "kind": GITHUB_RUNNER_TRANSPORT_POLICY_KIND,
        "ok": ok,
        "runner_target": target or raw_target,
        "team_dir": raw_team_dir,
        "event_name": event,
        "commit_results": commit_mode,
        "bundle_present": has_bundle,
        "timeout_sec": timeout_value,
        "max_items": max_items_value,
        "result_transport": result_transport,
        "credential_scope": credential_scope,
        "trust_boundary": "operator_triggered_github_actions",
        "violations": violations,
        "warnings": warnings,
        "summary": (
            f"github_runner_policy | ok={'yes' if ok else 'no'} | "
            f"transport={result_transport} | credential={credential_scope} | "
            f"team_dir={raw_team_dir}"
        ),
    }


def build_github_runner_worker_bundle(
    *,
    team_dir: Path | str,
    ticket_id: str,
    runner_target: str = "github_runner",
) -> Dict[str, Any]:
    resolved_team_dir = _resolve_team_dir(team_dir)
    target = normalize_executor_runner_target(runner_target, "github_runner")
    token = _trim(ticket_id, 96)
    if target != "github_runner":
        raise ValueError("github_runner bridge only supports runner_target=github_runner")
    if not token:
        raise ValueError("ticket_id is required")

    queue_path = background_runs_state_path(resolved_team_dir)
    row = get_background_run_ticket(queue_path, token)
    if not row:
        raise ValueError(f"background run ticket not found: {token}")
    row_status = _trim(row.get("status", ""), 32).lower()
    if row_status != "running":
        raise ValueError(f"background run ticket must be running, got {row_status or 'missing'}")

    handoff_path = external_background_handoff_path(resolved_team_dir, token, target)
    handoff = artifact_backend(resolved_team_dir).read_external_background_artifact(
        kind="handoffs",
        ticket_id=token,
        runner_target=target,
    )
    if not handoff:
        raise ValueError(f"external handoff artifact not found: {_relative_artifact_path(resolved_team_dir, handoff_path)}")

    state = load_background_runs_state(queue_path)
    background_runs = {
        "version": _trim(state.get("version", ""), 48) or "2026-04-04.v1",
        "updated_at": _trim(state.get("updated_at", ""), 64),
        "runs": [row],
    }
    return {
        "version": GITHUB_RUNNER_BUNDLE_VERSION,
        "kind": GITHUB_RUNNER_BUNDLE_KIND,
        "runner_target": target,
        "ticket_id": token,
        "team_dir": ".aoe-team",
        "background_runs": background_runs,
        "handoff": handoff,
        "handoff_artifact": _relative_artifact_path(resolved_team_dir, handoff_path),
        "summary": f"runner={target} ticket={token} handoff={_relative_artifact_path(resolved_team_dir, handoff_path)}",
    }


def encode_github_runner_worker_bundle(bundle: Dict[str, Any]) -> str:
    payload = json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def decode_github_runner_worker_bundle(raw: str) -> Dict[str, Any]:
    token = _trim(raw, 200000)
    if not token:
        raise ValueError("bundle_b64 is required")
    try:
        payload = json.loads(base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8"))
    except Exception as exc:
        raise ValueError("bundle_b64 is not valid base64 encoded JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("bundle payload must be a JSON object")
    if _trim(payload.get("kind", ""), 80) != GITHUB_RUNNER_BUNDLE_KIND:
        raise ValueError("bundle payload kind is not github_runner_worker_bundle")
    if _trim(payload.get("runner_target", ""), 64) != "github_runner":
        raise ValueError("bundle runner_target must be github_runner")
    if not _trim(payload.get("ticket_id", ""), 96):
        raise ValueError("bundle ticket_id is required")
    return payload


def _output_team_dir(*, output_root: Path | str, team_dir: str = "", bundle: Dict[str, Any]) -> Path:
    raw = _trim(team_dir, 240) or _trim(bundle.get("team_dir", ""), 240) or ".aoe-team"
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (Path(output_root).expanduser().resolve() / path).resolve()


def _rewrite_launch_spec_paths_for_checkout(
    *,
    handoff: Dict[str, Any],
    output_root: Path,
    team_dir: Path,
) -> Dict[str, Any]:
    updated_handoff = dict(handoff)
    launch_spec = dict(handoff.get("launch_spec") if isinstance(handoff.get("launch_spec"), dict) else {})
    if not launch_spec:
        return updated_handoff

    old_project_root = _trim(launch_spec.get("project_root", ""), 240)
    old_team_dir = _trim(launch_spec.get("team_dir", ""), 240)
    old_manager_state_file = _trim(launch_spec.get("manager_state_file", ""), 240)
    new_project_root = str(output_root)
    new_team_dir = str(team_dir)
    new_manager_state_file = str(team_dir / "orch_manager_state.json")
    replacements = {
        old_project_root: new_project_root,
        old_team_dir: new_team_dir,
        old_manager_state_file: new_manager_state_file,
    }

    launch_spec["project_root"] = new_project_root
    launch_spec["team_dir"] = new_team_dir
    if old_manager_state_file or launch_spec.get("manager_state_file"):
        launch_spec["manager_state_file"] = new_manager_state_file

    command_cwd = _trim(launch_spec.get("command_cwd", ""), 240)
    if command_cwd:
        rewritten_cwd = command_cwd
        for old, new in replacements.items():
            if old and rewritten_cwd == old:
                rewritten_cwd = new
                break
        if Path(rewritten_cwd).expanduser().is_absolute() and rewritten_cwd == command_cwd:
            rewritten_cwd = new_project_root
        launch_spec["command_cwd"] = rewritten_cwd

    rewritten_argv = []
    for item in list(launch_spec.get("command_argv") or []):
        text = str(item)
        for old, new in replacements.items():
            if old:
                text = text.replace(old, new)
        rewritten_argv.append(text)
    if rewritten_argv:
        launch_spec["command_argv"] = rewritten_argv

    updated_handoff["launch_spec"] = launch_spec
    return updated_handoff


def materialize_github_runner_worker_bundle(
    *,
    bundle: Dict[str, Any],
    output_root: Path | str,
    team_dir: str = "",
) -> Dict[str, Any]:
    ticket_id = _trim(bundle.get("ticket_id", ""), 96)
    runner_target = _trim(bundle.get("runner_target", ""), 64).lower()
    if runner_target != "github_runner" or not ticket_id:
        raise ValueError("bundle must include github_runner ticket_id")

    background_runs = bundle.get("background_runs") if isinstance(bundle.get("background_runs"), dict) else {}
    handoff = bundle.get("handoff") if isinstance(bundle.get("handoff"), dict) else {}
    if not background_runs or not isinstance(background_runs.get("runs"), list):
        raise ValueError("bundle background_runs is missing")
    if not handoff:
        raise ValueError("bundle handoff is missing")

    resolved_output_root = Path(output_root).expanduser().resolve()
    resolved_team_dir = _output_team_dir(output_root=resolved_output_root, team_dir=team_dir, bundle=bundle)
    rewritten_handoff = _rewrite_launch_spec_paths_for_checkout(
        handoff=handoff,
        output_root=resolved_output_root,
        team_dir=resolved_team_dir,
    )
    queue_path = background_runs_state_path(resolved_team_dir)
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(json.dumps(background_runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    handoff_path = artifact_backend(resolved_team_dir).write_external_background_artifact(
        kind="handoffs",
        ticket_id=ticket_id,
        runner_target=runner_target,
        payload=rewritten_handoff,
    )
    return {
        "team_dir": str(resolved_team_dir),
        "background_runs_path": _relative_artifact_path(resolved_team_dir, queue_path),
        "handoff_path": _relative_artifact_path(resolved_team_dir, handoff_path),
        "ticket_id": ticket_id,
        "runner_target": runner_target,
        "summary": f"materialized {runner_target} ticket {ticket_id}",
    }
