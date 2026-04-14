#!/usr/bin/env python3
"""Provider-side invocation helpers for modular model routes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from typing import Any, Dict

import aoe_tg_model_endpoint_adapter as endpoint_adapter
import aoe_tg_worker_task_contract as worker_task_contract


def _trim(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:limit]


def _coerce_timeout_sec(value: Any, default: float = 30.0) -> float:
    try:
        parsed = float(value if value is not None else default)
    except Exception:
        parsed = float(default)
    return max(0.1, parsed)


def _default_post_json(url: str, payload: Dict[str, Any], *, timeout_sec: float = 30.0) -> Dict[str, Any]:
    return _default_post_json_with_headers(url, payload, headers={}, timeout_sec=timeout_sec)


def _default_post_json_with_headers(
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Dict[str, str] | None = None,
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=max(0.1, float(timeout_sec or 30.0))) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return raw if isinstance(raw, dict) else {}


def _default_api_key_env(provider_kind: str, explicit: Any) -> str:
    token = _trim(explicit, 128)
    if token:
        return token
    if provider_kind == "anthropic":
        return "ANTHROPIC_API_KEY"
    if provider_kind == "openai":
        return "OPENAI_API_KEY"
    return ""


def _resolve_api_key(provider_kind: str, endpoint: Dict[str, Any]) -> tuple[str, str]:
    env_name = _default_api_key_env(provider_kind, endpoint.get("api_key_env"))
    if not env_name:
        return "", ""
    return env_name, _trim(os.environ.get(env_name), 800)


def _extract_openai_output_text(response: Dict[str, Any]) -> str:
    direct = _trim(response.get("output_text"), 8000)
    if direct:
        return direct
    output = response.get("output") if isinstance(response.get("output"), list) else []
    parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), list) else []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = _trim(block.get("text"), 8000)
            if text:
                parts.append(text)
    return "\n".join(part for part in parts if part)


def _extract_openai_compatible_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices") if isinstance(response.get("choices"), list) else []
    if not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return _trim(content, 8000)
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = _trim(block.get("text"), 8000)
            if text:
                parts.append(text)
        return "\n".join(part for part in parts if part)
    return ""


def _extract_anthropic_text(response: Dict[str, Any]) -> str:
    content = response.get("content") if isinstance(response.get("content"), list) else []
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if _trim(block.get("type"), 32).lower() not in {"text", ""}:
            continue
        text = _trim(block.get("text"), 8000)
        if text:
            parts.append(text)
    return "\n".join(part for part in parts if part)


def _run_subprocess(argv: list[str], *, timeout_sec: float = 30.0, cwd: str = "") -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout_sec or 30.0)),
            cwd=cwd or None,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": _trim(exc, 8000)}
    return {
        "ok": completed.returncode == 0,
        "exit_code": int(completed.returncode),
        "stdout": _trim(completed.stdout, 8000),
        "stderr": _trim(completed.stderr, 8000),
    }


def _claude_cli_model_alias(model: str) -> str:
    token = _trim(model, 128).lower()
    if token.startswith("claude-opus") or token == "opus":
        return "opus"
    if token.startswith("claude-sonnet") or token == "sonnet":
        return "sonnet"
    if token.startswith("claude-haiku") or token == "haiku":
        return "haiku"
    return _trim(model, 128)


def _invoke_cli_binding(
    provider_kind: str,
    endpoint: Dict[str, Any],
    *,
    prompt_text: str,
    system_text: str,
    timeout_sec: float,
    cwd: str = "",
    run_subprocess: Any = None,
) -> Dict[str, Any]:
    endpoint_id = _trim(endpoint.get("endpoint_id"), 64).lower()
    model = _trim(endpoint.get("model"), 128)
    runner = run_subprocess if callable(run_subprocess) else _run_subprocess
    if provider_kind == "claude_code_cli":
        binary = shutil.which("claude")
        if not binary:
            return {
                "ok": False,
                "executed": False,
                "endpoint_id": endpoint_id,
                "provider_kind": provider_kind,
                "model": model,
                "reason_code": "missing_cli_binary",
                "summary": f"endpoint={endpoint_id or '-'} provider=claude_code_cli status=missing_cli_binary",
            }
        argv = [binary, "-p", "--model", _claude_cli_model_alias(model), "--tools", ""]
        if system_text:
            argv.extend(["--system-prompt", system_text])
        argv.append(prompt_text)
        proc = runner(argv, timeout_sec=timeout_sec, cwd=cwd)
        merged = " ".join(part for part in (proc.get("stdout", ""), proc.get("stderr", "")) if part).lower()
        if not proc.get("ok"):
            reason_code = "provider_request_failed"
            if "not logged" in merged or "login" in merged or "auth" in merged:
                reason_code = "not_logged_in"
            elif "may not exist or you may not have access" in merged:
                reason_code = "model_unavailable"
            return {
                "ok": False,
                "executed": False,
                "endpoint_id": endpoint_id,
                "provider_kind": provider_kind,
                "model": model,
                "reason_code": reason_code,
                "summary": f"endpoint={endpoint_id or '-'} provider=claude_code_cli status={reason_code}",
                "stdout": _trim(proc.get("stdout"), 8000),
                "stderr": _trim(proc.get("stderr"), 8000),
            }
        text = _trim(proc.get("stdout"), 8000)
        return {
            "ok": bool(text),
            "executed": True,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "model": model,
            "response_text": text,
            "summary": f"endpoint={endpoint_id or '-'} provider=claude_code_cli model={model or '-'} status=completed",
        }
    binary = shutil.which("codex")
    if not binary:
        return {
            "ok": False,
            "executed": False,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "model": model,
            "reason_code": "missing_cli_binary",
            "summary": f"endpoint={endpoint_id or '-'} provider=codex_cli status=missing_cli_binary",
        }
    combined_prompt = prompt_text if not system_text else f"{system_text}\n\n{prompt_text}"
    with tempfile.NamedTemporaryFile("w+", delete=False, encoding="utf-8") as tmp:
        output_path = tmp.name
    try:
        argv = [
            binary,
            "-c",
            'model_reasoning_effort="high"',
            "-a",
            "never",
            "-s",
            "read-only",
            "exec",
            "-C",
            cwd or os.getcwd(),
            "--skip-git-repo-check",
            "-o",
            output_path,
        ]
        if model:
            argv.extend(["--model", model])
        argv.append(combined_prompt)
        proc = runner(argv, timeout_sec=timeout_sec, cwd=cwd)
        merged = " ".join(part for part in (proc.get("stdout", ""), proc.get("stderr", "")) if part).lower()
        if not proc.get("ok"):
            reason_code = "provider_request_failed"
            if "login" in merged and "logged in" not in merged:
                reason_code = "not_logged_in"
            return {
                "ok": False,
                "executed": False,
                "endpoint_id": endpoint_id,
                "provider_kind": provider_kind,
                "model": model,
                "reason_code": reason_code,
                "summary": f"endpoint={endpoint_id or '-'} provider=codex_cli status={reason_code}",
                "stdout": _trim(proc.get("stdout"), 8000),
                "stderr": _trim(proc.get("stderr"), 8000),
            }
        try:
            text = _trim(open(output_path, "r", encoding="utf-8").read(), 8000)
        except Exception:
            text = ""
        return {
            "ok": bool(text),
            "executed": True,
            "endpoint_id": endpoint_id,
            "provider_kind": provider_kind,
            "model": model,
            "response_text": text,
            "summary": f"endpoint={endpoint_id or '-'} provider=codex_cli model={model or '-'} status=completed",
        }
    finally:
        try:
            os.unlink(output_path)
        except Exception:
            pass


def _fallback_bindings(binding: Dict[str, Any]) -> list[Dict[str, Any]]:
    route = binding.get("route") if isinstance(binding.get("route"), dict) else {}
    registry = binding.get("registry") if isinstance(binding.get("registry"), dict) else {}
    rows = registry.get("endpoints") if isinstance(registry.get("endpoints"), list) else []
    index = {
        _trim(row.get("endpoint_id"), 64).lower(): row
        for row in rows
        if isinstance(row, dict) and _trim(row.get("endpoint_id"), 64)
    }
    out = [binding]
    for endpoint_id in route.get("fallback_ids") if isinstance(route.get("fallback_ids"), list) else []:
        token = _trim(endpoint_id, 64).lower()
        endpoint = index.get(token)
        if not endpoint:
            continue
        out.append(
            {
                **binding,
                "endpoint_id": token,
                "endpoint": endpoint,
                "bound": bool(endpoint.get("enabled")),
                "summary": f"{_trim(route.get('summary'), 240) or '-'} | fallback={token}",
            }
        )
    return out


def invoke_model_binding(
    binding: Any,
    *,
    prompt: Any,
    system: Any = "",
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding_data = binding if isinstance(binding, dict) else {}
    prompt_text = _trim(prompt, 8000)
    system_text = _trim(system, 4000)
    if not prompt_text:
        route = binding_data.get("route") if isinstance(binding_data.get("route"), dict) else {}
        route_id = _trim(route.get("route_id"), 64).lower() or "route"
        return {
            "ok": False,
            "executed": False,
            "route_id": route_id,
            "endpoint_id": _trim(binding_data.get("endpoint_id"), 64).lower(),
            "provider_kind": _trim(((binding_data.get("endpoint") or {}) if isinstance(binding_data.get("endpoint"), dict) else {}).get("provider_kind"), 64).lower() or "custom",
            "reason_code": "empty_prompt",
            "summary": f"route={route_id} endpoint={_trim(binding_data.get('endpoint_id'), 64) or '-'} reason=empty_prompt",
            "binding": binding_data,
        }
    fallback_reason_codes = {
        "model_route_unbound",
        "missing_api_key",
        "missing_cli_binary",
        "not_logged_in",
        "model_unavailable",
        "unsupported_provider_invoke",
        "missing_endpoint_metadata",
        "provider_request_failed",
    }
    primary_endpoint_id = _trim(binding_data.get("endpoint_id"), 64).lower()
    last_result: Dict[str, Any] | None = None
    for candidate in _fallback_bindings(binding_data):
        endpoint = candidate.get("endpoint") if isinstance(candidate.get("endpoint"), dict) else {}
        route = candidate.get("route") if isinstance(candidate.get("route"), dict) else {}
        route_id = _trim(route.get("route_id"), 64).lower() or "route"
        endpoint_id = _trim(endpoint.get("endpoint_id"), 64).lower()
        provider_kind = _trim(endpoint.get("provider_kind"), 64).lower() or "custom"
        model = _trim(endpoint.get("model"), 128)
        base_url = _trim(endpoint.get("base_url"), 240).rstrip("/")
        if not candidate.get("bound"):
            result = {
                "ok": False,
                "executed": False,
                "route_id": route_id,
                "endpoint_id": endpoint_id,
                "provider_kind": provider_kind,
                "reason_code": "model_route_unbound",
                "summary": _trim(candidate.get("summary"), 240) or f"{route_id}=unbound",
                "binding": candidate,
            }
        elif provider_kind in {"claude_code_cli", "codex_cli"}:
            cwd = _trim((((candidate.get("entry") or {}) if isinstance(candidate.get("entry"), dict) else {}).get("project_root")), 400) or os.getcwd()
            result = _invoke_cli_binding(
                provider_kind,
                endpoint,
                prompt_text=prompt_text,
                system_text=system_text,
                timeout_sec=timeout_sec,
                cwd=cwd,
                run_subprocess=post_json if callable(post_json) else None,
            )
            result.update({"route_id": route_id, "binding": candidate})
        elif provider_kind not in {"ollama", "openai", "openai_compatible", "anthropic"}:
            result = {
                "ok": False,
                "executed": False,
                "route_id": route_id,
                "endpoint_id": endpoint_id,
                "provider_kind": provider_kind,
                "reason_code": "unsupported_provider_invoke",
                "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind or '-'} status=unsupported_invoke",
                "binding": candidate,
            }
        else:
            default_base_url = {
                "openai": "https://api.openai.com",
                "anthropic": "https://api.anthropic.com",
            }.get(provider_kind, "")
            if not base_url:
                base_url = default_base_url
            if not base_url or not model:
                result = {
                    "ok": False,
                    "executed": False,
                    "route_id": route_id,
                    "endpoint_id": endpoint_id,
                    "provider_kind": provider_kind,
                    "reason_code": "missing_endpoint_metadata",
                    "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind or '-'} status=missing_metadata",
                    "binding": candidate,
                }
            else:
                api_key_env, api_key = _resolve_api_key(provider_kind, endpoint)
                if provider_kind in {"openai", "anthropic"} and not api_key:
                    result = {
                        "ok": False,
                        "executed": False,
                        "route_id": route_id,
                        "endpoint_id": endpoint_id,
                        "provider_kind": provider_kind,
                        "model": model,
                        "reason_code": "missing_api_key",
                        "summary": (
                            f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind} "
                            f"status=missing_api_key env={api_key_env or '-'}"
                        ),
                        "binding": candidate,
                    }
                else:
                    if provider_kind == "ollama":
                        url = f"{base_url}/api/generate"
                        payload: Dict[str, Any] = {"model": model, "prompt": prompt_text, "stream": False}
                        if system_text:
                            payload["system"] = system_text
                        headers: Dict[str, str] = {}
                    elif provider_kind == "openai":
                        url = f"{base_url}/v1/responses"
                        payload = {"model": model, "input": prompt_text}
                        if system_text:
                            payload["instructions"] = system_text
                        headers = {"Authorization": f"Bearer {api_key}"}
                    elif provider_kind == "openai_compatible":
                        url = f"{base_url}/v1/chat/completions"
                        messages = []
                        if system_text:
                            messages.append({"role": "system", "content": system_text})
                        messages.append({"role": "user", "content": prompt_text})
                        payload = {"model": model, "messages": messages, "stream": False}
                        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                    else:
                        url = f"{base_url}/v1/messages"
                        payload = {
                            "model": model,
                            "max_tokens": 256,
                            "messages": [{"role": "user", "content": prompt_text}],
                        }
                        if system_text:
                            payload["system"] = system_text
                        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
                    try:
                        if callable(post_json):
                            response = post_json(url, payload, timeout_sec=timeout_sec)
                        else:
                            response = _default_post_json_with_headers(url, payload, headers=headers, timeout_sec=timeout_sec)
                    except Exception as exc:
                        result = {
                            "ok": False,
                            "executed": False,
                            "route_id": route_id,
                            "endpoint_id": endpoint_id,
                            "provider_kind": provider_kind,
                            "model": model,
                            "reason_code": "provider_request_failed",
                            "error": _trim(exc, 240),
                            "summary": f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind or '-'} status=request_failed",
                            "binding": candidate,
                        }
                    else:
                        if provider_kind == "ollama":
                            text = _trim(response.get("response"), 8000)
                            done = bool(response.get("done"))
                            eval_count = int(response.get("eval_count", 0) or 0)
                            prompt_eval_count = int(response.get("prompt_eval_count", 0) or 0)
                            provider_suffix = f"done={'yes' if done else 'no'} prompt_eval={prompt_eval_count} eval={eval_count}"
                        elif provider_kind == "openai":
                            text = _extract_openai_output_text(response)
                            done = True
                            eval_count = 0
                            prompt_eval_count = 0
                            provider_suffix = "status=completed"
                        elif provider_kind == "openai_compatible":
                            text = _extract_openai_compatible_text(response)
                            done = True
                            eval_count = 0
                            prompt_eval_count = 0
                            provider_suffix = "status=completed"
                        else:
                            text = _extract_anthropic_text(response)
                            done = True
                            eval_count = 0
                            prompt_eval_count = 0
                            provider_suffix = "status=completed"
                        result = {
                            "ok": bool(text),
                            "executed": True,
                            "route_id": route_id,
                            "endpoint_id": endpoint_id,
                            "provider_kind": provider_kind,
                            "model": model,
                            "done": done,
                            "response_text": text,
                            "prompt_eval_count": prompt_eval_count,
                            "eval_count": eval_count,
                            "summary": (
                                f"route={route_id} endpoint={endpoint_id or '-'} provider={provider_kind} "
                                f"model={model or '-'} {provider_suffix}"
                            ),
                            "binding": candidate,
                            "raw": response if isinstance(response, dict) else {},
                        }
        if endpoint_id and endpoint_id != primary_endpoint_id:
            result["fallback_used"] = True
            result["fallback_endpoint_id"] = endpoint_id
            result["fallback_from_endpoint_id"] = primary_endpoint_id
        if result.get("ok") or result.get("executed"):
            return result
        last_result = result
        if result.get("reason_code") not in fallback_reason_codes:
            return result
    return last_result or {
        "ok": False,
        "executed": False,
        "reason_code": "provider_request_failed",
        "summary": "route=unknown status=request_failed",
    }


def invoke_task_judge_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "judge"
    return result


def invoke_task_research_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_research_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "research"
    return result


def invoke_task_worker_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_worker_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "worker"
    return result


def invoke_background_ticket_worker(
    team_dir: Any,
    *,
    ticket: Any,
    timeout_sec: float | None = None,
    post_json: Any = None,
) -> Dict[str, Any]:
    ticket_data = ticket if isinstance(ticket, dict) else {}
    launch_spec = ticket_data.get("launch_spec") if isinstance(ticket_data.get("launch_spec"), dict) else {}
    prompt_text = _trim(launch_spec.get("provider_prompt"), 8000)
    system_text = _trim(launch_spec.get("provider_system"), 4000)
    contract_summary = _trim(launch_spec.get("provider_task_contract_summary"), 320)
    if not prompt_text:
        rendered = worker_task_contract.render_worker_task_prompt(
            launch_spec.get("provider_task_contract_json")
        )
        prompt_text = _trim(rendered.get("prompt"), 8000)
        if not system_text:
            system_text = _trim(rendered.get("system"), 4000)
        if not contract_summary:
            contract_summary = _trim(rendered.get("summary"), 320)
    binding = endpoint_adapter.resolve_background_ticket_worker_binding(team_dir, ticket_data)
    result = invoke_model_binding(
        binding,
        prompt=prompt_text,
        system=system_text,
        timeout_sec=_coerce_timeout_sec(
            timeout_sec if timeout_sec is not None else launch_spec.get("provider_timeout_sec"),
            default=30.0,
        ),
        post_json=post_json,
    )
    result["kind"] = "background_worker"
    result["launch_kind"] = _trim(launch_spec.get("kind"), 64)
    if contract_summary:
        result["task_contract_summary"] = contract_summary
    if bool(result.get("ok")):
        task_result = worker_task_contract.load_worker_task_result(result.get("response_text"))
        if task_result:
            result["task_result_status"] = _trim(task_result.get("status"), 48) or "-"
            result["task_result_summary"] = _trim(task_result.get("summary_line"), 320) or "-"
            result["task_result_actions"] = list(task_result.get("actions") or [])
            result["task_result_cautions"] = list(task_result.get("cautions") or [])
            result["task_result_evidence_refs"] = list(task_result.get("evidence_refs") or [])
            update_stub = worker_task_contract.derive_worker_task_update_stub(
                launch_spec.get("provider_task_contract_json"),
                task_result,
            )
            gate = worker_task_contract.derive_worker_task_module_gate(
                launch_spec.get("provider_task_contract_json"),
                task_result,
                update_stub=update_stub,
            )
            if gate:
                result["task_gate_status"] = _trim(gate.get("state"), 64) or "-"
                result["task_gate_summary"] = _trim(gate.get("summary_line"), 320) or "-"
            profile = worker_task_contract.derive_worker_task_module_profile(
                launch_spec.get("provider_task_contract_json"),
                task_result,
                update_stub=update_stub,
                gate=gate,
            )
            if profile:
                result["task_profile_status"] = _trim(profile.get("state"), 64) or "-"
                result["task_profile_summary"] = _trim(profile.get("summary_line"), 320) or "-"
            checklist = worker_task_contract.derive_worker_task_module_checklist(
                launch_spec.get("provider_task_contract_json"),
                task_result,
                update_stub=update_stub,
                gate=gate,
                profile=profile,
            )
            if checklist:
                result["task_checklist_status"] = _trim(checklist.get("state"), 64) or "-"
                result["task_checklist_summary"] = _trim(checklist.get("summary_line"), 320) or "-"
            items = worker_task_contract.derive_worker_task_module_items(
                launch_spec.get("provider_task_contract_json"),
                task_result,
                update_stub=update_stub,
                gate=gate,
                profile=profile,
                checklist=checklist,
            )
            if items:
                result["task_items_summary"] = _trim(items.get("summary_line"), 320) or "-"
            if update_stub:
                result["task_update_stub_status"] = _trim(update_stub.get("status"), 48) or "-"
                result["task_update_stub_summary"] = _trim(update_stub.get("summary_line"), 320) or "-"
                result["task_update_stub_targets"] = list(update_stub.get("target_artifacts") or [])
    return result


def invoke_task_escalation_stub(
    team_dir: Any,
    *,
    entry: Any = None,
    task: Any = None,
    prompt: Any,
    system: Any = "",
    pack_profile_override: Any = None,
    timeout_sec: float = 30.0,
    post_json: Any = None,
) -> Dict[str, Any]:
    binding = endpoint_adapter.resolve_task_escalation_binding(
        team_dir,
        entry=entry,
        task=task,
        pack_profile_override=pack_profile_override,
    )
    result = invoke_model_binding(
        binding,
        prompt=prompt,
        system=system,
        timeout_sec=timeout_sec,
        post_json=post_json,
    )
    result["kind"] = "escalation"
    return result
