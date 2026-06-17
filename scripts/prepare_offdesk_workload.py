#!/usr/bin/env python3
"""Prepare a generic queued Offdesk workload.

The script writes a reviewed workload packet without executing the workload.
It is intentionally narrower than domain-specific producers: callers provide a
bounded command, project key, repository path, and output root; Forager keeps
runtime launch behind the normal `dispatch.runtime` approval gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shlex
import subprocess
from typing import Any

from offdesk_llm_endpoint import default_ollama_base_url


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = default_ollama_base_url()
DEFAULT_MODEL = os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder-next:latest")
DEFAULT_PROFILE = os.environ.get("OFFDESK_PROFILE", "default")
SYSTEM_CRITICAL_SAFETY: dict[str, Any] = {
    "repo_read_only": True,
    "writes_only_under_out_dir": True,
    "model_responses_not_executed": True,
    "no_file_deletion_or_cleanup": True,
    "no_reboot_shutdown_or_power_state_change": True,
    "no_service_restart_or_system_config_change": True,
    "no_storage_raid_nvme_or_mount_change": True,
    "no_package_install_or_permission_change": True,
    "no_process_termination_or_runner_interference": True,
    "no_network_firewall_or_remote_access_change": True,
    "no_kernel_driver_firmware_or_bios_change": True,
    "operator_approval_required_for_system_mutation": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-key", required=True)
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--workload-command", required=True)
    parser.add_argument("--title", default="Generic Offdesk Workload")
    parser.add_argument("--request-id")
    parser.add_argument("--task-id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider-id", default="ollama")
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument(
        "--runner",
        choices=("local-tmux", "local-background"),
        default="local-tmux",
        help="Runner backend for the Offdesk task. Use local-tmux for inspectable long runs.",
    )
    parser.add_argument("--artifact-kind", default="report")
    parser.add_argument("--agent-mode", default="critique")
    parser.add_argument(
        "--required-command-arg",
        action="append",
        default=[],
        help="Command argument that must appear in workload_command for review to pass.",
    )
    parser.add_argument(
        "--required-artifact",
        action="append",
        default=[],
        help="Artifact key that must be present and under the workload output directory.",
    )
    parser.add_argument(
        "--role-gate-required",
        action="store_true",
        help="Require a role gate summary in the manifest preflight block.",
    )
    parser.add_argument(
        "--evidence-review-required",
        action="store_true",
        help="Require evidence_bundle and evidence_review artifacts plus a sufficient evidence review.",
    )
    parser.add_argument(
        "--role-gate-result",
        type=pathlib.Path,
        help="Optional role gate results.json to summarize into preflight.",
    )
    parser.add_argument(
        "--evidence-bundle",
        type=pathlib.Path,
        help="Optional evidence bundle artifact to bind to this workload.",
    )
    parser.add_argument(
        "--evidence-review",
        type=pathlib.Path,
        help="Optional evidence review artifact to bind to this workload.",
    )
    parser.add_argument(
        "--review-artifact",
        default="generate",
        help="Use 'generate' or provide a prebuilt workload review results.json.",
    )
    parser.add_argument(
        "--forager-bin",
        type=pathlib.Path,
        default=REPO_ROOT / "target" / "debug" / "forager",
        help="Built forager binary to use when --enqueue is set.",
    )
    parser.add_argument("--out-root", type=pathlib.Path)
    parser.add_argument("--enqueue", action="store_true", help="Actually enqueue the reviewed workload.")
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def profile_dir(profile: str) -> pathlib.Path:
    config_home = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return config_home / "forager" / "profiles" / profile


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_role_gate(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "ready": False,
            "path": None,
            "failed": None,
            "failure_category_counts": {},
            "quality_gate": {"ready_for_long_workload": False},
            "reason": "role_gate_not_provided",
        }
    try:
        artifact = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return {
            "ready": False,
            "path": str(path),
            "failed": None,
            "failure_category_counts": {},
            "quality_gate": {"ready_for_long_workload": False},
            "reason": f"role_gate_unreadable:{error}",
        }
    summary = artifact.get("summary", {}) if isinstance(artifact, dict) else {}
    quality_gate = summary.get("quality_gate", {}) if isinstance(summary, dict) else {}
    failed = int(summary.get("failed") or 0) if isinstance(summary, dict) else 1
    failure_categories = summary.get("failure_category_counts", {}) if isinstance(summary, dict) else {}
    ready = (
        isinstance(summary, dict)
        and failed == 0
        and not failure_categories
        and isinstance(quality_gate, dict)
        and quality_gate.get("ready_for_long_workload") is True
    )
    return {
        "ready": ready,
        "path": str(path),
        "failed": failed,
        "failure_category_counts": failure_categories if isinstance(failure_categories, dict) else {},
        "quality_gate": quality_gate if isinstance(quality_gate, dict) else {},
        "reason": "ready" if ready else "role_gate_not_ready_for_long_workload",
    }


def summarize_review(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None:
        return {"ready": False, "path": None, "decision": None, "reason": "review_not_generated"}
    try:
        artifact = load_json(path)
    except (OSError, json.JSONDecodeError) as error:
        return {"ready": False, "path": str(path), "decision": None, "reason": f"review_unreadable:{error}"}
    decision = str(artifact.get("review_stage_decision") or artifact.get("decision") or "")
    ready = artifact.get("passed") is True and decision == "needs_approval"
    return {
        "ready": ready,
        "path": str(path),
        "decision": decision,
        "reason": "ready" if ready else "review_not_ready",
    }


def build_workload_wrapper(path: pathlib.Path, repo: pathlib.Path, command: str) -> None:
    write_text(
        path,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -uo pipefail",
                f"cd {shlex.quote(str(repo.resolve()))}",
                'echo "[offdesk-workload] started $(date -u +%Y-%m-%dT%H:%M:%SZ)"',
                command,
                "rc=$?",
                'echo "[offdesk-workload] finished rc=${rc} $(date -u +%Y-%m-%dT%H:%M:%SZ)"',
                "exit ${rc}",
                "",
            ]
        ),
    )
    path.chmod(0o755)


def enqueue_args(args: argparse.Namespace, request_id: str, task_id: str, out_dir: pathlib.Path, wrapper: pathlib.Path) -> list[str]:
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
        f"bash {shlex.quote(str(wrapper))}",
        "--workdir",
        str(args.repo.resolve()),
        "--artifact-kind",
        args.artifact_kind,
        "--agent-mode",
        args.agent_mode,
        "--provider-id",
        args.provider_id,
        "--model",
        args.model,
        "--preview",
        f"{args.duration_minutes:g}-minute read-only Offdesk workload: {args.title}",
        "--reason",
        "Prepare and test a bounded Offdesk workload with read-only project scope and system-critical mutation guards.",
        "--log-artifact",
        str(out_dir / "offdesk-runner.log"),
        "--result-artifact",
        str(out_dir / "result.json"),
        "--json",
    ]


def render_launch_packet(manifest: dict[str, Any], preflight: dict[str, Any]) -> str:
    blockers = preflight.get("blocking_reasons") or []
    lines = [
        "# Offdesk Launch Dry Run",
        "",
        "This packet is a review artifact. It does not execute runtime work and it does not approve the task.",
        "",
        "## Scope",
        "",
        f"- title: `{manifest['title']}`",
        f"- project_key: `{manifest['project_key']}`",
        f"- request_id: `{manifest['request_id']}`",
        f"- task_id: `{manifest['task_id']}`",
        f"- repo: `{manifest['repo']}`",
        f"- out_dir: `{manifest['out_dir']}`",
        f"- runner: `{manifest['safety']['runner']}`",
        f"- provider_model: `{manifest['provider']}:{manifest['model']}`",
        f"- ready_for_enqueue: `{preflight['ready_for_enqueue']}`",
        "",
        "## Blocking Reasons",
        "",
    ]
    lines.extend(f"- {item}" for item in blockers)
    lines.extend(
        [
            "",
            "## Next Commands",
            "",
            "```bash",
            manifest["commands"]["enqueue"],
            manifest["commands"]["tick"],
            manifest["commands"]["pending"],
            manifest["commands"]["poll"],
            "```",
            "",
            "## Safety Boundary",
            "",
            "- Project files are read-only unless a separate reviewed command says otherwise.",
            "- Runtime dispatch still requires the normal `dispatch.runtime` approval path.",
            "- Cleanup, deletion, service changes, provider retargeting, and wiki promotion are not authorized here.",
            "",
        ]
    )
    return "\n".join(lines)


def render_long_run_packet(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Offdesk Long-Run Validation Packet",
            "",
            "Use this packet to monitor and close out the prepared workload.",
            "",
            "## Gates",
            "",
            "1. Read `LAUNCH_DRY_RUN.md` and `preflight.json`.",
            "2. Approve only the matching `dispatch.runtime` row.",
            "3. Monitor task state, runner log, result artifact, and heartbeat/progress if the workload writes them.",
            "4. Run closeout before returning to Ondesk.",
            "5. Treat generated wiki candidates as review-only until promoted by an explicit command.",
            "",
            "## Commands",
            "",
            "```bash",
            manifest["commands"]["tasks"],
            manifest["commands"]["poll"],
            manifest["commands"]["closeout_dry_run"],
            manifest["commands"]["ondesk_prompt_package"],
            "```",
            "",
        ]
    )


def main() -> int:
    args = parse_args()
    repo = args.repo.expanduser().resolve()
    if not repo.exists():
        raise SystemExit(f"repo path does not exist: {repo}")
    stamp = timestamp()
    request_id = args.request_id or f"{args.project_key}-workload-{stamp}"
    task_id = args.task_id or request_id
    out_root = args.out_root or (profile_dir(args.profile) / "offdesk_workloads" / args.project_key)
    out_dir = (out_root / stamp).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    wrapper = out_dir / "run_workload.sh"
    build_workload_wrapper(wrapper, repo, args.workload_command)

    prepared_task = out_dir / "prepared_task.json"
    preflight_path = out_dir / "preflight.json"
    review_path = out_dir / "workload_review" / "results.json"
    launch_packet = out_dir / "LAUNCH_DRY_RUN.md"
    validation_packet = out_dir / "LONG_RUN_VALIDATION.md"
    enqueue_script = out_dir / "offdesk_enqueue_command.sh"

    role_gate = summarize_role_gate(args.role_gate_result)
    required_artifacts = ["prepared_task", "preflight", "runner_log", "result", "report"]
    for key in args.required_artifact:
        if key not in required_artifacts:
            required_artifacts.append(key)
    artifacts: dict[str, Any] = {
        "prepared_task": str(prepared_task),
        "preflight": str(preflight_path),
        "runner_log": str(out_dir / "offdesk-runner.log"),
        "result": str(out_dir / "result.json"),
        "report": str(out_dir / "REPORT.md"),
        "review_artifact": str(review_path),
        "launch_dry_run_report": str(launch_packet),
        "long_run_validation_packet": str(validation_packet),
        "workload_wrapper": str(wrapper),
    }
    if args.evidence_bundle:
        artifacts["evidence_bundle"] = str(args.evidence_bundle.expanduser().resolve())
    if args.evidence_review:
        artifacts["evidence_review"] = str(args.evidence_review.expanduser().resolve())
    command = ["bash", "-lc", args.workload_command]
    enqueue = enqueue_args(args, request_id, task_id, out_dir, wrapper)
    commands = {
        "enqueue": shell_join(enqueue),
        "tick": shell_join([str(args.forager_bin), "-p", args.profile, "offdesk", "tick", "--limit", "1", "--json"]),
        "pending": shell_join([str(args.forager_bin), "-p", args.profile, "offdesk", "pending", "--json"]),
        "poll": shell_join([str(args.forager_bin), "-p", args.profile, "offdesk", "poll", "--json"]),
        "tasks": shell_join(
            [
                str(args.forager_bin),
                "-p",
                args.profile,
                "offdesk",
                "tasks",
                "--project-key",
                args.project_key,
                "--task-id",
                task_id,
                "--json",
            ]
        ),
        "closeout_dry_run": shell_join(
            [
                str(args.forager_bin),
                "-p",
                args.profile,
                "offdesk",
                "closeout",
                "--project-key",
                args.project_key,
                "--task-id",
                task_id,
                "--dry-run",
            ]
        ),
        "ondesk_prompt_package": shell_join(
            [
                str(args.forager_bin),
                "-p",
                args.profile,
                "ondesk",
                "prompt-package",
                "--project-key",
                args.project_key,
            ]
        ),
    }
    preflight = {
        "ready_for_enqueue": False,
        "blocking_reasons": ["review_not_generated"],
        "warnings": [],
        "role_gate": role_gate,
        "review_artifact": {"ready": False, "path": str(review_path), "decision": None},
    }
    manifest: dict[str, Any] = {
        "created_at": utc_now(),
        "kind": "forager_offdesk_prepared_workload",
        "title": args.title,
        "profile": args.profile,
        "project_key": args.project_key,
        "request_id": request_id,
        "task_id": task_id,
        "repo": str(repo),
        "out_dir": str(out_dir),
        "duration_minutes": args.duration_minutes,
        "max_iterations": args.max_iterations,
        "provider": args.provider_id,
        "base_url": args.base_url,
        "model": args.model,
        "workload_command": command,
        "workload_command_text": args.workload_command,
        "workload_wrapper": str(wrapper),
        "enqueue_args": enqueue,
        "commands": commands,
        "artifacts": artifacts,
        "review_contract": {
            "schema": "forager_workload_review_contract.v1",
            "role_gate_required": args.role_gate_required,
            "evidence_review_required": args.evidence_review_required,
            "required_command_args": args.required_command_arg,
            "required_artifacts": required_artifacts,
            "provider_id": args.provider_id,
        },
        "safety": {
            **SYSTEM_CRITICAL_SAFETY,
            "capability": "dispatch.runtime",
            "runner": args.runner,
            "approval_required_before_dispatch": True,
            "clean_role_gate_required": args.role_gate_required,
            "separate_review_artifact_required": True,
            "deterministic_evidence_review_required": args.evidence_review_required,
        },
        "preflight": preflight,
    }
    write_text(prepared_task, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(preflight_path, json.dumps(preflight, ensure_ascii=False, indent=2) + "\n")

    if args.review_artifact == "generate":
        completed = subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "scripts" / "offdesk_workload_review_harness.py"),
                "--manifest",
                str(prepared_task),
                "--out",
                str(review_path),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        write_text(
            out_dir / "workload_review" / "invocation.json",
            json.dumps(
                {
                    "command": [
                        "python3",
                        str(REPO_ROOT / "scripts" / "offdesk_workload_review_harness.py"),
                        "--manifest",
                        str(prepared_task),
                        "--out",
                        str(review_path),
                    ],
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    else:
        review_path = pathlib.Path(args.review_artifact).expanduser().resolve()
        manifest["artifacts"]["review_artifact"] = str(review_path)

    review = summarize_review(review_path)
    blockers: list[str] = []
    if args.role_gate_required and not role_gate["ready"]:
        blockers.append("role_gate_not_ready")
    if not review["ready"]:
        blockers.append("workload_review_not_ready")
    preflight = {
        "ready_for_enqueue": not blockers,
        "blocking_reasons": blockers,
        "warnings": [],
        "role_gate": role_gate,
        "review_artifact": review,
    }
    manifest["preflight"] = preflight
    write_text(prepared_task, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    write_text(preflight_path, json.dumps(preflight, ensure_ascii=False, indent=2) + "\n")
    if preflight["ready_for_enqueue"]:
        write_text(out_dir / "preflight_ready", "ready\n")
    else:
        write_text(out_dir / "preflight_blocked", "\n".join(blockers) + "\n")

    write_text(launch_packet, render_launch_packet(manifest, preflight))
    write_text(validation_packet, render_long_run_packet(manifest))
    write_text(
        enqueue_script,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"if [ ! -f {shlex.quote(str(out_dir / 'preflight_ready'))} ]; then",
                "  echo 'preflight blocked; inspect preflight.json' >&2",
                f"  cat {shlex.quote(str(preflight_path))} >&2",
                "  exit 3",
                "fi",
                shell_join(enqueue),
                "",
            ]
        ),
    )
    enqueue_script.chmod(0o755)

    if args.enqueue:
        if not preflight["ready_for_enqueue"]:
            write_text(out_dir / "enqueue_blocked.json", json.dumps(preflight, ensure_ascii=False, indent=2) + "\n")
            raise SystemExit(3)
        completed = subprocess.run(enqueue, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
        write_text(
            out_dir / "enqueue_result.json",
            json.dumps(
                {
                    "command": enqueue,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)

    print(
        json.dumps(
            {
                "manifest": str(prepared_task),
                "preflight": str(preflight_path),
                "launch_dry_run_report": str(launch_packet),
                "long_run_validation_packet": str(validation_packet),
                "enqueue_script": str(enqueue_script),
                "ready_for_enqueue": preflight["ready_for_enqueue"],
                "blocking_reasons": preflight["blocking_reasons"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
