"""Typed contracts from authenticated input through final response cards."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .command_envelope import classify_command, command_envelope_binding_issue
from .plan_workflow import resolve_remote_plan_callback_payload
from .routing import parse_remote_command, remote_plan_session_command_payload


INBOUND_EVENT_SCHEMA = "remote_operator_inbound_event.v1"
ROUTED_INTENT_SCHEMA = "remote_operator_routed_intent.v1"
ACTION_CANDIDATE_SCHEMA = "remote_operator_action_candidate.v1"
ACTION_OUTCOME_SCHEMA = "remote_operator_action_outcome.v1"
RESPONSE_CARD_SCHEMA = "remote_operator_response_card.v1"


def _sha256_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_issue(value: Any) -> str | None:
    issue = str(value or "").strip()
    if re.fullmatch(r"[a-z][a-z0-9_.:-]{0,95}", issue):
        return issue
    return None


@dataclass(frozen=True)
class InboundEvent:
    """One authenticated transport input, including only bound identities."""

    transport: str
    source: str
    event_id: int
    message_id: int | None
    occurred_at_epoch: int | None
    input_hash: str
    actor_scope_hash: str
    actor_hash: str
    text: str

    def public_record(self) -> dict[str, Any]:
        """Return audit metadata without message or callback content."""

        return {
            "schema": INBOUND_EVENT_SCHEMA,
            "transport": self.transport,
            "source": self.source,
            "event_id": self.event_id,
            "message_id": self.message_id,
            "occurred_at_epoch": self.occurred_at_epoch,
            "input_hash": self.input_hash,
            "actor_scope_hash": self.actor_scope_hash,
            "actor_hash": self.actor_hash,
        }


@dataclass(frozen=True)
class RoutedIntent:
    """The single routing decision for an authenticated inbound event."""

    route: str
    parsed_command: dict[str, Any]
    plan_session_input: bool
    plan_callback: bool
    plan_text_owned: bool
    plan_payload: str | None
    validation_issue: str | None

    def public_record(self) -> dict[str, Any]:
        """Return routing evidence without user-supplied command arguments."""

        return {
            "schema": ROUTED_INTENT_SCHEMA,
            "route": self.route,
            "command": self.parsed_command.get("command"),
            "supported": bool(self.parsed_command.get("supported")),
            "reason": self.parsed_command.get("reason"),
            "plan_session_input": self.plan_session_input,
            "plan_callback": self.plan_callback,
            "plan_text_owned": self.plan_text_owned,
            "plan_payload_present": bool(self.plan_payload),
            "validation_issue": self.validation_issue,
        }


@dataclass(frozen=True)
class ActionCandidate:
    """A routed intent bound to its validated authority envelope."""

    event: InboundEvent
    intent: RoutedIntent
    command_envelope: dict[str, Any]
    validation_issues: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.validation_issues

    @property
    def primary_issue(self) -> str | None:
        return self.validation_issues[0] if self.validation_issues else None

    def public_record(self) -> dict[str, Any]:
        """Return a redacted action-admission record."""

        return {
            "schema": ACTION_CANDIDATE_SCHEMA,
            "status": "accepted" if self.valid else "refused",
            "valid": self.valid,
            "validation_issues": list(self.validation_issues),
            "route": self.intent.route,
            "command": self.command_envelope.get("command"),
            "command_class": self.command_envelope.get("command_class"),
            "event_id": self.event.event_id,
            "authority_envelope_id": self.command_envelope.get("envelope_id"),
        }


@dataclass(frozen=True)
class ActionOutcome:
    """One final domain outcome, before transport delivery is attempted."""

    outcome_id: str
    event_id: int
    authority_envelope_id: str | None
    route: str
    disposition: str
    result_status: str
    effect_kind: str
    effect_status: str
    effect_authorized: bool
    authority_domain: str
    refusal_reason: str | None

    def public_record(self) -> dict[str, Any]:
        return {
            "schema": ACTION_OUTCOME_SCHEMA,
            "outcome_id": self.outcome_id,
            "event_id": self.event_id,
            "authority_envelope_id": self.authority_envelope_id,
            "route": self.route,
            "disposition": self.disposition,
            "result_status": self.result_status,
            "effect_kind": self.effect_kind,
            "effect_status": self.effect_status,
            "effect_authorized": self.effect_authorized,
            "authority_domain": self.authority_domain,
            "refusal_reason": self.refusal_reason,
        }


@dataclass(frozen=True)
class ResponseCard:
    """One immutable user-facing payload derived from a final outcome."""

    card_id: str
    outcome_id: str
    message: str
    reply_markup: dict[str, Any] | None
    mobile_contract: dict[str, Any]
    choice_contract: dict[str, Any]
    message_sha256: str
    reply_markup_sha256: str | None
    control_surface: str
    action_count: int

    def public_record(self) -> dict[str, Any]:
        """Return payload identity and shape without copying message content."""

        return {
            "schema": RESPONSE_CARD_SCHEMA,
            "card_id": self.card_id,
            "outcome_id": self.outcome_id,
            "message_sha256": self.message_sha256,
            "reply_markup_sha256": self.reply_markup_sha256,
            "control_surface": self.control_surface,
            "action_count": self.action_count,
            "mobile_contract_schema": self.mobile_contract.get("schema"),
            "choice_contract_schema": self.choice_contract.get("schema"),
        }


def route_inbound_event(
    event: InboundEvent,
    *,
    active_plan_session: dict[str, Any] | None,
    plan_callback: bool,
    plan_text_owned: bool,
) -> RoutedIntent:
    """Arbitrate exactly once between Plan workflow input and a command."""

    plan_payload = (
        remote_plan_session_command_payload(event.text)
        if active_plan_session or plan_callback
        else None
    )
    validation_issue = None
    if plan_callback:
        if not isinstance(active_plan_session, dict):
            validation_issue = "plan_session_missing"
        elif not plan_payload:
            validation_issue = "plan_choice_missing"
        else:
            plan_payload, validation_issue = resolve_remote_plan_callback_payload(
                active_plan_session, plan_payload
            )
    plan_session_input = bool(
        plan_callback
        or (active_plan_session and (plan_payload or plan_text_owned))
    )
    if validation_issue:
        route = "remote_plan_callback_refusal"
    elif plan_session_input:
        route = "remote_plan_session"
    else:
        route = "command"
    return RoutedIntent(
        route=route,
        parsed_command=parse_remote_command(event.text),
        plan_session_input=plan_session_input,
        plan_callback=plan_callback,
        plan_text_owned=plan_text_owned,
        plan_payload=plan_payload,
        validation_issue=validation_issue,
    )


def validate_action_candidate(
    event: InboundEvent,
    intent: RoutedIntent,
    command_envelope: dict[str, Any],
) -> ActionCandidate:
    """Bind routing to the authority envelope before journaling or handling."""

    binding_issue = command_envelope_binding_issue(
        command_envelope,
        update_id=event.event_id,
        input_hash=event.input_hash,
        chat_hash=event.actor_scope_hash,
        user_hash=event.actor_hash,
    )
    issues: list[str] = []
    if binding_issue:
        issues.append(binding_issue)
    expected_class, expected_state_changing = classify_command(
        intent.parsed_command,
        plan_session_input=intent.plan_session_input,
    )
    expected_command = (
        "remote_plan_session_input"
        if intent.plan_session_input
        else str(intent.parsed_command.get("command") or "unsupported")
    )
    if command_envelope.get("command") != expected_command:
        issues.append("action_candidate_command_mismatch")
    if command_envelope.get("command_class") != expected_class:
        issues.append("action_candidate_command_class_mismatch")
    if command_envelope.get("state_changing") is not expected_state_changing:
        issues.append("action_candidate_authority_class_mismatch")
    if not command_envelope.get("valid"):
        issues.extend(
            str(issue)
            for issue in command_envelope.get("validation_issues") or []
        )
        if not issues:
            issues.append("invalid_command_envelope")
    if intent.validation_issue:
        issues.append(intent.validation_issue)
    return ActionCandidate(
        event=event,
        intent=intent,
        command_envelope=dict(command_envelope),
        validation_issues=tuple(issues),
    )


def finalize_action_outcome(
    candidate: ActionCandidate,
    result: dict[str, Any],
) -> ActionOutcome:
    """Freeze one internally consistent result after its effect is classified."""

    effect = result.get("effect")
    if not isinstance(effect, dict):
        raise ValueError("action_outcome_effect_missing")
    effect_kind = str(effect.get("kind") or "none")
    effect_status = str(effect.get("status") or "none")
    effect_authorized = bool(effect.get("authorized"))
    mutation_authorized = bool(result.get("mutation_authorized"))
    if effect_authorized and not candidate.valid:
        raise ValueError("action_outcome_invalid_candidate_authorized")
    if effect_authorized and not mutation_authorized:
        raise ValueError("action_outcome_effect_without_mutation_authority")
    if mutation_authorized and not effect_authorized:
        raise ValueError("action_outcome_mutation_without_authorized_effect")
    if effect_authorized and effect_status not in {"applied", "recorded"}:
        raise ValueError("action_outcome_authorized_effect_not_applied")
    if not effect_authorized and effect_status in {"applied", "recorded"}:
        raise ValueError("action_outcome_applied_effect_not_authorized")

    if effect_status in {"applied", "recorded"}:
        disposition = "succeeded"
    elif effect_status == "refused" or not candidate.valid:
        disposition = "refused"
    else:
        disposition = "observed"
    refusal_reason = _public_issue(
        effect.get("error")
        or candidate.primary_issue
        or result.get("reason")
    )
    identity = {
        "event_id": candidate.event.event_id,
        "authority_envelope_id": candidate.command_envelope.get("envelope_id"),
        "route": candidate.intent.route,
        "disposition": disposition,
        "result_status": str(result.get("status") or "unknown"),
        "effect_kind": effect_kind,
        "effect_status": effect_status,
        "effect_authorized": effect_authorized,
        "authority_domain": str(effect.get("authority_domain") or "none"),
        "refusal_reason": refusal_reason,
    }
    return ActionOutcome(
        outcome_id=f"rao-{_sha256_json(identity)[:24]}",
        event_id=candidate.event.event_id,
        authority_envelope_id=str(
            candidate.command_envelope.get("envelope_id") or ""
        )
        or None,
        route=candidate.intent.route,
        disposition=disposition,
        result_status=str(result.get("status") or "unknown"),
        effect_kind=effect_kind,
        effect_status=effect_status,
        effect_authorized=effect_authorized,
        authority_domain=str(effect.get("authority_domain") or "none"),
        refusal_reason=refusal_reason,
    )


def execution_receipt_message(outcome: ActionOutcome, result: dict[str, Any], message: str) -> str:
    """The transport receipt is derived from effects, never model prose."""
    parsed = result.get("parsed_command") or {}
    intent = parsed.get("agent_intent") or {}
    session_request = (
        parsed.get("command") in {"session_send", "session_continue", "session_approve"}
        or intent.get("intent") == "session_message"
        or bool(intent.get("session_target_status"))
    )
    session_effect = outcome.effect_kind in {"session_message", "session_input"}
    if session_request or session_effect:
        dispatch = result.get("dispatch_result") or {}
        target = html.escape(str(dispatch.get("session_title") or dispatch.get("session_id") or intent.get("session_id") or "미확정")[:100])
        if session_effect and outcome.effect_authorized and outcome.effect_status == "applied":
            return "<b>로컬 에이전트에 입력 전송됨</b>\n대상: " + target + "\n작업 수락·완료는 아직 확인되지 않았습니다."
        if outcome.effect_status == "refused":
            error = str(outcome.refusal_reason or "")
            if error == "already_processed":
                return "<b>이미 처리된 버튼입니다</b>\n추가 입력을 보내지 않았습니다.\n다음 조치: 아래 버튼으로 최신 상태를 확인하세요."
            if error in {"prompt_changed", "task_ready_context_missing_or_changed", "waiting_card_context_missing_or_changed"}:
                return "<b>이전 카드의 대기 상태가 바뀌었습니다</b>\n이 버튼으로 새 입력을 보내지 않았습니다.\n다음 조치: 아래 버튼으로 최신 상태를 확인하세요."
            if error.startswith(("session_identity_", "remote_session_identity_")):
                return "<b>세션 실행 정보가 바뀌었습니다</b>\n다른 실행에 입력하지 않도록 요청을 차단했습니다.\n다음 조치: 아래 버튼으로 최신 상태를 확인하세요."
            return "<b>실행 기록: 세션 입력 성공 확인 없음</b>\n입력 처리가 거부되거나 실패했습니다. 대상과 현재 상태를 확인해 주세요.\n사유: " + html.escape(str(outcome.refusal_reason or "unknown")[:120])
        return "<b>실행 기록: 세션 입력 없음</b>\n대상이 불명확하거나 실행 조건이 충족되지 않았습니다. 대상과 본문을 명시해 주세요."
    if parsed.get("command") == "chat" and outcome.effect_kind == "none":
        lines = message.splitlines()
        lines.insert(1, "실행 기록: 세션 입력 없음 · 대화는 실행 증거가 아닙니다.")
        return "\n".join(lines)
    return message


def finalize_response_card(
    outcome: ActionOutcome,
    result: dict[str, Any],
    *,
    mobile_contract_builder: Callable[[str], dict[str, Any]],
    choice_contract_builder: Callable[
        [dict[str, Any] | None, dict[str, Any] | None], dict[str, Any]
    ],
) -> ResponseCard:
    """Build the only transport payload after the action outcome is final."""

    message = str(result.get("message_preview") or "")
    if not message.strip():
        raise ValueError("response_card_message_missing")
    message = execution_receipt_message(outcome, result, message)
    raw_markup = result.get("reply_markup_preview")
    reply_markup = copy.deepcopy(raw_markup) if isinstance(raw_markup, dict) else None
    if isinstance(reply_markup, dict) and isinstance(reply_markup.get("keyboard"), list):
        raise ValueError("response_card_persistent_keyboard_forbidden")
    interaction_context = result.get("interaction_context")
    context = interaction_context if isinstance(interaction_context, dict) else None
    if outcome.effect_kind in {"session_message", "session_input"}:
        reply_markup = {"inline_keyboard": [[{"text": "최신 상태 확인", "callback_data": "fg1:s"}]]}
        context = None
    mobile_contract = mobile_contract_builder(message)
    choice_contract = choice_contract_builder(reply_markup, context)
    if not isinstance(mobile_contract, dict):
        raise ValueError("response_card_mobile_contract_missing")
    if not isinstance(choice_contract, dict):
        raise ValueError("response_card_choice_contract_missing")

    inline_rows = (
        reply_markup.get("inline_keyboard")
        if isinstance(reply_markup, dict)
        else None
    )
    action_count = sum(
        1
        for row in inline_rows or []
        if isinstance(row, list)
        for button in row
        if isinstance(button, dict)
    )
    control_surface = (
        "inline"
        if isinstance(inline_rows, list)
        else "remove"
        if isinstance(reply_markup, dict)
        and reply_markup.get("remove_keyboard") is True
        else "none"
    )
    if choice_contract.get("surface_kind") != control_surface:
        raise ValueError("response_card_choice_contract_mismatch")
    message_sha256 = hashlib.sha256(message.encode("utf-8")).hexdigest()
    reply_markup_sha256 = (
        _sha256_json(reply_markup) if isinstance(reply_markup, dict) else None
    )
    card_identity = {
        "outcome_id": outcome.outcome_id,
        "message_sha256": message_sha256,
        "reply_markup_sha256": reply_markup_sha256,
        "control_surface": control_surface,
        "action_count": action_count,
    }
    return ResponseCard(
        card_id=f"rrc-{_sha256_json(card_identity)[:24]}",
        outcome_id=outcome.outcome_id,
        message=message,
        reply_markup=reply_markup,
        mobile_contract=copy.deepcopy(mobile_contract),
        choice_contract=copy.deepcopy(choice_contract),
        message_sha256=message_sha256,
        reply_markup_sha256=reply_markup_sha256,
        control_surface=control_surface,
        action_count=action_count,
    )
