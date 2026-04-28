#!/usr/bin/env python3
"""Read-only project flow compiler for runtime/document convergence."""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from aoe_tg_artifact_backend import artifact_backend, load_json_file
from aoe_tg_orch_contract import derive_tf_phase, normalize_tf_phase


PROJECT_FLOW_VERSION = "2026-04-28.v1"
REGISTRY_ROOT = Path("docs/investigations_mo/registry")
PROJECT_REGISTRY_REL = REGISTRY_ROOT / "project_registry.md"
PROJECT_LOCK_REL = REGISTRY_ROOT / "project_lock.yaml"
TF_REGISTRY_REL = REGISTRY_ROOT / "tf_registry.md"
HANDOFF_INDEX_REL = REGISTRY_ROOT / "handoff_index.csv"
TF_CLOSE_INDEX_REL = REGISTRY_ROOT / "tf_close_index.csv"
_CLOSED_STATUSES = {"closed", "done", "completed", "cancelled", "canceled", "archived", "success"}
_ACTIVE_TASK_STATUSES = {"pending", "running", "active", "in_progress", "queued", "blocked", "waiting_on_dependencies", "rate_limited"}


def _trim(raw: Any, limit: int = 240) -> str:
    return str(raw or "").strip()[: max(0, int(limit or 0))]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _normalize_alias(raw: Any) -> str:
    token = _trim(raw, 32).upper()
    return token if re.fullmatch(r"O[1-9]\d{0,2}", token) else ""


def _clean_cell(raw: Any) -> str:
    text = _trim(raw, 400).strip()
    if text.startswith("`") and text.endswith("`"):
        text = text[1:-1].strip()
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    return text


def _key(raw: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _trim(raw, 80).lower()).strip("_")


def _dedupe(rows: Iterable[str], *, limit: int = 8, item_limit: int = 180) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for row in rows:
        token = _trim(row, item_limit)
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= limit:
            break
    return out


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _relative_label(path: Path | str, project_root: Path) -> str:
    token = _trim(path, 400)
    if not token:
        return ""
    resolved = Path(token).expanduser()
    if not resolved.is_absolute():
        resolved = project_root / resolved
    try:
        return resolved.resolve().relative_to(project_root.resolve()).as_posix()
    except Exception:
        return resolved.as_posix()


def _resolve_doc_path(project_root: Path, raw: Any) -> Path:
    token = _clean_cell(raw)
    if not token:
        return project_root / "__missing__"
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _split_md_cells(line: str) -> List[str]:
    return [_clean_cell(cell) for cell in line.strip().strip("|").split("|")]


def _is_separator_row(cells: List[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _markdown_tables(text: str) -> List[List[Dict[str, str]]]:
    tables: List[List[Dict[str, str]]] = []
    header: List[str] = []
    rows: List[Dict[str, str]] = []

    def flush() -> None:
        nonlocal header, rows
        if header and rows:
            tables.append(rows)
        header = []
        rows = []

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            flush()
            continue
        cells = _split_md_cells(line)
        if _is_separator_row(cells):
            continue
        if not header:
            header = [_key(cell) for cell in cells]
            continue
        padded = cells + [""] * max(0, len(header) - len(cells))
        rows.append({header[index]: padded[index] for index in range(len(header))})
    flush()
    return tables


def _first_table_with(text: str, required: Iterable[str]) -> List[Dict[str, str]]:
    wanted = {_key(item) for item in required}
    for table in _markdown_tables(text):
        keys = set(table[0].keys()) if table else set()
        if wanted.issubset(keys):
            return table
    return []


def _load_project_registry(project_root: Path) -> Dict[str, Dict[str, str]]:
    rows = _first_table_with(_read_text(project_root / PROJECT_REGISTRY_REL), ("project_alias", "purpose"))
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        alias = _normalize_alias(row.get("project_alias"))
        if alias:
            out[alias] = row
    return out


def _load_tf_registry(project_root: Path) -> List[Dict[str, str]]:
    return _first_table_with(_read_text(project_root / TF_REGISTRY_REL), ("tf_id", "project_alias"))


def _load_tf_close_index(project_root: Path) -> List[Dict[str, str]]:
    path = project_root / TF_CLOSE_INDEX_REL
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [{_key(key): _clean_cell(value) for key, value in row.items()} for row in csv.DictReader(handle)]
    except Exception:
        return []


def _load_project_lock(project_root: Path) -> Dict[str, Any]:
    path = project_root / PROJECT_LOCK_REL
    out: Dict[str, Any] = {"active_paths": {}}
    current_map = ""
    for raw in _read_text(path).splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        match = re.match(r"([A-Za-z0-9_-]+):\s*(.*)$", stripped)
        if not match:
            continue
        key, value = match.group(1), _clean_cell(match.group(2))
        if indent == 0:
            current_map = key if not value else ""
            if current_map:
                out.setdefault(current_map, {})
            else:
                out[key] = value
            continue
        if current_map:
            target = out.setdefault(current_map, {})
            if isinstance(target, dict):
                target[key] = value
    return out


def _heading_sections(text: str) -> List[Tuple[str, List[str]]]:
    sections: List[Tuple[str, List[str]]] = []
    title = ""
    body: List[str] = []
    for raw in str(text or "").splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", raw)
        if match:
            if title or body:
                sections.append((title, body))
            title = _trim(match.group(1), 120).strip("# ")
            body = []
        else:
            body.append(raw)
    if title or body:
        sections.append((title, body))
    return sections


def _clean_doc_item(raw: str) -> str:
    text = raw.strip()
    if not text or text.startswith("#") or text.startswith("|"):
        return ""
    text = re.sub(r"^\s*[-*]\s+\[[ xX]\]\s+", "", text)
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text)
    text = text.strip(" -\t")
    return _trim(text, 180)


def _placeholder(text: str) -> bool:
    token = _trim(text, 180).lower()
    return (
        not token
        or token in {"-", "n/a", "na", "none", "tbd", "todo", "unknown"}
        or "__fill" in token
        or "insert " in token
    )


def _section_items(text: str, title_needles: Iterable[str], *, limit: int = 8) -> List[str]:
    needles = [needle.lower() for needle in title_needles]
    rows: List[str] = []
    for title, body in _heading_sections(text):
        lower = title.lower()
        if not any(needle in lower for needle in needles):
            continue
        for line in body:
            item = _clean_doc_item(line)
            if item and not _placeholder(item):
                rows.append(item)
    return _dedupe(rows, limit=limit)


def _extract_objective(ongoing_text: str) -> str:
    for item in _section_items(ongoing_text, ("objective", "goal", "purpose"), limit=3):
        if not _placeholder(item):
            return item
    return ""


def _extract_todo_table_items(ongoing_text: str) -> List[str]:
    rows: List[str] = []
    for table in _markdown_tables(ongoing_text):
        if not table:
            continue
        keys = set(table[0].keys())
        if not ({"todo_id", "summary"} <= keys or {"id", "summary"} <= keys):
            continue
        for row in table:
            status = _trim(row.get("status"), 32).lower()
            if status in _CLOSED_STATUSES:
                continue
            todo_id = row.get("todo_id") or row.get("id") or ""
            summary = row.get("summary") or row.get("title") or row.get("task") or ""
            item = f"{todo_id}: {summary}" if todo_id and summary else summary or todo_id
            if item and not _placeholder(item):
                rows.append(item)
    return _dedupe(rows, limit=8)


def _extract_doc_signals(ongoing_text: str, note_text: str, report_text: str) -> Dict[str, Any]:
    objective = _extract_objective(ongoing_text)
    next_steps = _extract_todo_table_items(ongoing_text)
    if not next_steps:
        next_steps = _section_items(
            ongoing_text,
            ("next", "todo", "queue", "open", "remaining", "follow-up", "followup", "action"),
        )
    decisions = _dedupe(
        _section_items(ongoing_text, ("decision", "decisions", "결정"), limit=8)
        + _section_items(note_text, ("decision", "decisions", "결정"), limit=8),
        limit=8,
    )
    blockers = _dedupe(
        _section_items(ongoing_text, ("blocker", "blocked", "risk", "issue", "open_risk", "open risks", "남은"), limit=8)
        + _section_items(report_text, ("blocker", "blocked", "risk", "issue", "open_risk", "open risks", "남은"), limit=8),
        limit=8,
    )
    return {
        "document_objective": objective,
        "document_next_steps": next_steps,
        "document_open_decisions": decisions,
        "document_blockers": blockers,
    }


def _entry_alias(entry: Dict[str, Any], fallback: str = "") -> str:
    return _normalize_alias(entry.get("project_alias")) or _normalize_alias(fallback) or _trim(fallback, 32)


def _find_runtime_entry(
    manager_state: Dict[str, Any],
    *,
    project_alias: str,
    project_key: str = "",
    entry: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    if isinstance(entry, dict):
        return project_key, entry
    projects = manager_state.get("projects") if isinstance(manager_state, dict) else {}
    if not isinstance(projects, dict):
        return "", {}
    if project_key and isinstance(projects.get(project_key), dict):
        return project_key, projects[project_key]
    for key, row in projects.items():
        if isinstance(row, dict) and _entry_alias(row, str(key)) == project_alias:
            return str(key), row
    return "", {}


def _task_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks = entry.get("tasks") if isinstance(entry, dict) else {}
    if not isinstance(tasks, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        row = dict(task)
        row.setdefault("request_id", str(request_id))
        rows.append(row)
    return rows


def _task_closed(task: Dict[str, Any]) -> bool:
    status = _trim(task.get("status"), 40).lower()
    return status in {"completed", "done", "success", "cancelled", "canceled", "archived"}


def _active_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        status = _trim(task.get("status"), 40).lower() or "pending"
        phase = normalize_tf_phase(derive_tf_phase(task), "queued")
        if status in _ACTIVE_TASK_STATUSES or phase in {"queued", "planning", "running", "critic_review", "blocked", "rate_limited", "needs_retry"}:
            if not _task_closed(task):
                rows.append(task)
    return rows


def _latest_task(entry: Dict[str, Any], tasks: List[Dict[str, Any]], active: List[Dict[str, Any]]) -> Dict[str, Any]:
    last_request_id = _trim(entry.get("last_request_id"), 120)
    if last_request_id:
        for task in tasks:
            if _trim(task.get("request_id"), 120) == last_request_id:
                return task
    if active:
        return active[-1]
    return tasks[-1] if tasks else {}


def _runtime_snapshot(entry: Dict[str, Any], team_dir: Path) -> Dict[str, Any]:
    if not entry:
        return {
            "runtime_status": "missing",
            "active_request_ids": [],
            "active_task_short_ids": [],
            "latest_runtime_phase": "",
            "provider_pressure_summary": "-",
            "runtime_first_focus": "",
        }
    tasks = _task_rows(entry)
    active = _active_tasks(tasks)
    latest = _latest_task(entry, tasks, active)
    failed = [task for task in tasks if _trim(task.get("status"), 40).lower() == "failed"]
    runtime_status = "active" if active else "attention" if failed else "idle"
    latest_phase = normalize_tf_phase(derive_tf_phase(latest), "queued") if latest else ""
    active_request_ids = _dedupe((_trim(task.get("request_id"), 120) for task in active), limit=12, item_limit=120)
    active_task_short_ids = _dedupe((_trim(task.get("short_id"), 40).upper() for task in active), limit=12, item_limit=40)
    first_focus = ""
    if active:
        task = active[0]
        prompt = _trim(task.get("prompt") or task.get("title") or task.get("summary"), 160)
        first_focus = f"{_trim(task.get('short_id'), 40) or _trim(task.get('request_id'), 80)}: {prompt}".strip(": ")
    elif isinstance(entry.get("todos"), list):
        for todo in entry.get("todos") or []:
            if isinstance(todo, dict) and _trim(todo.get("status"), 24).lower() in {"", "open", "running"}:
                first_focus = _trim(todo.get("summary") or todo.get("title") or todo.get("id"), 160)
                break
    return {
        "runtime_status": runtime_status,
        "active_request_ids": active_request_ids,
        "active_task_short_ids": active_task_short_ids,
        "latest_runtime_phase": latest_phase,
        "provider_pressure_summary": _provider_pressure_summary(team_dir, active),
        "runtime_first_focus": first_focus,
    }


def _provider_pressure_summary(team_dir: Path, active_tasks: List[Dict[str, Any]]) -> str:
    task_pressures: List[str] = []
    for task in active_tasks:
        rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
        mode = _trim(rate_limit.get("mode"), 40).lower()
        if not mode or mode in {"ok", "ready", "available"}:
            continue
        providers = [str(item).strip() for item in rate_limit.get("limited_providers", []) if str(item).strip()]
        label = ",".join(providers) if providers else _trim(task.get("request_id"), 80)
        task_pressures.append(f"{mode}:{label}")
    capacity = artifact_backend(team_dir).load_provider_capacity_state()
    providers = capacity.get("providers") if isinstance(capacity, dict) else {}
    if isinstance(providers, dict):
        for name, row in providers.items():
            if not isinstance(row, dict):
                continue
            mode = _trim(row.get("mode") or row.get("status"), 40).lower()
            if mode and mode not in {"ok", "ready", "available", "normal"}:
                task_pressures.append(f"{name}:{mode}")
    return ", ".join(_dedupe(task_pressures, limit=6, item_limit=80)) or "-"


def _registry_report_for_tf(tf_rows: List[Dict[str, str]], tf_id: str) -> str:
    token = _trim(tf_id, 80)
    if not token:
        return ""
    for row in tf_rows:
        if _trim(row.get("tf_id"), 80) == token:
            return _clean_cell(row.get("report_doc"))
    return ""


def _lineage_snapshot(
    *,
    project_alias: str,
    project_root: Path,
    project_lock: Dict[str, Any],
    tf_rows: List[Dict[str, str]],
    close_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    rows = [row for row in tf_rows if _normalize_alias(row.get("project_alias")) == project_alias]
    closed = [row for row in close_rows if _normalize_alias(row.get("project_alias")) == project_alias]
    open_tf_ids = _dedupe(
        (
            _trim(row.get("tf_id"), 80)
            for row in rows
            if _trim(row.get("status"), 40).lower() not in _CLOSED_STATUSES
        ),
        limit=12,
        item_limit=80,
    )
    recent_closed_tf_ids = _dedupe((_trim(row.get("tf_id"), 80) for row in closed), limit=8, item_limit=80)
    if not recent_closed_tf_ids:
        recent_closed_tf_ids = _dedupe(
            (
                _trim(row.get("tf_id"), 80)
                for row in rows
                if _trim(row.get("status"), 40).lower() in _CLOSED_STATUSES
            ),
            limit=8,
            item_limit=80,
        )

    active_tf = _trim(project_lock.get("active_tf"), 80)
    lock_paths = project_lock.get("active_paths") if isinstance(project_lock.get("active_paths"), dict) else {}
    latest_report = ""
    if _normalize_alias(project_lock.get("active_project")) == project_alias:
        latest_report = _clean_cell(lock_paths.get("tf_report") if isinstance(lock_paths, dict) else "")
    latest_report = latest_report or _registry_report_for_tf(rows, active_tf)
    if not latest_report and open_tf_ids:
        latest_report = _registry_report_for_tf(rows, open_tf_ids[0])
    if not latest_report:
        for row in closed:
            latest_report = _clean_cell(row.get("report_doc"))
            if latest_report:
                break
    return {
        "latest_tf_report_path": _relative_label(_resolve_doc_path(project_root, latest_report), project_root) if latest_report else "",
        "open_tf_ids": open_tf_ids,
        "recent_closed_tf_ids": recent_closed_tf_ids,
    }


def _default_manager_state(team_dir: Path) -> Dict[str, Any]:
    return load_json_file(team_dir / "orch_manager_state.json")


def _resolve_project_root(project_root: Any, team_dir: Path, entry: Any = None) -> Path:
    entry_data = entry if isinstance(entry, dict) else {}
    backend = artifact_backend(team_dir)
    workspace = backend.load_workspace_brief()
    token = (
        _trim(project_root, 400)
        or _trim(entry_data.get("project_root"), 400)
        or _trim(workspace.get("project_root"), 400)
    )
    if token:
        return Path(token).expanduser().resolve()
    if team_dir.name == ".aoe-team":
        return team_dir.parent.resolve()
    return Path.cwd().resolve()


def _select_alias(
    *,
    explicit_alias: str,
    project_lock: Dict[str, Any],
    registry: Dict[str, Dict[str, str]],
    entry: Any = None,
    project_key: str = "",
) -> str:
    entry_data = entry if isinstance(entry, dict) else {}
    return (
        _normalize_alias(explicit_alias)
        or _normalize_alias(entry_data.get("project_alias"))
        or _normalize_alias(project_key)
        or _normalize_alias(project_lock.get("active_project"))
        or next((alias for alias, row in registry.items() if _trim(row.get("status"), 32).lower() == "active"), "")
        or next(iter(registry.keys()), "")
        or "O1"
    )


def _doc_paths_for_project(project_root: Path, project_alias: str, row: Dict[str, str], project_lock: Dict[str, Any]) -> Dict[str, str]:
    active_in_lock = _normalize_alias(project_lock.get("active_project")) == project_alias
    lock_paths = project_lock.get("active_paths") if isinstance(project_lock.get("active_paths"), dict) else {}
    ongoing = _clean_cell(row.get("ongoing_doc"))
    note = _clean_cell(row.get("note_doc"))
    if active_in_lock and isinstance(lock_paths, dict):
        ongoing = ongoing or _clean_cell(lock_paths.get("project_ongoing"))
        note = note or _clean_cell(lock_paths.get("project_note"))
    return {
        "ongoing_doc_path": _relative_label(_resolve_doc_path(project_root, ongoing), project_root) if ongoing else "",
        "note_doc_path": _relative_label(_resolve_doc_path(project_root, note), project_root) if note else "",
    }


def _stale_doc_refs(project_root: Path, doc_paths: Dict[str, str], latest_tf_report_path: str) -> List[str]:
    refs: List[str] = []
    for key in ("ongoing_doc_path", "note_doc_path"):
        label = _trim(doc_paths.get(key), 400)
        if label and not _resolve_doc_path(project_root, label).exists():
            refs.append(label)
    if latest_tf_report_path and not _resolve_doc_path(project_root, latest_tf_report_path).exists():
        refs.append(latest_tf_report_path)
    return _dedupe(refs, limit=12, item_limit=220)


def _evidence_refs(
    *,
    project_root: Path,
    doc_paths: Dict[str, str],
    latest_tf_report_path: str,
    active_request_ids: List[str],
) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for rel in (PROJECT_REGISTRY_REL, PROJECT_LOCK_REL, TF_REGISTRY_REL, HANDOFF_INDEX_REL, TF_CLOSE_INDEX_REL):
        path = project_root / rel
        if path.exists():
            refs.append({"kind": "registry", "path": rel.as_posix()})
    for key in ("ongoing_doc_path", "note_doc_path"):
        path = _trim(doc_paths.get(key), 400)
        if path:
            refs.append({"kind": key.replace("_path", ""), "path": path})
    if latest_tf_report_path:
        refs.append({"kind": "tf_report", "path": latest_tf_report_path})
    for request_id in active_request_ids[:6]:
        refs.append({"kind": "runtime_request", "request_id": request_id})
    return refs


def _drift_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    runtime_active = bool(payload.get("active_request_ids"))
    project_active = bool(payload.get("active_in_lock")) or _trim(payload.get("project_status"), 32).lower() == "active"
    stale_refs = [str(item).strip() for item in payload.get("stale_doc_refs", []) if str(item).strip()]
    objective_missing = not _trim(payload.get("document_objective"), 240)
    runtime_without_doc_signal = runtime_active and (objective_missing or bool(stale_refs))
    doc_without_runtime_signal = project_active and not runtime_active and _trim(payload.get("runtime_status"), 32) in {"missing", "idle"}

    if stale_refs:
        reasons.append("stale_doc_refs=" + ",".join(stale_refs[:4]))
    if objective_missing:
        reasons.append("document_objective_missing")
    if runtime_without_doc_signal:
        reasons.append("runtime_without_doc_signal")
    if doc_without_runtime_signal:
        reasons.append("doc_without_runtime_signal")
    if project_active and not payload.get("latest_tf_report_path"):
        reasons.append("latest_tf_report_missing")
    if not payload.get("document_next_steps"):
        reasons.append("document_next_steps_empty")

    level = "none"
    if stale_refs or runtime_without_doc_signal or doc_without_runtime_signal:
        level = "warning"
    elif reasons:
        level = "notice"
    return {
        "drift_level": level,
        "drift_reasons": _dedupe(reasons, limit=12, item_limit=220),
        "runtime_without_doc_signal": runtime_without_doc_signal,
        "doc_without_runtime_signal": doc_without_runtime_signal,
    }


def summarize_project_flow_payload(payload: Dict[str, Any]) -> str:
    return "alias={alias} status={status} drift={drift} active={active} open_tf={open_tf} closed_tf={closed_tf}".format(
        alias=_trim(payload.get("project_alias"), 32) or "-",
        status=_trim(payload.get("project_status"), 32) or "-",
        drift=_trim(payload.get("drift_level"), 32) or "none",
        active=len(payload.get("active_request_ids") or []),
        open_tf=len(payload.get("open_tf_ids") or []),
        closed_tf=len(payload.get("recent_closed_tf_ids") or []),
    )


def build_project_flow(
    team_dir: Path | str,
    *,
    project_root: Any = "",
    manager_state: Any = None,
    entry: Any = None,
    project_key: str = "",
    project_alias: str = "",
    compiled_at: str = "",
) -> Dict[str, Any]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    state = manager_state if isinstance(manager_state, dict) else _default_manager_state(resolved_team_dir)
    resolved_root = _resolve_project_root(project_root, resolved_team_dir, entry)
    project_lock = _load_project_lock(resolved_root)
    registry = _load_project_registry(resolved_root)
    alias = _select_alias(
        explicit_alias=project_alias,
        project_lock=project_lock,
        registry=registry,
        entry=entry,
        project_key=project_key,
    )
    runtime_key, runtime_entry = _find_runtime_entry(state, project_alias=alias, project_key=project_key, entry=entry)
    registry_row = registry.get(alias, {})
    doc_paths = _doc_paths_for_project(resolved_root, alias, registry_row, project_lock)
    tf_rows = _load_tf_registry(resolved_root)
    close_rows = _load_tf_close_index(resolved_root)
    lineage = _lineage_snapshot(
        project_alias=alias,
        project_root=resolved_root,
        project_lock=project_lock,
        tf_rows=tf_rows,
        close_rows=close_rows,
    )
    ongoing_text = _read_text(_resolve_doc_path(resolved_root, doc_paths.get("ongoing_doc_path")))
    note_text = _read_text(_resolve_doc_path(resolved_root, doc_paths.get("note_doc_path")))
    report_text = _read_text(_resolve_doc_path(resolved_root, lineage.get("latest_tf_report_path")))
    doc_signals = _extract_doc_signals(ongoing_text, note_text, report_text)
    runtime = _runtime_snapshot(runtime_entry, resolved_team_dir)
    payload: Dict[str, Any] = {
        "version": PROJECT_FLOW_VERSION,
        "project_alias": alias,
        "project_purpose": _trim(registry_row.get("purpose") or runtime_entry.get("display_name") or runtime_entry.get("name"), 180),
        "project_status": _trim(registry_row.get("status"), 40) or ("active" if _normalize_alias(project_lock.get("active_project")) == alias else "unknown"),
        "active_in_lock": _normalize_alias(project_lock.get("active_project")) == alias,
        "compiled_at": compiled_at or _now_iso(),
        "runtime_project_key": runtime_key,
        **runtime,
        **doc_paths,
        **lineage,
        **doc_signals,
    }
    payload["stale_doc_refs"] = _stale_doc_refs(resolved_root, doc_paths, payload.get("latest_tf_report_path", ""))
    payload.update(_drift_snapshot(payload))
    payload["evidence_refs"] = _evidence_refs(
        project_root=resolved_root,
        doc_paths=doc_paths,
        latest_tf_report_path=payload.get("latest_tf_report_path", ""),
        active_request_ids=payload.get("active_request_ids", []),
    )
    payload["summary"] = summarize_project_flow_payload(payload)
    return payload


def load_project_flow(
    team_dir: Path | str,
    *,
    project_alias: str,
    project_root: Any = "",
    manager_state: Any = None,
    entry: Any = None,
    project_key: str = "",
) -> Dict[str, Any]:
    backend = artifact_backend(team_dir)
    alias = _normalize_alias(project_alias) or "O1"
    existing = backend.load_project_flow(project_alias=alias)
    if existing:
        return existing
    return build_project_flow(
        team_dir,
        project_root=project_root,
        manager_state=manager_state,
        entry=entry,
        project_key=project_key,
        project_alias=alias,
    )


def write_project_flow(
    team_dir: Path | str,
    *,
    project_root: Any = "",
    manager_state: Any = None,
    entry: Any = None,
    project_key: str = "",
    project_alias: str = "",
    compiled_at: str = "",
) -> Dict[str, Any]:
    backend = artifact_backend(team_dir)
    payload = build_project_flow(
        team_dir,
        project_root=project_root,
        manager_state=manager_state,
        entry=entry,
        project_key=project_key,
        project_alias=project_alias,
        compiled_at=compiled_at,
    )
    path = backend.project_flow_path(project_alias=payload.get("project_alias", "O1"))
    payload["artifact_path"] = backend.relative_artifact_path(path)
    backend.write_project_flow(project_alias=payload.get("project_alias", "O1"), payload=payload)
    return payload
