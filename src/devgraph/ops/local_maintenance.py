"""Native maintenance for the configured private host; never a restore command.

The backup job only dumps a proven-stopped store. It always attempts service
readmission after a dump failure. Retention deletes only verified artifacts in
its dedicated managed directory, after a new backup has been verified.
"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx

from devgraph.local_host import (
    DEFAULT_CONFIG_PATH,
    LocalHostConfig,
    load_local_config,
    local_read_credential_paths,
)
from devgraph.ops.backup import ArtifactError, _stream_regular_file
from devgraph.ops.local_path_integrity import (
    require_file_descriptor_without_acl,
    require_receiver_directory_path,
)

SCHEMA = "devgraph.local-backup.v1"
BACKUP_RELATIVE = Path("backups/managed-v1")
KEEP_BACKUPS = 7
MAX_BACKUP_AGE = 26 * 3600
MIN_FREE_BYTES = 5 * 1024**3
_ARTIFACT = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
ALERT_ACTIONS = {
    "volume_unavailable": "Reconnect the configured Devgraph volume; inspect local status.",
    "capacity_low": "Free space or expand the Devgraph volume before it fills.",
    "readiness_failed": "Run devgraph local status and inspect bounded API/Neo4j logs.",
    "backup_stale": "Run devgraph local maintenance backup and inspect its result.",
    "backup_failed": "Inspect maintenance backup-result.json; do not delete the stopped store.",
    "maintenance_overdue": "Inspect the running maintenance process and local service status.",
}


class MaintenanceError(RuntimeError):
    pass


def _private_directory(root: Path, relative: Path) -> Path:
    require_receiver_directory_path(root, relative, missing_ok=True)
    current = root
    for part in relative.parts:
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
    require_receiver_directory_path(root, relative, missing_ok=False)
    if current.stat().st_mode & 0o077:
        raise MaintenanceError("maintenance_directory_not_private")
    return current


def _private_file(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > 1024**2
        ):
            raise MaintenanceError("unsafe_maintenance_file")
        require_file_descriptor_without_acl(fd)
        return os.read(fd, 1024**2 + 1)
    finally:
        os.close(fd)


def _write(path: Path, raw: bytes):
    if path.exists() or path.is_symlink():
        _private_file(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _json_write(path: Path, value):
    _write(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def _state_root(config):
    # Status/alerts survive an absent data volume.
    return _private_directory(config.log_root, Path("maintenance"))


@contextmanager
def _backup_lock(config, *, exclusive=True):
    path = _state_root(config) / "backup.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise MaintenanceError("unsafe_maintenance_lock")
        require_file_descriptor_without_acl(fd)
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MaintenanceError("maintenance_in_progress") from None
        yield
    finally:
        os.close(fd)


def _admin(config, *arguments):
    result = subprocess.run(
        [str(config.neo4j_home / "bin/neo4j-admin"), *arguments],
        env={
            "JAVA_HOME": str(config.java_home),
            "NEO4J_CONF": str(config.data_root / "neo4j/conf"),
            "PATH": "/usr/bin:/bin",
            "HEAP_SIZE": "512m",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=600,
    )
    if result.returncode:
        raise MaintenanceError("native_backup_command_failed")


def _snapshot_support(config, artifact):
    files = [
        Path("neo4j/conf/neo4j.conf"),
        Path("secrets/neo4j_password"),
        *(path.relative_to(config.data_root) for path in local_read_credential_paths(config)),
    ]
    for contract in (
        "devgraph.issue.create.v1",
        "devgraph.monitor.view.read.v1",
        "devgraph.work.v1",
    ):
        for name in ("receiver.json", "secs-public-key-registry.json"):
            path = Path("secrets/secs-magik") / contract / name
            if (config.data_root / path).exists():
                files.append(path)
    for relative in files:
        require_receiver_directory_path(config.data_root, relative.parent, missing_ok=False)
        target = artifact / "support" / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write(target, _private_file(config.data_root / relative))
    _json_write(artifact / "local-config.json", config.to_mapping())
    from devgraph.ops.retained_audit import AUDIT_RELATIVE

    audit_path = config.data_root / AUDIT_RELATIVE / "events.sqlite3"
    if audit_path.exists():
        from devgraph.ops.retained_audit import RetainedAuditLog

        audit = RetainedAuditLog(config.data_root)
        target = artifact / "audit.sqlite3"
        target.touch(mode=0o600)
        with sqlite3.connect(audit.path) as source, sqlite3.connect(target) as destination:
            source.backup(destination)


def verify_backup(artifact: Path) -> dict:
    if artifact.is_symlink() or not artifact.is_dir() or not _ARTIFACT.fullmatch(artifact.name):
        raise MaintenanceError("invalid_managed_backup")
    manifest = json.loads(_private_file(artifact / "manifest.json"))
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("complete") is not True
        or manifest.get("artifact_id") != artifact.name
    ):
        raise MaintenanceError("invalid_managed_backup")
    actual = set()
    for path in artifact.rglob("*"):
        if path.is_symlink():
            raise MaintenanceError("unsafe_managed_backup")
        if path.is_file() and path.name != "manifest.json":
            actual.add(path.relative_to(artifact).as_posix())
    expected = manifest["files"]
    if actual != set(expected) or not {"neo4j.dump", "system.dump"} <= actual:
        raise MaintenanceError("incomplete_managed_backup")
    for relative, metadata in expected.items():
        # Equality with the enumerated file set rules out traversal/absolute paths.
        size, digest, _ = _stream_regular_file(artifact / relative)
        if metadata != {"bytes": size, "sha256": digest}:
            raise MaintenanceError("backup_checksum_mismatch")
    return manifest


def retain_backups(root: Path) -> list[str]:
    artifacts = []
    for path in root.iterdir():
        if _ARTIFACT.fullmatch(path.name) and not path.is_symlink() and path.is_dir():
            # Partial, old, unrelated or damaged backups are never deleted automatically.
            try:
                manifest = verify_backup(path)
            except (OSError, ValueError, KeyError, TypeError, MaintenanceError, ArtifactError):
                continue
            artifacts.append((manifest["completed_at"], path))
    artifacts.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    removed = []
    for _, path in artifacts[KEEP_BACKUPS:]:
        shutil.rmtree(path)
        removed.append(path.name)
    return removed


def _wait_ready(config, *, seconds=90):
    from devgraph.cli import local_status_snapshot

    deadline = time.monotonic() + seconds
    while True:
        health = local_status_snapshot()
        if health.get("healthy"):
            return health
        if time.monotonic() >= deadline:
            raise MaintenanceError("service_readmission_failed")
        time.sleep(1)


def _readmit(config):
    # A pre-cutover backup may be run by a newer candidate. Only the selected
    # installed release may decide which migrations to admit on restart.
    result = subprocess.run(
        [str(config.python_executable), "-m", "devgraph.cli", "local", "start"],
        cwd=config.host_root,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=240,
    )
    if result.returncode != 0:
        raise MaintenanceError("service_readmission_failed")
    return json.loads(result.stdout)


def _recovery_snapshot(config, artifact):
    from devgraph.ops.local_recovery import create_recovery

    return create_recovery(config, artifact, lock_held=True)


def backup(config: LocalHostConfig) -> dict:
    from devgraph.cli import local_status_snapshot, stop_local_services

    with _backup_lock(config):
        state = _state_root(config)
        result = {"successful": False, "started_at": time.time(), "reason": "backup_failed"}
        _json_write(state / "backup-result.json", result)
        stopped = False
        try:
            root = _private_directory(config.data_root, BACKUP_RELATIVE)
            usage = shutil.disk_usage(root)
            source_size = sum(
                path.stat().st_size
                for base in ("data", "transactions")
                for path in (config.data_root / "neo4j" / base).rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            if usage.free < max(MIN_FREE_BYTES, source_size * 2):
                raise MaintenanceError("backup_capacity_insufficient")
            health = local_status_snapshot()
            if not health.get("healthy"):
                raise MaintenanceError("source_not_ready")
            artifact_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
            artifact_id += uuid.uuid4().hex[:12]
            artifact = root / artifact_id
            artifact.mkdir(mode=0o700)
            result["artifact_id"] = artifact_id
            # Always attempt admission after any attempted stop, including partial shutdown.
            stopped = True
            result["stop"] = stop_local_services(config)
            _json_write(state / "backup-result.json", result)
            if not result["stop"].get("successful"):
                raise MaintenanceError("source_stop_not_proven")
            for database in ("neo4j", "system"):
                _admin(
                    config,
                    "database",
                    "check",
                    database,
                    "--check-property-owners=true",
                    f"--report-path={artifact}",
                )
                _admin(config, "database", "dump", database, f"--to-path={artifact}")
            _snapshot_support(config, artifact)
            file_metadata = {}
            for path in artifact.rglob("*"):
                if path.is_file():
                    path.chmod(0o600)
                    size, digest, _ = _stream_regular_file(path)
                    file_metadata[path.relative_to(artifact).as_posix()] = {
                        "bytes": size,
                        "sha256": digest,
                    }
            manifest = {
                "schema": SCHEMA,
                "artifact_id": artifact_id,
                "complete": True,
                "completed_at": time.time(),
                "source_release": config.host_root.name,
                "migration": health["api"]["api"]["readiness"]["current_applied_version"],
                "neo4j_runtime": config.neo4j_home.name,
                "files": file_metadata,
                "encrypted_by_application": False,
                "identity_private_key_included": False,
                "authority_producer_state_included": False,
            }
            _json_write(artifact / "manifest.json", manifest)
            verify_backup(artifact)
            # The immutable managed database backup remains useful even when a
            # separate recovery destination or custody export fails. Preserve
            # that evidence without replacing the last fully successful run.
            result["managed_backup_verified"] = True
            _json_write(
                state / "last-managed-backup.json",
                {
                    "artifact_id": artifact_id,
                    "completed_at": time.time(),
                    "verified": True,
                    "migration": manifest["migration"],
                },
            )
            try:
                result["recovery"] = _recovery_snapshot(config, artifact)
            except Exception:
                result["recovery"] = {"successful": False, "reason": "recovery_snapshot_failed"}
            if result["recovery"].get("successful") is not True:
                raise MaintenanceError("recovery_snapshot_failed")
            result.update(
                successful=True,
                reason="backup_verified",
                completed_at=time.time(),
                bytes=sum(item["bytes"] for item in file_metadata.values()),
            )
        except Exception as error:
            # Do not project backend messages, configuration, or credentials.
            result.update(
                successful=False,
                reason=(str(error) if isinstance(error, MaintenanceError) else "backup_failed"),
            )
        finally:
            if stopped:
                try:
                    admitted = _readmit(config)
                    if not admitted.get("successful"):
                        raise MaintenanceError("service_readmission_failed")
                    _wait_ready(config)
                    result["service_ready"] = True
                except Exception:
                    result.update(
                        successful=False, reason="service_readmission_failed", service_ready=False
                    )
            result["finished_at"] = time.time()
            result["duration_seconds"] = round(result["finished_at"] - result["started_at"], 2)
            _json_write(state / "backup-result.json", result)
        if result["successful"]:
            result["removed_managed_backups"] = retain_backups(root)
            _json_write(state / "last-backup.json", result)
            _json_write(state / "backup-result.json", result)
        return result


def evaluate_alerts(
    *,
    volume_available,
    free_bytes,
    total_bytes,
    ready,
    backup_age,
    backup_failed,
    maintenance_age=None,
):
    codes = []
    if not volume_available:
        codes.append("volume_unavailable")
    elif free_bytes < MIN_FREE_BYTES or free_bytes < total_bytes * 0.10:
        codes.append("capacity_low")
    if maintenance_age is None:
        if not ready:
            codes.append("readiness_failed")
    elif maintenance_age > 20 * 60:
        codes.append("maintenance_overdue")
    if backup_age is None or backup_age > MAX_BACKUP_AGE:
        codes.append("backup_stale")
    if backup_failed and maintenance_age is None:
        codes.append("backup_failed")
    return codes


def _notify(codes):
    message = (
        "; ".join(codes) + ". Run devgraph local maintenance status."
        if codes
        else "Devgraph maintenance alerts have cleared."
    )
    # Fixed local desktop notification, no network delivery or arbitrary script content.
    result = subprocess.run(
        [
            "/usr/bin/osascript",
            "-e",
            "display notification " + json.dumps(message) + ' with title "Devgraph maintenance"',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return result.returncode == 0


def monitor(config, *, notify=True):
    state = _state_root(config)
    now = time.time()

    def previous(name):
        try:
            return json.loads(_private_file(state / name))
        except (OSError, ValueError, MaintenanceError):
            return {}

    last = previous("last-backup.json")
    current = previous("backup-result.json")
    maintenance_age = None
    try:
        with _backup_lock(config, exclusive=False):
            pass
    except MaintenanceError as error:
        if str(error) != "maintenance_in_progress":
            raise
        maintenance_age = now - current.get("started_at", 0)
    available = config.availability_path.exists() and config.data_root.is_dir()
    usage = shutil.disk_usage(config.data_root) if available else None
    ready = False
    try:
        with httpx.Client(trust_env=False, timeout=3, follow_redirects=False) as client:
            response = client.get("http://127.0.0.1:8080/ready")
            ready = response.status_code == 200 and response.json().get("ready") is True
            protected = client.get("http://127.0.0.1:8080/work/Issue")
            ready = ready and protected.status_code == 401
    except (httpx.HTTPError, ValueError):
        pass
    backup_age = now - last["completed_at"] if "completed_at" in last else None
    if backup_age is not None:
        try:
            artifact_id = last["artifact_id"]
            if not _ARTIFACT.fullmatch(artifact_id):
                raise MaintenanceError("invalid_backup_reference")
            manifest = json.loads(
                _private_file(config.data_root / BACKUP_RELATIVE / artifact_id / "manifest.json")
            )
            if manifest.get("artifact_id") != artifact_id or manifest.get("complete") is not True:
                raise MaintenanceError("backup_unavailable")
        except (OSError, ValueError, KeyError, TypeError, MaintenanceError):
            backup_age = None
    codes = evaluate_alerts(
        volume_available=available,
        free_bytes=usage.free if usage else 0,
        total_bytes=usage.total if usage else 0,
        ready=ready,
        backup_age=backup_age,
        backup_failed=current.get("successful") is False,
        maintenance_age=maintenance_age,
    )
    result = {
        "checked_at": now,
        "healthy": not codes,
        "ready": ready,
        "volume_available": available,
        "free_bytes": usage.free if usage else None,
        "backup_age_seconds": backup_age,
        "maintenance_age_seconds": maintenance_age,
        "alerts": [{"code": code, "action": ALERT_ACTIONS[code]} for code in codes],
    }
    old_codes = [item["code"] for item in previous("status.json").get("alerts", [])]
    if notify and (codes != old_codes or previous("status.json").get("notification_sent") is False):
        try:
            result["notification_sent"] = _notify(codes)
        except (OSError, subprocess.TimeoutExpired):
            result["notification_sent"] = False
    _json_write(state / "status.json", result)
    return result


def render_maintenance_agents(config):
    agents = {}
    for action in ("backup", "monitor"):
        label = "ca.zenith.devgraph." + action
        job = {
            "Label": label,
            "ProgramArguments": [
                str(config.python_executable),
                "-m",
                "devgraph.cli",
                "local",
                "maintenance",
                action,
            ],
            "WorkingDirectory": str(config.host_root),
            "ProcessType": "Background",
            "EnvironmentVariables": {"PATH": "/usr/bin:/bin"},
            "Umask": 0o077,
            "StandardOutPath": "/dev/null",
            "StandardErrorPath": "/dev/null",
        }
        if action == "backup":
            job["StartCalendarInterval"] = {"Hour": 3, "Minute": 30}
        else:
            job["StartInterval"] = 300
            job["RunAtLoad"] = True
        agents[label + ".plist"] = plistlib.dumps(job, sort_keys=True)
    return agents


def install_maintenance(config):
    domain = f"gui/{os.getuid()}"
    for filename, raw in render_maintenance_agents(config).items():
        path = config.launch_agent_root / filename
        if path.exists() and _private_file(path) != raw:
            raise MaintenanceError("maintenance_job_already_configured")
        _write(path, raw)
        label = filename.removesuffix(".plist")
        loaded = subprocess.run(
            ["/bin/launchctl", "print", f"{domain}/{label}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if loaded.returncode:
            result = subprocess.run(
                ["/bin/launchctl", "bootstrap", domain, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if result.returncode:
                raise MaintenanceError("maintenance_job_admission_failed")
    return {
        "installed": True,
        "backup_local_time": "03:30",
        "keep_verified_backups": 7,
        "monitor_interval_seconds": 300,
        "backup_directory": str(config.data_root / BACKUP_RELATIVE),
    }


def run_maintenance(action):
    config = load_local_config(DEFAULT_CONFIG_PATH)
    if config is None:
        raise MaintenanceError("local_configuration_required")
    if action == "install":
        return install_maintenance(config)
    if action == "backup":
        result = backup(config)
        monitor(config)
        return result
    return monitor(config, notify=action == "monitor")
