from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_migrations import MemoryMigrationStore

from devgraph.ops.migrate import (
    BOOTSTRAP_DDL,
    MigrationJournal,
    apply_migrations,
    load_manifest,
    migration_status,
)
from devgraph.storage.base import MigrationOwner

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("bootstrap", "reason"),
    [
        (
            ("wrong", "UNIQUENESS", "DevgraphMigration", ("version",), BOOTSTRAP_DDL),
            "operator_hold_bootstrap_name",
        ),
        (
            (
                "devgraph_migration_version_unique",
                "RANGE",
                "DevgraphMigration",
                ("version",),
                BOOTSTRAP_DDL,
            ),
            "operator_hold_bootstrap_type",
        ),
        (
            (
                "devgraph_migration_version_unique",
                "UNIQUENESS",
                "Wrong",
                ("version",),
                BOOTSTRAP_DDL,
            ),
            "operator_hold_bootstrap_label",
        ),
        (
            (
                "devgraph_migration_version_unique",
                "UNIQUENESS",
                "DevgraphMigration",
                ("other",),
                BOOTSTRAP_DDL,
            ),
            "operator_hold_bootstrap_property",
        ),
    ],
)
def test_mismatched_bootstrap_holds_without_mutation(bootstrap, reason: str) -> None:
    store = MemoryMigrationStore()
    store.bootstrap = bootstrap
    result = apply_migrations(
        load_manifest(ROOT / "migrations/manifest.json"), store, attempt_id="a"
    )
    assert result.reason == reason
    assert store.mutations == 0


def test_bootstrap_command_failure_holds_without_journal_mutation() -> None:
    store = MemoryMigrationStore()
    store.execute_bootstrap = lambda statement: (_ for _ in ()).throw(RuntimeError("failed"))

    result = apply_migrations(
        load_manifest(ROOT / "migrations/manifest.json"), store, attempt_id="a"
    )

    assert result.reason == "operator_hold_bootstrap_command_failed"
    assert store.journal == {}
    assert store.mutations == 0


def test_ambiguous_bootstrap_inspection_holds_without_mutation() -> None:
    store = MemoryMigrationStore()
    store.bootstrap = ("ambiguous",)

    result = apply_migrations(
        load_manifest(ROOT / "migrations/manifest.json"), store, attempt_id="a"
    )

    assert result.reason == "operator_hold_bootstrap_ambiguous"
    assert store.mutations == 0


def test_bootstrap_success_then_acquisition_loss_has_zero_application_mutation() -> None:
    store = MemoryMigrationStore()
    store.acquire_owner = lambda attempt_id: False

    result = apply_migrations(
        load_manifest(ROOT / "migrations/manifest.json"), store, attempt_id="loser"
    )

    assert result.reason == "migration_lock_busy"
    assert store.journal == {}
    assert store.ddl_calls == []
    assert store.mutations == 0


def test_unknown_schema_inspection_is_operator_hold() -> None:
    store = MemoryMigrationStore()
    store.inspection_unknown = True
    store.fail_ddl = True
    result = apply_migrations(
        load_manifest(ROOT / "migrations/manifest.json"), store, attempt_id="a"
    )
    assert result.reason == "operator_hold_schema_inspection_unknown"


def test_definition_mismatch_is_operator_hold() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    apply_migrations(manifest, store, attempt_id="a")
    store.objects[manifest.migrations[0].name] = ("UNIQUENESS", "different")
    result = apply_migrations(manifest, store, attempt_id="b")
    assert result.reason == "schema_definition_mismatch"


def test_wrong_owner_never_advances_journal() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = "other"
    store.journal[1] = replace(
        MigrationJournal.started(manifest.migrations[0], "other"),
        owner_attempt_id="other",
    )
    result = apply_migrations(manifest, store, attempt_id="a")
    assert result.reason == "migration_lock_busy"
    assert store.journal[1].state == "ddl_started"


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("owner_attempt_id", "wrong-owner"),
        ("name", "wrong-name"),
        ("checksum", "0" * 64),
        ("schema_object_name", "wrong-object"),
        ("schema_object_type", "RANGE"),
        ("schema_object_definition", "wrong-definition"),
        ("started_at", "wrong-started-at"),
        ("completed_at", "wrong-completed-at"),
        ("runner_schema_version", 2),
    ],
)
def test_same_state_wrong_identity_never_mutates(field: str, wrong_value: object) -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = "attempt-a"
    expected = MigrationJournal.started(manifest.migrations[0], "attempt-a")
    store.journal[1] = replace(expected, **{field: wrong_value})
    before = store.mutations

    changed = store.compare_and_set_journal(
        expected, replace(expected, state="ddl_observed"), "attempt-a"
    )

    assert changed is False
    assert store.journal[1].state == "ddl_started"
    assert store.mutations == before


def test_abandoned_owner_is_operator_hold_and_is_never_stolen() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    store.execute_bootstrap(BOOTSTRAP_DDL)
    store.owner = None
    store.journal[1] = MigrationJournal.started(manifest.migrations[0], "abandoned")
    before = store.mutations

    result = apply_migrations(manifest, store, attempt_id="recovery")

    assert result.reason == "operator_hold_owner_mismatch"
    assert store.owner is None
    assert store.journal[1].state == "ddl_started"
    assert store.mutations == before


def test_cli_reports_stable_safe_reason_without_configuration() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/devgraph_migrate.py", "status", "--safe-output"],
        cwd=ROOT,
        env={},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "applied": [],
        "current_applied_version": 0,
        "manifest_schema_version": 1,
        "maximum_schema_version": 26,
        "minimum_schema_version": 1,
        "ready": False,
        "reason": "operator_hold_storage_unavailable",
    }


def test_cli_reports_invalid_manifest_without_traceback(tmp_path: Path) -> None:
    invalid_manifest = tmp_path / "manifest.json"
    invalid_manifest.write_text("not-json", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/devgraph_migrate.py",
            "status",
            "--safe-output",
            "--manifest",
            str(invalid_manifest),
        ],
        cwd=ROOT,
        env={},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "applied": [],
        "current_applied_version": 0,
        "manifest_schema_version": 0,
        "maximum_schema_version": 0,
        "minimum_schema_version": 0,
        "ready": False,
        "reason": "invalid_manifest",
    }


def test_cli_reports_non_mapping_manifest_without_traceback(tmp_path: Path) -> None:
    invalid_manifest = tmp_path / "manifest.json"
    invalid_manifest.write_text("[]", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/devgraph_migrate.py",
            "status",
            "--safe-output",
            "--manifest",
            str(invalid_manifest),
        ],
        cwd=ROOT,
        env={},
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["reason"] == "invalid_manifest"


def test_status_rejects_active_owner_even_with_fully_applied_journal() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.owner = "active"

    result = migration_status(manifest, store)

    assert result.reason == "migration_lock_busy"


@pytest.mark.parametrize(
    ("owner_value", "reason"),
    [
        (None, "operator_hold_owner_missing"),
        ("malformed", "operator_hold_owner_malformed"),
        (MigrationOwner(None, "unexpected", 1), "operator_hold_owner_posture"),
    ],
)
def test_status_rejects_missing_or_malformed_owner_metadata(owner_value, reason: str) -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.inspect_owner = lambda: owner_value

    result = migration_status(manifest, store)

    assert result.reason == reason


def test_status_rejects_owner_runner_schema_version() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.inspect_owner = lambda: MigrationOwner(None, "clean", 999)
    assert migration_status(manifest, store).reason == "operator_hold_runner_schema_version"


def test_status_redacts_schema_inspection_exception() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.inspect_schema_object = lambda name: (_ for _ in ()).throw(RuntimeError("sensitive"))
    result = migration_status(manifest, store)
    assert result.ready is False
    assert result.reason == "operator_hold_schema_inspection"


def test_status_rejects_extra_above_manifest_journal_version() -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.journal[27] = replace(store.journal[26], version=27)
    assert migration_status(manifest, store).reason == "operator_hold_journal_version_out_of_range"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"runner_schema_version": 999}, "operator_hold_runner_schema_version"),
        ({"state": "attacker-controlled"}, "operator_hold_journal_state"),
        ({"owner_attempt_id": 7}, "operator_hold_journal_owner"),
        ({"started_at": "not-a-timestamp"}, "operator_hold_journal_timestamp"),
    ],
)
def test_status_rejects_malformed_journal_with_stable_reason(changes, reason: str) -> None:
    store = MemoryMigrationStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    assert apply_migrations(manifest, store, attempt_id="first").ready is True
    store.journal[1] = replace(store.journal[1], **changes)
    result = migration_status(manifest, store)
    assert result.ready is False
    assert result.reason == reason
    assert "attacker-controlled" not in result.reason
