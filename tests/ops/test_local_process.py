from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from devgraph.cli import restart_local_services, start_local_services, stop_local_services
from devgraph.local_host import LocalHostConfig
from devgraph.ops.local_process import neo4j_process_snapshot, wait_for_neo4j_exit


@pytest.fixture
def config(tmp_path: Path) -> LocalHostConfig:
    data = tmp_path / "data"
    (data / "neo4j" / "run").mkdir(parents=True, mode=0o700)
    return LocalHostConfig.build(data_root=data)


def _pid_file(config: LocalHostConfig, content: bytes = b"23456") -> Path:
    path = config.data_root / "neo4j" / "run" / "neo4j.pid"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("exists,state", [(True, "running"), (False, "stopped")])
def test_probe_live_and_stale_pid_without_signalling(config, exists, state):
    path = _pid_file(config)
    seen = []

    def probe(pid):
        seen.append(pid)
        return exists

    assert neo4j_process_snapshot(config, probe=probe)["state"] == state
    assert seen == [23456]
    assert path.read_bytes() == b"23456"


@pytest.mark.parametrize("raw", [b"", b"1", b"-2", b"0", b"2147483648", b"x" * 34])
def test_invalid_pid_is_unknown_without_process_probe(config, raw):
    _pid_file(config, raw)

    def forbidden(_pid):
        pytest.fail("invalid PID must not reach a process probe")

    result = neo4j_process_snapshot(config, probe=forbidden)
    assert result["state"] == "unknown"
    assert result["pid"] is None


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "directory", "fifo"])
def test_unsafe_pid_file_is_unknown(config, tmp_path, kind):
    path = config.data_root / "neo4j" / "run" / "neo4j.pid"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("23456")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        import os

        os.mkfifo(path)
    else:
        _pid_file(config)
        if kind == "hardlink":
            (tmp_path / "link").hardlink_to(path)
        else:
            path.chmod(0o666)
    assert neo4j_process_snapshot(config)["state"] == "unknown"


def test_process_probe_permission_failure_is_unknown_and_redacted(config):
    _pid_file(config)

    def denied(_pid):
        raise PermissionError("private process detail")

    result = neo4j_process_snapshot(config, probe=denied)
    assert result["state"] == "unknown"
    assert "private" not in json.dumps(result)


def test_missing_storage_is_not_a_successful_stop(config):
    missing = replace(config, data_root=config.data_root / "missing")
    assert neo4j_process_snapshot(missing)["reason"] == "configured_storage_unavailable"
    assert wait_for_neo4j_exit(missing)["successful"] is False


def test_unmounted_directory_is_not_admitted_as_a_volume(config):
    unmounted = replace(config, storage_mode="mounted_volume")
    assert neo4j_process_snapshot(unmounted)["reason"] == "configured_storage_unavailable"


def test_wait_for_exit_observes_shutdown_without_deleting_pid_file(config):
    path = _pid_file(config)
    states = iter([True, True, False])
    ticks = [0.0]
    result = wait_for_neo4j_exit(
        config,
        snapshot=lambda selected: neo4j_process_snapshot(selected, probe=lambda _: next(states)),
        clock=lambda: ticks[0],
        sleeper=lambda duration: ticks.__setitem__(0, ticks[0] + duration),
    )
    assert result["successful"] is True
    assert path.exists()


@pytest.mark.parametrize("shutdown_seconds", [33, 65, 105])
def test_default_wait_admits_observed_slow_graceful_shutdown(config, shutdown_seconds):
    _pid_file(config)
    ticks = [0.0]
    result = wait_for_neo4j_exit(
        config,
        snapshot=lambda selected: neo4j_process_snapshot(
            selected, probe=lambda _: ticks[0] < shutdown_seconds
        ),
        clock=lambda: ticks[0],
        sleeper=lambda duration: ticks.__setitem__(0, ticks[0] + duration),
    )
    assert result["successful"] is True
    assert shutdown_seconds <= ticks[0] < shutdown_seconds + 1


def test_shutdown_timeout_is_bounded_and_does_not_force_termination(config):
    _pid_file(config)
    ticks = [0.0]
    result = wait_for_neo4j_exit(
        config,
        timeout_seconds=1,
        snapshot=lambda selected: neo4j_process_snapshot(selected, probe=lambda _: True),
        clock=lambda: ticks[0],
        sleeper=lambda duration: ticks.__setitem__(0, ticks[0] + duration),
    )
    assert result["successful"] is False
    assert result["reason"] == "neo4j_shutdown_timeout"
    assert ticks[0] == pytest.approx(1)


@pytest.mark.parametrize("timeout", [True, -1, float("nan"), float("inf"), 121])
def test_shutdown_rejects_unbounded_timeout(config, timeout):
    with pytest.raises(ValueError):
        wait_for_neo4j_exit(config, timeout_seconds=timeout)


def test_start_holds_an_unmanaged_database_before_any_service_or_migration(config, monkeypatch):
    monkeypatch.setattr(
        "devgraph.cli.neo4j_process_snapshot",
        lambda _: {"pid": 23456, "state": "running", "reason": "neo4j_pid_alive"},
    )
    monkeypatch.setattr("devgraph.cli.launchd_snapshot", lambda: {"services": {}})
    monkeypatch.setattr("devgraph.cli.manage_services", lambda *a, **k: pytest.fail("admission"))
    monkeypatch.setattr("devgraph.cli.apply_local_migrations", lambda _: pytest.fail("migration"))
    result = start_local_services(config)
    assert result["successful"] is False
    assert result["reason"] == "unmanaged_neo4j_process"


def test_unloaded_jobs_with_surviving_database_are_not_a_successful_stop(config, monkeypatch):
    monkeypatch.setattr("devgraph.cli.manage_services", lambda *a, **k: {"successful": True})
    monkeypatch.setattr(
        "devgraph.cli.wait_for_neo4j_exit",
        lambda _, **kw: {"successful": False, "reason": "neo4j_shutdown_timeout"},
    )
    monkeypatch.setattr("devgraph.cli.start_local_services", lambda _: pytest.fail("restart"))
    assert stop_local_services(config)["successful"] is False
    result = restart_local_services(config)
    assert result["successful"] is False
    assert result["start"] is None


def test_removing_pid_file_does_not_prove_previously_tracked_process_exited(config):
    ticks = [0.0]
    seen = []
    result = wait_for_neo4j_exit(
        config,
        tracked_pid=23456,
        timeout_seconds=1,
        snapshot=lambda _: {"pid": None, "state": "stopped", "reason": "neo4j_pid_absent"},
        probe=lambda pid: seen.append(pid) or True,
        clock=lambda: ticks[0],
        sleeper=lambda duration: ticks.__setitem__(0, ticks[0] + duration),
    )
    assert result["successful"] is False
    assert result["reason"] == "neo4j_shutdown_timeout"
    assert seen and set(seen) == {23456}


def test_tracked_exit_requires_current_store_process_and_storage_proof(config):
    result = wait_for_neo4j_exit(config, tracked_pid=23456, probe=lambda _: False)
    assert result["successful"] is True
    result = wait_for_neo4j_exit(
        config,
        tracked_pid=23456,
        probe=lambda _: False,
        snapshot=lambda _: {
            "pid": None,
            "state": "unknown",
            "reason": "configured_storage_unavailable",
        },
    )
    assert result["successful"] is False


def test_stop_captures_pid_before_unload_and_retains_diagnostics(config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "devgraph.cli.neo4j_process_snapshot",
        lambda _: (
            calls.append("capture")
            or {"pid": 23456, "state": "running", "reason": "neo4j_pid_alive"}
        ),
    )
    monkeypatch.setattr(
        "devgraph.cli.manage_services",
        lambda *a, **kw: calls.append("unload") or {"successful": True},
    )

    def wait(_, *, tracked_pid):
        assert tracked_pid == 23456
        calls.append("wait")
        return {"successful": True, "state": "stopped", "reason": "neo4j_pid_absent", "pid": None}

    monkeypatch.setattr("devgraph.cli.wait_for_neo4j_exit", wait)
    monkeypatch.setattr(
        "devgraph.cli.launchd_snapshot",
        lambda: {"services": {"api": {"loaded": False}, "neo4j": {"loaded": False}}},
    )
    result = stop_local_services(config)
    assert calls == ["capture", "unload", "wait"]
    assert result["successful"] is True
    assert result["initial_neo4j_process"]["pid"] == 23456
    assert result["jobs_unloaded"] is True
    assert result["duration_seconds"] >= 0


def test_not_ready_migration_result_never_admits_api(config, monkeypatch):
    calls = []

    def manage(action, **kwargs):
        calls.append((action, kwargs["services"]))
        return {"successful": True}

    monkeypatch.setattr("devgraph.cli.manage_services", manage)
    monkeypatch.setattr("devgraph.cli.apply_local_migrations", lambda _: {"ready": False})
    result = start_local_services(config)
    assert result["successful"] is False
    assert result["api"] is None
    assert calls == [("down", ("api",)), ("up", ("neo4j",))]
