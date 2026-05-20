#!/usr/bin/env python3
"""Prepare a queued Offdesk task for the TwinPaper autonomy workload."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shlex
import subprocess
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TWINPAPER_REPO = pathlib.Path("/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper")
DEFAULT_BASE_URL = os.environ.get("OFFDESK_LLM_BASE_URL", "http://172.16.0.37:11434")
DEFAULT_MODEL = os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder-next:latest")
DEFAULT_PROFILE = os.environ.get("OFFDESK_PROFILE", "twinpaper-adaptive-debug")
REVIEW_GENERATE_VALUES = {"generate", "auto"}
REVIEW_CASES = {"review_offdesk_stage_contract", "workload_manifest_review"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-key", default="twinpaper")
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_TWINPAPER_REPO)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument(
        "--role-gate-result",
        help=(
            "Path to a clean offdesk_role_llm_episode_harness results.json, or "
            "'latest' to use the newest target/offdesk-role-llm-episode-harness result."
        ),
    )
    parser.add_argument(
        "--review-artifact",
        help=(
            "Path to a review results.json, 'latest', or 'generate' to review "
            "the exact prepared_task.json in this run."
        ),
    )
    parser.add_argument(
        "--allow-preflight-blockers",
        action="store_true",
        help="Allow --enqueue even when the role gate or review preflight is missing or failed.",
    )
    parser.add_argument(
        "--runner",
        choices=("local-tmux", "local-background"),
        default="local-tmux",
        help="Runner backend for the offdesk task. local-tmux is the default for long workloads.",
    )
    parser.add_argument(
        "--forager-bin",
        type=pathlib.Path,
        default=REPO_ROOT / "target" / "debug" / "forager",
        help="Built forager binary to use when --enqueue is set.",
    )
    parser.add_argument(
        "--out-root",
        type=pathlib.Path,
        help="Override output root. Defaults to the selected profile directory.",
    )
    parser.add_argument("--enqueue", action="store_true", help="Actually enqueue the prepared task.")
    return parser.parse_args()


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def profile_dir(profile: str) -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "agent-of-empires" / "profiles" / profile


def load_json_file(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_result_file(root: pathlib.Path) -> pathlib.Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.glob("*/results.json") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def latest_review_artifact(profile: str) -> pathlib.Path | None:
    root = profile_dir(profile) / "wiki_llm_harness_runs"
    if not root.exists():
        return None
    candidates = sorted(
        (path for path in root.glob("*/results.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = load_json_file(path)
        except (OSError, json.JSONDecodeError):
            continue
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, list) and any(
            result.get("case") in REVIEW_CASES for result in results if isinstance(result, dict)
        ):
            return path
    workload_root = REPO_ROOT / "target" / "offdesk-workload-review-harness"
    latest_workload_review = latest_result_file(workload_root)
    if latest_workload_review:
        return latest_workload_review
    return None


def resolve_role_gate_result(value: str | None) -> pathlib.Path | None:
    if not value:
        return None
    if value == "latest":
        return latest_result_file(REPO_ROOT / "target" / "offdesk-role-llm-episode-harness")
    return pathlib.Path(value).expanduser().resolve()


def resolve_review_artifact(value: str | None, profile: str) -> pathlib.Path | None:
    if not value:
        return None
    if value in REVIEW_GENERATE_VALUES:
        return None
    if value == "latest":
        return latest_review_artifact(profile)
    return pathlib.Path(value).expanduser().resolve()


def summarize_role_gate_result(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "ready": False,
            "reason": "missing_role_gate_result",
        }
    summary: dict[str, Any] = {
        "path": str(path),
        "ready": False,
    }
    if not path.exists():
        summary["reason"] = "role_gate_result_not_found"
        return summary
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError) as error:
        summary["reason"] = "role_gate_result_unreadable"
        summary["error"] = repr(error)
        return summary
    if not isinstance(data, dict):
        summary["reason"] = "role_gate_result_not_object"
        return summary
    result_summary = data.get("summary", {})
    quality_gate = result_summary.get("quality_gate", {}) if isinstance(result_summary, dict) else {}
    ready = (
        data.get("passed") is True
        and isinstance(result_summary, dict)
        and result_summary.get("failed") == 0
        and isinstance(quality_gate, dict)
        and quality_gate.get("ready_for_long_workload") is True
    )
    summary.update(
        {
            "ready": ready,
            "passed": data.get("passed"),
            "total": result_summary.get("total") if isinstance(result_summary, dict) else None,
            "failed": result_summary.get("failed") if isinstance(result_summary, dict) else None,
            "pass_rate": result_summary.get("pass_rate") if isinstance(result_summary, dict) else None,
            "failure_category_counts": result_summary.get("failure_category_counts", {})
            if isinstance(result_summary, dict)
            else {},
            "quality_gate": quality_gate,
            "reason": "clean_role_gate" if ready else "role_gate_not_clean",
        }
    )
    return summary


def summarize_review_artifact(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "ready": False,
            "reason": "missing_review_artifact",
        }
    summary: dict[str, Any] = {
        "path": str(path),
        "ready": False,
    }
    if not path.exists():
        summary["reason"] = "review_artifact_not_found"
        return summary
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError) as error:
        summary["reason"] = "review_artifact_unreadable"
        summary["error"] = repr(error)
        return summary
    if not isinstance(data, dict):
        summary["reason"] = "review_artifact_not_object"
        return summary
    results = data.get("results")
    if not isinstance(results, list):
        summary["reason"] = "review_artifact_missing_results"
        return summary
    review_results = [
        result
        for result in results
        if isinstance(result, dict) and result.get("case") in REVIEW_CASES
    ]
    if not review_results:
        summary["reason"] = "review_case_missing"
        return summary
    failed_reviews = [result for result in review_results if result.get("passed") is not True]
    result_summary = data.get("summary", {})
    summary_failed = result_summary.get("failed") if isinstance(result_summary, dict) else None
    decisions = [
        result.get("review_stage_decision") for result in review_results if result.get("review_stage_decision")
    ]
    normalized_decisions = {str(decision).replace(" ", "_") for decision in decisions}
    allowed_decisions = {"proceed", "needs_approval"}
    contract_passed = not failed_reviews and (summary_failed in (0, None))
    decision_allows_enqueue = bool(normalized_decisions) and normalized_decisions.issubset(allowed_decisions)
    ready = contract_passed and decision_allows_enqueue
    if not contract_passed:
        reason = "review_case_not_clean"
    elif not decision_allows_enqueue:
        reason = "review_decision_blocks_enqueue"
    else:
        reason = "review_case_allows_enqueue"
    summary.update(
        {
            "ready": ready,
            "contract_passed": contract_passed,
            "decision_allows_enqueue": decision_allows_enqueue,
            "allowed_decisions": sorted(allowed_decisions),
            "review_case_count": len(review_results),
            "failed_review_case_count": len(failed_reviews),
            "summary_failed": summary_failed,
            "decisions": decisions,
            "reason": reason,
        }
    )
    return summary


def summarize_evidence_review(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "ready": False,
            "reason": "missing_evidence_review",
        }
    summary: dict[str, Any] = {
        "path": str(path),
        "ready": False,
    }
    if not path.exists():
        summary["reason"] = "evidence_review_not_found"
        return summary
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError) as error:
        summary["reason"] = "evidence_review_unreadable"
        summary["error"] = repr(error)
        return summary
    if not isinstance(data, dict):
        summary["reason"] = "evidence_review_not_object"
        return summary
    results = data.get("results")
    first_result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}
    ready = (
        data.get("kind") == "evidence_bundle_review"
        and data.get("passed") is True
        and data.get("decision") == "sufficient"
    )
    summary.update(
        {
            "ready": ready,
            "kind": data.get("kind"),
            "passed": data.get("passed"),
            "decision": data.get("decision"),
            "baseline_evidence_status": first_result.get("baseline_evidence_status"),
            "claim_status": first_result.get("claim_status"),
            "blocking_reasons": data.get("blocking_reasons", []),
            "reason": "evidence_review_sufficient" if ready else "evidence_review_not_sufficient",
        }
    )
    return summary


def build_preflight(
    role_gate: dict[str, Any],
    review_artifact: dict[str, Any],
    evidence_review: dict[str, Any],
    allow_blockers: bool,
) -> dict[str, Any]:
    blockers = []
    if not role_gate["ready"]:
        blockers.append(role_gate["reason"])
    if not review_artifact["ready"]:
        blockers.append(review_artifact["reason"])
    if not evidence_review["ready"]:
        blockers.append(evidence_review["reason"])
    return {
        "role_gate": role_gate,
        "review_artifact": review_artifact,
        "evidence_review": evidence_review,
        "blocking_reasons": blockers,
        "ready_for_enqueue": not blockers,
        "enqueue_allowed": not blockers or allow_blockers,
        "allow_preflight_blockers": allow_blockers,
    }


def role_gate_command(args: argparse.Namespace) -> list[str]:
    return [
        "scripts/offdesk_role_llm_episode_harness.py",
        "--model",
        args.model,
        "--base-url",
        args.base_url,
        "--temperature",
        "0.0",
        "--iterations",
        "5",
        "--max-budget",
        "2048",
        "--num-ctx",
        str(args.num_ctx),
    ]


def review_harness_command(args: argparse.Namespace) -> list[str]:
    return [
        "scripts/offdesk_wiki_llm_harness.py",
        "--case",
        "review_offdesk_stage_contract",
        "--prompt-profile",
        "contract_v3",
        "--iterations",
        "1",
        "--model",
        args.model,
        "--base-url",
        args.base_url,
        "--temperature",
        "0.2",
        "--max-budget",
        "4096",
        "--num-ctx",
        str(args.num_ctx),
    ]


def workload_review_command(manifest_path: pathlib.Path, out_path: pathlib.Path) -> list[str]:
    return [
        "scripts/offdesk_workload_review_harness.py",
        "--manifest",
        str(manifest_path),
        "--out",
        str(out_path),
    ]


def evidence_bundle_command(args: argparse.Namespace, out_path: pathlib.Path) -> list[str]:
    return [
        "scripts/build_twinpaper_evidence_bundle.py",
        "--repo",
        str(args.repo.resolve()),
        "--out",
        str(out_path),
    ]


def evidence_review_command(bundle_path: pathlib.Path, out_path: pathlib.Path) -> list[str]:
    return [
        "scripts/review_evidence_bundle.py",
        "--bundle",
        str(bundle_path),
        "--out",
        str(out_path),
    ]


def run_command(command: list[str], invocation_path: pathlib.Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    write_text(
        invocation_path,
        json.dumps(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return completed


def build_workload_command(
    args: argparse.Namespace,
    out_dir: pathlib.Path,
    request_id: str,
    task_id: str,
    evidence_bundle_path: pathlib.Path,
    evidence_review_path: pathlib.Path,
) -> list[str]:
    return [
        "python3",
        str(REPO_ROOT / "scripts" / "offdesk_twinpaper_autonomy_workload.py"),
        "--repo",
        str(args.repo.resolve()),
        "--out-dir",
        str(out_dir),
        "--base-url",
        args.base_url,
        "--model",
        args.model,
        "--duration-minutes",
        str(args.duration_minutes),
        "--max-iterations",
        str(args.max_iterations),
        "--temperature",
        str(args.temperature),
        "--num-ctx",
        str(args.num_ctx),
        "--num-predict",
        str(args.num_predict),
        "--evidence-bundle",
        str(evidence_bundle_path),
        "--evidence-review",
        str(evidence_review_path),
        "--request-id",
        request_id,
        "--task-id",
        task_id,
    ]


def build_enqueue_args(
    *,
    args: argparse.Namespace,
    out_dir: pathlib.Path,
    request_id: str,
    task_id: str,
    command: str,
) -> list[str]:
    return [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "enqueue",
        "dispatch.runtime",
        "--runner",
        args.runner,
        "--project-key",
        args.project_key,
        "--request-id",
        request_id,
        "--task-id",
        task_id,
        "--cmd",
        command,
        "--workdir",
        str(REPO_ROOT),
        "--artifact-kind",
        "report",
        "--agent-mode",
        "critique",
        "--provider-id",
        "ollama",
        "--model",
        args.model,
        "--preview",
        "30-minute read-only TwinPaper offdesk autonomy workload",
        "--reason",
        "Prepare and test Offdesk autonomous mode on TwinPaper with qwen read-only diagnostics.",
        "--log-artifact",
        str(out_dir / "offdesk-runner.log"),
        "--result-artifact",
        str(out_dir / "result.json"),
        "--json",
    ]


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_workload_wrapper(path: pathlib.Path, workload_command: list[str]) -> None:
    write_text(
        path,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                f"cd {shlex.quote(str(REPO_ROOT))}",
                'echo "[offdesk-workload] started $(date -u +%Y-%m-%dT%H:%M:%SZ)"',
                shell_join(workload_command),
                "rc=$?",
                'echo "[offdesk-workload] finished rc=${rc} $(date -u +%Y-%m-%dT%H:%M:%SZ)"',
                "exit ${rc}",
                "",
            ]
        ),
    )
    path.chmod(0o755)


def main() -> int:
    args = parse_args()
    stamp = timestamp()
    request_id = f"twinpaper-autonomy-{stamp}"
    task_id = f"twinpaper-autonomy-{stamp}"
    out_root = args.out_root or (profile_dir(args.profile) / "offdesk_workloads" / "twinpaper_autonomy")
    out_dir = (out_root / stamp).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    evidence_dir = out_dir / "evidence"
    evidence_bundle_path = evidence_dir / "evidence_bundle.json"
    evidence_review_path = evidence_dir / "evidence_review.json"
    evidence_bundle_args = evidence_bundle_command(args, evidence_bundle_path)
    evidence_review_args = evidence_review_command(evidence_bundle_path, evidence_review_path)
    completed_bundle = run_command(evidence_bundle_args, evidence_dir / "build_invocation.json")
    if completed_bundle.returncode == 0:
        run_command(evidence_review_args, evidence_dir / "review_invocation.json")
    evidence_review = summarize_evidence_review(evidence_review_path)

    workload_command = build_workload_command(
        args,
        out_dir,
        request_id,
        task_id,
        evidence_bundle_path,
        evidence_review_path,
    )
    wrapper_path = out_dir / "run_workload.sh"
    write_workload_wrapper(wrapper_path, workload_command)
    enqueue_command = f"bash {shlex.quote(str(wrapper_path))}"
    review_generate = args.review_artifact in REVIEW_GENERATE_VALUES
    role_gate_path = resolve_role_gate_result(args.role_gate_result)
    review_artifact_path = resolve_review_artifact(args.review_artifact, args.profile)
    role_gate = summarize_role_gate_result(role_gate_path)
    review_artifact = summarize_review_artifact(review_artifact_path)
    preflight = build_preflight(role_gate, review_artifact, evidence_review, args.allow_preflight_blockers)
    enqueue_args = build_enqueue_args(
        args=args,
        out_dir=out_dir,
        request_id=request_id,
        task_id=task_id,
        command=enqueue_command,
    )
    tick_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "tick", "--limit", "1", "--json"]
    pending_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "pending", "--json"]
    tasks_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "tasks", "--json"]
    poll_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "poll", "--json"]
    role_gate_args = role_gate_command(args)
    review_args = review_harness_command(args)
    prepared_task_path = out_dir / "prepared_task.json"
    generated_review_path = out_dir / "workload_review" / "results.json"
    if review_generate:
        review_args = workload_review_command(prepared_task_path, generated_review_path)

    manifest: dict[str, Any] = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": args.profile,
        "project_key": args.project_key,
        "request_id": request_id,
        "task_id": task_id,
        "repo": str(args.repo.resolve()),
        "out_dir": str(out_dir),
        "duration_minutes": args.duration_minutes,
        "max_iterations": args.max_iterations,
        "provider": "ollama",
        "model": args.model,
        "safety": {
            "repo_read_only": True,
            "writes_only_under_out_dir": True,
            "capability": "dispatch.runtime",
            "runner": args.runner,
            "deterministic_evidence_review_required": True,
            "approval_required_before_dispatch": True,
            "clean_role_gate_required": True,
            "separate_review_artifact_required": True,
        },
        "evidence": {
            "bundle_path": str(evidence_bundle_path),
            "review_path": str(evidence_review_path),
            "review_ready": evidence_review["ready"],
            "review_decision": evidence_review.get("decision"),
            "baseline_evidence_status": evidence_review.get("baseline_evidence_status"),
            "claim_status": evidence_review.get("claim_status"),
        },
        "preflight": preflight,
        "workload_command": workload_command,
        "workload_wrapper": str(wrapper_path),
        "enqueue_args": enqueue_args,
        "commands": {
            "evidence_bundle": shell_join(evidence_bundle_args),
            "evidence_review": shell_join(evidence_review_args),
            "role_gate": shell_join(role_gate_args),
            "review_harness": shell_join(review_args),
            "enqueue": shell_join(enqueue_args),
            "tick": shell_join(tick_args),
            "pending": shell_join(pending_args),
            "tasks": shell_join(tasks_args),
            "poll": shell_join(poll_args),
            "approve_oldest_then_tick": shell_join([str(args.forager_bin), "-p", args.profile, "offdesk", "ok"])
            + " && "
            + shell_join(tick_args),
        },
        "artifacts": {
            "manifest": str(out_dir / "manifest.json"),
            "progress": str(out_dir / "progress.jsonl"),
            "heartbeat": str(out_dir / "heartbeat.json"),
            "result": str(out_dir / "result.json"),
            "report": str(out_dir / "REPORT.md"),
            "result_review": str(out_dir / "result_review" / "results.json"),
            "result_review_markdown": str(out_dir / "result_review" / "RESULT_REVIEW.md"),
            "runner_log": str(out_dir / "offdesk-runner.log"),
            "prepared_task": str(prepared_task_path),
            "preflight": str(out_dir / "preflight.json"),
            "review_artifact": str(generated_review_path) if review_generate else review_artifact.get("path"),
            "evidence_bundle": str(evidence_bundle_path),
            "evidence_markdown": str(evidence_bundle_path.with_name("EVIDENCE.md")),
            "evidence_review": str(evidence_review_path),
            "evidence_review_markdown": str(evidence_review_path.with_name("EVIDENCE_REVIEW.md")),
        },
    }

    if review_generate:
        write_text(prepared_task_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        completed_review = subprocess.run(
            [str(REPO_ROOT / "scripts" / "offdesk_workload_review_harness.py"), "--manifest", str(prepared_task_path), "--out", str(generated_review_path)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        write_text(
            out_dir / "workload_review" / "invocation.json",
            json.dumps(
                {
                    "returncode": completed_review.returncode,
                    "stdout": completed_review.stdout,
                    "stderr": completed_review.stderr,
                    "command": workload_review_command(prepared_task_path, generated_review_path),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        review_artifact = summarize_review_artifact(generated_review_path)
        preflight = build_preflight(role_gate, review_artifact, evidence_review, args.allow_preflight_blockers)
        manifest["preflight"] = preflight
        manifest["artifacts"]["review_artifact"] = str(generated_review_path)

    write_text(out_dir / "preflight.json", json.dumps(preflight, ensure_ascii=False, indent=2) + "\n")
    if preflight["ready_for_enqueue"]:
        write_text(out_dir / "preflight_ready", "ready\n")
    else:
        preflight_blockers = preflight["blocking_reasons"]
        write_text(out_dir / "preflight_blocked", "\n".join(preflight_blockers) + "\n")
    write_text(
        out_dir / "offdesk_enqueue_command.sh",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"if [ ! -f {shlex.quote(str(out_dir / 'preflight_ready'))} ] && "
                '[ "${FORAGER_ALLOW_PREFLIGHT_BLOCKERS:-0}" != "1" ]; then',
                "  echo 'preflight blocked; inspect preflight.json or rerun prepare with clean artifacts' >&2",
                f"  cat {shlex.quote(str(out_dir / 'preflight.json'))} >&2",
                "  exit 3",
                "fi",
                shell_join(enqueue_args),
                "",
            ]
        ),
    )
    (out_dir / "offdesk_enqueue_command.sh").chmod(0o755)
    write_text(out_dir / "offdesk_monitor_commands.md", "\n".join(
        [
            "# TwinPaper Offdesk Monitor Commands",
            "",
            "## Preflight",
            "",
            f"- role_gate_ready: `{role_gate['ready']}`",
            f"- role_gate_result: `{role_gate.get('path')}`",
            f"- review_ready: `{review_artifact['ready']}`",
            f"- review_artifact: `{review_artifact.get('path')}`",
            f"- evidence_ready: `{evidence_review['ready']}`",
            f"- evidence_review: `{evidence_review.get('path')}`",
            f"- evidence_baseline_status: `{evidence_review.get('baseline_evidence_status')}`",
            f"- ready_for_enqueue: `{preflight['ready_for_enqueue']}`",
            f"- blocking_reasons: `{preflight['blocking_reasons']}`",
            "",
            "```bash",
            manifest["commands"]["evidence_bundle"],
            manifest["commands"]["evidence_review"],
            manifest["commands"]["role_gate"],
            manifest["commands"]["review_harness"],
            manifest["commands"]["enqueue"],
            manifest["commands"]["tick"],
            manifest["commands"]["pending"],
            manifest["commands"]["approve_oldest_then_tick"],
            manifest["commands"]["poll"],
            manifest["commands"]["tasks"],
            "```",
            "",
        ]
    ))
    write_text(prepared_task_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    enqueue_result: dict[str, Any] | None = None
    if args.enqueue:
        if not preflight["enqueue_allowed"]:
            output = {
                "prepared": True,
                "enqueued": False,
                "enqueue_blocked": True,
                "manifest": str(out_dir / "prepared_task.json"),
                "out_dir": str(out_dir),
                "request_id": request_id,
                "task_id": task_id,
                "preflight": preflight,
                "evidence_review": evidence_review,
            }
            write_text(out_dir / "enqueue_blocked.json", json.dumps(output, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 3
        if not args.forager_bin.exists():
            raise SystemExit(f"forager binary not found: {args.forager_bin}")
        completed = subprocess.run(enqueue_args, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
        enqueue_result = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        write_text(out_dir / "enqueue_result.json", json.dumps(enqueue_result, ensure_ascii=False, indent=2) + "\n")
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)

    output = {
        "prepared": True,
        "enqueued": args.enqueue,
        "manifest": str(out_dir / "prepared_task.json"),
        "out_dir": str(out_dir),
        "request_id": request_id,
        "task_id": task_id,
        "enqueue_command": manifest["commands"]["enqueue"],
        "tick_command": manifest["commands"]["tick"],
        "approve_then_tick_command": manifest["commands"]["approve_oldest_then_tick"],
        "enqueue_result": enqueue_result,
        "preflight": preflight,
        "evidence_review": evidence_review,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
