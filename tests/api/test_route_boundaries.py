"""Commit 8: forbidden-import static checks per decision doc 0012.

Route files must stay thin: no Neo4j driver, no storage-layer imports,
no local lifecycle/auth/redaction/outbox logic. The checks read the
module sources deterministically — no runtime instrumentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "src" / "devgraph" / "api"
API_FILES = sorted(API_DIR.glob("*.py"))

FORBIDDEN_EVERYWHERE = (
    "import neo4j",
    "from neo4j",
    "devgraph.storage.neo4j",
)

FORBIDDEN_IN_ROUTES = (
    # Storage access and lifecycle mutation must flow through services.
    "from devgraph.storage",
    "from devgraph.model.lifecycle",
    # Local enforcement/redaction re-implementation is not allowed.
    "require_scope",
    "redact_",
    "record_mutation_with_receipt",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestForbiddenImports:
    def test_api_files_exist(self) -> None:
        names = {path.name for path in API_FILES}
        assert {"app.py", "routes.py", "schemas.py", "errors.py", "idempotency.py"} <= names

    @pytest.mark.parametrize("path", API_FILES, ids=lambda path: path.name)
    def test_no_neo4j_anywhere_in_api(self, path: Path) -> None:
        source = _source(path)
        for fragment in FORBIDDEN_EVERYWHERE:
            assert fragment not in source, (path.name, fragment)

    def test_routes_carry_no_storage_lifecycle_or_enforcement_logic(self) -> None:
        source = _source(API_DIR / "routes.py")
        for fragment in FORBIDDEN_IN_ROUTES:
            assert fragment not in source, fragment


def test_update_route_has_no_pre_read() -> None:
    source = _source(API_DIR / "routes.py")
    update_section = source.split("def update_work(", 1)[1].split("@app.post", 1)[0]
    assert "get_work_object" not in update_section
    assert "_repository" not in source


def test_api_docs_describe_completed_contract_without_stale_blocker() -> None:
    docs = (API_DIR.parents[2] / "docs" / "api.md").read_text()
    assert "Recorded blocker: unimplemented catalog rows" not in docs
    for route in (
        "PATCH /work/{kind}/{id}",
        "POST /exports/internal",
        "If-Match",
        "Idempotency-Key",
    ):
        assert route in docs
