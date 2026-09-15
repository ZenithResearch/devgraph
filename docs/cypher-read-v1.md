# Bounded Cypher reads v1

`devgraph query cypher --request-file /absolute/private/query.json` sends one
authenticated read to `POST http://127.0.0.1:8080/query/cypher`. The CLI loads the
existing owner-private `devgraph.read` credential internally, ignores HTTP proxy
environment settings, and never follows redirects. The request file must pass
the same private, regular-file checks as named Work request files.

This is a deliberately restricted Cypher language over public Work data. It is
not an arbitrary Neo4j console. Unsupported syntax fails closed before any query
is sent to the database. Existing Work operations and persisted data are
unchanged; this additive endpoint needs no migration.

## Request and result

The request has exactly these fields; duplicate JSON fields are rejected:

```json
{
  "schema": "devgraph.cypher-read-request.v1",
  "query": "MATCH (p:Project)-[:HAS_CHILD]->(i:Issue) WHERE p.id = $project AND i.archived = $archived RETURN i.id AS id, i.title AS title, i.priority AS priority ORDER BY priority DESC, id ASC LIMIT 50",
  "parameters": {"project": "my-project", "archived": false}
}
```

```json
{
  "schema": "devgraph.cypher-read-result.v1",
  "columns": ["id", "title", "priority"],
  "rows": [["my-issue", "Example issue", 2]],
  "row_count": 1,
  "limit": 50
}
```

Columns preserve projection order. Values are strings, signed 64-bit integers,
or booleans; values are never coerced and raw nodes, paths, lists and maps are
never returned. `LIMIT` limits returned rows, not all work performed by the
database. A full limit does not imply that no more matching rows exist. Ordering
is unspecified without `ORDER BY`; include a unique id projection and order to
make repeated reads stable. Reads do not create EventReceipts or alter Work data.

## Supported grammar

Keywords are case-insensitive. Kinds, properties, relationship names, parameters
and aliases are case-sensitive. Identifiers use ASCII letters followed by
letters, digits or underscores and contain at most 32 characters.

```text
MATCH (variable:Kind)
  [ -[:RELATIONSHIP]->(other:Kind) | <-[:RELATIONSHIP]-(other:Kind) ]
[ WHERE variable.property operator $parameter [ AND ... ] ]
RETURN variable.property AS alias [, ... ]
  | RETURN count(*) AS alias
[ ORDER BY alias [ASC|DESC] [, ... ] ]
LIMIT integer
```

- Kinds: `Proposal`, `Initiative`, `Project`, `Issue`, `Task`.
- Relationships: `HAS_CHILD`, `DEPENDS_ON`, `BLOCKS`; one directed hop maximum.
- Properties: `id`, `kind`, `title`, `description`, `status`, `created_at`,
  `updated_at`, `version`, `priority`, `archived`.
- Comparisons: `=`, `<>`, `<`, `<=`, `>`, `>=`; strings also support `CONTAINS`,
  `STARTS WITH`, `ENDS WITH`. Booleans support only `=` and `<>`.
- Values are supplied only as parameters. Parameter types must match the
  property's canonical type. Missing and unused parameters are rejected.
- `count(*)` is a sole projection; it cannot mix with property projections.
- Archived objects are included unless filtered explicitly with
  `WHERE variable.archived = $archived` and parameter `false`.

No inline literals, comments, semicolons, backtick identifiers, unlabelled nodes,
whole-node returns, dynamic properties, arbitrary functions, relationship
properties, optional matches, variable paths, Cartesian products, multiple
matches, `WITH`, `UNION`, `CALL`, subqueries, `LOAD CSV`, `USE`, user-supplied
`EXPLAIN`/`PROFILE`, schema commands or write clauses are supported.

Counts use normal Cypher row semantics: a one-hop count counts matching
relationships, including distinct parallel relationships if the stored graph
contains them. Only public canonical single-label Work nodes participate;
additional private labels and noncanonical `kind` values exclude a node.

## Enforcement and bounds

The service parses the entire request into a small grammar and emits fresh
Cypher with server-generated aliases and bound parameters. Submitted text is
never passed to the driver. Both endpoints of a traversal must be explicitly
labelled public Work kinds; generated predicates require exactly one label and
the matching canonical `kind` property. Private EventReceipt, migration, guard,
authority and generic storage properties are outside this language.

Every call verifies `devgraph.read` before its body is accepted and again before
execution. An unavailable backend denies the call. Anonymous or expired callers
receive 401; callers without the read scope receive 403. The new endpoint is
available only when the production composition root provides its dedicated
Neo4j read runner; development and unsupported backends fail closed.

| Bound | Limit |
| --- | --- |
| HTTP request / JSON structure | 32 KiB; two object levels; no arrays or nested parameter values |
| Query text | 8 KiB ASCII; 512 tokens |
| Parameters | 32 scalars; strings at most 8 KiB |
| Predicates / projections / ordering terms | 16 each |
| Returned rows | Required `LIMIT 1..100` |
| Returned string | 16,384 Unicode characters; overflow errors without truncation |
| Entire JSON result | 256 KiB; no partial success |
| Database transaction | Five seconds, including planning and result consumption |
| HTTP execution deadline / upload deadline | Eight seconds / five seconds |
| Concurrent DB queries | Two per service process; excess returns 429 immediately |
| Read pool | Two connections; two-second connect/acquisition bounds |

The read pool is separate from the named Work pool. The driver uses explicit
database selection, small incremental fetches and `READ_ACCESS` routing, then
`EXPLAIN` of compiler-owned text. Only a read-only query type with no update
counters is accepted. The same statement executes in that transaction and its
summary is checked again. Every transaction rolls back, including success; there
is no commit path. Retained audit records operation/outcome metadata and row
counts, never query text, parameters or response values.

Neo4j transaction timeouts are server-enforced, not a guarantee that an
unresponsive operating system stops a blocked syscall. The HTTP deadline can
return 504 while a stalled worker is unwinding; that worker retains its capacity
slot until completion so timeouts cannot create unlimited orphan queries.
Planning estimates are not used as a safety proof. Driver errors and query
failures produce fixed safe error codes without raw database messages.

The CLI reads HTTP responses incrementally and enforces the 256 KiB cap before
JSON decoding. Compressed responses and redirects are rejected.

## Why a compiler is required

Neo4j's driver documents that `READ_ACCESS` controls routing and does not enforce
access control. Enterprise Edition supplies graph privileges unavailable in
this Community deployment. A keyword blacklist plus `execute_read` would not
be an adequate boundary. [Driver access mode](https://neo4j.com/docs/api/python-driver/current/api.html#default-access-mode),
[Neo4j authorization](https://neo4j.com/docs/operations-manual/current/authentication-authorization/).

`EXPLAIN` plans without executing; `PROFILE` executes the query. Query type and
counter checks supplement the closed compiler and unconditional rollback.
[Neo4j query plans](https://neo4j.com/docs/cypher-manual/current/planning-and-tuning/execution-plans/).

Tests cover rejected mutation/introspection/external-call syntax, strict JSON,
parameter injection, canonical label guards, result limits, rollback, query
type/counter denial, concurrency and deadlines, safe HTTP failures, authentication,
and fixed CLI transport. Native acceptance must additionally execute generated
queries against the deployed Neo4j version and a disposable graph with private
canary rows before enabling the production endpoint.
