"""Telegram adapter configuration resolution."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
from typing import Any

from .common import (
    RemoteOperatorTelegramError,
    csv_values,
    parse_env_file,
    sha256_short,
)


DEFAULT_OPERATOR_MODE = "single_owner"
MULTI_OPERATOR_MODE = "multi_operator"


class TelegramControlRevokedError(RemoteOperatorTelegramError):
    """Raised when the live listener's local control authority changed."""

    def __init__(
        self,
        reason: str,
        *,
        previous_generation: str | None,
        current_generation: str | None,
    ) -> None:
        super().__init__(reason)
        self.previous_generation = previous_generation
        self.current_generation = current_generation


def _control_generation_sha256(
    *,
    token: str,
    operator_mode: str,
    owner_chat_id: str,
    owner_user_id: str,
    operator_members: set[tuple[str, str]],
) -> str:
    material = json.dumps(
        {
            "transport": "telegram",
            "token": token,
            "operator_mode": operator_mode,
            "owner_chat_id": owner_chat_id,
            "owner_user_id": owner_user_id,
            "operator_members": sorted(
                [list(member) for member in operator_members]
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def public_control_generation_id(generation: Any) -> str | None:
    value = str(generation or "").strip()
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        return None
    return f"sha256:{value.removeprefix('sha256:')[:16]}"


def telegram_id_is_valid(value: str) -> bool:
    normalized = str(value or "").strip()
    digits = normalized[1:] if normalized.startswith("-") else normalized
    return bool(digits) and digits.isdigit() and int(normalized) != 0


def parse_operator_members(raw: str) -> tuple[set[tuple[str, str]], list[str]]:
    members: set[tuple[str, str]] = set()
    issues: list[str] = []
    for item in csv_values(raw):
        parts = [part.strip() for part in item.split(":")]
        if (
            len(parts) != 2
            or not all(parts)
            or not all(telegram_id_is_valid(part) for part in parts)
        ):
            issues.append("multi_operator_member_invalid")
            continue
        chat_id, user_id = parts
        members.add((chat_id, user_id))
    return members, issues


def default_telegram_env_file() -> pathlib.Path:
    configured = os.environ.get("OFFDESK_TELEGRAM_ENV", "").strip()
    if configured:
        return pathlib.Path(configured).expanduser()
    if sys.platform.startswith("linux"):
        config_root = pathlib.Path(
            os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config")
        )
        return config_root / "forager" / "telegram.env"
    return pathlib.Path.home() / ".forager" / "telegram.env"


def resolve_telegram_config(env_file: pathlib.Path, *, required: bool) -> dict[str, Any]:
    env = parse_env_file(env_file, required=required)
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    operator_mode = (
        env.get("TELEGRAM_OPERATOR_MODE", DEFAULT_OPERATOR_MODE).strip().lower()
    )
    if not operator_mode:
        operator_mode = DEFAULT_OPERATOR_MODE
    owner_chat_id = env.get("TELEGRAM_OWNER_CHAT_ID", "").strip()
    owner_user_id = env.get("TELEGRAM_OWNER_USER_ID", "").strip()
    configured_chat_ids = set(csv_values(env.get("TELEGRAM_ALLOW_CHAT_IDS", "")))
    configured_chat_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")))
    configured_user_ids = set(csv_values(env.get("TELEGRAM_ALLOW_USER_IDS", "")))
    configured_user_ids.update(csv_values(env.get("TELEGRAM_ALLOWED_USER_IDS", "")))
    operator_members, member_issues = parse_operator_members(
        env.get("TELEGRAM_OPERATOR_MEMBERS", "")
    )

    operator_config_issues: list[str] = []
    if operator_mode == DEFAULT_OPERATOR_MODE:
        if not owner_chat_id:
            operator_config_issues.append("single_owner_chat_id_missing")
        if not owner_user_id:
            operator_config_issues.append("single_owner_user_id_missing")
        if owner_chat_id and not telegram_id_is_valid(owner_chat_id):
            operator_config_issues.append("single_owner_chat_id_invalid")
        if owner_user_id and not telegram_id_is_valid(owner_user_id):
            operator_config_issues.append("single_owner_user_id_invalid")
        if configured_chat_ids - {owner_chat_id}:
            operator_config_issues.append("single_owner_extra_chat_ids_configured")
        if configured_user_ids - {owner_user_id}:
            operator_config_issues.append("single_owner_extra_user_ids_configured")
        if operator_members:
            operator_config_issues.append("single_owner_operator_members_configured")
        operator_config_issues.extend(member_issues)
        allowed_chat_ids = {owner_chat_id} if owner_chat_id else set()
        allowed_user_ids = {owner_user_id} if owner_user_id else set()
        exact_operator_pairs = (
            {(owner_chat_id, owner_user_id)}
            if owner_chat_id and owner_user_id
            else set()
        )
    elif operator_mode == MULTI_OPERATOR_MODE:
        if not owner_chat_id:
            operator_config_issues.append("multi_operator_owner_chat_id_missing")
        if not owner_user_id:
            operator_config_issues.append("multi_operator_owner_user_id_missing")
        if owner_chat_id and not telegram_id_is_valid(owner_chat_id):
            operator_config_issues.append("multi_operator_owner_chat_id_invalid")
        if owner_user_id and not telegram_id_is_valid(owner_user_id):
            operator_config_issues.append("multi_operator_owner_user_id_invalid")
        if configured_chat_ids or configured_user_ids:
            operator_config_issues.append("multi_operator_unpaired_allowlist_configured")
        if not operator_members:
            operator_config_issues.append("multi_operator_members_missing")
        operator_config_issues.extend(member_issues)
        exact_operator_pairs = set(operator_members)
        if owner_chat_id and owner_user_id:
            exact_operator_pairs.add((owner_chat_id, owner_user_id))
        allowed_chat_ids = {chat_id for chat_id, _ in exact_operator_pairs}
        allowed_user_ids = {user_id for _, user_id in exact_operator_pairs}
    else:
        operator_config_issues.append("telegram_operator_mode_invalid")
        allowed_chat_ids = set()
        allowed_user_ids = set()
        exact_operator_pairs = set()

    target_chat_id = owner_chat_id
    if required and not token:
        raise RemoteOperatorTelegramError("TELEGRAM_BOT_TOKEN is missing")
    if required and operator_config_issues:
        raise RemoteOperatorTelegramError(
            "Telegram operator identity is not fail-closed: "
            + ", ".join(operator_config_issues)
        )
    return {
        "token": token,
        "operator_mode": operator_mode,
        "operator_config_issues": operator_config_issues,
        "operator_identity_configured": not operator_config_issues,
        "owner_chat_id": owner_chat_id,
        "owner_user_id": owner_user_id,
        "operator_members": exact_operator_pairs,
        "target_chat_id": target_chat_id,
        "target_chat_id_hash": sha256_short(target_chat_id) if target_chat_id else None,
        "allowed_chat_ids": allowed_chat_ids,
        "allowed_user_ids": allowed_user_ids,
        "chat_allowlist_configured": bool(allowed_chat_ids),
        "user_allowlist_configured": bool(allowed_user_ids),
        "env_file": str(env_file),
        "_control_generation_sha256": _control_generation_sha256(
            token=token,
            operator_mode=operator_mode,
            owner_chat_id=owner_chat_id,
            owner_user_id=owner_user_id,
            operator_members=exact_operator_pairs,
        ),
    }


def assert_telegram_config_current(
    config: dict[str, Any], *, required: bool = True
) -> None:
    """Fail closed when a running listener no longer owns the current env."""

    previous = str(config.get("_control_generation_sha256") or "").strip() or None
    env_file = pathlib.Path(str(config.get("env_file") or ""))
    try:
        current = resolve_telegram_config(env_file, required=required)
    except RemoteOperatorTelegramError as error:
        raise TelegramControlRevokedError(
            f"Telegram control authority is no longer readable: {type(error).__name__}",
            previous_generation=previous,
            current_generation=None,
        ) from error
    observed = (
        str(current.get("_control_generation_sha256") or "").strip() or None
    )
    if observed != previous:
        raise TelegramControlRevokedError(
            "Telegram control authority changed while the listener was running",
            previous_generation=previous,
            current_generation=observed,
        )
