import argparse
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def run_aoe_init(
    args: argparse.Namespace,
    project_root: Path,
    team_dir: Path,
    overview: str,
    *,
    run_command: Callable[..., Any],
    repair_runtime: Callable[..., List[str]],
    templates_root: Callable[[], Path],
) -> str:
    cfg = team_dir / "orchestrator.json"
    if cfg.exists():
        return "[SKIP] already initialized (.aoe-team/orchestrator.json exists)"

    cmd = [
        args.aoe_orch_bin,
        "init",
        "--project-root",
        str(project_root),
        "--team-dir",
        str(team_dir),
        "--overview",
        overview,
    ]
    proc = run_command(cmd, env=None, timeout_sec=max(60, int(args.orch_command_timeout_sec)))
    text = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        low = text.lower()
        if "file exists" in low and "agents.md" in low:
            logs = repair_runtime(
                aoe_orch_bin=args.aoe_orch_bin,
                template_root=templates_root(),
                project_root=project_root,
                team_dir=team_dir,
                overview=overview,
                timeout_sec=max(60, int(args.orch_command_timeout_sec)),
                force=False,
            )
            return "\n".join(["[FALLBACK] runtime seeded without touching project-root AGENTS.md", *logs])
        raise RuntimeError(f"aoe-orch init failed: {text[:1200]}")
    return text or "[OK] initialized"


def run_aoe_spawn(
    args: argparse.Namespace,
    project_root: Path,
    team_dir: Path,
    *,
    run_command: Callable[..., Any],
) -> str:
    cmd = [
        args.aoe_orch_bin,
        "spawn",
        "--project-root",
        str(project_root),
        "--team-dir",
        str(team_dir),
    ]
    proc = run_command(cmd, env=None, timeout_sec=max(60, int(args.orch_command_timeout_sec)))
    text = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"aoe-orch spawn failed: {text[:1200]}")
    return text or "[OK] spawned"


def summarize_three_stage_request(
    project_name: str,
    request_data: Dict[str, Any],
    task: Optional[Dict[str, Any]] = None,
    *,
    task_display_label: Callable[..., str],
) -> str:
    request_id = str(request_data.get("request_id", "-")).strip() or "-"
    counts = request_data.get("counts") or {}
    assignments = int(counts.get("assignments", 0) or 0)
    replies = int(counts.get("replies", 0) or 0)
    complete = bool(request_data.get("complete", False))

    roles = request_data.get("roles") or []
    running: List[str] = []
    failed: List[str] = []
    done: List[str] = []

    for row in roles:
        role = str(row.get("role", "?")).strip() or "?"
        status = str(row.get("status", "?")).strip().lower()
        item = f"{role}({status})"
        if status in {"done"}:
            done.append(item)
        elif status in {"failed", "error", "fail"}:
            failed.append(item)
        else:
            running.append(item)

    stage1 = "완료" if assignments > 0 else "대기"
    if failed:
        stage2 = "이슈"
    elif running:
        stage2 = "진행중"
    elif assignments > 0:
        stage2 = "완료"
    else:
        stage2 = "대기"

    if complete and not failed:
        stage3 = "완료"
    elif replies > 0:
        stage3 = "부분완료"
    else:
        stage3 = "대기"

    lines = [
        f"runtime: {project_name}",
        f"task: {task_display_label(task or {}, fallback_request_id=request_id)}",
        f"request_id: {request_id}",
        "3단계 진행확인",
        f"1) 접수/배정: {stage1} (assignments={assignments})",
        f"2) 실행: {stage2}" + (f" | running={', '.join(running)}" if running else ""),
        f"3) 완료/회신: {stage3} (replies={replies}, complete={'yes' if complete else 'no'})",
    ]

    if done:
        lines.append("done: " + ", ".join(done))
    if failed:
        lines.append("failed: " + ", ".join(failed))

    unresolved = request_data.get("unresolved_roles") or []
    if unresolved:
        lines.append("unresolved: " + ", ".join(str(x) for x in unresolved))

    return "\n".join(lines)


def run_aoe_orch(
    args: argparse.Namespace,
    prompt: str,
    chat_id: str,
    roles_override: Optional[str] = None,
    priority_override: Optional[str] = None,
    timeout_override: Optional[int] = None,
    no_wait_override: Optional[bool] = None,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    path_cls: Callable[[str], Path],
    resolve_effective_tf_backend: Callable[[Path], Dict[str, Any]],
    normalize_tf_backend_name: Callable[..., str],
    default_tf_backend: str,
    autogen_core_tf_backend: str,
    autogen_core_backend: Callable[[], Any],
    local_backend: Callable[[], Any],
    availability_tuple: Callable[[Any], Any],
    build_tf_backend_request: Callable[..., Dict[str, Any]],
    build_tf_backend_deps: Callable[..., Dict[str, Any]],
    default_tf_exec_mode: str,
    default_tf_work_root_name: str,
    default_tf_exec_map_file: str,
    default_tf_worker_startup_grace_sec: int,
    now_iso: Callable[[], str],
    run_command: Callable[..., Any],
    mirror_tf_backend_runtime_events: Callable[..., None],
) -> Dict[str, Any]:
    selection = resolve_effective_tf_backend(path_cls(str(args.team_dir)))
    backend_name = normalize_tf_backend_name(selection.get("effective_backend"), default=default_tf_backend)
    adapter = autogen_core_backend() if backend_name == autogen_core_tf_backend else local_backend()
    available, availability_reason = availability_tuple(adapter.availability())
    if not available:
        config_path = str(selection.get("config_path", "") or "").strip()
        config_hint = f" config={config_path}" if config_path else ""
        raise RuntimeError(
            f"tf backend unavailable: backend={backend_name}"
            f" reason={availability_reason or 'unavailable'}"
            f" selection={selection.get('selection_reason', 'default_local')}{config_hint}"
        )

    request_metadata = {
        "backend": backend_name,
        "selection_reason": str(selection.get("selection_reason", "") or ""),
        "profile": str(selection.get("profile", "") or ""),
        "sandbox_only": bool(selection.get("sandbox_only", True)),
        "config_path": str(selection.get("config_path", "") or ""),
    }
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue
            request_metadata[str(key).strip()] = value

    request = build_tf_backend_request(
        args=args,
        prompt=prompt,
        chat_id=chat_id,
        roles_override=roles_override,
        priority_override=priority_override,
        timeout_override=timeout_override,
        no_wait_override=no_wait_override,
        metadata=request_metadata,
    )
    deps = build_tf_backend_deps(
        default_tf_exec_mode=default_tf_exec_mode,
        default_tf_work_root_name=default_tf_work_root_name,
        default_tf_exec_map_file=default_tf_exec_map_file,
        default_tf_worker_startup_grace_sec=default_tf_worker_startup_grace_sec,
        now_iso=now_iso,
        run_command=run_command,
    )
    result = adapter.run(request, deps)
    if not isinstance(result, dict):
        result = {"result": result}
    result = dict(result)
    result["backend"] = backend_name
    result["backend_profile"] = str(selection.get("profile", "") or "")
    result["backend_selection_reason"] = str(selection.get("selection_reason", "") or "")
    result["backend_config_path"] = str(selection.get("config_path", "") or "")
    result["backend_availability_reason"] = str(availability_reason or "")

    runtime_events = result.get("runtime_events")
    if not isinstance(runtime_events, list):
        runtime_events = result.get("events")
    if isinstance(runtime_events, list) and runtime_events:
        mirror_tf_backend_runtime_events(
            team_dir=path_cls(str(args.team_dir)),
            backend=backend_name,
            runtime_events=runtime_events,
            trace_id=str(getattr(args, "_aoe_trace_id", "") or ""),
            project=str(getattr(args, "_aoe_project_key", "") or ""),
            request_id=str(result.get("request_id", "") or ""),
            task=result.get("task") if isinstance(result.get("task"), dict) else None,
            mirror_team_dir=path_cls(str(getattr(args, "_aoe_root_team_dir", args.team_dir))),
        )
    return result


def run_aoe_add_role(
    args: argparse.Namespace,
    role: str,
    provider: Optional[str],
    launch: Optional[str],
    spawn: bool,
    *,
    run_command: Callable[..., Any],
) -> str:
    cmd: List[str] = [
        args.aoe_orch_bin,
        "add-role",
        "--project-root",
        str(args.project_root),
        "--team-dir",
        str(args.team_dir),
        "--role",
        role,
        "--json",
    ]

    if provider:
        cmd.extend(["--provider", provider])
    if launch:
        cmd.extend(["--launch", launch])
    if spawn:
        cmd.append("--spawn")
    else:
        cmd.append("--no-spawn")

    proc = run_command(cmd, env=None, timeout_sec=60)
    payload = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"aoe-orch add-role failed: {payload[:1200]}")

    try:
        data = json.loads(payload)
    except Exception:
        return payload or f"[OK] role added: {role}"

    if not isinstance(data, dict):
        return payload or f"[OK] role added: {role}"

    resolved_role = str(data.get("role", role))
    sess = str(data.get("session", ""))
    prov = str(data.get("provider", provider or "codex"))
    launch_used = str(data.get("launch", launch or ""))
    exists = bool(data.get("exists", False))
    updated = bool(data.get("updated", False))

    lines = [f"role ready: {resolved_role}", f"provider: {prov}"]
    if launch_used:
        lines.append(f"launch: {launch_used}")
    if sess:
        lines.append(f"session: {sess}")
    lines.append(f"exists_before: {'yes' if exists else 'no'}")
    lines.append(f"updated: {'yes' if updated else 'no'}")

    spawn_info = data.get("spawn_info") or {}
    spawned = spawn_info.get("spawned") or []
    existing_rows = spawn_info.get("existing") or []
    failed = spawn_info.get("failed") or []
    if spawned:
        lines.append(f"spawned: {len(spawned)}")
    if existing_rows:
        lines.append(f"already_running: {len(existing_rows)}")
    if failed:
        lines.append(f"spawn_failed: {len(failed)}")

    return "\n".join(lines)
