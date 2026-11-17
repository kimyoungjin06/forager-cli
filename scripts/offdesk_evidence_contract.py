"""Bind model-selected evidence IDs to host-reviewed source scopes.

The catalog is an explicit operator input, never inferred from model prose.
These checks establish source identity and declared evidence scope, not truth.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

KINDS = frozenset(
    {
        "external_history",
        "current_membership",
        "current_cohort_statistics",
        "fixed_cohort_history",
        "historical_partition",
        "annual_c2c",
        "method_contract",
        "scope_limit",
    }
)


class ContractError(ValueError):
    """A response cannot be bound to the selected host evidence contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _text(value: object, limit: int = 2000) -> str:
    _require(isinstance(value, str) and 0 < len(value) <= limit, "invalid text")
    return value


def _keys(value: object, expected: set[str]) -> None:
    _require(isinstance(value, dict) and set(value) == expected, "unknown or missing keys")


def _strings(value: object) -> list[str]:
    _require(isinstance(value, list) and 1 <= len(value) <= 20, "invalid text list")
    return [_text(item) for item in value]


def source_reference(
    source_id: str,
    source_text: str,
    expected_sha256: str,
    first: int,
    last: int,
    supplied_lines: set[int],
    max_lines: int = 13,
    max_quote: int = 8000,
) -> dict:
    """Extract a source reference without asserting an evidence kind or meaning."""
    _require(hashlib.sha256(source_text.encode()).hexdigest() == expected_sha256, "source drift")
    lines = source_text.splitlines()
    _require(type(first) is int and type(last) is int, "line numbers must be integers")
    _require(type(max_lines) is int and 1 <= max_lines <= 13, "invalid line bound")
    _require(type(max_quote) is int and 1 <= max_quote <= 8000, "invalid quote bound")
    _require(1 <= first <= last <= len(lines) and last - first < max_lines, "invalid range")
    _require(set(range(first, last + 1)) <= supplied_lines, "lines were not supplied")
    _require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", source_id)), "invalid source id")
    quote = "\n".join(lines[first - 1 : last])
    _require(len(quote) <= max_quote, "quote exceeds bound")
    return {
        "evidence_id": f"{source_id}:L{first}-L{last}",
        "source_id": source_id,
        "source_sha256": expected_sha256,
        "line_start": first,
        "line_end": last,
        "quote": quote,
    }


def source_evidence(
    source_id: str, source_text: str, expected_sha256: str,
    first: int, last: int, kind: str, supplied_lines: set[int],
) -> dict:
    """Construct a reference with the operator's separately declared scope."""
    reference = source_reference(source_id, source_text, expected_sha256, first, last, supplied_lines)
    _require(kind in KINDS, "undeclared evidence kind")
    _text(reference["quote"], 8000)
    return {
        **reference, "kind": kind,
        "kind_authority": "host_reviewed_source_scope",
        "scientific_acceptance": False,
    }


def _catalog(catalog: list[dict]) -> dict[str, dict]:
    _require(isinstance(catalog, list) and 1 <= len(catalog) <= 512, "invalid catalog size")
    result = {}
    expected = {
        "evidence_id", "source_id", "source_sha256", "line_start", "line_end",
        "kind", "quote", "kind_authority", "scientific_acceptance",
    }
    for item in catalog:
        _keys(item, expected)
        _require(item["kind"] in KINDS, "undeclared evidence kind")
        _require(item["kind_authority"] == "host_reviewed_source_scope", "unbound kind authority")
        _require(item["scientific_acceptance"] is False, "catalog grants acceptance")
        _require(bool(re.fullmatch(r"[0-9a-f]{64}", item["source_sha256"])), "invalid source hash")
        _require(type(item["line_start"]) is int and type(item["line_end"]) is int, "invalid lines")
        _require(1 <= item["line_start"] <= item["line_end"], "invalid range")
        expected_id = f"{item['source_id']}:L{item['line_start']}-L{item['line_end']}"
        _require(item["evidence_id"] == expected_id, "evidence id disagrees with range")
        _text(item["quote"], 8000)
        _require(expected_id not in result, "duplicate evidence id")
        result[expected_id] = item
    return result


def field_schema(field: str, catalog: list[dict]) -> dict:
    ids = list(_catalog(catalog))
    _text(field, 80)
    text = {"type": "string", "minLength": 1, "maxLength": 2000}
    text_list = {"type": "array", "minItems": 1, "maxItems": 20, "items": text}
    properties = {
        "field": {"type": "string", "enum": [field]},
        "summary": text,
        "evidence_ids": {
            "type": "array", "minItems": 1, "maxItems": 6,
            "uniqueItems": True, "items": {"type": "string", "enum": ids},
        },
        "unknowns": text_list,
        "alternatives": text_list,
        "next_draft_action": text,
        "scientific_acceptance": {"type": "boolean", "enum": [False]},
    }
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def bind_field(value: dict, field: str, catalog: list[dict]) -> dict:
    """Use host excerpts as evidence; free model prose remains review-pending notes."""
    refs = _catalog(catalog)
    _keys(value, set(field_schema(field, catalog)["properties"]))
    _require(value["field"] == field, "wrong field")
    _require(value["scientific_acceptance"] is False, "response grants acceptance")
    ids = value["evidence_ids"]
    _require(isinstance(ids, list) and 1 <= len(ids) <= 6, "invalid evidence selection")
    _require(all(isinstance(item, str) and item in refs for item in ids), "unknown evidence id")
    _require(len(set(ids)) == len(ids), "duplicate evidence selection")
    notes = {"summary": _text(value["summary"]), "unknowns": _strings(value["unknowns"]),
             "alternatives": _strings(value["alternatives"]),
             "next_draft_action": _text(value["next_draft_action"])}
    return {
        "schema": "offdesk_bound_evidence_field.v1", "field": field,
        **notes, "observations": [copy.deepcopy(refs[item]) for item in ids],
        "model_prose_status": "unverified_review_notes",
        "historical_partition_verified": False, "annual_c2c_verified": False,
        "scientific_acceptance": False, "canonical_plan_updated": False,
    }


def audit_legacy_field(value: dict, catalog: list[dict]) -> dict:
    """Quarantine conflicting old classifications without modifying or relabelling them."""
    refs = list(_catalog(catalog).values())
    _require(isinstance(value, dict) and isinstance(value.get("observations"), list), "invalid legacy field")
    kept, withheld = [], []
    for index, item in enumerate(value["observations"]):
        if not isinstance(item, dict):
            withheld.append({"index": index, "original": copy.deepcopy(item), "reason": "invalid observation"})
            continue
        first, last = item.get("line_start"), item.get("line_end")
        relevant = []
        valid = type(first) is int and type(last) is int and 1 <= first <= last and last - first <= 12
        if valid:
            relevant = [ref for ref in refs if ref["source_id"] == item.get("source_id")
                        and ref["line_start"] <= last and ref["line_end"] >= first]
            covered = {line for ref in relevant for line in range(ref["line_start"], ref["line_end"] + 1)}
            valid = set(range(first, last + 1)) <= covered
        matching = [ref for ref in relevant if ref["kind"] == item.get("kind")]
        covered = {line for ref in matching for line in range(ref["line_start"], ref["line_end"] + 1)}
        valid = valid and set(range(first, last + 1)) <= covered
        # A correct type cannot rescue a changed literal source quotation.
        exact_lines = {}
        for ref in matching:
            for line, text in enumerate(ref["quote"].splitlines(), ref["line_start"]):
                _require(line not in exact_lines or exact_lines[line] == text, "conflicting catalog text")
                exact_lines[line] = text
        if valid:
            exact = "\n".join(exact_lines[line] for line in range(first, last + 1))
            valid = isinstance(item.get("quote"), str) and bool(item["quote"]) and item["quote"] in exact
        if valid:
            kept.append({"index": index, "original": copy.deepcopy(item),
                         "evidence_ids": [ref["evidence_id"] for ref in matching]})
        else:
            withheld.append({"index": index, "original": copy.deepcopy(item),
                             "declared_source_kinds": sorted({ref["kind"] for ref in relevant}),
                             "reason": "unbound source range, conflicting kind or nonliteral quote"})
    return {"kept": kept, "withheld": withheld, "changed_original": False,
            "supports_scientific_truth": False, "scientific_acceptance": False}


def review_schema(fields: list[str]) -> dict:
    text = {"type": "string", "minLength": 1, "maxLength": 2000}
    finding = {"type": "object", "properties": {
        "field": {"type": "string", "enum": [*fields, "all"]}, "issue": text},
        "required": ["field", "issue"], "additionalProperties": False}
    props = {"verdict": {"type": "string", "enum": ["ready_for_ondesk_review", "rework", "blocked"]},
             "scope_limit": text, "findings": {"type": "array", "maxItems": 20, "items": finding},
             "scientific_acceptance": {"type": "boolean", "enum": [False]}}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def validate_review(value: dict, fields: list[str]) -> dict:
    _keys(value, set(review_schema(fields)["properties"]))
    _require(value["verdict"] in {"ready_for_ondesk_review", "rework", "blocked"}, "invalid review verdict")
    _require(value["scientific_acceptance"] is False, "review grants acceptance")
    _text(value["scope_limit"])
    _require(isinstance(value["findings"], list) and len(value["findings"]) <= 20, "invalid findings")
    for item in value["findings"]:
        _keys(item, {"field", "issue"})
        _require(item["field"] in [*fields, "all"], "unbound review field")
        _text(item["issue"])
    return copy.deepcopy(value)


def contract_sha256(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ollama_schema(schema: dict) -> dict:
    """Keep bounded-string expansion out of the sampler grammar.

    Repeating maxLength across bounded arrays can exceed llama.cpp's grammar
    complexity limit. The host validators retain all length and uniqueness
    checks; this projection is only the server's structural sampling contract.
    """
    if isinstance(schema, list):
        return [ollama_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    return {key: ollama_schema(value) for key, value in schema.items()
            if key not in {"minLength", "maxLength", "uniqueItems"}}


def build_review_context(fields: list[dict], max_bytes: int = 24576) -> dict:
    """Preserve every supplied reference and complete passage, or refuse the budget.

    Callers may explicitly prepare separate field contexts when a complete
    combined context does not fit. Reference order must not decide which
    evidence types a reviewer is allowed to see.
    """
    _require(isinstance(fields, list) and 1 <= len(fields) <= 32, "invalid review fields")
    _require(type(max_bytes) is int and 1 <= max_bytes <= 4 * 1024 * 1024, "invalid review budget")
    source_versions, evidence, selections, coverage = {}, {}, [], []
    seen_fields = set()
    for field in fields:
        _require(isinstance(field, dict), "invalid review field")
        name = _text(field.get("field"), 80)
        _require(name not in seen_fields, "duplicate review field")
        seen_fields.add(name)
        _require(field.get("scientific_acceptance") is False, "review input grants acceptance")
        refs = _catalog(field.get("observations"))
        for identifier, ref in refs.items():
            sid = ref["source_id"]
            _require(sid not in source_versions or source_versions[sid] == ref["source_sha256"],
                     "mixed source versions require distinct source ids")
            source_versions[sid] = ref["source_sha256"]
            _require(identifier not in evidence or evidence[identifier] == ref,
                     "conflicting shared evidence")
            evidence[identifier] = copy.deepcopy(ref)
        selections.append({
            "field": name, "summary": _text(field.get("summary")),
            "unknowns": _strings(field.get("unknowns")),
            "alternatives": _strings(field.get("alternatives")),
            "next_draft_action": _text(field.get("next_draft_action")),
            "evidence_ids": list(refs), "prose_status": "unverified_review_notes",
        })
        coverage.append({"field": name, "supplied_references": len(refs),
                         "emitted_references": len(refs),
                         "evidence_kinds": sorted({ref["kind"] for ref in refs.values()})})
    result = {
        "schema": "offdesk_complete_review_context.v1",
        "fields": selections, "evidence": list(evidence.values()),
        "source_versions": source_versions, "coverage": coverage,
        "omitted_references": [], "truncated_quotes": [], "truncated_notes": [],
        "scientific_acceptance": False, "launch_authorized": False,
        "claim_scope": "supplied evidence and review notes only; no independent source reproduction",
    }
    size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())
    _require(size <= max_bytes,
             f"complete review context requires {size} bytes; budget {max_bytes}; select explicit separate contexts")
    return result
