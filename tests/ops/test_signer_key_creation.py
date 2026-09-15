import json
import subprocess
from pathlib import Path

import pytest

from devgraph.ops import signer_profile as profile


@pytest.fixture
def root(monkeypatch, tmp_path):
    root = tmp_path / "Zenith"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(profile, "INSTALL_ROOT", root)
    for name in profile.SIGNER_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    return root


def fake_wallet(monkeypatch):
    calls = []

    def wallet(mode, key_file):
        calls.append((mode, key_file))
        return "a" * 64

    monkeypatch.setattr(profile, "wallet_key_operation", wallet)
    monkeypatch.setattr(profile, "check_wallet_identity", lambda env: None)
    return calls


def test_key_creation_saves_wallet_public_pin_and_default_custody_reference(monkeypatch, root):
    calls = fake_wallet(monkeypatch)
    result = profile.create_signer()
    expected = root / "CastaliaWallet/keys/devgraph-dregg.key"
    assert calls == [("--create-identity", expected)]
    assert expected.parent.stat().st_mode & 0o777 == 0o700
    assert result["created"] is True and result["configured"] is True
    assert result["public_key"] == "a" * 64
    assert profile.signer_environment()["DEVGRAPH_SIGNING_KEY_FILE"] == str(expected)
    assert profile.signer_environment()["DEVGRAPH_SIGNING_PUBLIC_KEY"] == "a" * 64


@pytest.mark.parametrize("existing", ["profile", "environment"])
def test_existing_identity_selection_stops_before_generating(monkeypatch, root, existing):
    calls = fake_wallet(monkeypatch)
    if existing == "profile":
        profile.configure_signer(key_file=Path("/existing/key"), public_key="b" * 64)
    else:
        monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/existing/key")
    with pytest.raises(profile.SignerProfileError):
        profile.create_signer()
    assert calls == []


def test_failed_native_creation_does_not_save_profile(monkeypatch, root):
    def deny(mode, key_file):
        raise profile.SignerProfileError("creation failed")

    monkeypatch.setattr(profile, "wallet_key_operation", deny)
    with pytest.raises(profile.SignerProfileError):
        profile.create_signer()
    assert profile.signer_environment() == {}


def test_profile_failure_preserves_created_key_and_returns_recovery_details(monkeypatch, root):
    fake_wallet(monkeypatch)
    key = root / "simulated-wallet-key"

    def wallet(mode, key_file):
        key_file.write_bytes(b"opaque wallet-owned fixture")
        return "a" * 64

    monkeypatch.setattr(profile, "wallet_key_operation", wallet)

    def failed_save(*a, **kw):
        raise OSError("private implementation detail")

    monkeypatch.setattr(profile, "_save_reference", failed_save)
    result = profile.create_signer(key_file=key)
    assert result["created"] is True and result["configured"] is False
    assert result["public_key"] == "a" * 64
    assert key.read_bytes() == b"opaque wallet-owned fixture"
    assert "private implementation detail" not in json.dumps(result)
    assert profile.signer_environment() == {}


def test_inspect_does_not_modify_selected_profile(monkeypatch, root):
    calls = fake_wallet(monkeypatch)
    profile.configure_signer(key_file=Path("/current/key"), public_key="b" * 64)
    assert profile.inspect_signer_key(key_file=Path("/other/key"))["public_key"] == "a" * 64
    assert calls == [("--inspect-identity", Path("/other/key"))]
    assert profile.signer_environment()["DEVGRAPH_SIGNING_PUBLIC_KEY"] == "b" * 64


@pytest.mark.parametrize(
    "bad", ["oversized", "malformed", "unknown-field", "wrong-mode", "bad-pin", "timeout", "denied"]
)
def test_native_descriptor_is_bounded_and_closed(monkeypatch, root, bad):
    binary = root / profile.WALLET_BINARY
    binary.parent.mkdir(parents=True, mode=0o700)
    binary.write_bytes(b"fixture")
    binary.chmod(0o700)

    def run(command, **kwargs):
        assert command == [str(binary), "--create-identity", "--key-file", "/new/key"]
        assert kwargs["env"] == {}
        assert kwargs["timeout"] == 30
        if bad == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        value = {"schema": "devgraph.wallet-identity.v1", "created": True, "public_key": "a" * 64}
        if bad == "unknown-field":
            value["private_key"] = "never-print-this"
        if bad == "wrong-mode":
            value["created"] = False
        if bad == "bad-pin":
            value["public_key"] = "not-a-pin"
        raw = json.dumps(value).encode()
        if bad == "oversized":
            raw = b"x" * 2048
        if bad == "malformed":
            raw = b"never-print-this"
        return subprocess.CompletedProcess(command, 2 if bad == "denied" else 0, raw)

    monkeypatch.setattr(profile.subprocess, "run", run)
    with pytest.raises(profile.SignerProfileError) as error:
        profile.wallet_key_operation("--create-identity", Path("/new/key"))
    assert "never-print-this" not in str(error.value)


def test_invalid_creation_path_cannot_reach_wallet(monkeypatch, root):
    calls = fake_wallet(monkeypatch)
    with pytest.raises(profile.SignerProfileError):
        profile.create_signer(key_file=Path("relative.key"))
    assert calls == []


def test_key_cannot_use_the_profile_destination(monkeypatch, root):
    calls = fake_wallet(monkeypatch)
    with pytest.raises(profile.SignerProfileError):
        profile.create_signer(key_file=root / "Devgraph/auth/signer.json")
    assert calls == []
