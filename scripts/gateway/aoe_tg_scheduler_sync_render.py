#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


@dataclass(frozen=True)
class SyncRenderContext:
    focus_key: str
    focus_alias: str
    lock_narrowed: bool
    recalled_last_args: bool
    raw_rest: str
    mode: str
    docs_limit: int
    files_limit: int
    since_seconds: int
    since_label: str
    target_count: int
    prune_missing: bool


def _append_sync_mode_lines(lines: List[str], ctx: SyncRenderContext, *, scenario_default: bool = False) -> None:
    if ctx.focus_key:
        lines.append(f"- project_lock: {ctx.focus_alias or ctx.focus_key}")
        if ctx.lock_narrowed:
            lines.append("- scope: narrowed to locked project")
    if ctx.recalled_last_args and ctx.raw_rest:
        lines.append(f"- args: {ctx.raw_rest} (reused)")
    if ctx.mode == "recent_docs":
        lines.append("- mode: recent_docs")
        lines.append(f"- docs_per_project: {ctx.docs_limit}")
    elif ctx.mode == "salvage_docs":
        lines.append("- mode: salvage_docs")
        lines.append(f"- docs_per_project: {max(ctx.docs_limit, 5)}")
    elif ctx.mode == "bootstrap_docs":
        lines.append("- mode: bootstrap_docs")
        lines.append(f"- docs_per_project: {max(ctx.docs_limit, 5)}")
        lines.append(f"- files_per_project: {ctx.files_limit}")
    elif ctx.mode == "todo_files":
        lines.append("- mode: todo_files")
        lines.append(f"- files_per_project: {ctx.files_limit}")
    elif scenario_default:
        lines.append("- mode: scenario")
    if ctx.since_seconds > 0 and ctx.since_label:
        lines.append(f"- since: {ctx.since_label}")
    lines.append(f"- projects: {ctx.target_count}")


def send_sync_preview(
    *,
    ctx: SyncRenderContext,
    total: Dict[str, Any],
    total_candidate_classes: Dict[str, int],
    total_candidate_doc_types: Dict[str, int],
    preview_blocks: List[str],
    send: Callable[..., bool],
) -> Dict[str, Any]:
    lines: List[str] = ["sync preview"]
    _append_sync_mode_lines(lines, ctx, scenario_default=True)
    if total_candidate_classes:
        ordered = sorted(total_candidate_classes.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        lines.append("- candidate_classes: " + ", ".join(f"{k}={v}" for k, v in ordered[:6]))
    if total_candidate_doc_types:
        ordered = sorted(total_candidate_doc_types.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        lines.append("- candidate_doc_types: " + ", ".join(f"{k}={v}" for k, v in ordered[:6]))
    lines.append(f"- parsed: {total['parsed']}")
    lines.append(f"- would_add: {total['added']}")
    lines.append(f"- would_update: {total['updated']}")
    lines.append(f"- would_done: {total['done']}")
    if total["proposed"]:
        lines.append(f"- would_propose: {total['proposed']}")
    if ctx.prune_missing:
        lines.append(f"- would_prune: {total['pruned']}")
    if total["missing"]:
        lines.append(f"- missing: {total['missing']}")
    skipped_done = int(total.get("skipped_done_missing", 0) or 0)
    if skipped_done:
        lines.append(f"- skipped_done_missing: {skipped_done}")
    if preview_blocks:
        lines.append("")
        lines.append("projects:")
        for idx, block in enumerate(preview_blocks[:8]):
            if idx:
                lines.append("")
            lines.extend(block.splitlines())
        if len(preview_blocks) > 8:
            lines.append("")
            lines.append(f"... ({len(preview_blocks) - 8} more projects)")
    lines.extend(
        [
            "",
            "next:",
            "- /sync <same args without preview>   # actually import",
            "- /queue",
            "- /next",
        ]
    )
    send("\n".join(lines).strip(), context="sync-preview", with_menu=True)
    return {"terminal": True}


def persist_sync_state(
    *,
    args: Any,
    manager_state: Dict[str, Any],
    chat_id: str,
    history_candidate: str,
    any_changed: bool,
    proposal_changed: bool,
    sync_meta_changed: bool,
    save_manager_state: Callable[..., None],
    now_iso: Callable[[], str],
    set_last_cmd_args: Callable[[Dict[str, Any], str, str, str, str], bool],
) -> bool:
    history_changed = False
    if history_candidate and (not args.dry_run):
        history_changed = set_last_cmd_args(manager_state, chat_id, "sync", history_candidate, now_iso())
    if (any_changed or proposal_changed) and (not args.dry_run):
        save_manager_state(args.manager_state_file, manager_state)
    elif sync_meta_changed and (not args.dry_run):
        save_manager_state(args.manager_state_file, manager_state)
    elif history_changed and (not args.dry_run):
        save_manager_state(args.manager_state_file, manager_state)
    return history_changed


def send_sync_quiet(
    *,
    ctx: SyncRenderContext,
    total: Dict[str, Any],
    any_changed: bool,
    proposal_changed: bool,
    send: Callable[..., bool],
) -> Dict[str, Any]:
    if any_changed or proposal_changed:
        msg_lines: List[str] = ["sync updated"]
        _append_sync_mode_lines(msg_lines, ctx)
        if ctx.mode == "scenario":
            msg_lines.append(f"- missing_files: {total['missing']}")
            if ctx.since_seconds > 0:
                msg_lines.append(f"- skipped_stale: {total['skipped_stale']}")
        else:
            if ctx.mode in {"recent_docs", "salvage_docs", "bootstrap_docs"}:
                msg_lines.append(f"- missing_docs: {total['missing']}")
            else:
                msg_lines.append(f"- missing_files: {total['missing']}")
        msg_lines.append(f"- added: {total['added']}")
        msg_lines.append(f"- updated: {total['updated']}")
        msg_lines.append(f"- done: {total['done']}")
        if total["proposed"]:
            msg_lines.append(f"- proposed: {total['proposed']}")
        if ctx.prune_missing:
            msg_lines.append(f"- pruned: {total['pruned']}")
        send("\n".join(msg_lines).strip(), context="sync-quiet", with_menu=True)
    return {"terminal": True}


def send_sync_complete(
    *,
    ctx: SyncRenderContext,
    total: Dict[str, Any],
    per_project_lines: List[str],
    send: Callable[..., bool],
) -> Dict[str, Any]:
    lines: List[str] = ["sync finished"]
    _append_sync_mode_lines(lines, ctx)
    if ctx.mode == "scenario":
        lines.append(f"- missing_files: {total['missing']}")
        if ctx.since_seconds > 0:
            lines.append(f"- skipped_stale: {total['skipped_stale']}")
    else:
        if ctx.mode in {"recent_docs", "salvage_docs", "bootstrap_docs"}:
            lines.append(f"- missing_docs: {total['missing']}")
            lines.append(f"- docs_used: {total.get('docs_used', 0)}")
            lines.append(f"- docs_scanned: {total.get('docs_scanned', 0)}")
            if ctx.mode == "bootstrap_docs":
                lines.append(f"- files_used: {total.get('files_used', 0)}")
                lines.append(f"- files_scanned: {total.get('files_scanned', 0)}")
        else:
            lines.append(f"- missing_files: {total['missing']}")
            lines.append(f"- files_used: {total.get('files_used', 0)}")
            lines.append(f"- files_scanned: {total.get('files_scanned', 0)}")
    lines.append(f"- parsed: {total['parsed']}")
    lines.append(f"- added: {total['added']}")
    lines.append(f"- updated: {total['updated']}")
    lines.append(f"- done: {total['done']}")
    if total["proposed"]:
        lines.append(f"- proposed: {total['proposed']}")
    if ctx.prune_missing:
        lines.append(f"- pruned: {total['pruned']}")
    skipped_done = int(total.get("skipped_done_missing", 0) or 0)
    if skipped_done:
        lines.append(f"- skipped_done_missing: {skipped_done}")
    if per_project_lines:
        lines.append("")
        lines.append("details:")
        lines.extend(per_project_lines[:30])
        if len(per_project_lines) > 30:
            lines.append(f"... ({len(per_project_lines) - 30} more)")
    lines.extend(["", "next:", "- /queue", "- /next", "- /fanout", "- /auto on"])
    send("\n".join(lines).strip(), context="sync", with_menu=True)
    return {"terminal": True}
