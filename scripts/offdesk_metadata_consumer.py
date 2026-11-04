"""Versioned, small local JSONL consumer for checkpoint integration qualification.

This is not the R91 Parquet worker or a scientific execution adapter. No network,
subprocess, scheduler approval or arbitrary command is invoked by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import offdesk_resume_checkpoint as gate

ACTIONS = ("compute", "reuse", "exclude")
MAX_ROWS = 4096
MAX_INPUT_BYTES = 2 * 1024 * 1024
LOADED_SOURCE = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOADED_VALIDATOR = hashlib.sha256(Path(gate.__file__).read_bytes()).hexdigest()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def now():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def source_identity():
    gate.require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == LOADED_SOURCE,
                 "loaded consumer source changed")
    gate.require(hashlib.sha256(Path(gate.__file__).read_bytes()).hexdigest() == LOADED_VALIDATOR,
                 "loaded validator source changed")


def configuration(value):
    gate.require(type(value) is dict, "consumer configuration object required")
    schema = value.get("schema")
    gate.require(schema in ("forager.metadata_consumer.v1", "forager.metadata_consumer.v2"),
                 "unsupported consumer config")
    gate.keys(value, {"schema", "batch_rows", "limits"} | ({"packet"} if schema.endswith("v2") else set()))
    gate.require(type(value["batch_rows"]) is int and 1 <= value["batch_rows"] <= 128, "invalid batch size")
    gate.limits_schema(value["limits"])
    if schema.endswith("v2"):
        binding = value["packet"]
        gate.keys(binding, {"manifest_sha256", "purpose", "source_vintage", "source_artifact_sha256",
                            "source_contract_sha256", "selected_rows_sha256", "policy_snapshot_sha256"})
        gate.require(binding["purpose"] == "frozen_embedding_query_member_denial_counts",
                     "unsupported packet purpose")
        gate.require(type(binding["source_vintage"]) is str and 0 < len(binding["source_vintage"]) <= 100,
                     "explicit source vintage required")
        for key in set(binding) - {"purpose", "source_vintage"}:
            gate.digest(binding[key])
    return value


def _input_paths(cfg):
    base = ["input.jsonl", "validator.py"]
    return base + (["adapter.py", "manifest.json", "row_provenance.jsonl", "policy.json"]
                   if cfg["schema"].endswith("v2") else [])


def _packet_context(fd, cfg, payload, expected=None):
    """Validate the exact composite packet, never infer a role from action text."""
    if cfg["schema"].endswith("v1"):
        for name in ["adapter.py", "manifest.json", "row_provenance.jsonl", "policy.json"]:
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            gate.require(False, "packet artifacts require an explicit v2 contract")
        return []
    import offdesk_metadata_packet as packet
    gate.require(hashlib.sha256(Path(packet.__file__).read_bytes()).hexdigest() == packet.LOADED_SOURCE,
                 "loaded packet adapter source changed")
    values = {}
    bindings = []
    for name in _input_paths(cfg)[2:]:
        bound = gate.MAX_JSON_BYTES if name == "manifest.json" else gate.MAX_FILE_BYTES
        if name in ("row_provenance.jsonl", "policy.json"):
            bound = packet.MAX_SELECTED_BYTES
        binding, raw = gate.read_file(fd, name, bound, True)
        if expected is not None:
            gate.require(binding == expected[name], "packet artifact changed before interpretation")
        bindings.append(binding)
        values[name] = raw
    gate.require(bindings[0]["sha256"] == packet.LOADED_SOURCE, "running packet adapter differs from copy")
    gate.require(bindings[1]["sha256"] == cfg["packet"]["manifest_sha256"], "packet manifest changed")
    manifest = gate.decode(values["manifest.json"])
    gate.require(type(manifest) is dict and type(manifest.get("source")) is dict, "packet source context required")
    for key, value in cfg["packet"].items():
        if key != "manifest_sha256":
            gate.require(manifest["source"].get(key) == value, "selected packet purpose/epoch/source differs")
    packet.verify_packet(payload, values["row_provenance.jsonl"], manifest, values["policy.json"])
    return bindings


def records(raw):
    gate.require(len(raw) <= MAX_INPUT_BYTES, "consumer input exceeds small-file bound")
    result = []
    seen = set()
    for index, line in enumerate(raw.splitlines()):
        gate.require(index < MAX_ROWS and len(line) <= 2048, "record count or line bound exceeded")
        row = gate.decode(line)
        gate.keys(row, {"work_id", "local_idx", "action"})
        wid = row["work_id"]
        gate.require(type(wid) is str and 2 <= len(wid) <= 25 and wid[0] == "W"
                     and wid[1:].isascii() and wid[1:].isdigit() and wid not in seen, "invalid or duplicate WorkID")
        gate.require(int(wid[1:]) > 0 and wid == "W" + str(int(wid[1:])), "noncanonical WorkID")
        gate.require(type(row["local_idx"]) is int and row["local_idx"] == index,
                     "local_idx does not match ordered roster")
        gate.require(type(row["action"]) is str and row["action"] in ACTIONS, "unknown action")
        seen.add(wid)
        result.append(row)
    return result


def state_schema(state):
    gate.keys(state, {"schema", "processed", "counts", "window_start", "boot_id"})
    gate.require(state["schema"] == "forager.metadata_checkpoint.v1", "unsupported checkpoint")
    gate.require(type(state["processed"]) is int and 0 <= state["processed"] <= MAX_ROWS, "invalid offset")
    gate.keys(state["counts"], set(ACTIONS))
    gate.require(all(type(v) is int and v >= 0 for v in state["counts"].values())
                 and sum(state["counts"].values()) == state["processed"], "invalid checkpoint counts")
    gate.require(type(state["window_start"]) in (int, float) and math.isfinite(state["window_start"])
                 and 0 <= state["window_start"] <= now(), "invalid window anchor")
    gate.require(state["boot_id"] == boot_id(), "boot identity changed")


def check_window(state, limits):
    gate.require(now() <= state["window_start"] + limits["total_seconds"], "original total window exhausted")


def _artifact(root, name, limit):
    with gate.root_handle(root) as fd:
        return gate.read_file(fd, name, limit, retain=True)


def _publish(root, path, value):
    with gate.root_handle(root) as fd:
        return gate.publish(fd, path, value)


def initialize(root):
    """Explicit creation only; a later initialize never replaces old artifacts."""
    start = now()
    source_identity()
    config_binding, raw = _artifact(root, "configuration.json", gate.MAX_JSON_BYTES)
    cfg = configuration(gate.decode(raw))
    implementation, _ = _artifact(root, "consumer.py", gate.MAX_FILE_BYTES)
    validator, _ = _artifact(root, "validator.py", gate.MAX_FILE_BYTES)
    gate.require(implementation["sha256"] == LOADED_SOURCE and validator["sha256"] == LOADED_VALIDATOR,
                 "running implementation differs from declared source")
    input_binding, data = _artifact(root, "input.jsonl", MAX_INPUT_BYTES)
    records(data)
    with gate.root_handle(root) as fd:
        packet_bindings = _packet_context(fd, cfg, data)
    state = {"schema": "forager.metadata_checkpoint.v1", "processed": 0,
             "counts": dict.fromkeys(ACTIONS, 0), "window_start": start, "boot_id": boot_id()}
    _publish(root, "seed.checkpoint.json", state)
    contract_pin = gate.freeze(root, "seed.contract.json", stage_id="metadata_policy_bridge",
                              implementation="consumer.py", configuration="configuration.json",
                              inputs=_input_paths(cfg), checkpoints=["seed.checkpoint.json"],
                              limits=cfg["limits"])
    with gate.root_handle(root) as fd:
        contract = gate.bound_json(fd, "seed.contract.json", contract_pin)
        gate.require(contract["configuration"] == config_binding and contract["implementation"] == implementation
                     and contract["inputs"] == [input_binding, validator] + packet_bindings, "initial source drift")
    _admit_contract_packet(root, "seed.contract.json", contract_pin)
    end = now()
    gate.require(end - start <= cfg["limits"]["work_seconds"], "initial work deadline exceeded")
    check_window(state, cfg["limits"])
    receipt_pin, publication, publication_pin = gate.publish_observed_completion(root, "seed.contract.json", contract_pin, "seed.receipt.json",
                                         [{"attempt_id": "seed", "started_at": start, "work_ended_at": end,
                                           "ended_at": end, "outcome": "completed"}], state["window_start"], state["boot_id"])
    check_window(state, cfg["limits"])
    return {"status": "initialized", "contract": "seed.contract.json", "contract_sha256": contract_pin,
            "receipt": "seed.receipt.json", "receipt_sha256": receipt_pin, "processed": 0,
            "publication": publication, "publication_sha256": publication_pin,
            "consumer_schema": cfg["schema"], "packet_semantics_verified": cfg["schema"].endswith("v2"),
            "scientific_acceptance": False}


def _admit(root, contract_path, contract_pin, receipt_path, receipt_pin, publication=None, publication_pin=None):
    source_identity()
    gate.inspect_resume(root, contract_path, contract_pin, receipt_path, receipt_pin)
    with gate.root_handle(root) as fd:
        contract = gate.bound_json(fd, contract_path, contract_pin)
        gate.require(contract["implementation"]["path"] == "consumer.py"
                     and contract["configuration"]["path"] == "configuration.json"
                     and len(contract["inputs"]) >= 2
                     and len(contract["checkpoints"]) == 1, "consumer artifact scope mismatch")
        gate.require(contract["implementation"]["sha256"] == LOADED_SOURCE
                     and contract["inputs"][1]["sha256"] == LOADED_VALIDATOR, "implementation identity mismatch")
        cfg_binding, cfg_raw = gate.read_file(fd, "configuration.json", gate.MAX_JSON_BYTES, True)
        gate.require(cfg_binding == contract["configuration"], "configuration bytes changed")
        cfg = configuration(gate.decode(cfg_raw))
        gate.require([v["path"] for v in contract["inputs"]] == _input_paths(cfg),
                     "consumer packet inputs omitted or downgraded")
        gate.require(cfg["limits"] == contract["limits"], "contract and configuration limits differ")
        state_binding, state_raw = gate.read_file(fd, contract["checkpoints"][0]["path"], gate.MAX_JSON_BYTES, True)
        gate.require(state_binding == contract["checkpoints"][0], "checkpoint bytes changed")
        state = gate.decode(state_raw)
        binding, raw = gate.read_file(fd, "input.jsonl", MAX_INPUT_BYTES, True)
        gate.require(binding == contract["inputs"][0], "input changed before consumption")
        _packet_context(fd, cfg, raw, {v["path"]: v for v in contract["inputs"]})
        gate.verify_artifacts(fd, contract)
    state_schema(state)
    observed = gate.inspect_publication(root, contract_path, contract_pin, receipt_path, receipt_pin, publication, publication_pin)
    gate.require(observed["window_start"] == state["window_start"] and observed["boot_id"] == state["boot_id"],
                 "publication changed original window or boot")
    parsed = records(raw)
    gate.require(state["processed"] <= len(parsed), "checkpoint exceeds input")
    prefix_counts = dict.fromkeys(ACTIONS, 0)
    for row in parsed[:state["processed"]]:
        prefix_counts[row["action"]] += 1
    gate.require(prefix_counts == state["counts"], "checkpoint prefix counts disagree with input")
    check_window(state, cfg["limits"])
    return cfg, state, raw


def _admit_contract_packet(root, path, pin):
    """Revalidate source and semantics at publication, without an existing receipt."""
    source_identity()
    with gate.root_handle(root) as fd:
        contract = gate.bound_json(fd, path, pin)
        gate.verify_artifacts(fd, contract)
        binding, raw = gate.read_file(fd, "configuration.json", gate.MAX_JSON_BYTES, True)
        gate.require(binding == contract["configuration"], "configuration changed at publication")
        cfg = configuration(gate.decode(raw))
        gate.require([v["path"] for v in contract["inputs"]] == _input_paths(cfg), "packet inputs changed")
        binding, payload = gate.read_file(fd, "input.jsonl", MAX_INPUT_BYTES, True)
        gate.require(binding == contract["inputs"][0], "payload changed at publication")
        _packet_context(fd, cfg, payload, {v["path"]: v for v in contract["inputs"]})
        gate.verify_artifacts(fd, contract)


def consume_batch(root, contract_path, contract_pin, receipt_path, receipt_pin, publication=None, publication_pin=None):
    """One explicit batch. An unresolved entry cannot be replayed automatically."""
    start = now()
    cfg, state, raw = _admit(root, contract_path, contract_pin, receipt_path, receipt_pin, publication, publication_pin)
    offset = state["processed"]
    rows = records(raw)
    if offset == len(rows):
        return {"status": "complete", "processed": offset, "counts": state["counts"],
                "contract": contract_path, "contract_sha256": contract_pin,
                "receipt": receipt_path, "receipt_sha256": receipt_pin, "scientific_acceptance": False,
                "publication": publication, "publication_sha256": publication_pin,
                "consumer_schema": cfg["schema"], "packet_semantics_verified": cfg["schema"].endswith("v2")}
    entry = "entry-" + str(offset) + ".json"
    _publish(root, entry, {"schema": "forager.metadata_entry.v1", "processed": offset,
                          "contract_sha256": contract_pin, "receipt_sha256": receipt_pin})
    # Reobserve after the durable reservation and immediately before computation.
    fresh_cfg, fresh_state, fresh_raw = _admit(root, contract_path, contract_pin, receipt_path, receipt_pin, publication, publication_pin)
    gate.require((fresh_cfg, fresh_state, fresh_raw) == (cfg, state, raw), "entry source changed")
    stop = min(offset + cfg["batch_rows"], len(rows))
    counts = dict(state["counts"])
    for row in rows[offset:stop]:
        counts[row["action"]] += 1
        gate.require(now() - start <= cfg["limits"]["work_seconds"], "work deadline exceeded")
        check_window(state, cfg["limits"])
    prefix = "batch-" + str(offset) + "-" + str(stop)
    checkpoint_path = prefix + ".checkpoint.json"
    updated = {**state, "processed": stop, "counts": counts}
    gate.inspect_resume(root, contract_path, contract_pin, receipt_path, receipt_pin)
    _publish(root, checkpoint_path, updated)
    next_contract = prefix + ".contract.json"
    pin = gate.freeze(root, next_contract, stage_id="metadata_policy_bridge",
                      implementation="consumer.py", configuration="configuration.json",
                      inputs=_input_paths(cfg), checkpoints=[checkpoint_path], limits=cfg["limits"])
    gate.inspect_resume(root, contract_path, contract_pin, receipt_path, receipt_pin)
    _admit_contract_packet(root, next_contract, pin)
    end = now()
    gate.require(end - start <= cfg["limits"]["work_seconds"], "work deadline exceeded")
    check_window(state, cfg["limits"])
    next_receipt = prefix + ".receipt.json"
    next_pin, next_publication, next_publication_pin = gate.publish_observed_completion(root, next_contract, pin, next_receipt,
                                      [{"attempt_id": prefix, "started_at": start, "work_ended_at": end,
                                        "ended_at": end, "outcome": "completed"}], state["window_start"], state["boot_id"])
    check_window(state, cfg["limits"])
    return {"status": "complete" if stop == len(rows) else "checkpointed", "processed": stop,
            "counts": counts, "contract": next_contract, "contract_sha256": pin,
            "receipt": next_receipt, "receipt_sha256": next_pin, "scientific_acceptance": False,
            "publication": next_publication, "publication_sha256": next_publication_pin,
            "consumer_schema": cfg["schema"], "packet_semantics_verified": cfg["schema"].endswith("v2")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "batch"])
    parser.add_argument("root")
    parser.add_argument("--contract")
    parser.add_argument("--contract-sha256")
    parser.add_argument("--receipt")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--publication")
    parser.add_argument("--publication-sha256")
    args = parser.parse_args()
    if args.action == "initialize":
        gate.require(not any([args.contract, args.contract_sha256, args.receipt, args.receipt_sha256, args.publication, args.publication_sha256]),
                     "initialize cannot accept prior resume identity")
        result = initialize(args.root)
    else:
        gate.require(all([args.contract, args.contract_sha256, args.receipt, args.receipt_sha256, args.publication, args.publication_sha256]),
                     "explicit prior contract and receipt pins required")
        result = consume_batch(args.root, args.contract, args.contract_sha256, args.receipt, args.receipt_sha256, args.publication, args.publication_sha256)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

