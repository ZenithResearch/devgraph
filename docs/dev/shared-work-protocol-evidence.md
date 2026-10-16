# Work/Arena public source validation — 2026-10-02

Review branch: `codex/shared-work-arena-protocol`, based on public Devgraph
`1c6f367`. No private commit ancestry was merged. The source-only export retains
AGPL-3.0-only repository licensing and excludes signed secS policies/projections.

Parser SHA-256: `6ea8cc9da9e3ff8b6a5639e80616c616c2be5e5ccee6e30d057aff975c139c98`.

| Check | Result |
| --- | --- |
| Initial Arena regression against unchanged extracted Work parser | Expected failure: `invalid_work_request` |
| `cargo +1.96.0 test --locked --offline -p devgraph-work-protocol` | 6 tests + 1 compile-fail doctest passed |
| Same with `--features hostile-map-order,hostile-number-repr` | 6 tests + 1 doctest passed |
| `cargo +1.96.0 check --locked --offline -p devgraph-work-protocol --target wasm32-unknown-unknown` | Passed |
| `cargo +1.85.1 test --locked --offline -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr` | 6 tests + 1 doctest passed |
| `uv run --locked --offline pytest -q tests/model/test_shared_work_protocol.py` | 68 passed |
| `bash docs/dev/verification.md` | 2,181 passed; 5 environment-dependent skips; generated documents and static checks passed |
| `uv run --locked --offline ruff check src tests scripts integrations` | Passed |
| `node --test tests/frontend/*.test.mjs` (Node 24.18.0) | 109 passed |
| `cargo fmt --all --check` and `git diff --check` | Passed |

The corpus checks canonical bytes, original Work domains, distinct Arena request
domains, exact operations, complete resource sets, closed denial cases and
feature-independent serialization. These results cover synthetic fixtures and
repository behavior. They do not qualify installed native companions, a browser
SDK release, live authority, database operations or deployment.

Rust outputs and the Python environment were isolated outside the checkout;
no generated binaries or credentials are included in this change.
