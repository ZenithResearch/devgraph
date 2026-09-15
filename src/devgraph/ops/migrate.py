from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from devgraph.storage.base import (
    GraphStorage,
    MigrationJournal,
    MigrationOwner,
    MigrationStore,
)

ROOT = Path(__file__).resolve().parents[3]
MIRROR_HEADER = (
    "// GENERATED; NOT EXECUTED. Byte mirror of migrations/manifest.json payloads.\n"
    "// Hand editing is forbidden; regenerate from ordered migration payloads.\n\n"
)
BOOTSTRAP_CONSTRAINT_NAME = "devgraph_migration_version_unique"
BOOTSTRAP_DDL = (
    "CREATE CONSTRAINT devgraph_migration_version_unique IF NOT EXISTS "
    "FOR (m:DevgraphMigration) REQUIRE m.version IS UNIQUE"
)
BOOTSTRAP_DEFINITION = (
    BOOTSTRAP_CONSTRAINT_NAME,
    "UNIQUENESS",
    "DevgraphMigration",
    ("version",),
    BOOTSTRAP_DDL,
)
RUNNER_SCHEMA_VERSION = 1


class ManifestError(ValueError):
    """Raised before mutation when committed migration bytes are invalid."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    payload_path: str
    checksum: str
    posture: str
    kind: str
    payload: bytes


@dataclass(frozen=True)
class Manifest:
    schema_version: int
    migrations: tuple[Migration, ...]


@dataclass(frozen=True)
class MigrationStatus:
    ready: bool
    reason: str
    manifest_schema_version: int
    minimum_schema_version: int
    maximum_schema_version: int
    current_applied_version: int
    applied: tuple[tuple[int, str], ...] = ()

    @classmethod
    def clean(cls, current: int, manifest: Manifest) -> MigrationStatus:
        return cls(
            True,
            "clean",
            manifest.schema_version,
            1,
            len(manifest.migrations),
            current,
            tuple((item.version, item.checksum[:12]) for item in manifest.migrations[:current]),
        )

    @classmethod
    def failed(cls, reason: str, manifest: Manifest, current: int = 0) -> MigrationStatus:
        return cls(False, reason, manifest.schema_version, 1, len(manifest.migrations), current)

    @classmethod
    def unconfigured(cls, reason: str) -> MigrationStatus:
        return cls(False, reason, 0, 0, 0, 0)

    def safe_output(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "manifest_schema_version": self.manifest_schema_version,
            "minimum_schema_version": self.minimum_schema_version,
            "maximum_schema_version": self.maximum_schema_version,
            "current_applied_version": self.current_applied_version,
            "applied": [
                {"version": version, "checksum_prefix": checksum}
                for version, checksum in self.applied
            ],
        }


def load_manifest(path: Path, *, payload_override: Mapping[int, Path] | None = None) -> Manifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("invalid_manifest") from exc
    if not isinstance(raw, dict):
        raise ManifestError("invalid_manifest")
    if raw.get("schema_version") != 1:
        raise ManifestError("unsupported_manifest_schema_version")
    items = raw.get("migrations")
    if not isinstance(items, list):
        raise ManifestError("invalid_migration_list")
    migrations: list[Migration] = []
    seen_names: set[str] = set()
    root = path.parent.parent
    for expected_version, item in enumerate(items, 1):
        if not isinstance(item, dict) or item.get("version") != expected_version:
            raise ManifestError("invalid_migration_order")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen_names:
            raise ManifestError("duplicate_or_invalid_migration_name")
        if item.get("posture") != "forward_only":
            raise ManifestError("unsupported_migration_posture")
        kind = item.get("kind")
        if kind not in {"schema_ddl", "transactional_data"}:
            raise ManifestError("unsupported_migration_kind")
        relative = item.get("payload_path")
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ManifestError("invalid_payload_path")
        expected_relative = f"migrations/{expected_version:04d}_{name}.cypher"
        if relative != expected_relative:
            raise ManifestError("invalid_payload_filename")
        payload_path = (payload_override or {}).get(expected_version, root / relative)
        try:
            payload = payload_path.read_bytes()
            statement = payload.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ManifestError("missing_or_invalid_payload") from exc
        if hashlib.sha256(payload).hexdigest() != item.get("checksum"):
            raise ManifestError("checksum_mismatch")
        if kind == "schema_ddl":
            if statement.count(";") != 1 or not statement.rstrip().endswith(";"):
                raise ManifestError("payload_must_contain_one_statement")
            match = re.fullmatch(
                r"CREATE CONSTRAINT ([a-z0-9_]+) IF NOT EXISTS "
                r"FOR \(([A-Za-z][A-Za-z0-9_]*):([A-Za-z][A-Za-z0-9]*)\) "
                r"REQUIRE \2\.([a-z][a-z0-9_]*) IS UNIQUE;\n?",
                statement,
            )
            expected_definition = (
                (
                    "event_receipt_idempotency_claim_digest",
                    "EventReceipt",
                    "idempotency_claim_digest",
                )
                if expected_version == 24
                else (
                    name,
                    "".join(
                        part.title() for part in name.removesuffix("_id").split("_")
                    ),
                    "id",
                )
            )
            if match is None or (
                match.group(1),
                match.group(3),
                match.group(4),
            ) != expected_definition:
                raise ManifestError("payload_definition_mismatch")
        elif (
            expected_version != 23
            or name != "canonical_work_object_persistence_v1"
            or statement.count(";") != 1
            or "canonical_work_object_persistence_v1" not in statement
        ):
            raise ManifestError("unsupported_transactional_migration")
        migrations.append(
            Migration(
                expected_version,
                name,
                relative,
                item["checksum"],
                "forward_only",
                kind,
                payload,
            )
        )
        seen_names.add(name)
    if not migrations:
        raise ManifestError("empty_manifest")
    kinds = [item.kind for item in migrations]
    if (
        len(kinds) < 23
        or kinds[:23] != ["schema_ddl"] * 22 + ["transactional_data"]
        or any(kind != "schema_ddl" for kind in kinds[23:])
    ):
        raise ManifestError("invalid_migration_kind_order")
    referenced = {Path(item.payload_path).name for item in migrations}
    actual = {item.name for item in path.parent.glob("*.cypher")}
    if referenced != actual:
        raise ManifestError("payload_set_mismatch")
    return Manifest(1, tuple(migrations))


def render_constraint_mirror(manifest: Manifest) -> str:
    # Operational mutexes are private storage metadata, not ontology classes.
    # Preserve the published ontology bundle when adding this internal guard.
    return MIRROR_HEADER + b"".join(
        item.payload for item in manifest.migrations
        if item.kind == "schema_ddl" and item.name != "work_mutation_guard_id"
    ).decode()


def normalize_definition(value: str) -> str:
    normalized = " ".join(value.rstrip(";\n").replace("`", "").split())
    match = re.fullmatch(
        r"CREATE CONSTRAINT (\S+)(?: IF NOT EXISTS)? FOR \(\w+:(\w+)\) "
        r"REQUIRE \(?\w+\.(\w+)\)? IS UNIQUE",
        normalized,
    )
    if match is None:
        return normalized
    name, label, property_name = match.groups()
    return (
        f"CREATE CONSTRAINT {name} IF NOT EXISTS FOR (m:{label}) "
        f"REQUIRE m.{property_name} IS UNIQUE"
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _failed(reason: str, manifest: Manifest, current: int = 0) -> MigrationStatus:
    return MigrationStatus.failed(reason, manifest, current)


def _bootstrap_reason(value: object) -> str | None:
    if value is None:
        return "operator_hold_bootstrap_inspection"
    if not isinstance(value, tuple) or len(value) != 5:
        return "operator_hold_bootstrap_ambiguous"
    checks = (
        (value[0] == BOOTSTRAP_CONSTRAINT_NAME, "operator_hold_bootstrap_name"),
        (value[1] == "UNIQUENESS", "operator_hold_bootstrap_type"),
        (value[2] == "DevgraphMigration", "operator_hold_bootstrap_label"),
        (value[3] == ("version",), "operator_hold_bootstrap_property"),
        (
            normalize_definition(str(value[4])) == BOOTSTRAP_DDL,
            "operator_hold_bootstrap_definition",
        ),
    )
    return next((reason for valid, reason in checks if not valid), None)


def _valid_timestamp(value: str | None, *, required: bool) -> bool:
    if value is None:
        return not required
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _journal_reason(record: MigrationJournal, migration: Migration) -> str | None:
    if record.runner_schema_version != RUNNER_SCHEMA_VERSION:
        return "operator_hold_runner_schema_version"
    if record.state not in {
        "pending", "ddl_started", "ddl_observed", "applied", "recoverable_ddl_not_applied"
    }:
        return "operator_hold_journal_state"
    if not isinstance(record.owner_attempt_id, str) or not record.owner_attempt_id:
        return "operator_hold_journal_owner"
    if not _valid_timestamp(record.started_at, required=True) or not _valid_timestamp(
        record.completed_at, required=record.state == "applied"
    ):
        return "operator_hold_journal_timestamp"
    if record.state != "applied" and record.completed_at is not None:
        return "operator_hold_journal_timestamp"
    if record.name != migration.name or record.checksum != migration.checksum:
        return "checksum_mismatch"
    if migration.kind == "transactional_data":
        expected_definition = f"transactional_data:{migration.name}:{migration.checksum}"
        if (
            record.schema_object_name != migration.name
            or record.schema_object_type != "TRANSACTIONAL_DATA"
            or record.schema_object_definition != expected_definition
        ):
            return "schema_definition_mismatch"
        return None
    expected = normalize_definition(migration.payload.decode())
    if (
        record.schema_object_name != migration.name
        or record.schema_object_type != "UNIQUENESS"
        or record.schema_object_definition != expected
    ):
        return "schema_definition_mismatch"
    return None


def _object_reason(observed: object | bool | None, expected: str) -> str | None:
    if observed is None:
        return "operator_hold_schema_inspection_unknown"
    if observed is False:
        return "operator_hold_schema_object_absent"
    if not isinstance(observed, tuple) or len(observed) != 2:
        return "operator_hold_schema_inspection_ambiguous"
    if observed[0] != "UNIQUENESS" or normalize_definition(str(observed[1])) != expected:
        return "schema_definition_mismatch"
    return None


def migration_status(manifest: Manifest, store: MigrationStore) -> MigrationStatus:
    try:
        bootstrap = store.inspect_bootstrap_constraint()
    except Exception:
        return _failed("operator_hold_bootstrap_inspection", manifest)
    reason = _bootstrap_reason(bootstrap)
    if reason:
        return _failed(reason, manifest)
    try:
        owner_state = store.inspect_owner()
    except Exception:
        return _failed("operator_hold_owner_inspection", manifest)
    if owner_state is None:
        return _failed("operator_hold_owner_missing", manifest)
    if not isinstance(owner_state, MigrationOwner):
        return _failed("operator_hold_owner_malformed", manifest)
    if owner_state.runner_schema_version != RUNNER_SCHEMA_VERSION:
        return _failed("operator_hold_runner_schema_version", manifest)
    if owner_state.state == "owned":
        return _failed("migration_lock_busy", manifest)
    if owner_state.state != "clean" or owner_state.owner_attempt_id is not None:
        return _failed("operator_hold_owner_posture", manifest)
    try:
        raw_records = store.inspect_journal()
    except Exception:
        return _failed("operator_hold_journal_inspection", manifest)
    if not all(isinstance(item, MigrationJournal) for item in raw_records):
        return _failed("operator_hold_journal_malformed", manifest)
    records = cast(tuple[MigrationJournal, ...], raw_records)
    by_version = {item.version: item for item in records}
    if len(by_version) != len(records):
        return _failed("operator_hold_journal_ambiguous", manifest)
    if any(version < 1 or version > len(manifest.migrations) for version in by_version):
        return _failed("operator_hold_journal_version_out_of_range", manifest)
    applied = 0
    for migration in manifest.migrations:
        record = by_version.get(migration.version)
        if record is None:
            break
        mismatch = _journal_reason(record, migration)
        if mismatch:
            return _failed(mismatch, manifest, applied)
        if record.state != "applied":
            reason = (
                "recoverable_ddl_not_applied"
                if record.state == "recoverable_ddl_not_applied"
                else "operator_hold_journal_state"
            )
            return _failed(reason, manifest, applied)
        if migration.kind == "schema_ddl":
            try:
                observed = store.inspect_schema_object(migration.name)
            except Exception:
                return _failed("operator_hold_schema_inspection", manifest, applied)
            mismatch = _object_reason(observed, record.schema_object_definition)
            if mismatch:
                return _failed(mismatch, manifest, applied)
        applied += 1
    if applied != len(manifest.migrations):
        return _failed("unapplied_migrations", manifest, applied)
    return MigrationStatus.clean(applied, manifest)


def _canonical_persistence_reason(storage: GraphStorage) -> str | None:
    try:
        health = storage.health()
    except Exception:
        return "canonical_storage_unavailable"
    if not health.ready:
        return "canonical_storage_unavailable"
    try:
        storage.inspect_canonical_persistence()
    except Exception:
        try:
            if not storage.health().ready:
                return "canonical_storage_unavailable"
        except Exception:
            return "canonical_storage_unavailable"
        return "canonical_persistence_invalid"
    return None


def readiness_status(
    manifest: Manifest | None,
    store: MigrationStore | None,
    storage: GraphStorage,
) -> MigrationStatus:
    """Compose storage, migration, and canonical-data readiness without mutation."""

    if manifest is None or store is None:
        return MigrationStatus.unconfigured("migration_configuration_absent")
    canonical_reason = _canonical_persistence_reason(storage)
    if canonical_reason == "canonical_storage_unavailable":
        return MigrationStatus.failed(canonical_reason, manifest)
    status = migration_status(manifest, store)
    if not status.ready:
        return status
    if canonical_reason is not None:
        return MigrationStatus.failed(
            canonical_reason, manifest, current=status.current_applied_version
        )
    return status


def _read_journal(
    manifest: Manifest, store: MigrationStore
) -> tuple[dict[int, MigrationJournal] | None, str | None]:
    try:
        records = store.inspect_journal()
    except Exception:
        return None, "operator_hold_journal_inspection"
    if not all(isinstance(item, MigrationJournal) for item in records):
        return None, "operator_hold_journal_malformed"
    by_version = {item.version: item for item in records}
    if len(by_version) != len(records):
        return None, "operator_hold_journal_ambiguous"
    if any(version < 1 or version > len(manifest.migrations) for version in by_version):
        return None, "operator_hold_journal_version_out_of_range"
    for migration in manifest.migrations:
        record = by_version.get(migration.version)
        if record is not None:
            reason = _journal_reason(record, migration)
            if reason:
                return None, reason
    return by_version, None


def apply_migrations(
    manifest: Manifest,
    store: MigrationStore,
    *,
    attempt_id: str,
    recovery_owner_attempt_id: str | None = None,
) -> MigrationStatus:
    try:
        bootstrap = store.inspect_bootstrap_constraint()
        if bootstrap is None:
            store.execute_bootstrap(BOOTSTRAP_DDL)
            bootstrap = store.inspect_bootstrap_constraint()
    except Exception:
        return _failed("operator_hold_bootstrap_command_failed", manifest)
    reason = _bootstrap_reason(bootstrap)
    if reason:
        return _failed(reason, manifest)

    try:
        owner_state = store.inspect_owner()
    except Exception:
        return _failed("operator_hold_owner_inspection", manifest)
    if owner_state is not None and not isinstance(owner_state, MigrationOwner):
        return _failed("operator_hold_owner_malformed", manifest)

    by_version, reason = _read_journal(manifest, store)
    if reason or by_version is None:
        return _failed(reason or "operator_hold_journal_inspection", manifest)

    current_owner = None if owner_state is None else owner_state.owner_attempt_id
    if current_owner is None and any(
        record.state != "applied"
        and record.state != "recoverable_ddl_not_applied"
        and record.owner_attempt_id != attempt_id
        for record in by_version.values()
    ):
        return _failed("operator_hold_owner_mismatch", manifest)
    if current_owner not in (None, attempt_id):
        if recovery_owner_attempt_id != current_owner:
            return _failed("migration_lock_busy", manifest)
        recoverable = [
            record
            for record in by_version.values()
            if record.state == "ddl_started" and record.owner_attempt_id == current_owner
        ]
        if len(recoverable) == 1 and all(
            record.state in {"applied", "ddl_started"}
            for record in by_version.values()
        ):
            recovery_journal: MigrationJournal | None = recoverable[0]
        elif not recoverable and all(
            record.state in {"applied", "recoverable_ddl_not_applied"}
            for record in by_version.values()
        ):
            recovery_journal = None
        else:
            return _failed("operator_hold_recovery_posture", manifest)
        if not store.recover_owner(current_owner, attempt_id, recovery_journal):
            return _failed("migration_lock_busy", manifest)
    elif current_owner is None:
        if not store.acquire_owner(attempt_id):
            return _failed("migration_lock_busy", manifest)

    # The pre-acquisition snapshot is never authoritative after ownership changes.
    try:
        owner_state = store.inspect_owner()
    except Exception:
        return _failed("operator_hold_owner_inspection", manifest)
    if (
        not isinstance(owner_state, MigrationOwner)
        or owner_state.state != "owned"
        or owner_state.owner_attempt_id != attempt_id
        or owner_state.runner_schema_version != RUNNER_SCHEMA_VERSION
    ):
        return _failed("operator_hold_owner_posture", manifest)
    by_version, reason = _read_journal(manifest, store)
    if reason or by_version is None:
        return _failed(reason or "operator_hold_journal_inspection", manifest)

    try:
        for migration in manifest.migrations:
            record = by_version.get(migration.version)
            if record is not None and record.state == "applied":
                if migration.kind == "schema_ddl":
                    mismatch = _object_reason(
                        store.inspect_schema_object(migration.name),
                        record.schema_object_definition,
                    )
                    if mismatch:
                        return _failed(mismatch, manifest, migration.version - 1)
                continue

            if migration.kind == "transactional_data":
                if record is not None:
                    return _failed("operator_hold_journal_state", manifest, migration.version - 1)
                completed_at = _now()
                if not store.apply_transactional_data(migration, attempt_id, completed_at):
                    return _failed(
                        "operator_hold_transactional_migration", manifest, migration.version - 1
                    )
                refreshed, refresh_reason = _read_journal(manifest, store)
                if refresh_reason or refreshed is None:
                    return _failed(
                        refresh_reason or "operator_hold_journal_inspection",
                        manifest,
                        migration.version - 1,
                    )
                applied_record = refreshed.get(migration.version)
                if applied_record is None or applied_record.state != "applied":
                    return _failed(
                        "operator_hold_transactional_migration", manifest, migration.version - 1
                    )
                by_version = refreshed
                continue

            preexisting_started = record is not None and record.state == "ddl_started"
            if record is not None and record.owner_attempt_id != attempt_id:
                if record.state != "recoverable_ddl_not_applied":
                    return _failed("operator_hold_owner_mismatch", manifest, migration.version - 1)
                adopted = replace(
                    record,
                    state="pending",
                    owner_attempt_id=attempt_id,
                    started_at=_now(),
                    completed_at=None,
                )
                if not store.compare_and_set_journal(record, adopted, attempt_id):
                    return _failed("operator_hold_journal_transition", manifest)
                record = adopted
                by_version[migration.version] = record

            if record is None:
                record = MigrationJournal.pending(migration, attempt_id, _now())
                if not store.compare_and_set_journal(None, record, attempt_id):
                    return _failed("operator_hold_journal_transition", manifest)
                by_version[migration.version] = record

            if record.state == "recoverable_ddl_not_applied":
                pending = replace(
                    record, state="pending", owner_attempt_id=attempt_id, started_at=_now()
                )
                if not store.compare_and_set_journal(record, pending, attempt_id):
                    return _failed("operator_hold_journal_transition", manifest)
                record = pending
                by_version[migration.version] = record

            if record.state == "pending":
                started = replace(record, state="ddl_started")
                if not store.compare_and_set_journal(record, started, attempt_id):
                    return _failed("operator_hold_journal_transition", manifest)
                record = started
                by_version[migration.version] = record
                preexisting_started = False

            if record.state == "ddl_observed":
                observed = store.inspect_schema_object(migration.name)
                mismatch = _object_reason(observed, record.schema_object_definition)
                if mismatch:
                    return _failed(mismatch, manifest, migration.version - 1)
                applied = replace(record, state="applied", completed_at=_now())
                if not store.compare_and_set_journal(record, applied, attempt_id):
                    return _failed("operator_hold_journal_transition", manifest)
                by_version[migration.version] = applied
                continue

            if record.state != "ddl_started":
                return _failed("operator_hold_journal_state", manifest)

            if preexisting_started:
                observed = store.inspect_schema_object(migration.name)
                if observed is False:
                    recoverable = replace(record, state="recoverable_ddl_not_applied")
                    if not store.compare_and_set_journal(record, recoverable, attempt_id):
                        return _failed("operator_hold_journal_transition", manifest)
                    if not store.release_owner(attempt_id):
                        return _failed("operator_hold_owner_release", manifest)
                    return _failed("recoverable_ddl_not_applied", manifest)
                mismatch = _object_reason(observed, record.schema_object_definition)
                if mismatch:
                    return _failed(mismatch, manifest)
            else:
                try:
                    store.execute_ddl(migration.payload)
                except Exception:
                    observed = store.inspect_schema_object(migration.name)
                    if observed is None:
                        return _failed("operator_hold_schema_inspection_unknown", manifest)
                    if observed is not False:
                        return _failed("operator_hold_ddl_ambiguous", manifest)
                    recoverable = replace(record, state="recoverable_ddl_not_applied")
                    if not store.compare_and_set_journal(record, recoverable, attempt_id):
                        return _failed("operator_hold_journal_transition", manifest)
                    if not store.release_owner(attempt_id):
                        return _failed("operator_hold_owner_release", manifest)
                    return _failed("recoverable_ddl_not_applied", manifest)
                mismatch = _object_reason(
                    store.inspect_schema_object(migration.name), record.schema_object_definition
                )
                if mismatch:
                    return _failed(mismatch, manifest)

            observed_record = replace(record, state="ddl_observed")
            if not store.compare_and_set_journal(record, observed_record, attempt_id):
                return _failed("operator_hold_journal_transition", manifest)
            applied = replace(observed_record, state="applied", completed_at=_now())
            if not store.compare_and_set_journal(observed_record, applied, attempt_id):
                return _failed("operator_hold_journal_transition", manifest)
            by_version[migration.version] = applied

        result = MigrationStatus.clean(len(manifest.migrations), manifest)
        if not store.release_owner(attempt_id):
            return _failed("operator_hold_owner_release", manifest)
        return result
    except Exception:
        return _failed("operator_hold_unexpected_store_failure", manifest)
