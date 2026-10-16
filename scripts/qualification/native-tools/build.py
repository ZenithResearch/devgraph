#!/usr/bin/env python3
"""Build qualification-only tools from explicit clean sources; never provision or sign."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

PINS = {
    "devgraph": "97642d3feb0695d9943053010db6948e2c53bc99",
    "secs": "254f744d3151f234e6cc58db239c694937a0838e",
}
BINARIES = ("qualification-authority-admin", "qualification-native-client")


def checked_source(path, revision):
    path = path.resolve(strict=True)
    head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"], text=True
    )
    if head != revision or dirty:
        raise ValueError("Clean checkout at the exact runtime source revision required")
    return path


def prepare(template_root, devgraph, secs, output):
    devgraph = checked_source(devgraph, PINS["devgraph"])
    secs = checked_source(secs, PINS["secs"])
    if output.exists() or output.is_symlink():
        raise ValueError("A fresh build output directory is required")
    template = (template_root / "Cargo.toml.in").read_text()
    if template.count("@DEVGRAPH_ROOT@") != 3 or template.count("@SECS_ROOT@") != 1:
        raise ValueError("Unexpected qualification source placeholders")
    # JSON escaping is also valid for the Unix paths in these TOML basic strings.
    for placeholder, path in [("@DEVGRAPH_ROOT@", devgraph), ("@SECS_ROOT@", secs)]:
        template = template.replace('"' + placeholder, json.dumps(str(path))[:-1])
    if "@DEVGRAPH_ROOT@" in template or "@SECS_ROOT@" in template:
        raise ValueError("Unresolved source placeholders")
    output.mkdir(mode=0o700)
    (output / "Cargo.toml").write_text(template)
    shutil.copyfile(template_root / "Cargo.lock", output / "Cargo.lock")
    shutil.copytree(template_root / "src", output / "src")
    return output / "Cargo.toml"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(template_root, devgraph, secs, output, offline=False):
    manifest = prepare(template_root, devgraph, secs, output)
    base = ["cargo", "+1.96.0"]
    common = ["--manifest-path", str(manifest), "--locked", "--target-dir", str(output / "target")]
    if offline:
        common.append("--offline")
    for command in [
        base + ["test", *common, "--all-targets"],
        base + ["clippy", *common, "--all-targets", "--", "-D", "warnings"],
        base + ["build", *common, "--release", "--bins"],
    ]:
        subprocess.run(command, cwd=output, check=True)
    # New ignored build output must not mask edits to either input checkout.
    checked_source(devgraph, PINS["devgraph"])
    checked_source(secs, PINS["secs"])
    artifacts = output / "artifacts"
    artifacts.mkdir(mode=0o700)
    files = {}
    for name in BINARIES:
        target = artifacts / name
        shutil.copyfile(output / "target/release" / name, target)
        target.chmod(0o700)
        files[name] = digest(target)
    source_files = [
        template_root / "Cargo.toml.in",
        template_root / "Cargo.lock",
        template_root / "build.py",
        *sorted((template_root / "src").glob("*.rs")),
    ]
    report = {
        "schema": "devgraph.qualification-tool-build.v1",
        "sources": PINS,
        "tool_sources": {str(p.relative_to(template_root)): digest(p) for p in source_files},
        "artifacts": files,
        "rustc": subprocess.check_output(["rustc", "+1.96.0", "--version"], text=True).strip(),
        "cargo": subprocess.check_output(base + ["--version"], text=True).strip(),
        "authority_provisioned": False,
        "wallet_approval_invoked": False,
        "qualification": "tools built only; no installed case result",
    }
    (output / "tool-provenance.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devgraph", type=Path, required=True)
    parser.add_argument("--secs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    print(
        json.dumps(build(Path(__file__).resolve().parent, **vars(args)), indent=2, sort_keys=True)
    )


if __name__ == "__main__":
    main()
