#!/usr/bin/env python3
"""Build an operator-safe Telegram request for a morning Ondesk handoff."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
from typing import Any

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import offdesk_decision_records as decision_records


DEFAULT_PROFILE = os.environ.get("OFFDESK_PROFILE", "twinpaper-adaptive-debug")
DEFAULT_WEBUI_URL = os.environ.get("FORAGER_WEBUI_URL", "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--project-key", default="twinpaper")
    parser.add_argument("--subject", default="TwinPaper")
    parser.add_argument("--closeout-artifact-dir", type=pathlib.Path)
    parser.add_argument("--prompt-package", type=pathlib.Path)
    parser.add_argument("--webui-url", default=DEFAULT_WEBUI_URL)
    parser.add_argument("--handoff-local-time", default="08:30")
    parser.add_argument("--timezone", default="Asia/Seoul")
    parser.add_argument("--now", help="Deterministic ISO timestamp for tests.")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    return parser.parse_args()


def utc_now(now: str | None = None) -> dt.datetime:
    if now:
        parsed = dt.datetime.fromisoformat(now)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def profile_dir(profile: str) -> pathlib.Path:
    home = pathlib.Path.home()
    config_base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
    primary = config_base / "forager"
    legacy_candidates = [
        config_base / "agent-of-empires",
        home / ".agent-of-empires",
    ]
    base = primary if primary.exists() else next((path for path in legacy_candidates if path.exists()), primary)
    return base / "profiles" / profile


def load_json_object(path: pathlib.Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def latest_closeout_dir(profile: str, project_key: str) -> pathlib.Path | None:
    root = profile_dir(profile) / "offdesk_closeouts"
    if not root.exists():
        return None
    candidates: list[tuple[float, pathlib.Path]] = []
    for plan_path in root.glob("*/closeout_plan.json"):
        plan = load_json_object(plan_path)
        if plan.get("project_key") not in (None, project_key):
            continue
        candidates.append((plan_path.stat().st_mtime, plan_path.parent))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def latest_closeout_review_projection(closeout_dir: pathlib.Path | None) -> dict[str, Any]:
    if closeout_dir is None or not closeout_dir.exists():
        return {}
    candidates = sorted(closeout_dir.glob("closeout_review_*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        return {}
    review_path = candidates[-1]
    review = load_json_object(review_path)
    verdict = str(review.get("verdict") or "").strip()
    receipt = review.get("closeout_receipt") if isinstance(review.get("closeout_receipt"), dict) else {}
    receipt_status = str(receipt.get("acceptance_status") or "").strip()
    if not receipt_status and verdict == "approved_with_followups":
        receipt_status = "approved_with_followups"
    elif not receipt_status and verdict == "approved":
        receipt_status = "legacy_approved"
    elif not receipt_status and verdict in {"revise", "revision_required"}:
        receipt_status = "revision_required"
    elif not receipt_status and verdict == "blocked":
        receipt_status = "blocked"
    return {
        "verdict": verdict or None,
        "path": str(review_path),
        "receipt_status": receipt_status or None,
        "evidence_status": str(receipt.get("evidence_status") or "").strip() or None,
        "verification_status": str(receipt.get("verification_status") or "").strip() or None,
        "open_decisions": receipt.get("open_decisions") if isinstance(receipt.get("open_decisions"), list) else [],
        "missing_evidence": receipt.get("missing_evidence") if isinstance(receipt.get("missing_evidence"), list) else [],
        "next_safe_action": str(receipt.get("next_safe_action") or "").strip() or None,
    }


def human_closeout_receipt_status(status: str | None) -> str:
    labels = {
        "accepted": "accepted",
        "approved_with_followups": "approved with follow-ups",
        "revision_required": "revision required",
        "blocked": "blocked",
        "legacy_approved": "legacy approved without receipt",
    }
    return labels.get(str(status or "").strip(), str(status or "not reviewed").replace("_", " "))


def closeout_receipt_summary_line(review: dict[str, Any], open_count: int) -> str:
    status = str(review.get("receipt_status") or "").strip()
    if status == "accepted":
        return "Closeout receipt: accepted; WebUI에서 최종 근거만 확인하면 됩니다."
    if status == "approved_with_followups":
        return (
            "Closeout receipt: approved with follow-ups; accepted truth 아님. "
            f"남은 사용자 결정 {open_count}건은 WebUI에서 검토해야 합니다."
        )
    if status in {"revision_required", "blocked"}:
        label = human_closeout_receipt_status(status)
        return f"Closeout receipt: {label}; 다음 실행 전 수정/차단 사유를 검토해야 합니다."
    if status == "legacy_approved":
        return (
            "Closeout review: legacy approved without receipt; WebUI에서 근거를 재확인해야 합니다. "
            f"남은 사용자 결정 {open_count}건."
        )
    if review.get("verdict"):
        return f"Closeout review verdict: {review['verdict']}; WebUI에서 receipt 상태를 확인해야 합니다."
    return (
        f"남은 사용자 결정 {open_count}건은 WebUI에서 검토해야 합니다."
        if open_count
        else "현재 handoff 기준으로 열린 closeout 결정은 없습니다."
    )


def closeout_receipt_evidence_lines(review: dict[str, Any]) -> list[str]:
    status = str(review.get("receipt_status") or "").strip()
    if not status:
        return ["Closeout receipt가 아직 없어 accepted truth로 볼 수 없습니다."]
    status_line = f"Closeout receipt: {human_closeout_receipt_status(status)}."
    if status == "approved_with_followups":
        status_line = "Closeout receipt: approved with follow-ups; not accepted truth."
    lines = [status_line]
    evidence_status = review.get("evidence_status")
    verification_status = review.get("verification_status")
    status_parts = []
    if evidence_status:
        status_parts.append(f"evidence {evidence_status}")
    if verification_status:
        status_parts.append(f"verification {verification_status}")
    if status_parts:
        lines.append("Receipt status: " + ", ".join(status_parts) + ".")
    open_decisions = review.get("open_decisions") if isinstance(review.get("open_decisions"), list) else []
    missing_evidence = review.get("missing_evidence") if isinstance(review.get("missing_evidence"), list) else []
    if open_decisions or missing_evidence:
        lines.append(
            f"Receipt follow-ups: open decisions {len(open_decisions)}건, missing evidence {len(missing_evidence)}건."
        )
    next_safe_action = review.get("next_safe_action")
    if next_safe_action:
        lines.append(f"Receipt next safe action: {next_safe_action}")
    return lines


def human_decision_kind(kind: Any) -> str:
    labels = {
        "missing_artifact": "누락 아티팩트",
        "archive_review": "아카이브 검토",
        "git_state_review": "git 상태 검토",
        "commercial_review": "상업적 검토",
        "human_approval": "사용자 승인",
    }
    raw = str(kind or "").strip()
    return labels.get(raw, raw.replace("_", " "))


def open_decision_lines(open_decisions: Any, *, limit: int = 4) -> list[str]:
    if not isinstance(open_decisions, list):
        return []
    lines: list[str] = []
    for decision in open_decisions[:limit]:
        if not isinstance(decision, dict):
            continue
        kind = human_decision_kind(decision.get("kind"))
        detail = str(decision.get("detail") or "").strip()
        lines.append(f"{kind}: {detail}" if detail else kind)
    if len(open_decisions) > limit:
        lines.append(f"외 {len(open_decisions) - limit}개 결정은 WebUI 상세에서 확인")
    return lines


def summary_line_for_closeout(plan: dict[str, Any]) -> str:
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    completed = summary.get("completed_tasks")
    scanned = summary.get("tasks_scanned")
    missing = summary.get("missing_artifacts")
    archive = summary.get("archive_candidates")
    bits: list[str] = []
    if completed is not None and scanned is not None:
        bits.append(f"task {completed}/{scanned} 완료")
    if missing:
        bits.append(f"누락 아티팩트 {missing}건")
    if archive:
        bits.append(f"archive 검토 {archive}건")
    return "Closeout 요약: " + ", ".join(bits) + "." if bits else "Closeout 요약을 WebUI에서 확인해야 합니다."


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = utc_now(args.now)
    closeout_dir = args.closeout_artifact_dir or latest_closeout_dir(args.profile, args.project_key)
    plan_path = closeout_dir / "closeout_plan.json" if closeout_dir else None
    plan = load_json_object(plan_path)
    closeout_review = latest_closeout_review_projection(closeout_dir)
    review_verdict = closeout_review.get("verdict")
    review_record_path = closeout_review.get("path")
    receipt_status = str(closeout_review.get("receipt_status") or "").strip()
    open_decisions = plan.get("open_decisions") if isinstance(plan, dict) else []
    open_count = len(open_decisions) if isinstance(open_decisions, list) else 0
    closeout_id = str(plan.get("closeout_id") or (closeout_dir.name if closeout_dir else "none")).strip()

    scheduled_label = f"{args.handoff_local_time} {args.timezone}"
    summary_lines = [
        f"{scheduled_label} 기준 Ondesk 전환 브리핑입니다.",
        summary_line_for_closeout(plan),
        closeout_receipt_summary_line(closeout_review, open_count),
    ]

    evidence = [
        *open_decision_lines(open_decisions),
        *closeout_receipt_evidence_lines(closeout_review),
        "prompt package가 있으면 다음 harness는 이 패키지에서 시작합니다."
        if args.prompt_package
        else "prompt package 경로가 지정되지 않았습니다.",
        "Telegram 응답은 아침 검토 진입 여부만 기록합니다.",
    ]
    next_action = [
        "WebUI에서 RETURN_PACKAGE와 closeout receipt를 먼저 확인합니다.",
        "wiki projection/review 상태를 확인한 뒤 필요한 mutation만 별도 승인합니다.",
        "검토하지 못하면 pending ondesk review 상태로 유지합니다.",
    ]
    review_entry_detail = (
        "WebUI에서 closeout receipt follow-ups, RETURN_PACKAGE, wiki 상태를 함께 열어 아침 검토를 시작합니다."
        if receipt_status == "approved_with_followups"
        else "WebUI에서 closeout, RETURN_PACKAGE, wiki 상태를 함께 열어 아침 검토를 시작합니다."
    )
    next_safe_actions = [
        {
            "kind": "ondesk_review_entry",
            "detail": review_entry_detail,
            "scope": "operator_next_step",
            "commands": ["Open the WebUI handoff link"],
            "requires_operator_review": True,
            "does_not_authorize": [
                "cleanup, deletion, file movement, wiki promotion, provider retargeting, or accepting Offdesk output without separate review"
            ],
        },
        {
            "kind": "keep_pending",
            "detail": "지금 검토하지 못하면 자동 전환하지 않고 pending ondesk review 상태로 남깁니다.",
            "scope": "operator_next_step",
            "commands": ["Reply with keep_pending or defer_ondesk"],
            "requires_operator_review": False,
            "does_not_authorize": [
                "mutation, cleanup, deletion, provider changes, or wiki promotion"
            ],
        },
    ]

    decision_request_id = f"ondesk-handoff-{args.project_key}-{generated_at.strftime('%Y%m%dT%H%M%SZ')}"
    approval_brief = {
        "schema": "ondesk_handoff_brief.v1",
        "source": "build_ondesk_handoff_request",
        "recommendation": "start_ondesk_review",
        "subject": args.subject,
        "summary_lines": summary_lines,
        "why_recommendation": [
            "Telegram은 push 알림에 적합하지만 전체 검수 화면으로는 좁습니다.",
            "Ondesk 전환은 closeout, wiki, git 상태, prompt package를 함께 봐야 합니다.",
            "무응답이면 자동 전환하지 않고 pending 상태를 유지하는 편이 안전합니다.",
        ],
        "evidence": evidence,
        "next_action": next_action,
        "next_safe_actions": next_safe_actions,
        "decision_impacts": {
            "start_ondesk_review": "WebUI에서 상세 검토를 시작합니다. 이 선택만으로 mutation은 승인되지 않습니다.",
            "keep_pending": "자동 전환하지 않고 pending ondesk review로 남깁니다.",
            "defer_ondesk": "자연어로 남긴 시간이나 조건까지 검토를 미룹니다.",
        },
        "options": [dict(option) for option in DEFAULT_OPTIONS],
        "reply_examples": {
            "defer_ondesk": "30분 뒤 다시 알려줘.",
        },
        "scope": (
            "Telegram은 ondesk 검토 진입/대기만 기록합니다. wiki promotion, delete, cleanup, "
            "provider 변경, 파일 이동은 WebUI/CLI에서 별도 승인합니다."
        ),
        "question": "WebUI에서 ondesk 검토를 시작할까요?",
    }
    trace_refs = [
        ref
        for ref in (
            decision_records.trace_ref("artifact", "closeout_plan", plan_path),
            decision_records.trace_ref("artifact", "closeout_dir", closeout_dir),
            decision_records.trace_ref("artifact", "closeout_review_record", review_record_path),
            decision_records.trace_ref("artifact", "prompt_package", args.prompt_package),
        )
        if ref is not None
    ]
    decision_record = decision_records.build_decision_record(
        decision_id=decision_records.stable_decision_id(
            decision_request_id,
            args.project_key,
            closeout_id,
            generated_at.isoformat(),
            prefix="decision",
        ),
        project_key=args.project_key,
        request_id=decision_request_id,
        task_id=closeout_id or "ondesk-handoff",
        raised_by="closeout",
        source_surface="ondesk.handoff",
        materiality="high" if open_count else "medium",
        status="user_pending",
        decision_kind="ondesk_handoff_entry",
        summary=" ".join(summary_lines),
        decision_needed="Choose whether to start WebUI review, keep the handoff pending, or defer.",
        why_now=summary_lines[:3],
        current_scope="Ondesk review entry only.",
        non_authorized_scope=[
            "cleanup",
            "delete",
            "file movement",
            "wiki promotion",
            "provider retargeting",
            "accepting Offdesk output without separate review",
        ],
        options=[dict(option) for option in DEFAULT_OPTIONS],
        evidence_refs=trace_refs,
        route_target="user",
        route_reason="Morning handoff requires operator choice before returning to Ondesk review.",
        route_policy_basis=[
            "Telegram records only review entry intent.",
            "WebUI remains the detailed review surface.",
            "Mutation approval remains separate.",
        ],
        default_if_no_reply="defer_ondesk",
        approval_brief=approval_brief,
        trace_refs=trace_refs,
        created_at=generated_at.isoformat(),
        updated_at=generated_at.isoformat(),
    )

    request: dict[str, Any] = {
        "decision_request_id": decision_request_id,
        "message_type": "ondesk_handoff",
        "title": f"{args.subject} ondesk handoff",
        "project_key": args.project_key,
        "created_at": generated_at.isoformat(),
        "decision_record": decision_record,
        "approval_brief": approval_brief,
        "artifacts": {
            "closeout_id": closeout_id,
            "closeout_plan": str(plan_path) if plan_path else None,
            "closeout_dir": str(closeout_dir) if closeout_dir else None,
            "closeout_review_record": review_record_path,
            "prompt_package": str(args.prompt_package) if args.prompt_package else None,
        },
        "summary": {
            "project_key": args.project_key,
            "open_decisions": open_count,
            "handoff_local_time": args.handoff_local_time,
            "timezone": args.timezone,
            "closeout_review_verdict": review_verdict,
            "closeout_receipt_status": receipt_status or None,
            "closeout_receipt_evidence_status": closeout_review.get("evidence_status"),
            "closeout_receipt_verification_status": closeout_review.get("verification_status"),
            "closeout_receipt_open_decisions": len(closeout_review.get("open_decisions") or []),
            "closeout_receipt_missing_evidence": len(closeout_review.get("missing_evidence") or []),
        },
        "next_safe_actions": next_safe_actions,
    }
    if args.webui_url:
        request["links"] = [{"label": "WebUI 열기", "url": args.webui_url}]
    return request


DEFAULT_OPTIONS = (
    {
        "id": "start_ondesk_review",
        "label": "WebUI 검토 시작",
        "description": "WebUI에서 closeout, wiki, prompt package를 열어 아침 검토를 시작합니다.",
    },
    {
        "id": "keep_pending",
        "label": "대기 유지",
        "description": "자동 전환하지 않고 pending ondesk review 상태로 유지합니다.",
    },
    {
        "id": "defer_ondesk",
        "label": "나중에",
        "description": "검토를 미루고 재알림 조건을 자연어로 남깁니다.",
        "natural_input_prompt": "언제 다시 볼지 자연어로 답장하세요. 예: 30분 뒤 다시 알려줘.",
    },
)


def main() -> int:
    args = parse_args()
    request = build_request(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "out": str(args.out), "message_type": "ondesk_handoff"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
