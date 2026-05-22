#!/usr/bin/env python3
"""Build a deterministic TwinPaper Module03 operation profile.

The profile is read-only. It describes how Offdesk should reason about the
module as an operating unit: entrypoints, allowed operations, forbidden actions,
evidence gates, reportability vocabulary, and Ondesk return requirements.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
from typing import Any


DEFAULT_REPO = pathlib.Path("/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper")
MODULE_PATH = pathlib.PurePosixPath("modules/03_regspec_machine")
CANONICAL_MODES = (
    "plan",
    "single-nooption",
    "single-singlex",
    "paired",
    "overnight",
    "migration-smoke",
    "contract-ci",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_REPO)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument(
        "--evidence-bundle",
        type=pathlib.Path,
        help="Optional TwinPaper evidence bundle used to derive current evidence state.",
    )
    parser.add_argument(
        "--include-git",
        action="store_true",
        help="Include read-only git status evidence for the detected module repository.",
    )
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_meta(path: pathlib.Path, rel: str) -> dict[str, Any]:
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "path": rel,
        "exists": exists,
        "size_bytes": stat.st_size if stat else None,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat() if stat else None,
    }


def detect_layout(repo: pathlib.Path) -> dict[str, Any]:
    repo = repo.expanduser().resolve()
    monorepo_module = repo / MODULE_PATH
    if monorepo_module.exists():
        return {
            "repo_root": repo,
            "module_root": monorepo_module,
            "module_path": MODULE_PATH.as_posix(),
            "command_prefix": MODULE_PATH.as_posix(),
            "layout": "monorepo",
        }
    if (repo / "contract.yaml").exists() and (repo / "pyproject.toml").exists():
        return {
            "repo_root": repo,
            "module_root": repo,
            "module_path": ".",
            "command_prefix": ".",
            "layout": "standalone_module",
        }
    return {
        "repo_root": repo,
        "module_root": monorepo_module,
        "module_path": MODULE_PATH.as_posix(),
        "command_prefix": MODULE_PATH.as_posix(),
        "layout": "unknown",
    }


def rel_command(layout: dict[str, Any], suffix: str) -> str:
    prefix = str(layout["command_prefix"])
    if prefix == ".":
        return f"scripts/run_module_03.sh {suffix}".strip()
    return f"{prefix}/scripts/run_module_03.sh {suffix}".strip()


def mode_names_from_entrypoint(entrypoint: pathlib.Path) -> list[str]:
    if not entrypoint.exists():
        return []
    modes: list[str] = []
    for line in entrypoint.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s{2}([A-Za-z0-9_-]+)\)", line)
        if match:
            modes.append(match.group(1))
    return sorted(dict.fromkeys(modes))


def run_git(module_root: pathlib.Path, args: list[str]) -> str | None:
    completed = subprocess.run(
        ["git", *args],
        cwd=module_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def evidence_current_state(evidence_bundle: pathlib.Path | None) -> dict[str, Any]:
    if not evidence_bundle:
        return {
            "source": "not_supplied",
            "baseline_evidence_status": "unknown",
            "claim_status": "unknown",
            "latest_direction_review_artifact": None,
        }
    try:
        bundle = load_json(evidence_bundle.expanduser().resolve())
    except (OSError, json.JSONDecodeError) as error:
        return {
            "source": str(evidence_bundle),
            "baseline_evidence_status": "unknown",
            "claim_status": "unknown",
            "latest_direction_review_artifact": None,
            "error": repr(error),
        }
    state = bundle.get("current_state", {}) if isinstance(bundle, dict) else {}
    if not isinstance(state, dict):
        state = {}
    return {
        "source": str(evidence_bundle),
        "baseline_evidence_status": state.get("baseline_evidence_status", "unknown"),
        "claim_status": state.get("claim_status", "unknown"),
        "latest_direction_review_artifact": state.get("latest_direction_review_artifact"),
        "has_nooption_evidence": state.get("has_nooption_evidence"),
        "has_singlex_evidence": state.get("has_singlex_evidence"),
        "has_openexplore_evidence": state.get("has_openexplore_evidence"),
        "has_direction_review_evidence": state.get("has_direction_review_evidence"),
    }


def next_actions_for_state(state: dict[str, Any]) -> list[dict[str, str]]:
    baseline = str(state.get("baseline_evidence_status", "unknown"))
    claim = str(state.get("claim_status", "unknown"))
    if baseline == "executed_primary_gate_failed" or claim == "pending_not_reportable":
        return [
            {
                "action": "diagnose_primary_gate_failure",
                "agent_mode": "analysis",
                "reason": "Baseline evidence exists, but primary objective gates are failing.",
            },
            {
                "action": "preserve_nooption_singlex_pair",
                "agent_mode": "planning",
                "reason": "Do not pivot from the coupled no-option/singlex comparison until comparable gate evidence is available.",
            },
            {
                "action": "write_pending_not_reportable_status",
                "agent_mode": "writing",
                "reason": "RunLog/report text must separate executed evidence from reportable evidence.",
            },
        ]
    if baseline == "missing_or_not_in_bundle":
        return [
            {
                "action": "run_plan_then_paired_baseline",
                "agent_mode": "planning",
                "reason": "Evidence bundle lacks the coupled baseline needed for module-level judgement.",
            }
        ]
    return [
        {
            "action": "review_direction_artifacts_before_next_run",
            "agent_mode": "analysis",
            "reason": "Baseline state is present but not classified as promotion-ready.",
        }
    ]


def allowed_operations(layout: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "plan",
            "command": rel_command(layout, "plan"),
            "agent_mode": "planning",
            "approval_required": False,
            "mutation_policy": "read_only_plan",
            "purpose": "List Module03 scanner, preset, and dashboard command surfaces.",
        },
        {
            "id": "single-nooption",
            "command": rel_command(layout, "single-nooption --exec"),
            "agent_mode": "analysis",
            "approval_required": True,
            "mutation_policy": "module_artifact_write",
            "expected_outputs": ["data/metadata/*nooption*", "outputs/tables/*nooption*"],
            "purpose": "Run the no-option baseline branch of the primary objective pair.",
        },
        {
            "id": "single-singlex",
            "command": rel_command(layout, "single-singlex --exec"),
            "agent_mode": "analysis",
            "approval_required": True,
            "mutation_policy": "module_artifact_write",
            "expected_outputs": ["data/metadata/*singlex*", "outputs/tables/*singlex*"],
            "purpose": "Run the singlex baseline branch of the primary objective pair.",
        },
        {
            "id": "paired",
            "command": rel_command(layout, "paired --exec"),
            "agent_mode": "analysis",
            "approval_required": True,
            "mutation_policy": "module_artifact_write",
            "expected_outputs": ["data/metadata/*paired_preset_summary*", "outputs/tables/*paired*"],
            "purpose": "Run no-option and singlex together and build the paired dashboard.",
        },
        {
            "id": "overnight",
            "command": rel_command(layout, "overnight --exec"),
            "agent_mode": "analysis",
            "approval_required": True,
            "mutation_policy": "long_module_artifact_write",
            "purpose": "Run a long validation campaign; requires explicit runtime and system-safety approval.",
        },
        {
            "id": "contract-ci",
            "command": rel_command(layout, "contract-ci --exec"),
            "agent_mode": "development",
            "approval_required": True,
            "mutation_policy": "module_artifact_write",
            "purpose": "Run module contract checks and the fast coupled baseline smoke when inputs exist.",
        },
    ]


def build_profile(
    repo: pathlib.Path,
    *,
    evidence_bundle: pathlib.Path | None = None,
    include_git: bool = False,
) -> dict[str, Any]:
    layout = detect_layout(repo)
    repo_root = pathlib.Path(layout["repo_root"])
    module_root = pathlib.Path(layout["module_root"])
    entrypoint = module_root / "scripts" / "run_module_03.sh"
    entrypoint_modes = mode_names_from_entrypoint(entrypoint)
    current_state = evidence_current_state(evidence_bundle)
    required_files = {
        rel: file_meta(module_root / rel, rel)
        for rel in (
            "README.md",
            "contract.yaml",
            "pyproject.toml",
            "scripts/run_module_03.sh",
            "scripts/modeling/run_phase_b_regspec_preset.py",
            "scripts/modeling/run_phase_b_bikard_machine_scientist_scan.py",
            "scripts/reporting/build_phase_b_regspec_dashboard.py",
            "regspec_machine/orchestrator.py",
            "tests/test_orchestrator.py",
        )
    }
    gates = {
        "entrypoint_exists": entrypoint.exists(),
        "required_files_present": all(meta["exists"] for meta in required_files.values()),
        "canonical_modes_present": all(mode in entrypoint_modes for mode in CANONICAL_MODES),
        "missing_canonical_modes": [mode for mode in CANONICAL_MODES if mode not in entrypoint_modes],
        "reportability_status": current_state.get("claim_status", "unknown"),
        "baseline_evidence_status": current_state.get("baseline_evidence_status", "unknown"),
    }
    profile: dict[str, Any] = {
        "kind": "twinpaper_module_operation_profile",
        "version": 1,
        "created_at": utc_now(),
        "project_key": "twinpaper",
        "module_id": "module03_regspec_machine",
        "module_name": "Module 03: RegSpec-Machine",
        "layout": {
            "kind": layout["layout"],
            "repo_root": str(repo_root),
            "module_root": str(module_root),
            "module_path": layout["module_path"],
            "canonical_entrypoint": rel_command(layout, ""),
        },
        "purpose": [
            "Operate the coupled no-option and singlex search/validation path.",
            "Preserve holdout/FDR/bootstrap/restart governance before reportability or direction changes.",
            "Turn Module03 results into explicit evidence states for Offdesk and Ondesk handoff.",
        ],
        "required_files": required_files,
        "entrypoint_modes": entrypoint_modes,
        "allowed_operations": allowed_operations(layout),
        "forbidden_actions": [
            "Do not call run_phase_b_* Python scripts directly when the wrapper can express the operation.",
            "Do not use module-local ./scripts/run_module_03.sh from a monorepo-root Offdesk prompt.",
            "Do not delete, move, archive, or clean Module03 files without separate closeout review and human approval.",
            "Do not treat open-explore exploratory evidence as promotion-ready direction-review evidence.",
            "Do not mark a research claim reportable while primary_objective_gate evidence is failed or absent.",
        ],
        "safety_policy": {
            "default_target_repo_mode": "read_only",
            "runtime_execution_requires_dispatch_approval": True,
            "long_runs_require_local_tmux": True,
            "system_critical_guards": [
                "no file deletion or cleanup",
                "no reboot, shutdown, service restart, storage, RAID, NVMe, mount, driver, firmware, or BIOS mutation",
                "no package install or permission change without explicit operator approval",
            ],
        },
        "evidence_contract": {
            "primary_artifacts": [
                "docs/operations/RunLog.md",
                "data/metadata/*machine_scientist_direction_review*.json",
                "data/metadata/*paired_preset_summary*.json",
                "data/metadata/*run_summary*nooption*.json",
                "data/metadata/*run_summary*singlex*.json",
            ],
            "required_metrics": ["validated_candidate", "p/q", "restart_stability", "primary_objective_gate"],
            "coupled_modes": ["no-option", "singlex"],
        },
        "reportability_vocabulary": {
            "executed_primary_gate_failed": "No-option/singlex evidence exists, but primary objective gates failed; the result is not reportable as a success.",
            "pending_not_reportable": "Evidence can be discussed as operational status, but not promoted to a research claim.",
            "exploratory_evidence_available": "Open-explore evidence may exist, but it is secondary until comparable promotion gates are satisfied.",
            "promotion_ready_evidence_absent": "Required promotion-gate evidence is absent or failed.",
        },
        "current_state": current_state,
        "operation_gates": gates,
        "next_actions": next_actions_for_state(current_state),
        "ondesk_return": {
            "required_first_reads": [
                "docs/operations/RunLog.md",
                "data/metadata/*machine_scientist_direction_review*.json",
                "data/metadata/*paired_preset_summary*.json",
                "this module operation profile",
            ],
            "handoff_summary_should_answer": [
                "Which operation ran or was planned?",
                "Which evidence gates passed, failed, or are missing?",
                "Is the state reportable, exploratory, or blocked?",
                "Which wiki entries affected the judgement?",
            ],
        },
    }
    if include_git:
        profile["git_snapshot"] = {
            "module_root": str(module_root),
            "status_short": run_git(module_root, ["status", "--short"]),
            "diff_stat": run_git(module_root, ["diff", "--stat"]),
            "head": run_git(module_root, ["rev-parse", "HEAD"]),
        }
    return profile


def compact_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": profile["kind"],
        "version": profile["version"],
        "module_id": profile["module_id"],
        "module_name": profile["module_name"],
        "layout": profile["layout"],
        "allowed_operations": [
            {
                "id": item["id"],
                "command": item["command"],
                "agent_mode": item["agent_mode"],
                "approval_required": item["approval_required"],
                "mutation_policy": item["mutation_policy"],
            }
            for item in profile["allowed_operations"]
        ],
        "forbidden_actions": profile["forbidden_actions"],
        "evidence_contract": profile["evidence_contract"],
        "reportability_vocabulary": profile["reportability_vocabulary"],
        "current_state": profile["current_state"],
        "operation_gates": profile["operation_gates"],
        "next_actions": profile["next_actions"],
        "ondesk_return": profile["ondesk_return"],
    }


def write_markdown(path: pathlib.Path, profile: dict[str, Any]) -> None:
    lines = [
        "# TwinPaper Module03 Operation Profile",
        "",
        f"- created_at: `{profile['created_at']}`",
        f"- module_id: `{profile['module_id']}`",
        f"- layout: `{profile['layout']['kind']}`",
        f"- repo_root: `{profile['layout']['repo_root']}`",
        f"- module_root: `{profile['layout']['module_root']}`",
        f"- baseline_evidence_status: `{profile['current_state']['baseline_evidence_status']}`",
        f"- claim_status: `{profile['current_state']['claim_status']}`",
        "",
        "## Allowed Operations",
        "",
    ]
    for operation in profile["allowed_operations"]:
        lines.append(
            f"- `{operation['id']}`: `{operation['command']}` "
            f"approval_required={operation['approval_required']} policy=`{operation['mutation_policy']}`"
        )
    lines.extend(["", "## Operation Gates", "", "```json"])
    lines.append(json.dumps(profile["operation_gates"], ensure_ascii=False, indent=2))
    lines.extend(["```", "", "## Reportability Vocabulary", ""])
    for key, value in profile["reportability_vocabulary"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Next Actions", ""])
    for action in profile["next_actions"]:
        lines.append(f"- `{action['action']}` ({action['agent_mode']}): {action['reason']}")
    lines.extend(["", "## Forbidden Actions", ""])
    lines.extend(f"- {item}" for item in profile["forbidden_actions"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = build_profile(args.repo, evidence_bundle=args.evidence_bundle, include_git=args.include_git)
    out_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_path.with_name("MODULE03_OPERATION_PROFILE.md"), profile)
    print(
        json.dumps(
            {
                "out": str(out_path),
                "module_id": profile["module_id"],
                "baseline_evidence_status": profile["current_state"]["baseline_evidence_status"],
                "claim_status": profile["current_state"]["claim_status"],
                "allowed_operations": len(profile["allowed_operations"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
