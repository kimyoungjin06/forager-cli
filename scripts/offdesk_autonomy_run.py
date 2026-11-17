#!/usr/bin/env python3
"""Thin systemd adapter for reviewed, bounded Offdesk shift routines.

The Rust shift ledger is the only arming and schedule authority. This adapter
discovers the one active shift when ``--shift-id`` is omitted, immediately
pins that exact ID, asks Rust to select and reserve the manifest-bound handler,
performs only that typed effect, and records one terminal outcome receipt.

It owns no manifest parser, routine selector, disarm policy, mutable armed file,
standing project list, provider default, or free-form shell playbook.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import ipaddress
import json
import os
import pathlib
import subprocess
import sys
import urllib.parse
from typing import Any

REPO = pathlib.Path(__file__).resolve().parent.parent
ROUTINE_TASKS = ("brief", "distill", "tick")
ROUTINE_POLICY_DENIALS = {
    "unbound_routine",
    "command_or_workdir_change",
    "provider_retargeting",
    "destructive_action",
    "unlisted_external_side_effect",
    "wiki_promotion",
    "accepted_truth",
    "plan_revision_without_review",
}
ROUTINE_HANDLER_CONTRACTS = {
    "dispatch_tick": ("dispatch_tick", "bounded_shift_tick", False),
    "wiki_candidate_prereview": (
        "wiki_candidate_distillation",
        "candidate_only_wiki_prereview",
        False,
    ),
    "morning_review_delivery": (
        "morning_review_brief",
        "local_review_artifacts_and_notification",
        True,
    ),
}
ENTERPRISE_PROFILE_CONTRACTS = {
    "single_owner_local": (
        "single_owner",
        "telegram_owner_private",
        ["loopback_model", "telegram_bot_api"],
    ),
    "multi_operator_local": (
        "multi_operator",
        "telegram_owner_private",
        ["loopback_model", "telegram_bot_api"],
    ),
    "airgapped_local": (
        "single_owner",
        "local_evidence_only",
        ["loopback_model"],
    ),
}


class UsageMeasurementError(RuntimeError):
    """A routine effect may have happened but its resource usage is not exact."""


def validate_enterprise_policy_claim(
    policy: dict[str, Any], manifest: dict[str, Any]
) -> None:
    manifest_policy = manifest.get("enterprise_policy")
    if (
        not isinstance(manifest_policy, dict)
        or manifest_policy.get("schema") != "offdesk_shift_enterprise_policy.v1"
    ):
        raise RuntimeError("routine claim manifest has no enterprise policy authority")
    profile_key = policy.get("enterprise_policy_key")
    contract = ENTERPRISE_PROFILE_CONTRACTS.get(profile_key)
    if contract is None:
        raise RuntimeError("routine claim selected an unknown enterprise policy")
    operator_mode, notification_route, network_effects = contract
    expected = {
        "operator_mode": operator_mode,
        "notification_route": notification_route,
        "allowed_forager_network_effects": network_effects,
        "child_task_network_boundary": "host_policy_required",
        "forager_controls_child_task_network": False,
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        raise RuntimeError("routine claim enterprise policy is not canonical")
    if manifest_policy.get("profile_key") != profile_key or any(
        manifest_policy.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("routine claim enterprise policy changed its manifest authority")


def is_loopback_http_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def forager_bin() -> str:
    sys.path.insert(0, str(REPO / "scripts"))
    from forager_bin import resolve_forager_bin  # noqa: PLC0415

    return resolve_forager_bin()


def log(message: str) -> None:
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{timestamp}] {message}", flush=True)


def run_forager(
    profile: str,
    *args: str,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [forager_bin(), "-p", profile, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_json_result(result: subprocess.CompletedProcess[str], action: str) -> Any:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:500]
        raise RuntimeError(f"{action} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError(f"{action} returned invalid JSON") from error


def shift_status(profile: str) -> dict[str, Any]:
    result = run_forager(profile, "offdesk", "shift", "status", "--json")
    value = parse_json_result(result, "shift status")
    if not isinstance(value, dict) or value.get("schema") != "offdesk_shift_status_report.v1":
        raise RuntimeError("shift status returned an unexpected schema")
    return value


def resolve_shift_id(profile: str, requested: str | None) -> str | None:
    if requested:
        return requested
    active = shift_status(profile).get("active_shift_id")
    return str(active) if active else None


def claim_routine(profile: str, shift_id: str, task: str) -> dict[str, Any]:
    result = run_forager(
        profile,
        "offdesk",
        "shift",
        "routine-claim",
        shift_id,
        "--task",
        task,
        "--json",
    )
    value = parse_json_result(result, "routine claim")
    if not isinstance(value, dict) or value.get("schema") != "offdesk_shift_routine_claim_report.v1":
        raise RuntimeError("routine claim returned an unexpected schema")
    if value.get("requested_task") != task:
        raise RuntimeError("routine claim did not bind the requested timer task")
    record = value.get("record")
    manifest = record.get("manifest") if isinstance(record, dict) else None
    if not isinstance(manifest, dict) or manifest.get("shift_id") != shift_id:
        raise RuntimeError("routine claim did not bind the exact shift")
    if value.get("outcome") == "not_configured":
        if (
            value.get("policy") is not None
            or value.get("reservation") is not None
            or value.get("receipt") is not None
        ):
            raise RuntimeError("unconfigured routine claim carried execution authority")
        return value
    policy = value.get("policy")
    if not isinstance(policy, dict) or policy.get("schema") != "offdesk_shift_routine_execution_policy.v1":
        raise RuntimeError("routine claim has no typed execution policy")
    validate_enterprise_policy_claim(policy, manifest)
    handler = policy.get("handler")
    contract = ROUTINE_HANDLER_CONTRACTS.get(handler)
    if contract is None:
        raise RuntimeError("routine claim selected an unsupported handler")
    routine_kind, allowed_effect, disarm_after_terminal = contract
    if (
        policy.get("routine_kind") != routine_kind
        or policy.get("allowed_effect") != allowed_effect
        or policy.get("disarm_after_terminal") is not disarm_after_terminal
        or not isinstance(policy.get("does_not_authorize"), list)
        or not all(isinstance(value, str) for value in policy["does_not_authorize"])
        or set(policy["does_not_authorize"]) != ROUTINE_POLICY_DENIALS
        or len(policy["does_not_authorize"]) != len(ROUTINE_POLICY_DENIALS)
    ):
        raise RuntimeError("routine claim policy is not canonical")
    allowance = policy.get("resource_allowance")
    allowance_keys = (
        "process_starts",
        "external_calls",
        "disk_bytes_written",
        "llm_tokens",
        "cost_micro_usd",
        "cost_rate_micro_usd_per_million_tokens",
    )
    if (
        not isinstance(allowance, dict)
        or set(allowance) != set(allowance_keys)
        or any(
            type(allowance.get(key)) is not int or allowance[key] < 0
            for key in allowance_keys
        )
    ):
        raise RuntimeError("routine claim has an invalid resource allowance")
    routine = value.get("routine")
    if (
        not isinstance(routine, dict)
        or routine.get("routine_id") != policy.get("routine_id")
        or routine.get("routine_kind") != routine_kind
    ):
        raise RuntimeError("routine claim policy does not match its manifest binding")
    target_keys = (
        "project_key",
        "wiki_profile",
        "project_root_identity_sha256",
        "project_root_path",
    )
    if any(policy.get(key) != routine.get(key) for key in target_keys):
        raise RuntimeError("routine claim policy changed its manifest target")
    if handler == "wiki_candidate_prereview":
        if any(not isinstance(policy.get(key), str) or not policy[key] for key in target_keys):
            raise RuntimeError("Wiki routine claim has an incomplete manifest target")
    elif any(policy.get(key) is not None for key in target_keys):
        raise RuntimeError("global routine claim carried an unexpected project target")
    disposition = value.get("outcome")
    if disposition not in {
        "reserved",
        "recovery_required",
        "not_due",
        "already_completed",
        "held",
    }:
        raise RuntimeError("routine claim returned an unsupported disposition")
    reservation = value.get("reservation")
    receipt = value.get("receipt")
    if disposition in {"reserved", "recovery_required"}:
        if (
            not isinstance(reservation, dict)
            or reservation.get("shift_id") != shift_id
            or reservation.get("routine_id") != policy.get("routine_id")
            or reservation.get("routine_kind") != routine_kind
            or not isinstance(reservation.get("run_id"), str)
            or not reservation["run_id"]
            or receipt is not None
        ):
            raise RuntimeError("routine claim reservation does not match its policy")
    elif disposition == "already_completed":
        if (
            reservation is not None
            or not isinstance(receipt, dict)
            or receipt.get("shift_id") != shift_id
            or receipt.get("routine_id") != policy.get("routine_id")
            or receipt.get("routine_kind") != routine_kind
            or not isinstance(receipt.get("run_id"), str)
            or not receipt["run_id"]
        ):
            raise RuntimeError("completed routine claim receipt does not match its policy")
    elif reservation is not None or receipt is not None:
        raise RuntimeError("inactive routine claim carried execution evidence")
    return value


def complete_routine(
    profile: str,
    shift_id: str,
    run_id: str,
    outcome: str,
    summary: str,
    usage: dict[str, int | None],
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    command = [
        "offdesk",
        "shift",
        "routine-complete",
        shift_id,
        run_id,
        "--outcome",
        outcome,
        "--summary",
        summary[:1000],
        "--usage-complete",
        "--process-starts",
        str(usage["process_starts"]),
        "--external-calls",
        str(usage["external_calls"]),
        "--llm-tokens",
        str(usage["llm_tokens"]),
        "--cost-micro-usd",
        str(usage["cost_micro_usd"]),
        "--json",
    ]
    rate = usage.get("cost_rate_micro_usd_per_million_tokens")
    if rate is not None:
        command.extend(["--cost-rate-micro-usd-per-million-tokens", str(rate)])
    for evidence_path in evidence_paths or []:
        command.extend(["--evidence", evidence_path])
    result = run_forager(profile, *command)
    value = parse_json_result(result, "routine completion")
    if not isinstance(value, dict) or value.get("schema") != "offdesk_shift_routine_complete_report.v1":
        raise RuntimeError("routine completion returned an unexpected schema")
    return value


def zero_usage() -> dict[str, int | None]:
    return {
        "process_starts": 0,
        "external_calls": 0,
        "disk_bytes_written": 0,
        "llm_tokens": 0,
        "cost_micro_usd": 0,
        "cost_rate_micro_usd_per_million_tokens": None,
    }


def estimate_cost_micro_usd(tokens: int, rate: int) -> int:
    return (tokens * rate + 999_999) // 1_000_000


def evidence_bytes(paths: list[str]) -> int:
    return sum(pathlib.Path(path).stat().st_size for path in paths)


def ensure_usage_within_allowance(
    usage: dict[str, int | None],
    allowance: dict[str, int],
) -> None:
    for key in (
        "process_starts",
        "external_calls",
        "disk_bytes_written",
        "llm_tokens",
        "cost_micro_usd",
    ):
        value = usage.get(key)
        if type(value) is not int or value < 0 or value > allowance[key]:
            raise UsageMeasurementError(f"routine {key} usage exceeded its allowance")


def execute_tick(
    profile: str,
    shift_id: str,
    policy: dict[str, Any],
) -> tuple[str, str, list[str], dict[str, int | None]]:
    allowance = policy["resource_allowance"]
    child_limit = min(2, max(0, allowance["process_starts"] - 1))
    result = run_forager(
        profile,
        "offdesk",
        "tick",
        "--shift-id",
        shift_id,
        "--limit",
        str(child_limit),
        "--json",
        timeout=20 * 60,
    )
    if result.returncode != 0:
        usage = zero_usage()
        usage["process_starts"] = 1
        return "failed", "exact shift tick failed", [], usage
    try:
        report = json.loads(result.stdout)
    except ValueError:
        usage = zero_usage()
        usage["process_starts"] = 1
        return "failed", "exact shift tick returned invalid JSON", [], usage
    bounded = report.get("bounded_shift") if isinstance(report, dict) else None
    if not isinstance(bounded, dict) or bounded.get("shift_id") != shift_id:
        usage = zero_usage()
        usage["process_starts"] = 1 + int(report.get("launched", 0))
        ensure_usage_within_allowance(usage, allowance)
        return (
            "identity-drift",
            "tick did not confirm the exact selected shift",
            [],
            usage,
        )
    launched = int(report.get("launched", 0))
    usage = zero_usage()
    usage["process_starts"] = 1 + launched
    ensure_usage_within_allowance(usage, allowance)
    summary = (
        "tick launched={launched} completed={completed} failed={failed} held={held}"
    ).format(
        **{
            key: int(report.get(key, 0))
            for key in ("launched", "completed", "failed", "held")
        }
    )
    return "succeeded", summary, [], usage


def routine_output_dir(profile: str, run_id: str) -> pathlib.Path:
    digest = hashlib.sha256(f"{profile}\0{run_id}".encode("utf-8")).hexdigest()[:24]
    root = pathlib.Path(
        os.environ.get(
            "FORAGER_SHIFT_OUTPUT_DIR",
            pathlib.Path.home() / ".cache" / "forager" / "shift-routines",
        )
    )
    path = root / digest
    path.mkdir(parents=True, exist_ok=True)
    return path


def execute_distill(
    profile: str,
    policy: dict[str, Any],
    run_id: str,
) -> tuple[str, str, list[str], dict[str, int | None]]:
    allowance = policy["resource_allowance"]
    wiki_profile = str(policy.get("wiki_profile") or "").strip()
    project_key = str(policy.get("project_key") or "").strip()
    if not wiki_profile or not project_key:
        return (
            "identity-drift",
            "reviewed Wiki routine target is incomplete",
            [],
            zero_usage(),
        )
    if not os.environ.get("OFFDESK_LLM_BASE_URL") or not os.environ.get("OFFDESK_LLM_MODEL"):
        return "skipped", "Wiki prereview provider configuration is missing", [], zero_usage()
    if not is_loopback_http_url(os.environ["OFFDESK_LLM_BASE_URL"]):
        return (
            "identity-drift",
            "enterprise policy permits only a loopback Wiki model endpoint",
            [],
            zero_usage(),
        )
    cost_rate = allowance["cost_rate_micro_usd_per_million_tokens"]
    token_cap = allowance["llm_tokens"]
    if cost_rate > 0:
        affordable_tokens = allowance["cost_micro_usd"] * 1_000_000 // cost_rate
        token_cap = min(token_cap, affordable_tokens)
    if (
        allowance["process_starts"] < 2
        or allowance["external_calls"] < 1
        or allowance["disk_bytes_written"] < 1
        or token_cap < 512
    ):
        return (
            "budget-exhausted",
            "Wiki prereview has no complete resource allowance",
            [],
            zero_usage(),
        )
    output_dir = routine_output_dir(profile, run_id)
    report_path = output_dir / "wiki-prereview.json"
    packet_path = output_dir / "wiki-prereview.md"
    if report_path.exists() or packet_path.exists():
        raise UsageMeasurementError("Wiki prereview output already exists for this run")
    command = [
        sys.executable,
        str(REPO / "scripts" / "offdesk_wiki_prereview.py"),
        "--profile",
        wiki_profile,
        "--out",
        str(report_path),
        "--packet",
        str(packet_path),
        "--forager-bin",
        forager_bin(),
        "--num-ctx",
        str(min(token_cap, 16_384)),
        "--num-predict",
        str(min(2_048, max(1, token_cap // 4))),
        "--max-output-bytes",
        str(allowance["disk_bytes_written"]),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=3 * 3600)
    evidence = [str(path) for path in (report_path, packet_path) if path.is_file()]
    if not report_path.is_file():
        raise UsageMeasurementError("Wiki prereview did not emit exact provider usage")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        llm_usage = report["llm_usage"]
        calls = int(llm_usage["calls"])
        tokens = int(llm_usage["prompt_tokens"]) + int(llm_usage["output_tokens"])
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
        raise UsageMeasurementError("Wiki prereview usage report is invalid") from error
    usage = zero_usage()
    usage.update(
        {
            "process_starts": 2,
            "external_calls": calls,
            "disk_bytes_written": evidence_bytes(evidence),
            "llm_tokens": tokens,
            "cost_micro_usd": estimate_cost_micro_usd(tokens, cost_rate),
            "cost_rate_micro_usd_per_million_tokens": cost_rate,
        }
    )
    ensure_usage_within_allowance(usage, allowance)
    if result.returncode != 0:
        return "failed", f"Wiki prereview failed for {project_key}", evidence, usage
    if not evidence:
        return "skipped", f"Wiki prereview produced no artifact for {project_key}", [], usage
    return (
        "succeeded",
        f"candidate-only Wiki prereview completed for {project_key}",
        evidence,
        usage,
    )


def shift_review_projection(profile: str, shift_id: str, run_id: str) -> dict[str, Any]:
    result = run_forager(
        profile,
        "offdesk",
        "shift",
        "review",
        shift_id,
        "--current-run-id",
        run_id,
        "--json",
    )
    value = parse_json_result(result, "shift morning review")
    if not isinstance(value, dict):
        raise RuntimeError("shift morning review returned an unexpected payload")
    if value.get("schema") != "offdesk_shift_review_projection.v1":
        raise RuntimeError("shift morning review returned an unexpected schema")
    if value.get("shift_id") != shift_id or value.get("current_routine_run_id") != run_id:
        raise RuntimeError("shift morning review did not bind the exact shift and routine run")
    authority = value.get("authority")
    if not isinstance(authority, dict) or authority.get("review_only") is not True:
        raise RuntimeError("shift morning review did not preserve its review-only authority")
    return value


def render_shift_review_markdown(projection: dict[str, Any]) -> str:
    summary = projection.get("summary") if isinstance(projection.get("summary"), dict) else {}
    lines = [
        "# Bounded Offdesk Shift Morning Review",
        "",
        f"- Shift: `{projection.get('shift_id', 'unknown')}`",
        f"- Review status: `{projection.get('review_status', 'unknown')}`",
        f"- Lifecycle: `{projection.get('lifecycle_status', 'unknown')}`",
        (
            "- Task attempts: {reserved} reserved, {ticketed} ticketed, "
            "{terminal} terminal, {completed} completed"
        ).format(
            reserved=int(summary.get("task_attempts_reserved") or 0),
            ticketed=int(summary.get("task_attempts_ticketed") or 0),
            terminal=int(summary.get("task_attempts_terminal") or 0),
            completed=int(summary.get("task_attempts_completed") or 0),
        ),
        (
            "- Routine runs: {reserved} reserved, {terminal} terminal, "
            "{succeeded} succeeded"
        ).format(
            reserved=int(summary.get("routine_runs_reserved") or 0),
            terminal=int(summary.get("routine_runs_terminal") or 0),
            succeeded=int(summary.get("routine_runs_succeeded") or 0),
        ),
        "",
        "## Task evidence",
        "",
    ]
    task_line_count = 0
    for task in projection.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        attempts = task.get("attempts") if isinstance(task.get("attempts"), list) else []
        if not attempts:
            lines.append(
                f"- `{task.get('task_id', 'unknown')}`: no reserved attempt; "
                f"task status `{task.get('current_status', 'missing')}`"
            )
            task_line_count += 1
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            lines.append(
                "- `{task}` attempt {attempt_number}: `{status}`; "
                "ticket={ticket}, probe={probe}, result-reported={result}".format(
                    task=task.get("task_id", "unknown"),
                    attempt_number=int(attempt.get("attempt_number") or 0),
                    status=attempt.get("validation_status", "unknown"),
                    ticket="yes" if attempt.get("ticket_receipt_present") else "no",
                    probe=attempt.get("live_probe_identity", "absent"),
                    result="yes" if attempt.get("result_artifact_reported_present") else "no",
                )
            )
            task_line_count += 1
    if task_line_count == 0:
        lines.append("- No bound task evidence.")

    lines.extend(["", "## Bounded evidence drill-down", ""])
    drilldown = (
        projection.get("evidence_drilldown")
        if isinstance(projection.get("evidence_drilldown"), dict)
        else {}
    )
    emitted = int(drilldown.get("emitted_count") or 0)
    total = int(drilldown.get("total_available") or 0)
    omitted = int(drilldown.get("omitted_count") or 0)
    lines.append(f"- Showing {emitted} of {total} item(s); omitted={omitted}.")
    evidence_items = drilldown.get("items") if isinstance(drilldown.get("items"), list) else []
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        reference = item.get("reference") or item.get("label") or "reference unavailable"
        suffix = " (text truncated)" if item.get("text_truncated") else ""
        lines.append(
            "- [{source}] `{subject}`: `{reference}`; verification=`{verification}`{suffix}".format(
                source=item.get("source", "unknown"),
                subject=item.get("subject_id", "unknown"),
                reference=reference,
                verification=item.get("verification", "unknown"),
                suffix=suffix,
            )
        )
    if not evidence_items:
        lines.append("- No itemized evidence is available in the shared projection.")
    if drilldown.get("truncated"):
        lines.append("- Drill-down is bounded; omissions and truncated references are declared above.")
    for coverage in drilldown.get("coverage") or []:
        if isinstance(coverage, dict) and coverage.get("detail_status") == "summary_only":
            lines.append(
                f"- `{coverage.get('source', 'unknown')}` remains summary-only: "
                f"{coverage.get('note', 'no detail note')}"
            )

    lines.extend(["", "## Council and closeout", ""])
    council = projection.get("council") if isinstance(projection.get("council"), dict) else {}
    lines.append(f"- Open Council decisions: {int(council.get('open_count') or 0)}")
    for decision in council.get("recent") or []:
        if isinstance(decision, dict):
            lines.append(
                "- `{decision}`: `{recommendation}` ({status})".format(
                    decision=decision.get("decision_id", "unknown"),
                    recommendation=decision.get("recommendation", "not_recorded"),
                    status=decision.get("status", "unknown"),
                )
            )
    closeout = projection.get("closeout") if isinstance(projection.get("closeout"), dict) else {}
    lines.append(f"- Bound tasks requiring closeout review: {int(closeout.get('required_count') or 0)}")

    lines.extend(["", "## Wiki candidate queues", ""])
    wiki = projection.get("adaptive_wiki") if isinstance(projection.get("adaptive_wiki"), dict) else {}
    targets = wiki.get("targets") if isinstance(wiki.get("targets"), list) else []
    for target in targets:
        if isinstance(target, dict):
            lines.append(
                "- `{project}` via `{profile}`: {candidates} candidate(s), "
                "{due} review-due entry(s)".format(
                    project=target.get("project_key", "unknown"),
                    profile=target.get("wiki_profile", "unknown"),
                    candidates=int(target.get("candidate_count") or 0),
                    due=int(target.get("review_due_count") or 0),
                )
            )
    if not targets:
        lines.append("- No manifest-bound Wiki target.")

    attention = projection.get("attention_reasons") or []
    lines.extend(["", "## Review boundary", ""])
    if attention:
        lines.extend(f"- Attention: `{reason}`" for reason in attention)
    else:
        lines.append("- No projection-level attention reason.")
    lines.extend(
        [
            "- This projection is review-only.",
            "- It does not authorize task dispatch, accepted truth, plan revision, or Wiki promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def write_local_brief(
    profile: str,
    shift_id: str,
    run_id: str,
    max_output_bytes: int,
) -> tuple[list[pathlib.Path], str]:
    projection = shift_review_projection(profile, shift_id, run_id)
    output_dir = routine_output_dir(profile, run_id)
    json_path = output_dir / "morning-review.json"
    markdown_path = output_dir / "morning-review.md"
    if json_path.exists() or markdown_path.exists():
        raise UsageMeasurementError("morning review output already exists for this run")
    json_text = json.dumps(projection, indent=2) + "\n"
    markdown_text = render_shift_review_markdown(projection)
    output_bytes = len(json_text.encode("utf-8")) + len(markdown_text.encode("utf-8"))
    if output_bytes > max_output_bytes:
        raise RuntimeError("morning review evidence exceeds its resource allowance")
    json_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    wiki = projection.get("adaptive_wiki") if isinstance(projection.get("adaptive_wiki"), dict) else {}
    candidate_summary = f"{int(wiki.get('candidate_count') or 0)} candidate(s)"
    return [json_path, markdown_path], candidate_summary


def send_morning_attention(
    profile: str,
    candidate_summary: str,
    shift_id: str,
    run_id: str,
    expected_operator_mode: str,
) -> tuple[bool, bool]:
    sys.path.insert(0, str(REPO / "scripts"))
    from telegram_operator.common import RemoteOperatorTelegramError  # noqa: PLC0415
    from telegram_operator.config import (  # noqa: PLC0415
        default_telegram_env_file,
        resolve_telegram_config,
    )
    from telegram_operator.transport import send_message  # noqa: PLC0415

    try:
        config = resolve_telegram_config(default_telegram_env_file(), required=True)
    except RemoteOperatorTelegramError:
        return False, False
    if config.get("operator_mode") != expected_operator_mode:
        return False, False
    message = (
        "Forager morning review ready\n"
        f"Shift: {html.escape(shift_id)}\n"
        f"Wiki queues: {html.escape(candidate_summary)}\n"
        "The local review artifacts are read-only and require on-desk review."
    )
    args = argparse.Namespace(dry_run=False, api_timeout_sec=30)
    try:
        send_message(config, str(config["target_chat_id"]), message, args)
    except RemoteOperatorTelegramError:
        return False, True
    log(f"morning attention sent; Wiki queues: {candidate_summary}")
    return True, True


def execute_brief(
    profile: str,
    shift_id: str,
    run_id: str,
    policy: dict[str, Any],
) -> tuple[str, str, list[str], dict[str, int | None]]:
    allowance = policy["resource_allowance"]
    notification_route = policy["notification_route"]
    needs_telegram = notification_route == "telegram_owner_private"
    if (
        allowance["process_starts"] < 1
        or allowance["external_calls"] < int(needs_telegram)
        or allowance["disk_bytes_written"] < 1
    ):
        return (
            "budget-exhausted",
            "morning review has no complete resource allowance",
            [],
            zero_usage(),
        )
    try:
        brief_paths, candidate_summary = write_local_brief(
            profile,
            shift_id,
            run_id,
            allowance["disk_bytes_written"],
        )
    except UsageMeasurementError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise UsageMeasurementError("morning review usage could not be measured") from error
    except (RuntimeError, ValueError) as error:
        usage = zero_usage()
        usage["process_starts"] = 1
        return "failed", f"local morning review failed: {type(error).__name__}", [], usage
    evidence = [str(path) for path in brief_paths]
    if notification_route == "local_evidence_only":
        usage = zero_usage()
        usage.update(
            {
                "process_starts": 1,
                "external_calls": 0,
                "disk_bytes_written": evidence_bytes(evidence),
            }
        )
        ensure_usage_within_allowance(usage, allowance)
        return (
            "succeeded",
            "shared morning review written for local on-desk review",
            evidence,
            usage,
        )
    delivered, external_attempted = send_morning_attention(
        profile,
        candidate_summary,
        shift_id,
        run_id,
        str(policy["operator_mode"]),
    )
    usage = zero_usage()
    usage.update(
        {
            "process_starts": 1,
            "external_calls": int(external_attempted),
            "disk_bytes_written": evidence_bytes(evidence),
        }
    )
    ensure_usage_within_allowance(usage, allowance)
    if delivered:
        return (
            "succeeded",
            "shared morning review written and attention delivered",
            evidence,
            usage,
        )
    return (
        "failed",
        "shared morning review written but Telegram delivery failed",
        evidence,
        usage,
    )


def execute_reserved(
    profile: str,
    shift_id: str,
    policy: dict[str, Any],
    run_id: str,
) -> tuple[str, str, list[str], dict[str, int | None]]:
    handler = policy.get("handler")
    if handler == "dispatch_tick":
        return execute_tick(profile, shift_id, policy)
    if handler == "wiki_candidate_prereview":
        return execute_distill(profile, policy, run_id)
    if handler == "morning_review_delivery":
        return execute_brief(profile, shift_id, run_id, policy)
    raise RuntimeError("Rust policy returned an unsupported routine handler")


def run_due_task(profile: str, shift_id: str, task: str) -> int:
    claim = claim_routine(profile, shift_id, task)
    disposition = str(claim.get("outcome") or "")
    if disposition == "not_configured":
        log(f"shift {shift_id} has no reviewed routine for timer task {task}")
        return 0
    policy = claim.get("policy") or {}
    routine_id = str(policy.get("routine_id") or "")
    close_after_terminal = bool(policy.get("disarm_after_terminal"))
    if disposition in {"not_due", "already_completed", "held"}:
        log(f"routine {routine_id}: {disposition}")
        exit_code = 0
        close_after_terminal = close_after_terminal and disposition == "already_completed"
    elif disposition != "reserved":
        log(f"routine {routine_id}: {disposition}; operator recovery required")
        return 2
    else:
        reservation = claim.get("reservation") or {}
        run_id = str(reservation.get("run_id") or "")
        if not run_id:
            raise RuntimeError("routine reservation has no run ID")
        try:
            outcome, summary, evidence, usage = execute_reserved(
                profile,
                shift_id,
                policy,
                run_id,
            )
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            log(f"routine {routine_id}: {error}; operator recovery required")
            return 2
        complete_routine(profile, shift_id, run_id, outcome, summary, usage, evidence)
        log(f"routine {routine_id}: {outcome}; {summary}")
        exit_code = 0 if outcome == "succeeded" else 1

    if close_after_terminal:
        result = run_forager(
            profile,
            "offdesk",
            "shift",
            "disarm",
            shift_id,
            "--reason",
            "morning review routine finished; await on-desk review",
            "--json",
        )
        if result.returncode != 0:
            log("morning review could not make the shift inert")
            exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    parser.add_argument("--shift-id", help="Exact shift ID. Safely discovered once when omitted.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", choices=ROUTINE_TASKS)
    group.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(shift_status(args.profile), indent=1))
        return 0

    shift_id = resolve_shift_id(args.profile, args.shift_id)
    if not shift_id:
        return 0
    try:
        return run_due_task(args.profile, shift_id, args.task)
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        log(f"typed shift adapter failed: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
