from __future__ import annotations

import hashlib
import json
import plistlib
from types import SimpleNamespace

import pytest

from devgraph.local_host import LocalHostConfig
from devgraph.ops import local_maintenance as maintenance


def config(tmp_path):
    for name in ("data", "logs", "agents", "host"):
        (tmp_path / name).mkdir(mode=0o700)
    return LocalHostConfig.build(
        data_root=tmp_path / "data",
        log_root=tmp_path / "logs",
        launch_agent_root=tmp_path / "agents",
        host_root=tmp_path / "host",
    )


def artifact(root, index):
    path = root / f"20260910T003000Z-{index:012x}"
    path.mkdir(mode=0o700)
    entries = {}
    for name in ("neo4j.dump", "system.dump"):
        (path / name).write_bytes(b"fixture-dump")
        entries[name] = {"bytes": 12, "sha256": hashlib.sha256(b"fixture-dump").hexdigest()}
    maintenance._json_write(
        path / "manifest.json",
        {
            "schema": maintenance.SCHEMA,
            "complete": True,
            "artifact_id": path.name,
            "completed_at": index,
            "files": entries,
        },
    )
    return path


def test_retention_only_removes_verified_owned_artifacts(tmp_path):
    copies = [artifact(tmp_path, i) for i in range(9)]
    unrelated = tmp_path / "previous-recovery"
    unrelated.mkdir()
    partial = tmp_path / "20260910T003000Z-ffffffffffff"
    partial.mkdir()
    (partial / "incomplete.dump").write_text("partial")
    removed = maintenance.retain_backups(tmp_path)
    assert set(removed) == {copies[0].name, copies[1].name}
    assert all(path.exists() for path in copies[2:])
    assert partial.exists() and unrelated.exists()


def test_corrupt_or_symlinked_backup_cannot_verify_or_be_pruned(tmp_path):
    path = artifact(tmp_path, 0)
    (path / "neo4j.dump").write_bytes(b"changed-data")
    with pytest.raises(maintenance.MaintenanceError, match="checksum"):
        maintenance.verify_backup(path)
    assert maintenance.retain_backups(tmp_path) == []
    (path / "neo4j.dump").unlink()
    (path / "neo4j.dump").symlink_to(path / "system.dump")
    with pytest.raises(maintenance.MaintenanceError, match="unsafe"):
        maintenance.verify_backup(path)


def test_missing_or_full_volume_and_overdue_backup_are_actionable():
    args = dict(
        volume_available=True,
        free_bytes=100 * 1024**3,
        total_bytes=500 * 1024**3,
        ready=True,
        backup_age=10,
        backup_failed=False,
    )
    assert maintenance.evaluate_alerts(**args) == []
    assert maintenance.evaluate_alerts(**{**args, "volume_available": False}) == [
        "volume_unavailable"
    ]
    assert maintenance.evaluate_alerts(**{**args, "free_bytes": 1}) == ["capacity_low"]
    assert maintenance.evaluate_alerts(**{**args, "ready": False, "backup_age": None}) == [
        "readiness_failed",
        "backup_stale",
    ]
    assert maintenance.evaluate_alerts(**{**args, "ready": False}, maintenance_age=30) == []
    assert maintenance.evaluate_alerts(**args, maintenance_age=1201) == ["maintenance_overdue"]


def test_jobs_have_bounded_schedule_and_no_ambient_credentials(tmp_path):
    jobs = maintenance.render_maintenance_agents(config(tmp_path))
    backup = plistlib.loads(jobs["ca.zenith.devgraph.backup.plist"])
    monitor = plistlib.loads(jobs["ca.zenith.devgraph.monitor.plist"])
    assert backup["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert monitor["StartInterval"] == 300
    assert "KeepAlive" not in backup and "RunAtLoad" not in backup
    assert backup["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}
    assert backup["Umask"] == 0o077


def test_lock_excludes_other_backups_and_reports_maintenance(tmp_path):
    settings = config(tmp_path)
    with maintenance._backup_lock(settings):
        with pytest.raises(maintenance.MaintenanceError, match="maintenance_in_progress"):
            with maintenance._backup_lock(settings):
                pass


@pytest.mark.parametrize("failure", ["stop", "dump", "restart", "recovery", None])
def test_backup_restarts_after_failures_and_only_publishes_verified_success(
    tmp_path, monkeypatch, failure
):
    settings = config(tmp_path)
    calls = []
    monkeypatch.setattr(
        maintenance.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3)
    )
    monkeypatch.setattr(
        "devgraph.cli.local_status_snapshot",
        lambda: {"healthy": True, "api": {"api": {"readiness": {"current_applied_version": 25}}}},
    )

    def stop(value):
        calls.append("stop")
        return {"successful": failure != "stop"}

    def start(value):
        calls.append("start")
        return {"successful": failure != "restart"}

    def admin(value, *args):
        calls.append(args[1])
        if failure == "dump":
            raise maintenance.MaintenanceError("native_backup_command_failed")
        if args[1] == "dump":
            root = next((settings.data_root / maintenance.BACKUP_RELATIVE).iterdir())
            (root / (args[2] + ".dump")).write_bytes(b"dump")

    monkeypatch.setattr("devgraph.cli.stop_local_services", stop)
    monkeypatch.setattr(maintenance, "_readmit", start)
    monkeypatch.setattr(maintenance, "_admin", admin)
    monkeypatch.setattr(maintenance, "_snapshot_support", lambda *args: None)
    monkeypatch.setattr(
        maintenance, "_recovery_snapshot", lambda *args: {"successful": failure != "recovery"}
    )
    monkeypatch.setattr(maintenance, "_wait_ready", lambda *args: None)
    result = maintenance.backup(settings)
    assert calls[0] == "stop" and calls[-1] == "start"
    assert result["successful"] is (failure is None)
    last = settings.log_root / "maintenance/last-backup.json"
    assert last.exists() is (failure is None)
    if failure == "stop":
        assert "dump" not in calls and "check" not in calls
        assert result["stop"] == {"successful": False}
    if failure == "recovery":
        assert result["managed_backup_verified"] is True
        assert result["service_ready"] is True
        assert result["reason"] == "recovery_snapshot_failed"
        assert (settings.log_root / "maintenance/last-managed-backup.json").exists()
    if failure is None:
        retained = json.loads(last.read_text())
        assert retained["successful"] is True and retained["service_ready"] is True
