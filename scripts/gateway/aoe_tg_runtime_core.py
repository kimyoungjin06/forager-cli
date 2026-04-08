#!/usr/bin/env python3
"""Runtime core helpers for gateway path resolution and state persistence."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Optional


def _normalize_slot_limit(raw: Any, default: int = 1) -> int:
    try:
        value = int(raw or default)
    except Exception:
        value = int(default)
    return max(1, min(value, 32))


def _normalize_slot_limits_map(raw: Any, default: int = 1) -> Dict[str, int]:
    source = raw if isinstance(raw, dict) else {}
    normalized: Dict[str, int] = {}
    for key in ("local_tmux", "github_runner", "remote_worker"):
        normalized[key] = _normalize_slot_limit(source.get(key), default)
    return normalized


def resolve_project_root(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _legacy_team_dir(project_root: Path) -> Path:
    return project_root / ".aoe-team"


def _state_root_dir() -> Optional[Path]:
    raw = str(os.environ.get("AOE_STATE_DIR", "")).strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _slugify_project_name(name: str) -> str:
    slug = []
    last_dash = False
    for char in str(name or "").strip().lower():
        if char.isalnum():
            slug.append(char)
            last_dash = False
            continue
        if not last_dash:
            slug.append("-")
            last_dash = True
    text = "".join(slug).strip("-")
    return text or "project"


def _normalize_git_remote_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.endswith(".git"):
        text = text[:-4]
    if text.startswith("git@") and ":" in text:
        host_path = text.split("@", 1)[1]
        host, path = host_path.split(":", 1)
        text = f"ssh://{host}/{path}"
    return text.rstrip("/")


@lru_cache(maxsize=256)
def _git_origin_url(project_root_raw: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", project_root_raw, "config", "--get", "remote.origin.url"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
            timeout=1.5,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return _normalize_git_remote_url(proc.stdout)


def stable_project_id(project_root: Path) -> str:
    root = Path(project_root).expanduser().resolve()
    remote = _git_origin_url(str(root))
    identity = f"git:{remote}" if remote else f"path:{root}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{_slugify_project_name(root.name)}-{digest}"


def resolve_centralized_team_dir(project_root: Path, state_root_dir: Path) -> Path:
    return Path(state_root_dir).expanduser().resolve() / stable_project_id(project_root)


def provider_capacity_state_path(team_dir: Path | str, filename: str = "provider_capacity.json") -> Path:
    return Path(team_dir).expanduser().resolve() / str(filename or "provider_capacity.json").strip()


def model_endpoint_registry_path(team_dir: Path | str, filename: str = "model_endpoints.json") -> Path:
    return Path(team_dir).expanduser().resolve() / str(filename or "model_endpoints.json").strip()


def model_routing_policy_path(team_dir: Path | str, filename: str = "model_routing.json") -> Path:
    return Path(team_dir).expanduser().resolve() / str(filename or "model_routing.json").strip()


def workspace_brief_path(team_dir: Path | str, filename: str = "workspace_brief.json") -> Path:
    return Path(team_dir).expanduser().resolve() / str(filename or "workspace_brief.json").strip()


def document_registry_path(team_dir: Path | str, filename: str = "document_registry.json") -> Path:
    return Path(team_dir).expanduser().resolve() / str(filename or "document_registry.json").strip()


def context_pack_dir(team_dir: Path | str, dirname: str = "context_packs") -> Path:
    return Path(team_dir).expanduser().resolve() / str(dirname or "context_packs").strip()


def context_pack_path(team_dir: Path | str, *, request_id: str, profile: str) -> Path:
    safe_request = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(request_id or "").strip()).strip("._-")
    safe_profile = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(profile or "").strip()).strip("._-")
    return context_pack_dir(team_dir) / (safe_request or "runtime") / f"{safe_profile or 'default'}.json"


def harness_authoring_dir(team_dir: Path | str, dirname: str = "harness_authoring") -> Path:
    return Path(team_dir).expanduser().resolve() / str(dirname or "harness_authoring").strip()


def harness_authoring_plan_path(
    team_dir: Path | str,
    *,
    request_id: str = "",
    task_ref: str = "",
    filename: str = "",
) -> Path:
    token = "".join(
        ch if ch.isalnum() or ch in "._-" else "_"
        for ch in str(filename or request_id or task_ref or "runtime").strip()
    ).strip("._-")
    return harness_authoring_dir(team_dir) / f"{token or 'runtime'}.json"


def latest_intent_snapshot_path(team_dir: Path | str) -> Path:
    return Path(team_dir).expanduser().resolve() / "control" / "latest-intent.json"


def action_audit_path(team_dir: Path | str) -> Path:
    return Path(team_dir).expanduser().resolve() / "dashboard" / "action-history.jsonl"


def recovery_summary_dir(team_dir: Path | str) -> Path:
    return Path(team_dir).expanduser().resolve() / "recovery" / "nightly-session-summary"


def recovery_summary_latest_path(team_dir: Path | str) -> Path:
    return recovery_summary_dir(team_dir) / "latest.json"


def describe_resolved_team_dir(team_dir: Path | str) -> Dict[str, str]:
    resolved = Path(team_dir).expanduser().resolve()
    explicit_env = str(os.environ.get("AOE_TEAM_DIR", "")).strip()
    explicit_path = Path(explicit_env).expanduser().resolve() if explicit_env else None
    state_root = _state_root_dir()
    if explicit_path is not None and resolved == explicit_path:
        mode = "explicit-env"
    elif state_root is not None and (resolved == state_root or state_root in resolved.parents):
        mode = "centralized"
    elif resolved.name == ".aoe-team":
        mode = "legacy"
    else:
        mode = "explicit"
    return {"mode": mode, "path": str(resolved)}


def resolve_default_team_dir(project_root: Path) -> Path:
    root = Path(project_root).expanduser().resolve()
    state_root = _state_root_dir()
    legacy_dir = _legacy_team_dir(root)
    if not state_root:
        return legacy_dir
    centralized_dir = resolve_centralized_team_dir(root, state_root)
    if centralized_dir.exists():
        return centralized_dir
    if legacy_dir.exists():
        return legacy_dir
    return centralized_dir


def resolve_team_dir(project_root: Path, explicit_team_dir: Optional[str]) -> Path:
    if explicit_team_dir:
        return Path(explicit_team_dir).expanduser().resolve()
    env_dir = os.environ.get("AOE_TEAM_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return resolve_default_team_dir(project_root)


def resolve_state_file(project_root: Path, explicit_state_file: Optional[str]) -> Path:
    if explicit_state_file:
        return Path(explicit_state_file).expanduser().resolve()
    return resolve_team_dir(project_root, None) / "telegram_gateway_state.json"


def default_manager_state(project_root: Path, team_dir: Path, *, now_iso: Callable[[], str]) -> Dict[str, Any]:
    timestamp = now_iso()
    return {
        "version": 1,
        "active": "default",
        "project_lock": {},
        "updated_at": timestamp,
        "chat_sessions": {},
        "projects": {
            "default": {
                "name": "default",
                "display_name": "default",
                "project_alias": "O1",
                "background_runner_target": "local_background",
                "background_runner_slot_limit": 1,
                "background_runner_slot_limits": {},
                "model_routing_profile": "default",
                "model_endpoint_overrides": {},
                "run_lock_mode": "open",
                "project_root": str(project_root),
                "team_dir": str(team_dir),
                "overview": "",
                "last_request_id": "",
                "tasks": {},
                "task_alias_index": {},
                "task_seq": 0,
                "todos": [],
                "todo_seq": 0,
                "todo_proposals": [],
                "todo_proposal_seq": 0,
                "system_project": True,
                "ops_hidden": True,
                "ops_hidden_reason": "internal fallback project",
                "paused": False,
                "paused_at": "",
                "paused_by": "",
                "paused_reason": "",
                "resumed_at": "",
                "resumed_by": "",
                "last_sync_at": "",
                "last_sync_mode": "",
                "last_sync_candidate_classes": {},
                "last_sync_candidate_doc_types": {},
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        },
    }


def load_manager_state(
    path: Path,
    project_root: Path,
    team_dir: Path,
    *,
    default_manager_state: Callable[[Path, Path], Dict[str, Any]],
    now_iso: Callable[[], str],
    normalize_project_name: Callable[[str], str],
    sanitize_task_record: Callable[[Dict[str, Any], str], Dict[str, Any]],
    trim_project_tasks: Callable[[Dict[str, Any]], Any],
    normalize_task_alias_key: Callable[[str], str],
    bool_from_json: Callable[[Any, bool], bool],
    normalize_project_alias: Callable[[str], str],
    backfill_task_aliases: Callable[[Dict[str, Any]], Any],
    ensure_project_aliases: Callable[[Dict[str, Any]], Any],
    sanitize_project_lock_row: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    sanitize_chat_session_row: Callable[[Any], Dict[str, Any]],
) -> Dict[str, Any]:
    fallback = default_manager_state(project_root, team_dir)
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback

    projects = data.get("projects")
    if not isinstance(projects, dict) or not projects:
        return fallback

    normalized: Dict[str, Dict[str, Any]] = {}
    for raw_key, raw_entry in projects.items():
        key = normalize_project_name(str(raw_key))
        if not key or not isinstance(raw_entry, dict):
            continue
        root = str(raw_entry.get("project_root", "")).strip()
        if not root:
            continue
        root_path = Path(root).expanduser().resolve()
        td = str(raw_entry.get("team_dir", "")).strip()
        if not td:
            td = str(resolve_default_team_dir(root_path))
        else:
            try:
                gw_team = Path(team_dir).expanduser().resolve()
                gw_root = Path(project_root).expanduser().resolve()
                td_path = Path(td).expanduser().resolve()
                if td_path == gw_team and root_path != gw_root:
                    td = str(resolve_default_team_dir(root_path))
            except Exception:
                pass
        raw_tasks = raw_entry.get("tasks")
        tasks: Dict[str, Any] = {}
        if isinstance(raw_tasks, dict):
            for req_id, task in raw_tasks.items():
                rid = str(req_id or "").strip()
                if not rid or not isinstance(task, dict):
                    continue
                tasks[rid] = sanitize_task_record(task, rid)
            trim_project_tasks(tasks)

        raw_alias_index = raw_entry.get("task_alias_index")
        task_alias_index: Dict[str, str] = {}
        if isinstance(raw_alias_index, dict):
            for akey, rid in raw_alias_index.items():
                key_norm = normalize_task_alias_key(str(akey or ""))
                rid_norm = str(rid or "").strip()
                if key_norm and rid_norm:
                    task_alias_index[key_norm] = rid_norm

        raw_seq = raw_entry.get("task_seq")
        try:
            task_seq = max(0, int(raw_seq or 0))
        except Exception:
            task_seq = 0

        raw_todos = raw_entry.get("todos")
        todos: List[Dict[str, Any]] = []
        todo_seq_backfill = 0
        if isinstance(raw_todos, list):
            for row in raw_todos:
                if not isinstance(row, dict):
                    continue
                tid = str(row.get("id", "")).strip() or str(row.get("todo_id", "")).strip()
                if not tid:
                    continue
                summary = str(row.get("summary", "")).strip()
                pr = str(row.get("priority", "P2")).strip().upper() or "P2"
                if pr not in {"P1", "P2", "P3"}:
                    pr = "P2"
                st = str(row.get("status", "open")).strip().lower() or "open"
                if st not in {"open", "running", "blocked", "done", "canceled"}:
                    st = "open"
                created_at = str(row.get("created_at", "")).strip() or now_iso()
                updated_at = str(row.get("updated_at", "")).strip() or created_at
                item: Dict[str, Any] = {
                    "id": tid[:32],
                    "summary": summary[:600],
                    "priority": pr,
                    "status": st,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
                meta_fields = {
                    "created_by": 80,
                    "queued_at": 40,
                    "queued_by": 80,
                    "started_at": 40,
                    "started_by": 80,
                    "current_request_id": 80,
                    "current_task_label": 120,
                    "done_request_id": 80,
                    "done_task_label": 120,
                    "done_by": 80,
                    "blocked_at": 40,
                    "blocked_request_id": 80,
                    "blocked_reason": 240,
                    "blocked_bucket": 40,
                    "blocked_alerted_at": 40,
                    "proposal_id": 32,
                    "proposal_kind": 32,
                    "created_from_request_id": 128,
                    "created_from_todo_id": 64,
                }
                for field, max_len in meta_fields.items():
                    val = str(row.get(field, "")).strip()
                    if val:
                        item[field] = val[: int(max_len)]
                done_at = str(row.get("done_at", "")).strip()
                if done_at:
                    item["done_at"] = done_at
                try:
                    blocked_count = max(0, int(row.get("blocked_count", 0) or 0))
                except Exception:
                    blocked_count = 0
                if blocked_count:
                    item["blocked_count"] = min(blocked_count, 99)
                todos.append(item)

                token = tid.strip().upper()
                if token.startswith("TODO-"):
                    tail = token[5:]
                    if tail.isdigit():
                        todo_seq_backfill = max(todo_seq_backfill, int(tail))
                elif token.isdigit():
                    todo_seq_backfill = max(todo_seq_backfill, int(token))

        raw_todo_seq = raw_entry.get("todo_seq")
        try:
            todo_seq = max(0, int(raw_todo_seq or 0))
        except Exception:
            todo_seq = 0
        todo_seq = max(todo_seq, todo_seq_backfill)

        raw_proposals = raw_entry.get("todo_proposals")
        todo_proposals: List[Dict[str, Any]] = []
        proposal_seq_backfill = 0
        if isinstance(raw_proposals, list):
            for row in raw_proposals:
                if not isinstance(row, dict):
                    continue
                pid = str(row.get("id", "")).strip()
                if not pid:
                    continue
                summary = str(row.get("summary", "")).strip()
                if not summary:
                    continue
                pr = str(row.get("priority", "P2")).strip().upper() or "P2"
                if pr not in {"P1", "P2", "P3"}:
                    pr = "P2"
                kind = str(row.get("kind", "followup")).strip().lower() or "followup"
                if kind not in {"followup", "risk", "debt", "handoff"}:
                    kind = "followup"
                st = str(row.get("status", "open")).strip().lower() or "open"
                if st not in {"open", "accepted", "rejected"}:
                    st = "open"
                created_at = str(row.get("created_at", "")).strip() or now_iso()
                updated_at = str(row.get("updated_at", "")).strip() or created_at
                item = {
                    "id": pid[:32],
                    "summary": summary[:600],
                    "priority": pr,
                    "kind": kind,
                    "status": st,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
                reason = str(row.get("reason", "")).strip()
                if reason:
                    item["reason"] = reason[:240]
                try:
                    confidence = float(row.get("confidence", 0.0) or 0.0)
                except Exception:
                    confidence = 0.0
                confidence = max(0.0, min(1.0, confidence))
                if confidence > 0.0:
                    item["confidence"] = confidence
                proposal_meta_fields = {
                    "source_request_id": 128,
                    "source_todo_id": 64,
                    "source_task_label": 120,
                    "created_by": 40,
                    "source_file": 240,
                    "source_section": 160,
                    "source_reason": 80,
                    "accepted_at": 40,
                    "accepted_by": 80,
                    "accepted_todo_id": 32,
                    "rejected_at": 40,
                    "rejected_by": 80,
                    "rejected_reason": 240,
                }
                for field, max_len in proposal_meta_fields.items():
                    val = str(row.get(field, "")).strip()
                    if val:
                        item[field] = val[: int(max_len)]
                try:
                    source_line = int(row.get("source_line", 0) or 0)
                except Exception:
                    source_line = 0
                if source_line > 0:
                    item["source_line"] = source_line
                todo_proposals.append(item)

                token = pid.strip().upper()
                if token.startswith("PROP-"):
                    tail = token[5:]
                    if tail.isdigit():
                        proposal_seq_backfill = max(proposal_seq_backfill, int(tail))
                elif token.isdigit():
                    proposal_seq_backfill = max(proposal_seq_backfill, int(token))

        raw_proposal_seq = raw_entry.get("todo_proposal_seq")
        try:
            todo_proposal_seq = max(0, int(raw_proposal_seq or 0))
        except Exception:
            todo_proposal_seq = 0
        todo_proposal_seq = max(todo_proposal_seq, proposal_seq_backfill)

        pending_todo: Optional[Dict[str, Any]] = None
        raw_pending = raw_entry.get("pending_todo")
        if isinstance(raw_pending, dict):
            pt_id = str(raw_pending.get("todo_id", "")).strip()
            pt_chat = str(raw_pending.get("chat_id", "")).strip()
            pt_selected = str(raw_pending.get("selected_at", "")).strip()
            if pt_id and pt_chat:
                pending_todo = {
                    "todo_id": pt_id[:32],
                    "chat_id": pt_chat[:32],
                    "selected_at": pt_selected or now_iso(),
                }

        paused = bool_from_json(raw_entry.get("paused"), False)
        paused_at = str(raw_entry.get("paused_at", "")).strip()
        paused_by = str(raw_entry.get("paused_by", "")).strip()
        paused_reason = str(raw_entry.get("paused_reason", "")).strip()
        system_project = bool_from_json(raw_entry.get("system_project"), key == "default")
        ops_hidden = bool_from_json(raw_entry.get("ops_hidden"), system_project)
        ops_hidden_reason = str(raw_entry.get("ops_hidden_reason", "")).strip()
        resumed_at = str(raw_entry.get("resumed_at", "")).strip()
        resumed_by = str(raw_entry.get("resumed_by", "")).strip()
        last_sync_at = str(raw_entry.get("last_sync_at", "")).strip()
        last_sync_mode = str(raw_entry.get("last_sync_mode", "")).strip()
        last_sync_candidate_classes = raw_entry.get("last_sync_candidate_classes")
        last_sync_candidate_doc_types = raw_entry.get("last_sync_candidate_doc_types")

        normalized[key] = {
            "name": key,
            "display_name": str(raw_entry.get("display_name", key)).strip() or key,
            "project_alias": normalize_project_alias(str(raw_entry.get("project_alias", ""))),
            "background_runner_target": str(raw_entry.get("background_runner_target", "local_background")).strip().lower()
            or "local_background",
            "background_runner_slot_limit": _normalize_slot_limit(raw_entry.get("background_runner_slot_limit"), 1),
            "background_runner_slot_limits": _normalize_slot_limits_map(
                raw_entry.get("background_runner_slot_limits"),
                _normalize_slot_limit(raw_entry.get("background_runner_slot_limit"), 1),
            ),
            "run_lock_mode": str(raw_entry.get("run_lock_mode", "open")).strip().lower() or "open",
            "project_root": str(root_path),
            "team_dir": str(Path(td).expanduser().resolve()),
            "overview": str(raw_entry.get("overview", "")).strip(),
            "last_request_id": str(raw_entry.get("last_request_id", "")).strip(),
            "tasks": tasks,
            "task_alias_index": task_alias_index,
            "task_seq": task_seq,
            "todos": todos,
            "todo_seq": todo_seq,
            "todo_proposals": todo_proposals,
            "todo_proposal_seq": todo_proposal_seq,
            "paused": paused,
            "paused_at": paused_at,
            "paused_by": paused_by,
            "paused_reason": paused_reason[:400] if paused_reason else "",
            "system_project": system_project,
            "ops_hidden": ops_hidden,
            "ops_hidden_reason": ops_hidden_reason[:400] if ops_hidden_reason else "",
            "resumed_at": resumed_at,
            "resumed_by": resumed_by,
            "last_sync_at": last_sync_at[:40] if last_sync_at else "",
            "last_sync_mode": last_sync_mode[:40] if last_sync_mode else "",
            "last_sync_candidate_classes": dict(last_sync_candidate_classes)
            if isinstance(last_sync_candidate_classes, dict)
            else {},
            "last_sync_candidate_doc_types": dict(last_sync_candidate_doc_types)
            if isinstance(last_sync_candidate_doc_types, dict)
            else {},
            "created_at": str(raw_entry.get("created_at", "")).strip() or now_iso(),
            "updated_at": str(raw_entry.get("updated_at", "")).strip() or now_iso(),
        }
        if isinstance(pending_todo, dict):
            normalized[key]["pending_todo"] = pending_todo

    if not normalized:
        return fallback

    active = normalize_project_name(str(data.get("active", "default")))
    if active not in normalized:
        active = sorted(normalized.keys())[0]

    for entry in normalized.values():
        if isinstance(entry, dict):
            backfill_task_aliases(entry)

    temp_state: Dict[str, Any] = {"projects": normalized}
    ensure_project_aliases(temp_state)

    project_lock = sanitize_project_lock_row(data.get("project_lock"), temp_state.get("projects", normalized))
    if project_lock:
        active = str(project_lock.get("project_key", active)).strip() or active

    raw_chat = data.get("chat_sessions")
    chat_sessions: Dict[str, Any] = {}
    if isinstance(raw_chat, dict):
        for k, v in raw_chat.items():
            cid = str(k or "").strip()
            if not cid:
                continue
            row = sanitize_chat_session_row(v)
            if row:
                chat_sessions[cid] = row

    return {
        "version": 1,
        "active": active,
        "project_lock": project_lock,
        "updated_at": str(data.get("updated_at", "")).strip() or now_iso(),
        "chat_sessions": chat_sessions,
        "projects": temp_state.get("projects", normalized),
    }


def ensure_default_project_registered(
    state: Dict[str, Any],
    project_root: Path,
    team_dir: Path,
    *,
    now_iso: Callable[[], str],
    bool_from_json: Callable[[Any, bool], bool],
    normalize_project_alias: Callable[[str], str],
    normalize_project_name: Callable[[str], str],
    sanitize_project_lock_row: Callable[[Any, Dict[str, Any]], Dict[str, Any]],
    ensure_project_aliases: Callable[[Dict[str, Any]], Any],
    backfill_task_aliases: Callable[[Dict[str, Any]], Any],
) -> None:
    chat_sessions = state.get("chat_sessions")
    if not isinstance(chat_sessions, dict):
        state["chat_sessions"] = {}

    projects = state.setdefault("projects", {})
    if not isinstance(projects, dict):
        state["projects"] = {}
        projects = state["projects"]

    if "default" not in projects:
        projects["default"] = {
            "name": "default",
            "display_name": "default",
            "project_alias": "O1",
            "background_runner_target": "local_background",
            "background_runner_slot_limit": 1,
            "background_runner_slot_limits": {},
            "run_lock_mode": "open",
            "project_root": str(project_root),
            "team_dir": str(team_dir),
            "overview": "",
            "last_request_id": "",
            "tasks": {},
            "task_alias_index": {},
            "task_seq": 0,
            "todos": [],
            "todo_seq": 0,
            "system_project": True,
            "ops_hidden": True,
            "ops_hidden_reason": "internal fallback project",
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }

    for entry in projects.values():
        if isinstance(entry, dict):
            if "tasks" not in entry or not isinstance(entry.get("tasks"), dict):
                entry["tasks"] = {}
            if "task_alias_index" not in entry or not isinstance(entry.get("task_alias_index"), dict):
                entry["task_alias_index"] = {}
            if "todos" not in entry or not isinstance(entry.get("todos"), list):
                entry["todos"] = []
            entry["project_alias"] = normalize_project_alias(str(entry.get("project_alias", "")))
            entry["background_runner_target"] = str(entry.get("background_runner_target", "local_background")).strip().lower() or "local_background"
            entry["background_runner_slot_limit"] = _normalize_slot_limit(entry.get("background_runner_slot_limit"), 1)
            entry["background_runner_slot_limits"] = _normalize_slot_limits_map(
                entry.get("background_runner_slot_limits"),
                entry["background_runner_slot_limit"],
            )
            entry["run_lock_mode"] = str(entry.get("run_lock_mode", "open")).strip().lower() or "open"
            entry["system_project"] = bool_from_json(entry.get("system_project"), str(entry.get("name", "")).strip().lower() == "default")
            entry["ops_hidden"] = bool_from_json(entry.get("ops_hidden"), bool(entry.get("system_project")))
            entry["ops_hidden_reason"] = str(entry.get("ops_hidden_reason", "")).strip()[:400]
            try:
                entry["task_seq"] = max(0, int(entry.get("task_seq", 0) or 0))
            except Exception:
                entry["task_seq"] = 0
            try:
                entry["todo_seq"] = max(0, int(entry.get("todo_seq", 0) or 0))
            except Exception:
                entry["todo_seq"] = 0
            backfill_task_aliases(entry)

    active = normalize_project_name(str(state.get("active", "default")))
    if active not in projects:
        state["active"] = "default"
    project_lock = sanitize_project_lock_row(state.get("project_lock"), projects)
    if project_lock:
        state["project_lock"] = project_lock
        state["active"] = str(project_lock.get("project_key", state.get("active", "default"))).strip() or "default"
    else:
        state.pop("project_lock", None)
    ensure_project_aliases(state)


def save_manager_state(
    path: Path,
    state: Dict[str, Any],
    *,
    now_iso: Callable[[], str],
    sync_investigations_docs: Callable[[Path, Dict[str, Any]], None],
    cleanup_tf_exec_artifacts: Callable[[Path, Dict[str, Any]], None],
    cleanup_room_logs: Callable[[Path], int],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["updated_at"] = now_iso()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        sync_investigations_docs(path, payload)
    except Exception as exc:
        print(f"[WARN] investigations_mo sync skipped: {exc}", file=sys.stderr)
    try:
        cleanup_tf_exec_artifacts(path, payload)
    except Exception as exc:
        print(f"[WARN] tf_exec cleanup skipped: {exc}", file=sys.stderr)
    try:
        cleanup_room_logs(path.parent.resolve())
    except Exception as exc:
        print(f"[WARN] room log gc skipped: {exc}", file=sys.stderr)


def acquire_process_lock(lock_path: Path, *, now_iso: Callable[[], str]) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise RuntimeError(f"another gateway process is already running (lock={lock_path})")
    fh.seek(0)
    fh.truncate(0)
    fh.write(f"pid={os.getpid()} started_at={now_iso()}\n")
    fh.flush()
    return fh
