# Devgraph for programmers

Devgraph exposes one Work model through a local HTTP API, Python client, CLI,
monitor, and agent integrations. Use the API or CLI boundary for application
work; do not give clients database credentials or reproduce authorization by
writing directly to Neo4j.

## Choose an environment

| Environment | Intended use |
| --- | --- |
| `devgraph.local_app` on port 4174 | Explicit synthetic, in-memory read fixture for UI/API experiments. No native signer or Neo4j needed. |
| Configured local host on port 8080 | Persistent graph backed by Neo4j. Managed lifecycle currently targets macOS. |
| Signed Work/Arena operations | Persistent host plus compatible native Wallet/secS, selected identity, and current resource grants. |

From a checkout or extracted source bundle:

```sh
uv sync --locked
DEVGRAPH_AUTH_MODE=local-dev \
DEVGRAPH_MONITOR_DEMO=1 \
uv run uvicorn devgraph.local_app:app --host 127.0.0.1 --port 4174
```

The fixture registers `fake-credential-monitor` with read scope and a 12-hour
lifetime. Restarting discards its graph and creates a new fixture session.
The production composition does not accept this credential. Follow
[beta setup](beta.md) for CLI installation and persistent hosting; the source
and agent packages do not bundle Neo4j, Java, or the native signing components.
Native companion bundles are currently private and separately distributed.
This public repository does not supply a public native signer download, so
signed-write setup requires separate access to compatible components.

## Read through HTTP or Python

With the fixture running, this requests one bounded page of synthetic Issues:

```sh
curl --fail --silent --show-error \
  -H 'Authorization: Bearer fake-credential-monitor' \
  'http://127.0.0.1:4174/work/Issue?limit=5'
```

The typed Python client accepts an explicit credential and transport. Run this
in the installed/`uv` environment; it reads the same fixture:

```python
import httpx
from devgraph.client import DevgraphHttpClient, DevgraphRequestContext

with httpx.Client(trust_env=False, follow_redirects=False) as transport:
    client = DevgraphHttpClient(
        transport=transport,
        base_url="http://127.0.0.1:4174",
        timeout=10,
    )
    context = DevgraphRequestContext(credential="fake-credential-monitor")
    page = client.list_work(context, kind="Issue", limit=5)
    for item in page.items:
        print(item.id, item.title, item.version)
```

The client validates response envelopes but does not discover, mint, persist, or
renew credentials. For real data, supply only the intended receiver's provisioned
credential through your application's private configuration, use the actual local
host, and keep redirects/proxy inheritance disabled. Do not hardcode real values.

Work list responses use `{"items": [...]}`. Continue a full page with its last
item's `id` as `after_id`; relationship pages use the last `Kind/id` as
`after_resource`. Keep filters, ordering, and page size fixed. A final full page
may be followed by an empty page. These reads are current-state pages, not a
frozen transaction spanning the whole traversal.

See the [HTTP reference](../api.md), [client contract](../client-adapter-contract.md),
and [named Work contract](../named-work-v1.md) for models and errors. Read requests
without valid authentication return 401; insufficient scope returns 403. A local
read bearer cannot write, change membership, or perform privileged exports.

## Use the configured CLI

These commands target the configured persistent host at `127.0.0.1:8080`, not
the fixture on port 4174. The CLI loads its read credential internally. Replace
the example IDs with records from your own graph:

```sh
devgraph local status
devgraph ontology
devgraph query work Issue --limit 20
devgraph query work Issue example-issue
devgraph query children Project example-project --limit 20
devgraph query dependencies Issue example-issue --limit 20
devgraph query blockers example-task --limit 20
devgraph query arena --limit 20
devgraph query arena-members example-arena --limit 20
devgraph query arena-of Task example-task
```

Use `--after-id` for Work/Arena lists and `--after-resource Task/example-task`
for relationship/member continuation. The separator is `/`, not the monitor's
colon-separated node key. Limits are 1–100; clients must request another page
explicitly. [Bounded Cypher reads](../cypher-read-v1.md) provide a restricted
Work query language through `devgraph query cypher --request-file FILE`, not
arbitrary database Cypher.

## Make a signed change

Complete [identity and grant setup](../runbooks/cli-auth-setup.md) and verify
the [native prerequisites](../runbooks/beta-native-prerequisites.md) first:

```sh
devgraph auth status --check
devgraph auth work status
```

Identity possession and a current grant are separate requirements. Agent names,
Arena membership, or a read credential do not confer mutation authority.

For an intended new Task, save this request in a private regular file owned by
your account. The example creates work in your real graph if you submit it:

```json
{
  "schema": "devgraph.work-request.v1",
  "operation": "create",
  "kind": "Task",
  "id": "example-task",
  "expected_version": null,
  "payload": {
    "id": "example-task",
    "title": "Review the launch checklist",
    "description": "Record the remaining checks and their evidence."
  }
}
```

Save one stable retry identifier in a separate private file. It must contain
16–128 ASCII letters, digits, `.`, `_`, `~`, or `-`; it is an idempotency key, not
an authentication secret. Use owner-private directories/files (0700/0600), absolute
paths, and a new identifier for each distinct intended operation. Then submit:

```sh
devgraph work create \
  --request-file /absolute/private/request.json \
  --idempotency-key-file /absolute/private/idempotency.txt
```

Wallet signs the request, secS evaluates its resource grants, and Devgraph
validates the projection and commits the canonical operation. The CLI command
must match the JSON `operation`. The six top-level fields are required.
Non-create operations require the observed current `expected_version`; relationship
operations also bind the referenced endpoints' versions. Use `patch` for authored
content and the dedicated operations for status, archive, parentage, dependencies,
blockers, Proposal acceptance, or conversion. Do not translate an arbitrary PATCH
into a privileged lifecycle action.

After a timeout or lost response, preserve the **exact request, identity, and
idempotency key**. Retry those same files; do not generate a new key or update
versions while calling it a retry. A commit may already exist. After a confirmed
version conflict, re-read and prepare a separately reviewed new operation.

The signed HTTP routes are `POST /work-operations/v1` and
`POST /arena-operations/v1`. They reject bearer authorization and need a valid
`X-Devgraph-Work-Authority` projection plus `Idempotency-Key`. The Python
`DevgraphWorkContext` transports an existing projection; it cannot sign or grant
authority. Prefer the CLI unless implementing that native authorization pipeline.

Arena writes use `devgraph arena create|patch|archive|member.set` with the same
two file arguments. Direct membership is limited to eligible parentless
Initiatives and Tasks. Descendants inherit from the Work root; moving a member
binds the Work version and both touched Arena versions. Follow the
[Arena contract](../../ontology/arena-runtime.md), including its explicit grant
extension. Do not turn a monitor reachability result into membership assignments.

## Read descriptions and attached plans

A Work object's `description` may contain an authored plan. Supporting material
is resolved separately:

```text
GET /work/{kind}/{id}/supporting-material?limit=50
GET /work/{kind}/{id}/supporting-material/Artifact/{artifact_id}/document
```

The corresponding client methods are `get_supporting_material` and
`get_work_document`. Supporting-material pagination returns an opaque
`next_cursor`; preserve the parent and limit. A changed parent can return 409,
requiring a fresh first page.

An attachment's metadata is not its contents. Local previews require
`DEVGRAPH_DOCUMENT_ROOTS` in the API process environment: an explicit JSON array
of narrow absolute document directories. They must be configured deliberately;
do not use a home directory or a directory containing credentials. The reader
accepts attached UTF-8 text/Markdown up to 256 KiB, rejects symlink escapes, and
does not fetch remote URLs. Handle `unconfigured`, `metadata_only`, `missing`,
`unsupported`, and `unavailable` separately from `readable`. Render content as
text. See the [supporting-material contract](../supporting-material.md).

## Understand the monitor and operational records

The monitor is a read-only projection. Its Arena filter follows outgoing graph
connections; dependencies can reach another Arena's Work without changing
membership. Use the explicit Arena API for actual membership. Project-selection
estimates and drafts are browser-local scenario inputs, not new Work fields or
canonical updates; see [project selection](project-selection.md).

`InitiativeObservation` is an append-only inferred Artifact profile for a scout's
reading of an external repository/organization. Problem, desired outcome,
producer confidence, and source links do not establish maintainer agreement or
canonical Initiative ownership. [Observation contracts](../initiative-observations.md)
describe its distinct schema and claim semantics.

`EventReceipt` is the current wire/storage name for an **unsigned local
operational record**, committed atomically with a mutation. `pending` concerns
the local outbox, not whether the graph change committed. It is not a portable
cryptographic receipt or proof of external delivery. Signed authorization and
retained operational audit are separate layers.

## Connect an agent and develop safely

The [Codex/Hermes integrations](agent-integrations.md) use the same CLI and
canonical skill. Codex supplies instructions for CLI use; Hermes registers
`devgraph_status`, `devgraph_read`, `devgraph_work_operation`, and
`devgraph_arena_operation`. Plugin settings never contain bearer or signing-key
bytes. Two agents choosing the same signer profile act as the same principal.

For development, use fixtures and disposable data, then run:

```sh
uv sync --locked
uv run pytest -q
uv run ruff check src tests scripts integrations
git diff --check
```

The default tests require no live Neo4j. Tests and a successful build do not
establish clean-machine native setup, production availability, remote hosting,
or backup recovery for an installation. Keep real data, seeds, credentials,
request files, and logs out of commits. The persistent graph stays under the
operator-selected local data root; publishing the source does not publish it.

Devgraph code, ontology, and bundled skills/plugins use
[AGPL-3.0-only](../../LICENSE); review [third-party notices](../../THIRD_PARTY_NOTICES.md)
and the separate native components' terms when distributing an integration.
