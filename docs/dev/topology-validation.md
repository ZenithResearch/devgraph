# Topology UI qualification — 2 October 2026

Implemented on `codex/topology-ux`, based on public `origin/main` at
`1c6f367`. No installed runtime, live graph, read credential, or native
Wallet/secS authority was changed. The preview uses the explicit in-memory
demo. A separate disposable Neo4j 5.26.29 instance was stopped after testing.

## Changes and evidence

The nine requested improvements are implemented: scalable rendering/layout;
separated shapes and larger picking targets with hover labels; positioning undo;
action feedback; legend and API-backed granular filters; all five Work-kind
filters and eight visual identities; simpler copy; a collapsible Observer;
and validated preferences restored before the first graph query. The wide
layout uses a 1.618:1 graph/details split with responsive stacking and resizing.

`tests/browser/topology.mjs` is an optional reproducible browser qualification
script. Run a synthetic demo on port 4175 with manual refresh. It requires
Playwright and installed Chrome, and never uses a live credential. Set
`DEVGRAPH_PLAYWRIGHT_PACKAGE` to an installed runtime's package.json if
Playwright is outside normal Node resolution; `DEVGRAPH_QA_OUTPUT` selects the
screenshot/report directory (otherwise a temporary directory is used).

```sh
DEVGRAPH_AUTH_MODE=local-dev DEVGRAPH_MONITOR_DEMO=1 \
  uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4175
node tests/browser/topology.mjs
```

The saved [raw results](topology-qa-results.json) identify hardware, browser,
viewport, pixel density and fixtures. Browser tests use synthetic Work nodes
with three edge offsets; they do not prove performance for every possible
high-degree graph. Main-thread CPU slowdown does not reproduce a smaller Mac's
GPU, memory or worker performance.

## Performance

Apple M4 Pro, 24 GiB RAM, macOS 26.4.1, Chrome 154.0.8037.97, 1440×1000 CSS
pixels, 2× pixel density, fitted overview and 45 camera frames per case.
Power mode was not recorded. Frame intervals include browser scheduling;
draw timings measure synchronous drawing code, not the whole network-to-paint
path. Worker completion is measured separately.

| Fixture | CPU slowdown | Drawing p95 | Frame interval p95 | Worker completion |
| --- | ---: | ---: | ---: | ---: |
| 250 nodes / 713 connections | 1× | 0.7 ms | 16.7 ms | 32.8 ms |
| 1,500 nodes / 4,463 connections | 1× | 2.9 ms | 16.8 ms | 108.8 ms |
| 250 nodes / 713 connections | 4× | 2.6 ms | 16.7 ms | 31.9 ms |
| 1,500 nodes / 4,463 connections | 4× | 12.0 ms | 16.7 ms | 128.3 ms |

The initial renderer-only baseline on the same host, at 1× pixel density and
without explicit fit, measured p95 SVG redraws of 4.9 ms (250) and 60.7 ms
(1,500). The different fitted/density conditions mean these are supporting
baseline observations, not a controlled whole-application speedup ratio.

A preliminary 250-node SVG run reached 50 ms frame intervals under 4× slowdown.
That evidence set the canvas switch at 200 nodes. Small graphs retain keyed SVG;
large graphs keep only 20 SVG support elements and draw all map items in canvas,
with viewport culling and a separate accessible list. No node sampling is used.

Real disposable Neo4j topology reads (including bounded repeat-read validation
and projection) took 60–69 ms warm at 250 nodes and 318–329 ms warm at 1,500.
Cold reads measured 488 ms and 343 ms respectively. These timings exclude HTTP
authorization, encoding, transfer and the separate observation-list request.

## Interaction and layout

Browser checks passed for API filtering, explicit empty selections, reload
restoration, Observer collapse, hover previews, dragging and undo, reset and
undo, canvas picking, and absence of page errors. Additional browser checks
passed for keyboard movement/undo, Escape cancellation, null saved filters,
blocked local storage and layout overflow at 200% CSS zoom. CSS zoom supplements,
but does not replace, assistive-technology/browser zoom qualification.

With Observer collapsed and details open, measured graph/details track widths
were 676/418 at 1280, 769/475 at 1440, and 1060/655 at 1920 pixels. These follow
1.618:1 after rail, padding and divider subtraction. At 760 pixels the 688-pixel
tracks stack. At 1440 with Observer expanded the tracks measured 680/420.
No horizontal page overflow occurred in these checks.

Regression tests cover preserved hidden positions, removed items, bounded undo
history, stale worker replies, cancelled gestures, auth changes, stale filtered
responses, shared query encoding, empty/missing scopes, partial results,
source bounds, redaction/authority behavior and old signed v1 compatibility.
The shipped JavaScript page-proof producer interoperates with the Python v2
receiver using an ephemeral synthetic secS session. Query tampering, replay
and using v1 authority for v2 are denied. V2 provisioning has its own bundle
and replay state.

## Repository checks

All final commands exited 0:

- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv sync --locked`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv bash docs/dev/verification.md`
  — 2,153 Python tests passed; five existing opt-in tests skipped; generated
  ontology/backup artifacts and static checks passed.
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv run ruff check src tests scripts integrations`
- `node --test tests/frontend/*.test.mjs` — 119 passed.
- `git diff --check`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv build --out-dir /private/tmp/devgraph-ui-dist`
  — wheel and source distribution built; all five topology assets confirmed
  inside the wheel. Build dependency download needed network permission.

The verification script runs `uv run pytest -q` and `uv run ruff check src tests
scripts` itself. The remaining 111 Python warnings are existing Starlette
TestClient deprecation warnings. No new live authority was required.

## Remaining release qualification

A physical smaller Mac, Safari, actual browser zoom/assistive technology, and
a long-duration soak have not been qualified. The private installed runtime
has not been upgraded. Native Wallet/secS session issuance for signed v2 still
requires an independently approved compatible producer/provisioning; no new
grants or sign-in workflow were activated by this UI change. See the
[topology contract](../monitor-topology-v1.md) for exact limits and the
revision-validated read's concurrency limits.

## Platonic-solid visual revision

The subsequent shape revision replaces flat symbols with lit, projected regular
solids. The monitor's SVG nodes and legend were visually checked in the in-app
browser; keyboard selection opened the correct Project details. A separate
temporary loopback fixture exercised the shipped canvas renderer with all eight
types at both scales. At 2× pixel density, a 1200×700 canvas, 60 warm redraws and
no CPU throttling, drawing p95 was 0.60 ms for 250 nodes / 713 edges and 2.70 ms
for 1,500 nodes / 4,463 edges. Frame intervals p95 were 9.10 and 9.30 ms. These
are renderer-only observations in the in-app browser, not a controlled comparison
with the earlier Chrome run or qualification of a smaller Mac.

The geometry tests check regular edge lengths, expected face counts and closed
surfaces for all five Platonic solids. The canvas regression check verifies
1,500 items reuse eight type sprites plus one bounded unknown-type fallback
across redraws and zoom. All 122 JavaScript tests passed. The verification script
again passed 2,153 Python tests (five opt-in skips), generated-artifact checks and
lint. `uv sync --locked`, the separate `ruff check src tests scripts integrations`,
and `git diff --check` also exited 0. Commands use the same `UV_CACHE_DIR` shown
above; the standalone JavaScript command remains `node --test tests/frontend/*.test.mjs`.
