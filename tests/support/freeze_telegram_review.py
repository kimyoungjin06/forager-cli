"""Freeze review evidence only. Never installs, starts services, or sends messages.

The source inventory is intentionally explicit; it is not a clean release or
a complete rebuild archive. Run from the source tree with a new output path.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import time


ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory():
    paths = {ROOT / "Cargo.toml", ROOT / "Cargo.lock"}
    for directory in ("src", "scripts", "tests", "xtask"):
        paths.update(
            path for path in (ROOT / directory).rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.suffix in {".rs", ".py", ".toml", ".txt"}
            and "__pycache__" not in path.parts and "target" not in path.relative_to(ROOT).parts
        )
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    before = inventory()
    receipts = []

    def run(command):
        started = time.time()
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
        log = out / f"check-{len(receipts):02d}.log"
        log.write_text(result.stdout + result.stderr)
        receipt = {"command": command, "exit_code": result.returncode,
                   "elapsed_seconds": round(time.time() - started, 3),
                   "log": log.name, "sha256": digest(log)}
        receipts.append(receipt)
        print(json.dumps(receipt), flush=True)
        return result

    for _ in range(3):
        run(["cargo", "test", "--test", "remote_operator_telegram", "waiting_", "--", "--test-threads=1"])
    run(["cargo", "test", "--test", "remote_operator_notify_policy", "--test", "telegram_waiting_notification_safety"])
    run(["cargo", "test", "--test", "remote_operator_session_input",
         "semantic_prompt_identity_ignores_tui_churn_but_rejects_changed_action", "--", "--exact"])
    run(["cargo", "test", "--test", "remote_operator_session_input",
         "telegram_session_cards_separate_permission_from_next_task_continuation", "--", "--exact"])
    run(["cargo", "fmt", "--all", "--", "--check"])
    run(["cargo", "clippy", "--all-targets", "--", "-D", "warnings"])
    build = run(["cargo", "build", "--locked", "--bin", "forager"])
    artifacts = {}
    if build.returncode == 0:
        binary = out / "forager-debug-review"
        shutil.copy2(ROOT / "target/debug/forager", binary)
        artifacts[binary.name] = digest(binary)
        help_result = run([str(binary), "status", "--help"])
        if "--notification-capture" not in help_result.stdout:
            receipts[-1]["capability_missing"] = True
    archive = out / "source-review.tar.gz"
    with tarfile.open(archive, "w:gz") as target:
        for relative in before:
            target.add(ROOT / relative, arcname=relative, recursive=False)
    artifacts[archive.name] = digest(archive)
    archive_matches = True
    with tarfile.open(archive, "r:gz") as source:
        for relative, expected in before.items():
            stream = source.extractfile(relative)
            archive_matches &= stream is not None and hashlib.file_digest(stream, "sha256").hexdigest() == expected
    after = inventory()
    installed = Path.home() / ".local/share/forager/scripts"
    script_changes = []
    for relative, candidate_hash in before.items():
        if not relative.startswith("scripts/"):
            continue
        live = installed / relative.removeprefix("scripts/")
        if not live.is_file() or digest(live) != candidate_hash:
            script_changes.append(relative)
    report = {
        "schema": "telegram_review_bundle.v1", "deployable": False,
        "reason": "mixed_worktree_requires_scope_review_not_an_install_bundle",
        "profile": "debug-review-only", "source_hashes": before,
        "source_unchanged": before == after, "archive_matches": bool(archive_matches),
        "checks_passed": all(row["exit_code"] == 0 and not row.get("capability_missing") for row in receipts),
        "checks": receipts, "artifacts": artifacts,
        "candidate_scripts_different_or_missing_in_install": script_changes,
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "git_status": subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).splitlines(),
    }
    (out / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"manifest": str(out / "manifest.json"),
                      "source_unchanged": report["source_unchanged"],
                      "archive_matches": report["archive_matches"],
                      "checks_passed": report["checks_passed"],
                      "script_difference_count": len(script_changes), "deployable": False}), flush=True)
    return 0 if report["checks_passed"] and report["source_unchanged"] and archive_matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
