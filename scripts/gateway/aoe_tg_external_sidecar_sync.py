#!/usr/bin/env python3
"""Import external background worker sidecars into a local team dir."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_background_runs import background_runs_state_path, get_background_run_ticket
from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import (
    external_background_ack_path,
    external_background_result_path,
    poll_external_background_tickets,
    read_external_background_ack,
    read_external_background_result,
)
from aoe_tg_external_worker_runtime import external_background_log_path


EXTERNAL_SIDECAR_RUNNERS = {"github_runner", "remote_worker"}
SYNCHRONIZED_SIDECAR_DIRS = (
    "background_run_acks",
    "background_run_results",
    "background_run_logs",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _artifact_path_for_team(team_dir: Path, artifact_path: Path) -> str:
    return artifact_backend(team_dir).relative_artifact_path(artifact_path)


def _expected_sidecar_paths(team_dir: Path, ticket_id: str, runner_target: str) -> Dict[str, Path]:
    return {
        "ack": external_background_ack_path(team_dir, ticket_id, runner_target),
        "result": external_background_result_path(team_dir, ticket_id, runner_target),
        "log": external_background_log_path(team_dir, ticket_id, runner_target),
    }


def _safe_extract_zip(zip_path: Path, output_dir: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member.is_dir():
                continue
            if member_path.is_absolute() or ".." in member_path.parts:
                continue
            target = output_dir / member_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
    return output_dir


def _resolved_artifact_root(artifact_root: Path | str) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    root = Path(artifact_root).expanduser().resolve()
    if root.is_file() and root.suffix.lower() == ".zip":
        temp_dir = tempfile.TemporaryDirectory(prefix="aoe-external-sidecars-")
        return _safe_extract_zip(root, Path(temp_dir.name)), temp_dir
    return root, None


def _candidate_source_paths(
    *,
    artifact_root: Path,
    expected_paths: Dict[str, Path],
) -> Dict[str, Path]:
    expected_by_dir_and_name = {
        ("background_run_acks", expected_paths["ack"].name): "ack",
        ("background_run_results", expected_paths["result"].name): "result",
        ("background_run_logs", expected_paths["log"].name): "log",
    }
    found: Dict[str, Path] = {}
    if not artifact_root.exists():
        return found
    for source_path in artifact_root.rglob("*"):
        if source_path.is_symlink() or not source_path.is_file():
            continue
        parts = source_path.relative_to(artifact_root).parts
        for dirname in SYNCHRONIZED_SIDECAR_DIRS:
            if dirname not in parts:
                continue
            kind = expected_by_dir_and_name.get((dirname, source_path.name), "")
            if kind and kind not in found:
                found[kind] = source_path
            break
    return found


def _validate_json_sidecar(
    *,
    kind: str,
    source_path: Path,
    ticket_id: str,
) -> Dict[str, Any]:
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "reason": f"{kind}_json_invalid:{type(exc).__name__}"}
    if not isinstance(raw, dict):
        return {"ok": False, "reason": f"{kind}_json_not_object"}
    source_ticket = _trim(raw.get("ticket_id", ""), 96)
    if not source_ticket:
        return {"ok": False, "reason": f"{kind}_ticket_missing"}
    if source_ticket != ticket_id:
        return {"ok": False, "reason": f"{kind}_ticket_mismatch"}
    if kind == "ack":
        status = _trim(raw.get("status", ""), 32).lower()
        if status and status not in {"claimed", "running", "acknowledged"}:
            return {"ok": False, "reason": "ack_status_invalid"}
    if kind == "result":
        status = _trim(raw.get("status", ""), 32).lower()
        if status not in {"completed", "failed"}:
            return {"ok": False, "reason": "result_status_invalid"}
    return {"ok": True, "payload": raw}


def _copy_sidecar(
    *,
    kind: str,
    source_path: Path,
    target_path: Path,
    ticket_id: str,
    overwrite: bool,
) -> Dict[str, Any]:
    if kind in {"ack", "result"}:
        validation = _validate_json_sidecar(kind=kind, source_path=source_path, ticket_id=ticket_id)
        if not validation.get("ok"):
            return {
                "kind": kind,
                "status": "rejected",
                "reason": _trim(validation.get("reason", ""), 160),
                "source_path": str(source_path),
                "target_path": str(target_path),
            }
    if target_path.exists():
        source_bytes = source_path.read_bytes()
        target_bytes = target_path.read_bytes()
        if source_bytes == target_bytes:
            return {
                "kind": kind,
                "status": "unchanged",
                "source_path": str(source_path),
                "target_path": str(target_path),
            }
        if not overwrite:
            return {
                "kind": kind,
                "status": "skipped_existing",
                "reason": "target_exists",
                "source_path": str(source_path),
                "target_path": str(target_path),
            }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return {
        "kind": kind,
        "status": "copied",
        "source_path": str(source_path),
        "target_path": str(target_path),
    }


def _read_imported_ack_result(team_dir: Path, ticket_id: str, runner_target: str) -> Dict[str, Any]:
    ack = read_external_background_ack(external_background_ack_path(team_dir, ticket_id, runner_target))
    result = read_external_background_result(external_background_result_path(team_dir, ticket_id, runner_target))
    return {
        "ack_imported": bool(ack),
        "result_imported": bool(result),
        "ack_summary": _trim(ack.get("summary", ""), 240) if ack else "",
        "result_status": _trim(result.get("status", ""), 32) if result else "",
        "result_summary": _trim(result.get("summary", ""), 240) if result else "",
    }


def import_external_background_sidecars(
    *,
    team_dir: Path | str,
    artifact_root: Path | str,
    ticket_id: str,
    runner_target: str = "github_runner",
    overwrite: bool = False,
    poll_after_import: bool = False,
    now_iso: Callable[[], str] = _now_iso,
) -> Dict[str, Any]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        return {"ok": False, "reason": "unsupported_runner", "runner_target": target}
    if not token:
        return {"ok": False, "reason": "ticket_id_required", "runner_target": target}

    queue_path = background_runs_state_path(resolved_team_dir)
    row = get_background_run_ticket(queue_path, token)
    if not row:
        return {"ok": False, "reason": "ticket_not_found", "ticket_id": token, "runner_target": target}
    row_runner = normalize_executor_runner_target(row.get("runner_target", ""))
    if row_runner and row_runner != target:
        return {"ok": False, "reason": "ticket_runner_mismatch", "ticket_id": token, "runner_target": target}

    extracted_root, temp_dir = _resolved_artifact_root(artifact_root)
    try:
        expected_paths = _expected_sidecar_paths(resolved_team_dir, token, target)
        source_paths = _candidate_source_paths(artifact_root=extracted_root, expected_paths=expected_paths)
        copy_results: List[Dict[str, Any]] = []
        for kind, target_path in expected_paths.items():
            source_path = source_paths.get(kind)
            if not source_path:
                copy_results.append(
                    {
                        "kind": kind,
                        "status": "missing",
                        "target_path": str(target_path),
                    }
                )
                continue
            copy_results.append(
                _copy_sidecar(
                    kind=kind,
                    source_path=source_path,
                    target_path=target_path,
                    ticket_id=token,
                    overwrite=overwrite,
                )
            )

        copied_count = sum(1 for item in copy_results if item.get("status") == "copied")
        rejected_count = sum(1 for item in copy_results if item.get("status") == "rejected")
        imported = _read_imported_ack_result(resolved_team_dir, token, target)
        poll_result: Dict[str, Any] = {}
        if poll_after_import:
            poll_result = poll_external_background_tickets(
                queue_path=queue_path,
                now_iso=now_iso,
                ack_source_command=f"external-sidecar-sync import {target} {token}",
                result_source_command=f"external-sidecar-sync import {target} {token}",
            )
        rel_targets = {
            kind: _artifact_path_for_team(resolved_team_dir, path)
            for kind, path in expected_paths.items()
        }
        return {
            "ok": rejected_count == 0 and (copied_count > 0 or imported["ack_imported"] or imported["result_imported"]),
            "ticket_id": token,
            "runner_target": target,
            "artifact_root": str(Path(artifact_root).expanduser()),
            "copied_count": copied_count,
            "rejected_count": rejected_count,
            "copy_results": copy_results,
            "target_artifacts": rel_targets,
            "imported": imported,
            "poll_result": poll_result,
            "summary": (
                f"ticket={token} runner={target} copied={copied_count} "
                f"ack={'yes' if imported['ack_imported'] else 'no'} "
                f"result={imported['result_status'] or 'no'}"
            ),
        }
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
