"""Plan-session stage message renderers for the Telegram operator.

Pure functions that turn plan-session and stage-receipt dicts into the
operator-facing mobile-card text. No state, no I/O, no subprocess calls.
"""

from __future__ import annotations

import html
from typing import Any

from .project_candidates import display_project_readiness, display_project_risk, truncate_label
from .rendering import MOBILE_CARD_MAX_LINES, title_with_profile


def render_project_selection_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidates = [
        candidate
        for candidate in session.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    action_readiness_value = (
        session.get("action_readiness") if isinstance(session.get("action_readiness"), dict) else {}
    )
    build_plan_readiness = (
        action_readiness_value.get("build_plan")
        if isinstance(action_readiness_value.get("build_plan"), dict)
        else {}
    )
    build_plan_blocked = str(build_plan_readiness.get("status") or "") == "blocked"
    lines = [title_with_profile("계획 대상 선택", profile)]
    if not candidates:
        lines.append("후보 프로젝트를 찾지 못했습니다.")
        lines.append("다음 조치: /select 프로젝트명")
        lines.append("아직 실행은 시작하지 않았습니다.")
        return "\n".join(lines)
    if build_plan_blocked:
        lines.append("부분 장애: 로컬 에이전트 연결 실패")
        lines.append("막힘: 새 계획/야간주행 시작")
        shown_candidates = candidates[:1]
    else:
        lines.append("다음 조치: 번호/이름 또는 /select")
        shown_candidates = candidates[:3]
    for candidate in shown_candidates:
        name = truncate_label(candidate.get("display_name"), max_chars=22)
        lines.append(
            f"{candidate.get('rank')}. {html.escape(name)} · {display_project_readiness(candidate.get('readiness'))}"
        )
    if len(candidates) > len(shown_candidates) and len(lines) < MOBILE_CARD_MAX_LINES:
        lines.append(f"외 {len(candidates) - len(shown_candidates)}개는 다시 스캔")
    return "\n".join(lines[:MOBILE_CARD_MAX_LINES])


def render_project_selected_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "프로젝트", max_chars=24)
    readiness = display_project_readiness(candidate.get("readiness"))
    risk = display_project_risk(candidate.get("risk"))
    if candidate.get("manual_input") and not candidate.get("workspace_path"):
        return "\n".join(
            [
                title_with_profile("계획 대상 확인 필요", profile),
                f"{html.escape(name)} · 경로 미확인",
                "다음 조치: 실제 폴더명/경로 입력",
                "아직 실행은 시작하지 않았습니다.",
                "다시 선택 또는 보류 가능",
            ]
        )
    return "\n".join(
        [
            title_with_profile("계획 대상 선택됨", profile),
            f"{html.escape(name)} · {readiness} · 위험 {risk}",
            "다음 단계는 초기화 검토입니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_project_init_preview_message(*, profile: Any, session: dict[str, Any]) -> str:
    preview = session.get("project_init_preview") if isinstance(session.get("project_init_preview"), dict) else {}
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "프로젝트", max_chars=24)
    marker_count = len(preview.get("root_markers") if isinstance(preview.get("root_markers"), list) else [])
    doc_count = len(preview.get("documentation_sources") if isinstance(preview.get("documentation_sources"), list) else [])
    return "\n".join(
        [
            title_with_profile("초기화 검토 준비", profile),
            f"{html.escape(name)} · 문서 {doc_count} · 마커 {marker_count}",
            "초기화 검토 기록을 저장했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 초기화 명령 검토",
        ]
    )


def render_project_init_preview_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("초기화 검토 필요", profile),
            "먼저 초기화 검토가 필요합니다.",
            "초기화 검토를 먼저 선택하세요.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_project_init_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
    output = run.get("project_init_output") if isinstance(run.get("project_init_output"), dict) else {}
    summary = output.get("summary") if isinstance(output.get("summary"), dict) else {}
    name = truncate_label(output.get("project_key") or run.get("project_key") or "프로젝트", max_chars=24)
    module_count = int(summary.get("module_candidate_count") or 0)
    review_required = bool(output.get("requires_operator_review", True))
    return "\n".join(
        [
            title_with_profile("초기화 패킷 생성됨", profile),
            f"{html.escape(name)} · 모듈 {module_count} · 검토 {'필요' if review_required else '확인'}",
            "초기화 패킷을 저장했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "아래 버튼으로 계획 초안 생성",
        ]
    )


def render_project_init_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    run = session.get("project_init_run") if isinstance(session.get("project_init_run"), dict) else {}
    reason = truncate_label(run.get("error") or "생성 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("초기화 생성 실패", profile),
            html.escape(reason),
            "초기화 패킷을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_draft_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("초기화 패킷 필요", profile),
            "먼저 초기화 패킷을 생성하세요.",
            "계획 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_draft_validated_message(*, profile: Any, session: dict[str, Any]) -> str:
    draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
    output = draft.get("validation_output") if isinstance(draft.get("validation_output"), dict) else {}
    project_key = truncate_label(output.get("project_key") or draft.get("project_key") or "프로젝트", max_chars=24)
    ready = bool(output.get("ready_for_operator_review", True))
    return "\n".join(
        [
            title_with_profile("계획 초안 검증됨", profile),
            f"{html.escape(project_key)} · 검토 {'준비' if ready else '필요'}",
            "계획 초안을 저장했습니다.",
            "계획 등록/실행은 아직 하지 않았습니다.",
            "아래 버튼으로 계획 등록",
        ]
    )


def render_plan_draft_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    draft = session.get("plan_draft") if isinstance(session.get("plan_draft"), dict) else {}
    reason = truncate_label(draft.get("error") or "검증 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 초안 실패", profile),
            html.escape(reason),
            "계획 초안을 검증하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_registration_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 초안 필요", profile),
            "먼저 계획 초안을 생성하세요.",
            "계획 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_registration_stale_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 초안 변경됨", profile),
            "초안이 바뀌어 재검증이 필요합니다.",
            "계획 등록은 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "아래 버튼으로 초안 재생성",
        ]
    )


def render_plan_registered_message(*, profile: Any, session: dict[str, Any]) -> str:
    registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
    output = registration.get("registration_output") if isinstance(registration.get("registration_output"), dict) else {}
    project_key = truncate_label(output.get("project_key") or registration.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("계획 등록됨", profile),
            f"{html.escape(project_key)} · 검토 대기",
            "계획을 등록했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 계획 검토",
        ]
    )


def render_plan_review_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 등록 필요", profile),
            "먼저 계획을 등록하세요.",
            "계획 검토는 아직 기록하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_review_approved_message(*, profile: Any, session: dict[str, Any]) -> str:
    review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
    output = review.get("review_output") if isinstance(review.get("review_output"), dict) else {}
    project_key = truncate_label(review.get("project_key") or output.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("계획 승인됨", profile),
            f"{html.escape(project_key)} · 실행 준비 후보",
            "계획 검토를 기록했습니다.",
            "실행 준비는 아직 하지 않았습니다.",
            "아래 버튼으로 실행 준비 검토",
        ]
    )


def render_plan_launch_prep_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 승인 필요", profile),
            "먼저 계획 승인을 기록하세요.",
            "실행 준비 패킷은 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_launch_prep_prepared_message(*, profile: Any, session: dict[str, Any]) -> str:
    prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
    output = prep.get("launch_prep_output") if isinstance(prep.get("launch_prep_output"), dict) else {}
    project_key = truncate_label(prep.get("project_key") or output.get("project_key") or "프로젝트", max_chars=24)
    return "\n".join(
        [
            title_with_profile("실행 준비 패킷 생성됨", profile),
            f"{html.escape(project_key)} · 게이트 검토 대기",
            "패킷만 저장했습니다.",
            "실행/승인은 아직 하지 않았습니다.",
            "아래 버튼으로 게이트 요청",
        ]
    )


def render_plan_launch_prep_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    prep = session.get("plan_launch_prep") if isinstance(session.get("plan_launch_prep"), dict) else {}
    reason = truncate_label(prep.get("error") or "실행 준비 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 준비 실패", profile),
            html.escape(reason),
            "실행 준비 패킷을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_gate_request_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 준비 필요", profile),
            "먼저 실행 준비 패킷을 만드세요.",
            "게이트 요청은 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_gate_request_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
    output = gate_request.get("gate_output") if isinstance(gate_request.get("gate_output"), dict) else {}
    approval = output.get("approval") if isinstance(output.get("approval"), dict) else {}
    approval_id = truncate_label(approval.get("approval_id") or "approval", max_chars=28)
    return "\n".join(
        [
            title_with_profile("게이트 요청 생성됨", profile),
            f"{html.escape(approval_id)} · 승인 대기",
            "승인 대기열에 올렸습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 approval 확인",
        ]
    )


def render_plan_gate_request_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    gate_request = session.get("plan_gate_request") if isinstance(session.get("plan_gate_request"), dict) else {}
    reason = truncate_label(gate_request.get("error") or "게이트 요청 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("게이트 요청 실패", profile),
            html.escape(reason),
            "승인 대기열을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_gate_resolution_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("게이트 요청 필요", profile),
            "먼저 게이트 요청을 만드세요.",
            "승인/거절은 아직 기록하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_gate_resolution_done_message(*, profile: Any, session: dict[str, Any]) -> str:
    resolution = session.get("plan_gate_resolution") if isinstance(session.get("plan_gate_resolution"), dict) else {}
    decision = str(resolution.get("decision") or "")
    label = "승인됨" if decision == "approved" else "거절됨"
    approval_id = truncate_label(resolution.get("approval_id") or "approval", max_chars=28)
    return "\n".join(
        [
            title_with_profile(f"게이트 {label}", profile),
            f"{html.escape(approval_id)} · {label}",
            "approval만 해결했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 다음 단계 검토",
        ]
    )


def render_plan_gate_resolution_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    resolution = session.get("plan_gate_resolution") if isinstance(session.get("plan_gate_resolution"), dict) else {}
    reason = truncate_label(resolution.get("error") or "게이트 처리 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("게이트 처리 실패", profile),
            html.escape(reason),
            "approval을 해결하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 pending 상태 확인",
        ]
    )


def render_plan_execution_brief_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("게이트 승인 필요", profile),
            "먼저 게이트 승인을 완료하세요.",
            "실행 브리프는 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_execution_brief_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    brief = session.get("plan_execution_brief") if isinstance(session.get("plan_execution_brief"), dict) else {}
    task_id = truncate_label(brief.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("실행 브리프 생성됨", profile),
            f"{html.escape(task_id)} · 로컬 enqueue 준비",
            "브리프 파일만 저장했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 enqueue 검토",
        ]
    )


def render_plan_execution_brief_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    brief = session.get("plan_execution_brief") if isinstance(session.get("plan_execution_brief"), dict) else {}
    reason = truncate_label(brief.get("error") or "브리프 생성 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 브리프 실패", profile),
            html.escape(reason),
            "실행 브리프를 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_enqueue_handoff_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 브리프 필요", profile),
            "먼저 실행 브리프를 생성하세요.",
            "큐 등록 검토는 아직 없습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_enqueue_handoff_created_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = session.get("plan_enqueue_handoff") if isinstance(session.get("plan_enqueue_handoff"), dict) else {}
    task_id = truncate_label(handoff.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("큐 등록 검토 준비됨", profile),
            f"{html.escape(task_id)} · 로컬 검토 필요",
            "명령 템플릿만 저장했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 workload 명령 확인",
        ]
    )


def render_plan_enqueue_handoff_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = session.get("plan_enqueue_handoff") if isinstance(session.get("plan_enqueue_handoff"), dict) else {}
    reason = truncate_label(handoff.get("error") or "큐 등록 검토 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("큐 등록 검토 실패", profile),
            html.escape(reason),
            "명령 템플릿을 만들지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_workload_path_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 필요", profile),
            "prepared_task.json 경로를 입력하세요.",
            "검토된 패킷만 연결할 수 있습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 경로 직접 입력",
        ]
    )


def render_plan_workload_bound_message(*, profile: Any, session: dict[str, Any]) -> str:
    binding = session.get("plan_workload_binding") if isinstance(session.get("plan_workload_binding"), dict) else {}
    task_id = truncate_label(binding.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 연결됨", profile),
            f"{html.escape(task_id)} · 로컬 enqueue 검토 가능",
            "검토된 패킷만 연결했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 enqueue 실행 검토",
        ]
    )


def render_plan_workload_binding_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    binding = session.get("plan_workload_binding") if isinstance(session.get("plan_workload_binding"), dict) else {}
    reason = truncate_label(binding.get("error") or "패킷 연결 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("워크로드 패킷 실패", profile),
            html.escape(reason),
            "패킷을 연결하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 manifest 확인",
        ]
    )


def render_plan_enqueue_run_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("워크로드 연결 필요", profile),
            "먼저 prepared_task.json을 연결하세요.",
            "큐 등록은 아직 하지 않았습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 경로 직접 입력",
        ]
    )


def render_plan_enqueue_run_done_message(*, profile: Any, session: dict[str, Any]) -> str:
    enqueue_run = session.get("plan_enqueue_run") if isinstance(session.get("plan_enqueue_run"), dict) else {}
    task_id = truncate_label(enqueue_run.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("큐 등록됨", profile),
            f"{html.escape(task_id)} · queued",
            "Offdesk 큐에만 등록했습니다.",
            "실행은 아직 시작하지 않았습니다.",
            "로컬에서 다음 실행 검토",
        ]
    )


def render_plan_enqueue_run_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    enqueue_run = session.get("plan_enqueue_run") if isinstance(session.get("plan_enqueue_run"), dict) else {}
    reason = truncate_label(enqueue_run.get("error") or "큐 등록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("큐 등록 실패", profile),
            html.escape(reason),
            "Offdesk 큐에 등록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 queue 상태 확인",
        ]
    )


def render_plan_runtime_start_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("큐 등록 필요", profile),
            "먼저 큐 등록을 완료하세요.",
            "실행 시작은 아직 하지 않았습니다.",
            "완료 판정도 아직 없습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_runtime_started_message(*, profile: Any, session: dict[str, Any]) -> str:
    runtime_start = session.get("plan_runtime_start") if isinstance(session.get("plan_runtime_start"), dict) else {}
    task_id = truncate_label(runtime_start.get("task_id") or "task", max_chars=28)
    return "\n".join(
        [
            title_with_profile("실행 시작됨", profile),
            f"{html.escape(task_id)} · launched",
            "대상 task만 시작했습니다.",
            "완료 판정은 아직 없습니다.",
            "아래 버튼으로 상태 확인",
        ]
    )


def runtime_monitor_next_action_label(target_task: dict[str, Any]) -> str:
    next_action = target_task.get("next_safe_action") if isinstance(target_task.get("next_safe_action"), dict) else {}
    kind = str(next_action.get("kind") or "").strip()
    if kind in {"review_required", "closeout_check"}:
        return "로컬에서 closeout 검토"
    if kind in {"recovery_required", "resume_review_required", "result_artifact_missing"}:
        return "로컬에서 복구 검토"
    if kind == "runtime_monitoring":
        return "잠시 후 다시 확인"
    if kind in {"dispatch_pending", "approval_pending"}:
        return "로컬에서 큐/승인 확인"
    return "로컬에서 상태 확인"


def render_plan_runtime_monitor_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("실행 시작 필요", profile),
            "먼저 실행 시작을 완료하세요.",
            "상태 확인은 대상 task만 봅니다.",
            "완료 판정은 아직 없습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_plan_runtime_monitor_message(*, profile: Any, session: dict[str, Any]) -> str:
    monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
    target_task = monitor.get("target_task") if isinstance(monitor.get("target_task"), dict) else {}
    task_id = truncate_label(monitor.get("task_id") or target_task.get("task_id") or "task", max_chars=24)
    task_status = str(monitor.get("task_status") or target_task.get("status") or "unknown")
    status_labels = {
        "completed": ("실행 완료 확인", "완료 상태를 확인했습니다."),
        "failed": ("실행 실패 확인", "실패 상태입니다."),
        "resume_pending": ("복구 검토 필요", "복구 검토가 필요합니다."),
        "launched": ("실행 진행 중", "아직 실행 중입니다."),
        "running": ("실행 진행 중", "아직 실행 중입니다."),
        "queued": ("큐 대기 확인", "아직 큐에 있습니다."),
        "pending_approval": ("승인 대기 확인", "아직 승인 대기입니다."),
    }
    title, summary = status_labels.get(task_status, ("실행 상태 확인", "상태를 갱신했습니다."))
    return "\n".join(
        [
            title_with_profile(title, profile),
            f"{html.escape(task_id)} · {html.escape(task_status)}",
            summary,
            runtime_monitor_next_action_label(target_task),
            "결과 승인은 아직 없습니다.",
        ]
    )


def render_plan_runtime_monitor_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    monitor = session.get("plan_runtime_monitor") if isinstance(session.get("plan_runtime_monitor"), dict) else {}
    reason = truncate_label(monitor.get("error") or "상태 확인 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("상태 확인 실패", profile),
            html.escape(reason),
            "대상 task만 확인하려 했습니다.",
            "완료 판정은 아직 없습니다.",
            "로컬에서 상태 확인",
        ]
    )


def render_plan_closeout_required_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("완료 확인 필요", profile),
            "먼저 completed 상태를 확인하세요.",
            "마무리 패킷은 완료 task만 대상입니다.",
            "결과 승인은 아직 없습니다.",
            "아래 버튼으로 상태 확인",
        ]
    )


def render_plan_closeout_packet_message(*, profile: Any, session: dict[str, Any]) -> str:
    closeout_packet = session.get("plan_closeout_packet") if isinstance(session.get("plan_closeout_packet"), dict) else {}
    closeout_output = closeout_packet.get("closeout_output") if isinstance(closeout_packet.get("closeout_output"), dict) else {}
    closeout_id = truncate_label(closeout_output.get("closeout_id") or "closeout", max_chars=30)
    summary = closeout_output.get("summary") if isinstance(closeout_output.get("summary"), dict) else {}
    open_count = int(closeout_packet.get("open_decision_count") or summary.get("open_decision_records") or 0)
    return "\n".join(
        [
            title_with_profile("마무리 패킷 생성됨", profile),
            f"{html.escape(closeout_id)} · review needed",
            "closeout 자료만 만들었습니다.",
            f"열린 검토 {open_count}개 · 결과 승인은 아직 없습니다.",
            "로컬에서 closeout-review 검토",
        ]
    )


def render_plan_closeout_packet_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    closeout_packet = session.get("plan_closeout_packet") if isinstance(session.get("plan_closeout_packet"), dict) else {}
    reason = truncate_label(closeout_packet.get("error") or "마무리 패킷 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("마무리 패킷 실패", profile),
            html.escape(reason),
            "대상 task만 closeout하려 했습니다.",
            "결과 승인은 아직 없습니다.",
            "로컬에서 closeout 확인",
        ]
    )


def render_plan_closeout_review_handoff_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = (
        session.get("plan_closeout_review_handoff")
        if isinstance(session.get("plan_closeout_review_handoff"), dict)
        else {}
    )
    closeout_id = truncate_label(handoff.get("closeout_id") or "closeout", max_chars=30)
    if handoff.get("approved_verdict_may_accept_truth"):
        warning = "approved는 accepted truth가 될 수 있습니다."
    else:
        warning = "follow-up이 남아도 로컬 검토가 필요합니다."
    return "\n".join(
        [
            title_with_profile("마무리 검토 준비됨", profile),
            f"{html.escape(closeout_id)} · verdict ready",
            "Telegram에서 verdict를 기록할 수 있습니다.",
            warning,
            "아래 버튼에서 verdict 선택",
        ]
    )


def render_plan_closeout_review_handoff_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    handoff = (
        session.get("plan_closeout_review_handoff")
        if isinstance(session.get("plan_closeout_review_handoff"), dict)
        else {}
    )
    reason = truncate_label(handoff.get("error") or "마무리 검토 준비 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("마무리 검토 준비 실패", profile),
            html.escape(reason),
            "closeout packet만 참조하려 했습니다.",
            "verdict 실행은 하지 않았습니다.",
            "로컬에서 closeout 확인",
        ]
    )


def render_plan_closeout_verdict_message(*, profile: Any, session: dict[str, Any]) -> str:
    verdict = session.get("plan_closeout_verdict") if isinstance(session.get("plan_closeout_verdict"), dict) else {}
    output = (
        verdict.get("closeout_review_output")
        if isinstance(verdict.get("closeout_review_output"), dict)
        else {}
    )
    receipt = output.get("closeout_receipt") if isinstance(output.get("closeout_receipt"), dict) else {}
    recorded_verdict = truncate_label(verdict.get("verdict") or output.get("verdict") or "verdict", max_chars=18)
    acceptance = truncate_label(receipt.get("acceptance_status") or "not_accepted", max_chars=24)
    if receipt.get("acceptance_status") == "accepted":
        truth_line = "accepted truth가 기록됐습니다."
    elif recorded_verdict == "approved":
        truth_line = "follow-up이 남아 아직 accepted는 아닙니다."
    else:
        truth_line = "accepted truth는 아직 없습니다."
    return "\n".join(
        [
            title_with_profile("마무리 verdict 기록됨", profile),
            f"{html.escape(recorded_verdict)} · {html.escape(acceptance)}",
            truth_line,
            "프로젝트 파일 변경은 없습니다.",
            "로컬에서 return package 확인",
        ]
    )


def render_plan_closeout_verdict_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    verdict = session.get("plan_closeout_verdict") if isinstance(session.get("plan_closeout_verdict"), dict) else {}
    reason = truncate_label(verdict.get("error") or "verdict 기록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("verdict 기록 실패", profile),
            html.escape(reason),
            "accepted truth는 만들지 않았습니다.",
            "프로젝트 파일 변경은 없습니다.",
            "로컬에서 closeout-review 확인",
        ]
    )


def render_plan_runtime_start_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    runtime_start = session.get("plan_runtime_start") if isinstance(session.get("plan_runtime_start"), dict) else {}
    reason = truncate_label(runtime_start.get("error") or "실행 시작 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("실행 시작 실패", profile),
            html.escape(reason),
            "대상 task를 시작하지 못했습니다.",
            "완료 판정은 아직 없습니다.",
            "로컬에서 queue 상태 확인",
        ]
    )


def render_plan_review_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    review = session.get("plan_review") if isinstance(session.get("plan_review"), dict) else {}
    reason = truncate_label(review.get("error") or "승인 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 승인 실패", profile),
            html.escape(reason),
            "계획 검토를 기록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_plan_registration_failed_message(*, profile: Any, session: dict[str, Any]) -> str:
    registration = session.get("plan_registration") if isinstance(session.get("plan_registration"), dict) else {}
    reason = truncate_label(registration.get("error") or "등록 실패", max_chars=42)
    return "\n".join(
        [
            title_with_profile("계획 등록 실패", profile),
            html.escape(reason),
            "계획을 등록하지 못했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "로컬에서 원인 확인",
        ]
    )


def render_project_path_required_message(*, profile: Any, session: dict[str, Any]) -> str:
    candidate = session.get("selected_candidate") if isinstance(session.get("selected_candidate"), dict) else {}
    name = truncate_label(candidate.get("display_name") or "직접 입력", max_chars=24)
    return "\n".join(
        [
            title_with_profile("경로 확인 필요", profile),
            f"{html.escape(name)} 위치를 아직 모릅니다.",
            "프로젝트 경로를 직접 입력하세요.",
            "아직 실행은 시작하지 않았습니다.",
            "보류 또는 다시 선택 가능",
        ]
    )


def render_remote_plan_note_message(*, profile: Any, session: dict[str, Any]) -> str:
    return "\n".join(
        [
            title_with_profile("계획 의견 추가", profile),
            "세션에 의견을 남겼습니다.",
            "초기화 검토 전 참고합니다.",
            "아직 실행은 시작하지 않았습니다.",
            "버튼 또는 의견 직접 입력",
        ]
    )


def render_project_selection_deferred_message(*, profile: Any) -> str:
    return "\n".join(
        [
            title_with_profile("계획 선택 보류", profile),
            "세션을 보류했습니다.",
            "아직 실행은 시작하지 않았습니다.",
            "다시 시작하려면 계획 요청을 입력하세요.",
        ]
    )
