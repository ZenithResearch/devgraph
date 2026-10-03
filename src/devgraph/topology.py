"""Bounded, versioned topology reads. All counts share one materialized projection."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

from devgraph.events.model import OutboxStatus
from devgraph.model.base import WorkStatus
from devgraph.monitoring import MONITORED_WORK_KINDS, build_monitor_snapshot
from devgraph.storage.base import StorageUnavailable

PATH = "/monitor/topology/v1"
CATEGORIES = ("arena", "work", "observation", "receipt")
PUBLIC_LABELS = ("Arena", *MONITORED_WORK_KINDS, "Artifact", "EventReceipt")
SOURCE_NODE_LIMIT = 10_000
SOURCE_EDGE_LIMIT = 50_000
MAX_QUERY_BYTES = 2_048
OBSERVATION_STATUSES = ("unclaimed", "claimed", "amended", "rejected", "unknown")
RECORD_STATUSES = tuple(x.value for x in OutboxStatus) + ("unknown",)
FACETS = ("category", "work_kind", "work_status", "observation_status", "record_status")
ENUMS = {
    "category": CATEGORIES,
    "work_kind": MONITORED_WORK_KINDS,
    "work_status": tuple(x.value for x in WorkStatus) + ("unknown",),
    "observation_status": OBSERVATION_STATUSES,
    "record_status": RECORD_STATUSES,
}


class InvalidTopologyFilter(ValueError):
    """Closed filter grammar; caller input is never echoed."""


@dataclass(frozen=True)
class TopologyFilter:
    category: tuple[str, ...] | None = None
    work_kind: tuple[str, ...] | None = None
    work_status: tuple[str, ...] | None = None
    observation_status: tuple[str, ...] | None = None
    record_status: tuple[str, ...] | None = None
    relationship: tuple[str, ...] | None = None
    archived: str = "include"
    arena: str = ""
    anchor: str = ""
    q: str = ""
    node_limit: int = 5_000
    edge_limit: int = 20_000

    @classmethod
    def parse(cls, query: str) -> TopologyFilter:
        if not isinstance(query, str) or len(query.encode()) > MAX_QUERY_BYTES:
            raise InvalidTopologyFilter("Invalid topology filters")
        if not query:
            return cls()
        if re.search(r"%(?![0-9a-fA-F]{2})", query):
            raise InvalidTopologyFilter("Invalid topology filters")
        try:
            pairs = parse_qsl(
                query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=80,
            )
        except (ValueError, UnicodeError):
            raise InvalidTopologyFilter("Invalid topology filters") from None
        values: dict[str, list[str]] = {}
        allowed = set(cls.__dataclass_fields__)
        for key, value in pairs:
            if key not in allowed or value in values.get(key, []):
                raise InvalidTopologyFilter("Invalid topology filters")
            values.setdefault(key, []).append(value)
        normalized: dict[str, Any] = {}
        for key, items in values.items():
            if key in (*FACETS, "relationship"):
                if items == ["none"]:
                    normalized[key] = ()
                elif "none" in items or any(
                    item not in ENUMS[key]
                    if key in ENUMS
                    else re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", item) is None
                    for item in items
                ):
                    raise InvalidTopologyFilter("Invalid topology filters")
                else:
                    normalized[key] = tuple(sorted(items))
            else:
                if len(items) != 1:
                    raise InvalidTopologyFilter("Invalid topology filters")
                value = items[0]
                if key in ("node_limit", "edge_limit"):
                    maximum = 5_000 if key == "node_limit" else 50_000
                    if not re.fullmatch(r"[1-9][0-9]{0,4}", value) or int(value) > maximum:
                        raise InvalidTopologyFilter("Invalid topology filters")
                    normalized[key] = int(value)
                elif key == "archived":
                    if value not in ("include", "exclude", "only"):
                        raise InvalidTopologyFilter("Invalid topology filters")
                    normalized[key] = value
                elif key in ("arena", "anchor"):
                    if value and not (key == "arena" and value == "*"):
                        kind, sep, identity = value.partition(":")
                        if (
                            not sep
                            or kind not in PUBLIC_LABELS
                            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}", identity)
                            or (key == "arena" and kind != "Arena")
                        ):
                            raise InvalidTopologyFilter("Invalid topology filters")
                    normalized[key] = value
                else:
                    if len(value) > 200 or any(ord(c) < 32 for c in value):
                        raise InvalidTopologyFilter("Invalid topology filters")
                    normalized[key] = value.strip().lower()
        return cls(**normalized)

    def query(self) -> str:
        defaults = asdict(TopologyFilter())
        pairs = []
        for key, value in sorted(asdict(self).items()):
            if value == defaults[key]:
                continue
            if isinstance(value, tuple):
                pairs.extend((key, x) for x in (value or ("none",)))
            else:
                pairs.append((key, str(value)))
        return urlencode(pairs)

    def target(self) -> str:
        query = self.query()
        return PATH + (f"?{query}" if query else "")


def parse_target(target: str, *, canonical: bool = False) -> TopologyFilter:
    path, _, query = target.partition("?")
    if path != PATH:
        raise InvalidTopologyFilter("Invalid topology target")
    filters = TopologyFilter.parse(query)
    if canonical and filters.target() != target:
        raise InvalidTopologyFilter("Noncanonical topology target")
    return filters


class _CapturedStorage:
    """Read-only adapter over an already captured, bounded storage result."""

    def __init__(self, nodes, edges, health):
        self.nodes, self.edges, self._health = nodes, edges, health

    def query(self, label=None, archived=None, **_kwargs):
        return [
            n
            for n in self.nodes
            if (label is None or n.label == label) and (archived is None or n.archived == archived)
        ]

    def list_edges(self):
        return self.edges

    def health(self):
        return self._health


def _matches(node, filters, skip=None):
    category = node["category"]
    checks = {"category": category}
    if category == "work":
        checks.update(work_kind=node["kind"], work_status=node["status"])
    elif category == "observation":
        checks["observation_status"] = node["status"]
    elif category == "receipt":
        checks["record_status"] = node["status"]
    if any(
        key != skip and getattr(filters, key) is not None and value not in getattr(filters, key)
        for key, value in checks.items()
    ):
        return False
    if filters.archived != "include" and node["archived"] != (filters.archived == "only"):
        return False
    return not filters.q or filters.q in (node["title"] + " " + node["id"]).lower()


def filter_projection(snapshot, filters: TopologyFilter):
    all_nodes, all_edges = snapshot["graph_nodes"], snapshot["graph_edges"]
    available = {n["key"]: n for n in all_nodes}
    outgoing: dict[str, set[str]] = {}
    neighbors: dict[str, set[str]] = {}
    for e in all_edges:
        outgoing.setdefault(e["source"], set()).add(e["target"])
        neighbors.setdefault(e["source"], set()).add(e["target"])
        neighbors.setdefault(e["target"], set()).add(e["source"])
    keys = set(available)
    scope_error = None
    if filters.arena:
        roots = {
            n["key"]
            for n in all_nodes
            if n["kind"] == "Arena" and (filters.arena == "*" or filters.arena == n["key"])
        }
        if not roots and filters.arena != "*":
            scope_error = "arena_unavailable"
        keys, queue = set(roots), list(roots)
        for key in queue:
            for other in outgoing.get(key, ()):
                if other not in keys:
                    keys.add(other)
                    queue.append(other)
    if filters.anchor:
        if filters.anchor not in available:
            scope_error = "anchor_unavailable"
        keys &= {filters.anchor, *neighbors.get(filters.anchor, ())}
    scoped = [n for n in all_nodes if n["key"] in keys]
    facets = {}
    for facet in FACETS:
        counts = Counter()
        for node in scoped:
            if not _matches(node, filters, skip=facet):
                continue
            if facet == "category":
                counts[node["category"]] += 1
            elif facet.startswith("work_") and node["category"] == "work":
                counts[node["kind"] if facet == "work_kind" else node["status"]] += 1
            elif facet == "observation_status" and node["category"] == "observation":
                counts[node["status"]] += 1
            elif facet == "record_status" and node["category"] == "receipt":
                counts[node["status"]] += 1
        facets[facet] = dict(counts)
    matching = [n for n in scoped if _matches(n, filters)]
    matching_keys = {n["key"] for n in matching}
    edges = [
        e
        for e in all_edges
        if e["source"] in matching_keys
        and e["target"] in matching_keys
        and (filters.relationship is None or e["relationship"] in filters.relationship)
    ]
    degree: dict[str, set[str]] = {}
    for e in edges:
        degree.setdefault(e["source"], set()).add(e["target"])
        degree.setdefault(e["target"], set()).add(e["source"])
    shown = [
        {**n, "connection_count": len(degree.get(n["key"], ()))}
        for n in matching[: filters.node_limit]
    ]
    shown_keys = {n["key"] for n in shown}
    shown_edges = [e for e in edges if e["source"] in shown_keys and e["target"] in shown_keys]
    shown_edges = shown_edges[: filters.edge_limit]
    stable = {
        key: value
        for key, value in snapshot.items()
        if key not in ("generated_at", "storage", "selection_scope")
    }
    revision = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return {
        **snapshot,
        "schema": "devgraph.topology.v1",
        "revision": revision,
        "consistency": "materialized_projection",
        "applied_filters": asdict(filters),
        "graph_nodes": shown,
        "graph_edges": shown_edges,
        "facets": facets,
        "arenas": [n for n in all_nodes if n["kind"] == "Arena"],
        "relationship_types": sorted({e["relationship"] for e in all_edges}),
        "scope_error": scope_error,
        "counts": {
            "available_nodes": len(all_nodes),
            "scoped_nodes": len(scoped),
            "matching_nodes": len(matching),
            "matching_edges": len(edges),
            "returned_nodes": len(shown),
            "returned_edges": len(shown_edges),
        },
        "complete": len(shown) == len(matching) and len(shown_edges) == len(edges),
    }


def build_topology(storage, filters: TopologyFilter):
    nodes, edges = storage.monitor_records()
    if len(nodes) > SOURCE_NODE_LIMIT or len(edges) > SOURCE_EDGE_LIMIT:
        raise StorageUnavailable("topology source capacity exceeded")
    captured = _CapturedStorage(nodes, edges, storage.health())
    snapshot = build_monitor_snapshot(captured)
    return filter_projection(snapshot, filters)
