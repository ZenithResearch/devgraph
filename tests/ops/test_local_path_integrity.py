from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from devgraph.ops import local_path_integrity as integrity


class _FakeCFunction:
    def __init__(self, result: int) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: list[object] | None = None
        self.restype: object | None = None

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.result


class _FakeAclLibc:
    def __init__(self, entry_result: int) -> None:
        self.acl_get_entry = _FakeCFunction(entry_result)
        self.acl_free = _FakeCFunction(0)


def _directory_info(*, mode: int, uid: int, gid: int) -> os.stat_result:
    return os.stat_result((stat.S_IFDIR | mode, 1, 1, 1, uid, gid, 0, 0, 0, 0))


def _darwin_path_fixture(
    monkeypatch: pytest.MonkeyPatch,
    data_root: Path,
    *,
    mount_flags: int = 0,
    mounted_on: str | None = None,
    root_mode: int = 0o775,
    root_uid: int = 0,
    root_gid: int = 0,
    descendant_mode: int = 0o700,
    acl_path: Path | None = None,
    effective_uid: int = 501,
    effective_gid: int = 20,
    supplementary_groups: list[int] | None = None,
    group_lookup_error: OSError | None = None,
) -> None:
    monkeypatch.setattr(integrity.sys, "platform", "darwin")
    monkeypatch.setattr(integrity.os, "geteuid", lambda: effective_uid)
    monkeypatch.setattr(integrity.os, "getegid", lambda: effective_gid)
    if group_lookup_error is None:
        monkeypatch.setattr(
            integrity.os,
            "getgroups",
            lambda: [20, 12, 61] if supplementary_groups is None else supplementary_groups,
        )
    else:
        def raise_group_lookup_error() -> list[int]:
            raise group_lookup_error

        monkeypatch.setattr(integrity.os, "getgroups", raise_group_lookup_error)
    monkeypatch.setattr(
        integrity,
        "_darwin_mount_info",
        lambda _path: integrity._DarwinMountInfo(
            flags=mount_flags,
            mounted_on=mounted_on or str(data_root),
        ),
    )
    monkeypatch.setattr(
        integrity,
        "_darwin_has_extended_acl",
        lambda path: path == acl_path,
    )

    def fake_lstat(path: Path) -> os.stat_result:
        if path == data_root:
            return _directory_info(mode=root_mode, uid=root_uid, gid=root_gid)
        return _directory_info(mode=descendant_mode, uid=501, gid=20)

    monkeypatch.setattr(Path, "lstat", fake_lstat)


def test_darwin_acl_get_entry_zero_means_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libc = _FakeAclLibc(entry_result=0)
    monkeypatch.setattr(integrity.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    assert integrity._darwin_acl_has_entry(1234) is True
    assert len(libc.acl_get_entry.calls) == 1
    assert len(libc.acl_free.calls) == 1


def test_darwin_acl_null_with_enoent_means_no_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        integrity.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: pytest.fail("libc must not be loaded"),
    )
    monkeypatch.setattr(integrity.ctypes, "get_errno", lambda: errno.ENOENT)

    assert integrity._darwin_acl_has_entry(None) is False


@pytest.mark.parametrize("entry_result", [-1, 1])
def test_darwin_acl_get_entry_error_fails_closed(
    entry_result: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libc = _FakeAclLibc(entry_result=entry_result)
    monkeypatch.setattr(integrity.ctypes, "CDLL", lambda *_args, **_kwargs: libc)
    monkeypatch.setattr(integrity.ctypes, "get_errno", lambda: errno.EIO)

    with pytest.raises(OSError, match="acl_get_entry failed"):
        integrity._darwin_acl_has_entry(1234)

    assert len(libc.acl_free.calls) == 1


def test_darwin_acl_null_with_unexpected_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(integrity.ctypes, "get_errno", lambda: errno.EACCES)

    with pytest.raises(OSError, match="ACL lookup failed"):
        integrity._darwin_acl_has_entry(None)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS ACL API")
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_real_darwin_extended_acl_is_detected(
    kind: str,
    tmp_path: Path,
) -> None:
    target = tmp_path / kind
    if kind == "directory":
        target.mkdir()
    else:
        target.write_bytes(b"fixture")
    subprocess.run(
        ["chmod", "+a", "everyone allow read", os.fspath(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert integrity._darwin_has_extended_acl(target) is True


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS ACL API")
def test_real_darwin_file_descriptor_with_extended_acl_fails_closed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "receiver.json"
    target.write_bytes(b"{}")
    subprocess.run(
        ["chmod", "+a", "everyone allow read", os.fspath(target)],
        check=True,
        capture_output=True,
        text=True,
    )

    descriptor = os.open(target, os.O_RDONLY)
    try:
        with pytest.raises(integrity.LocalPathIntegrityError, match="extended ACL"):
            integrity.require_file_descriptor_without_acl(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS ACL API")
def test_real_darwin_no_extended_acl_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "receiver.json"
    target.write_bytes(b"{}")

    assert integrity._darwin_has_extended_acl(target) is False
    descriptor = os.open(target, os.O_RDONLY)
    try:
        integrity.require_file_descriptor_without_acl(descriptor)
    finally:
        os.close(descriptor)


def test_darwin_ownership_disabled_mount_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(
        monkeypatch,
        tmp_path,
        mount_flags=integrity._DARWIN_MNT_IGNORE_OWNERSHIP,
    )

    with pytest.raises(
        integrity.LocalPathIntegrityError,
        match="mount has ownership disabled",
    ):
        integrity.require_receiver_directory_path(
            tmp_path,
            Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
            missing_ok=False,
        )


def test_exact_root_wheel_0775_ownership_enabled_mount_root_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(monkeypatch, tmp_path)

    result = integrity.require_receiver_directory_path(
        tmp_path,
        Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
        missing_ok=False,
    )

    assert result == tmp_path / "secrets/secs-magik/devgraph.monitor.view.read.v1"


@pytest.mark.parametrize(
    "fixture",
    [
        {"effective_uid": 0},
        {"effective_gid": 0},
        {"supplementary_groups": [20, 0, 61]},
    ],
)
def test_mount_root_exception_rejects_privileged_process_authority(
    fixture: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(monkeypatch, tmp_path, **fixture)  # type: ignore[arg-type]

    with pytest.raises(
        integrity.LocalPathIntegrityError,
        match="privileged mount authority",
    ):
        integrity.require_receiver_directory_path(
            tmp_path,
            Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
            missing_ok=False,
        )


def test_mount_root_exception_fails_closed_when_groups_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(
        monkeypatch,
        tmp_path,
        group_lookup_error=OSError("group database unavailable"),
    )

    with pytest.raises(
        integrity.LocalPathIntegrityError,
        match="process authority cannot be verified",
    ):
        integrity.require_receiver_directory_path(
            tmp_path,
            Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
            missing_ok=False,
        )


@pytest.mark.parametrize(
    ("fixture", "match"),
    [
        ({"descendant_mode": 0o770}, "receiver-controlled"),
        ({"root_mode": 0o777}, "receiver-controlled"),
        ({"root_gid": 20}, "receiver-controlled"),
        (
            {"mounted_on": "/Volumes/Other", "root_mode": 0o775},
            "receiver-controlled",
        ),
    ],
)
def test_mount_root_exception_never_applies_to_unsafe_or_nonmount_paths(
    fixture: dict[str, object],
    match: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(monkeypatch, tmp_path, **fixture)  # type: ignore[arg-type]

    with pytest.raises(integrity.LocalPathIntegrityError, match=match):
        integrity.require_receiver_directory_path(
            tmp_path,
            Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
            missing_ok=False,
        )


def test_any_extended_acl_on_receiver_chain_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _darwin_path_fixture(
        monkeypatch,
        tmp_path,
        acl_path=tmp_path / "secrets",
    )

    with pytest.raises(integrity.LocalPathIntegrityError, match="extended ACL"):
        integrity.require_receiver_directory_path(
            tmp_path,
            Path("secrets/secs-magik/devgraph.monitor.view.read.v1"),
            missing_ok=False,
        )


def test_non_darwin_path_does_not_call_darwin_mount_or_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "receiver"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(integrity.sys, "platform", "linux")
    monkeypatch.setattr(
        integrity,
        "_darwin_mount_info",
        lambda _path: pytest.fail("Darwin statfs must not run"),
    )
    monkeypatch.setattr(
        integrity,
        "_darwin_has_extended_acl",
        lambda _path: pytest.fail("Darwin ACL API must not run"),
    )

    assert integrity.require_receiver_directory_path(
        tmp_path,
        Path("receiver"),
        missing_ok=False,
    ) == directory
