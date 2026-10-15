"""Source access stays scoped; incomplete companion runs cannot report a pass."""

import json
import subprocess
from pathlib import Path

import pytest
from scripts.sdk.companion import (
    PARITY_FILES,
    SECS_REQUIRED_CASES,
    compare_vectors,
    digest,
    pinned_source,
    python_results,
    read_parity,
    rust_results,
)

ROOT = Path(__file__).resolve().parents[2]
def test_pins_reject_dirty_or_wrong_checkout(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "--quiet")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "disposable",
    )
    revision = git("rev-parse", "HEAD")
    assert pinned_source(tmp_path, revision) == revision
    with pytest.raises(ValueError, match="full commit"):
        pinned_source(tmp_path, "main")
    with pytest.raises(ValueError, match="source-set pin"):
        pinned_source(tmp_path, "0" * 40)
    (tmp_path / "unexpected.rs").write_text("unreviewed source")
    with pytest.raises(ValueError, match="uncommitted"):
        pinned_source(tmp_path, revision)


def test_source_parity_rejects_tamper_and_wrong_provenance(tmp_path):
    wallet, secs = tmp_path / "wallet", tmp_path / "secs"
    read = secs / "tools/read-authority"
    hashes = {}
    for name in PARITY_FILES:
        for root in [
            wallet / "crates/castalia-wallet-presentation",
            read / "vendor/castalia-wallet-presentation",
        ]:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        hashes[name] = digest(path)
    source = {
        "revision": "a" * 40,
        "source_repository": "bananawalnut/castalia-wallet",
        "files": hashes,
    }
    (read / "SOURCE.json").write_text(json.dumps(source))
    assert read_parity(wallet, secs, "a" * 40) == hashes
    with pytest.raises(ValueError, match="provenance"):
        read_parity(wallet, secs, "b" * 40)
    (read / "vendor/castalia-wallet-presentation/src/lib.rs").write_text("tampered")
    with pytest.raises(ValueError, match="source parity"):
        read_parity(wallet, secs, "a" * 40)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "test result: ok. 0 passed; 0 failed; 0 ignored;",
        "test result: ok. 3 passed; 0 failed; 1 ignored;",
    ],
)
def test_empty_or_skipped_rust_checks_cannot_pass(output):
    with pytest.raises(ValueError):
        rust_results(output)


def test_required_rust_case_must_execute():
    output = "test fixture ... ok\ntest result: ok. 1 passed; 0 failed; 0 ignored;"
    assert rust_results(output, ["fixture"])["passed"] == 1
    with pytest.raises(ValueError, match="did not pass"):
        rust_results(output, ["absent"])


def test_foundation_only_binary_cannot_certify_todo_authority():
    # A shared Cargo cache reused the earlier foundation's binary. That binary
    # passes its Wallet fixture but does not exercise the new Todo operations.
    output = (
        "test optional_independent_wallet_fixture_matches_generic_signature_bytes ... ok\n"
        "test result: ok. 5 passed; 0 failed; 0 ignored;"
    )
    with pytest.raises(ValueError, match="workflow_and_todo_contracts_require_exact_new_grants"):
        rust_results(output, SECS_REQUIRED_CASES)


@pytest.mark.parametrize("child", ["<skipped/>", "<failure/>", "<error/>"])
def test_python_required_cases_cannot_skip(tmp_path, child):
    report = tmp_path / "report.xml"
    report.write_text(
        f"<testsuites><testsuite><testcase>{child}</testcase></testsuite></testsuites>"
    )
    with pytest.raises(ValueError):
        python_results(report)
    report.write_text("<testsuites><testsuite><testcase/></testsuite></testsuites>")
    assert python_results(report) == {"passed": 1, "skipped": 0}


def test_fresh_vectors_must_agree_with_consumed_vectors(tmp_path):
    generated, retained = tmp_path / "generated.json", tmp_path / "retained.json"
    generated.write_text('{"value":1}')
    retained.write_text('{ "value": 1 }')
    compare_vectors(generated, retained)
    generated.write_text('{"value":2}')
    with pytest.raises(ValueError, match="disagree"):
        compare_vectors(generated, retained)
