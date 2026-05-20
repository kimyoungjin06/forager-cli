#!/usr/bin/env python3
"""Review a prepared TwinPaper Offdesk workload manifest.

This is a deterministic, read-only review artifact generator. It inspects the
exact `prepared_task.json` that would be enqueued, checks the role-gate summary
and workload safety contract, and emits a review artifact that
`prepare_twinpaper_offdesk_task.py` can use as preflight evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CASE_NAME = "workload_manifest_review"
ALLOWED_LONG_RUNNER = "local-tmux"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, help="Write review JSON artifact here.")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def as_bool(value: Any) -> bool:
    return value is True


def path_under(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def contains_secret_like_text(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False).lower()
    markers = ("token=", "api_key=", "apikey=", "password=", "secret=")
    return any(marker in text for marker in markers) or re.search(r"\bsk-[a-z0-9]{12,}", text) is not None


def evaluate_manifest(manifest_path: pathlib.Path, manifest: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    missing_evidence: list[str] = []
    counterarguments: list[str] = []

    safety = manifest.get("safety", {})
    if not isinstance(safety, dict):
        blockers.append("missing_safety_block")
        safety = {}
    if not as_bool(safety.get("repo_read_only")):
        blockers.append("repo_read_only_not_confirmed")
    if not as_bool(safety.get("writes_only_under_out_dir")):
        blockers.append("writes_only_under_out_dir_not_confirmed")
    if safety.get("capability") != "dispatch.runtime":
        blockers.append("capability_not_dispatch_runtime")
    if not as_bool(safety.get("approval_required_before_dispatch")):
        blockers.append("dispatch_approval_not_required")
    if not as_bool(safety.get("clean_role_gate_required")):
        blockers.append("clean_role_gate_not_required")
    if not as_bool(safety.get("separate_review_artifact_required")):
        blockers.append("separate_review_not_required")

    runner = safety.get("runner")
    duration_minutes = float(manifest.get("duration_minutes") or 0)
    if duration_minutes >= 5 and runner != ALLOWED_LONG_RUNNER:
        blockers.append("long_workload_not_using_local_tmux")

    role_gate = manifest.get("preflight", {}).get("role_gate", {})
    if not isinstance(role_gate, dict):
        blockers.append("missing_role_gate_summary")
        role_gate = {}
    if not as_bool(role_gate.get("ready")):
        blockers.append("role_gate_not_ready")
    quality_gate = role_gate.get("quality_gate", {})
    if not isinstance(quality_gate, dict) or not as_bool(quality_gate.get("ready_for_long_workload")):
        blockers.append("role_gate_not_ready_for_long_workload")
    if role_gate.get("failed") not in (0, None):
        blockers.append("role_gate_has_failures")
    if role_gate.get("failure_category_counts"):
        blockers.append("role_gate_failure_categories_present")

    repo = pathlib.Path(str(manifest.get("repo", "")))
    if not repo.exists():
        blockers.append("repo_path_missing")
    out_dir = pathlib.Path(str(manifest.get("out_dir", "")))
    if not out_dir.exists():
        blockers.append("out_dir_missing")

    workload_command = manifest.get("workload_command")
    if not isinstance(workload_command, list):
        blockers.append("workload_command_not_list")
        workload_command = []
    command_text = " ".join(str(item) for item in workload_command)
    if "offdesk_twinpaper_autonomy_workload.py" not in command_text:
        blockers.append("workload_command_missing_twinpaper_script")
    if "--out-dir" not in workload_command or str(out_dir) not in workload_command:
        blockers.append("workload_command_out_dir_mismatch")
    if "--evidence-bundle" not in workload_command:
        blockers.append("workload_command_missing_evidence_bundle")
    if "--evidence-review" not in workload_command:
        blockers.append("workload_command_missing_evidence_review")

    wrapper_path = pathlib.Path(str(manifest.get("workload_wrapper", "")))
    if not wrapper_path.exists():
        blockers.append("workload_wrapper_missing")
    elif not path_under(wrapper_path, out_dir):
        blockers.append("workload_wrapper_outside_out_dir")

    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict):
        blockers.append("artifacts_block_missing")
        artifacts = {}
    for key in ("prepared_task", "preflight", "runner_log", "result", "report", "evidence_bundle", "evidence_review"):
        artifact_path = artifacts.get(key)
        if not artifact_path:
            missing_evidence.append(f"artifact_path_missing:{key}")
            continue
        normalized_artifact_path = pathlib.Path(str(artifact_path))
        if not path_under(normalized_artifact_path, out_dir):
            blockers.append(f"artifact_outside_out_dir:{key}")
        if key in {"evidence_bundle", "evidence_review"} and not normalized_artifact_path.exists():
            blockers.append(f"artifact_missing:{key}")

    evidence_review_path = artifacts.get("evidence_review")
    if evidence_review_path:
        try:
            evidence_review = load_json(pathlib.Path(str(evidence_review_path)))
        except (OSError, json.JSONDecodeError) as error:
            blockers.append("evidence_review_unreadable")
            counterarguments.append(repr(error))
        else:
            if not isinstance(evidence_review, dict):
                blockers.append("evidence_review_not_object")
            elif not (
                evidence_review.get("kind") == "evidence_bundle_review"
                and evidence_review.get("passed") is True
                and evidence_review.get("decision") == "sufficient"
            ):
                blockers.append("evidence_review_not_sufficient")

    enqueue_args = manifest.get("enqueue_args")
    if not isinstance(enqueue_args, list):
        blockers.append("enqueue_args_not_list")
        enqueue_args = []
    enqueue_text = " ".join(str(item) for item in enqueue_args)
    if "dispatch.runtime" not in enqueue_text:
        blockers.append("enqueue_missing_dispatch_runtime")
    if "--agent-mode" not in enqueue_args:
        blockers.append("enqueue_missing_agent_mode")
    if "--provider-id" not in enqueue_args or "ollama" not in enqueue_args:
        blockers.append("enqueue_missing_ollama_provider")

    if contains_secret_like_text(manifest):
        blockers.append("manifest_contains_secret_like_text")

    if duration_minutes > 45:
        warnings.append("duration_above_recommended_45_minutes")
    if int(manifest.get("max_iterations") or 0) <= 0:
        blockers.append("max_iterations_not_positive")
    if not manifest.get("model"):
        blockers.append("model_missing")

    counterarguments.append(
        "The role gate only tests prompt behavior on fixture episodes; the workload can still fail on real TwinPaper context."
    )
    counterarguments.append(
        "The review confirms enqueue readiness, not approval to execute; dispatch.runtime approval remains separate."
    )
    counterarguments.append(
        "The evidence bundle confirms available artifacts and current status only; the workload must still preserve full model responses for quality review."
    )
    if missing_evidence:
        counterarguments.append("Some artifact paths are absent or incomplete in the prepared manifest.")

    decision = "blocked" if blockers else "needs_approval"
    return {
        "decision": decision,
        "passed": not blockers,
        "reviewed_artifact": str(manifest_path),
        "blockers": blockers,
        "warnings": warnings,
        "missing_evidence": missing_evidence,
        "counterarguments": counterarguments,
        "safety_gates": [
            "repo_read_only",
            "writes_only_under_out_dir",
            "clean_role_gate",
            "separate_manifest_review",
            "deterministic_evidence_bundle_review",
            "local_tmux_for_long_workload",
        ],
        "approval_gates": [
            "dispatch.runtime approval before tick launches the workload",
            "operator review of prepared_task.json and preflight.json",
        ],
        "next_agent_mode": "operator_approval" if not blockers else "planning",
    }


def default_out_path(manifest_path: pathlib.Path) -> pathlib.Path:
    return manifest_path.parent / "workload_review" / "results.json"


def write_markdown(path: pathlib.Path, result: dict[str, Any]) -> None:
    lines = [
        "# TwinPaper Workload Review",
        "",
        f"- reviewed_artifact: `{result['reviewed_artifact']}`",
        f"- decision: `{result['review_stage_decision']}`",
        f"- passed: `{result['passed']}`",
        f"- next_agent_mode: `{result['next_agent_mode']}`",
        "",
        "## Blockers",
        "",
        *(f"- {item}" for item in result["blockers"]),
        "",
        "## Warnings",
        "",
        *(f"- {item}" for item in result["warnings"]),
        "",
        "## Counterarguments",
        "",
        *(f"- {item}" for item in result["counterarguments"]),
        "",
    ]
    write_text(path, "\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.expanduser().resolve()
    out_path = (args.out or default_out_path(manifest_path)).expanduser().resolve()
    try:
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ValueError("manifest JSON is not an object")
        evaluation = evaluate_manifest(manifest_path, manifest)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        evaluation = {
            "decision": "blocked",
            "passed": False,
            "reviewed_artifact": str(manifest_path),
            "blockers": ["manifest_unreadable"],
            "warnings": [],
            "missing_evidence": [],
            "counterarguments": [repr(error)],
            "safety_gates": [],
            "approval_gates": [],
            "next_agent_mode": "planning",
        }

    result = {
        "case": CASE_NAME,
        "passed": evaluation["passed"],
        "review_stage_required": True,
        "review_stage_present": True,
        "review_stage_decision": evaluation["decision"],
        "reviewed_artifact": evaluation["reviewed_artifact"],
        "blockers": evaluation["blockers"],
        "blocking_reasons": evaluation["blockers"],
        "warnings": evaluation["warnings"],
        "missing_evidence": evaluation["missing_evidence"],
        "counterarguments": evaluation["counterarguments"],
        "safety_gates": evaluation["safety_gates"],
        "approval_gates": evaluation["approval_gates"],
        "next_agent_mode": evaluation["next_agent_mode"],
    }
    summary = {
        "total": 1,
        "passed": 1 if result["passed"] else 0,
        "failed": 0 if result["passed"] else 1,
        "decision_counts": {result["review_stage_decision"]: 1},
        "ready_for_enqueue": result["passed"] and result["review_stage_decision"] == "needs_approval",
    }
    artifact = {
        "created_at": utc_now(),
        "kind": "workload_manifest_review",
        "manifest": str(manifest_path),
        "review_stage_decision": result["review_stage_decision"],
        "blocking_reasons": result["blocking_reasons"],
        "summary": summary,
        "results": [result],
        "passed": result["passed"],
    }
    write_text(out_path, json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")
    write_markdown(out_path.with_name("REVIEW.md"), result)
    print(json.dumps({"passed": artifact["passed"], "decision": result["review_stage_decision"], "out": str(out_path)}, ensure_ascii=False))
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
