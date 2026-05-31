#!/usr/bin/env python3
"""Audit project documentation and artifact governance surfaces.

This is a lightweight first pass for long-running agent projects. It checks the
small current surfaces, deliverable links, large logs, and optional adaptive wiki
projection freshness without moving or rewriting project files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


CURRENT_SURFACES = ("CURRENT_STATE.md", "PROJECT_STATE.md")
STANDARD_SURFACES = ("NEXT_ACTIONS.md", "DECISIONS.md", "DELIVERABLES.md")
LOG_NAMES = ("AGENT_LOG.md", "AGENTS.log", "RunLog.md")
DELIVERABLE_EXTENSIONS = {".html", ".png", ".jpg", ".jpeg", ".pdf"}
OUTPUT_ROOTS = ("outputs", "web", "deliverables", "previews", "gallery")
LOCAL_OUTPUT_PREFIXES = ("target/", "book/", "dist/")


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    path: str | None = None
    suggestion: str | None = None


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(errors="replace")


def add_finding(
    findings: list[Finding],
    severity: str,
    code: str,
    message: str,
    path: str | None = None,
    suggestion: str | None = None,
) -> None:
    findings.append(Finding(severity, code, message, path, suggestion))


def extract_backtick_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"`([^`]+)`", text):
        value = match.group(1).strip()
        if not value or " " in value:
            continue
        if value.startswith(("http://", "https://", "forager ", "python ", "./.venv")):
            continue
        if any(ch in value for ch in "*<>|"):
            continue
        if "/" in value or "." in Path(value).name:
            paths.append(value)
    return paths


def parse_updated_date(text: str) -> date | None:
    match = re.search(r"^Updated:\s*(\d{4}-\d{2}-\d{2})\s*$", text, re.MULTILINE)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: Path, names: Iterable[str]) -> Iterable[Path]:
    wanted = set(names)
    for path in root.rglob("*"):
        if path.is_file() and path.name in wanted:
            yield path


def audit_surfaces(root: Path, profile: str, findings: list[Finding]) -> dict[str, object]:
    existing = {name: (root / name).exists() for name in CURRENT_SURFACES + STANDARD_SURFACES}
    current_present = any(existing[name] for name in CURRENT_SURFACES)
    required = {
        "light": (),
        "standard": ("current", "DECISIONS.md", "DELIVERABLES.md"),
        "research-longrun": ("current", "NEXT_ACTIONS.md", "DECISIONS.md", "DELIVERABLES.md"),
    }[profile]

    if "current" in required and not current_present:
        add_finding(
            findings,
            "warn",
            "missing_current_surface",
            "No CURRENT_STATE.md or PROJECT_STATE.md was found.",
            suggestion="Add a compact current-state surface before relying on logs or README history.",
        )

    for surface in required:
        if surface == "current":
            continue
        if not existing.get(surface, False):
            add_finding(
                findings,
                "warn",
                "missing_surface",
                f"{surface} is missing for profile {profile}.",
                surface,
                "Add the surface or record where the equivalent current surface lives.",
            )

    return {"surfaces": existing, "current_present": current_present}


def audit_entrypoints(root: Path, findings: list[Finding]) -> dict[str, object]:
    entrypoints = [p for p in (root / "README.md", root / "AGENTS.md") if p.exists()]
    current_names = set(CURRENT_SURFACES + STANDARD_SURFACES)
    summary: dict[str, object] = {}

    for path in entrypoints:
        text = read_text(path)
        mentioned_current = [name for name in current_names if name in text]
        mentioned_logs = [name for name in LOG_NAMES if name in text]
        summary[path.name] = {
            "mentioned_current_surfaces": mentioned_current,
            "mentioned_logs": mentioned_logs,
        }
        if mentioned_logs and not mentioned_current:
            add_finding(
                findings,
                "warn",
                "entrypoint_points_to_log_without_current_surface",
                f"{path.name} references logs but no current-state surface.",
                rel(path, root),
                "Point new agents to current state and next actions before the chronological log.",
            )

    return summary


def audit_deliverables(root: Path, findings: list[Finding]) -> dict[str, object]:
    deliverables = root / "DELIVERABLES.md"
    if not deliverables.exists():
        output_candidates = collect_output_candidates(root)
        if output_candidates:
            add_finding(
                findings,
                "info",
                "deliverables_surface_missing",
                f"No DELIVERABLES.md exists, while {len(output_candidates)} human-facing output candidates were found.",
                suggestion="Add a deliverables surface if these outputs are meant for inspection or handoff.",
            )
        return {
            "present": False,
            "paths": 0,
            "missing_paths": [],
            "output_candidates": len(output_candidates),
        }

    text = read_text(deliverables)
    paths = extract_backtick_paths(text)
    referenced = set(paths)
    missing: list[str] = []
    existing_paths: list[Path] = []
    for value in paths:
        candidate = root / value
        if not candidate.exists():
            missing.append(value)
            add_finding(
                findings,
                "error",
                "missing_deliverable_path",
                "DELIVERABLES.md references a missing path.",
                value,
                "Update the deliverables surface or restore the artifact.",
            )
        else:
            existing_paths.append(candidate)

    output_candidates = collect_output_candidates(root)
    retention_managed = collect_retention_managed_outputs(root, output_candidates, referenced)
    referenced_outputs = sum(1 for value in paths if Path(value).suffix.lower() in DELIVERABLE_EXTENSIONS)
    if output_candidates and referenced_outputs == 0:
        add_finding(
            findings,
            "warn",
            "deliverables_without_human_outputs",
            "DELIVERABLES.md exists but does not reference HTML, image, or PDF outputs.",
            rel(deliverables, root),
            "Link the selected inspection artifacts from the deliverables surface.",
        )

    unreferenced = [
        path
        for path in output_candidates
        if rel(path, root) not in referenced
        and not equivalent_latest_alias_is_referenced(path, root, referenced)
        and not manifest_covers_output(path, root, referenced)
        and path not in retention_managed
    ]
    manifest_covered = [
        path
        for path in output_candidates
        if rel(path, root) not in referenced and manifest_covers_output(path, root, referenced)
    ]
    largest_unreferenced = sorted(unreferenced, key=lambda path: path.stat().st_size, reverse=True)[:10]
    if largest_unreferenced:
        add_finding(
            findings,
            "info",
            "unreferenced_human_output_candidates",
            f"{len(unreferenced)} human-facing output candidates are not listed in DELIVERABLES.md.",
            rel(deliverables, root),
            "Review the largest candidates and promote only the outputs useful for inspection or handoff.",
        )

    latest_aliases = audit_latest_aliases(root, existing_paths, findings)
    local_outputs = [
        rel(path, root)
        for path in existing_paths
        if any(rel(path, root).startswith(prefix) for prefix in LOCAL_OUTPUT_PREFIXES)
    ]

    return {
        "present": True,
        "paths": len(paths),
        "missing_paths": missing,
        "output_candidates": len(output_candidates),
        "referenced_human_outputs": referenced_outputs,
        "manifest_covered_human_outputs": len(manifest_covered),
        "retention_managed_human_outputs": len(retention_managed),
        "retention_managed_human_output_paths": [
            {"path": rel(path, root), "bytes": path.stat().st_size} for path in retention_managed
        ],
        "unreferenced_human_outputs": len(unreferenced),
        "unreferenced_human_output_paths": [
            {"path": rel(path, root), "bytes": path.stat().st_size} for path in unreferenced
        ],
        "largest_unreferenced_human_outputs": [
            {"path": rel(path, root), "bytes": path.stat().st_size} for path in largest_unreferenced
        ],
        "latest_aliases": latest_aliases,
        "local_outputs": local_outputs,
    }


def collect_output_candidates(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in OUTPUT_ROOTS:
        base = root / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in DELIVERABLE_EXTENSIONS:
                candidates.append(path)
    return sorted(candidates)


def collect_retention_managed_outputs(root: Path, output_candidates: list[Path], referenced: set[str]) -> list[Path]:
    retention_review = root / "RETENTION_REVIEW.md"
    if not retention_review.exists():
        return []
    managed_refs = set(extract_backtick_paths(read_text(retention_review)))
    managed: list[Path] = []
    for path in output_candidates:
        path_rel = rel(path, root)
        if path_rel in referenced:
            continue
        if path_rel in managed_refs:
            managed.append(path)
    return sorted(managed)


def equivalent_latest_alias_is_referenced(path: Path, root: Path, referenced: set[str]) -> bool:
    """Return true when a non-latest output is represented by a referenced latest alias."""
    name = path.name
    if "latest" in name:
        return False
    rel_path = rel(path, root)
    if rel_path in referenced:
        return True
    for ref in referenced:
        ref_path = root / ref
        if ref_path.parent != path.parent or "latest" not in ref_path.name:
            continue
        if ref_path.suffix.lower() != path.suffix.lower():
            continue
        try:
            if ref_path.exists() and ref_path.stat().st_size == path.stat().st_size:
                if file_sha256(ref_path) == file_sha256(path):
                    return True
        except OSError:
            continue
    return False


def manifest_covers_output(path: Path, root: Path, referenced: set[str]) -> bool:
    """Return true when a referenced manifest represents sibling visual outputs."""
    if path.suffix.lower() not in DELIVERABLE_EXTENSIONS:
        return False
    for ref in referenced:
        ref_path = root / ref
        if ref_path.parent != path.parent:
            continue
        if ref_path.suffix.lower() == ".json" and "manifest" in ref_path.name.lower() and ref_path.exists():
            return True
    return False


def audit_latest_aliases(root: Path, existing_paths: list[Path], findings: list[Finding]) -> list[dict[str, object]]:
    aliases: list[dict[str, object]] = []
    for path in existing_paths:
        if "latest" not in path.name or not path.is_file():
            continue
        pattern = path.name.replace("latest", "*")
        siblings = [
            sibling
            for sibling in path.parent.glob(pattern)
            if sibling != path and sibling.is_file() and "latest" not in sibling.name
        ]
        alias_hash = file_sha256(path)
        matching = [rel(sibling, root) for sibling in siblings if file_sha256(sibling) == alias_hash]
        aliases.append(
            {
                "path": rel(path, root),
                "candidate_siblings": len(siblings),
                "matching_siblings": matching,
            }
        )
        if not matching:
            add_finding(
                findings,
                "warn",
                "latest_alias_without_matching_artifact",
                "A latest deliverable alias has no same-content tagged sibling.",
                rel(path, root),
                "Confirm whether the alias is still meaningful or replace it with a stable tagged artifact.",
            )
    return aliases


def audit_decision_sources(root: Path, findings: list[Finding]) -> dict[str, object]:
    decisions = root / "DECISIONS.md"
    if not decisions.exists():
        return {"present": False, "sources": 0, "missing_sources": []}
    sources = extract_backtick_paths(read_text(decisions))
    missing = []
    for value in sources:
        if not (root / value).exists():
            missing.append(value)
            add_finding(
                findings,
                "warn",
                "missing_decision_source",
                "DECISIONS.md references a missing source path.",
                value,
                "Update the decision source or add a transition note.",
            )
    return {"present": True, "sources": len(sources), "missing_sources": missing}


def audit_current_freshness(root: Path, findings: list[Finding], stale_days: int) -> dict[str, object]:
    current = next((root / name for name in CURRENT_SURFACES if (root / name).exists()), None)
    if current is None:
        return {"present": False}
    updated = parse_updated_date(read_text(current))
    summary: dict[str, object] = {"present": True, "path": rel(current, root), "updated": str(updated) if updated else None}
    if updated is None:
        add_finding(
            findings,
            "warn",
            "missing_current_updated_date",
            "Current-state surface is missing an Updated: YYYY-MM-DD line.",
            rel(current, root),
            "Add an Updated line so stale current surfaces can be detected.",
        )
        return summary

    watched_docs = [root / "DECISIONS.md", root / "DELIVERABLES.md", root / "NEXT_ACTIONS.md"]
    newest_watched: Path | None = None
    for path in watched_docs:
        if not path.exists():
            continue
        if newest_watched is None or path.stat().st_mtime > newest_watched.stat().st_mtime:
            newest_watched = path
    if newest_watched is not None:
        newest_date = datetime.fromtimestamp(newest_watched.stat().st_mtime).date()
        summary["newest_watched_surface"] = rel(newest_watched, root)
        summary["newest_watched_date"] = str(newest_date)
        if (newest_date - updated).days > stale_days:
            add_finding(
                findings,
                "warn",
                "current_surface_stale",
                "Current-state surface is older than another current governance surface.",
                rel(current, root),
                "Refresh the current-state summary after changing next actions, decisions, or deliverables.",
            )
    return summary


def audit_logs(root: Path, findings: list[Finding], large_log_lines: int) -> dict[str, object]:
    logs = []
    for path in iter_files(root, LOG_NAMES):
        line_count = sum(1 for _ in path.open(errors="replace"))
        item = {"path": rel(path, root), "lines": line_count}
        logs.append(item)
        if line_count >= large_log_lines:
            add_finding(
                findings,
                "warn",
                "large_log",
                f"Log has {line_count} lines.",
                rel(path, root),
                "Keep the log as evidence, but maintain a smaller current-state surface.",
            )
    return {"logs": sorted(logs, key=lambda row: row["lines"], reverse=True)}


def audit_adaptive_wiki(profile_dir: Path | None, findings: list[Finding]) -> dict[str, object] | None:
    if profile_dir is None:
        return None
    entries = profile_dir / "adaptive_wiki_entries.json"
    candidates = profile_dir / "adaptive_wiki_candidates.json"
    vault_index = profile_dir / "wiki-vault" / "index.md"
    if not profile_dir.exists():
        add_finding(findings, "error", "adaptive_profile_missing", "Adaptive wiki profile dir is missing.", str(profile_dir))
        return {"present": False}

    summary: dict[str, object] = {
        "profile_dir": str(profile_dir),
        "entries_present": entries.exists(),
        "candidates_present": candidates.exists(),
        "vault_index_present": vault_index.exists(),
    }
    if entries.exists():
        data = json.loads(read_text(entries))
        rows = data.get("entries", data if isinstance(data, list) else [])
        summary["entries"] = len(rows)
        summary["promoted"] = sum(1 for row in rows if row.get("status") == "promoted")
        summary["deprecated"] = sum(1 for row in rows if row.get("status") == "deprecated")
    if candidates.exists():
        data = json.loads(read_text(candidates))
        rows = data.get("candidates", data if isinstance(data, list) else [])
        summary["candidates"] = len(rows)

    if entries.exists() and vault_index.exists():
        if entries.stat().st_mtime > vault_index.stat().st_mtime:
            add_finding(
                findings,
                "warn",
                "stale_adaptive_wiki_projection",
                "Canonical adaptive_wiki_entries.json is newer than wiki-vault/index.md.",
                str(vault_index),
                "Re-export the human markdown projection or mark it stale in the operator surface.",
            )
            summary["projection_stale"] = True
        else:
            summary["projection_stale"] = False
    elif entries.exists() and not vault_index.exists():
        add_finding(
            findings,
            "warn",
            "missing_adaptive_wiki_projection",
            "Canonical adaptive wiki entries exist without a wiki-vault/index.md human projection.",
            str(profile_dir),
            "Export the markdown projection if humans need to inspect the wiki.",
        )
    return summary


def build_markdown_report(result: dict[str, object]) -> str:
    findings = result["findings"]
    lines = [
        "# Documentation Governance Audit",
        "",
        f"- Root: `{result['root']}`",
        f"- Profile: `{result['profile']}`",
        f"- Generated: `{result['generated_at']}`",
        f"- Findings: `{len(findings)}`",
        "",
        "## Summary",
        "",
    ]
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    if counts:
        for severity in ("error", "warn", "info"):
            if severity in counts:
                lines.append(f"- `{severity}`: {counts[severity]}")
    else:
        lines.append("- No findings.")

    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.append("_none_")
    for finding in findings:
        lines.append(f"### {finding['severity'].upper()} `{finding['code']}`")
        lines.append("")
        lines.append(finding["message"])
        if finding.get("path"):
            lines.append(f"- Path: `{finding['path']}`")
        if finding.get("suggestion"):
            lines.append(f"- Suggested action: {finding['suggestion']}")
        lines.append("")

    lines.extend(["## Machine Summary", "", "```json", json.dumps(result["summary"], indent=2), "```", ""])
    return "\n".join(lines)


def run_audit(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).expanduser().resolve()
    findings: list[Finding] = []
    summary: dict[str, object] = {}

    summary["surfaces"] = audit_surfaces(root, args.profile, findings)
    summary["entrypoints"] = audit_entrypoints(root, findings)
    summary["deliverables"] = audit_deliverables(root, findings)
    summary["decisions"] = audit_decision_sources(root, findings)
    summary["current_freshness"] = audit_current_freshness(root, findings, args.current_stale_days)
    summary["logs"] = audit_logs(root, findings, args.large_log_lines)
    adaptive_profile = Path(args.adaptive_profile_dir).expanduser().resolve() if args.adaptive_profile_dir else None
    summary["adaptive_wiki"] = audit_adaptive_wiki(adaptive_profile, findings)

    result = {
        "schema": "documentation_governance_audit_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "profile": args.profile,
        "summary": summary,
        "findings": [asdict(finding) for finding in findings],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="Project root to audit")
    parser.add_argument(
        "--profile",
        choices=("light", "standard", "research-longrun"),
        default="standard",
        help="Governance profile to apply",
    )
    parser.add_argument("--adaptive-profile-dir", help="Optional Forager/AoE profile dir with adaptive wiki state")
    parser.add_argument(
        "--current-stale-days",
        type=int,
        default=0,
        help="Allowed day gap before current-state surface is considered older than governance surfaces",
    )
    parser.add_argument("--large-log-lines", type=int, default=1000, help="Line threshold for large-log warnings")
    parser.add_argument("--json-out", help="Write JSON report to this path")
    parser.add_argument("--md-out", help="Write markdown report to this path")
    args = parser.parse_args()

    result = run_audit(args)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.md_out:
        out = Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(build_markdown_report(result), encoding="utf-8")
    if not args.json_out and not args.md_out:
        print(json.dumps(result, indent=2))

    return 1 if any(item["severity"] == "error" for item in result["findings"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
