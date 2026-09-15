# Arena ontology

An **Arena** is a continuing area of responsibility, such as Research,
Operations, or Community. It groups Initiatives and standalone Tasks without
requiring a Project or Issue just to organize a small piece of work.

This is canonical ontology vocabulary in v0.5.0. `Arena` has
`runtimeLabel: false` and is outside the `Todo` hierarchy. The release defines
containment semantics; it does not add a live Arena resource to the API, CLI,
database, or monitor. The normative machine-readable profile is
[`arena-contract.json`](arena-contract.json).

## Todo and Task

`Todo` is the common work-object base. The concrete Work interface accepts
`Proposal`, `Initiative`, `Project`, `Issue`, and `Task`; it does not accept a
bare `Todo`. A standalone executable to-do is therefore a **Task**.

An Arena can have zero or more direct members of either kind:

| Direct member | Eligibility |
|---|---|
| `Initiative` | No incoming Work parent edge. |
| `Task` | No incoming Work parent edge: a standalone Task. |

Parentless Projects, Issues, Proposals, bare Todos, and other Arenas are not
direct members in this first profile. Arena nesting is not defined. Membership
is optional: work does not need an Arena, and an Arena may be empty.

## Containment and inherited membership

`CONTAINS_WORK` points from an Arena to a direct member. A direct member may
belong to **at most one Arena**. Duplicate membership edges are not valid.

Work parentage continues to use the existing `HAS_CHILD` hierarchy:

```text
Arena: Research
  CONTAINS_WORK → Initiative: New instruments
                    HAS_CHILD → Project
                                  HAS_CHILD → Issue
                                                HAS_CHILD → Task
  CONTAINS_WORK → Task: Review a paper
```

The existing Work containment vocabulary `CONTAINS` describes the Work
hierarchy; `HAS_CHILD` is its current stored parent relationship. Arena
membership uses the separate `CONTAINS_WORK` predicate and does not make the
Arena a Work parent.

**Parentless** means there is no incoming `HAS_CHILD` edge from a Work object.
An archived parent still counts as a parent. The direct-membership eligibility
rule is evaluated against the graph, not a caller-supplied `parentless` flag.

Projects, Issues, and child Tasks inherit their root Initiative's Arena for
grouping and display. Follow incoming Work parents to the root and resolve
its direct Arena membership. Do not persist duplicate inherited Arena edges.
A root with no Arena leaves its descendants unassigned. Multiple Work parents,
cycles, or multiple direct Arenas are invalid; a consumer must not select an
arbitrary Arena. Dependencies and blockers never confer membership.

## Changes and archival

These are normative requirements for a future runtime implementation, not
new executable operations in this release:

- Assigning a Work parent to a direct Arena member must remove its direct
  membership atomically before or with the parent assignment. The member then
  inherits the new root's Arena, which may differ or be absent. Do not reassign
  the new parent or its ancestors to preserve the child's former Arena.
- Removing a Work parent must not invent direct membership. A newly standalone
  Task may be assigned to an Arena explicitly.
- Moving a direct member must replace its old Arena membership atomically;
  an Initiative's descendants then resolve to the new Arena automatically.
- Archive preserves the Arena and membership references. Archiving an Arena
  never archives, completes, deletes, or reparents its Work. Archived Work and
  Arenas may be hidden by an explicit view filter without changing membership.

Arena membership grants no authority and changes no Work status, priority,
Proposal acceptance rule, or EventReceipt. It is not a status lane or an
authentication scope. Arena ownership and per-Arena access control are not
defined by this profile.

## Compatibility and runtime boundary

The v0.1.0 through v0.4.0 bundles remain byte-identical. Consumers can pin an
older bundle or ignore the additive Arena terms. Existing Work records,
parent edges, schema migrations, operation names, and wire shapes stay valid.

A later runtime binding must provide stable Arena IDs and record schemas,
authorized reads and mutations, atomic membership/parent updates, archival,
audit and idempotency, bounded traversal, storage migrations, and API/CLI/UI
support. Ontology examples do not authorize direct database writes or add
`Arena` to the existing five-kind Work interface.

## Verification

```bash
uv run python scripts/build_ontology_bundle.py --check
uv run pytest tests/docs/test_arena_ontology.py tests/docs/test_ontology_publication.py tests/cli/test_cli.py -q
```
