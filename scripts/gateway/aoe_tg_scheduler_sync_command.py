#!/usr/bin/env python3
"""Command parsing and target resolution helpers for sync flows."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, Dict, List, Tuple


@dataclass
class SyncCommandSpec:
    raw_rest: str
    recalled_last_args: bool
    quiet: bool
    preview: bool
    prune_missing: bool
    history_candidate: str
    since_seconds: int
    since_label: str
    min_mtime: float
    mode: str
    docs_limit: int
    files_limit: int
    target_token: str


@dataclass
class SyncTargetResolution:
    targets: List[Tuple[str, Dict[str, Any]]]
    lock_narrowed: bool


def parse_sync_command(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    rest: str,
    send: Callable[..., bool],
    get_last_cmd_args: Callable[[Dict[str, Any], str, str], str],
    is_auto_invocation: Callable[[Any], bool],
    parse_since_seconds: Callable[[str], int],
    default_docs_limit: int,
    default_files_limit: int,
) -> SyncCommandSpec | None:
    recalled_last_args = False
    raw_rest = str(rest or "").strip()
    user_provided_args = bool(raw_rest)
    if not raw_rest:
        last = get_last_cmd_args(manager_state, chat_id, "sync")
        if last:
            raw_rest = last
            rest = last
            recalled_last_args = True

    tokens = [t for t in str(rest or "").split() if t.strip()]
    quiet = False
    preview = False
    prune_missing = False
    filtered: List[str] = []
    for tok in tokens:
        low = tok.strip().lower()
        if low in {"quiet", "--quiet", "-q"}:
            quiet = True
            continue
        if low in {"preview", "inspect", "--preview", "--inspect"}:
            preview = True
            continue
        if low in {"prune", "replace", "rebuild", "--prune", "--replace"}:
            prune_missing = True
            continue
        filtered.append(tok)
    tokens = filtered
    history_candidate = raw_rest if (user_provided_args and (not is_auto_invocation(args)) and (not preview)) else ""

    since_seconds = 0
    since_label = ""
    filtered_since: List[str] = []
    i = 0
    while i < len(tokens):
        tok = str(tokens[i] or "").strip()
        low = tok.lower()

        raw_val = ""
        if low in {"since", "--since", "-s", "within", "--within"}:
            if i + 1 < len(tokens):
                raw_val = str(tokens[i + 1] or "").strip()
                i += 2
            else:
                i += 1
            secs = parse_since_seconds(raw_val)
            if secs > 0:
                since_seconds = secs
                since_label = raw_val
            continue

        if low.startswith("since=") or low.startswith("--since=") or low.startswith("-s="):
            raw_val = tok.split("=", 1)[1].strip() if "=" in tok else ""
            secs = parse_since_seconds(raw_val)
            if secs > 0:
                since_seconds = secs
                since_label = raw_val
                i += 1
                continue

        filtered_since.append(tok)
        i += 1

    tokens = filtered_since
    if since_seconds <= 0 and tokens:
        tail = str(tokens[-1] or "").strip()
        secs = parse_since_seconds(tail)
        if secs > 0:
            since_seconds = secs
            since_label = tail
            tokens = tokens[:-1]
    min_mtime = max(0.0, float(time.time()) - float(since_seconds)) if since_seconds > 0 else 0.0

    mode = "scenario"
    if tokens:
        head = tokens[0].strip().lower()
        if head in {"recent", "docs", "scan"}:
            mode = "recent_docs"
            tokens = tokens[1:]
        elif head in {"salvage"}:
            mode = "salvage_docs"
            tokens = tokens[1:]
        elif head in {"bootstrap", "recover"}:
            mode = "bootstrap_docs"
            tokens = tokens[1:]
        elif head in {"files", "todo-files", "todofiles"}:
            mode = "todo_files"
            tokens = tokens[1:]

    docs_limit = int(default_docs_limit)
    if mode in {"recent_docs", "salvage_docs", "bootstrap_docs"} and tokens and tokens[-1].isdigit():
        docs_limit = max(1, min(50, int(tokens[-1])))
        tokens = tokens[:-1]

    files_limit = int(default_files_limit)
    if mode == "todo_files" and tokens and tokens[-1].isdigit():
        files_limit = max(1, min(400, int(tokens[-1])))
        tokens = tokens[:-1]

    if prune_missing and (mode in {"recent_docs", "salvage_docs", "bootstrap_docs"} or since_seconds > 0):
        if mode == "recent_docs":
            detail = "recent_docs mode"
        elif mode == "salvage_docs":
            detail = "salvage_docs mode"
        elif mode == "bootstrap_docs":
            detail = "bootstrap_docs mode"
        else:
            detail = f"since {since_label or 'window'}"
        send(
            "sync prune blocked\n"
            "- reason: prune/replace needs a full-scope sync to avoid canceling unrelated todos\n"
            f"- scope: {detail}\n"
            "next:\n"
            "- /sync preview replace <O#|name>\n"
            "- /sync replace <O#|name>\n"
            "- or run plain /sync recent ... without replace",
            context="sync-prune-blocked",
            with_menu=True,
        )
        return None

    target_token = tokens[0].strip() if tokens else ""
    return SyncCommandSpec(
        raw_rest=raw_rest,
        recalled_last_args=recalled_last_args,
        quiet=quiet,
        preview=preview,
        prune_missing=prune_missing,
        history_candidate=history_candidate,
        since_seconds=since_seconds,
        since_label=since_label,
        min_mtime=min_mtime,
        mode=mode,
        docs_limit=docs_limit,
        files_limit=files_limit,
        target_token=target_token,
    )


def resolve_sync_targets(
    *,
    spec: SyncCommandSpec,
    projects: Dict[str, Any],
    focus_key: str,
    focus_entry: Dict[str, Any],
    focus_alias: str,
    orch_target: str | None,
    send: Callable[..., bool],
    get_context: Callable[[str | None], tuple[str, Dict[str, Any], Any]],
    list_projects: Callable[[Dict[str, Any]], List[Tuple[str, Dict[str, Any]]]],
    project_alias: Callable[[Dict[str, Any], str], str],
    render_sync_lock_message: Callable[..., str],
) -> SyncTargetResolution | None:
    target_token = spec.target_token
    want_all = False
    if spec.mode == "scenario":
        want_all = (not target_token) or target_token.lower() in {"all", "*"}
    else:
        want_all = bool(target_token) and target_token.lower() in {"all", "*"}

    targets: List[Tuple[str, Dict[str, Any]]] = []
    lock_narrowed = False
    if want_all:
        if focus_key and isinstance(focus_entry, dict):
            targets.append((focus_key, focus_entry))
            lock_narrowed = True
        else:
            targets.extend(list_projects(projects))
    else:
        requested_label = str(target_token or orch_target or "").strip()
        try:
            key, entry, _p_args = get_context(target_token or orch_target)
        except Exception as exc:
            if focus_key and "project lock active" in str(exc).strip().lower():
                send(
                    render_sync_lock_message(
                        locked_label=focus_alias or focus_key,
                        requested_label=requested_label or "-",
                    ),
                    context="sync-locked",
                    with_menu=True,
                )
                return None
            raise
        if focus_key and key != focus_key:
            send(
                render_sync_lock_message(
                    locked_label=focus_alias or focus_key,
                    requested_label=project_alias(entry, key),
                ),
                context="sync-locked",
                with_menu=True,
            )
            return None
        targets.append((key, entry))

    return SyncTargetResolution(targets=targets, lock_narrowed=lock_narrowed)
