#!/usr/bin/env python3
"""Runtime entrypoint for external background worker handoffs."""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

from aoe_tg_artifact_backend import artifact_backend
from aoe_tg_background_runs import background_runs_state_path, list_background_run_tickets
from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import (
    emit_external_background_ack,
    emit_external_background_result,
    external_background_ack_path,
    external_background_handoff_path,
    external_background_result_path,
)
from aoe_tg_request_contract import normalize_background_launch_spec_snapshot


EXTERNAL_WORKER_RUNNERS = {"github_runner", "remote_worker"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _safe_token(raw: Any, default: str = "run") -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(raw or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return token or default


def _artifact_path_for_team(team_dir: Path, artifact_path: Path) -> str:
    return artifact_backend(team_dir).relative_artifact_path(artifact_path)


def external_background_log_path(team_dir: Path, ticket_id: str, runner_target: str) -> Path:
    runner_token = _safe_token(runner_target, "external")
    ticket_token = _safe_token(ticket_id, "run")
    return Path(team_dir).expanduser().resolve() / "background_run_logs" / f"{runner_token}-{ticket_token}.log"


def _default_worker_id(runner_target: str) -> str:
    hostname = _safe_token(socket.gethostname(), "host")
    return f"external-worker:{_safe_token(runner_target, 'external')}:{hostname}"[:96]


def _safe_timeout(raw: int | float | str | None, default: int = 900) -> int:
    try:
        value = int(float(raw if raw is not None else default))
    except Exception:
        value = default
    return max(1, min(value, 86400))


def _decode_output(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _write_worker_log(
    *,
    log_path: Path,
    ticket_id: str,
    runner_target: str,
    command_argv: List[str],
    command_cwd: str,
    status: str,
    exit_code: int,
    reason: str,
    stdout: str = "",
    stderr: str = "",
    started_at: str = "",
    finished_at: str = "",
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"ticket_id={ticket_id}",
        f"runner_target={runner_target}",
        f"status={status}",
        f"exit_code={exit_code}",
        f"reason={reason}",
        f"started_at={started_at}",
        f"finished_at={finished_at}",
        f"cwd={command_cwd}",
        "command_argv:",
    ]
    lines.extend(f"- {item}" for item in command_argv)
    lines.extend(["", "stdout:", stdout.rstrip(), "", "stderr:", stderr.rstrip(), ""])
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return _artifact_path_for_team(log_path.parent.parent, log_path)


def _external_worker_env(team_dir: Path, launch_spec: Dict[str, Any]) -> Dict[str, str]:
    env = dict(os.environ)
    env["AOE_TEAM_DIR"] = str(team_dir)
    env.setdefault("AOE_STATE_DIR", str(team_dir))
    env.setdefault("AOE_ORCH_ALIAS", _trim(launch_spec.get("project_key", ""), 64))
    return env


def _select_external_handoff(
    *,
    team_dir: Path,
    runner_target: str,
    ticket_id: str = "",
) -> Dict[str, Any]:
    queue_path = background_runs_state_path(team_dir)
    target = normalize_executor_runner_target(runner_target)
    token = _trim(ticket_id, 96)
    if target not in EXTERNAL_WORKER_RUNNERS:
        return {"reason": "unsupported_runner"}

    for row in list_background_run_tickets(queue_path, statuses=["running"], runner_target=target):
        row_ticket_id = _trim(row.get("ticket_id", ""), 96)
        if not row_ticket_id or (token and row_ticket_id != token):
            continue
        handoff_path = external_background_handoff_path(team_dir, row_ticket_id, target)
        ack_path = external_background_ack_path(team_dir, row_ticket_id, target)
        result_path = external_background_result_path(team_dir, row_ticket_id, target)
        if result_path.exists():
            continue
        if ack_path.exists():
            continue
        if not handoff_path.exists():
            continue
        handoff_payload = artifact_backend(team_dir).read_external_background_artifact(
            kind="handoffs",
            ticket_id=row_ticket_id,
            runner_target=target,
        )
        if not isinstance(handoff_payload, dict):
            continue
        launch_spec = normalize_background_launch_spec_snapshot(handoff_payload.get("launch_spec"))
        return {
            "row": row,
            "ticket_id": row_ticket_id,
            "runner_target": target,
            "queue_path": queue_path,
            "handoff_path": handoff_path,
            "ack_path": ack_path,
            "result_path": result_path,
            "launch_spec": launch_spec,
        }
    return {"reason": "no_handoff_candidate"}


def _validate_launch_spec(
    *,
    launch_spec: Dict[str, Any],
    runner_target: str,
) -> Dict[str, Any]:
    spec_runner = normalize_executor_runner_target(launch_spec.get("runner_target", ""))
    command_argv = [_trim(item, 240) for item in list(launch_spec.get("command_argv") or []) if _trim(item, 240)]
    command_cwd = _trim(launch_spec.get("command_cwd", ""), 240) or _trim(launch_spec.get("project_root", ""), 240)
    if spec_runner and spec_runner != runner_target:
        return {"ok": False, "reason": "launch_spec_runner_mismatch", "command_argv": command_argv, "command_cwd": command_cwd}
    if not bool(launch_spec.get("externalizable", False)):
        return {"ok": False, "reason": "launch_spec_not_externalizable", "command_argv": command_argv, "command_cwd": command_cwd}
    if not command_argv:
        return {"ok": False, "reason": "launch_spec_missing_command", "command_argv": command_argv, "command_cwd": command_cwd}
    if not command_cwd:
        return {"ok": False, "reason": "command_cwd_missing", "command_argv": command_argv, "command_cwd": command_cwd}
    cwd_path = Path(command_cwd).expanduser()
    if not cwd_path.exists():
        return {"ok": False, "reason": "command_cwd_missing", "command_argv": command_argv, "command_cwd": command_cwd}
    if not cwd_path.is_dir():
        return {"ok": False, "reason": "command_cwd_not_directory", "command_argv": command_argv, "command_cwd": command_cwd}
    return {"ok": True, "reason": "", "command_argv": command_argv, "command_cwd": str(cwd_path)}


def _execute_external_command(
    *,
    team_dir: Path,
    ticket_id: str,
    runner_target: str,
    launch_spec: Dict[str, Any],
    log_path: Path,
    timeout_sec: int,
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    validation = _validate_launch_spec(launch_spec=launch_spec, runner_target=runner_target)
    command_argv = list(validation.get("command_argv") or [])
    command_cwd = _trim(validation.get("command_cwd", ""), 240)
    started_at = now_iso()
    if not validation.get("ok"):
        reason = _trim(validation.get("reason", ""), 160) or "launch_spec_invalid"
        log_artifact = _write_worker_log(
            log_path=log_path,
            ticket_id=ticket_id,
            runner_target=runner_target,
            command_argv=command_argv,
            command_cwd=command_cwd,
            status="failed",
            exit_code=1,
            reason=reason,
            started_at=started_at,
            finished_at=now_iso(),
        )
        return {
            "status": "failed",
            "exit_code": 1,
            "reason": reason,
            "summary": reason,
            "log_artifact": log_artifact,
        }

    try:
        proc = subprocess.run(
            command_argv,
            cwd=command_cwd,
            env=_external_worker_env(team_dir, launch_spec),
            capture_output=True,
            text=True,
            check=False,
            timeout=_safe_timeout(timeout_sec),
        )
        exit_code = int(proc.returncode)
        status = "completed" if exit_code == 0 else "failed"
        reason = "exit_code_0" if exit_code == 0 else f"exit_code_{exit_code}"
        log_artifact = _write_worker_log(
            log_path=log_path,
            ticket_id=ticket_id,
            runner_target=runner_target,
            command_argv=command_argv,
            command_cwd=command_cwd,
            status=status,
            exit_code=exit_code,
            reason=reason,
            stdout=proc.stdout,
            stderr=proc.stderr,
            started_at=started_at,
            finished_at=now_iso(),
        )
        return {
            "status": status,
            "exit_code": exit_code,
            "reason": reason,
            "summary": f"external command exited with {exit_code}",
            "log_artifact": log_artifact,
        }
    except subprocess.TimeoutExpired as exc:
        log_artifact = _write_worker_log(
            log_path=log_path,
            ticket_id=ticket_id,
            runner_target=runner_target,
            command_argv=command_argv,
            command_cwd=command_cwd,
            status="failed",
            exit_code=124,
            reason="command_timeout",
            stdout=_decode_output(exc.stdout),
            stderr=_decode_output(exc.stderr),
            started_at=started_at,
            finished_at=now_iso(),
        )
        return {
            "status": "failed",
            "exit_code": 124,
            "reason": "command_timeout",
            "summary": f"external command timed out after {_safe_timeout(timeout_sec)}s",
            "log_artifact": log_artifact,
        }
    except Exception as exc:
        reason = f"command_launch_error:{type(exc).__name__}"[:160]
        log_artifact = _write_worker_log(
            log_path=log_path,
            ticket_id=ticket_id,
            runner_target=runner_target,
            command_argv=command_argv,
            command_cwd=command_cwd,
            status="failed",
            exit_code=1,
            reason=reason,
            stderr=str(exc),
            started_at=started_at,
            finished_at=now_iso(),
        )
        return {
            "status": "failed",
            "exit_code": 1,
            "reason": reason,
            "summary": str(exc)[:240],
            "log_artifact": log_artifact,
        }


def run_external_background_worker_once(
    *,
    team_dir: Path | str,
    runner_target: str,
    ticket_id: str = "",
    worker_id: str = "",
    timeout_sec: int = 900,
    now_iso: Callable[[], str] = _now_iso,
) -> Dict[str, Any]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    target = normalize_executor_runner_target(runner_target)
    candidate = _select_external_handoff(
        team_dir=resolved_team_dir,
        runner_target=target,
        ticket_id=ticket_id,
    )
    if "ticket_id" not in candidate:
        return {
            "processed": False,
            "status": "idle",
            "runner_target": target,
            "ticket_id": _trim(ticket_id, 96),
            "reason": _trim(candidate.get("reason", ""), 160) or "no_handoff_candidate",
        }

    queue_path = candidate["queue_path"]
    token = str(candidate["ticket_id"])
    handoff_artifact = _artifact_path_for_team(resolved_team_dir, candidate["handoff_path"])
    ack = emit_external_background_ack(
        queue_path=queue_path,
        ticket_id=token,
        runner_target=target,
        now_iso=now_iso,
        worker_id=_trim(worker_id, 96) or _default_worker_id(target),
        summary="external worker accepted handoff",
        evidence_artifacts=[handoff_artifact],
    )
    log_path = external_background_log_path(resolved_team_dir, token, target)
    execution = _execute_external_command(
        team_dir=resolved_team_dir,
        ticket_id=token,
        runner_target=target,
        launch_spec=dict(candidate.get("launch_spec") or {}),
        log_path=log_path,
        timeout_sec=timeout_sec,
        now_iso=now_iso,
    )
    status = str(execution.get("status", "failed")).strip().lower()
    if status not in {"completed", "failed"}:
        status = "failed"
    try:
        exit_code = int(execution.get("exit_code", 1))
    except Exception:
        exit_code = 1
    reason = _trim(execution.get("reason", ""), 160) or f"exit_code_{exit_code}"
    log_artifact = _trim(execution.get("log_artifact", ""), 240)
    evidence_artifacts = [log_artifact] if log_artifact else []
    evidence_bundle = (
        f"status={status} | outcome=external_worker_exit_code | exit_code={exit_code}"
        f" | log={log_artifact or '-'}"
    )
    result = emit_external_background_result(
        queue_path=queue_path,
        ticket_id=token,
        runner_target=target,
        now_iso=now_iso,
        status=status,
        reason=reason,
        summary=_trim(execution.get("summary", ""), 240) or reason,
        evidence_bundle=evidence_bundle,
        evidence_artifacts=evidence_artifacts,
    )
    return {
        "processed": True,
        "status": status,
        "runner_target": target,
        "ticket_id": token,
        "exit_code": exit_code,
        "reason": reason,
        "ack_artifact": _trim((ack.get("artifact_path") if isinstance(ack, dict) else ""), 240),
        "result_artifact": _trim((result.get("artifact_path") if isinstance(result, dict) else ""), 240),
        "log_artifact": log_artifact,
    }


def run_external_background_worker_batch(
    *,
    team_dir: Path | str,
    runner_target: str,
    ticket_id: str = "",
    worker_id: str = "",
    timeout_sec: int = 900,
    max_items: int = 1,
    now_iso: Callable[[], str] = _now_iso,
) -> Dict[str, Any]:
    target = normalize_executor_runner_target(runner_target)
    limit = max(1, min(int(max_items or 1), 100))
    results: List[Dict[str, Any]] = []
    for _ in range(limit):
        result = run_external_background_worker_once(
            team_dir=team_dir,
            runner_target=target,
            ticket_id=ticket_id,
            worker_id=worker_id,
            timeout_sec=timeout_sec,
            now_iso=now_iso,
        )
        results.append(result)
        if not result.get("processed") or ticket_id:
            break
    processed = [item for item in results if item.get("processed")]
    failed = [item for item in processed if item.get("status") == "failed"]
    return {
        "runner_target": target,
        "processed_count": len(processed),
        "failed_count": len(failed),
        "results": results,
    }
