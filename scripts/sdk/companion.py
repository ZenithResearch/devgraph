"""Matched source qualification; never installs authority or uses live identities.

Run with the locked Devgraph Python environment. Evidence contains only allowlisted
source/tool hashes and check summaries. Raw test logs stay in a temporary directory.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARITY_FILES = {
    "Cargo.toml",
    "LICENSE",
    "src/lib.rs",
    "src/read_authority.rs",
    "src/grant_validity.rs",
    "tests/read_authority.rs",
    "tests/origin_security.rs",
}
SECS_REQUIRED_CASES = (
    "optional_independent_wallet_fixture_matches_generic_signature_bytes",
    "workflow_and_todo_contracts_require_exact_new_grants",
    "rejected_workflow_and_todo_contract_vectors_remain_rejected",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def pinned_source(repo, revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source revision must be a full commit SHA")
    if git(repo, "rev-parse", "HEAD") != revision:
        raise ValueError("companion checkout does not match the source-set pin")
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("companion checkout has uncommitted or untracked source")
    return revision


def read_parity(wallet, secs, revision):
    read = secs / "tools/read-authority"
    source = json.loads((read / "SOURCE.json").read_text())
    if (
        source.get("revision") != revision
        or source.get("source_repository") != "bananawalnut/castalia-wallet"
        or set(source.get("files", {})) != PARITY_FILES
    ):
        raise ValueError("read-authority provenance does not match the paired Wallet")
    for name, expected in source["files"].items():
        if (
            digest(read / "vendor/castalia-wallet-presentation" / name) != expected
            or digest(wallet / "crates/castalia-wallet-presentation" / name) != expected
        ):
            raise ValueError(f"read-authority source parity failed: {name}")
    return source["files"]


def rust_results(output, required=()):
    results = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;", output)
    if not results or sum(int(row[0]) for row in results) == 0:
        raise ValueError("Rust check did not execute tests")
    if any(int(failed) or int(ignored) for _, failed, ignored in results):
        raise ValueError("required Rust tests failed or were ignored")
    for name in required:
        if not re.search(rf"^test {re.escape(name)} \.\.\. ok$", output, re.MULTILINE):
            raise ValueError(f"required Rust test did not pass: {name}")
    return {"passed": sum(int(row[0]) for row in results), "skipped": 0}


def python_results(path):
    cases = list(ET.parse(path).iter("testcase"))
    if not cases or any(
        child.tag in {"skipped", "error", "failure"} for case in cases for child in case
    ):
        raise ValueError("required Python checks failed, skipped, or did not execute")
    return {"passed": len(cases), "skipped": 0}


def compare_vectors(generated, retained):
    if json.loads(generated.read_text()) != json.loads(retained.read_text()):
        raise ValueError("fresh secS vectors disagree with Devgraph's retained vectors")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet", required=True, type=Path)
    parser.add_argument("--secs", required=True, type=Path)
    parser.add_argument("--build-root", required=True, type=Path)
    args = parser.parse_args()
    wallet, secs = args.wallet.resolve(), args.secs.resolve()
    builds = args.build_root.resolve()
    builds.mkdir(parents=True, exist_ok=True)
    output = ROOT / ".sdk-validation/companion-source.json"
    output.parent.mkdir(exist_ok=True)
    report = {
        "schema": "devgraph.companion-source-check.v1",
        "status": "running",
        "installed_qualification": "not_run",
        "live_activation": "not_performed",
        "checks": [],
    }

    def save():
        output.write_text(json.dumps(report, indent=2) + "\n")

    save()  # Never leave a stale passing report after a failed preflight.
    try:
        source_set = json.loads((ROOT / "docs/dev/generic-wallet-source-set.json").read_text())
        sources = source_set["sources"]
        report["sources"] = {
            "wallet": pinned_source(wallet, sources["wallet_generic"]["revision"]),
            "secs": pinned_source(secs, sources["secs_generic_todo"]["revision"]),
            "devgraph": git(ROOT, "rev-parse", "HEAD"),
            "devgraph_dirty": bool(git(ROOT, "status", "--porcelain")),
        }
        report["source_set_sha256"] = digest(ROOT / "docs/dev/generic-wallet-source-set.json")
        report["runner_sha256"] = digest(Path(__file__))
        report["read_parity"] = read_parity(wallet, secs, report["sources"]["wallet"])
        report["tools"] = {
            name: subprocess.check_output([name, "--version"], text=True).strip()
            for name in ("cargo", "rustc", "node")
        }
        report["tools"]["python"] = sys.version.split()[0]
        env = dict(os.environ)
        # Source credentials belong only to fetch steps, never code-under-test.
        for key in ("SDK_WALLET_SOURCE_TOKEN", "SDK_SOURCE_APP_PRIVATE_KEY", "GITHUB_TOKEN"):
            env.pop(key, None)

        with tempfile.TemporaryDirectory(prefix="devgraph-companion-") as directory:
            scratch = Path(directory)

            def run(name, command, cwd, extra=None, tests=False, required=(), stdout_only=False):
                print(f"Running {name}", flush=True)
                start = time.monotonic()
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    env={**env, **(extra or {})},
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE if stdout_only else subprocess.STDOUT,
                    check=False,
                )
                replacements = [
                    (str(scratch), "<temporary>"),
                    (str(builds), "<build>"),
                    (str(wallet), "<wallet>"),
                    (str(secs), "<secs>"),
                    (str(ROOT), "<devgraph>"),
                    (sys.executable, "python"),
                ]

                def portable(value):
                    for source, target in replacements:
                        value = value.replace(source, target)
                    return value

                result = {
                    "name": name,
                    "command": [portable(part) for part in command],
                    "working_directory": portable(str(cwd)),
                    "exit_code": completed.returncode,
                    "seconds": round(time.monotonic() - start, 2),
                }
                report["checks"].append(result)
                save()
                if completed.returncode:
                    # Commands use only disposable fixtures. Never echo the environment.
                    print((completed.stdout + (completed.stderr or ""))[-12000:], file=sys.stderr)
                    raise ValueError(f"{name} failed")
                if tests:
                    result.update(rust_results(completed.stdout, required))
                save()
                return completed.stdout

            wallet_env = {"CARGO_TARGET_DIR": str(builds / "wallet")}
            secs_env = {"CARGO_TARGET_DIR": str(builds / "secs")}
            read_env = {"CARGO_TARGET_DIR": str(builds / "read")}
            fixture = scratch / "wallet.json"
            # Keep cargo diagnostics separate from this public synthetic JSON vector.
            fixture.write_text(
                run(
                    "wallet-fixture",
                    [
                        "cargo",
                        "run",
                        "--quiet",
                        "--locked",
                        "--offline",
                        "-p",
                        "castalia-wallet-presentation",
                        "--example",
                        "generate_fixture",
                    ],
                    wallet,
                    wallet_env,
                    stdout_only=True,
                )
            )
            value = json.loads(fixture.read_text())
            if value.get("schema") != "castalia.credential-presentation-test-vector.v2":
                raise ValueError("independent Wallet fixture missing or invalid")
            report["wallet_fixture_sha256"] = digest(fixture)
            run(
                "wallet-presentation",
                ["cargo", "test", "--locked", "--offline", "-p", "castalia-wallet-presentation"],
                wallet,
                wallet_env,
                tests=True,
            )
            run(
                "wallet-read-core",
                [
                    "cargo",
                    "test",
                    "--locked",
                    "--offline",
                    "-p",
                    "castalia-wallet-core",
                    "--test",
                    "read_access",
                ],
                wallet,
                wallet_env,
                tests=True,
            )
            run(
                "wallet-driver",
                [
                    "cargo",
                    "build",
                    "--locked",
                    "--offline",
                    "-p",
                    "castalia-wallet-core",
                    "--example",
                    "recovery_contract_driver",
                ],
                wallet,
                wallet_env,
            )
            work_vectors, progress_vectors = scratch / "work.json", scratch / "progress.json"
            run(
                "secs-generic-vectors",
                [
                    "cargo",
                    "test",
                    "--locked",
                    "--offline",
                    "-p",
                    "server",
                    "--test",
                    "devgraph_work_v2",
                ],
                secs,
                {
                    **secs_env,
                    "SECS_TEST_WALLET_GENERIC_FIXTURE": str(fixture),
                    "SECS_TEST_V2_FIXTURE_OUTPUT": str(work_vectors),
                    "SECS_TEST_PROGRESS_FIXTURE_OUTPUT": str(progress_vectors),
                },
                tests=True,
                required=SECS_REQUIRED_CASES,
            )
            for generated, retained in [
                (work_vectors, "authority-vectors.json"),
                (progress_vectors, "progress-authority-vectors.json"),
            ]:
                compare_vectors(generated, ROOT / "tests/fixtures/credential-v2" / retained)
            report["fresh_vectors"] = {
                "work_sha256": digest(work_vectors),
                "todo_sha256": digest(progress_vectors),
                "retained_vectors_equal": True,
            }
            run(
                "secs-native-v2",
                [
                    "cargo",
                    "test",
                    "--locked",
                    "--offline",
                    "-p",
                    "server",
                    "--lib",
                    "devgraph_work_v2_cli::tests::private_cli_preflight_approval_retry_and_fail_closed",
                ],
                secs,
                secs_env,
                tests=True,
                required=(
                    "devgraph_work_v2_cli::tests::private_cli_preflight_approval_retry_and_fail_closed",
                ),
            )
            read = secs / "tools/read-authority"
            run(
                "secs-read-tests",
                ["cargo", "test", "--locked", "--offline"],
                read,
                read_env,
                tests=True,
            )
            run(
                "read-contract-tests",
                ["cargo", "test", "--locked", "--offline"],
                read / "vendor/castalia-wallet-presentation",
                read_env,
                tests=True,
            )
            run("secs-read-build", ["cargo", "build", "--locked", "--offline"], read, read_env)
            binary = builds / "read/debug/secs-read-authority-v1"
            driver = builds / "wallet/debug/examples/recovery_contract_driver"
            if not binary.is_file() or not driver.is_file():
                raise ValueError("required source-built native test binaries missing")
            report["test_binary_sha256"] = {"secs_read": digest(binary), "wallet": digest(driver)}
            junit = scratch / "pytest.xml"
            run(
                "devgraph-native-and-todo",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    f"--junitxml={junit}",
                    "tests/native_read",
                    "tests/auth/test_secs_work_v2.py",
                    "tests/api/test_generic_todo_authority.py",
                    "tests/api/test_credential_work.py",
                    "tests/ops/test_named_work_v2_receiver.py",
                ],
                ROOT,
                {
                    "SECS_READ_AUTHORITY_TEST_BINARY": str(binary),
                    "CASTALIA_RECOVERY_TEST_DRIVER": str(driver),
                },
            )
            report["checks"][-1].update(python_results(junit))
            run(
                "wallet-provider",
                ["node", "scripts/qualification/wallet_provider_contract.mjs", str(wallet)],
                ROOT,
            )
            # No source writes, fixture drift or edited companions can be certified.
            pinned_source(wallet, report["sources"]["wallet"])
            pinned_source(secs, report["sources"]["secs"])
            read_parity(wallet, secs, report["sources"]["wallet"])
        report["status"] = "passed"
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        report["status"] = "failed"
        # Errors are deliberately bounded; never include file contents or environment values.
        report["error"] = str(error)[:512]
        print(f"Companion source checks failed: {report['error']}", file=sys.stderr)
    finally:
        save()
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
