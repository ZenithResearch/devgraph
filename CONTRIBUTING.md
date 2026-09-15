# Contributing

Use a branch and pull request for changes. Describe the observed problem, the
resulting behavior, and the checks that establish it. Use synthetic data in
fixtures, examples, and screenshots.

Install Python 3.10 or later, Node.js 24, and uv. From the repository root:

```sh
uv sync --locked
uv run pytest -q
node --test tests/frontend/*.test.mjs
bash docs/dev/verification.md
```

The in-memory demo and unit tests do not require a production graph. Native
signed-write integration tests need separately configured Wallet/secS components;
do not run them against an operator's production instance by default.

Preserve versioned API contracts, authorization, idempotency, expected-version
checks, and atomic graph mutations. Never edit released ontology/migration
payloads to change their meaning. Update the skill references and integration
tests when an exposed command changes.

See [SECURITY.md](SECURITY.md) for private vulnerability reports. Review the
repository's release license before submitting contributions for distribution.
