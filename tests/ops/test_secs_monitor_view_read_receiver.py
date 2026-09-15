from __future__ import annotations

import base64
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devgraph.auth.enforcement import AuditLog
from devgraph.auth.secs_monitor_view_read import (
    DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
    DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
    DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1,
    SecSMonitorViewReadAdapter,
    SecSMonitorViewReadDenied,
)
from devgraph.ops.local_path_integrity import LocalPathIntegrityError
from devgraph.ops.secs_monitor_view_read_receiver import (
    RECEIVER_BUNDLE_RELATIVE_PATH,
    REPLAY_DIRECTORY_NAME,
    REPLAY_LOCK_NAME,
    REPLAY_STORE_NAME,
    DurableSecSMonitorViewReadReplayStore,
    LocalSecSMonitorViewReadError,
    load_local_secs_monitor_view_read_adapter,
)
from devgraph.storage.memory import MemoryGraphStorage


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _write_bundle(data_root: Path) -> Path:
    bundle = data_root / RECEIVER_BUNDLE_RELATIVE_PATH
    bundle.mkdir(parents=True, exist_ok=True)
    private_key = Ed25519PrivateKey.generate()
    manifest = {
        "audience": DEVGRAPH_MONITOR_VIEW_READ_AUDIENCE_V1,
        "operation": DEVGRAPH_MONITOR_VIEW_READ_OPERATION_V1,
        "origin": DEVGRAPH_MONITOR_VIEW_READ_ORIGIN_V1,
        "policy_binding": {
            "policy_digest_sha256": "7c" * 32,
            "policy_id": "devgraph-monitor-view-local-v1",
            "policy_version": 1,
        },
        "schema": "devgraph-secs-monitor-view-read-receiver.v1",
        "schema_version": 1,
        "stable_issuer": "secs:devgraph-receiver-local",
    }
    registry = {
        "keys": [
            {
                "algorithm": "ed25519",
                "key_id": "secs-monitor-test-v1",
                "production_authority": True,
                "public_key_base64url": _b64url(
                    private_key.public_key().public_bytes_raw()
                ),
                "status": "active",
            }
        ],
        "schema": "secs-public-verifier-key-registry.v1",
        "schema_version": 1,
    }
    for name, value in (
        ("receiver.json", manifest),
        ("secs-public-key-registry.json", registry),
    ):
        path = bundle / name
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        path.chmod(0o644)
    return bundle


def _process_claim(
    path: str,
    barrier,
    results,
    digest: str,
    nonce: str,
    now: int,
    expires_at: int,
    started=None,
) -> None:
    if started is not None:
        started.set()
    store = DurableSecSMonitorViewReadReplayStore(Path(path))
    barrier.wait(timeout=10)
    try:
        store.claim(digest, nonce, now=now, expires_at=expires_at)
    except SecSMonitorViewReadDenied as denied:
        results.put(denied.reason)
    else:
        results.put("accepted")


def _process_high_water_claim(
    path: str,
    locked,
    release,
    results,
) -> None:
    def hold_after_temp(stage: str) -> None:
        if stage == "after_temp_fsync":
            locked.set()
            if not release.wait(timeout=10):
                raise OSError("test release timed out")

    store = DurableSecSMonitorViewReadReplayStore(
        Path(path),
        failure_hook=hold_after_temp,
    )
    try:
        store.claim(
            "33" * 32,
            "ISIjJCUmJygpKiss",
            now=1_800_000_301,
            expires_at=1_800_000_401,
        )
    except SecSMonitorViewReadDenied as denied:
        results.put(("high", denied.reason))
    else:
        results.put(("high", "accepted"))


def _process_low_water_claim(path: str, started, results) -> None:
    started.set()
    store = DurableSecSMonitorViewReadReplayStore(Path(path))
    try:
        store.claim(
            "22" * 32,
            "EBESExQVFhcYGRob",
            now=1_800_000_300,
            expires_at=1_800_000_400,
        )
    except SecSMonitorViewReadDenied as denied:
        results.put(denied.reason)
    else:
        results.put("accepted-low")


def test_missing_public_receiver_bundle_preserves_fail_closed_absence(tmp_path: Path) -> None:
    adapter = load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
        clock=lambda: 1_800_000_000,
    )
    assert adapter is None


def test_public_receiver_bundle_loads_with_owner_private_replay_state(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(tmp_path)

    adapter = load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
        clock=lambda: 1_800_000_000,
    )

    assert isinstance(adapter, SecSMonitorViewReadAdapter)
    assert sorted(path.name for path in bundle.iterdir()) == [
        "receiver.json",
        REPLAY_DIRECTORY_NAME,
        "secs-public-key-registry.json",
    ]
    replay_directory = bundle / REPLAY_DIRECTORY_NAME
    replay_store = replay_directory / REPLAY_STORE_NAME
    replay_lock = replay_directory / REPLAY_LOCK_NAME
    assert replay_directory.stat().st_mode & 0o777 == 0o700
    assert replay_store.stat().st_mode & 0o777 == 0o600
    assert replay_lock.stat().st_mode & 0o777 == 0o600
    assert not any("private" in path.name or "secret" in path.name for path in bundle.iterdir())


def test_partial_malformed_symlinked_or_writable_bundle_fails_closed(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    registry = bundle / "secs-public-key-registry.json"
    registry.unlink()
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    _write_bundle(tmp_path)
    manifest = bundle / "receiver.json"
    manifest.chmod(0o666)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    manifest.unlink()
    target = tmp_path / "outside.json"
    target.write_text("{}")
    os.symlink(target, manifest)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )


def test_manifest_rejects_unknown_fields_and_non_exact_authority(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    manifest_path = bundle / "receiver.json"
    manifest = json.loads(manifest_path.read_text())
    for update in (
        {"unexpected": True},
        {"operation": "devgraph.read"},
        {"audience": "devgraph"},
        {"origin": "http://localhost:8080"},
    ):
        candidate = {**manifest, **update}
        manifest_path.write_text(
            json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        )
        with pytest.raises(LocalSecSMonitorViewReadError):
            load_local_secs_monitor_view_read_adapter(
                data_root=tmp_path,
                storage=MemoryGraphStorage(),
                audit_log=AuditLog(),
            )


def test_symlinked_or_group_writable_intermediate_trust_parent_fails_closed(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink_root = tmp_path / "symlink-root"
    symlink_root.mkdir()
    os.symlink(outside, symlink_root / "secrets")
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=symlink_root,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    writable_root = tmp_path / "writable-root"
    writable_root.mkdir()
    _write_bundle(writable_root)
    (writable_root / "secrets").chmod(0o770)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=writable_root,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )


def test_ownership_disabled_mount_signal_fails_before_bundle_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_bundle(tmp_path)

    def ownership_disabled(
        _data_root: Path,
        _relative_path: Path,
        *,
        missing_ok: bool,
    ) -> None:
        assert missing_ok is True
        raise LocalPathIntegrityError(
            "configured data-root mount has ownership disabled"
        )

    monkeypatch.setattr(
        "devgraph.ops.secs_monitor_view_read_receiver.require_receiver_directory_path",
        ownership_disabled,
    )
    with pytest.raises(
        LocalSecSMonitorViewReadError,
        match="mount has ownership disabled",
    ):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )


def test_receiver_clock_failure_is_bounded_and_redacted_at_request_time(
    tmp_path: Path,
) -> None:
    _write_bundle(tmp_path)

    def unavailable_clock() -> int:
        raise RuntimeError("synthetic-secret-clock-marker")

    adapter = load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
        clock=unavailable_clock,
    )
    assert adapter is not None
    with pytest.raises(SecSMonitorViewReadDenied) as denied:
        adapter._verifier._read_clock()
    assert str(denied.value) == "monitor proof denied"
    assert "synthetic-secret-clock-marker" not in str(denied.value)


def test_durable_replay_claim_survives_receiver_process_restart(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
        clock=lambda: 1_800_000_000,
    )
    path = bundle / REPLAY_DIRECTORY_NAME / REPLAY_STORE_NAME
    first_process = DurableSecSMonitorViewReadReplayStore(path)
    first_process.claim(
        "11" * 32,
        "AAECAwQFBgcICQoL",
        now=1_800_000_000,
        expires_at=1_800_000_300,
    )

    restarted_process = DurableSecSMonitorViewReadReplayStore(path)
    before_replay = path.read_bytes()
    with pytest.raises(SecSMonitorViewReadDenied) as denied:
        restarted_process.claim(
            "11" * 32,
            "AAECAwQFBgcICQoL",
            now=1_800_000_001,
            expires_at=1_800_000_300,
        )
    assert denied.value.reason == "monitor_request_replayed"
    assert path.read_bytes() == before_replay


def test_durable_replay_unique_claim_is_atomic_across_receiver_instances(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "replay"
    directory.mkdir(mode=0o700)
    path = directory / REPLAY_STORE_NAME
    stores = [
        DurableSecSMonitorViewReadReplayStore(path),
        DurableSecSMonitorViewReadReplayStore(path),
    ]

    def claim(store: DurableSecSMonitorViewReadReplayStore) -> str:
        try:
            store.claim(
                "11" * 32,
                "AAECAwQFBgcICQoL",
                now=1_800_000_000,
                expires_at=1_800_000_300,
            )
        except SecSMonitorViewReadDenied as denied:
            return denied.reason
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(claim, stores))

    assert results == ["accepted", "monitor_request_replayed"]


def test_durable_replay_unique_claim_is_atomic_across_processes(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    directory = tmp_path / "replay"
    directory.mkdir(mode=0o700)
    path = directory / REPLAY_STORE_NAME
    DurableSecSMonitorViewReadReplayStore(path)
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_process_claim,
            args=(
                str(path),
                barrier,
                results,
                "11" * 32,
                "AAECAwQFBgcICQoL",
                1_800_000_000,
                1_800_000_300,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert sorted(results.get(timeout=2) for _ in processes) == [
        "accepted",
        "monitor_request_replayed",
    ]


def test_concurrent_process_rechecks_durable_clock_high_water_under_lock(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    directory = tmp_path / "replay"
    directory.mkdir(mode=0o700)
    path = directory / REPLAY_STORE_NAME
    DurableSecSMonitorViewReadReplayStore(path)
    locked = context.Event()
    low_started = context.Event()
    release = context.Event()
    results = context.Queue()
    high = context.Process(
        target=_process_high_water_claim,
        args=(str(path), locked, release, results),
    )
    high.start()
    assert locked.wait(timeout=10)
    low = context.Process(
        target=_process_low_water_claim,
        args=(str(path), low_started, results),
    )
    low.start()
    assert low_started.wait(timeout=10)
    release.set()
    high.join(timeout=15)
    low.join(timeout=15)
    assert high.exitcode == low.exitcode == 0

    received = {results.get(timeout=2), results.get(timeout=2)}
    assert ("high", "accepted") in received
    assert "monitor_replay_clock_regressed" in received


def test_atomic_replace_recovers_from_pre_and_post_rename_failures(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "replay"
    directory.mkdir(mode=0o700)
    path = directory / REPLAY_STORE_NAME
    store = DurableSecSMonitorViewReadReplayStore(path)
    store.claim(
        "11" * 32,
        "AAECAwQFBgcICQoL",
        now=1_800_000_000,
        expires_at=1_800_000_300,
    )
    before_pre_rename = path.read_bytes()

    def fail_before_rename(stage: str) -> None:
        if stage == "after_temp_fsync":
            raise OSError("injected pre-rename failure")

    pre_rename = DurableSecSMonitorViewReadReplayStore(
        path,
        failure_hook=fail_before_rename,
    )
    with pytest.raises(SecSMonitorViewReadDenied):
        pre_rename.claim(
            "22" * 32,
            "EBESExQVFhcYGRob",
            now=1_800_000_001,
            expires_at=1_800_000_300,
        )
    assert path.read_bytes() == before_pre_rename
    assert not any(item.name.startswith(".claims.json.") for item in directory.iterdir())

    recovered = DurableSecSMonitorViewReadReplayStore(path)
    recovered.claim(
        "22" * 32,
        "EBESExQVFhcYGRob",
        now=1_800_000_001,
        expires_at=1_800_000_300,
    )
    before_post_rename = path.read_bytes()

    def fail_after_rename(stage: str) -> None:
        if stage == "after_replace":
            raise OSError("injected post-rename failure")

    post_rename = DurableSecSMonitorViewReadReplayStore(
        path,
        failure_hook=fail_after_rename,
    )
    with pytest.raises(SecSMonitorViewReadDenied):
        post_rename.claim(
            "33" * 32,
            "ISIjJCUmJygpKiss",
            now=1_800_000_002,
            expires_at=1_800_000_300,
        )
    assert path.read_bytes() != before_post_rename
    restarted = DurableSecSMonitorViewReadReplayStore(path)
    with pytest.raises(SecSMonitorViewReadDenied) as replayed:
        restarted.claim(
            "33" * 32,
            "ISIjJCUmJygpKiss",
            now=1_800_000_003,
            expires_at=1_800_000_300,
        )
    assert replayed.value.reason == "monitor_request_replayed"


def test_durable_replay_store_fails_closed_on_backward_clock(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
    )
    path = bundle / REPLAY_DIRECTORY_NAME / REPLAY_STORE_NAME
    first_process = DurableSecSMonitorViewReadReplayStore(path)
    first_process.claim(
        "11" * 32,
        "AAECAwQFBgcICQoL",
        now=1_800_000_300,
        expires_at=1_800_000_400,
    )

    restarted_process = DurableSecSMonitorViewReadReplayStore(path)
    before_rollback = path.read_bytes()
    with pytest.raises(SecSMonitorViewReadDenied) as denied:
        restarted_process.claim(
            "22" * 32,
            "EBESExQVFhcYGRob",
            now=1_800_000_299,
            expires_at=1_800_000_400,
        )
    assert denied.value.reason == "monitor_replay_clock_regressed"
    assert path.read_bytes() == before_rollback


def test_durable_replay_store_is_bounded_and_prunes_expired_claims(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "replay"
    directory.mkdir(mode=0o700)
    store = DurableSecSMonitorViewReadReplayStore(
        directory / REPLAY_STORE_NAME,
        maximum_entries=1,
    )
    store.claim(
        "11" * 32,
        "AAECAwQFBgcICQoL",
        now=1_800_000_000,
        expires_at=1_800_000_010,
    )
    before_full = (directory / REPLAY_STORE_NAME).read_bytes()
    with pytest.raises(SecSMonitorViewReadDenied) as denied:
        store.claim(
            "22" * 32,
            "EBESExQVFhcYGRob",
            now=1_800_000_001,
            expires_at=1_800_000_020,
        )
    assert denied.value.reason == "monitor_replay_store_full"
    assert (directory / REPLAY_STORE_NAME).read_bytes() == before_full
    store.claim(
        "22" * 32,
        "EBESExQVFhcYGRob",
        now=1_800_000_010,
        expires_at=1_800_000_020,
    )


def test_replay_directory_and_store_must_remain_owner_private(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(tmp_path)
    replay_directory = bundle / REPLAY_DIRECTORY_NAME
    replay_directory.mkdir(mode=0o700)
    replay_directory.chmod(0o750)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    replay_directory.chmod(0o700)
    target = tmp_path / "outside-replay.json"
    target.write_text("{}")
    os.symlink(target, replay_directory / REPLAY_STORE_NAME)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    (replay_directory / REPLAY_STORE_NAME).unlink()
    load_local_secs_monitor_view_read_adapter(
        data_root=tmp_path,
        storage=MemoryGraphStorage(),
        audit_log=AuditLog(),
    )
    (replay_directory / REPLAY_STORE_NAME).chmod(0o640)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    replay_store = replay_directory / REPLAY_STORE_NAME
    replay_store.chmod(0o600)
    replay_store.write_text("{}")
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )


def test_replay_lock_and_public_trust_files_reject_links(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    replay_directory = bundle / REPLAY_DIRECTORY_NAME
    replay_directory.mkdir(mode=0o700)
    outside_lock = tmp_path / "outside.lock"
    outside_lock.write_text("")
    outside_lock.chmod(0o600)
    os.symlink(outside_lock, replay_directory / REPLAY_LOCK_NAME)
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )

    (replay_directory / REPLAY_LOCK_NAME).unlink()
    manifest = bundle / "receiver.json"
    os.link(manifest, tmp_path / "receiver-hardlink.json")
    with pytest.raises(LocalSecSMonitorViewReadError):
        load_local_secs_monitor_view_read_adapter(
            data_root=tmp_path,
            storage=MemoryGraphStorage(),
            audit_log=AuditLog(),
        )
