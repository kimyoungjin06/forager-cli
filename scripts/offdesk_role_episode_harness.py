#!/usr/bin/env python3
"""Deterministic role-specific adaptive-wiki projection episode harness.

This harness does not call a model. It creates an isolated profile with shared
and mode-specific adaptive wiki entries, drives real `forager offdesk gate`
commands, and checks that role-specific guidance does not leak across current
persisted adaptive-wiki modes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
from dataclasses import dataclass
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = "role-episode-harness"
DEFAULT_PROJECT_KEY = "role-episode-project"
DEFAULT_REQUEST_ID = "role-episode-request"
DEFAULT_TASK_ID = "role-episode-task"
DEFAULT_ARTIFACT_KIND = "report"


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
    parser.add_argument("--out", type=pathlib.Path, help="Write JSON results to this path.")
    parser.add_argument(
        "--work-root",
        type=pathlib.Path,
        help="Directory to use for the isolated HOME/XDG_CONFIG_HOME and command artifacts.",
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
    return REPO_ROOT / "target" / "offdesk-role-episode-harness" / timestamp()


def profile_dir(home: pathlib.Path, profile: str) -> pathlib.Path:
    return home / ".config" / "forager" / "profiles" / profile


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_json(base_cmd: list[str], args: list[str], *, home: pathlib.Path) -> Any:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env.pop("FORAGER_PROFILE", None)
    env.pop("AGENT_OF_EMPIRES_PROFILE", None)
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


def require(condition: bool, name: str, detail: str, steps: list[Step]) -> None:
    steps.append(Step(name=name, passed=condition, detail=detail))
    if not condition:
        raise HarnessFailure(f"{name}: {detail}")


def projection_ids(gate_output: Any) -> list[str]:
    return sorted(entry["id"] for entry in gate_output["adaptive_wiki"])


def write_fixture(profile_path: pathlib.Path, args: argparse.Namespace) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    write_json(
        profile_path / "adaptive_wiki_entries.json",
        {
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_role_shared",
                    "kind": "policy_rule",
                    "scope": "artifact_kind",
                    "scope_ref": args.artifact_kind,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Shared report rule",
                    "ai_instruction": "Shared report guidance applies to every role.",
                    "evidence_refs": ["task:shared"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "wiki_role_code",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": args.artifact_kind,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["code_development"],
                    "claim": "Code role rule",
                    "ai_instruction": "Code-development guidance only.",
                    "evidence_refs": ["task:code"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "wiki_role_research",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": args.artifact_kind,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["research_writing"],
                    "claim": "Research role rule",
                    "ai_instruction": "Research/writing guidance only.",
                    "evidence_refs": ["task:research"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "wiki_role_critique",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": args.artifact_kind,
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["critique"],
                    "claim": "Critique role rule",
                    "ai_instruction": "Critique/review guidance only.",
                    "evidence_refs": ["task:critique"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": "wiki_role_deprecated",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": args.artifact_kind,
                    "status": "deprecated",
                    "activation_mode": "confirm",
                    "claim": "Deprecated role rule",
                    "ai_instruction": "Deprecated guidance must not project.",
                    "evidence_refs": ["task:deprecated"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        },
    )


def gate_args(args: argparse.Namespace, agent_mode: str | None) -> list[str]:
    values = [
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
        "--json",
    ]
    if agent_mode:
        values.extend(["--agent-mode", agent_mode])
    return values


def run_episode(args: argparse.Namespace, base_cmd: list[str], work_root: pathlib.Path) -> dict[str, Any]:
    home = work_root / "home"
    profile_path = profile_dir(home, args.profile)
    profile_path.mkdir(parents=True, exist_ok=True)
    write_fixture(profile_path, args)
    steps: list[Step] = []

    cases = [
        (None, ["wiki_role_shared"]),
        ("code-development", ["wiki_role_code", "wiki_role_shared"]),
        ("research-writing", ["wiki_role_research", "wiki_role_shared"]),
        ("critique", ["wiki_role_critique", "wiki_role_shared"]),
    ]
    results: dict[str, Any] = {}
    for agent_mode, expected_ids in cases:
        output = run_json(base_cmd, gate_args(args, agent_mode), home=home)
        ids = projection_ids(output)
        label = agent_mode or "shared-only"
        require(
            ids == expected_ids,
            f"{label}_projection_matches_expected_role_scope",
            f"ids={ids} expected={expected_ids}",
            steps,
        )
        require(
            "wiki_role_deprecated" not in ids,
            f"{label}_projection_excludes_deprecated_entry",
            f"ids={ids}",
            steps,
        )
        results[label] = {
            "ids": ids,
            "runtime_ids": sorted(entry["id"] for entry in output["adaptive_wiki_runtime"]),
        }

    all_selected = {entry_id for result in results.values() for entry_id in result["ids"]}
    require(
        all_selected == {"wiki_role_shared", "wiki_role_code", "wiki_role_research", "wiki_role_critique"},
        "episode_exercises_all_role_specific_entries",
        f"selected={sorted(all_selected)}",
        steps,
    )

    return {
        "work_root": str(work_root),
        "profile_dir": str(profile_path),
        "results": results,
        "steps": [step.__dict__ for step in steps],
    }


def main() -> int:
    args = parse_args()
    work_root = args.work_root or default_work_root()
    work_root.mkdir(parents=True, exist_ok=True)
    base_cmd = forager_command(args.forager_bin)
    try:
        result = run_episode(args, base_cmd, work_root)
        result["passed"] = True
    except HarnessFailure as error:
        result = {
            "passed": False,
            "work_root": str(work_root),
            "error": str(error),
        }

    out_path = args.out or (work_root / "results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "out": str(out_path)}, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
