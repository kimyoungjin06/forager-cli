#!/usr/bin/env python3
"""Build a deterministic TwinPaper evidence bundle for Offdesk work."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
from typing import Any


DEFAULT_REPO = pathlib.Path("/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper")
DEFAULT_TERMS = (
    "Direction Review Start Rule",
    "no-option",
    "singlex",
    "openexplore",
    "open-explore",
    "direction-review",
    "direction_review",
    "validated_candidate",
    "p/q",
    "restart_stability",
    "primary_objective_gate",
)
SOURCE_FILES = (
    "AGENTS.md",
    "docs/operations/RunLog.md",
    "modules/03_regspec_machine/README.md",
    "modules/03_regspec_machine/scripts/run_module_03.sh",
    "modules/03_regspec_machine/regspec_machine/orchestrator.py",
    "modules/03_regspec_machine/tests/test_orchestrator.py",
)
ARTIFACT_PATTERNS = {
    "direction_review": ("data/metadata/*machine_scientist_direction_review*.json",),
    "paired_preset_summary": ("data/metadata/*paired_preset_summary*.json",),
    "run_summary_nooption": ("data/metadata/*run_summary*nooption*.json",),
    "run_summary_singlex": ("data/metadata/*run_summary*singlex*.json",),
    "run_summary_openexplore": ("data/metadata/*run_summary*openexplore*.json",),
}
INTERESTING_KEY_RE = re.compile(
    r"(validated|candidate|primary.*gate|objective.*gate|restart|p[_-]?value|q[_-]?value|p/q|singlex|nooption|no-option|track.*consensus|support|status)",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=pathlib.Path, default=DEFAULT_REPO)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--tail-lines", type=int, default=80)
    parser.add_argument("--max-excerpts-per-term", type=int, default=12)
    parser.add_argument("--max-artifacts-per-group", type=int, default=8)
    return parser.parse_args()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: pathlib.Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_meta(repo: pathlib.Path, rel: str) -> dict[str, Any]:
    path = repo / rel
    exists = path.exists()
    stat = path.stat() if exists else None
    return {
        "path": rel,
        "exists": exists,
        "size_bytes": stat.st_size if stat else None,
        "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat() if stat else None,
        "sha256": sha256_file(path) if exists else None,
    }


def read_lines(path: pathlib.Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def tail_entries(path: pathlib.Path, max_lines: int) -> list[dict[str, Any]]:
    lines = read_lines(path)
    start = max(0, len(lines) - max_lines)
    return [{"line": idx + 1, "text": lines[idx]} for idx in range(start, len(lines))]


def matching_excerpts(path: pathlib.Path, terms: tuple[str, ...], max_per_term: int) -> dict[str, list[dict[str, Any]]]:
    lines = read_lines(path)
    excerpts: dict[str, list[dict[str, Any]]] = {term: [] for term in terms}
    lowered_terms = [(term, term.lower()) for term in terms]
    for idx, line in enumerate(lines, start=1):
        lowered = line.lower()
        for term, lowered_term in lowered_terms:
            if lowered_term in lowered and len(excerpts[term]) < max_per_term:
                excerpts[term].append({"line": idx, "text": line})
    return excerpts


def artifact_sort_key(path: pathlib.Path) -> tuple[str, float, str]:
    date_tokens = re.findall(r"20\d{6}|20\d{4}", path.name)
    return (date_tokens[-1] if date_tokens else "", path.stat().st_mtime, path.name)


def short_value(value: Any, limit: int = 300) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "...[truncated]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return repr(value)[:limit]


def extract_metric_paths(value: Any, prefix: str = "", out: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if out is None:
        out = []
    if len(out) >= 80:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if INTERESTING_KEY_RE.search(str(key)):
                out.append({"path": path, "value": short_value(child)})
                if len(out) >= 80:
                    return out
            extract_metric_paths(child, path, out)
    elif isinstance(value, list):
        for idx, child in enumerate(value[:20]):
            extract_metric_paths(child, f"{prefix}[{idx}]", out)
            if len(out) >= 80:
                return out
    return out


def load_artifact(path: pathlib.Path, repo: pathlib.Path) -> dict[str, Any]:
    rel = path.relative_to(repo).as_posix()
    record = file_meta(repo, rel)
    record["top_level_keys"] = []
    record["metric_paths"] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        record["parse_error"] = repr(error)
        return record
    if isinstance(data, dict):
        record["top_level_keys"] = sorted(str(key) for key in data.keys())[:80]
        record["metric_paths"] = extract_metric_paths(data)
    else:
        record["json_type"] = type(data).__name__
    return record


def collect_artifacts(repo: pathlib.Path, max_per_group: int) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for group, patterns in ARTIFACT_PATTERNS.items():
        paths: list[pathlib.Path] = []
        for pattern in patterns:
            paths.extend(path for path in repo.glob(pattern) if path.is_file())
        unique_paths = sorted(set(paths), key=artifact_sort_key, reverse=True)[:max_per_group]
        groups[group] = [load_artifact(path, repo) for path in unique_paths]
    return groups


def line_texts(excerpts: dict[str, list[dict[str, Any]]], terms: tuple[str, ...]) -> str:
    lines: list[str] = []
    for term in terms:
        lines.extend(str(item.get("text", "")) for item in excerpts.get(term, []))
    return "\n".join(lines).lower()


def derive_current_state(excerpts: dict[str, list[dict[str, Any]]], artifacts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    recent_text = line_texts(
        excerpts,
        (
            "direction-review",
            "direction_review",
            "primary_objective_gate",
            "validated_candidate",
            "p/q",
            "restart_stability",
            "no-option",
            "singlex",
            "openexplore",
            "open-explore",
        ),
    )
    has_baseline = "nooption" in recent_text or "no-option" in recent_text
    has_singlex = "singlex" in recent_text
    has_openexplore = "openexplore" in recent_text or "open-explore" in recent_text
    has_direction_review = "direction-review" in recent_text or "direction_review" in recent_text
    gate_failed = "primary_objective_gate" in recent_text and "false" in recent_text
    if (has_baseline and has_singlex and gate_failed) or "primary objective gate `false`" in recent_text:
        baseline_status = "executed_primary_gate_failed"
    elif has_baseline and has_singlex:
        baseline_status = "executed_status_unclear"
    else:
        baseline_status = "missing_or_not_in_bundle"
    latest_direction_review = artifacts.get("direction_review", [])[:1]
    return {
        "baseline_evidence_status": baseline_status,
        "claim_status": "pending_not_reportable" if baseline_status != "missing_or_not_in_bundle" else "unknown",
        "has_nooption_evidence": has_baseline,
        "has_singlex_evidence": has_singlex,
        "has_openexplore_evidence": has_openexplore,
        "has_direction_review_evidence": has_direction_review or bool(latest_direction_review),
        "latest_direction_review_artifact": latest_direction_review[0]["path"] if latest_direction_review else None,
    }


def write_markdown(path: pathlib.Path, bundle: dict[str, Any]) -> None:
    state = bundle["current_state"]
    runlog = bundle["runlog"]
    lines = [
        "# TwinPaper Evidence Bundle",
        "",
        f"- created_at: `{bundle['created_at']}`",
        f"- repo: `{bundle['repo']}`",
        f"- baseline_evidence_status: `{state['baseline_evidence_status']}`",
        f"- claim_status: `{state['claim_status']}`",
        f"- latest_direction_review_artifact: `{state.get('latest_direction_review_artifact')}`",
        "",
        "## RunLog Tail",
        "",
    ]
    for item in runlog["tail"][-20:]:
        lines.append(f"- L{item['line']}: {item['text']}")
    lines.extend(["", "## Targeted Excerpts", ""])
    for term, excerpts in runlog["targeted_excerpts"].items():
        if not excerpts:
            continue
        lines.extend([f"### {term}", ""])
        for item in excerpts[-8:]:
            lines.append(f"- L{item['line']}: {item['text']}")
        lines.append("")
    lines.extend(["## Artifacts", ""])
    for group, records in bundle["artifacts"].items():
        lines.extend([f"### {group}", ""])
        for record in records[:5]:
            lines.append(f"- `{record['path']}` size={record.get('size_bytes')} modified={record.get('modified_at')}")
            for metric in record.get("metric_paths", [])[:10]:
                lines.append(f"  - `{metric['path']}` = `{metric['value']}`")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_bundle(args: argparse.Namespace) -> dict[str, Any]:
    repo = args.repo.expanduser().resolve()
    runlog_path = repo / "docs/operations/RunLog.md"
    source_files = {rel: file_meta(repo, rel) for rel in SOURCE_FILES}
    targeted_excerpts = matching_excerpts(runlog_path, DEFAULT_TERMS, args.max_excerpts_per_term)
    artifacts = collect_artifacts(repo, args.max_artifacts_per_group)
    bundle = {
        "kind": "twinpaper_evidence_bundle",
        "version": 1,
        "created_at": utc_now(),
        "repo": str(repo),
        "source_files": source_files,
        "runlog": {
            "path": "docs/operations/RunLog.md",
            "tail": tail_entries(runlog_path, args.tail_lines),
            "targeted_excerpts": targeted_excerpts,
        },
        "artifacts": artifacts,
        "entrypoints": {
            rel: {
                "path": rel,
                "exists": (repo / rel).exists(),
            }
            for rel in (
                "modules/03_regspec_machine/scripts/run_module_03.sh",
                "modules/03_regspec_machine/regspec_machine/orchestrator.py",
                "modules/03_regspec_machine/tests/test_orchestrator.py",
            )
        },
    }
    bundle["current_state"] = derive_current_state(targeted_excerpts, artifacts)
    return bundle


def main() -> int:
    args = parse_args()
    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(args)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(out_path.with_name("EVIDENCE.md"), bundle)
    print(json.dumps({"out": str(out_path), "baseline_evidence_status": bundle["current_state"]["baseline_evidence_status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
