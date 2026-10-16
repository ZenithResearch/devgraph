"""Evidence admission checks are not installed qualification themselves."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/qualification/macos_gate.py"
spec = importlib.util.spec_from_file_location("macos_gate", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def candidate(tmp_path):
    manifest = {
        "schema": "devgraph.installed-candidate.v2",
        "sources": {name: "a" * 40 for name in gate.SOURCES},
        "artifacts": {},
    }
    for role in [*gate.NATIVE, "wallet_extension", "devgraph_wheel", "web_sdk"]:
        path = tmp_path / role
        path.write_bytes(role.encode())
        manifest["artifacts"][role] = {role: gate.digest(path)}
    (tmp_path / "candidate.json").write_text(json.dumps(manifest))
    return manifest


def report(manifest, hashes):
    return {
        "schema": gate.REPORT_SCHEMA,
        "candidate": manifest,
        "before": hashes,
        "after": hashes,
        "installed_before": gate.expected_native(manifest),
        "installed_after": gate.expected_native(manifest),
        "environment": {"system": "Darwin", "model": "VirtualMac-test", "uid": 501},
        "guest_id": "disposable-test-guest",
        "cases": {
            name: {
                "outcome": "passed",
                "verification": "operator_observed_installed",
                "note": "Synthetic admission-unit-test data, never release evidence",
                "evidence_sha256": "b" * 64,
            }
            for name in gate.CASES
        },
    }


def test_inventory_requires_exact_complete_bundle(tmp_path):
    manifest = candidate(tmp_path)
    assert len(gate.inventory(tmp_path, manifest)) == 5
    (tmp_path / "unlisted").write_text("changed build")
    with pytest.raises(gate.GateError, match="undeclared"):
        gate.inventory(tmp_path, manifest)
    (tmp_path / "unlisted").unlink()
    (tmp_path / "wallet_signer").write_text("modified signer")
    with pytest.raises(gate.GateError, match="digest mismatch"):
        gate.inventory(tmp_path, manifest)


def test_missing_blocked_fixture_and_changed_artifact_evidence_cannot_pass(tmp_path):
    manifest = candidate(tmp_path)
    hashes = gate.inventory(tmp_path, manifest)
    valid = report(manifest, hashes)
    assert gate.verify(valid, manifest, hashes)
    for mutate in [
        lambda r: r["cases"].pop(gate.CASES[0]),
        lambda r: r["cases"][gate.CASES[0]].update(outcome="blocked"),
        lambda r: r["cases"][gate.CASES[0]].update(verification="fixture"),
        lambda r: r["cases"][gate.CASES[0]].update(evidence_sha256=""),
        lambda r: r.update(after={}),
        lambda r: r.update(installed_before={}),
        lambda r: r.update(installed_after={}),
        lambda r: r["installed_after"]["wallet_signer"].update(sha256="c" * 64),
        lambda r: r["installed_before"]["secs_authority"].update(path="another/binary"),
        lambda r: r.update(schema="devgraph.installed-qualification.v2"),
        lambda r: r.update(guest_id=""),
        lambda r: r["environment"].update(model="Mac14,2"),
        lambda r: r["environment"].update(system="Linux"),
        lambda r: r["environment"].update(uid=0),
        lambda r: r["environment"].update(uid=True),
    ]:
        value = deepcopy(valid)
        mutate(value)
        with pytest.raises(gate.GateError):
            gate.verify(value, manifest, hashes)


def test_paths_and_manifests_fail_closed(tmp_path):
    manifest = candidate(tmp_path)
    for name in ["../escape", "/etc/passwd", "absent"]:
        with pytest.raises(gate.GateError):
            gate.member(tmp_path, name)
    (tmp_path / "alias").symlink_to(tmp_path / "wallet_signer")
    with pytest.raises(gate.GateError, match="symlink"):
        gate.inventory(tmp_path, manifest)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}')
    with pytest.raises(gate.GateError, match="duplicate"):
        gate.read(duplicate)


def test_physical_machine_refused_before_initialization(monkeypatch):
    monkeypatch.setattr(gate.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(gate.subprocess, "check_output", lambda *a, **k: "Mac14,2\n")
    with pytest.raises(gate.GateError, match="guest required"):
        gate.guest()
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    with pytest.raises(gate.GateError, match="macOS VM required"):
        gate.guest()


def test_archive_candidates_cannot_qualify_public_artifacts(tmp_path):
    manifest = candidate(tmp_path)
    assert gate.SOURCES == {"devgraph_public", "wallet", "secs"}
    manifest["sources"]["devgraph_private"] = "b" * 40
    with pytest.raises(gate.GateError, match="three immutable"):
        gate.inventory(tmp_path, manifest)
    manifest.pop("sources")
    manifest["schema"] = "devgraph.installed-candidate.v1"
    with pytest.raises(gate.GateError, match="schema mismatch"):
        gate.inventory(tmp_path, manifest)


def install_fixture(home, bundle, manifest):
    for role, relative in gate.NATIVE.items():
        target = home / "Library/Application Support/Zenith" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        name = next(iter(manifest["artifacts"][role]))
        target.write_bytes((bundle / name).read_bytes())
        target.chmod(0o700)


def test_actual_native_files_must_match_candidate_and_remain_private(tmp_path):
    bundle = tmp_path / "candidate"
    bundle.mkdir()
    manifest = candidate(bundle)
    home = tmp_path / "guest"
    install_fixture(home, bundle, manifest)
    assert gate.installed_native(home, manifest) == gate.expected_native(manifest)
    target = home / "Library/Application Support/Zenith" / gate.NATIVE["secs_authority"]
    target.write_bytes(b"different installed authority")
    with pytest.raises(gate.GateError, match="differs from candidate"):
        gate.installed_native(home, manifest)
    install_fixture(home, bundle, manifest)
    for mode in [0o600, 0o744, 0o720]:
        target.chmod(mode)
        with pytest.raises(gate.GateError, match="unsafe installed native binary"):
            gate.installed_native(home, manifest)
    target.chmod(0o700)
    target.parent.chmod(0o777)
    with pytest.raises(gate.GateError, match="unsafe installed native directory"):
        gate.installed_native(home, manifest)


@pytest.mark.parametrize("directory", [False, True])
def test_installed_native_symlinks_are_rejected_even_with_matching_bytes(tmp_path, directory):
    bundle = tmp_path / "candidate"
    bundle.mkdir()
    manifest = candidate(bundle)
    home = tmp_path / "guest"
    install_fixture(home, bundle, manifest)
    target = home / "Library/Application Support/Zenith" / gate.NATIVE["wallet_signer"]
    if directory:
        target = target.parent
    original = target.with_name(target.name + "-original")
    target.rename(original)
    target.symlink_to(original, target_is_directory=directory)
    with pytest.raises(gate.GateError, match="unsafe installed native"):
        gate.installed_native(home, manifest)


def test_guest_run_checks_installation_and_evidence_before_completion(tmp_path, monkeypatch):
    """Mock the guest only; exercise real CLI files and failed completion writes."""
    bundle = tmp_path / "candidate"
    bundle.mkdir()
    manifest = candidate(bundle)
    home = tmp_path / "guest"
    home.mkdir()
    marker = home / ".devgraph-qualification-guest.json"
    gate.write_new(marker, {"id": "synthetic-admission-test", "uid": gate.os.geteuid()})
    environment = {"system": "Darwin", "model": "VirtualMac-test", "uid": 501}
    monkeypatch.setattr(gate, "guest", lambda: (home, environment))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    report_path = evidence / "report.json"

    def run(action, *args):
        monkeypatch.setattr(
            gate.sys,
            "argv",
            [str(SCRIPT), action, "--bundle", str(bundle), "--report", str(report_path), *args],
        )
        gate.main()

    with pytest.raises(OSError):
        run("begin")
    assert not report_path.exists()
    install_fixture(home, bundle, manifest)
    run("begin")
    initial = gate.read(report_path)
    assert initial["installed_before"] == gate.expected_native(manifest)
    assert "installed_after" not in initial
    for case in gate.CASES:
        proof = evidence / (case + ".txt")
        proof.write_text("Synthetic admission-unit-test data; not qualification evidence.")
        run(
            "record",
            "--case",
            case,
            "--outcome",
            "passed",
            "--evidence",
            str(proof),
            "--note",
            "Synthetic unit test only",
        )
    before_finish = report_path.read_bytes()
    changed_evidence = evidence / (gate.CASES[0] + ".txt")
    original = changed_evidence.read_bytes()
    changed_evidence.write_text("Changed after recording")
    with pytest.raises(gate.GateError, match="scenario evidence changed"):
        run("finish")
    assert report_path.read_bytes() == before_finish
    changed_evidence.write_bytes(original)
    native = home / "Library/Application Support/Zenith" / gate.NATIVE["wallet_signer"]
    native.write_bytes(b"different signer after scenarios")
    with pytest.raises(gate.GateError, match="differs from candidate"):
        run("finish")
    assert report_path.read_bytes() == before_finish
    install_fixture(home, bundle, manifest)
    run("finish")
    finished = gate.read(report_path)
    assert finished["installed_after"] == finished["installed_before"]
    run("verify")
    run(
        "record",
        "--case",
        gate.CASES[0],
        "--outcome",
        "blocked",
        "--evidence",
        str(changed_evidence),
        "--note",
        "Synthetic changed observation",
    )
    assert "after" not in gate.read(report_path)
    assert "installed_after" not in gate.read(report_path)
    with pytest.raises(gate.GateError):
        run("verify")
