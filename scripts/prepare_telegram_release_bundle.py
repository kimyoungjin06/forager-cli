#!/usr/bin/env python3
"""Stage a bounded Telegram package. Never installs or controls services."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def prepare(source, inventory, installed, output, *, installed_binary):
    source, installed = source.resolve(), installed.resolve()
    installed_binary = installed_binary.resolve(strict=True)
    if not installed_binary.is_file():
        raise ValueError("installed binary must be a regular file")
    manifest = json.loads(inventory.read_text())
    if git(source, "rev-parse", "HEAD") != manifest["baseline"]:
        raise ValueError("candidate baseline mismatch")
    changed = set(git(source, "diff", "--name-only", "HEAD").splitlines())
    changed.update(git(source, "ls-files", "--others", "--exclude-standard").splitlines())
    if changed != set(manifest["files"]):
        raise ValueError("candidate file set changed")
    for name, expected in manifest["files"].items():
        if digest(source / name) != expected:
            raise ValueError(f"candidate hash mismatch: {name}")
    binary = source / "target/release/forager"
    if not binary.is_file():
        raise ValueError("optimized binary missing")
    names = ["scripts/offdesk_remote_operator_telegram.py", "scripts/offdesk_llm_endpoint.py"]
    names += sorted(str(p.relative_to(source)) for p in (source / "scripts/telegram_operator").glob("*.py"))
    source_names = sorted(set(git(source, "ls-files").splitlines()) | changed)
    source_hashes = {n: digest(source / n) for n in source_names if (source / n).is_file()}
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    installed_differences = []
    for name in names:
        target = output / "candidate" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
        files[str(target.relative_to(output))] = digest(target)
        old = installed / Path(name).relative_to("scripts")
        if old.is_file():
            backup = output / "rollback-original" / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, backup)
            files[str(backup.relative_to(output))] = digest(backup)
        if not old.is_file() or digest(old) != digest(target):
            installed_differences.append(name)
    for name, origin in (("candidate/bin/forager", binary),
                         ("rollback-original/bin/forager", installed_binary)):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
        files[name] = digest(target)
    for name in ("prepare_telegram_release_bundle.py", "telegram_rollback_guard.py", "forager_frontend_maintenance.py"):
        target = output / "tools" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(__file__).with_name(name), target)
        files[str(target.relative_to(output))] = digest(target)
    receipt = {
        "schema": "forager_telegram_staged_bundle.v1",
        "deployment_ready": False,
        "baseline": manifest["baseline"],
        "delta_inventory": manifest,
        "source_files": source_hashes,
        "files": files,
        "installed_differences": installed_differences,
        "installed_binary": str(installed_binary),
        "build_command": "cargo build --release --locked --bin forager",
        "rustc": subprocess.check_output(["rustc", "-Vv"], text=True).strip(),
        "cargo": subprocess.check_output(["cargo", "-V"], text=True).strip(),
        "rollback_policy": "stop listener; preserve current state; never replay held events with legacy code",
        "not_included": ["credentials", "live state", "service changes", "unrelated harness scripts"],
    }
    (output / "manifest.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    for name, expected in files.items():
        if digest(output / name) != expected:
            raise ValueError(f"staged hash mismatch: {name}")
    for name, expected in source_hashes.items():
        if digest(source / name) != expected:
            raise ValueError(f"source drift while packaging: {name}")
    return {"bundle": str(output), "payload_files": len(files),
            "candidate_script_files": len(names), "changed_installed_scripts": len(installed_differences),
            "manifest_sha256": digest(output / "manifest.json"), "deployment_ready": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--installed-scripts", type=Path, required=True)
    parser.add_argument("--installed-binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.inventory, args.installed_scripts, args.output,
                             installed_binary=args.installed_binary), indent=2))
