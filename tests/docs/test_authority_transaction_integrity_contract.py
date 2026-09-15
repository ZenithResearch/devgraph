from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_single_authority_and_atomic_audit_contract_is_documented() -> None:
    api = (ROOT / "docs/api.md").read_text()
    auth = (ROOT / "docs/dev/auth-and-authority.md").read_text()
    legacy_auth = (ROOT / "docs/auth.md").read_text()
    events = (ROOT / "docs/dev/events-and-outbox.md").read_text()
    combined = "\n".join((api, auth, legacy_auth, events))

    for required in (
        "exactly once per idempotent write",
        "sole source for scope enforcement, mutation, audit, and receipt attribution",
        "audit append rolls back",
        "receipt ID, receipt node, or `EMITTED_EVENT` edge persistence fails",
        "actor, session, correlation, issuer, and audience identifiers",
        "printable closed grammar",
        "graph-bound write session",
        "Wrong-scope requests reject before duplicate lookup",
        "publish under a lock",
        "graph-owned authority registry",
        "atomic locked operation",
        "execution-context-local `ContextVar` buffer",
        "asyncio tasks share a thread",
        "Cancellation and other `BaseException` exits restore",
    ):
        assert required in combined

    for stale in (
        "No API-boundary enforcement",
        "API idempotency are not implemented here",
        "Enforcement at an API boundary is future scope",
        "Every guarded operation verifies its credential through the seam",
        "All mutations are callable inside `EventOutbox.record_mutation_with_receipt`",
    ):
        assert stale not in combined
