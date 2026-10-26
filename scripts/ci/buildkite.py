"""Buildkite source/evidence admission. Never reads Wallet keys or creates grants."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
SHA = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def source_set(root=ROOT):
    return json.loads((root / "docs/dev/generic-wallet-source-set.json").read_text())["sources"]


def companion_preflight(wallet, env, root=ROOT):
    expected = env.get("DEVGRAPH_REVISION", "")
    if not SHA.fullmatch(expected) or git(root, "rev-parse", "HEAD") != expected:
        raise ValueError("Devgraph must match the reviewed immutable commit")
    if env.get("BUILDKITE_PULL_REQUEST", "false") != "false":
        raise ValueError("Companion checks are manually approved, never PR-triggered")
    if env.get("BUILDKITE_SOURCE") not in {"ui", "api"}:
        raise ValueError("Companion checks require an explicit manual build")
    pin = source_set(root)["wallet_generic"]["revision"]
    if git(wallet, "rev-parse", "HEAD") != pin or env.get("BUILDKITE_COMMIT") != pin:
        raise ValueError("The Wallet-connected pipeline must check out the exact Wallet pin")
    for repo in (root, wallet):
        if git(repo, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("Reviewed source has uncommitted changes")
    return {"devgraph": expected, "wallet": pin}


def wallet_revisions(root=ROOT):
    revisions = set()
    for lock in [root / "Cargo.lock", *sorted((root / "crates").glob("*/Cargo.lock"))]:
        if not lock.exists():
            continue
        for source in re.findall(r'^source = "([^"]+)"$', lock.read_text(), re.M):
            if source.startswith("git+https://github.com/bananawalnut/castalia-wallet"):
                revision = source.rsplit("#", 1)[-1]
                if not SHA.fullmatch(revision):
                    raise ValueError("Wallet dependency must use an immutable commit")
                revisions.add(revision)
    return sorted(revisions)


def unpack_evidence(archive, destination):
    """Do not let an uploaded evidence bundle write outside its temporary root."""
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > 4 * 1024**3:
            raise ValueError("Evidence archive exceeds admission bounds")
        seen = set()
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] not in {"candidate", "evidence"}
                or not (member.isfile() or member.isdir())
                or path in seen
            ):
                raise ValueError("Unsafe or duplicate evidence archive member")
            seen.add(path)
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, path.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)


def check_candidate_sources(candidate, revision, sources):
    expected = {
        "devgraph_public": revision,
        "wallet": sources["wallet_generic"]["revision"],
        "secs": sources["secs_generic_todo"]["revision"],
    }
    if not SHA.fullmatch(revision) or any(
        candidate.get("sources", {}).get(key) != value for key, value in expected.items()
    ):
        raise ValueError("Installed evidence belongs to another source set; requalify")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("provenance", "companion-preflight", "wallet-revisions", "admit")
    )
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.command == "provenance":
        print(
            json.dumps(
                {
                    "schema": "devgraph.buildkite-source.v1",
                    "lane": args.value,
                    "source": git(ROOT, "rev-parse", "HEAD"),
                    "build_id": os.environ.get("BUILDKITE_BUILD_ID"),
                    "job_id": os.environ.get("BUILDKITE_JOB_ID"),
                    "files": {
                        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted([*(ROOT / ".buildkite").glob("*"),
                                         *(ROOT / "scripts/ci").glob("*.sh")])
                        if p.is_file()
                    },
                    "tools": {
                        tool: subprocess.check_output([tool, "--version"], text=True).strip()
                        for tool in ("uv", "node", "cargo", "rustc")
                    },
                    "installed_qualification": False,
                },
                indent=2,
            )
        )
    elif args.command == "companion-preflight":
        print(json.dumps(companion_preflight(Path(args.value), os.environ)))
    elif args.command == "wallet-revisions":
        print("\n".join(wallet_revisions()))
    else:
        for key in ("DG_EVIDENCE_BUILD", "DG_EVIDENCE_JOB"):
            if not UUID.fullmatch(os.environ.get(key, "")):
                raise ValueError("Evidence build and job must be exact Buildkite UUIDs")
        base = Path(args.value)
        unpack_evidence(base / "qualification.tar.gz", base / "unpacked")
        bundle = base / "unpacked/candidate"
        report = base / "unpacked/evidence/report.json"
        revision = git(ROOT, "rev-parse", "HEAD")
        if os.environ.get("BUILDKITE_COMMIT") != revision:
            raise ValueError("Admission checkout differs from Buildkite commit")
        check_candidate_sources(
            json.loads((bundle / "candidate.json").read_text()), revision, source_set()
        )
        subprocess.run(
            [
                "python3",
                "scripts/qualification/macos_gate.py",
                "verify",
                "--bundle",
                str(bundle),
                "--report",
                str(report),
            ],
            check=True,
            cwd=ROOT,
            stdout=subprocess.PIPE,
        )
        print(
            json.dumps(
                {
                    "schema": "devgraph.buildkite-installed-admission.v1",
                    "status": "passed",
                    "source": revision,
                    "evidence_build": os.environ["DG_EVIDENCE_BUILD"],
                    "evidence_job": os.environ["DG_EVIDENCE_JOB"],
                    "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                }
            )
        )


if __name__ == "__main__":
    main()
