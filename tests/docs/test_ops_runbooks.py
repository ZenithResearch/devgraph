from __future__ import annotations

from pathlib import Path

from devgraph.ops.migrate import MigrationStatus, load_manifest

ROOT = Path(__file__).resolve().parents[2]
RUNBOOKS = ROOT / "docs" / "runbooks"
MIGRATIONS = RUNBOOKS / "neo4j-migrations.md"
BACKUP_RESTORE = RUNBOOKS / "backup-restore.md"
READINESS = RUNBOOKS / "readiness.md"


def _text(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text()


def test_commit_five_runbooks_exist_and_state_disposable_boundary() -> None:
    combined = "\n".join(_text(path) for path in (MIGRATIONS, BACKUP_RESTORE, READINESS))

    for term in (
        "Prerequisites",
        "Expected safe output",
        "Stop conditions",
        "disposable",
        "synthetic",
        "operator",
        "reason",
    ):
        assert term in combined
    for nonclaim in (
        "not production evidence",
        "NOT_RUN_OPERATOR_INPUT_REQUIRED",
        "no RPO",
        "no RTO",
    ):
        assert nonclaim in combined


def test_migration_runbook_documents_explicit_status_apply_and_holds() -> None:
    text = _text(MIGRATIONS)

    for command in (
        "scripts/devgraph_migrate.py status",
        "scripts/devgraph_migrate.py apply",
    ):
        assert command in text
    for reason in (
        "migration_lock_busy",
        "checksum_mismatch",
        "schema_definition_mismatch",
        "recoverable_ddl_not_applied",
        "operator_hold_",
    ):
        assert reason in text
    for output_key in (
        "ready",
        "reason",
        "manifest_schema_version",
        "minimum_schema_version",
        "maximum_schema_version",
        "current_applied_version",
        "applied",
        "checksum_prefix",
    ):
        assert f"`{output_key}`" in text
    expected_contract = (
        "Expected safe output contains only `ready`, `reason`, "
        "`manifest_schema_version`, `minimum_schema_version`, "
        "`maximum_schema_version`, `current_applied_version`, and `applied` entries "
        "containing `version` plus `checksum_prefix`."
    )
    assert expected_contract in text
    assert "must not auto-migrate" in text
    assert "forward-only" in text
    assert "force unlock" in text


def test_migration_runbook_safe_output_shape_matches_successful_runtime_contract() -> None:
    manifest = load_manifest(ROOT / "migrations" / "manifest.json")
    output = MigrationStatus.clean(len(manifest.migrations), manifest).safe_output()

    assert set(output) == {
        "ready",
        "reason",
        "manifest_schema_version",
        "minimum_schema_version",
        "maximum_schema_version",
        "current_applied_version",
        "applied",
    }
    assert output["applied"]
    assert all(set(entry) == {"version", "checksum_prefix"} for entry in output["applied"])


def test_backup_restore_runbook_matches_named_volume_cli_and_confirmation_boundary() -> None:
    text = _text(BACKUP_RESTORE)

    for option in (
        "--data-volume",
        "--artifact-directory",
        "--source-stopped",
        "--target-volume",
        "--dry-run",
    ):
        assert option in text
    for command in (
        "scripts/devgraph_backup.py",
        "scripts/devgraph_restore.py",
        "neo4j-admin database dump",
        "neo4j-admin database load",
    ):
        assert command in text
    for safety_term in (
        "disposable_synthetic",
        "interactive",
        "interactive_restore_required",
        "post_restore_verification_required",
        "--overwrite-destination",
        "volume-nocopy",
        "discard",
    ):
        assert safety_term in text
    assert "production target" in text
    assert "external target" in text
    assert "NOT_RUN_OPERATOR_INPUT_REQUIRED" in text


def test_readiness_runbook_keeps_liveness_and_readiness_distinct() -> None:
    text = _text(READINESS)

    for term in (
        "/live",
        "/ready",
        "process-only",
        "HTTP 200",
        "HTTP 503",
        "migration",
        "canonical",
        "archive",
        "canonical_storage_unavailable",
    ):
        assert term in text
    assert "must not run migrations" in text
    assert "does not prove deployment" in text


def test_runbooks_cite_dated_authoritative_sources_without_secrets_or_personal_paths() -> None:
    combined = "\n".join(_text(path) for path in (MIGRATIONS, BACKUP_RESTORE, READINESS))

    assert "https://neo4j.com/docs/operations-manual/5/backup-restore/offline-backup/" in combined
    assert "https://neo4j.com/docs/operations-manual/5/backup-restore/restore-dump/" in combined
    assert "Retrieved: 2026-07-20" in combined
    for forbidden in (
        "/Users/",
        "/Volumes/",
        "/home/",
        "NEO4J_PASSWORD=",
        "Bearer ",
        "BEGIN PRIVATE KEY",
        "://user:password@",
    ):
        assert forbidden not in combined
