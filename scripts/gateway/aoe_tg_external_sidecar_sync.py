#!/usr/bin/env python3
"""Import external background worker sidecars into a local team dir."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
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
GITHUB_EXTERNAL_IMPORTS_FILENAME = "github_external_imports.json"
GITHUB_EXTERNAL_IMPORTS_VERSION = "2026-04-28.v1"
GITHUB_EXTERNAL_WORKFLOW_FILENAME = "external-background-worker.yml"
GITHUB_EXTERNAL_RUN_TITLE_PREFIX = "external-background"
GITHUB_IMPORT_TERMINAL_FAILURE_REASONS = {
    "gh_run_view_failed",
    "gh_run_view_json_invalid",
    "gh_run_download_failed",
}


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


def default_github_actions_artifact_name(*, ticket_id: str, runner_target: str) -> str:
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        target = "github_runner"
    return f"aoe-external-background-{target}-{token or 'batch'}"


def _default_download_command_runner(command: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )


def _github_run_view_command(
    *,
    gh_bin: str,
    run_id: str,
    repo: str,
) -> List[str]:
    command = [
        _trim(gh_bin, 120) or "gh",
        "run",
        "view",
        run_id,
        "--json",
        "status,conclusion,url,databaseId",
    ]
    repo_token = _trim(repo, 180)
    if repo_token:
        command.extend(["--repo", repo_token])
    return command


def _parse_github_run_view(raw: str) -> Dict[str, Any]:
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("run view JSON is not an object")
    return {
        "status": _trim(parsed.get("status", ""), 32).lower(),
        "conclusion": _trim(parsed.get("conclusion", ""), 32).lower(),
        "url": _trim(parsed.get("url", ""), 500),
        "database_id": _trim(parsed.get("databaseId", ""), 80),
    }


def _github_run_list_command(
    *,
    gh_bin: str,
    workflow: str,
    repo: str,
    limit: int,
) -> List[str]:
    command = [
        _trim(gh_bin, 120) or "gh",
        "run",
        "list",
        "--workflow",
        _trim(workflow, 180) or GITHUB_EXTERNAL_WORKFLOW_FILENAME,
        "--json",
        "databaseId,status,conclusion,url,displayTitle,createdAt,event",
        "--limit",
        str(max(1, min(int(limit or 20), 100))),
    ]
    repo_token = _trim(repo, 180)
    if repo_token:
        command.extend(["--repo", repo_token])
    return command


def _github_external_run_title(*, ticket_id: str, runner_target: str) -> str:
    return f"{GITHUB_EXTERNAL_RUN_TITLE_PREFIX}-{runner_target}-{ticket_id}"


def _parse_github_run_list(raw: str) -> List[Dict[str, Any]]:
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise ValueError("run list JSON is not an array")
    rows: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "run_id": _trim(item.get("databaseId", ""), 80),
                "status": _trim(item.get("status", ""), 32).lower(),
                "conclusion": _trim(item.get("conclusion", ""), 32).lower(),
                "url": _trim(item.get("url", ""), 500),
                "display_title": _trim(item.get("displayTitle", ""), 180),
                "created_at": _trim(item.get("createdAt", ""), 80),
                "event": _trim(item.get("event", ""), 80),
            }
        )
    return rows


def github_external_imports_path(team_dir: Path | str) -> Path:
    return Path(team_dir).expanduser().resolve() / GITHUB_EXTERNAL_IMPORTS_FILENAME


def _empty_github_imports_state() -> Dict[str, Any]:
    return {
        "version": GITHUB_EXTERNAL_IMPORTS_VERSION,
        "updated_at": "",
        "imports": [],
    }


def load_github_external_imports_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _empty_github_imports_state()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_github_imports_state()
    if not isinstance(parsed, dict):
        return _empty_github_imports_state()
    rows: List[Dict[str, Any]] = []
    for item in list(parsed.get("imports") or []):
        if not isinstance(item, dict):
            continue
        ticket_id = _trim(item.get("ticket_id", ""), 96)
        run_id = _trim(item.get("run_id", ""), 80)
        runner_target = normalize_executor_runner_target(item.get("runner_target", "github_runner"))
        if not ticket_id or not run_id or runner_target not in EXTERNAL_SIDECAR_RUNNERS:
            continue
        row = dict(item)
        row["ticket_id"] = ticket_id
        row["run_id"] = run_id
        row["runner_target"] = runner_target
        row["status"] = _trim(row.get("status", "pending"), 32).lower() or "pending"
        row["repo"] = _trim(row.get("repo", ""), 180)
        row["artifact_name"] = _trim(row.get("artifact_name", ""), 180)
        row["gh_bin"] = _trim(row.get("gh_bin", "gh"), 120) or "gh"
        row["run_url"] = _trim(row.get("run_url", ""), 500)
        try:
            row["attempts"] = max(0, int(row.get("attempts", 0) or 0))
        except Exception:
            row["attempts"] = 0
        rows.append(row)
    state = _empty_github_imports_state()
    state["version"] = _trim(parsed.get("version", GITHUB_EXTERNAL_IMPORTS_VERSION), 48) or GITHUB_EXTERNAL_IMPORTS_VERSION
    state["updated_at"] = _trim(parsed.get("updated_at", ""), 64)
    state["imports"] = rows[-200:]
    return state


def save_github_external_imports_state(
    path: Path,
    state: Dict[str, Any],
    *,
    now_iso: Callable[[], str],
) -> None:
    payload = _empty_github_imports_state()
    payload["version"] = _trim(state.get("version", GITHUB_EXTERNAL_IMPORTS_VERSION), 48) or GITHUB_EXTERNAL_IMPORTS_VERSION
    payload["updated_at"] = now_iso()
    payload["imports"] = list(state.get("imports") or [])[-200:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _github_import_record_key(record: Dict[str, Any]) -> str:
    return "|".join(
        [
            _trim(record.get("runner_target", ""), 64),
            _trim(record.get("ticket_id", ""), 96),
            _trim(record.get("run_id", ""), 80),
        ]
    )


def discover_github_external_worker_run(
    *,
    ticket_id: str,
    runner_target: str = "github_runner",
    repo: str = "",
    gh_bin: str = "gh",
    workflow: str = GITHUB_EXTERNAL_WORKFLOW_FILENAME,
    limit: int = 20,
    command_runner: Callable[[List[str]], subprocess.CompletedProcess[str]] = _default_download_command_runner,
) -> Dict[str, Any]:
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        return {"ok": False, "reason": "unsupported_runner", "runner_target": target}
    if not token:
        return {"ok": False, "reason": "ticket_id_required", "runner_target": target}
    expected_title = _github_external_run_title(ticket_id=token, runner_target=target)
    command = _github_run_list_command(
        gh_bin=gh_bin,
        workflow=workflow,
        repo=repo,
        limit=limit,
    )
    proc = command_runner(command)
    returncode = int(getattr(proc, "returncode", 1) or 0)
    stdout = _trim(getattr(proc, "stdout", ""), 12000)
    stderr = _trim(getattr(proc, "stderr", ""), 4000)
    if returncode != 0:
        return {
            "ok": False,
            "reason": "gh_run_list_failed",
            "ticket_id": token,
            "runner_target": target,
            "repo": _trim(repo, 180),
            "list_command": command,
            "list_returncode": returncode,
            "list_stdout": stdout,
            "list_stderr": stderr,
        }
    try:
        rows = _parse_github_run_list(stdout)
    except Exception as exc:
        return {
            "ok": False,
            "reason": "gh_run_list_json_invalid",
            "ticket_id": token,
            "runner_target": target,
            "repo": _trim(repo, 180),
            "list_command": command,
            "list_stdout": stdout,
            "list_stderr": stderr,
            "error": type(exc).__name__,
        }
    candidates = [
        row
        for row in rows
        if row.get("run_id")
        and (
            row.get("display_title") == expected_title
            or (token in str(row.get("display_title", "")) and target in str(row.get("display_title", "")))
        )
    ]
    if not candidates:
        return {
            "ok": False,
            "reason": "github_run_not_found",
            "ticket_id": token,
            "runner_target": target,
            "repo": _trim(repo, 180),
            "expected_display_title": expected_title,
            "list_command": command,
            "runs_seen": len(rows),
        }
    selected = candidates[0]
    return {
        "ok": True,
        "ticket_id": token,
        "runner_target": target,
        "repo": _trim(repo, 180),
        "workflow": _trim(workflow, 180) or GITHUB_EXTERNAL_WORKFLOW_FILENAME,
        "expected_display_title": expected_title,
        "run": selected,
        "run_id": selected.get("run_id", ""),
        "run_url": selected.get("url", ""),
        "list_command": command,
        "matched_count": len(candidates),
        "runs_seen": len(rows),
    }


def schedule_github_external_sidecar_import(
    *,
    team_dir: Path | str,
    run_id: str,
    ticket_id: str,
    runner_target: str = "github_runner",
    artifact_name: str = "",
    repo: str = "",
    gh_bin: str = "gh",
    run_url: str = "",
    now_iso: Callable[[], str] = _now_iso,
) -> Dict[str, Any]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    run_token = _trim(run_id, 80)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        return {"ok": False, "reason": "unsupported_runner", "runner_target": target}
    if not token:
        return {"ok": False, "reason": "ticket_id_required", "runner_target": target}
    if not run_token:
        return {"ok": False, "reason": "run_id_required", "ticket_id": token, "runner_target": target}

    path = github_external_imports_path(resolved_team_dir)
    state = load_github_external_imports_state(path)
    now = now_iso()
    record = {
        "status": "pending",
        "ticket_id": token,
        "runner_target": target,
        "run_id": run_token,
        "run_url": _trim(run_url, 500),
        "repo": _trim(repo, 180),
        "artifact_name": _trim(artifact_name, 180) or default_github_actions_artifact_name(ticket_id=token, runner_target=target),
        "gh_bin": _trim(gh_bin, 120) or "gh",
        "attempts": 0,
        "created_at": now,
        "updated_at": now,
        "last_reason": "",
        "last_summary": "",
    }
    key = _github_import_record_key(record)
    rows = []
    replaced = False
    for existing in list(state.get("imports") or []):
        if _github_import_record_key(existing) == key:
            merged = dict(existing)
            merged.update(record)
            merged["created_at"] = _trim(existing.get("created_at", ""), 64) or now
            rows.append(merged)
            replaced = True
        else:
            rows.append(existing)
    if not replaced:
        rows.append(record)
    state["imports"] = rows
    save_github_external_imports_state(path, state, now_iso=now_iso)
    return {
        "ok": True,
        "scheduled": not replaced,
        "replaced": replaced,
        "state_path": str(path),
        "record": record,
    }


def download_and_import_github_external_sidecars(
    *,
    team_dir: Path | str,
    run_id: str,
    ticket_id: str,
    runner_target: str = "github_runner",
    artifact_name: str = "",
    repo: str = "",
    gh_bin: str = "gh",
    overwrite: bool = False,
    poll_after_import: bool = False,
    now_iso: Callable[[], str] = _now_iso,
    command_runner: Callable[[List[str]], subprocess.CompletedProcess[str]] = _default_download_command_runner,
) -> Dict[str, Any]:
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        return {"ok": False, "reason": "unsupported_runner", "runner_target": target}
    run_token = _trim(run_id, 80)
    if not run_token:
        return {"ok": False, "reason": "run_id_required", "ticket_id": token, "runner_target": target}
    name = _trim(artifact_name, 180) or default_github_actions_artifact_name(ticket_id=token, runner_target=target)

    with tempfile.TemporaryDirectory(prefix="aoe-gh-sidecars-") as temp_dir:
        download_dir = Path(temp_dir) / "download"
        download_dir.mkdir(parents=True, exist_ok=True)
        command = [
            _trim(gh_bin, 120) or "gh",
            "run",
            "download",
            run_token,
            "--name",
            name,
            "--dir",
            str(download_dir),
        ]
        repo_token = _trim(repo, 180)
        if repo_token:
            command.extend(["--repo", repo_token])
        proc = command_runner(command)
        returncode = int(getattr(proc, "returncode", 1) or 0)
        stdout = _trim(getattr(proc, "stdout", ""), 4000)
        stderr = _trim(getattr(proc, "stderr", ""), 4000)
        if returncode != 0:
            return {
                "ok": False,
                "reason": "gh_run_download_failed",
                "ticket_id": token,
                "runner_target": target,
                "run_id": run_token,
                "artifact_name": name,
                "download_command": command,
                "download_returncode": returncode,
                "download_stdout": stdout,
                "download_stderr": stderr,
            }
        imported = import_external_background_sidecars(
            team_dir=team_dir,
            artifact_root=download_dir,
            ticket_id=token,
            runner_target=target,
            overwrite=overwrite,
            poll_after_import=poll_after_import,
            now_iso=now_iso,
        )
        imported["github_download"] = {
            "run_id": run_token,
            "artifact_name": name,
            "repo": repo_token,
            "download_command": command,
            "download_returncode": returncode,
            "download_stdout": stdout,
            "download_stderr": stderr,
        }
        return imported


def watch_and_import_github_external_sidecars(
    *,
    team_dir: Path | str,
    run_id: str,
    ticket_id: str,
    runner_target: str = "github_runner",
    artifact_name: str = "",
    repo: str = "",
    gh_bin: str = "gh",
    overwrite: bool = False,
    poll_after_import: bool = False,
    timeout_sec: int = 900,
    interval_sec: float = 10.0,
    now_iso: Callable[[], str] = _now_iso,
    command_runner: Callable[[List[str]], subprocess.CompletedProcess[str]] = _default_download_command_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    token = _trim(ticket_id, 96)
    target = normalize_executor_runner_target(runner_target)
    if target not in EXTERNAL_SIDECAR_RUNNERS:
        return {"ok": False, "reason": "unsupported_runner", "runner_target": target}
    run_token = _trim(run_id, 80)
    if not run_token:
        return {"ok": False, "reason": "run_id_required", "ticket_id": token, "runner_target": target}
    if not token:
        return {"ok": False, "reason": "ticket_id_required", "runner_target": target, "run_id": run_token}

    timeout = max(0.0, float(timeout_sec))
    interval = max(0.0, float(interval_sec))
    deadline = time.monotonic() + timeout
    attempts = 0
    latest_view: Dict[str, Any] = {}
    latest_command: List[str] = []
    repo_token = _trim(repo, 180)

    while True:
        attempts += 1
        latest_command = _github_run_view_command(gh_bin=gh_bin, run_id=run_token, repo=repo_token)
        proc = command_runner(latest_command)
        returncode = int(getattr(proc, "returncode", 1) or 0)
        stdout = _trim(getattr(proc, "stdout", ""), 4000)
        stderr = _trim(getattr(proc, "stderr", ""), 4000)
        if returncode != 0:
            return {
                "ok": False,
                "reason": "gh_run_view_failed",
                "ticket_id": token,
                "runner_target": target,
                "run_id": run_token,
                "repo": repo_token,
                "attempts": attempts,
                "view_command": latest_command,
                "view_returncode": returncode,
                "view_stdout": stdout,
                "view_stderr": stderr,
            }
        try:
            latest_view = _parse_github_run_view(stdout)
        except Exception as exc:
            return {
                "ok": False,
                "reason": "gh_run_view_json_invalid",
                "ticket_id": token,
                "runner_target": target,
                "run_id": run_token,
                "repo": repo_token,
                "attempts": attempts,
                "view_command": latest_command,
                "view_stdout": stdout,
                "view_stderr": stderr,
                "error": type(exc).__name__,
            }
        if latest_view.get("status") == "completed":
            imported = download_and_import_github_external_sidecars(
                team_dir=team_dir,
                run_id=run_token,
                ticket_id=token,
                runner_target=target,
                artifact_name=artifact_name,
                repo=repo_token,
                gh_bin=gh_bin,
                overwrite=overwrite,
                poll_after_import=poll_after_import,
                now_iso=now_iso,
                command_runner=command_runner,
            )
            imported["github_watch"] = {
                "run_id": run_token,
                "repo": repo_token,
                "attempts": attempts,
                "timeout_sec": timeout,
                "interval_sec": interval,
                "view_command": latest_command,
                "view": latest_view,
                "status": latest_view.get("status", ""),
                "conclusion": latest_view.get("conclusion", ""),
                "url": latest_view.get("url", ""),
            }
            return imported

        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "reason": "github_run_timeout",
                "ticket_id": token,
                "runner_target": target,
                "run_id": run_token,
                "repo": repo_token,
                "attempts": attempts,
                "timeout_sec": timeout,
                "interval_sec": interval,
                "view_command": latest_command,
                "view": latest_view,
                "status": latest_view.get("status", ""),
                "conclusion": latest_view.get("conclusion", ""),
                "url": latest_view.get("url", ""),
            }
        wait_for = min(interval if interval > 0 else 0.1, max(0.0, deadline - time.monotonic()))
        if wait_for > 0:
            sleeper(wait_for)


def drain_scheduled_github_external_sidecar_imports(
    *,
    team_dir: Path | str,
    max_items: int = 1,
    overwrite: bool = False,
    poll_after_import: bool = True,
    timeout_sec: int = 0,
    interval_sec: float = 0.0,
    now_iso: Callable[[], str] = _now_iso,
    command_runner: Callable[[List[str]], subprocess.CompletedProcess[str]] = _default_download_command_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    path = github_external_imports_path(resolved_team_dir)
    state = load_github_external_imports_state(path)
    rows = list(state.get("imports") or [])
    processed: List[Dict[str, Any]] = []
    limit = max(1, int(max_items or 1))
    now = now_iso()
    for row in rows:
        if len(processed) >= limit:
            break
        if _trim(row.get("status", ""), 32).lower() not in {"pending", "retry"}:
            continue
        result = watch_and_import_github_external_sidecars(
            team_dir=resolved_team_dir,
            run_id=_trim(row.get("run_id", ""), 80),
            ticket_id=_trim(row.get("ticket_id", ""), 96),
            runner_target=_trim(row.get("runner_target", "github_runner"), 64),
            artifact_name=_trim(row.get("artifact_name", ""), 180),
            repo=_trim(row.get("repo", ""), 180),
            gh_bin=_trim(row.get("gh_bin", "gh"), 120) or "gh",
            overwrite=overwrite,
            poll_after_import=poll_after_import,
            timeout_sec=timeout_sec,
            interval_sec=interval_sec,
            now_iso=now_iso,
            command_runner=command_runner,
            sleeper=sleeper,
        )
        row["attempts"] = int(row.get("attempts", 0) or 0) + 1
        row["updated_at"] = now
        row["last_reason"] = _trim(result.get("reason", ""), 160)
        row["last_summary"] = _trim(result.get("summary", ""), 500)
        row["last_ok"] = bool(result.get("ok"))
        if result.get("ok"):
            row["status"] = "completed"
            row["completed_at"] = now
        elif str(result.get("reason", "")).strip() == "github_run_timeout":
            row["status"] = "pending"
        elif str(result.get("reason", "")).strip() in GITHUB_IMPORT_TERMINAL_FAILURE_REASONS:
            row["status"] = "failed"
            row["failed_at"] = now
        else:
            row["status"] = "failed"
            row["failed_at"] = now
        processed.append(
            {
                "ticket_id": row.get("ticket_id", ""),
                "runner_target": row.get("runner_target", ""),
                "run_id": row.get("run_id", ""),
                "status": row.get("status", ""),
                "reason": row.get("last_reason", ""),
                "result": result,
            }
        )
    state["imports"] = rows
    save_github_external_imports_state(path, state, now_iso=now_iso)
    failed_count = sum(1 for item in processed if item.get("status") == "failed")
    return {
        "ok": failed_count == 0,
        "state_path": str(path),
        "processed_count": len(processed),
        "completed_count": sum(1 for item in processed if item.get("status") == "completed"),
        "pending_count": sum(1 for item in rows if _trim(item.get("status", ""), 32).lower() in {"pending", "retry"}),
        "failed_count": failed_count,
        "processed": processed,
    }


def discover_schedule_and_import_github_external_sidecars(
    *,
    team_dir: Path | str,
    ticket_id: str,
    runner_target: str = "github_runner",
    artifact_name: str = "",
    repo: str = "",
    gh_bin: str = "gh",
    workflow: str = GITHUB_EXTERNAL_WORKFLOW_FILENAME,
    list_limit: int = 20,
    overwrite: bool = False,
    poll_after_import: bool = True,
    timeout_sec: int = 900,
    interval_sec: float = 10.0,
    now_iso: Callable[[], str] = _now_iso,
    command_runner: Callable[[List[str]], subprocess.CompletedProcess[str]] = _default_download_command_runner,
    sleeper: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    discovered = discover_github_external_worker_run(
        ticket_id=ticket_id,
        runner_target=runner_target,
        repo=repo,
        gh_bin=gh_bin,
        workflow=workflow,
        limit=list_limit,
        command_runner=command_runner,
    )
    if not discovered.get("ok"):
        return {
            "ok": False,
            "reason": discovered.get("reason", "github_run_discovery_failed"),
            "discover": discovered,
        }
    run = discovered.get("run") if isinstance(discovered.get("run"), dict) else {}
    scheduled = schedule_github_external_sidecar_import(
        team_dir=team_dir,
        run_id=str(discovered.get("run_id", "")),
        ticket_id=ticket_id,
        runner_target=runner_target,
        artifact_name=artifact_name,
        repo=repo,
        gh_bin=gh_bin,
        run_url=str(run.get("url", "")),
        now_iso=now_iso,
    )
    if not scheduled.get("ok"):
        return {
            "ok": False,
            "reason": scheduled.get("reason", "github_import_schedule_failed"),
            "discover": discovered,
            "schedule": scheduled,
        }
    drained = drain_scheduled_github_external_sidecar_imports(
        team_dir=team_dir,
        max_items=1,
        overwrite=overwrite,
        poll_after_import=poll_after_import,
        timeout_sec=timeout_sec,
        interval_sec=interval_sec,
        now_iso=now_iso,
        command_runner=command_runner,
        sleeper=sleeper,
    )
    return {
        "ok": bool(drained.get("ok")),
        "reason": "" if drained.get("ok") else "github_import_drain_failed",
        "discover": discovered,
        "schedule": scheduled,
        "drain": drained,
    }
