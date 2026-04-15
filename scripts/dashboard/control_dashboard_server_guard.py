#!/usr/bin/env python3
"""Read-only runtime health collector for the control dashboard."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List

from control_dashboard_state_models import RuntimeCardDTO, ServerGuardActionDTO, ServerGuardDTO


def _fmt_percent(value: float) -> str:
    return f"{value:.0f}%"


def _fmt_gib(value: float) -> str:
    return f"{value:.1f}GiB"


def _read_meminfo() -> Dict[str, int]:
    path = Path("/proc/meminfo")
    data: Dict[str, int] = {}
    if not path.exists():
        return data
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _sep, rest = line.partition(":")
            if not key or not rest:
                continue
            token = str(rest).strip().split()
            if not token:
                continue
            try:
                data[str(key).strip()] = int(token[0])
            except Exception:
                continue
    except Exception:
        return {}
    return data


def _proc_counts() -> Dict[str, int]:
    proc_dir = Path("/proc")
    counts = {"total": 0, "python": 0, "tmux": 0, "codex": 0}
    if not proc_dir.exists():
        return counts
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        counts["total"] += 1
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip().lower()
        except Exception:
            comm = ""
        if comm.startswith("python"):
            counts["python"] += 1
        if comm == "tmux: server" or comm == "tmux":
            counts["tmux"] += 1
        try:
            cmdline = (entry / "cmdline").read_text(encoding="utf-8", errors="ignore").replace("\x00", " ").strip().lower()
        except Exception:
            cmdline = ""
        if "codex" in cmdline:
            counts["codex"] += 1
    return counts


def _dominant_next_step(reasons: List[str]) -> str:
    if any(reason.startswith("queue") for reason in reasons):
        return "/control/recovery"
    if any(reason.startswith("disk") for reason in reasons):
        return "/control/recovery"
    if any(reason.startswith("memory") or reason.startswith("load") for reason in reasons):
        return "/control/offdesk"
    if any(
        reason.startswith(prefix)
        for reason in reasons
        for prefix in ("process", "codex_process", "python_process", "tmux_process", "total_process")
    ):
        return "/control/history"
    return "/control"


def _server_guard_snapshot_path(team_dir: Path | str) -> Path:
    return Path(team_dir) / "control" / "server_guard.json"


def _has_reason(reasons: Iterable[str], *prefixes: str) -> bool:
    return any(str(reason).startswith(prefix) for reason in reasons for prefix in prefixes)


def _server_guard_cleanup_target(cards: Iterable[RuntimeCardDTO]) -> RuntimeCardDTO | None:
    candidates = [row for row in cards if int(getattr(row, "background_queue_stale_count", 0) or 0) > 0]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            -int(getattr(row, "background_queue_stale_count", 0) or 0),
            0 if str(getattr(row, "status", "")).strip() == "blocked" else 1,
            -int(getattr(row, "severity_score", 0) or 0),
            str(getattr(row, "project_alias", "")).strip(),
        )
    )
    return candidates[0]


def _recommended_actions(
    *,
    reasons: List[str],
    next_step: str,
    runtime_cards: Iterable[RuntimeCardDTO],
) -> list[ServerGuardActionDTO]:
    actions: list[ServerGuardActionDTO] = []
    cleanup_target = _server_guard_cleanup_target(runtime_cards)

    def _add_link(label: str, href: str, note: str) -> None:
        if any(str(row.href).strip() == href and not str(row.path).strip() for row in actions):
            return
        actions.append(ServerGuardActionDTO(label=label, href=href, note=note))

    def _add_action(label: str, path: str, payload: Dict[str, Any], note: str, *, command: str, mode: str = "safe") -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if any(str(row.path).strip() == path and str(row.payload_json).strip() == payload_json for row in actions):
            return
        actions.append(
            ServerGuardActionDTO(
                label=label,
                note=note,
                method="POST",
                path=path,
                mode=mode,
                payload_json=payload_json,
                command=command,
            )
        )

    _add_link("Open Health View", "/control/health/view", "inspect the operator-facing host health card view")
    _add_link("Open Health JSON", "/control/health", "inspect the raw host and queue snapshot")
    if next_step == "/control/recovery":
        _add_link("Open Recovery", "/control/recovery?focus=server-guard", "review stale queue, retries, and blocked runtimes first")
    if next_step == "/control/offdesk":
        _add_link("Open Offdesk", "/control/offdesk", "reduce concurrent work before retrying under host pressure")
    if next_step == "/control/history":
        _add_link("Open History", "/control/history", "inspect recent operator and worker activity")
    if any(reason.startswith("queue") for reason in reasons):
        _add_link("Open Recovery", "/control/recovery?focus=server-guard", "queue or stale runtime pressure needs recovery review")
    if any(reason.startswith("disk") for reason in reasons):
        _add_link("Open Recovery", "/control/recovery?focus=server-guard", "review artifacts and cleanup targets before write-heavy work")
    if any(
        reason.startswith(prefix)
        for reason in reasons
        for prefix in ("process", "codex_process", "python_process", "tmux_process", "total_process")
    ):
        _add_link("Open Audit", "/control/audit?focus=server-guard&limit=50", "inspect recent high-frequency operator actions")
        _add_link("Open History", "/control/history", "inspect background worker churn and repeated runs")
    if _has_reason(reasons, "codex_process"):
        _add_action(
            "Preview Codex Pressure",
            "/control/actions/runtime/server-guard-pressure-preview",
            {"pressure_kind": "codex"},
            "inspect codex session pressure before trimming or consolidating interactive surfaces",
            command="/ops pressure codex preview",
        )
        _add_link("Review Codex Pressure", "/control/history?q=codex&scope=control", "inspect codex session churn and trim duplicated interactive runs")
        _add_link("Open Chat Console", "/control/chat?preset=global-direct", "consolidate chat-bound codex sessions before opening more worker surfaces")
    if _has_reason(reasons, "python_process"):
        _add_action(
            "Preview Python Pressure",
            "/control/actions/runtime/server-guard-pressure-preview",
            {"pressure_kind": "python"},
            "inspect python worker pressure before launching more local background tasks",
            command="/ops pressure python preview",
        )
        _add_link("Review Python Pressure", "/control/history?q=python&scope=control", "inspect python worker churn and repeated local background launches")
        _add_link("Open Package Rail", "/control/chat?preset=package-rail", "open the package-oriented chat rail before revisiting python-backed worker activity")
        _add_link("Open Recovery", "/control/recovery?focus=server-guard", "review worker and queue rails before starting more python-backed jobs")
    if _has_reason(reasons, "tmux_process"):
        _add_action(
            "Preview Tmux Pressure",
            "/control/actions/runtime/server-guard-pressure-preview",
            {"pressure_kind": "tmux"},
            "inspect detached tmux runtime pressure before starting more off-desk workers",
            command="/ops pressure tmux preview",
        )
        _add_link("Review Tmux Pressure", "/control/history?q=tmux&scope=control", "inspect detached runtime sessions and stale tmux-backed workers")
        _add_link("Open Review Rail", "/control/chat?preset=review-rail", "open the review chat rail before restarting detached tmux-backed workers")
    if _has_reason(reasons, "total_process"):
        _add_action(
            "Preview Process Pressure",
            "/control/actions/runtime/server-guard-pressure-preview",
            {"pressure_kind": "process"},
            "inspect total process pressure before widening worker fanout",
            command="/ops pressure process preview",
        )
        _add_link("Review Process Pressure", "/control/history?q=process&scope=control", "inspect broad process churn before launching additional work")
        _add_link("Open Analysis Rail", "/control/chat?preset=analysis-rail", "open the analysis rail for lower-fanout triage while total process pressure is elevated")
    if any(reason.startswith("memory") or reason.startswith("load") for reason in reasons):
        _add_link("Open Offdesk", "/control/offdesk", "pause and review host pressure before running more work")
    if cleanup_target is not None:
        project_ref = str(getattr(cleanup_target, "project_alias", "")).strip()
        if project_ref:
            _add_action(
                "Preview Queue Cleanup",
                "/control/actions/runtime/background-queue-clean-preview",
                {"project_ref": project_ref},
                "inspect stale queue tickets before mutating background queue state",
                command=f"/orch bgq-clean {project_ref} preview",
            )
    return actions[:12]


def write_server_guard_snapshot(*, team_dir: Path | str, snapshot_taken_at: str, guard: ServerGuardDTO) -> tuple[str, str]:
    path = _server_guard_snapshot_path(team_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_taken_at": str(snapshot_taken_at or "").strip(),
        "status": guard.status,
        "summary": guard.summary,
        "reason_summary": guard.reason_summary,
        "note": guard.note,
        "next_step": guard.next_step,
        "disk_summary": guard.disk_summary,
        "memory_summary": guard.memory_summary,
        "load_summary": guard.load_summary,
        "process_summary": guard.process_summary,
        "queue_summary": guard.queue_summary,
        "recommended_actions": [
            {
                "label": row.label,
                "href": row.href,
                "note": row.note,
                "method": row.method,
                "path": row.path,
                "mode": row.mode,
                "payload_json": row.payload_json,
                "command": row.command,
            }
            for row in list(guard.recommended_actions or [])
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path), str(snapshot_taken_at or "").strip()


def build_server_guard(
    *,
    control_root: Path | str,
    team_dir: Path | str,
    runtime_cards: Iterable[RuntimeCardDTO],
) -> ServerGuardDTO:
    team = Path(team_dir)
    usage = shutil.disk_usage(team)
    disk_used_pct = ((usage.used / usage.total) * 100.0) if usage.total > 0 else 0.0
    disk_summary = (
        f"used={_fmt_percent(disk_used_pct)} | free={_fmt_gib(usage.free / (1024 ** 3))} | path={team}"
    )

    meminfo = _read_meminfo()
    mem_total_kib = int(meminfo.get("MemTotal", 0) or 0)
    mem_available_kib = int(meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) or 0)
    mem_available_pct = ((mem_available_kib / mem_total_kib) * 100.0) if mem_total_kib > 0 else 0.0
    memory_summary = (
        f"available={_fmt_percent(mem_available_pct)} | total={_fmt_gib(mem_total_kib / (1024 ** 2))} | avail={_fmt_gib(mem_available_kib / (1024 ** 2))}"
        if mem_total_kib > 0
        else "available=- | total=- | avail=-"
    )

    cpu_count = max(1, int(os.cpu_count() or 1))
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1, load5, load15 = 0.0, 0.0, 0.0
    load_norm = load1 / cpu_count
    load_summary = f"load1={load1:.2f} load5={load5:.2f} load15={load15:.2f} | cpu={cpu_count} | norm={load_norm:.2f}"

    counts = _proc_counts()
    process_summary = (
        f"total={counts['total']} | python={counts['python']} | tmux={counts['tmux']} | codex={counts['codex']}"
    )

    cards = list(runtime_cards)
    queue_depth = sum(int(getattr(card, "background_queue_depth", 0) or 0) for card in cards)
    queue_stale = sum(int(getattr(card, "background_queue_stale_count", 0) or 0) for card in cards)
    attention = len([card for card in cards if str(getattr(card, "status", "")).strip() in {"warn", "blocked"}])
    queue_summary = f"depth={queue_depth} | stale={queue_stale} | attention={attention}"

    reasons: List[str] = []
    status = "ok"

    def _raise(level: str, reason: str) -> None:
        nonlocal status
        reasons.append(reason)
        if status == "blocked":
            return
        if level == "blocked":
            status = "blocked"
            return
        if status != "warn":
            status = "warn"

    if disk_used_pct >= 92.0:
        _raise("blocked", f"disk_critical:{_fmt_percent(disk_used_pct)}")
    elif disk_used_pct >= 85.0:
        _raise("warn", f"disk_high:{_fmt_percent(disk_used_pct)}")

    if mem_total_kib > 0:
        if mem_available_pct <= 8.0:
            _raise("blocked", f"memory_low:{_fmt_percent(mem_available_pct)}")
        elif mem_available_pct <= 15.0:
            _raise("warn", f"memory_warn:{_fmt_percent(mem_available_pct)}")

    if load_norm >= 2.0:
        _raise("blocked", f"load_hot:{load_norm:.2f}")
    elif load_norm >= 1.2:
        _raise("warn", f"load_warm:{load_norm:.2f}")

    if queue_stale >= 3:
        _raise("blocked", f"queue_stale:{queue_stale}")
    elif queue_stale >= 1:
        _raise("warn", f"queue_warn:{queue_stale}")

    if counts["codex"] >= 70:
        _raise("blocked", f"codex_process_high:{counts['codex']}")
    elif counts["codex"] >= 40:
        _raise("warn", f"codex_process_warn:{counts['codex']}")

    if counts["python"] >= 160:
        _raise("blocked", f"python_process_high:{counts['python']}")
    elif counts["python"] >= 80:
        _raise("warn", f"python_process_warn:{counts['python']}")

    if counts["tmux"] >= 60:
        _raise("blocked", f"tmux_process_high:{counts['tmux']}")
    elif counts["tmux"] >= 20:
        _raise("warn", f"tmux_process_warn:{counts['tmux']}")

    if counts["total"] >= 900:
        _raise("blocked", f"total_process_high:{counts['total']}")
    elif counts["total"] >= 500:
        _raise("warn", f"total_process_warn:{counts['total']}")

    next_step = _dominant_next_step(reasons)
    recommended_actions = _recommended_actions(reasons=reasons, next_step=next_step, runtime_cards=cards)
    if not reasons:
        reason_summary = "-"
        note = "server guard is stable; continue with normal operator flow"
    elif any(reason.startswith("queue") for reason in reasons):
        reason_summary = " | ".join(reasons)
        note = "background queue or stale runtime pressure needs recovery review first"
    elif any(reason.startswith("disk") for reason in reasons):
        reason_summary = " | ".join(reasons)
        note = "disk pressure is high; free space before promoting more worker activity"
    elif any(reason.startswith("memory") or reason.startswith("load") for reason in reasons):
        reason_summary = " | ".join(reasons)
        note = "host pressure is elevated; reduce concurrent execution before retrying"
    elif _has_reason(reasons, "codex_process"):
        reason_summary = " | ".join(reasons)
        note = "codex process pressure is elevated; consolidate chat and operator sessions before launching more work"
    elif _has_reason(reasons, "python_process"):
        reason_summary = " | ".join(reasons)
        note = "python worker pressure is elevated; inspect local background churn before retrying"
    elif _has_reason(reasons, "tmux_process"):
        reason_summary = " | ".join(reasons)
        note = "tmux session pressure is elevated; inspect detached runtime handles and stale sessions"
    elif _has_reason(reasons, "total_process"):
        reason_summary = " | ".join(reasons)
        note = "overall process pressure is elevated; inspect recent worker churn and recovery surfaces"
    else:
        reason_summary = " | ".join(reasons)
        note = "process pressure is elevated; inspect active background workers and history"

    summary = (
        f"status={status} | disk={_fmt_percent(disk_used_pct)} | mem={_fmt_percent(mem_available_pct) if mem_total_kib > 0 else '-'} "
        f"| load={load_norm:.2f} | proc={counts['total']} | queue_stale={queue_stale}"
    )

    return ServerGuardDTO(
        status=status,
        summary=summary,
        reason_summary=reason_summary,
        note=note,
        next_step=next_step,
        disk_summary=disk_summary,
        memory_summary=memory_summary,
        load_summary=load_summary,
        process_summary=process_summary,
        queue_summary=queue_summary,
        recommended_actions=recommended_actions,
    )
