#!/usr/bin/env python3
"""Deterministic compatibility/deprecation envelope helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class DeprecatedSurfaceMatch:
    code: str
    surface: str
    replacement: str
    note: str
    next_step: str = ""


def _mother_orch_replacement(rest: str) -> str:
    token = str(rest or "").strip().lower()
    if token in {"status", "check"}:
        return "/auto status"
    if token in {"review", "recovery", "prepare"}:
        return "/offdesk review"
    if token in {"monitor", "tasks", "task"}:
        return "/monitor"
    return "/auto status"


def _swarm_replacement(rest: str) -> str:
    token = str(rest or "").strip().lower()
    if token in {"status", "monitor"}:
        return "/monitor"
    if token in {"review", "recovery", "prepare"}:
        return "/offdesk review"
    if token in {"task", "tasks"}:
        return "/task"
    return "/task"


def match_deprecated_slash_surface(cmd: str, rest: str) -> Optional[DeprecatedSurfaceMatch]:
    token = str(cmd or "").strip().lower()
    tail = str(rest or "").strip()
    if token in {"mother", "mother-orch", "mother_orch"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.mother_orch",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement=_mother_orch_replacement(tail),
            note="Mother-Orch terminology is retired. Use Control Plane / Project Runtime wording instead.",
            next_step="/offdesk review or /monitor depending on intent",
        )
    if token in {"swarm"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.swarm",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement=_swarm_replacement(tail),
            note="Swarm wording is retired. Use Task Team or runtime surfaces instead.",
            next_step="/monitor for runtime status, /task for task detail, /offdesk review for recovery",
        )
    return None


def match_deprecated_cli_surface(text: str) -> Optional[DeprecatedSurfaceMatch]:
    raw = " ".join(str(text or "").strip().split())
    low = raw.lower()
    if low in {"aoe mother", "aoe mother-orch", "aoe mother_orch"} or low.startswith("aoe mother ") or low.startswith("aoe mother-orch ") or low.startswith("aoe mother_orch "):
        rest = raw.split(" ", 2)[2] if len(raw.split(" ", 2)) >= 3 else ""
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.mother_orch",
            surface=raw,
            replacement=_mother_orch_replacement(rest),
            note="Mother-Orch terminology is retired. Use Control Plane / Project Runtime wording instead.",
            next_step="/offdesk review or /monitor depending on intent",
        )
    if low == "aoe swarm" or low.startswith("aoe swarm "):
        rest = raw.split(" ", 2)[2] if len(raw.split(" ", 2)) >= 3 else ""
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.swarm",
            surface=raw,
            replacement=_swarm_replacement(rest),
            note="Swarm wording is retired. Use Task Team or runtime surfaces instead.",
            next_step="/monitor for runtime status, /task for task detail, /offdesk review for recovery",
        )
    return None


def render_deprecated_surface_message(match: DeprecatedSurfaceMatch) -> str:
    lines = [
        "deprecated surface",
        f"- code: {match.code}",
        f"- surface: {match.surface}",
        f"- replacement: {match.replacement}",
        f"- note: {match.note}",
    ]
    if str(match.next_step or "").strip():
        lines.append(f"- next: {match.next_step}")
    return "\n".join(lines).strip()
