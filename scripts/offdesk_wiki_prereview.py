#!/usr/bin/env python3
"""Local-LLM pre-review for adaptive-wiki candidates (review tier 2).

Reads a profile's unpromoted candidates and has a local Ollama-compatible model
judge, per candidate, whether the stored evidence quote actually supports the
claim: supported | inverted | unsupported | unclear. Machine-distilled claims
sometimes state the opposite of their own evidence; this cheap batch check
flags those before an expensive Council pass or an operator read.

Read-only: it never promotes, rejects, or edits. Output is a JSON report plus
an operator review packet (tier 1 artifact) with pre-filter flags and ready
apply commands. Review tiers:

  tier 1  operator reads the packet (cheapest high-quality reviewer)
  tier 2  this pre-filter kills obvious inversions cheaply (local LLM)
  tier 3  Claude Council for contested/high-stakes sets only

Usage:
  OFFDESK_LLM_BASE_URL=http://<gpu-server>:11434 \
  OFFDESK_LLM_MODEL=qwen3-coder:30b \
  scripts/offdesk_wiki_prereview.py --profile lrnm \
    [--packet out/packet.md] [--out out/report.json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from offdesk_wiki_distiller import (  # noqa: E402
    LLM_USAGE,
    call_ollama,
    normalize,
    parse_candidates,
    repo_root,
)

VERDICTS = {"supported", "inverted", "unsupported", "unclear"}

RUBRIC = """You are a strict fidelity judge for machine-distilled knowledge candidates. For each numbered candidate, decide whether its EVIDENCE quote actually supports its CLAIM.

Verdicts:
- supported: the quote clearly grounds the claim;
- inverted: the claim states the OPPOSITE of what the quote says (watch for negations like "말고", "안되고", "not", "instead of");
- unsupported: the quote is about something else, or the claim overreaches far beyond it;
- unclear: cannot tell from the quote alone.

The quote may be Korean while the claim is English; judge the meaning. Return STRICT JSON only:
{"verdicts": [{"n": 1, "verdict": "supported|inverted|unsupported|unclear", "reason": "<one short line>"}, ...]}
Give exactly one verdict per candidate."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--tag", default="", help="Only review candidates carrying this core tag (e.g. source/chat).")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=2048)
    parser.add_argument("--timeout-sec", type=int, default=600)
    parser.add_argument("--forager-bin", default=str(repo_root() / "target" / "debug" / "forager"))
    parser.add_argument("--packet", type=pathlib.Path, help="Write the operator review packet (markdown) here.")
    parser.add_argument("--out", type=pathlib.Path, help="Write the JSON report here.")
    args = parser.parse_args()
    import os
    args.base_url = args.base_url or os.environ.get("OFFDESK_LLM_BASE_URL", "http://127.0.0.1:11434")
    args.model = args.model or os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder:30b")
    return args


def load_candidates(args: argparse.Namespace) -> list[dict]:
    result = subprocess.run(
        [args.forager_bin, "-p", args.profile, "offdesk", "wiki", "candidates", "--json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print((result.stderr or result.stdout).strip()[:200], file=sys.stderr)
        sys.exit(1)
    candidates = json.loads(result.stdout)
    if args.tag:
        candidates = [c for c in candidates if args.tag in (c.get("core_tags") or [])]
    return candidates


def evidence_quote(candidate: dict) -> str:
    """The verbatim quote stored at record time lives in review_reason.

    No fallback to evidence refs: a reference is not evidence, and feeding
    pointer strings to the judge produced unverifiable "supported" verdicts
    (measured failure mode). Candidates without a stored quote are marked
    unclear before any LLM call."""
    reason = str(candidate.get("review_reason") or "")
    match = re.search(r'"(.+)"', reason, re.DOTALL)
    return normalize(match.group(1)) if match else ""


def render_batch(items: list[dict]) -> str:
    lines = []
    for item in items:
        lines.append(f"[{item['n']}] KIND: {item['kind']}")
        lines.append(f"[{item['n']}] CLAIM: {item['claim']}")
        lines.append(f"[{item['n']}] EVIDENCE: {item['quote']}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    candidates = load_candidates(args)
    if not candidates:
        print("no candidates to review")
        return 0
    items = []
    for i, c in enumerate(candidates, 1):
        items.append({
            "n": i,
            "id": c.get("id"),
            "kind": c.get("kind"),
            "claim": normalize(str(c.get("claim") or "")),
            "quote": evidence_quote(c),
        })

    quoteless = [i for i in items if len(i["quote"]) < 15]
    for item in quoteless:
        item["verdict"] = "unclear"
        item["reason"] = "no stored evidence quote; needs operator eyes"
    judged = [i for i in items if len(i["quote"]) >= 15]
    prompt = RUBRIC + "\n\n--- CANDIDATES ---\n" + render_batch(judged)
    raw = call_ollama(args, prompt) if judged else '{"verdicts": []}' 
    try:
        verdicts = {int(v.get("n")): v for v in json.loads(raw).get("verdicts", []) if isinstance(v, dict)}
    except (ValueError, TypeError):
        # salvage a truncated array the same way the distillers do
        salvaged, _ = parse_candidates(raw.replace('"verdicts"', '"candidates"', 1))
        verdicts = {int(v.get("n", 0)): v for v in salvaged if isinstance(v, dict)}

    flagged = 0
    for item in items:
        if item.get("verdict") == "unclear" and item.get("reason"):
            flagged += 1
            print(f"  ?  [unclear    ] {item['claim'][:70]} (no stored quote)")
            continue
        v = verdicts.get(item["n"], {})
        verdict = str(v.get("verdict") or "unclear").lower()
        item["verdict"] = verdict if verdict in VERDICTS else "unclear"
        item["reason"] = normalize(str(v.get("reason") or ""))[:160]
        if item["verdict"] != "supported":
            flagged += 1
        marker = {"supported": "+", "inverted": "!!", "unsupported": "!", "unclear": "?"}[item["verdict"]]
        print(f"  {marker:2} [{item['verdict']:11}] {item['claim'][:70]}")
        if item["verdict"] != "supported" and item["reason"]:
            print(f"       ↳ {item['reason'][:100]}")
    print(f"\n{len(items)} candidates: {len(items)-flagged} supported, {flagged} flagged for operator attention")
    if LLM_USAGE["calls"]:
        print(f"llm cost: {LLM_USAGE['calls']} call(s), {LLM_USAGE['prompt_tokens']}+{LLM_USAGE['output_tokens']} tokens, "
              f"{LLM_USAGE['duration_ms']/1000:.1f}s wall")

    if args.packet:
        args.packet.parent.mkdir(parents=True, exist_ok=True)
        order = {"inverted": 0, "unsupported": 1, "unclear": 2, "supported": 3}
        lines = [f"# Wiki candidate review packet -- {args.profile}", "",
                 "Pre-filtered by a local model (tier 2). Flags are advisory; you decide.",
                 "Apply per candidate:", "```bash",
                 f"forager -p {args.profile} offdesk wiki promote <id> --activation-mode context_only --by operator",
                 f"forager -p {args.profile} offdesk wiki reject  <id> --reason <text>",
                 "```", ""]
        for item in sorted(items, key=lambda x: order[x["verdict"]]):
            flag = {"inverted": "🔴 INVERTED", "unsupported": "🟠 UNSUPPORTED",
                    "unclear": "🟡 UNCLEAR", "supported": "🟢 supported"}[item["verdict"]]
            lines += [f"## {flag} `{item['id']}`",
                      f"- kind: {item['kind']}",
                      f"- claim: {item['claim']}",
                      f"- evidence: \"{item['quote'][:160]}\""]
            if item["reason"]:
                lines.append(f"- pre-filter note: {item['reason']}")
            lines.append("")
        args.packet.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"packet: {args.packet}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "schema": "wiki_prereview_report.v1",
            "profile": args.profile,
            "model": args.model,
            "items": items,
            "llm_usage": dict(LLM_USAGE),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
