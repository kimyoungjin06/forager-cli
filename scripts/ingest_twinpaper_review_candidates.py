#!/usr/bin/env python3
"""Ingest TwinPaper post-run review learning candidates into adaptive wiki candidates.

This is intentionally candidate-only: it never promotes, rejects, or edits
promoted entries. Overnight workloads can use it to preserve learning signals
for morning review without granting autonomous wiki authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import uuid
from typing import Any


ADAPTIVE_WIKI_VERSION = "2026-05-14.v0"
DEFAULT_PROFILE = "twinpaper-adaptive-debug"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=pathlib.Path, required=True)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument(
        "--profile-dir",
        type=pathlib.Path,
        help="Forager/agent-of-empires profile directory. Defaults to ~/.config/agent-of-empires/profiles/<profile>.",
    )
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


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


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def clean_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = clean_text(value)
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def review_hash(review_path: pathlib.Path) -> str:
    return hashlib.sha256(review_path.read_bytes()).hexdigest()


def load_candidate_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": ADAPTIVE_WIKI_VERSION, "candidates": []}
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return {"version": ADAPTIVE_WIKI_VERSION, "candidates": []}
    state = json.loads(content)
    if not isinstance(state, dict):
        raise ValueError(f"candidate store is not an object: {path}")
    state.setdefault("version", ADAPTIVE_WIKI_VERSION)
    state.setdefault("candidates", [])
    if not isinstance(state["candidates"], list):
        raise ValueError(f"candidate store candidates is not a list: {path}")
    return state


def candidate_from_learning(
    learning: dict[str, Any],
    *,
    review_path: pathlib.Path,
    now: str,
    source_hash: str,
) -> dict[str, Any]:
    kind = clean_text(learning.get("kind")) or "failure_pattern"
    scope = clean_text(learning.get("scope")) or "project"
    scope_ref = clean_text(learning.get("scope_ref")) or "twinpaper"
    evidence_refs = clean_list(learning.get("evidence_refs"))
    source_refs = [str(review_path), *evidence_refs]
    review_reason = (
        "Captured from deterministic TwinPaper post-run review; morning review "
        "must decide whether to promote, merge, rescope, or reject."
    )
    return {
        "id": f"wiki_candidate_{uuid.uuid4()}",
        "kind": kind,
        "scope": scope,
        "scope_ref": scope_ref,
        "agent_modes": clean_list(learning.get("agent_modes")),
        "claim": clean_text(learning.get("claim")),
        "suggested_ai_instruction": clean_text(learning.get("ai_instruction")),
        "human_summary": clean_text(learning.get("human_summary")),
        "evidence_refs": evidence_refs,
        "signal_kind": "repeated_failure",
        "origin": "background_review",
        "source_refs": clean_list(source_refs),
        "source_hashes": [f"sha256:{source_hash}"],
        "suggested_scope": {
            "scope": scope,
            "scope_ref": scope_ref,
        },
        "review_reason": review_reason,
        "occurrence_count": 1,
        "confidence": "inferred",
        "created_at": now,
        "updated_at": now,
        "last_seen_at": now,
    }


def merge_candidate(existing: dict[str, Any], incoming: dict[str, Any], now: str) -> None:
    existing["occurrence_count"] = max(1, int(existing.get("occurrence_count") or 1) + 1)
    existing["updated_at"] = now
    existing["last_seen_at"] = now
    for field in (
        "suggested_ai_instruction",
        "human_summary",
        "review_reason",
    ):
        if len(clean_text(incoming.get(field))) >= len(clean_text(existing.get(field))):
            existing[field] = incoming[field]
    for field in ("agent_modes", "evidence_refs", "source_refs", "source_hashes", "core_tags", "proposed_tags"):
        merged = clean_list(existing.get(field)) + [
            value for value in clean_list(incoming.get(field)) if value not in clean_list(existing.get(field))
        ]
        existing[field] = merged
    existing["signal_kind"] = incoming["signal_kind"]
    existing["origin"] = incoming["origin"]
    existing["confidence"] = incoming["confidence"]


def candidate_key(candidate: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        clean_text(candidate.get("kind")),
        clean_text(candidate.get("scope")),
        clean_text(candidate.get("scope_ref")),
        normalize_key(clean_text(candidate.get("claim"))),
    )


def ingest(review_path: pathlib.Path, candidate_store_path: pathlib.Path, dry_run: bool) -> dict[str, Any]:
    review = load_json(review_path)
    learning_candidates = review.get("learning_candidates", [])
    if not isinstance(learning_candidates, list):
        learning_candidates = []
    now = utc_now()
    source_hash = review_hash(review_path)
    state = load_candidate_state(candidate_store_path)
    existing_by_key = {candidate_key(candidate): candidate for candidate in state["candidates"]}
    recorded: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for learning in learning_candidates:
        if not isinstance(learning, dict):
            skipped.append({"reason": "candidate_not_object"})
            continue
        candidate = candidate_from_learning(
            learning,
            review_path=review_path,
            now=now,
            source_hash=source_hash,
        )
        if not candidate["claim"] or not candidate["suggested_ai_instruction"]:
            skipped.append(
                {
                    "reason": "missing_claim_or_instruction",
                    "claim": candidate["claim"],
                }
            )
            continue
        key = candidate_key(candidate)
        if key in existing_by_key:
            merge_candidate(existing_by_key[key], candidate, now)
            updated.append(
                {
                    "id": existing_by_key[key].get("id"),
                    "claim": existing_by_key[key].get("claim"),
                    "occurrence_count": existing_by_key[key].get("occurrence_count"),
                }
            )
        else:
            state["candidates"].append(candidate)
            existing_by_key[key] = candidate
            recorded.append({"id": candidate["id"], "claim": candidate["claim"]})

    if not dry_run:
        write_json(candidate_store_path, state)
    return {
        "generated_at": now,
        "dry_run": dry_run,
        "review_path": str(review_path),
        "candidate_store_path": str(candidate_store_path),
        "learning_candidates": len(learning_candidates),
        "recorded": recorded,
        "updated": updated,
        "skipped": skipped,
        "summary": {
            "recorded": len(recorded),
            "updated": len(updated),
            "skipped": len(skipped),
            "candidate_store_total": len(state["candidates"]),
        },
    }


def main() -> None:
    args = parse_args()
    review_path = args.review.expanduser().resolve()
    if not review_path.exists():
        raise SystemExit(f"review file does not exist: {review_path}")
    candidate_store_path = profile_dir(args) / "adaptive_wiki_candidates.json"
    report = ingest(review_path, candidate_store_path, args.dry_run)
    if args.out:
        write_json(args.out, report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
