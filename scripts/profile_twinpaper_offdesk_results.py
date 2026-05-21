#!/usr/bin/env python3
"""Profile completed TwinPaper Offdesk autonomy result artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import pathlib
import statistics
from typing import Any


DEFAULT_PROFILE = "twinpaper-adaptive-debug"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--result", action="append", type=pathlib.Path)
    parser.add_argument("--glob", dest="glob_pattern")
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--markdown-out", type=pathlib.Path)
    return parser.parse_args()


def default_glob(profile: str) -> str:
    return str(
        pathlib.Path.home()
        / ".config"
        / "agent-of-empires"
        / "profiles"
        / profile
        / "offdesk_workloads"
        / "twinpaper_autonomy"
        / "*"
        / "result.json"
    )


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.fmean(values), 2)


def safe_median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 2)


def iso_to_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def kst(value: str | None) -> str | None:
    parsed = iso_to_dt(value)
    if parsed is None:
        return value
    return parsed.astimezone(dt.timezone(dt.timedelta(hours=9))).isoformat()


def profile_result(result_path: pathlib.Path) -> dict[str, Any]:
    result = load_json(result_path)
    summary = result.get("summary", {})
    records = result.get("records", [])
    review_path = result_path.parent / "result_review" / "results.json"
    review = load_json(review_path) if review_path.exists() else None
    findings = review.get("findings", []) if isinstance(review, dict) else []
    finding_by_iteration: dict[int, list[dict[str, Any]]] = {}
    for finding in findings:
        iteration = finding.get("iteration")
        if isinstance(iteration, int):
            finding_by_iteration.setdefault(iteration, []).append(finding)

    per_case: dict[str, dict[str, Any]] = {}
    active_elapsed = 0.0
    for record in records:
        case = record.get("case") or "unknown"
        bucket = per_case.setdefault(
            case,
            {
                "case": case,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "elapsed_sec": [],
                "response_chars": [],
                "warning_count": 0,
                "failure_categories": {},
            },
        )
        elapsed = float(record.get("elapsed_sec") or 0.0)
        chars = int(record.get("response_chars") or 0)
        active_elapsed += elapsed
        bucket["total"] += 1
        bucket["passed"] += 1 if record.get("passed") is True else 0
        bucket["failed"] += 0 if record.get("passed") is True else 1
        bucket["elapsed_sec"].append(elapsed)
        bucket["response_chars"].append(chars)
        bucket["warning_count"] += len(finding_by_iteration.get(record.get("iteration"), []))
        category = record.get("failure_category") or "unknown"
        bucket["failure_categories"][category] = bucket["failure_categories"].get(category, 0) + 1

    per_case_rows = []
    for case, bucket in sorted(per_case.items()):
        elapsed_values = bucket.pop("elapsed_sec")
        char_values = bucket.pop("response_chars")
        per_case_rows.append(
            {
                **bucket,
                "avg_elapsed_sec": safe_mean(elapsed_values),
                "median_elapsed_sec": safe_median(elapsed_values),
                "min_elapsed_sec": round(min(elapsed_values), 2) if elapsed_values else None,
                "max_elapsed_sec": round(max(elapsed_values), 2) if elapsed_values else None,
                "avg_response_chars": safe_mean([float(value) for value in char_values]),
                "max_response_chars": max(char_values) if char_values else None,
            }
        )

    duration = float(summary.get("duration_sec") or 0.0)
    idle_sec = max(0.0, duration - active_elapsed)
    active_ratio = active_elapsed / duration if duration > 0 else None

    return {
        "result_path": str(result_path),
        "run_id": summary.get("task_id") or result_path.parent.name,
        "created_at": summary.get("created_at"),
        "created_at_kst": kst(summary.get("created_at")),
        "completed_at": summary.get("completed_at"),
        "completed_at_kst": kst(summary.get("completed_at")),
        "model": summary.get("model"),
        "duration_sec": round(duration, 2),
        "active_model_elapsed_sec": round(active_elapsed, 2),
        "scheduled_idle_sec": round(idle_sec, 2),
        "active_ratio": round(active_ratio, 4) if active_ratio is not None else None,
        "total": summary.get("total", len(records)),
        "passed": summary.get("passed", sum(1 for record in records if record.get("passed") is True)),
        "failed": summary.get("failed", sum(1 for record in records if record.get("passed") is not True)),
        "overall_verdict": summary.get("assessment", {}).get("overall_verdict"),
        "operator_risk": summary.get("assessment", {}).get("operator_risk"),
        "evidence_review_decision": summary.get("evidence_review_decision"),
        "baseline_evidence_status": summary.get("baseline_evidence_status"),
        "claim_status": summary.get("claim_status"),
        "per_case": per_case_rows,
        "review": {
            "present": review is not None,
            "decision": review.get("decision") if isinstance(review, dict) else None,
            "passed": review.get("passed") if isinstance(review, dict) else None,
            "severity_counts": review.get("summary", {}).get("severity_counts", {})
            if isinstance(review, dict)
            else {},
            "category_counts": review.get("summary", {}).get("category_counts", {})
            if isinstance(review, dict)
            else {},
            "findings": findings,
            "learning_candidates": review.get("learning_candidates", [])
            if isinstance(review, dict)
            else [],
        },
    }


def collect_results(args: argparse.Namespace) -> list[pathlib.Path]:
    paths = [path for path in args.result or []]
    if not paths:
        pattern = args.glob_pattern or default_glob(args.profile)
        paths = [pathlib.Path(path) for path in glob.glob(pattern)]
    paths = sorted({path.resolve() for path in paths if path.exists()})
    if args.latest_only and paths:
        paths = [paths[-1]]
    return paths


def summarize_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    failure_by_case: dict[str, int] = {}
    warning_by_case: dict[str, int] = {}
    for run in runs:
        for case in run["per_case"]:
            if case["failed"]:
                failure_by_case[case["case"]] = failure_by_case.get(case["case"], 0) + case["failed"]
            if case["warning_count"]:
                warning_by_case[case["case"]] = warning_by_case.get(case["case"], 0) + case["warning_count"]
    latest = runs[-1] if runs else None
    return {
        "run_count": len(runs),
        "latest_run_id": latest.get("run_id") if latest else None,
        "total_cases": sum(int(run.get("total") or 0) for run in runs),
        "passed_cases": sum(int(run.get("passed") or 0) for run in runs),
        "failed_cases": sum(int(run.get("failed") or 0) for run in runs),
        "failure_by_case": dict(sorted(failure_by_case.items())),
        "warning_by_case": dict(sorted(warning_by_case.items())),
        "latest_active_ratio": latest.get("active_ratio") if latest else None,
        "latest_review_decision": latest.get("review", {}).get("decision") if latest else None,
    }


def markdown_report(profile: dict[str, Any]) -> str:
    summary = profile["summary"]
    latest = profile["runs"][-1] if profile["runs"] else None
    lines = [
        "# TwinPaper Offdesk Result Profile",
        "",
        "## Summary",
        "",
        f"- runs_profiled: `{summary['run_count']}`",
        f"- total_cases: `{summary['total_cases']}`",
        f"- passed_cases: `{summary['passed_cases']}`",
        f"- failed_cases: `{summary['failed_cases']}`",
        f"- latest_run_id: `{summary['latest_run_id']}`",
        f"- latest_review_decision: `{summary['latest_review_decision']}`",
        "",
    ]
    if latest:
        lines.extend(
            [
                "## Latest Run",
                "",
                f"- created_at_kst: `{latest['created_at_kst']}`",
                f"- completed_at_kst: `{latest['completed_at_kst']}`",
                f"- model: `{latest['model']}`",
                f"- duration_sec: `{latest['duration_sec']}`",
                f"- active_model_elapsed_sec: `{latest['active_model_elapsed_sec']}`",
                f"- scheduled_idle_sec: `{latest['scheduled_idle_sec']}`",
                f"- active_ratio: `{latest['active_ratio']}`",
                f"- pass: `{latest['passed']}/{latest['total']}`",
                f"- verdict: `{latest['overall_verdict']}`",
                f"- operator_risk: `{latest['operator_risk']}`",
                f"- claim_status: `{latest['claim_status']}`",
                "",
                "## Latest Case Profile",
                "",
                "| case | pass | warnings | avg sec | max sec | avg chars |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for case in latest["per_case"]:
            lines.append(
                f"| `{case['case']}` | `{case['passed']}/{case['total']}` | "
                f"`{case['warning_count']}` | `{case['avg_elapsed_sec']}` | "
                f"`{case['max_elapsed_sec']}` | `{case['avg_response_chars']}` |"
            )
        lines.extend(["", "## Review Findings", ""])
        findings = latest["review"]["findings"]
        if findings:
            for finding in findings:
                lines.append(
                    "- "
                    f"`{finding.get('severity')}` / `{finding.get('category')}` "
                    f"iteration `{finding.get('iteration')}` `{finding.get('case')}`: "
                    f"{finding.get('message')}"
                )
        else:
            lines.append("- No review findings.")
        lines.append("")
    lines.extend(
        [
            "## Historical Recurrence",
            "",
            f"- failure_by_case: `{summary['failure_by_case']}`",
            f"- warning_by_case: `{summary['warning_by_case']}`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    result_paths = collect_results(args)
    if not result_paths:
        raise SystemExit("no result.json artifacts found")
    runs = [profile_result(path) for path in result_paths]
    profile = {
        "kind": "twinpaper_offdesk_result_profile",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile": args.profile,
        "summary": summarize_runs(runs),
        "runs": runs,
    }
    if args.out:
        write_json(args.out, profile)
    if args.markdown_out:
        write_text(args.markdown_out, markdown_report(profile))
    if not args.out and not args.markdown_out:
        print(json.dumps(profile, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
