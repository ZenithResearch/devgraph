"""Live, versioned resource paths; never an issuance-time object inventory.

Paths are receiver predicates over canonical containment. Identity is kind/id,
not a display name, URL prefix, role label or a caller-supplied ancestry list.
Callers must hold the Work mutation transaction across checking and using them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from devgraph.model.validation import validate_work_object_id
from devgraph.progress import TODO_KINDS
from devgraph.workflow_contract import PARENTS

ANCHOR_KINDS = ("Arena", "Initiative", "Project")


class ResourcePath(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_: Literal["devgraph.resource-path.v1"] = Field(alias="schema")
    path: tuple[str, ...] = Field(min_length=1, max_length=3)

    @field_validator("path", mode="before")
    @classmethod
    def anchors(cls, value):
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("resource path needs typed anchors")
        ranks = []
        for resource in value:
            if not isinstance(resource, str) or resource.count("/") != 1:
                raise ValueError("invalid resource path anchor")
            kind, identifier = resource.split("/", 1)
            if kind not in ANCHOR_KINDS:
                raise ValueError("unsupported resource path anchor")
            validate_work_object_id(identifier)
            ranks.append(ANCHOR_KINDS.index(kind))
        if ranks != sorted(set(ranks)):
            raise ValueError("resource path anchors must follow containment order")
        return tuple(value)

    def contained_by(self, parent: ResourcePath) -> bool:
        # Keep every parent anchor. Dropping Arena/A from Arena/A, Project/P
        # would broaden the grant when P is later moved to another Arena.
        return all(anchor in self.path for anchor in parent.path)


def paths_subset(child, parent):
    """Each path is a conjunction; the path array is a disjunction.

    An absent path constraint is unrestricted by path only. Explicit [] is
    rejected by the versioned grant parser, rather than interpreted as all.
    No lookup of today's hierarchy can prove a durable attenuation.
    """
    return (
        not parent
        or bool(child)
        and all(any(selected.contained_by(ceiling) for ceiling in parent) for selected in child)
    )


def current_resource_path(storage, kind, identifier):
    """Return one unambiguous current ancestry, or None; never guess parentage.

    Read only the target's bounded ancestor chain. Validate above the selected
    anchor too, so an ambiguous/corrupt container cannot confer authority.
    The canonical hierarchy has at most Initiative/Project/Issue/Task (4 nodes).
    """
    if kind not in TODO_KINDS:
        return None
    current = kind, identifier
    visited, chain = set(), []
    for _ in range(4):
        if current in visited:
            return None
        visited.add(current)
        item = storage.work_containment(*current)
        if (
            item is None
            or (item.node.label, item.node.id) != current
            or item.node.properties.get("kind") != current[0]
        ):
            return None
        chain.append("/".join(current))
        if len(item.parents) > 1 or len(item.memberships) > 1:
            return None
        if item.parents:
            edge = item.parents[0]
            if (
                item.memberships
                or (edge.to_label, edge.to_id) != current
                or edge.relationship != "HAS_CHILD"
                or edge.from_label not in PARENTS.get(current[0], ())
            ):
                return None
            current = edge.from_label, edge.from_id
            continue
        if item.memberships:
            edge = item.memberships[0]
            arena = storage.get_node("Arena", edge.from_id)
            if (
                current[0] not in ("Initiative", "Task")
                or edge.from_label != "Arena"
                or (edge.to_label, edge.to_id) != current
                or edge.relationship != "CONTAINS_WORK"
                or arena is None
                or (arena.label, arena.id) != ("Arena", edge.from_id)
                or arena.properties.get("kind") != "Arena"
            ):
                return None
            chain.append("Arena/" + edge.from_id)
        return tuple(reversed(chain))
    return None


def within_paths(storage, kind, identifier, paths, cache=None):
    if not paths:
        return True
    cache = {} if cache is None else cache
    key = kind, identifier
    if key not in cache:
        cache[key] = current_resource_path(storage, kind, identifier)
    chain = cache[key]
    return chain is not None and any(all(anchor in chain for anchor in p.path) for p in paths)


def path_predicate_cypher():
    """Same bounded chain validation, before ORDER BY/LIMIT in the database.

    The caller binds n and selector. No user input is interpolated into Cypher.
    All HAS_CHILD and CONTAINS_WORK witnesses count, including duplicates.
    """
    return (
        "(size(coalesce(selector.resource_paths, [])) = 0 OR EXISTS { "
        "MATCH chain=(root)-[:HAS_CHILD*0..3]->(n) "
        "WHERE NOT EXISTS { MATCH ()-[:HAS_CHILD]->(root) } "
        "AND all(item IN nodes(chain) WHERE size(labels(item)) = 1 AND "
        "labels(item)[0] IN ['Todo','Proposal','Initiative','Project','Issue','Task'] "
        "AND item.kind = labels(item)[0]) "
        "AND all(edge IN relationships(chain) WHERE "
        "labels(startNode(edge))[0] IN CASE labels(endNode(edge))[0] "
        "WHEN 'Project' THEN ['Initiative'] "
        "WHEN 'Issue' THEN ['Initiative','Project'] "
        "WHEN 'Task' THEN ['Initiative','Project','Issue'] ELSE [] END) "
        "AND all(item IN nodes(chain)[1..] WHERE "
        "COUNT { MATCH ()-[:HAS_CHILD]->(item) } = 1 AND "
        "NOT EXISTS { MATCH ()-[:CONTAINS_WORK]->(item) }) "
        "AND COUNT { MATCH ()-[:CONTAINS_WORK]->(root) } <= 1 "
        "AND NOT EXISTS { MATCH (arena)-[:CONTAINS_WORK]->(root) "
        "WHERE size(labels(arena)) <> 1 OR NOT arena:Arena OR NOT ("
        "(coalesce(arena.__devgraph_generic_encoding, '') = '' "
        "AND coalesce(arena.kind, '') = 'Arena') OR "
        "(coalesce(arena.__devgraph_generic_encoding, '') = 'json-v1' "
        "AND coalesce(arena.kind, '') = $path_arena_kind)) "
        "OR NOT labels(root)[0] IN ['Initiative','Task'] } "
        "AND any(selected IN selector.resource_paths WHERE "
        "all(anchor IN selected.path WHERE "
        "any(item IN nodes(chain) WHERE labels(item)[0] + '/' + item.id = anchor) "
        "OR EXISTS { MATCH (arena:Arena)-[:CONTAINS_WORK]->(root) "
        "WHERE 'Arena/' + arena.id = anchor })) })"
    )
