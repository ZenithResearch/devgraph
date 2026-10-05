# Work board

Open `/monitor/kanban/` from **Work board** in the monitor. The board starts with
all work, including standalone Proposals, Initiatives, Projects, Issues and Tasks.
Columns summarize two workflows; cards show their exact stage. The legend uses
the same Platonic solids and colors as topology. Light, Dark, Aqua and System use
the shared Zenith tokens.

Click or right-click a card to read it. Escape closes the reading modal and
returns focus to the card. The child-count button opens that item's direct-child
board. **Include descendants** expands the selected scope without adding unrelated
standalone work. Search, scope, type, workflow, exact stage, column and archive
filters persist across refresh. Credentials and Wallet authority are separate.

The board scrolls horizontally. Narrow screens offer a column selector. Each
column initially loads at most 30 cards while showing its complete filtered count;
**Load more** fetches the next bounded page. Refresh keeps loaded card windows,
focus and horizontal position. A changed continuation revision requires refresh.
The service refuses a projection exceeding 10,000 work records or 10,000 edges
per relationship rather than claiming a complete count from a partial scan.

Older work with no assigned stage appears in Backlog as **Stage not set**. Reading
or moving its parent does not write a stage. **Set stage** explicitly classifies
it into Backlog or initial Planning; later gates still require their evidence.
New records persist their default workflow and Backlog at creation. Proposal,
Initiative and Project default to Vibe CEO; Issue and Task default to execution.
The assignment stays with the record across parent and filter changes.

## Moving work

Chrome editing requires the matching Castalia Wallet developer extension, native
host, secS and receiver candidates, the exact canonical
`http://127.0.0.1:8080/monitor/kanban/` document, an existing identity and explicitly
reviewed workflow grants. Other browsers retain the complete reading experience.
The current source candidate still requires the isolated installed-native
qualification described in `docs/dev/kanban-qualification.md`; fixture tests do
not establish installed signing. A read access key can never authorize a move.

Use **Move** with a keyboard, or drag a card to propose a column. Both open the
same dialog listing legal exact stages and unmet requirements. A drop never
advances a card by itself. Within-column stage changes use **Move** too. Record
the required reviews, evidence links, requirement outcomes and layer verdicts,
then review the exact request in Wallet. Only a confirmed committed receipt
updates the board and announces success. Proposal acceptance remains a separate
signed lifecycle action requiring Decision provenance.

Waiting requires a reason, remembers the interrupted stage, and requires a
recorded resolution or decision to resume. Feedback returns work to Planning or
implementation. Changed content or evidence invalidates superseded approvals.
A parent still needs its own approvals after its children finish. Done requires
satisfied requirements, recorded evidence, review clearance and a verified handoff
at that item's boundary. These are signed attestations, not automatic checks of
external code, documents or CI.

Cancellation keeps the card in place. A version conflict refreshes current work.
If dispatch or receipt delivery is uncertain, the page retains the original
request and idempotency key in session storage. Reconnect the **same identity and
receiver** and use the explicit exact retry. Do not clear that pending request or
create a fresh key to repeat an uncertain mutation.

## API and CLI

Authenticated reads:

- `GET /workflows/v1` — versioned definitions and review gates.
- `GET /work/{kind}/{id}/workflow` — exact state, legal transitions and unmet gates.
- `GET /monitor/kanban/v1` — counts, bounded cards and per-column cursors.

Board query keys: `scope=Kind/id`, `descendants=true|false`, repeated `kind`,
`workflow`, `stage`, `column`, `q`, `archived=exclude|include|only`, `limit=1..100`,
`after=Kind/id` and `revision`. A continuation needs one column and its previous
revision. Optional `queue=not_started` includes only non-archived explicit Backlog
or legacy Draft work. `parentage=standalone` excludes any item with a recorded
Work parent, including archived parents. The default is `parentage=any`.
`order=priority` sorts descending by exact signed 64-bit priority, then canonical
key ascending; default `order=key` preserves existing ordering. Priority cursors
must still belong to the filtered column, and a containment change invalidates
the board revision. Search compares redacted displayed titles and IDs.
Legacy absence uses `workflow=unset` or `stage=unset`.

```sh
devgraph query workflows
devgraph query workflow Task example
devgraph query board --filter 'kind=Task&column=review&limit=30'
devgraph work workflow.transition --request-file /private/request.json --idempotency-key-file /private/key
```

Workflow writes retain `devgraph.work-request.v1` and use `workflow.assign`,
`workflow.review`, or `workflow.transition`. Review the versioned payload models
in `src/devgraph/workflow_contract.py` and frozen public request vectors. New
review/link records need their own resource grants, in addition to the work item.
`devgraph auth work plan --renew --include-workflows` creates a reviewable grant
plan; ordinary renewal preserves the existing scope and never adds workflows.

## Read your existing local graph in the preview

The developer helper `scripts/kanban_live_preview.py` serves the new board at
`http://127.0.0.1:4193/monitor/kanban/` while reading the installed service at
`http://127.0.0.1:8080` through its authenticated Work and bounded relationship
APIs. Your existing local read key works on this preview. It exposes no Work
mutations and does not access the database or run migrations.

For a schema-26 host, all records remain unclassified and display “Stage not
set”; accepted or archived lifecycle values do not imply workflow completion.
The helper reads at most 10,000 Work records and 10,000 edges per relationship
type, caches its read projection for one minute, and revalidates read authority
on every request. The displayed update time is the projection's actual read
time. Card rendering remains bounded to 30 per column initially.

Start it from the development checkout with an owner-private, regular file
containing a random 64-hex connection ticket, referenced by
`DEVGRAPH_KANBAN_SESSION_FILE`:

```sh
uv run uvicorn scripts.kanban_live_preview:from_environment --factory \
  --host 127.0.0.1 --port 4193 --no-access-log
```

Open the page and enter your local read key. An operator can alternatively open
`/monitor/kanban/#reader_session=TICKET` once within ten minutes of startup to
establish a four-hour HttpOnly local session. This keeps the real credential
native; the ticket is removed from the URL immediately. An expired session can
reconnect with the existing read key. The helper rejects a newer host schema
instead of hiding its workflow data; upgrade to the native board API then.
