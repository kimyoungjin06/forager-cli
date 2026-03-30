#!/usr/bin/env python3
"""Data request-contract extraction and artifact-specific acceptance helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


_DATA_MARKERS = (
    "csv",
    "schema",
    "null",
    "sample",
    "dataset",
    "column",
    "normalize",
    "transform",
    "데이터",
    "스키마",
    "결측",
    "샘플",
    "컬럼",
    "정규화",
    "변환",
)

_FORMAT_MARKERS = ("YYYY/MM", "YYYY-MM", "YYYY.MM")


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def _contains_any(text: str, markers: List[str] | tuple[str, ...]) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    return any(str(marker).strip().lower() in low for marker in markers if str(marker).strip())


def _dedupe_rows(rows: List[str], *, limit: int = 8) -> List[str]:
    out: List[str] = []
    for item in rows:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out[: max(1, int(limit))]


def _extract_source_path(prompt: str) -> str:
    src = str(prompt or "")
    explicit_patterns = [
        r"(?:입력\s*csv|원본\s*csv|source\s*csv(?:\s*path)?)\s*(?:는|은|:|=|is)?\s*([A-Za-z0-9_./-]+\.csv)",
        r"([A-Za-z0-9_./-]+\.csv)\s*(?:이고|이며|as input|input으로|입력으로)",
    ]
    for pattern in explicit_patterns:
        match = re.search(pattern, src, flags=re.IGNORECASE)
        if match:
            return _trim(match.group(1), 200)

    candidates = re.findall(r"([A-Za-z0-9_./-]+\.csv)", src, flags=re.IGNORECASE)
    filtered = [
        token
        for token in candidates
        if token and not any(marker in token.lower() for marker in ("sample_", "sample-", "normalized", "output"))
    ]
    if filtered:
        return _trim(filtered[0], 200)
    if candidates:
        return _trim(candidates[0], 200)
    return ""


def _extract_target_column(prompt: str) -> str:
    src = str(prompt or "")
    patterns = [
        r"(?:대상\s*컬럼|정규화\s*대상\s*컬럼|target\s*column)\s*(?:은|는|:|=|is)?\s*([A-Za-z_][A-Za-z0-9_]*)",
        r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:컬럼|column)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, src, flags=re.IGNORECASE)
        if match:
            token = _trim(match.group(1), 80)
            if token.lower() not in {"csv", "data", "dataset"}:
                return token
    if re.search(r"\bmonth\b", src, flags=re.IGNORECASE):
        return "month"
    return ""


def _extract_accepted_formats(prompt: str) -> List[str]:
    src = str(prompt or "")
    found = [marker for marker in _FORMAT_MARKERS if marker.lower() in src.lower()]
    return _dedupe_rows(found, limit=4)


def _extract_normalize_to(prompt: str) -> str:
    src = str(prompt or "")
    explicit = re.search(
        r"(?:normalize\s+to|정규화(?:한다|해서|하여)?|표준화(?:한다|해서|하여)?)\s*(?:는|은|to|:|=)?\s*(YYYY[/.-]MM)",
        src,
        flags=re.IGNORECASE,
    )
    if explicit:
        return _trim(explicit.group(1).upper(), 32).replace("/", "-").replace(".", "-")
    if "yyyy-mm" in src.lower():
        return "YYYY-MM"
    return ""


def _extract_invalid_value_policy(prompt: str) -> Dict[str, bool]:
    src = str(prompt or "")
    low = src.lower()
    preserve_any_original = _contains_any(
        low,
        [
            "preserve original",
            "keep original",
            "leave unchanged",
            "unchanged",
            "원본 유지",
            "원본 그대로 유지",
            "원본 보존",
        ],
    )
    invalid_scope = _contains_any(
        low,
        [
            "invalid",
            "null",
            "empty",
            "out-of-range",
            "parse 불가",
            "범위를 벗어난",
            "비정상",
            "결측",
            "anomaly",
        ],
    )
    drop_row = _contains_any(low, ["drop row", "row drop", "행 삭제", "행 제거"])
    preserve_row = _contains_any(low, ["preserve row", "원본 행", "행 유지", "row 유지"])
    preserve_original_value = _contains_any(
        low,
        ["original value", "원값", "원본 값", "그대로 둔다", "그대로 두"],
    )

    # Data requests often compress the policy into "원본 유지" instead of spelling out
    # row/value separately. When that phrasing appears in an invalid-value context and
    # the prompt does not explicitly ask to drop rows, keep both row and original value.
    if preserve_any_original and invalid_scope and not drop_row:
        preserve_row = True
        preserve_original_value = True

    return {
        "preserve_row": preserve_row,
        "preserve_original_value": preserve_original_value,
        "record_anomaly": _contains_any(low, ["anomaly", "이상치", "기록", "요약", "summary"]),
        "drop_row": drop_row,
    }


def _data_output_artifacts(prompt: str) -> List[str]:
    src = str(prompt or "")
    outputs = ["normalized_csv"]
    if _contains_any(src, ["schema", "스키마"]):
        outputs.append("schema_report")
    if _contains_any(src, ["null", "결측", "anomaly", "요약"]):
        outputs.append("null_summary")
    if _contains_any(src, ["sample", "샘플", "5행", "5 rows"]):
        outputs.append("sample_output")
    return _dedupe_rows(outputs, limit=6)


def _artifact_contracts(outputs: List[str], *, target_column: str) -> Dict[str, Dict[str, Any]]:
    contracts: Dict[str, Dict[str, Any]] = {}
    if "normalized_csv" in outputs:
        contracts["normalized_csv"] = {
            "path": "normalized.csv",
            "format": "csv",
            "required_fields": _dedupe_rows([target_column] if target_column else [], limit=8),
            "acceptance_notes": [
                "normalized.csv is the transformed output used by downstream evidence artifacts.",
            ],
        }
    if "schema_report" in outputs:
        anomaly_field_path = (
            f"{_trim(target_column, 80)}_anomalies[]" if _trim(target_column, 80) else "target_anomalies[]"
        )
        contracts["schema_report"] = {
            "path": "schema_report.json",
            "format": "json",
            "required_fields": [
                "columns[].name",
                "columns[].inferred_type",
                "columns[].type_rule",
                "columns[].null_count",
                "columns[].observed_non_null_count",
                f"{anomaly_field_path}.bucket",
                f"{anomaly_field_path}.count",
                f"{anomaly_field_path}.examples[]",
            ],
            "inference_policy": _schema_inference_policy(),
            "acceptance_notes": [
                "schema_report.json must cover every transformed output column, not a partial subset.",
                "schema_report.json is the canonical anomaly evidence source for downstream null summaries.",
            ],
        }
    if "null_summary" in outputs:
        contracts["null_summary"] = {
            "path": "null_summary.md",
            "format": "markdown",
            "required_fields": [
                "column",
                "null_count",
                "invalid_value_examples",
            ],
            "acceptance_notes": [
                "null_summary.md records null/anomaly handling against the transformed output.",
            ],
        }
    if "sample_output" in outputs:
        contracts["sample_output"] = {
            "path": "sample_5.csv",
            "format": "csv",
            "required_fields": ["first_5_data_rows", "transformed_output_sample", "shortfall_note_when_needed"],
            "acceptance_notes": [
                "sample_5.csv follows the request-contract sample output policy.",
            ],
        }
    return contracts


def _month_bucket_policy(
    *,
    accepted_formats: List[str],
    normalize_to: str,
    invalid_value_policy: Dict[str, bool],
) -> Dict[str, Any]:
    if not accepted_formats and not normalize_to and not any(bool(value) for value in invalid_value_policy.values()):
        return {}
    return {
        "valid_patterns": _dedupe_rows(accepted_formats, limit=6),
        "valid_year_rule": "4-digit-year",
        "valid_month_rule": "01-12",
        "normalize_to": _trim(normalize_to, 32),
        "trim_before_match": True,
        "null_like_match": "trim+casefold-exact",
        "null_like_tokens": ["null", "nan"],
        "allowed_separators": ["/", "-", "."],
        "year_width_mismatch_bucket": "bad-year",
        "separator_mismatch_bucket": "malformed-value",
        "token_count_mismatch_bucket": "malformed-value",
        "one_digit_month_bucket": "one-digit-month",
        "match_order": [
            "empty-string",
            "whitespace-only",
            "literal-null",
            "literal-nan",
            "valid-format",
            "one-digit-month",
            "bad-year",
            "month-00",
            "month-13-plus",
            "malformed-value",
        ],
        "anomaly_buckets": [
            "one-digit-month",
            "bad-year",
            "month-00",
            "month-13-plus",
            "whitespace-only",
            "empty-string",
            "literal-null",
            "literal-nan",
            "malformed-value",
        ],
        "preserve_row": bool(invalid_value_policy.get("preserve_row")),
        "preserve_original_value": bool(invalid_value_policy.get("preserve_original_value")),
        "record_anomaly": bool(invalid_value_policy.get("record_anomaly")),
    }


def _schema_inference_policy() -> Dict[str, Any]:
    return {
        "allowed_inferred_types": ["string", "integer", "number", "boolean"],
        "precedence_order": ["integer", "number", "boolean", "string"],
        "mixed_type_resolution": "string",
        "no_non_null_resolution": "string",
        "type_rule_source": "observable-transformed-values",
    }


def _schema_null_count_policy(*, target_column: str) -> Dict[str, Any]:
    return {
        "all_columns_rule": "trimmed-empty-or-null-like-string",
        "null_like_buckets": ["empty-string", "whitespace-only", "literal-null", "literal-nan"],
        "target_invalid_buckets_excluded": [
            "one-digit-month",
            "bad-year",
            "month-00",
            "month-13-plus",
            "malformed-value",
        ],
        "target_column": _trim(target_column, 80),
    }


def _schema_anomaly_evidence_policy(*, target_column: str) -> Dict[str, Any]:
    column = _trim(target_column, 80)
    if not column:
        return {}
    return {
        "target_column": column,
        "field_path": f"{column}_anomalies[]",
        "required_fields": ["bucket", "count", "examples[]"],
        "source_policy": "month_bucket_policy",
    }


def _sample_output_policy() -> Dict[str, Any]:
    return {
        "selection_policy": "head",
        "sample_size": 5,
        "row_unit": "data-row",
        "order_basis": "transformed-output-order",
        "shortfall_policy": "emit-all-available-and-note-shortfall",
        "shortfall_encoding": "append-note-row",
        "shortfall_note_position": "after-emitted-rows",
        "shortfall_note_marker_column": "__aoe_sample_note__",
        "shortfall_note_marker_value": "sample_shortfall",
        "shortfall_note_fields": ["requested_rows", "emitted_rows", "missing_rows"],
    }


def _normalized_output_policy() -> Dict[str, Any]:
    return {
        "row_order_policy": "preserve-source-data-row-order",
        "header_policy": "preserve-source-header-order",
    }


def data_request_contract_matches(prompt: str) -> bool:
    src = str(prompt or "")
    if not src.strip():
        return False
    if _extract_source_path(src):
        return True
    strong_categories = [
        _contains_any(src, ["csv", "dataset", "table", "데이터셋", "테이블"]),
        _contains_any(src, ["column", "month", "월별", "컬럼"]),
        _contains_any(src, ["normalize", "transform", "정규화", "변환", "표준화"]),
        _contains_any(src, ["schema", "null", "결측", "스키마"]),
    ]
    sample_only = _contains_any(src, ["sample", "샘플", "5행", "5 rows"])
    if sum(1 for matched in strong_categories if matched) >= 2:
        return True
    if sample_only and sum(1 for matched in strong_categories if matched) >= 1:
        return True
    return False


def extract_data_request_contract(prompt: str) -> Optional[Dict[str, Any]]:
    source_prompt = str(prompt or "").strip()
    if not data_request_contract_matches(source_prompt):
        return None

    source_path = _extract_source_path(source_prompt)
    target_column = _extract_target_column(source_prompt)
    accepted_formats = _extract_accepted_formats(source_prompt)
    normalize_to = _extract_normalize_to(source_prompt)
    invalid_value_policy = _extract_invalid_value_policy(source_prompt)
    month_bucket_policy = _month_bucket_policy(
        accepted_formats=accepted_formats,
        normalize_to=normalize_to,
        invalid_value_policy=invalid_value_policy,
    )
    output_artifacts = _data_output_artifacts(source_prompt)
    artifact_contracts = _artifact_contracts(output_artifacts, target_column=target_column)
    schema_inference_policy = _schema_inference_policy() if "schema_report" in output_artifacts else {}
    schema_null_count_policy = (
        _schema_null_count_policy(target_column=target_column)
        if any(name in output_artifacts for name in ("schema_report", "null_summary"))
        else {}
    )
    schema_anomaly_evidence_policy = (
        _schema_anomaly_evidence_policy(target_column=target_column)
        if any(name in output_artifacts for name in ("schema_report", "null_summary"))
        else {}
    )
    sample_output_policy = _sample_output_policy() if "sample_output" in output_artifacts else {}
    normalized_output_policy = _normalized_output_policy()

    missing_fields: List[str] = []
    if not source_path:
        missing_fields.append("source_path")
    if not target_column:
        missing_fields.append("target_column")
    if not accepted_formats:
        missing_fields.append("accepted_input_formats")
    if not normalize_to:
        missing_fields.append("normalize_to")
    if not any(bool(value) for value in invalid_value_policy.values()):
        missing_fields.append("invalid_value_policy")

    status = "complete" if not missing_fields else "incomplete"
    summary_parts = [
        "data",
        f"source={source_path or '-'}",
        f"column={target_column or '-'}",
    ]
    if accepted_formats:
        summary_parts.append(f"formats={','.join(accepted_formats)}")
    if normalize_to:
        summary_parts.append(f"to={normalize_to}")
    if output_artifacts:
        summary_parts.append(
            "outputs="
            + ",".join(
                str((artifact_contracts.get(name) or {}).get("path", name)).strip() or name
                for name in output_artifacts
            )
        )

    return {
        "version": "2026-03-30.v1",
        "contract_type": "data",
        "preset": "data",
        "status": status,
        "objective": _trim(source_prompt, 240),
        "source_prompt": source_prompt,
        "fields": {
            "source_path": source_path,
            "target_column": target_column,
            "accepted_input_formats": accepted_formats,
            "normalize_to": normalize_to,
            "zero_pad": "YYYY-MM" in normalize_to,
            "invalid_value_policy": invalid_value_policy,
            "month_bucket_policy": month_bucket_policy,
            "normalized_output_policy": normalized_output_policy,
            "schema_inference_policy": schema_inference_policy,
            "schema_null_count_policy": schema_null_count_policy,
            "schema_anomaly_evidence_policy": schema_anomaly_evidence_policy,
            "sample_output_policy": sample_output_policy,
        },
        "required_outputs": [
            str((artifact_contracts.get(name) or {}).get("path", name)).strip() or name
            for name in output_artifacts
        ],
        "required_evidence": [
            token
            for token in ("schema_check", "null_summary", "sample_output")
            if token in (
                "schema_check" if "schema_report" in output_artifacts else "",
                "null_summary" if "null_summary" in output_artifacts else "",
                "sample_output" if "sample_output" in output_artifacts else "",
            )
        ],
        "missing_fields": missing_fields,
        "ambiguity_notes": [],
        "summary": " | ".join(summary_parts)[:400],
        "artifact_contracts": artifact_contracts,
    }


def data_request_contract_acceptance_floor(
    *,
    request_contract: Dict[str, Any],
    title: str,
    goal: str,
) -> List[str]:
    if not isinstance(request_contract, dict):
        return []
    if str(request_contract.get("contract_type", "")).strip().lower() != "data":
        return []

    fields = request_contract.get("fields") if isinstance(request_contract.get("fields"), dict) else {}
    artifact_contracts = (
        request_contract.get("artifact_contracts")
        if isinstance(request_contract.get("artifact_contracts"), dict)
        else {}
    )
    task_context = "\n".join((str(title or ""), str(goal or "")))
    title_context = str(title or "").lower()
    schema_contract = artifact_contracts.get("schema_report") if isinstance(artifact_contracts.get("schema_report"), dict) else {}
    null_contract = artifact_contracts.get("null_summary") if isinstance(artifact_contracts.get("null_summary"), dict) else {}
    sample_contract = artifact_contracts.get("sample_output") if isinstance(artifact_contracts.get("sample_output"), dict) else {}
    normalized_contract = artifact_contracts.get("normalized_csv") if isinstance(artifact_contracts.get("normalized_csv"), dict) else {}
    normalized_path = _trim(normalized_contract.get("path", ""), 200)
    schema_path = _trim(schema_contract.get("path", ""), 200)
    null_path = _trim(null_contract.get("path", ""), 200)
    sample_path = _trim(sample_contract.get("path", ""), 200)
    explicit_artifact_targets = [
        token
        for token, enabled in (
            ("normalized", bool(normalized_path and normalized_path.lower() in title_context)),
            ("schema", bool(schema_path and schema_path.lower() in title_context)),
            ("null", bool(null_path and null_path.lower() in title_context)),
            ("sample", bool(sample_path and sample_path.lower() in title_context)),
        )
        if enabled
    ]
    low_task_context = task_context.lower()
    is_transform = bool(re.search(r"\b(normalize|transform)\b", low_task_context)) or _contains_any(
        task_context,
        ["정규화", "변환"],
    )
    is_schema = _contains_any(task_context, ["schema", "스키마", "report", "리포트"])
    is_null = _contains_any(task_context, ["null", "결측", "anomaly", "요약", "summary"])
    is_sample = _contains_any(task_context, ["sample", "샘플", "5행", "5 rows"])
    if explicit_artifact_targets:
        evidence_targets = list(explicit_artifact_targets)
    else:
        evidence_targets = [
            token
            for token, enabled in (
                ("schema", is_schema),
                ("null", is_null),
                ("sample", is_sample),
            )
            if enabled
        ]
    combined_evidence = len([token for token in evidence_targets if token != "normalized"]) >= 2
    explicit_normalized = "normalized" in explicit_artifact_targets
    explicit_schema = "schema" in explicit_artifact_targets
    explicit_null = "null" in explicit_artifact_targets
    explicit_sample = "sample" in explicit_artifact_targets

    floor: List[str] = []
    source_path = _trim(fields.get("source_path", ""), 200)
    target_column = _trim(fields.get("target_column", ""), 80)
    accepted_formats = [
        _trim(item, 32) for item in (fields.get("accepted_input_formats") or []) if _trim(item, 32)
    ]
    normalize_to = _trim(fields.get("normalize_to", ""), 32)
    invalid_policy = fields.get("invalid_value_policy") if isinstance(fields.get("invalid_value_policy"), dict) else {}
    month_bucket_policy = (
        fields.get("month_bucket_policy") if isinstance(fields.get("month_bucket_policy"), dict) else {}
    )
    schema_inference_policy = (
        fields.get("schema_inference_policy") if isinstance(fields.get("schema_inference_policy"), dict) else {}
    )
    schema_null_count_policy = (
        fields.get("schema_null_count_policy") if isinstance(fields.get("schema_null_count_policy"), dict) else {}
    )
    schema_anomaly_evidence_policy = (
        fields.get("schema_anomaly_evidence_policy")
        if isinstance(fields.get("schema_anomaly_evidence_policy"), dict)
        else {}
    )
    sample_output_policy = (
        fields.get("sample_output_policy") if isinstance(fields.get("sample_output_policy"), dict) else {}
    )
    normalized_output_policy = (
        fields.get("normalized_output_policy")
        if isinstance(fields.get("normalized_output_policy"), dict)
        else {}
    )
    valid_patterns = [
        _trim(item, 32) for item in (month_bucket_policy.get("valid_patterns") or []) if _trim(item, 32)
    ] or accepted_formats
    bucket_normalize_to = _trim(month_bucket_policy.get("normalize_to", ""), 32) or normalize_to
    bucket_year_rule = _trim(month_bucket_policy.get("valid_year_rule", ""), 64) or "4-digit-year"
    bucket_month_rule = _trim(month_bucket_policy.get("valid_month_rule", ""), 32) or "01-12"
    anomaly_buckets = [
        _trim(item, 48) for item in (month_bucket_policy.get("anomaly_buckets") or []) if _trim(item, 48)
    ]
    bucket_match_order = [
        _trim(item, 32) for item in (month_bucket_policy.get("match_order") or []) if _trim(item, 32)
    ]
    trim_before_match = bool(month_bucket_policy.get("trim_before_match"))
    null_like_match = _trim(month_bucket_policy.get("null_like_match", ""), 40) or "trim+casefold-exact"
    null_like_tokens = [
        _trim(item, 16) for item in (month_bucket_policy.get("null_like_tokens") or []) if _trim(item, 16)
    ]
    allowed_separators = [
        _trim(item, 8) for item in (month_bucket_policy.get("allowed_separators") or []) if _trim(item, 8)
    ]
    year_width_mismatch_bucket = _trim(month_bucket_policy.get("year_width_mismatch_bucket", ""), 32) or "bad-year"
    separator_mismatch_bucket = _trim(month_bucket_policy.get("separator_mismatch_bucket", ""), 32) or "malformed-value"
    token_count_mismatch_bucket = _trim(month_bucket_policy.get("token_count_mismatch_bucket", ""), 32) or "malformed-value"
    one_digit_month_bucket = _trim(month_bucket_policy.get("one_digit_month_bucket", ""), 32) or "one-digit-month"
    allowed_inferred_types = [
        _trim(item, 24) for item in (schema_inference_policy.get("allowed_inferred_types") or []) if _trim(item, 24)
    ]
    precedence_order = [
        _trim(item, 24) for item in (schema_inference_policy.get("precedence_order") or []) if _trim(item, 24)
    ]
    mixed_type_resolution = _trim(schema_inference_policy.get("mixed_type_resolution", ""), 24) or "string"
    no_non_null_resolution = _trim(schema_inference_policy.get("no_non_null_resolution", ""), 24) or "string"
    type_rule_source = _trim(schema_inference_policy.get("type_rule_source", ""), 64) or "observable-transformed-values"
    inference_policy_clause = ""
    if allowed_inferred_types:
        inference_policy_clause = (
            "`schema_inference_policy`: "
            + ">".join(precedence_order or allowed_inferred_types)
            + f"; mixed/no-non-null=`{mixed_type_resolution}`."
        )
    null_like_buckets = [
        _trim(item, 32) for item in (schema_null_count_policy.get("null_like_buckets") or []) if _trim(item, 32)
    ]
    excluded_invalid_buckets = [
        _trim(item, 32)
        for item in (schema_null_count_policy.get("target_invalid_buckets_excluded") or [])
        if _trim(item, 32)
    ]
    anomaly_field_path = _trim(schema_anomaly_evidence_policy.get("field_path", ""), 80)
    anomaly_required_fields = [
        _trim(item, 32)
        for item in (schema_anomaly_evidence_policy.get("required_fields") or [])
        if _trim(item, 32)
    ]
    anomaly_policy_clause = ""
    if anomaly_field_path and anomaly_required_fields:
        anomaly_policy_clause = (
            "`schema_anomaly_evidence_policy`: "
            + anomaly_field_path
            + " has "
            + "/".join(anomaly_required_fields)
            + "."
        )
    anomaly_policy_summary = ""
    if anomaly_field_path and anomaly_required_fields:
        anomaly_policy_summary = (
            "`schema_anomaly_evidence_policy` "
            + anomaly_field_path
            + " "
            + "/".join(anomaly_required_fields).replace("examples[]", "examples")
        )
    sample_selection_policy = _trim(sample_output_policy.get("selection_policy", ""), 24) or "head"
    sample_size = int(sample_output_policy.get("sample_size") or 5)
    sample_row_unit = _trim(sample_output_policy.get("row_unit", ""), 24) or "data-row"
    sample_order_basis = _trim(sample_output_policy.get("order_basis", ""), 48) or "transformed-output-order"
    sample_shortfall_policy = (
        _trim(sample_output_policy.get("shortfall_policy", ""), 64)
        or "emit-all-available-and-note-shortfall"
    )
    sample_shortfall_encoding = (
        _trim(sample_output_policy.get("shortfall_encoding", ""), 48) or "append-note-row"
    )
    sample_shortfall_position = (
        _trim(sample_output_policy.get("shortfall_note_position", ""), 48) or "after-emitted-rows"
    )
    sample_shortfall_marker_column = (
        _trim(sample_output_policy.get("shortfall_note_marker_column", ""), 48) or "__aoe_sample_note__"
    )
    sample_shortfall_marker_value = (
        _trim(sample_output_policy.get("shortfall_note_marker_value", ""), 48) or "sample_shortfall"
    )
    sample_shortfall_note_fields = [
        _trim(item, 24)
        for item in (sample_output_policy.get("shortfall_note_fields") or [])
        if _trim(item, 24)
    ]
    normalized_row_order_policy = (
        _trim(normalized_output_policy.get("row_order_policy", ""), 64)
        or "preserve-source-data-row-order"
    )
    normalized_header_policy = (
        _trim(normalized_output_policy.get("header_policy", ""), 64)
        or "preserve-source-header-order"
    )
    sample_policy_summary = (
        "`sample_output_policy` "
        f"{sample_selection_policy} {sample_size} {sample_row_unit}s by {sample_order_basis}"
    )
    if sample_shortfall_encoding and sample_shortfall_note_fields:
        shortfall_note_field_summary = "req/emitted/missing"
        sample_policy_summary += (
            "; shortfall="
            f"{sample_shortfall_encoding}({sample_shortfall_position},"
            f"{sample_shortfall_marker_column}={sample_shortfall_marker_value},"
            f"{shortfall_note_field_summary})"
        )
    elif sample_shortfall_policy:
        sample_policy_summary += f"; {sample_shortfall_policy}"
    null_count_policy_clause = ""
    if null_like_buckets:
        null_count_policy_clause = (
            "`schema_null_count_policy`: "
            + "/".join(null_like_buckets)
        )
        if excluded_invalid_buckets:
            null_count_policy_clause += "; exclude month invalid buckets"
        null_count_policy_clause += "."
    null_count_policy_summary = ""
    if null_like_buckets:
        null_aliases = {
            "empty-string": "empty",
            "whitespace-only": "space",
            "literal-null": "null",
            "literal-nan": "nan",
        }
        summarized_buckets = [null_aliases.get(item, item) for item in null_like_buckets]
        null_count_policy_summary = "`schema_null_count_policy` " + "/".join(summarized_buckets)
        if excluded_invalid_buckets:
            null_count_policy_summary += ", exclude month invalids"
    inference_policy_summary = ""
    if precedence_order or allowed_inferred_types:
        inference_policy_summary = (
            "`schema_inference_policy` "
            + ">".join(precedence_order or allowed_inferred_types)
        )

    if combined_evidence:
        floor = []
        if "null" in evidence_targets and null_contract:
            floor.append(
                f"`{null_contract.get('path', 'null_summary.md')}` records affected columns, null counts, and invalid month examples from the transformed output while preserving the requested row/value policy and the request-contract `month_bucket_policy`."
            )
        if "schema" in evidence_targets and schema_contract:
            floor.append(
                f"`{schema_contract.get('path', 'schema_report.json')}` covers every transformed output column with name, inferred_type, type_rule, null_count, and observed_non_null_count; it uses request-contract `month_bucket_policy` + request-contract `schema_inference_policy`."
            )
        if "sample" in evidence_targets and sample_contract:
            floor.append(
                f"`{sample_contract.get('path', 'sample_5.csv')}` follows request-contract {sample_policy_summary}."
            )
    elif explicit_schema and schema_contract and not (explicit_normalized or is_transform):
        floor = [
            f"Schema report writes `{schema_contract.get('path', 'schema_report.json')}` as JSON.",
            "Schema evidence covers every transformed output column plus canonical anomaly evidence.",
            "Policies: "
            + anomaly_policy_summary
            + "; "
            + null_count_policy_summary
            + "; "
            + inference_policy_summary
            + ".",
        ]
    elif explicit_null and null_contract:
        floor = [
            f"Null summary writes `{null_contract.get('path', 'null_summary.md')}` as markdown.",
            (
                "Null/anomaly evidence reads canonical anomaly buckets/count/examples from `schema_report.json` "
                f"`{anomaly_field_path or 'target_anomalies[]'}` and summarizes the same transformed-output counts."
            ),
            "Null/anomaly handling preserves the requested row/value policy instead of silently rewriting evidence.",
        ]
    elif explicit_sample and sample_contract:
        floor = [
            f"Sample output writes `{sample_contract.get('path', 'sample_5.csv')}` as CSV.",
            f"Sampling follows request-contract {sample_policy_summary}.",
            "Sample rows are sufficient to inspect normalized month formatting and null/anomaly handling.",
        ]
    elif explicit_normalized or is_transform:
        if source_path and target_column and normalized_path:
            floor.append(
                f"Transform acceptance writes `{normalized_path}` from source `{source_path}`; row count + row/header order stay unchanged, non-target columns stay exact, and only target column `{target_column}` may change."
            )
        elif source_path and target_column:
            floor.append(
                f"Transform acceptance binds source `{source_path}` and target column `{target_column}` explicitly."
            )
        valid_rule = ""
        if valid_patterns and bucket_normalize_to:
            valid_rule = (
                f"Only request-contract valid month formats {', '.join(valid_patterns)} with {bucket_year_rule} and month {bucket_month_rule} normalize to {bucket_normalize_to}; all other month buckets stay anomalies."
            )
        match_rule = ""
        if bucket_match_order:
            match_rule = (
                "Month bucket policy: "
                + ("trim-before-match only for classification; " if trim_before_match else "")
                + f"null-like={'/'.join(null_like_tokens) or 'null/nan'} via {null_like_match}; "
                + f"bad-year={year_width_mismatch_bucket}; one-digit-month={one_digit_month_bucket}; "
                + "invalid/null-like/whitespace/out-of-range keep original row + month bytes"
                + "."
            )
        actions: List[str] = []
        if bool(invalid_policy.get("preserve_row")):
            actions.append("preserve the original row")
        if bool(invalid_policy.get("preserve_original_value")):
            actions.append("preserve the original month value")
        if bool(invalid_policy.get("record_anomaly")):
            actions.append("record an anomaly summary")
        if bool(invalid_policy.get("drop_row")):
            actions.append("drop invalid rows")
        final_rule = ""
        if normalized_path and target_column:
            final_rule = (
                f"`{normalized_path}` keeps the input row count unchanged, preserves every non-target column exactly, "
                f"preserves {normalized_row_order_policy} + {normalized_header_policy}, normalizes only valid `{target_column}` values, and preserves invalid/null/empty/out-of-range `{target_column}` values exactly as requested"
            )
        invalid_rule = ""
        if actions:
            invalid_rule = "Invalid/null/empty/out-of-range handling must " + ", ".join(actions) + "."
        if valid_rule:
            floor.append(valid_rule)
        if match_rule:
            floor.append(match_rule)
        if actions and final_rule:
            floor.append(final_rule + ".")
            if not bool(invalid_policy.get("preserve_row")) or not bool(invalid_policy.get("preserve_original_value")):
                floor.append(invalid_rule)
        elif final_rule:
            floor.append(final_rule + ".")
            if invalid_rule and not (
                bool(invalid_policy.get("preserve_row")) and bool(invalid_policy.get("preserve_original_value"))
            ):
                floor.append(invalid_rule)
        elif invalid_rule:
            floor.append(invalid_rule)
    elif is_schema and schema_contract:
        floor = [
            f"Schema report writes `{schema_contract.get('path', 'schema_report.json')}` as JSON.",
            "Schema evidence covers every transformed output column plus canonical anomaly evidence.",
            "Policies: "
            + anomaly_policy_summary
            + "; "
            + null_count_policy_summary
            + "; "
            + inference_policy_summary
            + ".",
        ]
    elif is_null and null_contract:
        floor = [
            f"Null summary writes `{null_contract.get('path', 'null_summary.md')}` as markdown.",
            (
                "Null/anomaly evidence reads canonical anomaly buckets/count/examples from `schema_report.json` "
                f"`{anomaly_field_path or 'target_anomalies[]'}` and summarizes the same transformed-output counts."
            ),
            "Null/anomaly handling preserves the requested row/value policy instead of silently rewriting evidence.",
        ]
    elif is_sample and sample_contract:
        floor = [
            f"Sample output writes `{sample_contract.get('path', 'sample_5.csv')}` as CSV.",
            f"Sampling follows request-contract {sample_policy_summary}.",
            "Sample rows are sufficient to inspect normalized month formatting and null/anomaly handling.",
        ]

    return _dedupe_rows([_trim(item, 480) for item in floor], limit=3)
