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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TWINPAPER_REPO = pathlib.Path("/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper")
DEFAULT_BASE_URL = os.environ.get("OFFDESK_LLM_BASE_URL", "http://172.16.0.37:11434")
DEFAULT_MODEL = os.environ.get("OFFDESK_LLM_MODEL", "qwen3-coder-next:latest")
DEFAULT_PROFILE = os.environ.get("OFFDESK_PROFILE", "twinpaper-adaptive-debug")
DEFAULT_TELEGRAM_ENV_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_TELEGRAM_ENV",
        "/home/kimyoungjin06/Desktop/Workspace/aoe_orch_control/.aoe-team/telegram.env",
    )
)
REVIEW_GENERATE_VALUES = {"generate", "auto"}
REVIEW_CASES = {"review_offdesk_stage_contract", "workload_manifest_review"}
EXPECTED_MODULE_SCOPE = "module03_regspec_machine"
EXPECTED_MODULE_PROFILE_KIND = "twinpaper_module03_regspec_machine"
REQUIRED_MODULE_PREFLIGHT_PURPOSES = {
    "build_evidence_bundle",
    "review_evidence_bundle",
    "build_module_operation_profile",
    "prepare_offdesk_task_after_review",
}
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
PROMPT_PACKAGE_STOP_WARNING = (
    "prompt_package_council_requires_external_reviewer_execution_and_will_stop_on_needs_council_execution"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-key", default="twinpaper")
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_TWINPAPER_REPO)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument(
        "--run-until-local",
        help="Prepare the workload to run until the next local HH:MM in --run-until-timezone.",
    )
    parser.add_argument(
        "--run-until-timezone",
        default="Asia/Seoul",
        help="IANA timezone for --run-until-local. Defaults to Asia/Seoul.",
    )
    parser.add_argument(
        "--run-until-kst",
        help="Shortcut for --run-until-local HH:MM --run-until-timezone Asia/Seoul.",
    )
    parser.add_argument("--max-iterations", type=int, default=12)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument(
        "--council-mode",
        choices=("disabled", "prompt-package", "mock", "command"),
        default=os.environ.get("OFFDESK_COUNCIL_MODE", "disabled"),
        help="Configure the workload to run a GPT/Claude council between episodes.",
    )
    parser.add_argument("--council-every", type=int, default=1)
    parser.add_argument("--gpt-council-command", default=os.environ.get("OFFDESK_GPT_COUNCIL_CMD"))
    parser.add_argument("--claude-council-command", default=os.environ.get("OFFDESK_CLAUDE_COUNCIL_CMD"))
    parser.add_argument(
        "--no-council-stop-on-non-continue",
        action="store_false",
        dest="council_stop_on_non_continue",
        default=True,
    )
    parser.add_argument(
        "--council-operator-decision-relay",
        choices=("disabled", "telegram"),
        default=os.environ.get("OFFDESK_COUNCIL_OPERATOR_DECISION_RELAY", "disabled"),
        help="Ask the operator for a continuation decision when Council returns a non-continue decision.",
    )
    parser.add_argument(
        "--telegram-env-file",
        type=pathlib.Path,
        default=DEFAULT_TELEGRAM_ENV_FILE,
        help="Env file containing TELEGRAM_BOT_TOKEN and owner/allow chat settings.",
    )
    parser.add_argument(
        "--telegram-decision-timeout-sec",
        type=int,
        default=int(os.environ.get("OFFDESK_TELEGRAM_DECISION_TIMEOUT_SEC", "1800")),
        help="How long the workload should wait for a Telegram operator decision.",
    )
    parser.add_argument(
        "--telegram-decision-poll-interval-sec",
        type=float,
        default=float(os.environ.get("OFFDESK_TELEGRAM_DECISION_POLL_INTERVAL_SEC", "5")),
        help="Polling interval for Telegram decision replies.",
    )
    parser.add_argument(
        "--telegram-decision-dry-run",
        action="store_true",
        default=os.environ.get("OFFDESK_TELEGRAM_DECISION_DRY_RUN", "0") in {"1", "true", "yes", "on"},
        help="Write relay artifacts without sending Telegram messages.",
    )
    parser.add_argument(
        "--wiki-candidate-mode",
        choices=("disabled", "candidate"),
        default=os.environ.get("OFFDESK_WIKI_CANDIDATE_MODE", "candidate"),
        help="Allow the overnight workload to write post-run review learning candidates into the adaptive wiki candidate queue.",
    )
    parser.add_argument(
        "--wiki-trial-mode",
        choices=("disabled", "council"),
        default=os.environ.get("OFFDESK_WIKI_TRIAL_MODE", "council"),
        help="Allow Council-agreed run-local provisional wiki context for the remaining overnight campaign.",
    )
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
        "--module-preflight-artifact",
        default="latest",
        help=(
            "Path to MODULE_OPERATION_PREFLIGHT.json, 'latest' to use the newest "
            "matching project initialization, or 'none' to require --allow-preflight-blockers."
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


def parse_hhmm(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("time must be within 00:00..23:59")
    return hour, minute


def compute_run_until_schedule(args: argparse.Namespace) -> dict[str, Any]:
    run_until = args.run_until_kst or args.run_until_local
    timezone_name = "Asia/Seoul" if args.run_until_kst else args.run_until_timezone
    if not run_until:
        return {
            "mode": "duration_minutes",
            "duration_minutes": args.duration_minutes,
            "target_time_local": None,
            "timezone": None,
            "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "target_at": None,
        }
    try:
        hour, minute = parse_hhmm(run_until)
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise SystemExit(f"invalid run-until schedule: {error}") from error
    now = dt.datetime.now(timezone)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    duration_minutes = max(0.0, (target - now).total_seconds() / 60.0)
    args.duration_minutes = duration_minutes
    args.run_until_local = run_until
    args.run_until_timezone = timezone_name
    return {
        "mode": "run_until_local",
        "duration_minutes": duration_minutes,
        "target_time_local": run_until,
        "timezone": timezone_name,
        "computed_at": now.isoformat(),
        "target_at": target.isoformat(),
    }


def shell_join(args: list[str]) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def profile_dir(profile: str) -> pathlib.Path:
    home = pathlib.Path.home()
    config_base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    primary = config_base / "forager"
    legacy_candidates = [
        config_base / "agent-of-empires",
        home / ".agent-of-empires",
    ]
    if primary.exists():
        base = primary
    else:
        base = next((candidate for candidate in legacy_candidates if candidate.exists()), primary)
    return base / "profiles" / profile


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


def latest_module_preflight_artifact(profile: str, project_key: str) -> pathlib.Path | None:
    root = profile_dir(profile) / "project_initializations"
    if not root.exists():
        return None
    candidates: list[tuple[str, pathlib.Path]] = []
    for profile_path in root.glob("*/PROJECT_OPERATION_PROFILE.json"):
        try:
            data = load_json_file(profile_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("project_key") != project_key:
            continue
        preflight = data.get("module_operation_preflight_path")
        preflight_path = (
            pathlib.Path(preflight).expanduser()
            if isinstance(preflight, str) and preflight.strip()
            else profile_path.with_name("MODULE_OPERATION_PREFLIGHT.json")
        )
        if not preflight_path.exists():
            continue
        generated_at = str(data.get("generated_at") or profile_path.stat().st_mtime)
        candidates.append((generated_at, preflight_path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


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


def resolve_module_preflight_artifact(
    value: str | None,
    profile: str,
    project_key: str,
) -> pathlib.Path | None:
    if not value or value == "none":
        return None
    if value == "latest":
        return latest_module_preflight_artifact(profile, project_key)
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


def summarize_module_preflight(path: pathlib.Path | None, project_key: str) -> dict[str, Any]:
    if path is None:
        return {
            "path": None,
            "ready": False,
            "reason": "missing_module_preflight_artifact",
        }
    summary: dict[str, Any] = {
        "path": str(path),
        "ready": False,
    }
    if not path.exists():
        summary["reason"] = "module_preflight_artifact_not_found"
        return summary
    try:
        data = load_json_file(path)
    except (OSError, json.JSONDecodeError) as error:
        summary["reason"] = "module_preflight_artifact_unreadable"
        summary["error"] = repr(error)
        return summary
    if not isinstance(data, dict):
        summary["reason"] = "module_preflight_artifact_not_object"
        return summary

    blockers: list[str] = []
    if data.get("kind") != "forager_module_operation_preflight":
        blockers.append("module_preflight_wrong_kind")
    if data.get("project_key") != project_key:
        blockers.append("module_preflight_project_key_mismatch")

    targets = data.get("operation_targets")
    if not isinstance(targets, list):
        targets = []
        blockers.append("module_preflight_missing_operation_targets")
    target = next(
        (
            item
            for item in targets
            if isinstance(item, dict) and item.get("scope_ref") == EXPECTED_MODULE_SCOPE
        ),
        None,
    )
    if not isinstance(target, dict):
        blockers.append(f"module_preflight_missing_target:{EXPECTED_MODULE_SCOPE}")
        target = {}

    target_blockers = target.get("blockers", [])
    recommended_commands = target.get("recommended_commands", [])
    recommended_purposes = {
        str(command.get("purpose"))
        for command in recommended_commands
        if isinstance(command, dict) and command.get("purpose")
    }
    missing_purposes = sorted(REQUIRED_MODULE_PREFLIGHT_PURPOSES - recommended_purposes)
    if target.get("recognized_profile_kind") != EXPECTED_MODULE_PROFILE_KIND:
        blockers.append("module_preflight_unrecognized_profile_kind")
    if target.get("profile_builder_available") is not True:
        blockers.append("module_preflight_profile_builder_missing")
    if target.get("evidence_bundle_builder_available") is not True:
        blockers.append("module_preflight_evidence_bundle_builder_missing")
    if target.get("evidence_review_builder_available") is not True:
        blockers.append("module_preflight_evidence_review_builder_missing")
    if missing_purposes:
        blockers.append("module_preflight_missing_recommended_commands")

    ready = not blockers
    summary.update(
        {
            "ready": ready,
            "kind": data.get("kind"),
            "project_key": data.get("project_key"),
            "ready_for_offdesk_runtime": data.get("ready_for_offdesk_runtime"),
            "target_count": len(targets),
            "scope_ref": target.get("scope_ref"),
            "readiness_level": target.get("readiness_level"),
            "recognized_profile_kind": target.get("recognized_profile_kind"),
            "profile_builder_available": target.get("profile_builder_available"),
            "evidence_bundle_builder_available": target.get("evidence_bundle_builder_available"),
            "evidence_review_builder_available": target.get("evidence_review_builder_available"),
            "recommended_command_purposes": sorted(recommended_purposes),
            "missing_recommended_command_purposes": missing_purposes,
            "blocker_count": len(target_blockers) if isinstance(target_blockers, list) else None,
            "blocking_reasons": blockers,
            "reason": "module_preflight_target_ready" if ready else ",".join(blockers),
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
    module_preflight: dict[str, Any],
    evidence_review: dict[str, Any],
    council_config: dict[str, Any],
    allow_blockers: bool,
) -> dict[str, Any]:
    blockers = []
    warnings = []
    if not role_gate["ready"]:
        blockers.append(role_gate["reason"])
    if not review_artifact["ready"]:
        blockers.append(review_artifact["reason"])
    if not module_preflight["ready"]:
        blockers.extend(module_preflight.get("blocking_reasons") or [module_preflight["reason"]])
    if not evidence_review["ready"]:
        blockers.append(evidence_review["reason"])
    if not council_config["ready"]:
        blockers.extend(council_config.get("blocking_reasons") or [council_config["reason"]])
    warnings.extend(council_config.get("warnings") or [])
    return {
        "role_gate": role_gate,
        "review_artifact": review_artifact,
        "module_operation_preflight": module_preflight,
        "evidence_review": evidence_review,
        "council": council_config,
        "blocking_reasons": blockers,
        "warnings": warnings,
        "ready_for_enqueue": not blockers,
        "enqueue_allowed": not blockers or allow_blockers,
        "allow_preflight_blockers": allow_blockers,
    }


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def summarize_operator_decision_relay(args: argparse.Namespace) -> dict[str, Any]:
    if args.council_operator_decision_relay == "disabled":
        return {
            "mode": "disabled",
            "ready": True,
            "reason": "operator_decision_relay_disabled",
            "blocking_reasons": [],
            "warnings": [],
        }
    blockers = []
    warnings = [
        "telegram_operator_decision_relay_controls_continuation_only_not_mutation_approval"
    ]
    if args.council_mode == "disabled":
        blockers.append("telegram_operator_decision_relay_requires_council")
    env = parse_env_file(args.telegram_env_file)
    if not args.telegram_env_file.exists():
        blockers.append("telegram_env_file_missing")
    elif not env.get("TELEGRAM_BOT_TOKEN"):
        blockers.append("telegram_bot_token_missing")
    elif not (env.get("TELEGRAM_OWNER_CHAT_ID") or env.get("TELEGRAM_ALLOW_CHAT_IDS")):
        blockers.append("telegram_owner_or_allow_chat_missing")
    if args.telegram_decision_timeout_sec <= 0:
        blockers.append("telegram_decision_timeout_must_be_positive")
    ready = not blockers
    return {
        "mode": "telegram",
        "ready": ready,
        "reason": "telegram_operator_decision_relay_configured" if ready else ",".join(blockers),
        "blocking_reasons": blockers,
        "warnings": warnings,
        "env_file": str(args.telegram_env_file),
        "owner_configured": bool(env.get("TELEGRAM_OWNER_CHAT_ID")),
        "allow_list_configured": bool(env.get("TELEGRAM_ALLOW_CHAT_IDS")),
        "timeout_sec": max(0, args.telegram_decision_timeout_sec),
        "poll_interval_sec": max(0.2, args.telegram_decision_poll_interval_sec),
        "dry_run": bool(args.telegram_decision_dry_run),
        "allowed_decisions": ["continue", "revise", "block", "stop"],
    }


def summarize_council_config(args: argparse.Namespace) -> dict[str, Any]:
    operator_relay = summarize_operator_decision_relay(args)
    if args.council_mode == "disabled":
        return {
            "mode": args.council_mode,
            "ready": operator_relay["ready"],
            "reason": "council_disabled",
            "blocking_reasons": operator_relay["blocking_reasons"],
            "warnings": operator_relay["warnings"],
            "operator_decision_relay": operator_relay,
        }
    blockers = []
    warnings = []
    if args.council_mode == "command" and not args.gpt_council_command:
        blockers.append("gpt_council_command_missing")
    if args.council_mode == "command" and not args.claude_council_command:
        blockers.append("claude_council_command_missing")
    if args.council_mode == "prompt-package":
        warnings.append("prompt_package_council_writes_reviewer_prompts_but_does_not_execute_them")
        if args.council_stop_on_non_continue and operator_relay["mode"] == "disabled":
            warnings.append(PROMPT_PACKAGE_STOP_WARNING)
        elif args.council_stop_on_non_continue:
            warnings.append("prompt_package_council_can_continue_only_after_operator_decision_relay")
        else:
            warnings.append("prompt_package_council_records_needs_council_execution_without_stopping")
    blockers.extend(operator_relay["blocking_reasons"])
    warnings.extend(operator_relay["warnings"])
    ready = not blockers
    return {
        "mode": args.council_mode,
        "ready": ready,
        "reason": "council_configured" if ready else ",".join(blockers),
        "blocking_reasons": blockers,
        "warnings": warnings,
        "reviewers": ["gpt", "claude"],
        "every": max(1, args.council_every),
        "stop_on_non_continue": args.council_stop_on_non_continue,
        "gpt_command_configured": bool(args.gpt_council_command),
        "claude_command_configured": bool(args.claude_council_command),
        "operator_decision_relay": operator_relay,
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
    command = [
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
    if args.run_until_local:
        command.extend(
            [
                "--run-until-local",
                args.run_until_local,
                "--run-until-timezone",
                args.run_until_timezone,
            ]
        )
    if args.council_mode != "disabled":
        command.extend(
            [
                "--council-mode",
                args.council_mode,
                "--council-every",
                str(max(1, args.council_every)),
            ]
        )
        if args.gpt_council_command:
            command.extend(["--gpt-council-command", args.gpt_council_command])
        if args.claude_council_command:
            command.extend(["--claude-council-command", args.claude_council_command])
        if not args.council_stop_on_non_continue:
            command.append("--no-council-stop-on-non-continue")
        if args.council_operator_decision_relay != "disabled":
            command.extend(
                [
                    "--council-operator-decision-relay",
                    args.council_operator_decision_relay,
                    "--telegram-env-file",
                    str(args.telegram_env_file),
                    "--telegram-decision-timeout-sec",
                    str(max(0, args.telegram_decision_timeout_sec)),
                    "--telegram-decision-poll-interval-sec",
                    str(max(0.2, args.telegram_decision_poll_interval_sec)),
                    "--decision-ledger-profile-dir",
                    str(profile_dir(args.profile)),
                    "--forager-bin",
                    str(args.forager_bin),
                ]
            )
            if args.telegram_decision_dry_run:
                command.append("--telegram-decision-dry-run")
    if args.wiki_candidate_mode != "disabled":
        command.extend(
            [
                "--wiki-candidate-mode",
                args.wiki_candidate_mode,
                "--wiki-candidate-profile-dir",
                str(profile_dir(args.profile)),
            ]
        )
    if args.wiki_trial_mode != "disabled":
        command.extend(["--wiki-trial-mode", args.wiki_trial_mode])
    return command


def build_enqueue_args(
    *,
    args: argparse.Namespace,
    out_dir: pathlib.Path,
    request_id: str,
    task_id: str,
    command: str,
) -> list[str]:
    if args.run_until_local:
        preview = (
            f"read-only TwinPaper offdesk autonomy workload until "
            f"{args.run_until_local} {args.run_until_timezone} "
            f"(estimated {args.duration_minutes:.1f} minutes)"
        )
    else:
        preview = f"{args.duration_minutes:g}-minute read-only TwinPaper offdesk autonomy workload"
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
        preview,
        "--reason",
        "Prepare and test Offdesk autonomous mode on TwinPaper with qwen read-only diagnostics and system-critical mutation guards.",
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


def markdown_scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    return str(value)


def render_launch_dry_run_report(
    *,
    args: argparse.Namespace,
    manifest: dict[str, Any],
    preflight: dict[str, Any],
    role_gate: dict[str, Any],
    review_artifact: dict[str, Any],
    module_preflight: dict[str, Any],
    evidence_review: dict[str, Any],
    council_config: dict[str, Any],
    schedule: dict[str, Any],
) -> str:
    blockers = preflight.get("blocking_reasons") or []
    warnings = preflight.get("warnings") or []
    module_purposes = module_preflight.get("recommended_command_purposes") or []
    operator_relay = council_config.get("operator_decision_relay") or {}
    lines = [
        "# TwinPaper Launch Dry Run",
        "",
        "This report is an operator-facing launch review packet. It does not prove the workload is complete, and it does not launch runtime work unless the preparation command was run with `--enqueue`.",
        "",
        "## Scope",
        "",
        f"- project_key: `{manifest['project_key']}`",
        f"- request_id: `{manifest['request_id']}`",
        f"- task_id: `{manifest['task_id']}`",
        f"- repo: `{manifest['repo']}`",
        f"- out_dir: `{manifest['out_dir']}`",
        f"- runner: `{manifest['safety']['runner']}`",
        f"- provider_model: `{manifest['provider']}:{manifest['model']}`",
        f"- schedule_mode: `{schedule['mode']}`",
        f"- schedule_target_at: `{markdown_scalar(schedule.get('target_at'))}`",
        f"- scheduled_duration_minutes: `{schedule['duration_minutes']:.1f}`",
        f"- enqueue_requested_in_this_run: `{markdown_scalar(args.enqueue)}`",
        "",
        "## Preflight Verdict",
        "",
        f"- ready_for_enqueue: `{markdown_scalar(preflight['ready_for_enqueue'])}`",
        f"- enqueue_allowed: `{markdown_scalar(preflight['enqueue_allowed'])}`",
        f"- allow_preflight_blockers: `{markdown_scalar(preflight['allow_preflight_blockers'])}`",
        f"- role_gate: `{markdown_scalar(role_gate['ready'])}` reason=`{role_gate.get('reason')}` path=`{role_gate.get('path')}`",
        f"- workload_review: `{markdown_scalar(review_artifact['ready'])}` reason=`{review_artifact.get('reason')}` path=`{review_artifact.get('path')}`",
        f"- module_preflight: `{markdown_scalar(module_preflight['ready'])}` reason=`{module_preflight.get('reason')}` path=`{module_preflight.get('path')}`",
        f"- module_scope: `{module_preflight.get('scope_ref')}`",
        f"- module_profile_kind: `{module_preflight.get('recognized_profile_kind')}`",
        f"- module_command_purposes: `{', '.join(module_purposes) if module_purposes else 'none'}`",
        f"- evidence_review: `{markdown_scalar(evidence_review['ready'])}` reason=`{evidence_review.get('reason')}` path=`{evidence_review.get('path')}`",
        f"- council: `{markdown_scalar(council_config['ready'])}` mode=`{council_config['mode']}` reason=`{council_config.get('reason')}`",
        f"- council_stop_on_non_continue: `{markdown_scalar(council_config.get('stop_on_non_continue'))}`",
        f"- council_operator_decision_relay: `{operator_relay.get('mode')}` ready=`{markdown_scalar(operator_relay.get('ready'))}` timeout_sec=`{operator_relay.get('timeout_sec')}`",
        "",
        "## Blockers",
        "",
    ]
    if blockers:
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Warnings",
            "",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
        if PROMPT_PACKAGE_STOP_WARNING in warnings:
            lines.append(
                "- prompt-package council writes reviewer prompts but does not run GPT/Claude reviewers; with `stop_on_non_continue=true`, `needs_council_execution` is an expected hard stop."
            )
        if "prompt_package_council_can_continue_only_after_operator_decision_relay" in warnings:
            lines.append(
                "- prompt-package council still does not run GPT/Claude reviewers; Telegram relay can only apply an explicit operator continuation decision."
            )
        if "telegram_operator_decision_relay_controls_continuation_only_not_mutation_approval" in warnings:
            lines.append(
                "- Telegram replies are limited to continuation control and do not approve mutations, cleanup, provider retargeting, wiki promotion, or file changes."
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "- Runtime dispatch still requires the normal `dispatch.runtime` approval path.",
            "- The target TwinPaper repo is treated as read-only.",
            "- File deletion, cleanup, reboot, service restart, storage, package, permission, process, network, kernel, firmware, and power-state mutations remain forbidden without separate approval.",
            "- Adaptive wiki writes are limited to the configured candidate/trial stores; promotion is not allowed here.",
            "- Telegram operator decisions, when enabled, are stored as workload artifacts and are not canonical approval records.",
            "",
            "## Operator Commands",
            "",
            "Inspect first:",
            "",
            "```bash",
            f"cat {shlex.quote(manifest['artifacts']['preflight'])}",
            f"cat {shlex.quote(manifest['artifacts']['prepared_task'])}",
            f"cat {shlex.quote(manifest['artifacts']['launch_dry_run_report'])}",
            "```",
            "",
            "Enqueue only after review:",
            "",
            "```bash",
            f"bash {shlex.quote(str(pathlib.Path(manifest['out_dir']) / 'offdesk_enqueue_command.sh'))}",
            manifest["commands"]["pending"],
            manifest["commands"]["approve_oldest_then_tick"],
            manifest["commands"]["poll"],
            "```",
            "",
            "## Key Artifacts",
            "",
            f"- prepared_task: `{manifest['artifacts']['prepared_task']}`",
            f"- preflight: `{manifest['artifacts']['preflight']}`",
            f"- workload_review: `{manifest['artifacts']['review_artifact']}`",
            f"- module_operation_preflight: `{manifest['artifacts']['module_operation_preflight']}`",
            f"- evidence_review: `{manifest['artifacts']['evidence_review']}`",
            f"- runner_log: `{manifest['artifacts']['runner_log']}`",
            f"- result: `{manifest['artifacts']['result']}`",
            "",
        ]
    )
    return "\n".join(lines)


def render_long_run_validation_packet(
    *,
    manifest: dict[str, Any],
    preflight: dict[str, Any],
    schedule: dict[str, Any],
) -> str:
    blockers = preflight.get("blocking_reasons") or []
    warnings = preflight.get("warnings") or []
    commands = manifest["commands"]
    artifacts = manifest["artifacts"]
    operator_relay = manifest["council"].get("operator_decision_relay") or {}
    lines = [
        "# TwinPaper Long-Run Validation Packet",
        "",
        "This packet turns a prepared TwinPaper Offdesk workload into an auditable validation sequence. It does not launch runtime work by itself, approve actions, close out files, or promote wiki entries.",
        "",
        "## Validation Goal",
        "",
        "- Prove more than launch mechanics: Council usefulness, output quality, closeout ergonomics, Ondesk return readiness, and wiki review load.",
        "- Keep runtime work under `local-tmux` so progress, heartbeat, logs, and result artifacts remain inspectable.",
        "- Treat Offdesk output as evidence requiring review, not as trusted completion.",
        "",
        "## Prepared Scope",
        "",
        f"- project_key: `{manifest['project_key']}`",
        f"- request_id: `{manifest['request_id']}`",
        f"- task_id: `{manifest['task_id']}`",
        f"- runner: `{manifest['safety']['runner']}`",
        f"- council_mode: `{manifest['council']['mode']}`",
        f"- council_stop_on_non_continue: `{markdown_scalar(manifest['council'].get('stop_on_non_continue'))}`",
        f"- council_operator_decision_relay: `{operator_relay.get('mode')}`",
        f"- telegram_decision_timeout_sec: `{operator_relay.get('timeout_sec')}`",
        f"- wiki_candidate_mode: `{manifest['adaptive_wiki_learning']['candidate_mode']}`",
        f"- wiki_trial_mode: `{manifest['adaptive_wiki_learning']['trial_mode']}`",
        f"- schedule_mode: `{schedule['mode']}`",
        f"- schedule_target_at: `{markdown_scalar(schedule.get('target_at'))}`",
        f"- scheduled_duration_minutes: `{schedule['duration_minutes']:.1f}`",
        "",
        "## Gate 1: Prepare Review",
        "",
        f"- ready_for_enqueue: `{markdown_scalar(preflight['ready_for_enqueue'])}`",
        f"- enqueue_allowed: `{markdown_scalar(preflight['enqueue_allowed'])}`",
        "",
    ]
    if blockers:
        lines.append("Blockers:")
        lines.extend(f"- {blocker}" for blocker in blockers)
    else:
        lines.append("Blockers: none")
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
        if PROMPT_PACKAGE_STOP_WARNING in warnings:
            lines.append(
                "- prompt-package council writes reviewer prompts but does not run GPT/Claude reviewers; with `stop_on_non_continue=true`, the run can stop before the scheduled duration."
            )
        if "prompt_package_council_can_continue_only_after_operator_decision_relay" in warnings:
            lines.append(
                "- prompt-package council can continue past `needs_council_execution` only when the Telegram operator relay receives an explicit `continue` decision."
            )
        if "telegram_operator_decision_relay_controls_continuation_only_not_mutation_approval" in warnings:
            lines.append(
                "- Telegram replies only control continuation and do not approve mutations, cleanup, provider retargeting, wiki promotion, or file changes."
            )
    else:
        lines.append("")
        lines.append("Warnings: none")
    lines.extend(
        [
            "",
            "Required first reads:",
            "",
            "```bash",
            f"cat {shlex.quote(artifacts['preflight'])}",
            f"cat {shlex.quote(artifacts['prepared_task'])}",
            f"cat {shlex.quote(artifacts['launch_dry_run_report'])}",
            "```",
            "",
            "Do not continue if `preflight.json` has blockers, the runner is not `local-tmux` for a long run, or the launch dry run does not match the intended project/module scope.",
            "",
            "## Gate 2: Runtime Approval",
            "",
            "```bash",
            commands["enqueue"],
            commands["tick"],
            commands["pending"],
            "```",
            "",
            "Approve only the pending row whose action is `dispatch.runtime`, whose task id matches this packet, and whose preview matches the launch dry run:",
            "",
            "```bash",
            commands["approve_oldest_then_tick"],
            "```",
            "",
            "This approval does not authorize provider fallback, file cleanup, closeout changes, or wiki promotion.",
            "",
            "## Gate 3: Live Monitoring",
            "",
            "```bash",
            commands["poll"],
            commands["tasks"],
            "```",
            "",
            "Inspect the runtime artifacts together; do not report completion from one status field:",
            "",
            f"- runner_log: `{artifacts['runner_log']}`",
            f"- heartbeat: `{artifacts['heartbeat']}`",
            f"- progress: `{artifacts['progress']}`",
            f"- council_progress: `{artifacts['council_progress']}`",
            f"- operator_decision_relay: `{artifacts.get('operator_decision_relay')}`",
            f"- result: `{artifacts['result']}`",
            f"- report: `{artifacts['report']}`",
            "",
            "Stop and inspect if polling reports stale callback, stale heartbeat, missing result, unexpected provider retargeting, or a non-`continue` Council decision without an accepted Telegram operator decision.",
            "",
            "## Gate 4: Completion Evidence",
            "",
            "After `result.json` exists, inspect:",
            "",
            "```bash",
            f"cat {shlex.quote(artifacts['result'])}",
            f"cat {shlex.quote(artifacts['report'])}",
            f"cat {shlex.quote(artifacts['result_review'])}",
            "```",
            "",
            "The final report must separate pass/fail mechanics from operator judgement about quality and direction control.",
            "",
            "## Gate 5: Closeout",
            "",
            "Generate a review packet before returning to live work:",
            "",
            "```bash",
            commands["closeout_dry_run"],
            "```",
            "",
            "Closeout must remain a dry-run planner. If it proposes movement, archive, or deletion, treat that as a separate reviewed approval path.",
            "",
            "## Gate 6: Ondesk Return",
            "",
            "Start the next harness from artifacts, not raw chat history:",
            "",
            "```bash",
            commands["ondesk_prompt_package"],
            "```",
            "",
            "The prompt package should point to the latest closeout return package and tell the fresh harness what to read first.",
            "",
            "## Gate 7: Wiki Review",
            "",
            "Review generated knowledge as candidates before promotion:",
            "",
            "```bash",
            commands["wiki_candidates"],
            commands["wiki_review_active"],
            commands["wiki_review_after_report"],
            commands["wiki_runtime_policy_ack_report"],
            commands["morning_wiki_review"],
            "```",
            "",
            "Promote only entries with explicit evidence refs, correct project/module/agent-mode scope, and no hidden changes to approval, provider, command, or workdir behavior.",
            "",
            "## Hard Stop Conditions",
            "",
            "- `LAUNCH_DRY_RUN.md` reports blockers.",
            "- `LAUNCH_DRY_RUN.md` reports warnings that contradict the validation goal.",
            "- The pending approval is not the expected `dispatch.runtime` approval.",
            "- The task uses `local-background` for a long Python workload.",
            "- Polling reports stale callback, stale heartbeat, missing result, or missing closeout evidence.",
            "- Telegram operator decision relay times out or returns anything other than an explicit request-id-matched decision.",
            "- Closeout proposes file movement or deletion as already authorized.",
            "- A wiki promotion lacks evidence refs or correct scope.",
            "- `ondesk prompt-package` cannot identify the next first reads.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    schedule = compute_run_until_schedule(args)
    stamp = timestamp()
    request_id = f"twinpaper-autonomy-{stamp}"
    task_id = f"twinpaper-autonomy-{stamp}"
    trial_overlay_enabled = args.wiki_trial_mode == "council" and args.council_mode != "disabled"
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
    module_preflight_path = resolve_module_preflight_artifact(
        args.module_preflight_artifact,
        args.profile,
        args.project_key,
    )
    role_gate = summarize_role_gate_result(role_gate_path)
    review_artifact = summarize_review_artifact(review_artifact_path)
    module_preflight = summarize_module_preflight(module_preflight_path, args.project_key)
    council_config = summarize_council_config(args)
    preflight = build_preflight(
        role_gate,
        review_artifact,
        module_preflight,
        evidence_review,
        council_config,
        args.allow_preflight_blockers,
    )
    enqueue_args = build_enqueue_args(
        args=args,
        out_dir=out_dir,
        request_id=request_id,
        task_id=task_id,
        command=enqueue_command,
    )
    tick_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "tick", "--limit", "1", "--json"]
    pending_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "pending", "--json"]
    tasks_args = [
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
    poll_args = [str(args.forager_bin), "-p", args.profile, "offdesk", "poll", "--json"]
    role_gate_args = role_gate_command(args)
    review_args = review_harness_command(args)
    morning_wiki_review_args = [
        "python3",
        str(REPO_ROOT / "scripts" / "review_twinpaper_morning_wiki.py"),
        "--result",
        str(out_dir / "result.json"),
        "--profile-dir",
        str(profile_dir(args.profile)),
        "--out",
        str(out_dir / "morning_wiki_review" / "report.json"),
    ]
    closeout_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "closeout",
        "--project-key",
        args.project_key,
        "--dry-run",
    ]
    ondesk_prompt_package_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "ondesk",
        "prompt-package",
        "--project-key",
        args.project_key,
    ]
    wiki_candidates_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "wiki",
        "candidates",
        "--project-key",
        args.project_key,
        "--json",
    ]
    wiki_review_active_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "wiki",
        "review",
        "--active-only",
        "--json",
    ]
    wiki_review_after_report_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "wiki",
        "review-after-report",
        "--project-key",
        args.project_key,
        "--artifact-kind",
        "report",
        "--agent-mode",
        "critique",
        "--json",
    ]
    wiki_runtime_policy_ack_report_args = [
        str(args.forager_bin),
        "-p",
        args.profile,
        "offdesk",
        "wiki",
        "runtime-policy-ack-report",
        "--project-key",
        args.project_key,
        "--artifact-kind",
        "report",
        "--agent-mode",
        "critique",
        "--json",
    ]
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
        "schedule": schedule,
        "max_iterations": args.max_iterations,
        "provider": "ollama",
        "model": args.model,
        "safety": {
            **SYSTEM_CRITICAL_SAFETY,
            "writes_only_under_out_dir": args.wiki_candidate_mode != "candidate",
            "writes_only_under_out_dir_except_adaptive_wiki_candidate_queue": args.wiki_candidate_mode == "candidate",
            "capability": "dispatch.runtime",
            "runner": args.runner,
            "deterministic_evidence_review_required": True,
            "module_operation_preflight_required": True,
            "approval_required_before_dispatch": True,
            "clean_role_gate_required": True,
            "separate_review_artifact_required": True,
            "episode_council_between_episodes": args.council_mode != "disabled",
            "adaptive_wiki_candidate_queue_write": args.wiki_candidate_mode == "candidate",
            "adaptive_wiki_trial_overlay_write": trial_overlay_enabled,
        },
        "council": council_config,
        "adaptive_wiki_learning": {
            "candidate_mode": args.wiki_candidate_mode,
            "promotion_allowed": False,
            "candidate_store": str(profile_dir(args.profile) / "adaptive_wiki_candidates.json"),
            "trial_mode": args.wiki_trial_mode,
            "trial_enabled": trial_overlay_enabled,
            "trial_store": str(out_dir / "adaptive_wiki_trial_entries.json"),
            "trial_promotion_allowed": False,
        },
        "evidence": {
            "bundle_path": str(evidence_bundle_path),
            "review_path": str(evidence_review_path),
            "review_ready": evidence_review["ready"],
            "review_decision": evidence_review.get("decision"),
            "baseline_evidence_status": evidence_review.get("baseline_evidence_status"),
            "claim_status": evidence_review.get("claim_status"),
        },
        "module_operation_preflight": module_preflight,
        "preflight": preflight,
        "workload_command": workload_command,
        "workload_wrapper": str(wrapper_path),
        "enqueue_args": enqueue_args,
        "commands": {
            "evidence_bundle": shell_join(evidence_bundle_args),
            "evidence_review": shell_join(evidence_review_args),
            "role_gate": shell_join(role_gate_args),
            "review_harness": shell_join(review_args),
            "morning_wiki_review": shell_join(morning_wiki_review_args),
            "enqueue": shell_join(enqueue_args),
            "tick": shell_join(tick_args),
            "pending": shell_join(pending_args),
            "tasks": shell_join(tasks_args),
            "poll": shell_join(poll_args),
            "closeout_dry_run": shell_join(closeout_args),
            "ondesk_prompt_package": shell_join(ondesk_prompt_package_args),
            "wiki_candidates": shell_join(wiki_candidates_args),
            "wiki_review_active": shell_join(wiki_review_active_args),
            "wiki_review_after_report": shell_join(wiki_review_after_report_args),
            "wiki_runtime_policy_ack_report": shell_join(wiki_runtime_policy_ack_report_args),
            "approve_oldest_then_tick": shell_join([str(args.forager_bin), "-p", args.profile, "offdesk", "ok"])
            + " && "
            + shell_join(tick_args),
        },
        "artifacts": {
            "manifest": str(out_dir / "manifest.json"),
            "progress": str(out_dir / "progress.jsonl"),
            "episodes": str(out_dir / "episodes"),
            "council": str(out_dir / "council"),
            "council_progress": str(out_dir / "council_progress.jsonl"),
            "operator_decision_relay": str(out_dir / "council" / "<episode>" / "operator_decision"),
            "heartbeat": str(out_dir / "heartbeat.json"),
            "result": str(out_dir / "result.json"),
            "report": str(out_dir / "REPORT.md"),
            "result_review": str(out_dir / "result_review" / "results.json"),
            "result_review_markdown": str(out_dir / "result_review" / "RESULT_REVIEW.md"),
            "wiki_candidate_ingest": str(out_dir / "result_review" / "wiki_candidate_ingest.json"),
            "wiki_trial_entries": str(out_dir / "adaptive_wiki_trial_entries.json"),
            "morning_wiki_review": str(out_dir / "morning_wiki_review" / "report.json"),
            "morning_wiki_review_markdown": str(out_dir / "morning_wiki_review" / "MORNING_WIKI_REVIEW.md"),
            "runner_log": str(out_dir / "offdesk-runner.log"),
            "prepared_task": str(prepared_task_path),
            "preflight": str(out_dir / "preflight.json"),
            "launch_dry_run_report": str(out_dir / "LAUNCH_DRY_RUN.md"),
            "long_run_validation_packet": str(out_dir / "LONG_RUN_VALIDATION.md"),
            "review_artifact": str(generated_review_path) if review_generate else review_artifact.get("path"),
            "module_operation_preflight": module_preflight.get("path"),
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
        preflight = build_preflight(
            role_gate,
            review_artifact,
            module_preflight,
            evidence_review,
            council_config,
            args.allow_preflight_blockers,
        )
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
            f"- module_preflight_ready: `{module_preflight['ready']}`",
            f"- module_preflight_artifact: `{module_preflight.get('path')}`",
            f"- module_preflight_scope: `{module_preflight.get('scope_ref')}`",
            f"- module_preflight_reason: `{module_preflight.get('reason')}`",
            f"- evidence_ready: `{evidence_review['ready']}`",
            f"- evidence_review: `{evidence_review.get('path')}`",
            f"- evidence_baseline_status: `{evidence_review.get('baseline_evidence_status')}`",
            f"- schedule_mode: `{schedule['mode']}`",
            f"- schedule_target_at: `{schedule.get('target_at')}`",
            f"- scheduled_duration_minutes: `{schedule['duration_minutes']:.1f}`",
            f"- council_mode: `{council_config['mode']}`",
            f"- council_ready: `{council_config['ready']}`",
            f"- council_reason: `{council_config['reason']}`",
            f"- council_operator_decision_relay: `{(council_config.get('operator_decision_relay') or {}).get('mode')}`",
            f"- telegram_decision_timeout_sec: `{(council_config.get('operator_decision_relay') or {}).get('timeout_sec')}`",
            f"- wiki_candidate_mode: `{args.wiki_candidate_mode}`",
            f"- wiki_trial_mode: `{args.wiki_trial_mode}`",
            f"- ready_for_enqueue: `{preflight['ready_for_enqueue']}`",
            f"- blocking_reasons: `{preflight['blocking_reasons']}`",
            f"- warnings: `{preflight['warnings']}`",
            f"- system_critical_constraints: `{', '.join(sorted(SYSTEM_CRITICAL_SAFETY))}`",
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
            manifest["commands"]["closeout_dry_run"],
            manifest["commands"]["ondesk_prompt_package"],
            manifest["commands"]["wiki_candidates"],
            manifest["commands"]["wiki_review_active"],
            "```",
            "",
        ]
    ))
    write_text(
        out_dir / "LAUNCH_DRY_RUN.md",
        render_launch_dry_run_report(
            args=args,
            manifest=manifest,
            preflight=preflight,
            role_gate=role_gate,
            review_artifact=review_artifact,
            module_preflight=module_preflight,
            evidence_review=evidence_review,
            council_config=council_config,
            schedule=schedule,
        )
        + "\n",
    )
    write_text(
        out_dir / "LONG_RUN_VALIDATION.md",
        render_long_run_validation_packet(
            manifest=manifest,
            preflight=preflight,
            schedule=schedule,
        )
        + "\n",
    )
    write_text(prepared_task_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    enqueue_result: dict[str, Any] | None = None
    if args.enqueue:
        if not preflight["enqueue_allowed"]:
            output = {
                "prepared": True,
                "enqueued": False,
                "enqueue_blocked": True,
                "manifest": str(out_dir / "prepared_task.json"),
                "launch_dry_run_report": str(out_dir / "LAUNCH_DRY_RUN.md"),
                "long_run_validation_packet": str(out_dir / "LONG_RUN_VALIDATION.md"),
                "out_dir": str(out_dir),
                "request_id": request_id,
                "task_id": task_id,
                "schedule": schedule,
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
        "launch_dry_run_report": str(out_dir / "LAUNCH_DRY_RUN.md"),
        "long_run_validation_packet": str(out_dir / "LONG_RUN_VALIDATION.md"),
        "out_dir": str(out_dir),
        "request_id": request_id,
        "task_id": task_id,
        "schedule": schedule,
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
