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


def _first_token(rest: str) -> str:
    return str(rest or "").strip().split(" ", 1)[0].strip().lower()


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
    if token == "orch" and _first_token(tail) in {"map"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.orch_map",
            surface=f"/orch {tail}".strip(),
            replacement="/map",
            note="Use the canonical project map surface directly instead of /orch map.",
            next_step="/map",
        )
    if token in {"tasks", "board"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.monitor_alias",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement="/monitor",
            note="Use the canonical runtime monitor surface.",
            next_step="/monitor",
        )
    if token in {"lifecycle"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.lifecycle_alias",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement="/task",
            note="Use the canonical task lifecycle surface.",
            next_step="/task",
        )
    if token in {"follow-up"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.followup_alias",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement="/followup",
            note="Use the canonical followup command spelling.",
            next_step="/followup <request_or_alias>",
        )
    if token in {"off-desk"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.offdesk_alias",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement="/offdesk",
            note="Use the canonical offdesk preset surface.",
            next_step="/offdesk status or /offdesk review",
        )
    if token in {"cleanup"}:
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.gc_alias",
            surface=f"/{token}" + (f" {tail}" if tail else ""),
            replacement="/gc",
            note="Use the canonical maintenance cleanup surface.",
            next_step="/gc",
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
    if low == "aoe orch map" or low.startswith("aoe orch map "):
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.orch_map",
            surface=raw,
            replacement="aoe orch list",
            note="Use the canonical project listing surface instead of orch map.",
            next_step="aoe orch list",
        )
    if low == "aoe lifecycle" or low.startswith("aoe lifecycle "):
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.lifecycle_alias",
            surface=raw,
            replacement="aoe task",
            note="Use the canonical task lifecycle surface.",
            next_step="aoe task",
        )
    if low == "aoe follow-up" or low.startswith("aoe follow-up "):
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.followup_alias",
            surface=raw,
            replacement="aoe followup",
            note="Use the canonical followup command spelling.",
            next_step="aoe followup <request_or_alias>",
        )
    if low == "aoe off-desk" or low.startswith("aoe off-desk "):
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.offdesk_alias",
            surface=raw,
            replacement="aoe offdesk",
            note="Use the canonical offdesk preset surface.",
            next_step="aoe offdesk status",
        )
    if low == "aoe cleanup" or low.startswith("aoe cleanup "):
        return DeprecatedSurfaceMatch(
            code="deprecated_surface.gc_alias",
            surface=raw,
            replacement="aoe gc",
            note="Use the canonical maintenance cleanup surface.",
            next_step="aoe gc",
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
