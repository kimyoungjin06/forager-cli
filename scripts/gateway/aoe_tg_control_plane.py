#!/usr/bin/env python3
"""Control plane exec and planning helpers extracted from the gateway monolith."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def run_codex_exec(
    args: Any,
    prompt: str,
    *,
    timeout_sec: int,
    run_command: Callable[[List[str], Optional[Dict[str, str]], int], Any],
    subprocess_run: Callable[..., Any],
) -> str:
    fd, out_path_raw = tempfile.mkstemp(prefix="aoe_tg_", suffix=".txt")
    os.close(fd)
    out_path = Path(out_path_raw)

    perm_mode = (os.environ.get("AOE_CODEX_PERMISSION_MODE", "full") or "full").strip().lower()
    run_as_root_raw = (os.environ.get("AOE_CODEX_RUN_AS_ROOT", "0") or "0").strip().lower()
    run_as_root = run_as_root_raw in {"1", "true", "yes", "on"}

    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--disable",
        "multi_agent",
        "-C",
        str(args.project_root),
        "-o",
        str(out_path),
        prompt,
    ]

    if perm_mode in {"full", "unsafe", "bypass", "dangerous"}:
        cmd.extend(["--dangerously-bypass-approvals-and-sandbox"])
    elif perm_mode in {"danger", "danger-full-access"}:
        cmd.extend(["--sandbox", "danger-full-access"])
    elif perm_mode in {"workspace", "workspace-write", "safe", ""}:
        cmd.extend(["--sandbox", "workspace-write"])
    elif perm_mode in {"read-only", "readonly"}:
        cmd.extend(["--sandbox", "read-only"])
    else:
        cmd.extend(["--sandbox", "workspace-write"])

    root_output_mode = False
    if run_as_root:
        can_sudo = subprocess_run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if can_sudo:
            env_pairs: List[str] = []
            for k in [
                "HOME",
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT_ID",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "ALL_PROXY",
            ]:
                v = os.environ.get(k, "")
                if v:
                    env_pairs.append(f"{k}={v}")
            cmd = ["sudo", "-n", "env", *env_pairs, *cmd]
            root_output_mode = True

    try:
        if root_output_mode:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
        proc = run_command(cmd, env=None, timeout_sec=timeout_sec)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"codex exec failed: {detail[:1000]}")

        body = ""
        if out_path.exists():
            try:
                body = out_path.read_text(encoding="utf-8").strip()
            except Exception:
                body = ""

        if not body:
            body = (proc.stdout or "").strip()

        if not body:
            raise RuntimeError("codex exec returned empty output")

        return body
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass


def run_claude_exec(args: Any, prompt: str, *, timeout_sec: int, subprocess_run: Callable[..., Any]) -> str:
    perm_mode = (
        os.environ.get("AOE_CLAUDE_PERMISSION_MODE", os.environ.get("AOE_CODEX_PERMISSION_MODE", "full")) or "full"
    ).strip().lower()
    run_as_root_raw = (
        os.environ.get("AOE_CLAUDE_RUN_AS_ROOT", os.environ.get("AOE_CODEX_RUN_AS_ROOT", "0")) or "0"
    ).strip().lower()
    run_as_root = run_as_root_raw in {"1", "true", "yes", "on"}

    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "text",
        "--add-dir",
        str(args.project_root),
        "--no-session-persistence",
    ]

    if perm_mode in {"full", "unsafe", "bypass", "dangerous", "danger", "danger-full-access"}:
        cmd.extend(["--dangerously-skip-permissions", "--permission-mode", "bypassPermissions"])
    elif perm_mode in {"workspace", "workspace-write", "safe", ""}:
        cmd.extend(["--permission-mode", "acceptEdits"])
    elif perm_mode in {"read-only", "readonly"}:
        cmd.extend(["--permission-mode", "plan"])
    elif perm_mode in {"auto", "default", "dontask", "dont-ask", "acceptedits", "bypasspermissions", "plan"}:
        mode_map = {
            "dontask": "dontAsk",
            "dont-ask": "dontAsk",
            "acceptedits": "acceptEdits",
            "bypasspermissions": "bypassPermissions",
        }
        cmd.extend(["--permission-mode", mode_map.get(perm_mode, perm_mode)])
    else:
        cmd.extend(["--dangerously-skip-permissions", "--permission-mode", "bypassPermissions"])

    if run_as_root:
        can_sudo = subprocess_run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0
        if can_sudo:
            env_pairs: List[str] = []
            for k in [
                "HOME",
                "PATH",
                "ANTHROPIC_API_KEY",
                "ANTHROPIC_BASE_URL",
                "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_OAUTH_TOKEN",
                "CLAUDE_CONFIG_DIR",
                "AWS_REGION",
                "AWS_DEFAULT_REGION",
                "AWS_PROFILE",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "ALL_PROXY",
            ]:
                v = os.environ.get(k, "")
                if v:
                    env_pairs.append(f"{k}={v}")
            cmd = ["sudo", "-n", "env", *env_pairs, *cmd]

    proc = subprocess_run(
        cmd,
        text=True,
        capture_output=True,
        cwd=str(args.project_root),
        timeout=max(5, int(timeout_sec)),
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"claude exec failed: {detail[:1000]}")
    body = (proc.stdout or "").strip()
    if not body:
        raise RuntimeError("claude exec returned empty output")
    return body


def configured_control_providers(args: Any) -> List[str]:
    raw = (
        str(getattr(args, "control_providers", "") or "").strip()
        or os.environ.get("AOE_CONTROL_PROVIDERS", "").strip()
        or str(getattr(args, "plan_phase1_providers", "") or "").strip()
        or os.environ.get("AOE_PLAN_PHASE1_PROVIDERS", "").strip()
        or "codex,claude"
    )
    providers: List[str] = []
    for token in raw.split(","):
        item = str(token or "").strip().lower()
        if item and item not in providers:
            providers.append(item)
    return providers or ["codex", "claude"]


def available_control_provider_execs(
    args: Any,
    *,
    configured_control_providers_fn: Callable[[Any], List[str]],
    run_codex_exec_fn: Callable[[Any, str, int], str],
    run_claude_exec_fn: Callable[[Any, str, int], str],
    which: Callable[[str], Optional[str]],
) -> tuple[List[str], Dict[str, Callable[[str, int], str]], List[str], List[str]]:
    requested = configured_control_providers_fn(args)
    runner_catalog: Dict[str, tuple[str, Callable[[str, int], str]]] = {
        "codex": ("codex", lambda prompt, timeout_sec: run_codex_exec_fn(args, prompt, timeout_sec)),
        "claude": ("claude", lambda prompt, timeout_sec: run_claude_exec_fn(args, prompt, timeout_sec)),
    }

    available_execs: Dict[str, Callable[[str, int], str]] = {}
    unsupported: List[str] = []
    missing: List[str] = []
    for name in requested:
        catalog_row = runner_catalog.get(name)
        if catalog_row is None:
            unsupported.append(name)
            continue
        binary, runner = catalog_row
        if which(binary):
            available_execs[name] = runner
        else:
            missing.append(binary)
    return requested, available_execs, unsupported, missing


def run_control_plane_exec(
    args: Any,
    prompt: str,
    *,
    timeout_sec: int,
    stage: str,
    available_control_provider_execs_fn: Callable[[Any], tuple[List[str], Dict[str, Callable[[str, int], str]], List[str], List[str]]],
    load_provider_capacity_state_fn: Callable[[Any], Dict[str, Any]],
    proactive_fallback_provider_fn: Callable[..., Optional[str]],
    fallback_provider_for_fn: Callable[[str], Optional[str]],
    is_rate_limit_error_fn: Callable[[str], bool],
) -> str:
    requested, available_execs, unsupported, missing = available_control_provider_execs_fn(args)
    if not available_execs:
        detail_parts: List[str] = []
        if unsupported:
            detail_parts.append(f"unsupported={','.join(unsupported)}")
        if missing:
            detail_parts.append(f"missing={','.join(missing)}")
        detail = " ".join(detail_parts) if detail_parts else "no available providers"
        raise RuntimeError(f"control plane exec unavailable for stage={stage}: {detail}")

    memory_state = load_provider_capacity_state_fn(getattr(args, "team_dir", ""))
    attempted: List[str] = []
    errors: List[str] = []

    ordered: List[str] = []
    for provider in requested:
        if provider in available_execs and provider not in ordered:
            preferred = proactive_fallback_provider_fn(
                provider,
                memory_state=memory_state,
                available_providers=available_execs.keys(),
            ) or provider
            if preferred in available_execs and preferred not in ordered:
                ordered.append(preferred)
            if preferred == provider and provider not in ordered:
                ordered.append(provider)

    for provider in available_execs:
        if provider not in ordered:
            ordered.append(provider)

    for provider in ordered:
        if provider in attempted:
            continue
        attempted.append(provider)
        runner = available_execs.get(provider)
        if not callable(runner):
            continue
        try:
            return runner(prompt, timeout_sec)
        except Exception as exc:
            detail = str(exc or "").strip()
            errors.append(f"{provider}:{detail[:240]}")
            if is_rate_limit_error_fn(detail):
                fallback = fallback_provider_for_fn(provider)
                if fallback and fallback in available_execs and fallback not in attempted:
                    attempted.append(fallback)
                    try:
                        return available_execs[fallback](prompt, timeout_sec)
                    except Exception as fb_exc:
                        errors.append(f"{fallback}:{str(fb_exc or '').strip()[:240]}")
            continue

    raise RuntimeError(
        "control plane exec failed stage={stage} providers={providers} attempted={attempted} errors={errors}".format(
            stage=str(stage or "").strip() or "control",
            providers=",".join(requested),
            attempted=",".join(attempted) or "none",
            errors=" | ".join(errors[-4:]) or "unknown",
        )
    )


def parse_json_object_from_text(text: str) -> Optional[Dict[str, Any]]:
    src = (text or "").strip()
    if not src:
        return None

    try:
        obj = json.loads(src)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(src):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(src[i:])
        except Exception:
            continue
        if isinstance(obj, dict):
            return obj

    return None


def planning_stage_timeout_sec(args: Any, stage: str) -> int:
    stage_token = str(stage or "").strip().lower()
    env_map = {
        "planner": "AOE_PLAN_PLANNER_TIMEOUT_SEC",
        "critic": "AOE_PLAN_CRITIC_TIMEOUT_SEC",
        "repair": "AOE_PLAN_REPAIR_TIMEOUT_SEC",
    }
    default_caps = {"planner": 240, "critic": 180, "repair": 240}
    min_floors = {"planner": 60, "critic": 45, "repair": 60}
    try:
        base = int(getattr(args, "orch_command_timeout_sec", 900) or 900)
    except Exception:
        base = 900
    cap = int(default_caps.get(stage_token, 180))
    floor = int(min_floors.get(stage_token, 60))

    raw_override = os.environ.get(env_map.get(stage_token, ""), "").strip()
    if raw_override:
        try:
            override = int(raw_override)
            return max(floor, min(override, max(base, floor)))
        except Exception:
            pass

    return max(floor, min(cap, max(base, floor)))


def build_task_execution_plan(
    args: Any,
    user_prompt: str,
    available_roles: List[str],
    max_subtasks: int,
    *,
    available_worker_roles_fn: Callable[[List[str]], List[str]],
    run_control_plane_exec_fn: Callable[..., str],
    planning_stage_timeout_sec_fn: Callable[[Any, str], int],
    parse_json_object_from_text_fn: Callable[[str], Optional[Dict[str, Any]]],
    normalize_task_plan_payload_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    workers = available_worker_roles_fn(available_roles)
    planner_prompt = (
        "너는 작업 오케스트레이션 planner다. 사용자 요청을 실행 가능한 sub-task 계획으로 분해해라.\n"
        "반드시 JSON 객체만 출력한다. 설명 문장 금지.\n"
        "JSON 스키마:\n"
        "{\n"
        "  \"summary\": \"한 줄 요약\",\n"
        "  \"subtasks\": [\n"
        "    {\"id\":\"S1\", \"title\":\"...\", \"goal\":\"...\", \"owner_role\":\"ROLE\", \"acceptance\":[\"...\"]}\n"
        "  ]\n"
        "}\n"
        "제약:\n"
        f"- owner_role은 다음 중 하나만 사용: {', '.join(workers)}\n"
        f"- subtasks는 1~{max(1, int(max_subtasks))}개\n"
        "- 각 subtask는 서로 다른 산출물을 갖도록 분해\n"
        "- acceptance는 검증 가능한 문장 1~3개\n"
        "- approval_mode는 기본적으로 policy다. 최종 승인/복귀 결정은 Control Plane operator가 맡고, Task Team 내부 역할에 가짜 DRI/최종 승인자를 만들지 마라\n"
        "- 사람 승인 필요는 acceptance/evidence/manual follow-up 성격으로 남겨라\n\n"
        f"사용자 요청:\n{user_prompt.strip()}\n"
    )
    raw = run_control_plane_exec_fn(
        args,
        planner_prompt,
        timeout_sec=planning_stage_timeout_sec_fn(args, "planner"),
        stage="planner",
    )
    parsed = parse_json_object_from_text_fn(raw)
    return normalize_task_plan_payload_fn(parsed, user_prompt=user_prompt, workers=workers, max_subtasks=max_subtasks)


def critique_task_execution_plan(
    args: Any,
    user_prompt: str,
    plan: Dict[str, Any],
    *,
    run_control_plane_exec_fn: Callable[..., str],
    planning_stage_timeout_sec_fn: Callable[[Any, str], int],
    parse_json_object_from_text_fn: Callable[[str], Optional[Dict[str, Any]]],
    normalize_plan_critic_payload_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    payload = json.dumps(plan, ensure_ascii=False)
    critic_prompt = (
        "너는 task plan critic이다. 아래 계획의 누락/과도분해/검증불가 항목을 점검해라.\n"
        "반드시 JSON 객체만 출력한다. 설명 문장 금지.\n"
        "JSON 스키마:\n"
        "{\n"
        "  \"approved\": true|false,\n"
        "  \"issues\": [\"...\"],\n"
        "  \"recommendations\": [\"...\"]\n"
        "}\n"
        "규칙:\n"
        "- issues는 치명/중요 문제만\n"
        "- recommendations는 실행 가능한 수정 제안만\n"
        "- operator approval/recovery는 Task Team 바깥의 Control Plane 책임이다\n"
        "- reviewer/critic role이 있다는 이유만으로 human approver/DRI 부재를 blocker로 만들지 마라\n"
        "- approval 필요성은 acceptance/evidence/manual follow-up으로 남겨라\n\n"
        f"사용자 요청:\n{user_prompt.strip()}\n\n"
        f"plan:\n{payload}\n"
    )
    try:
        raw = run_control_plane_exec_fn(
            args,
            critic_prompt,
            timeout_sec=planning_stage_timeout_sec_fn(args, "critic"),
            stage="critic",
        )
        parsed = parse_json_object_from_text_fn(raw)
    except Exception:
        parsed = None
    return normalize_plan_critic_payload_fn(parsed, max_items=5)


def repair_task_execution_plan(
    args: Any,
    user_prompt: str,
    current_plan: Dict[str, Any],
    critic: Dict[str, Any],
    available_roles: List[str],
    max_subtasks: int,
    attempt_no: int,
    *,
    available_worker_roles_fn: Callable[[List[str]], List[str]],
    run_control_plane_exec_fn: Callable[..., str],
    planning_stage_timeout_sec_fn: Callable[[Any, str], int],
    parse_json_object_from_text_fn: Callable[[str], Optional[Dict[str, Any]]],
    normalize_task_plan_payload_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    workers = available_worker_roles_fn(available_roles)
    current_payload = json.dumps(current_plan, ensure_ascii=False)
    critic_payload = json.dumps(critic, ensure_ascii=False)
    repair_prompt = (
        "너는 task planner다. critic 이슈를 반영해 계획을 고쳐라.\n"
        "반드시 JSON 객체만 출력한다. 설명 문장 금지.\n"
        "JSON 스키마:\n"
        "{\n"
        "  \"summary\": \"한 줄 요약\",\n"
        "  \"subtasks\": [\n"
        "    {\"id\":\"S1\", \"title\":\"...\", \"goal\":\"...\", \"owner_role\":\"ROLE\", \"acceptance\":[\"...\"]}\n"
        "  ]\n"
        "}\n"
        "제약:\n"
        f"- owner_role은 다음 중 하나만 사용: {', '.join(workers)}\n"
        f"- subtasks는 1~{max(1, int(max_subtasks))}개\n"
        "- acceptance는 검증 가능한 문장 1~3개\n"
        "- critic issues를 가능한 한 모두 해소\n"
        "- 최종 승인/복귀 판단은 Control Plane operator가 맡는다. Task Team 내부에 가짜 approver/DRI role을 만들지 마라\n"
        "- 사람 승인 필요는 manual follow-up 또는 evidence 항목으로 남겨라\n\n"
        f"attempt: {int(attempt_no)}\n"
        f"사용자 요청:\n{user_prompt.strip()}\n\n"
        f"current_plan:\n{current_payload}\n\n"
        f"critic:\n{critic_payload}\n"
    )
    raw = run_control_plane_exec_fn(
        args,
        repair_prompt,
        timeout_sec=planning_stage_timeout_sec_fn(args, "repair"),
        stage="repair",
    )
    parsed = parse_json_object_from_text_fn(raw)
    return normalize_task_plan_payload_fn(parsed, user_prompt=user_prompt, workers=workers, max_subtasks=max_subtasks)


def run_phase1_ensemble_planning(
    args: Any,
    user_prompt: str,
    available_roles: List[str],
    *,
    selected_roles: Optional[List[str]],
    role_preset: str,
    report_progress: Optional[Callable[..., None]],
    run_codex_exec_fn: Callable[[Any, str, int], str],
    run_claude_exec_fn: Callable[[Any, str, int], str],
    parse_json_object_from_text_fn: Callable[[str], Optional[Dict[str, Any]]],
    normalize_task_plan_payload_fn: Callable[..., Dict[str, Any]],
    plan_roles_from_subtasks_fn: Callable[[Dict[str, Any]], List[str]],
    default_plan_critic_payload_fn: Callable[[], Dict[str, Any]],
    run_phase1_ensemble_planning_fn: Callable[..., Dict[str, Any]],
    which: Callable[[str], Optional[str]],
) -> Dict[str, Any]:
    providers_csv = str(getattr(args, "plan_phase1_providers", "codex,claude") or "codex,claude")
    requested: List[str] = []
    for token in providers_csv.split(","):
        item = str(token or "").strip().lower()
        if item and item not in requested:
            requested.append(item)
    if not requested:
        requested = ["codex", "claude"]

    runner_catalog: Dict[str, tuple[str, Callable[[str, int], str]]] = {
        "codex": ("codex", lambda prompt, timeout_sec: run_codex_exec_fn(args, prompt, timeout_sec)),
        "claude": ("claude", lambda prompt, timeout_sec: run_claude_exec_fn(args, prompt, timeout_sec)),
    }
    unsupported = [name for name in requested if name not in runner_catalog]
    if unsupported:
        detail = f"unsupported phase1 providers: {', '.join(unsupported)}"
        return {
            "plan_data": None,
            "plan_critic": default_plan_critic_payload_fn(),
            "plan_roles": [],
            "plan_replans": [],
            "plan_error": detail,
            "plan_gate_blocked": True,
            "plan_gate_reason": detail,
            "phase1_rounds": 0,
            "phase1_mode": "ensemble",
            "phase1_providers": requested,
        }

    available_execs: Dict[str, Callable[[str, int], str]] = {}
    missing_binaries: List[str] = []
    for name in requested:
        binary, runner = runner_catalog[name]
        if which(binary):
            available_execs[name] = runner
        else:
            missing_binaries.append(binary)

    min_providers = max(1, int(getattr(args, "plan_phase1_min_providers", 2) or 2))
    if len(available_execs) < min_providers:
        detail = (
            f"phase1 ensemble requires at least {min_providers} providers; "
            f"available={','.join(sorted(available_execs)) or 'none'} "
            f"missing={','.join(missing_binaries) or 'none'}"
        )
        return {
            "plan_data": None,
            "plan_critic": default_plan_critic_payload_fn(),
            "plan_roles": [],
            "plan_replans": [],
            "plan_error": detail,
            "plan_gate_blocked": True,
            "plan_gate_reason": detail,
            "phase1_rounds": 0,
            "phase1_mode": "ensemble",
            "phase1_providers": list(available_execs),
        }

    return run_phase1_ensemble_planning_fn(
        args=args,
        user_prompt=user_prompt,
        available_roles=available_roles,
        selected_roles=selected_roles,
        role_preset=role_preset,
        normalize_task_plan_payload=normalize_task_plan_payload_fn,
        parse_json_object_from_text=parse_json_object_from_text_fn,
        run_provider_execs=available_execs,
        plan_roles_from_subtasks=plan_roles_from_subtasks_fn,
        report_progress=report_progress,
    )
