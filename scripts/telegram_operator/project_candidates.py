"""Workspace project discovery and candidate ranking."""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from typing import Any

from .common import csv_values, sha256_short
from .rendering import sanitize_text


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROJECT_CANDIDATE_SCHEMA = "telegram_remote_project_candidate.v1"
PROJECT_MARKER_FILES = (
    ".git",
    ".forager",
    "AGENTS.md",
    "README.md",
    "README_KO.md",
    "Cargo.toml",
    "pyproject.toml",
    "package.json",
    "uv.lock",
)


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


def truncate_label(value: Any, *, max_chars: int = 34) -> str:
    text = sanitize_text(str(value or "").strip(), max_chars=max_chars + 20)
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)] + "…"


def slugify_project_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9가-힣]+", "-", text)
    return text.strip("-") or "project"


def workspace_root_inputs(args: Any) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    for raw in args.workspace_root or []:
        roots.append(pathlib.Path(raw))
    for raw in csv_values(os.environ.get("OFFDESK_REMOTE_OPERATOR_WORKSPACE_ROOTS", "")):
        roots.append(pathlib.Path(raw))
    if roots:
        return roots
    for parent in REPO_ROOT.parents:
        if parent.name == "Workspace" and parent.exists():
            return [parent]
    return [REPO_ROOT.parent]


def workspace_roots(args: Any) -> list[pathlib.Path]:
    roots: list[pathlib.Path] = []
    seen: set[str] = set()
    for raw in workspace_root_inputs(args):
        try:
            path = pathlib.Path(raw).expanduser().resolve()
        except OSError:
            path = pathlib.Path(raw).expanduser()
        key = str(path)
        if key in seen or not path.exists() or not path.is_dir():
            continue
        seen.add(key)
        roots.append(path)
    return roots


def project_marker_names(path: pathlib.Path) -> list[str]:
    markers: list[str] = []
    for name in PROJECT_MARKER_FILES:
        if (path / name).exists():
            markers.append(name)
    return markers


def looks_like_project_dir(path: pathlib.Path) -> bool:
    if project_marker_names(path):
        return True
    try:
        child_names = {child.name for child in path.iterdir() if child.is_dir()}
    except OSError:
        return False
    return bool(child_names.intersection({"src", "scripts", "tests", "forager-cli"}))


def discover_project_paths(roots: list[pathlib.Path], *, max_paths: int = 80) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    seen: set[str] = set()

    def add(path: pathlib.Path) -> None:
        if len(found) >= max_paths:
            return
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = str(resolved)
        if key in seen or not path.exists() or not path.is_dir():
            return
        if not looks_like_project_dir(path):
            return
        seen.add(key)
        found.append(path)

    for root in roots:
        add(root)
        try:
            children = sorted(
                [child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")],
                key=lambda item: item.name.lower(),
            )
        except OSError:
            continue
        for child in children:
            add(child)
        for child in children[:40]:
            try:
                nested = sorted(
                    [
                        grandchild
                        for grandchild in child.iterdir()
                        if grandchild.is_dir() and not grandchild.name.startswith(".")
                    ],
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                continue
            for grandchild in nested:
                if (grandchild / ".git").exists() or project_marker_names(grandchild):
                    add(grandchild)
    return found


def git_output(path: pathlib.Path, args: list[str], *, timeout_sec: float = 1.5) -> str | None:
    try:
        process = subprocess.run(
            ["git", "-C", str(path), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    return process.stdout.strip()


def is_git_repo(path: pathlib.Path) -> bool:
    marker = path / ".git"
    if marker.exists():
        return True
    return git_output(path, ["rev-parse", "--is-inside-work-tree"]) == "true"


def request_tokens(*values: Any) -> set[str]:
    text = " ".join(str(value or "") for value in values)
    tokens = re.findall(r"[A-Za-z0-9가-힣]{2,}", text.lower())
    return set(tokens)


def relative_path_hint(path: pathlib.Path, roots: list[pathlib.Path]) -> str:
    for root in roots:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            continue
    return path.name


def project_readiness(markers: list[str], git_repo: bool, dirty: bool | None) -> str:
    if git_repo and dirty is False:
        return "ready"
    if git_repo:
        return "needs_review"
    if markers:
        return "needs_review"
    return "not_git"


def project_risk(readiness: str, dirty: bool | None) -> str:
    if readiness == "ready":
        return "low"
    if dirty is True:
        return "medium"
    return "medium" if readiness == "needs_review" else "high"


def display_project_readiness(value: Any) -> str:
    labels = {
        "ready": "준비됨",
        "needs_review": "검토 필요",
        "not_git": "경로 확인 필요",
    }
    return labels.get(str(value or ""), "검토 필요")


def display_project_risk(value: Any) -> str:
    labels = {"low": "낮음", "medium": "중간", "high": "높음"}
    return labels.get(str(value or ""), "중간")


def build_project_candidate(
    path: pathlib.Path,
    *,
    roots: list[pathlib.Path],
    tokens: set[str],
    rank: int,
) -> dict[str, Any]:
    markers = project_marker_names(path)
    git_repo = is_git_repo(path)
    branch = git_output(path, ["branch", "--show-current"]) if git_repo else None
    head = git_output(path, ["rev-parse", "--short", "HEAD"]) if git_repo else None
    status = git_output(path, ["status", "--porcelain"]) if git_repo else None
    dirty = bool(status) if status is not None else (None if git_repo else False)
    hint = relative_path_hint(path, roots)
    display_name = path.name if path.name else hint
    if "/" in hint and path.name not in hint.split("/", 1)[0]:
        display_name = hint
    readiness = project_readiness(markers, git_repo, dirty)
    risk = project_risk(readiness, dirty)
    reasons: list[str] = []
    if git_repo:
        reasons.append("git repository")
    if dirty is False and git_repo:
        reasons.append("clean worktree")
    if dirty is True:
        reasons.append("dirty worktree")
    for marker in markers[:3]:
        if marker != ".git":
            reasons.append(marker)
    name_blob = f"{display_name} {hint}".lower()
    token_hits = sum(1 for token in tokens if token and token in name_blob)
    score = token_hits * 20
    score += 10 if git_repo else 0
    score += 5 if dirty is False and git_repo else 0
    score += min(5, len(markers))
    score -= 3 if dirty is True else 0
    return {
        "schema": PROJECT_CANDIDATE_SCHEMA,
        "rank": rank,
        "score": score,
        "project_key": slugify_project_key(display_name),
        "display_name": truncate_label(display_name, max_chars=40),
        "workspace_path": str(path),
        "workspace_path_hint": sanitize_text(hint, max_chars=160),
        "is_git_repo": git_repo,
        "branch": branch,
        "head": head,
        "dirty": dirty,
        "readiness": readiness,
        "risk": risk,
        "autonomy_fit": "high" if readiness == "ready" else "medium" if readiness == "needs_review" else "low",
        "reasons": unique_nonempty(reasons)[:4],
        "next_step": "init_review",
    }


def ranked_project_candidates(
    args: Any,
    *,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    roots = workspace_roots(args)
    token_set = request_tokens(
        request_text,
        agent_intent.get("project_hint") if isinstance(agent_intent, dict) else None,
        agent_intent.get("goal") if isinstance(agent_intent, dict) else None,
    )
    candidates = [
        build_project_candidate(path, roots=roots, tokens=token_set, rank=index + 1)
        for index, path in enumerate(discover_project_paths(roots))
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            str(item.get("workspace_path_hint") or "").lower(),
        )
    )
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    return candidates


def scan_project_candidates(
    args: Any,
    *,
    request_text: str,
    agent_intent: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidates = ranked_project_candidates(
        args,
        request_text=request_text,
        agent_intent=agent_intent,
    )
    limited = candidates[: max(1, int(args.max_project_candidates))]
    for index, candidate in enumerate(limited, start=1):
        candidate["rank"] = index
    return limited


def public_project_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    public = dict(candidate)
    if "workspace_path" in public:
        public["workspace_path_hash"] = sha256_short(public.pop("workspace_path"))
    return public
