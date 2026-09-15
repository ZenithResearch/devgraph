from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from devgraph.model.validation import validate_version, validate_work_object_id

ARTIFACT_MANIFEST_NAME = "manifest.json"
ARTIFACT_SCHEMA_VERSION = 1
BACKEND_ID = "neo4j-community-5.26-offline-dump-v1"
PAYLOAD_MEDIA_TYPE = "application/vnd.neo4j.database-dump"


class ArtifactError(ValueError):
    """Fixed, non-secret backup-artifact failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _identifier(value: object, reason: str) -> str:
    try:
        return validate_work_object_id(value)
    except (TypeError, ValueError):
        raise ArtifactError(reason) from None


def _text(value: object, reason: str, *, maximum: int = 128) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ArtifactError(reason)
    return value


def _token(value: object, reason: str) -> str:
    value = _text(value, reason)
    if not all(
        character.isascii() and (character.isalnum() or character in "._-") for character in value
    ):
        raise ArtifactError(reason)
    return value


def _integer(value: object, reason: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ArtifactError(reason)
    return value


def _migration_version(value: object) -> int:
    try:
        return validate_version(value)
    except (TypeError, ValueError):
        raise ArtifactError("invalid_migration_version") from None


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ArtifactError("invalid_artifact_path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ArtifactError("invalid_artifact_path")
    return value


def _sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ArtifactError("invalid_artifact_checksum")
    return value


def _exact_mapping(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ArtifactError("malformed_backup_manifest")
    return value


@dataclass(frozen=True)
class BackupEncryption:
    encrypted: bool
    algorithm_profile: str
    key_reference: str | None

    def __post_init__(self) -> None:
        if type(self.encrypted) is not bool:
            raise ArtifactError("invalid_encryption_metadata")
        _token(self.algorithm_profile, "invalid_encryption_metadata")
        if self.encrypted:
            if self.algorithm_profile == "none" or self.key_reference is None:
                raise ArtifactError("invalid_encryption_metadata")
            _identifier(self.key_reference, "invalid_encryption_metadata")
        elif self.algorithm_profile != "none" or self.key_reference is not None:
            raise ArtifactError("invalid_encryption_metadata")


@dataclass(frozen=True)
class BackupMetadata:
    artifact_id: str
    created_at: datetime
    source_database_id: str
    source_storage_identity: str
    restore_size_bytes: int
    neo4j_edition: str
    neo4j_version: str
    migration_current_version: int
    migration_minimum_version: int
    migration_maximum_version: int
    backend_id: str
    backend_version: str
    consistency_mode: str
    payload_media_type: str
    encryption: BackupEncryption
    completion_state: str

    def __post_init__(self) -> None:
        _identifier(self.artifact_id, "invalid_artifact_id")
        _identifier(self.source_database_id, "invalid_source_database_id")
        _token(self.source_storage_identity, "invalid_source_storage_identity")
        _integer(self.restore_size_bytes, "invalid_restore_size", minimum=1)
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ArtifactError("invalid_artifact_created_at")
        if self.created_at.utcoffset() != timezone.utc.utcoffset(self.created_at):
            raise ArtifactError("invalid_artifact_created_at")
        _text(self.neo4j_edition, "invalid_neo4j_metadata")
        _text(self.neo4j_version, "invalid_neo4j_metadata")
        current = _migration_version(self.migration_current_version)
        minimum = _migration_version(self.migration_minimum_version)
        maximum = _migration_version(self.migration_maximum_version)
        if not minimum <= current <= maximum:
            raise ArtifactError("invalid_migration_version")
        _token(self.backend_id, "invalid_backend_metadata")
        _token(self.backend_version, "invalid_backend_metadata")
        if self.consistency_mode not in {
            "offline_consistent",
            "online_backend_consistent",
            "logical_export_non_atomic",
        }:
            raise ArtifactError("invalid_consistency_mode")
        _text(self.payload_media_type, "invalid_payload_metadata", maximum=256)
        if self.completion_state not in {"complete", "failed"}:
            raise ArtifactError("invalid_completion_state")


@dataclass(frozen=True, order=True)
class BackupFile:
    relative_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        _relative_path(self.relative_path)
        _integer(self.size_bytes, "invalid_artifact_size", minimum=1)
        _sha256(self.sha256)


@dataclass(frozen=True)
class BackupManifest:
    manifest_schema_version: int
    artifact_id: str
    created_at: str
    source_database_id: str
    source_storage_identity: str
    restore_size_bytes: int
    neo4j_edition: str
    neo4j_version: str
    migration_current_version: int
    migration_minimum_version: int
    migration_maximum_version: int
    backend_id: str
    backend_version: str
    consistency_mode: str
    payload_media_type: str
    payload_size_bytes: int
    files: tuple[BackupFile, ...]
    encryption: BackupEncryption
    completion_state: str

    def __post_init__(self) -> None:
        if (
            type(self.manifest_schema_version) is not int
            or self.manifest_schema_version != ARTIFACT_SCHEMA_VERSION
        ):
            raise ArtifactError("unsupported_artifact_schema")
        _identifier(self.artifact_id, "invalid_artifact_id")
        _identifier(self.source_database_id, "invalid_source_database_id")
        _token(self.source_storage_identity, "invalid_source_storage_identity")
        _integer(self.restore_size_bytes, "invalid_restore_size", minimum=1)
        if not isinstance(self.created_at, str):
            raise ArtifactError("invalid_artifact_created_at")
        try:
            created_at = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ArtifactError("invalid_artifact_created_at") from None
        if not self.created_at.endswith("Z") or created_at.utcoffset() != timezone.utc.utcoffset(
            created_at
        ):
            raise ArtifactError("invalid_artifact_created_at")
        metadata = BackupMetadata(
            artifact_id=self.artifact_id,
            created_at=created_at,
            source_database_id=self.source_database_id,
            source_storage_identity=self.source_storage_identity,
            restore_size_bytes=self.restore_size_bytes,
            neo4j_edition=self.neo4j_edition,
            neo4j_version=self.neo4j_version,
            migration_current_version=self.migration_current_version,
            migration_minimum_version=self.migration_minimum_version,
            migration_maximum_version=self.migration_maximum_version,
            backend_id=self.backend_id,
            backend_version=self.backend_version,
            consistency_mode=self.consistency_mode,
            payload_media_type=self.payload_media_type,
            encryption=self.encryption,
            completion_state=self.completion_state,
        )
        del metadata
        if not self.files:
            raise ArtifactError("incomplete_backup_artifact")
        paths = [entry.relative_path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ArtifactError("duplicate_artifact_path")
        if tuple(sorted(self.files)) != self.files:
            raise ArtifactError("noncanonical_backup_manifest")
        if self.payload_size_bytes != sum(entry.size_bytes for entry in self.files):
            raise ArtifactError("artifact_size_mismatch")

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "backend": {
                "consistency_mode": self.consistency_mode,
                "id": self.backend_id,
                "version": self.backend_version,
            },
            "completion_state": self.completion_state,
            "created_at": self.created_at,
            "encryption": asdict(self.encryption),
            "files": [asdict(entry) for entry in self.files],
            "manifest_schema_version": self.manifest_schema_version,
            "migration": {
                "current": self.migration_current_version,
                "maximum": self.migration_maximum_version,
                "minimum": self.migration_minimum_version,
            },
            "neo4j": {"edition": self.neo4j_edition, "version": self.neo4j_version},
            "payload": {
                "media_type": self.payload_media_type,
                "size_bytes": self.payload_size_bytes,
            },
            "source_database_id": self.source_database_id,
            "source_storage_identity": self.source_storage_identity,
            "restore_size_bytes": self.restore_size_bytes,
        }

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def from_dict(cls, value: object) -> BackupManifest:
        root = _exact_mapping(
            value,
            {
                "artifact_id",
                "backend",
                "completion_state",
                "created_at",
                "encryption",
                "files",
                "manifest_schema_version",
                "migration",
                "neo4j",
                "payload",
                "source_database_id",
                "source_storage_identity",
                "restore_size_bytes",
            },
        )
        backend = _exact_mapping(root["backend"], {"consistency_mode", "id", "version"})
        migration = _exact_mapping(root["migration"], {"current", "maximum", "minimum"})
        neo4j = _exact_mapping(root["neo4j"], {"edition", "version"})
        payload = _exact_mapping(root["payload"], {"media_type", "size_bytes"})
        encryption = _exact_mapping(
            root["encryption"], {"algorithm_profile", "encrypted", "key_reference"}
        )
        raw_files = root["files"]
        if not isinstance(raw_files, list):
            raise ArtifactError("malformed_backup_manifest")
        files = tuple(
            BackupFile(
                relative_path=_exact_mapping(item, {"relative_path", "sha256", "size_bytes"})[
                    "relative_path"
                ],
                size_bytes=item["size_bytes"],
                sha256=item["sha256"],
            )
            for item in raw_files
        )
        return cls(
            manifest_schema_version=root["manifest_schema_version"],
            artifact_id=root["artifact_id"],
            created_at=root["created_at"],
            source_database_id=root["source_database_id"],
            source_storage_identity=root["source_storage_identity"],
            restore_size_bytes=root["restore_size_bytes"],
            neo4j_edition=neo4j["edition"],
            neo4j_version=neo4j["version"],
            migration_current_version=migration["current"],
            migration_minimum_version=migration["minimum"],
            migration_maximum_version=migration["maximum"],
            backend_id=backend["id"],
            backend_version=backend["version"],
            consistency_mode=backend["consistency_mode"],
            payload_media_type=payload["media_type"],
            payload_size_bytes=payload["size_bytes"],
            files=files,
            encryption=BackupEncryption(
                encrypted=encryption["encrypted"],
                algorithm_profile=encryption["algorithm_profile"],
                key_reference=encryption["key_reference"],
            ),
            completion_state=root["completion_state"],
        )


@dataclass(frozen=True)
class VerifiedBackup:
    root: Path
    manifest: BackupManifest
    payload_paths: tuple[Path, ...]

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest.canonical_bytes()).hexdigest()


def _open_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    absolute = Path(path).absolute()
    parts = absolute.parts
    if not parts or parts[0] != absolute.anchor or len(parts) < 2:
        raise OSError(errno.EINVAL, "invalid anchored path")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    directory_fd = os.open(absolute.anchor, directory_flags)
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            parts[-1],
            flags | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def _reject_symlink_root(root: Path) -> None:
    descriptor = -1
    try:
        descriptor = _open_nofollow(
            Path(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ArtifactError("unsafe_artifact_symlink")
    except OSError:
        raise ArtifactError("unsafe_artifact_symlink") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _stream_regular_file(path: Path, *, capture_limit: int | None = None) -> tuple[int, str, bytes]:
    flags = os.O_RDONLY
    try:
        descriptor = _open_nofollow(path, flags)
    except OSError as exc:
        reason = (
            "unsafe_artifact_symlink"
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}
            else "artifact_file_missing"
        )
        raise ArtifactError(reason) from None
    captured = bytearray()
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactError("artifact_file_missing")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            if capture_limit is not None:
                if size > capture_limit:
                    raise ArtifactError("malformed_backup_manifest")
                captured.extend(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or after.st_size != size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ArtifactError("artifact_file_changed")
    finally:
        os.close(descriptor)
    return size, digest.hexdigest(), bytes(captured)


def create_manifest(
    root: Path, relative_paths: tuple[str, ...], metadata: BackupMetadata
) -> BackupManifest:
    _reject_symlink_root(root)
    normalized = tuple(_relative_path(path) for path in relative_paths)
    if len(normalized) != len(set(normalized)):
        raise ArtifactError("duplicate_artifact_path")
    files = []
    for relative_path in sorted(normalized):
        path = root / relative_path
        if any(component.is_symlink() for component in path.parents if component != root.parent):
            raise ArtifactError("unsafe_artifact_symlink")
        size, checksum, _ = _stream_regular_file(path)
        if size < 1:
            raise ArtifactError("invalid_artifact_size")
        files.append(
            BackupFile(
                relative_path=relative_path,
                size_bytes=size,
                sha256=checksum,
            )
        )
    created_at = metadata.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return BackupManifest(
        manifest_schema_version=ARTIFACT_SCHEMA_VERSION,
        artifact_id=metadata.artifact_id,
        created_at=created_at,
        source_database_id=metadata.source_database_id,
        source_storage_identity=metadata.source_storage_identity,
        restore_size_bytes=metadata.restore_size_bytes,
        neo4j_edition=metadata.neo4j_edition,
        neo4j_version=metadata.neo4j_version,
        migration_current_version=metadata.migration_current_version,
        migration_minimum_version=metadata.migration_minimum_version,
        migration_maximum_version=metadata.migration_maximum_version,
        backend_id=metadata.backend_id,
        backend_version=metadata.backend_version,
        consistency_mode=metadata.consistency_mode,
        payload_media_type=metadata.payload_media_type,
        payload_size_bytes=sum(entry.size_bytes for entry in files),
        files=tuple(files),
        encryption=metadata.encryption,
        completion_state=metadata.completion_state,
    )


def write_manifest(root: Path, manifest: BackupManifest) -> Path:
    path = root / ARTIFACT_MANIFEST_NAME
    descriptor = -1
    try:
        descriptor = _open_nofollow(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        view = memoryview(manifest.canonical_bytes())
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        raise ArtifactError("backup_manifest_write_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return path


def load_and_verify_artifact(root: Path) -> VerifiedBackup:
    root = Path(root)
    _reject_symlink_root(root)
    manifest_path = root / ARTIFACT_MANIFEST_NAME
    if manifest_path.is_symlink():
        raise ArtifactError("unsafe_artifact_symlink")
    try:
        _, _, raw = _stream_regular_file(manifest_path, capture_limit=1024 * 1024)
        parsed = json.loads(raw.decode("utf-8"))
        manifest = BackupManifest.from_dict(parsed)
    except ArtifactError:
        raise
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise ArtifactError("malformed_backup_manifest") from None
    if manifest.completion_state != "complete":
        raise ArtifactError("incomplete_backup_artifact")
    if manifest.encryption.encrypted:
        raise ArtifactError("unsupported_backup_encryption")
    if raw != manifest.canonical_bytes():
        raise ArtifactError("noncanonical_backup_manifest")

    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactError("unsafe_artifact_symlink")
        if path.is_file() and path != manifest_path:
            actual.add(path.relative_to(root).as_posix())
    expected = {entry.relative_path for entry in manifest.files}
    if expected - actual:
        raise ArtifactError("artifact_file_missing")
    if actual - expected:
        raise ArtifactError("artifact_file_extra")

    paths = []
    for entry in manifest.files:
        path = root / entry.relative_path
        size, checksum, _ = _stream_regular_file(path)
        if size != entry.size_bytes:
            raise ArtifactError("artifact_size_mismatch")
        if checksum != entry.sha256:
            raise ArtifactError("artifact_checksum_mismatch")
        paths.append(path)
    return VerifiedBackup(root=root, manifest=manifest, payload_paths=tuple(paths))


def stage_verified_payloads(verified: VerifiedBackup, destination_root: Path) -> tuple[Path, ...]:
    current = load_and_verify_artifact(verified.root)
    if current.manifest_sha256 != verified.manifest_sha256:
        raise ArtifactError("artifact_identity_changed")
    destination = Path(destination_root)
    if destination.is_symlink() or not destination.is_dir():
        raise ArtifactError("unsafe_staging_directory")
    staged: list[Path] = []
    for entry, source in zip(current.manifest.files, current.payload_paths, strict=True):
        target = destination / PurePosixPath(entry.relative_path)
        try:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise ArtifactError("artifact_staging_failed") from None
        source_flags = os.O_RDONLY
        target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        source_fd = target_fd = -1
        digest = hashlib.sha256()
        size = 0
        try:
            source_fd = _open_nofollow(source, source_flags)
            target_fd = _open_nofollow(target, target_flags, 0o400)
            source_before = os.fstat(source_fd)
            if not stat.S_ISREG(source_before.st_mode):
                raise ArtifactError("artifact_file_missing")
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    view = view[written:]
            os.fsync(target_fd)
            source_after = os.fstat(source_fd)
            if (
                (source_before.st_dev, source_before.st_ino)
                != (source_after.st_dev, source_after.st_ino)
                or source_before.st_mtime_ns != source_after.st_mtime_ns
                or size != entry.size_bytes
                or digest.hexdigest() != entry.sha256
            ):
                raise ArtifactError("artifact_file_changed")
        except OSError:
            raise ArtifactError("artifact_staging_failed") from None
        finally:
            if source_fd >= 0:
                os.close(source_fd)
            if target_fd >= 0:
                os.close(target_fd)
        staged.append(target)
    return tuple(staged)
