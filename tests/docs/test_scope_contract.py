from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
AUTH = ROOT / "docs" / "auth.md"
OPERATOR = ROOT / "docs" / "user" / "operator-runbook.md"
VERIFY = ROOT / "docs" / "dev" / "verification.md"


def _text(path: Path) -> str:
    assert path.is_file(), path
    return path.read_text()


def test_readme_records_current_operations_and_bounded_private_runtime() -> None:
    text = _text(README)

    for term in (
        "Neo4j operations tooling",
        "Forward-only migration",
        "backup-artifact",
        "restore-preflight",
        "disposable",
        "private, loopback-only production composition",
        "not a public or highly available",
        "production credential verifier",
    ):
        assert term in text
    assert "The GitHub #8 draft PR requires fresh exact-head review" not in text
    assert "bounded `devgraph` operator CLI" in text


def test_auth_docs_separate_fail_closed_http_from_exact_cli_receiver() -> None:
    text = _text(AUTH)

    for term in (
        "general protected HTTP surface has no local credential bypass",
        "exactly one local `secs-issue-create-v1` mutation",
        "projection-consumer form accepts the three owner-only artifacts",
        "one-shot Wallet form",
        "no executable selector or authority-selection",
    ):
        assert term in text
    assert "operator CLI accepts no credential and exposes no work-mutation command" not in text


def test_operator_runbook_routes_to_landed_ops_commands_and_nonclaims() -> None:
    text = _text(OPERATOR)

    for term in (
        "scripts/devgraph_migrate.py status",
        "scripts/devgraph_migrate.py apply",
        "scripts/devgraph_backup.py",
        "scripts/devgraph_restore.py",
        "scripts/render_backup_costs.py --check",
        "docs/runbooks/neo4j-migrations.md",
        "docs/runbooks/backup-restore.md",
        "docs/runbooks/readiness.md",
        "disposable synthetic",
    ):
        assert term in text
    assert "There is no database migration command to run." not in text
    assert "Neo4j migrations, backup/restore, and backup cost evidence (Issue 13)." not in text
    assert "production" in text
    assert "RPO" in text and "RTO" in text


def test_repository_verification_checks_issue_13_artifacts_and_generated_table() -> None:
    text = _text(VERIFY)

    for term in (
        "docs/runbooks/neo4j-migrations.md",
        "docs/runbooks/backup-restore.md",
        "docs/runbooks/readiness.md",
        "docs/ops/backup-cost-inputs.json",
        "docs/ops/backup-cost-table.md",
        "scripts/render_backup_costs.py",
        "uv run python scripts/render_backup_costs.py --check",
        "uv run pytest -q",
        "ruff check src tests scripts",
    ):
        assert term in text
    assert "DEVGRAPH_TEST_NEO4J=1" not in text
    assert "NEO4J_PASSWORD" not in text
