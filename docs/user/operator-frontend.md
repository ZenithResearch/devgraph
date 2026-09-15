# DevGraph operator frontend

The DevGraph operator frontend is the repository's official human-facing view
of the authorized work graph. Its source is owned by the `devgraph` package
under `src/devgraph/frontend/`; it is not a separate demo repository or an
external client copy.

## Routes and authority

The frontend shell is served at `GET /`. `GET /monitor` remains a compatibility
alias so existing bookmarks continue to work. The shell contains no embedded
graph data or credential. After an operator supplies a `devgraph.read`
credential, the browser calls the same scoped `/monitor/snapshot` and
`/initiative-observations` API routes available to other authorized clients.

Credentials are held only in the current tab's `sessionStorage`. Closing the
tab clears them. The frontend neither mints credentials nor bypasses the
service authorization, redaction, or projection boundaries.

The current bearer field is a repository-level verifier seam, not the final
Castalia authentication design. Production integration must replace it with a
wallet/secS Magik signing flow; root keys and `.castaway` contents must never be
loaded into this page.

The API now also contains a separate exact PoP receiver for
`devgraph.monitor.view.read.v1`, documented in
[monitor proof of possession](../monitor-proof-of-possession.md). This receiver
slice does not yet change the frontend: it does not mint an ephemeral page key,
request Wallet approval, sign refreshes, remove the bearer field, or replace
the separate observation-list request. The snapshot already contains the
topology, observation nodes, and Project/Issue progress needed for the graph
inspector, so those frontend changes are a bounded next slice rather than a
generic read authorization expansion.

## Current capabilities

The frontend presents safe work, initiative-observation, and receipt summaries;
recent graph activity; and an interactive 3D topology of monitor-visible stored
relationships. Operators can orbit the scene, drag nodes, settle or reset the
layout, tune node separation, and tune attraction independently for each
visible relationship type. These controls affect presentation only and never
mutate stored graph state.

### Filter by Arena

The **From Arena** selector narrows the topology to nodes reachable from the
selected Arena through outgoing connections in the loaded graph. **Any Arena**
shows the union reachable from all loaded Arenas; **All nodes** clears this
filter and includes disconnected work. An Arena's inspector also has a
**Filter from this Arena** action.

Reachability follows actual directed edges: root membership (`CONTAINS_WORK`),
child hierarchy (`HAS_CHILD`), dependencies, blockers, and attached observations
or mutation records where a stored edge exists. Dependencies can lead into
another Arena's work. That work is reachable in this view; its canonical Arena
membership has not changed. Incoming-only connections do not establish a path
from the selected Arena. Titles, IDs and provenance fields never substitute
for missing graph links.

Category and neighborhood filters apply after reachability. Hiding an Arena or
intermediate Work node therefore does not hide its otherwise reachable records.
Search and category counts use the selected Arena scope. Selecting a new Arena
exits neighborhood mode and fits the resulting graph; automatic refresh retains
the Arena choice. Details remain readable when their node is filtered out, with
an explicit **Show in all nodes** action to clear an incompatible Arena filter.

If the selected Arena disappears from the loaded snapshot, the filter remains
selected and shows an explanation instead of silently broadening the view.
Changing credentials clears the selection. This filter uses only the existing
monitor projection; it cannot follow paths through object types absent from
that projection. It changes no stored relationships, authorization or API shape.

When an operator highlights a `Project` or `Issue`, the node inspector renders
its `devgraph.work-progress.v0` progress projection. This is a local,
read-only structure derived from canonical `HAS_CHILD` relationships and Work
statuses; it is not persisted back onto the node. An `Issue` uses child Tasks,
falling back to its own status when it has none. A `Project` uses the leaf Work
at its canonical Issue/Task frontier: Tasks where an Issue is decomposed and
the Issue itself otherwise, then its own status when it has no Issues. Progress
is the proportion of tracked work in the ontology's terminal `accepted` or
`archived` states, and the inspector keeps the full
draft/review/accepted/archived breakdown visible.

## Local runtime

The repository includes an explicit memory-backed runtime for local inspection:

```bash
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open `http://127.0.0.1:4174/` and enter the synthetic read-only fixture
credential `fake-credential-monitor`.

The frontend is official repository source. The command above is still only a
development fixture: it proves no deployment, durable persistence, production
credential format, live Neo4j configuration, or external transport.

## Private production runtime

The loopback-only production service serves this frontend at
`http://127.0.0.1:8080/`. Its Neo4j monitor and initiative-observation
dependencies are wired. New local agents select `DEVGRAPH_AUTH_MODE=local-read`
and accept the owner-provisioned `devgraph.read` capability for the protected
read routes. Missing or wrong values remain HTTP 401, and the capability cannot
write or export. `/monitor/snapshot` can additionally accept its separately
installed exact PoP receiver. Do not paste the synthetic fixture credential
into the private page or add a localhost bypass.

## Verification

```bash
uv run pytest tests/api/test_initiative_observations_monitor.py -q
uv run pytest tests/api/test_monitor_progress.py -q
bash docs/dev/verification.md
uv run ruff check src tests
git diff --check
```
