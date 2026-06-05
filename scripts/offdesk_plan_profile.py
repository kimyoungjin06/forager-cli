#!/usr/bin/env python3
"""Shared profile helpers for Offdesk planning harness scripts."""

from __future__ import annotations

import json
import pathlib
from typing import Any


GENERIC_PROFILE: dict[str, Any] = {
    "profile_key": "generic",
    "profile_name": "Generic Offdesk Plan",
    "task_label": "Offdesk task",
    "plan_subject": "Offdesk autonomy plan packet",
    "context_profile_key": None,
    "required_anchors": (),
    "required_denials": ("enqueue", "launch", "approval", "delete", "accepted truth"),
    "allowed_commands": (),
    "hard_rules": (
        "This plan does not enqueue, launch, approve, move, delete, archive, or promote anything.",
        "Use repo-root-relative commands only.",
        "Include stop conditions and closeout criteria before any long run can be launched.",
        "Keep JSON concise: at most 3 execution steps, at most 6 items per list, no repeated prose, and no field value longer than 600 characters.",
    ),
    "planner_requirements": (),
    "revision_requirements": (),
    "final_review_requirements": (),
    "forbidden_command_fragments": (" --exec", " enqueue", " launch", " approve"),
    "mock_objective": "Turn the supplied evidence into an inspectable overnight diagnostic plan.",
    "mock_execution_sequence": (
        {
            "id": "inspect_current_state",
            "agent_mode": "analysis",
            "purpose": "Summarize the current state from supplied evidence before proposing runtime work.",
            "allowed_reads_from_context": "primary_artifacts",
            "allowed_commands_from_profile": True,
            "expected_artifacts": ["current-state diagnostic note"],
            "stop_conditions": ["current state cannot be tied to evidence_refs"],
            "closeout_criteria": ["open questions and evidence gaps are explicit"],
            "evidence_refs": ["source_evidence_bundle"],
        },
        {
            "id": "write_operator_review_packet",
            "agent_mode": "writing",
            "purpose": "Produce operator-review wording without authorizing runtime execution.",
            "allowed_reads_from_context": "primary_artifacts",
            "allowed_commands": [],
            "expected_artifacts": ["operator review packet"],
            "stop_conditions": ["draft claims approval or finality"],
            "closeout_criteria": ["handoff states that runtime approval remains separate"],
            "evidence_refs": ["source_evidence_bundle"],
        },
    ),
    "mock_council_checkpoints": (
        "Before long execution, check that required evidence anchors are represented.",
        "Before enqueue, confirm the packet is still read-only and operator-reviewed.",
    ),
    "mock_launch_preconditions": (
        "operator reviews this plan packet",
        "runtime dispatch approval is created and matched to the exact task id",
    ),
    "mock_open_questions": ("Which evidence gap blocks promotion to runtime preparation?",),
}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def load_profile(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return json.loads(json.dumps(GENERIC_PROFILE, ensure_ascii=False))
    resolved = path.expanduser().resolve()
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected profile JSON object: {resolved}")
    merged = json.loads(json.dumps(GENERIC_PROFILE, ensure_ascii=False))
    merged.update(data)
    merged["profile_path"] = str(resolved)
    return merged


def profile_key(profile: dict[str, Any]) -> str:
    return str(profile.get("profile_key") or "generic")


def task_label(profile: dict[str, Any]) -> str:
    return str(profile.get("task_label") or profile.get("profile_name") or "Offdesk task")


def plan_subject(profile: dict[str, Any]) -> str:
    return str(profile.get("plan_subject") or "Offdesk autonomy plan packet")


def list_field(profile: dict[str, Any], key: str) -> list[str]:
    return [str(item) for item in _as_list(profile.get(key)) if str(item).strip()]


def allowed_commands(profile: dict[str, Any]) -> list[str]:
    return list_field(profile, "allowed_commands")


def required_anchors(profile: dict[str, Any]) -> list[str]:
    return list_field(profile, "required_anchors")


def required_denials(profile: dict[str, Any]) -> list[str]:
    return list_field(profile, "required_denials")


def command_allowed(profile: dict[str, Any], command: str) -> bool:
    allowed = set(allowed_commands(profile))
    return not command or command in allowed


def command_policy_text(profile: dict[str, Any]) -> str:
    commands = allowed_commands(profile)
    if not commands:
        return "allowed_commands must be empty unless the profile explicitly supplies safe read-only commands."
    rendered = "\n".join(f"  - {command}" for command in commands)
    return f"Any non-empty allowed_commands entry must be one of:\n{rendered}"


def profile_prompt_block(profile: dict[str, Any]) -> str:
    lines = [f"Profile key: {profile_key(profile)}", f"Profile name: {profile.get('profile_name')}"]
    requirements = list_field(profile, "planner_requirements")
    if requirements:
        lines.extend(["Profile-specific planning requirements:"])
        lines.extend(f"- {item}" for item in requirements)
    anchors = required_anchors(profile)
    if anchors:
        lines.append(f"Required evidence anchors: {', '.join(anchors)}")
    lines.append(command_policy_text(profile))
    return "\n".join(lines)


def primary_artifacts(context: dict[str, Any]) -> list[str]:
    contract = context.get("evidence_contract") if isinstance(context.get("evidence_contract"), dict) else {}
    return [str(item) for item in _as_list(contract.get("primary_artifacts")) if str(item).strip()]


def mock_steps(profile: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    reads = primary_artifacts(context) or ["source_evidence_bundle"]
    commands = allowed_commands(profile)
    steps: list[dict[str, Any]] = []
    for raw in _as_list(profile.get("mock_execution_sequence")):
        if not isinstance(raw, dict):
            continue
        step = dict(raw)
        if step.pop("allowed_reads_from_context", None):
            step["allowed_reads"] = reads
        if step.pop("allowed_commands_from_profile", False):
            step["allowed_commands"] = commands
        step.setdefault("allowed_reads", reads)
        step.setdefault("allowed_commands", [])
        step.setdefault("expected_artifacts", ["operator-review artifact"])
        step.setdefault("stop_conditions", ["required evidence is missing"])
        step.setdefault("closeout_criteria", ["handoff keeps runtime approval separate"])
        step.setdefault("evidence_refs", ["source_evidence_bundle"])
        steps.append(step)
    return steps
