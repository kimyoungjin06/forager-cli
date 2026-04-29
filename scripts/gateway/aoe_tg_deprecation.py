#!/usr/bin/env python3
"""Deterministic compatibility/deprecation envelope helpers."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence


ReplacementResolver = Callable[[str], str]


@dataclass(frozen=True)
class DeprecatedSurfaceMatch:
    code: str
    surface: str
    replacement: str
    note: str
    next_step: str = ""


@dataclass(frozen=True)
class DeprecatedSurfaceSpec:
    code: str
    note: str
    next_step: str
    slash_tokens: Sequence[str]
    cli_prefixes: Sequence[str]
    slash_replacement: str | ReplacementResolver
    cli_replacement: str | ReplacementResolver


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


def _constant(value: str) -> ReplacementResolver:
    return lambda _rest: value


DEPRECATED_SURFACE_SPECS: tuple[DeprecatedSurfaceSpec, ...] = (
    DeprecatedSurfaceSpec(
        code="deprecated_surface.mother_orch",
        note="Mother-Orch terminology is retired. Use Control Plane / Project Runtime wording instead.",
        next_step="/offdesk review or /monitor depending on intent",
        slash_tokens=("mother", "mother-orch", "mother_orch"),
        cli_prefixes=("aoe mother", "aoe mother-orch", "aoe mother_orch"),
        slash_replacement=_mother_orch_replacement,
        cli_replacement=_mother_orch_replacement,
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.swarm",
        note="Swarm wording is retired. Use Task Team or runtime surfaces instead.",
        next_step="/monitor for runtime status, /task for task detail, /offdesk review for recovery",
        slash_tokens=("swarm",),
        cli_prefixes=("aoe swarm",),
        slash_replacement=_swarm_replacement,
        cli_replacement=_swarm_replacement,
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.orch_map",
        note="Use the canonical project map surface directly instead of /orch map.",
        next_step="/map",
        slash_tokens=("orch map",),
        cli_prefixes=("aoe orch map",),
        slash_replacement=_constant("/map"),
        cli_replacement=_constant("aoe orch list"),
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.monitor_alias",
        note="Use the canonical runtime monitor surface.",
        next_step="/monitor",
        slash_tokens=("tasks", "board"),
        cli_prefixes=(),
        slash_replacement=_constant("/monitor"),
        cli_replacement=_constant(""),
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.lifecycle_alias",
        note="Use the canonical task lifecycle surface.",
        next_step="/task",
        slash_tokens=("lifecycle",),
        cli_prefixes=("aoe lifecycle",),
        slash_replacement=_constant("/task"),
        cli_replacement=_constant("aoe task"),
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.followup_alias",
        note="Use the canonical followup command spelling.",
        next_step="/followup <request_or_alias>",
        slash_tokens=("follow-up",),
        cli_prefixes=("aoe follow-up",),
        slash_replacement=_constant("/followup"),
        cli_replacement=_constant("aoe followup"),
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.offdesk_alias",
        note="Use the canonical offdesk preset surface.",
        next_step="/offdesk status or /offdesk review",
        slash_tokens=("off-desk",),
        cli_prefixes=("aoe off-desk",),
        slash_replacement=_constant("/offdesk"),
        cli_replacement=_constant("aoe offdesk"),
    ),
    DeprecatedSurfaceSpec(
        code="deprecated_surface.gc_alias",
        note="Use the canonical maintenance cleanup surface.",
        next_step="/gc",
        slash_tokens=("cleanup",),
        cli_prefixes=("aoe cleanup",),
        slash_replacement=_constant("/gc"),
        cli_replacement=_constant("aoe gc"),
    ),
)


def _resolve_replacement(resolver: str | ReplacementResolver, rest: str) -> str:
    if callable(resolver):
        return str(resolver(rest or "")).strip()
    return str(resolver or "").strip()


def list_deprecated_surfaces() -> List[dict]:
    rows: List[dict] = []
    for spec in DEPRECATED_SURFACE_SPECS:
        slash_surfaces = [f"/{token}" for token in spec.slash_tokens]
        cli_surfaces = list(spec.cli_prefixes)
        rows.append(
            {
                "code": spec.code,
                "slash_surfaces": slash_surfaces,
                "cli_surfaces": cli_surfaces,
                "slash_replacement": _resolve_replacement(spec.slash_replacement, ""),
                "cli_replacement": _resolve_replacement(spec.cli_replacement, ""),
                "note": spec.note,
                "next_step": spec.next_step,
            }
        )
    return rows


def render_deprecated_surface_inventory(rows: Optional[Sequence[dict]] = None) -> str:
    inventory = list(rows) if rows is not None else list_deprecated_surfaces()
    lines = [
        "deprecated surface inventory",
        f"- total: {len(inventory)}",
        "",
        "surfaces:",
    ]
    for row in inventory:
        lines.append(f"- code: {row.get('code', '')}")
        slash_surfaces = ", ".join(str(value) for value in row.get("slash_surfaces", []) if str(value).strip())
        cli_surfaces = ", ".join(str(value) for value in row.get("cli_surfaces", []) if str(value).strip())
        if slash_surfaces:
            lines.append(f"  slash: {slash_surfaces}")
        if cli_surfaces:
            lines.append(f"  cli: {cli_surfaces}")
        slash_replacement = str(row.get("slash_replacement", "")).strip()
        cli_replacement = str(row.get("cli_replacement", "")).strip()
        if slash_replacement:
            lines.append(f"  slash_replacement: {slash_replacement}")
        if cli_replacement:
            lines.append(f"  cli_replacement: {cli_replacement}")
        next_step = str(row.get("next_step", "")).strip()
        if next_step:
            lines.append(f"  next: {next_step}")
        note = str(row.get("note", "")).strip()
        if note:
            lines.append(f"  note: {note}")
    return "\n".join(lines).strip()


def _match_slash_surface(spec: DeprecatedSurfaceSpec, cmd: str, rest: str) -> Optional[DeprecatedSurfaceMatch]:
    token = str(cmd or "").strip().lower()
    tail = str(rest or "").strip()
    for surface in spec.slash_tokens:
        surface_token = str(surface or "").strip().lower()
        if not surface_token:
            continue
        if " " in surface_token:
            head, expected_rest = surface_token.split(" ", 1)
            if token != head:
                continue
            if str(tail or "").strip().lower() != expected_rest:
                continue
            surface_text = f"/{head} {tail}".strip()
            replacement = _resolve_replacement(spec.slash_replacement, tail)
            return DeprecatedSurfaceMatch(
                code=spec.code,
                surface=surface_text,
                replacement=replacement,
                note=spec.note,
                next_step=spec.next_step,
            )
        if token != surface_token:
            continue
        surface_text = f"/{token}" + (f" {tail}" if tail else "")
        replacement = _resolve_replacement(spec.slash_replacement, tail)
        return DeprecatedSurfaceMatch(
            code=spec.code,
            surface=surface_text,
            replacement=replacement,
            note=spec.note,
            next_step=spec.next_step,
        )
    return None


def _match_cli_surface(spec: DeprecatedSurfaceSpec, text: str) -> Optional[DeprecatedSurfaceMatch]:
    raw = " ".join(str(text or "").strip().split())
    low = raw.lower()
    for prefix in spec.cli_prefixes:
        prefix_norm = " ".join(str(prefix or "").strip().split()).lower()
        if not prefix_norm:
            continue
        if low == prefix_norm:
            rest = ""
        elif low.startswith(prefix_norm + " "):
            rest = raw[len(prefix_norm) :].strip()
        else:
            continue
        replacement = _resolve_replacement(spec.cli_replacement, rest)
        return DeprecatedSurfaceMatch(
            code=spec.code,
            surface=raw,
            replacement=replacement,
            note=spec.note,
            next_step=spec.next_step,
        )
    return None


def match_deprecated_slash_surface(cmd: str, rest: str) -> Optional[DeprecatedSurfaceMatch]:
    for spec in DEPRECATED_SURFACE_SPECS:
        match = _match_slash_surface(spec, cmd, rest)
        if match is not None:
            return match
    return None


def match_deprecated_cli_surface(text: str) -> Optional[DeprecatedSurfaceMatch]:
    for spec in DEPRECATED_SURFACE_SPECS:
        match = _match_cli_surface(spec, text)
        if match is not None:
            return match
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List deterministic deprecated gateway surfaces")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    rows = list_deprecated_surfaces()
    if args.json:
        print(json.dumps({"deprecated_surfaces": rows, "count": len(rows)}, ensure_ascii=False, indent=2))
    else:
        print(render_deprecated_surface_inventory(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
