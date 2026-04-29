#!/usr/bin/env python3
"""Extract repeated recovery patterns into learned runbook candidates."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aoe_tg_runtime_core import action_audit_path, recovery_summary_dir, resolve_project_root, resolve_team_dir


DEFAULT_OUTPUT_NAME = "learned-recovery-runbook.md"
BENIGN_REASON_CODES = {
    "-",
    "applied",
    "completed",
    "dispatch_completed",
    "external_pickup_acknowledged",
    "external_result_completed",
    "noop",
    "ok",
    "ready",
    "started",
    "stopped",
}
BLOCKER_STATUS_TOKENS = {"blocked", "failed", "failure", "warn", "warning", "pending"}
BLOCKER_REASON_MARKERS = (
    "blocked",
    "exhausted",
    "failed",
    "missing",
    "not_ready",
    "required",
)


@dataclass(frozen=True)
class LearnedRunbookObservation:
    source_type: str
    source_path: str
    at: str
    headline: str
    outcome_kind: str
    outcome_status: str
    reason_code: str
    remediation: str
    next_step: str
    source_command: str


@dataclass(frozen=True)
class LearnedRunbookCandidate:
    reason_code: str
    remediation: str
    next_step: str
    occurrence_count: int
    outcome_kinds: List[str]
    source_types: List[str]
    examples: List[LearnedRunbookObservation]


@dataclass(frozen=True)
class LearnedRunbookReport:
    project_root: str
    team_dir: str
    min_count: int
    observation_count: int
    candidate_count: int
    candidates: List[LearnedRunbookCandidate]


def _clean_text(value: Any, fallback: str = "-") -> str:
    text = " ".join(str(value or "").strip().split())
    return text or fallback


def _load_json_file(path: Path) -> Dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _load_jsonl_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except Exception:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _observation_from_row(*, row: Dict[str, Any], source_type: str, source_path: Path) -> Optional[LearnedRunbookObservation]:
    reason_code = _clean_text(row.get("outcome_reason_code") or row.get("reason_code"))
    remediation = _clean_text(row.get("remediation"))
    next_step = _clean_text(row.get("next_step"))
    if remediation == "-" and next_step == "-":
        return None
    return LearnedRunbookObservation(
        source_type=source_type,
        source_path=str(source_path),
        at=_clean_text(row.get("at") or row.get("generated_at")),
        headline=_clean_text(row.get("headline") or row.get("title")),
        outcome_kind=_clean_text(row.get("outcome_kind")),
        outcome_status=_clean_text(row.get("outcome_status") or row.get("status")),
        reason_code=reason_code,
        remediation=remediation,
        next_step=next_step,
        source_command=_clean_text(row.get("source_command")),
    )


def _nightly_summary_observations(path: Path) -> List[LearnedRunbookObservation]:
    payload = _load_json_file(path)
    rows = payload.get("recent_action_audit") if isinstance(payload.get("recent_action_audit"), list) else []
    observations: List[LearnedRunbookObservation] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        obs = _observation_from_row(row=raw, source_type="nightly_summary", source_path=path)
        if obs is not None:
            observations.append(obs)
    return observations


def collect_learned_runbook_observations(*, team_dir: Path | str) -> List[LearnedRunbookObservation]:
    resolved_team_dir = Path(team_dir).expanduser().resolve()
    observations: List[LearnedRunbookObservation] = []

    audit_path = action_audit_path(resolved_team_dir)
    for row in _load_jsonl_rows(audit_path):
        obs = _observation_from_row(row=row, source_type="action_audit", source_path=audit_path)
        if obs is not None:
            observations.append(obs)

    summary_root = recovery_summary_dir(resolved_team_dir)
    for path in sorted(summary_root.glob("*.json")) if summary_root.exists() else []:
        observations.extend(_nightly_summary_observations(path))

    deduped: Dict[Tuple[str, str, str, str, str, str], LearnedRunbookObservation] = {}
    for obs in observations:
        key = (
            obs.at,
            obs.headline,
            obs.outcome_kind,
            obs.reason_code,
            obs.remediation,
            obs.next_step,
        )
        deduped.setdefault(key, obs)
    return list(deduped.values())


def _is_learning_candidate(observations: Sequence[LearnedRunbookObservation]) -> bool:
    if not observations:
        return False
    first = observations[0]
    reason = first.reason_code.strip().lower()
    if reason in BENIGN_REASON_CODES:
        return False
    if first.remediation.strip() in {"", "-"}:
        return False
    statuses = {obs.outcome_status.strip().lower() for obs in observations}
    if statuses & BLOCKER_STATUS_TOKENS:
        return True
    return any(marker in reason for marker in BLOCKER_REASON_MARKERS)


def build_learned_runbook_report(
    *,
    project_root: Path | str,
    team_dir: Optional[str] = None,
    min_count: int = 2,
) -> LearnedRunbookReport:
    root = resolve_project_root(str(project_root))
    resolved_team_dir = resolve_team_dir(root, team_dir)
    observations = collect_learned_runbook_observations(team_dir=resolved_team_dir)
    groups: Dict[Tuple[str, str, str], List[LearnedRunbookObservation]] = defaultdict(list)
    for obs in observations:
        groups[(obs.reason_code, obs.remediation, obs.next_step)].append(obs)

    candidates: List[LearnedRunbookCandidate] = []
    threshold = max(1, int(min_count or 1))
    for (reason_code, remediation, next_step), rows in groups.items():
        if len(rows) < threshold:
            continue
        ordered = sorted(rows, key=lambda row: (row.at, row.headline, row.source_path))
        if not _is_learning_candidate(ordered):
            continue
        outcome_kinds = sorted({row.outcome_kind for row in ordered if row.outcome_kind not in {"", "-"}})
        source_types = sorted({row.source_type for row in ordered if row.source_type not in {"", "-"}})
        candidates.append(
            LearnedRunbookCandidate(
                reason_code=reason_code,
                remediation=remediation,
                next_step=next_step,
                occurrence_count=len(ordered),
                outcome_kinds=outcome_kinds,
                source_types=source_types,
                examples=ordered[:5],
            )
        )

    candidates.sort(key=lambda row: (-row.occurrence_count, row.reason_code, row.next_step))
    return LearnedRunbookReport(
        project_root=str(root),
        team_dir=str(resolved_team_dir),
        min_count=threshold,
        observation_count=len(observations),
        candidate_count=len(candidates),
        candidates=candidates,
    )


def render_learned_runbook(report: LearnedRunbookReport) -> str:
    lines: List[str] = [
        "# Learned Recovery Runbook",
        "",
        f"- project_root: {report.project_root}",
        f"- team_dir: {report.team_dir}",
        f"- min_count: {report.min_count}",
        f"- observations: {report.observation_count}",
        f"- candidates: {report.candidate_count}",
        "",
    ]
    if not report.candidates:
        lines.append("No learned runbook candidates met the threshold.")
        return "\n".join(lines).strip() + "\n"

    for idx, candidate in enumerate(report.candidates, start=1):
        lines.extend(
            [
                f"## {idx}. {candidate.reason_code}",
                f"- occurrence_count: {candidate.occurrence_count}",
                f"- outcome_kinds: {', '.join(candidate.outcome_kinds) if candidate.outcome_kinds else '-'}",
                f"- source_types: {', '.join(candidate.source_types) if candidate.source_types else '-'}",
                f"- next_step: {candidate.next_step}",
                f"- remediation: {candidate.remediation}",
                "- evidence:",
            ]
        )
        for obs in candidate.examples:
            lines.append(
                "  - {at} | {headline} | status={status} | source={source_type}".format(
                    at=obs.at,
                    headline=obs.headline,
                    status=obs.outcome_status,
                    source_type=obs.source_type,
                )
            )
            if obs.source_command != "-":
                lines.append(f"    command: {obs.source_command}")
            lines.append(f"    file: {obs.source_path}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def learned_runbook_report_to_dict(report: LearnedRunbookReport) -> Dict[str, Any]:
    return {
        **{key: value for key, value in asdict(report).items() if key != "candidates"},
        "candidates": [
            {
                **{key: value for key, value in asdict(candidate).items() if key != "examples"},
                "examples": [asdict(obs) for obs in candidate.examples],
            }
            for candidate in report.candidates
        ],
    }


def write_learned_runbook(*, report: LearnedRunbookReport, output_path: Path | str) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_learned_runbook(report), encoding="utf-8")
    return path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract repeated recovery patterns into learned runbook candidates")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--team-dir")
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--output")
    parser.add_argument("--write-doc", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = resolve_project_root(str(args.project_root))
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (root / "docs" / "runbooks" / DEFAULT_OUTPUT_NAME).resolve()
    )
    report = build_learned_runbook_report(
        project_root=root,
        team_dir=args.team_dir,
        min_count=args.min_count,
    )
    if args.write_doc:
        write_learned_runbook(report=report, output_path=output_path)
    if args.json:
        payload = learned_runbook_report_to_dict(report)
        if args.write_doc:
            payload["output_path"] = str(output_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if args.write_doc:
            print(f"wrote learned runbook: {output_path}")
        print(render_learned_runbook(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
