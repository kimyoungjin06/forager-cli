#!/usr/bin/env python3
"""Build a read-only morning wiki review report for a TwinPaper Offdesk run.

The report treats candidate and provisional trial rows as one lifecycle queue.
It recommends a morning Ondesk action, but it never mutates canonical wiki
entries, candidates, approvals, task queues, or workload artifacts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
from typing import Any


DEFAULT_PROFILE = "twinpaper-adaptive-debug"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=pathlib.Path, required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--profile-dir",
        type=pathlib.Path,
        help="Forager/agent-of-empires profile directory. Defaults to ~/.config/agent-of-empires/profiles/<profile>.",
    )
    parser.add_argument("--candidate-store", type=pathlib.Path, help="Override adaptive_wiki_candidates.json path.")
    parser.add_argument("--trial-store", type=pathlib.Path, help="Override adaptive_wiki_trial_entries.json path.")
    parser.add_argument("--result-review", type=pathlib.Path, help="Override result_review/results.json path.")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help="Write report JSON here. Defaults to <workload>/morning_wiki_review/report.json.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_or_default(path: pathlib.Path | None, default: Any) -> Any:
    if path is None or not path.exists():
        return default
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError):
        return default


def load_jsonl(path: pathlib.Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def profile_dir(args: argparse.Namespace) -> pathlib.Path:
    if args.profile_dir:
        return args.profile_dir.expanduser().resolve()
    return (
        pathlib.Path.home()
        / ".config"
        / "agent-of-empires"
        / "profiles"
        / args.profile
    )


def default_out_path(result_path: pathlib.Path) -> pathlib.Path:
    return result_path.parent / "morning_wiki_review" / "report.json"


def clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def result_path_from_summary(result: dict[str, Any], key: str) -> pathlib.Path | None:
    summary = result.get("summary", {})
    if not isinstance(summary, dict):
        return None
    value = summary.get(key)
    if not value:
        return None
    return pathlib.Path(str(value))


def candidate_store_path(args: argparse.Namespace, result: dict[str, Any]) -> pathlib.Path:
    if args.candidate_store:
        return args.candidate_store.expanduser().resolve()
    manifest = result.get("manifest", {})
    if isinstance(manifest, dict):
        learning = manifest.get("adaptive_wiki_learning", {})
        if isinstance(learning, dict) and learning.get("candidate_store"):
            return pathlib.Path(str(learning["candidate_store"]))
    return profile_dir(args) / "adaptive_wiki_candidates.json"


def trial_store_path(result_path: pathlib.Path, result: dict[str, Any]) -> pathlib.Path:
    summary = result.get("summary", {})
    if isinstance(summary, dict) and summary.get("wiki_trial_entries_path"):
        return pathlib.Path(str(summary["wiki_trial_entries_path"]))
    if isinstance(summary, dict):
        trial = summary.get("adaptive_wiki_trial", {})
        if isinstance(trial, dict) and trial.get("path"):
            return pathlib.Path(str(trial["path"]))
    return result_path.parent / "adaptive_wiki_trial_entries.json"


def result_review_path(result_path: pathlib.Path, result: dict[str, Any]) -> pathlib.Path:
    return result_path_from_summary(result, "result_review_path") or (
        result_path.parent / "result_review" / "results.json"
    )


def council_records(result_path: pathlib.Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    records = result.get("council_records", [])
    if isinstance(records, list):
        normalized = [record for record in records if isinstance(record, dict)]
        if normalized:
            return normalized
    progress = result_path_from_summary(result, "council_progress_path")
    if progress is None:
        summary = result.get("summary", {})
        if isinstance(summary, dict):
            council = summary.get("council", {})
            if isinstance(council, dict) and council.get("progress_path"):
                progress = pathlib.Path(str(council["progress_path"]))
    return load_jsonl(progress or (result_path.parent / "council_progress.jsonl"))


def load_candidates(path: pathlib.Path) -> list[dict[str, Any]]:
    state = load_json_or_default(path, {"candidates": []})
    candidates = state.get("candidates", []) if isinstance(state, dict) else []
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def load_trials(path: pathlib.Path) -> list[dict[str, Any]]:
    state = load_json_or_default(path, {"entries": []})
    entries = state.get("entries", []) if isinstance(state, dict) else []
    return [entry for entry in entries if isinstance(entry, dict)]


def item_key_for_candidate(candidate: dict[str, Any]) -> str:
    return clean_text(candidate.get("id")) or f"candidate:{clean_text(candidate.get('claim'))}"


def item_key_for_trial(trial: dict[str, Any]) -> str:
    return clean_text(trial.get("candidate_id")) or clean_text(trial.get("id")) or f"trial:{clean_text(trial.get('claim'))}"


def base_item_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": item_key_for_candidate(candidate),
        "candidate_id": clean_text(candidate.get("id")) or None,
        "trial_entry_ids": [],
        "lifecycle_status": "candidate",
        "activation_mode": "none",
        "confidence": clean_text(candidate.get("confidence")) or "inferred",
        "evidence_states": ["observed"],
        "scope": clean_text(candidate.get("scope")) or "project",
        "scope_ref": clean_text(candidate.get("scope_ref")) or "twinpaper",
        "agent_modes": clean_list(candidate.get("agent_modes")),
        "claim": clean_text(candidate.get("claim")),
        "instruction": clean_text(candidate.get("suggested_ai_instruction")) or clean_text(candidate.get("claim")),
        "human_summary": clean_text(candidate.get("human_summary")),
        "evidence_refs": clean_list(candidate.get("evidence_refs")),
        "source_refs": clean_list(candidate.get("source_refs")),
        "council_refs": [],
        "trial_usage": [],
        "review_notes": [],
    }


def merge_trial(item: dict[str, Any], trial: dict[str, Any]) -> None:
    trial_id = clean_text(trial.get("id"))
    if trial_id and trial_id not in item["trial_entry_ids"]:
        item["trial_entry_ids"].append(trial_id)
    item["lifecycle_status"] = "provisional"
    item["activation_mode"] = "context_only"
    if clean_text(trial.get("claim")):
        item["claim"] = clean_text(trial.get("claim"))
    if clean_text(trial.get("instruction")):
        item["instruction"] = clean_text(trial.get("instruction"))
    if clean_text(trial.get("human_summary")):
        item["human_summary"] = clean_text(trial.get("human_summary"))
    item["evidence_refs"] = clean_list(item["evidence_refs"] + clean_list(trial.get("evidence_refs")))
    item["source_refs"] = clean_list(item["source_refs"] + clean_list(trial.get("source_refs")))
    item["council_refs"] = clean_list(item["council_refs"] + clean_list(trial.get("council_refs")))
    item["expires_at"] = clean_text(trial.get("expires_at")) or None
    add_state(item, "council_agreed")


def add_state(item: dict[str, Any], state: str) -> None:
    if state not in item["evidence_states"]:
        item["evidence_states"].append(state)


def index_council_decisions(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        decisions = record.get("wiki_candidate_decisions", [])
        if not isinstance(decisions, list):
            continue
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            candidate_id = clean_text(decision.get("candidate_id"))
            if not candidate_id:
                continue
            row = dict(decision)
            row["council_path"] = record.get("council_path")
            row["iteration"] = record.get("iteration")
            row["case"] = record.get("case")
            by_candidate.setdefault(candidate_id, []).append(row)
    return by_candidate


def index_trial_usage(result: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    usage: dict[str, list[dict[str, Any]]] = {}
    records = result.get("records", [])
    if not isinstance(records, list):
        return usage
    for record in records:
        if not isinstance(record, dict):
            continue
        for entry_id in clean_list(record.get("adaptive_wiki_trial_entry_ids")):
            usage.setdefault(entry_id, []).append(
                {
                    "iteration": record.get("iteration"),
                    "case": record.get("case"),
                    "passed": record.get("passed"),
                    "failure_category": record.get("failure_category"),
                    "response_path": record.get("response_path"),
                }
            )
    return usage


def apply_council_and_usage(
    item: dict[str, Any],
    *,
    candidate_decisions: list[dict[str, Any]],
    usage_by_trial: dict[str, list[dict[str, Any]]],
) -> None:
    for decision in candidate_decisions:
        item["review_notes"].append(
            {
                "kind": "council_decision",
                "decision": decision.get("decision"),
                "iteration": decision.get("iteration"),
                "case": decision.get("case"),
                "reason": decision.get("reason"),
                "council_path": decision.get("council_path"),
            }
        )
        if decision.get("decision") == "trial_promote":
            add_state(item, "council_agreed")
        elif decision.get("decision") == "needs_more_evidence":
            add_state(item, "needs_more_evidence")
    for trial_id in item["trial_entry_ids"]:
        usage = usage_by_trial.get(trial_id, [])
        if usage:
            add_state(item, "used")
            item["trial_usage"].extend(usage)
            if any(row.get("passed") is False for row in usage):
                add_state(item, "counterexample_found")
            if any(row.get("passed") is True for row in usage):
                add_state(item, "evaluated")


def recommendation_for_item(item: dict[str, Any], result_review: dict[str, Any]) -> dict[str, Any]:
    states = set(item["evidence_states"])
    decision = "needs_more_evidence"
    reason = "The item is observed but has not accumulated enough morning-review evidence."
    suggested_activation = "context_only"
    suggested_confidence = item.get("confidence") or "inferred"

    if "counterexample_found" in states:
        decision = "rescope"
        reason = "The item was used during a failing episode, so keep or narrow it before promotion."
    elif item["lifecycle_status"] == "provisional" and "council_agreed" in states:
        decision = "promote_context_only"
        reason = "Council agreed to provisional use; final promotion may proceed with low confidence even without effect evaluation."
    elif item["lifecycle_status"] == "candidate" and item.get("instruction") and item.get("evidence_refs"):
        decision = "keep_candidate"
        reason = "The item has provenance and instruction text, but it was not provisionally exercised."

    review_decision = result_review.get("decision") if isinstance(result_review, dict) else None
    if review_decision == "blocked" and decision == "promote_context_only":
        decision = "needs_more_evidence"
        reason = "The overall run review was blocked, so defer final promotion even though provisional use was approved."

    command = None
    if decision == "promote_context_only" and item.get("candidate_id"):
        command = (
            "forager -p <profile> offdesk wiki promote "
            f"{item['candidate_id']} --activation-mode context-only "
            f"--scope {item['scope']} --scope-ref {item['scope_ref']} "
            "--reason \"morning Ondesk review accepted provisional overnight wiki item\""
        )
    return {
        "decision": decision,
        "reason": reason,
        "suggested_activation_mode": suggested_activation,
        "suggested_confidence": suggested_confidence,
        "evaluation_required": False,
        "recommended_command": command,
    }


def build_review(args: argparse.Namespace, result_path: pathlib.Path) -> dict[str, Any]:
    result = load_json(result_path)
    if not isinstance(result, dict):
        raise ValueError("result must be a JSON object")
    candidate_path = candidate_store_path(args, result)
    trial_path = args.trial_store.expanduser().resolve() if args.trial_store else trial_store_path(result_path, result)
    review_path = (
        args.result_review.expanduser().resolve()
        if args.result_review
        else result_review_path(result_path, result)
    )
    candidates = load_candidates(candidate_path)
    trials = load_trials(trial_path)
    result_review = load_json_or_default(review_path, {})
    council = council_records(result_path, result)
    council_by_candidate = index_council_decisions(council)
    usage_by_trial = index_trial_usage(result)

    items: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        items[item_key_for_candidate(candidate)] = base_item_from_candidate(candidate)
    for trial in trials:
        key = item_key_for_trial(trial)
        item = items.get(key)
        if item is None:
            item = {
                "item_id": key,
                "candidate_id": clean_text(trial.get("candidate_id")) or None,
                "trial_entry_ids": [],
                "lifecycle_status": "provisional",
                "activation_mode": "context_only",
                "confidence": "inferred",
                "evidence_states": ["observed"],
                "scope": clean_text(trial.get("scope")) or "project",
                "scope_ref": clean_text(trial.get("scope_ref")) or "twinpaper",
                "agent_modes": clean_list(trial.get("agent_modes")),
                "claim": clean_text(trial.get("claim")),
                "instruction": clean_text(trial.get("instruction")) or clean_text(trial.get("claim")),
                "human_summary": clean_text(trial.get("human_summary")),
                "evidence_refs": [],
                "source_refs": [],
                "council_refs": [],
                "trial_usage": [],
                "review_notes": [],
            }
            items[key] = item
        merge_trial(item, trial)

    for item in items.values():
        candidate_id = item.get("candidate_id")
        apply_council_and_usage(
            item,
            candidate_decisions=council_by_candidate.get(candidate_id, []) if candidate_id else [],
            usage_by_trial=usage_by_trial,
        )
        item["recommendation"] = recommendation_for_item(item, result_review)

    sorted_items = sorted(
        items.values(),
        key=lambda item: (
            item["recommendation"]["decision"],
            item.get("scope", ""),
            item.get("scope_ref", ""),
            item.get("item_id", ""),
        ),
    )
    counts: dict[str, int] = {}
    state_counts: dict[str, int] = {}
    for item in sorted_items:
        decision = item["recommendation"]["decision"]
        counts[decision] = counts.get(decision, 0) + 1
        for state in item["evidence_states"]:
            state_counts[state] = state_counts.get(state, 0) + 1

    return {
        "generated_at": utc_now(),
        "kind": "twinpaper_morning_wiki_review",
        "version": 1,
        "read_only": True,
        "result_path": str(result_path),
        "candidate_store_path": str(candidate_path),
        "trial_store_path": str(trial_path),
        "result_review_path": str(review_path),
        "summary": {
            "items": len(sorted_items),
            "candidates_loaded": len(candidates),
            "trial_entries_loaded": len(trials),
            "council_records_loaded": len(council),
            "recommendation_counts": counts,
            "evidence_state_counts": state_counts,
            "evaluation_required_for_insertion": False,
            "canonical_mutation_performed": False,
        },
        "items": sorted_items,
    }


def write_markdown(path: pathlib.Path, review: dict[str, Any]) -> None:
    lines = [
        "# TwinPaper Morning Wiki Review",
        "",
        f"- generated_at: `{review['generated_at']}`",
        f"- result_path: `{review['result_path']}`",
        f"- candidate_store_path: `{review['candidate_store_path']}`",
        f"- trial_store_path: `{review['trial_store_path']}`",
        f"- result_review_path: `{review['result_review_path']}`",
        f"- read_only: `{review['read_only']}`",
        f"- evaluation_required_for_insertion: `{review['summary']['evaluation_required_for_insertion']}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(review["summary"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Lifecycle Queue",
        "",
    ]
    if not review["items"]:
        lines.append("- No candidate or provisional wiki items found.")
    for item in review["items"]:
        recommendation = item["recommendation"]
        lines.extend(
            [
                f"### {item['item_id']}",
                "",
                f"- status: `{item['lifecycle_status']}`",
                f"- activation_mode: `{item['activation_mode']}`",
                f"- confidence: `{item['confidence']}`",
                f"- evidence_states: `{', '.join(item['evidence_states'])}`",
                f"- scope: `{item['scope']}:{item['scope_ref']}`",
                f"- agent_modes: `{', '.join(item['agent_modes']) or 'shared'}`",
                f"- claim: {item['claim']}",
                f"- instruction: {item['instruction']}",
                f"- recommendation: `{recommendation['decision']}`",
                f"- reason: {recommendation['reason']}",
                f"- evaluation_required: `{recommendation['evaluation_required']}`",
            ]
        )
        if recommendation.get("recommended_command"):
            lines.extend(["", "```bash", recommendation["recommended_command"], "```"])
        lines.append("")
    write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    result_path = args.result.expanduser().resolve()
    out_path = (args.out or default_out_path(result_path)).expanduser().resolve()
    review = build_review(args, result_path)
    write_text(out_path, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    write_markdown(out_path.with_name("MORNING_WIKI_REVIEW.md"), review)
    print(
        json.dumps(
            {
                "out": str(out_path),
                "items": review["summary"]["items"],
                "recommendation_counts": review["summary"]["recommendation_counts"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
