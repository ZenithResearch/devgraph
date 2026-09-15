"""One-shot Wallet-to-Devgraph composition for ``devgraph.issue.create.v1``.

This local composition invokes only the installed fixed secS Wallet adapter,
keeps its short-lived authority projection in an owner-private temporary
directory, and immediately hands that projection to Devgraph's exact local
receiver.  It is not a generic subprocess, transport, credential, or Work API
surface.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from devgraph.ops.local_path_integrity import (
    LocalPathIntegrityError,
    require_file_descriptor_without_acl,
    require_mount_ownership_enforced,
)
from devgraph.ops.secs_issue_create_receiver import (
    execute_local_secs_issue_create_v1,
)

SECS_WALLET_BINARY_NAME = "secs-devgraph-issue-create-v1-wallet"
SECS_WALLET_INSTALL_ROOT = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Zenith"
)
SECS_WALLET_BINARY_RELATIVE_PATH = Path("secS") / "bin" / SECS_WALLET_BINARY_NAME
SECS_WALLET_BINARY_PATH = SECS_WALLET_INSTALL_ROOT / SECS_WALLET_BINARY_RELATIVE_PATH


class LocalSecSWalletIssueCreateError(RuntimeError):
    """Redaction-safe failure for the fixed local Wallet composition."""


def _require_directory_descriptor_without_granting_acl(descriptor: int) -> None:
    """Allow Darwin deny entries but reject every access-granting directory ACL."""

    if sys.platform != "darwin":
        return
    acl = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        acl_get_fd_np = libc.acl_get_fd_np
        acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        acl_get_fd_np.restype = ctypes.c_void_p
        ctypes.set_errno(0)
        acl = acl_get_fd_np(descriptor, 0x00000100)
        if not acl:
            if ctypes.get_errno() == errno.ENOENT:
                return
            raise OSError(ctypes.get_errno(), "ACL lookup failed")

        acl_get_entry = libc.acl_get_entry
        acl_get_entry.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        acl_get_entry.restype = ctypes.c_int
        acl_get_tag_type = libc.acl_get_tag_type
        acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        acl_get_tag_type.restype = ctypes.c_int

        selector = 0  # ACL_FIRST_ENTRY
        while True:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = acl_get_entry(acl, selector, ctypes.byref(entry))
            if result == -1:
                if ctypes.get_errno() == errno.EINVAL:
                    return
                raise OSError(ctypes.get_errno(), "ACL entry lookup failed")
            if result != 0 or not entry:
                raise OSError(ctypes.get_errno(), "ACL entry lookup failed")
            tag = ctypes.c_int()
            if acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                raise OSError(ctypes.get_errno(), "ACL tag lookup failed")
            if tag.value == 1:  # ACL_EXTENDED_ALLOW
                raise LocalPathIntegrityError(
                    "fixed Wallet adapter directory has an access-granting ACL"
                )
            if tag.value != 2:  # ACL_EXTENDED_DENY
                raise LocalPathIntegrityError(
                    "fixed Wallet adapter directory ACL is unsupported"
                )
            selector = -1  # ACL_NEXT_ENTRY
    except LocalPathIntegrityError:
        raise
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocalPathIntegrityError(
            "fixed Wallet adapter directory ACLs cannot be verified"
        ) from None
    finally:
        if acl:
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free.restype = ctypes.c_int
            libc.acl_free(acl)


def _require_safe_directory_descriptor(descriptor: int) -> None:
    info = os.fstat(descriptor)
    mode = stat.S_IMODE(info.st_mode)
    root_owned_sticky = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in {0, os.geteuid()}
        or (mode & 0o022 and not root_owned_sticky)
    ):
        raise LocalPathIntegrityError(
            "fixed Wallet adapter directory is not owner-controlled"
        )
    _require_directory_descriptor_without_granting_acl(descriptor)


def _directory_open_flags() -> int:
    flags = getattr(os, "O_SEARCH", getattr(os, "O_PATH", os.O_RDONLY))
    for option in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, option, 0)
    return flags


def _close_wallet_binary_descriptors(
    directory_descriptors: Sequence[int],
    binary_descriptor: int | None,
) -> None:
    if binary_descriptor is not None:
        os.close(binary_descriptor)
    for descriptor in reversed(directory_descriptors):
        os.close(descriptor)


def _require_fixed_wallet_binary(path: Path) -> tuple[tuple[int, ...], int]:
    """Open and hold one owner-controlled executable through its full path."""

    return _require_fixed_executable(
        path,
        expected_path=SECS_WALLET_BINARY_PATH,
        install_root=SECS_WALLET_INSTALL_ROOT,
        relative_path=SECS_WALLET_BINARY_RELATIVE_PATH,
    )


def _require_fixed_executable(
    path: Path,
    *,
    expected_path: Path,
    install_root: Path,
    relative_path: Path,
) -> tuple[tuple[int, ...], int]:
    """Shared descriptor checks for code-selected local executables only."""

    if (
        path != expected_path
        or path != install_root / relative_path
        or not path.is_absolute()
    ):
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter installation is unsafe"
        )

    try:
        path.lstat()
    except (FileNotFoundError, NotADirectoryError) as error:
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter is not installed"
        ) from error
    except OSError as error:
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter installation is unsafe"
        ) from error

    directory_descriptors: list[int] = []
    binary_descriptor = None
    try:
        require_mount_ownership_enforced(path.parent)
        current_descriptor = os.open(path.anchor, _directory_open_flags())
        directory_descriptors.append(current_descriptor)
        _require_safe_directory_descriptor(current_descriptor)
        for part in path.parent.parts[1:]:
            current_descriptor = os.open(
                part,
                _directory_open_flags(),
                dir_fd=current_descriptor,
            )
            directory_descriptors.append(current_descriptor)
            _require_safe_directory_descriptor(current_descriptor)

        flags = getattr(os, "O_EXEC", getattr(os, "O_PATH", os.O_RDONLY))
        for option in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, option, 0)
        binary_descriptor = os.open(
            path.name,
            flags,
            dir_fd=directory_descriptors[-1],
        )
        info = os.fstat(binary_descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o7022
            or not info.st_mode & stat.S_IXUSR
            or info.st_nlink != 1
        ):
            raise LocalPathIntegrityError("fixed Wallet adapter file is unsafe")
        require_file_descriptor_without_acl(binary_descriptor)
        return tuple(directory_descriptors), binary_descriptor
    except FileNotFoundError as error:
        _close_wallet_binary_descriptors(directory_descriptors, binary_descriptor)
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter is not installed"
        ) from error
    except (LocalPathIntegrityError, OSError, ValueError) as error:
        _close_wallet_binary_descriptors(directory_descriptors, binary_descriptor)
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter installation is unsafe"
        ) from error


def _run_wallet_adapter(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, check=False)
    except OSError as error:
        raise LocalSecSWalletIssueCreateError(
            "the fixed secS Wallet adapter could not start"
        ) from error


def execute_local_wallet_secs_issue_create_v1(
    *,
    request_file: Path,
    idempotency_key_file: Path,
) -> dict[str, Any]:
    """Approve, authorize, and execute exactly one local Issue create."""

    directory_descriptors, binary_descriptor = _require_fixed_wallet_binary(
        SECS_WALLET_BINARY_PATH
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="devgraph-secs-issue-create-v1-"
        ) as raw_root:
            temporary_root = Path(raw_root)
            temporary_root.chmod(0o700)
            signed_projection_file = temporary_root / "signed-projection.json"
            completed = _run_wallet_adapter(
                [
                    str(SECS_WALLET_BINARY_PATH),
                    "--request-file",
                    str(request_file),
                    "--idempotency-key-file",
                    str(idempotency_key_file),
                    "--signed-projection-output",
                    str(signed_projection_file),
                ]
            )
            if completed.returncode != 0:
                raise LocalSecSWalletIssueCreateError(
                    "the secS Wallet ceremony did not complete"
                )
            return execute_local_secs_issue_create_v1(
                request_file=request_file,
                signed_projection_file=signed_projection_file,
                idempotency_key_file=idempotency_key_file,
            )
    finally:
        _close_wallet_binary_descriptors(directory_descriptors, binary_descriptor)
