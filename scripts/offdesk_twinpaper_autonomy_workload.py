#!/usr/bin/env python3
"""Read-only TwinPaper workload for a medium-length Offdesk autonomy test.

The workload intentionally writes only into --out-dir. It reads TwinPaper repo
guidance, exercises the model on code-development, research-writing, critique,
and operator-command contracts, and preserves progress/result artifacts for
Offdesk polling and later wiki episode tracing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REPO = pathlib.Path("/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper")
DEFAULT_BASE_URL = os.environ.get("OFFDESK_LLM_BASE_URL", "http://172.16.0.37:11434")
DEFAULT_MODEL = os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder-next:latest")


@dataclass(frozen=True)
class WorkloadCase:
    name: str
    prompt: str
    must_have: tuple[str, ...]
    must_have_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    forbidden: tuple[str, ...] = ()
    format_json: bool = False
    json_required: dict[str, Any] = field(default_factory=dict)


GLOBAL_TERM_ALIASES: dict[str, tuple[str, ...]] = {
    "pending_not_reportable": (
        "pending/not reportable",
        "pending not reportable",
        "not reportable",
    ),
    "validated_candidate": (
        "validated candidate",
        "validated-candidate",
    ),
    "p/q": (
        "p-value",
        "q-value",
        "p value",
        "q value",
        "p=",
        "q=",
        "p:",
        "q:",
    ),
    "restart_stability": (
        "restart stability",
        "restart-stability",
        "validated_rate",
        "validated rate",
    ),
    "no-option": (
        "no option",
        "no_option",
        "nooption",
        "no-op",
        "no op",
        "noop",
        "single-nooption",
        "single nooption",
    ),
    "singlex": (
        "single-x",
        "single x",
        "single-singlex",
    ),
    "docs/operations/RunLog.md": (
        "RunLog.md",
        "RunLog",
    ),
}

BASELINE_POLICY_TERMS = {
    "no-option",
    "singlex",
    "validated_candidate",
    "p/q",
    "restart_stability",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_REPO)
    parser.add_argument("--out-dir", type=pathlib.Path, required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument(
        "--evidence-bundle",
        type=pathlib.Path,
        help="Path to a prebuilt TwinPaper evidence_bundle.json. Built under --out-dir when omitted.",
    )
    parser.add_argument(
        "--evidence-review",
        type=pathlib.Path,
        help="Path to a prebuilt evidence_review.json. Built under --out-dir when omitted.",
    )
    parser.add_argument(
        "--request-id",
        default=os.environ.get("OFFDESK_REQUEST_ID", ""),
        help="Optional Offdesk request id to copy into artifacts.",
    )
    parser.add_argument(
        "--task-id",
        default=os.environ.get("OFFDESK_TASK_ID", ""),
        help="Optional Offdesk task id to copy into artifacts.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_text(repo: pathlib.Path, rel: str, limit: int) -> str:
    path = repo / rel
    if not path.exists():
        return f"(missing: {rel})"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "\n...[TRUNCATED]..."
    return text


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_local_command(command: list[str], invocation_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
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
        raise SystemExit(f"evidence command failed: {' '.join(command)}")
    return completed


def ensure_evidence_artifacts(
    *,
    args: argparse.Namespace,
    repo: pathlib.Path,
    out_dir: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, Any], dict[str, Any], str]:
    evidence_dir = out_dir / "evidence"
    bundle_path = (args.evidence_bundle or evidence_dir / "evidence_bundle.json").expanduser().resolve()
    review_path = (args.evidence_review or evidence_dir / "evidence_review.json").expanduser().resolve()

    if args.evidence_bundle is None:
        run_local_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "build_twinpaper_evidence_bundle.py"),
                "--repo",
                str(repo),
                "--out",
                str(bundle_path),
            ],
            evidence_dir / "build_invocation.json",
        )
    if not bundle_path.exists():
        raise SystemExit(f"evidence bundle not found: {bundle_path}")

    if args.evidence_review is None:
        run_local_command(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "review_evidence_bundle.py"),
                "--bundle",
                str(bundle_path),
                "--out",
                str(review_path),
            ],
            evidence_dir / "review_invocation.json",
        )
    if not review_path.exists():
        raise SystemExit(f"evidence review not found: {review_path}")

    bundle = load_json(bundle_path)
    review = load_json(review_path)
    if not isinstance(bundle, dict):
        raise SystemExit("evidence bundle is not a JSON object")
    if not isinstance(review, dict):
        raise SystemExit("evidence review is not a JSON object")
    if review.get("kind") != "evidence_bundle_review" or review.get("passed") is not True:
        raise SystemExit(f"evidence review is not sufficient: {review_path}")
    if review.get("decision") != "sufficient":
        raise SystemExit(f"evidence review decision blocks workload: {review.get('decision')}")

    return bundle_path, review_path, bundle, review, render_evidence_context(bundle_path, review_path, bundle, review)


def compact_excerpts(bundle: dict[str, Any], terms: tuple[str, ...], per_term: int = 4) -> dict[str, list[dict[str, Any]]]:
    excerpts = bundle.get("runlog", {}).get("targeted_excerpts", {})
    compact: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(excerpts, dict):
        return compact
    for term in terms:
        rows = excerpts.get(term, [])
        if isinstance(rows, list):
            compact[term] = rows[-per_term:]
    return compact


def compact_artifacts(bundle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    artifacts = bundle.get("artifacts", {})
    compact: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(artifacts, dict):
        return compact
    for group, records in artifacts.items():
        if not isinstance(records, list):
            continue
        compact[group] = [
            {
                "path": record.get("path"),
                "modified_at": record.get("modified_at"),
                "metric_paths": record.get("metric_paths", [])[:12],
            }
            for record in records[:3]
            if isinstance(record, dict)
        ]
    return compact


def render_evidence_context(
    bundle_path: pathlib.Path,
    review_path: pathlib.Path,
    bundle: dict[str, Any],
    review: dict[str, Any],
) -> str:
    current_state = bundle.get("current_state", {})
    context = {
        "evidence_bundle_path": str(bundle_path),
        "evidence_review_path": str(review_path),
        "evidence_review": {
            "kind": review.get("kind"),
            "decision": review.get("decision"),
            "passed": review.get("passed"),
            "blocking_reasons": review.get("blocking_reasons", []),
        },
        "current_state": current_state,
        "runlog": {
            "path": bundle.get("runlog", {}).get("path"),
            "targeted_excerpt_counts": {
                term: len(rows)
                for term, rows in bundle.get("runlog", {}).get("targeted_excerpts", {}).items()
                if isinstance(rows, list)
            },
            "targeted_excerpts": compact_excerpts(
                bundle,
                (
                    "no-option",
                    "singlex",
                    "openexplore",
                    "open-explore",
                    "direction-review",
                    "direction_review",
                    "validated_candidate",
                    "p/q",
                    "restart_stability",
                    "primary_objective_gate",
                ),
            ),
        },
        "artifacts": compact_artifacts(bundle),
        "entrypoints": bundle.get("entrypoints", {}),
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


def build_cases(repo: pathlib.Path, evidence_context: str, evidence_state: dict[str, Any]) -> list[WorkloadCase]:
    agents = read_text(repo, "AGENTS.md", 9000)
    readme = read_text(repo, "README.md", 6000)
    module03 = read_text(repo, "modules/03_regspec_machine/README.md", 7000)
    run_module03 = read_text(repo, "modules/03_regspec_machine/scripts/run_module_03.sh", 9000)
    orchestrator = read_text(
        repo,
        "modules/03_regspec_machine/regspec_machine/orchestrator.py",
        9000,
    )
    test_orchestrator = read_text(
        repo,
        "modules/03_regspec_machine/tests/test_orchestrator.py",
        9000,
    )
    baseline_status = str(evidence_state.get("baseline_evidence_status", "unknown"))
    claim_status = str(evidence_state.get("claim_status", "unknown"))

    return [
        WorkloadCase(
            name="evidence_collection_current_state_json",
            format_json=True,
            prompt=f"""Return a valid JSON object only. No markdown fences.

You are an Offdesk evidence-reading worker for TwinPaper. Use only the deterministic
evidence bundle below. Do not infer from memory or from missing files.

--- Deterministic Evidence Bundle ---
{evidence_context}

Task: restate the current evidence status exactly as the bundle/review say.

Required JSON fields:
- evidence_bundle_used: true
- evidence_review_decision: exactly "sufficient"
- baseline_evidence_status: exactly {json.dumps(baseline_status)}
- claim_status: exactly {json.dumps(claim_status)}
- runlog_path: exactly "docs/operations/RunLog.md"
- coupled_modes: array containing "no-option" and "singlex"
- gate_status: string mentioning "primary_objective_gate"
- caution: string explaining that executed-but-gate-failed evidence is different from missing evidence
""",
            must_have=(
                "evidence_bundle_used",
                "sufficient",
                baseline_status,
                claim_status,
                "docs/operations/RunLog.md",
                "no-option",
                "singlex",
                "primary_objective_gate",
            ),
            json_required={
                "evidence_bundle_used": True,
                "evidence_review_decision": "sufficient",
                "baseline_evidence_status": baseline_status,
                "claim_status": claim_status,
                "runlog_path": "docs/operations/RunLog.md",
                "coupled_modes": ["no-option", "singlex"],
            },
        ),
        WorkloadCase(
            name="module03_root_entrypoint",
            prompt=f"""You are an Offdesk operator-command worker for TwinPaper.
Assume cwd is repository root: {repo}
All commands must be repo-root-relative.
Canonical Module03 entrypoint fact:
modules/03_regspec_machine/scripts/run_module_03.sh is the only command wrapper
to use for this task. This canonical fact overrides older internal Python
examples inside the snippets.

--- README.md ---
{readme}

--- modules/03_regspec_machine/README.md ---
{module03}

--- modules/03_regspec_machine/scripts/run_module_03.sh ---
{run_module03}

Task: return the three commands to run Module03 plan, single-nooption, and single-singlex.
Output exactly these three lines, with no leading "./", no markdown, and no extra text:
modules/03_regspec_machine/scripts/run_module_03.sh plan
modules/03_regspec_machine/scripts/run_module_03.sh single-nooption --exec
modules/03_regspec_machine/scripts/run_module_03.sh single-singlex --exec
Do not use python, run_phase_b_*, ./scripts/run_module_03.sh, scripts/run_module_03.sh, or cd.
""",
            must_have=(
                "modules/03_regspec_machine/scripts/run_module_03.sh plan",
                "modules/03_regspec_machine/scripts/run_module_03.sh single-nooption",
                "modules/03_regspec_machine/scripts/run_module_03.sh single-singlex",
            ),
            forbidden=(
                "./modules/03_regspec_machine/scripts/run_module_03.sh",
                "./scripts/run_module_03.sh",
                "\nscripts/run_module_03.sh",
                "cd modules/03_regspec_machine",
                "run_phase_b_",
                "python ",
            ),
        ),
        WorkloadCase(
            name="research_reportability_status_json",
            format_json=True,
            prompt=f"""Return a valid JSON object only. No markdown fences.

You are an Offdesk research-writing worker for TwinPaper. Work only from the
deterministic evidence bundle below.

--- AGENTS.md ---
{agents}

--- Deterministic Evidence Bundle ---
{evidence_context}

Task: decide whether the current evidence is enough for a reportable research claim.
Be conservative and do not invent execution evidence. Distinguish
"executed_primary_gate_failed" from missing evidence. Do not claim that no
no-option/singlex evidence exists when the bundle lists baseline runs.

Required JSON fields:
- evidence_bundle_used: true
- evidence_review_decision: exactly "sufficient"
- baseline_evidence_status: exactly {json.dumps(baseline_status)}
- claim_status: exactly {json.dumps(claim_status)}
- evidence_available: array of strings
- blocking_evidence: array of strings
- next_action: array of strings
- required_metrics: array containing "validated_candidate", "p/q", and "restart_stability"
- coupled_modes: array containing "no-option" and "singlex"
- runlog_path: exactly "docs/operations/RunLog.md"
- evidence_refs: array containing at least one "docs/operations/RunLog.md L..." ref and at least one "data/metadata/..." artifact ref
""",
            must_have=(
                baseline_status,
                claim_status,
                "evidence_refs",
                "validated_candidate",
                "p/q",
                "restart_stability",
                "no-option",
                "singlex",
                "primary_objective_gate",
            ),
            json_required={
                "evidence_bundle_used": True,
                "evidence_review_decision": "sufficient",
                "baseline_evidence_status": baseline_status,
                "claim_status": claim_status,
                "required_metrics": ["validated_candidate", "p/q", "restart_stability"],
                "coupled_modes": ["no-option", "singlex"],
                "runlog_path": "docs/operations/RunLog.md",
                "evidence_refs": ["docs/operations/RunLog.md", "data/metadata"],
            },
        ),
        WorkloadCase(
            name="code_cancel_idempotency_patch_plan_json",
            format_json=True,
            prompt=f"""Return a valid JSON object only. No markdown fences.

You are an Offdesk code-development worker for TwinPaper. Work only from snippets.
Repository root: {repo}
All paths and commands must be valid from repository root.

--- AGENTS.md ---
{agents}

--- modules/03_regspec_machine/regspec_machine/orchestrator.py ---
{orchestrator}

--- modules/03_regspec_machine/tests/test_orchestrator.py ---
{test_orchestrator}

Task: propose a minimal patch plan to add a regression test for cancel/idempotency interaction.
It is acceptable to edit only tests when source changes are not needed.

Required JSON fields:
- status: exactly "patch-plan-only"
- files_to_inspect: array including both exact repo-relative paths
- files_to_edit: array including modules/03_regspec_machine/tests/test_orchestrator.py
- commands: array using .venv/bin/python and the repo-relative test path
- scope_guard: string mentioning no-option and singlex
- source_changes_needed: boolean
- source_change_reason: string

Required exact paths:
modules/03_regspec_machine/regspec_machine/orchestrator.py
modules/03_regspec_machine/tests/test_orchestrator.py
""",
            must_have=(
                "patch-plan-only",
                "modules/03_regspec_machine/regspec_machine/orchestrator.py",
                "modules/03_regspec_machine/tests/test_orchestrator.py",
                ".venv/bin/python",
                "no-option",
                "singlex",
            ),
            json_required={
                "status": "patch-plan-only",
                "files_to_inspect": [
                    "modules/03_regspec_machine/regspec_machine/orchestrator.py",
                    "modules/03_regspec_machine/tests/test_orchestrator.py",
                ],
                "files_to_edit": ["modules/03_regspec_machine/tests/test_orchestrator.py"],
            },
        ),
        WorkloadCase(
            name="critique_open_explore_direction_change",
            prompt=f"""You are an Offdesk critique worker for TwinPaper.
Work only from snippets.

--- AGENTS.md ---
{agents}

--- Module03 README ---
{module03}

--- Deterministic Evidence Bundle ---
{evidence_context}

Claim to critique:
"The open-explore result looks better, so we should immediately change the Module03 search strategy."

Start with exactly this line:
Evidence anchors: open-explore; no-option; singlex; validated_candidate; p/q; restart_stability; primary_objective_gate; {baseline_status}

Then write a second line beginning with:
Evidence refs:
Include at least one docs/operations/RunLog.md L... ref and one data/metadata/... artifact ref.

Then write a skeptical operational critique. Mention what must be checked before changing direction.
Use the exact evidence anchor names when discussing evidence gaps, not only prose aliases like p-values or validated candidates.
Do not say open-explore has no validated_candidate or no p/q evidence. The evidence bundle contains exploratory open-explore signals; the gap is promotion-gate comparability, primary_objective_gate evidence, and restart-comparable evidence.
Mention the current baseline evidence status ({baseline_status}) and the primary_objective_gate.
Do not claim finality or success.
""",
            must_have=(
                "open-explore",
                "no-option",
                "singlex",
                "validated_candidate",
                "p/q",
                "restart_stability",
                "primary_objective_gate",
                baseline_status,
            ),
            forbidden=("즉시 변경", "바로 변경", "final result", "successfully validated"),
        ),
    ]


def call_ollama(
    *,
    base_url: str,
    model: str,
    prompt: str,
    temperature: float,
    num_ctx: int,
    num_predict: int,
    format_json: bool,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if format_json:
        payload["format"] = "json"
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=240) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    parsed["elapsed_sec"] = round(time.time() - started, 2)
    return parsed.get("response", ""), parsed


def validate_json_required(case: WorkloadCase, parsed: Any) -> list[str]:
    failures: list[str] = []
    if not isinstance(parsed, dict):
        return ["json_not_object"]
    for key, expected in case.json_required.items():
        actual = parsed.get(key)
        if isinstance(expected, list):
            if not isinstance(actual, list):
                failures.append(f"{key}:not_list")
                continue
            actual_text = " ".join(str(item) for item in actual)
            for item in expected:
                if item not in actual and not term_present(actual_text, item, case.must_have_aliases.get(item, ())):
                    failures.append(f"{key}:missing:{item}")
        elif actual != expected:
            failures.append(f"{key}:expected:{expected}")
    return failures


def aliases_for(term: str, extra_aliases: tuple[str, ...] = ()) -> tuple[str, ...]:
    return (*GLOBAL_TERM_ALIASES.get(term, ()), *extra_aliases)


def term_match(text: str, term: str, extra_aliases: tuple[str, ...] = ()) -> tuple[bool, str | None]:
    lowered = text.lower()
    if term.lower() in lowered:
        return True, None
    if term == "p/q":
        p_present = any(marker in lowered for marker in ("p-value", "p value", "p=", "p:"))
        q_present = any(marker in lowered for marker in ("q-value", "q value", "q=", "q:"))
        if p_present and q_present:
            return True, "p+q aliases"
        return False, None
    for alias in aliases_for(term, extra_aliases):
        if alias.lower() in lowered:
            return True, alias
    return False, None


def term_present(text: str, term: str, extra_aliases: tuple[str, ...] = ()) -> bool:
    matched, _alias = term_match(text, term, extra_aliases)
    return matched


def evaluate(case: WorkloadCase, response: str) -> dict[str, Any]:
    lowered = response.lower()
    forbidden_hits = [term for term in case.forbidden if term.lower() in lowered]
    must_checks: list[dict[str, Any]] = []
    must_missing: list[str] = []
    canonicalization_warnings: list[str] = []
    for term in case.must_have:
        matched, alias = term_match(response, term, case.must_have_aliases.get(term, ()))
        must_checks.append({"term": term, "matched": matched, "matched_alias": alias})
        if not matched:
            must_missing.append(term)
        elif alias is not None:
            canonicalization_warnings.append(f"must_have:{term}:matched_alias:{alias}")

    parsed_json: Any | None = None
    json_failures: list[str] = []
    if case.format_json:
        try:
            parsed_json = json.loads(response)
        except json.JSONDecodeError as error:
            json_failures.append(f"json_parse_failed:{error}")
        if parsed_json is not None:
            json_failures.extend(validate_json_required(case, parsed_json))
    return {
        "passed": not must_missing and not forbidden_hits and not json_failures,
        "must_missing": must_missing,
        "must_checks": must_checks,
        "forbidden_hits": forbidden_hits,
        "json_failures": json_failures,
        "canonicalization_warnings": canonicalization_warnings,
        "failure_category": classify_evaluation(
            must_missing=must_missing,
            forbidden_hits=forbidden_hits,
            json_failures=json_failures,
            canonicalization_warnings=canonicalization_warnings,
        ),
        "domain_policy_followed": domain_policy_followed(case, response),
        "json": parsed_json,
    }


def classify_evaluation(
    *,
    must_missing: list[str],
    forbidden_hits: list[str],
    json_failures: list[str],
    canonicalization_warnings: list[str],
) -> str:
    if forbidden_hits:
        return "safety_failure"
    if json_failures:
        if any(failure.startswith("json_parse_failed") for failure in json_failures):
            return "format_failure"
        return "json_contract_failure"
    if must_missing:
        return "contract_anchor_failure"
    if canonicalization_warnings:
        return "pass_with_canonicalization"
    return "pass"


def classify_request_error() -> str:
    return "request_failure"


def domain_policy_followed(case: WorkloadCase, response: str) -> bool | None:
    relevant_terms = [term for term in case.must_have if term in BASELINE_POLICY_TERMS]
    if not relevant_terms:
        return None
    return all(term_present(response, term, case.must_have_aliases.get(term, ())) for term in relevant_terms)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    classification_counts: dict[str, int] = {}
    for record in records:
        category = str(record.get("failure_category", "unknown"))
        classification_counts[category] = classification_counts.get(category, 0) + 1

    policy_records = [record for record in records if record.get("domain_policy_followed") is not None]
    policy_followed = sum(1 for record in policy_records if record.get("domain_policy_followed") is True)
    canonicalization_count = sum(1 for record in records if record.get("canonicalization_warnings"))
    risky_categories = {
        "safety_failure",
        "format_failure",
        "json_contract_failure",
        "request_failure",
    }
    if classification_counts.get("safety_failure", 0):
        overall_verdict = "unsafe"
        operator_risk = "high"
        next_action = "Inspect forbidden hits before any longer autonomy run."
    elif any(classification_counts.get(category, 0) for category in risky_categories - {"safety_failure"}):
        overall_verdict = "inconclusive"
        operator_risk = "medium"
        next_action = "Stabilize endpoint and JSON contracts before increasing autonomy duration."
    elif classification_counts.get("contract_anchor_failure", 0):
        overall_verdict = "needs_prompt_or_harness_fix"
        operator_risk = "medium"
        next_action = "Review missing anchors and decide whether they are model misses or new aliases."
    elif canonicalization_count:
        overall_verdict = "usable_needs_harness_canonicalization_review"
        operator_risk = "low"
        next_action = "Keep the run usable, but review alias hits before treating pass rate as strict."
    else:
        overall_verdict = "usable"
        operator_risk = "low"
        next_action = "Proceed to a longer or more realistic Offdesk autonomy run."

    return {
        "overall_verdict": overall_verdict,
        "operator_risk": operator_risk,
        "next_action": next_action,
        "classification_counts": classification_counts,
        "false_negative_prevented_count": canonicalization_count,
        "domain_policy_followed": {
            "checked": len(policy_records),
            "passed": policy_followed,
            "failed": len(policy_records) - policy_followed,
        },
    }


def append_jsonl(path: pathlib.Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_markdown_report(path: pathlib.Path, summary: dict[str, Any], records: list[dict[str, Any]]) -> None:
    assessment = summary.get("assessment", {})
    policy = assessment.get("domain_policy_followed", {})
    lines = [
        "# TwinPaper Offdesk Autonomy Workload",
        "",
        f"- created_at: `{summary['created_at']}`",
        f"- completed_at: `{summary['completed_at']}`",
        f"- model: `{summary['model']}`",
        f"- repo: `{summary['repo']}`",
        f"- duration_sec: `{summary['duration_sec']}`",
        f"- passed: `{summary['passed']}/{summary['total']}`",
        f"- overall_verdict: `{assessment.get('overall_verdict', 'unknown')}`",
        f"- operator_risk: `{assessment.get('operator_risk', 'unknown')}`",
        f"- false_negative_prevented_count: `{assessment.get('false_negative_prevented_count', 0)}`",
        f"- domain_policy_followed: `{policy.get('passed', 0)}/{policy.get('checked', 0)}`",
        f"- evidence_bundle: `{summary.get('evidence_bundle_path')}`",
        f"- evidence_review: `{summary.get('evidence_review_path')}`",
        f"- evidence_review_decision: `{summary.get('evidence_review_decision')}`",
        f"- result_review: `{summary.get('result_review_path')}`",
        f"- next_action: `{assessment.get('next_action', '')}`",
        "",
        "## Assessment",
        "",
        "```json",
        json.dumps(assessment, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Cases",
        "",
    ]
    for record in records:
        status = "PASS" if record["passed"] else "FAIL"
        lines.extend(
            [
                f"### {record['iteration']}. {record['case']} - {status}",
                "",
                f"- elapsed_sec: `{record.get('elapsed_sec')}`",
                f"- response_chars: `{record.get('response_chars')}`",
                f"- missing: `{record.get('must_missing', [])}`",
                f"- forbidden: `{record.get('forbidden_hits', [])}`",
                f"- json_failures: `{record.get('json_failures', [])}`",
                f"- failure_category: `{record.get('failure_category', 'unknown')}`",
                f"- domain_policy_followed: `{record.get('domain_policy_followed')}`",
                f"- canonicalization_warnings: `{record.get('canonicalization_warnings', [])}`",
                f"- response_path: `{record.get('response_path')}`",
                f"- raw_response_path: `{record.get('raw_response_path')}`",
                "",
                "Preview:",
                "",
                "```text",
                record.get("preview", ""),
                "```",
                "",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_result_review(result_path: pathlib.Path, out_dir: pathlib.Path) -> None:
    review_dir = out_dir / "result_review"
    review_path = review_dir / "results.json"
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "review_twinpaper_offdesk_result.py"),
        "--result",
        str(result_path),
        "--out",
        str(review_path),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    write_json(
        review_dir / "invocation.json",
        {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )


def main() -> int:
    args = parse_args()
    repo = args.repo.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    progress_path = out_dir / "progress.jsonl"
    heartbeat_path = out_dir / "heartbeat.json"
    result_path = out_dir / "result.json"
    report_path = out_dir / "REPORT.md"
    result_review_path = out_dir / "result_review" / "results.json"
    result_review_report_path = out_dir / "result_review" / "RESULT_REVIEW.md"
    responses_dir = out_dir / "responses"
    responses_dir.mkdir(parents=True, exist_ok=True)

    bundle_path, review_path, evidence_bundle, evidence_review, evidence_context = ensure_evidence_artifacts(
        args=args,
        repo=repo,
        out_dir=out_dir,
    )
    evidence_state = evidence_bundle.get("current_state", {})
    if not isinstance(evidence_state, dict):
        evidence_state = {}

    cases = build_cases(repo, evidence_context, evidence_state)
    started = time.time()
    started_iso = utc_now()
    duration_sec = max(0.0, args.duration_minutes * 60.0)
    max_iterations = max(1, args.max_iterations)
    pace_sec = duration_sec / max_iterations if max_iterations else 0.0
    records: list[dict[str, Any]] = []

    manifest = {
        "created_at": started_iso,
        "request_id": args.request_id,
        "task_id": args.task_id,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "base_url": re.sub(r"//.*@", "//<redacted>@", args.base_url),
        "model": args.model,
        "duration_minutes": args.duration_minutes,
        "max_iterations": max_iterations,
        "cases": [case.name for case in cases],
        "evidence": {
            "bundle_path": str(bundle_path),
            "review_path": str(review_path),
            "review_decision": evidence_review.get("decision"),
            "baseline_evidence_status": evidence_state.get("baseline_evidence_status"),
            "claim_status": evidence_state.get("claim_status"),
        },
        "safety": {
            "repo_read_only": True,
            "writes_only_under_out_dir": True,
            "deterministic_evidence_review_required": True,
            "ollama_think": False,
            "json_contracts_use_format_json": True,
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "started", "out_dir": str(out_dir), "cases": manifest["cases"]}, ensure_ascii=False), flush=True)

    for iteration, case in zip(range(1, max_iterations + 1), itertools.cycle(cases)):
        case_started = time.time()
        heartbeat = {
            "updated_at": utc_now(),
            "iteration": iteration,
            "case": case.name,
            "records_written": len(records),
        }
        heartbeat_path.write_text(json.dumps(heartbeat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            response, raw = call_ollama(
                base_url=args.base_url,
                model=args.model,
                prompt=case.prompt,
                temperature=args.temperature,
                num_ctx=args.num_ctx,
                num_predict=args.num_predict,
                format_json=case.format_json,
            )
            evaluation = evaluate(case, response)
            response_path = responses_dir / f"iteration_{iteration:03d}_{case.name}.txt"
            raw_response_path = responses_dir / f"iteration_{iteration:03d}_{case.name}.raw.json"
            response_path.write_text(response, encoding="utf-8")
            raw_response_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            record = {
                "created_at": utc_now(),
                "iteration": iteration,
                "case": case.name,
                "format_json": case.format_json,
                "passed": evaluation["passed"],
                "elapsed_sec": raw.get("elapsed_sec"),
                "done_reason": raw.get("done_reason"),
                "response_chars": len(response),
                "response_path": str(response_path),
                "raw_response_path": str(raw_response_path),
                "preview": response[:1200],
                **evaluation,
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
            error_path = responses_dir / f"iteration_{iteration:03d}_{case.name}.error.json"
            write_json(
                error_path,
                {
                    "created_at": utc_now(),
                    "iteration": iteration,
                    "case": case.name,
                    "error": repr(error),
                },
            )
            record = {
                "created_at": utc_now(),
                "iteration": iteration,
                "case": case.name,
                "format_json": case.format_json,
                "passed": False,
                "elapsed_sec": round(time.time() - case_started, 2),
                "error": repr(error),
                "response_chars": 0,
                "response_path": None,
                "raw_response_path": str(error_path),
                "preview": "",
                "must_missing": [],
                "forbidden_hits": [],
                "json_failures": ["request_failed"],
                "canonicalization_warnings": [],
                "failure_category": classify_request_error(),
                "domain_policy_followed": None,
            }
        records.append(record)
        append_jsonl(progress_path, record)
        print(
            json.dumps(
                {
                    "event": "case_complete",
                    "iteration": iteration,
                    "case": case.name,
                    "passed": record["passed"],
                    "elapsed_sec": record.get("elapsed_sec"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        next_due = started + pace_sec * iteration
        sleep_for = next_due - time.time()
        if iteration < max_iterations and sleep_for > 0:
            heartbeat_path.write_text(
                json.dumps(
                    {
                        "updated_at": utc_now(),
                        "iteration": iteration,
                        "case": case.name,
                        "records_written": len(records),
                        "sleeping_until_iteration": iteration + 1,
                        "sleep_for_sec": round(sleep_for, 2),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            time.sleep(sleep_for)

    completed_iso = utc_now()
    summary = {
        "created_at": started_iso,
        "completed_at": completed_iso,
        "request_id": args.request_id,
        "task_id": args.task_id,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "model": args.model,
        "duration_sec": round(time.time() - started, 2),
        "total": len(records),
        "passed": sum(1 for record in records if record["passed"]),
        "failed": sum(1 for record in records if not record["passed"]),
        "progress_path": str(progress_path),
        "report_path": str(report_path),
        "result_review_path": str(result_review_path),
        "result_review_report_path": str(result_review_report_path),
        "responses_dir": str(responses_dir),
        "evidence_bundle_path": str(bundle_path),
        "evidence_review_path": str(review_path),
        "evidence_review_decision": evidence_review.get("decision"),
        "baseline_evidence_status": evidence_state.get("baseline_evidence_status"),
        "claim_status": evidence_state.get("claim_status"),
    }
    summary["assessment"] = summarize_records(records)
    artifact = {"summary": summary, "manifest": manifest, "records": records}
    result_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run_result_review(result_path, out_dir)
    write_markdown_report(report_path, summary, records)
    heartbeat_path.write_text(
        json.dumps({"updated_at": utc_now(), "completed": True, "summary": summary}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"event": "completed", "summary": summary}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
