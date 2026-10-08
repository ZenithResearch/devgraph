#!/usr/bin/env python3
"""Guided, exact-artifact macOS qualification. Never infer an installed pass from fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import uuid
from pathlib import Path

CASES = (
    "provider-runtime-setup",
    "browser-todo",
    "browser-workflows",
    "terminal-todo",
    "native-sdk-workflows",
    "archive-restore",
    "proposal-decisions",
    "parent-and-evidence-gates",
    "cancelled-approval",
    "locked-wallet",
    "navigation-disconnect",
    "worker-restart",
    "expired-and-revoked-authority",
    "wrong-caller-and-resource",
    "replay-and-version-conflict",
    "unknown-before-commit",
    "unknown-after-commit",
    "read-credential-write-denial",
    "sdk-reads-pagination-disposal",
    "persistent-migration-ambiguity-and-rollback",
    "wallet-custody-registration-files",
    "generic-two-applications",
)
SOURCES = {"devgraph_public", "wallet", "secs"}
NATIVE = {
    "wallet_signer": "CastaliaWallet/bin/castalia-wallet-present-credential-v2",
    "secs_authority": "secS/bin/secs-devgraph-work-v2",
}


class GateError(ValueError):
    pass


def read(path):
    if path.is_symlink() or path.stat().st_size > 1_048_576:
        raise GateError("unsafe or oversized manifest")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GateError("duplicate manifest key")
            result[key] = value
        return result

    return json.loads(path.read_text(), object_pairs_hook=unique)


def write_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            h.update(chunk)
    return h.hexdigest()


def member(root, relative):
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(p in {"..", "."} for p in path.parts):
        raise GateError("artifact path must be relative to the bundle")
    selected = root
    for part in path.parts:
        selected = selected / part
        if selected.is_symlink():
            raise GateError("symlink artifact denied")
    if not selected.is_file():
        raise GateError("artifact missing")
    return selected


def inventory(bundle, manifest):
    if manifest.get("schema") != "devgraph.installed-candidate.v2":
        raise GateError("candidate schema mismatch")
    sources = manifest.get("sources", {})
    if set(sources) != SOURCES or any(
        re.fullmatch(r"[0-9a-f]{40}", s) is None for s in sources.values()
    ):
        raise GateError("all three immutable source revisions required")
    entries = manifest.get("artifacts", {})
    if (
        not {"wallet_signer", "secs_authority", "wallet_extension", "devgraph_wheel", "web_sdk"}
        <= entries.keys()
    ):
        raise GateError("matched native, browser, SDK and Devgraph artifacts required")
    hashes = {}
    listed = set()
    for role, files in entries.items():
        if not files:
            raise GateError("empty artifact inventory")
        for name, expected in files.items():
            if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
                raise GateError("invalid artifact digest")
            if name in listed or name == "candidate.json":
                raise GateError("artifact must appear exactly once")
            listed.add(name)
            actual = digest(member(bundle, name))
            if actual != expected:
                raise GateError("artifact digest mismatch")
            hashes[f"{role}:{name}"] = actual
    if any(len(entries[role]) != 1 for role in NATIVE):
        raise GateError("exactly one binary per native role required")
    actual_files = set()
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise GateError("symlink bundle member denied")
        if path.is_file():
            actual_files.add(path.relative_to(bundle).as_posix())
    if actual_files != listed | {"candidate.json"}:
        raise GateError("bundle contains undeclared files")
    return hashes


def guest():
    if platform.system() != "Darwin":
        raise GateError("blocked: disposable macOS VM required")
    try:
        model = subprocess.check_output(
            ["/usr/sbin/sysctl", "-n", "hw.model"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise GateError("blocked: cannot verify disposable macOS VM identity") from error
    if not model.startswith("VirtualMac") or os.geteuid() == 0:
        raise GateError("blocked: non-root Apple Virtualization guest required")
    account = pwd.getpwuid(os.geteuid())
    home = Path(account.pw_dir)
    if not home.is_absolute() or home != Path.home():
        raise GateError("OS identity and home disagree")
    return home, {"platform": platform.platform(), "model": model, "uid": account.pw_uid}


def verify(report, manifest, hashes):
    if (
        report.get("schema") != "devgraph.installed-qualification.v2"
        or report.get("candidate") != manifest
    ):
        raise GateError("candidate/report mismatch")
    if report.get("before") != hashes or report.get("after") != hashes:
        raise GateError("exact artifacts changed or final inventory missing")
    environment = report.get("environment", {})
    if not environment.get("model", "").startswith("VirtualMac") or not report.get("guest_id"):
        raise GateError("isolated macOS evidence missing")
    results = report.get("cases", {})
    if set(results) != set(CASES):
        raise GateError("installed scenarios missing")
    for result in results.values():
        if (
            result.get("outcome") != "passed"
            or result.get("verification") != "operator_observed_installed"
        ):
            raise GateError("installed scenario failed or remains blocked")
        if (
            not result.get("note")
            or re.fullmatch(r"[0-9a-f]{64}", result.get("evidence_sha256", "")) is None
        ):
            raise GateError("scenario evidence missing")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["init-guest", "begin", "install-native", "record", "finish", "verify"]
    )
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--outcome", choices=["passed", "failed", "blocked"])
    parser.add_argument(
        "--evidence", type=Path, help="Redacted evidence produced by this installed case"
    )
    parser.add_argument("--note", help="What was exercised and observed; never credentials")
    args = parser.parse_args()
    if args.action != "init-guest" and (
        not args.bundle or (args.action != "install-native" and not args.report)
    ):
        raise GateError("bundle and report paths are required")
    if args.action == "verify":
        bundle = args.bundle.resolve(strict=True)
        manifest = read(bundle / "candidate.json")
        report = read(args.report)
        verify(report, manifest, inventory(bundle, manifest))
        for result in report["cases"].values():
            if (
                digest(member(args.report.parent, result["evidence_file"]))
                != result["evidence_sha256"]
            ):
                raise GateError("scenario evidence changed")
        print("Installed qualification gate passed (recorded operator observations).")
        return
    home, environment = guest()
    marker = home / ".devgraph-qualification-guest.json"
    if args.action == "init-guest":
        if (home / "Library/Application Support/Zenith").exists():
            raise GateError("fresh guest required: existing Zenith installation")
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", 8080)) == 0:
                raise GateError("fresh guest required: port 8080 occupied")
        write_new(marker, {"id": str(uuid.uuid4()), "uid": os.geteuid()})
        print("Disposable guest initialized; no identities or grants created.")
        return
    identity = read(marker)
    if (
        identity["uid"] != os.geteuid()
        or marker.stat().st_uid != os.geteuid()
        or stat.S_IMODE(marker.stat().st_mode) != 0o600
    ):
        raise GateError("guest marker mismatch")
    bundle = args.bundle.resolve(strict=True)
    manifest = read(bundle / "candidate.json")
    hashes = inventory(bundle, manifest)
    if args.action == "install-native":
        targets = []
        for role, relative in NATIVE.items():
            name = next(iter(manifest["artifacts"][role]))
            target = home / "Library/Application Support/Zenith" / relative
            for parent in [target.parent, *target.parent.parents]:
                if parent == home.parent:
                    break
                if parent.is_symlink() or parent.exists() and parent.stat().st_uid != os.geteuid():
                    raise GateError("unsafe native installation directory")
            if target.exists() or target.is_symlink():
                raise GateError("refusing to replace an existing native installation")
            targets.append((target, name))
        for target, name in targets:
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with target.open("xb") as out, member(bundle, name).open("rb") as source:
                os.chmod(target, 0o700)
                shutil.copyfileobj(source, out)
        print("Matched native candidates installed in disposable guest.")
        return
    if args.action == "begin":
        write_new(
            args.report,
            {
                "schema": "devgraph.installed-qualification.v2",
                "candidate": manifest,
                "environment": environment,
                "guest_id": identity["id"],
                "before": hashes,
                "cases": {},
            },
        )
        return
    report = read(args.report)
    if (
        report["guest_id"] != identity["id"]
        or report["candidate"] != manifest
        or report["before"] != hashes
    ):
        raise GateError("run, guest, or candidate changed")
    if args.action == "record":
        if (
            not args.case
            or not args.outcome
            or not args.evidence
            or not args.note
            or len(args.note) > 1000
        ):
            raise GateError("case, outcome, redacted evidence and concise note required")
        relative = args.evidence.resolve(strict=True).relative_to(args.report.parent.resolve())
        evidence = member(args.report.parent, str(relative))
        report["cases"][args.case] = {
            "outcome": args.outcome,
            "verification": "operator_observed_installed",
            "evidence_file": str(relative),
            "evidence_sha256": digest(evidence),
            "note": args.note,
        }
    else:
        report["after"] = hashes
        verify(report, manifest, hashes)
    temporary = args.report.with_name(args.report.name + ".new")
    write_new(temporary, report)
    temporary.replace(args.report)


if __name__ == "__main__":
    try:
        main()
    except (GateError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"Qualification blocked: {error}", file=sys.stderr)
        raise SystemExit(2) from None
