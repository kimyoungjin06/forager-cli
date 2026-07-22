#!/usr/bin/env python3
"""Build an Offdesk plan through a planner council."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shlex
import subprocess
import sys
from typing import Any


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[0]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_offdesk_multiturn_plan as plan_builder
import offdesk_plan_profile as profiles


COUNCIL_SCHEMA = "offdesk_planner_council.v1"
COUNCIL_TRACE_SCHEMA = "offdesk_planner_council_trace.v1"
REVIEWERS = ("gpt", "claude", "qwen")
VALID_REVIEW_DECISIONS = {"usable", "revise", "block"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-bundle", type=pathlib.Path, required=True)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    parser.add_argument(
        "--profile",
        type=pathlib.Path,
        help="Optional plan profile JSON containing domain-specific anchors, allowed commands, and prompt rules.",
    )
    parser.add_argument(
        "--mode",
        choices=("prompt-package", "mock", "command"),
        default=os.environ.get("OFFDESK_PLANNER_COUNCIL_MODE", "prompt-package"),
    )
    parser.add_argument("--gpt-command", default=os.environ.get("OFFDESK_PLANNER_COUNCIL_GPT_CMD"))
    parser.add_argument("--claude-command", default=os.environ.get("OFFDESK_PLANNER_COUNCIL_CLAUDE_CMD"))
    parser.add_argument("--qwen-command", default=os.environ.get("OFFDESK_PLANNER_COUNCIL_QWEN_CMD"))
    parser.add_argument("--command-timeout-sec", type=int, default=900)
    parser.add_argument("--max-context-chars", type=int, default=14000)
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def compact(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...TRUNCATED..."


def planner_command(args: argparse.Namespace, reviewer: str) -> str | None:
    return {
        "gpt": args.gpt_command,
        "claude": args.claude_command,
        "qwen": args.qwen_command,
    }[reviewer]


def parse_json_response(text: str) -> dict[str, Any]:
    return plan_builder.parse_json_response(text)


def prompt_step_examples(profile: dict[str, Any]) -> list[dict[str, Any]]:
    examples = profiles.mock_steps(profile, {})
    if examples:
        return examples[:3]
    return [
        {
            "id": "inspect_current_state",
            "agent_mode": "analysis",
            "purpose": "...",
            "allowed_reads": ["..."],
            "allowed_commands": [],
            "expected_artifacts": ["..."],
            "stop_conditions": ["..."],
            "closeout_criteria": ["..."],
            "evidence_refs": ["..."],
        }
    ]


def planner_prompt(reviewer: str, context: dict[str, Any], profile: dict[str, Any], max_chars: int) -> str:
    return f"""{plan_builder.common_system_contract(profile)}

You are the {reviewer.upper()} member of an Offdesk Planner Council.
Work independently. Produce one candidate overnight plan from the evidence.

Council-specific rules:
- Return the final plan schema directly: {plan_builder.PLAN_SCHEMA}.
- Every execution_sequence item must include non-empty allowed_reads,
  expected_artifacts, stop_conditions, closeout_criteria, and evidence_refs.
- {profiles.command_policy_text(profile)}
- Do not claim the plan authorizes launch or enqueue.
- Preserve dissent-worthy uncertainty in open_questions.

Context:
{compact(context, max_chars)}

Return JSON only with this top-level schema:
{{
  "schema": "{plan_builder.PLAN_SCHEMA}",
  "profile_key": "{profiles.profile_key(profile)}",
  "generated_at": "{utc_now()}",
  "decision": {{
    "ready_for_operator_review": true,
    "ready_for_launch_preparation": false,
    "ready_for_enqueue": false,
    "reason": "planning artifact only"
  }},
  "source_evidence_bundle": "{context.get('source_evidence_bundle')}",
  "current_state": {{}},
  "objective": "...",
  "execution_sequence": {json.dumps(prompt_step_examples(profile), ensure_ascii=False, indent=2)},
  "council_checkpoints": ["..."],
  "launch_preconditions": ["..."],
  "stop_conditions": ["..."],
  "closeout_criteria": ["..."],
  "forbidden_actions": ["..."],
  "open_questions": ["..."],
  "review_trace": {{"planner": "{reviewer}"}},
  "authority": {{
    "read_only_plan": true,
    "does_not_authorize": ["enqueue", "launch", "approval", "file movement", "archive", "delete", "wiki promotion", "accepted truth"]
  }}
}}
"""


def cross_review_prompt(
    reviewer: str,
    context: dict[str, Any],
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    max_chars: int,
) -> str:
    summaries = []
    for candidate in candidates:
        plan = candidate.get("plan") if isinstance(candidate.get("plan"), dict) else {}
        summaries.append(
            {
                "planner": candidate.get("planner"),
                "ready": candidate.get("ready"),
                "validation_failures": candidate.get("validation_failures", []),
                "objective": plan.get("objective"),
                "execution_ids": [
                    item.get("id")
                    for item in plan.get("execution_sequence", [])
                    if isinstance(item, dict)
                ],
                "open_questions": plan.get("open_questions", []),
            }
        )
    return f"""You are the {reviewer.upper()} reviewer in an Offdesk Planner Council.

Review all candidate plans. Do not rewrite them. Identify launch-blocking gaps,
missing per-step evidence gates, unsafe commands, unresolved dissent, and weak
stop/closeout criteria.

Profile contract:
{profiles.profile_prompt_block(profile)}

Context:
{compact(context, max_chars // 2)}

Candidate summaries:
{compact(summaries, max_chars)}

Return JSON only:
{{
  "reviewer": "{reviewer}",
  "decision": "usable | revise | block",
  "preferred_plan_ids": ["planner names"],
  "blockers": ["..."],
  "required_revisions": ["..."],
  "dissent": ["..."],
  "rationale": "short evidence-backed reason"
}}
"""


def revision_prompt(
    reviewer: str,
    context: dict[str, Any],
    base_plan: dict[str, Any],
    reviews: list[dict[str, Any]],
    profile: dict[str, Any],
    max_chars: int,
) -> str:
    review_payload = [
        {
            "reviewer": review.get("reviewer"),
            "decision": review.get("decision"),
            "blockers": review.get("blockers", []),
            "required_revisions": review.get("required_revisions", []),
            "dissent": review.get("dissent", []),
            "rationale": review.get("rationale", ""),
        }
        for review in reviews
    ]
    revision_requirements = profiles.list_field(profile, "revision_requirements")
    revision_block = "\n".join(f"- {item}" for item in revision_requirements) or "- Preserve the profile-specific planning contract."
    return f"""{plan_builder.common_system_contract(profile)}

You are the {reviewer.upper()} selected planner revising an Offdesk Planner
Council plan after cross-review.

Revision goal:
- Produce one final operator-review plan, not a launch plan.
- Address every concrete blocker and required revision from the reviews.
- Convert genuine uncertainty into explicit stop_conditions, closeout_criteria,
  council_checkpoints, or open_questions so it cannot be mistaken for approval.

Profile-specific revision requirements:
{revision_block}

Allowed command policy:
{profiles.command_policy_text(profile)}

Context:
{compact(context, max_chars // 2)}

Base plan:
{compact(base_plan, max_chars // 2)}

Cross-review feedback:
{compact(review_payload, max_chars)}

Return JSON only with this top-level schema:
{{
  "schema": "{plan_builder.PLAN_SCHEMA}",
  "profile_key": "{profiles.profile_key(profile)}",
  "generated_at": "{utc_now()}",
  "decision": {{
    "ready_for_operator_review": true,
    "ready_for_launch_preparation": false,
    "ready_for_enqueue": false,
    "reason": "Planning artifact only; launch preparation requires separate operator approval after all blockers are resolved."
  }},
  "source_evidence_bundle": "{context.get('source_evidence_bundle')}",
  "current_state": {{}},
  "objective": "...",
  "execution_sequence": {json.dumps(prompt_step_examples(profile), ensure_ascii=False, indent=2)},
  "council_checkpoints": ["..."],
  "launch_preconditions": ["..."],
  "stop_conditions": ["..."],
  "closeout_criteria": ["..."],
  "forbidden_actions": ["..."],
  "open_questions": ["..."],
  "review_trace": {{
    "revision_planner": "{reviewer}",
    "initial_reviewer_decisions": {{}},
    "required_revisions_addressed": ["..."],
    "dissent_handling": ["..."]
  }},
  "authority": {{
    "read_only_plan": true,
    "does_not_authorize": ["enqueue", "launch", "approval", "file movement", "archive", "delete", "wiki promotion", "accepted truth"]
  }}
}}
"""


def final_review_prompt(
    reviewer: str,
    context: dict[str, Any],
    revised_plan: dict[str, Any],
    prior_reviews: list[dict[str, Any]],
    profile: dict[str, Any],
    max_chars: int,
) -> str:
    review_payload = [
        {
            "reviewer": review.get("reviewer"),
            "decision": review.get("decision"),
            "blockers": review.get("blockers", []),
            "required_revisions": review.get("required_revisions", []),
            "dissent": review.get("dissent", []),
        }
        for review in prior_reviews
    ]
    final_requirements = profiles.list_field(profile, "final_review_requirements")
    final_block = "\n".join(f"- {item}" for item in final_requirements) or "- The plan satisfies the profile-specific planning contract."
    return f"""You are the {reviewer.upper()} final reviewer in an Offdesk Planner Council.

Review the revised plan for operator-review readiness, not launch readiness.
Return decision "usable" only if:
{final_block}
If decision is "usable", blockers, required_revisions, and dissent must be
empty arrays. Put non-blocking preserved uncertainty in rationale, not dissent.

Context:
{compact(context, max_chars // 3)}

Prior cross-review feedback:
{compact(review_payload, max_chars // 2)}

Revised plan:
{compact(revised_plan, max_chars)}

Return JSON only:
{{
  "reviewer": "{reviewer}",
  "decision": "usable | revise | block",
  "preferred_plan_ids": ["revised"],
  "blockers": [],
  "required_revisions": [],
  "dissent": [],
  "rationale": "short evidence-backed reason"
}}
"""


def run_command(
    *,
    command: str,
    prompt: str,
    out_dir: pathlib.Path,
    name: str,
    timeout_sec: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    prompt_path = out_dir / f"{name}_prompt.txt"
    response_path = out_dir / f"{name}_response.txt"
    write_text(prompt_path, prompt)
    formatted = command.format(
        prompt_path=str(prompt_path),
        response_path=str(response_path),
        turn=name,
    )
    env = dict(os.environ)
    env.update(
        {
            "OFFDESK_PLANNER_COUNCIL_PROMPT_PATH": str(prompt_path),
            "OFFDESK_PLANNER_COUNCIL_RESPONSE_PATH": str(response_path),
            "OFFDESK_PLANNER_COUNCIL_TURN": name,
        }
    )
    try:
        completed = subprocess.run(
            shlex.split(formatted),
            cwd=REPO_ROOT,
            input=prompt,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_sec,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return None, {"command": formatted, "error": repr(error), "prompt_path": str(prompt_path)}
    response_text = response_path.read_text(encoding="utf-8") if response_path.exists() else completed.stdout
    invocation = {
        "command": formatted,
        "returncode": completed.returncode,
        "prompt_path": str(prompt_path),
        "response_path": str(response_path) if response_path.exists() else None,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if completed.returncode != 0:
        return None, invocation
    try:
        parsed = parse_json_response(response_text)
    except (json.JSONDecodeError, ValueError) as error:
        invocation["parse_error"] = repr(error)
        return None, invocation
    return parsed, invocation


def mock_candidate(reviewer: str, context: dict[str, Any], source: pathlib.Path, profile: dict[str, Any]) -> dict[str, Any]:
    draft = plan_builder.mock_draft(context, profile)
    draft["objective"] = f"{reviewer.upper()} council candidate: {draft['objective']}"
    review = plan_builder.mock_review(draft)
    final = plan_builder.mock_final(context, draft, review, source, profile)
    final["review_trace"]["planner"] = reviewer
    return final


def build_candidates(
    args: argparse.Namespace,
    context: dict[str, Any],
    source: pathlib.Path,
    profile: dict[str, Any],
    out_dir: pathlib.Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    invocations: list[dict[str, Any]] = []
    prompt_dir = out_dir / "planner_prompts"
    response_dir = out_dir / "planner_responses"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    for reviewer in REVIEWERS:
        prompt = planner_prompt(reviewer, context, profile, args.max_context_chars)
        write_text(prompt_dir / f"{reviewer}_planner.txt", prompt)
        plan: dict[str, Any] | None
        invocation: dict[str, Any] = {}
        if args.mode == "mock":
            plan = mock_candidate(reviewer, context, source, profile)
        elif args.mode == "prompt-package":
            plan = None
            invocation = {
                "mode": "prompt-package",
                "prompt_path": str(prompt_dir / f"{reviewer}_planner.txt"),
                "ready": False,
            }
        else:
            command = planner_command(args, reviewer)
            if not command:
                plan = None
                invocation = {"mode": "command", "ready": False, "error": f"{reviewer}_command_missing"}
            else:
                plan, invocation = run_command(
                    command=command,
                    prompt=prompt,
                    out_dir=response_dir,
                    name=f"{reviewer}_planner",
                    timeout_sec=args.command_timeout_sec,
                )
        validation_failures = plan_builder.validate_final(plan, profile) if isinstance(plan, dict) else ["planner_not_executed"]
        candidate = {
            "planner": reviewer,
            "ready": isinstance(plan, dict) and not validation_failures,
            "plan_path": str(response_dir / f"{reviewer}_plan.json"),
            "validation_failures": validation_failures,
            "plan": plan,
            "invocation": invocation,
        }
        if isinstance(plan, dict):
            write_json(response_dir / f"{reviewer}_plan.json", plan)
        candidates.append(candidate)
        invocations.append({"planner": reviewer, **invocation})
    return candidates, invocations


def normalize_review(review: dict[str, Any] | None, reviewer: str) -> dict[str, Any]:
    if not isinstance(review, dict):
        return {
            "reviewer": reviewer,
            "ready": False,
            "decision": "block",
            "preferred_plan_ids": [],
            "blockers": ["review_not_executed"],
            "required_revisions": [],
            "dissent": [],
            "rationale": "Reviewer did not return JSON.",
        }
    decision = str(review.get("decision") or "block").strip().replace(" ", "_")
    if decision not in VALID_REVIEW_DECISIONS:
        decision = "block"
    for key in ("preferred_plan_ids", "blockers", "required_revisions", "dissent"):
        if not isinstance(review.get(key), list):
            review[key] = []
    review["reviewer"] = reviewer
    review["ready"] = True
    review["decision"] = decision
    review["preferred_plan_ids"] = [str(item) for item in review["preferred_plan_ids"]]
    review["blockers"] = [str(item) for item in review["blockers"]]
    review["required_revisions"] = [str(item) for item in review["required_revisions"]]
    review["dissent"] = [str(item) for item in review["dissent"]]
    review["rationale"] = str(review.get("rationale") or "")
    return review


def build_cross_reviews(
    args: argparse.Namespace,
    context: dict[str, Any],
    candidates: list[dict[str, Any]],
    profile: dict[str, Any],
    out_dir: pathlib.Path,
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    prompt_dir = out_dir / "review_prompts"
    response_dir = out_dir / "review_responses"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    for reviewer in REVIEWERS:
        prompt = cross_review_prompt(reviewer, context, candidates, profile, args.max_context_chars)
        write_text(prompt_dir / f"{reviewer}_cross_review.txt", prompt)
        if args.mode == "mock":
            ready = [candidate["planner"] for candidate in candidates if candidate.get("ready")]
            review = {
                "reviewer": reviewer,
                "decision": "usable" if ready else "block",
                "preferred_plan_ids": ready[:1],
                "blockers": [] if ready else ["no_valid_candidate_plan"],
                "required_revisions": [],
                "dissent": [],
                "rationale": "Deterministic mock review of structurally valid council candidates.",
            }
        elif args.mode == "prompt-package":
            review = {
                "reviewer": reviewer,
                "ready": False,
                "decision": "block",
                "preferred_plan_ids": [],
                "blockers": ["reviewer_prompt_not_executed"],
                "required_revisions": [],
                "dissent": [],
                "rationale": "Prompt package was generated but no reviewer command was run.",
                "prompt_path": str(prompt_dir / f"{reviewer}_cross_review.txt"),
            }
        else:
            command = planner_command(args, reviewer)
            if not command:
                review = None
            else:
                parsed, invocation = run_command(
                    command=command,
                    prompt=prompt,
                    out_dir=response_dir,
                    name=f"{reviewer}_cross_review",
                    timeout_sec=args.command_timeout_sec,
                )
                write_json(response_dir / f"{reviewer}_cross_review_invocation.json", invocation)
                review = parsed
        normalized = normalize_review(review, reviewer)
        write_json(response_dir / f"{reviewer}_cross_review.json", normalized)
        reviews.append(normalized)
    return reviews


def choose_candidate(candidates: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [candidate for candidate in candidates if candidate.get("ready") and isinstance(candidate.get("plan"), dict)]
    if not valid:
        return None
    votes: dict[str, int] = {candidate["planner"]: 0 for candidate in valid}
    for review in reviews:
        for planner in review.get("preferred_plan_ids", []):
            if planner in votes:
                votes[planner] += 1
    return sorted(valid, key=lambda candidate: (-votes[candidate["planner"]], candidate["planner"]))[0]


def synthesize_plan(
    *,
    source: pathlib.Path,
    candidates: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    mode: str,
) -> dict[str, Any] | None:
    if selected is None:
        return None
    plan = json.loads(json.dumps(selected["plan"], ensure_ascii=False))
    plan["source_evidence_bundle"] = str(source)
    plan["generated_at"] = utc_now()
    trace = plan.get("review_trace") if isinstance(plan.get("review_trace"), dict) else {}
    trace.update(
        {
            "planner_council_schema": COUNCIL_SCHEMA,
            "mode": mode,
            "selected_planner": selected["planner"],
            "candidate_count": len(candidates),
            "ready_candidate_count": sum(1 for candidate in candidates if candidate.get("ready")),
            "reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
            "unresolved_dissent": [
                item
                for review in reviews
                for item in review.get("dissent", [])
            ],
        }
    )
    plan["review_trace"] = trace
    plan["planner_council"] = {
        "schema": COUNCIL_TRACE_SCHEMA,
        "mode": mode,
        "selected_planner": selected["planner"],
        "candidate_planners": [candidate["planner"] for candidate in candidates],
        "reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
        "dissent": trace["unresolved_dissent"],
    }
    return plan


def apply_council_trace(
    *,
    plan: dict[str, Any],
    source: pathlib.Path,
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    mode: str,
    revision_planner: str,
) -> dict[str, Any]:
    plan["source_evidence_bundle"] = str(source)
    plan["generated_at"] = utc_now()
    trace = plan.get("review_trace") if isinstance(plan.get("review_trace"), dict) else {}
    trace.update(
        {
            "planner_council_schema": COUNCIL_SCHEMA,
            "mode": mode,
            "selected_planner": selected["planner"],
            "revision_planner": revision_planner,
            "candidate_count": len(candidates),
            "ready_candidate_count": sum(1 for candidate in candidates if candidate.get("ready")),
            "initial_reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
            "initial_blocker_count": sum(len(review.get("blockers", [])) for review in reviews),
            "initial_dissent_count": sum(len(review.get("dissent", [])) for review in reviews),
        }
    )
    plan["review_trace"] = trace
    plan["planner_council"] = {
        "schema": COUNCIL_TRACE_SCHEMA,
        "mode": mode,
        "selected_planner": selected["planner"],
        "revision_planner": revision_planner,
        "candidate_planners": [candidate["planner"] for candidate in candidates],
        "initial_reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
    }
    return plan


def revise_plan(
    *,
    args: argparse.Namespace,
    context: dict[str, Any],
    source: pathlib.Path,
    candidates: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    base_plan: dict[str, Any] | None,
    profile: dict[str, Any],
    out_dir: pathlib.Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if selected is None or not isinstance(base_plan, dict):
        return None, {"ready": False, "error": "base_plan_missing"}
    revision_planner = str(selected["planner"])
    prompt = revision_prompt(revision_planner, context, base_plan, reviews, profile, args.max_context_chars)
    prompt_dir = out_dir / "revision_prompts"
    response_dir = out_dir / "revision_responses"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    write_text(prompt_dir / f"{revision_planner}_revision.txt", prompt)

    if args.mode == "mock":
        revised = json.loads(json.dumps(base_plan, ensure_ascii=False))
        trace = revised.get("review_trace") if isinstance(revised.get("review_trace"), dict) else {}
        trace["required_revisions_addressed"] = [
            item
            for review in reviews
            for item in review.get("required_revisions", [])
        ]
        trace["dissent_handling"] = [
            item
            for review in reviews
            for item in review.get("dissent", [])
        ]
        revised["review_trace"] = trace
        invocation = {"mode": "mock", "ready": True, "prompt_path": str(prompt_dir / f"{revision_planner}_revision.txt")}
    elif args.mode == "prompt-package":
        return None, {
            "mode": "prompt-package",
            "ready": False,
            "prompt_path": str(prompt_dir / f"{revision_planner}_revision.txt"),
            "error": "revision_prompt_not_executed",
        }
    else:
        command = planner_command(args, revision_planner)
        if not command:
            return None, {"mode": "command", "ready": False, "error": f"{revision_planner}_command_missing"}
        revised, invocation = run_command(
            command=command,
            prompt=prompt,
            out_dir=response_dir,
            name=f"{revision_planner}_revision",
            timeout_sec=args.command_timeout_sec,
        )
        write_json(response_dir / f"{revision_planner}_revision_invocation.json", invocation)
        if not isinstance(revised, dict):
            return None, invocation

    revised = apply_council_trace(
        plan=revised,
        source=source,
        selected=selected,
        candidates=candidates,
        reviews=reviews,
        mode=args.mode,
        revision_planner=revision_planner,
    )
    write_json(response_dir / f"{revision_planner}_revision_plan.json", revised)
    return revised, invocation


def build_final_reviews(
    args: argparse.Namespace,
    context: dict[str, Any],
    revised_plan: dict[str, Any] | None,
    prior_reviews: list[dict[str, Any]],
    profile: dict[str, Any],
    out_dir: pathlib.Path,
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    prompt_dir = out_dir / "final_review_prompts"
    response_dir = out_dir / "final_review_responses"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    for reviewer in REVIEWERS:
        if not isinstance(revised_plan, dict):
            normalized = normalize_review(None, reviewer)
            write_json(response_dir / f"{reviewer}_final_review.json", normalized)
            reviews.append(normalized)
            continue
        prompt = final_review_prompt(reviewer, context, revised_plan, prior_reviews, profile, args.max_context_chars)
        write_text(prompt_dir / f"{reviewer}_final_review.txt", prompt)
        if args.mode == "mock":
            review = {
                "reviewer": reviewer,
                "decision": "usable",
                "preferred_plan_ids": ["revised"],
                "blockers": [],
                "required_revisions": [],
                "dissent": [],
                "rationale": "Deterministic mock final review of the revised plan.",
            }
        elif args.mode == "prompt-package":
            review = {
                "reviewer": reviewer,
                "ready": False,
                "decision": "block",
                "preferred_plan_ids": [],
                "blockers": ["final_review_prompt_not_executed"],
                "required_revisions": [],
                "dissent": [],
                "rationale": "Prompt package was generated but no final reviewer command was run.",
                "prompt_path": str(prompt_dir / f"{reviewer}_final_review.txt"),
            }
        else:
            command = planner_command(args, reviewer)
            if not command:
                review = None
            else:
                parsed, invocation = run_command(
                    command=command,
                    prompt=prompt,
                    out_dir=response_dir,
                    name=f"{reviewer}_final_review",
                    timeout_sec=args.command_timeout_sec,
                )
                write_json(response_dir / f"{reviewer}_final_review_invocation.json", invocation)
                review = parsed
        normalized = normalize_review(review, reviewer)
        write_json(response_dir / f"{reviewer}_final_review.json", normalized)
        reviews.append(normalized)
    return reviews


def consensus(
    candidates: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    validation_failures: list[str],
) -> dict[str, Any]:
    missing_reviews = [review["reviewer"] for review in reviews if review.get("ready") is not True]
    review_decisions = [str(review.get("decision")) for review in reviews]
    selected_validation_failures = selected.get("validation_failures", []) if selected else ["no_selected_candidate"]
    blockers = sorted(
        {
            str(item)
            for item in selected_validation_failures
            if item
        }
        | {
            str(item)
            for review in reviews
            for item in review.get("blockers", [])
            if item
        }
        | set(validation_failures)
    )
    recorded_dissent = sorted({str(item) for review in reviews for item in review.get("dissent", []) if item})
    blocking_dissent = sorted(
        {
            str(item)
            for review in reviews
            if review.get("decision") != "usable"
            for item in review.get("dissent", [])
            if item
        }
    )
    ready = (
        selected is not None
        and not missing_reviews
        and not blockers
        and not blocking_dissent
        and set(review_decisions) == {"usable"}
    )
    return {
        "decision": "ready_for_operator_review" if ready else "needs_revision_or_execution",
        "agreement": ready,
        "ready_for_operator_review": ready,
        "ready_for_launch_preparation": False,
        "ready_for_enqueue": False,
        "selected_planner": selected.get("planner") if selected else None,
        "reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
        "missing_reviewers": missing_reviews,
        "blockers": blockers,
        "dissent": blocking_dissent,
        "recorded_dissent": recorded_dissent,
        "reason": (
            "All planner council members produced usable final reviews and no blocking dissent remains."
            if ready
            else "Council plan requires further execution or revision before launch preparation."
        ),
    }


def public_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    plan = candidate.get("plan") if isinstance(candidate.get("plan"), dict) else {}
    return {
        "planner": candidate.get("planner"),
        "ready": candidate.get("ready"),
        "plan_path": candidate.get("plan_path"),
        "validation_failures": candidate.get("validation_failures", []),
        "objective": plan.get("objective"),
        "execution_ids": [
            item.get("id")
            for item in plan.get("execution_sequence", [])
            if isinstance(item, dict)
        ],
    }


def write_markdown(path: pathlib.Path, artifact: dict[str, Any]) -> None:
    consensus_block = artifact["consensus"]
    lines = [
        "# Offdesk Planner Council",
        "",
        f"- schema: `{artifact['schema']}`",
        f"- created_at: `{artifact['created_at']}`",
        f"- mode: `{artifact['mode']}`",
        f"- source_evidence_bundle: `{artifact['source_evidence_bundle']}`",
        f"- decision: `{consensus_block['decision']}`",
        f"- agreement: `{consensus_block['agreement']}`",
        f"- ready_for_operator_review: `{consensus_block['ready_for_operator_review']}`",
        f"- ready_for_launch_preparation: `{consensus_block['ready_for_launch_preparation']}`",
        f"- ready_for_enqueue: `{consensus_block['ready_for_enqueue']}`",
        f"- selected_planner: `{consensus_block.get('selected_planner')}`",
        f"- validation_failures: `{artifact.get('validation_failures', [])}`",
        "",
        "## Candidates",
        "",
    ]
    for candidate in artifact["candidates"]:
        lines.extend(
            [
                f"### {candidate['planner']}",
                "",
                f"- ready: `{candidate['ready']}`",
                f"- validation_failures: `{candidate.get('validation_failures', [])}`",
                f"- objective: {candidate.get('objective')}",
                f"- execution_ids: `{candidate.get('execution_ids', [])}`",
                "",
            ]
        )
    lines.extend(["## Cross Reviews", ""])
    for review in artifact["cross_reviews"]:
        lines.extend(
            [
                f"### {review['reviewer']}",
                "",
                f"- ready: `{review.get('ready')}`",
                f"- decision: `{review.get('decision')}`",
                f"- blockers: `{review.get('blockers', [])}`",
                f"- dissent: `{review.get('dissent', [])}`",
                f"- rationale: {review.get('rationale', '')}",
                "",
            ]
        )
    if artifact.get("final_reviews"):
        lines.extend(["## Final Reviews", ""])
        for review in artifact["final_reviews"]:
            lines.extend(
                [
                    f"### {review['reviewer']}",
                    "",
                    f"- ready: `{review.get('ready')}`",
                    f"- decision: `{review.get('decision')}`",
                    f"- blockers: `{review.get('blockers', [])}`",
                    f"- dissent: `{review.get('dissent', [])}`",
                    f"- rationale: {review.get('rationale', '')}",
                    "",
                ]
            )
    if artifact.get("synthesized_plan_path"):
        lines.extend(
            [
                "## Synthesized Plan",
                "",
                f"- path: `{artifact['synthesized_plan_path']}`",
                "",
            ]
        )
    write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    source = args.evidence_bundle.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = profiles.load_profile(args.profile)

    bundle = plan_builder.read_json(source)
    context = plan_builder.plan_context(bundle, source, profile)
    write_json(out_dir / "planner_profile.json", profile)
    write_json(out_dir / "council_context.json", context)

    candidates, invocations = build_candidates(args, context, source, profile, out_dir)
    reviews = build_cross_reviews(args, context, candidates, profile, out_dir)
    selected = choose_candidate(candidates, reviews)
    base_plan = synthesize_plan(
        source=source,
        candidates=candidates,
        reviews=reviews,
        selected=selected,
        mode=args.mode,
    )
    synthesized, revision_invocation = revise_plan(
        args=args,
        context=context,
        source=source,
        candidates=candidates,
        reviews=reviews,
        selected=selected,
        base_plan=base_plan,
        profile=profile,
        out_dir=out_dir,
    )
    final_reviews = build_final_reviews(args, context, synthesized, reviews, profile, out_dir)
    validation_failures = (
        plan_builder.validate_final(synthesized, profile) if isinstance(synthesized, dict) else ["synthesized_plan_missing"]
    )
    consensus_block = consensus(candidates, final_reviews, selected, validation_failures)
    if consensus_block["dissent"] and "council_dissent_unresolved" not in validation_failures:
        validation_failures.append("council_dissent_unresolved")
        consensus_block = consensus(candidates, final_reviews, selected, validation_failures)
    if consensus_block["missing_reviewers"] and "council_reviewers_missing" not in validation_failures:
        validation_failures.append("council_reviewers_missing")
        consensus_block = consensus(candidates, final_reviews, selected, validation_failures)

    synthesized_path = out_dir / "COUNCIL_PLAN.json"
    synthesized_md_path = out_dir / "COUNCIL_PLAN.md"
    if isinstance(synthesized, dict):
        write_json(synthesized_path, synthesized)
        plan_builder.write_markdown(synthesized_md_path, synthesized, validation_failures)

    artifact = {
        "schema": COUNCIL_SCHEMA,
        "created_at": utc_now(),
        "mode": args.mode,
        "profile_key": profiles.profile_key(profile),
        "profile_name": profile.get("profile_name"),
        "source_evidence_bundle": str(source),
        "profile_path": profile.get("profile_path"),
        "context_path": str(out_dir / "council_context.json"),
        "candidates": [public_candidate_summary(candidate) for candidate in candidates],
        "cross_reviews": reviews,
        "revision_invocation": revision_invocation,
        "final_reviews": final_reviews,
        "consensus": consensus_block,
        "validation_failures": validation_failures,
        "synthesized_plan_path": str(synthesized_path) if isinstance(synthesized, dict) else None,
        "synthesized_plan_markdown_path": str(synthesized_md_path) if isinstance(synthesized, dict) else None,
        "invocations": invocations,
    }
    write_json(out_dir / "planner_council_result.json", artifact)
    write_markdown(out_dir / "PLANNER_COUNCIL.md", artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if consensus_block["ready_for_operator_review"] and not validation_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
