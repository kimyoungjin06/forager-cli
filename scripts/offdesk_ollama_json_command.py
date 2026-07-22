#!/usr/bin/env python3
"""Command-provider shim that returns one Ollama JSON response.

Planner command modes send a prompt on stdin and optionally expose
OFFDESK_* prompt/response paths. This shim keeps the contract small: read
prompt, call Ollama /api/generate with format=json, print the model JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from typing import Any

from offdesk_llm_endpoint import default_ollama_base_url


DEFAULT_BASE_URL = default_ollama_base_url()
DEFAULT_MODEL = "qwen3-coder-next:latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.environ.get("OFFDESK_OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument("--timeout-sec", type=int, default=300)
    return parser.parse_args()


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
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
        raise ValueError("Ollama response was not a JSON object")
    return parsed


def main() -> int:
    args = parse_args()
    prompt = sys.stdin.read()
    if not prompt.strip():
        prompt_path = (
            os.environ.get("OFFDESK_PLANNER_COUNCIL_PROMPT_PATH")
            or os.environ.get("OFFDESK_PLAN_PROMPT_PATH")
        )
        if prompt_path:
            prompt = pathlib.Path(prompt_path).read_text(encoding="utf-8")
    if not prompt.strip():
        print("prompt was empty", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": args.temperature,
            "top_p": 0.9,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
        },
    }
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout_sec) as response:
            raw = json.loads(response.read().decode("utf-8"))
        parsed = parse_json_response(str(raw.get("response") or ""))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
        print(f"ollama_json_command failed: {error}", file=sys.stderr)
        return 1

    output = json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"
    response_path = (
        os.environ.get("OFFDESK_PLANNER_COUNCIL_RESPONSE_PATH")
        or os.environ.get("OFFDESK_PLAN_RESPONSE_PATH")
    )
    if response_path:
        pathlib.Path(response_path).write_text(output, encoding="utf-8")
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
