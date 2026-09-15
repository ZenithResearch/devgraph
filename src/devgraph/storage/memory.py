from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from typing import Any

from devgraph.model.validation import (
    CANONICAL_WORK_KINDS,
    validate_canonical_work_object_properties,
    validate_page_limit,
    validate_work_object_id,
)
from devgraph.storage.base import (
    ConstraintResult,
    EdgeRecord,
    EventReceiptClaim,
    HealthStatus,
    NodeRecord,
    StorageUnavailable,
    WorkContainment,
)
from devgraph.storage.containment import validate_arena_page, validate_containment_subject
from devgraph.storage.migrations import count_idempotent_application
from devgraph.storage.supporting_material import (
    MAX_PROVENANCE,
    SUPPORTING_PROPERTIES,
    SUPPORTING_RELATIONSHIPS,
    SupportingReference,
    validate_supporting_keys,
    validate_supporting_read,
)


class MemoryGraphStorage:
    """In-memory GraphStorage implementation for tests and local parity checks only."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], NodeRecord] = {}
        self._edges: list[EdgeRecord] = []
        self._applied_constraints: set[str] = set()
        self._transaction_lock = RLock()

    @staticmethod
    def _is_canonical_payload(label: str, properties: object) -> bool:
        return (
            label in CANONICAL_WORK_KINDS and isinstance(properties, dict) and "kind" in properties
        )

    def create_node(
        self, label: str, node_id: str, properties: dict[str, Any] | None = None
    ) -> NodeRecord:
        validate_work_object_id(node_id)
        node_properties = dict(properties or {})
        if self._is_canonical_payload(label, properties):
            try:
                node_properties = validate_canonical_work_object_properties(
                    label, node_id, properties, archived=False
                )
            except (TypeError, ValueError) as exc:
                raise StorageUnavailable("malformed canonical work object") from exc
        key = (label, node_id)
        if key in self._nodes:
            raise KeyError(f"node {label}:{node_id} already exists")
        record = NodeRecord(label=label, id=node_id, properties=node_properties, archived=False)
        self._nodes[key] = record
        return record

    def get_node(self, label: str, node_id: str) -> NodeRecord | None:
        validate_work_object_id(node_id)
        return self._nodes.get((label, node_id))

    def _work_containment(self, keys: list[tuple[str, str]]) -> list[WorkContainment]:
        # One inventory pass for the whole page, retaining duplicate witnesses.
        incoming: dict[tuple[str, str], dict[str, list[EdgeRecord]]] = {
            key: {"HAS_CHILD": [], "CONTAINS_WORK": []} for key in keys
        }
        for edge in self._edges:
            groups = incoming.get((edge.to_label, edge.to_id))
            if groups is not None and edge.relationship in groups:
                group = groups[edge.relationship]
                if len(group) < 2:
                    group.append(edge)
        result = []
        for key in keys:
            node = self._nodes.get(key)
            if node is None:
                raise StorageUnavailable("missing containment target")
            result.append(WorkContainment(
                node, tuple(incoming[key]["HAS_CHILD"]), tuple(incoming[key]["CONTAINS_WORK"])
            ))
        return result

    def work_containment(self, label: str, node_id: str) -> WorkContainment | None:
        validate_containment_subject(label, node_id)
        with self._transaction_lock:
            key = (label, node_id)
            return self._work_containment([key])[0] if key in self._nodes else None

    def arena_member_page(
        self, arena_id: str, *, after_resource: str | None = None, limit: int = 50
    ) -> list[WorkContainment]:
        validate_arena_page(arena_id, after_resource, limit)
        with self._transaction_lock:
            # Select unique targets; their incoming duplicate edges remain visible above.
            keys = sorted({
                (edge.to_label, edge.to_id) for edge in self._edges
                if (edge.from_label, edge.from_id, edge.relationship)
                == ("Arena", arena_id, "CONTAINS_WORK")
                and (after_resource is None or f"{edge.to_label}/{edge.to_id}" > after_resource)
            })[:limit]
            return self._work_containment(keys)

    def update_node(self, label: str, node_id: str, properties: dict[str, Any]) -> NodeRecord:
        validate_work_object_id(node_id)
        existing = self._require_node(label, node_id)
        canonical = self._is_canonical_payload(
            label, existing.properties
        ) or self._is_canonical_payload(label, properties)
        if canonical and (existing.archived or existing.properties.get("status") == "archived"):
            raise KeyError(f"archived {label}: {node_id}")
        node_properties = properties
        if canonical:
            try:
                current_properties = validate_canonical_work_object_properties(
                    label,
                    node_id,
                    existing.properties,
                    archived=existing.archived,
                    allow_unknown=True,
                )
                node_properties = validate_canonical_work_object_properties(
                    label,
                    node_id,
                    properties,
                    archived=properties.get("status") == "archived",
                )
            except (TypeError, ValueError) as exc:
                raise StorageUnavailable("malformed canonical work object") from exc
            if (
                node_properties["created_at"] != current_properties["created_at"]
                or node_properties["version"] != current_properties["version"] + 1
            ):
                raise KeyError(f"stale or inconsistent {label}: {node_id}")
        updated = NodeRecord(
            label=existing.label,
            id=existing.id,
            properties={**existing.properties, **node_properties},
            archived=(
                existing.archived or (canonical and node_properties.get("status") == "archived")
            ),
        )
        self._nodes[(label, node_id)] = updated
        return updated

    def archive_node(
        self,
        label: str,
        node_id: str,
        properties: dict[str, Any] | None = None,
    ) -> NodeRecord:
        validate_work_object_id(node_id)
        existing = self._require_node(label, node_id)
        canonical_payload = self._is_canonical_payload(
            label, existing.properties
        ) or self._is_canonical_payload(label, properties)
        if canonical_payload and existing.archived:
            if properties is None and existing.properties.get("status") == "archived":
                return existing
            raise KeyError(f"already archived {label}: {node_id}")
        if not canonical_payload:
            archived_properties = {**existing.properties, **(properties or {})}
        elif properties is None:
            try:
                validate_canonical_work_object_properties(
                    label,
                    node_id,
                    existing.properties,
                    archived=True,
                    allow_unknown=True,
                )
            except (TypeError, ValueError) as exc:
                raise StorageUnavailable("malformed canonical work object") from exc
            archived_properties = dict(existing.properties)
        else:
            try:
                canonical = validate_canonical_work_object_properties(
                    label, node_id, properties, archived=True
                )
            except (TypeError, ValueError) as exc:
                raise StorageUnavailable("malformed canonical work object") from exc
            current_version = existing.properties.get("version")
            new_version = canonical.get("version")
            if (
                isinstance(current_version, bool)
                or not isinstance(current_version, int)
                or isinstance(new_version, bool)
                or not isinstance(new_version, int)
                or new_version != current_version + 1
                or canonical.get("created_at") != existing.properties.get("created_at")
                or canonical.get("kind") != label
                or canonical.get("status") != "archived"
            ):
                raise KeyError(f"missing, stale, or inconsistent {label}: {node_id}")
            archived_properties = dict(canonical)
        archived = NodeRecord(
            label=existing.label,
            id=existing.id,
            properties=archived_properties,
            archived=True,
        )
        self._nodes[(label, node_id)] = archived
        return archived

    def create_edge(
        self,
        from_label: str,
        from_id: str,
        relationship: str,
        to_label: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> EdgeRecord:
        validate_work_object_id(from_id)
        validate_work_object_id(to_id)
        if (from_label, from_id) not in self._nodes:
            raise KeyError(f"missing from node {from_label}:{from_id}")
        if (to_label, to_id) not in self._nodes:
            raise KeyError(f"missing to node {to_label}:{to_id}")
        edge = EdgeRecord(
            from_label=from_label,
            from_id=from_id,
            relationship=relationship,
            to_label=to_label,
            to_id=to_id,
            properties=dict(properties or {}),
        )
        if edge not in self._edges:
            self._edges.append(edge)
        return edge

    def related_work_nodes(
        self,
        label: str,
        node_id: str,
        relationship: str,
        *,
        incoming: bool,
        after_resource: str | None = None,
        limit: int = 50,
    ) -> list[NodeRecord]:
        from devgraph.model.validation import CANONICAL_WORK_KINDS, validate_page_limit

        validate_page_limit(limit)
        if relationship not in {"HAS_CHILD", "DEPENDS_ON", "BLOCKS"} or type(incoming) is not bool:
            raise ValueError("invalid relationship read")
        with self.transaction():
            keys = set()
            for edge in self._edges:
                source = (
                    (edge.to_label, edge.to_id) if incoming else (edge.from_label, edge.from_id)
                )
                target = (
                    (edge.from_label, edge.from_id) if incoming else (edge.to_label, edge.to_id)
                )
                if (
                    edge.relationship == relationship
                    and source == (label, node_id)
                    and target[0] in CANONICAL_WORK_KINDS
                ):
                    if after_resource is None or "/".join(target) > after_resource:
                        keys.add(target)
            return [self._nodes[key] for key in sorted(keys)[:limit] if key in self._nodes]

    def supporting_material_references(
        self,
        label: str,
        node_id: str,
        *,
        artifact_ids: tuple[str, ...],
        external_link_ids: tuple[str, ...],
        after_resource: str | None = None,
        target_resource: str | None = None,
        limit: int = 50,
    ) -> list[SupportingReference]:
        validate_supporting_read(
            label, node_id, artifact_ids, external_link_ids, limit, after_resource, target_resource
        )
        with self.transaction():
            paths: dict[tuple[str, str], set[str]] = {}

            def add(kind, item_id, path):
                resource = kind + "/" + item_id
                if (after_resource is None or resource > after_resource) and (
                    target_resource is None or resource == target_resource
                ):
                    paths.setdefault((kind, item_id), set()).add(path)

            for item_id in artifact_ids:
                add("Artifact", item_id, "field/artifact_ids")
            for item_id in external_link_ids:
                add("ExternalLink", item_id, "field/external_link_ids")
            requirements = set()
            for edge in self._edges:
                if (edge.from_label, edge.from_id) == (
                    label,
                    node_id,
                ) and SUPPORTING_RELATIONSHIPS.get(edge.relationship) == edge.to_label:
                    add(edge.to_label, edge.to_id, "edge/" + edge.relationship)
                    if edge.relationship == "HAS_REQUIREMENT":
                        requirements.add(edge.to_id)
            for edge in self._edges:
                if (
                    edge.from_label == "Requirement"
                    and edge.from_id in requirements
                    and edge.relationship == "HAS_ACCEPTANCE_CRITERION"
                    and edge.to_label == "AcceptanceCriterion"
                ):
                    add(edge.to_label, edge.to_id, "requirement/" + edge.from_id)
            return [
                SupportingReference(
                    kind, item_id, tuple(sorted(via)[:MAX_PROVENANCE]), len(via) > MAX_PROVENANCE
                )
                for (kind, item_id), via in sorted(paths.items())[:limit]
            ]


    def supporting_material_nodes(self, keys: list[tuple[str, str]]) -> list[NodeRecord]:
        validate_supporting_keys(keys)
        with self.transaction():
            return [
                NodeRecord(
                    node.label,
                    node.id,
                    {
                        name: deepcopy(value)
                        for name, value in node.properties.items()
                        if name in SUPPORTING_PROPERTIES
                    },
                    node.archived,
                )
                for key in keys
                if (node := self._nodes.get(key)) is not None
            ]

    def list_edges(
        self, relationship: str | None = None, *, limit: int | None = None
    ) -> list[EdgeRecord]:
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10_001):
            raise ValueError("invalid edge limit")
        edges = list(self._edges)
        if relationship is not None:
            edges = [edge for edge in edges if edge.relationship == relationship]
        return edges if limit is None else edges[:limit]

    def delete_edge(
        self, from_label: str, from_id: str, relationship: str, to_label: str, to_id: str
    ) -> None:
        self._edges = [
            edge
            for edge in self._edges
            if (edge.from_label, edge.from_id, edge.relationship, edge.to_label, edge.to_id)
            != (from_label, from_id, relationship, to_label, to_id)
        ]

    def work_mutation_transaction(self):
        return self.transaction()

    def query(
        self,
        label: str | None = None,
        archived: bool | None = None,
        *,
        descending: bool = False,
        after_id: str | None = None,
        limit: int | None = None,
    ) -> list[NodeRecord]:
        if not isinstance(descending, bool):
            raise TypeError("descending must be boolean")
        if after_id is not None:
            if label is None:
                raise ValueError("after_id requires a node label")
            validate_work_object_id(after_id)
        if limit is not None:
            validate_page_limit(limit)
        records = sorted(
            self._nodes.values(), key=lambda node: (node.label, node.id), reverse=descending
        )
        if label is not None:
            records = [node for node in records if node.label == label]
        if archived is not None:
            records = [node for node in records if node.archived is archived]
        if after_id is not None:
            if descending:
                records = [node for node in records if node.id < after_id]
            else:
                records = [node for node in records if node.id > after_id]
        return records if limit is None else records[:limit]

    def claim_event_receipt(
        self,
        node_id: str,
        idempotency_claim_digest: str,
        properties: dict[str, Any],
    ) -> EventReceiptClaim:
        if properties.get("idempotency_claim_digest") != idempotency_claim_digest:
            raise StorageUnavailable("invalid idempotency claim")
        with self._transaction_lock:
            matches = [
                node
                for node in self._nodes.values()
                if node.label == "EventReceipt"
                and node.properties.get("idempotency_claim_digest") == idempotency_claim_digest
            ]
            if len(matches) > 1:
                raise StorageUnavailable("ambiguous idempotency claim")
            if matches:
                return EventReceiptClaim(False, matches[0])
            return EventReceiptClaim(
                True,
                self.create_node("EventReceipt", node_id, properties),
            )

    def inspect_canonical_persistence(self) -> None:
        try:
            for node in self._nodes.values():
                if node.label not in CANONICAL_WORK_KINDS:
                    continue
                if node.properties.get("kind") != node.label:
                    raise ValueError("kind")
                validate_canonical_work_object_properties(
                    node.label,
                    node.id,
                    node.properties,
                    archived=node.archived,
                    allow_unknown=True,
                )
        except (TypeError, ValueError) as exc:
            raise StorageUnavailable("malformed canonical persistence") from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._transaction_lock:
            nodes_snapshot = deepcopy(self._nodes)
            edges_snapshot = deepcopy(self._edges)
            constraints_snapshot = deepcopy(self._applied_constraints)
            try:
                yield
            except BaseException:
                self._nodes = nodes_snapshot
                self._edges = edges_snapshot
                self._applied_constraints = constraints_snapshot
                raise

    def apply_constraints(self, statements: list[str]) -> ConstraintResult:
        return count_idempotent_application(statements, self._applied_constraints)

    def health(self) -> HealthStatus:
        return HealthStatus(live=True, ready=True, detail="memory storage ready")

    def _require_node(self, label: str, node_id: str) -> NodeRecord:
        node = self.get_node(label, node_id)
        if node is None:
            raise KeyError(f"missing node {label}:{node_id}")
        return node
