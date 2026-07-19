"""Curated dispatch allowlist: named command templates for /run.

The allowlist lives on the trusted local machine. The remote operator can only
name a template; the runner and command come from this file, never from the
Telegram message. This is the safe alternative to free-form /dispatch: with an
allowlist configured the operator can dispatch only pre-vetted commands, without
enabling the arbitrary-command --enable-runtime-dispatch surface.

Loading is deliberately tolerant. A missing or malformed file yields no
templates so /run degrades to "not configured" rather than crashing the poll
loop, and only fully-specified templates (name, runner, command) survive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_dispatch_allowlist(path: Any) -> dict[str, Any]:
    """Read the allowlist file into ``{"templates": [...]}``.

    Any read or parse failure returns an empty template set; the caller treats
    that as "curated dispatch not configured". Templates missing a name, runner,
    or command are dropped, and duplicate names (case-insensitive) keep the
    first entry so lookups are unambiguous.
    """

    if not path:
        return {"templates": []}
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {"templates": []}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"templates": []}

    entries = data.get("templates") if isinstance(data, dict) else None
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            runner = str(entry.get("runner") or "").strip()
            command = str(entry.get("command") or "").strip()
            if not name or not runner or not command:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            templates.append(
                {
                    "name": name,
                    "runner": runner,
                    "command": command,
                    "description": str(entry.get("description") or "").strip(),
                }
            )
    return {"templates": templates}


def dispatch_templates(allowlist: dict[str, Any]) -> list[dict[str, Any]]:
    templates = allowlist.get("templates") if isinstance(allowlist, dict) else None
    return list(templates) if isinstance(templates, list) else []


def find_dispatch_template(allowlist: dict[str, Any], name: str) -> dict[str, Any] | None:
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for template in dispatch_templates(allowlist):
        if str(template.get("name") or "").strip().lower() == wanted:
            return template
    return None
