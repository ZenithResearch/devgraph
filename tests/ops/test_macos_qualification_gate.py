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
        "schema": "devgraph.installed-qualification.v2",
        "candidate": manifest,
        "before": hashes,
        "after": hashes,
        "environment": {"model": "VirtualMac-test"},
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
        lambda r: r.update(guest_id=""),
        lambda r: r["environment"].update(model="Mac14,2"),
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
