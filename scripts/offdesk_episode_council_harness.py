#!/usr/bin/env python3
"""Run a GPT/Claude-style council review between Offdesk episodes.

The harness is intentionally adapter-based. In `prompt-package` mode it writes
the exact reviewer prompts without calling a model. In `mock` mode it produces
deterministic reviewer JSON for smoke tests. In `command` mode it sends the
prompt on stdin to externally configured reviewer commands and expects each
reviewer to return JSON.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REVIEWERS = ("gpt", "claude")
DECISION_ORDER = (
    "needs_council_execution",
    "needs_approval",
    "block",
    "handoff",
    "pivot",
    "revise",
    "continue",
)
VALID_DECISIONS = set(DECISION_ORDER)
VALID_WIKI_CANDIDATE_DECISIONS = {
    "trial_promote",
    "needs_more_evidence",
    "merge_with_existing",
    "rescope",
    "reject",
    "no_change",
}
SYSTEM_MUTATION_MARKERS = (
    "delete",
    "remove files",
    "cleanup",
    "rm -",
    "reboot",
    "shutdown",
    "restart service",
    "systemctl",
    "mount",
    "umount",
    "raid",
    "nvme",
    "chmod",
    "chown",
    "kill -9",
    "pkill",
    "firewall",
    "driver",
    "firmware",
    "bios",
)


class CouncilFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-record", type=pathlib.Path)
    parser.add_argument("--campaign-state", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument(
        "--mode",
        choices=("prompt-package", "mock", "command"),
        default=os.environ.get("OFFDESK_COUNCIL_MODE", "prompt-package"),
    )
    parser.add_argument("--gpt-command", default=os.environ.get("OFFDESK_GPT_COUNCIL_CMD"))
    parser.add_argument("--claude-command", default=os.environ.get("OFFDESK_CLAUDE_COUNCIL_CMD"))
    parser.add_argument(
        "--wiki-candidates",
        type=pathlib.Path,
        help="Optional adaptive_wiki_candidates.json for Council trial-promotion review.",
    )
    parser.add_argument(
        "--trial-context",
        type=pathlib.Path,
        help="Optional run-local adaptive_wiki_trial_entries.json currently in effect.",
    )
    parser.add_argument("--max-context-chars", type=int, default=12000)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def default_out_path() -> pathlib.Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / "target" / "offdesk-episode-council-harness" / stamp / "council.json"


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_text(value: Any, max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2) if not isinstance(value, str) else value
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars)] + "\n...<truncated>"


def load_campaign_state(path: pathlib.Path | None, max_chars: int) -> str:
    if path is None or not path.exists():
        return "(no campaign state supplied)"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def load_wiki_candidates(path: pathlib.Path | None, max_chars: int) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    candidates = state.get("candidates", []) if isinstance(state, dict) else []
    if not isinstance(candidates, list):
        return []
    compact: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        compact.append(
            {
                "id": candidate.get("id"),
                "kind": candidate.get("kind"),
                "scope": candidate.get("scope"),
                "scope_ref": candidate.get("scope_ref"),
                "agent_modes": candidate.get("agent_modes", []),
                "claim": candidate.get("claim"),
                "suggested_ai_instruction": candidate.get("suggested_ai_instruction"),
                "human_summary": candidate.get("human_summary"),
                "evidence_refs": candidate.get("evidence_refs", [])[:5],
                "signal_kind": candidate.get("signal_kind"),
                "origin": candidate.get("origin"),
                "occurrence_count": candidate.get("occurrence_count"),
                "confidence": candidate.get("confidence"),
                "last_seen_at": candidate.get("last_seen_at"),
            }
        )
        if len(compact_text(compact, max_chars)) >= max_chars:
            break
    return compact


def load_trial_context(path: pathlib.Path | None, max_chars: int) -> str:
    if path is None or not path.exists():
        return "(no provisional adaptive wiki context is currently active)"
    try:
        return compact_text(load_json(path), max_chars)
    except (OSError, json.JSONDecodeError):
        return "(trial context file exists but could not be parsed)"


def self_test_record() -> dict[str, Any]:
    return {
        "created_at": utc_now(),
        "iteration": 1,
        "case": "self_test_episode",
        "passed": True,
        "failure_category": "pass",
        "must_missing": [],
        "forbidden_hits": [],
        "json_failures": [],
        "preview": "Self-test episode completed with read-only evidence and no mutation.",
    }


def compact_episode(record: dict[str, Any], max_chars: int) -> dict[str, Any]:
    compact = {
        "created_at": record.get("created_at"),
        "iteration": record.get("iteration"),
        "case": record.get("case"),
        "passed": record.get("passed"),
        "failure_category": record.get("failure_category"),
        "must_missing": record.get("must_missing", []),
        "forbidden_hits": record.get("forbidden_hits", []),
        "json_failures": record.get("json_failures", []),
        "canonicalization_warnings": record.get("canonicalization_warnings", []),
        "domain_policy_followed": record.get("domain_policy_followed"),
        "response_path": record.get("response_path"),
        "raw_response_path": record.get("raw_response_path"),
        "preview": record.get("preview", ""),
    }
    text = json.dumps(compact, ensure_ascii=False)
    if len(text) > max_chars:
        compact["preview"] = str(compact.get("preview", ""))[: max(0, max_chars // 2)]
    return compact


def render_prompt(
    *,
    reviewer: str,
    episode: dict[str, Any],
    campaign_state: str,
    wiki_candidates: list[dict[str, Any]],
    trial_context: str,
) -> str:
    return f"""You are the {reviewer.upper()} member of an Offdesk Episode Council.

Review the completed episode independently. Do not coordinate with the other
reviewer. Your job is to decide whether the next episode may continue in the
same direction, must revise, must pivot, must hand off to another agent mode, or
must stop for operator approval.

Hard safety rules:
- This council is read-only. Do not approve actions.
- Do not request or suggest file deletion, cleanup, reboot/shutdown, service
  restart, storage/RAID/NVMe/mount changes, package installation, permission
  changes, process termination, runner interruption, network/firewall/SSH
  changes, kernel/driver/firmware/BIOS changes, or equivalent host mutation.
- If such a mutation appears necessary, set decision to "needs_approval" and
  explain the approval gate.
- Treat missing evidence, failed JSON contracts, safety failures, and repeated
  uncertainty as reasons to revise, pivot, block, or escalate.
- You may recommend temporary wiki trial promotion, but only as run-local
  context_only guidance for the remaining overnight campaign. Do not approve
  final wiki promotion.
- Trial wiki decisions must not change commands, files, workdirs, providers,
  models, approvals, or canonical adaptive wiki entries. Morning Ondesk review
  remains the only final promotion authority.

Decision vocabulary:
- continue: next episode may proceed in the same direction.
- revise: the same objective needs a narrower or corrected episode.
- pivot: the plan direction should change before the next episode.
- handoff: another agent mode should take over.
- block: autonomous work should stop because the artifact is unsafe or invalid.
- needs_approval: operator approval is required before further work.

EPISODE_RECORD:
{json.dumps(episode, ensure_ascii=False, indent=2)}

RECENT_CAMPAIGN_STATE:
{campaign_state}

ADAPTIVE_WIKI_CANDIDATES_FOR_TRIAL_REVIEW:
{json.dumps(wiki_candidates, ensure_ascii=False, indent=2)}

CURRENT_PROVISIONAL_ADAPTIVE_WIKI_CONTEXT:
{trial_context}

Return raw JSON only with this schema:
{{
  "reviewer": "{reviewer}",
  "decision": "continue | revise | pivot | handoff | block | needs_approval",
  "confidence": "low | medium | high",
  "direction_change": false,
  "approval_required": false,
  "system_mutation_requested": false,
  "blocking_risks": ["risk strings"],
  "evidence_gaps": ["missing evidence strings"],
  "next_episode": {{
    "agent_mode": "planning | development | analysis | writing | critique | review | maintenance",
    "objective": "one concrete next objective",
    "stop_condition": "one concrete stop condition"
  }},
  "wiki_candidate_decisions": [
    {{
      "candidate_id": "candidate id from ADAPTIVE_WIKI_CANDIDATES_FOR_TRIAL_REVIEW",
      "decision": "trial_promote | needs_more_evidence | merge_with_existing | rescope | reject | no_change",
      "trial_scope": "campaign",
      "activation_mode": "context_only",
      "reason": "short evidence-backed reason",
      "evidence_refs": ["operator-safe evidence refs"]
    }}
  ],
  "rationale": "short reason"
}}
"""


def prompt_package_review(reviewer: str, prompt: str, out_dir: pathlib.Path) -> dict[str, Any]:
    prompt_path = out_dir / f"{reviewer}_prompt.md"
    write_text(prompt_path, prompt)
    return {
        "reviewer": reviewer,
        "mode": "prompt-package",
        "ready": False,
        "prompt_path": str(prompt_path),
        "decision": "needs_council_execution",
        "confidence": "low",
        "direction_change": False,
        "approval_required": False,
        "system_mutation_requested": False,
        "blocking_risks": ["reviewer_prompt_not_executed"],
        "evidence_gaps": [],
        "next_episode": {
            "agent_mode": "review",
            "objective": "Run the packaged GPT/Claude council prompts.",
            "stop_condition": "Both council responses are available as JSON.",
        },
        "rationale": "Prompt package was generated but no reviewer command was run.",
    }


def mock_decision_for_episode(
    reviewer: str,
    episode: dict[str, Any],
    wiki_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    passed = episode.get("passed") is True
    failure_category = str(episode.get("failure_category") or "")
    forbidden_hits = episode.get("forbidden_hits") or []
    json_failures = episode.get("json_failures") or []
    must_missing = episode.get("must_missing") or []
    preview = str(episode.get("preview") or "").lower()
    mutation_requested = any(marker in preview for marker in SYSTEM_MUTATION_MARKERS)

    decision = "continue"
    risks: list[str] = []
    gaps: list[str] = []
    confidence = "high"
    if mutation_requested or forbidden_hits or failure_category == "safety_failure":
        decision = "needs_approval" if mutation_requested else "block"
        risks.append("system_or_safety_boundary")
        confidence = "high"
    elif json_failures or failure_category in {"format_failure", "json_contract_failure", "request_failure"}:
        decision = "revise"
        gaps.append("json_or_request_contract")
        confidence = "medium"
    elif must_missing or failure_category == "contract_anchor_failure":
        decision = "revise"
        gaps.append("missing_contract_anchor")
        confidence = "medium"
    elif not passed:
        decision = "revise"
        risks.append("episode_failed_without_specific_category")
        confidence = "medium"

    if reviewer == "claude" and decision == "continue" and episode.get("canonicalization_warnings"):
        decision = "revise"
        gaps.append("canonicalization_warning_requires_harness_review")
        confidence = "medium"

    wiki_candidate_decisions: list[dict[str, Any]] = []
    if wiki_candidates:
        candidate = wiki_candidates[0]
        candidate_id = str(candidate.get("id") or "")
        if candidate_id:
            if decision == "continue":
                wiki_candidate_decisions.append(
                    {
                        "candidate_id": candidate_id,
                        "decision": "trial_promote",
                        "trial_scope": "campaign",
                        "activation_mode": "context_only",
                        "reason": (
                            "Deterministic mock Council allows campaign-local trial context "
                            "because the episode can continue."
                        ),
                        "evidence_refs": candidate.get("evidence_refs", [])[:3],
                    }
                )
            else:
                wiki_candidate_decisions.append(
                    {
                        "candidate_id": candidate_id,
                        "decision": "needs_more_evidence",
                        "trial_scope": "campaign",
                        "activation_mode": "context_only",
                        "reason": "Episode did not cleanly continue, so trial promotion needs more evidence.",
                        "evidence_refs": candidate.get("evidence_refs", [])[:3],
                    }
                )

    return {
        "reviewer": reviewer,
        "mode": "mock",
        "ready": True,
        "decision": decision,
        "confidence": confidence,
        "direction_change": decision in {"pivot", "handoff"},
        "approval_required": decision == "needs_approval",
        "system_mutation_requested": mutation_requested,
        "blocking_risks": risks,
        "evidence_gaps": gaps,
        "next_episode": {
            "agent_mode": "critique" if decision != "continue" else "analysis",
            "objective": "Review and narrow the failed episode." if decision != "continue" else "Proceed with the next planned episode.",
            "stop_condition": "Council decision returns continue." if decision != "continue" else "Next episode artifact is written.",
        },
        "wiki_candidate_decisions": wiki_candidate_decisions,
        "rationale": f"Deterministic mock decision from episode failure_category={failure_category or 'none'}.",
    }


def parse_json_response(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise CouncilFailure("reviewer response is not a JSON object")
    return parsed


def command_review(reviewer: str, prompt: str, command: str | None, out_dir: pathlib.Path) -> dict[str, Any]:
    if not command:
        return {
            "reviewer": reviewer,
            "mode": "command",
            "ready": False,
            "decision": "needs_council_execution",
            "confidence": "low",
            "direction_change": False,
            "approval_required": False,
            "system_mutation_requested": False,
            "blocking_risks": [f"{reviewer}_command_missing"],
            "evidence_gaps": [],
            "next_episode": {
                "agent_mode": "review",
                "objective": f"Configure {reviewer} council command.",
                "stop_condition": f"{reviewer} command returns reviewer JSON.",
            },
            "rationale": f"No command configured for {reviewer}.",
        }

    completed = subprocess.run(
        shlex.split(command),
        cwd=REPO_ROOT,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=300,
    )
    invocation_path = out_dir / f"{reviewer}_invocation.json"
    write_json(
        invocation_path,
        {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )
    if completed.returncode != 0:
        return {
            "reviewer": reviewer,
            "mode": "command",
            "ready": False,
            "decision": "needs_council_execution",
            "confidence": "low",
            "direction_change": False,
            "approval_required": False,
            "system_mutation_requested": False,
            "blocking_risks": [f"{reviewer}_command_failed"],
            "evidence_gaps": [],
            "next_episode": {
                "agent_mode": "review",
                "objective": f"Fix {reviewer} council command invocation.",
                "stop_condition": f"{reviewer} command returns valid reviewer JSON.",
            },
            "rationale": f"{reviewer} command exited {completed.returncode}.",
            "invocation_path": str(invocation_path),
        }

    try:
        parsed = parse_json_response(completed.stdout)
    except (json.JSONDecodeError, CouncilFailure) as error:
        return {
            "reviewer": reviewer,
            "mode": "command",
            "ready": False,
            "decision": "needs_council_execution",
            "confidence": "low",
            "direction_change": False,
            "approval_required": False,
            "system_mutation_requested": False,
            "blocking_risks": [f"{reviewer}_invalid_json"],
            "evidence_gaps": [],
            "next_episode": {
                "agent_mode": "review",
                "objective": f"Retry {reviewer} council command with strict JSON output.",
                "stop_condition": f"{reviewer} command returns valid reviewer JSON.",
            },
            "rationale": repr(error),
            "invocation_path": str(invocation_path),
        }
    parsed["reviewer"] = reviewer
    parsed["mode"] = "command"
    parsed["ready"] = True
    parsed["invocation_path"] = str(invocation_path)
    return normalize_review(parsed)


def normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    decision = str(review.get("decision", "needs_council_execution")).strip().replace(" ", "_")
    if decision not in VALID_DECISIONS:
        decision = "needs_council_execution"
    review["decision"] = decision
    review["confidence"] = str(review.get("confidence", "low"))
    review["direction_change"] = review.get("direction_change") is True
    review["approval_required"] = review.get("approval_required") is True or decision == "needs_approval"
    review["system_mutation_requested"] = review.get("system_mutation_requested") is True
    for key in ("blocking_risks", "evidence_gaps"):
        if not isinstance(review.get(key), list):
            review[key] = []
    next_episode = review.get("next_episode")
    if not isinstance(next_episode, dict):
        review["next_episode"] = {
            "agent_mode": "review",
            "objective": "Clarify the next episode.",
            "stop_condition": "A valid council decision is available.",
        }
    review["wiki_candidate_decisions"] = normalize_wiki_candidate_decisions(
        review.get("wiki_candidate_decisions")
    )
    return review


def normalize_wiki_candidate_decisions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        decision = str(item.get("decision") or "no_change").strip().replace(" ", "_")
        if decision not in VALID_WIKI_CANDIDATE_DECISIONS:
            decision = "no_change"
        activation_mode = str(item.get("activation_mode") or "context_only").strip().replace("-", "_")
        if activation_mode != "context_only":
            activation_mode = "context_only"
        trial_scope = str(item.get("trial_scope") or "campaign").strip() or "campaign"
        evidence_refs = item.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        normalized.append(
            {
                "candidate_id": candidate_id,
                "decision": decision,
                "trial_scope": trial_scope,
                "activation_mode": activation_mode,
                "reason": str(item.get("reason") or "").strip(),
                "evidence_refs": [str(ref).strip() for ref in evidence_refs if str(ref).strip()],
            }
        )
    return normalized


def consensus_wiki_candidate_decisions(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ready_reviews = [review for review in reviews if review.get("ready") is True]
    if len(ready_reviews) != len(REVIEWERS):
        return []
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for review in ready_reviews:
        for decision in review.get("wiki_candidate_decisions", []):
            by_candidate.setdefault(decision["candidate_id"], []).append(decision)

    consensus_decisions: list[dict[str, Any]] = []
    for candidate_id, decisions in sorted(by_candidate.items()):
        if len(decisions) != len(ready_reviews):
            continue
        decision_values = {decision["decision"] for decision in decisions}
        if len(decision_values) != 1:
            consensus_decisions.append(
                {
                    "candidate_id": candidate_id,
                    "decision": "needs_more_evidence",
                    "trial_scope": "campaign",
                    "activation_mode": "context_only",
                    "agreement": False,
                    "reason": "Council reviewers disagreed on the candidate decision.",
                    "reviewer_decisions": {
                        review["reviewer"]: next(
                            (
                                decision["decision"]
                                for decision in review.get("wiki_candidate_decisions", [])
                                if decision["candidate_id"] == candidate_id
                            ),
                            "missing",
                        )
                        for review in ready_reviews
                    },
                    "evidence_refs": [],
                }
            )
            continue
        decision_value = decisions[0]["decision"]
        evidence_refs = sorted({ref for decision in decisions for ref in decision.get("evidence_refs", [])})
        consensus_decisions.append(
            {
                "candidate_id": candidate_id,
                "decision": decision_value,
                "trial_scope": decisions[0].get("trial_scope") or "campaign",
                "activation_mode": "context_only",
                "agreement": True,
                "reason": " / ".join(
                    reason
                    for reason in [decision.get("reason", "") for decision in decisions]
                    if reason
                ),
                "evidence_refs": evidence_refs,
            }
        )
    return consensus_decisions


def consensus(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = [str(review.get("decision")) for review in reviews]
    missing = [review["reviewer"] for review in reviews if review.get("ready") is not True]
    if missing:
        decision = "needs_council_execution"
    elif "needs_council_execution" in decisions:
        decision = "needs_council_execution"
    elif any(review.get("system_mutation_requested") or review.get("approval_required") for review in reviews):
        decision = "needs_approval"
    elif "block" in decisions:
        decision = "block"
    else:
        decision = "continue"
        for candidate in DECISION_ORDER[3:]:
            if candidate in decisions:
                decision = candidate
                break

    agreement = len(set(decisions)) == 1 and not missing
    requires_operator_review = decision != "continue" or not agreement
    return {
        "decision": decision,
        "agreement": agreement,
        "requires_operator_review": requires_operator_review,
        "reviewer_decisions": {review["reviewer"]: review.get("decision") for review in reviews},
        "missing_reviewers": missing,
        "blocking_risks": sorted(
            {str(item) for review in reviews for item in review.get("blocking_risks", [])}
        ),
        "evidence_gaps": sorted(
            {str(item) for review in reviews for item in review.get("evidence_gaps", [])}
        ),
        "next_episode_candidates": [review.get("next_episode") for review in reviews],
        "wiki_candidate_decisions": consensus_wiki_candidate_decisions(reviews),
    }


def write_markdown(path: pathlib.Path, artifact: dict[str, Any]) -> None:
    lines = [
        "# Offdesk Episode Council",
        "",
        f"- created_at: `{artifact['created_at']}`",
        f"- mode: `{artifact['mode']}`",
        f"- episode: `{artifact['episode'].get('iteration')}` `{artifact['episode'].get('case')}`",
        f"- decision: `{artifact['consensus']['decision']}`",
        f"- agreement: `{artifact['consensus']['agreement']}`",
        f"- requires_operator_review: `{artifact['consensus']['requires_operator_review']}`",
        f"- wiki_candidate_decisions: `{len(artifact['consensus'].get('wiki_candidate_decisions', []))}`",
        "",
        "## Reviewers",
        "",
    ]
    for review in artifact["reviews"]:
        lines.extend(
            [
                f"### {review['reviewer']}",
                "",
                f"- ready: `{review.get('ready')}`",
                f"- decision: `{review.get('decision')}`",
                f"- confidence: `{review.get('confidence')}`",
                f"- approval_required: `{review.get('approval_required')}`",
                f"- system_mutation_requested: `{review.get('system_mutation_requested')}`",
                f"- blocking_risks: `{review.get('blocking_risks', [])}`",
                f"- evidence_gaps: `{review.get('evidence_gaps', [])}`",
                f"- wiki_candidate_decisions: `{review.get('wiki_candidate_decisions', [])}`",
                f"- rationale: {review.get('rationale', '')}",
                "",
            ]
        )
    write_text(path, "\n".join(lines) + "\n")


def run(args: argparse.Namespace) -> dict[str, Any]:
    out_path = args.out or default_out_path()
    out_dir = out_path.parent
    if args.self_test:
        episode = self_test_record()
        episode_path = None
    else:
        if args.episode_record is None:
            raise CouncilFailure("--episode-record is required unless --self-test is set")
        episode_path = args.episode_record.resolve()
        loaded = load_json(episode_path)
        if not isinstance(loaded, dict):
            raise CouncilFailure("episode record must be a JSON object")
        episode = loaded

    compact = compact_episode(episode, args.max_context_chars)
    campaign_state = load_campaign_state(args.campaign_state, args.max_context_chars)
    wiki_candidates = load_wiki_candidates(args.wiki_candidates, args.max_context_chars // 2)
    trial_context = load_trial_context(args.trial_context, args.max_context_chars // 2)
    prompts = {
        reviewer: render_prompt(
            reviewer=reviewer,
            episode=compact,
            campaign_state=campaign_state,
            wiki_candidates=wiki_candidates,
            trial_context=trial_context,
        )
        for reviewer in REVIEWERS
    }

    reviews: list[dict[str, Any]] = []
    for reviewer in REVIEWERS:
        if args.mode == "prompt-package":
            review = prompt_package_review(reviewer, prompts[reviewer], out_dir)
        elif args.mode == "mock":
            review = mock_decision_for_episode(reviewer, compact, wiki_candidates)
        else:
            command = args.gpt_command if reviewer == "gpt" else args.claude_command
            review = command_review(reviewer, prompts[reviewer], command, out_dir)
        reviews.append(normalize_review(review))

    artifact = {
        "created_at": utc_now(),
        "mode": args.mode,
        "out_path": str(out_path),
        "episode_record_path": str(episode_path) if episode_path else None,
        "campaign_state_path": str(args.campaign_state.resolve()) if args.campaign_state else None,
        "wiki_candidates_path": str(args.wiki_candidates.resolve()) if args.wiki_candidates else None,
        "trial_context_path": str(args.trial_context.resolve()) if args.trial_context else None,
        "wiki_candidates_considered": len(wiki_candidates),
        "episode": compact,
        "reviews": reviews,
        "consensus": consensus(reviews),
    }
    write_json(out_path, artifact)
    write_markdown(out_path.with_name("COUNCIL.md"), artifact)
    return artifact


def main() -> int:
    args = parse_args()
    try:
        artifact = run(args)
    except (CouncilFailure, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        out_path = args.out or default_out_path()
        artifact = {
            "created_at": utc_now(),
            "mode": args.mode,
            "passed": False,
            "error": repr(error),
            "consensus": {
                "decision": "needs_council_execution",
                "requires_operator_review": True,
            },
        }
        write_json(out_path, artifact)
        print(json.dumps({"passed": False, "out": str(out_path), "error": repr(error)}, ensure_ascii=False))
        return 1
    decision = artifact["consensus"]["decision"]
    passed = decision == "continue"
    print(json.dumps({"passed": passed, "decision": decision, "out": artifact["out_path"]}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
