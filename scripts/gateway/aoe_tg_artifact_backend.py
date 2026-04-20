#!/usr/bin/env python3
"""Artifact persistence backend seam for control-plane sidecars."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from aoe_tg_runtime_core import (
    action_audit_path as runtime_action_audit_path,
    context_pack_dir as runtime_context_pack_dir,
    context_pack_path as runtime_context_pack_path,
    harness_authoring_dir as runtime_harness_authoring_dir,
    harness_authoring_plan_path as runtime_harness_authoring_plan_path,
    model_endpoint_registry_path as runtime_model_endpoint_registry_path,
    model_routing_policy_path as runtime_model_routing_policy_path,
    recovery_summary_dir as runtime_recovery_summary_dir,
)


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _safe_token(raw: Any, default: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(raw or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return token or default


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def load_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = str(line or "").strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows


def append_jsonl_row(path: Path, row: Dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return False
    return True


def rewrite_jsonl_rows(path: Path, rows: List[Dict[str, Any]]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    tmp_path = None
    try:
        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=path.name + ".tmp.",
                suffix=".jsonl",
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return False
    return True


@dataclass(frozen=True)
class ArtifactBackendDescriptor:
    backend_kind: str
    team_dir: str
    context_pack_dir: str
    harness_authoring_dir: str
    action_audit_path: str
    recovery_summary_dir: str
    model_endpoint_registry_path: str
    model_routing_policy_path: str
    summary: str


class FileSystemArtifactBackend:
    backend_kind = "filesystem"

    def __init__(self, team_dir: Path | str) -> None:
        self.team_dir = Path(team_dir).expanduser().resolve()

    def descriptor(self) -> Dict[str, Any]:
        descriptor = ArtifactBackendDescriptor(
            backend_kind=self.backend_kind,
            team_dir=str(self.team_dir),
            context_pack_dir=str(runtime_context_pack_dir(self.team_dir)),
            harness_authoring_dir=str(self.harness_authoring_dir()),
            action_audit_path=str(self.action_audit_path()),
            recovery_summary_dir=str(self.recovery_summary_dir()),
            model_endpoint_registry_path=str(self.model_endpoint_registry_path()),
            model_routing_policy_path=str(self.model_routing_policy_path()),
            summary=(
                "backend=filesystem "
                f"context={runtime_context_pack_dir(self.team_dir).name} "
                f"harness={self.harness_authoring_dir().name} "
                f"audit={self.action_audit_path().name} "
                f"recovery={self.recovery_summary_dir().name} "
                f"model={self.model_routing_policy_path().name}"
            ),
        )
        return asdict(descriptor)

    def context_pack_path(self, *, request_id: str, profile: str) -> Path:
        return runtime_context_pack_path(self.team_dir, request_id=request_id, profile=profile)

    def load_context_pack(self, *, request_id: str, profile: str) -> Dict[str, Any]:
        return load_json_file(self.context_pack_path(request_id=request_id, profile=profile))

    def write_context_pack(self, *, request_id: str, profile: str, payload: Dict[str, Any]) -> Path:
        return _write_json(self.context_pack_path(request_id=request_id, profile=profile), payload)

    def harness_authoring_dir(self) -> Path:
        return runtime_harness_authoring_dir(self.team_dir)

    def harness_authoring_plan_path(
        self,
        *,
        request_id: str = "",
        task_ref: str = "",
        filename: str = "",
    ) -> Path:
        return runtime_harness_authoring_plan_path(
            self.team_dir,
            request_id=request_id,
            task_ref=task_ref,
            filename=filename,
        )

    def load_harness_authoring_plan(
        self,
        *,
        request_id: str = "",
        task_ref: str = "",
        filename: str = "",
    ) -> Dict[str, Any]:
        return load_json_file(
            self.harness_authoring_plan_path(request_id=request_id, task_ref=task_ref, filename=filename)
        )

    def write_harness_authoring_plan(
        self,
        *,
        payload: Dict[str, Any],
        request_id: str = "",
        task_ref: str = "",
        filename: str = "",
    ) -> Path:
        return _write_json(
            self.harness_authoring_plan_path(request_id=request_id, task_ref=task_ref, filename=filename),
            payload,
        )

    def action_audit_path(self) -> Path:
        return runtime_action_audit_path(self.team_dir)

    def load_action_audit_rows(self) -> List[Dict[str, Any]]:
        return load_jsonl_rows(self.action_audit_path())

    def append_action_audit_row(self, row: Dict[str, Any]) -> bool:
        return append_jsonl_row(self.action_audit_path(), row)

    def rewrite_action_audit_rows(self, rows: List[Dict[str, Any]]) -> bool:
        return rewrite_jsonl_rows(self.action_audit_path(), rows)

    def recovery_summary_dir(self) -> Path:
        return runtime_recovery_summary_dir(self.team_dir)

    def model_endpoint_registry_path(self) -> Path:
        return runtime_model_endpoint_registry_path(self.team_dir)

    def load_model_endpoint_registry(self) -> Dict[str, Any]:
        return load_json_file(self.model_endpoint_registry_path())

    def write_model_endpoint_registry(self, payload: Dict[str, Any]) -> Path:
        return _write_json(self.model_endpoint_registry_path(), payload)

    def model_routing_policy_path(self) -> Path:
        return runtime_model_routing_policy_path(self.team_dir)

    def load_model_routing_policy(self) -> Dict[str, Any]:
        return load_json_file(self.model_routing_policy_path())

    def write_model_routing_policy(self, payload: Dict[str, Any]) -> Path:
        return _write_json(self.model_routing_policy_path(), payload)

    def write_recovery_summary(
        self,
        *,
        markdown: str,
        payload: str,
        output_dir: Path | str | None = None,
        stamp: str = "",
        write_timestamped_copy: bool = True,
    ) -> Tuple[Path, Path]:
        target_dir = Path(output_dir).expanduser().resolve() if output_dir else self.recovery_summary_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        latest_md = target_dir / "latest.md"
        latest_json = target_dir / "latest.json"
        latest_md.write_text(markdown, encoding="utf-8")
        latest_json.write_text(payload, encoding="utf-8")
        safe_stamp = _trim(stamp, 128)
        if write_timestamped_copy and safe_stamp:
            (target_dir / f"{safe_stamp}.md").write_text(markdown, encoding="utf-8")
            (target_dir / f"{safe_stamp}.json").write_text(payload, encoding="utf-8")
        return latest_md, latest_json

    def external_background_handoff_path(self, *, ticket_id: str, runner_target: str) -> Path:
        return self._external_background_path(kind="handoffs", ticket_id=ticket_id, runner_target=runner_target)

    def external_background_result_path(self, *, ticket_id: str, runner_target: str) -> Path:
        return self._external_background_path(kind="results", ticket_id=ticket_id, runner_target=runner_target)

    def external_background_ack_path(self, *, ticket_id: str, runner_target: str) -> Path:
        return self._external_background_path(kind="acks", ticket_id=ticket_id, runner_target=runner_target)

    def write_external_background_artifact(
        self,
        *,
        kind: str,
        ticket_id: str,
        runner_target: str,
        payload: Dict[str, Any],
    ) -> Path:
        path = self._external_background_path(kind=kind, ticket_id=ticket_id, runner_target=runner_target)
        return _write_json(path, payload)

    def read_external_background_artifact(
        self,
        *,
        kind: str,
        ticket_id: str,
        runner_target: str,
    ) -> Dict[str, Any]:
        path = self._external_background_path(kind=kind, ticket_id=ticket_id, runner_target=runner_target)
        return load_json_file(path)

    def artifact_path(self, relative_path: str) -> Path:
        token = _trim(relative_path, 400).strip("/")
        return self.team_dir / token if token else self.team_dir

    def write_json_artifact(self, *, relative_path: str, payload: Dict[str, Any]) -> Path:
        return _write_json(self.artifact_path(relative_path), payload)

    def read_json_artifact(self, *, relative_path: str) -> Dict[str, Any]:
        return load_json_file(self.artifact_path(relative_path))

    def relative_artifact_path(self, artifact_path: Path | str) -> str:
        resolved = Path(artifact_path).expanduser().resolve()
        try:
            return str(resolved.relative_to(self.team_dir)).strip()
        except Exception:
            return str(resolved).strip()

    def _external_background_path(self, *, kind: str, ticket_id: str, runner_target: str) -> Path:
        dirname = {
            "handoffs": "background_run_handoffs",
            "results": "background_run_results",
            "acks": "background_run_acks",
        }.get(str(kind or "").strip().lower(), str(kind or "").strip())
        ticket_token = _safe_token(ticket_id, "run")
        runner_token = _safe_token(runner_target, "external")
        return self.team_dir / dirname / f"{runner_token}-{ticket_token}.json"


def artifact_backend(team_dir: Path | str, *, backend_kind: str = "") -> FileSystemArtifactBackend:
    requested = _trim(backend_kind or os.environ.get("AOE_ARTIFACT_BACKEND"), 64).lower()
    if requested not in {"", "filesystem"}:
        return FileSystemArtifactBackend(team_dir)
    return FileSystemArtifactBackend(team_dir)
