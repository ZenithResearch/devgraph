#!/usr/bin/env python3
"""Package pinned native companion sources; optionally verify isolated archive builds."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

SPECS = {
    "wallet": {
        "repository": "https://github.com/bananawalnut/castalia-wallet",
        "commit": "96a336747d5b050d334ec565b3c06de3b6b23f38",
        "package": "castalia-wallet-devgraph-cli",
        "binary": "castalia-wallet-devgraph-work-v1",
        "install_path": "Library/Application Support/Zenith/CastaliaWallet/bin/"
                        "castalia-wallet-devgraph-work-v1",
        "known_arm64_sha256": "42187a4ddd8343b3f0ac405a97ccb5964d91ef2eca1ebd3eebb394f045601b40",
        "source_license": "unresolved: native CLI has no license declaration or license file",
    },
    "secs": {
        "repository": "https://github.com/ZenithResearch/secS-magik",
        "commit": "2540d1ae37cac9c6d439bcd49aee0f94c4279889",
        "package": "server",
        "binary": "secs-devgraph-work-v1",
        "install_path": "Library/Application Support/Zenith/secS/bin/secs-devgraph-work-v1",
        "known_arm64_sha256": "ad93c73315dcf03e93da22c0f02c7a225d46d81312c1e42e10a0606d6e3374fc",
        "source_license": "MIT; preserve the source archive LICENSE",
    },
}
SCHEMA = "devgraph.beta-native-bundle.v1"
FORBIDDEN = {".git", ".venv", "__pycache__", "node_modules", "target", "credentials",
             "secrets", ".devgraph", ".devgraph-backups"}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if (path.is_absolute() or ".." in path.parts or FORBIDDEN.intersection(path.parts)
            or path.suffix in {".key", ".pem", ".dump", ".pyc"}
            or path.name == ".env"
            or (path.name.startswith(".env.") and path.name != ".env.example")):
        raise ValueError(f"forbidden archive path: {member.name}")
    if not member.isfile() and not member.isdir():
        raise ValueError(f"archive contains a link or special file: {member.name}")


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", *arguments], cwd=root,
                                   env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})


def pinned_archive(root: Path, commit: str, destination: Path, prefix: str) -> None:
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("native source checkout must be clean")
    if git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise ValueError("native source HEAD does not match the reviewed pin")
    tree = {}
    for item in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not item:
            continue
        metadata, name = item.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != b"blob":
            raise ValueError("submodules must have separately pinned source bundles")
        tree[name.decode()] = (mode, object_id.decode())
    raw = git(root, "archive", "--format=tar", commit)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        members = sorted(source.getmembers(), key=lambda entry: entry.name)
        exported = set()
        for member in members:
            checked_member(member)
            if member.isdir():
                continue
            exported.add(member.name)
            handle = source.extractfile(member)
            assert handle is not None
            data = handle.read()
            blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            mode, expected = tree.get(member.name, (b"", ""))
            if blob != expected or bool(member.mode & 0o111) != (mode == b"100755"):
                raise ValueError("git archive transformed a pinned source blob or mode")
        if exported != set(tree):
            raise ValueError("git archive omitted pinned source files")
        with destination.open("xb") as output:
            with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as gz:
                with tarfile.open(fileobj=gz, mode="w") as archive:
                    for member in members:
                        if member.isdir():
                            continue
                        data = source.extractfile(member)
                        assert data is not None
                        normalized = tarfile.TarInfo(f"{prefix}/{member.name}")
                        normalized.size = member.size
                        normalized.mode = 0o755 if member.mode & 0o111 else 0o644
                        archive.addfile(normalized, data)


def artifact(path: Path) -> dict:
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}


def build_environment(target: Path) -> dict[str, str]:
    """Forward cache/tool discovery only, never signer/proxy/authority variables."""
    env = {name: os.environ[name] for name in
           ("PATH", "HOME", "TMPDIR", "RUSTUP_HOME", "CARGO_HOME") if name in os.environ}
    compiler = shutil.which("rustc", path=env.get("PATH", os.defpath))
    if compiler is None:
        raise ValueError("rustc is not installed")
    env.update({"CARGO_TARGET_DIR": str(target), "CARGO_INCREMENTAL": "0",
                "RUSTC": compiler, "RUSTC_WRAPPER": "", "RUSTC_WORKSPACE_WRAPPER": ""})
    return env


def build_bundle(output: Path, sources: dict[str, Path], binaries: dict[str, Path]) -> dict:
    output = output.resolve()
    if output.exists():
        raise ValueError("output directory already exists")
    for root in [*sources.values(), Path(__file__).resolve().parents[1]]:
        if output == root.resolve() or root.resolve() in output.parents:
            raise ValueError("output must be outside source checkouts")
    if binaries and set(binaries) != set(SPECS):
        raise ValueError("provide both known binaries or neither")
    for name, binary in binaries.items():
        if (binary.is_symlink() or not binary.is_file()
                or sha256(binary) != SPECS[name]["known_arm64_sha256"]):
            raise ValueError(f"{name} binary does not match the reviewed arm64 candidate")
    output.mkdir(parents=True)
    entries = []
    for name, spec in SPECS.items():
        prefix = f"{name}-{spec['commit']}"
        archive = output / f"{prefix}-source.tar.gz"
        pinned_archive(sources[name], spec["commit"], archive, prefix)
        entry = {**spec, "name": name, "source": artifact(archive), "source_prefix": prefix}
        if name in binaries:
            target = output / spec["binary"]
            shutil.copyfile(binaries[name], target)
            target.chmod(0o755)
            if sha256(target) != spec["known_arm64_sha256"]:
                raise ValueError("binary changed while being copied")
            entry["binary_artifact"] = artifact(target)
        entries.append(entry)
    helper = output / "build_beta_native.py"
    shutil.copyfile(Path(__file__), helper)
    (output / "BUILD.md").write_text(
        "# Native companion source bundle\n\n"
        "Private review bundle: Wallet CLI redistribution permission is unresolved. "
        "The secS source includes its MIT license; preserve it. No license is inferred "
        "for Wallet from its separate core crate. Compiled dependencies also need notices.\n\n"
        "On Apple Silicon macOS with Python 3.10+, Rust/Cargo 1.96.0 and Xcode Command "
        "Line Tools, verify `shasum -a 256 -c SHA256SUMS`, then run:\n\n"
        "```sh\npython3 build_beta_native.py --bundle-dir . --verify-build --offline\n```\n\n"
        "Offline requires populated Cargo registry caches. On a clean machine, omit "
        "`--offline` to fetch the registry dependencies pinned in Cargo.lock. "
        "Sources are extracted into a new verification directory; builds never use "
        "installed credentials, native authority, or live services.\n\n"
        "Build logs, binaries and build-report.json are in verification/. Hashes identify "
        "the actual binaries: archive reproducibility does not promise identical machine "
        "code across toolchains, SDKs or build paths. native-manifest.json records both "
        "source pins, known arm64 candidate hashes and the required install paths relative "
        "to the operating-system account home. No installation is performed.\n"
    )
    manifest = {
        "schema": SCHEMA, "status": "private-review; Wallet redistribution permission unresolved",
        "target": "aarch64-apple-darwin", "package_version": "0.1.0",
        "reviewed_rust_version": "1.96.0", "sources": entries, "helper": artifact(helper),
        "credentials_included": False, "runtime_configuration_included": False,
        "git_history_included": False, "registry_dependencies_vendored": False,
        "binary_reproducibility": "source archives deterministic; rebuilt binary hashes recorded",
    }
    manifest_path = output / "native-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    files = [path for path in output.iterdir() if path.is_file()]
    (output / "SHA256SUMS").write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in sorted(files)))
    return manifest


def verify_build(bundle: Path, *, offline: bool) -> dict:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("the qualified native build target is Apple Silicon macOS")
    manifest = json.loads((bundle / "native-manifest.json").read_text())
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported native bundle manifest")
    expected_names = set(SPECS)
    entries = manifest.get("sources", [])
    if len(entries) != len(expected_names) or {e["name"] for e in entries} != expected_names:
        raise ValueError("manifest must contain both reviewed native sources")
    for entry in entries:
        spec = SPECS[entry["name"]]
        prefix = f"{entry['name']}-{spec['commit']}"
        filename = f"{prefix}-source.tar.gz"
        if (entry.get("commit") != spec["commit"] or entry.get("source_prefix") != prefix
                or entry["source"]["file"] != filename
                or sha256(bundle / filename) != entry["source"]["sha256"]):
            raise ValueError("source archive pin or checksum mismatch")
    work = bundle / "verification"
    work.mkdir(exist_ok=False)
    results = []
    for entry in entries:
        spec = SPECS[entry["name"]]
        with tarfile.open(bundle / entry["source"]["file"]) as archive:
            for member in archive:
                checked_member(member)
                if PurePosixPath(member.name).parts[0] != entry["source_prefix"]:
                    raise ValueError("source archive prefix mismatch")
                if member.isdir():
                    continue
                target = work / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                data = archive.extractfile(member)
                assert data is not None
                with target.open("xb") as output:
                    shutil.copyfileobj(data, output)
                target.chmod(member.mode & 0o777)
        command = ["cargo", "build", "--locked", "--release", "-p", spec["package"],
                   "--bin", spec["binary"]]
        if offline:
            command.append("--offline")
        env = build_environment(work / "target")
        with (work / f"{entry['name']}-build.log").open("wb") as log:
            subprocess.run(command, cwd=work / entry["source_prefix"], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        binary = work / "target" / "release" / spec["binary"]
        built = artifact(binary)
        built["matches_known_candidate"] = built["sha256"] == spec["known_arm64_sha256"]
        results.append({"name": entry["name"], "command": command, "binary": built})
    report = {"schema": "devgraph.beta-native-build.v1", "offline": offline,
              "rustc": subprocess.check_output([env["RUSTC"], "--version"],
                                               env=env).decode().strip(),
              "cargo": subprocess.check_output(["cargo", "--version"], env=env).decode().strip(),
              "target": "aarch64-apple-darwin", "results": results,
              "authority_or_live_service_used": False}
    (work / "build-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output-dir", type=Path)
    destination.add_argument("--bundle-dir", type=Path)
    parser.add_argument("--wallet-source", type=Path)
    parser.add_argument("--secs-source", type=Path)
    parser.add_argument("--wallet-binary", type=Path)
    parser.add_argument("--secs-binary", type=Path)
    parser.add_argument("--verify-build", action="store_true")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.output_dir and not (args.wallet_source and args.secs_source):
        parser.error("packaging requires both source checkouts")
    if args.bundle_dir and not args.verify_build:
        parser.error("--bundle-dir requires --verify-build")
    try:
        bundle = (args.output_dir or args.bundle_dir).expanduser().resolve()
        if args.output_dir:
            result = build_bundle(bundle,
                                  {"wallet": args.wallet_source.expanduser(),
                                   "secs": args.secs_source.expanduser()},
                                  {name: path.expanduser() for name, path in
                                   (("wallet", args.wallet_binary), ("secs", args.secs_binary))
                                   if path is not None})
        if args.verify_build:
            result = verify_build(bundle, offline=args.offline)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Native bundle stopped: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
