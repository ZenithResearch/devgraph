# Events and outbox implementation

HTTP mutations atomically claim local `EventReceipt` nodes and create
`EMITTED_EVENT` edges. Idempotency stores a domain-separated principal-scoped
SHA-256 claim digest, returns the existing receipt for same-scope retries, and
rejects cross-scope reuse. The only
dispatcher is a deterministic local dry run; no external delivery is present.

## Implemented guarantees

- `EventReceipt` model/status serialization exists in `src/devgraph/events/model.py` with stable lowercase outbox status values: `pending`, `dispatched_dry_run`, `retry_scheduled`, and `failed`.
- `EventOutbox.record_mutation_with_receipt(...)` uses `GraphStorage.transaction()` to persist the domain mutation, one local `EventReceipt` node, and one `EMITTED_EVENT` edge atomically. API idempotent writes wrap that storage transaction in the in-memory audit transaction: the audit append rolls back if mutation, receipt ID, receipt node, or `EMITTED_EVENT` edge persistence fails.
- Idempotency stores `idempotency_claim_digest`, never the raw caller key. Claim identity is exactly the versioned, length-prefixed `(issuer, audience, actor_id, raw_key)` digest; session is excluded. Claim scope is exactly `operation + subject_label + subject_id`; scope reuse fails closed. Memory holds one reentrant lock across the full transaction and Neo4j migration 24 uniquely constrains the claim property, so concurrent first writers have one winner.
- Legacy `idempotency_key_digest` receipts remain read/dispatch compatible. Matching legacy retries fail closed without receipt disclosure because their original authority inputs cannot be reconstructed.
- `DryRunEventDispatcher` provides deterministic local retry/backoff status transitions for due receipts.
- Event summaries pass through `devgraph.policy.redaction.redact_event` before receipt storage.
- v0 is in-process/local and graph-storage-backed by the configured `GraphStorage` adapter. No separate Postgres dependency is introduced solely for outbox.
- Work, Proposal lifecycle, and append-only initiative-observation HTTP mutations use this same receipt/idempotency boundary. Observation mutation responses use `observation` where Work mutation responses use `work`.
- Monitor snapshots and `devgraph.work-progress.v0` are read-only projections and do not create receipts or persist progress fields.
- The bounded HTTP client preserves duplicate receipts and forwards caller-owned idempotency keys, but it makes one transport attempt and implements no retry policy.

## Specified by issues

Issue #9 / GitHub #17 specifies event receipts and a transactional outbox. Decision doc `0011-event-receipt-outbox-boundary.md` says Issue #9 owns the EventReceipt model and taxonomy, transactionally writing domain mutation plus outbox/event receipt where storage supports transactions, idempotency key recording for event-producing mutations, retry-safe outbox status fields, and tests that event emission does not occur without a persisted mutation.

Issue #9 consumes Issue #3's transaction boundary, Issue #7's actor/session authority context, and Issue #8's redaction seam. Receipts link from the mutated subject using `EMITTED_EVENT` and carry only safe mutation evidence plus local outbox status.

The delivery posture is at-least-once. Exactly-once delivery is explicitly not claimed. The dry-run dispatcher is local test evidence only; it is not delivery proof.

## Not implemented yet

- External delivery adapters are not implemented.
- Matrix dispatch, webhook delivery, Hub eventbus publication, subscriptions, background workers, production delivery, and any external message bus integration are out of scope and not implemented.
- API route handling, API-boundary authorization, and API idempotency are implemented as an in-process adapter; they do not constitute deployment proof.
- The package can run this code in a private loopback local host, but this layer does not prove that any particular service is running, externally reachable, or production-authorized.

See [events and outbox](../events.md) for the concise user-facing contract.

## Verification

```bash
uv run pytest tests/events tests/api/test_idempotency.py -q
uv run pytest tests/api/test_authority_transaction_integrity.py -q
```
