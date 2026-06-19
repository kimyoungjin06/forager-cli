#!/usr/bin/env python3
"""Render compact operator-state cards for human UI surfaces.

This module is intentionally pure: it reads one `operator_state_card.v1`
document and renders surface projections without querying Forager state,
Telegram, tmux, or the filesystem beyond the requested input file.
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys
from typing import Any


CARD_SCHEMA = "operator_state_card.v1"
RENDER_SCHEMA = "operator_state_card_render.v1"
WEBUI_CARD_SCHEMA = "operator_state_card.webui_card.v1"
TELEGRAM_MOBILE_CONTRACT_SCHEMA = "telegram_mobile_card_contract.v1"

MOBILE_CARD_MAX_LINES = 5
MOBILE_CARD_MAX_CHARS = 360
MOBILE_CARD_FORBIDDEN_TERMS = (
    "request_id",
    "sha256:",
    "/home/",
    "/tmp/",
    ".telegram_decision_state",
    "runtime_handle_alive",
    "dispatch",
    "shell",
)

SEVERITY_LABELS = {
    "ok": "정상",
    "info": "정보",
    "attention": "주의",
    "blocked": "막힘",
    "critical": "위험",
}
ALLOWED_SEVERITIES = set(SEVERITY_LABELS)


class OperatorStateCardError(ValueError):
    pass


def compact(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + "..."


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise OperatorStateCardError(f"{field}:expected_object")
    return value


def require_text(card: dict[str, Any], field: str) -> str:
    value = card.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OperatorStateCardError(f"{field}:missing")
    return value.strip()


def validate_card(card: dict[str, Any]) -> None:
    if card.get("schema") != CARD_SCHEMA:
        raise OperatorStateCardError("schema:unsupported")
    for field in (
        "id",
        "title",
        "severity",
        "state_summary",
        "authorization_boundary",
    ):
        require_text(card, field)
    severity = require_text(card, "severity")
    if severity not in ALLOWED_SEVERITIES:
        raise OperatorStateCardError(f"severity:unsupported:{severity}")
    primary = require_mapping(card.get("primary_blocker_or_decision"), "primary_blocker_or_decision")
    next_action = require_mapping(card.get("next_safe_action"), "next_safe_action")
    detail_ref = require_mapping(card.get("detail_ref"), "detail_ref")
    for field in ("label", "summary"):
        require_text(primary, field)
    require_text(next_action, "label")
    require_text(detail_ref, "label")
    require_text(detail_ref, "kind")


def severity_label(card: dict[str, Any]) -> str:
    return SEVERITY_LABELS.get(str(card.get("severity")), str(card.get("severity")))


def render_telegram_text(card: dict[str, Any]) -> str:
    primary = require_mapping(card.get("primary_blocker_or_decision"), "primary_blocker_or_decision")
    next_action = require_mapping(card.get("next_safe_action"), "next_safe_action")
    lines = [
        f"<b>{html.escape(compact(card['title'], 48))}</b>",
        f"{severity_label(card)} · {html.escape(compact(card['state_summary'], 96))}",
        f"{html.escape(compact(primary['label'], 28))}: {html.escape(compact(primary['summary'], 112))}",
        f"다음 조치: {html.escape(compact(next_action['label'], 96))}",
        f"권한: {html.escape(compact(card['authorization_boundary'], 96))}",
    ]
    return "\n".join(line for line in lines if line.strip())


def mobile_card_contract(message: str) -> dict[str, Any]:
    lines = str(message or "").splitlines()
    content_lines = [line.strip() for line in lines if line.strip()]
    warnings: list[str] = []
    if len(lines) > MOBILE_CARD_MAX_LINES:
        warnings.append("too_many_lines")
    if len(str(message or "")) > MOBILE_CARD_MAX_CHARS:
        warnings.append("too_many_chars")
    has_title = bool(content_lines and content_lines[0].startswith("<b>"))
    body_lines = content_lines[1:] if has_title else content_lines
    has_status_headline = bool(body_lines)
    has_next_action = any("다음 조치" in line for line in body_lines)
    if not has_title:
        warnings.append("missing_title")
    if not has_status_headline:
        warnings.append("missing_status_headline")
    if not has_next_action:
        warnings.append("missing_next_action")
    leaked_terms = [term for term in MOBILE_CARD_FORBIDDEN_TERMS if term in message]
    if leaked_terms:
        warnings.append("forbidden_terms:" + ",".join(leaked_terms))
    return {
        "schema": TELEGRAM_MOBILE_CONTRACT_SCHEMA,
        "line_count": len(lines),
        "char_count": len(str(message or "")),
        "max_lines": MOBILE_CARD_MAX_LINES,
        "max_chars": MOBILE_CARD_MAX_CHARS,
        "has_title": has_title,
        "has_status_headline": has_status_headline,
        "has_next_action": has_next_action,
        "warnings": warnings,
    }


def render_tui_rows(card: dict[str, Any]) -> list[dict[str, Any]]:
    primary = require_mapping(card.get("primary_blocker_or_decision"), "primary_blocker_or_decision")
    next_action = require_mapping(card.get("next_safe_action"), "next_safe_action")
    rows = [
        {
            "label": "상태",
            "value": compact(card["state_summary"], 120),
            "severity": card["severity"],
        },
        {
            "label": compact(primary["label"], 24),
            "value": compact(primary["summary"], 120),
            "severity": card["severity"],
        },
        {
            "label": "다음 조치",
            "value": compact(next_action["label"], 120),
            "severity": card["severity"],
        },
        {
            "label": "권한 경계",
            "value": compact(card["authorization_boundary"], 120),
            "severity": "info",
        },
    ]
    command = str(next_action.get("command") or "").strip()
    if command:
        rows.append(
            {
                "label": "명령",
                "value": compact(command, 120),
                "severity": "info",
            }
        )
    return rows


def render_webui_card(card: dict[str, Any]) -> dict[str, Any]:
    primary = require_mapping(card.get("primary_blocker_or_decision"), "primary_blocker_or_decision")
    next_action = require_mapping(card.get("next_safe_action"), "next_safe_action")
    detail_ref = require_mapping(card.get("detail_ref"), "detail_ref")
    return {
        "schema": WEBUI_CARD_SCHEMA,
        "id": card["id"],
        "title": card["title"],
        "severity": card["severity"],
        "state_summary": card["state_summary"],
        "primary_blocker_or_decision": {
            "label": primary["label"],
            "summary": primary["summary"],
        },
        "next_safe_action": next_action,
        "detail_ref": detail_ref,
        "authorization_boundary": card["authorization_boundary"],
        "counts": card.get("counts", {}),
    }


def render_all(card: dict[str, Any]) -> dict[str, Any]:
    validate_card(card)
    telegram_text = render_telegram_text(card)
    return {
        "schema": RENDER_SCHEMA,
        "source_schema": CARD_SCHEMA,
        "id": card["id"],
        "telegram": {
            "text": telegram_text,
            "mobile_card_contract": mobile_card_contract(telegram_text),
        },
        "tui_rows": render_tui_rows(card),
        "webui_card": render_webui_card(card),
    }


def load_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OperatorStateCardError("input:expected_object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        rendered = render_all(load_json(args.fixture))
    except (OSError, json.JSONDecodeError, OperatorStateCardError) as error:
        print(f"operator_state_card: {error}", file=sys.stderr)
        return 2
    output = json.dumps(rendered, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
