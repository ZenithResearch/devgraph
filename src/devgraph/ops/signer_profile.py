"""Owner-private signer references; private key bytes are opened only by Wallet."""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path

from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_mount_ownership_enforced,
)
from devgraph.ops.secs_issue_create_receiver import LocalSecSIssueCreateError, _strict_json_object
from devgraph.ops.secs_issue_create_wallet import (
    LocalSecSWalletIssueCreateError,
    _close_wallet_binary_descriptors,
    _require_fixed_executable,
    _require_safe_directory_descriptor,
)

INSTALL_ROOT = Path(pwd.getpwuid(os.geteuid()).pw_dir) / "Library/Application Support/Zenith"
WALLET_BINARY = Path("CastaliaWallet/bin/castalia-wallet-devgraph-work-v1")
PROFILE_DIRECTORY = Path("Devgraph/auth")
PROFILE_NAME = "signer.json"
KEY_DIRECTORY = Path("CastaliaWallet/keys")
KEY_NAME = "devgraph-dregg.key"
SIGNER_ENVIRONMENT = ("DEVGRAPH_SIGNING_KEY_FILE", "DEVGRAPH_SIGNING_PUBLIC_KEY")
SCHEMA = "devgraph.signer-reference.v1"
MAX_BYTES = 8192


class SignerProfileError(RuntimeError):
    """Redaction-safe configuration failure."""


def _reference(key_file: str, public_key: str) -> dict[str, str]:
    if (
        not isinstance(key_file, str)
        or not key_file.startswith("/")
        or len(key_file.encode("utf-8", errors="replace")) > 4096
        or any(ord(c) < 32 or ord(c) == 127 for c in key_file)
        or any(part in {".", ".."} for part in key_file.split("/"))
        or not isinstance(public_key, str)
        or re.fullmatch(r"[0-9a-f]{64}", public_key) is None
    ):
        raise SignerProfileError(
            "an absolute existing key-file path and lowercase public-key pin are required"
        )
    return {"schema": SCHEMA, "key_file": key_file, "public_key": public_key}


def _environment(reference: dict[str, str]) -> dict[str, str]:
    return dict(
        zip(SIGNER_ENVIRONMENT, (reference["key_file"], reference["public_key"]), strict=True)
    )


@contextmanager
def _directory(*, create=False, relative_path=PROFILE_DIRECTORY):
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        current = os.open(INSTALL_ROOT.anchor, flags)
        descriptors.append(current)
        _require_safe_directory_descriptor(current)
        parts = (*INSTALL_ROOT.parts[1:], *relative_path.parts)
        for index, part in enumerate(parts):
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                # Installation ancestors must already exist. Only the profile subtree is created.
                if not create:
                    yield None
                    return
                if index < len(INSTALL_ROOT.parts) - 1:
                    raise SignerProfileError("Devgraph installation is unavailable") from None
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=current)
            descriptors.append(child)
            current = child
            _require_safe_directory_descriptor(current)
        require_mount_ownership_enforced(INSTALL_ROOT / relative_path)
        info = os.fstat(current)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o7077:
            raise SignerProfileError(
                "signer profile directory must be private and owner-controlled"
            )
        require_file_descriptor_without_acl(current)
        yield current
    except (OSError, LocalPathIntegrityError, ValueError):
        raise SignerProfileError("signer profile storage is unavailable or unsafe") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _private_descriptor(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) & 0o7077
    ):
        raise SignerProfileError("signer profile file must be private and owner-controlled")
    require_file_descriptor_without_acl(descriptor)


def _read(directory: int | None) -> dict[str, str] | None:
    if directory is None:
        return None
    try:
        fd = os.open(PROFILE_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None
    try:
        _private_descriptor(fd)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SignerProfileError("signer profile exceeds its size limit")
        data = _strict_json_object(raw, label="signer profile")
        if set(data) != {"schema", "key_file", "public_key"} or data["schema"] != SCHEMA:
            raise SignerProfileError("signer profile is malformed")
        return _reference(data["key_file"], data["public_key"])
    except LocalSecSIssueCreateError:
        raise SignerProfileError("signer profile is malformed") from None
    finally:
        os.close(fd)


def signer_environment() -> dict[str, str]:
    """An explicit complete environment pair wins; otherwise load the fixed profile."""
    if any(name in os.environ for name in SIGNER_ENVIRONMENT):
        if not all(os.environ.get(name) for name in SIGNER_ENVIRONMENT):
            raise SignerProfileError(
                "set both signer environment variables or unset both to use auth setup"
            )
        return _environment(_reference(*(os.environ[name] for name in SIGNER_ENVIRONMENT)))
    with _directory() as directory:
        reference = _read(directory)
    return _environment(reference) if reference else {}


def check_wallet_identity(env: dict[str, str]) -> None:
    """Wallet verifies the key/pin without a signature, authority request, or graph write."""
    path = INSTALL_ROOT / WALLET_BINARY
    try:
        directories, binary = _require_fixed_executable(
            path, expected_path=path, install_root=INSTALL_ROOT, relative_path=WALLET_BINARY
        )
    except LocalSecSWalletIssueCreateError:
        raise SignerProfileError("the fixed Wallet signer is unavailable or unsafe") from None
    try:
        result = subprocess.run(
            [str(path), "--check-identity"],
            env=dict(env),
            cwd="/",
            timeout=30,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            raise SignerProfileError(
                "Wallet identity check failed; check the existing key path, public pin, "
                "private permissions, and Wallet version"
            )
    except (OSError, subprocess.TimeoutExpired):
        raise SignerProfileError("the fixed Wallet identity check could not complete") from None
    finally:
        _close_wallet_binary_descriptors(directories, binary)


@contextmanager
def _locked(directory: int):
    fd = os.open(
        "profile.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=directory,
    )
    try:
        _private_descriptor(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SignerProfileError("another signer setup is in progress; retry") from None
        yield
    finally:
        os.close(fd)


def _save_reference(directory: int, reference: dict[str, str]) -> None:
    temporary = f".signer-{secrets.token_hex(16)}.tmp"
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            _private_descriptor(handle.fileno())
            handle.write((json.dumps(reference, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, PROFILE_NAME, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def configure_signer(*, key_file: Path, public_key: str, replace=False) -> dict:
    reference = _reference(str(key_file), public_key)
    check_wallet_identity(_environment(reference))
    with _directory(create=True) as directory, _locked(directory):
        existing = _read(directory)
        if existing and not replace:
            raise SignerProfileError(
                "signer profile already exists; use auth setup --replace to change it"
            )
        _save_reference(directory, reference)
    return {
        "configured": True,
        "identity_verified": True,
        "profile_path": str(INSTALL_ROOT / PROFILE_DIRECTORY / PROFILE_NAME),
        "key_file": reference["key_file"],
        "public_key": reference["public_key"],
        "write_authority": "requires_current_secS_grant",
        "environment_override_present": any(name in os.environ for name in SIGNER_ENVIRONMENT),
    }


def forget_signer() -> dict:
    with _directory() as directory:
        if directory is None:
            return {"removed": False}
        with _locked(directory):
            if _read(directory) is None:
                return {"removed": False}
            os.unlink(PROFILE_NAME, dir_fd=directory)
            os.fsync(directory)
    return {"removed": True}


def signer_status(*, check=False) -> dict:
    result = {
        "configured": False,
        "identity_verified": None,
        "profile_path": str(INSTALL_ROOT / PROFILE_DIRECTORY / PROFILE_NAME),
    }
    try:
        env = signer_environment()
        if not env:
            return result
        result.update(
            configured=True,
            source="environment"
            if any(name in os.environ for name in SIGNER_ENVIRONMENT)
            else "saved_profile",
            key_file=env[SIGNER_ENVIRONMENT[0]],
            public_key=env[SIGNER_ENVIRONMENT[1]],
        )
        if check:
            result["identity_verified"] = False
            check_wallet_identity(env)
            result["identity_verified"] = True
    except SignerProfileError as error:
        result["error"] = str(error)
    return result


def wallet_key_operation(mode: str, key_file: Path) -> str:
    """Only the fixed Wallet can create/read seed bytes; return its public descriptor."""
    if mode not in {"--create-identity", "--inspect-identity"}:
        raise SignerProfileError("unsupported Wallet identity operation")
    _reference(str(key_file), "0" * 64)
    path = INSTALL_ROOT / WALLET_BINARY
    try:
        directories, binary = _require_fixed_executable(
            path, expected_path=path, install_root=INSTALL_ROOT, relative_path=WALLET_BINARY
        )
    except LocalSecSWalletIssueCreateError:
        raise SignerProfileError("the fixed Wallet signer is unavailable or unsafe") from None
    try:
        result = subprocess.run(
            [str(path), mode, "--key-file", str(key_file)],
            env={},
            cwd="/",
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise ValueError()
        value = _strict_json_object(result.stdout, label="Wallet identity descriptor")
        if (
            set(value) != {"schema", "public_key", "created"}
            or value["schema"] != "devgraph.wallet-identity.v1"
            or value["created"] is not (mode == "--create-identity")
        ):
            raise ValueError()
        return _reference(str(key_file), value["public_key"])["public_key"]
    except (
        OSError,
        subprocess.TimeoutExpired,
        ValueError,
        LocalSecSIssueCreateError,
        SignerProfileError,
    ):
        raise SignerProfileError(
            "Wallet identity operation did not complete; creation never overwrites a key. "
            "If a key file exists, use devgraph auth key inspect --key-file PATH, then auth setup"
        ) from None
    finally:
        _close_wallet_binary_descriptors(directories, binary)


def inspect_signer_key(*, key_file: Path) -> dict:
    public_key = wallet_key_operation("--inspect-identity", key_file)
    return {"key_file": str(key_file), "public_key": public_key, "profile_changed": False}


def create_signer(*, key_file: Path | None = None) -> dict:
    if any(name in os.environ for name in SIGNER_ENVIRONMENT):
        raise SignerProfileError(
            "unset both signer environment overrides before creating an identity"
        )
    selected = key_file if key_file is not None else INSTALL_ROOT / KEY_DIRECTORY / KEY_NAME
    _reference(str(selected), "0" * 64)
    if selected.is_relative_to(INSTALL_ROOT / PROFILE_DIRECTORY):
        raise SignerProfileError("key custody must be outside the signer profile directory")
    with ExitStack() as stack:
        directory = stack.enter_context(_directory(create=True))
        stack.enter_context(_locked(directory))
        if _read(directory) is not None:
            raise SignerProfileError(
                "a signer profile already exists; reuse it or explicitly forget it "
                "before creating another"
            )
        if key_file is None:
            stack.enter_context(_directory(create=True, relative_path=KEY_DIRECTORY))
        public_key = wallet_key_operation("--create-identity", selected)
        reference = _reference(str(selected), public_key)
        result = {
            "created": True,
            "configured": False,
            "identity_verified": False,
            "key_file": str(selected),
            "public_key": public_key,
            "profile_path": str(INSTALL_ROOT / PROFILE_DIRECTORY / PROFILE_NAME),
            "write_authority": "requires_current_secS_grant",
        }
        try:
            check_wallet_identity(_environment(reference))
            result["identity_verified"] = True
            _save_reference(directory, reference)
        except (OSError, LocalPathIntegrityError, SignerProfileError):
            result["error"] = (
                "key created; profile setup incomplete. Preserve the key; "
                "run auth status --check, then auth setup if needed"
            )
            return result
        result["configured"] = True
        return result
