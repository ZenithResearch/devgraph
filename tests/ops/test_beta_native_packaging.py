"""Pinned native archives preserve source and reject runtime material."""

import hashlib
import io
import subprocess
import tarfile

import pytest
from scripts.build_beta_native import build_bundle, checked_member, pinned_archive


def repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Cargo.toml").write_text('[workspace]\nmembers=[]\n')
    (root / "Cargo.lock").write_text("version = 4\n")
    script = root / "build.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL)

    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "synthetic native source")
    return root, git("rev-parse", "HEAD").decode().strip()


def test_archived_source_is_deterministic_preserves_modes_and_has_no_git(tmp_path):
    root, commit = repository(tmp_path)
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    pinned_archive(root, commit, first, "native")
    pinned_archive(root, commit, second, "native")
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes())) as archive:
        assert set(archive.getnames()) == {"native/Cargo.toml", "native/Cargo.lock",
                                           "native/build.sh"}
        assert archive.getmember("native/build.sh").mode == 0o755
        assert all(m.mtime == 0 and m.uid == 0 and m.gid == 0 for m in archive)


def test_wrong_pin_and_dirty_source_are_rejected_before_archive(tmp_path):
    root, commit = repository(tmp_path)
    target = tmp_path / "bad.tar.gz"
    with pytest.raises(ValueError, match="reviewed pin"):
        pinned_archive(root, "0" * 40, target, "native")
    assert not target.exists()
    (root / "untracked-operator-state").write_text("synthetic fixture")
    with pytest.raises(ValueError, match="clean"):
        pinned_archive(root, commit, target, "native")
    assert not target.exists()


@pytest.mark.parametrize("name", ["../outside", "/absolute", ".git/config", "secrets/key",
                                  "credentials/read", ".env", "identity.key", "store.dump"])
def test_archive_rejects_private_and_unsafe_paths(name):
    with pytest.raises(ValueError):
        checked_member(tarfile.TarInfo(name))


def test_archive_rejects_links():
    member = tarfile.TarInfo("source/link")
    member.type = tarfile.SYMTYPE
    member.linkname = "/private/operator"
    with pytest.raises(ValueError, match="link"):
        checked_member(member)


def test_export_attributes_cannot_omit_committed_files(tmp_path):
    root, _ = repository(tmp_path)
    (root / ".gitattributes").write_text("Cargo.lock export-ignore\n")
    subprocess.run(["git", "add", ".gitattributes"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "omit"],
                   cwd=root, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    with pytest.raises(ValueError, match="omitted pinned source"):
        pinned_archive(root, commit, tmp_path / "bad.tar.gz", "native")


def test_build_environment_excludes_authority_and_compiler_overrides(tmp_path, monkeypatch):
    from scripts.build_beta_native import build_environment

    monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/synthetic/private")
    monkeypatch.setenv("RUSTC", "/synthetic/compiler")
    monkeypatch.setenv("RUSTC_WRAPPER", "/synthetic/wrapper")
    monkeypatch.setenv("CARGO_BUILD_RUSTC", "/synthetic/compiler")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic://proxy")
    env = build_environment(tmp_path)
    assert "DEVGRAPH_SIGNING_KEY_FILE" not in env
    assert "CARGO_BUILD_RUSTC" not in env
    assert "HTTPS_PROXY" not in env
    assert env["RUSTC"] != "/synthetic/compiler"
    assert env["RUSTC_WRAPPER"] == ""


def test_unreviewed_binary_rejected_without_creating_output(tmp_path):
    root, _ = repository(tmp_path)
    binary = tmp_path / "binary"
    binary.write_bytes(b"synthetic not executable")
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="reviewed arm64"):
        build_bundle(output, {"wallet": root, "secs": root},
                     {"wallet": binary, "secs": binary})
    assert not output.exists()


def test_manifest_pins_sources_and_optional_binaries(tmp_path, monkeypatch):
    from scripts import build_beta_native as module

    root, commit = repository(tmp_path)
    binary = tmp_path / "candidate"
    binary.write_bytes(b"synthetic reviewed candidate")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    specs = {name: {**spec, "commit": commit, "known_arm64_sha256": digest}
             for name, spec in module.SPECS.items()}
    monkeypatch.setattr(module, "SPECS", specs)
    output = tmp_path / "out"
    manifest = build_bundle(output, {"wallet": root, "secs": root},
                            {"wallet": binary, "secs": binary})
    assert manifest["credentials_included"] is False
    assert manifest["runtime_configuration_included"] is False
    assert "unresolved" in manifest["status"]
    for entry in manifest["sources"]:
        assert entry["commit"] == commit
        assert entry["binary_artifact"]["sha256"] == digest
    assert (output / "BUILD.md").is_file()
    assert "native-manifest.json" in (output / "SHA256SUMS").read_text()
