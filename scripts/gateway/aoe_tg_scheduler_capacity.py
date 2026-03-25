#!/usr/bin/env python3
"""Provider capacity and recovery helpers for scheduler control handlers."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


_PROVIDER_RECOVERY_GRACE_SEC = max(
    60,
    int(str(os.environ.get("AOE_PROVIDER_RECOVERY_GRACE_SEC", "600") or "600").strip() or "600"),
)


def _parse_iso_datetime(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _compact_text(raw: Any, limit: int = 160) -> str:
    text = " ".join(str(raw or "").strip().split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _next_rate_limited_task_snapshot(manager_state: Dict[str, Any]) -> Dict[str, str]:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict):
        return {}
    best_dt: Optional[datetime] = None
    best_row: Dict[str, str] = {}
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key or "").strip() or "-"
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        for request_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
            if str(rate_limit.get("mode", "")).strip().lower() != "blocked":
                continue
            retry_at = str(rate_limit.get("retry_at", "")).strip()
            parsed = _parse_iso_datetime(retry_at)
            if parsed is None:
                continue
            if best_dt is not None and parsed >= best_dt:
                continue
            result = task.get("result") if isinstance(task.get("result"), dict) else {}
            degraded_by = [str(x).strip() for x in (result.get("degraded_by") or []) if str(x).strip()]
            providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
            best_dt = parsed
            best_row = {
                "alias": alias,
                "task_ref": str(task.get("label", "")).strip() or str(task.get("short_id", "")).strip() or str(request_id or "").strip() or "-",
                "providers": ",".join(providers) if providers else "-",
                "retry_at": retry_at or "-",
                "degraded": ",".join(degraded_by) if degraded_by else "-",
            }
    return best_row


def _rate_limited_project_aliases(manager_state: Dict[str, Any]) -> List[str]:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict):
        return []
    aliases: set[str] = set()
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key or "").strip().upper()
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        for task in tasks.values():
            if not isinstance(task, dict):
                continue
            rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
            if str(rate_limit.get("mode", "")).strip().lower() == "blocked":
                if alias:
                    aliases.add(alias)
                break
    return sorted(aliases)


def _next_rate_limited_retry_at(manager_state: Dict[str, Any], *, now: Optional[datetime] = None) -> str:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict):
        return ""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    best_dt: Optional[datetime] = None
    best_text = ""
    for entry in projects.values():
        if not isinstance(entry, dict):
            continue
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        for task in tasks.values():
            if not isinstance(task, dict):
                continue
            rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
            if str(rate_limit.get("mode", "")).strip().lower() != "blocked":
                continue
            retry_at = str(rate_limit.get("retry_at", "")).strip()
            parsed = _parse_iso_datetime(retry_at)
            if parsed is None or parsed <= current.astimezone(timezone.utc):
                continue
            if best_dt is None or parsed < best_dt:
                best_dt = parsed
                best_text = retry_at
    return best_text


def _rate_limited_capacity_summary(manager_state: Dict[str, Any]) -> Dict[str, str]:
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict):
        return {}
    project_aliases: set[str] = set()
    provider_counts: Dict[str, int] = {}
    task_count = 0
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key or "").strip() or "-"
        tasks = entry.get("tasks")
        if not isinstance(tasks, dict):
            continue
        project_has_limited_task = False
        for task in tasks.values():
            if not isinstance(task, dict):
                continue
            rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
            if str(rate_limit.get("mode", "")).strip().lower() != "blocked":
                continue
            task_count += 1
            project_has_limited_task = True
            for raw_provider in rate_limit.get("limited_providers") or []:
                provider = str(raw_provider or "").strip().lower()
                if not provider:
                    continue
                provider_counts[provider] = int(provider_counts.get(provider, 0)) + 1
        if project_has_limited_task:
            project_aliases.add(alias)
    if task_count <= 0:
        return {}
    ordered = sorted(provider_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    provider_summary = ", ".join(f"{provider}={count}" for provider, count in ordered) or "-"
    return {
        "task_count": str(task_count),
        "project_count": str(len(project_aliases)),
        "provider_summary": provider_summary,
        "provider_counts": dict(provider_counts),
    }


def _rate_limited_capacity_summary_for_reports(reports: List[Dict[str, Any]]) -> Dict[str, str]:
    project_aliases: set[str] = set()
    provider_counts: Dict[str, int] = {}
    task_count = 0
    for row in reports:
        if not isinstance(row, dict):
            continue
        rate_limit = row.get("active_task_rate_limit") if isinstance(row.get("active_task_rate_limit"), dict) else {}
        if str(rate_limit.get("mode", "")).strip().lower() != "blocked":
            continue
        task_count += 1
        alias = str(row.get("alias", "")).strip().upper()
        if alias:
            project_aliases.add(alias)
        for raw_provider in rate_limit.get("limited_providers") or []:
            provider = str(raw_provider or "").strip().lower()
            if not provider:
                continue
            provider_counts[provider] = int(provider_counts.get(provider, 0)) + 1
    if task_count <= 0:
        return {}
    ordered = sorted(provider_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    provider_summary = ", ".join(f"{provider}={count}" for provider, count in ordered) or "-"
    return {
        "task_count": str(task_count),
        "project_count": str(len(project_aliases)),
        "provider_summary": provider_summary,
        "provider_counts": dict(provider_counts),
    }


def _provider_capacity_repeat_memory(memory_state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(memory_state, dict):
        return {}
    count = int(memory_state.get("recovery_repeat_count", 0) or 0)
    last_at = str(memory_state.get("recovery_repeat_last_at", "")).strip()
    history = [
        row
        for row in (memory_state.get("recovery_repeat_history") if isinstance(memory_state.get("recovery_repeat_history"), list) else [])
        if isinstance(row, dict)
    ]
    latest = history[-1] if history else {}
    latest_summary = str(latest.get("summary", "")).strip()
    return {
        "count": count,
        "last_at": last_at,
        "history": history[-5:],
        "latest_summary": latest_summary,
    }


def _provider_capacity_repeat_summary_line(memory_state: Dict[str, Any]) -> str:
    repeat_memory = _provider_capacity_repeat_memory(memory_state)
    count = int(repeat_memory.get("count", 0) or 0)
    if count <= 0:
        return ""
    latest = str(repeat_memory.get("latest_summary", "")).strip() or "-"
    last_at = str(repeat_memory.get("last_at", "")).strip()
    detail = f"count={count} latest={latest}"
    if last_at:
        detail += f" last={last_at}"
    return f"- capacity_recovery_repeat_summary: {detail}"


def _provider_repeat_count_map(memory_state: Dict[str, Any]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    history = memory_state.get("recovery_repeat_history") if isinstance(memory_state, dict) else None
    if not isinstance(history, list):
        return result
    for row in history:
        if not isinstance(row, dict):
            continue
        for alias in row.get("aliases") or []:
            token = str(alias or "").strip().upper()
            if not token:
                continue
            result[token] = int(result.get(token, 0) or 0) + 1
    return result


def _annotate_reports_with_provider_repeat_memory(reports: List[Dict[str, Any]], memory_state: Dict[str, Any]) -> List[Dict[str, Any]]:
    repeat_counts = _provider_repeat_count_map(memory_state)
    history = memory_state.get("recovery_repeat_history") if isinstance(memory_state, dict) else None
    latest_by_alias: Dict[str, str] = {}
    if isinstance(history, list):
        for row in history:
            if not isinstance(row, dict):
                continue
            at = str(row.get("at", "")).strip()
            for alias in row.get("aliases") or []:
                token = str(alias or "").strip().upper()
                if token and at:
                    latest_by_alias[token] = at
    annotated: List[Dict[str, Any]] = []
    for row in reports:
        if not isinstance(row, dict):
            continue
        alias = str(row.get("alias", "")).strip().upper()
        enriched = dict(row)
        enriched["capacity_repeat_count"] = int(repeat_counts.get(alias, 0) or 0)
        enriched["capacity_repeat_last_at"] = str(latest_by_alias.get(alias, "")).strip()
        annotated.append(enriched)
    return annotated


def _provider_capacity_policy(
    summary: Dict[str, Any],
    recovery_repeat: Optional[Dict[str, Any]] = None,
    recovery_repeat_memory: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    if not isinstance(summary, dict):
        return {}
    try:
        task_count = int(summary.get("task_count", 0) or 0)
    except Exception:
        task_count = 0
    try:
        project_count = int(summary.get("project_count", 0) or 0)
    except Exception:
        project_count = 0
    provider_counts = summary.get("provider_counts") if isinstance(summary.get("provider_counts"), dict) else {}
    provider_names = {str(key or "").strip().lower() for key in provider_counts.keys() if str(key or "").strip()}
    if task_count <= 0:
        return {}
    both_primary = {"codex", "claude"}.issubset(provider_names)
    repeat_summary = str((recovery_repeat or {}).get("summary", "")).strip()
    if repeat_summary:
        if both_primary:
            return {
                "level": "critical",
                "reason": f"same recovered project hit both primary providers again after recovery grace ({repeat_summary})",
                "operator_action": "/auto off",
            }
        return {
            "level": "elevated",
            "reason": f"same recovered project hit provider cooldown again after recovery grace ({repeat_summary})",
            "operator_action": "/offdesk review",
        }
    memory_count = int((recovery_repeat_memory or {}).get("count", 0) or 0)
    memory_latest = str((recovery_repeat_memory or {}).get("latest_summary", "")).strip()
    if memory_count >= 2:
        memory_suffix = f"recent repeat history count={memory_count}"
        if memory_latest:
            memory_suffix += f" latest={memory_latest}"
        if both_primary:
            return {
                "level": "critical",
                "reason": f"both primary providers are blocked with {memory_suffix}",
                "operator_action": "/auto off",
            }
        if task_count >= 1:
            return {
                "level": "elevated",
                "reason": f"provider cooldown is recurring with {memory_suffix}",
                "operator_action": "/offdesk review",
            }
    if both_primary and (task_count >= 2 or project_count >= 2):
        return {
            "level": "critical",
            "reason": "both primary providers are blocked across multiple tasks/projects",
            "operator_action": "/auto off",
        }
    if both_primary:
        return {
            "level": "elevated",
            "reason": "both primary providers are blocked",
            "operator_action": "/auto status",
        }
    if task_count >= 2 or project_count >= 2:
        return {
            "level": "elevated",
            "reason": "provider cooldown is affecting multiple tasks/projects",
            "operator_action": "/offdesk review",
        }
    return {
        "level": "cooldown",
        "reason": "provider cooldown is isolated to a single task",
        "operator_action": "/auto status",
    }


def _provider_capacity_memory_lines(memory_state: Dict[str, Any]) -> List[str]:
    if not isinstance(memory_state, dict):
        return []
    lines: List[str] = []
    updated_at = str(memory_state.get("updated_at", "")).strip()
    providers = memory_state.get("providers") if isinstance(memory_state.get("providers"), dict) else {}
    recovery_repeat = memory_state.get("recovery_repeat") if isinstance(memory_state.get("recovery_repeat"), dict) else {}
    history = memory_state.get("override_history") if isinstance(memory_state.get("override_history"), list) else []
    if updated_at:
        lines.append(f"- capacity_memory_updated_at: {updated_at}")
    repeat_summary = str(recovery_repeat.get("summary", "")).strip()
    if repeat_summary:
        lines.append(f"- capacity_recovery_repeat_memory: {repeat_summary}")
    repeat_count = int(memory_state.get("recovery_repeat_count", 0) or 0)
    repeat_last_at = str(memory_state.get("recovery_repeat_last_at", "")).strip()
    repeat_history = [
        row
        for row in (memory_state.get("recovery_repeat_history") if isinstance(memory_state.get("recovery_repeat_history"), list) else [])
        if isinstance(row, dict)
    ]
    if repeat_count > 0:
        detail = f"count={repeat_count}"
        if repeat_last_at:
            detail += f" last={repeat_last_at}"
        lines.append(f"- capacity_recovery_repeat_stats: {detail}")
    if repeat_history:
        compact = []
        for row in repeat_history[-3:]:
            summary = str(row.get("summary", "")).strip() or "-"
            at = str(row.get("at", "")).strip() or "-"
            compact.append(f"{summary}@{at}")
        if compact:
            lines.append(f"- capacity_recovery_repeat_history: {'; '.join(compact)}")
    if providers:
        parts: List[str] = []
        for name in sorted(str(key).strip().lower() for key in providers.keys() if str(key).strip()):
            row = providers.get(name) if isinstance(providers.get(name), dict) else {}
            blocked_count = int(row.get("blocked_count", 0) or 0)
            level = str(row.get("cooldown_level", "")).strip() or "cooldown"
            retry_at = str(row.get("next_retry_at", "")).strip() or "-"
            wait_bucket = str(row.get("retry_wait_bucket", "")).strip()
            if not wait_bucket:
                parsed = _parse_iso_datetime(retry_at)
                current = _parse_iso_datetime(updated_at) or datetime.now(timezone.utc)
                retry_wait_sec = max(0.0, (parsed - current).total_seconds()) if parsed is not None else 0.0
                if retry_wait_sec >= 1800:
                    wait_bucket = "long"
                elif retry_wait_sec >= 540:
                    wait_bucket = "medium"
                else:
                    wait_bucket = "short"
            project_count = int(row.get("project_count", 0) or 0)
            repeat_projects = [str(x).strip().upper() for x in (row.get("repeat_projects") or []) if str(x).strip()]
            repeat_suffix = f" repeat={','.join(repeat_projects)}" if repeat_projects else ""
            parts.append(
                f"{name}(blocked={blocked_count} projects={project_count} level={level} wait={wait_bucket} retry={retry_at}{repeat_suffix})"
            )
        if parts:
            lines.append(f"- provider_memory: {', '.join(parts)}")
    if history:
        last = history[-1] if isinstance(history[-1], dict) else {}
        action = str(last.get("action", "")).strip() or "-"
        at = str(last.get("at", "")).strip() or "-"
        level = str(last.get("policy_level", "")).strip() or "-"
        lines.append(f"- capacity_override_last: {action} @ {at} ({level})")
    return lines


def _prune_provider_capacity_state(memory_state: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    state = dict(memory_state) if isinstance(memory_state, dict) else {}
    providers = state.get("providers") if isinstance(state.get("providers"), dict) else {}
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    kept: Dict[str, Any] = {}
    for name, row in providers.items():
        if not isinstance(row, dict):
            continue
        retry_at = _parse_iso_datetime(row.get("next_retry_at") or row.get("last_retry_at"))
        if retry_at is not None and retry_at <= current.astimezone(timezone.utc):
            continue
        kept[str(name)] = row
    state["providers"] = kept
    repeated = sorted(
        {
            str(alias).strip().upper()
            for row in kept.values()
            if isinstance(row, dict)
            for alias in (row.get("repeat_projects") or [])
            if str(alias).strip()
        }
    )
    if repeated:
        state["recovery_repeat"] = {
            "project_count": len(repeated),
            "aliases": repeated,
            "summary": ",".join(repeated),
        }
    else:
        state.pop("recovery_repeat", None)
        state.pop("recovery_repeat_active_summary", None)
    history = state.get("override_history") if isinstance(state.get("override_history"), list) else []
    if history:
        state["override_history"] = [row for row in history if isinstance(row, dict)][-10:]
    repeat_history = state.get("recovery_repeat_history") if isinstance(state.get("recovery_repeat_history"), list) else []
    if repeat_history:
        state["recovery_repeat_history"] = [row for row in repeat_history if isinstance(row, dict)][-10:]
    return state


def _capacity_recovery_action(
    auto_state: Dict[str, Any],
    provider_state: Dict[str, Any],
    manager_state: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if bool(auto_state.get("enabled", False)):
        return {}
    history = provider_state.get("override_history") if isinstance(provider_state.get("override_history"), list) else []
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    if str(last.get("action", "")).strip() != "/auto off":
        return {}
    retry_at = _next_rate_limited_retry_at(manager_state)
    retry_dt = _parse_iso_datetime(retry_at)
    if retry_dt is not None and retry_dt > current.astimezone(timezone.utc):
        return {
            "action": "/auto recover force",
            "reason": f"operator override can resume auto before provider retry_at ({retry_at})",
        }
    return {
        "action": "/auto recover",
        "reason": "capacity cooldown has cleared; resume the auto scheduler",
    }


def _recovery_repeat_snapshot(
    auto_state: Dict[str, Any],
    manager_state: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    grace_until = _parse_iso_datetime(auto_state.get("recovery_grace_until"))
    if grace_until is None or grace_until > current.astimezone(timezone.utc):
        return {}
    prior = {
        str(raw or "").strip().upper()
        for raw in (auto_state.get("recovery_project_aliases") or [])
        if str(raw or "").strip()
    }
    if not prior:
        return {}
    current_aliases = set(_rate_limited_project_aliases(manager_state))
    repeated = sorted(prior & current_aliases)
    if not repeated:
        return {}
    return {
        "project_count": len(repeated),
        "aliases": repeated,
        "summary": ",".join(repeated),
    }


def _capacity_recovery_target(
    auto_state: Dict[str, Any],
    *,
    focus_row: Optional[Dict[str, Any]] = None,
    normalize_prefetch_token: Optional[Callable[[Any], str]] = None,
    prefetch_display: Optional[Callable[[Any, Any, bool], str]] = None,
) -> Dict[str, str]:
    effective_command = str(auto_state.get("command", "next")).strip().lower() or "next"
    requested_command = effective_command
    if effective_command not in {"next", "fanout"}:
        effective_command = "next"
    adjusted_reason = ""
    if focus_row and effective_command == "fanout":
        effective_command = "next"
        adjusted_reason = "project lock forces next instead of fanout"
    normalize = normalize_prefetch_token or (lambda raw: str(raw or "").strip().lower())
    token = normalize(auto_state.get("prefetch", ""))
    replace_sync = bool(auto_state.get("prefetch_replace_sync", False))
    if prefetch_display:
        prefetch_summary = str(prefetch_display(token, auto_state.get("prefetch_since", ""), replace_sync) or "").strip()
    else:
        prefetch_summary = token
    target = effective_command
    if token and prefetch_summary and prefetch_summary != "-":
        target = f"{effective_command} + {prefetch_summary}"
    result = {
        "command": effective_command,
        "target": target,
    }
    if requested_command != effective_command:
        result["adjusted_reason"] = adjusted_reason or f"requested {requested_command}"
    return result

