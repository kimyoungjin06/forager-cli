"""Independent finite-vector checker. This supplies no stream decoder."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def load_target(root):
    target = root / "scripts/offdesk_stream_frames.py"
    if not target.is_file() or target.is_symlink():
        raise ValueError("Expected a regular model-authored helper at the exact path")
    spec = importlib.util.spec_from_file_location("selected_stream_helper", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.StreamFrames, hashlib.sha256(target.read_bytes()).hexdigest()


def partitions(payload, style):
    if style == "whole":
        return [payload]
    width = 1 if style == "bytewise" else 3
    return [payload[i:i + width] for i in range(0, len(payload), width)]


def execute(factory, case, style):
    decoder = factory(mode=case["mode"], max_bytes=case["max_bytes"])
    actual = []
    peak_reported = 0
    prior_drops = 0

    def observe(frames):
        nonlocal peak_reported, prior_drops
        assert type(frames) is list and all(type(item) is bytes for item in frames), "list[bytes] required"
        actual.extend(frames)
        size, drops = decoder.buffered_bytes, decoder.dropped_events
        assert type(size) is int and 0 <= size <= 2 * case["max_bytes"] + 3, "reported retention bound"
        assert type(drops) is int and drops >= prior_drops, "monotonic drop count"
        peak_reported, prior_drops = max(peak_reported, size), drops

    observe(decoder.feed(b""))
    for chunk in partitions(bytes.fromhex(case["payload_hex"]), style):
        observe(decoder.feed(chunk))
        observe(decoder.feed(b""))
    assert [item.hex() for item in actual] == case["expected_before_finish_hex"], "dispatch must occur before EOF"
    observe(decoder.finish())
    assert [item.hex() for item in actual] == case["expected_frames_hex"], "exact dispatched payload mismatch"
    assert decoder.dropped_events == case["expected_drops"], "exact drop count mismatch"
    assert decoder.buffered_bytes == 0, "EOF must release pending inspection bytes"
    assert decoder.finish() == [], "finish must be idempotent"
    try:
        decoder.feed(b"data: forbidden-after-finish\n\n")
    except ValueError:
        pass
    else:
        raise AssertionError("feed after finish must fail")
    return {"frames": len(actual), "drops": decoder.dropped_events,
            "peak_reported_buffered_bytes": peak_reported}


def check_independence(factory):
    a = factory(mode="sse", max_bytes=65536)
    b = factory(mode="ndjson", max_bytes=65536)
    assert a.feed(b"data: A\n") == []
    assert b.feed(b'{"done":') == []
    assert a.feed(b"\n") == [b"A"]
    assert b.feed(b"true}\n") == [b'{"done":true}']
    assert a.finish() == b.finish() == []


def run(root, vectors):
    rows = []
    report = {"schema": "forager_stream_helper_checks.v1", "source_root": str(root),
              "implementation_patch_supplied": False, "real_model_calls": 0,
              "fixture_data": True, "qualification_complete": False,
              "coverage": "finite vectors; reported retention needs independent code audit",
              "cases": rows}
    try:
        factory, digest = load_target(root)
        report["target_sha256"] = digest
    except Exception as error:
        report.update(status="target_unavailable", tests_run=0, passed=0, failed=0,
                      errors=1, error_class=type(error).__name__)
        return report, 2
    for case in vectors["cases"]:
        for style in ("whole", "bytewise", "three-byte"):
            row = {"id": case["id"] + "/" + style}
            try:
                row.update(execute(factory, case, style), passed=True)
            except Exception as error:
                row.update(passed=False, error_class=type(error).__name__)
            rows.append(row)
    row = {"id": "independent-instances"}
    try:
        check_independence(factory)
        row["passed"] = True
    except Exception as error:
        row.update(passed=False, error_class=type(error).__name__)
    rows.append(row)
    passed = sum(row["passed"] for row in rows)
    report.update(status="passed" if passed == len(rows) else "failed", tests_run=len(rows),
                  passed=passed, failed=len(rows) - passed, errors=0)
    return report, 0 if passed == len(rows) and rows else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--vectors", type=Path, default=Path(__file__).with_name("HELPER_VECTORS.json"))
    parser.add_argument("--json-report", required=True, type=Path)
    args = parser.parse_args()
    vectors = json.loads(args.vectors.read_text())
    assert vectors["schema"] == "forager_stream_helper_vectors.v1" and vectors["cases"]
    assert len({case["id"] for case in vectors["cases"]}) == len(vectors["cases"])
    report, code = run(args.source_root.resolve(), vectors)
    args.json_report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "tests_run", "passed", "failed", "errors")}))
    sys.exit(code)
