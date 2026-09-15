#!/usr/bin/env python3
"""Build reviewable beta source and agent bundles from a clean Git commit."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {".git", ".venv", "__pycache__", "node_modules", "target",
                   "credentials", "secrets", ".devgraph", ".devgraph-backups"}


def git(*args: str, root: Path = ROOT) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)


def validate_path(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or FORBIDDEN_PARTS.intersection(path.parts):
        raise ValueError(f"release contains a forbidden path: {name}")
    if path.name == ".env" or (
        path.name.startswith(".env.") and path.name != ".env.example"
    ) or path.suffix in {".key", ".pem", ".dump", ".pyc"}:
        raise ValueError(f"release contains a local/credential artifact: {name}")


def committed_files(root: Path, commit: str = "HEAD") -> dict[str, bytes]:
    dirty = git("status", "--porcelain", "--untracked-files=all", root=root)
    if dirty:
        raise ValueError("commit the reviewed source before building a beta")
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(git("archive", commit, root=root))) as archive:
        for member in archive:
            if member.isdir():
                continue
            validate_path(member.name)
            if not member.isfile():
                raise ValueError("release source must contain regular files, not links")
            handle = archive.extractfile(member)
            assert handle is not None
            files[member.name] = handle.read()
    return files


def write_bundle(path: Path, prefix: str, files: dict[str, bytes]) -> None:
    """Normalized, reproducible archives without host ownership or Git history."""
    with path.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as archive:
            for name, data in sorted(files.items()):
                validate_path(name)
                info = tarfile.TarInfo(f"{prefix}/{name}")
                info.size = len(data)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(data))


def subset(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    return {name.removeprefix(prefix): data for name, data in files.items()
            if name.startswith(prefix)}


def build(output: Path, *, root: Path = ROOT, private_review: bool = False) -> dict[str, object]:
    commit = git("rev-parse", "HEAD", root=root).decode().strip()
    files = committed_files(root, commit)
    required = ("SECURITY.md", "plugins/devgraph/.codex-plugin/plugin.json",
                "plugins/devgraph/skills/devgraph/SKILL.md",
                "integrations/hermes/devgraph/plugin.yaml",
                ".agents/plugins/marketplace.json")
    for name in required:
        if name not in files:
            raise ValueError(f"beta prerequisite is missing: {name}")
    if "LICENSE" not in files and not private_review:
        raise ValueError(
            "beta prerequisite is missing: LICENSE; use --private-review for owner review"
        )
    if private_review:
        files["REVIEW_ONLY.txt"] = (
            b"Private beta review candidate. Public distribution is not approved.\n"
            b"This notice grants no license. Confirm project and native dependency terms\n"
            b"before distributing a public release.\n"
        )
    match = re.search(rb'^version = "([0-9A-Za-z.\-]+)"$', files["pyproject.toml"], re.M)
    if match is None:
        raise ValueError("package version is missing")
    version = match[1].decode("ascii")
    legal = {name: files[name] for name in ("LICENSE", "NOTICE", "SECURITY.md",
                                           "THIRD_PARTY_NOTICES.md", "REVIEW_ONLY.txt")
             if name in files}
    codex = {name: data for name, data in files.items()
             if name.startswith("plugins/devgraph/") or name == ".agents/plugins/marketplace.json"}
    codex.update(legal)
    if "docs/user/agent-integrations.md" in files:
        codex["INSTALL.md"] = files["docs/user/agent-integrations.md"]
    hermes = subset(files, "integrations/hermes/devgraph/")
    hermes.update({f"skills/devgraph/{name}": data for name, data in
                   subset(files, "plugins/devgraph/skills/devgraph/").items()})
    hermes.update(legal)
    artifacts = {
        f"devgraph-{version}-source.tar.gz": (f"devgraph-{version}", files),
        f"devgraph-{version}-codex.tar.gz": ("devgraph-codex", codex),
        f"devgraph-{version}-hermes.tar.gz": ("devgraph", hermes),
    }
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for name, (prefix, contents) in artifacts.items():
        path = output / name
        write_bundle(path, prefix, contents)
        entries.append({"file": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "bytes": path.stat().st_size, "files": len(contents)})
    manifest = {"schema": "devgraph.beta-release.v1", "version": version,
                "source_commit": commit,
                "distribution": "private-review" if private_review else "licensed-candidate",
                "project_license_included": "LICENSE" in files,
                "ontology": "0.6.0", "artifacts": entries,
                "operator_credentials_included": False, "git_history_included": False,
                "native_binaries_included": False}
    (output / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    checksums = [f"{item['sha256']}  {item['file']}" for item in entries]
    manifest_digest = hashlib.sha256((output / "release-manifest.json").read_bytes()).hexdigest()
    checksums.append(f"{manifest_digest}  release-manifest.json")
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="New directory outside the checkout")
    parser.add_argument("--private-review", action="store_true",
                        help="Build owner-review bundles without granting a distribution license")
    args = parser.parse_args()
    output = args.output_dir.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        parser.error("output must be outside the source checkout")
    try:
        result = build(output, private_review=args.private_review)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Beta build stopped: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
