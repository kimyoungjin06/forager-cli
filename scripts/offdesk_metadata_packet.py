"""Prepare copied-input compatibility evidence without starting a consumer.

Only frozen embedding query/member denial maps to engineering 'exclude'.
P0 actions, original indexes, vector presence and diagnostic permission remain
separate in a hash-bound provenance sidecar. Legacy v1 does not enforce it;
packet preparation never declares a packet ready for launch.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import offdesk_resume_checkpoint as gate
import offdesk_metadata_consumer as consumer

PURPOSE = "frozen_embedding_query_member_denial_counts"
P0_ACTIONS = {1: "compute", 2: "reuse_pending_vector_id_verification",
              3: "exclude_text_quality", 4: "exclude_frozen_ineligible_no_20260330_vector"}
MAX_SOURCE_ROWS = 4096
MAX_SELECTED_BYTES = 2 * 1024 * 1024
LOADED_SOURCE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
CONTEXT_KEYS = {"purpose", "source_vintage", "source_artifact_sha256", "source_contract_sha256",
                "selected_rows_sha256", "policy_snapshot_sha256", "selection_start", "selection_rows"}


def canonical_id(value):
    gate.require(type(value) is str and 2 <= len(value) <= 25 and value[0] == "W"
                 and value[1:].isascii() and value[1:].isdigit()
                 and int(value[1:]) > 0 and value == "W" + str(int(value[1:])), "noncanonical WorkID")
    return value


def build_packet(selected_rows_raw, policy_raw, context):
    gate.keys(context, CONTEXT_KEYS)
    gate.require(context["purpose"] == PURPOSE, "unsupported interpretation purpose")
    gate.require(type(context["source_vintage"]) is str and 0 < len(context["source_vintage"]) <= 100,
                 "explicit source vintage required")
    for name in ["source_artifact_sha256", "source_contract_sha256", "selected_rows_sha256", "policy_snapshot_sha256"]:
        gate.digest(context[name])
    gate.require(type(selected_rows_raw) is bytes and len(selected_rows_raw) <= MAX_SELECTED_BYTES,
                 "selected rows exceed local bound")
    gate.require(type(policy_raw) is bytes and len(policy_raw) <= MAX_SELECTED_BYTES, "policy exceeds local bound")
    gate.require(hashlib.sha256(selected_rows_raw).hexdigest() == context["selected_rows_sha256"], "selected source drift")
    gate.require(hashlib.sha256(policy_raw).hexdigest() == context["policy_snapshot_sha256"], "policy source drift")
    gate.require(type(context["selection_start"]) is int and context["selection_start"] >= 0,
                 "explicit selection origin required")
    gate.require(type(context["selection_rows"]) is int and 1 <= context["selection_rows"] <= MAX_SOURCE_ROWS,
                 "invalid selected row count")
    policy = gate.decode(policy_raw)
    gate.require(type(policy) is dict and policy.get("schema") == "incremental_embedding_usage_exclusion_v1",
                 "unsupported policy schema")
    gate.require(policy.get("query_policy") == policy.get("member_policy") == "deny",
                 "query/member denial scope required")
    gate.require(type(policy.get("diagnostic_compute_allowed")) is bool
                 and type(policy.get("live_applied")) is bool, "explicit diagnostic/live state required")
    gate.require(type(policy.get("denied_work_ids")) is list and len(policy["denied_work_ids"]) <= 8192,
                 "bounded denied-ID roster required")
    denied = [canonical_id(w) for w in policy["denied_work_ids"]]
    gate.require(len(set(denied)) == len(denied), "duplicate denied ID")
    lines = selected_rows_raw.splitlines()
    gate.require(len(lines) == context["selection_rows"], "selection count drift")
    output = []
    provenance = []
    seen = set()
    for index, line in enumerate(lines):
        row = gate.decode(line)
        gate.keys(row, {"work_id", "publication_year", "local_idx", "p0_action", "vector_present"})
        wid = canonical_id(row["work_id"])
        gate.require(wid not in seen and wid in denied, "unbound or duplicate selected WorkID")
        seen.add(wid)
        for field in ["publication_year", "local_idx"]:
            gate.require(row[field] is None or (type(row[field]) is int and row[field] >= 0),
                         "invalid source year/index")
        gate.require(row["p0_action"] is None or (type(row["p0_action"]) is int
                     and row["p0_action"] in P0_ACTIONS), "unknown P0 action")
        gate.require(type(row["vector_present"]) is bool, "explicit vector-presence flag required")
        output.append({"work_id": wid, "local_idx": index, "action": "exclude"})
        provenance.append({"consumer_local_idx": index, "source_row_ordinal": context["selection_start"] + index,
                           "source_row": row, "p0_action_name": P0_ACTIONS.get(row["p0_action"], "unknown"),
                           "reason": "frozen_embedding_query_member_denial",
                           "diagnostic_compute_allowed": policy["diagnostic_compute_allowed"],
                           "work_removal_authorized": False, "bc_exclusion_authorized": False})
    payload_raw = b"".join(gate.encode(r) for r in output)
    provenance_raw = b"".join(gate.encode(r) for r in provenance)
    gate.require(b"".join(gate.encode(r["source_row"]) for r in provenance) == selected_rows_raw,
                 "selected derivative rows must use explicit canonical JSONL")
    consumer.records(payload_raw)
    manifest = {"schema": "forager.metadata_compatibility_packet.v1", "source": context,
                "input_sha256": hashlib.sha256(payload_raw).hexdigest(),
                "provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
                "rows": len(output), "policy_live_applied_in_snapshot": policy["live_applied"],
                "diagnostic_compute_allowed_in_snapshot": policy["diagnostic_compute_allowed"],
                "consumer_index_is_selection_ordinal_not_source_local_idx": True,
                "source_rows_reconstructable": True, "syntax_compatible": True,
                "runtime_provenance_enforced": False, "ready_for_launch": False,
                "scientific_acceptance": False, "work_or_bc_removal_authorized": False}
    return payload_raw, provenance_raw, manifest


def verify_packet(payload_raw, provenance_raw, manifest, policy_raw):
    """Verify prepared bytes and source-row pairing, without granting execution."""
    gate.keys(manifest, {"schema", "source", "input_sha256", "provenance_sha256", "rows",
                        "policy_live_applied_in_snapshot", "diagnostic_compute_allowed_in_snapshot",
                        "consumer_index_is_selection_ordinal_not_source_local_idx", "source_rows_reconstructable",
                        "syntax_compatible", "runtime_provenance_enforced", "ready_for_launch",
                        "scientific_acceptance", "work_or_bc_removal_authorized"})
    gate.keys(manifest["source"], CONTEXT_KEYS)
    gate.require(type(payload_raw) is bytes and type(provenance_raw) is bytes
                 and len(payload_raw) <= MAX_SELECTED_BYTES and len(provenance_raw) <= MAX_SELECTED_BYTES,
                 "packet exceeds local bound")
    gate.require(manifest["schema"] == "forager.metadata_compatibility_packet.v1"
                 and manifest["source"]["purpose"] == PURPOSE, "unsupported packet interpretation")
    gate.require(type(policy_raw) is bytes and len(policy_raw) <= MAX_SELECTED_BYTES
                 and hashlib.sha256(policy_raw).hexdigest() == manifest["source"]["policy_snapshot_sha256"],
                 "policy bytes changed")
    policy = gate.decode(policy_raw)
    gate.require(type(policy) is dict, "policy object required")
    gate.require(policy.get("schema") == "incremental_embedding_usage_exclusion_v1"
                 and policy.get("query_policy") == policy.get("member_policy") == "deny",
                 "policy interpretation changed")
    gate.require(type(policy.get("live_applied")) is bool
                 and type(policy.get("diagnostic_compute_allowed")) is bool
                 and manifest["policy_live_applied_in_snapshot"] is policy["live_applied"]
                 and manifest["diagnostic_compute_allowed_in_snapshot"] is policy["diagnostic_compute_allowed"],
                 "policy state relabelled")
    gate.require(hashlib.sha256(payload_raw).hexdigest() == manifest["input_sha256"]
                 and hashlib.sha256(provenance_raw).hexdigest() == manifest["provenance_sha256"],
                 "prepared packet bytes changed")
    rows = consumer.records(payload_raw)
    provenance = [gate.decode(line) for line in provenance_raw.splitlines()]
    gate.require(len(rows) == len(provenance) == manifest["rows"], "packet row counts disagree")
    reconstructed = []
    for i, (row, source) in enumerate(zip(rows, provenance)):
        gate.require(source["consumer_local_idx"] == i and row["local_idx"] == i
                     and source["source_row_ordinal"] == manifest["source"]["selection_start"] + i,
                     "provenance ordinal mismatch")
        gate.require(row["work_id"] == source["source_row"]["work_id"] and row["action"] == "exclude",
                     "source/normalized row mismatch")
        gate.require(source["reason"] == "frozen_embedding_query_member_denial"
                     and source["work_removal_authorized"] is False
                     and source["bc_exclusion_authorized"] is False, "policy distinction lost")
        gate.require(type(source["diagnostic_compute_allowed"]) is bool
                     and source["diagnostic_compute_allowed"] is manifest["diagnostic_compute_allowed_in_snapshot"],
                     "diagnostic permission changed")
        gate.require(source["p0_action_name"] == P0_ACTIONS.get(source["source_row"]["p0_action"], "unknown"),
                     "P0 action relabelled")
        reconstructed.append(source["source_row"])
    gate.require(manifest["runtime_provenance_enforced"] is False and manifest["ready_for_launch"] is False
                 and manifest["scientific_acceptance"] is False
                 and manifest["work_or_bc_removal_authorized"] is False, "preparation cannot grant execution")
    gate.require(manifest["consumer_index_is_selection_ordinal_not_source_local_idx"] is True
                 and manifest["source_rows_reconstructable"] is True and manifest["syntax_compatible"] is True,
                 "unsupported index or provenance claim")
    gate.require(hashlib.sha256(b"".join(gate.encode(r) for r in reconstructed)).hexdigest()
                 == manifest["source"]["selected_rows_sha256"], "reconstructed source rows changed")
    expected_payload, expected_provenance, expected_manifest = build_packet(
        b"".join(gate.encode(r) for r in reconstructed), policy_raw, manifest["source"])
    gate.require(payload_raw == expected_payload and provenance_raw == expected_provenance
                 and gate.encode(manifest) == gate.encode(expected_manifest),
                 "packet interpretation differs from source and policy")
    return reconstructed
