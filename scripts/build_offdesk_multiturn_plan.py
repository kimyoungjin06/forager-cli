#!/usr/bin/env python3
"""Build a multi-turn Offdesk plan packet from evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from typing import Any

import offdesk_plan_profile as profiles
from offdesk_llm_endpoint import default_ollama_base_url


DEFAULT_BASE_URL = default_ollama_base_url()
DEFAULT_MODEL = "qwen3-coder-next:latest"
PLAN_SCHEMA = "offdesk_multiturn_plan.v1"
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
    parser.add_argument("--evidence-bundle", type=pathlib.Path, required=True)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    parser.add_argument(
        "--profile",
        type=pathlib.Path,
        help="Optional plan profile JSON containing domain-specific anchors, allowed commands, and prompt rules.",
    )
    parser.add_argument("--provider", choices=("ollama", "command"), default="ollama")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--command",
        help=(
            "External command for --provider command. The prompt is sent on stdin. "
            "Optional placeholders: {prompt_path}, {response_path}, {turn}."
        ),
    )
    parser.add_argument("--command-label", help="Human-readable provider/model label for command runs.")
    parser.add_argument("--command-timeout-sec", type=int, default=900)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic local turns instead of calling the model.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return data


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def compact(value: Any, limit: int = 12000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...TRUNCATED..."


def module_profile(bundle: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    profiles = bundle.get("module_operation_profiles")
    context_profile_key = profile.get("context_profile_key")
    if not context_profile_key or not isinstance(profiles, dict):
        return {}
    selected = profiles.get(str(context_profile_key))
    return selected if isinstance(selected, dict) else {}


def plan_context(bundle: dict[str, Any], source_path: pathlib.Path, profile: dict[str, Any]) -> dict[str, Any]:
    module = module_profile(bundle, profile)
    return {
        "source_evidence_bundle": str(source_path),
        "profile_key": profiles.profile_key(profile),
        "profile_name": profile.get("profile_name"),
        "created_from_bundle_kind": bundle.get("kind"),
        "repo": bundle.get("repo"),
        "current_state": module.get("current_state") or bundle.get("current_state") or {},
        "operation_gates": module.get("operation_gates") or {},
        "next_actions": module.get("next_actions") or bundle.get("next_actions") or [],
        "allowed_operations": module.get("allowed_operations") or bundle.get("allowed_operations") or [],
        "forbidden_actions": module.get("forbidden_actions") or bundle.get("forbidden_actions") or [],
        "evidence_contract": module.get("evidence_contract") or bundle.get("evidence_contract") or {},
        "ondesk_return": module.get("ondesk_return") or {},
        "layout": module.get("layout") or {},
    }


def common_system_contract(profile: dict[str, Any] | None = None) -> str:
    profile = profile or profiles.GENERIC_PROFILE
    hard_rules = "\n".join(f"- {item}" for item in profiles.list_field(profile, "hard_rules"))
    profile_block = profiles.profile_prompt_block(profile)
    return f"""You are building an Offdesk autonomy plan packet.
Return only valid JSON. Do not use markdown fences.

Hard rules:
{hard_rules}

Profile contract:
{profile_block}
"""


def draft_prompt(context: dict[str, Any], profile: dict[str, Any]) -> str:
    return f"""{common_system_contract(profile)}

Turn 1: Draft an overnight plan packet from the seed next_actions.

Context:
{compact(context)}

Return JSON with this shape:
{{
  "schema": "offdesk_multiturn_plan_draft.v1",
  "objective": "...",
  "seed_actions_used": ["..."],
  "execution_sequence": [
    {{
      "id": "diagnose_primary_gate_failure",
      "agent_mode": "analysis",
      "purpose": "...",
      "allowed_reads": ["..."],
      "allowed_commands": ["..."],
      "expected_artifacts": ["..."],
      "stop_conditions": ["..."],
      "closeout_criteria": ["..."],
      "evidence_refs": ["..."]
    }}
  ],
  "council_checkpoints": ["..."],
  "forbidden_actions": ["..."],
  "launch_preconditions": ["..."],
  "open_questions": ["..."]
}}
"""


def review_prompt(context: dict[str, Any], draft: dict[str, Any], profile: dict[str, Any]) -> str:
    return f"""{common_system_contract(profile)}

Turn 2: Skeptically review the draft. Look for missing gates, unsafe launch
assumptions, weak evidence refs, command ambiguity, and places where the draft
could be mistaken for approval to launch.

Context:
{compact(context)}

Draft:
{compact(draft)}

Return JSON with this shape:
{{
  "schema": "offdesk_multiturn_plan_review.v1",
  "verdict": "revise|usable",
  "blockers": ["..."],
  "required_revisions": ["..."],
  "missing_evidence": ["..."],
  "unsafe_or_ambiguous_items": ["..."],
  "strengths": ["..."],
  "go_no_go": {{
    "ready_for_operator_review": true,
    "ready_for_enqueue": false,
    "reason": "..."
  }}
}}
"""


def revision_prompt(context: dict[str, Any], draft: dict[str, Any], review: dict[str, Any], profile: dict[str, Any]) -> str:
    return f"""{common_system_contract(profile)}

Turn 3: Revise the draft into the final operator-review packet. Address the
review directly. Keep ready_for_enqueue false because this artifact is only a
planning packet.

Context:
{compact(context)}

Draft:
{compact(draft)}

Review:
{compact(review)}

Return JSON with this exact top-level schema:
{{
  "schema": "{PLAN_SCHEMA}",
  "profile_key": "{profiles.profile_key(profile)}",
  "generated_at": "{utc_now()}",
  "decision": {{
    "ready_for_operator_review": true,
    "ready_for_launch_preparation": false,
    "ready_for_enqueue": false,
    "reason": "Set ready_for_launch_preparation true only in a later artifact after a human operator resolves every launch blocker."
  }},
  "source_evidence_bundle": "...",
  "current_state": {{}},
  "objective": "...",
  "execution_sequence": [],
  "council_checkpoints": [],
  "launch_preconditions": [],
  "stop_conditions": [],
  "closeout_criteria": [],
  "forbidden_actions": [],
  "open_questions": [],
  "review_trace": {{
    "draft_schema": "...",
    "review_verdict": "...",
    "required_revisions_addressed": []
  }},
  "authority": {{
    "read_only_plan": true,
    "does_not_authorize": ["enqueue", "launch", "approval", "file movement", "archive", "delete", "wiki promotion", "accepted truth"]
  }}
}}
"""


def call_ollama(
    *,
    base_url: str,
    model: str,
    prompt: str,
    temperature: float,
    num_ctx: int,
    num_predict: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SystemExit(f"model call failed: {error}") from error
    text = str(raw.get("response") or "")
    return parse_json_response(text), raw


def call_command(
    *,
    command: str,
    prompt: str,
    out_dir: pathlib.Path,
    turn_name: str,
    timeout_sec: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    command_dir = out_dir / "command"
    command_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = command_dir / f"{turn_name}_prompt.txt"
    response_path = command_dir / f"{turn_name}_response.txt"
    prompt_path.write_text(prompt, encoding="utf-8")
    formatted_command = command.format(
        prompt_path=str(prompt_path),
        response_path=str(response_path),
        turn=turn_name,
    )
    env = dict(os.environ)
    env.update(
        {
            "OFFDESK_PLAN_PROMPT_PATH": str(prompt_path),
            "OFFDESK_PLAN_RESPONSE_PATH": str(response_path),
            "OFFDESK_PLAN_TURN": turn_name,
        }
    )
    try:
        completed = subprocess.run(
            shlex.split(formatted_command),
            cwd=pathlib.Path(__file__).resolve().parents[1],
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_sec,
            env=env,
        )
    except (subprocess.SubprocessError, OSError) as error:
        raise SystemExit(f"command provider failed for {turn_name}: {error}") from error
    response_text = response_path.read_text(encoding="utf-8") if response_path.exists() else completed.stdout
    invocation = {
        "provider": "command",
        "turn": turn_name,
        "command": formatted_command,
        "returncode": completed.returncode,
        "prompt_path": str(prompt_path),
        "response_path": str(response_path) if response_path.exists() else None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    write_json(command_dir / f"{turn_name}_invocation.json", invocation)
    if completed.returncode != 0:
        raise SystemExit(f"command provider exited {completed.returncode} for {turn_name}")
    parsed = parse_json_response(response_text)
    if parsed.get("type") == "result" and isinstance(parsed.get("result"), str):
        parsed = parse_json_response(str(parsed["result"]))
    return parsed, invocation


def parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("model response was not a JSON object")
    return data


def mock_draft(context: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    seed_actions = [str(item.get("action")) for item in context.get("next_actions", []) if isinstance(item, dict)]
    return {
        "schema": "offdesk_multiturn_plan_draft.v1",
        "profile_key": profiles.profile_key(profile),
        "objective": str(profile.get("mock_objective") or "Turn supplied evidence into an inspectable overnight diagnostic plan."),
        "seed_actions_used": seed_actions,
        "execution_sequence": profiles.mock_steps(profile, context),
        "council_checkpoints": profiles.list_field(profile, "mock_council_checkpoints"),
        "forbidden_actions": context.get("forbidden_actions", []),
        "launch_preconditions": profiles.list_field(profile, "mock_launch_preconditions"),
        "open_questions": profiles.list_field(profile, "mock_open_questions"),
    }


def mock_review(draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "offdesk_multiturn_plan_review.v1",
        "verdict": "usable",
        "blockers": [],
        "required_revisions": [
            "State explicitly that the packet is not enqueue approval.",
            "Keep ready_for_enqueue false until a separate runtime approval exists.",
        ],
        "missing_evidence": [],
        "unsafe_or_ambiguous_items": [],
        "strengths": [
            "Preserves profile-specific planning constraints.",
            "Separates executed evidence from missing evidence.",
        ],
        "go_no_go": {
            "ready_for_operator_review": bool(draft.get("execution_sequence")),
            "ready_for_enqueue": False,
            "reason": "Planning packet is reviewable but cannot authorize runtime.",
        },
    }


def mock_final(
    context: dict[str, Any],
    draft: dict[str, Any],
    review: dict[str, Any],
    source: pathlib.Path,
    profile: dict[str, Any],
) -> dict[str, Any]:
    all_stop_conditions: list[str] = []
    all_closeout_criteria: list[str] = []
    for item in draft.get("execution_sequence", []):
        if isinstance(item, dict):
            all_stop_conditions.extend(str(value) for value in item.get("stop_conditions", []))
            all_closeout_criteria.extend(str(value) for value in item.get("closeout_criteria", []))
    return {
        "schema": PLAN_SCHEMA,
        "profile_key": profiles.profile_key(profile),
        "profile_name": profile.get("profile_name"),
        "generated_at": utc_now(),
        "decision": {
            "ready_for_operator_review": True,
            "ready_for_launch_preparation": False,
            "ready_for_enqueue": False,
            "reason": "This is a strengthened Offdesk plan packet; launch preparation and enqueue still require a separate operator-approved path.",
        },
        "source_evidence_bundle": str(source),
        "current_state": context.get("current_state", {}),
        "objective": draft.get("objective"),
        "execution_sequence": draft.get("execution_sequence", []),
        "council_checkpoints": draft.get("council_checkpoints", []),
        "launch_preconditions": draft.get("launch_preconditions", []),
        "stop_conditions": sorted(set(all_stop_conditions)),
        "closeout_criteria": sorted(set(all_closeout_criteria)),
        "forbidden_actions": draft.get("forbidden_actions", []),
        "open_questions": draft.get("open_questions", []),
        "review_trace": {
            "draft_schema": draft.get("schema"),
            "review_verdict": review.get("verdict"),
            "required_revisions_addressed": review.get("required_revisions", []),
        },
        "authority": {
            "read_only_plan": True,
            "does_not_authorize": [
                "enqueue",
                "launch",
                "approval",
                "file movement",
                "archive",
                "delete",
                "wiki promotion",
                "accepted truth",
            ],
        },
    }


def validate_final(packet: dict[str, Any], profile: dict[str, Any] | None = None) -> list[str]:
    profile = profile or profiles.GENERIC_PROFILE
    failures: list[str] = []
    if packet.get("schema") != PLAN_SCHEMA:
        failures.append("schema_mismatch")
    if packet.get("profile_key") != profiles.profile_key(profile):
        failures.append("profile_key_mismatch")
    decision = packet.get("decision") if isinstance(packet.get("decision"), dict) else {}
    if decision.get("ready_for_enqueue") is not False:
        failures.append("ready_for_enqueue_must_be_false")
    if decision.get("ready_for_launch_preparation") is not False:
        failures.append("ready_for_launch_preparation_must_be_false")
    if not packet.get("execution_sequence"):
        failures.append("execution_sequence_missing")
    authority = packet.get("authority") if isinstance(packet.get("authority"), dict) else {}
    blocked = set(str(item) for item in authority.get("does_not_authorize", []))
    for required in profiles.required_denials(profile):
        if required not in blocked:
            failures.append(f"authority_missing:{required}")
    text = json.dumps(packet, ensure_ascii=False)
    lowered = text.lower()
    for anchor in profiles.required_anchors(profile):
        if anchor.lower() not in lowered:
            failures.append(f"anchor_missing:{anchor}")
    if authority.get("read_only_plan") is not True:
        failures.append("authority_read_only_plan_must_be_true")
    if not packet.get("source_evidence_bundle"):
        failures.append("source_evidence_bundle_missing")
    if not packet.get("stop_conditions"):
        failures.append("stop_conditions_missing")
    if not packet.get("closeout_criteria"):
        failures.append("closeout_criteria_missing")
    for index, item in enumerate(packet.get("execution_sequence", [])):
        if not isinstance(item, dict):
            failures.append(f"execution_sequence[{index}]:not_object")
            continue
        for field in STEP_REQUIRED_TEXT_FIELDS:
            if not str(item.get(field) or "").strip():
                failures.append(f"execution_sequence[{index}]:{field}:missing")
        for field in STEP_REQUIRED_LIST_FIELDS:
            value = item.get(field)
            if not isinstance(value, list) or not any(str(entry).strip() for entry in value):
                failures.append(f"execution_sequence[{index}]:{field}:missing_or_empty")
        commands = item.get("allowed_commands")
        if not isinstance(commands, list):
            failures.append(f"execution_sequence[{index}]:allowed_commands:not_list")
            commands = []
        for command in item.get("allowed_commands", []):
            command_text = str(command)
            if "\n" in command_text or "#" in command_text or "```" in command_text:
                failures.append(f"forbidden_allowed_command:comment_or_multiline:{index}")
            if command_text.strip() != command_text:
                failures.append(f"forbidden_allowed_command:surrounding_whitespace:{index}")
            if not profiles.command_allowed(profile, command_text):
                failures.append(f"forbidden_allowed_command:not_plan_only:{index}")
            for fragment in profiles.list_field(profile, "forbidden_command_fragments"):
                if fragment in f" {command_text}":
                    failures.append(f"forbidden_allowed_command:fragment:{fragment.strip()}")
    return failures


def write_markdown(path: pathlib.Path, packet: dict[str, Any], validation_failures: list[str]) -> None:
    lines = [
        "# Offdesk Multiturn Plan",
        "",
        f"- schema: `{packet.get('schema')}`",
        f"- profile_key: `{packet.get('profile_key')}`",
        f"- generated_at: `{packet.get('generated_at')}`",
        f"- source_evidence_bundle: `{packet.get('source_evidence_bundle')}`",
        f"- ready_for_operator_review: `{(packet.get('decision') or {}).get('ready_for_operator_review')}`",
        f"- ready_for_launch_preparation: `{(packet.get('decision') or {}).get('ready_for_launch_preparation')}`",
        f"- ready_for_enqueue: `{(packet.get('decision') or {}).get('ready_for_enqueue')}`",
        f"- validation_failures: `{validation_failures}`",
        "",
        "## Objective",
        "",
        str(packet.get("objective") or ""),
        "",
        "## Current State",
        "",
        "```json",
        json.dumps(packet.get("current_state", {}), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Execution Sequence",
        "",
    ]
    for item in packet.get("execution_sequence", []):
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"### {item.get('id')}",
                "",
                f"- agent_mode: `{item.get('agent_mode')}`",
                f"- purpose: {item.get('purpose')}",
                "- allowed_commands:",
            ]
        )
        for command in item.get("allowed_commands", []):
            lines.append(f"  - `{command}`")
        lines.append("- expected_artifacts:")
        for artifact in item.get("expected_artifacts", []):
            lines.append(f"  - {artifact}")
        lines.append("- stop_conditions:")
        for condition in item.get("stop_conditions", []):
            lines.append(f"  - {condition}")
        lines.append("- closeout_criteria:")
        for criterion in item.get("closeout_criteria", []):
            lines.append(f"  - {criterion}")
        lines.append("")
    lines.extend(["## Council Checkpoints", ""])
    for checkpoint in packet.get("council_checkpoints", []):
        lines.append(f"- {checkpoint}")
    lines.extend(["", "## Authority", "", "This packet is read-only and does not authorize runtime launch."])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_turns(
    args: argparse.Namespace,
    context: dict[str, Any],
    source_path: pathlib.Path,
    out_dir: pathlib.Path,
    profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    raw_turns: list[dict[str, Any]] = []
    if args.mock:
        draft = mock_draft(context, profile)
        review = mock_review(draft)
        final = mock_final(context, draft, review, source_path, profile)
        return draft, review, final, raw_turns

    prompts = out_dir / "prompts"
    responses = out_dir / "responses"
    prompts.mkdir(parents=True, exist_ok=True)
    responses.mkdir(parents=True, exist_ok=True)

    turn1_prompt = draft_prompt(context, profile)
    (prompts / "turn1_draft.txt").write_text(turn1_prompt, encoding="utf-8")
    if args.provider == "command":
        if not args.command:
            raise SystemExit("--command is required when --provider command")
        draft, raw = call_command(
            command=args.command,
            prompt=turn1_prompt,
            out_dir=out_dir,
            turn_name="turn1_draft",
            timeout_sec=args.command_timeout_sec,
        )
    else:
        draft, raw = call_ollama(
            base_url=args.base_url,
            model=args.model,
            prompt=turn1_prompt,
            temperature=args.temperature,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
        )
    raw_turns.append(raw)
    write_json(responses / "turn1_draft.json", draft)

    turn2_prompt = review_prompt(context, draft, profile)
    (prompts / "turn2_review.txt").write_text(turn2_prompt, encoding="utf-8")
    if args.provider == "command":
        review, raw = call_command(
            command=args.command,
            prompt=turn2_prompt,
            out_dir=out_dir,
            turn_name="turn2_review",
            timeout_sec=args.command_timeout_sec,
        )
    else:
        review, raw = call_ollama(
            base_url=args.base_url,
            model=args.model,
            prompt=turn2_prompt,
            temperature=args.temperature,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
        )
    raw_turns.append(raw)
    write_json(responses / "turn2_review.json", review)

    turn3_prompt = revision_prompt(context, draft, review, profile)
    (prompts / "turn3_revision.txt").write_text(turn3_prompt, encoding="utf-8")
    if args.provider == "command":
        final, raw = call_command(
            command=args.command,
            prompt=turn3_prompt,
            out_dir=out_dir,
            turn_name="turn3_revision",
            timeout_sec=args.command_timeout_sec,
        )
    else:
        final, raw = call_ollama(
            base_url=args.base_url,
            model=args.model,
            prompt=turn3_prompt,
            temperature=args.temperature,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
        )
    raw_turns.append(raw)
    write_json(responses / "turn3_revision.json", final)
    return draft, review, final, raw_turns


def main() -> int:
    args = parse_args()
    source_path = args.evidence_bundle.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = profiles.load_profile(args.profile)

    bundle = read_json(source_path)
    context = plan_context(bundle, source_path, profile)
    write_json(out_dir / "plan_profile.json", profile)
    write_json(out_dir / "plan_context.json", context)

    draft, review, final, raw_turns = run_turns(args, context, source_path, out_dir, profile)
    final["source_evidence_bundle"] = str(source_path)
    final["profile_key"] = profiles.profile_key(profile)
    final.setdefault("profile_name", profile.get("profile_name"))
    if args.mock:
        write_json(out_dir / "responses" / "turn1_draft.json", draft)
        write_json(out_dir / "responses" / "turn2_review.json", review)
        write_json(out_dir / "responses" / "turn3_revision.json", final)

    validation_failures = validate_final(final, profile)
    result = {
        "schema": "offdesk_multiturn_plan_pipeline_result.v1",
        "generated_at": utc_now(),
        "status": "passed" if not validation_failures else "failed",
        "profile_key": profiles.profile_key(profile),
        "profile_name": profile.get("profile_name"),
        "provider": "mock" if args.mock else args.provider,
        "model": args.model,
        "command_label": args.command_label,
        "mock": args.mock,
        "source_evidence_bundle": str(source_path),
        "out_dir": str(out_dir),
        "turns": [
            {"name": "draft", "schema": draft.get("schema")},
            {"name": "review", "schema": review.get("schema"), "verdict": review.get("verdict")},
            {"name": "revision", "schema": final.get("schema")},
        ],
        "validation_failures": validation_failures,
        "artifacts": {
            "profile": str(out_dir / "plan_profile.json"),
            "context": str(out_dir / "plan_context.json"),
            "draft": str(out_dir / "responses" / "turn1_draft.json"),
            "review": str(out_dir / "responses" / "turn2_review.json"),
            "final": str(out_dir / "OVERNIGHT_PLAN.json"),
            "markdown": str(out_dir / "OVERNIGHT_PLAN.md"),
        },
        "raw_turn_count": len(raw_turns),
    }
    write_json(out_dir / "OVERNIGHT_PLAN.json", final)
    write_markdown(out_dir / "OVERNIGHT_PLAN.md", final, validation_failures)
    write_json(out_dir / "pipeline_result.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not validation_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
