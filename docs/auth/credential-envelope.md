# Scoped credential envelope v0

Repo-local decision doc mirroring the accepted "scoped credential envelope v0" decision for Issue #7. This document specifies the v0 credential shape; it makes no runtime capability claim beyond what repo-local tests verify.

## Decision

- The v0 credential shape is a devgraph-owned signed/opaque service credential fixture carrying: actor id, session/correlation id, scopes, expiry, issuer, audience, and redaction partition claims.
- secS-signed envelopes and macaroon-shaped attenuation are future adapter targets, not v0 requirements.
- No trusted-localhost bypass is allowed.
- No raw token values may appear in logs, tests, docs, screenshots, or fixtures.
- Test fixtures use fake credential strings only (e.g. `fake-credential-alpha`).
- devgraph does not mint canonical identity.

## Scope vocabulary

The v0 scope vocabulary is exactly these seven scopes:

```text
devgraph.read
devgraph.write
devgraph.admin
devgraph.tool.use
devgraph.skill.use
devgraph.export.internal
devgraph.export.redacted
```

## Policy matrix

Each operation category requires exactly its own dedicated scope. Every grant is explicit: the admin scope does not implicitly satisfy any other category — broad DB access is a granted scope, not implied by agent identity.

| Operation category | Required scope             |
| ------------------ | -------------------------- |
| read               | `devgraph.read`            |
| write              | `devgraph.write`           |
| admin              | `devgraph.admin`           |
| export.internal    | `devgraph.export.internal` |
| export.redacted    | `devgraph.export.redacted` |
| tool.use           | `devgraph.tool.use`        |
| skill.use          | `devgraph.skill.use`       |

The export categories are policy vocabulary only; export operation code is owned by Issue #16.

## Code location

- Scope constants and policy matrix: `src/devgraph/auth/scopes.py`
- Tests: `tests/auth/`
