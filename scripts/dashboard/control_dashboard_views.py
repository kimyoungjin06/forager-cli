#!/usr/bin/env python3
"""HTML rendering helpers for the read-only Control Dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "templates"

_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(("html", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(name: str, **context: Any) -> str:
    template = _ENV.get_template(name)
    return template.render(**context)
