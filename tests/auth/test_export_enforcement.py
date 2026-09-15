"""Commit 3: scope-gated export operations on the authorized façade.

Deny-by-default: internal export requires exactly the internal export
scope; redacted and public-safe-summary exports require exactly the
redacted export scope. Insufficient scope raises a safe ForbiddenError
— never a silent downgrade to a lesser mode — and denied calls write no
audit record. Fixtures use obviously-fake credentials only.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from devgraph.auth import (
    SCOPE_EXPORT_INTERNAL,
    SCOPE_EXPORT_REDACTED,
    SCOPE_READ,
    AuditLog,
    AuthorizedWorkGraph,
    CredentialEnvelope,
    ForbiddenError,
    LocalDevVerifier,
    UnauthenticatedError,
)
from devgraph.model.base import utc_now
from devgraph.model.lifecycle import ProposalLifecycle
from devgraph.model.work import Issue, Proposal
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

FAKE_CREDENTIAL = "fake-credential-export"
AUDIENCE = "devgraph"
FAKE_MARKER = "FAKE-SECRET-export-42"


def _build_graph(scopes: frozenset[str]) -> tuple[AuthorizedWorkGraph, AuditLog]:
    storage = MemoryGraphStorage()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(
        FAKE_CREDENTIAL,
        CredentialEnvelope(
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
            scopes=scopes,
            expires_at=utc_now() + timedelta(hours=1),
            issuer="devgraph-test-issuer",
            audience=AUDIENCE,
        ),
    )
    audit_log = AuditLog()
    graph = AuthorizedWorkGraph(
        verifier=verifier,
        relationships=RelationshipGraph(storage),
        lifecycle=ProposalLifecycle(storage),
        audience=AUDIENCE,
        audit_log=audit_log,
    )
    return graph, audit_log


def _records() -> list:
    return [
        Issue(id="is-1", title="issue", description=f"secret={FAKE_MARKER}"),
        Proposal(id="p-1", title="private proposal"),
    ]


class TestInternalExportScope:
    def test_internal_scope_allows_and_audits(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_INTERNAL}))
        result = graph.export_internal(FAKE_CREDENTIAL, _records())
        assert result["mode"] == "internal"
        assert len(audit_log.records) == 1
        record = audit_log.records[0]
        assert record.category == "export.internal"
        assert record.operation == "export_internal"

    def test_redacted_scope_cannot_reach_internal_export(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_REDACTED}))
        with pytest.raises(ForbiddenError) as excinfo:
            graph.export_internal(FAKE_CREDENTIAL, _records())
        assert FAKE_CREDENTIAL not in str(excinfo.value)
        assert audit_log.records == []


class TestRedactedExportScope:
    def test_redacted_scope_allows_scrubbed_output(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_REDACTED}))
        result = graph.export_redacted(FAKE_CREDENTIAL, _records())
        assert result["mode"] == "redacted"
        assert FAKE_MARKER not in json.dumps(result)
        assert audit_log.records[0].category == "export.redacted"

    def test_read_scope_alone_cannot_export(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_READ}))
        with pytest.raises(ForbiddenError):
            graph.export_redacted(FAKE_CREDENTIAL, _records())
        assert audit_log.records == []

    def test_internal_scope_does_not_imply_redacted_export(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_INTERNAL}))
        with pytest.raises(ForbiddenError):
            graph.export_redacted(FAKE_CREDENTIAL, _records())
        assert audit_log.records == []


class TestPublicSafeSummaryScope:
    def test_redacted_scope_allows_summary(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_REDACTED}))
        result = graph.export_public_safe_summary(FAKE_CREDENTIAL, _records())
        assert result["mode"] == "public_safe_summary"
        assert FAKE_MARKER not in json.dumps(result)
        assert audit_log.records[0].operation == "export_public_safe_summary"


class TestUnauthenticatedExport:
    def test_missing_credential_fails_closed_without_audit(self) -> None:
        graph, audit_log = _build_graph(frozenset({SCOPE_EXPORT_INTERNAL}))
        with pytest.raises(UnauthenticatedError):
            graph.export_internal(None, _records())
        assert audit_log.records == []
