#!/usr/bin/env python3
"""Session retrospective distiller: operator corrections -> wiki candidates.

Reads a local session transcript (Claude Code JSONL), pairs each operator
message with the assistant activity just before it, and asks a local
Ollama-compatible model to extract durable lessons -- corrections, boundaries,
and preferences that would stop the assistant repeating a mistake. Survivors
are recorded (only with --record) as unpromoted adaptive-wiki candidates with
origin=background_review; failure patterns carry signal_kind=operator_correction
so they feed first-class correction records and `wiki evaluate-recurrence`.

Benchmark note (Hermes): Hermes captures corrections only if the model
remembers to call its memory tool mid-turn, with no structure, no provenance,
and exact-string dedup. This distiller is the post-hoc alternative: batch
extraction, verbatim operator-quote verification (fuzzy-repaired against the
real messages, so fabricated provenance rejects), governed candidate review,
and occurrence merging. It NEVER promotes.

Typical use (session id defaults to the newest transcript in the project dir):

  OFFDESK_LLM_BASE_URL=http://<gpu-server>:11434 \
  OFFDESK_LLM_MODEL=qwen3-coder:30b \
  scripts/offdesk_wiki_session_distiller.py \
    --transcript ~/.claude/projects/<proj>/<session>.jsonl \
    --profile forager-ops --scope user_global [--record]
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
    CLAIM_HARD_CAP,
    LLM_USAGE,
    VALID_FACETS,
    VALID_KINDS,
    VALID_MODES,
    call_ollama,
    normalize,
    parse_candidates,
    repair_quote,
    repo_root,
)

MIN_OPERATOR_QUOTE = 10
SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{8,})"),
    re.compile(r"((?:token|password|secret|api_key|apikey)\s*[=:]\s*\S+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+[A-Za-z0-9._-]{8,})"),
]

RUBRIC = """You review numbered exchanges from a work session between an OPERATOR (human, often writing Korean) and an AI assistant working on software/research projects. Extract durable lessons that would prevent the assistant from repeating a mistake or making the operator say the same thing twice.

Extract ONLY:
- corrections: the operator redirects, rejects, or points out a wrong assumption or mistake by the assistant;
- durable boundaries or preferences the operator states;
- decisions that should persist beyond this session;
- environment/tooling gotchas resolved in the session: a computation or tool that failed until a specific setting changed (thread counts, memory limits, env vars, version pins) -- capture the exact failure AND the working setting.
SKIP pure approvals ("okay", "proceed"), one-time task instructions, and anything with no future value.

Each candidate:
- kind: failure_pattern (mistake to avoid) | preference | policy_rule | procedure | fact;
- facet: ops (how to operate/run tools) | research | product;
- claim: <=120 chars, English, one durable declarative lesson;
- ai_instruction: <=200 chars imperative, or "";
- agent_modes: subset of [planning, development, analysis, writing, critique, review, maintenance], or [];
- operator_quote: a VERBATIM fragment (>=10 chars, exact characters, Korean allowed) copied from an OPERATOR message that grounds the lesson;
- exchange: the exchange number it came from.

Return STRICT JSON only: {"candidates": [...]}. At most {max_candidates} candidates; fewer, well-chosen lessons beat many weak ones."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", required=True, type=pathlib.Path, help="Session JSONL transcript.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--scope", default="user_global", choices=["project", "user_global", "artifact_kind", "session"])
    parser.add_argument("--scope-ref", default="")
    parser.add_argument("--domain-tag", default="", help="domain/<x> tag for recorded candidates.")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=4096)
    parser.add_argument("--timeout-sec", type=int, default=600)
    parser.add_argument("--max-candidates", type=int, default=10)
    parser.add_argument("--chunk-chars", type=int, default=18000)
    parser.add_argument("--assistant-tail-chars", type=int, default=320)
    parser.add_argument("--record", action="store_true", help="Record verified candidates (origin background_review). Default dry-run.")
    parser.add_argument("--forager-bin", default=str(repo_root() / "target" / "debug" / "forager"))
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()
    import os
    args.base_url = args.base_url or os.environ.get("OFFDESK_LLM_BASE_URL", "http://127.0.0.1:11434")
    args.model = args.model or os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder:30b")
    if args.scope != "user_global" and not args.scope_ref.strip():
        parser.error("--scope-ref is required unless --scope user_global")
    return args


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def extract_exchanges(transcript: pathlib.Path, assistant_tail_chars: int) -> list[dict]:
    """Pair each real operator message with the assistant text just before it."""
    exchanges: list[dict] = []
    last_assistant = ""
    with transcript.open() as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get("type")
            message = record.get("message") or {}
            if kind == "assistant":
                text = normalize(message_text(message.get("content")))
                if text:
                    last_assistant = text
                continue
            if kind != "user":
                continue
            text = normalize(message_text(message.get("content")))
            # Skip tool results, harness wrappers, and empty messages. Long
            # "user" records are almost always injected skill/system prompts
            # rendered as user messages, not the human operator typing.
            if not text or text.startswith("<") or len(text) > 900:
                continue
            exchanges.append(
                {
                    "n": len(exchanges) + 1,
                    "assistant_before": redact(last_assistant[-assistant_tail_chars:]),
                    "operator": redact(text[:600]),
                }
            )
    return exchanges


def chunk_exchanges(exchanges: list[dict], chunk_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for exchange in exchanges:
        cost = len(exchange["assistant_before"]) + len(exchange["operator"]) + 40
        if current and size + cost > chunk_chars:
            chunks.append(current)
            current, size = [], 0
        current.append(exchange)
        size += cost
    if current:
        chunks.append(current)
    return chunks


def render_chunk(chunk: list[dict]) -> str:
    lines = []
    for e in chunk:
        lines.append(f"[{e['n']}] ASSISTANT(before): {e['assistant_before']}")
        lines.append(f"[{e['n']}] OPERATOR: {e['operator']}")
        lines.append("")
    return "\n".join(lines)


def verify(raw: dict, operator_norm: str, operator_lines: list[str]) -> tuple[dict | None, str, bool]:
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in VALID_KINDS:
        return None, f"invalid kind: {kind!r}", False
    facet = str(raw.get("facet") or "").strip().lower()
    if facet not in VALID_FACETS:
        return None, f"invalid facet: {facet!r}", False
    claim = normalize(str(raw.get("claim") or ""))
    if not claim:
        return None, "empty claim", False
    if len(claim) > CLAIM_HARD_CAP:
        return None, f"claim too long ({len(claim)} > {CLAIM_HARD_CAP})", False
    quote = normalize(str(raw.get("operator_quote") or ""))
    if len(quote) < MIN_OPERATOR_QUOTE:
        return None, f"operator quote too short ({len(quote)} chars)", False
    repaired = False
    if quote not in operator_norm:
        fixed = repair_quote(quote, operator_lines)
        if fixed is None or normalize(fixed) not in operator_norm:
            return None, "operator quote not found in transcript", False
        quote, repaired = normalize(fixed), True
    modes = [str(m).strip().lower() for m in (raw.get("agent_modes") or []) if str(m).strip().lower() in VALID_MODES]
    return {
        "kind": kind,
        "facet": facet,
        "claim": claim,
        "ai_instruction": normalize(str(raw.get("ai_instruction") or ""))[:200],
        "agent_modes": modes,
        "operator_quote": quote[:200],
        "exchange": int(raw.get("exchange") or 0),
    }, "", repaired


def record_candidate(args: argparse.Namespace, session_label: str, cand: dict) -> tuple[bool, str]:
    signal = "operator_correction" if cand["kind"] == "failure_pattern" else "explicit_preference"
    evidence = f"chat:{session_label}#ex{cand['exchange']}"
    command = [
        args.forager_bin, "-p", args.profile, "offdesk", "wiki", "record-candidate",
        "--kind", cand["kind"], "--scope", args.scope,
        "--claim", cand["claim"],
        "--origin", "background_review",
        "--signal-kind", signal,
        "--confidence", "inferred",
        "--evidence-ref", evidence,
        "--core-tag", f"facet/{cand['facet']}",
        "--core-tag", "source/chat",
        "--review-reason", f"session retrospective; operator said: \"{cand['operator_quote'][:120]}\"",
    ]
    if args.scope != "user_global":
        command += ["--scope-ref", args.scope_ref]
    if args.domain_tag:
        command += ["--core-tag", f"domain/{args.domain_tag}"]
    if cand["ai_instruction"]:
        command += ["--ai-instruction", cand["ai_instruction"]]
    for mode in cand["agent_modes"]:
        command += ["--agent-mode", mode]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()[:200]
    return True, ""


def main() -> int:
    args = parse_args()
    exchanges = extract_exchanges(args.transcript, args.assistant_tail_chars)
    if not exchanges:
        print("no operator messages found in transcript", file=sys.stderr)
        return 1
    session_label = args.transcript.stem[:8]
    operator_lines = [e["operator"] for e in exchanges]
    operator_norm = normalize(" \n ".join(operator_lines))

    accepted: list[dict] = []
    rejected: list[dict] = []
    for chunk in chunk_exchanges(exchanges, args.chunk_chars):
        if len(accepted) >= args.max_candidates:
            break
        prompt = RUBRIC.replace("{max_candidates}", str(args.max_candidates)) + "\n\n--- EXCHANGES ---\n" + render_chunk(chunk)
        raw_response = call_ollama(args, prompt)
        raw_candidates, note = parse_candidates(raw_response)
        if not raw_candidates:
            rejected.append({"reason": note, "preview": raw_response[:160]})
            continue
        if note:
            rejected.append({"reason": note})
        for raw in raw_candidates:
            if len(accepted) >= args.max_candidates or not isinstance(raw, dict):
                continue
            clean, reason, repaired = verify(raw, operator_norm, operator_lines)
            if clean is None:
                rejected.append({"reason": reason, "claim": normalize(str(raw.get("claim") or ""))[:100]})
                continue
            if any(c["claim"].lower() == clean["claim"].lower() for c in accepted):
                rejected.append({"reason": "duplicate claim in run", "claim": clean["claim"][:100]})
                continue
            if repaired:
                clean["quote_repaired"] = True
            clean["recorded"] = False
            if args.record:
                ok, error = record_candidate(args, session_label, clean)
                clean["recorded"] = ok
                if not ok:
                    clean["record_error"] = error
            accepted.append(clean)

    print(f"{args.transcript.name}: {len(exchanges)} exchanges -> accepted {len(accepted)}, rejected {len(rejected)}"
          + ("" if args.record else " (dry-run)"))
    for c in accepted:
        marker = " (quote repaired)" if c.get("quote_repaired") else ""
        print(f"  + [{c['kind']}/{c['facet']}] {c['claim']}{marker}")
        print(f"      ↳ operator: \"{c['operator_quote'][:80]}\"")
    for r in rejected:
        print(f"  - rejected: {r['reason']}" + (f" | {r.get('claim','')}" if r.get("claim") else ""))

    if LLM_USAGE["calls"]:
        print(f"llm cost: {LLM_USAGE['calls']} call(s), {LLM_USAGE['prompt_tokens']}+{LLM_USAGE['output_tokens']} tokens, "
              f"{LLM_USAGE['duration_ms']/1000:.1f}s wall ({LLM_USAGE['duration_ms']/1000/max(1,len(accepted)):.1f}s per accepted lesson)")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema": "wiki_session_distiller_report.v1",
            "transcript": str(args.transcript),
            "model": args.model,
            "profile": args.profile,
            "exchanges": len(exchanges),
            "accepted": accepted,
            "rejected": rejected,
            "llm_usage": dict(LLM_USAGE),
        }
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report: {args.out}")
    if args.record and accepted:
        print(f"\n{sum(1 for c in accepted if c['recorded'])} candidate(s) recorded (unpromoted).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
