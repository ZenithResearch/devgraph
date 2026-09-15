import fcntl
import json
import os
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


def configure(monkeypatch):
    calls = []
    monkeypatch.setattr(profile, "check_wallet_identity", lambda env: calls.append(env))
    result = profile.configure_signer(
        key_file=Path("/private/existing/dregg.key"), public_key="a" * 64
    )
    return calls, result


def test_setup_checks_wallet_and_persists_only_reference(monkeypatch, root):
    calls, result = configure(monkeypatch)
    saved = root / "Devgraph/auth/signer.json"
    assert len(calls) == 1
    assert result["identity_verified"] is True
    assert saved.stat().st_mode & 0o777 == 0o600
    assert saved.parent.stat().st_mode & 0o777 == 0o700
    data = json.loads(saved.read_bytes())
    assert set(data) == {"schema", "key_file", "public_key"}
    assert profile.signer_environment() == calls[0]
    assert profile.signer_status()["source"] == "saved_profile"


def test_rejected_identity_does_not_save_or_replace_profile(monkeypatch, root):
    configure(monkeypatch)
    saved = root / "Devgraph/auth/signer.json"
    before = saved.read_bytes()

    def deny(env):
        raise profile.SignerProfileError("Wallet identity check failed")

    monkeypatch.setattr(profile, "check_wallet_identity", deny)
    with pytest.raises(profile.SignerProfileError):
        profile.configure_signer(key_file=Path("/another/key"), public_key="b" * 64, replace=True)
    assert saved.read_bytes() == before


def test_replace_requires_explicit_flag_and_forget_keeps_key(monkeypatch, root):
    configure(monkeypatch)
    with pytest.raises(profile.SignerProfileError, match="replace"):
        configure(monkeypatch)
    profile.configure_signer(key_file=Path("/different/key"), public_key="b" * 64, replace=True)
    assert profile.signer_environment()["DEVGRAPH_SIGNING_PUBLIC_KEY"] == "b" * 64
    assert profile.forget_signer()["removed"] is True
    assert profile.forget_signer()["removed"] is False
    assert profile.signer_status()["configured"] is False


def test_environment_overrides_as_complete_pair_without_mixing(monkeypatch, root):
    configure(monkeypatch)
    monkeypatch.setenv("DEVGRAPH_SIGNING_KEY_FILE", "/explicit/key")
    with pytest.raises(profile.SignerProfileError, match="both"):
        profile.signer_environment()
    monkeypatch.setenv("DEVGRAPH_SIGNING_PUBLIC_KEY", "b" * 64)
    assert profile.signer_environment()["DEVGRAPH_SIGNING_KEY_FILE"] == "/explicit/key"
    assert profile.signer_status()["source"] == "environment"


@pytest.mark.parametrize(
    "bad",
    [
        "broad",
        "symlink",
        "hardlink",
        "duplicate",
        "unknown",
        "directory",
        "fifo",
        "oversize",
        "parent-link",
    ],
)
def test_unsafe_or_malformed_profile_denied(monkeypatch, root, bad):
    configure(monkeypatch)
    saved = root / "Devgraph/auth/signer.json"
    if bad == "broad":
        saved.chmod(0o644)
    elif bad in {"symlink", "hardlink"}:
        other = saved.with_name("other")
        saved.rename(other)
        if bad == "symlink":
            saved.symlink_to(other)
        else:
            os.link(other, saved)
    elif bad == "duplicate":
        saved.write_text('{"schema":"x","schema":"x"}')
    elif bad == "unknown":
        data = json.loads(saved.read_bytes())
        data["secret"] = "never-print-this"
        saved.write_text(json.dumps(data))
    elif bad == "oversize":
        saved.write_bytes(b"x" * 9000)
    elif bad == "parent-link":
        other = saved.parent.with_name("other-auth")
        saved.parent.rename(other)
        saved.parent.symlink_to(other, target_is_directory=True)
    else:
        saved.unlink()
        if bad == "directory":
            saved.mkdir()
        else:
            os.mkfifo(saved)
    with pytest.raises(profile.SignerProfileError):
        profile.signer_environment()
    assert profile.signer_status()["configured"] is False


@pytest.mark.parametrize(
    "key,pin",
    [("relative", "a" * 64), ("/key", "A" * 64), ("/key", ""), ("/key/../other", "a" * 64)],
)
def test_invalid_reference_never_invokes_wallet(monkeypatch, root, key, pin):
    monkeypatch.setattr(
        profile, "check_wallet_identity", lambda env: pytest.fail("invalid reference delegated")
    )
    with pytest.raises(profile.SignerProfileError):
        profile.configure_signer(key_file=Path(key), public_key=pin)


def test_check_uses_fixed_wallet_and_minimal_environment(monkeypatch, root):
    binary = root / profile.WALLET_BINARY
    binary.parent.mkdir(mode=0o700, parents=True)
    binary.write_bytes(b"fixture")
    binary.chmod(0o700)
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-reach-wallet")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(profile.subprocess, "run", run)
    env = {"DEVGRAPH_SIGNING_KEY_FILE": "/private/key", "DEVGRAPH_SIGNING_PUBLIC_KEY": "a" * 64}
    profile.check_wallet_identity(env)
    command, kwargs = calls[0]
    assert command == [str(binary), "--check-identity"]
    assert kwargs["env"] == env
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 30


def test_current_check_failure_is_not_cached_as_verified(monkeypatch, root):
    configure(monkeypatch)

    def deny(env):
        raise profile.SignerProfileError("Wallet identity check failed")

    monkeypatch.setattr(profile, "check_wallet_identity", deny)
    assert profile.signer_status()["identity_verified"] is None
    assert profile.signer_status(check=True)["identity_verified"] is False


def test_profile_lock_denies_concurrent_update_without_partial_write(monkeypatch, root):
    configure(monkeypatch)
    saved = root / "Devgraph/auth/signer.json"
    before = saved.read_bytes()
    with saved.with_name("profile.lock").open("r+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(profile.SignerProfileError, match="progress"):
            profile.configure_signer(key_file=Path("/other/key"), public_key="b" * 64, replace=True)
    assert saved.read_bytes() == before


def test_no_dotenv_or_home_override_is_loaded(monkeypatch, root, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".env").write_text("DEVGRAPH_SIGNING_KEY_FILE=/untrusted/key\n")
    assert profile.signer_environment() == {}
