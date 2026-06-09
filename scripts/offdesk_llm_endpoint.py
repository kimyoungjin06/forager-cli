"""Shared defaults for local/offdesk LLM endpoints."""

from __future__ import annotations

import os


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
