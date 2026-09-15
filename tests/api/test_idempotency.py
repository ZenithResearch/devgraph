"""Commit 5 (reordered before routes with recorded reason): idempotency.

Executor-level proofs over the Issue 9 outbox seam: retried identical
writes do not duplicate side effects, digest reuse outside its scope
fails closed, the raw idempotency key is never persisted, and an
unverifiable credential fails closed before any side effect. All
fixtures are synthetic.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import timedelta

import pytest

from devgraph.api.idempotency import IdempotentWriteExecutor
from devgraph.auth import CredentialEnvelope, LocalDevVerifier, UnauthenticatedError
from devgraph.auth.context import AuthorityContext
from devgraph.auth.scopes import SCOPE_WRITE
from devgraph.events.outbox import (
    EVENT_RECEIPT_LABEL,
    EventOutbox,
    IdempotencyScopeConflict,
)
from devgraph.model.base import utc_now
from devgraph.storage.memory import MemoryGraphStorage

AUDIENCE = "devgraph"
FAKE_CREDENTIAL = "fake-credential-idem"
RAW_KEY = "synthetic-raw-idempotency-key-do-not-store"


class _StubWriteSession:
    def __init__(self, context: AuthorityContext) -> None:
        self._context = context

    @property
    def authority_context(self) -> AuthorityContext:
        return self._context

    def transaction(self):
        return nullcontext()


def _executor() -> tuple[IdempotentWriteExecutor[_StubWriteSession], MemoryGraphStorage]:
    storage = MemoryGraphStorage()
    verifier = LocalDevVerifier(auth_mode="local-dev")
    verifier.register(
        FAKE_CREDENTIAL,
        CredentialEnvelope(
            actor_id="agent-frank",
            session_id="session-0001",
            correlation_id="corr-0001",
            scopes=frozenset({SCOPE_WRITE}),
            expires_at=utc_now() + timedelta(hours=1),
            issuer="devgraph-test-issuer",
            audience=AUDIENCE,
        ),
    )

    def authorize_write(credential: str | None) -> _StubWriteSession:
        return _StubWriteSession(verifier.verify(credential, audience=AUDIENCE))

    executor = IdempotentWriteExecutor(
        authorize_write=authorize_write,
        outbox=EventOutbox(storage),
    )
    return executor, storage


def _mutation_factory(storage: MemoryGraphStorage, calls: list[int]):
    def mutation(_authority):
        calls.append(1)
        storage.create_node("Issue", f"issue-{len(calls)}", {"title": "synthetic"})
        return f"issue-{len(calls)}"

    return mutation


class TestIdempotentExecution:
    def test_first_call_mutates_and_records_receipt(self) -> None:
        executor, storage = _executor()
        calls: list[int] = []
        result, receipt, duplicate = executor.execute(
            credential=FAKE_CREDENTIAL,
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key=RAW_KEY,
            summary={"message": "created"},
            mutation=_mutation_factory(storage, calls),
        )
        assert (result, duplicate) == ("issue-1", False)
        assert calls == [1]
        assert receipt.actor_id == "agent-frank"

    def test_retry_does_not_rerun_mutation(self) -> None:
        executor, storage = _executor()
        calls: list[int] = []
        mutation = _mutation_factory(storage, calls)
        common = dict(
            credential=FAKE_CREDENTIAL,
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key=RAW_KEY,
            summary={"message": "created"},
        )
        _, first_receipt, _ = executor.execute(mutation=mutation, **common)
        result, retry_receipt, duplicate = executor.execute(mutation=mutation, **common)

        assert calls == [1]
        assert result is None and duplicate is True
        assert retry_receipt.id == first_receipt.id
        assert storage.get_node("Issue", "issue-2") is None

    def test_same_key_different_scope_fails_closed(self) -> None:
        executor, storage = _executor()
        calls: list[int] = []
        mutation = _mutation_factory(storage, calls)
        executor.execute(
            credential=FAKE_CREDENTIAL,
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key=RAW_KEY,
            summary={},
            mutation=mutation,
        )
        with pytest.raises(IdempotencyScopeConflict):
            executor.execute(
                credential=FAKE_CREDENTIAL,
                operation="archive_issue",
                subject_label="Issue",
                subject_id="issue-1",
                idempotency_key=RAW_KEY,
                summary={},
                mutation=mutation,
            )
        assert calls == [1]

    def test_raw_key_is_never_persisted(self) -> None:
        executor, storage = _executor()
        calls: list[int] = []
        executor.execute(
            credential=FAKE_CREDENTIAL,
            operation="create_issue",
            subject_label="Issue",
            subject_id="issue-1",
            idempotency_key=RAW_KEY,
            summary={"message": "created"},
            mutation=_mutation_factory(storage, calls),
        )
        rendered = json.dumps([node.properties for node in storage.query(EVENT_RECEIPT_LABEL)])
        assert RAW_KEY not in rendered

    def test_unverifiable_credential_fails_before_side_effects(self) -> None:
        executor, storage = _executor()
        calls: list[int] = []
        with pytest.raises(UnauthenticatedError):
            executor.execute(
                credential="fake-credential-unknown",
                operation="create_issue",
                subject_label="Issue",
                subject_id="issue-1",
                idempotency_key=RAW_KEY,
                summary={},
                mutation=_mutation_factory(storage, calls),
            )
        assert calls == []
        assert storage.query(EVENT_RECEIPT_LABEL) == []
