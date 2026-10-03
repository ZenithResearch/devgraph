# Complete Devgraph source integration

`codex/devgraph-complete` combines the open local Devgraph feature branches
against public `main` at `1c6f367`. Every source head below is an ancestor of the
integration branch, through explicit merge commits. No private Git ancestry or
companion implementation is imported into this public repository.

| Source PR | Head | Included work |
| --- | --- | --- |
| [#7](https://github.com/ZenithResearch/devgraph/pull/7) | `59a0141cefb22f3a5084a13d12c44e1e22f36ab1` | Shared Rust Work/Arena protocol, conformance vectors, native/WASM/MSRV CI and source notices |
| [#8](https://github.com/ZenithResearch/devgraph/pull/8) | `56b59b5dc83b0934e0e2ad8bce33ff2b2ef0496f` | Topology performance and interactions, fullscreen canvas, Platonic solids, reading modal, saved filters and Zenith themes |
| [#9](https://github.com/ZenithResearch/devgraph/pull/9) | `e5ded0f1d5eef791490b3811519377015b20a193` | Kanban, workflow contracts and gates, hierarchy, migration 27, ontology v0.7.0, CLI and real-data read preview |

The workflow-aware Rust parser and Python vectors from #9 extend the protocol
export in #7. Both sets of license notices and the native/WASM/MSRV CI job are
retained. The initial integration's `src`, `tests`, `crates`, `ontology`, and
Cargo files match #9 exactly; consolidation restores the additional CI and
protocol documentation from #7. CI explicitly runs the Kanban frontend tests
alongside the monitor tests.

The separate Dependabot #5 branch is not an authored local feature branch and
is outside this consolidation. Wallet, secS, and private SDK follow-ups remain
in their own repositories and retain their immutable protocol pins. Their
runtime code cannot be folded into one public Devgraph PR.

## Source versus installed UI

The read-only Kanban preview on port 4193 serves the new board while reading
the installed service at port 8080. Its Overview, Topology, and Project selection
links open that installed service, which may display a different release than
the board. Updating the installed runtime is a separate release activation,
including migration and matched-companion requirements.

## Validation

Checks run from the integration checkout on 2026-10-03 (all exit 0):

| Command | Result |
| --- | --- |
| `uv sync --locked` | Locked Python 3.10 environment installed |
| `bash docs/dev/verification.md` | 2,313 passed, 5 environment-dependent skips; generated artifacts and static checks passed |
| `node --test tests/frontend/*.test.mjs tests/frontend_kanban.mjs` | 153 passed |
| `cargo test --locked --offline -p devgraph-work-protocol` | 8 tests and 1 doctest passed |
| Same with `--features hostile-map-order,hostile-number-repr` | 8 tests and 1 doctest passed |
| `cargo check --locked --offline -p devgraph-work-protocol --target wasm32-unknown-unknown` | Passed |
| `cargo +1.85.1 test --locked --offline -p devgraph-work-protocol --features hostile-map-order,hostile-number-repr` | 8 tests and 1 doctest passed |
| `.venv/bin/ruff check src tests scripts integrations` | Passed |
| `cargo fmt --all --check` and `git diff --check` | Passed |
| `uv build --offline` | Python wheel and source distribution built |

These runs preserve the original Work/Arena signature vectors and include the
additive workflow vectors. Opt-in live database checks were not enabled.

Browser reads have independent UI evidence. Installed Chrome editing remains
unqualified until the matched Wallet → native host → secS → Devgraph test passes
in a disposable account/runner. Keep the consolidated candidate draft until
that gate is recorded; source and fixture tests cannot substitute for it.
