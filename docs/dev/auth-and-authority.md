# Authorization implementation

The current code provides credential-envelope validation, an injected verifier
protocol, the environment-gated `LocalDevVerifier`, an exact-scope policy
matrix, the private-host `LocalReadCredentialVerifier`, `AuthorizedWorkGraph`,
safe auth errors, and an in-memory audit sink.

## Implemented guarantees

- Model-layer scoped authorization is implemented under `src/devgraph/auth/` on `main` by PR #20 / GitHub #15: the seven-scope vocabulary and explicit-grant policy matrix, the `CredentialEnvelope` parsed claim set, the env-gated `LocalDevVerifier` fixture behind the `CredentialVerifier` seam, `require_scope` plus the `AuthorizedWorkGraph` scope-enforcing façade with an in-memory audit log, and safe typed unauthenticated/forbidden errors. See `docs/auth.md`; verified by `python3 -m pytest tests/auth -q`.
- Deny-by-default and fail-closed posture are test-enforced: only `DEVGRAPH_AUTH_MODE=local-dev` enables the fixture verifier; there is no trusted-localhost bypass; devgraph does not mint canonical identity.
- Partition-aware export/redaction is implemented under `src/devgraph/policy/` on `main` by PR #21 / GitHub #16: the four export modes locked by decision doc `0010-redaction-fixture-boundary` (internal, redacted, denied, public-safe metadata summary), the D16 private-resource taxonomy and classification, the shared redaction helper, scope-gated `export_internal` / `export_redacted` / `export_public_safe_summary` façade operations, safe-error message scrubbing, and private reference-ID stripping in redacted mode. See `docs/export-redaction.md`; verified by `python3 -m pytest tests/policy tests/auth -q`.
- Authorized work-object CRUD/query operations are implemented on the façade for GitHub #25 / Issue 16: a kind-aware `WorkObjectRepository` delegate (`src/devgraph/model/repository.py`) over the `GraphStorage` protocol, authorized get/query reads, create/update/archive/status-transition writes (update is compare-and-set on `version` with a safe conflict error; status transitions validated against the explicit `GENERIC_STATUS_TRANSITIONS` table with no Proposal-acceptance side door), and export-by-ids record-source operations gated on exactly the existing export categories. No new scopes or categories. See `docs/auth.md`; verified by `python3 -m pytest tests/model tests/auth -q`.
- API-boundary authorization reuses the same verifier and façade. Each idempotent write verifies exactly once before duplicate lookup. The verified context remains in a graph-owned registry, while an immutable single-use session is atomically consumed under lock and supplies mutation/audit/receipt attribution. Audit transactions restore their execution context and discard pending entries on cancellation and other `BaseException` exits. Actor, session, correlation, issuer, and audience identifiers use a printable closed grammar that rejects controls before evidence emission.
- Initiative-observation reads and append-only creation, plus the safe monitor snapshot and derived `devgraph.work-progress.v0` projection, use the same exact read/write scope policy. The public frontend shell contains no graph data or embedded credential.
- A separate exact `devgraph.monitor.view.read.v1` receiver can authorize only the safe monitor snapshot with a secS-signed short session plus an ephemeral-page-key request proof. It grants no generic scope and does not authorize the separate observation-list route. The current frontend producer migration remains unimplemented.
- The bounded HTTP client accepts an explicit opaque credential per call and does not create authority, retry, access storage, or wrap export, observation, or monitor routes.
- The packaged local-host workflow runs a private composition on loopback with an operator-selected data root and one fixed-scope local read capability. Its receiver registry stores only the capability digest and fixed `devgraph.read` claims; this does not provide canonical identity or prove that a particular host is running.
- No generic production credential format, authority minting, Wallet producer, or external identity integration is implemented in this repository; the exact monitor receiver only verifies already-produced signed material.

## Specified by issues

Issue #7 specifies scoped authorization and caller/session authority. Decision doc `0009-scoped-credential-envelope-v0.md` selects a devgraph-owned scoped bearer envelope for v0 tests and local service enforcement. The implemented envelope fields are actor id, session/correlation id, scopes, expiry, issuer, audience, and redaction partition claims. Raw token values must not be printed in logs, tests, docs, screenshots, or fixtures.

Issue #7 specifies this v0 scope vocabulary:

```text
devgraph.read
devgraph.write
devgraph.admin
devgraph.export.internal
devgraph.export.redacted
devgraph.tool.use
devgraph.skill.use
```

Issue #7 specifies credential parsing/validation interfaces, scope checks, deny-by-default behavior, actor/session context propagation, fake-fixture tests, and a verifier seam compatible with future secS-magik/Dregg/macaroons authority. It explicitly does not make devgraph the canonical identity provider.

Issue #8 specifies partition-aware export/redaction: omission of private records from unsafe exports by default; redaction of sensitive fields in logs/errors/event receipts; and tests for private proposal/artifact/review-packet omission. The original issue text sketched modes as internal, agent-safe, and public/client-unsafe; decision doc `0010-redaction-fixture-boundary` supersedes that sketch with the implemented four-mode vocabulary (internal, redacted, denied, public-safe metadata summary).

Private-resource categories specified by the scope include private proposals or strategy notes, review packets and acceptance evidence not intended for public/client export, artifact payloads/screenshots/logs/source documents, private external-link URLs or repository metadata, actor/session metadata and token-usage aggregates, raw imported payloads, and any accidental auth envelope metadata. This document does not include actual private payloads.

## Not implemented yet

- No generic production credential format is implemented; bearer verification covers the env-gated fake fixture and one fixed local-host read capability only.
- No generic secS-magik, Dregg, or macaroon `CredentialVerifier` is implemented. The exact Issue-create and monitor-read consumers do not widen that seam.
- General API-boundary authorization is implemented through the same verifier/façade contract. The only separate route contract is the injected exact monitor PoP receiver, which cannot authorize another route or generic scope.
- Event receipts are implemented; external delivery remains out of scope.

Devgraph does not mint identity. See [authorization](../auth.md) for the
current contract and safe HTTP behavior.

## Verification

```bash
uv run pytest tests/auth tests/api/test_authority_transaction_integrity.py -q
uv run pytest tests/api/test_route_boundaries.py tests/policy -q
```
