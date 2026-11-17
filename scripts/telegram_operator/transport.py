"""Telegram Bot API transport helpers."""

from __future__ import annotations

import errno
import http.client
import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any

from .common import RemoteOperatorTelegramError, load_json
from .config import TelegramControlRevokedError, assert_telegram_config_current


class TelegramDeliveryUnconfirmed(RemoteOperatorTelegramError):
    """A send may have reached Telegram, but no trustworthy receipt was read."""


def _transport_failure(method: str, reason: str) -> RemoteOperatorTelegramError:
    kind = TelegramDeliveryUnconfirmed if method == "sendMessage" else RemoteOperatorTelegramError
    return kind(f"Telegram API {reason} ({method})")


def network_failure_kind(error: BaseException) -> str:
    """Retain a useful category without copying URLs, credentials or peer text."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    for error_type, label in (
        (ssl.SSLCertVerificationError, "tls_certificate_error"),
        (ssl.SSLError, "tls_error"),
        (socket.gaierror, "dns_error"),
        (TimeoutError, "timeout"),
        (ConnectionRefusedError, "connection_refused"),
        (ConnectionResetError, "connection_reset"),
        (ConnectionError, "connection_error"),
        (http.client.HTTPException, "http_transport_error"),
    ):
        if isinstance(cause, error_type):
            return label
    if isinstance(cause, OSError):
        return {
            errno.ENETUNREACH: "network_unreachable",
            errno.EHOSTUNREACH: "host_unreachable",
            errno.ENETDOWN: "network_down",
        }.get(cause.errno, "os_transport_error")
    return "url_error"


def telegram_api(token: str, method: str, payload: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    api_base = os.environ.get(
        "OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL",
        "https://api.telegram.org",
    ).rstrip("/")
    url = f"{api_base}/bot{token}/{method}"
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
        if method == "sendMessage" and error.code >= 500:
            raise _transport_failure(method, "server response uncertain") from error
        detail = error.read().decode("utf-8", errors="replace") if hasattr(error, "read") else str(error)
        raise RemoteOperatorTelegramError(f"Telegram API HTTP error ({method}): {detail}") from error
    except (TimeoutError, http.client.HTTPException, ConnectionError) as error:
        raise _transport_failure(method, f"transport error: {type(error).__name__}") from error
    except urllib.error.URLError as error:
        raise _transport_failure(method, f"URL error: {network_failure_kind(error)}") from error
    except OSError as error:
        raise _transport_failure(method, f"transport error: {type(error).__name__}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _transport_failure(method, "invalid response") from error
    if not isinstance(data, dict) or type(data.get("ok")) is not bool:
        raise _transport_failure(method, "invalid response envelope")
    if data["ok"] is False:
        raise RemoteOperatorTelegramError(f"Telegram API error ({method}): {data}")
    return data


def get_updates(config: dict[str, Any], offset: int, args: Any) -> list[dict[str, Any]]:
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
    assert_telegram_config_current(config)
    data = telegram_api(
        config["token"],
        "getUpdates",
        {
            "offset": int(offset),
            "timeout": max(0, int(args.poll_timeout_sec)),
            "allowed_updates": ["message", "callback_query"],
        },
        timeout_sec=max(int(args.api_timeout_sec), int(args.poll_timeout_sec) + 10),
    )
    # A rotation can happen while getUpdates is blocked. Do not process the
    # returned update under authority that is no longer current.
    assert_telegram_config_current(config)
    updates = data.get("result", [])
    return [item for item in updates if isinstance(item, dict)] if isinstance(updates, list) else []


def send_message(
    config: dict[str, Any],
    chat_id: str,
    message: str,
    args: Any,
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
    assert_telegram_config_current(config)
    data = telegram_api(
        config["token"],
        "sendMessage",
        payload,
        timeout_sec=max(1, int(args.api_timeout_sec)),
    )
    result = data.get("result")
    if isinstance(result, dict) and type(result.get("message_id")) is int and result["message_id"] > 0:
        return result["message_id"]
    raise TelegramDeliveryUnconfirmed("Telegram sendMessage acknowledgement missing")


def edit_message(
    config: dict[str, Any],
    chat_id: str,
    message_id: int,
    message: str,
    args: Any,
    *,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Best-effort edit of a previously sent message (e.g. expiring a stale
    action card). Returns False instead of raising on dry-run or API refusal
    (edits fail once a message is older than Telegram's 48h edit window)."""

    if args.dry_run:
        return False
    try:
        assert_telegram_config_current(config)
        telegram_api(
            config["token"],
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": reply_markup if reply_markup is not None else {"inline_keyboard": []},
            },
            timeout_sec=max(1, int(args.api_timeout_sec)),
        )
        return True
    except TelegramControlRevokedError:
        raise
    except Exception:  # noqa: BLE001 - expiry edits must never break the loop
        return False


def answer_callback_query(
    config: dict[str, Any], callback_query_id: str, args: Any, *, text: str = ""
) -> bool:
    """Clear Telegram's inline-button spinner without widening authority."""

    if args.dry_run or not callback_query_id:
        return False
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:160]
    try:
        assert_telegram_config_current(config)
        telegram_api(
            config["token"],
            "answerCallbackQuery",
            payload,
            timeout_sec=max(1, int(args.api_timeout_sec)),
        )
        return True
    except TelegramControlRevokedError:
        raise
    except Exception:  # noqa: BLE001 - callback acknowledgement is best effort
        return False


def clear_inline_keyboard(
    config: dict[str, Any], chat_id: str, message_id: int, args: Any
) -> bool:
    """Invalidate one action card without rewriting its visible evidence."""

    if args.dry_run:
        return False
    try:
        assert_telegram_config_current(config)
        telegram_api(
            config["token"],
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": int(message_id),
                "reply_markup": {"inline_keyboard": []},
            },
            timeout_sec=max(1, int(args.api_timeout_sec)),
        )
        return True
    except TelegramControlRevokedError:
        raise
    except Exception:  # noqa: BLE001 - stale card cleanup is best effort
        return False
