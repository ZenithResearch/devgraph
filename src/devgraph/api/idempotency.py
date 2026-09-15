"""API-level idempotent write execution over the Issue 9 outbox seam.

The executor composes existing seams without reimplementing any of
them: the façade verifies and write-scope-checks a graph-bound
``WriteSession`` before duplicate lookup, and the mutation runs inside
``EventOutbox.record_mutation_with_receipt`` so the write, its
EventReceipt, and the ``EMITTED_EVENT`` edge share one transaction with
digest-scoped deduplication. Raw idempotency keys pass through to the
outbox digest and are never stored or logged here.

A duplicate retry returns the previously recorded receipt and no new
mutation result — mutations passed to this executor must return a
non-None value so the duplicate path is unambiguous.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Any, Generic, Protocol, TypeVar

from devgraph.auth.context import AuthorityContext
from devgraph.events.model import EventReceipt
from devgraph.events.outbox import EventOutbox


class ExecutorWriteSession(Protocol):
    @property
    def authority_context(self) -> AuthorityContext: ...

    def transaction(self) -> AbstractContextManager[None]: ...


SessionT = TypeVar("SessionT", bound=ExecutorWriteSession)


class IdempotentWriteExecutor(Generic[SessionT]):
    def __init__(
        self,
        *,
        authorize_write: Callable[[str | None], SessionT],
        outbox: EventOutbox,
    ) -> None:
        self._authorize_write = authorize_write
        self._outbox = outbox

    def execute(
        self,
        *,
        credential: str | None,
        operation: str,
        subject_label: str,
        subject_id: str,
        idempotency_key: str,
        summary: Mapping[str, Any],
        mutation: Callable[[SessionT], Any],
    ) -> tuple[Any | None, EventReceipt, bool]:
        """Run ``mutation`` idempotently; returns (result, receipt, duplicate).

        ``result`` is None exactly when the call was a duplicate within
        the pinned ``operation + subject_label + subject_id`` scope."""
        session = self._authorize_write(credential)
        with session.transaction():
            result, receipt = self._outbox.record_mutation_with_receipt(
                authority=session.authority_context,
                operation=operation,
                subject_label=subject_label,
                subject_id=subject_id,
                idempotency_key=idempotency_key,
                summary=summary,
                mutation=lambda storage: _require_result(mutation(session)),
            )
        return result, receipt, result is None


def _require_result(value: Any) -> Any:
    if value is None:
        raise ValueError("idempotent mutations must return a non-None result")
    return value
