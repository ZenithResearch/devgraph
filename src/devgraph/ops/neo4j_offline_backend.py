from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from devgraph.ops.backup import BACKEND_ID, PAYLOAD_MEDIA_TYPE
from devgraph.ops.restore import RestoreCapability, RestoreTarget

PINNED_IMAGE_DIGEST = "sha256:4bae36aff76271e27fd6a6ed0835413f86a284cd179cfb1cb7d188f5f7533aca"
PINNED_IMAGE = f"neo4j:5.26-community@{PINNED_IMAGE_DIGEST}"
DATABASE_NAME = "neo4j"
PAYLOAD_NAME = "neo4j.dump"
TARGET_CLASSIFICATION_LABEL = "devgraph.restore.classification"
TARGET_RECEIVER_LABEL = "devgraph.restore.receiver"
TARGET_AUTHORITY_LABEL = "devgraph.restore.authority"
TARGET_CLASSIFICATION = "disposable_synthetic"

Runner = Callable[..., subprocess.CompletedProcess]
_VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_AUTHORITY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,127}\Z")


@dataclass(frozen=True)
class CommandEvidence:
    operation: str
    exit_code: int
    backend_id: str
    image_digest: str
    argv: tuple[str, ...]
    summary: str
    reported_version: str | None = None


@dataclass(frozen=True)
class _VolumeSnapshot:
    name: str
    physical_identity: str
    classification: str


class BackendError(RuntimeError):
    """Fixed, redacted offline-backend failure."""

    def __init__(self, reason: str, evidence: CommandEvidence) -> None:
        self.reason = reason
        self.evidence = evidence
        super().__init__(reason)


def disposable_target_volume_labels(authority_id: str) -> tuple[str, ...]:
    if not isinstance(authority_id, str) or not _AUTHORITY_ID.fullmatch(authority_id):
        raise ValueError("invalid_disposable_volume_authority")
    return (
        "--label",
        f"{TARGET_CLASSIFICATION_LABEL}={TARGET_CLASSIFICATION}",
        "--label",
        f"{TARGET_RECEIVER_LABEL}={BACKEND_ID}",
        "--label",
        f"{TARGET_AUTHORITY_LABEL}={authority_id}",
    )


def _open_directory_nofollow(path: Path) -> int:
    absolute = Path(path).absolute()
    parts = absolute.parts
    if not parts or parts[0] != absolute.anchor:
        raise OSError("invalid anchored directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
    except OSError:
        os.close(descriptor)
        raise
    return descriptor


def _read_regular_file_nofollow(path: Path) -> bytes:
    absolute = Path(path).absolute()
    parts = absolute.parts
    if not parts or parts[0] != absolute.anchor or len(parts) < 2:
        raise BackendError("invalid_restore_payload", _fixed_evidence("load"))
    directory = _open_directory_nofollow(absolute.parent)
    descriptor = -1
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BackendError("invalid_restore_payload", _fixed_evidence("load"))
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            size < 1
            or (before.st_dev, before.st_ino, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns)
            or after.st_size != size
        ):
            raise BackendError("invalid_restore_payload", _fixed_evidence("load"))
        return b"".join(chunks)
    except BackendError:
        raise
    except OSError:
        raise BackendError("invalid_restore_payload", _fixed_evidence("load")) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _fixed_evidence(operation: str, summary: str = "invalid_input") -> CommandEvidence:
    return CommandEvidence(
        operation=operation,
        exit_code=-1,
        backend_id=BACKEND_ID,
        image_digest=PINNED_IMAGE_DIGEST,
        argv=(),
        summary=summary,
    )


class Neo4jCommunityOfflineDumpBackend:
    """Pinned Community 5.26 offline dump/load adapter using Docker named volumes."""

    backend_id = BACKEND_ID
    backend_version = "1"
    consistency_mode = "offline_consistent"
    payload_media_type = PAYLOAD_MEDIA_TYPE

    def __init__(
        self,
        data_volume: str,
        artifact_directory: Path,
        *,
        timeout_seconds: int = 300,
        runner: Runner = subprocess.run,
    ) -> None:
        raw_artifact = Path(artifact_directory)
        artifact = raw_artifact.absolute()
        if (
            not isinstance(data_volume, str)
            or not _VOLUME_NAME.fullmatch(data_volume)
            or not raw_artifact.is_absolute()
            or type(timeout_seconds) is not int
            or timeout_seconds < 1
            or timeout_seconds > 3600
        ):
            raise ValueError("invalid_owned_backend_resources")
        descriptor = -1
        try:
            descriptor = _open_directory_nofollow(artifact)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("artifact root is not a directory")
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise ValueError("invalid_owned_backend_resources") from None
        self._data_volume = data_volume
        self._artifact_directory = artifact
        self._artifact_descriptor = descriptor
        self._descriptor_finalizer = weakref.finalize(self, _close_descriptor, descriptor)
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._last_evidence: CommandEvidence | None = None
        self._last_source_identity: str | None = None
        self._last_source_size: int | None = None

    @property
    def payload_path(self) -> Path:
        return self._artifact_directory / PAYLOAD_NAME

    @property
    def last_evidence(self) -> CommandEvidence | None:
        return self._last_evidence

    @property
    def source_storage_identity(self) -> str:
        if self._last_source_identity is None:
            self._last_source_identity = self._inspect_volume().physical_identity
        return self._last_source_identity

    @property
    def source_restore_size_bytes(self) -> int:
        if self._last_source_size is None:
            snapshot = self._inspect_volume()
            holder = self._create_holder(snapshot)
            try:
                self._last_source_size = self._volume_size(holder)
            finally:
                self._remove_container(holder)
        return self._last_source_size

    def _safe_argv(self, argv: Sequence[str], container_name: str | None = None) -> tuple[str, ...]:
        safe: list[str] = []
        for item in argv:
            if container_name is not None and item == container_name:
                safe.append("<container>")
            elif item.startswith("type=volume,source="):
                target = item.rsplit(",target=", 1)[-1]
                safe.append(f"type=volume,source=<volume>,target={target}")
            else:
                safe.append(item)
        return tuple(safe)

    def _invoke(
        self,
        operation: str,
        argv: tuple[str, ...],
        *,
        container_name: str | None = None,
        input_data: bytes | None = None,
        binary: bool = False,
    ) -> tuple[subprocess.CompletedProcess, CommandEvidence]:
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": not binary,
            "timeout": self._timeout_seconds,
            "check": False,
            "shell": False,
        }
        if input_data is not None:
            kwargs["input"] = input_data
            kwargs["text"] = False
        safe_argv = self._safe_argv(argv, container_name)
        try:
            completed = self._runner(argv, **kwargs)
        except KeyboardInterrupt:
            if container_name is not None:
                self._remove_container(container_name, tolerate_absent=True)
            raise BackendError(
                "backend_interrupted",
                CommandEvidence(
                    operation, -1, self.backend_id, PINNED_IMAGE_DIGEST, safe_argv, "interrupted"
                ),
            ) from None
        except subprocess.TimeoutExpired:
            if container_name is not None:
                self._remove_container(container_name, tolerate_absent=True)
            raise BackendError(
                "backend_timeout",
                CommandEvidence(
                    operation, -1, self.backend_id, PINNED_IMAGE_DIGEST, safe_argv, "timeout"
                ),
            ) from None
        except (OSError, ValueError, TypeError):
            raise BackendError(
                "backend_command_unavailable",
                CommandEvidence(
                    operation,
                    -1,
                    self.backend_id,
                    PINNED_IMAGE_DIGEST,
                    safe_argv,
                    "command_unavailable",
                ),
            ) from None
        stdout = completed.stdout
        reported_version = None
        if operation == "version" and completed.returncode == 0 and isinstance(stdout, str):
            match = re.search(r"(?<!\d)(5\.26\.\d+)(?!\d)", stdout)
            reported_version = match.group(1) if match else None
        evidence = CommandEvidence(
            operation=operation,
            exit_code=int(completed.returncode),
            backend_id=self.backend_id,
            image_digest=PINNED_IMAGE_DIGEST,
            argv=safe_argv,
            summary="command_completed" if completed.returncode == 0 else "command_failed",
            reported_version=reported_version,
        )
        self._last_evidence = evidence
        if completed.returncode != 0:
            raise BackendError("backend_command_failed", evidence)
        return completed, evidence

    def _remove_container(self, name: str, *, tolerate_absent: bool = False) -> None:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": min(self._timeout_seconds, 30),
            "check": False,
            "shell": False,
        }
        try:
            removed = self._runner(("docker", "rm", "--force", name), **kwargs)
            inspected = self._runner(("docker", "inspect", name), **kwargs)
        except Exception:
            raise BackendError("backend_cleanup_failed", _fixed_evidence("cleanup")) from None
        absent = inspected.returncode != 0
        if not absent or (removed.returncode != 0 and not tolerate_absent):
            raise BackendError("backend_cleanup_failed", _fixed_evidence("cleanup"))

    def _create_temp_volume(self) -> str:
        name = f"devgraph-backup-{uuid4().hex}"
        self._invoke(
            "volume_create",
            (
                "docker",
                "volume",
                "create",
                "--label",
                "devgraph.temporary=true",
                name,
            ),
        )
        initializer = f"devgraph-volume-init-{uuid4().hex}"
        mount = f"type=volume,source={name},target=/backups,volume-nocopy"
        try:
            self._invoke(
                "volume_initialize",
                (
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    initializer,
                    "--user",
                    "0:0",
                    "--entrypoint",
                    "chown",
                    "--mount",
                    mount,
                    PINNED_IMAGE,
                    "neo4j:neo4j",
                    "/backups",
                ),
                container_name=initializer,
            )
        except BackendError:
            self._remove_volume(name)
            raise
        return name

    def _remove_volume(self, name: str) -> None:
        kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": min(self._timeout_seconds, 30),
            "check": False,
            "shell": False,
        }
        try:
            removed = self._runner(("docker", "volume", "rm", "--force", name), **kwargs)
            inspected = self._runner(("docker", "volume", "inspect", name), **kwargs)
        except Exception:
            raise BackendError(
                "backend_volume_cleanup_failed", _fixed_evidence("cleanup")
            ) from None
        if removed.returncode != 0 or inspected.returncode == 0:
            raise BackendError("backend_volume_cleanup_failed", _fixed_evidence("cleanup"))

    def _inspect_volume(self) -> _VolumeSnapshot:
        completed, _ = self._invoke(
            "volume_inspect",
            (
                "docker",
                "volume",
                "inspect",
                self._data_volume,
                "--format",
                "{{json .}}",
            ),
        )
        try:
            raw = json.loads(str(completed.stdout))
            if not isinstance(raw, dict) or raw.get("Name") != self._data_volume:
                raise ValueError
            driver = raw.get("Driver")
            scope = raw.get("Scope")
            created_at = raw.get("CreatedAt")
            mountpoint = raw.get("Mountpoint")
            labels = raw.get("Labels") or {}
            if not all(
                isinstance(item, str) and item for item in (driver, scope, created_at, mountpoint)
            ):
                raise ValueError
            if not isinstance(labels, dict):
                raise ValueError
            authority = labels.get(TARGET_AUTHORITY_LABEL)
            authorized = (
                labels.get(TARGET_CLASSIFICATION_LABEL) == TARGET_CLASSIFICATION
                and labels.get(TARGET_RECEIVER_LABEL) == BACKEND_ID
                and isinstance(authority, str)
                and _AUTHORITY_ID.fullmatch(authority) is not None
            )
            identity_bytes = json.dumps(
                {
                    "authority": authority,
                    "created_at": created_at,
                    "driver": driver,
                    "mountpoint": mountpoint,
                    "name": self._data_volume,
                    "scope": scope,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            physical = "volume-" + hashlib.sha256(identity_bytes).hexdigest()[:32]
        except (KeyError, RecursionError, TypeError, ValueError, json.JSONDecodeError):
            raise BackendError(
                "invalid_volume_inspection", _fixed_evidence("volume_inspect")
            ) from None
        return _VolumeSnapshot(
            name=self._data_volume,
            physical_identity=physical,
            classification=TARGET_CLASSIFICATION if authorized else "unclassified",
        )

    def _create_holder(self, expected: _VolumeSnapshot) -> str:
        holder = f"devgraph-holder-{uuid4().hex}"
        mount = (
            f"type=volume,source={self._data_volume},target=/data,volume-nocopy"
        )
        self._invoke(
            "holder_create",
            (
                "docker",
                "create",
                "--name",
                holder,
                "--mount",
                mount,
                PINNED_IMAGE,
                "true",
            ),
            container_name=holder,
        )
        observed = self._inspect_volume()
        if observed != expected:
            self._remove_container(holder)
            raise BackendError("volume_identity_changed", _fixed_evidence("holder_create"))
        return holder

    def _probe(
        self, holder: str, operation: str, command: tuple[str, ...]
    ) -> subprocess.CompletedProcess:
        name = f"devgraph-{operation}-{uuid4().hex}"
        entrypoint, *arguments = command
        completed, _ = self._invoke(
            operation,
            (
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--user",
                "7474:7474",
                "--entrypoint",
                entrypoint,
                "--volumes-from",
                holder,
                PINNED_IMAGE,
                *arguments,
            ),
            container_name=name,
        )
        return completed

    def _volume_size(self, holder: str) -> int:
        completed = self._probe(holder, "volume_size", ("du", "-sk", "/data"))
        try:
            value = int(str(completed.stdout).strip().split()[0]) * 1024
        except (IndexError, TypeError, ValueError):
            raise BackendError("invalid_volume_size", _fixed_evidence("volume_size")) from None
        return max(1, value)

    def _target_from_holder(self, holder: str, snapshot: _VolumeSnapshot) -> RestoreTarget:
        empty_result = self._probe(
            holder,
            "target_empty",
            ("find", "/data", "-mindepth", "1", "-maxdepth", "1", "-print", "-quit"),
        )
        capacity_result = self._probe(holder, "target_capacity", ("df", "-Pk", "/data"))
        try:
            fields = str(capacity_result.stdout).strip().splitlines()[-1].split()
            available = int(fields[3]) * 1024
        except (IndexError, TypeError, ValueError):
            raise BackendError(
                "invalid_target_capacity", _fixed_evidence("target_capacity")
            ) from None
        return RestoreTarget(
            logical_id=f"restore-{snapshot.physical_identity.removeprefix('volume-')[:24]}",
            physical_identity=snapshot.physical_identity,
            classification=snapshot.classification,
            reachable=True,
            empty=not bool(str(empty_result.stdout).strip()),
            available_bytes=available,
        )

    def inspect_target(self) -> RestoreTarget:
        snapshot = self._inspect_volume()
        holder = self._create_holder(snapshot)
        try:
            target = self._target_from_holder(holder, snapshot)
            if self._inspect_volume() != snapshot:
                raise BackendError("volume_identity_changed", _fixed_evidence("target_inspect"))
            return target
        finally:
            self._remove_container(holder)

    def inspect_capability(self) -> RestoreCapability:
        evidence = self.version()
        if evidence.reported_version is None:
            raise BackendError("invalid_backend_version_output", evidence)
        return RestoreCapability(
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            consistency_modes=(self.consistency_mode,),
            payload_media_types=(self.payload_media_type,),
            database_editions=("community",),
            runtime_database_version=evidence.reported_version,
            payload_count=1,
            target_classifications=(TARGET_CLASSIFICATION,),
        )

    def version(self) -> CommandEvidence:
        name = f"devgraph-version-{uuid4().hex}"
        _, evidence = self._invoke(
            "version",
            (
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                PINNED_IMAGE,
                "neo4j-admin",
                "--version",
            ),
            container_name=name,
        )
        return evidence

    def _write_payload(self, payload: bytes) -> None:
        descriptor = -1
        try:
            descriptor = os.open(
                PAYLOAD_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o400,
                dir_fd=self._artifact_descriptor,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        except OSError:
            raise BackendError("backup_payload_write_failed", _fixed_evidence("dump")) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def dump(self) -> CommandEvidence:
        if os.listdir(self._artifact_descriptor):
            raise BackendError("backup_target_not_empty", _fixed_evidence("dump"))
        snapshot = self._inspect_volume()
        holder = self._create_holder(snapshot)
        backup_volume = ""
        try:
            source_size = self._volume_size(holder)
            backup_volume = self._create_temp_volume()
            name = f"devgraph-dump-{uuid4().hex}"
            mount = (
                f"type=volume,source={backup_volume},target=/backups,volume-nocopy"
            )
            _, evidence = self._invoke(
                "dump",
                (
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    name,
                    "--volumes-from",
                    holder,
                    "--mount",
                    mount,
                    PINNED_IMAGE,
                    "neo4j-admin",
                    "database",
                    "dump",
                    "--to-path=/backups",
                    DATABASE_NAME,
                ),
                container_name=name,
            )
            export_name = f"devgraph-export-{uuid4().hex}"
            exported, _ = self._invoke(
                "payload_export",
                (
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    export_name,
                    "--mount",
                    mount,
                    PINNED_IMAGE,
                    "cat",
                    f"/backups/{PAYLOAD_NAME}",
                ),
                container_name=export_name,
                binary=True,
            )
            payload = exported.stdout
            if not isinstance(payload, bytes) or not payload:
                raise BackendError("backup_payload_missing", _fixed_evidence("dump"))
            self._write_payload(payload)
            self._last_source_identity = snapshot.physical_identity
            self._last_source_size = source_size
            return evidence
        finally:
            if backup_volume:
                self._remove_volume(backup_volume)
            self._remove_container(holder)

    def load(
        self,
        payloads: tuple[Path, ...],
        expected_target: RestoreTarget,
        required_restore_bytes: int,
    ) -> CommandEvidence:
        if (
            len(payloads) != 1
            or type(required_restore_bytes) is not int
            or required_restore_bytes < 1
        ):
            raise BackendError("invalid_restore_payload_or_target", _fixed_evidence("load"))
        payload = _read_regular_file_nofollow(payloads[0])
        snapshot = self._inspect_volume()
        holder = self._create_holder(snapshot)
        backup_volume = ""
        try:
            observed = self._target_from_holder(holder, snapshot)
            stable = (
                observed.logical_id == expected_target.logical_id
                and observed.physical_identity == expected_target.physical_identity
                and observed.classification == expected_target.classification
                and observed.reachable is expected_target.reachable
                and observed.empty is expected_target.empty
                and observed.available_bytes >= required_restore_bytes
            )
            if not stable or not observed.empty:
                raise BackendError("invalid_restore_payload_or_target", _fixed_evidence("load"))
            backup_volume = self._create_temp_volume()
            mount = (
                f"type=volume,source={backup_volume},target=/backups,volume-nocopy"
            )
            import_name = f"devgraph-import-{uuid4().hex}"
            self._invoke(
                "payload_import",
                (
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--name",
                    import_name,
                    "--mount",
                    mount,
                    PINNED_IMAGE,
                    "tee",
                    f"/backups/{PAYLOAD_NAME}",
                ),
                container_name=import_name,
                input_data=payload,
                binary=True,
            )
            load_name = f"devgraph-load-{uuid4().hex}"
            _, evidence = self._invoke(
                "load",
                (
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    load_name,
                    "--volumes-from",
                    holder,
                    "--mount",
                    mount,
                    PINNED_IMAGE,
                    "neo4j-admin",
                    "database",
                    "load",
                    "--from-path=/backups",
                    DATABASE_NAME,
                ),
                container_name=load_name,
            )
            return evidence
        finally:
            if backup_volume:
                self._remove_volume(backup_volume)
            self._remove_container(holder)
