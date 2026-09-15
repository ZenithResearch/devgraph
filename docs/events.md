# Event receipts and transactional outbox

Every successful HTTP mutation is coupled to a local `EventReceipt` and an
`EMITTED_EVENT` edge in the same `GraphStorage.transaction()` as the domain
mutation.

`EventReceipt` stores operation, subject label/ID, actor/session/correlation
IDs, a domain-separated principal-scoped idempotency claim digest, status,
attempt count, retry time, timestamps, a redacted summary, and a safe
last-error summary. The raw idempotency key and reversible principal material
are not persisted.

## Idempotency

`idempotency_claim_digest` is SHA-256 over a versioned domain and unambiguous
length-prefixed UTF-8 fields `(issuer, audience, actor_id, raw_key)`. Session is
deliberately excluded so the same verified principal can retry across sessions.
The digest is uniquely claimed before mutation inside the same transaction as
the receipt and `EMITTED_EVENT` edge. Repeating the same operation/subject
scope returns the winner's receipt without a second mutation; another scope
fails closed. Different principals sharing a raw key have independent claims.

Legacy `idempotency_key_digest` receipts remain readable for dispatch. Because
their missing issuer/audience/raw-key inputs cannot be reconstructed safely, a
matching post-upgrade retry fails closed and must use a new key; the legacy
receipt and correlation are not returned.

For exactly `devgraph.issue.create.v1`, the receipt also stores
`request_digest_sha256` and `idempotency_key_digest_sha256`. Both are lowercase
SHA-256 hex. The request digest is compared on both the normal duplicate lookup
and an atomic-claim loser path; it does not enter the principal claim digest.
The raw-key SHA is bounded telemetry, is not unique across principals, and is
never written into the legacy `idempotency_key_digest` field. Exact duplicate
status is returned and audited without rewriting the winning receipt.

## Dispatch

Receipt states are:

- `pending`
- `retry_scheduled`
- `dispatched_dry_run`
- `failed`

`DryRunEventDispatcher` processes due local rows, marks success as
`dispatched_dry_run`, and uses exponential backoff until its configured attempt
limit. It deliberately performs no external I/O. This repository has no event
transport adapter, webhook, relay publisher, queue worker, or delivery daemon.

## Verification

```bash
uv run pytest tests/events tests/api/test_idempotency.py -q
```
