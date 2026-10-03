# Filtered topology and saved views

`GET /monitor/topology/v1` returns a bounded, read-only map using the existing
safe monitor projection. The UI calls this route instead of `/monitor/snapshot`.
The legacy snapshot and signed v1 contract remain unchanged. Responses use
`Cache-Control: no-store`; the shell and packaged assets contain no graph data.

## Query contract

Authorize with the existing `devgraph.read` bearer or the separate signed v2
profile below. Authorization precedes filter validation and storage reads.
OpenAPI lists every parameter. Unknown keys, invalid values, duplicates, mixed
`none` selections, malformed UTF-8, and oversized input return a redacted 400.

| Parameter | Values / meaning |
| --- | --- |
| `category` | `arena`, `work`, `observation`, `receipt` (the UI calls these Records) |
| `work_kind` | `Proposal`, `Initiative`, `Project`, `Issue`, `Task` |
| `work_status` | `draft`, `review`, `accepted`, `archived`, `unknown` |
| `observation_status` | `unclaimed`, `claimed`, `amended`, `rejected`, `unknown` |
| `record_status` | `pending`, `dispatched_dry_run`, `retry_scheduled`, `failed`, `unknown` |
| `relationship` | Stored uppercase relationship names; restricts lines, retaining isolated items |
| `archived` | `include` (default), `exclude`, `only` |
| `arena` | `Arena:<id>` for outgoing reachability, `*` for any Arena, omitted for all nodes |
| `anchor` | Stable public node key for incoming/outgoing one-hop scope |
| `q` | Literal case-insensitive title/ID search, at most 200 characters |
| `node_limit` | 1–5,000, default 5,000 |
| `edge_limit` | 1–50,000, default 20,000 |

Repeat a multi-select parameter for OR. Omit it for all values; `none` is an
explicit empty selection. Facets combine with AND only for their applicable
category: a Work status filter does not exclude observations. Arena and anchor
reachability run before display facets and relationship filters. Missing scopes
return `scope_error` and an empty map instead of widening the view.

Example: `/monitor/topology/v1?category=work&work_kind=Issue&work_kind=Task`.
An observation's stable key is `Artifact:<id>`, with display kind
`InitiativeObservation`; other Artifact roles remain excluded. `EventReceipt`
and the `receipt` category stay wire compatible while display copy says Record.

Canonical targets sort parameter names and multi-values, omit defaults,
lowercase and trim search, encode UTF-8 using uppercase percent escapes, and
encode spaces as `+`. Unreserved `~` remains literal; `!*'()` are escaped.
`tests/frontend/fixtures/topology_queries.json` is shared by JS/Python checks.
The query is capped at 2,048 bytes and 80 fields.

## Response, consistency, and limits

The response includes `schema: devgraph.topology.v1`, normalized
`applied_filters`, `graph_nodes`, `graph_edges`, `facets`, `arenas`,
`relationship_types`, `counts`, `complete`, `scope_error`, and `revision`.
Counts distinguish available, scoped, matching and returned items/connections.
Facets apply other restrictions while ignoring their own selection. Per-node
`connection_count` counts unique connected items in the matching map before
result limits. Dashboard totals remain global and are not filtered totals.
Edges never reference an omitted node. A partial result is explicitly marked;
the UI shows **Partial map** and returned/matching counts. Narrow filters or
request a larger supported limit when a result is partial.

A bounded source materialization supplies the safe graph and all derived
summaries, counts and facets. Memory storage captures under the Work mutation
lock. Neo4j uses a parameterized statement with a five-second timeout, and
requires two consecutive equal materializations, allowing one retry (three
reads maximum). Continued change fails with 503; the UI retains the last good
map. The revision hashes the safe materialized projection, excluding read time
and health. It is a content revision, not a database transaction ID or a claim
of Neo4j serializable isolation. The bounded repeat-read check detects observed
concurrent changes; it cannot rule out an ABA change between reads.

Source limits are 10,000 public-label nodes and 50,000 connections, checked
with a sentinel. Exceeding source capacity returns 503 instead of silently
publishing incomplete totals. This endpoint is not an unbounded graph export.

## Additive signed v2 profile

The old operation continues to accept only exact `/monitor/snapshot` reads.
The new receiver uses `devgraph.monitor.view.read.v2`, session schema
`secs-devgraph-monitor-session.v2`, proof schema
`devgraph-monitor-request-proof.v2`, and `schema_version: 2`. All other session
fields, signature suite, origin, policy/currentness checks, time bounds,
header/body restrictions and replay rules follow
[the v1 contract](monitor-proof-of-possession.md). Only canonical topology
request targets are accepted. The proof binds every filter and limit.

The domain separators are:

- `secs-devgraph-monitor-session.v2/signature\0`
- `secs-devgraph-monitor-session.v2/session\0`
- `devgraph.monitor.view.read.v2/request-proof\0`
- `devgraph.monitor.view.read.v2/request-proof-digest\0`

The receiver loads only an independently provisioned bundle at
`secrets/secs-magik/devgraph.monitor.view.read.v2` beneath the private data root.
Its manifest uses `devgraph-secs-monitor-view-read-receiver.v2`, version 2 and
the v2 operation. V1 authority and replay state are never reused implicitly.
Missing v2 provisioning leaves signed topology access closed.

The packaged `DevgraphMonitorProof.createPage()` produces an ephemeral,
nonextractable Ed25519 page key. A compatible native secS session producer
binds its public key and the v2 operation in the approved session. Call
`page.install(session)` and `page.headers(canonicalTarget)` to obtain exact
request headers; `page.clear()` drops the session. Nothing is persisted.
The JS producer is tested end to end against the Python receiver with a
synthetic secS-signed session. This change does not provision a grant, modify
native Wallet/secS binaries, or activate a Wallet sign-in flow. The monitor's
existing bearer sign-in and separate authorized detail/list reads remain.

## Interaction and layout

A shared kind registry defines all eight shape/color/label combinations for
SVG, canvas, legend, Work-type filters and the accessible result list. Maps
under 200 items use keyed SVG; larger maps use canvas with viewport culling and
a keyboard-accessible item list. Canvas retains the same camera, picking,
selection and hover behavior. Labels are bounded and collision checked; a
preview discloses how many connected items are labelled. Dense target overlap
opens a chooser. Browse visible items and search provide alternatives to
picking tiny shapes. Canvas selection supports arrow-key positioning from the
focused graph, just like SVG selection.

Nodes use shaded, orthographically projected Platonic solids: Proposal is a
tetrahedron, Initiative an icosahedron, Project a cube, Issue an octahedron, and
Task a dodecahedron. Arena, Observation and Record reuse the icosahedron,
tetrahedron and cube in distinct orientations and colors. Lighting, facets and
silhouettes come from the same regular 3D meshes in both renderers. SVG reuses
one definition per type; canvas caches one 256px sprite per type, including
lighting, instead of drawing every facet on every frame. Solid orientation is
fixed for recognition while the map camera orbits. All silhouettes fit inside
the existing picking radius; hover and selection retain their white outline.

Automatic layout uses a spatial-grid worker, bounded neighbors/iterations and
a generation token. Filters, undo, gestures and credential changes invalidate
stale results. Pointer movement never runs layout. Redraws are coalesced to
animation frames; adjacency is indexed. Positions survive filtered responses.
Only an unfiltered complete response proves a missing item has disappeared.

**Undo positioning** retains up to 20 local gestures, including keyboard
moves, arrangement, spacing and reset. Escape/pointer cancellation restores
the gesture start. **Reset layout** is undoable and separate from **Fit view**.
Position history is local to the page and never mutates stored work.

The major graph/details layout uses a **1.618:1 golden-ratio split**, after
subtracting the Observer rail, padding and divider. At a map container width
of 1,060 pixels or less, details stack to protect graph/reader usability.
User resizing overrides the ratio. Observer and details collapse independently.
Small controls use normal readable sizes instead of forcing the golden ratio.

Preferences are versioned and scoped by service origin and a SHA-256 digest
of the current read credential. Filters, label density, Observer state and
reader width/collapse survive reload. No credentials, graph objects, descriptions
or layout history enter preference storage. Unsupported saved fields are
normalized with notice; invalid/unavailable storage falls back gracefully.
Clearing filters saves the default view. Tabs adopt saved preferences on reload.
