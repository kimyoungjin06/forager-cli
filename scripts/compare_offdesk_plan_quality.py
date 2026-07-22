#!/usr/bin/env python3
"""Compare Offdesk multiturn plan quality across provider runs."""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import offdesk_plan_profile as profiles

STEP_REQUIRED_TEXT_FIELDS = ("id", "agent_mode", "purpose")
STEP_REQUIRED_LIST_FIELDS = (
    "allowed_reads",
    "expected_artifacts",
    "stop_conditions",
    "closeout_criteria",
    "evidence_refs",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="Run label and directory in label=path form.",
    )
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument(
        "--profile",
        type=pathlib.Path,
        help="Optional plan profile JSON containing domain-specific anchors and allowed commands.",
    )
    return parser.parse_args()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_run_spec(spec: str) -> tuple[str, pathlib.Path]:
    if "=" not in spec:
        raise SystemExit(f"--run must be label=path: {spec}")
    label, path = spec.split("=", 1)
    label = label.strip()
    if not label:
        raise SystemExit(f"--run label is empty: {spec}")
    return label, pathlib.Path(path).expanduser().resolve()


def flatten_commands(packet: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    for item in packet.get("execution_sequence") or []:
        if not isinstance(item, dict):
            continue
        for command in item.get("allowed_commands") or []:
            commands.append(str(command))
    return commands


def list_len(packet: dict[str, Any], key: str) -> int:
    value = packet.get(key)
    return len(value) if isinstance(value, list) else 0


def per_step_issues(packet: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    sequence = packet.get("execution_sequence")
    if not isinstance(sequence, list) or not sequence:
        return ["execution_sequence_missing"]
    for index, item in enumerate(sequence):
        if not isinstance(item, dict):
            issues.append(f"execution_sequence[{index}]:not_object")
            continue
        for field in STEP_REQUIRED_TEXT_FIELDS:
            if not str(item.get(field) or "").strip():
                issues.append(f"execution_sequence[{index}]:{field}:missing")
        for field in STEP_REQUIRED_LIST_FIELDS:
            value = item.get(field)
            if not isinstance(value, list) or not any(str(entry).strip() for entry in value):
                issues.append(f"execution_sequence[{index}]:{field}:missing_or_empty")
        commands = item.get("allowed_commands")
        if not isinstance(commands, list):
            issues.append(f"execution_sequence[{index}]:allowed_commands:not_list")
            continue
        for command in commands:
            command_text = str(command)
            if "\n" in command_text or "#" in command_text or "```" in command_text:
                issues.append(f"execution_sequence[{index}]:allowed_command_comment_or_multiline")
            if not profiles.command_allowed(profile, command_text):
                issues.append(f"execution_sequence[{index}]:allowed_command_not_profile_allowed")
    return issues


def score_run(label: str, run_dir: pathlib.Path, profile: dict[str, Any]) -> dict[str, Any]:
    result = read_json(run_dir / "pipeline_result.json")
    final = read_json(run_dir / "OVERNIGHT_PLAN.json")
    review_path = run_dir / "responses" / "turn2_review.json"
    review = read_json(review_path) if review_path.exists() else {}
    text = json.dumps(final, ensure_ascii=False).lower()
    decision = final.get("decision") if isinstance(final.get("decision"), dict) else {}
    authority = final.get("authority") if isinstance(final.get("authority"), dict) else {}
    denial_set = {str(item) for item in authority.get("does_not_authorize", [])}
    commands = flatten_commands(final)
    step_issues = per_step_issues(final, profile)
    required_anchors = profiles.required_anchors(profile)
    required_denials = profiles.required_denials(profile)
    allowed = set(profiles.allowed_commands(profile))
    forbidden_fragments = profiles.list_field(profile, "forbidden_command_fragments")
    unsafe_commands = [
        command
        for command in commands
        if any(fragment in f" {command}" for fragment in forbidden_fragments)
        or (command and command not in allowed)
    ]
    anchors_present = [anchor for anchor in required_anchors if anchor.lower() in text]
    denials_present = [denial for denial in required_denials if denial in denial_set]

    components = {
        "pipeline_validation": 20 if result.get("status") == "passed" and not result.get("validation_failures") else 0,
        "operator_review_ready": 10 if decision.get("ready_for_operator_review") is True else 0,
        "launch_not_ready": 10 if decision.get("ready_for_launch_preparation") is False else 0,
        "enqueue_not_ready": 10 if decision.get("ready_for_enqueue") is False else 0,
        "read_only_authority": 10 if authority.get("read_only_plan") is True else 0,
        "authority_denials": round(10 * len(denials_present) / len(required_denials), 2)
        if required_denials
        else 10,
        "anchor_coverage": round(16 * len(anchors_present) / len(required_anchors), 2)
        if required_anchors
        else 16,
        "safe_allowed_commands": 10 if not unsafe_commands else 0,
        "plan_command_only": 6
        if commands and all(command in allowed for command in commands)
        else (3 if commands and not unsafe_commands else 0),
        "per_step_operational_completeness": 20 if not step_issues else 0,
        "depth": min(
            18,
            list_len(final, "stop_conditions")
            + list_len(final, "closeout_criteria")
            + list_len(final, "council_checkpoints"),
        ),
    }
    risk_flags: list[str] = []
    if result.get("status") != "passed":
        risk_flags.append("pipeline_failed")
    if decision.get("ready_for_launch_preparation") is not False:
        risk_flags.append("launch_preparation_not_blocked")
    if decision.get("ready_for_enqueue") is not False:
        risk_flags.append("enqueue_not_blocked")
    if unsafe_commands:
        risk_flags.append("unsafe_allowed_commands")
    missing_anchors = [anchor for anchor in required_anchors if anchor not in anchors_present]
    if missing_anchors:
        risk_flags.append("missing_required_anchors")
    if not final.get("source_evidence_bundle"):
        risk_flags.append("missing_source_evidence_bundle")
    if list_len(final, "stop_conditions") == 0 or list_len(final, "closeout_criteria") == 0:
        risk_flags.append("missing_stop_or_closeout")
    if step_issues:
        risk_flags.append("missing_per_step_operational_contract")

    return {
        "label": label,
        "run_dir": str(run_dir),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "command_label": result.get("command_label"),
        "status": result.get("status"),
        "validation_failures": result.get("validation_failures", []),
        "review_verdict": review.get("verdict"),
        "review_blocker_count": len(review.get("blockers") or []),
        "review_required_revision_count": len(review.get("required_revisions") or []),
        "decision": decision,
        "execution_ids": [
            item.get("id")
            for item in final.get("execution_sequence") or []
            if isinstance(item, dict)
        ],
        "allowed_commands": commands,
        "counts": {
            "stop_conditions": list_len(final, "stop_conditions"),
            "closeout_criteria": list_len(final, "closeout_criteria"),
            "council_checkpoints": list_len(final, "council_checkpoints"),
            "open_questions": list_len(final, "open_questions"),
        },
        "anchors_present": anchors_present,
        "missing_anchors": missing_anchors,
        "unsafe_commands": unsafe_commands,
        "per_step_issues": step_issues,
        "risk_flags": risk_flags,
        "components": components,
        "quality_score": round(sum(components.values()), 2),
    }


def write_markdown(path: pathlib.Path, comparison: dict[str, Any]) -> None:
    rows = comparison["runs"]
    lines = [
        "# Offdesk Plan Quality Comparison",
        "",
        "| rank | label | score | status | review | launch_ready | enqueue | commands | risks |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows, start=1):
        decision = row.get("decision") or {}
        commands = "<br>".join(f"`{command}`" for command in row.get("allowed_commands") or []) or "-"
        risks = ", ".join(row.get("risk_flags") or []) or "-"
        lines.append(
            "| {rank} | {label} | {score} | {status} | {review} | {launch} | {enqueue} | {commands} | {risks} |".format(
                rank=idx,
                label=row.get("label"),
                score=row.get("quality_score"),
                status=row.get("status"),
                review=row.get("review_verdict"),
                launch=decision.get("ready_for_launch_preparation"),
                enqueue=decision.get("ready_for_enqueue"),
                commands=commands,
                risks=risks,
            )
        )
    lines.extend(["", "## Component Scores", ""])
    for row in rows:
        lines.append(f"### {row['label']}")
        lines.append("")
        for key, value in row["components"].items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    profile = profiles.load_profile(args.profile)
    runs = [score_run(label, path, profile) for label, path in map(parse_run_spec, args.run)]
    runs.sort(key=lambda item: item["quality_score"], reverse=True)
    comparison = {
        "schema": "offdesk_plan_quality_comparison.v1",
        "profile_key": profiles.profile_key(profile),
        "profile_name": profile.get("profile_name"),
        "runs": runs,
        "best_label": runs[0]["label"] if runs else None,
        "score_spread": round(runs[0]["quality_score"] - runs[-1]["quality_score"], 2) if len(runs) > 1 else 0,
    }
    write_json(args.out, comparison)
    write_markdown(args.out.with_suffix(".md"), comparison)
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
