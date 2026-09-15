"""Shared local receiver path-integrity preflight."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_DARWIN_MNT_IGNORE_OWNERSHIP = 0x00200000
_DARWIN_MAXPATHLEN = 1024
_DARWIN_MFSTYPENAMELEN = 16
OWNERSHIP_DISABLED_DIAGNOSTIC = (
    "configured data-root mount has ownership disabled"
)


class LocalPathIntegrityError(RuntimeError):
    """Redaction-safe local path-integrity failure."""


class _DarwinFsid(ctypes.Structure):
    _fields_ = [("values", ctypes.c_int32 * 2)]


class _DarwinStatfs(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", _DarwinFsid),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * _DARWIN_MFSTYPENAMELEN),
        ("f_mntonname", ctypes.c_char * _DARWIN_MAXPATHLEN),
        ("f_mntfromname", ctypes.c_char * _DARWIN_MAXPATHLEN),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


@dataclass(frozen=True)
class _DarwinMountInfo:
    flags: int
    mounted_on: str


def _darwin_mount_info(path: Path) -> _DarwinMountInfo:
    try:
        encoded = os.fsencode(path)
        libc = ctypes.CDLL(None, use_errno=True)
        statfs = libc.statfs
        statfs.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatfs)]
        statfs.restype = ctypes.c_int
        result = _DarwinStatfs()
        if statfs(encoded, ctypes.byref(result)) != 0:
            raise OSError(ctypes.get_errno(), "statfs failed")
        mounted_on = bytes(result.f_mntonname).split(b"\0", 1)[0]
        return _DarwinMountInfo(
            flags=int(result.f_flags),
            mounted_on=os.fsdecode(mounted_on),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocalPathIntegrityError(
            "configured data-root mount ownership cannot be verified"
        ) from None


def _darwin_acl_has_entry(acl: int | None) -> bool:
    if not acl:
        error_number = ctypes.get_errno()
        if error_number == errno.ENOENT:
            return False
        raise OSError(error_number, "ACL lookup failed")
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        acl_get_entry = libc.acl_get_entry
        acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        acl_get_entry.restype = ctypes.c_int
        entry = ctypes.c_void_p()
        result = acl_get_entry(acl, 0, ctypes.byref(entry))
        # Darwin returns zero when it successfully obtains an ACL entry.
        if result == 0:
            return True
        raise OSError(ctypes.get_errno(), "acl_get_entry failed")
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free.restype = ctypes.c_int
        libc.acl_free(acl)


def _darwin_has_extended_acl(path: Path) -> bool:
    acl = None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        acl_get_file = libc.acl_get_file
        acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_int]
        acl_get_file.restype = ctypes.c_void_p
        acl = acl_get_file(os.fsencode(path), 0x00000100)
        return _darwin_acl_has_entry(acl)
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocalPathIntegrityError(
            "configured receiver path ACLs cannot be verified"
        ) from None


def require_file_descriptor_without_acl(descriptor: int) -> None:
    """Reject every extended ACL through an already-open file descriptor."""

    if sys.platform != "darwin":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        acl_get_fd_np = libc.acl_get_fd_np
        acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
        acl_get_fd_np.restype = ctypes.c_void_p
        if _darwin_acl_has_entry(acl_get_fd_np(descriptor, 0x00000100)):
            raise LocalPathIntegrityError(
                "configured receiver file has an extended ACL"
            )
    except LocalPathIntegrityError:
        raise
    except (AttributeError, OSError, TypeError, ValueError):
        raise LocalPathIntegrityError(
            "configured receiver file ACLs cannot be verified"
        ) from None


def _require_no_unsafe_acl(path: Path) -> None:
    if sys.platform == "darwin" and _darwin_has_extended_acl(path):
        raise LocalPathIntegrityError(
            "configured receiver path has an extended ACL"
        )


def require_mount_ownership_enforced(path: Path) -> None:
    """Fail closed when macOS ignores filesystem ownership metadata."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise LocalPathIntegrityError("configured data root is invalid")
    if sys.platform != "darwin":
        return
    if _darwin_mount_info(path).flags & _DARWIN_MNT_IGNORE_OWNERSHIP:
        raise LocalPathIntegrityError(OWNERSHIP_DISABLED_DIAGNOSTIC)


def _require_unprivileged_mount_root_process() -> None:
    """Prove the service cannot write a root:wheel mount through process authority."""

    try:
        effective_uid = os.geteuid()
        effective_gid = os.getegid()
        supplementary_groups = os.getgroups()
    except (AttributeError, OSError):
        raise LocalPathIntegrityError(
            "receiver process authority cannot be verified"
        ) from None
    if effective_uid == 0 or effective_gid == 0 or 0 in supplementary_groups:
        raise LocalPathIntegrityError(
            "receiver process has privileged mount authority"
        )


def require_receiver_directory_path(
    data_root: Path,
    relative_path: Path,
    *,
    missing_ok: bool,
) -> Path | None:
    """Validate one fixed receiver directory chain without following links."""

    if (
        not isinstance(data_root, Path)
        or not data_root.is_absolute()
        or not isinstance(relative_path, Path)
        or relative_path.is_absolute()
        or not relative_path.parts
        or any(part in ("", ".", "..") for part in relative_path.parts)
    ):
        raise LocalPathIntegrityError("configured receiver path is invalid")
    require_mount_ownership_enforced(data_root)
    try:
        root_info = data_root.lstat()
    except FileNotFoundError as error:
        raise LocalPathIntegrityError("configured data root is unavailable") from error
    if not stat.S_ISDIR(root_info.st_mode) or data_root.is_symlink():
        raise LocalPathIntegrityError("configured data root is not a directory")
    _require_no_unsafe_acl(data_root)

    mount_root_exception = False
    if sys.platform == "darwin":
        mount = _darwin_mount_info(data_root)
        mount_root_exception = (
            os.path.normpath(str(data_root)) == os.path.normpath(mount.mounted_on)
            and root_info.st_uid == 0
            and root_info.st_gid == 0
            and stat.S_IMODE(root_info.st_mode) & 0o002 == 0
        )
        if mount_root_exception:
            _require_unprivileged_mount_root_process()
    if not mount_root_exception and (
        root_info.st_uid != os.geteuid()
        or stat.S_IMODE(root_info.st_mode) & 0o022
    ):
        raise LocalPathIntegrityError("configured data root is not receiver-controlled")
    if mount_root_exception and stat.S_IMODE(root_info.st_mode) & 0o002:
        raise LocalPathIntegrityError("configured data root is world-writable")

    current = data_root
    for part in relative_path.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise LocalPathIntegrityError(
                "configured receiver path is unavailable"
            ) from None
        if (
            not stat.S_ISDIR(info.st_mode)
            or current.is_symlink()
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise LocalPathIntegrityError(
                "configured receiver path is not receiver-controlled"
            )
        _require_no_unsafe_acl(current)
    return current
