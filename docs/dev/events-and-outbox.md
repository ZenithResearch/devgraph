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
- Retry lookup is a closed storage capability for the claim and legacy-key digest fields. Neo4j filters by parameterized raw/current generic-encoded values before returning at most two receipts; malformed or ambiguous matches fail closed. Claim lookup uses migration 24's unique index. The legacy-key predicate remains server-side without a dedicated index; neither path loads unrelated receipt bodies into the mutation transaction. Archived receipts still protect retry identity. Stored bytes, atomic claims, scope checks and transaction deadlines are unchanged.
- A newly created receipt is linked with the required `link_new_event_receipt` storage capability. Storage retains a one-use proof bound to its actual active transaction, exact receipt identity and stored subject; Neo4j also binds the physical element ID. The bounded endpoint pair must be unique, match the claimed subject and have no incoming receipt link before `CREATE`. Proof is consumed on success and discarded at transaction completion or rollback. Existing receipts and duplicate retries never receive fresh proof. The generic relationship `MERGE` operation keeps its existing deduplication behavior. There is no fallback for an adapter missing the capability, and no new persisted proof field, migration, authority or wire format.

Neo4j additionally requires exact single-label `id` uniqueness constraints for
both endpoint labels. All admitted Work, Artifact, ReadinessAssessment, Arena and
SessionRef labels already have these committed constraints. Unsupported custom
storage adapters must implement the new capability; unconstrained custom
subjects are rejected. This is an explicit internal adapter contract change.
A bounded read-only indexed lookup resolves the endpoint pair, then the write
matches their physical element IDs and rechecks logical IDs, labels, receipt
subject and absence of a prior link. The final returned edge is checked too.
Uniqueness constraints protect against concurrent duplicate logical endpoints;
the final checks reject changed or replaced endpoints. This relies on admitted
writers and stable canonical schema, not repeatable-read isolation. Physical IDs
remain private to the same actual transaction and are never persisted or reused
in another write, consistent with [Neo4j's elementId contract](https://neo4j.com/docs/cypher-manual/5/functions/scalar/).
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
