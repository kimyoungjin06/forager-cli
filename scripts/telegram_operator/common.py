"""Shared helpers for the Telegram remote operator adapter."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import secrets
import stat
from typing import Any


class RemoteOperatorTelegramError(RuntimeError):
    pass


PRIVATE_FILE_MODE = 0o600
MAX_PRIVATE_ENV_BYTES = 64 * 1024


def private_file_mode_issue(path: pathlib.Path) -> str | None:
    """Return a fail-closed reason when a local secret is readable by peers."""

    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        return f"unreadable:{error}"
    if mode & 0o077:
        return f"permissions_too_open:{mode:04o}"
    return None


def require_private_file(path: pathlib.Path, *, label: str) -> None:
    issue = private_file_mode_issue(path)
    if issue:
        raise RemoteOperatorTelegramError(
            f"{label} must be private ({issue}); run: chmod 600 {path}"
        )


def read_private_text_snapshot(
    path: pathlib.Path,
    *,
    label: str,
    max_bytes: int = MAX_PRIVATE_ENV_BYTES,
) -> str:
    """Read one owner-only regular file through a stable no-follow descriptor."""

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RemoteOperatorTelegramError(
            f"{label} is not a safe readable regular file: {path} ({type(error).__name__})"
        ) from error
    try:
        initial = os.fstat(descriptor)
        mode = stat.S_IMODE(initial.st_mode)
        if not stat.S_ISREG(initial.st_mode):
            raise RemoteOperatorTelegramError(f"{label} must be a regular file: {path}")
        if initial.st_nlink != 1:
            raise RemoteOperatorTelegramError(
                f"{label} must have exactly one filesystem link: {path}"
            )
        if mode & 0o077:
            raise RemoteOperatorTelegramError(
                f"{label} must be private (permissions_too_open:{mode:04o}); "
                f"run: chmod 600 {path}"
            )
        if hasattr(os, "geteuid") and initial.st_uid != os.geteuid():
            raise RemoteOperatorTelegramError(
                f"{label} must be owned by the current user: {path}"
            )
        if initial.st_size > max_bytes:
            raise RemoteOperatorTelegramError(
                f"{label} exceeds the {max_bytes}-byte read budget: {path}"
            )
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > max_bytes:
            raise RemoteOperatorTelegramError(
                f"{label} grew beyond the {max_bytes}-byte read budget: {path}"
            )
        final = os.fstat(descriptor)
        if (
            final.st_dev != initial.st_dev
            or final.st_ino != initial.st_ino
            or final.st_size != initial.st_size
            or final.st_mtime_ns != initial.st_mtime_ns
            or len(content) != final.st_size
        ):
            raise RemoteOperatorTelegramError(
                f"{label} changed while it was being read: {path}"
            )
        try:
            active = path.lstat()
        except OSError as error:
            raise RemoteOperatorTelegramError(
                f"{label} path changed while it was being read: {path} "
                f"({type(error).__name__})"
            ) from error
        if (
            not stat.S_ISREG(active.st_mode)
            or active.st_dev != final.st_dev
            or active.st_ino != final.st_ino
        ):
            raise RemoteOperatorTelegramError(
                f"{label} path changed while it was being read: {path}"
            )
        return content.decode("utf-8", errors="replace")
    finally:
        os.close(descriptor)


def _private_text_writer(path: pathlib.Path, *, append: bool):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, "a" if append else "w", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: pathlib.Path, value: Any) -> None:
    write_private_text_atomic(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def write_private_text_atomic(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        with _private_text_writer(tmp, append=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        path.chmod(PRIVATE_FILE_MODE)
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        parent_descriptor = os.open(path.parent, flags)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def append_jsonl(path: pathlib.Path, value: Any, *, durable: bool = False) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _private_text_writer(path, append=True) as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        if durable:
            handle.flush()
            os.fsync(handle.fileno())


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_env_file(path: pathlib.Path, *, required: bool) -> dict[str, str]:
    try:
        content = read_private_text_snapshot(path, label="telegram env file")
    except RemoteOperatorTelegramError as error:
        if not required:
            try:
                missing = not path.exists()
            except OSError:
                missing = False
            if missing:
                return {}
        raise error
    values: dict[str, str] = {}
    for raw in content.splitlines():
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


def unique_nonempty(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def sha256_short(value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"
