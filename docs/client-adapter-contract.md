# devgraph client adapter contract

The client mirrors the complete bounded Work API without expanding the server
or authority boundary. Its original Issue-only methods remain compatibility wrappers.

## Implemented now

`src/devgraph/client/http.py` provides one synchronous, HTTP-only
`DevgraphHttpClient`. It receives an injected request transport, a non-blank
string base URL, and a finite positive non-boolean timeout. Its generic surface
is:

- `create_work`, `get_work`, and `list_work` for `Proposal`, `Initiative`,
  `Project`, `Issue`, and `Task`;
- `patch_work` with an exact positive quoted `If-Match` version;
- `transition_work_status` and `archive_work`;
- `get_work_children` and `get_task_blockers`;
- `accept_proposal` and `convert_proposal`.

The original `create_issue`, `get_issue`, `list_issues`, and
`transition_issue_to_review` methods remain compatibility wrappers. The Issue
#14 historical remote gateway remains separate from the beta integrations. Its legacy
four-operation bearer proof is not the production signed-write path.

The client also provides `execute_named_work` and `execute_arena`, receiving an
explicit `DevgraphWorkContext` projection from the native authority path, plus
typed Arena and bounded relationship reads. The Hermes beta plugin composes
the installed CLI, whose Wallet/secS flow supplies this projection. A read
credential never gains write authority from a client method or plugin.

The client owns immutable strict response values, an immutable per-call
credential context, and typed fail-closed errors. The credential is excluded
from repr and model serialization, and validation errors hide raw inputs.
Caller-supplied idempotency keys are forwarded unchanged on writes and are
absent from reads. Duplicate mutations preserve the server's null-work,
original-receipt response.

## Adapter boundary

The client constructs HTTP requests and parses HTTP responses. It does not import or receive the application, route functions, service façade, repository, storage, verifier, policy, outbox, or application state.

Forbidden responsibilities include:

- direct Neo4j access;
- lifecycle, authorization, redaction, or outbox policy;
- credential or idempotency-key generation, persistence, normalization, retry, or fallback;
- ambient credentials, environment/file configuration, singleton clients, or localhost bypass;
- export, initiative-observation, monitor, or rich-query methods;
- Matrix, secS-magik, Dregg, or Hermes product behavior.

## Request and authority contract

Bearer-authenticated reads and compatibility calls receive an explicit immutable
`DevgraphRequestContext` carrying one opaque credential. Named Work/Arena writes
instead receive `DevgraphWorkContext`, with the native verifier's bounded signed
authorization projection. The test-only bearer proof constructs synthetic
`LocalDevVerifier(auth_mode="local-dev")` fixtures directly for exact read,
write, read+write, admin-only wrong-scope, invalid, and missing cases.
Production client code does not read verifier configuration or know credential
claims.

The full-sequence fixture carries only `devgraph.read` plus `devgraph.write`; it has no admin implication. Missing or invalid authority maps to a typed 401 `Unauthenticated` problem. Valid authority without the route's exact scope maps to a typed 403 `Forbidden` problem.

## Safe response handling

Strict immutable models reject malformed JSON, wrong envelope shapes, wrong
field types, unknown fields, non-canonical Work kinds/statuses, invalid receipt
operations/statuses, inconsistent duplicate shapes, and route/receipt/subject
mismatches. Mutation projections must carry the receipt-bound work identifier;
create/conversion must return `draft`, status transition must return its requested
target, archive must return `archived`, and proposal acceptance must return
`accepted`. PATCH validates identity and kind while preserving whichever
non-archived lifecycle state the server already held. Parsed RFC 7807 problems
retain only `status`, `title`, `type`, safe optional `detail`, and safe optional
`correlation_id`.

Distinct errors cover malformed success JSON, invalid or unknown-field success envelopes, unexpected success content type, malformed problem responses, timeout, and other transport failure. Built-in and HTTPX timeout exceptions map to the same safe `DevgraphTimeout` without retaining exception text. Errors do not retain raw bodies, credentials, idempotency keys, request payloads, stack traces, or transport exception text. Every method makes at most one transport call; there is no retry, downgrade, fallback, or reconstruction.

## In-process proof

The proof uses `fastapi.testclient.TestClient` over the real
`create_app(ApiServices(...))` route/service stack with `MemoryGraphStorage`.
Test-only observation handles exercise every bounded route and all five Work
kinds while verifying EventReceipts, emitted-event edges, audits, duplicate
non-rerun, and digest-only idempotency evidence.

This is an in-process contract proof. It starts no subprocess or server, binds no port or listener, makes no external network request, and provides no running-service, deployment, availability, performance, live-Neo4j, remote-access, or production-readiness evidence.

## Verification

```bash
uv run pytest tests/client -q
uv run pytest tests/integration -q
uv run ruff check src tests
git diff --check
```

Static boundary assertions are in
`tests/client/test_http_client_boundaries.py`; transport parity is in
`tests/client/test_http_client_parity.py`; the compatibility and full Work API
proofs are in `tests/integration/test_client_contract_proof.py`; denial and
process/listener/network instrumentation are in
`tests/integration/test_client_contract_denials.py`.
