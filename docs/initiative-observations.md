# InitiativeObservation v0

`InitiativeObservation` is a transport-neutral, evidence-backed reading of an
external public project's apparent initiative. It records what a scout inferred
from a pinned source state. It does not attribute the initiative to maintainers,
prove repository control, or admit a project into a federation.

## Compatibility posture

The profile is persisted under the existing `Artifact` label with
`role=initiative_observation`. This is an additive contract:

- existing stores reuse the `Artifact.id` uniqueness constraint;
- older readers can retain the node as generic artifact evidence;
- aware readers select the role and require
  `schema_version=devgraph.initiative-observation.v0`;
- no canonical `Initiative` work-object field or stored decoder changes;
- no new Neo4j label or migration is introduced.

An observation can later be referenced by a separately authorized claim or
declaration contract. v0 does not implement project claiming, key binding,
Nostr publication, or a maintainer declaration.

## Required contract

| Field | Meaning |
|---|---|
| `id` | Stable observation identifier using the Dev Graph ID grammar. |
| `project_id` | Stable transport-neutral project reference allocated by the producer. |
| `subject_kind` | `github_repository` or `github_organization`. |
| `subject_url` | Normalized HTTPS GitHub locator; useful as an alias, not ownership proof. |
| `title` | Concise inferred initiative title. |
| `problem` | Evidence-backed problem the project appears to address. |
| `desired_state` | Apparent intended future, phrased without claiming maintainer authorship. |
| `evidence_urls` | One or more HTTPS sources supporting the observation. |
| `observed_by` | Scout or producer identifier. |
| `confidence` | Finite value from `0` through `1`. |

Optional provider and attestation fields are `github_node_id`, `source_commit`,
and `observation_signature`. The embedded signature is optional because current
API authorization plus its `EventReceipt` already establishes the local actor
context; absence of an embedded signature must not be presented as a portable
cryptographic attestation.

Server-owned fields are fixed in v0:

```yaml
schema_version: devgraph.initiative-observation.v0
authorship: inferred
claim_status: unclaimed
```

Clients cannot set `authorship` or `claim_status` during creation. This
branch exposes no claim, amendment, or rejection mutation and does not rewrite
the original scout observation.

## API

All data routes use the existing scoped credential verifier.

| Method and path | Required scope | Purpose |
|---|---|---|
| `GET /initiative-observations` | `devgraph.read` | List typed observations. |
| `GET /initiative-observations/{id}` | `devgraph.read` | Read one observation. |
| `POST /initiative-observations` | `devgraph.write` | Append one observation and atomic local receipt. |
| `GET /monitor/snapshot` | `devgraph.read` | Read the monitor's redaction-safe aggregate. |
| `GET /` | none | Load the official operator frontend; data still requires a read credential. |
| `GET /monitor` | none | Compatibility alias for the official operator frontend. |

Creation requires `Idempotency-Key`. A duplicate in the same operation and
subject scope returns the original receipt, `duplicate: true`, and no second
observation. The receipt subject is the underlying `Artifact` node.

## Official operator frontend

The repository-owned frontend is a dependency-free HTML/CSS/JavaScript view in
`src/devgraph/frontend/`, served by the same FastAPI adapter. It shows work
counts, observation claim states, local outbox
receipt states, recent safe activity, observation summaries, and an
interactive topology of monitor-visible nodes. Topology edges are projected
only when the relationship is stored and both endpoints are visible; the UI
does not infer missing links. Generic private artifacts and their edges are
excluded from this projection. The browser keeps the bearer token in
`sessionStorage`, so it is cleared with the tab; the HTML never embeds a token.

The topology uses a dependency-free SVG perspective projection with compact
spherical node shading and an in-browser force-directed layout. Every visible
stored edge contributes one unit spring by default; a node's total attraction
therefore grows with its visible connection count. Long-range pairwise
repulsion spreads clusters, while a stronger short-range collision term acts
in the current camera plane so nodes cannot remain overlapped merely because
they differ in depth. A small centering force keeps the graph in frame. `Node
separation` tunes both repulsion terms from `0.5` to `3.0` and defaults to
`1.5`. The camera-plane safety distance is 72 SVG units. A generated control
for each visible relationship type tunes its spring multiplier from `0` to
`2.5`; `Reset force defaults` restores node separation to `1.5` and every edge
multiplier to `1.0`.

Dragging a node pins it temporarily while connected structure relaxes around
it; `Settle graph` releases the pin and settles every node. Dragging empty scene
space changes camera yaw and pitch. Focused nodes can also be nudged with the
arrow keys, with Shift increasing the step. Hover and focus tooltips expose the
node title, kind, state, and relationship count without keeping those labels
permanently on the scene. `Reset layout` restores the deterministic camera and
recomputes node positions under the current edge strengths. The optional orbit
control stops whenever a node is selected or focused, and continuous orbit is
disabled when the browser requests reduced motion. Sphere size encodes
projected depth only; it is not a work priority, confidence, or importance
score. Force tuning and repositioning are presentation-only operations and do
not mutate the stored graph.

The first force model is connection-weighted rather than time-weighted because
the monitor's redaction-safe `graph_edges` envelope currently contains only
`source`, `target`, and `relationship`; it has no trustworthy edge timestamp.

`graph_nodes` and `graph_edges` are additive monitor-snapshot fields. New UI
code treats their absence as an empty topology, so it remains compatible with
an older snapshot producer. Node keys retain the underlying storage label and
ID, while `kind` can expose a safer typed presentation such as
`InitiativeObservation` for an `Artifact` profile.

Run the explicit memory-backed local fixture:

```bash
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open `http://127.0.0.1:4174/` and enter
`fake-credential-monitor`, or set a different obvious local fixture with
`DEVGRAPH_DEV_TOKEN`. This server uses memory storage and synthetic demo data,
including real `HAS_CHILD` and `HAS_ARTIFACT` fixture edges;
it proves no production deployment, durable persistence, external delivery, or
Nostr/secS integration.

## Verification

```bash
uv run pytest tests/model/test_initiative_observations.py \
  tests/api/test_initiative_observations_monitor.py -q
uv run pytest tests/model tests/api -q
uv run ruff check src tests
git diff --check
```
