#!/usr/bin/env python3
"""Deterministic CLI harness for Offdesk adaptive-wiki runtime episodes.

This harness does not call a model. It creates an isolated profile, writes a
small adaptive wiki fixture, drives real `forager offdesk` CLI commands, and
checks that runtime wiki context is injected only through the intended runtime
surface.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_KEY = "runtime-harness-project"
DEFAULT_REQUEST_ID = "runtime-harness-request"
DEFAULT_TASK_ID = "runtime-harness-task"
DEFAULT_PROFILE = "runtime-episode-harness"
DEFAULT_ARTIFACT_KIND = "report"
DEFAULT_AGENT_MODE = "development"
SECRET = "sk-secretsecretsecretsecret"


@dataclass
class Step:
    name: str
    passed: bool
    detail: str


class HarnessFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forager-bin", default=os.environ.get("FORAGER_BIN"))
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-key", default=DEFAULT_PROJECT_KEY)
    parser.add_argument("--request-id", default=DEFAULT_REQUEST_ID)
    parser.add_argument("--task-id", default=DEFAULT_TASK_ID)
    parser.add_argument("--artifact-kind", default=DEFAULT_ARTIFACT_KIND)
    parser.add_argument("--agent-mode", default=DEFAULT_AGENT_MODE)
    parser.add_argument("--out", type=pathlib.Path, help="Write JSON results to this path.")
    parser.add_argument(
        "--work-root",
        type=pathlib.Path,
        help="Directory to use for the isolated HOME/XDG_CONFIG_HOME and command artifacts.",
    )
    parser.add_argument(
        "--runtime-disabled-check",
        action="store_true",
        help="Also run a launch episode with FORAGER_ADAPTIVE_WIKI_RUNTIME=0.",
    )
    return parser.parse_args()


def forager_command(forager_bin: str | None) -> list[str]:
    if forager_bin:
        return [forager_bin]
    local = REPO_ROOT / "target" / "debug" / "forager"
    if local.exists():
        return [str(local)]
    return ["cargo", "run", "--quiet", "--bin", "forager", "--"]


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_work_root() -> pathlib.Path:
    return REPO_ROOT / "target" / "offdesk-runtime-episode-harness" / timestamp()


def run_json(
    base_cmd: list[str],
    args: list[str],
    *,
    home: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> Any:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env.pop("FORAGER_PROFILE", None)
    env.pop("AGENT_OF_EMPIRES_PROFILE", None)
    env.pop("FORAGER_DEBUG", None)
    env.pop("AGENT_OF_EMPIRES_DEBUG", None)
    if extra_env:
        env.update(extra_env)
    completed = subprocess.run(
        [*base_cmd, *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessFailure(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise HarnessFailure(
            f"command did not emit JSON: {' '.join(args)}\nstdout:\n{completed.stdout}"
        ) from error


def profile_dir(home: pathlib.Path, profile: str) -> pathlib.Path:
    return home / ".config" / "forager" / "profiles" / profile


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: pathlib.Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sorted_ids(values: list[Any]) -> list[str]:
    return sorted(item["id"] for item in values)


def require(condition: bool, name: str, detail: str, steps: list[Step]) -> None:
    steps.append(Step(name=name, passed=condition, detail=detail))
    if not condition:
        raise HarnessFailure(f"{name}: {detail}")


def write_fixture(profile_path: pathlib.Path, brief_path: pathlib.Path, args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    fresh_until = now + dt.timedelta(minutes=30)
    write_json(
        brief_path,
        {
            "request_id": args.request_id,
            "task_id": args.task_id,
            "project_key": args.project_key,
            "approved": True,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": fresh_until.isoformat().replace("+00:00", "Z"),
        },
    )
    write_json(
        profile_path / "adaptive_wiki_entries.json",
        {
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_runtime_shared",
                    "kind": "policy_rule",
                    "scope": "project",
                    "scope_ref": args.project_key,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Shared runtime rule",
                    "ai_instruction": f"Preserve shared report evidence boundaries token={SECRET}",
                    "human_summary": "Human-only shared note",
                    "evidence_refs": ["task:shared"],
                    "confidence": "explicit",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                },
                {
                    "id": "wiki_runtime_code",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": args.project_key,
                    "status": "promoted",
                    "activation_mode": "context_only",
                    "agent_modes": ["development"],
                    "claim": "Development runtime rule",
                    "ai_instruction": "For development runtime, prefer module-local scripts.",
                    "human_summary": "Human-only code note",
                    "evidence_refs": ["task:code"],
                    "confidence": "explicit",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                },
                {
                    "id": "wiki_runtime_research",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": args.project_key,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["writing"],
                    "claim": "Research runtime rule",
                    "ai_instruction": "Do not inject this into development runtime.",
                    "human_summary": "Human-only research note",
                    "evidence_refs": ["task:research"],
                    "confidence": "explicit",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                },
                {
                    "id": "wiki_runtime_deprecated",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": args.project_key,
                    "status": "deprecated",
                    "activation_mode": "confirm",
                    "claim": "Deprecated runtime rule",
                    "ai_instruction": "Do not inject deprecated runtime entries.",
                    "human_summary": "Human-only deprecated note",
                    "evidence_refs": ["task:deprecated"],
                    "confidence": "explicit",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                },
            ],
        },
    )


def run_runtime_episode(args: argparse.Namespace, base_cmd: list[str], work_root: pathlib.Path) -> dict[str, Any]:
    home = work_root / "home"
    run_dir = work_root / "run"
    profile_path = profile_dir(home, args.profile)
    brief_path = run_dir / "brief.json"
    result_path = run_dir / "runtime-result.txt"
    log_path = run_dir / "runtime.log"
    profile_path.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_fixture(profile_path, brief_path, args)

    steps: list[Step] = []
    common_scope = [
        "-p",
        args.profile,
        "offdesk",
        "gate",
        "inspect.status",
        "--project-key",
        args.project_key,
        "--request-id",
        args.request_id,
        "--task-id",
        args.task_id,
        "--artifact-kind",
        args.artifact_kind,
    ]
    gate_without_mode = run_json(base_cmd, [*common_scope, "--json"], home=home)
    without_mode_ids = sorted_ids(gate_without_mode["adaptive_wiki_runtime"])
    require(
        without_mode_ids == ["wiki_runtime_shared"],
        "gate_without_mode_uses_shared_only_runtime_projection",
        f"ids={without_mode_ids}",
        steps,
    )

    gate_with_mode = run_json(
        base_cmd,
        [*common_scope, "--agent-mode", args.agent_mode, "--json"],
        home=home,
    )
    with_mode_ids = sorted_ids(gate_with_mode["adaptive_wiki_runtime"])
    require(
        with_mode_ids == ["wiki_runtime_code", "wiki_runtime_shared"],
        "gate_with_mode_projects_shared_plus_matching_mode",
        f"ids={with_mode_ids}",
        steps,
    )
    require(
        gate_with_mode["adaptive_wiki_runtime_policy"]["review_expired"] == "warn",
        "gate_runtime_policy_is_warn",
        json.dumps(gate_with_mode["adaptive_wiki_runtime_policy"]),
        steps,
    )

    command = f"printf runtime-ok > {result_path}"
    enqueue = run_json(
        base_cmd,
        [
            "-p",
            args.profile,
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            args.project_key,
            "--request-id",
            args.request_id,
            "--task-id",
            args.task_id,
            "--brief",
            str(brief_path),
            "--artifact-kind",
            args.artifact_kind,
            "--agent-mode",
            args.agent_mode,
            "--cmd",
            command,
            "--workdir",
            str(run_dir),
            "--log-artifact",
            str(log_path),
            "--result-artifact",
            str(result_path),
            "--json",
        ],
        home=home,
    )
    require(
        enqueue["status"] == "queued",
        "enqueue_records_queued_task",
        f"status={enqueue.get('status')}",
        steps,
    )

    tick = run_json(base_cmd, ["-p", args.profile, "offdesk", "tick", "--json"], home=home)
    require(tick["launched"] == 1, "tick_launches_one_task", f"launched={tick.get('launched')}", steps)

    runs = read_json(profile_path / "background_runs.json")
    require(len(runs) == 1, "background_run_recorded", f"count={len(runs)}", steps)
    run = runs[0]
    run_ids = sorted(run["adaptive_wiki_entry_ids"])
    require(
        run_ids == ["wiki_runtime_code", "wiki_runtime_shared"],
        "background_probe_has_runtime_entry_ids",
        f"ids={run_ids}",
        steps,
    )
    context = run["adaptive_wiki_context"]
    require(
        "<adaptive-wiki-context>" in context and "wiki_runtime_code" in context and "wiki_runtime_shared" in context,
        "background_probe_has_fenced_runtime_context",
        context[:240],
        steps,
    )
    require(
        "wiki_runtime_research" not in context and "wiki_runtime_deprecated" not in context,
        "runtime_context_excludes_out_of_scope_entries",
        context,
        steps,
    )
    require(
        SECRET not in context and "[REDACTED]" in context,
        "runtime_context_is_redacted",
        context,
        steps,
    )
    require(
        run["launch_spec_summary"] == command and run["working_dir"] == str(run_dir),
        "runtime_context_does_not_rewrite_launch_spec_or_workdir",
        f"launch_spec={run['launch_spec_summary']} workdir={run['working_dir']}",
        steps,
    )

    tasks = read_json(profile_path / "offdesk_tasks.json")
    require(
        tasks[0]["command"] == command and tasks[0]["workdir"] == str(run_dir),
        "task_command_and_workdir_unchanged",
        f"command={tasks[0]['command']} workdir={tasks[0]['workdir']}",
        steps,
    )
    require(
        sorted(tasks[0]["last_adaptive_wiki_entry_ids"]) == ["wiki_runtime_code", "wiki_runtime_shared"],
        "task_tracks_last_runtime_wiki_ids",
        str(tasks[0]["last_adaptive_wiki_entry_ids"]),
        steps,
    )

    usage = read_jsonl(profile_path / "adaptive_wiki_usage.jsonl")
    usage_ids = sorted(row["entry_id"] for row in usage)
    require(
        usage_ids == ["wiki_runtime_code", "wiki_runtime_shared"],
        "usage_records_runtime_projection_entries",
        f"ids={usage_ids}",
        steps,
    )
    require(
        all(
            row["task_id"] == args.task_id
            and row["request_id"] == args.request_id
            and row["project_key"] == args.project_key
            and row["artifact_kind"] == args.artifact_kind
            and row["agent_mode"] == "development"
            and row["projection_kind"] == "runtime_probe"
            and row["projection_policy"]["review_expired"] == "warn"
            for row in usage
        ),
        "usage_records_keep_runtime_scope_and_policy",
        json.dumps(usage, ensure_ascii=False),
        steps,
    )

    debug_bundle = run_json(base_cmd, ["-p", args.profile, "offdesk", "debug-bundle", "--json"], home=home)
    debug_text = json.dumps(debug_bundle, ensure_ascii=False)
    require(SECRET not in debug_text, "debug_bundle_redacts_secret_like_values", "secret absent", steps)
    require(
        len(debug_bundle["adaptive_wiki_usage"]) == 2,
        "debug_bundle_includes_runtime_usage_summary_source",
        f"usage_count={len(debug_bundle['adaptive_wiki_usage'])}",
        steps,
    )

    trace = run_json(
        base_cmd,
        [
            "-p",
            args.profile,
            "offdesk",
            "wiki",
            "episode-trace",
            "--request-id",
            args.request_id,
            "--task-id",
            args.task_id,
            "--project-key",
            args.project_key,
            "--artifact-kind",
            args.artifact_kind,
            "--dry-run",
            "--json",
        ],
        home=home,
    )
    trace_text = json.dumps(trace, ensure_ascii=False)
    require("wiki_runtime_code" in trace_text, "episode_trace_links_runtime_entry", "wiki_runtime_code present", steps)
    require(
        SECRET not in trace_text,
        "episode_trace_is_redacted",
        "secret absent",
        steps,
    )

    disabled_result: dict[str, Any] | None = None
    if args.runtime_disabled_check:
        disabled_result = run_disabled_launch_episode(args, base_cmd, home, profile_path, brief_path, steps)

    return {
        "work_root": str(work_root),
        "profile_dir": str(profile_path),
        "gate_without_mode_runtime_ids": without_mode_ids,
        "gate_with_mode_runtime_ids": with_mode_ids,
        "tick": tick,
        "background_run_runtime_ids": run_ids,
        "usage_ids": usage_ids,
        "trace_event_count": trace.get("summary", {}).get("events"),
        "trace_summary": trace.get("summary"),
        "runtime_disabled": disabled_result,
        "steps": [step.__dict__ for step in steps],
    }


def run_disabled_launch_episode(
    args: argparse.Namespace,
    base_cmd: list[str],
    home: pathlib.Path,
    profile_path: pathlib.Path,
    brief_path: pathlib.Path,
    steps: list[Step],
) -> dict[str, Any]:
    disabled_task_id = f"{args.task_id}-disabled"
    disabled_request_id = f"{args.request_id}-disabled"
    disabled_brief_path = brief_path.with_name("brief-disabled.json")
    original_brief = read_json(brief_path)
    original_brief["request_id"] = disabled_request_id
    original_brief["task_id"] = disabled_task_id
    write_json(disabled_brief_path, original_brief)
    disabled = run_json(
        base_cmd,
        [
            "-p",
            args.profile,
            "offdesk",
            "launch",
            "dispatch.runtime",
            "--runner",
            "remote-worker",
            "--project-key",
            args.project_key,
            "--request-id",
            disabled_request_id,
            "--task-id",
            disabled_task_id,
            "--brief",
            str(disabled_brief_path),
            "--artifact-kind",
            args.artifact_kind,
            "--agent-mode",
            args.agent_mode,
            "--ticket-id",
            f"ticket-{uuid.uuid4()}",
            "--json",
        ],
        home=home,
        extra_env={"FORAGER_ADAPTIVE_WIKI_RUNTIME": "0"},
    )
    gate_ids = sorted_ids(disabled["gate"]["adaptive_wiki_runtime"])
    probe = disabled.get("probe") or {}
    require(
        gate_ids == ["wiki_runtime_code", "wiki_runtime_shared"],
        "runtime_disabled_keeps_preflight_runtime_projection",
        f"ids={gate_ids}",
        steps,
    )
    require(
        "adaptive_wiki_context" not in probe and "adaptive_wiki_entry_ids" not in probe,
        "runtime_disabled_omits_probe_context",
        json.dumps(probe, ensure_ascii=False),
        steps,
    )
    usage = read_jsonl(profile_path / "adaptive_wiki_usage.jsonl")
    require(
        len(usage) == 2,
        "runtime_disabled_does_not_append_usage",
        f"usage_count={len(usage)}",
        steps,
    )
    return {"gate_runtime_ids": gate_ids, "probe_keys": sorted(probe.keys())}


def main() -> int:
    args = parse_args()
    work_root = (args.work_root or default_work_root()).resolve()
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True)
    out_path = (args.out or (work_root / "results.json")).resolve()
    base_cmd = forager_command(args.forager_bin)
    try:
        result = run_runtime_episode(args, base_cmd, work_root)
        result["summary"] = {"passed": True, "failed_steps": 0}
        exit_code = 0
    except Exception as error:
        result = {
            "work_root": str(work_root),
            "summary": {"passed": False, "failed_steps": 1},
            "error": str(error),
        }
        exit_code = 1
    result["created_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result["forager_command"] = base_cmd
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, result)
    print(json.dumps({"passed": result["summary"]["passed"], "out": str(out_path)}, ensure_ascii=False))
    if exit_code != 0:
        print(result["error"], file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
