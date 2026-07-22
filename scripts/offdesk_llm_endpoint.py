"""Shared local/offdesk LLM provider resolution.

This module intentionally keeps product defaults generic. Private host/IP
values should come from config or environment, not from repository constants.
"""

from __future__ import annotations

import json
import os
import pathlib
import tomllib
import urllib.error
import urllib.request
from typing import Any


PROVIDER_RESOLUTION_SCHEMA = "offdesk_llm_provider_resolution.v1"
DEFAULT_LOCAL_OLLAMA_BASE_URLS = (
    "http://127.0.0.1:11434",
    "http://localhost:11434",
)
DEFAULT_CODING_MODEL_CANDIDATES = (
    "qwen3-coder-next:latest",
    "qwen3-coder:30b",
    "qwen2.5-coder:32b",
    "qwen2.5-coder:14b",
)
PROVIDER_CONFIG_SECTION_PATHS = (
    ("offdesk", "llm", "provider"),
    ("llm", "provider"),
    ("remote_operator", "agent"),
    ("remote_operator", "telegram", "agent"),
)


class LlmProviderError(RuntimeError):
    pass


def ollama_base_url_from_host(host: str) -> str:
    value = str(host or "").strip()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"http://{value}:11434"


def default_ollama_base_url() -> str:
    for key in (
        "OFFDESK_LLM_BASE_URL",
        "OLLAMA_BASE_URL",
        "OFFDESK_REMOTE_OPERATOR_AGENT_BASE_URL",
    ):
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    for key in ("GPU_SERVER_HOST", "SERVER_HOST"):
        value = ollama_base_url_from_host(os.environ.get(key, ""))
        if value:
            return value
    return "http://127.0.0.1:11434"


def default_ollama_base_urls() -> list[str]:
    return unique_nonempty([default_ollama_base_url(), *DEFAULT_LOCAL_OLLAMA_BASE_URLS])


def csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def unique_nonempty(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def safe_config_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def config_section(config: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    current: Any = config
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return safe_config_dict(current)


def load_provider_config_file(
    path: pathlib.Path,
    section_paths: tuple[tuple[str, ...], ...] = PROVIDER_CONFIG_SECTION_PATHS,
) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, []
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LlmProviderError(f"provider config cannot be read: {path}: {error}") from error
    if not isinstance(raw, dict):
        return {}, []
    merged: dict[str, Any] = {}
    sources: list[str] = []
    for section_path in section_paths:
        section = config_section(raw, section_path)
        if section:
            merged.update(section)
            sources.append(".".join(section_path))
    return merged, sources


def config_string(config: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def config_string_list(config: dict[str, Any], *keys: str) -> list[str]:
    values: list[Any] = []
    for key in keys:
        value = config.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif isinstance(value, tuple):
            values.extend(list(value))
        elif isinstance(value, str):
            values.extend(csv_values(value) if "," in value else [value])
    return unique_nonempty(values)


def config_int(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def env_values(keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = str(os.environ.get(key) or "").strip()
        if not raw:
            continue
        values.extend(csv_values(raw) if "," in raw else [raw])
    return unique_nonempty(values)


def resolve_provider_config(
    *,
    config_file: pathlib.Path,
    section_paths: tuple[tuple[str, ...], ...] = PROVIDER_CONFIG_SECTION_PATHS,
    mode: str = "auto",
    mode_explicit: bool = False,
    provider: str | None = None,
    provider_explicit: bool = False,
    base_urls: list[Any] | None = None,
    models: list[Any] | None = None,
    model_candidates: list[Any] | None = None,
    timeout_sec: int = 20,
    timeout_explicit: bool = False,
    num_ctx: int = 8192,
    num_ctx_explicit: bool = False,
    num_predict: int = 768,
    num_predict_explicit: bool = False,
    env_mode_key: str = "OFFDESK_LLM_MODE",
    env_provider_key: str = "OFFDESK_LLM_PROVIDER",
    env_base_url_keys: tuple[str, ...] = ("OFFDESK_LLM_BASE_URL", "OLLAMA_BASE_URL"),
    env_model_keys: tuple[str, ...] = ("OFFDESK_LLM_MODELS", "OFFDESK_LLM_MODEL", "OFFDESK_OLLAMA_MODEL"),
    env_timeout_key: str = "OFFDESK_LLM_TIMEOUT_SEC",
    env_num_ctx_key: str = "OFFDESK_LLM_NUM_CTX",
    env_num_predict_key: str = "OFFDESK_LLM_NUM_PREDICT",
    default_provider: str = "ollama",
    default_base_urls: list[Any] | None = None,
    default_models: list[Any] | None = None,
) -> dict[str, Any]:
    file_config, config_sources = load_provider_config_file(config_file, section_paths)

    resolved_mode = str(mode or "").strip().lower()
    if not mode_explicit and os.environ.get(env_mode_key):
        resolved_mode = str(os.environ.get(env_mode_key) or "").strip().lower()
    if not mode_explicit and not os.environ.get(env_mode_key):
        resolved_mode = config_string(file_config, "intent_mode", "mode") or resolved_mode or "auto"
    if resolved_mode not in {"auto", "off", "required"}:
        raise LlmProviderError(f"unsupported provider mode: {resolved_mode}")

    resolved_provider = str(provider or "").strip().lower() if provider_explicit else ""
    if not resolved_provider and os.environ.get(env_provider_key):
        resolved_provider = str(os.environ.get(env_provider_key) or "").strip().lower()
    resolved_provider = (
        resolved_provider
        or config_string(file_config, "provider")
        or default_provider
    ).strip().lower()

    resolved_base_urls = unique_nonempty(
        list(base_urls or [])
        + env_values(env_base_url_keys)
        + config_string_list(file_config, "base_urls", "base_url")
        + list(default_base_urls or default_ollama_base_urls())
    )

    resolved_models = unique_nonempty(
        list(models or [])
        + env_values(env_model_keys)
        + config_string_list(file_config, "models", "model")
        + list(model_candidates or [])
        + list(default_models or DEFAULT_CODING_MODEL_CANDIDATES)
    )

    resolved_timeout = int(timeout_sec)
    if not timeout_explicit and os.environ.get(env_timeout_key):
        resolved_timeout = config_int({env_timeout_key: os.environ.get(env_timeout_key)}, env_timeout_key, resolved_timeout)
    if not timeout_explicit and not os.environ.get(env_timeout_key):
        resolved_timeout = config_int(file_config, "timeout_sec", resolved_timeout)

    resolved_num_ctx = int(num_ctx)
    if not num_ctx_explicit and os.environ.get(env_num_ctx_key):
        resolved_num_ctx = config_int({env_num_ctx_key: os.environ.get(env_num_ctx_key)}, env_num_ctx_key, resolved_num_ctx)
    if not num_ctx_explicit and not os.environ.get(env_num_ctx_key):
        resolved_num_ctx = config_int(file_config, "num_ctx", resolved_num_ctx)

    resolved_num_predict = int(num_predict)
    if not num_predict_explicit and os.environ.get(env_num_predict_key):
        resolved_num_predict = config_int({env_num_predict_key: os.environ.get(env_num_predict_key)}, env_num_predict_key, resolved_num_predict)
    if not num_predict_explicit and not os.environ.get(env_num_predict_key):
        resolved_num_predict = config_int(file_config, "num_predict", resolved_num_predict)

    return {
        "schema": PROVIDER_RESOLUTION_SCHEMA,
        "mode": resolved_mode,
        "provider": resolved_provider,
        "base_urls": resolved_base_urls,
        "models": resolved_models,
        "timeout_sec": max(1, resolved_timeout),
        "num_ctx": max(512, resolved_num_ctx),
        "num_predict": max(64, resolved_num_predict),
        "config_file": str(config_file),
        "config_sources": config_sources,
    }


def parse_json_object_response(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("provider response was not a JSON object")
    return parsed


def ollama_available_models(base_url: str, timeout_sec: int) -> list[str]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/tags",
        headers={"Content-Type": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=max(1, int(timeout_sec))) as response:
        raw = json.loads(response.read().decode("utf-8"))
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, list):
        return []
    return unique_nonempty(
        [item.get("name") for item in models if isinstance(item, dict)]
    )


def choose_model(available: list[str], candidates: list[str]) -> str:
    selected = next((item for item in candidates if item in available), "")
    if selected:
        return selected
    selected = next(
        (
            item
            for item in available
            if "qwen" in item.lower() and "coder" in item.lower()
        ),
        "",
    )
    return selected or (available[0] if available else "")


def select_provider_runtime(provider_config: dict[str, Any]) -> dict[str, Any] | None:
    if provider_config.get("mode") == "off":
        return None
    provider = str(provider_config.get("provider") or "").strip().lower()
    if provider != "ollama":
        if provider_config.get("mode") == "required":
            raise LlmProviderError(f"unsupported provider: {provider}")
        return None
    timeout_sec = int(provider_config.get("timeout_sec") or 20)
    candidates = unique_nonempty(list(provider_config.get("models") or []))
    errors: list[str] = []
    for base_url in unique_nonempty(list(provider_config.get("base_urls") or [])):
        try:
            available = ollama_available_models(base_url, min(timeout_sec, 10))
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
            errors.append(f"{base_url}:{type(error).__name__}")
            continue
        if not available:
            continue
        model = choose_model(available, candidates)
        if not model:
            continue
        return {
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "available_models": available,
            "timeout_sec": timeout_sec,
            "num_ctx": int(provider_config.get("num_ctx") or 8192),
            "num_predict": int(provider_config.get("num_predict") or 768),
            "config_sources": list(provider_config.get("config_sources") or []),
        }
    if provider_config.get("mode") == "required":
        detail = ", ".join(errors[:3]) if errors else "no available provider model"
        raise LlmProviderError(f"LLM provider runtime unavailable: {detail}")
    return None


def provider_status(provider_config: dict[str, Any]) -> dict[str, Any]:
    if provider_config.get("mode") == "off":
        return {
            "schema": PROVIDER_RESOLUTION_SCHEMA,
            "status": "disabled",
            "provider": provider_config.get("provider"),
            "config_sources": list(provider_config.get("config_sources") or []),
        }
    try:
        runtime = select_provider_runtime(provider_config)
    except LlmProviderError as error:
        return {
            "schema": PROVIDER_RESOLUTION_SCHEMA,
            "status": "error",
            "provider": provider_config.get("provider"),
            "error": str(error),
            "config_sources": list(provider_config.get("config_sources") or []),
        }
    if not runtime:
        return {
            "schema": PROVIDER_RESOLUTION_SCHEMA,
            "status": "unavailable",
            "provider": provider_config.get("provider"),
            "candidate_base_urls": list(provider_config.get("base_urls") or [])[:4],
            "candidate_models": list(provider_config.get("models") or [])[:4],
            "config_sources": list(provider_config.get("config_sources") or []),
        }
    return {
        "schema": PROVIDER_RESOLUTION_SCHEMA,
        "status": "available",
        "provider": runtime.get("provider"),
        "base_url": runtime.get("base_url"),
        "model": runtime.get("model"),
        "available_model_count": len(runtime.get("available_models") or []),
        "config_sources": list(runtime.get("config_sources") or []),
    }


def call_ollama_json(runtime: dict[str, Any], prompt: str, *, temperature: float = 0.1) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": runtime["model"],
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_ctx": int(runtime.get("num_ctx") or 8192),
            "num_predict": int(runtime.get("num_predict") or 768),
        },
    }
    request = urllib.request.Request(
        str(runtime["base_url"]).rstrip("/") + "/api/generate",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=int(runtime.get("timeout_sec") or 20)) as response:
        raw = json.loads(response.read().decode("utf-8"))
    return parse_json_object_response(str(raw.get("response") or ""))
