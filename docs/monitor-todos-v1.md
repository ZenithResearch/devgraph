# Exact-base Todo monitor reads v1

The Todo check-in reads only records whose storage label is exactly `Todo` and
whose persisted `kind` is exactly `Todo`. Proposal, Initiative, Project, Issue,
and Task are subclasses in the model, but **none are included** in this view.
Multiply labelled Neo4j nodes, mismatched `kind` values, and untyped legacy
records are excluded rather than guessed into the base type.

This is an additive read projection. It does not change public `WorkKind`, Work
envelopes, named-write admission, workflow/lifecycle rules, native signatures,
existing signed monitor request targets, migrations, or stored records. It does
not create or backfill Todos. An empty response means no matching explicitly
typed base records were found; it does not mean all other Work is complete.

Both routes require the existing `devgraph.read` credential and enter through
`AuthorizedWorkGraph`. Successful reads record `monitor_todos` or `monitor_todo`
in the existing audit sink. Retained request/denial auditing uses the app's
existing middleware when configured. Missing credentials return 401; a valid
credential without read scope returns 403, before filter evaluation or storage.
Existing signed snapshot/topology profiles do not authorize these new paths.

## List

`GET /monitor/todos/v1`

| Parameter | Meaning |
| --- | --- |
| `limit` | Page size, 1–100; default 50. |
| `after_id` | Exclusive ascending ID cursor; omit on the first page. |
| `status` | Optional exact `draft`, `review`, `accepted`, or `archived` lifecycle state. Omit for all states allowed by archive visibility. |
| `archived` | `exclude` (default), `include`, or `only`. |
| `q` | Literal case-insensitive substring of the **redacted displayed title** or ID. Trimmed; maximum 200 characters. No description search. |

Unknown parameters, duplicate parameters, malformed percent/UTF-8 encodings,
invalid identifiers, control characters, unsupported enum values, and invalid
limits fail with 400. Queries are capped at 4,096 encoded characters. Empty `q`
means no search restriction. Empty status/cursor values are invalid; omit them.
Authorization takes place before this validation.

Example request:

```text
GET /monitor/todos/v1?limit=6&status=review&archived=exclude
Authorization: Bearer <local read credential>
```

Example response:

```json
{
  "schema": "devgraph.todos.v1",
  "generated_at": "2026-10-04T17:30:00+00:00",
  "items": [
    {
      "kind": "Todo",
      "id": "review-plan",
      "title": "Review the launch plan",
      "status": "review",
      "priority": "3",
      "version": "2",
      "created_at": "2026-10-03T12:00:00+00:00",
      "updated_at": "2026-10-04T16:00:00+00:00",
      "archived": false
    }
  ],
  "next_after_id": null,
  "has_more": false,
  "counts": {"total": 3, "draft": 1, "review": 1, "accepted": 1, "archived": 0},
  "matching_count": 1
}
```

`counts` describes **all exact-base Todos after `q` and archive visibility**, but
before the status selection, page cursor, or page limit. This allows status
summary buttons to remain meaningful when one status is selected. `total` is
the sum of those four lifecycle counts. `matching_count` additionally applies
`status`, still before cursor/limit. Neither count is the page length. For
example, default archive exclusion produces `archived: 0`; a contradictory
`status=draft&archived=only` has no items and zero matching count, while its
summary counts still describe the archived search scope.

Records are ordered by canonical ID ascending, independent of priority.
`has_more` is determined using one look-ahead record. `next_after_id` is the last
returned ID only when another page exists; otherwise it is null. An empty page
contains `items: []`, `has_more: false`, and `next_after_id: null`; its counts
still describe the full filter scope. A client can keep its own prior cursors
for a Previous button. Reset pagination when any filter changes.

Priority and version are **decimal strings**, preserving the complete signed
64-bit priority and positive signed 64-bit version ranges in JavaScript. Do not
convert them to `Number` when exact ordering/equality matters; use `BigInt` or
retain the string. Timestamps are timezone-aware ISO strings from the stored
record, not inferred deadlines or completion dates. Returned records must have
valid canonical content; malformed matching data is an unavailable read rather
than a fabricated Todo. Unknown extra stored fields are not returned. List
items omit descriptions and supporting-material references.

## Detail

`GET /monitor/todos/v1/{id}` returns the same item fields plus `description`, as
a direct object without a list envelope. Titles and descriptions use the
existing credential-pattern redaction helper. An absent, mismatched, subtype,
or untyped record returns 404. Archived base Todos remain readable by ID.
No new relationship, supporting-material, mutation, or completion route is
introduced for base Todo.

## Bounds and consistency

Unsearched Neo4j reads use a Todo-label-scoped aggregate and a bounded page in
one statement. They hydrate at most `limit + 1` records; aggregate computation
still depends on the number of base Todos. Existing `todo_id` uniqueness is
reused. No query scans or hydrates the complete graph or its relationships.

Search has to compare the same redacted title shown to the user, so neither
hidden credential text nor description text can be discovered through counts.
It materializes only ID/title/status/version/archive metadata for up to 10,000
exact-base Todos within the archive scope, then computes the search counts and
hydrates the selected page in one bounded batch. Exceeding that source budget
returns 503; it never silently truncates or reports incomplete counts as total.
Each Neo4j statement has a five-second timeout. A search may use two statements.

Pagination is a **current-state read**, not a pinned snapshot. Counts and pages
can change between requests, and inserts before the cursor require a refresh
from the first page. The adapter also checks the cursor-relative remaining count
against the exact expected bounded page length, rejects duplicate/out-of-order
IDs and archive/status violations, and checks page status counts against the
aggregate. Detected aggregate/page disagreement or search metadata/hydration
changes fail with 503 and require retry. This guards detectable inconsistencies;
it is not a claim of snapshot isolation or immutable counts during concurrent writes.

The subqueries use `CALL ()`, supported since Neo4j 5.23 and compatible with the
5.26 target. Neo4j read-committed isolation permits non-repeatable reads even
inside a single query, which is why a single statement is not described as a
snapshot. See the [Neo4j 5 subquery reference](https://neo4j.com/docs/cypher-manual/5/subqueries/call-subquery/)
and [concurrent data access reference](https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access/).

Successful responses and storage-unavailable responses use `Cache-Control:
no-store`. Storage failures return a safe 503 and must be shown as unavailable or
stale by the UI, not as zero Todos. An older host without these routes returns
404; a connecting preview must distinguish that missing capability from an
empty successful list. No live-host read, storage mutation, or performance claim
is implied by repository tests.
