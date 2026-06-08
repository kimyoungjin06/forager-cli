#!/usr/bin/env python3
"""Telegram adapter for read-only Forager Remote Operator projections.

This adapter is intentionally narrow. It maps a small Telegram command surface
to `forager offdesk remote-operator ... --json` projections. It never executes
arbitrary shell text and never resolves approvals, launches work, enqueues
tasks, dispatches runtimes, or mutates project files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_TELEGRAM_ENV_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_TELEGRAM_ENV",
        "/home/kimyoungjin06/Desktop/Workspace/aoe_orch_control/.aoe-team/telegram.env",
    )
)
DEFAULT_STATE_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_STATE",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_state.json"),
    )
)
DEFAULT_FEEDBACK_FILE = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_FEEDBACK",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_feedback.jsonl"),
    )
)
DEFAULT_FEEDBACK_INGEST_DIR = pathlib.Path(
    os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_FEEDBACK_INGEST_DIR",
        str(pathlib.Path.home() / ".cache" / "forager" / "remote_operator_telegram_feedback_ingest"),
    )
)

RESULT_SCHEMA = "remote_operator_telegram_adapter_result.v1"
MOBILE_CARD_CONTRACT_SCHEMA = "telegram_mobile_card_contract.v1"
CHOICE_SURFACE_CONTRACT_SCHEMA = "telegram_choice_surface_contract.v1"
INTERACTION_CONTEXT_SCHEMA = "telegram_interaction_context.v1"
MOBILE_CARD_MAX_LINES = 8
MOBILE_CARD_MAX_CHARS = 650
MOBILE_CARD_FORBIDDEN_TERMS = (
    "Forager Remote Status",
    "Read-only",
    "검증:",
    "sha256:",
    "dispatch",
    "shell",
    "launch-prep",
    "runtime_handle_alive",
)
BUTTON_COMMAND_ALIASES = {
    "상태": "/status",
    "승인 대기": "/pending",
    "전체 승인": "/pending --all",
    "계획": "/plans --latest",
    "도움말": "/help",
}
CORE_BUTTON_LABELS = ("상태", "승인 대기", "계획", "도움말")
ALLOWED_COMMANDS = ("status", "pending", "plans", "show", "help", "feedback")
FORBIDDEN_REMOTE_INTENTS = (
    "approve_plan",
    "approve_launch",
    "deny_launch",
    "enqueue",
    "launch",
    "dispatch",
    "shell",
    "git_push",
    "delete",
    "provider_retarget",
)


class RemoteOperatorTelegramError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("FORAGER_PROFILE", "default"))
    parser.add_argument("--forager-bin", default=os.environ.get("FORAGER_BIN", "forager"))
    parser.add_argument("--env-file", type=pathlib.Path, default=DEFAULT_TELEGRAM_ENV_FILE)
    parser.add_argument("--state-file", type=pathlib.Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--feedback-file", type=pathlib.Path, default=DEFAULT_FEEDBACK_FILE)
    parser.add_argument("--feedback-ingest-dir", type=pathlib.Path, default=DEFAULT_FEEDBACK_INGEST_DIR)
    parser.add_argument(
        "--no-decision-feedback-ingest",
        dest="decision_feedback_ingest",
        action="store_false",
        default=True,
        help="Record freeform Telegram feedback JSONL only; do not promote it to offdesk decisions.",
    )
    parser.add_argument("--out", type=pathlib.Path, help="Optional JSON result path.")
    parser.add_argument("--command-text", help="Deterministic command text, for tests or manual dry-runs.")
    parser.add_argument("--send-command-text", help="Render a read-only command and send it to the configured target chat.")
    parser.add_argument("--replay-update-file", type=pathlib.Path, help="Dry-run only: process local Telegram update JSON through --once.")
    parser.add_argument("--projection-file", type=pathlib.Path, help="Dry-run only: render this read-only projection instead of invoking forager.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call the Telegram API.")
    parser.add_argument("--once", action="store_true", help="Poll Telegram once and answer at most one update.")
    parser.add_argument("--poll-timeout-sec", type=int, default=5)
    parser.add_argument("--api-timeout-sec", type=int, default=20)
    parser.add_argument("--max-message-chars", type=int, default=3500)
    return parser.parse_args()


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_jsonl(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_file(path: pathlib.Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise RemoteOperatorTelegramError(f"telegram env file not found: {path}")
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def sha256_short(value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def resolve_telegram_config(env_file: pathlib.Path, *, required: bool) -> dict[str, Any]:
    env = parse_env_file(env_file, required=required)
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner_chat_id = env.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    allowed_chat_ids = set(csv_values(env.get("TELEGRAM_ALLOW_CHAT_IDS", "")))
    allowed_chat_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")))
    if owner_chat_id:
        allowed_chat_ids.add(owner_chat_id)
    owner_user_id = env.get("TELEGRAM_OWNER_USER_ID", "").strip()
    allowed_user_ids = set(csv_values(env.get("TELEGRAM_ALLOW_USER_IDS", "")))
    allowed_user_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_USER_IDS", "")))
    if owner_user_id:
        allowed_user_ids.add(owner_user_id)
    target_chat_id = owner_chat_id or next(iter(sorted(allowed_chat_ids)), "")
    if required and not token:
        raise RemoteOperatorTelegramError("TELEGRAM_BOT_TOKEN is missing")
    if required and not allowed_chat_ids:
        raise RemoteOperatorTelegramError(
            "TELEGRAM_OWNER_CHAT_ID or TELEGRAM_ALLOW_CHAT_IDS is required"
        )
    return {
        "token": token,
        "target_chat_id": target_chat_id,
        "target_chat_id_hash": sha256_short(target_chat_id) if target_chat_id else None,
        "allowed_chat_ids": allowed_chat_ids,
        "allowed_user_ids": allowed_user_ids,
        "chat_allowlist_configured": bool(allowed_chat_ids),
        "user_allowlist_configured": bool(allowed_user_ids),
        "env_file": str(env_file),
    }


def normalize_command_name(raw: str) -> str:
    text = raw.strip()
    if text.startswith("/"):
        text = text[1:]
    if "@" in text:
        text = text.split("@", 1)[0]
    return text.strip().lower().replace("-", "_")


def parse_remote_command(command_text: str) -> dict[str, Any]:
    text = str(command_text or "").strip()
    if not text:
        return unsupported_command(text, "empty_command")
    original_text = text
    alias = BUTTON_COMMAND_ALIASES.get(text)
    if alias:
        text = alias
    if not text.startswith("/"):
        return {
            "supported": True,
            "command": "feedback",
            "argv": [],
            "reason": "freeform_feedback",
            "command_text": original_text,
            "feedback_text": original_text,
        }
    try:
        tokens = shlex.split(text)
    except ValueError as error:
        return unsupported_command(original_text, f"parse_error:{error}")
    if not tokens:
        return unsupported_command(original_text, "empty_command")

    command = normalize_command_name(tokens[0])
    args = tokens[1:]
    if command in {"start", "help"}:
        return {"supported": True, "command": "help", "argv": [], "reason": "help"}
    if command == "status":
        if args:
            return unsupported_command(original_text, "status_accepts_no_arguments")
        return {"supported": True, "command": "status", "argv": ["status"]}
    if command == "pending":
        argv = ["pending"]
        for arg in args:
            if arg == "--all":
                argv.append("--all")
            else:
                return unsupported_command(original_text, f"unsupported_pending_argument:{arg}")
        return {"supported": True, "command": "pending", "argv": argv}
    if command == "plans":
        return parse_plans_command(original_text, args)
    if command == "show":
        return parse_show_command(original_text, args)
    return unsupported_command(original_text, "unsupported_remote_operator_command")


def parse_plans_command(command_text: str, args: list[str]) -> dict[str, Any]:
    argv = ["plans"]
    index = 0
    value_flags = {"--project-key", "--task-id", "--profile-key", "--artifact-kind"}
    while index < len(args):
        arg = args[index]
        if arg == "--latest":
            argv.append(arg)
            index += 1
            continue
        if arg in value_flags:
            if index + 1 >= len(args):
                return unsupported_command(command_text, f"missing_value:{arg}")
            value = args[index + 1].strip()
            if not value:
                return unsupported_command(command_text, f"empty_value:{arg}")
            argv.extend([arg, value])
            index += 2
            continue
        return unsupported_command(command_text, f"unsupported_plans_argument:{arg}")
    return {"supported": True, "command": "plans", "argv": argv}


def parse_show_command(command_text: str, args: list[str]) -> dict[str, Any]:
    if len(args) != 1 or not args[0].strip():
        return unsupported_command(command_text, "show_requires_one_plan_ref")
    return {"supported": True, "command": "show", "argv": ["show", args[0].strip()]}


def unsupported_command(command_text: str, reason: str) -> dict[str, Any]:
    return {
        "supported": False,
        "command": None,
        "argv": [],
        "reason": reason,
        "command_text": command_text,
    }


def projection_command(forager_bin: str, profile: str, parsed: dict[str, Any]) -> list[str]:
    argv = [forager_bin]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(["offdesk", "remote-operator"])
    argv.extend(parsed["argv"])
    argv.extend(["--transport", "telegram", "--json"])
    return argv


def run_projection(forager_bin: str, profile: str, parsed: dict[str, Any]) -> dict[str, Any]:
    command = projection_command(forager_bin, profile, parsed)
    process = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        detail = sanitize_text(process.stderr.strip() or process.stdout.strip())
        raise RemoteOperatorTelegramError(
            f"forager remote operator projection failed: {detail}"
        )
    try:
        projection = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError("forager projection did not return JSON") from error
    validate_projection(projection, expected_command=parsed.get("command"))
    return projection


def decision_feedback_ingest_command(
    args: argparse.Namespace,
    feedback_path: pathlib.Path,
) -> list[str]:
    argv = [args.forager_bin]
    if args.profile:
        argv.extend(["--profile", args.profile])
    argv.extend(
        [
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            str(feedback_path),
            "--json",
        ]
    )
    return argv


def ingest_feedback_decision(
    args: argparse.Namespace,
    feedback_record: dict[str, Any],
) -> dict[str, Any]:
    if not args.decision_feedback_ingest:
        return {"decision_feedback_ingest_status": "disabled"}
    fingerprint = hashlib.sha256(
        json.dumps(feedback_record, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    message_id = feedback_record.get("message_id")
    suffix = str(message_id) if message_id is not None else fingerprint
    feedback_path = args.feedback_ingest_dir / f"telegram_feedback_{suffix}_{fingerprint}.json"
    write_json(feedback_path, feedback_record)
    command = decision_feedback_ingest_command(args, feedback_path)
    try:
        process = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(str(error), max_chars=300),
        }
    if process.returncode != 0:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": sanitize_text(
                process.stderr.strip() or process.stdout.strip(),
                max_chars=300,
            ),
        }
    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError:
        return {
            "decision_feedback_ingest_status": "error",
            "decision_feedback_ingest_file": str(feedback_path),
            "decision_feedback_ingest_error": "decision ingest did not return JSON",
        }
    return {
        "decision_feedback_ingest_status": "recorded"
        if report.get("appended") is True
        else "existing",
        "decision_feedback_ingest_file": str(feedback_path),
        "decision_feedback_decision_id": report.get("decision_id"),
        "decision_feedback_appended": bool(report.get("appended")),
    }


def load_projection_file(path: pathlib.Path, parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        projection = load_json(path)
    except OSError as error:
        raise RemoteOperatorTelegramError(f"projection file cannot be read: {path}") from error
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError(f"projection file is not valid JSON: {path}") from error
    if not isinstance(projection, dict):
        raise RemoteOperatorTelegramError("projection file must contain one JSON object")
    validate_projection(projection, expected_command=parsed.get("command"))
    return projection


def validate_projection(projection: dict[str, Any], *, expected_command: Any = None) -> None:
    if projection.get("schema") != "remote_operator_readonly_projection.v1":
        raise RemoteOperatorTelegramError("unexpected projection schema")
    if projection.get("read_only") is not True:
        raise RemoteOperatorTelegramError("projection is not read-only")
    if projection.get("mutation_authorized") is not False:
        raise RemoteOperatorTelegramError("projection unexpectedly authorizes mutation")
    if projection.get("approval_authorized") is not False:
        raise RemoteOperatorTelegramError("projection unexpectedly authorizes approval")
    expected = str(expected_command or "").strip()
    actual = str(projection.get("command") or "").strip()
    if expected and actual != expected:
        raise RemoteOperatorTelegramError(
            f"projection command mismatch: expected {expected}, got {actual or 'missing'}"
        )


def sanitize_text(text: str, *, max_chars: int = 1200) -> str:
    safe = str(text or "")
    safe = re.sub(r"bot[0-9]+:[A-Za-z0-9_-]+", "bot<redacted>", safe)
    safe = re.sub(r"(?i)(telegram_bot_token|bot_token|token)=\S+", r"\1=<redacted>", safe)
    safe = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "sk-<redacted>", safe)
    if len(safe) > max_chars:
        safe = safe[:max_chars] + "...<truncated>"
    return safe


def kst_time_label(value: Any) -> str:
    text = str(value or "").strip()
    try:
        timestamp = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        timestamp = dt.datetime.now(dt.timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=dt.timezone.utc)
    kst = dt.timezone(dt.timedelta(hours=9), name="KST")
    return timestamp.astimezone(kst).strftime("%H:%M KST")


def profile_label_from_projection(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    value = payload.get("profile") or projection.get("forager_profile") or "default"
    return sanitize_text(str(value), max_chars=80)


def title_with_profile(title: str, profile: Any) -> str:
    return f"<b>{html.escape(str(title))} / {html.escape(str(profile or 'default'))}</b>"


def basis_line(generated_at: Any) -> str:
    return f"기준: {kst_time_label(generated_at)}"


def render_projection_message(projection: dict[str, Any], *, max_chars: int) -> str:
    command = str(projection.get("command") or "").strip()
    if command == "status":
        message = render_status_message(projection)
    elif command == "pending":
        message = render_pending_message(projection)
    elif command == "plans":
        message = render_plans_message(projection)
    elif command == "show":
        message = render_show_message(projection)
    else:
        message = render_generic_projection_message(projection)
    if len(message) > max_chars:
        return message[: max(0, max_chars - 20)] + "\n...<truncated>"
    return message


def render_status_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    summary = status_summary(payload)
    return "\n".join(
        [
            title_with_profile("Forager 점검", profile),
            status_headline(payload),
            basis_line(projection.get("generated_at")),
            "",
            summary,
            status_next_action(payload),
        ]
    )


def render_pending_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    approvals = payload.get("approvals") if isinstance(payload.get("approvals"), list) else []
    lines = [
        title_with_profile("승인 대기", profile),
    ]
    if approvals:
        expired_count = sum(1 for item in approvals if isinstance(item, dict) and item.get("expired"))
        expired_suffix = f" · 만료 {expired_count}" if expired_count else ""
        lines.append(
            f"상태: 승인 요청 {number(payload, 'approval_count')}개 확인 필요{expired_suffix}"
        )
    else:
        lines.append("상태: 승인할 항목 없음")
    lines.append(basis_line(projection.get("generated_at")))
    if approvals:
        lines.append("")
    for approval in approvals[:2]:
        if not isinstance(approval, dict):
            continue
        expired = " 만료" if approval.get("expired") else ""
        lines.append(
            "- "
            + html.escape(str(approval.get("approval_id") or "approval"))
            + f": {html.escape(display_action(approval.get('action')))}"
            + expired
        )
    if len(approvals) > 2:
        lines.append(f"- 외 {len(approvals) - 2}개")
    next_line = (
        "다음: 로컬에서 승인 판단"
        if approvals
        else "다음: 알림 대기"
    )
    lines.append(next_line)
    return "\n".join(lines)


def render_plans_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    plans = payload.get("plans") if isinstance(payload.get("plans"), list) else []
    lines = [
        title_with_profile("자율주행 계획", profile),
    ]
    if plans:
        lines.append(f"상태: 계획 {number(payload, 'plan_count')}개 확인 가능")
    else:
        lines.append("상태: 등록된 계획 없음")
    lines.append(basis_line(projection.get("generated_at")))
    if plans:
        lines.append("")
    for plan in plans[:2]:
        if not isinstance(plan, dict):
            continue
        lines.append(
            "- "
            + html.escape(str(plan.get("plan_id") or "plan"))
            + ": "
            + html.escape(display_review_status(plan.get("review_status")))
        )
    if len(plans) > 2:
        lines.append(f"- 외 {len(plans) - 2}개")
    next_line = (
        "다음: <code>/show PLAN_ID</code> 로 세부 확인"
        if plans
        else "다음: 계획 등록 후 확인"
    )
    lines.append(next_line)
    return "\n".join(lines)


def render_show_message(projection: dict[str, Any]) -> str:
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    reviews = payload.get("reviews") if isinstance(payload.get("reviews"), list) else []
    launch_preps = payload.get("launch_preps") if isinstance(payload.get("launch_preps"), list) else []
    lines = [
        title_with_profile("계획 상세", profile),
        f"상태: {html.escape(display_review_status(plan.get('review_status')))}",
        basis_line(projection.get("generated_at")),
        f"계획: {html.escape(str(plan.get('plan_id') or 'unknown'))}",
        f"리뷰: {html.escape(display_review_status(plan.get('review_status')))} / 실행 준비 {len(launch_preps)}개",
        f"다음: {html.escape(display_next_action(plan.get('next_safe_action')))}",
    ]
    if reviews:
        latest = reviews[-1] if isinstance(reviews[-1], dict) else {}
        lines.append(
            "최근 리뷰: "
            + html.escape(str(latest.get("decision") or "unknown"))
            + " by "
            + html.escape(str(latest.get("reviewer") or "operator"))
        )
    return "\n".join(lines)


def render_generic_projection_message(projection: dict[str, Any]) -> str:
    card = projection.get("card") if isinstance(projection.get("card"), dict) else {}
    title = html.escape(str(card.get("title") or "Forager"))
    summary_lines = safe_string_list(card.get("summary_lines"))
    lines = [
        f"<b>{title}</b>",
        "상태: " + html.escape(summary_lines[0] if summary_lines else "내용 확인"),
        basis_line(projection.get("generated_at")),
    ]
    for item in summary_lines[1:4]:
        lines.append(f"- {html.escape(item)}")
    lines.append("다음: 세부 내용은 로컬에서 확인")
    return "\n".join(lines)


def projection_payload(projection: dict[str, Any]) -> dict[str, Any]:
    payload = projection.get("payload")
    return payload if isinstance(payload, dict) else {}


def projection_card(projection: dict[str, Any]) -> dict[str, Any]:
    card = projection.get("card")
    return card if isinstance(card, dict) else {}


def number(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    return int(raw) if isinstance(raw, int) else 0


def status_headline(payload: dict[str, Any]) -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    active = number(payload, "active_offdesk_tasks")
    queued = number(payload, "queued_offdesk_tasks")
    if pending:
        return f"상태: 승인 요청 {pending}개 확인 필요"
    if failed:
        return f"상태: 실패한 자율주행 {failed}개"
    if closeout:
        return f"상태: 마무리 확인 {closeout}개 필요"
    if active:
        return f"상태: 자율주행 {active}개 진행 중"
    if queued:
        return f"상태: 자율주행 {queued}개 대기 중"
    return "상태: 정상, 처리할 항목 없음"


def status_summary(payload: dict[str, Any]) -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    active = number(payload, "active_offdesk_tasks")
    queued = number(payload, "queued_offdesk_tasks")
    parts: list[str] = []
    if pending:
        parts.append(f"승인 {pending}")
    if failed:
        parts.append(f"실패 {failed}")
    if closeout:
        parts.append(f"마무리 {closeout}")
    if active or queued:
        parts.append(f"자율주행 진행 {active} / 대기 {queued}")
    return " · ".join(parts) if parts else "작업 없음 · 승인 없음 · 실패 없음"


def status_next_action(payload: dict[str, Any]) -> str:
    pending = number(payload, "pending_approvals")
    failed = number(payload, "failed_offdesk_tasks")
    closeout = number(payload, "closeout_required_offdesk_tasks")
    if pending:
        return "다음: <code>/pending</code> 으로 승인 요청 확인"
    if failed or closeout:
        return "다음: 실패/마무리 항목 로컬 점검"
    return "다음: 알림 대기"


def display_action(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "approve_plan": "계획 승인",
        "approve_launch": "실행 승인",
        "deny_launch": "실행 거절",
        "provider_fallback": "모델 대체",
        "provider_retarget": "모델 변경",
    }
    return labels.get(text, text.replace("_", " ") or "확인 필요")


def display_review_status(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "accepted": "승인됨",
        "approved": "승인됨",
        "pending": "검토 대기",
        "missing": "검토 없음",
        "not_reviewed": "검토 없음",
        "revision_required": "수정 필요",
        "rejected": "거절됨",
        "review_unknown": "검토 상태 불명",
        "unknown": "상태 불명",
    }
    return labels.get(text, text.replace("_", " ") or "상태 불명")


def display_next_action(value: Any) -> str:
    text = str(value or "").strip()
    labels = {
        "inspect": "내용 확인",
        "review": "리뷰 필요",
        "approve": "승인 검토",
        "launch_prep": "실행 준비 확인",
        "launch": "실행 검토",
        "closeout": "마무리 확인",
    }
    return labels.get(text, text.replace("_", " ") or "내용 확인")


def short_hash(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "sha256:unknown"
    if text.startswith("sha256:") and len(text) > 22:
        return text[:22]
    return text


def safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [sanitize_text(str(item), max_chars=400) for item in value if str(item).strip()]


def show_command_for(plan_id: Any) -> str:
    value = str(plan_id or "").strip()
    return f"/show {shlex.quote(value)}" if value else "/plans --latest"


def interaction_context_from_projection(projection: dict[str, Any]) -> dict[str, Any]:
    command = str(projection.get("command") or "").strip()
    payload = projection_payload(projection)
    profile = profile_label_from_projection(projection)
    context: dict[str, Any] = {
        "schema": INTERACTION_CONTEXT_SCHEMA,
        "command": command or "unknown",
        "profile": profile,
        "projection_generated_at": str(projection.get("generated_at") or ""),
        "context_kind": "generic",
        "focus_kind": None,
        "focus_ref": None,
        "focus_label": None,
        "next_command": None,
    }
    if command == "status":
        pending = number(payload, "pending_approvals")
        failed = number(payload, "failed_offdesk_tasks")
        closeout = number(payload, "closeout_required_offdesk_tasks")
        active = number(payload, "active_offdesk_tasks")
        queued = number(payload, "queued_offdesk_tasks")
        if pending:
            context.update(
                {
                    "context_kind": "status_attention",
                    "focus_kind": "approval_queue",
                    "focus_ref": str(pending),
                    "focus_label": f"승인 요청 {pending}개",
                    "next_command": "/pending",
                }
            )
        elif failed or closeout:
            context.update(
                {
                    "context_kind": "status_attention",
                    "focus_kind": "local_review",
                    "focus_ref": f"failed:{failed};closeout:{closeout}",
                    "focus_label": status_summary(payload),
                    "next_command": "/status",
                }
            )
        elif active or queued:
            context.update(
                {
                    "context_kind": "status_activity",
                    "focus_kind": "offdesk_activity",
                    "focus_ref": f"active:{active};queued:{queued}",
                    "focus_label": status_summary(payload),
                    "next_command": "/status",
                }
            )
        else:
            context.update(
                {
                    "context_kind": "status_clear",
                    "focus_kind": "none",
                    "focus_label": "처리할 항목 없음",
                    "next_command": "/status",
                }
            )
    elif command == "pending":
        approvals = payload.get("approvals") if isinstance(payload.get("approvals"), list) else []
        if approvals and isinstance(approvals[0], dict):
            approval = approvals[0]
            context.update(
                {
                    "context_kind": "approval_attention",
                    "focus_kind": "approval",
                    "focus_ref": str(approval.get("approval_id") or "approval"),
                    "focus_label": display_action(approval.get("action")),
                    "next_command": "/pending --all" if len(approvals) > 1 else "/pending",
                }
            )
        else:
            context.update(
                {
                    "context_kind": "approval_clear",
                    "focus_kind": "none",
                    "focus_label": "승인할 항목 없음",
                    "next_command": "/pending",
                }
            )
    elif command == "plans":
        plans = payload.get("plans") if isinstance(payload.get("plans"), list) else []
        if plans and isinstance(plans[0], dict):
            plan = plans[0]
            plan_id = str(plan.get("plan_id") or "plan")
            context.update(
                {
                    "context_kind": "plan_attention",
                    "focus_kind": "plan",
                    "focus_ref": plan_id,
                    "focus_label": display_review_status(plan.get("review_status")),
                    "next_command": show_command_for(plan_id),
                }
            )
        else:
            context.update(
                {
                    "context_kind": "plan_clear",
                    "focus_kind": "none",
                    "focus_label": "등록된 계획 없음",
                    "next_command": "/plans --latest",
                }
            )
    elif command == "show":
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        plan_id = str(plan.get("plan_id") or "unknown")
        context.update(
            {
                "context_kind": "plan_detail",
                "focus_kind": "plan",
                "focus_ref": plan_id,
                "focus_label": display_review_status(plan.get("review_status")),
                "next_command": "/plans --latest",
            }
        )
    return context


def interaction_context_label(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return ""
    focus_kind = str(context.get("focus_kind") or "").strip()
    focus_ref = str(context.get("focus_ref") or "").strip()
    focus_label = str(context.get("focus_label") or "").strip()
    if focus_kind == "plan" and focus_ref:
        suffix = f" · {focus_label}" if focus_label else ""
        return f"계획 {focus_ref}{suffix}"
    if focus_kind == "approval" and focus_ref:
        suffix = f" · {focus_label}" if focus_label else ""
        return f"승인 {focus_ref}{suffix}"
    if focus_label:
        return focus_label
    command = str(context.get("command") or "").strip()
    return command


def mobile_card_contract(message: str) -> dict[str, Any]:
    lines = str(message or "").splitlines()
    warnings: list[str] = []
    if len(lines) > MOBILE_CARD_MAX_LINES:
        warnings.append("too_many_lines")
    if len(str(message or "")) > MOBILE_CARD_MAX_CHARS:
        warnings.append("too_many_chars")
    if not lines or not lines[0].strip().startswith("<b>"):
        warnings.append("missing_title")
    if not any(line.startswith("상태:") for line in lines):
        warnings.append("missing_status_headline")
    if "다음:" not in message:
        warnings.append("missing_next_action")
    leaked_terms = [term for term in MOBILE_CARD_FORBIDDEN_TERMS if term in message]
    if leaked_terms:
        warnings.append("forbidden_terms:" + ",".join(leaked_terms))
    return {
        "schema": MOBILE_CARD_CONTRACT_SCHEMA,
        "line_count": len(lines),
        "char_count": len(str(message or "")),
        "max_lines": MOBILE_CARD_MAX_LINES,
        "max_chars": MOBILE_CARD_MAX_CHARS,
        "has_title": bool(lines and lines[0].strip().startswith("<b>")),
        "has_status_headline": any(line.startswith("상태:") for line in lines),
        "has_next_action": "다음:" in message,
        "warnings": warnings,
    }


def choice_keyboard(context: dict[str, Any] | None = None) -> dict[str, Any]:
    rows: list[list[str]] = []
    seen: set[str] = set()

    def add_row(*labels: str) -> None:
        row: list[str] = []
        for label in labels:
            text = str(label or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            row.append(text)
        if row:
            rows.append(row)

    context_kind = str(context.get("context_kind") or "") if isinstance(context, dict) else ""
    next_command = str(context.get("next_command") or "").strip() if isinstance(context, dict) else ""
    if next_command and next_command not in {"/status", "/pending", "/plans --latest", "/help"}:
        add_row(next_command)
    if context_kind == "status_attention":
        add_row("승인 대기", "계획")
        add_row("상태", "도움말")
    elif context_kind == "approval_attention":
        add_row("전체 승인", "상태")
        add_row("승인 대기", "계획")
        add_row("도움말")
    elif context_kind == "plan_attention":
        add_row("계획", "상태")
        add_row("승인 대기", "도움말")
    elif context_kind == "plan_detail":
        add_row("계획", "상태")
        add_row("승인 대기", "도움말")
    else:
        add_row("상태", "승인 대기")
        add_row("계획", "도움말")
    for label in CORE_BUTTON_LABELS:
        if label not in seen:
            add_row(label)
    return {
        "keyboard": rows,
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "input_field_placeholder": "의견을 직접 입력할 수 있습니다",
    }


def button_resolves_to(button_text: str, command_text: str) -> bool:
    button = str(button_text or "").strip()
    command = str(command_text or "").strip()
    return bool(command) and (button == command or BUTTON_COMMAND_ALIASES.get(button) == command)


def choice_surface_contract(
    reply_markup: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    keyboard = reply_markup.get("keyboard") if isinstance(reply_markup, dict) else None
    button_texts: list[str] = []
    if isinstance(keyboard, list):
        for row in keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if isinstance(button, str):
                    button_texts.append(button)
                elif isinstance(button, dict):
                    button_texts.append(str(button.get("text") or ""))
    else:
        warnings.append("missing_keyboard")
    for label in CORE_BUTTON_LABELS:
        if label not in button_texts:
            warnings.append(f"missing_button:{label}")
    placeholder = ""
    if isinstance(reply_markup, dict):
        placeholder = str(reply_markup.get("input_field_placeholder") or "")
    if "의견" not in placeholder:
        warnings.append("missing_freeform_placeholder")
    next_command = str(context.get("next_command") or "").strip() if isinstance(context, dict) else ""
    has_contextual_choice = False
    if next_command:
        has_contextual_choice = any(button_resolves_to(button, next_command) for button in button_texts)
        if not has_contextual_choice:
            warnings.append(f"missing_contextual_choice:{next_command}")
    return {
        "schema": CHOICE_SURFACE_CONTRACT_SCHEMA,
        "button_texts": button_texts,
        "has_freeform_placeholder": "의견" in placeholder,
        "context_kind": context.get("context_kind") if isinstance(context, dict) else None,
        "context_command": next_command or None,
        "has_contextual_choice": has_contextual_choice,
        "warnings": warnings,
    }


def help_message(*, profile: Any, generated_at: Any) -> str:
    return "\n".join(
        [
            title_with_profile("Forager 원격 조작", profile),
            "상태: 버튼으로 조회하고, 애매하면 직접 입력합니다.",
            basis_line(generated_at),
            "",
            "직접 명령: /status, /pending, /plans, /show PLAN_ID",
            "다음: 필요한 버튼을 누르거나 의견을 입력하세요.",
        ]
    )


def render_feedback_message(
    *,
    profile: Any,
    generated_at: Any,
    feedback_text: str,
    feedback_context: dict[str, Any] | None = None,
    inbox_status: str | None = None,
) -> str:
    excerpt = sanitize_text(feedback_text, max_chars=120)
    if inbox_status in {"recorded", "existing"}:
        status_line = "상태: 입력 내용을 결정함에 등록했습니다."
    elif inbox_status == "error":
        status_line = "상태: 입력 저장됨 · 결정함 등록 실패"
    else:
        status_line = "상태: 입력 내용을 저장했습니다."
    lines = [
        title_with_profile("의견 접수", profile),
        status_line,
        basis_line(generated_at),
    ]
    context_label = interaction_context_label(feedback_context)
    if context_label:
        lines.append(f"맥락: {html.escape(context_label)}")
    lines.extend(
        [
            f"내용: {html.escape(excerpt)}",
            "다음: 로컬 검토 대기",
        ]
    )
    return "\n".join(lines)


def result_base(args: argparse.Namespace, config: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "generated_at": utc_now(),
        "mode": mode,
        "profile": args.profile,
        "target_chat_id_hash": config.get("target_chat_id_hash"),
        "chat_allowlist_configured": bool(config.get("chat_allowlist_configured")),
        "user_allowlist_configured": bool(config.get("user_allowlist_configured")),
        "read_only": True,
        "mutation_authorized": False,
        "approval_authorized": False,
        "forbidden_remote_intents": list(FORBIDDEN_REMOTE_INTENTS),
    }


def attach_choice_surface(result: dict[str, Any], context: dict[str, Any] | None) -> None:
    reply_markup = choice_keyboard(context)
    result["reply_markup_preview"] = reply_markup
    result["choice_surface_contract"] = choice_surface_contract(reply_markup, context)
    if isinstance(context, dict):
        result["interaction_context"] = context


def render_command_result(
    args: argparse.Namespace,
    config: dict[str, Any],
    command_text: str,
    *,
    mode: str,
    feedback_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = result_base(args, config, mode)
    result["command_text"] = sanitize_text(command_text, max_chars=400)
    parsed = parse_remote_command(command_text)
    result["parsed_command"] = parsed
    if not parsed.get("supported"):
        message_preview = help_message(profile=args.profile, generated_at=result["generated_at"])
        attach_choice_surface(result, None)
        result.update(
            {
                "status": "unsupported",
                "reason": parsed.get("reason"),
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "help":
        message_preview = help_message(profile=args.profile, generated_at=result["generated_at"])
        attach_choice_surface(result, None)
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if parsed.get("command") == "feedback":
        message_preview = render_feedback_message(
            profile=args.profile,
            generated_at=result["generated_at"],
            feedback_text=str(parsed.get("feedback_text") or command_text),
            feedback_context=feedback_context,
        )
        attach_choice_surface(result, feedback_context)
        if isinstance(feedback_context, dict):
            result["feedback_context"] = feedback_context
        result.update(
            {
                "status": "rendered",
                "projection": None,
                "message_preview": message_preview,
                "mobile_card_contract": mobile_card_contract(message_preview),
            }
        )
        return result
    if args.projection_file:
        projection = load_projection_file(args.projection_file, parsed)
    else:
        projection = run_projection(args.forager_bin, args.profile, parsed)
    message_preview = render_projection_message(
        projection,
        max_chars=max(200, int(args.max_message_chars)),
    )
    interaction_context = interaction_context_from_projection(projection)
    attach_choice_surface(result, interaction_context)
    result.update(
        {
            "status": "rendered",
            "projection_schema": projection.get("schema"),
            "projection": projection,
            "message_preview": message_preview,
            "mobile_card_contract": mobile_card_contract(message_preview),
        }
    )
    return result


def telegram_api(token: str, method: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace") if hasattr(error, "read") else str(error)
        raise RemoteOperatorTelegramError(f"Telegram API HTTP error ({method}): {detail}") from error
    except urllib.error.URLError as error:
        raise RemoteOperatorTelegramError(f"Telegram API URL error ({method}): {error}") from error
    except json.JSONDecodeError as error:
        raise RemoteOperatorTelegramError(f"Telegram API invalid JSON ({method})") from error
    if not data.get("ok"):
        raise RemoteOperatorTelegramError(f"Telegram API error ({method}): {data}")
    return data


def load_state(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    try:
        state = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    if not isinstance(state, dict):
        return {"schema": "remote_operator_telegram_state.v1", "offset": 0}
    state.setdefault("schema", "remote_operator_telegram_state.v1")
    state.setdefault("offset", 0)
    return state


def save_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json(path, state)


def last_context_for_chat_hash(state: dict[str, Any], chat_hash: Any) -> dict[str, Any] | None:
    contexts = state.get("last_interaction_context_by_chat")
    if not isinstance(contexts, dict):
        return None
    context = contexts.get(str(chat_hash or ""))
    return context if isinstance(context, dict) else None


def remember_context_for_chat_hash(
    state: dict[str, Any],
    chat_hash: Any,
    rendered: dict[str, Any],
) -> None:
    context = rendered.get("interaction_context")
    parsed = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
    if not isinstance(context, dict) or parsed.get("command") == "feedback":
        return
    contexts = state.setdefault("last_interaction_context_by_chat", {})
    if not isinstance(contexts, dict):
        contexts = {}
        state["last_interaction_context_by_chat"] = contexts
    remembered = dict(context)
    remembered["remembered_at"] = utc_now()
    if isinstance(rendered.get("sent_message_id"), int):
        remembered["source_message_id"] = rendered["sent_message_id"]
    contexts[str(chat_hash or "")] = remembered


def remember_context_for_message(
    state: dict[str, Any],
    message: dict[str, Any],
    rendered: dict[str, Any],
) -> None:
    remember_context_for_chat_hash(state, sha256_short(chat_id_for(message)), rendered)


def get_updates(config: dict[str, Any], offset: int, args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.replay_update_file:
        value = load_json(args.replay_update_file)
        if isinstance(value, dict) and isinstance(value.get("result"), list):
            raw_updates = value["result"]
        elif isinstance(value, list):
            raw_updates = value
        elif isinstance(value, dict):
            raw_updates = [value]
        else:
            raw_updates = []
        updates = [item for item in raw_updates if isinstance(item, dict)]
        return [
            item
            for item in updates
            if not isinstance(item.get("update_id"), int) or item["update_id"] >= int(offset)
        ]
    data = telegram_api(
        config["token"],
        "getUpdates",
        {
            "offset": int(offset),
            "timeout": max(0, int(args.poll_timeout_sec)),
            "allowed_updates": ["message"],
        },
        timeout_sec=max(int(args.api_timeout_sec), int(args.poll_timeout_sec) + 10),
    )
    updates = data.get("result", [])
    return [item for item in updates if isinstance(item, dict)] if isinstance(updates, list) else []


def send_message(
    config: dict[str, Any],
    chat_id: str,
    message: str,
    args: argparse.Namespace,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> int | None:
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if args.dry_run:
        return None
    data = telegram_api(
        config["token"],
        "sendMessage",
        payload,
        timeout_sec=max(1, int(args.api_timeout_sec)),
    )
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("message_id"), int):
        return int(result["message_id"])
    return None


def message_from_update(update: dict[str, Any]) -> dict[str, Any] | None:
    message = update.get("message")
    return message if isinstance(message, dict) else None


def update_text(message: dict[str, Any]) -> str:
    text = message.get("text")
    return str(text or "").strip()


def chat_id_for(message: dict[str, Any]) -> str:
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return ""
    value = chat.get("id")
    return str(value or "").strip()


def user_id_for(message: dict[str, Any]) -> str:
    user = message.get("from")
    if not isinstance(user, dict):
        return ""
    value = user.get("id")
    return str(value or "").strip()


def message_id_for(message: dict[str, Any]) -> int | None:
    value = message.get("message_id")
    return int(value) if isinstance(value, int) else None


def record_feedback(
    args: argparse.Namespace,
    config: dict[str, Any],
    message: dict[str, Any],
    text: str,
    *,
    feedback_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": utc_now(),
        "profile": args.profile,
        "chat_id_hash": sha256_short(chat_id_for(message)),
        "user_id_hash": sha256_short(user_id_for(message)),
        "message_id": message_id_for(message),
        "feedback_text": sanitize_text(text, max_chars=2000),
        "target_chat_id_hash": config.get("target_chat_id_hash"),
        "feedback_context": feedback_context,
    }
    append_jsonl(args.feedback_file, record)
    return {
        "feedback_recorded": True,
        "feedback_file": str(args.feedback_file),
        "feedback_text_chars": len(str(text or "")),
        "feedback_context": feedback_context,
        "feedback_record": record,
    }


def update_is_allowed(config: dict[str, Any], message: dict[str, Any]) -> tuple[bool, str]:
    chat_id = chat_id_for(message)
    user_id = user_id_for(message)
    allowed_chat_ids = config.get("allowed_chat_ids") or set()
    allowed_user_ids = config.get("allowed_user_ids") or set()
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return False, "chat_not_allowed"
    if allowed_user_ids and user_id not in allowed_user_ids:
        return False, "user_not_allowed"
    return True, "allowed"


def run_once(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    state = load_state(args.state_file)
    updates = get_updates(config, int(state.get("offset") or 0), args)
    result = result_base(args, config, "live_once")
    result.update({"status": "no_update", "updates_seen": len(updates)})
    max_update_id = int(state.get("offset") or 0) - 1
    for update in updates:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            max_update_id = max(max_update_id, update_id)
        message = message_from_update(update)
        if not message:
            continue
        allowed, reason = update_is_allowed(config, message)
        if not allowed:
            result.update(
                {
                    "status": "ignored",
                    "reason": reason,
                    "chat_id_hash": sha256_short(chat_id_for(message)),
                    "user_id_hash": sha256_short(user_id_for(message)),
                }
            )
            continue
        text = update_text(message)
        if not text:
            result.update({"status": "ignored", "reason": "empty_message"})
            continue
        feedback_context = last_context_for_chat_hash(state, sha256_short(chat_id_for(message)))
        rendered = render_command_result(
            args,
            config,
            text,
            mode="live_once",
            feedback_context=feedback_context,
        )
        parsed_command = rendered.get("parsed_command") if isinstance(rendered.get("parsed_command"), dict) else {}
        if parsed_command.get("command") == "feedback":
            feedback_result = record_feedback(
                args,
                config,
                message,
                text,
                feedback_context=feedback_context,
            )
            feedback_record = feedback_result.pop("feedback_record", None)
            rendered.update(feedback_result)
            if isinstance(feedback_record, dict):
                ingest_result = ingest_feedback_decision(args, feedback_record)
                rendered.update(ingest_result)
                rendered["message_preview"] = render_feedback_message(
                    profile=args.profile,
                    generated_at=rendered["generated_at"],
                    feedback_text=str(parsed_command.get("feedback_text") or text),
                    feedback_context=feedback_context,
                    inbox_status=str(ingest_result.get("decision_feedback_ingest_status") or ""),
                )
                rendered["mobile_card_contract"] = mobile_card_contract(rendered["message_preview"])
        message_id = send_message(
            config,
            chat_id_for(message),
            rendered["message_preview"],
            args,
            reply_markup=rendered.get("reply_markup_preview")
            if isinstance(rendered.get("reply_markup_preview"), dict)
            else None,
        )
        rendered["sent_message_id"] = message_id
        remember_context_for_message(state, message, rendered)
        result = rendered
        break
    if max_update_id >= int(state.get("offset") or 0):
        state["offset"] = max_update_id + 1
        save_state(args.state_file, state)
    return result


def send_command_text(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    target_chat_id = str(config.get("target_chat_id") or "").strip()
    if not target_chat_id:
        raise RemoteOperatorTelegramError("target chat id is missing")
    state = load_state(args.state_file)
    feedback_context = last_context_for_chat_hash(state, sha256_short(target_chat_id))
    rendered = render_command_result(
        args,
        config,
        args.send_command_text or "/status",
        mode="live_send",
        feedback_context=feedback_context,
    )
    if rendered.get("status") != "rendered":
        return rendered
    rendered["sent_message_id"] = send_message(
        config,
        target_chat_id,
        rendered["message_preview"],
        args,
        reply_markup=rendered.get("reply_markup_preview")
        if isinstance(rendered.get("reply_markup_preview"), dict)
        else None,
    )
    remember_context_for_chat_hash(state, sha256_short(target_chat_id), rendered)
    save_state(args.state_file, state)
    return rendered


def emit_result(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.out:
        write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()
    try:
        if args.projection_file and not args.dry_run:
            raise RemoteOperatorTelegramError("--projection-file is only allowed with --dry-run")
        if args.replay_update_file and not args.dry_run:
            raise RemoteOperatorTelegramError("--replay-update-file is only allowed with --dry-run")
        if args.dry_run:
            config = resolve_telegram_config(args.env_file, required=False)
            if args.replay_update_file:
                result = run_once(args, config)
                emit_result(args, result)
                return 0 if result.get("status") != "unsupported" else 2
            command_text = args.command_text or args.send_command_text or "/status"
            state = load_state(args.state_file)
            feedback_context = last_context_for_chat_hash(
                state,
                config.get("target_chat_id_hash"),
            )
            result = render_command_result(
                args,
                config,
                command_text,
                mode="dry_run",
                feedback_context=feedback_context,
            )
            emit_result(args, result)
            return 0 if result.get("status") != "unsupported" else 2
        if args.send_command_text:
            config = resolve_telegram_config(args.env_file, required=True)
            result = send_command_text(args, config)
            emit_result(args, result)
            return 0 if result.get("status") != "unsupported" else 2
        if not args.once:
            raise RemoteOperatorTelegramError("live mode currently requires --once")
        config = resolve_telegram_config(args.env_file, required=True)
        result = run_once(args, config)
        emit_result(args, result)
        return 0
    except RemoteOperatorTelegramError as error:
        result = {
            "schema": RESULT_SCHEMA,
            "generated_at": utc_now(),
            "status": "error",
            "error": sanitize_text(str(error)),
            "read_only": True,
            "mutation_authorized": False,
            "approval_authorized": False,
            "forbidden_remote_intents": list(FORBIDDEN_REMOTE_INTENTS),
        }
        if args.out:
            write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
