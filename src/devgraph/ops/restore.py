from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from devgraph.model.validation import validate_work_object_id
from devgraph.ops.backup import (
    ArtifactError,
    VerifiedBackup,
    load_and_verify_artifact,
    stage_verified_payloads,
)


def _safe_text(value: object, reason: str, *, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(reason)
    return value


def _safe_values(values: object, reason: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(reason)
    normalized = tuple(_safe_text(value, reason) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(reason)
    return normalized


@dataclass(frozen=True)
class RestoreCapability:
    backend_id: str
    backend_version: str
    consistency_modes: tuple[str, ...]
    payload_media_types: tuple[str, ...]
    database_editions: tuple[str, ...]
    runtime_database_version: str
    payload_count: int
    target_classifications: tuple[str, ...]

    def __post_init__(self) -> None:
        _safe_text(self.backend_id, "invalid_restore_capability")
        _safe_text(self.backend_version, "invalid_restore_capability")
        _safe_values(self.consistency_modes, "invalid_restore_capability")
        _safe_values(self.payload_media_types, "invalid_restore_capability")
        _safe_values(self.database_editions, "invalid_restore_capability")
        _safe_text(self.runtime_database_version, "invalid_restore_capability")
        _safe_values(self.target_classifications, "invalid_restore_capability")
        if type(self.payload_count) is not int or self.payload_count < 1:
            raise ValueError("invalid_restore_capability")


@dataclass(frozen=True)
class RestoreTarget:
    logical_id: str
    physical_identity: str
    classification: str
    reachable: bool
    empty: bool
    available_bytes: int

    def __post_init__(self) -> None:
        try:
            validate_work_object_id(self.logical_id)
        except (TypeError, ValueError):
            raise ValueError("invalid_restore_target") from None
        _safe_text(self.physical_identity, "invalid_restore_target")
        _safe_text(self.classification, "invalid_restore_target")
        if type(self.reachable) is not bool or type(self.empty) is not bool:
            raise ValueError("invalid_restore_target")
        if type(self.available_bytes) is not int or self.available_bytes < 0:
            raise ValueError("invalid_restore_target")


class RestoreBackend(Protocol):
    def inspect_capability(self) -> RestoreCapability: ...

    def inspect_target(self) -> RestoreTarget: ...

    def load(
        self,
        payloads: tuple[Path, ...],
        expected_target: RestoreTarget,
        required_restore_bytes: int,
    ) -> object: ...


def _stable_target(observed: RestoreTarget, expected: RestoreTarget) -> bool:
    return (
        observed.logical_id == expected.logical_id
        and observed.physical_identity == expected.physical_identity
        and observed.classification == expected.classification
        and observed.reachable is expected.reachable
        and observed.empty is expected.empty
    )


@dataclass(frozen=True)
class RestorePreflight:
    ready: bool
    reason: str
    artifact_id: str
    target_id: str
    backend_id: str
    payload_size_bytes: int
    required_restore_bytes: int
    manifest_sha256: str
    _verified: VerifiedBackup | None = field(default=None, repr=False, compare=False)
    _target: RestoreTarget | None = field(default=None, repr=False, compare=False)
    _capability: RestoreCapability | None = field(default=None, repr=False, compare=False)

    @property
    def confirmation_text(self) -> str:
        return f"RESTORE {self.artifact_id}@{self.manifest_sha256} TO {self.target_id}"

    def safe_output(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "artifact_id": self.artifact_id,
            "target_id": self.target_id,
            "backend_id": self.backend_id,
            "payload_size_bytes": self.payload_size_bytes,
            "required_restore_bytes": self.required_restore_bytes,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class RestoreAttempt:
    completed: bool
    ready: bool
    reason: str
    artifact_id: str
    target_id: str
    backend_id: str
    mutation_attempted: bool
    backend_operation: str | None = None
    backend_exit_code: int | None = None

    def safe_output(self) -> dict[str, object]:
        return {
            "completed": self.completed,
            "ready": self.ready,
            "reason": self.reason,
            "artifact_id": self.artifact_id,
            "target_id": self.target_id,
            "backend_id": self.backend_id,
            "mutation_attempted": self.mutation_attempted,
            "backend_operation": self.backend_operation,
            "backend_exit_code": self.backend_exit_code,
        }


@dataclass(frozen=True)
class PostRestoreChecks:
    artifact_integrity: bool
    storage_connectivity: bool
    migration_clean: bool
    fixture_match: bool
    constraints_match: bool
    readiness: bool

    def __post_init__(self) -> None:
        if not all(type(value) is bool for value in self.__dict__.values()):
            raise ValueError("invalid_post_restore_checks")


def _failed_preflight(
    reason: str,
    target: RestoreTarget,
    capability: RestoreCapability,
) -> RestorePreflight:
    return RestorePreflight(
        ready=False,
        reason=reason,
        artifact_id="unknown",
        target_id=target.logical_id,
        backend_id=capability.backend_id,
        payload_size_bytes=0,
        required_restore_bytes=0,
        manifest_sha256="unknown",
    )


def preflight_restore(
    artifact_root: Path,
    target: RestoreTarget,
    capability: RestoreCapability,
    *,
    supported_migration_minimum: int,
    supported_migration_maximum: int,
) -> RestorePreflight:
    """Perform the mandatory read-only, receiver-bound restore preflight."""

    try:
        verified = load_and_verify_artifact(artifact_root)
    except ArtifactError as exc:
        return _failed_preflight(exc.reason, target, capability)
    manifest = verified.manifest
    base = {
        "artifact_id": manifest.artifact_id,
        "target_id": target.logical_id,
        "backend_id": manifest.backend_id,
        "payload_size_bytes": manifest.payload_size_bytes,
        "required_restore_bytes": manifest.restore_size_bytes,
        "manifest_sha256": verified.manifest_sha256,
    }

    def fail(reason: str) -> RestorePreflight:
        return RestorePreflight(ready=False, reason=reason, **base)

    if manifest.backend_id != capability.backend_id:
        return fail("wrong_restore_backend")
    if manifest.backend_version != capability.backend_version:
        return fail("incompatible_backend_version")
    if manifest.consistency_mode not in capability.consistency_modes:
        return fail("unsupported_backup_consistency")
    if (
        manifest.payload_media_type not in capability.payload_media_types
        or len(verified.payload_paths) != capability.payload_count
    ):
        return fail("unsupported_backup_payload")
    if manifest.source_storage_identity == target.physical_identity:
        return fail("wrong_restore_target")
    if target.classification not in capability.target_classifications:
        return fail("target_classification_unsupported")
    if not target.reachable:
        return fail("target_unreachable")
    if not target.empty:
        return fail("target_not_empty")
    if manifest.neo4j_edition not in capability.database_editions:
        return fail("incompatible_database_edition")
    if manifest.neo4j_version != capability.runtime_database_version:
        return fail("incompatible_database_version")
    if (
        type(supported_migration_minimum) is not int
        or type(supported_migration_maximum) is not int
        or supported_migration_minimum < 1
        or supported_migration_maximum < supported_migration_minimum
        or manifest.migration_minimum_version != supported_migration_minimum
        or manifest.migration_maximum_version != supported_migration_maximum
        or not supported_migration_minimum
        <= manifest.migration_current_version
        <= supported_migration_maximum
    ):
        return fail("incompatible_migration_version")
    if target.available_bytes < manifest.restore_size_bytes:
        return fail("insufficient_restore_capacity")
    return RestorePreflight(
        ready=True,
        reason="restore_preflight_clean",
        _verified=verified,
        _target=target,
        _capability=capability,
        **base,
    )


def _attempt(
    plan: RestorePreflight,
    reason: str,
    *,
    completed: bool = False,
    mutation_attempted: bool = False,
    backend_operation: str | None = None,
    backend_exit_code: int | None = None,
) -> RestoreAttempt:
    return RestoreAttempt(
        completed=completed,
        ready=False,
        reason=reason,
        artifact_id=plan.artifact_id,
        target_id=plan.target_id,
        backend_id=plan.backend_id,
        mutation_attempted=mutation_attempted,
        backend_operation=backend_operation,
        backend_exit_code=backend_exit_code,
    )


def execute_restore(
    plan: RestorePreflight,
    backend: RestoreBackend,
    *,
    confirmation: str,
    synthetic_confirmation: bool,
) -> RestoreAttempt:
    if not plan.ready or plan._verified is None or plan._target is None or plan._capability is None:
        return _attempt(plan, plan.reason)
    if confirmation != plan.confirmation_text:
        return _attempt(plan, "restore_confirmation_required")
    if synthetic_confirmation is not True:
        return _attempt(plan, "synthetic_restore_authority_required")
    try:
        if backend.inspect_capability() != plan._capability:
            return _attempt(plan, "restore_capability_changed")
        observed_target = backend.inspect_target()
        if (
            not _stable_target(observed_target, plan._target)
            or observed_target.available_bytes < plan.required_restore_bytes
        ):
            return _attempt(plan, "restore_target_changed")
    except Exception:
        return _attempt(plan, "restore_target_revalidation_failed")
    try:
        verified = load_and_verify_artifact(plan._verified.root)
    except ArtifactError as exc:
        return _attempt(plan, exc.reason)
    if verified.manifest_sha256 != plan.manifest_sha256:
        return _attempt(plan, "artifact_identity_changed")
    try:
        with tempfile.TemporaryDirectory(prefix="devgraph-restore-") as temporary:
            staging_root = Path(temporary).resolve(strict=True)
            os.chmod(staging_root, 0o700)
            staged = stage_verified_payloads(verified, staging_root)
            if len(staged) != plan._capability.payload_count:
                return _attempt(plan, "artifact_identity_changed")
            observed_target = backend.inspect_target()
            if (
                not _stable_target(observed_target, plan._target)
                or observed_target.available_bytes < plan.required_restore_bytes
            ):
                return _attempt(plan, "restore_target_changed")
            evidence = backend.load(staged, plan._target, plan.required_restore_bytes)
    except ArtifactError as exc:
        return _attempt(plan, exc.reason)
    except Exception:
        return _attempt(plan, "restore_backend_failed", mutation_attempted=True)
    operation = getattr(evidence, "operation", None)
    exit_code = getattr(evidence, "exit_code", None)
    if operation != "load" or type(exit_code) is not int or exit_code != 0:
        return _attempt(plan, "restore_backend_failed", mutation_attempted=True)
    return _attempt(
        plan,
        "post_restore_verification_required",
        completed=True,
        mutation_attempted=True,
        backend_operation=operation,
        backend_exit_code=exit_code,
    )


def complete_post_restore(attempt: RestoreAttempt, checks: PostRestoreChecks) -> RestoreAttempt:
    if not attempt.completed or attempt.reason != "post_restore_verification_required":
        return attempt
    for name in PostRestoreChecks.__dataclass_fields__:
        if not getattr(checks, name):
            return RestoreAttempt(
                completed=True,
                ready=False,
                reason=f"post_restore_{name}_failed",
                artifact_id=attempt.artifact_id,
                target_id=attempt.target_id,
                backend_id=attempt.backend_id,
                mutation_attempted=attempt.mutation_attempted,
                backend_operation=attempt.backend_operation,
                backend_exit_code=attempt.backend_exit_code,
            )
    return RestoreAttempt(
        completed=True,
        ready=True,
        reason="restore_verified",
        artifact_id=attempt.artifact_id,
        target_id=attempt.target_id,
        backend_id=attempt.backend_id,
        mutation_attempted=attempt.mutation_attempted,
        backend_operation=attempt.backend_operation,
        backend_exit_code=attempt.backend_exit_code,
    )
