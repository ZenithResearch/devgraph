# Usage guide

## Arenas

Use `devgraph arena create|patch|archive|member.set` with private request and
idempotency-key files. `devgraph query arena`, `query arena-members ID`, and
`query arena-of KIND ID` read Arena records and effective root membership.
The monitor displays Arenas separately from Work. See the
[Arena runtime contract](../ontology/arena-runtime.md) for the Gallery request,
versioned membership changes, and explicit local grant setup.

## Requirements and installation

- Python 3.10 or newer
- `uv` for the locked development environment

From the repository root:

```bash
uv sync --locked
```

The package installs a bounded `devgraph` console-script entry point. From a
source checkout, invoke it through the locked environment:

```bash
uv run devgraph status
uv run devgraph ontology
uv run devgraph service status
uv run devgraph logs api --stream stderr --lines 50
```

The configured private host provisions one rotatable, local-only read
capability. It authorizes the existing bounded HTTP query routes with exactly
`devgraph.read`; it cannot write or export. The CLI also has one separate
exact-operation receiver command for a secS-authorized Issue create; that
command accepts no bearer credential and does not widen the HTTP capability.

On macOS, configure an existing local directory and launch the private host:

```bash
mkdir -p "$HOME/Devgraph-Data"
uv run devgraph local configure --data-root "$HOME/Devgraph-Data"
uv run devgraph local start
uv run devgraph local status
uv run devgraph local read-credential status
uv run devgraph query work Issue
```

Use `--require-mounted-volume` when the chosen directory must reside on an
attached filesystem. This local command contains no cloud/provider workflow;
Hub owns that separately. See `docs/runbooks/local-macos-self-host.md` for
runtime prerequisites and reconfiguration behavior. An installed distribution
can invoke the same commands as `devgraph ...` without the `uv run` prefix.

To connect the bundled monitor, explicitly reveal the capability and paste it
into the password field:

```bash
devgraph local read-credential show
```

The value is printed only by this explicit command. It is stored in an
owner-only client file; the receiver-owned registry stores only its digest and
fixed read claims. Rotate it without changing graph data:

```bash
devgraph local read-credential rotate --actor-id local-codex-reader --ttl-hours 24
```

Rotation invalidates the prior value on the next request. `devgraph query work`
loads the owner-only file itself and never includes the credential in output:

```bash
devgraph query work Initiative
devgraph query work Project
devgraph query work Issue secure-local-devgraph-completion
```

These are bounded Work API reads, not arbitrary Cypher and not direct Neo4j
access.

## Approve and execute one secS-authorized Issue create locally

Install the fixed secS Wallet adapter at the fixed owner-controlled path:

```text
~/Library/Application Support/Zenith/secS/bin/secs-devgraph-issue-create-v1-wallet
```

The file must be owned by the current user, executable by its owner, and not
writable by group or other users. Then one command performs the complete local
flow:

```bash
chmod 600 request.json idempotency-key.txt
devgraph wallet-issue-create-v1 \
  --request-file request.json \
  --idempotency-key-file idempotency-key.txt
```

Open the exact URL printed by the command in the Wallet-enabled Chrome profile,
prepare the challenge, review the exact operation, and approve it. The command
waits for the fixed secS adapter, keeps the signed authority projection in an
owner-private temporary directory, immediately verifies and consumes it, then
removes the temporary projection. Success is the bounded Devgraph result with
the persisted Issue and canonical `EventReceipt`; Wallet or secS acceptance
alone is not reported as Work success.

The command accepts no projection-output, executable, URL, audience, database,
operation, scope, policy, or bearer-token option.

## Consume an existing secS projection locally

The fixed command consumes the exact `devgraph.issue.create.v1` request,
signed secS authority projection, and matching idempotency key:

```bash
chmod 600 request.json signed-projection.json idempotency-key.txt
devgraph secs-issue-create-v1 \
  --request-file request.json \
  --signed-projection-file signed-projection.json \
  --idempotency-key-file idempotency-key.txt
```

All three files must be regular, owned by the current user, non-symlinked, and
inaccessible to group and other users. The idempotency file is one ASCII key
of 16–128 characters with an optional single trailing LF. It must be the same
key whose SHA-256 digest is bound by the signed projection. A valid retry
returns the existing receipt with `duplicate=true`; it does not create a
second Issue, receipt, or edge. Both the create and its duplicate return the
same bounded `receipt.correlation_id` (`dg:sha256:<64-lowercase-hex>`) as the
secS-to-Devgraph telemetry handle. The output does not expose the authority
projection, signature, actor/session claims, request content, or raw key.

The receiver trust bundle is fixed under the configured data root:

```text
<data-root>/secrets/secs-magik/devgraph.issue.create.v1/
├── receiver.json
└── secs-public-key-registry.json
```

Both files must also be owner-only regular files. `receiver.json` has this
closed shape; values come from the deployed secS receiver policy, not from CLI
flags:

```json
{
  "schema": "devgraph-secs-issue-create-receiver.v1",
  "schema_version": 1,
  "operation": "devgraph.issue.create.v1",
  "audience": "devgraph://receiver-local",
  "stable_issuer": "secs:devgraph-receiver-local",
  "policy_binding": {
    "policy_id": "<receiver-owned-policy-id>",
    "policy_version": 1,
    "policy_digest_sha256": "<64-lowercase-hex>"
  }
}
```

`secs-public-key-registry.json` is the exact
`secs-public-verifier-key-registry.v1` production-key registry supplied by the
secS deployment. The command has no URI, user, password, database, audience,
operation, scope, or policy flags. It always loads `local.json`, uses
`bolt://127.0.0.1:7687` and the configured local credential file, proves the
canonical migration and persisted-data posture, then calls the exact consumer.
Malformed, expired, mismatched, or untrusted inputs fail before work mutation.
On macOS it also fails before bundle access when the data-root filesystem is
mounted with ownership disabled; follow the ownership preflight in the local
self-host runbook rather than trusting apparent file modes or changing them in
runtime.
The raw idempotency key, projection, registry, and database credential are not
returned.

## Verify the repository

```bash
uv run pytest -q
bash docs/dev/verification.md
uv run ruff check src tests scripts
git diff --check
```

The default tests use memory storage, fakes, and an in-process FastAPI client.
They do not start a listener or require a live Neo4j database.

## Run the read-only local monitor

```bash
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

Open `http://127.0.0.1:4174/`, paste `fake-credential-monitor`, and select
**Connect**. `DEVGRAPH_MONITOR_DEMO=1` adds synthetic work, a GitHub repository
observation, and stored relationships. Omit it for an empty graph.

The credential is a conspicuously fake, read-only fixture. Set
`DEVGRAPH_DEV_TOKEN` before startup to change its literal value. The browser
holds it in the current tab's `sessionStorage`; closing the tab clears it.

The frontend can inspect summaries, activity, observations, receipts, and the
3D topology. Node dragging, camera orbit, settling, reset, separation tuning,
and relationship-specific attraction tuning do not write graph data.

Search a title or ID in the loaded graph, then select a result or node to read
its complete **Description / plan**, priority, version, and progress in the
details pane. A plan is authored description text, related Work, or an explicitly
linked document; the monitor does not manufacture missing content. Expand the
Parent, Child work, dependency, or Task blocker sections to follow related Work.
Lists load on demand and page with **Load more**.

Drag the details divider on wide screens to adjust reading width, or focus it
and use the arrow keys. On narrow screens, use **Reading view** for a larger
reading surface. Selection, reading scroll, keyboard focus, and graph controls
survive refreshes. Failed refreshes retain the last successful data with an
explicit status. Credential changes clear the in-memory content cache.
Each read has a 30-second deadline. If a refresh fails, its companion request
is cancelled and retry waits for the next update instead of piling up requests.

**Load supporting material** resolves attached documents, links, requirements,
and acceptance criteria. **Open source** opens a validated HTTP(S) location;
**Read document** previews linked local text/Markdown when the host has an
explicit document root configured. Missing records and unavailable/unsupported
content are reported separately from empty content. See
[Supporting material and linked documents](supporting-material.md) for the
read contract and bounded local document configuration.

Use the zoom buttons or slider (25–400%), scroll/pinch over the graph, or press
`+`/`-` while the graph has focus. **Fit** (`F`) brings every visible node
into view; `0` restores 100% zoom. Shift-drag pans the view. Drag the **Graph
height** handle below the panel to resize it, or focus the handle and use Up/Down
(Shift for larger steps). **Expand view** fills the window; Escape restores it.
The graph adapts to its panel width and preserves the chosen height during
refreshes. These view controls do not change stored records or relationships.

The **Work**, **Observation**, and **Receipt** checkboxes show or hide those
node categories and their connecting edges. Choices survive automatic refreshes,
and hidden nodes retain their layout positions when shown again. Summary totals
continue to describe the full snapshot; the graph reports its visible node count.

Use **Focus in graph** or **Show neighborhood** from the reader to frame the
selected item; **Exit neighborhood** restores all eligible nodes. Labels
default to the selected neighborhood, with collision suppression. The details
pane always lists connections present in the snapshot, including hidden ones.
Work kinds also have different shapes. **Layout settings** contains the
optional orbit, settle, and force controls.

The private Neo4j-backed composition is operated through
`docs/runbooks/local-macos-self-host.md`. It serves the same frontend shell and
accepts the provisioned `devgraph.read` capability for its snapshot, observation,
Work, and attached-material reads. Never use the fixture token against that runtime. The
separately installed `devgraph.monitor.view.read.v1` PoP receiver remains an
additional exact-path option; it is not needed for the local read capability.

## Use the domain model in Python

The memory adapter is appropriate for tests and ephemeral local evaluation:

```python
from devgraph.model.repository import WorkObjectRepository
from devgraph.model.work import Initiative, Issue
from devgraph.relationships import RelationshipGraph
from devgraph.storage.memory import MemoryGraphStorage

storage = MemoryGraphStorage()
repository = WorkObjectRepository(storage)
relationships = RelationshipGraph(storage)

initiative = repository.create(
    Initiative(id="initiative-docs", title="Document current behavior")
)
issue = repository.create(
    Issue(id="issue-api", title="Describe the HTTP contract", priority=2)
)
relationships.add_parent(issue, initiative)

assert repository.get_by_id("Issue", "issue-api") == issue
assert relationships.children_of(initiative) == [issue]
```

Objects are frozen dataclasses. Repository mutations create updated instances,
increment `version`, and enforce lifecycle and storage validation.

## Construct an API application

`devgraph.api.create_app` is a factory, not a deployment launcher. Supply an
`ApiServices` instance containing:

- an `AuthorizedWorkGraph` wired to repositories and relationships;
- an `EventOutbox` using the same storage;
- that `GraphStorage` instance;
- a `CredentialVerifier` and its expected audience;
- optionally, a migration manifest and migration store for readiness.

The tests under `tests/api/` and `tests/integration/client_contract_fixtures.py`
are the executable construction examples. The included `LocalDevVerifier`
registers fixtures in memory; it does not parse or mint production tokens.

## Call the HTTP API

All data requests use an authorization header:

```text
Authorization: Bearer <credential>
```

Writes also require a caller-generated, non-empty `Idempotency-Key`. PATCH
requires `If-Match` with the current quoted version:

```http
PATCH /work/Issue/issue-api
Authorization: Bearer <credential>
Idempotency-Key: update-issue-api-v1
If-Match: "1"
Content-Type: application/json

{"title":"Document the exact HTTP contract"}
```

The shipped local monitor credential cannot execute this write. Use this
contract only against an application assembled with an authorized write
fixture or another verifier implementation.

See [the API reference](api.md) for every route and payload.

## Use the bounded Python HTTP client

`DevgraphHttpClient` accepts an injected synchronous transport, base URL, and
positive timeout. Its bounded generic methods are:

- `create_work`, `get_work`, and `list_work`
- `patch_work`, `transition_work_status`, and `archive_work`
- `get_work_children` and `get_task_blockers`
- `accept_proposal` and `convert_proposal`

The original `create_issue`, `get_issue`, `list_issues`, and
`transition_issue_to_review` calls remain compatible. Each call still requires
an explicit opaque `DevgraphRequestContext`; the client does not discover,
issue, persist, or refresh credentials. It validates successful JSON envelopes
and operation-specific receipts strictly, and converts RFC 7807 problem
responses, timeout, transport, content-type, and malformed-body failures into
safe typed exceptions. It does not expose exports, observations, monitor data,
or any direct storage path.

## Neo4j and operations

`Neo4jGraphStorage` reads `NEO4J_URI`, `NEO4J_USER`, and either
`NEO4J_PASSWORD` or `NEO4J_PASSWORD_FILE`; `NEO4J_DATABASE` is optional. Do not
place credentials in documentation or command history.

Repository scripts:

```bash
uv run python scripts/devgraph_migrate.py status --help
uv run python scripts/devgraph_migrate.py apply --help
uv run python scripts/devgraph_backup.py --help
uv run python scripts/devgraph_restore.py --help
```

Before operating on Neo4j, follow the exact runbooks in `docs/runbooks/`.
Backup/restore execution is deliberately bounded to the documented disposable,
synthetic target contract and does not establish a production recovery policy.
