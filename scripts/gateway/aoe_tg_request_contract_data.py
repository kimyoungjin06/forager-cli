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
    return {
        "preserve_row": _contains_any(low, ["preserve row", "원본 행", "행 유지", "row 유지"]),
        "preserve_original_value": _contains_any(low, ["original value", "원값", "원본 값", "그대로 둔다", "그대로 두"]),
        "record_anomaly": _contains_any(low, ["anomaly", "이상치", "기록", "요약", "summary"]),
        "drop_row": _contains_any(low, ["drop row", "row drop", "행 삭제", "행 제거"]),
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
        contracts["schema_report"] = {
            "path": "schema_report.json",
            "format": "json",
            "required_fields": [
                "columns[].name",
                "columns[].inferred_type",
                "columns[].type_rule",
                "columns[].null_count",
                "columns[].observed_non_null_count",
            ],
            "acceptance_notes": [
                "schema_report.json must cover every transformed output column, not a partial subset.",
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
            "required_fields": ["5_rows", "transformed_output_sample"],
            "acceptance_notes": [
                "sample_5.csv contains exactly five rows sampled from the transformed output.",
            ],
        }
    return contracts


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
    output_artifacts = _data_output_artifacts(source_prompt)
    artifact_contracts = _artifact_contracts(output_artifacts, target_column=target_column)

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
    is_transform = _contains_any(task_context, ["normalize", "transform", "정규화", "변환", "month", "월별"])
    is_schema = _contains_any(task_context, ["schema", "스키마", "report", "리포트"])
    is_null = _contains_any(task_context, ["null", "결측", "anomaly", "요약", "summary"])
    is_sample = _contains_any(task_context, ["sample", "샘플", "5행", "5 rows"])

    floor: List[str] = []
    source_path = _trim(fields.get("source_path", ""), 200)
    target_column = _trim(fields.get("target_column", ""), 80)
    accepted_formats = [
        _trim(item, 32) for item in (fields.get("accepted_input_formats") or []) if _trim(item, 32)
    ]
    normalize_to = _trim(fields.get("normalize_to", ""), 32)
    invalid_policy = fields.get("invalid_value_policy") if isinstance(fields.get("invalid_value_policy"), dict) else {}

    if is_transform:
        normalized_contract = artifact_contracts.get("normalized_csv") if isinstance(artifact_contracts.get("normalized_csv"), dict) else {}
        normalized_path = _trim(normalized_contract.get("path", ""), 200)
        if source_path and target_column and normalized_path:
            floor.append(
                f"Transform acceptance writes `{normalized_path}` from source `{source_path}` and only mutates target column `{target_column}`."
            )
        elif source_path and target_column:
            floor.append(
                f"Transform acceptance binds source `{source_path}` and target column `{target_column}` explicitly."
            )
        if accepted_formats and normalize_to:
            floor.append(
                "Normalization rules enumerate the only accepted month input formats "
                f"{', '.join(accepted_formats)} and normalize them to {normalize_to}; "
                "variants such as YYYY/M, YYYY-M, and YYYY.M stay anomalies instead of being normalized."
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
                f"`{normalized_path}` keeps the input row count unchanged, normalizes only valid `{target_column}` values, "
                f"and preserves invalid/null/empty/out-of-range `{target_column}` values exactly as requested"
            )
        if actions and final_rule:
            floor.append(final_rule + "; invalid handling must " + ", ".join(actions) + ".")
        elif final_rule:
            floor.append(final_rule + ".")
        elif actions:
            floor.append("Invalid, unparseable, or out-of-range month handling must " + ", ".join(actions) + ".")

    if is_schema and isinstance(artifact_contracts.get("schema_report"), dict):
        floor = [
            f"Schema report writes `{artifact_contracts['schema_report'].get('path', 'schema_report.json')}` as JSON.",
            "Schema evidence covers every transformed output column with name, inferred_type, type_rule, null_count, and observed_non_null_count.",
            "Schema coverage is for the transformed output, not a partial column subset.",
        ]
    elif is_null and isinstance(artifact_contracts.get("null_summary"), dict):
        floor = [
            f"Null summary writes `{artifact_contracts['null_summary'].get('path', 'null_summary.md')}` as markdown.",
            "Null/anomaly evidence summarizes affected columns, null counts, and invalid month examples from the transformed output.",
            "Null/anomaly handling preserves the requested row/value policy instead of silently rewriting evidence.",
        ]
    elif is_sample and isinstance(artifact_contracts.get("sample_output"), dict):
        floor = [
            f"Sample output writes `{artifact_contracts['sample_output'].get('path', 'sample_5.csv')}` as CSV.",
            "Sample evidence contains exactly five rows sampled from the transformed output.",
            "Sample rows are sufficient to inspect normalized month formatting and null/anomaly handling.",
        ]

    return _dedupe_rows([_trim(item, 240) for item in floor], limit=3)
