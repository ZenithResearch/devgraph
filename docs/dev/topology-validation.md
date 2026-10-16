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

## Independent canvas panel and fullscreen

The canvas now owns a separate panel and a packaged `surface.js` controller.
The old whole-card expansion is removed. Native fullscreen and Escape back to
the page were verified in the in-app browser with synthetic demo data. The
canvas fills the screen with its own controls and item counts. Unit tests cover
native fullscreen exit events, denied API fallback, pending-entry cancellation,
keyboard focus containment, and restoration of the original DOM node, scroll,
focus, and pre-existing inert states. The responsive 1.618:1 details layout is
retained; the filters and overview remain outside the fullscreen surface.

`UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv sync --locked`,
`UV_CACHE_DIR=/private/tmp/devgraph-ui-uv bash docs/dev/verification.md`,
`UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv run ruff check src tests scripts integrations`,
`node --test tests/frontend/*.test.mjs`, and `git diff --check` exited 0.
The verification script ran 2,153 passing Python tests with the same five opt-in
skips and 111 existing warnings; all 127 frontend tests passed. The new asset's
allowlisted route is included in the packaged-asset API test. No installed
runtime or live graph was changed.

## UI walkthrough and reader refinement — 2026-10-02

The local synthetic monitor was exercised in the Codex in-app browser at
1280px desktop, 760px tablet, and 390px phone viewport widths. Viewport overrides
were reset after testing. The demo now has explicitly synthetic multiline work
plans, so reading can be assessed with realistic text length.

| Area | Browser evidence |
| --- | --- |
| Connection | Invalid demo key shows Access denied and clears private content; reconnect restores the graph and collapses settings. |
| Navigation and overview | Compact cards and search controls bring the canvas closer to the top; section links update the selected navigation item; desktop rail remains visible while scrolling. |
| Filters | Project subtype exclusion updates the graph; exclusion survives reload; archived-only produces a clear empty state; Clear filters restores all four demo items. |
| Canvas controls | Legend and arrangement menus open one at a time; Escape dismisses them; arrangement enables Undo; a keyboard move leaves the reader closed and Undo reports Positions restored. |
| Reader selection | Search, graph keyboard selection, visible-item browsing, related work, and connection links open the intended item. Work and Observation sections render their respective content. |
| Reading | Desktop shows the map and reader together with a default 1.618:1 split. Expand reader and Back to map keep navigation visible. The description scrolls within the reader beneath its fixed header. |
| Reader loading | Child work loads correctly. Supporting material keeps the action focused as cards appear. Unsupported material remains explicitly unresolved. Existing automated document tests cover safe text rendering, stale content, and late responses. |
| Persistence | Observer visibility, reader collapse, and subtype filters were checked across reload. |
| Responsive layout | At 760px and 390px the reader covers the map; the underlying map becomes inert while covered. At 390px the document and viewport widths both measured 390px. Selecting an item scrolls the complete pane into view. |
| Fullscreen | The selected reader remains beside the map; Escape restores the canvas to the page and focus to the fullscreen button. |
| Project selection | The monitor network loads; Load example and Run selection complete with three selected items and net utility 9. This only changes local planning state in the isolated test tab. |

The walkthrough fixes excess vertical spacing, prematurely stacked reader panes,
offscreen reading navigation, lost focus during reader reconciliation, stale
navigation highlighting, and menus overlapping one another. IDs and record
metadata now sit in a disclosure below the prose. Actions are collapsed by
default. Platonic-solid styling and the independent fullscreen canvas remain.

Final validation commands (all exit 0):

- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv sync --locked`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv bash docs/dev/verification.md`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv run ruff check src tests scripts integrations`
- `node --test tests/frontend/*.test.mjs`
- `git diff --check`

Results: 2,153 Python tests passed, five existing opt-in tests skipped, 111 existing
Starlette warnings; 131 frontend tests passed. No operator input was required.
Browser checks used synthetic data, not live Work or document files. Physical
small-Mac performance, Safari, assistive technology, and long-session qualification
remain the release limits listed above.

## Right-click reading modal — 2026-10-02

Right-clicking a topology node, its label, or an item in search/browse results
opens the existing reader in a native dialog. The reader retains its detail,
related-work, and supporting-material controls. Closing it restores the inline
reader's previous presentation and focus without changing the map layout.
Empty canvas retains the browser context menu. ContextMenu and Shift+F10 provide
keyboard entry; Escape, Close reader, and a click beginning on the backdrop
dismiss the dialog. Escape closes the reader independently of canvas fullscreen.

The synthetic demo was checked in the in-app browser: Project and Issue reading,
ContextMenu entry, focus wrapping, close/focus restoration, native fullscreen,
and a 390px responsive layout with no document overflow. Viewport overrides
were reset after checking. Shift+F10 dispatch, all eight node types, canvas hit
detection, label targeting, and late native close events are covered by unit
tests. The large-node canvas fixture was not rerun for this interaction change.
The desktop screenshot is saved in the workspace at
`research/devgraph-node-reading-modal.png` (outside this repository worktree).

Final validation commands (all exit 0):

- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv sync --locked`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv bash docs/dev/verification.md`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv run ruff check src tests scripts integrations`
- `node --test tests/frontend/*.test.mjs`
- `git diff --check`

Results: 2,153 Python tests passed, five existing opt-in tests skipped, 111 existing
Starlette warnings; 137 frontend tests passed. No operator input was required.
Browser checks used synthetic data. The installed runtime and live graph were
not changed; the release qualification limits above still apply.

## Zenith themes and design roundtable — 2026-10-02

All three themes from Zenith UI are packaged locally: Dark, Light, and Aqua.
The complete `public/tokens.css` file is copied byte-for-byte from source revision
`46c7fbf09de23a54ac04ede705e8a98623056dc3`. The revision, hash, and theme inventory
are recorded in `zenith-tokens.json`. No React runtime, font files, CDN, or sibling
checkout is required to use the monitor. Verify or update the pin with:

```sh
python3 scripts/sync_zenith_tokens.py
python3 scripts/sync_zenith_tokens.py --source /absolute/path/to/zenith-ui
python3 scripts/sync_zenith_tokens.py --source /absolute/path/to/zenith-ui --update
```

The default verification script also checks the pinned bytes. Changes must be
committed upstream before the importer will accept them. Review upstream theme
additions against the chooser and shared-controller tests when updating.

The requested roundtable used three agent review perspectives, including an
atomic-design review inspired by Brad Frost; it was not a personal consultation
with him. The integrated decisions were:

| Perspective | Decision |
| --- | --- |
| Atomic design | One pinned token source and one adapter/controller serve the monitor and Project selection. Filled actions use matched fill/content roles; links use readable text roles. Devgraph's panel radius is namespaced so it does not overwrite Zenith's `--radius`. |
| UX design | The labelled native selector remains available with Observer collapsed and inside fullscreen. System is a preference resolving to Light/Dark. Validate and restore the origin-wide preference before paint, synchronize tabs, and retain an in-memory choice if storage is unavailable. |
| Art direction | Preserve the Platonic forms, facet lighting, object hues, and 1.618 composition. Use strong contextual focus/warning colors on pale backgrounds, opaque backing for Aqua overlays, and theme-aware node outlines, edges, and labels. |

The shared theme is applied to the root element, including reading modals and
fullscreen surfaces. CSS roles repaint SVG, while computed graph colors are read
once per theme change and passed to Canvas. Material sprites remain cached by
kind; theme changes do not move nodes, rerun layout, fetch graph data, or rebuild
reader/selection forms. No font or layout redesign was introduced.

Browser checks used the synthetic local demo in the in-app browser. Dark, Light,
and Aqua were inspected on the graph and Project selection; Light and Aqua reader
modals were inspected, including a reader inside the fullscreen surface. The
saved choice survived reload and navigation, and changing it in Project selection
updated the open monitor tab. An edited example estimate and its open inspector
survived a theme change. Restoring the estimate and running selection produced
three selected items and net utility 9. No browser script errors were observed.

At 390px, both pages measured a 390px document width. The monitor picker remained
available with Observer collapsed; fullscreen controls wrapped within that width.
Viewport overrides were reset. A separate temporary renderer fixture exercised
250 and 1,500 synthetic nodes, with all three palettes at 1,500. This checks color
painting, not layout quality or a new performance benchmark. Unit tests also
cover palette updates without new sprites or changed positions, OS preference
changes, invalid/denied storage, cross-tab updates, and control synchronization.

Final validation commands (all exit 0):

- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv sync --locked`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv bash docs/dev/verification.md`
- `UV_CACHE_DIR=/private/tmp/devgraph-ui-uv uv run ruff check src tests scripts integrations`
- `node --test tests/frontend/*.test.mjs`
- `python3 scripts/sync_zenith_tokens.py --source /Volumes/home/bananawalnut/repos/zenith-ui`
- `git diff --check`

Results: 2,153 Python tests passed, five existing opt-in tests skipped, 111 existing
Starlette warnings; 148 frontend tests passed. No operator input was required.
The first verification attempt found one long line in the new sync script; it
was corrected and the full verification rerun passed. The installed runtime and
live graph were not changed. Physical smaller-Mac, Safari, assistive-technology,
and long-session qualification remain outside this check.
